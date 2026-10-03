import logging
from datetime import datetime, timezone
from typing import Any

from sqlalchemy.orm import Session

from app.ai.errors import AIProviderError
from app.ai.risk_factor_analyzer import identify_risk_factors
from app.database import IS_POSTGRES
from app.models.assessment import Assessment
from app.models.assessment_intelligence import AssessmentIntelligence
from app.models.risk_factor import RiskFactor
from app.risk_engine.degraded import (
    DEGRADED_REASON,
    UNAVAILABLE_REASON,
    AiStatus,
    AssessmentMode,
    ScoreSource,
    build_rules_only_factors,
    classify_ai_failure,
    fallback_enabled,
    unevaluated_categories,
)
from app.models.assessment_document import AssessmentDocument
from app.risk_engine.engine import RiskEngine
from app.risk_engine.evidence import (
    ASSESSMENT_SOURCE_FIELDS,
    UNRESOLVED_STATUSES,
    build_evidence_sources,
)
from app.risk_engine.methodology import get_methodology_config
from app.risk_engine.scoring import calculate_inherent_risk
from app.schemas.risk_factor import RISK_CATEGORIES
from app.services.audit_service import AuditAction, log_audit_event
from app.services.inherent_risk_service import recalculate_inherent_risk
from app.services.risk_scoring import recalculate_assessment_score

from .state import RiskAssessmentState

logger = logging.getLogger(__name__)


def load_assessment(state: RiskAssessmentState, db: Session) -> dict[str, Any]:
    assessment = (
        db.query(Assessment)
        .filter(Assessment.id == state["assessment_id"])
        .first()
    )

    if not assessment:
        raise ValueError("Assessment not found.")

    assessment_data = {
        "id": assessment.id,
        "change_type": assessment.change_type,
        "status": assessment.status,
    }
    # Every intake field the model may quote from (FIELD:<name> sources in
    # app/risk_engine/evidence.py) is passed in, so a quote can be checked
    # against exactly the text the model was shown.
    for field in ASSESSMENT_SOURCE_FIELDS:
        assessment_data[field] = getattr(assessment, field, None)

    return {
        "assessment": assessment_data,
        "previous_status": assessment.status,
        "status": "LOADED",
    }


def gather_intelligence(state: RiskAssessmentState, db: Session) -> dict[str, Any]:
    intelligence = (
        db.query(AssessmentIntelligence)
        .filter(
            AssessmentIntelligence.assessment_id
            == state["assessment_id"]
        )
        .first()
    )

    return {
        "intelligence": intelligence,
        "status": "INTELLIGENCE_GATHERED",
    }


def _apply_stage4_rules(factors: list[dict[str, Any]], state: RiskAssessmentState) -> None:
    """P4: the fixed Stage 4 rules (app/risk_engine/stage4_rules.py) run on
    every factor set the analysis produces, AI or rules-only."""

    from app.risk_engine import stage4_rules

    stage4_rules.apply(factors, state["assessment"], state.get("intelligence"))
    order = {category: index for index, category in enumerate(RISK_CATEGORIES)}
    factors.sort(key=lambda factor: order.get(factor["category"], len(order)))


def _unavailable_result(ai_status: str, error_code: str) -> dict[str, Any]:
    """
    The analysis produced nothing trustworthy. No risk factors, no score,
    no rating -- and explicitly not a set of zeroes that would read as
    "we looked and found nothing".
    """

    return {
        "risk_factors": [],
        "ai_available": False,
        "assessment_mode": AssessmentMode.UNAVAILABLE,
        "ai_status": ai_status,
        "score_source": ScoreSource.NOT_AVAILABLE,
        "is_provisional": True,
        "requires_human_review": True,
        "degraded_reason": UNAVAILABLE_REASON,
        "technical_error_code": error_code,
        "unevaluated_categories": list(RISK_CATEGORIES),
        "status": "ANALYSIS_UNAVAILABLE",
    }


def identify_risks(state: RiskAssessmentState, db: Session) -> dict[str, Any]:
    """
    LangGraph AI node.

    Stage 4 (R4.1-R4.5): identifies which of the 10 canonical risk
    categories apply -- a fully dynamic, AI-determined set (see
    app/ai/risk_factor_analyzer.py) -- replacing the old fixed
    6-dimension analyze_risks() call entirely. This node only returns
    the identified factors; calculate_scores (next) derives the
    overall score/level from them.

    Degraded modes (see app/risk_engine/degraded.py). This node used to
    answer an AI failure by writing all ten categories back as
    `applicable=False, score=0.0, severity="LOW"` -- which an analyst,
    and the overall-score calculation, would both read as a genuine
    "low risk" finding. It now resolves to one of three explicit modes:

      ai_assisted  the AI answered.
      rules_only   the AI failed *operationally* (timeout, rate limit,
                   provider outage, unparseable response) and the
                   deterministic RiskEngine produced a real result
                   instead. Provisional; needs reviewer acknowledgement.
      unavailable  the fallback could not run either, or the failure was
                   not an AI-operational one at all (bad assessment
                   data, broken methodology config, a bug in the rule
                   engine). No rating is produced.

    The boundary matters: a deterministic fallback can stand in for a
    missing model, but it cannot compensate for invalid input data or
    for its own failure.
    """
    similar_context = None

    if IS_POSTGRES:
        # Semantic search (pgvector) over other assessments' indexed
        # evidence, using this assessment's own description+evidence as
        # the query -- best-effort context for the AI, never required.
        from app.services.semantic_risk_search import find_similar_risk_context

        query_text = " ".join(
            filter(
                None,
                [
                    state["assessment"].get("description"),
                    state["assessment"].get("evidence"),
                ],
            )
        )

        similar_context = find_similar_risk_context(
            db=db,
            query_text=query_text,
            exclude_assessment_id=state["assessment_id"],
        )

    try:
        documents = (
            db.query(AssessmentDocument)
            .filter(
                AssessmentDocument.assessment_id == state["assessment_id"],
                AssessmentDocument.is_current.is_(True),
            )
            .order_by(AssessmentDocument.id)
            .all()
        )
        # P4 (R2.6): an expired document is used only after a person has
        # acknowledged it as evidence; one they excluded is never used.
        from app.services.evidence_currency import usable_documents

        documents = usable_documents(db, documents)
        risk_factors = identify_risk_factors(
            assessment=state["assessment"],
            intelligence=state.get("intelligence"),
            similar_context=similar_context,
            evidence_sources=build_evidence_sources(state["assessment"], documents),
        )
        _apply_stage4_rules(risk_factors, state)

    except AIProviderError as exc:
        # An operational AI failure -- the one case a deterministic
        # fallback can legitimately stand in for.
        ai_status, error_code = classify_ai_failure(exc)
        logger.warning(
            "AI risk-factor identification failed for assessment %s (%s); "
            "attempting the deterministic rules-only fallback. Reason: %s",
            state["assessment_id"],
            error_code,
            exc,
        )

        if not fallback_enabled():
            logger.warning(
                "ENABLE_RULES_ONLY_FALLBACK is off; assessment %s is "
                "recorded as unavailable rather than rules-only.",
                state["assessment_id"],
            )
            log_audit_event(
                db=db,
                assessment_id=state["assessment_id"],
                action=AuditAction.ANALYSIS_UNAVAILABLE,
                previous_status=state.get("previous_status"),
                new_status=state.get("previous_status"),
                details=(
                    "AI analysis failed and the deterministic fallback is "
                    f"disabled (ENABLE_RULES_ONLY_FALLBACK). Error code: "
                    f"{error_code}. No risk rating was produced."
                ),
            )
            return _unavailable_result(ai_status, error_code)

        try:
            assessment = state["assessment"]
            engine_results = RiskEngine().assess(
                change_type=assessment.get("change_type") or "",
                description=assessment.get("description") or "",
                evidence=assessment.get("evidence") or "",
                intelligence=state.get("intelligence"),
            )
            rules_factors = build_rules_only_factors(engine_results)
        except Exception as rule_exc:  # noqa: BLE001
            # The fallback itself is broken. There is nothing left to
            # fall back to, and guessing would be worse than saying so.
            logger.exception(
                "Deterministic rule engine also failed for assessment %s; "
                "recording the analysis as unavailable. Reason: %s",
                state["assessment_id"],
                rule_exc,
            )
            log_audit_event(
                db=db,
                assessment_id=state["assessment_id"],
                action=AuditAction.ANALYSIS_UNAVAILABLE,
                previous_status=state.get("previous_status"),
                new_status=state.get("previous_status"),
                details=(
                    "AI analysis failed and the deterministic rule engine "
                    "failed as well. No risk rating was produced. Error "
                    "code: RULE_ENGINE_FAILURE."
                ),
            )
            return _unavailable_result(ai_status, "RULE_ENGINE_FAILURE")

        if not rules_factors:
            # No rule fired at all. An empty factor set would score 0.0,
            # which is precisely the silent-low-risk outcome this whole
            # path exists to prevent.
            logger.warning(
                "Deterministic rule engine produced no factors for "
                "assessment %s; recording the analysis as unavailable.",
                state["assessment_id"],
            )
            log_audit_event(
                db=db,
                assessment_id=state["assessment_id"],
                action=AuditAction.ANALYSIS_UNAVAILABLE,
                previous_status=state.get("previous_status"),
                new_status=state.get("previous_status"),
                details=(
                    "AI analysis failed and the deterministic rule engine "
                    "identified no applicable risk. No risk rating was "
                    "produced. Error code: RULE_ENGINE_NO_RESULT."
                ),
            )
            return _unavailable_result(ai_status, "RULE_ENGINE_NO_RESULT")

        # Computed before the Stage 4 rules add anything: a factor a rule
        # requires is still one the engine did not evaluate.
        skipped = unevaluated_categories(rules_factors)
        _apply_stage4_rules(rules_factors, state)

        log_audit_event(
            db=db,
            assessment_id=state["assessment_id"],
            action=AuditAction.AI_ANALYSIS_DEGRADED,
            previous_status=state.get("previous_status"),
            new_status=state.get("previous_status"),
            details=(
                "AI risk-factor identification was unavailable "
                f"({error_code}); this assessment was analysed by the "
                "deterministic rule engine only. The result is "
                "provisional and requires reviewer acknowledgement. "
                f"Categories not evaluated: {', '.join(skipped) or 'none'}."
            ),
        )

        return {
            "risk_factors": rules_factors,
            "ai_available": False,
            "assessment_mode": AssessmentMode.RULES_ONLY,
            "ai_status": ai_status,
            "score_source": ScoreSource.DETERMINISTIC_RULES,
            "is_provisional": True,
            "requires_human_review": True,
            "degraded_reason": DEGRADED_REASON,
            "technical_error_code": error_code,
            "unevaluated_categories": skipped,
            "status": "RISKS_IDENTIFIED_BY_RULES",
        }

    except Exception as exc:  # noqa: BLE001
        # Not an AI-operational failure: missing/corrupt assessment data,
        # an invalid methodology configuration, a database problem. The
        # rule engine cannot compensate for any of those, so no result.
        logger.exception(
            "Risk identification failed for assessment %s for a reason "
            "unrelated to the AI provider; no rating will be produced. "
            "Reason: %s",
            state["assessment_id"],
            exc,
        )
        log_audit_event(
            db=db,
            assessment_id=state["assessment_id"],
            action=AuditAction.ANALYSIS_UNAVAILABLE,
            previous_status=state.get("previous_status"),
            new_status=state.get("previous_status"),
            details=(
                "Risk identification failed for a reason unrelated to the "
                "AI provider, so the deterministic fallback was not used. "
                "No risk rating was produced. Error code: "
                "UNEXPECTED_ANALYSIS_FAILURE."
            ),
        )
        return _unavailable_result(
            AiStatus.FAILED, "UNEXPECTED_ANALYSIS_FAILURE"
        )

    return {
        "risk_factors": risk_factors,
        "ai_available": True,
        "assessment_mode": AssessmentMode.AI_ASSISTED,
        "ai_status": AiStatus.SUCCESS,
        "score_source": ScoreSource.AI_AND_RULES,
        "is_provisional": False,
        "requires_human_review": False,
        "degraded_reason": None,
        "technical_error_code": None,
        "unevaluated_categories": [],
        "status": "RISKS_IDENTIFIED_BY_AI",
    }


def calculate_scores(state: RiskAssessmentState, db: Session) -> dict[str, Any]:
    """
    Keep score aggregation deterministic.

    overall_score/risk_level come from the Stage 6 calculation
    (calculate_inherent_risk) and only from analyst likelihood x impact
    ratings. A fresh AI run rates nothing, so this is normally no score
    yet -- unless an approved policy rule fires on a verified indicator.
    This is provisional -- persist_results recomputes the authoritative
    value from the full current factor set (including carried-forward
    manual factors and ratings) once everything is saved.
    """
    if state.get("assessment_mode") == AssessmentMode.UNAVAILABLE:
        # Nothing was evaluated, so there is nothing to average. Returning
        # 0.0/LOW here would manufacture exactly the false low-risk
        # reading identify_risks just refused to write.
        return {
            "overall_score": None,
            "risk_level": None,
            "status": "SCORES_UNAVAILABLE",
        }

    config = get_methodology_config(db)

    result = calculate_inherent_risk(
        [{**factor, "rated": False} for factor in state.get("risk_factors", [])],
        weights=config["factor_weights"],
        risk_bands=config["risk_bands"],
        escalation_rules=config["escalation_rules"],
        mitigant_categories=config["mitigant_categories"],
    )

    return {
        "overall_score": result["final_score"],
        "risk_level": result["risk_band"],
        "status": "SCORES_CALCULATED",
    }


def _apply_analysis_mode(assessment: Assessment, state: RiskAssessmentState) -> None:
    """
    Record how this run was produced, on the assessment itself.

    These fields are the stable contract the API and the frontend read.
    Nothing downstream should have to infer degradation by matching on
    rationale text -- that coupling is what this replaces.
    """

    assessment.assessment_mode = state.get(
        "assessment_mode", AssessmentMode.AI_ASSISTED
    )
    assessment.ai_status = state.get("ai_status", AiStatus.SUCCESS)
    assessment.score_source = state.get("score_source", ScoreSource.AI_AND_RULES)
    assessment.analysis_is_provisional = bool(state.get("is_provisional", False))
    assessment.requires_human_review = bool(state.get("requires_human_review", False))
    assessment.degraded_reason = state.get("degraded_reason")
    assessment.technical_error_code = state.get("technical_error_code")
    assessment.analysis_mode_at = datetime.now(timezone.utc)

    unevaluated = state.get("unevaluated_categories") or []
    assessment.set_unevaluated_categories(unevaluated)

    if assessment.requires_human_review:
        # A fresh degraded run invalidates any earlier acknowledgement:
        # what was acknowledged was a different result.
        assessment.degraded_acknowledged_by = None
        assessment.degraded_acknowledged_at = None


def persist_results(state: RiskAssessmentState, db: Session) -> dict[str, Any]:
    assessment = (
        db.query(Assessment)
        .filter(Assessment.id == state["assessment_id"])
        .first()
    )

    if not assessment:
        raise ValueError("Assessment not found.")

    if state.get("assessment_mode") == AssessmentMode.UNAVAILABLE:
        # Deliberately writes no risk factors and recomputes no score.
        #
        # Note what it also does NOT do: it does not clear an existing
        # score. If an earlier run succeeded, those factors are still the
        # last real analysis of this assessment and stay current --
        # wiping them because a later run failed would destroy good data.
        # What changes is the mode fields, which say plainly that the
        # most recent attempt produced nothing.
        _apply_analysis_mode(assessment, state)

        db.commit()
        db.refresh(assessment)

        return {
            "status": "UNAVAILABLE",
        }

    # Stage 4 (R4.1-R4.4): same supersede-don't-delete versioning
    # already used elsewhere in this pipeline. Only AI-sourced factors
    # are superseded by a re-analysis -- manually added factors (R4.5)
    # are an analyst's own input and carry forward untouched until
    # explicitly excluded.
    # Both automated sources supersede each other: a rules-only re-run
    # must retire the previous AI factors (and vice versa), or the
    # assessment would end up scored off a mix of two different runs.
    # Manually added factors (R4.5) are untouched either way.
    factor_source = (
        "RULES"
        if state.get("assessment_mode") == AssessmentMode.RULES_ONLY
        else "AI"
    )

    previous_factors = (
        db.query(RiskFactor)
        .filter(
            RiskFactor.assessment_id == assessment.id,
            RiskFactor.is_current.is_(True),
            RiskFactor.source.in_(["AI", "RULES"]),
        )
        .all()
    )

    now = datetime.now(timezone.utc)
    next_factor_version = 1

    for previous in previous_factors:
        previous.is_current = False
        previous.superseded_at = now
        next_factor_version = max(next_factor_version, previous.version + 1)

    for factor in state.get("risk_factors", []):
        new_factor = RiskFactor(
            assessment_id=assessment.id,
            category=factor["category"],
            applicable=factor["applicable"],
            score=factor.get("score", 0.0),
            severity=factor.get("severity", "LOW"),
            rationale=factor["rationale"],
            misuse_scenario=factor.get("misuse_scenario"),
            source=factor_source,
            version=next_factor_version,
            is_current=True,
        )
        new_factor.set_indicators(factor.get("indicators", []))
        new_factor.set_evidence_fields(factor)
        if factor.get("rule_triggers"):
            import json

            new_factor.rule_triggers = json.dumps(factor["rule_triggers"])
        db.add(new_factor)

    # Recomputes overall_score/risk_level and the mirrored RiskResult
    # rows from the FULL current factor set (this run's AI factors plus
    # any manual additions carried forward above) rather than trusting
    # state's provisional calculate_scores output, which only saw this
    # run's AI factors.
    db.flush()
    recalculate_assessment_score(db, assessment)
    recalculate_inherent_risk(db, assessment)
    _apply_analysis_mode(assessment, state)

    factors = state.get("risk_factors", [])
    unresolved = [
        factor["category"]
        for factor in factors
        if factor.get("applicable") and factor.get("evidence_status") in UNRESOLVED_STATUSES
    ]
    rejected_quotes = sum(factor.get("rejected_quote_count") or 0 for factor in factors)
    rejected_indicators = [
        f"{factor['category']}:{item['indicator']}"
        for factor in factors
        for item in factor.get("rejected_indicators") or []
    ]
    fired_rules = sorted(
        {
            f"{trigger['rule_id']} -> {factor['category']} ({trigger['effect']})"
            for factor in factors
            for trigger in factor.get("rule_triggers") or []
        }
    )
    rules_summary = (
        " Fixed Stage 4 rules (provisional signals): " + "; ".join(fired_rules) + "."
        if fired_rules
        else ""
    )
    evidence_summary = (
        f" Evidence verification: {len(unresolved)} applicable factor(s) "
        f"unresolved ({', '.join(unresolved) or 'none'}); {rejected_quotes} "
        f"quote(s) rejected as not found in the cited source; indicators "
        f"refused for lack of a verified quote: "
        f"{', '.join(rejected_indicators) or 'none'}."
        if any("evidence_status" in factor for factor in factors)
        else ""
    )
    # NOTE: this node deliberately does NOT change assessment.status.
    # Stage progression is owned exclusively by the
    # PATCH /api/assessments/{id}/advance-stage endpoint (see
    # app/api/assessments.py), which invokes this workflow as a side
    # effect of the EVIDENCE_COLLECTION -> RISK_IDENTIFICATION stage
    # transition and sets the resulting status itself, after this
    # commits. This keeps "run the risk engine" and "move the pipeline
    # stage forward" as two clearly separated concerns.

    log_audit_event(
        db=db,
        assessment_id=assessment.id,
        action=AuditAction.ANALYSIS,
        previous_status=state.get("previous_status"),
        new_status=state.get("previous_status"),
        details=(
            "Risk assessment analyzed through the LangGraph workflow in "
            f"{state.get('assessment_mode', AssessmentMode.AI_ASSISTED)} "
            "mode -- risk factors across the 10 canonical categories. "
            "No factor is scored until an analyst rates it."
            + evidence_summary
            + rules_summary
        ),
    )

    db.commit()
    db.refresh(assessment)

    return {
        "status": "COMPLETED",
    }

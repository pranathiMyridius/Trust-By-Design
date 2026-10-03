"""
Stage 17 (R17.4): AI evaluation.

`record_ai_run()` snapshots what the AI produced on a risk-identification
run (called by the LangGraph workflow right after it persists results).
`refresh_evaluation()` then compares that snapshot with what humans have
since done on the assessment and stores the comparison on the same row,
so every metric in the AI evaluation report is backed by stored data:

  * AI-human agreement -- per risk category, did the AI's applicable /
    not-applicable call match the human-reviewed outcome (kept, excluded,
    or added by a human)? Only counted once a human has reviewed.
  * risk-band agreement / score difference -- the AI assigns each risk a
    score and severity; the system averages them (the same deterministic
    calculation used for the assessment itself) into the AI overall
    score/band, compared with the human-finalised inherent band after
    any override. Per factor: AI severity vs the analyst's likelihood x
    impact band.
  * missing-risk rate -- risk factors a human had to add (source != AI).
  * unsupported-content -- AI-applicable factors a human excluded, plus
    challenge findings flagging an UNSUPPORTED_CONCLUSION.
  * evidence-groundedness -- lexical heuristic: share of an AI
    rationale's content words that appear in the assessment's own
    evidence (description, evidence, uploaded document text). Documented
    as a heuristic, not a semantic judgement.
  * control-mapping accuracy -- controls the AI extracted from the
    source document(s) that match a control type an analyst actually
    recorded on the assessment.
  * human overrides -- explicit AI-value overrides, inherent-risk
    overrides and manual score overrides on the assessment.
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from typing import Any

from sqlalchemy.orm import Session

from app.models.ai_metrics import AIEvaluationRecord, AIUsageLog
from app.models.assessment import Assessment

GROUNDED_THRESHOLD = 0.5

_STOPWORDS = {
    "this", "that", "with", "from", "have", "will", "would", "could", "should", "their",
    "there", "which", "where", "while", "these", "those", "into", "such", "than", "then",
    "also", "been", "being", "they", "them", "within", "about", "across", "because",
    "risk", "risks", "may", "might", "more", "most", "other", "some", "through", "using",
    "used", "over", "under", "each", "only", "very", "when", "what", "whose", "does",
    "potential", "potentially", "including", "related", "given", "based", "assessment",
}

# Free-text phrases (as the AI extracts them from documents) -> CONTROL_LIBRARY type.
_CONTROL_KEYWORDS = {
    "KYC_CUSTOMER_DUE_DILIGENCE": ["kyc", "know your customer", "customer due diligence", "cdd", "identity verification", "onboarding check"],
    "ENHANCED_DUE_DILIGENCE": ["enhanced due diligence", "edd"],
    "BENEFICIAL_OWNERSHIP_VERIFICATION": ["beneficial owner", "ubo", "ownership verification"],
    "SANCTIONS_SCREENING": ["sanction", "pep screening", "watchlist", "ofac"],
    "TRANSACTION_MONITORING": ["transaction monitoring", "aml monitoring", "suspicious activity monitoring"],
    "FRAUD_MONITORING": ["fraud"],
    "TRANSACTION_LIMITS": ["limit", "threshold", "cap"],
    "GEOGRAPHIC_RESTRICTIONS": ["geographic", "geo-block", "country restriction", "geoblock", "jurisdiction restriction"],
    "CUSTOMER_RISK_MONITORING": ["customer risk", "risk rating", "periodic review", "ongoing monitoring"],
    "VENDOR_DUE_DILIGENCE": ["vendor", "third party due diligence", "third-party due diligence", "supplier"],
    "INVESTIGATION_PROCESSES": ["investigation", "case management", "escalation process"],
    "REGULATORY_REPORTING": ["regulatory report", "sar", "str filing", "suspicious activity report"],
}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _terms(text: str) -> set[str]:
    return {
        word
        for word in re.findall(r"[a-z][a-z\-]{3,}", (text or "").lower())
        if word not in _STOPWORDS
    }


def _band_for_score(score: float | None, risk_bands: list[dict] | None) -> str | None:
    from app.risk_engine.scoring import determine_risk_band

    if score is None:
        return None
    return determine_risk_band(score, risk_bands)


def record_ai_run(
    db: Session,
    assessment_id: int,
    state: dict[str, Any],
    processing_ms: int | None,
    model: str | None,
) -> AIEvaluationRecord:
    """Snapshot a completed AI risk-identification run. Does not commit."""

    from app.risk_engine.methodology import get_methodology_config

    from app.risk_engine.scoring import calculate_overall_score_from_factors

    factors = state.get("risk_factors") or []
    config = get_methodology_config(db)

    # P4: the AI's own call. A fixed Stage 4 rule may have made a factor
    # applicable (app/risk_engine/stage4_rules.py); the evaluation measures
    # the AI, so it reads what the analysis concluded before the rule.
    def ai_applicable(factor) -> bool:
        return bool(factor.get("analysis_applicable", factor.get("applicable")))

    # AI overall = the system's own average over the AI-assigned factor
    # scores (no score at all if the AI scored nothing applicable). The
    # analyzer no longer emits scores, so for current runs this is None
    # ("not measurable"); only pre-change runs carry one.
    scored = [f for f in factors if f.get("applicable") and f.get("model_score") is not None]
    ai_score = calculate_overall_score_from_factors(factors) if scored else None

    for previous in db.query(AIEvaluationRecord).filter(
        AIEvaluationRecord.assessment_id == assessment_id,
        AIEvaluationRecord.is_current.is_(True),
    ):
        previous.is_current = False

    record = AIEvaluationRecord(
        assessment_id=assessment_id,
        run_at=_now(),
        model=model,
        ai_available=bool(state.get("ai_available", True)),
        processing_ms=processing_ms,
        ai_overall_score=ai_score,
        # Same band scale the human inherent-risk calculation uses, so the
        # two are directly comparable.
        ai_risk_band=_band_for_score(ai_score, config.get("risk_bands")),
        ai_factor_count=len(factors),
        ai_applicable_count=sum(1 for f in factors if ai_applicable(f)),
        ai_factors=json.dumps(
            [
                {
                    "category": f.get("category"),
                    "applicable": ai_applicable(f),
                    "indicators": f.get("indicators") or [],
                    "rationale": f.get("analysis_rationale", f.get("rationale")) or "",
                    "score": f.get("score"),
                    "severity": f.get("severity"),
                    # No model score since the analyzer stopped asking
                    # for one; older snapshots keep theirs.
                    "model_score": f.get("model_score"),
                    "model_severity": f.get("model_severity"),
                    "evidence_status": f.get("evidence_status"),
                    "verified_quote_count": f.get("verified_quote_count"),
                    "rejected_quote_count": f.get("rejected_quote_count"),
                }
                for f in factors
            ]
        ),
        is_current=True,
    )
    db.add(record)
    db.flush()
    refresh_evaluation(db, record)
    return record


def _source_text(db: Session, assessment: Assessment) -> str:
    from app.models.assessment_document import AssessmentDocument

    parts = [
        assessment.description,
        assessment.evidence,
        assessment.product_or_service_name,
        assessment.countries_jurisdictions,
        assessment.delivery_channels,
        assessment.customer_segment,
        assessment.transaction_types,
        assessment.third_party_vendor_usage,
        assessment.technology_process_changes,
    ]
    parts += [
        doc.extracted_text
        for doc in db.query(AssessmentDocument).filter(
            AssessmentDocument.assessment_id == assessment.id,
            AssessmentDocument.is_current.is_(True),
        )
    ]
    return " ".join(filter(None, parts))


def _ai_extracted_controls(db: Session, assessment_id: int) -> list[str]:
    from app.models.assessment_intelligence import AssessmentIntelligence

    intelligence = (
        db.query(AssessmentIntelligence)
        .filter(AssessmentIntelligence.assessment_id == assessment_id)
        .first()
    )
    if not intelligence or not intelligence.raw_extraction:
        return []
    try:
        raw = json.loads(intelligence.raw_extraction)
    except ValueError:
        return []
    controls = raw.get("existing_controls") if isinstance(raw, dict) else None
    return [str(c) for c in controls if str(c).strip()] if isinstance(controls, list) else []


def _control_type_for(text: str) -> str | None:
    lowered = text.lower()
    for control_type, keywords in _CONTROL_KEYWORDS.items():
        if control_type.lower().replace("_", " ") in lowered or any(k in lowered for k in keywords):
            return control_type
    return None


def refresh_evaluation(db: Session, record: AIEvaluationRecord) -> AIEvaluationRecord:
    """Recompute the human-comparison columns on `record`. Does not commit."""

    from app.models.assessment_override import AssessmentOverride
    from app.models.audit_event import AuditEvent
    from app.models.challenge_review import ChallengeFinding
    from app.models.control import Control
    from app.models.inherent_risk_calculation import InherentRiskCalculation
    from app.models.risk_factor import RiskFactor
    from app.risk_engine.methodology import get_methodology_config
    from app.risk_engine.scoring import compute_factor_score, determine_risk_band

    assessment = db.get(Assessment, record.assessment_id)
    if assessment is None:
        return record
    config = get_methodology_config(db)
    risk_bands = config.get("risk_bands")

    # --- Overall: human-finalised inherent risk vs the AI's. ---
    calculation = (
        db.query(InherentRiskCalculation)
        .filter(
            InherentRiskCalculation.assessment_id == assessment.id,
            InherentRiskCalculation.is_current.is_(True),
        )
        .first()
    )
    if calculation and not calculation.is_provisional:
        if calculation.overridden and calculation.override_value is not None:
            record.human_score = calculation.override_value
            record.human_band = calculation.override_band or _band_for_score(calculation.override_value, risk_bands)
        else:
            record.human_score = calculation.final_score
            record.human_band = calculation.risk_band
    else:
        record.human_score = None
        record.human_band = None

    if record.human_band and record.ai_risk_band:
        record.band_agreement = record.human_band.upper() == record.ai_risk_band.upper()
    else:
        record.band_agreement = None
    if record.human_score is not None and record.ai_overall_score is not None:
        record.score_difference = round(record.human_score - record.ai_overall_score, 2)
    else:
        record.score_difference = None

    # --- Factor level. ---
    current = (
        db.query(RiskFactor)
        .filter(RiskFactor.assessment_id == assessment.id, RiskFactor.is_current.is_(True))
        .all()
    )
    # P4: a factor only a fixed Stage 4 rule made applicable is not an AI risk.
    def rule_made_applicable(factor) -> bool:
        return any(t.get("effect") in ("FORCED_APPLICABLE", "ADDED") for t in factor.get_rule_triggers())

    ai_current = [f for f in current if f.source == "AI" and f.applicable and not rule_made_applicable(f)]
    human_added = [f for f in current if f.source != "AI" and not f.excluded]
    record.human_added_factors = len(human_added)
    record.ai_factors_excluded = sum(1 for f in ai_current if f.excluded)

    # Applicability agreement, once a human has actually reviewed the
    # factors (rated, excluded or added one, or finalised inherent risk).
    reviewed = bool(
        (calculation and not calculation.is_provisional)
        or human_added
        or any(f.excluded or (f.likelihood and f.impact) for f in ai_current)
    )
    snapshot = record.get_ai_factors()
    if reviewed and snapshot:
        final_applicable = {
            f.category for f in current if f.applicable and not f.excluded
        }
        # P4: a category a fixed rule made applicable has no human outcome
        # until someone rates or excludes it, so it isn't compared yet.
        undecided = {
            f.category for f in current
            if rule_made_applicable(f) and not f.excluded and not (f.likelihood and f.impact)
        }
        compared = [f for f in snapshot if f.get("category") not in undecided]
        record.applicability_compared = len(compared)
        record.applicability_agreeing = sum(
            1 for f in compared if bool(f.get("applicable")) == (f.get("category") in final_applicable)
        )
    else:
        record.applicability_compared = 0
        record.applicability_agreeing = 0

    # Band agreement per factor -- the AI-assigned severity vs the
    # analyst's likelihood x impact band, where an analyst has rated it.
    advisory = {
        f.get("category"): str(f.get("severity") or f.get("model_severity")).upper()
        for f in snapshot
        if f.get("applicable") and (f.get("severity") or f.get("model_severity"))
        and (f.get("model_score") is not None or f.get("model_severity"))
    }
    rated = [
        f for f in ai_current if not f.excluded and f.likelihood and f.impact and f.category in advisory
    ]
    record.factors_rated = len(rated)
    record.factors_agreeing = sum(
        1
        for f in rated
        if determine_risk_band(
            compute_factor_score(f.likelihood, f.impact, config.get("likelihood_scale"), config.get("impact_scale")),
            risk_bands,
        ).upper()
        == advisory[f.category]
    )

    # --- Groundedness (on the snapshot, so later edits don't change it). ---
    source_terms = _terms(_source_text(db, assessment))
    applicable_snapshot = [f for f in snapshot if f.get("applicable")]
    ratios = []
    for factor in applicable_snapshot:
        terms = _terms(factor.get("rationale", ""))
        ratios.append(len(terms & source_terms) / len(terms) if terms else 0.0)
    record.grounded_factors = sum(1 for r in ratios if r >= GROUNDED_THRESHOLD)
    record.groundedness_score = round(sum(ratios) / len(ratios), 3) if ratios else None

    record.unsupported_findings = (
        db.query(ChallengeFinding)
        .filter(
            ChallengeFinding.assessment_id == assessment.id,
            ChallengeFinding.category == "UNSUPPORTED_CONCLUSION",
        )
        .count()
    )

    # --- Human overrides of AI output. ---
    record.human_overrides = (
        db.query(AssessmentOverride).filter(AssessmentOverride.assessment_id == assessment.id).count()
        + db.query(InherentRiskCalculation)
        .filter(
            InherentRiskCalculation.assessment_id == assessment.id,
            InherentRiskCalculation.overridden.is_(True),
        )
        .count()
        + db.query(AuditEvent)
        .filter(
            AuditEvent.assessment_id == assessment.id,
            AuditEvent.action == "MANUAL_SCORE_OVERRIDE",
        )
        .count()
    )

    # --- Control mapping: AI-extracted controls vs recorded controls. ---
    extracted = _ai_extracted_controls(db, assessment.id)
    recorded_types = {
        c.control_type
        for c in db.query(Control).filter(Control.assessment_id == assessment.id, Control.is_current.is_(True))
    }
    record.controls_ai_extracted = len(extracted)
    record.controls_matched = sum(1 for text in extracted if _control_type_for(text) in recorded_types)

    record.evaluated_at = _now()
    return record


def refresh_current_evaluations(db: Session, assessment_ids: list[int] | None = None) -> list[AIEvaluationRecord]:
    query = db.query(AIEvaluationRecord).filter(AIEvaluationRecord.is_current.is_(True))
    if assessment_ids is not None:
        if not assessment_ids:
            return []
        query = query.filter(AIEvaluationRecord.assessment_id.in_(assessment_ids))
    records = query.all()
    for record in records:
        refresh_evaluation(db, record)
    return records


def latest_model_for(db: Session, assessment_id: int, purpose: str) -> str | None:
    log = (
        db.query(AIUsageLog)
        .filter(AIUsageLog.assessment_id == assessment_id, AIUsageLog.purpose == purpose)
        .order_by(AIUsageLog.created_at.desc(), AIUsageLog.id.desc())
        .first()
    )
    return (log.response_model or log.requested_model) if log else None

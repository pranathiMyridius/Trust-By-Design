"""
Stage 9 (R9.1-R9.4): assembles a structured, decision-ready
AssessmentDraft from every prior stage's already-computed data, using
the LLM (app/ai/assessment_draft_generator.py) only to write narrative
prose about that data -- never to compute or alter a score. Falls back
to plain deterministic templates if the model is unavailable, so the
draft is never blocked on an LLM call (R9.1's "structured output" still
holds either way; only the executive summary/recommendation wording
degrades to something plainer).
"""

import json
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.ai.assessment_draft_generator import (
    OPENROUTER_MODEL,
    DraftNarrativeError,
    generate_draft_narrative,
)
from app.ai.metering import ai_assessment_context
from app.models.assessment import Assessment
from app.models.assessment_document import AssessmentDocument
from app.models.assessment_draft import AssessmentDraft
from app.models.assessment_intelligence import AssessmentIntelligence
from app.models.control import Control, ControlAssessment, ControlCondition, ControlGap
from app.models.inherent_risk_calculation import InherentRiskCalculation
from app.models.risk_factor import RiskFactor
from app.risk_engine.methodology import get_methodology_config
from app.services.consistency_check import detect_inconsistencies


def _current(query):
    return query.all()


def _gather_context(db: Session, assessment: Assessment) -> dict:
    intelligence = (
        db.query(AssessmentIntelligence)
        .filter(AssessmentIntelligence.assessment_id == assessment.id)
        .first()
    )

    factors = _current(
        db.query(RiskFactor).filter(
            RiskFactor.assessment_id == assessment.id,
            RiskFactor.is_current.is_(True),
        )
    )

    applicable_factors = [f for f in factors if f.applicable and not f.excluded]

    inherent_calc = (
        db.query(InherentRiskCalculation)
        .filter(
            InherentRiskCalculation.assessment_id == assessment.id,
            InherentRiskCalculation.is_current.is_(True),
        )
        .first()
    )

    controls = _current(
        db.query(Control).filter(
            Control.assessment_id == assessment.id,
            Control.is_current.is_(True),
        )
    )

    control_assessments_by_control: dict[int, ControlAssessment] = {}
    for control in controls:
        current = (
            db.query(ControlAssessment)
            .filter(
                ControlAssessment.control_id == control.id,
                ControlAssessment.is_current.is_(True),
            )
            .first()
        )
        if current:
            control_assessments_by_control[control.id] = current

    gaps = _current(
        db.query(ControlGap).filter(
            ControlGap.assessment_id == assessment.id,
            ControlGap.resolved.is_(False),
        )
    )

    conditions = _current(
        db.query(ControlCondition).filter(
            ControlCondition.assessment_id == assessment.id,
            ControlCondition.status.notin_(["COMPLETED", "CANCELLED"]),
        )
    )

    documents = _current(
        db.query(AssessmentDocument).filter(
            AssessmentDocument.assessment_id == assessment.id,
            AssessmentDocument.is_current.is_(True),
        )
    )

    # Deferred import: app.api.assessments imports this module, so a
    # module-level import here would be circular.
    from app.api.assessments import _compute_evidence_gaps

    evidence_issues = _compute_evidence_gaps(assessment, intelligence, documents)
    conflicts = detect_inconsistencies(assessment, intelligence)

    return {
        "assessment": assessment,
        "intelligence": intelligence,
        "factors": factors,
        "applicable_factors": applicable_factors,
        "inherent_calc": inherent_calc,
        "controls": controls,
        "control_assessments_by_control": control_assessments_by_control,
        "gaps": gaps,
        "conditions": conditions,
        "documents": documents,
        "evidence_issues": evidence_issues,
        "conflicts": conflicts,
    }


def _business_profile(intelligence: AssessmentIntelligence | None) -> dict:
    if not intelligence:
        return {}

    return {
        # R3.1: every structured profile field, so the draft carries the
        # whole validated profile.
        "business_line": intelligence.business_line,
        "customer_type": intelligence.customer_type,
        "customer_segments": intelligence.get_list("customer_segments"),
        "countries": intelligence.get_list("countries"),
        "transaction_origin": intelligence.transaction_origin,
        "transaction_destination": intelligence.transaction_destination,
        "channels": intelligence.get_list("channels"),
        "transaction_volume": intelligence.transaction_volume,
        "average_transaction_size": intelligence.average_transaction_size,
        "transaction_frequency": intelligence.transaction_frequency,
        "payment_methods": intelligence.get_list("payment_methods"),
        "onboarding_approach": intelligence.onboarding_approach,
        "third_party_vendors": intelligence.get_list("third_party_vendors"),
        "technologies": intelligence.get_list("technologies"),
        "ownership_entity_structure": intelligence.ownership_entity_structure,
        "regulatory_considerations": intelligence.get_list("regulatory_considerations"),
        "existing_controls": intelligence.get_list("existing_controls"),
        "confirmed": intelligence.confirmed,
        "confirmed_by": intelligence.confirmed_by,
        "confirmed_at": intelligence.confirmed_at.isoformat() if intelligence.confirmed_at else None,
    }


def _mapped_controls(
    controls: list[Control],
    control_assessments_by_control: dict[int, ControlAssessment],
) -> list[dict]:
    result = []
    for control in controls:
        assessment_row = control_assessments_by_control.get(control.id)
        result.append(
            {
                "control_id": control.id,
                "risk_factor_id": control.risk_factor_id,
                "control_type": control.control_type,
                "description": control.description,
                "owner": control.owner,
                "operating_status": control.operating_status,
                "design_adequacy": assessment_row.design_adequacy if assessment_row else "NOT_ASSESSED",
                "operating_effectiveness": assessment_row.operating_effectiveness if assessment_row else "UNVERIFIED",
                "has_evidence": assessment_row.has_evidence if assessment_row else False,
            }
        )
    return result


def _control_effectiveness_summary(
    controls: list[Control],
    control_assessments_by_control: dict[int, ControlAssessment],
) -> dict:
    total = len(controls)
    effective = sum(
        1
        for c in controls
        if control_assessments_by_control.get(c.id)
        and control_assessments_by_control[c.id].operating_effectiveness == "EFFECTIVE"
    )
    unverified = sum(
        1
        for c in controls
        if control_assessments_by_control.get(c.id)
        and control_assessments_by_control[c.id].operating_effectiveness == "UNVERIFIED"
    )

    return {
        "total_controls": total,
        "effective_controls": effective,
        "unverified_controls": unverified,
        "effectiveness_rate": round(effective / total, 2) if total else None,
    }


def _fallback_narrative(context: dict) -> dict:
    """Plain deterministic prose, used when the LLM is unavailable."""

    assessment: Assessment = context["assessment"]
    applicable_factors: list[RiskFactor] = context["applicable_factors"]
    inherent_calc: InherentRiskCalculation | None = context["inherent_calc"]

    band = (inherent_calc.override_band or inherent_calc.risk_band) if inherent_calc else "UNKNOWN"
    residual_band = assessment.residual_risk_level or "UNKNOWN"

    executive_summary = (
        f"{assessment.title} ({assessment.change_type}) was assessed with an inherent "
        f"risk band of {band} and a residual risk band of {residual_band} after "
        f"applying {len(context['controls'])} mapped control(s). "
        f"{len(applicable_factors)} risk categor{'y' if len(applicable_factors) == 1 else 'ies'} "
        "applied to this change."
    )

    risk_statements = [
        {
            "category": factor.category,
            "statement": (
                f"{factor.category.replace('_', ' ').title()}: score "
                f"{factor.score:g} ({factor.severity}). {factor.rationale}"
            ),
        }
        for factor in applicable_factors
    ]

    recommendation = (
        "Approve with conditions"
        if context["conditions"] or context["gaps"]
        else "Approve"
    ) if residual_band in ("LOW", "MEDIUM") else "Escalate for committee review"

    return {
        "executive_summary": executive_summary,
        "business_change_description": assessment.description or "",
        "risk_statements": risk_statements,
        "assumptions": [],
        "unresolved_questions": [],
        "low_confidence_items": [],
        "unsupported_conclusions": [],
        "analyst_recommendation": (
            f"Suggested outcome: {recommendation}. Based on a residual risk band of {residual_band} "
            f"with {len(context['gaps'])} open control gap(s) and "
            f"{len(context['conditions'])} open condition(s)."
        ),
    }


def _inherent_section(calc) -> dict:
    """R9.1/R6.7: the inherent result of record -- an analyst override
    where there is one, with the calculated value kept beside it."""

    if calc is None:
        return {"score": None, "band": None, "escalated": False}

    section = {
        "score": calc.final_score,
        "band": calc.risk_band,
        "escalated": calc.escalated,
        "provisional": calc.is_provisional,
    }
    if calc.overridden and calc.override_band:
        section.update(
            {
                "score": calc.override_value,
                "band": calc.override_band,
                "calculated_score": calc.calculated_score,
                "calculated_band": calc.calculated_band,
                "overridden": True,
                "override_reason": calc.override_reason,
                "override_by": calc.override_by,
            }
        )
    return section


def _residual_section(db: Session, assessment: Assessment) -> dict:
    """R8: the calculated residual risk and, where an analyst confirmed or
    adjusted it, the confirmed value with the reason."""

    from app.services.residual_risk_service import current_residual_calculation

    section = {"score": assessment.residual_score, "band": assessment.residual_risk_level}
    calc = current_residual_calculation(db, assessment.id)
    if calc is not None and calc.confirmed_band:
        section.update(
            {
                "confirmed_band": calc.confirmed_band,
                "confirmed_score": calc.confirmed_score,
                "confirmation_reason": calc.confirmation_reason,
                "confirmed_by": calc.confirmed_by,
            }
        )
    return section


def generate_assessment_draft(
    db: Session,
    assessment: Assessment,
    requested_by: str | None = None,
) -> AssessmentDraft:
    """Does not commit -- the caller commits."""

    context = _gather_context(db, assessment)
    config = get_methodology_config(db)

    llm_context = {
        "title": assessment.title,
        "change_type": assessment.change_type,
        "description": assessment.description,
        "business_profile": _business_profile(context["intelligence"]),
        "applicable_risk_categories": [
            {
                "category": f.category,
                "score": f.score,
                "severity": f.severity,
                "indicators": f.get_indicators(),
                "rationale": f.rationale,
                "misuse_scenario": f.misuse_scenario,
            }
            for f in context["applicable_factors"]
        ],
        "inherent_risk": _inherent_section(context["inherent_calc"]),
        "control_effectiveness": _control_effectiveness_summary(
            context["controls"], context["control_assessments_by_control"]
        ),
        "residual_risk": _residual_section(db, assessment),
        "open_control_gaps": [gap.gap_type for gap in context["gaps"]],
        "open_conditions": [c.description for c in context["conditions"]],
        "evidence_gaps": [issue.title for issue in context["evidence_issues"] if issue.missing],
        "conflicting_evidence": context["conflicts"],
    }

    generation_method = "llm+deterministic"
    model_version = None

    try:
        with ai_assessment_context(assessment.id):
            narrative = generate_draft_narrative(llm_context)
        model_version = OPENROUTER_MODEL
    except DraftNarrativeError:
        narrative = _fallback_narrative(context)
        generation_method = "deterministic-template"

    required_approvals = config["required_approvals"].get(
        assessment.residual_risk_level or "LOW",
        config["required_approvals"].get("LOW", []),
    )

    missing_information = [
        issue.title for issue in context["evidence_issues"] if issue.missing
    ] + [f"Open control condition: {c.description}" for c in context["conditions"]]

    # R8.6: open control conditions plus the recommended conditions an
    # analyst accepted or modified (never one still only proposed).
    from app.services.residual_conditions import adopted_conditions

    recommended_conditions = [c.description for c in context["conditions"]] + adopted_conditions(db, assessment.id)

    uncertainty = {
        "low_confidence_items": narrative.get("low_confidence_items", []),
        "unsupported_conclusions": narrative.get("unsupported_conclusions", []),
        "conflicting_evidence": context["conflicts"],
        "unresolved_questions": narrative.get("unresolved_questions", []),
    }

    source_evidence = [
        {"document_id": d.id, "filename": d.filename, "document_type": d.document_type}
        for d in context["documents"]
    ]

    previous_current = (
        db.query(AssessmentDraft)
        .filter(
            AssessmentDraft.assessment_id == assessment.id,
            AssessmentDraft.is_current.is_(True),
        )
        .all()
    )

    now = datetime.now(timezone.utc)
    next_version = 1
    for previous in previous_current:
        previous.is_current = False
        previous.superseded_at = now
        next_version = max(next_version, previous.version + 1)

    draft = AssessmentDraft(
        assessment_id=assessment.id,
        executive_summary=narrative.get("executive_summary", ""),
        business_change_description=narrative.get("business_change_description", assessment.description or ""),
        business_profile=json.dumps(_business_profile(context["intelligence"])),
        applicable_risk_categories=json.dumps([f.category for f in context["applicable_factors"]]),
        risk_indicators=json.dumps(
            sorted({indicator for f in context["applicable_factors"] for indicator in f.get_indicators()})
        ),
        risk_statements=json.dumps(narrative.get("risk_statements", [])),
        inherent_risk=json.dumps(llm_context["inherent_risk"]),
        evidence_references=json.dumps(source_evidence),
        mapped_controls=json.dumps(
            _mapped_controls(context["controls"], context["control_assessments_by_control"])
        ),
        control_effectiveness=json.dumps(llm_context["control_effectiveness"]),
        residual_risk=json.dumps(llm_context["residual_risk"]),
        risk_gaps=json.dumps(
            [
                {
                    "gap_type": gap.gap_type,
                    "description": gap.description,
                    "risk_factor_id": gap.risk_factor_id,
                    "control_id": gap.control_id,
                }
                for gap in context["gaps"]
            ]
        ),
        assumptions=json.dumps(narrative.get("assumptions", [])),
        missing_information=json.dumps(missing_information),
        recommended_conditions=json.dumps(recommended_conditions),
        analyst_recommendation=narrative.get("analyst_recommendation", ""),
        required_approvals=json.dumps(required_approvals),
        uncertainty=json.dumps(uncertainty),
        generated_at=now,
        generation_method=generation_method,
        model_version=model_version,
        config_version=config["methodology_name"],
        source_evidence=json.dumps(source_evidence),
        generated_by=requested_by or "System",
        is_edited=False,
        version=next_version,
        is_current=True,
    )

    db.add(draft)
    db.flush()

    return draft

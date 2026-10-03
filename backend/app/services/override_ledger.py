"""
R6.7 / R10.2 / R10.4: the override ledger (assessment_overrides).

Three rules:

1. The system value an override is compared against is always read here,
   from the record, at override time -- never taken from the request
   (`resolve_system_value`).
2. A ledger row never changes the calculation it overrides. Calculated
   values stay on their own versioned records; the human value of record
   sits beside them (inherent: override_*; residual: confirmed_*).
3. A free-standing override is PROPOSED until a different, independent
   reviewer confirms or rejects it. Only APPLIED (made through a typed
   endpoint under its own role rules) and CONFIRMED values count in the
   decision package (`value_comparisons`).
"""

from __future__ import annotations

import json
from typing import Any

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.models.assessment import Assessment
from app.models.assessment_override import (
    AUTHORISED_REVIEW_STATUSES,
    REVIEW_APPLIED,
    AssessmentOverride,
)
from app.models.user import User, UserRole

# Provisional (pending governance confirmation): who may independently
# confirm or reject a PROPOSED override. Administrators are deliberately
# not included -- system administration is not a review authority.
OVERRIDE_REVIEWER_ROLES = {UserRole.FCRM_ANALYST.value, UserRole.MANAGER.value}

SYSTEM = "SYSTEM"

_FACTOR_FIELDS = {
    "applicable",
    "category",
    "likelihood",
    "impact",
    "score",
    "severity",
    "rationale",
    "misuse_scenario",
    "indicators",
}
_CONTROL_EFFECTIVENESS_FIELDS = {
    "design_adequacy",
    "operating_effectiveness",
    "has_evidence",
    "coverage_complete",
    "depends_on_unavailable_data",
}


def _text(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, (list, dict)):
        return json.dumps(value, sort_keys=True)
    return str(value)


def _require_entity(entity_id: str | None, section: str) -> int:
    try:
        return int(entity_id)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        raise HTTPException(status_code=422, detail=f"entity_id (a record id) is required for {section} overrides.")


def _unknown_field(section: str, field_name: str, allowed) -> HTTPException:
    return HTTPException(
        status_code=422,
        detail=f"'{field_name}' is not a {section} field that can be overridden. Use one of: {', '.join(sorted(allowed))}.",
    )


def resolve_system_value(
    db: Session,
    assessment: Assessment,
    section: str,
    field_name: str,
    entity_id: str | None,
) -> str | None:
    """The record's own value for (section, field, entity) right now. 422
    when the target can't be identified -- an override must point at a
    real value."""

    from app.models.assessment_intelligence import AssessmentIntelligence
    from app.models.control import CONTROL_CONFIG_FIELDS, Control, ControlAssessment
    from app.models.recommended_condition import RecommendedCondition
    from app.models.risk_factor import RiskFactor
    from app.services.residual_risk_service import current_inherent_calculation, current_residual_calculation

    if section == "INTAKE_FIELD":
        intelligence = (
            db.query(AssessmentIntelligence).filter(AssessmentIntelligence.assessment_id == assessment.id).first()
        )
        if intelligence is not None and intelligence.raw_extraction:
            try:
                extracted = json.loads(intelligence.raw_extraction)
            except ValueError:
                extracted = {}
            if field_name in extracted:
                return _text(extracted[field_name])
        if intelligence is not None and field_name in AssessmentIntelligence.__table__.columns:
            return _text(getattr(intelligence, field_name))
        if field_name in Assessment.__table__.columns:
            return _text(getattr(assessment, field_name))
        raise HTTPException(status_code=422, detail=f"'{field_name}' is not an intake or profile field.")

    if section in {"RISK_CATEGORY", "FACTOR_RATING", "RISK_RATIONALE"}:
        factor = (
            db.query(RiskFactor)
            .filter(
                RiskFactor.id == _require_entity(entity_id, section),
                RiskFactor.assessment_id == assessment.id,
                RiskFactor.is_current.is_(True),
            )
            .first()
        )
        if factor is None:
            raise HTTPException(status_code=422, detail="entity_id is not a current risk factor of this assessment.")
        if field_name not in _FACTOR_FIELDS:
            raise _unknown_field(section, field_name, _FACTOR_FIELDS)
        if field_name == "indicators":
            return _text(factor.get_indicators())
        if field_name in {"likelihood", "impact"}:
            # The system's value for a rating is the AI suggestion (the
            # analyst's own rating is the human value).
            suggested = getattr(factor, f"ai_suggested_{field_name}")
            return _text(suggested if suggested is not None else getattr(factor, field_name))
        return _text(getattr(factor, field_name))

    if section in {"CONTROL_MAPPING", "CONTROL_EFFECTIVENESS"}:
        control = (
            db.query(Control)
            .filter(Control.id == _require_entity(entity_id, section), Control.assessment_id == assessment.id)
            .first()
        )
        if control is None:
            raise HTTPException(status_code=422, detail="entity_id is not a control of this assessment.")
        if section == "CONTROL_MAPPING":
            if field_name not in CONTROL_CONFIG_FIELDS:
                raise _unknown_field(section, field_name, CONTROL_CONFIG_FIELDS)
            return _text(getattr(control, field_name))
        if field_name not in _CONTROL_EFFECTIVENESS_FIELDS:
            raise _unknown_field(section, field_name, _CONTROL_EFFECTIVENESS_FIELDS)
        current = (
            db.query(ControlAssessment)
            .filter(ControlAssessment.control_id == control.id, ControlAssessment.is_current.is_(True))
            .first()
        )
        return _text(getattr(current, field_name)) if current else "NOT_ASSESSED"

    if section == "RESIDUAL_RISK":
        calc = current_residual_calculation(db, assessment.id)
        if calc is None:
            raise HTTPException(status_code=422, detail="Residual risk has not been calculated yet.")
        mapping = {"band": calc.residual_band, "residual_band": calc.residual_band, "score": calc.residual_score, "residual_score": calc.residual_score}
        if field_name not in mapping:
            raise _unknown_field(section, field_name, mapping)
        return _text(mapping[field_name])

    if section == "INHERENT_RISK":
        calc = current_inherent_calculation(db, assessment.id)
        if calc is None:
            raise HTTPException(status_code=422, detail="Inherent risk has not been calculated yet.")
        mapping = {"band": calc.calculated_band, "score": calc.calculated_score}
        if field_name not in mapping:
            raise _unknown_field(section, field_name, mapping)
        return _text(mapping[field_name])

    if section == "CONDITION":
        condition = (
            db.query(RecommendedCondition)
            .filter(
                RecommendedCondition.id == _require_entity(entity_id, section),
                RecommendedCondition.assessment_id == assessment.id,
            )
            .first()
        )
        if condition is None:
            raise HTTPException(status_code=422, detail="entity_id is not a recommended condition of this assessment.")
        allowed = {"recommended_text", "status", "condition_type"}
        if field_name not in allowed:
            raise _unknown_field(section, field_name, allowed)
        return _text(getattr(condition, field_name))

    raise HTTPException(status_code=422, detail=f"Unknown override section {section}.")


def record_applied(
    db: Session,
    assessment_id: int,
    section: str,
    field_name: str,
    entity_id: Any,
    system_value: Any,
    human_value: Any,
    reason: str,
    user: User,
) -> AssessmentOverride:
    """Ledger entry for a change a typed endpoint has already made under
    its own rules. Does not commit."""

    row = AssessmentOverride(
        assessment_id=assessment_id,
        section=section,
        field_name=field_name,
        entity_id=None if entity_id is None else str(entity_id),
        ai_value=_text(system_value),
        ai_value_source=SYSTEM,
        human_value=_text(human_value) or "",
        reason=reason,
        overridden_by=user.full_name or user.email,
        overridden_by_id=user.id,
        review_status=REVIEW_APPLIED,
        origin="TYPED",
    )
    # P3: materiality from the actual change; a material typed change still
    # needs independent review and approval before the committee.
    from app.governance.overrides import classify_entry

    classify_entry(db, row)
    db.add(row)
    return row


# -- R10.4: calculated vs human, for the decision package -------------------


def _difference(calculated: Any, human: Any) -> str | None:
    if calculated is None or human is None:
        return None
    try:
        delta = float(human) - float(calculated)
    except (TypeError, ValueError):
        return None if str(calculated) == str(human) else "changed"
    return f"{delta:+g}"


def _entry(section, item, calculated, human, reason, by, at, status, counts) -> dict[str, Any]:
    return {
        "section": section,
        "item": item,
        "calculated_value": _text(calculated),
        "human_value": _text(human),
        "difference": _difference(calculated, human),
        "reason": reason,
        "by": by,
        "at": at.isoformat() if hasattr(at, "isoformat") else at,
        "review_status": status,
        # Whether this human value is the one the decision relies on.
        "counts_in_decision": counts,
    }


def value_comparisons(db: Session, assessment: Assessment) -> list[dict[str, Any]]:
    """
    Every place a human value stands beside a calculated one, as it stands
    now. Calculated values are reported from their own records and are
    never altered by anything here.
    """

    from app.models.assessment_fcrm_review import AssessmentFcrmReview
    from app.models.risk_result import RiskResult
    from app.services.inherent_risk_service import current_factors
    from app.services.residual_risk_service import current_inherent_calculation, current_residual_calculation

    entries: list[dict[str, Any]] = []

    inherent = current_inherent_calculation(db, assessment.id)
    if inherent is not None and inherent.overridden:
        entries.append(
            _entry(
                "INHERENT_RISK",
                "Overall inherent risk",
                f"{inherent.calculated_score:g} ({inherent.calculated_band})" if inherent.calculated_score is not None else inherent.calculated_band,
                f"{inherent.override_value:g} ({inherent.override_band})" if inherent.override_value is not None else inherent.override_band,
                inherent.override_reason,
                inherent.override_by,
                inherent.override_at,
                REVIEW_APPLIED,
                True,
            )
        )
        entries[-1]["difference"] = _difference(inherent.calculated_score, inherent.override_value)

    residual = current_residual_calculation(db, assessment.id)
    if residual is not None and residual.confirmed_band:
        entries.append(
            _entry(
                "RESIDUAL_RISK",
                "Residual risk",
                residual.residual_band if residual.residual_score is None else f"{residual.residual_score:g} ({residual.residual_band})",
                residual.confirmed_band if residual.confirmed_score is None else f"{residual.confirmed_score:g} ({residual.confirmed_band})",
                residual.confirmation_reason,
                residual.confirmed_by,
                residual.confirmed_at,
                REVIEW_APPLIED,
                True,
            )
        )
        entries[-1]["difference"] = _difference(residual.residual_score, residual.confirmed_score)

    ledger = (
        db.query(AssessmentOverride)
        .filter(AssessmentOverride.assessment_id == assessment.id)
        .order_by(AssessmentOverride.created_at.asc(), AssessmentOverride.id.asc())
        .all()
    )
    latest_rating_reason = {
        row.entity_id: row
        for row in ledger
        if row.section == "FACTOR_RATING" and row.review_status == REVIEW_APPLIED
    }

    for factor in current_factors(db, assessment):
        if factor.likelihood is None or factor.ai_suggested_likelihood is None:
            continue
        if (factor.likelihood, factor.impact) == (factor.ai_suggested_likelihood, factor.ai_suggested_impact):
            continue
        row = latest_rating_reason.get(str(factor.id))
        entries.append(
            _entry(
                "FACTOR_RATING",
                f"{factor.category} likelihood x impact",
                f"{factor.ai_suggested_likelihood} x {factor.ai_suggested_impact}",
                f"{factor.likelihood} x {factor.impact}",
                row.reason if row else None,
                factor.rated_by,
                factor.rated_at,
                REVIEW_APPLIED,
                True,
            )
        )

    # Free-standing overrides and other typed ledger entries not shown above.
    shown_live = {"INHERENT_RISK", "RESIDUAL_RISK", "FACTOR_RATING"}
    from app.governance.overrides import in_effect, state

    for row in ledger:
        if row.section in shown_live and (row.review_status == REVIEW_APPLIED or row.origin == "TYPED"):
            continue
        status = row.review_status or "LEGACY_UNREVIEWED"
        entries.append(
            _entry(
                row.section,
                row.field_name + (f" (#{row.entity_id})" if row.entity_id else ""),
                row.ai_value,
                row.human_value,
                row.reason,
                row.overridden_by,
                row.created_at,
                status,
                # P3: a proposal counts once confirmed and, if material,
                # approved; legacy rows keep the P2 rule.
                in_effect(db, row) if row.materiality else row.review_status in AUTHORISED_REVIEW_STATUSES,
            )
        )
        entries[-1]["materiality"] = row.materiality
        entries[-1]["governance_state"] = state(db, row)
        if row.reviewed_by:
            entries[-1]["reviewed_by"] = row.reviewed_by
            entries[-1]["review_note"] = row.review_note

    # The FCRM review's per-dimension ratings (legacy six-dimension
    # results), beside the calculated severity they reconcile.
    review = db.query(AssessmentFcrmReview).filter(AssessmentFcrmReview.assessment_id == assessment.id).first()
    if review is not None:
        try:
            ratings = json.loads(review.human_ratings or "{}")
        except ValueError:
            ratings = {}
        results = {
            str(result.id): result
            for result in db.query(RiskResult).filter(RiskResult.assessment_id == assessment.id).all()
        }
        for result_id, human_severity in ratings.items():
            result = results.get(str(result_id))
            if result is None or str(human_severity) == str(result.severity):
                continue
            entries.append(
                _entry(
                    "FCRM_REVIEW",
                    f"{result.dimension} severity",
                    result.severity,
                    human_severity,
                    review.justification,
                    review.reviewed_by,
                    review.updated_at,
                    REVIEW_APPLIED,
                    True,
                )
            )

    return entries

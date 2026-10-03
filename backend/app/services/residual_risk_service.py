"""
Residual risk: inherent band x control-effectiveness rating, through the
methodology's versioned residual grid, then raised to any non-mitigable
rule's minimum level. Persists each result as a new versioned
ResidualRiskCalculation row, like inherent_risk_service does for inherent.

This replaces the old `residual = inherent_score - control_reduction`
subtraction as the source of residual_risk_level. The subtraction survives
only as an indicative number clamped into the residual band.
"""

import json
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.models.assessment import Assessment
from app.models.inherent_risk_calculation import InherentRiskCalculation
from app.models.residual_risk_calculation import ResidualRiskCalculation
from app.risk_engine.methodology import get_methodology_config, mark_methodology_used
from app.risk_engine.scoring import (
    calculate_residual_risk,
    determine_risk_band,
    residual_position_score,
)
from app.services.inherent_risk_service import UNRATED_BAND


def current_inherent_calculation(db: Session, assessment_id: int) -> InherentRiskCalculation | None:
    return (
        db.query(InherentRiskCalculation)
        .filter(
            InherentRiskCalculation.assessment_id == assessment_id,
            InherentRiskCalculation.is_current.is_(True),
        )
        .first()
    )


def current_residual_calculation(db: Session, assessment_id: int) -> ResidualRiskCalculation | None:
    return (
        db.query(ResidualRiskCalculation)
        .filter(
            ResidualRiskCalculation.assessment_id == assessment_id,
            ResidualRiskCalculation.is_current.is_(True),
        )
        .first()
    )


def _inherent_of_record(calc: InherentRiskCalculation | None) -> tuple[str | None, float | None]:
    """The inherent band and score residual works from, override included."""

    if calc is None:
        return None, None
    if calc.overridden and calc.override_band:
        return calc.override_band, calc.override_value
    if calc.risk_band == UNRATED_BAND or calc.is_provisional:
        # A provisional inherent result is not a basis for residual risk.
        return None, None
    return calc.risk_band, calc.final_score


def compute_residual_risk(db: Session, assessment: Assessment) -> dict:
    """The residual result without persisting a calculation row."""

    from app.control_engine.engine import recompute_control_state

    config = get_methodology_config(db)
    inherent_calc = current_inherent_calculation(db, assessment.id)
    inherent_band, inherent_score = _inherent_of_record(inherent_calc)

    control_state = recompute_control_state(db, assessment.id)
    control_rating = control_state["control_rating"]

    result = calculate_residual_risk(
        inherent_band,
        control_rating,
        triggered_rules=inherent_calc.get_triggered_rules() if inherent_calc else [],
        residual_grid=config["residual_grid"],
        risk_bands=config["risk_bands"],
    )

    if inherent_calc is not None and inherent_band is None and result["reason"]:
        result["reason"] = (
            "The inherent risk result is provisional or unrated, so there is "
            "no inherent band to apply controls to."
        )
    if control_rating is None and inherent_band is not None:
        result["reason"] = "No applicable risk factors to rate controls against."

    # When a policy rule set the band (the inherent band is above what the
    # score alone gives, or a non-mitigable floor held the residual up),
    # no arithmetic position within the band is meaningful: clamping 42
    # into CRITICAL would print 80 and suggest controls raised risk. The
    # band stands alone then.
    band_set_by_policy = bool(result["floors_applied"]) or (
        inherent_band is not None
        and inherent_score is not None
        and inherent_band != determine_risk_band(inherent_score, config["risk_bands"])
    )
    residual_score = (
        None
        if band_set_by_policy
        else residual_position_score(
            inherent_score,
            control_state["control_reduction"],
            result["residual_band"],
            config["risk_bands"],
        )
    )

    return {
        "config": config,
        "inherent_calc": inherent_calc,
        "inherent_band": inherent_band,
        "inherent_score": inherent_score,
        "control_state": control_state,
        "result": result,
        "residual_score": residual_score,
    }


def recalculate_residual_risk(
    db: Session,
    assessment: Assessment,
    frozen: bool = False,
    calculated_by: str | None = None,
) -> ResidualRiskCalculation:
    """
    Does not commit. With frozen=True (arrival at RESIDUAL_RISK) the
    result also becomes the assessment's residual_score/residual_risk_level
    of record.
    """

    computed = compute_residual_risk(db, assessment)
    config = computed["config"]
    inherent_calc = computed["inherent_calc"]
    inherent_band = computed["inherent_band"]
    inherent_score = computed["inherent_score"]
    control_state = computed["control_state"]
    control_rating = control_state["control_rating"]
    result = computed["result"]
    residual_score = computed["residual_score"]

    now = datetime.now(timezone.utc)
    next_version = 1
    for previous in (
        db.query(ResidualRiskCalculation)
        .filter(
            ResidualRiskCalculation.assessment_id == assessment.id,
            ResidualRiskCalculation.is_current.is_(True),
        )
        .all()
    ):
        previous.is_current = False
        previous.superseded_at = now
        next_version = max(next_version, previous.version + 1)

    calculation = ResidualRiskCalculation(
        assessment_id=assessment.id,
        inherent_calculation_id=inherent_calc.id if inherent_calc else None,
        methodology_id=config["methodology_id"],
        methodology_version=config["methodology_version"],
        methodology_fingerprint=config["methodology_fingerprint"],
        inherent_band=inherent_band,
        inherent_score=inherent_score,
        control_rating=control_rating,
        control_ratings=json.dumps(control_state["control_ratings"]),
        control_reduction=control_state["control_reduction"],
        residual_grid=json.dumps(config["residual_grid"]),
        grid_version=result["grid_version"],
        grid_band=result["grid_band"],
        floors_applied=json.dumps(result["floors_applied"]),
        residual_band=result["residual_band"],
        residual_score=residual_score,
        reason=result["reason"],
        frozen=frozen,
        calculated_by=calculated_by or "System",
        calculated_at=now,
        version=next_version,
        is_current=True,
    )
    db.add(calculation)
    mark_methodology_used(db, config["methodology_id"])

    if frozen:
        assessment.residual_score = residual_score
        assessment.residual_risk_level = result["residual_band"]

    db.flush()
    return calculation


def residual_response(calc: ResidualRiskCalculation) -> dict:
    return {
        "id": calc.id,
        "assessment_id": calc.assessment_id,
        "inherent_calculation_id": calc.inherent_calculation_id,
        "methodology_id": calc.methodology_id,
        "methodology_version": calc.methodology_version,
        "methodology_fingerprint": calc.methodology_fingerprint,
        "inherent_band": calc.inherent_band,
        "inherent_score": calc.inherent_score,
        "control_rating": calc.control_rating,
        "control_ratings": calc.get_control_ratings(),
        "control_reduction": calc.control_reduction,
        "residual_grid": calc.get_residual_grid(),
        "grid_version": calc.grid_version,
        "grid_band": calc.grid_band,
        "floors_applied": calc.get_floors_applied(),
        "residual_band": calc.residual_band,
        "residual_score": calc.residual_score,
        "reason": calc.reason,
        # R8: the human-confirmed residual risk and how it differs.
        "confirmed_band": calc.confirmed_band,
        "confirmed_score": calc.confirmed_score,
        "confirmation_reason": calc.confirmation_reason,
        "confirmed_by": calc.confirmed_by,
        "confirmed_at": calc.confirmed_at,
        "confirmation_differs": bool(calc.confirmed_band) and calc.confirmed_band != calc.residual_band,
        "confirmed_score_difference": (
            round(calc.confirmed_score - calc.residual_score, 2)
            if calc.confirmed_score is not None and calc.residual_score is not None
            else None
        ),
        "frozen": calc.frozen,
        "calculated_by": calc.calculated_by,
        "calculated_at": calc.calculated_at,
        "version": calc.version,
    }

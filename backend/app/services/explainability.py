"""
Stage 16 (R16.2): "provide a clear explanation of how each rating was
determined." Rather than re-deriving anything, this reads the frozen
snapshots and reasoning already captured by earlier stages -- the
InherentRiskCalculation's frozen inputs/weights/bands (Stage 6), the
control-engine's per-risk-factor reduction (Stage 7), any analyst
overrides (Stage 10), and the challenge review's trigger/finding state
(Stage 11) -- and assembles them into one human-readable explanation per
rating, so nothing here is a second source of truth for a score.
"""

from sqlalchemy.orm import Session

from app.control_engine import scoring as control_scoring
from app.control_engine.engine import _assessment_to_dict, _current_assessment_for_control
from app.models.assessment import Assessment
from app.models.assessment_override import AssessmentOverride
from app.models.challenge_review import ChallengeFinding
from app.models.control import Control
from app.models.inherent_risk_calculation import InherentRiskCalculation
from app.models.risk_factor import RiskFactor


def explain_assessment(db: Session, assessment_id: int) -> dict | None:
    assessment = db.query(Assessment).filter(Assessment.id == assessment_id).first()
    if not assessment:
        return None

    sections = []

    inherent = (
        db.query(InherentRiskCalculation)
        .filter(
            InherentRiskCalculation.assessment_id == assessment_id,
            InherentRiskCalculation.is_current.is_(True),
        )
        .first()
    )

    if inherent:
        explanation = (
            f"Computed as the weighted average of {len(inherent.get_inputs())} rated risk "
            f"factor(s) using method '{inherent.calculation_method}'."
        )
        if inherent.escalated:
            explanation += (
                " An escalation rule forced the band up: "
                + "; ".join(inherent.get_escalation_reasons())
                + "."
            )
        if inherent.is_provisional:
            explanation += " This result is provisional -- not every applicable factor has been rated yet."
        if inherent.overridden:
            explanation += (
                f" An analyst overrode the calculated value ({inherent.calculated_score} / "
                f"{inherent.calculated_band}) to {inherent.override_value} / {inherent.override_band}: "
                f"{inherent.override_reason}"
            )

        sections.append(
            {
                "rating": "Inherent Risk",
                "value": inherent.final_score,
                "level": inherent.risk_band,
                "explanation": explanation,
                "inputs": inherent.get_inputs(),
                "weights": inherent.get_weights(),
                "risk_bands": inherent.get_risk_bands(),
            }
        )

    risk_factors = (
        db.query(RiskFactor)
        .filter(
            RiskFactor.assessment_id == assessment_id,
            RiskFactor.is_current.is_(True),
            RiskFactor.applicable.is_(True),
            RiskFactor.excluded.is_(False),
        )
        .all()
    )

    control_breakdown = []
    points_per_risk = []
    for factor in risk_factors:
        controls = (
            db.query(Control)
            .filter(
                Control.assessment_id == assessment_id,
                Control.risk_factor_id == factor.id,
                Control.is_current.is_(True),
            )
            .all()
        )
        assessment_dicts = [
            _assessment_to_dict(_current_assessment_for_control(db, control.id), control.operating_status)
            for control in controls
        ]
        points = control_scoring.points_for_risk(assessment_dicts)
        points_per_risk.append(points)
        control_breakdown.append(
            {
                "risk_factor_id": factor.id,
                "category": factor.category,
                "mapped_controls": len(controls),
                "reduction_points": points,
            }
        )

    control_reduction = control_scoring.calculate_control_reduction(points_per_risk)

    sections.append(
        {
            "rating": "Control Reduction",
            "value": control_reduction,
            "level": None,
            "explanation": (
                f"Sum of the best (most protective) evidenced control effectiveness "
                f"across {len(risk_factors)} applicable risk(s), capped at "
                f"{control_scoring.MAX_CONTROL_REDUCTION}. A control with no supporting "
                "evidence, incomplete coverage, or a dependency on unavailable data never "
                "contributes full credit."
            ),
            "per_risk_factor": control_breakdown,
        }
    )

    if assessment.residual_risk_level is not None:
        from app.services.residual_risk_service import current_residual_calculation

        residual_calc = current_residual_calculation(db, assessment_id)
        if residual_calc is not None:
            explanation = (
                f"Residual grid v{residual_calc.grid_version} ({residual_calc.methodology_version}): "
                f"inherent {residual_calc.inherent_band} x controls {residual_calc.control_rating} "
                f"-> {residual_calc.grid_band}."
            )
            for floor in residual_calc.get_floors_applied():
                explanation += (
                    f" Held at {floor['band_after']} by non-mitigable rule {floor['rule_code']}."
                )
        else:
            inherent_value = assessment.inherent_score if assessment.inherent_score is not None else assessment.overall_score
            explanation = (
                f"Inherent risk ({inherent_value}) minus the control reduction "
                f"({control_reduction}), floored at 0 (calculated before the residual grid)."
            )
        sections.append(
            {
                "rating": "Residual Risk",
                "value": assessment.residual_score,
                "level": assessment.residual_risk_level,
                "explanation": explanation,
            }
        )

    overrides = (
        db.query(AssessmentOverride)
        .filter(AssessmentOverride.assessment_id == assessment_id)
        .order_by(AssessmentOverride.created_at.asc())
        .all()
    )

    challenge_findings = (
        db.query(ChallengeFinding)
        .filter(ChallengeFinding.assessment_id == assessment_id)
        .order_by(ChallengeFinding.detected_at.desc())
        .all()
    )

    return {
        "assessment_id": assessment_id,
        "sections": sections,
        "overrides": [
            {
                "section": override.section,
                "field_name": override.field_name,
                "entity_id": override.entity_id,
                "ai_value": override.ai_value,
                "human_value": override.human_value,
                "reason": override.reason,
                "overridden_by": override.overridden_by,
                "created_at": override.created_at.isoformat(),
            }
            for override in overrides
        ],
        "challenge_findings": [
            {
                "category": finding.category,
                "severity": finding.severity,
                "description": finding.description,
                "resolution_status": finding.resolution_status,
            }
            for finding in challenge_findings
        ],
    }

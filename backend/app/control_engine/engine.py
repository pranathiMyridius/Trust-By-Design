"""
DB-facing orchestration for Stage 7: given an assessment, look at its
applicable/non-excluded risk factors and whatever controls/assessments are
currently mapped to them, recompute the persisted ControlGap rows
(R7.6) and the deterministic control-reduction contribution used to
compute residual risk (see app/api/assessments.py's advance-stage and
/challenge endpoints, which called the old dimension-keyed heuristic this
replaces).
"""

from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.control_engine import gap_detection, scoring
from app.models.control import Control, ControlAssessment, ControlGap
from app.models.risk_factor import RiskFactor


def _assessment_to_dict(
    assessment: ControlAssessment | None,
    operating_status: str | None = None,
) -> dict | None:
    """The control's current assessment as plain data, plus the control's
    own operating status (R7.3), which gap detection and scoring use."""

    if assessment is None:
        return None

    return {
        "design_adequacy": assessment.design_adequacy,
        "operating_effectiveness": assessment.operating_effectiveness,
        "has_evidence": assessment.has_evidence,
        "coverage_complete": assessment.coverage_complete,
        "depends_on_unavailable_data": assessment.depends_on_unavailable_data,
        "operating_status": operating_status or "ACTIVE",
    }


def _current_assessment_for_control(db: Session, control_id: int) -> ControlAssessment | None:
    return (
        db.query(ControlAssessment)
        .filter(
            ControlAssessment.control_id == control_id,
            ControlAssessment.is_current.is_(True),
        )
        .first()
    )


def recompute_control_state(db: Session, assessment_id: int) -> dict:
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

    from app.risk_engine.methodology import get_methodology_config

    mitigants = set(get_methodology_config(db)["mitigant_categories"])

    new_candidates: list[gap_detection.GapCandidate] = []
    points_per_risk: list[int] = []
    control_ratings: list[dict] = []

    for risk_factor in risk_factors:
        controls = (
            db.query(Control)
            .filter(
                Control.assessment_id == assessment_id,
                Control.risk_factor_id == risk_factor.id,
                Control.is_current.is_(True),
            )
            .all()
        )

        controls_with_assessment = []
        assessment_dicts = []

        for control in controls:
            current_assessment = _current_assessment_for_control(db, control.id)
            assessment_dict = _assessment_to_dict(current_assessment, control.operating_status)
            assessment_dicts.append(assessment_dict)
            controls_with_assessment.append(
                gap_detection.ControlWithAssessment(
                    control_id=control.id,
                    current_assessment=assessment_dict,
                )
            )

        new_candidates.extend(
            gap_detection.detect_gaps_for_risk(risk_factor.id, controls_with_assessment)
        )
        points_per_risk.append(scoring.points_for_risk(assessment_dicts))

        # Mitigant categories (the control environment) are what controls
        # are measured against, not risks the controls must cover.
        if risk_factor.category not in mitigants:
            control_ratings.append(
                {
                    "risk_factor_id": risk_factor.id,
                    "category": risk_factor.category,
                    "control_count": len(controls),
                    "rating": scoring.rating_for_risk(assessment_dicts),
                }
            )

    control_reduction = scoring.calculate_control_reduction(points_per_risk)

    # Reconcile persisted ControlGap rows against the freshly detected set,
    # so a resolved gap's history is kept (resolved=True) rather than
    # deleted, while a still-open gap isn't duplicated on every recompute.
    existing_open = (
        db.query(ControlGap)
        .filter(
            ControlGap.assessment_id == assessment_id,
            ControlGap.resolved.is_(False),
        )
        .all()
    )

    new_keys = {
        (candidate.gap_type, candidate.risk_factor_id, candidate.control_id)
        for candidate in new_candidates
    }

    existing_keys = set()
    for gap in existing_open:
        key = (gap.gap_type, gap.risk_factor_id, gap.control_id)
        existing_keys.add(key)
        if key not in new_keys:
            gap.resolved = True
            gap.resolved_at = datetime.now(timezone.utc)

    created_gaps = []
    for candidate in new_candidates:
        key = (candidate.gap_type, candidate.risk_factor_id, candidate.control_id)
        if key in existing_keys:
            continue
        gap = ControlGap(
            assessment_id=assessment_id,
            risk_factor_id=candidate.risk_factor_id,
            control_id=candidate.control_id,
            gap_type=candidate.gap_type,
            description=candidate.description,
        )
        db.add(gap)
        created_gaps.append(gap)
        existing_keys.add(key)  # guard against duplicate candidates in this pass

    return {
        "control_reduction": control_reduction,
        "created_gaps": created_gaps,
        "risk_factor_count": len(risk_factors),
        "control_ratings": control_ratings,
        "control_rating": scoring.overall_control_rating(
            [item["rating"] for item in control_ratings]
        ),
    }

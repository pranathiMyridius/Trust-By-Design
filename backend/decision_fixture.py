"""
Test helper: gives an assessment a complete, genuine decision record's
inputs through the real services -- one analyst-rated factor, the inherent
calculation, the frozen residual lookup and the FCRM analyst review (an
approval the methodology requires for low and medium residual risk) -- so
workflow tests that fast-forward an assessment's status can still reach
committee approval.
"""

from datetime import datetime, timezone

from app.models.assessment import Assessment
from app.models.assessment_fcrm_review import AssessmentFcrmReview
from app.models.risk_factor import RiskFactor
from app.risk_engine.scoring import compute_factor_score, determine_risk_band
from app.services.inherent_risk_service import recalculate_inherent_risk
from app.services.residual_risk_service import recalculate_residual_risk


def seed_decision_inputs(db, assessment_id: int, likelihood: int = 3, impact: int = 3) -> None:
    """Does not commit."""

    assessment = db.get(Assessment, assessment_id)
    score = compute_factor_score(likelihood, impact)
    factor = RiskFactor(
        assessment_id=assessment_id,
        category="PRODUCT_SERVICE_RISK",
        applicable=True,
        score=score,
        severity=determine_risk_band(score),
        likelihood=likelihood,
        impact=impact,
        rated_by="Fixture Analyst",
        rated_at=datetime.now(timezone.utc),
        rating_source="ANALYST_RATED",
        rationale="Fixture factor for workflow tests.",
        source="MANUAL",
        version=1,
        is_current=True,
    )
    factor.set_indicators([])
    db.add(factor)
    db.flush()

    calculation = recalculate_inherent_risk(db, assessment, calculated_by="Fixture")
    assessment.inherent_score = calculation.final_score
    assessment.inherent_risk_level = calculation.risk_band
    assessment.overall_score = calculation.final_score
    assessment.risk_level = calculation.risk_band
    recalculate_residual_risk(db, assessment, frozen=True, calculated_by="Fixture")
    db.add(
        AssessmentFcrmReview(
            assessment_id=assessment_id,
            justification="Fixture FCRM review.",
            human_ratings="{}",
            reviewed_by="Fixture Analyst",
        )
    )

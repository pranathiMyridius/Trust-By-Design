"""
The single place that recomputes an assessment's numeric
overall_score/risk_level (from the Stage 6 calculation -- see
app/services/inherent_risk_service.py) from its current risk factors, and rebuilds
the RiskResult rows that mirror them for existing dimension-keyed UI
(Manual Scoring Calculator, Controls/Residual stages).

The risk-category set is now fully dynamic and AI-determined (any of
the 10 canonical categories the AI, or an analyst manually, marks
applicable) rather than a fixed 6-dimension list, so this recomputes
from whatever RiskFactor rows are actually current -- called both
right after a fresh AI analysis (app/langgraph/nodes.py::persist_results)
and whenever a factor is added or excluded afterward (see the
/risk-factors endpoints in app/api/assessments.py), so the score never
goes stale relative to the factors an analyst is looking at.
"""

from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.models.assessment import Assessment
from app.models.risk_factor import RiskFactor
from app.models.risk_result import RiskResult
from app.services.inherent_risk_service import compute_inherent_risk


def recalculate_assessment_score(db: Session, assessment: Assessment) -> None:
    """
    Does not commit -- the caller commits (it may be part of a larger
    transaction, e.g. alongside inserting a new RiskFactor row).
    """

    current_factors = (
        db.query(RiskFactor)
        .filter(
            RiskFactor.assessment_id == assessment.id,
            RiskFactor.is_current.is_(True),
        )
        .all()
    )

    # The official score is the deterministic Stage 6 calculation --
    # analyst likelihood x impact ratings, methodology weights, approved
    # policy rules -- and nothing else. None while applicable factors
    # exist but none is rated yet; never a placeholder 0/LOW.
    result, _config = compute_inherent_risk(db, assessment)

    assessment.overall_score = result["final_score"]
    assessment.risk_level = result["risk_band"]

    # Same supersede-don't-delete versioning as everywhere else in this
    # pipeline, so any human rating/audit trail keyed off a RiskResult
    # id stays valid.
    previous_results = (
        db.query(RiskResult)
        .filter(
            RiskResult.assessment_id == assessment.id,
            RiskResult.is_current.is_(True),
        )
        .all()
    )

    now = datetime.now(timezone.utc)
    next_version = 1

    for previous in previous_results:
        previous.is_current = False
        previous.superseded_at = now
        next_version = max(next_version, previous.version + 1)

    for factor in current_factors:
        if not factor.applicable or factor.excluded:
            continue

        db.add(
            RiskResult(
                assessment_id=assessment.id,
                dimension=factor.category,
                score=factor.score,
                severity=factor.severity,
                reason=factor.rationale,
                version=next_version,
                is_current=True,
            )
        )

"""
P3 (R-GOV-03): who is "involved" in an assessment, and so is not an
independent reviewer of it.

Involved: the owner; anyone who rated a current factor; anyone who
proposed or made an override on it; whoever saved its FCRM review; and
whoever assessed one of its controls. Matched by user id where the record
holds one, and by recorded name for rows made before ids were stored.
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.models.assessment import Assessment
from app.models.user import User


def involved(db: Session, assessment: Assessment) -> tuple[set[int], set[str]]:
    """(user ids, display names) of the people who prepared the assessment."""

    from app.models.assessment_fcrm_review import AssessmentFcrmReview
    from app.models.assessment_override import AssessmentOverride
    from app.models.audit_event import AuditEvent
    from app.models.control import ControlAssessment
    from app.models.risk_factor import RiskFactor

    ids: set[int] = set()
    names: set[str] = set()
    if assessment.owner_id:
        ids.add(assessment.owner_id)

    for rated_by, rated_by_id in db.query(RiskFactor.rated_by, RiskFactor.rated_by_id).filter(
        RiskFactor.assessment_id == assessment.id, RiskFactor.is_current.is_(True)
    ):
        if rated_by_id:
            ids.add(rated_by_id)
        elif rated_by:
            names.add(rated_by)

    for (overrider,) in db.query(AssessmentOverride.overridden_by_id).filter(
        AssessmentOverride.assessment_id == assessment.id
    ):
        if overrider:
            ids.add(overrider)

    review = db.query(AssessmentFcrmReview).filter(AssessmentFcrmReview.assessment_id == assessment.id).first()
    if review is not None and review.reviewed_by:
        names.add(review.reviewed_by)
    # The FCRM review is audited as APPROVAL with the reviewer's id.
    for (actor_id,) in db.query(AuditEvent.actor_id).filter(
        AuditEvent.assessment_id == assessment.id, AuditEvent.action == "APPROVAL"
    ):
        if actor_id:
            ids.add(actor_id)

    for (assessed_by,) in db.query(ControlAssessment.assessed_by).filter(
        ControlAssessment.assessment_id == assessment.id
    ):
        if assessed_by:
            names.add(assessed_by)

    return ids, names


def is_involved(db: Session, assessment: Assessment, user: User) -> bool:
    ids, names = involved(db, assessment)
    return user.id in ids or (user.full_name or user.email) in names or user.email in names

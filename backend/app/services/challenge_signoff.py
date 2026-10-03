"""
R11 mandatory challenge review, in two separated steps (P3, R-GOV-03):

  1. REVIEW   -- the independent challenge review, by a Challenge
                 Reviewer, Senior Analyst or QA Reviewer who did not
                 prepare the assessment;
  2. SIGNOFF  -- by an FCRM Manager (the assigned one, or their delegate)
                 or the Head of FCRM, who is not the reviewer.

Both are required for every assessment before the committee, including
when no trigger fired (the reviewer then attests to that). CRITICAL cases
are flagged to the Committee on sign-off.

Deterministic rules, no AI involvement:
  * the outcome is derived from the trigger evaluation, not chosen;
  * sign-off is refused while a high-severity finding is still open;
  * a review or sign-off stops counting when a finding appears after it,
    and both are superseded when the manager returns or rejects the
    assessment.

Roles are PROVISIONAL configuration (app/governance/policy.py).
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.models.assessment import Assessment
from app.models.challenge_review import ChallengeFinding, ChallengeReviewSignoff
from app.models.user import User, UserRole

STAGE_REVIEW = "REVIEW"
STAGE_SIGNOFF = "SIGNOFF"

# The stages at which the review can be completed: analyst review, the
# manager's challenge stage, and -- for assessments already waiting for
# the committee when the sign-off became mandatory, or deferred by it --
# before committee review (re)opens. Never once the committee is sitting.
SIGNOFF_STATUSES = {"HUMAN_REVIEW", "SUBMITTED_TO_MANAGER", "READY_FOR_COMMITTEE", "DEFERRED"}

OUTCOME_NO_TRIGGERS = "NO_TRIGGERS_FIRED"
OUTCOME_FINDINGS_ADDRESSED = "FINDINGS_ADDRESSED"


def _current(db: Session, assessment_id: int, stage: str) -> ChallengeReviewSignoff | None:
    return (
        db.query(ChallengeReviewSignoff)
        .filter(
            ChallengeReviewSignoff.assessment_id == assessment_id,
            ChallengeReviewSignoff.stage == stage,
            ChallengeReviewSignoff.is_current.is_(True),
        )
        .first()
    )


def current_review(db: Session, assessment_id: int) -> ChallengeReviewSignoff | None:
    return _current(db, assessment_id, STAGE_REVIEW)


def current_signoff(db: Session, assessment_id: int) -> ChallengeReviewSignoff | None:
    return _current(db, assessment_id, STAGE_SIGNOFF)


def signoff_history(db: Session, assessment_id: int) -> list[ChallengeReviewSignoff]:
    return (
        db.query(ChallengeReviewSignoff)
        .filter(ChallengeReviewSignoff.assessment_id == assessment_id)
        .order_by(ChallengeReviewSignoff.completed_at.asc(), ChallengeReviewSignoff.id.asc())
        .all()
    )


def signoff_payload(row: ChallengeReviewSignoff) -> dict[str, Any]:
    return {
        "id": row.id,
        "assessment_id": row.assessment_id,
        "stage": row.stage,
        "version": row.version,
        "is_current": row.is_current,
        "outcome": row.outcome,
        "reason": row.reason,
        "committee_escalation": bool(row.committee_escalation),
        "triggers": json.loads(row.trigger_snapshot or "{}"),
        "findings": json.loads(row.findings_snapshot or "[]"),
        "reviewer": row.reviewer,
        "reviewer_id": row.reviewer_id,
        "reviewer_role": row.reviewer_role,
        "completed_at": row.completed_at.isoformat() if row.completed_at else None,
        "superseded_at": row.superseded_at.isoformat() if row.superseded_at else None,
        "superseded_reason": row.superseded_reason,
    }


def _naive(value: datetime) -> datetime:
    return value.replace(tzinfo=None) if value.tzinfo else value


def _newer_findings(db: Session, assessment_id: int, since: datetime) -> int:
    completed = _naive(since)
    return sum(
        1
        for finding in db.query(ChallengeFinding).filter(ChallengeFinding.assessment_id == assessment_id).all()
        if finding.detected_at is not None and _naive(finding.detected_at) > completed
    )


def signoff_problem(db: Session, assessment: Assessment) -> str | None:
    """Why the assessment does not have a valid, completed challenge
    review (review + sign-off), or None."""

    review = current_review(db, assessment.id)
    signoff = current_signoff(db, assessment.id)
    if review is None:
        return (
            "The independent challenge review has not been completed (required even when "
            "no challenge trigger fired)."
        )
    newer = _newer_findings(db, assessment.id, review.completed_at)
    if newer:
        return (
            f"{newer} challenge finding(s) were raised after the challenge review; the review "
            "must be completed again."
        )
    if signoff is None:
        return "The challenge review has not been signed off by an FCRM Manager or the Head of FCRM."
    if _naive(signoff.completed_at) < _naive(review.completed_at):
        return "The challenge review was redone after the sign-off; it must be signed off again."
    return None


def _evaluate(db: Session, assessment: Assessment) -> dict:
    from app.challenge_engine.engine import recompute_challenge_review

    evaluation = recompute_challenge_review(db, assessment.id)
    db.flush()
    return evaluation


def _snapshots(evaluation: dict) -> tuple[str, str, str]:
    findings = evaluation["findings"]
    outcome = (
        OUTCOME_NO_TRIGGERS
        if not evaluation.get("triggered") and not findings
        else OUTCOME_FINDINGS_ADDRESSED
    )
    triggers = json.dumps(
        {
            "triggered": bool(evaluation.get("triggered")),
            "triggers": [{"name": t.get("name"), "fired": bool(t.get("fired"))} for t in evaluation.get("triggers", [])],
        }
    )
    # Ids and states only -- never the findings' evidence text.
    snapshot = json.dumps(
        [
            {"id": f.id, "category": f.category, "severity": f.severity, "resolution_status": f.resolution_status}
            for f in findings
        ]
    )
    return outcome, triggers, snapshot


def _append(db: Session, assessment: Assessment, stage: str, user: User, role: str, reason: str,
            evaluation: dict, escalation: bool = False) -> ChallengeReviewSignoff:
    outcome, triggers, snapshot = _snapshots(evaluation)
    now = datetime.now(timezone.utc)
    previous = _current(db, assessment.id, stage)
    version = 1
    if previous is not None:
        version = previous.version + 1
        previous.is_current = False
        previous.superseded_at = now
        previous.superseded_reason = f"Replaced by {stage.lower()} v{version}."
        db.flush()
    row = ChallengeReviewSignoff(
        assessment_id=assessment.id,
        stage=stage,
        outcome=outcome,
        reason=reason,
        trigger_snapshot=triggers,
        findings_snapshot=snapshot,
        reviewer=user.full_name or user.email,
        reviewer_id=user.id,
        reviewer_role=role,
        committee_escalation=escalation,
        completed_at=now,
        version=version,
        is_current=True,
    )
    db.add(row)
    db.flush()
    return row


def _common_checks(assessment: Assessment, user: User) -> None:
    if user.id == assessment.owner_id:
        raise HTTPException(status_code=403, detail="You own this assessment and cannot take part in its challenge review.")
    if assessment.status not in SIGNOFF_STATUSES:
        raise HTTPException(
            status_code=409,
            detail=(
                "The challenge review can be completed during analyst review, the manager's "
                "challenge stage, or before committee review opens -- not while the assessment "
                f"is {assessment.status}."
            ),
        )


def review_problem(db: Session, assessment: Assessment, user: User) -> str | None:
    from app.governance.independence import is_involved
    from app.governance.policy import describe, holds, policy

    rule = policy()["challenge_reviewers"]
    if not holds(user, rule):
        return f"The challenge review is performed by: {describe(rule)}."
    if is_involved(db, assessment, user):
        return "You prepared or changed this assessment, so you are not independent of it."
    return None


def record_review(db: Session, assessment: Assessment, user: User, summary: str) -> ChallengeReviewSignoff:
    """The independent challenge review. Does not commit."""

    _common_checks(assessment, user)
    problem = review_problem(db, assessment, user)
    if problem:
        raise HTTPException(status_code=403, detail=problem)
    evaluation = _evaluate(db, assessment)
    return _append(db, assessment, STAGE_REVIEW, user, user.role, summary, evaluation)


def signoff_eligibility(db: Session, assessment: Assessment, user: User) -> str | None:
    from app.governance.policy import HEAD_OF_FCRM, describe, holds, policy

    rule = policy()["challenge_signoff"]
    if not holds(user, rule):
        return f"The challenge review is signed off by: {describe(rule)}."
    review = current_review(db, assessment.id)
    if review is not None and review.reviewer_id == user.id:
        return "You performed the challenge review; a different person must sign it off."
    # An FCRM Manager signs off their own assessments (assigned, or by
    # delegation); the Head of FCRM may sign off any.
    if user.role == UserRole.MANAGER.value and not holds(user, {"designations": [HEAD_OF_FCRM]}):
        from app.services import delegation as delegation_rules

        try:
            delegation_rules.resolve_manager_authority(db, user, assessment)
        except HTTPException as exc:
            return str(exc.detail)
    return None


def sign_off(db: Session, assessment: Assessment, user: User, reason: str) -> ChallengeReviewSignoff:
    """The sign-off that follows the independent review. Does not commit."""

    from app.challenge_engine.engine import has_blocking_findings
    from app.challenge_engine.engine import _challenge_risk_level
    from app.governance.policy import policy

    _common_checks(assessment, user)
    problem = signoff_eligibility(db, assessment, user)
    if problem:
        raise HTTPException(status_code=403, detail=problem)

    evaluation = _evaluate(db, assessment)
    if has_blocking_findings(db, assessment.id):
        raise HTTPException(
            status_code=409,
            detail=(
                "High/critical challenge findings are not resolved. They can't be accepted: "
                "resolve them before signing off the challenge review."
            ),
        )
    review = current_review(db, assessment.id)
    if review is None or _newer_findings(db, assessment.id, review.completed_at):
        raise HTTPException(
            status_code=409,
            detail="The independent challenge review must be completed (and be current) before it is signed off.",
        )
    escalation = (_challenge_risk_level(db, assessment) or "") in policy()["challenge_escalation_bands"]
    return _append(db, assessment, STAGE_SIGNOFF, user, user.role, reason, evaluation, escalation)


def supersede_current(db: Session, assessment_id: int, reason: str) -> list[ChallengeReviewSignoff]:
    """Retires the current review and sign-off (e.g. the assessment was
    returned). Does not commit."""

    retired = []
    for stage in (STAGE_REVIEW, STAGE_SIGNOFF):
        row = _current(db, assessment_id, stage)
        if row is None:
            continue
        row.is_current = False
        row.superseded_at = datetime.now(timezone.utc)
        row.superseded_reason = reason
        retired.append(row)
    return retired

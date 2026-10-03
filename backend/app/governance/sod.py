"""
P3 (R15.2, R-GOV-01/02): the Segregation-of-Duties exception lifecycle,
and the checks that consult it.

Every rule is enforced here; the API layer only translates. Functions
don't commit -- the caller commits once, so a multi-step change is one
transaction. Approval uses optimistic locking (SodException.row_version):
a second, concurrent decision fails rather than overwriting the first.

Policy is PROVISIONAL (app/governance/policy.py).
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.governance.policy import POLICY_STATUS, describe, holds, policy
from app.models.assessment import Assessment
from app.models.sod_exception import (
    EXCEPTION_TYPES,
    RISK_LEVELS,
    STATUS_APPROVED,
    STATUS_DRAFT,
    STATUS_EXPIRED,
    STATUS_PENDING,
    STATUS_REJECTED,
    STATUS_REVOKED,
    TIER_COMMITTEE,
    TIER_STANDARD,
    TYPE_ADMIN_COMMITTEE_DUAL_ROLE,
    TYPE_COMMITTEE_SEPARATION,
    SodException,
    SodExceptionEvent,
)
from app.models.user import User, UserRole


def utcnow() -> datetime:
    """Naive UTC, matching how the DateTime columns are stored."""

    return datetime.now(timezone.utc).replace(tzinfo=None)


def naive_utc(value: datetime) -> datetime:
    if value.tzinfo is not None:
        value = value.astimezone(timezone.utc).replace(tzinfo=None)
    return value


def _name(user: User | None) -> str:
    return (user.full_name or user.email) if user else "System"


def _audit_action(name: str):
    from app.services.audit_service import AuditAction

    return getattr(AuditAction, name)


def record(db: Session, exception: SodException, action: str, actor: User | None, detail: str | None = None,
           from_status: str | None = None, to_status: str | None = None) -> None:
    """Appends the history event and the audit event. Does not commit."""

    from app.services.audit_service import log_audit_event

    db.add(
        SodExceptionEvent(
            exception_id=exception.id,
            action=action,
            from_status=from_status,
            to_status=to_status,
            actor=_name(actor),
            actor_id=actor.id if actor else None,
            detail=detail,
        )
    )
    log_audit_event(
        db,
        assessment_id=exception.assessment_id,
        action=_audit_action(f"SOD_EXCEPTION_{action}"),
        previous_status=from_status,
        new_status=to_status,
        actor=_name(actor),
        actor_id=actor.id if actor else None,
        details=f"SoD exception {exception.reference}: {detail or action.lower()}",
    )


# -- conflicts --------------------------------------------------------------


def conflicting_roles(db: Session, exception_type: str, affected: User, assessment: Assessment | None) -> list[str]:
    """The conflict the exception would set aside. 422 when there is none:
    an exception is only ever for a real, identified conflict."""

    if exception_type == TYPE_ADMIN_COMMITTEE_DUAL_ROLE:
        if affected.role != UserRole.ADMIN.value:
            raise HTTPException(status_code=422, detail="A dual-role exception is only for a user whose role is Admin.")
        return ["ADMIN (system administration)", "COMMITTEE_MEMBER (committee voting and decisions)"]

    if exception_type == TYPE_COMMITTEE_SEPARATION:
        if assessment is None:
            raise HTTPException(status_code=422, detail="A committee-separation exception must name the assessment.")
        if affected.role != UserRole.COMMITTEE_MEMBER.value:
            raise HTTPException(
                status_code=422,
                detail="A committee-separation exception is only for a Committee Member who is conflicted on the assessment.",
            )
        conflicts = []
        if affected.id == assessment.owner_id:
            conflicts.append("SUBMITTER (owner of the assessment)")
        if affected.id in {assessment.manager_id, assessment.manager_decided_by_id}:
            conflicts.append("REVIEWING_MANAGER (assigned or deciding manager)")
        if not conflicts:
            raise HTTPException(
                status_code=422,
                detail="This person has no separation-of-duties conflict on this assessment; no exception is needed.",
            )
        return conflicts + ["COMMITTEE_MEMBER (committee voting and decisions)"]

    raise HTTPException(status_code=422, detail=f"exception_type must be one of: {', '.join(sorted(EXCEPTION_TYPES))}")


# -- tiering ------------------------------------------------------------------


def _other_exceptions(db: Session, exception: SodException, window_days: int) -> int:
    since = utcnow() - timedelta(days=window_days)
    return (
        db.query(SodException.id)
        .filter(
            SodException.affected_user_id == exception.affected_user_id,
            SodException.id != exception.id,
            SodException.status.notin_([STATUS_DRAFT, STATUS_REJECTED]),
            SodException.created_at >= since,
        )
        .count()
    )


def compute_tier(db: Session, exception: SodException) -> tuple[str, list[str], bool]:
    rules = policy()["exception_committee_tier"]
    reasons: list[str] = []
    if exception.risk_level in rules["risk_levels"]:
        reasons.append(f"risk level {exception.risk_level}")
    days = (exception.end_at - exception.start_at).total_seconds() / 86400
    if days > rules["max_standard_days"]:
        reasons.append(f"duration {days:.0f} days exceeds {rules['max_standard_days']}")
    if rules["enterprise_wide"] and exception.assessment_id is None:
        reasons.append("not limited to one assessment (enterprise-wide)")
    repeated = _other_exceptions(db, exception, rules["repeat_window_days"]) >= rules["repeat_threshold"]
    if repeated:
        reasons.append(
            f"repeated: {rules['repeat_threshold']}+ other exceptions for this person in {rules['repeat_window_days']} days"
        )
    if exception.assessment_id is not None:
        assessment = db.get(Assessment, exception.assessment_id)
        band = (assessment.residual_risk_level or assessment.inherent_risk_level or "") if assessment else ""
        if band in rules["assessment_bands"]:
            reasons.append(f"the assessment is {band} risk")
    return (TIER_COMMITTEE if reasons else TIER_STANDARD), reasons, repeated


# -- eligibility --------------------------------------------------------------


def approver_problem(db: Session, exception: SodException, user: User) -> str | None:
    """Why `user` may not approve or reject `exception`, or None."""

    if exception.status != STATUS_PENDING:
        return f"The request is {exception.status.replace('_', ' ').lower()}, not pending approval."
    if user.id == exception.requestor_id:
        return "You requested this exception and cannot decide it."
    if user.id == exception.affected_user_id:
        return "The exception is for you; you cannot decide it."
    affected = db.get(User, exception.affected_user_id)
    if affected is not None and affected.manager_id == user.id:
        return "You are the affected person's direct manager and cannot decide it."
    if exception.assessment_id is not None:
        assessment = db.get(Assessment, exception.assessment_id)
        if assessment is not None and user.id in {assessment.owner_id, assessment.manager_id, assessment.manager_decided_by_id}:
            return "You are a party to the assessment this exception concerns."
    rule = policy()["exception_approvers"][exception.tier or TIER_STANDARD]
    if not holds(user, rule):
        return f"A {exception.tier.lower()}-tier exception needs approval by: {describe(rule)}."
    if exception.assigned_approver_id and exception.assigned_approver_id != user.id:
        return "This request is assigned to a different approver."
    return None


def revoker_problem(exception: SodException, user: User) -> str | None:
    if exception.status != STATUS_APPROVED:
        return "Only an approved exception can be revoked."
    if user.id == exception.affected_user_id:
        return "You cannot revoke an exception that is for you; ask an administrator or approver."
    rules = policy()
    if holds(user, rules["exception_revokers"]) or holds(user, rules["exception_approvers"][exception.tier or TIER_STANDARD]):
        return None
    return f"Revoking needs: {describe(rules['exception_revokers'])} or an approver of this tier."


def reviewer_problem(exception: SodException, user: User) -> str | None:
    if exception.status != STATUS_APPROVED:
        return "Only an active (approved) exception is reviewed periodically."
    if user.id in {exception.affected_user_id, exception.requestor_id}:
        return "The requestor or the affected person cannot review it."
    if exception.assigned_reviewer_id == user.id:
        return None
    if holds(user, policy()["exception_approvers"][exception.tier or TIER_STANDARD]):
        return None
    return "Only the assigned reviewer or an approver of this tier can review it."


# -- lifecycle ----------------------------------------------------------------


def _validate_window(start_at: datetime, end_at: datetime) -> tuple[datetime, datetime]:
    start_at, end_at = naive_utc(start_at), naive_utc(end_at)
    if end_at <= start_at:
        raise HTTPException(status_code=422, detail="The end date must be after the start date.")
    if end_at <= utcnow():
        raise HTTPException(status_code=422, detail="The end date must be in the future.")
    max_days = policy()["exception_max_days"]
    if (end_at - start_at) > timedelta(days=max_days):
        raise HTTPException(status_code=422, detail=f"An exception can last at most {max_days} days.")
    return start_at, end_at


def create(db: Session, requestor: User, data: dict) -> SodException:
    affected = db.get(User, data["affected_user_id"])
    if affected is None or not affected.is_active:
        raise HTTPException(status_code=422, detail="affected_user_id is not an active user.")
    assessment = db.get(Assessment, data["assessment_id"]) if data.get("assessment_id") else None
    if data.get("assessment_id") and assessment is None:
        raise HTTPException(status_code=422, detail="assessment_id does not exist.")
    if data["risk_level"] not in RISK_LEVELS:
        raise HTTPException(status_code=422, detail=f"risk_level must be one of: {', '.join(RISK_LEVELS)}")
    roles = conflicting_roles(db, data["exception_type"], affected, assessment)
    start_at, end_at = _validate_window(data["start_at"], data["end_at"])

    exception = SodException(
        exception_type=data["exception_type"],
        status=STATUS_DRAFT,
        requestor_id=requestor.id,
        affected_user_id=affected.id,
        conflicting_roles=json.dumps(roles),
        assessment_id=assessment.id if assessment else None,
        business_justification=data["business_justification"],
        standard_workflow_reason=data["standard_workflow_reason"],
        risk_level=data["risk_level"],
        compensating_controls=data["compensating_controls"],
        start_at=start_at,
        end_at=end_at,
        assigned_approver_id=data.get("assigned_approver_id"),
        assigned_reviewer_id=data.get("assigned_reviewer_id"),
    )
    db.add(exception)
    db.flush()
    exception.reference = f"SOD-{utcnow().year}-{exception.id:04d}"
    record(db, exception, "CREATED", requestor, f"{exception.exception_type} for {_name(affected)}", None, STATUS_DRAFT)
    return exception


def submit(db: Session, exception: SodException, user: User) -> SodException:
    if user.id != exception.requestor_id:
        raise HTTPException(status_code=403, detail="Only the requestor can submit this request.")
    if exception.status != STATUS_DRAFT:
        raise HTTPException(status_code=409, detail=f"The request is already {exception.status}.")
    # Re-validate: time has passed since the draft was saved.
    _validate_window(exception.start_at, exception.end_at)
    affected = db.get(User, exception.affected_user_id)
    assessment = db.get(Assessment, exception.assessment_id) if exception.assessment_id else None
    exception.conflicting_roles = json.dumps(conflicting_roles(db, exception.exception_type, affected, assessment))

    tier, reasons, repeated = compute_tier(db, exception)
    exception.tier = tier
    exception.tier_reasons = json.dumps(reasons)
    exception.repeated = repeated
    if exception.assigned_approver_id:
        assignee = db.get(User, exception.assigned_approver_id)
        exception.status = STATUS_PENDING  # so approver_problem evaluates the pending request
        problem = approver_problem(db, exception, assignee) if assignee else "the assigned approver does not exist"
        if problem:
            raise HTTPException(status_code=422, detail=f"The assigned approver is not eligible: {problem}")
    exception.status = STATUS_PENDING
    exception.submitted_at = utcnow()
    record(
        db, exception, "SUBMITTED", user,
        f"submitted for {tier.lower()}-tier approval" + (f" ({'; '.join(reasons)})" if reasons else ""),
        STATUS_DRAFT, STATUS_PENDING,
    )
    return exception


def decide(db: Session, exception: SodException, user: User, decision: str, rationale: str) -> SodException:
    problem = approver_problem(db, exception, user)
    if problem:
        raise HTTPException(status_code=409 if "not pending" in problem else 403, detail=problem)
    if decision == "APPROVE":
        # An approval that would already have run out authorizes nothing.
        if exception.end_at <= utcnow():
            raise HTTPException(status_code=409, detail="The requested period has already ended; it cannot be approved.")
        new_status = STATUS_APPROVED
    else:
        new_status = STATUS_REJECTED
    previous = exception.status
    exception.status = new_status
    exception.decision = new_status
    exception.decision_rationale = rationale
    exception.decided_by_id = user.id
    exception.decided_at = utcnow()
    record(db, exception, new_status, user, rationale, previous, new_status)
    return exception


def declare(db: Session, exception: SodException, user: User, statement: str) -> SodException:
    if user.id != exception.affected_user_id:
        raise HTTPException(status_code=403, detail="Only the person the exception is for can make its declaration.")
    if exception.status not in {STATUS_PENDING, STATUS_APPROVED}:
        raise HTTPException(status_code=409, detail="A declaration is made on a pending or approved exception.")
    if exception.declared_at is not None:
        raise HTTPException(status_code=409, detail="The declaration has already been made.")
    exception.declaration = statement
    exception.declared_at = utcnow()
    record(db, exception, "DECLARED", user, statement)
    return exception


def revoke(db: Session, exception: SodException, user: User, reason: str) -> SodException:
    expire_if_due(db, exception)
    problem = revoker_problem(exception, user)
    if problem:
        raise HTTPException(status_code=409 if "Only an approved" in problem else 403, detail=problem)
    exception.status = STATUS_REVOKED
    exception.revoked_by_id = user.id
    exception.revoked_at = utcnow()
    exception.revocation_reason = reason
    record(db, exception, "REVOKED", user, reason, STATUS_APPROVED, STATUS_REVOKED)
    return exception


def periodic_review(db: Session, exception: SodException, user: User, note: str) -> SodException:
    expire_if_due(db, exception)
    problem = reviewer_problem(exception, user)
    if problem:
        raise HTTPException(status_code=403 if "Only an active" not in problem else 409, detail=problem)
    exception.last_reviewed_at = utcnow()
    exception.last_reviewed_by_id = user.id
    exception.last_review_note = note
    record(db, exception, "REVIEWED", user, note)
    return exception


def expire_if_due(db: Session, exception: SodException) -> bool:
    if exception.status == STATUS_APPROVED and exception.end_at <= utcnow():
        exception.status = STATUS_EXPIRED
        exception.expired_at = utcnow()
        record(db, exception, "EXPIRED", None, f"end date {exception.end_at.isoformat()} reached", STATUS_APPROVED, STATUS_EXPIRED)
        return True
    return False


def expire_due(db: Session) -> int:
    """Scheduled sweep (expiry is also checked at every point of use)."""

    due = (
        db.query(SodException)
        .filter(SodException.status == STATUS_APPROVED, SodException.end_at <= utcnow())
        .all()
    )
    for exception in due:
        expire_if_due(db, exception)
    return len(due)


# -- use at the point of authorization ----------------------------------------


def active_exception(
    db: Session,
    user: User,
    exception_type: str,
    assessment: Assessment | None,
) -> SodException | None:
    """The approved, in-period, declared exception that lets `user` do
    `exception_type` on `assessment` -- checked now, not by a job."""

    now = utcnow()
    candidates = (
        db.query(SodException)
        .filter(
            SodException.affected_user_id == user.id,
            SodException.exception_type == exception_type,
            SodException.status == STATUS_APPROVED,
        )
        .all()
    )
    for exception in candidates:
        if expire_if_due(db, exception):
            continue
        if exception.start_at > now or exception.declared_at is None:
            continue
        if exception.assessment_id is None or (assessment is not None and exception.assessment_id == assessment.id):
            return exception
    return None


def record_use(db: Session, exception: SodException, user: User, what: str) -> None:
    record(db, exception, "USED", user, what)


def active_dual_role_exceptions(db: Session, user: User) -> list[SodException]:
    """Every approved, in-period dual-role exception the user holds (used
    by the same-case administrative-action restriction)."""

    if user.role != UserRole.ADMIN.value:
        return []
    now = utcnow()
    active = []
    for exception in (
        db.query(SodException)
        .filter(
            SodException.affected_user_id == user.id,
            SodException.exception_type == TYPE_ADMIN_COMMITTEE_DUAL_ROLE,
            SodException.status == STATUS_APPROVED,
        )
        .all()
    ):
        if exception.end_at > now and exception.start_at <= now:
            active.append(exception)
    return active


# -- presentation -------------------------------------------------------------


def flags(exception: SodException) -> list[str]:
    out = []
    if exception.repeated:
        out.append("REPEATED")
    if exception.status == STATUS_APPROVED:
        warn = timedelta(days=policy()["exception_expiry_warning_days"])
        if exception.end_at - utcnow() <= warn:
            out.append("EXPIRING_SOON")
        if exception.declared_at is None:
            out.append("DECLARATION_MISSING")
    return out


def actions_for(db: Session, exception: SodException, user: User) -> dict:
    """What `user` may do next, and why not -- computed here so the UI
    never re-implements the rules."""

    def entry(problem: str | None) -> dict:
        return {"allowed": problem is None, "reason": problem}

    submit_problem = None
    if exception.status != STATUS_DRAFT:
        submit_problem = "Only a draft can be submitted."
    elif user.id != exception.requestor_id:
        submit_problem = "Only the requestor can submit this request."

    declare_problem = None
    if user.id != exception.affected_user_id:
        declare_problem = "Only the person the exception is for makes the declaration."
    elif exception.declared_at is not None:
        declare_problem = "Already declared."
    elif exception.status not in {STATUS_PENDING, STATUS_APPROVED}:
        declare_problem = "Not pending or approved."

    return {
        "submit": entry(submit_problem),
        "decide": entry(approver_problem(db, exception, user)),
        "declare": entry(declare_problem),
        "revoke": entry(revoker_problem(exception, user)),
        "review": entry(reviewer_problem(exception, user)),
    }


__all__ = ["POLICY_STATUS"]

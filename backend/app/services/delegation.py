"""
AW.7: approval delegation.

Everything that decides whether a delegation lets someone approve lives
here, so the four acceptance criteria are enforced in one place:

  1. A delegate can approve only during the active delegation period.
  2. The approval record identifies both the original approver and the
     delegate.
  3. Delegates cannot approve outside the delegated scope.
  4. Expired delegations automatically stop granting approval access.

(1) and (4) hold by construction: whether a delegation is active is
computed at the moment of approval (is_active), never stored, so there
is no flag for a background job to forget to clear. (3) is
authorizing_delegation's scope match. (2) is up to each approval
endpoint, which records the delegate as the actor and the delegator as
the person acted for (see app/api/approvals.py).

Two authorities can be delegated:

  MANAGER_APPROVAL    the assigned Manager's decision on an assessment
                      submitted to them (approve / reject / return).
  COMMITTEE_SIGN_OFF  a Committee Member's vote and binding decision.

Rules on who may hold a delegation:

  * Only authority held natively can be delegated, and no delegation can
    be re-delegated. A delegator must hold the authority's role
    themselves; a delegate acting under one cannot pass it on.
  * The delegate must be an active Manager. Committee Members already
    hold committee authority and Admins hold everything, so delegating
    to them adds nothing. FCRM Analysts are excluded because they
    prepare the assessments the committee signs off, which is one of
    the prohibited role combinations under AW.2. Business Users hold no
    approval standing.
  * Separation of duties (AW.2) still applies per assessment. A delegate
    cannot approve their own submission, or sign off an assessment they
    approved as its manager; the endpoints check that.
  * A delegation is temporary: at most APPROVAL_DELEGATION_MAX_DAYS
    (default 90) long.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.models.approval_delegation import ApprovalDelegation
from app.models.assessment import Assessment
from app.models.user import User, UserRole

AUTHORITY_MANAGER_APPROVAL = "MANAGER_APPROVAL"
AUTHORITY_COMMITTEE_SIGN_OFF = "COMMITTEE_SIGN_OFF"
AUTHORITIES = (AUTHORITY_MANAGER_APPROVAL, AUTHORITY_COMMITTEE_SIGN_OFF)

AUTHORITY_LABELS = {
    AUTHORITY_MANAGER_APPROVAL: "manager approval",
    AUTHORITY_COMMITTEE_SIGN_OFF: "committee sign-off",
}

SCOPE_ALL = "ALL"
SCOPE_ASSESSMENT = "ASSESSMENT"
SCOPES = (SCOPE_ALL, SCOPE_ASSESSMENT)

STATE_SCHEDULED = "SCHEDULED"
STATE_ACTIVE = "ACTIVE"
STATE_EXPIRED = "EXPIRED"
STATE_REVOKED = "REVOKED"

# Which native role each authority belongs to.
_AUTHORITY_ROLE = {
    AUTHORITY_MANAGER_APPROVAL: UserRole.MANAGER.value,
    AUTHORITY_COMMITTEE_SIGN_OFF: UserRole.COMMITTEE_MEMBER.value,
}

DELEGATE_ROLES = {UserRole.MANAGER.value}


def max_days() -> int:
    try:
        return max(1, int(os.getenv("APPROVAL_DELEGATION_MAX_DAYS") or 90))
    except ValueError:
        return 90


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def as_utc(value: datetime | None) -> datetime | None:
    # SQLite hands back naive datetimes for values written as UTC.
    if value is None:
        return None
    return value.astimezone(timezone.utc) if value.tzinfo else value.replace(tzinfo=timezone.utc)


def _display(user: User | None) -> str:
    if user is None:
        return "an unknown user"
    return user.full_name or user.email


# --- state -------------------------------------------------------------------


def state_of(delegation: ApprovalDelegation, now: datetime | None = None) -> str:
    now = now or now_utc()
    if delegation.revoked_at is not None:
        return STATE_REVOKED
    if now < as_utc(delegation.start_at):
        return STATE_SCHEDULED
    if now >= as_utc(delegation.end_at):
        return STATE_EXPIRED
    return STATE_ACTIVE


def is_active(delegation: ApprovalDelegation, now: datetime | None = None) -> bool:
    return state_of(delegation, now) == STATE_ACTIVE


# --- validation on create ------------------------------------------------------


def validate_new_delegation(
    db: Session,
    *,
    delegator: User,
    delegate: User | None,
    authority: str,
    scope_type: str,
    scope_assessment_id: int | None,
    start_at: datetime,
    end_at: datetime,
    reason: str,
) -> None:
    """Raises HTTPException(422) with a specific message if the delegation is not allowed."""

    def reject(detail: str) -> None:
        raise HTTPException(status_code=422, detail=detail)

    if authority not in AUTHORITIES:
        reject(f"authority must be one of: {', '.join(AUTHORITIES)}.")
    label = AUTHORITY_LABELS[authority]

    if delegator.role != _AUTHORITY_ROLE[authority] or not delegator.is_active:
        reject(
            f"{_display(delegator)} does not hold {label} authority, so it cannot be delegated. "
            "Only authority you hold yourself can be delegated."
        )

    if delegate is None or not delegate.is_active:
        reject("The delegate must be an active user.")
    if delegate.id == delegator.id:
        reject("You cannot delegate to yourself.")
    if delegate.role not in DELEGATE_ROLES:
        reject(
            "The delegate must be an active Manager. Committee Members and Admins already "
            "hold this authority, and FCRM Analysts and Business Users cannot approve."
        )

    if not (reason or "").strip():
        reject("A reason is required for a delegation.")

    if scope_type not in SCOPES:
        reject(f"scope_type must be one of: {', '.join(SCOPES)}.")
    if scope_type == SCOPE_ASSESSMENT:
        if scope_assessment_id is None:
            reject("scope_assessment_id is required when the scope is a single assessment.")
        assessment = db.query(Assessment).filter(Assessment.id == scope_assessment_id).first()
        if assessment is None:
            reject(f"Assessment {scope_assessment_id} does not exist.")
        if authority == AUTHORITY_MANAGER_APPROVAL and assessment.manager_id not in (None, delegator.id):
            reject(
                f"Assessment {scope_assessment_id} is assigned to a different manager, "
                "so its approval is not yours to delegate."
            )
        if assessment.owner_id == delegate.id:
            reject(f"{_display(delegate)} submitted assessment {scope_assessment_id} and cannot approve it.")
    elif scope_assessment_id is not None:
        reject("scope_assessment_id must be empty when the scope is ALL.")

    start, end = as_utc(start_at), as_utc(end_at)
    if end <= start:
        reject("The end date must be after the start date.")
    if end <= now_utc():
        reject("The end date is in the past, so this delegation would never be active.")
    if end - start > timedelta(days=max_days()):
        reject(f"A delegation is temporary: it can last at most {max_days()} days.")


# --- resolution at approval time --------------------------------------------------


def _in_scope(delegation: ApprovalDelegation, assessment: Assessment) -> bool:
    if delegation.scope_type == SCOPE_ASSESSMENT and delegation.scope_assessment_id != assessment.id:
        return False
    if delegation.authority == AUTHORITY_MANAGER_APPROVAL:
        # Manager approval is personal to the assessment's assigned
        # manager: only their own queue can be delegated.
        return assessment.manager_id == delegation.delegator_id
    return True


def authorizing_delegation(
    db: Session,
    delegate: User,
    assessment: Assessment,
    authority: str,
    now: datetime | None = None,
) -> tuple[ApprovalDelegation, User] | None:
    """
    The active, in-scope delegation (and its delegator) that lets
    `delegate` exercise `authority` on `assessment` right now, or None.

    The delegator must still hold the authority's role today: a
    delegation from someone who has since changed role or been
    deactivated stops granting access.
    """

    now = now or now_utc()
    candidates = (
        db.query(ApprovalDelegation)
        .filter(
            ApprovalDelegation.delegate_id == delegate.id,
            ApprovalDelegation.authority == authority,
            ApprovalDelegation.revoked_at.is_(None),
        )
        .order_by(ApprovalDelegation.id.asc())
        .all()
    )
    for delegation in candidates:
        if not is_active(delegation, now):
            continue
        delegator = db.query(User).filter(User.id == delegation.delegator_id).first()
        if delegator is None or not delegator.is_active or delegator.role != _AUTHORITY_ROLE[authority]:
            continue
        if _in_scope(delegation, assessment):
            return delegation, delegator
    return None


def explain_missing_delegation(
    db: Session,
    delegate: User,
    assessment: Assessment,
    authority: str,
    now: datetime | None = None,
) -> str | None:
    """
    If `delegate` has a delegation for `authority` that would cover
    `assessment` but is not in force right now, a message saying why.
    Lets a 403 say "your delegation expired on ..." instead of just
    "not assigned to you".
    """

    now = now or now_utc()
    label = AUTHORITY_LABELS[authority]
    for delegation in (
        db.query(ApprovalDelegation)
        .filter(ApprovalDelegation.delegate_id == delegate.id, ApprovalDelegation.authority == authority)
        .order_by(ApprovalDelegation.id.desc())
        .all()
    ):
        if not _in_scope(delegation, assessment):
            continue
        delegator = db.query(User).filter(User.id == delegation.delegator_id).first()
        state = state_of(delegation, now)
        who = _display(delegator)
        if state == STATE_SCHEDULED:
            return f"Your {label} delegation from {who} starts on {as_utc(delegation.start_at):%Y-%m-%d %H:%M} UTC."
        if state == STATE_EXPIRED:
            return f"Your {label} delegation from {who} expired on {as_utc(delegation.end_at):%Y-%m-%d %H:%M} UTC."
        if state == STATE_REVOKED:
            return f"Your {label} delegation from {who} was revoked."
    return None


@dataclass
class Authority:
    """Who is acting, and on whose authority."""

    actor: User
    # The native approver whose authority is exercised. Same as actor
    # unless acting under a delegation.
    acting_for: User
    delegation: ApprovalDelegation | None = None
    # P3: the approved SoD exception this authority relies on, if any
    # (an Admin's dual-role access). Its use is logged.
    sod_exception: object | None = None

    @property
    def is_delegated(self) -> bool:
        return self.delegation is not None

    def describe(self) -> str:
        name = _display(self.actor)
        if not self.delegation:
            return name
        return f"{name} (delegate for {_display(self.acting_for)}, delegation #{self.delegation.id})"


def resolve_manager_authority(db: Session, user: User, assessment: Assessment) -> Authority:
    """The authority `user` holds to make the manager decision on `assessment`, or 403."""

    if user.role == UserRole.MANAGER.value and assessment.manager_id == user.id:
        return Authority(actor=user, acting_for=user)

    found = authorizing_delegation(db, user, assessment, AUTHORITY_MANAGER_APPROVAL)
    if found:
        delegation, delegator = found
        return Authority(actor=user, acting_for=delegator, delegation=delegation)

    reason = explain_missing_delegation(db, user, assessment, AUTHORITY_MANAGER_APPROVAL)
    if reason:
        raise HTTPException(status_code=403, detail=reason)
    if user.role != UserRole.MANAGER.value:
        raise HTTPException(status_code=403, detail="Only a Manager can make this decision.")
    raise HTTPException(status_code=403, detail="This assessment is not assigned to you.")


def admin_has_committee_authority() -> bool:
    """Retired (P3, R-GOV-02): administration and committee membership are
    mutually exclusive; an Admin acts as a committee member only through an
    approved, declared ADMIN_COMMITTEE_DUAL_ROLE exception. The P2 switch
    COMMITTEE_ADMIN_AUTHORITY no longer grants anything."""

    if os.getenv("COMMITTEE_ADMIN_AUTHORITY", "").strip().lower() in {"1", "true", "yes", "on"}:
        import logging

        logging.getLogger(__name__).warning(
            "COMMITTEE_ADMIN_AUTHORITY is set but retired: Admins need an approved SoD dual-role exception."
        )
    return False


def committee_roles() -> set[str]:
    """Roles that natively hold committee voting and sign-off authority."""

    return {UserRole.COMMITTEE_MEMBER.value}


def resolve_committee_authority(db: Session, user: User, assessment: Assessment) -> Authority:
    """The authority `user` holds to vote on or sign off `assessment`, or 403."""

    if user.role in committee_roles():
        return Authority(actor=user, acting_for=user)

    if user.role == UserRole.ADMIN.value:
        admin_has_committee_authority()  # warns if the retired switch is set
        from app.governance.sod import active_exception
        from app.models.sod_exception import TYPE_ADMIN_COMMITTEE_DUAL_ROLE

        exception = active_exception(db, user, TYPE_ADMIN_COMMITTEE_DUAL_ROLE, assessment)
        if exception is not None:
            return Authority(actor=user, acting_for=user, sod_exception=exception)

    found = authorizing_delegation(db, user, assessment, AUTHORITY_COMMITTEE_SIGN_OFF)
    if found:
        delegation, delegator = found
        return Authority(actor=user, acting_for=delegator, delegation=delegation)

    reason = explain_missing_delegation(db, user, assessment, AUTHORITY_COMMITTEE_SIGN_OFF)
    if not reason and user.role == UserRole.ADMIN.value:
        reason = (
            "Administrator access does not include committee membership. Only a Committee "
            "Member (or their delegate) can vote or make the committee decision, unless an "
            "approved, declared SoD dual-role exception covers this assessment."
        )
    raise HTTPException(
        status_code=403,
        detail=reason or "Only a Committee Member can make this decision.",
    )


# --- visibility -------------------------------------------------------------------


def active_delegations_received(db: Session, user: User, now: datetime | None = None) -> list[ApprovalDelegation]:
    """Delegations `user` currently holds as delegate, whose delegator still holds the authority."""

    now = now or now_utc()
    held = []
    for delegation in (
        db.query(ApprovalDelegation)
        .filter(ApprovalDelegation.delegate_id == user.id, ApprovalDelegation.revoked_at.is_(None))
        .all()
    ):
        if not is_active(delegation, now):
            continue
        delegator = db.query(User).filter(User.id == delegation.delegator_id).first()
        if delegator and delegator.is_active and delegator.role == _AUTHORITY_ROLE[delegation.authority]:
            held.append(delegation)
    return held

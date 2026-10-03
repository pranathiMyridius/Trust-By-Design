"""
P5 (R16.1, R16.3, R16.4): retention policy versions, legal holds and the
one retention-eligibility rule.

  * Policy versions are append-only (retention_policy_versions). A change
    is a new PROPOSED version; with independent approval on (D-1) it takes
    effect only when an eligible approver other than the proposer approves
    it, and the previous ACTIVE version is SUPERSEDED in the same
    transaction. Nothing is ever edited in place.
  * Legal holds keep the current state on assessment_retention and an
    append-only history in legal_hold_events. Release needs a reason and,
    with D-2 on, a different user from the one who set the hold.
  * evaluate() is the only place eligibility is decided. The per-
    assessment route, the eligibility report and soft delete all call it.
    Eligibility is a determination for a controlled review -- it never
    deletes anything, and nothing here schedules or purges.

Every rule comes from policy()["retention"] (app/governance/policy.py).
STATUS: PROVISIONAL -- pending governance approval. No period here is
compliance-approved; governance_approval_reference/_at on a version exist
for a later, separate approval process and are never set by this module.

Functions don't commit; the caller commits once per request.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.governance.policy import POLICY_STATUS, describe, holds, policy
from app.models.assessment import Assessment
from app.models.audit_trail import (
    AssessmentRetention,
    LegalHoldEvent,
    RetentionPolicy,
    RetentionPolicyVersion,
)
from app.models.user import User, UserRole

RECORD_TYPE_ASSESSMENT = "ASSESSMENT"
BASIS_FINAL_DECISION_DATE = "FINAL_DECISION_DATE"

STATUS_PROPOSED = "PROPOSED"
STATUS_ACTIVE = "ACTIVE"
STATUS_SUPERSEDED = "SUPERSEDED"
STATUS_REJECTED = "REJECTED"

HOLD_SET = "SET"
HOLD_RELEASED = "RELEASED"

# Eligibility outcomes, in the order they are tested (the first that
# applies wins). Only ELIGIBLE is eligible.
LEGAL_HOLD = "LEGAL_HOLD"
SOFT_DELETED = "SOFT_DELETED"
NO_POLICY = "NO_POLICY"
INVALID_POLICY = "INVALID_POLICY"
NOT_STARTED = "NOT_STARTED"
INVALID_DATE = "INVALID_DATE"
RETAINED = "RETAINED"
ELIGIBLE = "ELIGIBLE"
ELIGIBILITY_STATUSES = [LEGAL_HOLD, SOFT_DELETED, NO_POLICY, INVALID_POLICY, NOT_STARTED, INVALID_DATE, RETAINED, ELIGIBLE]

SEED_REASON = "Migrated existing default; not compliance-approved"
DEFAULT_RETENTION_DAYS = 2555  # ~7 years -- provisional (Q-3), not compliance-approved


def utcnow() -> datetime:
    """Naive UTC, matching how the DateTime columns are stored."""

    return datetime.now(timezone.utc).replace(tzinfo=None)


def naive_utc(value: datetime | None) -> datetime | None:
    if value is not None and value.tzinfo is not None:
        value = value.astimezone(timezone.utc).replace(tzinfo=None)
    return value


def rules() -> dict:
    return policy()["retention"]


def _name(user: User | None) -> str:
    return (user.full_name or user.email) if user else "System"


def _audit(db: Session, action: str, user: User | None, details: str, assessment_id: int | None = None,
           previous_status: str | None = None, new_status: str | None = None) -> None:
    from app.services.audit_service import AuditAction, log_audit_event

    log_audit_event(
        db,
        assessment_id=assessment_id,
        action=getattr(AuditAction, action),
        previous_status=previous_status,
        new_status=new_status,
        actor=_name(user),
        actor_id=user.id if user else None,
        details=f"{details} Policy status: {POLICY_STATUS}.",
    )


# -- who may do what ----------------------------------------------------------


def can_view(user: User) -> bool:
    """The retention administration screen: proposers, approvers, report
    readers and legal-hold administrators."""

    r = rules()
    return any(
        holds(user, r[key])
        for key in ("proposers", "approvers", "eligibility_report_readers", "legal_hold_setters", "legal_hold_releasers")
    )


def can_see_hold_detail(user: User) -> bool:
    """Hold reasons and matter references are shown to retention
    administrators and report readers only; everyone else who can see the
    assessment sees whether it is held."""

    return can_view(user)


def permissions(user: User) -> dict:
    r = rules()
    return {
        "can_view": can_view(user),
        "can_propose": holds(user, r["proposers"]),
        "can_approve": holds(user, r["approvers"]),
        "can_set_hold": holds(user, r["legal_hold_setters"]),
        "can_release_hold": holds(user, r["legal_hold_releasers"]),
        "can_soft_delete": holds(user, r["soft_deleters"]),
        "can_read_report": holds(user, r["eligibility_report_readers"]),
        "require_independent_approval": bool(r["require_independent_approval"]),
        "release_requires_different_user": bool(r["release_requires_different_user"]),
        "proposers": describe(r["proposers"]),
        "approvers": describe(r["approvers"]),
        "min_retention_days": r["min_retention_days"],
        "max_retention_days": r["max_retention_days"],
        "min_reason_length": r["min_reason_length"],
        "record_types": sorted(r["record_types"]),
        "policy_status": POLICY_STATUS,
    }


# -- policy versions ----------------------------------------------------------


def _versions(db: Session, record_type: str):
    return db.query(RetentionPolicyVersion).filter(RetentionPolicyVersion.record_type == record_type)


def active_version(db: Session, record_type: str = RECORD_TYPE_ASSESSMENT) -> RetentionPolicyVersion | None:
    return _versions(db, record_type).filter(RetentionPolicyVersion.status == STATUS_ACTIVE).first()


def pending_version(db: Session, record_type: str = RECORD_TYPE_ASSESSMENT) -> RetentionPolicyVersion | None:
    return _versions(db, record_type).filter(RetentionPolicyVersion.status == STATUS_PROPOSED).first()


def effective_version(db: Session, record_type: str, at: datetime | None = None) -> RetentionPolicyVersion | None:
    """The approved version in force at `at` (default now): the ACTIVE or
    SUPERSEDED version with the latest effective_from not after `at`.
    Proposed and rejected versions are never in force."""

    at = naive_utc(at) or utcnow()
    return (
        _versions(db, record_type)
        .filter(
            RetentionPolicyVersion.status.in_([STATUS_ACTIVE, STATUS_SUPERSEDED]),
            RetentionPolicyVersion.effective_from.isnot(None),
            RetentionPolicyVersion.effective_from <= at,
        )
        .order_by(RetentionPolicyVersion.effective_from.desc(), RetentionPolicyVersion.version.desc())
        .first()
    )


def ensure_seeded(db: Session) -> RetentionPolicyVersion:
    """ASSESSMENT v1 from the legacy single policy row (or the provisional
    default), if no version exists yet. Migration 0019 does the same; this
    covers a database created after it from the models alone."""

    existing = active_version(db, RECORD_TYPE_ASSESSMENT)
    if existing is not None or _versions(db, RECORD_TYPE_ASSESSMENT).first() is not None:
        return existing
    legacy = db.query(RetentionPolicy).filter(RetentionPolicy.is_active.is_(True)).first()
    days = legacy.default_retention_days if legacy else DEFAULT_RETENTION_DAYS
    now = utcnow()
    seed = RetentionPolicyVersion(
        record_type=RECORD_TYPE_ASSESSMENT,
        version=1,
        retention_days=days,
        basis=BASIS_FINAL_DECISION_DATE,
        effective_from=naive_utc(legacy.created_at) if legacy and legacy.created_at else now,
        status=STATUS_ACTIVE,
        policy_status=POLICY_STATUS,
        change_reason=SEED_REASON,
        proposed_at=now,
        decided_at=now,
        decision_reason=SEED_REASON,
        system_seeded=True,
    )
    db.add(seed)
    db.flush()
    return seed


def approver_problem(version: RetentionPolicyVersion, user: User) -> str | None:
    """Why `user` may not approve or reject `version`, or None."""

    if version.status != STATUS_PROPOSED:
        return f"This version is {version.status.lower()}, not pending approval."
    if version.proposed_by_id is not None and version.proposed_by_id == user.id:
        return "You proposed this change and cannot approve or reject it (independent approval)."
    rule = rules()["approvers"]
    if not holds(user, rule):
        if user.role == UserRole.ADMIN.value:
            return f"An Admin can propose a retention change but not approve it. Approval needs: {describe(rule)}."
        return f"Approving a retention change needs: {describe(rule)}."
    return None


def _validate_proposal(db: Session, record_type: str, retention_days: int, reason: str) -> RetentionPolicyVersion | None:
    r = rules()
    if record_type not in r["record_types"]:
        raise HTTPException(
            status_code=422,
            detail=f"Unknown record type '{record_type}'. Configured: {', '.join(sorted(r['record_types']))}.",
        )
    if not isinstance(retention_days, int) or isinstance(retention_days, bool):
        raise HTTPException(status_code=422, detail="retention_days must be a whole number of days.")
    low, high = r["min_retention_days"], r["max_retention_days"]
    if not low <= retention_days <= high:
        raise HTTPException(
            status_code=422,
            detail=f"retention_days must be between {low} and {high} (provisional bounds, pending governance approval).",
        )
    if len((reason or "").strip()) < r["min_reason_length"]:
        raise HTTPException(
            status_code=422, detail=f"A business justification of at least {r['min_reason_length']} characters is required."
        )
    if pending_version(db, record_type) is not None:
        raise HTTPException(
            status_code=409,
            detail=f"A {record_type} retention change is already pending approval; decide it before proposing another.",
        )
    current = active_version(db, record_type)
    if current is not None and current.retention_days == retention_days:
        raise HTTPException(status_code=422, detail=f"The active {record_type} period is already {retention_days} days.")
    return current


def propose(db: Session, user: User, record_type: str, retention_days: int, reason: str) -> RetentionPolicyVersion:
    if not holds(user, rules()["proposers"]):
        raise HTTPException(
            status_code=403, detail=f"Proposing a retention change needs: {describe(rules()['proposers'])}."
        )
    ensure_seeded(db)
    current = _validate_proposal(db, record_type, retention_days, reason)
    latest = _versions(db, record_type).order_by(RetentionPolicyVersion.version.desc()).first()
    version = RetentionPolicyVersion(
        record_type=record_type,
        version=(latest.version + 1) if latest else 1,
        retention_days=retention_days,
        basis=rules()["record_types"][record_type]["basis"],
        status=STATUS_PROPOSED,
        policy_status=POLICY_STATUS,
        change_reason=reason.strip(),
        proposed_by_id=user.id,
        proposed_at=utcnow(),
        supersedes_id=current.id if current else None,
        previous_retention_days=current.retention_days if current else None,
    )
    db.add(version)
    db.flush()
    _audit(
        db, "RETENTION_POLICY_PROPOSED", user,
        f"Retention policy {record_type} v{version.version} proposed: "
        f"{_days(version.previous_retention_days)} -> {retention_days} days "
        f"(replaces v{current.version if current else '-'}). Reason: {version.change_reason}.",
        new_status=STATUS_PROPOSED,
    )
    if not rules()["require_independent_approval"]:
        # D-1 off: still versioned, reasoned and audited, but takes effect now.
        _activate(db, version, user, version.change_reason)
    return version


def decide(db: Session, version: RetentionPolicyVersion, user: User, decision: str, reason: str) -> RetentionPolicyVersion:
    problem = approver_problem(version, user)
    if problem:
        raise HTTPException(status_code=409 if "not pending" in problem else 403, detail=problem)
    if not (reason or "").strip():
        raise HTTPException(status_code=422, detail="A reason is required for the decision.")
    reason = reason.strip()
    if decision == "APPROVE":
        _audit(
            db, "RETENTION_POLICY_APPROVED", user,
            f"Retention policy {version.record_type} v{version.version} approved "
            f"({_days(version.previous_retention_days)} -> {version.retention_days} days). Reason: {reason}.",
            previous_status=STATUS_PROPOSED, new_status=STATUS_ACTIVE,
        )
        _activate(db, version, user, reason)
    elif decision == "REJECT":
        version.status = STATUS_REJECTED
        version.decided_by_id = user.id
        version.decided_at = utcnow()
        version.decision_reason = reason
        _audit(
            db, "RETENTION_POLICY_REJECTED", user,
            f"Retention policy {version.record_type} v{version.version} rejected "
            f"({version.retention_days} days not adopted; active period unchanged). Reason: {reason}.",
            previous_status=STATUS_PROPOSED, new_status=STATUS_REJECTED,
        )
    else:
        raise HTTPException(status_code=422, detail="decision must be APPROVE or REJECT")
    return version


def _activate(db: Session, version: RetentionPolicyVersion, user: User, reason: str) -> None:
    now = utcnow()
    previous = active_version(db, version.record_type)
    if previous is not None and previous.id != version.id:
        previous.status = STATUS_SUPERSEDED
        previous.superseded_at = now
        previous.superseded_by_id = version.id
        # Flush first: at most one ACTIVE version per record type is
        # enforced by a unique index.
        db.flush()
        _audit(
            db, "RETENTION_POLICY_SUPERSEDED", user,
            f"Retention policy {previous.record_type} v{previous.version} ({previous.retention_days} days) "
            f"superseded by v{version.version}.",
            previous_status=STATUS_ACTIVE, new_status=STATUS_SUPERSEDED,
        )
    version.status = STATUS_ACTIVE
    version.decided_by_id = user.id
    version.decided_at = now
    version.decision_reason = reason
    version.effective_from = now
    db.flush()
    _audit(
        db, "RETENTION_POLICY_ACTIVATED", user,
        f"Retention policy {version.record_type} v{version.version} in force from {now.isoformat()}: "
        f"{_days(version.previous_retention_days)} -> {version.retention_days} days.",
        previous_status=STATUS_PROPOSED, new_status=STATUS_ACTIVE,
    )


def _days(value: int | None) -> str:
    return f"{value}" if value is not None else "none"


def version_dict(db: Session, version: RetentionPolicyVersion, user: User | None = None) -> dict:
    iso = lambda value: value.isoformat() if value else None  # noqa: E731

    def name(user_id):
        person = db.get(User, user_id) if user_id else None
        return _name(person) if person else None

    out = {
        "id": version.id,
        "record_type": version.record_type,
        "version": version.version,
        "retention_days": version.retention_days,
        "previous_retention_days": version.previous_retention_days,
        "basis": version.basis,
        "status": version.status,
        "effective_from": iso(version.effective_from),
        "change_reason": version.change_reason,
        "proposed_by": name(version.proposed_by_id) or ("System (migration)" if version.system_seeded else None),
        "proposed_by_id": version.proposed_by_id,
        "proposed_at": iso(version.proposed_at),
        "decided_by": name(version.decided_by_id) or ("System (migration)" if version.system_seeded else None),
        "decided_at": iso(version.decided_at),
        "decision_reason": version.decision_reason,
        "supersedes_id": version.supersedes_id,
        "superseded_at": iso(version.superseded_at),
        "superseded_by_id": version.superseded_by_id,
        "system_seeded": bool(version.system_seeded),
        "governance_approval_reference": version.governance_approval_reference,
        "governance_approved_at": iso(version.governance_approved_at),
        "policy_status": POLICY_STATUS,
    }
    if user is not None:
        problem = approver_problem(version, user)
        out["actions"] = {"decide": {"allowed": problem is None, "reason": problem}}
    return out


# -- legal holds --------------------------------------------------------------


def get_retention_row(db: Session, assessment_id: int) -> AssessmentRetention:
    row = db.query(AssessmentRetention).filter(AssessmentRetention.assessment_id == assessment_id).first()
    if row is None:
        row = AssessmentRetention(assessment_id=assessment_id)
        db.add(row)
        db.flush()
    return row


def _same_person_as_setter(retention: AssessmentRetention, user: User) -> bool:
    if retention.legal_hold_set_by_id is not None:
        return retention.legal_hold_set_by_id == user.id
    # Holds set before P5 recorded a name only (fallback, as in P3).
    return bool(retention.legal_hold_set_by) and retention.legal_hold_set_by == _name(user)


def set_hold_problem(retention: AssessmentRetention, user: User) -> str | None:
    if not holds(user, rules()["legal_hold_setters"]):
        return f"Placing a legal hold needs: {describe(rules()['legal_hold_setters'])}."
    if retention.is_deleted:
        return "This assessment has been soft-deleted; a legal hold can't be placed on it."
    if retention.legal_hold:
        return "A legal hold is already in place."
    return None


def release_hold_problem(retention: AssessmentRetention, user: User) -> str | None:
    if not holds(user, rules()["legal_hold_releasers"]):
        return f"Releasing a legal hold needs: {describe(rules()['legal_hold_releasers'])}."
    if not retention.legal_hold:
        return "There is no legal hold to release."
    if rules()["release_requires_different_user"] and _same_person_as_setter(retention, user):
        return "You placed this legal hold; a different authorized user must release it (independent release)."
    return None


def _status_for(problem: str) -> int:
    return 403 if "needs:" in problem or "different authorized user" in problem else 409


def set_hold(db: Session, assessment: Assessment, retention: AssessmentRetention, user: User, reason: str,
             matter_reference: str | None = None) -> LegalHoldEvent:
    problem = set_hold_problem(retention, user)
    if problem:
        raise HTTPException(status_code=_status_for(problem), detail=problem)
    now = utcnow()
    retention.legal_hold = True
    retention.legal_hold_reason = reason
    retention.legal_hold_set_by = _name(user)
    retention.legal_hold_set_by_id = user.id
    retention.legal_hold_set_at = now
    event = LegalHoldEvent(
        assessment_id=assessment.id, action=HOLD_SET, reason=reason, matter_reference=matter_reference,
        actor_id=user.id, actor_name=_name(user), created_at=now,
    )
    db.add(event)
    db.flush()
    _audit(
        db, "LEGAL_HOLD_SET", user,
        f"Legal hold placed (hold event {event.id}; previous state: no hold). Reason: {reason}."
        + (f" Matter reference: {matter_reference}." if matter_reference else ""),
        assessment_id=assessment.id, previous_status="NO_HOLD", new_status="HELD",
    )
    return event


def release_hold(db: Session, assessment: Assessment, retention: AssessmentRetention, user: User, reason: str) -> LegalHoldEvent:
    problem = release_hold_problem(retention, user)
    if problem:
        raise HTTPException(status_code=_status_for(problem), detail=problem)
    now = utcnow()
    held_since = retention.legal_hold_set_at
    set_by = retention.legal_hold_set_by
    retention.legal_hold = False
    retention.legal_hold_reason = None
    retention.legal_hold_set_by = None
    retention.legal_hold_set_by_id = None
    retention.legal_hold_set_at = None
    event = LegalHoldEvent(
        assessment_id=assessment.id, action=HOLD_RELEASED, reason=reason,
        actor_id=user.id, actor_name=_name(user), created_at=now,
    )
    db.add(event)
    db.flush()
    _audit(
        db, "LEGAL_HOLD_RELEASED", user,
        f"Legal hold released (hold event {event.id}; set by {set_by or 'unknown'}"
        f"{' on ' + held_since.isoformat() if held_since else ''}). Reason: {reason}.",
        assessment_id=assessment.id, previous_status="HELD", new_status="NO_HOLD",
    )
    return event


def hold_history(db: Session, assessment_id: int) -> list[dict]:
    rows = (
        db.query(LegalHoldEvent)
        .filter(LegalHoldEvent.assessment_id == assessment_id)
        .order_by(LegalHoldEvent.created_at.asc(), LegalHoldEvent.id.asc())
        .all()
    )
    return [
        {
            "id": e.id,
            "action": e.action,
            "reason": e.reason,
            "matter_reference": e.matter_reference,
            "actor": e.actor_name,
            "at": e.created_at.isoformat() if e.created_at else None,
            "backfilled": bool(e.system_seeded),
        }
        for e in rows
    ]


# -- eligibility (the one rule) -----------------------------------------------


def basis_date(assessment: Assessment) -> datetime | None:
    """FINAL_DECISION_DATE: when the final decision was recorded. None
    until there is one -- retention hasn't started."""

    from app.services.decision_lock import FINAL_DECISION_STATUSES

    if assessment.status not in FINAL_DECISION_STATUSES:
        return None
    return naive_utc(assessment.committee_decided_at or assessment.manager_decided_at)


def evaluate(
    db: Session,
    assessment: Assessment,
    retention: AssessmentRetention | None = None,
    at: datetime | None = None,
    record_type: str = RECORD_TYPE_ASSESSMENT,
    version: RetentionPolicyVersion | None | bool = False,
) -> dict:
    """Whether `assessment` is eligible for controlled disposal review now.
    Deterministic and explainable; missing or invalid information is never
    eligible, and an active legal hold always overrides.
    `version` may be passed in (the report evaluates many records against
    one lookup); False means look it up."""

    from app.services.decision_lock import FINAL_DECISION_STATUSES

    at = naive_utc(at) or utcnow()
    if retention is None:
        retention = (
            db.query(AssessmentRetention).filter(AssessmentRetention.assessment_id == assessment.id).first()
        )
    held = bool(retention and retention.legal_hold)
    deleted = bool(retention and retention.is_deleted)
    if version is False:
        version = effective_version(db, record_type, at) if record_type in rules()["record_types"] else None

    r = rules()
    days = version.retention_days if version is not None else None
    policy_valid = (
        version is not None
        and isinstance(days, int)
        and r["min_retention_days"] <= days <= r["max_retention_days"]
        and version.basis == r["record_types"].get(record_type, {}).get("basis")
    )

    raw_basis = assessment.committee_decided_at or assessment.manager_decided_at
    basis = basis_date(assessment)
    eligible_at = basis + timedelta(days=days) if (basis is not None and policy_valid) else None

    if held:
        status, reason = LEGAL_HOLD, "An active legal hold overrides retention eligibility until it is released."
    elif deleted:
        status, reason = SOFT_DELETED, "Already soft-deleted under the retention process; kept for the record (nothing is purged)."
    elif version is None:
        status, reason = NO_POLICY, f"No approved retention policy is in force for record type {record_type}; never eligible."
    elif not policy_valid:
        status, reason = INVALID_POLICY, (
            f"The policy in force (v{version.version}) is outside the configured bounds or basis; never eligible until corrected."
        )
    elif assessment.status not in FINAL_DECISION_STATUSES:
        status, reason = NOT_STARTED, f"No final decision yet (status {assessment.status}); the retention period has not started."
    elif raw_basis is None:
        status, reason = INVALID_DATE, "Finally decided, but the decision date is missing; never eligible until it is recorded."
    elif basis > at:
        status, reason = INVALID_DATE, "The recorded decision date is in the future; never eligible until it is corrected."
    elif at < eligible_at:
        status, reason = RETAINED, f"Within the {days}-day retention period (v{version.version}); eligible from {eligible_at.date().isoformat()}."
    else:
        status, reason = ELIGIBLE, (
            f"The {days}-day retention period (v{version.version}) has elapsed and there is no legal hold. "
            "Eligible for controlled disposal review only; nothing is deleted automatically."
        )

    return {
        "record_id": assessment.id,
        "record_type": record_type,
        "reference": assessment.reference_id,
        "assessment_status": assessment.status,
        "policy_version_id": version.id if version is not None else None,
        "policy_version": version.version if version is not None else None,
        "retention_days": days,
        "basis": version.basis if version is not None else None,
        "basis_date": basis.isoformat() if basis else None,
        "eligible_at": eligible_at.isoformat() if eligible_at else None,
        "eligible": status == ELIGIBLE,
        "eligibility_status": status,
        "legal_hold": held,
        "is_deleted": deleted,
        "reason": reason,
        "evaluated_at": at.isoformat(),
        "policy_status": POLICY_STATUS,
    }


def soft_delete(db: Session, assessment: Assessment, retention: AssessmentRetention, user: User, reason: str) -> dict:
    """R16.1/AC5: a controlled, logged soft delete -- no row is removed.
    Allowed only when evaluate() says ELIGIBLE; records the policy version
    that made it so."""

    if not holds(user, rules()["soft_deleters"]):
        raise HTTPException(status_code=403, detail=f"Soft deletion needs: {describe(rules()['soft_deleters'])}.")
    result = evaluate(db, assessment, retention)
    if result["eligibility_status"] == SOFT_DELETED:
        raise HTTPException(status_code=400, detail="This assessment is already deleted.")
    if not result["eligible"]:
        raise HTTPException(status_code=409, detail=f"Not eligible for deletion: {result['reason']}")
    retention.is_deleted = True
    retention.deleted_by = _name(user)
    retention.deleted_at = utcnow()
    retention.deletion_reason = reason
    retention.retention_policy_version_id = result["policy_version_id"]
    _audit(
        db, "ASSESSMENT_SOFT_DELETED", user,
        f"Assessment soft-deleted under retention policy {result['record_type']} v{result['policy_version']} "
        f"({result['retention_days']} days from {result['basis_date']}; eligible from {result['eligible_at']}). "
        f"No data was physically removed. Reason: {reason}.",
        assessment_id=assessment.id, previous_status="RETAINED", new_status="SOFT_DELETED",
    )
    return result


def actions_for(retention: AssessmentRetention, user: User, eligibility: dict) -> dict:
    def entry(problem):
        return {"allowed": problem is None, "reason": problem}

    if not holds(user, rules()["soft_deleters"]):
        delete_problem = f"Soft deletion needs: {describe(rules()['soft_deleters'])}."
    elif retention.is_deleted:
        delete_problem = "Already deleted."
    elif not eligibility["eligible"]:
        delete_problem = eligibility["reason"]
    else:
        delete_problem = None
    return {
        "set_hold": entry(set_hold_problem(retention, user)),
        "release_hold": entry(release_hold_problem(retention, user)),
        "soft_delete": entry(delete_problem),
    }

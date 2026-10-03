"""
P3 (R15.2, R-GOV-01/02): SoD exception requests and their lifecycle, plus
the governance policy and committee-readiness read APIs.

All rules live in app/governance/sod.py and app/governance/readiness.py;
this module validates input, checks object-level access, translates
errors and commits once per request. Policy is PROVISIONAL.
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, field_validator
from sqlalchemy.orm import Session
from sqlalchemy.orm.exc import StaleDataError

from app.auth.access import ensure_assessment_visible
from app.auth.dependencies import get_current_user, log_denied_attempt
from app.database import get_db
from app.governance import sod
from app.governance.policy import POLICY_STATUS, policy
from app.models.assessment import Assessment
from app.models.sod_exception import EXCEPTION_TYPES, RISK_LEVELS, SodException, SodExceptionEvent
from app.models.user import READ_ONLY_ROLES, User, UserRole

router = APIRouter(prefix="/api", tags=["SoD Exceptions"])


class SodExceptionCreate(BaseModel):
    exception_type: str
    affected_user_id: int
    assessment_id: Optional[int] = None
    business_justification: str
    standard_workflow_reason: str
    risk_level: str
    compensating_controls: str
    start_at: datetime
    end_at: datetime
    assigned_approver_id: Optional[int] = None
    assigned_reviewer_id: Optional[int] = None

    @field_validator("exception_type")
    @classmethod
    def type_known(cls, value: str) -> str:
        if value not in EXCEPTION_TYPES:
            raise ValueError(f"exception_type must be one of: {', '.join(sorted(EXCEPTION_TYPES))}")
        return value

    @field_validator("risk_level")
    @classmethod
    def risk_known(cls, value: str) -> str:
        if value not in RISK_LEVELS:
            raise ValueError(f"risk_level must be one of: {', '.join(RISK_LEVELS)}")
        return value

    @field_validator("business_justification", "standard_workflow_reason", "compensating_controls")
    @classmethod
    def required_text(cls, value: str) -> str:
        if not value or not value.strip():
            raise ValueError("This field is required.")
        return value.strip()


class Rationale(BaseModel):
    rationale: str

    @field_validator("rationale")
    @classmethod
    def required(cls, value: str) -> str:
        if not value or not value.strip():
            raise ValueError("A rationale is required.")
        return value.strip()


class Decision(Rationale):
    decision: str

    @field_validator("decision")
    @classmethod
    def known(cls, value: str) -> str:
        if value not in {"APPROVE", "REJECT"}:
            raise ValueError("decision must be APPROVE or REJECT")
        return value


def _name(db: Session, user_id: int | None) -> str | None:
    if not user_id:
        return None
    user = db.get(User, user_id)
    return (user.full_name or user.email) if user else None


def _response(db: Session, exception: SodException, user: User) -> dict:
    events = (
        db.query(SodExceptionEvent)
        .filter(SodExceptionEvent.exception_id == exception.id)
        .order_by(SodExceptionEvent.created_at.asc(), SodExceptionEvent.id.asc())
        .all()
    )
    iso = lambda value: value.isoformat() if value else None  # noqa: E731
    return {
        "id": exception.id,
        "reference": exception.reference,
        "exception_type": exception.exception_type,
        "status": exception.status,
        "requestor_id": exception.requestor_id,
        "requestor": _name(db, exception.requestor_id),
        "affected_user_id": exception.affected_user_id,
        "affected_user": _name(db, exception.affected_user_id),
        "conflicting_roles": json.loads(exception.conflicting_roles or "[]"),
        "assessment_id": exception.assessment_id,
        "business_justification": exception.business_justification,
        "standard_workflow_reason": exception.standard_workflow_reason,
        "risk_level": exception.risk_level,
        "compensating_controls": exception.compensating_controls,
        "start_at": iso(exception.start_at),
        "end_at": iso(exception.end_at),
        "tier": exception.tier,
        "tier_reasons": json.loads(exception.tier_reasons or "[]"),
        "repeated": exception.repeated,
        "assigned_approver_id": exception.assigned_approver_id,
        "assigned_approver": _name(db, exception.assigned_approver_id),
        "assigned_reviewer_id": exception.assigned_reviewer_id,
        "assigned_reviewer": _name(db, exception.assigned_reviewer_id),
        "decision": exception.decision,
        "decision_rationale": exception.decision_rationale,
        "decided_by": _name(db, exception.decided_by_id),
        "decided_at": iso(exception.decided_at),
        "declaration": exception.declaration,
        "declared_at": iso(exception.declared_at),
        "revoked_by": _name(db, exception.revoked_by_id),
        "revoked_at": iso(exception.revoked_at),
        "revocation_reason": exception.revocation_reason,
        "expired_at": iso(exception.expired_at),
        "last_reviewed_at": iso(exception.last_reviewed_at),
        "last_reviewed_by": _name(db, exception.last_reviewed_by_id),
        "last_review_note": exception.last_review_note,
        "submitted_at": iso(exception.submitted_at),
        "created_at": iso(exception.created_at),
        "updated_at": iso(exception.updated_at),
        "flags": sod.flags(exception),
        "actions": sod.actions_for(db, exception, user),
        "history": [
            {
                "action": e.action,
                "from_status": e.from_status,
                "to_status": e.to_status,
                "actor": e.actor,
                "detail": e.detail,
                "at": iso(e.created_at),
            }
            for e in events
        ],
        "policy_status": POLICY_STATUS,
    }


def _can_see_all(user: User) -> bool:
    if user.role in {UserRole.ADMIN.value, *READ_ONLY_ROLES}:
        return True
    rules = policy()
    return any(sod.holds(user, rule) for rule in rules["exception_approvers"].values()) or sod.holds(
        user, rules["exception_revokers"]
    )


def _load(db: Session, exception_id: int, user: User, request: Request) -> SodException:
    exception = db.get(SodException, exception_id)
    if exception is None:
        raise HTTPException(status_code=404, detail="SoD exception not found")
    party = user.id in {exception.requestor_id, exception.affected_user_id, exception.assigned_approver_id, exception.assigned_reviewer_id}
    if not (party or _can_see_all(user)):
        log_denied_attempt(db, user, request, f"SoD exception {exception_id} outside the user's visibility")
        raise HTTPException(status_code=403, detail="You do not have access to this SoD exception.")
    if exception.assessment_id is not None:
        ensure_assessment_visible(db, exception.assessment_id, user, request)
    sod.expire_if_due(db, exception)
    return exception


def _commit(db: Session) -> None:
    try:
        db.commit()
    except StaleDataError:
        db.rollback()
        raise HTTPException(status_code=409, detail="Someone else changed this request at the same time; reload and try again.")


def _refuse(db: Session, user: User, request: Request, exc: HTTPException, exception: SodException) -> None:
    db.rollback()
    if exc.status_code == 403:
        log_denied_attempt(db, user, request, f"SoD exception {exception.reference}: {exc.detail}", assessment_id=exception.assessment_id)


@router.get("/sod-exceptions")
def list_exceptions(
    scope: str = "mine",
    request: Request = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """scope: mine (requested by / for / assigned to me), awaiting_me
    (pending requests I am eligible to decide), all (governance roles)."""

    if sod.expire_due(db):
        db.commit()
    query = db.query(SodException).order_by(SodException.created_at.desc())
    if scope == "all":
        if not _can_see_all(current_user):
            raise HTTPException(status_code=403, detail="Only governance roles can list every SoD exception.")
        rows = query.all()
    elif scope == "awaiting_me":
        rows = [e for e in query.filter(SodException.status == "PENDING_APPROVAL").all() if sod.approver_problem(db, e, current_user) is None]
    else:
        rows = [
            e for e in query.all()
            if current_user.id in {e.requestor_id, e.affected_user_id, e.assigned_approver_id, e.assigned_reviewer_id}
        ]
    return [_response(db, e, current_user) for e in rows]


@router.get("/sod-exceptions/candidates")
def list_candidates(exception_type: str, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    """Who an exception of this type could be for -- id, name and role only
    (least privilege: no e-mail or scope), active users only."""

    roles = {"COMMITTEE_SEPARATION": UserRole.COMMITTEE_MEMBER.value, "ADMIN_COMMITTEE_DUAL_ROLE": UserRole.ADMIN.value}
    if exception_type not in roles:
        raise HTTPException(status_code=422, detail=f"exception_type must be one of: {', '.join(sorted(roles))}")
    people = db.query(User).filter(User.role == roles[exception_type], User.is_active.is_(True)).order_by(User.full_name).all()
    return [{"id": u.id, "name": u.full_name or f"User {u.id}", "role": u.role} for u in people]


@router.post("/sod-exceptions", status_code=201)
def create_exception(
    payload: SodExceptionCreate,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Creates a DRAFT. A request never grants anything by itself."""

    if payload.assessment_id is not None:
        ensure_assessment_visible(db, payload.assessment_id, current_user, request)
    exception = sod.create(db, current_user, payload.model_dump())
    _commit(db)
    return _response(db, exception, current_user)


@router.get("/sod-exceptions/{exception_id}")
def get_exception(exception_id: int, request: Request, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    exception = _load(db, exception_id, current_user, request)
    db.commit()  # persists an expiry noticed on read
    return _response(db, exception, current_user)


def _act(action, exception_id, request, db, user, *args):
    exception = _load(db, exception_id, user, request)
    try:
        action(db, exception, user, *args)
    except HTTPException as exc:
        _refuse(db, user, request, exc, exception)
        raise
    _commit(db)
    db.refresh(exception)
    return _response(db, exception, user)


@router.post("/sod-exceptions/{exception_id}/submit")
def submit_exception(exception_id: int, request: Request, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    return _act(sod.submit, exception_id, request, db, current_user)


@router.post("/sod-exceptions/{exception_id}/decision")
def decide_exception(exception_id: int, payload: Decision, request: Request, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    return _act(sod.decide, exception_id, request, db, current_user, payload.decision, payload.rationale)


@router.post("/sod-exceptions/{exception_id}/declaration")
def declare_conflicts(exception_id: int, payload: Rationale, request: Request, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    """The affected person's conflict-of-interest declaration (`rationale`
    holds the statement); required before an approved exception is used."""

    return _act(sod.declare, exception_id, request, db, current_user, payload.rationale)


@router.post("/sod-exceptions/{exception_id}/revoke")
def revoke_exception(exception_id: int, payload: Rationale, request: Request, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    return _act(sod.revoke, exception_id, request, db, current_user, payload.rationale)


@router.post("/sod-exceptions/{exception_id}/review")
def review_exception(exception_id: int, payload: Rationale, request: Request, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    return _act(sod.periodic_review, exception_id, request, db, current_user, payload.rationale)


# -- governance read APIs ---------------------------------------------------------


@router.get("/governance/policy")
def get_governance_policy(current_user: User = Depends(get_current_user)):
    """The effective governance rules and their approval status."""

    return {"policy_status": POLICY_STATUS, "policy": policy()}


@router.get("/assessments/{assessment_id}/readiness")
def get_readiness(
    assessment_id: int,
    purpose: str = "COMMITTEE_SUBMISSION",
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """P3 (R-GOV-04): the authoritative committee-readiness evaluation."""

    from app.governance.readiness import COMMITTEE_SUBMISSION, FINAL_DECISION, evaluate

    if purpose not in {COMMITTEE_SUBMISSION, FINAL_DECISION}:
        raise HTTPException(status_code=422, detail="purpose must be COMMITTEE_SUBMISSION or FINAL_DECISION")
    assessment = db.get(Assessment, assessment_id)
    if assessment is None:
        raise HTTPException(status_code=404, detail="Assessment not found")
    result = evaluate(db, assessment, purpose)
    db.commit()  # the challenge evaluation may have reconciled findings
    return result

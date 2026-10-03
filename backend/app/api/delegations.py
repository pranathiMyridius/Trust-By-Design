"""
AW.7: approval delegation endpoints.

A Manager or Committee Member delegates their own approval authority;
an Admin may record one on an absent approver's behalf. Delegations are
never deleted, only revoked, so the record of who could approve what,
and when, survives. The rules themselves live in
app/services/delegation.py.
"""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.auth.dependencies import get_current_user
from app.database import get_db
from app.models.approval_delegation import ApprovalDelegation
from app.models.user import User, UserRole
from app.schemas.delegation import (
    DelegationCreate,
    DelegationOptions,
    DelegationResponse,
    DelegationRevoke,
    DelegationUserOption,
)
from app.services import delegation as rules
from app.services.audit_service import AuditAction, log_audit_event

router = APIRouter(prefix="/api/delegations", tags=["Delegations"])

_DELEGATOR_ROLES = {UserRole.MANAGER.value, UserRole.COMMITTEE_MEMBER.value}


def _name(db: Session, user_id: int | None) -> str | None:
    if user_id is None:
        return None
    user = db.query(User).filter(User.id == user_id).first()
    return (user.full_name or user.email) if user else None


def _to_response(db: Session, delegation: ApprovalDelegation) -> DelegationResponse:
    return DelegationResponse(
        id=delegation.id,
        delegator_id=delegation.delegator_id,
        delegator_name=_name(db, delegation.delegator_id),
        delegate_id=delegation.delegate_id,
        delegate_name=_name(db, delegation.delegate_id),
        authority=delegation.authority,
        scope_type=delegation.scope_type,
        scope_assessment_id=delegation.scope_assessment_id,
        start_at=rules.as_utc(delegation.start_at),
        end_at=rules.as_utc(delegation.end_at),
        reason=delegation.reason,
        created_by_id=delegation.created_by_id,
        created_at=rules.as_utc(delegation.created_at),
        revoked_at=rules.as_utc(delegation.revoked_at),
        revoked_by_id=delegation.revoked_by_id,
        revoke_reason=delegation.revoke_reason,
        state=rules.state_of(delegation),
    )


def _describe(db: Session, delegation: ApprovalDelegation) -> str:
    scope = (
        f"assessment {delegation.scope_assessment_id}"
        if delegation.scope_type == rules.SCOPE_ASSESSMENT
        else "all of the delegator's approvals"
    )
    # An emergency delegation: set up for an absent approver by someone
    # else (an Admin), naming the authorized emergency approver. The Admin
    # never gains the authority (the delegate must be an active Manager).
    emergency = (
        f"EMERGENCY delegation set up by {_name(db, delegation.created_by_id)} on behalf of the approver. "
        if delegation.created_by_id and delegation.created_by_id != delegation.delegator_id
        else ""
    )
    return emergency + (
        f"{rules.AUTHORITY_LABELS[delegation.authority].capitalize()} delegated by "
        f"{_name(db, delegation.delegator_id)} to {_name(db, delegation.delegate_id)} "
        f"(delegation #{delegation.id}) for {scope}, from "
        f"{rules.as_utc(delegation.start_at):%Y-%m-%d %H:%M} to "
        f"{rules.as_utc(delegation.end_at):%Y-%m-%d %H:%M} UTC. Reason: {delegation.reason}"
    )


@router.get("/options", response_model=DelegationOptions)
def delegation_options(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    delegates = (
        db.query(User)
        .filter(
            User.role.in_(rules.DELEGATE_ROLES),
            User.is_active.is_(True),
            User.id != current_user.id,
        )
        .order_by(User.full_name, User.email)
        .all()
    )

    if current_user.role == UserRole.ADMIN.value:
        delegators = (
            db.query(User)
            .filter(User.role.in_(_DELEGATOR_ROLES), User.is_active.is_(True))
            .order_by(User.full_name, User.email)
            .all()
        )
    elif current_user.role in _DELEGATOR_ROLES:
        delegators = [current_user]
    else:
        delegators = []

    return DelegationOptions(
        delegates=[DelegationUserOption.model_validate(user) for user in delegates],
        delegators=[DelegationUserOption.model_validate(user) for user in delegators],
        max_days=rules.max_days(),
    )


@router.get("", response_model=list[DelegationResponse])
def list_delegations(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    query = db.query(ApprovalDelegation)
    if current_user.role != UserRole.ADMIN.value:
        query = query.filter(
            or_(
                ApprovalDelegation.delegator_id == current_user.id,
                ApprovalDelegation.delegate_id == current_user.id,
                ApprovalDelegation.created_by_id == current_user.id,
            )
        )
    return [_to_response(db, d) for d in query.order_by(ApprovalDelegation.start_at.desc()).all()]


@router.post("", response_model=DelegationResponse, status_code=201)
def create_delegation(
    payload: DelegationCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    if payload.delegator_id is not None and payload.delegator_id != current_user.id:
        if current_user.role != UserRole.ADMIN.value:
            raise HTTPException(
                status_code=403,
                detail="You can only delegate your own approval authority.",
            )
        delegator = db.query(User).filter(User.id == payload.delegator_id).first()
        if delegator is None:
            raise HTTPException(status_code=422, detail="The delegator does not exist.")
    else:
        delegator = current_user

    delegate = db.query(User).filter(User.id == payload.delegate_id).first()

    rules.validate_new_delegation(
        db,
        delegator=delegator,
        delegate=delegate,
        authority=payload.authority,
        scope_type=payload.scope_type,
        scope_assessment_id=payload.scope_assessment_id,
        start_at=payload.start_at,
        end_at=payload.end_at,
        reason=payload.reason,
    )

    delegation = ApprovalDelegation(
        delegator_id=delegator.id,
        delegate_id=delegate.id,
        authority=payload.authority,
        scope_type=payload.scope_type,
        scope_assessment_id=payload.scope_assessment_id,
        start_at=rules.as_utc(payload.start_at),
        end_at=rules.as_utc(payload.end_at),
        reason=payload.reason.strip(),
        created_by_id=current_user.id,
    )
    db.add(delegation)
    db.flush()

    log_audit_event(
        db=db,
        assessment_id=delegation.scope_assessment_id,
        action=AuditAction.DELEGATION_CREATED,
        actor=current_user.full_name or current_user.email,
        actor_id=current_user.id,
        details=_describe(db, delegation),
    )

    db.commit()
    db.refresh(delegation)
    return _to_response(db, delegation)


@router.post("/{delegation_id}/revoke", response_model=DelegationResponse)
def revoke_delegation(
    delegation_id: int,
    payload: DelegationRevoke,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    delegation = db.query(ApprovalDelegation).filter(ApprovalDelegation.id == delegation_id).first()
    if delegation is None:
        raise HTTPException(status_code=404, detail="Delegation not found.")

    if current_user.role != UserRole.ADMIN.value and current_user.id not in {
        delegation.delegator_id,
        delegation.created_by_id,
    }:
        raise HTTPException(
            status_code=403,
            detail="Only the delegator, whoever recorded the delegation, or an Admin can revoke it.",
        )

    if not (payload.reason or "").strip():
        raise HTTPException(status_code=422, detail="A reason is required to revoke a delegation.")

    state = rules.state_of(delegation)
    if state in {rules.STATE_REVOKED, rules.STATE_EXPIRED}:
        raise HTTPException(
            status_code=409,
            detail=f"This delegation is already {state.lower()} and no longer grants access.",
        )

    delegation.revoked_at = rules.now_utc()
    delegation.revoked_by_id = current_user.id
    delegation.revoke_reason = payload.reason.strip()

    log_audit_event(
        db=db,
        assessment_id=delegation.scope_assessment_id,
        action=AuditAction.DELEGATION_REVOKED,
        actor=current_user.full_name or current_user.email,
        actor_id=current_user.id,
        details=f"Delegation #{delegation.id} revoked. Reason: {delegation.revoke_reason}",
    )

    db.commit()
    db.refresh(delegation)
    return _to_response(db, delegation)

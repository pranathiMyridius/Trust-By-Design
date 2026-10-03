from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.api.assessments import _compute_evidence_gaps, require_pipeline_role
from app.auth.dependencies import get_current_user
from app.database import get_db
from app.models.action_item import (
    SOURCES_REQUIRING_CLOSURE_APPROVAL,
    ActionItem,
    ActionItemSourceType,
    ActionItemStatus,
)
from app.models.assessment import Assessment
from app.models.assessment_document import AssessmentDocument
from app.models.assessment_intelligence import AssessmentIntelligence
from app.models.committee_condition import CommitteeCondition
from app.models.control import ControlGap
from app.models.user import User, UserRole
from app.schemas.action_item import (
    ActionItemClosureDecision,
    ActionItemCreate,
    ActionItemRequestClosure,
    ActionItemResponse,
    ActionItemSyncResult,
    ActionItemUpdate,
)
from app.services.audit_service import AuditAction, log_audit_event
from app.services.escalation import escalate_overdue_action_items

router = APIRouter(prefix="/api/assessments", tags=["Action Items"])


def _get_assessment_or_404(db: Session, assessment_id: int) -> Assessment:
    assessment = db.query(Assessment).filter(Assessment.id == assessment_id).first()
    if not assessment:
        raise HTTPException(status_code=404, detail="Assessment not found")
    return assessment


def _get_action_item_or_404(db: Session, assessment_id: int, action_item_id: int) -> ActionItem:
    item = (
        db.query(ActionItem)
        .filter(ActionItem.id == action_item_id, ActionItem.assessment_id == assessment_id)
        .first()
    )
    if not item:
        raise HTTPException(status_code=404, detail="Action item not found")
    return item


@router.get("/{assessment_id}/action-items", response_model=list[ActionItemResponse])
def list_action_items(
    assessment_id: int,
    status: str = "all",
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """R13's "the assessment displays open and closed actions", plus a
    lazy escalation sweep (R13.3) so overdue items get flagged without a
    scheduler."""

    _get_assessment_or_404(db, assessment_id)

    escalate_overdue_action_items(db, assessment_id=assessment_id)

    query = db.query(ActionItem).filter(ActionItem.assessment_id == assessment_id)
    if status == "open":
        from app.models.action_item import OPEN_STATUSES

        query = query.filter(ActionItem.status.in_(OPEN_STATUSES))
    elif status == "closed":
        from app.models.action_item import CLOSED_STATUSES

        query = query.filter(ActionItem.status.in_(CLOSED_STATUSES))
    elif status != "all":
        raise HTTPException(status_code=422, detail="status must be one of: open, closed, all")

    return query.order_by(ActionItem.created_at.asc()).all()


@router.post("/{assessment_id}/action-items", response_model=ActionItemResponse, status_code=201)
def create_action_item(
    assessment_id: int,
    payload: ActionItemCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_pipeline_role),
):
    _get_assessment_or_404(db, assessment_id)

    item = ActionItem(
        assessment_id=assessment_id,
        source_type=payload.source_type,
        risk_factor_id=payload.risk_factor_id,
        control_id=payload.control_id,
        control_gap_id=payload.control_gap_id,
        source_reference=payload.source_reference,
        title=payload.title,
        description=payload.description,
        owner=payload.owner,
        department=payload.department,
        due_date=payload.due_date,
        priority=payload.priority,
        created_by=current_user.full_name or current_user.email,
    )
    db.add(item)

    log_audit_event(
        db,
        assessment_id=assessment_id,
        action=AuditAction.ACTION_ITEM_CREATED,
        actor=current_user.full_name or current_user.email,
        actor_id=current_user.id,
        details=f"{payload.source_type}: {payload.title}",
    )

    db.commit()
    db.refresh(item)
    return item


@router.patch("/{assessment_id}/action-items/{action_item_id}", response_model=ActionItemResponse)
def update_action_item(
    assessment_id: int,
    action_item_id: int,
    payload: ActionItemUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_pipeline_role),
):
    item = _get_action_item_or_404(db, assessment_id, action_item_id)

    # R13.4: ActionItemUpdate only accepts working statuses, so nothing can
    # be moved *into* a closure state here. Nor can an item leave one by a
    # direct edit: a pending closure is settled by the closure decision, and
    # a completed item stays completed.
    if payload.status is not None and item.status in {
        ActionItemStatus.PENDING_CLOSURE_APPROVAL.value,
        ActionItemStatus.COMPLETED.value,
    }:
        raise HTTPException(
            status_code=400,
            detail=(
                f"Action item is {item.status}; its status can only change "
                "through the closure-decision endpoint."
            ),
        )

    changes = payload.model_dump(exclude_unset=True)
    for field, value in changes.items():
        setattr(item, field, value)

    log_audit_event(
        db,
        assessment_id=assessment_id,
        action=AuditAction.ACTION_ITEM_UPDATED,
        actor=current_user.full_name or current_user.email,
        actor_id=current_user.id,
        details=f"Action item #{item.id} updated: {', '.join(changes.keys())}",
    )

    db.commit()
    db.refresh(item)
    return item


@router.post(
    "/{assessment_id}/action-items/{action_item_id}/request-closure",
    response_model=ActionItemResponse,
)
def request_action_item_closure(
    assessment_id: int,
    action_item_id: int,
    payload: ActionItemRequestClosure,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    R13.4: attaches the completion evidence. For sources that don't
    require reviewer sign-off, this immediately completes the action;
    for the higher-stakes sources (SOURCES_REQUIRING_CLOSURE_APPROVAL),
    it instead moves to PENDING_CLOSURE_APPROVAL for the closure-decision
    endpoint.
    """

    item = _get_action_item_or_404(db, assessment_id, action_item_id)

    if item.status in {ActionItemStatus.COMPLETED.value, ActionItemStatus.CANCELLED.value}:
        raise HTTPException(status_code=400, detail=f"Action item is already {item.status}.")

    actor_name = current_user.full_name or current_user.email
    item.completion_evidence = payload.completion_evidence
    item.closure_requested_by = actor_name
    item.closure_requested_by_id = current_user.id
    item.closure_requested_at = datetime.now(timezone.utc)
    item.closure_decision = None
    item.closure_decided_by = None
    item.closure_decided_at = None
    item.closure_decision_note = None

    if item.source_type in SOURCES_REQUIRING_CLOSURE_APPROVAL:
        item.status = ActionItemStatus.PENDING_CLOSURE_APPROVAL.value
        action = AuditAction.ACTION_ITEM_CLOSURE_REQUESTED
        details = f"Action item #{item.id} closure requested, awaiting reviewer approval."
    else:
        item.status = ActionItemStatus.COMPLETED.value
        item.completed_at = datetime.now(timezone.utc)
        action = AuditAction.ACTION_ITEM_CLOSURE_APPROVED
        details = f"Action item #{item.id} completed (no reviewer approval required for {item.source_type})."

    log_audit_event(
        db,
        assessment_id=assessment_id,
        action=action,
        actor=actor_name,
        actor_id=current_user.id,
        details=details,
    )

    db.commit()
    db.refresh(item)
    return item


@router.post(
    "/{assessment_id}/action-items/{action_item_id}/closure-decision",
    response_model=ActionItemResponse,
)
def decide_action_item_closure(
    assessment_id: int,
    action_item_id: int,
    payload: ActionItemClosureDecision,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """R13.4: an authorized reviewer approves or rejects a requested
    closure. Open to FCRM Analyst/Manager/Admin plus Committee Member,
    since committee-condition closures are one of the sources that land
    here. The reviewer can't be the person who requested the closure, and
    an approval needs completion evidence on the item."""

    if current_user.role not in {
        UserRole.FCRM_ANALYST.value,
        UserRole.MANAGER.value,
        UserRole.ADMIN.value,
        UserRole.COMMITTEE_MEMBER.value,
    }:
        raise HTTPException(status_code=403, detail="You are not authorized to decide action item closures.")

    item = _get_action_item_or_404(db, assessment_id, action_item_id)

    if item.status != ActionItemStatus.PENDING_CLOSURE_APPROVAL.value:
        raise HTTPException(
            status_code=400,
            detail="Action item does not have a closure awaiting a decision.",
        )

    if item.closure_requested_by_id == current_user.id:
        raise HTTPException(
            status_code=403,
            detail="You requested this closure, so another reviewer must decide it.",
        )

    if payload.decision == "approve" and not (item.completion_evidence or "").strip():
        raise HTTPException(
            status_code=400,
            detail="This action item has no completion evidence, so its closure can't be approved.",
        )

    actor_name = current_user.full_name or current_user.email
    item.closure_decision = payload.decision.upper()
    item.closure_decided_by = actor_name
    item.closure_decided_at = datetime.now(timezone.utc)
    item.closure_decision_note = payload.note

    if payload.decision == "approve":
        item.status = ActionItemStatus.COMPLETED.value
        item.completed_at = datetime.now(timezone.utc)
        action = AuditAction.ACTION_ITEM_CLOSURE_APPROVED

        # The approved closure is what completes a committee condition.
        if item.committee_condition_id:
            condition = db.get(CommitteeCondition, item.committee_condition_id)
            if condition and condition.status != "COMPLETED":
                condition.status = "COMPLETED"
                condition.completion_evidence = item.completion_evidence
                condition.completed_at = item.completed_at
    else:
        item.status = ActionItemStatus.CLOSURE_REJECTED.value
        action = AuditAction.ACTION_ITEM_CLOSURE_REJECTED

    log_audit_event(
        db,
        assessment_id=assessment_id,
        action=action,
        actor=actor_name,
        actor_id=current_user.id,
        details=payload.note or f"Closure {payload.decision}d for action item #{item.id}.",
    )

    db.commit()
    db.refresh(item)
    return item


@router.post("/{assessment_id}/action-items/sync", response_model=ActionItemSyncResult)
def sync_action_items(
    assessment_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_pipeline_role),
):
    """
    R13.1: creates action items for control gaps and committee
    conditions that don't have one yet (idempotent -- matches on the
    existing control_gap_id/committee_condition_id link). Missing
    evidence is computed live rather than persisted, so this also
    creates one action item per currently-open evidence issue, keyed by
    source_reference=issue.code so re-running doesn't duplicate it.

    Policy exceptions, vendor remediation, and monitoring enhancements
    have no detector in this system -- those are created directly via
    POST .../action-items with the matching source_type.
    """

    assessment = _get_assessment_or_404(db, assessment_id)
    actor_name = current_user.full_name or current_user.email
    created: list[ActionItem] = []

    existing = db.query(ActionItem).filter(ActionItem.assessment_id == assessment_id).all()
    existing_gap_ids = {item.control_gap_id for item in existing if item.control_gap_id}
    existing_condition_ids = {
        item.committee_condition_id for item in existing if item.committee_condition_id
    }
    existing_evidence_refs = {
        item.source_reference
        for item in existing
        if item.source_type == ActionItemSourceType.MISSING_EVIDENCE.value
    }

    for gap in (
        db.query(ControlGap)
        .filter(ControlGap.assessment_id == assessment_id, ControlGap.resolved.is_(False))
        .all()
    ):
        if gap.id in existing_gap_ids:
            continue
        item = ActionItem(
            assessment_id=assessment_id,
            source_type=ActionItemSourceType.CONTROL_GAP.value,
            risk_factor_id=gap.risk_factor_id,
            control_id=gap.control_id,
            control_gap_id=gap.id,
            title=f"Remediate control gap: {gap.gap_type.replace('_', ' ').title()}",
            description=gap.description,
            priority="HIGH" if gap.gap_type in {"NO_CONTROL", "INEFFECTIVE"} else "MEDIUM",
            created_by=actor_name,
        )
        db.add(item)
        created.append(item)

    for condition in (
        db.query(CommitteeCondition)
        .filter(CommitteeCondition.assessment_id == assessment_id)
        .all()
    ):
        if condition.id in existing_condition_ids:
            continue
        item = ActionItem(
            assessment_id=assessment_id,
            source_type=ActionItemSourceType.COMMITTEE_CONDITION.value,
            committee_condition_id=condition.id,
            title=condition.description[:255],
            description=condition.description,
            owner=condition.owner,
            due_date=condition.due_date,
            priority=condition.priority,
            created_by=actor_name,
        )
        db.add(item)
        created.append(item)

    intelligence = (
        db.query(AssessmentIntelligence)
        .filter(AssessmentIntelligence.assessment_id == assessment_id)
        .first()
    )
    documents = (
        db.query(AssessmentDocument)
        .filter(
            AssessmentDocument.assessment_id == assessment_id,
            AssessmentDocument.is_current.is_(True),
        )
        .all()
    )
    for issue in _compute_evidence_gaps(assessment, intelligence, documents):
        if not issue.missing or issue.code in existing_evidence_refs:
            continue
        item = ActionItem(
            assessment_id=assessment_id,
            source_type=ActionItemSourceType.MISSING_EVIDENCE.value,
            source_reference=issue.code,
            title=issue.title,
            description=issue.description,
            priority="MEDIUM",
            created_by=actor_name,
        )
        db.add(item)
        created.append(item)

    if created:
        for item in created:
            log_audit_event(
                db,
                assessment_id=assessment_id,
                action=AuditAction.ACTION_ITEM_CREATED,
                actor=actor_name,
                actor_id=current_user.id,
                details=f"{item.source_type}: {item.title} (auto-created by sync)",
            )
        db.commit()
        for item in created:
            db.refresh(item)

    return ActionItemSyncResult(created=len(created), items=created)

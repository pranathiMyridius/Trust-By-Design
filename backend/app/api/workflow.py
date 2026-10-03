"""
Stage 14: Workflow and Status Management endpoints.

Status changes that belong to an existing stage (advance-stage, manager /
committee decisions, information requests, ...) stay on their own
endpoints and call app/services/workflow.py from there. This router
adds the lifecycle views (per-assessment workflow summary + history,
the user's work queue, the role/transition configuration) and the
lifecycle-only actions that had no endpoint before: submitting a draft,
withdrawing, opening committee review, closing, assigning the current
task and setting the target date.
"""

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.auth.dependencies import get_current_user, require_role
from app.database import get_db
from app.models.assessment import Assessment
from app.models.user import User, UserRole
from app.models.workflow_transition import WorkflowTransition
from app.schemas.assessment import AssessmentResponse
from app.schemas.workflow import (
    ActionEscalationItem,
    AssignableUser,
    ReassessmentAlertItem,
    AssignRequest,
    OptionalReasonRequest,
    ReasonRequest,
    TargetDateRequest,
    WorkflowSummary,
    WorkflowTransitionResponse,
    WorkQueueItem,
    WorkQueueResponse,
)
from app.services import workflow
from app.services.audit_service import AuditAction, log_audit_event

router = APIRouter(prefix="/api", tags=["Workflow"])


def _actor(user: User) -> str:
    return user.full_name or user.email


def _get_visible_assessment(db: Session, assessment_id: int, user: User) -> Assessment:
    from app.api.assessments import _scope_assessments_for_user

    query = db.query(Assessment).filter(Assessment.id == assessment_id)
    if not query.first():
        raise HTTPException(status_code=404, detail="Assessment not found")
    assessment = _scope_assessments_for_user(query, user).first()
    if not assessment:
        raise HTTPException(status_code=403, detail="You do not have access to this assessment.")
    workflow.ensure_workflow_state(db, assessment)
    return assessment


def _history(db: Session, assessment_id: int) -> list[WorkflowTransition]:
    return (
        db.query(WorkflowTransition)
        .filter(WorkflowTransition.assessment_id == assessment_id)
        .order_by(WorkflowTransition.created_at.asc(), WorkflowTransition.id.asc())
        .all()
    )


# Lifecycle statuses where the committee gate is relevant enough to
# compute (and show) the mandatory-issue list.
_PRE_COMMITTEE_STATUSES = {
    workflow.WorkflowStatus.ANALYST_REVIEW.value,
    workflow.WorkflowStatus.CHALLENGE_REVIEW.value,
    workflow.WorkflowStatus.READY_FOR_COMMITTEE.value,
    workflow.WorkflowStatus.DEFERRED.value,
}


def _available_actions(db: Session, assessment: Assessment, user: User) -> list[str]:
    actions = []
    W = workflow.WorkflowStatus
    wf = assessment.workflow_status

    if wf == W.DRAFT.value and workflow.can_perform(user, assessment, "submit"):
        actions.append("submit")
    if assessment.status == "INTAKE" and workflow.can_perform(user, assessment, "withdraw"):
        actions.append("withdraw")
    if wf in {W.READY_FOR_COMMITTEE.value, W.DEFERRED.value} and workflow.can_perform(
        user, assessment, "approve"
    ):
        actions.append("open_committee_review")
    if wf in workflow.DECIDED_WORKFLOW_STATUSES and workflow.can_perform(user, assessment, "close"):
        actions.append("close")
    if workflow.can_perform(user, assessment, "assign"):
        actions.append("assign")
    if workflow.can_perform(user, assessment, "set_target_date"):
        actions.append("set_target_date")
    return actions


def _summary(db: Session, assessment: Assessment, user: User) -> WorkflowSummary:
    escalated_to = (
        db.query(User).filter(User.id == assessment.escalated_to_id).first()
        if assessment.escalated_to_id
        else None
    )
    issues = (
        workflow.get_mandatory_issues(db, assessment)
        if assessment.workflow_status in _PRE_COMMITTEE_STATUSES
        else []
    )
    return WorkflowSummary(
        assessment_id=assessment.id,
        reference_id=assessment.reference_id,
        title=assessment.title,
        status=assessment.status,
        workflow_status=assessment.workflow_status,
        workflow_status_label=workflow.label_for(assessment.workflow_status),
        owner=workflow.describe_owner(db, assessment),
        current_assignee_id=assessment.current_assignee_id,
        status_entered_at=assessment.status_entered_at,
        status_due_at=assessment.status_due_at,
        target_date=assessment.target_date,
        sla_state=workflow.compute_sla_state(assessment),
        sla_days=workflow.sla_days_for(assessment.workflow_status, assessment.priority),
        escalation_level=assessment.escalation_level or 0,
        escalated_at=assessment.escalated_at,
        escalated_to_id=assessment.escalated_to_id,
        escalated_to_name=_actor(escalated_to) if escalated_to else None,
        escalation_note=assessment.escalation_note,
        priority=assessment.priority,
        mandatory_issues=issues,
        available_transitions=workflow.available_transitions(db, assessment, user),
        available_actions=_available_actions(db, assessment, user),
        history=[WorkflowTransitionResponse.model_validate(t) for t in _history(db, assessment.id)],
    )


# ---------------------------------------------------------------------------
# Configuration (R14.1 / R14.2 / R14.4), for display and integration.
# ---------------------------------------------------------------------------


@router.get("/workflow/config")
def get_workflow_config(current_user: User = Depends(get_current_user)):
    return {
        "statuses": [
            {
                "value": status.value,
                "label": workflow.WORKFLOW_STATUS_LABELS[status],
                "sla_days": workflow.STATUS_SLA_DAYS.get(status.value),
            }
            for status in workflow.WorkflowStatus
        ],
        "transitions": {
            from_status: {to_status: sorted(roles) for to_status, roles in targets.items()}
            for from_status, targets in workflow.TRANSITIONS.items()
        },
        "permissions": workflow.ACTION_PERMISSIONS,
        "priority_sla_factor": workflow.PRIORITY_SLA_FACTOR,
        "target_days_by_priority": workflow.TARGET_DAYS_BY_PRIORITY,
    }


# ---------------------------------------------------------------------------
# Work queue (acceptance: "given an assigned task, the responsible user
# sees it in their work queue").
# ---------------------------------------------------------------------------


def _queue_item(db: Session, assessment: Assessment, reason: str) -> WorkQueueItem:
    return WorkQueueItem(
        assessment_id=assessment.id,
        reference_id=assessment.reference_id,
        title=assessment.title,
        change_type=assessment.change_type,
        status=assessment.status,
        workflow_status=assessment.workflow_status,
        workflow_status_label=workflow.label_for(assessment.workflow_status),
        priority=assessment.priority,
        risk_level=assessment.residual_risk_level or assessment.risk_level,
        owner=workflow.describe_owner(db, assessment),
        status_entered_at=assessment.status_entered_at,
        status_due_at=assessment.status_due_at,
        target_date=assessment.target_date,
        sla_state=workflow.compute_sla_state(assessment),
        escalation_level=assessment.escalation_level or 0,
        escalation_note=assessment.escalation_note,
        reason=reason,
    )


_SLA_ORDER = {"OVERDUE": 0, "AT_RISK": 1, "ON_TRACK": 2, "NONE": 3}


def _queue_sort_key(item: WorkQueueItem):
    due = workflow.as_utc(item.status_due_at)
    return (
        _SLA_ORDER.get(item.sla_state, 9),
        due.timestamp() if due else float("inf"),
    )


@router.get("/workflow/work-queue", response_model=WorkQueueResponse)
def get_work_queue(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    workflow.escalate_overdue_assessments(db)

    from app.models.audit_trail import soft_deleted_assessment_ids

    active = (
        db.query(Assessment)
        .filter(
            (Assessment.workflow_status.is_(None))
            | (Assessment.workflow_status.notin_(workflow.TERMINAL_WORKFLOW_STATUSES))
        )
        # R16.4: a soft-deleted assessment is no one's task.
        .filter(Assessment.id.notin_(soft_deleted_assessment_ids()))
        .all()
    )

    # AW.7: tasks the user is covering as a delegate appear too.
    from app.services.delegation import active_delegations_received

    delegations = active_delegations_received(db, current_user)

    tasks: list[WorkQueueItem] = []
    escalations: list[WorkQueueItem] = []
    for assessment in active:
        workflow.ensure_workflow_state(db, assessment)
        if workflow.is_responsible(current_user, assessment, delegations):
            tasks.append(_queue_item(db, assessment, "TASK"))
        if (assessment.escalation_level or 0) > 0 and (
            assessment.escalated_to_id == current_user.id
            or (
                current_user.role == UserRole.ADMIN.value
                and assessment.escalation_level >= 2
            )
        ):
            escalations.append(_queue_item(db, assessment, "ESCALATION"))

    db.commit()  # persist any lazily-initialised workflow state

    tasks.sort(key=_queue_sort_key)
    escalations.sort(key=_queue_sort_key)
    return WorkQueueResponse(
        tasks=tasks,
        escalations=escalations,
        overdue_count=sum(1 for item in tasks if item.sla_state == "OVERDUE"),
        at_risk_count=sum(1 for item in tasks if item.sla_state == "AT_RISK"),
        action_escalations=_action_escalations(db, current_user),
        reassessment_alerts=_reassessment_alerts(db, current_user),
    )


def _reassessment_alerts(db: Session, user: User) -> list[ReassessmentAlertItem]:
    """Stage 18 AC2: open EXPIRY / PERIODIC_REVIEW triggers on assessments
    the user owns or reviews (an Admin sees all). Runs the date check
    first, so the queue is current even between scheduled sweeps."""

    from app.models.audit_trail import soft_deleted_assessment_ids
    from app.models.reassessment_trigger import ReassessmentTrigger
    from app.services.reassessment_service import sweep_review_dates

    sweep_review_dates(db)

    from app.services import reassessment_lifecycle as lifecycle

    # P6: every open or acknowledged trigger (date-based and flagged), never
    # on a superseded approval (its successor is the one in force).
    query = (
        db.query(ReassessmentTrigger, Assessment)
        .join(Assessment, Assessment.id == ReassessmentTrigger.assessment_id)
        .filter(
            ReassessmentTrigger.status.in_(sorted(lifecycle.OPEN_TRIGGER_STATUSES)),
            Assessment.id.notin_(soft_deleted_assessment_ids()),
            (Assessment.reassessment_state.is_(None)) | (Assessment.reassessment_state != lifecycle.SUPERSEDED),
        )
    )
    if user.role == UserRole.FCRM_ANALYST.value:
        # The FCRM pipeline settles triggers: everything within the analyst's
        # visibility and entity scope (R15.4).
        from app.api.assessments import _scope_assessments_for_user

        query = _scope_assessments_for_user(query, user)
    elif user.role != UserRole.ADMIN.value:
        query = query.filter((Assessment.owner_id == user.id) | (Assessment.manager_id == user.id))

    items = []
    for trigger, assessment in query.order_by(Assessment.next_review_date.asc(), ReassessmentTrigger.detected_at.asc()).all():
        child = lifecycle.in_progress_child(db, assessment.id)
        items.append(
            ReassessmentAlertItem(
                trigger_id=trigger.id,
                assessment_id=assessment.id,
                reference_id=assessment.reference_id,
                assessment_title=assessment.title,
                trigger_type=trigger.trigger_type,
                description=trigger.description,
                next_review_date=assessment.next_review_date,
                detected_at=trigger.detected_at,
                trigger_status=trigger.status,
                reassessment_state=assessment.reassessment_state or "IN_FORCE",
                in_progress_reassessment_id=child.id if child else None,
                can_start_reassessment=lifecycle.propose_problem(db, user, assessment) is None,
                can_resolve=lifecycle.resolve_problem(user) is None,
            )
        )
    return items


def _action_escalations(db: Session, user: User) -> list[ActionEscalationItem]:
    """R13.3: escalated, still-open action items for this user -- on
    assessments they own or manage (the reviewer); an Admin sees all."""

    from app.models.action_item import OPEN_STATUSES, ActionItem
    from app.models.audit_trail import soft_deleted_assessment_ids
    from app.services.escalation import escalate_overdue_action_items

    escalate_overdue_action_items(db)

    query = (
        db.query(ActionItem, Assessment)
        .join(Assessment, Assessment.id == ActionItem.assessment_id)
        .filter(
            ActionItem.escalated.is_(True),
            ActionItem.status.in_(OPEN_STATUSES),
            Assessment.id.notin_(soft_deleted_assessment_ids()),
        )
    )
    if user.role != UserRole.ADMIN.value:
        query = query.filter((Assessment.owner_id == user.id) | (Assessment.manager_id == user.id))

    return [
        ActionEscalationItem(
            action_item_id=item.id,
            assessment_id=assessment.id,
            reference_id=assessment.reference_id,
            assessment_title=assessment.title,
            title=item.title,
            owner=item.owner,
            priority=item.priority,
            due_date=item.due_date,
            escalated_at=item.escalated_at,
            escalation_note=item.escalation_note,
        )
        for item, assessment in query.order_by(ActionItem.due_date.asc()).all()
    ]


@router.post("/workflow/escalations/run")
def run_escalations(
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role(UserRole.ADMIN, UserRole.MANAGER)),
):
    escalated = workflow.escalate_overdue_assessments(db)
    return {
        "escalated": [
            {"assessment_id": a.id, "level": a.escalation_level, "note": a.escalation_note}
            for a in escalated
        ]
    }


# ---------------------------------------------------------------------------
# Per-assessment workflow view (R14.3, R14.4, R14.5).
# ---------------------------------------------------------------------------


@router.get("/assessments/{assessment_id}/workflow", response_model=WorkflowSummary)
def get_assessment_workflow(
    assessment_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    assessment = _get_visible_assessment(db, assessment_id, current_user)
    workflow.escalate_overdue_assessments(db, assessment_id=assessment_id)
    summary = _summary(db, assessment, current_user)
    db.commit()
    return summary


@router.get(
    "/assessments/{assessment_id}/workflow/history",
    response_model=list[WorkflowTransitionResponse],
)
def get_assessment_workflow_history(
    assessment_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    _get_visible_assessment(db, assessment_id, current_user)
    return _history(db, assessment_id)


@router.get(
    "/assessments/{assessment_id}/workflow/assignable-users",
    response_model=list[AssignableUser],
)
def get_assignable_users(
    assessment_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    assessment = _get_visible_assessment(db, assessment_id, current_user)
    party = workflow.responsibility_for(assessment).party
    if party is None:
        return []
    if party == workflow.PARTY_OWNER:
        return db.query(User).filter(User.id == assessment.owner_id).all()
    if party == workflow.PARTY_MANAGER:
        return db.query(User).filter(User.id == assessment.manager_id).all()
    roles = workflow.PARTY_ELIGIBLE_ROLES[party]
    return (
        db.query(User)
        .filter(User.role.in_(roles), User.is_active.is_(True))
        .order_by(User.full_name, User.email)
        .all()
    )


# ---------------------------------------------------------------------------
# Lifecycle-only actions.
# ---------------------------------------------------------------------------


@router.post("/assessments/{assessment_id}/workflow/submit", response_model=AssessmentResponse)
def submit_draft(
    assessment_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Draft -> Submitted, after mandatory-field validation (R1.4)."""

    from app.api.assessments import (
        _apply_intake_triage_and_routing,
        get_missing_mandatory_fields,
    )
    from app.services.reference_id import generate_reference_id

    assessment = _get_visible_assessment(db, assessment_id, current_user)
    workflow.require_action(current_user, assessment, "submit")

    missing = get_missing_mandatory_fields(
        {column.name: getattr(assessment, column.name) for column in Assessment.__table__.columns}
    )
    if missing:
        raise HTTPException(
            status_code=422,
            detail={
                "message": "Missing mandatory information. Complete these fields before submitting.",
                "missing_fields": missing,
            },
        )

    from app.services import intake_history

    before_values = intake_history.request_values(assessment)
    assessment.is_draft = False
    assessment.submitted_by = _actor(current_user)
    assessment.submitted_at = datetime.now(timezone.utc)
    if not assessment.reference_id:
        assessment.reference_id = generate_reference_id(assessment.id)

    # P4 (R3.4): the request exactly as submitted.
    intake_history.record(
        db,
        assessment.id,
        intake_history.ASSESSMENT_REQUEST,
        after=intake_history.request_values(assessment),
        before=before_values,
        trigger="SUBMITTED",
        user=current_user,
        reason="Draft submitted.",
        changes=[{"field": "is_draft", "old": True, "new": False}],
    )

    log_audit_event(
        db,
        assessment_id=assessment.id,
        action=AuditAction.DRAFT_SUBMITTED,
        previous_status=assessment.status,
        new_status=assessment.status,
        actor=_actor(current_user),
        actor_id=current_user.id,
        details="Draft submitted.",
    )
    _apply_intake_triage_and_routing(db, assessment)
    workflow.sync_workflow_status(
        db, assessment, user=current_user, reason="Draft submitted for review.", action="SUBMIT"
    )

    db.commit()
    db.refresh(assessment)
    return assessment


@router.post("/assessments/{assessment_id}/workflow/withdraw", response_model=AssessmentResponse)
def withdraw_assessment(
    assessment_id: int,
    payload: ReasonRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    assessment = _get_visible_assessment(db, assessment_id, current_user)
    workflow.require_action(current_user, assessment, "withdraw")

    previous_status = assessment.status
    workflow.transition(
        db,
        assessment,
        workflow.STATUS_CLOSED,
        user=current_user,
        reason=f"Withdrawn: {payload.reason}",
        action="WITHDRAW",
    )
    log_audit_event(
        db,
        assessment_id=assessment.id,
        action=AuditAction.ASSESSMENT_WITHDRAWN,
        previous_status=previous_status,
        new_status=assessment.status,
        actor=_actor(current_user),
        actor_id=current_user.id,
        details=payload.reason,
    )
    db.commit()
    db.refresh(assessment)
    return assessment


@router.post(
    "/assessments/{assessment_id}/workflow/open-committee-review",
    response_model=AssessmentResponse,
)
def open_committee_review(
    assessment_id: int,
    payload: OptionalReasonRequest = OptionalReasonRequest(),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    from app.api.approvals import _open_committee_review

    assessment = _get_visible_assessment(db, assessment_id, current_user)
    if assessment.status == workflow.STATUS_COMMITTEE_REVIEW:
        raise HTTPException(status_code=409, detail="Committee review is already open.")

    _open_committee_review(
        db,
        assessment,
        current_user,
        (payload.reason or "").strip() or "Committee review opened.",
    )
    db.commit()
    db.refresh(assessment)
    return assessment


@router.post("/assessments/{assessment_id}/workflow/close", response_model=AssessmentResponse)
def close_assessment(
    assessment_id: int,
    payload: ReasonRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    assessment = _get_visible_assessment(db, assessment_id, current_user)
    workflow.require_action(current_user, assessment, "close")

    previous_status = assessment.status
    workflow.transition(
        db,
        assessment,
        workflow.STATUS_CLOSED,
        user=current_user,
        reason=payload.reason,
        action="CLOSE",
    )
    log_audit_event(
        db,
        assessment_id=assessment.id,
        action=AuditAction.ASSESSMENT_CLOSED,
        previous_status=previous_status,
        new_status=assessment.status,
        actor=_actor(current_user),
        actor_id=current_user.id,
        details=payload.reason,
    )
    db.commit()
    db.refresh(assessment)
    return assessment


@router.post("/assessments/{assessment_id}/workflow/assign", response_model=WorkflowSummary)
def assign_current_task(
    assessment_id: int,
    payload: AssignRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    assessment = _get_visible_assessment(db, assessment_id, current_user)
    workflow.require_action(current_user, assessment, "assign")

    party = workflow.responsibility_for(assessment).party
    assignee = None
    if payload.user_id is not None:
        assignee = db.query(User).filter(User.id == payload.user_id, User.is_active.is_(True)).first()
        if not assignee:
            raise HTTPException(status_code=404, detail="Assignee not found or inactive.")
        eligible = (
            (party == workflow.PARTY_OWNER and assignee.id == assessment.owner_id)
            or (party == workflow.PARTY_MANAGER and assignee.id == assessment.manager_id)
            or (
                party in {workflow.PARTY_ANALYST, workflow.PARTY_COMMITTEE}
                and assignee.role in workflow.PARTY_ELIGIBLE_ROLES[party]
            )
        )
        if not eligible:
            raise HTTPException(
                status_code=400,
                detail=(
                    f"{_actor(assignee)} ({assignee.role}) cannot take the current task "
                    f"({workflow.label_for(assessment.workflow_status)})."
                ),
            )

    assessment.current_assignee_id = assignee.id if assignee else None
    details = (
        f"Current task ({workflow.label_for(assessment.workflow_status)}) assigned to {_actor(assignee)}."
        if assignee
        else "Current task returned to the shared queue."
    )
    if (payload.reason or "").strip():
        details += f" Reason: {payload.reason.strip()}"

    log_audit_event(
        db,
        assessment_id=assessment.id,
        action=AuditAction.WORKFLOW_ASSIGNED,
        actor=_actor(current_user),
        actor_id=current_user.id,
        details=details,
    )
    db.commit()
    db.refresh(assessment)
    return _summary(db, assessment, current_user)


@router.patch("/assessments/{assessment_id}/workflow/target-date", response_model=WorkflowSummary)
def set_target_date(
    assessment_id: int,
    payload: TargetDateRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    assessment = _get_visible_assessment(db, assessment_id, current_user)
    workflow.require_action(current_user, assessment, "set_target_date")

    previous = assessment.target_date
    assessment.target_date = payload.target_date
    log_audit_event(
        db,
        assessment_id=assessment.id,
        action=AuditAction.WORKFLOW_TARGET_DATE_SET,
        actor=_actor(current_user),
        actor_id=current_user.id,
        details=(
            f"Target date changed from {previous.isoformat() if previous else 'none'} "
            f"to {payload.target_date.isoformat()}. Reason: {payload.reason}"
        ),
    )
    db.commit()
    db.refresh(assessment)
    return _summary(db, assessment, current_user)

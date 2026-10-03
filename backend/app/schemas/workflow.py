from datetime import date, datetime
from typing import Optional

from pydantic import BaseModel, ConfigDict, field_validator


class WorkflowTransitionResponse(BaseModel):
    id: int
    assessment_id: int
    from_status: Optional[str] = None
    to_status: str
    from_workflow_status: Optional[str] = None
    to_workflow_status: str
    action: str
    reason: str
    user_id: Optional[int] = None
    actor: str
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class AvailableTransition(BaseModel):
    to_status: str
    to_workflow_status: str
    to_workflow_label: str
    allowed_for_user: bool
    roles: list[str]


class WorkflowOwner(BaseModel):
    party: Optional[str] = None
    owner_name: str
    owner_user_id: Optional[int] = None
    team: str
    next_action: str


class WorkflowSummary(BaseModel):
    assessment_id: int
    reference_id: Optional[str] = None
    title: str
    status: str
    workflow_status: str
    workflow_status_label: str
    owner: WorkflowOwner
    current_assignee_id: Optional[int] = None
    status_entered_at: Optional[datetime] = None
    status_due_at: Optional[datetime] = None
    target_date: Optional[date] = None
    sla_state: str
    sla_days: Optional[int] = None
    escalation_level: int = 0
    escalated_at: Optional[datetime] = None
    escalated_to_id: Optional[int] = None
    escalated_to_name: Optional[str] = None
    escalation_note: Optional[str] = None
    priority: Optional[str] = None
    # Blocking the move to committee review (empty = clear to proceed).
    mandatory_issues: list[str] = []
    available_transitions: list[AvailableTransition] = []
    # Stage 14 lifecycle actions (api/workflow.py) the current user may
    # take right now: submit, withdraw, open_committee_review, close,
    # assign, set_target_date.
    available_actions: list[str] = []
    history: list[WorkflowTransitionResponse] = []


class WorkQueueItem(BaseModel):
    assessment_id: int
    reference_id: Optional[str] = None
    title: str
    change_type: str
    status: str
    workflow_status: str
    workflow_status_label: str
    priority: Optional[str] = None
    risk_level: Optional[str] = None
    owner: WorkflowOwner
    status_entered_at: Optional[datetime] = None
    status_due_at: Optional[datetime] = None
    target_date: Optional[date] = None
    sla_state: str
    escalation_level: int = 0
    escalation_note: Optional[str] = None
    # TASK = the user is responsible for the next action; ESCALATION =
    # it has been escalated to the user because it's overdue.
    reason: str


class ActionEscalationItem(BaseModel):
    """R13.3: an overdue action item escalated to this user (as the
    assessment's owner or its reviewing manager)."""

    action_item_id: int
    assessment_id: int
    reference_id: Optional[str] = None
    assessment_title: str
    title: str
    owner: Optional[str] = None
    priority: str
    due_date: Optional[date] = None
    escalated_at: Optional[datetime] = None
    escalation_note: Optional[str] = None


class ReassessmentAlertItem(BaseModel):
    """Stage 18 AC2: an approval that has expired or is due for its
    periodic review, for the assessment's owner and reviewer."""

    trigger_id: int
    assessment_id: int
    reference_id: Optional[str] = None
    assessment_title: str
    trigger_type: str
    description: str
    next_review_date: Optional[date] = None
    detected_at: Optional[datetime] = None
    # P6: where the approval stands and what this user may do (server-side).
    trigger_status: Optional[str] = None
    reassessment_state: Optional[str] = None
    in_progress_reassessment_id: Optional[int] = None
    can_start_reassessment: bool = False
    can_resolve: bool = False


class WorkQueueResponse(BaseModel):
    tasks: list[WorkQueueItem]
    escalations: list[WorkQueueItem]
    overdue_count: int
    at_risk_count: int
    action_escalations: list[ActionEscalationItem] = []
    reassessment_alerts: list[ReassessmentAlertItem] = []


class ReasonRequest(BaseModel):
    reason: str

    @field_validator("reason")
    @classmethod
    def reason_must_not_be_blank(cls, value: str) -> str:
        if not value or not value.strip():
            raise ValueError("A reason or comment is required.")
        return value.strip()


class OptionalReasonRequest(BaseModel):
    reason: Optional[str] = None


class AssignRequest(BaseModel):
    # Null un-assigns (returns the task to the role's shared queue).
    user_id: Optional[int] = None
    reason: Optional[str] = None


class TargetDateRequest(BaseModel):
    target_date: date
    reason: str

    @field_validator("reason")
    @classmethod
    def reason_must_not_be_blank(cls, value: str) -> str:
        if not value or not value.strip():
            raise ValueError("A reason is required when changing the target date.")
        return value.strip()


class AssignableUser(BaseModel):
    id: int
    email: str
    full_name: Optional[str] = None
    role: str

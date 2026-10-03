from datetime import date, datetime
from typing import Optional

from pydantic import BaseModel, ConfigDict, field_validator

from app.models.action_item import ActionItemSourceType, ActionItemStatus

VALID_SOURCE_TYPES = {t.value for t in ActionItemSourceType}
VALID_PRIORITIES = {"LOW", "MEDIUM", "HIGH", "CRITICAL"}
DIRECTLY_SETTABLE_STATUSES = {
    ActionItemStatus.OPEN.value,
    ActionItemStatus.IN_PROGRESS.value,
    ActionItemStatus.CANCELLED.value,
}


class ActionItemCreate(BaseModel):
    source_type: str
    title: str
    description: Optional[str] = None
    risk_factor_id: Optional[int] = None
    control_id: Optional[int] = None
    control_gap_id: Optional[int] = None
    source_reference: Optional[str] = None
    owner: Optional[str] = None
    department: Optional[str] = None
    due_date: Optional[date] = None
    priority: str = "MEDIUM"

    @field_validator("source_type")
    @classmethod
    def source_type_must_be_known(cls, value: str) -> str:
        if value not in VALID_SOURCE_TYPES:
            raise ValueError(f"source_type must be one of: {', '.join(sorted(VALID_SOURCE_TYPES))}")
        return value

    @field_validator("title")
    @classmethod
    def title_must_not_be_blank(cls, value: str) -> str:
        if not value or not value.strip():
            raise ValueError("title is required.")
        return value

    @field_validator("priority")
    @classmethod
    def priority_must_be_known(cls, value: str) -> str:
        if value not in VALID_PRIORITIES:
            raise ValueError(f"priority must be one of: {', '.join(sorted(VALID_PRIORITIES))}")
        return value


class ActionItemUpdate(BaseModel):
    title: Optional[str] = None
    description: Optional[str] = None
    owner: Optional[str] = None
    department: Optional[str] = None
    due_date: Optional[date] = None
    priority: Optional[str] = None
    # Direct status changes are limited to working states. The closure
    # states (PENDING_CLOSURE_APPROVAL, COMPLETED, CLOSURE_REJECTED) are
    # only reachable through request-closure / closure-decision, so the
    # evidence and reviewer checks there can't be bypassed (R13.4).
    status: Optional[str] = None

    @field_validator("status")
    @classmethod
    def status_must_be_directly_settable(cls, value: Optional[str]) -> Optional[str]:
        if value is not None and value not in DIRECTLY_SETTABLE_STATUSES:
            raise ValueError(
                f"status must be one of: {', '.join(sorted(DIRECTLY_SETTABLE_STATUSES))}. "
                "Closing an action item goes through request-closure (and, where "
                "required, the closure-decision endpoint)."
            )
        return value

    @field_validator("priority")
    @classmethod
    def priority_must_be_known(cls, value: Optional[str]) -> Optional[str]:
        if value is not None and value not in VALID_PRIORITIES:
            raise ValueError(f"priority must be one of: {', '.join(sorted(VALID_PRIORITIES))}")
        return value


class ActionItemRequestClosure(BaseModel):
    # R13.4: incomplete evidence -> the action cannot be marked complete.
    completion_evidence: str

    @field_validator("completion_evidence")
    @classmethod
    def evidence_must_not_be_blank(cls, value: str) -> str:
        if not value or not value.strip():
            raise ValueError("completion_evidence is required to close an action.")
        return value


class ActionItemClosureDecision(BaseModel):
    decision: str
    note: Optional[str] = None

    @field_validator("decision")
    @classmethod
    def decision_must_be_known(cls, value: str) -> str:
        if value not in {"approve", "reject"}:
            raise ValueError("decision must be one of: approve, reject")
        return value


class ActionItemResponse(BaseModel):
    id: int
    assessment_id: int
    source_type: str
    risk_factor_id: Optional[int] = None
    control_id: Optional[int] = None
    control_gap_id: Optional[int] = None
    committee_condition_id: Optional[int] = None
    source_reference: Optional[str] = None
    title: str
    description: Optional[str] = None
    owner: Optional[str] = None
    department: Optional[str] = None
    due_date: Optional[date] = None
    priority: str
    status: str
    completion_evidence: Optional[str] = None
    escalated: bool
    escalated_at: Optional[datetime] = None
    escalation_note: Optional[str] = None
    closure_requested_by: Optional[str] = None
    closure_requested_at: Optional[datetime] = None
    closure_decision: Optional[str] = None
    closure_decided_by: Optional[str] = None
    closure_decided_at: Optional[datetime] = None
    closure_decision_note: Optional[str] = None
    created_by: Optional[str] = None
    created_at: datetime
    updated_at: datetime
    completed_at: Optional[datetime] = None

    model_config = ConfigDict(from_attributes=True)


class ActionItemSyncResult(BaseModel):
    created: int
    items: list[ActionItemResponse]

from datetime import date, datetime
from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, field_validator, model_validator

COMMITTEE_CONDITION_PRIORITIES = ["LOW", "MEDIUM", "HIGH", "CRITICAL"]
COMMITTEE_CONDITION_STATUSES = ["OPEN", "IN_PROGRESS", "COMPLETED", "CANCELLED"]
COMMITTEE_VOTE_VALUES = ["APPROVE", "DISSENT", "ABSTAIN"]


class CommitteeConditionCreate(BaseModel):
    """R12.4: one structured condition of an 'Approve with Conditions' decision."""

    description: str
    owner: str
    due_date: date
    priority: str = "MEDIUM"

    @field_validator("description", "owner")
    @classmethod
    def not_blank(cls, value: str) -> str:
        if not value or not value.strip():
            raise ValueError("This field is required.")
        return value

    @field_validator("priority")
    @classmethod
    def priority_must_be_known(cls, value: str) -> str:
        if value not in COMMITTEE_CONDITION_PRIORITIES:
            raise ValueError(f"priority must be one of: {', '.join(COMMITTEE_CONDITION_PRIORITIES)}")
        return value


class CommitteeConditionUpdate(BaseModel):
    status: Optional[str] = None
    completion_evidence: Optional[str] = None
    owner: Optional[str] = None
    due_date: Optional[date] = None
    priority: Optional[str] = None

    @field_validator("status")
    @classmethod
    def status_must_be_known(cls, value: Optional[str]) -> Optional[str]:
        if value is not None and value not in COMMITTEE_CONDITION_STATUSES:
            raise ValueError(f"status must be one of: {', '.join(COMMITTEE_CONDITION_STATUSES)}")
        return value

    @field_validator("priority")
    @classmethod
    def priority_must_be_known(cls, value: Optional[str]) -> Optional[str]:
        if value is not None and value not in COMMITTEE_CONDITION_PRIORITIES:
            raise ValueError(f"priority must be one of: {', '.join(COMMITTEE_CONDITION_PRIORITIES)}")
        return value


class CommitteeConditionResponse(BaseModel):
    id: int
    assessment_id: int
    description: str
    owner: str
    due_date: date
    priority: str
    status: str
    completion_evidence: Optional[str] = None
    created_by: Optional[str] = None
    created_at: datetime
    updated_at: datetime
    completed_at: Optional[datetime] = None

    model_config = ConfigDict(from_attributes=True)


class CommitteeDecisionRequestV2(BaseModel):
    """
    R12.1/R12.3/R12.4: the committee's final, binding decision. Replaces
    the free-text `conditions` string with structured conditions --
    still accepted as `conditions` (a plain-text summary) for whatever
    still reads that column, but `structured_conditions` is what's
    actually validated/persisted as CommitteeCondition rows.
    """

    decision: str
    rationale: str
    conditions: Optional[str] = None
    structured_conditions: list[CommitteeConditionCreate] = []

    @field_validator("decision")
    @classmethod
    def decision_must_be_known(cls, value: str) -> str:
        if value not in {"approve", "approve_with_conditions", "defer", "reject"}:
            raise ValueError(
                "decision must be one of: approve, approve_with_conditions, defer, reject"
            )
        return value

    @field_validator("rationale")
    @classmethod
    def rationale_must_not_be_blank(cls, value: str) -> str:
        # R12.3: every decision requires a written rationale -- including
        # "reject", where this doubles as the mandatory rejection reason.
        if not value or not value.strip():
            raise ValueError("A decision rationale is required.")
        return value

    @model_validator(mode="after")
    def conditions_required_for_conditional_approval(self):
        # R12.4/AC3: at least one condition, with an owner and due date,
        # is required for "Approve with Conditions" -- CommitteeConditionCreate
        # itself already enforces owner/due_date are present per condition.
        if self.decision == "approve_with_conditions" and not self.structured_conditions:
            raise ValueError(
                "At least one condition (with an owner and due date) is "
                "required when approving with conditions."
            )
        return self


class CommitteeVoteCreate(BaseModel):
    """R12.6: an individual committee member's recorded position."""

    vote: str
    comment: Optional[str] = None
    # R12.6: required when this seat has already voted -- the earlier vote
    # is kept, and the record says why it changed.
    recast_reason: Optional[str] = None

    @field_validator("vote")
    @classmethod
    def vote_must_be_known(cls, value: str) -> str:
        if value not in COMMITTEE_VOTE_VALUES:
            raise ValueError(f"vote must be one of: {', '.join(COMMITTEE_VOTE_VALUES)}")
        return value


class CommitteeVoteResponse(BaseModel):
    id: int
    assessment_id: int
    member_id: int
    member_name: Optional[str] = None
    # AW.7: set when a delegate cast this vote for member_id.
    delegate_id: Optional[int] = None
    delegate_name: Optional[str] = None
    delegation_id: Optional[int] = None
    vote: str
    comment: Optional[str] = None
    voted_at: datetime
    # R12.6 history.
    version: int = 1
    is_current: bool = True
    superseded_at: Optional[datetime] = None
    superseded_by_id: Optional[int] = None
    cast_by_id: Optional[int] = None
    recast_reason: Optional[str] = None

    model_config = ConfigDict(from_attributes=True)


class AssessmentAmendmentRequest(BaseModel):
    """
    Acceptance criterion: "a final decision makes the assessment
    read-only except through a controlled amendment process." This is
    that process -- reopens a finally-decided assessment back into
    REMEDIATION (the existing rework loop-back status) with a mandatory
    reason, rather than allowing silent edits to a decided record.
    """

    reason: str
    requested_by: str = "System"

    @field_validator("reason")
    @classmethod
    def reason_required(cls, value: str) -> str:
        if not value or not value.strip():
            raise ValueError("A reason is required to open a controlled amendment.")
        return value


class DecisionPackageResponse(BaseModel):
    """
    R12.2: everything the committee needs to see in one call. Reuses the
    Stage 9 AssessmentDraft (executive summary, inherent/residual risk,
    main risk drivers via risk_statements, control gaps, evidence,
    analyst recommendation already live there) and adds what that draft
    doesn't carry: challenge findings, committee conditions/votes, open
    issues, and assessment history.
    """

    assessment_id: int
    draft: Optional[dict[str, Any]] = None
    challenge_findings: list[dict[str, Any]] = []
    committee_conditions: list[CommitteeConditionResponse] = []
    committee_votes: list[CommitteeVoteResponse] = []
    open_issues: list[str] = []
    assessment_history: list[dict[str, Any]] = []
    # R12.2 "main risk drivers": the highest-scoring rated factors, live
    # from the current risk factors (not the possibly older draft).
    main_risk_drivers: list[dict[str, Any]] = []
    # R10.4 / R6.7: every calculated value with a human value beside it --
    # calculated, human, difference, reason, who, when, review status --
    # and whether the human value is the one the decision relies on.
    # Calculated values are never altered (app/services/override_ledger.py).
    value_comparisons: list[dict[str, Any]] = []
    # R11: the current mandatory challenge-review sign-off, if recorded.
    challenge_signoff: Optional[dict[str, Any]] = None

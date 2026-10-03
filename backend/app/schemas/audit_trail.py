from datetime import datetime
from typing import Optional

from pydantic import BaseModel, ConfigDict, field_validator, model_validator


class RetentionPolicyResponse(BaseModel):
    """Legacy shape, read through to the ACTIVE ASSESSMENT version (P5)."""

    id: int
    name: str
    is_active: bool
    default_retention_days: int
    created_at: datetime
    updated_at: datetime
    record_type: str = "ASSESSMENT"
    active_version_id: Optional[int] = None
    active_version: Optional[int] = None
    effective_from: Optional[datetime] = None
    pending_version_id: Optional[int] = None
    pending_retention_days: Optional[int] = None
    policy_status: str

    model_config = ConfigDict(from_attributes=True)


class RetentionPolicyUpdate(BaseModel):
    """Legacy PATCH: now creates a proposal (never edits in place)."""

    name: Optional[str] = None
    default_retention_days: Optional[int] = None
    change_reason: Optional[str] = None

    @field_validator("default_retention_days")
    @classmethod
    def must_be_positive(cls, value: Optional[int]) -> Optional[int]:
        if value is not None and value <= 0:
            raise ValueError("default_retention_days must be a positive number of days.")
        return value


class RetentionProposal(BaseModel):
    record_type: str = "ASSESSMENT"
    retention_days: int
    change_reason: str


class RetentionDecision(BaseModel):
    decision: str
    reason: str

    @field_validator("decision")
    @classmethod
    def known(cls, value: str) -> str:
        if value not in {"APPROVE", "REJECT"}:
            raise ValueError("decision must be APPROVE or REJECT")
        return value

    @field_validator("reason")
    @classmethod
    def required(cls, value: str) -> str:
        if not value or not value.strip():
            raise ValueError("A reason is required for the decision.")
        return value.strip()


class AssessmentRetentionResponse(BaseModel):
    assessment_id: int
    legal_hold: bool
    legal_hold_reason: Optional[str] = None
    legal_hold_set_by: Optional[str] = None
    legal_hold_set_at: Optional[datetime] = None
    is_deleted: bool
    deleted_by: Optional[str] = None
    deleted_at: Optional[datetime] = None
    deletion_reason: Optional[str] = None
    # Computed, not stored: when this assessment becomes eligible for
    # soft-deletion under the active retention policy. Null if the
    # assessment has no final decision yet (retention hasn't started).
    eligible_for_deletion_at: Optional[datetime] = None
    retention_expired: bool
    # P5: the shared eligibility result (app/governance/retention.py), the
    # hold history and what the signed-in user may do.
    eligibility: dict
    policy_version_id: Optional[int] = None
    policy_version: Optional[int] = None
    retention_days: Optional[int] = None
    retention_policy_version_id: Optional[int] = None
    hold_detail_visible: bool
    hold_history: list[dict]
    actions: dict
    policy_status: str


class LegalHoldUpdate(BaseModel):
    hold: bool
    reason: Optional[str] = None
    matter_reference: Optional[str] = None

    @model_validator(mode="after")
    def reason_required(self):
        if not (self.reason or "").strip():
            raise ValueError(
                "A reason is required to place a legal hold." if self.hold else "A reason is required to release a legal hold."
            )
        self.reason = self.reason.strip()
        if self.matter_reference is not None:
            self.matter_reference = self.matter_reference.strip() or None
            if self.matter_reference and len(self.matter_reference) > 100:
                raise ValueError("matter_reference is at most 100 characters (a reference, not a document).")
        return self


class SoftDeleteRequest(BaseModel):
    reason: str

    @field_validator("reason")
    @classmethod
    def reason_required(cls, value: str) -> str:
        if not value or not value.strip():
            raise ValueError("A reason is required to delete an assessment's record.")
        return value

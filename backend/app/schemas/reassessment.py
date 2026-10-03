from datetime import datetime
from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, field_validator

from app.models.reassessment_trigger import (
    REASSESSMENT_TRIGGER_STATUSES,
    REASSESSMENT_TRIGGER_TYPES,
)


class ReassessmentTriggerResponse(BaseModel):
    id: int
    assessment_id: int
    trigger_type: str
    description: str
    detected_at: datetime
    detected_by: Optional[str] = None
    status: str
    dismissed_reason: Optional[str] = None
    resolved_by: Optional[str] = None
    resolved_at: Optional[datetime] = None
    reassessment_id: Optional[int] = None
    detected_by_id: Optional[int] = None
    resolved_by_id: Optional[int] = None
    resolution_note: Optional[str] = None

    model_config = ConfigDict(from_attributes=True)


class ManualTriggerCreate(BaseModel):
    """
    R18.1: flagging REGULATORY_POLICY_CHANGE or SIGNIFICANT_CONTROL_FAILURE
    (or any other trigger type) by hand, since those two have no field to
    diff against the parent assessment.
    """

    trigger_type: str
    description: str
    flagged_by: str = "System"

    @field_validator("trigger_type")
    @classmethod
    def trigger_type_must_be_known(cls, value: str) -> str:
        if value not in REASSESSMENT_TRIGGER_TYPES:
            raise ValueError(f"trigger_type must be one of: {', '.join(REASSESSMENT_TRIGGER_TYPES)}")
        return value

    @field_validator("description")
    @classmethod
    def description_required(cls, value: str) -> str:
        if not value or not value.strip():
            raise ValueError("A description is required to flag a reassessment trigger.")
        return value


class TriggerResolution(BaseModel):
    status: str
    dismissed_reason: Optional[str] = None
    resolution_note: Optional[str] = None
    resolved_by: str = "System"

    @field_validator("status")
    @classmethod
    def status_must_be_known(cls, value: str) -> str:
        if value not in REASSESSMENT_TRIGGER_STATUSES:
            raise ValueError(f"status must be one of: {', '.join(REASSESSMENT_TRIGGER_STATUSES)}")
        return value


# R1.1 intake fields a proposed change may update. Kept as a plain dict
# (rather than importing AssessmentRequestFields) so every field stays
# optional and "not supplied" is unambiguous from "supplied as blank".
class ProposedChangeFields(BaseModel):
    product_or_service_name: Optional[str] = None
    business_owner: Optional[str] = None
    legal_entity: Optional[str] = None
    customer_segment: Optional[str] = None
    countries_jurisdictions: Optional[str] = None
    delivery_channels: Optional[str] = None
    expected_transaction_volume: Optional[str] = None
    expected_transaction_value: Optional[str] = None
    transaction_types: Optional[str] = None
    third_party_vendor_usage: Optional[str] = None
    technology_process_changes: Optional[str] = None


class ProposeChangeRequest(BaseModel):
    """
    R18.1/R18.4: an owner (or analyst) proposing a change to an approved
    assessment. The system diffs the supplied fields against the current
    (parent) assessment, detects which R18.1 triggers fire, and -- since
    R18.4 says the previous approval cannot automatically remain valid --
    always opens a new reassessment (a fresh, fully re-piped Assessment
    linked via parent_assessment_id) rather than editing the approved
    record in place.
    """

    changes: ProposedChangeFields
    reason: str
    proposed_by: str = "System"

    @field_validator("reason")
    @classmethod
    def reason_required(cls, value: str) -> str:
        if not value or not value.strip():
            raise ValueError("A reason for the proposed change is required.")
        return value


class ProposeChangeResponse(BaseModel):
    reassessment_id: int
    triggers: list[ReassessmentTriggerResponse]


class ReassessmentComparisonResponse(BaseModel):
    """R18.2/AC4: old vs. new risks, controls, scores, and conditions."""

    parent_assessment_id: int
    reassessment_id: int
    fields_changed: list[dict[str, Any]]
    reused_field_sources: dict[str, Any]
    parent: dict[str, Any]
    reassessment: dict[str, Any]
    # P6 (R18.2, R18.3): the structured comparison and the reused fields
    # with their source and age.
    structured: dict[str, Any] = {}
    reused_fields: list[dict[str, Any]] = []

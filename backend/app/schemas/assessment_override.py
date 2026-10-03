from datetime import datetime
from typing import Optional

from pydantic import BaseModel, ConfigDict, field_validator

from app.governance.policy import POLICY_STATUS

# R10.2: the categories of AI-generated value an analyst can change.
OVERRIDE_SECTIONS = {
    "INTAKE_FIELD",
    "RISK_CATEGORY",
    "FACTOR_RATING",
    "RISK_RATIONALE",
    "CONTROL_MAPPING",
    "CONTROL_EFFECTIVENESS",
    "RESIDUAL_RISK",
    "CONDITION",
}


class OverrideCreate(BaseModel):
    section: str
    field_name: str
    entity_id: Optional[str] = None
    # Ignored: the system value is read from the record on the server
    # (app/services/override_ledger.py). Accepted so older clients that
    # still send it keep working.
    ai_value: Optional[str] = None
    human_value: str
    reason: str

    @field_validator("section")
    @classmethod
    def section_must_be_known(cls, value: str) -> str:
        if value not in OVERRIDE_SECTIONS:
            raise ValueError(
                f"section must be one of: {', '.join(sorted(OVERRIDE_SECTIONS))}"
            )
        return value

    @field_validator("field_name")
    @classmethod
    def field_name_must_not_be_blank(cls, value: str) -> str:
        if not value or not value.strip():
            raise ValueError("field_name is required.")
        return value

    @field_validator("human_value")
    @classmethod
    def human_value_must_not_be_blank(cls, value: str) -> str:
        if not value or not value.strip():
            raise ValueError("human_value is required.")
        return value

    @field_validator("reason")
    @classmethod
    def reason_must_not_be_blank(cls, value: str) -> str:
        if not value or not value.strip():
            raise ValueError("A reason is required for every override (R10.3).")
        return value


class OverrideResponse(BaseModel):
    id: int
    assessment_id: int
    section: str
    field_name: str
    entity_id: Optional[str] = None
    ai_value: Optional[str] = None
    human_value: str
    reason: str
    overridden_by: Optional[str] = None
    overridden_by_id: Optional[int] = None
    created_at: datetime
    # SYSTEM: ai_value was read from the record. None: a legacy row whose
    # ai_value came from the client.
    ai_value_source: Optional[str] = None
    # APPLIED | PROPOSED | CONFIRMED | REJECTED; None for legacy rows.
    review_status: Optional[str] = None
    reviewed_by: Optional[str] = None
    reviewed_by_id: Optional[int] = None
    reviewed_at: Optional[datetime] = None
    review_note: Optional[str] = None
    # P3: TYPED | PROPOSAL; materiality classified by the server; the
    # material-override approval; where the entry stands; and what the
    # requesting user may do with it (computed server-side).
    origin: Optional[str] = None
    materiality: Optional[str] = None
    materiality_reasons: list[str] = []
    approval_status: Optional[str] = None
    approved_by: Optional[str] = None
    approved_by_id: Optional[int] = None
    approved_at: Optional[datetime] = None
    approval_rationale: Optional[str] = None
    state: Optional[str] = None
    actions: dict = {}
    # The P3 review/approval rules that decide `state` and `actions` are
    # provisional (app/governance/policy.py); never set from a request.
    policy_status: str = POLICY_STATUS

    model_config = ConfigDict(from_attributes=True)

    @field_validator("materiality_reasons", mode="before")
    @classmethod
    def reasons_from_json(cls, value):
        if isinstance(value, str):
            import json

            try:
                value = json.loads(value)
            except ValueError:
                return []
        return value or []


class OverrideApproval(BaseModel):
    """P3: the material-override approval, after a confirming review."""

    decision: str
    rationale: str

    @field_validator("decision")
    @classmethod
    def decision_must_be_known(cls, value: str) -> str:
        if value not in {"APPROVE", "REJECT"}:
            raise ValueError("decision must be APPROVE or REJECT")
        return value

    @field_validator("rationale")
    @classmethod
    def rationale_required(cls, value: str) -> str:
        if not value or not value.strip():
            raise ValueError("An approval rationale is required.")
        return value.strip()


class OverrideReview(BaseModel):
    """An independent reviewer's decision on a PROPOSED override."""

    decision: str
    note: str

    @field_validator("decision")
    @classmethod
    def decision_must_be_known(cls, value: str) -> str:
        if value not in {"CONFIRM", "REJECT"}:
            raise ValueError("decision must be CONFIRM or REJECT")
        return value

    @field_validator("note")
    @classmethod
    def note_required(cls, value: str) -> str:
        if not value or not value.strip():
            raise ValueError("A review note is required.")
        return value.strip()

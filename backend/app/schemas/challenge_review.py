from datetime import datetime
from typing import Optional

from pydantic import BaseModel, ConfigDict, field_validator

RESOLUTION_STATUSES = ["OPEN", "RESOLVED", "ACCEPTED"]
SEVERITIES = ["LOW", "MEDIUM", "HIGH", "CRITICAL"]


class ChallengeTriggerConfigResponse(BaseModel):
    id: int
    name: str
    is_active: bool
    trigger_risk_levels: list[str]
    residual_risk_tolerance: float
    rating_mismatch_enabled: bool
    low_confidence_enabled: bool
    high_risk_jurisdictions: list[str]
    high_risk_technologies: list[str]
    # R11.1: switched-off triggers, plus what can and can't be switched off.
    disabled_triggers: list[str] = []
    configurable_triggers: list[str] = []
    mandatory_triggers: list[str] = []
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class ChallengeTriggerConfigUpdate(BaseModel):
    """R11.1: authorized users configure the challenge-trigger conditions."""

    name: Optional[str] = None
    trigger_risk_levels: Optional[list[str]] = None
    residual_risk_tolerance: Optional[float] = None
    rating_mismatch_enabled: Optional[bool] = None
    low_confidence_enabled: Optional[bool] = None
    high_risk_jurisdictions: Optional[list[str]] = None
    high_risk_technologies: Optional[list[str]] = None
    disabled_triggers: Optional[list[str]] = None

    @field_validator("disabled_triggers")
    @classmethod
    def only_configurable_triggers(cls, value: Optional[list[str]]) -> Optional[list[str]]:
        from app.challenge_engine.rules import CONFIGURABLE_TRIGGERS

        if value is None:
            return value
        unknown = sorted(set(value) - CONFIGURABLE_TRIGGERS)
        if unknown:
            raise ValueError(
                f"These triggers can't be disabled: {', '.join(unknown)}. "
                f"Configurable triggers: {', '.join(sorted(CONFIGURABLE_TRIGGERS))}."
            )
        return sorted(set(value))


class TriggerResult(BaseModel):
    name: str
    label: str
    fired: bool
    enabled: bool = True


class ChallengeFindingResponse(BaseModel):
    id: int
    assessment_id: int
    category: str
    description: str
    related_section: str
    severity: str
    supporting_evidence: Optional[str] = None
    recommended_action: Optional[str] = None
    resolution_status: str
    resolved_by: Optional[str] = None
    resolved_at: Optional[datetime] = None
    resolution_note: Optional[str] = None
    accepted_by: Optional[str] = None
    accepted_at: Optional[datetime] = None
    accepted_reason: Optional[str] = None
    # COMMITTEE for a documented Committee exception; None for an
    # acceptance recorded under the earlier rule (it no longer counts).
    acceptance_authority: Optional[str] = None
    detected_at: datetime

    model_config = ConfigDict(from_attributes=True)


class ChallengeReviewResponse(BaseModel):
    assessment_id: int
    triggered: bool
    triggers: list[TriggerResult]
    findings: list[ChallengeFindingResponse]
    high_severity_open_count: int


class ChallengeFindingResolve(BaseModel):
    resolution_note: str
    resolved_by: Optional[str] = None

    @field_validator("resolution_note")
    @classmethod
    def note_required(cls, value: str) -> str:
        if not value or not value.strip():
            raise ValueError("A resolution note is required to resolve a finding.")
        return value


class ChallengeFindingAccept(BaseModel):
    """R11.6/AC5: accepting rather than fixing a finding still requires a
    reason, and records who accepted it and when."""

    reason: str
    accepted_by: Optional[str] = None

    @field_validator("reason")
    @classmethod
    def reason_required(cls, value: str) -> str:
        if not value or not value.strip():
            raise ValueError("A reason is required to accept a challenge finding.")
        return value

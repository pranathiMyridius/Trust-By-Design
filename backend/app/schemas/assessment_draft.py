from datetime import datetime
from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, field_validator


class RiskStatement(BaseModel):
    category: str
    statement: str


class AssessmentDraftResponse(BaseModel):
    """R9.1: the full structured, decision-ready assessment draft."""

    id: int
    assessment_id: int

    executive_summary: str
    business_change_description: str
    business_profile: dict[str, Any]
    applicable_risk_categories: list[str]
    risk_indicators: list[str]
    risk_statements: list[RiskStatement]
    inherent_risk: dict[str, Any]
    evidence_references: list[dict[str, Any]]
    mapped_controls: list[dict[str, Any]]
    control_effectiveness: dict[str, Any]
    residual_risk: dict[str, Any]
    risk_gaps: list[dict[str, Any]]
    assumptions: list[str]
    missing_information: list[str]
    recommended_conditions: list[str]
    analyst_recommendation: str
    # Stage 19 (Explainability): the recommendation is always advisory,
    # never a decision -- the UI shows this notice alongside it.
    recommendation_status: str = "ADVISORY"
    recommendation_notice: str = ""
    required_approvals: list[str]

    # R9.2
    uncertainty: dict[str, Any]

    # R9.4
    generated_at: datetime
    generation_method: str
    model_version: Optional[str] = None
    config_version: Optional[str] = None
    source_evidence: list[dict[str, Any]]
    generated_by: Optional[str] = None

    # R9.3
    is_edited: bool
    edited_by: Optional[str] = None
    edited_at: Optional[datetime] = None
    accepted_by: Optional[str] = None
    accepted_at: Optional[datetime] = None

    version: int
    is_current: bool
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class AssessmentDraftVersionSummary(BaseModel):
    """A lightweight entry for the version-history list (R9.3 retention)."""

    id: int
    version: int
    is_current: bool
    generated_at: datetime
    is_edited: bool
    edited_by: Optional[str] = None
    edited_at: Optional[datetime] = None
    accepted_by: Optional[str] = None
    accepted_at: Optional[datetime] = None

    model_config = ConfigDict(from_attributes=True)


class AssessmentDraftUpdate(BaseModel):
    """
    R9.3: an authorized analyst's edit. Only the narrative/judgment
    sections are editable -- the structured, already-computed sections
    (business_profile, inherent_risk, mapped_controls,
    control_effectiveness, residual_risk, risk_gaps, evidence_references,
    applicable_risk_categories, risk_indicators) reflect frozen upstream
    calculations and are not free-text editable here; excluding/
    re-rating a factor or re-assessing a control (which DO change those)
    already have their own dedicated endpoints and trigger a fresh
    regeneration instead.
    """

    executive_summary: Optional[str] = None
    business_change_description: Optional[str] = None
    risk_statements: Optional[list[RiskStatement]] = None
    assumptions: Optional[list[str]] = None
    missing_information: Optional[list[str]] = None
    recommended_conditions: Optional[list[str]] = None
    analyst_recommendation: Optional[str] = None
    required_approvals: Optional[list[str]] = None
    edited_by: str = "System"

    @field_validator("edited_by")
    @classmethod
    def edited_by_required(cls, value: str) -> str:
        if not value or not value.strip():
            raise ValueError("edited_by is required to record who modified the draft.")
        return value


class AssessmentDraftAccept(BaseModel):
    accepted_by: str = "System"

    @field_validator("accepted_by")
    @classmethod
    def accepted_by_required(cls, value: str) -> str:
        if not value or not value.strip():
            raise ValueError("accepted_by is required.")
        return value

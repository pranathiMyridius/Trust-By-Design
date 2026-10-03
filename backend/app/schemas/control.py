from datetime import date, datetime
from typing import Optional

from pydantic import BaseModel, ConfigDict, field_validator, model_validator

from app.control_engine.library import CONTROL_LIBRARY

DESIGN_ADEQUACY_VALUES = ["ADEQUATE", "INADEQUATE", "NOT_ASSESSED"]
OPERATING_EFFECTIVENESS_VALUES = [
    "EFFECTIVE",
    "PARTIALLY_EFFECTIVE",
    "INEFFECTIVE",
    "UNVERIFIED",
]
CONTROL_CONDITION_STATUSES = ["OPEN", "IN_PROGRESS", "COMPLETED", "CANCELLED"]


class ControlCreate(BaseModel):
    """R7.1/R7.2/R7.3: map a control from the library to an identified risk."""

    risk_factor_id: int
    control_type: str
    description: Optional[str] = None
    owner: Optional[str] = None
    performing_department: Optional[str] = None
    frequency: Optional[str] = None
    trigger: Optional[str] = None
    scope: Optional[str] = None
    evidence_source: Optional[str] = None
    operating_status: str = "ACTIVE"
    added_by: Optional[str] = "System"

    @field_validator("control_type")
    @classmethod
    def control_type_must_be_known(cls, value: str) -> str:
        if value not in CONTROL_LIBRARY:
            raise ValueError(
                f"control_type must be one of: {', '.join(CONTROL_LIBRARY)}"
            )
        return value


class ControlUpdate(BaseModel):
    """R7.2/R7.3/R10.2: edit a control's metadata, or remap it to another
    risk (risk_factor_id) -- recorded as a new version with the previous
    configuration kept (ControlRevision). A reason is required."""

    risk_factor_id: Optional[int] = None
    control_type: Optional[str] = None
    description: Optional[str] = None
    owner: Optional[str] = None
    performing_department: Optional[str] = None
    frequency: Optional[str] = None
    trigger: Optional[str] = None
    scope: Optional[str] = None
    evidence_source: Optional[str] = None
    operating_status: Optional[str] = None
    reason: str

    @field_validator("control_type")
    @classmethod
    def control_type_must_be_known(cls, value: Optional[str]) -> Optional[str]:
        if value is not None and value not in CONTROL_LIBRARY:
            raise ValueError(f"control_type must be one of: {', '.join(CONTROL_LIBRARY)}")
        return value

    @field_validator("reason")
    @classmethod
    def reason_required(cls, value: str) -> str:
        if not value or not value.strip():
            raise ValueError("A reason is required for every control change.")
        return value.strip()


class ControlUnmap(BaseModel):
    """R7.2/R10.2: remove a control from the assessment. Kept on record."""

    reason: str

    @field_validator("reason")
    @classmethod
    def reason_required(cls, value: str) -> str:
        if not value or not value.strip():
            raise ValueError("A reason is required to unmap a control.")
        return value.strip()


class ControlRevisionResponse(BaseModel):
    id: int
    control_id: int
    assessment_id: int
    change_type: str
    version: int
    previous_config: dict
    new_config: Optional[dict] = None
    changed_fields: list[str]
    reason: str
    changed_by: str
    changed_by_id: int
    changed_at: datetime


class ControlResponse(BaseModel):
    id: int
    assessment_id: int
    risk_factor_id: int
    control_type: str
    description: Optional[str] = None
    owner: Optional[str] = None
    performing_department: Optional[str] = None
    frequency: Optional[str] = None
    trigger: Optional[str] = None
    scope: Optional[str] = None
    evidence_source: Optional[str] = None
    operating_status: str
    added_by: Optional[str] = None
    version: int
    is_current: bool
    superseded_at: Optional[datetime] = None
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class ControlAssessmentCreate(BaseModel):
    """R7.4/R7.5/R7.6: assess a control's design adequacy and operating
    effectiveness."""

    design_adequacy: str = "NOT_ASSESSED"
    design_rationale: Optional[str] = None
    operating_effectiveness: str = "UNVERIFIED"
    effectiveness_rationale: Optional[str] = None
    has_evidence: bool = False
    coverage_complete: bool = True
    depends_on_unavailable_data: bool = False
    assessed_by: Optional[str] = "System"

    @field_validator("design_adequacy")
    @classmethod
    def design_adequacy_must_be_known(cls, value: str) -> str:
        if value not in DESIGN_ADEQUACY_VALUES:
            raise ValueError(
                f"design_adequacy must be one of: {', '.join(DESIGN_ADEQUACY_VALUES)}"
            )
        return value

    @field_validator("operating_effectiveness")
    @classmethod
    def effectiveness_must_be_known(cls, value: str) -> str:
        if value not in OPERATING_EFFECTIVENESS_VALUES:
            raise ValueError(
                "operating_effectiveness must be one of: "
                f"{', '.join(OPERATING_EFFECTIVENESS_VALUES)}"
            )
        return value

    @model_validator(mode="after")
    def effective_requires_evidence(self):
        # R7.6/AC3: a control with no supporting evidence can never be
        # recorded as EFFECTIVE -- enforced here, not just left to the UI.
        if self.operating_effectiveness == "EFFECTIVE" and not self.has_evidence:
            raise ValueError(
                "operating_effectiveness cannot be EFFECTIVE without "
                "has_evidence=true; it must be UNVERIFIED."
            )
        return self


class ControlAssessmentResponse(BaseModel):
    id: int
    control_id: int
    assessment_id: int
    design_adequacy: str
    design_rationale: Optional[str] = None
    operating_effectiveness: str
    effectiveness_rationale: Optional[str] = None
    has_evidence: bool
    coverage_complete: bool
    depends_on_unavailable_data: bool
    assessed_by: Optional[str] = None
    assessed_at: datetime
    version: int
    is_current: bool

    model_config = ConfigDict(from_attributes=True)


class ControlConditionCreate(BaseModel):
    """R7.7: a required control enhancement/condition."""

    description: str
    due_date: Optional[date] = None
    owner: Optional[str] = None
    created_by: Optional[str] = "System"

    @field_validator("description")
    @classmethod
    def description_required(cls, value: str) -> str:
        if not value or not value.strip():
            raise ValueError("A description is required for a control condition.")
        return value


class ControlConditionUpdate(BaseModel):
    status: Optional[str] = None
    description: Optional[str] = None
    due_date: Optional[date] = None
    owner: Optional[str] = None

    @field_validator("status")
    @classmethod
    def status_must_be_known(cls, value: Optional[str]) -> Optional[str]:
        if value is not None and value not in CONTROL_CONDITION_STATUSES:
            raise ValueError(
                f"status must be one of: {', '.join(CONTROL_CONDITION_STATUSES)}"
            )
        return value


class ControlConditionResponse(BaseModel):
    id: int
    control_id: int
    assessment_id: int
    description: str
    status: str
    due_date: Optional[date] = None
    owner: Optional[str] = None
    created_by: Optional[str] = None
    created_at: datetime
    updated_at: datetime
    completed_at: Optional[datetime] = None

    model_config = ConfigDict(from_attributes=True)


class ControlGapResponse(BaseModel):
    id: int
    assessment_id: int
    risk_factor_id: Optional[int] = None
    control_id: Optional[int] = None
    gap_type: str
    description: Optional[str] = None
    detected_at: datetime
    resolved: bool
    resolved_at: Optional[datetime] = None

    model_config = ConfigDict(from_attributes=True)


class AssessmentControlSummary(BaseModel):
    """R7.2/R7.6 rollup: every control mapped on this assessment, its
    current assessment (if any), open gaps, and open conditions -- what
    the Controls stage UI and the acceptance criteria actually need in one
    call."""

    assessment_id: int
    controls: list[ControlResponse]
    control_assessments: dict[int, ControlAssessmentResponse]  # keyed by control_id
    gaps: list[ControlGapResponse]
    conditions: list[ControlConditionResponse]
    control_reduction: float
    risk_factor_count: int

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, ConfigDict, field_validator

from app.models.assessment_comment import CommentVisibility

VALID_VISIBILITIES = {v.value for v in CommentVisibility}

# R10.1: the sections of the assessment a comment can be linked to.
COMMENT_SECTIONS = {
    "INTAKE",
    "EVIDENCE",
    "RISK_FACTORS",
    "SCORES",
    "RISK_RATIONALE",
    "CONTROL_MAPPINGS",
    "RESIDUAL_RISK",
    "CONDITIONS",
    "MISSING_INFORMATION",
}


class CommentCreate(BaseModel):
    body: str
    visibility: str = CommentVisibility.ALL.value
    section: Optional[str] = None
    related_entity_id: Optional[str] = None

    @field_validator("body")
    @classmethod
    def body_must_not_be_blank(cls, value: str) -> str:
        if not value or not value.strip():
            raise ValueError("Comment body must not be blank.")
        return value

    @field_validator("visibility")
    @classmethod
    def visibility_must_be_known(cls, value: str) -> str:
        if value not in VALID_VISIBILITIES:
            raise ValueError(f"visibility must be one of: {', '.join(sorted(VALID_VISIBILITIES))}")
        return value

    @field_validator("section")
    @classmethod
    def section_must_be_known(cls, value: Optional[str]) -> Optional[str]:
        if value is not None and value not in COMMENT_SECTIONS:
            raise ValueError(f"section must be one of: {', '.join(sorted(COMMENT_SECTIONS))}")
        return value


class CommentResolve(BaseModel):
    resolved: bool = True
    # R10 acceptance criteria: set only when an authorized user is
    # explicitly accepting the exception of leaving this comment
    # unresolved, rather than genuinely resolving it.
    exception_reason: Optional[str] = None


class CommentResponse(BaseModel):
    id: int
    assessment_id: int
    author_id: int
    author_name: Optional[str] = None
    author_role: Optional[str] = None
    body: str
    visibility: str
    section: Optional[str] = None
    related_entity_id: Optional[str] = None
    resolved: bool = False
    resolved_by: Optional[str] = None
    resolved_at: Optional[datetime] = None
    exception_reason: Optional[str] = None
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator


class AuditEventResponse(BaseModel):
    id: int
    # None for standalone Risk Calculator activity that isn't tied to
    # any one assessment.
    assessment_id: int | None = None
    action: str
    previous_status: str | None = None
    new_status: str | None = None
    details: str | None = None
    actor: str | None = None
    actor_id: int | None = None
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class CalculatorAuditCreate(BaseModel):
    """
    Body for POST /api/assessments/audit/calculator — a debounced
    snapshot logged by the Manual Scoring Calculator (either the
    standalone Risk Calculator page, or the panel embedded on an
    assessment's detail page) a couple of seconds after the user
    stops editing.
    """

    # Set when the calculator is embedded on a specific assessment's
    # detail page; left None for the standalone Risk Calculator page.
    assessment_id: int | None = None
    weighted_total: float
    risk_level: str
    # Dimension -> score, for every factor currently toggled "included".
    included_factors: dict[str, float]
    # Dimension -> weight actually used (0-1), which may have been
    # manually overridden from the engine's default. Optional so older
    # clients without weight overrides still validate.
    factor_weights: dict[str, float] | None = None
    actor: str | None = None


class ManualScoreOverrideCreate(BaseModel):
    """
    Body for PATCH /api/assessments/{assessment_id}/manual-score —
    records the Manual Scoring Calculator's current total as an R6.7
    override of the calculated inherent risk. A reason is mandatory; the
    band is derived server-side from the configured methodology and the
    user from the session, so `risk_level` and `actor` are accepted for
    older clients but ignored.
    """

    weighted_total: float = Field(ge=0, le=100)
    reason: str
    included_factors: dict[str, float]
    factor_weights: dict[str, float] | None = None
    risk_level: str | None = None
    actor: str | None = None

    @field_validator("reason")
    @classmethod
    def reason_required(cls, value: str) -> str:
        if not value or not value.strip():
            raise ValueError("A reason is required to override the calculated inherent risk.")
        return value.strip()

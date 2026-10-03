from datetime import datetime
from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, field_validator


class RiskFactorRatingUpdate(BaseModel):
    """
    Stage 6 (R6.2, R6.3): an analyst's manual likelihood/impact rating for
    one risk factor. This is the ONLY input to that factor's numeric
    score -- the score itself is computed deterministically by
    app/risk_engine/scoring.py, never supplied directly or by an LLM.
    """

    likelihood: int
    impact: int
    # R10.3: required when the rating differs from the AI's suggestion
    # (enforced in the endpoint, which knows the suggestion).
    reason: Optional[str] = None
    # Ignored: the rater is the signed-in user.
    rated_by: Optional[str] = None

    @field_validator("likelihood", "impact")
    @classmethod
    def must_be_positive(cls, value: int) -> int:
        if value < 1:
            raise ValueError("likelihood/impact must be at least 1.")
        return value


class InherentRiskFactorBreakdown(BaseModel):
    category: str
    weight: float
    likelihood: Optional[int] = None
    impact: Optional[int] = None
    score: float
    rated: bool
    evidence_status: Optional[str] = None


class InherentRiskCalculationResponse(BaseModel):
    """R6.5: full calculation transparency -- inputs, weights, method, thresholds, final score, band."""

    id: int
    assessment_id: int
    methodology_id: Optional[int] = None
    methodology_name: Optional[str] = None
    calculation_method: str
    inputs: list[InherentRiskFactorBreakdown]
    weights: dict[str, float]
    thresholds: dict[str, float] = {}
    risk_bands: list[dict[str, Any]]
    escalation_rules: list[dict[str, Any]] = []
    # None while applicable factors exist but none has been rated: there
    # is no score yet, and 0 would read as low risk. risk_band is then
    # "UNRATED" unless a policy rule has already set one.
    final_score: Optional[float] = None
    risk_band: str
    is_provisional: bool
    rated_factor_count: int = 0
    applicable_factor_count: int = 0
    escalated: bool
    escalation_reasons: list[str] = []
    triggered_rules: list[dict[str, Any]] = []
    mandatory_review: bool = False
    methodology_version: Optional[str] = None
    methodology_fingerprint: Optional[str] = None
    # {snapshots_used, snapshots_skipped, unresolved_countries}
    reference_data: dict[str, Any] = {}
    jurisdiction_matches: list[dict[str, Any]] = []
    calculated_by: Optional[str] = None
    calculated_at: datetime
    overridden: bool
    calculated_score: Optional[float] = None
    calculated_band: Optional[str] = None
    override_value: Optional[float] = None
    override_band: Optional[str] = None
    override_reason: Optional[str] = None
    override_by: Optional[str] = None
    override_at: Optional[datetime] = None
    version: int
    is_current: bool

    model_config = ConfigDict(from_attributes=True)


class InherentRiskOverrideCreate(BaseModel):
    """
    R6.7: an authorized analyst overriding the calculated inherent-risk
    rating. A reason is mandatory -- the calculated value, user and
    timestamp are captured automatically, never supplied by the caller.
    """

    override_value: float
    override_band: Optional[str] = None
    reason: str
    actor: Optional[str] = "System"

    @field_validator("reason")
    @classmethod
    def reason_required(cls, value: str) -> str:
        if not value or not value.strip():
            raise ValueError("A reason is required to override the calculated inherent risk.")
        return value

    @field_validator("override_value")
    @classmethod
    def value_in_range(cls, value: float) -> float:
        if value < 0 or value > 100:
            raise ValueError("override_value must be between 0 and 100.")
        return value

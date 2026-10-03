from datetime import datetime
from typing import Optional

from pydantic import BaseModel, ConfigDict, field_validator


class RiskMethodologyResponse(BaseModel):
    id: int
    name: str
    is_active: bool
    weights: dict[str, float]
    thresholds: dict[str, float]
    created_at: datetime
    updated_at: datetime
    # Versioning and governance. A locked methodology has produced results
    # and can only be cloned; activation records the approval.
    version: int = 1
    parent_id: Optional[int] = None
    change_reason: Optional[str] = None
    locked: bool = False
    locked_at: Optional[datetime] = None
    approved_by: Optional[str] = None
    approved_at: Optional[datetime] = None
    approval_reason: Optional[str] = None
    effective_from: Optional[datetime] = None
    retired_at: Optional[datetime] = None
    fingerprint: Optional[str] = None

    model_config = ConfigDict(from_attributes=True)


class RiskMethodologyCreate(BaseModel):
    name: str
    weights: dict[str, float]
    thresholds: dict[str, float]
    # New methodologies are inactive by default; activate explicitly via
    # PATCH /{id}/activate so exactly one active methodology is intentional.
    is_active: bool = False

    @field_validator("weights")
    @classmethod
    def weights_must_sum_to_one(
        cls, value: dict[str, float]
    ) -> dict[str, float]:
        if not value:
            raise ValueError("At least one dimension weight is required.")
        total = sum(value.values())
        if not (0.99 <= total <= 1.01):
            raise ValueError(
                f"Dimension weights must sum to 1.0 (got {total})."
            )
        return value


class RiskMethodologyUpdate(BaseModel):
    name: Optional[str] = None
    weights: Optional[dict[str, float]] = None
    thresholds: Optional[dict[str, float]] = None

    @field_validator("weights")
    @classmethod
    def weights_must_sum_to_one(
        cls, value: Optional[dict[str, float]]
    ) -> Optional[dict[str, float]]:
        if value is None:
            return value
        total = sum(value.values())
        if not (0.99 <= total <= 1.01):
            raise ValueError(
                f"Dimension weights must sum to 1.0 (got {total})."
            )
        return value

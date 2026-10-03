import json
from datetime import datetime, timezone

from sqlalchemy import Boolean, Column, DateTime, Float, ForeignKey, Integer, String, Text

from app.database import Base


class ResidualRiskCalculation(Base):
    """
    One versioned snapshot of a residual-risk lookup: the inherent band,
    the control-effectiveness rating (and the per-risk ratings behind
    it), the residual grid exactly as applied, and any non-mitigable
    minimum levels that held the result up. Same supersede-don't-overwrite
    pattern as InherentRiskCalculation.

    residual_band is the result. residual_score is only an indicative
    position inside that band (see scoring.residual_position_score).
    """

    __tablename__ = "residual_risk_calculations"

    id = Column(Integer, primary_key=True, index=True)
    assessment_id = Column(Integer, ForeignKey("assessments.id"), nullable=False, index=True)

    inherent_calculation_id = Column(Integer, nullable=True)
    methodology_id = Column(Integer, nullable=True)
    methodology_version = Column(String(30), nullable=True)
    methodology_fingerprint = Column(String(80), nullable=True)

    inherent_band = Column(String(30), nullable=True)
    inherent_score = Column(Float, nullable=True)

    # WEAK | PARTIAL | EFFECTIVE -- the weakest per-risk rating.
    control_rating = Column(String(20), nullable=True)
    # JSON list[{risk_factor_id, category, control_count, rating}]
    control_ratings = Column(Text, nullable=True)
    control_reduction = Column(Float, nullable=True)

    # JSON snapshot of the grid applied.
    residual_grid = Column(Text, nullable=True)
    grid_version = Column(String(30), nullable=True)
    grid_band = Column(String(30), nullable=True)
    # JSON list[{rule_code, band_before, band_after}] of non-mitigable floors.
    floors_applied = Column(Text, nullable=True)

    residual_band = Column(String(30), nullable=True)
    residual_score = Column(Float, nullable=True)

    # R8: the human-confirmed residual risk, kept beside the calculated
    # one so the difference is visible. Set on the calculation of record.
    confirmed_band = Column(String(30), nullable=True)
    confirmed_score = Column(Float, nullable=True)
    confirmation_reason = Column(Text, nullable=True)
    confirmed_by = Column(String(255), nullable=True)
    confirmed_by_id = Column(Integer, nullable=True)
    confirmed_at = Column(DateTime, nullable=True)
    # Why there is no residual_band, when there isn't one.
    reason = Column(Text, nullable=True)

    # True for the calculation frozen on arrival at RESIDUAL_RISK -- the
    # value of record for review and committee. False for previews.
    frozen = Column(Boolean, nullable=False, default=False)

    calculated_by = Column(String(255), nullable=True)
    calculated_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), nullable=False)

    version = Column(Integer, nullable=False, default=1)
    is_current = Column(Boolean, nullable=False, default=True)
    superseded_at = Column(DateTime, nullable=True)

    def _json(self, raw: str | None, default):
        try:
            return json.loads(raw) if raw else default
        except (TypeError, ValueError):
            return default

    def get_control_ratings(self) -> list:
        return self._json(self.control_ratings, [])

    def get_residual_grid(self) -> dict:
        return self._json(self.residual_grid, {})

    def get_floors_applied(self) -> list:
        return self._json(self.floors_applied, [])

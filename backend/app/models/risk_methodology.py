from datetime import datetime, timezone

from sqlalchemy import Boolean, Column, DateTime, Integer, String, Text

from app.database import Base


class RiskMethodology(Base):
    """
    Configurable risk-scoring methodology: per-dimension weights (must sum
    to 1.0) used to combine AI-assessed dimension scores into an overall
    score, and the score thresholds used to translate that overall score
    into a risk level (LOW/MEDIUM/HIGH/CRITICAL).

    Exactly one row should have is_active=True at a time; the risk engine
    reads that row instead of hardcoded constants, so the methodology can
    be changed without a code deploy. If no active row exists, the risk
    engine falls back to the built-in defaults in app/risk_engine/scoring.py.
    """

    __tablename__ = "risk_methodologies"

    id = Column(Integer, primary_key=True, index=True)

    name = Column(String(255), nullable=False)

    is_active = Column(
        Boolean,
        nullable=False,
        default=False,
    )

    # JSON text: {"CUSTOMER": 0.15, "OPERATIONAL": 0.20, ...}
    weights = Column(Text, nullable=False)

    # JSON text: {"CRITICAL": 80, "HIGH": 60, "MEDIUM": 40} — a score at or
    # above a threshold gets that level; below all thresholds is LOW.
    thresholds = Column(Text, nullable=False)

    # --- Stage 6 (R6.1): configurable inherent-risk methodology ---
    # All nullable so existing rows keep working -- app/risk_engine/scoring.py
    # falls back to its DEFAULT_* constants for any column left unset.

    # JSON text: {"PRODUCT_SERVICE_RISK": 0.10, ...} — per risk-factor
    # category weight used by calculate_inherent_risk (R6.1, R6.4).
    factor_weights = Column(Text, nullable=True)

    # JSON text: [{"value": 1, "label": "Rare"}, ...]
    likelihood_scale = Column(Text, nullable=True)

    # JSON text: [{"value": 1, "label": "Negligible"}, ...]
    impact_scale = Column(Text, nullable=True)

    # JSON text: [{"name": "LOW", "min": 0, "max": 39}, ...]
    risk_bands = Column(Text, nullable=True)

    # JSON text: [{"id": ..., "description": ..., "indicator"|"category":
    # ..., "min_score": ..., "min_band": "CRITICAL"}, ...] (R6.6).
    escalation_rules = Column(Text, nullable=True)

    # JSON text: {"LOW": ["ANALYST"], "CRITICAL": ["COMMITTEE"], ...}
    required_approvals = Column(Text, nullable=True)

    # JSON text: ["CONTROL_ENVIRONMENT_RISK"] -- categories assessed as
    # mitigants, outside the inherent average (see scoring.py).
    mitigant_categories = Column(Text, nullable=True)

    # JSON text: {"version": "1.0", "cells": {"HIGH": {"WEAK": "HIGH", ...}}}
    # -- the residual lookup grid (inherent band x control rating).
    residual_grid = Column(Text, nullable=True)

    # --- Versioning and governance ---
    # A methodology is immutable once any calculation has used it
    # (locked_at set): its results must stay reproducible. Changes are
    # made on a clone (parent_id -> this row, version + 1), which is
    # editable until it is activated and used in turn.
    version = Column(Integer, nullable=False, default=1)
    parent_id = Column(Integer, nullable=True)
    change_reason = Column(Text, nullable=True)
    # When the first calculation used this row; edits are refused after.
    locked_at = Column(DateTime, nullable=True)
    # Activation is the approval: who approved it, why, and from when.
    approved_by = Column(String(255), nullable=True)
    approved_at = Column(DateTime, nullable=True)
    approval_reason = Column(Text, nullable=True)
    effective_from = Column(DateTime, nullable=True)
    retired_at = Column(DateTime, nullable=True)

    created_at = Column(
        DateTime,
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )

    updated_at = Column(
        DateTime,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
        nullable=False,
    )

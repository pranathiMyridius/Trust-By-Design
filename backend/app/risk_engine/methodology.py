import json
from typing import Any

from sqlalchemy.orm import Session

from app.models.risk_methodology import RiskMethodology
from app.risk_engine.scoring import (
    DEFAULT_ESCALATION_RULES,
    DEFAULT_FACTOR_WEIGHTS,
    DEFAULT_IMPACT_SCALE,
    DEFAULT_LIKELIHOOD_SCALE,
    DEFAULT_MITIGANT_CATEGORIES,
    DEFAULT_REQUIRED_APPROVALS,
    DEFAULT_RESIDUAL_GRID,
    DEFAULT_RISK_BANDS,
    DEFAULT_RISK_THRESHOLDS,
    methodology_fingerprint,
)


def _load_json(raw: str | None, default: Any) -> Any:
    if not raw:
        return default

    try:
        return json.loads(raw)
    except (TypeError, ValueError):
        return default


def get_methodology_config(db: Session) -> dict[str, Any]:
    """
    Stage 6 (R6.1): the single place the deterministic inherent-risk
    calculation reads its full configuration from -- factor weights,
    likelihood/impact scales, risk bands, mandatory escalation rules and
    required approvals -- falling back to the built-in defaults in
    app/risk_engine/scoring.py for anything not configured (including
    when there is no active methodology row at all, or an older row that
    predates these columns).
    """

    methodology = (
        db.query(RiskMethodology)
        .filter(RiskMethodology.is_active.is_(True))
        .order_by(RiskMethodology.updated_at.desc())
        .first()
    )

    if not methodology:
        config = config_from_row(None)
    else:
        config = config_from_row(methodology)

    return config


def config_from_row(methodology: RiskMethodology | None) -> dict[str, Any]:
    """
    A methodology's full configuration, with the built-in defaults for
    anything unset. `methodology_version` is "builtin" for the defaults
    (no row) and "v<n>" for a row; `methodology_fingerprint` identifies
    the exact settings either way.
    """

    import copy

    if methodology is None:
        config: dict[str, Any] = {
            "methodology_id": None,
            "methodology_name": "Default",
            "methodology_version": "builtin",
            "factor_weights": dict(DEFAULT_FACTOR_WEIGHTS),
            "thresholds": dict(DEFAULT_RISK_THRESHOLDS),
            "likelihood_scale": list(DEFAULT_LIKELIHOOD_SCALE),
            "impact_scale": list(DEFAULT_IMPACT_SCALE),
            "risk_bands": list(DEFAULT_RISK_BANDS),
            "escalation_rules": copy.deepcopy(DEFAULT_ESCALATION_RULES),
            "required_approvals": dict(DEFAULT_REQUIRED_APPROVALS),
            "mitigant_categories": list(DEFAULT_MITIGANT_CATEGORIES),
            "residual_grid": copy.deepcopy(DEFAULT_RESIDUAL_GRID),
        }
    else:
        config = {
            "methodology_id": methodology.id,
            "methodology_name": methodology.name,
            "methodology_version": f"v{methodology.version or 1}",
            "factor_weights": _load_json(methodology.factor_weights, dict(DEFAULT_FACTOR_WEIGHTS)),
            "thresholds": _load_json(methodology.thresholds, dict(DEFAULT_RISK_THRESHOLDS)),
            "likelihood_scale": _load_json(methodology.likelihood_scale, list(DEFAULT_LIKELIHOOD_SCALE)),
            "impact_scale": _load_json(methodology.impact_scale, list(DEFAULT_IMPACT_SCALE)),
            "risk_bands": _load_json(methodology.risk_bands, list(DEFAULT_RISK_BANDS)),
            "escalation_rules": _load_json(
                methodology.escalation_rules, copy.deepcopy(DEFAULT_ESCALATION_RULES)
            ),
            "required_approvals": _load_json(methodology.required_approvals, dict(DEFAULT_REQUIRED_APPROVALS)),
            "mitigant_categories": _load_json(
                methodology.mitigant_categories, list(DEFAULT_MITIGANT_CATEGORIES)
            ),
            "residual_grid": _load_json(methodology.residual_grid, copy.deepcopy(DEFAULT_RESIDUAL_GRID)),
        }

    config["methodology_fingerprint"] = methodology_fingerprint(config)
    return config


def mark_methodology_used(db: Session, methodology_id: int | None) -> None:
    """
    Locks a methodology the first time a calculation relies on it. From
    then on it can only be cloned, never edited (see api/risk_methodology.py).
    """

    if methodology_id is None:
        return

    methodology = db.get(RiskMethodology, methodology_id)
    if methodology is not None and methodology.locked_at is None:
        from datetime import datetime, timezone

        methodology.locked_at = datetime.now(timezone.utc)


def get_active_thresholds(db: Session) -> dict[str, float]:
    """
    Returns the risk-level thresholds for the currently active
    RiskMethodology row, or the built-in defaults if none is
    configured/active. This is the single place the risk engine and
    stage-pipeline scoring should go through, so the methodology stays
    configurable rather than hardcoded.

    Note this deliberately returns thresholds only. The overall score is
    an unweighted average of the applicable risk factors (see
    app/risk_engine/scoring.py::calculate_overall_score_from_factors),
    so there is no per-category weighting to apply here. Per-factor
    likelihood/impact weights live in get_methodology_config()'s
    factor_weights.
    """

    methodology = (
        db.query(RiskMethodology)
        .filter(RiskMethodology.is_active.is_(True))
        .order_by(RiskMethodology.updated_at.desc())
        .first()
    )

    if not methodology:
        return dict(DEFAULT_RISK_THRESHOLDS)

    try:
        return json.loads(methodology.thresholds)
    except (TypeError, ValueError):
        return dict(DEFAULT_RISK_THRESHOLDS)

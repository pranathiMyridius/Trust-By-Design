"""
Deterministic risk logic that stands in when the AI is unavailable:
the keyword RiskEngine and the degraded-mode translation into the 10
canonical categories (app/risk_engine/engine.py, degraded.py).
"""

import pytest

from app.ai.errors import (
    AIInvalidResponseError,
    AINotConfiguredError,
    AIProviderError,
    AIRateLimitError,
    AITimeoutError,
)
from app.risk_engine.degraded import (
    DIMENSION_TO_CATEGORY,
    UNEVALUATED_IN_RULES_ONLY,
    AiStatus,
    build_rules_only_factors,
    classify_ai_failure,
    fallback_enabled,
    unevaluated_categories,
)
from app.risk_engine.engine import CHANGE_TYPE_RISK_CATEGORY, RiskEngine
from app.schemas.risk_factor import RISK_CATEGORIES


def dims(results):
    return {result.dimension: result for result in results}


def test_new_geography_raises_geographic_and_compliance_risk():
    results = dims(RiskEngine().assess("NEW_GEOGRAPHY", "Launch in Germany.", "No customer data."))
    # Merged results re-derive severity from score (80+ is CRITICAL).
    assert results["GEOGRAPHIC"].score >= 80
    assert results["GEOGRAPHIC"].severity == "CRITICAL"
    assert results["COMPLIANCE"].score >= 80


def test_customer_data_keywords_escalate_customer_risk():
    plain = dims(RiskEngine().assess("NEW_GEOGRAPHY", "Launch in Germany.", ""))
    with_data = dims(RiskEngine().assess("NEW_GEOGRAPHY", "Launch in Germany.", "collects customer data"))
    assert with_data["CUSTOMER"].score > plain["CUSTOMER"].score


@pytest.mark.parametrize("change_type", sorted(CHANGE_TYPE_RISK_CATEGORY))
def test_every_supported_change_type_produces_a_result(change_type):
    assert RiskEngine().assess(change_type, "A business change.", "Some evidence.")


def test_unknown_change_type_falls_back_to_material_change():
    unknown = RiskEngine().assess("SOMETHING_NEW", "desc", "ev")
    material = RiskEngine().assess("MATERIAL_CHANGE", "desc", "ev")
    assert {r.dimension for r in unknown} == {r.dimension for r in material}


def test_engine_severity_is_consistent_with_score():
    for result in RiskEngine().assess("THIRD_PARTY", "Outsourced KYC vendor.", "vendor handles onboarding"):
        assert 0 <= result.score <= 100
        assert result.severity in {"LOW", "MEDIUM", "HIGH", "CRITICAL"}


# -- degraded-mode translation ------------------------------------------------


def test_rules_only_factors_map_onto_canonical_categories():
    results = RiskEngine().assess("NEW_GEOGRAPHY", "Launch in Germany.", "customer data")
    factors = build_rules_only_factors(results)
    assert factors
    for factor in factors:
        assert factor["category"] in RISK_CATEGORIES
        assert factor["applicable"] is True
        assert factor["evidence_status"] == "NOT_VERIFIED"
        assert factor["rationale"].startswith("Identified by the deterministic risk rule engine")


def test_rules_only_never_writes_zero_rows_for_categories_it_cannot_judge():
    factors = build_rules_only_factors(RiskEngine().assess("NEW_GEOGRAPHY", "x", "y"))
    covered = {factor["category"] for factor in factors}
    assert not covered & set(UNEVALUATED_IN_RULES_ONLY)
    assert set(unevaluated_categories(factors)) >= set(UNEVALUATED_IN_RULES_ONLY)


def test_unmapped_engine_dimension_is_dropped_not_guessed():
    class Result:
        dimension, score, severity, reason = "ASTROLOGICAL", 90, "HIGH", "stars"

    assert build_rules_only_factors([Result()]) == []
    assert "ASTROLOGICAL" not in DIMENSION_TO_CATEGORY


@pytest.mark.parametrize(
    ("exc", "status", "code"),
    [
        (AITimeoutError("t"), AiStatus.TIMEOUT, "AI_TIMEOUT"),
        (AIRateLimitError("r"), AiStatus.RATE_LIMITED, "AI_RATE_LIMITED"),
        (AIInvalidResponseError("i"), AiStatus.INVALID_RESPONSE, "AI_INVALID_RESPONSE"),
        (AINotConfiguredError("n"), AiStatus.FAILED, "AI_NOT_CONFIGURED"),
        (AIProviderError("p"), AiStatus.FAILED, "AI_PROVIDER_ERROR"),
        (ValueError("bad data"), AiStatus.FAILED, "UNEXPECTED_ANALYSIS_FAILURE"),
    ],
)
def test_failure_classification(exc, status, code):
    assert classify_ai_failure(exc) == (status, code)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [(None, True), ("true", True), ("1", True), ("false", False), ("OFF", False), ("0", False)],
)
def test_fallback_flag_parsing(monkeypatch, raw, expected):
    if raw is None:
        monkeypatch.delenv("ENABLE_RULES_ONLY_FALLBACK", raising=False)
    else:
        monkeypatch.setenv("ENABLE_RULES_ONLY_FALLBACK", raw)
    assert fallback_enabled() is expected

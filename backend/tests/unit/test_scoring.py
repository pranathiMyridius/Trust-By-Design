"""
Deterministic inherent-risk arithmetic (app/risk_engine/scoring.py).

This is the part of the system that turns ratings into the official
score and band, so it is tested exhaustively and exactly -- no LLM is
involved anywhere below.
"""

import pytest

from app.risk_engine.scoring import (
    DEFAULT_ESCALATION_RULES,
    DEFAULT_RISK_BANDS,
    calculate_inherent_risk,
    calculate_overall_score_from_factors,
    compute_factor_score,
    determine_risk_band,
    determine_risk_level,
    methodology_fingerprint,
    validate_rule,
)


def rated(category, likelihood, impact, **extra):
    return {
        "category": category,
        "applicable": True,
        "excluded": False,
        "rated": True,
        "likelihood": likelihood,
        "impact": impact,
        "score": compute_factor_score(likelihood, impact),
        "indicators": [],
        **extra,
    }


def unrated(category, **extra):
    return {"category": category, "applicable": True, "excluded": False, "rated": False, "score": 0.0, "indicators": [], **extra}


# -- factor score ---------------------------------------------------------


@pytest.mark.parametrize(
    ("likelihood", "impact", "expected"),
    [(1, 1, 4.0), (3, 3, 36.0), (4, 5, 80.0), (5, 5, 100.0), (2, 3, 24.0)],
)
def test_factor_score_is_likelihood_times_impact_scaled_to_100(likelihood, impact, expected):
    assert compute_factor_score(likelihood, impact) == expected


def test_factor_score_is_clamped_to_0_100():
    assert compute_factor_score(9, 9) == 100.0
    assert compute_factor_score(0, 5) == 0.0


def test_factor_score_uses_configured_scale_maximum():
    three_point = [{"value": v, "label": str(v)} for v in (1, 2, 3)]
    assert compute_factor_score(3, 3, three_point, three_point) == 100.0
    assert compute_factor_score(1, 3, three_point, three_point) == pytest.approx(33.33, abs=0.01)


# -- band classification ----------------------------------------------------


@pytest.mark.parametrize(
    ("score", "band"),
    [
        (0, "LOW"),
        (39, "LOW"),
        (39.99, "LOW"),
        (40, "MEDIUM"),
        (59.99, "MEDIUM"),
        (60, "HIGH"),
        (79.99, "HIGH"),
        (80, "CRITICAL"),
        (100, "CRITICAL"),
    ],
)
def test_default_band_boundaries(score, band):
    assert determine_risk_band(score) == band


def test_configured_bands_replace_the_defaults():
    bands = [{"name": "GREEN", "min": 0, "max": 49}, {"name": "RED", "min": 50, "max": 100}]
    assert determine_risk_band(49.9, bands) == "GREEN"
    assert determine_risk_band(50, bands) == "RED"


@pytest.mark.parametrize(("score", "level"), [(10, "LOW"), (40, "MEDIUM"), (60, "HIGH"), (80, "CRITICAL")])
def test_legacy_risk_level_thresholds(score, level):
    assert determine_risk_level(score) == level


# -- the official inherent-risk calculation ---------------------------------


def test_weighted_average_of_rated_factors():
    result = calculate_inherent_risk(
        [rated("GEOGRAPHIC_RISK", 4, 5), rated("CUSTOMER_SEGMENT_RISK", 2, 3)]
    )
    assert result["final_score"] == pytest.approx((80 + 24) / 2)
    assert result["risk_band"] == "MEDIUM"
    assert result["is_provisional"] is False
    assert result["rated_factor_count"] == 2


def test_custom_weights_change_the_average():
    weights = {"GEOGRAPHIC_RISK": 0.75, "CUSTOMER_SEGMENT_RISK": 0.25}
    result = calculate_inherent_risk(
        [rated("GEOGRAPHIC_RISK", 4, 5), rated("CUSTOMER_SEGMENT_RISK", 2, 3)], weights=weights
    )
    assert result["final_score"] == pytest.approx(80 * 0.75 + 24 * 0.25)
    assert result["risk_band"] == "HIGH"


def test_unrated_factors_are_unknown_not_zero():
    """An unrated factor must not drag the average down as if it were 0."""

    result = calculate_inherent_risk([rated("GEOGRAPHIC_RISK", 4, 5), unrated("CUSTOMER_SEGMENT_RISK")])
    assert result["final_score"] == 80.0
    assert result["is_provisional"] is True


def test_applicable_factors_with_no_ratings_have_no_score_at_all():
    result = calculate_inherent_risk([unrated("GEOGRAPHIC_RISK"), unrated("PRODUCT_SERVICE_RISK")])
    assert result["final_score"] is None
    assert result["risk_band"] is None
    assert result["is_provisional"] is True


def test_nothing_applicable_is_a_genuine_zero():
    factors = [{"category": "GEOGRAPHIC_RISK", "applicable": False, "score": 0}]
    result = calculate_inherent_risk(factors)
    assert result["final_score"] == 0.0
    assert result["risk_band"] == "LOW"


def test_excluded_and_mitigant_factors_do_not_count():
    result = calculate_inherent_risk(
        [
            rated("GEOGRAPHIC_RISK", 2, 2),
            rated("PRODUCT_SERVICE_RISK", 5, 5, excluded=True),
            rated("CONTROL_ENVIRONMENT_RISK", 5, 5),
        ]
    )
    assert result["final_score"] == 16.0
    assert result["applicable_factor_count"] == 1
    assert "CONTROL_ENVIRONMENT_RISK" in result["mitigant_categories"]


def test_sanctions_indicator_forces_critical_even_when_scores_are_low():
    result = calculate_inherent_risk(
        [rated("GEOGRAPHIC_RISK", 1, 2, indicators=["SANCTIONS_EXPOSURE"])]
    )
    assert result["final_score"] == 8.0
    assert result["risk_band"] == "CRITICAL"
    assert result["escalated"] is True
    assert result["mandatory_review"] is True
    fired = result["triggered_rules"][0]
    assert fired["rule_code"] == "SANCTIONS_EXPOSURE_001"
    assert fired["band_before"] == "LOW" and fired["band_after"] == "CRITICAL"


def test_sanctions_rule_sets_a_band_before_anything_is_rated():
    result = calculate_inherent_risk([unrated("GEOGRAPHIC_RISK", indicators=["SANCTIONS_EXPOSURE"])])
    assert result["final_score"] is None
    assert result["risk_band"] == "CRITICAL"


def test_draft_rules_are_carried_but_never_applied():
    # MIN_BAND_KEY_FACTOR_001 ships as draft: a HIGH geographic factor
    # averaged with a LOW one must stay at the averaged band.
    assert any(rule["status"] == "draft" for rule in DEFAULT_ESCALATION_RULES)
    result = calculate_inherent_risk([rated("GEOGRAPHIC_RISK", 4, 4), rated("PRODUCT_SERVICE_RISK", 1, 1)])
    assert result["risk_band"] == "LOW"
    assert result["escalated"] is False


def test_an_approved_minimum_band_rule_raises_the_floor():
    rules = [
        {**rule, "status": "approved"} if rule["rule_code"] == "MIN_BAND_KEY_FACTOR_001" else rule
        for rule in DEFAULT_ESCALATION_RULES
    ]
    result = calculate_inherent_risk(
        [rated("GEOGRAPHIC_RISK", 4, 4), rated("PRODUCT_SERVICE_RISK", 1, 1)], escalation_rules=rules
    )
    assert result["final_score"] == pytest.approx((64 + 4) / 2)
    assert result["risk_band"] == "HIGH"


def test_rule_validation_rejects_unknown_bands():
    bad = {**DEFAULT_ESCALATION_RULES[0], "min_band": "APOCALYPTIC"}
    assert validate_rule(bad, DEFAULT_RISK_BANDS)
    assert validate_rule(DEFAULT_ESCALATION_RULES[0], DEFAULT_RISK_BANDS) == []


def test_legacy_average_ignores_not_applicable_and_excluded():
    factors = [
        {"applicable": True, "score": 80},
        {"applicable": True, "score": 40},
        {"applicable": False, "score": 100},
        {"applicable": True, "excluded": True, "score": 100},
    ]
    assert calculate_overall_score_from_factors(factors) == 60.0
    assert calculate_overall_score_from_factors([]) == 0.0


def test_methodology_fingerprint_is_stable_and_sensitive():
    config = {"factor_weights": {"A": 0.5}, "risk_bands": DEFAULT_RISK_BANDS}
    assert methodology_fingerprint(config) == methodology_fingerprint(dict(config))
    changed = {**config, "factor_weights": {"A": 0.6}}
    assert methodology_fingerprint(changed) != methodology_fingerprint(config)

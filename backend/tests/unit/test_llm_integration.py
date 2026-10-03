"""
The LLM integration layer, with the provider faked at the HTTP seam
(app/ai/metering.py). Covers response parsing, output validation, the
hallucination guard (quote verification), retry policy and the typed
errors the workflow relies on to choose a fallback.

No assertion here looks at generated prose: the fake's wording is
irrelevant, only structure and behaviour are checked.
"""

import json

import pytest

import app.ai.likelihood_impact_analyzer as likelihood_impact_analyzer
import app.ai.risk_factor_analyzer as analyzer
from app.ai.errors import (
    AIInvalidResponseError,
    AINotConfiguredError,
    AIProviderError,
    AIRateLimitError,
    AITimeoutError,
)
from app.risk_engine.evidence import build_evidence_sources
from app.schemas.risk_factor import RISK_CATEGORIES
from tests.support.fake_llm import FakeLLM, FakeResponse, chat

ASSESSMENT = {
    "id": 1,
    "change_type": "NEW_PRODUCT",
    "title": "Prepaid travel card",
    "description": "A prepaid card allowing cross-border cash withdrawals in 40 countries.",
    "evidence": "Customers are onboarded remotely through the mobile app.",
}


def identify(**kwargs):
    return analyzer.identify_risk_factors(
        assessment=ASSESSMENT, evidence_sources=build_evidence_sources(ASSESSMENT), **kwargs
    )


def reply_with(fake_llm, factors):
    fake_llm.transport = lambda url, payload: chat(json.dumps({"factors": factors}))


# -- well-formed replies -----------------------------------------------------------


def test_returns_all_ten_categories_in_canonical_order(fake_llm):
    factors = identify()
    assert [factor["category"] for factor in factors] == RISK_CATEGORIES


def test_model_scores_are_discarded_every_factor_starts_unrated(fake_llm):
    reply_with(
        fake_llm,
        [{"category": "GEOGRAPHIC_RISK", "applicable": True, "score": 95, "severity": "CRITICAL", "rationale": "x"}],
    )
    geographic = next(f for f in identify() if f["category"] == "GEOGRAPHIC_RISK")
    assert geographic["score"] == 0.0
    assert geographic["severity"] == "LOW"


def test_omitted_categories_become_unresolved_not_low_risk(fake_llm):
    reply_with(fake_llm, [{"category": "GEOGRAPHIC_RISK", "applicable": False, "rationale": "Domestic only."}])
    factors = {f["category"]: f for f in identify()}
    omitted = factors["CUSTOMER_SEGMENT_RISK"]
    assert omitted["applicable"] is True
    assert omitted["evidence_status"] == "INSUFFICIENT_EVIDENCE"
    assert omitted["missing_information"]


def test_unknown_indicators_are_dropped(fake_llm):
    reply_with(
        fake_llm,
        [
            {
                "category": "PRODUCT_SERVICE_RISK",
                "applicable": True,
                "indicators": ["CASH_ACCESS", "TELEPATHY"],
                "evidence": [
                    {"source_id": "FIELD:description", "quote": "cross-border cash withdrawals", "indicator": "CASH_ACCESS"}
                ],
                "rationale": "Cash access abroad.",
            }
        ],
    )
    product = next(f for f in identify() if f["category"] == "PRODUCT_SERVICE_RISK")
    assert product["indicators"] == ["CASH_ACCESS"]
    assert product["evidence_status"] == "EVIDENCE_FOUND"


def test_fabricated_quote_is_rejected_and_its_indicator_refused(fake_llm):
    """The hallucination guard: a quote that is not in the source is not evidence."""

    reply_with(
        fake_llm,
        [
            {
                "category": "GEOGRAPHIC_RISK",
                "applicable": True,
                "indicators": ["SANCTIONS_EXPOSURE"],
                "evidence": [
                    {
                        "source_id": "FIELD:description",
                        "quote": "customers include sanctioned entities in Iran",
                        "indicator": "SANCTIONS_EXPOSURE",
                    }
                ],
                "rationale": "Sanctions exposure.",
            }
        ],
    )
    geographic = next(f for f in identify() if f["category"] == "GEOGRAPHIC_RISK")
    assert geographic["indicators"] == []
    assert geographic["rejected_quote_count"] == 1
    assert geographic["evidence_status"] == "NOT_VERIFIED"
    assert geographic["rejected_indicators"][0]["indicator"] == "SANCTIONS_EXPOSURE"


def test_code_fenced_json_is_accepted(fake_llm):
    body = json.dumps({"factors": [{"category": "GEOGRAPHIC_RISK", "applicable": False, "rationale": "n/a"}]})
    fake_llm.transport = lambda url, payload: chat(f"```json\n{body}\n```")
    assert len(identify()) == 10


def test_json_embedded_in_prose_is_extracted(fake_llm):
    body = json.dumps({"factors": [{"category": "GEOGRAPHIC_RISK", "applicable": False, "rationale": "n/a"}]})
    fake_llm.transport = lambda url, payload: chat(f"Here you go: {body} Hope this helps!")
    assert len(identify()) == 10


def test_prompt_forbids_scoring_and_lists_every_category_and_source(fake_llm):
    identify()
    prompt = fake_llm.calls[0]["json"]["messages"][0]["content"]
    assert "Do NOT score or rate anything" in prompt
    for category in RISK_CATEGORIES:
        assert category in prompt
    assert 'source id="FIELD:description"' in prompt
    assert fake_llm.calls[0]["json"]["temperature"] <= 0.3


# -- malformed replies ---------------------------------------------------------------


@pytest.mark.parametrize(
    "transport",
    [
        FakeLLM.malformed,
        lambda url, payload: chat(""),
        lambda url, payload: FakeResponse(200, {"unexpected": True}),
        lambda url, payload: FakeResponse(200, None, text="<html>gateway</html>"),
        lambda url, payload: chat(json.dumps({"factors": "none"})),
    ],
    ids=["not-json", "empty", "no-choices", "non-json-body", "factors-not-a-list"],
)
def test_unusable_replies_raise_invalid_response(fake_llm, transport):
    fake_llm.transport = transport
    with pytest.raises(AIInvalidResponseError):
        identify()


def test_reply_with_only_invented_categories_is_an_invalid_ai_response(fake_llm):
    """Invalid AI output must take the AI-failure path (rules-only fallback)."""

    reply_with(fake_llm, [{"category": "VIBES_RISK", "applicable": True, "rationale": "?"}])
    with pytest.raises(AIInvalidResponseError):
        identify()


def test_an_invented_category_is_dropped_and_the_rest_kept(fake_llm):
    reply_with(
        fake_llm,
        [
            {"category": "VIBES_RISK", "applicable": True, "rationale": "?"},
            {"category": "GEOGRAPHIC_RISK", "applicable": False, "rationale": "Domestic only."},
        ],
    )
    factors = {f["category"]: f for f in identify()}
    assert "VIBES_RISK" not in factors
    assert factors["GEOGRAPHIC_RISK"]["applicable"] is False
    assert factors["PRODUCT_SERVICE_RISK"]["evidence_status"] == "INSUFFICIENT_EVIDENCE"


# -- sanctions: the one indicator that forces CRITICAL on its own ---------------------


def sanctions_factor(quote, source_id="FIELD:description"):
    return [
        {
            "category": "GEOGRAPHIC_RISK",
            "applicable": True,
            "indicators": ["SANCTIONS_EXPOSURE"],
            "evidence": [{"source_id": source_id, "quote": quote, "indicator": "SANCTIONS_EXPOSURE"}],
            "rationale": "Sanctions exposure.",
        }
    ]


def test_real_but_off_topic_quote_cannot_carry_sanctions_exposure(fake_llm):
    """'cross-border cash withdrawals' is verbatim, but it is not about sanctions."""

    reply_with(fake_llm, sanctions_factor("cross-border cash withdrawals in 40 countries"))
    geographic = next(f for f in identify() if f["category"] == "GEOGRAPHIC_RISK")
    assert "SANCTIONS_EXPOSURE" not in geographic["indicators"]
    assert geographic["evidence"][0]["quote_verified"] is True
    assert geographic["evidence"][0]["supports_indicator"] is False
    assert "does not refer" in geographic["rejected_indicators"][0]["reason"]
    # Referred to a human, not silently dropped.
    assert any("sanctions exposure" in m for m in geographic["missing_information"])


def test_sanctions_quote_from_a_sanctions_sentence_is_accepted(fake_llm):
    assessment = {
        **ASSESSMENT,
        "evidence": "One buyer is located in a country the bank's sanctions policy treats as embargoed. Goods ship via a free-trade zone.",
    }
    fake_llm.transport = lambda url, payload: chat(
        json.dumps({"factors": sanctions_factor("One buyer is located in a country", "FIELD:evidence")})
    )
    factors = analyzer.identify_risk_factors(assessment=assessment, evidence_sources=build_evidence_sources(assessment))
    geographic = next(f for f in factors if f["category"] == "GEOGRAPHIC_RISK")
    assert geographic["indicators"] == ["SANCTIONS_EXPOSURE"]


def test_prompt_restricts_sanctions_tagging(fake_llm):
    identify()
    prompt = fake_llm.calls[0]["json"]["messages"][0]["content"]
    assert "Tag SANCTIONS_EXPOSURE only when a source explicitly mentions sanctions" in prompt


# -- provider failures and retry policy ------------------------------------------------


def test_timeout_is_typed_and_not_retried(fake_llm, monkeypatch):
    monkeypatch.setattr(analyzer, "OPENROUTER_MAX_ATTEMPTS", 3)
    fake_llm.transport = FakeLLM.timeout
    with pytest.raises(AITimeoutError):
        identify()
    assert len(fake_llm.calls) == 1


def test_rate_limit_is_typed(fake_llm):
    fake_llm.transport = FakeLLM.rate_limited
    with pytest.raises(AIRateLimitError):
        identify()


def test_transient_5xx_is_retried_then_succeeds(fake_llm, monkeypatch):
    monkeypatch.setattr(analyzer, "OPENROUTER_MAX_ATTEMPTS", 2)
    monkeypatch.setattr(analyzer, "OPENROUTER_BACKOFF_SECONDS", 0)
    replies = iter([FakeLLM.server_error, None])

    def flaky(url, payload):
        transport = next(replies)
        return transport(url, payload) if transport else chat(json.dumps({"factors": []}))

    fake_llm.transport = flaky
    assert len(identify()) == 10
    assert len(fake_llm.calls) == 2


def test_client_error_is_final_not_retried(fake_llm, monkeypatch):
    monkeypatch.setattr(analyzer, "OPENROUTER_MAX_ATTEMPTS", 3)
    fake_llm.transport = lambda url, payload: FakeResponse(400, {"error": "bad request"})
    with pytest.raises(AIProviderError):
        identify()
    assert len(fake_llm.calls) == 1


def test_connection_error_becomes_provider_error(fake_llm):
    fake_llm.transport = FakeLLM.connection_error
    with pytest.raises(AIProviderError):
        identify()


def test_missing_api_key_is_not_configured(fake_llm, monkeypatch):
    monkeypatch.setattr(analyzer, "OPENROUTER_API_KEY", None)
    with pytest.raises(AINotConfiguredError):
        identify()
    assert fake_llm.calls == []


def test_sensitive_data_is_masked_before_it_leaves(fake_llm, monkeypatch):
    monkeypatch.setenv("AI_MASK_SENSITIVE_DATA", "true")
    analyzer.identify_risk_factors(
        assessment={**ASSESSMENT, "evidence": "Test card 4111 1111 1111 1111, contact ops@bank.example"},
        evidence_sources={},
    )
    sent = json.dumps(fake_llm.calls[0]["json"])
    assert "4111 1111 1111 1111" not in sent
    assert "ops@bank.example" not in sent


# -- likelihood / impact suggestions -------------------------------------------------


def estimate():
    return likelihood_impact_analyzer.estimate_likelihood_impact(
        "GEOGRAPHIC_RISK", "Cross-border flows.", "Layering through foreign accounts."
    )


def test_suggestion_values_are_clamped_to_the_scale(fake_llm):
    fake_llm.transport = lambda url, payload: chat(json.dumps({"likelihood": 9, "impact": 0, "reasoning": "r"}))
    result = estimate()
    assert (result["likelihood"], result["impact"], result["fallback"]) == (5, 1, False)


def test_failed_suggestion_is_flagged_as_fallback_not_presented_as_ai(fake_llm):
    fake_llm.transport = FakeLLM.malformed
    result = estimate()
    assert result["fallback"] is True
    assert (result["likelihood"], result["impact"]) == (3, 3)


def test_suggestion_without_key_is_a_flagged_fallback(fake_llm, monkeypatch):
    monkeypatch.setattr(likelihood_impact_analyzer, "OPENROUTER_API_KEY", None)
    assert estimate()["fallback"] is True
    assert fake_llm.calls == []

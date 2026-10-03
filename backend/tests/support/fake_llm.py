"""
A deterministic stand-in for the OpenRouter chat-completions API.

Installed in place of `requests.post` inside app/ai/metering.py -- the one
seam every AI call in the app goes through -- so the real analyzers,
evidence verifier, rule engine and persistence all run unchanged and only
the network hop is faked. Used by the pytest suite (tests/conftest.py)
and by the Playwright backend (tests/e2e_server.py).

The default replies are built from the prompt itself: risk factors quote
real text from the prompt's <source> blocks (so evidence verification
passes), and the indicators chosen follow simple keyword cues. This is
NOT an evaluation of AI quality -- that is tests/evals -- it just gives
deterministic tests a realistic, well-formed model answer.
"""

from __future__ import annotations

import json
import re
from typing import Any, Callable

import requests

from app.schemas.risk_factor import RISK_CATEGORIES

MODEL_NAME = "fake-vendor/fake-risk-model"

_SOURCE_BLOCK = re.compile(r'<source id="([^"]+)" label="[^"]*">\n(.*?)\n</source>', re.S)

# keyword -> (category, indicator)
_CUES: list[tuple[tuple[str, ...], str, str]] = [
    (("cross-border", "cross border", "international"), "GEOGRAPHIC_RISK", "CROSS_BORDER_CAPABILITY"),
    (("sanction",), "GEOGRAPHIC_RISK", "SANCTIONS_EXPOSURE"),
    (("cash",), "PRODUCT_SERVICE_RISK", "CASH_ACCESS"),
    (("remote", "online onboarding", "app onboarding"), "DELIVERY_CHANNEL_RISK", "REMOTE_ONBOARDING"),
    (("processor", "vendor", "third party", "third-party", "partner"), "THIRD_PARTY_VENDOR_RISK", "THIRD_PARTY_DEPENDENCIES"),
    (("crypto", "anonym", "prepaid"), "PRODUCT_SERVICE_RISK", "ANONYMITY"),
    (("currenc", "fx"), "TRANSACTION_ACTIVITY_RISK", "MULTIPLE_CURRENCIES"),
]


class FakeResponse:
    def __init__(self, status_code: int, body: Any = None, text: str | None = None):
        self.status_code = status_code
        self._body = body
        self.text = text if text is not None else json.dumps(body)
        self.content = self.text.encode()

    def json(self) -> Any:
        if self._body is None:
            raise ValueError("no json body")
        return self._body

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise requests.HTTPError(f"{self.status_code} error", response=self)


def chat(content: str, model: str = MODEL_NAME) -> FakeResponse:
    return FakeResponse(
        200,
        {
            "model": model,
            "choices": [{"message": {"content": content}}],
            "usage": {"prompt_tokens": 120, "completion_tokens": 80, "total_tokens": 200},
        },
    )


def prompt_of(payload: dict[str, Any] | None) -> str:
    messages = (payload or {}).get("messages") or []
    return "\n".join(str(message.get("content") or "") for message in messages if isinstance(message, dict))


def sources_in(prompt: str) -> dict[str, str]:
    return {source_id: text for source_id, text in _SOURCE_BLOCK.findall(prompt)}


def _quote_near(text: str, keyword: str) -> str | None:
    index = text.lower().find(keyword)
    if index < 0:
        return None
    start = max(0, index - 10)
    return text[start : start + 60].strip() or None


def factors_for(prompt: str) -> list[dict[str, Any]]:
    """A plausible, fully verifiable factor set for the prompt's sources."""

    sources = sources_in(prompt)
    by_category: dict[str, dict[str, Any]] = {}

    for keywords, category, indicator in _CUES:
        for source_id, text in sources.items():
            quote = next((q for k in keywords if (q := _quote_near(text, k))), None)
            if not quote:
                continue
            factor = by_category.setdefault(
                category,
                {
                    "category": category,
                    "applicable": True,
                    "indicators": [],
                    "evidence": [],
                    "missing_information": [],
                    "rationale": f"The intake describes {indicator.replace('_', ' ').lower()}.",
                    "misuse_scenario": "Could be used to move illicit funds through the new capability.",
                },
            )
            if indicator not in factor["indicators"]:
                factor["indicators"].append(indicator)
                factor["evidence"].append({"source_id": source_id, "quote": quote, "indicator": indicator})
            break

    description = sources.get("FIELD:description")
    if description and "PRODUCT_SERVICE_RISK" not in by_category:
        by_category["PRODUCT_SERVICE_RISK"] = {
            "category": "PRODUCT_SERVICE_RISK",
            "applicable": True,
            "indicators": [],
            "evidence": [{"source_id": "FIELD:description", "quote": description[:60].strip()}],
            "missing_information": [],
            "rationale": "A new product is being introduced.",
            "misuse_scenario": "The new product could be misused to layer funds.",
        }

    return [
        by_category.get(
            category,
            {
                "category": category,
                "applicable": False,
                "indicators": [],
                "evidence": [],
                "missing_information": [],
                "rationale": f"Nothing in the intake points to {category.replace('_', ' ').lower()}.",
                "misuse_scenario": None,
            },
        )
        for category in RISK_CATEGORIES
    ]


# "Label: value" lines the document extractor's fake answer reads, as
# (label, field, is_list). Opt-in (tests/e2e_server.py): the default reply
# for extraction stays an empty object.
_EXTRACTION_LABELS = [
    ("Project Name", "product_or_service_name", False),
    ("Target Destination Jurisdictions", "countries", True),
    ("Primary Vendor", "third_party_vendors", True),
    ("Projected Volume", "transaction_volume", False),
    ("Business Line", "business_line", False),
]


def extraction_for(prompt: str) -> dict[str, Any] | None:
    """A document-extraction answer quoting the document's own labelled
    lines, or None when the prompt is not a document-extraction prompt."""

    if "SOURCE DOCUMENT:" not in prompt:
        return None
    document = prompt.split("SOURCE DOCUMENT:", 1)[1].split("Return ONLY valid JSON", 1)[0]
    answer: dict[str, Any] = {"title": "Extracted change", "change_type": "NEW_PRODUCT", "field_evidence": {}}
    for label, field, is_list in _EXTRACTION_LABELS:
        match = re.search(rf"^{re.escape(label)}:\s*(.+)$", document, re.M)
        if not match:
            continue
        value = match.group(1).strip()
        if is_list:
            answer[field] = [item.split("(")[0].strip() for item in re.split(r",| and ", value) if item.strip()]
        else:
            answer[field] = value
        answer["field_evidence"][field] = [{"quote": match.group(0).strip()}]
    return answer


def default_reply(url: str, payload: dict[str, Any] | None) -> FakeResponse:
    prompt = prompt_of(payload)

    if "Work through EVERY one of the 10 risk categories" in prompt:
        return chat(json.dumps({"factors": factors_for(prompt)}))

    if "estimate the likelihood" in prompt:
        high = any(word in prompt.lower() for word in ("sanction", "cross-border", "crypto", "cash"))
        return chat(
            json.dumps(
                {
                    "likelihood": 4 if high else 2,
                    "impact": 4 if high else 3,
                    "reasoning": "Deterministic test suggestion.",
                }
            )
        )

    # Any other AI feature (controls, drafts, document extraction): an
    # empty-but-valid JSON object; those callers all degrade gracefully.
    return chat("{}")


Transport = Callable[[str, dict[str, Any] | None], FakeResponse]


class FakeLLM:
    """
    Callable with requests.post's signature. `transport` decides each
    reply; every call is recorded in `calls` for assertions.
    """

    def __init__(self) -> None:
        self.transport: Transport = default_reply
        self.calls: list[dict[str, Any]] = []

    def __call__(self, url, headers=None, json=None, timeout=None, **kwargs):  # noqa: A002
        self.calls.append({"url": url, "headers": headers, "json": json, "timeout": timeout})
        return self.transport(url, json)

    # -- canned failure modes -------------------------------------------

    @staticmethod
    def timeout(url, payload):
        raise requests.Timeout("simulated provider timeout")

    @staticmethod
    def rate_limited(url, payload):
        return FakeResponse(429, {"error": "rate limit exceeded"})

    @staticmethod
    def server_error(url, payload):
        return FakeResponse(503, {"error": "upstream unavailable"})

    @staticmethod
    def malformed(url, payload):
        return chat("Sure! Here is your analysis: <definitely not json>")

    @staticmethod
    def connection_error(url, payload):
        raise requests.ConnectionError("simulated connection refused")

    def factor_calls(self) -> list[dict[str, Any]]:
        return [call for call in self.calls if "Work through EVERY one" in prompt_of(call["json"])]

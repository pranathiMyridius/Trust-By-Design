"""
Degraded-mode risk analysis: what the system does when the AI step fails.

Before this module existed, an AI failure in
app/langgraph/nodes.py::identify_risks was written back as all ten
categories at `applicable=False, score=0.0, severity="LOW"`. That is a
"could not evaluate" result wearing the clothes of a "we evaluated this
and found nothing". This module removes that semantic collapse by giving
every analysis run one of three explicit modes:

    ai_assisted  -- the AI answered; normal scoring, finalizable.
    rules_only   -- the AI failed operationally; the deterministic
                    RiskEngine produced a real, usable result, marked
                    provisional and requiring reviewer acknowledgement.
    unavailable  -- neither path produced a result; NO risk rating is
                    written at all.

The governing rule, in one line:

    No successful-looking low-risk score is allowed when the AI step
    failed.

A rules-only result is deliberately still *shown* -- graceful
degradation means a reduced, clearly-labelled level of service, not
pretending the full service ran.
"""

from __future__ import annotations

import os
from typing import Any, Iterable

from app.ai.errors import AIProviderError
from app.schemas.risk_factor import RISK_CATEGORIES


# --- modes and statuses ----------------------------------------------------

class AssessmentMode:
    AI_ASSISTED = "ai_assisted"
    RULES_ONLY = "rules_only"
    UNAVAILABLE = "unavailable"


class AiStatus:
    SUCCESS = "success"
    FAILED = "failed"
    TIMEOUT = "timeout"
    RATE_LIMITED = "rate_limited"
    INVALID_RESPONSE = "invalid_response"
    NOT_ATTEMPTED = "not_attempted"


class ScoreSource:
    AI_AND_RULES = "ai_and_rules"
    DETERMINISTIC_RULES = "deterministic_rules"
    NOT_AVAILABLE = "not_available"


# User-facing text. Deliberately says nothing about which provider, which
# model, or what the error was -- provider messages can echo back prompt
# content, and the vendor name is not an ordinary user's business.
DEGRADED_REASON = (
    "AI analysis was temporarily unavailable. This result was generated "
    "using approved deterministic risk rules only, and is provisional "
    "until a reviewer confirms it."
)

UNAVAILABLE_REASON = (
    "This assessment could not be completed automatically. Neither the AI "
    "analysis nor the deterministic rule engine produced a result, so no "
    "risk rating has been recorded."
)


def fallback_enabled() -> bool:
    """
    ENABLE_RULES_ONLY_FALLBACK (default: on).

    Turning it off does NOT restore the old silent all-zeros behaviour --
    that path is gone. Off means an AI failure produces `unavailable`
    (no rating at all) instead of a rules-only rating, which is the more
    conservative of the two, not the less.
    """

    raw = os.getenv("ENABLE_RULES_ONLY_FALLBACK")
    if raw is None:
        return True
    return raw.strip().lower() not in {"0", "false", "no", "off"}


# --- failure classification ------------------------------------------------

def classify_ai_failure(exc: BaseException) -> tuple[str, str]:
    """
    Map an exception raised by the AI analyzer to (ai_status, error_code).

    Only AIProviderError and its subclasses reach here in the normal
    path; anything else is not an AI-operational failure and the caller
    routes it to `unavailable` rather than to the rules fallback.
    """

    if isinstance(exc, AIProviderError):
        return exc.ai_status, exc.error_code

    return AiStatus.FAILED, "UNEXPECTED_ANALYSIS_FAILURE"


# --- rule-engine output -> canonical risk factors --------------------------

# RiskEngine (app/risk_engine/engine.py) predates the 10 canonical
# categories and still speaks the legacy 6/7-dimension vocabulary. This
# is the translation, and it is a judgement call per row rather than a
# mechanical rename -- kept as one readable table so it can be argued
# with.
DIMENSION_TO_CATEGORY: dict[str, str] = {
    "GEOGRAPHIC": "GEOGRAPHIC_RISK",
    "CUSTOMER": "CUSTOMER_SEGMENT_RISK",
    "TECHNOLOGY": "TECHNOLOGY_DEVELOPMENT_RISK",
    "THIRD_PARTY": "THIRD_PARTY_VENDOR_RISK",
    # The engine's OPERATIONAL rules are about how the change is
    # delivered and supported -- processes, capacity, monitoring --
    # which is what DELIVERY_CHANNEL_RISK covers in the new taxonomy.
    "OPERATIONAL": "DELIVERY_CHANNEL_RISK",
    # The engine's FINANCIAL rules fire on transaction limits and
    # volumes, not on credit or pricing exposure.
    "FINANCIAL": "TRANSACTION_ACTIVITY_RISK",
    # COMPLIANCE is the loosest fit of the seven: the engine uses it for
    # regulatory/sanctions/jurisdictional obligations, and
    # CONTROL_ENVIRONMENT_RISK is the canonical category that carries
    # compliance obligations. Worth revisiting if the engine's
    # compliance rules are ever split.
    "COMPLIANCE": "CONTROL_ENVIRONMENT_RISK",
}

# Categories no deterministic rule can currently speak to. These are
# NOT written back as zero/not-applicable rows: in rules-only mode they
# are simply not recorded, and reported as unevaluated. An absent factor
# means "the system could not evaluate this"; a factor at score 0.0
# would mean "the system evaluated this and found no material risk", and
# conflating the two is the exact bug this module exists to remove.
UNEVALUATED_IN_RULES_ONLY = [
    category
    for category in RISK_CATEGORIES
    if category not in set(DIMENSION_TO_CATEGORY.values())
]

RULES_ONLY_RATIONALE_PREFIX = "Identified by the deterministic risk rule engine"


def build_rules_only_factors(results: Iterable[Any]) -> list[dict[str, Any]]:
    """
    Translate RiskEngine's RiskResult dataclasses into the risk-factor
    dicts the rest of the pipeline persists.

    Only dimensions the engine actually produced become factors. A
    dimension that no rule fired for is left out entirely, for the same
    reason as UNEVALUATED_IN_RULES_ONLY above.
    """

    factors: list[dict[str, Any]] = []

    for result in results:
        category = DIMENSION_TO_CATEGORY.get(result.dimension)

        if category is None:
            # An unmapped dimension is dropped rather than guessed at.
            continue

        factors.append(
            {
                "category": category,
                "applicable": True,
                "score": float(result.score),
                "severity": result.severity,
                "indicators": [],
                "rationale": (
                    f"{RULES_ONLY_RATIONALE_PREFIX} (rule set: "
                    f"{result.dimension}), because AI analysis was "
                    f"unavailable for this run. {result.reason}"
                ),
                "misuse_scenario": None,
                # A keyword rule match, not a verified quote: the factor
                # is flagged for review and stays unrated like any other.
                "evidence": [],
                "rejected_indicators": [],
                "evidence_status": "NOT_VERIFIED",
                "missing_information": [
                    "Rules-only analysis: no evidence was extracted or "
                    "verified for this factor."
                ],
            }
        )

    return factors


def unevaluated_categories(factors: Iterable[dict[str, Any]]) -> list[str]:
    """Which of the 10 canonical categories this run did not evaluate."""

    covered = {factor.get("category") for factor in factors}
    return [category for category in RISK_CATEGORIES if category not in covered]

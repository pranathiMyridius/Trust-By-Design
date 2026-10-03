"""
Automated intake screening/triage: a deterministic, rule-based priority
score computed from the R1.1 intake fields, independent of the AI risk
engine (which only runs later, at RISK_IDENTIFICATION). This lets urgent
or high-exposure requests be flagged and routed (see app/services/routing.py)
as soon as intake is complete, rather than waiting for the full pipeline.
"""

from typing import Any

# A request accumulates points from each signal below; the total maps to
# a priority level. Kept as plain module constants (not DB-configurable)
# since this is a fast intake-time heuristic, distinct from the
# configurable RiskMethodology used for the real risk engine.
PRIORITY_THRESHOLDS = {
    "URGENT": 70,
    "HIGH": 45,
    "MEDIUM": 20,
}

HIGH_RISK_CHANGE_TYPES = {
    "NEW_GEOGRAPHY": 25,
    "THIRD_PARTY_INTRODUCTION": 20,
    "TECHNOLOGY_CHANGE": 15,
    "NEW_CUSTOMER_SEGMENT": 15,
    "TRANSACTION_LIMIT_OR_CHANNEL_CHANGE": 15,
    "NEW_PRODUCT": 10,
    "NEW_SERVICE": 10,
    "PROCESS_CHANGE": 5,
    "PERIODIC_REASSESSMENT": 0,
}

# Free-text signals in expected_transaction_volume/value that suggest a
# materially larger request. Simple case-insensitive substring checks --
# a heuristic, not a parsed numeric comparison, since these are free-text
# intake fields.
HIGH_VOLUME_SIGNALS = ("million", "mm", "high volume", "unlimited")

# A counterparty the requester flagged as a potential shell entity
# (shell_company_indicator) is a primary financial-crime red flag: it
# adds points like any other signal, and the request is never triaged
# below HIGH however benign the rest of the intake looks.
SHELL_COMPANY_POINTS = 30
SHELL_COMPANY_MIN_LEVEL = "HIGH"

_LEVEL_ORDER = ("LOW", "MEDIUM", "HIGH", "URGENT")


def _count_list_field(value: str | None) -> int:
    if not value or not value.strip():
        return 0
    return len([item for item in value.split(",") if item.strip()])


def compute_priority(data: dict[str, Any]) -> tuple[str, int]:
    """
    Returns (priority_level, priority_score) for an intake request's
    field data (as produced by AssessmentCreate/AssessmentUpdate's
    model_dump()).
    """

    score = 0

    score += HIGH_RISK_CHANGE_TYPES.get(
        str(data.get("change_type") or "").upper(), 5
    )

    # Multiple jurisdictions compounds cross-border regulatory exposure.
    countries = _count_list_field(data.get("countries_jurisdictions"))
    if countries >= 3:
        score += 20
    elif countries == 2:
        score += 10

    if (data.get("third_party_vendor_usage") or "").strip().lower() not in (
        "",
        "none",
        "n/a",
        "na",
    ):
        score += 15

    volume_text = " ".join(
        str(data.get(field) or "").lower()
        for field in (
            "expected_transaction_volume",
            "expected_transaction_value",
        )
    )
    if any(signal in volume_text for signal in HIGH_VOLUME_SIGNALS):
        score += 20

    if (data.get("technology_process_changes") or "").strip():
        score += 10

    shell_flagged = data.get("shell_company_indicator") is True
    if shell_flagged:
        score += SHELL_COMPANY_POINTS

    score = max(0, min(100, score))

    if score >= PRIORITY_THRESHOLDS["URGENT"]:
        level = "URGENT"
    elif score >= PRIORITY_THRESHOLDS["HIGH"]:
        level = "HIGH"
    elif score >= PRIORITY_THRESHOLDS["MEDIUM"]:
        level = "MEDIUM"
    else:
        level = "LOW"

    if shell_flagged and _LEVEL_ORDER.index(level) < _LEVEL_ORDER.index(SHELL_COMPANY_MIN_LEVEL):
        level = SHELL_COMPANY_MIN_LEVEL

    return level, score

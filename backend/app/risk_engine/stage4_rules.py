"""
P4 (Stage 4 acceptance criteria): fixed, deterministic risk-factor rules.

The brief fixes three outcomes, whatever the AI concludes:

  * a cross-border payment product -> geographic, product, transaction and
    sanctions-related factors are considered
  * a remote digital channel       -> channel and authentication risks are
    considered
  * a third-party processor        -> third-party risk is included

Each rule fires on plain keyword / structural signals read from the
intake fields and the structured profile, and then guarantees that its
categories are on the assessment as applicable factors. A category the AI
marked not applicable (or omitted) is put back as applicable and unrated,
with the AI's own view kept in the rationale, so an analyst has to rate
it -- or exclude it with a reason (R4.5). A rule never asserts an
indicator: indicators still need a verified quote, and a forced
SANCTIONS_EXPOSURE would trigger the CRITICAL escalation rule. "Sanctions
considered" and "authentication considered" are recorded as explicit
considerations on the factor instead.

STATUS: the rules are fixed by the brief; the *signals* that detect
"cross-border", "remote digital" and "third-party processor" are not (Q-1).
They are configuration -- the defaults below, replaceable as a whole by the
JSON file named in STAGE4_RULES_FILE -- carry a ruleset version recorded on
every factor they touch, and are PROVISIONAL pending business validation.
"""

from __future__ import annotations

import copy
import json
import os
import re
from functools import lru_cache
from typing import Any

from app.risk_engine.evidence import EvidenceStatus, normalize_text

RULESET_STATUS = "PROVISIONAL_PENDING_BUSINESS_VALIDATION"

_TEXT_FIELDS = [
    "title",
    "description",
    "evidence",
    "product_or_service_name",
    "transaction_types",
    "countries_jurisdictions",
    "delivery_channels",
    "third_party_vendor_usage",
    "technology_process_changes",
    "customer_segment",
]
_PROFILE_FIELDS = [
    "channels",
    "countries",
    "third_party_vendors",
    "payment_methods",
    "onboarding_approach",
    "transaction_origin",
    "transaction_destination",
    "technologies",
    "additional_risk_factors",
    "regulatory_considerations",
]

_NARRATIVE = ["title", "description", "evidence", "product_or_service_name"]

DEFAULT_RULESET: dict[str, Any] = {
    "version": "2026-10-02.1",
    "status": RULESET_STATUS,
    "rules": [
        {
            "rule_id": "S4-01-CROSS-BORDER-PAYMENT",
            "description": "Cross-border payment product",
            # Every group must match; a group matches on any keyword in any
            # of its fields, or any of its structural checks.
            "signal_groups": [
                {
                    "name": "cross_border",
                    "fields": _NARRATIVE
                    + ["transaction_types", "countries_jurisdictions", "transaction_origin", "transaction_destination",
                       "additional_risk_factors", "regulatory_considerations"],
                    "keywords": ["cross-border", "cross border", "international", "overseas", "foreign",
                                 "remittance*", "correspondent", "multi-currency", "fx"],
                    "checks": ["multiple_countries", "origin_differs_from_destination"],
                },
                {
                    "name": "payment",
                    "fields": _NARRATIVE + ["transaction_types", "payment_methods"],
                    "keywords": ["payment*", "transfer*", "remittance*", "settlement*", "acquiring", "wire*",
                                 "payout*", "card*", "money transmission"],
                    "checks": [],
                },
            ],
            "required": [
                {"category": "GEOGRAPHIC_RISK", "considerations": [
                    {"indicator": "SANCTIONS_EXPOSURE",
                     "text": "Sanctions exposure: confirm whether any counterparty, corridor or jurisdiction is subject to sanctions."},
                ]},
                {"category": "PRODUCT_SERVICE_RISK", "considerations": []},
                {"category": "TRANSACTION_ACTIVITY_RISK", "considerations": []},
            ],
        },
        {
            "rule_id": "S4-02-REMOTE-DIGITAL-CHANNEL",
            "description": "Remote digital delivery channel",
            "signal_groups": [
                {
                    "name": "remote_digital",
                    "fields": ["delivery_channels", "channels", "onboarding_approach", "description", "evidence",
                               "technology_process_changes"],
                    "keywords": ["online", "mobile", "app", "web", "website", "digital", "remote*", "internet", "api",
                                 "portal", "non-face-to-face", "e-kyc", "ekyc", "video identification"],
                    "checks": [],
                },
            ],
            "required": [
                {"category": "DELIVERY_CHANNEL_RISK", "considerations": [
                    {"indicator": None, "text": "Channel risk: misuse of the remote channel without face-to-face contact."},
                    {"indicator": "REMOTE_ONBOARDING",
                     "text": "Authentication risk: how customers are identified and authenticated remotely."},
                ]},
            ],
        },
        {
            "rule_id": "S4-03-THIRD-PARTY-PROCESSOR",
            "description": "Third-party processor or vendor",
            "signal_groups": [
                {
                    "name": "third_party",
                    "fields": ["third_party_vendor_usage", "third_party_vendors", "description", "evidence",
                               "technology_process_changes"],
                    "keywords": ["processor*", "third party", "third-party", "vendor*", "outsourc*",
                                 "service provider*", "partner*"],
                    "checks": ["vendor_present"],
                },
            ],
            "required": [
                {"category": "THIRD_PARTY_VENDOR_RISK", "considerations": [
                    {"indicator": "THIRD_PARTY_DEPENDENCIES",
                     "text": "Third-party risk: the processor's controls, oversight and dependency."},
                ]},
            ],
        },
    ],
}

_NONE_VALUES = {"", "none", "n/a", "na", "no", "not applicable", "nil"}


@lru_cache(maxsize=1)
def _loaded() -> dict[str, Any]:
    path = os.getenv("STAGE4_RULES_FILE")
    if path:
        with open(path, encoding="utf-8") as handle:
            ruleset = json.load(handle)
        ruleset.setdefault("status", RULESET_STATUS)
        return ruleset
    return copy.deepcopy(DEFAULT_RULESET)


def ruleset() -> dict[str, Any]:
    return copy.deepcopy(_loaded())


def reload() -> None:
    _loaded.cache_clear()


def _as_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, list):
        return "; ".join(str(item) for item in value)
    if isinstance(value, str) and value.startswith("["):
        try:
            parsed = json.loads(value)
            if isinstance(parsed, list):
                return "; ".join(str(item) for item in parsed)
        except ValueError:
            pass
    return str(value)


def signal_texts(assessment: dict[str, Any], intelligence: Any = None) -> dict[str, str]:
    """field -> text the signals are matched against."""

    texts = {field: _as_text(assessment.get(field)) for field in _TEXT_FIELDS}
    if intelligence is not None:
        for field in _PROFILE_FIELDS:
            if hasattr(intelligence, "get_list") and field in {
                "channels", "countries", "third_party_vendors", "payment_methods", "technologies",
                "additional_risk_factors", "regulatory_considerations",
            }:
                value = intelligence.get_list(field)
            else:
                value = intelligence.get(field) if isinstance(intelligence, dict) else getattr(intelligence, field, None)
            texts[field] = _as_text(value)
    return {field: text for field, text in texts.items() if text.strip()}


def _keyword_pattern(keyword: str) -> re.Pattern:
    prefix = keyword.endswith("*")
    word = normalize_text(keyword.rstrip("*"))
    return re.compile(r"(?<![0-9a-z])" + re.escape(word) + ("" if prefix else r"(?![0-9a-z])"))


def _countries(texts: dict[str, str]) -> set[str]:
    names = set()
    for field in ("countries_jurisdictions", "countries"):
        for item in re.split(r"[;,/]|\band\b", texts.get(field, "")):
            item = normalize_text(item)
            if item and item not in _NONE_VALUES:
                names.add(item)
    return names


def _check(name: str, texts: dict[str, str]) -> str | None:
    """A structural signal: the matched text, or None."""

    if name == "multiple_countries":
        countries = _countries(texts)
        return ", ".join(sorted(countries)) if len(countries) >= 2 else None
    if name == "origin_differs_from_destination":
        origin = normalize_text(texts.get("transaction_origin", ""))
        destination = normalize_text(texts.get("transaction_destination", ""))
        return f"{origin} -> {destination}" if origin and destination and origin != destination else None
    if name == "vendor_present":
        for field in ("third_party_vendors", "third_party_vendor_usage"):
            value = normalize_text(texts.get(field, ""))
            if value and value not in _NONE_VALUES:
                return texts[field]
        return None
    return None


def _group_signals(group: dict[str, Any], texts: dict[str, str]) -> list[dict[str, str]]:
    found = []
    for field in group.get("fields", []):
        text = texts.get(field)
        if not text:
            continue
        normalized = normalize_text(text)
        for keyword in group.get("keywords", []):
            match = _keyword_pattern(keyword).search(normalized)
            if match:
                start = max(0, match.start() - 30)
                found.append({"group": group["name"], "field": field, "keyword": keyword.rstrip("*"),
                              "context": normalized[start: match.end() + 30].strip()})
                break
    for check in group.get("checks", []):
        matched = _check(check, texts)
        if matched:
            found.append({"group": group["name"], "field": check, "keyword": None, "context": matched})
    return found


def evaluate(assessment: dict[str, Any], intelligence: Any = None) -> list[dict[str, Any]]:
    """The rules that fire, each with the signals that made it fire."""

    rules = ruleset()
    texts = signal_texts(assessment, intelligence)
    fired = []
    for rule in rules["rules"]:
        signals = []
        for group in rule["signal_groups"]:
            group_signals = _group_signals(group, texts)
            if not group_signals:
                break
            signals.extend(group_signals)
        else:
            fired.append({**rule, "signals": signals, "ruleset_version": rules["version"], "ruleset_status": rules["status"]})
    return fired


def _blank_factor(category: str) -> dict[str, Any]:
    return {
        "category": category,
        "applicable": True,
        "score": 0.0,
        "severity": "LOW",
        "indicators": [],
        "rationale": "",
        "misuse_scenario": None,
        "evidence": [],
        "rejected_indicators": [],
        "evidence_status": EvidenceStatus.INSUFFICIENT_EVIDENCE,
        "missing_information": [],
        "verified_quote_count": 0,
        "rejected_quote_count": 0,
    }


def apply(factors: list[dict[str, Any]], assessment: dict[str, Any], intelligence: Any = None) -> list[dict[str, Any]]:
    """
    Applies the fired rules to a factor set (in place, also returned) and
    returns the list of {rule_id, category, effect} applied. Categories a
    rule requires end up applicable; each carries `rule_triggers`.
    """

    applied: list[dict[str, Any]] = []
    by_category = {factor["category"]: factor for factor in factors}

    for rule in evaluate(assessment, intelligence):
        for requirement in rule["required"]:
            category = requirement["category"]
            factor = by_category.get(category)
            label = f"fixed Stage 4 rule {rule['rule_id']} ({rule['description']})"

            if factor is None:
                factor = _blank_factor(category)
                factor["rationale"] = f"Required by {label}. The analysis returned no assessment for this category."
                # What the analysis itself concluded, kept apart from the rule's
                # effect (the AI evaluation measures the AI, not the rules).
                factor["analysis_applicable"] = False
                factors.append(factor)
                by_category[category] = factor
                effect = "ADDED"
            elif not factor.get("applicable"):
                original = (factor.get("rationale") or "").strip()
                factor["analysis_applicable"] = False
                factor["analysis_rationale"] = original
                factor["applicable"] = True
                factor["evidence_status"] = EvidenceStatus.INSUFFICIENT_EVIDENCE
                factor["rationale"] = (
                    f"Required by {label}. The analysis had assessed this category as not applicable"
                    + (f": {original}" if original else ".")
                )
                factor.setdefault("indicators", [])
                effect = "FORCED_APPLICABLE"
            else:
                effect = "CONFIRMED"

            present = set(factor.get("indicators") or [])
            considerations = [
                item for item in requirement.get("considerations", []) if not item.get("indicator") or item["indicator"] not in present
            ]
            missing = list(factor.get("missing_information") or [])
            for item in considerations:
                note = f"Stage 4 rule {rule['rule_id']}: consider {item['text']}"
                if note not in missing:
                    missing.append(note)
            factor["missing_information"] = missing

            trigger = {
                "rule_id": rule["rule_id"],
                "description": rule["description"],
                "ruleset_version": rule["ruleset_version"],
                "ruleset_status": rule["ruleset_status"],
                "effect": effect,
                "signals": rule["signals"],
                "considerations": considerations,
            }
            factor.setdefault("rule_triggers", [])
            if not any(t["rule_id"] == trigger["rule_id"] for t in factor["rule_triggers"]):
                factor["rule_triggers"].append(trigger)
            applied.append({"rule_id": rule["rule_id"], "category": category, "effect": effect})

    return applied

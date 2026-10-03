"""
P3 (R-GOV-04): override materiality, classified by the server from the
actual effect of the change -- never from a label the user chooses.

  CRITICAL     moves a risk band by two or more levels, lowers a HIGH or
               CRITICAL band, or adds/removes an escalation indicator
  MATERIAL     changes a risk band, a factor's band, applicability, a
               category, control effectiveness or mapping, a decision
               condition, or an intake field that feeds triage/escalation
  NONMATERIAL  none of those (e.g. rationale wording, a control's owner
               text, a score inside the same band)

Rules are configurable (app/governance/policy.py; PROVISIONAL).
"""

from __future__ import annotations

import json
import re

from app.governance.policy import policy

NONMATERIAL = "NONMATERIAL"
MATERIAL = "MATERIAL"
CRITICAL = "CRITICAL"

BAND_ORDER = {"LOW": 0, "MEDIUM": 1, "HIGH": 2, "CRITICAL": 3}
_BAND_IN_TEXT = re.compile(r"\b(LOW|MEDIUM|HIGH|CRITICAL)\b")


def band_of(value) -> str | None:
    """The risk band named in a value such as '70 (HIGH)' or 'HIGH'."""

    if value is None:
        return None
    match = _BAND_IN_TEXT.search(str(value).upper())
    return match.group(1) if match else None


def _band_change(before: str | None, after: str | None, critical_allowed: bool = True) -> tuple[str, list[str]]:
    if before is None or after is None or before == after:
        return NONMATERIAL, [f"risk band unchanged ({before or 'n/a'})"]
    steps = abs(BAND_ORDER[after] - BAND_ORDER[before])
    lowers_high = BAND_ORDER[after] < BAND_ORDER[before] and before in {"HIGH", "CRITICAL"}
    if critical_allowed and (steps >= 2 or lowers_high):
        why = f"risk band {before} -> {after}"
        why += " (two or more levels)" if steps >= 2 else " (lowers a high-risk band)"
        return CRITICAL, [why]
    return MATERIAL, [f"risk band {before} -> {after}"]


def _factor_band(likelihood_x_impact) -> str | None:
    from app.risk_engine.scoring import compute_factor_score, determine_risk_band

    match = re.match(r"\s*(\d+)\s*x\s*(\d+)\s*$", str(likelihood_x_impact or ""))
    if not match:
        return None
    return determine_risk_band(compute_factor_score(int(match.group(1)), int(match.group(2))))


def _escalation_indicators(db) -> set[str]:
    indicators = set(policy()["critical_indicators"])
    if db is None:
        return indicators
    try:
        from app.risk_engine.methodology import get_methodology_config

        for rule in get_methodology_config(db).get("escalation_rules", []) or []:
            condition = rule.get("condition", {}) if isinstance(rule, dict) else {}
            if condition.get("indicator"):
                indicators.add(condition["indicator"])
    except Exception:  # noqa: BLE001 -- classification falls back to the defaults
        pass
    return indicators


def _as_list(value) -> list:
    if isinstance(value, list):
        return value
    try:
        parsed = json.loads(value) if value else []
    except (TypeError, ValueError):
        return []
    return parsed if isinstance(parsed, list) else []


def classify(section: str, field_name: str, system_value, human_value, db=None) -> tuple[str, list[str]]:
    """(level, reasons) for a change of `field_name` in `section` from
    `system_value` to `human_value`."""

    rules = policy()
    field = (field_name or "").strip()

    if section in {"INHERENT_RISK", "RESIDUAL_RISK"}:
        return _band_change(band_of(system_value), band_of(human_value))

    if section == "FCRM_REVIEW":
        return _band_change(band_of(system_value), band_of(human_value), critical_allowed=False)

    if section == "FACTOR_RATING":
        if field in {"likelihood", "impact", "score"} or "likelihood x impact" in field:
            before = _factor_band(system_value) if "x" in str(system_value) else band_of(system_value)
            after = _factor_band(human_value) if "x" in str(human_value) else band_of(human_value)
            if before is None or after is None:
                return MATERIAL, ["factor rating changed (band could not be compared)"]
            return _band_change(before, after, critical_allowed=False)
        if field == "severity":
            return _band_change(band_of(system_value), band_of(human_value), critical_allowed=False)
        return MATERIAL, [f"factor {field} changed"]

    if section == "RISK_CATEGORY":
        if field == "indicators":
            changed = set(_as_list(system_value)) ^ set(_as_list(human_value))
            critical = changed & _escalation_indicators(db)
            if critical:
                return CRITICAL, [f"escalation indicator(s) changed: {', '.join(sorted(critical))}"]
            return MATERIAL, ["risk indicators changed"]
        return MATERIAL, [f"risk {field or 'category'} changed (scope of the assessment)"]

    if section == "RISK_RATIONALE":
        return NONMATERIAL, ["wording of the rationale only; no score, band, control or escalation effect"]

    if section == "CONTROL_EFFECTIVENESS":
        return MATERIAL, [f"control {field} changed (control effectiveness / residual risk)"]

    if section == "CONTROL_MAPPING":
        fields = {f.strip() for f in field.split(",") if f.strip()}
        material = fields & set(rules["material_control_fields"])
        if material:
            return MATERIAL, [f"control mapping changed: {', '.join(sorted(material))}"]
        return NONMATERIAL, ["control metadata only (no mapping or effectiveness effect)"]

    if section == "CONDITION":
        return MATERIAL, ["a decision condition changed"]

    if section == "INTAKE_FIELD":
        if field in set(rules["material_intake_fields"]):
            return MATERIAL, [f"intake field {field} feeds triage, scoping or escalation"]
        return NONMATERIAL, [f"intake field {field} has no scoring or escalation effect"]

    return MATERIAL, [f"unclassified section {section} treated as material"]

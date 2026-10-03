from typing import Any, Optional


# Built-in fallback thresholds, used only when no active RiskMethodology
# row exists in the database (see app/risk_engine/methodology.py).
#
# A score at or above a threshold gets that level; below all thresholds
# is LOW. Checked in descending order.
DEFAULT_RISK_THRESHOLDS = {
    "CRITICAL": 80,
    "HIGH": 60,
    "MEDIUM": 40,
}


# ---------------------------------------------------------------------------
# Stage 6 (R6.1-R6.7): configurable, deterministic inherent-risk methodology.
#
# The AI (app/ai/risk_factor_analyzer.py) identifies which of the 10
# canonical risk categories apply, their indicators, rationale and misuse
# scenario, and may additionally *suggest* a likelihood/impact rating
# (app/ai/likelihood_impact_analyzer.py) to pre-fill the analyst's form.
# A suggestion is stored separately on the factor and is never an input
# here: only an analyst's own rating, confirming or overriding it, feeds
# this calculation. Everything from there (factor score, weighting,
# overall score, risk band, mandatory escalation) is plain deterministic
# arithmetic, never LLM output.
# ---------------------------------------------------------------------------

# Equal weighting by default -- an authorized admin can configure real
# weights per category via RiskMethodology.factor_weights (R6.1).
DEFAULT_FACTOR_WEIGHTS = {
    "PRODUCT_SERVICE_RISK": 0.10,
    "CUSTOMER_SEGMENT_RISK": 0.10,
    "GEOGRAPHIC_RISK": 0.10,
    "DELIVERY_CHANNEL_RISK": 0.10,
    "TRANSACTION_ACTIVITY_RISK": 0.10,
    "TECHNOLOGY_DEVELOPMENT_RISK": 0.10,
    "THIRD_PARTY_VENDOR_RISK": 0.10,
    "OWNERSHIP_ENTITY_COMPLEXITY_RISK": 0.10,
    "FINANCIAL_CRIME_TYPOLOGY_RISK": 0.10,
    "CONTROL_ENVIRONMENT_RISK": 0.10,
}

DEFAULT_LIKELIHOOD_SCALE = [
    {"value": 1, "label": "Rare"},
    {"value": 2, "label": "Unlikely"},
    {"value": 3, "label": "Possible"},
    {"value": 4, "label": "Likely"},
    {"value": 5, "label": "Almost Certain"},
]

DEFAULT_IMPACT_SCALE = [
    {"value": 1, "label": "Negligible"},
    {"value": 2, "label": "Minor"},
    {"value": 3, "label": "Moderate"},
    {"value": 4, "label": "Major"},
    {"value": 5, "label": "Severe"},
]

# Ordered low -> high. A band's range is inclusive of min, exclusive of the
# next band's min (the last band's max is inclusive).
DEFAULT_RISK_BANDS = [
    {"name": "LOW", "min": 0, "max": 39},
    {"name": "MEDIUM", "min": 40, "max": 59},
    {"name": "HIGH", "min": 60, "max": 79},
    {"name": "CRITICAL", "min": 80, "max": 100},
]

# R6.6: the policy rule library -- conditions that force at least a given
# band regardless of the weighted-average score, so a single severe
# factor can't be diluted away by averaging, but only when an approved
# rule explicitly says so (never silently, and never from a single
# factor's weight alone). Rules are data: an active RiskMethodology's
# escalation_rules replaces this list wholesale.
#
# Rule record fields (see normalize_rule for legacy shapes):
#   rule_code        stable identifier, shown wherever the rule fires
#   rule_type        "override"      -- a hard regulatory outcome
#                    "minimum_band"  -- a floor on the overall band
#   status           only "approved" rules are applied; "draft" and
#                    "disabled" are carried in the config but ignored
#   version          the rule's own version, recorded when it fires
#   condition        one of:
#                      {"indicator": CODE}  any applicable factor carries
#                        that indicator (for AI factors, only indicators
#                        backed by a verified quote are stored at all)
#                      {"categories": [...], "min_factor_band": BAND}
#                        an analyst-rated factor in one of those
#                        categories scored at or above BAND
#                      {"jurisdiction_tiers": [...]}  a country named on
#                        the assessment carries one of these designations
#                        in a verified reference-data snapshot
#   min_band         the band the overall result is raised to, at least
#   mandatory_review whether firing requires a human reviewer
#   non_mitigable    residual risk may not fall below min_band either
DEFAULT_ESCALATION_RULES = [
    {
        "rule_code": "SANCTIONS_EXPOSURE_001",
        "rule_type": "override",
        "status": "approved",
        "version": "1.0",
        "description": (
            "Any factor flagged with the SANCTIONS_EXPOSURE indicator "
            "forces at least a CRITICAL rating."
        ),
        "condition": {"indicator": "SANCTIONS_EXPOSURE"},
        "min_band": "CRITICAL",
        "mandatory_review": True,
        # Controls cannot lower the residual below min_band.
        "non_mitigable": True,
    },
    {
        # Proposed policy, not yet approved: a HIGH rating on a factor
        # that carries material regulatory exposure sets the overall
        # floor. Deliberately limited to key factors -- a HIGH on, say,
        # technology change is not the same kind of finding -- and ships
        # as draft until a compliance owner approves it.
        "rule_code": "MIN_BAND_KEY_FACTOR_001",
        "rule_type": "minimum_band",
        "status": "draft",
        "version": "1.0",
        "description": (
            "A key factor (geographic, customer segment, or ownership / "
            "entity complexity) rated HIGH or above sets an overall floor "
            "of HIGH."
        ),
        "condition": {
            "categories": [
                "GEOGRAPHIC_RISK",
                "CUSTOMER_SEGMENT_RISK",
                "OWNERSHIP_ENTITY_COMPLEXITY_RISK",
            ],
            "min_factor_band": "HIGH",
        },
        "min_band": "HIGH",
        "mandatory_review": True,
    },
]

RULE_TYPES = {"override", "minimum_band"}
RULE_STATUSES = {"approved", "draft", "disabled"}

# Reference-data rules (condition {"jurisdiction_tiers": [...]}) fire on
# the assessment's countries_jurisdictions resolved against *verified*
# country-risk snapshots only -- see app/services/reference_data_service.py.
# They ship as draft: which designations set which floor is policy for a
# compliance owner to approve, not an engineering default.
DEFAULT_ESCALATION_RULES += [
    {
        "rule_code": "GEO_FATF_CALL_FOR_ACTION_001",
        "rule_type": "override",
        "status": "draft",
        "version": "1.0",
        "description": (
            "Exposure to a jurisdiction on the FATF call-for-action list "
            "forces a CRITICAL rating that controls cannot lower."
        ),
        "condition": {"jurisdiction_tiers": ["CALL_FOR_ACTION"]},
        "min_band": "CRITICAL",
        "mandatory_review": True,
        "non_mitigable": True,
    },
    {
        "rule_code": "GEO_HIGH_RISK_THIRD_COUNTRY_001",
        "rule_type": "minimum_band",
        "status": "draft",
        "version": "1.0",
        "description": (
            "Exposure to a jurisdiction under FATF increased monitoring or "
            "on the EU high-risk third-country list sets an overall floor "
            "of HIGH."
        ),
        "condition": {
            "jurisdiction_tiers": [
                "INCREASED_MONITORING",
                "EU_ONGOING_SUBSTANTIAL_RISK",
                "EU_TECHNICAL_ASSISTANCE",
                "EU_FATF_MEMBERSHIP_SUSPENDED",
                "EU_COMMITMENT_ACTION_PLAN",
            ]
        },
        "min_band": "HIGH",
        "mandatory_review": True,
    },
]

# Categories assessed as mitigants rather than inherent risk. The control
# environment describes how well risk is managed, which is what the
# control rating and residual grid measure; counting it again inside the
# inherent average would double-count controls. Such factors stay visible
# (and feed the controls stage) but take no part in the inherent score.
DEFAULT_MITIGANT_CATEGORIES = ["CONTROL_ENVIRONMENT_RISK"]

# Control-effectiveness ratings, ordered weakest -> strongest. The overall
# rating for an assessment is its weakest inherent factor's rating (see
# app/control_engine/scoring.py::overall_control_rating).
CONTROL_RATINGS = ["WEAK", "PARTIAL", "EFFECTIVE"]

# Residual lookup grid: inherent band x control rating -> residual band.
# Versioned with the methodology. Controls only ever mitigate, so no cell
# may exceed its inherent band (enforced by validate_residual_grid and
# asserted on every lookup); weak or absent controls leave risk unchanged.
DEFAULT_RESIDUAL_GRID = {
    "version": "1.0",
    "cells": {
        "LOW": {"WEAK": "LOW", "PARTIAL": "LOW", "EFFECTIVE": "LOW"},
        "MEDIUM": {"WEAK": "MEDIUM", "PARTIAL": "MEDIUM", "EFFECTIVE": "LOW"},
        "HIGH": {"WEAK": "HIGH", "PARTIAL": "HIGH", "EFFECTIVE": "MEDIUM"},
        "CRITICAL": {"WEAK": "CRITICAL", "PARTIAL": "CRITICAL", "EFFECTIVE": "HIGH"},
    },
}

DEFAULT_REQUIRED_APPROVALS = {
    "LOW": ["ANALYST"],
    "MEDIUM": ["ANALYST", "REVIEWER"],
    "HIGH": ["REVIEWER", "COMMITTEE"],
    "CRITICAL": ["COMMITTEE"],
}

_BAND_ORDER = ["LOW", "MEDIUM", "HIGH", "CRITICAL"]


def compute_factor_score(
    likelihood: float,
    impact: float,
    likelihood_scale: Optional[list[dict[str, Any]]] = None,
    impact_scale: Optional[list[dict[str, Any]]] = None,
) -> float:
    """
    Deterministically turns a manually-rated likelihood x impact pair
    into a 0-100 factor score, scaled by the configured scales' maximum
    values. No AI/LLM involvement (R6.2).
    """

    likelihood_scale = likelihood_scale or DEFAULT_LIKELIHOOD_SCALE
    impact_scale = impact_scale or DEFAULT_IMPACT_SCALE

    max_likelihood = max((item["value"] for item in likelihood_scale), default=5)
    max_impact = max((item["value"] for item in impact_scale), default=5)
    max_raw = max_likelihood * max_impact

    if max_raw <= 0:
        return 0.0

    raw = float(likelihood) * float(impact)
    score = (raw / max_raw) * 100.0

    return round(max(0.0, min(100.0, score)), 2)


def determine_risk_band(
    score: float,
    risk_bands: Optional[list[dict[str, Any]]] = None,
) -> str:
    """
    Configurable replacement for determine_risk_level: looks the score up
    in an ordered list of named bands ({"name", "min", "max"}) instead of
    a fixed threshold map, so bands themselves (not just cutoffs) are
    configurable (R6.1).
    """

    risk_bands = risk_bands or DEFAULT_RISK_BANDS

    for band in sorted(risk_bands, key=lambda b: b["min"], reverse=True):
        if score >= band["min"]:
            return band["name"]

    return risk_bands[0]["name"] if risk_bands else "LOW"


def _band_rank(band_name: str, risk_bands: list[dict[str, Any]]) -> int:
    names = [band["name"] for band in sorted(risk_bands, key=lambda b: b["min"])]
    try:
        return names.index(band_name)
    except ValueError:
        return _BAND_ORDER.index(band_name) if band_name in _BAND_ORDER else 0


def normalize_rule(rule: dict[str, Any]) -> dict[str, Any]:
    """
    Brings a rule into the rule-record shape documented above
    DEFAULT_ESCALATION_RULES. Rules saved before the rule library existed
    ({"id", "indicator" | "category" + "min_score", "min_band"}) were live
    configuration, so they are read as approved minimum_band rules and
    behave exactly as they did.
    """

    if "rule_code" in rule and isinstance(rule.get("condition"), dict):
        normalized = dict(rule)
        normalized.setdefault("rule_type", "minimum_band")
        normalized.setdefault("status", "draft")
        normalized.setdefault("version", "1.0")
        normalized.setdefault("mandatory_review", False)
        normalized.setdefault("non_mitigable", False)
        normalized.setdefault("min_band", "CRITICAL")
        return normalized

    condition: dict[str, Any] = {}
    if rule.get("indicator"):
        condition["indicator"] = rule["indicator"]
    if rule.get("category"):
        condition["categories"] = [rule["category"]]
        if rule.get("min_score") is not None:
            condition["min_factor_score"] = rule["min_score"]

    return {
        "rule_code": rule.get("id") or rule.get("rule_code") or "LEGACY_RULE",
        "rule_type": "minimum_band",
        "status": "approved",
        "version": "legacy",
        "description": rule.get("description"),
        "condition": condition,
        "min_band": rule.get("min_band", "CRITICAL"),
        "mandatory_review": False,
        "non_mitigable": False,
    }


def validate_rule(rule: dict[str, Any], risk_bands: Optional[list[dict[str, Any]]] = None) -> list[str]:
    """Problems with a rule record, for the methodology API to reject."""

    from app.schemas.risk_factor import RISK_CATEGORIES, RISK_INDICATORS

    risk_bands = risk_bands or DEFAULT_RISK_BANDS
    band_names = {band["name"] for band in risk_bands}
    normalized = normalize_rule(rule)
    condition = normalized.get("condition") or {}
    code = normalized.get("rule_code")
    errors: list[str] = []

    if normalized["rule_type"] not in RULE_TYPES:
        errors.append(f"{code}: rule_type must be one of {sorted(RULE_TYPES)}")
    if normalized["status"] not in RULE_STATUSES:
        errors.append(f"{code}: status must be one of {sorted(RULE_STATUSES)}")
    if normalized["min_band"] not in band_names:
        errors.append(f"{code}: min_band must be one of {sorted(band_names)}")

    indicator = condition.get("indicator")
    categories = condition.get("categories")
    tiers = condition.get("jurisdiction_tiers")
    if not indicator and not categories and not tiers:
        errors.append(f"{code}: condition needs an indicator, categories or jurisdiction_tiers")
    if tiers:
        from app.services.country_risk_service import TIER_META

        for tier in tiers:
            if tier not in TIER_META:
                errors.append(f"{code}: unknown jurisdiction tier {tier}")
    if indicator and indicator not in RISK_INDICATORS:
        errors.append(f"{code}: unknown indicator {indicator}")
    for category in categories or []:
        if category not in RISK_CATEGORIES:
            errors.append(f"{code}: unknown category {category}")
    if condition.get("min_factor_band") and condition["min_factor_band"] not in band_names:
        errors.append(f"{code}: min_factor_band must be one of {sorted(band_names)}")

    return errors


def _rule_triggers(
    condition: dict[str, Any],
    factors: list[dict[str, Any]],
    risk_bands: list[dict[str, Any]],
    jurisdiction_matches: Optional[list[dict[str, Any]]] = None,
) -> list[str]:
    """
    What satisfied a rule's condition: factor categories, or for
    jurisdiction rules "<ISO>:<tier> (<source>)" per matching designation.
    """

    tiers = condition.get("jurisdiction_tiers")
    if tiers:
        return [
            f"{match['iso_code']}:{match['tier']} ({match.get('source')})"
            for match in jurisdiction_matches or []
            if match.get("tier") in tiers
        ]

    indicator = condition.get("indicator")
    categories = condition.get("categories")
    min_factor_band = condition.get("min_factor_band")
    min_factor_score = condition.get("min_factor_score")
    hits: list[str] = []

    for factor in factors:
        if indicator:
            if indicator in (factor.get("indicators") or []):
                hits.append(factor.get("category"))
            continue

        if categories and factor.get("category") not in categories:
            continue

        if min_factor_band or min_factor_score is not None:
            # A threshold on a factor's rating can only be met by a
            # rating: an unrated factor's placeholder 0 says nothing.
            if not factor.get("rated"):
                continue
            score = float(factor.get("score", 0) or 0)
            if min_factor_score is not None and score < float(min_factor_score):
                continue
            if min_factor_band and _band_rank(
                determine_risk_band(score, risk_bands), risk_bands
            ) < _band_rank(min_factor_band, risk_bands):
                continue

        hits.append(factor.get("category"))

    return hits


def calculate_inherent_risk(
    factors: list[dict[str, Any]],
    weights: Optional[dict[str, float]] = None,
    risk_bands: Optional[list[dict[str, Any]]] = None,
    escalation_rules: Optional[list[dict[str, Any]]] = None,
    mitigant_categories: Optional[list[str]] = None,
    jurisdiction_matches: Optional[list[dict[str, Any]]] = None,
) -> dict[str, Any]:
    """
    R6.2-R6.6: the single deterministic entry point for the official
    inherent-risk score. assessment.overall_score/risk_level and every
    InherentRiskCalculation row come from here and nowhere else.

    `factors` is a list of dicts with at least: category, applicable,
    excluded, score, rated (whether likelihood+impact have actually been
    set by an analyst), indicators, and optionally evidence_status.

    Order of operations:
      1. weighted average of the *rated* applicable factors' scores
      2. initial band from that score
      3. approved override and minimum-band rules, each recorded
      4. provisional if any applicable factor is unrated

    Unrated factors are left out of the average rather than counted as 0:
    a factor nobody has rated -- including one the AI could find no
    verified evidence for -- is unknown, not low risk. With applicable
    factors but none rated there is no score at all (final_score None).

    Factors in `mitigant_categories` (the control environment) are
    assessed as controls, not inherent risk: they take no part in the
    average, the provisional check or the rules. `jurisdiction_matches`
    are designations from verified reference-data snapshots for the
    countries on the assessment, used by jurisdiction_tiers rules.
    """

    weights = weights or DEFAULT_FACTOR_WEIGHTS
    risk_bands = risk_bands or DEFAULT_RISK_BANDS
    escalation_rules = escalation_rules if escalation_rules is not None else DEFAULT_ESCALATION_RULES
    mitigants = set(
        mitigant_categories if mitigant_categories is not None else DEFAULT_MITIGANT_CATEGORIES
    )

    applicable_factors = [
        factor
        for factor in factors
        if factor.get("applicable")
        and not factor.get("excluded")
        and factor.get("category") not in mitigants
    ]

    breakdown = []
    weighted_sum = 0.0
    total_weight = 0.0
    rated_count = 0
    is_provisional = False

    for factor in applicable_factors:
        category = factor.get("category")
        weight = weights.get(category, 0.0)
        rated = bool(factor.get("rated"))
        score = max(0.0, min(100.0, float(factor.get("score", 0) or 0)))

        if rated:
            rated_count += 1
            weighted_sum += score * weight
            total_weight += weight
        else:
            is_provisional = True

        breakdown.append(
            {
                "category": category,
                "weight": weight,
                "likelihood": factor.get("likelihood"),
                "impact": factor.get("impact"),
                "score": score,
                "rated": rated,
                "evidence_status": factor.get("evidence_status"),
            }
        )

    # R6.6: prevented by construction -- the weighted average never lets
    # one factor decide the outcome on its own unless a rule (below)
    # explicitly overrides it.
    if total_weight > 0:
        final_score: Optional[float] = round(weighted_sum / total_weight, 2)
    elif applicable_factors:
        final_score = None
    else:
        # Every category considered and found not applicable, each with a
        # rationale: a genuine finding of no inherent risk.
        final_score = 0.0

    band: Optional[str] = (
        determine_risk_band(final_score, risk_bands) if final_score is not None else None
    )

    escalated = False
    mandatory_review = False
    escalation_reasons: list[str] = []
    triggered_rules: list[dict[str, Any]] = []

    for raw_rule in escalation_rules:
        rule = normalize_rule(raw_rule)
        if rule["status"] != "approved":
            continue

        hits = _rule_triggers(
            rule.get("condition") or {}, applicable_factors, risk_bands, jurisdiction_matches
        )
        if not hits:
            continue

        min_band = rule["min_band"]
        band_before = band
        if band is None or _band_rank(min_band, risk_bands) > _band_rank(band, risk_bands):
            band = min_band

        escalated = True
        mandatory_review = mandatory_review or bool(rule.get("mandatory_review"))
        escalation_reasons.append(
            rule.get("description") or rule["rule_code"] or "Mandatory escalation rule triggered."
        )
        triggered_rules.append(
            {
                "rule_code": rule["rule_code"],
                "rule_type": rule["rule_type"],
                "version": rule.get("version"),
                "description": rule.get("description"),
                "min_band": min_band,
                "band_before": band_before,
                "band_after": band,
                "raised_band": band != band_before,
                "mandatory_review": bool(rule.get("mandatory_review")),
                "non_mitigable": bool(rule.get("non_mitigable")),
                "triggered_by": hits,
            }
        )

    return {
        "final_score": final_score,
        "risk_band": band,
        "is_provisional": is_provisional,
        "rated_factor_count": rated_count,
        "applicable_factor_count": len(applicable_factors),
        "escalated": escalated,
        "escalation_reasons": escalation_reasons,
        "triggered_rules": triggered_rules,
        "mandatory_review": mandatory_review,
        "breakdown": breakdown,
        "mitigant_categories": sorted(mitigants),
    }


def validate_residual_grid(
    grid: dict[str, Any],
    risk_bands: Optional[list[dict[str, Any]]] = None,
) -> list[str]:
    """A grid must cover every band x rating, and never raise risk."""

    risk_bands = risk_bands or DEFAULT_RISK_BANDS
    errors: list[str] = []
    cells = grid.get("cells") or {}

    for band in (b["name"] for b in risk_bands):
        row = cells.get(band)
        if not isinstance(row, dict):
            errors.append(f"residual grid has no row for inherent band {band}")
            continue
        for rating in CONTROL_RATINGS:
            residual = row.get(rating)
            if residual is None:
                errors.append(f"residual grid has no cell for {band} x {rating}")
            elif _band_rank(residual, risk_bands) > _band_rank(band, risk_bands):
                errors.append(
                    f"residual grid {band} x {rating} -> {residual} exceeds the inherent band; "
                    "controls can only mitigate"
                )

    return errors


def calculate_residual_risk(
    inherent_band: Optional[str],
    control_rating: Optional[str],
    triggered_rules: Optional[list[dict[str, Any]]] = None,
    residual_grid: Optional[dict[str, Any]] = None,
    risk_bands: Optional[list[dict[str, Any]]] = None,
) -> dict[str, Any]:
    """
    Residual band = grid[inherent band][control rating], then raised to
    any non-mitigable rule's min_band. Pure lookup, no arithmetic on
    scores: the grid is the approved policy, readable in one table.

    Returns residual_band None (with a reason) when there is no inherent
    band or no control rating to look up -- never a guessed band.
    """

    residual_grid = residual_grid or DEFAULT_RESIDUAL_GRID
    risk_bands = risk_bands or DEFAULT_RISK_BANDS

    result: dict[str, Any] = {
        "inherent_band": inherent_band,
        "control_rating": control_rating,
        "grid_version": residual_grid.get("version"),
        "grid_band": None,
        "residual_band": None,
        "floors_applied": [],
        "reason": None,
    }

    if inherent_band is None or inherent_band not in {b["name"] for b in risk_bands}:
        result["reason"] = "There is no inherent risk band to apply controls to."
        return result
    if control_rating not in CONTROL_RATINGS:
        result["reason"] = "There is no control-effectiveness rating."
        return result

    grid_band = (residual_grid.get("cells") or {}).get(inherent_band, {}).get(control_rating)
    if grid_band is None:
        result["reason"] = f"The residual grid has no cell for {inherent_band} x {control_rating}."
        return result

    # Invariant, not a policy choice: controls never raise risk. A grid that
    # tries to is a configuration error and must not produce a result.
    assert _band_rank(grid_band, risk_bands) <= _band_rank(inherent_band, risk_bands), (
        f"residual grid raises {inherent_band} to {grid_band}"
    )

    band = grid_band
    for rule in triggered_rules or []:
        if not rule.get("non_mitigable"):
            continue
        floor = rule.get("min_band")
        if floor and _band_rank(floor, risk_bands) > _band_rank(band, risk_bands):
            result["floors_applied"].append(
                {"rule_code": rule.get("rule_code"), "band_before": band, "band_after": floor}
            )
            band = floor

    result["grid_band"] = grid_band
    result["residual_band"] = band
    return result


def residual_position_score(
    inherent_score: Optional[float],
    control_reduction: float,
    residual_band: Optional[str],
    risk_bands: Optional[list[dict[str, Any]]] = None,
) -> Optional[float]:
    """
    An indicative 0-100 number for the residual, for screens and reports
    that expect one: the inherent score less the control reduction,
    clamped into the residual band's range so number and band can never
    disagree. The band is authoritative; this only places it.
    """

    if residual_band is None:
        return None

    risk_bands = risk_bands or DEFAULT_RISK_BANDS
    band = next((b for b in risk_bands if b["name"] == residual_band), None)
    if band is None:
        return None

    raw = max(0.0, float(inherent_score or 0) - float(control_reduction or 0))
    return round(min(max(raw, float(band["min"])), float(band["max"])), 2)


def residual_exceeds_tolerance(
    residual_score: Optional[float],
    residual_band: Optional[str],
    tolerance: float,
    risk_bands: Optional[list[dict[str, Any]]] = None,
) -> bool:
    """
    Whether a residual result is above the risk-appetite tolerance. A
    residual whose band was set by policy has no score (see
    residual_risk_service), so the band is compared with the band the
    tolerance falls in: a CRITICAL residual held up by a sanctions rule
    must not slip past the check for want of a number.
    """

    if residual_score is not None:
        return residual_score > tolerance
    if residual_band is None:
        return False
    risk_bands = risk_bands or DEFAULT_RISK_BANDS
    return _band_rank(residual_band, risk_bands) >= _band_rank(
        determine_risk_band(tolerance, risk_bands), risk_bands
    )


def methodology_fingerprint(config: dict[str, Any]) -> str:
    """
    SHA-256 over every setting that affects a result. Two calculations
    with the same fingerprint used identical methodology, whatever the
    row's name or id -- including the built-in defaults, which have no
    row at all.
    """

    import hashlib
    import json

    keys = [
        "factor_weights",
        "likelihood_scale",
        "impact_scale",
        "risk_bands",
        "escalation_rules",
        "required_approvals",
        "mitigant_categories",
        "residual_grid",
    ]
    payload = json.dumps({key: config.get(key) for key in keys}, sort_keys=True, default=str)
    return "sha256:" + hashlib.sha256(payload.encode("utf-8")).hexdigest()


def calculate_overall_score_from_factors(
    factors: list[dict[str, Any]],
) -> float:
    """
    Legacy Stage 4 equal-weighted average of factor scores. No longer the
    source of overall_score/risk_level -- that is calculate_inherent_risk
    alone -- and kept only for the fallback RiskEngine report and for
    reading AI-evaluation snapshots taken before the analyzer stopped
    emitting scores. Do not use it for anything official.
    """

    scores = [
        max(0.0, min(100.0, float(factor.get("score", 0))))
        for factor in factors
        if factor.get("applicable") and not factor.get("excluded")
    ]

    if not scores:
        return 0.0

    return round(sum(scores) / len(scores), 2)


def determine_risk_level(
    score: float,
    thresholds: Optional[dict[str, float]] = None,
) -> str:
    """
    Convert the deterministic overall score into a risk level.

    `thresholds` defaults to DEFAULT_RISK_THRESHOLDS but can be supplied
    from a configured RiskMethodology.
    """

    thresholds = thresholds or DEFAULT_RISK_THRESHOLDS

    if score >= thresholds.get("CRITICAL", DEFAULT_RISK_THRESHOLDS["CRITICAL"]):
        return "CRITICAL"

    if score >= thresholds.get("HIGH", DEFAULT_RISK_THRESHOLDS["HIGH"]):
        return "HIGH"

    if score >= thresholds.get("MEDIUM", DEFAULT_RISK_THRESHOLDS["MEDIUM"]):
        return "MEDIUM"

    return "LOW"


# ---------------------------------------------------------------------------
# OCC supervisory roll-up (OCC NR 96-2a, "Categories of Risk").
#
# A read-only lens over factors that have already been rated under the 10
# assessed categories. It has no inputs of its own, writes nothing, and is
# never consulted by calculate_inherent_risk -- an assessment's score is
# exactly what it was before this existed.
# ---------------------------------------------------------------------------


def calculate_occ_risk_profile(
    factors: list[dict[str, Any]],
    risk_bands: Optional[list[dict[str, Any]]] = None,
) -> dict[str, Any]:
    """
    Rolls this assessment's risk factors up into the nine OCC categories
    via app.schemas.risk_factor.CATEGORY_TO_OCC.

    Each OCC category's score is the equal-weighted mean of the scores of
    the applicable, non-excluded factors that map into it -- the same
    convention as calculate_overall_score_from_factors, so the lens reads
    consistently with the primary score. `max_factor_score` is reported
    alongside it because averaging across an overlapping many-to-many map
    can mute a single severe factor, and a supervisory view should still
    be able to see it.

    Because the OCC categories overlap by design, one factor contributes
    to several of them; the per-category scores therefore do not sum to
    anything meaningful and are not intended to.
    """

    from app.schemas.risk_factor import (
        CATEGORY_TO_OCC,
        OCC_CATEGORIES_NOT_COVERED,
        OCC_RISK_CATEGORIES,
        OCC_RISK_CATEGORY_DEFINITIONS,
    )

    risk_bands = risk_bands or DEFAULT_RISK_BANDS

    applicable_factors = [
        factor
        for factor in factors
        if factor.get("applicable") and not factor.get("excluded")
    ]

    categories: list[dict[str, Any]] = []
    any_provisional = False

    for occ_category in OCC_RISK_CATEGORIES:
        covered = occ_category not in OCC_CATEGORIES_NOT_COVERED

        contributing = [
            factor
            for factor in applicable_factors
            if occ_category in CATEGORY_TO_OCC.get(factor.get("category"), [])
        ]

        entry: dict[str, Any] = {
            "occ_category": occ_category,
            "label": occ_category.replace("_", " ").title(),
            "definition": OCC_RISK_CATEGORY_DEFINITIONS.get(occ_category, ""),
            "covered": covered,
            "score": None,
            "risk_band": None,
            "max_factor_score": None,
            "is_provisional": False,
            "factor_count": len(contributing),
            "contributing_factors": [
                {
                    "risk_factor_id": factor.get("id"),
                    "category": factor.get("category"),
                    "score": max(0.0, min(100.0, float(factor.get("score", 0) or 0))),
                    "severity": factor.get("severity") or "LOW",
                    "rated": bool(factor.get("rated")),
                    "excluded": bool(factor.get("excluded")),
                }
                for factor in contributing
            ],
        }

        # An uncovered category, or a covered one with nothing applicable
        # mapping into it, keeps a null score: neither is a reading of zero.
        if contributing:
            # Same rule as calculate_inherent_risk: only analyst-rated
            # factors count; an unrated factor is unknown, not a 0.
            scores = [
                item["score"] for item in entry["contributing_factors"] if item["rated"]
            ]
            entry["is_provisional"] = len(scores) < len(contributing)

            if scores:
                score = round(sum(scores) / len(scores), 2)
                entry["score"] = score
                entry["risk_band"] = determine_risk_band(score, risk_bands)
                entry["max_factor_score"] = max(scores)

            if entry["is_provisional"]:
                any_provisional = True

        categories.append(entry)

    return {
        "categories": categories,
        "uncovered_categories": list(OCC_CATEGORIES_NOT_COVERED),
        "is_provisional": any_provisional,
    }

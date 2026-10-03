"""
Stage 11: pure trigger-evaluation and finding-generation rules. Given a
plain-data snapshot of an assessment (built by app/challenge_engine/engine.py
from the DB) plus the active ChallengeTriggerConfig, decide which trigger
conditions fired (R11.1) and produce the corresponding findings (R11.2-R11.5).
No DB access here -- easy to test, mirrors app/risk_engine/scoring.py and
app/control_engine/scoring.py's separation of pure logic from orchestration.
"""

from app.risk_engine.scoring import residual_exceeds_tolerance

DEFAULT_TRIGGER_RISK_LEVELS = ["HIGH", "CRITICAL"]
DEFAULT_RESIDUAL_RISK_TOLERANCE = 60.0
# Left empty on purpose: high-risk jurisdictions now come from the
# country_risk reference table (loaded from published FATF snapshots --
# see app/services/country_risk_service.py), not from a hand-maintained
# list. This config remains as an *additional* manual override for
# jurisdictions an organisation treats as high-risk beyond the published
# designations; matches from either source fire the trigger.
DEFAULT_HIGH_RISK_JURISDICTIONS: list[str] = []
DEFAULT_HIGH_RISK_TECHNOLOGIES: list[str] = []

SEVERITY_RANK = {"LOW": 0, "MEDIUM": 1, "HIGH": 2, "CRITICAL": 3}

# R11.6: only these severities block progression to committee.
HIGH_SEVERITY_LEVELS = {"HIGH", "CRITICAL"}

# R11.1: triggers an authorized user may switch off. Two stay mandatory:
# a high/critical risk always gets a challenge review (Stage 11 acceptance
# criteria; which levels count is still configurable), and an unresolved
# country is a safety check -- off, it would read as "no exposure".
MANDATORY_TRIGGERS = {"RISK_HIGH_OR_CRITICAL", "UNRESOLVED_COUNTRY_REFERENCE"}
CONFIGURABLE_TRIGGERS = {
    "EVIDENCE_MISSING",
    "CONTRADICTIONS_EXIST",
    "LOW_CONFIDENCE",
    "MISSING_RISK_FACTORS",
    "RATING_MISMATCH",
    "WEAK_CONTROLS",
    "RESIDUAL_RISK_EXCEEDS_TOLERANCE",
    "HIGH_RISK_JURISDICTION_OR_TECHNOLOGY",
}

# Which trigger produces each finding category, so a disabled trigger
# produces no findings either.
FINDING_TRIGGER = {
    "UNSUPPORTED_CONCLUSION": "EVIDENCE_MISSING",
    "CONTRADICTION": "CONTRADICTIONS_EXIST",
    "LOW_CONFIDENCE": "LOW_CONFIDENCE",
    "MISSING_RISK": "MISSING_RISK_FACTORS",
    "RATING_MISMATCH": "RATING_MISMATCH",
    "WEAK_CONTROL": "WEAK_CONTROLS",
    "RESIDUAL_RISK_EXCEEDS_TOLERANCE": "RESIDUAL_RISK_EXCEEDS_TOLERANCE",
    "HIGH_RISK_JURISDICTION_OR_TECHNOLOGY": "HIGH_RISK_JURISDICTION_OR_TECHNOLOGY",
    "UNRESOLVED_COUNTRY_REFERENCE": "UNRESOLVED_COUNTRY_REFERENCE",
}


def disabled_triggers(config: dict) -> set[str]:
    """The configurable triggers switched off in `config`, including the
    two older per-trigger toggles."""

    disabled = set(config.get("disabled_triggers") or []) & CONFIGURABLE_TRIGGERS
    if not config.get("rating_mismatch_enabled", True):
        disabled.add("RATING_MISMATCH")
    if not config.get("low_confidence_enabled", True):
        disabled.add("LOW_CONFIDENCE")
    return disabled


def evaluate_triggers(context: dict, config: dict) -> list[dict]:
    triggers = []

    risk_level = (context.get("risk_level") or "").upper()
    triggers.append(
        {
            "name": "RISK_HIGH_OR_CRITICAL",
            "label": "Risk is high or critical",
            "fired": risk_level in set(config.get("trigger_risk_levels", DEFAULT_TRIGGER_RISK_LEVELS)),
        }
    )

    triggers.append(
        {
            "name": "EVIDENCE_MISSING",
            "label": "Evidence is missing",
            "fired": (context.get("evidence_open_count") or 0) > 0,
        }
    )

    triggers.append(
        {
            "name": "CONTRADICTIONS_EXIST",
            "label": "Contradictions exist",
            "fired": len(context.get("inconsistencies") or []) > 0,
        }
    )

    triggers.append(
        {
            "name": "LOW_CONFIDENCE",
            "label": "Confidence is low",
            "fired": bool(config.get("low_confidence_enabled", True))
            and bool(context.get("is_provisional")),
        }
    )

    triggers.append(
        {
            "name": "MISSING_RISK_FACTORS",
            "label": "Important risk factors are missing",
            "fired": len(context.get("missing_categories") or []) > 0,
        }
    )

    triggers.append(
        {
            "name": "RATING_MISMATCH",
            "label": "Human and system ratings differ materially",
            "fired": bool(config.get("rating_mismatch_enabled", True))
            and len(context.get("rating_mismatches") or []) > 0,
        }
    )

    triggers.append(
        {
            "name": "WEAK_CONTROLS",
            "label": "Controls are weak or unsupported",
            "fired": len(context.get("weak_control_gaps") or []) > 0,
        }
    )

    residual_score = context.get("residual_score")
    tolerance = config.get("residual_risk_tolerance", DEFAULT_RESIDUAL_RISK_TOLERANCE)
    triggers.append(
        {
            "name": "RESIDUAL_RISK_EXCEEDS_TOLERANCE",
            "label": "Residual risk exceeds tolerance",
            "fired": residual_exceeds_tolerance(
                residual_score, context.get("residual_risk_level"), tolerance
            ),
        }
    )

    high_risk_jurisdictions = {
        value.strip().upper() for value in config.get("high_risk_jurisdictions", [])
    }
    high_risk_technologies = {
        value.strip().upper() for value in config.get("high_risk_technologies", [])
    }
    countries = {value.strip().upper() for value in context.get("countries") or []}
    technologies = {value.strip().upper() for value in context.get("technologies") or []}

    # Published designations (FATF today), resolved to ISO codes by the
    # engine before we get here. These are the primary source; the
    # configured list above is an organisation-specific addition.
    designations = context.get("country_designations") or []

    matched_jurisdictions = (countries & high_risk_jurisdictions) | {
        designation["iso_code"] for designation in designations
    }
    matched_technologies = {
        tech
        for tech in technologies
        for keyword in high_risk_technologies
        if keyword and keyword in tech
    }

    triggers.append(
        {
            "name": "HIGH_RISK_JURISDICTION_OR_TECHNOLOGY",
            "label": "New high-risk jurisdiction or technology",
            "fired": bool(matched_jurisdictions or matched_technologies),
            "matched_jurisdictions": sorted(matched_jurisdictions),
            "matched_technologies": sorted(matched_technologies),
        }
    )

    # Country text the normalizer could not resolve. This is a safety
    # trigger, not a risk one: unrecognised text would otherwise read as
    # "no jurisdiction exposure", which is a false negative in the
    # dangerous direction.
    unresolved_countries = context.get("unresolved_countries") or []

    triggers.append(
        {
            "name": "UNRESOLVED_COUNTRY_REFERENCE",
            "label": "A stated country could not be identified",
            "fired": bool(unresolved_countries),
            "unresolved_countries": sorted(unresolved_countries),
        }
    )

    off = disabled_triggers(config)
    for trigger in triggers:
        trigger["enabled"] = trigger["name"] not in off
        if not trigger["enabled"]:
            trigger["fired"] = False

    return triggers


def generate_findings(context: dict, disabled: set[str] | None = None) -> list[dict]:
    """Findings for every enabled trigger (R11.1: a disabled trigger
    produces none)."""

    disabled = disabled or set()
    return [
        finding
        for finding in _all_findings(context)
        if FINDING_TRIGGER.get(finding["category"]) not in disabled
    ]


def _all_findings(context: dict) -> list[dict]:
    findings: list[dict] = []

    # R11.2: risk categories that may have been overlooked entirely.
    for category in context.get("missing_categories") or []:
        findings.append(
            {
                "category": "MISSING_RISK",
                "description": f"{category} was not identified or recorded for this assessment.",
                "related_section": "RISK_IDENTIFICATION",
                "severity": "MEDIUM",
                "supporting_evidence": None,
                "recommended_action": "Confirm whether this risk category applies and, if so, "
                "add it with a rationale before final disposition.",
            }
        )

    # R11.3: conclusions without adequate evidence.
    if (context.get("evidence_open_count") or 0) > 0:
        for issue in context.get("evidence_issues") or []:
            findings.append(
                {
                    "category": "UNSUPPORTED_CONCLUSION",
                    "description": issue.get("description")
                    or f"{issue.get('title')} is missing supporting evidence.",
                    "related_section": "EVIDENCE",
                    "severity": "HIGH",
                    "supporting_evidence": None,
                    "recommended_action": "Obtain and upload the missing evidence before "
                    "the assessment proceeds to committee.",
                }
            )

    # R11.4: contradictions between the intake form, documents, and
    # extracted intelligence.
    for conflict in context.get("inconsistencies") or []:
        findings.append(
            {
                "category": "CONTRADICTION",
                "description": conflict.get("message")
                or f"Conflicting values for {conflict.get('field')}.",
                "related_section": "EVIDENCE",
                "severity": "HIGH",
                "supporting_evidence": (
                    "; ".join(
                        f"{document['filename']}: \"{document['passage']}\""
                        for document in conflict.get("documents") or []
                    )
                    if conflict.get("source") == "DOCUMENTS"
                    else (
                        f"Form: {conflict.get('form_value')!r} vs. "
                        f"Extracted: {conflict.get('extracted_value')!r}"
                    )
                ),
                "recommended_action": "Reconcile the conflicting values with the business "
                "owner or source documents.",
            }
        )

    # Human vs. system rating disagreement.
    for mismatch in context.get("rating_mismatches") or []:
        findings.append(
            {
                "category": "RATING_MISMATCH",
                "description": (
                    f"{mismatch.get('dimension')}: system rated "
                    f"{mismatch.get('system_severity')}, human reviewer rated "
                    f"{mismatch.get('human_severity')}."
                ),
                "related_section": "FCRM_REVIEW",
                "severity": "MEDIUM",
                "supporting_evidence": None,
                "recommended_action": "Document the rationale for the human override, or "
                "align the rating with the system's finding.",
            }
        )

    # Weak/unsupported controls (Stage 7 gaps carried forward).
    weak_control_severity = {
        "NO_CONTROL": "HIGH",
        "INEFFECTIVE": "HIGH",
        "NO_EVIDENCE": "MEDIUM",
        "PARTIAL": "MEDIUM",
        "INCOMPLETE_COVERAGE": "MEDIUM",
        "UNAVAILABLE_DATA_DEPENDENCY": "MEDIUM",
    }
    for gap in context.get("weak_control_gaps") or []:
        findings.append(
            {
                "category": "WEAK_CONTROL",
                "description": gap.get("description")
                or "A mapped control is weak or unsupported.",
                "related_section": "CONTROLS",
                "severity": weak_control_severity.get(gap.get("gap_type"), "MEDIUM"),
                "supporting_evidence": None,
                "recommended_action": "Strengthen or replace the control, or add a "
                "condition tracking the required enhancement.",
            }
        )

    # Residual risk exceeds tolerance.
    residual_score = context.get("residual_score")
    tolerance = context.get("residual_risk_tolerance", DEFAULT_RESIDUAL_RISK_TOLERANCE)
    residual_band = context.get("residual_risk_level")
    if residual_exceeds_tolerance(residual_score, residual_band, tolerance):
        shown = (
            f"{residual_score:.1f}"
            if residual_score is not None
            else f"{residual_band}, set by a non-mitigable policy rule"
        )
        findings.append(
            {
                "category": "RESIDUAL_RISK_EXCEEDS_TOLERANCE",
                "description": (
                    f"Residual risk ({shown}) exceeds the configured "
                    f"tolerance ({tolerance:.1f})."
                ),
                "related_section": "RESIDUAL_RISK",
                "severity": "HIGH",
                "supporting_evidence": None,
                "recommended_action": "Add or strengthen controls, or obtain explicit "
                "risk acceptance before proceeding.",
            }
        )

    # New high-risk jurisdiction or technology. A published designation
    # names every list the country appears on, with each list's own date,
    # so a reviewer can see exactly what it was assessed against -- the
    # lists do not always agree, and "FATF removed it but the EU has not"
    # is material. Anything matched only from the configured override
    # list keeps the generic wording.
    designations_by_code = {
        designation["iso_code"]: designation
        for designation in context.get("country_designations") or []
    }

    for jurisdiction in context.get("matched_jurisdictions") or []:
        match = designations_by_code.get(jurisdiction)

        if match:
            # Only echo what the analyst typed when it differs from the
            # published name, so this doesn't read "Iran (Iran)".
            stated = match["matched_text"]
            as_written = (
                ""
                if stated.strip().lower() == match["name"].lower()
                else f" ({stated})"
            )

            listings = "; ".join(
                f"{entry['source']} lists it as {entry['tier_label']} "
                f"as of {entry['as_of']}"
                for entry in match["designations"]
            )
            description = (
                f"This change involves {match['name']}{as_written}. {listings}."
            )
            severity = match["severity"]
        else:
            description = (
                f"This change involves {jurisdiction}, a configured "
                "high-risk jurisdiction."
            )
            severity = "MEDIUM"

        findings.append(
            {
                "category": "HIGH_RISK_JURISDICTION_OR_TECHNOLOGY",
                "description": description,
                "related_section": "INTAKE",
                "severity": severity,
                "supporting_evidence": None,
                "recommended_action": "Confirm jurisdiction-specific controls and "
                "regulatory obligations are addressed.",
            }
        )

    for unresolved in context.get("unresolved_countries") or []:
        findings.append(
            {
                "category": "UNRESOLVED_COUNTRY_REFERENCE",
                "description": (
                    f'"{unresolved}" was given as a country or jurisdiction but '
                    "could not be identified, so it was not checked against the "
                    "published high-risk lists."
                ),
                "related_section": "INTAKE",
                "severity": "MEDIUM",
                "supporting_evidence": None,
                "recommended_action": "Replace it with a specific country name or "
                "ISO code so the jurisdiction check can run.",
            }
        )

    for technology in context.get("matched_technologies") or []:
        findings.append(
            {
                "category": "HIGH_RISK_JURISDICTION_OR_TECHNOLOGY",
                "description": f"This change involves {technology}, a configured "
                "high-risk technology.",
                "related_section": "INTAKE",
                "severity": "MEDIUM",
                "supporting_evidence": None,
                "recommended_action": "Confirm technology-specific controls and "
                "security reviews are addressed.",
            }
        )

    # Low confidence (provisional inherent-risk calculation).
    if context.get("is_provisional"):
        findings.append(
            {
                "category": "LOW_CONFIDENCE",
                "description": "The inherent risk calculation is still provisional -- "
                "one or more applicable risk factors have not been manually rated.",
                "related_section": "INHERENT_RISK_ASSESSMENT",
                "severity": "MEDIUM",
                "supporting_evidence": None,
                "recommended_action": "Rate the remaining risk factors before relying "
                "on this assessment's conclusions.",
            }
        )

    return findings


def has_high_severity_open_findings(findings: list[dict]) -> bool:
    return any(
        finding["severity"] in HIGH_SEVERITY_LEVELS and finding["resolution_status"] == "OPEN"
        for finding in findings
    )

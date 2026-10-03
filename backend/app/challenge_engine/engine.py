"""
DB-facing orchestration for Stage 11: builds a plain-data context for one
assessment, evaluates trigger conditions (R11.1) and findings (R11.2-R11.5)
via app/challenge_engine/rules.py, and reconciles the persisted
ChallengeFinding rows the same way app/control_engine/engine.py reconciles
ControlGap rows -- a still-detected finding is left alone (so its
resolution/acceptance state survives), a no-longer-detected OPEN finding is
auto-resolved, and nothing already accepted/resolved is duplicated.
"""

import json
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.challenge_engine import rules
from app.models.assessment import Assessment
from app.models.assessment_document import AssessmentDocument
from app.models.assessment_fcrm_review import AssessmentFcrmReview
from app.models.assessment_intelligence import AssessmentIntelligence
from app.models.challenge_review import ChallengeFinding, ChallengeTriggerConfig
from app.models.control import ControlGap
from app.models.inherent_risk_calculation import InherentRiskCalculation
from app.models.risk_factor import RiskFactor
from app.models.risk_result import RiskResult
from app.services.country_risk_service import assess_countries
from app.schemas.risk_factor import RISK_CATEGORIES
from app.services.consistency_check import detect_inconsistencies


def get_active_trigger_config(db: Session) -> dict:
    config_row = (
        db.query(ChallengeTriggerConfig)
        .filter(ChallengeTriggerConfig.is_active.is_(True))
        .first()
    )

    if not config_row:
        return {
            "trigger_risk_levels": rules.DEFAULT_TRIGGER_RISK_LEVELS,
            "residual_risk_tolerance": rules.DEFAULT_RESIDUAL_RISK_TOLERANCE,
            "rating_mismatch_enabled": True,
            "low_confidence_enabled": True,
            "high_risk_jurisdictions": rules.DEFAULT_HIGH_RISK_JURISDICTIONS,
            "high_risk_technologies": rules.DEFAULT_HIGH_RISK_TECHNOLOGIES,
            "disabled_triggers": [],
        }

    return {
        "trigger_risk_levels": json.loads(config_row.trigger_risk_levels),
        "residual_risk_tolerance": config_row.residual_risk_tolerance,
        "rating_mismatch_enabled": config_row.rating_mismatch_enabled,
        "low_confidence_enabled": config_row.low_confidence_enabled,
        "high_risk_jurisdictions": json.loads(config_row.high_risk_jurisdictions),
        "high_risk_technologies": json.loads(config_row.high_risk_technologies),
        "disabled_triggers": json.loads(config_row.disabled_triggers or "[]"),
    }


_BAND_ORDER = {"LOW": 0, "MEDIUM": 1, "HIGH": 2, "CRITICAL": 3}


def _challenge_risk_level(db: Session, assessment) -> str | None:
    """The band the HIGH/CRITICAL trigger sees: the higher of the band of
    record and the calculated band, so a human override that lowers the
    rating can never switch off the mandatory challenge itself."""

    from app.services.residual_risk_service import current_inherent_calculation

    candidates = [assessment.inherent_risk_level or assessment.risk_level]
    calculation = current_inherent_calculation(db, assessment.id)
    if calculation is not None:
        candidates.append(calculation.calculated_band)
    known = [band for band in candidates if band in _BAND_ORDER]
    if not known:
        return candidates[0]
    return max(known, key=_BAND_ORDER.__getitem__)


def _build_context(db: Session, assessment: Assessment) -> dict:
    intelligence = (
        db.query(AssessmentIntelligence)
        .filter(AssessmentIntelligence.assessment_id == assessment.id)
        .first()
    )

    documents = (
        db.query(AssessmentDocument)
        .filter(
            AssessmentDocument.assessment_id == assessment.id,
            AssessmentDocument.is_current.is_(True),
        )
        .all()
    )

    # Imported lazily to avoid a module-load-time circular import between
    # app.api.assessments and app.challenge_engine.
    from app.api.assessments import _compute_evidence_gaps

    evidence_issues = [
        issue.model_dump() if hasattr(issue, "model_dump") else issue
        for issue in _compute_evidence_gaps(assessment, intelligence, documents)
    ]
    evidence_issues = [issue for issue in evidence_issues if issue.get("missing")]

    inconsistencies = detect_inconsistencies(assessment, intelligence)

    risk_factors = (
        db.query(RiskFactor)
        .filter(RiskFactor.assessment_id == assessment.id, RiskFactor.is_current.is_(True))
        .all()
    )
    present_categories = {factor.category for factor in risk_factors}
    missing_categories = [
        category for category in RISK_CATEGORIES if category not in present_categories
    ]

    inherent_calc = (
        db.query(InherentRiskCalculation)
        .filter(
            InherentRiskCalculation.assessment_id == assessment.id,
            InherentRiskCalculation.is_current.is_(True),
        )
        .first()
    )
    is_provisional = bool(inherent_calc.is_provisional) if inherent_calc else False

    risk_results = (
        db.query(RiskResult)
        .filter(RiskResult.assessment_id == assessment.id, RiskResult.is_current.is_(True))
        .all()
    )
    fcrm_review = (
        db.query(AssessmentFcrmReview)
        .filter(AssessmentFcrmReview.assessment_id == assessment.id)
        .first()
    )
    human_ratings = {}
    if fcrm_review and fcrm_review.human_ratings:
        try:
            human_ratings = {
                int(key): value for key, value in json.loads(fcrm_review.human_ratings).items()
            }
        except (TypeError, ValueError, AttributeError):
            human_ratings = {}

    rating_mismatches = [
        {
            "dimension": result.dimension,
            "system_severity": result.severity,
            "human_severity": human_ratings[result.id],
        }
        for result in risk_results
        if result.id in human_ratings and human_ratings[result.id] != result.severity
    ]

    weak_control_gaps = [
        {"gap_type": gap.gap_type, "description": gap.description}
        for gap in db.query(ControlGap)
        .filter(ControlGap.assessment_id == assessment.id, ControlGap.resolved.is_(False))
        .all()
    ]

    countries = intelligence.get_list("countries") if intelligence else []
    technologies = intelligence.get_list("technologies") if intelligence else []

    # Resolve the stated countries to ISO codes and look them up against
    # the published designations (see country_risk_service). Done here
    # rather than in rules.py so that module stays pure/DB-free.
    country_assessment = assess_countries(db, countries)

    return {
        "risk_level": _challenge_risk_level(db, assessment),
        "residual_score": assessment.residual_score,
        "residual_risk_level": assessment.residual_risk_level,
        "evidence_open_count": len(evidence_issues),
        "evidence_issues": evidence_issues,
        "inconsistencies": inconsistencies,
        "is_provisional": is_provisional,
        "missing_categories": missing_categories,
        "rating_mismatches": rating_mismatches,
        "weak_control_gaps": weak_control_gaps,
        "countries": countries,
        "technologies": technologies,
        "country_designations": country_assessment["matches"],
        "unresolved_countries": country_assessment["unresolved"],
    }


def _reconcile_findings(
    db: Session,
    assessment_id: int,
    candidates: list[dict],
    disabled: set[str] | None = None,
) -> None:
    # All existing findings (any resolution_status) count for dedup, so an
    # already-ACCEPTED or -RESOLVED finding is never resurrected as a new
    # OPEN duplicate just because the same underlying condition is still
    # detected on a later recompute -- only a still-OPEN finding that is no
    # longer detected gets auto-resolved.
    existing = (
        db.query(ChallengeFinding)
        .filter(ChallengeFinding.assessment_id == assessment_id)
        .all()
    )

    new_keys = {(candidate["category"], candidate["description"]) for candidate in candidates}
    existing_keys = set()

    for finding in existing:
        key = (finding.category, finding.description)
        existing_keys.add(key)
        if key not in new_keys and finding.resolution_status == "OPEN":
            trigger = rules.FINDING_TRIGGER.get(finding.category)
            finding.resolution_status = "RESOLVED"
            finding.resolved_by = "System"
            finding.resolved_at = datetime.now(timezone.utc)
            finding.resolution_note = (
                f"The {trigger} trigger is disabled in the challenge configuration."
                if trigger in (disabled or set())
                else "No longer detected on re-run of the challenge review."
            )

    for candidate in candidates:
        key = (candidate["category"], candidate["description"])
        if key in existing_keys:
            continue
        db.add(
            ChallengeFinding(
                assessment_id=assessment_id,
                category=candidate["category"],
                description=candidate["description"],
                related_section=candidate["related_section"],
                severity=candidate["severity"],
                supporting_evidence=candidate.get("supporting_evidence"),
                recommended_action=candidate.get("recommended_action"),
            )
        )
        existing_keys.add(key)


def recompute_challenge_review(db: Session, assessment_id: int) -> dict:
    assessment = db.query(Assessment).filter(Assessment.id == assessment_id).first()
    if not assessment:
        return {"triggers": [], "findings": []}

    config = get_active_trigger_config(db)
    context = _build_context(db, assessment)

    triggers = rules.evaluate_triggers(context, config)

    matched = next(
        (t for t in triggers if t["name"] == "HIGH_RISK_JURISDICTION_OR_TECHNOLOGY"), {}
    )
    context["matched_jurisdictions"] = matched.get("matched_jurisdictions", [])
    context["matched_technologies"] = matched.get("matched_technologies", [])
    context["residual_risk_tolerance"] = config["residual_risk_tolerance"]

    disabled = rules.disabled_triggers(config)
    candidates = rules.generate_findings(context, disabled)
    _reconcile_findings(db, assessment_id, candidates, disabled)
    db.flush()

    findings = (
        db.query(ChallengeFinding)
        .filter(ChallengeFinding.assessment_id == assessment_id)
        .order_by(ChallengeFinding.detected_at.desc())
        .all()
    )

    return {
        "triggered": any(t["fired"] for t in triggers),
        "triggers": triggers,
        "findings": findings,
    }


def has_blocking_findings(db: Session, assessment_id: int) -> bool:
    """R11.6 / G-4 (2026-10-03): a HIGH/CRITICAL finding blocks committee
    submission until RESOLVED. It can't be accepted, and an acceptance
    recorded under the earlier rule no longer counts."""

    return (
        db.query(ChallengeFinding)
        .filter(
            ChallengeFinding.assessment_id == assessment_id,
            ChallengeFinding.resolution_status != "RESOLVED",
            ChallengeFinding.severity.in_(list(rules.HIGH_SEVERITY_LEVELS)),
        )
        .first()
        is not None
    )

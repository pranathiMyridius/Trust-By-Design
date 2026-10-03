"""
Stage 6 (R6.2-R6.7): the single place that (re)computes an assessment's
deterministic inherent-risk calculation from its current risk factors'
manual likelihood/impact ratings, and persists it as a new versioned
InherentRiskCalculation row.

Called after any factor mutation (rated, added, excluded) so the
calculation never goes stale relative to the factors an analyst is
looking at -- same pattern as app/services/risk_scoring.py.
"""

import json
from datetime import datetime, timezone
from typing import Any

from sqlalchemy.orm import Session

from app.models.assessment import Assessment
from app.models.inherent_risk_calculation import InherentRiskCalculation
from app.models.risk_factor import RiskFactor
from app.risk_engine.methodology import get_methodology_config, mark_methodology_used
from app.risk_engine.scoring import calculate_inherent_risk

# Stored in InherentRiskCalculation.risk_band (non-nullable) when no
# factor has been rated and no rule fired: there is no band yet, and
# writing LOW would claim one.
UNRATED_BAND = "UNRATED"


def factor_inputs(factors: list[RiskFactor]) -> list[dict[str, Any]]:
    """The calculation's view of RiskFactor rows."""

    return [
        {
            "id": factor.id,
            "category": factor.category,
            "applicable": factor.applicable,
            "excluded": factor.excluded,
            "score": factor.score,
            "likelihood": factor.likelihood,
            "impact": factor.impact,
            # Deliberately the analyst's own likelihood/impact, not the
            # AI's suggestion: a suggested rating nobody has confirmed
            # must keep the calculation provisional, or the R6.7
            # completeness gate would pass with no human in the loop.
            "rated": factor.likelihood is not None and factor.impact is not None,
            "indicators": factor.get_indicators(),
            "evidence_status": factor.evidence_status,
        }
        for factor in factors
    ]


def current_factors(db: Session, assessment: Assessment) -> list[RiskFactor]:
    return (
        db.query(RiskFactor)
        .filter(
            RiskFactor.assessment_id == assessment.id,
            RiskFactor.is_current.is_(True),
        )
        .all()
    )


def compute_inherent_risk(db: Session, assessment: Assessment) -> tuple[dict[str, Any], dict[str, Any]]:
    """The calculation result and the methodology config it used."""

    from app.services.country_risk_service import scoring_jurisdiction_context

    config = get_methodology_config(db)
    jurisdictions = scoring_jurisdiction_context(db, assessment.countries_jurisdictions)
    result = calculate_inherent_risk(
        factor_inputs(current_factors(db, assessment)),
        weights=config["factor_weights"],
        risk_bands=config["risk_bands"],
        escalation_rules=config["escalation_rules"],
        mitigant_categories=config["mitigant_categories"],
        jurisdiction_matches=jurisdictions["matches"],
    )
    result["jurisdictions"] = jurisdictions
    return result, config


def recalculate_inherent_risk(
    db: Session,
    assessment: Assessment,
    calculated_by: str | None = None,
) -> InherentRiskCalculation:
    """
    Does not commit -- the caller commits, same convention as
    recalculate_assessment_score.
    """

    result, config = compute_inherent_risk(db, assessment)

    previous_current = (
        db.query(InherentRiskCalculation)
        .filter(
            InherentRiskCalculation.assessment_id == assessment.id,
            InherentRiskCalculation.is_current.is_(True),
        )
        .all()
    )

    now = datetime.now(timezone.utc)
    next_version = 1

    for previous in previous_current:
        previous.is_current = False
        previous.superseded_at = now
        next_version = max(next_version, previous.version + 1)

    # final_score is stored as 0.0 when there is no score (the column is
    # non-nullable); the API reads the breakdown and reports None, see
    # app/api/assessments.py::_build_inherent_risk_response.
    stored_score = result["final_score"] if result["final_score"] is not None else 0.0
    stored_band = result["risk_band"] or UNRATED_BAND

    calculation = InherentRiskCalculation(
        assessment_id=assessment.id,
        methodology_id=config["methodology_id"],
        methodology_name=config["methodology_name"],
        inputs=json.dumps(result["breakdown"]),
        weights=json.dumps(config["factor_weights"]),
        thresholds=json.dumps(config["thresholds"]),
        risk_bands=json.dumps(config["risk_bands"]),
        escalation_rules=json.dumps(config["escalation_rules"]),
        final_score=stored_score,
        risk_band=stored_band,
        calculated_score=stored_score,
        calculated_band=stored_band,
        is_provisional=result["is_provisional"],
        escalated=result["escalated"],
        escalation_reasons=json.dumps(result["escalation_reasons"]),
        triggered_rules=json.dumps(result["triggered_rules"]),
        mandatory_review=result["mandatory_review"],
        methodology_version=config["methodology_version"],
        methodology_fingerprint=config["methodology_fingerprint"],
        reference_data=json.dumps(
            {
                "snapshots_used": result["jurisdictions"]["snapshots_used"],
                "snapshots_skipped": result["jurisdictions"]["snapshots_skipped"],
                "unresolved_countries": result["jurisdictions"]["unresolved"],
            }
        ),
        jurisdiction_matches=json.dumps(result["jurisdictions"]["matches"]),
        calculated_by=calculated_by or "System",
        calculated_at=now,
        version=next_version,
        is_current=True,
    )

    _carry_forward_override(db, assessment, previous_current, calculation)

    db.add(calculation)
    # From now on this methodology has produced a result, so it is
    # frozen: changes go on a clone.
    mark_methodology_used(db, config["methodology_id"])
    db.flush()

    _audit_rule_changes(db, assessment, previous_current, calculation, calculated_by)

    return calculation


def _carry_forward_override(
    db: Session,
    assessment: Assessment,
    previous: list[InherentRiskCalculation],
    calculation: InherentRiskCalculation,
) -> None:
    """
    R6.7: an analyst override is a judgement about one calculated result.
    If a recalculation reproduces that result (same score and band -- e.g.
    the re-run on stage advance), the override stays on the new row. If
    the result changed, the override no longer refers to it: it is
    dropped, and the audit log says so, so it never disappears silently.
    The superseded row keeps the original override either way.
    """

    from app.services.audit_service import AuditAction, log_audit_event

    overridden = next((row for row in previous if row.overridden), None)
    if overridden is None:
        return

    if (
        overridden.calculated_score == calculation.calculated_score
        and overridden.calculated_band == calculation.calculated_band
    ):
        calculation.overridden = True
        calculation.override_value = overridden.override_value
        calculation.override_band = overridden.override_band
        calculation.override_reason = overridden.override_reason
        calculation.override_by = overridden.override_by
        calculation.override_at = overridden.override_at
        return

    def score(value: float | None) -> str:
        return "—" if value is None else f"{value:g}"

    log_audit_event(
        db=db,
        assessment_id=assessment.id,
        action=AuditAction.MANUAL_SCORE_OVERRIDE,
        previous_status=overridden.override_band,
        new_status=calculation.calculated_band,
        details=(
            f"Inherent risk override by {overridden.override_by} "
            f"({score(overridden.override_value)}, {overridden.override_band}) no longer "
            f"applies: the calculated result changed from "
            f"{score(overridden.calculated_score)} ({overridden.calculated_band}) to "
            f"{score(calculation.calculated_score)} ({calculation.calculated_band}). "
            "Re-apply the override if it is still warranted."
        ),
    )


def _audit_rule_changes(
    db: Session,
    assessment: Assessment,
    previous: list[InherentRiskCalculation],
    calculation: InherentRiskCalculation,
    actor: str | None,
) -> None:
    """
    Records in the audit log each policy rule that starts or stops firing.
    Only changes are logged -- recalculation runs on every factor edit,
    and repeating an unchanged rule each time would bury the entries that
    matter.
    """

    from app.services.audit_service import AuditAction, log_audit_event

    before = {
        rule["rule_code"]
        for calc in previous
        for rule in calc.get_triggered_rules()
    }
    fired = {rule["rule_code"]: rule for rule in calculation.get_triggered_rules()}

    for code, rule in fired.items():
        if code in before:
            continue
        log_audit_event(
            db=db,
            assessment_id=assessment.id,
            action=AuditAction.STATUS_CHANGE,
            actor=actor or "System",
            details=(
                f"Policy rule {code} (v{rule.get('version')}, {rule.get('rule_type')}) "
                f"fired on {', '.join(rule.get('triggered_by') or [])}: "
                f"band {rule.get('band_before') or 'unrated'} -> {rule.get('band_after')}"
                + ("; mandatory review required." if rule.get("mandatory_review") else ".")
            ),
        )

    for code in sorted(before - fired.keys()):
        log_audit_event(
            db=db,
            assessment_id=assessment.id,
            action=AuditAction.STATUS_CHANGE,
            actor=actor or "System",
            details=f"Policy rule {code} no longer applies after recalculation.",
        )

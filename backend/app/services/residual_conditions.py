"""
R8.6: recommended conditions for elevated residual risk.

Deterministic, explainable rules -- not an LLM -- decide which of the
condition types the requirement lists apply, from the residual band and
tolerance, the high-scoring risk factors and the open control gaps. Each
proposal says why it was made. Nothing is adopted automatically: an
analyst accepts, modifies or rejects each one with a reason (R8 acceptance
criteria), and only accepted or modified conditions are carried into the
assessment draft.
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.models.assessment import Assessment
from app.models.control import ControlGap
from app.models.recommended_condition import RecommendedCondition
from app.risk_engine.scoring import residual_exceeds_tolerance
from app.services.inherent_risk_service import current_factors

ELEVATED_BANDS = {"HIGH", "CRITICAL"}
HIGH_FACTOR_BANDS = {"HIGH", "CRITICAL"}

CONDITION_TYPES = {
    "ADDITIONAL_MONITORING": "Additional monitoring",
    "LOWER_TRANSACTION_LIMITS": "Lower transaction limits",
    "GEOGRAPHIC_RESTRICTIONS": "Geographic restrictions",
    "ENHANCED_DUE_DILIGENCE": "Enhanced due diligence",
    "ADDITIONAL_VENDOR_CONTROLS": "Additional vendor controls",
    "PERIODIC_REASSESSMENT": "Periodic reassessment",
    "MANAGEMENT_APPROVAL": "Management approval",
}

DECISIONS = {"accept": "ACCEPTED", "modify": "MODIFIED", "reject": "REJECTED"}


def _residual_of_record(db: Session, assessment: Assessment) -> tuple[str | None, float | None]:
    """The confirmed residual where a human has confirmed one, else the
    calculated result (frozen, or a live preview before RESIDUAL_RISK)."""

    from app.services.residual_risk_service import compute_residual_risk, current_residual_calculation

    calc = current_residual_calculation(db, assessment.id)
    if calc is not None and calc.frozen:
        if calc.confirmed_band:
            return calc.confirmed_band, calc.confirmed_score
        return calc.residual_band, calc.residual_score

    computed = compute_residual_risk(db, assessment)
    return computed["result"]["residual_band"], computed["residual_score"]


def _tolerance(db: Session) -> float:
    from app.models.challenge_review import ChallengeTriggerConfig

    config = db.query(ChallengeTriggerConfig).filter(ChallengeTriggerConfig.is_active.is_(True)).first()
    return float(config.residual_risk_tolerance) if config else 60.0


def recommend(db: Session, assessment: Assessment) -> list[dict]:
    """The conditions the rules recommend right now, each with a rationale.
    Empty when residual risk is not elevated."""

    band, score = _residual_of_record(db, assessment)
    tolerance = _tolerance(db)
    over_tolerance = residual_exceeds_tolerance(score, band, tolerance)
    if (band or "").upper() not in ELEVATED_BANDS and not over_tolerance:
        return []

    shown = f"{band}" + (f", score {score:.1f}" if score is not None else "")
    elevated = f"Residual risk is elevated ({shown})."

    high = {
        factor.category: factor
        for factor in current_factors(db, assessment)
        if factor.applicable and not factor.excluded and (factor.severity or "").upper() in HIGH_FACTOR_BANDS
    }
    gaps = (
        db.query(ControlGap)
        .filter(ControlGap.assessment_id == assessment.id, ControlGap.resolved.is_(False))
        .all()
    )
    factor_category = {factor.id: factor.category for factor in current_factors(db, assessment)}
    gap_categories = {factor_category.get(gap.risk_factor_id) for gap in gaps}

    def because(category: str, label: str) -> str:
        factor = high.get(category)
        detail = f"{label} is rated {factor.severity}" if factor else f"{label} has an open control gap"
        return f"{elevated} {detail}."

    proposals = [
        {
            "condition_type": "ADDITIONAL_MONITORING",
            "recommended_text": "Apply enhanced transaction monitoring, with scenarios tuned to this product, "
            "for at least the first 12 months after launch.",
            "rationale": elevated,
        },
        {
            "condition_type": "PERIODIC_REASSESSMENT",
            "recommended_text": "Reassess this product within 6 months of launch, using actual volumes and alerts.",
            "rationale": elevated,
        },
    ]

    if "TRANSACTION_ACTIVITY_RISK" in high or "PRODUCT_SERVICE_RISK" in high:
        category = "TRANSACTION_ACTIVITY_RISK" if "TRANSACTION_ACTIVITY_RISK" in high else "PRODUCT_SERVICE_RISK"
        proposals.append(
            {
                "condition_type": "LOWER_TRANSACTION_LIMITS",
                "recommended_text": "Launch with reduced per-transaction and monthly limits, raised only after "
                "monitoring confirms expected behaviour.",
                "rationale": because(category, "Transaction / product risk"),
            }
        )
    if "GEOGRAPHIC_RISK" in high or "GEOGRAPHIC_RISK" in gap_categories:
        proposals.append(
            {
                "condition_type": "GEOGRAPHIC_RESTRICTIONS",
                "recommended_text": "Restrict the product to the approved jurisdictions and block transactions "
                "involving high-risk or sanctioned countries.",
                "rationale": because("GEOGRAPHIC_RISK", "Geographic risk"),
            }
        )
    if {"CUSTOMER_SEGMENT_RISK", "OWNERSHIP_ENTITY_COMPLEXITY_RISK"} & (set(high) | gap_categories):
        category = "CUSTOMER_SEGMENT_RISK" if "CUSTOMER_SEGMENT_RISK" in high else "OWNERSHIP_ENTITY_COMPLEXITY_RISK"
        proposals.append(
            {
                "condition_type": "ENHANCED_DUE_DILIGENCE",
                "recommended_text": "Apply enhanced due diligence, including beneficial-ownership verification, "
                "to customers onboarded to this product.",
                "rationale": because(category, "Customer / ownership risk"),
            }
        )
    if "THIRD_PARTY_VENDOR_RISK" in high or "THIRD_PARTY_VENDOR_RISK" in gap_categories:
        proposals.append(
            {
                "condition_type": "ADDITIONAL_VENDOR_CONTROLS",
                "recommended_text": "Obtain and review the vendor's control evidence (e.g. audit report) before "
                "launch, with contractual monitoring and audit rights.",
                "rationale": because("THIRD_PARTY_VENDOR_RISK", "Third-party risk"),
            }
        )
    if (band or "").upper() == "CRITICAL" or over_tolerance:
        proposals.append(
            {
                "condition_type": "MANAGEMENT_APPROVAL",
                "recommended_text": "Require senior management sign-off before launch, recording acceptance of "
                "the residual risk.",
                "rationale": (
                    f"{elevated} It exceeds the residual-risk tolerance ({tolerance:.0f})."
                    if over_tolerance
                    else f"{elevated} It is CRITICAL."
                ),
            }
        )

    return proposals


def sync_recommendations(db: Session, assessment: Assessment) -> list[RecommendedCondition]:
    """
    Brings the stored recommendations in line with the rules: adds newly
    applicable types as PROPOSED, withdraws undecided proposals that no
    longer apply, and never touches a decided one. Returns the current
    rows. Does not commit.
    """

    proposals = {proposal["condition_type"]: proposal for proposal in recommend(db, assessment)}
    rows = (
        db.query(RecommendedCondition)
        .filter(RecommendedCondition.assessment_id == assessment.id, RecommendedCondition.is_current.is_(True))
        .all()
    )
    by_type = {row.condition_type: row for row in rows}

    for row in rows:
        if row.status == "PROPOSED" and row.condition_type not in proposals:
            row.is_current = False

    for condition_type, proposal in proposals.items():
        existing = by_type.get(condition_type)
        if existing is None:
            db.add(RecommendedCondition(assessment_id=assessment.id, status="PROPOSED", **proposal))
        elif existing.status == "PROPOSED":
            existing.recommended_text = proposal["recommended_text"]
            existing.rationale = proposal["rationale"]

    db.flush()
    return (
        db.query(RecommendedCondition)
        .filter(RecommendedCondition.assessment_id == assessment.id, RecommendedCondition.is_current.is_(True))
        .order_by(RecommendedCondition.id.asc())
        .all()
    )


def decide(
    row: RecommendedCondition,
    decision: str,
    reason: str,
    text: str | None,
    actor: str,
    actor_id: int,
) -> None:
    status = DECISIONS[decision]
    row.status = status
    row.final_text = None if status == "REJECTED" else (text if status == "MODIFIED" else row.recommended_text)
    row.decision_reason = reason
    row.decided_by = actor
    row.decided_by_id = actor_id
    row.decided_at = datetime.now(timezone.utc)


def adopted_conditions(db: Session, assessment_id: int) -> list[str]:
    """Accepted or modified recommendations, as adopted -- these go into
    the assessment draft's recommended conditions."""

    rows = (
        db.query(RecommendedCondition)
        .filter(
            RecommendedCondition.assessment_id == assessment_id,
            RecommendedCondition.is_current.is_(True),
            RecommendedCondition.status.in_(["ACCEPTED", "MODIFIED"]),
        )
        .order_by(RecommendedCondition.id.asc())
        .all()
    )
    return [row.final_text for row in rows if row.final_text]

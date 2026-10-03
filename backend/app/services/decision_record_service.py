"""
The reproducibility record frozen at committee approval.

An assessment may not reach APPROVED / APPROVED_WITH_CONDITIONS unless
everything needed to explain and re-derive the decision is on record:

  methodology      id, version, name and fingerprint of the settings used
  reference_data   the attested snapshots used, and any held back
  factors          every factor's rating, accepted and rejected
                   indicators, evidence quotes with verification results
  inherent         the calculation of record, with triggered rules
  controls         per-risk control ratings and the overall rating
  residual         the frozen grid lookup and any non-mitigable floors
  challenge        every challenge finding and its resolution
  ai               the model that identified the risk factors
  decision         who decided, on whose authority, when, and why

missing_requirements() says what is absent; freeze_decision_record()
writes the record once it is complete.
"""

import hashlib
import json
from datetime import datetime, timezone
from typing import Any

from sqlalchemy.orm import Session

from app.models.ai_metrics import AIUsageLog
from app.models.assessment import Assessment
from app.models.challenge_review import ChallengeFinding
from app.models.decision_record import DecisionRecord
from app.services.inherent_risk_service import current_factors
from app.services.residual_risk_service import (
    current_inherent_calculation,
    current_residual_calculation,
    residual_response,
)

# Sources whose factors must carry verified-evidence results. Manual
# factors carry the analyst's own rationale instead.
_EVIDENCE_SOURCES = {"AI", "RULES"}


def missing_requirements(db: Session, assessment: Assessment) -> list[str]:
    missing: list[str] = []

    inherent = current_inherent_calculation(db, assessment.id)
    if inherent is None:
        missing.append("There is no inherent-risk calculation.")
    else:
        if inherent.is_provisional and not inherent.overridden:
            missing.append("The inherent-risk calculation is provisional.")
        if not inherent.methodology_version or not inherent.methodology_fingerprint:
            missing.append(
                "The inherent-risk calculation predates methodology versioning; "
                "recalculate it (re-rate or re-run risk identification)."
            )
        if inherent.reference_data is None:
            missing.append(
                "The inherent-risk calculation does not record which reference "
                "data it used; recalculate it."
            )

    residual = current_residual_calculation(db, assessment.id)
    if residual is None or not residual.frozen:
        missing.append(
            "Residual risk has not been frozen through the residual grid "
            "(advance from Control Assessment to Residual Risk)."
        )
    else:
        if residual.residual_band is None:
            missing.append(f"The frozen residual result has no band: {residual.reason}")
        if inherent is not None and residual.inherent_calculation_id != inherent.id:
            missing.append(
                "The inherent-risk result changed after residual risk was frozen; "
                "residual risk must be re-frozen against the current result."
            )

    unverified = [
        factor.category
        for factor in current_factors(db, assessment)
        if factor.applicable
        and not factor.excluded
        and factor.source in _EVIDENCE_SOURCES
        and factor.evidence_status is None
    ]
    if unverified:
        missing.append(
            "These factors have no evidence-verification record (identified "
            f"before quote verification existed): {', '.join(sorted(unverified))}. "
            "Re-run risk identification."
        )

    missing += _missing_required_approvals(db, assessment, residual)

    return missing


def _missing_required_approvals(db: Session, assessment: Assessment, residual) -> list[str]:
    """
    R6.1 "required approvals": the methodology names, per residual band,
    which approvals the decision needs -- ANALYST (the FCRM review),
    REVIEWER (the manager's approval) and COMMITTEE (this decision). The
    band of record is the human-confirmed residual where there is one.
    """

    from app.models.assessment_fcrm_review import AssessmentFcrmReview
    from app.risk_engine.methodology import get_methodology_config

    if residual is None:
        return []
    band = residual.confirmed_band or residual.residual_band
    if not band:
        return []

    required = get_methodology_config(db)["required_approvals"].get(band, [])
    missing = []
    if "ANALYST" in required and not (
        db.query(AssessmentFcrmReview.id).filter(AssessmentFcrmReview.assessment_id == assessment.id).first()
    ):
        missing.append(f"A {band} residual risk requires the FCRM analyst review, which has not been recorded.")
    if "REVIEWER" in required and (assessment.manager_decision or "").upper() != "APPROVE":
        missing.append(f"A {band} residual risk requires the reviewing manager's approval, which has not been given.")
    return missing


def _factor_record(factor) -> dict[str, Any]:
    return {
        "id": factor.id,
        "category": factor.category,
        "source": factor.source,
        "version": factor.version,
        "applicable": factor.applicable,
        "excluded": factor.excluded,
        "exclusion_reason": factor.exclusion_reason,
        "excluded_by": factor.excluded_by,
        "likelihood": factor.likelihood,
        "impact": factor.impact,
        "score": factor.score,
        "severity": factor.severity,
        "rated_by": factor.rated_by,
        "rated_at": factor.rated_at,
        "rating_source": factor.rating_source,
        "ai_suggested_likelihood": factor.ai_suggested_likelihood,
        "ai_suggested_impact": factor.ai_suggested_impact,
        "evidence_status": factor.evidence_status,
        "accepted_indicators": factor.get_indicators(),
        "rejected_indicators": factor.get_rejected_indicators(),
        "evidence": factor.get_evidence(),
        "missing_information": factor.get_missing_information(),
        "rationale": factor.rationale,
    }


def _human_values(db: Session, assessment: Assessment) -> list[dict[str, Any]]:
    from app.services.override_ledger import value_comparisons

    return value_comparisons(db, assessment)


def _vote_history(db: Session, assessment_id: int) -> list[dict[str, Any]]:
    from app.models.committee_vote import CommitteeVote

    votes = (
        db.query(CommitteeVote)
        .filter(CommitteeVote.assessment_id == assessment_id)
        .order_by(CommitteeVote.voted_at.asc(), CommitteeVote.id.asc())
        .all()
    )
    return [
        {
            "id": vote.id,
            "member_id": vote.member_id,
            "member_name": vote.member_name,
            "cast_by_id": vote.cast_by_id,
            "delegate_id": vote.delegate_id,
            "vote": vote.vote,
            "comment": vote.comment,
            "version": vote.version,
            "is_current": vote.is_current,
            "recast_reason": vote.recast_reason,
            "voted_at": vote.voted_at,
            "superseded_at": vote.superseded_at,
        }
        for vote in votes
    ]


def _signoff(db: Session, assessment_id: int) -> dict[str, Any] | None:
    from app.services.challenge_signoff import current_signoff, signoff_payload

    signoff = current_signoff(db, assessment_id)
    return signoff_payload(signoff) if signoff else None


def build_record(
    db: Session,
    assessment: Assessment,
    decision: dict[str, Any],
) -> dict[str, Any]:
    inherent = current_inherent_calculation(db, assessment.id)
    residual = current_residual_calculation(db, assessment.id)

    findings = (
        db.query(ChallengeFinding)
        .filter(ChallengeFinding.assessment_id == assessment.id)
        .order_by(ChallengeFinding.id)
        .all()
    )
    ai_call = (
        db.query(AIUsageLog)
        .filter(
            AIUsageLog.assessment_id == assessment.id,
            AIUsageLog.purpose == "RISK_FACTOR_IDENTIFICATION",
        )
        .order_by(AIUsageLog.id.desc())
        .first()
    )

    return {
        "record_format": "1.1",
        "assessment": {
            "id": assessment.id,
            "reference_id": assessment.reference_id,
            "title": assessment.title,
            "assessment_mode": assessment.assessment_mode,
        },
        "methodology": {
            "id": inherent.methodology_id,
            "name": inherent.methodology_name,
            "version": inherent.methodology_version,
            "fingerprint": inherent.methodology_fingerprint,
            "residual_methodology_version": residual.methodology_version,
            "residual_methodology_fingerprint": residual.methodology_fingerprint,
        },
        "reference_data": {
            **inherent.get_reference_data(),
            "jurisdiction_matches": inherent.get_jurisdiction_matches(),
        },
        "factors": [_factor_record(f) for f in current_factors(db, assessment)],
        "inherent": {
            "calculation_id": inherent.id,
            "version": inherent.version,
            "final_score": inherent.final_score,
            "risk_band": inherent.risk_band,
            "is_provisional": inherent.is_provisional,
            "inputs": inherent.get_inputs(),
            "weights": inherent.get_weights(),
            "risk_bands": inherent.get_risk_bands(),
            "escalation_rules": inherent.get_escalation_rules(),
            "triggered_rules": inherent.get_triggered_rules(),
            "mandatory_review": inherent.mandatory_review,
            "overridden": inherent.overridden,
            "override_value": inherent.override_value,
            "override_band": inherent.override_band,
            "override_reason": inherent.override_reason,
            "override_by": inherent.override_by,
        },
        "residual": residual_response(residual),
        "challenge_findings": [
            {
                "id": f.id,
                "category": f.category,
                "severity": f.severity,
                "description": f.description,
                "resolution_status": f.resolution_status,
                "resolved_by": f.resolved_by,
                "resolution_note": f.resolution_note,
                "accepted_by": f.accepted_by,
                "accepted_reason": f.accepted_reason,
            }
            for f in findings
        ],
        # Format 1.1 (P2): human values beside calculated ones, the full
        # committee vote history and the challenge-review sign-off.
        "human_values": _human_values(db, assessment),
        "committee_votes": _vote_history(db, assessment.id),
        "challenge_signoff": _signoff(db, assessment.id),
        "ai": {
            "risk_identification_model": ai_call.response_model if ai_call else None,
            "requested_model": ai_call.requested_model if ai_call else None,
            "provider": ai_call.provider if ai_call else None,
        },
        "decision": decision,
    }


def freeze_decision_record(
    db: Session,
    assessment: Assessment,
    decision: dict[str, Any],
) -> DecisionRecord:
    """Writes the record. Callers check missing_requirements() first."""

    record = build_record(db, assessment, decision)
    payload = json.dumps(record, sort_keys=True, default=str)
    row = DecisionRecord(
        assessment_id=assessment.id,
        decision=decision["decision"],
        decided_by=decision["decided_by"],
        decided_at=decision["decided_at"],
        record=payload,
        checksum="sha256:" + hashlib.sha256(payload.encode("utf-8")).hexdigest(),
        created_at=datetime.now(timezone.utc),
    )
    db.add(row)
    db.flush()
    return row


def verify_record(row: DecisionRecord) -> bool:
    """True if the stored record still matches its checksum."""

    return row.checksum == "sha256:" + hashlib.sha256(row.record.encode("utf-8")).hexdigest()

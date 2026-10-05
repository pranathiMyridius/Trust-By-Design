"""
AI challenge analysis for an assessment (advisory findings only).

`run_ai_challenge` snapshots the assessment, asks the model for gaps the
rules did not raise, and stores them as OPEN findings. A reviewer then
confirms or dismisses each (`decide_finding`). Nothing here changes a
score, a control, or the rule-based challenge review, and a failed run
leaves the existing findings untouched.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy.orm import Session

from app.ai import challenge_analyzer, provider
from app.ai.metering import ai_assessment_context, prompt_version
from app.models.ai_challenge import AIChallengeFinding
from app.models.assessment import Assessment
from app.models.control import Control, ControlAssessment, ControlGap
from app.models.risk_factor import RiskFactor
from app.services.control_evidence_service import current_documents

DECIDED = ("CONFIRMED", "DISMISSED")


def _now() -> datetime:
    return datetime.now(timezone.utc)


def build_snapshot(db: Session, assessment: Assessment) -> dict[str, Any]:
    factors = (
        db.query(RiskFactor)
        .filter(
            RiskFactor.assessment_id == assessment.id,
            RiskFactor.is_current.is_(True),
            RiskFactor.applicable.is_(True),
            RiskFactor.excluded.is_(False),
        )
        .order_by(RiskFactor.id.asc())
        .all()
    )
    controls = (
        db.query(Control)
        .filter(Control.assessment_id == assessment.id, Control.is_current.is_(True))
        .order_by(Control.id.asc())
        .all()
    )
    assessments = {
        row.control_id: row
        for row in db.query(ControlAssessment).filter(
            ControlAssessment.assessment_id == assessment.id, ControlAssessment.is_current.is_(True)
        )
    }
    gaps = (
        db.query(ControlGap)
        .filter(ControlGap.assessment_id == assessment.id, ControlGap.resolved.is_(False))
        .order_by(ControlGap.id.asc())
        .all()
    )

    def control_row(control: Control) -> dict[str, Any]:
        current = assessments.get(control.id)
        return {
            "id": control.id,
            "risk_factor_id": control.risk_factor_id,
            "control_type": control.control_type,
            "description": control.description,
            "frequency": control.frequency,
            "operating_status": control.operating_status,
            "design_adequacy": current.design_adequacy if current else "NOT_ASSESSED",
            "operating_effectiveness": current.operating_effectiveness if current else "UNVERIFIED",
            "has_evidence": bool(current and current.has_evidence),
            "coverage_complete": current.coverage_complete if current else True,
        }

    return {
        "assessment": {
            "title": assessment.title,
            "change_type": assessment.change_type,
            "description": (assessment.description or "")[:1500],
            "inherent_risk_level": getattr(assessment, "inherent_risk_level", None),
            "residual_risk_level": getattr(assessment, "residual_risk_level", None),
        },
        "risk_factors": [
            {
                "id": factor.id,
                "category": factor.category,
                "severity": factor.severity,
                "rationale": (factor.rationale or "")[:500],
            }
            for factor in factors
        ],
        "controls": [control_row(control) for control in controls],
        "existing_gaps": [
            {
                "gap_type": gap.gap_type,
                "risk_factor_id": gap.risk_factor_id,
                "control_id": gap.control_id,
                "description": gap.description,
            }
            for gap in gaps
        ],
        "documents": [
            {"id": d.id, "filename": d.filename, "text": d.extracted_text or ""}
            for d in current_documents(db, assessment.id)
        ],
    }


def run_ai_challenge(db: Session, assessment: Assessment, actor: str) -> dict[str, Any]:
    """Returns {"status": "OK" | "AI_UNAVAILABLE" | "NO_RISKS" | "FAILED",
    "findings_raised": n}. Never raises."""

    if not provider.API_KEY:
        return {"status": "AI_UNAVAILABLE", "findings_raised": 0}

    try:
        snapshot = build_snapshot(db, assessment)
        if not snapshot["risk_factors"]:
            return {"status": "NO_RISKS", "findings_raised": 0}

        with ai_assessment_context(assessment.id):
            outcome = challenge_analyzer.analyze_assessment_gaps(snapshot)
        if outcome is None:
            return {"status": "FAILED", "findings_raised": 0}

        existing = (
            db.query(AIChallengeFinding)
            .filter(AIChallengeFinding.assessment_id == assessment.id, AIChallengeFinding.status != "SUPERSEDED")
            .all()
        )
        # Unreviewed findings are replaced by this run's; decisions stay.
        for finding in existing:
            if finding.status == "OPEN":
                finding.status = "SUPERSEDED"

        def key(category: str, control_id, risk_factor_id, title: str):
            return (category, control_id, risk_factor_id, title.strip().lower())

        decided = {
            key(f.category, f.control_id, f.risk_factor_id, f.title) for f in existing if f.status in DECIDED
        }

        run_id = str(uuid.uuid4())
        version = prompt_version("AI_CHALLENGE_ANALYSIS")
        raised = 0
        for item in outcome["findings"]:
            if key(item["category"], item["control_id"], item["risk_factor_id"], item["title"]) in decided:
                continue
            db.add(
                AIChallengeFinding(
                    assessment_id=assessment.id,
                    run_id=run_id,
                    status="OPEN",
                    model=outcome.get("model"),
                    prompt_version=version,
                    generated_by=actor,
                    **item,
                )
            )
            raised += 1
        db.flush()
        return {"status": "OK", "findings_raised": raised}
    except Exception:  # noqa: BLE001 -- an advisory analysis must never break the caller
        db.rollback()
        return {"status": "FAILED", "findings_raised": 0}


def decide_finding(
    finding: AIChallengeFinding, decision: str, actor: str, actor_id: int, note: str | None
) -> None:
    finding.status = "CONFIRMED" if decision == "CONFIRM" else "DISMISSED"
    finding.decided_by = actor
    finding.decided_by_id = actor_id
    finding.decided_at = _now()
    finding.decision_note = note

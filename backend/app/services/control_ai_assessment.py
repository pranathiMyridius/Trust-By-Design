"""
AI-suggested design adequacy for newly mapped controls.

Entering the controls stage maps controls and checks them against the
uploaded documents. This adds the first assessment of each control that has
none yet: the model's view of whether the control type is designed to
address the risks it is mapped to. It is recorded as an ordinary (version 1)
ControlAssessment so the analyst sees it pre-filled in "Assess control" and
can change it; it never replaces an assessment that already exists.

It sets design adequacy only. Operating effectiveness stays UNVERIFIED and
`has_evidence` stays false until an analyst accepts evidence (which starts the
rating -- see control_evidence_service.accept_link), so nothing here earns a
control any credit. Like the evidence check, the control type is reviewed
once for all of its mappings, and a failure never blocks the stage.
"""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy.orm import Session

from app.ai import provider
from app.ai.control_identifier import assess_control_design
from app.ai.metering import ai_assessment_context
from app.models.assessment import Assessment
from app.models.control import Control, ControlAssessment
from app.models.risk_factor import RiskFactor
from app.services.control_evidence_service import combined_risk_rationale

logger = logging.getLogger(__name__)

AI_ASSESSOR = "AI (suggested)"
DESIGN_NOTE = "AI-suggested design review:"
# The model answers DESIGN_ADEQUATE / DESIGN_INADEQUATE; the API and the
# "Assess control" form use ADEQUATE / INADEQUATE. Both mean the same, so
# the suggestion is stored in the form's vocabulary and shows in its select.
_VERDICTS = {"DESIGN_ADEQUATE": "ADEQUATE", "DESIGN_INADEQUATE": "INADEQUATE"}


def suggest_designs(db: Session, assessment_id: int) -> dict[str, Any]:
    """Record an AI design suggestion for every current control that has no
    assessment yet. Returns {"status": "OK" | "AI_UNAVAILABLE" | "NOTHING_TO_DO",
    "controls_suggested": n, "controls_skipped": n}."""

    if not provider.API_KEY:
        return {"status": "AI_UNAVAILABLE", "controls_suggested": 0, "controls_skipped": 0}

    controls = (
        db.query(Control)
        .filter(Control.assessment_id == assessment_id, Control.is_current.is_(True))
        .order_by(Control.id.asc())
        .all()
    )
    assessed = {
        row.control_id
        for row in db.query(ControlAssessment.control_id).filter(
            ControlAssessment.assessment_id == assessment_id, ControlAssessment.is_current.is_(True)
        )
    }
    pending = [control for control in controls if control.id not in assessed]
    if not pending:
        return {"status": "NOTHING_TO_DO", "controls_suggested": 0, "controls_skipped": 0}

    factors = {
        factor.id: factor
        for factor in db.query(RiskFactor).filter(
            RiskFactor.assessment_id == assessment_id, RiskFactor.is_current.is_(True)
        )
    }
    assessment = db.query(Assessment).filter(Assessment.id == assessment_id).first()
    context = {
        "assessment_title": assessment.title if assessment else None,
        "change_type": assessment.change_type if assessment else None,
    }

    groups: dict[str, list[Control]] = {}
    for control in pending:
        groups.setdefault(control.control_type, []).append(control)

    suggested = skipped = 0
    with ai_assessment_context(assessment_id):
        for control_type, members in groups.items():
            try:
                rationale = combined_risk_rationale(members, factors) or ""
                result = assess_control_design(control_type, rationale, context)
                verdict = _VERDICTS.get(result.get("design_adequacy"))
                if verdict is None:
                    skipped += len(members)
                    continue
                note = f"{DESIGN_NOTE} {result.get('rationale') or ''}".strip()
                for control in members:
                    db.add(
                        ControlAssessment(
                            control_id=control.id,
                            assessment_id=assessment_id,
                            design_adequacy=verdict,
                            design_rationale=note,
                            operating_effectiveness="UNVERIFIED",
                            has_evidence=False,
                            coverage_complete=True,
                            depends_on_unavailable_data=False,
                            assessed_by=AI_ASSESSOR,
                            version=1,
                            is_current=True,
                        )
                    )
                db.commit()
                suggested += len(members)
            except Exception as exc:  # noqa: BLE001 -- must never break the stage
                db.rollback()
                skipped += len(members)
                logger.warning("AI design suggestion failed for control type %s: %s", control_type, exc)

    return {"status": "OK", "controls_suggested": suggested, "controls_skipped": skipped}

"""
AI evidence check for an assessment's controls (suggestions only).

`run_evidence_check` reads the assessment's current uploaded documents and
asks the model, control by control, whether they support it. Results are
stored as SUGGESTED links; nothing about a control changes until an analyst
accepts one (`accept_link`), which records evidence on the control exactly
as a manual "has evidence" assessment would. Never raises: a failed check
leaves the assessment as it was.

The same control type is usually mapped to several risks. It is checked once
per type (not once per mapping) and every mapping receives that result, so
the model is called a handful of times rather than dozens and two mappings of
one control can't come back with contradictory verdicts.

When an analyst accepts evidence, the control also gets a starting rating from
the model's reading of it (see `ai_effectiveness_for`). That rating is capped
below EFFECTIVE, is labelled as AI-suggested, and never replaces a rating an
analyst has set.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any

from sqlalchemy.orm import Session

from app.ai import evidence_checker, provider
from app.ai.metering import ai_assessment_context, prompt_version
from app.models.assessment_document import AssessmentDocument
from app.models.control import Control, ControlAssessment
from app.models.control_evidence import ControlEvidenceLink
from app.models.risk_factor import RiskFactor

logger = logging.getLogger(__name__)

# Decisions the analyst has already made are kept across re-runs.
DECIDED = ("ACCEPTED", "REJECTED")

# Marks an effectiveness rating that came from the model's reading of accepted
# evidence rather than from an analyst. It is replaced by a later suggestion
# but never overwrites an analyst's own rating.
AI_RATING_NOTE = "AI-suggested from accepted evidence; confirm or change under Assess control."

# The model can say a control is partly effective or ineffective. It can't
# say EFFECTIVE on its own: that needs an analyst's confirmation (testing,
# operation), so an AI "EFFECTIVE" is recorded as PARTIALLY_EFFECTIVE.
_AI_RATINGS = {
    "EFFECTIVE": "PARTIALLY_EFFECTIVE",
    "PARTIALLY_EFFECTIVE": "PARTIALLY_EFFECTIVE",
    "INEFFECTIVE": "INEFFECTIVE",
}

MAX_RATIONALES = 4
MAX_RATIONALE_CHARS = 300


def ai_effectiveness_for(link: ControlEvidenceLink) -> str | None:
    """The rating to start a control at from an accepted link, or None when the
    model had no usable view (then the control stays UNVERIFIED)."""

    return _AI_RATINGS.get((link.suggested_effectiveness or "").upper())


def combined_risk_rationale(controls: list[Control], factors: dict[int, RiskFactor]) -> str | None:
    """The rationales of the distinct risks a control type is mapped to, so one
    check or design review covers all of its mappings."""

    seen: list[str] = []
    for control in controls:
        factor = factors.get(control.risk_factor_id)
        text = (factor.rationale or "").strip() if factor else ""
        text = text[:MAX_RATIONALE_CHARS]
        if text and text not in seen:
            seen.append(text)
    return " | ".join(seen[:MAX_RATIONALES]) or None


def _now() -> datetime:
    return datetime.now(timezone.utc)


def current_documents(db: Session, assessment_id: int) -> list[AssessmentDocument]:
    return (
        db.query(AssessmentDocument)
        .filter(AssessmentDocument.assessment_id == assessment_id, AssessmentDocument.is_current.is_(True))
        .order_by(AssessmentDocument.id.asc())
        .all()
    )


def run_evidence_check(db: Session, assessment_id: int) -> dict[str, Any]:
    """Check every mapped control against the current documents. Returns a
    summary {"status": "OK" | "NO_DOCUMENTS" | "AI_UNAVAILABLE" | "NO_CONTROLS",
    "controls_checked": n, "controls_failed": n}."""

    documents = current_documents(db, assessment_id)
    if not documents:
        return {"status": "NO_DOCUMENTS", "controls_checked": 0, "controls_failed": 0}
    if not provider.API_KEY:
        return {"status": "AI_UNAVAILABLE", "controls_checked": 0, "controls_failed": 0}

    controls = (
        db.query(Control)
        .filter(Control.assessment_id == assessment_id, Control.is_current.is_(True))
        .order_by(Control.id.asc())
        .all()
    )
    if not controls:
        return {"status": "NO_CONTROLS", "controls_checked": 0, "controls_failed": 0}

    factors = {
        factor.id: factor
        for factor in db.query(RiskFactor).filter(
            RiskFactor.assessment_id == assessment_id, RiskFactor.is_current.is_(True)
        )
    }
    payload_documents = [
        {"id": d.id, "filename": d.filename, "text": d.extracted_text or ""} for d in documents
    ]
    versions = {d.id: d.version for d in documents}

    groups: dict[str, list[Control]] = {}
    for control in controls:
        groups.setdefault(control.control_type, []).append(control)

    checked = failed = 0
    with ai_assessment_context(assessment_id):
        for control_type, members in groups.items():
            try:
                outcome = evidence_checker.check_control_evidence(
                    control_type,
                    members[0].description,
                    combined_risk_rationale(members, factors),
                    payload_documents,
                )
                if outcome is None:
                    failed += len(members)
                    continue
                for control in members:
                    _store_outcome(db, assessment_id, control, outcome, versions)
                db.commit()
                checked += len(members)
            except Exception as exc:  # noqa: BLE001 -- a check must never break the stage
                db.rollback()
                failed += len(members)
                logger.warning("Evidence check failed for control type %s: %s", control_type, exc)

    return {"status": "OK", "controls_checked": checked, "controls_failed": failed}


def _store_outcome(
    db: Session,
    assessment_id: int,
    control: Control,
    outcome: dict[str, Any],
    versions: dict[int, int],
) -> None:
    existing = (
        db.query(ControlEvidenceLink)
        .filter(ControlEvidenceLink.control_id == control.id, ControlEvidenceLink.status != "SUPERSEDED")
        .all()
    )
    # Unresolved suggestions are replaced by this run's; decisions stay.
    for link in existing:
        if link.status == "SUGGESTED":
            link.status = "SUPERSEDED"
    decided = {(link.document_id, link.document_version) for link in existing if link.status in DECIDED}

    model = outcome.get("model")
    version = prompt_version("CONTROL_EVIDENCE_CHECK")
    results = outcome["results"]

    if not results:
        db.add(
            ControlEvidenceLink(
                assessment_id=assessment_id,
                control_id=control.id,
                support_level="NONE",
                rationale="No uploaded document was found that supports this control.",
                status="SUGGESTED",
                model=model,
                prompt_version=version,
            )
        )
        return

    for result in results:
        document_version = versions.get(result["document_id"])
        if (result["document_id"], document_version) in decided:
            continue
        db.add(
            ControlEvidenceLink(
                assessment_id=assessment_id,
                control_id=control.id,
                document_id=result["document_id"],
                document_version=document_version,
                support_level=result["support_level"],
                confidence=result["confidence"],
                quote=result["quote"],
                rationale=result["rationale"],
                shortfalls=json.dumps(result["shortfalls"]),
                suggested_effectiveness=outcome.get("suggested_effectiveness"),
                status="SUGGESTED",
                model=model,
                prompt_version=version,
            )
        )


def accept_link(db: Session, control: Control, link: ControlEvidenceLink, user_name: str, user_id: int) -> bool:
    """Record the accepted evidence on the control. Returns True when a new
    ControlAssessment version was written (False if the control already had
    evidence recorded). The caller recomputes control state and commits."""

    link.status = "ACCEPTED"
    link.decided_by = user_name
    link.decided_by_id = user_id
    link.decided_at = _now()

    previous = (
        db.query(ControlAssessment)
        .filter(ControlAssessment.control_id == control.id, ControlAssessment.is_current.is_(True))
        .first()
    )
    if previous is not None and previous.has_evidence:
        return False

    next_version = 1
    carried: dict[str, Any] = {}
    if previous is not None:
        previous.is_current = False
        previous.superseded_at = _now()
        next_version = previous.version + 1
        carried = {
            "design_adequacy": previous.design_adequacy,
            "design_rationale": previous.design_rationale,
            "operating_effectiveness": previous.operating_effectiveness,
            "effectiveness_rationale": previous.effectiveness_rationale,
            "coverage_complete": previous.coverage_complete,
            "depends_on_unavailable_data": previous.depends_on_unavailable_data,
        }

    # Start the rating from the model's reading of the evidence, unless an
    # analyst has already rated the control: a rating counts as the analyst's
    # when it is anything but an unexplained UNVERIFIED or an earlier AI one.
    rating = carried.get("operating_effectiveness", "UNVERIFIED")
    rationale = (carried.get("effectiveness_rationale") or "").strip()
    suggested = ai_effectiveness_for(link)
    if suggested and ((rating == "UNVERIFIED" and not rationale) or rationale.startswith(AI_RATING_NOTE)):
        carried["operating_effectiveness"] = suggested
        carried["effectiveness_rationale"] = AI_RATING_NOTE

    db.add(
        ControlAssessment(
            control_id=control.id,
            assessment_id=control.assessment_id,
            has_evidence=True,
            assessed_by=user_name,
            version=next_version,
            is_current=True,
            **carried,
        )
    )
    db.flush()
    return True


def reject_link(link: ControlEvidenceLink, user_name: str, user_id: int, note: str | None) -> None:
    link.status = "REJECTED"
    link.decided_by = user_name
    link.decided_by_id = user_id
    link.decided_at = _now()
    link.decision_note = note

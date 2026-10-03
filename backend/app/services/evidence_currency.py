"""
P4 (R2.6, Stage 2 AC): "Given an expired or superseded document, then the
system warns the user before it is used as current evidence."

One rule for whether a document may be used as current evidence:

  superseded (not is_current)        never used; a newer version replaced it
  no expiry date / not yet expired   used
  expired, or an unreadable expiry   used only after a person acknowledges
    date                             it (USE_AS_EVIDENCE, with a reason);
                                     EXCLUDE_FROM_EVIDENCE keeps it on file
                                     but out of the analysis

An acknowledgement is bound to the document version and the expiry date it
was made against. Risk identification refuses to run while any current
document is unacknowledged (`ensure_no_unacknowledged`), and the analysis
only ever reads `usable_documents`.
"""

from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Iterable

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.models.assessment_document import AssessmentDocument
from app.models.evidence_traceability import EvidenceAcknowledgement

USE_AS_EVIDENCE = "USE_AS_EVIDENCE"
EXCLUDE_FROM_EVIDENCE = "EXCLUDE_FROM_EVIDENCE"
DECISIONS = {USE_AS_EVIDENCE, EXCLUDE_FROM_EVIDENCE}

CURRENT = "CURRENT"
EXPIRED = "EXPIRED"
INVALID_EXPIRY_DATE = "INVALID_EXPIRY_DATE"
SUPERSEDED = "SUPERSEDED"

MIN_REASON_LENGTH = 10


def _today() -> date:
    return datetime.now(timezone.utc).date()


def expiry_condition(document: AssessmentDocument, today: date | None = None) -> str:
    if not document.is_current:
        return SUPERSEDED
    raw = (document.expiry_date or "").strip()
    if not raw:
        return CURRENT
    try:
        expiry = date.fromisoformat(raw[:10])
    except ValueError:
        return INVALID_EXPIRY_DATE
    return EXPIRED if expiry < (today or _today()) else CURRENT


def latest_acknowledgement(db: Session, document: AssessmentDocument) -> EvidenceAcknowledgement | None:
    """The acknowledgement in force: the latest one for this exact version
    and expiry date."""

    rows = (
        db.query(EvidenceAcknowledgement)
        .filter(
            EvidenceAcknowledgement.document_id == document.id,
            EvidenceAcknowledgement.document_version == document.version,
        )
        .order_by(EvidenceAcknowledgement.id.desc())
        .all()
    )
    return next((ack for ack in rows if (ack.expiry_date or "") == (document.expiry_date or "")), None)


def evaluate(db: Session, document: AssessmentDocument, today: date | None = None) -> dict:
    condition = expiry_condition(document, today)
    ack = latest_acknowledgement(db, document) if condition in {EXPIRED, INVALID_EXPIRY_DATE} else None
    if condition == SUPERSEDED:
        usable, state = False, "SUPERSEDED"
    elif condition == CURRENT:
        usable, state = True, "CURRENT"
    elif ack is None:
        usable, state = False, "ACKNOWLEDGEMENT_REQUIRED"
    elif ack.decision == USE_AS_EVIDENCE:
        usable, state = True, "ACKNOWLEDGED_FOR_USE"
    else:
        usable, state = False, "EXCLUDED_FROM_EVIDENCE"
    return {
        "document_id": document.id,
        "document_version": document.version,
        "filename": document.filename,
        "expiry_date": document.expiry_date,
        "condition": condition,
        "state": state,
        "usable_as_evidence": usable,
        "acknowledgement": None
        if ack is None
        else {
            "id": ack.id,
            "decision": ack.decision,
            "reason": ack.reason,
            "acknowledged_by": ack.acknowledged_by,
            "acknowledged_at": ack.created_at.isoformat() if ack.created_at else None,
        },
    }


def usable_documents(db: Session, documents: Iterable[AssessmentDocument]) -> list[AssessmentDocument]:
    return [document for document in documents if evaluate(db, document)["usable_as_evidence"]]


def current_documents(db: Session, assessment_id: int) -> list[AssessmentDocument]:
    return (
        db.query(AssessmentDocument)
        .filter(AssessmentDocument.assessment_id == assessment_id, AssessmentDocument.is_current.is_(True))
        .order_by(AssessmentDocument.id)
        .all()
    )


def pending_acknowledgements(db: Session, assessment_id: int) -> list[dict]:
    return [
        row
        for row in (evaluate(db, document) for document in current_documents(db, assessment_id))
        if row["state"] == "ACKNOWLEDGEMENT_REQUIRED"
    ]


def ensure_no_unacknowledged(db: Session, assessment_id: int) -> None:
    pending = pending_acknowledgements(db, assessment_id)
    if pending:
        names = ", ".join(f"{row['filename']} (expiry {row['expiry_date']})" for row in pending)
        raise HTTPException(
            status_code=409,
            detail={
                "message": (
                    "Expired evidence must be acknowledged before it is used: "
                    f"{names}. Use it as evidence with a reason, exclude it, or upload a current version."
                ),
                "code": "EXPIRED_EVIDENCE_UNACKNOWLEDGED",
                "documents": pending,
            },
        )

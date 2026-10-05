"""
Amendment after a manager return: did the owner's inputs change?

A manager returns an assessment (RETURNED_BY_MANAGER, "Amendment Required")
for correction. While it is returned nothing is re-run: the owner may take
as long as needed to upload documents and fix details. When the owner
resubmits, this decides whether the risk assessment still reflects the
inputs. If documents or request details changed since the manager's return,
the assessment goes back through analysis (Evidence Collection onward)
instead of straight back to the manager; if nothing material changed it
goes straight back.

"Since the return" is the manager's decision time. Documents are compared
by when they were added (a replaced version is a new row); request details
and the business profile by the intake change history. A re-confirmation
that changed nothing does not count.
"""

from __future__ import annotations

import json
from datetime import timezone
from typing import Any

from sqlalchemy.orm import Session

from app.models.assessment import Assessment
from app.models.assessment_document import AssessmentDocument
from app.models.evidence_traceability import IntakeSnapshot

RETURNED = "RETURNED_BY_MANAGER"
# Edits to these records change what the risk assessment was based on.
_MATERIAL_TRIGGERS = ("EDITED", "PROFILE_CORRECTED")


def _naive_utc(value):
    if value is None:
        return None
    if value.tzinfo is not None:
        value = value.astimezone(timezone.utc).replace(tzinfo=None)
    return value


def inputs_changed_since_return(db: Session, assessment: Assessment) -> dict[str, Any]:
    """{"requires_reanalysis": bool, "since": datetime | None, "changes": [{"kind", "description"}]}.
    Only meaningful while the assessment is RETURNED_BY_MANAGER."""

    since = _naive_utc(assessment.manager_decided_at)
    if assessment.status != RETURNED or since is None:
        return {"requires_reanalysis": False, "since": assessment.manager_decided_at, "changes": []}

    changes: list[dict[str, str]] = []

    documents = (
        db.query(AssessmentDocument)
        .filter(AssessmentDocument.assessment_id == assessment.id, AssessmentDocument.created_at > since)
        .order_by(AssessmentDocument.id.asc())
        .all()
    )
    for document in documents:
        replaced = document.supersedes_id is not None
        changes.append(
            {
                "kind": "DOCUMENT",
                "description": (
                    f"Document replaced: {document.filename} (v{document.version})"
                    if replaced
                    else f"Document added: {document.filename}"
                ),
            }
        )

    snapshots = (
        db.query(IntakeSnapshot)
        .filter(
            IntakeSnapshot.assessment_id == assessment.id,
            IntakeSnapshot.created_at > since,
            IntakeSnapshot.trigger.in_(_MATERIAL_TRIGGERS),
        )
        .order_by(IntakeSnapshot.id.asc())
        .all()
    )
    for snapshot in snapshots:
        try:
            fields = sorted({item["field"] for item in json.loads(snapshot.changes or "[]")})
        except (ValueError, KeyError, TypeError):
            fields = []
        if not fields:
            continue
        what = "Request details" if snapshot.record_type == "ASSESSMENT_REQUEST" else "Business profile"
        changes.append({"kind": snapshot.record_type, "description": f"{what} changed: {', '.join(fields)}"})

    return {
        "requires_reanalysis": bool(changes),
        "since": assessment.manager_decided_at,
        "changes": changes,
    }

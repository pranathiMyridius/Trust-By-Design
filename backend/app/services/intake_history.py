"""
P4 (R3.4): versioned snapshots of the intake record.

"The original request must remain available even after corrections or
updates", and "given a change to the profile after validation, the system
records the user, time, changed fields, and reason". Every change to the
business request or the structured profile appends an IntakeSnapshot with
the full values after the change and each changed field's old and new
value. A record that existed before P4 gets a BASELINE snapshot of its
state just before its first recorded change, so nothing earlier than
that is claimed.
"""

from __future__ import annotations

import json
from datetime import date, datetime
from typing import Any

from sqlalchemy.orm import Session

from app.models.evidence_traceability import IntakeSnapshot

ASSESSMENT_REQUEST = "ASSESSMENT_REQUEST"
BUSINESS_PROFILE = "BUSINESS_PROFILE"

REQUEST_FIELDS = [
    "title",
    "change_type",
    "description",
    "evidence",
    "product_or_service_name",
    "business_owner",
    "legal_entity",
    "business_unit",
    "customer_segment",
    "countries_jurisdictions",
    "delivery_channels",
    "expected_transaction_volume",
    "expected_transaction_value",
    "transaction_types",
    "third_party_vendor_usage",
    "technology_process_changes",
    "expected_launch_date",
    "shell_company_indicator",
    "is_draft",
]

PROFILE_LIST_FIELDS = [
    "payment_methods",
    "channels",
    "countries",
    "customer_segments",
    "third_party_vendors",
    "data_shared",
    "technologies",
    "regulatory_considerations",
    "existing_controls",
    "additional_risk_factors",
]
PROFILE_TEXT_FIELDS = [
    "business_line",
    "customer_type",
    "transaction_origin",
    "transaction_destination",
    "transaction_frequency",
    "onboarding_approach",
    "ownership_entity_structure",
    "transaction_volume",
    "average_transaction_size",
    "maximum_transaction_limit",
]
PROFILE_FIELDS = PROFILE_TEXT_FIELDS + PROFILE_LIST_FIELDS


def _plain(value: Any) -> Any:
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    return value


def request_values(assessment) -> dict[str, Any]:
    return {field: _plain(getattr(assessment, field, None)) for field in REQUEST_FIELDS}


def profile_values(intelligence) -> dict[str, Any]:
    values = {field: getattr(intelligence, field, None) for field in PROFILE_TEXT_FIELDS}
    values.update({field: intelligence.get_list(field) for field in PROFILE_LIST_FIELDS})
    values["confirmed"] = bool(intelligence.confirmed)
    return values


def diff(before: dict[str, Any], after: dict[str, Any], fields: list[str] | None = None) -> list[dict[str, Any]]:
    keys = fields or sorted(set(before) | set(after))
    changes = []
    for field in keys:
        old, new = before.get(field), after.get(field)
        if old in ("", []) and new in (None, "", []):
            continue
        if old is None and new in ("", []):
            continue
        if old != new:
            changes.append({"field": field, "old": old, "new": new})
    return changes


def _latest(db: Session, assessment_id: int, record_type: str) -> IntakeSnapshot | None:
    return (
        db.query(IntakeSnapshot)
        .filter(IntakeSnapshot.assessment_id == assessment_id, IntakeSnapshot.record_type == record_type)
        .order_by(IntakeSnapshot.version.desc())
        .first()
    )


def record(
    db: Session,
    assessment_id: int,
    record_type: str,
    *,
    after: dict[str, Any],
    trigger: str,
    user=None,
    actor: str | None = None,
    reason: str | None = None,
    changes: list[dict[str, Any]] | None = None,
    before: dict[str, Any] | None = None,
    was_validated: bool = False,
) -> IntakeSnapshot:
    """Appends a snapshot. With `before` and no earlier snapshot for this
    record, the pre-change state is kept first as the BASELINE. Does not
    commit."""

    name = actor or (user.full_name or user.email if user is not None else "System")
    latest = _latest(db, assessment_id, record_type)
    version = latest.version if latest else 0

    if latest is None and before is not None:
        version += 1
        db.add(
            IntakeSnapshot(
                assessment_id=assessment_id,
                record_type=record_type,
                version=version,
                trigger="BASELINE",
                snapshot=json.dumps(before, default=str),
                changes="[]",
                reason="State before the first recorded change (earlier history was not versioned).",
                was_validated=was_validated,
                changed_by="System",
            )
        )

    version += 1
    row = IntakeSnapshot(
        assessment_id=assessment_id,
        record_type=record_type,
        version=version,
        trigger=trigger,
        snapshot=json.dumps(after, default=str),
        changes=json.dumps(changes or [], default=str),
        reason=reason,
        was_validated=was_validated,
        changed_by=name,
        changed_by_id=getattr(user, "id", None),
    )
    db.add(row)
    db.flush()
    return row


def history(db: Session, assessment_id: int, record_type: str | None = None) -> list[dict[str, Any]]:
    query = db.query(IntakeSnapshot).filter(IntakeSnapshot.assessment_id == assessment_id)
    if record_type:
        query = query.filter(IntakeSnapshot.record_type == record_type)
    rows = query.order_by(IntakeSnapshot.record_type, IntakeSnapshot.version).all()
    return [
        {
            "id": row.id,
            "record_type": row.record_type,
            "version": row.version,
            "trigger": row.trigger,
            "snapshot": json.loads(row.snapshot or "{}"),
            "changes": json.loads(row.changes or "[]"),
            "reason": row.reason,
            "was_validated": row.was_validated,
            "changed_by": row.changed_by,
            "changed_by_id": row.changed_by_id,
            "created_at": row.created_at,
        }
        for row in rows
    ]


def describe_changes(changes: list[dict[str, Any]], limit: int = 200) -> str:
    """Audit-detail text: field: 'old' -> 'new', each value truncated."""

    def short(value: Any) -> str:
        text = json.dumps(value, default=str) if isinstance(value, (list, dict)) else repr(value)
        return text if len(text) <= limit else text[: limit - 3] + "..."

    return "; ".join(f"{c['field']}: {short(c['old'])} -> {short(c['new'])}" for c in changes)

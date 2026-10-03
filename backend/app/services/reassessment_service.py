"""
Stage 18 (R18.1-R18.4): reassessment trigger detection, and opening a
reassessment as a new, independently-pipelined Assessment linked to its
parent via parent_assessment_id.

Field-level trigger detection reuses the same explainable, token-overlap
heuristic as app/services/consistency_check.py (R3.2) -- a trigger is
flagged when the parent and proposed values share no common token, so
every flagged trigger is traceable to the two exact values compared.
"""

import calendar
import json
from datetime import date, datetime, timedelta, timezone

from sqlalchemy.orm import Session

from app.models.assessment import Assessment
from app.models.reassessment_trigger import ReassessmentTrigger
from app.schemas.reassessment import ProposeChangeRequest
from app.services.consistency_check import _tokens

# R18.1: (Assessment field, trigger type, human label).
_FIELD_TRIGGERS: list[tuple[str, str, str]] = [
    ("product_or_service_name", "MAJOR_PRODUCT_CHANGE", "Product or service name"),
    ("countries_jurisdictions", "NEW_GEOGRAPHY", "Countries/jurisdictions"),
    ("customer_segment", "NEW_CUSTOMER_SEGMENT", "Customer segment"),
    ("third_party_vendor_usage", "NEW_VENDOR", "Third-party vendor usage"),
    (
        "expected_transaction_volume",
        "MATERIAL_TRANSACTION_VOLUME_CHANGE",
        "Expected transaction volume",
    ),
    ("delivery_channels", "NEW_DELIVERY_CHANNEL", "Delivery channels"),
    ("technology_process_changes", "NEW_TECHNOLOGY", "Technology or process changes"),
]

# R18.1: months an approval stays valid before PERIODIC_REVIEW/EXPIRY
# fire, by the assessment's residual (else inherent) band -- higher risk
# is reviewed sooner. The single source for reassessment due dates; the
# governance report reads next_review_date rather than recomputing.
REASSESSMENT_INTERVAL_MONTHS = {"CRITICAL": 6, "HIGH": 12, "MEDIUM": 24, "LOW": 36}
DEFAULT_REASSESSMENT_INTERVAL_MONTHS = 12
# A review becomes due (PERIODIC_REVIEW, not yet EXPIRY) this many days
# before next_review_date.
PERIODIC_REVIEW_LEAD_DAYS = 30


def review_interval_months(assessment: Assessment) -> int:
    band = (assessment.residual_risk_level or assessment.inherent_risk_level or assessment.risk_level or "").upper()
    return REASSESSMENT_INTERVAL_MONTHS.get(band, DEFAULT_REASSESSMENT_INTERVAL_MONTHS)


def _add_months(value: date, months: int) -> date:
    month_index = value.month - 1 + months
    year = value.year + month_index // 12
    month = month_index % 12 + 1
    return date(year, month, min(value.day, calendar.monthrange(year, month)[1]))


def set_next_review_date(assessment: Assessment) -> None:
    """Called on an APPROVED/APPROVED_WITH_CONDITIONS committee decision."""

    assessment.next_review_date = _add_months(
        datetime.now(timezone.utc).date(), review_interval_months(assessment)
    )


def detect_date_triggers(assessment: Assessment) -> list[tuple[str, str]]:
    """R18.1: EXPIRY / PERIODIC_REVIEW, evaluated on demand."""

    if not assessment.next_review_date:
        return []

    today = datetime.now(timezone.utc).date()
    triggers = []

    if today > assessment.next_review_date:
        triggers.append(
            (
                "EXPIRY",
                f"This assessment's approval expired on {assessment.next_review_date} "
                "and has not been reassessed.",
            )
        )
    elif today >= assessment.next_review_date - timedelta(days=PERIODIC_REVIEW_LEAD_DAYS):
        triggers.append(
            (
                "PERIODIC_REVIEW",
                f"This assessment's periodic review is due on {assessment.next_review_date}.",
            )
        )

    return triggers


def detect_field_triggers(
    parent: Assessment, changes: dict
) -> list[tuple[str, str]]:
    """R18.1: field-level triggers from a proposed change vs. the parent."""

    triggers = []

    for field_name, trigger_type, label in _FIELD_TRIGGERS:
        proposed_value = changes.get(field_name)
        if proposed_value is None:
            continue

        parent_value = getattr(parent, field_name, None)
        parent_tokens = _tokens(parent_value)
        proposed_tokens = _tokens(proposed_value)

        if not proposed_tokens:
            continue

        # Unlike consistency_check's contradiction detector (any overlap
        # at all means "not a conflict"), this needs the opposite: a
        # genuinely new token added (e.g. one more country/vendor in an
        # otherwise-unchanged list) must still fire, even though most of
        # the list still overlaps. So: fire on ANY difference between the
        # two token sets, not just "no overlap".
        if proposed_tokens != parent_tokens:
            triggers.append(
                (
                    trigger_type,
                    f"{label} changed: {parent_value!r} -> {proposed_value!r}.",
                )
            )

    return triggers


def open_reassessment(
    db: Session,
    parent: Assessment,
    payload: ProposeChangeRequest,
) -> tuple[Assessment, list[ReassessmentTrigger]]:
    """
    R18.4: the previous approval cannot automatically remain valid --
    always creates a new, independently-pipelined Assessment (starting
    at INTAKE) rather than editing `parent` in place. Does not commit.
    """

    changes = payload.changes.model_dump(exclude_unset=True, exclude_none=True)
    field_triggers = detect_field_triggers(parent, changes)

    reusable_fields = [
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
    ]

    # R18.3: reuse prior information carefully -- carry forward whatever
    # wasn't explicitly proposed as changed, and record exactly where it
    # came from and how old it is.
    reused_sources = {}
    child_kwargs = {}

    for field_name in reusable_fields:
        if field_name in changes:
            child_kwargs[field_name] = changes[field_name]
        else:
            child_kwargs[field_name] = getattr(parent, field_name, None)
            reused_sources[field_name] = {
                "source_assessment_id": parent.id,
                "source_date": parent.updated_at.isoformat() if parent.updated_at else None,
            }

    child = Assessment(
        **child_kwargs,
        is_draft=False,
        submitted_by=payload.proposed_by,
        submitted_at=datetime.now(timezone.utc),
        owner_id=parent.owner_id,
        parent_assessment_id=parent.id,
        reused_field_sources=json.dumps(reused_sources),
    )
    db.add(child)
    db.flush()
    # P6: a reassessment is a case of its own and gets its own reference,
    # like any intake (it previously had none).
    from app.services.reference_id import generate_reference_id

    child.reference_id = generate_reference_id(child.id)

    trigger_rows = []
    for trigger_type, description in field_triggers:
        trigger = ReassessmentTrigger(
            assessment_id=parent.id,
            trigger_type=trigger_type,
            description=description,
            detected_by=payload.proposed_by,
            status="REASSESSMENT_CREATED",
            resolved_by=payload.proposed_by,
            resolved_at=datetime.now(timezone.utc),
            reassessment_id=child.id,
        )
        db.add(trigger)
        trigger_rows.append(trigger)

    # Even with no specific field trigger detected, the owner explicitly
    # asked for a reassessment (`payload.reason`) -- record that too so
    # the "why" is never just "a new Assessment row appeared".
    if not trigger_rows:
        trigger = ReassessmentTrigger(
            assessment_id=parent.id,
            trigger_type="MAJOR_PRODUCT_CHANGE",
            description=f"Manually requested reassessment: {payload.reason}",
            detected_by=payload.proposed_by,
            status="REASSESSMENT_CREATED",
            resolved_by=payload.proposed_by,
            resolved_at=datetime.now(timezone.utc),
            reassessment_id=child.id,
        )
        db.add(trigger)
        trigger_rows.append(trigger)

    db.flush()

    return child, trigger_rows


def record_date_triggers(db: Session, assessment: Assessment) -> list[ReassessmentTrigger]:
    """
    R18.1 / Stage 18 AC2: persists a new OPEN EXPIRY / PERIODIC_REVIEW
    trigger the first time it is detected (never re-flagging an open one)
    and records who it is for -- the assessment's owner and its reviewing
    manager, whose work queues show it. Does not commit.
    """

    from app.models.user import User
    from app.services.audit_service import AuditAction, log_audit_event

    from app.services import reassessment_lifecycle as lifecycle

    # P6: a superseded approval is no longer due for anything.
    if assessment.reassessment_state == lifecycle.SUPERSEDED:
        return []
    detected = detect_date_triggers(assessment)
    if not detected:
        return []
    child = lifecycle.in_progress_child(db, assessment.id) if assessment.reassessment_state == lifecycle.UNDER_REASSESSMENT else None

    open_types = {
        row.trigger_type
        for row in db.query(ReassessmentTrigger.trigger_type).filter(
            ReassessmentTrigger.assessment_id == assessment.id,
            ReassessmentTrigger.status.in_(["OPEN", "ACKNOWLEDGED"])
            | ((ReassessmentTrigger.status == "REASSESSMENT_CREATED") & (ReassessmentTrigger.reassessment_id == (child.id if child else -1))),
        )
    }

    created = []
    for trigger_type, description in detected:
        if trigger_type in open_types:
            continue
        trigger = ReassessmentTrigger(
            assessment_id=assessment.id,
            trigger_type=trigger_type,
            description=description,
            detected_by="System",
            status="OPEN",
        )
        if child is not None:
            # Already being reassessed: record it against that reassessment.
            trigger.status = "REASSESSMENT_CREATED"
            trigger.reassessment_id = child.id
            trigger.resolved_by = "System"
            trigger.resolved_at = datetime.now(timezone.utc)
            trigger.resolution_note = f"Raised while reassessment #{child.id} is in progress."
        db.add(trigger)
        created.append(trigger)

    if created:
        ids = [user_id for user_id in (assessment.owner_id, assessment.manager_id) if user_id]
        recipients = db.query(User).filter(User.id.in_(ids)).all() if ids else []
        notified = ", ".join(user.full_name or user.email for user in recipients) or "no assigned owner or reviewer"
        log_audit_event(
            db=db,
            assessment_id=assessment.id,
            action=AuditAction.REASSESSMENT_DUE_DETECTED,
            details=(
                f"Reassessment trigger(s) detected: {', '.join(t.trigger_type for t in created)}"
                + (f" (linked to reassessment #{child.id})" if child else "")
                + f". Notified: {notified}."
            ),
        )
        db.flush()

    return created


def sweep_review_dates(db: Session) -> list[ReassessmentTrigger]:
    """Stage 18 AC2: the scheduled check -- every decided assessment with a
    review date, so expiry is caught without anyone opening it. Commits."""

    created: list[ReassessmentTrigger] = []
    from app.models.audit_trail import soft_deleted_assessment_ids

    candidates = db.query(Assessment).filter(
        Assessment.next_review_date.isnot(None),
        # P6: a superseded approval's review dates no longer apply.
        (Assessment.reassessment_state.is_(None)) | (Assessment.reassessment_state != "SUPERSEDED"),
        # R16.4: nothing is due on a soft-deleted assessment.
        Assessment.id.notin_(soft_deleted_assessment_ids()),
    )
    for assessment in candidates.all():
        created.extend(record_date_triggers(db, assessment))
    if created:
        db.commit()
    return created

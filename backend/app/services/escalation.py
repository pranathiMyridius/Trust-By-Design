import json
import logging
import os
from datetime import date, datetime, timezone

from sqlalchemy.orm import Session

from app.models.action_item import ActionItem, OPEN_STATUSES
from app.models.assessment import Assessment
from app.models.user import User
from app.services.audit_service import AuditAction, log_audit_event

logger = logging.getLogger(__name__)

# R13.3: "overdue actions shall be escalated according to configured
# rules." A per-priority grace period (days past due_date before
# escalation fires) rather than a single fixed cutoff, so a CRITICAL
# action escalates the moment it's overdue while a LOW one gets a week's
# grace. These are the defaults; ACTION_ESCALATION_GRACE_DAYS (JSON, e.g.
# '{"HIGH": 0, "LOW": 14}') overrides any of them.
DEFAULT_ESCALATION_GRACE_DAYS = {
    "CRITICAL": 0,
    "HIGH": 1,
    "MEDIUM": 3,
    "LOW": 7,
}


def escalation_grace_days() -> dict[str, int]:
    rules = dict(DEFAULT_ESCALATION_GRACE_DAYS)
    raw = os.getenv("ACTION_ESCALATION_GRACE_DAYS", "").strip()
    if raw:
        try:
            configured = json.loads(raw)
            rules.update(
                {str(priority).upper(): int(days) for priority, days in configured.items() if int(days) >= 0}
            )
        except (TypeError, ValueError, AttributeError):
            logger.warning("Ignoring invalid ACTION_ESCALATION_GRACE_DAYS: %r", raw)
    return rules



def escalation_recipients(db: Session, item: ActionItem) -> list[User]:
    """R13.3 acceptance criteria: "the owner and relevant reviewer
    receive an escalation". The action's owner is free text, so the
    people notified are the assessment's owner (who raised it) and its
    manager (the reviewer); escalated items appear in both work queues."""

    assessment = db.get(Assessment, item.assessment_id)
    if assessment is None:
        return []
    ids = [user_id for user_id in (assessment.owner_id, assessment.manager_id) if user_id]
    return db.query(User).filter(User.id.in_(ids)).all() if ids else []


def escalate_overdue_action_items(db: Session, assessment_id: int | None = None) -> list[ActionItem]:
    """
    Sweeps open action items for any that are now overdue (past their
    priority's grace period) and haven't already been escalated, marks
    them escalated, and logs an audit event naming the owner so there is
    a durable record of "the owner and relevant reviewer received an
    escalation" (this app has no outbound email/notification channel --
    the audit trail plus the `escalated` flag surfaced in the UI is the
    escalation).

    Called opportunistically whenever action items are listed, so
    escalation doesn't depend on a cron/scheduler existing.
    """

    query = db.query(ActionItem).filter(
        ActionItem.status.in_(OPEN_STATUSES),
        ActionItem.escalated.is_(False),
        ActionItem.due_date.isnot(None),
    )
    if assessment_id is not None:
        query = query.filter(ActionItem.assessment_id == assessment_id)

    today = date.today()
    newly_escalated: list[ActionItem] = []
    rules = escalation_grace_days()

    for item in query.all():
        grace_days = rules.get(item.priority, rules["MEDIUM"])
        overdue_days = (today - item.due_date).days
        if overdue_days < grace_days:
            continue

        recipients = escalation_recipients(db, item)
        notified = ", ".join(user.full_name or user.email for user in recipients) or "no assigned reviewer"

        item.escalated = True
        item.escalated_at = datetime.now(timezone.utc)
        item.escalation_note = (
            f"Overdue by {overdue_days} day(s) (priority {item.priority}, "
            f"grace {grace_days} day(s)). Action owner "
            f"'{item.owner or 'unassigned'}', department "
            f"'{item.department or 'unassigned'}'. Escalated to: {notified}."
        )

        log_audit_event(
            db,
            assessment_id=item.assessment_id,
            action=AuditAction.ACTION_ITEM_ESCALATED,
            actor="System",
            details=(
                f"Action item #{item.id} ({item.title}) escalated -- "
                f"{item.escalation_note}"
            ),
        )

        newly_escalated.append(item)

    if newly_escalated:
        db.commit()
        for item in newly_escalated:
            db.refresh(item)

    return newly_escalated

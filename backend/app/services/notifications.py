"""Email notifications for the assessment lifecycle.

How it fits together
--------------------
* Workflow code calls `queue_for_transition()` (from the single place every
  status change is recorded) or `queue_assignment()`. These only note down
  *who* gets *which* template -- nothing is sent yet.
* When the database transaction commits, `_send_after_commit` hands the
  noted messages to a background thread. A rolled-back action therefore
  never sends mail, and a slow or failing mail server never delays or breaks
  the request (see app/services/email_service.py).
* Everything is a no-op unless email is switched on, so tests and existing
  setups are unaffected.

Templates
---------
Each message is a template with {{placeholders}} (see CONTEXT_KEYS). The
built-in wording is below; set EMAIL_TEMPLATES_FILE to a JSON file to change
the wording of any template without touching code:

    {"MANAGER_DECIDED_APPROVE": {"subject": "...", "heading": "...",
                                 "intro": "...", "cta": "Open the assessment"}}

Placeholders are filled with plain values and HTML-escaped in the HTML part.
"""

import html
import json
import logging
import os
import re
import threading
from typing import Any

from sqlalchemy import event
from sqlalchemy.orm import Session

from app.services.email_service import email_enabled, send_email

logger = logging.getLogger(__name__)

# {{name}} values available to every template.
CONTEXT_KEYS = (
    "recipient_name", "title", "reference", "status", "risk_level", "actor",
    "comment", "conditions", "task", "due", "role_label", "app_url", "minutes",
)

_ROLE_LABELS = {
    "ASSIGNED_MANAGER": "Manager",
    "FCRM_ANALYST": "FCRM Analyst",
    "COMMITTEE": "Committee",
    "OWNER": "Business owner",
}

TEMPLATES: dict[str, dict[str, str]] = {
    # -- creation / submission -------------------------------------------------
    "CREATED_OWNER": {
        "subject": "Assessment submitted: {{title}} {{reference}}",
        "heading": "Your assessment has been submitted",
        "intro": "Hello {{recipient_name}}, \"{{title}}\" {{reference}} was submitted and is now with the FCRM team.",
        "cta": "Track your assessment",
    },
    "CREATED_ANALYSTS": {
        "subject": "New assessment to analyse: {{title}} {{reference}}",
        "heading": "A new assessment is waiting for analysis",
        "intro": "Hello {{recipient_name}}, \"{{title}}\" {{reference}} was submitted by {{actor}} and is waiting for the FCRM team.",
        "cta": "Open the assessment",
    },
    # -- manager step ----------------------------------------------------------
    "SUBMITTED_TO_MANAGER_MANAGER": {
        "subject": "Review requested: {{title}} {{reference}}",
        "heading": "An assessment needs your review",
        "intro": "Hello {{recipient_name}}, {{actor}} has submitted \"{{title}}\" {{reference}} to you for challenge review.",
        "cta": "Review the assessment",
    },
    "SUBMITTED_TO_MANAGER_OWNER": {
        "subject": "Submitted to your manager: {{title}} {{reference}}",
        "heading": "Sent to your manager",
        "intro": "Hello {{recipient_name}}, \"{{title}}\" {{reference}} is now with your manager for review.",
        "cta": "Track your assessment",
    },
    "MANAGER_DECIDED_APPROVE": {
        "subject": "Approved by manager: {{title}} {{reference}}",
        "heading": "The manager approved this assessment",
        "intro": "Hello {{recipient_name}}, {{actor}} approved \"{{title}}\" {{reference}}. It now goes to the committee.",
        "cta": "Open the assessment",
    },
    "MANAGER_DECIDED_REJECT": {
        "subject": "Rejected by manager: {{title}} {{reference}}",
        "heading": "The manager rejected this assessment",
        "intro": "Hello {{recipient_name}}, {{actor}} rejected \"{{title}}\" {{reference}}.",
        "cta": "See the manager's comment",
    },
    "MANAGER_DECIDED_RETURN": {
        "subject": "Returned for changes: {{title}} {{reference}}",
        "heading": "The manager returned this assessment for changes",
        "intro": "Hello {{recipient_name}}, {{actor}} returned \"{{title}}\" {{reference}}. Please review the comment, update it and resubmit.",
        "cta": "Make the changes",
    },
    # -- committee step --------------------------------------------------------
    "READY_FOR_COMMITTEE_COMMITTEE": {
        "subject": "Ready for committee: {{title}} {{reference}}",
        "heading": "An assessment is ready for committee review",
        "intro": "Hello {{recipient_name}}, \"{{title}}\" {{reference}} was approved by the manager and is ready for the committee.",
        "cta": "Open the assessment",
    },
    "COMMITTEE_REVIEW_COMMITTEE": {
        "subject": "Committee review opened: {{title}} {{reference}}",
        "heading": "Committee review is open",
        "intro": "Hello {{recipient_name}}, committee review has opened for \"{{title}}\" {{reference}}.",
        "cta": "Open the assessment",
    },
    "COMMITTEE_DECIDED_APPROVED": {
        "subject": "Approved by committee: {{title}} {{reference}}",
        "heading": "The committee approved this assessment",
        "intro": "Hello {{recipient_name}}, the committee ({{actor}}) approved \"{{title}}\" {{reference}}.",
        "cta": "View the decision",
    },
    "COMMITTEE_DECIDED_APPROVED_WITH_CONDITIONS": {
        "subject": "Approved with conditions: {{title}} {{reference}}",
        "heading": "The committee approved this assessment with conditions",
        "intro": "Hello {{recipient_name}}, the committee ({{actor}}) approved \"{{title}}\" {{reference}} subject to conditions.",
        "cta": "View the conditions",
    },
    "COMMITTEE_DECIDED_REJECTED": {
        "subject": "Rejected by committee: {{title}} {{reference}}",
        "heading": "The committee rejected this assessment",
        "intro": "Hello {{recipient_name}}, the committee ({{actor}}) rejected \"{{title}}\" {{reference}}.",
        "cta": "View the decision",
    },
    "COMMITTEE_DECIDED_DEFERRED": {
        "subject": "Deferred by committee: {{title}} {{reference}}",
        "heading": "The committee deferred this assessment",
        "intro": "Hello {{recipient_name}}, the committee ({{actor}}) deferred \"{{title}}\" {{reference}}.",
        "cta": "View the decision",
    },
    # -- account ---------------------------------------------------------------------
    "PASSWORD_RESET": {
        "subject": "Reset your Risk Assessment Workbench password",
        "heading": "Reset your password",
        "intro": "Hello {{recipient_name}}, we received a request to reset the password for your account. The link below is valid for {{minutes}} minutes and can be used once. If you did not ask for this, ignore this email; your password stays as it is.",
        "cta": "Choose a new password",
    },
    # -- explicit assignment, worded for the role it is assigned to ----------------
    "ASSIGNED_ASSIGNED_MANAGER": {
        "subject": "Assigned to you (manager): {{title}} {{reference}}",
        "heading": "A review has been assigned to you",
        "intro": "Hello {{recipient_name}}, {{actor}} assigned you the manager task \"{{task}}\" on \"{{title}}\" {{reference}}.",
        "cta": "Review the assessment",
    },
    "ASSIGNED_FCRM_ANALYST": {
        "subject": "Assigned to you (FCRM analyst): {{title}} {{reference}}",
        "heading": "An analysis task has been assigned to you",
        "intro": "Hello {{recipient_name}}, {{actor}} assigned you the analyst task \"{{task}}\" on \"{{title}}\" {{reference}}.",
        "cta": "Start the task",
    },
    "ASSIGNED_COMMITTEE": {
        "subject": "Assigned to you (committee): {{title}} {{reference}}",
        "heading": "A committee task has been assigned to you",
        "intro": "Hello {{recipient_name}}, {{actor}} assigned you the committee task \"{{task}}\" on \"{{title}}\" {{reference}}.",
        "cta": "Open the assessment",
    },
    "ASSIGNED_OWNER": {
        "subject": "Assigned to you: {{title}} {{reference}}",
        "heading": "A task has been assigned to you",
        "intro": "Hello {{recipient_name}}, {{actor}} assigned you the task \"{{task}}\" on \"{{title}}\" {{reference}}.",
        "cta": "Open the assessment",
    },
}


def _templates() -> dict[str, dict[str, str]]:
    """Built-in templates, with any wording overridden from EMAIL_TEMPLATES_FILE."""
    path = os.getenv("EMAIL_TEMPLATES_FILE")
    if not path:
        return TEMPLATES
    try:
        with open(path, encoding="utf-8") as handle:
            overrides = json.load(handle)
        merged = {key: dict(value) for key, value in TEMPLATES.items()}
        for key, value in overrides.items():
            if key in merged and isinstance(value, dict):
                merged[key].update({k: str(v) for k, v in value.items() if k in merged[key]})
        return merged
    except Exception as exc:  # a bad override file falls back to the built-ins
        logger.warning("EMAIL_TEMPLATES_FILE not used: %s", type(exc).__name__)
        return TEMPLATES


_PLACEHOLDER = re.compile(r"\{\{(\w+)\}\}")


def _fill(text: str, context: dict[str, str], escape: bool = False) -> str:
    def value(match: re.Match) -> str:
        found = context.get(match.group(1), "")
        return html.escape(found) if escape else found

    return re.sub(r"\s+", " ", _PLACEHOLDER.sub(value, text)).strip()


def render(template_key: str, context: dict[str, str]) -> tuple[str, str, str]:
    """(subject, plain-text body, HTML body) for a template and its context."""
    template = _templates()[template_key]
    context = {key: "" for key in CONTEXT_KEYS} | context
    subject = _fill(template["subject"], context)
    heading = _fill(template["heading"], context)
    intro = _fill(template["intro"], context)
    cta = _fill(template["cta"], context)

    details = [
        ("Assessment", f"{context['title']} {context['reference']}".strip()),
        ("Current status", context["status"]),
        ("Risk level", context["risk_level"]),
        ("Due", context["due"]),
        ("Comment", context["comment"]),
        ("Conditions", context["conditions"]),
    ]
    details = [(label, value) for label, value in details if value]

    text = "\n".join(
        [heading, "", intro, "", *[f"{label}: {value}" for label, value in details], "",
         f"{cta}: {context['app_url']}", "",
         "This is an automated message from the Risk Assessment Workbench; please do not reply."]
    )

    rows = "".join(
        f'<tr><td style="padding:6px 12px 6px 0;color:#667085;white-space:nowrap;vertical-align:top">{html.escape(label)}</td>'
        f'<td style="padding:6px 0;color:#1e293b">{html.escape(value)}</td></tr>'
        for label, value in details
    )
    page = (
        '<div style="background:#f3f4f6;padding:24px;font-family:Segoe UI,Arial,sans-serif">'
        '<div style="max-width:560px;margin:0 auto;background:#ffffff;border:1px solid #e1e7ed;border-radius:10px;overflow:hidden">'
        '<div style="background:#172033;color:#ffffff;padding:14px 24px;font-size:13px;letter-spacing:.04em">'
        "RISK ASSESSMENT WORKBENCH</div>"
        '<div style="padding:24px">'
        f'<h2 style="margin:0 0 12px;font-size:18px;color:#1e293b">{html.escape(heading)}</h2>'
        f'<p style="margin:0 0 16px;font-size:14px;line-height:1.55;color:#334155">{html.escape(intro)}</p>'
        f'<table style="border-collapse:collapse;font-size:13px;margin-bottom:20px">{rows}</table>'
        f'<a href="{html.escape(context["app_url"], quote=True)}" style="display:inline-block;background:#2563eb;color:#ffffff;'
        f'text-decoration:none;padding:10px 18px;border-radius:8px;font-size:14px">{html.escape(cta)}</a>'
        "</div>"
        '<div style="padding:14px 24px;border-top:1px solid #edf1f5;color:#98a2b3;font-size:12px">'
        "Automated message &mdash; please do not reply.</div>"
        "</div></div>"
    )
    return subject, text, page


# ---------------------------------------------------------------------------
# Who gets what
# ---------------------------------------------------------------------------


def _context(assessment, recipient, actor, comment: str = "", task: str = "", role_label: str = "") -> dict[str, str]:
    from app.services import workflow

    due = assessment.status_due_at
    return {
        "recipient_name": (recipient.full_name or recipient.email.split("@")[0]) if recipient else "",
        "title": assessment.title or "",
        "reference": f"({assessment.reference_id})" if assessment.reference_id else "",
        "status": workflow.label_for(assessment.workflow_status),
        "risk_level": (assessment.residual_risk_level or assessment.risk_level or "").title(),
        "actor": (actor.full_name or actor.email) if actor else "The system",
        "comment": (comment or "").strip(),
        "conditions": (assessment.committee_conditions or "").strip(),
        "task": task,
        "due": due.strftime("%d %b %Y") if due else "",
        "role_label": role_label,
        "app_url": os.getenv("APP_BASE_URL", "http://localhost:5173").rstrip("/"),
    }


def _users_with_role(db: Session, *roles: str):
    from app.models.user import User

    return db.query(User).filter(User.role.in_(roles), User.is_active.is_(True)).all()


def _user(db: Session, user_id: int | None):
    from app.models.user import User

    return db.query(User).filter(User.id == user_id, User.is_active.is_(True)).first() if user_id else None


_MANAGER_DECISIONS = {
    "READY_FOR_COMMITTEE": "APPROVE",
    "MANAGER_REJECTED": "REJECT",
    "RETURNED_BY_MANAGER": "RETURN",
}


def _plan(db: Session, assessment, action: str, reason: str, actor):
    """[(template key, recipients, extra context)] for a recorded transition."""
    from app.models.user import UserRole

    owner = _user(db, assessment.owner_id)
    status = assessment.status
    plan: list[tuple[str, list, dict]] = []

    if action in {"CREATED", "SUBMIT"} and not assessment.is_draft:
        plan.append(("CREATED_OWNER", [owner], {}))
        plan.append(("CREATED_ANALYSTS", _users_with_role(db, UserRole.FCRM_ANALYST.value), {}))
    elif action == "SUBMIT_TO_MANAGER":
        plan.append(("SUBMITTED_TO_MANAGER_MANAGER", [_user(db, assessment.manager_id)], {}))
        plan.append(("SUBMITTED_TO_MANAGER_OWNER", [owner], {}))
    elif action == "MANAGER_DECISION":
        decision = _MANAGER_DECISIONS.get(status)
        if decision:
            plan.append((f"MANAGER_DECIDED_{decision}", [owner], {"comment": reason}))
        if decision == "APPROVE":
            plan.append(("READY_FOR_COMMITTEE_COMMITTEE", _users_with_role(db, UserRole.COMMITTEE_MEMBER.value), {}))
    elif action == "OPEN_COMMITTEE_REVIEW":
        plan.append(("COMMITTEE_REVIEW_COMMITTEE", _users_with_role(db, UserRole.COMMITTEE_MEMBER.value), {}))
    elif action == "COMMITTEE_DECISION" and f"COMMITTEE_DECIDED_{status}" in TEMPLATES:
        plan.append((f"COMMITTEE_DECIDED_{status}", [owner, _user(db, assessment.manager_id)], {"comment": reason}))
    return plan


def _enqueue(db: Session, template_key: str, assessment, recipients, actor, extra: dict | None = None,
             include_actor: bool = False) -> None:
    pending = db.info.setdefault("pending_emails", [])
    seen: set[int] = set()
    for recipient in recipients:
        if recipient is None or recipient.id in seen or not recipient.email:
            continue
        if not include_actor and actor is not None and recipient.id == actor.id:
            continue
        seen.add(recipient.id)
        context = _context(assessment, recipient, actor, **(extra or {}))
        pending.append((recipient.email, template_key, context))


def queue_for_transition(db: Session, assessment, *, action: str, reason: str, user) -> None:
    """Note a just-recorded status change. Cheap; the emails are worked out
    at commit time, once the endpoint has finished setting the decision,
    comment and conditions. Never raises."""
    if email_enabled():
        db.info.setdefault("pending_events", []).append(("transition", assessment, action, reason, user))


def queue_assignment(db: Session, assessment, assignee, actor) -> None:
    """Note an 'assigned to you' email, worded for the role it is assigned to."""
    if email_enabled() and assignee is not None:
        db.info.setdefault("pending_events", []).append(("assign", assessment, assignee, actor))


def _build(db: Session, events) -> None:
    for event_ in events:
        try:
            if event_[0] == "transition":
                _, assessment, action, reason, user = event_
                for key, recipients, extra in _plan(db, assessment, action, reason, user):
                    _enqueue(
                        db, key, assessment, recipients, user, extra,
                        include_actor=key == "CREATED_OWNER",  # confirm the submission to whoever made it
                    )
            else:
                from app.services import workflow

                _, assessment, assignee, actor = event_
                party = workflow.responsibility_for(assessment).party or "OWNER"
                key = f"ASSIGNED_{party}" if f"ASSIGNED_{party}" in TEMPLATES else "ASSIGNED_OWNER"
                _enqueue(
                    db, key, assessment, [assignee], actor,
                    {"task": workflow.label_for(assessment.workflow_status), "role_label": _ROLE_LABELS.get(party, "")},
                )
        except Exception as exc:  # never let notifications break a commit
            logger.warning("Could not prepare notification emails: %s", type(exc).__name__)


# ---------------------------------------------------------------------------
# Sending, only once the transaction has committed
# ---------------------------------------------------------------------------


def _deliver(messages: list[tuple[str, str, dict[str, Any]]]) -> None:
    for to, key, context in messages:
        try:
            subject, text, page = render(key, context)
            send_email(to, subject, text, page)
        except Exception as exc:
            logger.warning("Notification %s to %s failed: %s", key, to, type(exc).__name__)


@event.listens_for(Session, "before_commit")
def _prepare_before_commit(session: Session) -> None:
    events = session.info.pop("pending_events", None)
    if events:
        _build(session, events)


@event.listens_for(Session, "after_commit")
def _send_after_commit(session: Session) -> None:
    messages = session.info.pop("pending_emails", None)
    if messages:
        threading.Thread(target=_deliver, args=(messages,), daemon=True).start()


@event.listens_for(Session, "after_rollback")
def _drop_on_rollback(session: Session) -> None:
    session.info.pop("pending_emails", None)
    session.info.pop("pending_events", None)



def send_password_reset_email(user, reset_url: str, minutes: int) -> None:
    """Email a password-reset link (in the background; never raises).
    Not tied to a database commit -- nothing is written when it is requested."""

    if not email_enabled():
        logger.warning(
            "Password reset email for %s not sent: email is switched off or incomplete "
            "(needs NOTIFY_EMAIL_ENABLED=true and SMTP_HOST, SMTP_USER, SMTP_PASSWORD).",
            user.email,
        )
        return
    if not user.email:
        return
    context = {
        "recipient_name": user.full_name or user.email.split("@")[0],
        "app_url": reset_url,
        "minutes": str(minutes),
    }
    threading.Thread(target=_deliver, args=([(user.email, "PASSWORD_RESET", context)],), daemon=True).start()

"""
Read-only tools the assistant can call.

Design rules (see assistant/__init__.py):

  * Every tool takes the signed-in user and returns plain JSON-able data.
    Nothing here writes to the database.
  * Visibility is never decided by the model. A tool that names an
    assessment re-checks it against the caller's role, scope and
    delegations (`_visible_assessment`); an id outside the caller's view
    behaves exactly like one that does not exist.
  * Free text written by people or extracted from uploaded documents is
    untrusted. It is truncated and returned inside a field named
    `*_untrusted`, and the system prompt tells the model to treat such
    fields as data, never as instructions.
  * Document contents are never returned -- only the structured fields
    the application itself recorded.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from typing import Any, Callable

from sqlalchemy.orm import Session

from app.models.action_item import OPEN_STATUSES, ActionItem
from app.models.assessment import Assessment
from app.models.audit_trail import AssessmentRetention
from app.models.risk_factor import RiskFactor
from app.models.user import User
from app.services import workflow

MAX_RESULTS = 10
MAX_DEADLINES = 25
MAX_TEXT = 500


class ToolError(Exception):
    """A problem the model should be told about (bad argument, not found)."""


def _clip(value: Any, limit: int = MAX_TEXT) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _iso(value: date | datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


def _visible_query(db: Session, user: User):
    """Assessments this user may see, soft-deleted ones excluded."""

    # Lazy import: app.api.assessments imports half the application.
    from app.api.assessments import _scope_assessments_for_user

    query = _scope_assessments_for_user(db.query(Assessment), user)
    deleted = db.query(AssessmentRetention.assessment_id).filter(AssessmentRetention.is_deleted.is_(True))
    return query.filter(Assessment.id.notin_(deleted))


def _visible_assessment(db: Session, user: User, assessment_id: Any) -> Assessment:
    try:
        wanted = int(assessment_id)
    except (TypeError, ValueError):
        raise ToolError("assessment_id must be a whole number") from None
    assessment = _visible_query(db, user).filter(Assessment.id == wanted).first()
    if assessment is None:
        # Same answer whether it does not exist or is not visible.
        raise ToolError(f"No assessment with id {wanted} is available to you.")
    return assessment


def _user_name(db: Session, user_id: int | None) -> str | None:
    if not user_id:
        return None
    person = db.get(User, user_id)
    return (person.full_name or person.email) if person else None


def _brief(assessment: Assessment) -> dict[str, Any]:
    return {
        "id": assessment.id,
        "reference_id": assessment.reference_id,
        "title": _clip(assessment.title, 160),
        "status": workflow.label_for(assessment.workflow_status),
        "priority": assessment.priority,
        "inherent_risk_level": assessment.inherent_risk_level,
        "residual_risk_level": assessment.residual_risk_level,
        "overall_risk_level": assessment.risk_level,
        "sla_state": workflow.compute_sla_state(assessment),
        "target_date": _iso(assessment.target_date),
    }


# ---------------------------------------------------------------------------
# find_assessments
# ---------------------------------------------------------------------------

def find_assessments(db: Session, user: User, args: dict[str, Any]) -> dict[str, Any]:
    query = _visible_query(db, user)

    search = _clip(args.get("search"), 100)
    if search:
        pattern = f"%{search}%"
        query = query.filter(
            Assessment.title.ilike(pattern)
            | Assessment.reference_id.ilike(pattern)
            | Assessment.product_or_service_name.ilike(pattern)
        )

    status = _clip(args.get("status"), 50)
    if status:
        query = query.filter(Assessment.workflow_status == status.upper())

    only_mine = bool(args.get("only_mine"))
    if only_mine:
        query = query.filter(
            (Assessment.owner_id == user.id)
            | (Assessment.manager_id == user.id)
            | (Assessment.current_assignee_id == user.id)
        )

    try:
        limit = max(1, min(int(args.get("limit") or MAX_RESULTS), MAX_RESULTS))
    except (TypeError, ValueError):
        limit = MAX_RESULTS

    total = query.count()
    rows = query.order_by(Assessment.created_at.desc()).limit(limit).all()
    return {"total_matching": total, "returned": len(rows), "assessments": [_brief(a) for a in rows]}


# ---------------------------------------------------------------------------
# get_assessment_summary
# ---------------------------------------------------------------------------

def get_assessment_summary(db: Session, user: User, args: dict[str, Any]) -> dict[str, Any]:
    assessment = _visible_assessment(db, user, args.get("assessment_id"))
    responsibility = workflow.responsibility_for(assessment)

    open_actions = (
        db.query(ActionItem)
        .filter(ActionItem.assessment_id == assessment.id, ActionItem.status.in_(OPEN_STATUSES))
        .all()
    )
    today = date.today()
    overdue_actions = [a for a in open_actions if a.due_date and a.due_date < today]

    summary = _brief(assessment)
    summary.update(
        {
            "change_type": assessment.change_type,
            "product_or_service": _clip(assessment.product_or_service_name, 160),
            "legal_entity": assessment.legal_entity,
            "business_unit": assessment.business_unit,
            "countries_jurisdictions": _clip(assessment.countries_jurisdictions, 300),
            "expected_launch_date": assessment.expected_launch_date,
            "owner": _user_name(db, assessment.owner_id),
            "manager": _user_name(db, assessment.manager_id),
            "current_assignee": _user_name(db, assessment.current_assignee_id),
            "waiting_on": responsibility.party,
            "next_action": responsibility.next_action,
            "pipeline_stage": assessment.status,
            "stage_due_at": _iso(assessment.status_due_at),
            "next_review_date": _iso(assessment.next_review_date),
            "inherent_score": assessment.inherent_score,
            "residual_score": assessment.residual_score,
            "committee_decision": assessment.committee_decision,
            "manager_decision": assessment.manager_decision,
            "information_requested": bool(assessment.information_request_note)
            and assessment.information_responded_at is None,
            "open_action_items": len(open_actions),
            "overdue_action_items": len(overdue_actions),
            "description_untrusted": _clip(assessment.description),
        }
    )
    return summary


# ---------------------------------------------------------------------------
# get_deadlines
# ---------------------------------------------------------------------------

def get_deadlines(db: Session, user: User, args: dict[str, Any]) -> dict[str, Any]:
    today = date.today()
    try:
        within_days = max(1, min(int(args.get("within_days") or 30), 365))
    except (TypeError, ValueError):
        within_days = 30
    horizon = today + timedelta(days=within_days)

    query = _visible_query(db, user).filter(
        Assessment.workflow_status.notin_(list(workflow.TERMINAL_WORKFLOW_STATUSES))
    )
    if args.get("assessment_id") not in (None, ""):
        query = query.filter(Assessment.id == _visible_assessment(db, user, args["assessment_id"]).id)
    elif args.get("only_mine"):
        query = query.filter(
            (Assessment.owner_id == user.id)
            | (Assessment.manager_id == user.id)
            | (Assessment.current_assignee_id == user.id)
        )
    assessments = query.all()
    by_id = {a.id: a for a in assessments}

    items: list[dict[str, Any]] = []

    def add(kind: str, due: date | None, assessment: Assessment, label: str, **extra: Any) -> None:
        if due is None or due > horizon:
            return
        items.append(
            {
                "kind": kind,
                "due": due.isoformat(),
                "days_from_today": (due - today).days,
                "overdue": due < today,
                "assessment_id": assessment.id,
                "reference_id": assessment.reference_id,
                "assessment_title": _clip(assessment.title, 120),
                "what": label,
                **extra,
            }
        )

    for assessment in assessments:
        add("assessment_target_date", assessment.target_date, assessment, "Overall target completion date")
        stage_due = workflow.as_utc(assessment.status_due_at)
        add(
            "stage_sla",
            stage_due.date() if stage_due else None,
            assessment,
            f"Time allowed in the current stage ({workflow.label_for(assessment.workflow_status)})",
            sla_state=workflow.compute_sla_state(assessment),
        )
        add("periodic_review", assessment.next_review_date, assessment, "Next review is due")

    if by_id:
        for item in (
            db.query(ActionItem)
            .filter(
                ActionItem.assessment_id.in_(list(by_id)),
                ActionItem.status.in_(OPEN_STATUSES),
                ActionItem.due_date.isnot(None),
            )
            .all()
        ):
            add(
                "action_item",
                item.due_date,
                by_id[item.assessment_id],
                _clip(item.title, 160) or "Action item",
                priority=item.priority,
                action_owner=_clip(item.owner, 80),
                action_status=item.status,
                escalated=bool(item.escalated),
            )

    # Overdue first (most overdue at the top), then by date.
    items.sort(key=lambda row: (not row["overdue"], row["due"]))
    return {
        "today": today.isoformat(),
        "window_days": within_days,
        "total": len(items),
        "overdue": sum(1 for row in items if row["overdue"]),
        "deadlines": items[:MAX_DEADLINES],
        "truncated": len(items) > MAX_DEADLINES,
    }


# ---------------------------------------------------------------------------
# get_risk_analysis
# ---------------------------------------------------------------------------

def get_risk_analysis(db: Session, user: User, args: dict[str, Any]) -> dict[str, Any]:
    assessment = _visible_assessment(db, user, args.get("assessment_id"))

    from app.services.explainability import explain_assessment

    explained = explain_assessment(db, assessment.id) or {"sections": [], "overrides": [], "challenge_findings": []}
    sections = [
        {
            "rating": section.get("rating"),
            "value": section.get("value"),
            "level": section.get("level"),
            "how_it_was_determined": _clip(section.get("explanation"), 700),
        }
        for section in explained.get("sections", [])
    ]

    factors = (
        db.query(RiskFactor)
        .filter(
            RiskFactor.assessment_id == assessment.id,
            RiskFactor.is_current.is_(True),
            RiskFactor.applicable.is_(True),
            RiskFactor.excluded.is_(False),
        )
        .order_by(RiskFactor.score.desc())
        .limit(8)
        .all()
    )
    top_factors = [
        {
            "category": factor.category,
            "severity": factor.severity,
            "score": factor.score,
            "likelihood": factor.likelihood,
            "impact": factor.impact,
            "rated": factor.likelihood is not None and factor.impact is not None,
            "indicators": factor.get_indicators()[:6],
            "evidence_status": factor.evidence_status,
            "missing_information": [_clip(m, 160) for m in factor.get_missing_information()[:4]],
            "rationale_untrusted": _clip(factor.rationale, 400),
        }
        for factor in factors
    ]

    findings = explained.get("challenge_findings", [])
    return {
        **_brief(assessment),
        "inherent_score": assessment.inherent_score,
        "residual_score": assessment.residual_score,
        "ratings": sections,
        "top_risk_factors": top_factors,
        "analyst_overrides": len(explained.get("overrides", [])),
        "challenge_findings": {
            "total": len(findings),
            "unresolved": sum(1 for f in findings if f.get("resolution_status") not in ("RESOLVED", "ACCEPTED")),
            "highest_severity": next(
                (sev for sev in ("CRITICAL", "HIGH", "MEDIUM", "LOW") if any(f.get("severity") == sev for f in findings)),
                None,
            ),
        },
        "note": (
            "Scores and levels are produced by the application's own rules; "
            "do not recompute or adjust them. Unrated factors mean the risk "
            "is still provisional."
        ),
    }


# ---------------------------------------------------------------------------
# Registry (OpenAI tool-calling format)
# ---------------------------------------------------------------------------

_ID = {"type": "integer", "description": "The assessment's numeric id (from find_assessments or the page the user is on)."}

TOOL_SPECS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "find_assessments",
            "description": "Find assessments the user can see, by words in the title/reference/product, by status, or only those the user owns/manages. Use this to resolve a reference or name to an id.",
            "parameters": {
                "type": "object",
                "properties": {
                    "search": {"type": "string", "description": "Words from the title, reference id or product name."},
                    "status": {"type": "string", "description": "Workflow status, e.g. DRAFT, SUBMITTED, COMMITTEE_REVIEW, CLOSED."},
                    "only_mine": {"type": "boolean", "description": "Only assessments the user owns, manages or is assigned."},
                    "limit": {"type": "integer", "description": "Max results (up to 10)."},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_assessment_summary",
            "description": "Summary of one assessment: status, who it is waiting on and the next action, owner, risk levels, dates, open actions.",
            "parameters": {"type": "object", "properties": {"assessment_id": _ID}, "required": ["assessment_id"]},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_deadlines",
            "description": "Upcoming and overdue deadlines (target dates, stage time limits, reviews, action items). Give assessment_id for one assessment, otherwise it covers everything the user can see; only_mine narrows to the user's own.",
            "parameters": {
                "type": "object",
                "properties": {
                    "assessment_id": _ID,
                    "only_mine": {"type": "boolean"},
                    "within_days": {"type": "integer", "description": "Look-ahead window in days (default 30, max 365). Overdue items are always included."},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_risk_analysis",
            "description": "How the risk was rated for one assessment: inherent/residual ratings and how each was determined, the top risk factors with evidence status and gaps, overrides and challenge findings.",
            "parameters": {"type": "object", "properties": {"assessment_id": _ID}, "required": ["assessment_id"]},
        },
    },
]

TOOLS: dict[str, Callable[[Session, User, dict[str, Any]], dict[str, Any]]] = {
    "find_assessments": find_assessments,
    "get_assessment_summary": get_assessment_summary,
    "get_deadlines": get_deadlines,
    "get_risk_analysis": get_risk_analysis,
}

"""
P6 (Stage 18, R18.1-R18.4): what happens to the APPROVED parent while it
is reassessed, and when the reassessment ends.

The parent's pipeline status never changes. Its approval stays the record
of what was decided. A separate flag, `reassessment_state`, says where it
stands:

    NULL                -- in force, no reassessment open
    UNDER_REASSESSMENT  -- a reassessment (child) is open
    SUPERSEDED          -- a reassessment was approved; the child's approval
                           replaces it (terminal; superseded_by_id is set)

    open reassessment              -> UNDER_REASSESSMENT; the parent's open
                                      triggers are linked to the child
    child APPROVED / ..._CONDITIONS -> parent SUPERSEDED; its remaining
                                      open triggers are closed against the child
    child REJECTED / MANAGER_REJECTED, or CLOSED without an approval
                                   -> parent back in force (NULL)

A superseded parent raises no more review-date triggers and can't be
reassessed again (reassess its successor). Every step is audited with a
dedicated action. Called from app/services/workflow.py transition(), which
every status change goes through, so no decision path can skip it.
Functions don't commit.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.models.assessment import Assessment
from app.models.reassessment_trigger import ReassessmentTrigger

UNDER_REASSESSMENT = "UNDER_REASSESSMENT"
SUPERSEDED = "SUPERSEDED"

APPROVED_STATUSES = {"APPROVED", "APPROVED_WITH_CONDITIONS"}
REJECTED_STATUSES = {"REJECTED", "MANAGER_REJECTED"}
# R18.4: only a decided approval can be reassessed.
REASSESSABLE_STATUSES = {"APPROVED", "APPROVED_WITH_CONDITIONS", "CLOSED"}
OPEN_TRIGGER_STATUSES = {"OPEN", "ACKNOWLEDGED"}
PIPELINE_ROLES = {"FCRM_ANALYST", "MANAGER", "ADMIN"}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _audit(db: Session, assessment_id: int, action: str, user, details: str,
           previous_status: str | None = None, new_status: str | None = None) -> None:
    from app.services.audit_service import AuditAction, log_audit_event

    log_audit_event(
        db,
        assessment_id=assessment_id,
        action=getattr(AuditAction, action),
        previous_status=previous_status,
        new_status=new_status,
        actor=(user.full_name or user.email) if user is not None else "System",
        actor_id=user.id if user is not None else None,
        details=details,
    )


def in_progress_child(db: Session, parent_id: int) -> Assessment | None:
    """The reassessment of `parent_id` that hasn't reached a final decision."""

    from app.services.decision_lock import FINAL_DECISION_STATUSES

    return (
        db.query(Assessment)
        .filter(Assessment.parent_assessment_id == parent_id, Assessment.status.notin_(FINAL_DECISION_STATUSES))
        .order_by(Assessment.id.desc())
        .first()
    )


def link_open_triggers(db: Session, parent: Assessment, child: Assessment, user, why: str) -> list[ReassessmentTrigger]:
    """Close the parent's open / acknowledged triggers against `child`."""

    rows = (
        db.query(ReassessmentTrigger)
        .filter(ReassessmentTrigger.assessment_id == parent.id, ReassessmentTrigger.status.in_(OPEN_TRIGGER_STATUSES))
        .all()
    )
    for trigger in rows:
        trigger.status = "REASSESSMENT_CREATED"
        trigger.reassessment_id = child.id
        trigger.resolved_by = (user.full_name or user.email) if user is not None else "System"
        trigger.resolved_by_id = user.id if user is not None else None
        trigger.resolved_at = _now()
        trigger.resolution_note = why
    return rows


def mark_under_reassessment(db: Session, parent: Assessment, child: Assessment, user) -> list[ReassessmentTrigger]:
    previous = parent.reassessment_state
    parent.reassessment_state = UNDER_REASSESSMENT
    linked = link_open_triggers(db, parent, child, user, f"Addressed by reassessment #{child.id}.")
    _audit(
        db, parent.id, "REASSESSMENT_OPENED", user,
        f"Reassessment opened as Assessment #{child.id}; this approval stays in force until it is decided. "
        f"Open triggers linked: {', '.join(f'#{t.id} {t.trigger_type}' for t in linked) or 'none'}.",
        previous_status=previous or "IN_FORCE", new_status=UNDER_REASSESSMENT,
    )
    return linked


def on_status_changed(db: Session, assessment: Assessment, from_status: str | None, to_status: str, user) -> None:
    """Hook for every status change (app/services/workflow.py)."""

    if not assessment.parent_assessment_id or from_status == to_status:
        return
    parent = db.get(Assessment, assessment.parent_assessment_id)
    if parent is None or parent.reassessment_state == SUPERSEDED:
        return

    if to_status in APPROVED_STATUSES:
        supersede(db, parent, assessment, user)
    elif to_status in REJECTED_STATUSES or (to_status == "CLOSED" and from_status not in APPROVED_STATUSES):
        release(db, parent, assessment, user, to_status)


def supersede(db: Session, parent: Assessment, child: Assessment, user) -> None:
    previous = parent.reassessment_state
    parent.reassessment_state = SUPERSEDED
    parent.superseded_by_id = child.id
    parent.superseded_at = _now()
    closed = link_open_triggers(db, parent, child, user, f"Superseded by the approval of reassessment #{child.id}.")
    detail = (
        f"Superseded by reassessment #{child.id} ({child.status}). The earlier approval is kept on record but no longer "
        f"in force; review dates now follow #{child.id}. Triggers closed: {len(closed)}."
    )
    _audit(db, parent.id, "REASSESSMENT_PARENT_SUPERSEDED", user, detail, previous or "IN_FORCE", SUPERSEDED)
    _audit(db, child.id, "REASSESSMENT_PARENT_SUPERSEDED", user, f"This approval supersedes Assessment #{parent.id}.",
           None, child.status)


def release(db: Session, parent: Assessment, child: Assessment, user, outcome: str) -> None:
    if parent.reassessment_state != UNDER_REASSESSMENT:
        return
    other = in_progress_child(db, parent.id)
    if other is not None and other.id != child.id:
        return
    parent.reassessment_state = None
    _audit(
        db, parent.id, "REASSESSMENT_ENDED_WITHOUT_APPROVAL", user,
        f"Reassessment #{child.id} ended {outcome} without an approval; this assessment's approval remains in force.",
        UNDER_REASSESSMENT, "IN_FORCE",
    )


# -- who may do what (shown by the UI, enforced by the API) -----------------------


def propose_problem(db: Session, user, assessment: Assessment) -> str | None:
    if user.role not in PIPELINE_ROLES and user.id != assessment.owner_id:
        return "Only the assessment's owner or an FCRM Analyst, Manager or Admin can propose a change."
    if assessment.reassessment_state == SUPERSEDED:
        return f"Superseded by reassessment #{assessment.superseded_by_id}; propose changes on that assessment instead."
    if assessment.status not in REASSESSABLE_STATUSES:
        return f"Only an approved or closed assessment can be reassessed (this one is {assessment.status})."
    child = in_progress_child(db, assessment.id)
    if child is not None:
        return f"Reassessment #{child.id} is already in progress; propose further changes on it."
    return None


def flag_problem(user, assessment: Assessment) -> str | None:
    if user.role not in PIPELINE_ROLES and user.id != assessment.owner_id:
        return "Only the assessment's owner or an FCRM Analyst, Manager or Admin can flag a trigger."
    if assessment.reassessment_state == SUPERSEDED:
        return f"Superseded by reassessment #{assessment.superseded_by_id}; flag triggers on that assessment instead."
    return None


def resolve_problem(user) -> str | None:
    if user.role not in PIPELINE_ROLES:
        return "Only an FCRM Analyst, Manager or Admin can resolve reassessment triggers."
    return None


def actions_for(db: Session, user, assessment: Assessment) -> dict:
    def entry(problem):
        return {"allowed": problem is None, "reason": problem}

    return {
        "propose": entry(propose_problem(db, user, assessment)),
        "flag": entry(flag_problem(user, assessment)),
        "resolve": entry(resolve_problem(user)),
    }


def status_for(db: Session, user, assessment: Assessment) -> dict:
    child = in_progress_child(db, assessment.id)
    children = (
        db.query(Assessment.id, Assessment.status, Assessment.reference_id)
        .filter(Assessment.parent_assessment_id == assessment.id)
        .order_by(Assessment.id.asc())
        .all()
    )
    return {
        "assessment_id": assessment.id,
        "status": assessment.status,
        "reassessment_state": assessment.reassessment_state or "IN_FORCE",
        "superseded_by_id": assessment.superseded_by_id,
        "superseded_at": assessment.superseded_at.isoformat() if assessment.superseded_at else None,
        "parent_assessment_id": assessment.parent_assessment_id,
        "in_progress_reassessment_id": child.id if child else None,
        "reassessments": [{"id": c.id, "status": c.status, "reference_id": c.reference_id} for c in children],
        "next_review_date": assessment.next_review_date.isoformat() if assessment.next_review_date else None,
        "actions": actions_for(db, user, assessment),
    }


# -- R18.3: reused fields with source and age --------------------------------------

FIELD_LABELS = {
    "title": "Title", "change_type": "Change type", "description": "Description", "evidence": "Evidence summary",
    "product_or_service_name": "Product or service", "business_owner": "Business owner", "legal_entity": "Legal entity",
    "business_unit": "Business unit", "customer_segment": "Customer segment", "countries_jurisdictions": "Countries / jurisdictions",
    "delivery_channels": "Delivery channels", "expected_transaction_volume": "Expected transaction volume",
    "expected_transaction_value": "Expected transaction value", "transaction_types": "Transaction types",
    "third_party_vendor_usage": "Third-party vendors", "technology_process_changes": "Technology / process changes",
    "expected_launch_date": "Expected launch date",
}


def reused_fields(assessment: Assessment, now: datetime | None = None) -> list[dict]:
    now = now or _now()
    try:
        sources = json.loads(assessment.reused_field_sources or "{}")
    except ValueError:
        sources = {}
    out = []
    for field, source in sorted(sources.items()):
        when = source.get("source_date")
        age = None
        if when:
            try:
                moment = datetime.fromisoformat(when)
                if moment.tzinfo is None:
                    moment = moment.replace(tzinfo=timezone.utc)
                age = (now - moment).days
            except ValueError:
                age = None
        out.append({
            "field": field,
            "label": FIELD_LABELS.get(field, field.replace("_", " ").capitalize()),
            "value": getattr(assessment, field, None),
            "source_assessment_id": source.get("source_assessment_id"),
            "source_date": when,
            "age_days": age,
        })
    return out


# -- R18.2: structured comparison ----------------------------------------------------


def _factor_rows(db: Session, assessment_id: int) -> dict:
    from app.models.risk_factor import RiskFactor

    rows = (
        db.query(RiskFactor)
        .filter(RiskFactor.assessment_id == assessment_id, RiskFactor.is_current.is_(True))
        .all()
    )
    return {
        f.category: {
            "applicable": bool(f.applicable) and not bool(f.excluded),
            "likelihood": f.likelihood,
            "impact": f.impact,
            "score": f.score,
            "severity": f.severity,
        }
        for f in rows
    }


def _control_rows(db: Session, assessment_id: int) -> dict:
    from app.models.control import Control, ControlAssessment

    controls = (
        db.query(Control).filter(Control.assessment_id == assessment_id, Control.is_current.is_(True)).all()
    )
    out = {}
    for c in controls:
        latest = (
            db.query(ControlAssessment)
            .filter(ControlAssessment.control_id == c.id, ControlAssessment.is_current.is_(True))
            .order_by(ControlAssessment.id.desc())
            .first()
        )
        key = f"{c.control_type}: {(c.description or '').strip()[:80]}".rstrip(": ")
        out[key] = {
            "control_type": c.control_type,
            "operating_status": c.operating_status,
            "design_adequacy": latest.design_adequacy if latest else None,
            "operating_effectiveness": latest.operating_effectiveness if latest else None,
        }
    return out


def _condition_rows(db: Session, assessment_id: int) -> dict:
    from app.models.committee_condition import CommitteeCondition

    return {
        " ".join((c.description or "").lower().split()): {"description": c.description, "status": c.status, "due_date": str(c.due_date) if c.due_date else None}
        for c in db.query(CommitteeCondition).filter(CommitteeCondition.assessment_id == assessment_id).all()
    }


def _diff(old: dict, new: dict, fields: list[str]) -> list[dict]:
    out = []
    for key in sorted(set(old) | set(new)):
        before, after = old.get(key), new.get(key)
        if before is None:
            change = "ADDED"
        elif after is None:
            change = "REMOVED"
        else:
            changed = [f for f in fields if before.get(f) != after.get(f)]
            change = "CHANGED" if changed else "UNCHANGED"
        out.append({"key": key, "change": change, "before": before, "after": after,
                    "changed_fields": [f for f in fields if before and after and before.get(f) != after.get(f)]})
    return out


def structured_comparison(db: Session, parent: Assessment, child: Assessment) -> dict:
    def score(a: Assessment, attr: str):
        return getattr(a, attr, None)

    scores = []
    for label, attr, level in (("Inherent", "inherent_score", "inherent_risk_level"), ("Residual", "residual_score", "residual_risk_level")):
        before, after = score(parent, attr), score(child, attr)
        scores.append({
            "measure": label,
            "before": before, "after": after,
            "before_level": score(parent, level), "after_level": score(child, level),
            "delta": (after - before) if isinstance(before, (int, float)) and isinstance(after, (int, float)) else None,
        })
    factors = _diff(_factor_rows(db, parent.id), _factor_rows(db, child.id), ["applicable", "likelihood", "impact", "score", "severity"])
    controls = _diff(_control_rows(db, parent.id), _control_rows(db, child.id), ["operating_status", "design_adequacy", "operating_effectiveness"])
    conditions = _diff(_condition_rows(db, parent.id), _condition_rows(db, child.id), ["status", "due_date"])

    def counts(rows):
        tally = {"ADDED": 0, "REMOVED": 0, "CHANGED": 0, "UNCHANGED": 0}
        for r in rows:
            tally[r["change"]] += 1
        return tally

    return {
        "scores": scores,
        "factors": factors,
        "controls": controls,
        "conditions": conditions,
        "summary": {"factors": counts(factors), "controls": counts(controls), "conditions": counts(conditions)},
    }

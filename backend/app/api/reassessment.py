import json
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from app.auth.dependencies import get_current_user, log_denied_attempt
from app.database import get_db
from app.models.assessment import Assessment
from app.models.committee_condition import CommitteeCondition
from app.models.control import Control
from app.models.reassessment_trigger import ReassessmentTrigger
from app.models.risk_factor import RiskFactor
from app.models.user import User
from app.schemas.reassessment import (
    ManualTriggerCreate,
    ProposeChangeRequest,
    ProposeChangeResponse,
    ReassessmentComparisonResponse,
    ReassessmentTriggerResponse,
    TriggerResolution,
)
from app.services import reassessment_lifecycle as lifecycle
from app.services.audit_service import AuditAction, actor_name, log_audit_event
from app.services.reassessment_service import open_reassessment, record_date_triggers

router = APIRouter(prefix="/api/assessments", tags=["Reassessment"])

# Kept for importers; the rules live in app/services/reassessment_lifecycle.py.
PIPELINE_ROLES = lifecycle.PIPELINE_ROLES
REASSESSABLE_STATUSES = lifecycle.REASSESSABLE_STATUSES


def _refuse(db: Session, user: User, request: Request, status: int, detail: str, assessment_id: int) -> HTTPException:
    """R15.5: a refused (403) action is recorded."""

    if status == 403:
        log_denied_attempt(db, user, request, f"reassessment: {detail}", assessment_id=assessment_id)
    return HTTPException(status_code=status, detail=detail)


def _open_reassessment_of(db: Session, parent_id: int) -> Assessment | None:
    return lifecycle.in_progress_child(db, parent_id)


def _get_assessment_or_404(db: Session, assessment_id: int) -> Assessment:
    assessment = db.query(Assessment).filter(Assessment.id == assessment_id).first()
    if not assessment:
        raise HTTPException(status_code=404, detail="Assessment not found")
    return assessment


@router.get("/{assessment_id}/reassessment/status")
def get_reassessment_status(
    assessment_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """P6: where this approval stands (in force, under reassessment,
    superseded), its reassessments, and what the user may do -- the UI
    shows actions from this; the routes below enforce the same rules."""

    return lifecycle.status_for(db, current_user, _get_assessment_or_404(db, assessment_id))


@router.get(
    "/{assessment_id}/reassessment/check-triggers",
    response_model=list[ReassessmentTriggerResponse],
)
def check_reassessment_triggers(
    assessment_id: int,
    db: Session = Depends(get_db),
):
    """
    R18.1/AC2: on-demand evaluation of the date-based triggers (EXPIRY,
    PERIODIC_REVIEW). Persists a new OPEN trigger row the first time a
    given type is detected (none on a superseded approval; linked to the
    open reassessment while one is in progress).
    """

    assessment = _get_assessment_or_404(db, assessment_id)

    if record_date_triggers(db, assessment):
        db.commit()

    return (
        db.query(ReassessmentTrigger)
        .filter(
            ReassessmentTrigger.assessment_id == assessment_id,
            ReassessmentTrigger.status == "OPEN",
        )
        .order_by(ReassessmentTrigger.detected_at.desc())
        .all()
    )


@router.get(
    "/{assessment_id}/reassessment/triggers",
    response_model=list[ReassessmentTriggerResponse],
)
def list_reassessment_triggers(
    assessment_id: int,
    db: Session = Depends(get_db),
):
    _get_assessment_or_404(db, assessment_id)

    return (
        db.query(ReassessmentTrigger)
        .filter(ReassessmentTrigger.assessment_id == assessment_id)
        .order_by(ReassessmentTrigger.detected_at.desc())
        .all()
    )


@router.post(
    "/{assessment_id}/reassessment/flag-trigger",
    response_model=ReassessmentTriggerResponse,
    status_code=201,
)
def flag_reassessment_trigger(
    assessment_id: int,
    payload: ManualTriggerCreate,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    R18.1: REGULATORY_POLICY_CHANGE and SIGNIFICANT_CONTROL_FAILURE (or
    any other trigger type) flagged by hand -- there's no field on the
    assessment to diff for either of these.
    """

    assessment = _get_assessment_or_404(db, assessment_id)
    problem = lifecycle.flag_problem(current_user, assessment)
    if problem:
        raise _refuse(db, current_user, request, 409 if "Superseded" in problem else 403, problem, assessment_id)
    # Who flagged it is the signed-in user, not a name from the body.
    payload.flagged_by = actor_name(current_user)

    trigger = ReassessmentTrigger(
        assessment_id=assessment.id,
        trigger_type=payload.trigger_type,
        description=payload.description,
        detected_by=payload.flagged_by,
        detected_by_id=current_user.id,
        status="OPEN",
    )
    db.add(trigger)
    db.flush()

    log_audit_event(
        db=db,
        assessment_id=assessment_id,
        action=AuditAction.REASSESSMENT_TRIGGER_FLAGGED,
        actor=payload.flagged_by,
        actor_id=current_user.id,
        new_status="OPEN",
        details=f"Reassessment trigger #{trigger.id} flagged: {payload.trigger_type} — {payload.description}",
    )

    db.commit()
    db.refresh(trigger)

    return trigger


@router.patch(
    "/{assessment_id}/reassessment/triggers/{trigger_id}",
    response_model=ReassessmentTriggerResponse,
)
def resolve_reassessment_trigger(
    assessment_id: int,
    trigger_id: int,
    payload: TriggerResolution,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    # Settling a trigger decides whether an approval still stands, so it
    # is FCRM work -- not for the owner whose approval it is.
    problem = lifecycle.resolve_problem(current_user)
    if problem:
        raise _refuse(db, current_user, request, 403, problem, assessment_id)

    trigger = (
        db.query(ReassessmentTrigger)
        .filter(
            ReassessmentTrigger.id == trigger_id,
            ReassessmentTrigger.assessment_id == assessment_id,
        )
        .first()
    )

    if not trigger:
        raise HTTPException(status_code=404, detail="Reassessment trigger not found")

    if trigger.status not in lifecycle.OPEN_TRIGGER_STATUSES:
        raise HTTPException(status_code=409, detail=f"This trigger is already settled ({trigger.status}).")
    if payload.status == "OPEN" or payload.status == trigger.status:
        raise HTTPException(status_code=409, detail=f"The trigger is already {trigger.status}.")

    if payload.status == "DISMISSED" and not (payload.dismissed_reason or "").strip():
        raise HTTPException(
            status_code=422,
            detail="A reason is required to dismiss a reassessment trigger.",
        )

    # R18.4: "reassessment created" must be true, not just asserted.
    child = None
    if payload.status == "REASSESSMENT_CREATED":
        child = (
            _open_reassessment_of(db, assessment_id)
            or db.query(Assessment).filter(Assessment.parent_assessment_id == assessment_id).order_by(Assessment.id.desc()).first()
        )
        if child is None:
            raise HTTPException(
                status_code=409,
                detail=(
                    "No reassessment exists for this assessment yet. Propose the "
                    "change to open one, or dismiss the trigger with a reason."
                ),
            )

    previous = trigger.status
    trigger.status = payload.status
    trigger.dismissed_reason = payload.dismissed_reason
    trigger.resolution_note = (payload.resolution_note or "").strip() or None
    trigger.resolved_by = actor_name(current_user)
    trigger.resolved_by_id = current_user.id
    trigger.resolved_at = datetime.now(timezone.utc)
    if child is not None:
        trigger.reassessment_id = child.id

    note = payload.dismissed_reason or payload.resolution_note
    log_audit_event(
        db=db,
        assessment_id=assessment_id,
        action=AuditAction.REASSESSMENT_TRIGGER_RESOLVED,
        actor=trigger.resolved_by,
        actor_id=current_user.id,
        previous_status=previous,
        new_status=payload.status,
        details=(
            f"Reassessment trigger #{trigger_id} ({trigger.trigger_type}) -> {payload.status}"
            + (f" (reassessment #{child.id})" if child is not None else "")
            + (f": {note}" if note else ".")
        ),
    )

    db.commit()
    db.refresh(trigger)

    return trigger


@router.post(
    "/{assessment_id}/reassessment/propose-change",
    response_model=ProposeChangeResponse,
    status_code=201,
)
def propose_change(
    assessment_id: int,
    payload: ProposeChangeRequest,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    R18.1/R18.4/AC1, AC3: proposing a change to an approved assessment
    always opens a new reassessment (a fresh Assessment linked via
    parent_assessment_id) -- the previous approval is never silently
    kept valid. P6: the parent is marked UNDER_REASSESSMENT and its open
    triggers are linked to the reassessment.
    """

    parent = _get_assessment_or_404(db, assessment_id)
    problem = lifecycle.propose_problem(db, current_user, parent)
    if problem:
        raise _refuse(db, current_user, request, 403 if problem.startswith("Only the assessment's owner") else 409, problem, assessment_id)

    # open_reassessment records the proposer on the child and its
    # triggers; it is always the signed-in user.
    payload.proposed_by = actor_name(current_user)

    child, triggers = open_reassessment(db, parent, payload)
    for trigger in triggers:
        trigger.detected_by_id = current_user.id
        trigger.resolved_by_id = current_user.id
    lifecycle.mark_under_reassessment(db, parent, child, current_user)

    log_audit_event(
        db=db,
        assessment_id=child.id,
        action=AuditAction.CREATED,
        actor=payload.proposed_by,
        actor_id=current_user.id,
        details=(
            f"Created as a reassessment of Assessment #{parent.id}. Reason: {payload.reason}. "
            f"Triggers: {', '.join(t.trigger_type for t in triggers) or 'none detected'}."
        ),
    )

    db.commit()
    db.refresh(child)
    for trigger in triggers:
        db.refresh(trigger)

    return ProposeChangeResponse(reassessment_id=child.id, triggers=triggers)


def _assessment_snapshot(db: Session, assessment: Assessment) -> dict:
    factors = (
        db.query(RiskFactor)
        .filter(
            RiskFactor.assessment_id == assessment.id,
            RiskFactor.is_current.is_(True),
            RiskFactor.applicable.is_(True),
            RiskFactor.excluded.is_(False),
        )
        .all()
    )

    controls = (
        db.query(Control)
        .filter(Control.assessment_id == assessment.id, Control.is_current.is_(True))
        .all()
    )

    conditions = (
        db.query(CommitteeCondition)
        .filter(CommitteeCondition.assessment_id == assessment.id)
        .all()
    )

    return {
        "id": assessment.id,
        "title": assessment.title,
        "status": assessment.status,
        "reassessment_state": assessment.reassessment_state or "IN_FORCE",
        "inherent_score": assessment.inherent_score,
        "inherent_risk_level": assessment.inherent_risk_level,
        "residual_score": assessment.residual_score,
        "residual_risk_level": assessment.residual_risk_level,
        "committee_decision": assessment.committee_decision,
        "risk_categories": sorted(
            {f"{f.category} ({f.severity}, {f.score:g})" for f in factors if f.score is not None}
            | {f"{f.category} ({f.severity}, unrated)" for f in factors if f.score is None}
        ),
        "controls": sorted({c.control_type for c in controls}),
        "conditions": sorted({c.description for c in conditions}),
    }


@router.get(
    "/{assessment_id}/reassessment/compare",
    response_model=ReassessmentComparisonResponse,
)
def compare_reassessment(
    assessment_id: int,
    db: Session = Depends(get_db),
):
    """R18.2/AC4: old vs. new risks, controls, scores, and conditions --
    P6: structured by category, control and condition, with the reused
    intake fields' source and age (R18.3)."""

    reassessment = _get_assessment_or_404(db, assessment_id)

    if not reassessment.parent_assessment_id:
        raise HTTPException(
            status_code=400,
            detail="This assessment is not a reassessment of another assessment.",
        )

    parent = _get_assessment_or_404(db, reassessment.parent_assessment_id)

    fields_changed = []
    for field_name in (
        "product_or_service_name",
        "customer_segment",
        "countries_jurisdictions",
        "delivery_channels",
        "expected_transaction_volume",
        "third_party_vendor_usage",
        "technology_process_changes",
    ):
        old_value = getattr(parent, field_name, None)
        new_value = getattr(reassessment, field_name, None)
        if (old_value or "") != (new_value or ""):
            fields_changed.append(
                {"field": field_name, "old_value": old_value, "new_value": new_value}
            )

    reused_sources = (
        json.loads(reassessment.reused_field_sources)
        if reassessment.reused_field_sources
        else {}
    )

    return ReassessmentComparisonResponse(
        parent_assessment_id=parent.id,
        reassessment_id=reassessment.id,
        fields_changed=fields_changed,
        reused_field_sources=reused_sources,
        parent=_assessment_snapshot(db, parent),
        reassessment=_assessment_snapshot(db, reassessment),
        structured=lifecycle.structured_comparison(db, parent, reassessment),
        reused_fields=lifecycle.reused_fields(reassessment),
    )

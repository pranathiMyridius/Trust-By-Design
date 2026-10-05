import json
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.ai.control_identifier import identify_applicable_controls, assess_control_design
from app.auth.dependencies import require_role
from app.control_engine.engine import recompute_control_state
from app.database import get_db
from app.models.assessment import Assessment
from app.models.control import CONTROL_CONFIG_FIELDS, Control, ControlAssessment, ControlCondition, ControlGap, ControlRevision
from app.models.risk_factor import RiskFactor
from app.models.user import User, UserRole
from app.schemas.control import (
    AssessmentControlSummary,
    ControlAssessmentCreate,
    ControlAssessmentResponse,
    ControlConditionCreate,
    ControlConditionResponse,
    ControlConditionUpdate,
    ControlCreate,
    ControlGapResponse,
    ControlResponse,
    ControlRevisionResponse,
    ControlUnmap,
    ControlUpdate,
)
from app.services.audit_service import AuditAction, actor_name, log_audit_event
from app.services.decision_lock import ensure_assessment_editable

# Same restriction as the rest of the AI-analysis/scoring pipeline (see
# api/assessments.py) -- control identification/assessment is analyst work.
require_pipeline_role = require_role(UserRole.FCRM_ANALYST, UserRole.MANAGER, UserRole.ADMIN)

router = APIRouter(prefix="/api/assessments", tags=["Controls"])


def _get_assessment_or_404(db: Session, assessment_id: int) -> Assessment:
    assessment = db.query(Assessment).filter(Assessment.id == assessment_id).first()
    if not assessment:
        raise HTTPException(status_code=404, detail="Assessment not found")
    return assessment


def _get_current_risk_factor_or_404(db: Session, assessment_id: int, risk_factor_id: int) -> RiskFactor:
    factor = (
        db.query(RiskFactor)
        .filter(
            RiskFactor.id == risk_factor_id,
            RiskFactor.assessment_id == assessment_id,
            RiskFactor.is_current.is_(True),
        )
        .first()
    )
    if not factor:
        raise HTTPException(status_code=404, detail="Risk factor not found")
    return factor


def _get_current_control_or_404(db: Session, assessment_id: int, control_id: int) -> Control:
    control = (
        db.query(Control)
        .filter(
            Control.id == control_id,
            Control.assessment_id == assessment_id,
            Control.is_current.is_(True),
        )
        .first()
    )
    if not control:
        raise HTTPException(status_code=404, detail="Control not found")
    return control


@router.get("/{assessment_id}/controls", response_model=list[ControlResponse])
def list_controls(assessment_id: int, include_unmapped: bool = False, db: Session = Depends(get_db)):
    """Mapped controls; with include_unmapped, also those removed (R7.2
    history -- see /revisions for who removed them and why)."""

    _get_assessment_or_404(db, assessment_id)

    query = db.query(Control).filter(Control.assessment_id == assessment_id)
    if not include_unmapped:
        query = query.filter(Control.is_current.is_(True))
    return query.order_by(Control.risk_factor_id.asc(), Control.id.asc()).all()


@router.post("/{assessment_id}/controls", response_model=ControlResponse, status_code=201)
def add_control(
    assessment_id: int,
    payload: ControlCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_pipeline_role),
):
    """R7.1/R7.2: map a control from the library to an identified risk."""

    assessment = _get_assessment_or_404(db, assessment_id)
    ensure_assessment_editable(assessment)
    _get_current_risk_factor_or_404(db, assessment_id, payload.risk_factor_id)

    control = Control(
        assessment_id=assessment_id,
        risk_factor_id=payload.risk_factor_id,
        control_type=payload.control_type,
        description=payload.description,
        owner=payload.owner,
        performing_department=payload.performing_department,
        frequency=payload.frequency,
        trigger=payload.trigger,
        scope=payload.scope,
        evidence_source=payload.evidence_source,
        operating_status=payload.operating_status,
        added_by=actor_name(current_user),
    )
    db.add(control)
    db.flush()

    log_audit_event(
        db=db,
        assessment_id=assessment_id,
        action=AuditAction.STATUS_CHANGE,
        actor=control.added_by,
        actor_id=current_user.id,
        details=f"Control mapped: {control.control_type} -> risk factor {payload.risk_factor_id}.",
    )

    recompute_control_state(db, assessment_id)
    _refreeze_residual_if_frozen(db, assessment, current_user, f"control {control.id} was mapped")

    db.commit()
    db.refresh(control)
    return control


def _refreeze_residual_if_frozen(db: Session, assessment: Assessment, user: User, why: str) -> None:
    """
    R8 / R7: once residual risk has been frozen, a control change must flow
    into it -- otherwise the residual of record would describe controls
    that no longer exist. The residual is recalculated as a new version
    (the old one is kept). A human confirmation is carried forward only
    if the calculated result is unchanged; otherwise the audit log says it
    no longer applies, and it must be confirmed again.
    """

    from app.services.residual_risk_service import current_residual_calculation, recalculate_residual_risk

    previous = current_residual_calculation(db, assessment.id)
    if previous is None or not previous.frozen:
        return

    db.flush()
    calc = recalculate_residual_risk(db, assessment, frozen=True, calculated_by=actor_name(user))
    unchanged = (calc.residual_band, calc.residual_score) == (previous.residual_band, previous.residual_score)
    if previous.confirmed_band and unchanged:
        calc.confirmed_band = previous.confirmed_band
        calc.confirmed_score = previous.confirmed_score
        calc.confirmation_reason = previous.confirmation_reason
        calc.confirmed_by = previous.confirmed_by
        calc.confirmed_by_id = previous.confirmed_by_id
        calc.confirmed_at = previous.confirmed_at

    note = ""
    if previous.confirmed_band and not unchanged:
        note = (
            f" The human-confirmed residual ({previous.confirmed_band}, by {previous.confirmed_by}) "
            "no longer applies and must be confirmed again."
        )
    log_audit_event(
        db=db,
        assessment_id=assessment.id,
        action=AuditAction.STATUS_CHANGE,
        previous_status=previous.residual_band,
        new_status=calc.residual_band,
        actor=actor_name(user),
        actor_id=user.id,
        details=(
            f"Residual risk re-frozen after {why}: controls {previous.control_rating} -> "
            f"{calc.control_rating}, residual {previous.residual_band} -> {calc.residual_band}.{note}"
        ),
    )


def _control_config(control: Control) -> dict:
    return {field: getattr(control, field) for field in CONTROL_CONFIG_FIELDS}


# Who counts as "the system" in Control.added_by (AI identification and
# older rows that recorded no person).
SYSTEM_ACTORS = {"System", "AI", None, ""}


def _revise_control(
    db: Session,
    control: Control,
    change_type: str,
    previous: dict,
    new: dict | None,
    reason: str,
    user: User,
) -> ControlRevision:
    """Appends the revision and bumps the control's version. Does not commit."""

    changed = sorted(
        field for field in CONTROL_CONFIG_FIELDS if new is None or previous.get(field) != new.get(field)
    )
    control.version = (control.version or 1) + 1
    revision = ControlRevision(
        control_id=control.id,
        assessment_id=control.assessment_id,
        change_type=change_type,
        version=control.version,
        previous_config=json.dumps(previous, default=str),
        new_config=json.dumps(new, default=str) if new is not None else None,
        changed_fields=json.dumps(changed),
        reason=reason,
        changed_by=actor_name(user),
        changed_by_id=user.id,
    )
    db.add(revision)

    # R10.4: changing a control the system mapped is a change to a system
    # value, so it goes to the override ledger as well.
    if control.added_by in SYSTEM_ACTORS:
        from app.services.override_ledger import record_applied

        record_applied(
            db,
            control.assessment_id,
            "CONTROL_MAPPING",
            "unmapped" if new is None else ",".join(changed),
            control.id,
            {field: previous[field] for field in changed},
            None if new is None else {field: new[field] for field in changed},
            reason,
            user,
        )
    return revision


@router.patch("/{assessment_id}/controls/{control_id}", response_model=ControlResponse)
def update_control(
    assessment_id: int,
    control_id: int,
    payload: ControlUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_pipeline_role),
):
    """R7.2/R7.3/R10.2: edit a control's metadata or remap it to another
    risk. Each change is a new version: the previous configuration, the
    new one, who, when and why are kept as a ControlRevision. Gaps and the
    control rating are recomputed."""

    assessment = _get_assessment_or_404(db, assessment_id)
    ensure_assessment_editable(assessment)
    control = _get_current_control_or_404(db, assessment_id, control_id)

    previous = _control_config(control)
    requested = payload.model_dump(exclude_unset=True, exclude={"reason"})
    if requested.get("risk_factor_id") is not None:
        _get_current_risk_factor_or_404(db, assessment_id, requested["risk_factor_id"])
    for field, value in requested.items():
        if field in CONTROL_CONFIG_FIELDS and value is not None:
            setattr(control, field, value)
    new = _control_config(control)
    if new == previous:
        raise HTTPException(status_code=422, detail="Nothing changed: the control already has this configuration.")

    remapped = new["risk_factor_id"] != previous["risk_factor_id"]
    revision = _revise_control(
        db, control, "REMAP" if remapped else "EDIT", previous, new, payload.reason, current_user
    )

    if remapped:
        summary = f"remapped from risk factor {previous['risk_factor_id']} to {new['risk_factor_id']}"
    else:
        summary = "changed " + ", ".join(json.loads(revision.changed_fields))
    log_audit_event(
        db=db,
        assessment_id=assessment_id,
        action=AuditAction.CONTROL_REMAPPED if remapped else AuditAction.CONTROL_UPDATED,
        actor=actor_name(current_user),
        actor_id=current_user.id,
        details=f"Control {control.control_type} (id={control.id}) v{control.version}: {summary}. Reason: {payload.reason}",
    )

    db.flush()
    recompute_control_state(db, assessment_id)
    _refreeze_residual_if_frozen(db, assessment, current_user, f"a change to control {control.id}")

    db.commit()
    db.refresh(control)
    return control


@router.post("/{assessment_id}/controls/{control_id}/unmap", response_model=ControlResponse)
def unmap_control(
    assessment_id: int,
    control_id: int,
    payload: ControlUnmap,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_pipeline_role),
):
    """R7.2/R10.2: remove a control from the assessment. It stops counting
    (gaps are recomputed, so its risk may now show a gap) but is never
    deleted: the row, its effectiveness assessments and the revision
    saying who removed it and why all stay on record."""

    assessment = _get_assessment_or_404(db, assessment_id)
    ensure_assessment_editable(assessment)
    control = _get_current_control_or_404(db, assessment_id, control_id)

    previous = _control_config(control)
    _revise_control(db, control, "UNMAP", previous, None, payload.reason, current_user)
    control.is_current = False
    control.superseded_at = datetime.now(timezone.utc)

    log_audit_event(
        db=db,
        assessment_id=assessment_id,
        action=AuditAction.CONTROL_UNMAPPED,
        actor=actor_name(current_user),
        actor_id=current_user.id,
        details=(
            f"Control {control.control_type} (id={control.id}) unmapped from risk factor "
            f"{previous['risk_factor_id']}. Reason: {payload.reason}"
        ),
    )

    db.flush()
    recompute_control_state(db, assessment_id)
    _refreeze_residual_if_frozen(db, assessment, current_user, f"a change to control {control.id}")

    db.commit()
    db.refresh(control)
    return control


@router.get(
    "/{assessment_id}/controls/{control_id}/revisions",
    response_model=list[ControlRevisionResponse],
)
def list_control_revisions(assessment_id: int, control_id: int, db: Session = Depends(get_db)):
    """Every change to the control's mapping or configuration, oldest first."""

    _get_assessment_or_404(db, assessment_id)
    rows = (
        db.query(ControlRevision)
        .filter(ControlRevision.control_id == control_id, ControlRevision.assessment_id == assessment_id)
        .order_by(ControlRevision.version.asc())
        .all()
    )
    return [
        ControlRevisionResponse(
            id=row.id,
            control_id=row.control_id,
            assessment_id=row.assessment_id,
            change_type=row.change_type,
            version=row.version,
            previous_config=json.loads(row.previous_config),
            new_config=json.loads(row.new_config) if row.new_config else None,
            changed_fields=json.loads(row.changed_fields),
            reason=row.reason,
            changed_by=row.changed_by,
            changed_by_id=row.changed_by_id,
            changed_at=row.changed_at,
        )
        for row in rows
    ]


@router.post(
    "/{assessment_id}/controls/{control_id}/assessments",
    response_model=ControlAssessmentResponse,
    status_code=201,
)
def assess_control(
    assessment_id: int,
    control_id: int,
    payload: ControlAssessmentCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_pipeline_role),
):
    """R7.4/R7.5/R7.6: record a design/operating-effectiveness assessment
    of a control. Supersedes the control's previous current assessment
    (same versioning pattern as InherentRiskCalculation) rather than
    overwriting it, so a past finding stays inspectable."""

    assessment = _get_assessment_or_404(db, assessment_id)
    ensure_assessment_editable(assessment)
    control = _get_current_control_or_404(db, assessment_id, control_id)

    previous = (
        db.query(ControlAssessment)
        .filter(
            ControlAssessment.control_id == control_id,
            ControlAssessment.is_current.is_(True),
        )
        .first()
    )

    next_version = 1
    if previous:
        previous.is_current = False
        previous.superseded_at = datetime.now(timezone.utc)
        next_version = previous.version + 1

    assessment_row = ControlAssessment(
        control_id=control_id,
        assessment_id=assessment_id,
        design_adequacy=payload.design_adequacy,
        design_rationale=payload.design_rationale,
        operating_effectiveness=payload.operating_effectiveness,
        effectiveness_rationale=payload.effectiveness_rationale,
        has_evidence=payload.has_evidence,
        coverage_complete=payload.coverage_complete,
        depends_on_unavailable_data=payload.depends_on_unavailable_data,
        assessed_by=actor_name(current_user),
        version=next_version,
        is_current=True,
    )
    db.add(assessment_row)
    db.flush()

    log_audit_event(
        db=db,
        assessment_id=assessment_id,
        action=AuditAction.STATUS_CHANGE,
        actor=assessment_row.assessed_by,
        actor_id=current_user.id,
        details=(
            f"Control {control.control_type} (id={control.id}) assessed: "
            f"design={payload.design_adequacy}, "
            f"effectiveness={payload.operating_effectiveness}."
        ),
    )

    recompute_control_state(db, assessment_id)
    _refreeze_residual_if_frozen(db, assessment, current_user, f"control {control.id} was re-assessed")

    db.commit()
    db.refresh(assessment_row)
    return assessment_row


@router.get("/{assessment_id}/control-conditions", response_model=list[ControlConditionResponse])
def list_control_conditions(assessment_id: int, db: Session = Depends(get_db)):
    _get_assessment_or_404(db, assessment_id)

    return (
        db.query(ControlCondition)
        .filter(ControlCondition.assessment_id == assessment_id)
        .order_by(ControlCondition.created_at.desc())
        .all()
    )


@router.post(
    "/{assessment_id}/controls/{control_id}/conditions",
    response_model=ControlConditionResponse,
    status_code=201,
)
def add_control_condition(
    assessment_id: int,
    control_id: int,
    payload: ControlConditionCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_pipeline_role),
):
    """R7.7: record a required control enhancement/condition."""

    ensure_assessment_editable(_get_assessment_or_404(db, assessment_id))
    _get_current_control_or_404(db, assessment_id, control_id)

    condition = ControlCondition(
        control_id=control_id,
        assessment_id=assessment_id,
        description=payload.description,
        due_date=payload.due_date,
        owner=payload.owner,
        created_by=actor_name(current_user),
    )
    db.add(condition)

    log_audit_event(
        db=db,
        assessment_id=assessment_id,
        action=AuditAction.STATUS_CHANGE,
        actor=condition.created_by,
        actor_id=current_user.id,
        details=f"Control condition recorded on control id={control_id}.",
    )

    db.commit()
    db.refresh(condition)
    return condition


@router.patch(
    "/{assessment_id}/control-conditions/{condition_id}",
    response_model=ControlConditionResponse,
)
def update_control_condition(
    assessment_id: int,
    condition_id: int,
    payload: ControlConditionUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_pipeline_role),
):
    """R7.7: track a control condition to completion."""

    condition = (
        db.query(ControlCondition)
        .filter(
            ControlCondition.id == condition_id,
            ControlCondition.assessment_id == assessment_id,
        )
        .first()
    )
    if not condition:
        raise HTTPException(status_code=404, detail="Control condition not found")

    if payload.description is not None:
        condition.description = payload.description
    if payload.due_date is not None:
        condition.due_date = payload.due_date
    if payload.owner is not None:
        condition.owner = payload.owner
    if payload.status is not None:
        condition.status = payload.status
        condition.completed_at = (
            datetime.now(timezone.utc) if payload.status == "COMPLETED" else None
        )

    log_audit_event(
        db=db,
        assessment_id=assessment_id,
        action=AuditAction.STATUS_CHANGE,
        actor=actor_name(current_user),
        actor_id=current_user.id,
        details=f"Control condition id={condition_id} updated"
        + (f" to {payload.status}." if payload.status else "."),
    )

    db.commit()
    db.refresh(condition)
    return condition


@router.get("/{assessment_id}/control-summary", response_model=AssessmentControlSummary)
def get_control_summary(assessment_id: int, db: Session = Depends(get_db)):
    """
    R7.2/R7.6: recomputes gaps + the control-reduction contribution from
    whatever controls/assessments currently exist, then returns
    everything the Controls stage UI needs in one call.
    """

    _get_assessment_or_404(db, assessment_id)

    result = recompute_control_state(db, assessment_id)
    db.commit()

    controls = (
        db.query(Control)
        .filter(Control.assessment_id == assessment_id, Control.is_current.is_(True))
        .order_by(Control.risk_factor_id.asc(), Control.id.asc())
        .all()
    )

    control_assessments: dict[int, ControlAssessment] = {}
    for control in controls:
        current = (
            db.query(ControlAssessment)
            .filter(
                ControlAssessment.control_id == control.id,
                ControlAssessment.is_current.is_(True),
            )
            .first()
        )
        if current:
            control_assessments[control.id] = current

    gaps = (
        db.query(ControlGap)
        .filter(ControlGap.assessment_id == assessment_id, ControlGap.resolved.is_(False))
        .order_by(ControlGap.detected_at.desc())
        .all()
    )

    conditions = (
        db.query(ControlCondition)
        .filter(
            ControlCondition.assessment_id == assessment_id,
            ControlCondition.status.notin_(["COMPLETED", "CANCELLED"]),
        )
        .order_by(ControlCondition.created_at.desc())
        .all()
    )

    return AssessmentControlSummary(
        assessment_id=assessment_id,
        controls=controls,
        control_assessments={
            control_id: ControlAssessmentResponse.model_validate(assessment_row)
            for control_id, assessment_row in control_assessments.items()
        },
        gaps=gaps,
        conditions=conditions,
        control_reduction=result["control_reduction"],
        risk_factor_count=result["risk_factor_count"],
    )


# AI-powered control identification


class IdentifyControlsRequest(BaseModel):
    risk_factor_id: int
    auto_create: bool = False  # If true, automatically create suggested controls


class IdentifyControlsResponse(BaseModel):
    suggested_controls: list[str]
    reasoning: str


@router.post("/{assessment_id}/risk-factors/{risk_factor_id}/identify-controls",
             response_model=IdentifyControlsResponse)
def identify_controls_for_risk(
    assessment_id: int,
    risk_factor_id: int,
    payload: IdentifyControlsRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_pipeline_role),
):
    """
    Use AI to identify applicable controls for an identified risk factor.
    Optionally auto-creates the suggested controls.
    """
    assessment = _get_assessment_or_404(db, assessment_id)
    ensure_assessment_editable(assessment)

    risk_factor = _get_current_risk_factor_or_404(db, assessment_id, risk_factor_id)

    # Gather assessment context for AI
    context = {
        "assessment_title": assessment.title,
        "change_type": assessment.change_type,
        "description": assessment.description,
    }

    # Call AI to identify controls
    suggested_controls = identify_applicable_controls(
        risk_category=risk_factor.category,
        risk_rationale=risk_factor.rationale,
        misuse_scenario=risk_factor.misuse_scenario or "",
        assessment_context=context,
    )

    # Optionally auto-create the controls
    if payload.auto_create and suggested_controls:
        for control_type in suggested_controls:
            # Check if control already exists
            existing = (
                db.query(Control)
                .filter(
                    Control.assessment_id == assessment_id,
                    Control.risk_factor_id == risk_factor_id,
                    Control.control_type == control_type,
                    Control.is_current.is_(True),
                )
                .first()
            )

            if not existing:
                control = Control(
                    assessment_id=assessment_id,
                    risk_factor_id=risk_factor_id,
                    control_type=control_type,
                    added_by=current_user.email,
                )
                db.add(control)

        db.flush()

        log_audit_event(
            db,
            assessment_id=assessment_id,
            action=AuditAction.CONTROL_IDENTIFIED,
            actor=current_user.full_name or current_user.email,
            actor_id=current_user.id,
            details=f"AI-identified {len(suggested_controls)} controls for risk factor {risk_factor_id}",
        )

        # Same order as add_control: recompute, then one commit, so the
        # controls, the audit event and the gap state land together.
        recompute_control_state(db, assessment_id)
        db.commit()

    return IdentifyControlsResponse(
        suggested_controls=suggested_controls,
        reasoning="Controls identified by AI analysis of risk factors"
    )


class AssessControlDesignRequest(BaseModel):
    pass


class AssessControlDesignResponse(BaseModel):
    design_adequacy: str  # "DESIGN_ADEQUATE" | "DESIGN_INADEQUATE" | "NOT_ASSESSED"
    rationale: str
    apply_to_control: bool = False  # If true, update the control assessment


@router.post("/{assessment_id}/controls/{control_id}/assess-design",
             response_model=AssessControlDesignResponse)
def assess_control_design_endpoint(
    assessment_id: int,
    control_id: int,
    payload: AssessControlDesignRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_pipeline_role),
):
    """
    Use AI to assess the design adequacy of a control for its mapped risk.
    """
    assessment = _get_assessment_or_404(db, assessment_id)
    ensure_assessment_editable(assessment)

    control = _get_current_control_or_404(db, assessment_id, control_id)

    # Get the risk factor
    risk_factor = (
        db.query(RiskFactor)
        .filter(RiskFactor.id == control.risk_factor_id, RiskFactor.is_current.is_(True))
        .first()
    )

    if not risk_factor:
        raise HTTPException(status_code=404, detail="Associated risk factor not found")

    # Gather assessment context
    context = {
        "assessment_title": assessment.title,
        "change_type": assessment.change_type,
        "risk_category": risk_factor.category,
    }

    # Call AI to assess design
    assessment_result = assess_control_design(
        control_type=control.control_type,
        risk_rationale=risk_factor.rationale,
        assessment_context=context,
    )

    log_audit_event(
        db,
        assessment_id=assessment_id,
        action=AuditAction.CONTROL_ASSESSED,
        actor=current_user.full_name or current_user.email,
        actor_id=current_user.id,
        details=f"AI design assessment for control {control_id}: {assessment_result['design_adequacy']}",
    )
    db.commit()

    return AssessControlDesignResponse(
        design_adequacy=assessment_result["design_adequacy"],
        rationale=assessment_result["rationale"],
        apply_to_control=False
    )


# ---------------------------------------------------------------------------
# AI evidence check: suggestions that uploaded documents support a control.
# The AI only suggests; an analyst accepting a suggestion is what records
# evidence on the control.
# ---------------------------------------------------------------------------


class EvidenceLinkResponse(BaseModel):
    id: int
    control_id: int
    document_id: int | None
    document_name: str | None
    document_version: int | None
    # False when the document has since been replaced by a newer version:
    # the link should be re-checked.
    document_is_current: bool
    support_level: str
    confidence: str | None
    quote: str | None
    rationale: str | None
    shortfalls: list[str]
    suggested_effectiveness: str | None
    status: str
    model: str | None
    checked_at: datetime
    decided_by: str | None
    decided_at: datetime | None
    decision_note: str | None


class EvidenceCheckSummary(BaseModel):
    status: str
    controls_checked: int
    controls_failed: int
    links: list[EvidenceLinkResponse]


class EvidenceLinkDecision(BaseModel):
    decision: str  # ACCEPT | REJECT
    note: str | None = None


def _evidence_links(db: Session, assessment_id: int) -> list[EvidenceLinkResponse]:
    from app.models.assessment_document import AssessmentDocument
    from app.models.control_evidence import ControlEvidenceLink

    rows = (
        db.query(ControlEvidenceLink, AssessmentDocument)
        .outerjoin(AssessmentDocument, AssessmentDocument.id == ControlEvidenceLink.document_id)
        .join(Control, Control.id == ControlEvidenceLink.control_id)
        .filter(
            ControlEvidenceLink.assessment_id == assessment_id,
            ControlEvidenceLink.status != "SUPERSEDED",
            Control.is_current.is_(True),
        )
        .order_by(ControlEvidenceLink.control_id.asc(), ControlEvidenceLink.id.asc())
        .all()
    )
    return [
        EvidenceLinkResponse(
            id=link.id,
            control_id=link.control_id,
            document_id=link.document_id,
            document_name=document.filename if document else None,
            document_version=link.document_version,
            document_is_current=bool(document and document.is_current),
            support_level=link.support_level,
            confidence=link.confidence,
            quote=link.quote,
            rationale=link.rationale,
            shortfalls=json.loads(link.shortfalls) if link.shortfalls else [],
            suggested_effectiveness=link.suggested_effectiveness,
            status=link.status,
            model=link.model,
            checked_at=link.checked_at,
            decided_by=link.decided_by,
            decided_at=link.decided_at,
            decision_note=link.decision_note,
        )
        for link, document in rows
    ]


@router.get("/{assessment_id}/evidence-links", response_model=list[EvidenceLinkResponse])
def list_evidence_links(assessment_id: int, db: Session = Depends(get_db)):
    _get_assessment_or_404(db, assessment_id)
    return _evidence_links(db, assessment_id)


@router.post("/{assessment_id}/evidence-check", response_model=EvidenceCheckSummary)
def run_evidence_check_endpoint(
    assessment_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_pipeline_role),
):
    """Re-run the AI evidence check against the assessment's current documents."""

    from app.services.control_evidence_service import run_evidence_check

    assessment = _get_assessment_or_404(db, assessment_id)
    ensure_assessment_editable(assessment)
    summary = run_evidence_check(db, assessment_id)
    log_audit_event(
        db=db,
        assessment_id=assessment_id,
        action=AuditAction.CONTROL_ASSESSED,
        actor=actor_name(current_user),
        actor_id=current_user.id,
        details=(
            f"AI evidence check ({summary['status']}): {summary['controls_checked']} control(s) checked, "
            f"{summary['controls_failed']} could not be checked."
        ),
    )
    db.commit()
    return EvidenceCheckSummary(**summary, links=_evidence_links(db, assessment_id))


@router.post(
    "/{assessment_id}/evidence-links/{link_id}/decision",
    response_model=EvidenceLinkResponse,
)
def decide_evidence_link(
    assessment_id: int,
    link_id: int,
    payload: EvidenceLinkDecision,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_pipeline_role),
):
    """Accept (records evidence on the control) or reject an AI suggestion."""

    from app.models.control_evidence import ControlEvidenceLink
    from app.services.control_evidence_service import accept_link, reject_link

    assessment = _get_assessment_or_404(db, assessment_id)
    ensure_assessment_editable(assessment)

    decision = (payload.decision or "").upper()
    if decision not in ("ACCEPT", "REJECT"):
        raise HTTPException(status_code=400, detail="decision must be ACCEPT or REJECT.")

    link = (
        db.query(ControlEvidenceLink)
        .filter(ControlEvidenceLink.id == link_id, ControlEvidenceLink.assessment_id == assessment_id)
        .first()
    )
    if not link:
        raise HTTPException(status_code=404, detail="Evidence suggestion not found")
    if link.status != "SUGGESTED":
        raise HTTPException(status_code=409, detail=f"This suggestion is already {link.status.lower()}.")
    control = _get_current_control_or_404(db, assessment_id, link.control_id)
    name = actor_name(current_user)

    if decision == "ACCEPT":
        if link.document_id is None or link.support_level not in ("SUPPORTED", "PARTIAL"):
            raise HTTPException(
                status_code=400,
                detail="There is no supporting document to accept; upload evidence or reject this result.",
            )
        wrote = accept_link(db, control, link, name, current_user.id)
        log_audit_event(
            db=db,
            assessment_id=assessment_id,
            action=AuditAction.CONTROL_ASSESSED,
            actor=name,
            actor_id=current_user.id,
            details=(
                f"Accepted AI evidence suggestion {link.id} for control {control.control_type} "
                f"(id={control.id}), document {link.document_id} v{link.document_version}"
                + ("; evidence recorded." if wrote else "; control already had evidence recorded.")
            ),
        )
        if wrote:
            recompute_control_state(db, assessment_id)
            _refreeze_residual_if_frozen(db, assessment, current_user, f"evidence was accepted for control {control.id}")
    else:
        reject_link(link, name, current_user.id, payload.note)
        log_audit_event(
            db=db,
            assessment_id=assessment_id,
            action=AuditAction.CONTROL_ASSESSED,
            actor=name,
            actor_id=current_user.id,
            details=f"Rejected AI evidence suggestion {link.id} for control {control.control_type} (id={control.id}).",
        )

    db.commit()
    return next(item for item in _evidence_links(db, assessment_id) if item.id == link_id)

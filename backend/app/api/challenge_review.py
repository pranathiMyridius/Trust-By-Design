import json
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, field_validator
from sqlalchemy.orm import Session

from app.auth.dependencies import get_current_user, log_denied_attempt, require_role
from app.challenge_engine import rules
from app.challenge_engine.engine import recompute_challenge_review
from app.database import get_db
from app.models.assessment import Assessment
from app.models.challenge_review import ChallengeFinding, ChallengeTriggerConfig
from app.models.user import User, UserRole
from app.schemas.challenge_review import (
    ChallengeFindingAccept,
    ChallengeFindingResolve,
    ChallengeFindingResponse,
    ChallengeReviewResponse,
    ChallengeTriggerConfigResponse,
    ChallengeTriggerConfigUpdate,
    TriggerResult,
)
from app.services.audit_service import AuditAction, actor_name, log_audit_event
from app.services.decision_lock import ensure_assessment_editable

# Resolving a finding (fixing what it points at) is analyst work, so it
# uses the same roles as the rest of the pipeline. Configuring triggers
# (R11.1) needs a Manager or Admin. Accepting a finding *without* fixing it
# (R11.6) is a documented Committee exception, MEDIUM only (see
# accept_challenge_finding), so an analyst can't wave through a challenge to their
# own work.
require_pipeline_role = require_role(UserRole.FCRM_ANALYST, UserRole.MANAGER, UserRole.ADMIN)
require_challenge_authority = require_role(UserRole.MANAGER, UserRole.ADMIN)

router = APIRouter(tags=["Challenge Review"])


def _config_to_response(config: ChallengeTriggerConfig) -> dict:
    return {
        "id": config.id,
        "name": config.name,
        "is_active": config.is_active,
        "trigger_risk_levels": json.loads(config.trigger_risk_levels),
        "residual_risk_tolerance": config.residual_risk_tolerance,
        "rating_mismatch_enabled": config.rating_mismatch_enabled,
        "low_confidence_enabled": config.low_confidence_enabled,
        "high_risk_jurisdictions": json.loads(config.high_risk_jurisdictions),
        "high_risk_technologies": json.loads(config.high_risk_technologies),
        "disabled_triggers": json.loads(config.disabled_triggers or "[]"),
        "configurable_triggers": sorted(rules.CONFIGURABLE_TRIGGERS),
        "mandatory_triggers": sorted(rules.MANDATORY_TRIGGERS),
        "created_at": config.created_at,
        "updated_at": config.updated_at,
    }


def _get_or_create_active_config(db: Session) -> ChallengeTriggerConfig:
    config = (
        db.query(ChallengeTriggerConfig)
        .filter(ChallengeTriggerConfig.is_active.is_(True))
        .first()
    )

    if config:
        return config

    config = ChallengeTriggerConfig(
        name="Default Challenge Triggers",
        is_active=True,
        trigger_risk_levels=json.dumps(rules.DEFAULT_TRIGGER_RISK_LEVELS),
        residual_risk_tolerance=rules.DEFAULT_RESIDUAL_RISK_TOLERANCE,
        high_risk_jurisdictions=json.dumps(rules.DEFAULT_HIGH_RISK_JURISDICTIONS),
        high_risk_technologies=json.dumps(rules.DEFAULT_HIGH_RISK_TECHNOLOGIES),
        disabled_triggers="[]",
    )
    db.add(config)
    db.commit()
    db.refresh(config)
    return config


@router.get("/api/challenge-triggers", response_model=ChallengeTriggerConfigResponse)
def get_challenge_trigger_config(db: Session = Depends(get_db)):
    return _config_to_response(_get_or_create_active_config(db))


@router.patch("/api/challenge-triggers", response_model=ChallengeTriggerConfigResponse)
def update_challenge_trigger_config(
    payload: ChallengeTriggerConfigUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_challenge_authority),
):
    config = _get_or_create_active_config(db)

    if payload.name is not None:
        config.name = payload.name
    if payload.trigger_risk_levels is not None:
        config.trigger_risk_levels = json.dumps(payload.trigger_risk_levels)
    if payload.residual_risk_tolerance is not None:
        config.residual_risk_tolerance = payload.residual_risk_tolerance
    if payload.rating_mismatch_enabled is not None:
        config.rating_mismatch_enabled = payload.rating_mismatch_enabled
    if payload.low_confidence_enabled is not None:
        config.low_confidence_enabled = payload.low_confidence_enabled
    if payload.high_risk_jurisdictions is not None:
        config.high_risk_jurisdictions = json.dumps(payload.high_risk_jurisdictions)
    if payload.high_risk_technologies is not None:
        config.high_risk_technologies = json.dumps(payload.high_risk_technologies)
    if payload.disabled_triggers is not None:
        config.disabled_triggers = json.dumps(payload.disabled_triggers)

    changed = ", ".join(sorted(payload.model_dump(exclude_unset=True))) or "nothing"
    disabled = json.loads(config.disabled_triggers or "[]")
    log_audit_event(
        db=db,
        assessment_id=None,
        action=AuditAction.CHALLENGE_CONFIG_CHANGED,
        actor=current_user.full_name or current_user.email,
        actor_id=current_user.id,
        details=(
            f"Challenge trigger configuration '{config.name}' updated ({changed}). "
            f"Disabled triggers: {', '.join(disabled) or 'none'}."
        ),
    )

    db.commit()
    db.refresh(config)
    return _config_to_response(config)


def _get_assessment_or_404(db: Session, assessment_id: int) -> Assessment:
    assessment = db.query(Assessment).filter(Assessment.id == assessment_id).first()
    if not assessment:
        raise HTTPException(status_code=404, detail="Assessment not found")
    return assessment


@router.get(
    "/api/assessments/{assessment_id}/challenge-review",
    response_model=ChallengeReviewResponse,
)
def get_challenge_review(assessment_id: int, db: Session = Depends(get_db)):
    """
    R11.1-R11.5: recomputes trigger conditions and findings from the
    assessment's current state, then returns everything the Challenge
    stage UI needs -- which triggers fired, and every finding (open,
    resolved, or accepted) with its resolution history.
    """

    _get_assessment_or_404(db, assessment_id)

    result = recompute_challenge_review(db, assessment_id)
    db.commit()

    high_severity_open_count = sum(
        1
        for finding in result["findings"]
        if finding.severity in rules.HIGH_SEVERITY_LEVELS and finding.resolution_status == "OPEN"
    )

    return ChallengeReviewResponse(
        assessment_id=assessment_id,
        triggered=result["triggered"],
        triggers=[TriggerResult(**t) for t in result["triggers"]],
        findings=result["findings"],
        high_severity_open_count=high_severity_open_count,
    )


def _get_finding_or_404(db: Session, assessment_id: int, finding_id: int) -> ChallengeFinding:
    finding = (
        db.query(ChallengeFinding)
        .filter(
            ChallengeFinding.id == finding_id,
            ChallengeFinding.assessment_id == assessment_id,
        )
        .first()
    )
    if not finding:
        raise HTTPException(status_code=404, detail="Challenge finding not found")
    return finding


@router.patch(
    "/api/assessments/{assessment_id}/challenge-findings/{finding_id}/resolve",
    response_model=ChallengeFindingResponse,
)
def resolve_challenge_finding(
    assessment_id: int,
    finding_id: int,
    payload: ChallengeFindingResolve,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_pipeline_role),
):
    ensure_assessment_editable(_get_assessment_or_404(db, assessment_id))
    finding = _get_finding_or_404(db, assessment_id, finding_id)

    finding.resolution_status = "RESOLVED"
    finding.resolved_by = current_user.full_name or current_user.email
    finding.resolved_at = datetime.now(timezone.utc)
    finding.resolution_note = payload.resolution_note

    log_audit_event(
        db=db,
        assessment_id=assessment_id,
        action=AuditAction.STATUS_CHANGE,
        actor=current_user.full_name or current_user.email,
        actor_id=current_user.id,
        details=f"Challenge finding {finding.id} ({finding.category}) resolved: "
        f"{payload.resolution_note}",
    )

    db.commit()
    db.refresh(finding)
    return finding


@router.patch(
    "/api/assessments/{assessment_id}/challenge-findings/{finding_id}/accept",
    response_model=ChallengeFindingResponse,
)
def accept_challenge_finding(
    assessment_id: int,
    finding_id: int,
    payload: ChallengeFindingAccept,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """R11.6/AC5: accept a finding without fixing it -- records the
    accepting user, reason, and timestamp.

    G-4 (user direction 2026-10-03, provisional policy): only a MEDIUM
    finding may be accepted, and only as a documented Committee exception
    -- by an eligible committee member (not a party to or preparer of the
    case) while the Committee holds it. HIGH and CRITICAL findings must be
    resolved."""

    from app.governance import quorum as quorum_rules
    from app.governance.policy import holds, policy

    assessment = _get_assessment_or_404(db, assessment_id)
    ensure_assessment_editable(assessment)
    finding = _get_finding_or_404(db, assessment_id, finding_id)
    rules_ = policy()

    refusal = None
    if not holds(current_user, rules_["finding_acceptors"]):
        refusal = "Only a committee member may accept a finding, as a documented Committee exception."
    else:
        why, names = quorum_rules.excluded(db, assessment)
        conflict = quorum_rules.ineligible_reason(current_user, why, names)
        if conflict:
            refusal = f"You can't accept a finding on this case ({conflict})."
    if refusal:
        log_denied_attempt(db, current_user, request, f"finding acceptance refused: {refusal}", assessment_id=assessment_id)
        raise HTTPException(status_code=403, detail=refusal)

    if finding.severity not in rules_["acceptable_finding_severities"]:
        raise HTTPException(
            status_code=409,
            detail=(
                f"A {finding.severity} finding can't be accepted; it must be resolved before the "
                "assessment goes to the Committee. Only "
                + "/".join(rules_["acceptable_finding_severities"])
                + " findings may be accepted, as a documented Committee exception."
            ),
        )
    if finding.resolution_status != "OPEN" and not (
        finding.resolution_status == "ACCEPTED" and finding.acceptance_authority != "COMMITTEE"
    ):
        raise HTTPException(status_code=409, detail=f"This finding is already {finding.resolution_status.lower()}.")
    if assessment.status not in rules_["finding_acceptance_statuses"]:
        raise HTTPException(
            status_code=409,
            detail="A finding can be accepted only while the Committee holds the case (Ready for Committee, Committee Review or Deferred).",
        )

    finding.resolution_status = "ACCEPTED"
    finding.accepted_by = current_user.full_name or current_user.email
    finding.accepted_by_id = current_user.id
    finding.acceptance_authority = "COMMITTEE"
    finding.accepted_at = datetime.now(timezone.utc)
    finding.accepted_reason = payload.reason

    log_audit_event(
        db=db,
        assessment_id=assessment_id,
        action=AuditAction.STATUS_CHANGE,
        actor=current_user.full_name or current_user.email,
        actor_id=current_user.id,
        details=(
            f"Challenge finding {finding.id} ({finding.severity}, {finding.category}) accepted as a "
            f"documented Committee exception: {payload.reason}"
        ),
    )

    db.commit()
    db.refresh(finding)
    return finding


# ---------------------------------------------------------------------
# R11: the mandatory challenge-review sign-off.
# ---------------------------------------------------------------------


class ChallengeSignoffCreate(BaseModel):
    # The reviewer's attestation: what was reviewed and how the findings
    # (or the absence of any trigger) were dealt with.
    reason: str

    @field_validator("reason")
    @classmethod
    def reason_required(cls, value: str) -> str:
        if not value or not value.strip():
            raise ValueError("A reason or summary of the review is required.")
        return value.strip()


@router.get("/api/assessments/{assessment_id}/challenge-review/signoff")
def get_challenge_signoff(
    assessment_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """P3: the current independent review and sign-off, whether together
    they count, every earlier one, and what the user may do next (computed
    here so the UI never re-implements the rules)."""

    from app.governance.policy import POLICY_STATUS
    from app.services.challenge_signoff import (
        SIGNOFF_STATUSES,
        current_review,
        current_signoff,
        review_problem,
        signoff_eligibility,
        signoff_history,
        signoff_payload,
        signoff_problem,
    )

    assessment = db.query(Assessment).filter(Assessment.id == assessment_id).first()
    if not assessment:
        raise HTTPException(status_code=404, detail="Assessment not found")
    review, current = current_review(db, assessment_id), current_signoff(db, assessment_id)
    problem = signoff_problem(db, assessment)

    def action(check: str | None) -> dict:
        if current_user.id == assessment.owner_id:
            check = "You own this assessment and cannot take part in its challenge review."
        elif assessment.status not in SIGNOFF_STATUSES:
            check = f"Not possible while the assessment is {assessment.status}."
        return {"allowed": check is None, "reason": check}

    signoff_check = signoff_eligibility(db, assessment, current_user)
    if signoff_check is None and review is None:
        signoff_check = "The independent challenge review must be completed first."
    return {
        "assessment_id": assessment_id,
        "review": signoff_payload(review) if review else None,
        "current": signoff_payload(current) if current else None,
        "valid": problem is None,
        "problem": problem,
        "history": [signoff_payload(row) for row in signoff_history(db, assessment_id)],
        "actions": {
            "review": action(review_problem(db, assessment, current_user)),
            "signoff": action(signoff_check),
        },
        "policy_status": POLICY_STATUS,
    }


@router.post("/api/assessments/{assessment_id}/challenge-review/review", status_code=201)
def complete_challenge_review(
    assessment_id: int,
    payload: ChallengeSignoffCreate,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """P3 (R-GOV-03): the independent challenge review -- by a Challenge
    Reviewer, Senior Analyst or QA Reviewer who did not prepare the case."""

    from app.services.challenge_signoff import record_review, signoff_payload

    assessment = db.query(Assessment).filter(Assessment.id == assessment_id).first()
    if not assessment:
        raise HTTPException(status_code=404, detail="Assessment not found")
    ensure_assessment_editable(assessment)

    try:
        row = record_review(db, assessment, current_user, payload.reason)
    except HTTPException as exc:
        if exc.status_code == 403:
            db.rollback()
            log_denied_attempt(
                db, current_user, request, f"challenge review refused: {exc.detail}", assessment_id=assessment_id
            )
        raise

    log_audit_event(
        db=db,
        assessment_id=assessment_id,
        action=AuditAction.CHALLENGE_REVIEW_COMPLETED,
        actor=actor_name(current_user),
        actor_id=current_user.id,
        details=f"Independent challenge review completed (v{row.version}, {row.outcome}): {payload.reason}",
    )
    db.commit()
    db.refresh(row)
    return signoff_payload(row)


@router.post("/api/assessments/{assessment_id}/challenge-review/signoff", status_code=201)
def sign_off_challenge_review(
    assessment_id: int,
    payload: ChallengeSignoffCreate,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    from app.services.challenge_signoff import sign_off, signoff_payload

    assessment = db.query(Assessment).filter(Assessment.id == assessment_id).first()
    if not assessment:
        raise HTTPException(status_code=404, detail="Assessment not found")
    ensure_assessment_editable(assessment)

    try:
        row = sign_off(db, assessment, current_user, payload.reason)
    except HTTPException as exc:
        if exc.status_code == 403:
            db.rollback()
            log_denied_attempt(
                db, current_user, request, f"challenge sign-off refused: {exc.detail}", assessment_id=assessment_id
            )
        raise

    log_audit_event(
        db=db,
        assessment_id=assessment_id,
        action=AuditAction.CHALLENGE_REVIEW_SIGNED_OFF,
        actor=actor_name(current_user),
        actor_id=current_user.id,
        details=(
            f"Challenge review signed off (v{row.version}, {row.outcome})"
            + (" -- critical case escalated to the Committee" if row.committee_escalation else "")
            + f": {payload.reason}"
        ),
    )
    db.commit()
    db.refresh(row)
    return signoff_payload(row)

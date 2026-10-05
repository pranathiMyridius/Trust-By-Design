from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, field_validator
from sqlalchemy.orm import Session

from app.auth.dependencies import get_current_user, log_denied_attempt
from app.challenge_engine.engine import has_blocking_findings, recompute_challenge_review
from app.database import get_db
from app.services import delegation as delegation_rules
from app.services.delegation import Authority
from app.models.action_item import ActionItem, ActionItemSourceType
from app.models.assessment import Assessment
from app.models.assessment_comment import AssessmentComment, CommentVisibility
from app.models.challenge_review import ChallengeFinding
from app.models.committee_condition import CommitteeCondition
from app.models.committee_vote import CommitteeVote
from app.models.audit_event import AuditEvent
from app.models.user import User, UserRole
from app.schemas.assessment import AssessmentResponse
from app.schemas.assessment_comment import CommentCreate, CommentResolve, CommentResponse
from app.schemas.challenge_review import ChallengeFindingResponse
from app.schemas.committee_decision import (
    AssessmentAmendmentRequest,
    CommitteeConditionResponse,
    CommitteeConditionUpdate,
    CommitteeDecisionRequestV2,
    CommitteeVoteCreate,
    CommitteeVoteResponse,
    DecisionPackageResponse,
)
from app.services.assessment_draft_service import generate_assessment_draft
from app.services.audit_service import AuditAction, log_audit_event
from app.models.decision_record import DecisionRecord
from app.services.decision_lock import ensure_assessment_editable
from app.services.decision_record_service import (
    freeze_decision_record,
    missing_requirements,
    verify_record,
)
from app.services.reassessment_service import set_next_review_date
from app.services import workflow
from app.services.amendment import inputs_changed_since_return

router = APIRouter(prefix="/api/assessments", tags=["Approvals"])

# AW = hierarchical approval workflow (AW.1-AW.7). A project requirement
# outside the brief's stage numbering; it was originally labelled R17,
# which collided with the brief's Stage 17 (Reporting and Monitoring).
#
# AW: statuses that replace the tail of the old 9-stage pipeline
# (previously HUMAN_REVIEW -> COMMITTEE_DECISION -> AUDIT/REJECTED).
STATUS_SUBMITTED_TO_MANAGER = "SUBMITTED_TO_MANAGER"
STATUS_RETURNED_BY_MANAGER = "RETURNED_BY_MANAGER"
STATUS_MANAGER_REJECTED = "MANAGER_REJECTED"
STATUS_READY_FOR_COMMITTEE = "READY_FOR_COMMITTEE"
STATUS_APPROVED = "APPROVED"
STATUS_APPROVED_WITH_CONDITIONS = "APPROVED_WITH_CONDITIONS"
STATUS_DEFERRED = "DEFERRED"
STATUS_REJECTED = "REJECTED"

# Statuses from which a Business User may (re)submit to their manager.
SUBMITTABLE_STATUSES = {"HUMAN_REVIEW", STATUS_RETURNED_BY_MANAGER}

# Who may update a committee condition's tracking fields (owner, due date,
# priority, working status).
CONDITION_TRACKING_ROLES = {
    UserRole.FCRM_ANALYST.value,
    UserRole.MANAGER.value,
    UserRole.COMMITTEE_MEMBER.value,
    UserRole.ADMIN.value,
}

COMMITTEE_VISIBLE_STATUSES = {
    STATUS_READY_FOR_COMMITTEE,
    workflow.STATUS_COMMITTEE_REVIEW,
    workflow.STATUS_CLOSED,
    STATUS_DEFERRED,
    STATUS_APPROVED,
    STATUS_APPROVED_WITH_CONDITIONS,
    STATUS_REJECTED,
}


def _get_assessment_or_404(db: Session, assessment_id: int) -> Assessment:
    assessment = db.query(Assessment).filter(Assessment.id == assessment_id).first()
    if not assessment:
        raise HTTPException(status_code=404, detail="Assessment not found")
    return assessment


def _open_committee_review(
    db: Session,
    assessment: Assessment,
    user: User,
    reason: str,
    authority: Authority | None = None,
) -> None:
    """
    READY_FOR_COMMITTEE / DEFERRED -> COMMITTEE_REVIEW, if not already there.
    `authority` is set when a delegate acts for a committee member (AW.7).
    """

    if assessment.status == workflow.STATUS_COMMITTEE_REVIEW:
        return

    previous_status = assessment.status
    workflow.transition(
        db,
        assessment,
        workflow.STATUS_COMMITTEE_REVIEW,
        user=user,
        reason=reason,
        action="OPEN_COMMITTEE_REVIEW",
        on_behalf_of=_on_behalf_of(authority),
    )
    log_audit_event(
        db=db,
        assessment_id=assessment.id,
        action=AuditAction.COMMITTEE_REVIEW_OPENED,
        previous_status=previous_status,
        new_status=assessment.status,
        actor=authority.describe() if authority else (user.full_name or user.email),
        actor_id=user.id,
        details=reason,
    )


def _on_behalf_of(authority: Authority | None) -> User | None:
    """The delegator, when `authority` comes from a delegation (AW.7)."""

    return authority.acting_for if authority and authority.is_delegated else None


def _authorize_committee(db: Session, user: User, assessment: Assessment, request: Request) -> Authority:
    """R12.5 + R15.2: committee authority and separation of duties for a
    vote or the binding decision. A refusal is recorded (R15.5); so is
    every use of an SoD exception (P3)."""

    try:
        authority = delegation_rules.resolve_committee_authority(db, user, assessment)
        separation_exception = _check_committee_separation(db, assessment, authority)
    except HTTPException as exc:
        if exc.status_code == 403:
            log_denied_attempt(db, user, request, f"committee action refused: {exc.detail}", assessment_id=assessment.id)
        raise

    from app.governance.sod import record_use

    for exception in filter(None, (authority.sod_exception, separation_exception)):
        record_use(db, exception, user, f"{request.method} {request.url.path} on assessment {assessment.id}")
    return authority


def _check_committee_separation(db: Session, assessment: Assessment, authority: Authority):
    """AW.2 / R15.2 separation of duties for committee votes and sign-off.

    Neither the requester nor the manager who approved the assessment may
    vote on or sign off the same assessment -- unless an approved, in-period,
    declared COMMITTEE_SEPARATION exception names that person and this
    assessment (P3). Returns the exception relied on, if any. A delegate
    acting for a conflicted member gets no exception route.
    """

    from app.governance.sod import active_exception
    from app.models.sod_exception import TYPE_COMMITTEE_SEPARATION

    actor = authority.actor
    conflict = None
    if actor.id == assessment.owner_id:
        conflict = "You submitted this assessment and cannot also provide committee sign-off."
    elif actor.id in {assessment.manager_id, assessment.manager_decided_by_id}:
        conflict = "You approved this assessment as its manager and cannot also provide committee sign-off."
    if conflict:
        exception = None if authority.is_delegated else active_exception(db, actor, TYPE_COMMITTEE_SEPARATION, assessment)
        if exception is None:
            raise HTTPException(
                status_code=403,
                detail=conflict + " (An approved, declared SoD exception is the only way to set this aside.)",
            )
        return exception
    if not authority.is_delegated:
        return None

    # AW.7: under a delegation, the member the delegate acts for is bound
    # by the same rules.
    delegator = authority.acting_for
    who = delegator.full_name or delegator.email
    if delegator.id == assessment.owner_id:
        raise HTTPException(
            status_code=403,
            detail=f"{who} submitted this assessment, so it cannot be signed off under this delegation.",
        )
    if delegator.id in {assessment.manager_id, assessment.manager_decided_by_id}:
        raise HTTPException(
            status_code=403,
            detail=(
                f"{who} approved this assessment as its manager, so it cannot also be "
                "signed off under this delegation."
            ),
        )
    return None


@router.post("/{assessment_id}/submit-to-manager", response_model=AssessmentResponse)
def submit_to_manager(
    assessment_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    assessment = _get_assessment_or_404(db, assessment_id)

    if assessment.owner_id != current_user.id:
        raise HTTPException(
            status_code=403,
            detail="Only the assessment's owner can submit it to a manager.",
        )

    if assessment.status not in SUBMITTABLE_STATUSES:
        raise HTTPException(
            status_code=400,
            detail=(
                "Assessment must have completed human review (or been "
                "returned by a manager) before it can be submitted."
            ),
        )

    # A returned assessment whose documents or request details changed since
    # the return no longer rests on the analysis the manager saw: it goes
    # back through analysis instead of straight back to the manager.
    if assessment.status == STATUS_RETURNED_BY_MANAGER:
        amendment = inputs_changed_since_return(db, assessment)
        if amendment["requires_reanalysis"]:
            previous_status = assessment.status
            summary = "; ".join(change["description"] for change in amendment["changes"])
            workflow.transition(
                db,
                assessment,
                "EVIDENCE_COLLECTION",
                user=current_user,
                reason=f"Inputs changed after the manager's return, so re-analysis is required: {summary}",
                action="REANALYSIS_REQUIRED",
            )
            log_audit_event(
                db=db,
                assessment_id=assessment.id,
                action=AuditAction.REANALYSIS_REQUIRED,
                previous_status=previous_status,
                new_status=assessment.status,
                actor=current_user.full_name or current_user.email,
                actor_id=current_user.id,
                details=f"Resubmitted after a manager return with changed inputs; sent back through analysis. {summary}",
            )
            db.commit()
            db.refresh(assessment)
            return assessment

    if not current_user.manager_id:
        raise HTTPException(
            status_code=400,
            detail="You have no assigned manager. Contact an administrator.",
        )

    previous_status = assessment.status
    assessment.manager_id = current_user.manager_id
    workflow.transition(
        db,
        assessment,
        STATUS_SUBMITTED_TO_MANAGER,
        user=current_user,
        reason="Submitted to assigned manager for challenge review.",
        action="SUBMIT_TO_MANAGER",
    )

    log_audit_event(
        db=db,
        assessment_id=assessment.id,
        action=AuditAction.SUBMITTED_TO_MANAGER,
        previous_status=previous_status,
        new_status=assessment.status,
        actor=current_user.full_name or current_user.email,
        actor_id=current_user.id,
        details="Assessment submitted to assigned manager for review.",
    )

    db.commit()
    db.refresh(assessment)
    return assessment


class AmendmentChange(BaseModel):
    kind: str
    description: str


class AmendmentStatusResponse(BaseModel):
    # True when resubmitting would send the assessment back through analysis.
    requires_reanalysis: bool
    since: datetime | None
    changes: list[AmendmentChange]


@router.get("/{assessment_id}/amendment-status", response_model=AmendmentStatusResponse)
def amendment_status(assessment_id: int, db: Session = Depends(get_db)):
    """What changed since the manager returned the assessment, and whether
    resubmitting will therefore send it back through analysis."""

    return inputs_changed_since_return(db, _get_assessment_or_404(db, assessment_id))


class ManagerDecisionRequest(BaseModel):
    decision: str
    comment: str | None = None

    @field_validator("decision")
    @classmethod
    def decision_must_be_known(cls, value: str) -> str:
        if value not in {"approve", "reject", "return"}:
            raise ValueError("decision must be one of: approve, reject, return")
        return value


@router.post("/{assessment_id}/manager-decision", response_model=AssessmentResponse)
def manager_decision(
    assessment_id: int,
    payload: ManagerDecisionRequest,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    assessment = _get_assessment_or_404(db, assessment_id)

    # The assigned manager, or a delegate acting for them within an
    # active, in-scope delegation (AW.7). Raises 403 otherwise -- for an
    # Admin too: Admin is not a substitute for the manager. Refusals are
    # recorded (R15.5).
    try:
        authority = delegation_rules.resolve_manager_authority(db, current_user, assessment)
    except HTTPException as exc:
        if exc.status_code == 403:
            log_denied_attempt(db, current_user, request, f"manager decision refused: {exc.detail}", assessment_id=assessment.id)
        raise

    # AW.2: a user cannot approve their own submission.
    if current_user.id == assessment.owner_id:
        log_denied_attempt(db, current_user, request, "manager decision refused: own submission", assessment_id=assessment.id)
        raise HTTPException(
            status_code=403,
            detail="You cannot approve or reject your own submission.",
        )

    if assessment.status != STATUS_SUBMITTED_TO_MANAGER:
        raise HTTPException(
            status_code=400,
            detail="Assessment must be Submitted to Manager before a decision can be made.",
        )

    if payload.decision in {"reject", "return"} and not (payload.comment or "").strip():
        raise HTTPException(
            status_code=422,
            detail="A comment is required when rejecting or returning an assessment.",
        )

    # R10 acceptance criteria: an unresolved FCRM analyst review comment
    # blocks proceeding to final approval unless an authorized user has
    # explicitly accepted the exception (comment.exception_reason set).
    if payload.decision == "approve":
        unresolved_comments = [
            comment
            for comment in db.query(AssessmentComment)
            .filter(
                AssessmentComment.assessment_id == assessment.id,
                AssessmentComment.resolved.is_(False),
            )
            .all()
            if not (comment.exception_reason or "").strip()
        ]
        if unresolved_comments:
            raise HTTPException(
                status_code=400,
                detail=(
                    f"{len(unresolved_comments)} unresolved analyst comment(s) must be "
                    "resolved, or have an exception explicitly accepted via PATCH "
                    "/api/assessments/{assessment_id}/comments/{comment_id}/resolve, "
                    "before this assessment can proceed to final approval."
                ),
            )

    # R11.6/AC4: an unresolved high-severity challenge finding blocks
    # submission to committee. Recompute first so a finding fixed just now
    # (e.g. evidence uploaded, a control assessed) is reflected immediately
    # rather than requiring a separate GET first.
    if payload.decision == "approve":
        # A provisional inherent-risk result (an applicable factor still
        # unrated -- including one the AI found no verified evidence for)
        # cannot be approved. The stage gate normally stops this earlier;
        # this holds even if factors changed after that gate was passed.
        from app.models.inherent_risk_calculation import InherentRiskCalculation

        current_calc = (
            db.query(InherentRiskCalculation)
            .filter(
                InherentRiskCalculation.assessment_id == assessment.id,
                InherentRiskCalculation.is_current.is_(True),
            )
            .first()
        )
        if current_calc is not None and current_calc.is_provisional and not current_calc.overridden:
            raise HTTPException(
                status_code=400,
                detail=(
                    "The inherent-risk result is provisional: one or more "
                    "applicable risk factors are unrated or unresolved. Rate "
                    "or exclude them (with a reason), or record a documented "
                    "override, before approving."
                ),
            )

        recompute_challenge_review(db, assessment.id)
        db.flush()
        if has_blocking_findings(db, assessment.id):
            raise HTTPException(
                status_code=400,
                detail=(
                    "This assessment has unresolved high/critical challenge "
                    "findings. They can't be accepted: resolve them via "
                    "PATCH /api/assessments/{assessment_id}/challenge-findings/"
                    "{finding_id}/resolve before submitting to committee."
                ),
            )

    previous_status = assessment.status

    decision_map = {
        "approve": (STATUS_READY_FOR_COMMITTEE, AuditAction.MANAGER_APPROVED),
        "reject": (STATUS_MANAGER_REJECTED, AuditAction.MANAGER_REJECTED),
        "return": (STATUS_RETURNED_BY_MANAGER, AuditAction.MANAGER_RETURNED),
    }
    new_status, action = decision_map[payload.decision]

    workflow.transition(
        db,
        assessment,
        new_status,
        user=current_user,
        reason=payload.comment or f"Manager decision: {payload.decision}.",
        action="MANAGER_DECISION",
        on_behalf_of=_on_behalf_of(authority),
    )
    if payload.decision in {"reject", "return"}:
        # R11: a returned or rejected assessment must be challenged again
        # when it comes back; the earlier sign-off stays on the record.
        from app.services.challenge_signoff import supersede_current

        if supersede_current(db, assessment.id, f"Assessment {payload.decision}ed by the manager."):
            log_audit_event(
                db=db,
                assessment_id=assessment.id,
                action=AuditAction.CHALLENGE_REVIEW_SIGNOFF_SUPERSEDED,
                actor=authority.describe(),
                actor_id=current_user.id,
                details=f"Challenge-review sign-off superseded: assessment {payload.decision}ed by the manager.",
            )
    assessment.manager_decision = payload.decision.upper()
    # AW.7: decided_by is whoever acted; on_behalf_of is the manager
    # whose authority a delegate used, so the record names both.
    assessment.manager_decided_by_id = current_user.id
    assessment.manager_decided_on_behalf_of_id = authority.acting_for.id if authority.is_delegated else None
    assessment.manager_delegation_id = authority.delegation.id if authority.is_delegated else None
    assessment.manager_decided_at = datetime.now(timezone.utc)
    assessment.manager_comment = payload.comment

    log_audit_event(
        db=db,
        assessment_id=assessment.id,
        action=action,
        previous_status=previous_status,
        new_status=new_status,
        actor=authority.describe(),
        actor_id=current_user.id,
        details=payload.comment or f"Manager decision: {payload.decision}.",
    )

    db.commit()
    db.refresh(assessment)
    return assessment


@router.post("/{assessment_id}/committee-decision", response_model=AssessmentResponse)
def committee_decision(
    assessment_id: int,
    payload: CommitteeDecisionRequestV2,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Stage 12 (R12.1, R12.3, R12.5): the committee's final, binding
    decision. R12.5: only a Committee Member may call this (an Admin only
    if COMMITTEE_ADMIN_AUTHORITY is switched on -- Q-6),
    or a delegate acting for a Committee Member under an active,
    in-scope delegation (AW.7).
    """

    assessment = _get_assessment_or_404(db, assessment_id)

    authority = _authorize_committee(db, current_user, assessment, request)

    # AW.3/AW.4: committee can only act once the manager has approved
    # (READY_FOR_COMMITTEE) -- or on a previously DEFERRED assessment.
    if assessment.status not in {
        STATUS_READY_FOR_COMMITTEE,
        workflow.STATUS_COMMITTEE_REVIEW,
        STATUS_DEFERRED,
    }:
        raise HTTPException(
            status_code=400,
            detail="Assessment must be manager-approved (Ready for Committee) before committee sign-off.",
        )

    # An approval must be reproducible: methodology version, reference
    # data, ratings, evidence, rules, controls and residual all on record.
    approving = payload.decision in {"approve", "approve_with_conditions"}
    if approving:
        missing = missing_requirements(db, assessment)
        if missing:
            raise HTTPException(
                status_code=400,
                detail={
                    "message": (
                        "This assessment cannot be approved until its decision "
                        "record is complete."
                    ),
                    "missing": missing,
                },
            )

    # P3 (R-GOV-04): a final decision (approve, approve with conditions,
    # reject) needs the same governance readiness as committee submission
    # -- re-checked here, because things can change while the committee
    # sits. Deferral is not final and stays possible.
    if payload.decision in {"approve", "approve_with_conditions", "reject"}:
        from app.governance.readiness import FINAL_DECISION, evaluate, log_block

        readiness = evaluate(db, assessment, FINAL_DECISION)
        if not readiness["ready"]:
            log_block(assessment.id, current_user, FINAL_DECISION, readiness["blockers"])
            raise HTTPException(
                status_code=409,
                detail={
                    "message": "The committee decision is blocked by unresolved governance requirements.",
                    "issues": [b["message"] for b in readiness["blockers"]],
                    "blockers": readiness["blockers"],
                },
            )

    # Stage 14: a decision is only ever made from Committee Review. If the
    # committee decides straight from Ready for Committee / Deferred, the
    # review is opened implicitly first (recorded as its own transition,
    # and subject to the "no unresolved mandatory issues" gate).
    _open_committee_review(
        db, assessment, current_user, "Committee review opened for decision.", authority
    )

    previous_status = assessment.status

    decision_map = {
        "approve": (STATUS_APPROVED, AuditAction.COMMITTEE_APPROVED),
        "approve_with_conditions": (
            STATUS_APPROVED_WITH_CONDITIONS,
            AuditAction.COMMITTEE_APPROVED_WITH_CONDITIONS,
        ),
        "defer": (STATUS_DEFERRED, AuditAction.COMMITTEE_DEFERRED),
        "reject": (STATUS_REJECTED, AuditAction.COMMITTEE_REJECTED),
    }
    new_status, action = decision_map[payload.decision]

    workflow.transition(
        db,
        assessment,
        new_status,
        user=current_user,
        reason=payload.rationale,
        action="COMMITTEE_DECISION",
        on_behalf_of=_on_behalf_of(authority),
    )
    assessment.committee_decision = new_status
    # AW.7: as for the manager decision.
    assessment.committee_decided_by_id = current_user.id
    assessment.committee_decided_on_behalf_of_id = authority.acting_for.id if authority.is_delegated else None
    assessment.committee_delegation_id = authority.delegation.id if authority.is_delegated else None
    assessment.committee_decided_at = datetime.now(timezone.utc)
    assessment.committee_rationale = payload.rationale

    # Stage 18 (R18.1): a fresh approval starts a new review-validity
    # window, so EXPIRY/PERIODIC_REVIEW triggers fire again next cycle.
    if new_status in {STATUS_APPROVED, STATUS_APPROVED_WITH_CONDITIONS}:
        set_next_review_date(assessment)

    actor_name = current_user.full_name or current_user.email

    # R12.4: persist each structured condition as its own tracked row.
    # `committee_conditions` (free text) is kept in sync as a plain-text
    # summary for any older reader of that column.
    if payload.decision == "approve_with_conditions":
        for condition in payload.structured_conditions:
            committee_condition = CommitteeCondition(
                assessment_id=assessment.id,
                description=condition.description,
                owner=condition.owner,
                due_date=condition.due_date,
                priority=condition.priority,
                created_by=actor_name,
            )
            db.add(committee_condition)
            # R13.1/AC1: a committee condition automatically becomes a
            # trackable action item.
            db.flush()
            db.add(
                ActionItem(
                    assessment_id=assessment.id,
                    source_type=ActionItemSourceType.COMMITTEE_CONDITION.value,
                    committee_condition_id=committee_condition.id,
                    title=condition.description[:255],
                    description=condition.description,
                    owner=condition.owner,
                    due_date=condition.due_date,
                    priority=condition.priority,
                    created_by=actor_name,
                )
            )
        assessment.committee_conditions = payload.conditions or "; ".join(
            f"{c.description} (owner: {c.owner}, due {c.due_date})"
            for c in payload.structured_conditions
        )
    else:
        assessment.committee_conditions = None

    log_audit_event(
        db=db,
        assessment_id=assessment.id,
        action=action,
        previous_status=previous_status,
        new_status=new_status,
        actor=authority.describe(),
        actor_id=current_user.id,
        details=payload.rationale,
    )

    if approving:
        record = freeze_decision_record(
            db,
            assessment,
            {
                "decision": new_status,
                "decided_by": actor_name,
                "decided_by_id": current_user.id,
                "on_behalf_of_id": assessment.committee_decided_on_behalf_of_id,
                "delegation_id": assessment.committee_delegation_id,
                "decided_at": assessment.committee_decided_at,
                "rationale": payload.rationale,
                "conditions": assessment.committee_conditions,
                "manager_decided_by_id": assessment.manager_decided_by_id,
                "manager_decided_at": assessment.manager_decided_at,
            },
        )
        log_audit_event(
            db=db,
            assessment_id=assessment.id,
            action=AuditAction.DECISION_RECORD_FROZEN,
            actor=actor_name,
            actor_id=current_user.id,
            details=f"Decision record {record.id} frozen ({record.checksum[:19]}...).",
        )

    db.commit()
    db.refresh(assessment)
    return assessment


@router.get("/{assessment_id}/decision-record")
def get_decision_record(
    assessment_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """The latest frozen decision record, and whether it is intact."""

    _get_assessment_or_404(db, assessment_id)
    row = (
        db.query(DecisionRecord)
        .filter(DecisionRecord.assessment_id == assessment_id)
        .order_by(DecisionRecord.id.desc())
        .first()
    )
    if row is None:
        raise HTTPException(status_code=404, detail="No decision record has been frozen yet.")
    return {
        "id": row.id,
        "decision": row.decision,
        "decided_by": row.decided_by,
        "decided_at": row.decided_at,
        "checksum": row.checksum,
        "intact": verify_record(row),
        "record": row.get_record(),
    }


@router.get("/{assessment_id}/decision-record/readiness")
def get_decision_record_readiness(
    assessment_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """What is still missing before this assessment could be approved."""

    assessment = _get_assessment_or_404(db, assessment_id)
    missing = missing_requirements(db, assessment)
    return {"ready": not missing, "missing": missing}


def _visible_comment_visibilities(current_user: User) -> set[str]:
    if current_user.role in {UserRole.COMMITTEE_MEMBER.value, UserRole.ADMIN.value}:
        return {v.value for v in CommentVisibility}
    if current_user.role == UserRole.MANAGER.value:
        return {CommentVisibility.ALL.value, CommentVisibility.MANAGER_AND_ABOVE.value}
    return {CommentVisibility.ALL.value}


@router.post("/{assessment_id}/comments", response_model=CommentResponse, status_code=201)
def post_comment(
    assessment_id: int,
    payload: CommentCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    assessment = _get_assessment_or_404(db, assessment_id)

    if current_user.role == UserRole.BUSINESS_USER.value and payload.visibility != CommentVisibility.ALL.value:
        raise HTTPException(
            status_code=403,
            detail="Business Users can only post comments visible to everyone.",
        )

    comment = AssessmentComment(
        assessment_id=assessment.id,
        author_id=current_user.id,
        body=payload.body,
        visibility=payload.visibility,
        section=payload.section,
        related_entity_id=payload.related_entity_id,
    )
    db.add(comment)

    log_audit_event(
        db=db,
        assessment_id=assessment.id,
        action=AuditAction.COMMENT_ADDED,
        actor=current_user.full_name or current_user.email,
        actor_id=current_user.id,
        details=payload.body,
    )

    db.commit()
    db.refresh(comment)

    return CommentResponse(
        id=comment.id,
        assessment_id=comment.assessment_id,
        author_id=comment.author_id,
        author_name=current_user.full_name or current_user.email,
        author_role=current_user.role,
        body=comment.body,
        visibility=comment.visibility,
        section=comment.section,
        related_entity_id=comment.related_entity_id,
        resolved=comment.resolved,
        resolved_by=comment.resolved_by,
        resolved_at=comment.resolved_at,
        exception_reason=comment.exception_reason,
        created_at=comment.created_at,
    )


@router.patch("/{assessment_id}/comments/{comment_id}/resolve", response_model=CommentResponse)
def resolve_comment(
    assessment_id: int,
    comment_id: int,
    payload: CommentResolve,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    _get_assessment_or_404(db, assessment_id)

    comment = (
        db.query(AssessmentComment)
        .filter(
            AssessmentComment.id == comment_id,
            AssessmentComment.assessment_id == assessment_id,
        )
        .first()
    )
    if not comment:
        raise HTTPException(status_code=404, detail="Comment not found")

    # R10 acceptance criteria: only an authorized reviewer may explicitly
    # accept the exception of leaving a comment unresolved.
    if (payload.exception_reason or "").strip() and current_user.role == UserRole.BUSINESS_USER.value:
        raise HTTPException(
            status_code=403,
            detail="Only an authorized reviewer can accept an exception on an unresolved comment.",
        )

    comment.resolved = payload.resolved
    comment.resolved_by = current_user.full_name or current_user.email
    comment.resolved_at = datetime.now(timezone.utc)
    comment.exception_reason = payload.exception_reason

    log_audit_event(
        db=db,
        assessment_id=assessment_id,
        action=(
            AuditAction.COMMENT_EXCEPTION_ACCEPTED
            if (payload.exception_reason or "").strip()
            else AuditAction.COMMENT_RESOLVED
        ),
        actor=current_user.full_name or current_user.email,
        actor_id=current_user.id,
        details=payload.exception_reason or "Comment marked resolved.",
    )

    db.commit()
    db.refresh(comment)

    author = db.query(User).filter(User.id == comment.author_id).first()

    return CommentResponse(
        id=comment.id,
        assessment_id=comment.assessment_id,
        author_id=comment.author_id,
        author_name=(author.full_name or author.email) if author else None,
        author_role=author.role if author else None,
        body=comment.body,
        visibility=comment.visibility,
        section=comment.section,
        related_entity_id=comment.related_entity_id,
        resolved=comment.resolved,
        resolved_by=comment.resolved_by,
        resolved_at=comment.resolved_at,
        exception_reason=comment.exception_reason,
        created_at=comment.created_at,
    )


@router.get("/{assessment_id}/comments", response_model=list[CommentResponse])
def get_comments(
    assessment_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    _get_assessment_or_404(db, assessment_id)

    allowed = _visible_comment_visibilities(current_user)

    comments = (
        db.query(AssessmentComment)
        .filter(
            AssessmentComment.assessment_id == assessment_id,
            AssessmentComment.visibility.in_(allowed),
        )
        .order_by(AssessmentComment.created_at.asc())
        .all()
    )

    author_ids = {comment.author_id for comment in comments}
    authors = {
        user.id: user
        for user in db.query(User).filter(User.id.in_(author_ids)).all()
    }

    return [
        CommentResponse(
            id=comment.id,
            assessment_id=comment.assessment_id,
            author_id=comment.author_id,
            author_name=(
                authors[comment.author_id].full_name or authors[comment.author_id].email
                if comment.author_id in authors
                else None
            ),
            author_role=authors[comment.author_id].role if comment.author_id in authors else None,
            body=comment.body,
            visibility=comment.visibility,
            section=comment.section,
            related_entity_id=comment.related_entity_id,
            resolved=comment.resolved,
            resolved_by=comment.resolved_by,
            resolved_at=comment.resolved_at,
            exception_reason=comment.exception_reason,
            created_at=comment.created_at,
        )
        for comment in comments
    ]


# ---------------------------------------------------------------------
# Stage 12 (R12.4): structured committee-decision conditions, tracked to
# completion the same way Stage 7's ControlCondition is.
# ---------------------------------------------------------------------


@router.get(
    "/{assessment_id}/committee-conditions",
    response_model=list[CommitteeConditionResponse],
)
def list_committee_conditions(
    assessment_id: int,
    db: Session = Depends(get_db),
):
    _get_assessment_or_404(db, assessment_id)

    return (
        db.query(CommitteeCondition)
        .filter(CommitteeCondition.assessment_id == assessment_id)
        .order_by(CommitteeCondition.created_at.asc())
        .all()
    )


@router.patch(
    "/{assessment_id}/committee-conditions/{condition_id}",
    response_model=CommitteeConditionResponse,
)
def update_committee_condition(
    assessment_id: int,
    condition_id: int,
    payload: CommitteeConditionUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Tracking a condition to completion is explicitly allowed even once
    the assessment itself is read-only (APPROVED_WITH_CONDITIONS is a
    final decision) -- this IS how a condition gets closed out, so it is
    never blocked by ensure_assessment_editable.

    R13.4: a condition is never completed here. It completes when its
    linked action item's closure (completion evidence + reviewer approval)
    is approved -- see app/api/action_items.py.
    """

    if current_user.role not in CONDITION_TRACKING_ROLES:
        raise HTTPException(status_code=403, detail="You are not authorized to update committee conditions.")

    if payload.status == "COMPLETED":
        raise HTTPException(
            status_code=400,
            detail=(
                "A committee condition can't be marked COMPLETED directly. "
                "Request closure on its action item with completion evidence; "
                "the condition completes when a reviewer approves that closure."
            ),
        )

    condition = (
        db.query(CommitteeCondition)
        .filter(
            CommitteeCondition.id == condition_id,
            CommitteeCondition.assessment_id == assessment_id,
        )
        .first()
    )

    if not condition:
        raise HTTPException(status_code=404, detail="Committee condition not found")

    if payload.status is not None and condition.status == "COMPLETED":
        raise HTTPException(status_code=400, detail="This committee condition is already completed.")

    if payload.status is not None:
        condition.status = payload.status
    if payload.completion_evidence is not None:
        condition.completion_evidence = payload.completion_evidence
    if payload.owner is not None:
        condition.owner = payload.owner
    if payload.due_date is not None:
        condition.due_date = payload.due_date
    if payload.priority is not None:
        condition.priority = payload.priority

    log_audit_event(
        db=db,
        assessment_id=assessment_id,
        action=AuditAction.COMMITTEE_CONDITION_UPDATED,
        actor=current_user.full_name or current_user.email,
        actor_id=current_user.id,
        details=f"Committee condition #{condition_id} updated.",
    )

    db.commit()
    db.refresh(condition)

    return condition


# ---------------------------------------------------------------------
# Stage 12 (R12.6): individual committee member votes -- approval,
# dissent, or abstention -- recorded alongside (not instead of) the one
# binding decision above.
# ---------------------------------------------------------------------


@router.post(
    "/{assessment_id}/committee-votes",
    response_model=CommitteeVoteResponse,
)
def cast_committee_vote(
    assessment_id: int,
    payload: CommitteeVoteCreate,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    assessment = _get_assessment_or_404(db, assessment_id)

    authority = _authorize_committee(db, current_user, assessment, request)

    if assessment.status not in {
        STATUS_READY_FOR_COMMITTEE,
        workflow.STATUS_COMMITTEE_REVIEW,
        STATUS_DEFERRED,
    }:
        raise HTTPException(
            status_code=400,
            detail="Votes can only be recorded while the assessment is before the committee.",
        )

    # Stage 14: the first vote opens Committee Review.
    _open_committee_review(
        db, assessment, current_user, "Committee review opened by first vote.", authority
    )

    # AW.7: a delegate's vote fills the seat of the member they act for.
    seat = authority.acting_for
    delegate = current_user if authority.is_delegated else None

    # R12.6: votes are append-only. A re-cast supersedes the seat's
    # current vote; the earlier vote stays on the record unchanged.
    previous = (
        db.query(CommitteeVote)
        .filter(
            CommitteeVote.assessment_id == assessment_id,
            CommitteeVote.member_id == seat.id,
            CommitteeVote.is_current.is_(True),
        )
        .with_for_update()
        .first()
    )
    recast_reason = (payload.recast_reason or "").strip()
    if previous is not None and not recast_reason:
        raise HTTPException(
            status_code=422,
            detail=(
                f"This seat already voted {previous.vote}. Give a reason for changing the "
                "vote (recast_reason); the earlier vote is kept on the record."
            ),
        )

    now = datetime.now(timezone.utc)
    vote = CommitteeVote(
        assessment_id=assessment_id,
        member_id=seat.id,
        member_name=seat.full_name or seat.email,
        vote=payload.vote,
        comment=payload.comment,
        version=(previous.version + 1) if previous else 1,
        is_current=True,
        cast_by_id=current_user.id,
        recast_reason=recast_reason or None,
        delegate_id=delegate.id if delegate else None,
        delegate_name=(delegate.full_name or delegate.email) if delegate else None,
        delegation_id=authority.delegation.id if authority.is_delegated else None,
        voted_at=now,
    )
    if previous is not None:
        # Retire the old vote first so the one-current-vote-per-seat
        # index never sees two current rows.
        previous.is_current = False
        previous.superseded_at = now
        db.flush()
    db.add(vote)
    db.flush()
    if previous is not None:
        previous.superseded_by_id = vote.id

    details = f"Committee vote v{vote.version}: {payload.vote}"
    if previous is not None:
        details += f" (replaces v{previous.version}: {previous.vote}; reason: {recast_reason})"
    if payload.comment:
        details += f" — {payload.comment}"
    log_audit_event(
        db=db,
        assessment_id=assessment_id,
        action=AuditAction.COMMITTEE_VOTE_CAST,
        actor=authority.describe(),
        actor_id=current_user.id,
        details=details,
    )

    db.commit()
    db.refresh(vote)

    return vote


@router.get(
    "/{assessment_id}/committee-votes",
    response_model=list[CommitteeVoteResponse],
)
def list_committee_votes(
    assessment_id: int,
    include_history: bool = False,
    db: Session = Depends(get_db),
):
    """Each seat's current vote; with include_history, every vote ever
    cast, superseded ones included (R12.6)."""

    _get_assessment_or_404(db, assessment_id)

    query = db.query(CommitteeVote).filter(CommitteeVote.assessment_id == assessment_id)
    if not include_history:
        query = query.filter(CommitteeVote.is_current.is_(True))
    return query.order_by(CommitteeVote.voted_at.asc(), CommitteeVote.id.asc()).all()


# ---------------------------------------------------------------------
# Stage 12 (R12.2): the decision package -- everything the committee
# needs in one call, reusing the Stage 9 assessment draft.
# ---------------------------------------------------------------------


@router.get(
    "/{assessment_id}/decision-package",
    response_model=DecisionPackageResponse,
)
def get_decision_package(
    assessment_id: int,
    db: Session = Depends(get_db),
):
    from app.api.assessments import _build_draft_response, _get_current_draft

    assessment = _get_assessment_or_404(db, assessment_id)

    draft = _get_current_draft(db, assessment_id)
    if not draft:
        # Best-effort: assemble one on the fly so the committee is never
        # shown an empty package just because auto-generation didn't run
        # yet (e.g. an older assessment that predates Stage 9).
        try:
            draft = generate_assessment_draft(db, assessment, requested_by="System")
            db.commit()
        except Exception:
            db.rollback()
            draft = None

    draft_payload = _build_draft_response(draft).model_dump() if draft else None

    findings = (
        db.query(ChallengeFinding)
        .filter(ChallengeFinding.assessment_id == assessment_id)
        .order_by(ChallengeFinding.detected_at.desc())
        .all()
    )

    conditions = (
        db.query(CommitteeCondition)
        .filter(CommitteeCondition.assessment_id == assessment_id)
        .order_by(CommitteeCondition.created_at.asc())
        .all()
    )

    votes = (
        db.query(CommitteeVote)
        .filter(CommitteeVote.assessment_id == assessment_id)
        .order_by(CommitteeVote.voted_at.asc())
        .all()
    )

    open_issues = list(draft.get_missing_information()) if draft else []
    open_issues += [
        f"Open challenge finding ({finding.severity}): {finding.description}"
        for finding in findings
        if finding.resolution_status == "OPEN"
    ]

    history = (
        db.query(AuditEvent)
        .filter(AuditEvent.assessment_id == assessment_id)
        .order_by(AuditEvent.created_at.asc())
        .all()
    )

    from app.services.inherent_risk_service import current_factors

    rated = [
        factor
        for factor in current_factors(db, assessment)
        if factor.applicable and not factor.excluded and factor.likelihood is not None
    ]
    main_risk_drivers = [
        {
            "category": factor.category,
            "score": factor.score,
            "severity": factor.severity,
            "likelihood": factor.likelihood,
            "impact": factor.impact,
            "rationale": factor.rationale,
        }
        for factor in sorted(rated, key=lambda f: f.score or 0, reverse=True)[:5]
    ]

    from app.services.challenge_signoff import current_signoff, signoff_payload
    from app.services.override_ledger import value_comparisons

    signoff = current_signoff(db, assessment_id)

    return DecisionPackageResponse(
        value_comparisons=value_comparisons(db, assessment),
        challenge_signoff=signoff_payload(signoff) if signoff else None,
        main_risk_drivers=main_risk_drivers,
        assessment_id=assessment_id,
        draft=draft_payload,
        challenge_findings=[
            ChallengeFindingResponse.model_validate(finding).model_dump()
            for finding in findings
        ],
        committee_conditions=conditions,
        committee_votes=votes,
        open_issues=open_issues,
        assessment_history=[
            {
                "action": event.action,
                "previous_status": event.previous_status,
                "new_status": event.new_status,
                "actor": event.actor,
                "details": event.details,
                "created_at": event.created_at.isoformat(),
            }
            for event in history
        ],
    )


# ---------------------------------------------------------------------
# Controlled amendment: the only way to edit a finally-decided
# assessment (see app/services/decision_lock.py).
# ---------------------------------------------------------------------


@router.post("/{assessment_id}/amend", response_model=AssessmentResponse)
def open_amendment(
    assessment_id: int,
    payload: AssessmentAmendmentRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    assessment = _get_assessment_or_404(db, assessment_id)

    if current_user.role not in {UserRole.COMMITTEE_MEMBER.value, UserRole.ADMIN.value}:
        raise HTTPException(
            status_code=403,
            detail="Only a Committee Member or Admin can open a controlled amendment.",
        )

    from app.services.decision_lock import FINAL_DECISION_STATUSES

    if assessment.status not in FINAL_DECISION_STATUSES:
        raise HTTPException(
            status_code=400,
            detail="This assessment does not have a final decision to amend.",
        )

    previous_status = assessment.status
    workflow.transition(
        db,
        assessment,
        "REMEDIATION",
        user=current_user,
        reason=f"Controlled amendment opened: {payload.reason}",
        action="AMEND",
    )

    log_audit_event(
        db=db,
        assessment_id=assessment_id,
        action=AuditAction.ASSESSMENT_AMENDMENT_OPENED,
        previous_status=previous_status,
        new_status="REMEDIATION",
        actor=current_user.full_name or current_user.email,
        actor_id=current_user.id,
        details=f"Controlled amendment opened on a {previous_status} decision. Reason: {payload.reason}",
    )

    db.commit()
    db.refresh(assessment)

    return assessment

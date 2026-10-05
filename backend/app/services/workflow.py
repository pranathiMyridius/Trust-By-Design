"""
Stage 14: Workflow and Status Management.

The single place an assessment's status is allowed to change. Every
endpoint that moves an assessment (advance-stage, analyze, request /
provide information, submit-to-manager, manager / committee decisions,
amendment, close, ...) calls `transition()` (pipeline status change) or
`sync_workflow_status()` (lifecycle-only change, e.g. a draft being
submitted) instead of assigning `assessment.status` itself.

Two layers of status:

  * `Assessment.status` -- the internal pipeline stage the rest of the
    backend already keys off (INTAKE, EVIDENCE_COLLECTION, ...,
    SUBMITTED_TO_MANAGER, READY_FOR_COMMITTEE, APPROVED, ...).
  * `Assessment.workflow_status` -- the Stage 14 business lifecycle
    (DRAFT, SUBMITTED, INTAKE_VALIDATION, ..., CLOSED), always *derived*
    from the pipeline status (see derive_workflow_status) so the two can
    never drift apart.

R14.1 is enforced on the pipeline layer (it is strictly finer-grained --
e.g. it also stops CONTROL_ASSESSMENT jumping straight to HUMAN_REVIEW,
which the lifecycle layer alone would see as RISK_ASSESSMENT_IN_PROGRESS
-> ANALYST_REVIEW and allow). Every pipeline transition also implies a
permitted lifecycle transition, so the lifecycle-level rules hold too
(e.g. DRAFT can never reach APPROVED directly).
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from enum import Enum

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.models.assessment import Assessment
from app.models.user import User, UserRole
from app.models.workflow_transition import WorkflowTransition
from app.services.audit_service import AuditAction, log_audit_event


# ---------------------------------------------------------------------------
# Lifecycle statuses
# ---------------------------------------------------------------------------


class WorkflowStatus(str, Enum):
    DRAFT = "DRAFT"
    SUBMITTED = "SUBMITTED"
    INTAKE_VALIDATION = "INTAKE_VALIDATION"
    INFORMATION_REQUESTED = "INFORMATION_REQUESTED"
    EVIDENCE_REVIEW = "EVIDENCE_REVIEW"
    RISK_ASSESSMENT_IN_PROGRESS = "RISK_ASSESSMENT_IN_PROGRESS"
    ANALYST_REVIEW = "ANALYST_REVIEW"
    CHALLENGE_REVIEW = "CHALLENGE_REVIEW"
    READY_FOR_COMMITTEE = "READY_FOR_COMMITTEE"
    COMMITTEE_REVIEW = "COMMITTEE_REVIEW"
    APPROVED = "APPROVED"
    APPROVED_WITH_CONDITIONS = "APPROVED_WITH_CONDITIONS"
    DEFERRED = "DEFERRED"
    REJECTED = "REJECTED"
    CLOSED = "CLOSED"
    AMENDMENT_REQUIRED = "AMENDMENT_REQUIRED"


WORKFLOW_STATUS_LABELS = {
    WorkflowStatus.DRAFT: "Draft",
    WorkflowStatus.SUBMITTED: "Submitted",
    WorkflowStatus.INTAKE_VALIDATION: "Intake Validation",
    WorkflowStatus.INFORMATION_REQUESTED: "Information Requested",
    WorkflowStatus.EVIDENCE_REVIEW: "Evidence Review",
    WorkflowStatus.RISK_ASSESSMENT_IN_PROGRESS: "Risk Assessment in Progress",
    WorkflowStatus.ANALYST_REVIEW: "Analyst Review",
    WorkflowStatus.CHALLENGE_REVIEW: "Challenge Review",
    WorkflowStatus.READY_FOR_COMMITTEE: "Ready for Committee",
    WorkflowStatus.COMMITTEE_REVIEW: "Committee Review",
    WorkflowStatus.APPROVED: "Approved",
    WorkflowStatus.APPROVED_WITH_CONDITIONS: "Approved with Conditions",
    WorkflowStatus.DEFERRED: "Deferred",
    WorkflowStatus.REJECTED: "Rejected",
    WorkflowStatus.CLOSED: "Closed",
    WorkflowStatus.AMENDMENT_REQUIRED: "Amendment Required",
}

# Lifecycle statuses where nothing further is expected of anyone except
# closing out -- no SLA clock, no escalation, not "active".
DECIDED_WORKFLOW_STATUSES = {
    WorkflowStatus.APPROVED.value,
    WorkflowStatus.APPROVED_WITH_CONDITIONS.value,
    WorkflowStatus.REJECTED.value,
}
TERMINAL_WORKFLOW_STATUSES = {WorkflowStatus.CLOSED.value}


# ---------------------------------------------------------------------------
# Pipeline statuses (the values Assessment.status actually takes)
# ---------------------------------------------------------------------------

# New pipeline statuses introduced by Stage 14. Everything else already
# existed (see api/assessments.py STAGE_ORDER and api/approvals.py).
STATUS_COMMITTEE_REVIEW = "COMMITTEE_REVIEW"
STATUS_CLOSED = "CLOSED"

RISK_ASSESSMENT_STAGES = {
    "RISK_IDENTIFICATION",
    "INHERENT_RISK_ASSESSMENT",
    "CONTROL_ASSESSMENT",
    "RESIDUAL_RISK",
}

_PIPELINE_TO_WORKFLOW = {
    "RISK_IDENTIFICATION": WorkflowStatus.RISK_ASSESSMENT_IN_PROGRESS,
    "INHERENT_RISK_ASSESSMENT": WorkflowStatus.RISK_ASSESSMENT_IN_PROGRESS,
    "CONTROL_ASSESSMENT": WorkflowStatus.RISK_ASSESSMENT_IN_PROGRESS,
    "RESIDUAL_RISK": WorkflowStatus.RISK_ASSESSMENT_IN_PROGRESS,
    "HUMAN_REVIEW": WorkflowStatus.ANALYST_REVIEW,
    # The line manager's review is the second-line challenge of the
    # analyst's work (R11 challenge findings gate its "approve").
    "SUBMITTED_TO_MANAGER": WorkflowStatus.CHALLENGE_REVIEW,
    "RETURNED_BY_MANAGER": WorkflowStatus.AMENDMENT_REQUIRED,
    "REMEDIATION": WorkflowStatus.AMENDMENT_REQUIRED,
    "INFORMATION_REQUESTED": WorkflowStatus.INFORMATION_REQUESTED,
    "READY_FOR_COMMITTEE": WorkflowStatus.READY_FOR_COMMITTEE,
    STATUS_COMMITTEE_REVIEW: WorkflowStatus.COMMITTEE_REVIEW,
    "APPROVED": WorkflowStatus.APPROVED,
    "APPROVED_WITH_CONDITIONS": WorkflowStatus.APPROVED_WITH_CONDITIONS,
    "DEFERRED": WorkflowStatus.DEFERRED,
    "REJECTED": WorkflowStatus.REJECTED,
    "MANAGER_REJECTED": WorkflowStatus.REJECTED,
    STATUS_CLOSED: WorkflowStatus.CLOSED,
    # Legacy values from before the stage pipeline / AW existed.
    "UNDER_REVIEW": WorkflowStatus.ANALYST_REVIEW,
    "READY_FOR_REVIEW": WorkflowStatus.ANALYST_REVIEW,
    "COMMITTEE_DECISION": WorkflowStatus.COMMITTEE_REVIEW,
    "AUDIT": WorkflowStatus.CLOSED,
}


def _profile_confirmed(db: Session, assessment_id: int) -> bool:
    from app.models.assessment_intelligence import AssessmentIntelligence

    intelligence = (
        db.query(AssessmentIntelligence)
        .filter(AssessmentIntelligence.assessment_id == assessment_id)
        .first()
    )
    return bool(intelligence and intelligence.confirmed)


def derive_workflow_status(db: Session, assessment: Assessment) -> str:
    status = assessment.status

    if status == "INTAKE":
        return (
            WorkflowStatus.DRAFT.value
            if assessment.is_draft
            else WorkflowStatus.SUBMITTED.value
        )

    if status == "EVIDENCE_COLLECTION":
        # R3 (Intake Validation and Structuring): until the business owner
        # has confirmed the structured profile the case is still being
        # validated; after that, it's evidence being reviewed.
        return (
            WorkflowStatus.EVIDENCE_REVIEW.value
            if _profile_confirmed(db, assessment.id)
            else WorkflowStatus.INTAKE_VALIDATION.value
        )

    return _PIPELINE_TO_WORKFLOW.get(status, WorkflowStatus.ANALYST_REVIEW).value


# ---------------------------------------------------------------------------
# R14.1 + R14.2: permitted transitions, and who may make each one.
# ---------------------------------------------------------------------------

# Role tokens beyond real UserRole values: resolved against the
# assessment itself.
OWNER = "OWNER"  # assessment.owner_id == user.id
ASSIGNED_MANAGER = "ASSIGNED_MANAGER"  # assessment.manager_id == user.id

_PIPELINE = {UserRole.FCRM_ANALYST.value, UserRole.MANAGER.value, UserRole.ADMIN.value}
_COMMITTEE = {UserRole.COMMITTEE_MEMBER.value, UserRole.ADMIN.value}
_CLOSERS = {UserRole.FCRM_ANALYST.value, UserRole.COMMITTEE_MEMBER.value, UserRole.ADMIN.value}

_INFO_REQUEST = {"INFORMATION_REQUESTED": _PIPELINE}

_FINAL_DECISION_EXITS = {
    "REMEDIATION": _COMMITTEE,
    STATUS_CLOSED: _CLOSERS,
}

# from pipeline status -> {to pipeline status: roles allowed}
TRANSITIONS: dict[str, dict[str, set[str]]] = {
    "INTAKE": {
        "EVIDENCE_COLLECTION": {OWNER} | _PIPELINE,
        # POST /{id}/analyze runs the risk engine straight from Intake.
        "RISK_IDENTIFICATION": _PIPELINE,
        **_INFO_REQUEST,
        # Withdrawal of a request that's never progressed.
        STATUS_CLOSED: {OWNER, UserRole.ADMIN.value},
    },
    "EVIDENCE_COLLECTION": {
        "RISK_IDENTIFICATION": {OWNER} | _PIPELINE,
        **_INFO_REQUEST,
        STATUS_CLOSED: {UserRole.ADMIN.value},
    },
    "RISK_IDENTIFICATION": {"INHERENT_RISK_ASSESSMENT": _PIPELINE, **_INFO_REQUEST},
    "INHERENT_RISK_ASSESSMENT": {"CONTROL_ASSESSMENT": _PIPELINE, **_INFO_REQUEST},
    "CONTROL_ASSESSMENT": {"RESIDUAL_RISK": _PIPELINE, **_INFO_REQUEST},
    "RESIDUAL_RISK": {"HUMAN_REVIEW": _PIPELINE, **_INFO_REQUEST},
    "HUMAN_REVIEW": {"SUBMITTED_TO_MANAGER": {OWNER}, **_INFO_REQUEST},
    # The manager decision belongs to the assigned manager alone (or a
    # delegate acting on their authority, passed as on_behalf_of). An
    # Admin is not a substitute: that would bypass manager approval and
    # separation of duties (R15.2, R-GOV-02). An absent manager is covered
    # by an audited delegation.
    "SUBMITTED_TO_MANAGER": {
        "READY_FOR_COMMITTEE": {ASSIGNED_MANAGER},
        "RETURNED_BY_MANAGER": {ASSIGNED_MANAGER},
        "MANAGER_REJECTED": {ASSIGNED_MANAGER},
        **_INFO_REQUEST,
    },
    # EVIDENCE_COLLECTION: inputs changed after the return, so the owner's
    # resubmission goes back through analysis (see services/amendment.py).
    "RETURNED_BY_MANAGER": {
        "SUBMITTED_TO_MANAGER": {OWNER},
        "EVIDENCE_COLLECTION": {OWNER},
        **_INFO_REQUEST,
    },
    "READY_FOR_COMMITTEE": {
        STATUS_COMMITTEE_REVIEW: _COMMITTEE,
        "INFORMATION_REQUESTED": _PIPELINE | _COMMITTEE,
    },
    STATUS_COMMITTEE_REVIEW: {
        "APPROVED": _COMMITTEE,
        "APPROVED_WITH_CONDITIONS": _COMMITTEE,
        "DEFERRED": _COMMITTEE,
        "REJECTED": _COMMITTEE,
        "INFORMATION_REQUESTED": _PIPELINE | _COMMITTEE,
    },
    "DEFERRED": {
        STATUS_COMMITTEE_REVIEW: _COMMITTEE,
        "INFORMATION_REQUESTED": _PIPELINE | _COMMITTEE,
    },
    "APPROVED": dict(_FINAL_DECISION_EXITS),
    "APPROVED_WITH_CONDITIONS": dict(_FINAL_DECISION_EXITS),
    "REJECTED": dict(_FINAL_DECISION_EXITS),
    "MANAGER_REJECTED": dict(_FINAL_DECISION_EXITS),
    "REMEDIATION": {
        "INTAKE": {OWNER} | _PIPELINE,
        # POST /{id}/analyze may restart the pipeline straight from here.
        "RISK_IDENTIFICATION": _PIPELINE,
    },
    # INFORMATION_REQUESTED is special-cased: its only exit is back to
    # the status it was requested from (pre_information_request_status).
    "INFORMATION_REQUESTED": {},
    STATUS_CLOSED: {},
}

_INFO_RESUME_ROLES = {OWNER} | _PIPELINE | _COMMITTEE


def _allowed_targets(assessment: Assessment) -> dict[str, set[str]]:
    if assessment.status == "INFORMATION_REQUESTED":
        resume_to = assessment.pre_information_request_status or "HUMAN_REVIEW"
        return {resume_to: _INFO_RESUME_ROLES}
    return TRANSITIONS.get(assessment.status, {})


def _user_holds(user: User, assessment: Assessment, roles: set[str]) -> bool:
    if user.role in roles:
        return True
    if OWNER in roles and assessment.owner_id == user.id:
        return True
    if ASSIGNED_MANAGER in roles and assessment.manager_id == user.id:
        return True
    return False


def _actor_name(user: User | None) -> str:
    if user is None:
        return "System"
    return user.full_name or user.email


# R14.2: the role/action matrix, for display (GET /api/workflow/permissions)
# and for the lifecycle-only actions this module owns directly. The
# per-transition roles above are the enforcement for status changes.
ACTION_PERMISSIONS: dict[str, dict] = {
    "submit": {
        "label": "Submit a draft request",
        "roles": [OWNER],
        "statuses": [WorkflowStatus.DRAFT.value],
    },
    "edit": {
        "label": "Edit request details",
        "roles": [OWNER, *sorted(_PIPELINE)],
        "statuses": [
            WorkflowStatus.DRAFT.value,
            WorkflowStatus.SUBMITTED.value,
            WorkflowStatus.AMENDMENT_REQUIRED.value,
        ],
    },
    "review": {
        "label": "Validate, review evidence and run the risk assessment",
        "roles": sorted(_PIPELINE),
        "statuses": [
            WorkflowStatus.SUBMITTED.value,
            WorkflowStatus.INTAKE_VALIDATION.value,
            WorkflowStatus.EVIDENCE_REVIEW.value,
            WorkflowStatus.RISK_ASSESSMENT_IN_PROGRESS.value,
            WorkflowStatus.ANALYST_REVIEW.value,
        ],
    },
    "request_information": {
        "label": "Request more information",
        "roles": sorted(_PIPELINE | _COMMITTEE),
        "statuses": "any active",
    },
    "challenge": {
        "label": "Challenge review decision (approve / return / reject)",
        "roles": [ASSIGNED_MANAGER],
        "statuses": [WorkflowStatus.CHALLENGE_REVIEW.value],
    },
    "approve": {
        "label": "Committee decision (approve / conditions / defer / reject)",
        "roles": sorted(_COMMITTEE),
        "statuses": [
            WorkflowStatus.READY_FOR_COMMITTEE.value,
            WorkflowStatus.COMMITTEE_REVIEW.value,
            WorkflowStatus.DEFERRED.value,
        ],
    },
    "amend": {
        "label": "Open a controlled amendment on a decision",
        "roles": sorted(_COMMITTEE),
        "statuses": sorted(DECIDED_WORKFLOW_STATUSES),
    },
    "close": {
        "label": "Close an assessment",
        "roles": sorted(_CLOSERS),
        "statuses": sorted(DECIDED_WORKFLOW_STATUSES),
    },
    "withdraw": {
        "label": "Withdraw (close) a request that has not started review",
        "roles": [OWNER, UserRole.ADMIN.value],
        "statuses": [WorkflowStatus.DRAFT.value, WorkflowStatus.SUBMITTED.value],
    },
    "assign": {
        "label": "Assign or claim the current task",
        "roles": sorted(_PIPELINE | _COMMITTEE),
        "statuses": "any active",
    },
    "set_target_date": {
        "label": "Set the target completion date",
        "roles": sorted(_PIPELINE),
        "statuses": "any active",
    },
}


def can_perform(user: User, assessment: Assessment, action: str) -> bool:
    rule = ACTION_PERMISSIONS[action]
    if not _user_holds(user, assessment, set(rule["roles"])):
        return False
    statuses = rule["statuses"]
    current = assessment.workflow_status
    if statuses == "any active":
        return current not in TERMINAL_WORKFLOW_STATUSES
    return current in statuses


def require_action(user: User, assessment: Assessment, action: str) -> None:
    if not can_perform(user, assessment, action):
        rule = ACTION_PERMISSIONS[action]
        raise HTTPException(
            status_code=403,
            detail=(
                f"You cannot '{rule['label'].lower()}' on an assessment that is "
                f"{label_for(assessment.workflow_status)}."
            ),
        )


def label_for(workflow_status: str | None) -> str:
    try:
        return WORKFLOW_STATUS_LABELS[WorkflowStatus(workflow_status)]
    except ValueError:
        return workflow_status or "Unknown"


# ---------------------------------------------------------------------------
# Guards: business rules on top of the transition table.
# ---------------------------------------------------------------------------

# "Given unresolved mandatory issues, it cannot move to committee review."
COMMITTEE_GATED_STATUSES = {"READY_FOR_COMMITTEE", STATUS_COMMITTEE_REVIEW}


def get_mandatory_issues(db: Session, assessment: Assessment) -> list[str]:
    """
    Everything that must be resolved before an assessment may go to the
    committee. Also surfaced on GET /{id}/workflow so the UI can show why
    the move is blocked before anyone tries it.

    P3: one authoritative evaluation (app/governance/readiness.py) --
    intake, residual, comments, challenge findings by severity, the
    independent challenge review and sign-off, pending/rejected material
    overrides and unresolved SoD conflicts.
    """

    from app.governance.readiness import COMMITTEE_SUBMISSION, blocker_messages

    return blocker_messages(db, assessment, COMMITTEE_SUBMISSION)


def _open_action_item_count(db: Session, assessment_id: int) -> int:
    from app.models.action_item import ActionItem, OPEN_STATUSES

    return (
        db.query(ActionItem)
        .filter(
            ActionItem.assessment_id == assessment_id,
            ActionItem.status.in_(OPEN_STATUSES),
        )
        .count()
    )


def _check_guards(db: Session, assessment: Assessment, to_status: str, user: User | None = None) -> None:
    if assessment.is_draft and assessment.status == "INTAKE" and to_status != STATUS_CLOSED:
        # "Given an assessment in draft status, it cannot be approved
        # directly" -- nor progress at all until it's been submitted.
        raise HTTPException(
            status_code=409,
            detail=(
                "This assessment is still a Draft. Submit it (all mandatory "
                "fields complete) before it can move into review."
            ),
        )

    if to_status in COMMITTEE_GATED_STATUSES:
        from app.governance.readiness import COMMITTEE_SUBMISSION, evaluate, log_block

        readiness = evaluate(db, assessment, COMMITTEE_SUBMISSION)
        if not readiness["ready"]:
            log_block(assessment.id, user, COMMITTEE_SUBMISSION, readiness["blockers"])
            raise HTTPException(
                status_code=409,
                detail={
                    "message": (
                        "This assessment has unresolved mandatory issues and "
                        "cannot move to committee review."
                    ),
                    "issues": [b["message"] for b in readiness["blockers"]],
                    "blockers": readiness["blockers"],
                },
            )

    if to_status == STATUS_CLOSED and assessment.status in {"APPROVED", "APPROVED_WITH_CONDITIONS"}:
        open_items = _open_action_item_count(db, assessment.id)
        if open_items:
            raise HTTPException(
                status_code=409,
                detail=(
                    f"{open_items} action item(s) / committee condition(s) are "
                    "still open. Complete or cancel them before closing."
                ),
            )


# ---------------------------------------------------------------------------
# R14.4: SLA / due dates.
# ---------------------------------------------------------------------------

# Calendar days allowed in each lifecycle status before it's overdue.
# None = no SLA clock (nobody is waiting on anyone).
STATUS_SLA_DAYS: dict[str, int | None] = {
    WorkflowStatus.DRAFT.value: None,
    WorkflowStatus.SUBMITTED.value: 2,
    WorkflowStatus.INTAKE_VALIDATION.value: 3,
    WorkflowStatus.INFORMATION_REQUESTED.value: 5,
    WorkflowStatus.EVIDENCE_REVIEW.value: 5,
    WorkflowStatus.RISK_ASSESSMENT_IN_PROGRESS.value: 10,
    WorkflowStatus.ANALYST_REVIEW.value: 3,
    WorkflowStatus.CHALLENGE_REVIEW.value: 3,
    WorkflowStatus.READY_FOR_COMMITTEE.value: 7,
    WorkflowStatus.COMMITTEE_REVIEW.value: 5,
    WorkflowStatus.DEFERRED.value: 14,
    WorkflowStatus.AMENDMENT_REQUIRED.value: 10,
    WorkflowStatus.APPROVED.value: None,
    WorkflowStatus.APPROVED_WITH_CONDITIONS.value: None,
    WorkflowStatus.REJECTED.value: None,
    WorkflowStatus.CLOSED.value: None,
}

# Intake priority (see services/triage.py) tightens or relaxes SLAs.
PRIORITY_SLA_FACTOR = {"URGENT": 0.5, "HIGH": 0.75, "MEDIUM": 1.0, "LOW": 1.25}

# Default overall target (days from submission) by priority.
TARGET_DAYS_BY_PRIORITY = {"URGENT": 15, "HIGH": 30, "MEDIUM": 45, "LOW": 60}
DEFAULT_TARGET_DAYS = 45

AT_RISK_FRACTION = 0.25  # < 25% of the SLA window left => AT_RISK


def _now() -> datetime:
    return datetime.now(timezone.utc)


def as_utc(value: datetime | None) -> datetime | None:
    # SQLite hands back naive datetimes for values written as UTC.
    if value is None:
        return None
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def sla_days_for(workflow_status: str, priority: str | None) -> int | None:
    base = STATUS_SLA_DAYS.get(workflow_status)
    if base is None:
        return None
    factor = PRIORITY_SLA_FACTOR.get((priority or "").upper(), 1.0)
    return max(1, math.ceil(base * factor))


def default_target_date(assessment: Assessment, start: datetime | None = None) -> date:
    days = TARGET_DAYS_BY_PRIORITY.get((assessment.priority or "").upper(), DEFAULT_TARGET_DAYS)
    return ((start or _now()) + timedelta(days=days)).date()


def compute_sla_state(assessment: Assessment, now: datetime | None = None) -> str:
    """NONE | ON_TRACK | AT_RISK | OVERDUE (for the current status)."""

    now = now or _now()
    if assessment.workflow_status in TERMINAL_WORKFLOW_STATUSES:
        return "NONE"

    due = as_utc(assessment.status_due_at)
    target = assessment.target_date
    target_overdue = (
        target is not None
        and assessment.workflow_status not in DECIDED_WORKFLOW_STATUSES
        and now.date() > target
    )

    if due is None:
        return "OVERDUE" if target_overdue else "NONE"
    if now > due or target_overdue:
        return "OVERDUE"

    entered = as_utc(assessment.status_entered_at) or now
    window = (due - entered).total_seconds()
    remaining = (due - now).total_seconds()
    if window > 0 and remaining / window < AT_RISK_FRACTION:
        return "AT_RISK"
    return "ON_TRACK"


# ---------------------------------------------------------------------------
# R14.3: current owner, team and next action.
# ---------------------------------------------------------------------------

PARTY_OWNER = "OWNER"
PARTY_MANAGER = "ASSIGNED_MANAGER"
PARTY_ANALYST = "FCRM_ANALYST"
PARTY_COMMITTEE = "COMMITTEE"

# Roles eligible to be assigned / claim a task for each party.
PARTY_ELIGIBLE_ROLES = {
    PARTY_OWNER: {UserRole.BUSINESS_USER.value, *_PIPELINE},
    PARTY_MANAGER: {UserRole.MANAGER.value},
    PARTY_ANALYST: set(_PIPELINE),
    PARTY_COMMITTEE: set(_COMMITTEE),
}

_NEXT_ACTION_BY_STAGE = {
    "RISK_IDENTIFICATION": "Review identified risk factors and rate each one",
    "INHERENT_RISK_ASSESSMENT": "Finalise inherent risk and assess controls",
    "CONTROL_ASSESSMENT": "Record the control assessment outcome",
    "RESIDUAL_RISK": "Confirm residual risk and move to analyst review",
}


@dataclass
class Responsibility:
    party: str | None  # primary responsible party
    next_action: str
    also: tuple[str, ...] = ()  # secondary parties who also see it in their queue


def responsibility_for(assessment: Assessment) -> Responsibility:
    wf = assessment.workflow_status
    W = WorkflowStatus

    if wf == W.DRAFT.value:
        return Responsibility(PARTY_OWNER, "Complete the mandatory fields and submit the request")
    if wf == W.SUBMITTED.value:
        return Responsibility(PARTY_ANALYST, "Validate the submission and start intake validation", (PARTY_OWNER,))
    if wf == W.INTAKE_VALIDATION.value:
        return Responsibility(PARTY_OWNER, "Review and confirm the structured business/product profile", (PARTY_ANALYST,))
    if wf == W.EVIDENCE_REVIEW.value:
        return Responsibility(PARTY_ANALYST, "Review the evidence and run risk identification")
    if wf == W.INFORMATION_REQUESTED.value:
        note = (assessment.information_request_note or "").strip()
        target = assessment.information_request_target or "the business owner"
        return Responsibility(
            PARTY_OWNER,
            f"Provide the information requested from {target}" + (f": {note}" if note else ""),
        )
    if wf == W.RISK_ASSESSMENT_IN_PROGRESS.value:
        return Responsibility(
            PARTY_ANALYST,
            _NEXT_ACTION_BY_STAGE.get(assessment.status, "Continue the risk assessment"),
        )
    if wf == W.ANALYST_REVIEW.value:
        return Responsibility(
            PARTY_ANALYST,
            "Complete the FCRM analyst review; the business owner then submits it for challenge review",
            (PARTY_OWNER,),
        )
    if wf == W.CHALLENGE_REVIEW.value:
        return Responsibility(PARTY_MANAGER, "Challenge the assessment and approve, return or reject it")
    if wf == W.AMENDMENT_REQUIRED.value:
        if assessment.status == "RETURNED_BY_MANAGER":
            return Responsibility(PARTY_OWNER, "Address the manager's feedback and resubmit")
        return Responsibility(PARTY_OWNER, "Address the required amendments and return the request to intake")
    if wf == W.READY_FOR_COMMITTEE.value:
        return Responsibility(PARTY_COMMITTEE, "Open committee review")
    if wf == W.COMMITTEE_REVIEW.value:
        return Responsibility(PARTY_COMMITTEE, "Record votes and the committee decision")
    if wf == W.DEFERRED.value:
        return Responsibility(PARTY_COMMITTEE, "Re-open committee review once deferral reasons are addressed")
    if wf == W.APPROVED_WITH_CONDITIONS.value:
        return Responsibility(
            PARTY_OWNER,
            "Complete the committee conditions / action items; the assessment can then be closed",
            (PARTY_ANALYST,),
        )
    if wf in {W.APPROVED.value, W.REJECTED.value}:
        return Responsibility(PARTY_ANALYST, "Close the assessment")
    return Responsibility(None, "No further action")


def _team_for(assessment: Assessment, party: str | None) -> str:
    if party == PARTY_OWNER:
        unit = assessment.legal_entity or assessment.business_owner
        return f"Business — {unit}" if unit else "Business owner"
    if party == PARTY_MANAGER:
        return "Line management"
    if party == PARTY_ANALYST:
        return assessment.assigned_team or "FCRM Analysis"
    if party == PARTY_COMMITTEE:
        return "Risk Committee"
    return "—"


def _delegated_party(assessment: Assessment, party: str, delegations) -> bool:
    """AW.7: whether one of `delegations` (active, held by the user) covers this party's task."""

    from app.services import delegation as delegation_rules

    for grant in delegations:
        if grant.scope_type == delegation_rules.SCOPE_ASSESSMENT and grant.scope_assessment_id != assessment.id:
            continue
        if party == PARTY_MANAGER and grant.authority == delegation_rules.AUTHORITY_MANAGER_APPROVAL:
            if grant.delegator_id == assessment.manager_id:
                return True
        if party == PARTY_COMMITTEE and grant.authority == delegation_rules.AUTHORITY_COMMITTEE_SIGN_OFF:
            return True
    return False


def _user_matches_party(user: User, assessment: Assessment, party: str, delegations=()) -> bool:
    if party == PARTY_OWNER:
        return assessment.owner_id == user.id
    if party == PARTY_MANAGER:
        return assessment.manager_id == user.id or _delegated_party(assessment, party, delegations)
    if party == PARTY_ANALYST:
        return user.role == UserRole.FCRM_ANALYST.value
    if party == PARTY_COMMITTEE:
        return user.role == UserRole.COMMITTEE_MEMBER.value or _delegated_party(assessment, party, delegations)
    return False


def is_responsible(user: User, assessment: Assessment, delegations=()) -> bool:
    """
    Whether this assessment's current task belongs in `user`'s work queue.
    `delegations` are the active approval delegations the user holds
    (AW.7), so a delegate sees the tasks they are covering.
    """

    if assessment.workflow_status in TERMINAL_WORKFLOW_STATUSES:
        return False
    if assessment.current_assignee_id is not None:
        return assessment.current_assignee_id == user.id
    resp = responsibility_for(assessment)
    parties = ([resp.party] if resp.party else []) + list(resp.also)
    return any(_user_matches_party(user, assessment, party, delegations) for party in parties)


def describe_owner(db: Session, assessment: Assessment) -> dict:
    resp = responsibility_for(assessment)
    assignee = None
    if assessment.current_assignee_id:
        assignee = db.query(User).filter(User.id == assessment.current_assignee_id).first()

    name: str
    user_id: int | None = None
    if assignee:
        name, user_id = _actor_name(assignee), assignee.id
    elif resp.party == PARTY_OWNER and assessment.owner_id:
        owner = db.query(User).filter(User.id == assessment.owner_id).first()
        name = _actor_name(owner) if owner else (assessment.business_owner or "Business owner")
        user_id = owner.id if owner else None
    elif resp.party == PARTY_MANAGER and assessment.manager_id:
        manager = db.query(User).filter(User.id == assessment.manager_id).first()
        name = _actor_name(manager) if manager else "Assigned manager"
        user_id = manager.id if manager else None
    elif resp.party == PARTY_ANALYST:
        name = "Unassigned (FCRM analyst queue)"
    elif resp.party == PARTY_COMMITTEE:
        name = "Risk Committee"
    else:
        name = "—"

    return {
        "party": resp.party,
        "owner_name": name,
        "owner_user_id": user_id,
        "team": _team_for(assessment, resp.party),
        "next_action": resp.next_action,
    }


# ---------------------------------------------------------------------------
# Transitions
# ---------------------------------------------------------------------------


def _record(
    db: Session,
    assessment: Assessment,
    *,
    from_status: str | None,
    from_workflow_status: str | None,
    user: User | None,
    reason: str,
    action: str,
    on_behalf_of: User | None = None,
) -> WorkflowTransition:
    new_wf = derive_workflow_status(db, assessment)
    now = _now()

    if new_wf != from_workflow_status or assessment.status_entered_at is None:
        # New lifecycle status: restart the SLA clock, drop any task
        # assignment and escalation belonging to the previous one.
        assessment.status_entered_at = now
        days = sla_days_for(new_wf, assessment.priority)
        assessment.status_due_at = now + timedelta(days=days) if days else None
        assessment.current_assignee_id = None
        assessment.escalation_level = 0
        assessment.escalated_at = None
        assessment.escalated_to_id = None
        assessment.escalation_note = None

    assessment.workflow_status = new_wf

    if new_wf == WorkflowStatus.SUBMITTED.value and assessment.target_date is None:
        assessment.target_date = default_target_date(assessment, now)
    if new_wf == WorkflowStatus.CLOSED.value:
        assessment.closed_at = now

    entry = WorkflowTransition(
        assessment_id=assessment.id,
        from_status=from_status,
        to_status=assessment.status,
        from_workflow_status=from_workflow_status,
        to_workflow_status=new_wf,
        action=action,
        reason=reason.strip(),
        user_id=user.id if user else None,
        actor=(
            f"{_actor_name(user)} (delegate for {_actor_name(on_behalf_of)})"
            if on_behalf_of is not None and user is not None and on_behalf_of.id != user.id
            else _actor_name(user)
        ),
        created_at=now,
    )
    db.add(entry)
    # Email notifications for this change; sent only after the commit.
    from app.services.notifications import queue_for_transition

    queue_for_transition(db, assessment, action=action, reason=reason.strip(), user=user)
    return entry


def check_transition(
    db: Session,
    assessment: Assessment,
    to_status: str,
    *,
    user: User | None,
    on_behalf_of: User | None = None,
) -> None:
    """
    Validates a move to pipeline status `to_status` without making it:
    R14.1 (permitted transitions), R14.2 (the user's role for this
    transition; skipped when user is None, i.e. a system-triggered move)
    and the business-rule guards. Raises HTTPException if not allowed.
    Endpoints whose transition has expensive side effects (e.g. running
    the risk engine) call this first, then transition() afterwards.
    """

    ensure_workflow_state(db, assessment)
    allowed = _allowed_targets(assessment)

    if to_status not in allowed:
        permitted = ", ".join(sorted(allowed)) or "none (this status is final)"
        raise HTTPException(
            status_code=409,
            detail=(
                f"Transition from {assessment.status} "
                f"({label_for(assessment.workflow_status)}) to {to_status} is "
                f"not permitted. Permitted next statuses: {permitted}."
            ),
        )

    # AW.7: a delegate moves the assessment on the delegator's authority,
    # already resolved and scope-checked by app/services/delegation.py.
    authority = on_behalf_of or user
    if authority is not None and not _user_holds(authority, assessment, allowed[to_status]):
        raise HTTPException(
            status_code=403,
            detail=(
                f"Your role ({user.role}) cannot move this assessment from "
                f"{assessment.status} to {to_status}."
            ),
        )

    _check_guards(db, assessment, to_status, user)


def transition(
    db: Session,
    assessment: Assessment,
    to_status: str,
    *,
    user: User | None,
    reason: str,
    action: str,
    on_behalf_of: User | None = None,
) -> WorkflowTransition:
    """
    Moves `assessment` to pipeline status `to_status` after
    check_transition(), then records the R14.5 history row. Does not
    commit -- the caller's endpoint commits alongside its own changes.
    """

    if not (reason or "").strip():
        raise HTTPException(status_code=422, detail="A reason or comment is required for a status change.")

    check_transition(db, assessment, to_status, user=user, on_behalf_of=on_behalf_of)

    from_status = assessment.status
    from_wf = assessment.workflow_status
    assessment.status = to_status
    # P6 (R18.4): a reassessment's outcome decides its parent's standing.
    from app.services.reassessment_lifecycle import on_status_changed

    on_status_changed(db, assessment, from_status, to_status, user)
    return _record(
        db,
        assessment,
        from_status=from_status,
        from_workflow_status=from_wf,
        user=user,
        reason=reason,
        action=action,
        on_behalf_of=on_behalf_of,
    )


def sync_workflow_status(
    db: Session,
    assessment: Assessment,
    *,
    user: User | None,
    reason: str,
    action: str,
) -> WorkflowTransition | None:
    """
    For changes that alter the lifecycle status without touching the
    pipeline status (a draft being submitted, the structured profile
    being confirmed). Records a history row only if the lifecycle status
    actually changed.
    """

    db.flush()
    ensure_workflow_state(db, assessment)
    previous = assessment.workflow_status
    if previous == derive_workflow_status(db, assessment):
        return None
    return _record(
        db,
        assessment,
        from_status=assessment.status,
        from_workflow_status=previous,
        user=user,
        reason=reason,
        action=action,
    )


def record_creation(db: Session, assessment: Assessment, user: User | None, reason: str) -> WorkflowTransition:
    db.flush()
    return _record(
        db,
        assessment,
        from_status=None,
        from_workflow_status=None,
        user=user,
        reason=reason,
        action="CREATED",
    )


def ensure_workflow_state(db: Session, assessment: Assessment) -> None:
    """
    Initialises workflow_status / SLA fields for a row that predates
    Stage 14 (and wasn't backfilled by migrate_stage14_workflow.py),
    without inventing a history entry for it.
    """

    if assessment.workflow_status:
        return
    assessment.workflow_status = derive_workflow_status(db, assessment)
    entered = as_utc(assessment.updated_at) or _now()
    assessment.status_entered_at = entered
    days = sla_days_for(assessment.workflow_status, assessment.priority)
    assessment.status_due_at = entered + timedelta(days=days) if days else None
    if assessment.target_date is None and not assessment.is_draft:
        assessment.target_date = default_target_date(assessment, as_utc(assessment.submitted_at) or entered)


def available_transitions(db: Session, assessment: Assessment, user: User) -> list[dict]:
    """The permitted next statuses from here, and whether `user` may make each."""

    result = []
    for to_status, roles in sorted(_allowed_targets(assessment).items()):
        simulated = _PIPELINE_TO_WORKFLOW.get(to_status)
        if to_status == "INTAKE":
            to_wf = WorkflowStatus.SUBMITTED.value if not assessment.is_draft else WorkflowStatus.DRAFT.value
        elif to_status == "EVIDENCE_COLLECTION":
            to_wf = WorkflowStatus.INTAKE_VALIDATION.value
        else:
            to_wf = simulated.value if simulated else to_status
        result.append(
            {
                "to_status": to_status,
                "to_workflow_status": to_wf,
                "to_workflow_label": label_for(to_wf),
                "allowed_for_user": _user_holds(user, assessment, roles),
                "roles": sorted(roles),
            }
        )
    return result


# ---------------------------------------------------------------------------
# Escalation of overdue assessments ("the system displays or sends an
# escalation"). No outbound email exists in this app -- the escalation
# is the persisted flag + named recipient surfaced in the UI / work
# queue, plus an audit event.
# ---------------------------------------------------------------------------


def _escalation_recipient(db: Session, assessment: Assessment, level: int) -> User | None:
    if level >= 2:
        return (
            db.query(User)
            .filter(User.role == UserRole.ADMIN.value, User.is_active.is_(True))
            .order_by(User.id)
            .first()
        )

    party = responsibility_for(assessment).party
    responsible: User | None = None
    if assessment.current_assignee_id:
        responsible = db.query(User).filter(User.id == assessment.current_assignee_id).first()
    elif party == PARTY_OWNER and assessment.owner_id:
        responsible = db.query(User).filter(User.id == assessment.owner_id).first()
    elif party == PARTY_MANAGER and assessment.manager_id:
        responsible = db.query(User).filter(User.id == assessment.manager_id).first()

    if responsible and responsible.manager_id:
        manager = db.query(User).filter(User.id == responsible.manager_id).first()
        if manager and manager.is_active:
            return manager

    if party == PARTY_ANALYST and assessment.manager_id:
        manager = db.query(User).filter(User.id == assessment.manager_id).first()
        if manager and manager.is_active:
            return manager

    # No line manager on record: straight to an administrator.
    return _escalation_recipient(db, assessment, 2)


def escalate_overdue_assessments(db: Session, assessment_id: int | None = None) -> list[Assessment]:
    """
    Level 1 as soon as the current status passes its due date (to the
    responsible person's line manager); level 2 once it's overdue by a
    further full SLA window (to an administrator). Each level fires once
    per lifecycle status. Commits if anything was escalated.
    """

    now = _now()
    query = db.query(Assessment).filter(
        Assessment.status_due_at.isnot(None),
        Assessment.workflow_status.notin_(TERMINAL_WORKFLOW_STATUSES | DECIDED_WORKFLOW_STATUSES),
    )
    if assessment_id is not None:
        query = query.filter(Assessment.id == assessment_id)

    escalated: list[Assessment] = []
    for assessment in query.all():
        due = as_utc(assessment.status_due_at)
        if due is None or now <= due:
            continue

        entered = as_utc(assessment.status_entered_at) or due
        window = max(due - entered, timedelta(days=1))
        level = 2 if now > due + window else 1
        if level <= (assessment.escalation_level or 0):
            continue

        recipient = _escalation_recipient(db, assessment, level)
        overdue_days = max(1, (now - due).days)
        owner_info = describe_owner(db, assessment)

        assessment.escalation_level = level
        assessment.escalated_at = now
        assessment.escalated_to_id = recipient.id if recipient else None
        assessment.escalation_note = (
            f"{label_for(assessment.workflow_status)} is overdue by {overdue_days} day(s) "
            f"(due {due.date().isoformat()}). Current owner: {owner_info['owner_name']} "
            f"({owner_info['team']}). Escalated (level {level}) to "
            f"{_actor_name(recipient) if recipient else 'an administrator'}."
        )

        log_audit_event(
            db,
            assessment_id=assessment.id,
            action=AuditAction.WORKFLOW_ESCALATED,
            previous_status=assessment.status,
            new_status=assessment.status,
            actor="System",
            details=assessment.escalation_note,
        )
        escalated.append(assessment)

    if escalated:
        db.commit()
    return escalated

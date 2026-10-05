from enum import Enum
from sqlalchemy.orm import Session
from app.models.audit_event import AuditEvent


class AuditAction(str, Enum):
    CREATED = "CREATED"
    ANALYSIS = "ANALYSIS"
    # Degraded analysis (see app/risk_engine/degraded.py). Recorded on
    # the run itself, so "this rating came out of a rules-only run during
    # an AI outage" is answerable from the audit trail alone.
    AI_ANALYSIS_DEGRADED = "AI_ANALYSIS_DEGRADED"
    ANALYSIS_UNAVAILABLE = "ANALYSIS_UNAVAILABLE"
    DEGRADED_RESULT_ACKNOWLEDGED = "DEGRADED_RESULT_ACKNOWLEDGED"
    STATUS_CHANGE = "STATUS_CHANGE"
    APPROVAL = "APPROVAL"
    REMEDIATION = "REMEDIATION"
    REJECTION = "REJECTION"
    STAGE_ADVANCED = "STAGE_ADVANCED"
    CHALLENGE = "CHALLENGE"
    RISK_CALCULATOR_UPDATED = "RISK_CALCULATOR_UPDATED"
    MANUAL_SCORE_OVERRIDE = "MANUAL_SCORE_OVERRIDE"
    ACKNOWLEDGMENT = "ACKNOWLEDGMENT"
    # AW: hierarchical approval workflow.
    SUBMITTED_TO_MANAGER = "SUBMITTED_TO_MANAGER"
    MANAGER_APPROVED = "MANAGER_APPROVED"
    MANAGER_RETURNED = "MANAGER_RETURNED"
    MANAGER_REJECTED = "MANAGER_REJECTED"
    COMMITTEE_APPROVED = "COMMITTEE_APPROVED"
    COMMITTEE_APPROVED_WITH_CONDITIONS = "COMMITTEE_APPROVED_WITH_CONDITIONS"
    COMMITTEE_DEFERRED = "COMMITTEE_DEFERRED"
    COMMITTEE_REJECTED = "COMMITTEE_REJECTED"
    # AW.7: approval delegation.
    DELEGATION_CREATED = "DELEGATION_CREATED"
    DELEGATION_REVOKED = "DELEGATION_REVOKED"
    COMMENT_ADDED = "COMMENT_ADDED"
    # Stage 10: FCRM Analyst Review.
    OVERRIDE_APPLIED = "OVERRIDE_APPLIED"
    # P2: an independent reviewer confirmed or rejected a PROPOSED override.
    OVERRIDE_REVIEWED = "OVERRIDE_REVIEWED"
    INFORMATION_REQUESTED = "INFORMATION_REQUESTED"
    INFORMATION_PROVIDED = "INFORMATION_PROVIDED"
    COMMENT_RESOLVED = "COMMENT_RESOLVED"
    COMMENT_EXCEPTION_ACCEPTED = "COMMENT_EXCEPTION_ACCEPTED"
    # Stage 12: Risk Committee Decision.
    COMMITTEE_VOTE_CAST = "COMMITTEE_VOTE_CAST"
    # R11: the mandatory challenge-review sign-off.
    CHALLENGE_REVIEW_SIGNED_OFF = "CHALLENGE_REVIEW_SIGNED_OFF"
    CHALLENGE_REVIEW_SIGNOFF_SUPERSEDED = "CHALLENGE_REVIEW_SIGNOFF_SUPERSEDED"
    COMMITTEE_CONDITION_UPDATED = "COMMITTEE_CONDITION_UPDATED"
    ASSESSMENT_AMENDMENT_OPENED = "ASSESSMENT_AMENDMENT_OPENED"
    # Stage 7: AI control identification and design assessment.
    CONTROL_IDENTIFIED = "CONTROL_IDENTIFIED"
    CONTROL_ASSESSED = "CONTROL_ASSESSED"
    # R7.2/R10.2: versioned control changes (see ControlRevision).
    CONTROL_UPDATED = "CONTROL_UPDATED"
    CONTROL_REMAPPED = "CONTROL_REMAPPED"
    CONTROL_UNMAPPED = "CONTROL_UNMAPPED"
    # Stage 13: Conditions and Remediation Tracking.
    ACTION_ITEM_CREATED = "ACTION_ITEM_CREATED"
    ACTION_ITEM_UPDATED = "ACTION_ITEM_UPDATED"
    ACTION_ITEM_ESCALATED = "ACTION_ITEM_ESCALATED"
    ACTION_ITEM_CLOSURE_REQUESTED = "ACTION_ITEM_CLOSURE_REQUESTED"
    ACTION_ITEM_CLOSURE_APPROVED = "ACTION_ITEM_CLOSURE_APPROVED"
    ACTION_ITEM_CLOSURE_REJECTED = "ACTION_ITEM_CLOSURE_REJECTED"
    # Stage 14: Workflow and Status Management.
    DRAFT_SUBMITTED = "DRAFT_SUBMITTED"
    COMMITTEE_REVIEW_OPENED = "COMMITTEE_REVIEW_OPENED"
    ASSESSMENT_CLOSED = "ASSESSMENT_CLOSED"
    ASSESSMENT_WITHDRAWN = "ASSESSMENT_WITHDRAWN"
    WORKFLOW_ASSIGNED = "WORKFLOW_ASSIGNED"
    WORKFLOW_TARGET_DATE_SET = "WORKFLOW_TARGET_DATE_SET"
    WORKFLOW_ESCALATED = "WORKFLOW_ESCALATED"
    # Stage 17: policy / methodology change log (governance reporting).
    METHODOLOGY_CHANGED = "METHODOLOGY_CHANGED"
    REFERENCE_DATA_CHANGED = "REFERENCE_DATA_CHANGED"
    DECISION_RECORD_FROZEN = "DECISION_RECORD_FROZEN"
    CHALLENGE_CONFIG_CHANGED = "CHALLENGE_CONFIG_CHANGED"
    # Stage 19: non-functional requirements.
    ADMIN_ACTION = "ADMIN_ACTION"
    # R15.5: a refused request (wrong role, out of scope, read-only role).
    ACCESS_DENIED = "ACCESS_DENIED"
    # R15.5: a document's original file was opened in the browser or
    # downloaded (content is never logged).
    DOCUMENT_VIEWED = "DOCUMENT_VIEWED"
    DOCUMENT_DOWNLOADED = "DOCUMENT_DOWNLOADED"
    # P3: SoD exceptions (lifecycle and every use), designations,
    # material-override approval, the two-step challenge review, and
    # refused committee submissions / decisions.
    SOD_EXCEPTION_CREATED = "SOD_EXCEPTION_CREATED"
    SOD_EXCEPTION_UPDATED = "SOD_EXCEPTION_UPDATED"
    SOD_EXCEPTION_SUBMITTED = "SOD_EXCEPTION_SUBMITTED"
    SOD_EXCEPTION_APPROVED = "SOD_EXCEPTION_APPROVED"
    SOD_EXCEPTION_REJECTED = "SOD_EXCEPTION_REJECTED"
    SOD_EXCEPTION_DECLARED = "SOD_EXCEPTION_DECLARED"
    SOD_EXCEPTION_USED = "SOD_EXCEPTION_USED"
    SOD_EXCEPTION_REVIEWED = "SOD_EXCEPTION_REVIEWED"
    SOD_EXCEPTION_REVOKED = "SOD_EXCEPTION_REVOKED"
    SOD_EXCEPTION_EXPIRED = "SOD_EXCEPTION_EXPIRED"
    USER_DESIGNATIONS_CHANGED = "USER_DESIGNATIONS_CHANGED"
    OVERRIDE_APPROVAL_DECIDED = "OVERRIDE_APPROVAL_DECIDED"
    CHALLENGE_REVIEW_COMPLETED = "CHALLENGE_REVIEW_COMPLETED"
    GOVERNANCE_READINESS_BLOCKED = "GOVERNANCE_READINESS_BLOCKED"
    # P5: retention policy versions, legal holds, soft delete and the
    # eligibility report (previously all logged as STATUS_CHANGE).
    RETENTION_POLICY_PROPOSED = "RETENTION_POLICY_PROPOSED"
    RETENTION_POLICY_APPROVED = "RETENTION_POLICY_APPROVED"
    RETENTION_POLICY_REJECTED = "RETENTION_POLICY_REJECTED"
    RETENTION_POLICY_ACTIVATED = "RETENTION_POLICY_ACTIVATED"
    RETENTION_POLICY_SUPERSEDED = "RETENTION_POLICY_SUPERSEDED"
    LEGAL_HOLD_SET = "LEGAL_HOLD_SET"
    LEGAL_HOLD_RELEASED = "LEGAL_HOLD_RELEASED"
    ASSESSMENT_SOFT_DELETED = "ASSESSMENT_SOFT_DELETED"
    RETENTION_REPORT_VIEWED = "RETENTION_REPORT_VIEWED"
    # P6: reassessment lifecycle (previously STATUS_CHANGE).
    REASSESSMENT_TRIGGER_FLAGGED = "REASSESSMENT_TRIGGER_FLAGGED"
    REASSESSMENT_TRIGGER_RESOLVED = "REASSESSMENT_TRIGGER_RESOLVED"
    REASSESSMENT_DUE_DETECTED = "REASSESSMENT_DUE_DETECTED"
    REASSESSMENT_OPENED = "REASSESSMENT_OPENED"
    REASSESSMENT_PARENT_SUPERSEDED = "REASSESSMENT_PARENT_SUPERSEDED"
    REASSESSMENT_ENDED_WITHOUT_APPROVAL = "REASSESSMENT_ENDED_WITHOUT_APPROVAL"
    # P4: evidence traceability -- intake / profile changes with old and new
    # values (previously STATUS_CHANGE with field names only), expired
    # evidence acknowledgements, analyst indicator edits.
    INTAKE_UPDATED = "INTAKE_UPDATED"
    PROFILE_CORRECTED = "PROFILE_CORRECTED"
    PROFILE_CONFIRMED = "PROFILE_CONFIRMED"
    EVIDENCE_EXPIRY_ACKNOWLEDGED = "EVIDENCE_EXPIRY_ACKNOWLEDGED"
    RISK_INDICATORS_CHANGED = "RISK_INDICATORS_CHANGED"
    # R5.1-R5.3: approved-source library changes and evidence attached.
    SOURCE_LIBRARY_CHANGED = "SOURCE_LIBRARY_CHANGED"
    USER_CREATED = "USER_CREATED"
    USER_UPDATED = "USER_UPDATED"
    PROCESSING_FAILED = "PROCESSING_FAILED"
    PROCESSING_INCOMPLETE = "PROCESSING_INCOMPLETE"
    PROCESSING_RETRIED = "PROCESSING_RETRIED"
    BACKUP_CREATED = "BACKUP_CREATED"
    BACKUP_VERIFIED = "BACKUP_VERIFIED"
    # The read-only assistant answered a question from this assessment's
    # details (who looked at what; the question text is not recorded).
    ASSISTANT_QUERY = "ASSISTANT_QUERY"
    # AI challenge analysis: a run, and a reviewer's confirm / dismiss.
    AI_CHALLENGE_RUN = "AI_CHALLENGE_RUN"
    AI_CHALLENGE_DECIDED = "AI_CHALLENGE_DECIDED"
    # The owner resubmitted after a manager return, but documents or request
    # details had changed, so the assessment went back through analysis.
    REANALYSIS_REQUIRED = "REANALYSIS_REQUIRED"


def actor_name(user) -> str:
    """The display name recorded for an authenticated user's actions.
    Every "who did this" field comes from the session through this, never
    from a request body."""

    return user.full_name or user.email


def log_audit_event(
    db: Session,
    assessment_id: int | None,
    action: AuditAction,
    previous_status: str | None = None,
    new_status: str | None = None,
    actor: str | None = None,
    actor_id: int | None = None,
    details: str | None = None,
):
    event = AuditEvent(
        assessment_id=assessment_id,
        action=action.value,
        previous_status=previous_status,
        new_status=new_status,
        actor=actor or "System",
        actor_id=actor_id,
        details=details,
    )
    db.add(event)
    return event
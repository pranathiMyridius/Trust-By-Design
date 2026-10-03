from datetime import datetime, timezone
from enum import Enum

from sqlalchemy import Boolean, Column, Date, DateTime, ForeignKey, Integer, String, Text

from app.database import Base


class ActionItemSourceType(str, Enum):
    CONTROL_GAP = "CONTROL_GAP"
    COMMITTEE_CONDITION = "COMMITTEE_CONDITION"
    MISSING_EVIDENCE = "MISSING_EVIDENCE"
    POLICY_EXCEPTION = "POLICY_EXCEPTION"
    VENDOR_REMEDIATION = "VENDOR_REMEDIATION"
    MONITORING_ENHANCEMENT = "MONITORING_ENHANCEMENT"


class ActionItemStatus(str, Enum):
    OPEN = "OPEN"
    IN_PROGRESS = "IN_PROGRESS"
    # R13.4: evidence has been submitted and, for sources that require it,
    # is awaiting a reviewer's closure decision.
    PENDING_CLOSURE_APPROVAL = "PENDING_CLOSURE_APPROVAL"
    COMPLETED = "COMPLETED"
    CLOSURE_REJECTED = "CLOSURE_REJECTED"
    CANCELLED = "CANCELLED"


# Source types where closing the action requires an authorized reviewer's
# approval (not just evidence) -- the higher-stakes sources. The rest can
# be closed directly once evidence is attached (R13.4: "...and, where
# necessary, approval by an authorized reviewer").
SOURCES_REQUIRING_CLOSURE_APPROVAL = {
    ActionItemSourceType.COMMITTEE_CONDITION.value,
    ActionItemSourceType.POLICY_EXCEPTION.value,
    ActionItemSourceType.VENDOR_REMEDIATION.value,
}

OPEN_STATUSES = {
    ActionItemStatus.OPEN.value,
    ActionItemStatus.IN_PROGRESS.value,
    ActionItemStatus.PENDING_CLOSURE_APPROVAL.value,
    ActionItemStatus.CLOSURE_REJECTED.value,
}
CLOSED_STATUSES = {ActionItemStatus.COMPLETED.value, ActionItemStatus.CANCELLED.value}


class ActionItem(Base):
    """
    Stage 13 (R13.1-R13.5): a trackable remediation/action item, unifying
    six sources under one owner/due-date/priority/status/evidence shape.
    Two of those sources (control_conditions, committee_conditions)
    already had their own tracked-to-completion tables (Stage 7/Stage 12)
    -- rather than duplicating their fields, an ActionItem for those
    sources links back via control_gap_id/committee_condition_id
    (R13.5) and is the thing Stage 13's escalation/closure-approval
    workflow operates on. The other four sources (missing evidence,
    policy exception, vendor remediation, monitoring enhancement) have no
    dedicated table, so `source_reference` free-text carries their
    provenance instead.
    """

    __tablename__ = "action_items"

    id = Column(Integer, primary_key=True, index=True)

    assessment_id = Column(Integer, ForeignKey("assessments.id"), nullable=False, index=True)

    # One of ActionItemSourceType.
    source_type = Column(String(50), nullable=False)

    # R13.5: link to the relevant risk/control/committee condition, where
    # a dedicated row exists for the source.
    risk_factor_id = Column(Integer, ForeignKey("risk_factors.id"), nullable=True)
    control_id = Column(Integer, ForeignKey("controls.id"), nullable=True)
    control_gap_id = Column(Integer, ForeignKey("control_gaps.id"), nullable=True)
    committee_condition_id = Column(Integer, ForeignKey("committee_conditions.id"), nullable=True)
    # Free-text provenance for a source with no dedicated table (e.g. an
    # evidence-gap code, a named policy exception, a vendor name).
    source_reference = Column(String(255), nullable=True)

    title = Column(String(255), nullable=False)
    description = Column(Text, nullable=True)

    # R13.2
    owner = Column(String(255), nullable=True)
    department = Column(String(255), nullable=True)
    due_date = Column(Date, nullable=True)
    # LOW | MEDIUM | HIGH | CRITICAL
    priority = Column(String(20), nullable=False, default="MEDIUM")
    # One of ActionItemStatus.
    status = Column(String(30), nullable=False, default="OPEN")
    completion_evidence = Column(Text, nullable=True)

    # R13.3: escalation.
    escalated = Column(Boolean, nullable=False, default=False)
    escalated_at = Column(DateTime, nullable=True)
    escalation_note = Column(Text, nullable=True)

    # R13.4: closure approval, for SOURCES_REQUIRING_CLOSURE_APPROVAL.
    closure_requested_by = Column(String(255), nullable=True)
    # So the reviewer can be checked against the requester exactly.
    closure_requested_by_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    closure_requested_at = Column(DateTime, nullable=True)
    # APPROVED | REJECTED
    closure_decision = Column(String(20), nullable=True)
    closure_decided_by = Column(String(255), nullable=True)
    closure_decided_at = Column(DateTime, nullable=True)
    closure_decision_note = Column(Text, nullable=True)

    created_by = Column(String(255), nullable=True)
    created_at = Column(
        DateTime,
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )
    updated_at = Column(
        DateTime,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
        nullable=False,
    )
    completed_at = Column(DateTime, nullable=True)

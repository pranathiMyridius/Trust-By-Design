"""
P3 (R15.2): Segregation-of-Duties exceptions.

An exception lets one named person do one thing the SoD rules would
otherwise refuse -- for a limited time, usually on one assessment -- and
only once an independent, authorized approver has approved it. A request
never grants anything by itself.

Lifecycle (app/governance/sod.py enforces every transition):

    DRAFT -> PENDING_APPROVAL -> APPROVED -> EXPIRED | REVOKED
                              -> REJECTED

Every step is appended to sod_exception_events (append-only) and to the
audit log. After a request leaves DRAFT its content can't change
(app/services/data_protection.py), so what was approved is what applies.
"""

from datetime import datetime, timezone

from sqlalchemy import Boolean, Column, DateTime, ForeignKey, Integer, String, Text

from app.database import Base

# What the exception permits. Each type is consulted at exactly one
# enforcement point (see app/governance/sod.py).
TYPE_COMMITTEE_SEPARATION = "COMMITTEE_SEPARATION"  # submitter / deciding manager may vote or decide
TYPE_ADMIN_COMMITTEE_DUAL_ROLE = "ADMIN_COMMITTEE_DUAL_ROLE"  # an Admin may act as a committee member
EXCEPTION_TYPES = {TYPE_COMMITTEE_SEPARATION, TYPE_ADMIN_COMMITTEE_DUAL_ROLE}

STATUS_DRAFT = "DRAFT"
STATUS_PENDING = "PENDING_APPROVAL"
STATUS_APPROVED = "APPROVED"
STATUS_REJECTED = "REJECTED"
STATUS_EXPIRED = "EXPIRED"
STATUS_REVOKED = "REVOKED"
EXCEPTION_STATUSES = {STATUS_DRAFT, STATUS_PENDING, STATUS_APPROVED, STATUS_REJECTED, STATUS_EXPIRED, STATUS_REVOKED}

TIER_STANDARD = "STANDARD"
TIER_COMMITTEE = "COMMITTEE"

RISK_LEVELS = ["LOW", "MEDIUM", "HIGH", "CRITICAL"]


def _now():
    return datetime.now(timezone.utc)


class SodException(Base):
    __tablename__ = "sod_exceptions"

    id = Column(Integer, primary_key=True, index=True)
    # Human-readable id, e.g. SOD-2026-0007.
    reference = Column(String(30), nullable=True, unique=True, index=True)

    exception_type = Column(String(40), nullable=False)
    status = Column(String(20), nullable=False, default=STATUS_DRAFT, index=True)

    requestor_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    affected_user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    # JSON list -- the roles/duties that conflict, derived by the server.
    conflicting_roles = Column(Text, nullable=False)
    # NULL = not limited to one assessment ("enterprise-wide").
    assessment_id = Column(Integer, ForeignKey("assessments.id"), nullable=True, index=True)

    business_justification = Column(Text, nullable=False)
    standard_workflow_reason = Column(Text, nullable=False)
    risk_level = Column(String(10), nullable=False)
    compensating_controls = Column(Text, nullable=False)

    start_at = Column(DateTime, nullable=False)
    # The exception authorizes nothing at or after this time.
    end_at = Column(DateTime, nullable=False)

    # Set by the server on submission (approval tiering).
    tier = Column(String(20), nullable=True)
    tier_reasons = Column(Text, nullable=True)
    repeated = Column(Boolean, nullable=False, default=False)

    assigned_approver_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    assigned_reviewer_id = Column(Integer, ForeignKey("users.id"), nullable=True)

    decision = Column(String(20), nullable=True)  # APPROVED | REJECTED
    decision_rationale = Column(Text, nullable=True)
    decided_by_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    decided_at = Column(DateTime, nullable=True)

    # The affected user's conflict-of-interest declaration; required before
    # an approved exception can be used.
    declaration = Column(Text, nullable=True)
    declared_at = Column(DateTime, nullable=True)

    revoked_by_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    revoked_at = Column(DateTime, nullable=True)
    revocation_reason = Column(Text, nullable=True)
    expired_at = Column(DateTime, nullable=True)

    last_reviewed_at = Column(DateTime, nullable=True)
    last_reviewed_by_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    last_review_note = Column(Text, nullable=True)

    submitted_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=_now, nullable=False)
    updated_at = Column(DateTime, default=_now, onupdate=_now, nullable=False)

    # Optimistic locking: two approvers acting at once -- the second write
    # fails instead of silently overwriting the first decision.
    row_version = Column(Integer, nullable=False, default=1)

    __mapper_args__ = {"version_id_col": row_version}


class SodExceptionEvent(Base):
    """Append-only history of one exception."""

    __tablename__ = "sod_exception_events"

    id = Column(Integer, primary_key=True, index=True)
    exception_id = Column(Integer, ForeignKey("sod_exceptions.id"), nullable=False, index=True)
    # CREATED | UPDATED | SUBMITTED | APPROVED | REJECTED | DECLARED |
    # USED | REVIEWED | REVOKED | EXPIRED
    action = Column(String(20), nullable=False)
    from_status = Column(String(20), nullable=True)
    to_status = Column(String(20), nullable=True)
    actor = Column(String(255), nullable=False)
    actor_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    detail = Column(Text, nullable=True)
    created_at = Column(DateTime, default=_now, nullable=False)

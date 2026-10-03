from datetime import datetime, timezone

from sqlalchemy import Boolean, Column, DateTime, ForeignKey, Index, Integer, String, Text, UniqueConstraint, text

from app.database import Base


class RetentionPolicy(Base):
    """
    Stage 16 (R16.4): the original single, global retention setting.

    P5: kept for backward compatibility only. It is no longer edited --
    the period in force is the ACTIVE RetentionPolicyVersion (below), and
    GET /api/retention-policy reads through to it. Migration 0019 copied
    this row into version 1.
    """

    __tablename__ = "retention_policies"

    id = Column(Integer, primary_key=True, index=True)

    name = Column(String(255), nullable=False)
    is_active = Column(Boolean, nullable=False, default=False)

    default_retention_days = Column(Integer, nullable=False, default=2555)  # ~7 years

    created_at = Column(
        DateTime, default=lambda: datetime.now(timezone.utc), nullable=False
    )
    updated_at = Column(
        DateTime,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
        nullable=False,
    )


class AssessmentRetention(Base):
    """
    Stage 16 (R16.1/R16.4): per-assessment legal hold and soft-delete
    state. One row per assessment, created lazily on first access -- same
    pattern as AssessmentChallenge/AssessmentFcrmReview.

    R16.1/AC5 ("no completed assessment history can be deleted without a
    controlled, logged process") is implemented as a SOFT delete: is_deleted
    flags the assessment as purged from normal views/exports, but no row
    in any table is ever physically removed by the application, so the
    underlying history remains intact and reconstructable if the flag is
    ever cleared by a direct database action.
    """

    __tablename__ = "assessment_retention"

    id = Column(Integer, primary_key=True, index=True)

    assessment_id = Column(
        Integer,
        ForeignKey("assessments.id"),
        nullable=False,
        unique=True,
        index=True,
    )

    legal_hold = Column(Boolean, nullable=False, default=False)
    legal_hold_reason = Column(Text, nullable=True)
    legal_hold_set_by = Column(String(255), nullable=True)
    legal_hold_set_at = Column(DateTime, nullable=True)
    # P5: who set the current hold (D-2: a different user releases it).
    # NULL on holds set before P5, which recorded a name only.
    legal_hold_set_by_id = Column(Integer, ForeignKey("users.id"), nullable=True)

    is_deleted = Column(Boolean, nullable=False, default=False)
    deleted_by = Column(String(255), nullable=True)
    deleted_at = Column(DateTime, nullable=True)
    deletion_reason = Column(Text, nullable=True)
    # P5: the policy version under which the record became eligible and
    # was soft-deleted.
    retention_policy_version_id = Column(Integer, ForeignKey("retention_policy_versions.id"), nullable=True)

    created_at = Column(
        DateTime, default=lambda: datetime.now(timezone.utc), nullable=False
    )
    updated_at = Column(
        DateTime,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
        nullable=False,
    )


def _now():
    return datetime.now(timezone.utc)


class RetentionPolicyVersion(Base):
    """
    P5 (R16.4): one version of the retention period for a record type.
    Append-only: a change is a new row. After insert only the decision and
    supersession fields may change, once each, along
    PROPOSED -> ACTIVE -> SUPERSEDED or PROPOSED -> REJECTED
    (app/services/data_protection.py). At most one ACTIVE and one PROPOSED
    version per record type (partial unique indexes).

    policy_status is always PROVISIONAL_PENDING_GOVERNANCE_APPROVAL. The
    governance_approval_* columns are for a later, separate approval
    process; the application never sets them.
    """

    __tablename__ = "retention_policy_versions"

    id = Column(Integer, primary_key=True, index=True)
    record_type = Column(String(40), nullable=False, index=True)
    version = Column(Integer, nullable=False)
    retention_days = Column(Integer, nullable=False)
    # What the period runs from (ASSESSMENT: FINAL_DECISION_DATE).
    basis = Column(String(40), nullable=False)
    # Set when the version becomes ACTIVE.
    effective_from = Column(DateTime, nullable=True)
    status = Column(String(20), nullable=False, index=True)
    policy_status = Column(String(60), nullable=False)

    change_reason = Column(Text, nullable=False)
    proposed_by_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    proposed_at = Column(DateTime, nullable=False, default=_now)
    decided_by_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    decided_at = Column(DateTime, nullable=True)
    decision_reason = Column(Text, nullable=True)

    supersedes_id = Column(Integer, ForeignKey("retention_policy_versions.id"), nullable=True)
    previous_retention_days = Column(Integer, nullable=True)
    superseded_at = Column(DateTime, nullable=True)
    superseded_by_id = Column(Integer, ForeignKey("retention_policy_versions.id"), nullable=True)

    # v1 copied from the pre-P5 setting by migration 0019 (or seeded).
    system_seeded = Column(Boolean, nullable=False, default=False)

    governance_approval_reference = Column(String(100), nullable=True)
    governance_approved_at = Column(DateTime, nullable=True)

    created_at = Column(DateTime, default=_now, nullable=False)
    # Optimistic locking: a second, concurrent decision fails.
    row_version = Column(Integer, nullable=False, default=1)

    __mapper_args__ = {"version_id_col": row_version}
    __table_args__ = (
        UniqueConstraint("record_type", "version", name="uq_retention_policy_version"),
        Index(
            "uq_retention_policy_one_active", "record_type", unique=True,
            sqlite_where=text("status = 'ACTIVE'"), postgresql_where=text("status = 'ACTIVE'"),
        ),
        Index(
            "uq_retention_policy_one_proposed", "record_type", unique=True,
            sqlite_where=text("status = 'PROPOSED'"), postgresql_where=text("status = 'PROPOSED'"),
        ),
    )


class LegalHoldEvent(Base):
    """P5 (R16.1/R16.4): append-only history of legal holds on one
    assessment. assessment_retention.legal_hold* is the current state,
    changed in the same transaction as the event."""

    __tablename__ = "legal_hold_events"

    id = Column(Integer, primary_key=True, index=True)
    assessment_id = Column(Integer, ForeignKey("assessments.id"), nullable=False, index=True)
    action = Column(String(20), nullable=False)  # SET | RELEASED
    reason = Column(Text, nullable=False)
    # Optional free-text matter / case reference -- never a document body.
    matter_reference = Column(String(100), nullable=True)
    actor_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    actor_name = Column(String(255), nullable=False)
    # Backfilled by migration 0019 from the pre-P5 hold columns.
    system_seeded = Column(Boolean, nullable=False, default=False)
    created_at = Column(DateTime, default=_now, nullable=False)


def soft_deleted_assessment_ids():
    """R16.4: a SELECT of the ids of soft-deleted assessments, for
    `Assessment.id.notin_(...)` filters on lists, queues and sweeps."""

    from sqlalchemy import select

    return select(AssessmentRetention.assessment_id).where(AssessmentRetention.is_deleted.is_(True))


def is_soft_deleted(db, assessment_id: int) -> bool:
    return (
        db.query(AssessmentRetention.id)
        .filter(AssessmentRetention.assessment_id == assessment_id, AssessmentRetention.is_deleted.is_(True))
        .first()
        is not None
    )

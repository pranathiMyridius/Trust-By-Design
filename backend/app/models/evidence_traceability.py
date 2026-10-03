from datetime import datetime, timezone

from sqlalchemy import Boolean, Column, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint

from app.database import Base


class IntakeSnapshot(Base):
    """
    P4 (R3.4): an append-only version of an assessment's intake record --
    the business request (ASSESSMENT_REQUEST) or the structured profile
    (BUSINESS_PROFILE) -- taken whenever either changes. `snapshot` is the
    full set of values after the change; `changes` lists each changed field
    with its old and new value. The first row for a record is the state
    before the first recorded change (trigger BASELINE), so the original
    submission stays available after corrections.
    """

    __tablename__ = "intake_snapshots"
    __table_args__ = (UniqueConstraint("assessment_id", "record_type", "version", name="uq_intake_snapshot_version"),)

    id = Column(Integer, primary_key=True, index=True)
    assessment_id = Column(Integer, ForeignKey("assessments.id"), nullable=False, index=True)
    # ASSESSMENT_REQUEST | BUSINESS_PROFILE
    record_type = Column(String(30), nullable=False)
    version = Column(Integer, nullable=False)
    # BASELINE | SUBMITTED | EDITED | PROFILE_CREATED | PROFILE_CORRECTED |
    # PROFILE_CONFIRMED | PROFILE_UNCONFIRMED
    trigger = Column(String(30), nullable=False)
    # JSON object: every tracked field's value after this change.
    snapshot = Column(Text, nullable=False)
    # JSON list of {"field", "old", "new"}; empty for BASELINE/confirmations.
    changes = Column(Text, nullable=False, default="[]")
    reason = Column(Text, nullable=True)
    # Whether the record had been validated (profile confirmed by the
    # business owner) when this change was made.
    was_validated = Column(Boolean, nullable=False, default=False)
    changed_by = Column(String(255), nullable=False)
    changed_by_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), nullable=False)


class EvidenceAcknowledgement(Base):
    """
    P4 (R2.6, Stage 2 AC): a user's decision about an expired document
    before it is used as current evidence -- USE_AS_EVIDENCE (with a
    reason) or EXCLUDE_FROM_EVIDENCE. Bound to the document version and
    the expiry date it was made against; append-only, the latest row for a
    document is the one in force.
    """

    __tablename__ = "evidence_acknowledgements"

    id = Column(Integer, primary_key=True, index=True)
    assessment_id = Column(Integer, ForeignKey("assessments.id"), nullable=False, index=True)
    document_id = Column(Integer, ForeignKey("assessment_documents.id"), nullable=False, index=True)
    document_version = Column(Integer, nullable=False)
    # The expiry date as stored on the document when acknowledged; an
    # acknowledgement made against a different date does not apply.
    expiry_date = Column(String(20), nullable=True)
    # EXPIRED | INVALID_EXPIRY_DATE
    condition = Column(String(30), nullable=False)
    # USE_AS_EVIDENCE | EXCLUDE_FROM_EVIDENCE
    decision = Column(String(30), nullable=False)
    reason = Column(Text, nullable=False)
    acknowledged_by = Column(String(255), nullable=False)
    acknowledged_by_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), nullable=False)

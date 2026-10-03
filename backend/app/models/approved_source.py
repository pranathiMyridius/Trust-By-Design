from datetime import datetime, timezone

from sqlalchemy import Boolean, Column, Date, DateTime, ForeignKey, Integer, String, Text

from app.database import Base


class ApprovedSource(Base):
    """
    R5.1/R5.2: an entry in the approved evidence-source library -- an
    internal policy, procedure or control standard, regulatory guidance,
    an approved risk framework, a previous assessment or vendor control
    documentation. Only APPROVED sources are searched for formal evidence;
    a DRAFT is still being prepared and a RETIRED one is kept for history.
    Maintained by a Policy Admin or Admin.
    """

    __tablename__ = "approved_sources"

    id = Column(Integer, primary_key=True, index=True)
    title = Column(String(255), nullable=False)
    # One of SOURCE_TYPES in app/services/source_library.py.
    source_type = Column(String(40), nullable=False)
    issuer = Column(String(255), nullable=True)
    version = Column(String(50), nullable=False)
    # Publication / effective date of this version.
    effective_date = Column(Date, nullable=True)
    # After this date the source is outdated and must be re-reviewed
    # (Stage 5 acceptance criteria: outdated policy evidence warns).
    review_date = Column(Date, nullable=True)
    # Link or document reference (URL, policy number, file path).
    reference = Column(String(500), nullable=True)
    content = Column(Text, nullable=False)

    # The original uploaded document, when the source was created from one
    # (R5.1/R5.3). Stored encrypted at rest (app/file_processing/storage.py);
    # set once at creation and never replaced -- a revision is a new source
    # version. NULL for sources whose text was pasted.
    original_filename = Column(String(255), nullable=True)
    file_path = Column(String(500), nullable=True)
    file_content_type = Column(String(100), nullable=True)
    file_size = Column(Integer, nullable=True)
    file_sha256 = Column(String(64), nullable=True)

    # DRAFT | APPROVED | RETIRED
    status = Column(String(20), nullable=False, default="DRAFT")
    approved_by = Column(String(255), nullable=True)
    approved_by_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    approved_at = Column(DateTime, nullable=True)
    created_by = Column(String(255), nullable=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), nullable=False)
    updated_at = Column(
        DateTime,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
        nullable=False,
    )


class SourceEvidenceLink(Base):
    """
    R5.3: a passage from an approved source that an analyst attached as
    evidence for a risk factor, frozen as retrieved -- source name and
    version, passage, effective date, reference and retrieval date -- so
    the evidence stays traceable even after the source is revised.
    """

    __tablename__ = "source_evidence_links"

    id = Column(Integer, primary_key=True, index=True)
    assessment_id = Column(Integer, ForeignKey("assessments.id"), nullable=False, index=True)
    risk_factor_id = Column(Integer, ForeignKey("risk_factors.id"), nullable=True, index=True)
    source_id = Column(Integer, ForeignKey("approved_sources.id"), nullable=False)

    source_title = Column(String(255), nullable=False)
    source_type = Column(String(40), nullable=False)
    source_version = Column(String(50), nullable=False)
    effective_date = Column(Date, nullable=True)
    reference = Column(String(500), nullable=True)
    passage = Column(Text, nullable=False)
    retrieved_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), nullable=False)
    retrieved_by = Column(String(255), nullable=True)
    retrieved_by_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    # P4 (Stage 5 AC): whether the source was past its review date when
    # attached, and the acknowledgement that was required to attach it.
    outdated_at_attach = Column(Boolean, nullable=False, default=False)
    outdated_acknowledgement_reason = Column(Text, nullable=True)
    # A user id; a plain integer (no FK) because migration 0020 adds it in
    # place, which SQLite can't do with a constraint.
    outdated_acknowledged_by_id = Column(Integer, nullable=True)

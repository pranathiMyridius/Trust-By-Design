"""
Source Library: governed regulatory and internal-policy sources.

A *source record* is the library entry (an RBI Direction, an internal KYC
procedure ...). It owns any number of *versions*; each version is a link
and/or an uploaded PDF with its own review lifecycle:

    DRAFT -> IN_REVIEW -> APPROVED -> SUPERSEDED | RETIRED
                      \\-> REJECTED -> (edited) DRAFT

At most one version of a record is APPROVED at a time (partial unique
index). A new version never replaces it automatically: the approved
version is superseded only at the moment a reviewer approves its
successor. Every submission, decision and status change is an
append-only row in `source_approvals` / `source_audit_logs`.

Text extracted from a version's PDF is split into `source_chunks`
(keeping page and section), each embedded into `source_embeddings`
(pgvector on Postgres; a JSON array on SQLite, which only tests and local
development use).

These tables are separate from the older flat `approved_sources` table,
which the evidence-attachment flow still reads. An approved version with
text is mirrored into it (see app/services/source_governance.py).
"""

import uuid
from datetime import datetime, timezone

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    JSON,
    Column,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    text,
)
from sqlalchemy.orm import relationship

from app.ai.embeddings import EMBEDDING_DIM
from app.database import Base


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _uuid_pk() -> Column:
    return Column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)


class SourceRecord(Base):
    __tablename__ = "source_records"

    id = _uuid_pk()
    # Stable human identifier, e.g. SRC-RBI-KYC-001.
    source_code = Column(String(64), nullable=False, unique=True, index=True)
    title = Column(String(255), nullable=False)
    authority = Column(String(255), nullable=False)
    category = Column(String(40), nullable=False, index=True)
    jurisdiction = Column(String(100), nullable=True, index=True)
    applicable_entity = Column(String(255), nullable=True)
    source_url = Column(String(1000), nullable=True)
    description = Column(Text, nullable=True)
    # JSON list of topic tags (also used to rank retrieval).
    topics = Column(JSON, nullable=False, default=list)
    owner = Column(String(255), nullable=True)
    review_frequency = Column(String(100), nullable=True)
    # ACTIVE | RETIRED. Whether the *record* is in use; the status of the
    # content is on its versions.
    status = Column(String(20), nullable=False, default="ACTIVE", index=True)
    created_by = Column(String(255), nullable=True)
    created_by_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    created_at = Column(DateTime, default=_now, nullable=False)
    updated_at = Column(DateTime, default=_now, onupdate=_now, nullable=False)

    versions = relationship(
        "SourceVersion", back_populates="record", order_by="SourceVersion.version_number", lazy="select"
    )


class SourceVersion(Base):
    __tablename__ = "source_versions"
    __table_args__ = (
        UniqueConstraint("record_id", "version_number", name="uq_source_versions_record_number"),
        # One approved version per record, enforced by the database.
        Index(
            "uq_source_versions_one_approved",
            "record_id",
            unique=True,
            sqlite_where=text("status = 'APPROVED'"),
            postgresql_where=text("status = 'APPROVED'"),
        ),
    )

    id = _uuid_pk()
    record_id = Column(Uuid(as_uuid=True), ForeignKey("source_records.id"), nullable=False, index=True)
    version_number = Column(Integer, nullable=False)
    # What the publisher calls it ("2016, updated 2025-06-12", "v3.1" ...).
    version_label = Column(String(100), nullable=False)
    effective_date = Column(Date, nullable=True)
    review_date = Column(Date, nullable=True)
    # When a controlled copy was downloaded / captured from the publisher.
    retrieved_date = Column(Date, nullable=True)
    source_url = Column(String(1000), nullable=True)
    change_summary = Column(Text, nullable=True)

    # DRAFT | IN_REVIEW | APPROVED | REJECTED | SUPERSEDED | RETIRED
    status = Column(String(20), nullable=False, default="DRAFT", index=True)

    # The uploaded PDF (private, encrypted at rest when a key is set).
    original_filename = Column(String(255), nullable=True)
    storage_key = Column(String(500), nullable=True)
    content_type = Column(String(100), nullable=True)
    file_size = Column(Integer, nullable=True)
    file_sha256 = Column(String(64), nullable=True, index=True)
    page_count = Column(Integer, nullable=True)

    # NONE (link only) | PENDING | PROCESSED | FAILED
    processing_status = Column(String(20), nullable=False, default="NONE")
    processing_error = Column(Text, nullable=True)
    # NOT_SCANNED | CLEAN | INFECTED | ERROR  (+ the scanner that decided)
    scan_status = Column(String(20), nullable=False, default="NOT_SCANNED")
    scan_detail = Column(String(255), nullable=True)
    extracted_text = Column(Text, nullable=True)
    text_sha256 = Column(String(64), nullable=True)
    chunk_count = Column(Integer, nullable=False, default=0)
    # NONE | COMPLETE | PARTIAL | UNAVAILABLE
    embedding_status = Column(String(20), nullable=False, default="NONE")

    # The row in the older approved_sources table this version is mirrored
    # to (and, for sources created through the older API, came from).
    legacy_source_id = Column(Integer, ForeignKey("approved_sources.id"), nullable=True, unique=True)

    created_by = Column(String(255), nullable=True)
    created_by_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    submitted_by = Column(String(255), nullable=True)
    submitted_by_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    submitted_at = Column(DateTime, nullable=True)
    decided_by = Column(String(255), nullable=True)
    decided_by_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    decided_at = Column(DateTime, nullable=True)
    decision_comment = Column(Text, nullable=True)
    superseded_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=_now, nullable=False)
    updated_at = Column(DateTime, default=_now, onupdate=_now, nullable=False)

    record = relationship("SourceRecord", back_populates="versions")


class SourceApproval(Base):
    """Append-only: one row per submission or decision on a version."""

    __tablename__ = "source_approvals"

    id = _uuid_pk()
    record_id = Column(Uuid(as_uuid=True), ForeignKey("source_records.id"), nullable=False, index=True)
    version_id = Column(Uuid(as_uuid=True), ForeignKey("source_versions.id"), nullable=False, index=True)
    # SUBMITTED | WITHDRAWN | APPROVED | REJECTED | RETIRED
    action = Column(String(20), nullable=False)
    from_status = Column(String(20), nullable=True)
    to_status = Column(String(20), nullable=False)
    actor = Column(String(255), nullable=False)
    actor_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    comment = Column(Text, nullable=True)
    created_at = Column(DateTime, default=_now, nullable=False, index=True)


class SourceAuditLog(Base):
    """Append-only timeline of everything done to a record or version."""

    __tablename__ = "source_audit_logs"

    id = _uuid_pk()
    record_id = Column(Uuid(as_uuid=True), ForeignKey("source_records.id"), nullable=True, index=True)
    version_id = Column(Uuid(as_uuid=True), ForeignKey("source_versions.id"), nullable=True, index=True)
    event_type = Column(String(40), nullable=False, index=True)
    actor = Column(String(255), nullable=False)
    actor_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    details = Column(Text, nullable=True)
    # Structured before/after values; never document text.
    event_data = Column(JSON, nullable=True)
    created_at = Column(DateTime, default=_now, nullable=False, index=True)


class SourceChunk(Base):
    __tablename__ = "source_chunks"
    __table_args__ = (UniqueConstraint("version_id", "chunk_index", name="uq_source_chunks_version_index"),)

    id = _uuid_pk()
    version_id = Column(Uuid(as_uuid=True), ForeignKey("source_versions.id"), nullable=False, index=True)
    # Denormalised so retrieval can filter without a join through versions.
    record_id = Column(Uuid(as_uuid=True), ForeignKey("source_records.id"), nullable=False, index=True)
    chunk_index = Column(Integer, nullable=False)
    text = Column(Text, nullable=False)
    page_start = Column(Integer, nullable=True)
    page_end = Column(Integer, nullable=True)
    section = Column(String(255), nullable=True)
    text_sha256 = Column(String(64), nullable=False)
    created_at = Column(DateTime, default=_now, nullable=False)


class SourceEmbedding(Base):
    __tablename__ = "source_embeddings"

    id = _uuid_pk()
    chunk_id = Column(Uuid(as_uuid=True), ForeignKey("source_chunks.id"), nullable=False, unique=True)
    model = Column(String(100), nullable=True)
    dimensions = Column(Integer, nullable=False)
    # pgvector on Postgres; a JSON array on SQLite (no vector type there).
    embedding = Column(Vector(EMBEDDING_DIM).with_variant(JSON(), "sqlite"), nullable=False)
    created_at = Column(DateTime, default=_now, nullable=False)

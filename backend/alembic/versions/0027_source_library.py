"""Source Library: versioned, governed regulatory and policy sources.

Adds (all additive; nothing existing is altered or deleted):

* source_records, source_versions, source_approvals, source_audit_logs,
  source_chunks, source_embeddings -- UUID primary keys, foreign keys,
  indexes, and a partial unique index allowing one APPROVED version per
  record.
* risk_factors.source_citations -- nullable JSON text: the verified
  library citations attached to an AI-identified factor.
* A backfill that gives every row of the older approved_sources table a
  record and a version (linked through source_versions.legacy_source_id),
  so existing sources appear in the new library with their status and
  history intact. The older rows themselves are not modified.

Downgrade refuses while any version has a stored file or any approval row
beyond the migrated ones, rather than lose governance history.

Revision ID: 0027_source_library
Revises: 0026_ai_challenge
Create Date: 2026-10-06
"""
import hashlib
import uuid
from datetime import datetime, timezone
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0027_source_library"
down_revision: Union[str, None] = "0026_ai_challenge"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

TABLES = [
    "source_embeddings",
    "source_chunks",
    "source_audit_logs",
    "source_approvals",
    "source_versions",
    "source_records",
]

_LEGACY_CATEGORY = {
    "INTERNAL_POLICY": "INTERNAL_POLICY",
    "PROCEDURE": "PROCEDURE",
    "CONTROL_STANDARD": "CONTROL_LIBRARY",
    "REGULATORY_GUIDANCE": "REGULATORY_GUIDANCE",
    "RISK_FRAMEWORK": "RISK_METHODOLOGY",
    "PREVIOUS_ASSESSMENT": "PREVIOUS_ASSESSMENT",
    "VENDOR_CONTROL_DOCUMENTATION": "VENDOR_DOCUMENT",
}


def _uuid():
    return sa.Uuid(as_uuid=True)


def _embedding_type(is_postgres: bool):
    if not is_postgres:
        return sa.JSON()
    from pgvector.sqlalchemy import Vector

    from app.ai.embeddings import EMBEDDING_DIM

    return Vector(EMBEDDING_DIM)


def _create_tables(bind) -> None:
    existing = set(sa.inspect(bind).get_table_names())
    is_postgres = bind.dialect.name == "postgresql"

    if "source_records" not in existing:
        op.create_table(
            "source_records",
            sa.Column("id", _uuid(), primary_key=True),
            sa.Column("source_code", sa.String(64), nullable=False),
            sa.Column("title", sa.String(255), nullable=False),
            sa.Column("authority", sa.String(255), nullable=False),
            sa.Column("category", sa.String(40), nullable=False),
            sa.Column("jurisdiction", sa.String(100), nullable=True),
            sa.Column("applicable_entity", sa.String(255), nullable=True),
            sa.Column("source_url", sa.String(1000), nullable=True),
            sa.Column("description", sa.Text(), nullable=True),
            sa.Column("topics", sa.JSON(), nullable=False),
            sa.Column("owner", sa.String(255), nullable=True),
            sa.Column("review_frequency", sa.String(100), nullable=True),
            sa.Column("status", sa.String(20), nullable=False, server_default="ACTIVE"),
            sa.Column("created_by", sa.String(255), nullable=True),
            sa.Column("created_by_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), nullable=False),
        )
        op.create_index("ix_source_records_source_code", "source_records", ["source_code"], unique=True)
        op.create_index("ix_source_records_category", "source_records", ["category"])
        op.create_index("ix_source_records_jurisdiction", "source_records", ["jurisdiction"])
        op.create_index("ix_source_records_status", "source_records", ["status"])

    if "source_versions" not in existing:
        op.create_table(
            "source_versions",
            sa.Column("id", _uuid(), primary_key=True),
            sa.Column("record_id", _uuid(), sa.ForeignKey("source_records.id"), nullable=False),
            sa.Column("version_number", sa.Integer(), nullable=False),
            sa.Column("version_label", sa.String(100), nullable=False),
            sa.Column("effective_date", sa.Date(), nullable=True),
            sa.Column("review_date", sa.Date(), nullable=True),
            sa.Column("retrieved_date", sa.Date(), nullable=True),
            sa.Column("source_url", sa.String(1000), nullable=True),
            sa.Column("change_summary", sa.Text(), nullable=True),
            sa.Column("status", sa.String(20), nullable=False, server_default="DRAFT"),
            sa.Column("original_filename", sa.String(255), nullable=True),
            sa.Column("storage_key", sa.String(500), nullable=True),
            sa.Column("content_type", sa.String(100), nullable=True),
            sa.Column("file_size", sa.Integer(), nullable=True),
            sa.Column("file_sha256", sa.String(64), nullable=True),
            sa.Column("page_count", sa.Integer(), nullable=True),
            sa.Column("processing_status", sa.String(20), nullable=False, server_default="NONE"),
            sa.Column("processing_error", sa.Text(), nullable=True),
            sa.Column("scan_status", sa.String(20), nullable=False, server_default="NOT_SCANNED"),
            sa.Column("scan_detail", sa.String(255), nullable=True),
            sa.Column("extracted_text", sa.Text(), nullable=True),
            sa.Column("text_sha256", sa.String(64), nullable=True),
            sa.Column("chunk_count", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("embedding_status", sa.String(20), nullable=False, server_default="NONE"),
            sa.Column("legacy_source_id", sa.Integer(), sa.ForeignKey("approved_sources.id"), nullable=True),
            sa.Column("created_by", sa.String(255), nullable=True),
            sa.Column("created_by_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
            sa.Column("submitted_by", sa.String(255), nullable=True),
            sa.Column("submitted_by_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
            sa.Column("submitted_at", sa.DateTime(), nullable=True),
            sa.Column("decided_by", sa.String(255), nullable=True),
            sa.Column("decided_by_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
            sa.Column("decided_at", sa.DateTime(), nullable=True),
            sa.Column("decision_comment", sa.Text(), nullable=True),
            sa.Column("superseded_at", sa.DateTime(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), nullable=False),
            sa.UniqueConstraint("record_id", "version_number", name="uq_source_versions_record_number"),
            sa.UniqueConstraint("legacy_source_id", name="uq_source_versions_legacy_source"),
        )
        op.create_index("ix_source_versions_record_id", "source_versions", ["record_id"])
        op.create_index("ix_source_versions_status", "source_versions", ["status"])
        op.create_index("ix_source_versions_file_sha256", "source_versions", ["file_sha256"])
        op.create_index(
            "uq_source_versions_one_approved",
            "source_versions",
            ["record_id"],
            unique=True,
            sqlite_where=sa.text("status = 'APPROVED'"),
            postgresql_where=sa.text("status = 'APPROVED'"),
        )

    if "source_approvals" not in existing:
        op.create_table(
            "source_approvals",
            sa.Column("id", _uuid(), primary_key=True),
            sa.Column("record_id", _uuid(), sa.ForeignKey("source_records.id"), nullable=False),
            sa.Column("version_id", _uuid(), sa.ForeignKey("source_versions.id"), nullable=False),
            sa.Column("action", sa.String(20), nullable=False),
            sa.Column("from_status", sa.String(20), nullable=True),
            sa.Column("to_status", sa.String(20), nullable=False),
            sa.Column("actor", sa.String(255), nullable=False),
            sa.Column("actor_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
            sa.Column("comment", sa.Text(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False),
        )
        op.create_index("ix_source_approvals_record_id", "source_approvals", ["record_id"])
        op.create_index("ix_source_approvals_version_id", "source_approvals", ["version_id"])
        op.create_index("ix_source_approvals_created_at", "source_approvals", ["created_at"])

    if "source_audit_logs" not in existing:
        op.create_table(
            "source_audit_logs",
            sa.Column("id", _uuid(), primary_key=True),
            sa.Column("record_id", _uuid(), sa.ForeignKey("source_records.id"), nullable=True),
            sa.Column("version_id", _uuid(), sa.ForeignKey("source_versions.id"), nullable=True),
            sa.Column("event_type", sa.String(40), nullable=False),
            sa.Column("actor", sa.String(255), nullable=False),
            sa.Column("actor_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
            sa.Column("details", sa.Text(), nullable=True),
            sa.Column("event_data", sa.JSON(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False),
        )
        op.create_index("ix_source_audit_logs_record_id", "source_audit_logs", ["record_id"])
        op.create_index("ix_source_audit_logs_version_id", "source_audit_logs", ["version_id"])
        op.create_index("ix_source_audit_logs_event_type", "source_audit_logs", ["event_type"])
        op.create_index("ix_source_audit_logs_created_at", "source_audit_logs", ["created_at"])

    if "source_chunks" not in existing:
        op.create_table(
            "source_chunks",
            sa.Column("id", _uuid(), primary_key=True),
            sa.Column("version_id", _uuid(), sa.ForeignKey("source_versions.id"), nullable=False),
            sa.Column("record_id", _uuid(), sa.ForeignKey("source_records.id"), nullable=False),
            sa.Column("chunk_index", sa.Integer(), nullable=False),
            sa.Column("text", sa.Text(), nullable=False),
            sa.Column("page_start", sa.Integer(), nullable=True),
            sa.Column("page_end", sa.Integer(), nullable=True),
            sa.Column("section", sa.String(255), nullable=True),
            sa.Column("text_sha256", sa.String(64), nullable=False),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.UniqueConstraint("version_id", "chunk_index", name="uq_source_chunks_version_index"),
        )
        op.create_index("ix_source_chunks_version_id", "source_chunks", ["version_id"])
        op.create_index("ix_source_chunks_record_id", "source_chunks", ["record_id"])

    if "source_embeddings" not in existing:
        op.create_table(
            "source_embeddings",
            sa.Column("id", _uuid(), primary_key=True),
            sa.Column("chunk_id", _uuid(), sa.ForeignKey("source_chunks.id"), nullable=False, unique=True),
            sa.Column("model", sa.String(100), nullable=True),
            sa.Column("dimensions", sa.Integer(), nullable=False),
            sa.Column("embedding", _embedding_type(is_postgres), nullable=False),
            sa.Column("created_at", sa.DateTime(), nullable=False),
        )
        if is_postgres:
            # Approximate nearest-neighbour index for cosine search. Best
            # effort: the search is correct (just slower) without it.
            try:
                with bind.begin_nested():
                    op.execute(
                        "CREATE INDEX ix_source_embeddings_hnsw ON source_embeddings "
                        "USING hnsw (embedding vector_cosine_ops)"
                    )
            except Exception:  # noqa: BLE001 -- e.g. an older pgvector without HNSW
                pass


def _add_citations_column(bind) -> None:
    columns = {column["name"] for column in sa.inspect(bind).get_columns("risk_factors")}
    if "source_citations" not in columns:
        op.add_column("risk_factors", sa.Column("source_citations", sa.Text(), nullable=True))


def _backfill_from_legacy(bind) -> None:
    """One record + one version per approved_sources row not yet linked."""

    def col(name, type_=None):
        return sa.column(name, type_) if type_ is not None else sa.column(name)

    U, D, T, J = sa.Uuid(as_uuid=True), sa.Date(), sa.DateTime(), sa.JSON()
    legacy = sa.table(
        "approved_sources",
        col("id"), col("title"), col("source_type"), col("issuer"), col("version"),
        col("effective_date", D), col("review_date", D), col("reference"), col("content"),
        col("original_filename"), col("file_path"), col("file_content_type"), col("file_size"),
        col("file_sha256"), col("status"), col("approved_by"), col("approved_by_id"),
        col("approved_at", T), col("created_by"), col("created_at", T), col("updated_at", T),
    )
    records = sa.table(
        "source_records",
        col("id", U), col("source_code"), col("title"), col("authority"), col("category"),
        col("jurisdiction"), col("applicable_entity"), col("source_url"), col("description"),
        col("topics", J), col("owner"), col("review_frequency"), col("status"), col("created_by"),
        col("created_by_id"), col("created_at", T), col("updated_at", T),
    )
    versions = sa.table(
        "source_versions",
        col("id", U), col("record_id", U), col("version_number"), col("version_label"),
        col("effective_date", D), col("review_date", D), col("source_url"), col("change_summary"),
        col("status"), col("original_filename"), col("storage_key"), col("content_type"),
        col("file_size"), col("file_sha256"), col("processing_status"), col("scan_status"),
        col("scan_detail"), col("extracted_text"), col("text_sha256"), col("chunk_count"),
        col("embedding_status"), col("legacy_source_id"), col("created_by"), col("submitted_by"),
        col("submitted_at", T), col("decided_by"), col("decided_by_id"), col("decided_at", T),
        col("decision_comment"), col("created_at", T), col("updated_at", T),
    )
    approvals = sa.table(
        "source_approvals",
        col("id", U), col("record_id", U), col("version_id", U), col("action"), col("from_status"),
        col("to_status"), col("actor"), col("actor_id"), col("comment"), col("created_at", T),
    )
    audits = sa.table(
        "source_audit_logs",
        col("id", U), col("record_id", U), col("version_id", U), col("event_type"), col("actor"),
        col("details"), col("event_data", J), col("created_at", T),
    )

    already = {
        row[0]
        for row in bind.execute(sa.text("SELECT legacy_source_id FROM source_versions WHERE legacy_source_id IS NOT NULL"))
    }
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    for row in bind.execute(sa.select(legacy).order_by(legacy.c.id)).mappings():
        # Help articles for workbench users are not regulatory sources.
        if row["id"] in already or row["source_type"] == "USER_GUIDE":
            continue
        record_id, version_id = uuid.uuid4(), uuid.uuid4()
        reference = (row["reference"] or "").strip()
        is_url = reference.lower().startswith(("http://", "https://"))
        text = row["content"] or ""
        created = row["created_at"] or now
        status = row["status"] if row["status"] in {"DRAFT", "APPROVED", "RETIRED"} else "DRAFT"
        bind.execute(records.insert().values(
            id=record_id,
            source_code=f"SRC-LEG-{row['id']:04d}",
            title=row["title"],
            authority=row["issuer"] or "Not recorded",
            category=_LEGACY_CATEGORY.get(row["source_type"], "REGULATORY_GUIDANCE"),
            jurisdiction=None,
            applicable_entity=None,
            source_url=reference if is_url else None,
            description=None if is_url or not reference else f"Reference: {reference}",
            topics=[],
            owner=row["created_by"],
            review_frequency=None,
            status="RETIRED" if status == "RETIRED" else "ACTIVE",
            created_by=row["created_by"],
            created_by_id=None,
            created_at=created,
            updated_at=row["updated_at"] or created,
        ))
        bind.execute(versions.insert().values(
            id=version_id,
            record_id=record_id,
            version_number=1,
            version_label=row["version"],
            effective_date=row["effective_date"],
            review_date=row["review_date"],
            source_url=reference if is_url else None,
            change_summary="Migrated from the earlier approved-source library.",
            status=status,
            original_filename=row["original_filename"],
            storage_key=None,
            content_type=row["file_content_type"],
            file_size=row["file_size"],
            file_sha256=row["file_sha256"],
            processing_status="PROCESSED" if text.strip() else "NONE",
            scan_status="NOT_SCANNED",
            scan_detail="Migrated record; not scanned by this module.",
            extracted_text=text or None,
            text_sha256=hashlib.sha256(text.encode("utf-8")).hexdigest() if text else None,
            chunk_count=0,
            embedding_status="NONE",
            legacy_source_id=row["id"],
            created_by=row["created_by"],
            submitted_by=row["created_by"] if status != "DRAFT" else None,
            submitted_at=created if status != "DRAFT" else None,
            decided_by=row["approved_by"] if status != "DRAFT" else None,
            decided_by_id=row["approved_by_id"] if status != "DRAFT" else None,
            decided_at=row["approved_at"] if status != "DRAFT" else None,
            decision_comment="Migrated from the earlier library." if status != "DRAFT" else None,
            created_at=created,
            updated_at=row["updated_at"] or created,
        ))
        if status in {"APPROVED", "RETIRED"} and row["approved_by"]:
            bind.execute(approvals.insert().values(
                id=uuid.uuid4(), record_id=record_id, version_id=version_id, action="APPROVED",
                from_status="DRAFT", to_status="APPROVED", actor=row["approved_by"],
                actor_id=row["approved_by_id"],
                comment="Approved in the earlier library; carried over by migration 0027.",
                created_at=row["approved_at"] or created,
            ))
        bind.execute(audits.insert().values(
            id=uuid.uuid4(), record_id=record_id, version_id=version_id, event_type="MIGRATED",
            actor="System",
            details=f"Created from approved source #{row['id']} by migration 0027 (status {status}).",
            event_data={"legacy_source_id": row["id"], "status": status},
            created_at=now,
        ))


def upgrade() -> None:
    bind = op.get_bind()
    _create_tables(bind)
    _add_citations_column(bind)
    if "approved_sources" in sa.inspect(bind).get_table_names():
        _backfill_from_legacy(bind)


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    names = set(inspector.get_table_names())
    if "source_versions" in names:
        stored = bind.execute(sa.text("SELECT COUNT(*) FROM source_versions WHERE storage_key IS NOT NULL")).scalar()
        if stored:
            raise RuntimeError(f"{stored} source version(s) have a stored document; downgrading 0027 would orphan it.")
    if "source_approvals" in names:
        extra = bind.execute(
            sa.text("SELECT COUNT(*) FROM source_approvals WHERE comment IS NULL OR comment NOT LIKE 'Approved in the earlier library%'")
        ).scalar()
        if extra:
            raise RuntimeError(f"{extra} approval/submission record(s) would be lost by downgrading 0027.")
    for table in TABLES:
        if table in names:
            op.drop_table(table)
    if "risk_factors" in names:
        columns = {column["name"] for column in inspector.get_columns("risk_factors")}
        if "source_citations" in columns:
            op.drop_column("risk_factors", "source_citations")

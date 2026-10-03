"""P4: evidence traceability.

* intake_snapshots (new, append-only): versioned snapshots of the business
  request and the structured profile, with old/new values and reason.
* evidence_acknowledgements (new, append-only): decisions on expired
  documents before they are used as evidence.
* assessment_intelligence: field_provenance, extraction_method,
  extracted_at, source_document_ids (all nullable; existing profiles keep
  NULL -- their provenance was never recorded and is not invented).
* risk_factors.rule_triggers (nullable): fixed Stage 4 rules applied.
* source_evidence_links: outdated_at_attach (default false),
  outdated_acknowledgement_reason, outdated_acknowledged_by_id.

No backfill. Downgrade refuses while any snapshot, acknowledgement,
recorded provenance, rule trigger or outdated-source acknowledgement
exists, rather than lose it.

Revision ID: 0020_evidence_traceability
Revises: 0019_retention_lifecycle
Create Date: 2026-10-02
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0020_evidence_traceability"
down_revision: Union[str, None] = "0019_retention_lifecycle"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _added() -> dict[str, list[sa.Column]]:
    # Plain integers (no FK constraint): SQLite can't add one in place.
    return {
        "assessment_intelligence": [
            sa.Column("field_provenance", sa.Text(), nullable=True),
            sa.Column("extraction_method", sa.String(length=20), nullable=True),
            sa.Column("extracted_at", sa.DateTime(), nullable=True),
            sa.Column("source_document_ids", sa.Text(), nullable=True),
        ],
        "risk_factors": [sa.Column("rule_triggers", sa.Text(), nullable=True)],
        "source_evidence_links": [
            sa.Column("outdated_at_attach", sa.Boolean(), nullable=False, server_default=sa.false()),
            sa.Column("outdated_acknowledgement_reason", sa.Text(), nullable=True),
            sa.Column("outdated_acknowledged_by_id", sa.Integer(), nullable=True),
        ],
    }


def _columns(bind, table: str) -> set[str]:
    return {column["name"] for column in sa.inspect(bind).get_columns(table)}


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if not inspector.has_table("intake_snapshots"):
        op.create_table(
            "intake_snapshots",
            sa.Column("id", sa.Integer(), primary_key=True, index=True),
            sa.Column("assessment_id", sa.Integer(), sa.ForeignKey("assessments.id"), nullable=False, index=True),
            sa.Column("record_type", sa.String(length=30), nullable=False),
            sa.Column("version", sa.Integer(), nullable=False),
            sa.Column("trigger", sa.String(length=30), nullable=False),
            sa.Column("snapshot", sa.Text(), nullable=False),
            sa.Column("changes", sa.Text(), nullable=False),
            sa.Column("reason", sa.Text(), nullable=True),
            sa.Column("was_validated", sa.Boolean(), nullable=False, server_default=sa.false()),
            sa.Column("changed_by", sa.String(length=255), nullable=False),
            sa.Column("changed_by_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.UniqueConstraint("assessment_id", "record_type", "version", name="uq_intake_snapshot_version"),
        )

    if not inspector.has_table("evidence_acknowledgements"):
        op.create_table(
            "evidence_acknowledgements",
            sa.Column("id", sa.Integer(), primary_key=True, index=True),
            sa.Column("assessment_id", sa.Integer(), sa.ForeignKey("assessments.id"), nullable=False, index=True),
            sa.Column("document_id", sa.Integer(), sa.ForeignKey("assessment_documents.id"), nullable=False, index=True),
            sa.Column("document_version", sa.Integer(), nullable=False),
            sa.Column("expiry_date", sa.String(length=20), nullable=True),
            sa.Column("condition", sa.String(length=30), nullable=False),
            sa.Column("decision", sa.String(length=30), nullable=False),
            sa.Column("reason", sa.Text(), nullable=False),
            sa.Column("acknowledged_by", sa.String(length=255), nullable=False),
            sa.Column("acknowledged_by_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False),
        )

    for table, columns in _added().items():
        existing = _columns(bind, table)
        for column in columns:
            if column.name not in existing:
                op.add_column(table, column)


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    checks = []
    for table, what in (("intake_snapshots", "intake snapshot(s)"), ("evidence_acknowledgements", "evidence acknowledgement(s)")):
        if inspector.has_table(table):
            checks.append((what, f"SELECT COUNT(*) FROM {table}"))
    if "field_provenance" in _columns(bind, "assessment_intelligence"):
        checks.append(("profile(s) with recorded field provenance",
                       "SELECT COUNT(*) FROM assessment_intelligence WHERE field_provenance IS NOT NULL"))
    if "rule_triggers" in _columns(bind, "risk_factors"):
        checks.append(("risk factor(s) with Stage 4 rule triggers", "SELECT COUNT(*) FROM risk_factors WHERE rule_triggers IS NOT NULL"))
    if "outdated_acknowledgement_reason" in _columns(bind, "source_evidence_links"):
        checks.append(("outdated-source acknowledgement(s)",
                       "SELECT COUNT(*) FROM source_evidence_links WHERE outdated_acknowledgement_reason IS NOT NULL"))
    for what, sql in checks:
        count = bind.execute(sa.text(sql)).scalar()
        if count:
            raise RuntimeError(f"{count} {what} exist; downgrading 0020 would discard evidence traceability history.")

    # Plain DROP COLUMN (SQLite >= 3.35, Postgres): none of these columns is
    # indexed or constrained, and it avoids rebuilding the tables.
    for table, columns in _added().items():
        existing = _columns(bind, table)
        for column in reversed(columns):
            if column.name in existing:
                op.drop_column(table, column.name)
    for table in ("evidence_acknowledgements", "intake_snapshots"):
        if inspector.has_table(table):
            op.drop_table(table)

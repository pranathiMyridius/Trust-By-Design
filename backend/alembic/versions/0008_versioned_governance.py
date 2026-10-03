"""Methodology versioning, reference-data snapshots, residual grid, decision records.

* risk_methodologies: version, lineage, lock and approval columns, plus
  the mitigant categories and residual grid as configuration.
* reference_data_snapshots (new): one row per loaded publication, with a
  checksum and a separate reviewer attestation; country_risk rows point
  at theirs via snapshot_id.
* inherent_risk_calculations: the methodology version/fingerprint and
  reference-data snapshots each result used.
* residual_risk_calculations (new): versioned residual lookups.
* decision_records (new): the reproducibility record frozen at approval.

Existing rows are not backfilled. Existing methodologies become version 1
and unlocked (nothing recorded which calculations used them); existing
country_risk rows keep snapshot_id NULL and so are never treated as
attested.

Revision ID: 0008_versioned_governance
Revises: 0007_evidence_rules
Create Date: 2026-09-28
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0008_versioned_governance"
down_revision: Union[str, None] = "0007_evidence_rules"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _added_columns() -> dict[str, list[sa.Column]]:
    return {
        "risk_methodologies": [
            sa.Column("mitigant_categories", sa.Text(), nullable=True),
            sa.Column("residual_grid", sa.Text(), nullable=True),
            sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
            sa.Column("parent_id", sa.Integer(), nullable=True),
            sa.Column("change_reason", sa.Text(), nullable=True),
            sa.Column("locked_at", sa.DateTime(), nullable=True),
            sa.Column("approved_by", sa.String(length=255), nullable=True),
            sa.Column("approved_at", sa.DateTime(), nullable=True),
            sa.Column("approval_reason", sa.Text(), nullable=True),
            sa.Column("effective_from", sa.DateTime(), nullable=True),
            sa.Column("retired_at", sa.DateTime(), nullable=True),
        ],
        "country_risk": [
            sa.Column("snapshot_id", sa.Integer(), nullable=True),
        ],
        "inherent_risk_calculations": [
            sa.Column("methodology_version", sa.String(length=30), nullable=True),
            sa.Column("methodology_fingerprint", sa.String(length=80), nullable=True),
            sa.Column("reference_data", sa.Text(), nullable=True),
            sa.Column("jurisdiction_matches", sa.Text(), nullable=True),
        ],
    }


def _new_tables() -> dict[str, list]:
    return {
        "reference_data_snapshots": [
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("source", sa.String(length=50), nullable=False, index=True),
            sa.Column("as_of", sa.String(length=20), nullable=False),
            sa.Column("source_url", sa.Text(), nullable=True),
            sa.Column("statement", sa.Text(), nullable=True),
            sa.Column("checksum", sa.String(length=80), nullable=False),
            sa.Column("entry_count", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("source_claims_verified", sa.Boolean(), nullable=False, server_default=sa.false()),
            sa.Column("source_verification_note", sa.Text(), nullable=True),
            sa.Column("attested_by", sa.String(length=255), nullable=True),
            sa.Column("attested_at", sa.DateTime(), nullable=True),
            sa.Column("attestation_note", sa.Text(), nullable=True),
            sa.Column("loaded_by", sa.String(length=255), nullable=True),
            sa.Column("loaded_at", sa.DateTime(), nullable=False),
            sa.Column("is_current", sa.Boolean(), nullable=False, server_default=sa.true(), index=True),
            sa.Column("superseded_at", sa.DateTime(), nullable=True),
        ],
        "residual_risk_calculations": [
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("assessment_id", sa.Integer(), sa.ForeignKey("assessments.id"), nullable=False, index=True),
            sa.Column("inherent_calculation_id", sa.Integer(), nullable=True),
            sa.Column("methodology_id", sa.Integer(), nullable=True),
            sa.Column("methodology_version", sa.String(length=30), nullable=True),
            sa.Column("methodology_fingerprint", sa.String(length=80), nullable=True),
            sa.Column("inherent_band", sa.String(length=30), nullable=True),
            sa.Column("inherent_score", sa.Float(), nullable=True),
            sa.Column("control_rating", sa.String(length=20), nullable=True),
            sa.Column("control_ratings", sa.Text(), nullable=True),
            sa.Column("control_reduction", sa.Float(), nullable=True),
            sa.Column("residual_grid", sa.Text(), nullable=True),
            sa.Column("grid_version", sa.String(length=30), nullable=True),
            sa.Column("grid_band", sa.String(length=30), nullable=True),
            sa.Column("floors_applied", sa.Text(), nullable=True),
            sa.Column("residual_band", sa.String(length=30), nullable=True),
            sa.Column("residual_score", sa.Float(), nullable=True),
            sa.Column("reason", sa.Text(), nullable=True),
            sa.Column("frozen", sa.Boolean(), nullable=False, server_default=sa.false()),
            sa.Column("calculated_by", sa.String(length=255), nullable=True),
            sa.Column("calculated_at", sa.DateTime(), nullable=False),
            sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
            sa.Column("is_current", sa.Boolean(), nullable=False, server_default=sa.true()),
            sa.Column("superseded_at", sa.DateTime(), nullable=True),
        ],
        "decision_records": [
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("assessment_id", sa.Integer(), sa.ForeignKey("assessments.id"), nullable=False, index=True),
            sa.Column("decision", sa.String(length=40), nullable=False),
            sa.Column("decided_by", sa.String(length=255), nullable=False),
            sa.Column("decided_at", sa.DateTime(), nullable=False),
            sa.Column("record", sa.Text(), nullable=False),
            sa.Column("checksum", sa.String(length=80), nullable=False),
            sa.Column("created_at", sa.DateTime(), nullable=False),
        ],
    }


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = set(inspector.get_table_names())

    # Databases built by create_all against the current models already
    # have all of this.
    for table, columns in _new_tables().items():
        if table not in tables:
            op.create_table(table, *columns)

    for table, columns in _added_columns().items():
        existing = {column["name"] for column in inspector.get_columns(table)}
        for column in columns:
            if column.name not in existing:
                op.add_column(table, column)


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    for table, columns in _added_columns().items():
        existing = {column["name"] for column in inspector.get_columns(table)}
        for column in reversed(columns):
            if column.name in existing:
                op.drop_column(table, column.name)

    tables = set(inspector.get_table_names())
    for table in reversed(list(_new_tables())):
        if table in tables:
            op.drop_table(table)

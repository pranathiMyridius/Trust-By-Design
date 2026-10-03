"""Verified evidence on risk factors; triggered policy rules on calculations.

risk_factors gains the evidence contract from app/risk_engine/evidence.py:
a deterministic evidence_status, the evidence records (verified and
rejected), indicators refused for lack of a verified quote, and the
missing information the model reported.

inherent_risk_calculations gains the list of policy rules that fired and
whether any of them requires mandatory human review.

Existing rows are left NULL / false rather than backfilled: they were
produced before quotes were verified, and labelling them otherwise would
claim a check that never ran. Their stored scores are untouched, so
historical calculations read exactly as they did.

Revision ID: 0007_evidence_rules
Revises: 0006_approval_delegation
Create Date: 2026-09-28
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# Keep revision ids <= 32 chars: alembic_version.version_num is
# varchar(32) on Postgres and a longer id fails at the stamp step.
revision: str = "0007_evidence_rules"
down_revision: Union[str, None] = "0006_approval_delegation"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _columns() -> dict[str, list[sa.Column]]:
    # Rebuilt on each call: a Column instance cannot be attached to two
    # tables, and SQLAlchemy 2.0 removed Column.copy().
    return {
        "risk_factors": [
            sa.Column("evidence_status", sa.String(length=30), nullable=True),
            sa.Column("evidence", sa.Text(), nullable=True),
            sa.Column("rejected_indicators", sa.Text(), nullable=True),
            sa.Column("missing_information", sa.Text(), nullable=True),
        ],
        "inherent_risk_calculations": [
            sa.Column("triggered_rules", sa.Text(), nullable=True),
            sa.Column(
                "mandatory_review",
                sa.Boolean(),
                nullable=False,
                server_default=sa.false(),
            ),
        ],
    }


def _column_names(bind, table: str) -> set[str]:
    return {column["name"] for column in sa.inspect(bind).get_columns(table)}


def upgrade() -> None:
    bind = op.get_bind()

    for table, columns in _columns().items():
        # Databases built by create_all against the current models already
        # have these columns.
        existing = _column_names(bind, table)
        for column in columns:
            if column.name not in existing:
                op.add_column(table, column)


def downgrade() -> None:
    bind = op.get_bind()

    for table, columns in _columns().items():
        existing = _column_names(bind, table)
        for column in reversed(columns):
            if column.name in existing:
                op.drop_column(table, column.name)

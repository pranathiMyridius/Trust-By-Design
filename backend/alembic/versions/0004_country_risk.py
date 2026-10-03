"""Add the country_risk reference table.

Holds published country/jurisdiction risk designations (FATF today),
loaded from dated snapshots under app/reference_data/. Snapshots are
versioned via is_current rather than overwritten, so an assessment
completed under an earlier statement stays reproducible.

Revision ID: 0004_country_risk
Revises: 0003_rf_ai_suggested_rating
Create Date: 2026-09-24
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# Keep revision ids <= 32 chars: alembic_version.version_num is
# varchar(32) on Postgres and a longer id fails at the stamp step.
revision: str = "0004_country_risk"
down_revision: Union[str, None] = "0003_rf_ai_suggested_rating"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

TABLE = "country_risk"


def upgrade() -> None:
    # Databases built by create_all against the current models already
    # have this table.
    if sa.inspect(op.get_bind()).has_table(TABLE):
        return

    op.create_table(
        TABLE,
        sa.Column("id", sa.Integer(), primary_key=True, index=True),
        sa.Column("iso_code", sa.String(length=2), nullable=False, index=True),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("tier", sa.String(length=50), nullable=False, index=True),
        sa.Column("source", sa.String(length=50), nullable=False, index=True),
        sa.Column("as_of", sa.String(length=20), nullable=False),
        sa.Column("verified", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("is_current", sa.Boolean(), nullable=False, server_default=sa.true(), index=True),
        sa.Column("superseded_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )


def downgrade() -> None:
    if sa.inspect(op.get_bind()).has_table(TABLE):
        op.drop_table(TABLE)

"""Add shell_company_indicator to assessments.

Captured on the intake form (Product Information): whether the requester
flagged the counterparty as a potential shell entity. Nullable, and
existing rows are not backfilled -- NULL means "not answered", which is
kept distinct from an explicit "no".

Revision ID: 0009_shell_company_indicator
Revises: 0008_versioned_governance
Create Date: 2026-10-01
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0009_shell_company_indicator"
down_revision: Union[str, None] = "0008_versioned_governance"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

TABLE = "assessments"
COLUMN = "shell_company_indicator"


def _existing_columns(bind) -> set[str]:
    return {column["name"] for column in sa.inspect(bind).get_columns(TABLE)}


def upgrade() -> None:
    # Databases built by create_all against the current model already
    # have it, so only add it when it is actually missing.
    if COLUMN not in _existing_columns(op.get_bind()):
        op.add_column(TABLE, sa.Column(COLUMN, sa.Boolean(), nullable=True))


def downgrade() -> None:
    if COLUMN in _existing_columns(op.get_bind()):
        with op.batch_alter_table(TABLE) as batch:
            batch.drop_column(COLUMN)

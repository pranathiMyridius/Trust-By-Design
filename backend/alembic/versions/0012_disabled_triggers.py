"""Add disabled_triggers to challenge_trigger_configs.

R11.1: authorized users can switch individual challenge triggers off. A
JSON list of trigger names; NULL (existing rows) means none disabled.

Revision ID: 0012_disabled_triggers
Revises: 0011_action_item_closure_req
Create Date: 2026-10-01
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0012_disabled_triggers"
down_revision: Union[str, None] = "0011_action_item_closure_req"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

TABLE = "challenge_trigger_configs"
COLUMN = "disabled_triggers"


def _existing_columns(bind) -> set[str]:
    return {column["name"] for column in sa.inspect(bind).get_columns(TABLE)}


def upgrade() -> None:
    # Databases built by create_all against the current model already
    # have it, so only add it when it is actually missing.
    if COLUMN not in _existing_columns(op.get_bind()):
        op.add_column(TABLE, sa.Column(COLUMN, sa.Text(), nullable=True))


def downgrade() -> None:
    if COLUMN in _existing_columns(op.get_bind()):
        with op.batch_alter_table(TABLE) as batch:
            batch.drop_column(COLUMN)

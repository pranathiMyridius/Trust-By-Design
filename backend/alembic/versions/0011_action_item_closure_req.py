"""Add closure_requested_by_id to action_items.

R13.4: the reviewer who approves an action item's closure must not be the
person who requested it. The requester was stored only as a display name;
this records their user id so the check is exact. Existing rows keep NULL.

Revision ID: 0011_action_item_closure_req
Revises: 0010_processing_job_stage_log
Create Date: 2026-10-01
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0011_action_item_closure_req"
down_revision: Union[str, None] = "0010_processing_job_stage_log"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

TABLE = "action_items"
COLUMN = "closure_requested_by_id"


def _existing_columns(bind) -> set[str]:
    return {column["name"] for column in sa.inspect(bind).get_columns(TABLE)}


def upgrade() -> None:
    # Databases built by create_all against the current model already
    # have it, so only add it when it is actually missing.
    if COLUMN not in _existing_columns(op.get_bind()):
        op.add_column(TABLE, sa.Column(COLUMN, sa.Integer(), nullable=True))


def downgrade() -> None:
    if COLUMN in _existing_columns(op.get_bind()):
        with op.batch_alter_table(TABLE) as batch:
            batch.drop_column(COLUMN)

"""Add prompt_version to ai_usage_logs.

R16.1: the audit trail records the model *and prompt* version of every AI
call. A fingerprint of the prompt template (see app/ai/metering.py).
Existing rows keep NULL.

Revision ID: 0013_ai_prompt_version
Revises: 0012_disabled_triggers
Create Date: 2026-10-02
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0013_ai_prompt_version"
down_revision: Union[str, None] = "0012_disabled_triggers"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

TABLE = "ai_usage_logs"
COLUMN = "prompt_version"


def _existing_columns(bind) -> set[str]:
    return {column["name"] for column in sa.inspect(bind).get_columns(TABLE)}


def upgrade() -> None:
    # Databases built by create_all against the current model already
    # have it, so only add it when it is actually missing.
    if COLUMN not in _existing_columns(op.get_bind()):
        op.add_column(TABLE, sa.Column(COLUMN, sa.String(80), nullable=True))


def downgrade() -> None:
    if COLUMN in _existing_columns(op.get_bind()):
        with op.batch_alter_table(TABLE) as batch:
            batch.drop_column(COLUMN)

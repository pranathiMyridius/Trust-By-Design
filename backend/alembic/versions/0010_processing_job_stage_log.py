"""Add stage_log to processing_jobs.

Stage moves can now run as background jobs (job_type STAGE_ADVANCE;
originally RISK_IDENTIFICATION) that record each step's status and
timestamps; the final log is stored here as JSON. Existing rows keep
NULL.

Revision ID: 0010_processing_job_stage_log
Revises: 0009_shell_company_indicator
Create Date: 2026-10-01
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0010_processing_job_stage_log"
down_revision: Union[str, None] = "0009_shell_company_indicator"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

TABLE = "processing_jobs"
COLUMN = "stage_log"


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

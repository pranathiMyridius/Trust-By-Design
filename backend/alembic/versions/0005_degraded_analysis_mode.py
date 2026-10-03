"""Record how each risk analysis was produced (ai_assisted / rules_only / unavailable).

Before this, an AI-provider outage was persisted as ten risk factors at
score 0.0 / not-applicable -- indistinguishable, to an analyst and to the
scoring code, from a genuine low-risk finding. These columns make the
operating mode of each analysis run explicit and machine-readable, so a
degraded result can be labelled provisional and held back from
finalization instead of passing as a complete AI-assisted conclusion.

See app/risk_engine/degraded.py.

Revision ID: 0005_degraded_mode
Revises: 0004_country_risk
Create Date: 2026-09-24
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# Keep revision ids <= 32 chars: alembic_version.version_num is
# varchar(32) on Postgres and a longer id fails at the stamp step.
revision: str = "0005_degraded_mode"
down_revision: Union[str, None] = "0004_country_risk"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

TABLE = "assessments"
INDEX = "ix_assessments_assessment_mode"

# Existing rows keep assessment_mode NULL rather than being backfilled to
# "ai_assisted": we do not actually know how they were produced, and
# asserting that they were AI-assisted would be the same class of mistake
# this migration exists to correct. The API reads NULL as "never analysed
# under the degraded-mode contract".


def _columns() -> list[sa.Column]:
    # Rebuilt on each call: a Column instance cannot be attached to two
    # tables, and SQLAlchemy 2.0 removed Column.copy().
    return [
        sa.Column("assessment_mode", sa.String(length=20), nullable=True),
        sa.Column("ai_status", sa.String(length=30), nullable=True),
        sa.Column("score_source", sa.String(length=30), nullable=True),
        sa.Column(
            "analysis_is_provisional",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
        sa.Column(
            "requires_human_review",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
        sa.Column("degraded_reason", sa.Text(), nullable=True),
        sa.Column("technical_error_code", sa.String(length=60), nullable=True),
        sa.Column("unevaluated_categories", sa.Text(), nullable=True),
        sa.Column("analysis_mode_at", sa.DateTime(), nullable=True),
        sa.Column("degraded_acknowledged_by", sa.String(length=255), nullable=True),
        sa.Column("degraded_acknowledged_at", sa.DateTime(), nullable=True),
    ]


def _column_names(bind) -> set[str]:
    return {column["name"] for column in sa.inspect(bind).get_columns(TABLE)}


def _index_names(bind) -> set[str]:
    return {index["name"] for index in sa.inspect(bind).get_indexes(TABLE)}


def upgrade() -> None:
    bind = op.get_bind()
    # Databases built by create_all against the current models already
    # have these columns.
    existing = _column_names(bind)

    for column in _columns():
        if column.name not in existing:
            op.add_column(TABLE, column)

    if INDEX not in _index_names(bind):
        op.create_index(INDEX, TABLE, ["assessment_mode"])


def downgrade() -> None:
    bind = op.get_bind()

    if INDEX in _index_names(bind):
        op.drop_index(INDEX, table_name=TABLE)

    existing = _column_names(bind)
    for column in reversed(_columns()):
        if column.name in existing:
            op.drop_column(TABLE, column.name)

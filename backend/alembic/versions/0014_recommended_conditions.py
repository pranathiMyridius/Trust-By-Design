"""Add recommended_conditions and the human-confirmed residual risk.

R8.6: conditions recommended for an elevated residual risk, with the
analyst's accept / modify / reject decision and reason. R8: the residual
risk a human confirmed, stored beside the calculated one on
residual_risk_calculations. Existing rows keep NULL.

Revision ID: 0014_recommended_conditions
Revises: 0013_ai_prompt_version
Create Date: 2026-10-02
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0014_recommended_conditions"
down_revision: Union[str, None] = "0013_ai_prompt_version"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

TABLE = "recommended_conditions"
RESIDUAL = "residual_risk_calculations"


def _residual_columns() -> list[sa.Column]:
    return [
        sa.Column("confirmed_band", sa.String(length=30), nullable=True),
        sa.Column("confirmed_score", sa.Float(), nullable=True),
        sa.Column("confirmation_reason", sa.Text(), nullable=True),
        sa.Column("confirmed_by", sa.String(length=255), nullable=True),
        sa.Column("confirmed_by_id", sa.Integer(), nullable=True),
        sa.Column("confirmed_at", sa.DateTime(), nullable=True),
    ]


def _column_names(bind, table: str) -> set[str]:
    return {column["name"] for column in sa.inspect(bind).get_columns(table)}


def upgrade() -> None:
    bind = op.get_bind()

    # Databases built by create_all against the current models already
    # have the table and columns.
    if not sa.inspect(bind).has_table(TABLE):
        op.create_table(
            TABLE,
            sa.Column("id", sa.Integer(), primary_key=True, index=True),
            sa.Column("assessment_id", sa.Integer(), sa.ForeignKey("assessments.id"), nullable=False, index=True),
            sa.Column("condition_type", sa.String(length=50), nullable=False),
            sa.Column("recommended_text", sa.Text(), nullable=False),
            sa.Column("rationale", sa.Text(), nullable=False),
            sa.Column("status", sa.String(length=20), nullable=False),
            sa.Column("final_text", sa.Text(), nullable=True),
            sa.Column("decision_reason", sa.Text(), nullable=True),
            sa.Column("decided_by", sa.String(length=255), nullable=True),
            sa.Column("decided_by_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
            sa.Column("decided_at", sa.DateTime(), nullable=True),
            sa.Column("is_current", sa.Boolean(), nullable=False),
            sa.Column("created_at", sa.DateTime(), nullable=False),
        )

    existing = _column_names(bind, RESIDUAL)
    for column in _residual_columns():
        if column.name not in existing:
            op.add_column(RESIDUAL, column)


def downgrade() -> None:
    bind = op.get_bind()

    existing = _column_names(bind, RESIDUAL)
    for column in reversed(_residual_columns()):
        if column.name in existing:
            with op.batch_alter_table(RESIDUAL) as batch:
                batch.drop_column(column.name)

    if sa.inspect(bind).has_table(TABLE):
        op.drop_table(TABLE)

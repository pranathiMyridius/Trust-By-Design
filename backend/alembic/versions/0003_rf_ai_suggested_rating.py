"""Add AI-suggested likelihood/impact columns to risk_factors.

The AI may now propose a likelihood/impact rating for a risk factor, which
pre-fills the analyst's rating form. The suggestion is stored separately
from the analyst's own likelihood/impact so the audit trail keeps both:
what the model proposed, and what the human decided. rating_source records
which of those the stored rating represents.

Revision ID: 0003_rf_ai_suggested_rating
Revises: 0002_parent_assessment_fk
Create Date: 2026-09-24
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0003_rf_ai_suggested_rating"
down_revision: Union[str, None] = "0002_parent_assessment_fk"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

TABLE = "risk_factors"

NEW_COLUMNS = (
    ("ai_suggested_likelihood", sa.Integer()),
    ("ai_suggested_impact", sa.Integer()),
    ("ai_suggestion_rationale", sa.Text()),
    ("ai_suggested_at", sa.DateTime()),
    ("rating_source", sa.String(length=30)),
)


def _existing_columns(bind) -> set[str]:
    return {column["name"] for column in sa.inspect(bind).get_columns(TABLE)}


def upgrade() -> None:
    # Databases built by create_all against the current model already have
    # these, so only add what is actually missing.
    existing = _existing_columns(op.get_bind())

    for name, column_type in NEW_COLUMNS:
        if name not in existing:
            op.add_column(TABLE, sa.Column(name, column_type, nullable=True))


def downgrade() -> None:
    existing = _existing_columns(op.get_bind())

    for name, _ in reversed(NEW_COLUMNS):
        if name in existing:
            op.drop_column(TABLE, name)

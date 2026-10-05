"""AI challenge analysis: advisory gap findings raised by the model.

* ai_challenge_findings: one row per finding, with the category, severity,
  the control / risk / document it concerns, a verified quote, the model and
  prompt version, and the reviewer's confirm / dismiss decision.

Additive; nothing is backfilled. Downgrade refuses while any finding has a
reviewer decision, rather than lose that record.

Revision ID: 0026_ai_challenge
Revises: 0025_control_evidence
Create Date: 2026-10-05
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0026_ai_challenge"
down_revision: Union[str, None] = "0025_control_evidence"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

TABLE = "ai_challenge_findings"


def upgrade() -> None:
    bind = op.get_bind()
    if TABLE in sa.inspect(bind).get_table_names():
        return
    op.create_table(
        TABLE,
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("assessment_id", sa.Integer(), sa.ForeignKey("assessments.id"), nullable=False),
        sa.Column("run_id", sa.String(length=36), nullable=False),
        sa.Column("category", sa.String(length=30), nullable=False),
        sa.Column("severity", sa.String(length=10), nullable=False),
        sa.Column("title", sa.String(length=255), nullable=False),
        sa.Column("detail", sa.Text(), nullable=False),
        sa.Column("risk_factor_id", sa.Integer(), sa.ForeignKey("risk_factors.id"), nullable=True),
        sa.Column("control_id", sa.Integer(), sa.ForeignKey("controls.id"), nullable=True),
        sa.Column("document_id", sa.Integer(), sa.ForeignKey("assessment_documents.id"), nullable=True),
        sa.Column("quote", sa.Text(), nullable=True),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="OPEN"),
        sa.Column("model", sa.String(length=100), nullable=True),
        sa.Column("prompt_version", sa.String(length=100), nullable=True),
        sa.Column("generated_by", sa.String(length=255), nullable=True),
        sa.Column("generated_at", sa.DateTime(), nullable=False),
        sa.Column("decided_by", sa.String(length=255), nullable=True),
        sa.Column("decided_by_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("decided_at", sa.DateTime(), nullable=True),
        sa.Column("decision_note", sa.Text(), nullable=True),
    )
    for column in ("assessment_id", "run_id", "status"):
        op.create_index(f"ix_{TABLE}_{column}", TABLE, [column])


def downgrade() -> None:
    bind = op.get_bind()
    if TABLE not in sa.inspect(bind).get_table_names():
        return
    decided = bind.execute(
        sa.text(f"SELECT COUNT(*) FROM {TABLE} WHERE status IN ('CONFIRMED', 'DISMISSED')")
    ).scalar()
    if decided:
        raise RuntimeError(
            f"{decided} AI challenge finding(s) carry a reviewer decision; downgrading 0026 would lose that record."
        )
    op.drop_table(TABLE)

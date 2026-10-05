"""AI evidence check: suggested links between uploaded documents and controls.

* control_evidence_links: one row per (control, document) suggestion, with
  the verified quote, confidence, shortfalls, model and prompt version, and
  the analyst's accept/reject decision.

Additive; nothing is backfilled. Downgrade refuses while any suggestion has
been accepted or rejected by an analyst, rather than lose that decision.

Revision ID: 0025_control_evidence
Revises: 0024_committee_acceptance
Create Date: 2026-10-05
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0025_control_evidence"
down_revision: Union[str, None] = "0024_committee_acceptance"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

TABLE = "control_evidence_links"


def upgrade() -> None:
    bind = op.get_bind()
    if TABLE in sa.inspect(bind).get_table_names():
        return
    op.create_table(
        TABLE,
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("assessment_id", sa.Integer(), sa.ForeignKey("assessments.id"), nullable=False),
        sa.Column("control_id", sa.Integer(), sa.ForeignKey("controls.id"), nullable=False),
        sa.Column("document_id", sa.Integer(), sa.ForeignKey("assessment_documents.id"), nullable=True),
        sa.Column("document_version", sa.Integer(), nullable=True),
        sa.Column("support_level", sa.String(length=20), nullable=False),
        sa.Column("confidence", sa.String(length=10), nullable=True),
        sa.Column("quote", sa.Text(), nullable=True),
        sa.Column("rationale", sa.Text(), nullable=True),
        sa.Column("shortfalls", sa.Text(), nullable=True),
        sa.Column("suggested_effectiveness", sa.String(length=30), nullable=True),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="SUGGESTED"),
        sa.Column("model", sa.String(length=100), nullable=True),
        sa.Column("prompt_version", sa.String(length=100), nullable=True),
        sa.Column("checked_at", sa.DateTime(), nullable=False),
        sa.Column("decided_by", sa.String(length=255), nullable=True),
        sa.Column("decided_by_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("decided_at", sa.DateTime(), nullable=True),
        sa.Column("decision_note", sa.Text(), nullable=True),
    )
    for column in ("assessment_id", "control_id", "document_id", "status"):
        op.create_index(f"ix_{TABLE}_{column}", TABLE, [column])


def downgrade() -> None:
    bind = op.get_bind()
    if TABLE not in sa.inspect(bind).get_table_names():
        return
    decided = bind.execute(
        sa.text(f"SELECT COUNT(*) FROM {TABLE} WHERE status IN ('ACCEPTED', 'REJECTED')")
    ).scalar()
    if decided:
        raise RuntimeError(
            f"{decided} evidence suggestion(s) carry an analyst decision; downgrading 0025 would lose that record."
        )
    op.drop_table(TABLE)

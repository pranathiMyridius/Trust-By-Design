"""Add the approved evidence-source library.

R5.1/R5.2: approved_sources holds internal policies, procedures, control
standards, regulatory guidance, frameworks, previous assessments and
vendor-control documentation, each with a version, effective and review
dates and a DRAFT / APPROVED / RETIRED status. R5.3: source_evidence_links
records a passage attached to a risk factor as evidence, frozen as
retrieved.

Revision ID: 0016_approved_sources
Revises: 0015_user_access_scope
Create Date: 2026-10-02
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0016_approved_sources"
down_revision: Union[str, None] = "0015_user_access_scope"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())

    # Databases built by create_all against the current models already
    # have these tables.
    if not inspector.has_table("approved_sources"):
        op.create_table(
            "approved_sources",
            sa.Column("id", sa.Integer(), primary_key=True, index=True),
            sa.Column("title", sa.String(length=255), nullable=False),
            sa.Column("source_type", sa.String(length=40), nullable=False),
            sa.Column("issuer", sa.String(length=255), nullable=True),
            sa.Column("version", sa.String(length=50), nullable=False),
            sa.Column("effective_date", sa.Date(), nullable=True),
            sa.Column("review_date", sa.Date(), nullable=True),
            sa.Column("reference", sa.String(length=500), nullable=True),
            sa.Column("content", sa.Text(), nullable=False),
            sa.Column("status", sa.String(length=20), nullable=False),
            sa.Column("approved_by", sa.String(length=255), nullable=True),
            sa.Column("approved_by_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
            sa.Column("approved_at", sa.DateTime(), nullable=True),
            sa.Column("created_by", sa.String(length=255), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), nullable=False),
        )

    if not inspector.has_table("source_evidence_links"):
        op.create_table(
            "source_evidence_links",
            sa.Column("id", sa.Integer(), primary_key=True, index=True),
            sa.Column("assessment_id", sa.Integer(), sa.ForeignKey("assessments.id"), nullable=False, index=True),
            sa.Column("risk_factor_id", sa.Integer(), sa.ForeignKey("risk_factors.id"), nullable=True, index=True),
            sa.Column("source_id", sa.Integer(), sa.ForeignKey("approved_sources.id"), nullable=False),
            sa.Column("source_title", sa.String(length=255), nullable=False),
            sa.Column("source_type", sa.String(length=40), nullable=False),
            sa.Column("source_version", sa.String(length=50), nullable=False),
            sa.Column("effective_date", sa.Date(), nullable=True),
            sa.Column("reference", sa.String(length=500), nullable=True),
            sa.Column("passage", sa.Text(), nullable=False),
            sa.Column("retrieved_at", sa.DateTime(), nullable=False),
            sa.Column("retrieved_by", sa.String(length=255), nullable=True),
            sa.Column("retrieved_by_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
        )


def downgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    if inspector.has_table("source_evidence_links"):
        op.drop_table("source_evidence_links")
    if inspector.has_table("approved_sources"):
        op.drop_table("approved_sources")

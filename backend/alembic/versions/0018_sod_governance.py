"""P3: SoD exceptions, governance designations, override approval,
two-step challenge review.

* users.governance_designations (JSON list; NULL = none).
* risk_factors.rated_by_id (independence checks; NULL on older ratings).
* assessment_overrides: origin, materiality, materiality_reasons and the
  material-override approval fields. Existing rows keep NULL ("legacy":
  recorded before independent review existed; never relabelled).
* challenge_review_signoffs: stage (REVIEW | SIGNOFF; existing rows become
  SIGNOFF) and committee_escalation.
* sod_exceptions and sod_exception_events (new).

Additive and idempotent, like the earlier revisions. Downgrade refuses
while SoD exceptions or independent challenge reviews exist, rather than
discard governance records.

Revision ID: 0018_sod_governance
Revises: 0017_governance_records
Create Date: 2026-10-02
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0018_sod_governance"
down_revision: Union[str, None] = "0017_governance_records"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _added() -> dict[str, list[sa.Column]]:
    return {
        "users": [sa.Column("governance_designations", sa.Text(), nullable=True)],
        "risk_factors": [sa.Column("rated_by_id", sa.Integer(), nullable=True)],
        "assessment_overrides": [
            sa.Column("origin", sa.String(length=20), nullable=True),
            sa.Column("materiality", sa.String(length=20), nullable=True),
            sa.Column("materiality_reasons", sa.Text(), nullable=True),
            sa.Column("approval_status", sa.String(length=20), nullable=True),
            sa.Column("approved_by", sa.String(length=255), nullable=True),
            sa.Column("approved_by_id", sa.Integer(), nullable=True),
            sa.Column("approved_at", sa.DateTime(), nullable=True),
            sa.Column("approval_rationale", sa.Text(), nullable=True),
        ],
        "challenge_review_signoffs": [
            sa.Column("stage", sa.String(length=10), nullable=False, server_default="SIGNOFF"),
            sa.Column("committee_escalation", sa.Boolean(), nullable=False, server_default=sa.false()),
        ],
    }


def _columns(bind, table: str) -> set[str]:
    return {column["name"] for column in sa.inspect(bind).get_columns(table)}


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    for table, columns in _added().items():
        existing = _columns(bind, table)
        for column in columns:
            if column.name not in existing:
                op.add_column(table, column)

    if not inspector.has_table("sod_exceptions"):
        op.create_table(
            "sod_exceptions",
            sa.Column("id", sa.Integer(), primary_key=True, index=True),
            sa.Column("reference", sa.String(length=30), nullable=True, unique=True, index=True),
            sa.Column("exception_type", sa.String(length=40), nullable=False),
            sa.Column("status", sa.String(length=20), nullable=False, index=True),
            sa.Column("requestor_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False, index=True),
            sa.Column("affected_user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False, index=True),
            sa.Column("conflicting_roles", sa.Text(), nullable=False),
            sa.Column("assessment_id", sa.Integer(), sa.ForeignKey("assessments.id"), nullable=True, index=True),
            sa.Column("business_justification", sa.Text(), nullable=False),
            sa.Column("standard_workflow_reason", sa.Text(), nullable=False),
            sa.Column("risk_level", sa.String(length=10), nullable=False),
            sa.Column("compensating_controls", sa.Text(), nullable=False),
            sa.Column("start_at", sa.DateTime(), nullable=False),
            sa.Column("end_at", sa.DateTime(), nullable=False),
            sa.Column("tier", sa.String(length=20), nullable=True),
            sa.Column("tier_reasons", sa.Text(), nullable=True),
            sa.Column("repeated", sa.Boolean(), nullable=False, server_default=sa.false()),
            sa.Column("assigned_approver_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
            sa.Column("assigned_reviewer_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
            sa.Column("decision", sa.String(length=20), nullable=True),
            sa.Column("decision_rationale", sa.Text(), nullable=True),
            sa.Column("decided_by_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
            sa.Column("decided_at", sa.DateTime(), nullable=True),
            sa.Column("declaration", sa.Text(), nullable=True),
            sa.Column("declared_at", sa.DateTime(), nullable=True),
            sa.Column("revoked_by_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
            sa.Column("revoked_at", sa.DateTime(), nullable=True),
            sa.Column("revocation_reason", sa.Text(), nullable=True),
            sa.Column("expired_at", sa.DateTime(), nullable=True),
            sa.Column("last_reviewed_at", sa.DateTime(), nullable=True),
            sa.Column("last_reviewed_by_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
            sa.Column("last_review_note", sa.Text(), nullable=True),
            sa.Column("submitted_at", sa.DateTime(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), nullable=False),
            sa.Column("row_version", sa.Integer(), nullable=False, server_default="1"),
        )
    if not inspector.has_table("sod_exception_events"):
        op.create_table(
            "sod_exception_events",
            sa.Column("id", sa.Integer(), primary_key=True, index=True),
            sa.Column("exception_id", sa.Integer(), sa.ForeignKey("sod_exceptions.id"), nullable=False, index=True),
            sa.Column("action", sa.String(length=20), nullable=False),
            sa.Column("from_status", sa.String(length=20), nullable=True),
            sa.Column("to_status", sa.String(length=20), nullable=True),
            sa.Column("actor", sa.String(length=255), nullable=False),
            sa.Column("actor_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
            sa.Column("detail", sa.Text(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False),
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if inspector.has_table("sod_exceptions"):
        count = bind.execute(sa.text("SELECT COUNT(*) FROM sod_exceptions")).scalar()
        if count:
            raise RuntimeError(f"{count} SoD exception(s) exist; downgrading 0018 would discard them and their history.")
    if "stage" in _columns(bind, "challenge_review_signoffs"):
        reviews = bind.execute(sa.text("SELECT COUNT(*) FROM challenge_review_signoffs WHERE stage = 'REVIEW'")).scalar()
        if reviews:
            raise RuntimeError(f"{reviews} independent challenge review(s) exist; downgrading 0018 would discard them.")

    for table in ("sod_exception_events", "sod_exceptions"):
        if inspector.has_table(table):
            op.drop_table(table)
    for table, columns in _added().items():
        existing = _columns(bind, table)
        with op.batch_alter_table(table) as batch:
            for column in reversed(columns):
                if column.name in existing:
                    batch.drop_column(column.name)

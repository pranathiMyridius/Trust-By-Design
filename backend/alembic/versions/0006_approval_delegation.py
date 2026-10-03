"""Add approval delegation (AW.7).

A new approval_delegations table holds temporary grants of a Manager's
or Committee Member's approval authority to another user. New columns
on assessments and committee_votes record, for each approval, both the
delegate who acted and the approver whose authority they used.

See app/services/delegation.py.

Revision ID: 0006_approval_delegation
Revises: 0005_degraded_mode
Create Date: 2026-09-28
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# Keep revision ids <= 32 chars: alembic_version.version_num is
# varchar(32) on Postgres and a longer id fails at the stamp step.
revision: str = "0006_approval_delegation"
down_revision: Union[str, None] = "0005_degraded_mode"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

TABLE = "approval_delegations"

# Added as plain integer columns: SQLite cannot add a foreign-key
# constraint to an existing table, and the ids are only ever written by
# the approval endpoints, which take them from rows they just loaded.


def _assessment_columns() -> list[sa.Column]:
    # Rebuilt on each call: a Column instance cannot be attached to two
    # tables, and SQLAlchemy 2.0 removed Column.copy().
    return [
        sa.Column("manager_decided_on_behalf_of_id", sa.Integer(), nullable=True),
        sa.Column("manager_delegation_id", sa.Integer(), nullable=True),
        sa.Column("committee_decided_on_behalf_of_id", sa.Integer(), nullable=True),
        sa.Column("committee_delegation_id", sa.Integer(), nullable=True),
    ]


def _vote_columns() -> list[sa.Column]:
    return [
        sa.Column("delegate_id", sa.Integer(), nullable=True),
        sa.Column("delegate_name", sa.String(length=255), nullable=True),
        sa.Column("delegation_id", sa.Integer(), nullable=True),
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
            sa.Column("delegator_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False, index=True),
            sa.Column("delegate_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False, index=True),
            sa.Column("authority", sa.String(length=30), nullable=False),
            sa.Column("scope_type", sa.String(length=20), nullable=False),
            sa.Column("scope_assessment_id", sa.Integer(), sa.ForeignKey("assessments.id"), nullable=True),
            sa.Column("start_at", sa.DateTime(), nullable=False),
            sa.Column("end_at", sa.DateTime(), nullable=False),
            sa.Column("reason", sa.Text(), nullable=False),
            sa.Column("created_by_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("revoked_at", sa.DateTime(), nullable=True),
            sa.Column("revoked_by_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
            sa.Column("revoke_reason", sa.Text(), nullable=True),
        )

    for table, columns in (("assessments", _assessment_columns()), ("committee_votes", _vote_columns())):
        existing = _column_names(bind, table)
        for column in columns:
            if column.name not in existing:
                op.add_column(table, column)


def downgrade() -> None:
    bind = op.get_bind()

    for table, columns in (("committee_votes", _vote_columns()), ("assessments", _assessment_columns())):
        existing = _column_names(bind, table)
        for column in reversed(columns):
            if column.name in existing:
                with op.batch_alter_table(table) as batch:
                    batch.drop_column(column.name)

    if sa.inspect(bind).has_table(TABLE):
        op.drop_table(TABLE)

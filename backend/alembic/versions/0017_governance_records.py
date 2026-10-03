"""Governance records: append-only votes, reviewed overrides, challenge
sign-off, control revisions.

* committee_votes (R12.6): version / is_current / superseded_at /
  superseded_by_id / cast_by_id / recast_reason. Every existing vote
  becomes version 1 and current, with cast_by_id taken from the delegate
  (if one cast it) or the member. The one-vote-per-seat unique constraint
  is replaced by unique (assessment_id, member_id, version) plus a partial
  unique index allowing one *current* vote per seat.
* assessment_overrides (R6.7/R10.2/R10.4): ai_value_source and the review
  fields. Existing rows keep NULL: their ai_value came from the client and
  they were never reviewed, and they are not relabelled.
* inherent_risk_calculations: override_by_id (existing rows NULL).
* challenge_review_signoffs (R11, new).
* control_revisions (R7.2/R10.2, new).

Idempotent like the earlier revisions: a database built by create_all
against the current models already has all of this.

Revision ID: 0017_governance_records
Revises: 0016_approved_sources
Create Date: 2026-10-02
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0017_governance_records"
down_revision: Union[str, None] = "0016_approved_sources"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

VOTES = "committee_votes"
OLD_VOTE_CONSTRAINT = "uq_committee_vote_member"
VOTE_VERSION_CONSTRAINT = "uq_committee_vote_version"
CURRENT_VOTE_INDEX = "uq_committee_vote_current"


def _vote_columns() -> list[sa.Column]:
    return [
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("is_current", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("superseded_at", sa.DateTime(), nullable=True),
        sa.Column("superseded_by_id", sa.Integer(), nullable=True),
        sa.Column("cast_by_id", sa.Integer(), nullable=True),
        sa.Column("recast_reason", sa.Text(), nullable=True),
    ]


def _override_columns() -> list[sa.Column]:
    return [
        sa.Column("ai_value_source", sa.String(length=20), nullable=True),
        sa.Column("review_status", sa.String(length=20), nullable=True),
        sa.Column("reviewed_by", sa.String(length=255), nullable=True),
        sa.Column("reviewed_by_id", sa.Integer(), nullable=True),
        sa.Column("reviewed_at", sa.DateTime(), nullable=True),
        sa.Column("review_note", sa.Text(), nullable=True),
    ]


def _columns(bind, table: str) -> set[str]:
    return {column["name"] for column in sa.inspect(bind).get_columns(table)}


def _unique_constraints(bind, table: str) -> set[str]:
    return {c["name"] for c in sa.inspect(bind).get_unique_constraints(table) if c.get("name")}


def _indexes(bind, table: str) -> set[str]:
    return {i["name"] for i in sa.inspect(bind).get_indexes(table)}


def _add_missing(bind, table: str, columns: list[sa.Column]) -> None:
    existing = _columns(bind, table)
    for column in columns:
        if column.name not in existing:
            op.add_column(table, column)


def _upgrade_votes(bind) -> None:
    _add_missing(bind, VOTES, _vote_columns())

    # Existing votes: version 1, current (server defaults), and who cast
    # them -- recorded already as delegate_id when a delegate did.
    op.execute(
        sa.text(
            f"UPDATE {VOTES} SET cast_by_id = COALESCE(delegate_id, member_id) "
            "WHERE cast_by_id IS NULL"
        )
    )

    constraints = _unique_constraints(bind, VOTES)
    with op.batch_alter_table(VOTES) as batch:
        if OLD_VOTE_CONSTRAINT in constraints:
            batch.drop_constraint(OLD_VOTE_CONSTRAINT, type_="unique")
        if VOTE_VERSION_CONSTRAINT not in constraints:
            batch.create_unique_constraint(VOTE_VERSION_CONSTRAINT, ["assessment_id", "member_id", "version"])

    if CURRENT_VOTE_INDEX not in _indexes(bind, VOTES):
        op.create_index(
            CURRENT_VOTE_INDEX,
            VOTES,
            ["assessment_id", "member_id"],
            unique=True,
            postgresql_where=sa.text("is_current"),
            sqlite_where=sa.text("is_current = 1"),
        )


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    _upgrade_votes(bind)
    _add_missing(bind, "assessment_overrides", _override_columns())
    _add_missing(bind, "inherent_risk_calculations", [sa.Column("override_by_id", sa.Integer(), nullable=True)])

    if not inspector.has_table("challenge_review_signoffs"):
        op.create_table(
            "challenge_review_signoffs",
            sa.Column("id", sa.Integer(), primary_key=True, index=True),
            sa.Column("assessment_id", sa.Integer(), sa.ForeignKey("assessments.id"), nullable=False, index=True),
            sa.Column("outcome", sa.String(length=30), nullable=False),
            sa.Column("reason", sa.Text(), nullable=False),
            sa.Column("trigger_snapshot", sa.Text(), nullable=False),
            sa.Column("findings_snapshot", sa.Text(), nullable=False),
            sa.Column("reviewer", sa.String(length=255), nullable=False),
            sa.Column("reviewer_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
            sa.Column("reviewer_role", sa.String(length=30), nullable=False),
            sa.Column("completed_at", sa.DateTime(), nullable=False),
            sa.Column("version", sa.Integer(), nullable=False),
            sa.Column("is_current", sa.Boolean(), nullable=False),
            sa.Column("superseded_at", sa.DateTime(), nullable=True),
            sa.Column("superseded_reason", sa.Text(), nullable=True),
        )

    if not inspector.has_table("control_revisions"):
        op.create_table(
            "control_revisions",
            sa.Column("id", sa.Integer(), primary_key=True, index=True),
            sa.Column("control_id", sa.Integer(), sa.ForeignKey("controls.id"), nullable=False, index=True),
            sa.Column("assessment_id", sa.Integer(), sa.ForeignKey("assessments.id"), nullable=False, index=True),
            sa.Column("change_type", sa.String(length=20), nullable=False),
            sa.Column("version", sa.Integer(), nullable=False),
            sa.Column("previous_config", sa.Text(), nullable=False),
            sa.Column("new_config", sa.Text(), nullable=True),
            sa.Column("changed_fields", sa.Text(), nullable=False),
            sa.Column("reason", sa.Text(), nullable=False),
            sa.Column("changed_by", sa.String(length=255), nullable=False),
            sa.Column("changed_by_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
            sa.Column("changed_at", sa.DateTime(), nullable=False),
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    # Going back to one vote per seat would have to throw away vote
    # history. Refuse rather than lose it.
    if "version" in _columns(bind, VOTES):
        recast = bind.execute(sa.text(f"SELECT COUNT(*) FROM {VOTES} WHERE version > 1")).scalar()
        if recast:
            raise RuntimeError(
                f"{recast} re-cast committee vote(s) exist; downgrading 0017 would discard "
                "vote history. Export it and remove those rows deliberately first."
            )

    for table in ("control_revisions", "challenge_review_signoffs"):
        if inspector.has_table(table):
            op.drop_table(table)

    for table, names in (
        ("inherent_risk_calculations", ["override_by_id"]),
        ("assessment_overrides", [c.name for c in _override_columns()]),
    ):
        existing = _columns(bind, table)
        with op.batch_alter_table(table) as batch:
            for name in reversed(names):
                if name in existing:
                    batch.drop_column(name)

    if CURRENT_VOTE_INDEX in _indexes(bind, VOTES):
        op.drop_index(CURRENT_VOTE_INDEX, table_name=VOTES)
    constraints = _unique_constraints(bind, VOTES)
    existing = _columns(bind, VOTES)
    with op.batch_alter_table(VOTES) as batch:
        if VOTE_VERSION_CONSTRAINT in constraints:
            batch.drop_constraint(VOTE_VERSION_CONSTRAINT, type_="unique")
        for column in reversed(_vote_columns()):
            if column.name in existing:
                batch.drop_column(column.name)
        if OLD_VOTE_CONSTRAINT not in constraints:
            batch.create_unique_constraint(OLD_VOTE_CONSTRAINT, ["assessment_id", "member_id"])

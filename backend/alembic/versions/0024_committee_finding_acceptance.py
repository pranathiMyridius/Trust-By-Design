"""Challenge findings: who accepted a finding, and on whose authority.

* challenge_findings: accepted_by_id (integer, the accepting user) and
  acceptance_authority ('COMMITTEE' for a documented Committee exception)
  -- both nullable. Nothing is backfilled: an acceptance recorded under
  the earlier rule (by a Manager, any severity) keeps NULL and no longer
  counts (G-4, user direction 2026-10-03): a HIGH/CRITICAL finding must be
  resolved, a MEDIUM one resolved or accepted again by the Committee.

accepted_by_id is a plain integer here (no FK constraint), as earlier
migrations add reference columns, so SQLite can apply it in place.

Downgrade refuses while any Committee acceptance is recorded, rather than
lose who accepted it on the Committee's authority.

Revision ID: 0024_committee_acceptance
Revises: 0023_source_files
Create Date: 2026-10-03
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0024_committee_acceptance"
down_revision: Union[str, None] = "0023_source_files"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _added() -> list[sa.Column]:
    return [
        sa.Column("accepted_by_id", sa.Integer(), nullable=True),
        sa.Column("acceptance_authority", sa.String(length=30), nullable=True),
    ]


def _columns(bind) -> set[str]:
    return {column["name"] for column in sa.inspect(bind).get_columns("challenge_findings")}


def upgrade() -> None:
    existing = _columns(op.get_bind())
    for column in _added():
        if column.name not in existing:
            op.add_column("challenge_findings", column)


def downgrade() -> None:
    bind = op.get_bind()
    existing = _columns(bind)
    if "acceptance_authority" in existing:
        count = bind.execute(
            sa.text("SELECT COUNT(*) FROM challenge_findings WHERE acceptance_authority IS NOT NULL")
        ).scalar()
        if count:
            raise RuntimeError(
                f"{count} challenge finding(s) were accepted as Committee exceptions; "
                "downgrading 0024 would lose who accepted them on the Committee's authority."
            )
    # Plain DROP COLUMN (SQLite >= 3.35, Postgres): neither is indexed or constrained.
    for column in reversed(_added()):
        if column.name in existing:
            op.drop_column("challenge_findings", column.name)

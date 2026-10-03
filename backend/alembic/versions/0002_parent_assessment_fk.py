"""Add the missing assessments.parent_assessment_id foreign key.

The Stage 18 script added the column without the constraint the model
declares. Databases built by create_all already have it, so this only acts
when the key is missing.

Revision ID: 0002_parent_assessment_fk
Revises: 0001_baseline
Create Date: 2026-09-24
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0002_parent_assessment_fk"
down_revision: Union[str, None] = "0001_baseline"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

FK_NAME = "fk_assessments_parent_assessment_id"


def _has_parent_fk(bind) -> bool:
    for fk in sa.inspect(bind).get_foreign_keys("assessments"):
        if fk["constrained_columns"] == ["parent_assessment_id"]:
            return True
    return False


def upgrade() -> None:
    bind = op.get_bind()
    if _has_parent_fk(bind):
        return

    # Drop links to assessments that no longer exist, or the FK can't be added.
    op.execute(
        "UPDATE assessments SET parent_assessment_id = NULL "
        "WHERE parent_assessment_id IS NOT NULL "
        "AND parent_assessment_id NOT IN (SELECT id FROM assessments)"
    )
    with op.batch_alter_table("assessments") as batch_op:
        batch_op.create_foreign_key(
            FK_NAME, "assessments", ["parent_assessment_id"], ["id"]
        )


def downgrade() -> None:
    with op.batch_alter_table("assessments") as batch_op:
        batch_op.drop_constraint(FK_NAME, type_="foreignkey")

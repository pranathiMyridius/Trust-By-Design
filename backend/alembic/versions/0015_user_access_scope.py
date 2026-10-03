"""Add access-scope columns to users.

R15.4: users may be restricted to particular legal entities, business
units and countries. Each is a JSON list; NULL (every existing user)
means unrestricted. The new roles (AUDITOR, EXECUTIVE, CONTROL_OWNER,
POLICY_ADMIN) need no migration -- users.role is a plain string.

Revision ID: 0015_user_access_scope
Revises: 0014_recommended_conditions
Create Date: 2026-10-02
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0015_user_access_scope"
down_revision: Union[str, None] = "0014_recommended_conditions"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

TABLE = "users"
COLUMNS = ("scope_legal_entities", "scope_business_units", "scope_countries")


def _existing_columns(bind) -> set[str]:
    return {column["name"] for column in sa.inspect(bind).get_columns(TABLE)}


def upgrade() -> None:
    # Databases built by create_all against the current model already
    # have them, so only add what is actually missing.
    existing = _existing_columns(op.get_bind())
    for name in COLUMNS:
        if name not in existing:
            op.add_column(TABLE, sa.Column(name, sa.Text(), nullable=True))


def downgrade() -> None:
    existing = _existing_columns(op.get_bind())
    for name in reversed(COLUMNS):
        if name in existing:
            with op.batch_alter_table(TABLE) as batch:
                batch.drop_column(name)

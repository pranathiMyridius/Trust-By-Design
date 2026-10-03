"""Approved-source library: keep the original uploaded document.

* approved_sources: original_filename, file_path, file_content_type,
  file_size, file_sha256 -- all nullable. Existing sources (pasted text)
  keep NULL; nothing is backfilled.

Downgrade refuses while any source has a stored file, rather than lose the
link between a source and its original document.

Revision ID: 0023_source_files
Revises: 0022_reassessment_approval
Create Date: 2026-10-03
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0023_source_files"
down_revision: Union[str, None] = "0022_reassessment_approval"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _added() -> list[sa.Column]:
    return [
        sa.Column("original_filename", sa.String(length=255), nullable=True),
        sa.Column("file_path", sa.String(length=500), nullable=True),
        sa.Column("file_content_type", sa.String(length=100), nullable=True),
        sa.Column("file_size", sa.Integer(), nullable=True),
        sa.Column("file_sha256", sa.String(length=64), nullable=True),
    ]


def _columns(bind) -> set[str]:
    return {column["name"] for column in sa.inspect(bind).get_columns("approved_sources")}


def upgrade() -> None:
    existing = _columns(op.get_bind())
    for column in _added():
        if column.name not in existing:
            op.add_column("approved_sources", column)


def downgrade() -> None:
    bind = op.get_bind()
    existing = _columns(bind)
    if "file_path" in existing:
        count = bind.execute(sa.text("SELECT COUNT(*) FROM approved_sources WHERE file_path IS NOT NULL")).scalar()
        if count:
            raise RuntimeError(
                f"{count} approved source(s) have a stored original document; downgrading 0023 would lose the link to it."
            )
    # Plain DROP COLUMN (SQLite >= 3.35, Postgres): none is indexed or constrained.
    for column in reversed(_added()):
        if column.name in existing:
            op.drop_column("approved_sources", column.name)

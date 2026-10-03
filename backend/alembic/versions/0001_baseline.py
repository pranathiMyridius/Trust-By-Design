"""Baseline: bring any database up to the current models.

Before Alembic, the schema was built by Base.metadata.create_all plus the
hand-run migrate_*.py scripts in backend/. Databases in the wild are at
different points in that history, so instead of a fixed CREATE script this
revision is idempotent:
  * creates any missing tables (create_all skips existing ones), and
  * adds any column or index the models define but the table lacks.

Later revisions are ordinary Alembic migrations.

Revision ID: 0001_baseline
Revises:
Create Date: 2026-09-24
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0001_baseline"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _add_missing_columns_and_indexes(bind, metadata) -> None:
    inspector = sa.inspect(bind)
    preparer = bind.dialect.identifier_preparer

    for table in metadata.sorted_tables:
        existing_columns = {c["name"] for c in inspector.get_columns(table.name)}
        for column in table.columns:
            if column.name in existing_columns:
                continue
            col_type = column.type.compile(dialect=bind.dialect)
            ddl = (
                f"ALTER TABLE {preparer.format_table(table)} "
                f"ADD COLUMN {preparer.format_column(column)} {col_type}"
            )
            # Only enforce NOT NULL when there's a server default to fill
            # existing rows; otherwise the ALTER would fail on a non-empty
            # table. The ORM still supplies Python-side defaults on insert.
            if column.server_default is not None:
                default = column.server_default.arg
                if isinstance(default, str):
                    default = f"'{default}'"
                else:
                    default = str(default.compile(dialect=bind.dialect))
                ddl += f" DEFAULT {default}"
                if not column.nullable:
                    ddl += " NOT NULL"
            op.execute(ddl)

        existing_indexes = {i["name"] for i in inspector.get_indexes(table.name)}
        for index in table.indexes:
            if index.name not in existing_indexes:
                index.create(bind)


def upgrade() -> None:
    bind = op.get_bind()
    metadata = op.get_context().opts["target_metadata"]

    if bind.dialect.name == "postgresql":
        # pgvector's type must exist before document_embeddings is created.
        op.execute("CREATE EXTENSION IF NOT EXISTS vector")

    metadata.create_all(bind=bind)
    _add_missing_columns_and_indexes(bind, metadata)


def downgrade() -> None:
    # Baseline -- nothing to go back to.
    pass

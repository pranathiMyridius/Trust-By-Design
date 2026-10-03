import importlib
import pkgutil

from alembic import context
from sqlalchemy import text

import app.models
from app.database import IS_POSTGRES, Base, engine

# Import every model module so Base.metadata knows about all tables
# (needed by the baseline revision and by --autogenerate).
for module in pkgutil.iter_modules(app.models.__path__):
    importlib.import_module(f"app.models.{module.name}")

target_metadata = Base.metadata

# Arbitrary constant key for the Postgres advisory lock below.
MIGRATION_LOCK_KEY = 7482019


def _compare_type(context, inspected_column, metadata_column, inspected_type, metadata_type):
    # SQLite has no pgvector type (the column is stored as NUMERIC), so don't
    # let autogenerate report that as a change there.
    if not IS_POSTGRES and type(metadata_type).__name__ == "VECTOR":
        return False
    return None  # default comparison


def run_migrations_offline() -> None:
    context.configure(
        url=str(engine.url),
        target_metadata=target_metadata,
        literal_binds=True,
        render_as_batch=not IS_POSTGRES,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    # Same guard as app/migrations.py: a remote database is only touched
    # when the migration has been authorized for its host, so a bare
    # `alembic upgrade` can't bypass it.
    from app.migrations import ensure_authorized

    ensure_authorized(engine.url.render_as_string(hide_password=False))

    with engine.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            # SQLite can't ALTER most things in place; batch mode rebuilds
            # the table instead.
            render_as_batch=not IS_POSTGRES,
            compare_type=_compare_type,
        )
        with context.begin_transaction():
            if IS_POSTGRES:
                # Several app processes (uvicorn workers, --reload) may start
                # at once; only one should migrate at a time.
                connection.execute(
                    text("SELECT pg_advisory_xact_lock(:key)"),
                    {"key": MIGRATION_LOCK_KEY},
                )
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()

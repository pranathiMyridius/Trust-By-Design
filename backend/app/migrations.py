"""
Schema migrations, with a safety guard.

Importing the application never touches the database. The schema is
prepared by an explicit call -- `prepare_schema()` on server start-up, or
the CLI below -- and that call refuses to migrate a remote database unless
the migration has been authorized for that exact host:

  * Local databases (SQLite, or Postgres on localhost) are migrated
    automatically: these are development and test databases.
  * A remote database is migrated only when MIGRATION_AUTHORIZED_HOST
    equals its host name (or the CLI's --confirm-host does). A server
    start-up against a remote database that is behind the code fails with
    instructions instead of migrating.
  * A host listed in PROTECTED_DATABASE_HOSTS (e.g. the primary Neon
    endpoint) additionally needs MIGRATION_ALLOW_PROTECTED_HOST=true --
    a second, deliberate step.

alembic/env.py applies the same guard, so the raw `alembic` command
cannot bypass it.

CLI (run from backend/):

    python -m app.migrations status
    python -m app.migrations upgrade --confirm-host <host>
    python -m app.migrations upgrade --confirm-host <host> --allow-protected-host
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from pathlib import Path

from sqlalchemy.engine import make_url

logger = logging.getLogger(__name__)

ALEMBIC_INI = Path(__file__).resolve().parent.parent / "alembic.ini"

LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1"}

AUTHORIZED_HOST_ENV = "MIGRATION_AUTHORIZED_HOST"
PROTECTED_HOSTS_ENV = "PROTECTED_DATABASE_HOSTS"
ALLOW_PROTECTED_ENV = "MIGRATION_ALLOW_PROTECTED_HOST"


class MigrationNotAuthorized(RuntimeError):
    pass


def database_host(url: str) -> str | None:
    """None for SQLite (a local file); the host name otherwise."""

    parsed = make_url(url)
    if parsed.get_backend_name() == "sqlite":
        return None
    return (parsed.host or "localhost").lower()


def is_local(url: str) -> bool:
    host = database_host(url)
    return host is None or host in LOCAL_HOSTS


def _protected_hosts() -> set[str]:
    return {h.strip().lower() for h in os.getenv(PROTECTED_HOSTS_ENV, "").split(",") if h.strip()}


def _truthy(value: str | None) -> bool:
    return (value or "").strip().lower() in {"1", "true", "yes", "on"}


def authorization_problem(url: str) -> str | None:
    """Why migrating `url` is not authorized right now, or None."""

    if is_local(url):
        return None
    host = database_host(url)
    authorized = (os.getenv(AUTHORIZED_HOST_ENV) or "").strip().lower()
    if authorized != host:
        return (
            f"Migrating the remote database at {host} has not been authorized. "
            f"Set {AUTHORIZED_HOST_ENV}={host} for this run, or use "
            f"`python -m app.migrations upgrade --confirm-host {host}`."
        )
    if host in _protected_hosts() and not _truthy(os.getenv(ALLOW_PROTECTED_ENV)):
        return (
            f"{host} is listed in {PROTECTED_HOSTS_ENV}. Migrating it also needs "
            f"{ALLOW_PROTECTED_ENV}=true (CLI: --allow-protected-host)."
        )
    return None


def ensure_authorized(url: str) -> None:
    problem = authorization_problem(url)
    if problem:
        raise MigrationNotAuthorized(problem)


def _config():
    from alembic.config import Config

    return Config(str(ALEMBIC_INI))


def schema_status(url: str | None = None) -> dict:
    """Read-only: the database's revision and the code's head."""

    from alembic.runtime.migration import MigrationContext
    from alembic.script import ScriptDirectory
    from sqlalchemy import create_engine

    from app.database import DATABASE_URL

    url = url or DATABASE_URL
    heads = set(ScriptDirectory.from_config(_config()).get_heads())
    engine = create_engine(url)
    try:
        with engine.connect() as connection:
            current = set(MigrationContext.configure(connection).get_current_heads())
    finally:
        engine.dispose()
    return {
        "host": database_host(url) or "sqlite (local file)",
        "local": is_local(url),
        "current": sorted(current),
        "head": sorted(heads),
        "up_to_date": current == heads,
    }


def run_migrations() -> None:
    """alembic upgrade head on the configured database -- only when the
    guard allows it."""

    from alembic import command

    from app.database import DATABASE_URL

    ensure_authorized(DATABASE_URL)
    logger.info("Applying database migrations (alembic upgrade head) to %s", database_host(DATABASE_URL) or "SQLite")
    command.upgrade(_config(), "head")


def prepare_schema() -> None:
    """Server start-up: bring a local database up to date; for a remote
    one, migrate only if authorized, and refuse to start against a schema
    the code doesn't match."""

    from app.database import DATABASE_URL

    if is_local(DATABASE_URL) or authorization_problem(DATABASE_URL) is None:
        run_migrations()
        return

    status = schema_status(DATABASE_URL)
    if not status["up_to_date"]:
        raise MigrationNotAuthorized(
            f"The database at {status['host']} is at {status['current'] or 'no revision'} but the "
            f"code needs {status['head']}. It will not be migrated automatically. "
            + authorization_problem(DATABASE_URL)
        )
    logger.info("Remote database %s is at head %s; nothing to migrate.", status["host"], status["head"])


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m app.migrations")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("status", help="show the database revision and the code head (read-only)")
    upgrade = sub.add_parser("upgrade", help="alembic upgrade head, if authorized")
    upgrade.add_argument("--confirm-host", help="the database host you intend to migrate")
    upgrade.add_argument("--allow-protected-host", action="store_true")
    args = parser.parse_args(argv)

    from app.database import DATABASE_URL

    if args.command == "status":
        for key, value in schema_status(DATABASE_URL).items():
            print(f"{key}: {value}")
        return 0

    if args.confirm_host:
        os.environ[AUTHORIZED_HOST_ENV] = args.confirm_host
    if args.allow_protected_host:
        os.environ[ALLOW_PROTECTED_ENV] = "true"
    try:
        run_migrations()
    except MigrationNotAuthorized as exc:
        print(f"Refused: {exc}", file=sys.stderr)
        return 2
    print("Migrated:", schema_status(DATABASE_URL))
    return 0


if __name__ == "__main__":
    sys.exit(main())

"""
The migration safety guard (app/migrations.py, alembic/env.py):

  * importing the application never touches a database;
  * local databases migrate automatically;
  * a remote database migrates only when authorized for its exact host,
    and a protected host needs a second, explicit flag;
  * start-up against a remote database that is behind the code refuses
    to run instead of migrating it.

Subprocess tests use unroutable `.invalid` hosts: if anything tried to
connect, it would fail loudly -- and no real database is ever named.
"""

import os
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

import app.database as database
import app.migrations as migrations

BACKEND = Path(__file__).resolve().parents[2]
# P7: real remote URLs require TLS (app/database.py refuses others before
# the migration guard is reached), so these carry sslmode=require.
REMOTE = "postgresql://user:secret@db.example.invalid:5432/app?sslmode=require"
PROTECTED = "postgresql://user:secret@primary.example.invalid:5432/app?sslmode=require"


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for name in (migrations.AUTHORIZED_HOST_ENV, migrations.PROTECTED_HOSTS_ENV, migrations.ALLOW_PROTECTED_ENV):
        monkeypatch.delenv(name, raising=False)


def _run(args: list[str], database_url: str, **env) -> subprocess.CompletedProcess:
    full_env = {**os.environ, "DATABASE_URL": database_url, **env}
    for name in (migrations.AUTHORIZED_HOST_ENV, migrations.PROTECTED_HOSTS_ENV, migrations.ALLOW_PROTECTED_ENV):
        if name not in env:
            full_env.pop(name, None)
    return subprocess.run(
        [sys.executable, *args], cwd=BACKEND, env=full_env, capture_output=True, text=True, timeout=300
    )


# -- the rules -----------------------------------------------------------


def test_local_databases_need_no_authorization():
    assert migrations.authorization_problem("sqlite:///./x.db") is None
    assert migrations.authorization_problem("postgresql://u:p@localhost:5432/db") is None
    assert migrations.authorization_problem("postgresql://u:p@127.0.0.1/db") is None


def test_remote_database_needs_its_own_host_authorized(monkeypatch):
    assert "has not been authorized" in migrations.authorization_problem(REMOTE)

    monkeypatch.setenv(migrations.AUTHORIZED_HOST_ENV, "some-other-host.invalid")
    assert "has not been authorized" in migrations.authorization_problem(REMOTE)

    monkeypatch.setenv(migrations.AUTHORIZED_HOST_ENV, "db.example.invalid")
    assert migrations.authorization_problem(REMOTE) is None


def test_protected_host_needs_a_second_flag(monkeypatch):
    monkeypatch.setenv(migrations.PROTECTED_HOSTS_ENV, "primary.example.invalid, other.invalid")
    monkeypatch.setenv(migrations.AUTHORIZED_HOST_ENV, "primary.example.invalid")
    assert migrations.PROTECTED_HOSTS_ENV in migrations.authorization_problem(PROTECTED)

    monkeypatch.setenv(migrations.ALLOW_PROTECTED_ENV, "true")
    assert migrations.authorization_problem(PROTECTED) is None


def test_problem_messages_never_contain_credentials():
    assert "secret" not in migrations.authorization_problem(REMOTE)


# -- start-up --------------------------------------------------------------


def test_startup_refuses_a_remote_database_that_is_behind(monkeypatch):
    monkeypatch.setattr(database, "DATABASE_URL", REMOTE)
    monkeypatch.setattr(
        migrations,
        "schema_status",
        lambda url=None: {"host": "db.example.invalid", "local": False, "current": ["0016"], "head": ["0017"], "up_to_date": False},
    )
    upgrades = []
    monkeypatch.setattr(migrations, "run_migrations", lambda: upgrades.append(1))

    with pytest.raises(migrations.MigrationNotAuthorized, match="will not be migrated automatically"):
        migrations.prepare_schema()
    assert upgrades == []


def test_startup_runs_against_a_remote_database_already_at_head(monkeypatch):
    monkeypatch.setattr(database, "DATABASE_URL", REMOTE)
    monkeypatch.setattr(
        migrations,
        "schema_status",
        lambda url=None: {"host": "db.example.invalid", "local": False, "current": ["0017"], "head": ["0017"], "up_to_date": True},
    )
    upgrades = []
    monkeypatch.setattr(migrations, "run_migrations", lambda: upgrades.append(1))

    migrations.prepare_schema()
    assert upgrades == []


def test_startup_migrates_an_authorized_remote_database(monkeypatch):
    monkeypatch.setattr(database, "DATABASE_URL", REMOTE)
    monkeypatch.setenv(migrations.AUTHORIZED_HOST_ENV, "db.example.invalid")
    upgrades = []
    monkeypatch.setattr(migrations, "run_migrations", lambda: upgrades.append(1))

    migrations.prepare_schema()
    assert upgrades == [1]


# -- in a fresh process --------------------------------------------------


def test_importing_the_app_never_touches_a_local_database(tmp_path):
    db_file = tmp_path / "import-only.db"
    result = _run(["-c", "import app.main"], f"sqlite:///{db_file.as_posix()}")
    assert result.returncode == 0, result.stderr

    if db_file.exists():
        tables = sqlite3.connect(db_file).execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
        assert tables == []


def test_importing_the_app_never_connects_to_a_remote_database():
    # The host can't resolve: any connection attempt at import would fail.
    result = _run(["-c", "import app.main; print('imported')"], REMOTE)
    assert result.returncode == 0, result.stderr
    assert "imported" in result.stdout


def test_raw_alembic_cannot_migrate_an_unauthorized_remote_database():
    result = _run(["-m", "alembic", "upgrade", "head"], REMOTE)
    assert result.returncode != 0
    assert "has not been authorized" in result.stderr
    assert "secret" not in result.stderr


def test_cli_refuses_a_mismatched_host_and_a_protected_one_without_the_flag():
    wrong = _run(["-m", "app.migrations", "upgrade", "--confirm-host", "other.invalid"], REMOTE)
    assert wrong.returncode == 2 and "has not been authorized" in wrong.stderr

    protected = _run(
        ["-m", "app.migrations", "upgrade", "--confirm-host", "primary.example.invalid"],
        PROTECTED,
        PROTECTED_DATABASE_HOSTS="primary.example.invalid",
    )
    assert protected.returncode == 2 and "PROTECTED_DATABASE_HOSTS" in protected.stderr


def test_local_database_is_migrated_by_prepare_database(tmp_path):
    db_file = tmp_path / "prepared.db"
    result = _run(
        ["-c", "from app.main import prepare_database; prepare_database()"],
        f"sqlite:///{db_file.as_posix()}",
        ADMIN_BOOTSTRAP_PASSWORD="Adm1n-Test-Only!",
    )
    assert result.returncode == 0, result.stderr
    connection = sqlite3.connect(db_file)
    head = connection.execute("SELECT version_num FROM alembic_version").fetchone()[0]
    from alembic.config import Config
    from alembic.script import ScriptDirectory

    assert head == ScriptDirectory.from_config(Config(str(BACKEND / "alembic.ini"))).get_current_head()
    assert connection.execute("SELECT COUNT(*) FROM users").fetchone()[0] == 1

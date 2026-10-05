"""
Migration 0026 (ai_challenge_findings) on an isolated SQLite database at
0025. Subprocess alembic; never the suite's database or any remote one.
"""

import sqlite3
from pathlib import Path

import pytest

from tests.unit.test_migration_0017 import _alembic

HEAD = "0026_ai_challenge"
PREVIOUS = "0025_control_evidence"
TABLE = "ai_challenge_findings"


def _tables(db_file: Path) -> set[str]:
    connection = sqlite3.connect(db_file)
    try:
        return {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    finally:
        connection.close()


@pytest.fixture
def at_0025(tmp_path) -> Path:
    db_file = tmp_path / "at0025.db"
    assert _alembic(db_file, "upgrade", PREVIOUS).returncode == 0
    # The baseline uses today's models; put 0025 back by dropping the new table.
    connection = sqlite3.connect(db_file)
    connection.execute(f"DROP TABLE IF EXISTS {TABLE}")
    connection.commit()
    connection.close()
    return db_file


def test_upgrade_creates_the_table_and_rerun_is_safe(at_0025):
    assert TABLE not in _tables(at_0025)
    result = _alembic(at_0025, "upgrade", HEAD)
    assert result.returncode == 0, result.stderr
    assert TABLE in _tables(at_0025)
    assert _alembic(at_0025, "stamp", PREVIOUS).returncode == 0
    assert _alembic(at_0025, "upgrade", HEAD).returncode == 0


def test_round_trip_and_downgrade_refuses_after_a_reviewer_decision(at_0025):
    assert _alembic(at_0025, "upgrade", HEAD).returncode == 0
    down = _alembic(at_0025, "downgrade", PREVIOUS)
    assert down.returncode == 0, down.stderr
    assert TABLE not in _tables(at_0025)

    assert _alembic(at_0025, "upgrade", HEAD).returncode == 0
    connection = sqlite3.connect(at_0025)
    connection.execute("PRAGMA foreign_keys=OFF")
    connection.execute(
        f"INSERT INTO {TABLE} (assessment_id, run_id, category, severity, title, detail, status, generated_at) "
        "VALUES (1, 'run-1', 'OTHER', 'LOW', 'T', 'D', 'CONFIRMED', '2026-10-05')"
    )
    connection.commit()
    connection.close()
    refused = _alembic(at_0025, "downgrade", PREVIOUS)
    assert refused.returncode != 0 and "reviewer decision" in refused.stderr

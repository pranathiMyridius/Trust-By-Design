"""
Migration 0025 (control_evidence_links) on an isolated SQLite database at
0024. Subprocess alembic; never the suite's database or any remote one.
"""

import sqlite3
from pathlib import Path

import pytest

from tests.unit.test_migration_0017 import _alembic

HEAD = "0025_control_evidence"
PREVIOUS = "0024_committee_acceptance"


def _tables(db_file: Path) -> set[str]:
    connection = sqlite3.connect(db_file)
    try:
        return {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    finally:
        connection.close()


@pytest.fixture
def at_0024(tmp_path) -> Path:
    db_file = tmp_path / "at0024.db"
    assert _alembic(db_file, "upgrade", PREVIOUS).returncode == 0
    # The baseline uses today's models; put 0024 back by dropping the new table.
    connection = sqlite3.connect(db_file)
    connection.execute("DROP TABLE IF EXISTS control_evidence_links")
    connection.commit()
    connection.close()
    return db_file


def test_upgrade_creates_the_table_and_rerun_is_safe(at_0024):
    assert "control_evidence_links" not in _tables(at_0024)
    result = _alembic(at_0024, "upgrade", HEAD)
    assert result.returncode == 0, result.stderr
    assert "control_evidence_links" in _tables(at_0024)
    assert _alembic(at_0024, "stamp", PREVIOUS).returncode == 0
    assert _alembic(at_0024, "upgrade", HEAD).returncode == 0


def test_round_trip_and_downgrade_refuses_after_an_analyst_decision(at_0024):
    assert _alembic(at_0024, "upgrade", HEAD).returncode == 0
    down = _alembic(at_0024, "downgrade", PREVIOUS)
    assert down.returncode == 0, down.stderr
    assert "control_evidence_links" not in _tables(at_0024)

    assert _alembic(at_0024, "upgrade", HEAD).returncode == 0
    connection = sqlite3.connect(at_0024)
    connection.execute("PRAGMA foreign_keys=OFF")
    connection.execute(
        "INSERT INTO control_evidence_links (assessment_id, control_id, support_level, status, checked_at) "
        "VALUES (1, 1, 'SUPPORTED', 'ACCEPTED', '2026-10-05')"
    )
    connection.commit()
    connection.close()
    refused = _alembic(at_0024, "downgrade", PREVIOUS)
    assert refused.returncode != 0 and "analyst decision" in refused.stderr

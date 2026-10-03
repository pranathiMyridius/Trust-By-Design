"""
Migration 0024 (challenge findings: Committee acceptance) on an isolated
SQLite database really at 0023 -- the new columns removed, holding a
finding accepted under the earlier rule. Subprocess alembic; never the
suite's database or any remote one.
"""

import sqlite3
from pathlib import Path

import pytest

from tests.unit.test_migration_0017 import _alembic, _without_columns

NEW = ["accepted_by_id", "acceptance_authority"]
HEAD = "0024_committee_acceptance"


def _columns(db_file: Path) -> set[str]:
    connection = sqlite3.connect(db_file)
    try:
        return {row[1] for row in connection.execute("PRAGMA table_info(challenge_findings)")}
    finally:
        connection.close()


def _rows(db_file: Path, sql: str):
    connection = sqlite3.connect(db_file)
    try:
        return connection.execute(sql).fetchall()
    finally:
        connection.close()


@pytest.fixture
def at_0023(tmp_path) -> Path:
    db_file = tmp_path / "at0023.db"
    assert _alembic(db_file, "upgrade", "0023_source_files").returncode == 0
    connection = sqlite3.connect(db_file)
    # The baseline used today's models (accepted_by_id carries a foreign
    # key, which SQLite can't DROP); put 0023 back by rebuilding the table.
    _without_columns(connection, "challenge_findings", set(NEW))
    connection.execute(
        "INSERT INTO challenge_findings (id, assessment_id, category, description, related_section, severity, "
        "resolution_status, accepted_by, accepted_reason, detected_at) "
        "VALUES (1, 1, 'CONTRADICTION', 'Earlier acceptance', 'EVIDENCE', 'HIGH', 'ACCEPTED', 'Manager', 'Earlier rule', '2026-09-01')"
    )
    connection.commit()
    connection.close()
    return db_file


def test_upgrade_adds_the_columns_and_backfills_nothing(at_0023):
    result = _alembic(at_0023, "upgrade", HEAD)
    assert result.returncode == 0, result.stderr
    assert set(NEW) <= _columns(at_0023)
    # The earlier acceptance is kept as recorded, with no Committee authority.
    assert _rows(at_0023, "SELECT resolution_status, accepted_by, accepted_by_id, acceptance_authority FROM challenge_findings") == [
        ("ACCEPTED", "Manager", None, None)
    ]


def test_rerun_and_round_trip_without_committee_acceptances(at_0023):
    assert _alembic(at_0023, "upgrade", HEAD).returncode == 0
    assert _alembic(at_0023, "stamp", "0023_source_files").returncode == 0
    assert _alembic(at_0023, "upgrade", HEAD).returncode == 0
    down = _alembic(at_0023, "downgrade", "0023_source_files")
    assert down.returncode == 0, down.stderr
    assert not set(NEW) & _columns(at_0023)
    assert _rows(at_0023, "SELECT COUNT(*) FROM challenge_findings") == [(1,)]
    assert _alembic(at_0023, "upgrade", HEAD).returncode == 0


def test_downgrade_refuses_once_the_committee_has_accepted_a_finding(at_0023):
    assert _alembic(at_0023, "upgrade", HEAD).returncode == 0
    connection = sqlite3.connect(at_0023)
    connection.execute("UPDATE challenge_findings SET severity = 'MEDIUM', accepted_by_id = 7, acceptance_authority = 'COMMITTEE' WHERE id = 1")
    connection.commit()
    connection.close()
    result = _alembic(at_0023, "downgrade", "0023_source_files")
    assert result.returncode != 0 and "Committee exceptions" in result.stderr
    assert _rows(at_0023, "SELECT version_num FROM alembic_version") == [(HEAD,)]

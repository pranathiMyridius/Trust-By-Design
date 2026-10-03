"""
Migration 0023 (source library: original documents) on an isolated SQLite
database really at 0022 -- the new columns removed, holding a pasted source.
Subprocess alembic; never the suite's database or any remote one.
"""

import sqlite3
from pathlib import Path

import pytest

from tests.unit.test_migration_0017 import _alembic

NEW = ["original_filename", "file_path", "file_content_type", "file_size", "file_sha256"]


def _columns(db_file: Path) -> set[str]:
    connection = sqlite3.connect(db_file)
    try:
        return {row[1] for row in connection.execute("PRAGMA table_info(approved_sources)")}
    finally:
        connection.close()


def _rows(db_file: Path, sql: str):
    connection = sqlite3.connect(db_file)
    try:
        return connection.execute(sql).fetchall()
    finally:
        connection.close()


@pytest.fixture
def at_0022(tmp_path) -> Path:
    db_file = tmp_path / "at0022.db"
    assert _alembic(db_file, "upgrade", "0022_reassessment_approval").returncode == 0
    connection = sqlite3.connect(db_file)
    for column in NEW:  # the baseline used today's models; put 0022 back
        connection.execute(f"ALTER TABLE approved_sources DROP COLUMN {column}")
    connection.execute(
        "INSERT INTO approved_sources (id, title, source_type, version, content, status, created_at, updated_at) "
        "VALUES (1, 'Pasted policy', 'INTERNAL_POLICY', '1', 'Some text.', 'APPROVED', '2026-09-01', '2026-09-01')"
    )
    connection.commit()
    connection.close()
    return db_file


def test_upgrade_adds_the_columns_and_backfills_nothing(at_0022):
    result = _alembic(at_0022, "upgrade", "0023_source_files")
    assert result.returncode == 0, result.stderr
    assert set(NEW) <= _columns(at_0022)
    assert _rows(at_0022, "SELECT title, content, original_filename, file_path, file_sha256 FROM approved_sources") == [
        ("Pasted policy", "Some text.", None, None, None)
    ]


def test_rerun_and_round_trip_without_files(at_0022):
    assert _alembic(at_0022, "upgrade", "0023_source_files").returncode == 0
    assert _alembic(at_0022, "stamp", "0022_reassessment_approval").returncode == 0
    assert _alembic(at_0022, "upgrade", "0023_source_files").returncode == 0
    down = _alembic(at_0022, "downgrade", "0022_reassessment_approval")
    assert down.returncode == 0, down.stderr
    assert not set(NEW) & _columns(at_0022)
    assert _rows(at_0022, "SELECT COUNT(*) FROM approved_sources") == [(1,)]
    assert _alembic(at_0022, "upgrade", "0023_source_files").returncode == 0


def test_downgrade_refuses_once_a_source_has_a_file(at_0022):
    assert _alembic(at_0022, "upgrade", "0023_source_files").returncode == 0
    connection = sqlite3.connect(at_0022)
    connection.execute("UPDATE approved_sources SET original_filename = 'p.pdf', file_path = '/x/source_p.pdf' WHERE id = 1")
    connection.commit()
    connection.close()
    result = _alembic(at_0022, "downgrade", "0022_reassessment_approval")
    assert result.returncode != 0 and "stored original document" in result.stderr
    assert _rows(at_0022, "SELECT version_num FROM alembic_version") == [("0023_source_files",)]

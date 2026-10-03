"""
Migration 0021 (reassessment lifecycle) on an isolated SQLite database at
0020: the backfill is derived only from existing parent/child rows,
re-running is a no-op, a lossless downgrade and re-upgrade, and the
downgrade refusal once P6 trigger data exists. Subprocess alembic -- never
the suite's database or any remote one.

(The add-column path itself runs whenever a real 0020 database is
upgraded; SQLite can't drop a foreign-key column to rebuild that shape
here, so the columns start present and empty, as a fresh 0020 database
built from today's models has them.)
"""

import sqlite3
from pathlib import Path

import pytest

from tests.unit.test_migration_0017 import _alembic

SEED = """
INSERT INTO assessments (id, title, change_type, is_draft, status, escalation_level, analysis_is_provisional,
        requires_human_review, created_at, updated_at, parent_assessment_id, committee_decided_at)
VALUES
 (1, 'parent superseded', 'NEW_PRODUCT', 0, 'APPROVED', 0, 0, 0, '2025-01-01', '2025-01-01', NULL, '2025-01-02'),
 (2, 'child approved',    'NEW_PRODUCT', 0, 'APPROVED', 0, 0, 0, '2026-01-01', '2026-01-01', 1,    '2026-02-01 10:00:00'),
 (3, 'parent under',      'NEW_PRODUCT', 0, 'APPROVED', 0, 0, 0, '2025-01-01', '2025-01-01', NULL, '2025-01-02'),
 (4, 'child in progress', 'NEW_PRODUCT', 0, 'INTAKE',   0, 0, 0, '2026-09-01', '2026-09-01', 3,    NULL),
 (5, 'parent released',   'NEW_PRODUCT', 0, 'APPROVED', 0, 0, 0, '2025-01-01', '2025-01-01', NULL, '2025-01-02'),
 (6, 'child rejected',    'NEW_PRODUCT', 0, 'REJECTED', 0, 0, 0, '2026-03-01', '2026-03-01', 5,    '2026-04-01'),
 (7, 'no reassessment',   'NEW_PRODUCT', 0, 'APPROVED', 0, 0, 0, '2025-01-01', '2025-01-01', NULL, '2025-01-02');
"""


@pytest.fixture
def at_0020(tmp_path) -> Path:
    db_file = tmp_path / "at0020.db"
    result = _alembic(db_file, "upgrade", "0020_evidence_traceability")
    assert result.returncode == 0, result.stderr
    connection = sqlite3.connect(db_file)
    connection.executescript(SEED)
    connection.commit()
    connection.close()
    return db_file


def _rows(db_file: Path, sql: str):
    connection = sqlite3.connect(db_file)
    try:
        return connection.execute(sql).fetchall()
    finally:
        connection.close()


STATE = "SELECT id, status, reassessment_state, superseded_by_id, superseded_at IS NOT NULL FROM assessments ORDER BY id"


def test_backfill_is_derived_from_existing_reassessments_only(at_0020):
    result = _alembic(at_0020, "upgrade", "0021_reassessment_lifecycle")
    assert result.returncode == 0, result.stderr
    assert _rows(at_0020, "SELECT version_num FROM alembic_version") == [("0021_reassessment_lifecycle",)]
    assert _rows(at_0020, STATE) == [
        (1, "APPROVED", "SUPERSEDED", 2, 1),           # its reassessment was approved
        (2, "APPROVED", None, None, 0),
        (3, "APPROVED", "UNDER_REASSESSMENT", None, 0),  # reassessment still open
        (4, "INTAKE", None, None, 0),
        (5, "APPROVED", None, None, 0),                 # reassessment rejected: still in force
        (6, "REJECTED", None, None, 0),
        (7, "APPROVED", None, None, 0),
    ]
    # Pipeline statuses are untouched.
    assert [r[1] for r in _rows(at_0020, STATE)] == ["APPROVED", "APPROVED", "APPROVED", "INTAKE", "APPROVED", "REJECTED", "APPROVED"]


def test_rerun_is_a_no_op_and_downgrade_round_trips(at_0020):
    assert _alembic(at_0020, "upgrade", "0021_reassessment_lifecycle").returncode == 0
    first = _rows(at_0020, STATE)
    assert _alembic(at_0020, "stamp", "0020_evidence_traceability").returncode == 0
    assert _alembic(at_0020, "upgrade", "0021_reassessment_lifecycle").returncode == 0
    assert _rows(at_0020, STATE) == first

    down = _alembic(at_0020, "downgrade", "0020_evidence_traceability")
    assert down.returncode == 0, down.stderr
    columns = {r[1] for r in _rows(at_0020, "PRAGMA table_info(assessments)")}
    assert not {"reassessment_state", "superseded_by_id", "superseded_at"} & columns
    assert _rows(at_0020, "SELECT COUNT(*) FROM assessments") == [(7,)]
    assert _alembic(at_0020, "upgrade", "0021_reassessment_lifecycle").returncode == 0
    assert _rows(at_0020, STATE) == first


def test_downgrade_refuses_once_p6_trigger_data_exists(at_0020):
    assert _alembic(at_0020, "upgrade", "0021_reassessment_lifecycle").returncode == 0
    connection = sqlite3.connect(at_0020)
    connection.execute(
        "INSERT INTO reassessment_triggers (assessment_id, trigger_type, description, detected_at, status, resolved_by_id) "
        "VALUES (7, 'NEW_VENDOR', 'x', '2026-10-03', 'ACKNOWLEDGED', 1)"
    )
    connection.commit()
    connection.close()
    result = _alembic(at_0020, "downgrade", "0020_evidence_traceability")
    assert result.returncode != 0 and "reassessment trigger" in result.stderr
    assert _rows(at_0020, "SELECT version_num FROM alembic_version") == [("0021_reassessment_lifecycle",)]

"""
Migration 0019 (retention lifecycle) against a database that really is at
0018: the new tables dropped and the new columns removed, holding an
edited legacy retention policy (3,650 days) and an assessment on legal
hold. Subprocess alembic, isolated SQLite -- never the suite's database or
any remote one.
"""

import sqlite3
from pathlib import Path

import pytest

from tests.unit.test_migration_0017 import _alembic, _without_columns


def _at_0018(tmp_path: Path) -> Path:
    db_file = tmp_path / "at0018.db"
    result = _alembic(db_file, "upgrade", "0018_sod_governance")
    assert result.returncode == 0, result.stderr

    connection = sqlite3.connect(db_file)
    # The baseline's create_all used today's models; put 0018 back.
    connection.executescript("DROP TABLE legal_hold_events; DROP TABLE retention_policy_versions;")
    _without_columns(connection, "assessment_retention", {"legal_hold_set_by_id", "retention_policy_version_id"})
    connection.executescript(
        """
        INSERT INTO retention_policies (id, name, is_active, default_retention_days, created_at, updated_at)
            VALUES (1, 'Default Retention Policy', 1, 3650, '2025-01-01 00:00:00', '2026-06-01 00:00:00');
        INSERT INTO assessment_retention (id, assessment_id, legal_hold, legal_hold_reason, legal_hold_set_by,
                legal_hold_set_at, is_deleted, created_at, updated_at)
            VALUES (1, 7, 1, 'Regulator inquiry', 'Old Admin', '2026-09-01 09:00:00', 0, '2026-09-01', '2026-09-01'),
                   (2, 8, 0, NULL, NULL, NULL, 0, '2026-09-01', '2026-09-01');
        """
    )
    connection.commit()
    connection.close()
    return db_file


@pytest.fixture
def at_0018(tmp_path):
    return _at_0018(tmp_path)


def _rows(db_file: Path, sql: str):
    connection = sqlite3.connect(db_file)
    try:
        return connection.execute(sql).fetchall()
    finally:
        connection.close()


def test_upgrade_backfills_version_1_and_existing_holds(at_0018):
    result = _alembic(at_0018, "upgrade", "0019_retention_lifecycle")
    assert result.returncode == 0, result.stderr

    assert _rows(at_0018, "SELECT version_num FROM alembic_version") == [("0019_retention_lifecycle",)]
    # The edited legacy value is carried over as v1, marked as migrated and not approved.
    assert _rows(
        at_0018,
        "SELECT record_type, version, retention_days, basis, status, policy_status, change_reason, system_seeded, "
        "governance_approval_reference FROM retention_policy_versions",
    ) == [
        ("ASSESSMENT", 1, 3650, "FINAL_DECISION_DATE", "ACTIVE", "PROVISIONAL_PENDING_GOVERNANCE_APPROVAL",
         "Migrated existing default; not compliance-approved", 1, None)
    ]
    # One SET event for the assessment on hold; none for the other.
    assert _rows(at_0018, "SELECT assessment_id, action, reason, actor_name, system_seeded FROM legal_hold_events") == [
        (7, "SET", "Backfilled from pre-P5 state: Regulator inquiry", "Old Admin", 1)
    ]
    # Existing rows are unchanged; the legacy policy row is kept.
    assert _rows(at_0018, "SELECT assessment_id, legal_hold, legal_hold_reason, legal_hold_set_by_id, retention_policy_version_id FROM assessment_retention ORDER BY id") == [
        (7, 1, "Regulator inquiry", None, None),
        (8, 0, None, None, None),
    ]
    assert _rows(at_0018, "SELECT default_retention_days FROM retention_policies") == [(3650,)]
    indexes = {row[0] for row in _rows(at_0018, "SELECT name FROM sqlite_master WHERE type='index'")}
    assert {"uq_retention_policy_one_active", "uq_retention_policy_one_proposed"} <= indexes


def test_upgrade_is_idempotent_and_downgrade_round_trips_when_only_backfill_exists(at_0018):
    assert _alembic(at_0018, "upgrade", "0019_retention_lifecycle").returncode == 0
    assert _alembic(at_0018, "stamp", "0018_sod_governance").returncode == 0
    rerun = _alembic(at_0018, "upgrade", "0019_retention_lifecycle")
    assert rerun.returncode == 0, rerun.stderr
    assert _rows(at_0018, "SELECT COUNT(*) FROM retention_policy_versions") == [(1,)]
    assert _rows(at_0018, "SELECT COUNT(*) FROM legal_hold_events") == [(1,)]

    down = _alembic(at_0018, "downgrade", "0018_sod_governance")
    assert down.returncode == 0, down.stderr
    tables = {row[0] for row in _rows(at_0018, "SELECT name FROM sqlite_master WHERE type='table'")}
    assert "retention_policy_versions" not in tables and "legal_hold_events" not in tables
    assert _rows(at_0018, "SELECT COUNT(*), SUM(legal_hold) FROM assessment_retention") == [(2, 1)]
    assert _rows(at_0018, "SELECT default_retention_days FROM retention_policies") == [(3650,)]
    assert _alembic(at_0018, "upgrade", "0019_retention_lifecycle").returncode == 0


@pytest.mark.parametrize(
    "seed, message",
    [
        (
            "INSERT INTO retention_policy_versions (record_type, version, retention_days, basis, status, policy_status, "
            "change_reason, proposed_at, system_seeded, created_at, row_version) VALUES ('ASSESSMENT', 2, 3000, "
            "'FINAL_DECISION_DATE', 'PROPOSED', 'PROVISIONAL_PENDING_GOVERNANCE_APPROVAL', 'a proposal after P5', "
            "'2026-10-02', 0, '2026-10-02', 1)",
            "retention policy version(s)",
        ),
        (
            "INSERT INTO legal_hold_events (assessment_id, action, reason, actor_name, system_seeded, created_at) "
            "VALUES (8, 'SET', 'new hold', 'Admin', 0, '2026-10-02')",
            "legal hold event(s)",
        ),
        ("UPDATE assessment_retention SET retention_policy_version_id = 1 WHERE id = 2", "soft delete(s)"),
    ],
)
def test_downgrade_refuses_to_discard_lifecycle_history(at_0018, seed, message):
    assert _alembic(at_0018, "upgrade", "0019_retention_lifecycle").returncode == 0
    connection = sqlite3.connect(at_0018)
    connection.execute(seed)
    connection.commit()
    connection.close()
    result = _alembic(at_0018, "downgrade", "0018_sod_governance")
    assert result.returncode != 0 and message in result.stderr
    assert _rows(at_0018, "SELECT version_num FROM alembic_version") == [("0019_retention_lifecycle",)]

"""
Migration 0018 (SoD governance) against a database that really is at 0017:
the new tables dropped and the new columns removed, holding a legacy
sign-off, a legacy override and a user. Subprocess alembic, isolated
SQLite -- never the suite's database or any remote one.
"""

import sqlite3
from pathlib import Path

import pytest

from tests.unit.test_migration_0017 import _alembic, _without_columns


def _at_0017(tmp_path: Path) -> Path:
    db_file = tmp_path / "at0017.db"
    result = _alembic(db_file, "upgrade", "0017_governance_records")
    assert result.returncode == 0, result.stderr

    connection = sqlite3.connect(db_file)
    connection.executescript("DROP TABLE sod_exception_events; DROP TABLE sod_exceptions;")
    # Nor do the later (0019, P5) retention objects exist at 0017.
    connection.executescript("DROP TABLE legal_hold_events; DROP TABLE retention_policy_versions;")
    _without_columns(connection, "assessment_retention", {"legal_hold_set_by_id", "retention_policy_version_id"})
    _without_columns(connection, "users", {"governance_designations"})
    _without_columns(connection, "risk_factors", {"rated_by_id"})
    _without_columns(
        connection,
        "assessment_overrides",
        {"origin", "materiality", "materiality_reasons", "approval_status", "approved_by", "approved_by_id", "approved_at", "approval_rationale"},
    )
    _without_columns(connection, "challenge_review_signoffs", {"stage", "committee_escalation"})
    connection.executescript(
        """
        INSERT INTO users (id, email, hashed_password, full_name, role, is_active, created_at)
            VALUES (1, 'u@x', 'h', 'User', 'FCRM_ANALYST', 1, '2026-09-01');
        INSERT INTO assessment_overrides (id, assessment_id, section, field_name, ai_value, human_value, reason, created_at, review_status)
            VALUES (1, 7, 'FACTOR_RATING', 'x', '3 x 3', '4 x 4', 'legacy', '2026-09-02', 'APPLIED');
        INSERT INTO challenge_review_signoffs (id, assessment_id, outcome, reason, trigger_snapshot, findings_snapshot,
                reviewer, reviewer_id, reviewer_role, completed_at, version, is_current)
            VALUES (1, 7, 'NO_TRIGGERS_FIRED', 'P2 sign-off', '{}', '[]', 'User', 1, 'FCRM_ANALYST', '2026-10-02', 1, 1);
        """
    )
    connection.commit()
    connection.close()
    return db_file


@pytest.fixture
def at_0017(tmp_path):
    return _at_0017(tmp_path)


def test_upgrade_keeps_legacy_rows_and_labels_nothing(at_0017):
    # Pinned to 0018 itself (P5 added 0019 on top).
    result = _alembic(at_0017, "upgrade", "0018_sod_governance")
    assert result.returncode == 0, result.stderr

    connection = sqlite3.connect(at_0017)
    assert connection.execute("SELECT version_num FROM alembic_version").fetchone()[0] == "0018_sod_governance"
    # The P2 sign-off becomes a SIGNOFF row, not escalated.
    assert connection.execute("SELECT stage, committee_escalation, reason FROM challenge_review_signoffs").fetchall() == [("SIGNOFF", 0, "P2 sign-off")]
    # The legacy override is kept and not classified after the fact.
    assert connection.execute("SELECT human_value, review_status, origin, materiality, approval_status FROM assessment_overrides").fetchall() == [
        ("4 x 4", "APPLIED", None, None, None)
    ]
    assert connection.execute("SELECT governance_designations FROM users").fetchall() == [(None,)]
    tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"sod_exceptions", "sod_exception_events"} <= tables
    connection.close()


def test_upgrade_reruns_and_downgrade_round_trips(at_0017):
    assert _alembic(at_0017, "upgrade", "head").returncode == 0
    assert _alembic(at_0017, "stamp", "0017_governance_records").returncode == 0
    rerun = _alembic(at_0017, "upgrade", "head")
    assert rerun.returncode == 0, rerun.stderr

    down = _alembic(at_0017, "downgrade", "0017_governance_records")
    assert down.returncode == 0, down.stderr
    connection = sqlite3.connect(at_0017)
    assert connection.execute("SELECT COUNT(*) FROM challenge_review_signoffs").fetchone()[0] == 1
    assert connection.execute("SELECT COUNT(*) FROM assessment_overrides").fetchone()[0] == 1
    tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert "sod_exceptions" not in tables
    connection.close()
    assert _alembic(at_0017, "upgrade", "head").returncode == 0


@pytest.mark.parametrize(
    "seed, message",
    [
        (
            "INSERT INTO sod_exceptions (id, exception_type, status, requestor_id, affected_user_id, conflicting_roles, "
            "business_justification, standard_workflow_reason, risk_level, compensating_controls, start_at, end_at, "
            "repeated, created_at, updated_at, row_version) VALUES (1, 'COMMITTEE_SEPARATION', 'DRAFT', 1, 1, '[]', "
            "'j', 'r', 'LOW', 'c', '2026-10-02', '2026-10-09', 0, '2026-10-02', '2026-10-02', 1)",
            "SoD exception",
        ),
        (
            "INSERT INTO challenge_review_signoffs (assessment_id, stage, committee_escalation, outcome, reason, trigger_snapshot, "
            "findings_snapshot, reviewer, reviewer_id, reviewer_role, completed_at, version, is_current) "
            "VALUES (7, 'REVIEW', 0, 'NO_TRIGGERS_FIRED', 'r', '{}', '[]', 'U', 1, 'FCRM_ANALYST', '2026-10-02', 1, 1)",
            "independent challenge review",
        ),
    ],
)
def test_downgrade_refuses_to_discard_governance_records(at_0017, seed, message):
    assert _alembic(at_0017, "upgrade", "head").returncode == 0
    connection = sqlite3.connect(at_0017)
    connection.execute(seed)
    connection.commit()
    connection.close()
    result = _alembic(at_0017, "downgrade", "0017_governance_records")
    assert result.returncode != 0 and message in result.stderr

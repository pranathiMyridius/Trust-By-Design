"""
Migration 0022: only a previously approved assessment is "under
reassessment". Subprocess alembic on isolated SQLite -- never the suite's
database or any remote one.

Seeded parents (each with the reassessment rows production-shaped data has):
  1  approved, then reassessed (open child 2)        -> UNDER_REASSESSMENT
  3  never approved, three historical open children  -> in force (the #6 case)
  7  approved, no reassessment                       -> in force
  9  approved only AFTER its open child was created  -> in force
 11  approved, its reassessment approved (child 12)  -> SUPERSEDED (untouched)
 13  never approved, its only child rejected         -> in force
 15  approved status (no decision value) + decision time, open child 16
                                                     -> UNDER_REASSESSMENT
 17  approved status but no decision time, open child 18
                                                     -> in force (can't show "before")
"""

import sqlite3
from pathlib import Path

import pytest

from tests.unit.test_migration_0017 import _alembic

SEED = """
INSERT INTO assessments (id, title, change_type, is_draft, status, workflow_status, escalation_level,
        analysis_is_provisional, requires_human_review, created_at, updated_at, parent_assessment_id,
        committee_decision, committee_decided_at)
VALUES
 (1,  'approved parent',          'NEW_PRODUCT', 0, 'APPROVED', 'APPROVED', 0, 0, 0, '2025-01-01', '2025-01-01', NULL, 'APPROVED', '2025-01-02 10:00:00'),
 (2,  'its open reassessment',    'NEW_PRODUCT', 0, 'INTAKE', 'SUBMITTED', 0, 0, 0, '2026-09-01 09:00:00', '2026-09-01', 1, NULL, NULL),
 (3,  'never-approved parent',    'NEW_PRODUCT', 0, 'INHERENT_RISK_ASSESSMENT', 'RISK_ASSESSMENT_IN_PROGRESS', 0, 0, 0, '2026-09-23 06:16:10', '2026-09-23', NULL, NULL, NULL),
 (4,  'historical child A',       'NEW_PRODUCT', 0, 'RISK_IDENTIFICATION', 'RISK_ASSESSMENT_IN_PROGRESS', 0, 0, 0, '2026-09-23 13:43:59', '2026-09-23', 3, NULL, NULL),
 (5,  'historical child B',       'NEW_PRODUCT', 0, 'INTAKE', 'SUBMITTED', 0, 0, 0, '2026-09-23 13:44:45', '2026-09-23', 3, NULL, NULL),
 (6,  'historical child C',       'NEW_PRODUCT', 0, 'EVIDENCE_COLLECTION', 'INTAKE_VALIDATION', 0, 0, 0, '2026-09-23 17:22:00', '2026-09-23', 3, NULL, NULL),
 (7,  'approved, no reassessment','NEW_PRODUCT', 0, 'APPROVED', 'APPROVED', 0, 0, 0, '2025-01-01', '2025-01-01', NULL, 'APPROVED', '2025-01-02 10:00:00'),
 (9,  'approved after its child', 'NEW_PRODUCT', 0, 'APPROVED_WITH_CONDITIONS', 'APPROVED_WITH_CONDITIONS', 0, 0, 0, '2025-01-01', '2025-01-01', NULL, 'APPROVED_WITH_CONDITIONS', '2026-05-01 10:00:00'),
 (10, 'child older than approval','NEW_PRODUCT', 0, 'INTAKE', 'SUBMITTED', 0, 0, 0, '2026-04-01 09:00:00', '2026-04-01', 9, NULL, NULL),
 (11, 'superseded parent',        'NEW_PRODUCT', 0, 'APPROVED', 'APPROVED', 0, 0, 0, '2025-01-01', '2025-01-01', NULL, 'APPROVED', '2025-01-02 10:00:00'),
 (12, 'approved reassessment',    'NEW_PRODUCT', 0, 'APPROVED', 'APPROVED', 0, 0, 0, '2026-01-01', '2026-01-01', 11, 'APPROVED', '2026-02-01 10:00:00'),
 (13, 'never approved, rejected child', 'NEW_PRODUCT', 0, 'HUMAN_REVIEW', 'ANALYST_REVIEW', 0, 0, 0, '2026-01-01', '2026-01-01', NULL, NULL, NULL),
 (14, 'rejected child',           'NEW_PRODUCT', 0, 'REJECTED', 'REJECTED', 0, 0, 0, '2026-02-01', '2026-02-01', 13, 'REJECTED', '2026-03-01'),
 (15, 'approved status only',     'NEW_PRODUCT', 0, 'APPROVED', 'APPROVED', 0, 0, 0, '2025-01-01', '2025-01-01', NULL, NULL, '2025-01-02 10:00:00'),
 (16, 'its open reassessment',    'NEW_PRODUCT', 0, 'INTAKE', 'SUBMITTED', 0, 0, 0, '2026-09-01 09:00:00', '2026-09-01', 15, NULL, NULL),
 (17, 'approved, no decision time','NEW_PRODUCT', 0, 'APPROVED', 'APPROVED', 0, 0, 0, '2025-01-01', '2025-01-01', NULL, 'APPROVED', NULL),
 (18, 'its open reassessment',    'NEW_PRODUCT', 0, 'INTAKE', 'SUBMITTED', 0, 0, 0, '2026-09-01 09:00:00', '2026-09-01', 17, NULL, NULL);
INSERT INTO reassessment_triggers (id, assessment_id, trigger_type, description, detected_at, detected_by, status,
        resolved_by, resolved_at, reassessment_id)
VALUES
 (1, 3, 'MAJOR_PRODUCT_CHANGE', 'Historical request A', '2026-09-23 13:43:59', 'QA', 'REASSESSMENT_CREATED', 'QA', '2026-09-23 13:43:59', 4),
 (2, 3, 'MAJOR_PRODUCT_CHANGE', 'Historical request B', '2026-09-23 13:44:46', 'QA', 'REASSESSMENT_CREATED', 'QA', '2026-09-23 13:44:46', 5),
 (3, 3, 'NEW_GEOGRAPHY',        'Historical request C', '2026-09-23 17:22:01', 'QA', 'REASSESSMENT_CREATED', 'QA', '2026-09-23 17:22:01', 6),
 (4, 1, 'PERIODIC_REVIEW',      'Annual review',        '2026-08-30 09:00:00', 'System', 'REASSESSMENT_CREATED', 'QA', '2026-09-01 09:00:00', 2);
"""

EXPECTED_STATES = {
    1: "UNDER_REASSESSMENT",
    3: None,
    7: None,
    9: None,
    11: "SUPERSEDED",
    13: None,
    15: "UNDER_REASSESSMENT",
    17: None,
}

# Everything but the columns 0021 derives (reassessment_state, superseded_*).
ASSESSMENT_COLUMNS = (
    "id, title, status, workflow_status, is_draft, parent_assessment_id, committee_decision, committee_decided_at, "
    "created_at, updated_at"
)


def _rows(db_file: Path, sql: str):
    connection = sqlite3.connect(db_file)
    try:
        return connection.execute(sql).fetchall()
    finally:
        connection.close()


def _seed(db_file: Path) -> None:
    connection = sqlite3.connect(db_file)
    connection.executescript(SEED)
    connection.commit()
    connection.close()


def _states(db_file: Path) -> dict:
    return dict(_rows(db_file, "SELECT id, reassessment_state FROM assessments WHERE parent_assessment_id IS NULL"))


def _fingerprint(db_file: Path) -> tuple:
    """Everything 0022 must leave alone: every assessment row except the
    flag, every reassessment trigger row, and the row counts."""

    return (
        _rows(db_file, f"SELECT {ASSESSMENT_COLUMNS} FROM assessments ORDER BY id"),
        # Named columns: a 0021 round trip re-adds its columns at the end.
        _rows(
            db_file,
            "SELECT id, assessment_id, trigger_type, description, detected_at, detected_by, status, "
            "dismissed_reason, resolved_by, resolved_at, reassessment_id FROM reassessment_triggers ORDER BY id",
        ),
        _rows(db_file, "SELECT COUNT(*) FROM assessments"),
        _rows(db_file, "SELECT COUNT(*) FROM reassessment_triggers"),
    )


@pytest.fixture
def at_0020(tmp_path) -> Path:
    db_file = tmp_path / "at0020.db"
    assert _alembic(db_file, "upgrade", "0020_evidence_traceability").returncode == 0
    _seed(db_file)
    return db_file


def test_staging_path_0021_wrongly_flags_and_0022_corrects(at_0020):
    before = _fingerprint(at_0020)
    assert _alembic(at_0020, "upgrade", "0021_reassessment_lifecycle").returncode == 0
    # The defect: 0021 flags the never-approved parent and the late approval.
    assert _states(at_0020)[3] == "UNDER_REASSESSMENT"
    assert _states(at_0020)[9] == "UNDER_REASSESSMENT"
    whole_rows_0021 = _rows(at_0020, "SELECT * FROM assessments ORDER BY id")

    result = _alembic(at_0020, "upgrade", "0022_reassessment_approval")
    assert result.returncode == 0, result.stderr
    assert _states(at_0020) == EXPECTED_STATES
    assert _fingerprint(at_0020) == before  # no row added, deleted or otherwise changed

    # 0022 itself changed exactly one column on exactly the wrongly flagged rows.
    columns = [row[1] for row in _rows(at_0020, "PRAGMA table_info(assessments)")]
    flag = columns.index("reassessment_state")
    changed = {}
    for old, new in zip(whole_rows_0021, _rows(at_0020, "SELECT * FROM assessments ORDER BY id")):
        differing = [columns[i] for i in range(len(columns)) if old[i] != new[i]]
        if differing:
            changed[old[0]] = (differing, old[flag], new[flag])
    assert changed == {
        3: (["reassessment_state"], "UNDER_REASSESSMENT", None),
        9: (["reassessment_state"], "UNDER_REASSESSMENT", None),
        17: (["reassessment_state"], "UNDER_REASSESSMENT", None),
    }


def test_production_path_from_an_unflagged_database_straight_to_head(at_0020):
    # Production upgrades 0017 -> head in one run: 0021 then 0022 back to back.
    before = _fingerprint(at_0020)
    result = _alembic(at_0020, "upgrade", "0022_reassessment_approval")
    assert result.returncode == 0, result.stderr
    assert _rows(at_0020, "SELECT version_num FROM alembic_version") == [("0022_reassessment_approval",)]
    assert _states(at_0020) == EXPECTED_STATES
    assert _fingerprint(at_0020) == before


def test_rerun_is_a_no_op_and_downgrade_round_trips(at_0020):
    assert _alembic(at_0020, "upgrade", "0022_reassessment_approval").returncode == 0
    after = (_states(at_0020), _fingerprint(at_0020))

    # Idempotent.
    assert _alembic(at_0020, "stamp", "0021_reassessment_lifecycle").returncode == 0
    assert _alembic(at_0020, "upgrade", "0022_reassessment_approval").returncode == 0
    assert (_states(at_0020), _fingerprint(at_0020)) == after

    # Downgrade 0022 is a no-op: the corrected flags and every row stay.
    down = _alembic(at_0020, "downgrade", "0021_reassessment_lifecycle")
    assert down.returncode == 0, down.stderr
    assert (_states(at_0020), _fingerprint(at_0020)) == after

    # Further back and up again: data preserved, rule applied again.
    assert _alembic(at_0020, "downgrade", "0020_evidence_traceability").returncode == 0
    assert _rows(at_0020, "SELECT COUNT(*) FROM assessments") == [(17,)]
    assert _rows(at_0020, "SELECT COUNT(*) FROM reassessment_triggers") == [(4,)]
    assert _alembic(at_0020, "upgrade", "0022_reassessment_approval").returncode == 0
    assert (_states(at_0020), _fingerprint(at_0020)) == after


def test_every_revision_id_fits_postgres_alembic_version():
    # alembic_version.version_num is VARCHAR(32). SQLite doesn't enforce the
    # length, Postgres does: a longer id fails only on the real database
    # (0022 was first named with 37 characters and failed on staging).
    from alembic.config import Config
    from alembic.script import ScriptDirectory

    backend = Path(__file__).resolve().parents[2]
    revisions = list(ScriptDirectory.from_config(Config(str(backend / "alembic.ini"))).walk_revisions())
    too_long = [r.revision for r in revisions if len(r.revision) > 32]
    assert len(revisions) >= 22 and too_long == []


def test_full_chain_from_0017(tmp_path):
    from tests.unit.test_migration_0018 import _at_0017

    db_file = _at_0017(tmp_path)  # a database really at 0017 (later objects removed)
    connection = sqlite3.connect(db_file)
    connection.execute("DELETE FROM assessment_overrides")  # the 0017 fixture's row points at assessment 7
    connection.execute("DELETE FROM challenge_review_signoffs")
    connection.commit()
    connection.close()
    _seed(db_file)
    before = _fingerprint(db_file)

    result = _alembic(db_file, "upgrade", "0022_reassessment_approval")
    assert result.returncode == 0, result.stderr
    assert _rows(db_file, "SELECT version_num FROM alembic_version") == [("0022_reassessment_approval",)]
    assert _states(db_file) == EXPECTED_STATES
    assert _fingerprint(db_file) == before

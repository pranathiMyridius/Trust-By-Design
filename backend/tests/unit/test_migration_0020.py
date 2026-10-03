"""
Migration 0020 (evidence traceability) against a database that really is
at 0019: the new tables dropped and the new columns removed, holding a
profile, a risk factor and an approved-source link. Subprocess alembic,
isolated SQLite -- never the suite's database or any remote one.
"""

import sqlite3
from pathlib import Path

import pytest

from tests.unit.test_migration_0017 import _alembic, _without_columns

NEW_COLUMNS = {
    "assessment_intelligence": {"field_provenance", "extraction_method", "extracted_at", "source_document_ids"},
    "risk_factors": {"rule_triggers"},
    "source_evidence_links": {"outdated_at_attach", "outdated_acknowledgement_reason", "outdated_acknowledged_by_id"},
}


def _at_0019(tmp_path: Path) -> Path:
    db_file = tmp_path / "at0019.db"
    result = _alembic(db_file, "upgrade", "0019_retention_lifecycle")
    assert result.returncode == 0, result.stderr

    connection = sqlite3.connect(db_file)
    # The baseline's create_all used today's models; put 0019 back.
    connection.executescript("DROP TABLE intake_snapshots; DROP TABLE evidence_acknowledgements;")
    for table, columns in NEW_COLUMNS.items():
        _without_columns(connection, table, columns)
    connection.executescript(
        """
        INSERT INTO assessment_intelligence (id, assessment_id, countries, confirmed, created_at)
            VALUES (1, 7, '["Germany"]', 1, '2026-09-01');
        INSERT INTO risk_factors (id, assessment_id, category, applicable, score, severity, rationale, source,
                excluded, version, is_current, created_at)
            VALUES (1, 7, 'GEOGRAPHIC_RISK', 1, 0, 'LOW', 'legacy', 'AI', 0, 1, 1, '2026-09-01');
        INSERT INTO source_evidence_links (id, assessment_id, risk_factor_id, source_id, source_title, source_type,
                source_version, passage, retrieved_at)
            VALUES (1, 7, 1, 1, 'Policy', 'INTERNAL_POLICY', '1', 'passage', '2026-09-01');
        """
    )
    connection.commit()
    connection.close()
    return db_file


@pytest.fixture
def at_0019(tmp_path):
    return _at_0019(tmp_path)


def _rows(db_file: Path, sql: str):
    connection = sqlite3.connect(db_file)
    try:
        return connection.execute(sql).fetchall()
    finally:
        connection.close()


def _columns(db_file: Path, table: str) -> set[str]:
    return {row[1] for row in _rows(db_file, f"PRAGMA table_info({table})")}


def test_upgrade_adds_the_objects_and_invents_no_provenance(at_0019):
    result = _alembic(at_0019, "upgrade", "0020_evidence_traceability")
    assert result.returncode == 0, result.stderr

    assert _rows(at_0019, "SELECT version_num FROM alembic_version") == [("0020_evidence_traceability",)]
    tables = {row[0] for row in _rows(at_0019, "SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"intake_snapshots", "evidence_acknowledgements"} <= tables
    for table, columns in NEW_COLUMNS.items():
        assert columns <= _columns(at_0019, table)
    # Existing rows are unchanged and nothing is backfilled.
    assert _rows(at_0019, "SELECT countries, confirmed, field_provenance, extraction_method FROM assessment_intelligence") == [
        ('["Germany"]', 1, None, None)
    ]
    assert _rows(at_0019, "SELECT rationale, rule_triggers FROM risk_factors") == [("legacy", None)]
    assert _rows(at_0019, "SELECT outdated_at_attach, outdated_acknowledgement_reason FROM source_evidence_links") == [(0, None)]
    assert _rows(at_0019, "SELECT COUNT(*) FROM intake_snapshots") == [(0,)]


def test_upgrade_is_idempotent_and_downgrade_round_trips_without_history(at_0019):
    assert _alembic(at_0019, "upgrade", "0020_evidence_traceability").returncode == 0
    assert _alembic(at_0019, "stamp", "0019_retention_lifecycle").returncode == 0
    rerun = _alembic(at_0019, "upgrade", "0020_evidence_traceability")
    assert rerun.returncode == 0, rerun.stderr

    down = _alembic(at_0019, "downgrade", "0019_retention_lifecycle")
    assert down.returncode == 0, down.stderr
    tables = {row[0] for row in _rows(at_0019, "SELECT name FROM sqlite_master WHERE type='table'")}
    assert "intake_snapshots" not in tables and "evidence_acknowledgements" not in tables
    for table, columns in NEW_COLUMNS.items():
        assert not columns & _columns(at_0019, table)
    assert _rows(at_0019, "SELECT COUNT(*) FROM risk_factors") == [(1,)]
    assert _rows(at_0019, "SELECT COUNT(*) FROM source_evidence_links") == [(1,)]
    assert _alembic(at_0019, "upgrade", "0020_evidence_traceability").returncode == 0


@pytest.mark.parametrize(
    "seed, message",
    [
        (
            "INSERT INTO intake_snapshots (assessment_id, record_type, version, trigger, snapshot, changes, was_validated, "
            "changed_by, created_at) VALUES (7, 'ASSESSMENT_REQUEST', 1, 'CREATED', '{}', '[]', 0, 'Owner', '2026-10-02')",
            "intake snapshot(s)",
        ),
        (
            "INSERT INTO evidence_acknowledgements (assessment_id, document_id, document_version, condition, decision, "
            "reason, acknowledged_by, created_at) VALUES (7, 1, 1, 'EXPIRED', 'USE_AS_EVIDENCE', 'reason here', 'Owner', '2026-10-02')",
            "evidence acknowledgement(s)",
        ),
        ("UPDATE assessment_intelligence SET field_provenance = '{}' WHERE id = 1", "field provenance"),
        ("UPDATE risk_factors SET rule_triggers = '[]' WHERE id = 1", "Stage 4 rule triggers"),
        ("UPDATE source_evidence_links SET outdated_acknowledgement_reason = 'r' WHERE id = 1", "outdated-source"),
    ],
)
def test_downgrade_refuses_to_discard_traceability_history(at_0019, seed, message):
    assert _alembic(at_0019, "upgrade", "0020_evidence_traceability").returncode == 0
    connection = sqlite3.connect(at_0019)
    connection.execute(seed)
    connection.commit()
    connection.close()
    result = _alembic(at_0019, "downgrade", "0019_retention_lifecycle")
    assert result.returncode != 0 and message in result.stderr
    assert _rows(at_0019, "SELECT version_num FROM alembic_version") == [("0020_evidence_traceability",)]

"""
Migration 0017 (governance records) against a database that really is at
0016: committee_votes rebuilt in its pre-0017 shape (one vote per seat,
unique constraint) holding existing votes, and the overrides /
calculation tables without the new columns.

Runs alembic in a subprocess, because app.database binds its engine to
DATABASE_URL at import and this test must not touch the suite's database.
"""

import os
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[2]

PRE_0017_VOTES = """
CREATE TABLE committee_votes (
    id INTEGER NOT NULL PRIMARY KEY,
    assessment_id INTEGER NOT NULL REFERENCES assessments (id),
    member_id INTEGER NOT NULL REFERENCES users (id),
    member_name VARCHAR(255),
    delegate_id INTEGER REFERENCES users (id),
    delegate_name VARCHAR(255),
    delegation_id INTEGER REFERENCES approval_delegations (id),
    vote VARCHAR(20) NOT NULL,
    comment TEXT,
    voted_at DATETIME NOT NULL,
    CONSTRAINT uq_committee_vote_member UNIQUE (assessment_id, member_id)
)
"""


def _alembic(db_file: Path, *args: str) -> subprocess.CompletedProcess:
    env = {**os.environ, "DATABASE_URL": f"sqlite:///{db_file.as_posix()}"}
    return subprocess.run(
        [sys.executable, "-m", "alembic", *args],
        cwd=BACKEND,
        env=env,
        capture_output=True,
        text=True,
        timeout=300,
    )


def _legacy_database(tmp_path: Path) -> Path:
    db_file = tmp_path / "legacy.db"
    result = _alembic(db_file, "upgrade", "0016_approved_sources")
    assert result.returncode == 0, result.stderr

    # create_all in the baseline used today's models; put the tables back
    # the way a real 0016 database has them.
    connection = sqlite3.connect(db_file)
    connection.executescript(
        "DROP TABLE committee_votes;"
        + PRE_0017_VOTES
        + "; DROP TABLE challenge_review_signoffs; DROP TABLE control_revisions;"
    )
    _without_columns(
        connection,
        "assessment_overrides",
        {"ai_value_source", "review_status", "reviewed_by", "reviewed_by_id", "reviewed_at", "review_note"},
    )
    _without_columns(connection, "inherent_risk_calculations", {"override_by_id"})
    connection.executescript(
        """
        -- Foreign keys are not enforced here (SQLite default), so the
        -- votes need no seeded users or assessment.
        INSERT INTO committee_votes (id, assessment_id, member_id, member_name, delegate_id, vote, comment, voted_at)
            VALUES (1, 501, 901, 'Member One', NULL, 'APPROVE', 'ok', '2026-09-02 10:00:00'),
                   (2, 501, 902, 'Member Two', 903, 'DISSENT', 'no', '2026-09-02 11:00:00');
        INSERT INTO assessment_overrides (id, assessment_id, section, field_name, ai_value, human_value, reason, created_at)
            VALUES (1, 501, 'FACTOR_RATING', 'likelihood x impact', '3 x 3', '4 x 4', 'legacy', '2026-09-02');
        """
    )
    connection.commit()
    connection.close()
    return db_file


def _without_columns(connection, table: str, dropped: set[str]) -> None:
    # SQLite can't DROP a column that carries a foreign key; rebuild the
    # table from the remaining columns instead.
    # legacy_alter_table: the rename must not rewrite other tables' foreign
    # keys to point at the temporary "<table>_pre" (dropped below), or later
    # migrations that rebuild those tables fail on a reference no real
    # database has.
    keep = [row[1] for row in connection.execute(f"PRAGMA table_info({table})") if row[1] not in dropped]
    columns = ", ".join(keep)
    connection.executescript(
        "PRAGMA legacy_alter_table = ON;"
        f"ALTER TABLE {table} RENAME TO {table}_pre;"
        f"CREATE TABLE {table} AS SELECT {columns} FROM {table}_pre WHERE 0;"
        f"DROP TABLE {table}_pre;"
    )


@pytest.fixture
def legacy_db(tmp_path):
    return _legacy_database(tmp_path)


def test_existing_votes_become_version_one_and_current(legacy_db):
    result = _alembic(legacy_db, "upgrade", "head")
    assert result.returncode == 0, result.stderr

    connection = sqlite3.connect(legacy_db)
    rows = connection.execute(
        "SELECT id, member_id, vote, comment, voted_at, version, is_current, cast_by_id, superseded_at "
        "FROM committee_votes ORDER BY id"
    ).fetchall()
    assert rows == [
        (1, 901, "APPROVE", "ok", "2026-09-02 10:00:00", 1, 1, 901, None),
        (2, 902, "DISSENT", "no", "2026-09-02 11:00:00", 1, 1, 903, None),
    ]

    # Legacy overrides keep their data and are not relabelled.
    assert connection.execute(
        "SELECT ai_value, human_value, reason, ai_value_source, review_status FROM assessment_overrides"
    ).fetchall() == [("3 x 3", "4 x 4", "legacy", None, None)]

    tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"challenge_review_signoffs", "control_revisions"} <= tables

    # The new constraints: a second version for a seat is allowed, a
    # second *current* vote for a seat is not.
    connection.execute(
        "UPDATE committee_votes SET is_current = 0 WHERE id = 1"
    )
    connection.execute(
        "INSERT INTO committee_votes (assessment_id, member_id, vote, voted_at, version, is_current) "
        "VALUES (501, 901, 'DISSENT', '2026-09-03', 2, 1)"
    )
    with pytest.raises(sqlite3.IntegrityError):
        connection.execute(
            "INSERT INTO committee_votes (assessment_id, member_id, vote, voted_at, version, is_current) "
            "VALUES (501, 901, 'ABSTAIN', '2026-09-03', 3, 1)"
        )
    with pytest.raises(sqlite3.IntegrityError):
        connection.execute(
            "INSERT INTO committee_votes (assessment_id, member_id, vote, voted_at, version, is_current) "
            "VALUES (501, 902, 'ABSTAIN', '2026-09-03', 1, 0)"
        )
    connection.close()


def test_upgrade_is_safe_to_rerun_and_downgrade_round_trips(legacy_db):
    assert _alembic(legacy_db, "upgrade", "head").returncode == 0

    # Re-running 0017 over an already-upgraded schema is a no-op.
    assert _alembic(legacy_db, "downgrade", "0016_approved_sources").returncode == 0
    connection = sqlite3.connect(legacy_db)
    assert connection.execute("SELECT COUNT(*) FROM committee_votes").fetchone()[0] == 2
    constraints = connection.execute("SELECT sql FROM sqlite_master WHERE name='committee_votes'").fetchone()[0]
    assert "uq_committee_vote_member" in constraints and "version" not in constraints
    connection.close()

    assert _alembic(legacy_db, "upgrade", "head").returncode == 0
    assert _alembic(legacy_db, "stamp", "0016_approved_sources").returncode == 0
    rerun = _alembic(legacy_db, "upgrade", "head")
    assert rerun.returncode == 0, rerun.stderr


def test_downgrade_refuses_to_discard_vote_history(legacy_db):
    assert _alembic(legacy_db, "upgrade", "head").returncode == 0
    connection = sqlite3.connect(legacy_db)
    connection.execute("UPDATE committee_votes SET is_current = 0 WHERE id = 1")
    connection.execute(
        "INSERT INTO committee_votes (assessment_id, member_id, vote, voted_at, version, is_current) "
        "VALUES (501, 901, 'DISSENT', '2026-09-03', 2, 1)"
    )
    connection.commit()
    connection.close()

    result = _alembic(legacy_db, "downgrade", "0016_approved_sources")
    assert result.returncode != 0 and "vote history" in result.stderr

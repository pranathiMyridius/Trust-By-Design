"""
scripts/validate_staging_migration.py: refuses anything that could be the
primary database, and -- rehearsed on a local copy at 0016 -- migrates,
verifies integrity, starts the app and round-trips the rollback.
"""

import os
import subprocess
import sys
from pathlib import Path

from tests.unit.test_migration_0017 import _legacy_database

BACKEND = Path(__file__).resolve().parents[2]
SCRIPT = BACKEND / "scripts" / "validate_staging_migration.py"


def _validate(*args: str, **env) -> subprocess.CompletedProcess:
    full_env = {k: v for k, v in os.environ.items() if k not in {"STAGING_DATABASE_URL", "PROTECTED_DATABASE_HOSTS"}}
    full_env.update(env)
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args], cwd=BACKEND, env=full_env, capture_output=True, text=True, timeout=900
    )


def test_refuses_without_a_staging_url(monkeypatch, tmp_path):
    # backend/.env may define STAGING_DATABASE_URL in a real checkout; point
    # the script at an empty environment instead by hiding the key.
    result = _validate("--status-only", STAGING_DATABASE_URL="")
    assert result.returncode == 2 and "STAGING_DATABASE_URL is not set" in result.stderr


def test_refuses_the_primary_host_and_protected_hosts():
    primary = "postgresql://u:secret@primary.example.invalid:5432/db"
    same_host = _validate("--status-only", STAGING_DATABASE_URL=primary, DATABASE_URL=primary)
    assert same_host.returncode == 2 and "same host as the primary" in same_host.stderr

    protected = _validate(
        "--status-only",
        STAGING_DATABASE_URL="postgresql://u:secret@guarded.example.invalid/db",
        DATABASE_URL="postgresql://u:secret@other.example.invalid/db",
        PROTECTED_DATABASE_HOSTS="guarded.example.invalid",
    )
    assert protected.returncode == 2 and "PROTECTED_DATABASE_HOSTS" in protected.stderr
    for result in (same_host, protected):
        assert "secret" not in result.stderr + result.stdout


def test_sqlite_only_as_an_explicit_local_rehearsal(tmp_path):
    result = _validate("--status-only", STAGING_DATABASE_URL=f"sqlite:///{(tmp_path / 'x.db').as_posix()}")
    assert result.returncode == 2 and "--local-rehearsal" in result.stderr


def test_local_rehearsal_migrates_verifies_and_rolls_back(tmp_path):
    legacy = _legacy_database(tmp_path)  # at 0016 with two existing votes
    result = _validate(
        "--rollback-check",
        "--local-rehearsal",
        STAGING_DATABASE_URL=f"sqlite:///{legacy.as_posix()}",
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "revision before: 0016_approved_sources" in result.stdout
    assert "[FAIL]" not in result.stdout
    for check in (
        "database at code head",
        "no rows lost or added in governed tables",
        "committee vote values unchanged",
        "existing votes are version 1 and current",
        "application starts against the migrated schema",
        "rollback (downgrade -1) succeeds",
        "vote values unchanged after round trip",
    ):
        assert f"[PASS] {check}" in result.stdout, check


def test_wrong_revision_stops_and_rehearsals_never_write_staging_evidence(tmp_path):
    legacy = _legacy_database(tmp_path)  # at 0016
    evidence = BACKEND / "eval_results" / "staging_migration_report.json"
    before = evidence.read_bytes() if evidence.exists() else None

    stopped = _validate(
        "--rollback-check", "--local-rehearsal", "--expect-revision", "0018_sod_governance",
        STAGING_DATABASE_URL=f"sqlite:///{legacy.as_posix()}",
    )
    assert stopped.returncode == 2 and "STOP" in stopped.stdout and "Nothing was changed" in stopped.stdout
    import sqlite3

    connection = sqlite3.connect(legacy)
    assert connection.execute("SELECT version_num FROM alembic_version").fetchone()[0] == "0016_approved_sources"
    connection.close()

    rehearsal = _validate("--local-rehearsal", STAGING_DATABASE_URL=f"sqlite:///{legacy.as_posix()}")
    assert rehearsal.returncode == 0, rehearsal.stdout + rehearsal.stderr
    assert "staging_migration_rehearsal_report.json" in rehearsal.stdout
    # The staging evidence file is untouched by any rehearsal.
    assert (evidence.read_bytes() if evidence.exists() else None) == before


def test_rehearsal_from_0019_checks_0020_and_0021_and_rolls_back_to_the_start(tmp_path):
    # P8: the staging plan is 0019 -> head; the rollback leg must undo every
    # new migration, not only the last one.
    import sqlite3

    from tests.unit.test_migration_0020 import _at_0019
    from tests.unit.test_migration_0021 import SEED

    db_file = _at_0019(tmp_path)
    connection = sqlite3.connect(db_file)
    connection.executescript(SEED)  # an approved reassessment and one in progress
    connection.commit()
    connection.close()

    url = f"sqlite:///{db_file.as_posix()}"
    assert _validate("--local-rehearsal", "--rollback-to", "0019_retention_lifecycle", STAGING_DATABASE_URL=url).returncode == 2
    result = _validate(
        "--local-rehearsal", "--expect-revision", "0019_retention_lifecycle",
        "--rollback-check", "--rollback-to", "0019_retention_lifecycle",
        STAGING_DATABASE_URL=url,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "[FAIL]" not in result.stdout
    for check in (
        "no assessment's pipeline or lifecycle status changed",
        "0020 traceability tables present",
        "0020 columns present",
        "0020: nothing backfilled",
        "0021 columns present",
        "0021: every reassessment flag is backed by its parent/child rows",
        "rollback (downgrade 0019_retention_lifecycle) succeeds",
        "rolled back to the requested revision",
        "statuses unchanged after round trip",
        "0021: reassessment flags derived again after round trip",
    ):
        assert f"[PASS] {check}" in result.stdout, check
    # The backfill really ran: one parent superseded, one under reassessment.
    assert '"SUPERSEDED": 1' in result.stdout and '"UNDER_REASSESSMENT": 1' in result.stdout


def test_report_name_is_separate_and_cannot_target_staging_evidence(tmp_path):
    legacy = _legacy_database(tmp_path)
    url = f"sqlite:///{legacy.as_posix()}"
    refused = _validate("--local-rehearsal", "--report-name", "staging_migration_report.json", STAGING_DATABASE_URL=url)
    assert refused.returncode == 2 and "--report-name" in refused.stderr
    named = _validate("--local-rehearsal", "--status-only", "--report-name", "x/../y.json", STAGING_DATABASE_URL=url)
    assert named.returncode == 2
    ok = _validate("--local-rehearsal", "--report-name", "test_named_rehearsal.json", STAGING_DATABASE_URL=url)
    out = BACKEND / "eval_results" / "test_named_rehearsal.json"
    try:
        assert ok.returncode == 0 and out.exists(), ok.stdout + ok.stderr
    finally:
        out.unlink(missing_ok=True)

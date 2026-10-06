"""
Migration 0027 (Source Library) on a throwaway SQLite database that starts
at the previous revision with existing approved_sources rows: the new
tables are created, every existing source is carried over without being
modified, help articles are left out, and the downgrade refuses to destroy
governance history.

Runs in a subprocess so it never touches the test session's database.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import textwrap
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[2]

SCRIPT = textwrap.dedent(
    """
    import json, os, sqlite3, sys
    from alembic import command
    from app.migrations import _config

    path = sys.argv[1]
    cfg = _config()
    command.upgrade(cfg, "0026_ai_challenge")

    con = sqlite3.connect(path)
    cols = "title, source_type, issuer, version, effective_date, review_date, reference, content, status, approved_by, approved_at, created_by, created_at, updated_at"
    rows = [
        ("Group KYC Policy", "INTERNAL_POLICY", "Group Compliance", "4.2", "2026-01-01", "2027-01-01", "POL-KYC-004", "Customers are identified.\\n\\nBeneficial owners are verified.", "APPROVED", "Pat Approver", "2026-02-01 10:00:00.000000", "Sam Maker", "2026-01-15 09:00:00.000000", "2026-02-01 10:00:00.000000"),
        ("FATF Recommendations", "REGULATORY_GUIDANCE", "FATF", "2025", None, None, "https://www.fatf-gafi.org/x", "Countries should apply a risk-based approach.", "DRAFT", None, None, "Sam Maker", "2026-03-01 09:00:00.000000", "2026-03-01 09:00:00.000000"),
        ("Old Procedure", "PROCEDURE", None, "1", None, None, None, "Old steps.", "RETIRED", "Pat Approver", "2025-05-01 10:00:00.000000", "Sam Maker", "2025-04-01 09:00:00.000000", "2025-06-01 09:00:00.000000"),
        ("How to reset your password", "USER_GUIDE", None, "1", None, None, None, "Click forgot password.", "APPROVED", "Pat Approver", "2026-02-01 10:00:00.000000", "Sam Maker", "2026-01-15 09:00:00.000000", "2026-02-01 10:00:00.000000"),
    ]
    for row in rows:
        con.execute(f"INSERT INTO approved_sources ({cols}) VALUES ({','.join('?' * 14)})", row)
    con.commit()
    before = con.execute("SELECT id, title, status, content FROM approved_sources ORDER BY id").fetchall()
    con.close()

    command.upgrade(cfg, "head")

    con = sqlite3.connect(path)
    out = {}
    out["before"] = before
    out["after"] = con.execute("SELECT id, title, status, content FROM approved_sources ORDER BY id").fetchall()
    out["tables"] = sorted(r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'source_%'"))
    out["records"] = con.execute(
        "SELECT r.source_code, r.title, r.authority, r.category, r.status, r.source_url, v.version_label, v.status, v.legacy_source_id, v.chunk_count, "
        "length(v.extracted_text) > 0, v.decided_by FROM source_records r JOIN source_versions v ON v.record_id = r.id ORDER BY v.legacy_source_id"
    ).fetchall()
    out["approvals"] = con.execute("SELECT action, to_status, actor FROM source_approvals").fetchall()
    out["audits"] = con.execute("SELECT event_type FROM source_audit_logs").fetchall()
    out["citations_column"] = any(r[1] == "source_citations" for r in con.execute("PRAGMA table_info(risk_factors)"))
    out["indexes"] = sorted(r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='index' AND tbl_name='source_versions'"))
    con.close()

    # Re-running is harmless, and the downgrade guard holds history.
    command.upgrade(cfg, "head")
    con = sqlite3.connect(path)
    out["records_after_second_upgrade"] = con.execute("SELECT COUNT(*) FROM source_records").fetchone()[0]
    con.execute("INSERT INTO source_approvals (id, record_id, version_id, action, to_status, actor, comment, created_at) "
                "SELECT lower(hex(randomblob(16))), record_id, id, 'SUBMITTED', 'IN_REVIEW', 'Someone', 'real history', '2026-10-01 00:00:00' FROM source_versions LIMIT 1")
    con.commit()
    con.close()
    try:
        command.downgrade(cfg, "0026_ai_challenge")
        out["downgrade"] = "allowed"
    except RuntimeError as exc:
        out["downgrade"] = str(exc)
    print("RESULT" + json.dumps(out))
    """
)


def test_migration_backfills_legacy_sources_and_protects_history(tmp_path):
    db_file = tmp_path / "migrate.db"
    env = {**os.environ, "DATABASE_URL": f"sqlite:///{db_file}"}
    completed = subprocess.run(
        [sys.executable, "-c", SCRIPT, str(db_file)], cwd=BACKEND, env=env, capture_output=True, text=True, timeout=600
    )
    assert completed.returncode == 0, completed.stderr[-3000:]
    result = json.loads(completed.stdout.split("RESULT", 1)[1])

    assert {"source_records", "source_versions", "source_approvals", "source_audit_logs", "source_chunks", "source_embeddings"} <= set(result["tables"])
    assert result["citations_column"] is True
    assert {"uq_source_versions_one_approved", "ix_source_versions_file_sha256"} <= set(result["indexes"])

    # The existing rows are untouched.
    assert result["before"] == result["after"]

    # Three sources carried over; the help article is not a regulatory source.
    records = result["records"]
    assert [r[1] for r in records] == ["Group KYC Policy", "FATF Recommendations", "Old Procedure"]
    approved, draft, retired = records
    assert approved[0].startswith("SRC-LEG-") and approved[3] == "INTERNAL_POLICY" and approved[2] == "Group Compliance"
    assert (approved[4], approved[7], approved[6]) == ("ACTIVE", "APPROVED", "4.2") and approved[10] == 1 and approved[11] == "Pat Approver"
    assert draft[3] == "REGULATORY_GUIDANCE" and draft[5] == "https://www.fatf-gafi.org/x" and draft[7] == "DRAFT" and draft[11] is None
    assert (retired[4], retired[7], retired[2]) == ("RETIRED", "RETIRED", "Not recorded")
    assert all(r[9] == 0 for r in records)  # chunks are made lazily on first use

    # History: the approved and retired sources keep their approval; every row is audited as migrated.
    assert sorted(a[0] for a in result["approvals"]) == ["APPROVED", "APPROVED"]
    assert all("Pat Approver" == a[2] for a in result["approvals"])
    assert [a[0] for a in result["audits"]] == ["MIGRATED"] * 3

    assert result["records_after_second_upgrade"] == 3  # idempotent
    assert "approval/submission record(s) would be lost" in result["downgrade"]

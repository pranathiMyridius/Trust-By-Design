"""
READ-ONLY checks before and after an approved production migration.

This script never migrates, never writes and never prints the connection
string. Every query runs in a READ ONLY transaction. The migration itself is
a separate, approved step:

    python -m app.migrations upgrade --confirm-host <host> --allow-protected-host

Usage (from backend/, with the production database in DATABASE_URL, i.e.
backend/.env, and nothing overriding it in the shell):

    python scripts/verify_production_migration.py pre  --confirm-host <host> --expect-revision 0017_governance_records
    python scripts/verify_production_migration.py post --confirm-host <host>

`pre` records the revision and the row count of every table in
eval_results/production_migration_pre.json and stops (exit 2) if the
revision isn't the expected one. `post` requires the code head, compares
every pre-existing table's row count with `pre`, and runs the read-only
0018 / 0019 checks shared with validate_staging_migration.py (backfill,
partial unique indexes, foreign keys, orphans, eligibility probe). Report:
eval_results/production_migration_post.json.

--local-rehearsal accepts a local SQLite DATABASE_URL so the procedure can
be rehearsed; its reports get a "rehearsal_" prefix and are never evidence.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND / "scripts"))
sys.path.insert(0, str(BACKEND))

from dotenv import dotenv_values  # noqa: E402
from sqlalchemy import create_engine, inspect, text  # noqa: E402

import validate_staging_migration as shared  # noqa: E402


def _database_url() -> str:
    url = os.environ.get("DATABASE_URL") or dotenv_values(BACKEND / ".env").get("DATABASE_URL")
    if not url:
        raise SystemExit("DATABASE_URL is not set.")
    return url


def _counts(url: str) -> dict:
    engine = create_engine(url)
    try:
        with engine.connect() as connection:
            if shared._host(url):
                connection.execute(text("SET TRANSACTION READ ONLY"))
            tables = sorted(inspect(connection).get_table_names())
            revision = (
                connection.execute(text("SELECT version_num FROM alembic_version")).scalar()
                if "alembic_version" in tables
                else None
            )
            return {
                "revision": revision,
                "counts": {t: connection.execute(text(f'SELECT COUNT(*) FROM "{t}"')).scalar() for t in tables},
            }
    finally:
        engine.dispose()


def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("phase", choices=["pre", "post"])
    parser.add_argument("--confirm-host", default=None)
    parser.add_argument("--expect-revision", default=None)
    parser.add_argument("--local-rehearsal", action="store_true")
    args = parser.parse_args(argv)

    url = _database_url()
    host = shared._host(url)
    if host is None and not args.local_rehearsal:
        raise SystemExit("DATABASE_URL is SQLite; that is only allowed with --local-rehearsal.")
    if host is not None:
        if not args.confirm_host or args.confirm_host.lower() != host:
            raise SystemExit("Refused: --confirm-host must name the database host in DATABASE_URL exactly.")
    prefix = "rehearsal_" if host is None else ""
    out_dir = BACKEND / "eval_results"
    out_dir.mkdir(exist_ok=True)
    head = shared.head_revision()
    state = _counts(url)
    report = {
        "phase": args.phase,
        "target": host or "local SQLite rehearsal",
        "run_at": datetime.now(timezone.utc).isoformat(),
        "code_head": head,
        "revision": state["revision"],
        "counts": state["counts"],
        "checks": [],
    }

    def check(name, ok, detail=""):
        report["checks"].append({"check": name, "ok": bool(ok), "detail": detail})
        print(f"[{'PASS' if ok else 'FAIL'}] {name}" + (f" -- {detail}" if detail else ""))

    print(f"Target: {report['target']}; revision: {state['revision']}; code head: {head}; tables: {len(state['counts'])}")
    if args.phase == "pre":
        if args.expect_revision and state["revision"] != args.expect_revision:
            print(f"STOP: the database is at {state['revision']}, not {args.expect_revision}. Do not migrate.")
            code = 2
        else:
            check("revision as expected", True, state["revision"])
            code = 0
        (out_dir / f"{prefix}production_migration_pre.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
        return code

    pre_file = out_dir / f"{prefix}production_migration_pre.json"
    if not pre_file.exists():
        raise SystemExit(f"{pre_file.name} not found: run the 'pre' phase before migrating.")
    pre = json.loads(pre_file.read_text(encoding="utf-8"))
    check("database at code head", state["revision"] == head, str(state["revision"]))
    changed = {t: (n, state["counts"].get(t)) for t, n in pre["counts"].items() if t != "alembic_version" and state["counts"].get(t) != n}
    check("every pre-existing table has the same row count", not changed, json.dumps(changed))
    added = sorted(set(state["counts"]) - set(pre["counts"]))
    check("only the expected tables were added",
          set(added) <= {"sod_exceptions", "sod_exception_events", "retention_policy_versions", "legal_hold_events"}, json.dumps(added))
    if head >= "0018":
        check("0018: existing overrides and sign-offs left unlabelled", shared._legacy_unlabelled(url))
    if head >= "0019":
        check("0019: v1 backfilled, holds have history, nothing marked approved", shared._retention_backfilled(url))
        schema = shared._retention_schema(url)
        check("0019 partial unique indexes and foreign keys present", schema["partial_unique_indexes"] and schema["foreign_keys"])
        check("0019: no orphaned hold events, no duplicate open versions",
              schema["orphan_hold_events"] == 0 and schema["duplicate_open_versions"] == 0, json.dumps(schema))
        ok, detail = shared._eligibility_probe(url)
        check("0019 eligibility probe (read-only)", ok, detail)
    failed = [c for c in report["checks"] if not c["ok"]]
    print(f"\n{len(report['checks']) - len(failed)}/{len(report['checks'])} post-migration checks passed.")
    (out_dir / f"{prefix}production_migration_post.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())

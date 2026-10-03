"""
Validate pending migrations on an ISOLATED STAGING database (e.g. a Neon
branch of the primary) before any production change is proposed.

    python scripts/validate_staging_migration.py              # check + migrate + verify
    python scripts/validate_staging_migration.py --rollback-check
    python scripts/validate_staging_migration.py --status-only
    python scripts/validate_staging_migration.py --expect-revision 0018_sod_governance --rollback-check

The staging connection string comes from STAGING_DATABASE_URL (environment
or backend/.env) -- never from DATABASE_URL, and it is never printed. The
script refuses to run when the staging host is the primary's host
(backend/.env DATABASE_URL) or is listed in PROTECTED_DATABASE_HOSTS.

Steps, each reported:
  1. pre-flight: revision, row counts of governed tables, a fingerprint of
     every committee vote (read-only)
  2. upgrade head, authorized for the staging host only
  3. post-checks: at head; no rows lost; existing votes are version 1 and
     current with their values unchanged; 0017 constraints and tables
     present
  4. application start-up against staging: prepare_database() must be a
     no-op (schema at head) and /health must answer
  5. (--rollback-check) downgrade one step, verify data, upgrade again --
     proving the rollback path on staging, never on production

--expect-revision stops before anything is changed when the database is
not at the revision the validation plan expects.

--report-name <file>.json writes the report under that name instead (for a
rehearsal on another remote branch, so staging evidence is never touched).

--local-rehearsal lets the script run against a local SQLite file so the
procedure itself can be rehearsed; it is never a substitute for staging.
Its report goes to a separate file, so it never overwrites staging evidence.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

from dotenv import dotenv_values  # noqa: E402
from sqlalchemy import create_engine, inspect, text  # noqa: E402
from sqlalchemy.engine import make_url  # noqa: E402

GOVERNED_TABLES = [
    "assessments",
    "audit_events",
    "committee_votes",
    "assessment_overrides",
    "inherent_risk_calculations",
    "residual_risk_calculations",
    "controls",
    "challenge_findings",
    "decision_records",
    "users",
    # P5 (0019): retention state and the legacy policy row are kept as-is.
    "assessment_retention",
    "retention_policies",
    # P4 (0020) / P6 (0021): tables that gain columns; no rows change.
    "assessment_intelligence",
    "risk_factors",
    "source_evidence_links",
    "reassessment_triggers",
]

# 0020 / 0021 additions (P8 validation).
ADDED_0020 = {
    "assessment_intelligence": {"field_provenance", "extraction_method", "extracted_at", "source_document_ids"},
    "risk_factors": {"rule_triggers"},
    "source_evidence_links": {"outdated_at_attach", "outdated_acknowledgement_reason", "outdated_acknowledged_by_id"},
}
ADDED_0023 = {
    "approved_sources": {"original_filename", "file_path", "file_content_type", "file_size", "file_sha256"},
}
ADDED_0021 = {
    "assessments": {"reassessment_state", "superseded_by_id", "superseded_at"},
    "reassessment_triggers": {"detected_by_id", "resolved_by_id", "resolution_note"},
}


class Refused(SystemExit):
    pass


def _host(url: str) -> str | None:
    parsed = make_url(url)
    return None if parsed.get_backend_name() == "sqlite" else (parsed.host or "localhost").lower()


def staging_url(local_rehearsal: bool) -> str:
    env_file = dotenv_values(BACKEND / ".env")
    # An explicitly set variable (even an empty one) wins over backend/.env,
    # so callers -- and the tests -- can always rule the .env value out.
    if "STAGING_DATABASE_URL" in os.environ:
        url = os.environ["STAGING_DATABASE_URL"]
    else:
        url = env_file.get("STAGING_DATABASE_URL")
    if not url:
        raise Refused(
            "STAGING_DATABASE_URL is not set. Create an isolated staging branch and provide its "
            "connection string through secure configuration (backend/.env or the environment)."
        )

    host = _host(url)
    if host is None:
        if not local_rehearsal:
            raise Refused("STAGING_DATABASE_URL is SQLite; that is only allowed with --local-rehearsal.")
        return url

    primary = os.getenv("DATABASE_URL") or env_file.get("DATABASE_URL")
    protected = {
        h.strip().lower()
        for h in (os.getenv("PROTECTED_DATABASE_HOSTS") or env_file.get("PROTECTED_DATABASE_HOSTS") or "").split(",")
        if h.strip()
    }
    if primary and _host(primary) == host:
        raise Refused("STAGING_DATABASE_URL points at the same host as the primary DATABASE_URL. Refusing.")
    if host in protected:
        raise Refused(f"{host} is listed in PROTECTED_DATABASE_HOSTS. Refusing.")
    if url == primary:
        raise Refused("STAGING_DATABASE_URL equals DATABASE_URL. Refusing.")
    return url


def _env_for(url: str) -> dict:
    env = {key: value for key, value in os.environ.items() if not key.startswith("MIGRATION_")}
    env["DATABASE_URL"] = url
    host = _host(url)
    if host:
        # Authorized for the staging host only; never the protected flag.
        env["MIGRATION_AUTHORIZED_HOST"] = host
    env.setdefault("ADMIN_BOOTSTRAP_PASSWORD", "unused-on-an-existing-database")
    env["WORKFLOW_ESCALATION_INTERVAL_SECONDS"] = "0"
    env["BACKUP_INTERVAL_HOURS"] = "0"
    return env


def _run(url: str, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, *args], cwd=BACKEND, env=_env_for(url), capture_output=True, text=True, timeout=900
    )


def snapshot(url: str) -> dict:
    """Read-only state of the database."""

    engine = create_engine(url)
    try:
        with engine.connect() as connection:
            if _host(url):
                connection.execute(text("SET TRANSACTION READ ONLY"))
            inspector = inspect(connection)
            tables = set(inspector.get_table_names())
            revision = (
                connection.execute(text("SELECT version_num FROM alembic_version")).scalar()
                if "alembic_version" in tables
                else None
            )
            counts = {
                table: connection.execute(text(f"SELECT COUNT(*) FROM {table}")).scalar()
                for table in GOVERNED_TABLES
                if table in tables
            }
            votes = []
            if "committee_votes" in tables:
                columns = {c["name"] for c in inspector.get_columns("committee_votes")}
                extra = ", version, is_current" if "version" in columns else ""
                for row in connection.execute(
                    text(f"SELECT id, assessment_id, member_id, vote, comment, voted_at{extra} FROM committee_votes ORDER BY id")
                ):
                    votes.append(list(row))
            vote_fingerprint = hashlib.sha256(
                json.dumps([v[:6] for v in votes], default=str).encode()
            ).hexdigest()
            # Pipeline and lifecycle status of every assessment: no
            # migration may change them (0021 adds a flag, never a status).
            statuses = []
            if "assessments" in tables:
                statuses = [
                    list(row)
                    for row in connection.execute(
                        text("SELECT id, status, workflow_status, is_draft FROM assessments ORDER BY id")
                    )
                ]
            status_fingerprint = hashlib.sha256(json.dumps(statuses, default=str).encode()).hexdigest()
            return {
                "revision": revision,
                "counts": counts,
                "status_fingerprint": status_fingerprint,
                "vote_fingerprint": vote_fingerprint,
                "votes": votes,
                "tables": tables,
                "vote_constraints": {c["name"] for c in inspector.get_unique_constraints("committee_votes")}
                if "committee_votes" in tables
                else set(),
                "vote_indexes": {i["name"] for i in inspector.get_indexes("committee_votes")}
                if "committee_votes" in tables
                else set(),
            }
    finally:
        engine.dispose()


def _legacy_unlabelled(url: str) -> bool:
    """Rows from before 0018 are not classified or relabelled: overrides keep
    NULL materiality; earlier sign-offs are SIGNOFF, not REVIEW."""

    engine = create_engine(url)
    try:
        with engine.connect() as connection:
            if _host(url):
                connection.execute(text("SET TRANSACTION READ ONLY"))
            labelled = connection.execute(
                text("SELECT COUNT(*) FROM assessment_overrides WHERE origin IS NULL AND materiality IS NOT NULL")
            ).scalar()
            reviews = connection.execute(
                text("SELECT COUNT(*) FROM challenge_review_signoffs WHERE stage = 'REVIEW' AND reviewer_role IS NULL")
            ).scalar()
            return labelled == 0 and reviews == 0
    finally:
        engine.dispose()


def _retention_backfilled(url: str) -> bool:
    """0019: exactly one ACTIVE ASSESSMENT version, v1 marked as migrated
    (not compliance-approved) with the legacy period, never governance-
    approved; one backfilled SET event per assessment on hold."""

    engine = create_engine(url)
    try:
        with engine.connect() as connection:
            if _host(url):
                connection.execute(text("SET TRANSACTION READ ONLY"))
            active = connection.execute(
                text("SELECT COUNT(*) FROM retention_policy_versions WHERE record_type = 'ASSESSMENT' AND status = 'ACTIVE'")
            ).scalar()
            v1 = connection.execute(
                text(
                    "SELECT retention_days, change_reason, governance_approval_reference FROM retention_policy_versions "
                    "WHERE record_type = 'ASSESSMENT' AND version = 1"
                )
            ).first()
            legacy = connection.execute(
                text("SELECT default_retention_days FROM retention_policies WHERE is_active = :yes ORDER BY id LIMIT 1"),
                {"yes": True},
            ).first()
            held = connection.execute(text("SELECT COUNT(*) FROM assessment_retention WHERE legal_hold = :yes"), {"yes": True}).scalar()
            held_without_history = connection.execute(
                text(
                    "SELECT COUNT(*) FROM assessment_retention r WHERE r.legal_hold = :yes AND NOT EXISTS "
                    "(SELECT 1 FROM legal_hold_events e WHERE e.assessment_id = r.assessment_id)"
                ),
                {"yes": True},
            ).scalar()
            return (
                active == 1
                and v1 is not None
                and "not compliance-approved" in (v1[1] or "")
                and v1[2] is None
                and (legacy is None or v1[0] == legacy[0])
                and (held == 0 or held_without_history == 0)
            )
    finally:
        engine.dispose()


def _retention_schema(url: str) -> dict:
    """0019 (read-only): the partial unique indexes, foreign keys, no
    orphaned hold events, and at most one ACTIVE / PROPOSED version per
    record type."""

    engine = create_engine(url)
    try:
        with engine.connect() as connection:
            if _host(url):
                connection.execute(text("SET TRANSACTION READ ONLY"))
            inspector = inspect(connection)
            indexes = {i["name"]: i for i in inspector.get_indexes("retention_policy_versions")}
            partial = all(
                name in indexes and indexes[name].get("unique")
                for name in ("uq_retention_policy_one_active", "uq_retention_policy_one_proposed")
            )

            def targets(table):
                return {fk["referred_table"] for fk in inspector.get_foreign_keys(table)}

            fks = {"users", "retention_policy_versions"} <= targets("retention_policy_versions") and {
                "assessments", "users"
            } <= targets("legal_hold_events")
            orphans = connection.execute(
                text(
                    "SELECT COUNT(*) FROM legal_hold_events e WHERE NOT EXISTS "
                    "(SELECT 1 FROM assessments a WHERE a.id = e.assessment_id)"
                )
            ).scalar()
            duplicates = connection.execute(
                text(
                    "SELECT COUNT(*) FROM (SELECT record_type, status FROM retention_policy_versions "
                    "WHERE status IN ('ACTIVE', 'PROPOSED') GROUP BY record_type, status HAVING COUNT(*) > 1) d"
                )
            ).scalar()
            return {"partial_unique_indexes": partial, "foreign_keys": fks, "orphan_hold_events": orphans, "duplicate_open_versions": duplicates}
    finally:
        engine.dispose()


# Run in a subprocess against the target (app.main is never imported, so
# nothing migrates), inside a READ ONLY transaction on Postgres.
_ELIGIBILITY_PROBE = """
import json
from sqlalchemy import text
from app.database import SessionLocal, engine
from app.governance import retention as r
from app.models.assessment import Assessment
from app.models.audit_trail import AssessmentRetention

db = SessionLocal()
try:
    if engine.dialect.name == "postgresql":
        db.execute(text("SET TRANSACTION READ ONLY"))
    v1 = r.active_version(db)
    held = {row.assessment_id for row in db.query(AssessmentRetention).filter(AssessmentRetention.legal_hold.is_(True))}
    results = [r.evaluate(db, a) for a in db.query(Assessment).all()]
    counts = {}
    for row in results:
        counts[row["eligibility_status"]] = counts.get(row["eligibility_status"], 0) + 1
    violations = [
        row["record_id"] for row in results
        if (row["eligible"] and (row["legal_hold"] or row["basis_date"] is None))
        or (row["record_id"] in held and row["eligibility_status"] != "LEGAL_HOLD")
        or (row["policy_version_id"] != (v1.id if v1 else None))
    ]
    print(json.dumps({"assessments": len(results), "counts": counts, "violations": violations,
                      "active_version": v1.version if v1 else None, "active_days": v1.retention_days if v1 else None}))
finally:
    db.rollback()
    db.close()
"""


def _eligibility_probe(url: str) -> tuple[bool, str]:
    result = _run(url, "-c", _ELIGIBILITY_PROBE)
    if result.returncode != 0:
        return False, (result.stderr.strip().splitlines() or ["probe failed"])[-1]
    try:
        data = json.loads(result.stdout.strip().splitlines()[-1])
    except (ValueError, IndexError):
        return False, "probe produced no result"
    ok = not data["violations"] and data["active_version"] == 1
    return ok, json.dumps({k: data[k] for k in ("assessments", "counts", "active_version", "active_days", "violations")}, sort_keys=True)


def _columns_present(url: str, added: dict[str, set[str]]) -> tuple[bool, str]:
    engine = create_engine(url)
    try:
        with engine.connect() as connection:
            if _host(url):
                connection.execute(text("SET TRANSACTION READ ONLY"))
            inspector = inspect(connection)
            missing = {
                table: sorted(columns - {c["name"] for c in inspector.get_columns(table)})
                for table, columns in added.items()
            }
            missing = {table: cols for table, cols in missing.items() if cols}
            return not missing, json.dumps(missing)
    finally:
        engine.dispose()


def _traceability_untouched(url: str) -> tuple[bool, str]:
    """0020 (read-only): the new tables exist and nothing was backfilled --
    no snapshots or acknowledgements, no provenance or rule triggers
    invented for existing rows, no existing link marked outdated."""

    engine = create_engine(url)
    try:
        with engine.connect() as connection:
            if _host(url):
                connection.execute(text("SET TRANSACTION READ ONLY"))
            counts = {
                "intake_snapshots": connection.execute(text("SELECT COUNT(*) FROM intake_snapshots")).scalar(),
                "evidence_acknowledgements": connection.execute(text("SELECT COUNT(*) FROM evidence_acknowledgements")).scalar(),
                "profiles_with_provenance": connection.execute(
                    text("SELECT COUNT(*) FROM assessment_intelligence WHERE field_provenance IS NOT NULL")
                ).scalar(),
                "factors_with_rule_triggers": connection.execute(
                    text("SELECT COUNT(*) FROM risk_factors WHERE rule_triggers IS NOT NULL")
                ).scalar(),
                "links_marked_outdated": connection.execute(
                    text("SELECT COUNT(*) FROM source_evidence_links WHERE outdated_at_attach = :yes"), {"yes": True}
                ).scalar(),
            }
            return not any(counts.values()), json.dumps(counts, sort_keys=True)
    finally:
        engine.dispose()


def _reassessment_consistent(url: str) -> tuple[bool, str]:
    """0021 (read-only): every reassessment flag is backed by real rows --
    a SUPERSEDED parent names an approved child of its own; an
    UNDER_REASSESSMENT parent has a child without a final decision; no
    assessment without children carries a flag."""

    final = "('APPROVED','APPROVED_WITH_CONDITIONS','REJECTED','MANAGER_REJECTED','CLOSED')"
    engine = create_engine(url)
    try:
        with engine.connect() as connection:
            if _host(url):
                connection.execute(text("SET TRANSACTION READ ONLY"))
            superseded_bad = connection.execute(
                text(
                    "SELECT COUNT(*) FROM assessments p WHERE p.reassessment_state = 'SUPERSEDED' AND NOT EXISTS "
                    "(SELECT 1 FROM assessments c WHERE c.id = p.superseded_by_id AND c.parent_assessment_id = p.id "
                    "AND c.status IN ('APPROVED','APPROVED_WITH_CONDITIONS'))"
                )
            ).scalar()
            under_bad = connection.execute(
                text(
                    "SELECT COUNT(*) FROM assessments p WHERE p.reassessment_state = 'UNDER_REASSESSMENT' AND NOT EXISTS "
                    f"(SELECT 1 FROM assessments c WHERE c.parent_assessment_id = p.id AND c.status NOT IN {final})"
                )
            ).scalar()
            # 0022: UNDER_REASSESSMENT also needs a recorded approval of the
            # parent before that open reassessment began.
            under_without_prior_approval = connection.execute(
                text(
                    "SELECT COUNT(*) FROM assessments p WHERE p.reassessment_state = 'UNDER_REASSESSMENT' AND NOT EXISTS "
                    f"(SELECT 1 FROM assessments c WHERE c.parent_assessment_id = p.id AND c.status NOT IN {final} "
                    "AND (p.committee_decision IN ('APPROVED','APPROVED_WITH_CONDITIONS') "
                    "OR p.status IN ('APPROVED','APPROVED_WITH_CONDITIONS')) "
                    "AND p.committee_decided_at IS NOT NULL AND p.committee_decided_at <= c.created_at)"
                )
            ).scalar() if head_revision() >= "0022" else 0
            orphan_flags = connection.execute(
                text(
                    "SELECT COUNT(*) FROM assessments p WHERE p.reassessment_state IS NOT NULL AND NOT EXISTS "
                    "(SELECT 1 FROM assessments c WHERE c.parent_assessment_id = p.id)"
                )
            ).scalar()
            states = {
                (row[0] or "IN_FORCE"): row[1]
                for row in connection.execute(
                    text("SELECT reassessment_state, COUNT(*) FROM assessments GROUP BY reassessment_state")
                )
            }
            detail = {"states": states, "superseded_without_approved_child": superseded_bad,
                      "under_reassessment_without_open_child": under_bad, "flag_without_children": orphan_flags,
                      "under_reassessment_without_prior_approval": under_without_prior_approval}
            return (
                not (superseded_bad or under_bad or orphan_flags or under_without_prior_approval),
                json.dumps(detail, sort_keys=True),
            )
    finally:
        engine.dispose()


def head_revision() -> str:
    from alembic.config import Config
    from alembic.script import ScriptDirectory

    return ScriptDirectory.from_config(Config(str(BACKEND / "alembic.ini"))).get_current_head()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--status-only", action="store_true")
    parser.add_argument("--rollback-check", action="store_true")
    parser.add_argument("--local-rehearsal", action="store_true")
    parser.add_argument("--expect-revision", default=None)
    # A rehearsal on another remote branch (e.g. a temporary copy of
    # production) writes its own report, never the staging evidence file.
    parser.add_argument("--report-name", default=None)
    # The rollback leg goes back to this revision (e.g. the starting one, to
    # exercise every new migration's downgrade) instead of one step.
    parser.add_argument("--rollback-to", default=None)
    args = parser.parse_args(argv)
    if args.rollback_to and not args.rollback_check:
        raise Refused("--rollback-to needs --rollback-check.")
    if args.report_name and (not args.report_name.endswith(".json") or "/" in args.report_name or "\\" in args.report_name
                             or args.report_name == "staging_migration_report.json"):
        raise Refused("--report-name must be a plain *.json file name other than staging_migration_report.json.")

    url = staging_url(args.local_rehearsal)
    host = _host(url) or "local SQLite rehearsal"
    head = head_revision()
    from datetime import datetime, timezone

    report: dict = {
        "target": host,
        "mode": "local rehearsal (not staging evidence)" if args.local_rehearsal else "staging",
        "run_at": datetime.now(timezone.utc).isoformat(),
        "code_head": head,
        "expected_revision": args.expect_revision,
        "checks": [],
    }

    def check(name: str, ok: bool, detail: str = "") -> None:
        report["checks"].append({"check": name, "ok": bool(ok), "detail": detail})
        print(f"[{'PASS' if ok else 'FAIL'}] {name}" + (f" -- {detail}" if detail else ""))

    before = snapshot(url)
    print(f"Target: {host}; revision before: {before['revision']}; code head: {head}")
    print(f"Row counts before: {before['counts']}")
    report["before"] = {"revision": before["revision"], "counts": before["counts"]}
    if args.expect_revision and before["revision"] != args.expect_revision:
        print(f"STOP: the database is at {before['revision']}, not the expected {args.expect_revision}. Nothing was changed.")
        return 2
    if args.status_only:
        return 0

    upgrade = _run(url, "-m", "alembic", "upgrade", "head")
    check("alembic upgrade head", upgrade.returncode == 0, upgrade.stderr.strip().splitlines()[-1] if upgrade.returncode else "")
    if upgrade.returncode:
        print(json.dumps(report, indent=2, default=str))
        return 1

    after = snapshot(url)
    report["after"] = {"revision": after["revision"], "counts": after["counts"]}
    check("database at code head", after["revision"] == head, f"{after['revision']}")
    lost = {t: (before["counts"][t], after["counts"].get(t)) for t in before["counts"] if after["counts"].get(t) != before["counts"][t]}
    check("no rows lost or added in governed tables", not lost, json.dumps(lost))
    check("committee vote values unchanged", after["vote_fingerprint"] == before["vote_fingerprint"])
    check("no assessment's pipeline or lifecycle status changed", after["status_fingerprint"] == before["status_fingerprint"])
    check(
        "existing votes are version 1 and current",
        all(v[6] == 1 and bool(v[7]) for v in after["votes"]) if after["votes"] and len(after["votes"][0]) > 6 else True,
    )
    check("0017 vote constraints present", "uq_committee_vote_version" in after["vote_constraints"] and "uq_committee_vote_member" not in after["vote_constraints"])
    check("0017 one-current-vote index present", "uq_committee_vote_current" in after["vote_indexes"])
    check("0017 tables present", {"challenge_review_signoffs", "control_revisions"} <= after["tables"])
    if head >= "0018":
        check("0018 SoD tables present", {"sod_exceptions", "sod_exception_events"} <= after["tables"])
        check("0018: existing overrides and sign-offs left unlabelled", _legacy_unlabelled(url))
    if head >= "0019":
        check("0019 retention tables present", {"retention_policy_versions", "legal_hold_events"} <= after["tables"])
        check("0019: v1 backfilled from the existing policy, holds have history, nothing marked approved", _retention_backfilled(url))
        schema = _retention_schema(url)
        check("0019 partial unique indexes present (one ACTIVE / one PROPOSED per record type)", schema["partial_unique_indexes"])
        check("0019 foreign keys present", schema["foreign_keys"])
        check("0019: no orphaned hold events, no duplicate open versions",
              schema["orphan_hold_events"] == 0 and schema["duplicate_open_versions"] == 0, json.dumps(schema))
        ok, detail = _eligibility_probe(url)
        check("0019: eligibility uses v1; held records never eligible; undecided never eligible (read-only)", ok, detail)
    if head >= "0020":
        check("0020 traceability tables present", {"intake_snapshots", "evidence_acknowledgements"} <= after["tables"])
        ok, detail = _columns_present(url, ADDED_0020)
        check("0020 columns present", ok, detail)
        ok, detail = _traceability_untouched(url)
        check("0020: nothing backfilled (no snapshots, acknowledgements, provenance or rule triggers invented)", ok, detail)
    if head >= "0021":
        ok, detail = _columns_present(url, ADDED_0021)
        check("0021 columns present", ok, detail)
        ok, detail = _reassessment_consistent(url)
        check("0021: every reassessment flag is backed by its parent/child rows", ok, detail)
    if head >= "0023":
        ok, detail = _columns_present(url, ADDED_0023)
        check("0023 source-file columns present", ok, detail)

    startup = _run(
        url,
        "-c",
        "from fastapi.testclient import TestClient; from app.main import app, prepare_database; "
        "prepare_database(); print(TestClient(app).get('/health').json())",
    )
    check("application starts against the migrated schema", startup.returncode == 0 and "healthy" in startup.stdout, (startup.stderr.strip().splitlines() or [""])[-1] if startup.returncode else "")

    if args.rollback_check:
        # Compared with the state just before the rollback (start-up may
        # have seeded the bootstrap admin on an empty rehearsal database).
        pre_rollback = snapshot(url)
        target = args.rollback_to or "-1"
        down = _run(url, "-m", "alembic", "downgrade", target)
        mid = snapshot(url) if down.returncode == 0 else None
        check(f"rollback (downgrade {target}) succeeds", down.returncode == 0, (down.stderr.strip().splitlines() or [""])[-1] if down.returncode else "")
        if mid:
            if args.rollback_to:
                check("rolled back to the requested revision", mid["revision"] == args.rollback_to, str(mid["revision"]))
            check("no rows lost by rollback", mid["counts"] == pre_rollback["counts"], json.dumps(mid["counts"]))
            check("statuses unchanged by rollback", mid["status_fingerprint"] == before["status_fingerprint"])
        up = _run(url, "-m", "alembic", "upgrade", "head")
        final = snapshot(url)
        check("re-upgrade after rollback", up.returncode == 0 and final["revision"] == head)
        check("vote values unchanged after round trip", final["vote_fingerprint"] == before["vote_fingerprint"])
        check("statuses unchanged after round trip", final["status_fingerprint"] == before["status_fingerprint"])
        if head >= "0019":
            check("0019: v1 and hold history backfilled again after round trip", _retention_backfilled(url))
        if head >= "0021":
            ok, detail = _reassessment_consistent(url)
            check("0021: reassessment flags derived again after round trip", ok, detail)

    failed = [c for c in report["checks"] if not c["ok"]]
    print(f"\n{len(report['checks']) - len(failed)}/{len(report['checks'])} checks passed on {host}.")
    # A local rehearsal never overwrites the evidence of a real staging run.
    name = args.report_name or ("staging_migration_report.json" if _host(url) else "staging_migration_rehearsal_report.json")
    out = BACKEND / "eval_results" / name
    out.parent.mkdir(exist_ok=True)
    out.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
    print(f"Report: {out}")
    return 1 if failed else 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Refused as exc:
        print(f"Refused: {exc.code}", file=sys.stderr)
        sys.exit(2)

"""
Starter catalogue of official AML/KYC/sanctions sources and internal
policy placeholders (app/reference_data/source_library_catalog.json).

Seeding creates each missing entry as a source record with one DRAFT
placeholder version. It never approves anything, never changes a record that
already exists (matched by source ID), and is safe to run repeatedly.
Whether a link is the right one, and whether a source may be used as formal
evidence, is for the source owner and an authorised compliance reviewer.

CLI (from backend/):

    python -m app.services.source_catalog seed --dry-run
    python -m app.services.source_catalog seed [--confirm-host <host>]
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session

from app.models.source_library import SourceRecord, SourceVersion
from app.models.user import User
from app.services import source_governance as gov

CATALOG_PATH = Path(__file__).resolve().parent.parent / "reference_data" / "source_library_catalog.json"


def load_catalog() -> dict[str, Any]:
    return json.loads(CATALOG_PATH.read_text(encoding="utf-8"))


def seed(db: Session, user: User | None = None, *, dry_run: bool = False) -> dict[str, list[str]]:
    """Creates the missing catalogue entries. Returns {"created": [...], "existing": [...]}."""

    catalog = load_catalog()
    defaults = catalog.get("defaults", {})
    existing_codes = {code for (code,) in db.query(SourceRecord.source_code).all()}
    created: list[str] = []
    existing: list[str] = []
    for entry in catalog["sources"]:
        code = entry["source_code"]
        if code in existing_codes:
            existing.append(code)
            continue
        created.append(code)
        if dry_run:
            continue
        note = "" if entry.get("url_confirmed") or not entry.get("source_url") else " Link not yet confirmed by the source owner."
        record = SourceRecord(
            source_code=code,
            title=entry["title"],
            authority=entry["authority"],
            category=entry["category"],
            jurisdiction=entry.get("jurisdiction"),
            applicable_entity=entry.get("applicable_entity"),
            source_url=entry.get("source_url"),
            description=((entry.get("description") or "") + note).strip() or None,
            topics=list(entry.get("topics") or []),
            owner=entry.get("owner") or defaults.get("owner"),
            review_frequency=entry.get("review_frequency") or defaults.get("review_frequency"),
            status="ACTIVE",
            created_by=user.full_name or user.email if user else "System (starter catalogue)",
            created_by_id=user.id if user else None,
        )
        db.add(record)
        db.flush()
        version = SourceVersion(
            record_id=record.id,
            version_number=1,
            version_label=entry.get("version_label") or defaults.get("version_label") or "Not yet captured",
            source_url=entry.get("source_url"),
            change_summary="Placeholder from the starter catalogue. Record the publisher's version and effective date, "
            "and upload a controlled copy where one is needed, before submitting for review.",
            status="DRAFT",
            created_by=record.created_by,
            created_by_id=record.created_by_id,
        )
        db.add(version)
        db.flush()
        gov.audit(db, user, "RECORD_CREATED", record=record, version=version,
                  details=f"Source {code} '{record.title}' created from the starter catalogue as a draft.",
                  data={"catalogue": True})
    if not dry_run:
        db.commit()
    return {"created": created, "existing": existing}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m app.services.source_catalog")
    sub = parser.add_subparsers(dest="command", required=True)
    seed_cmd = sub.add_parser("seed", help="create the missing catalogue entries as drafts")
    seed_cmd.add_argument("--dry-run", action="store_true", help="show what would be created, change nothing")
    seed_cmd.add_argument("--confirm-host", help="the database host you intend to write to (required for a remote database)")
    args = parser.parse_args(argv)

    from app.database import DATABASE_URL, SessionLocal
    from app.migrations import database_host, is_local

    host = database_host(DATABASE_URL) or "sqlite (local file)"
    if not args.dry_run and not is_local(DATABASE_URL) and (args.confirm_host or "").lower() != host.lower():
        print(f"Refused: {host} is a remote database. Re-run with --confirm-host {host} to write to it.", file=sys.stderr)
        return 2
    db = SessionLocal()
    try:
        result = seed(db, dry_run=args.dry_run)
    finally:
        db.close()
    verb = "Would create" if args.dry_run else "Created"
    print(f"Database: {host}")
    print(f"{verb} {len(result['created'])} source(s); {len(result['existing'])} already present.")
    for code in result["created"]:
        print(f"  + {code}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

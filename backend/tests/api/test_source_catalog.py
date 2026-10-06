"""The starter catalogue of official AML/KYC/sanctions sources."""

from __future__ import annotations

import json

import pytest

from app.database import SessionLocal
from app.models.source_library import SourceRecord
from app.services import source_catalog
from tests.api.test_source_library_governance import BASE, act, ids
from tests.conftest import ok


def test_the_catalogue_is_well_formed():
    catalog = source_catalog.load_catalog()
    codes = [entry["source_code"] for entry in catalog["sources"]]
    assert len(codes) == len(set(codes)) >= 30
    from app.services.source_governance import CATEGORIES

    for entry in catalog["sources"]:
        assert entry["category"] in CATEGORIES, entry["source_code"]
        assert entry["title"] and entry["authority"]
        url = entry.get("source_url")
        assert url is None or url.startswith("https://"), entry["source_code"]
    # The suggested IDs from the requirements are all present.
    for code in ("SRC-RBI-KYC-001", "SRC-FATF-REC-001", "SRC-FFIEC-RA-001", "SRC-OFAC-FWK-001", "SRC-INT-METH-001", "SRC-UN-SAN-001"):
        assert code in codes
    rbi = next(e for e in catalog["sources"] if e["source_code"] == "SRC-RBI-KYC-001")
    assert rbi["source_url"].endswith("notification.aspx?id=2607") and "Sanctions screening" in rbi["topics"]


def test_seeding_creates_drafts_only_and_is_idempotent(client, auth):
    forbidden = client.post(f"{BASE}/catalog/seed", headers=auth("analyst"))
    assert forbidden.status_code == 403
    assert client.post(f"{BASE}/catalog/seed", headers=auth("compliance")).status_code == 403  # reviewers don't maintain

    first = ok(client.post(f"{BASE}/catalog/seed", headers=auth("policy")))
    total = len(source_catalog.load_catalog()["sources"])
    assert first["created"] + first["existing"] == total
    second = ok(client.post(f"{BASE}/catalog/seed", headers=auth("policy")))
    assert second["created"] == 0 and second["existing"] == total

    record = ok(client.get(f"{BASE}/records", params={"q": "SRC-RBI-KYC-001"}, headers=auth("policy")))["items"][0]
    assert record["library_status"] == "DRAFT" and record["current_version"] is None
    assert record["jurisdiction"] == "India" and record["owner"] == "FCRM Compliance Owner" and "KYC" in record["topics"]
    # Nothing is visible to ordinary users until reviewed.
    assert ok(client.get(f"{BASE}/records", params={"q": "SRC-RBI-KYC-001"}, headers=auth("analyst")))["total"] == 0

    # An unconfirmed link carries a note; the user-supplied RBI link does not.
    detail = ok(client.get(f"{BASE}/records/{record['id']}", headers=auth("policy")))
    assert "not yet confirmed" not in (detail["description"] or "")
    other = ok(client.get(f"{BASE}/records", params={"q": "SRC-FATF-REC-001"}, headers=auth("policy")))["items"][0]
    assert "not yet confirmed" in (other["description"] or "")

    # An internal policy placeholder cannot be submitted until its document is uploaded.
    internal = ok(client.get(f"{BASE}/records", params={"q": "SRC-INT-AML-001"}, headers=auth("policy")))["items"][0]
    rid, vid = internal["id"], internal["pending_version"]["id"]
    assert act(client, auth("policy"), rid, vid, "submit").json()["detail"]["code"] == "NOTHING_TO_REVIEW"
    # A link-only official source can be submitted, but still needs a reviewer to approve it.
    rid, vid = record["id"], record["pending_version"]["id"]
    ok(act(client, auth("policy"), rid, vid, "submit"))
    assert act(client, auth("policy"), rid, vid, "approve", comment="Approving what I submitted.").status_code == 403


def test_the_cli_dry_run_changes_nothing():
    db = SessionLocal()
    try:
        before = db.query(SourceRecord).count()
        result = source_catalog.seed(db, dry_run=True)
        assert db.query(SourceRecord).count() == before
        assert len(result["created"]) + len(result["existing"]) == len(source_catalog.load_catalog()["sources"])
    finally:
        db.close()

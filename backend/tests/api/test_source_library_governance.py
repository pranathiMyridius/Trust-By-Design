"""
Source Library API: creating sources and versions, the review workflow,
role-based access, separation of duties, version history, the audit trail,
uploads and listing. Real app and database; fake LLM (embeddings are off on
SQLite, so no network is involved).

Users (tests/conftest.py): `policy` is a Policy Admin and `admin` an Admin
(maintainers); `compliance` and `compliance2` are Managers holding a
compliance designation (reviewers); `analyst`, `owner`, `manager` have
neither; `auditor` is read-only.
"""

from __future__ import annotations

import json
import uuid

import pytest

from app.database import SessionLocal
from app.file_processing.storage import LIBRARY_DIR, read_library_file
from app.governance import policy
from app.models.source_library import SourceAuditLog, SourceVersion
from tests.conftest import ok
from tests.support.pdf import EICAR, encrypted, make_pdf

BASE = "/api/source-library"


def unique_pdf(*pages: str, marker: str | None = None) -> bytes:
    token = marker or uuid.uuid4().hex
    return make_pdf([f"{page}\nref {token}" for page in (pages or ("Customer due diligence applies.",))])


def new_source(client, headers, *, pdf: bytes | None = None, expect: int = 201, **overrides):
    data = {
        "title": f"Test Source {uuid.uuid4().hex[:6]}",
        "authority": "Reserve Bank of India",
        "category": "REGULATORY_REQUIREMENT",
        "jurisdiction": "India",
        "owner": "FCRM Compliance Owner",
        "topics": "KYC, CDD",
        "source_url": "https://www.rbi.org.in/example",
        "version_label": "2026.1",
        **overrides,
    }
    files = {"file": ("policy.pdf", pdf, "application/pdf")} if pdf is not None else None
    response = client.post(f"{BASE}/records", data=data, files=files, headers=headers)
    return ok(response, expect) if expect else response


def ids(record: dict, index: int = 0) -> tuple[str, str]:
    return record["id"], record["versions"][index]["id"]


def act(client, headers, record_id, version_id, action, **body):
    return client.post(f"{BASE}/records/{record_id}/versions/{version_id}/{action}", json=body, headers=headers)


def approve_flow(client, auth, record, comment="Reviewed against the official text."):
    rid, vid = ids(record)
    ok(act(client, auth("policy"), rid, vid, "submit"))
    return ok(act(client, auth("compliance"), rid, vid, "approve", comment=comment))


@pytest.fixture
def dual_role_policy(tmp_path, monkeypatch):
    """A policy under which the compliance Manager is also a maintainer, so
    separation of duties (not just the role split) can be exercised."""

    path = tmp_path / "policy.json"
    rules = policy.policy()["source_library"]
    rules["maintainers"] = {"roles": ["POLICY_ADMIN", "ADMIN"], "designations": ["COMPLIANCE_MANAGER"]}
    path.write_text(json.dumps({"source_library": rules}), encoding="utf-8")
    monkeypatch.setenv("GOVERNANCE_POLICY_FILE", str(path))
    policy.reload()
    yield
    monkeypatch.delenv("GOVERNANCE_POLICY_FILE")
    policy.reload()


# -- who may do what -------------------------------------------------------------------


def test_meta_reports_the_callers_capabilities(client, auth):
    assert ok(client.get(f"{BASE}/meta", headers=auth("policy")))["can_maintain"] is True
    reviewer = ok(client.get(f"{BASE}/meta", headers=auth("compliance")))
    assert reviewer["can_review"] is True and reviewer["can_maintain"] is False
    analyst = ok(client.get(f"{BASE}/meta", headers=auth("analyst")))
    assert (analyst["can_maintain"], analyst["can_review"], analyst["can_view_all"]) == (False, False, False)
    assert ok(client.get(f"{BASE}/meta", headers=auth("auditor")))["can_view_all"] is True


def test_the_library_needs_a_login(client):
    assert client.get(f"{BASE}/records").status_code == 401
    assert client.get(f"{BASE}/meta").status_code == 401


@pytest.mark.parametrize("who", ["analyst", "owner", "manager", "committee", "compliance"])
def test_only_maintainers_can_create_sources(client, auth, who):
    response = new_source(client, auth(who), expect=0)
    assert response.status_code == 403
    assert response.json()["detail"]["code"] == "NOT_A_MAINTAINER"


def test_a_read_only_auditor_cannot_change_anything(client, auth):
    assert new_source(client, auth("auditor"), expect=0).status_code == 403


def test_a_maintainer_creates_a_draft_link_only_source(client, auth):
    record = new_source(client, auth("policy"))
    assert record["library_status"] == "DRAFT" and record["current_version"] is None
    version = record["versions"][0]
    assert version["status"] == "DRAFT" and version["processing_status"] == "NONE" and not version["has_file"]
    assert record["source_code"].startswith("SRC-")
    assert record["topics"] == ["KYC", "CDD"]


def test_source_ids_are_unique_and_normalised(client, auth):
    code = f"SRC-DUP-{uuid.uuid4().hex[:6]}".lower()
    first = new_source(client, auth("policy"), source_code=code)
    assert first["source_code"] == code.upper()
    again = new_source(client, auth("policy"), source_code=code.upper(), expect=0)
    assert again.status_code == 409 and again.json()["detail"]["code"] == "DUPLICATE_SOURCE_CODE"


@pytest.mark.parametrize(
    "overrides, status",
    [
        ({"category": "NOT_A_CATEGORY"}, 422),
        ({"source_url": "javascript:alert(1)"}, 422),
        ({"source_url": "ftp://example.org/x"}, 422),
        ({"title": "   "}, 422),
    ],
)
def test_input_is_validated(client, auth, overrides, status):
    assert new_source(client, auth("policy"), expect=0, **overrides).status_code == status


# -- the review workflow ----------------------------------------------------------------


def test_a_draft_is_invisible_to_ordinary_users_until_approved(client, auth):
    record = new_source(client, auth("policy"), title=f"Hidden until approved {uuid.uuid4().hex[:6]}")
    rid, vid = ids(record)
    assert client.get(f"{BASE}/records/{rid}", headers=auth("analyst")).status_code == 404
    listed = ok(client.get(f"{BASE}/records?q={record['title']}", headers=auth("analyst")))
    assert listed["total"] == 0
    assert client.get(f"{BASE}/records/{rid}/approvals", headers=auth("analyst")).status_code == 403
    assert client.get(f"{BASE}/records/{rid}/audit", headers=auth("analyst")).status_code == 403

    ok(act(client, auth("policy"), rid, vid, "submit"))
    assert client.get(f"{BASE}/records/{rid}", headers=auth("analyst")).status_code == 404  # still not approved
    ok(act(client, auth("compliance"), rid, vid, "approve", comment="Checked against the official source."))
    seen = ok(client.get(f"{BASE}/records/{rid}", headers=auth("analyst")))
    assert seen["library_status"] == "APPROVED" and [v["status"] for v in seen["versions"]] == ["APPROVED"]


def test_submit_approve_records_history_and_audit(client, auth):
    record = new_source(client, auth("policy"))
    rid, vid = ids(record)
    submitted = ok(act(client, auth("policy"), rid, vid, "submit"))
    assert submitted["versions"][0]["status"] == "IN_REVIEW" and submitted["library_status"] == "IN_REVIEW"
    approved = ok(act(client, auth("compliance"), rid, vid, "approve", comment="Matches the RBI Direction text."))
    assert approved["library_status"] == "APPROVED"
    current = approved["current_version"]
    assert current["decided_by"] and current["decision_comment"] == "Matches the RBI Direction text."

    history = ok(client.get(f"{BASE}/records/{rid}/approvals", headers=auth("compliance")))
    assert [(row["action"], row["to_status"]) for row in reversed(history)] == [("SUBMITTED", "IN_REVIEW"), ("APPROVED", "APPROVED")]
    assert history[0]["comment"] == "Matches the RBI Direction text."

    audit = ok(client.get(f"{BASE}/records/{rid}/audit", headers=auth("auditor")))
    events = [row["event_type"] for row in audit["items"]]
    for expected in ("RECORD_CREATED", "VERSION_CREATED", "SUBMITTED", "APPROVED"):
        assert expected in events
    assert all(row["actor"] for row in audit["items"])


@pytest.mark.parametrize("who", ["analyst", "owner", "manager", "admin", "policy", "auditor", "committee"])
def test_unauthorised_users_cannot_approve_or_reject(client, auth, who):
    record = new_source(client, auth("policy"))
    rid, vid = ids(record)
    ok(act(client, auth("policy"), rid, vid, "submit"))
    for action in ("approve", "reject"):
        response = act(client, auth(who), rid, vid, action, comment="I would like this approved please.")
        assert response.status_code == 403, (who, action, response.text)
    # Nothing changed, and the refusal is on record.
    assert ok(client.get(f"{BASE}/records/{rid}", headers=auth("policy")))["versions"][0]["status"] == "IN_REVIEW"
    if who != "auditor":  # the read-only gate refuses auditors first (and logs it application-wide)
        audit = ok(client.get(f"{BASE}/records/{rid}/audit?event_type=ACTION_DENIED", headers=auth("policy")))
        assert audit["total"] >= 2 and "refused" in audit["items"][0]["details"]


def test_a_decision_needs_a_substantive_comment(client, auth):
    record = new_source(client, auth("policy"))
    rid, vid = ids(record)
    ok(act(client, auth("policy"), rid, vid, "submit"))
    for comment in ("", "   ", "ok"):
        response = act(client, auth("compliance"), rid, vid, "approve", comment=comment)
        assert response.status_code == 422 and response.json()["detail"]["code"] == "COMMENT_REQUIRED"
    assert act(client, auth("compliance"), rid, vid, "reject", comment="").status_code == 422


def test_only_a_version_in_review_can_be_decided(client, auth):
    record = new_source(client, auth("policy"))
    rid, vid = ids(record)
    early = act(client, auth("compliance"), rid, vid, "approve", comment="Approving a draft directly.")
    assert early.status_code == 409 and early.json()["detail"]["code"] == "INVALID_TRANSITION"
    approve_flow(client, auth, record)
    again = act(client, auth("compliance2"), rid, vid, "approve", comment="Approving it a second time.")
    assert again.status_code == 409


def test_separation_of_duties_blocks_the_preparer_from_deciding(client, auth, dual_role_policy):
    # Under this policy the compliance Manager may also maintain sources.
    record = new_source(client, auth("compliance"))
    rid, vid = ids(record)
    ok(act(client, auth("compliance"), rid, vid, "submit"))
    for action in ("approve", "reject"):
        response = act(client, auth("compliance"), rid, vid, action, comment="Approving my own submission.")
        assert response.status_code == 403 and response.json()["detail"]["code"] == "SEPARATION_OF_DUTIES"
    # A different reviewer can.
    ok(act(client, auth("compliance2"), rid, vid, "approve", comment="Independently reviewed and approved."))


def test_anyone_who_edited_the_version_is_also_excluded(client, auth, dual_role_policy):
    record = new_source(client, auth("policy"))
    rid, vid = ids(record)
    # compliance (a maintainer under this policy) touches the draft...
    ok(client.patch(f"{BASE}/records/{rid}/versions/{vid}", json={"change_summary": "Clarified wording."}, headers=auth("compliance")))
    ok(act(client, auth("policy"), rid, vid, "submit"))
    # ...so they can no longer decide it, though they never created or submitted it.
    response = act(client, auth("compliance"), rid, vid, "approve", comment="I edited this, but approving it.")
    assert response.status_code == 403 and response.json()["detail"]["code"] == "SEPARATION_OF_DUTIES"
    ok(act(client, auth("compliance2"), rid, vid, "approve", comment="Independent review of the final text."))


def test_rejection_returns_the_version_for_rework(client, auth):
    record = new_source(client, auth("policy"))
    rid, vid = ids(record)
    ok(act(client, auth("policy"), rid, vid, "submit"))
    rejected = ok(act(client, auth("compliance"), rid, vid, "reject", comment="Wrong version of the Direction cited."))
    assert rejected["versions"][0]["status"] == "REJECTED" and rejected["current_version"] is None
    # Editing a rejected version reopens it as a draft, and it can be resubmitted.
    ok(client.patch(f"{BASE}/records/{rid}/versions/{vid}", json={"version_label": "2026.2"}, headers=auth("policy")))
    reopened = ok(client.get(f"{BASE}/records/{rid}", headers=auth("policy")))
    assert reopened["versions"][0]["status"] == "DRAFT"
    ok(act(client, auth("policy"), rid, vid, "submit"))
    history = ok(client.get(f"{BASE}/records/{rid}/approvals", headers=auth("policy")))
    assert [row["action"] for row in reversed(history)] == ["SUBMITTED", "REJECTED", "REOPENED", "SUBMITTED"]


def test_withdrawing_returns_a_version_in_review_to_draft(client, auth):
    record = new_source(client, auth("policy"))
    rid, vid = ids(record)
    ok(act(client, auth("policy"), rid, vid, "submit"))
    assert act(client, auth("analyst"), rid, vid, "withdraw", comment="not mine").status_code == 403
    withdrawn = ok(act(client, auth("policy"), rid, vid, "withdraw", comment="Needs a better source"))
    assert withdrawn["versions"][0]["status"] == "DRAFT"


def test_a_version_in_review_cannot_be_edited(client, auth):
    record = new_source(client, auth("policy"))
    rid, vid = ids(record)
    ok(act(client, auth("policy"), rid, vid, "submit"))
    response = client.patch(f"{BASE}/records/{rid}/versions/{vid}", json={"version_label": "sneaky"}, headers=auth("policy"))
    assert response.status_code == 409 and response.json()["detail"]["code"] == "VERSION_NOT_EDITABLE"


def test_nothing_to_review_cannot_be_submitted(client, auth):
    record = new_source(client, auth("policy"), source_url="", version_source_url="")
    rid, vid = ids(record)
    response = act(client, auth("policy"), rid, vid, "submit")
    assert response.status_code == 422 and response.json()["detail"]["code"] == "NOTHING_TO_REVIEW"


# -- versions: an approved version is never replaced automatically ------------------------


def test_a_new_version_never_replaces_the_approved_one_until_approved(client, auth):
    record = approve_flow(client, auth, new_source(client, auth("policy"), version_label="v1"))
    rid = record["id"]
    v1 = record["current_version"]["id"]

    drafted = ok(client.post(f"{BASE}/records/{rid}/versions", data={"version_label": "v2", "change_summary": "Annual update"}, headers=auth("policy")), 201)
    v2 = drafted["pending_version"]["id"]
    assert drafted["current_version"]["id"] == v1 and drafted["library_status"] == "APPROVED"

    ok(act(client, auth("policy"), rid, v2, "submit"))
    in_review = ok(client.get(f"{BASE}/records/{rid}", headers=auth("policy")))
    assert in_review["current_version"]["id"] == v1  # still v1 while v2 is under review

    # An ordinary user sees only v1.
    seen = ok(client.get(f"{BASE}/records/{rid}", headers=auth("analyst")))
    assert [v["version_label"] for v in seen["versions"]] == ["v1"] and seen["pending_version"] is None

    ok(act(client, auth("compliance"), rid, v2, "reject", comment="Not the final published text."))
    assert ok(client.get(f"{BASE}/records/{rid}", headers=auth("policy")))["current_version"]["id"] == v1


def test_approving_the_successor_supersedes_the_old_version_with_history(client, auth):
    record = approve_flow(client, auth, new_source(client, auth("policy"), version_label="v1"))
    rid, v1 = record["id"], record["current_version"]["id"]
    v2 = ok(client.post(f"{BASE}/records/{rid}/versions", data={"version_label": "v2"}, headers=auth("policy")), 201)["pending_version"]["id"]
    ok(act(client, auth("policy"), rid, v2, "submit"))
    after = ok(act(client, auth("compliance2"), rid, v2, "approve", comment="Annual update verified against the source."))
    assert after["current_version"]["id"] == v2
    by_label = {v["version_label"]: v["status"] for v in after["versions"]}
    assert by_label == {"v1": "SUPERSEDED", "v2": "APPROVED"}
    history = ok(client.get(f"{BASE}/records/{rid}/approvals", headers=auth("policy")))
    assert any(row["action"] == "SUPERSEDED" and row["version_label"] == "v1" for row in history)
    audit = ok(client.get(f"{BASE}/records/{rid}/audit?event_type=SUPERSEDED", headers=auth("policy")))
    assert audit["total"] == 1


def test_only_one_open_version_at_a_time(client, auth):
    record = approve_flow(client, auth, new_source(client, auth("policy")))
    rid = record["id"]
    ok(client.post(f"{BASE}/records/{rid}/versions", data={"version_label": "v2"}, headers=auth("policy")), 201)
    again = client.post(f"{BASE}/records/{rid}/versions", data={"version_label": "v3"}, headers=auth("policy"))
    assert again.status_code == 409 and again.json()["detail"]["code"] == "OPEN_VERSION_EXISTS"


def test_the_database_itself_refuses_two_approved_versions(client, auth):
    record = approve_flow(client, auth, new_source(client, auth("policy")))
    rid = record["id"]
    v2 = ok(client.post(f"{BASE}/records/{rid}/versions", data={"version_label": "v2"}, headers=auth("policy")), 201)["pending_version"]["id"]
    from sqlalchemy.exc import IntegrityError

    db = SessionLocal()
    try:
        version = db.get(SourceVersion, uuid.UUID(v2))
        version.status = "APPROVED"
        with pytest.raises(IntegrityError):
            db.commit()
    finally:
        db.rollback()
        db.close()


def test_an_approved_version_is_immutable(client, auth):
    record = approve_flow(client, auth, new_source(client, auth("policy")))
    rid, vid = record["id"], record["current_version"]["id"]
    assert client.patch(f"{BASE}/records/{rid}/versions/{vid}", json={"version_label": "x"}, headers=auth("policy")).status_code == 409
    upload = client.post(f"{BASE}/records/{rid}/versions/{vid}/file", files={"file": ("a.pdf", unique_pdf(), "application/pdf")}, headers=auth("policy"))
    assert upload.status_code == 409


def test_retiring_a_source_withdraws_it_from_use(client, auth):
    record = approve_flow(client, auth, new_source(client, auth("policy")))
    rid = record["id"]
    assert client.post(f"{BASE}/records/{rid}/retire", json={"reason": "No longer applies."}, headers=auth("analyst")).status_code == 403
    assert client.post(f"{BASE}/records/{rid}/retire", json={"reason": "short"}, headers=auth("policy")).status_code == 422
    retired = ok(client.post(f"{BASE}/records/{rid}/retire", json={"reason": "Superseded by a new regulation."}, headers=auth("policy")))
    assert retired["status"] == "RETIRED" and retired["library_status"] == "RETIRED" and retired["current_version"] is None
    assert client.get(f"{BASE}/records/{rid}", headers=auth("analyst")).status_code == 404
    assert client.post(f"{BASE}/records/{rid}/versions", data={"version_label": "v2"}, headers=auth("policy")).status_code == 409


# -- metadata edits are audited ---------------------------------------------------------------


def test_changing_where_an_approved_source_applies_needs_a_reason(client, auth):
    record = approve_flow(client, auth, new_source(client, auth("policy")))
    rid = record["id"]
    response = client.patch(f"{BASE}/records/{rid}", json={"jurisdiction": "European Union"}, headers=auth("policy"))
    assert response.status_code == 422 and response.json()["detail"]["code"] == "CHANGE_REASON_REQUIRED"
    ok(client.patch(f"{BASE}/records/{rid}", json={"jurisdiction": "European Union", "change_reason": "Source actually issued by the EBA."}, headers=auth("policy")))
    audit = ok(client.get(f"{BASE}/records/{rid}/audit?event_type=RECORD_UPDATED", headers=auth("policy")))
    change = audit["items"][0]["event_data"]
    assert change["before"] == {"jurisdiction": "India"} and change["after"] == {"jurisdiction": "European Union"}
    # An unrelated edit needs no reason.
    ok(client.patch(f"{BASE}/records/{rid}", json={"owner": "Head of FCRM"}, headers=auth("policy")))
    assert client.patch(f"{BASE}/records/{rid}", json={"title": "x"}, headers=auth("analyst")).status_code == 403


# -- uploads and document processing ----------------------------------------------------------


def test_uploading_a_pdf_stores_hashes_and_processes_it(client, auth):
    pdf = unique_pdf("CHAPTER 1 DUE DILIGENCE\nRegulated entities shall identify every customer.", "2.1 Ownership\nIdentify the beneficial owner.")
    record = new_source(client, auth("policy"), pdf=pdf)
    rid, vid = ids(record)
    version = record["versions"][0]
    assert version["has_file"] and version["original_filename"] == "policy.pdf"
    assert version["processing_status"] == "PROCESSED" and version["page_count"] == 2 and version["chunk_count"] >= 2
    assert version["scan_status"] == "CLEAN" and len(version["file_sha256"]) == 64
    assert "storage_key" not in version and "extracted_text" not in version  # neither the path nor the text leaks

    chunks = ok(client.get(f"{BASE}/records/{rid}/versions/{vid}/chunks", headers=auth("compliance")))
    assert chunks["total"] == version["chunk_count"]
    assert {c["page_start"] for c in chunks["items"]} == {1, 2}
    assert any((c["section"] or "").startswith("2.1") for c in chunks["items"])
    assert client.get(f"{BASE}/records/{rid}/versions/{vid}/chunks", headers=auth("analyst")).status_code == 403


def test_the_stored_file_is_private_and_served_only_through_the_api(client, auth):
    pdf = unique_pdf()
    record = new_source(client, auth("policy"), pdf=pdf)
    rid, vid = ids(record)
    db = SessionLocal()
    try:
        key = db.get(SourceVersion, uuid.UUID(vid)).storage_key
    finally:
        db.close()
    assert key.endswith(".pdf") and "policy" not in key  # random name, never the client's
    assert read_library_file(key) == pdf and (LIBRARY_DIR in __import__("os").path.abspath(__import__("os").path.join(LIBRARY_DIR, key)))

    url = f"{BASE}/records/{rid}/versions/{vid}/file"
    assert client.get(url).status_code == 401
    assert client.get(url, headers=auth("analyst")).status_code == 404  # not approved yet
    download = client.get(url, headers=auth("policy"))
    assert download.status_code == 200 and download.content == pdf
    assert download.headers["content-type"] == "application/pdf" and download.headers["x-content-type-options"] == "nosniff"
    assert "attachment" in download.headers["content-disposition"] and download.headers["cache-control"] == "private, no-store"
    audit = ok(client.get(f"{BASE}/records/{rid}/audit?event_type=FILE_DOWNLOADED", headers=auth("policy")))
    assert audit["total"] == 1

    ok(act(client, auth("policy"), rid, vid, "submit"))
    ok(act(client, auth("compliance"), rid, vid, "approve", comment="Reviewed the document in full."))
    assert client.get(url, headers=auth("analyst")).status_code == 200  # approved: any signed-in user


@pytest.mark.parametrize(
    "name, content_type, data, status, code",
    [
        ("notes.txt", "text/plain", b"%PDF-1.4 hi", 400, "UNSUPPORTED_FILE_TYPE"),
        ("fake.pdf", "application/pdf", b"MZ not really a pdf", 400, "NOT_A_PDF"),
        ("empty.pdf", "application/pdf", b"", 400, "EMPTY_FILE"),
    ],
)
def test_bad_uploads_are_refused_and_create_nothing(client, auth, name, content_type, data, status, code):
    title = f"Refused {uuid.uuid4().hex[:6]}"
    response = client.post(f"{BASE}/records", data={"title": title, "authority": "X", "category": "INTERNAL_POLICY", "version_label": "1"},
                           files={"file": (name, data, content_type)}, headers=auth("policy"))
    assert response.status_code == status and response.json()["detail"]["code"] == code
    assert ok(client.get(f"{BASE}/records?q={title}", headers=auth("policy")))["total"] == 0  # no empty source left behind


def test_an_oversized_upload_is_refused(client, auth, monkeypatch):
    from app.services import source_documents

    monkeypatch.setattr(source_documents, "MAX_UPLOAD_BYTES", 2000)
    response = new_source(client, auth("policy"), pdf=make_pdf(["x " * 3000]), expect=0)
    assert response.status_code == 413 and response.json()["detail"]["code"] == "FILE_TOO_LARGE"


def test_malware_is_rejected_and_the_attempt_is_audited(client, auth):
    title = f"Infected {uuid.uuid4().hex[:6]}"
    pdf = make_pdf([f"Harmless looking page {title}"], extra=EICAR)
    response = client.post(f"{BASE}/records", data={"title": title, "authority": "X", "category": "INTERNAL_POLICY", "version_label": "1"},
                           files={"file": ("virus.pdf", pdf, "application/pdf")}, headers=auth("policy"))
    assert response.status_code == 422 and response.json()["detail"]["code"] == "MALWARE_DETECTED"
    assert ok(client.get(f"{BASE}/records?q={title}", headers=auth("policy")))["total"] == 0
    db = SessionLocal()
    try:
        rejected = db.query(SourceAuditLog).filter(SourceAuditLog.event_type == "UPLOAD_REJECTED").order_by(SourceAuditLog.created_at.desc()).first()
        assert rejected is not None and "SCAN_INFECTED" in rejected.details and "virus.pdf" in rejected.details
    finally:
        db.close()


def test_pdf_javascript_is_rejected(client, auth):
    response = new_source(client, auth("policy"), pdf=make_pdf(["Policy text for the library"], active_content=True), expect=0)
    assert response.status_code == 422 and response.json()["detail"]["code"] == "MALWARE_DETECTED"


def test_an_unscannable_upload_is_refused_when_scanning_is_required(client, auth, monkeypatch):
    monkeypatch.delenv("CLAMAV_HOST", raising=False)
    monkeypatch.setenv("MALWARE_SCAN_REQUIRED", "true")
    response = new_source(client, auth("policy"), pdf=unique_pdf(), expect=0)
    assert response.status_code == 503 and response.json()["detail"]["code"] == "SCAN_UNAVAILABLE"


def test_duplicate_documents_are_refused_with_a_pointer_to_the_original(client, auth):
    pdf = unique_pdf()
    first = new_source(client, auth("policy"), pdf=pdf)
    second = new_source(client, auth("policy"), pdf=pdf, expect=0)
    assert second.status_code == 409 and second.json()["detail"]["code"] == "DUPLICATE_DOCUMENT"
    assert first["source_code"] in second.json()["detail"]["message"]


def test_unreadable_pdfs_are_stored_as_failed_and_cannot_be_submitted(client, auth):
    record = new_source(client, auth("policy"), pdf=make_pdf(["", ""]))  # a page with no text layer
    rid, vid = ids(record)
    version = record["versions"][0]
    assert version["processing_status"] == "FAILED" and "text layer" in version["processing_error"]
    response = act(client, auth("policy"), rid, vid, "submit")
    assert response.status_code == 409 and response.json()["detail"]["code"] == "PROCESSING_FAILED"
    # Replacing it with a readable PDF fixes it.
    fixed = ok(client.post(f"{BASE}/records/{rid}/versions/{vid}/file", files={"file": ("ok.pdf", unique_pdf(), "application/pdf")}, headers=auth("policy")))
    assert fixed["processing_status"] == "PROCESSED"
    ok(act(client, auth("policy"), rid, vid, "submit"))


def test_an_encrypted_pdf_fails_processing_with_a_clear_reason(client, auth):
    record = new_source(client, auth("policy"), pdf=encrypted(unique_pdf()))
    assert record["versions"][0]["processing_status"] == "FAILED"
    assert "password" in record["versions"][0]["processing_error"]


def test_reprocessing_rebuilds_the_chunks(client, auth):
    record = new_source(client, auth("policy"), pdf=unique_pdf("Alpha beta gamma."))
    rid, vid = ids(record)
    again = ok(client.post(f"{BASE}/records/{rid}/versions/{vid}/reprocess", headers=auth("policy")))
    assert again["processing_status"] == "PROCESSED" and again["chunk_count"] == record["versions"][0]["chunk_count"]
    assert client.post(f"{BASE}/records/{rid}/versions/{vid}/reprocess", headers=auth("analyst")).status_code == 403


# -- listing, search, filters, pagination --------------------------------------------------------------


def test_listing_searches_filters_and_paginates(client, auth):
    tag = uuid.uuid4().hex[:8]
    made = [
        new_source(client, auth("policy"), title=f"Zeta {tag} one", category="SANCTIONS_RESOURCE", jurisdiction="United Kingdom", authority="OFSI"),
        new_source(client, auth("policy"), title=f"Zeta {tag} two", category="REGULATORY_REQUIREMENT", jurisdiction="India", authority="RBI"),
        new_source(client, auth("policy"), title=f"Zeta {tag} three", category="REGULATORY_REQUIREMENT", jurisdiction="India", authority="RBI"),
    ]
    approve_flow(client, auth, made[1])
    policy_headers = auth("policy")

    everything = ok(client.get(f"{BASE}/records?q=Zeta {tag}&page_size=2", headers=policy_headers))
    assert everything["total"] == 3 and len(everything["items"]) == 2 and everything["page"] == 1
    second = ok(client.get(f"{BASE}/records?q=Zeta {tag}&page_size=2&page=2", headers=policy_headers))
    assert len(second["items"]) == 1
    assert not {i["id"] for i in everything["items"]} & {i["id"] for i in second["items"]}

    def total(query):
        return ok(client.get(f"{BASE}/records?q=Zeta {tag}&{query}", headers=policy_headers))["total"]

    assert total("category=SANCTIONS_RESOURCE") == 1
    assert total("jurisdiction=india") == 2
    assert total("authority=RBI") == 2
    assert total("status=APPROVED") == 1
    assert total("status=DRAFT") == 2
    assert total("status=RETIRED") == 0
    assert total("topic=kyc") == 3
    assert ok(client.get(f"{BASE}/records?q={made[0]['source_code']}", headers=policy_headers))["total"] == 1  # search by source ID

    # Ordinary users see only the approved one.
    visible = ok(client.get(f"{BASE}/records?q=Zeta {tag}", headers=auth("analyst")))
    assert visible["total"] == 1 and visible["items"][0]["id"] == made[1]["id"]
    assert "pending_version" in visible["items"][0] and visible["items"][0]["pending_version"] is None
    assert client.get(f"{BASE}/records?page_size=1000", headers=policy_headers).status_code == 422
    assert client.get(f"{BASE}/records?status=BOGUS", headers=policy_headers).status_code == 422


def test_the_summary_counts_reflect_the_library(client, auth):
    record = approve_flow(client, auth, new_source(client, auth("policy")))
    summary = ok(client.get(f"{BASE}/records", headers=auth("policy")))["summary"]
    assert summary["approved"] >= 1 and summary["total"] >= summary["approved"]
    assert set(summary) == {"total", "approved", "in_review", "draft", "retired", "outdated"}
    assert record["current_version"]


def test_an_approved_source_past_its_review_date_is_flagged(client, auth):
    record = new_source(client, auth("policy"), review_date="2020-01-01")
    approved = approve_flow(client, auth, record)
    assert approved["outdated"] is True and approved["current_version"]["outdated"] is True
    outdated = ok(client.get(f"{BASE}/records?status=OUTDATED&q={record['title']}", headers=auth("analyst")))
    assert outdated["total"] == 1


# -- the earlier /api/sources endpoints run through the same workflow ------------------------------------


def test_the_earlier_approve_endpoint_cannot_bypass_review(client, auth):
    body = {"title": f"Legacy path {uuid.uuid4().hex[:6]}", "source_type": "INTERNAL_POLICY", "version": "1", "content": "Customers must be screened.\n\nAlerts are escalated."}
    source = ok(client.post("/api/sources", json=body, headers=auth("admin")), 201)
    for who in ("admin", "policy", "analyst"):
        denied = client.post(f"/api/sources/{source['id']}/approve", json={"reason": "Approve it."}, headers=auth(who))
        assert denied.status_code == 403, who
    assert ok(client.get("/api/sources", headers=auth("admin")))  # still listed...
    assert all(s["id"] != source["id"] or s["status"] == "DRAFT" for s in ok(client.get("/api/sources", headers=auth("admin"))))

    approved = ok(client.post(f"/api/sources/{source['id']}/approve", json={"reason": "Reviewed."}, headers=auth("compliance")))
    assert approved["status"] == "APPROVED" and approved["approved_by"] == "Compliance"

    # It is a governed record too, with the approval in its history.
    listing = ok(client.get(f"{BASE}/records?q={body['title']}", headers=auth("policy")))
    assert listing["total"] == 1 and listing["items"][0]["library_status"] == "APPROVED"
    history = ok(client.get(f"{BASE}/records/{listing['items'][0]['id']}/approvals", headers=auth("policy")))
    assert [row["action"] for row in reversed(history)] == ["SUBMITTED", "APPROVED"]


def test_a_new_library_approval_is_mirrored_for_evidence_attachment(client, auth):
    marker = uuid.uuid4().hex
    record = approve_flow(client, auth, new_source(client, auth("policy"), pdf=unique_pdf(f"Mirror marker {marker} applies to onboarding."), title=f"Mirrored {marker[:6]}"))
    hits = ok(client.get(f"/api/sources/search?q={marker}", headers=auth("analyst")))
    assert hits and hits[0]["source_title"] == record["title"]
    retired = ok(client.post(f"{BASE}/records/{record['id']}/retire", json={"reason": "Withdrawn from use."}, headers=auth("policy")))
    assert retired["status"] == "RETIRED"
    assert ok(client.get(f"/api/sources/search?q={marker}", headers=auth("analyst"))) == []

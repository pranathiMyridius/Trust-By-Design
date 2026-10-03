"""
P1 security (docs/REMAINING_REQUIREMENTS.md):

R15.3  the original file of a CONFIDENTIAL/RESTRICTED document is served
       only to people who may read it unmasked; evidence quotes taken
       from it are masked the same way
R15.5  document views and downloads, and refused attempts, are recorded
R16.4  a soft-deleted assessment can't be opened by id (except by Admins
       and Auditors), can't be changed, and leaves every work queue
"""

import json

import pytest

from app.auth.security import hash_password
from app.database import SessionLocal
from app.models.audit_event import AuditEvent
from app.models.audit_trail import AssessmentRetention
from app.models.risk_factor import RiskFactor
from app.models.user import User
from tests.conftest import PASSWORD, ok

BODY = b"Customer contact: jane.doe@bank.com, card 4111 1111 1111 1111."


def _user(email: str, role: str) -> int:
    db = SessionLocal()
    try:
        user = db.query(User).filter(User.email == email).first()
        if user is None:
            user = User(email=email, hashed_password=hash_password(PASSWORD), full_name=email.split("@")[0].title(), role=role)
            db.add(user)
            db.commit()
        return user.id
    finally:
        db.close()


def _login(client, email: str) -> dict:
    token = ok(client.post("/api/auth/login", json={"email": email, "password": PASSWORD}))["access_token"]
    return {"Authorization": f"Bearer {token}"}


def _events(assessment_id: int | None, action: str, actor_id: int | None = None) -> list[AuditEvent]:
    db = SessionLocal()
    try:
        query = db.query(AuditEvent).filter(AuditEvent.action == action)
        if assessment_id is not None:
            query = query.filter(AuditEvent.assessment_id == assessment_id)
        if actor_id is not None:
            query = query.filter(AuditEvent.actor_id == actor_id)
        return query.all()
    finally:
        db.close()


def _assign_manager(aid: int, manager_id: int) -> None:
    from app.models.assessment import Assessment

    db = SessionLocal()
    try:
        db.get(Assessment, aid).manager_id = manager_id
        db.commit()
    finally:
        db.close()


def _upload(client, auth, aid: int, confidentiality: str, filename: str = "customer.txt") -> dict:
    return ok(
        client.post(
            f"/api/assessments/{aid}/documents",
            files={"file": (filename, BODY)},
            data={"confidentiality": confidentiality},
            headers=auth("owner"),
        )
    )


def _file(client, headers, document_id: int, disposition: str | None = None):
    query = f"?disposition={disposition}" if disposition else ""
    return client.get(f"/api/assessments/documents/{document_id}/file{query}", headers=headers)


@pytest.mark.parametrize("classification", ["CONFIDENTIAL", "RESTRICTED"])
def test_classified_original_only_for_unmasked_readers(client, auth, users, create_assessment, classification):
    aid = create_assessment()["id"]
    _assign_manager(aid, users["manager"])
    doc = _upload(client, auth, aid, classification)

    # Owner and reviewing analyst get the original; the reviewing manager
    # (who sees masked text) does not -- for CONFIDENTIAL too, which used
    # to be served to anyone who could see the assessment.
    assert _file(client, auth("owner"), doc["id"]).content == BODY
    assert _file(client, auth("analyst"), doc["id"]).content == BODY
    refused = _file(client, auth("manager"), doc["id"])
    assert refused.status_code == 403 and classification in refused.json()["detail"]

    # The refusal is recorded (R15.5) without the document's content.
    denied = _events(aid, "ACCESS_DENIED", users["manager"])
    assert denied and all("jane.doe" not in (event.details or "") for event in denied)

    # The document list tells the UI so it can disable the action.
    def listed(who):
        return next(d for d in ok(client.get(f"/api/assessments/{aid}/documents", headers=auth(who))) if d["id"] == doc["id"])

    assert listed("owner")["can_open_original"] is True
    manager_view = listed("manager")
    assert manager_view["can_open_original"] is False and manager_view["is_masked"] is True


def test_unclassified_original_open_to_anyone_who_sees_the_assessment(client, auth, users, create_assessment):
    aid = create_assessment()["id"]
    _assign_manager(aid, users["manager"])
    doc = _upload(client, auth, aid, "INTERNAL")
    assert _file(client, auth("manager"), doc["id"]).status_code == 200
    listed = ok(client.get(f"/api/assessments/{aid}/documents", headers=auth("manager")))
    assert next(d for d in listed if d["id"] == doc["id"])["can_open_original"] is True


def test_views_and_downloads_are_recorded_separately(client, auth, users, create_assessment):
    aid = create_assessment()["id"]
    doc = _upload(client, auth, aid, "INTERNAL")

    viewed = _file(client, auth("analyst"), doc["id"], "inline")
    assert viewed.status_code == 200
    assert viewed.headers["content-disposition"].startswith("inline;")
    assert viewed.headers["content-security-policy"] == "sandbox"

    downloaded = _file(client, auth("analyst"), doc["id"])  # default: attachment
    assert downloaded.headers["content-disposition"].startswith("attachment;")
    assert "content-security-policy" not in downloaded.headers

    views = _events(aid, "DOCUMENT_VIEWED", users["analyst"])
    downloads = _events(aid, "DOCUMENT_DOWNLOADED", users["analyst"])
    assert len(views) == 1 and len(downloads) == 1
    assert f"document {doc['id']}" in downloads[0].details and "INTERNAL" in downloads[0].details

    assert _file(client, auth("analyst"), doc["id"], "evil").status_code == 422


def test_download_header_cannot_be_broken_by_the_filename(client, auth, create_assessment):
    aid = create_assessment()["id"]
    doc = _upload(client, auth, aid, "INTERNAL", filename='rep"orté.txt')
    header = _file(client, auth("owner"), doc["id"]).headers["content-disposition"]
    fallback = header.split('filename="', 1)[1].split('"', 1)[0]
    assert '"' not in fallback and "\\" not in fallback
    assert "filename*=UTF-8''" in header and "%C3%A9" in header


def test_evidence_quotes_from_classified_documents_are_masked(client, auth, users, create_assessment):
    aid = create_assessment()["id"]
    _assign_manager(aid, users["manager"])
    doc = _upload(client, auth, aid, "CONFIDENTIAL")
    quote = "Customer contact: jane.doe@bank.com"

    db = SessionLocal()
    try:
        factor = RiskFactor(
            assessment_id=aid,
            category="CUSTOMER_SEGMENT_RISK",
            applicable=True,
            rationale="Customer data is shared.",
            source="AI",
            version=1,
            is_current=True,
            evidence=json.dumps([{"document_id": doc["id"], "verbatim_quote": quote, "quote_verified": True}]),
        )
        factor.set_indicators([])
        db.add(factor)
        db.commit()
    finally:
        db.close()

    def evidence(who):
        factors = ok(client.get(f"/api/assessments/{aid}/risk-factors", headers=auth(who)))
        return next(f for f in factors if f["category"] == "CUSTOMER_SEGMENT_RISK")["evidence"][0]

    assert evidence("analyst")["verbatim_quote"] == quote
    masked = evidence("manager")
    assert masked["is_masked"] is True and "jane.doe" not in masked["verbatim_quote"]

    # The stored record is untouched.
    db = SessionLocal()
    try:
        stored = db.query(RiskFactor).filter(RiskFactor.assessment_id == aid).first().get_evidence()
        assert stored[0]["verbatim_quote"] == quote
    finally:
        db.close()


def _soft_delete(aid: int) -> None:
    db = SessionLocal()
    try:
        db.add(AssessmentRetention(assessment_id=aid, is_deleted=True, deleted_by="Admin", deletion_reason="Retention expired"))
        db.commit()
    finally:
        db.close()


def test_soft_deleted_assessment_is_gone_except_for_admins_and_auditors(client, auth, users, create_assessment):
    aid = create_assessment()["id"]
    doc = _upload(client, auth, aid, "INTERNAL")
    _soft_delete(aid)

    for who in ("owner", "analyst", "manager"):
        assert client.get(f"/api/assessments/{aid}", headers=auth(who)).status_code == 404
        assert client.get(f"/api/assessments/{aid}/documents", headers=auth(who)).status_code == 404
        assert _file(client, auth(who), doc["id"]).status_code == 404

    _user("p1auditor@test.io", "AUDITOR")
    auditor = _login(client, "p1auditor@test.io")
    assert ok(client.get(f"/api/assessments/{aid}", headers=auditor))["id"] == aid
    assert ok(client.get(f"/api/assessments/{aid}", headers=auth("admin")))["id"] == aid
    ok(client.get(f"/api/assessments/{aid}/audit", headers=auth("admin")))

    # Nobody can change it -- not even an Admin -- and the attempt is logged.
    before = len(_events(aid, "ACCESS_DENIED"))
    change = client.post(
        f"/api/assessments/{aid}/comments",
        json={"comment": "late note", "section": "GENERAL"},
        headers=auth("admin"),
    )
    assert change.status_code == 409 and "deleted" in change.json()["detail"]
    assert len(_events(aid, "ACCESS_DENIED")) == before + 1


def test_soft_deleted_assessment_leaves_work_queues(client, auth, create_assessment):
    aid = create_assessment()["id"]
    queue = ok(client.get("/api/workflow/work-queue", headers=auth("owner")))
    assert any(item["assessment_id"] == aid for item in queue["tasks"])

    _soft_delete(aid)
    queue = ok(client.get("/api/workflow/work-queue", headers=auth("owner")))
    assert all(item["assessment_id"] != aid for item in queue["tasks"])

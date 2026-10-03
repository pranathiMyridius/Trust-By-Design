"""
Stage 19 (Non-Functional Requirements) acceptance checks.

Runs against a throwaway SQLite database, upload folder and backup folder
in a temp directory -- never the DATABASE_URL in backend/.env -- and never
calls a real AI provider. Run from backend/:

    python test_stage19_nfr.py
"""

import json
import os
import tempfile
import time

_TMP = tempfile.mkdtemp()
# Tests never use the real AI provider/key from backend/.env (their HTTP
# calls are faked or absent); pin the offline OpenRouter configuration.
os.environ["LLM_PROVIDER"] = "openrouter"
os.environ["OPENAI_API_KEY"] = ""
os.environ["DATABASE_URL"] = f"sqlite:///{os.path.join(_TMP, 'stage19_test.db')}"
os.environ["WORKFLOW_ESCALATION_INTERVAL_SECONDS"] = "0"
os.environ["BACKUP_INTERVAL_HOURS"] = "0"
os.environ["BACKUP_DIR"] = os.path.join(_TMP, "backups")
os.environ["ADMIN_BOOTSTRAP_PASSWORD"] = "ChangeMe123!"
os.environ["PROCESSING_JOBS_INLINE"] = "true"
os.environ["OPENROUTER_API_KEY"] = "test-key-not-real"
os.environ["AI_MASK_SENSITIVE_DATA"] = "true"

from cryptography.fernet import Fernet  # noqa: E402

os.environ["FILE_ENCRYPTION_KEY"] = Fernet.generate_key().decode()
# Uploaded files are stored under <cwd>/uploaded_files.
os.chdir(_TMP)

from fastapi.testclient import TestClient  # noqa: E402

import app.ai.metering as metering  # noqa: E402
from app.auth.security import hash_password  # noqa: E402
from app.database import SessionLocal  # noqa: E402
from app.main import app, prepare_database  # noqa: E402

# The schema is prepared explicitly; importing the app never migrates.
prepare_database()
from app.models.assessment import Assessment  # noqa: E402
from app.models.audit_event import AuditEvent  # noqa: E402
from app.models.assessment_document import AssessmentDocument  # noqa: E402
from app.models.processing_job import ProcessingJob  # noqa: E402
from app.models.user import User  # noqa: E402
from app.services import backup  # noqa: E402
from app.services.advisory import ensure_advisory_wording  # noqa: E402
from app.services.data_masking import mask_sensitive_text  # noqa: E402
from app.services.data_protection import ProtectedDataError  # noqa: E402
from app.services.processing_jobs import recover_interrupted_jobs  # noqa: E402

client = TestClient(app)
PASSWORD = "Passw0rd!"

REQUEST = {
    "title": "Merchant acquiring in Germany",
    "change_type": "NEW_SERVICE",
    "product_or_service_name": "Merchant Acquiring DE",
    "description": "Card acquiring for German merchants with cross-border settlement to Poland.",
    "evidence": "Merchants in Germany receive cross-border settlement; onboarding is remote.",
    "business_owner": "Merchant Services",
    "legal_entity": "Bank DE GmbH",
    "business_unit": "Merchant Services",
    "customer_segment": "SME merchants",
    "countries_jurisdictions": "Germany, Poland",
    "delivery_channels": "Web portal; API",
    "expected_transaction_volume": "50k/month",
    "expected_transaction_value": "EUR 20m/month",
    "transaction_types": "Card acquiring",
    "third_party_vendor_usage": "Acquiring processor",
    "technology_process_changes": "New acquiring platform",
    "expected_launch_date": "2027-03-01",
}


def _users() -> dict[str, int]:
    db = SessionLocal()
    try:
        def add(email, role, manager_id=None):
            user = User(email=email, hashed_password=hash_password(PASSWORD), full_name=email.split("@")[0].title(),
                        role=role, manager_id=manager_id)
            db.add(user)
            db.flush()
            return user.id

        manager = add("manager@test.io", "MANAGER")
        ids = {
            "manager": manager,
            "owner": add("owner@test.io", "BUSINESS_USER", manager),
            "other": add("other@test.io", "BUSINESS_USER"),
            "analyst": add("analyst@test.io", "FCRM_ANALYST", manager),
        }
        db.commit()
        return ids
    finally:
        db.close()


USERS = _users()
_TOKENS: dict[str, str] = {}


def auth(who: str) -> dict:
    if who not in _TOKENS:
        email = "admin@example.com" if who == "admin" else f"{who}@test.io"
        password = "ChangeMe123!" if who == "admin" else PASSWORD
        response = client.post("/api/auth/login", json={"email": email, "password": password})
        assert response.status_code == 200, response.text
        _TOKENS[who] = response.json()["access_token"]
    return {"Authorization": f"Bearer {_TOKENS[who]}"}


def ok(response, status=200):
    assert response.status_code == status, f"{response.status_code}: {response.text}"
    return response.json()


def _create(**overrides) -> int:
    return ok(client.post("/api/assessments", json={**REQUEST, "is_draft": False, **overrides},
                          headers=auth("owner")), 201)["id"]


def _upload(aid: int, filename: str, content: bytes, who="owner", **form):
    return client.post(
        f"/api/assessments/{aid}/documents",
        files={"file": (filename, content)},
        data=form,
        headers=auth(who),
    )


AID = _create()


# ---------------------------------------------------------------------------
# Security
# ---------------------------------------------------------------------------


def test_every_api_route_requires_authentication():
    open_paths = []
    for path, operations in app.openapi()["paths"].items():
        for method, operation in operations.items():
            if path.startswith("/api") and path != "/api/auth/login" and not operation.get("security"):
                open_paths.append(f"{method.upper()} {path}")
    assert not open_paths, open_paths
    # And behaviourally, on routes that used to be open.
    for path in (
        f"/api/assessments/{AID}/documents",
        f"/api/assessments/{AID}/audit",
        "/api/assessments/audit/all",
        f"/api/assessments/{AID}/explain",
        f"/api/assessments/{AID}/risk-results",
        "/api/risk-methodologies",
        "/api/assessments/check-duplicates",
    ):
        assert client.get(path).status_code == 401, path


def test_least_privilege_blocks_other_users_assessment():
    assert client.get(f"/api/assessments/{AID}/documents", headers=auth("other")).status_code == 403
    assert client.get(f"/api/assessments/{AID}/audit", headers=auth("other")).status_code == 403
    assert _upload(AID, "x.txt", b"hello", who="other").status_code == 403
    assert client.get(f"/api/assessments/{AID}/documents", headers=auth("owner")).status_code == 200


def test_admin_actions_are_logged_without_secrets():
    created = ok(client.post("/api/users", json={
        "email": "new.user@test.io", "password": "S3cretPass!", "role": "BUSINESS_USER",
    }, headers=auth("admin")), 201)
    ok(client.patch(f"/api/users/{created['id']}", json={"role": "MANAGER"}, headers=auth("admin")))

    db = SessionLocal()
    try:
        events = db.query(AuditEvent).filter(AuditEvent.action.in_(
            ["USER_CREATED", "USER_UPDATED", "ADMIN_ACTION"])).all()
    finally:
        db.close()
    actions = [e.action for e in events]
    assert "USER_CREATED" in actions and "USER_UPDATED" in actions and "ADMIN_ACTION" in actions
    assert any("BUSINESS_USER" in e.details and "MANAGER" in e.details for e in events if e.action == "USER_UPDATED")
    assert all("S3cretPass" not in (e.details or "") for e in events)
    assert all(e.actor_id is not None for e in events)


def test_no_secrets_in_code():
    import app.auth.security as security

    source = open(security.__file__, encoding="utf-8").read()
    assert "dev-secret-change-me" not in source
    main_source = open(os.path.join(os.path.dirname(security.__file__), "..", "main.py"), encoding="utf-8").read()
    assert "ChangeMe123!" not in main_source


def test_masking_patterns():
    text = ("Card 4111 1111 1111 1111, IBAN DE89 3704 0044 0532 0130 00, mail jane.doe@bank.com, "
            "call +49 30 1234 5678, SSN 123-45-6789, order 1234567890123 stays")
    masked = mask_sensitive_text(text)
    assert "4111 1111" not in masked and "[CARD ****1111]" in masked
    assert "DE89" not in masked and "[IBAN ****3000]" in masked
    assert "jane.doe" not in masked and "[EMAIL]" in masked
    assert "[PHONE]" in masked and "[NATIONAL ID]" in masked
    # Not a valid card number (fails Luhn) -> left alone.
    assert "1234567890123" in masked


def test_ai_payload_is_masked_before_leaving():
    captured = {}

    class _Resp:
        status_code = 200
        content = b"{}"
        text = "{}"

        def json(self):
            return {"model": "m", "usage": {}, "choices": [{"message": {"content": "{}"}}]}

    def fake_post(url, headers=None, json=None, timeout=None, **_):
        captured["payload"] = json
        return _Resp()

    original = metering.requests.post
    metering.requests.post = fake_post
    try:
        metering.metered_post("TEST", "https://openrouter.ai/api/v1/chat/completions", json={
            "model": "m", "messages": [{"role": "user", "content": "Customer card 4111111111111111 x@y.com"}],
        })
    finally:
        metering.requests.post = original
    content = captured["payload"]["messages"][0]["content"]
    assert "4111111111111111" not in content and "x@y.com" not in content


def test_restricted_document_masked_for_non_reviewers_and_encrypted_at_rest():
    # The manager sees this assessment once it's routed to them for review.
    db = SessionLocal()
    try:
        db.get(Assessment, AID).manager_id = USERS["manager"]
        db.commit()
    finally:
        db.close()

    body = b"Customer contact: jane.doe@bank.com, card 4111 1111 1111 1111."
    doc = ok(_upload(AID, "customer.txt", body, confidentiality="RESTRICTED"))

    # Encrypted on disk: the plaintext never appears in the stored file.
    db = SessionLocal()
    try:
        path = db.get(AssessmentDocument, doc["id"]).file_path
    finally:
        db.close()
    raw = open(path, "rb").read()
    assert raw.startswith(b"RAWENC1:") and b"jane.doe" not in raw

    # Owner and analyst see it raw; the manager (not a reviewing role) masked.
    owner_view = next(d for d in ok(client.get(f"/api/assessments/{AID}/documents", headers=auth("owner")))
                      if d["id"] == doc["id"])
    assert "jane.doe@bank.com" in owner_view["extracted_text"] and not owner_view["is_masked"]
    manager_view = next(d for d in ok(client.get(f"/api/assessments/{AID}/documents", headers=auth("manager")))
                        if d["id"] == doc["id"])
    assert manager_view["is_masked"] and "jane.doe" not in manager_view["extracted_text"]

    # Raw file: decrypted for the owner, refused for the manager.
    file_response = client.get(f"/api/assessments/documents/{doc['id']}/file", headers=auth("owner"))
    assert file_response.status_code == 200 and file_response.content == body
    assert client.get(f"/api/assessments/documents/{doc['id']}/file", headers=auth("manager")).status_code == 403
    assert client.get(f"/api/assessments/documents/{doc['id']}/file").status_code == 401


def test_security_headers():
    response = client.get("/api/auth/me", headers=auth("owner"))
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["x-frame-options"] == "DENY"
    assert response.headers["cache-control"] == "no-store"


# ---------------------------------------------------------------------------
# Performance / Reliability
# ---------------------------------------------------------------------------


def test_upload_processing_reports_progress_and_success():
    doc = ok(_upload(AID, "policy.txt", b"Onboarding policy: enhanced due diligence for merchants."))
    job = doc["processing"]
    assert job["job_type"] == "DOCUMENT_EXTRACTION"
    job = ok(client.get(f"/api/processing-jobs/{job['id']}", headers=auth("owner")))
    assert job["status"] == "SUCCEEDED" and job["progress"] == 100 and not job["is_incomplete"]
    docs = ok(client.get(f"/api/assessments/{AID}/documents", headers=auth("owner")))
    assert "enhanced due diligence" in next(d for d in docs if d["id"] == doc["id"])["extracted_text"]


def test_failed_processing_keeps_data_is_clear_and_retryable():
    doc = ok(_upload(AID, "broken.pdf", b"this is not really a pdf"))
    job = ok(client.get(f"/api/processing-jobs/{doc['processing']['id']}", headers=auth("owner")))
    assert job["status"] == "FAILED" and job["is_retryable"]
    assert "stored safely" in job["error_message"] and job["error_detail"]

    # The document and assessment are still there.
    db = SessionLocal()
    try:
        assert db.get(AssessmentDocument, doc["id"]) is not None
        assert db.get(Assessment, AID) is not None
        failures = db.query(AuditEvent).filter(AuditEvent.action == "PROCESSING_FAILED").count()
    finally:
        db.close()
    assert failures >= 1

    retried = ok(client.post(f"/api/processing-jobs/{job['id']}/retry", headers=auth("owner")), 202)
    after = ok(client.get(f"/api/processing-jobs/{retried['id']}", headers=auth("owner")))
    assert after["attempts"] == 2 and after["status"] == "FAILED"

    # Someone who can't see the assessment can't see or retry its jobs.
    assert client.get(f"/api/processing-jobs/{job['id']}", headers=auth("other")).status_code == 403


def test_partial_results_are_marked_incomplete():
    doc = ok(_upload(AID, "blank.txt", b"   \n   "))
    job = ok(client.get(f"/api/processing-jobs/{doc['processing']['id']}", headers=auth("owner")))
    assert job["status"] == "PARTIAL" and job["is_incomplete"]
    assert any("No readable text" in reason for reason in job["incomplete_reasons"])


def test_background_processing_does_not_block_the_request():
    os.environ["PROCESSING_JOBS_INLINE"] = "false"
    try:
        doc = ok(_upload(AID, "big.txt", b"Large evidence file. " * 20000))
        assert doc["processing"]["status"] in {"QUEUED", "RUNNING", "SUCCEEDED"}
        deadline = time.time() + 30
        while True:
            job = ok(client.get(f"/api/processing-jobs/{doc['processing']['id']}", headers=auth("owner")))
            if job["is_finished"] or time.time() > deadline:
                break
            time.sleep(0.2)
        assert job["status"] == "SUCCEEDED", job
    finally:
        os.environ["PROCESSING_JOBS_INLINE"] = "true"


def test_interrupted_jobs_become_retryable_on_restart():
    db = SessionLocal()
    try:
        job = ProcessingJob(job_type="DOCUMENT_EXTRACTION", status="RUNNING", assessment_id=AID,
                            progress=40, attempts=1, max_attempts=5)
        db.add(job)
        db.commit()
        job_id = job.id
    finally:
        db.close()
    recover_interrupted_jobs()
    job = ok(client.get(f"/api/processing-jobs/{job_id}", headers=auth("owner")))
    assert job["status"] == "FAILED" and job["is_retryable"] and "restarted" in job["error_message"]


def test_upload_size_limit():
    import app.api.assessments as assessments_api

    original = assessments_api.MAX_UPLOAD_BYTES
    assessments_api.MAX_UPLOAD_BYTES = 10
    try:
        response = _upload(AID, "big.txt", b"x" * 100)
    finally:
        assessments_api.MAX_UPLOAD_BYTES = original
    assert response.status_code == 413 and "upload limit" in response.json()["detail"]


def test_response_timing_and_performance_report():
    response = client.get("/api/assessments", headers=auth("owner"))
    assert "app;dur=" in response.headers["server-timing"]
    report = ok(client.get("/api/system/performance", headers=auth("admin")))
    route = next(r for r in report["routes"] if r["route"] == "/api/assessments" and r["method"] == "GET")
    assert route["category"] == "PAGE" and route["target_ms"] == 2000 and route["samples"] >= 1
    assert client.get("/api/system/performance", headers=auth("owner")).status_code == 403


def test_async_analysis_returns_job_immediately():
    aid = _create(title="Async analysis")
    job = ok(client.post(f"/api/assessments/{aid}/analyze-async", headers=auth("analyst")), 202)
    assert job["job_type"] == "RISK_ANALYSIS" and job["assessment_id"] == aid
    assert client.post(f"/api/assessments/{aid}/analyze-async", headers=auth("owner")).status_code == 403


# ---------------------------------------------------------------------------
# Availability and recovery
# ---------------------------------------------------------------------------


def test_backup_verify_and_recovery_status():
    created = ok(client.post("/api/system/backups", headers=auth("admin")), 201)
    assert created["valid_manifest"] and created["uploaded_file_count"] >= 1
    verified = ok(client.post(f"/api/system/backups/{created['name']}/verify", headers=auth("admin")))
    assert verified["valid"], verified

    status = ok(client.get("/api/system/recovery-status", headers=auth("admin")))
    assert status["rpo_met"] and status["recovery_point_objective_hours"] == 24
    assert status["recovery_time_objective_hours"] == 4

    # Corruption is detected.
    path = backup.backup_path(created["name"])
    # The key is set above, so the database copy is stored encrypted.
    database_file = json.loads((path / "manifest.json").read_text())["database_file"]
    assert created["encrypted"] and database_file == "database.sqlite.enc"
    assert (path / database_file).read_bytes().startswith(b"RAWENC1:")
    with open(path / database_file, "ab") as f:
        f.write(b"corruption")
    broken = ok(client.post(f"/api/system/backups/{created['name']}/verify", headers=auth("admin")))
    assert not broken["valid"] and any("Checksum mismatch" in p for p in broken["problems"])

    assert client.post(f"/api/system/backups/../../etc/verify", headers=auth("admin")).status_code in (400, 404)
    assert client.get("/api/system/backups", headers=auth("owner")).status_code == 403
    assert ok(client.get("/api/system/integrity", headers=auth("admin")))["database_check"] == "ok"


def test_accidental_deletion_and_audit_tampering_are_blocked():
    db = SessionLocal()
    try:
        assessment = db.get(Assessment, AID)
        db.delete(assessment)
        try:
            db.flush()
            raise AssertionError("hard delete of an assessment was allowed")
        except ProtectedDataError:
            db.rollback()

        try:
            db.query(AuditEvent).filter(AuditEvent.assessment_id == AID).delete()
            raise AssertionError("bulk delete of audit events was allowed")
        except ProtectedDataError:
            db.rollback()

        event = db.query(AuditEvent).first()
        event.details = "rewritten history"
        try:
            db.flush()
            raise AssertionError("audit event modification was allowed")
        except ProtectedDataError:
            db.rollback()
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Scalability
# ---------------------------------------------------------------------------


def test_business_units_legal_entities_filters_and_paging():
    _create(title="UK payments", legal_entity="Bank UK Ltd", business_unit="Payments")
    _create(title="UK cards", legal_entity="Bank UK Ltd", business_unit="Cards")

    uk = ok(client.get("/api/assessments?legal_entity=Bank UK Ltd", headers=auth("owner")))
    assert {a["title"] for a in uk} == {"UK payments", "UK cards"}
    cards = ok(client.get("/api/assessments?business_unit=Cards", headers=auth("owner")))
    assert [a["title"] for a in cards] == ["UK cards"]

    page = client.get("/api/assessments?limit=1&offset=0", headers=auth("owner"))
    assert len(page.json()) == 1 and int(page.headers["x-total-count"]) >= 3

    units = ok(client.get("/api/assessments/org-units", headers=auth("owner")))
    assert {"name": "Bank UK Ltd", "assessment_count": 2} in units["legal_entities"]
    assert any(u["name"] == "Cards" for u in units["business_units"])


# ---------------------------------------------------------------------------
# Explainability
# ---------------------------------------------------------------------------


def test_recommendations_never_read_as_decisions():
    assert ensure_advisory_wording("Approved. Residual risk is low.") == \
        "Suggested outcome: Approve. Residual risk is low."
    assert ensure_advisory_wording("Decision: Reject - too risky") == "Suggested outcome: Reject. too risky"
    assert ensure_advisory_wording("Suggested outcome: Escalate.") == "Suggested outcome: Escalate."


def test_statements_distinguish_facts_assumptions_recommendations_decisions():
    result = ok(client.get(f"/api/assessments/{AID}/explain/statements", headers=auth("owner")))
    assert "not an approval or a rejection" in result["advisory_notice"]
    kinds = {s["kind"] for s in result["statements"]}
    assert "FACT" in kinds
    assert set(result["counts"]) == {"FACT", "ASSUMPTION", "RECOMMENDATION", "DECISION"}
    for statement in result["statements"]:
        assert statement["origin"] in {"HUMAN", "AUTOMATED"}
        assert statement["reference"] and statement["reference"]["id"] is not None
        # Automated output is never presented as a decision.
        if statement["origin"] == "AUTOMATED":
            assert statement["kind"] != "DECISION"
            assert statement["review_status"] in {"REVIEWED", "PENDING_REVIEW"}
    assert not result["final_decision_recorded"]


# ---------------------------------------------------------------------------
# Standalone Risk Calculator: values persist per user between visits
# ---------------------------------------------------------------------------


def test_standalone_calculator_values_persist_per_user():
    assert ok(client.get("/api/risk-calculator/draft", headers=auth("analyst")))["draft"] is None

    payload = {
        "scores": {"GEOGRAPHIC": 80, "CUSTOMER": 55},
        "included": {"GEOGRAPHIC": True, "CUSTOMER": False},
        "weights": {"GEOGRAPHIC": 0.6, "CUSTOMER": 0.4},
    }
    ok(client.put("/api/risk-calculator/draft", json=payload, headers=auth("analyst")))
    assert ok(client.get("/api/risk-calculator/draft", headers=auth("analyst")))["draft"] == payload

    # Updating replaces the saved values.
    payload["scores"]["GEOGRAPHIC"] = 20
    ok(client.put("/api/risk-calculator/draft", json=payload, headers=auth("analyst")))
    assert ok(client.get("/api/risk-calculator/draft", headers=auth("analyst")))["draft"]["scores"]["GEOGRAPHIC"] == 20

    # Each user has their own saved values; anonymous access is refused.
    assert ok(client.get("/api/risk-calculator/draft", headers=auth("owner")))["draft"] is None
    assert client.get("/api/risk-calculator/draft").status_code == 401


if __name__ == "__main__":
    tests = [value for name, value in list(globals().items()) if name.startswith("test_") and callable(value)]
    failures = 0
    for test in tests:
        try:
            test()
            print(f"PASS  {test.__name__}")
        except Exception as exc:  # noqa: BLE001
            failures += 1
            import traceback

            print(f"FAIL  {test.__name__}: {exc!r}")
            traceback.print_exc()
    print(f"\n{len(tests) - failures}/{len(tests)} passed")
    raise SystemExit(1 if failures else 0)

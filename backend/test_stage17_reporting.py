"""
Stage 17 (Reporting and Monitoring) acceptance checks.

Runs against a throwaway SQLite database -- never the DATABASE_URL in
backend/.env -- and never calls OpenRouter: the HTTP layer under
app/ai/metering.py is replaced with a fake that returns an
OpenRouter-shaped response, so the real analyzer parsing, metering and
AI-evaluation snapshot all run end to end. Run from backend/:

    python test_stage17_reporting.py
"""

import json
import os
import tempfile
from datetime import date, datetime, timedelta, timezone

_DB_FILE = os.path.join(tempfile.mkdtemp(), "stage17_test.db")
# Tests never use the real AI provider/key from backend/.env (their HTTP
# calls are faked or absent); pin the offline OpenRouter configuration.
os.environ["LLM_PROVIDER"] = "openrouter"
os.environ["OPENAI_API_KEY"] = ""
os.environ["DATABASE_URL"] = f"sqlite:///{_DB_FILE}"
os.environ["WORKFLOW_ESCALATION_INTERVAL_SECONDS"] = "0"
# Stage 19: the bootstrap admin password is no longer hard-coded.
os.environ["ADMIN_BOOTSTRAP_PASSWORD"] = "ChangeMe123!"
os.environ["BACKUP_INTERVAL_HOURS"] = "0"
os.environ["PROCESSING_JOBS_INLINE"] = "true"
os.environ["OPENROUTER_API_KEY"] = "test-key-not-real"

import requests  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

import app.ai.metering as metering  # noqa: E402
from app.auth.security import hash_password  # noqa: E402
from app.database import SessionLocal  # noqa: E402
from app.main import app, prepare_database  # noqa: E402

# The schema is prepared explicitly; importing the app never migrates.
prepare_database()
from app.models.action_item import ActionItem  # noqa: E402
from app.models.ai_metrics import AIEvaluationRecord, AIUsageLog  # noqa: E402
from app.models.assessment import Assessment  # noqa: E402
from app.models.control import ControlGap  # noqa: E402
from app.models.risk_factor import RiskFactor  # noqa: E402
from app.models.user import User  # noqa: E402

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

FAKE_MODEL = "test-vendor/test-model-1"


def _fake_ai_response(url, headers=None, json=None, timeout=None, **_):
    factors = [
        {
            "category": "GEOGRAPHIC_RISK",
            "applicable": True,
            "indicators": ["CROSS_BORDER_CAPABILITY"],
            # Grounded: every content word appears in the evidence.
            "rationale": "Cross-border settlement between Germany and Poland for merchants.",
            "score": 70,
            "severity": "HIGH",
        },
        {
            "category": "THIRD_PARTY_VENDOR_RISK",
            "applicable": True,
            "indicators": [],
            # Ungrounded: nothing here appears in the evidence.
            "rationale": "Cryptocurrency mixers facilitate darknet laundering typologies.",
            "score": 50,
            "severity": "MEDIUM",
        },
    ] + [
        # The model assesses all ten categories; these eight don't apply.
        {"category": category, "applicable": False, "rationale": f"{category} does not apply."}
        for category in [
            "PRODUCT_SERVICE_RISK", "CUSTOMER_SEGMENT_RISK", "DELIVERY_CHANNEL_RISK",
            "TRANSACTION_ACTIVITY_RISK", "TECHNOLOGY_DEVELOPMENT_RISK",
            "OWNERSHIP_ENTITY_COMPLEXITY_RISK", "FINANCIAL_CRIME_TYPOLOGY_RISK",
            "CONTROL_ENVIRONMENT_RISK",
        ]
    ]
    body = {
        "model": FAKE_MODEL,
        "choices": [{"message": {"content": __import__("json").dumps({"factors": factors})}}],
        "usage": {"prompt_tokens": 1000, "completion_tokens": 200, "total_tokens": 1200, "cost": 0.0042},
    }
    response = requests.Response()
    response.status_code = 200
    response._content = __import__("json").dumps(body).encode()
    return response


metering.requests.post = _fake_ai_response


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
            "analyst": add("analyst@test.io", "FCRM_ANALYST", manager),
            "committee": add("committee@test.io", "COMMITTEE_MEMBER"),
        }
        db.commit()
        return ids
    finally:
        db.close()


USERS = _users()
_TOKENS: dict[str, str] = {}


def auth(who: str) -> dict:
    if who not in _TOKENS:
        email, password = (
            ("admin@example.com", "ChangeMe123!") if who == "admin" else (f"{who}@test.io", PASSWORD)
        )
        response = client.post("/api/auth/login", json={"email": email, "password": password})
        assert response.status_code == 200, response.text
        _TOKENS[who] = response.json()["access_token"]
    return {"Authorization": f"Bearer {_TOKENS[who]}"}


def ok(response, status=200):
    assert response.status_code == status, f"{response.status_code}: {response.text}"
    return response.json()


def _analysed_assessment() -> int:
    """Create -> submit -> intake -> confirm profile -> AI risk identification."""
    created = ok(client.post("/api/assessments", json={**REQUEST, "is_draft": False}, headers=auth("owner")), 201)
    aid = created["id"]
    ok(client.patch(f"/api/assessments/{aid}/advance-stage", json={}, headers=auth("owner")))
    # First attempt creates the structured profile, then asks for confirmation.
    client.patch(f"/api/assessments/{aid}/advance-stage", json={}, headers=auth("analyst"))
    ok(client.post(f"/api/assessments/{aid}/intelligence/confirm", json={"confirmed_by": "Owner"}, headers=auth("owner")))
    moved = ok(client.patch(f"/api/assessments/{aid}/advance-stage", json={}, headers=auth("analyst")))
    assert moved["status"] == "RISK_IDENTIFICATION", moved
    return aid


AID = _analysed_assessment()


def _factor(aid: int, category: str) -> RiskFactor:
    db = SessionLocal()
    try:
        return (
            db.query(RiskFactor)
            .filter(RiskFactor.assessment_id == aid, RiskFactor.category == category, RiskFactor.is_current.is_(True))
            .first()
        )
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Acceptance 4: AI-generated assessments store the evaluation metrics.
# ---------------------------------------------------------------------------


def test_ai_run_is_metered_and_snapshotted():
    db = SessionLocal()
    try:
        logs = db.query(AIUsageLog).filter(AIUsageLog.assessment_id == AID).all()
        assert len(logs) == 1, [(l.purpose, l.assessment_id) for l in logs]
        log = logs[0]
        assert log.purpose == "RISK_FACTOR_IDENTIFICATION" and log.success
        assert log.response_model == FAKE_MODEL
        assert (log.prompt_tokens, log.completion_tokens, log.total_tokens) == (1000, 200, 1200)
        assert log.cost_usd == 0.0042 and log.cost_source == "REPORTED"

        record = db.query(AIEvaluationRecord).filter(AIEvaluationRecord.assessment_id == AID).one()
        assert record.is_current and record.ai_available and not record.backfilled
        assert record.model == FAKE_MODEL
        assert record.processing_ms is not None
        assert record.ai_applicable_count == 2 and record.ai_factor_count == 10
        # The model's scores (70, 50) are discarded: there is no AI overall.
        assert record.ai_overall_score is None and record.ai_risk_band is None
        snapshot = {f["category"]: f for f in record.get_ai_factors()}
        assert snapshot["GEOGRAPHIC_RISK"]["score"] == 0.0
        # No quotes were given, so the evidence is insufficient.
        assert snapshot["GEOGRAPHIC_RISK"]["evidence_status"] == "INSUFFICIENT_EVIDENCE"
        assessment = db.get(Assessment, AID)
    finally:
        db.close()

    # AI factors start unrated, so there is no overall score until an
    # analyst rates them.
    geo = _factor(AID, "GEOGRAPHIC_RISK")
    assert geo.score == 0.0 and geo.likelihood is None and geo.source == "AI"
    assert _factor(AID, "PRODUCT_SERVICE_RISK").score == 0.0  # not applicable -> no score
    assert assessment.overall_score is None and assessment.risk_level is None


def test_ai_evaluation_metrics_after_human_review():
    geo = _factor(AID, "GEOGRAPHIC_RISK")
    vendor = _factor(AID, "THIRD_PARTY_VENDOR_RISK")

    # Human review: rate the geographic risk HIGH (4x4 -> 64), reject the
    # vendor risk, add a customer-segment risk the AI missed, and record
    # an explicit override of an AI value.
    ok(client.patch(f"/api/assessments/{AID}/risk-factors/{geo.id}/rating",
                    json={"likelihood": 4, "impact": 4, "rated_by": "Analyst"}, headers=auth("analyst")))
    ok(client.patch(f"/api/assessments/{AID}/risk-factors/{vendor.id}/exclude",
                    json={"reason": "Processor is a regulated bank; not a crypto exposure.", "excluded_by": "Analyst"},
                    headers=auth("analyst")))
    ok(client.post(f"/api/assessments/{AID}/risk-factors",
                   json={"category": "CUSTOMER_SEGMENT_RISK", "rationale": "SME merchants onboarded remotely.",
                         "added_by": "Analyst"}, headers=auth("analyst")), 201)
    # The override names the record and field it changes; the original
    # value is read from that record (a client-sent ai_value is ignored).
    proposed = ok(client.post(f"/api/assessments/{AID}/overrides",
                   json={"section": "RISK_RATIONALE", "field_name": "rationale", "entity_id": str(geo.id),
                         "ai_value": "Cross-border settlement", "human_value": "Cross-border settlement incl. PL sanctions nexus",
                         "reason": "AI missed the sanctions angle."}, headers=auth("analyst")), 201)
    assert proposed["ai_value"] == geo.rationale and proposed["ai_value_source"] == "SYSTEM"
    assert proposed["review_status"] == "PROPOSED"

    report = ok(client.get("/api/reports/ai-evaluation", headers=auth("analyst")))
    m = report["metrics"]
    # 10 categories, less the 3 the fixed Stage 4 rules made applicable
    # (product, transaction, delivery channel) that no one has rated or
    # excluded yet -- they have no human outcome to compare (P4). Of the 7:
    # GEO agree, VENDOR (AI yes / human no) disagree, CUSTOMER (AI no /
    # human yes) disagree, 4 others agree.
    assert m["categories_compared"] == 7 and m["ai_human_agreement"] == round(5 / 7, 4), m
    # The AI no longer assigns factor severities, so there is nothing to
    # compare the human's factor bands against.
    assert m["factor_band_agreement"] is None, m
    assert m["missing_risk_rate"] == 0.5, m  # 1 added / (2 AI - 1 excluded + 1 added)
    assert m["unsupported_content_rate"] == 0.5, m  # 1 of 2 AI risks excluded
    assert m["evidence_groundedness"] == 0.5, m  # GEO grounded, VENDOR not
    assert m["human_override_rate"] == 1.0 and m["human_overrides_total"] >= 1, m
    assert m["avg_processing_ms"] is not None

    assert report["summary"]["total_cost_usd"] == 0.0042
    by_model = {row["key"]: row for row in report["usage_by_model"]}
    assert by_model[FAKE_MODEL]["total_tokens"] == 1200 and by_model[FAKE_MODEL]["cost_complete"]
    purposes = {row["key"] for row in report["usage_by_purpose"]}
    assert "RISK_FACTOR_IDENTIFICATION" in purposes


# ---------------------------------------------------------------------------
# Acceptance 3: overrides report original and final values with reasons.
# ---------------------------------------------------------------------------


def test_governance_reports_overrides_with_original_final_and_reason():
    report = ok(client.get("/api/reports/governance", headers=auth("analyst")))
    overrides = {o["kind"]: o for o in report["human_overrides"] if o["assessment_id"] == AID}

    explicit = overrides["AI_VALUE_OVERRIDE"]
    assert explicit["original_value"] == _factor(AID, "GEOGRAPHIC_RISK").rationale
    assert explicit["review_status"] == "PROPOSED"
    assert explicit["final_value"] == "Cross-border settlement incl. PL sanctions nexus"
    assert explicit["reason"] == "AI missed the sanctions angle."

    excluded = overrides["AI_RISK_EXCLUDED"]
    assert excluded["original_value"].startswith("Applicable") and excluded["final_value"] == "Excluded"
    assert "regulated bank" in excluded["reason"]


def test_methodology_changes_are_reported_with_actor():
    ok(client.post("/api/risk-methodologies",
                   json={"name": "2026 recalibration", "weights": {"GEOGRAPHIC_RISK": 1.0},
                         "thresholds": {"LOW": 0, "MEDIUM": 40, "HIGH": 60}},
                   headers=auth("admin")), 201)
    # Unauthenticated changes are no longer possible.
    assert client.post("/api/risk-methodologies", json={"name": "x", "weights": {}, "thresholds": {}}).status_code == 401

    changes = ok(client.get("/api/reports/governance", headers=auth("admin")))["policy_changes"]
    change = next(c for c in changes if "2026 recalibration" in (c["details"] or ""))
    assert change["kind"] == "METHODOLOGY" and change["by"] == "Default Admin"


# ---------------------------------------------------------------------------
# Acceptance 2: high-risk portfolio shows products, geographies, risks,
# controls and open actions.
# ---------------------------------------------------------------------------


def test_high_risk_portfolio():
    geo = _factor(AID, "GEOGRAPHIC_RISK")
    ok(client.post(f"/api/assessments/{AID}/controls",
                   json={"risk_factor_id": geo.id, "control_type": "SANCTIONS_SCREENING", "owner": "FCC Ops"},
                   headers=auth("admin")), 201)

    db = SessionLocal()
    assessment = db.get(Assessment, AID)
    assessment.residual_score = 72.0
    assessment.residual_risk_level = "HIGH"
    db.add(ActionItem(assessment_id=AID, source_type="CONTROL_GAP", title="Implement PL sanctions list",
                      owner="FCC Ops", priority="HIGH", due_date=date.today() - timedelta(days=1), created_by="test"))
    db.add(ControlGap(assessment_id=AID, risk_factor_id=geo.id, gap_type="NO_EVIDENCE",
                      description="No screening evidence yet."))
    db.commit()
    db.close()

    portfolio = ok(client.get("/api/reports/high-risk-portfolio", headers=auth("analyst")))
    item = next(i for i in portfolio["assessments"] if i["assessment_id"] == AID)
    assert item["product"] == "Merchant Acquiring DE"
    assert item["geographies"] == ["Germany", "Poland"]
    assert any(r["category"] == "GEOGRAPHIC_RISK" and r["band"] == "HIGH" for r in item["risks"])
    assert [c["control_type"] for c in item["controls"]] == ["SANCTIONS_SCREENING"]
    assert any(a["title"] == "Implement PL sanctions list" and a["overdue"] for a in item["open_actions"])
    assert item["control_gaps"]
    assert portfolio["summary"]["overdue_actions"] >= 1

    risk = ok(client.get("/api/reports/risk", headers=auth("analyst")))
    assert {row["label"] for row in risk["by_geography"]} >= {"Germany", "Poland"}
    assert risk["summary"]["above_tolerance"] >= 1  # 72 > default tolerance 60
    assert any(g["gap_type"] == "NO_EVIDENCE" for g in risk["control_gaps"])


# ---------------------------------------------------------------------------
# Acceptance 1: date-range reports for authorized users.
# ---------------------------------------------------------------------------


def test_date_range_and_authorization():
    old = ok(client.post("/api/assessments", json={**REQUEST, "title": "Legacy product", "is_draft": False},
                         headers=auth("owner")), 201)
    db = SessionLocal()
    legacy = db.get(Assessment, old["id"])
    legacy.created_at = datetime(2025, 3, 10, tzinfo=timezone.utc)
    legacy.committee_decision = "DEFERRED"
    legacy.committee_decided_at = datetime(2025, 4, 1, tzinfo=timezone.utc)
    legacy.committee_rationale = "Await vendor audit."
    db.commit()
    db.close()

    in_2025 = ok(client.get("/api/reports/operational?date_from=2025-01-01&date_to=2025-12-31", headers=auth("analyst")))
    assert in_2025["summary"]["total_assessments"] == 1
    assert in_2025["period"] == {"date_from": "2025-01-01", "date_to": "2025-12-31"}
    assert [r["outcome"] for r in in_2025["deferred_and_rejected"]] == ["DEFERRED"]

    this_year = ok(client.get(f"/api/reports/operational?date_from={date.today().year}-01-01", headers=auth("analyst")))
    assert old["id"] not in {r["assessment_id"] for r in this_year["deferred_and_rejected"]}
    assert this_year["summary"]["total_assessments"] == 1

    everything = ok(client.get("/api/reports/operational", headers=auth("analyst")))
    assert everything["summary"]["total_assessments"] == 2
    assert {row["label"] for row in everything["by_business_unit"]} == {"Bank DE GmbH"}

    assert client.get("/api/reports/operational?date_from=2026-02-01&date_to=2026-01-01",
                      headers=auth("analyst")).status_code == 422

    # Business users have no reporting access; committee members get the
    # business reports but not AI evaluation.
    assert client.get("/api/reports/operational", headers=auth("owner")).status_code == 403
    assert client.get("/api/reports/ai-evaluation", headers=auth("committee")).status_code == 403
    assert client.get("/api/reports/governance", headers=auth("committee")).status_code == 200


def test_failed_ai_call_is_metered_and_marked_unavailable():
    def failing(*_, **__):
        raise requests.ConnectionError("network down")

    metering.requests.post = failing
    try:
        created = ok(client.post("/api/assessments", json={**REQUEST, "title": "Offline run", "is_draft": False},
                                 headers=auth("owner")), 201)
        aid = created["id"]
        # R3.3: analysis needs the owner-confirmed profile first.
        ok(client.patch(f"/api/assessments/{aid}/advance-stage", json={}, headers=auth("owner")))
        client.patch(f"/api/assessments/{aid}/advance-stage", json={}, headers=auth("analyst"))
        ok(client.post(f"/api/assessments/{aid}/intelligence/confirm", json={}, headers=auth("owner")))
        ok(client.post(f"/api/assessments/{aid}/analyze", headers=auth("analyst")))
    finally:
        metering.requests.post = _fake_ai_response

    db = SessionLocal()
    try:
        # Every attempt is metered, including retries: each one is a
        # real outbound request whose latency and failure should be
        # visible, so there is one failed row per attempt.
        from app.ai.risk_factor_analyzer import OPENROUTER_MAX_ATTEMPTS

        logs = db.query(AIUsageLog).filter(AIUsageLog.assessment_id == aid).all()
        assert len(logs) == OPENROUTER_MAX_ATTEMPTS, len(logs)
        assert all(not log.success and "network down" in log.error for log in logs)
        record = db.query(AIEvaluationRecord).filter(AIEvaluationRecord.assessment_id == aid).one()
        assert record.ai_available is False
    finally:
        db.close()

    report = ok(client.get("/api/reports/ai-evaluation", headers=auth("admin")))
    assert report["summary"]["ai_call_failures"] >= 1
    assert report["metrics"]["ai_availability"] < 1
    # A failed call costs nothing, so it doesn't make the cost total incomplete.
    assert report["summary"]["cost_complete"] is True


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

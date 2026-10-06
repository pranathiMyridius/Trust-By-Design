"""
Shared pytest setup for backend/tests.

Everything here runs before the app is imported, because app.database
reads DATABASE_URL at import time. The suite therefore always runs
against a throwaway SQLite file -- never backend/risk.db or whatever
DATABASE_URL points at in backend/.env.

Deterministic tests (unit/, api/, workflow/) never reach the network:
the autouse `fake_llm` fixture replaces requests.post inside
app/ai/metering.py, the single seam every AI call goes through. Only
tests marked `live_llm` (tests/evals) are allowed to talk to a real model.
"""

from __future__ import annotations

import os
import tempfile

_TMP_DIR = tempfile.mkdtemp(prefix="raw-pytest-")

os.environ["DATABASE_URL"] = f"sqlite:///{os.path.join(_TMP_DIR, 'test.db')}"
os.environ["WORKFLOW_ESCALATION_INTERVAL_SECONDS"] = "0"
os.environ["BACKUP_INTERVAL_HOURS"] = "0"
os.environ["BACKUP_DIR"] = os.path.join(_TMP_DIR, "backups")
os.environ["PROCESSING_JOBS_INLINE"] = "true"
# Never send real email from the suite, whatever backend/.env says. Tests of
# the email feature switch it on themselves and replace the sender.
if os.environ.get("LIVE_EMAIL_TEST") != "1":  # tests/api/test_live_email_flow.py opts in
    os.environ["NOTIFY_EMAIL_ENABLED"] = "false"
    os.environ["SMTP_PASSWORD"] = ""
os.environ["ADMIN_BOOTSTRAP_EMAIL"] = "admin@example.com"
os.environ["ADMIN_BOOTSTRAP_PASSWORD"] = "Adm1n-Test-Only!"
# Deterministic tests never use the real AI provider or key from
# backend/.env: every call is faked at app/ai/metering.py, and the
# provider is pinned to the offline OpenRouter configuration. Live
# evaluations (tests/evals) opt in with LIVE_LLM=1 to use backend/.env.
if not os.getenv("LIVE_LLM"):
    os.environ["LLM_PROVIDER"] = "openrouter"
    os.environ["OPENAI_API_KEY"] = ""
# One attempt, no sleeping: keeps failure-path tests fast.
os.environ["OPENROUTER_MAX_ATTEMPTS"] = "1"
os.environ["OPENROUTER_BACKOFF_SECONDS"] = "0"
os.environ.setdefault("ENABLE_RULES_ONLY_FALLBACK", "true")
# Tracing is off for deterministic tests; tests/evals turns it back on
# (it still needs LANGFUSE_* keys to actually send anything).
os.environ["LANGFUSE_ENABLED"] = "false"
os.environ.setdefault("DEEPEVAL_TELEMETRY_OPT_OUT", "YES")

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

import app.ai.likelihood_impact_analyzer as likelihood_impact_analyzer  # noqa: E402
import app.ai.metering as metering  # noqa: E402
import app.ai.risk_factor_analyzer as risk_factor_analyzer  # noqa: E402
from app.auth.security import hash_password  # noqa: E402
from app.database import SessionLocal  # noqa: E402
from app.main import app, prepare_database  # noqa: E402

# The schema is prepared explicitly; importing the app never migrates.
prepare_database()
from app.models.user import User  # noqa: E402

from tests.support.fake_llm import FakeLLM  # noqa: E402

PASSWORD = "Passw0rd-Test-Only!"
ADMIN_PASSWORD = os.environ["ADMIN_BOOTSTRAP_PASSWORD"]

# A complete, valid intake request (every MANDATORY_INTAKE_FIELD set).
FULL_REQUEST = {
    "title": "Cross-border merchant acquiring in Germany",
    "change_type": "NEW_GEOGRAPHY",
    "product_or_service_name": "Merchant Acquiring DE",
    "description": (
        "Card acquiring for German merchants with cross-border settlement "
        "to Poland, delivered through an external acquiring processor."
    ),
    "evidence": (
        "Merchants receive cross-border settlement; onboarding is remote "
        "and customer data is shared with the processor."
    ),
    "business_owner": "Merchant Services",
    "legal_entity": "Bank DE GmbH",
    "customer_segment": "SME merchants",
    "countries_jurisdictions": "Germany, Poland",
    "delivery_channels": "Web portal; API",
    "expected_transaction_volume": "50k/month",
    "expected_transaction_value": "EUR 20m/month",
    "transaction_types": "Card acquiring",
    "third_party_vendor_usage": "External acquiring processor",
    "technology_process_changes": "New acquiring platform",
    "expected_launch_date": "2027-03-01",
}

_USER_ROLES = {
    "manager": "MANAGER",
    "owner": "BUSINESS_USER",
    "other_owner": "BUSINESS_USER",
    "analyst": "FCRM_ANALYST",
    "committee": "COMMITTEE_MEMBER",
    # P3 governance designations (provisional role matrix).
    "senior": "FCRM_ANALYST",
    "head": "MANAGER",
    "governance": "MANAGER",
    "chair": "COMMITTEE_MEMBER",
    # G-5 quorum (2026-10-03): the two representative seats.
    "fcrm_rep": "COMMITTEE_MEMBER",
    "business_rep": "COMMITTEE_MEMBER",
    # Source Library: authorised compliance reviewers (two, so one can
    # decide what the other cannot), a maintainer, and a read-only auditor.
    "compliance": "MANAGER",
    "compliance2": "MANAGER",
    "policy": "POLICY_ADMIN",
    "auditor": "AUDITOR",
}

_DESIGNATIONS = {
    "senior": ["SENIOR_ANALYST", "QA_REVIEWER", "CHALLENGE_REVIEWER"],
    "head": ["HEAD_OF_FCRM"],
    "governance": ["FCRM_GOVERNANCE_OWNER"],
    "chair": ["COMMITTEE_CHAIR"],
    "fcrm_rep": ["COMMITTEE_FCRM_COMPLIANCE_REP"],
    "business_rep": ["COMMITTEE_BUSINESS_RISK_REP"],
    "compliance": ["COMPLIANCE_MANAGER"],
    "compliance2": ["FCRM_GOVERNANCE_OWNER"],
}


def pytest_configure(config):
    config.addinivalue_line("markers", "live_llm: calls a real LLM provider (tests/evals only)")


@pytest.fixture(scope="session")
def client() -> TestClient:
    return TestClient(app)


@pytest.fixture(scope="session")
def users() -> dict[str, int]:
    db = SessionLocal()
    try:
        manager = db.query(User).filter(User.email == "manager@test.io").first()
        if manager is None:
            manager = User(
                email="manager@test.io",
                hashed_password=hash_password(PASSWORD),
                full_name="Manager",
                role="MANAGER",
            )
            db.add(manager)
            db.flush()
        ids = {"manager": manager.id}
        for key, role in _USER_ROLES.items():
            if key == "manager":
                continue
            email = f"{key}@test.io"
            user = db.query(User).filter(User.email == email).first()
            if user is None:
                user = User(
                    email=email,
                    hashed_password=hash_password(PASSWORD),
                    full_name=key.replace("_", " ").title(),
                    role=role,
                    manager_id=manager.id,
                )
                db.add(user)
                db.flush()
            user.set_designations(_DESIGNATIONS.get(key))
            ids[key] = user.id
        db.commit()
        return ids
    finally:
        db.close()


@pytest.fixture(scope="session")
def auth(client, users):
    """auth("analyst") -> Authorization header for that test user."""

    tokens: dict[str, str] = {}

    def headers(who: str) -> dict[str, str]:
        if who not in tokens:
            email, password = (
                ("admin@example.com", ADMIN_PASSWORD) if who == "admin" else (f"{who}@test.io", PASSWORD)
            )
            response = client.post("/api/auth/login", json={"email": email, "password": password})
            assert response.status_code == 200, response.text
            tokens[who] = response.json()["access_token"]
        return {"Authorization": f"Bearer {tokens[who]}"}

    return headers


@pytest.fixture(autouse=True)
def fake_llm(request, monkeypatch):
    """
    Deterministic AI provider for every test not marked `live_llm`.
    Tests change `fake_llm.transport` to inject a failure mode.
    """

    if request.node.get_closest_marker("live_llm"):
        yield None
        return

    fake = FakeLLM()
    monkeypatch.setattr(metering.requests, "post", fake)
    # The analyzers read the key once at import; CI has no .env at all.
    monkeypatch.setattr(risk_factor_analyzer, "OPENROUTER_API_KEY", "test-key-not-real")
    monkeypatch.setattr(likelihood_impact_analyzer, "OPENROUTER_API_KEY", "test-key-not-real")
    yield fake


# -- API helpers ---------------------------------------------------------


def ok(response, status: int = 200):
    assert response.status_code == status, f"{response.status_code}: {response.text}"
    return response.json()


@pytest.fixture
def create_assessment(client, auth):
    def create(who: str = "owner", is_draft: bool = False, **overrides) -> dict:
        body = {**FULL_REQUEST, **overrides, "is_draft": is_draft}
        return ok(client.post("/api/assessments", json=body, headers=auth(who)), 201)

    return create


@pytest.fixture
def analysed_assessment(client, auth, create_assessment):
    """
    Drive an assessment the way the UI does -- intake -> evidence ->
    confirm profile -> run risk identification -- and return it.
    """

    def run(**overrides) -> dict:
        aid = create_assessment(**overrides)["id"]
        ok(client.patch(f"/api/assessments/{aid}/advance-stage", json={}, headers=auth("owner")))
        # First attempt auto-creates the structured profile, then refuses
        # until the owner confirms it.
        client.patch(f"/api/assessments/{aid}/advance-stage", json={}, headers=auth("analyst"))
        ok(
            client.post(
                f"/api/assessments/{aid}/intelligence/confirm",
                json={"confirmed_by": "Owner"},
                headers=auth("owner"),
            )
        )
        ok(client.patch(f"/api/assessments/{aid}/advance-stage", json={}, headers=auth("analyst")))
        return ok(client.get(f"/api/assessments/{aid}", headers=auth("analyst")))

    return run

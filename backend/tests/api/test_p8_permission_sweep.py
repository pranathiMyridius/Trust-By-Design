"""
P8: route x role permission sweep (R15.1, R15.3-R15.5).

Every API operation is enumerated from the OpenAPI schema, so a route added
later is covered automatically. Invariants checked on all of them:

  1. no token                     -> 401 (except the explicit PUBLIC list)
  2. Auditor / Read-only Executive -> 403 on every write, logged ACCESS_DENIED,
                                      and nothing changes
  3. another Business User         -> 403 on every route scoped to an
                                      assessment (or its document) they can't
                                      see, read or write, logged
  4. an entity-scoped analyst      -> 403 on every assessment-scoped read
                                      outside their legal entity

plus a curated table of privileged operations and the roles that are and
aren't allowed. Every probe uses an empty body and placeholder ids, so a
request that does get through fails validation or lookup instead of
changing anything.
"""

from __future__ import annotations

import re

import pytest

from app.database import SessionLocal
from app.main import app
from app.models.assessment import Assessment
from app.auth.access import READ_ONLY_POST_ALLOWLIST
from app.models.audit_event import AuditEvent
from tests.api.test_roles_and_scope import _login, _user
from tests.conftest import ok

WRITE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}

# Reachable without a token, by design.
PUBLIC = {("POST", "/api/auth/login")}

PLACEHOLDER = "999999"


def _operations() -> list[tuple[str, str]]:
    paths = app.openapi()["paths"]
    return sorted(
        (method.upper(), path)
        for path, item in paths.items()
        for method in item
        if method in {"get", "post", "put", "patch", "delete"} and path.startswith("/api")
    )


OPERATIONS = _operations()


def _fill(path: str, **ids) -> str:
    return re.sub(r"\{(\w+)\}", lambda m: str(ids.get(m.group(1), PLACEHOLDER)), path)


def _call(client, method: str, url: str, headers: dict | None = None):
    kwargs = {"headers": headers or {}}
    if method in WRITE_METHODS:
        kwargs["json"] = {}
    return client.request(method, url, **kwargs)


def _denials(user_id: int) -> int:
    db = SessionLocal()
    try:
        return db.query(AuditEvent).filter(AuditEvent.action == "ACCESS_DENIED", AuditEvent.actor_id == user_id).count()
    finally:
        db.close()


def _fingerprint(aid: int) -> tuple:
    """What a refused write must not change."""

    db = SessionLocal()
    try:
        assessment = db.get(Assessment, aid)
        events = db.query(AuditEvent).filter(AuditEvent.assessment_id == aid, AuditEvent.action != "ACCESS_DENIED").count()
        return (assessment.status, assessment.workflow_status, assessment.updated_at, events)
    finally:
        db.close()


def test_the_sweep_sees_the_whole_api():
    # Guards against the enumeration silently finding nothing.
    assert len(OPERATIONS) > 150
    assert sum(1 for _, path in OPERATIONS if "{assessment_id}" in path) > 80


def test_every_route_needs_a_token(client):
    unprotected = []
    for method, path in OPERATIONS:
        if (method, path) in PUBLIC:
            continue
        response = _call(client, method, _fill(path))
        if response.status_code != 401:
            unprotected.append(f"{method} {path} -> {response.status_code}")
    assert unprotected == []


@pytest.mark.parametrize("role", ["AUDITOR", "EXECUTIVE"])
def test_read_only_roles_are_refused_every_write(client, create_assessment, role):
    user_id = _user(f"sweep.{role.lower()}@test.io", role)
    headers = _login(client, f"sweep.{role.lower()}@test.io")
    aid = create_assessment()["id"]
    before = _fingerprint(aid)
    denied_before = _denials(user_id)

    # The read-only assistant is the one deliberate exception (it changes
    # nothing); tests/api/test_assistant.py covers it.
    writes = [
        (m, p)
        for m, p in OPERATIONS
        if m in WRITE_METHODS
        and (m, p) not in PUBLIC
        and (m, p) not in READ_ONLY_POST_ALLOWLIST
        and not p.startswith("/api/auth/")
    ]
    allowed = []
    for method, path in writes:
        response = _call(client, method, _fill(path, assessment_id=aid), headers)
        if response.status_code != 403:
            allowed.append(f"{method} {path} -> {response.status_code}")

    assert allowed == []
    assert _denials(user_id) - denied_before == len(writes)  # every refusal is on the record
    assert _fingerprint(aid) == before


def test_another_business_user_reaches_nothing_on_an_assessment_they_cannot_see(client, auth, users, create_assessment):
    aid = create_assessment(who="owner")["id"]
    # A document on it, for the {document_id} routes.
    document = ok(
        client.post(
            f"/api/assessments/{aid}/documents",
            files={"file": ("brief.txt", b"Card acquiring brief.", "text/plain")},
            headers=auth("owner"),
        )
    )
    before = _fingerprint(aid)
    denied_before = _denials(users["other_owner"])

    scoped = [(m, p) for m, p in OPERATIONS if "{assessment_id}" in p or "{document_id}" in p]
    reached = []
    for method, path in scoped:
        response = _call(client, method, _fill(path, assessment_id=aid, document_id=document["id"]), auth("other_owner"))
        if response.status_code != 403:
            reached.append(f"{method} {path} -> {response.status_code}")

    assert reached == []
    assert _denials(users["other_owner"]) - denied_before == len(scoped)
    assert _fingerprint(aid) == before


def test_entity_scope_applies_to_every_assessment_read(client, create_assessment):
    _user("sweep.scoped@test.io", "FCRM_ANALYST", scope_legal_entities=["Bank DE GmbH"])
    headers = _login(client, "sweep.scoped@test.io")
    outside = create_assessment(legal_entity="Bank FR SA")["id"]
    inside = create_assessment(legal_entity="Bank DE GmbH")["id"]

    reads = [p for m, p in OPERATIONS if m == "GET" and "{assessment_id}" in p]
    leaked = [p for p in reads if _call(client, "GET", _fill(p, assessment_id=outside), headers).status_code != 403]
    assert leaked == []
    # ...while the same analyst can open one inside their scope.
    assert client.get(f"/api/assessments/{inside}", headers=headers).status_code == 200


# (method, path, roles that must be refused, roles that must get past the role check)
PRIVILEGED = [
    ("GET", "/api/system/security-posture", ["owner", "analyst", "manager"], ["admin"]),
    ("GET", "/api/system/performance", ["owner", "analyst", "manager"], ["admin"]),
    ("GET", "/api/system/backups", ["owner", "analyst", "manager"], ["admin"]),
    ("POST", "/api/users", ["owner", "analyst", "manager", "committee"], ["admin"]),
    ("PUT", "/api/users/{user_id}/designations", ["owner", "analyst", "manager", "head"], ["admin"]),
    ("PUT", "/api/risk-methodologies/{methodology_id}/escalation-rules", ["owner", "analyst", "manager"], ["admin"]),
    ("POST", "/api/sources", ["owner", "analyst", "manager"], ["admin"]),
    ("POST", "/api/sources/extract", ["owner", "analyst", "manager"], ["admin"]),
    ("POST", "/api/sources/with-file", ["owner", "analyst", "manager"], ["admin"]),
    ("GET", "/api/reports/ai-evaluation", ["owner", "committee"], ["analyst", "manager", "admin"]),
    ("GET", "/api/retention/eligibility", ["owner", "analyst", "manager", "committee"], ["admin"]),
    ("POST", "/api/assessments/{assessment_id}/analyze", ["owner"], []),
    ("PATCH", "/api/assessments/{assessment_id}/risk-factors/{risk_factor_id}/rating", ["owner", "committee"], []),
    ("PATCH", "/api/assessments/{assessment_id}/risk-factors/{risk_factor_id}/indicators", ["owner", "committee"], []),
]


@pytest.mark.parametrize("method, path, refused, permitted", PRIVILEGED, ids=[f"{m} {p}" for m, p, _, _ in PRIVILEGED])
def test_privileged_operations(client, auth, users, create_assessment, method, path, refused, permitted):
    assert (method, path) in OPERATIONS, f"{method} {path} is not a route any more"
    aid = create_assessment()["id"]
    url = _fill(path, assessment_id=aid, user_id=users["analyst"])
    for who in refused:
        response = _call(client, method, url, auth(who))
        assert response.status_code == 403, f"{who}: {response.status_code} {response.text[:200]}"
    for who in permitted:
        response = _call(client, method, url, auth(who))
        assert response.status_code not in (401, 403), f"{who}: {response.status_code} {response.text[:200]}"

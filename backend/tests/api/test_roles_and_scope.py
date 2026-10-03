"""
R15.1  Auditor and Read-only Executive roles: see, never change
R15.4  access restricted by legal entity / business unit / country
R15.5  denied attempts are recorded
"""

import pytest

from app.auth.security import hash_password
from app.database import SessionLocal
from app.models.audit_event import AuditEvent
from app.models.user import User
from tests.conftest import FULL_REQUEST, PASSWORD, ok


def _user(email: str, role: str, **scope) -> int:
    db = SessionLocal()
    try:
        user = db.query(User).filter(User.email == email).first()
        if user is None:
            user = User(email=email, hashed_password=hash_password(PASSWORD), full_name=email.split("@")[0].title(), role=role)
            db.add(user)
        for field, values in scope.items():
            user.set_scope(field, values)
        db.commit()
        return user.id
    finally:
        db.close()


def _login(client, email: str) -> dict:
    token = ok(client.post("/api/auth/login", json={"email": email, "password": PASSWORD}))["access_token"]
    return {"Authorization": f"Bearer {token}"}


def _denied(user_id: int) -> list[AuditEvent]:
    db = SessionLocal()
    try:
        return db.query(AuditEvent).filter(AuditEvent.action == "ACCESS_DENIED", AuditEvent.actor_id == user_id).all()
    finally:
        db.close()


@pytest.mark.parametrize("role", ["AUDITOR", "EXECUTIVE"])
def test_read_only_roles_see_but_cannot_change(client, auth, create_assessment, role):
    user_id = _user(f"{role.lower()}@test.io", role)
    headers = _login(client, f"{role.lower()}@test.io")
    aid = create_assessment()["id"]

    assert ok(client.get(f"/api/assessments/{aid}", headers=headers))["id"] == aid
    assert any(row["id"] == aid for row in ok(client.get("/api/assessments", headers=headers)))
    ok(client.get("/api/reports/operational", headers=headers))

    before = len(_denied(user_id))
    create = client.post("/api/assessments", json={**FULL_REQUEST, "is_draft": False}, headers=headers)
    assert create.status_code == 403 and "read-only" in create.json()["detail"]
    edit = client.patch(f"/api/assessments/{aid}/intelligence", json={"customer_type": "x"}, headers=headers)
    assert edit.status_code == 403
    assert len(_denied(user_id)) == before + 2


def test_auditor_reads_all_history_and_exports(client, auth, create_assessment):
    _user("auditor@test.io", "AUDITOR")
    headers = _login(client, "auditor@test.io")
    aid = create_assessment()["id"]

    events = ok(client.get("/api/assessments/audit/all", headers=headers))
    assert any(event["assessment_id"] == aid for event in events)
    package = ok(client.get(f"/api/assessments/{aid}/audit-export", headers=headers))
    assert package["assessment_id"] == aid


def test_audit_history_is_limited_to_visible_assessments(client, auth, create_assessment):
    mine = create_assessment(who="owner")["id"]
    theirs = create_assessment(who="other_owner")["id"]

    seen = {event["assessment_id"] for event in ok(client.get("/api/assessments/audit/all", headers=auth("owner")))}
    assert mine in seen and theirs not in seen


def test_entity_scope_restricts_what_a_user_sees(client, auth, create_assessment):
    analyst_id = _user("scoped.analyst@test.io", "FCRM_ANALYST", scope_legal_entities=["Bank DE GmbH"])
    headers = _login(client, "scoped.analyst@test.io")

    inside = create_assessment(legal_entity="Bank DE GmbH")["id"]
    outside = create_assessment(legal_entity="Bank FR SA")["id"]

    listed = {row["id"] for row in ok(client.get("/api/assessments?limit=500", headers=headers))}
    assert inside in listed and outside not in listed

    assert ok(client.get(f"/api/assessments/{inside}", headers=headers))["id"] == inside
    before = len(_denied(analyst_id))
    assert client.get(f"/api/assessments/{outside}", headers=headers).status_code == 403
    assert len(_denied(analyst_id)) == before + 1


def test_country_and_unit_scopes_combine(client, auth, create_assessment):
    _user(
        "scoped.manager@test.io",
        "FCRM_ANALYST",
        scope_business_units=["Cards"],
        scope_countries=["Poland"],
    )
    headers = _login(client, "scoped.manager@test.io")

    both = create_assessment(business_unit="Cards", countries_jurisdictions="Germany, Poland")["id"]
    wrong_unit = create_assessment(business_unit="Payments", countries_jurisdictions="Poland")["id"]
    wrong_country = create_assessment(business_unit="Cards", countries_jurisdictions="France")["id"]

    listed = {row["id"] for row in ok(client.get("/api/assessments?limit=500", headers=headers))}
    assert both in listed
    assert wrong_unit not in listed and wrong_country not in listed


def test_admin_sets_scopes_through_the_users_api(client, auth):
    user_id = _user("to.scope@test.io", "FCRM_ANALYST")
    updated = ok(
        client.patch(
            f"/api/users/{user_id}",
            json={"scope_legal_entities": ["Bank DE GmbH", " "], "scope_countries": ["Poland"]},
            headers=auth("admin"),
        )
    )
    assert updated["scope_legal_entities"] == ["Bank DE GmbH"]
    assert updated["scope_countries"] == ["Poland"]
    assert updated["scope_business_units"] == []

    cleared = ok(client.patch(f"/api/users/{user_id}", json={"scope_legal_entities": []}, headers=auth("admin")))
    assert cleared["scope_legal_entities"] == []

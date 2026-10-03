"""
P3 closure: every API that applies the provisional governance rules
(app/governance/policy.py) reports `policy_status`, with the same value,
taken from the policy module and never from the request. Nothing reports
the rules as approved.

Covers the two surfaces that lacked it: the override ledger (review and
approval rules, R-GOV-03) and user designations (the designation model).
"""

from app.governance.policy import POLICY_STATUS
from tests.api.test_p2_governance import _at, no_challenge_triggers  # noqa: F401
from tests.api.test_p3_sod import _user
from tests.conftest import ok

PENDING = "PROVISIONAL_PENDING_GOVERNANCE_APPROVAL"


def test_policy_status_constant_is_the_provisional_value():
    assert POLICY_STATUS == PENDING


def _propose(client, auth, aid, **extra):
    return ok(
        client.post(
            f"/api/assessments/{aid}/overrides",
            json={
                "section": "INTAKE_FIELD",
                "field_name": "countries_jurisdictions",
                "human_value": "Germany, Poland, Ukraine",
                "reason": "Missed jurisdiction.",
                **extra,
            },
            headers=auth("senior"),
        ),
        201,
    )


def test_override_ledger_reports_provisional_policy_at_every_step(client, auth, users, create_assessment, no_challenge_triggers):
    aid = create_assessment()["id"]
    _at(aid, "HUMAN_REVIEW", manager_id=users["manager"])

    proposed = _propose(client, auth, aid)
    assert proposed["policy_status"] == PENDING

    review = f"/api/assessments/{aid}/overrides/{proposed['id']}/review"
    reviewed = ok(client.patch(review, json={"decision": "CONFIRM", "note": "Supported by the contract."}, headers=auth("manager")))
    assert reviewed["policy_status"] == PENDING

    approval = f"/api/assessments/{aid}/overrides/{proposed['id']}/approval"
    approved = ok(client.patch(approval, json={"decision": "APPROVE", "rationale": "Material and justified."}, headers=auth("head")))
    # The override is approved; the policy that governed it is still not.
    assert approved["state"] == "APPROVED"
    assert approved["policy_status"] == PENDING

    ledger = ok(client.get(f"/api/assessments/{aid}/overrides", headers=auth("manager")))
    assert ledger and {entry["policy_status"] for entry in ledger} == {PENDING}


def test_a_client_cannot_set_the_policy_status(client, auth, users, create_assessment, no_challenge_triggers):
    aid = create_assessment()["id"]
    _at(aid, "HUMAN_REVIEW", manager_id=users["manager"])
    entry = _propose(client, auth, aid, policy_status="APPROVED")
    assert entry["policy_status"] == PENDING


def test_user_responses_report_the_designation_policy_status(client, auth, users):
    target = _user("p3status-designee@test.io", "FCRM_ANALYST")
    updated = ok(
        client.put(
            f"/api/users/{target}/designations",
            json={"designations": ["SENIOR_ANALYST"], "reason": "Assigned for P3 closure test.", "policy_status": "APPROVED"},
            headers=auth("admin"),
        )
    )
    assert updated["governance_designations"] == ["SENIOR_ANALYST"]
    assert updated["policy_status"] == PENDING

    listed = ok(client.get("/api/users", headers=auth("admin")))
    assert listed and {user["policy_status"] for user in listed} == {PENDING}

    me = ok(client.get("/api/auth/me", headers=auth("senior")))
    assert me["policy_status"] == PENDING


def _scoped_user(email: str, role: str, designations, entities=None) -> None:
    from app.database import SessionLocal
    from app.models.user import User

    _user(email, role, designations=designations)
    db = SessionLocal()
    try:
        user = db.query(User).filter(User.email == email).first()
        user.set_scope("scope_legal_entities", entities)
        db.commit()
    finally:
        db.close()


def test_designations_widen_visibility_only_as_documented(client, auth, create_assessment):
    """A governance designation (held with its permitted base role) lets a
    Manager see cases outside their chain, as the provisional matrix says.
    Entity scope still applies on top, and a designation held with the
    wrong base role (only possible by a direct database write) grants
    nothing."""

    from tests.api.test_p3_sod import _login

    inside = create_assessment(legal_entity="Bank DE GmbH")["id"]
    outside = create_assessment(legal_entity="Bank FR SA")["id"]

    _scoped_user("p3vis-plain-mgr@test.io", "MANAGER", None)
    _scoped_user("p3vis-qa-mgr@test.io", "MANAGER", ["QA_REVIEWER"], entities=["Bank DE GmbH"])
    _scoped_user("p3vis-wrong-base@test.io", "BUSINESS_USER", ["HEAD_OF_FCRM"])

    plain = _login(client, "p3vis-plain-mgr@test.io")
    assert client.get(f"/api/assessments/{inside}", headers=plain).status_code == 403

    qa = _login(client, "p3vis-qa-mgr@test.io")
    assert ok(client.get(f"/api/assessments/{inside}", headers=qa))["id"] == inside
    assert client.get(f"/api/assessments/{outside}", headers=qa).status_code == 403  # scope still applies

    wrong = _login(client, "p3vis-wrong-base@test.io")
    assert client.get(f"/api/assessments/{inside}", headers=wrong).status_code == 403


def test_all_governance_surfaces_report_the_same_status(client, auth, users, create_assessment, no_challenge_triggers):
    aid = create_assessment()["id"]
    _at(aid, "HUMAN_REVIEW", manager_id=users["manager"])

    policy = ok(client.get("/api/governance/policy", headers=auth("manager")))
    readiness = ok(client.get(f"/api/assessments/{aid}/readiness?purpose=COMMITTEE_SUBMISSION", headers=auth("manager")))
    signoff = ok(client.get(f"/api/assessments/{aid}/challenge-review/signoff", headers=auth("manager")))
    ledger_entry = _propose(client, auth, aid)
    user = ok(client.get("/api/auth/me", headers=auth("manager")))

    statuses = {
        "governance policy": policy["policy_status"],
        "readiness": readiness["policy_status"],
        "challenge review": signoff["policy_status"],
        "override ledger": ledger_entry["policy_status"],
        "user designations": user["policy_status"],
    }
    assert set(statuses.values()) == {PENDING}, statuses

"""
AW.7 (Approval delegation) acceptance checks.

Runs against a throwaway SQLite database -- never the DATABASE_URL in
backend/.env. Run from the backend/ folder:

    python test_aw7_delegation.py

(Also collectable by pytest if it's installed: every check is a
test_* function.)

Acceptance criteria covered:
  AC1  A delegate can approve only during the active delegation period.
  AC2  The approval record identifies both the original approver and
       the delegate.
  AC3  Delegates cannot approve outside the delegated scope.
  AC4  Expired delegations automatically stop granting approval access.
"""

import os
import tempfile
from datetime import datetime, timedelta, timezone

_DB_FILE = os.path.join(tempfile.mkdtemp(), "aw7_test.db")
# Tests never use the real AI provider/key from backend/.env (their HTTP
# calls are faked or absent); pin the offline OpenRouter configuration.
os.environ["LLM_PROVIDER"] = "openrouter"
os.environ["OPENAI_API_KEY"] = ""
os.environ["DATABASE_URL"] = f"sqlite:///{_DB_FILE}"
os.environ["WORKFLOW_ESCALATION_INTERVAL_SECONDS"] = "0"
os.environ["ADMIN_BOOTSTRAP_PASSWORD"] = "ChangeMe123!"
os.environ["BACKUP_INTERVAL_HOURS"] = "0"
os.environ["PROCESSING_JOBS_INLINE"] = "true"

from fastapi.testclient import TestClient  # noqa: E402

import app.api.approvals as approvals_api  # noqa: E402
import app.challenge_engine.engine as challenge_engine  # noqa: E402
from app.auth.security import hash_password  # noqa: E402
from app.database import SessionLocal  # noqa: E402
from app.main import app, prepare_database  # noqa: E402

# The schema is prepared explicitly; importing the app never migrates.
prepare_database()
from app.models.approval_delegation import ApprovalDelegation  # noqa: E402
from app.models.assessment import Assessment  # noqa: E402
from app.models.audit_event import AuditEvent  # noqa: E402
from app.models.committee_vote import CommitteeVote  # noqa: E402
from app.models.user import User  # noqa: E402
from app.models.workflow_transition import WorkflowTransition  # noqa: E402

client = TestClient(app)
PASSWORD = "Passw0rd!"

# Challenge findings are covered by their own suite. Here the assigned
# manager accepts them with a reason (_accept_open_findings) -- P3
# readiness reads the findings themselves, so no stub stands in for them.

REQUEST = {
    "title": "Launch card product in Germany",
    "change_type": "NEW_PRODUCT",
    "product_or_service_name": "Debit Card DE",
    "description": "New debit card for German retail customers.",
    "evidence": "Product spec v1.",
    "business_owner": "Retail Cards",
    "legal_entity": "Bank DE GmbH",
    "customer_segment": "Retail",
    "countries_jurisdictions": "Germany",
    "delivery_channels": "Mobile app",
    "expected_transaction_volume": "10000/month",
    "expected_transaction_value": "EUR 1m/month",
    "transaction_types": "Card payments",
    "third_party_vendor_usage": "Card processor",
    "technology_process_changes": "New card platform",
    "expected_launch_date": "2027-01-01",
}

EMAILS = {
    "m1": "m1@test.io",
    "m2": "m2@test.io",
    "m3": "m3@test.io",
    "owner": "owner@test.io",
    "owner3": "owner3@test.io",
    "analyst": "analyst@test.io",
    "c1": "c1@test.io",
    "c2": "c2@test.io",
    # G-5 quorum (2026-10-03): the two representative seats.
    "c3": "c3@test.io",
    "c4": "c4@test.io",
    "admin": "admin@example.com",
}


def _make_users() -> dict[str, int]:
    db = SessionLocal()
    try:
        made = {}

        def add(key, role, manager_key=None, designations=None):
            user = User(
                email=EMAILS[key],
                hashed_password=hash_password(PASSWORD),
                full_name=key.upper(),
                role=role,
                manager_id=made[manager_key].id if manager_key else None,
            )
            # P3: governance designations (provisional role matrix).
            user.set_designations(designations)
            db.add(user)
            db.flush()
            made[key] = user

        add("m1", "MANAGER")
        add("m2", "MANAGER")
        add("m3", "MANAGER")
        add("owner", "BUSINESS_USER", "m1")
        add("owner3", "BUSINESS_USER", "m3")
        add("analyst", "FCRM_ANALYST", "m1", ["CHALLENGE_REVIEWER"])
        add("c1", "COMMITTEE_MEMBER")
        add("c2", "COMMITTEE_MEMBER")
        add("c3", "COMMITTEE_MEMBER", designations=["COMMITTEE_FCRM_COMPLIANCE_REP"])
        add("c4", "COMMITTEE_MEMBER", designations=["COMMITTEE_BUSINESS_RISK_REP"])
        db.commit()
        ids = {key: user.id for key, user in made.items()}
        ids["admin"] = db.query(User).filter(User.email == EMAILS["admin"]).one().id
        return ids
    finally:
        db.close()


USERS = _make_users()
_TOKENS: dict[str, str] = {}


def auth(who: str) -> dict:
    if who not in _TOKENS:
        password = "ChangeMe123!" if who == "admin" else PASSWORD
        response = client.post("/api/auth/login", json={"email": EMAILS[who], "password": password})
        assert response.status_code == 200, response.text
        _TOKENS[who] = response.json()["access_token"]
    return {"Authorization": f"Bearer {_TOKENS[who]}"}


def _resolve_open_findings(assessment_id: int, who: str) -> None:
    """The real journey: each open challenge finding is resolved with a
    note (P3 readiness reads the findings themselves). G-4 (2026-10-03):
    HIGH/CRITICAL findings can't be accepted, and MEDIUM ones only by the
    Committee, so before submission they are resolved."""

    review = client.get(f"/api/assessments/{assessment_id}/challenge-review", headers=auth(who))
    assert review.status_code == 200, review.text
    for finding in review.json()["findings"]:
        if finding["resolution_status"] == "OPEN":
            resolved = client.patch(
                f"/api/assessments/{assessment_id}/challenge-findings/{finding['id']}/resolve",
                json={"resolution_note": "Addressed for this workflow test."},
                headers=auth(who),
            )
            assert resolved.status_code == 200, resolved.text


def _iso(delta: timedelta) -> str:
    return (datetime.now(timezone.utc) + delta).isoformat()


def _assessment(status: str, owner: str = "owner", manager: str = "m1", **fields) -> int:
    """An assessment placed straight at `status`, assigned to `manager`."""

    from app.services import workflow

    response = client.post("/api/assessments", json={**REQUEST, "is_draft": False}, headers=auth(owner))
    assert response.status_code == 201, response.text
    assessment_id = response.json()["id"]

    db = SessionLocal()
    try:
        assessment = db.get(Assessment, assessment_id)
        assessment.status = status
        assessment.manager_id = USERS[manager]
        from decision_fixture import seed_decision_inputs

        seed_decision_inputs(db, assessment_id)
        for key, value in fields.items():
            setattr(assessment, key, value)
        assessment.workflow_status = workflow.derive_workflow_status(db, assessment)
        db.commit()
    finally:
        db.close()

    # R11 / P3: the independent challenge review (a designated Challenge
    # Reviewer) and the assigned manager's sign-off, through the API,
    # before an assessment reaches the committee.
    from app.services.challenge_signoff import SIGNOFF_STATUSES

    if status in SIGNOFF_STATUSES:
        _resolve_open_findings(assessment_id, manager)
        reviewed = client.post(
            f"/api/assessments/{assessment_id}/challenge-review/review",
            json={"reason": "Challenge review completed for the delegation tests."},
            headers=auth("analyst"),
        )
        assert reviewed.status_code == 201, reviewed.text
        signed = client.post(
            f"/api/assessments/{assessment_id}/challenge-review/signoff",
            json={"reason": "Signed off for the delegation tests."},
            headers=auth(manager),
        )
        assert signed.status_code == 201, signed.text
    return assessment_id


def _committee_ready(**kwargs) -> int:
    return _assessment(
        "READY_FOR_COMMITTEE",
        manager_decision="APPROVE",
        manager_decided_by_id=USERS[kwargs.pop("approved_by", "m1")],
        **kwargs,
    )


def _delegate(
    delegator: str,
    delegate: str,
    authority: str = "MANAGER_APPROVAL",
    *,
    who: str | None = None,
    scope_assessment_id: int | None = None,
    start: timedelta = timedelta(hours=-1),
    end: timedelta = timedelta(days=7),
    reason: str = "Annual leave.",
    expect: int = 201,
) -> dict:
    body = {
        "delegate_id": USERS[delegate],
        "authority": authority,
        "scope_type": "ASSESSMENT" if scope_assessment_id else "ALL",
        "scope_assessment_id": scope_assessment_id,
        "start_at": _iso(start),
        "end_at": _iso(end),
        "reason": reason,
    }
    if who and who != delegator:
        body["delegator_id"] = USERS[delegator]
    response = client.post("/api/delegations", json=body, headers=auth(who or delegator))
    assert response.status_code == expect, (response.status_code, response.text)
    return response.json()


def _manager_decision(assessment_id: int, who: str, decision: str = "approve"):
    return client.post(
        f"/api/assessments/{assessment_id}/manager-decision",
        json={"decision": decision, "comment": "Reviewed."},
        headers=auth(who),
    )


def _revoke_all() -> None:
    """Isolate tests: no delegation from an earlier test stays in force."""

    db = SessionLocal()
    try:
        now = datetime.now(timezone.utc)
        for delegation in db.query(ApprovalDelegation).filter(ApprovalDelegation.revoked_at.is_(None)):
            delegation.revoked_at = now
            delegation.revoked_by_id = USERS["admin"]
            delegation.revoke_reason = "Test isolation."
        db.commit()
    finally:
        db.close()


def _set_window(delegation_id: int, start: timedelta, end: timedelta) -> None:
    """Move a stored delegation's window, i.e. let time pass."""

    db = SessionLocal()
    try:
        delegation = db.get(ApprovalDelegation, delegation_id)
        now = datetime.now(timezone.utc)
        delegation.start_at = now + start
        delegation.end_at = now + end
        db.commit()
    finally:
        db.close()


# --- AC1 + AC2 --------------------------------------------------------------------


def test_delegate_approves_during_active_period_and_record_names_both():
    _revoke_all()
    delegation = _delegate("m1", "m2")
    assert delegation["state"] == "ACTIVE"
    aid = _assessment("SUBMITTED_TO_MANAGER")

    # The delegate can see the assessment and finds it in their work queue.
    assert client.get(f"/api/assessments/{aid}", headers=auth("m2")).status_code == 200
    queue = client.get("/api/workflow/work-queue", headers=auth("m2")).json()
    assert aid in {task["assessment_id"] for task in queue["tasks"]}, queue

    response = _manager_decision(aid, "m2")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["workflow_status"] == "READY_FOR_COMMITTEE"

    # AC2: the record names the delegate who acted and the manager acted for.
    assert body["manager_decided_by_id"] == USERS["m2"]
    assert body["manager_decided_on_behalf_of_id"] == USERS["m1"]
    assert body["manager_delegation_id"] == delegation["id"]

    db = SessionLocal()
    try:
        event = (
            db.query(AuditEvent)
            .filter(AuditEvent.assessment_id == aid, AuditEvent.action == "MANAGER_APPROVED")
            .one()
        )
        assert "M2" in event.actor and "delegate for M1" in event.actor, event.actor
        assert f"delegation #{delegation['id']}" in event.actor, event.actor
        history = (
            db.query(WorkflowTransition)
            .filter(WorkflowTransition.assessment_id == aid, WorkflowTransition.action == "MANAGER_DECISION")
            .one()
        )
        assert history.user_id == USERS["m2"] and "delegate for M1" in history.actor, history.actor
    finally:
        db.close()


def test_native_approvals_are_unchanged():
    _revoke_all()
    aid = _assessment("SUBMITTED_TO_MANAGER")
    # Another manager still cannot see, let alone approve, m1's queue.
    assert _manager_decision(aid, "m3").status_code == 403
    response = _manager_decision(aid, "m1")
    assert response.status_code == 200, response.text
    assert response.json()["manager_decided_on_behalf_of_id"] is None
    assert response.json()["manager_delegation_id"] is None


def test_delegation_not_yet_started_grants_nothing():
    _revoke_all()
    _delegate("m1", "m2", start=timedelta(days=1), end=timedelta(days=3))
    aid = _assessment("SUBMITTED_TO_MANAGER")
    response = _manager_decision(aid, "m2")
    assert response.status_code == 403, response.text
    assert "starts on" in response.json()["detail"], response.text


# --- AC3 --------------------------------------------------------------------------


def test_single_assessment_scope_is_enforced():
    _revoke_all()
    in_scope = _assessment("SUBMITTED_TO_MANAGER")
    out_of_scope = _assessment("SUBMITTED_TO_MANAGER")
    _delegate("m1", "m2", scope_assessment_id=in_scope)

    # Outside the scope: not visible, and cannot be approved.
    assert client.get(f"/api/assessments/{out_of_scope}", headers=auth("m2")).status_code == 403
    assert _manager_decision(out_of_scope, "m2").status_code == 403

    assert _manager_decision(in_scope, "m2").status_code == 200


def test_delegate_cannot_approve_another_managers_queue():
    _revoke_all()
    _delegate("m1", "m2")
    other = _assessment("SUBMITTED_TO_MANAGER", owner="owner3", manager="m3")
    assert client.get(f"/api/assessments/{other}", headers=auth("m2")).status_code == 403
    assert _manager_decision(other, "m2").status_code == 403


def test_manager_delegation_grants_no_committee_authority():
    _revoke_all()
    _delegate("m1", "m2")
    aid = _committee_ready()
    response = client.post(
        f"/api/assessments/{aid}/committee-decision",
        json={"decision": "approve", "rationale": "Fine."},
        headers=auth("m2"),
    )
    assert response.status_code == 403, response.text


# --- AC4 --------------------------------------------------------------------------


def test_expired_delegation_stops_granting_access_without_any_job():
    _revoke_all()
    delegation = _delegate("m1", "m2")
    aid = _assessment("SUBMITTED_TO_MANAGER")
    assert client.get(f"/api/assessments/{aid}", headers=auth("m2")).status_code == 200

    # Time passes: the window closes. Nothing runs; nothing is updated.
    _set_window(delegation["id"], start=timedelta(days=-7), end=timedelta(minutes=-1))

    response = _manager_decision(aid, "m2")
    assert response.status_code == 403, response.text
    assert "expired" in response.json()["detail"], response.text
    assert client.get(f"/api/assessments/{aid}", headers=auth("m2")).status_code == 403
    queue = client.get("/api/workflow/work-queue", headers=auth("m2")).json()
    assert aid not in {task["assessment_id"] for task in queue["tasks"]}

    listed = {d["id"]: d for d in client.get("/api/delegations", headers=auth("m1")).json()}
    assert listed[delegation["id"]]["state"] == "EXPIRED"


def test_revoked_delegation_stops_granting_access():
    _revoke_all()
    delegation = _delegate("m1", "m2")
    aid = _assessment("SUBMITTED_TO_MANAGER")

    # Only the delegator (or whoever recorded it, or an Admin) can revoke.
    response = client.post(
        f"/api/delegations/{delegation['id']}/revoke", json={"reason": "Mine now."}, headers=auth("m2")
    )
    assert response.status_code == 403, response.text

    response = client.post(
        f"/api/delegations/{delegation['id']}/revoke", json={"reason": "Back early."}, headers=auth("m1")
    )
    assert response.status_code == 200, response.text
    assert response.json()["state"] == "REVOKED"

    response = _manager_decision(aid, "m2")
    assert response.status_code == 403 and "revoked" in response.json()["detail"], response.text

    again = client.post(
        f"/api/delegations/{delegation['id']}/revoke", json={"reason": "Again."}, headers=auth("m1")
    )
    assert again.status_code == 409, again.text


def test_delegator_losing_the_role_ends_the_delegation():
    _revoke_all()
    _delegate("m1", "m2")
    aid = _assessment("SUBMITTED_TO_MANAGER")
    db = SessionLocal()
    try:
        db.get(User, USERS["m1"]).role = "BUSINESS_USER"
        db.commit()
        assert _manager_decision(aid, "m2").status_code == 403
    finally:
        db.get(User, USERS["m1"]).role = "MANAGER"
        db.commit()
        db.close()


# --- committee sign-off -----------------------------------------------------------


def test_committee_delegate_votes_in_the_members_seat_and_signs_off():
    _revoke_all()
    delegation = _delegate("c1", "m2", "COMMITTEE_SIGN_OFF")
    aid = _committee_ready()

    assert client.get(f"/api/assessments/{aid}", headers=auth("m2")).status_code == 200

    response = client.post(
        f"/api/assessments/{aid}/committee-votes", json={"vote": "APPROVE"}, headers=auth("m2")
    )
    assert response.status_code == 200, response.text
    vote = response.json()
    assert vote["member_id"] == USERS["c1"], vote
    assert vote["delegate_id"] == USERS["m2"] and vote["delegation_id"] == delegation["id"], vote

    # R12.6: c1 voting again later fills the same seat. The delegate's
    # vote is kept, superseded; a re-cast needs a reason.
    no_reason = client.post(f"/api/assessments/{aid}/committee-votes", json={"vote": "DISSENT"}, headers=auth("c1"))
    assert no_reason.status_code == 422, no_reason.text
    response = client.post(
        f"/api/assessments/{aid}/committee-votes",
        json={"vote": "DISSENT", "recast_reason": "Back from leave; I disagree."},
        headers=auth("c1"),
    )
    assert response.status_code == 200, response.text
    db = SessionLocal()
    try:
        seat = (
            db.query(CommitteeVote)
            .filter(CommitteeVote.assessment_id == aid)
            .order_by(CommitteeVote.version)
            .all()
        )
        assert [(v.version, v.vote, v.is_current) for v in seat] == [(1, "APPROVE", False), (2, "DISSENT", True)]
        assert {v.member_id for v in seat} == {USERS["c1"]}
        assert seat[0].delegate_id == USERS["m2"] and seat[1].delegate_id is None
        assert seat[0].superseded_by_id == seat[1].id
    finally:
        db.close()

    # G-5: the quorum -- the seat filled above plus both representatives.
    for member in ("c3", "c4"):
        response = client.post(f"/api/assessments/{aid}/committee-votes", json={"vote": "APPROVE"}, headers=auth(member))
        assert response.status_code == 200, response.text

    response = client.post(
        f"/api/assessments/{aid}/committee-decision",
        json={"decision": "approve", "rationale": "Within appetite."},
        headers=auth("m2"),
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["workflow_status"] == "APPROVED"
    assert body["committee_decided_by_id"] == USERS["m2"]
    assert body["committee_decided_on_behalf_of_id"] == USERS["c1"]
    assert body["committee_delegation_id"] == delegation["id"]


def test_committee_delegate_is_bound_by_separation_of_duties():
    _revoke_all()
    _delegate("c1", "m2", "COMMITTEE_SIGN_OFF")

    # m2 gave the manager approval (as m1's delegate), so m2 cannot also
    # sign it off, even under a committee delegation.
    aid = _committee_ready(approved_by="m2")
    response = client.post(
        f"/api/assessments/{aid}/committee-decision",
        json={"decision": "approve", "rationale": "Fine."},
        headers=auth("m2"),
    )
    assert response.status_code == 403 and "approved this assessment as its manager" in response.json()["detail"]

    # Nor the assessment's own manager of record.
    _delegate("c2", "m1", "COMMITTEE_SIGN_OFF")
    aid = _committee_ready()
    response = client.post(
        f"/api/assessments/{aid}/committee-votes", json={"vote": "APPROVE"}, headers=auth("m1")
    )
    assert response.status_code == 403, response.text


# --- creation rules -------------------------------------------------------------


def test_creation_rules():
    _revoke_all()
    # Only authority you hold natively can be delegated.
    _delegate("analyst", "m2", expect=422)
    _delegate("m1", "m2", "COMMITTEE_SIGN_OFF", expect=422)
    # The delegate must be an active Manager, and not yourself.
    _delegate("m1", "c1", expect=422)
    _delegate("m1", "analyst", expect=422)
    _delegate("m1", "m1", expect=422)
    # Temporary, well-formed, and with a reason.
    _delegate("m1", "m2", end=timedelta(days=120), expect=422)
    _delegate("m1", "m2", start=timedelta(days=2), end=timedelta(days=1), expect=422)
    _delegate("m1", "m2", start=timedelta(days=-3), end=timedelta(days=-1), expect=422)
    _delegate("m1", "m2", reason="  ", expect=422)
    # A single-assessment scope must be the delegator's own assessment.
    other = _assessment("SUBMITTED_TO_MANAGER", owner="owner3", manager="m3")
    _delegate("m1", "m2", scope_assessment_id=other, expect=422)

    # Only an Admin can record a delegation for someone else.
    _delegate("m3", "m2", who="m1", expect=403)
    created = _delegate("m3", "m2", who="admin")
    assert created["delegator_id"] == USERS["m3"] and created["created_by_id"] == USERS["admin"]

    db = SessionLocal()
    try:
        assert db.query(AuditEvent).filter(AuditEvent.action == "DELEGATION_CREATED").count() >= 1
        assert db.query(AuditEvent).filter(AuditEvent.action == "DELEGATION_REVOKED").count() >= 1
    finally:
        db.close()


def test_listing_is_limited_to_the_parties():
    _revoke_all()
    mine = _delegate("m1", "m2")
    seen_by_m3 = {d["id"] for d in client.get("/api/delegations", headers=auth("m3")).json()}
    assert mine["id"] not in seen_by_m3
    for who in ("m1", "m2", "admin"):
        assert mine["id"] in {d["id"] for d in client.get("/api/delegations", headers=auth(who)).json()}

    options = client.get("/api/delegations/options", headers=auth("m1")).json()
    assert [u["id"] for u in options["delegators"]] == [USERS["m1"]]
    assert USERS["m1"] not in {u["id"] for u in options["delegates"]}
    assert {u["role"] for u in options["delegates"]} == {"MANAGER"}


if __name__ == "__main__":
    tests = [value for name, value in list(globals().items()) if name.startswith("test_") and callable(value)]
    failures = 0
    for test in tests:
        try:
            test()
            print(f"PASS  {test.__name__}")
        except Exception as exc:  # noqa: BLE001
            failures += 1
            print(f"FAIL  {test.__name__}: {exc!r}")
    print(f"\n{len(tests) - failures}/{len(tests)} passed")
    raise SystemExit(1 if failures else 0)

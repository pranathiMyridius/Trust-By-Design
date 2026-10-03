"""
P3 -- SoD exceptions and governance enforcement (provisional policy,
app/governance/policy.py):

  * exception lifecycle: draft grants nothing; independent, tiered approval;
    self / affected-user / direct-manager / wrong-role approval refused;
    declaration before use; expiry and revocation checked at use; flags
  * Admin vs committee: mutually exclusive except through an approved,
    declared dual-role exception; same-case admin actions refused
  * overrides: proposal / review / approval separated; materiality from
    the actual change; pending and rejected material overrides block
  * challenge review independence and the two-step sign-off
  * committee readiness: authoritative, explained, enforced at submission
    and at the final decision; audit completeness; data protection
"""

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import text

import app.governance.sod as sod_service
from app.auth.security import hash_password
from app.database import SessionLocal
from app.models.assessment import Assessment
from app.models.audit_event import AuditEvent
from app.models.challenge_review import ChallengeFinding
from app.models.sod_exception import SodException, SodExceptionEvent
from app.models.user import User
from app.services.data_protection import ProtectedDataError
from tests.api.test_p2_governance import _at, _committee_ready, _review, _sign_off, no_challenge_triggers  # noqa: F401
from tests.conftest import PASSWORD, ok

# -- helpers -----------------------------------------------------------------


def _user(email: str, role: str, designations=None, manager_id=None) -> int:
    db = SessionLocal()
    try:
        user = db.query(User).filter(User.email == email).first()
        if user is None:
            user = User(email=email, hashed_password=hash_password(PASSWORD), full_name=email.split("@")[0].title(), role=role)
            db.add(user)
        user.manager_id = manager_id
        user.set_designations(designations)
        db.commit()
        return user.id
    finally:
        db.close()


def _login(client, email: str) -> dict:
    token = ok(client.post("/api/auth/login", json={"email": email, "password": PASSWORD}))["access_token"]
    return {"Authorization": f"Bearer {token}"}


def _events(action: str, assessment_id: int | None = None, actor_id: int | None = None) -> list[AuditEvent]:
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


def _set(aid: int, **fields) -> None:
    db = SessionLocal()
    try:
        assessment = db.get(Assessment, aid)
        for key, value in fields.items():
            setattr(assessment, key, value)
        db.commit()
    finally:
        db.close()


def _window(days: float = 7):
    now = datetime.now(timezone.utc)
    return (now - timedelta(minutes=1)).isoformat(), (now + timedelta(days=days)).isoformat()


def _request(client, headers, affected_id, aid=None, exception_type="COMMITTEE_SEPARATION", risk="LOW", days=7, **extra):
    start, end = _window(days)
    body = {
        "exception_type": exception_type,
        "affected_user_id": affected_id,
        "assessment_id": aid,
        "business_justification": "Only two eligible committee members are available this month.",
        "standard_workflow_reason": "Quorum cannot be reached without the conflicted member.",
        "risk_level": risk,
        "compensating_controls": "Chair reviews the vote; second-line QA re-performs the review.",
        "start_at": start,
        "end_at": end,
        **extra,
    }
    return client.post("/api/sod-exceptions", json=body, headers=headers)


def _vote(client, headers, aid, vote="APPROVE", **extra):
    return client.post(f"/api/assessments/{aid}/committee-votes", json={"vote": vote, **extra}, headers=headers)


@pytest.fixture
def member(users):
    """A Committee Member whose direct manager is the FCRM Governance Owner
    (so that otherwise-eligible approver is disqualified)."""

    return {"id": _user("p3member@test.io", "COMMITTEE_MEMBER", manager_id=users["governance"])}


@pytest.fixture
def conflicted_case(client, auth, users, create_assessment, no_challenge_triggers, member):
    """An assessment before the committee, owned by the committee member."""

    aid = _committee_ready(client, auth, users, create_assessment)
    _set(aid, owner_id=member["id"])
    return aid


# -- exception lifecycle --------------------------------------------------------


def test_a_request_grants_nothing_until_independently_approved_and_declared(client, auth, users, member, conflicted_case):
    aid = conflicted_case
    member_headers = _login(client, "p3member@test.io")

    # Conflicted: refused.
    assert _vote(client, member_headers, aid).status_code == 403

    draft = ok(_request(client, auth("analyst"), member["id"], aid), 201)
    assert draft["status"] == "DRAFT" and draft["reference"].startswith("SOD-")
    assert "SUBMITTER (owner of the assessment)" in draft["conflicting_roles"]
    assert _vote(client, member_headers, aid).status_code == 403  # a draft grants nothing

    eid = draft["id"]
    assert client.post(f"/api/sod-exceptions/{eid}/submit", headers=auth("governance")).status_code == 403  # not the requestor
    pending = ok(client.post(f"/api/sod-exceptions/{eid}/submit", headers=auth("analyst")))
    assert pending["status"] == "PENDING_APPROVAL" and pending["tier"] == "STANDARD"
    assert _vote(client, member_headers, aid).status_code == 403  # pending grants nothing

    decide = f"/api/sod-exceptions/{eid}/decision"
    body = {"decision": "APPROVE", "rationale": "Compensating controls are adequate."}
    assert "requested this exception" in client.post(decide, json=body, headers=auth("analyst")).json()["detail"]
    assert "for you" in client.post(decide, json=body, headers=member_headers).json()["detail"]
    # The Governance Owner is eligible for the tier but is the member's manager.
    assert "direct manager" in client.post(decide, json=body, headers=auth("governance")).json()["detail"]
    # A Committee Chair sees the request but holds the wrong tier's authority.
    assert "needs approval by" in client.post(decide, json=body, headers=auth("chair")).json()["detail"]
    assert len(_events("ACCESS_DENIED", aid)) >= 4

    approved = ok(client.post(decide, json=body, headers=auth("head")))
    assert approved["status"] == "APPROVED" and approved["decided_by"]
    # A second decision is refused -- no duplicate approvals.
    assert client.post(decide, json=body, headers=auth("head")).status_code == 409

    # Approved but not declared: still refused.
    assert _vote(client, member_headers, aid).status_code == 403
    assert "DECLARATION_MISSING" in approved["flags"]
    assert client.post(f"/api/sod-exceptions/{eid}/declaration", json={"rationale": "x"}, headers=auth("analyst")).status_code == 403
    ok(client.post(f"/api/sod-exceptions/{eid}/declaration", json={"rationale": "I own the request; I declare no other interest."}, headers=member_headers))

    ok(_vote(client, member_headers, aid, "ABSTAIN"))
    assert _events("SOD_EXCEPTION_USED", aid, member["id"])

    history = ok(client.get(f"/api/sod-exceptions/{eid}", headers=auth("governance")))["history"]
    assert [h["action"] for h in history] == ["CREATED", "SUBMITTED", "APPROVED", "DECLARED", "USED"]
    for action in ("SOD_EXCEPTION_CREATED", "SOD_EXCEPTION_SUBMITTED", "SOD_EXCEPTION_APPROVED", "SOD_EXCEPTION_DECLARED", "SOD_EXCEPTION_USED"):
        assert _events(action, aid), action


@pytest.mark.parametrize(
    "overrides, reason",
    [
        ({"risk": "HIGH"}, "risk level HIGH"),
        ({"days": 45}, "duration"),
    ],
)
def test_committee_tier_needs_the_chair(client, auth, users, member, conflicted_case, overrides, reason):
    eid = ok(_request(client, auth("analyst"), member["id"], conflicted_case, **overrides), 201)["id"]
    pending = ok(client.post(f"/api/sod-exceptions/{eid}/submit", headers=auth("analyst")))
    assert pending["tier"] == "COMMITTEE" and any(reason in r for r in pending["tier_reasons"])

    body = {"decision": "APPROVE", "rationale": "Accepted with the stated controls."}
    refused = client.post(f"/api/sod-exceptions/{eid}/decision", json=body, headers=auth("head"))
    assert refused.status_code == 403 and "Committee Chair" in refused.json()["detail"]
    ok(client.post(f"/api/sod-exceptions/{eid}/decision", json=body, headers=auth("chair")))


def test_a_high_risk_case_routes_to_the_chair(client, auth, users, member, conflicted_case):
    _set(conflicted_case, residual_risk_level="HIGH")
    eid = ok(_request(client, auth("analyst"), member["id"], conflicted_case), 201)["id"]
    pending = ok(client.post(f"/api/sod-exceptions/{eid}/submit", headers=auth("analyst")))
    assert pending["tier"] == "COMMITTEE" and any("HIGH risk" in r for r in pending["tier_reasons"])


def test_repeated_exceptions_are_flagged_and_escalated(client, auth, users, create_assessment, no_challenge_triggers):
    repeat_id = _user("p3repeat@test.io", "COMMITTEE_MEMBER")
    tiers = []
    for _ in range(3):
        aid = _committee_ready(client, auth, users, create_assessment)
        _set(aid, owner_id=repeat_id)
        eid = ok(_request(client, auth("analyst"), repeat_id, aid), 201)["id"]
        tiers.append(ok(client.post(f"/api/sod-exceptions/{eid}/submit", headers=auth("analyst"))))
    assert [t["tier"] for t in tiers] == ["STANDARD", "STANDARD", "COMMITTEE"]
    assert tiers[-1]["repeated"] and "REPEATED" in tiers[-1]["flags"]


@pytest.mark.parametrize(
    "change, message",
    [
        ({"days": 120}, "at most 90 days"),
        ({"exception_type": "ADMIN_COMMITTEE_DUAL_ROLE"}, "only for a user whose role is Admin"),
        ({"exception_type": "NOT_A_TYPE"}, "exception_type"),
    ],
)
def test_requests_are_validated(client, auth, member, conflicted_case, change, message):
    response = _request(client, auth("analyst"), member["id"], conflicted_case, **change)
    assert response.status_code == 422 and message in response.text


def test_no_exception_without_a_real_conflict(client, auth, users, create_assessment, no_challenge_triggers):
    aid = _committee_ready(client, auth, users, create_assessment)
    response = _request(client, auth("analyst"), users["committee"], aid)
    assert response.status_code == 422 and "no separation-of-duties conflict" in response.json()["detail"]


def _approved_declared(client, auth, member_id, aid, member_headers):
    eid = ok(_request(client, auth("analyst"), member_id, aid), 201)["id"]
    pending = ok(client.post(f"/api/sod-exceptions/{eid}/submit", headers=auth("analyst")))
    # The same member accumulates exceptions across this module's tests, so
    # later requests are (correctly) "repeated" and need the Chair.
    approver = "chair" if pending["tier"] == "COMMITTEE" else "head"
    ok(client.post(f"/api/sod-exceptions/{eid}/decision", json={"decision": "APPROVE", "rationale": "ok"}, headers=auth(approver)))
    ok(client.post(f"/api/sod-exceptions/{eid}/declaration", json={"rationale": "No other interest."}, headers=member_headers))
    return eid


def test_an_expired_exception_authorizes_nothing(client, auth, users, member, conflicted_case, monkeypatch):
    member_headers = _login(client, "p3member@test.io")
    eid = _approved_declared(client, auth, member["id"], conflicted_case, member_headers)

    # Time passes beyond the end date -- checked at the point of use, no job.
    later = sod_service.utcnow() + timedelta(days=8)
    monkeypatch.setattr(sod_service, "utcnow", lambda: later)
    assert _vote(client, member_headers, conflicted_case).status_code == 403
    detail = ok(client.get(f"/api/sod-exceptions/{eid}", headers=auth("governance")))
    assert detail["status"] == "EXPIRED" and detail["history"][-1]["action"] == "EXPIRED"
    assert _events("SOD_EXCEPTION_EXPIRED", conflicted_case)


def test_a_revoked_exception_authorizes_nothing(client, auth, users, member, conflicted_case):
    member_headers = _login(client, "p3member@test.io")
    eid = _approved_declared(client, auth, member["id"], conflicted_case, member_headers)
    assert client.post(f"/api/sod-exceptions/{eid}/revoke", json={"rationale": "mine"}, headers=member_headers).status_code == 403
    ok(client.post(f"/api/sod-exceptions/{eid}/revoke", json={"rationale": "Quorum restored."}, headers=auth("admin")))
    assert _vote(client, member_headers, conflicted_case).status_code == 403
    assert client.post(f"/api/sod-exceptions/{eid}/revoke", json={"rationale": "again"}, headers=auth("admin")).status_code == 409
    assert _events("SOD_EXCEPTION_REVOKED", conflicted_case)


def test_expiring_soon_flag_and_periodic_review(client, auth, users, member, conflicted_case, monkeypatch):
    member_headers = _login(client, "p3member@test.io")
    eid = _approved_declared(client, auth, member["id"], conflicted_case, member_headers)
    later = sod_service.utcnow() + timedelta(days=3)  # 4 days left of 7
    monkeypatch.setattr(sod_service, "utcnow", lambda: later)
    detail = ok(client.get(f"/api/sod-exceptions/{eid}", headers=auth("governance")))
    assert "EXPIRING_SOON" in detail["flags"]
    assert client.post(f"/api/sod-exceptions/{eid}/review", json={"rationale": "x"}, headers=member_headers).status_code == 403
    reviewer = "chair" if ok(client.get(f"/api/sod-exceptions/{eid}", headers=auth("head")))["tier"] == "COMMITTEE" else "head"
    reviewed = ok(client.post(f"/api/sod-exceptions/{eid}/review", json={"rationale": "Still needed until quorum returns."}, headers=auth(reviewer)))
    assert reviewed["last_review_note"] and reviewed["history"][-1]["action"] == "REVIEWED"


def test_out_of_order_transitions_are_refused(client, auth, member, conflicted_case):
    eid = ok(_request(client, auth("analyst"), member["id"], conflicted_case), 201)["id"]
    body = {"decision": "APPROVE", "rationale": "x"}
    assert client.post(f"/api/sod-exceptions/{eid}/decision", json=body, headers=auth("head")).status_code == 409  # still a draft
    assert client.post(f"/api/sod-exceptions/{eid}/revoke", json={"rationale": "x"}, headers=auth("admin")).status_code == 409
    ok(client.post(f"/api/sod-exceptions/{eid}/submit", headers=auth("analyst")))
    assert client.post(f"/api/sod-exceptions/{eid}/submit", headers=auth("analyst")).status_code == 409


def test_request_content_and_history_are_immutable(client, auth, member, conflicted_case):
    eid = ok(_request(client, auth("analyst"), member["id"], conflicted_case), 201)["id"]
    ok(client.post(f"/api/sod-exceptions/{eid}/submit", headers=auth("analyst")))
    db = SessionLocal()
    try:
        row = db.get(SodException, eid)
        row.business_justification = "rewritten after submission"
        with pytest.raises(ProtectedDataError):
            db.flush()
        db.rollback()
        event = db.query(SodExceptionEvent).filter(SodExceptionEvent.exception_id == eid).first()
        event.detail = "rewritten"
        with pytest.raises(ProtectedDataError):
            db.flush()
    finally:
        db.rollback()
        db.close()


def test_exceptions_are_visible_only_to_parties_and_governance(client, auth, member, conflicted_case):
    eid = ok(_request(client, auth("analyst"), member["id"], conflicted_case), 201)["id"]
    _user("p3outsider@test.io", "FCRM_ANALYST")
    outsider = _login(client, "p3outsider@test.io")
    assert client.get(f"/api/sod-exceptions/{eid}", headers=outsider).status_code == 403
    assert client.get("/api/sod-exceptions?scope=all", headers=outsider).status_code == 403
    assert ok(client.get(f"/api/sod-exceptions/{eid}", headers=auth("governance")))["id"] == eid


# -- Admin vs committee (R-GOV-02) ---------------------------------------------


@pytest.fixture
def dual_admin(users):
    return {"id": _user("p3admin@test.io", "ADMIN", manager_id=users["manager"])}


def test_admin_votes_only_through_an_approved_declared_dual_role_exception(client, auth, users, create_assessment, no_challenge_triggers, dual_admin):
    aid = _committee_ready(client, auth, users, create_assessment)
    other = _committee_ready(client, auth, users, create_assessment)
    admin = _login(client, "p3admin@test.io")
    assert _vote(client, admin, aid).status_code == 403

    eid = ok(_request(client, auth("analyst"), dual_admin["id"], aid, exception_type="ADMIN_COMMITTEE_DUAL_ROLE"), 201)["id"]
    ok(client.post(f"/api/sod-exceptions/{eid}/submit", headers=auth("analyst")))
    ok(client.post(f"/api/sod-exceptions/{eid}/decision", json={"decision": "APPROVE", "rationale": "Interim cover."}, headers=auth("governance")))
    assert _vote(client, admin, aid).status_code == 403  # no declaration yet
    ok(client.post(f"/api/sod-exceptions/{eid}/declaration", json={"rationale": "No interest in this case."}, headers=admin))

    ok(_vote(client, admin, aid))
    assert _events("SOD_EXCEPTION_USED", aid, dual_admin["id"])
    assert _vote(client, admin, other).status_code == 403  # the exception covers one assessment

    # Same-case administrative actions are refused; another admin must act.
    assign = client.post(f"/api/assessments/{aid}/workflow/assign", json={"user_id": users["analyst"], "reason": "x"}, headers=admin)
    assert assign.status_code == 403 and "another administrator" in assign.json()["detail"]
    # Global configuration affects every case, the covered one included.
    config = client.patch("/api/challenge-triggers", json={"residual_risk_tolerance": 50}, headers=admin)
    assert config.status_code == 403 and "another administrator" in config.json()["detail"]
    user_change = client.patch(f"/api/users/{users['analyst']}", json={"full_name": "Renamed"}, headers=admin)
    assert user_change.status_code == 403
    # On a different case the restriction does not apply.
    elsewhere = client.post(f"/api/assessments/{other}/workflow/assign", json={"user_id": users["analyst"], "reason": "x"}, headers=admin)
    assert "another administrator" not in elsewhere.text
    assert len(_events("ACCESS_DENIED", aid, dual_admin["id"])) >= 2

    ok(client.post(f"/api/sod-exceptions/{eid}/revoke", json={"rationale": "Cover ended."}, headers=auth("admin")))
    assert _vote(client, admin, aid, "DISSENT", recast_reason="x").status_code == 403


def test_enterprise_wide_dual_role_needs_the_chair(client, auth, dual_admin):
    eid = ok(_request(client, auth("analyst"), dual_admin["id"], None, exception_type="ADMIN_COMMITTEE_DUAL_ROLE"), 201)["id"]
    pending = ok(client.post(f"/api/sod-exceptions/{eid}/submit", headers=auth("analyst")))
    assert pending["tier"] == "COMMITTEE" and any("enterprise-wide" in r for r in pending["tier_reasons"])
    ok(client.post(f"/api/sod-exceptions/{eid}/decision", json={"decision": "REJECT", "rationale": "Too broad."}, headers=auth("chair")))


# -- designations ----------------------------------------------------------------


def test_designations_are_admin_assigned_validated_and_audited(client, auth, users):
    target = _user("p3designee@test.io", "FCRM_ANALYST")
    url = f"/api/users/{target}/designations"
    assert client.put(url, json={"designations": ["SENIOR_ANALYST"], "reason": "x"}, headers=auth("manager")).status_code == 403
    wrong = client.put(url, json={"designations": ["HEAD_OF_FCRM"], "reason": "x"}, headers=auth("admin"))
    assert wrong.status_code == 422 and "needs role MANAGER" in wrong.json()["detail"]
    updated = ok(client.put(url, json={"designations": ["SENIOR_ANALYST"], "reason": "Promoted."}, headers=auth("admin")))
    assert updated["governance_designations"] == ["SENIOR_ANALYST"]
    assert _events("USER_DESIGNATIONS_CHANGED")
    admin_id = _user("admin@example.com", "ADMIN")
    self_change = client.put(f"/api/users/{admin_id}/designations", json={"designations": [], "reason": "x"}, headers=auth("admin"))
    assert self_change.status_code == 403


# -- overrides (R-GOV-03/04) -------------------------------------------------------


def _readiness(client, auth, aid, purpose="COMMITTEE_SUBMISSION"):
    return ok(client.get(f"/api/assessments/{aid}/readiness?purpose={purpose}", headers=auth("manager")))


def _codes(readiness):
    return {b["code"] for b in readiness["blockers"]}


def test_material_override_is_proposed_reviewed_and_approved_by_different_people(client, auth, users, create_assessment, no_challenge_triggers):
    aid = create_assessment()["id"]
    _at(aid, "HUMAN_REVIEW", manager_id=users["manager"])

    assert client.post(
        f"/api/assessments/{aid}/overrides",
        json={"section": "INTAKE_FIELD", "field_name": "countries_jurisdictions", "human_value": "Germany, Poland, Ukraine", "reason": "x"},
        headers=auth("manager"),
    ).status_code == 403  # only an FCRM Analyst proposes

    entry = ok(
        client.post(
            f"/api/assessments/{aid}/overrides",
            json={"section": "INTAKE_FIELD", "field_name": "countries_jurisdictions", "human_value": "Germany, Poland, Ukraine", "reason": "Missed jurisdiction."},
            headers=auth("senior"),
        ),
        201,
    )
    assert entry["materiality"] == "MATERIAL" and entry["state"] == "PENDING_REVIEW"
    assert "OVERRIDE_PENDING_REVIEW" in _codes(_readiness(client, auth, aid))

    review = f"/api/assessments/{aid}/overrides/{entry['id']}/review"
    approval = f"/api/assessments/{aid}/overrides/{entry['id']}/approval"
    assert client.patch(approval, json={"decision": "APPROVE", "rationale": "x"}, headers=auth("head")).status_code == 409  # before review
    self_review = client.patch(review, json={"decision": "CONFIRM", "note": "x"}, headers=auth("senior"))
    assert self_review.status_code == 403 and "proposed this override" in self_review.json()["detail"]

    reviewed = ok(client.patch(review, json={"decision": "CONFIRM", "note": "Supported by the contract."}, headers=auth("manager")))
    assert reviewed["state"] == "PENDING_APPROVAL" and reviewed["approval_status"] == "PENDING"
    assert "OVERRIDE_PENDING_APPROVAL" in _codes(_readiness(client, auth, aid))

    by_reviewer = client.patch(approval, json={"decision": "APPROVE", "rationale": "x"}, headers=auth("manager"))
    assert by_reviewer.status_code == 403 and "reviewed this override" in by_reviewer.json()["detail"]
    approved = ok(client.patch(approval, json={"decision": "APPROVE", "rationale": "Material and justified."}, headers=auth("head")))
    assert approved["state"] == "APPROVED" and approved["approved_by_id"] == users["head"]
    assert not {"OVERRIDE_PENDING_REVIEW", "OVERRIDE_PENDING_APPROVAL"} & _codes(_readiness(client, auth, aid))
    assert _events("OVERRIDE_APPROVAL_DECIDED", aid, users["head"])


def test_nonmaterial_correction_does_not_block(client, auth, users, create_assessment, no_challenge_triggers):
    aid = create_assessment()["id"]
    _at(aid, "HUMAN_REVIEW", manager_id=users["manager"])
    entry = ok(
        client.post(
            f"/api/assessments/{aid}/overrides",
            json={"section": "INTAKE_FIELD", "field_name": "title", "human_value": "Merchant acquiring (DE)", "reason": "Typo."},
            headers=auth("analyst"),
        ),
        201,
    )
    assert entry["materiality"] == "NONMATERIAL"
    assert not {c for c in _codes(_readiness(client, auth, aid)) if c.startswith("OVERRIDE")}


def _inherent_override(client, auth, aid, value):
    return ok(client.post(f"/api/assessments/{aid}/inherent-risk/override", json={"override_value": value, "reason": "Analyst judgement."}, headers=auth("analyst")))


def _ledger(client, auth, aid, section):
    return [e for e in ok(client.get(f"/api/assessments/{aid}/overrides", headers=auth("manager"))) if e["section"] == section]


def test_a_rejected_material_override_blocks_until_corrected(client, auth, users, create_assessment, no_challenge_triggers):
    aid = create_assessment()["id"]
    _at(aid, "HUMAN_REVIEW", manager_id=users["manager"])
    _inherent_override(client, auth, aid, 45)  # LOW -> MEDIUM
    entry = _ledger(client, auth, aid, "INHERENT_RISK")[-1]
    assert entry["materiality"] == "MATERIAL" and entry["origin"] == "TYPED"

    ok(client.patch(f"/api/assessments/{aid}/overrides/{entry['id']}/review", json={"decision": "REJECT", "note": "Not supported."}, headers=auth("manager")))
    readiness = _readiness(client, auth, aid)
    assert "OVERRIDE_REJECTED_IN_EFFECT" in _codes(readiness)
    assert readiness["overrides"]["rejected_in_effect"][0]["id"] == entry["id"]

    # Corrected: overridden back within the calculated band (non-material).
    _inherent_override(client, auth, aid, 38)
    assert _ledger(client, auth, aid, "INHERENT_RISK")[-1]["materiality"] == "NONMATERIAL"
    assert "OVERRIDE_REJECTED_IN_EFFECT" not in _codes(_readiness(client, auth, aid))


def test_a_critical_override_needs_the_head_of_fcrm(client, auth, users, create_assessment, no_challenge_triggers):
    aid = create_assessment()["id"]
    _at(aid, "HUMAN_REVIEW", manager_id=users["manager"])
    _inherent_override(client, auth, aid, 70)  # LOW -> HIGH: two levels
    entry = _ledger(client, auth, aid, "INHERENT_RISK")[-1]
    assert entry["materiality"] == "CRITICAL"
    ok(client.patch(f"/api/assessments/{aid}/overrides/{entry['id']}/review", json={"decision": "CONFIRM", "note": "ok"}, headers=auth("senior")))
    approval = f"/api/assessments/{aid}/overrides/{entry['id']}/approval"
    refused = client.patch(approval, json={"decision": "APPROVE", "rationale": "x"}, headers=auth("manager"))
    assert refused.status_code == 403 and "Head Of Fcrm" in refused.json()["detail"]
    ok(client.patch(approval, json={"decision": "APPROVE", "rationale": "Escalated and agreed."}, headers=auth("head")))
    assert any(w["code"] == "CRITICAL_OVERRIDES" for w in _readiness(client, auth, aid)["warnings"])


# -- challenge review independence (R-GOV-03) ---------------------------------


def test_an_involved_analyst_cannot_perform_the_challenge_review(client, auth, users, analysed_assessment):
    aid = analysed_assessment()["id"]
    factor = next(f for f in ok(client.get(f"/api/assessments/{aid}/risk-factors", headers=auth("senior"))) if f["applicable"])
    ok(client.patch(f"/api/assessments/{aid}/risk-factors/{factor['id']}/rating", json={"likelihood": 3, "impact": 3, "reason": "x"}, headers=auth("senior")))
    _set(aid, status="SUBMITTED_TO_MANAGER", manager_id=users["manager"])
    refused = _review(client, auth, aid)
    assert refused.status_code == 403 and "not independent" in refused.json()["detail"]


def test_the_reviewer_cannot_sign_off_their_own_review(client, auth, users, create_assessment, no_challenge_triggers):
    aid = create_assessment()["id"]
    _at(aid, "SUBMITTED_TO_MANAGER", manager_id=users["head"])
    _user("p3qa-manager@test.io", "MANAGER", designations=["QA_REVIEWER"])
    qa = _login(client, "p3qa-manager@test.io")
    ok(client.post(f"/api/assessments/{aid}/challenge-review/review", json={"reason": "Reviewed."}, headers=qa), 201)
    own = client.post(f"/api/assessments/{aid}/challenge-review/signoff", json={"reason": "x"}, headers=qa)
    assert own.status_code == 403
    # An unassigned FCRM Manager can't sign off; the Head of FCRM can.
    assert _sign_off(client, auth, aid, "manager").status_code == 403
    signed = ok(_sign_off(client, auth, aid, "head"), 201)
    assert signed["stage"] == "SIGNOFF"


def test_a_critical_case_is_escalated_to_the_committee(client, auth, users, create_assessment, no_challenge_triggers):
    aid = create_assessment()["id"]
    _at(aid, "SUBMITTED_TO_MANAGER", manager_id=users["manager"])
    db = SessionLocal()
    try:
        db.execute(text("UPDATE inherent_risk_calculations SET calculated_band = 'CRITICAL' WHERE assessment_id = :a"), {"a": aid})
        db.commit()
    finally:
        db.close()
    ok(_review(client, auth, aid), 201)
    signed = ok(_sign_off(client, auth, aid), 201)
    assert signed["committee_escalation"] is True
    assert any(w["code"] == "CRITICAL_ESCALATED" for w in _readiness(client, auth, aid)["warnings"])


# -- readiness: findings and enforcement (R-GOV-04) ---------------------------


def _finding(aid, severity):
    db = SessionLocal()
    try:
        row = ChallengeFinding(assessment_id=aid, category="CONTRADICTION", description=f"{severity} finding", related_section="EVIDENCE", severity=severity)
        db.add(row)
        db.commit()
        return row.id
    finally:
        db.close()


def test_medium_findings_block_the_final_decision_low_findings_never(client, auth, users, create_assessment, no_challenge_triggers):
    # G-4 (2026-10-03): an open MEDIUM finding doesn't stop submission (a
    # warning), but blocks the final decision until resolved or accepted
    # as a Committee exception (test_committee_quorum_and_findings.py).
    aid = create_assessment()["id"]
    _at(aid, "SUBMITTED_TO_MANAGER", manager_id=users["manager"])
    _medium, _low = _finding(aid, "MEDIUM"), _finding(aid, "LOW")
    readiness = _readiness(client, auth, aid)
    assert "MEDIUM_FINDINGS_OPEN" not in _codes(readiness)
    assert {"MEDIUM_FINDINGS_OPEN", "LOW_FINDINGS_OPEN"} <= {w["code"] for w in readiness["warnings"]}
    final = _readiness(client, auth, aid, purpose="FINAL_DECISION")
    assert "MEDIUM_FINDINGS_OPEN" in _codes(final) and "LOW_FINDINGS_OPEN" not in _codes(final)


def test_readiness_changes_only_after_the_governance_actions_and_is_enforced(client, auth, users, create_assessment, no_challenge_triggers):
    aid = create_assessment()["id"]
    _at(aid, "SUBMITTED_TO_MANAGER", manager_id=users["manager"])
    entry = ok(
        client.post(
            f"/api/assessments/{aid}/overrides",
            json={"section": "INTAKE_FIELD", "field_name": "delivery_channels", "human_value": "Web portal; API; mobile app", "reason": "Mobile channel missing."},
            headers=auth("analyst"),
        ),
        201,
    )
    readiness = _readiness(client, auth, aid)
    assert readiness["status"] == "BLOCKED" and readiness["policy_status"].startswith("PROVISIONAL")
    assert {"CHALLENGE_REVIEW_INCOMPLETE", "OVERRIDE_PENDING_REVIEW"} <= _codes(readiness)
    assert all(b["responsible_role"] and b["next_action"] for b in readiness["blockers"])

    # Enforced at submission, and the refusal is audited.
    blocked = client.post(f"/api/assessments/{aid}/manager-decision", json={"decision": "approve", "comment": "ok"}, headers=auth("manager"))
    assert blocked.status_code == 409 and blocked.json()["detail"]["blockers"]
    assert _events("GOVERNANCE_READINESS_BLOCKED", aid)

    ok(_review(client, auth, aid), 201)
    ok(_sign_off(client, auth, aid), 201)
    ok(client.patch(f"/api/assessments/{aid}/overrides/{entry['id']}/review", json={"decision": "CONFIRM", "note": "ok"}, headers=auth("senior")))
    assert _codes(_readiness(client, auth, aid)) == {"OVERRIDE_PENDING_APPROVAL"}
    ok(client.patch(f"/api/assessments/{aid}/overrides/{entry['id']}/approval", json={"decision": "APPROVE", "rationale": "ok"}, headers=auth("head")))
    assert _readiness(client, auth, aid)["ready"] is True
    ok(client.post(f"/api/assessments/{aid}/manager-decision", json={"decision": "approve", "comment": "ok"}, headers=auth("manager")))


def test_the_final_decision_is_blocked_by_a_late_pending_override(client, auth, users, create_assessment, no_challenge_triggers):
    aid = _committee_ready(client, auth, users, create_assessment)
    ok(_vote(client, auth("committee"), aid))  # opens Committee Review
    ok(
        client.post(
            f"/api/assessments/{aid}/overrides",
            json={"section": "INTAKE_FIELD", "field_name": "third_party_vendor_usage", "human_value": "Two processors", "reason": "Second processor found."},
            headers=auth("analyst"),
        ),
        201,
    )
    decision = client.post(f"/api/assessments/{aid}/committee-decision", json={"decision": "approve", "rationale": "ok"}, headers=auth("committee"))
    assert decision.status_code == 409 and "OVERRIDE_PENDING_REVIEW" in {b["code"] for b in decision.json()["detail"]["blockers"]}
    # Deferral is not final and stays possible.
    deferred = client.post(f"/api/assessments/{aid}/committee-decision", json={"decision": "defer", "rationale": "Await the review."}, headers=auth("committee"))
    assert deferred.status_code == 200, deferred.text


def test_policy_is_reported_as_provisional(client, auth):
    body = ok(client.get("/api/governance/policy", headers=auth("analyst")))
    assert body["policy_status"] == "PROVISIONAL_PENDING_GOVERNANCE_APPROVAL"
    assert body["policy"]["exception_approvers"]["COMMITTEE"]["designations"] == ["COMMITTEE_CHAIR"]

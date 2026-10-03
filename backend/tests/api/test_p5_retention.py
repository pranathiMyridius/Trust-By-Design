"""
P5 (R16.1, R16.3, R16.4): versioned retention policy, independent approval
(D-1), legal-hold history and independent release (D-2), the one
eligibility rule, the read-only eligibility report (D-4), dedicated audit
actions and ORM immutability. Policy is PROVISIONAL.
"""

from __future__ import annotations

import copy
import json
from datetime import timedelta

import pytest

from app.auth.access import _GLOBAL_ADMIN_PATHS
from app.database import SessionLocal
from app.governance import policy as policy_module
from app.governance import retention as retention_rules
from app.governance.policy import DEFAULT_POLICY, POLICY_STATUS
from app.models.assessment import Assessment
from app.models.audit_trail import AssessmentRetention, LegalHoldEvent, RetentionPolicy, RetentionPolicyVersion
from app.services.data_protection import ProtectedDataError
from tests.api.test_p3_sod import _events, _login, _user
from tests.conftest import ok

REASON = "Annual retention review by the FCRM governance forum"


# -- fixtures and helpers -------------------------------------------------------


@pytest.fixture
def people():
    return {
        "compliance": _login_as("p5compliance@test.io", "MANAGER", ["COMPLIANCE_MANAGER"]),
        "plain_manager": _login_as("p5manager@test.io", "MANAGER"),
        "auditor": _login_as("p5auditor@test.io", "AUDITOR"),
        "executive": _login_as("p5exec@test.io", "EXECUTIVE"),
    }


_TOKENS: dict[str, dict] = {}


def _login_as(email, role, designations=None):
    from fastapi.testclient import TestClient

    from app.main import app

    _user(email, role, designations)
    if email not in _TOKENS:
        _TOKENS[email] = _login(TestClient(app), email)
    return _TOKENS[email]


@pytest.fixture
def retention_config(tmp_path, monkeypatch):
    """Override the "retention" policy section for one test."""

    def apply(**changes):
        config = copy.deepcopy(DEFAULT_POLICY["retention"])
        config.update(changes)
        path = tmp_path / "governance_policy.json"
        path.write_text(json.dumps({"retention": config}), encoding="utf-8")
        monkeypatch.setenv("GOVERNANCE_POLICY_FILE", str(path))
        policy_module.reload()

    yield apply
    monkeypatch.delenv("GOVERNANCE_POLICY_FILE", raising=False)
    policy_module.reload()


@pytest.fixture(autouse=True)
def restore_default_period(client, auth):
    """Tests share one database: put the ASSESSMENT period back to the
    provisional 2,555 days -- through the governed workflow, as a real
    change would be."""

    yield
    policy_module.reload()
    db = SessionLocal()
    try:
        pending = retention_rules.pending_version(db)
        active = retention_rules.active_version(db)
        pending_id = pending.id if pending else None
        active_days = active.retention_days if active else None
    finally:
        db.close()
    compliance = _login_as("p5compliance@test.io", "MANAGER", ["COMPLIANCE_MANAGER"])
    if pending_id:
        ok(client.post(f"/api/retention/policies/{pending_id}/decision", json={"decision": "REJECT", "reason": "test cleanup"}, headers=compliance))
    if active_days not in (None, 2555):
        proposal = ok(
            client.post("/api/retention/policies", json={"retention_days": 2555, "change_reason": "Restore the provisional default (test cleanup)"}, headers=auth("admin")),
            201,
        )
        ok(client.post(f"/api/retention/policies/{proposal['id']}/decision", json={"decision": "APPROVE", "reason": "test cleanup"}, headers=compliance))


def _propose(client, headers, days, reason=REASON, record_type="ASSESSMENT"):
    return client.post("/api/retention/policies", json={"record_type": record_type, "retention_days": days, "change_reason": reason}, headers=headers)


def _decide(client, headers, version_id, decision="APPROVE", reason="Reviewed against the retention schedule"):
    return client.post(f"/api/retention/policies/{version_id}/decision", json={"decision": decision, "reason": reason}, headers=headers)


def _decided(create_assessment, days_ago: float | None, status: str = "APPROVED", **overrides) -> int:
    aid = create_assessment(**overrides)["id"]
    db = SessionLocal()
    try:
        assessment = db.get(Assessment, aid)
        assessment.status = status
        assessment.committee_decided_at = (retention_rules.utcnow() - timedelta(days=days_ago)) if days_ago is not None else None
        db.commit()
    finally:
        db.close()
    return aid


def _hold(client, headers, aid, hold=True, reason="Litigation hold requested by Legal", matter=None):
    body = {"hold": hold, "reason": reason}
    if matter:
        body["matter_reference"] = matter
    return client.patch(f"/api/assessments/{aid}/retention/legal-hold", json=body, headers=headers)


def _version(version_id: int) -> RetentionPolicyVersion:
    db = SessionLocal()
    try:
        version = db.get(RetentionPolicyVersion, version_id)
        db.expunge(version)
        return version
    finally:
        db.close()


# -- policy versions and independent approval (D-1) -----------------------------


def test_seeded_version_is_provisional_and_read_through(client, auth):
    legacy = ok(client.get("/api/retention-policy", headers=auth("owner")))
    assert legacy["default_retention_days"] == 2555
    assert legacy["policy_status"] == POLICY_STATUS == "PROVISIONAL_PENDING_GOVERNANCE_APPROVAL"
    assert legacy["active_version_id"] is not None

    listing = ok(client.get("/api/retention/policies", headers=auth("admin")))
    assert listing["policy_status"] == POLICY_STATUS
    assessment = next(rt for rt in listing["record_types"] if rt["record_type"] == "ASSESSMENT")
    v1 = next(v for v in assessment["versions"] if v["version"] == 1)
    assert v1["system_seeded"] and v1["retention_days"] == 2555
    assert v1["change_reason"] == "Migrated existing default; not compliance-approved"
    # The application never records a governance approval.
    assert all(v["governance_approval_reference"] is None and v["governance_approved_at"] is None for v in assessment["versions"])
    assert assessment["basis"] == "FINAL_DECISION_DATE"


def test_independent_approval_creates_a_new_version_and_keeps_history(client, auth, users, people):
    before = ok(client.get("/api/retention/policies", headers=auth("admin")))
    old_active = next(rt for rt in before["record_types"] if rt["record_type"] == "ASSESSMENT")["active"]

    proposal = ok(_propose(client, auth("admin"), 3000), 201)
    assert proposal["status"] == "PROPOSED" and proposal["previous_retention_days"] == old_active["retention_days"]
    assert proposal["supersedes_id"] == old_active["id"] and proposal["policy_status"] == POLICY_STATUS
    # Nothing changed yet.
    assert ok(client.get("/api/retention-policy", headers=auth("owner")))["default_retention_days"] == 2555
    assert ok(client.get("/api/retention-policy", headers=auth("owner")))["pending_retention_days"] == 3000

    # Only one open proposal per record type.
    assert _propose(client, auth("admin"), 3100).status_code == 409

    # An Admin can propose but not approve -- not even someone else's.
    denied_before = len(_events("ACCESS_DENIED"))
    refused = _decide(client, auth("admin"), proposal["id"])
    assert refused.status_code == 403 and "You proposed this change" in refused.json()["detail"]
    other_admin = _login_as("p5admin2@test.io", "ADMIN")
    refused = _decide(client, other_admin, proposal["id"])
    assert refused.status_code == 403 and "An Admin can propose" in refused.json()["detail"]
    # A Manager without an approver designation can't either.
    assert _decide(client, people["plain_manager"], proposal["id"]).status_code == 403
    assert len(_events("ACCESS_DENIED")) == denied_before + 3
    # A reason is mandatory.
    assert _decide(client, auth("governance"), proposal["id"], reason="  ").status_code == 422

    approved = ok(_decide(client, auth("governance"), proposal["id"]))
    assert approved["status"] == "ACTIVE" and approved["effective_from"] and approved["decided_by"] == "Governance"
    # A second decision on the same proposal is refused.
    assert _decide(client, people["compliance"], proposal["id"]).status_code == 409

    old = _version(old_active["id"])
    assert old.status == "SUPERSEDED" and old.superseded_by_id == proposal["id"]
    # The historical version's values are untouched.
    assert (old.retention_days, old.change_reason, old.effective_from is not None) == (
        old_active["retention_days"], old_active["change_reason"], True,
    )
    assert ok(client.get("/api/retention-policy", headers=auth("owner")))["default_retention_days"] == 3000

    # Only the approved version is in force; the earlier one applied before it.
    db = SessionLocal()
    try:
        assert retention_rules.effective_version(db, "ASSESSMENT").id == proposal["id"]
        assert retention_rules.effective_version(db, "ASSESSMENT", old.effective_from).id == old.id
        assert db.query(RetentionPolicyVersion).filter(RetentionPolicyVersion.status == "ACTIVE", RetentionPolicyVersion.record_type == "ASSESSMENT").count() == 1
    finally:
        db.close()

    # Dedicated audit actions, with previous and new values and the reason.
    proposed = [e for e in _events("RETENTION_POLICY_PROPOSED") if f"v{proposal['version']} " in e.details][-1]
    assert f"{old_active['retention_days']} -> 3000 days" in proposed.details and REASON in proposed.details
    assert POLICY_STATUS in proposed.details
    assert any(f"v{proposal['version']} approved" in e.details for e in _events("RETENTION_POLICY_APPROVED", actor_id=users["governance"]))
    assert any(f"v{proposal['version']} in force" in e.details for e in _events("RETENTION_POLICY_ACTIVATED"))
    assert any(f"v{old_active['version']} " in e.details for e in _events("RETENTION_POLICY_SUPERSEDED"))


def test_self_approval_is_refused_even_for_an_eligible_approver(client, auth, users, retention_config):
    # A configuration where a governance owner may also propose.
    retention_config(proposers={"roles": ["ADMIN"], "designations": ["FCRM_GOVERNANCE_OWNER"]})
    proposal = ok(_propose(client, auth("governance"), 3200), 201)
    refused = _decide(client, auth("governance"), proposal["id"])
    assert refused.status_code == 403 and "You proposed this change" in refused.json()["detail"]
    assert any("You proposed this change" in e.details for e in _events("ACCESS_DENIED", actor_id=users["governance"]))
    assert _version(proposal["id"]).status == "PROPOSED"


def test_rejection_needs_a_reason_and_changes_nothing(client, auth, people):
    proposal = ok(_propose(client, auth("admin"), 4000), 201)
    rejected = ok(_decide(client, people["compliance"], proposal["id"], "REJECT", "Not supported by the retention schedule"))
    assert rejected["status"] == "REJECTED" and rejected["decision_reason"] == "Not supported by the retention schedule"
    assert ok(client.get("/api/retention-policy", headers=auth("owner")))["default_retention_days"] == 2555
    assert any("rejected" in e.details for e in _events("RETENTION_POLICY_REJECTED"))
    # A rejected version can't be approved later.
    assert _decide(client, auth("governance"), proposal["id"]).status_code == 409


@pytest.mark.parametrize(
    "days, reason, record_type, fragment",
    [
        (100, REASON, "ASSESSMENT", "between 365 and 36500"),
        (40000, REASON, "ASSESSMENT", "between 365 and 36500"),
        (3000, "too short", "ASSESSMENT", "at least 20 characters"),
        (3000, REASON, "DOCUMENT", "Unknown record type"),
        (2555, REASON, "ASSESSMENT", "already 2555 days"),
    ],
)
def test_invalid_proposals_are_rejected(client, auth, days, reason, record_type, fragment):
    response = _propose(client, auth("admin"), days, reason, record_type)
    assert response.status_code == 422 and fragment in response.json()["detail"]


def test_only_proposers_may_propose(client, auth, users, people):
    assert _propose(client, auth("manager"), 3000).status_code == 403
    assert _propose(client, auth("governance"), 3000).status_code == 403
    # Read-only roles are refused by the global gate.
    assert _propose(client, people["auditor"], 3000).status_code == 403


def test_legacy_patch_creates_a_proposal_instead_of_editing_in_place(client, auth):
    db = SessionLocal()
    try:
        legacy_days = db.query(RetentionPolicy).filter(RetentionPolicy.is_active.is_(True)).first().default_retention_days
    finally:
        db.close()

    missing_reason = client.patch("/api/retention-policy", json={"default_retention_days": 3300}, headers=auth("admin"))
    assert missing_reason.status_code == 422
    body = ok(client.patch("/api/retention-policy", json={"default_retention_days": 3300, "change_reason": REASON}, headers=auth("admin")))
    assert body["default_retention_days"] == 2555 and body["pending_retention_days"] == 3300
    assert body["policy_status"] == POLICY_STATUS

    db = SessionLocal()
    try:
        assert db.query(RetentionPolicy).filter(RetentionPolicy.is_active.is_(True)).first().default_retention_days == legacy_days
    finally:
        db.close()
    assert not [e for e in _events("STATUS_CHANGE") if "Retention policy updated" in (e.details or "")]


def test_without_independent_approval_the_change_applies_at_once_but_is_still_versioned(client, auth, retention_config):
    retention_config(require_independent_approval=False)
    version = ok(_propose(client, auth("admin"), 3400), 201)
    assert version["status"] == "ACTIVE" and version["decided_by"] == "Default Admin"
    assert ok(client.get("/api/retention-policy", headers=auth("owner")))["default_retention_days"] == 3400
    assert any(f"v{version['version']} in force" in e.details for e in _events("RETENTION_POLICY_ACTIVATED"))
    assert ok(client.get("/api/retention/permissions", headers=auth("admin")))["require_independent_approval"] is False


# -- legal holds (D-2) -----------------------------------------------------------


def test_legal_hold_history_and_independent_release(client, auth, users, people, create_assessment):
    aid = create_assessment()["id"]  # not finally decided: a hold can still be placed

    assert _hold(client, auth("analyst"), aid).status_code == 403
    assert _hold(client, auth("admin"), aid, reason=" ").status_code == 422
    assert _hold(client, auth("admin"), aid, matter="x" * 101).status_code == 422

    held = ok(_hold(client, auth("admin"), aid, matter="LIT-2026-014"))
    assert held["legal_hold"] and held["eligibility"]["eligibility_status"] == "LEGAL_HOLD" and not held["eligibility"]["eligible"]
    assert held["policy_status"] == POLICY_STATUS
    assert held["actions"]["release_hold"]["allowed"] is False  # the setter
    assert _hold(client, auth("admin"), aid).status_code == 409  # already held

    # The person who set it can't release it, and the refusal is logged.
    refused = _hold(client, auth("admin"), aid, hold=False, reason="Matter closed")
    assert refused.status_code == 403 and "different authorized user" in refused.json()["detail"]
    assert any("different authorized user" in e.details for e in _events("ACCESS_DENIED", aid, users.get("admin")))
    # A release needs a reason.
    assert _hold(client, people["compliance"], aid, hold=False, reason="").status_code == 422

    released = ok(_hold(client, people["compliance"], aid, hold=False, reason="Matter closed by Legal on 2026-10-01"))
    assert released["legal_hold"] is False
    assert [(h["action"], h["actor"]) for h in released["hold_history"]] == [("SET", "Default Admin"), ("RELEASED", "P5Compliance")]
    assert released["hold_history"][0]["matter_reference"] == "LIT-2026-014"
    assert _hold(client, people["compliance"], aid, hold=False, reason="again please").status_code == 409

    set_event = _events("LEGAL_HOLD_SET", aid)[-1]
    assert "Litigation hold requested by Legal" in set_event.details and set_event.new_status == "HELD"
    release_event = _events("LEGAL_HOLD_RELEASED", aid)[-1]
    assert "Matter closed by Legal" in release_event.details and "set by Default Admin" in release_event.details
    assert not [e for e in _events("STATUS_CHANGE", aid) if "Legal hold" in (e.details or "")]

    # Hold reasons are for retention administrators; the owner sees the flag only.
    ok(_hold(client, auth("admin"), aid, reason="Regulatory inquiry received"))
    owner_view = ok(client.get(f"/api/assessments/{aid}/retention", headers=auth("owner")))
    assert owner_view["legal_hold"] and owner_view["legal_hold_reason"] is None and owner_view["hold_history"] == []
    assert owner_view["hold_detail_visible"] is False
    ok(_hold(client, people["compliance"], aid, hold=False, reason="Inquiry closed without findings"))


def test_hold_events_and_versions_are_immutable(client, auth, create_assessment, people):
    aid = create_assessment()["id"]
    ok(_hold(client, auth("admin"), aid))
    ok(_hold(client, people["compliance"], aid, hold=False, reason="Released for the immutability test"))

    db = SessionLocal()
    try:
        event = db.query(LegalHoldEvent).filter(LegalHoldEvent.assessment_id == aid).first()
        event.reason = "rewritten"
        with pytest.raises(ProtectedDataError):
            db.flush()
        db.rollback()
        event = db.query(LegalHoldEvent).filter(LegalHoldEvent.assessment_id == aid).first()
        db.delete(event)
        with pytest.raises(ProtectedDataError):
            db.flush()
        db.rollback()

        version = retention_rules.active_version(db)
        version.retention_days = 9999
        with pytest.raises(ProtectedDataError):
            db.flush()
        db.rollback()
        version = retention_rules.active_version(db)
        version.status = "PROPOSED"
        with pytest.raises(ProtectedDataError):
            db.flush()
        db.rollback()
        version = retention_rules.active_version(db)
        db.delete(version)
        with pytest.raises(ProtectedDataError):
            db.flush()
        db.rollback()
        with pytest.raises(ProtectedDataError):
            db.query(RetentionPolicyVersion).update({"retention_days": 1})
        db.rollback()
    finally:
        db.close()


# -- eligibility and soft delete --------------------------------------------------


def test_soft_delete_uses_the_shared_rule_and_records_the_version(client, auth, users, people, create_assessment):
    old = _decided(create_assessment, days_ago=3000)
    recent = _decided(create_assessment, days_ago=10)
    open_case = create_assessment()["id"]

    view = ok(client.get(f"/api/assessments/{old}/retention", headers=auth("admin")))
    assert view["eligibility"]["eligibility_status"] == "ELIGIBLE" and view["retention_expired"]
    assert view["actions"]["soft_delete"]["allowed"]

    # A hold blocks it.
    ok(_hold(client, people["compliance"], old))
    blocked = client.post(f"/api/assessments/{old}/soft-delete", json={"reason": "Retention elapsed"}, headers=auth("admin"))
    assert blocked.status_code == 409 and "legal hold" in blocked.json()["detail"]
    ok(_hold(client, auth("admin"), old, hold=False, reason="Hold lifted after review by Legal"))

    # Not yet eligible, and not finally decided.
    early = client.post(f"/api/assessments/{recent}/soft-delete", json={"reason": "Retention elapsed"}, headers=auth("admin"))
    assert early.status_code == 409 and "Within the 2555-day retention period" in early.json()["detail"]
    undecided = client.post(f"/api/assessments/{open_case}/soft-delete", json={"reason": "Retention elapsed"}, headers=auth("admin"))
    assert undecided.status_code == 409 and "No final decision yet" in undecided.json()["detail"]
    # Only an Admin soft-deletes.
    assert client.post(f"/api/assessments/{old}/soft-delete", json={"reason": "x"}, headers=people["compliance"]).status_code == 403

    deleted = ok(client.post(f"/api/assessments/{old}/soft-delete", json={"reason": "Retention elapsed; reviewed"}, headers=auth("admin")))
    assert deleted["is_deleted"] and deleted["retention_policy_version_id"] == view["policy_version_id"]
    assert deleted["eligibility"]["eligibility_status"] == "SOFT_DELETED"
    event = _events("ASSESSMENT_SOFT_DELETED", old)[-1]
    assert f"v{view['policy_version']}" in event.details and "No data was physically removed" in event.details

    # Still in the database, gone for everyone except Admins and Auditors (P1).
    db = SessionLocal()
    try:
        assert db.get(Assessment, old) is not None
    finally:
        db.close()
    assert client.get(f"/api/assessments/{old}", headers=auth("owner")).status_code == 404
    assert ok(client.get(f"/api/assessments/{old}/retention", headers=people["auditor"]))["is_deleted"]
    # No new hold on a deleted record (refused and logged); for anyone
    # other than an Admin or Auditor it doesn't exist at all (P1).
    denied = len(_events("ACCESS_DENIED", old))
    assert _hold(client, auth("admin"), old).status_code == 409
    assert len(_events("ACCESS_DENIED", old)) == denied + 1
    assert _hold(client, people["compliance"], old).status_code == 404


def test_eligibility_never_assumes_missing_information(client, auth, create_assessment):
    missing_date = _decided(create_assessment, days_ago=None)
    future_date = _decided(create_assessment, days_ago=-30)
    db = SessionLocal()
    try:
        for aid, expected in ((missing_date, "INVALID_DATE"), (future_date, "INVALID_DATE")):
            result = retention_rules.evaluate(db, db.get(Assessment, aid))
            assert result["eligibility_status"] == expected and result["eligible"] is False
        # A record type with no configured policy is never eligible.
        result = retention_rules.evaluate(db, db.get(Assessment, missing_date), record_type="AUDIT_EVENT")
        assert result["eligibility_status"] == "NO_POLICY" and not result["eligible"]
        # A policy outside the configured bounds is never applied.
        version = retention_rules.active_version(db)
        db.expunge(version)
        version.retention_days = 10
        old = _decided(create_assessment, days_ago=3000)
        result = retention_rules.evaluate(db, db.get(Assessment, old), version=version)
        assert result["eligibility_status"] == "INVALID_POLICY" and not result["eligible"]
    finally:
        db.close()


# -- eligibility report (D-4) -----------------------------------------------------


def test_report_access_is_read_only_and_limited(client, auth, users, people):
    for headers in (auth("manager"), auth("analyst"), auth("owner"), people["executive"], auth("governance")):
        assert client.get("/api/retention/eligibility", headers=headers).status_code == 403
    assert any("eligibility report" in e.details for e in _events("ACCESS_DENIED", actor_id=users["analyst"]))

    report = ok(client.get("/api/retention/eligibility", headers=people["auditor"]))
    assert report["read_only"] and report["policy_status"] == POLICY_STATUS
    assert _events("RETENTION_REPORT_VIEWED")
    # Auditors read; they never change retention.
    assert _propose(client, people["auditor"], 3000).status_code == 403
    assert client.post("/api/retention/policies/1/decision", json={"decision": "APPROVE", "reason": "x"}, headers=people["auditor"]).status_code == 403
    perms = ok(client.get("/api/retention/permissions", headers=people["auditor"]))
    assert perms["can_read_report"] and not (perms["can_propose"] or perms["can_approve"] or perms["can_set_hold"] or perms["can_soft_delete"])


def test_report_matches_the_shared_rule_and_filters(client, auth, people, create_assessment):
    eligible = _decided(create_assessment, days_ago=3000)
    held = _decided(create_assessment, days_ago=3000)
    upcoming = _decided(create_assessment, days_ago=2555 - 30)
    ok(_hold(client, auth("admin"), held, reason="Preservation notice received"))

    report = ok(client.get("/api/retention/eligibility?page_size=200", headers=auth("admin")))
    rows = {row["record_id"]: row for row in report["items"]}
    for aid in (eligible, held, upcoming):
        single = ok(client.get(f"/api/assessments/{aid}/retention", headers=auth("admin")))["eligibility"]
        for key in ("eligibility_status", "eligible", "eligible_at", "policy_version_id", "basis_date", "legal_hold"):
            assert rows[aid][key] == single[key]
    assert rows[eligible]["eligibility_status"] == "ELIGIBLE"
    assert rows[held]["eligibility_status"] == "LEGAL_HOLD"
    assert rows[upcoming]["eligibility_status"] == "RETAINED"

    # No assessment content in the report.
    forbidden = {"title", "description", "evidence", "business_owner", "product_or_service_name", "legal_hold_reason"}
    assert all(not (forbidden & set(row)) for row in report["items"])

    def ids(query):
        return {row["record_id"] for row in ok(client.get(f"/api/retention/eligibility?page_size=200&{query}", headers=auth("admin")))["items"]}

    assert eligible in ids("status=eligible") and held not in ids("status=eligible")
    assert held in ids("status=held") and held in ids("legal_hold=true") and eligible not in ids("legal_hold=true")
    assert upcoming in ids("status=upcoming&within_days=60") and upcoming not in ids("status=upcoming&within_days=10")
    assert eligible in ids(f"policy_version_id={rows[eligible]['policy_version_id']}") and not ids("policy_version_id=-1")
    assert client.get("/api/retention/eligibility?status=bogus", headers=auth("admin")).status_code == 422
    assert client.get("/api/retention/eligibility?basis_from=not-a-date", headers=auth("admin")).status_code == 422
    ok(_hold(client, people["compliance"], held, hold=False, reason="Notice withdrawn by counsel"))


def test_report_respects_entity_scope_and_the_soft_delete_gate(client, auth, create_assessment):
    inside = _decided(create_assessment, days_ago=3000, legal_entity="Bank DE GmbH")
    outside = _decided(create_assessment, days_ago=3000, legal_entity="Bank FR SA")
    db = SessionLocal()
    try:
        from app.models.user import User

        _user("p5scoped.auditor@test.io", "AUDITOR")
        user = db.query(User).filter(User.email == "p5scoped.auditor@test.io").first()
        user.set_scope("scope_legal_entities", ["Bank DE GmbH"])
        db.add(AssessmentRetention(assessment_id=inside, is_deleted=True, deleted_by="Admin", deletion_reason="test"))
        db.commit()
    finally:
        db.close()
    scoped = _login(client, "p5scoped.auditor@test.io")

    visible = {row["record_id"] for row in ok(client.get("/api/retention/eligibility?page_size=200", headers=scoped))["items"]}
    assert outside not in visible and inside not in visible  # out of scope / soft-deleted
    with_deleted = ok(client.get("/api/retention/eligibility?page_size=200&include_deleted=true", headers=scoped))["items"]
    row = next(r for r in with_deleted if r["record_id"] == inside)
    assert row["eligibility_status"] == "SOFT_DELETED" and outside not in {r["record_id"] for r in with_deleted}


def test_policy_writes_are_global_admin_actions():
    # A dual-role Admin (P3) is refused global configuration changes.
    assert _GLOBAL_ADMIN_PATHS.match("/api/retention/policies")
    assert _GLOBAL_ADMIN_PATHS.match("/api/retention/policies/4/decision")
    assert _GLOBAL_ADMIN_PATHS.match("/api/retention-policy")


def test_every_retention_response_carries_the_policy_status(client, auth, people, create_assessment):
    aid = create_assessment()["id"]
    for body in (
        ok(client.get("/api/retention-policy", headers=auth("owner"))),
        ok(client.get("/api/retention/policies", headers=auth("admin"))),
        ok(client.get("/api/retention/permissions", headers=auth("owner"))),
        ok(client.get("/api/retention/eligibility", headers=auth("admin"))),
        ok(client.get("/api/retention/legal-holds", headers=people["compliance"])),
        ok(client.get(f"/api/assessments/{aid}/retention", headers=auth("owner"))),
    ):
        assert body["policy_status"] == POLICY_STATUS
    assert ok(client.get(f"/api/assessments/{aid}/retention", headers=auth("owner")))["eligibility"]["policy_status"] == POLICY_STATUS


def test_concurrent_or_duplicate_activation_is_refused(client, auth, users, people):
    from sqlalchemy.exc import IntegrityError
    from sqlalchemy.orm.exc import StaleDataError

    from app.models.user import User

    proposal = ok(_propose(client, auth("admin"), 3600), 201)
    stale = SessionLocal()
    try:
        # A second approver loaded the proposal before the first decided it.
        version = stale.get(RetentionPolicyVersion, proposal["id"])
        approver = stale.get(User, users["governance"])
        ok(_decide(client, people["compliance"], proposal["id"]))
        with pytest.raises(StaleDataError):
            retention_rules.decide(stale, version, approver, "REJECT", "late, concurrent decision")
            stale.flush()
        stale.rollback()
    finally:
        stale.close()
    assert _version(proposal["id"]).status == "ACTIVE"

    # The database itself allows only one ACTIVE version per record type.
    db = SessionLocal()
    try:
        db.add(RetentionPolicyVersion(
            record_type="ASSESSMENT", version=999, retention_days=1000, basis="FINAL_DECISION_DATE", status="ACTIVE",
            policy_status=POLICY_STATUS, change_reason="direct insert bypassing the workflow",
        ))
        with pytest.raises(IntegrityError):
            db.flush()
        db.rollback()
    finally:
        db.close()

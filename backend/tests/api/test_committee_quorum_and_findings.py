"""
User direction 2026-10-03 (provisional policy, docs/GOVERNANCE_DECISIONS.md):

* G-5 quorum: a final committee decision needs current votes from three
  eligible members -- an FCRM/Compliance representative, an independent
  Business Risk representative (different people) and one more. The
  requester, their manager, the case manager, preparers and conflicted
  members never count; abstentions don't count. Submission is refused if
  the committee can't form such a quorum at all.
* G-4 findings: HIGH/CRITICAL block submission until RESOLVED and can't be
  accepted (an earlier acceptance doesn't count). Only MEDIUM may be
  accepted, as a documented Committee exception by an eligible committee
  member while the Committee holds the case; until then it blocks the
  final decision.
* Emergency approver: an Admin may set up a delegation for an absent
  manager; it names a Manager as the emergency approver, is labelled
  EMERGENCY in the audit trail, and never gives the Admin the authority.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from app.database import SessionLocal
from app.governance import quorum
from app.models.audit_event import AuditEvent
from app.models.challenge_review import ChallengeFinding
from tests.api.test_p2_governance import _at, _committee_ready, no_challenge_triggers  # noqa: F401
from tests.api.test_p3_sod import _codes, _finding, _readiness
from tests.conftest import ok


def _vote(client, auth, aid, who, vote="APPROVE"):
    return client.post(f"/api/assessments/{aid}/committee-votes", json={"vote": vote, "comment": "Reviewed."}, headers=auth(who))


def _accept(client, auth, aid, finding_id, who):
    return client.patch(
        f"/api/assessments/{aid}/challenge-findings/{finding_id}/accept",
        json={"reason": "Tolerable with the agreed monitoring.", "accepted_by": "Somebody Else"},
        headers=auth(who),
    )


def _set_finding(finding_id, **fields):
    db = SessionLocal()
    try:
        row = db.get(ChallengeFinding, finding_id)
        for key, value in fields.items():
            setattr(row, key, value)
        db.commit()
    finally:
        db.close()


def _denials(aid, actor_id):
    db = SessionLocal()
    try:
        return db.query(AuditEvent).filter(
            AuditEvent.action == "ACCESS_DENIED", AuditEvent.assessment_id == aid, AuditEvent.actor_id == actor_id
        ).all()
    finally:
        db.close()


# -- quorum ----------------------------------------------------------------------


def test_final_decision_needs_three_eligible_members_with_both_representatives(client, auth, users, create_assessment, no_challenge_triggers):
    aid = _committee_ready(client, auth, users, create_assessment)

    ok(_vote(client, auth, aid, "committee"))
    ok(_vote(client, auth, aid, "fcrm_rep"))
    final = _readiness(client, auth, aid, purpose="FINAL_DECISION")
    assert "COMMITTEE_QUORUM_NOT_MET" in _codes(final)
    blocked = client.post(f"/api/assessments/{aid}/committee-decision", json={"decision": "reject", "rationale": "No."}, headers=auth("committee"))
    assert blocked.status_code == 409 and "COMMITTEE_QUORUM_NOT_MET" in {b["code"] for b in blocked.json()["detail"]["blockers"]}
    # Deferral is not final and needs no quorum.
    ok(client.post(f"/api/assessments/{aid}/committee-decision", json={"decision": "defer", "rationale": "Await the Business Risk view."}, headers=auth("committee")))

    ok(_vote(client, auth, aid, "business_rep"))
    final = _readiness(client, auth, aid, purpose="FINAL_DECISION")
    assert "COMMITTEE_QUORUM_NOT_MET" not in _codes(final)
    assert final["quorum"]["met"] and final["quorum"]["counted_members"] == 3
    ok(client.post(f"/api/assessments/{aid}/committee-decision", json={"decision": "reject", "rationale": "Risk outside appetite."}, headers=auth("committee")))


def test_abstentions_do_not_count(client, auth, users, create_assessment, no_challenge_triggers):
    aid = _committee_ready(client, auth, users, create_assessment)
    ok(_vote(client, auth, aid, "committee"))
    ok(_vote(client, auth, aid, "fcrm_rep", vote="ABSTAIN"))
    ok(_vote(client, auth, aid, "business_rep"))
    q = _readiness(client, auth, aid, purpose="FINAL_DECISION")["quorum"]
    assert not q["met"] and q["counted_members"] == 2
    assert any(s["reason"] == "abstained" for s in q["seats"])


def test_a_conflicted_member_never_counts(client, auth, users, create_assessment, no_challenge_triggers):
    aid = _committee_ready(client, auth, users, create_assessment)
    # The Business Risk representative prepared the case (e.g. rated a factor).
    db = SessionLocal()
    try:
        from app.models.assessment import Assessment
        from app.models.user import User

        assessment = db.get(Assessment, aid)
        rep = db.get(User, users["business_rep"])
        why, names = quorum.excluded(db, assessment)
        assert quorum.ineligible_reason(rep, why, names) is None
        why[rep.id] = "prepared the case"
        assert quorum.ineligible_reason(rep, why, names) == "prepared the case"
    finally:
        db.close()
    db = SessionLocal()
    try:
        from app.models.assessment import Assessment

        why, _ = quorum.excluded(db, db.get(Assessment, aid))
        assert why[users["owner"]] == "requester"
        # Here the requester's manager is also the case's manager.
        assert why[users["manager"]] in {"the requester's manager", "the case's manager"}
    finally:
        db.close()


def test_one_person_cannot_fill_both_representative_seats():
    both = SimpleNamespace(id=1, role="COMMITTEE_MEMBER", has_designation=lambda d: True)
    other = SimpleNamespace(id=2, role="COMMITTEE_MEMBER", has_designation=lambda d: False)
    third = SimpleNamespace(id=3, role="COMMITTEE_MEMBER", has_designation=lambda d: False)
    met, missing = quorum._composition([both, other, third], quorum.rules())
    assert not met and any("different people" in m for m in missing)


def test_submission_is_refused_when_no_quorum_can_be_formed(client, auth, users, create_assessment, no_challenge_triggers, monkeypatch):
    aid = create_assessment()["id"]
    _at(aid, "SUBMITTED_TO_MANAGER", manager_id=users["manager"])
    cfg = quorum.rules()
    cfg["business_risk_designations"] = ["NOBODY_HOLDS_THIS"]
    monkeypatch.setattr(quorum, "rules", lambda: cfg)
    readiness = _readiness(client, auth, aid)
    assert "COMMITTEE_QUORUM_UNAVAILABLE" in _codes(readiness)
    assert "Business Risk representative" in next(b for b in readiness["blockers"] if b["code"] == "COMMITTEE_QUORUM_UNAVAILABLE")["message"]


# -- challenge findings ------------------------------------------------------------


@pytest.mark.parametrize("severity", ["HIGH", "CRITICAL"])
def test_high_and_critical_findings_cannot_be_accepted_and_block_submission(client, auth, users, create_assessment, no_challenge_triggers, severity):
    aid = _committee_ready(client, auth, users, create_assessment)
    finding = _finding(aid, severity)
    refused = _accept(client, auth, aid, finding, "committee")
    assert refused.status_code == 409 and "can't be accepted" in refused.json()["detail"]

    # An acceptance recorded under the earlier rule no longer counts.
    _set_finding(finding, resolution_status="ACCEPTED", accepted_by="Manager", accepted_reason="Earlier rule.")
    assert "HIGH_FINDINGS_OPEN" in _codes(_readiness(client, auth, aid))
    _set_finding(finding, resolution_status="RESOLVED")
    assert "HIGH_FINDINGS_OPEN" not in _codes(_readiness(client, auth, aid))


def test_medium_finding_is_accepted_only_as_a_committee_exception(client, auth, users, create_assessment, no_challenge_triggers):
    aid = create_assessment()["id"]
    _at(aid, "SUBMITTED_TO_MANAGER", manager_id=users["manager"])
    medium = _finding(aid, "MEDIUM")

    # A Manager (or an analyst) can't accept; refusals are logged.
    assert _accept(client, auth, aid, medium, "manager").status_code == 403
    assert _denials(aid, users["manager"])
    # A committee member can't accept before the Committee holds the case
    # (the Chair can already see it; an ordinary member can't open it yet).
    early = _accept(client, auth, aid, medium, "chair")
    assert early.status_code == 409 and "while the Committee holds the case" in early.json()["detail"]

    from tests.api.test_p2_governance import _complete_challenge

    _complete_challenge(client, auth, aid)
    ok(client.post(f"/api/assessments/{aid}/manager-decision", json={"decision": "approve", "comment": "ok"}, headers=auth("manager")))
    assert "MEDIUM_FINDINGS_OPEN" in _codes(_readiness(client, auth, aid, purpose="FINAL_DECISION"))

    accepted = ok(_accept(client, auth, aid, medium, "chair"))
    assert accepted["acceptance_authority"] == "COMMITTEE" and accepted["accepted_by"] == "Chair"  # never the payload's name
    final = _readiness(client, auth, aid, purpose="FINAL_DECISION")
    assert "MEDIUM_FINDINGS_OPEN" not in _codes(final)
    assert any(w["code"] == "FINDINGS_ACCEPTED" for w in final["warnings"])


def test_a_medium_finding_accepted_under_the_earlier_rule_must_be_accepted_again(client, auth, users, create_assessment, no_challenge_triggers):
    aid = _committee_ready(client, auth, users, create_assessment)
    medium = _finding(aid, "MEDIUM")
    _set_finding(medium, resolution_status="ACCEPTED", accepted_by="Manager", accepted_reason="Earlier rule.")
    assert "MEDIUM_FINDINGS_OPEN" in _codes(_readiness(client, auth, aid, purpose="FINAL_DECISION"))
    ok(_accept(client, auth, aid, medium, "fcrm_rep"))
    assert "MEDIUM_FINDINGS_OPEN" not in _codes(_readiness(client, auth, aid, purpose="FINAL_DECISION"))


def test_a_case_party_on_the_committee_cannot_accept(client, auth, users, create_assessment, no_challenge_triggers):
    aid = _committee_ready(client, auth, users, create_assessment)
    medium = _finding(aid, "MEDIUM")
    _at(aid, "READY_FOR_COMMITTEE", seed=False, manager_decided_by_id=users["chair"])
    refused = _accept(client, auth, aid, medium, "chair")
    assert refused.status_code == 403 and "took the manager decision" in refused.json()["detail"]
    assert _denials(aid, users["chair"])


# -- emergency approver -------------------------------------------------------------


def test_emergency_delegation_names_a_manager_and_is_labelled(client, auth, users, create_assessment, no_challenge_triggers):
    from tests.api.test_p2_governance import _complete_challenge

    aid = create_assessment()["id"]
    _at(aid, "SUBMITTED_TO_MANAGER", manager_id=users["manager"])
    _complete_challenge(client, auth, aid)
    now = datetime.now(timezone.utc)
    delegation = ok(
        client.post(
            "/api/delegations",
            json={
                "delegator_id": users["manager"],
                "delegate_id": users["head"],
                "authority": "MANAGER_APPROVAL",
                "scope_type": "ASSESSMENT",
                "scope_assessment_id": aid,
                "start_at": (now - timedelta(minutes=1)).isoformat(),
                "end_at": (now + timedelta(days=2)).isoformat(),
                "reason": "Manager on unplanned leave; decision due today.",
            },
            headers=auth("admin"),
        ),
        201,
    )
    try:
        db = SessionLocal()
        try:
            created = db.query(AuditEvent).filter(AuditEvent.action == "DELEGATION_CREATED", AuditEvent.assessment_id == aid).one()
            assert created.details.startswith("EMERGENCY delegation set up by")
        finally:
            db.close()
        # The Admin who set it up still can't decide; the named Manager can.
        assert client.post(f"/api/assessments/{aid}/manager-decision", json={"decision": "approve", "comment": "x"}, headers=auth("admin")).status_code == 403
        decided = ok(client.post(f"/api/assessments/{aid}/manager-decision", json={"decision": "return", "comment": "Need the vendor report."}, headers=auth("head")))
        assert decided["status"] == "RETURNED_BY_MANAGER"
    finally:
        client.post(f"/api/delegations/{delegation['id']}/revoke", json={"reason": "Test done."}, headers=auth("admin"))

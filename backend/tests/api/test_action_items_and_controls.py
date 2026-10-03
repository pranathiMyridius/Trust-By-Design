"""
Regression tests for:

4. R13.4: an action item could be completed without evidence (PATCH it to
   PENDING_CLOSURE_APPROVAL, then approve), the requester could approve
   their own closure, committee members couldn't reach closure-decision,
   and committee conditions could be marked COMPLETED directly.
5. FCRM Analysts were locked out of the controls and challenge-review APIs.
6. identify-controls (auto_create) and assess-design always crashed.
"""

from datetime import date, timedelta

import pytest

import app.api.controls as controls_api
from app.database import SessionLocal
from app.models.action_item import ActionItem
from app.models.assessment import Assessment
from app.models.audit_event import AuditEvent
from app.models.committee_condition import CommitteeCondition
from tests.conftest import ok


def _visible(aid: int, users, status: str | None = None) -> None:
    """Assign the test manager (managers only see their own queue) and,
    where a committee member acts, put the assessment in a status the
    committee can see."""

    db = SessionLocal()
    try:
        assessment = db.get(Assessment, aid)
        assessment.manager_id = users["manager"]
        if status:
            assessment.status = status
        db.commit()
    finally:
        db.close()


def _item(client, auth, aid, source_type="POLICY_EXCEPTION") -> dict:
    return ok(
        client.post(
            f"/api/assessments/{aid}/action-items",
            json={"source_type": source_type, "title": "Close the policy exception"},
            headers=auth("analyst"),
        ),
        201,
    )


def _request_closure(client, auth, aid, item_id, who="analyst"):
    return client.post(
        f"/api/assessments/{aid}/action-items/{item_id}/request-closure",
        json={"completion_evidence": "Signed exception memo, ref PX-12."},
        headers=auth(who),
    )


def _decide(client, auth, aid, item_id, who, decision="approve"):
    return client.post(
        f"/api/assessments/{aid}/action-items/{item_id}/closure-decision",
        json={"decision": decision},
        headers=auth(who),
    )


# -- 4. action-item closure --------------------------------------------------


@pytest.mark.parametrize("status", ["PENDING_CLOSURE_APPROVAL", "COMPLETED", "CLOSURE_REJECTED"])
def test_closure_states_cannot_be_set_directly(client, auth, create_assessment, status):
    aid = create_assessment()["id"]
    item = _item(client, auth, aid)

    response = client.patch(
        f"/api/assessments/{aid}/action-items/{item['id']}",
        json={"status": status},
        headers=auth("analyst"),
    )
    assert response.status_code == 422, response.text


def test_pending_closure_cannot_be_reopened_by_a_direct_edit(client, auth, create_assessment):
    aid = create_assessment()["id"]
    item = _item(client, auth, aid)
    ok(_request_closure(client, auth, aid, item["id"]))

    response = client.patch(
        f"/api/assessments/{aid}/action-items/{item['id']}",
        json={"status": "OPEN"},
        headers=auth("analyst"),
    )
    assert response.status_code == 400, response.text


def test_requester_cannot_approve_own_closure(client, auth, users, create_assessment):
    aid = create_assessment()["id"]
    _visible(aid, users)
    item = _item(client, auth, aid)
    assert ok(_request_closure(client, auth, aid, item["id"]))["status"] == "PENDING_CLOSURE_APPROVAL"

    response = _decide(client, auth, aid, item["id"], "analyst")
    assert response.status_code == 403
    assert "requested this closure" in response.json()["detail"]

    approved = ok(_decide(client, auth, aid, item["id"], "manager"))
    assert approved["status"] == "COMPLETED"
    assert approved["completion_evidence"]


def test_committee_member_can_decide_a_closure(client, auth, users, create_assessment):
    aid = create_assessment()["id"]
    _visible(aid, users, "APPROVED_WITH_CONDITIONS")
    item = _item(client, auth, aid)
    ok(_request_closure(client, auth, aid, item["id"]))

    assert ok(_decide(client, auth, aid, item["id"], "committee", "reject"))["status"] == "CLOSURE_REJECTED"


def test_approval_without_evidence_is_refused(client, auth, users, create_assessment):
    aid = create_assessment()["id"]
    _visible(aid, users)
    item = _item(client, auth, aid)
    ok(_request_closure(client, auth, aid, item["id"]))

    db = SessionLocal()
    try:
        db.get(ActionItem, item["id"]).completion_evidence = "  "
        db.commit()
    finally:
        db.close()

    response = _decide(client, auth, aid, item["id"], "manager")
    assert response.status_code == 400
    assert "no completion evidence" in response.json()["detail"]


def _condition_with_item(aid: int) -> tuple[int, int]:
    db = SessionLocal()
    try:
        condition = CommitteeCondition(
            assessment_id=aid,
            description="Lower transaction limits for the first quarter",
            owner="Merchant Services",
            due_date=date.today() + timedelta(days=30),
            priority="HIGH",
            status="OPEN",
        )
        db.add(condition)
        db.flush()
        item = ActionItem(
            assessment_id=aid,
            source_type="COMMITTEE_CONDITION",
            committee_condition_id=condition.id,
            title=condition.description,
            status="OPEN",
        )
        db.add(item)
        db.commit()
        return condition.id, item.id
    finally:
        db.close()


def test_committee_condition_completes_only_through_its_action_item(client, auth, users, create_assessment):
    aid = create_assessment()["id"]
    _visible(aid, users, "APPROVED_WITH_CONDITIONS")
    condition_id, item_id = _condition_with_item(aid)
    url = f"/api/assessments/{aid}/committee-conditions/{condition_id}"

    direct = client.patch(url, json={"status": "COMPLETED"}, headers=auth("analyst"))
    assert direct.status_code == 400, direct.text

    not_allowed = client.patch(url, json={"status": "IN_PROGRESS"}, headers=auth("owner"))
    assert not_allowed.status_code == 403, not_allowed.text

    assert ok(client.patch(url, json={"status": "IN_PROGRESS"}, headers=auth("analyst")))["status"] == "IN_PROGRESS"

    ok(_request_closure(client, auth, aid, item_id))
    ok(_decide(client, auth, aid, item_id, "committee"))

    db = SessionLocal()
    try:
        condition = db.get(CommitteeCondition, condition_id)
        assert condition.status == "COMPLETED"
        assert condition.completion_evidence == "Signed exception memo, ref PX-12."
        assert condition.completed_at is not None
    finally:
        db.close()


# -- 5. analyst access to controls and challenge review ----------------------


def test_analyst_can_map_controls_and_resolve_but_not_accept_findings(client, auth, users, analysed_assessment):
    aid = analysed_assessment()["id"]
    _visible(aid, users)
    factor = ok(client.get(f"/api/assessments/{aid}/risk-factors", headers=auth("analyst")))[0]

    ok(
        client.post(
            f"/api/assessments/{aid}/controls",
            json={"risk_factor_id": factor["id"], "control_type": "SANCTIONS_SCREENING"},
            headers=auth("analyst"),
        ),
        201,
    )

    review = ok(client.get(f"/api/assessments/{aid}/challenge-review", headers=auth("analyst")))
    open_findings = [f for f in review["findings"] if f["resolution_status"] == "OPEN"]
    assert len(open_findings) >= 2
    first, second = open_findings[:2]

    accept = client.patch(
        f"/api/assessments/{aid}/challenge-findings/{first['id']}/accept",
        json={"reason": "Accepted risk."},
        headers=auth("analyst"),
    )
    assert accept.status_code == 403

    resolved = ok(
        client.patch(
            f"/api/assessments/{aid}/challenge-findings/{first['id']}/resolve",
            json={"resolution_note": "Added the missing factor.", "resolved_by": "Somebody Else"},
            headers=auth("analyst"),
        )
    )
    assert resolved["resolution_status"] == "RESOLVED"
    assert resolved["resolved_by"] == "Analyst"

    # G-4 (2026-10-03): accepting is a documented Committee exception, so a
    # Manager can't accept either (covered in test_committee_quorum_and_findings.py).
    refused = client.patch(
        f"/api/assessments/{aid}/challenge-findings/{second['id']}/accept",
        json={"reason": "Accepted risk.", "accepted_by": "Somebody Else"},
        headers=auth("manager"),
    )
    assert refused.status_code == 403

    config = client.patch("/api/challenge-triggers", json={}, headers=auth("analyst"))
    assert config.status_code == 403


# -- 6. AI control endpoints ---------------------------------------------------


def test_identify_controls_auto_create_and_assess_design(client, auth, analysed_assessment, monkeypatch):
    monkeypatch.setattr(
        controls_api,
        "identify_applicable_controls",
        lambda **_: ["SANCTIONS_SCREENING", "TRANSACTION_MONITORING"],
    )
    monkeypatch.setattr(
        controls_api,
        "assess_control_design",
        lambda **_: {"design_adequacy": "DESIGN_ADEQUATE", "rationale": "Covers the risk."},
    )

    aid = analysed_assessment()["id"]
    factor = ok(client.get(f"/api/assessments/{aid}/risk-factors", headers=auth("analyst")))[0]

    identified = ok(
        client.post(
            f"/api/assessments/{aid}/risk-factors/{factor['id']}/identify-controls",
            json={"risk_factor_id": factor["id"], "auto_create": True},
            headers=auth("analyst"),
        )
    )
    assert identified["suggested_controls"] == ["SANCTIONS_SCREENING", "TRANSACTION_MONITORING"]

    controls = ok(client.get(f"/api/assessments/{aid}/controls", headers=auth("analyst")))
    mapped = [c for c in controls if c["risk_factor_id"] == factor["id"]]
    assert {c["control_type"] for c in mapped} >= {"SANCTIONS_SCREENING", "TRANSACTION_MONITORING"}

    design = ok(
        client.post(
            f"/api/assessments/{aid}/controls/{mapped[0]['id']}/assess-design",
            json={},
            headers=auth("analyst"),
        )
    )
    assert design["design_adequacy"] == "DESIGN_ADEQUATE"

    db = SessionLocal()
    try:
        actions = {
            event.action
            for event in db.query(AuditEvent).filter(AuditEvent.assessment_id == aid).all()
        }
        assert {"CONTROL_IDENTIFIED", "CONTROL_ASSESSED"} <= actions
    finally:
        db.close()

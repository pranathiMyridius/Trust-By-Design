"""
The manager decision (approve / return / reject on SUBMITTED_TO_MANAGER)
belongs to the assigned manager or a delegate acting for them (AW.7; the
delegate path is covered by test_aw7_delegation.py). An Admin is not a
substitute (R15.2, R-GOV-02): refused, logged as ACCESS_DENIED, nothing
changes -- both at the endpoint and in the shared workflow transition
rule, so no other code path can use the Admin as the manager.
"""

from __future__ import annotations

import pytest
from fastapi import HTTPException

from app.database import SessionLocal
from app.models.assessment import Assessment
from app.models.audit_event import AuditEvent
from app.models.user import User
from app.services import workflow
from tests.api.test_p2_governance import _at, _review, _sign_off, no_challenge_triggers  # noqa: F401
from tests.conftest import ok

DECISIONS = ["approve", "return", "reject"]


def _submitted(create_assessment, users) -> int:
    aid = create_assessment()["id"]
    _at(aid, "SUBMITTED_TO_MANAGER", manager_id=users["manager"])
    return aid


def _state(aid: int) -> tuple:
    db = SessionLocal()
    try:
        a = db.get(Assessment, aid)
        return a.status, a.workflow_status, a.manager_decision, a.manager_decided_at
    finally:
        db.close()


def _denials(aid: int, actor_id: int) -> list[AuditEvent]:
    db = SessionLocal()
    try:
        return (
            db.query(AuditEvent)
            .filter(AuditEvent.action == "ACCESS_DENIED", AuditEvent.assessment_id == aid, AuditEvent.actor_id == actor_id)
            .all()
        )
    finally:
        db.close()


def _admin_id() -> int:
    db = SessionLocal()
    try:
        return db.query(User).filter(User.email == "admin@example.com").one().id
    finally:
        db.close()


@pytest.mark.parametrize("decision", DECISIONS)
def test_admin_cannot_take_the_manager_decision(client, auth, users, create_assessment, no_challenge_triggers, decision):
    aid = _submitted(create_assessment, users)
    before = _state(aid)

    refused = client.post(
        f"/api/assessments/{aid}/manager-decision",
        json={"decision": decision, "comment": "Admin attempt."},
        headers=auth("admin"),
    )
    assert refused.status_code == 403, refused.text
    assert _state(aid) == before  # nothing changed
    denials = _denials(aid, _admin_id())
    assert len(denials) == 1 and "manager decision refused" in denials[0].details


def test_another_manager_is_refused_and_logged(client, auth, users, create_assessment, no_challenge_triggers):
    aid = _submitted(create_assessment, users)
    refused = client.post(
        f"/api/assessments/{aid}/manager-decision", json={"decision": "approve", "comment": "x"}, headers=auth("head")
    )
    assert refused.status_code == 403
    assert _denials(aid, users["head"])


def test_assigned_manager_still_decides(client, auth, users, create_assessment, no_challenge_triggers):
    aid = _submitted(create_assessment, users)
    ok(_review(client, auth, aid), 201)  # readiness: the challenge review is mandatory
    ok(_sign_off(client, auth, aid), 201)
    decided = ok(
        client.post(f"/api/assessments/{aid}/manager-decision", json={"decision": "approve", "comment": "Fine."}, headers=auth("manager"))
    )
    assert decided["status"] == "READY_FOR_COMMITTEE"


def test_workflow_rule_refuses_admin_for_every_manager_transition(users, create_assessment):
    aid = create_assessment()["id"]
    _at(aid, "SUBMITTED_TO_MANAGER", manager_id=users["manager"])
    db = SessionLocal()
    try:
        assessment = db.get(Assessment, aid)
        admin = db.get(User, _admin_id())
        manager = db.get(User, users["manager"])
        for target in ("READY_FOR_COMMITTEE", "RETURNED_BY_MANAGER", "MANAGER_REJECTED"):
            assert "ADMIN" not in workflow.TRANSITIONS["SUBMITTED_TO_MANAGER"][target]
            with pytest.raises(HTTPException) as refused:
                workflow.check_transition(db, assessment, target, user=admin)
            assert refused.value.status_code == 403

        offered = {t["to_status"]: t for t in workflow.available_transitions(db, assessment, admin)}
        for target in ("READY_FOR_COMMITTEE", "RETURNED_BY_MANAGER", "MANAGER_REJECTED"):
            assert offered[target]["allowed_for_user"] is False and "ADMIN" not in offered[target]["roles"]
        mine = {t["to_status"]: t for t in workflow.available_transitions(db, assessment, manager)}
        assert mine["READY_FOR_COMMITTEE"]["allowed_for_user"] is True
    finally:
        db.rollback()
        db.close()

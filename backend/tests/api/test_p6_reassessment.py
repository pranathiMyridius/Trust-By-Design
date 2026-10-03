"""
P6 (Stage 18, R18.1-R18.4): the approved parent's lifecycle while it is
reassessed (UNDER_REASSESSMENT -> SUPERSEDED or back in force), trigger
linking, the due queue, flag/resolve with dedicated audit and logged
refusals, server-side actions, reused fields with source and age, and the
structured comparison.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

import pytest

from app.database import SessionLocal
from app.models.assessment import Assessment
from app.models.reassessment_trigger import ReassessmentTrigger
from app.services import reassessment_lifecycle as lifecycle
from app.services.data_protection import ProtectedDataError
from app.services.reassessment_service import sweep_review_dates
from tests.api.test_p3_sod import _events, _login, _user
from tests.conftest import ok


def _utc_today() -> date:
    # The review-date checks use the UTC date.
    return datetime.now(timezone.utc).date()


def _set(aid: int, **fields) -> None:
    db = SessionLocal()
    try:
        assessment = db.get(Assessment, aid)
        for key, value in fields.items():
            setattr(assessment, key, value)
        db.commit()
    finally:
        db.close()


def _get(aid: int) -> Assessment:
    db = SessionLocal()
    try:
        assessment = db.get(Assessment, aid)
        db.expunge(assessment)
        return assessment
    finally:
        db.close()


def _decide_child(child_id: int, to_status: str, from_status: str = "COMMITTEE_REVIEW") -> None:
    """Drive the child's final status through the one transition hook (the
    committee route itself is covered by the P2/P3 suites)."""

    db = SessionLocal()
    try:
        child = db.get(Assessment, child_id)
        child.status = from_status
        db.flush()
        lifecycle.on_status_changed(db, child, from_status, to_status, None)
        child.status = to_status
        db.commit()
    finally:
        db.close()


@pytest.fixture
def approved(create_assessment, users):
    def make(**fields) -> int:
        aid = create_assessment()["id"]
        _set(aid, status="APPROVED", manager_id=users["manager"], **fields)
        return aid

    return make


def _propose(client, headers, aid, **changes):
    return client.post(
        f"/api/assessments/{aid}/reassessment/propose-change",
        json={"changes": changes or {"countries_jurisdictions": "Germany, Poland, France"}, "reason": "Adding France."},
        headers=headers,
    )


def _status(client, headers, aid):
    return ok(client.get(f"/api/assessments/{aid}/reassessment/status", headers=headers))


# -- parent lifecycle -------------------------------------------------------------


def test_opening_marks_parent_and_links_its_open_triggers(client, auth, approved):
    aid = approved()
    flagged = ok(client.post(f"/api/assessments/{aid}/reassessment/flag-trigger",
                             json={"trigger_type": "SIGNIFICANT_CONTROL_FAILURE", "description": "TM outage in Q3."},
                             headers=auth("analyst")), 201)
    child = ok(_propose(client, auth("owner"), aid), 201)["reassessment_id"]

    assert _get(child).reference_id and _get(child).reference_id != _get(aid).reference_id
    parent = _get(aid)
    assert parent.status == "APPROVED"  # the pipeline status is untouched
    assert parent.reassessment_state == "UNDER_REASSESSMENT"
    db = SessionLocal()
    try:
        trigger = db.get(ReassessmentTrigger, flagged["id"])
        assert (trigger.status, trigger.reassessment_id) == ("REASSESSMENT_CREATED", child)
        assert "Addressed by reassessment" in trigger.resolution_note
    finally:
        db.close()
    status = _status(client, auth("owner"), aid)
    assert status["reassessment_state"] == "UNDER_REASSESSMENT" and status["in_progress_reassessment_id"] == child
    assert status["actions"]["propose"]["allowed"] is False and "already in progress" in status["actions"]["propose"]["reason"]
    assert any(f"#{child}" in e.details for e in _events("REASSESSMENT_OPENED", aid))
    assert not [e for e in _events("STATUS_CHANGE", aid) if "Reassessment opened" in (e.details or "")]


def test_approval_of_the_child_supersedes_the_parent(client, auth, approved):
    aid = approved(next_review_date=_utc_today() + timedelta(days=5))
    child = ok(_propose(client, auth("owner"), aid), 201)["reassessment_id"]

    _decide_child(child, "APPROVED")

    parent = _get(aid)
    assert (parent.status, parent.reassessment_state, parent.superseded_by_id) == ("APPROVED", "SUPERSEDED", child)
    assert parent.superseded_at is not None
    assert _events("REASSESSMENT_PARENT_SUPERSEDED", aid) and _events("REASSESSMENT_PARENT_SUPERSEDED", child)

    # A superseded parent raises no more review triggers ...
    db = SessionLocal()
    try:
        assert not [t for t in sweep_review_dates(db) if t.assessment_id == aid]
    finally:
        db.close()
    queue = ok(client.get("/api/workflow/work-queue", headers=auth("owner")))
    assert not [a for a in queue["reassessment_alerts"] if a["assessment_id"] == aid]
    # ... and can't be reassessed or flagged again: its successor is the one in force.
    refused = _propose(client, auth("owner"), aid)
    assert refused.status_code == 409 and f"#{child}" in refused.json()["detail"]
    flag = client.post(f"/api/assessments/{aid}/reassessment/flag-trigger",
                       json={"trigger_type": "REGULATORY_POLICY_CHANGE", "description": "x"}, headers=auth("analyst"))
    assert flag.status_code == 409
    assert _status(client, auth("owner"), aid)["reassessment_state"] == "SUPERSEDED"


@pytest.mark.parametrize("outcome, from_status", [("REJECTED", "COMMITTEE_REVIEW"), ("MANAGER_REJECTED", "SUBMITTED_TO_MANAGER"), ("CLOSED", "INTAKE")])
def test_a_reassessment_that_ends_without_approval_leaves_the_parent_in_force(client, auth, approved, outcome, from_status):
    aid = approved()
    child = ok(_propose(client, auth("owner"), aid), 201)["reassessment_id"]
    _decide_child(child, outcome, from_status)

    parent = _get(aid)
    assert parent.reassessment_state is None and parent.superseded_by_id is None
    assert any(f"#{child}" in e.details for e in _events("REASSESSMENT_ENDED_WITHOUT_APPROVAL", aid))
    # A new reassessment can be opened again.
    assert _propose(client, auth("owner"), aid).status_code == 201


def test_the_hook_runs_on_real_transitions(client, auth, approved):
    """Withdrawing the child through the real workflow route releases the parent."""

    aid = approved()
    child = ok(_propose(client, auth("owner"), aid), 201)["reassessment_id"]
    ok(client.post(f"/api/assessments/{child}/workflow/withdraw", json={"reason": "Change abandoned by the business."}, headers=auth("owner")))
    assert _get(child).status == "CLOSED"
    assert _get(aid).reassessment_state is None


def test_parent_flags_are_protected(approved, client, auth):
    aid = approved()
    child = ok(_propose(client, auth("owner"), aid), 201)["reassessment_id"]
    _decide_child(child, "APPROVED")
    db = SessionLocal()
    try:
        parent = db.get(Assessment, aid)
        parent.reassessment_state = None
        with pytest.raises(ProtectedDataError):
            db.flush()
        db.rollback()
        parent = db.get(Assessment, aid)
        parent.superseded_by_id = aid
        with pytest.raises(ProtectedDataError):
            db.flush()
        db.rollback()
    finally:
        db.close()


# -- triggers: flag, resolve, due queue ----------------------------------------------


def test_flag_and_resolve_use_dedicated_audit_and_log_refusals(client, auth, users, approved):
    aid = approved()
    trigger = ok(client.post(f"/api/assessments/{aid}/reassessment/flag-trigger",
                             json={"trigger_type": "REGULATORY_POLICY_CHANGE", "description": "New EU AML guidance."},
                             headers=auth("owner")), 201)
    assert any("New EU AML guidance." in e.details for e in _events("REASSESSMENT_TRIGGER_FLAGGED", aid))

    denied = len(_events("ACCESS_DENIED", aid))
    url = f"/api/assessments/{aid}/reassessment/triggers/{trigger['id']}"
    assert client.patch(url, json={"status": "ACKNOWLEDGED"}, headers=auth("owner")).status_code == 403
    assert client.post(f"/api/assessments/{aid}/reassessment/flag-trigger",
                       json={"trigger_type": "NEW_VENDOR", "description": "x"}, headers=auth("committee")).status_code == 403
    assert len(_events("ACCESS_DENIED", aid)) == denied + 2

    acknowledged = ok(client.patch(url, json={"status": "ACKNOWLEDGED", "resolution_note": "Reviewing with Legal."}, headers=auth("analyst")))
    assert acknowledged["status"] == "ACKNOWLEDGED" and acknowledged["resolved_by_id"] == users["analyst"]
    assert client.patch(url, json={"status": "DISMISSED"}, headers=auth("analyst")).status_code == 422
    dismissed = ok(client.patch(url, json={"status": "DISMISSED", "dismissed_reason": "Guidance does not apply to card acquiring."}, headers=auth("manager")))
    assert dismissed["status"] == "DISMISSED"
    # A settled trigger isn't re-opened.
    assert client.patch(url, json={"status": "ACKNOWLEDGED"}, headers=auth("analyst")).status_code == 409
    events = _events("REASSESSMENT_TRIGGER_RESOLVED", aid)
    assert any("ACKNOWLEDGED" in e.details for e in events) and any("Guidance does not apply" in e.details for e in events)


def test_due_queue_lists_all_open_triggers_with_actions(client, auth, users, approved):
    aid = approved(next_review_date=_utc_today() - timedelta(days=1))
    ok(client.post(f"/api/assessments/{aid}/reassessment/flag-trigger",
                   json={"trigger_type": "NEW_VENDOR", "description": "New processor onboarded."}, headers=auth("owner")), 201)

    owner_items = [a for a in ok(client.get("/api/workflow/work-queue", headers=auth("owner")))["reassessment_alerts"] if a["assessment_id"] == aid]
    assert {a["trigger_type"] for a in owner_items} == {"EXPIRY", "NEW_VENDOR"}
    assert all(a["can_start_reassessment"] and not a["can_resolve"] for a in owner_items)

    # FCRM analysts now see the due queue too (within their visibility), with resolve rights.
    analyst_items = [a for a in ok(client.get("/api/workflow/work-queue", headers=auth("analyst")))["reassessment_alerts"] if a["assessment_id"] == aid]
    assert analyst_items and all(a["can_resolve"] for a in analyst_items)
    other = ok(client.get("/api/workflow/work-queue", headers=auth("other_owner")))["reassessment_alerts"]
    assert not [a for a in other if a["assessment_id"] == aid]

    # Once a reassessment is open, the items say so and stop asking to start one.
    child = ok(_propose(client, auth("owner"), aid), 201)["reassessment_id"]
    after = [a for a in ok(client.get("/api/workflow/work-queue", headers=auth("owner")))["reassessment_alerts"] if a["assessment_id"] == aid]
    assert not after  # the open triggers were linked to the reassessment
    db = SessionLocal()
    try:
        sweep_review_dates(db)  # a date trigger raised while under reassessment is linked, not queued
        linked = db.query(ReassessmentTrigger).filter(ReassessmentTrigger.assessment_id == aid, ReassessmentTrigger.trigger_type == "EXPIRY").all()
        assert all(t.reassessment_id == child for t in linked)
    finally:
        db.close()


def test_start_reassessment_actions_follow_the_role_rule(client, auth, approved):
    aid = approved()
    for who, allowed in (("owner", True), ("analyst", True), ("manager", True), ("admin", True), ("committee", False), ("other_owner", None)):
        response = client.get(f"/api/assessments/{aid}/reassessment/status", headers=auth(who))
        if allowed is None:
            assert response.status_code in (403, 404)
            continue
        assert ok(response)["actions"]["propose"]["allowed"] is allowed, who
    refused = _propose(client, auth("committee"), aid)
    assert refused.status_code == 403
    assert any("propose" in (e.details or "").lower() or "reassess" in (e.details or "").lower() for e in _events("ACCESS_DENIED", aid))


# -- reused fields and comparison ---------------------------------------------------


def test_reused_fields_show_source_and_age(client, auth, approved):
    aid = approved()
    child = ok(_propose(client, auth("owner"), aid, customer_segment="Large corporates"), 201)["reassessment_id"]
    comparison = ok(client.get(f"/api/assessments/{child}/reassessment/compare", headers=auth("analyst")))
    reused = {r["field"]: r for r in comparison["reused_fields"]}
    assert "customer_segment" not in reused  # it was proposed, not reused
    assert reused["countries_jurisdictions"]["source_assessment_id"] == aid
    assert reused["countries_jurisdictions"]["label"] == "Countries / jurisdictions"
    assert isinstance(reused["countries_jurisdictions"]["age_days"], int)
    assert {"field": "customer_segment"} .items() <= next(f for f in comparison["fields_changed"] if f["field"] == "customer_segment").items()


def test_structured_comparison_by_category_control_and_condition(client, auth, approved):
    from app.models.committee_condition import CommitteeCondition
    from app.models.risk_factor import RiskFactor

    aid = approved(inherent_score=12.0, inherent_risk_level="MEDIUM", residual_score=6.0, residual_risk_level="LOW")
    child = ok(_propose(client, auth("owner"), aid), 201)["reassessment_id"]
    _set(child, inherent_score=16.0, inherent_risk_level="HIGH")
    db = SessionLocal()
    try:
        for assessment_id, category, likelihood in ((aid, "GEOGRAPHIC", 3), (child, "GEOGRAPHIC", 4), (child, "THIRD_PARTY", 3)):
            db.add(RiskFactor(assessment_id=assessment_id, category=category, applicable=True, likelihood=likelihood, impact=3,
                              score=float(likelihood * 3), severity="MEDIUM", rationale="P6 test factor", is_current=True))
        db.add(CommitteeCondition(assessment_id=aid, description="Quarterly  TM tuning review", owner="FCRM Ops", due_date=_utc_today() + timedelta(days=30), status="OPEN", created_by="x"))
        db.commit()
    finally:
        db.close()

    structured = ok(client.get(f"/api/assessments/{child}/reassessment/compare", headers=auth("analyst")))["structured"]
    factors = {f["key"]: f for f in structured["factors"]}
    assert factors["GEOGRAPHIC"]["change"] == "CHANGED" and "likelihood" in factors["GEOGRAPHIC"]["changed_fields"]
    assert factors["THIRD_PARTY"]["change"] == "ADDED"
    assert {c["change"] for c in structured["conditions"]} == {"REMOVED"}
    inherent = next(s for s in structured["scores"] if s["measure"] == "Inherent")
    assert (inherent["before"], inherent["after"], inherent["delta"], inherent["after_level"]) == (12.0, 16.0, 4.0, "HIGH")
    assert structured["summary"]["factors"]["ADDED"] == 1

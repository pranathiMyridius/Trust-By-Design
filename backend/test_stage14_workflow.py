"""
Stage 14 (Workflow and Status Management) acceptance checks.

Runs against a throwaway SQLite database -- never the DATABASE_URL in
backend/.env. Run from the backend/ folder:

    python test_stage14_workflow.py

(Also collectable by pytest if it's installed: every check is a
test_* function.)
"""

import os
import tempfile
from datetime import datetime, timedelta, timezone

_DB_FILE = os.path.join(tempfile.mkdtemp(), "stage14_test.db")
# Tests never use the real AI provider/key from backend/.env (their HTTP
# calls are faked or absent); pin the offline OpenRouter configuration.
os.environ["LLM_PROVIDER"] = "openrouter"
os.environ["OPENAI_API_KEY"] = ""
os.environ["DATABASE_URL"] = f"sqlite:///{_DB_FILE}"
os.environ["WORKFLOW_ESCALATION_INTERVAL_SECONDS"] = "0"
# Stage 19: the bootstrap admin password is no longer hard-coded.
os.environ["ADMIN_BOOTSTRAP_PASSWORD"] = "ChangeMe123!"
os.environ["BACKUP_INTERVAL_HOURS"] = "0"
os.environ["PROCESSING_JOBS_INLINE"] = "true"

from fastapi.testclient import TestClient  # noqa: E402

from app.auth.security import hash_password  # noqa: E402
from app.database import SessionLocal  # noqa: E402
from app.main import app, prepare_database  # noqa: E402

# The schema is prepared explicitly; importing the app never migrates.
prepare_database()
from app.models.action_item import ActionItem  # noqa: E402
from app.models.assessment import Assessment  # noqa: E402
from app.models.assessment_comment import AssessmentComment  # noqa: E402
from app.models.audit_event import AuditEvent  # noqa: E402
from app.models.user import User  # noqa: E402
from app.models.workflow_transition import WorkflowTransition  # noqa: E402
import app.challenge_engine.engine as challenge_engine  # noqa: E402

client = TestClient(app)
PASSWORD = "Passw0rd!"

FULL_REQUEST = {
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


def _make_users() -> dict[str, User]:
    db = SessionLocal()
    try:
        def add(email, role, manager=None, designations=None):
            user = User(
                email=email,
                hashed_password=hash_password(PASSWORD),
                full_name=email.split("@")[0].title(),
                role=role,
                manager_id=manager.id if manager else None,
            )
            # P3: governance designations (provisional role matrix).
            user.set_designations(designations)
            db.add(user)
            db.flush()
            return user

        manager = add("manager@test.io", "MANAGER")
        users = {
            "manager": manager,
            "owner": add("owner@test.io", "BUSINESS_USER", manager),
            "other_owner": add("other@test.io", "BUSINESS_USER", manager),
            "analyst": add("analyst@test.io", "FCRM_ANALYST", manager),
            "analyst2": add("analyst2@test.io", "FCRM_ANALYST", manager, ["CHALLENGE_REVIEWER"]),
            "committee": add("committee@test.io", "COMMITTEE_MEMBER"),
            # G-5 quorum (2026-10-03): the two representative seats.
            "fcrm_rep": add("fcrmrep@test.io", "COMMITTEE_MEMBER", designations=["COMMITTEE_FCRM_COMPLIANCE_REP"]),
            "business_rep": add("businessrep@test.io", "COMMITTEE_MEMBER", designations=["COMMITTEE_BUSINESS_RISK_REP"]),
        }
        db.commit()
        return {key: user.id for key, user in users.items()}
    finally:
        db.close()


USERS = _make_users()
_TOKENS: dict[str, str] = {}


def auth(who: str) -> dict:
    if who not in _TOKENS:
        email = {
            "manager": "manager@test.io",
            "owner": "owner@test.io",
            "other_owner": "other@test.io",
            "analyst": "analyst@test.io",
            "analyst2": "analyst2@test.io",
            "committee": "committee@test.io",
            "fcrm_rep": "fcrmrep@test.io",
            "business_rep": "businessrep@test.io",
            "admin": "admin@example.com",
        }[who]
        password = "ChangeMe123!" if who == "admin" else PASSWORD
        response = client.post("/api/auth/login", json={"email": email, "password": password})
        assert response.status_code == 200, response.text
        _TOKENS[who] = response.json()["access_token"]
    return {"Authorization": f"Bearer {_TOKENS[who]}"}


def _create(is_draft: bool, **overrides) -> dict:
    body = {**FULL_REQUEST, **overrides, "is_draft": is_draft}
    response = client.post("/api/assessments", json=body, headers=auth("owner"))
    assert response.status_code == 201, response.text
    return response.json()


def _force_status(assessment_id: int, status: str, **fields) -> None:
    """Test shortcut: place an assessment at a later pipeline stage."""
    from app.services import workflow

    db = SessionLocal()
    try:
        assessment = db.get(Assessment, assessment_id)
        assessment.status = status
        assessment.is_draft = False
        for key, value in fields.items():
            setattr(assessment, key, value)
        assessment.workflow_status = workflow.derive_workflow_status(db, assessment)
        db.commit()
    finally:
        db.close()


def _history(assessment_id: int, who: str = "owner") -> list[dict]:
    response = client.get(f"/api/assessments/{assessment_id}/workflow/history", headers=auth(who))
    assert response.status_code == 200, response.text
    return response.json()


# ---------------------------------------------------------------------------
# Acceptance criterion 1: a draft cannot be approved directly.
# ---------------------------------------------------------------------------


def test_draft_cannot_be_approved_or_progressed():
    draft = _create(is_draft=True)
    assert draft["workflow_status"] == "DRAFT"

    # Committee decision straight from draft is refused.
    response = client.post(
        f"/api/assessments/{draft['id']}/committee-decision",
        json={"decision": "approve", "rationale": "LGTM"},
        headers=auth("committee"),
    )
    assert response.status_code in {400, 403, 409}, response.text

    # Nor can it progress into review before being submitted.
    response = client.patch(f"/api/assessments/{draft['id']}/advance-stage", json={}, headers=auth("owner"))
    assert response.status_code == 409, response.text
    assert "Draft" in response.text

    # The service itself refuses DRAFT -> APPROVED outright.
    from fastapi import HTTPException
    from app.services import workflow

    db = SessionLocal()
    try:
        assessment = db.get(Assessment, draft["id"])
        try:
            workflow.transition(db, assessment, "APPROVED", user=None, reason="x", action="TEST")
            raise AssertionError("DRAFT -> APPROVED should have been refused")
        except HTTPException as exc:
            assert exc.status_code == 409
        db.rollback()
    finally:
        db.close()

    detail = client.get(f"/api/assessments/{draft['id']}", headers=auth("owner")).json()
    assert detail["status"] == "INTAKE" and detail["workflow_status"] == "DRAFT"


def test_submit_draft_records_transition_and_target_date():
    draft = _create(is_draft=True)

    # Someone else's draft: not theirs to submit (and not visible).
    response = client.post(f"/api/assessments/{draft['id']}/workflow/submit", headers=auth("other_owner"))
    assert response.status_code == 403, response.text

    response = client.post(f"/api/assessments/{draft['id']}/workflow/submit", headers=auth("owner"))
    assert response.status_code == 200, response.text
    submitted = response.json()
    assert submitted["workflow_status"] == "SUBMITTED"
    assert submitted["target_date"] is not None
    assert submitted["status_due_at"] is not None

    history = _history(draft["id"])
    last = history[-1]
    assert (last["from_workflow_status"], last["to_workflow_status"]) == ("DRAFT", "SUBMITTED")
    assert last["user_id"] == USERS["owner"]


def test_incomplete_draft_cannot_be_submitted():
    draft = _create(is_draft=True, legal_entity=None)
    response = client.post(f"/api/assessments/{draft['id']}/workflow/submit", headers=auth("owner"))
    assert response.status_code == 422, response.text
    assert "Legal entity" in response.text


# ---------------------------------------------------------------------------
# R14.1 / R14.2: permitted transitions and role-based actions.
# ---------------------------------------------------------------------------


def test_invalid_transition_and_wrong_role_are_rejected():
    submitted = _create(is_draft=False)
    _force_status(submitted["id"], "RISK_IDENTIFICATION")

    # A business user cannot run the analyst pipeline.
    response = client.patch(f"/api/assessments/{submitted['id']}/advance-stage", json={}, headers=auth("owner"))
    assert response.status_code == 403, response.text

    # Skipping straight to closure from mid-pipeline isn't a permitted move.
    response = client.post(
        f"/api/assessments/{submitted['id']}/workflow/close",
        json={"reason": "done"},
        headers=auth("analyst"),
    )
    assert response.status_code == 403, response.text

    # The workflow view lists exactly the permitted next statuses.
    summary = client.get(f"/api/assessments/{submitted['id']}/workflow", headers=auth("analyst")).json()
    targets = {t["to_status"]: t["allowed_for_user"] for t in summary["available_transitions"]}
    assert targets == {"INHERENT_RISK_ASSESSMENT": True, "INFORMATION_REQUESTED": True}


def test_business_user_cannot_edit_someone_elses_request():
    submitted = _create(is_draft=False)
    response = client.patch(
        f"/api/assessments/{submitted['id']}",
        json={**FULL_REQUEST, "is_draft": False, "title": "hijacked"},
        headers=auth("other_owner"),
    )
    assert response.status_code == 403, response.text


# ---------------------------------------------------------------------------
# Acceptance criterion 2: unresolved mandatory issues block committee review.
# ---------------------------------------------------------------------------


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


def test_mandatory_issues_block_committee_then_decision_path_is_recorded():
    item = _create(is_draft=False)
    _force_status(item["id"], "SUBMITTED_TO_MANAGER", manager_id=USERS["manager"])

    # No residual score + an unresolved analyst comment => blocked.
    db = SessionLocal()
    db.add(
        AssessmentComment(
            assessment_id=item["id"],
            author_id=USERS["analyst"],
            body="Evidence for vendor due diligence is missing.",
            visibility="ALL",
        )
    )
    db.commit()
    db.close()

    summary = client.get(f"/api/assessments/{item['id']}/workflow", headers=auth("manager")).json()
    assert any("Residual risk" in issue for issue in summary["mandatory_issues"]), summary

    response = client.post(
        f"/api/assessments/{item['id']}/manager-decision",
        json={"decision": "approve"},
        headers=auth("manager"),
    )
    assert response.status_code in {400, 409}, response.text
    assert client.get(f"/api/assessments/{item['id']}", headers=auth("owner")).json()["status"] == "SUBMITTED_TO_MANAGER"

    # Resolve everything: residual risk frozen (with the rest of the
    # decision record's inputs), comment resolved, no blocking findings.
    from decision_fixture import seed_decision_inputs

    db = SessionLocal()
    seed_decision_inputs(db, item["id"])
    for comment in db.query(AssessmentComment).filter(AssessmentComment.assessment_id == item["id"]):
        comment.resolved = True
    db.commit()
    db.close()

    import app.api.approvals as approvals_api

    original = challenge_engine.has_blocking_findings
    # P3: the manager accepts the open findings with a reason instead of a
    # stub hiding them -- readiness reads the findings themselves.
    _resolve_open_findings(item["id"], "manager")
    try:
        # R11: still blocked -- the mandatory challenge review has not
        # been signed off, even though no blocking finding is open.
        response = client.post(
            f"/api/assessments/{item['id']}/manager-decision",
            json={"decision": "approve", "comment": "Challenged and agreed."},
            headers=auth("manager"),
        )
        assert response.status_code == 409, response.text
        assert any("challenge review has not been completed" in issue for issue in response.json()["detail"]["issues"])

        # P3: an independent challenge review, then the manager's sign-off.
        reviewed = client.post(
            f"/api/assessments/{item['id']}/challenge-review/review",
            json={"reason": "Findings reviewed; nothing outstanding."},
            headers=auth("analyst2"),
        )
        assert reviewed.status_code == 201, reviewed.text
        signed = client.post(
            f"/api/assessments/{item['id']}/challenge-review/signoff",
            json={"reason": "Challenge review signed off."},
            headers=auth("manager"),
        )
        assert signed.status_code == 201, signed.text

        response = client.post(
            f"/api/assessments/{item['id']}/manager-decision",
            json={"decision": "approve", "comment": "Challenged and agreed."},
            headers=auth("manager"),
        )
        assert response.status_code == 200, response.text
        assert response.json()["workflow_status"] == "READY_FOR_COMMITTEE"

        # G-5: three eligible members vote, including both representatives.
        for member in ("committee", "fcrm_rep", "business_rep"):
            voted = client.post(
                f"/api/assessments/{item['id']}/committee-votes", json={"vote": "APPROVE"}, headers=auth(member)
            )
            assert voted.status_code == 200, voted.text

        response = client.post(
            f"/api/assessments/{item['id']}/committee-decision",
            json={"decision": "approve", "rationale": "Risk within appetite."},
            headers=auth("committee"),
        )
        assert response.status_code == 200, response.text
        assert response.json()["workflow_status"] == "APPROVED"
    finally:
        challenge_engine.has_blocking_findings = original
        approvals_api.has_blocking_findings = original

    # Acceptance criterion 3: every transition's details are recorded.
    path = [(h["from_workflow_status"], h["to_workflow_status"]) for h in _history(item["id"])]
    assert path[-3:] == [
        ("CHALLENGE_REVIEW", "READY_FOR_COMMITTEE"),
        ("READY_FOR_COMMITTEE", "COMMITTEE_REVIEW"),
        ("COMMITTEE_REVIEW", "APPROVED"),
    ], path
    for entry in _history(item["id"])[-3:]:
        assert entry["from_status"] and entry["to_status"]
        assert entry["user_id"] is not None and entry["actor"]
        assert entry["created_at"]
        assert entry["reason"].strip()

    # Close: blocked while an action item is open, allowed after.
    db = SessionLocal()
    action = ActionItem(
        assessment_id=item["id"],
        source_type="MONITORING_ENHANCEMENT",
        title="Monitor first 3 months",
        owner="Retail Cards",
        priority="LOW",
        created_by="test",
    )
    db.add(action)
    db.commit()
    action_id = action.id
    db.close()

    response = client.post(f"/api/assessments/{item['id']}/workflow/close", json={"reason": "Complete"}, headers=auth("analyst"))
    assert response.status_code == 409, response.text

    db = SessionLocal()
    db.get(ActionItem, action_id).status = "COMPLETED"
    db.commit()
    db.close()

    response = client.post(f"/api/assessments/{item['id']}/workflow/close", json={"reason": "Complete"}, headers=auth("analyst"))
    assert response.status_code == 200, response.text
    assert response.json()["workflow_status"] == "CLOSED"

    # Closed is final: no amendment path out.
    response = client.post(f"/api/assessments/{item['id']}/amend", json={"reason": "reopen"}, headers=auth("committee"))
    assert response.status_code in {400, 409}, response.text


# ---------------------------------------------------------------------------
# R14.3 + acceptance criterion 4: owner / next action and the work queue.
# ---------------------------------------------------------------------------


def test_assigned_task_appears_in_responsible_users_work_queue():
    item = _create(is_draft=False)  # SUBMITTED -> FCRM analyst queue

    summary = client.get(f"/api/assessments/{item['id']}/workflow", headers=auth("owner")).json()
    assert summary["owner"]["party"] == "FCRM_ANALYST"
    assert summary["owner"]["next_action"]
    assert summary["owner"]["team"]

    def queue_ids(who):
        body = client.get("/api/workflow/work-queue", headers=auth(who)).json()
        return {task["assessment_id"] for task in body["tasks"]}

    # Unassigned: every analyst sees it in the shared queue.
    assert item["id"] in queue_ids("analyst")
    assert item["id"] in queue_ids("analyst2")

    response = client.post(
        f"/api/assessments/{item['id']}/workflow/assign",
        json={"user_id": USERS["analyst2"], "reason": "Cards specialist"},
        headers=auth("analyst"),
    )
    assert response.status_code == 200, response.text
    assert response.json()["owner"]["owner_name"] == "Analyst2"

    assert item["id"] in queue_ids("analyst2")
    assert item["id"] not in queue_ids("analyst")

    # A business user can't be given an analyst task.
    response = client.post(
        f"/api/assessments/{item['id']}/workflow/assign",
        json={"user_id": USERS["other_owner"]},
        headers=auth("analyst"),
    )
    assert response.status_code in {400, 403}, response.text

    # Owner-side task: an information request lands in the owner's queue.
    _force_status(item["id"], "HUMAN_REVIEW")
    response = client.post(
        f"/api/assessments/{item['id']}/request-information",
        json={"target": "Business owner", "note": "Send vendor contract"},
        headers=auth("analyst"),
    )
    assert response.status_code == 200, response.text
    assert response.json()["workflow_status"] == "INFORMATION_REQUESTED"
    assert item["id"] in queue_ids("owner")

    response = client.post(
        f"/api/assessments/{item['id']}/provide-information",
        json={"response": "Attached."},
        headers=auth("owner"),
    )
    assert response.status_code == 200, response.text
    assert response.json()["workflow_status"] == "ANALYST_REVIEW"


# ---------------------------------------------------------------------------
# R14.4 + acceptance criterion 5: due dates and escalation.
# ---------------------------------------------------------------------------


def test_overdue_assessment_is_escalated():
    item = _create(is_draft=False)
    _force_status(item["id"], "EVIDENCE_COLLECTION")  # Intake Validation -> owner confirms

    db = SessionLocal()
    assessment = db.get(Assessment, item["id"])
    past = datetime.now(timezone.utc) - timedelta(days=2)
    assessment.status_entered_at = past - timedelta(days=3)
    assessment.status_due_at = past
    assessment.escalation_level = 0
    db.commit()
    db.close()

    summary = client.get(f"/api/assessments/{item['id']}/workflow", headers=auth("owner")).json()
    assert summary["sla_state"] == "OVERDUE"
    assert summary["escalation_level"] == 1
    # Owner-held task escalates to the owner's line manager.
    assert summary["escalated_to_id"] == USERS["manager"]
    assert "overdue" in summary["escalation_note"]

    queue = client.get("/api/workflow/work-queue", headers=auth("manager")).json()
    assert item["id"] in {e["assessment_id"] for e in queue["escalations"]}

    db = SessionLocal()
    escalation_events = (
        db.query(AuditEvent)
        .filter(AuditEvent.assessment_id == item["id"], AuditEvent.action == "WORKFLOW_ESCALATED")
        .count()
    )
    db.close()
    assert escalation_events == 1

    # Re-running doesn't re-escalate at the same level.
    client.get(f"/api/assessments/{item['id']}/workflow", headers=auth("owner"))
    db = SessionLocal()
    assert (
        db.query(AuditEvent)
        .filter(AuditEvent.assessment_id == item["id"], AuditEvent.action == "WORKFLOW_ESCALATED")
        .count()
        == 1
    )
    db.close()


def test_target_date_requires_reviewer_role():
    item = _create(is_draft=False)
    response = client.patch(
        f"/api/assessments/{item['id']}/workflow/target-date",
        json={"target_date": "2026-12-31", "reason": "Launch moved"},
        headers=auth("owner"),
    )
    assert response.status_code == 403, response.text

    response = client.patch(
        f"/api/assessments/{item['id']}/workflow/target-date",
        json={"target_date": "2026-12-31", "reason": "Launch moved"},
        headers=auth("analyst"),
    )
    assert response.status_code == 200, response.text
    assert response.json()["target_date"] == "2026-12-31"


if __name__ == "__main__":
    tests = [value for name, value in sorted(globals().items()) if name.startswith("test_") and callable(value)]
    failures = 0
    for test in tests:
        try:
            test()
            print(f"PASS  {test.__name__}")
        except Exception as exc:  # noqa: BLE001
            failures += 1
            import traceback

            print(f"FAIL  {test.__name__}: {exc!r}")
            traceback.print_exc()
    print(f"\n{len(tests) - failures}/{len(tests)} passed")
    raise SystemExit(1 if failures else 0)

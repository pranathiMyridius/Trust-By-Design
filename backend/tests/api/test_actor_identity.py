"""
Regression tests for defect 7: "who did this" fields (submitted_by,
changed_by, added_by, excluded_by, flagged_by, ...) were taken from the
request body, so audit records could name anyone. They now always come
from the signed-in user, and the matching audit events carry actor_id.
"""

from app.database import SessionLocal
from app.models.assessment import Assessment
from app.models.audit_event import AuditEvent
from tests.conftest import ok

SPOOF = "Somebody Else"


def _latest_event(aid: int, contains: str) -> AuditEvent:
    db = SessionLocal()
    try:
        return (
            db.query(AuditEvent)
            .filter(AuditEvent.assessment_id == aid, AuditEvent.details.contains(contains))
            .order_by(AuditEvent.id.desc())
            .first()
        )
    finally:
        db.close()


def test_submitter_is_the_signed_in_user(users, create_assessment):
    aid = create_assessment(submitted_by=SPOOF)["id"]

    db = SessionLocal()
    try:
        assert db.get(Assessment, aid).submitted_by == "Owner"
    finally:
        db.close()

    event = _latest_event(aid, "submitted manually")
    assert (event.actor, event.actor_id) == ("Owner", users["owner"])


def test_pipeline_actions_record_the_signed_in_user(client, auth, users, analysed_assessment):
    aid = analysed_assessment()["id"]
    analyst = auth("analyst")

    ok(
        client.patch(
            f"/api/assessments/{aid}/intelligence",
            json={"customer_type": "Retail merchants", "changed_by": SPOOF, "change_reason": "Corrected segment."},
            headers=analyst,
        )
    )
    event = _latest_event(aid, "Corrected segment.")
    assert (event.actor, event.actor_id) == ("Analyst", users["analyst"])

    factor = ok(
        client.post(
            f"/api/assessments/{aid}/risk-factors",
            json={
                "category": "CONTROL_ENVIRONMENT_RISK",
                "rationale": "External acquiring processor handles settlement.",
                "added_by": SPOOF,
            },
            headers=analyst,
        ),
        201,
    )
    assert factor["added_by"] == "Analyst"

    excluded = ok(
        client.patch(
            f"/api/assessments/{aid}/risk-factors/{factor['id']}/exclude",
            json={"reason": "Covered by the processor contract.", "excluded_by": SPOOF},
            headers=analyst,
        )
    )
    assert excluded["excluded_by"] == "Analyst"

    trigger = ok(
        client.post(
            f"/api/assessments/{aid}/reassessment/flag-trigger",
            json={
                "trigger_type": "REGULATORY_POLICY_CHANGE",
                "description": "New EU AML guidance.",
                "flagged_by": SPOOF,
            },
            headers=analyst,
        ),
        201,
    )
    assert trigger["detected_by"] == "Analyst"
    event = _latest_event(aid, "New EU AML guidance.")
    assert (event.actor, event.actor_id) == ("Analyst", users["analyst"])

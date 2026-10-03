"""
Regression tests for three governance defects:

1. R15.2: the requester (or the approving manager) could give committee
   sign-off on their own assessment outside a delegation.
2. Stage 12: several endpoints still accepted edits after a final decision.
3. R6.2/R6.7: the Manual Scoring Calculator overwrote the deterministic
   score with a client-computed value, no reason and a client-supplied actor.
"""

import pytest

from app.database import SessionLocal
from app.models.assessment import Assessment
from app.models.audit_event import AuditEvent
from app.models.inherent_risk_calculation import InherentRiskCalculation
from tests.conftest import ok


def _set_status(aid: int, status: str, **fields) -> None:
    db = SessionLocal()
    try:
        assessment = db.get(Assessment, aid)
        assessment.status = status
        for key, value in fields.items():
            setattr(assessment, key, value)
        db.commit()
    finally:
        db.close()


MANUAL_SCORE = {
    "weighted_total": 72,
    "included_factors": {"GEOGRAPHIC": 80, "PRODUCT_SERVICE": 60},
    "factor_weights": {"GEOGRAPHIC": 0.5, "PRODUCT_SERVICE": 0.5},
}


# -- 1. separation of duties at committee ---------------------------------


def test_requester_cannot_sign_off_or_vote_on_own_assessment(client, auth, users, create_assessment):
    aid = create_assessment(who="committee")["id"]
    _set_status(aid, "READY_FOR_COMMITTEE", owner_id=users["committee"])

    decision = client.post(
        f"/api/assessments/{aid}/committee-decision",
        json={"decision": "approve", "rationale": "Looks fine."},
        headers=auth("committee"),
    )
    assert decision.status_code == 403
    assert "submitted this assessment" in decision.json()["detail"]

    vote = client.post(
        f"/api/assessments/{aid}/committee-votes",
        json={"vote": "APPROVE"},
        headers=auth("committee"),
    )
    assert vote.status_code == 403
    assert "submitted this assessment" in vote.json()["detail"]


def test_member_who_gave_manager_approval_cannot_vote_at_committee(
    client, auth, users, create_assessment
):
    aid = create_assessment()["id"]
    _set_status(aid, "READY_FOR_COMMITTEE", manager_decided_by_id=users["committee"])

    vote = client.post(
        f"/api/assessments/{aid}/committee-votes",
        json={"vote": "APPROVE"},
        headers=auth("committee"),
    )
    assert vote.status_code == 403, vote.text
    assert "approved this assessment as its manager" in vote.json()["detail"]


# -- 2. read-only after a final decision -----------------------------------


@pytest.mark.parametrize("status", ["APPROVED", "APPROVED_WITH_CONDITIONS", "REJECTED", "CLOSED"])
def test_finally_decided_assessment_rejects_edits(client, auth, create_assessment, status):
    aid = create_assessment()["id"]
    _set_status(aid, status)
    analyst = auth("analyst")

    attempts = {
        "manual-score": client.patch(
            f"/api/assessments/{aid}/manual-score",
            json={**MANUAL_SCORE, "reason": "Late change."},
            headers=analyst,
        ),
        "intelligence": client.patch(
            f"/api/assessments/{aid}/intelligence",
            json={"customer_type": "Retail", "changed_by": "x", "change_reason": "y"},
            headers=analyst,
        ),
        "overrides": client.post(
            f"/api/assessments/{aid}/overrides",
            json={
                "section": "FACTOR_RATING",
                "field_name": "likelihood",
                "human_value": "5",
                "reason": "Late change.",
            },
            headers=analyst,
        ),
        "draft/generate": client.post(f"/api/assessments/{aid}/draft/generate", headers=analyst),
    }

    for name, response in attempts.items():
        assert response.status_code == 409, f"{name}: {response.status_code} {response.text}"
        assert "read-only" in response.json()["detail"], f"{name}: {response.text}"


# -- 3. manual score is a recorded override, not a score overwrite ---------


def test_manual_score_requires_a_reason(client, auth, create_assessment):
    aid = create_assessment()["id"]

    for reason in (None, "", "   "):
        body = dict(MANUAL_SCORE) if reason is None else {**MANUAL_SCORE, "reason": reason}
        response = client.patch(f"/api/assessments/{aid}/manual-score", json=body, headers=auth("analyst"))
        assert response.status_code == 422, response.text


def test_manual_score_records_override_without_touching_calculated_score(
    client, auth, users, create_assessment
):
    aid = create_assessment()["id"]
    before = ok(client.get(f"/api/assessments/{aid}", headers=auth("analyst")))

    after = ok(
        client.patch(
            f"/api/assessments/{aid}/manual-score",
            json={
                **MANUAL_SCORE,
                "reason": "Processor has a sanctions history.",
                # Ignored: band and user come from the server.
                "risk_level": "LOW",
                "actor": "Someone Else",
            },
            headers=auth("analyst"),
        )
    )

    assert after["overall_score"] == before["overall_score"]
    assert after["risk_level"] == before["risk_level"]

    db = SessionLocal()
    try:
        calculation = (
            db.query(InherentRiskCalculation)
            .filter(
                InherentRiskCalculation.assessment_id == aid,
                InherentRiskCalculation.is_current.is_(True),
            )
            .one()
        )
        assert calculation.overridden
        assert calculation.override_value == 72
        assert calculation.override_band != "LOW"
        assert calculation.override_reason == "Processor has a sanctions history."
        assert calculation.override_by == "Analyst"

        event = (
            db.query(AuditEvent)
            .filter(AuditEvent.assessment_id == aid, AuditEvent.action == "MANUAL_SCORE_OVERRIDE")
            .order_by(AuditEvent.id.desc())
            .first()
        )
        assert event.actor == "Analyst"
        assert event.actor_id == users["analyst"]
        assert "Reason: Processor has a sanctions history." in event.details
    finally:
        db.close()

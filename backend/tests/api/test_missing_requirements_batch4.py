"""
Previously missing requirements, batch 4:

Stage 18 AC2  an expired approval is found by the scheduled check and
              reaches the owner's and reviewer's work queues
R8.6          recommended conditions for elevated residual risk, with an
              accept / modify / reject decision and reason
R8            the human-confirmed residual risk beside the calculated one
"""

from datetime import date, timedelta

from app.database import SessionLocal
from app.models.assessment import Assessment
from app.models.audit_event import AuditEvent
from app.models.reassessment_trigger import ReassessmentTrigger
from app.models.residual_risk_calculation import ResidualRiskCalculation
from app.models.risk_factor import RiskFactor
from app.services.reassessment_service import sweep_review_dates
from tests.conftest import ok



def _utc_today() -> date:
    """The review-date checks use the UTC date (reassessment_service); a
    local date.today() disagrees with it for part of the day east of UTC."""

    from datetime import datetime, timezone

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


# -- Stage 18: expiry ----------------------------------------------------------


def test_expired_approval_reaches_owner_and_reviewer(client, auth, users, create_assessment):
    aid = create_assessment()["id"]
    _set(aid, status="APPROVED", manager_id=users["manager"], next_review_date=_utc_today() - timedelta(days=1))

    db = SessionLocal()
    try:
        created = sweep_review_dates(db)
        assert [t.trigger_type for t in created if t.assessment_id == aid] == ["EXPIRY"]
        # A second sweep doesn't re-flag an open trigger.
        assert not [t for t in sweep_review_dates(db) if t.assessment_id == aid]
        notice = (
            db.query(AuditEvent)
            .filter(AuditEvent.assessment_id == aid, AuditEvent.details.contains("Notified:"))
            .one()
        )
        assert "Owner" in notice.details and "Manager" in notice.details
    finally:
        db.close()

    for who in ("owner", "manager"):
        queue = ok(client.get("/api/workflow/work-queue", headers=auth(who)))
        alerts = [a for a in queue["reassessment_alerts"] if a["assessment_id"] == aid]
        assert [a["trigger_type"] for a in alerts] == ["EXPIRY"], who

    other = ok(client.get("/api/workflow/work-queue", headers=auth("other_owner")))
    assert not [a for a in other["reassessment_alerts"] if a["assessment_id"] == aid]


def test_review_due_soon_is_a_periodic_review_alert(client, auth, create_assessment):
    aid = create_assessment()["id"]
    _set(aid, status="APPROVED", next_review_date=_utc_today() + timedelta(days=10))

    queue = ok(client.get("/api/workflow/work-queue", headers=auth("owner")))
    assert [a["trigger_type"] for a in queue["reassessment_alerts"] if a["assessment_id"] == aid] == ["PERIODIC_REVIEW"]

    db = SessionLocal()
    try:
        assert db.query(ReassessmentTrigger).filter(ReassessmentTrigger.assessment_id == aid).count() == 1
    finally:
        db.close()


# -- R8.6 / R8: residual review ---------------------------------------------------


def _elevated_residual(aid: int, band: str = "HIGH", score: float = 70.0) -> None:
    db = SessionLocal()
    try:
        db.add(
            ResidualRiskCalculation(
                assessment_id=aid,
                inherent_band="CRITICAL",
                inherent_score=85.0,
                control_rating="PARTIAL",
                residual_band=band,
                residual_score=score,
                frozen=True,
                is_current=True,
                version=1,
            )
        )
        for factor in db.query(RiskFactor).filter(RiskFactor.assessment_id == aid, RiskFactor.is_current.is_(True)):
            if factor.category in {"GEOGRAPHIC_RISK", "THIRD_PARTY_VENDOR_RISK"}:
                factor.applicable = True
                factor.excluded = False
                factor.severity = "HIGH"
        db.commit()
    finally:
        db.close()


def test_recommended_conditions_follow_the_residual_risk(client, auth, analysed_assessment):
    aid = analysed_assessment()["id"]
    url = f"/api/assessments/{aid}/recommended-conditions"

    assert ok(client.get(url, headers=auth("analyst"))) == []  # nothing elevated yet

    _elevated_residual(aid)
    conditions = {c["condition_type"]: c for c in ok(client.get(url, headers=auth("analyst")))}
    assert {"ADDITIONAL_MONITORING", "PERIODIC_REASSESSMENT", "GEOGRAPHIC_RESTRICTIONS", "ADDITIONAL_VENDOR_CONTROLS", "MANAGEMENT_APPROVAL"} <= set(conditions)
    assert all(c["status"] == "PROPOSED" and c["rationale"] for c in conditions.values())
    assert "Geographic risk is rated HIGH" in conditions["GEOGRAPHIC_RESTRICTIONS"]["rationale"]

    decide = lambda cid, body, who="analyst": client.post(  # noqa: E731
        f"{url}/{cid}/decision", json=body, headers=auth(who)
    )

    assert decide(conditions["ADDITIONAL_MONITORING"]["id"], {"decision": "accept", "reason": " "}).status_code == 422
    assert decide(conditions["ADDITIONAL_MONITORING"]["id"], {"decision": "modify", "reason": "Tighter."}).status_code == 422
    assert decide(conditions["ADDITIONAL_MONITORING"]["id"], {"decision": "accept", "reason": "Agreed."}, "owner").status_code == 403

    accepted = ok(decide(conditions["ADDITIONAL_MONITORING"]["id"], {"decision": "accept", "reason": "Agreed."}))
    assert accepted["status"] == "ACCEPTED" and accepted["final_text"] == accepted["recommended_text"]
    assert accepted["decided_by"] == "Analyst"

    modified = ok(
        decide(
            conditions["GEOGRAPHIC_RESTRICTIONS"]["id"],
            {"decision": "modify", "reason": "Poland only for now.", "text": "Limit to Germany and Poland."},
        )
    )
    assert modified["status"] == "MODIFIED" and modified["final_text"] == "Limit to Germany and Poland."

    rejected = ok(decide(conditions["PERIODIC_REASSESSMENT"]["id"], {"decision": "reject", "reason": "Annual review suffices."}))
    assert rejected["status"] == "REJECTED" and rejected["final_text"] is None

    # Decisions survive a refresh; only adopted conditions reach the draft.
    again = {c["condition_type"]: c for c in ok(client.get(url, headers=auth("analyst")))}
    assert again["GEOGRAPHIC_RESTRICTIONS"]["status"] == "MODIFIED"
    draft = ok(client.post(f"/api/assessments/{aid}/draft/generate", headers=auth("analyst")))
    assert "Limit to Germany and Poland." in draft["recommended_conditions"]
    assert accepted["final_text"] in draft["recommended_conditions"]
    assert again["PERIODIC_REASSESSMENT"]["recommended_text"] not in draft["recommended_conditions"]


def test_residual_risk_can_be_confirmed_beside_the_calculation(client, auth, analysed_assessment):
    aid = analysed_assessment()["id"]
    url = f"/api/assessments/{aid}/residual-risk/confirm"

    assert client.post(url, json={"band": "HIGH", "reason": "x"}, headers=auth("analyst")).status_code == 409

    _elevated_residual(aid)
    assert client.post(url, json={"band": "HIGH", "reason": " "}, headers=auth("analyst")).status_code == 422
    mismatch = client.post(url, json={"band": "LOW", "score": 90, "reason": "x"}, headers=auth("analyst"))
    assert mismatch.status_code == 422

    confirmed = ok(
        client.post(
            url,
            json={"band": "MEDIUM", "score": 45, "reason": "Vendor controls verified on site."},
            headers=auth("analyst"),
        )
    )
    assert confirmed["residual_band"] == "HIGH" and confirmed["residual_score"] == 70.0
    assert confirmed["confirmed_band"] == "MEDIUM" and confirmed["confirmed_by"] == "Analyst"
    assert confirmed["confirmation_differs"] is True
    assert confirmed["confirmed_score_difference"] == -25.0

    residual = ok(client.get(f"/api/assessments/{aid}/residual-risk", headers=auth("analyst")))
    assert residual["confirmed_band"] == "MEDIUM"

"""
Partly-done requirements completed in batch 1:

R1.2   unsupported change types can't be submitted
R6.7   an override survives a recalculation that reproduces its result,
       and is dropped -- visibly -- when the result changes
R10.3  a rating that departs from the AI's suggestion needs a reason, and
R10.4  lands in the AI-vs-human override ledger
R11.1  a disabled challenge trigger produces no findings
R17.1  reports group by business unit and skip soft-deleted assessments
R18.4  reassessment needs the right role and a decided assessment
"""

from app.database import SessionLocal
from app.models.assessment import Assessment
from app.models.assessment_override import AssessmentOverride
from app.models.audit_event import AuditEvent
from app.models.audit_trail import AssessmentRetention
from app.models.inherent_risk_calculation import InherentRiskCalculation
from tests.conftest import FULL_REQUEST, ok


def _factors(client, auth, aid):
    return ok(client.get(f"/api/assessments/{aid}/risk-factors", headers=auth("analyst")))


def _rate(client, auth, aid, factor_id, likelihood, impact, reason=None):
    body = {"likelihood": likelihood, "impact": impact}
    if reason is not None:
        body["reason"] = reason
    return client.patch(
        f"/api/assessments/{aid}/risk-factors/{factor_id}/rating", json=body, headers=auth("analyst")
    )


def _current_calc(aid: int) -> InherentRiskCalculation:
    db = SessionLocal()
    try:
        return (
            db.query(InherentRiskCalculation)
            .filter(
                InherentRiskCalculation.assessment_id == aid,
                InherentRiskCalculation.is_current.is_(True),
            )
            .one()
        )
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


# -- R1.2 ---------------------------------------------------------------------


def test_unsupported_change_type_cannot_be_submitted(client, auth):
    body = {**FULL_REQUEST, "change_type": "MATERIAL_CHANGE", "is_draft": False}
    response = client.post("/api/assessments", json=body, headers=auth("owner"))
    assert response.status_code == 422
    assert any("not a supported type" in field for field in response.json()["detail"]["missing_fields"])

    # A draft can still hold it while the request is being completed.
    ok(client.post("/api/assessments", json={**body, "is_draft": True}, headers=auth("owner")), 201)


# -- R10.3 / R10.4 --------------------------------------------------------------


def test_rating_against_the_ai_suggestion_needs_a_reason(client, auth, analysed_assessment):
    aid = analysed_assessment()["id"]
    result = ok(client.post(f"/api/assessments/{aid}/risk-factors/suggest-ratings", json={}, headers=auth("analyst")))
    factor = next(f for f in result["factors"] if f["ai_suggested_likelihood"] is not None)
    likelihood = 1 if factor["ai_suggested_likelihood"] != 1 else 2

    missing = _rate(client, auth, aid, factor["id"], likelihood, factor["ai_suggested_impact"])
    assert missing.status_code == 422
    assert "differs from the AI's suggestion" in missing.json()["detail"]

    # Agreeing with the AI needs no reason.
    ok(_rate(client, auth, aid, factor["id"], factor["ai_suggested_likelihood"], factor["ai_suggested_impact"]))

    rated = ok(
        _rate(client, auth, aid, factor["id"], likelihood, factor["ai_suggested_impact"], "Processor history.")
    )
    assert rated["rating_source"] == "ANALYST_OVERRIDE"

    db = SessionLocal()
    try:
        ledger = (
            db.query(AssessmentOverride)
            .filter(AssessmentOverride.assessment_id == aid, AssessmentOverride.section == "FACTOR_RATING")
            .one()
        )
        assert ledger.entity_id == str(factor["id"])
        assert ledger.ai_value == f"{factor['ai_suggested_likelihood']} x {factor['ai_suggested_impact']}"
        assert ledger.human_value == f"{likelihood} x {factor['ai_suggested_impact']}"
        assert ledger.reason == "Processor history."
        assert ledger.overridden_by == "Analyst"
    finally:
        db.close()


# -- R6.7 -----------------------------------------------------------------------


def test_inherent_override_follows_its_calculated_result(client, auth, analysed_assessment):
    aid = analysed_assessment()["id"]
    applicable = [f for f in _factors(client, auth, aid) if f["applicable"] and not f["excluded"]]
    for factor in applicable:
        ok(_rate(client, auth, aid, factor["id"], 3, 3))

    ok(
        client.post(
            f"/api/assessments/{aid}/inherent-risk/override",
            json={"override_value": 90, "reason": "Sanctions exposure understated."},
            headers=auth("analyst"),
        )
    )
    assert _current_calc(aid).overridden

    # Same inputs -> same calculated result -> the override carries over.
    ok(_rate(client, auth, aid, applicable[0]["id"], 3, 3))
    carried = _current_calc(aid)
    assert carried.overridden and carried.override_value == 90
    assert carried.override_reason == "Sanctions exposure understated."

    # Different inputs -> the override no longer refers to this result.
    ok(_rate(client, auth, aid, applicable[0]["id"], 5, 5))
    assert not _current_calc(aid).overridden

    db = SessionLocal()
    try:
        assert (
            db.query(AuditEvent)
            .filter(AuditEvent.assessment_id == aid, AuditEvent.details.contains("no longer applies"))
            .count()
            == 1
        )
    finally:
        db.close()


# -- R11.1 ----------------------------------------------------------------------


def test_disabled_trigger_produces_no_findings(client, auth, analysed_assessment):
    aid = analysed_assessment()["id"]
    url = f"/api/assessments/{aid}/challenge-review"
    before = ok(client.get(url, headers=auth("analyst")))
    assert any(f["category"] == "CONTRADICTION" and f["resolution_status"] == "OPEN" for f in before["findings"])

    assert client.patch(
        "/api/challenge-triggers", json={"disabled_triggers": ["RISK_HIGH_OR_CRITICAL"]}, headers=auth("manager")
    ).status_code == 422

    config = ok(
        client.patch(
            "/api/challenge-triggers", json={"disabled_triggers": ["CONTRADICTIONS_EXIST"]}, headers=auth("manager")
        )
    )
    assert config["disabled_triggers"] == ["CONTRADICTIONS_EXIST"]
    assert "RISK_HIGH_OR_CRITICAL" in config["mandatory_triggers"]

    try:
        after = ok(client.get(url, headers=auth("analyst")))
        assert not any(
            f["category"] == "CONTRADICTION" and f["resolution_status"] == "OPEN" for f in after["findings"]
        )
        closed = [f for f in after["findings"] if f["category"] == "CONTRADICTION"]
        assert closed and all("disabled" in (f["resolution_note"] or "") for f in closed)
        trigger = next(t for t in after["triggers"] if t["name"] == "CONTRADICTIONS_EXIST")
        assert trigger["enabled"] is False and trigger["fired"] is False
    finally:
        ok(client.patch("/api/challenge-triggers", json={"disabled_triggers": []}, headers=auth("manager")))


# -- R17.1 ----------------------------------------------------------------------


def test_reports_group_by_business_unit_and_skip_deleted(client, auth, create_assessment):
    kept = create_assessment(business_unit="Cards BU R17")["id"]
    deleted = create_assessment(business_unit="Deleted BU R17")["id"]
    db = SessionLocal()
    try:
        db.add(AssessmentRetention(assessment_id=deleted, is_deleted=True, deleted_by="Admin"))
        db.commit()
    finally:
        db.close()

    report = ok(client.get("/api/reports/operational", headers=auth("admin")))
    units = {row["key"] for row in report["by_business_unit"]}
    assert "Cards BU R17" in units and "Deleted BU R17" not in units
    assert any(row["key"] == FULL_REQUEST["legal_entity"] for row in report["by_legal_entity"])
    assert kept


# -- R18.4 ----------------------------------------------------------------------


def test_reassessment_needs_a_decided_assessment_and_the_right_role(client, auth, create_assessment):
    aid = create_assessment()["id"]
    propose = lambda who: client.post(  # noqa: E731
        f"/api/assessments/{aid}/reassessment/propose-change",
        json={"changes": {"countries_jurisdictions": "Germany, Poland, France"}, "reason": "Adding France."},
        headers=auth(who),
    )

    assert propose("owner").status_code == 409  # still at INTAKE

    _set(aid, status="APPROVED")
    created = ok(propose("owner"), 201)
    assert created["reassessment_id"]
    assert propose("analyst").status_code == 409  # one already in progress

    trigger_url = f"/api/assessments/{aid}/reassessment/flag-trigger"
    trigger = ok(
        client.post(
            trigger_url,
            json={"trigger_type": "REGULATORY_POLICY_CHANGE", "description": "New guidance."},
            headers=auth("owner"),
        ),
        201,
    )
    resolve_url = f"/api/assessments/{aid}/reassessment/triggers/{trigger['id']}"
    assert client.patch(
        resolve_url, json={"status": "DISMISSED", "dismissed_reason": "n/a"}, headers=auth("owner")
    ).status_code == 403
    ok(client.patch(resolve_url, json={"status": "REASSESSMENT_CREATED"}, headers=auth("analyst")))

    # Without a reassessment, "reassessment created" can't be claimed.
    other = create_assessment()["id"]
    _set(other, status="APPROVED")
    other_trigger = ok(
        client.post(
            f"/api/assessments/{other}/reassessment/flag-trigger",
            json={"trigger_type": "SIGNIFICANT_CONTROL_FAILURE", "description": "TM outage."},
            headers=auth("analyst"),
        ),
        201,
    )
    response = client.patch(
        f"/api/assessments/{other}/reassessment/triggers/{other_trigger['id']}",
        json={"status": "REASSESSMENT_CREATED"},
        headers=auth("analyst"),
    )
    assert response.status_code == 409

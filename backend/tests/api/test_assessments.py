"""
FastAPI endpoints for assessments, driven through TestClient exactly as
the React app calls them: create, retrieve, list, update, validation,
access control, analysis, rating and the resulting score.
"""

import pytest

import app.api.assessments as assessments_api
from app.risk_engine.scoring import compute_factor_score
from tests.conftest import FULL_REQUEST, ok
from tests.support.fake_llm import FakeLLM


def factors(client, auth, aid, who="analyst"):
    return ok(client.get(f"/api/assessments/{aid}/risk-factors", headers=auth(who)))


def confirmed_assessment(client, auth, create_assessment) -> int:
    """An assessment at EVIDENCE_COLLECTION whose profile the owner has
    confirmed -- the precondition (R3.3) for running risk identification."""

    aid = create_assessment()["id"]
    ok(client.patch(f"/api/assessments/{aid}/advance-stage", json={}, headers=auth("owner")))
    # Creates the structured profile, then refuses until it is confirmed.
    client.patch(f"/api/assessments/{aid}/advance-stage", json={}, headers=auth("analyst"))
    ok(client.post(f"/api/assessments/{aid}/intelligence/confirm", json={}, headers=auth("owner")))
    return aid


def rate(client, auth, aid, factor_id, likelihood, impact):
    return client.patch(
        f"/api/assessments/{aid}/risk-factors/{factor_id}/rating",
        json={"likelihood": likelihood, "impact": impact, "rated_by": "Analyst"},
        headers=auth("analyst"),
    )


# -- service basics ----------------------------------------------------------------


def test_health_and_root(client):
    assert ok(client.get("/health")) == {"status": "healthy"}
    assert ok(client.get("/"))["status"] == "running"


def test_assessment_api_requires_authentication(client):
    assert client.get("/api/assessments").status_code == 401
    assert client.post("/api/assessments", json=FULL_REQUEST).status_code == 401


def test_login_rejects_a_wrong_password(client, users):
    response = client.post("/api/auth/login", json={"email": "owner@test.io", "password": "nope"})
    assert response.status_code == 401


# -- creation and validation ----------------------------------------------------------


def test_create_submitted_assessment(create_assessment):
    created = create_assessment()
    assert created["id"] > 0
    assert created["reference_id"]
    assert created["status"] == "INTAKE"
    assert created["is_draft"] is False
    assert created["title"] == FULL_REQUEST["title"]
    assert created["priority"], "submitted requests are triaged"
    assert created["overall_score"] is None and created["risk_level"] is None


def test_draft_needs_only_a_title(client, auth):
    draft = ok(
        client.post("/api/assessments", json={"title": "Half-finished idea", "is_draft": True}, headers=auth("owner")),
        201,
    )
    assert draft["is_draft"] is True
    assert draft["workflow_status"] == "DRAFT"


def test_submission_with_missing_mandatory_fields_is_rejected(client, auth):
    body = {**FULL_REQUEST, "is_draft": False, "customer_segment": "", "countries_jurisdictions": "   "}
    response = client.post("/api/assessments", json=body, headers=auth("owner"))
    assert response.status_code == 422
    detail = response.json()["detail"]
    assert "Customer segment" in detail["missing_fields"]
    assert "Countries and jurisdictions involved" in detail["missing_fields"]


@pytest.mark.parametrize(
    "body",
    [{}, {"is_draft": True}, {"title": None, "is_draft": True}, {"title": "x", "is_draft": "sometimes"}],
    ids=["empty", "no-title", "null-title", "bad-bool"],
)
def test_malformed_payloads_are_422(client, auth, body):
    assert client.post("/api/assessments", json=body, headers=auth("owner")).status_code == 422


# -- retrieval, listing and access control -------------------------------------------------


def test_get_by_id(client, auth, create_assessment):
    created = create_assessment()
    fetched = ok(client.get(f"/api/assessments/{created['id']}", headers=auth("owner")))
    assert fetched["id"] == created["id"]
    assert fetched["description"] == FULL_REQUEST["description"]


def test_unknown_assessment_is_404(client, auth):
    assert client.get("/api/assessments/987654", headers=auth("analyst")).status_code == 404


def test_business_user_cannot_see_someone_elses_assessment(client, auth, create_assessment):
    created = create_assessment(who="owner")
    assert client.get(f"/api/assessments/{created['id']}", headers=auth("other_owner")).status_code == 403
    listed = ok(client.get("/api/assessments", headers=auth("other_owner")))
    assert created["id"] not in {item["id"] for item in listed}


def test_analyst_sees_every_assessment(client, auth, create_assessment):
    created = create_assessment(who="owner")
    listed = ok(client.get("/api/assessments", headers=auth("analyst")))
    assert created["id"] in {item["id"] for item in listed}


def test_list_supports_search_and_paging(client, auth, create_assessment):
    marker = "Zebra-unique-product"
    create_assessment(title=f"{marker} one")
    create_assessment(title=f"{marker} two")
    response = client.get(f"/api/assessments?search={marker}&limit=1", headers=auth("analyst"))
    assert response.status_code == 200
    assert len(response.json()) == 1
    assert int(response.headers["X-Total-Count"]) == 2


def test_update_edits_an_intake_assessment(client, auth, create_assessment):
    created = create_assessment()
    updated = ok(
        client.patch(
            f"/api/assessments/{created['id']}",
            json={**FULL_REQUEST, "title": "Renamed request", "is_draft": False},
            headers=auth("owner"),
        )
    )
    assert updated["title"] == "Renamed request"


# -- status changes -------------------------------------------------------------------------


def test_legacy_status_endpoint_rejects_unknown_status(client, auth, create_assessment):
    aid = create_assessment()["id"]
    assert client.patch(f"/api/assessments/{aid}/status?status=PARTYING", headers=auth("analyst")).status_code == 400


def test_legacy_status_endpoint_cannot_skip_stages(client, auth, create_assessment):
    aid = create_assessment()["id"]
    response = client.patch(f"/api/assessments/{aid}/status?status=APPROVED", headers=auth("analyst"))
    assert response.status_code in (400, 403, 409)
    assert ok(client.get(f"/api/assessments/{aid}", headers=auth("analyst")))["status"] == "INTAKE"


def test_business_user_cannot_use_pipeline_status_endpoint(client, auth, create_assessment):
    aid = create_assessment()["id"]
    assert client.patch(f"/api/assessments/{aid}/status?status=INTAKE", headers=auth("owner")).status_code == 403


def test_stage_pipeline_happy_path_to_inherent_risk(client, auth, analysed_assessment):
    """INTAKE -> EVIDENCE_COLLECTION -> RISK_IDENTIFICATION -> (rate) -> INHERENT_RISK_ASSESSMENT."""

    assessment = analysed_assessment()
    aid = assessment["id"]
    assert assessment["status"] == "RISK_IDENTIFICATION"

    # Blocked: nothing is rated yet, so the inherent result would be half-known
    # -- and the message says so, rather than blaming risk identification.
    blocked = client.patch(f"/api/assessments/{aid}/advance-stage", json={}, headers=auth("analyst"))
    assert blocked.status_code == 400
    assert "must be rated" in blocked.json()["detail"]
    assert "no usable" not in blocked.json()["detail"]

    for factor in factors(client, auth, aid):
        if factor["applicable"] and not factor["excluded"]:
            ok(rate(client, auth, aid, factor["id"], 3, 4))

    advanced = ok(client.patch(f"/api/assessments/{aid}/advance-stage", json={}, headers=auth("analyst")))
    assert advanced["status"] == "INHERENT_RISK_ASSESSMENT"
    assert advanced["inherent_score"] == compute_factor_score(3, 4)
    assert advanced["inherent_risk_level"] == "MEDIUM"


def test_intake_cannot_complete_without_description_and_evidence(client, auth):
    draft = ok(client.post("/api/assessments", json={"title": "Bare", "is_draft": True}, headers=auth("owner")), 201)
    response = client.patch(f"/api/assessments/{draft['id']}/advance-stage", json={}, headers=auth("owner"))
    # The workflow guard refuses a draft (409) before the intake check (400).
    assert response.status_code in (400, 409)
    assert ok(client.get(f"/api/assessments/{draft['id']}", headers=auth("owner")))["status"] == "INTAKE"


def test_evidence_stage_requires_confirmed_profile(client, auth, create_assessment):
    aid = create_assessment()["id"]
    ok(client.patch(f"/api/assessments/{aid}/advance-stage", json={}, headers=auth("owner")))
    response = client.patch(f"/api/assessments/{aid}/advance-stage", json={}, headers=auth("analyst"))
    assert response.status_code == 400
    detail = response.json()["detail"]
    assert "has not been confirmed" in detail
    assert "/intelligence" not in detail, "no raw API paths in user-facing messages"


# -- analysis and the resulting score ----------------------------------------------------------


def test_business_user_cannot_run_analysis(client, auth, create_assessment):
    aid = create_assessment()["id"]
    assert client.post(f"/api/assessments/{aid}/analyze", headers=auth("owner")).status_code == 403


def test_analyze_refuses_an_unconfirmed_profile(client, auth, create_assessment):
    aid = create_assessment()["id"]
    response = client.post(f"/api/assessments/{aid}/analyze", headers=auth("analyst"))
    assert response.status_code == 400
    assert "has not been confirmed" in response.json()["detail"]
    assert factors(client, auth, aid) == []


def test_only_the_owner_confirms_the_profile(client, auth, create_assessment):
    aid = create_assessment()["id"]
    ok(client.patch(f"/api/assessments/{aid}/advance-stage", json={}, headers=auth("owner")))
    client.patch(f"/api/assessments/{aid}/advance-stage", json={}, headers=auth("analyst"))
    response = client.post(f"/api/assessments/{aid}/intelligence/confirm", json={}, headers=auth("analyst"))
    assert response.status_code == 403
    confirmed = ok(client.post(f"/api/assessments/{aid}/intelligence/confirm", json={}, headers=auth("owner")))
    assert confirmed["confirmed"] and confirmed["confirmed_by"] == "Owner"


def test_analyze_identifies_all_categories_unrated(client, auth, create_assessment):
    aid = confirmed_assessment(client, auth, create_assessment)
    analysed = ok(client.post(f"/api/assessments/{aid}/analyze", headers=auth("analyst")))
    assert analysed["status"] == "RISK_IDENTIFICATION"
    assert analysed["assessment_mode"] == "ai_assisted"

    found = factors(client, auth, aid)
    assert len(found) == 10
    assert all(f["likelihood"] is None and f["score"] == 0 for f in found)
    applicable = [f for f in found if f["applicable"]]
    assert applicable and all(f["rationale"] for f in applicable)
    # Cross-border settlement in the intake -> geographic risk with a
    # verified quote backing the indicator.
    geographic = next(f for f in found if f["category"] == "GEOGRAPHIC_RISK")
    assert geographic["applicable"] and "CROSS_BORDER_CAPABILITY" in geographic["indicators"]
    assert geographic["evidence_status"] == "EVIDENCE_FOUND"


def test_rating_produces_the_deterministic_overall_score(client, auth, create_assessment):
    aid = confirmed_assessment(client, auth, create_assessment)
    ok(client.post(f"/api/assessments/{aid}/analyze", headers=auth("analyst")))
    inherent = [f for f in factors(client, auth, aid) if f["applicable"] and f["category"] != "CONTROL_ENVIRONMENT_RISK"]
    ratings = [(5, 5), (2, 2)] + [(3, 3)] * (len(inherent) - 2)
    for factor, (likelihood, impact) in zip(inherent, ratings):
        rated = ok(rate(client, auth, aid, factor["id"], likelihood, impact))
        assert rated["score"] == compute_factor_score(likelihood, impact)

    assessment = ok(client.get(f"/api/assessments/{aid}", headers=auth("analyst")))
    expected = round(sum(compute_factor_score(*r) for r in ratings) / len(ratings), 2)
    assert assessment["overall_score"] == pytest.approx(expected)
    assert assessment["risk_level"] in {"LOW", "MEDIUM", "HIGH", "CRITICAL"}


@pytest.mark.parametrize(("likelihood", "impact"), [(0, 3), (3, -1)])
def test_invalid_rating_is_rejected(client, auth, create_assessment, likelihood, impact):
    aid = confirmed_assessment(client, auth, create_assessment)
    ok(client.post(f"/api/assessments/{aid}/analyze", headers=auth("analyst")))
    factor = next(f for f in factors(client, auth, aid) if f["applicable"])
    assert rate(client, auth, aid, factor["id"], likelihood, impact).status_code == 422


def test_ai_rating_suggestions_prefill_but_never_score(client, auth, create_assessment):
    aid = confirmed_assessment(client, auth, create_assessment)
    ok(client.post(f"/api/assessments/{aid}/analyze", headers=auth("analyst")))
    result = ok(
        client.post(f"/api/assessments/{aid}/risk-factors/suggest-ratings", json={}, headers=auth("analyst"))
    )
    assert result["suggested_count"] > 0 and result["degraded"] is False
    suggested = [f for f in result["factors"] if f["ai_suggested_likelihood"] is not None]
    assert suggested and all(f["likelihood"] is None for f in suggested)
    assessment = ok(client.get(f"/api/assessments/{aid}", headers=auth("analyst")))
    assert assessment["overall_score"] is None


def test_async_analysis_returns_a_job_that_completes(client, auth, create_assessment):
    aid = confirmed_assessment(client, auth, create_assessment)
    job = ok(client.post(f"/api/assessments/{aid}/analyze-async", headers=auth("analyst")), 202)
    final = ok(client.get(f"/api/processing-jobs/{job['id']}", headers=auth("analyst")))
    assert final["status"] == "SUCCEEDED", final


# -- LLM and workflow failures through the API -------------------------------------------------------


def test_ai_outage_yields_labelled_provisional_result_that_gates_progress(client, auth, create_assessment, fake_llm):
    aid = confirmed_assessment(client, auth, create_assessment)
    fake_llm.transport = FakeLLM.timeout
    analysed = ok(client.post(f"/api/assessments/{aid}/analyze", headers=auth("analyst")))
    assert analysed["assessment_mode"] == "rules_only"
    assert analysed["ai_status"] == "timeout"
    assert analysed["requires_human_review"] is True
    assert analysed["degraded_reason"] and "openrouter" not in analysed["degraded_reason"].lower()

    blocked = client.patch(f"/api/assessments/{aid}/advance-stage", json={}, headers=auth("analyst"))
    assert blocked.status_code == 400
    assert "acknowledge" in blocked.json()["detail"]

    acknowledged = ok(client.post(f"/api/assessments/{aid}/acknowledge-degraded", json={}, headers=auth("analyst")))
    assert acknowledged["degraded_acknowledged_at"] is not None


def test_ai_outage_with_fallback_disabled_records_no_rating(client, auth, create_assessment, fake_llm, monkeypatch):
    monkeypatch.setenv("ENABLE_RULES_ONLY_FALLBACK", "false")
    fake_llm.transport = FakeLLM.rate_limited
    aid = confirmed_assessment(client, auth, create_assessment)
    analysed = ok(client.post(f"/api/assessments/{aid}/analyze", headers=auth("analyst")))
    assert analysed["assessment_mode"] == "unavailable"
    assert analysed["overall_score"] is None and analysed["risk_level"] is None
    assert factors(client, auth, aid) == []
    assert client.patch(f"/api/assessments/{aid}/advance-stage", json={}, headers=auth("analyst")).status_code == 400


def test_workflow_crash_is_a_500_and_leaves_the_assessment_untouched(client, auth, create_assessment, monkeypatch):
    def crash(assessment_id, db):
        raise RuntimeError("graph compilation failed")

    monkeypatch.setattr(assessments_api, "run_risk_assessment_workflow", crash)
    aid = confirmed_assessment(client, auth, create_assessment)
    response = client.post(f"/api/assessments/{aid}/analyze", headers=auth("analyst"))
    assert response.status_code == 500
    assert "Risk assessment workflow failed" in response.json()["detail"]
    after = ok(client.get(f"/api/assessments/{aid}", headers=auth("analyst")))
    assert after["status"] == "EVIDENCE_COLLECTION"
    assert factors(client, auth, aid) == []


def test_analyzing_a_draft_without_evidence_is_a_400(client, auth):
    draft = ok(client.post("/api/assessments", json={"title": "Bare", "is_draft": True}, headers=auth("owner")), 201)
    assert client.post(f"/api/assessments/{draft['id']}/analyze", headers=auth("analyst")).status_code == 400


def test_analyzing_an_unknown_assessment_is_a_404(client, auth):
    assert client.post("/api/assessments/424242/analyze", headers=auth("analyst")).status_code == 404

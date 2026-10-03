"""
The LangGraph risk-assessment workflow (app/langgraph/): each node on
its own, then the compiled graph end to end, including every degraded
path. Real database (throwaway SQLite), real rule engine, fake LLM.
"""

import pytest

import app.langgraph.nodes as nodes
from app.database import SessionLocal
from app.langgraph.graph import build_risk_assessment_graph
from app.langgraph.service import run_risk_assessment_workflow
from app.models.ai_metrics import AIEvaluationRecord, AIUsageLog
from app.models.assessment import Assessment
from app.models.audit_event import AuditEvent
from app.models.risk_factor import RiskFactor
from app.risk_engine.degraded import AiStatus, AssessmentMode, ScoreSource
from app.schemas.risk_factor import RISK_CATEGORIES
from tests.support.fake_llm import FakeLLM


@pytest.fixture
def db():
    session = SessionLocal()
    yield session
    session.close()


@pytest.fixture
def assessment_id(create_assessment):
    return create_assessment()["id"]


def loaded_state(db, assessment_id):
    state = {"assessment_id": assessment_id}
    state.update(nodes.load_assessment(state, db))
    state.update(nodes.gather_intelligence(state, db))
    return state


def current_factors(db, assessment_id):
    db.expire_all()
    return (
        db.query(RiskFactor)
        .filter(RiskFactor.assessment_id == assessment_id, RiskFactor.is_current.is_(True))
        .all()
    )


def audit_actions(db, assessment_id):
    return [e.action for e in db.query(AuditEvent).filter(AuditEvent.assessment_id == assessment_id)]


# -- individual nodes -----------------------------------------------------------


def test_graph_runs_the_five_nodes_in_order(db):
    graph = build_risk_assessment_graph(db).get_graph()
    edges = {(edge.source, edge.target) for edge in graph.edges}
    assert edges == {
        ("__start__", "load_assessment"),
        ("load_assessment", "gather_intelligence"),
        ("gather_intelligence", "identify_risks"),
        ("identify_risks", "calculate_scores"),
        ("calculate_scores", "persist_results"),
        ("persist_results", "__end__"),
    }


def test_load_assessment_passes_every_citable_intake_field(db, assessment_id):
    update = nodes.load_assessment({"assessment_id": assessment_id}, db)
    assert update["status"] == "LOADED"
    assert update["previous_status"] == "INTAKE"
    assert update["assessment"]["description"].startswith("Card acquiring")
    assert "countries_jurisdictions" in update["assessment"]


def test_load_assessment_rejects_unknown_id(db):
    with pytest.raises(ValueError, match="not found"):
        nodes.load_assessment({"assessment_id": 999_999}, db)


def test_gather_intelligence_tolerates_a_missing_profile(db, assessment_id):
    update = nodes.gather_intelligence({"assessment_id": assessment_id}, db)
    assert update == {"intelligence": None, "status": "INTELLIGENCE_GATHERED"}


def test_identify_risks_ai_assisted(db, assessment_id, fake_llm):
    update = nodes.identify_risks(loaded_state(db, assessment_id), db)
    assert update["assessment_mode"] == AssessmentMode.AI_ASSISTED
    assert update["ai_status"] == AiStatus.SUCCESS
    assert [f["category"] for f in update["risk_factors"]] == RISK_CATEGORIES
    assert update["requires_human_review"] is False
    assert len(fake_llm.factor_calls()) == 1


@pytest.mark.parametrize(
    ("transport", "ai_status", "code"),
    [
        (FakeLLM.timeout, AiStatus.TIMEOUT, "AI_TIMEOUT"),
        (FakeLLM.rate_limited, AiStatus.RATE_LIMITED, "AI_RATE_LIMITED"),
        (FakeLLM.malformed, AiStatus.INVALID_RESPONSE, "AI_INVALID_RESPONSE"),
        (FakeLLM.server_error, AiStatus.FAILED, "AI_PROVIDER_ERROR"),
    ],
    ids=["timeout", "rate-limited", "malformed", "5xx"],
)
def test_identify_risks_falls_back_to_rules_on_ai_failure(db, assessment_id, fake_llm, transport, ai_status, code):
    fake_llm.transport = transport
    update = nodes.identify_risks(loaded_state(db, assessment_id), db)
    assert update["assessment_mode"] == AssessmentMode.RULES_ONLY
    assert update["ai_status"] == ai_status
    assert update["technical_error_code"] == code
    assert update["score_source"] == ScoreSource.DETERMINISTIC_RULES
    assert update["is_provisional"] and update["requires_human_review"]
    assert update["risk_factors"], "the rule engine should have produced factors"
    assert update["unevaluated_categories"]
    db.commit()
    assert "AI_ANALYSIS_DEGRADED" in audit_actions(db, assessment_id)


def test_identify_risks_unavailable_when_fallback_disabled(db, assessment_id, fake_llm, monkeypatch):
    monkeypatch.setenv("ENABLE_RULES_ONLY_FALLBACK", "false")
    fake_llm.transport = FakeLLM.timeout
    update = nodes.identify_risks(loaded_state(db, assessment_id), db)
    assert update["assessment_mode"] == AssessmentMode.UNAVAILABLE
    assert update["risk_factors"] == []
    assert update["unevaluated_categories"] == RISK_CATEGORIES


def test_identify_risks_unavailable_when_rule_engine_also_fails(db, assessment_id, fake_llm, monkeypatch):
    class Broken:
        def assess(self, **_):
            raise RuntimeError("rule engine exploded")

    monkeypatch.setattr(nodes, "RiskEngine", Broken)
    fake_llm.transport = FakeLLM.timeout
    update = nodes.identify_risks(loaded_state(db, assessment_id), db)
    assert update["assessment_mode"] == AssessmentMode.UNAVAILABLE
    assert update["technical_error_code"] == "RULE_ENGINE_FAILURE"


def test_non_ai_failure_never_uses_the_fallback(db, assessment_id, monkeypatch):
    def corrupt(**_):
        raise KeyError("assessment data is corrupt")

    monkeypatch.setattr(nodes, "identify_risk_factors", corrupt)
    update = nodes.identify_risks(loaded_state(db, assessment_id), db)
    assert update["assessment_mode"] == AssessmentMode.UNAVAILABLE
    assert update["technical_error_code"] == "UNEXPECTED_ANALYSIS_FAILURE"


def test_calculate_scores_refuses_to_invent_a_score_when_unavailable(db):
    update = nodes.calculate_scores({"assessment_mode": AssessmentMode.UNAVAILABLE}, db)
    assert update == {"overall_score": None, "risk_level": None, "status": "SCORES_UNAVAILABLE"}


def test_calculate_scores_unrated_factors_have_no_score_but_rules_still_fire(db):
    factors = [
        {"category": "GEOGRAPHIC_RISK", "applicable": True, "score": 0.0, "indicators": ["SANCTIONS_EXPOSURE"]},
        {"category": "PRODUCT_SERVICE_RISK", "applicable": True, "score": 0.0, "indicators": []},
    ]
    update = nodes.calculate_scores({"risk_factors": factors}, db)
    assert update["overall_score"] is None
    assert update["risk_level"] == "CRITICAL"


# -- the compiled workflow end to end ----------------------------------------------


def test_full_run_persists_versioned_ai_factors_and_metering(db, assessment_id):
    result = run_risk_assessment_workflow(assessment_id, db)
    assert result["status"] == "COMPLETED"

    factors = current_factors(db, assessment_id)
    assert sorted(f.category for f in factors) == sorted(RISK_CATEGORIES)
    assert {f.source for f in factors} == {"AI"}
    assert {f.version for f in factors} == {1}

    assessment = db.get(Assessment, assessment_id)
    assert assessment.assessment_mode == AssessmentMode.AI_ASSISTED
    # Workflow never moves the pipeline stage itself.
    assert assessment.status == "INTAKE"
    assert "ANALYSIS" in audit_actions(db, assessment_id)

    usage = db.query(AIUsageLog).filter(AIUsageLog.assessment_id == assessment_id).all()
    assert usage and usage[0].total_tokens == 200 and usage[0].success
    assert db.query(AIEvaluationRecord).filter(AIEvaluationRecord.assessment_id == assessment_id).count() == 1


def test_rerun_supersedes_ai_factors_but_keeps_manual_ones(db, assessment_id, client, auth):
    run_risk_assessment_workflow(assessment_id, db)
    response = client.post(
        f"/api/assessments/{assessment_id}/risk-factors",
        json={"category": "OWNERSHIP_ENTITY_COMPLEXITY_RISK", "rationale": "Analyst concern about UBOs."},
        headers=auth("analyst"),
    )
    assert response.status_code in (200, 201), response.text

    run_risk_assessment_workflow(assessment_id, db)
    factors = current_factors(db, assessment_id)
    ai = [f for f in factors if f.source == "AI"]
    assert {f.version for f in ai} == {2}
    assert len(ai) == 10
    assert [f for f in factors if f.source == "MANUAL"], "manual factor must carry forward"


def test_failed_rerun_does_not_destroy_the_last_good_analysis(db, assessment_id, fake_llm, monkeypatch):
    run_risk_assessment_workflow(assessment_id, db)
    monkeypatch.setenv("ENABLE_RULES_ONLY_FALLBACK", "false")
    fake_llm.transport = FakeLLM.timeout

    result = run_risk_assessment_workflow(assessment_id, db)
    assert result["status"] == "UNAVAILABLE"
    assert len(current_factors(db, assessment_id)) == 10
    db.expire_all()
    assessment = db.get(Assessment, assessment_id)
    assert assessment.assessment_mode == AssessmentMode.UNAVAILABLE
    assert assessment.technical_error_code == "AI_TIMEOUT"


def test_rules_only_run_is_persisted_as_rules_and_provisional(db, assessment_id, fake_llm):
    fake_llm.transport = FakeLLM.timeout
    run_risk_assessment_workflow(assessment_id, db)
    factors = current_factors(db, assessment_id)
    assert factors and {f.source for f in factors} == {"RULES"}
    assessment = db.get(Assessment, assessment_id)
    assert assessment.analysis_is_provisional is True
    assert assessment.requires_human_review is True
    assert set(assessment.get_unevaluated_categories()) & set(RISK_CATEGORIES)

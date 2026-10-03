"""
Optional Langfuse tracing (app/observability/tracing.py).

A recording stand-in replaces the Langfuse client, so these tests check
what *would* be sent -- without a Langfuse server -- and prove the two
properties that matter most: tracing never changes the app's behaviour,
and it never carries secrets or (by default) assessment content.
"""

import json
from contextlib import contextmanager

import pytest

from app.database import SessionLocal
from app.langgraph.service import run_risk_assessment_workflow
from app.observability import tracing
from tests.support.fake_llm import FakeLLM


class RecordedObservation:
    def __init__(self, name, as_type, attributes):
        self.name = name
        self.as_type = as_type
        self.fields = dict(attributes)
        self.trace_id = "trace-0001"

    def update(self, **fields):
        self.fields.update(fields)


class RecordingClient:
    def __init__(self, fail_on_start=False):
        self.observations: list[RecordedObservation] = []
        self.fail_on_start = fail_on_start
        self.flushed = False

    @contextmanager
    def start_as_current_observation(self, *, name, as_type="span", **attributes):
        if self.fail_on_start:
            raise RuntimeError("langfuse is down")
        observation = RecordedObservation(name, as_type, attributes)
        self.observations.append(observation)
        yield observation

    def flush(self):
        self.flushed = True

    def get_trace_url(self, trace_id):
        return f"https://langfuse.example/trace/{trace_id}"

    def named(self, name):
        return [o for o in self.observations if o.name == name]


@pytest.fixture
def recorder(monkeypatch):
    client = RecordingClient()
    monkeypatch.setattr(tracing, "_get_client", lambda: client)
    return client


@pytest.fixture
def db():
    session = SessionLocal()
    yield session
    session.close()


def test_disabled_without_keys(monkeypatch):
    tracing.reset_for_tests()
    monkeypatch.setenv("LANGFUSE_ENABLED", "true")
    monkeypatch.delenv("LANGFUSE_PUBLIC_KEY", raising=False)
    monkeypatch.delenv("LANGFUSE_SECRET_KEY", raising=False)
    assert tracing.is_configured() is False
    with tracing.observe("anything") as span:
        span.update(output={"x": 1})
        assert span.active is False and span.trace_id is None
    tracing.flush()


def test_enabled_flag_can_force_it_off(monkeypatch):
    monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "pk-lf-test")
    monkeypatch.setenv("LANGFUSE_SECRET_KEY", "sk-lf-test")
    monkeypatch.setenv("LANGFUSE_ENABLED", "false")
    assert tracing.is_configured() is False


def test_exceptions_propagate_unchanged_and_are_recorded(recorder):
    with pytest.raises(KeyError):
        with tracing.observe("boom"):
            raise KeyError("missing")
    assert recorder.named("boom")[0].fields["level"] == "ERROR"


def test_ai_errors_are_reduced_to_their_code():
    from app.ai.errors import AIRateLimitError

    message = tracing.safe_error(AIRateLimitError("429: {'prompt echoed': 'customer X'}"))
    assert message == "AIRateLimitError (AI_RATE_LIMITED)"


def test_workflow_trace_has_workflow_node_and_generation_spans(recorder, db, create_assessment):
    aid = create_assessment()["id"]
    result = run_risk_assessment_workflow(aid, db)

    assert result["trace_id"] == "trace-0001"
    workflow = recorder.named("risk_assessment_workflow")[0]
    assert workflow.as_type == "chain"
    assert workflow.fields["metadata"]["assessment_id"] == aid
    assert workflow.fields["output"]["assessment_mode"] == "ai_assisted"
    assert workflow.fields["output"]["processing_ms"] >= 0

    for node in ("load_assessment", "gather_intelligence", "identify_risks", "calculate_scores", "persist_results"):
        assert recorder.named(node), f"missing span for node {node}"
    identify = recorder.named("identify_risks")[0]
    assert identify.fields["output"]["risk_factor_count"] == 10

    generation = recorder.named("risk_factor_identification")[0]
    assert generation.as_type == "generation"
    assert generation.fields["model"] == "fake-vendor/fake-risk-model"
    assert generation.fields["usage_details"] == {"input": 120, "output": 80, "total": 200}
    assert generation.fields["metadata"]["assessment_id"] == aid


def test_no_secrets_or_content_are_sent_by_default(recorder, db, create_assessment, monkeypatch):
    monkeypatch.delenv("LANGFUSE_CAPTURE_CONTENT", raising=False)
    aid = create_assessment()["id"]
    run_risk_assessment_workflow(aid, db)

    sent = json.dumps([o.fields for o in recorder.observations], default=str)
    assert "test-key-not-real" not in sent  # the provider API key
    assert "Bearer" not in sent
    assert "Card acquiring for German merchants" not in sent  # assessment text
    generation = recorder.named("risk_factor_identification")[0]
    assert set(generation.fields["input"]) == {"message_count", "prompt_chars"}


def test_content_capture_is_opt_in_and_masked(recorder, db, create_assessment, monkeypatch):
    monkeypatch.setenv("LANGFUSE_CAPTURE_CONTENT", "true")
    monkeypatch.setenv("AI_MASK_SENSITIVE_DATA", "true")
    aid = create_assessment(evidence="Settlement card 4111 1111 1111 1111 for cross-border payouts.")["id"]
    run_risk_assessment_workflow(aid, db)
    generation = recorder.named("risk_factor_identification")[0]
    sent = json.dumps(generation.fields["input"])
    assert "Card acquiring" in sent
    assert "4111 1111 1111 1111" not in sent


def test_failed_llm_call_is_marked_as_error(recorder, db, create_assessment, fake_llm):
    fake_llm.transport = FakeLLM.rate_limited
    aid = create_assessment()["id"]
    run_risk_assessment_workflow(aid, db)
    generation = recorder.named("risk_factor_identification")[0]
    assert generation.fields["level"] == "ERROR"
    assert generation.fields["status_message"] == "HTTP 429"
    assert recorder.named("risk_assessment_workflow")[0].fields["output"]["assessment_mode"] == "rules_only"


def test_a_broken_langfuse_never_breaks_the_workflow(monkeypatch, db, create_assessment):
    monkeypatch.setattr(tracing, "_get_client", lambda: RecordingClient(fail_on_start=True))
    aid = create_assessment()["id"]
    result = run_risk_assessment_workflow(aid, db)
    assert result["status"] == "COMPLETED"
    assert "trace_id" not in result


def test_flush_and_trace_url(recorder):
    tracing.flush()
    assert recorder.flushed
    assert tracing.trace_url("abc") == "https://langfuse.example/trace/abc"

"""
Stage moves as background jobs (POST /api/assessments/{id}/advance-stage-
async): the same move as PATCH /advance-stage, with per-step progress.
Jobs run inline under pytest (PROCESSING_JOBS_INLINE).
"""

from types import SimpleNamespace

import pytest

import app.ai.control_identifier as control_identifier
import app.langgraph.graph as graph_module
from app.database import SessionLocal
from app.langgraph import progress
from app.models.processing_job import ProcessingJob
from app.schemas.processing_job import build_processing_job_response
from app.services import processing_jobs
from tests.conftest import ok


def plan(from_status):
    return [key for key, _ in progress.STAGE_STEPS[from_status]]


@pytest.fixture
def evidence_ready(client, auth, create_assessment):
    """An assessment in EVIDENCE_COLLECTION; confirm=True also confirms
    its business profile."""

    def make(confirm: bool = True) -> int:
        aid = create_assessment()["id"]
        ok(client.patch(f"/api/assessments/{aid}/advance-stage", json={}, headers=auth("owner")))
        if confirm:
            # The first attempt creates the profile and refuses until confirmed.
            client.patch(f"/api/assessments/{aid}/advance-stage", json={}, headers=auth("owner"))
            ok(
                client.post(
                    f"/api/assessments/{aid}/intelligence/confirm",
                    json={"confirmed_by": "Owner"},
                    headers=auth("owner"),
                )
            )
        return aid

    return make


def start(client, auth, aid, who="owner"):
    return client.post(f"/api/assessments/{aid}/advance-stage-async", headers=auth(who))


def run(client, auth, aid, who="owner"):
    """Start a move and return the finished job."""
    job = ok(start(client, auth, aid, who), 202)
    return ok(client.get(f"/api/processing-jobs/{job['id']}", headers=auth("owner")))


def steps(job):
    return {step["key"]: step["status"] for step in job["stages"]}


def status_of(client, auth, aid):
    return ok(client.get(f"/api/assessments/{aid}", headers=auth("owner")))["status"]


def start_failing(client, auth, aid, monkeypatch, node="identify_risks"):
    """Start a move in which graph node `node` raises. The patch is scoped
    so the suite's autouse fake LLM (same monkeypatch object) stays."""

    def boom(state, db):
        raise RuntimeError(f"{node} exploded")

    with monkeypatch.context() as patch:
        patch.setattr(graph_module, node, boom)
        return ok(start(client, auth, aid), 202)


def rate_everything(client, auth, aid):
    for factor in ok(client.get(f"/api/assessments/{aid}/risk-factors", headers=auth("analyst"))):
        if factor["applicable"] and not factor["excluded"]:
            ok(
                client.patch(
                    f"/api/assessments/{aid}/risk-factors/{factor['id']}/rating",
                    json={"likelihood": 3, "impact": 4, "rated_by": "Analyst", "reason": "The analyst rates this differently from the model."},
                    headers=auth("analyst"),
                )
            )


# -- every stage ---------------------------------------------------------------------


def test_intake_move_reports_its_steps(client, auth, create_assessment):
    aid = create_assessment()["id"]
    job = run(client, auth, aid)

    assert job["job_type"] == "STAGE_ADVANCE"
    assert job["status"] == "SUCCEEDED"
    assert job["from_status"] == "INTAKE"
    assert steps(job) == {"validate_intake": "completed", "record_transition": "completed"}
    assert status_of(client, auth, aid) == "EVIDENCE_COLLECTION"


def test_risk_identification_runs_every_node(client, auth, evidence_ready):
    aid = evidence_ready()
    job = run(client, auth, aid)

    assert job["status"] == "SUCCEEDED"
    assert [step["key"] for step in job["stages"]] == plan("EVIDENCE_COLLECTION")
    for step in job["stages"]:
        assert step["status"] == "completed"
        assert step["started_at"] and step["finished_at"]
    assert status_of(client, auth, aid) == "RISK_IDENTIFICATION"


def test_a_stage_gate_is_reported_as_a_refusal(client, auth, evidence_ready):
    aid = evidence_ready()
    run(client, auth, aid)

    # Risk Identification -> Inherent: nothing rated yet.
    job = run(client, auth, aid, who="analyst")
    assert job["status"] == "FAILED"
    assert job["refused"] is True
    assert job["is_retryable"] is False
    assert "must be rated" in job["error_message"]
    assert steps(job)["calculate_inherent"] == "failed"
    assert steps(job)["record_transition"] == "queued"
    assert status_of(client, auth, aid) == "RISK_IDENTIFICATION"

    # Rated: the same move goes through.
    rate_everything(client, auth, aid)
    job = run(client, auth, aid, who="analyst")
    assert job["status"] == "SUCCEEDED"
    assert steps(job) == dict.fromkeys(plan("RISK_IDENTIFICATION"), "completed")
    assert status_of(client, auth, aid) == "INHERENT_RISK_ASSESSMENT"


def test_best_effort_ai_failure_is_a_warning_not_a_failure(client, auth, evidence_ready, monkeypatch):
    aid = evidence_ready()
    run(client, auth, aid)
    rate_everything(client, auth, aid)
    run(client, auth, aid, who="analyst")
    assert status_of(client, auth, aid) == "INHERENT_RISK_ASSESSMENT"

    def unavailable(**kwargs):
        raise RuntimeError("control AI unavailable")

    with monkeypatch.context() as patch:
        patch.setattr(control_identifier, "identify_applicable_controls", unavailable)
        job = run(client, auth, aid, who="analyst")

    assert job["status"] == "SUCCEEDED"
    assert steps(job) == {
        "identify_controls": "warning",
        "evaluate_controls": "skipped",
        "record_transition": "completed",
    }
    assert status_of(client, auth, aid) == "CONTROL_ASSESSMENT"


# -- refusals before a job starts ------------------------------------------------------


def test_unconfirmed_profile_is_refused_up_front(client, auth, evidence_ready):
    aid = evidence_ready(confirm=False)
    response = start(client, auth, aid)
    assert response.status_code == 400
    assert "not been confirmed" in response.json()["detail"]
    assert ok(client.get(f"/api/processing-jobs?assessment_id={aid}", headers=auth("owner"))) == []


def test_wrong_role_is_refused_up_front(client, auth, evidence_ready):
    aid = evidence_ready()
    assert start(client, auth, aid, who="committee").status_code == 403
    assert status_of(client, auth, aid) == "EVIDENCE_COLLECTION"


# -- failure and retry -------------------------------------------------------------------


def test_failed_step_is_recorded_and_nothing_advances(client, auth, evidence_ready, monkeypatch):
    aid = evidence_ready()
    job = start_failing(client, auth, aid, monkeypatch, node="calculate_scores")
    job = ok(client.get(f"/api/processing-jobs/{job['id']}", headers=auth("owner")))

    assert job["status"] == "FAILED"
    assert job["refused"] is False
    assert job["is_retryable"]
    assert steps(job) == {
        "confirm_profile": "completed",
        "load_assessment": "completed",
        "gather_intelligence": "completed",
        "identify_risks": "completed",
        "calculate_scores": "failed",
        "persist_results": "queued",
        "record_transition": "queued",
    }
    assert status_of(client, auth, aid) == "EVIDENCE_COLLECTION"

    # Fixed: a retry by someone allowed to run it completes the move.
    retried = ok(client.post(f"/api/processing-jobs/{job['id']}/retry", headers=auth("analyst")), 202)
    retried = ok(client.get(f"/api/processing-jobs/{retried['id']}", headers=auth("owner")))
    assert retried["status"] == "SUCCEEDED"
    assert retried["requested_by"] == "Analyst"
    assert status_of(client, auth, aid) == "RISK_IDENTIFICATION"


def test_retry_cannot_advance_an_assessment_that_moved_on(client, auth, evidence_ready, monkeypatch):
    aid = evidence_ready()
    job = start_failing(client, auth, aid, monkeypatch)

    # Meanwhile the move is made the synchronous way.
    ok(client.patch(f"/api/assessments/{aid}/advance-stage", json={}, headers=auth("analyst")))
    assert status_of(client, auth, aid) == "RISK_IDENTIFICATION"

    # The retry endpoint refuses (no longer a valid transition)...
    assert client.post(f"/api/processing-jobs/{job['id']}/retry", headers=auth("analyst")).status_code == 409

    # ...and a job that reaches a worker anyway (e.g. queued before the
    # stage moved) fails without advancing a second stage.
    db = SessionLocal()
    try:
        row = db.query(ProcessingJob).filter(ProcessingJob.id == job["id"]).first()
        processing_jobs.retry_job(db, row)
        db.commit()
    finally:
        db.close()
    processing_jobs.enqueue(job["id"])

    job = ok(client.get(f"/api/processing-jobs/{job['id']}", headers=auth("owner")))
    assert job["status"] == "FAILED"
    assert "already moved on" in job["error_message"]
    assert status_of(client, auth, aid) == "RISK_IDENTIFICATION"


def test_retry_needs_permission_to_run_it(client, auth, evidence_ready, monkeypatch):
    aid = evidence_ready()
    job = start_failing(client, auth, aid, monkeypatch)
    assert client.post(f"/api/processing-jobs/{job['id']}/retry", headers=auth("committee")).status_code == 403


# -- live progress ---------------------------------------------------------------------------


def test_live_progress_is_visible_while_the_move_is_in_flight(client, auth, evidence_ready, monkeypatch):
    seen: list[dict] = []
    real_identify = graph_module.identify_risks

    def watching_identify(state, db):
        seen.extend(progress._listener.get().snapshot())
        return real_identify(state, db)

    aid = evidence_ready()
    with monkeypatch.context() as patch:
        patch.setattr(graph_module, "identify_risks", watching_identify)
        ok(start(client, auth, aid), 202)

    mid_run = steps({"stages": seen})
    assert mid_run["confirm_profile"] == "completed"
    assert mid_run["gather_intelligence"] == "completed"
    assert mid_run["identify_risks"] == "running"
    assert mid_run["calculate_scores"] == "queued"


def test_response_reads_the_live_log_while_running():
    job = SimpleNamespace(
        id=987654, job_type="STAGE_ADVANCE", status="RUNNING", assessment_id=None, document_id=None,
        progress=10, stage_message="Starting the stage move", error_message=None, error_detail=None,
        is_incomplete=False, get_incomplete_reasons=lambda: [], attempts=1, max_attempts=5,
        requested_by=None, created_at="2026-10-01T09:00:00Z", started_at=None, finished_at=None, stage_log=None,
    )
    with progress.track(job.id, "CONTROL_ASSESSMENT") as log:
        log.begin("check_challenge")
        log.begin("calculate_residual")
        response = build_processing_job_response(job)

    assert response.from_status == "CONTROL_ASSESSMENT"
    assert response.stage_message == "Calculating and freezing residual risk"
    assert response.progress == 10 + 85 // 3
    assert [step.status for step in response.stages] == ["completed", "running", "queued"]
    assert progress.live_log(job.id) is None


def test_logs_from_before_stage_moves_were_generalised_still_read():
    legacy = '{"load_assessment": {"status": "completed", "started_at": null, "finished_at": null}}'
    log = progress.StageLog.from_json(legacy)
    assert log.from_status == "EVIDENCE_COLLECTION"
    assert steps({"stages": log.snapshot()})["load_assessment"] == "completed"

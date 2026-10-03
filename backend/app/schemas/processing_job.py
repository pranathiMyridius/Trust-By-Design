from datetime import datetime
from typing import Optional

from pydantic import BaseModel


class ProcessingStage(BaseModel):
    """One step of a stage move (STAGE_ADVANCE jobs)."""

    key: str
    label: str
    status: str  # queued | running | completed | warning | failed
    started_at: Optional[datetime] = None
    finished_at: Optional[datetime] = None


class ProcessingJobResponse(BaseModel):
    id: int
    job_type: str
    status: str
    assessment_id: Optional[int] = None
    document_id: Optional[int] = None

    progress: int
    stage_message: Optional[str] = None

    error_message: Optional[str] = None
    error_detail: Optional[str] = None

    # Stage 19 (Reliability): partial results are flagged, never passed off
    # as complete.
    is_incomplete: bool = False
    incomplete_reasons: list[str] = []

    attempts: int
    max_attempts: int
    is_retryable: bool = False
    is_finished: bool = False

    requested_by: Optional[str] = None
    created_at: datetime
    started_at: Optional[datetime] = None
    finished_at: Optional[datetime] = None

    # STAGE_ADVANCE jobs: the stage being left, and every step of the move
    # in order; empty for other job types.
    from_status: Optional[str] = None
    stages: list[ProcessingStage] = []
    # FAILED because the API refused the move (a stage gate), not because
    # something broke: the UI reports it beside the button.
    refused: bool = False


def build_processing_job_response(job) -> ProcessingJobResponse:
    from app.langgraph import progress as stage_progress
    from app.services.processing_jobs import STAGE_ADVANCE_TYPES, is_refusal, is_retryable

    progress_value = job.progress
    stage_message = job.stage_message
    stages: list[dict] = []
    from_status = None
    if job.job_type in STAGE_ADVANCE_TYPES:
        live = stage_progress.live_log(job.id)
        log = live or stage_progress.StageLog.from_json(job.stage_log)
        stages = log.snapshot()
        from_status = log.from_status
        if live is not None:
            # The row isn't written mid-run (see progress.py), so derive
            # the live percentage and step from the in-memory log.
            done = live.done_count()
            progress_value = max(progress_value, 10 + (85 * done) // len(stages))
            stage_message = live.current_label() or stage_message

    return ProcessingJobResponse(
        stages=stages,
        from_status=from_status,
        refused=job.status == "FAILED" and is_refusal(job),
        id=job.id,
        job_type=job.job_type,
        status=job.status,
        assessment_id=job.assessment_id,
        document_id=job.document_id,
        progress=progress_value,
        stage_message=stage_message,
        error_message=job.error_message,
        error_detail=job.error_detail,
        is_incomplete=bool(job.is_incomplete),
        incomplete_reasons=job.get_incomplete_reasons(),
        attempts=job.attempts,
        max_attempts=job.max_attempts,
        is_retryable=is_retryable(job),
        is_finished=job.status in {"SUCCEEDED", "PARTIAL", "FAILED"},
        requested_by=job.requested_by,
        created_at=job.created_at,
        started_at=job.started_at,
        finished_at=job.finished_at,
    )

"""
Stage 19 (Performance / Reliability): background processing jobs.

Long-running work -- extracting text from an uploaded document, running
the risk-analysis workflow -- runs on a small worker pool instead of
inside the HTTP request, so the UI never waits on it. Each job is a
ProcessingJob row the frontend polls for progress (0-100 plus a
plain-language step description).

Reliability guarantees:
  * The input a job works on is committed before the job is queued, so a
    crash or error mid-job never loses the document or assessment.
  * A failed job is marked FAILED with a readable error_message (and the
    technical detail kept separately in error_detail) and can be retried
    up to max_attempts times.
  * A job that finishes with part of its output missing is marked
    PARTIAL with is_incomplete=True and the reasons listed, instead of
    being silently treated as complete.
  * Jobs interrupted by a server restart are surfaced as FAILED/retryable
    on the next start (recover_interrupted_jobs), never left "running".

PROCESSING_JOBS_INLINE=true runs jobs synchronously in the calling
thread (used by the acceptance tests); PROCESSING_MAX_WORKERS sets the
pool size (default 2).
"""

from __future__ import annotations

import logging
import os
import traceback
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from typing import Callable

from sqlalchemy.orm import Session

from app.database import IS_POSTGRES, SessionLocal
from app.models.processing_job import ProcessingJob, ProcessingJobStatus, ProcessingJobType
from app.services.audit_service import AuditAction, log_audit_event

logger = logging.getLogger(__name__)

MAX_ATTEMPTS = int(os.getenv("PROCESSING_MAX_ATTEMPTS", "5"))

_executor: ThreadPoolExecutor | None = None


class JobError(Exception):
    """A failure with a message that is safe and useful to show the user."""

    def __init__(self, user_message: str, detail: str | None = None):
        super().__init__(user_message)
        self.user_message = user_message
        self.detail = detail


def _inline() -> bool:
    return os.getenv("PROCESSING_JOBS_INLINE", "false").strip().lower() in {"1", "true", "yes"}


def _get_executor() -> ThreadPoolExecutor:
    global _executor
    if _executor is None:
        _executor = ThreadPoolExecutor(
            max_workers=max(1, int(os.getenv("PROCESSING_MAX_WORKERS", "2"))),
            thread_name_prefix="processing-job",
        )
    return _executor


def create_job(
    db: Session,
    job_type: ProcessingJobType,
    *,
    assessment_id: int | None = None,
    document_id: int | None = None,
    user=None,
    stage_message: str = "Waiting to start",
) -> ProcessingJob:
    """Adds a QUEUED job to the caller's session. The caller commits, then
    calls enqueue(job.id) -- queueing before the commit would let a worker
    look for a row that doesn't exist yet."""

    job = ProcessingJob(
        job_type=job_type.value,
        status=ProcessingJobStatus.QUEUED.value,
        assessment_id=assessment_id,
        document_id=document_id,
        progress=0,
        stage_message=stage_message,
        max_attempts=MAX_ATTEMPTS,
        requested_by=(getattr(user, "full_name", None) or getattr(user, "email", None)) if user else None,
        requested_by_id=getattr(user, "id", None),
    )
    db.add(job)
    db.flush()
    return job


def enqueue(job_id: int) -> None:
    if _inline():
        run_job(job_id)
    else:
        _get_executor().submit(run_job, job_id)


def is_retryable(job: ProcessingJob) -> bool:
    return (
        job.status in {ProcessingJobStatus.FAILED.value, ProcessingJobStatus.PARTIAL.value}
        and job.attempts < job.max_attempts
        # A refused stage move would only be refused again.
        and not is_refusal(job)
    )


def retry_job(db: Session, job: ProcessingJob, user=None) -> ProcessingJob:
    job.status = ProcessingJobStatus.QUEUED.value
    job.progress = 0
    job.stage_message = "Queued for retry"
    job.error_message = None
    job.error_detail = None
    job.is_incomplete = False
    job.incomplete_reasons = None
    job.finished_at = None
    if job.job_type in STAGE_ADVANCE_TYPES:
        # Same stage move, fresh step log (no stale results from the
        # failed attempt while it waits to start).
        from app.langgraph import progress as stage_progress

        job.stage_log = stage_progress.StageLog(
            stage_progress.StageLog.from_json(job.stage_log).from_status
        ).to_json()

    log_audit_event(
        db=db,
        assessment_id=job.assessment_id,
        action=AuditAction.PROCESSING_RETRIED,
        actor=(getattr(user, "full_name", None) or getattr(user, "email", None)) if user else None,
        actor_id=getattr(user, "id", None),
        details=f"{_label(job)} retried (attempt {job.attempts + 1} of {job.max_attempts}).",
    )
    return job


def _label(job: ProcessingJob) -> str:
    if job.job_type == ProcessingJobType.DOCUMENT_EXTRACTION.value:
        return f"Document processing (job {job.id}, document {job.document_id})"
    if job.job_type in (ProcessingJobType.STAGE_ADVANCE.value, ProcessingJobType.RISK_IDENTIFICATION.value):
        return f"Stage move (job {job.id})"
    return f"Risk analysis (job {job.id})"


class ProgressReporter:
    """Commits progress through the job's own session. Handlers call it
    only between steps, after committing their own work, so on SQLite it
    never contends with an open write transaction."""

    def __init__(self, db: Session, job: ProcessingJob):
        self.db = db
        self.job = job

    def __call__(self, progress: int, message: str) -> None:
        self.job.progress = max(0, min(99, progress))
        self.job.stage_message = message
        self.db.commit()


def run_job(job_id: int) -> None:
    db = SessionLocal()
    try:
        job = db.query(ProcessingJob).filter(ProcessingJob.id == job_id).first()
        if job is None or job.status != ProcessingJobStatus.QUEUED.value:
            return

        job.status = ProcessingJobStatus.RUNNING.value
        job.attempts += 1
        job.started_at = datetime.now(timezone.utc)
        job.progress = 5
        job.stage_message = "Starting"
        db.commit()

        handler = _HANDLERS.get(job.job_type)

        try:
            if handler is None:
                raise JobError(f"Unknown job type {job.job_type}.")
            reasons = handler(db, job, ProgressReporter(db, job)) or []
        except JobError as exc:
            db.rollback()
            _finish_failed(db, job_id, exc.user_message, exc.detail)
            return
        except Exception as exc:  # noqa: BLE001 -- any failure must land on the job row
            db.rollback()
            logger.exception("Processing job %s failed", job_id)
            _finish_failed(
                db,
                job_id,
                "Processing failed because of an unexpected error. Your assessment "
                "and uploaded files have been kept -- you can retry.",
                "".join(traceback.format_exception_only(type(exc), exc)).strip(),
            )
            return

        job = db.query(ProcessingJob).filter(ProcessingJob.id == job_id).first()
        job.progress = 100
        job.finished_at = datetime.now(timezone.utc)
        if reasons:
            job.status = ProcessingJobStatus.PARTIAL.value
            job.is_incomplete = True
            job.incomplete_reasons = "\n".join(reasons)
            job.stage_message = "Finished with incomplete results"
            log_audit_event(
                db=db,
                assessment_id=job.assessment_id,
                action=AuditAction.PROCESSING_INCOMPLETE,
                details=f"{_label(job)} finished incomplete: " + "; ".join(reasons),
            )
        else:
            job.status = ProcessingJobStatus.SUCCEEDED.value
            job.stage_message = "Complete"
        db.commit()
    finally:
        db.close()


def _finish_failed(db: Session, job_id: int, message: str, detail: str | None) -> None:
    job = db.query(ProcessingJob).filter(ProcessingJob.id == job_id).first()
    if job is None:
        return
    job.status = ProcessingJobStatus.FAILED.value
    job.error_message = message
    job.error_detail = detail
    job.finished_at = datetime.now(timezone.utc)
    job.stage_message = "Failed"
    log_audit_event(
        db=db,
        assessment_id=job.assessment_id,
        action=AuditAction.PROCESSING_FAILED,
        details=f"{_label(job)} failed: {message}",
    )
    db.commit()


def recover_interrupted_jobs() -> None:
    """On startup: RUNNING jobs were cut off by a restart -> FAILED and
    retryable; QUEUED jobs never started -> queue them again."""

    db = SessionLocal()
    try:
        interrupted = (
            db.query(ProcessingJob)
            .filter(ProcessingJob.status == ProcessingJobStatus.RUNNING.value)
            .all()
        )
        for job in interrupted:
            job.status = ProcessingJobStatus.FAILED.value
            job.error_message = (
                "Processing was interrupted because the server restarted. Your data "
                "has been kept -- retry to finish processing."
            )
            job.finished_at = datetime.now(timezone.utc)
            job.stage_message = "Interrupted"

        queued_ids = [
            job_id
            for (job_id,) in db.query(ProcessingJob.id).filter(
                ProcessingJob.status == ProcessingJobStatus.QUEUED.value
            )
        ]
        db.commit()
    except Exception as exc:  # noqa: BLE001 -- e.g. table not created yet
        db.rollback()
        logger.warning("Could not recover interrupted processing jobs: %s", exc)
        return
    finally:
        db.close()

    for job_id in queued_ids:
        enqueue(job_id)


# --- Handlers -------------------------------------------------------------
# Each takes (db, job, report) and returns a list of reasons the result is
# incomplete (empty when complete), or raises JobError / any exception.


def _process_document(db: Session, job: ProcessingJob, report: ProgressReporter) -> list[str]:
    from app.file_processing.extractor import extract_text
    from app.file_processing.storage import read_file
    from app.models.assessment_document import AssessmentDocument

    document = db.query(AssessmentDocument).filter(AssessmentDocument.id == job.document_id).first()
    if document is None or not document.file_path:
        raise JobError("The document record for this job could not be found.")

    report(15, "Reading the stored file")
    try:
        content = read_file(document.file_path)
    except FileNotFoundError as exc:
        raise JobError(
            "The stored copy of this file is missing, so it can't be processed. "
            "Please upload the document again.",
            str(exc),
        )
    except RuntimeError as exc:
        raise JobError("The stored file couldn't be decrypted.", str(exc))

    report(35, f"Extracting text from {document.filename}")
    try:
        text = extract_text(filename=document.filename, file_content=content) or ""
    except Exception as exc:  # noqa: BLE001 -- parser errors vary by format
        raise JobError(
            "We couldn't read the text in this file. It may be damaged, "
            "password-protected, or an unsupported variant of the format. The file "
            "itself is stored safely -- you can retry or upload a different version.",
            "".join(traceback.format_exception_only(type(exc), exc)).strip(),
        )

    document.extracted_text = text
    db.commit()
    report(70, "Saved the extracted text")

    reasons: list[str] = []
    if not text.strip():
        reasons.append(
            "No readable text was found (the file may be a scanned image). It is "
            "stored, but its content is not available to analysis."
        )

    if IS_POSTGRES and text.strip():
        report(85, "Indexing the document for similar-assessment search")
        try:
            from app.services.document_embedding_service import index_text

            index_text(db, assessment_id=document.assessment_id, text=text, document_id=document.id)
            db.commit()
        except Exception as exc:  # noqa: BLE001 -- indexing is best effort
            db.rollback()
            logger.warning("Semantic indexing failed for document %s: %s", document.id, exc)
            reasons.append(
                "Semantic search indexing failed, so this document won't appear in "
                "similar-assessment search until it is reprocessed."
            )

    return reasons


def _run_risk_analysis(db: Session, job: ProcessingJob, report: ProgressReporter) -> list[str]:
    from fastapi import HTTPException

    from app.api.assessments import run_assessment_analysis
    from app.models.user import User

    user = None
    if job.requested_by_id is not None:
        user = db.query(User).filter(User.id == job.requested_by_id).first()

    report(20, "Running risk identification and scoring")
    try:
        run_assessment_analysis(db, job.assessment_id, user)
    except HTTPException as exc:
        raise JobError(str(exc.detail))

    report(95, "Saving results")
    return []


STAGE_ADVANCE_TYPES = {
    ProcessingJobType.STAGE_ADVANCE.value,
    ProcessingJobType.RISK_IDENTIFICATION.value,
}

# Marks a stage move the API refused (a gate such as "acknowledge the
# provisional result first"), as opposed to one that broke.
REFUSED_PREFIX = "Refused (HTTP "


def is_refusal(job: ProcessingJob) -> bool:
    return (job.error_detail or "").startswith(REFUSED_PREFIX)


def _run_stage_advance(db: Session, job: ProcessingJob, report: ProgressReporter) -> list[str]:
    """One PATCH /advance-stage move, exactly as the synchronous call does
    it, with live per-step progress."""

    from fastapi import HTTPException

    from app.api.assessments import advance_assessment_stage
    from app.langgraph import progress as stage_progress
    from app.models.assessment import Assessment
    from app.models.user import User
    from app.schemas.assessment import AdvanceStageRequest

    from_status = stage_progress.StageLog.from_json(job.stage_log).from_status

    # advance_assessment_stage moves whatever stage the assessment is in,
    # so a retried (or stale) job must not touch one that has moved on.
    assessment = db.query(Assessment).filter(Assessment.id == job.assessment_id).first()
    if assessment is None or assessment.status != from_status:
        raise JobError(
            f"This stage move was started from {from_status}, but the "
            "assessment has already moved on. Nothing was changed."
        )

    user = None
    if job.requested_by_id is not None:
        user = db.query(User).filter(User.id == job.requested_by_id).first()

    report(10, "Starting the stage move")

    def keep_log(log: stage_progress.StageLog) -> None:
        # The failed move was rolled back; store the log on its own.
        db.rollback()
        failed = db.query(ProcessingJob).filter(ProcessingJob.id == job.id).first()
        failed.stage_log = log.to_json()
        db.commit()

    with stage_progress.track(job.id, from_status) as log:
        try:
            advance_assessment_stage(job.assessment_id, AdvanceStageRequest(), db, user)
        except HTTPException as exc:
            log.finish(succeeded=False)
            keep_log(log)
            detail = exc.detail if isinstance(exc.detail, str) else str(exc.detail)
            raise JobError(detail, f"{REFUSED_PREFIX}{exc.status_code})" if exc.status_code < 500 else None)
        except Exception:
            log.finish(succeeded=False)
            keep_log(log)
            raise
        log.finish(succeeded=True)
        # Saved before the live log is dropped, so a poll in between
        # never sees every step reset to queued.
        job.stage_log = log.to_json()
        db.commit()

    return []


_HANDLERS: dict[str, Callable[[Session, ProcessingJob, ProgressReporter], list[str]]] = {
    ProcessingJobType.DOCUMENT_EXTRACTION.value: _process_document,
    ProcessingJobType.RISK_ANALYSIS.value: _run_risk_analysis,
    ProcessingJobType.STAGE_ADVANCE.value: _run_stage_advance,
    ProcessingJobType.RISK_IDENTIFICATION.value: _run_stage_advance,
}

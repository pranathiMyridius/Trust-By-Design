from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.auth.access import ensure_assessment_visible
from app.auth.dependencies import get_current_user
from app.database import get_db
from app.models.assessment import Assessment
from app.models.processing_job import ProcessingJob
from app.models.user import User, UserRole
from app.schemas.processing_job import ProcessingJobResponse, build_processing_job_response
from app.services import processing_jobs, workflow

# Stage 19 (Performance / Reliability): progress, errors and retry for the
# background jobs started by document upload and /analyze-async.
router = APIRouter(prefix="/api/processing-jobs", tags=["Processing Jobs"])

_SEE_ALL_ROLES = {UserRole.ADMIN.value, UserRole.FCRM_ANALYST.value}


def _get_visible_job(db: Session, job_id: int, user: User) -> ProcessingJob:
    job = db.query(ProcessingJob).filter(ProcessingJob.id == job_id).first()
    if job is None:
        raise HTTPException(status_code=404, detail="Processing job not found.")

    if job.assessment_id is not None:
        ensure_assessment_visible(db, job.assessment_id, user)
    elif user.role not in _SEE_ALL_ROLES and job.requested_by_id != user.id:
        raise HTTPException(status_code=403, detail="You do not have access to this job.")

    return job


@router.get("", response_model=list[ProcessingJobResponse])
def list_processing_jobs(
    assessment_id: int | None = None,
    status: str | None = None,
    limit: int = Query(50, ge=1, le=200),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    query = db.query(ProcessingJob)

    if assessment_id is not None:
        ensure_assessment_visible(db, assessment_id, current_user)
        query = query.filter(ProcessingJob.assessment_id == assessment_id)
    elif current_user.role not in _SEE_ALL_ROLES:
        query = query.filter(ProcessingJob.requested_by_id == current_user.id)

    if status:
        query = query.filter(ProcessingJob.status == status)

    jobs = query.order_by(ProcessingJob.id.desc()).limit(limit).all()
    return [build_processing_job_response(job) for job in jobs]


@router.get("/{job_id}", response_model=ProcessingJobResponse)
def get_processing_job(
    job_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    return build_processing_job_response(_get_visible_job(db, job_id, current_user))


@router.post("/{job_id}/retry", response_model=ProcessingJobResponse, status_code=202)
def retry_processing_job(
    job_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    job = _get_visible_job(db, job_id, current_user)

    if not processing_jobs.is_retryable(job):
        if job.attempts >= job.max_attempts:
            detail = (
                f"This job has already been tried {job.attempts} times, the maximum allowed. "
                "Upload a different version of the document or contact support."
            )
        else:
            detail = f"Only failed or incomplete jobs can be retried (this one is {job.status})."
        raise HTTPException(status_code=409, detail=detail)

    if job.job_type in processing_jobs.STAGE_ADVANCE_TYPES:
        # The job advances the stage as whoever requested it, so a retry
        # needs the same permission as starting it -- and then runs as
        # the person retrying, not the original requester.
        from app.api.assessments import next_stage_status
        from app.langgraph.progress import StageLog

        assessment = db.query(Assessment).filter(Assessment.id == job.assessment_id).first()
        target = next_stage_status(StageLog.from_json(job.stage_log).from_status)
        if target is None:
            raise HTTPException(status_code=409, detail="This stage move can no longer be retried.")
        workflow.check_transition(db, assessment, target, user=current_user)
        job.requested_by_id = current_user.id
        job.requested_by = current_user.full_name or current_user.email

    processing_jobs.retry_job(db, job, current_user)
    db.commit()
    processing_jobs.enqueue(job.id)

    db.refresh(job)
    return build_processing_job_response(job)

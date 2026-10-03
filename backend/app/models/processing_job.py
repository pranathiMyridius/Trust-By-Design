from datetime import datetime, timezone
from enum import Enum

from sqlalchemy import Boolean, Column, DateTime, ForeignKey, Integer, String, Text

from app.database import Base


class ProcessingJobStatus(str, Enum):
    QUEUED = "QUEUED"
    RUNNING = "RUNNING"
    SUCCEEDED = "SUCCEEDED"
    # Finished, but some of the output is missing (e.g. no text could be
    # extracted, or semantic indexing failed). The result is kept and
    # flagged incomplete rather than discarded.
    PARTIAL = "PARTIAL"
    FAILED = "FAILED"


class ProcessingJobType(str, Enum):
    DOCUMENT_EXTRACTION = "DOCUMENT_EXTRACTION"
    RISK_ANALYSIS = "RISK_ANALYSIS"
    # Any PATCH /advance-stage move run as a background job, with per-step
    # progress (app/langgraph/progress.py).
    STAGE_ADVANCE = "STAGE_ADVANCE"
    # Earlier name for the same job when it only covered Evidence
    # Collection -> Risk Identification; still read for existing rows.
    RISK_IDENTIFICATION = "RISK_IDENTIFICATION"


class ProcessingJob(Base):
    """
    Stage 19 (Performance / Reliability): one row per long-running
    background task -- extracting an uploaded document's text, or running
    the risk-analysis workflow -- so the request that started it returns
    immediately and the UI polls this row for progress instead of waiting.

    The input the job works on (the stored document file, the assessment
    record) is always committed BEFORE the job is queued, so a failed job
    never loses assessment data: it's marked FAILED with a readable
    error_message and can be retried (POST /api/processing-jobs/{id}/retry).
    """

    __tablename__ = "processing_jobs"

    id = Column(Integer, primary_key=True, index=True)

    job_type = Column(String(40), nullable=False)
    status = Column(String(20), nullable=False, default=ProcessingJobStatus.QUEUED.value, index=True)

    assessment_id = Column(Integer, ForeignKey("assessments.id"), nullable=True, index=True)
    document_id = Column(Integer, ForeignKey("assessment_documents.id"), nullable=True, index=True)

    # 0-100, plus a plain-language description of the current step.
    progress = Column(Integer, nullable=False, default=0)
    stage_message = Column(String(255), nullable=True)

    # Plain-language error for the UI, and the technical detail for
    # support (never shown as the primary message).
    error_message = Column(Text, nullable=True)
    error_detail = Column(Text, nullable=True)

    # True when the job finished with only part of its output (PARTIAL),
    # with the reasons in incomplete_reasons (newline separated).
    is_incomplete = Column(Boolean, nullable=False, default=False)
    incomplete_reasons = Column(Text, nullable=True)

    # STAGE_ADVANCE only: the stage being left plus each step's final
    # status and timestamps, as JSON (see app/langgraph/progress.py).
    # Live progress is read from memory while the job runs.
    stage_log = Column(Text, nullable=True)

    attempts = Column(Integer, nullable=False, default=0)
    max_attempts = Column(Integer, nullable=False, default=5)

    requested_by = Column(String(255), nullable=True)
    requested_by_id = Column(Integer, ForeignKey("users.id"), nullable=True)

    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), nullable=False)
    started_at = Column(DateTime, nullable=True)
    finished_at = Column(DateTime, nullable=True)
    updated_at = Column(
        DateTime,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
        nullable=False,
    )

    def get_incomplete_reasons(self) -> list[str]:
        return [line for line in (self.incomplete_reasons or "").split("\n") if line.strip()]

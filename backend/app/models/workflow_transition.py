from datetime import datetime, timezone

from sqlalchemy import Column, DateTime, ForeignKey, Integer, String, Text

from app.database import Base


class WorkflowTransition(Base):
    """
    Stage 14 (R14.5): one row per status change on an assessment -- the
    dedicated workflow history, distinct from the broader audit_events
    trail (which also records edits, uploads, votes, etc. that aren't
    status changes).

    Records both layers of status: the internal pipeline `status`
    (e.g. CONTROL_ASSESSMENT) and the Stage 14 lifecycle
    `workflow_status` (e.g. RISK_ASSESSMENT_IN_PROGRESS) -- see
    app/services/workflow.py. A lifecycle-only change (e.g. a draft being
    submitted, which leaves the pipeline status at INTAKE) has
    from_status == to_status.
    """

    __tablename__ = "workflow_transitions"

    id = Column(Integer, primary_key=True, index=True)

    assessment_id = Column(
        Integer,
        ForeignKey("assessments.id"),
        nullable=False,
        index=True,
    )

    # Pipeline status. Null from_status only for the creation row.
    from_status = Column(String(50), nullable=True)
    to_status = Column(String(50), nullable=False)

    # Stage 14 lifecycle status.
    from_workflow_status = Column(String(50), nullable=True)
    to_workflow_status = Column(String(50), nullable=False)

    # Short machine-readable label for what caused the transition (e.g.
    # "ADVANCE_STAGE", "MANAGER_DECISION", "CLOSE").
    action = Column(String(50), nullable=False)

    # R14.5: every transition carries a reason or comment.
    reason = Column(Text, nullable=False)

    # Null for system-triggered transitions.
    user_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    actor = Column(String(255), nullable=False, default="System")

    created_at = Column(
        DateTime,
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
        index=True,
    )

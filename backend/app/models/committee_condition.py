from datetime import datetime, timezone

from sqlalchemy import Column, Date, DateTime, ForeignKey, Integer, String, Text

from app.database import Base


class CommitteeCondition(Base):
    """
    Stage 12 (R12.4): a structured condition attached to an "Approve with
    Conditions" committee decision -- distinct from Stage 7's
    ControlCondition (a control-remediation item raised during Control
    Assessment). This one is set by the committee at final decision time
    and tracked to completion the same way, so "the condition is
    included in the final assessment and tracked to completion" holds
    for committee-level conditions too.
    """

    __tablename__ = "committee_conditions"

    id = Column(Integer, primary_key=True, index=True)

    assessment_id = Column(
        Integer,
        ForeignKey("assessments.id"),
        nullable=False,
        index=True,
    )

    description = Column(Text, nullable=False)
    owner = Column(String(255), nullable=False)
    due_date = Column(Date, nullable=False)

    # LOW | MEDIUM | HIGH | CRITICAL
    priority = Column(String(20), nullable=False, default="MEDIUM")

    # OPEN | IN_PROGRESS | COMPLETED | CANCELLED
    status = Column(String(20), nullable=False, default="OPEN")

    completion_evidence = Column(Text, nullable=True)

    created_by = Column(String(255), nullable=True)
    created_at = Column(
        DateTime,
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )
    updated_at = Column(
        DateTime,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
        nullable=False,
    )
    completed_at = Column(DateTime, nullable=True)

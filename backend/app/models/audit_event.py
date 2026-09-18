from datetime import datetime, timezone

from sqlalchemy import Column, DateTime, ForeignKey, Integer, String, Text

from app.database import Base


class AuditEvent(Base):
    __tablename__ = "audit_events"

    id = Column(
        Integer,
        primary_key=True,
        index=True,
    )

    assessment_id = Column(
        Integer,
        ForeignKey("assessments.id"),
        nullable=False,
        index=True,
    )

    action = Column(
        String(50),
        nullable=False,
    )

    previous_status = Column(
        String(50),
        nullable=True,
    )

    new_status = Column(
        String(50),
        nullable=True,
    )

    details = Column(
        Text,
        nullable=True,
    )

    actor = Column(
        String(255),
        nullable=True,
        default="System",
    )

    created_at = Column(
        DateTime,
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )
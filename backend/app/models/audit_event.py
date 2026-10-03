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

    # Nullable: most audit events belong to one assessment, but the
    # standalone Risk Calculator page logs activity that isn't tied to
    # any single assessment (see migrate_audit_calculator.py).
    assessment_id = Column(
        Integer,
        ForeignKey("assessments.id"),
        nullable=True,
        index=True,
    )

    action = Column(
        String(50),
        nullable=False,
        index=True,
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

    # AW: links this event to a real identity, alongside the free-text
    # `actor` display string above (kept for events with no logged-in
    # user, e.g. system-triggered ones).
    actor_id = Column(
        Integer,
        ForeignKey("users.id"),
        nullable=True,
    )

    created_at = Column(
        DateTime,
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
        index=True,
    )
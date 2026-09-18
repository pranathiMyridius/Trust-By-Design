from datetime import datetime, timezone

from sqlalchemy import Column, DateTime, ForeignKey, Integer, String, Text

from app.database import Base


class AssessmentChallenge(Base):
    __tablename__ = "assessment_challenges"

    id = Column(Integer, primary_key=True, index=True)

    assessment_id = Column(
        Integer,
        ForeignKey("assessments.id"),
        nullable=False,
        unique=True,
        index=True,
    )

    challenge_id = Column(String(50), nullable=False, unique=True)

    status = Column(
        String(50),
        nullable=False,
        default="OPEN",
    )

    outcome = Column(String(50), nullable=True)

    comment = Column(Text, nullable=True)

    challenged_by = Column(
        String(255),
        nullable=True,
        default="FCRM Reviewer",
    )

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
from datetime import datetime, timezone

from sqlalchemy import Column, DateTime, ForeignKey, Integer, String, Text

from app.database import Base


class AssessmentFcrmReview(Base):
    """
    Stores the FCRM reviewer's saved state for the "FCRM Review" workflow
    step: the free-text justification and any per-dimension human rating
    overrides. One row per assessment (created on first save).
    """

    __tablename__ = "assessment_fcrm_reviews"

    id = Column(Integer, primary_key=True, index=True)

    assessment_id = Column(
        Integer,
        ForeignKey("assessments.id"),
        nullable=False,
        unique=True,
        index=True,
    )

    justification = Column(Text, nullable=True)

    # JSON-encoded {risk_result_id: severity} map of human overrides.
    human_ratings = Column(Text, nullable=True)

    reviewed_by = Column(
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

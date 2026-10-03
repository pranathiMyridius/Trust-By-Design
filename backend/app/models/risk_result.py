from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
)

from app.database import Base


class RiskResult(Base):
    __tablename__ = "risk_results"

    id = Column(Integer, primary_key=True, index=True)

    assessment_id = Column(
        Integer,
        ForeignKey("assessments.id"),
        nullable=False,
        index=True,
    )

    dimension = Column(
        String(50),
        nullable=False,
    )

    score = Column(
        Float,
        nullable=False,
    )

    severity = Column(
        String(30),
        nullable=False,
    )

    reason = Column(
        Text,
        nullable=False,
    )

    # Re-analysis no longer deletes prior AI results (which would silently
    # invalidate any human rating/audit trail keyed off a risk_result id).
    # Instead each re-run of the risk engine is a new version: previous
    # rows for the assessment are marked is_current=False/superseded_at,
    # and the new rows get an incremented version number.
    version = Column(
        Integer,
        nullable=False,
        default=1,
    )

    is_current = Column(
        Boolean,
        nullable=False,
        default=True,
    )

    superseded_at = Column(
        DateTime,
        nullable=True,
    )
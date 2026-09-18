from sqlalchemy import Column, Float, ForeignKey, Integer, String, Text

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
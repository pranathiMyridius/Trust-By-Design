from datetime import datetime, timezone

from sqlalchemy import Column, DateTime, Float, Integer, String, Text

from app.database import Base


class Assessment(Base):
    __tablename__ = "assessments"

    id = Column(Integer, primary_key=True, index=True)

    title = Column(String(255), nullable=False)

    change_type = Column(
        String(50),
        nullable=False
    )

    description = Column(Text, nullable=False)

    evidence = Column(Text, nullable=False)

    status = Column(
        String(50),
        nullable=False,
        default="DRAFT"
    )

    overall_score = Column(
        Float,
        nullable=True
    )

    risk_level = Column(
        String(30),
        nullable=True
    )

    created_at = Column(
        DateTime,
        default=lambda: datetime.now(timezone.utc),
        nullable=False
    )

    updated_at = Column(
        DateTime,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
        nullable=False
    )

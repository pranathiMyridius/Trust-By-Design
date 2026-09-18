import json
from datetime import datetime, timezone

from sqlalchemy import Column, DateTime, ForeignKey, Integer, String, Text

from app.database import Base


class AssessmentIntelligence(Base):
    __tablename__ = "assessment_intelligence"

    id = Column(
        Integer,
        primary_key=True,
        index=True,
    )

    assessment_id = Column(
        Integer,
        ForeignKey("assessments.id"),
        nullable=False,
        unique=True,
        index=True,
    )

    business_line = Column(
        String(255),
        nullable=True,
    )

    channels = Column(
        Text,
        nullable=True,
    )

    countries = Column(
        Text,
        nullable=True,
    )

    customer_segments = Column(
        Text,
        nullable=True,
    )

    transaction_volume = Column(
        String(255),
        nullable=True,
    )

    average_transaction_size = Column(
        String(255),
        nullable=True,
    )

    maximum_transaction_limit = Column(
        String(255),
        nullable=True,
    )

    third_party_vendors = Column(
        Text,
        nullable=True,
    )

    data_shared = Column(
        Text,
        nullable=True,
    )

    technologies = Column(
        Text,
        nullable=True,
    )

    regulatory_considerations = Column(
        Text,
        nullable=True,
    )

    existing_controls = Column(
        Text,
        nullable=True,
    )

    additional_risk_factors = Column(
        Text,
        nullable=True,
    )

    created_at = Column(
        DateTime,
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )

    def set_list(self, field_name: str, values: list[str]):
        setattr(
            self,
            field_name,
            json.dumps(values),
        )

    def get_list(self, field_name: str) -> list[str]:
        value = getattr(self, field_name)

        if not value:
            return []

        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return []
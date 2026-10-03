import json
from datetime import datetime, timezone

from sqlalchemy import Column, DateTime, ForeignKey, Integer, String, Text

from app.database import Base


class DecisionRecord(Base):
    """
    The reproducibility record frozen when the committee approves an
    assessment: everything needed to explain and re-derive the decision
    later, even after methodologies, reference lists, prompts or models
    change. Written once, never updated; a later approval (after an
    amendment) writes a new record.

    `record` is JSON (see app/services/decision_record_service.py for the
    exact contents) and `checksum` is a SHA-256 over it, so any later
    alteration of the stored record is detectable.
    """

    __tablename__ = "decision_records"

    id = Column(Integer, primary_key=True, index=True)
    assessment_id = Column(Integer, ForeignKey("assessments.id"), nullable=False, index=True)

    decision = Column(String(40), nullable=False)
    decided_by = Column(String(255), nullable=False)
    decided_at = Column(DateTime, nullable=False)

    record = Column(Text, nullable=False)
    checksum = Column(String(80), nullable=False)

    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), nullable=False)

    def get_record(self) -> dict:
        try:
            return json.loads(self.record)
        except (TypeError, ValueError):
            return {}

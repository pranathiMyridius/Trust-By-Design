from datetime import datetime, timezone

from sqlalchemy import Column, DateTime, ForeignKey, Integer, String, Text

from app.database import Base

# R18.1: the trigger types the system knows how to detect (MAJOR_PRODUCT_
# CHANGE, NEW_GEOGRAPHY, NEW_CUSTOMER_SEGMENT, NEW_VENDOR,
# MATERIAL_TRANSACTION_VOLUME_CHANGE, NEW_DELIVERY_CHANNEL, NEW_TECHNOLOGY)
# from a field-level diff against the parent assessment, plus
# EXPIRY/PERIODIC_REVIEW (date-based), and REGULATORY_POLICY_CHANGE /
# SIGNIFICANT_CONTROL_FAILURE, which have no field to diff and so are
# always manually flagged rather than auto-detected.
REASSESSMENT_TRIGGER_TYPES = [
    "MAJOR_PRODUCT_CHANGE",
    "NEW_GEOGRAPHY",
    "NEW_CUSTOMER_SEGMENT",
    "NEW_VENDOR",
    "MATERIAL_TRANSACTION_VOLUME_CHANGE",
    "NEW_DELIVERY_CHANNEL",
    "NEW_TECHNOLOGY",
    "REGULATORY_POLICY_CHANGE",
    "SIGNIFICANT_CONTROL_FAILURE",
    "EXPIRY",
    "PERIODIC_REVIEW",
]

REASSESSMENT_TRIGGER_STATUSES = [
    "OPEN",
    "ACKNOWLEDGED",
    "REASSESSMENT_CREATED",
    "DISMISSED",
]


class ReassessmentTrigger(Base):
    """
    Stage 18 (R18.1, R18.4): a detected or manually-flagged reason an
    approved assessment may need to be reassessed. Recorded against the
    original (approved) assessment; `reassessment_id` is set once a
    reassessment request has actually been opened in response (see
    app/services/reassessment_service.py), so "a material change cannot
    automatically remain approved without review" has a durable record
    of exactly what prompted the review.
    """

    __tablename__ = "reassessment_triggers"

    id = Column(Integer, primary_key=True, index=True)

    assessment_id = Column(
        Integer,
        ForeignKey("assessments.id"),
        nullable=False,
        index=True,
    )

    trigger_type = Column(String(50), nullable=False)
    description = Column(Text, nullable=False)

    detected_at = Column(
        DateTime,
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )
    detected_by = Column(String(255), nullable=True)

    status = Column(String(30), nullable=False, default="OPEN")
    dismissed_reason = Column(Text, nullable=True)
    resolved_by = Column(String(255), nullable=True)
    resolved_at = Column(DateTime, nullable=True)
    # P6: identities (the name columns above are kept for display) and a
    # note recorded on acknowledgement or linking.
    detected_by_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    resolved_by_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    resolution_note = Column(Text, nullable=True)

    # Set once a reassessment (child Assessment) has been opened for
    # this trigger.
    reassessment_id = Column(Integer, ForeignKey("assessments.id"), nullable=True)

from datetime import datetime, timezone

from sqlalchemy import Boolean, Column, DateTime, ForeignKey, Integer, String, Text

from app.database import Base


class RecommendedCondition(Base):
    """
    R8.6: a condition the system recommends for an elevated residual risk
    (additional monitoring, lower limits, geographic restrictions, EDD,
    vendor controls, periodic reassessment, management approval), and
    the analyst's decision on it -- accept, modify or reject, always with
    a reason. Proposed by deterministic rules in
    app/services/residual_conditions.py; never applied without a human.
    """

    __tablename__ = "recommended_conditions"

    id = Column(Integer, primary_key=True, index=True)
    assessment_id = Column(Integer, ForeignKey("assessments.id"), nullable=False, index=True)

    # One of CONDITION_TYPES in app/services/residual_conditions.py.
    condition_type = Column(String(50), nullable=False)
    recommended_text = Column(Text, nullable=False)
    # Why the rules recommended it (band, factors, gaps).
    rationale = Column(Text, nullable=False)

    # PROPOSED | ACCEPTED | MODIFIED | REJECTED
    status = Column(String(20), nullable=False, default="PROPOSED")
    # The condition as adopted: recommended_text if ACCEPTED, the
    # analyst's wording if MODIFIED, None if PROPOSED/REJECTED.
    final_text = Column(Text, nullable=True)
    decision_reason = Column(Text, nullable=True)
    decided_by = Column(String(255), nullable=True)
    decided_by_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    decided_at = Column(DateTime, nullable=True)

    # False once a still-undecided proposal stops applying (the residual
    # risk came down, the factor was re-rated). Decided rows stay current.
    is_current = Column(Boolean, nullable=False, default=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), nullable=False)

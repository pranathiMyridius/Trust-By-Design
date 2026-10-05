from datetime import datetime, timezone

from sqlalchemy import Boolean, Column, DateTime, ForeignKey, Integer, String, Text

from app.database import Base


class AIChallengeFinding(Base):
    """
    A gap the AI challenge analysis raised about an assessment, beyond the
    rule-based control gaps and the Stage 11 challenge review.

    These are advisory. They never block progression, never feed a score and
    are not counted in the rule-based challenge findings; a reviewer
    confirms (the gap is real and needs follow-up) or dismisses (with a
    reason) each one. Every finding records the model and prompt version
    that produced it, and a quote is kept only if it was found verbatim in
    the cited document.
    """

    __tablename__ = "ai_challenge_findings"

    id = Column(Integer, primary_key=True, index=True)
    assessment_id = Column(Integer, ForeignKey("assessments.id"), nullable=False, index=True)

    # One analysis run; findings of a run share it.
    run_id = Column(String(36), nullable=False, index=True)

    # MISSING_CONTROL | CONTROL_MISMATCH | EVIDENCE_GAP | CONTRADICTION | COVERAGE | OTHER
    category = Column(String(30), nullable=False)
    # LOW | MEDIUM | HIGH
    severity = Column(String(10), nullable=False)
    title = Column(String(255), nullable=False)
    detail = Column(Text, nullable=False)

    risk_factor_id = Column(Integer, ForeignKey("risk_factors.id"), nullable=True)
    control_id = Column(Integer, ForeignKey("controls.id"), nullable=True)
    document_id = Column(Integer, ForeignKey("assessment_documents.id"), nullable=True)
    quote = Column(Text, nullable=True)

    # OPEN | CONFIRMED | DISMISSED | SUPERSEDED
    status = Column(String(20), nullable=False, default="OPEN", index=True)

    model = Column(String(100), nullable=True)
    prompt_version = Column(String(100), nullable=True)
    generated_by = Column(String(255), nullable=True)
    generated_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), nullable=False)

    decided_by = Column(String(255), nullable=True)
    decided_by_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    decided_at = Column(DateTime, nullable=True)
    decision_note = Column(Text, nullable=True)

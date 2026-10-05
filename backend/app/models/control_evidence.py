from datetime import datetime, timezone

from sqlalchemy import Column, DateTime, ForeignKey, Integer, String, Text

from app.database import Base


class ControlEvidenceLink(Base):
    """
    An AI suggestion (and the analyst's decision on it) that an uploaded
    assessment document supports a control. Many-to-many: one document can
    back several controls and one control can cite several documents.

    The AI only ever *suggests* (status SUGGESTED). Accepting a suggestion
    is an analyst action and is what records evidence on the control; the
    AI never sets has_evidence or an effectiveness rating by itself.

    A row with support_level NONE and no document records that the control
    was checked and no uploaded document supported it.
    """

    __tablename__ = "control_evidence_links"

    id = Column(Integer, primary_key=True, index=True)

    assessment_id = Column(Integer, ForeignKey("assessments.id"), nullable=False, index=True)
    control_id = Column(Integer, ForeignKey("controls.id"), nullable=False, index=True)

    # Null only for a NONE (nothing found) row.
    document_id = Column(Integer, ForeignKey("assessment_documents.id"), nullable=True, index=True)
    # The document's version when it was read: a later version means re-check.
    document_version = Column(Integer, nullable=True)

    # SUPPORTED | PARTIAL | NONE
    support_level = Column(String(20), nullable=False)
    # LOW | MEDIUM | HIGH
    confidence = Column(String(10), nullable=True)

    # A passage verified to appear in the document text (never invented).
    quote = Column(Text, nullable=True)
    rationale = Column(Text, nullable=True)
    # JSON list of what the evidence does not cover.
    shortfalls = Column(Text, nullable=True)
    # Informational only: EFFECTIVE | PARTIALLY_EFFECTIVE | INEFFECTIVE | UNVERIFIED
    suggested_effectiveness = Column(String(30), nullable=True)

    # SUGGESTED | ACCEPTED | REJECTED | SUPERSEDED
    status = Column(String(20), nullable=False, default="SUGGESTED", index=True)

    model = Column(String(100), nullable=True)
    prompt_version = Column(String(100), nullable=True)
    checked_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), nullable=False)

    decided_by = Column(String(255), nullable=True)
    decided_by_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    decided_at = Column(DateTime, nullable=True)
    decision_note = Column(Text, nullable=True)

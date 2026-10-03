from datetime import datetime, timezone
from sqlalchemy import Boolean, Column, DateTime, ForeignKey, Integer, String, Text
from app.database import Base


class AssessmentDocument(Base):
    __tablename__ = "assessment_documents"

    id = Column(Integer, primary_key=True, index=True)
    assessment_id = Column(Integer, ForeignKey("assessments.id"), nullable=False, index=True)
    filename = Column(String(255), nullable=False)
    file_type = Column(String(50), nullable=False)
    extracted_text = Column(Text, nullable=False)
    file_path = Column(String(500), nullable=True)  # NEW

    # R2.2: document classification metadata. All nullable except
    # document_type (defaulted) so existing rows and simple uploads still
    # work without every field being filled in.
    document_type = Column(String(50), nullable=False, default="OTHER")
    document_owner = Column(String(255), nullable=True)
    source = Column(String(255), nullable=True)
    effective_date = Column(String(20), nullable=True)  # ISO date string
    expiry_date = Column(String(20), nullable=True)  # ISO date string
    confidentiality = Column(String(30), nullable=True)

    # R2.6: document versioning. Uploading a new version of an existing
    # document (rather than an unrelated new one) marks the previous row
    # is_current=False/superseded_at and links back via supersedes_id,
    # mirroring the version/is_current/superseded_at pattern already used
    # by RiskResult for re-analysis history.
    version = Column(Integer, nullable=False, default=1)
    is_current = Column(Boolean, nullable=False, default=True, index=True)
    supersedes_id = Column(
        Integer, ForeignKey("assessment_documents.id"), nullable=True
    )
    superseded_at = Column(DateTime, nullable=True)

    created_at = Column(
        DateTime,
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )
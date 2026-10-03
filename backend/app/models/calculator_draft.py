from datetime import datetime, timezone

from sqlalchemy import Column, DateTime, ForeignKey, Integer, Text

from app.database import Base


class CalculatorDraft(Base):
    """
    The standalone Risk Calculator page's last-used inputs, one row per
    user. The calculator embedded on an assessment saves its draft on the
    assessment itself (Assessment.manual_score_draft); the standalone page
    isn't tied to any assessment, so without this its inputs reset to the
    defaults every time the page was opened.

    New table, so Base.metadata.create_all creates it -- no migration.
    """

    __tablename__ = "calculator_drafts"

    id = Column(Integer, primary_key=True, index=True)

    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, unique=True, index=True)

    # JSON: {"scores": {...}, "included": {...}, "weights": {...}} -- same
    # shape as Assessment.manual_score_draft.
    payload = Column(Text, nullable=False)

    updated_at = Column(
        DateTime,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
        nullable=False,
    )

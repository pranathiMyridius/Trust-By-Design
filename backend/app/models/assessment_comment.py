from datetime import datetime, timezone
from enum import Enum

from sqlalchemy import Boolean, Column, DateTime, ForeignKey, Integer, String, Text

from app.database import Base


class CommentVisibility(str, Enum):
    ALL = "ALL"
    MANAGER_AND_ABOVE = "MANAGER_AND_ABOVE"
    COMMITTEE_ONLY = "COMMITTEE_ONLY"


class AssessmentComment(Base):
    __tablename__ = "assessment_comments"

    id = Column(Integer, primary_key=True, index=True)

    assessment_id = Column(Integer, ForeignKey("assessments.id"), nullable=False, index=True)

    author_id = Column(Integer, ForeignKey("users.id"), nullable=False)

    body = Column(Text, nullable=False)

    # AW.5: who can see this comment. A Business User can only ever post
    # ALL; Manager/Committee/Admin can also post the more restricted tiers.
    visibility = Column(String(20), nullable=False, default=CommentVisibility.ALL.value)

    # R10.6: link this comment to the specific part of the assessment it
    # was made about. `section` is one of the reviewable areas from
    # R10.1 (e.g. "RISK_FACTORS", "CONTROL_MAPPINGS"); `related_entity_id`
    # optionally narrows it to one record within that section (e.g. a
    # risk_result or control id). Both nullable -- older/general comments
    # (and existing rows) have neither.
    section = Column(String(50), nullable=True)
    related_entity_id = Column(String(50), nullable=True)

    # R10 acceptance criteria: an unresolved comment blocks final
    # approval unless an authorized user explicitly accepts the
    # exception (exception_reason set) instead of genuinely resolving it.
    resolved = Column(Boolean, nullable=False, default=False)
    resolved_by = Column(String(255), nullable=True)
    resolved_at = Column(DateTime, nullable=True)
    exception_reason = Column(Text, nullable=True)

    created_at = Column(
        DateTime,
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )

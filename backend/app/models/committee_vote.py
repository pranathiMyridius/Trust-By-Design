from datetime import datetime, timezone

from sqlalchemy import Boolean, Column, DateTime, ForeignKey, Index, Integer, String, Text, UniqueConstraint, text

from app.database import Base


class CommitteeVote(Base):
    """
    Stage 12 (R12.6): an individual committee member's recorded position
    -- approval, dissent, or abstention, with an optional comment --
    separate from the single binding decision recorded by whichever
    member closes out the assessment via POST .../committee-decision.
    A committee can have several members weigh in even though only one
    decision is ultimately binding; this is what lets dissent stay on
    the record rather than being silently overwritten by the final call.

    Append-only (R12.6): casting again -- a member changing their mind
    before the final decision -- adds a new version and marks the previous
    one superseded; no vote is ever overwritten or removed. The member's
    current vote is the one row with is_current true for their seat.
    Votes recorded before migration 0017 became version 1.
    """

    __tablename__ = "committee_votes"
    __table_args__ = (
        UniqueConstraint("assessment_id", "member_id", "version", name="uq_committee_vote_version"),
        # At most one current vote per seat.
        Index(
            "uq_committee_vote_current",
            "assessment_id",
            "member_id",
            unique=True,
            postgresql_where=text("is_current"),
            sqlite_where=text("is_current = 1"),
        ),
    )

    id = Column(Integer, primary_key=True, index=True)

    assessment_id = Column(
        Integer,
        ForeignKey("assessments.id"),
        nullable=False,
        index=True,
    )

    # The committee seat this vote belongs to. Under a delegation (AW.7)
    # that is the delegating member, not the delegate, so a delegate's
    # vote fills the absent member's seat rather than adding a new one.
    member_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    member_name = Column(String(255), nullable=True)

    # AW.7: who actually cast the vote, when a delegate cast it for
    # member_id. Null when the member voted themselves.
    delegate_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    delegate_name = Column(String(255), nullable=True)
    delegation_id = Column(Integer, ForeignKey("approval_delegations.id"), nullable=True)

    # APPROVE | DISSENT | ABSTAIN
    vote = Column(String(20), nullable=False)

    comment = Column(Text, nullable=True)

    # R12.6 history. version counts this seat's votes on the assessment.
    version = Column(Integer, nullable=False, default=1, server_default="1")
    is_current = Column(Boolean, nullable=False, default=True, server_default=text("true"))
    superseded_at = Column(DateTime, nullable=True)
    superseded_by_id = Column(Integer, ForeignKey("committee_votes.id"), nullable=True)

    # Who pressed the button (the member, or their delegate). Migration
    # 0017 filled it for earlier votes from delegate_id / member_id.
    cast_by_id = Column(Integer, ForeignKey("users.id"), nullable=True)

    # Why the member changed a previously cast vote; required on a re-cast.
    recast_reason = Column(Text, nullable=True)

    # When this vote was cast. Never updated.
    voted_at = Column(
        DateTime,
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )

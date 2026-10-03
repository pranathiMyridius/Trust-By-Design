from datetime import datetime, timezone

from sqlalchemy import Column, DateTime, ForeignKey, Integer, String, Text

from app.database import Base


class ApprovalDelegation(Base):
    """
    AW.7: a temporary grant of one approver's authority to another user,
    for when a Manager or Committee Member is unavailable.

    Carries the seven fields the requirement names: delegator, delegate,
    start, end, scope, reason and approval authority. Whether a
    delegation is in force is never stored -- it is computed at the
    moment of approval from start_at/end_at/revoked_at (see
    app/services/delegation.py), so an expired delegation stops granting
    access by construction, with no background job to fail or lag.

    Rows are never deleted: revoking sets revoked_at, keeping the record
    of who could approve what, when.
    """

    __tablename__ = "approval_delegations"

    id = Column(Integer, primary_key=True, index=True)

    delegator_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    delegate_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)

    # MANAGER_APPROVAL | COMMITTEE_SIGN_OFF
    authority = Column(String(30), nullable=False)

    # ALL (everything the delegator could approve) | ASSESSMENT (one).
    scope_type = Column(String(20), nullable=False)
    scope_assessment_id = Column(Integer, ForeignKey("assessments.id"), nullable=True)

    # Half-open window [start_at, end_at), stored as naive UTC like the
    # rest of the schema.
    start_at = Column(DateTime, nullable=False)
    end_at = Column(DateTime, nullable=False)

    reason = Column(Text, nullable=False)

    created_by_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    created_at = Column(
        DateTime,
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )

    revoked_at = Column(DateTime, nullable=True)
    revoked_by_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    revoke_reason = Column(Text, nullable=True)

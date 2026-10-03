from datetime import datetime, timezone

from sqlalchemy import Column, DateTime, ForeignKey, Integer, String, Text

from app.database import Base


class AssessmentOverride(Base):
    """
    R10.2/R10.3/R10.4: a single analyst-made change to an AI-generated
    value, on any reviewable section of the assessment (extracted field,
    risk category, factor rating, risk rationale, control mapping,
    control effectiveness, residual risk, recommended condition). One row
    per change -- the AI value is captured at override time so the
    original stays visible even if the underlying record is edited again
    later (R10.4/AC5).

    A ledger: rows are never deleted, and after creation only the review
    fields may be set, once (app/services/data_protection.py). The
    overridden calculation itself is never changed by a ledger row --
    calculated values stay on their own versioned records
    (inherent_risk_calculations.calculated_*, residual_risk_calculations.
    residual_*, risk_factors.ai_suggested_*).
    """

    __tablename__ = "assessment_overrides"

    id = Column(Integer, primary_key=True, index=True)

    assessment_id = Column(Integer, ForeignKey("assessments.id"), nullable=False, index=True)

    # One of the OVERRIDE_SECTIONS values in schemas/assessment_override.py.
    section = Column(String(50), nullable=False)

    # Name of the field/record changed (e.g. "customer_segment",
    # "severity"). Free text -- `section` (and `entity_id`, if set)
    # already scope what it means.
    field_name = Column(String(255), nullable=False)

    # Optional id of the underlying record this override applies to (e.g.
    # a risk_result or control id), stored as text so it can reference
    # any entity type without a polymorphic foreign key.
    entity_id = Column(String(50), nullable=True)

    # The system's value at override time. Since migration 0017 it is
    # always read server-side from the record (ai_value_source SYSTEM);
    # older rows hold whatever the client sent (NULL source = legacy).
    ai_value = Column(Text, nullable=True)
    ai_value_source = Column(String(20), nullable=True)
    human_value = Column(Text, nullable=False)

    # R10.3: every material change requires a reason.
    reason = Column(Text, nullable=False)

    overridden_by = Column(String(255), nullable=True)
    overridden_by_id = Column(Integer, ForeignKey("users.id"), nullable=True)

    # REVIEW_STATUSES below. APPLIED: recorded by a typed endpoint that
    # made the change itself (a factor rating, the inherent override, the
    # residual confirmation, a control remap) under that endpoint's own
    # role rules. PROPOSED: a free-standing override awaiting independent
    # review; CONFIRMED / REJECTED once reviewed. NULL: recorded before
    # migration 0017 and never reviewed.
    review_status = Column(String(20), nullable=True)
    reviewed_by = Column(String(255), nullable=True)
    reviewed_by_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    reviewed_at = Column(DateTime, nullable=True)
    review_note = Column(Text, nullable=True)

    # P3. origin: TYPED (made by a typed endpoint, in effect at once) or
    # PROPOSAL (free-standing, in effect only once confirmed); NULL legacy.
    origin = Column(String(20), nullable=True)
    # NONMATERIAL | MATERIAL | CRITICAL, classified by the server from the
    # actual impact (app/governance/materiality.py) -- never a user label.
    # NULL on rows recorded before migration 0018.
    materiality = Column(String(20), nullable=True)
    materiality_reasons = Column(Text, nullable=True)
    # NOT_REQUIRED | PENDING | APPROVED | REJECTED -- the material-override
    # approval that follows a confirming independent review.
    approval_status = Column(String(20), nullable=True)
    approved_by = Column(String(255), nullable=True)
    approved_by_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    approved_at = Column(DateTime, nullable=True)
    approval_rationale = Column(Text, nullable=True)

    created_at = Column(
        DateTime,
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )


REVIEW_APPLIED = "APPLIED"
REVIEW_PROPOSED = "PROPOSED"
REVIEW_CONFIRMED = "CONFIRMED"
REVIEW_REJECTED = "REJECTED"
REVIEW_STATUSES = {REVIEW_APPLIED, REVIEW_PROPOSED, REVIEW_CONFIRMED, REVIEW_REJECTED}

# Human values that count in the decision package.
AUTHORISED_REVIEW_STATUSES = {REVIEW_APPLIED, REVIEW_CONFIRMED}

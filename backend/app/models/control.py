from datetime import datetime, timezone

from sqlalchemy import Boolean, Column, Date, DateTime, ForeignKey, Integer, String, Text

from app.database import Base


class Control(Base):
    """
    Stage 7 (R7.1-R7.3): a control mapped to one identified risk
    (RiskFactor) on an assessment.

    The control library itself (R7.1 -- KYC/CDD, sanctions screening,
    transaction monitoring, etc.) is a fixed constant list, see
    app/control_engine/library.py; this table holds instances of that
    library mapped to a specific risk on a specific assessment, same
    relationship as RiskFactor is to RISK_CATEGORIES.

    Versioned like RiskFactor/InherentRiskCalculation so re-mapping or
    editing a control's metadata doesn't destroy the record of what was
    in force at an earlier point in the assessment's history.
    """

    __tablename__ = "controls"

    id = Column(Integer, primary_key=True, index=True)

    assessment_id = Column(
        Integer,
        ForeignKey("assessments.id"),
        nullable=False,
        index=True,
    )

    # R7.2: the risk this control is mapped to.
    risk_factor_id = Column(
        Integer,
        ForeignKey("risk_factors.id"),
        nullable=False,
        index=True,
    )

    # R7.1: one of CONTROL_LIBRARY (see app/control_engine/library.py).
    control_type = Column(String(100), nullable=False)

    description = Column(Text, nullable=True)

    # --- R7.3: control ownership/operating metadata ---
    owner = Column(String(255), nullable=True)
    performing_department = Column(String(255), nullable=True)
    frequency = Column(String(100), nullable=True)
    trigger = Column(String(255), nullable=True)
    scope = Column(Text, nullable=True)
    evidence_source = Column(String(255), nullable=True)
    operating_status = Column(String(50), nullable=False, default="ACTIVE")

    added_by = Column(String(255), nullable=True)

    version = Column(Integer, nullable=False, default=1)
    is_current = Column(Boolean, nullable=False, default=True)
    superseded_at = Column(DateTime, nullable=True)

    created_at = Column(
        DateTime,
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )

    updated_at = Column(
        DateTime,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
        nullable=False,
    )


class ControlAssessment(Base):
    """
    Stage 7 (R7.4-R7.6): a design/operating-effectiveness evaluation of a
    Control, kept in its own table (rather than columns on Control) so
    re-assessing a control's effectiveness over time doesn't overwrite the
    control's static ownership metadata, and so a past assessment stays
    inspectable (versioned the same way).

    `operating_effectiveness` is never set to EFFECTIVE by the API layer
    unless `has_evidence` is true (R7.6/AC3: no supporting evidence ->
    effectiveness is marked UNVERIFIED, not silently treated as adequate).
    """

    __tablename__ = "control_assessments"

    id = Column(Integer, primary_key=True, index=True)

    control_id = Column(
        Integer,
        ForeignKey("controls.id"),
        nullable=False,
        index=True,
    )

    assessment_id = Column(
        Integer,
        ForeignKey("assessments.id"),
        nullable=False,
        index=True,
    )

    # R7.4: DESIGN_ADEQUATE | DESIGN_INADEQUATE | NOT_ASSESSED
    design_adequacy = Column(String(30), nullable=False, default="NOT_ASSESSED")
    design_rationale = Column(Text, nullable=True)

    # R7.5/R7.6: EFFECTIVE | PARTIALLY_EFFECTIVE | INEFFECTIVE | UNVERIFIED
    operating_effectiveness = Column(String(30), nullable=False, default="UNVERIFIED")
    effectiveness_rationale = Column(Text, nullable=True)

    # R7.6: drives the UNVERIFIED enforcement described above.
    has_evidence = Column(Boolean, nullable=False, default=False)

    # R7.6: false -> "control does not cover the full risk" gap.
    coverage_complete = Column(Boolean, nullable=False, default=True)

    # R7.6: "control dependent on unavailable data" gap.
    depends_on_unavailable_data = Column(Boolean, nullable=False, default=False)

    assessed_by = Column(String(255), nullable=True)
    assessed_at = Column(
        DateTime,
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )

    version = Column(Integer, nullable=False, default=1)
    is_current = Column(Boolean, nullable=False, default=True)
    superseded_at = Column(DateTime, nullable=True)


class ControlCondition(Base):
    """
    R7.7: a required control enhancement/condition, tracked to completion
    (AC5: "the condition is included in the final assessment and tracked
    to completion").
    """

    __tablename__ = "control_conditions"

    id = Column(Integer, primary_key=True, index=True)

    control_id = Column(
        Integer,
        ForeignKey("controls.id"),
        nullable=False,
        index=True,
    )

    assessment_id = Column(
        Integer,
        ForeignKey("assessments.id"),
        nullable=False,
        index=True,
    )

    description = Column(Text, nullable=False)

    # OPEN | IN_PROGRESS | COMPLETED | CANCELLED
    status = Column(String(20), nullable=False, default="OPEN")

    due_date = Column(Date, nullable=True)
    owner = Column(String(255), nullable=True)

    created_by = Column(String(255), nullable=True)
    created_at = Column(
        DateTime,
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )

    updated_at = Column(
        DateTime,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
        nullable=False,
    )

    completed_at = Column(DateTime, nullable=True)


class ControlGap(Base):
    """
    R7.6: a persisted control gap, recomputed by
    app/control_engine/gap_detection.py whenever a Control or
    ControlAssessment changes. Persisted (not just computed on read) so
    gap history stays visible in an assessment's record even if the
    underlying control is later fixed.
    """

    __tablename__ = "control_gaps"

    id = Column(Integer, primary_key=True, index=True)

    assessment_id = Column(
        Integer,
        ForeignKey("assessments.id"),
        nullable=False,
        index=True,
    )

    # Null when the gap is about a risk with no control at all.
    risk_factor_id = Column(
        Integer,
        ForeignKey("risk_factors.id"),
        nullable=True,
        index=True,
    )

    # Null when the gap is "risk without any control" (no control exists
    # to reference).
    control_id = Column(
        Integer,
        ForeignKey("controls.id"),
        nullable=True,
        index=True,
    )

    # NO_CONTROL | NO_EVIDENCE | INEFFECTIVE | PARTIAL |
    # INCOMPLETE_COVERAGE | UNAVAILABLE_DATA_DEPENDENCY
    gap_type = Column(String(50), nullable=False)

    description = Column(Text, nullable=True)

    detected_at = Column(
        DateTime,
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )

    resolved = Column(Boolean, nullable=False, default=False)
    resolved_at = Column(DateTime, nullable=True)


class ControlRevision(Base):
    """
    R7.2 / R10.2: one append-only entry per change to a control's mapping
    or configuration -- EDIT (metadata), REMAP (moved to another risk) or
    UNMAP (removed from the assessment). Records the configuration before
    and after, who, when and why. The Control row keeps its id (so its
    effectiveness assessments and conditions stay attached) and its
    `version` counts these revisions; an unmapped control is no longer
    current but is never deleted.
    """

    __tablename__ = "control_revisions"

    id = Column(Integer, primary_key=True, index=True)

    control_id = Column(Integer, ForeignKey("controls.id"), nullable=False, index=True)
    assessment_id = Column(Integer, ForeignKey("assessments.id"), nullable=False, index=True)

    # EDIT | REMAP | UNMAP
    change_type = Column(String(20), nullable=False)

    # The control's version this revision produced.
    version = Column(Integer, nullable=False)

    # JSON snapshots of the mapped configuration (CONTROL_CONFIG_FIELDS).
    previous_config = Column(Text, nullable=False)
    new_config = Column(Text, nullable=True)
    changed_fields = Column(Text, nullable=False)

    reason = Column(Text, nullable=False)
    changed_by = Column(String(255), nullable=False)
    changed_by_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    changed_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), nullable=False)


# The mapped configuration a revision snapshots.
CONTROL_CONFIG_FIELDS = (
    "risk_factor_id",
    "control_type",
    "description",
    "owner",
    "performing_department",
    "frequency",
    "trigger",
    "scope",
    "evidence_source",
    "operating_status",
)

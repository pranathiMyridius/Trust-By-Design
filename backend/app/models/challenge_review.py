from datetime import datetime, timezone

from sqlalchemy import Boolean, Column, DateTime, Float, ForeignKey, Integer, String, Text, text

from app.database import Base


class ChallengeTriggerConfig(Base):
    """
    R11.1: configurable conditions that trigger a Stage 11 challenge
    review. Exactly one row should have is_active=True at a time -- same
    single-active-row pattern as RiskMethodology. If no active row
    exists, the engine falls back to the built-in defaults in
    app/challenge_engine/rules.py.
    """

    __tablename__ = "challenge_trigger_configs"

    id = Column(Integer, primary_key=True, index=True)

    name = Column(String(255), nullable=False)
    is_active = Column(Boolean, nullable=False, default=False)

    # A risk_level/inherent_risk_level at or above one of these fires the
    # "risk is high or critical" trigger. JSON list, e.g. ["HIGH","CRITICAL"].
    trigger_risk_levels = Column(Text, nullable=False)

    # "Residual risk exceeds tolerance" trigger.
    residual_risk_tolerance = Column(Float, nullable=False, default=60.0)

    # Toggle for "human and system ratings differ materially".
    rating_mismatch_enabled = Column(Boolean, nullable=False, default=True)

    # Toggle for the inherent-risk-calculation "is_provisional" proxy for
    # "confidence is low" (no numeric confidence score exists elsewhere in
    # the system to test against).
    low_confidence_enabled = Column(Boolean, nullable=False, default=True)

    # JSON list of jurisdiction names/codes considered high-risk for the
    # "new high-risk jurisdiction or technology" trigger.
    high_risk_jurisdictions = Column(Text, nullable=False)

    # JSON list of technology/keyword substrings considered high-risk.
    high_risk_technologies = Column(Text, nullable=False)

    # R11.1: JSON list of trigger names switched off (see
    # CONFIGURABLE_TRIGGERS in app/challenge_engine/rules.py). A disabled
    # trigger neither fires nor produces findings.
    disabled_triggers = Column(Text, nullable=True, default="[]")

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


class ChallengeFinding(Base):
    """
    Stage 11 (R11.2-R11.6): a persisted challenge-review finding.

    Recomputed (reconciled, not overwritten) whenever the challenge
    review runs -- see app/challenge_engine/engine.py -- so a finding's
    resolution/acceptance state survives across re-runs the same way
    ControlGap does: a finding no longer detected is left alone if
    already resolved/accepted, or auto-resolved if it was still open;
    a finding already open is not duplicated.
    """

    __tablename__ = "challenge_findings"

    id = Column(Integer, primary_key=True, index=True)

    assessment_id = Column(
        Integer,
        ForeignKey("assessments.id"),
        nullable=False,
        index=True,
    )

    # MISSING_RISK | UNSUPPORTED_CONCLUSION | CONTRADICTION |
    # RATING_MISMATCH | WEAK_CONTROL | RESIDUAL_RISK_EXCEEDS_TOLERANCE |
    # HIGH_RISK_JURISDICTION_OR_TECHNOLOGY | LOW_CONFIDENCE
    category = Column(String(50), nullable=False)

    # R11.5
    description = Column(Text, nullable=False)
    related_section = Column(String(50), nullable=False)
    severity = Column(String(20), nullable=False, default="MEDIUM")
    supporting_evidence = Column(Text, nullable=True)
    recommended_action = Column(Text, nullable=True)

    # OPEN | RESOLVED | ACCEPTED
    resolution_status = Column(String(20), nullable=False, default="OPEN")

    resolved_by = Column(String(255), nullable=True)
    resolved_at = Column(DateTime, nullable=True)
    resolution_note = Column(Text, nullable=True)

    # R11.6/AC5: an accepted (rather than resolved) finding still records
    # who accepted it, why, and when.
    accepted_by = Column(String(255), nullable=True)
    accepted_at = Column(DateTime, nullable=True)
    accepted_reason = Column(Text, nullable=True)
    # G-4 (2026-10-03): only MEDIUM findings may be accepted, and only as a
    # documented Committee exception: COMMITTEE here. NULL on acceptances
    # recorded under the earlier rule, which no longer count (0024).
    accepted_by_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    acceptance_authority = Column(String(30), nullable=True)

    detected_at = Column(
        DateTime,
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )


class ChallengeReviewSignoff(Base):
    """
    R11 (mandatory challenge review): the recorded completion of the
    challenge review, required for every assessment before it may go to
    the committee -- including when no trigger fired, in which case the
    reviewer attests to that.

    Versioned and supersede-only: a new sign-off (e.g. after the
    assessment was returned and re-submitted) marks the previous one
    superseded; nothing is overwritten (app/services/data_protection.py).
    The trigger evaluation and the findings as they stood are frozen on
    the row, so the record shows what the reviewer actually looked at.
    """

    __tablename__ = "challenge_review_signoffs"

    id = Column(Integer, primary_key=True, index=True)

    assessment_id = Column(Integer, ForeignKey("assessments.id"), nullable=False, index=True)

    # P3: REVIEW (the independent challenge review) or SIGNOFF (the FCRM
    # Manager / Head of FCRM sign-off that follows it). One current row per
    # (assessment, stage). Rows recorded before migration 0018 are SIGNOFF.
    stage = Column(String(10), nullable=False, default="SIGNOFF", server_default="SIGNOFF")

    # P3: set on a sign-off of a CRITICAL case -- the Committee is told the
    # case was escalated to it by the challenge process.
    committee_escalation = Column(Boolean, nullable=False, default=False, server_default=text("false"))

    # NO_TRIGGERS_FIRED | FINDINGS_ADDRESSED -- derived by the system from
    # the frozen evaluation, never chosen by the reviewer.
    outcome = Column(String(30), nullable=False)

    # The reviewer's attestation / summary of how findings were dealt with.
    reason = Column(Text, nullable=False)

    # JSON: {"triggered": bool, "triggers": [...]} at sign-off.
    trigger_snapshot = Column(Text, nullable=False)
    # JSON: [{"id", "category", "severity", "resolution_status"}] -- ids
    # and states only, never the evidence text.
    findings_snapshot = Column(Text, nullable=False)

    reviewer = Column(String(255), nullable=False)
    reviewer_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    reviewer_role = Column(String(30), nullable=False)
    completed_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), nullable=False)

    version = Column(Integer, nullable=False, default=1)
    is_current = Column(Boolean, nullable=False, default=True)
    superseded_at = Column(DateTime, nullable=True)
    # Why it stopped being current (a newer sign-off, the assessment
    # returned by the manager, a new finding after sign-off, ...).
    superseded_reason = Column(Text, nullable=True)

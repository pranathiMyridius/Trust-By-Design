from datetime import datetime, timezone

from sqlalchemy import Boolean, Column, DateTime, Integer, String, Text

from app.database import Base


class CountryRisk(Base):
    """
    Published country/jurisdiction risk designations (FATF today, other
    sources later), loaded from a dated snapshot under
    app/reference_data/ rather than hardcoded in the rules.

    Two things this is deliberately NOT:

    * Not a sanctions screening list. These are country-level risk
      designations used to inform an assessment; screening named parties
      against OFAC/UN/OFSI is a different system with different
      freshness requirements.
    * Not a live feed. FATF revises at each plenary (roughly three times
      a year), so rows carry the `as_of` of the statement they came from.
      Snapshots are versioned rather than overwritten (is_current, same
      pattern as RiskFactor/InherentRiskCalculation) so an assessment
      completed under an older list stays reproducible.
    """

    __tablename__ = "country_risk"

    id = Column(Integer, primary_key=True, index=True)

    # ISO 3166-1 alpha-2. The canonical key -- free-text country names
    # from the intake form are normalised to this before any lookup
    # (see app/reference_data/country_normalizer.py).
    iso_code = Column(String(2), nullable=False, index=True)

    # The name as the source publishes it, kept for display and so a
    # reviewer can see what was actually listed.
    name = Column(String(255), nullable=False)

    # Source-specific designation, e.g. FATF's CALL_FOR_ACTION or
    # INCREASED_MONITORING. Not normalised across sources on purpose:
    # an EU listing and a FATF listing mean different things and
    # flattening them would lose that.
    tier = Column(String(50), nullable=False, index=True)

    # Which publication this row came from, e.g. "FATF".
    source = Column(String(50), nullable=False, index=True)

    # The date of the statement this snapshot represents (not the load
    # time), so "which list was this assessed against?" is answerable.
    as_of = Column(String(20), nullable=False)

    # False when the source's own snapshot says it has not been checked
    # against the primary publication. Callers that gate on data quality
    # can refuse to escalate on unverified rows.
    verified = Column(Boolean, nullable=False, default=False)

    notes = Column(Text, nullable=True)

    # The ReferenceDataSnapshot this row was loaded from. NULL for rows
    # loaded before snapshots were recorded; those are never attested, so
    # they never feed scoring.
    snapshot_id = Column(Integer, nullable=True, index=True)

    is_current = Column(Boolean, nullable=False, default=True, index=True)
    superseded_at = Column(DateTime, nullable=True)

    created_at = Column(
        DateTime,
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )

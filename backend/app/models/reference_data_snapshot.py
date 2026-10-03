from datetime import datetime, timezone

from sqlalchemy import Boolean, Column, DateTime, Integer, String, Text

from app.database import Base


class ReferenceDataSnapshot(Base):
    """
    One loaded publication of a reference list (e.g. the FATF statement of
    a given plenary, or a consolidated EU Regulation text), identified by a
    checksum of exactly what was loaded. CountryRisk rows point at the
    snapshot they came from.

    Two kinds of "verified" are kept apart on purpose:

    * source_claims_verified -- what the snapshot file says about itself.
      Recorded, never relied on: a file cannot vouch for itself.
    * attested_at / attested_by -- a named reviewer confirmed the snapshot
      against the primary publication (POST /api/reference-data/
      snapshots/{id}/attest). Only attested snapshots feed scoring rules;
      an unattested one is still shown to reviewers and used by the
      challenge engine, which flags rather than scores.

    Calculations record the snapshots they used (id, source, as_of,
    checksum), so a result stays explainable against the list actually in
    force even after the next plenary replaces it.
    """

    __tablename__ = "reference_data_snapshots"

    id = Column(Integer, primary_key=True, index=True)

    # "FATF", "EU", ...
    source = Column(String(50), nullable=False, index=True)
    # Date of the publication itself, not of the load.
    as_of = Column(String(20), nullable=False)
    source_url = Column(Text, nullable=True)
    statement = Column(Text, nullable=True)

    # "sha256:..." over the canonical JSON of the loaded file.
    checksum = Column(String(80), nullable=False)
    entry_count = Column(Integer, nullable=False, default=0)

    source_claims_verified = Column(Boolean, nullable=False, default=False)
    source_verification_note = Column(Text, nullable=True)

    attested_by = Column(String(255), nullable=True)
    attested_at = Column(DateTime, nullable=True)
    attestation_note = Column(Text, nullable=True)

    loaded_by = Column(String(255), nullable=True)
    loaded_at = Column(
        DateTime,
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )

    is_current = Column(Boolean, nullable=False, default=True, index=True)
    superseded_at = Column(DateTime, nullable=True)

    @property
    def attested(self) -> bool:
        return self.attested_at is not None

    def as_reference(self) -> dict:
        """How a calculation records that it used this snapshot."""

        return {
            "snapshot_id": self.id,
            "source": self.source,
            "as_of": self.as_of,
            "checksum": self.checksum,
            "attested": self.attested,
            "attested_by": self.attested_by,
        }

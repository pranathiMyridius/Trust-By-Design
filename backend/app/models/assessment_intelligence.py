import json
from datetime import datetime, timezone

from sqlalchemy import Boolean, Column, DateTime, ForeignKey, Integer, String, Text

from app.database import Base


class AssessmentIntelligence(Base):
    __tablename__ = "assessment_intelligence"

    id = Column(
        Integer,
        primary_key=True,
        index=True,
    )

    assessment_id = Column(
        Integer,
        ForeignKey("assessments.id"),
        nullable=False,
        unique=True,
        index=True,
    )

    business_line = Column(
        String(255),
        nullable=True,
    )

    # --- R3.1: additional structured profile fields not otherwise
    # covered above (product/customer segment/countries/channels/
    # transaction volume/value already exist as columns on this model).
    customer_type = Column(String(255), nullable=True)
    transaction_origin = Column(String(255), nullable=True)
    transaction_destination = Column(String(255), nullable=True)
    transaction_frequency = Column(String(255), nullable=True)
    payment_methods = Column(Text, nullable=True)
    onboarding_approach = Column(String(255), nullable=True)
    ownership_entity_structure = Column(Text, nullable=True)

    channels = Column(
        Text,
        nullable=True,
    )

    countries = Column(
        Text,
        nullable=True,
    )

    customer_segments = Column(
        Text,
        nullable=True,
    )

    transaction_volume = Column(
        String(255),
        nullable=True,
    )

    average_transaction_size = Column(
        String(255),
        nullable=True,
    )

    maximum_transaction_limit = Column(
        String(255),
        nullable=True,
    )

    third_party_vendors = Column(
        Text,
        nullable=True,
    )

    data_shared = Column(
        Text,
        nullable=True,
    )

    technologies = Column(
        Text,
        nullable=True,
    )

    regulatory_considerations = Column(
        Text,
        nullable=True,
    )

    existing_controls = Column(
        Text,
        nullable=True,
    )

    additional_risk_factors = Column(
        Text,
        nullable=True,
    )

    # R2.4: which document this was extracted from (the one document
    # analysis was run against when this record was created), if any --
    # a manually-created assessment has no intelligence record at all,
    # so this is only ever set on the document-driven creation paths.
    source_document_id = Column(
        Integer,
        ForeignKey("assessment_documents.id"),
        nullable=True,
    )

    # R2.3: the AI's original output, exactly as extracted, captured once
    # at creation time and never modified afterwards -- kept alongside
    # the (possibly since-edited) fields above so a correction never
    # loses what the AI originally said. JSON text of the full
    # DocumentAssessmentExtraction payload.
    raw_extraction = Column(
        Text,
        nullable=True,
    )

    # P4 (R2.4): per-field provenance, JSON {field: record}. Each record
    # names the source document/version, page or sheet, the verified quote,
    # how the value was found and a confidence level derived
    # deterministically from that verification (never from a model's own
    # confidence) -- see app/governance/provenance.py. A user correction
    # replaces the field's record with one of origin USER_CORRECTION.
    field_provenance = Column(Text, nullable=True)
    # AI | RULES | INTAKE_FORM -- how this profile was produced.
    extraction_method = Column(String(20), nullable=True)
    extracted_at = Column(DateTime, nullable=True)
    # JSON list of every document the extraction read (source_document_id
    # is only the first, for multi-file uploads).
    source_document_ids = Column(Text, nullable=True)

    # R2.3/R2.4: whether a human has reviewed and confirmed this
    # extracted information (as opposed to it still being an
    # unconfirmed AI suggestion).
    confirmed = Column(
        Boolean,
        nullable=False,
        default=False,
    )

    confirmed_by = Column(
        String(255),
        nullable=True,
    )

    confirmed_at = Column(
        DateTime,
        nullable=True,
    )

    created_at = Column(
        DateTime,
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )

    def set_list(self, field_name: str, values: list[str]):
        setattr(
            self,
            field_name,
            json.dumps(values),
        )

    def get_list(self, field_name: str) -> list[str]:
        value = getattr(self, field_name)

        if not value:
            return []

        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return []
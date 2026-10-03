from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


class DocumentAssessmentExtraction(BaseModel):
    """
    Structured information extracted from an uploaded
    business change document.
    """

    title: str = Field(
        description="Name of the proposed business change or initiative."
    )

    change_type: str = Field(
        description=(
            "Type of business change. "
            "Must be one of: NEW_PRODUCT, MATERIAL_CHANGE, "
            "NEW_GEOGRAPHY, THIRD_PARTY."
        )
    )

    business_description: str = Field(
        description=(
            "Clear summary of what the proposed change is "
            "and what the business intends to do."
        )
    )

    evidence: str = Field(
        description=(
            "Important facts from the source document that "
            "support the assessment."
        )
    )

    business_line: Optional[str] = None

    # R1.1: fields the intake form needs that have no other structured
    # counterpart above (unlike e.g. delivery channels/countries/customer
    # segment, which the frontend derives from the list fields below).
    product_or_service_name: Optional[str] = None
    business_owner: Optional[str] = None
    legal_entity: Optional[str] = None
    transaction_types: Optional[str] = None
    expected_launch_date: Optional[str] = None

    # R1.5: who the document itself names as the submitter (e.g. a
    # "Submitted By:" line), if any. Null when the document doesn't
    # state one -- the frontend falls back to its own default in that
    # case.
    submitted_by: Optional[str] = None

    channels: List[str] = Field(
        default_factory=list
    )

    countries: List[str] = Field(
        default_factory=list
    )

    customer_segments: List[str] = Field(
        default_factory=list
    )

    transaction_volume: Optional[str] = None

    average_transaction_size: Optional[str] = None

    maximum_transaction_limit: Optional[str] = None

    third_party_vendors: List[str] = Field(
        default_factory=list
    )

    data_shared: List[str] = Field(
        default_factory=list
    )

    technologies: List[str] = Field(
        default_factory=list
    )

    regulatory_considerations: List[str] = Field(
        default_factory=list
    )

    existing_controls: List[str] = Field(
        default_factory=list
    )

    additional_risk_factors: List[str] = Field(
        default_factory=list
    )

    # P4 (R2.4): the verbatim passages the model cites for each field,
    # {field: [{"document": filename, "quote": text}]}. Only used as
    # claims to check -- app/governance/provenance.py verifies every quote
    # against the document text and derives the confidence itself.
    field_evidence: Dict[str, List[Dict[str, Any]]] = Field(default_factory=dict)

    # AI | RULES: which extractor produced this result.
    extraction_method: str = "AI"
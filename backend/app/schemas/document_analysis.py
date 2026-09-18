from typing import List, Optional

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
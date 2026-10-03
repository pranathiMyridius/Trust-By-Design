from datetime import datetime
from typing import Any, List, Optional

from pydantic import BaseModel


class ConsistencyConflict(BaseModel):
    """R3.2: one detected contradiction -- between the intake form and the
    structured/extracted profile (source FORM_VS_PROFILE), or between two
    uploaded documents (source DOCUMENTS, with the passage each one gave)."""

    field: str
    source: str = "FORM_VS_PROFILE"
    form_value: Optional[str] = None
    extracted_value: Optional[str] = None
    documents: List[dict[str, Any]] = []
    message: str


class AssessmentIntelligenceResponse(BaseModel):
    id: int
    assessment_id: int

    business_line: Optional[str] = None

    # R3.1: structured business/product profile fields.
    customer_type: Optional[str] = None
    transaction_origin: Optional[str] = None
    transaction_destination: Optional[str] = None
    transaction_frequency: Optional[str] = None
    payment_methods: List[str] = []
    onboarding_approach: Optional[str] = None
    ownership_entity_structure: Optional[str] = None

    channels: List[str] = []
    countries: List[str] = []
    customer_segments: List[str] = []

    transaction_volume: Optional[str] = None
    average_transaction_size: Optional[str] = None
    maximum_transaction_limit: Optional[str] = None

    third_party_vendors: List[str] = []
    data_shared: List[str] = []
    technologies: List[str] = []
    regulatory_considerations: List[str] = []
    existing_controls: List[str] = []
    additional_risk_factors: List[str] = []

    # R2.4: provenance -- which document this came from, if any.
    source_document_id: Optional[int] = None

    # R2.3: whether a human has reviewed/confirmed this extracted
    # information, and by whom/when.
    confirmed: bool = False
    confirmed_by: Optional[str] = None
    confirmed_at: Optional[datetime] = None

    # R3.2: contradictions found between the intake form and this
    # structured profile, if any (empty when everything lines up, or
    # when a side has no value to compare).
    conflicts: List[ConsistencyConflict] = []

    # R2.3 acceptance criteria: when a user corrects an extracted value,
    # the original extracted value is retained -- this is it, for every
    # field whose current value differs from what the AI extracted.
    # Empty when the profile wasn't extracted from a document.
    original_values: dict[str, Any] = {}

    # P4 (R2.4): per-field provenance -- source document, version, page or
    # sheet, extraction date and method, a deterministically derived
    # confidence, and whether a person has confirmed it. Empty for profiles
    # created before P4 (their provenance was never recorded).
    field_provenance: dict[str, Any] = {}
    provenance_summary: dict[str, int] = {}
    extraction_method: Optional[str] = None
    extracted_at: Optional[datetime] = None
    source_document_ids: List[int] = []

    created_at: datetime

    class Config:
        from_attributes = True


class AssessmentIntelligenceUpdate(BaseModel):
    """
    R2.3/R3.4: corrects one or more structured-profile fields. Only the
    fields the caller actually sends are changed -- the original AI
    output stays intact in `raw_extraction` regardless, so "what the AI
    said" vs. "what was corrected to" are both always retrievable.
    Correcting a record clears its confirmed flag (a correction is not
    yet re-confirmed).

    R3.4 acceptance criteria: a change made *after* the profile was
    already validated must record who made it and why -- changed_by and
    change_reason are required in that case (enforced in the endpoint,
    not here, since whether they're required depends on the record's
    current confirmed state).
    """

    business_line: Optional[str] = None

    customer_type: Optional[str] = None
    transaction_origin: Optional[str] = None
    transaction_destination: Optional[str] = None
    transaction_frequency: Optional[str] = None
    payment_methods: Optional[List[str]] = None
    onboarding_approach: Optional[str] = None
    ownership_entity_structure: Optional[str] = None

    channels: Optional[List[str]] = None
    countries: Optional[List[str]] = None
    customer_segments: Optional[List[str]] = None
    transaction_volume: Optional[str] = None
    average_transaction_size: Optional[str] = None
    maximum_transaction_limit: Optional[str] = None
    third_party_vendors: Optional[List[str]] = None
    data_shared: Optional[List[str]] = None
    technologies: Optional[List[str]] = None
    regulatory_considerations: Optional[List[str]] = None
    existing_controls: Optional[List[str]] = None
    additional_risk_factors: Optional[List[str]] = None

    # R3.4: who is making this change, and (required only when correcting
    # an already-confirmed/validated profile) why.
    changed_by: Optional[str] = None
    change_reason: Optional[str] = None


class AssessmentIntelligenceConfirm(BaseModel):
    confirmed_by: Optional[str] = "System"

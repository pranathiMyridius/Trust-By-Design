from datetime import datetime
from typing import Optional

from pydantic import BaseModel, ConfigDict

from app.schemas.processing_job import ProcessingJobResponse

# R2.1: the document categories the intake/evidence-collection stages
# accept. "OTHER" is the default for uploads that don't specify one
# (e.g. older clients), so this list is additive, never a hard gate.
DOCUMENT_TYPES = [
    "BUSINESS_REQUIREMENT",
    "PRODUCT_SPECIFICATION",
    "PROCESS_MAP",
    "CUSTOMER_INFORMATION",
    "VENDOR_DOCUMENT",
    "CONTROL_DESCRIPTION",
    "REGULATORY_POLICY_REFERENCE",
    "PREVIOUS_ASSESSMENT",
    "OTHER",
]

# R2.2: confidentiality classifications documents can be tagged with.
CONFIDENTIALITY_LEVELS = [
    "PUBLIC",
    "INTERNAL",
    "CONFIDENTIAL",
    "RESTRICTED",
]


class AssessmentDocumentResponse(BaseModel):
    id: int
    assessment_id: int
    filename: str
    file_type: str
    extracted_text: str

    # R2.2: classification metadata.
    document_type: str
    document_owner: Optional[str] = None
    source: Optional[str] = None
    effective_date: Optional[str] = None
    expiry_date: Optional[str] = None
    confidentiality: Optional[str] = None

    # R2.6: versioning.
    version: int
    is_current: bool
    supersedes_id: Optional[int] = None
    superseded_at: Optional[datetime] = None

    created_at: datetime

    # Stage 19: extracted_text is masked (card numbers, e-mails, ...) for a
    # CONFIDENTIAL/RESTRICTED document the viewer isn't entitled to see raw.
    is_masked: bool = False

    # R15.3: whether the viewer may open the original file. False for a
    # CONFIDENTIAL/RESTRICTED document they only see masked, so the UI can
    # explain instead of offering an action that will be refused.
    can_open_original: bool = True

    # Stage 19: status of the latest background processing job for this
    # document (text extraction), so the UI can show progress, a clear
    # error with a retry action, or an "incomplete" marker.
    processing: Optional[ProcessingJobResponse] = None

    model_config = ConfigDict(from_attributes=True)

from typing import Optional

from pydantic import BaseModel


class EvidenceIssue(BaseModel):
    """
    R2.5: one missing-evidence/information-request item. `missing=True`
    means this is an open issue the reviewer still needs to resolve;
    `missing=False` rows are included too so the UI can show a full
    checklist rather than only ever showing problems.
    """

    code: str
    title: str
    description: str
    missing: bool


class DocumentWarning(BaseModel):
    """
    R2.6: a warning about a document currently flagged as evidence --
    expired, or superseded by a newer version but still is_current
    (shouldn't normally happen, but flagged defensively).
    """

    document_id: int
    filename: str
    reason: str
    # P4 (R2.6): EXPIRED | INVALID_EXPIRY_DATE; and ACKNOWLEDGEMENT_REQUIRED
    # | ACKNOWLEDGED_FOR_USE | EXCLUDED_FROM_EVIDENCE.
    document_version: Optional[int] = None
    expiry_date: Optional[str] = None
    condition: Optional[str] = None
    state: Optional[str] = None
    usable_as_evidence: bool = False
    acknowledgement: Optional[dict] = None


class EvidenceGapsResponse(BaseModel):
    assessment_id: int
    issues: list[EvidenceIssue]
    open_count: int
    document_warnings: list[DocumentWarning] = []
    # P4: expired documents still waiting for an acknowledgement; risk
    # identification refuses to run while this is non-zero.
    acknowledgements_required: int = 0


class EvidenceAcknowledgementCreate(BaseModel):
    """P4 (R2.6): USE_AS_EVIDENCE or EXCLUDE_FROM_EVIDENCE, with a reason."""

    decision: str
    reason: str

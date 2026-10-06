"""
R5.1-R5.5: the approved evidence-source library and evidence retrieval.

Library maintenance (create, edit a draft, approve, retire) is for a
Policy Admin or Admin. Searching, and attaching a passage to a risk
factor as evidence, use only APPROVED sources.
"""

from datetime import date, datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from pydantic import BaseModel, ConfigDict, field_validator
from sqlalchemy.orm import Session

from app.api.assessments import require_pipeline_role
from app.auth.dependencies import get_current_user, require_role
from app.database import get_db
from app.models.approved_source import ApprovedSource, SourceEvidenceLink
from app.models.assessment import Assessment
from app.models.risk_factor import RiskFactor
from app.models.user import User, UserRole
from app.services import source_governance as gov
from app.services import source_library
from app.services.audit_service import AuditAction, actor_name, log_audit_event
from app.services.decision_lock import ensure_assessment_editable

router = APIRouter(tags=["Evidence Sources"])

require_library_admin = require_role(UserRole.POLICY_ADMIN, UserRole.ADMIN)


def require_library_admin_user(user: User) -> None:
    if user.role not in {UserRole.POLICY_ADMIN.value, UserRole.ADMIN.value}:
        raise HTTPException(status_code=403, detail="You do not have permission to perform this action.")

LIBRARY_ADMIN_ROLES = {UserRole.POLICY_ADMIN.value, UserRole.ADMIN.value}


class SourceBase(BaseModel):
    title: str
    source_type: str
    issuer: Optional[str] = None
    version: str
    effective_date: Optional[date] = None
    review_date: Optional[date] = None
    reference: Optional[str] = None
    content: str

    @field_validator("source_type")
    @classmethod
    def type_known(cls, value: str) -> str:
        if value not in source_library.SOURCE_TYPES:
            raise ValueError(f"source_type must be one of: {', '.join(sorted(source_library.SOURCE_TYPES))}")
        return value

    @field_validator("title", "version", "content")
    @classmethod
    def required(cls, value: str) -> str:
        if not value or not value.strip():
            raise ValueError("This field is required.")
        return value.strip()


class SourceCreate(SourceBase):
    pass


class SourceUpdate(BaseModel):
    title: Optional[str] = None
    source_type: Optional[str] = None
    issuer: Optional[str] = None
    version: Optional[str] = None
    effective_date: Optional[date] = None
    review_date: Optional[date] = None
    reference: Optional[str] = None
    content: Optional[str] = None

    @field_validator("source_type")
    @classmethod
    def type_known(cls, value: Optional[str]) -> Optional[str]:
        if value is not None and value not in source_library.SOURCE_TYPES:
            raise ValueError(f"source_type must be one of: {', '.join(sorted(source_library.SOURCE_TYPES))}")
        return value


class ReasonBody(BaseModel):
    reason: str

    @field_validator("reason")
    @classmethod
    def reason_required(cls, value: str) -> str:
        if not value or not value.strip():
            raise ValueError("A reason is required.")
        return value.strip()


class SourceResponse(SourceBase):
    id: int
    source_type_label: str
    status: str
    outdated: bool
    # The original uploaded document, if the source was created from one
    # (the storage path is never exposed).
    has_file: bool = False
    original_filename: Optional[str] = None
    file_content_type: Optional[str] = None
    file_size: Optional[int] = None
    file_sha256: Optional[str] = None
    approved_by: Optional[str] = None
    approved_at: Optional[datetime] = None
    created_by: Optional[str] = None
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class SearchResult(BaseModel):
    source_id: int
    source_title: str
    source_type: str
    source_type_label: str
    issuer: Optional[str] = None
    source_version: str
    effective_date: Optional[date] = None
    review_date: Optional[date] = None
    reference: Optional[str] = None
    passage: str
    matched_terms: list[str]
    score: float
    outdated: bool
    retrieved_at: datetime


class EvidenceLinkCreate(BaseModel):
    source_id: int
    passage: str
    # P4 (Stage 5 AC): a source past its review date is attached only with
    # an explicit acknowledgement and a reason.
    acknowledge_outdated: bool = False
    acknowledgement_reason: Optional[str] = None

    @field_validator("passage")
    @classmethod
    def passage_required(cls, value: str) -> str:
        if not value or not value.strip():
            raise ValueError("The passage is required.")
        return value.strip()


class EvidenceLinkResponse(BaseModel):
    id: int
    assessment_id: int
    risk_factor_id: Optional[int] = None
    source_id: int
    source_title: str
    source_type: str
    source_version: str
    effective_date: Optional[date] = None
    reference: Optional[str] = None
    passage: str
    retrieved_at: datetime
    retrieved_by: Optional[str] = None
    # Whether the source has since passed its review date or been retired.
    outdated: bool = False
    source_status: Optional[str] = None
    # P4: outdated when attached, and the acknowledgement given then.
    outdated_at_attach: bool = False
    outdated_acknowledgement_reason: Optional[str] = None

    model_config = ConfigDict(from_attributes=True)


class FactorSourceEvidence(BaseModel):
    """R5.1/R5.5: what the library holds for one risk factor -- the
    passages attached as evidence and further suggestions. No attached
    or suggested evidence means the factor's conclusion is unsupported by
    approved sources."""

    risk_factor_id: int
    linked: list[EvidenceLinkResponse]
    suggestions: list[SearchResult]
    supported: bool


def _source_response(source: ApprovedSource) -> SourceResponse:
    return SourceResponse.model_validate(
        {
            **{column.name: getattr(source, column.name) for column in source.__table__.columns},
            "source_type_label": source_library.SOURCE_TYPES.get(source.source_type, source.source_type),
            "outdated": source_library.is_outdated(source),
            "has_file": bool(source.file_path),
        }
    )


def _source_or_404(db: Session, source_id: int) -> ApprovedSource:
    source = db.get(ApprovedSource, source_id)
    if source is None:
        raise HTTPException(status_code=404, detail="Source not found")
    return source


def _log(db: Session, user: User, details: str) -> None:
    log_audit_event(
        db=db,
        assessment_id=None,
        action=AuditAction.SOURCE_LIBRARY_CHANGED,
        actor=actor_name(user),
        actor_id=user.id,
        details=details,
    )


# -- the library ------------------------------------------------------------


@router.get("/api/sources", response_model=list[SourceResponse])
def list_sources(
    status: Optional[str] = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Approved sources for everyone; drafts and retired sources too for a
    Policy Admin or Admin."""

    query = db.query(ApprovedSource)
    if current_user.role not in LIBRARY_ADMIN_ROLES:
        query = query.filter(ApprovedSource.status == "APPROVED")
    elif status:
        query = query.filter(ApprovedSource.status == status)
    return [_source_response(source) for source in query.order_by(ApprovedSource.title, ApprovedSource.id).all()]


@router.post("/api/sources", response_model=SourceResponse, status_code=201)
def create_source(
    payload: SourceCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_library_admin),
):
    source = ApprovedSource(**payload.model_dump(), status="DRAFT", created_by=actor_name(current_user))
    db.add(source)
    db.flush()
    _register_governed(db, current_user, source)
    _log(db, current_user, f"Source '{source.title}' v{source.version} added as a draft.")
    db.commit()
    db.refresh(source)
    return _source_response(source)


@router.patch("/api/sources/{source_id}", response_model=SourceResponse)
def update_source(
    source_id: int,
    payload: SourceUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_library_admin),
):
    source = _source_or_404(db, source_id)
    if source.status != "DRAFT":
        # Evidence already attached quotes this exact version.
        raise HTTPException(
            status_code=409,
            detail="Only a draft can be edited. Add the revision as a new source version and retire this one.",
        )
    changes = payload.model_dump(exclude_unset=True)
    for field, value in changes.items():
        if field in {"title", "version", "content"} and not (value or "").strip():
            raise HTTPException(status_code=422, detail=f"{field} can't be blank.")
        setattr(source, field, value.strip() if isinstance(value, str) else value)
    db.flush()
    if source.source_type != source_library.GUIDE_TYPE:
        gov.sync_from_legacy(db, current_user, source)
    _log(db, current_user, f"Draft source '{source.title}' edited ({', '.join(sorted(changes)) or 'nothing'}).")
    db.commit()
    db.refresh(source)
    return _source_response(source)


class SourceExtraction(BaseModel):
    filename: str
    title: str
    content: str
    characters: int


SOURCE_UPLOAD_EXTENSIONS = {".pdf", ".docx", ".doc", ".xlsx", ".txt", ".csv"}


@router.post("/api/sources/extract", response_model=SourceExtraction)
async def extract_source_file(
    file: UploadFile = File(...),
    current_user: User = Depends(require_library_admin),
):
    """
    R5.1: read the text of an uploaded policy, procedure or guidance
    document so it can be reviewed and saved as a draft source (the same
    create -> approve flow as pasted text). Nothing is stored here; the
    file's text is returned for the form.
    """

    from pathlib import Path

    from app.api.assessments import ensure_upload_size
    from app.file_processing.extractor import extract_text

    filename = Path((file.filename or "").replace("\\", "/")).name
    if Path(filename).suffix.lower() not in SOURCE_UPLOAD_EXTENSIONS:
        raise HTTPException(status_code=400, detail="Upload a PDF, DOCX, DOC, XLSX, TXT or CSV file.")
    data = await file.read()
    if not data:
        raise HTTPException(status_code=400, detail="The uploaded file is empty.")
    ensure_upload_size(filename, data)
    try:
        text = extract_text(filename=filename, file_content=data)
    except Exception:  # noqa: BLE001 -- parser errors vary by format
        raise HTTPException(status_code=422, detail=f"The text of {filename} couldn't be read. Try another format.")
    text = (text or "").strip()
    if not text:
        raise HTTPException(
            status_code=422,
            detail=f"No readable text was found in {filename} (a scanned image has no text layer).",
        )
    title = Path(filename).stem.replace("_", " ").replace("-", " ").strip() or filename
    return SourceExtraction(filename=filename, title=title, content=text, characters=len(text))


_SOURCE_MIME_TYPES = {
    ".pdf": "application/pdf",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".doc": "application/msword",
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ".txt": "text/plain",
    ".csv": "text/csv",
}


@router.post("/api/sources/with-file", response_model=SourceResponse, status_code=201)
async def create_source_with_file(
    title: str = Form(...),
    source_type: str = Form(...),
    version: str = Form(...),
    content: str = Form(...),
    issuer: Optional[str] = Form(None),
    effective_date: Optional[date] = Form(None),
    review_date: Optional[date] = Form(None),
    reference: Optional[str] = Form(None),
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    current_user: User = Depends(require_library_admin),
):
    """
    R5.1/R5.3: a source created from an uploaded document. The
    reviewed text is the searchable content; the original file is kept with
    it (encrypted at rest, with its SHA-256) and never replaced -- a revised
    document is a new source version.
    """

    import hashlib
    from pathlib import Path

    from pydantic import ValidationError

    from app.api.assessments import ensure_upload_size
    from app.file_processing.storage import save_source_file

    try:
        fields = SourceCreate(
            title=title, source_type=source_type, version=version, content=content,
            issuer=(issuer or "").strip() or None, effective_date=effective_date,
            review_date=review_date, reference=(reference or "").strip() or None,
        )
    except ValidationError as exc:
        raise HTTPException(status_code=422, detail=exc.errors(include_url=False, include_context=False))

    filename = Path((file.filename or "").replace("\\", "/")).name
    extension = Path(filename).suffix.lower()
    if extension not in SOURCE_UPLOAD_EXTENSIONS:
        raise HTTPException(status_code=400, detail="Upload a PDF, DOCX, DOC, XLSX, TXT or CSV file.")
    data = await file.read()
    if not data:
        raise HTTPException(status_code=400, detail="The uploaded file is empty.")
    ensure_upload_size(filename, data)

    # A help article for workbench users (USER_GUIDE) is published at once --
    # it is never risk evidence. Every other type (policy, procedure,
    # regulatory guidance ...) is a draft that an authorised compliance
    # reviewer must approve before it can be used (Source Library workflow).
    publish_now = fields.source_type == source_library.GUIDE_TYPE
    approval = (
        {
            "status": "APPROVED",
            "approved_by": actor_name(current_user),
            "approved_by_id": current_user.id,
            "approved_at": datetime.now(timezone.utc),
        }
        if publish_now
        else {"status": "DRAFT"}
    )
    source = ApprovedSource(
        **fields.model_dump(),
        **approval,
        created_by=actor_name(current_user),
        original_filename=filename,
        file_path=save_source_file(filename, data),
        file_content_type=_SOURCE_MIME_TYPES[extension],
        file_size=len(data),
        file_sha256=hashlib.sha256(data).hexdigest(),
    )
    db.add(source)
    db.flush()
    _register_governed(db, current_user, source)
    _log(
        db, current_user,
        (
            f"Help article '{source.title}' v{source.version} uploaded from {filename} and published as approved "
            if publish_now
            else f"Source '{source.title}' v{source.version} added as a draft from {filename} "
        )
        + f"({len(data):,} bytes, sha256 {source.file_sha256[:12]}…).",
    )
    db.commit()
    db.refresh(source)
    return _source_response(source)


@router.get("/api/sources/{source_id}/file")
def download_source_file(
    source_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """The original document of a source, as an attachment. Visible to the
    same people as the source (approved: everyone signed in; drafts and
    retired: library admins). Every download is audited."""

    from fastapi.responses import Response

    from app.file_processing.storage import content_disposition, read_file

    source = db.get(ApprovedSource, source_id)
    if source is None or (source.status != "APPROVED" and current_user.role not in LIBRARY_ADMIN_ROLES):
        raise HTTPException(status_code=404, detail="Source not found")
    if not source.file_path:
        raise HTTPException(status_code=404, detail="This source has no original document (its text was pasted).")
    try:
        data = read_file(source.file_path)
    except FileNotFoundError:
        raise HTTPException(status_code=410, detail="The original document is no longer in storage.")

    log_audit_event(
        db=db,
        assessment_id=None,
        action=AuditAction.DOCUMENT_DOWNLOADED,
        actor=actor_name(current_user),
        actor_id=current_user.id,
        details=f"Source library document downloaded: source #{source.id} '{source.title}' v{source.version} ({source.original_filename}).",
    )
    db.commit()
    return Response(
        content=data,
        media_type=source.file_content_type or "application/octet-stream",
        headers={"Content-Disposition": content_disposition("attachment", source.original_filename or "document")},
    )


def _register_governed(db: Session, user: User, source: ApprovedSource) -> None:
    """Regulatory and policy sources created through this API are governed by
    the Source Library workflow. Help articles for workbench users
    (USER_GUIDE) are not regulatory sources, are never used as risk evidence,
    and keep the earlier behaviour."""

    if source.source_type != source_library.GUIDE_TYPE:
        gov.register_legacy(db, user, source)


def _governed_version(db: Session, source: ApprovedSource, user: User):
    """The record/version a legacy source is governed through (created on
    first use for a source that predates the Source Library module)."""

    version = gov.version_for_legacy(db, source)
    if version is None:
        record, version = gov.register_legacy(db, user, source)
        db.flush()
    return version.record, version


@router.post("/api/sources/{source_id}/approve", response_model=SourceResponse)
def approve_source(
    source_id: int,
    payload: ReasonBody,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """R5.2: only an approved source is used as formal evidence. Goes through
    the Source Library workflow: only an authorised compliance reviewer may
    approve, never someone who prepared or submitted the source."""

    source = _source_or_404(db, source_id)
    if source.source_type == source_library.GUIDE_TYPE:
        require_library_admin_user(current_user)
        if source.status != "DRAFT":
            raise HTTPException(status_code=409, detail=f"This source is {source.status}; only a draft can be approved.")
        source.status = "APPROVED"
        source.approved_by = actor_name(current_user)
        source.approved_by_id = current_user.id
        source.approved_at = datetime.now(timezone.utc)
        _log(db, current_user, f"Help article '{source.title}' v{source.version} approved. Reason: {payload.reason}")
        db.commit()
        db.refresh(source)
        return _source_response(source)
    gov.require_reviewer(current_user)
    if source.status != "DRAFT":
        raise HTTPException(status_code=409, detail=f"This source is {source.status}; only a draft can be approved.")
    record, version = _governed_version(db, source, current_user)
    if version.status == "DRAFT":
        gov.implicit_submit(db, record, version)
    gov.approve(db, current_user, record, version, payload.reason, min_length=1)
    db.commit()
    db.refresh(source)
    return _source_response(source)


@router.post("/api/sources/{source_id}/retire", response_model=SourceResponse)
def retire_source(
    source_id: int,
    payload: ReasonBody,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_library_admin),
):
    source = _source_or_404(db, source_id)
    if source.status == "RETIRED":
        raise HTTPException(status_code=409, detail="This source is already retired.")
    if source.source_type != source_library.GUIDE_TYPE:
        record, version = _governed_version(db, source, current_user)
        gov.retire_version(db, current_user, record, version, payload.reason, min_length=1)
    _log(db, current_user, f"Source '{source.title}' v{source.version} retired. Reason: {payload.reason}")
    source.status = "RETIRED"
    db.commit()
    db.refresh(source)
    return _source_response(source)


@router.get("/api/sources/search", response_model=list[SearchResult])
def search_sources(
    q: str,
    limit: int = 10,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """R5.1: keyword search over approved sources."""

    return source_library.search(db, q, limit=max(1, min(limit, 50)))


# -- evidence for a risk factor ----------------------------------------------


def _factor_or_404(db: Session, assessment_id: int, risk_factor_id: int) -> RiskFactor:
    factor = (
        db.query(RiskFactor)
        .filter(RiskFactor.id == risk_factor_id, RiskFactor.assessment_id == assessment_id)
        .first()
    )
    if factor is None:
        raise HTTPException(status_code=404, detail="Risk factor not found")
    return factor


def _link_response(db: Session, link: SourceEvidenceLink) -> EvidenceLinkResponse:
    source = db.get(ApprovedSource, link.source_id)
    return EvidenceLinkResponse.model_validate(
        {
            **{column.name: getattr(link, column.name) for column in link.__table__.columns},
            "outdated": bool(source and (source_library.is_outdated(source) or source.status == "RETIRED")),
            "source_status": source.status if source else None,
        }
    )


@router.get(
    "/api/assessments/{assessment_id}/risk-factors/{risk_factor_id}/source-evidence",
    response_model=FactorSourceEvidence,
)
def factor_source_evidence(
    assessment_id: int,
    risk_factor_id: int,
    db: Session = Depends(get_db),
):
    factor = _factor_or_404(db, assessment_id, risk_factor_id)
    links = (
        db.query(SourceEvidenceLink)
        .filter(SourceEvidenceLink.assessment_id == assessment_id, SourceEvidenceLink.risk_factor_id == risk_factor_id)
        .order_by(SourceEvidenceLink.id.asc())
        .all()
    )
    linked_passages = {(link.source_id, link.passage) for link in links}
    suggestions = [
        row for row in source_library.search_for_factor(db, factor) if (row["source_id"], row["passage"]) not in linked_passages
    ]
    return FactorSourceEvidence(
        risk_factor_id=risk_factor_id,
        linked=[_link_response(db, link) for link in links],
        suggestions=suggestions,
        supported=bool(links),
    )


@router.post(
    "/api/assessments/{assessment_id}/risk-factors/{risk_factor_id}/source-evidence",
    response_model=EvidenceLinkResponse,
    status_code=201,
)
def attach_source_evidence(
    assessment_id: int,
    risk_factor_id: int,
    payload: EvidenceLinkCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_pipeline_role),
):
    """R5.3: attach a passage from an approved source as evidence for this
    risk factor, frozen with its source details and retrieval time."""

    assessment = db.get(Assessment, assessment_id)
    if assessment is None:
        raise HTTPException(status_code=404, detail="Assessment not found")
    ensure_assessment_editable(assessment)
    factor = _factor_or_404(db, assessment_id, risk_factor_id)

    source = _source_or_404(db, payload.source_id)
    if source.status != "APPROVED":
        raise HTTPException(status_code=409, detail="Only an approved source can be used as evidence (R5.2).")
    if payload.passage not in " ".join(source.content.split()) and payload.passage not in source.content:
        raise HTTPException(status_code=422, detail="The passage must be quoted exactly from the source.")

    outdated = source_library.is_outdated(source)
    ack_reason = (payload.acknowledgement_reason or "").strip()
    if outdated and not (payload.acknowledge_outdated and len(ack_reason) >= 10):
        raise HTTPException(
            status_code=409,
            detail={
                "code": "OUTDATED_SOURCE_UNACKNOWLEDGED",
                "message": (
                    f"'{source.title}' v{source.version} is past its review date ({source.review_date}). "
                    "Acknowledge that it is outdated and give a reason (at least 10 characters) to use it as evidence."
                ),
            },
        )

    link = SourceEvidenceLink(
        assessment_id=assessment_id,
        risk_factor_id=factor.id,
        source_id=source.id,
        source_title=source.title,
        source_type=source.source_type,
        source_version=source.version,
        effective_date=source.effective_date,
        reference=source.reference,
        passage=payload.passage,
        retrieved_by=actor_name(current_user),
        retrieved_by_id=current_user.id,
        outdated_at_attach=outdated,
        outdated_acknowledgement_reason=ack_reason if outdated else None,
        outdated_acknowledged_by_id=current_user.id if outdated else None,
    )
    db.add(link)
    log_audit_event(
        db=db,
        assessment_id=assessment_id,
        action=AuditAction.SOURCE_LIBRARY_CHANGED,
        actor=actor_name(current_user),
        actor_id=current_user.id,
        details=(
            f"Evidence attached to {factor.category}: '{source.title}' v{source.version}"
            + (f" (past its review date; acknowledged: {ack_reason})" if outdated else "")
            + "."
        ),
    )
    db.commit()
    db.refresh(link)
    return _link_response(db, link)

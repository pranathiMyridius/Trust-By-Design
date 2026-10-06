"""
Source Library API: governed regulatory and internal-policy sources.

    /api/source-library/meta                         enums, limits, caller's capabilities
    /api/source-library/records                      list/search/filter, create
    /api/source-library/records/{id}                 detail, update, retire
    /api/source-library/records/{id}/versions        add a version (link and/or PDF)
    .../versions/{vid}                               detail, edit a draft, file, reprocess,
                                                     submit, withdraw, approve, reject, retire
    .../records/{id}/approvals | /audit              approval history, audit timeline
    /api/source-library/approved | /passages         what risk assessments may use

Business rules live in app/services/source_governance.py; this module
validates input, applies visibility, and shapes responses. Everything
that changes anything is audited there.
"""

from __future__ import annotations

import logging
import uuid
from datetime import date, datetime
from typing import Any, Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Response, UploadFile
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import and_, exists, func, or_, String, cast
from sqlalchemy.orm import Session

from app.auth.dependencies import get_current_user
from app.database import get_db
from app.file_processing import malware_scan
from app.file_processing.storage import content_disposition, delete_library_file, read_library_file, save_library_file
from app.governance import policy
from app.models.source_library import (
    SourceApproval,
    SourceAuditLog,
    SourceRecord,
    SourceVersion,
)
from app.models.user import User
from app.services import source_documents, source_governance as gov, source_retrieval
from app.services.audit_service import AuditAction, actor_name, log_audit_event
from app.services.source_documents import DocumentError

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/source-library", tags=["Source Library"])

MAX_PAGE_SIZE = 100


# -- schemas -----------------------------------------------------------------------


class VersionOut(BaseModel):
    id: uuid.UUID
    version_number: int
    version_label: str
    status: str
    effective_date: Optional[date] = None
    review_date: Optional[date] = None
    retrieved_date: Optional[date] = None
    source_url: Optional[str] = None
    change_summary: Optional[str] = None
    has_file: bool = False
    original_filename: Optional[str] = None
    file_size: Optional[int] = None
    file_sha256: Optional[str] = None
    page_count: Optional[int] = None
    processing_status: str
    processing_error: Optional[str] = None
    scan_status: str
    scan_detail: Optional[str] = None
    chunk_count: int
    embedding_status: str
    outdated: bool = False
    created_by: Optional[str] = None
    created_at: datetime
    submitted_by: Optional[str] = None
    submitted_at: Optional[datetime] = None
    decided_by: Optional[str] = None
    decided_at: Optional[datetime] = None
    decision_comment: Optional[str] = None
    superseded_at: Optional[datetime] = None
    migrated: bool = False

    model_config = ConfigDict(from_attributes=True)


class RecordOut(BaseModel):
    id: uuid.UUID
    source_code: str
    title: str
    authority: str
    category: str
    category_label: str
    jurisdiction: Optional[str] = None
    applicable_entity: Optional[str] = None
    source_url: Optional[str] = None
    description: Optional[str] = None
    topics: list[str] = []
    owner: Optional[str] = None
    review_frequency: Optional[str] = None
    status: str
    library_status: str
    outdated: bool = False
    version_count: int = 0
    current_version: Optional[VersionOut] = None
    pending_version: Optional[VersionOut] = None
    created_by: Optional[str] = None
    created_at: datetime
    updated_at: datetime


class RecordDetail(RecordOut):
    versions: list[VersionOut] = []


class RecordPage(BaseModel):
    items: list[RecordOut]
    total: int
    page: int
    page_size: int
    summary: dict[str, int]
    jurisdictions: list[str]
    authorities: list[str]


class RecordPatch(BaseModel):
    title: Optional[str] = None
    authority: Optional[str] = None
    category: Optional[str] = None
    jurisdiction: Optional[str] = None
    applicable_entity: Optional[str] = None
    source_url: Optional[str] = None
    description: Optional[str] = None
    topics: Optional[list[str]] = None
    owner: Optional[str] = None
    review_frequency: Optional[str] = None
    # Required (and recorded) when category/jurisdiction/topics change on a
    # source that has an approved version in use.
    change_reason: Optional[str] = None

    @field_validator("source_url")
    @classmethod
    def url_ok(cls, value: Optional[str]) -> Optional[str]:
        return _clean_url(value)


class VersionPatch(BaseModel):
    version_label: Optional[str] = None
    effective_date: Optional[date] = None
    review_date: Optional[date] = None
    retrieved_date: Optional[date] = None
    source_url: Optional[str] = None
    change_summary: Optional[str] = None

    @field_validator("source_url")
    @classmethod
    def url_ok(cls, value: Optional[str]) -> Optional[str]:
        return _clean_url(value)


class CommentBody(BaseModel):
    comment: str = Field(default="")


class ReasonBody(BaseModel):
    reason: str = Field(default="")


class ApprovalOut(BaseModel):
    id: uuid.UUID
    version_id: uuid.UUID
    version_label: Optional[str] = None
    version_number: Optional[int] = None
    action: str
    from_status: Optional[str] = None
    to_status: str
    actor: str
    comment: Optional[str] = None
    created_at: datetime


class AuditOut(BaseModel):
    id: uuid.UUID
    version_id: Optional[uuid.UUID] = None
    version_label: Optional[str] = None
    event_type: str
    actor: str
    details: Optional[str] = None
    event_data: Optional[dict[str, Any]] = None
    created_at: datetime


class AuditPage(BaseModel):
    items: list[AuditOut]
    total: int
    page: int
    page_size: int


class ChunkOut(BaseModel):
    id: uuid.UUID
    chunk_index: int
    page_start: Optional[int] = None
    page_end: Optional[int] = None
    section: Optional[str] = None
    text: str

    model_config = ConfigDict(from_attributes=True)


class ChunkPage(BaseModel):
    items: list[ChunkOut]
    total: int
    page: int
    page_size: int


class PassageOut(BaseModel):
    chunk_id: str
    record_id: str
    version_id: str
    source_code: str
    title: str
    authority: str
    category: str
    jurisdiction: Optional[str] = None
    version_label: str
    effective_date: Optional[str] = None
    review_date: Optional[str] = None
    outdated: bool
    source_url: Optional[str] = None
    page_start: Optional[int] = None
    page_end: Optional[int] = None
    section: Optional[str] = None
    location: str
    score: float
    method: str
    text: str


# -- helpers ---------------------------------------------------------------------------


def _clean_url(value: Optional[str]) -> Optional[str]:
    if value is None:
        return None
    text = value.strip()
    if not text:
        return None
    if len(text) > 1000 or not text.lower().startswith(("http://", "https://")) or any(c.isspace() for c in text):
        raise ValueError("Enter a valid web address starting with http:// or https://")
    return text


def _url_or_400(value: Optional[str]) -> Optional[str]:
    try:
        return _clean_url(value)
    except ValueError as exc:
        raise gov.fail(422, "INVALID_URL", str(exc))


def _split_topics(raw: Optional[str]) -> list[str]:
    if not raw:
        return []
    text = raw.strip()
    if text.startswith("["):
        import json

        try:
            parsed = json.loads(text)
            if isinstance(parsed, list):
                return [str(item) for item in parsed]
        except ValueError:
            pass
    return [part for part in text.replace("\n", ",").replace(";", ",").split(",") if part.strip()]


def _version_out(version: SourceVersion) -> VersionOut:
    return VersionOut.model_validate(
        {
            **{column.name: getattr(version, column.name) for column in version.__table__.columns},
            "has_file": bool(version.storage_key or (version.legacy_source_id and version.original_filename)),
            "outdated": version.status == "APPROVED" and gov.is_outdated(version),
            "migrated": bool(version.legacy_source_id) and not version.storage_key,
        }
    )


def _library_status(record: SourceRecord, versions: list[SourceVersion]) -> str:
    if any(v.status == "APPROVED" for v in versions):
        return "APPROVED"
    if record.status == "RETIRED":
        return "RETIRED"
    if not versions:
        return "DRAFT"
    latest = max(versions, key=lambda v: v.version_number)
    return latest.status


def _record_out(record: SourceRecord, user: User, *, detail: bool = False) -> RecordOut | RecordDetail:
    all_versions = sorted(record.versions, key=lambda v: v.version_number)
    full = gov.can_view_all(user)
    versions = all_versions if full else [v for v in all_versions if v.status == "APPROVED"]
    current = next((v for v in all_versions if v.status == "APPROVED"), None)
    pending = next((v for v in reversed(all_versions) if v.status in {"DRAFT", "IN_REVIEW", "REJECTED"}), None) if full else None
    base = {
        **{column.name: getattr(record, column.name) for column in record.__table__.columns},
        "topics": list(record.topics or []),
        "category_label": gov.CATEGORIES.get(record.category, record.category),
        "library_status": _library_status(record, all_versions) if full else "APPROVED",
        "outdated": bool(current and gov.is_outdated(current)),
        "version_count": len(versions),
        "current_version": _version_out(current) if current else None,
        "pending_version": _version_out(pending) if pending else None,
    }
    if detail:
        return RecordDetail.model_validate({**base, "versions": [_version_out(v) for v in reversed(versions)]})
    return RecordOut.model_validate(base)


def _visible_record(db: Session, user: User, record_id: uuid.UUID) -> SourceRecord:
    record = gov.get_record(db, record_id)
    if not gov.can_view_all(user):
        if record.status != "ACTIVE" or gov.approved_version(db, record) is None:
            raise gov.fail(404, "SOURCE_NOT_FOUND", "Source not found.")
    return record


def _require_view_all(user: User) -> None:
    if not gov.can_view_all(user):
        raise gov.fail(403, "FORBIDDEN", "You do not have access to this part of the Source Library.")


def _commit(db: Session) -> None:
    try:
        db.commit()
    except Exception:
        db.rollback()
        logger.exception("Source Library commit failed")
        raise


async def _read_upload(file: UploadFile) -> bytes:
    # One byte over the limit is enough to know it is too big.
    return await file.read(source_documents.MAX_UPLOAD_BYTES + 1)


def _document_error(exc: DocumentError):
    return gov.fail(exc.status_code, exc.code, exc.message)


def _check_duplicate(db: Session, sha256: str, exclude_version: uuid.UUID | None) -> None:
    query = (
        db.query(SourceVersion, SourceRecord)
        .join(SourceRecord, SourceRecord.id == SourceVersion.record_id)
        .filter(SourceVersion.file_sha256 == sha256, SourceVersion.status.in_(("DRAFT", "IN_REVIEW", "APPROVED")))
    )
    if exclude_version is not None:
        query = query.filter(SourceVersion.id != exclude_version)
    match = query.first()
    if match is not None:
        version, record = match
        raise gov.fail(
            409,
            "DUPLICATE_DOCUMENT",
            f"This exact document is already in the library: {record.source_code} '{record.title}', "
            f"version '{version.version_label}' ({version.status.replace('_', ' ').lower()}).",
        )


class _Accepted(BaseModel):
    filename: str
    data: bytes
    sha256: str
    scan_status: str
    scan_detail: str
    content_type: Optional[str] = None


async def _accept_pdf(
    db: Session,
    user: User,
    file: UploadFile,
    record: SourceRecord | None = None,
    version: SourceVersion | None = None,
) -> _Accepted:
    """Validate, de-duplicate and malware-scan an uploaded PDF. Raises a
    structured HTTP error (after recording the refusal in the audit trail)
    if it can't be accepted. Call it BEFORE creating anything the upload
    belongs to: the refusal is committed on the spot."""

    data = await _read_upload(file)
    try:
        filename = source_documents.validate_pdf_upload(file.filename or "", file.content_type, data)
    except DocumentError as exc:
        _audit_rejected_upload(db, user, record, version, file.filename or "", exc.code, exc.message)
        raise _document_error(exc)
    sha = source_documents.sha256_hex(data)
    try:
        _check_duplicate(db, sha, version.id if version else None)
    except Exception as exc:  # the structured HTTPException from fail()
        _audit_rejected_upload(db, user, record, version, filename, "DUPLICATE_DOCUMENT", "identical document already in the library")
        raise exc
    scan = malware_scan.scan_bytes(data)
    if not scan.clean:
        _audit_rejected_upload(db, user, record, version, filename, f"SCAN_{scan.status}", scan.detail)
        if scan.status == malware_scan.INFECTED:
            raise gov.fail(422, "MALWARE_DETECTED", f"{filename} was rejected by malware scanning ({scan.detail}).")
        raise gov.fail(503, "SCAN_UNAVAILABLE", f"{filename} could not be scanned for malware ({scan.detail}); it was not stored.")
    return _Accepted(filename=filename, data=data, sha256=sha, scan_status=scan.status, scan_detail=scan.detail,
                     content_type=file.content_type)


def _store_accepted(db: Session, user: User, record: SourceRecord, version: SourceVersion, accepted: _Accepted) -> None:
    key = save_library_file(accepted.data)
    try:
        gov.attach_file(db, user, record, version, filename=accepted.filename, content_type=accepted.content_type,
                        data=accepted.data, storage_key=key, scan_status=accepted.scan_status,
                        scan_detail=accepted.scan_detail, sha256=accepted.sha256)
    except Exception:
        delete_library_file(key)
        raise


def _audit_rejected_upload(
    db: Session, user: User, record: SourceRecord | None, version: SourceVersion | None, filename: str, code: str, why: str
) -> None:
    """Committed immediately: the request is about to fail, and a refused
    upload (especially a malware hit) must stay on record."""

    try:
        gov.audit(db, user, "UPLOAD_REJECTED", record=record, version=version,
                  details=f"Upload '{filename[:120]}' rejected ({code}): {why}", data={"code": code})
        db.commit()
    except Exception:  # noqa: BLE001 -- never mask the real error
        db.rollback()
        logger.exception("Could not record a rejected upload")


# -- meta ---------------------------------------------------------------------------------


@router.get("/meta")
def library_meta(current_user: User = Depends(get_current_user)):
    rules = policy.policy()["source_library"]
    return {
        "categories": [{"value": key, "label": label} for key, label in gov.CATEGORIES.items()],
        "version_statuses": gov.VERSION_STATUSES,
        "max_upload_mb": source_documents.MAX_UPLOAD_BYTES // (1024 * 1024),
        "accepted_types": [".pdf"],
        "home_jurisdiction": rules.get("home_jurisdiction"),
        "min_comment_length": gov.min_comment_length(),
        "reviewer_rule": policy.describe(rules["reviewers"]),
        "policy_status": policy.POLICY_STATUS,
        "malware_scan_required": malware_scan.scan_required(),
        "antivirus_configured": malware_scan.clamav_configured(),
        "can_maintain": gov.can_maintain(current_user),
        "can_review": gov.can_review(current_user),
        "can_view_all": gov.can_view_all(current_user),
        "user_id": current_user.id,
    }


# -- records ---------------------------------------------------------------------------------


def _approved_exists():
    return exists().where(and_(SourceVersion.record_id == SourceRecord.id, SourceVersion.status == "APPROVED"))


def _has_status(status: str):
    return exists().where(and_(SourceVersion.record_id == SourceRecord.id, SourceVersion.status == status))


def _filtered_query(db: Session, user: User, q, category, jurisdiction, authority, topic, status):
    query = db.query(SourceRecord)
    if not gov.can_view_all(user):
        query = query.filter(SourceRecord.status == "ACTIVE", _approved_exists())
    if q:
        needle = f"%{q.strip().lower()}%"
        query = query.filter(
            or_(
                func.lower(SourceRecord.title).like(needle),
                func.lower(SourceRecord.source_code).like(needle),
                func.lower(SourceRecord.authority).like(needle),
                func.lower(func.coalesce(SourceRecord.owner, "")).like(needle),
                func.lower(func.coalesce(SourceRecord.description, "")).like(needle),
                func.lower(cast(SourceRecord.topics, String)).like(needle),
            )
        )
    if category:
        query = query.filter(SourceRecord.category == category)
    if jurisdiction:
        query = query.filter(func.lower(SourceRecord.jurisdiction) == jurisdiction.strip().lower())
    if authority:
        query = query.filter(func.lower(SourceRecord.authority) == authority.strip().lower())
    if topic:
        query = query.filter(func.lower(cast(SourceRecord.topics, String)).like(f'%"{topic.strip().lower()}"%'))
    if status:
        status = status.upper()
        if status == "APPROVED":
            query = query.filter(_approved_exists(), SourceRecord.status == "ACTIVE")
        elif status == "RETIRED":
            query = query.filter(SourceRecord.status == "RETIRED")
        elif status == "OUTDATED":
            query = query.filter(
                exists().where(
                    and_(
                        SourceVersion.record_id == SourceRecord.id,
                        SourceVersion.status == "APPROVED",
                        SourceVersion.review_date.isnot(None),
                        SourceVersion.review_date < date.today(),
                    )
                )
            )
        elif status in {"DRAFT", "IN_REVIEW", "REJECTED"}:
            query = query.filter(SourceRecord.status == "ACTIVE", _has_status(status))
        else:
            raise gov.fail(422, "INVALID_STATUS", "Unknown status filter.")
    return query


@router.get("/records", response_model=RecordPage)
def list_records(
    q: Optional[str] = None,
    status: Optional[str] = None,
    category: Optional[str] = None,
    jurisdiction: Optional[str] = None,
    authority: Optional[str] = None,
    topic: Optional[str] = None,
    sort: str = Query("title", pattern="^(title|updated|code|review)$"),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=MAX_PAGE_SIZE),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    query = _filtered_query(db, current_user, q, category, jurisdiction, authority, topic, status)
    total = query.count()
    order = {
        "title": [func.lower(SourceRecord.title)],
        "updated": [SourceRecord.updated_at.desc()],
        "code": [SourceRecord.source_code],
        "review": [SourceRecord.updated_at.desc()],
    }[sort]
    rows = query.order_by(*order, SourceRecord.source_code).offset((page - 1) * page_size).limit(page_size).all()

    base = _filtered_query(db, current_user, None, None, None, None, None, None)
    summary = {
        "total": base.count(),
        "approved": _filtered_query(db, current_user, None, None, None, None, None, "APPROVED").count(),
        "in_review": _filtered_query(db, current_user, None, None, None, None, None, "IN_REVIEW").count() if gov.can_view_all(current_user) else 0,
        "draft": _filtered_query(db, current_user, None, None, None, None, None, "DRAFT").count() if gov.can_view_all(current_user) else 0,
        "retired": _filtered_query(db, current_user, None, None, None, None, None, "RETIRED").count() if gov.can_view_all(current_user) else 0,
        "outdated": _filtered_query(db, current_user, None, None, None, None, None, "OUTDATED").count(),
    }
    jurisdictions = sorted({r[0] for r in base.with_entities(SourceRecord.jurisdiction).distinct().all() if r[0]})
    authorities = sorted({r[0] for r in base.with_entities(SourceRecord.authority).distinct().all() if r[0]})
    return RecordPage(
        items=[_record_out(record, current_user) for record in rows],
        total=total,
        page=page,
        page_size=page_size,
        summary=summary,
        jurisdictions=jurisdictions,
        authorities=authorities,
    )


@router.post("/records", response_model=RecordDetail, status_code=201)
async def create_record(
    title: str = Form(...),
    authority: str = Form(...),
    category: str = Form(...),
    jurisdiction: Optional[str] = Form(None),
    applicable_entity: Optional[str] = Form(None),
    source_url: Optional[str] = Form(None),
    description: Optional[str] = Form(None),
    topics: Optional[str] = Form(None),
    owner: Optional[str] = Form(None),
    review_frequency: Optional[str] = Form(None),
    source_code: Optional[str] = Form(None),
    version_label: str = Form(...),
    effective_date: Optional[date] = Form(None),
    review_date: Optional[date] = Form(None),
    retrieved_date: Optional[date] = Form(None),
    change_summary: Optional[str] = Form(None),
    version_source_url: Optional[str] = Form(None),
    file: Optional[UploadFile] = File(None),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """A new source with its first version, as a draft. Optionally with an
    official URL and/or an uploaded PDF. Nothing is usable until reviewed."""

    gov.require_maintainer(current_user)
    for label, value in (("title", title), ("authority", authority), ("version_label", version_label)):
        if not value or not value.strip():
            raise gov.fail(422, "REQUIRED_FIELD", f"{label.replace('_', ' ').capitalize()} is required.")
    url, version_url = _url_or_400(source_url), _url_or_400(version_source_url)
    # A refused upload must not leave an empty source behind, so the file is
    # checked before anything is created.
    accepted = await _accept_pdf(db, current_user, file) if file is not None and file.filename else None
    record = gov.create_record(db, current_user, {
        "title": title, "authority": authority, "category": category, "jurisdiction": jurisdiction,
        "applicable_entity": applicable_entity, "source_url": url, "description": description,
        "topics": _split_topics(topics), "owner": owner, "review_frequency": review_frequency, "source_code": source_code,
    })
    version = gov.create_version(db, current_user, record, {
        "version_label": version_label, "effective_date": effective_date, "review_date": review_date,
        "retrieved_date": retrieved_date, "change_summary": change_summary,
        "source_url": version_url,
    })
    if accepted is not None:
        _store_accepted(db, current_user, record, version, accepted)
    _commit(db)
    db.refresh(record)
    return _record_out(record, current_user, detail=True)


@router.get("/records/{record_id}", response_model=RecordDetail)
def get_record(record_id: uuid.UUID, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    return _record_out(_visible_record(db, current_user, record_id), current_user, detail=True)


@router.patch("/records/{record_id}", response_model=RecordDetail)
def update_record(
    record_id: uuid.UUID,
    payload: RecordPatch,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    gov.require_maintainer(current_user)
    record = gov.get_record(db, record_id)
    gov.update_record(db, current_user, record, payload.model_dump(exclude_unset=True))
    _commit(db)
    db.refresh(record)
    return _record_out(record, current_user, detail=True)


@router.post("/records/{record_id}/retire", response_model=RecordDetail)
def retire_record(
    record_id: uuid.UUID,
    payload: ReasonBody,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    record = gov.get_record(db, record_id)
    gov.retire_record(db, current_user, record, payload.reason)
    _commit(db)
    db.refresh(record)
    return _record_out(record, current_user, detail=True)


# -- versions ------------------------------------------------------------------------------------


def _version_or_404(db: Session, user: User, record: SourceRecord, version_id: uuid.UUID) -> SourceVersion:
    version = gov.get_version(db, record, version_id)
    if not gov.can_view_all(user) and version.status != "APPROVED":
        raise gov.fail(404, "VERSION_NOT_FOUND", "Version not found.")
    return version


@router.post("/records/{record_id}/versions", response_model=RecordDetail, status_code=201)
async def add_version(
    record_id: uuid.UUID,
    version_label: str = Form(...),
    effective_date: Optional[date] = Form(None),
    review_date: Optional[date] = Form(None),
    retrieved_date: Optional[date] = Form(None),
    change_summary: Optional[str] = Form(None),
    source_url: Optional[str] = Form(None),
    file: Optional[UploadFile] = File(None),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """A new draft version of a source. The approved version stays in force
    until a reviewer approves this one."""

    gov.require_maintainer(current_user)
    record = gov.get_record(db, record_id)
    url = _url_or_400(source_url)
    accepted = await _accept_pdf(db, current_user, file, record) if file is not None and file.filename else None
    version = gov.create_version(db, current_user, record, {
        "version_label": version_label, "effective_date": effective_date, "review_date": review_date,
        "retrieved_date": retrieved_date, "change_summary": change_summary, "source_url": url,
    })
    if accepted is not None:
        _store_accepted(db, current_user, record, version, accepted)
    _commit(db)
    db.refresh(record)
    return _record_out(record, current_user, detail=True)


@router.get("/records/{record_id}/versions/{version_id}", response_model=VersionOut)
def get_version(record_id: uuid.UUID, version_id: uuid.UUID, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    record = _visible_record(db, current_user, record_id)
    return _version_out(_version_or_404(db, current_user, record, version_id))


@router.patch("/records/{record_id}/versions/{version_id}", response_model=VersionOut)
def update_version(
    record_id: uuid.UUID,
    version_id: uuid.UUID,
    payload: VersionPatch,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    gov.require_maintainer(current_user)
    record = gov.get_record(db, record_id)
    version = gov.get_version(db, record, version_id)
    gov.update_version(db, current_user, record, version, payload.model_dump(exclude_unset=True))
    _commit(db)
    db.refresh(version)
    return _version_out(version)


@router.post("/records/{record_id}/versions/{version_id}/file", response_model=VersionOut)
async def replace_file(
    record_id: uuid.UUID,
    version_id: uuid.UUID,
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    gov.require_maintainer(current_user)
    record = gov.get_record(db, record_id)
    version = gov.get_version(db, record, version_id)
    if version.status not in gov.EDITABLE:
        raise gov.fail(409, "VERSION_NOT_EDITABLE", "Only a draft or rejected version can have its document replaced.")
    accepted = await _accept_pdf(db, current_user, file, record, version)
    _store_accepted(db, current_user, record, version, accepted)
    _commit(db)
    db.refresh(version)
    return _version_out(version)


@router.post("/records/{record_id}/versions/{version_id}/reprocess", response_model=VersionOut)
def reprocess_version(record_id: uuid.UUID, version_id: uuid.UUID, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    record = gov.get_record(db, record_id)
    version = gov.get_version(db, record, version_id)
    gov.reprocess(db, current_user, record, version)
    _commit(db)
    db.refresh(version)
    return _version_out(version)


@router.get("/records/{record_id}/versions/{version_id}/file")
def download_file(record_id: uuid.UUID, version_id: uuid.UUID, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    """The stored PDF as an attachment. Approved versions: any signed-in
    user; others: maintainers, reviewers and auditors. Every download is
    audited."""

    record = _visible_record(db, current_user, record_id)
    version = _version_or_404(db, current_user, record, version_id)
    data = None
    if version.storage_key:
        try:
            data = read_library_file(version.storage_key)
        except (FileNotFoundError, RuntimeError):
            raise gov.fail(410, "DOCUMENT_MISSING", "The stored document is no longer available.")
    elif version.legacy_source_id:
        from app.file_processing.storage import read_file
        from app.models.approved_source import ApprovedSource

        legacy = db.get(ApprovedSource, version.legacy_source_id)
        if legacy is not None and legacy.file_path:
            try:
                data = read_file(legacy.file_path)
            except (FileNotFoundError, RuntimeError):
                raise gov.fail(410, "DOCUMENT_MISSING", "The stored document is no longer available.")
    if data is None:
        raise gov.fail(404, "NO_DOCUMENT", "This version has no uploaded document.")
    gov.audit(db, current_user, "FILE_DOWNLOADED", record=record, version=version,
              details=f"Document '{version.original_filename}' of {record.source_code} version '{version.version_label}' downloaded.")
    log_audit_event(db=db, assessment_id=None, action=AuditAction.DOCUMENT_DOWNLOADED, actor=actor_name(current_user),
                    actor_id=current_user.id,
                    details=f"Source Library document downloaded: {record.source_code} version '{version.version_label}'.")
    db.commit()
    return Response(
        content=data,
        media_type="application/pdf",
        headers={
            "Content-Disposition": content_disposition("attachment", version.original_filename or "document.pdf"),
            "X-Content-Type-Options": "nosniff",
            "Cache-Control": "private, no-store",
        },
    )


@router.get("/records/{record_id}/versions/{version_id}/chunks", response_model=ChunkPage)
def version_chunks(
    record_id: uuid.UUID,
    version_id: uuid.UUID,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """The extracted passages of a version (with page and section), for
    reviewers checking what text will be searched and cited."""

    _require_view_all(current_user)
    record = gov.get_record(db, record_id)
    version = gov.get_version(db, record, version_id)
    rows, total = gov.chunks_of(db, version, page, page_size)
    return ChunkPage(items=[ChunkOut.model_validate(r) for r in rows], total=total, page=page, page_size=page_size)


def _transition(db: Session, record_id: uuid.UUID, version_id: uuid.UUID, user: User, action, *args) -> RecordDetail:
    record = gov.get_record(db, record_id)
    version = gov.get_version(db, record, version_id)
    try:
        action(db, user, record, version, *args)
    except HTTPException as exc:
        if exc.status_code == 403:
            # A refused action (not a reviewer, separation of duties ...) is
            # itself recorded -- committed now, as the request is failing.
            db.rollback()
            code = (exc.detail or {}).get("code") if isinstance(exc.detail, dict) else None
            gov.audit(db, user, "ACTION_DENIED", record=record, version=version,
                      details=f"{action.__name__.replace('_', ' ').capitalize()} refused for {actor_name(user)} ({code}).",
                      data={"action": action.__name__, "code": code})
            db.commit()
        raise
    _commit(db)
    db.refresh(record)
    return _record_out(record, user, detail=True)


@router.post("/records/{record_id}/versions/{version_id}/submit", response_model=RecordDetail)
def submit_version(record_id: uuid.UUID, version_id: uuid.UUID, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    return _transition(db, record_id, version_id, current_user, gov.submit)


@router.post("/records/{record_id}/versions/{version_id}/withdraw", response_model=RecordDetail)
def withdraw_version(record_id: uuid.UUID, version_id: uuid.UUID, payload: CommentBody, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    return _transition(db, record_id, version_id, current_user, gov.withdraw, payload.comment)


@router.post("/records/{record_id}/versions/{version_id}/approve", response_model=RecordDetail)
def approve_version(record_id: uuid.UUID, version_id: uuid.UUID, payload: CommentBody, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    return _transition(db, record_id, version_id, current_user, gov.approve, payload.comment)


@router.post("/records/{record_id}/versions/{version_id}/reject", response_model=RecordDetail)
def reject_version(record_id: uuid.UUID, version_id: uuid.UUID, payload: CommentBody, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    return _transition(db, record_id, version_id, current_user, gov.reject, payload.comment)


@router.post("/records/{record_id}/versions/{version_id}/retire", response_model=RecordDetail)
def retire_version(record_id: uuid.UUID, version_id: uuid.UUID, payload: ReasonBody, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    return _transition(db, record_id, version_id, current_user, gov.retire_version, payload.reason)


# -- history ---------------------------------------------------------------------------------------


@router.get("/records/{record_id}/approvals", response_model=list[ApprovalOut])
def approval_history(record_id: uuid.UUID, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    _require_view_all(current_user)
    record = gov.get_record(db, record_id)
    labels = {v.id: v for v in record.versions}
    rows = db.query(SourceApproval).filter(SourceApproval.record_id == record.id).order_by(SourceApproval.created_at.desc(), SourceApproval.id).all()
    return [
        ApprovalOut(
            id=row.id, version_id=row.version_id,
            version_label=labels[row.version_id].version_label if row.version_id in labels else None,
            version_number=labels[row.version_id].version_number if row.version_id in labels else None,
            action=row.action, from_status=row.from_status, to_status=row.to_status, actor=row.actor,
            comment=row.comment, created_at=row.created_at,
        )
        for row in rows
    ]


@router.get("/records/{record_id}/audit", response_model=AuditPage)
def audit_timeline(
    record_id: uuid.UUID,
    page: int = Query(1, ge=1),
    page_size: int = Query(25, ge=1, le=100),
    event_type: Optional[str] = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    _require_view_all(current_user)
    record = gov.get_record(db, record_id)
    labels = {v.id: v.version_label for v in record.versions}
    query = db.query(SourceAuditLog).filter(SourceAuditLog.record_id == record.id)
    if event_type:
        query = query.filter(SourceAuditLog.event_type == event_type.upper())
    total = query.count()
    rows = query.order_by(SourceAuditLog.created_at.desc(), SourceAuditLog.id).offset((page - 1) * page_size).limit(page_size).all()
    return AuditPage(
        items=[
            AuditOut(id=r.id, version_id=r.version_id, version_label=labels.get(r.version_id), event_type=r.event_type,
                     actor=r.actor, details=r.details, event_data=r.event_data, created_at=r.created_at)
            for r in rows
        ],
        total=total, page=page, page_size=page_size,
    )


# -- what risk assessments may use ---------------------------------------------------------------------


@router.get("/approved", response_model=list[RecordOut])
def approved_sources(
    jurisdictions: Optional[str] = Query(None, description="Free text naming the assessment's countries, e.g. 'Germany, Poland'."),
    category: Optional[list[str]] = Query(None),
    topic: Optional[str] = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Approved, in-effect sources that apply to an assessment touching the
    given jurisdictions (the home jurisdiction and global sources always
    apply). Only the approved version of each is described."""

    applicable = source_retrieval.applicable_jurisdictions(jurisdictions) if jurisdictions is not None else None
    pairs = source_retrieval.list_approved(db, jurisdictions=applicable, categories=category, topic=topic)
    out = []
    for _version, record in pairs:
        item = _record_out(record, current_user)
        item.pending_version = None
        out.append(item)
    return out


@router.get("/passages", response_model=list[PassageOut])
def search_passages(
    q: str = Query(..., min_length=2, max_length=2000),
    jurisdictions: Optional[str] = None,
    category: Optional[list[str]] = Query(None),
    topic: Optional[list[str]] = Query(None),
    limit: int = Query(8, ge=1, le=25),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Ranked passages from approved, applicable source versions, each with
    the source ID, title, version, page and section needed to cite it."""

    applicable = source_retrieval.applicable_jurisdictions(jurisdictions) if jurisdictions is not None else None
    passages = source_retrieval.retrieve(db, q, jurisdictions=applicable, categories=category, topics=topic, limit=limit)
    return [PassageOut(**source_retrieval.passage_dict(p)) for p in passages]


# -- starter catalogue ---------------------------------------------------------------------------------


@router.post("/catalog/seed")
def seed_catalog(db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    """Creates the starter catalogue's missing entries (official AML/KYC and
    sanctions resources, internal-policy placeholders) as DRAFT sources.
    Nothing is approved and existing sources are never changed."""

    gov.require_maintainer(current_user)
    from app.services import source_catalog

    result = source_catalog.seed(db, current_user)
    return {"created": len(result["created"]), "existing": len(result["existing"]), "source_codes": result["created"]}

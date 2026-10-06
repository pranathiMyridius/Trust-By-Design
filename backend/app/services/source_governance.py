"""
Source Library governance: who may do what to a source, the version
lifecycle, separation of duties, and the audit trail.

    DRAFT --submit--> IN_REVIEW --approve--> APPROVED --(successor approved)--> SUPERSEDED
                          |  \\--reject--> REJECTED --(edit)--> DRAFT
                          \\--withdraw--> DRAFT          APPROVED/DRAFT/... --retire--> RETIRED

Rules enforced here, not in the UI:

* Only maintainers create or edit sources; only reviewers approve or
  reject (app/governance/policy.py, "source_library"; provisional).
* A version's creator, uploader, editor and submitter can never decide it.
* An approved version is never changed, and is replaced only when a
  reviewer approves a successor -- creating, uploading or submitting a new
  version leaves it in force.
* Every action writes a source_audit_logs row; submissions and decisions
  also write source_approvals and the application-wide audit trail.
"""

from __future__ import annotations

import logging
import uuid
from datetime import date, datetime, timedelta, timezone
from typing import Any

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.governance import policy
from app.models.approved_source import ApprovedSource
from app.models.source_library import (
    SourceApproval,
    SourceAuditLog,
    SourceChunk,
    SourceRecord,
    SourceVersion,
)
from app.models.user import User
from app.services.audit_service import AuditAction, actor_name, log_audit_event

logger = logging.getLogger(__name__)

CATEGORIES = {
    "REGULATORY_REQUIREMENT": "Regulatory requirement",
    "REGULATORY_GUIDANCE": "Regulatory guidance",
    "INTERNAL_POLICY": "Internal policy",
    "PROCEDURE": "Procedure",
    "CONTROL_LIBRARY": "Control library",
    "RISK_METHODOLOGY": "Risk methodology",
    "SANCTIONS_RESOURCE": "Sanctions resource",
    "TYPOLOGY": "Typologies and standards",
    "VENDOR_DOCUMENT": "Vendor documentation",
    "PREVIOUS_ASSESSMENT": "Previous assessment",
}
VERSION_STATUSES = ["DRAFT", "IN_REVIEW", "APPROVED", "REJECTED", "SUPERSEDED", "RETIRED"]
# Statuses in which a version's content can still be changed.
EDITABLE = {"DRAFT", "REJECTED"}
# Audit events that make a user a contributor to a version (and so unable to decide it).
CONTRIBUTION_EVENTS = {"RECORD_CREATED", "VERSION_CREATED", "VERSION_UPDATED", "FILE_UPLOADED", "REPROCESSED", "SUBMITTED"}
# Record fields that decide where an approved source is used.
SCOPE_FIELDS = ("category", "jurisdiction", "topics")

_LEGACY_TYPE = {
    "INTERNAL_POLICY": "INTERNAL_POLICY",
    "PROCEDURE": "PROCEDURE",
    "CONTROL_LIBRARY": "CONTROL_STANDARD",
    "RISK_METHODOLOGY": "RISK_FRAMEWORK",
    "PREVIOUS_ASSESSMENT": "PREVIOUS_ASSESSMENT",
    "VENDOR_DOCUMENT": "VENDOR_CONTROL_DOCUMENTATION",
}


def _now() -> datetime:
    return datetime.now(timezone.utc)


_last_tick: datetime | None = None


def _tick() -> datetime:
    """A timestamp strictly later than the previous one handed out, so events
    written in the same instant (a supersede and the approval that caused it)
    always sort in the order they happened."""

    global _last_tick
    now = _now()
    if _last_tick is not None and now <= _last_tick:
        now = _last_tick + timedelta(microseconds=1)
    _last_tick = now
    return now


def fail(status_code: int, code: str, message: str) -> HTTPException:
    return HTTPException(status_code=status_code, detail={"code": code, "message": message})


# -- permissions --------------------------------------------------------------------


def _rule(name: str) -> dict:
    return policy.policy()["source_library"][name]


def can_maintain(user: User) -> bool:
    return policy.holds(user, _rule("maintainers"))


def can_review(user: User) -> bool:
    return policy.holds(user, _rule("reviewers"))


def can_view_all(user: User) -> bool:
    """Drafts, rejected and in-review versions, history and audit."""

    return can_maintain(user) or can_review(user) or policy.holds(user, _rule("viewers"))


def require_maintainer(user: User) -> None:
    if not can_maintain(user):
        raise fail(403, "NOT_A_MAINTAINER", "Only a Policy Admin or Admin can add or change library sources.")


def require_reviewer(user: User) -> None:
    if not can_review(user):
        raise fail(
            403,
            "NOT_A_REVIEWER",
            "Only an authorised compliance reviewer ("
            + policy.describe(_rule("reviewers"))
            + ") can approve or reject a source.",
        )


def min_comment_length() -> int:
    return int(policy.policy()["source_library"].get("min_comment_length", 10))


def home_jurisdiction() -> str:
    return str(policy.policy()["source_library"].get("home_jurisdiction") or "").strip()


# -- audit ---------------------------------------------------------------------------


def audit(
    db: Session,
    user: User | None,
    event_type: str,
    *,
    record: SourceRecord | None = None,
    version: SourceVersion | None = None,
    details: str,
    data: dict[str, Any] | None = None,
) -> None:
    db.add(
        SourceAuditLog(
            record_id=record.id if record else (version.record_id if version else None),
            version_id=version.id if version else None,
            event_type=event_type,
            actor=actor_name(user) if user else "System",
            actor_id=user.id if user else None,
            details=details,
            event_data=_jsonable(data) if data else None,
            created_at=_tick(),
        )
    )


def _jsonable(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_jsonable(item) for item in value]
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, uuid.UUID):
        return str(value)
    return value


def _app_audit(db: Session, user: User, details: str) -> None:
    """The same event on the application-wide audit trail."""

    log_audit_event(
        db=db,
        assessment_id=None,
        action=AuditAction.SOURCE_LIBRARY_CHANGED,
        actor=actor_name(user),
        actor_id=user.id,
        details=details,
    )


def _history(
    db: Session,
    user: User,
    version: SourceVersion,
    action: str,
    from_status: str | None,
    to_status: str,
    comment: str | None,
) -> None:
    db.add(
        SourceApproval(
            record_id=version.record_id,
            version_id=version.id,
            action=action,
            from_status=from_status,
            to_status=to_status,
            actor=actor_name(user),
            actor_id=user.id,
            comment=comment,
            created_at=_tick(),
        )
    )


def contributors(db: Session, version: SourceVersion) -> set[int]:
    """Ids of everyone who created, changed or submitted this version."""

    ids = {version.created_by_id, version.submitted_by_id}
    rows = (
        db.query(SourceAuditLog.actor_id)
        .filter(SourceAuditLog.version_id == version.id, SourceAuditLog.event_type.in_(CONTRIBUTION_EVENTS))
        .all()
    )
    ids.update(row[0] for row in rows)
    ids.discard(None)
    return ids


# -- lookup ---------------------------------------------------------------------------


def get_record(db: Session, record_id: uuid.UUID) -> SourceRecord:
    record = db.get(SourceRecord, record_id)
    if record is None:
        raise fail(404, "SOURCE_NOT_FOUND", "Source not found.")
    return record


def get_version(db: Session, record: SourceRecord, version_id: uuid.UUID) -> SourceVersion:
    version = db.get(SourceVersion, version_id)
    if version is None or version.record_id != record.id:
        raise fail(404, "VERSION_NOT_FOUND", "Version not found.")
    return version


def approved_version(db: Session, record: SourceRecord) -> SourceVersion | None:
    return (
        db.query(SourceVersion)
        .filter(SourceVersion.record_id == record.id, SourceVersion.status == "APPROVED")
        .first()
    )


def is_outdated(version: SourceVersion | None, today: date | None = None) -> bool:
    return bool(version and version.review_date and version.review_date < (today or date.today()))


# -- records ----------------------------------------------------------------------------


def _clean_topics(topics: list[str] | None) -> list[str]:
    seen: list[str] = []
    for topic in topics or []:
        value = " ".join(str(topic).split())
        if value and value.lower() not in {item.lower() for item in seen}:
            seen.append(value[:80])
    return seen[:40]


def _generate_code(db: Session, category: str) -> str:
    stem = "SRC-" + "".join(part[0] for part in category.split("_"))[:4]
    number = db.query(SourceRecord).count() + 1
    while True:
        code = f"{stem}-{number:03d}"
        if not db.query(SourceRecord.id).filter(SourceRecord.source_code == code).first():
            return code
        number += 1


def create_record(db: Session, user: User, fields: dict[str, Any]) -> SourceRecord:
    require_maintainer(user)
    category = fields["category"]
    if category not in CATEGORIES:
        raise fail(422, "INVALID_CATEGORY", f"category must be one of: {', '.join(sorted(CATEGORIES))}")
    code = (fields.get("source_code") or "").strip().upper() or _generate_code(db, category)
    if db.query(SourceRecord.id).filter(SourceRecord.source_code == code).first():
        raise fail(409, "DUPLICATE_SOURCE_CODE", f"A source with the ID {code} already exists.")
    record = SourceRecord(
        source_code=code,
        title=fields["title"].strip(),
        authority=fields["authority"].strip(),
        category=category,
        jurisdiction=(fields.get("jurisdiction") or "").strip() or None,
        applicable_entity=(fields.get("applicable_entity") or "").strip() or None,
        source_url=(fields.get("source_url") or "").strip() or None,
        description=(fields.get("description") or "").strip() or None,
        topics=_clean_topics(fields.get("topics")),
        owner=(fields.get("owner") or "").strip() or None,
        review_frequency=(fields.get("review_frequency") or "").strip() or None,
        status="ACTIVE",
        created_by=actor_name(user),
        created_by_id=user.id,
    )
    db.add(record)
    db.flush()
    audit(db, user, "RECORD_CREATED", record=record, details=f"Source {record.source_code} '{record.title}' created.",
          data={"category": record.category, "jurisdiction": record.jurisdiction})
    _app_audit(db, user, f"Source {record.source_code} '{record.title}' created.")
    return record


def update_record(db: Session, user: User, record: SourceRecord, changes: dict[str, Any]) -> SourceRecord:
    require_maintainer(user)
    if record.status == "RETIRED":
        raise fail(409, "RECORD_RETIRED", "A retired source can't be changed.")
    reason = (changes.pop("change_reason", None) or "").strip()
    if "category" in changes and changes["category"] not in CATEGORIES:
        raise fail(422, "INVALID_CATEGORY", f"category must be one of: {', '.join(sorted(CATEGORIES))}")
    if "topics" in changes:
        changes["topics"] = _clean_topics(changes["topics"])
    for key in ("title", "authority"):
        if key in changes and not (changes[key] or "").strip():
            raise fail(422, "REQUIRED_FIELD", f"{key} can't be blank.")

    before: dict[str, Any] = {}
    after: dict[str, Any] = {}
    for field, value in changes.items():
        value = value.strip() if isinstance(value, str) else value
        if isinstance(value, str) and field not in {"title", "authority", "category"}:
            value = value or None
        if getattr(record, field) != value:
            before[field] = getattr(record, field)
            after[field] = value
    if not after:
        return record

    scope_changed = [field for field in SCOPE_FIELDS if field in after]
    if scope_changed and approved_version(db, record) is not None and len(reason) < min_comment_length():
        raise fail(
            422,
            "CHANGE_REASON_REQUIRED",
            f"This source has an approved version in use; give a reason (at least {min_comment_length()} "
            f"characters) to change {', '.join(scope_changed)}.",
        )
    for field, value in after.items():
        setattr(record, field, value)
    audit(db, user, "RECORD_UPDATED", record=record,
          details=f"Source {record.source_code} updated ({', '.join(sorted(after))})." + (f" Reason: {reason}" if reason else ""),
          data={"before": before, "after": after, "reason": reason or None})
    if scope_changed:
        _app_audit(db, user, f"Source {record.source_code}: {', '.join(scope_changed)} changed while in use. Reason: {reason}")
    sync_record_to_legacy(db, record)
    return record


# -- versions ---------------------------------------------------------------------------


def create_version(db: Session, user: User, record: SourceRecord, fields: dict[str, Any]) -> SourceVersion:
    """A new DRAFT version. The record's approved version is untouched."""

    require_maintainer(user)
    if record.status == "RETIRED":
        raise fail(409, "RECORD_RETIRED", "This source is retired; add a new source instead.")
    label = (fields.get("version_label") or "").strip()
    if not label:
        raise fail(422, "REQUIRED_FIELD", "A version label is required.")
    open_version = (
        db.query(SourceVersion)
        .filter(SourceVersion.record_id == record.id, SourceVersion.status.in_(("DRAFT", "IN_REVIEW", "REJECTED")))
        .first()
    )
    if open_version is not None:
        raise fail(
            409,
            "OPEN_VERSION_EXISTS",
            f"Version '{open_version.version_label}' is still {open_version.status.replace('_', ' ').lower()}; "
            "finish or withdraw it before adding another.",
        )
    number = (db.query(SourceVersion).filter(SourceVersion.record_id == record.id).count()) + 1
    version = SourceVersion(
        record_id=record.id,
        version_number=number,
        version_label=label[:100],
        effective_date=fields.get("effective_date"),
        review_date=fields.get("review_date"),
        retrieved_date=fields.get("retrieved_date"),
        source_url=(fields.get("source_url") or "").strip() or None,
        change_summary=(fields.get("change_summary") or "").strip() or None,
        status="DRAFT",
        created_by=actor_name(user),
        created_by_id=user.id,
    )
    db.add(version)
    db.flush()
    previous = approved_version(db, record)
    audit(db, user, "VERSION_CREATED", record=record, version=version,
          details=f"Version {version.version_number} ('{version.version_label}') of {record.source_code} created as a draft."
          + (f" The approved version '{previous.version_label}' remains in force until this one is approved." if previous else ""),
          data={"version_number": number})
    return version


VERSION_EDITABLE_FIELDS = ("version_label", "effective_date", "review_date", "retrieved_date", "source_url", "change_summary")


def update_version(db: Session, user: User, record: SourceRecord, version: SourceVersion, changes: dict[str, Any]) -> SourceVersion:
    require_maintainer(user)
    _require_editable(version)
    before, after = {}, {}
    for field in VERSION_EDITABLE_FIELDS:
        if field not in changes:
            continue
        value = changes[field]
        if isinstance(value, str):
            value = value.strip() or None
        if field == "version_label" and not value:
            raise fail(422, "REQUIRED_FIELD", "A version label is required.")
        if getattr(version, field) != value:
            before[field] = getattr(version, field)
            after[field] = value
            setattr(version, field, value)
    if not after:
        return version
    reopened = version.status == "REJECTED"
    if reopened:
        version.status = "DRAFT"
        _history(db, user, version, "REOPENED", "REJECTED", "DRAFT", "Edited after rejection.")
    audit(db, user, "VERSION_UPDATED", record=record, version=version,
          details=f"Version {version.version_number} of {record.source_code} edited ({', '.join(sorted(after))})."
          + (" Returned to draft." if reopened else ""),
          data={"before": before, "after": after})
    return version


def _require_editable(version: SourceVersion) -> None:
    if version.status not in EDITABLE:
        raise fail(
            409,
            "VERSION_NOT_EDITABLE",
            f"A {version.status.replace('_', ' ').lower()} version can't be changed. "
            + ("Withdraw it from review first." if version.status == "IN_REVIEW" else "Add a new version instead."),
        )


def attach_file(db: Session, user: User, record: SourceRecord, version: SourceVersion, *, filename: str,
                content_type: str | None, data: bytes, storage_key: str, scan_status: str, scan_detail: str,
                sha256: str) -> SourceVersion:
    """Records an already-validated, already-scanned, already-stored PDF on a
    draft version and (re)processes it."""

    from app.services import source_documents

    _require_editable(version)
    old_key = version.storage_key
    version.original_filename = filename
    version.storage_key = storage_key
    version.content_type = "application/pdf"
    version.file_size = len(data)
    version.file_sha256 = sha256
    version.scan_status = scan_status
    version.scan_detail = scan_detail[:255]
    if version.status == "REJECTED":
        version.status = "DRAFT"
        _history(db, user, version, "REOPENED", "REJECTED", "DRAFT", "Document replaced after rejection.")
    source_documents.process_pdf(db, version, data)
    audit(db, user, "FILE_UPLOADED", record=record, version=version,
          details=f"Document '{filename}' ({len(data):,} bytes, sha256 {sha256[:12]}…) attached to version "
                  f"{version.version_number}; processing {version.processing_status.lower()}; {version.chunk_count} chunk(s).",
          data={"sha256": sha256, "size": len(data), "processing_status": version.processing_status,
                "embedding_status": version.embedding_status, "scan": scan_status})
    return version if old_key is None else _discard_old_file(version, old_key)


def _discard_old_file(version: SourceVersion, old_key: str) -> SourceVersion:
    from app.file_processing.storage import delete_library_file

    if old_key != version.storage_key:
        delete_library_file(old_key)
    return version


def reprocess(db: Session, user: User, record: SourceRecord, version: SourceVersion) -> SourceVersion:
    from app.file_processing.storage import read_library_file
    from app.services import source_documents

    require_maintainer(user)
    _require_editable(version)
    if not version.storage_key:
        raise fail(409, "NO_DOCUMENT", "This version has no uploaded document to process.")
    try:
        data = read_library_file(version.storage_key)
    except (FileNotFoundError, RuntimeError):
        raise fail(410, "DOCUMENT_MISSING", "The stored document can't be read; upload it again.")
    source_documents.process_pdf(db, version, data)
    audit(db, user, "REPROCESSED", record=record, version=version,
          details=f"Version {version.version_number} reprocessed: {version.processing_status.lower()}, {version.chunk_count} chunk(s).",
          data={"processing_status": version.processing_status, "embedding_status": version.embedding_status})
    return version


# -- workflow --------------------------------------------------------------------------


def submit(db: Session, user: User, record: SourceRecord, version: SourceVersion) -> SourceVersion:
    require_maintainer(user)
    if record.status == "RETIRED":
        raise fail(409, "RECORD_RETIRED", "This source is retired.")
    if version.status != "DRAFT":
        raise fail(409, "INVALID_TRANSITION", f"Only a draft can be submitted; this version is {version.status.replace('_', ' ').lower()}.")
    if not (version.storage_key or version.source_url or record.source_url or version.extracted_text):
        raise fail(422, "NOTHING_TO_REVIEW", "Add an official URL or upload a PDF before submitting for review.")
    if version.storage_key:
        if version.scan_status != "CLEAN":
            raise fail(409, "NOT_SCANNED", "The document has not passed malware scanning; upload it again.")
        if version.processing_status != "PROCESSED":
            raise fail(409, "PROCESSING_FAILED",
                       "The document's text could not be processed" + (f" ({version.processing_error})" if version.processing_error else "")
                       + ". Fix or replace the document before submitting.")
    version.status = "IN_REVIEW"
    version.submitted_by = actor_name(user)
    version.submitted_by_id = user.id
    version.submitted_at = _now()
    version.decided_by = version.decided_by_id = version.decided_at = version.decision_comment = None
    _history(db, user, version, "SUBMITTED", "DRAFT", "IN_REVIEW", None)
    audit(db, user, "SUBMITTED", record=record, version=version,
          details=f"Version {version.version_number} ('{version.version_label}') of {record.source_code} submitted for review.")
    _app_audit(db, user, f"Source {record.source_code} version '{version.version_label}' submitted for review.")
    return version


def withdraw(db: Session, user: User, record: SourceRecord, version: SourceVersion, comment: str | None) -> SourceVersion:
    if not (can_maintain(user) or user.id == version.submitted_by_id):
        raise fail(403, "NOT_A_MAINTAINER", "Only a maintainer or the submitter can withdraw a version from review.")
    if version.status != "IN_REVIEW":
        raise fail(409, "INVALID_TRANSITION", "Only a version in review can be withdrawn.")
    version.status = "DRAFT"
    _history(db, user, version, "WITHDRAWN", "IN_REVIEW", "DRAFT", (comment or "").strip() or None)
    audit(db, user, "WITHDRAWN", record=record, version=version,
          details=f"Version {version.version_number} of {record.source_code} withdrawn from review.")
    _app_audit(db, user, f"Source {record.source_code} version '{version.version_label}' withdrawn from review.")
    return version


def _decision_guard(
    db: Session, user: User, record: SourceRecord, version: SourceVersion, comment: str | None,
    min_length: int | None = None,
) -> str:
    require_reviewer(user)
    if version.status != "IN_REVIEW":
        raise fail(409, "INVALID_TRANSITION", f"Only a version in review can be decided; this one is {version.status.replace('_', ' ').lower()}.")
    if record.status == "RETIRED":
        raise fail(409, "RECORD_RETIRED", "This source is retired.")
    if policy.policy()["source_library"].get("require_independent_review", True) and user.id in contributors(db, version):
        raise fail(
            403,
            "SEPARATION_OF_DUTIES",
            "You prepared or submitted this version, so you can't approve or reject it. Another compliance reviewer must decide.",
        )
    text = (comment or "").strip()
    required = min_comment_length() if min_length is None else min_length
    if len(text) < max(required, 1):
        raise fail(422, "COMMENT_REQUIRED", f"A decision comment of at least {max(required, 1)} characters is required.")
    return text


def approve(
    db: Session, user: User, record: SourceRecord, version: SourceVersion, comment: str | None,
    min_length: int | None = None,
) -> SourceVersion:
    text = _decision_guard(db, user, record, version, comment, min_length)
    now = _now()
    superseded = approved_version(db, record)
    if superseded is not None:
        superseded.status = "SUPERSEDED"
        superseded.superseded_at = now
        db.flush()  # free the one-approved-version slot before taking it
        _history(db, user, superseded, "SUPERSEDED", "APPROVED", "SUPERSEDED",
                 f"Replaced by version '{version.version_label}' on approval.")
        audit(db, user, "SUPERSEDED", record=record, version=superseded,
              details=f"Version {superseded.version_number} ('{superseded.version_label}') superseded by version {version.version_number}.")
        sync_to_legacy(db, record, superseded)
    version.status = "APPROVED"
    version.decided_by = actor_name(user)
    version.decided_by_id = user.id
    version.decided_at = now
    version.decision_comment = text
    _history(db, user, version, "APPROVED", "IN_REVIEW", "APPROVED", text)
    audit(db, user, "APPROVED", record=record, version=version,
          details=f"Version {version.version_number} ('{version.version_label}') of {record.source_code} approved. Comment: {text}")
    _app_audit(db, user, f"Source {record.source_code} version '{version.version_label}' approved. Comment: {text}")
    db.flush()
    # Text-only (migrated) versions are chunked the first time they are used.
    from app.services import source_documents

    source_documents.ensure_chunks(db, version)
    sync_to_legacy(db, record, version)
    return version


def reject(db: Session, user: User, record: SourceRecord, version: SourceVersion, comment: str | None) -> SourceVersion:
    text = _decision_guard(db, user, record, version, comment)
    version.status = "REJECTED"
    version.decided_by = actor_name(user)
    version.decided_by_id = user.id
    version.decided_at = _now()
    version.decision_comment = text
    _history(db, user, version, "REJECTED", "IN_REVIEW", "REJECTED", text)
    audit(db, user, "REJECTED", record=record, version=version,
          details=f"Version {version.version_number} ('{version.version_label}') of {record.source_code} rejected. Comment: {text}")
    _app_audit(db, user, f"Source {record.source_code} version '{version.version_label}' rejected. Comment: {text}")
    return version


def retire_version(
    db: Session, user: User, record: SourceRecord, version: SourceVersion, reason: str | None,
    min_length: int | None = None,
) -> SourceVersion:
    if not (can_maintain(user) or can_review(user)):
        raise fail(403, "NOT_A_MAINTAINER", "Only a maintainer or compliance reviewer can retire a source.")
    text = (reason or "").strip()
    required = max(min_comment_length() if min_length is None else min_length, 1)
    if len(text) < required:
        raise fail(422, "COMMENT_REQUIRED", f"A reason of at least {required} characters is required.")
    if version.status in {"RETIRED", "SUPERSEDED"}:
        raise fail(409, "INVALID_TRANSITION", f"This version is already {version.status.lower()}.")
    previous = version.status
    version.status = "RETIRED"
    _history(db, user, version, "RETIRED", previous, "RETIRED", text)
    audit(db, user, "RETIRED", record=record, version=version,
          details=f"Version {version.version_number} ('{version.version_label}') of {record.source_code} retired. Reason: {text}")
    _app_audit(db, user, f"Source {record.source_code} version '{version.version_label}' retired. Reason: {text}")
    db.flush()
    sync_to_legacy(db, record, version)
    return version


def retire_record(db: Session, user: User, record: SourceRecord, reason: str | None) -> SourceRecord:
    if not (can_maintain(user) or can_review(user)):
        raise fail(403, "NOT_A_MAINTAINER", "Only a maintainer or compliance reviewer can retire a source.")
    if record.status == "RETIRED":
        raise fail(409, "RECORD_RETIRED", "This source is already retired.")
    for version in list(record.versions):
        if version.status not in {"RETIRED", "SUPERSEDED"}:
            retire_version(db, user, record, version, reason)
    record.status = "RETIRED"
    audit(db, user, "RECORD_RETIRED", record=record, details=f"Source {record.source_code} retired. Reason: {(reason or '').strip()}")
    return record


# -- the older approved_sources table -----------------------------------------------------


def sync_to_legacy(db: Session, record: SourceRecord, version: SourceVersion) -> None:
    """Keeps the older flat approved_sources row (read by evidence
    attachment and its search) in step with an approved/retired version
    that has text. A version without text has no legacy row."""

    legacy = db.get(ApprovedSource, version.legacy_source_id) if version.legacy_source_id else None
    if version.status == "APPROVED":
        if not (version.extracted_text or "").strip():
            return
        if legacy is None:
            legacy = ApprovedSource(status="DRAFT", content=version.extracted_text, title=record.title,
                                    source_type="REGULATORY_GUIDANCE", version=version.version_label)
            db.add(legacy)
        legacy.title = record.title
        legacy.source_type = _LEGACY_TYPE.get(record.category, "REGULATORY_GUIDANCE")
        legacy.issuer = record.authority
        legacy.version = version.version_label
        legacy.effective_date = version.effective_date
        legacy.review_date = version.review_date
        link = version.source_url or record.source_url
        if link:
            legacy.reference = link
        elif not legacy.reference:
            # Keep a document reference (e.g. a policy number) already given.
            legacy.reference = version.original_filename or record.source_code
        legacy.content = version.extracted_text
        legacy.status = "APPROVED"
        legacy.approved_by = version.decided_by
        legacy.approved_by_id = version.decided_by_id
        legacy.approved_at = version.decided_at
        legacy.created_by = legacy.created_by or version.created_by
        db.flush()
        version.legacy_source_id = legacy.id
    elif legacy is not None and version.status in {"SUPERSEDED", "RETIRED"}:
        legacy.status = "RETIRED"


def sync_record_to_legacy(db: Session, record: SourceRecord) -> None:
    version = approved_version(db, record)
    if version is not None:
        sync_to_legacy(db, record, version)


def chunks_of(db: Session, version: SourceVersion, page: int, page_size: int) -> tuple[list[SourceChunk], int]:
    query = db.query(SourceChunk).filter(SourceChunk.version_id == version.id)
    total = query.count()
    rows = query.order_by(SourceChunk.chunk_index).offset((page - 1) * page_size).limit(page_size).all()
    return rows, total


# -- the earlier /api/sources endpoints, run through this workflow -----------------------------

_LEGACY_CATEGORY = {legacy: category for category, legacy in _LEGACY_TYPE.items()}
_LEGACY_CATEGORY.update({"REGULATORY_GUIDANCE": "REGULATORY_GUIDANCE"})


def version_for_legacy(db: Session, legacy: ApprovedSource) -> SourceVersion | None:
    return db.query(SourceVersion).filter(SourceVersion.legacy_source_id == legacy.id).first()


def register_legacy(db: Session, user: User, legacy: ApprovedSource) -> tuple[SourceRecord, SourceVersion]:
    """A record and draft version for a source created through the earlier
    API, so it is governed (reviewed, audited, versioned) like any other."""

    reference = (legacy.reference or "").strip()
    is_url = reference.lower().startswith(("http://", "https://")) and len(reference) <= 1000
    record = SourceRecord(
        source_code=f"SRC-LEG-{legacy.id:04d}",
        title=legacy.title,
        authority=legacy.issuer or "Not recorded",
        category=_LEGACY_CATEGORY.get(legacy.source_type, "REGULATORY_GUIDANCE"),
        source_url=reference if is_url else None,
        description=f"Reference: {reference}" if reference and not is_url else None,
        topics=[],
        owner=legacy.created_by,
        status="ACTIVE",
        created_by=actor_name(user),
        created_by_id=user.id,
    )
    db.add(record)
    db.flush()
    version = SourceVersion(
        record_id=record.id,
        version_number=1,
        version_label=legacy.version,
        effective_date=legacy.effective_date,
        review_date=legacy.review_date,
        source_url=record.source_url,
        status="DRAFT",
        original_filename=legacy.original_filename,
        content_type=legacy.file_content_type,
        file_size=legacy.file_size,
        file_sha256=legacy.file_sha256,
        processing_status="PROCESSED",
        scan_status="NOT_SCANNED",
        scan_detail="Added through the earlier library API; not scanned by this module.",
        extracted_text=legacy.content,
        legacy_source_id=legacy.id,
        created_by=actor_name(user),
        created_by_id=user.id,
    )
    db.add(version)
    db.flush()
    audit(db, user, "RECORD_CREATED", record=record, version=version,
          details=f"Source {record.source_code} '{record.title}' created through the earlier library API (legacy source #{legacy.id}).")
    return record, version


def sync_from_legacy(db: Session, user: User, legacy: ApprovedSource) -> None:
    """Carries an edit of a draft legacy source to its record/version."""

    version = version_for_legacy(db, legacy)
    if version is None or version.status not in EDITABLE:
        return
    record = version.record
    record.title = legacy.title
    record.authority = legacy.issuer or "Not recorded"
    record.category = _LEGACY_CATEGORY.get(legacy.source_type, record.category)
    version.version_label = legacy.version
    version.effective_date = legacy.effective_date
    version.review_date = legacy.review_date
    if (version.extracted_text or "") != legacy.content:
        from app.services import source_documents

        version.extracted_text = legacy.content
        source_documents.delete_chunks(db, version)
    audit(db, user, "VERSION_UPDATED", record=record, version=version,
          details=f"Draft edited through the earlier library API (legacy source #{legacy.id}).")


def implicit_submit(db: Session, record: SourceRecord, version: SourceVersion) -> None:
    """The earlier API approves a draft directly. Record that as a
    submission by the draft's own creator, so the history is complete and
    separation of duties still applies to them."""

    version.status = "IN_REVIEW"
    version.submitted_by = version.created_by
    version.submitted_by_id = version.created_by_id
    version.submitted_at = _now()
    db.add(SourceApproval(
        record_id=record.id, version_id=version.id, action="SUBMITTED", from_status="DRAFT", to_status="IN_REVIEW",
        actor=version.created_by or "System", actor_id=version.created_by_id,
        comment="Submitted implicitly: approval was requested through the earlier library API.",
        created_at=_tick(),
    ))
    audit(db, None, "SUBMITTED", record=record, version=version,
          details="Submitted for review implicitly because approval was requested through the earlier library API.")

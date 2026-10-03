import json
from datetime import datetime, timedelta
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from sqlalchemy.orm.exc import StaleDataError

from app.auth.dependencies import get_current_user, log_denied_attempt, require_role
from app.database import get_db
from app.governance import retention as retention_rules
from app.governance.policy import POLICY_STATUS, holds
from app.models.assessment import Assessment
from app.models.audit_trail import AssessmentRetention, LegalHoldEvent, RetentionPolicy, RetentionPolicyVersion
from app.models.user import User, UserRole
from app.schemas.audit_trail import (
    AssessmentRetentionResponse,
    LegalHoldUpdate,
    RetentionDecision,
    RetentionPolicyResponse,
    RetentionPolicyUpdate,
    RetentionProposal,
    SoftDeleteRequest,
)
from app.services.audit_export import build_audit_package
from app.services.audit_service import AuditAction, log_audit_event
from app.services.explainability import explain_assessment
from app.services.explainability_statements import classify_statements

# R16.1/R16.3/R16.4: exporting and deleting are authorized-user actions.
# P5: who may propose / approve retention changes and set / release legal
# holds is configured in app/governance/policy.py ("retention") and
# enforced in app/governance/retention.py.
require_pipeline_role = require_role(UserRole.MANAGER, UserRole.ADMIN)
require_admin_role = require_role(UserRole.ADMIN)
# R15.1/R16.3: an Auditor exports audit packages too (a read, so allowed
# for a read-only role).
require_export_role = require_role(UserRole.MANAGER, UserRole.ADMIN, UserRole.AUDITOR)

router = APIRouter(tags=["Audit Trail"])

# P5: the provisional default lives in app/governance/retention.py.
DEFAULT_RETENTION_DAYS = retention_rules.DEFAULT_RETENTION_DAYS


def _get_assessment_or_404(db: Session, assessment_id: int) -> Assessment:
    assessment = db.query(Assessment).filter(Assessment.id == assessment_id).first()
    if not assessment:
        raise HTTPException(status_code=404, detail="Assessment not found")
    return assessment


def _get_or_create_active_policy(db: Session) -> RetentionPolicy:
    """The legacy single policy row (kept for backward compatibility; P5
    never edits it -- see RetentionPolicyVersion)."""

    policy = (
        db.query(RetentionPolicy).filter(RetentionPolicy.is_active.is_(True)).first()
    )
    if policy:
        return policy

    policy = RetentionPolicy(
        name="Default Retention Policy",
        is_active=True,
        default_retention_days=DEFAULT_RETENTION_DAYS,
    )
    db.add(policy)
    db.flush()
    return policy


def _commit(db: Session) -> None:
    try:
        db.commit()
    except (StaleDataError, IntegrityError):
        # A concurrent decision, or a second open proposal / active version
        # (unique indexes): nothing was written.
        db.rollback()
        raise HTTPException(
            status_code=409,
            detail="Someone else changed the retention policy at the same time; reload and try again.",
        )


def _refuse(db: Session, user: User, request: Request, exc: HTTPException, assessment_id: int | None = None) -> None:
    """Roll back the attempted change; a refused (403) action is logged as
    ACCESS_DENIED (R15.5)."""

    db.rollback()
    if exc.status_code == 403:
        log_denied_attempt(db, user, request, f"retention: {exc.detail}", assessment_id=assessment_id)


def _guarded(db: Session, user: User, request: Request, action, *args, assessment_id: int | None = None):
    try:
        return action(*args)
    except HTTPException as exc:
        _refuse(db, user, request, exc, assessment_id)
        raise
    except (StaleDataError, IntegrityError):
        # Flushed mid-transaction: a concurrent decision or a second open
        # proposal / active version. Nothing is written.
        db.rollback()
        raise HTTPException(
            status_code=409,
            detail="Someone else changed the retention policy at the same time; reload and try again.",
        )


def _deny(db: Session, user: User, request: Request, detail: str) -> HTTPException:
    log_denied_attempt(db, user, request, f"retention: {detail}")
    return HTTPException(status_code=403, detail=detail)


def _legacy_policy_response(db: Session) -> dict:
    active = retention_rules.ensure_seeded(db)
    legacy = _get_or_create_active_policy(db)
    pending = retention_rules.pending_version(db)
    return {
        "id": legacy.id,
        "name": legacy.name,
        "is_active": legacy.is_active,
        # Read-through: the period in force is the ACTIVE version.
        "default_retention_days": active.retention_days if active else legacy.default_retention_days,
        "created_at": legacy.created_at,
        "updated_at": legacy.updated_at,
        "record_type": retention_rules.RECORD_TYPE_ASSESSMENT,
        "active_version_id": active.id if active else None,
        "active_version": active.version if active else None,
        "effective_from": active.effective_from if active else None,
        "pending_version_id": pending.id if pending else None,
        "pending_retention_days": pending.retention_days if pending else None,
        "policy_status": POLICY_STATUS,
    }


def _retention_response(
    db: Session, assessment: Assessment, retention: AssessmentRetention, user: User | None = None
) -> AssessmentRetentionResponse:
    eligibility = retention_rules.evaluate(db, assessment, retention)
    detail_visible = user is not None and retention_rules.can_see_hold_detail(user)
    eligible_at = eligibility["eligible_at"]

    return AssessmentRetentionResponse(
        assessment_id=assessment.id,
        legal_hold=retention.legal_hold,
        legal_hold_reason=retention.legal_hold_reason if detail_visible else None,
        legal_hold_set_by=retention.legal_hold_set_by,
        legal_hold_set_at=retention.legal_hold_set_at,
        is_deleted=retention.is_deleted,
        deleted_by=retention.deleted_by,
        deleted_at=retention.deleted_at,
        deletion_reason=retention.deletion_reason,
        eligible_for_deletion_at=datetime.fromisoformat(eligible_at) if eligible_at else None,
        # Kept for older clients: true only when eligible under the one rule.
        retention_expired=eligibility["eligible"],
        eligibility=eligibility,
        policy_version_id=eligibility["policy_version_id"],
        policy_version=eligibility["policy_version"],
        retention_days=eligibility["retention_days"],
        retention_policy_version_id=retention.retention_policy_version_id,
        hold_detail_visible=detail_visible,
        hold_history=retention_rules.hold_history(db, assessment.id) if detail_visible else [],
        actions=retention_rules.actions_for(retention, user, eligibility) if user is not None else {},
        policy_status=POLICY_STATUS,
    )


# -- policy (legacy single-value API, now versioned underneath) -----------------


@router.get("/api/retention-policy", response_model=RetentionPolicyResponse)
def get_retention_policy(db: Session = Depends(get_db)):
    response = _legacy_policy_response(db)
    db.commit()  # persists a lazily seeded version / legacy row
    return response


@router.patch("/api/retention-policy", response_model=RetentionPolicyResponse)
def update_retention_policy(
    payload: RetentionPolicyUpdate,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """P5: no longer edits in place. A new period becomes a PROPOSED
    version (with the mandatory change_reason) that needs independent
    approval; the response shows the unchanged active period and the
    pending proposal."""

    if payload.default_retention_days is None:
        raise HTTPException(
            status_code=422,
            detail="Send default_retention_days and change_reason; the policy is versioned and its name is not editable.",
        )
    _guarded(
        db, current_user, request, retention_rules.propose,
        db, current_user, retention_rules.RECORD_TYPE_ASSESSMENT, payload.default_retention_days, payload.change_reason or "",
    )
    _commit(db)
    return _legacy_policy_response(db)


# -- P5 retention administration ------------------------------------------------


@router.get("/api/retention/permissions")
def get_retention_permissions(current_user: User = Depends(get_current_user)):
    """What the signed-in user may do with retention (the UI shows actions
    from this; the server enforces them again on every request)."""

    return retention_rules.permissions(current_user)


@router.get("/api/retention/policies")
def list_retention_policies(
    request: Request, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)
):
    if not retention_rules.can_view(current_user):
        raise _deny(db, current_user, request, "only retention administrators, approvers and report readers can view policy versions")
    retention_rules.ensure_seeded(db)
    db.commit()
    rules = retention_rules.rules()
    record_types = []
    for record_type, config in sorted(rules["record_types"].items()):
        versions = (
            db.query(RetentionPolicyVersion)
            .filter(RetentionPolicyVersion.record_type == record_type)
            .order_by(RetentionPolicyVersion.version.desc())
            .all()
        )
        active = next((v for v in versions if v.status == retention_rules.STATUS_ACTIVE), None)
        pending = next((v for v in versions if v.status == retention_rules.STATUS_PROPOSED), None)
        record_types.append(
            {
                "record_type": record_type,
                "basis": config["basis"],
                "active": retention_rules.version_dict(db, active, current_user) if active else None,
                "pending": retention_rules.version_dict(db, pending, current_user) if pending else None,
                "versions": [retention_rules.version_dict(db, v, current_user) for v in versions],
            }
        )
    return {
        "record_types": record_types,
        "permissions": retention_rules.permissions(current_user),
        "policy_status": POLICY_STATUS,
    }


@router.post("/api/retention/policies", status_code=201)
def propose_retention_policy(
    payload: RetentionProposal,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    version = _guarded(
        db, current_user, request, retention_rules.propose,
        db, current_user, payload.record_type, payload.retention_days, payload.change_reason,
    )
    _commit(db)
    db.refresh(version)
    return retention_rules.version_dict(db, version, current_user)


def _load_version(db: Session, version_id: int) -> RetentionPolicyVersion:
    version = db.get(RetentionPolicyVersion, version_id)
    if version is None:
        raise HTTPException(status_code=404, detail="Retention policy version not found")
    return version


@router.get("/api/retention/policies/{version_id}")
def get_retention_policy_version(
    version_id: int, request: Request, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)
):
    if not retention_rules.can_view(current_user):
        raise _deny(db, current_user, request, "only retention administrators, approvers and report readers can view policy versions")
    return retention_rules.version_dict(db, _load_version(db, version_id), current_user)


@router.post("/api/retention/policies/{version_id}/decision")
def decide_retention_policy(
    version_id: int,
    payload: RetentionDecision,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    version = _load_version(db, version_id)
    _guarded(db, current_user, request, retention_rules.decide, db, version, current_user, payload.decision, payload.reason)
    _commit(db)
    db.refresh(version)
    return retention_rules.version_dict(db, version, current_user)


_REPORT_STATUS = {
    "eligible": {retention_rules.ELIGIBLE},
    "held": {retention_rules.LEGAL_HOLD},
    "upcoming": {retention_rules.RETAINED},
    "retained": {retention_rules.RETAINED},
    "not_started": {retention_rules.NOT_STARTED},
    "no_policy": {retention_rules.NO_POLICY},
    "invalid": {retention_rules.INVALID_POLICY, retention_rules.INVALID_DATE},
    "deleted": {retention_rules.SOFT_DELETED},
}
_REPORT_FIELDS = [
    "record_id", "record_type", "reference", "assessment_status", "policy_version_id", "policy_version",
    "retention_days", "basis", "basis_date", "eligible_at", "eligible", "eligibility_status", "legal_hold",
    "is_deleted", "reason", "evaluated_at",
]


def _parse_date(value: str | None, name: str) -> datetime | None:
    if not value:
        return None
    try:
        return retention_rules.naive_utc(datetime.fromisoformat(value))
    except ValueError:
        raise HTTPException(status_code=422, detail=f"{name} must be an ISO date (YYYY-MM-DD).")


def _in_range(value: str | None, low: datetime | None, high: datetime | None) -> bool:
    if low is None and high is None:
        return True
    if value is None:
        return False
    moment = datetime.fromisoformat(value)
    return (low is None or moment >= low) and (high is None or moment <= high)


@router.get("/api/retention/eligibility")
def retention_eligibility_report(
    request: Request,
    status: Optional[str] = None,
    record_type: str = "ASSESSMENT",
    within_days: int = Query(90, ge=0, le=36500),
    legal_hold: Optional[bool] = None,
    policy_version_id: Optional[int] = None,
    basis_from: Optional[str] = None,
    basis_to: Optional[str] = None,
    eligible_from: Optional[str] = None,
    eligible_to: Optional[str] = None,
    include_deleted: bool = False,
    sort: str = "eligible_at",
    order: str = "asc",
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """P5 (R16.4, D-4): read-only retention eligibility across the
    assessments the user can see (P1 visibility and R15.4 entity scope
    apply). Identifiers, dates and the outcome only -- no assessment
    content, evidence or personal data. Nothing here deletes anything."""

    from app.api.assessments import _scope_assessments_for_user
    from app.auth.access import DELETED_ASSESSMENT_READERS
    from app.models.audit_trail import soft_deleted_assessment_ids

    if not holds(current_user, retention_rules.rules()["eligibility_report_readers"]):
        raise _deny(db, current_user, request, "the retention eligibility report is for authorized report readers")
    if include_deleted and current_user.role not in DELETED_ASSESSMENT_READERS:
        raise _deny(db, current_user, request, "soft-deleted assessments are visible to Admins and Auditors only")
    if record_type not in retention_rules.rules()["record_types"]:
        raise HTTPException(status_code=422, detail=f"Unknown record type '{record_type}'.")
    statuses = None
    if status:
        statuses = [key.strip().lower() for key in status.split(",") if key.strip()]
        if any(key not in _REPORT_STATUS for key in statuses):
            raise HTTPException(status_code=422, detail=f"status must be one of: {', '.join(_REPORT_STATUS)}")
    if sort not in {"eligible_at", "basis_date", "record_id"} or order not in {"asc", "desc"}:
        raise HTTPException(status_code=422, detail="sort is eligible_at, basis_date or record_id; order is asc or desc.")
    ranges = {
        "basis_from": _parse_date(basis_from, "basis_from"),
        "basis_to": _parse_date(basis_to, "basis_to"),
        "eligible_from": _parse_date(eligible_from, "eligible_from"),
        "eligible_to": _parse_date(eligible_to, "eligible_to"),
    }

    retention_rules.ensure_seeded(db)
    now = retention_rules.utcnow()
    version = retention_rules.effective_version(db, record_type, now)
    query = _scope_assessments_for_user(db.query(Assessment), current_user)
    if not include_deleted:
        query = query.filter(Assessment.id.notin_(soft_deleted_assessment_ids()))
    assessments = query.all()
    retentions = {
        row.assessment_id: row
        for row in db.query(AssessmentRetention).filter(
            AssessmentRetention.assessment_id.in_([a.id for a in assessments] or [-1])
        )
    }

    def keep(row: dict) -> bool:
        if statuses:
            matched = False
            for key in statuses:
                if row["eligibility_status"] not in _REPORT_STATUS[key]:
                    continue
                if key == "upcoming":
                    horizon = now + timedelta(days=within_days)
                    matched = matched or (row["eligible_at"] is not None and datetime.fromisoformat(row["eligible_at"]) <= horizon)
                else:
                    matched = True
            if not matched:
                return False
        if legal_hold is not None and row["legal_hold"] != legal_hold:
            return False
        if policy_version_id is not None and row["policy_version_id"] != policy_version_id:
            return False
        return _in_range(row["basis_date"], ranges["basis_from"], ranges["basis_to"]) and _in_range(
            row["eligible_at"], ranges["eligible_from"], ranges["eligible_to"]
        )

    evaluated = [
        retention_rules.evaluate(db, a, retentions.get(a.id), at=now, record_type=record_type, version=version)
        for a in assessments
    ]
    summary = {code: 0 for code in retention_rules.ELIGIBILITY_STATUSES}
    for row in evaluated:
        summary[row["eligibility_status"]] += 1
    rows = [row for row in evaluated if keep(row)]
    rows.sort(key=lambda r: (r[sort] is None, r[sort] if r[sort] is not None else ""), reverse=(order == "desc"))
    total = len(rows)
    page_rows = rows[(page - 1) * page_size : page * page_size]

    filters = {
        "status": statuses,
        "record_type": record_type,
        "within_days": within_days,
        "legal_hold": legal_hold,
        "policy_version_id": policy_version_id,
        "include_deleted": include_deleted,
        **{key: (value.date().isoformat() if value else None) for key, value in ranges.items()},
    }
    log_audit_event(
        db=db,
        assessment_id=None,
        action=AuditAction.RETENTION_REPORT_VIEWED,
        actor=current_user.full_name or current_user.email,
        actor_id=current_user.id,
        details=(
            f"Retention eligibility report viewed (read-only): {total} matching record(s) of {len(evaluated)} in scope; "
            f"filters {json.dumps(filters, sort_keys=True)}. Policy status: {POLICY_STATUS}."
        ),
    )
    db.commit()
    return {
        "items": [{field: row[field] for field in _REPORT_FIELDS} for row in page_rows],
        "total": total,
        "page": page,
        "page_size": page_size,
        "summary": summary,
        "filters": filters,
        "active_policy": retention_rules.version_dict(db, version) if version else None,
        "evaluated_at": now.isoformat(),
        "read_only": True,
        "note": "Eligibility is a determination for controlled lifecycle review, not authorization to delete. Nothing is purged.",
        "policy_status": POLICY_STATUS,
    }


@router.get("/api/retention/legal-holds")
def list_legal_holds(
    request: Request,
    current_only: bool = False,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Current and historical (released) legal holds on the assessments
    the user can see -- retention administrators and report readers."""

    from app.api.assessments import _scope_assessments_for_user
    from app.auth.access import DELETED_ASSESSMENT_READERS
    from app.models.audit_trail import soft_deleted_assessment_ids

    if not retention_rules.can_view(current_user):
        raise _deny(db, current_user, request, "only retention administrators and report readers can list legal holds")
    query = _scope_assessments_for_user(db.query(Assessment), current_user)
    if current_user.role not in DELETED_ASSESSMENT_READERS:
        query = query.filter(Assessment.id.notin_(soft_deleted_assessment_ids()))
    visible = {a.id: a for a in query.all()}
    held_ids = {
        row.assessment_id
        for row in db.query(LegalHoldEvent.assessment_id).filter(LegalHoldEvent.assessment_id.in_(list(visible) or [-1]))
    }
    current = {
        row.assessment_id: row
        for row in db.query(AssessmentRetention).filter(AssessmentRetention.assessment_id.in_(list(held_ids) or [-1]))
    }
    items = []
    for assessment_id in sorted(held_ids):
        state = current.get(assessment_id)
        on_hold = bool(state and state.legal_hold)
        if current_only and not on_hold:
            continue
        items.append(
            {
                "assessment_id": assessment_id,
                "reference": visible[assessment_id].reference_id,
                "legal_hold": on_hold,
                "set_by": state.legal_hold_set_by if on_hold else None,
                "set_at": state.legal_hold_set_at.isoformat() if on_hold and state.legal_hold_set_at else None,
                "history": retention_rules.hold_history(db, assessment_id),
            }
        )
    return {"items": items, "policy_status": POLICY_STATUS}


# -- per-assessment retention and legal holds -----------------------------------


@router.get(
    "/api/assessments/{assessment_id}/retention",
    response_model=AssessmentRetentionResponse,
)
def get_assessment_retention(
    assessment_id: int, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)
):
    assessment = _get_assessment_or_404(db, assessment_id)
    retention_rules.ensure_seeded(db)
    retention = retention_rules.get_retention_row(db, assessment_id)
    db.commit()
    return _retention_response(db, assessment, retention, current_user)


@router.patch(
    "/api/assessments/{assessment_id}/retention/legal-hold",
    response_model=AssessmentRetentionResponse,
)
def set_legal_hold(
    assessment_id: int,
    payload: LegalHoldUpdate,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """R16.4: place (hold=true) or release (hold=false) a legal hold, each
    with a reason, recorded in the append-only hold history. While held, an
    assessment is never eligible and can't be soft-deleted."""

    assessment = _get_assessment_or_404(db, assessment_id)
    retention = retention_rules.get_retention_row(db, assessment_id)
    if payload.hold:
        args = (retention_rules.set_hold, db, assessment, retention, current_user, payload.reason, payload.matter_reference)
    else:
        args = (retention_rules.release_hold, db, assessment, retention, current_user, payload.reason)
    _guarded(db, current_user, request, *args, assessment_id=assessment_id)
    db.commit()
    db.refresh(retention)
    return _retention_response(db, assessment, retention, current_user)


@router.post(
    "/api/assessments/{assessment_id}/soft-delete",
    response_model=AssessmentRetentionResponse,
)
def soft_delete_assessment(
    assessment_id: int,
    payload: SoftDeleteRequest,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_admin_role),
):
    """
    R16.1/AC5: a controlled, logged soft delete -- no row is ever
    physically removed. Allowed only when the shared eligibility rule says
    ELIGIBLE (finally decided, retention period elapsed under the policy in
    force, no legal hold); records that policy version.
    """

    assessment = _get_assessment_or_404(db, assessment_id)
    retention_rules.ensure_seeded(db)
    retention = retention_rules.get_retention_row(db, assessment_id)
    _guarded(
        db, current_user, request, retention_rules.soft_delete,
        db, assessment, retention, current_user, payload.reason, assessment_id=assessment_id,
    )
    db.commit()
    db.refresh(retention)
    return _retention_response(db, assessment, retention, current_user)


@router.get("/api/assessments/{assessment_id}/explain")
def get_assessment_explanation(assessment_id: int, db: Session = Depends(get_db)):
    """R16.2: a clear explanation of how each rating was determined."""

    result = explain_assessment(db, assessment_id)
    if result is None:
        raise HTTPException(status_code=404, detail="Assessment not found")
    return result


@router.get("/api/assessments/{assessment_id}/explain/statements")
def get_assessment_statements(
    assessment_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Stage 19 (Explainability): every fact, assumption, recommendation and
    decision on the assessment, each tagged with its origin (person vs
    automated), author/model, time, review state and a reference to the
    record it came from. P4 (R5.4): and with its evidence category.
    Quotes from documents this viewer sees masked are masked (P1).
    """

    from app.api.assessments import _masked_document_ids
    from app.models.assessment import Assessment

    assessment = db.query(Assessment).filter(Assessment.id == assessment_id).first()
    result = classify_statements(db, assessment_id, _masked_document_ids(db, assessment, current_user))
    if result is None:
        raise HTTPException(status_code=404, detail="Assessment not found")
    return result


@router.get("/api/assessments/{assessment_id}/audit-export")
def export_audit_package(
    assessment_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_export_role),
):
    """R16.3: export a complete assessment package (every table row that
    references this assessment) as a downloadable JSON file."""

    package = build_audit_package(db, assessment_id)
    if package is None:
        raise HTTPException(status_code=404, detail="Assessment not found")

    log_audit_event(
        db=db,
        assessment_id=assessment_id,
        action=AuditAction.STATUS_CHANGE,
        actor=current_user.full_name or current_user.email,
        actor_id=current_user.id,
        details="Audit package exported.",
    )
    db.commit()

    body = json.dumps(package, indent=2, default=str)
    return Response(
        content=body,
        media_type="application/json",
        headers={
            "Content-Disposition": f'attachment; filename="assessment-{assessment_id}-audit-export.json"'
        },
    )

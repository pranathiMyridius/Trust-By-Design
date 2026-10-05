import logging
import os
from datetime import datetime, timedelta, timezone
from typing import Any
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi import Response as FastAPIResponse
from fastapi import File, UploadFile
from pydantic import BaseModel, field_validator
from sqlalchemy.orm import Session
from fastapi import Form
from app.database import get_db
from app.auth.access import ensure_assessment_visible
from app.auth.dependencies import get_current_user, log_denied_attempt, require_role
from app.models.user import User, UserRole

# Stage 10: the internal AI-analysis/scoring pipeline (analyze,
# advance-stage, risk-factor edits, manual score override, challenge,
# FCRM review, value overrides, request-information) is restricted to
# the FCRM Analyst role, plus Manager/Admin who can also act as one. A
# Business User keeps read-only access plus their own intake/draft
# actions (create, edit while draft, upload documents, confirm their
# profile, submit-to-manager) -- see api/approvals.py.
require_pipeline_role = require_role(
    UserRole.FCRM_ANALYST, UserRole.MANAGER, UserRole.ADMIN
)
from app.models.assessment import Assessment
from app.schemas.assessment import (
    AssessmentCreate,
    AssessmentResponse,
    AdvanceStageRequest,
    DegradedResultAcknowledgement,
    ManualScoreDraftSave,
)
from app.risk_engine.degraded import AssessmentMode
from app.langgraph.service import run_risk_assessment_workflow
from app.risk_engine.scoring import (
    calculate_occ_risk_profile,
    determine_risk_level,
    compute_factor_score,
    determine_risk_band,
)
from app.risk_engine.methodology import get_active_thresholds, get_methodology_config
from app.models.risk_result import RiskResult
from app.schemas.risk_result import RiskResultResponse
from app.models.risk_factor import RiskFactor
from app.schemas.risk_factor import (
    RiskFactorResponse,
    RiskFactorManualCreate,
    RiskFactorExclude,
    RiskFactorSuggestRatingsRequest,
    RiskFactorSuggestRatingsResponse,
    OccRiskProfileResponse,
)
from app.ai.likelihood_impact_analyzer import estimate_likelihood_impact
from app.models.inherent_risk_calculation import InherentRiskCalculation
from app.schemas.inherent_risk import (
    RiskFactorRatingUpdate,
    InherentRiskCalculationResponse,
    InherentRiskOverrideCreate,
)
from app.services.inherent_risk_service import recalculate_inherent_risk
from app.services.residual_risk_service import (
    compute_residual_risk,
    current_residual_calculation,
    recalculate_residual_risk,
    residual_response,
)
from app.models.assessment_draft import AssessmentDraft
from app.schemas.assessment_draft import (
    AssessmentDraftResponse,
    AssessmentDraftVersionSummary,
    AssessmentDraftUpdate,
    AssessmentDraftAccept,
)
from app.services.assessment_draft_service import generate_assessment_draft
from app.services.decision_lock import ensure_assessment_editable
from app.control_engine.engine import recompute_control_state
from app.models.assessment_document import AssessmentDocument
from app.file_processing.extractor import expand_uploads, extract_text
from app.schemas.assessment_document import (
    AssessmentDocumentResponse,
    DOCUMENT_TYPES,
    CONFIDENTIALITY_LEVELS,
)
from app.schemas.assessment_evidence import (
    EvidenceGapsResponse,
    EvidenceIssue,
    DocumentWarning,
)
from app.models.assessment_intelligence import AssessmentIntelligence
from app.schemas.assessment_intelligence import (
    AssessmentIntelligenceResponse,
    AssessmentIntelligenceUpdate,
    AssessmentIntelligenceConfirm,
    ConsistencyConflict,
)
from app.services.consistency_check import detect_inconsistencies
from app.models.audit_event import AuditEvent
from app.schemas.audit_event import (
    AuditEventResponse,
    CalculatorAuditCreate,
    ManualScoreOverrideCreate,
)
from app.services.audit_service import AuditAction, actor_name, log_audit_event
from app.services.risk_scoring import recalculate_assessment_score
from app.schemas.assessment import AssessmentUpdate
from app.file_processing.storage import content_disposition, read_file, save_file
from fastapi.responses import FileResponse, Response
from app.document_analysis.analyzer import analyze_document_text
from app.schemas.assessment_challenge import (
    AssessmentChallengeResponse,
    ChallengeFinding,
    ChallengeUpdate,
)
from app.models.assessment_challenge import AssessmentChallenge
from app.schemas.assessment_fcrm_review import (
    FcrmReviewResponse,
    FcrmReviewUpdate,
)
from app.models.assessment_fcrm_review import AssessmentFcrmReview
from app.schemas.assessment_override import OverrideApproval, OverrideCreate, OverrideResponse, OverrideReview
from app.models.assessment_override import AssessmentOverride
from app.services.reference_id import generate_reference_id
from app.services.triage import compute_priority
from app.langgraph import progress as stage_progress
from app.services.routing import compute_routing
from app.services.duplicate_detection import find_potential_duplicates
from app.schemas.duplicate_check import DuplicateMatch
from app.database import IS_POSTGRES
from app.services.document_embedding_service import index_text
from app.services.semantic_risk_search import find_similar_risk_context
from app.schemas.semantic_search import SimilarAssessmentMatch
from app.services import workflow
from app.services import processing_jobs
from app.models.processing_job import ProcessingJob, ProcessingJobStatus, ProcessingJobType
from app.schemas.processing_job import ProcessingJobResponse, build_processing_job_response
from app.services.data_masking import (
    can_open_original_file,
    mask_evidence_records,
    mask_sensitive_text,
    should_mask_document,
)
from app.services.advisory import ensure_advisory_wording, recommendation_notice
from app.governance import provenance as field_provenance
from app.services import evidence_currency, intake_history
from app.schemas.assessment_evidence import EvidenceAcknowledgementCreate
from app.schemas.risk_factor import RiskFactorIndicatorsUpdate
import json
import mimetypes

MIME_TYPES = {
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".doc": "application/msword",
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ".xls": "application/vnd.ms-excel",
    ".pdf": "application/pdf",
    ".txt": "text/plain",
    ".csv": "text/csv",
}

# Stage 19 (Scalability): uploads are bounded so one oversized file can't
# exhaust memory/disk. Configurable; default 50 MB per file.
MAX_UPLOAD_BYTES = int(float(os.getenv("MAX_UPLOAD_MB", "50")) * 1024 * 1024)


def ensure_upload_size(filename: str, content: bytes) -> None:
    if len(content) > MAX_UPLOAD_BYTES:
        raise HTTPException(
            status_code=413,
            detail=(
                f"{filename} is {len(content) / (1024 * 1024):.1f} MB, which is over the "
                f"{MAX_UPLOAD_BYTES // (1024 * 1024)} MB upload limit. Split or compress "
                "the document and try again."
            ),
        )


router = APIRouter(
    prefix="/api/assessments",
    tags=["Assessments"],
)

# R1.1/R1.4: the fields a request must have before it can be submitted
# (as opposed to saved as a draft). (field name, human-readable label).
# "evidence" is intentionally excluded -- it's not one of R1.1's listed
# intake fields, it's used later by the risk engine/stage pipeline.
MANDATORY_INTAKE_FIELDS = [
    ("title", "Assessment title"),
    ("change_type", "Business change type"),
    ("product_or_service_name", "Product or service name"),
    ("description", "Business description"),
    ("business_owner", "Business owner"),
    ("legal_entity", "Legal entity or business unit"),
    ("customer_segment", "Customer segment"),
    ("countries_jurisdictions", "Countries and jurisdictions involved"),
    ("delivery_channels", "Delivery channels"),
    ("expected_transaction_volume", "Expected transaction volume"),
    ("expected_transaction_value", "Expected transaction value"),
    ("transaction_types", "Transaction types"),
    ("third_party_vendor_usage", "Use of third parties or vendors"),
    ("technology_process_changes", "Technology or process changes"),
    ("expected_launch_date", "Expected launch or implementation date"),
]

# R1.2: the request types a new submission may use. Mirrors CHANGE_TYPES
# in frontend/src/api/assessments.ts; the legacy MATERIAL_CHANGE and
# THIRD_PARTY values stay readable on old records but can't be submitted.
SUPPORTED_CHANGE_TYPES = {
    "NEW_PRODUCT",
    "NEW_SERVICE",
    "NEW_CUSTOMER_SEGMENT",
    "NEW_GEOGRAPHY",
    "PROCESS_CHANGE",
    "TECHNOLOGY_CHANGE",
    "THIRD_PARTY_INTRODUCTION",
    "TRANSACTION_LIMIT_OR_CHANNEL_CHANGE",
    "PERIODIC_REASSESSMENT",
}


def _optional_form_bool(value: str | None) -> bool | None:
    """A multipart yes/no field: blank (not answered) stays None."""
    text = (value or "").strip().lower()
    if not text:
        return None
    if text in ("true", "1", "yes", "on"):
        return True
    if text in ("false", "0", "no", "off"):
        return False
    raise HTTPException(status_code=422, detail=f"Expected true or false, got {value!r}.")


def get_missing_mandatory_fields(data: dict) -> list[str]:
    """
    R1.4: returns the human-readable labels of every mandatory intake
    field that is missing/blank in `data`. Only meaningful when the
    request is being submitted (is_draft=False) -- a draft is exempt.
    """

    missing = []

    for field, label in MANDATORY_INTAKE_FIELDS:
        value = data.get(field)

        if not (isinstance(value, str) and value.strip()):
            missing.append(label)
        elif field == "change_type" and value not in SUPPORTED_CHANGE_TYPES:
            # R1.2 acceptance criteria: an unsupported change type can't be
            # submitted. Drafts (and records already stored with a legacy
            # type) are untouched -- this only runs on submission.
            missing.append(f"{label} (\"{value}\" is not a supported type)")

    return missing


def _record_extraction_provenance(
    intelligence: AssessmentIntelligence,
    structured_data,
    documents: list[AssessmentDocument],
) -> None:
    """P4 (R2.4): provenance of each extracted field, verified against the
    text of every document the extraction read (not only the first)."""

    now = datetime.now(timezone.utc)
    method = getattr(structured_data, "extraction_method", None) or "AI"
    intelligence.extraction_method = method
    intelligence.extracted_at = now
    intelligence.source_document_ids = json.dumps([document.id for document in documents])
    intelligence.field_provenance = field_provenance.dump(
        field_provenance.build_field_provenance(
            structured_data,
            field_provenance.sources_from_documents(documents),
            method=method,
            extracted_at=now,
        )
    )


def _snapshot_new_intake(db: Session, assessment: Assessment, user: User | None, trigger: str = "CREATED") -> None:
    """P4 (R3.4): the request as first saved, so the original stays
    available after any later correction."""

    intake_history.record(
        db,
        assessment.id,
        intake_history.ASSESSMENT_REQUEST,
        after=intake_history.request_values(assessment),
        trigger=trigger,
        user=user,
        reason="Request saved as a draft." if assessment.is_draft else "Request submitted.",
    )


def _snapshot_new_profile(db: Session, intelligence: AssessmentIntelligence, user: User | None, trigger: str, reason: str) -> None:
    intake_history.record(
        db,
        intelligence.assessment_id,
        intake_history.BUSINESS_PROFILE,
        after=intake_history.profile_values(intelligence),
        trigger=trigger,
        user=user,
        reason=reason,
    )


def _index_for_semantic_search(
    db: Session,
    assessment_id: int,
    text: str | None,
    document_id: int | None = None,
) -> None:
    """
    Best-effort: indexes `text` into pgvector for later semantic search
    (see app/services/semantic_risk_search.py). No-op on SQLite (no
    document_embeddings table exists there) and swallows any indexing
    failure -- this must never fail the document-upload/creation
    request it's called from.
    """

    if not IS_POSTGRES or not (text or "").strip():
        return

    try:
        index_text(db, assessment_id=assessment_id, text=text, document_id=document_id)
    except Exception:
        logging.getLogger(__name__).exception(
            "Semantic indexing failed for assessment %s (document %s)",
            assessment_id,
            document_id,
        )


def _apply_intake_triage_and_routing(
    db: Session, assessment: Assessment, retriage_reason: str | None = None
) -> None:
    """
    Automated screening/triage + routing (R1.1's Intake features): run
    once a request is actually submitted (is_draft flips to False), using
    the intake fields as they stand at that moment. Logs an ACKNOWLEDGMENT
    audit event carrying the reference id, priority and assigned team --
    the in-app equivalent of an intake confirmation, since this system has
    no outbound email/SMS integration.

    With `retriage_reason`, it instead re-scores an already-submitted
    request after an edit that changes its triage (e.g. the shell company
    indicator), and logs the change rather than a second acknowledgment.
    """

    data = {
        column: getattr(assessment, column)
        for column in (
            "change_type",
            "countries_jurisdictions",
            "third_party_vendor_usage",
            "expected_transaction_volume",
            "expected_transaction_value",
            "technology_process_changes",
            "shell_company_indicator",
        )
    }

    previous = (assessment.priority, assessment.priority_score)
    priority, priority_score = compute_priority(data)
    assigned_team, assigned_queue = compute_routing(data, priority)

    assessment.priority = priority
    assessment.priority_score = priority_score
    assessment.assigned_team = assigned_team
    assessment.assigned_queue = assigned_queue

    if retriage_reason is not None:
        # Priority scales the stage SLA; re-time the current stage from
        # when it was entered. The overall target date is left alone --
        # it may have been set by hand.
        days = workflow.sla_days_for(assessment.workflow_status, priority)
        entered = workflow.as_utc(assessment.status_entered_at)
        if days and entered:
            assessment.status_due_at = entered + timedelta(days=days)

        log_audit_event(
            db=db,
            assessment_id=assessment.id,
            action=AuditAction.STATUS_CHANGE,
            previous_status=assessment.status,
            new_status=assessment.status,
            details=(
                f"Priority re-triaged ({retriage_reason}): "
                f"{previous[0]} (score {previous[1]}) -> {priority} (score {priority_score}). "
                f"Routed to {assigned_team} ({assigned_queue})."
            ),
        )
        return

    log_audit_event(
        db=db,
        assessment_id=assessment.id,
        action=AuditAction.ACKNOWLEDGMENT,
        actor=assessment.submitted_by,
        details=(
            f"Intake acknowledged as {assessment.reference_id}. "
            f"Priority: {priority} (score {priority_score}). "
            f"Routed to {assigned_team} ({assigned_queue})."
        ),
    )


# The 7 tracked AI-analysis pipeline stages, in required order. "Business
# Request" (the CreateAssessment form) is deliberately not on this list —
# it happens before a record exists at all. HUMAN_REVIEW is the last
# stage this list covers: from there, the AW hierarchical approval
# workflow takes over (see app/api/approvals.py's submit-to-manager /
# manager-decision / committee-decision endpoints), replacing what used
# to be a single generic COMMITTEE_DECISION -> AUDIT step.
# One special, non-tracked status value exists outside this list:
#   - REMEDIATION: a transient marker set when a committee decision
#     outcome requires rework. It sits just before INTAKE in spirit (the
#     business owner must acknowledge/return to Intake to restart the
#     pipeline) but is kept as its own value rather than silently
#     collapsing into INTAKE, since existing UI/audit history and the
#     document-upload gate already treat REMEDIATION as a distinct,
#     meaningful state.
# Statuses in which the request fields may be edited / documents uploaded.
# RETURNED_BY_MANAGER is the manager's "amendment required": the owner fixes
# the request and its evidence, then resubmits.
REQUEST_EDITABLE_STATUSES = {"INTAKE", "REMEDIATION", "RETURNED_BY_MANAGER"}
DOCUMENT_UPLOAD_STATUSES = {"INTAKE", "EVIDENCE_COLLECTION", "REMEDIATION", "RETURNED_BY_MANAGER"}

STAGE_ORDER = [
    "INTAKE",
    "EVIDENCE_COLLECTION",
    "RISK_IDENTIFICATION",
    "INHERENT_RISK_ASSESSMENT",
    "CONTROL_ASSESSMENT",
    "RESIDUAL_RISK",
    "HUMAN_REVIEW",
]


# AW: which assessments a given role may see, mirroring the "Access
# level" rules in the requirement (a Business User sees only their own,
# a Manager only their team's, a Committee Member only committee-visible
# ones, an Admin sees everything).
COMMITTEE_VISIBLE_STATUSES = {
    "READY_FOR_COMMITTEE",
    "COMMITTEE_REVIEW",
    "CLOSED",
    "DEFERRED",
    "APPROVED",
    "APPROVED_WITH_CONDITIONS",
    "REJECTED",
}


def _delegated_visibility(session, current_user: User):
    """
    AW.7: what `current_user` can see through the approval delegations
    they currently hold, as a filter condition, or None. Only Managers
    can be delegates (app/services/delegation.py), so only that branch
    below uses it.
    """

    from sqlalchemy import or_

    from app.services import delegation as delegation_rules

    conditions = []
    for grant in delegation_rules.active_delegations_received(session, current_user):
        if grant.scope_type == delegation_rules.SCOPE_ASSESSMENT:
            conditions.append(Assessment.id == grant.scope_assessment_id)
        elif grant.authority == delegation_rules.AUTHORITY_MANAGER_APPROVAL:
            conditions.append(Assessment.manager_id == grant.delegator_id)
        else:
            conditions.append(Assessment.status.in_(COMMITTEE_VISIBLE_STATUSES))
    return or_(*conditions) if conditions else None


def _entity_scope_condition(current_user: User):
    """
    R15.4: the user's legal-entity / business-unit / country restriction
    as a filter condition, or None when unrestricted. Each configured
    dimension must match (AND); within one, any listed value (OR). An
    Admin is never restricted.
    """

    from sqlalchemy import and_, func, or_

    if current_user.role == UserRole.ADMIN.value or not current_user.is_scoped:
        return None

    conditions = []
    entities = current_user.get_scope("scope_legal_entities")
    if entities:
        conditions.append(func.lower(Assessment.legal_entity).in_([value.lower() for value in entities]))
    units = current_user.get_scope("scope_business_units")
    if units:
        conditions.append(func.lower(Assessment.business_unit).in_([value.lower() for value in units]))
    countries = current_user.get_scope("scope_countries")
    if countries:
        # Free text on the request ("Germany, Poland"), so matched by name.
        conditions.append(
            or_(*(func.lower(Assessment.countries_jurisdictions).contains(value.lower()) for value in countries))
        )
    return and_(*conditions)


def _role_visibility(query, current_user: User):
    if current_user.role in {
        UserRole.ADMIN.value,
        UserRole.FCRM_ANALYST.value,
        UserRole.AUDITOR.value,
        UserRole.EXECUTIVE.value,
    }:
        # Stage 10: the FCRM Analyst reviews any assessment moving through
        # the pipeline, not just ones tied to their own management chain.
        # R15.1: an Auditor and a Read-only Executive see everything (and
        # change nothing -- app/auth/access.py).
        return query

    # P3 (provisional): people who govern every case -- the Head of FCRM,
    # the FCRM Governance Owner, a Compliance Manager, the Committee Chair
    # and QA / challenge reviewers -- need to see the cases they approve
    # exceptions and overrides for, or review. Only when the designation is
    # held with a permitted base role; R15.4 entity scope still applies.
    from app.governance.policy import (
        CHALLENGE_REVIEWER,
        COMMITTEE_CHAIR,
        COMPLIANCE_MANAGER,
        FCRM_GOVERNANCE_OWNER,
        HEAD_OF_FCRM,
        QA_REVIEWER,
        holds,
    )

    if holds(current_user, {"designations": [HEAD_OF_FCRM, FCRM_GOVERNANCE_OWNER, COMPLIANCE_MANAGER, COMMITTEE_CHAIR, QA_REVIEWER, CHALLENGE_REVIEWER]}):
        return query

    if current_user.role == UserRole.MANAGER.value:
        visible = (Assessment.manager_id == current_user.id) | (Assessment.owner_id == current_user.id)
        delegated = _delegated_visibility(query.session, current_user)
        return query.filter(visible if delegated is None else (visible | delegated))

    if current_user.role == UserRole.COMMITTEE_MEMBER.value:
        return query.filter(
            (Assessment.status.in_(COMMITTEE_VISIBLE_STATUSES))
            | (Assessment.owner_id == current_user.id)
        )

    if current_user.role == UserRole.CONTROL_OWNER.value:
        # Stage 15: the assessments where a control or an action is
        # assigned to this person (by name or email -- both are free text).
        from sqlalchemy import func, or_

        from app.models.action_item import ActionItem
        from app.models.control import Control

        names = [name.lower() for name in (current_user.full_name, current_user.email) if name]
        owns_control = (
            query.session.query(Control.assessment_id)
            .filter(Control.is_current.is_(True), func.lower(Control.owner).in_(names))
        )
        owns_action = query.session.query(ActionItem.assessment_id).filter(func.lower(ActionItem.owner).in_(names))
        return query.filter(
            or_(
                Assessment.owner_id == current_user.id,
                Assessment.id.in_(owns_control),
                Assessment.id.in_(owns_action),
            )
        )

    # BUSINESS_USER, POLICY_ADMIN (and any unrecognized role): only their own.
    return query.filter(Assessment.owner_id == current_user.id)


def _scope_assessments_for_user(query, current_user: User):
    query = _role_visibility(query, current_user)

    # R15.4: on top of the role's visibility, a scoped user only sees
    # assessments in their entities / units / countries -- except ones
    # they raised themselves.
    scope = _entity_scope_condition(current_user)
    if scope is not None:
        query = query.filter(scope | (Assessment.owner_id == current_user.id))
    return query


@router.post(
    "",
    response_model=AssessmentResponse,
    status_code=201,
)
def create_assessment(
    assessment_data: AssessmentCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    data = assessment_data.model_dump()

    # R1.4: validate mandatory fields before allowing a submission (not a
    # draft) to go through.
    if not data["is_draft"]:
        missing_fields = get_missing_mandatory_fields(data)

        if missing_fields:
            raise HTTPException(
                status_code=422,
                detail={
                    "message": (
                        "Missing mandatory information. Complete these "
                        "fields before submitting, or save as a draft."
                    ),
                    "missing_fields": missing_fields,
                },
            )

    # model_dump() carries over every field on AssessmentCreate, including
    # the R1.1 request fields (product/service name, business owner,
    # legal entity, countries, channels, transaction details, etc.) —
    # each one maps 1:1 to a column on the Assessment model.
    assessment = Assessment(**data)
    assessment.owner_id = current_user.id
    # R1.5: the submitter is the signed-in user, never a client-supplied name.
    assessment.submitted_by = actor_name(current_user)

    # R1.5: stamp the submission moment. Left null while still a draft.
    if not assessment.is_draft:
        assessment.submitted_at = datetime.now(timezone.utc)

    db.add(assessment)
    db.flush()

    # Intake "front door": generate the case reference id as soon as a
    # record exists, regardless of draft status, so it can be quoted back
    # to the submitter/used to track the request from the start.
    assessment.reference_id = generate_reference_id(assessment.id)
    _snapshot_new_intake(db, assessment, current_user)

    log_audit_event(
        db=db,
        assessment_id=assessment.id,
        action=AuditAction.CREATED,
        previous_status=None,
        new_status=assessment.status,
        actor=assessment.submitted_by,
        actor_id=current_user.id,
        details=(
            "Assessment saved as a draft."
            if assessment.is_draft
            else "Assessment submitted manually."
        ),
    )

    if not assessment.is_draft:
        _apply_intake_triage_and_routing(db, assessment)

    workflow.record_creation(
        db,
        assessment,
        current_user,
        "Request saved as a draft." if assessment.is_draft else "Request created and submitted.",
    )

    db.commit()
    db.refresh(assessment)

    return assessment


@router.get(
    "",
    response_model=list[AssessmentResponse],
)
def get_assessments(
    response: FastAPIResponse,
    # Stage 19 (Scalability): optional filters and paging. With none of
    # them given this still returns the full visible list, exactly as
    # before; with `limit` the total match count is sent back in the
    # X-Total-Count header so the UI can page through large volumes.
    legal_entity: str | None = None,
    business_unit: str | None = None,
    status: str | None = None,
    search: str | None = None,
    limit: int | None = Query(None, ge=1, le=500),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    # Stage 14 (R14.4): escalate anything that has gone overdue since the
    # last look, so list views never show a stale "on track".
    workflow.escalate_overdue_assessments(db)

    # Stage 16 (R16.1): a soft-deleted assessment (see
    # app/api/audit_trail.py's /soft-delete) is hidden from normal list
    # views, but its row and all history are never removed.
    from app.models.audit_trail import AssessmentRetention

    deleted_ids = {
        row.assessment_id
        for row in db.query(AssessmentRetention.assessment_id).filter(
            AssessmentRetention.is_deleted.is_(True)
        )
    }

    query = db.query(Assessment)
    query = _scope_assessments_for_user(query, current_user)
    if deleted_ids:
        query = query.filter(Assessment.id.notin_(deleted_ids))
    if legal_entity:
        query = query.filter(Assessment.legal_entity == legal_entity)
    if business_unit:
        query = query.filter(Assessment.business_unit == business_unit)
    if status:
        query = query.filter(Assessment.status == status)
    if search and search.strip():
        pattern = f"%{search.strip()}%"
        query = query.filter(
            Assessment.title.ilike(pattern)
            | Assessment.reference_id.ilike(pattern)
            | Assessment.product_or_service_name.ilike(pattern)
        )

    query = query.order_by(Assessment.created_at.desc())

    if limit is not None:
        response.headers["X-Total-Count"] = str(query.count())
        query = query.offset(offset).limit(limit)

    return query.all()


@router.get("/org-units")
def get_org_units(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Stage 19 (Scalability): the distinct legal entities and business
    units across the assessments this user can see, with counts -- feeds
    the list filters. Registered ahead of GET /{assessment_id}.
    """

    from sqlalchemy import func

    scoped = _scope_assessments_for_user(db.query(Assessment), current_user).subquery()

    def _counts(column):
        rows = (
            db.query(column, func.count())
            .select_from(scoped)
            .filter(column.isnot(None), column != "")
            .group_by(column)
            .order_by(column)
            .all()
        )
        return [{"name": name, "assessment_count": count} for name, count in rows]

    return {
        "legal_entities": _counts(scoped.c.legal_entity),
        "business_units": _counts(scoped.c.business_unit),
    }


@router.get(
    "/check-duplicates",
    response_model=list[DuplicateMatch],
)
def check_duplicate_assessments(
    product_or_service_name: str = "",
    legal_entity: str = "",
    business_owner: str = "",
    exclude_id: int | None = None,
    db: Session = Depends(get_db),
):
    """
    Identity search / de-duplication (Intake feature): looks for existing
    assessments that plausibly describe the same request, so the
    CreateAssessment form can warn the user before they create a
    duplicate record. Registered ahead of GET /{assessment_id} so
    "check-duplicates" is matched as its own route, not an assessment id.
    """

    return find_potential_duplicates(
        db=db,
        product_or_service_name=product_or_service_name,
        legal_entity=legal_entity,
        business_owner=business_owner,
        exclude_id=exclude_id,
    )


@router.get(
    "/{assessment_id}",
    response_model=AssessmentResponse,
)
def get_assessment(
    assessment_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    query = db.query(Assessment).filter(Assessment.id == assessment_id)

    assessment = query.first()

    if not assessment:
        raise HTTPException(
            status_code=404,
            detail="Assessment not found",
        )

    visible = _scope_assessments_for_user(query, current_user).first()

    if not visible:
        raise HTTPException(
            status_code=403,
            detail="You do not have access to this assessment.",
        )

    return assessment


@router.patch(
    "/{assessment_id}/status",
    response_model=AssessmentResponse,
)
def update_assessment_status(
    assessment_id: int,
    status: str,
    reason: str | None = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_pipeline_role),
):
    assessment = (
        db.query(Assessment)
        .filter(Assessment.id == assessment_id)
        .first()
    )

    if not assessment:
        raise HTTPException(
            status_code=404,
            detail="Assessment not found",
        )
    # Kept for backward compatibility with older clients. New code should
    # use PATCH /{assessment_id}/advance-stage (pipeline stages) or the
    # Stage 14 workflow endpoints in api/workflow.py. Every move made
    # here is still validated against the central transition table in
    # app/services/workflow.py, so this can't be used to skip stages --
    # in practice only REMEDIATION -> INTAKE is reachable through it.
    allowed_statuses = {
        "UNDER_REVIEW",
        "APPROVED",
        "REMEDIATION",
        "REJECTED",
        "INTAKE",
    }

    if status not in allowed_statuses:
        raise HTTPException(
            status_code=400,
            detail="Invalid assessment status",
        )

    previous_status = assessment.status

    workflow.transition(
        db,
        assessment,
        status,
        user=current_user,
        reason=reason or f"Status changed from {previous_status} to {status}.",
        action="STATUS_UPDATE",
    )

    # Use a more specific audit action for final decisions
    status_to_action = {
    "APPROVED": AuditAction.APPROVAL,
    "REMEDIATION": AuditAction.REMEDIATION,
    "REJECTED": AuditAction.REJECTION,
    }
    action = status_to_action.get(status, AuditAction.STATUS_CHANGE)

    log_audit_event(
        db=db,
        assessment_id=assessment.id,
        action=action,
        previous_status=previous_status,
        new_status=status,
        actor=current_user.full_name or current_user.email,
        actor_id=current_user.id,
        details=(
            f"Status changed from "
            f"{previous_status} to {status}."
        ),
    )

    db.commit()
    db.refresh(assessment)

    return assessment

def run_assessment_analysis(
    db: Session,
    assessment_id: int,
    current_user: User | None,
) -> Assessment:
    """
    Run the assessment through the LangGraph orchestration layer.

    LangGraph currently orchestrates the existing deterministic risk
    engine; it does not duplicate or replace the business rules.
    Shared by the synchronous POST /analyze and the Stage 19 background
    POST /analyze-async (see app/services/processing_jobs.py).
    """
    draft_assessment = (
        db.query(Assessment)
        .filter(Assessment.id == assessment_id)
        .first()
    )

    if not draft_assessment:
        raise HTTPException(status_code=404, detail="Assessment not found")

    if not (draft_assessment.description or "").strip() or not (
        draft_assessment.evidence or ""
    ).strip():
        raise HTTPException(
            status_code=400,
            detail=(
                "This is still a draft. Add a description and evidence "
                "before starting analysis."
            ),
        )

    # Stage 14: validate the implied status move before the (slow) risk
    # engine runs, so a draft or a closed assessment is rejected up front.
    if draft_assessment.status in {"INTAKE", "EVIDENCE_COLLECTION", "REMEDIATION"}:
        workflow.check_transition(
            db, draft_assessment, "RISK_IDENTIFICATION", user=current_user
        )

        # R3.3: same gate as advance-stage -- risk identification never
        # runs on an unconfirmed profile, whichever endpoint starts it.
        intelligence = (
            db.query(AssessmentIntelligence)
            .filter(AssessmentIntelligence.assessment_id == draft_assessment.id)
            .first()
        )
        if not (intelligence and intelligence.confirmed):
            raise HTTPException(status_code=400, detail=PROFILE_NOT_CONFIRMED_DETAIL)
    elif draft_assessment.workflow_status == workflow.WorkflowStatus.CLOSED.value:
        raise HTTPException(status_code=409, detail="This assessment is closed.")

    # P4 (R2.6): no analysis while an expired document is undecided.
    evidence_currency.ensure_no_unacknowledged(db, draft_assessment.id)

    try:
        run_risk_assessment_workflow(
            assessment_id=assessment_id,
            db=db,
        )
    except ValueError as exc:
        db.rollback()
        raise HTTPException(
            status_code=404,
            detail=str(exc),
        )
    except Exception as exc:
        db.rollback()
        raise HTTPException(
            status_code=500,
            detail=f"Risk assessment workflow failed: {str(exc)}",
        )

    assessment = (
        db.query(Assessment)
        .filter(Assessment.id == assessment_id)
        .first()
    )

    if not assessment:
        raise HTTPException(
            status_code=404,
            detail="Assessment not found",
        )

    # Legacy convenience: calling /analyze directly (outside the
    # advance-stage flow) still moves an early-stage assessment forward to
    # RISK_IDENTIFICATION, since the risk engine has now actually run.
    if assessment.status in {"INTAKE", "EVIDENCE_COLLECTION", "REMEDIATION"}:
        previous_status = assessment.status
        workflow.transition(
            db,
            assessment,
            "RISK_IDENTIFICATION",
            user=current_user,
            reason="Risk analysis run; assessment moved to Risk Identification.",
            action="ANALYZE",
        )
        log_audit_event(
            db=db,
            assessment_id=assessment.id,
            action=AuditAction.STAGE_ADVANCED,
            previous_status=previous_status,
            new_status="RISK_IDENTIFICATION",
            details="Risk identification completed via /analyze.",
        )
        db.commit()
        db.refresh(assessment)

    return assessment




@router.post(
    "/{assessment_id}/analyze",
    response_model=AssessmentResponse,
)
def analyze_assessment(
    assessment_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_pipeline_role),
):
    return run_assessment_analysis(db, assessment_id, current_user)


@router.post(
    "/{assessment_id}/analyze-async",
    response_model=ProcessingJobResponse,
    status_code=202,
)
def analyze_assessment_async(
    assessment_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_pipeline_role),
):
    """
    Stage 19 (Performance): same as POST /analyze, but returns a
    ProcessingJob immediately and runs the (slow) workflow in the
    background, so the UI stays responsive and can show progress.
    Poll GET /api/processing-jobs/{id}.
    """

    assessment = db.query(Assessment).filter(Assessment.id == assessment_id).first()
    if not assessment:
        raise HTTPException(status_code=404, detail="Assessment not found")

    active = (
        db.query(ProcessingJob)
        .filter(
            ProcessingJob.assessment_id == assessment_id,
            ProcessingJob.job_type == ProcessingJobType.RISK_ANALYSIS.value,
            ProcessingJob.status.in_(
                [ProcessingJobStatus.QUEUED.value, ProcessingJobStatus.RUNNING.value]
            ),
        )
        .first()
    )
    if active:
        return build_processing_job_response(active)

    job = processing_jobs.create_job(
        db,
        ProcessingJobType.RISK_ANALYSIS,
        assessment_id=assessment_id,
        user=current_user,
    )
    db.commit()
    processing_jobs.enqueue(job.id)
    db.refresh(job)
    return build_processing_job_response(job)


DOCUMENT_UPLOAD_EXTENSIONS = {
    ".docx",
    ".doc",
    ".xlsx",
    ".pdf",
    ".txt",
    ".csv",
    ".zip",
}


async def _read_and_expand_uploads(
    files: list[UploadFile],
) -> list[tuple[str, bytes]]:
    """
    Validates each uploaded file's extension, reads its bytes, and
    expands any .zip into its supported member documents -- so callers
    always get back a flat list of (filename, content) pairs regardless
    of whether the caller sent several files, a zip of several files, or
    a mix of both.
    """

    documents: list[tuple[str, bytes]] = []

    for file in files:
        filename = file.filename or ""
        extension = (
            "." + filename.split(".")[-1].lower() if "." in filename else ""
        )

        if extension not in DOCUMENT_UPLOAD_EXTENSIONS:
            raise HTTPException(
                status_code=400,
                detail=(
                    f"Unsupported file type: {filename}. Supported files: "
                    "DOCX, DOC, XLSX, PDF, TXT, CSV, or a ZIP of these."
                ),
            )

        file_content = await file.read()

        if not file_content:
            raise HTTPException(
                status_code=400,
                detail=f"Uploaded file is empty: {filename}.",
            )

        ensure_upload_size(filename, file_content)

        documents.extend(expand_uploads(filename, file_content))

    if not documents:
        raise HTTPException(
            status_code=400,
            detail="No supported documents were found in the upload.",
        )

    return documents


@router.post("/analyze-document")
async def analyze_uploaded_document(
    files: list[UploadFile] = File(...),
):
    documents = await _read_and_expand_uploads(files)

    try:
        extracted_sections = []
        preview_sources = []

        for doc_filename, doc_content in documents:
            text = extract_text(filename=doc_filename, file_content=doc_content)

            if text.strip():
                extracted_sections.append(f"--- {doc_filename} ---\n{text}")
                preview_sources.append(field_provenance.SourceText(None, None, doc_filename, text))

        combined_text = "\n\n".join(extracted_sections)

        if not combined_text.strip():
            raise HTTPException(
                status_code=400,
                detail=(
                    "No readable text was found "
                    "in the uploaded file(s)."
                ),
            )

        from app.document_analysis.analyzer import (
            analyze_document_text,
        )

        structured_data = analyze_document_text(
            combined_text
        )

        # P4 (R2.4): provenance shown on the review screen before anything
        # is saved; recomputed against the stored documents on creation.
        return {
            "filenames": [name for name, _ in documents],
            "extracted_text": combined_text,
            "assessment": structured_data.model_dump(),
            "field_provenance": field_provenance.build_field_provenance(
                structured_data,
                preview_sources,
                method=structured_data.extraction_method,
            ),
        }

    except HTTPException:
        raise

    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=(
                f"Unable to analyze document: {str(exc)}"
            ),
        )


@router.post(
    "/create-from-document",
    response_model=AssessmentResponse,
)
async def create_assessment_from_document(
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    allowed_extensions = {
        ".docx",
        ".doc",
        ".xlsx",
        ".pdf",
        ".txt",
        ".csv",
    }

    filename = file.filename or ""

    extension = (
        "." + filename.split(".")[-1].lower()
        if "." in filename
        else ""
    )

    if extension not in allowed_extensions:
        raise HTTPException(
            status_code=400,
            detail=(
                "Unsupported file type. "
                "Supported files: DOCX, DOC, XLSX, PDF, TXT, CSV."
            ),
        )

    file_content = await file.read()

    if not file_content:
        raise HTTPException(
            status_code=400,
            detail="Uploaded file is empty.",
        )

    try:
        # -----------------------------------------
        # 1. Extract text from document
        # -----------------------------------------
        extracted_text = extract_text(
            filename=filename,
            file_content=file_content,
        )

        if not extracted_text.strip():
            raise HTTPException(
                status_code=400,
                detail="No readable text was found in the uploaded file.",
            )

        # -----------------------------------------
        # 2. Analyze extracted document
        # -----------------------------------------
        from app.document_analysis.analyzer import analyze_document_text

        structured_data = analyze_document_text(
            extracted_text
        )

        # -----------------------------------------
        # 3. Create Assessment
        # -----------------------------------------
        assessment = Assessment(
            title=structured_data.title,
            change_type=structured_data.change_type,
            description=structured_data.business_description,
            evidence=structured_data.evidence,
            status="INTAKE",
            owner_id=current_user.id,
        )

        db.add(assessment)

        # Get assessment.id before creating child records
        db.flush()

        # -----------------------------------------
        # 4. Save source document
        # -----------------------------------------
        document = AssessmentDocument(
            assessment_id=assessment.id,
            filename=filename,
            file_type=extension,
            extracted_text=extracted_text,
        )

        db.add(document)
        db.flush()

        # -----------------------------------------
        # 5. Save extracted intelligence
        # -----------------------------------------
        intelligence = AssessmentIntelligence(
            assessment_id=assessment.id,
            business_line=structured_data.business_line,
            transaction_volume=structured_data.transaction_volume,
            average_transaction_size=structured_data.average_transaction_size,
            maximum_transaction_limit=structured_data.maximum_transaction_limit,
            source_document_id=document.id,
            raw_extraction=json.dumps(structured_data.model_dump()),
        )

        intelligence.set_list(
            "channels",
            structured_data.channels,
        )

        intelligence.set_list(
            "countries",
            structured_data.countries,
        )

        intelligence.set_list(
            "customer_segments",
            structured_data.customer_segments,
        )

        intelligence.set_list(
            "third_party_vendors",
            structured_data.third_party_vendors,
        )

        intelligence.set_list(
            "data_shared",
            structured_data.data_shared,
        )

        intelligence.set_list(
            "technologies",
            structured_data.technologies,
        )

        intelligence.set_list(
            "regulatory_considerations",
            structured_data.regulatory_considerations,
        )

        intelligence.set_list(
            "existing_controls",
            structured_data.existing_controls,
        )

        intelligence.set_list(
            "additional_risk_factors",
            structured_data.additional_risk_factors,
        )

        _record_extraction_provenance(intelligence, structured_data, [document])
        db.add(intelligence)
        db.flush()
        _snapshot_new_intake(db, assessment, current_user)
        _snapshot_new_profile(
            db, intelligence, current_user, "PROFILE_EXTRACTED", f"Profile extracted from {filename}."
        )

        _index_for_semantic_search(
            db, assessment.id, extracted_text, document_id=document.id
        )

        # -----------------------------------------
        # 6. Log audit event for creation
        # -----------------------------------------
        log_audit_event(
            db=db,
            assessment_id=assessment.id,
            action=AuditAction.CREATED,
            previous_status=None,
            new_status=assessment.status,
            details=f"Assessment created from uploaded document: {filename}.",
        )

        workflow.record_creation(
            db,
            assessment,
            current_user,
            f"Draft request created from uploaded document: {filename}.",
        )

        # -----------------------------------------
        # 7. Save everything
        # -----------------------------------------
        db.commit()

        # Refresh assessment from database
        db.refresh(assessment)

        # -----------------------------------------
        # 8. IMPORTANT: return assessment
        # -----------------------------------------
        return assessment

    except HTTPException:
        db.rollback()
        raise

    except Exception as exc:
        db.rollback()

        raise HTTPException(
            status_code=500,
            detail=(
                "Unable to create assessment from document: "
                f"{str(exc)}"
            ),
        )


@router.get(
    "/{assessment_id}/documents",
    response_model=list[AssessmentDocumentResponse],
)
def get_assessment_documents(
    assessment_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    documents = (
        db.query(AssessmentDocument)
        .filter(
            AssessmentDocument.assessment_id == assessment_id
        )
        .order_by(AssessmentDocument.created_at.desc())
        .all()
    )

    assessment = db.query(Assessment).filter(Assessment.id == assessment_id).first()
    owner_id = assessment.owner_id if assessment else None

    return [
        _build_document_response(db, document, current_user, owner_id)
        for document in documents
    ]


def _latest_document_job(db: Session, document_id: int) -> ProcessingJob | None:
    return (
        db.query(ProcessingJob)
        .filter(ProcessingJob.document_id == document_id)
        .order_by(ProcessingJob.id.desc())
        .first()
    )


def _build_document_response(
    db: Session,
    document: AssessmentDocument,
    current_user: User,
    owner_id: int | None,
) -> AssessmentDocumentResponse:
    response = AssessmentDocumentResponse.model_validate(document)

    # Stage 19 (Security -- data masking).
    if should_mask_document(document.confidentiality, current_user, owner_id):
        response.extracted_text = mask_sensitive_text(document.extracted_text) or ""
        response.is_masked = True
    response.can_open_original = can_open_original_file(document.confidentiality, current_user, owner_id)

    # Stage 19 (Reliability): surface background processing state.
    job = _latest_document_job(db, document.id)
    if job is not None:
        response.processing = build_processing_job_response(job)

    return response


@router.get(
    "/{assessment_id}/evidence-gaps",
    response_model=EvidenceGapsResponse,
)
def get_assessment_evidence_gaps(
    assessment_id: int,
    db: Session = Depends(get_db),
):
    """
    R2.5: identifies missing evidence/information for this assessment
    and surfaces it as a visible checklist of issues, so a gap is a
    concrete named item (e.g. "missing beneficial ownership
    information") rather than a single blanket "incomplete" flag.
    R2.6: also flags any is_current document that's expired, so it
    gets a visible warning before being relied on as current evidence.
    """

    assessment = (
        db.query(Assessment)
        .filter(Assessment.id == assessment_id)
        .first()
    )

    if not assessment:
        raise HTTPException(status_code=404, detail="Assessment not found")

    intelligence = (
        db.query(AssessmentIntelligence)
        .filter(AssessmentIntelligence.assessment_id == assessment_id)
        .first()
    )

    documents = (
        db.query(AssessmentDocument)
        .filter(
            AssessmentDocument.assessment_id == assessment_id,
            AssessmentDocument.is_current.is_(True),
        )
        .all()
    )

    issues = _compute_evidence_gaps(assessment, intelligence, documents)
    document_warnings = _compute_document_warnings(db, documents)

    return EvidenceGapsResponse(
        assessment_id=assessment_id,
        issues=issues,
        open_count=sum(1 for issue in issues if issue.missing),
        document_warnings=document_warnings,
        acknowledgements_required=sum(1 for w in document_warnings if w.state == "ACKNOWLEDGEMENT_REQUIRED"),
    )


# Who may decide whether an expired document is used: the owner (who
# collects the evidence) and the pipeline roles. Read-only roles are
# refused by the global gate.
EVIDENCE_ACKNOWLEDGER_ROLES = {UserRole.FCRM_ANALYST.value, UserRole.MANAGER.value, UserRole.ADMIN.value}


@router.post(
    "/{assessment_id}/documents/{document_id}/expiry-acknowledgement",
    response_model=DocumentWarning,
    status_code=201,
)
def acknowledge_expired_document(
    assessment_id: int,
    document_id: int,
    payload: EvidenceAcknowledgementCreate,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    P4 (R2.6): before an expired document is used as current evidence, a
    person decides: USE_AS_EVIDENCE (with a reason) or
    EXCLUDE_FROM_EVIDENCE. Append-only; a later decision replaces the one
    in force but never the history.
    """

    from app.models.evidence_traceability import EvidenceAcknowledgement

    assessment = db.query(Assessment).filter(Assessment.id == assessment_id).first()
    if not assessment:
        raise HTTPException(status_code=404, detail="Assessment not found")
    document = (
        db.query(AssessmentDocument)
        .filter(AssessmentDocument.id == document_id, AssessmentDocument.assessment_id == assessment_id)
        .first()
    )
    if not document:
        raise HTTPException(status_code=404, detail="Document not found on this assessment.")

    if current_user.id != assessment.owner_id and current_user.role not in EVIDENCE_ACKNOWLEDGER_ROLES:
        log_denied_attempt(db, current_user, request, "expired-evidence acknowledgement", assessment_id=assessment_id)
        db.commit()
        raise HTTPException(
            status_code=403,
            detail="Only the assessment owner or an FCRM analyst, manager or administrator can decide on expired evidence.",
        )
    ensure_assessment_editable(assessment)

    decision = (payload.decision or "").strip().upper()
    if decision not in evidence_currency.DECISIONS:
        raise HTTPException(status_code=422, detail="decision must be USE_AS_EVIDENCE or EXCLUDE_FROM_EVIDENCE.")
    reason = (payload.reason or "").strip()
    if len(reason) < evidence_currency.MIN_REASON_LENGTH:
        raise HTTPException(
            status_code=422,
            detail=f"Give a reason of at least {evidence_currency.MIN_REASON_LENGTH} characters.",
        )

    condition = evidence_currency.expiry_condition(document)
    if condition == evidence_currency.SUPERSEDED:
        raise HTTPException(status_code=409, detail="This version has been superseded; it is never used as current evidence.")
    if condition == evidence_currency.CURRENT:
        raise HTTPException(status_code=409, detail="This document has not expired; no acknowledgement is needed.")

    previous = evidence_currency.latest_acknowledgement(db, document)
    db.add(
        EvidenceAcknowledgement(
            assessment_id=assessment_id,
            document_id=document.id,
            document_version=document.version,
            expiry_date=document.expiry_date,
            condition=condition,
            decision=decision,
            reason=reason,
            acknowledged_by=actor_name(current_user),
            acknowledged_by_id=current_user.id,
        )
    )
    log_audit_event(
        db=db,
        assessment_id=assessment_id,
        action=AuditAction.EVIDENCE_EXPIRY_ACKNOWLEDGED,
        actor=actor_name(current_user),
        actor_id=current_user.id,
        details=(
            f"{document.filename} v{document.version} (expiry {document.expiry_date or 'unreadable'}, {condition}): "
            f"{decision}"
            + (f", replacing {previous.decision}" if previous else "")
            + f". Reason: {reason}"
        ),
    )
    db.commit()
    return _compute_document_warnings(db, [document])[0]


def _compute_evidence_gaps(
    assessment: Assessment,
    intelligence: AssessmentIntelligence | None,
    documents: list[AssessmentDocument],
) -> list[EvidenceIssue]:
    combined_text = " ".join(
        filter(
            None,
            [
                assessment.evidence,
                assessment.description,
                *(document.extracted_text for document in documents),
            ],
        )
    ).lower()

    document_types = {document.document_type for document in documents}

    has_vendors = bool(
        (assessment.third_party_vendor_usage or "").strip()
        and (assessment.third_party_vendor_usage or "").strip().lower()
        not in {"none", "n/a", "na"}
    )

    existing_controls = intelligence.get_list("existing_controls") if intelligence else []

    issues = [
        EvidenceIssue(
            code="COUNTRIES",
            title="Countries and jurisdictions",
            description="At least one country/jurisdiction must be captured for this change.",
            missing=not (
                (assessment.countries_jurisdictions or "").strip()
                or (intelligence and intelligence.get_list("countries"))
            ),
        ),
        EvidenceIssue(
            code="TRANSACTION_VOLUME",
            title="Transaction volumes",
            description="Expected transaction volume/value must be captured.",
            missing=not (
                (assessment.expected_transaction_volume or "").strip()
                or (assessment.expected_transaction_value or "").strip()
                or (intelligence and (intelligence.transaction_volume or "").strip())
            ),
        ),
        EvidenceIssue(
            code="CUSTOMER_INFORMATION",
            title="Customer information",
            description="Customer segment/type information must be captured.",
            missing=not (
                (assessment.customer_segment or "").strip()
                or (intelligence and intelligence.get_list("customer_segments"))
            ),
        ),
        EvidenceIssue(
            code="VENDOR_CONTROLS",
            title="Vendor controls",
            description=(
                "A third party/vendor is in use, but no vendor document or "
                "control description has been uploaded for it."
            ),
            missing=has_vendors
            and not (
                {"VENDOR_DOCUMENT", "CONTROL_DESCRIPTION"} & document_types
            ),
        ),
        EvidenceIssue(
            code="BENEFICIAL_OWNERSHIP",
            title="Beneficial ownership information",
            description=(
                "No customer-information document or supporting text "
                "mentions beneficial ownership."
            ),
            missing=(
                "CUSTOMER_INFORMATION" not in document_types
                and "beneficial owner" not in combined_text
            ),
        ),
        EvidenceIssue(
            code="MONITORING_ARRANGEMENTS",
            title="Monitoring arrangements",
            description=(
                "No control description document or extracted control "
                "mentions ongoing monitoring."
            ),
            missing=(
                "CONTROL_DESCRIPTION" not in document_types
                and not existing_controls
                and "monitoring" not in combined_text
            ),
        ),
    ]

    return issues


def _compute_document_warnings(
    db: Session,
    documents: list[AssessmentDocument],
) -> list[DocumentWarning]:
    """R2.6 / P4: a warning for each expired (or unreadably dated) document,
    with whether it may be used as evidence (see services/evidence_currency.py)."""

    warnings = []

    for document in documents:
        row = evidence_currency.evaluate(db, document)
        if row["condition"] not in {evidence_currency.EXPIRED, evidence_currency.INVALID_EXPIRY_DATE}:
            continue
        if row["condition"] == evidence_currency.EXPIRED:
            reason = f"Expired on {document.expiry_date}."
        else:
            reason = f"The expiry date '{document.expiry_date}' can't be read as a date."
        reason += {
            "ACKNOWLEDGEMENT_REQUIRED": " Decide whether to use it as evidence before risk identification runs.",
            "ACKNOWLEDGED_FOR_USE": " Acknowledged for use as evidence.",
            "EXCLUDED_FROM_EVIDENCE": " Excluded from evidence; kept on file.",
        }.get(row["state"], "")
        warnings.append(
            DocumentWarning(
                document_id=document.id,
                filename=document.filename,
                reason=reason,
                document_version=row["document_version"],
                expiry_date=row["expiry_date"],
                condition=row["condition"],
                state=row["state"],
                usable_as_evidence=row["usable_as_evidence"],
                acknowledgement=row["acknowledgement"],
            )
        )

    return warnings


INTELLIGENCE_LIST_FIELDS = {
    "channels",
    "countries",
    "customer_segments",
    "third_party_vendors",
    "data_shared",
    "technologies",
    "regulatory_considerations",
    "existing_controls",
    "additional_risk_factors",
    "payment_methods",
}


def _ensure_structured_profile(
    db: Session, assessment: Assessment
) -> AssessmentIntelligence:
    """
    R3.1: guarantees a structured business/product profile exists for
    this assessment, even when it was created manually with no document
    upload (document-driven creation already makes one via document
    analysis -- see create_assessment_with_document). Seeds blank
    structured fields from the assessment's own intake fields (R1.1) so
    there is always something for the business owner to review/confirm
    (R3.3), rather than the profile simply not existing.
    """

    intelligence = (
        db.query(AssessmentIntelligence)
        .filter(AssessmentIntelligence.assessment_id == assessment.id)
        .first()
    )

    if intelligence:
        return intelligence

    intelligence = AssessmentIntelligence(
        assessment_id=assessment.id,
        business_line=assessment.product_or_service_name,
        transaction_volume=assessment.expected_transaction_volume,
        average_transaction_size=assessment.expected_transaction_value,
    )

    def _split(value: str | None) -> list[str]:
        if not value:
            return []
        return [item.strip() for item in value.split(",") if item.strip()]

    intelligence.set_list("countries", _split(assessment.countries_jurisdictions))
    intelligence.set_list("channels", _split(assessment.delivery_channels))
    intelligence.set_list("customer_segments", _split(assessment.customer_segment))

    vendor_usage = (assessment.third_party_vendor_usage or "").strip()
    if vendor_usage.lower() not in ("", "none", "n/a", "na"):
        intelligence.set_list("third_party_vendors", _split(vendor_usage))
    else:
        intelligence.set_list("third_party_vendors", [])

    intelligence.set_list("technologies", _split(assessment.technology_process_changes))

    # P4 (R2.4): every value here was typed by the requester.
    intelligence.extraction_method = "INTAKE_FORM"
    intelligence.extracted_at = datetime.now(timezone.utc)
    seeded = intake_history.profile_values(intelligence)
    seeded.pop("confirmed", None)
    intelligence.field_provenance = field_provenance.dump(
        field_provenance.intake_form_provenance(seeded, actor=assessment.submitted_by, at=intelligence.extracted_at)
    )

    db.add(intelligence)
    db.flush()
    _snapshot_new_profile(
        db, intelligence, None, "PROFILE_CREATED", "Profile created from the intake form (no source document)."
    )

    log_audit_event(
        db=db,
        assessment_id=assessment.id,
        action=AuditAction.STATUS_CHANGE,
        details=(
            "Structured business/product profile created from intake "
            "fields (no source document was uploaded) -- pending "
            "business-owner confirmation."
        ),
    )

    return intelligence


@router.get(
    "/{assessment_id}/intelligence",
    response_model=AssessmentIntelligenceResponse,
)
def get_assessment_intelligence(
    assessment_id: int,
    db: Session = Depends(get_db),
):
    assessment = (
        db.query(Assessment)
        .filter(Assessment.id == assessment_id)
        .first()
    )

    if not assessment:
        raise HTTPException(status_code=404, detail="Assessment not found")

    intelligence = (
        db.query(AssessmentIntelligence)
        .filter(
            AssessmentIntelligence.assessment_id == assessment_id
        )
        .first()
    )

    if not intelligence:
        raise HTTPException(
            status_code=404,
            detail="Assessment intelligence not found.",
        )

    return _build_intelligence_response(intelligence, assessment)


@router.get(
    "/{assessment_id}/consistency-check",
    response_model=list[ConsistencyConflict],
)
def check_assessment_consistency(
    assessment_id: int,
    db: Session = Depends(get_db),
):
    """
    R3.2: contradictions between the intake form and the structured
    profile for this assessment, if any.
    """

    assessment = (
        db.query(Assessment)
        .filter(Assessment.id == assessment_id)
        .first()
    )

    if not assessment:
        raise HTTPException(status_code=404, detail="Assessment not found")

    intelligence = (
        db.query(AssessmentIntelligence)
        .filter(AssessmentIntelligence.assessment_id == assessment_id)
        .first()
    )

    return detect_inconsistencies(assessment, intelligence)


@router.get(
    "/{assessment_id}/similar-assessments",
    response_model=list[SimilarAssessmentMatch],
)
def get_similar_assessments(
    assessment_id: int,
    db: Session = Depends(get_db),
):
    """
    pgvector semantic search: other assessments whose indexed evidence
    reads similarly to this one's description/evidence. Postgres-only
    (see app/database.py's IS_POSTGRES) -- returns an empty list on
    SQLite rather than erroring, so the frontend can call this
    unconditionally.
    """

    assessment = (
        db.query(Assessment)
        .filter(Assessment.id == assessment_id)
        .first()
    )

    if not assessment:
        raise HTTPException(status_code=404, detail="Assessment not found")

    if not IS_POSTGRES:
        return []

    query_text = " ".join(
        filter(None, [assessment.description, assessment.evidence])
    )

    return find_similar_risk_context(
        db=db,
        query_text=query_text,
        exclude_assessment_id=assessment_id,
    )


def _build_intelligence_response(
    intelligence: AssessmentIntelligence,
    assessment: Assessment | None = None,
) -> AssessmentIntelligenceResponse:
    return AssessmentIntelligenceResponse(
        id=intelligence.id,
        assessment_id=intelligence.assessment_id,
        business_line=intelligence.business_line,
        customer_type=intelligence.customer_type,
        transaction_origin=intelligence.transaction_origin,
        transaction_destination=intelligence.transaction_destination,
        transaction_frequency=intelligence.transaction_frequency,
        payment_methods=intelligence.get_list("payment_methods"),
        onboarding_approach=intelligence.onboarding_approach,
        ownership_entity_structure=intelligence.ownership_entity_structure,
        channels=intelligence.get_list("channels"),
        countries=intelligence.get_list("countries"),
        customer_segments=intelligence.get_list("customer_segments"),
        transaction_volume=intelligence.transaction_volume,
        average_transaction_size=intelligence.average_transaction_size,
        maximum_transaction_limit=intelligence.maximum_transaction_limit,
        third_party_vendors=intelligence.get_list(
            "third_party_vendors"
        ),
        data_shared=intelligence.get_list("data_shared"),
        technologies=intelligence.get_list("technologies"),
        regulatory_considerations=intelligence.get_list(
            "regulatory_considerations"
        ),
        existing_controls=intelligence.get_list(
            "existing_controls"
        ),
        additional_risk_factors=intelligence.get_list(
            "additional_risk_factors"
        ),
        source_document_id=intelligence.source_document_id,
        confirmed=intelligence.confirmed,
        confirmed_by=intelligence.confirmed_by,
        confirmed_at=intelligence.confirmed_at,
        conflicts=(
            detect_inconsistencies(assessment, intelligence)
            if assessment is not None
            else []
        ),
        original_values=_original_extracted_values(intelligence),
        field_provenance=_provenance_view(intelligence),
        provenance_summary=field_provenance.summarize(field_provenance.load(intelligence.field_provenance)),
        extraction_method=intelligence.extraction_method,
        extracted_at=intelligence.extracted_at,
        source_document_ids=_json_int_list(intelligence.source_document_ids)
        or ([intelligence.source_document_id] if intelligence.source_document_id else []),
        created_at=intelligence.created_at,
    )


def _json_int_list(raw: str | None) -> list[int]:
    try:
        value = json.loads(raw) if raw else []
    except (TypeError, ValueError):
        return []
    return [int(item) for item in value if isinstance(item, int)] if isinstance(value, list) else []


def _provenance_view(intelligence: AssessmentIntelligence) -> dict:
    """R2.4: each field's provenance plus whether a person confirmed it
    (the business owner's confirmation covers the profile as it stands)."""

    view = {}
    for field, record in field_provenance.load(intelligence.field_provenance).items():
        view[field] = {
            **record,
            "confirmed_by_user": bool(intelligence.confirmed),
            "confirmed_by": intelligence.confirmed_by if intelligence.confirmed else None,
            "confirmed_at": intelligence.confirmed_at.isoformat() if intelligence.confirmed and intelligence.confirmed_at else None,
        }
    return view


def _original_extracted_values(intelligence: AssessmentIntelligence) -> dict:
    """R2.3: the AI's original value of every profile field a user has
    since corrected, read from the untouched raw_extraction."""

    try:
        raw = json.loads(intelligence.raw_extraction or "{}")
    except (TypeError, ValueError):
        return {}
    if not isinstance(raw, dict):
        return {}

    originals = {}
    for field, original in raw.items():
        if not hasattr(intelligence, field) or field in {"id", "assessment_id"}:
            continue
        current = intelligence.get_list(field) if field in INTELLIGENCE_LIST_FIELDS else getattr(intelligence, field)
        if original in (None, "", []) and current in (None, "", []):
            continue
        if original != current:
            originals[field] = original
    return originals


@router.patch(
    "/{assessment_id}/intelligence",
    response_model=AssessmentIntelligenceResponse,
)
def update_assessment_intelligence(
    assessment_id: int,
    payload: AssessmentIntelligenceUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    R2.3/R3.4: corrects one or more structured-profile fields.
    `raw_extraction` (the AI's original output) is never touched here, so
    the original and corrected values both stay retrievable. A correction
    un-confirms the record -- it's a fresh claim that needs re-review.

    R3.4: if the profile was already confirmed/validated at the moment of
    this edit, the caller must supply a non-blank `change_reason` -- the
    acceptance criteria requires the system to record who changed a
    validated profile, when, which fields, and why. "Who" is the signed-in
    user; a `changed_by` in the body is ignored.
    """

    assessment = (
        db.query(Assessment)
        .filter(Assessment.id == assessment_id)
        .first()
    )

    if not assessment:
        raise HTTPException(status_code=404, detail="Assessment not found")

    ensure_assessment_editable(assessment)

    intelligence = (
        db.query(AssessmentIntelligence)
        .filter(AssessmentIntelligence.assessment_id == assessment_id)
        .first()
    )

    if not intelligence:
        raise HTTPException(
            status_code=404,
            detail="Assessment intelligence not found.",
        )

    was_confirmed = intelligence.confirmed
    before_values = intake_history.profile_values(intelligence)

    incoming = payload.model_dump(
        exclude_unset=True, exclude={"changed_by", "change_reason"}
    )

    changed_fields: list[str] = []

    for field, value in incoming.items():
        if value is None:
            continue

        if field in INTELLIGENCE_LIST_FIELDS:
            before = intelligence.get_list(field)
            if before == value:
                continue
            intelligence.set_list(field, value)
        else:
            before = getattr(intelligence, field)
            if before == value:
                continue
            setattr(intelligence, field, value)

        changed_fields.append(field)

    if not changed_fields:
        return _build_intelligence_response(intelligence, assessment)

    if was_confirmed:
        # R3.4 acceptance criteria: correcting an already-validated
        # profile must be attributed and justified.
        if not (payload.change_reason or "").strip():
            raise HTTPException(
                status_code=422,
                detail=(
                    "This profile was already validated. Provide a "
                    "change_reason to correct it."
                ),
            )

    intelligence.confirmed = False
    intelligence.confirmed_by = None
    intelligence.confirmed_at = None

    # P4 (R2.4): a corrected field's provenance becomes the correction,
    # keeping the extracted record it replaces.
    now = datetime.now(timezone.utc)
    records = field_provenance.load(intelligence.field_provenance)
    for field in changed_fields:
        value = intelligence.get_list(field) if field in INTELLIGENCE_LIST_FIELDS else getattr(intelligence, field)
        records[field] = field_provenance.correction_record(
            records.get(field), field, value, actor=actor_name(current_user), actor_id=current_user.id, at=now
        )
    intelligence.field_provenance = field_provenance.dump(records)

    # P4 (R3.4): old and new value of every changed field, who, when, why.
    after_values = intake_history.profile_values(intelligence)
    changes = intake_history.diff(before_values, after_values, changed_fields)
    reason = (payload.change_reason or "").strip() or None
    intake_history.record(
        db,
        assessment_id,
        intake_history.BUSINESS_PROFILE,
        after=after_values,
        before=before_values,
        trigger="PROFILE_CORRECTED",
        user=current_user,
        reason=reason,
        changes=changes,
        was_validated=was_confirmed,
    )

    log_audit_event(
        db=db,
        assessment_id=assessment_id,
        action=AuditAction.PROFILE_CORRECTED,
        actor=actor_name(current_user),
        actor_id=current_user.id,
        details=(
            (
                f"Validated business/product profile corrected by "
                f"{actor_name(current_user)}. Changed fields: "
                f"{', '.join(changed_fields)}. Reason: {payload.change_reason}"
            )
            if was_confirmed
            else (
                "Structured business/product profile corrected by a "
                f"reviewer. Changed fields: {', '.join(changed_fields)}."
            )
        )
        + f" Values: {intake_history.describe_changes(changes)}."
        + (" The profile needs the business owner's confirmation again." if was_confirmed else ""),
    )

    db.commit()
    db.refresh(intelligence)

    return _build_intelligence_response(intelligence, assessment)


@router.post(
    "/{assessment_id}/intelligence/confirm",
    response_model=AssessmentIntelligenceResponse,
)
def confirm_assessment_intelligence(
    assessment_id: int,
    payload: AssessmentIntelligenceConfirm,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    R2.3/R2.4/R3.3: marks this assessment's structured business/product
    profile as reviewed and confirmed by the business/product owner, as
    opposed to being an unconfirmed AI suggestion/draft.
    """

    assessment = (
        db.query(Assessment)
        .filter(Assessment.id == assessment_id)
        .first()
    )

    if not assessment:
        raise HTTPException(status_code=404, detail="Assessment not found")

    ensure_assessment_editable(assessment)

    # R3.3: the business/product owner -- the person who raised the
    # request -- confirms that the profile represents their change. An
    # analyst can correct it, but not sign it off on the owner's behalf.
    if current_user.id != assessment.owner_id:
        raise HTTPException(
            status_code=403,
            detail=(
                "Only the business owner who raised this request can confirm "
                "its structured profile."
            ),
        )

    intelligence = (
        db.query(AssessmentIntelligence)
        .filter(AssessmentIntelligence.assessment_id == assessment_id)
        .first()
    )

    if not intelligence:
        raise HTTPException(
            status_code=404,
            detail="Assessment intelligence not found.",
        )

    intelligence.confirmed = True
    intelligence.confirmed_by = actor_name(current_user)
    intelligence.confirmed_at = datetime.now(timezone.utc)

    # P4 (R3.4): the exact profile the owner validated.
    intake_history.record(
        db,
        assessment_id,
        intake_history.BUSINESS_PROFILE,
        after=intake_history.profile_values(intelligence),
        trigger="PROFILE_CONFIRMED",
        user=current_user,
        reason="Confirmed by the business/product owner.",
        was_validated=True,
    )

    log_audit_event(
        db=db,
        assessment_id=assessment_id,
        action=AuditAction.PROFILE_CONFIRMED,
        actor=intelligence.confirmed_by,
        actor_id=current_user.id,
        details=(
            "Structured business/product profile confirmed by the "
            "business/product owner as accurately representing the "
            "proposed change."
        ),
    )

    # Stage 14: Intake Validation -> Evidence Review.
    workflow.sync_workflow_status(
        db,
        assessment,
        user=current_user,
        reason="Structured business/product profile confirmed; intake validated.",
        action="PROFILE_CONFIRMED",
    )

    db.commit()
    db.refresh(intelligence)

    return _build_intelligence_response(intelligence, assessment)


@router.get(
    "/{assessment_id}/audit",
    response_model=list[AuditEventResponse],
)
def get_assessment_audit(
    assessment_id: int,
    db: Session = Depends(get_db),
):
    assessment = (
        db.query(Assessment)
        .filter(Assessment.id == assessment_id)
        .first()
    )

    if not assessment:
        raise HTTPException(
            status_code=404,
            detail="Assessment not found",
        )

    return (
        db.query(AuditEvent)
        .filter(
            AuditEvent.assessment_id == assessment_id
        )
        .order_by(
            AuditEvent.created_at.desc()
        )
        .all()
    )
@router.get(
    "/audit/all",
    response_model=list[AuditEventResponse],
)
def get_all_audit_events(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Audit history across assessments. An Admin or Auditor (R15.1) sees
    everything; anyone else only events on assessments they can see."""

    query = db.query(AuditEvent)
    if current_user.role not in {UserRole.ADMIN.value, UserRole.AUDITOR.value}:
        visible = _scope_assessments_for_user(db.query(Assessment.id), current_user)
        query = query.filter(AuditEvent.assessment_id.in_(visible))
    return query.order_by(AuditEvent.created_at.desc()).all()


@router.post(
    "/audit/calculator",
    response_model=AuditEventResponse,
    status_code=201,
)
def log_calculator_audit(
    payload: CalculatorAuditCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Debounced snapshot logged by the Manual Scoring Calculator a couple
    of seconds after the user stops editing — from either the
    standalone Risk Calculator page (payload.assessment_id is None) or
    the panel embedded on a specific assessment's detail page
    (payload.assessment_id set).
    """

    if payload.assessment_id is not None:
        assessment = (
            db.query(Assessment)
            .filter(Assessment.id == payload.assessment_id)
            .first()
        )

        if not assessment:
            raise HTTPException(
                status_code=404,
                detail="Assessment not found",
            )

        # The id is in the body, not the path, so the global access check
        # never saw it.
        ensure_assessment_visible(db, payload.assessment_id, current_user)

    factor_weights = payload.factor_weights or {}

    if payload.included_factors:
        factors_summary = ", ".join(
            f"{dimension}={score:g}"
            + (
                f" (weight {factor_weights[dimension] * 100:g}%)"
                if dimension in factor_weights
                else ""
            )
            for dimension, score in payload.included_factors.items()
        )
    else:
        factors_summary = "no factors included"

    details = (
        f"Manual scoring calculator updated — weighted total "
        f"{payload.weighted_total:.2f} ({payload.risk_level}). "
        f"Factors: {factors_summary}."
    )

    event = log_audit_event(
        db=db,
        assessment_id=payload.assessment_id,
        action=AuditAction.RISK_CALCULATOR_UPDATED,
        # Repurposed to carry the resulting risk level so the frontend
        # can show a severity badge on recent-activity cards without
        # parsing the free-text `details`.
        new_status=payload.risk_level,
        details=details,
        actor=actor_name(current_user),
        actor_id=current_user.id,
    )

    db.commit()
    db.refresh(event)

    return event


# Legacy pipeline statuses locked in addition to the final-decision
# statuses in app/services/decision_lock.py: AUDIT is the old terminal
# "approved/closed" stage and REMEDIATION is a closed-loop rework marker
# (see migrate_stage_pipeline.py). The calculator itself stays usable as a
# what-if tool for these — only the write to the assessment is blocked.
LOCKED_OVERRIDE_STATUSES = {"AUDIT", "REJECTED", "REMEDIATION"}


def _ensure_override_allowed(assessment: Assessment) -> None:
    ensure_assessment_editable(assessment)

    if assessment.status in LOCKED_OVERRIDE_STATUSES:
        raise HTTPException(
            status_code=409,
            detail=(
                f"This assessment is {assessment.status} and its inherent "
                "risk rating can no longer be overridden."
            ),
        )


def _apply_inherent_override(
    db: Session,
    assessment: Assessment,
    override_value: float,
    override_band: str | None,
    reason: str,
    user: User,
    source: str,
) -> InherentRiskCalculation:
    """
    R6.7: records a human override against the current deterministic
    calculation -- calculated value, overridden value, user, timestamp
    and reason -- leaving the calculated score itself untouched. The
    user always comes from the authenticated session. Does not commit.
    """

    calculation = (
        db.query(InherentRiskCalculation)
        .filter(
            InherentRiskCalculation.assessment_id == assessment.id,
            InherentRiskCalculation.is_current.is_(True),
        )
        .first()
    )

    if not calculation:
        calculation = recalculate_inherent_risk(db, assessment)

    override_band = override_band or determine_risk_band(
        override_value, calculation.get_risk_bands()
    )
    actor_name = user.full_name or user.email

    calculation.overridden = True
    calculation.override_value = override_value
    calculation.override_band = override_band
    calculation.override_reason = reason
    calculation.override_by = actor_name
    calculation.override_by_id = user.id
    calculation.override_at = datetime.now(timezone.utc)

    # R6.7/R10.4: a ledger entry per override, so a later override of the
    # same calculation never hides an earlier one. calculated_score and
    # calculated_band on the calculation are untouched.
    from app.services.override_ledger import record_applied

    record_applied(
        db,
        assessment.id,
        "INHERENT_RISK",
        "score",
        calculation.id,
        f"{calculation.calculated_score:g} ({calculation.calculated_band})"
        if calculation.calculated_score is not None
        else calculation.calculated_band,
        f"{override_value:g} ({override_band})",
        reason,
        user,
    )

    if assessment.inherent_score is not None:
        assessment.inherent_score = override_value
        assessment.inherent_risk_level = override_band

    calculated = (
        f"{calculation.calculated_score:g} ({calculation.calculated_band})"
        if calculation.calculated_score is not None
        else "not yet calculable"
    )

    log_audit_event(
        db=db,
        assessment_id=assessment.id,
        action=AuditAction.MANUAL_SCORE_OVERRIDE,
        previous_status=calculation.calculated_band,
        new_status=override_band,
        actor=actor_name,
        actor_id=user.id,
        details=(
            f"{source} — calculated {calculated}, "
            f"overridden to {override_value:g} ({override_band}). "
            f"Reason: {reason}"
        ),
    )

    return calculation


@router.patch(
    "/{assessment_id}/manual-score",
    response_model=AssessmentResponse,
)
def apply_manual_score_override(
    assessment_id: int,
    payload: ManualScoreOverrideCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_pipeline_role),
):
    """
    Records the Manual Scoring Calculator's current total as an R6.7
    override of the calculated inherent risk. The deterministic score
    (overall_score/risk_level) is never overwritten: the override sits
    alongside it with the calculated value, user, timestamp and a
    mandatory reason, exactly like POST /inherent-risk/override. The band
    comes from the configured methodology, not the client.
    """

    assessment = (
        db.query(Assessment)
        .filter(Assessment.id == assessment_id)
        .first()
    )

    if not assessment:
        raise HTTPException(
            status_code=404,
            detail="Assessment not found",
        )

    _ensure_override_allowed(assessment)

    factor_weights = payload.factor_weights or {}

    if payload.included_factors:
        factors_summary = ", ".join(
            f"{dimension}={score:g}"
            + (
                f" (weight {factor_weights[dimension] * 100:g}%)"
                if dimension in factor_weights
                else ""
            )
            for dimension, score in payload.included_factors.items()
        )
    else:
        factors_summary = "no factors included"

    _apply_inherent_override(
        db,
        assessment,
        override_value=payload.weighted_total,
        override_band=None,
        reason=payload.reason,
        user=current_user,
        source=f"Manual Scoring Calculator override (factors: {factors_summary})",
    )

    db.commit()
    db.refresh(assessment)

    return assessment


@router.patch(
    "/{assessment_id}/manual-score/draft",
    response_model=AssessmentResponse,
)
def save_manual_score_draft(
    assessment_id: int,
    payload: ManualScoreDraftSave,
    db: Session = Depends(get_db),
):
    """
    Persists the Manual Scoring Calculator's current inputs for this
    assessment, so they're restored the next time it's opened instead of
    resetting to the AI-assessed defaults. This is independent of
    apply_manual_score_override above — saving a draft never touches the
    assessment's real overall_score/risk_level, and works regardless of
    status (including locked ones, where the calculator is still a
    usable what-if tool).
    """

    assessment = (
        db.query(Assessment)
        .filter(Assessment.id == assessment_id)
        .first()
    )

    if not assessment:
        raise HTTPException(
            status_code=404,
            detail="Assessment not found",
        )

    assessment.manual_score_draft = json.dumps(payload.model_dump())

    db.commit()
    db.refresh(assessment)

    return assessment


from app.schemas.assessment import AssessmentUpdate  # you'll need to add this schema

@router.patch(
    "/{assessment_id}",
    response_model=AssessmentResponse,
)
def update_assessment(
    assessment_id: int,
    assessment_data: AssessmentUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    assessment = (
        db.query(Assessment)
        .filter(Assessment.id == assessment_id)
        .first()
    )

    if not assessment:
        raise HTTPException(status_code=404, detail="Assessment not found")

    # Stage 14 (R14.2): only the owner or a reviewer may edit a request.
    workflow.ensure_workflow_state(db, assessment)
    if not workflow.can_perform(current_user, assessment, "edit"):
        raise HTTPException(
            status_code=403,
            detail="Only the assessment owner or a reviewer can edit this request.",
        )

    # RETURNED_BY_MANAGER: the owner corrects the request after the manager
    # sent it back (the status itself is unchanged by the edit).
    if assessment.status not in REQUEST_EDITABLE_STATUSES:
        raise HTTPException(
            status_code=400,
            detail="Assessment can only be edited while in INTAKE, REMEDIATION or RETURNED_BY_MANAGER.",
        )

    data = assessment_data.model_dump()

    # R1.3/R1.4: is_draft/submitted_by are handled explicitly below, not
    # via the blind setattr loop -- a plain field edit of an already
    # -submitted assessment must not silently flip it back to a draft
    # just because AssessmentUpdate.is_draft defaults to True.
    submitting_now = assessment.is_draft and not data["is_draft"]

    if submitting_now:
        missing_fields = get_missing_mandatory_fields(data)

        if missing_fields:
            raise HTTPException(
                status_code=422,
                detail={
                    "message": (
                        "Missing mandatory information. Complete these "
                        "fields before submitting, or keep saving as a "
                        "draft."
                    ),
                    "missing_fields": missing_fields,
                },
            )

    # A client that doesn't know about the shell company flag must not
    # wipe it just by leaving it out (None is a real answer: "not
    # answered"), so only an explicitly sent value replaces it.
    if "shell_company_indicator" not in assessment_data.model_fields_set:
        data.pop("shell_company_indicator", None)
    shell_flag_changed = (
        "shell_company_indicator" in data
        and data["shell_company_indicator"] != assessment.shell_company_indicator
    )

    # P4 (R3.4): what changes, before anything is written.
    change_reason = (data.pop("change_reason", None) or "").strip() or None
    before_values = intake_history.request_values(assessment)
    proposed = {
        **before_values,
        **{field: value for field, value in data.items() if field in before_values and field != "is_draft"},
    }
    if submitting_now:
        proposed["is_draft"] = False
    changes = intake_history.diff(before_values, proposed, intake_history.REQUEST_FIELDS)
    field_changes = [change for change in changes if change["field"] != "is_draft"]

    profile = (
        db.query(AssessmentIntelligence)
        .filter(AssessmentIntelligence.assessment_id == assessment.id)
        .first()
    )
    validated = bool(profile and profile.confirmed)
    if validated and field_changes and not change_reason:
        raise HTTPException(
            status_code=422,
            detail=(
                "This request's business profile has already been validated. Give a change_reason "
                "to change: " + ", ".join(change["field"] for change in field_changes) + "."
            ),
        )

    if not changes and not submitting_now:
        return assessment

    # Update every other field carried on AssessmentUpdate, including the
    # R1.1 request fields, so completing/editing a draft can fill them in.
    for field, value in data.items():
        if field in {"is_draft", "submitted_by"}:
            continue
        setattr(assessment, field, value)

    if submitting_now:
        assessment.is_draft = False
        assessment.submitted_by = actor_name(current_user)
        assessment.submitted_at = datetime.now(timezone.utc)

        if not assessment.reference_id:
            assessment.reference_id = generate_reference_id(assessment.id)

    intake_history.record(
        db,
        assessment.id,
        intake_history.ASSESSMENT_REQUEST,
        after=intake_history.request_values(assessment),
        before=before_values,
        trigger="SUBMITTED" if submitting_now else "EDITED",
        user=current_user,
        reason=change_reason,
        changes=changes,
        was_validated=validated,
    )

    # A validated profile no longer covers a change to a field that feeds
    # triage, scoping or escalation: the owner confirms it again.
    from app.governance.policy import policy as governance_policy

    material = sorted(
        {change["field"] for change in field_changes} & set(governance_policy()["material_intake_fields"])
    )
    reconfirm = validated and bool(material)
    if reconfirm:
        before_profile = intake_history.profile_values(profile)
        profile.confirmed = False
        profile.confirmed_by = None
        profile.confirmed_at = None
        intake_history.record(
            db,
            assessment.id,
            intake_history.BUSINESS_PROFILE,
            after=intake_history.profile_values(profile),
            before=before_profile,
            trigger="PROFILE_UNCONFIRMED",
            user=current_user,
            reason=f"Request fields changed after validation ({', '.join(material)}): {change_reason}",
            changes=[{"field": "confirmed", "old": True, "new": False}],
            was_validated=True,
        )

    log_audit_event(
        db=db,
        assessment_id=assessment.id,
        action=AuditAction.STATUS_CHANGE if submitting_now else AuditAction.INTAKE_UPDATED,
        previous_status=assessment.status,
        new_status=assessment.status,
        actor=actor_name(current_user),
        actor_id=current_user.id,
        details=(
            ("Draft submitted." if submitting_now else "Assessment details edited.")
            + (f" Changes: {intake_history.describe_changes(field_changes)}." if field_changes else "")
            + (f" Reason: {change_reason}" if change_reason else "")
            + (
                f" The validated profile needs the business owner's confirmation again ({', '.join(material)} changed)."
                if reconfirm
                else ""
            )
        ),
    )

    if reconfirm and not submitting_now:
        workflow.sync_workflow_status(
            db,
            assessment,
            user=current_user,
            reason="Request changed after validation; profile confirmation required again.",
            action="PROFILE_UNCONFIRMED",
        )

    if not submitting_now and not assessment.is_draft and shell_flag_changed:
        # Triage normally runs once, on submission; this flag is the one
        # edit that must still move the priority afterwards.
        _apply_intake_triage_and_routing(
            db, assessment, retriage_reason="shell company indicator changed"
        )

    if submitting_now:
        _apply_intake_triage_and_routing(db, assessment)
        workflow.sync_workflow_status(
            db,
            assessment,
            user=current_user,
            reason="Draft completed and submitted.",
            action="SUBMIT",
        )

    db.commit()
    db.refresh(assessment)
    return assessment


@router.post(
    "/{assessment_id}/documents",
    response_model=AssessmentDocumentResponse,
)
async def upload_assessment_document(
    assessment_id: int,
    file: UploadFile = File(...),
    # R2.2: classification metadata. All optional (document_type
    # defaults to OTHER) so this stays a simple drag-and-drop upload
    # when the caller doesn't have this information yet.
    document_type: str = Form("OTHER"),
    document_owner: str = Form(None),
    source: str = Form(None),
    effective_date: str = Form(None),
    expiry_date: str = Form(None),
    confidentiality: str = Form(None),
    # R2.6: pass the id of the document this upload replaces to record
    # it as a new version of the same document, instead of an unrelated
    # new one.
    supersedes_id: int = Form(None),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Stage 19 (Performance / Reliability): the file is validated and stored,
    and the document row committed, before any text extraction happens.
    Extraction (and semantic indexing) then runs as a background
    ProcessingJob, so a large document never blocks this request, its
    progress is visible via the returned document's `processing` field,
    and a parsing failure leaves the stored document in place, marked
    FAILED and retryable, instead of losing the upload.
    """

    assessment = (
        db.query(Assessment)
        .filter(Assessment.id == assessment_id)
        .first()
    )

    if not assessment:
        raise HTTPException(status_code=404, detail="Assessment not found")

    # INTAKE is included so initial supporting documents can be attached
    # at the point of entry, not only once formal evidence collection
    # begins (Intake feature: "Basic Document & Attachment Upload").
    # RETURNED_BY_MANAGER: the owner can add or replace evidence the manager
    # asked for before resubmitting.
    if assessment.status not in DOCUMENT_UPLOAD_STATUSES:
        raise HTTPException(
            status_code=400,
            detail=(
                "Documents can only be uploaded while in INTAKE, EVIDENCE_COLLECTION, "
                "REMEDIATION or RETURNED_BY_MANAGER."
            ),
        )

    if document_type not in DOCUMENT_TYPES:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid document_type. Must be one of: {', '.join(DOCUMENT_TYPES)}.",
        )

    if confidentiality and confidentiality not in CONFIDENTIALITY_LEVELS:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid confidentiality. Must be one of: {', '.join(CONFIDENTIALITY_LEVELS)}.",
        )

    allowed_extensions = {".docx", ".doc", ".xlsx", ".pdf", ".txt", ".csv"}
    filename = file.filename or ""
    extension = "." + filename.split(".")[-1].lower() if "." in filename else ""

    if extension not in allowed_extensions:
        raise HTTPException(
            status_code=400,
            detail="Unsupported file type. Supported files: DOCX, DOC, XLSX, PDF, TXT, CSV.",
        )

    file_content = await file.read()

    if not file_content:
        raise HTTPException(status_code=400, detail="Uploaded file is empty.")

    ensure_upload_size(filename, file_content)

    previous_version = None
    version = 1

    if supersedes_id is not None:
        previous_version = (
            db.query(AssessmentDocument)
            .filter(
                AssessmentDocument.id == supersedes_id,
                AssessmentDocument.assessment_id == assessment_id,
            )
            .first()
        )

        if not previous_version:
            raise HTTPException(
                status_code=404,
                detail="The document being superseded was not found on this assessment.",
            )

        # P4 (R2.6): only the current version can be replaced; superseding
        # an older one would leave two current versions of one document.
        if not previous_version.is_current:
            raise HTTPException(
                status_code=409,
                detail="That version has already been superseded. Upload the new version against the current one.",
            )

        version = previous_version.version + 1

    saved_path = save_file(assessment_id, filename, file_content)

    document = AssessmentDocument(
        assessment_id=assessment.id,
        filename=filename,
        file_type=extension,
        # Filled in by the background extraction job.
        extracted_text="",
        file_path=saved_path,
        document_type=document_type,
        document_owner=document_owner,
        source=source,
        effective_date=effective_date,
        expiry_date=expiry_date,
        confidentiality=confidentiality,
        version=version,
        is_current=True,
        supersedes_id=supersedes_id,
    )

    db.add(document)

    if previous_version:
        previous_version.is_current = False
        previous_version.superseded_at = datetime.now(timezone.utc)

    log_audit_event(
        db=db,
        assessment_id=assessment.id,
        action=AuditAction.STATUS_CHANGE,
        previous_status=assessment.status,
        new_status=assessment.status,
        actor=current_user.full_name or current_user.email,
        actor_id=current_user.id,
        details=(
            f"Document uploaded: {filename} (v{version})."
            if previous_version
            else f"Document uploaded: {filename}."
        ),
    )

    db.flush()

    job = processing_jobs.create_job(
        db,
        ProcessingJobType.DOCUMENT_EXTRACTION,
        assessment_id=assessment.id,
        document_id=document.id,
        user=current_user,
    )

    db.commit()
    processing_jobs.enqueue(job.id)

    db.refresh(document)
    return _build_document_response(db, document, current_user, assessment.owner_id)


@router.get(
    "/{assessment_id}/risk-results",
    response_model=list[RiskResultResponse],
)
def get_assessment_risk_results(
    assessment_id: int,
    db: Session = Depends(get_db),
):
    assessment = (
        db.query(Assessment)
        .filter(Assessment.id == assessment_id)
        .first()
    )

    if not assessment:
        raise HTTPException(
            status_code=404,
            detail="Assessment not found",
        )

    return (
        db.query(RiskResult)
        .filter(
            RiskResult.assessment_id == assessment_id,
            RiskResult.is_current.is_(True),
        )
        .all()
    )


@router.get(
    "/{assessment_id}/risk-factors",
    response_model=list[RiskFactorResponse],
)
def get_assessment_risk_factors(
    assessment_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Stage 4 (R4.1-R4.4): the current per-category risk-factor
    identification for this assessment -- separate from the
    6-dimension /risk-results scoring.
    """

    assessment = (
        db.query(Assessment)
        .filter(Assessment.id == assessment_id)
        .first()
    )

    if not assessment:
        raise HTTPException(status_code=404, detail="Assessment not found")

    factors = (
        db.query(RiskFactor)
        .filter(
            RiskFactor.assessment_id == assessment_id,
            RiskFactor.is_current.is_(True),
        )
        .order_by(RiskFactor.source.desc(), RiskFactor.id.asc())
        .all()
    )

    masked = _masked_document_ids(db, assessment, current_user)
    return [_build_risk_factor_response(factor, masked) for factor in factors]


@router.get(
    "/{assessment_id}/occ-risk-profile",
    response_model=OccRiskProfileResponse,
)
def get_assessment_occ_risk_profile(
    assessment_id: int,
    db: Session = Depends(get_db),
):
    """
    The nine OCC supervisory risk categories (OCC NR 96-2a) as a
    read-only lens over this assessment's already-rated risk factors.

    Purely derived: it stores nothing, and the assessment's score and
    band are unaffected by it. Analysts still rate only the 10 assessed
    categories -- see app/schemas/risk_factor.py::CATEGORY_TO_OCC for the
    mapping and the reasoning behind each entry.
    """

    assessment = (
        db.query(Assessment)
        .filter(Assessment.id == assessment_id)
        .first()
    )

    if not assessment:
        raise HTTPException(status_code=404, detail="Assessment not found")

    factors = (
        db.query(RiskFactor)
        .filter(
            RiskFactor.assessment_id == assessment_id,
            RiskFactor.is_current.is_(True),
        )
        .all()
    )

    config = get_methodology_config(db)

    profile = calculate_occ_risk_profile(
        [
            {
                "id": factor.id,
                "category": factor.category,
                "applicable": factor.applicable,
                "excluded": factor.excluded,
                "score": factor.score,
                "severity": factor.severity,
                # Same definition of "rated" the inherent-risk calculation
                # uses: an analyst's own likelihood x impact, never an AI
                # suggestion.
                "rated": factor.likelihood is not None and factor.impact is not None,
            }
            for factor in factors
        ],
        risk_bands=config["risk_bands"],
    )

    return OccRiskProfileResponse(
        assessment_id=assessment_id,
        categories=profile["categories"],
        uncovered_categories=profile["uncovered_categories"],
        is_provisional=profile["is_provisional"],
    )


def _masked_document_ids(db: Session, assessment: Assessment | None, user: User) -> set[int]:
    """Ids of this assessment's documents that `user` only sees masked
    (R15.3 / Stage 19), so evidence quotes from them are masked too."""

    if assessment is None:
        return set()
    rows = (
        db.query(AssessmentDocument.id, AssessmentDocument.confidentiality)
        .filter(AssessmentDocument.assessment_id == assessment.id)
        .all()
    )
    return {
        document_id
        for document_id, confidentiality in rows
        if should_mask_document(confidentiality, user, assessment.owner_id)
    }


def _build_risk_factor_response(factor: RiskFactor, masked_document_ids: set[int] | None = None) -> RiskFactorResponse:
    return RiskFactorResponse(
        id=factor.id,
        assessment_id=factor.assessment_id,
        category=factor.category,
        applicable=factor.applicable,
        score=factor.score,
        severity=factor.severity,
        likelihood=factor.likelihood,
        impact=factor.impact,
        rated_by=factor.rated_by,
        rated_at=factor.rated_at,
        ai_suggested_likelihood=factor.ai_suggested_likelihood,
        ai_suggested_impact=factor.ai_suggested_impact,
        ai_suggestion_rationale=factor.ai_suggestion_rationale,
        ai_suggested_at=factor.ai_suggested_at,
        rating_source=factor.rating_source,
        indicators=factor.get_indicators(),
        evidence_status=factor.evidence_status,
        evidence=mask_evidence_records(factor.get_evidence(), masked_document_ids or set()),
        rejected_indicators=factor.get_rejected_indicators(),
        missing_information=factor.get_missing_information(),
        rule_triggers=factor.get_rule_triggers(),
        rationale=factor.rationale,
        misuse_scenario=factor.misuse_scenario,
        source=factor.source,
        added_by=factor.added_by,
        excluded=factor.excluded,
        exclusion_reason=factor.exclusion_reason,
        excluded_by=factor.excluded_by,
        excluded_at=factor.excluded_at,
        version=factor.version,
        is_current=factor.is_current,
        created_at=factor.created_at,
    )


@router.post(
    "/{assessment_id}/risk-factors",
    response_model=RiskFactorResponse,
    status_code=201,
)
def add_assessment_risk_factor(
    assessment_id: int,
    payload: RiskFactorManualCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_pipeline_role),
):
    """
    R4.5: lets an analyst add a risk factor that wasn't identified
    automatically. A rationale is required (enforced by the schema);
    misuse_scenario is optional since not every added factor implies a
    specific misuse pathway.
    """

    assessment = (
        db.query(Assessment)
        .filter(Assessment.id == assessment_id)
        .first()
    )

    if not assessment:
        raise HTTPException(status_code=404, detail="Assessment not found")

    ensure_assessment_editable(assessment)

    score = max(0.0, min(100.0, payload.score)) if payload.applicable else 0.0
    # Severity comes from the methodology's bands (not fixed cutoffs), so a
    # manually added factor is labelled the same way as an identified one.
    severity = determine_risk_band(score, get_methodology_config(db)["risk_bands"])

    factor = RiskFactor(
        assessment_id=assessment_id,
        category=payload.category,
        applicable=payload.applicable,
        score=score,
        severity=severity,
        rationale=payload.rationale,
        misuse_scenario=payload.misuse_scenario,
        source="MANUAL",
        added_by=actor_name(current_user),
        version=1,
        is_current=True,
    )
    factor.set_indicators(payload.indicators)

    db.add(factor)
    db.flush()

    log_audit_event(
        db=db,
        assessment_id=assessment_id,
        action=AuditAction.STATUS_CHANGE,
        actor=factor.added_by,
        actor_id=current_user.id,
        details=f"Risk factor added manually: {factor.category}.",
    )

    # Keeps overall_score/risk_level in sync with the full current
    # factor set immediately -- not just after the next re-analysis.
    recalculate_assessment_score(db, assessment)
    recalculate_inherent_risk(db, assessment, calculated_by=factor.added_by)

    db.commit()
    db.refresh(factor)

    return _build_risk_factor_response(factor, _masked_document_ids(db, assessment, current_user))


def _assessment_context_for_rating(assessment: Assessment) -> dict[str, Any]:
    """
    The change's actual profile, passed to the AI alongside the factor's
    own rationale/misuse scenario. Without this the model is estimating
    likelihood and impact from a bare category name, which is what the
    Manual Scoring Calculator used to do -- the resulting numbers were
    not worth overriding.
    """

    context = {
        "title": assessment.title,
        "change_type": assessment.change_type,
        "description": assessment.description,
        "product_or_service_name": assessment.product_or_service_name,
        "customer_segment": assessment.customer_segment,
        "countries_jurisdictions": assessment.countries_jurisdictions,
        "delivery_channels": assessment.delivery_channels,
        "transaction_types": assessment.transaction_types,
        "expected_transaction_volume": assessment.expected_transaction_volume,
        "expected_transaction_value": assessment.expected_transaction_value,
        "third_party_vendor_usage": assessment.third_party_vendor_usage,
        "technology_process_changes": assessment.technology_process_changes,
        "legal_entity": assessment.legal_entity,
        "business_unit": assessment.business_unit,
        # Only a positive flag is worth telling the model about.
        "shell_company_indicator": (
            "Flagged by the requester as a potential shell entity"
            if assessment.shell_company_indicator
            else None
        ),
    }

    return {key: value for key, value in context.items() if value}


@router.post(
    "/{assessment_id}/risk-factors/suggest-ratings",
    response_model=RiskFactorSuggestRatingsResponse,
)
def suggest_assessment_risk_factor_ratings(
    assessment_id: int,
    payload: RiskFactorSuggestRatingsRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_pipeline_role),
):
    """
    Ask the AI for a suggested likelihood/impact on each applicable,
    non-excluded factor, so an analyst always has a starting point to
    accept or override rather than a blank form.

    The suggestion is written only to the ai_suggested_* columns. It
    deliberately does not touch likelihood/impact/score/severity, which
    means the inherent-risk calculation is unchanged by this call and an
    assessment stays provisional until a human actually rates each
    factor -- the R6.7 completeness gate on the stage transition keeps
    working exactly as before.
    """

    assessment = (
        db.query(Assessment)
        .filter(Assessment.id == assessment_id)
        .first()
    )

    if not assessment:
        raise HTTPException(status_code=404, detail="Assessment not found")

    ensure_assessment_editable(assessment)

    config = get_methodology_config(db)
    context = _assessment_context_for_rating(assessment)

    factors = (
        db.query(RiskFactor)
        .filter(
            RiskFactor.assessment_id == assessment_id,
            RiskFactor.is_current.is_(True),
        )
        .all()
    )

    targets = [
        factor
        for factor in factors
        if factor.applicable
        and not factor.excluded
        and (payload.include_rated or factor.likelihood is None or factor.impact is None)
    ]

    if not targets:
        raise HTTPException(
            status_code=400,
            detail=(
                "No applicable, non-excluded risk factors need a suggested "
                "rating. Run risk identification first, or pass "
                "include_rated=true to re-suggest for already-rated factors."
            ),
        )

    now = datetime.now(timezone.utc)
    suggested_count = 0
    fallback_count = 0

    for factor in targets:
        estimate = estimate_likelihood_impact(
            risk_category=factor.category,
            risk_rationale=factor.rationale,
            misuse_scenario=factor.misuse_scenario or "",
            assessment_context=context,
            likelihood_scale=config["likelihood_scale"],
            impact_scale=config["impact_scale"],
        )

        if estimate.get("fallback"):
            # Don't write a stub in as though it were a suggestion --
            # leaving it absent keeps the form honest about having
            # nothing to propose for this factor.
            fallback_count += 1
            continue

        factor.ai_suggested_likelihood = estimate["likelihood"]
        factor.ai_suggested_impact = estimate["impact"]
        factor.ai_suggestion_rationale = estimate.get("reasoning") or None
        factor.ai_suggested_at = now
        suggested_count += 1

    actor = actor_name(current_user)

    log_audit_event(
        db=db,
        assessment_id=assessment_id,
        action=AuditAction.STATUS_CHANGE,
        actor=actor,
        actor_id=current_user.id,
        details=(
            f"AI suggested likelihood/impact ratings for {suggested_count} "
            f"risk factor(s); {fallback_count} could not be estimated. "
            "Suggestions do not change the inherent risk calculation until "
            "an analyst confirms or overrides each one."
        ),
    )

    db.commit()

    for factor in targets:
        db.refresh(factor)

    masked = _masked_document_ids(db, assessment, current_user)
    return RiskFactorSuggestRatingsResponse(
        factors=[_build_risk_factor_response(factor, masked) for factor in factors],
        suggested_count=suggested_count,
        fallback_count=fallback_count,
        degraded=fallback_count > 0,
    )


@router.patch(
    "/{assessment_id}/risk-factors/{risk_factor_id}/rating",
    response_model=RiskFactorResponse,
)
def rate_assessment_risk_factor(
    assessment_id: int,
    risk_factor_id: int,
    payload: RiskFactorRatingUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_pipeline_role),
):
    """
    Stage 6 (R6.2, R6.3): an analyst's own likelihood/impact rating for a
    risk factor -- the only thing that moves score/severity. Where the AI
    has suggested a rating, this either confirms or overrides it, and
    rating_source records which; the suggestion itself is left untouched
    so both sides of the decision stay on the record.

    The new score is computed deterministically from these values against
    the active methodology's likelihood/impact scales, and the
    assessment's overall inherent-risk calculation is recomputed
    immediately so it never goes stale.
    """

    factor = (
        db.query(RiskFactor)
        .filter(
            RiskFactor.id == risk_factor_id,
            RiskFactor.assessment_id == assessment_id,
            RiskFactor.is_current.is_(True),
        )
        .first()
    )

    if not factor:
        raise HTTPException(status_code=404, detail="Risk factor not found")

    assessment = (
        db.query(Assessment)
        .filter(Assessment.id == assessment_id)
        .first()
    )

    if not assessment:
        raise HTTPException(status_code=404, detail="Assessment not found")

    ensure_assessment_editable(assessment)

    config = get_methodology_config(db)
    likelihood_values = {item["value"] for item in config["likelihood_scale"]}
    impact_values = {item["value"] for item in config["impact_scale"]}

    if payload.likelihood not in likelihood_values:
        raise HTTPException(
            status_code=400,
            detail=f"likelihood must be one of: {sorted(likelihood_values)}",
        )
    if payload.impact not in impact_values:
        raise HTTPException(
            status_code=400,
            detail=f"impact must be one of: {sorted(impact_values)}",
        )

    score = compute_factor_score(
        payload.likelihood,
        payload.impact,
        likelihood_scale=config["likelihood_scale"],
        impact_scale=config["impact_scale"],
    )
    band = determine_risk_band(score, config["risk_bands"])

    # Distinguish "a human agreed with the model" from "a human decided
    # differently" from "there was nothing to agree with" -- the raw
    # presence of a likelihood/impact value cannot tell these apart, and
    # an auditor needs to.
    had_suggestion = (
        factor.ai_suggested_likelihood is not None
        and factor.ai_suggested_impact is not None
    )

    if not had_suggestion:
        rating_source = "ANALYST_RATED"
    elif (
        payload.likelihood == factor.ai_suggested_likelihood
        and payload.impact == factor.ai_suggested_impact
    ):
        rating_source = "ANALYST_CONFIRMED"
    else:
        rating_source = "ANALYST_OVERRIDE"

    reason = (payload.reason or "").strip()

    if rating_source == "ANALYST_OVERRIDE":
        # R10.3: departing from the AI's suggestion is a material change.
        if not reason:
            raise HTTPException(
                status_code=422,
                detail=(
                    "This rating differs from the AI's suggestion "
                    f"(likelihood {factor.ai_suggested_likelihood}, impact "
                    f"{factor.ai_suggested_impact}). Give a reason for the change."
                ),
            )

        # R10.4: the AI value, human value, reason, user and time land in
        # the same ledger the review screen compares, from the record
        # itself rather than retyped by hand.
        rating_override = AssessmentOverride(
            assessment_id=assessment_id,
            section="FACTOR_RATING",
            field_name=f"{factor.category} likelihood x impact",
            entity_id=str(factor.id),
            ai_value=f"{factor.ai_suggested_likelihood} x {factor.ai_suggested_impact}",
            human_value=f"{payload.likelihood} x {payload.impact}",
            reason=reason,
            overridden_by=actor_name(current_user),
            overridden_by_id=current_user.id,
            ai_value_source="SYSTEM",
            # Made here, under this endpoint's own role rule; the AI
            # suggestion stays on the factor (ai_suggested_*).
            review_status="APPLIED",
            origin="TYPED",
        )
        # P3: classified by its actual effect on the factor's band.
        from app.governance.overrides import classify_entry

        classify_entry(db, rating_override)
        db.add(rating_override)

    factor.likelihood = payload.likelihood
    factor.impact = payload.impact
    factor.rated_by = actor_name(current_user)
    factor.rated_by_id = current_user.id
    factor.rated_at = datetime.now(timezone.utc)
    factor.score = score
    factor.severity = band
    factor.rating_source = rating_source

    if rating_source == "ANALYST_OVERRIDE":
        provenance = (
            f" Overrides the AI suggestion of likelihood "
            f"{factor.ai_suggested_likelihood}, impact "
            f"{factor.ai_suggested_impact}."
        )
    elif rating_source == "ANALYST_CONFIRMED":
        provenance = " Confirms the AI's suggested rating unchanged."
    else:
        provenance = " No AI suggestion was present."

    log_audit_event(
        db=db,
        assessment_id=assessment_id,
        action=AuditAction.STATUS_CHANGE,
        actor=factor.rated_by,
        actor_id=current_user.id,
        details=(
            f"Risk factor rated: {factor.category} — likelihood "
            f"{payload.likelihood}, impact {payload.impact} -> score "
            f"{score:g} ({band})." + provenance
            + (f" Reason: {reason}" if reason else "")
        ),
    )

    recalculate_assessment_score(db, assessment)
    recalculate_inherent_risk(db, assessment, calculated_by=factor.rated_by)

    db.commit()
    db.refresh(factor)

    return _build_risk_factor_response(factor, _masked_document_ids(db, assessment, current_user))


@router.patch(
    "/{assessment_id}/risk-factors/{risk_factor_id}/exclude",
    response_model=RiskFactorResponse,
)
def exclude_assessment_risk_factor(
    assessment_id: int,
    risk_factor_id: int,
    payload: RiskFactorExclude,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_pipeline_role),
):
    """
    R4.5: excludes a risk factor/category -- a reason is required
    (enforced by the schema), so "we considered this and ruled it out"
    always carries its own justification.
    """

    factor = (
        db.query(RiskFactor)
        .filter(
            RiskFactor.id == risk_factor_id,
            RiskFactor.assessment_id == assessment_id,
        )
        .first()
    )

    if not factor:
        raise HTTPException(status_code=404, detail="Risk factor not found")

    assessment = (
        db.query(Assessment)
        .filter(Assessment.id == assessment_id)
        .first()
    )

    if not assessment:
        raise HTTPException(status_code=404, detail="Assessment not found")

    ensure_assessment_editable(assessment)

    factor.excluded = True
    factor.exclusion_reason = payload.reason
    factor.excluded_by = actor_name(current_user)
    factor.excluded_at = datetime.now(timezone.utc)

    log_audit_event(
        db=db,
        assessment_id=assessment_id,
        action=AuditAction.STATUS_CHANGE,
        actor=factor.excluded_by,
        actor_id=current_user.id,
        details=f"Risk factor excluded: {factor.category} — {payload.reason}",
    )

    # An excluded factor no longer counts toward the score.
    recalculate_assessment_score(db, assessment)
    recalculate_inherent_risk(db, assessment, calculated_by=factor.excluded_by)

    db.commit()
    db.refresh(factor)

    return _build_risk_factor_response(factor, _masked_document_ids(db, assessment, current_user))


@router.patch(
    "/{assessment_id}/risk-factors/{risk_factor_id}/indicators",
    response_model=RiskFactorResponse,
)
def update_risk_factor_indicators(
    assessment_id: int,
    risk_factor_id: int,
    payload: RiskFactorIndicatorsUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_pipeline_role),
):
    """
    P4 (R4.2): an analyst assesses a factor's detailed indicators -- on an
    AI or rules factor as well as a manual one. The system value is read
    from the factor, the change goes to the override ledger (classified by
    its effect: an escalation indicator is CRITICAL and needs approval
    before the committee, P3), and the calculation is recomputed.
    """

    factor = (
        db.query(RiskFactor)
        .filter(
            RiskFactor.id == risk_factor_id,
            RiskFactor.assessment_id == assessment_id,
            RiskFactor.is_current.is_(True),
        )
        .first()
    )
    if not factor:
        raise HTTPException(status_code=404, detail="Risk factor not found")
    assessment = db.query(Assessment).filter(Assessment.id == assessment_id).first()
    if not assessment:
        raise HTTPException(status_code=404, detail="Assessment not found")
    ensure_assessment_editable(assessment)
    if not factor.applicable or factor.excluded:
        raise HTTPException(status_code=409, detail="Indicators can only be set on an applicable, non-excluded factor.")

    before = factor.get_indicators()
    after = payload.indicators
    if set(before) == set(after):
        return _build_risk_factor_response(factor, _masked_document_ids(db, assessment, current_user))

    from app.services.override_ledger import record_applied

    ledger_row = record_applied(
        db, assessment_id, "RISK_CATEGORY", "indicators", factor.id, before, after, payload.reason, current_user
    )
    factor.set_indicators(after)

    added = sorted(set(after) - set(before))
    removed = sorted(set(before) - set(after))
    log_audit_event(
        db=db,
        assessment_id=assessment_id,
        action=AuditAction.RISK_INDICATORS_CHANGED,
        actor=actor_name(current_user),
        actor_id=current_user.id,
        details=(
            f"{factor.category} indicators changed by an analyst"
            + (f"; added {', '.join(added)}" if added else "")
            + (f"; removed {', '.join(removed)}" if removed else "")
            + f". Previous: {before or 'none'}. Materiality: {ledger_row.materiality}. Reason: {payload.reason}"
        ),
    )

    recalculate_assessment_score(db, assessment)
    recalculate_inherent_risk(db, assessment, calculated_by=actor_name(current_user))

    db.commit()
    db.refresh(factor)
    return _build_risk_factor_response(factor, _masked_document_ids(db, assessment, current_user))


@router.get("/{assessment_id}/intake-history")
def get_intake_history(
    assessment_id: int,
    record_type: str | None = None,
    db: Session = Depends(get_db),
):
    """P4 (R3.4): every recorded version of the request and of the
    structured profile, with old and new values, who, when and why."""

    if not db.query(Assessment.id).filter(Assessment.id == assessment_id).first():
        raise HTTPException(status_code=404, detail="Assessment not found")
    if record_type and record_type not in {intake_history.ASSESSMENT_REQUEST, intake_history.BUSINESS_PROFILE}:
        raise HTTPException(status_code=422, detail="record_type must be ASSESSMENT_REQUEST or BUSINESS_PROFILE.")
    return {"assessment_id": assessment_id, "versions": intake_history.history(db, assessment_id, record_type)}


def _build_inherent_risk_response(
    calculation: InherentRiskCalculation,
) -> InherentRiskCalculationResponse:
    inputs = calculation.get_inputs()
    rated_count = sum(1 for item in inputs if item.get("rated"))
    # The row stores 0.0 when nothing is rated (the column is
    # non-nullable); report that as no score. Overrides always carry one.
    has_score = calculation.overridden or rated_count > 0 or not inputs

    return InherentRiskCalculationResponse(
        id=calculation.id,
        assessment_id=calculation.assessment_id,
        methodology_id=calculation.methodology_id,
        methodology_name=calculation.methodology_name,
        calculation_method=calculation.calculation_method,
        inputs=calculation.get_inputs(),
        weights=calculation.get_weights(),
        thresholds=json.loads(calculation.thresholds) if calculation.thresholds else {},
        risk_bands=calculation.get_risk_bands(),
        escalation_rules=calculation.get_escalation_rules(),
        final_score=calculation.final_score if has_score else None,
        risk_band=calculation.risk_band,
        is_provisional=calculation.is_provisional,
        rated_factor_count=rated_count,
        applicable_factor_count=len(inputs),
        escalated=calculation.escalated,
        escalation_reasons=calculation.get_escalation_reasons(),
        triggered_rules=calculation.get_triggered_rules(),
        mandatory_review=bool(calculation.mandatory_review),
        methodology_version=calculation.methodology_version,
        methodology_fingerprint=calculation.methodology_fingerprint,
        reference_data=calculation.get_reference_data(),
        jurisdiction_matches=calculation.get_jurisdiction_matches(),
        calculated_by=calculation.calculated_by,
        calculated_at=calculation.calculated_at,
        overridden=calculation.overridden,
        calculated_score=calculation.calculated_score,
        calculated_band=calculation.calculated_band,
        override_value=calculation.override_value,
        override_band=calculation.override_band,
        override_reason=calculation.override_reason,
        override_by=calculation.override_by,
        override_at=calculation.override_at,
        version=calculation.version,
        is_current=calculation.is_current,
    )


@router.get(
    "/{assessment_id}/inherent-risk",
    response_model=InherentRiskCalculationResponse,
)
def get_inherent_risk_calculation(
    assessment_id: int,
    db: Session = Depends(get_db),
):
    """
    Stage 6 (R6.5): calculation transparency -- returns the current
    deterministic inherent-risk calculation (inputs, weights, method,
    thresholds/bands, final score, resulting band, provisional/escalation
    flags). Computes one on the fly if none exists yet (e.g. right after
    risk identification, before any factor has been rated).
    """

    assessment = (
        db.query(Assessment)
        .filter(Assessment.id == assessment_id)
        .first()
    )

    if not assessment:
        raise HTTPException(status_code=404, detail="Assessment not found")

    calculation = (
        db.query(InherentRiskCalculation)
        .filter(
            InherentRiskCalculation.assessment_id == assessment_id,
            InherentRiskCalculation.is_current.is_(True),
        )
        .first()
    )

    if not calculation:
        calculation = recalculate_inherent_risk(db, assessment)
        db.commit()
        db.refresh(calculation)

    return _build_inherent_risk_response(calculation)


@router.get("/{assessment_id}/residual-risk")
def get_residual_risk_calculation(
    assessment_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    The residual result: the frozen calculation of record once the
    assessment has reached RESIDUAL_RISK, otherwise a live preview
    through the residual grid (not stored). `frozen` says which.
    """

    assessment = (
        db.query(Assessment)
        .filter(Assessment.id == assessment_id)
        .first()
    )

    if not assessment:
        raise HTTPException(status_code=404, detail="Assessment not found")

    frozen = current_residual_calculation(db, assessment_id)
    if frozen is not None and frozen.frozen:
        return residual_response(frozen)

    computed = compute_residual_risk(db, assessment)
    db.commit()
    result = computed["result"]
    config = computed["config"]
    control_state = computed["control_state"]
    return {
        "id": None,
        "assessment_id": assessment_id,
        "inherent_calculation_id": computed["inherent_calc"].id if computed["inherent_calc"] else None,
        "methodology_id": config["methodology_id"],
        "methodology_version": config["methodology_version"],
        "methodology_fingerprint": config["methodology_fingerprint"],
        "inherent_band": computed["inherent_band"],
        "inherent_score": computed["inherent_score"],
        "control_rating": control_state["control_rating"],
        "control_ratings": control_state["control_ratings"],
        "control_reduction": control_state["control_reduction"],
        "residual_grid": config["residual_grid"],
        "grid_version": result["grid_version"],
        "grid_band": result["grid_band"],
        "floors_applied": result["floors_applied"],
        "residual_band": result["residual_band"],
        "residual_score": computed["residual_score"],
        "reason": result["reason"],
        "frozen": False,
        "calculated_by": None,
        "calculated_at": None,
        "version": None,
    }


@router.get(
    "/{assessment_id}/inherent-risk/history",
    response_model=list[InherentRiskCalculationResponse],
)
def get_inherent_risk_history(
    assessment_id: int,
    db: Session = Depends(get_db),
):
    """
    R6.5 / Stage 6 acceptance criteria: every calculation version, newest
    first -- a recalculation supersedes the previous result but never
    deletes it, and this is where the previous results are read back.
    """

    if not db.query(Assessment.id).filter(Assessment.id == assessment_id).first():
        raise HTTPException(status_code=404, detail="Assessment not found")

    calculations = (
        db.query(InherentRiskCalculation)
        .filter(InherentRiskCalculation.assessment_id == assessment_id)
        .order_by(InherentRiskCalculation.version.desc())
        .all()
    )

    return [_build_inherent_risk_response(calculation) for calculation in calculations]


@router.post(
    "/{assessment_id}/inherent-risk/override",
    response_model=InherentRiskCalculationResponse,
)
def override_inherent_risk_calculation(
    assessment_id: int,
    payload: InherentRiskOverrideCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_pipeline_role),
):
    """
    R6.7: an authorized analyst overriding the calculated inherent-risk
    rating. Records the calculated value, overridden value, user,
    timestamp and reason on the current calculation row, and -- if this
    assessment has already frozen an inherent_score snapshot (i.e. it's
    at/past INHERENT_RISK_ASSESSMENT) -- updates that snapshot too, so
    downstream residual-risk math reflects the override.
    """

    assessment = (
        db.query(Assessment)
        .filter(Assessment.id == assessment_id)
        .first()
    )

    if not assessment:
        raise HTTPException(status_code=404, detail="Assessment not found")

    _ensure_override_allowed(assessment)

    calculation = _apply_inherent_override(
        db,
        assessment,
        override_value=payload.override_value,
        override_band=payload.override_band,
        reason=payload.reason,
        user=current_user,
        source="Inherent risk override",
    )

    db.commit()
    db.refresh(calculation)

    return _build_inherent_risk_response(calculation)


def _build_draft_response(draft: AssessmentDraft) -> AssessmentDraftResponse:
    return AssessmentDraftResponse(
        id=draft.id,
        assessment_id=draft.assessment_id,
        executive_summary=draft.executive_summary,
        business_change_description=draft.business_change_description,
        business_profile=draft.get_business_profile(),
        applicable_risk_categories=draft.get_applicable_risk_categories(),
        risk_indicators=draft.get_risk_indicators(),
        risk_statements=draft.get_risk_statements(),
        inherent_risk=draft.get_inherent_risk(),
        evidence_references=draft.get_evidence_references(),
        mapped_controls=draft.get_mapped_controls(),
        control_effectiveness=draft.get_control_effectiveness(),
        residual_risk=draft.get_residual_risk(),
        risk_gaps=draft.get_risk_gaps(),
        assumptions=draft.get_assumptions(),
        missing_information=draft.get_missing_information(),
        recommended_conditions=draft.get_recommended_conditions(),
        analyst_recommendation=ensure_advisory_wording(draft.analyst_recommendation),
        recommendation_status="ADVISORY",
        recommendation_notice=recommendation_notice(bool(draft.is_edited)),
        required_approvals=draft.get_required_approvals(),
        uncertainty=draft.get_uncertainty(),
        generated_at=draft.generated_at,
        generation_method=draft.generation_method,
        model_version=draft.model_version,
        config_version=draft.config_version,
        source_evidence=draft.get_source_evidence(),
        generated_by=draft.generated_by,
        is_edited=draft.is_edited,
        edited_by=draft.edited_by,
        edited_at=draft.edited_at,
        accepted_by=draft.accepted_by,
        accepted_at=draft.accepted_at,
        version=draft.version,
        is_current=draft.is_current,
        created_at=draft.created_at,
    )


def _get_current_draft(db: Session, assessment_id: int) -> AssessmentDraft | None:
    return (
        db.query(AssessmentDraft)
        .filter(
            AssessmentDraft.assessment_id == assessment_id,
            AssessmentDraft.is_current.is_(True),
        )
        .first()
    )


@router.post(
    "/{assessment_id}/draft/generate",
    response_model=AssessmentDraftResponse,
)
def generate_draft(
    assessment_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_pipeline_role),
):
    """
    Stage 9 (R9.1): (re)generates the structured, decision-ready
    assessment draft from every prior stage's current data. Supersedes
    (never overwrites) any existing current draft, so a past generation
    stays retrievable via GET /{assessment_id}/draft/versions (R9.3).
    """

    assessment = (
        db.query(Assessment)
        .filter(Assessment.id == assessment_id)
        .first()
    )

    if not assessment:
        raise HTTPException(status_code=404, detail="Assessment not found")

    ensure_assessment_editable(assessment)

    draft = generate_assessment_draft(db, assessment, requested_by=current_user.email)

    log_audit_event(
        db=db,
        assessment_id=assessment_id,
        action=AuditAction.STATUS_CHANGE,
        actor=current_user.email,
        details=f"Assessment draft generated (version {draft.version}).",
    )

    db.commit()
    db.refresh(draft)

    return _build_draft_response(draft)


@router.get(
    "/{assessment_id}/draft",
    response_model=AssessmentDraftResponse,
)
def get_draft(
    assessment_id: int,
    db: Session = Depends(get_db),
):
    draft = _get_current_draft(db, assessment_id)

    if not draft:
        raise HTTPException(
            status_code=404,
            detail="No assessment draft has been generated yet.",
        )

    return _build_draft_response(draft)


@router.get(
    "/{assessment_id}/draft/versions",
    response_model=list[AssessmentDraftVersionSummary],
)
def list_draft_versions(
    assessment_id: int,
    db: Session = Depends(get_db),
):
    """R9.3: every past generation/edit of this assessment's draft, including the original AI-generated content."""

    return (
        db.query(AssessmentDraft)
        .filter(AssessmentDraft.assessment_id == assessment_id)
        .order_by(AssessmentDraft.version.desc())
        .all()
    )


@router.get(
    "/{assessment_id}/draft/versions/{draft_id}",
    response_model=AssessmentDraftResponse,
)
def get_draft_version(
    assessment_id: int,
    draft_id: int,
    db: Session = Depends(get_db),
):
    """R9.3 acceptance criteria: an analyst edit never overwrites the
    generated content -- any earlier version can be read back in full."""

    draft = (
        db.query(AssessmentDraft)
        .filter(AssessmentDraft.id == draft_id, AssessmentDraft.assessment_id == assessment_id)
        .first()
    )
    if not draft:
        raise HTTPException(status_code=404, detail="Draft version not found")

    return _build_draft_response(draft)


@router.patch(
    "/{assessment_id}/draft",
    response_model=AssessmentDraftResponse,
)
def update_draft(
    assessment_id: int,
    payload: AssessmentDraftUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_pipeline_role),
):
    """
    R9.3: an authorized analyst's edit. Creates a new current version
    (copying forward every field, overriding only what was supplied) so
    the original generated content is retained as an earlier version
    rather than being overwritten.
    """

    assessment = (
        db.query(Assessment)
        .filter(Assessment.id == assessment_id)
        .first()
    )

    if not assessment:
        raise HTTPException(status_code=404, detail="Assessment not found")

    ensure_assessment_editable(assessment)

    current = _get_current_draft(db, assessment_id)

    if not current:
        raise HTTPException(
            status_code=404,
            detail="No assessment draft has been generated yet.",
        )

    now = datetime.now(timezone.utc)
    current.is_current = False
    current.superseded_at = now

    edited = AssessmentDraft(
        assessment_id=assessment_id,
        executive_summary=payload.executive_summary if payload.executive_summary is not None else current.executive_summary,
        business_change_description=payload.business_change_description if payload.business_change_description is not None else current.business_change_description,
        business_profile=current.business_profile,
        applicable_risk_categories=current.applicable_risk_categories,
        risk_indicators=current.risk_indicators,
        risk_statements=json.dumps([s.model_dump() for s in payload.risk_statements]) if payload.risk_statements is not None else current.risk_statements,
        inherent_risk=current.inherent_risk,
        evidence_references=current.evidence_references,
        mapped_controls=current.mapped_controls,
        control_effectiveness=current.control_effectiveness,
        residual_risk=current.residual_risk,
        risk_gaps=current.risk_gaps,
        assumptions=json.dumps(payload.assumptions) if payload.assumptions is not None else current.assumptions,
        missing_information=json.dumps(payload.missing_information) if payload.missing_information is not None else current.missing_information,
        recommended_conditions=json.dumps(payload.recommended_conditions) if payload.recommended_conditions is not None else current.recommended_conditions,
        analyst_recommendation=payload.analyst_recommendation if payload.analyst_recommendation is not None else current.analyst_recommendation,
        required_approvals=json.dumps(payload.required_approvals) if payload.required_approvals is not None else current.required_approvals,
        uncertainty=current.uncertainty,
        generated_at=current.generated_at,
        generation_method=current.generation_method,
        model_version=current.model_version,
        config_version=current.config_version,
        source_evidence=current.source_evidence,
        generated_by=current.generated_by,
        is_edited=True,
        edited_by=actor_name(current_user),
        edited_at=now,
        accepted_by=None,
        accepted_at=None,
        version=current.version + 1,
        is_current=True,
    )

    db.add(edited)

    log_audit_event(
        db=db,
        assessment_id=assessment_id,
        action=AuditAction.STATUS_CHANGE,
        actor=actor_name(current_user),
        actor_id=current_user.id,
        details=f"Assessment draft edited (version {edited.version}).",
    )

    db.commit()
    db.refresh(edited)

    return _build_draft_response(edited)


@router.post(
    "/{assessment_id}/draft/accept",
    response_model=AssessmentDraftResponse,
)
def accept_draft(
    assessment_id: int,
    payload: AssessmentDraftAccept,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_pipeline_role),
):
    """R9.4: records who accepted the current draft content, and when."""

    assessment = db.query(Assessment).filter(Assessment.id == assessment_id).first()
    if not assessment:
        raise HTTPException(status_code=404, detail="Assessment not found")

    ensure_assessment_editable(assessment)

    draft = _get_current_draft(db, assessment_id)

    if not draft:
        raise HTTPException(
            status_code=404,
            detail="No assessment draft has been generated yet.",
        )

    draft.accepted_by = actor_name(current_user)
    draft.accepted_at = datetime.now(timezone.utc)

    log_audit_event(
        db=db,
        assessment_id=assessment_id,
        action=AuditAction.STATUS_CHANGE,
        actor=draft.accepted_by,
        actor_id=current_user.id,
        details="Assessment draft accepted.",
    )

    db.commit()
    db.refresh(draft)

    return _build_draft_response(draft)


@router.get("/documents/{document_id}/file")
def get_document_file(
    document_id: int,
    request: Request,
    disposition: str = Query(
        "attachment",
        pattern="^(inline|attachment)$",
        description="inline = view in the browser, attachment = download. Both are audit-logged.",
    ),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    document = (
        db.query(AssessmentDocument)
        .filter(AssessmentDocument.id == document_id)
        .first()
    )

    if not document or not document.file_path:
        raise HTTPException(status_code=404, detail="Document file not found")

    # R15.3: the original file of a CONFIDENTIAL or RESTRICTED document is
    # only served to people who may read its text unmasked (the
    # assessment's owner and reviewing analysts); everyone else sees the
    # masked extracted text with the document details. Visibility of the
    # assessment itself (role + R15.4 scope) is enforced by the global
    # access gate before this runs.
    assessment = db.query(Assessment).filter(Assessment.id == document.assessment_id).first()
    classification = (document.confidentiality or "").upper()
    if not can_open_original_file(document.confidentiality, current_user, assessment.owner_id if assessment else None):
        # R15.5: the refused attempt is recorded.
        log_denied_attempt(
            db,
            current_user,
            request,
            f"{classification} document {document.id} original file",
            assessment_id=document.assessment_id,
        )
        raise HTTPException(
            status_code=403,
            detail=(
                f"This document is classified {classification}. Only the assessment owner and "
                "reviewing analysts can open the original file; a masked text version is "
                "shown with the document details."
            ),
        )

    if not os.path.exists(document.file_path):
        raise HTTPException(status_code=404, detail="File no longer exists on disk")

    try:
        content = read_file(document.file_path)
    except RuntimeError as exc:
        raise HTTPException(status_code=500, detail=str(exc))

    extension = os.path.splitext(document.filename)[1].lower()
    media_type = MIME_TYPES.get(extension) or mimetypes.guess_type(document.filename)[0] or "application/octet-stream"

    # R15.5: every view and download is recorded -- who, which document
    # and version, its classification. Never the content.
    viewed = disposition == "inline"
    log_audit_event(
        db=db,
        assessment_id=document.assessment_id,
        action=AuditAction.DOCUMENT_VIEWED if viewed else AuditAction.DOCUMENT_DOWNLOADED,
        actor=actor_name(current_user),
        actor_id=current_user.id,
        details=(
            f"{'Viewed' if viewed else 'Downloaded'} document {document.id} "
            f"(v{document.version}, {classification or 'UNCLASSIFIED'})."
        ),
    )
    db.commit()

    # Stage 19: served from memory (not FileResponse) because the file may
    # be encrypted at rest and must be decrypted first. Cache-Control:
    # no-store and nosniff come from SecurityHeadersMiddleware.
    headers = {"Content-Disposition": content_disposition(disposition, document.filename)}
    if viewed:
        # A file rendered in the browser can't run script or reach the
        # app's origin.
        headers["Content-Security-Policy"] = "sandbox"
    return Response(content=content, media_type=media_type, headers=headers)


# @router.post(
#     "/create-with-document",
#     response_model=AssessmentResponse,
# )
# async def create_assessment_with_document(
#     title: str = Form(...),
#     change_type: str = Form(...),
#     description: str = Form(...),
#     evidence: str = Form(...),
#     file: UploadFile = File(...),
#     db: Session = Depends(get_db),
# ):
#     allowed_extensions = {".docx", ".doc", ".xlsx", ".pdf", ".txt", ".csv"}
#     filename = file.filename or ""
#     extension = "." + filename.split(".")[-1].lower() if "." in filename else ""

#     if extension not in allowed_extensions:
#         raise HTTPException(
#             status_code=400,
#             detail="Unsupported file type. Supported files: DOCX, DOC, XLSX, PDF, TXT, CSV.",
#         )

#     file_content = await file.read()

#     if not file_content:
#         raise HTTPException(status_code=400, detail="Uploaded file is empty.")

#     try:
#         extracted_text = extract_text(filename=filename, file_content=file_content)

#         # -----------------------------------------
#         # 1. Create assessment using the user's
#         #    reviewed/edited field values
#         # -----------------------------------------
#         assessment = Assessment(
#             title=title,
#             change_type=change_type,
#             description=description,
#             evidence=evidence,
#             status="DRAFT",
#         )

#         db.add(assessment)
#         db.flush()
#         print("STEP 2: assessment created, id =", assessment.id)

#         # -----------------------------------------
#         # 2. Save the original file
#         # -----------------------------------------
#         saved_path = save_file(assessment.id, filename, file_content)

#         document = AssessmentDocument(
#             assessment_id=assessment.id,
#             filename=filename,
#             file_type=extension,
#             extracted_text=extracted_text,
#             file_path=saved_path,
#         )

#         db.add(document)
#         print("STEP 3: document added")

#         # -----------------------------------------
#         # 3. Log audit event
#         # -----------------------------------------
#         from app.document_analysis.analyzer import analyze_document_text
#         print("STEP 4: about to call analyze_document_text")
#         structured_data = analyze_document_text(extracted_text)
#         print("STEP 5: structured_data received:", structured_data.business_line)
#         log_audit_event(
#             db=db,
#             assessment_id=assessment.id,
#             action=AuditAction.CREATED,
#             previous_status=None,
#             new_status=assessment.status,
#             details=f"Assessment created from uploaded document: {filename}.",
#         )

#         db.commit()
#         db.refresh(assessment)
#         print("STEP 4: db committed and assessment refreshed")
#         return assessment

#     except HTTPException:
#         db.rollback()
#         raise

#     except Exception as exc:
#         db.rollback()
#         raise HTTPException(
#             status_code=500,
#             detail=f"Unable to create assessment: {str(exc)}",
#         )
@router.post(
    "/create-with-document",
    response_model=AssessmentResponse,
)
async def create_assessment_with_document(
    title: str = Form(...),
    change_type: str = Form(...),
    description: str = Form(""),
    evidence: str = Form(""),
    # R1.1 request fields, all optional so this request can still be
    # saved as a draft (R1.3) with an attached document.
    product_or_service_name: str = Form(None),
    business_owner: str = Form(None),
    legal_entity: str = Form(None),
    business_unit: str = Form(None),
    customer_segment: str = Form(None),
    countries_jurisdictions: str = Form(None),
    delivery_channels: str = Form(None),
    expected_transaction_volume: str = Form(None),
    expected_transaction_value: str = Form(None),
    transaction_types: str = Form(None),
    third_party_vendor_usage: str = Form(None),
    technology_process_changes: str = Form(None),
    expected_launch_date: str = Form(None),
    # "true"/"false", or blank for not answered.
    shell_company_indicator: str = Form(None),
    is_draft: bool = Form(True),
    submitted_by: str = Form(None),
    files: list[UploadFile] = File(...),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    shell_flag = _optional_form_bool(shell_company_indicator)
    documents_to_save = await _read_and_expand_uploads(files)

    # R1.4: validate mandatory fields before allowing a submission (not a
    # draft) to go through.
    if not is_draft:
        missing_fields = get_missing_mandatory_fields(
            {
                "title": title,
                "change_type": change_type,
                "product_or_service_name": product_or_service_name,
                "description": description,
                "business_owner": business_owner,
                "legal_entity": legal_entity,
                "customer_segment": customer_segment,
                "countries_jurisdictions": countries_jurisdictions,
                "delivery_channels": delivery_channels,
                "expected_transaction_volume": expected_transaction_volume,
                "expected_transaction_value": expected_transaction_value,
                "transaction_types": transaction_types,
                "third_party_vendor_usage": third_party_vendor_usage,
                "technology_process_changes": technology_process_changes,
                "expected_launch_date": expected_launch_date,
            }
        )

        if missing_fields:
            raise HTTPException(
                status_code=422,
                detail={
                    "message": (
                        "Missing mandatory information. Complete these "
                        "fields before submitting, or save as a draft."
                    ),
                    "missing_fields": missing_fields,
                },
            )

    try:
        # -----------------------------------------
        # 1. Create assessment using the user's
        #    reviewed/edited field values
        # -----------------------------------------
        assessment = Assessment(
            title=title,
            change_type=change_type,
            description=description,
            evidence=evidence,
            product_or_service_name=product_or_service_name,
            business_owner=business_owner,
            legal_entity=legal_entity,
            business_unit=business_unit or None,
            customer_segment=customer_segment,
            countries_jurisdictions=countries_jurisdictions,
            delivery_channels=delivery_channels,
            expected_transaction_volume=expected_transaction_volume,
            expected_transaction_value=expected_transaction_value,
            transaction_types=transaction_types,
            third_party_vendor_usage=third_party_vendor_usage,
            technology_process_changes=technology_process_changes,
            expected_launch_date=expected_launch_date,
            shell_company_indicator=shell_flag,
            is_draft=is_draft,
            submitted_by=actor_name(current_user),
            submitted_at=None if is_draft else datetime.now(timezone.utc),
            status="INTAKE",
        )
        assessment.owner_id = current_user.id

        db.add(assessment)
        db.flush()

        assessment.reference_id = generate_reference_id(assessment.id)

        # -----------------------------------------
        # 2. Save each uploaded file (or, for a zip,
        #    each supported document inside it) as
        #    its own AssessmentDocument
        # -----------------------------------------
        saved_documents: list[AssessmentDocument] = []
        combined_sections = []

        for doc_filename, doc_content in documents_to_save:
            doc_extension = (
                "." + doc_filename.split(".")[-1].lower()
                if "." in doc_filename
                else ""
            )
            # Stage 19 (Reliability): one unreadable document must not
            # roll back the whole request and lose what the user entered.
            # The file is stored regardless; its extraction is recorded as
            # a FAILED, retryable ProcessingJob instead.
            extraction_error = None
            try:
                doc_extracted_text = extract_text(
                    filename=doc_filename, file_content=doc_content
                )
            except Exception as exc:  # noqa: BLE001 -- parser errors vary by format
                doc_extracted_text = ""
                extraction_error = str(exc)
            saved_path = save_file(assessment.id, doc_filename, doc_content)

            document = AssessmentDocument(
                assessment_id=assessment.id,
                filename=doc_filename,
                file_type=doc_extension,
                extracted_text=doc_extracted_text,
                file_path=saved_path,
                document_type="BUSINESS_REQUIREMENT",
            )

            db.add(document)
            db.flush()

            if extraction_error is not None:
                failed_job = processing_jobs.create_job(
                    db,
                    ProcessingJobType.DOCUMENT_EXTRACTION,
                    assessment_id=assessment.id,
                    document_id=document.id,
                    user=current_user,
                )
                failed_job.status = ProcessingJobStatus.FAILED.value
                failed_job.attempts = 1
                failed_job.stage_message = "Failed"
                failed_job.error_message = (
                    f"We couldn't read the text in {doc_filename}. The file is stored "
                    "and the assessment was created -- retry, or upload a different version."
                )
                failed_job.error_detail = extraction_error
                failed_job.finished_at = datetime.now(timezone.utc)

            saved_documents.append(document)

            if doc_extracted_text.strip():
                combined_sections.append(
                    f"--- {doc_filename} ---\n{doc_extracted_text}"
                )

            _index_for_semantic_search(
                db, assessment.id, doc_extracted_text, document_id=document.id
            )

        combined_text = "\n\n".join(combined_sections)

        # -----------------------------------------
        # 3. Extract structured business intelligence
        #    from the combined text of every uploaded
        #    document
        # -----------------------------------------
        from app.document_analysis.analyzer import analyze_document_text

        try:
            structured_data = analyze_document_text(combined_text)
        except Exception as exc:  # noqa: BLE001 -- keep the assessment regardless
            logging.getLogger(__name__).exception(
                "Document intelligence extraction failed for assessment %s", assessment.id
            )
            structured_data = None
            log_audit_event(
                db=db,
                assessment_id=assessment.id,
                action=AuditAction.PROCESSING_INCOMPLETE,
                details=(
                    "Business intelligence could not be extracted from the uploaded "
                    f"document(s) ({exc}). The assessment was created; fill in the "
                    "profile manually or re-upload."
                ),
            )

        if structured_data is not None:
            intelligence = AssessmentIntelligence(
                assessment_id=assessment.id,
                business_line=structured_data.business_line,
                transaction_volume=structured_data.transaction_volume,
                average_transaction_size=structured_data.average_transaction_size,
                maximum_transaction_limit=structured_data.maximum_transaction_limit,
                source_document_id=saved_documents[0].id,
                raw_extraction=json.dumps(structured_data.model_dump()),
            )

            intelligence.set_list("channels", structured_data.channels)
            intelligence.set_list("countries", structured_data.countries)
            intelligence.set_list("customer_segments", structured_data.customer_segments)
            intelligence.set_list("third_party_vendors", structured_data.third_party_vendors)
            intelligence.set_list("data_shared", structured_data.data_shared)
            intelligence.set_list("technologies", structured_data.technologies)
            intelligence.set_list(
                "regulatory_considerations", structured_data.regulatory_considerations
            )
            intelligence.set_list("existing_controls", structured_data.existing_controls)
            intelligence.set_list(
                "additional_risk_factors", structured_data.additional_risk_factors
            )

            _record_extraction_provenance(intelligence, structured_data, saved_documents)
            db.add(intelligence)
            db.flush()
            _snapshot_new_profile(
                db,
                intelligence,
                current_user,
                "PROFILE_EXTRACTED",
                "Profile extracted from: " + ", ".join(doc.filename for doc in saved_documents) + ".",
            )

        _snapshot_new_intake(db, assessment, current_user)

        # -----------------------------------------
        # 4. Log audit event
        # -----------------------------------------
        log_audit_event(
            db=db,
            assessment_id=assessment.id,
            action=AuditAction.CREATED,
            previous_status=None,
            new_status=assessment.status,
            details=(
                "Assessment created from uploaded document(s): "
                + ", ".join(doc.filename for doc in saved_documents)
                + "."
            ),
        )

        if not assessment.is_draft:
            _apply_intake_triage_and_routing(db, assessment)

        workflow.record_creation(
            db,
            assessment,
            current_user,
            (
                "Draft request created with attached document(s)."
                if assessment.is_draft
                else "Request created with attached document(s) and submitted."
            ),
        )

        db.commit()
        db.refresh(assessment)

        return assessment

    except HTTPException:
        db.rollback()
        raise

    except Exception as exc:
        db.rollback()
        raise HTTPException(
            status_code=500,
            detail=f"Unable to create assessment: {str(exc)}",
        )
def _get_control_reduction_for_challenge(db: Session, assessment_id: int) -> float:
    """
    Stage 7: the deterministic control-reduction contribution, computed
    from whatever Controls/ControlAssessments are actually mapped to this
    assessment's risk factors (see app/control_engine/). Replaces the
    former hardcoded per-dimension heuristic -- a risk with no mapped
    control, or only an unverified/ineffective one, now genuinely
    contributes 0 reduction instead of a fixed per-dimension guess.
    """

    return recompute_control_state(db, assessment_id)["control_reduction"]


def _residual_preview(db: Session, assessment: Assessment) -> dict:
    """
    The residual result as it would be frozen now (grid lookup, no row
    written). Used by the challenge screens, which show residual risk
    before the RESIDUAL_RISK stage freezes it.
    """

    computed = compute_residual_risk(db, assessment)
    result = computed["result"]
    return {
        "control_reduction": computed["control_state"]["control_reduction"],
        "control_rating": computed["control_state"]["control_rating"],
        "residual_score": computed["residual_score"],
        "residual_level": result["residual_band"],
        "residual_reason": result["reason"],
    }


def _build_challenge_findings(
    assessment,
    risk_results,
    intelligence,
):
    findings = []

    critical_results = [
        result
        for result in risk_results
        if determine_risk_band(result.score, risk_bands) == "CRITICAL"
    ]

    high_results = [
        result
        for result in risk_results
        if determine_risk_band(result.score, risk_bands) == "HIGH"
    ]

    if critical_results:
        dimensions = ", ".join(
            result.dimension
            for result in critical_results
        )

        findings.append(
            ChallengeFinding(
                title="Critical Risk Dimension Challenge",
                severity="CRITICAL",
                finding=(
                    f"The assessment contains critical risk "
                    f"exposure in {dimensions}. The challenge "
                    f"requires explicit acknowledgement of these "
                    f"risk dimensions before final committee "
                    f"disposition."
                ),
            )
        )

    if high_results:
        dimensions = ", ".join(
            result.dimension
            for result in high_results
        )

        findings.append(
            ChallengeFinding(
                title="High Risk Validation",
                severity="HIGH",
                finding=(
                    f"High risk exposure was identified in "
                    f"{dimensions}. The reviewer should confirm "
                    f"that the mapped controls adequately address "
                    f"the underlying risk drivers."
                ),
            )
        )

    if intelligence:
        countries = intelligence.get_list("countries")

        if len(countries) > 1:
            findings.append(
                ChallengeFinding(
                    title="Geographic Exposure Challenge",
                    severity="HIGH",
                    finding=(
                        f"The assessment involves multiple "
                        f"jurisdictions ({', '.join(countries)}). "
                        f"Cross-border regulatory obligations and "
                        f"country-specific controls should be "
                        f"validated before approval."
                    ),
                )
            )

        vendors = intelligence.get_list(
            "third_party_vendors"
        )

        if vendors:
            findings.append(
                ChallengeFinding(
                    title="Third-Party Dependency Challenge",
                    severity="HIGH",
                    finding=(
                        f"The assessment relies on third-party "
                        f"processor(s): {', '.join(vendors)}. "
                        f"Due diligence, SLA coverage and fallback "
                        f"controls should be confirmed."
                    ),
                )
            )

        data_shared = intelligence.get_list(
            "data_shared"
        )

        if data_shared:
            findings.append(
                ChallengeFinding(
                    title="Customer Data Challenge",
                    severity="HIGH",
                    finding=(
                        "Customer or transaction information is "
                        "shared as part of the proposed change. "
                        "Data protection, access control and "
                        "regulatory requirements should be "
                        "validated."
                    ),
                )
            )

    if assessment.evidence:
        findings.append(
            ChallengeFinding(
                title="Evidence Consistency",
                severity="LOW",
                finding=(
                    "Assessment evidence is available for review. "
                    "Evidence should remain traceable to the "
                    "identified risks and mapped controls."
                ),
            )
        )
    else:
        findings.append(
            ChallengeFinding(
                title="Evidence Consistency",
                severity="HIGH",
                finding=(
                    "No assessment evidence is currently "
                    "available. Additional evidence is required "
                    "before final disposition."
                ),
            )
        )

    if not findings:
        findings.append(
            ChallengeFinding(
                title="General Risk Challenge",
                severity="MEDIUM",
                finding=(
                    "The challenge review did not identify a "
                    "specific critical exception. The reviewer "
                    "should validate the overall risk rationale "
    risk_bands=None,
                    "and supporting controls."
                ),
            )
    # Bands come from the methodology in force (risk_bands), falling back to
    # the built-in defaults, so a score is called CRITICAL/HIGH here exactly
    # when it is everywhere else.
        )

    return findings
@router.get(
    "/{assessment_id}/challenge",
    response_model=AssessmentChallengeResponse,
)
def get_assessment_challenge(
    assessment_id: int,
    db: Session = Depends(get_db),
):
    assessment = (
        db.query(Assessment)
        .filter(Assessment.id == assessment_id)
        .first()
    )

    if not assessment:
        raise HTTPException(
            status_code=404,
            detail="Assessment not found",
        )

    risk_results = (
        db.query(RiskResult)
        .filter(
            RiskResult.assessment_id == assessment_id,
            RiskResult.is_current.is_(True),
        )
        .all()
    )

    intelligence = (
        db.query(AssessmentIntelligence)
        .filter(
            AssessmentIntelligence.assessment_id
            == assessment_id
        )
        .first()
    )

    challenge = (
        db.query(AssessmentChallenge)
        .filter(
            AssessmentChallenge.assessment_id
            == assessment_id
        )
        .first()
    )

    if not challenge:
        challenge = AssessmentChallenge(
            assessment_id=assessment_id,
            challenge_id=f"CHL-{assessment_id:04d}-01",
            status="OPEN",
            challenged_by="FCRM Reviewer",
        )

        db.add(challenge)
        db.commit()
        db.refresh(challenge)

    # Prefer the frozen inherent-risk snapshot (set on arrival at
    # INHERENT_RISK_ASSESSMENT) once it exists; fall back to the live
    # overall_score for assessments still earlier in the pipeline.
    inherent_score = (
        assessment.inherent_score
        if assessment.inherent_score is not None
        else (assessment.overall_score or 0)
    )

    residual_preview = _residual_preview(db, assessment)
    control_reduction = residual_preview["control_reduction"]

    findings = _build_challenge_findings(
        assessment,
        risk_results,
        intelligence,
    )

    return {
        "id": challenge.id,
        "assessment_id": challenge.assessment_id,
        "challenge_id": challenge.challenge_id,
        "status": challenge.status,
        "outcome": challenge.outcome,
        "comment": challenge.comment,
        "challenged_by": challenge.challenged_by,
        "created_at": challenge.created_at,
        "updated_at": challenge.updated_at,
        "findings": findings,
        "inherent_score": inherent_score,
        "residual_score": residual_preview["residual_score"],
        "residual_level": residual_preview["residual_level"],
        "residual_reason": residual_preview["residual_reason"],
        "control_rating": residual_preview["control_rating"],
        "control_reduction": control_reduction,
    }
@router.patch(
    "/{assessment_id}/challenge",
    response_model=AssessmentChallengeResponse,
)
def update_assessment_challenge(
    assessment_id: int,
    payload: ChallengeUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_pipeline_role),
):
    assessment = (
        db.query(Assessment)
        .filter(Assessment.id == assessment_id)
        .first()
    )

    if not assessment:
        raise HTTPException(
            status_code=404,
            detail="Assessment not found",
        )

    ensure_assessment_editable(assessment)

    if payload.outcome not in {
        "ACCEPTED",
        "REMEDIATION_REQUIRED",
        "ESCALATE",
    }:
        raise HTTPException(
            status_code=400,
            detail="Invalid challenge outcome",
        )

    if not payload.comment.strip():
        raise HTTPException(
            status_code=400,
            detail=(
                "Challenge commentary is required."
            ),
        )

    challenge = (
        db.query(AssessmentChallenge)
        .filter(
            AssessmentChallenge.assessment_id
            == assessment_id
        )
        .first()
    )

    if not challenge:
        challenge = AssessmentChallenge(
            assessment_id=assessment_id,
            challenge_id=f"CHL-{assessment_id:04d}-01",
        )

        db.add(challenge)

    # NOTE: this endpoint records the control-assessment/challenge outcome
    # only. It deliberately does NOT move the assessment's pipeline stage
    # (assessment.status) any more — that is a separate, explicit action
    # via PATCH /{assessment_id}/advance-stage (CONTROL_ASSESSMENT ->
    # RESIDUAL_RISK), which requires this outcome to already be recorded.
    # This keeps "record the control assessment result" and "approve and
    # move to the next stage" as two distinct human actions.
    challenge.outcome = payload.outcome
    challenge.comment = payload.comment
    challenge.status = "COMPLETED"

    log_audit_event(
        db=db,
        assessment_id=assessment_id,
        action=AuditAction.CHALLENGE,
        previous_status=assessment.status,
        new_status=assessment.status,
        details=(
            f"Challenge {challenge.challenge_id} "
            f"completed with outcome "
            f"{payload.outcome}."
        ),
    )

    db.commit()
    db.refresh(challenge)
    db.refresh(assessment)

    risk_results = (
        db.query(RiskResult)
        .filter(
            RiskResult.assessment_id == assessment_id,
            RiskResult.is_current.is_(True),
        )
        .all()
    )

    intelligence = (
        db.query(AssessmentIntelligence)
        .filter(
            AssessmentIntelligence.assessment_id
            == assessment_id
        )
        .first()
    )

    inherent_score = (
        assessment.inherent_score
        if assessment.inherent_score is not None
        else (assessment.overall_score or 0)
    )

    residual_preview = _residual_preview(db, assessment)
    control_reduction = residual_preview["control_reduction"]

    return {
        "id": challenge.id,
        "assessment_id": challenge.assessment_id,
        "challenge_id": challenge.challenge_id,
        "status": challenge.status,
        "outcome": challenge.outcome,
        "comment": challenge.comment,
        "challenged_by": challenge.challenged_by,
        "created_at": challenge.created_at,
        get_methodology_config(db)["risk_bands"],
        "updated_at": challenge.updated_at,
        "findings": _build_challenge_findings(
            assessment,
            risk_results,
            intelligence,
        ),
        "inherent_score": inherent_score,
        "residual_score": residual_preview["residual_score"],
        "residual_level": residual_preview["residual_level"],
        "residual_reason": residual_preview["residual_reason"],
        "control_rating": residual_preview["control_rating"],
        "control_reduction": control_reduction,
    }

@router.get(
    "/{assessment_id}/fcrm-review",
    response_model=FcrmReviewResponse,
)
def get_fcrm_review(
    assessment_id: int,
    db: Session = Depends(get_db),
):
    assessment = (
        db.query(Assessment)
        .filter(Assessment.id == assessment_id)
        .first()
    )

    if not assessment:
        raise HTTPException(
            status_code=404,
            detail="Assessment not found",
        )

    review = (
        db.query(AssessmentFcrmReview)
        .filter(
            AssessmentFcrmReview.assessment_id == assessment_id
        )
        .first()
    )

    if not review:
        # Nothing saved yet — return an empty review rather than 404 so
        # the frontend can always load this endpoint on mount.
        return {
            "assessment_id": assessment_id,
            "justification": "",
            "human_ratings": {},
            "reviewed_by": None,
            "updated_at": None,
        }

    try:
        human_ratings = (
            json.loads(review.human_ratings)
            if review.human_ratings
            else {}
        )
    except (TypeError, ValueError):
        human_ratings = {}

    return {
        "assessment_id": review.assessment_id,
        "justification": review.justification or "",
        "human_ratings": human_ratings,
        "reviewed_by": review.reviewed_by,
        "updated_at": review.updated_at,
    }


@router.patch(
    "/{assessment_id}/fcrm-review",
    response_model=FcrmReviewResponse,
)
def update_fcrm_review(
    assessment_id: int,
    payload: FcrmReviewUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_pipeline_role),
):
    assessment = (
        db.query(Assessment)
        .filter(Assessment.id == assessment_id)
        .first()
    )

    if not assessment:
        raise HTTPException(
            status_code=404,
            detail="Assessment not found",
        )

    ensure_assessment_editable(assessment)

    review = (
        db.query(AssessmentFcrmReview)
        .filter(
            AssessmentFcrmReview.assessment_id == assessment_id
        )
        .first()
    )

    encoded_ratings = json.dumps(payload.human_ratings)

    # R10.3/R10.4: each human rating that differs from the calculated
    # severity (and from what this review said before) is a material
    # change -- it needs the review's justification as its reason and
    # lands in the override ledger, so a later save never hides it.
    previous_ratings = {}
    if review is not None:
        try:
            previous_ratings = {str(k): v for k, v in json.loads(review.human_ratings or "{}").items()}
        except ValueError:
            previous_ratings = {}
    results = {
        result.id: result
        for result in db.query(RiskResult).filter(RiskResult.assessment_id == assessment_id).all()
    }
    changed = [
        (results[int(result_id)], severity)
        for result_id, severity in payload.human_ratings.items()
        if int(result_id) in results
        and str(severity) != str(results[int(result_id)].severity)
        and previous_ratings.get(str(result_id)) != severity
    ]
    if changed and not (payload.justification or "").strip():
        raise HTTPException(
            status_code=422,
            detail="A justification is required when a human rating differs from the calculated one.",
        )
    from app.services.override_ledger import record_applied

    for result, severity in changed:
        record_applied(
            db,
            assessment_id,
            "FCRM_REVIEW",
            f"{result.dimension} severity",
            result.id,
            result.severity,
            severity,
            payload.justification.strip(),
            current_user,
        )

    if not review:
        review = AssessmentFcrmReview(
            get_methodology_config(db)["risk_bands"],
            assessment_id=assessment_id,
            justification=payload.justification,
            human_ratings=encoded_ratings,
            reviewed_by=actor_name(current_user),
        )
        db.add(review)
    else:
        review.justification = payload.justification
        review.human_ratings = encoded_ratings
        review.reviewed_by = actor_name(current_user)

    log_audit_event(
        db,
        assessment_id=assessment_id,
        action=AuditAction.APPROVAL,
        actor=review.reviewed_by,
        actor_id=current_user.id,
        details="FCRM review saved (justification and rating reconciliation updated).",
    )

    db.commit()
    db.refresh(review)

    return {
        "assessment_id": review.assessment_id,
        "justification": review.justification or "",
        "human_ratings": payload.human_ratings,
        "reviewed_by": review.reviewed_by,
        "updated_at": review.updated_at,
    }


# =============================================================================
# Stage 10: FCRM Analyst Review
# =============================================================================
# R10.2/R10.3/R10.4: a generic log of analyst-made changes to AI-generated
# values, covering every reviewable section from R10.1 (extracted fields,
# risk category, factor ratings, risk rationale, control mappings,
# control effectiveness, residual risk, recommended conditions). Each row
# keeps both the original AI value and the human-confirmed value, plus
# the required reason and who/when -- exactly what R10.4's comparison
# view needs, without a bespoke override table per entity type.


@router.get(
    "/{assessment_id}/overrides",
    response_model=list[OverrideResponse],
)
def list_assessment_overrides(
    assessment_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    assessment = db.query(Assessment).filter(Assessment.id == assessment_id).first()
    if not assessment:
        raise HTTPException(status_code=404, detail="Assessment not found")

    rows = (
        db.query(AssessmentOverride)
        .filter(AssessmentOverride.assessment_id == assessment_id)
        .order_by(AssessmentOverride.created_at.asc())
        .all()
    )
    return [_override_response(db, assessment, row, current_user) for row in rows]


def _override_response(db: Session, assessment: Assessment, row: AssessmentOverride, user: User) -> OverrideResponse:
    from app.governance import overrides as override_rules

    response = OverrideResponse.model_validate(row)
    response.state = override_rules.state(db, row)
    response.actions = override_rules.actions_for(db, assessment, row, user)
    return response


@router.post(
    "/{assessment_id}/overrides",
    response_model=OverrideResponse,
    status_code=201,
)
def create_assessment_override(
    assessment_id: int,
    payload: OverrideCreate,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_pipeline_role),
):
    assessment = db.query(Assessment).filter(Assessment.id == assessment_id).first()
    if not assessment:
        raise HTTPException(status_code=404, detail="Assessment not found")

    ensure_assessment_editable(assessment)

    # P3 (R-GOV-03): only an FCRM Analyst (incl. Senior Analyst) proposes.
    from app.governance import overrides as override_rules

    problem = override_rules.propose_problem(current_user)
    if problem:
        log_denied_attempt(db, current_user, request, f"override proposal refused: {problem}", assessment_id=assessment_id)
        raise HTTPException(status_code=403, detail=problem)

    # R10.4: the value being overridden is read from the record, never
    # taken from the request.
    from app.models.assessment_override import REVIEW_PROPOSED
    from app.services.override_ledger import SYSTEM, resolve_system_value

    system_value = resolve_system_value(db, assessment, payload.section, payload.field_name, payload.entity_id)
    if system_value is not None and str(system_value) == payload.human_value.strip():
        raise HTTPException(status_code=422, detail="The proposed value is the same as the current value.")

    override = AssessmentOverride(
        assessment_id=assessment_id,
        section=payload.section,
        field_name=payload.field_name,
        entity_id=payload.entity_id,
        ai_value=system_value,
        ai_value_source=SYSTEM,
        human_value=payload.human_value,
        reason=payload.reason,
        overridden_by=actor_name(current_user),
        overridden_by_id=current_user.id,
        # A free-standing override changes nothing by itself; it counts
        # once an independent reviewer confirms it.
        review_status=REVIEW_PROPOSED,
        origin=override_rules.ORIGIN_PROPOSAL,
    )
    override_rules.classify_entry(db, override)
    db.add(override)
    db.flush()

    ignored = ""
    if payload.ai_value is not None and payload.ai_value != system_value:
        ignored = " A client-supplied system value was ignored."
    log_audit_event(
        db,
        assessment_id=assessment_id,
        action=AuditAction.OVERRIDE_APPLIED,
        actor=actor_name(current_user),
        actor_id=current_user.id,
        details=(
            f"Override #{override.id} proposed: {payload.section}/{payload.field_name}"
            + (f" (#{payload.entity_id})" if payload.entity_id else "")
            + f" from {system_value!r} to {payload.human_value!r} ({override.materiality}). "
            + f"Reason: {payload.reason}.{ignored}"
        ),
    )

    db.commit()
    db.refresh(override)
    return _override_response(db, assessment, override, current_user)


@router.patch(
    "/{assessment_id}/overrides/{override_id}/review",
    response_model=OverrideResponse,
)
def review_assessment_override(
    assessment_id: int,
    override_id: int,
    payload: OverrideReview,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """R10.2-R10.4: an independent reviewer confirms or rejects a
    PROPOSED override. Neither the person who proposed it nor the
    assessment's owner may review it."""

    from app.governance import overrides as override_rules
    from app.models.assessment_override import REVIEW_CONFIRMED, REVIEW_REJECTED

    assessment = db.query(Assessment).filter(Assessment.id == assessment_id).first()
    if not assessment:
        raise HTTPException(status_code=404, detail="Assessment not found")
    override = (
        db.query(AssessmentOverride)
        .filter(AssessmentOverride.id == override_id, AssessmentOverride.assessment_id == assessment_id)
        .with_for_update()
        .first()
    )
    if override is None:
        raise HTTPException(status_code=404, detail="Override not found")

    # P3 (R-GOV-03): an independent Senior Analyst, QA Reviewer or FCRM
    # Manager -- never the proposer or the owner (the beneficiary).
    refusal = override_rules.review_problem(assessment, override, current_user)
    if refusal:
        status_code = 409 if "nothing left to review" in refusal or "before independent review" in refusal else 403
        if status_code == 403:
            log_denied_attempt(db, current_user, request, f"override review refused: {refusal}", assessment_id=assessment_id)
        raise HTTPException(status_code=status_code, detail=refusal)
    ensure_assessment_editable(assessment)

    confirmed = payload.decision == "CONFIRM"
    override.review_status = REVIEW_CONFIRMED if confirmed else REVIEW_REJECTED
    override.reviewed_by = actor_name(current_user)
    override.reviewed_by_id = current_user.id
    override.reviewed_at = datetime.now(timezone.utc)
    override.review_note = payload.note
    if override_rules.is_material(override) and override.approval_status is None:
        # A confirmed material change now needs its approval; a rejected
        # one needs none (it must be corrected instead).
        override.approval_status = override_rules.APPROVAL_PENDING if confirmed else override_rules.APPROVAL_NOT_REQUIRED

    log_audit_event(
        db,
        assessment_id=assessment_id,
        action=AuditAction.OVERRIDE_REVIEWED,
        actor=actor_name(current_user),
        actor_id=current_user.id,
        details=f"Override #{override.id} ({override.materiality}) {override.review_status.lower()}: {payload.note}",
    )
    db.commit()
    db.refresh(override)
    return _override_response(db, assessment, override, current_user)


@router.patch(
    "/{assessment_id}/overrides/{override_id}/approval",
    response_model=OverrideResponse,
)
def approve_assessment_override(
    assessment_id: int,
    override_id: int,
    payload: OverrideApproval,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """P3 (R-GOV-03): approval of a material (FCRM Manager / Head of FCRM)
    or critical (Head of FCRM) override, after a confirming independent
    review, by a person who neither proposed nor reviewed it."""

    from app.governance import overrides as override_rules

    assessment = db.query(Assessment).filter(Assessment.id == assessment_id).first()
    if not assessment:
        raise HTTPException(status_code=404, detail="Assessment not found")
    override = (
        db.query(AssessmentOverride)
        .filter(AssessmentOverride.id == override_id, AssessmentOverride.assessment_id == assessment_id)
        .with_for_update()
        .first()
    )
    if override is None:
        raise HTTPException(status_code=404, detail="Override not found")

    refusal = override_rules.approval_problem(assessment, override, current_user)
    if refusal:
        out_of_order = "must confirm" in refusal or "Nothing to approve" in refusal or "Only material" in refusal
        if not out_of_order:
            log_denied_attempt(db, current_user, request, f"override approval refused: {refusal}", assessment_id=assessment_id)
        raise HTTPException(status_code=409 if out_of_order else 403, detail=refusal)
    ensure_assessment_editable(assessment)

    override.approval_status = (
        override_rules.APPROVAL_APPROVED if payload.decision == "APPROVE" else override_rules.APPROVAL_REJECTED
    )
    override.approved_by = actor_name(current_user)
    override.approved_by_id = current_user.id
    override.approved_at = datetime.now(timezone.utc)
    override.approval_rationale = payload.rationale

    log_audit_event(
        db,
        assessment_id=assessment_id,
        action=AuditAction.OVERRIDE_APPROVAL_DECIDED,
        actor=actor_name(current_user),
        actor_id=current_user.id,
        details=f"Override #{override.id} ({override.materiality}) approval {override.approval_status.lower()}: {payload.rationale}",
    )
    db.commit()
    db.refresh(override)
    return _override_response(db, assessment, override, current_user)


# R10.5: return the assessment to the business owner (or another team)
# for clarification instead of continuing the review.


class RequestInformationRequest(BaseModel):
    target: str
    note: str

    @field_validator("target")
    @classmethod
    def target_must_not_be_blank(cls, value: str) -> str:
        if not value or not value.strip():
            raise ValueError("A target (business owner or team) is required.")
        return value

    @field_validator("note")
    @classmethod
    def note_must_not_be_blank(cls, value: str) -> str:
        if not value or not value.strip():
            raise ValueError("A note explaining what information is needed is required.")
        return value


@router.post(
    "/{assessment_id}/request-information",
    response_model=AssessmentResponse,
)
def request_information(
    assessment_id: int,
    payload: RequestInformationRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_pipeline_role),
):
    assessment = db.query(Assessment).filter(Assessment.id == assessment_id).first()
    if not assessment:
        raise HTTPException(status_code=404, detail="Assessment not found")

    if assessment.status == "INFORMATION_REQUESTED":
        raise HTTPException(
            status_code=400,
            detail="Information has already been requested and not yet provided.",
        )

    previous_status = assessment.status
    # AC4: status changes to "Information Requested".
    workflow.check_transition(db, assessment, "INFORMATION_REQUESTED", user=current_user)
    assessment.pre_information_request_status = previous_status
    workflow.transition(
        db,
        assessment,
        "INFORMATION_REQUESTED",
        user=current_user,
        reason=f"Information requested from {payload.target}: {payload.note}",
        action="REQUEST_INFORMATION",
    )
    assessment.information_request_target = payload.target
    assessment.information_request_note = payload.note
    assessment.information_requested_by = current_user.full_name or current_user.email
    assessment.information_requested_at = datetime.now(timezone.utc)
    assessment.information_response = None
    assessment.information_responded_at = None

    log_audit_event(
        db,
        assessment_id=assessment_id,
        action=AuditAction.INFORMATION_REQUESTED,
        previous_status=previous_status,
        new_status=assessment.status,
        actor=current_user.full_name or current_user.email,
        actor_id=current_user.id,
        details=f"Requested from {payload.target}: {payload.note}",
    )

    db.commit()
    db.refresh(assessment)
    return assessment


class ProvideInformationRequest(BaseModel):
    response: str

    @field_validator("response")
    @classmethod
    def response_must_not_be_blank(cls, value: str) -> str:
        if not value or not value.strip():
            raise ValueError("A response is required.")
        return value


@router.post(
    "/{assessment_id}/provide-information",
    response_model=AssessmentResponse,
)
def provide_information(
    assessment_id: int,
    payload: ProvideInformationRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    assessment = db.query(Assessment).filter(Assessment.id == assessment_id).first()
    if not assessment:
        raise HTTPException(status_code=404, detail="Assessment not found")

    if assessment.status != "INFORMATION_REQUESTED":
        raise HTTPException(
            status_code=400,
            detail="No information request is outstanding on this assessment.",
        )

    # Only the owner being asked, or an authorized reviewer, can respond.
    if current_user.id != assessment.owner_id and current_user.role not in {
        UserRole.FCRM_ANALYST.value,
        UserRole.MANAGER.value,
        UserRole.ADMIN.value,
    }:
        raise HTTPException(
            status_code=403,
            detail="Only the assessment owner or an authorized reviewer can provide the requested information.",
        )

    previous_status = assessment.status
    assessment.information_response = payload.response
    assessment.information_responded_at = datetime.now(timezone.utc)
    workflow.transition(
        db,
        assessment,
        assessment.pre_information_request_status or "HUMAN_REVIEW",
        user=current_user,
        reason=f"Requested information provided: {payload.response}",
        action="PROVIDE_INFORMATION",
    )

    log_audit_event(
        db,
        assessment_id=assessment_id,
        action=AuditAction.INFORMATION_PROVIDED,
        previous_status=previous_status,
        new_status=assessment.status,
        actor=current_user.full_name or current_user.email,
        actor_id=current_user.id,
        details=payload.response,
    )

    db.commit()
    db.refresh(assessment)
    return assessment


# =============================================================================
# STAGE PIPELINE — explicit, server-validated stage advancement.
#
# Business Request -> INTAKE -> EVIDENCE_COLLECTION -> RISK_IDENTIFICATION ->
# INHERENT_RISK_ASSESSMENT -> CONTROL_ASSESSMENT -> RESIDUAL_RISK ->
# HUMAN_REVIEW -> COMMITTEE_DECISION -> AUDIT
#
# "Business Request" is the CreateAssessment form itself (before a record
# exists) and is not a status value. Every other arrow above is only ever
# crossed by an explicit call to this endpoint — there is no automatic or
# implicit advancement, even though some transitions trigger a computation
# (e.g. running the risk engine) as a side effect of arriving at the next
# stage. Skipping stages or jumping arbitrarily is rejected; the only
# branching point is COMMITTEE_DECISION.
# =============================================================================


@router.post(
    "/{assessment_id}/acknowledge-degraded",
    response_model=AssessmentResponse,
)
def acknowledge_degraded_result(
    assessment_id: int,
    payload: DegradedResultAcknowledgement = DegradedResultAcknowledgement(),
    db: Session = Depends(get_db),
    current_user: User = Depends(require_pipeline_role),
):
    """
    A reviewer states, on the record, that they have read a provisional
    rules-only result and accept it as the basis for advancing.

    This does not clear requires_human_review -- that flag describes how
    the analysis was produced and stays true for the life of the run.
    What it records is that a named human looked at it anyway. A later
    degraded re-run clears the acknowledgement, because what was
    acknowledged was a different result.
    """

    assessment = (
        db.query(Assessment)
        .filter(Assessment.id == assessment_id)
        .first()
    )

    if not assessment:
        raise HTTPException(status_code=404, detail="Assessment not found")

    ensure_assessment_editable(assessment)

    if not assessment.requires_human_review:
        raise HTTPException(
            status_code=400,
            detail=(
                "This assessment is not awaiting acknowledgement of a "
                "degraded result."
            ),
        )

    if assessment.assessment_mode == AssessmentMode.UNAVAILABLE:
        raise HTTPException(
            status_code=400,
            detail=(
                "This assessment has no risk rating to acknowledge. Re-run "
                "risk identification instead."
            ),
        )

    actor = current_user.full_name or current_user.email

    assessment.degraded_acknowledged_by = actor
    assessment.degraded_acknowledged_at = datetime.now(timezone.utc)

    log_audit_event(
        db=db,
        assessment_id=assessment.id,
        action=AuditAction.DEGRADED_RESULT_ACKNOWLEDGED,
        previous_status=assessment.status,
        new_status=assessment.status,
        actor=actor,
        actor_id=current_user.id,
        details=(
            "Reviewer acknowledged a provisional rules-only risk result "
            f"(error code: {assessment.technical_error_code or 'unknown'})."
            + (f" Note: {payload.note}" if payload.note else "")
        ),
    )

    db.commit()
    db.refresh(assessment)

    return assessment


@router.post(
    "/{assessment_id}/advance-stage-async",
    response_model=ProcessingJobResponse,
    status_code=202,
)
def start_stage_advance(
    assessment_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    PATCH /advance-stage run as a background STAGE_ADVANCE job whose
    per-step progress the UI polls (GET /api/processing-jobs/{id}).

    Permission -- and, leaving Evidence Collection, the confirmed-profile
    gate -- are checked here so those refusals come back immediately. The
    job itself runs advance_assessment_stage unchanged; a gate it refuses
    on finishes the job FAILED with `refused` set.
    """

    assessment = db.query(Assessment).filter(Assessment.id == assessment_id).first()
    if not assessment:
        raise HTTPException(status_code=404, detail="Assessment not found")

    active = (
        db.query(ProcessingJob)
        .filter(
            ProcessingJob.assessment_id == assessment_id,
            ProcessingJob.job_type.in_(processing_jobs.STAGE_ADVANCE_TYPES),
            ProcessingJob.status.in_(
                [ProcessingJobStatus.QUEUED.value, ProcessingJobStatus.RUNNING.value]
            ),
        )
        .first()
    )
    if active:
        return build_processing_job_response(active)

    next_status = next_stage_status(assessment.status)
    if next_status is None:
        raise HTTPException(
            status_code=400,
            detail=f"An assessment in {assessment.status} can't be advanced from here.",
        )

    workflow.check_transition(db, assessment, next_status, user=current_user)

    if assessment.status == "EVIDENCE_COLLECTION" and not _ensure_structured_profile(db, assessment).confirmed:
        db.commit()
        raise HTTPException(status_code=400, detail=PROFILE_NOT_CONFIRMED_DETAIL)
    if assessment.status == "EVIDENCE_COLLECTION":
        db.commit()
        evidence_currency.ensure_no_unacknowledged(db, assessment.id)

    job = processing_jobs.create_job(
        db,
        ProcessingJobType.STAGE_ADVANCE,
        assessment_id=assessment_id,
        user=current_user,
    )
    job.stage_log = stage_progress.StageLog(assessment.status).to_json()
    db.commit()
    processing_jobs.enqueue(job.id)
    db.refresh(job)
    return build_processing_job_response(job)


def next_stage_status(status: str) -> str | None:
    """Where PATCH /advance-stage moves an assessment in `status`; None
    when it can't advance it (rejected, Human Review, not in the
    pipeline)."""

    if status == "REMEDIATION":
        return "INTAKE"
    if status in STAGE_ORDER[:-1]:
        return STAGE_ORDER[STAGE_ORDER.index(status) + 1]
    return None


PROFILE_NOT_CONFIRMED_DETAIL = (
    "The business profile has not been confirmed yet. The "
    "business/product owner must review it on the Intake "
    "step (Business Intelligence -> Confirm extracted "
    "information) before risk identification can run."
)


@router.patch(
    "/{assessment_id}/advance-stage",
    response_model=AssessmentResponse,
)
def advance_assessment_stage(
    assessment_id: int,
    payload: AdvanceStageRequest = AdvanceStageRequest(),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    assessment = (
        db.query(Assessment)
        .filter(Assessment.id == assessment_id)
        .first()
    )

    if not assessment:
        raise HTTPException(status_code=404, detail="Assessment not found")

    current_status = assessment.status

    # AW / Stage 14 (R14.2): who may make each move is defined once, in
    # the TRANSITIONS table in app/services/workflow.py -- the Business
    # User owns their own intake and evidence collection (INTAKE ->
    # EVIDENCE_COLLECTION -> RISK_IDENTIFICATION, REMEDIATION -> INTAKE);
    # every later pipeline stage belongs to the FCRM Analyst (or a
    # Manager/Admin acting as one). Checked up front, before any stage
    # side effect (e.g. the risk engine) runs.
    if current_status == "REMEDIATION":
        workflow.check_transition(db, assessment, "INTAKE", user=current_user)
    elif current_status in STAGE_ORDER[:-1]:
        workflow.check_transition(
            db,
            assessment,
            STAGE_ORDER[STAGE_ORDER.index(current_status) + 1],
            user=current_user,
        )

    if current_status == "REJECTED":
        raise HTTPException(
            status_code=400,
            detail="This assessment has been rejected and cannot advance further.",
        )

    if current_status == "REMEDIATION":
        # Transient loop-back marker: the one allowed move out of
        # REMEDIATION is back into the start of the tracked pipeline.
        next_status = "INTAKE"
        stage_progress.begin("record_transition")
        workflow.transition(
            db,
            assessment,
            next_status,
            user=current_user,
            reason="Remediation acknowledged; assessment returned to Intake.",
            action="ADVANCE_STAGE",
        )

        log_audit_event(
            db=db,
            assessment_id=assessment.id,
            action=AuditAction.STAGE_ADVANCED,
            previous_status=current_status,
            new_status=next_status,
            details="Remediation acknowledged; assessment returned to Intake.",
        )

        db.commit()
        db.refresh(assessment)
        return assessment

    if current_status not in STAGE_ORDER:
        raise HTTPException(
            status_code=400,
            detail=(
                f"Assessment status '{current_status}' is not part of the "
                "current stage pipeline. Run the stage-pipeline migration "
                "(backend/migrate_stage_pipeline.py) before using "
                "advance-stage."
            ),
        )

    current_index = STAGE_ORDER.index(current_status)

    if current_status == "HUMAN_REVIEW":
        raise HTTPException(
            status_code=400,
            detail=(
                "Human review is complete. Use POST "
                f"/{assessment_id}/submit-to-manager to send this "
                "assessment into the approval workflow."
            ),
        )

    # -------------------------------------------------------------------
    # Every other stage: move exactly one step forward in STAGE_ORDER,
    # after any stage-specific validation/side-effect.
    # -------------------------------------------------------------------
    next_status = STAGE_ORDER[current_index + 1]

    if current_status == "INTAKE":
        stage_progress.begin("validate_intake")
        if (
            not (assessment.title or "").strip()
            or not (assessment.description or "").strip()
            or not (assessment.evidence or "").strip()
        ):
            raise HTTPException(
                status_code=400,
                detail=(
                    "This is still a draft. Add a title, description and "
                    "evidence before completing Intake."
                ),
            )

    if current_status == "EVIDENCE_COLLECTION":
        stage_progress.begin("confirm_profile")
        # R3.1/R3.3 (Intake Validation and Structuring): the structured
        # business/product profile must exist and be confirmed by the
        # business/product owner before the pipeline is allowed to reach
        # final risk calculation (RISK_IDENTIFICATION). Auto-creates the
        # profile from intake fields the first time, for assessments that
        # were created manually with no source document.
        structured_profile = _ensure_structured_profile(db, assessment)

        # Index this assessment's own description+evidence text (not
        # just uploaded documents) so future assessments' semantic
        # search can find it as similar past context, even when this
        # one had no attached document.
        _index_for_semantic_search(
            db,
            assessment.id,
            " ".join(
                filter(None, [assessment.description, assessment.evidence])
            ),
        )

        if not structured_profile.confirmed:
            db.commit()
            raise HTTPException(status_code=400, detail=PROFILE_NOT_CONFIRMED_DETAIL)

        # P4 (R2.6): an expired document is never used as current
        # evidence until a person has decided on it. (Committed first, as
        # above, so a refusal doesn't lose the profile just created.)
        db.commit()
        evidence_currency.ensure_no_unacknowledged(db, assessment.id)

        # Entering RISK_IDENTIFICATION runs the deterministic/AI risk
        # engine. This is the one stage whose "arrival" is itself a
        # computation, but it still only happens because of this explicit
        # advance-stage call — never automatically.
        try:
            run_risk_assessment_workflow(
                assessment_id=assessment_id,
                db=db,
            )
        except ValueError as exc:
            db.rollback()
            raise HTTPException(status_code=404, detail=str(exc))
        except Exception as exc:
            db.rollback()
            raise HTTPException(
                status_code=500,
                detail=f"Risk identification failed: {str(exc)}",
            )

        # run_risk_assessment_workflow commits internally; re-fetch.
        assessment = (
            db.query(Assessment)
            .filter(Assessment.id == assessment_id)
            .first()
        )

    if current_status == "RISK_IDENTIFICATION":
        stage_progress.begin("check_analysis")
        # Degraded-analysis gate (see app/risk_engine/degraded.py).
        #
        # An `unavailable` run produced no rating at all, and a
        # `rules_only` run produced one without the AI step. Neither may
        # be frozen as the inherent assessment of record on its own: the
        # first has nothing to freeze, and the second is explicitly
        # provisional until a reviewer says they have looked at it. This
        # is checked before anything else in this block, so a degraded
        # result cannot slip through on the strength of a previous run's
        # risk results.
        if assessment.assessment_mode == AssessmentMode.UNAVAILABLE:
            raise HTTPException(
                status_code=400,
                detail=(
                    assessment.degraded_reason
                    or "This assessment could not be analysed."
                )
                + " Re-run risk identification before advancing.",
            )

        if (
            assessment.requires_human_review
            and assessment.degraded_acknowledged_at is None
        ):
            raise HTTPException(
                status_code=400,
                detail=(
                    "This is a provisional, rules-only assessment produced "
                    "while AI analysis was unavailable. A reviewer must "
                    "acknowledge it (Acknowledge provisional result, on the "
                    "Risk Identification step) or re-run risk identification "
                    "before it can advance to Inherent Risk Assessment."
                ),
            )

        # Arrival at INHERENT_RISK_ASSESSMENT freezes a snapshot of the
        # just-computed AI risk results as the inherent (pre-control)
        # assessment of record. Distinct from EVIDENCE_COLLECTION's gate:
        # this specifically prevents an assessment with no supporting risk
        # results (e.g. the AI analysis produced nothing usable) from
        # progressing into Inherent Risk Assessment at all.
        current_risk_results = (
            db.query(RiskResult)
            .filter(
                RiskResult.assessment_id == assessment_id,
                RiskResult.is_current.is_(True),
            )
            .count()
        )

        # R6.2/R6.7: freeze the deterministic Stage 6 calculation (not the
        # legacy AI-averaged overall_score) as the inherent-risk snapshot.
        # An incomplete factor set (an applicable, non-excluded factor
        # still missing a manual likelihood/impact rating) blocks the
        # transition outright rather than freezing a half-known result.
        # Checked first: unrated factors are also why overall_score is
        # still empty, and "rate the remaining factors" is the actionable
        # message, not "re-run risk identification".
        stage_progress.begin("calculate_inherent")
        inherent_calculation = (
            recalculate_inherent_risk(db, assessment) if current_risk_results else None
        )

        if inherent_calculation is not None and inherent_calculation.is_provisional:
            db.rollback()
            raise HTTPException(
                status_code=400,
                detail=(
                    "Every applicable, non-excluded risk factor must be "
                    "rated (likelihood x impact) before the inherent risk "
                    "calculation can be finalized. Rate the remaining "
                    "factors on the Risk Identification step before "
                    "advancing to Inherent Risk Assessment."
                ),
            )

        if not current_risk_results or assessment.overall_score is None:
            raise HTTPException(
                status_code=400,
                detail=(
                    "Risk identification produced no usable risk results. "
                    "Re-run risk identification before advancing to "
                    "Inherent Risk Assessment."
                ),
            )

        # R6.7: a carried-forward analyst override is the inherent result
        # of record (the calculated value stays on the row alongside it).
        if inherent_calculation.overridden and inherent_calculation.override_band:
            assessment.inherent_score = inherent_calculation.override_value
            assessment.inherent_risk_level = inherent_calculation.override_band
        else:
            assessment.inherent_score = inherent_calculation.final_score
            assessment.inherent_risk_level = inherent_calculation.risk_band

    if current_status == "INHERENT_RISK_ASSESSMENT":
        # Entering CONTROL_ASSESSMENT: automatically identify and create
        # applicable controls for each risk factor using AI
        stage_progress.begin("identify_controls")
        try:
            from app.ai.control_identifier import identify_applicable_controls
            from app.models.control import Control

            risk_factors = (
                db.query(RiskFactor)
                .filter(
                    RiskFactor.assessment_id == assessment_id,
                    RiskFactor.is_current.is_(True),
                    RiskFactor.applicable.is_(True),
                    RiskFactor.excluded.is_(False),
                )
                .all()
            )

            assessment_context = {
                "assessment_title": assessment.title,
                "change_type": assessment.change_type,
                "description": assessment.description,
            }

            for risk_factor in risk_factors:
                # Skip if controls already exist for this risk
                existing_controls = (
                    db.query(Control)
                    .filter(
                        Control.assessment_id == assessment_id,
                        Control.risk_factor_id == risk_factor.id,
                        Control.is_current.is_(True),
                    )
                    .count()
                )

                if existing_controls > 0:
                    continue

                # Identify controls using AI
                suggested_controls = identify_applicable_controls(
                    risk_category=risk_factor.category,
                    risk_rationale=risk_factor.rationale,
                    misuse_scenario=risk_factor.misuse_scenario or "",
                    assessment_context=assessment_context,
                )

                # Auto-create the suggested controls
                for control_type in suggested_controls:
                    control = Control(
                        assessment_id=assessment_id,
                        risk_factor_id=risk_factor.id,
                        control_type=control_type,
                        added_by="System",
                    )
                    db.add(control)

            db.commit()

            stage_progress.begin("evaluate_controls")
            # Recompute control state
            from app.control_engine.engine import recompute_control_state
            recompute_control_state(db, assessment_id)

            # AI evidence check against the uploaded documents. Suggestions
            # only -- an analyst accepts them -- and a failure never blocks
            # the stage.
            try:
                from app.services.control_evidence_service import run_evidence_check

                run_evidence_check(db, assessment_id)
            except Exception as evidence_exc:  # noqa: BLE001
                db.rollback()
                logging.getLogger(__name__).warning(
                    "Automatic evidence check failed for assessment %s: %s",
                    assessment_id,
                    evidence_exc,
                )

            # AI-suggested design adequacy for controls with no assessment
            # yet. A starting point the analyst can change: it earns no
            # credit by itself and a failure never blocks the stage.
            try:
                from app.services.control_ai_assessment import suggest_designs

                suggest_designs(db, assessment_id)
                recompute_control_state(db, assessment_id)
                db.commit()
            except Exception as design_exc:  # noqa: BLE001
                db.rollback()
                logging.getLogger(__name__).warning(
                    "Automatic design suggestion failed for assessment %s: %s",
                    assessment_id,
                    design_exc,
                )

        except Exception as exc:
            stage_progress.warn_running()
            logging.getLogger(__name__).warning(
                "Automatic control identification failed for assessment %s: %s",
                assessment_id,
                exc,
            )

    if current_status == "CONTROL_ASSESSMENT":
        stage_progress.begin("check_challenge")
        challenge = (
            db.query(AssessmentChallenge)
            .filter(AssessmentChallenge.assessment_id == assessment_id)
            .first()
        )

        if not challenge or not challenge.outcome:
            raise HTTPException(
                status_code=400,
                detail=(
                    "Record a control assessment outcome via "
                    "PATCH /{assessment_id}/challenge before advancing to "
                    "Residual Risk."
                ),
            )

        # Arrival at RESIDUAL_RISK freezes the residual result as the
        # source of truth for Human Review / Committee Decision: inherent
        # band x control rating through the methodology's residual grid,
        # with non-mitigable floors (app/services/residual_risk_service.py).
        stage_progress.begin("calculate_residual")
        residual_calc = recalculate_residual_risk(
            db,
            assessment,
            frozen=True,
            calculated_by=current_user.full_name or current_user.email,
        )

        if residual_calc.residual_band is None:
            db.rollback()
            raise HTTPException(
                status_code=400,
                detail=(
                    "Residual risk cannot be determined: "
                    f"{residual_calc.reason or 'missing inputs.'}"
                ),
            )

        log_audit_event(
            db=db,
            assessment_id=assessment_id,
            action=AuditAction.STATUS_CHANGE,
            actor=current_user.full_name or current_user.email,
            details=(
                f"Residual risk frozen: inherent {residual_calc.inherent_band} x "
                f"controls {residual_calc.control_rating} -> "
                f"{residual_calc.grid_band} (grid v{residual_calc.grid_version}, "
                f"methodology {residual_calc.methodology_version})"
                + (
                    "; held at "
                    + ", ".join(
                        f"{f['band_after']} by {f['rule_code']}"
                        for f in residual_calc.get_floors_applied()
                    )
                    if residual_calc.get_floors_applied()
                    else ""
                )
                + f". Residual band of record: {residual_calc.residual_band}."
            ),
        )

    if current_status == "RESIDUAL_RISK":
        # Stage 9 (R9.1): arrival at HUMAN_REVIEW auto-generates the
        # structured, decision-ready assessment draft -- every input it
        # needs (inherent/residual scores, controls, gaps) is finalized
        # by this point. Best-effort: a draft-generation failure (e.g.
        # the LLM is unreachable) never blocks the stage transition
        # itself, since the deterministic sections still assemble fine
        # and an analyst can always regenerate via POST .../draft/generate.
        stage_progress.begin("generate_draft")
        try:
            generate_assessment_draft(db, assessment, requested_by="System")
        except Exception as exc:
            stage_progress.warn_running()
            logging.getLogger(__name__).warning(
                "Assessment draft auto-generation failed for assessment %s: %s",
                assessment_id,
                exc,
            )

    stage_progress.begin("record_transition")
    previous_status = assessment.status
    workflow.transition(
        db,
        assessment,
        next_status,
        user=current_user,
        reason=f"Stage advanced from {previous_status} to {next_status}.",
        action="ADVANCE_STAGE",
    )

    log_audit_event(
        db=db,
        assessment_id=assessment.id,
        action=AuditAction.STAGE_ADVANCED,
        previous_status=previous_status,
        new_status=next_status,
        details=f"Stage advanced from {previous_status} to {next_status}.",
    )

    db.commit()
    db.refresh(assessment)

    return assessment

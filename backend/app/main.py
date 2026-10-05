import asyncio
import logging
import os
import secrets

from fastapi import Depends, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.httpsredirect import HTTPSRedirectMiddleware
from fastapi.responses import FileResponse


from app.database import Base, engine, SessionLocal
from app.models.assessment import Assessment
from app.models.risk_result import RiskResult
from app.models.assessment_document import AssessmentDocument
from app.api.assessments import router as assessment_router
from app.api.risk_methodology import router as risk_methodology_router
from app.api.auth import router as auth_router
from app.api.users import router as users_router
from app.api.approvals import router as approvals_router
from app.api.controls import router as controls_router
from app.api.challenge_review import router as challenge_review_router
from app.api.action_items import router as action_items_router
from app.api.workflow import router as workflow_router
from app.api.reports import router as reports_router
from app.api.audit_trail import router as audit_trail_router
from app.api.reassessment import router as reassessment_router
from app.api.processing_jobs import router as processing_jobs_router
from app.api.system import router as system_router
from app.api.risk_calculator import router as risk_calculator_router
from app.api.delegations import router as delegations_router
from app.api.reference_data import router as reference_data_router
from app.api.residual_review import router as residual_review_router
from app.api.source_library import router as source_library_router
from app.api.sod_exceptions import router as sod_exceptions_router
from app.api.assistant import router as assistant_router
from app.models.calculator_draft import CalculatorDraft
from app.auth.access import enforce_api_access
from app.middleware import AdminAuditMiddleware, RequestTimingMiddleware, SecurityHeadersMiddleware
from app.models.processing_job import ProcessingJob
from app.services.data_protection import install_data_protection
from app.models.assessment_intelligence import AssessmentIntelligence
from app.models.audit_event import AuditEvent
from app.models.assessment_challenge import AssessmentChallenge
from app.models.assessment_fcrm_review import AssessmentFcrmReview
from app.models.assessment_override import AssessmentOverride
from app.models.risk_methodology import RiskMethodology
from app.models.risk_factor import RiskFactor
from app.models.inherent_risk_calculation import InherentRiskCalculation
from app.models.document_embedding import DocumentEmbedding
from app.models.user import User, UserRole
from app.models.assessment_comment import AssessmentComment
from app.models.control import Control, ControlAssessment, ControlCondition, ControlGap
from app.models.challenge_review import ChallengeFinding, ChallengeTriggerConfig
from app.models.control_evidence import ControlEvidenceLink
from app.models.assessment_draft import AssessmentDraft
from app.models.committee_condition import CommitteeCondition
from app.models.country_risk import CountryRisk
from app.models.reference_data_snapshot import ReferenceDataSnapshot
from app.models.residual_risk_calculation import ResidualRiskCalculation
from app.models.decision_record import DecisionRecord
from app.models.committee_vote import CommitteeVote
from app.models.approval_delegation import ApprovalDelegation
from app.models.action_item import ActionItem
from app.models.workflow_transition import WorkflowTransition
from app.models.ai_metrics import AIEvaluationRecord, AIUsageLog
from app.models.audit_trail import AssessmentRetention, RetentionPolicy
from app.models.reassessment_trigger import ReassessmentTrigger
from app.models.evidence_traceability import EvidenceAcknowledgement, IntakeSnapshot
from app.auth.security import hash_password

from app.migrations import prepare_schema

# Stage 19: block hard deletes of records of record / edits to audit rows.
# (ORM event listeners only -- registering them touches no database.)
install_data_protection()

# Importing this module never touches the database. The schema and the
# bootstrap admin are prepared by prepare_database(): on server start-up
# (below), or explicitly by tests and scripts after they have pointed
# DATABASE_URL at their own database. See app/migrations.py for the guard
# that keeps remote databases from being migrated without authorization.


def _seed_default_admin() -> None:
    """
    AW: without this, there would be no way to log in and create real
    users at all on a fresh database.

    Stage 19 (Security): no credential is stored in code. The bootstrap
    admin's e-mail/password come from ADMIN_BOOTSTRAP_EMAIL /
    ADMIN_BOOTSTRAP_PASSWORD; if no password is configured a random one
    is generated and printed once to the server console. Rotate it on
    first login.
    """

    db = SessionLocal()
    try:
        if db.query(User).count() > 0:
            return

        email = os.getenv("ADMIN_BOOTSTRAP_EMAIL", "admin@example.com")
        password = os.getenv("ADMIN_BOOTSTRAP_PASSWORD")
        generated = not password
        if generated:
            password = secrets.token_urlsafe(12)

        admin = User(
            email=email,
            hashed_password=hash_password(password),
            full_name="Default Admin",
            role=UserRole.ADMIN.value,
            is_active=True,
        )
        db.add(admin)
        db.commit()

        if generated:
            print(
                f"Seeded bootstrap admin user -- email: {email}, one-time password: "
                f"{password} (set ADMIN_BOOTSTRAP_PASSWORD to choose it; change it after first login)."
            )
        else:
            print(f"Seeded bootstrap admin user {email} from ADMIN_BOOTSTRAP_PASSWORD.")
    finally:
        db.close()


def prepare_database() -> None:
    """Migrate (local databases, or an authorized remote one) and seed the
    bootstrap admin. Raises MigrationNotAuthorized rather than migrating a
    remote database that has not been authorized."""

    # P7 (R19): a production deployment refuses to start with any
    # encryption requirement unmet (keys, HTTPS, database TLS).
    from app.security_settings import enforce_production_security

    enforce_production_security()
    prepare_schema()
    _seed_default_admin()


app = FastAPI(
    title="Risk Assessment Workbench",
    description="Enterprise risk assessment platform",
    version="0.1.0",
)

# Stage 19: allowed browser origins come from configuration, not code.
_DEFAULT_CORS_ORIGINS = (
    "http://localhost:5176,http://127.0.0.1:5176,http://localhost:5173,"
    "http://localhost:1574,http://127.0.0.1:1574"
)
CORS_ALLOWED_ORIGINS = [
    origin.strip()
    for origin in os.getenv("CORS_ALLOWED_ORIGINS", _DEFAULT_CORS_ORIGINS).split(",")
    if origin.strip()
]

# Stage 19 middleware (outermost last): timing and admin-action logging
# see the final status; security headers are added to every response.
app.add_middleware(AdminAuditMiddleware)
app.add_middleware(RequestTimingMiddleware)
app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["X-Total-Count", "Server-Timing", "X-Response-Time-Ms"],
)
app.add_middleware(SecurityHeadersMiddleware)

# Stage 19 (Security -- protection in transit): behind a TLS-terminating
# proxy or with uvicorn --ssl-*, FORCE_HTTPS=true redirects plain HTTP.
if os.getenv("FORCE_HTTPS", "false").strip().lower() in {"1", "true", "yes"}:
    app.add_middleware(HTTPSRedirectMiddleware)


# Stage 19 (Security -- least privilege): every API router except login
# requires an authenticated user, and any {assessment_id}/{document_id}
# in the path is checked against the caller's assessment visibility
# (app/auth/access.py). Individual endpoints still add role checks.
_protected = [Depends(enforce_api_access)]

app.include_router(assessment_router, dependencies=_protected)
app.include_router(risk_methodology_router, dependencies=_protected)
app.include_router(auth_router)
app.include_router(users_router, dependencies=_protected)
app.include_router(approvals_router, dependencies=_protected)
app.include_router(controls_router, dependencies=_protected)
app.include_router(challenge_review_router, dependencies=_protected)
app.include_router(action_items_router, dependencies=_protected)
app.include_router(workflow_router, dependencies=_protected)
app.include_router(reports_router, dependencies=_protected)
app.include_router(audit_trail_router, dependencies=_protected)
app.include_router(reassessment_router, dependencies=_protected)
app.include_router(processing_jobs_router, dependencies=_protected)
app.include_router(system_router, dependencies=_protected)
app.include_router(risk_calculator_router, dependencies=_protected)
app.include_router(delegations_router, dependencies=_protected)
app.include_router(reference_data_router, dependencies=_protected)
app.include_router(residual_review_router, dependencies=_protected)
app.include_router(source_library_router, dependencies=_protected)
app.include_router(sod_exceptions_router, dependencies=_protected)
app.include_router(assistant_router, dependencies=_protected)


# Stage 14 (R14.4): periodic escalation sweep, so an overdue assessment is
# escalated even if nobody opens a list/work queue (those also sweep
# opportunistically). Interval configurable; 0 disables it.
ESCALATION_INTERVAL_SECONDS = int(os.getenv("WORKFLOW_ESCALATION_INTERVAL_SECONDS", "900"))


async def _escalation_loop() -> None:
    from app.services.escalation import escalate_overdue_action_items
    from app.services.reassessment_service import sweep_review_dates
    from app.services.workflow import escalate_overdue_assessments

    while True:
        await asyncio.sleep(ESCALATION_INTERVAL_SECONDS)
        db = SessionLocal()
        try:
            escalate_overdue_assessments(db)
            # R13.3: overdue action items too, not only when someone
            # happens to open that assessment's action list.
            escalate_overdue_action_items(db)
            # Stage 18 AC2: expired / due-for-review approvals.
            sweep_review_dates(db)
            # P3: SoD exceptions past their end date (also checked at use).
            from app.governance.sod import expire_due

            if expire_due(db):
                db.commit()
        except Exception as exc:  # never let the sweep kill the loop
            logging.getLogger(__name__).warning("Workflow escalation sweep failed: %s", exc)
            db.rollback()
        finally:
            db.close()


@app.on_event("startup")
async def _prepare_database_on_startup() -> None:
    # Registered before the other start-up hooks, so a server never runs
    # against a schema it doesn't match.
    await asyncio.to_thread(prepare_database)


@app.on_event("startup")
async def _start_escalation_loop() -> None:
    if ESCALATION_INTERVAL_SECONDS > 0:
        asyncio.create_task(_escalation_loop())


# Stage 19 (Availability and Recovery): scheduled backups. The interval
# should be no longer than the recovery point objective
# (RECOVERY_POINT_OBJECTIVE_HOURS, default 24). 0 disables it.
BACKUP_INTERVAL_HOURS = float(os.getenv("BACKUP_INTERVAL_HOURS", "24"))


async def _backup_loop() -> None:
    from app.services import backup

    while True:
        await asyncio.sleep(BACKUP_INTERVAL_HOURS * 3600)
        try:
            result = await asyncio.to_thread(backup.create_backup, "scheduled", "System")
            logging.getLogger(__name__).info("Scheduled backup %s created.", result["name"])
        except Exception as exc:  # never let a failed backup kill the loop
            logging.getLogger(__name__).error("Scheduled backup failed: %s", exc)


@app.on_event("startup")
async def _start_background_services() -> None:
    from app.services.processing_jobs import recover_interrupted_jobs

    # Stage 19 (Reliability): jobs cut off by a restart become retryable.
    await asyncio.to_thread(recover_interrupted_jobs)

    # Optional Langfuse tracing: build the client off the request path.
    from app.observability.tracing import warm_up_in_background

    warm_up_in_background()

    if BACKUP_INTERVAL_HOURS > 0:
        asyncio.create_task(_backup_loop())


# Single-server deployment (see Dockerfile): when FRONTEND_DIST_DIR points
# at the built frontend (frontend/dist), this app serves it too, so the
# whole workbench is one URL and the browser calls /api on the same
# origin -- no CORS setup needed. Unset in local development, where Vite
# serves the frontend.
_frontend_dist = os.getenv("FRONTEND_DIST_DIR", "").strip()
FRONTEND_DIST_DIR = os.path.realpath(_frontend_dist) if _frontend_dist else ""
FRONTEND_INDEX = os.path.join(FRONTEND_DIST_DIR, "index.html") if FRONTEND_DIST_DIR else ""
SERVE_FRONTEND = bool(FRONTEND_INDEX) and os.path.isfile(FRONTEND_INDEX)


@app.get("/")
def root():
    if SERVE_FRONTEND:
        return FileResponse(FRONTEND_INDEX)
    return {
        "application": "Risk Assessment Workbench",
        "status": "running",
        "version": "0.1.0",
    }


@app.get("/health")
def health():
    return {
        "status": "healthy"
    }


if SERVE_FRONTEND:
    # Registered last so every API route above matches first.
    @app.get("/{path:path}", include_in_schema=False)
    def frontend(path: str):
        if path == "api" or path.startswith("api/"):
            raise HTTPException(status_code=404, detail="Not Found")

        candidate = os.path.realpath(os.path.join(FRONTEND_DIST_DIR, path))
        # Only files inside the build folder; anything else (including
        # "../" tricks) falls through to the app shell.
        if candidate.startswith(FRONTEND_DIST_DIR + os.sep) and os.path.isfile(candidate):
            return FileResponse(candidate)
        return FileResponse(FRONTEND_INDEX)

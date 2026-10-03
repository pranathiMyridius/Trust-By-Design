import os

from fastapi import APIRouter, Depends, HTTPException
from fastapi.concurrency import run_in_threadpool
from sqlalchemy.orm import Session

from app.auth.dependencies import require_role
from app.database import IS_POSTGRES, get_db
from app.models.user import User, UserRole
from app.services import backup, degraded_metrics, performance
from app.services.audit_service import AuditAction, log_audit_event

# Stage 19: operational endpoints for the non-functional requirements --
# performance against service targets, backup/recovery, integrity, and the
# security posture. Admin only. Restore is intentionally CLI-only (see
# app/services/backup.py).
router = APIRouter(prefix="/api/system", tags=["System"])

require_admin = require_role(UserRole.ADMIN)


@router.get("/ai-reliability")
def get_ai_reliability(
    window_hours: int = 24,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_admin),
):
    """
    AI provider reliability and how often risk analysis has had to fall
    back to deterministic rules (see app/risk_engine/degraded.py).

    Worth watching: a climbing rules_only_rate usually means an expired
    key, an exhausted quota or a provider incident, not a change in the
    risk profile of what is being assessed.
    """

    return degraded_metrics.degraded_summary(db, window_hours=window_hours)


@router.get("/performance")
def get_performance(current_user: User = Depends(require_admin)):
    return performance.summary()


@router.get("/recovery-status")
def get_recovery_status(current_user: User = Depends(require_admin)):
    return backup.recovery_status()


@router.get("/integrity")
async def get_integrity(current_user: User = Depends(require_admin)):
    return await run_in_threadpool(backup.check_database_integrity)


@router.get("/backups")
def list_backups(current_user: User = Depends(require_admin)):
    return backup.list_backups()


@router.post("/backups", status_code=201)
async def create_backup(
    db: Session = Depends(get_db),
    current_user: User = Depends(require_admin),
):
    actor = current_user.full_name or current_user.email
    try:
        result = await run_in_threadpool(backup.create_backup, "manual", actor)
    except Exception as exc:  # noqa: BLE001 -- report why, don't 500 opaquely
        raise HTTPException(status_code=500, detail=f"Backup failed: {exc}")

    log_audit_event(
        db=db,
        assessment_id=None,
        action=AuditAction.BACKUP_CREATED,
        actor=actor,
        actor_id=current_user.id,
        details=f"Backup {result['name']} created ({result['size_bytes']} bytes).",
    )
    db.commit()
    return result


@router.post("/backups/{name}/verify")
async def verify_backup(
    name: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_admin),
):
    try:
        result = await run_in_threadpool(backup.verify_backup, name)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="Backup not found.")

    log_audit_event(
        db=db,
        assessment_id=None,
        action=AuditAction.BACKUP_VERIFIED,
        actor=current_user.full_name or current_user.email,
        actor_id=current_user.id,
        details=(
            f"Backup {name} verified OK."
            if result["valid"]
            else f"Backup {name} FAILED verification: " + "; ".join(result["problems"])
        ),
    )
    db.commit()
    return result


@router.get("/security-posture")
def get_security_posture(current_user: User = Depends(require_admin)):
    """A self-check of the security-related configuration, so gaps (e.g.
    no encryption key, HTTPS not enforced) are visible rather than silent."""

    from app import security_settings as sec
    from app.database import DATABASE_URL
    from app.file_processing.storage import encryption_enabled
    from app.services.data_masking import ai_masking_enabled

    tls_problem = sec.database_tls_problem(DATABASE_URL)
    problems = sec.security_problems(DATABASE_URL)
    return {
        # P7: the environment, and whether a production start would be refused.
        "app_env": sec.app_env(),
        "production_enforced": sec.is_production(),
        "production_ready": not problems,
        "jwt_secret_configured": bool(os.getenv("JWT_SECRET")),
        "jwt_secret_strong": sec.jwt_secret_problem() is None,
        "file_encryption_at_rest": encryption_enabled(),
        "file_encryption_key_valid": sec.file_key_problem() is None,
        "https_enforced": sec.https_problem() is None,
        # True for a local database (no network hop) or a remote one with TLS required.
        "database_tls_required": tls_problem is None,
        "database_is_postgres": IS_POSTGRES,
        "database_insecure_transport_override": sec.insecure_transport_allowed(),
        # Provider-managed; not verifiable from the application (docs/NON_FUNCTIONAL_REQUIREMENTS.md).
        "database_encryption_at_rest": "PROVIDER_MANAGED_NOT_VERIFIABLE_IN_APP" if IS_POSTGRES else "NOT_ENCRYPTED_LOCAL_FILE",
        "ai_payload_masking": ai_masking_enabled(),
        "all_api_routes_require_authentication": True,
        "admin_actions_logged": True,
        "recommendations": problems,
    }

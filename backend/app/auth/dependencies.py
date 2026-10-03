from fastapi import Depends, HTTPException, Request, status
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy.orm import Session

from app.auth.security import decode_access_token
from app.database import get_db
from app.models.user import User

# tokenUrl is only used by the OpenAPI docs' "Authorize" button; the
# frontend calls /api/auth/login directly with JSON, not form data.
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/auth/login", auto_error=False)


def get_current_user(
    token: str | None = Depends(oauth2_scheme),
    db: Session = Depends(get_db),
) -> User:
    credentials_error = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )

    if not token:
        raise credentials_error

    user_id = decode_access_token(token)

    if user_id is None:
        raise credentials_error

    user = db.query(User).filter(User.id == int(user_id)).first()

    if user is None or not user.is_active:
        raise credentials_error

    return user


def log_denied_attempt(
    db: Session,
    user: User,
    request: Request,
    why: str,
    assessment_id: int | None = None,
) -> None:
    """R15.5: "the system prevents unauthorized actions and records the
    denied attempt". Committed on the spot, because the request is about
    to fail and its own transaction will not be committed. Never raises."""

    from app.services.audit_service import AuditAction, log_audit_event

    try:
        if assessment_id is None:
            from_path = request.path_params.get("assessment_id")
            assessment_id = int(from_path) if str(from_path or "").isdigit() else None
        log_audit_event(
            db=db,
            assessment_id=assessment_id,
            action=AuditAction.ACCESS_DENIED,
            actor=user.full_name or user.email,
            actor_id=user.id,
            details=f"Denied {request.method} {request.url.path} for role {user.role}: {why}.",
        )
        db.commit()
    except Exception:  # noqa: BLE001 -- logging must not change the response
        db.rollback()


def require_role(*roles: str):
    """
    Dependency factory: 403s unless current_user.role is one of `roles`.
    Usage: Depends(require_role(UserRole.ADMIN)).
    """

    allowed = {role.value if hasattr(role, "value") else role for role in roles}

    def _check(
        request: Request,
        current_user: User = Depends(get_current_user),
        db: Session = Depends(get_db),
    ) -> User:
        if current_user.role not in allowed:
            # R15.5: a refused action is recorded.
            log_denied_attempt(db, current_user, request, f"requires role {', '.join(sorted(allowed))}")
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="You do not have permission to perform this action.",
            )
        return current_user

    return _check

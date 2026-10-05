import os
import secrets

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from app.auth.dependencies import get_current_user, require_role
from app.auth.security import RESET_TOKEN_MINUTES, WELCOME_TOKEN_MINUTES, create_reset_token, hash_password
from app.database import get_db
from app.models.user import SCOPE_FIELDS, User, UserRole
from app.schemas.user import DesignationsUpdate, UserCreate, UserResponse, UserUpdate
from app.services.audit_service import AuditAction, log_audit_event
from app.services.email_service import email_enabled
from app.services.notifications import send_password_reset_email, send_welcome_email


class UserCreatedResponse(UserResponse):
    # True when the welcome email was handed to the mail server.
    email_sent: bool = False


def _app_url() -> str:
    return os.getenv("APP_BASE_URL", "http://localhost:5173").rstrip("/")


def _actor(user: User) -> str:
    return user.full_name or user.email

router = APIRouter(
    prefix="/api/users",
    tags=["Users"],
    dependencies=[Depends(require_role(UserRole.ADMIN))],
)


@router.post("", response_model=UserCreatedResponse, status_code=201)
def create_user(
    payload: UserCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    if db.query(User).filter(User.email == payload.email).first():
        raise HTTPException(status_code=409, detail="A user with this email already exists.")

    if payload.manager_id is not None:
        manager = db.query(User).filter(User.id == payload.manager_id).first()
        if not manager:
            raise HTTPException(status_code=404, detail="manager_id does not refer to an existing user.")

    # No password given: the user chooses their own from the welcome email, so
    # the account gets an unguessable placeholder until then. That only works
    # when the server can send email.
    if not payload.password and not email_enabled():
        raise HTTPException(
            status_code=422,
            detail="Email isn't set up on this server, so enter a temporary password for the user instead.",
        )
    user = User(
        email=payload.email,
        hashed_password=hash_password(payload.password or secrets.token_urlsafe(32)),
        full_name=payload.full_name,
        role=payload.role,
        manager_id=payload.manager_id,
    )
    for field in SCOPE_FIELDS:
        user.set_scope(field, getattr(payload, field))

    db.add(user)
    db.flush()

    # Stage 19 (Security): administrative actions are logged -- who created
    # which account with which role. Never the password.
    log_audit_event(
        db=db,
        assessment_id=None,
        action=AuditAction.USER_CREATED,
        actor=_actor(current_user),
        actor_id=current_user.id,
        details=f"User {user.email} (id {user.id}) created with role {user.role}.",
    )

    db.commit()
    db.refresh(user)

    # Welcome email with a one-time link to choose a password. Sent after the
    # account is saved; a mail problem never undoes the account.
    token = create_reset_token(user.id, user.hashed_password, WELCOME_TOKEN_MINUTES)
    email_sent = send_welcome_email(
        user, f"{_app_url()}/#reset={token}", WELCOME_TOKEN_MINUTES, _actor(current_user)
    )

    return UserCreatedResponse.model_validate(user).model_copy(update={"email_sent": email_sent})


@router.get("", response_model=list[UserResponse])
def list_users(db: Session = Depends(get_db)):
    return db.query(User).order_by(User.created_at.desc()).all()


@router.patch("/{user_id}", response_model=UserResponse)
def update_user(
    user_id: int,
    payload: UserUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    user = db.query(User).filter(User.id == user_id).first()

    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    data = payload.model_dump(exclude_unset=True)

    if "manager_id" in data and data["manager_id"] is not None:
        manager = db.query(User).filter(User.id == data["manager_id"]).first()
        if not manager:
            raise HTTPException(status_code=404, detail="manager_id does not refer to an existing user.")

    password = data.pop("password", None)
    if password:
        user.hashed_password = hash_password(password)

    changes = []
    # R15.4: scope lists are stored as JSON; compare and record them as lists.
    for field in SCOPE_FIELDS:
        if field in data:
            previous = user.get_scope(field)
            user.set_scope(field, data.pop(field))
            if user.get_scope(field) != previous:
                changes.append(f"{field}: {previous!r} -> {user.get_scope(field)!r}")

    for field, value in data.items():
        previous = getattr(user, field)
        if previous != value:
            changes.append(f"{field}: {previous!r} -> {value!r}")
        setattr(user, field, value)
    if password:
        changes.append("password reset")

    if changes:
        log_audit_event(
            db=db,
            assessment_id=None,
            action=AuditAction.USER_UPDATED,
            actor=_actor(current_user),
            actor_id=current_user.id,
            details=f"User {user.email} (id {user.id}) updated: " + "; ".join(changes) + ".",
        )

    db.commit()
    db.refresh(user)

    return user


@router.post("/{user_id}/send-reset-email")
def send_reset_email(
    user_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Email the user a one-time link to choose a new password. The admin
    never sees or sets the password."""

    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    if not user.is_active:
        raise HTTPException(status_code=409, detail="This account is deactivated. Activate it before sending a reset email.")
    if not email_enabled():
        raise HTTPException(
            status_code=503,
            detail="Email isn't set up on this server (NOTIFY_EMAIL_ENABLED and the SMTP settings), so no reset email can be sent.",
        )

    token = create_reset_token(user.id, user.hashed_password)
    if not send_password_reset_email(user, f"{_app_url()}/#reset={token}", RESET_TOKEN_MINUTES):
        raise HTTPException(status_code=502, detail="The reset email couldn't be sent. Please try again.")

    log_audit_event(
        db=db,
        assessment_id=None,
        action=AuditAction.USER_UPDATED,
        actor=_actor(current_user),
        actor_id=current_user.id,
        details=f"User {user.email} (id {user.id}) updated: password reset email sent.",
    )
    db.commit()
    return {"message": f"A password reset email is on its way to {user.email}.", "email_sent": True}


@router.put("/{user_id}/designations", response_model=UserResponse)
def set_user_designations(
    user_id: int,
    payload: DesignationsUpdate,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """P3: governance designations (Senior Analyst, Head of FCRM, Committee
    Chair, ...). Admin only (router dependency); never one's own; each must
    be allowed for the user's base role; every change is audited."""

    from app.auth.dependencies import log_denied_attempt
    from app.governance.policy import DESIGNATIONS, policy

    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    if user.id == current_user.id:
        log_denied_attempt(db, current_user, request, "an administrator cannot change their own designations")
        raise HTTPException(status_code=403, detail="You cannot change your own governance designations.")

    unknown = sorted(set(payload.designations) - set(DESIGNATIONS))
    if unknown:
        raise HTTPException(status_code=422, detail=f"Unknown designation(s): {', '.join(unknown)}.")
    base_roles = policy()["designation_base_roles"]
    wrong = [d for d in payload.designations if user.role not in base_roles.get(d, [])]
    if wrong:
        raise HTTPException(
            status_code=422,
            detail="; ".join(f"{d} needs role {' or '.join(base_roles.get(d, []))} (user is {user.role})" for d in wrong),
        )

    previous = user.get_designations()
    user.set_designations(payload.designations)
    if user.get_designations() != previous:
        log_audit_event(
            db=db,
            assessment_id=None,
            action=AuditAction.USER_DESIGNATIONS_CHANGED,
            actor=_actor(current_user),
            actor_id=current_user.id,
            details=(
                f"User {user.email} (id {user.id}) designations {previous!r} -> {user.get_designations()!r}. "
                f"Reason: {payload.reason}"
            ),
        )
    db.commit()
    db.refresh(user)
    return user

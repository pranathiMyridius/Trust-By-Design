import logging
import os
import time

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func
from pydantic import BaseModel, field_validator
from sqlalchemy.orm import Session

from app.auth.dependencies import get_current_user
from app.auth.security import (
    RESET_TOKEN_MINUTES,
    create_access_token,
    create_reset_token,
    hash_password,
    read_reset_token,
    reset_token_matches,
    verify_password,
)
from app.database import get_db
from app.models.user import User
from app.schemas.user import LoginRequest, TokenResponse, UserResponse
from app.services.audit_service import AuditAction, log_audit_event
from app.services.notifications import send_password_reset_email

router = APIRouter(prefix="/api/auth", tags=["Auth"])


@router.post("/login", response_model=TokenResponse)
def login(payload: LoginRequest, db: Session = Depends(get_db)):
    user = db.query(User).filter(User.email == payload.email).first()

    if not user or not user.is_active or not verify_password(
        payload.password, user.hashed_password
    ):
        raise HTTPException(status_code=401, detail="Incorrect email or password.")

    token = create_access_token(subject=str(user.id))

    return TokenResponse(access_token=token, user=user)


@router.get("/me", response_model=UserResponse)
def read_current_user(current_user: User = Depends(get_current_user)):
    return current_user


# ---------------------------------------------------------------------------
# Forgot / reset password (self-service, verified by email)
# ---------------------------------------------------------------------------

logger = logging.getLogger(__name__)

# Same answer whether or not the address has an account, so this can't be
# used to discover who has one.
_FORGOT_REPLY = {"message": "If that email address has an account, a reset link is on its way."}
_COOLDOWN_SECONDS = 60
_last_request: dict[str, float] = {}


class ForgotPasswordRequest(BaseModel):
    email: str


class ResetPasswordRequest(BaseModel):
    token: str
    new_password: str

    @field_validator("new_password")
    @classmethod
    def long_enough(cls, value: str) -> str:
        if len(value) < 8:
            raise ValueError("Password must be at least 8 characters.")
        return value


@router.post("/forgot-password")
def forgot_password(payload: ForgotPasswordRequest, db: Session = Depends(get_db)):
    email = payload.email.strip().lower()

    # One reset email per address a minute, so this can't be used to flood
    # someone's inbox.
    now = time.monotonic()
    if now - _last_request.get(email, -_COOLDOWN_SECONDS) < _COOLDOWN_SECONDS:
        logger.warning("Password reset for %s skipped: another was requested in the last minute.", email)
        return _FORGOT_REPLY
    if len(_last_request) > 5000:  # keep the table small
        _last_request.clear()
    _last_request[email] = now

    user = db.query(User).filter(func.lower(User.email) == email).first()
    if not user or not user.is_active:
        # Not told to the caller (that would reveal who has an account); the
        # server log says why no email went out.
        logger.warning("Password reset for %s skipped: no active account with that email.", email)
    else:
        base = os.getenv("APP_BASE_URL", "http://localhost:5173").rstrip("/")
        token = create_reset_token(user.id, user.hashed_password)
        # The token goes in the URL fragment, which browsers never send to a
        # server, so it stays out of access logs and referrer headers.
        send_password_reset_email(user, f"{base}/#reset={token}", RESET_TOKEN_MINUTES)
    return _FORGOT_REPLY


@router.post("/reset-password")
def reset_password(payload: ResetPasswordRequest, db: Session = Depends(get_db)):
    invalid = HTTPException(status_code=400, detail="This reset link is invalid or has expired. Ask for a new one.")

    parsed = read_reset_token(payload.token)
    if parsed is None:
        raise invalid
    user_id, fingerprint = parsed

    user = db.query(User).filter(User.id == user_id).first()
    # The fingerprint no longer matches once the password has changed, so a
    # link works once.
    if not user or not user.is_active or not reset_token_matches(fingerprint, user.hashed_password):
        raise invalid

    user.hashed_password = hash_password(payload.new_password)
    log_audit_event(
        db=db,
        assessment_id=None,
        action=AuditAction.USER_UPDATED,
        actor=user.full_name or user.email,
        actor_id=user.id,
        details=f"User {user.email} (id {user.id}) updated: password reset by email link.",
    )
    db.commit()
    return {"message": "Your password has been updated. You can sign in now."}

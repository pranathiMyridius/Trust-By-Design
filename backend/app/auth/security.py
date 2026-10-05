import hashlib
import logging
import os
import secrets
from datetime import datetime, timedelta, timezone

from dotenv import load_dotenv
from jose import JWTError, jwt
from passlib.context import CryptContext

load_dotenv()

# Stage 19 (Security): no signing secret lives in code. JWT_SECRET must be
# supplied via the environment (backend/.env or a secrets manager -- see
# backend/.env.example). If it's missing, a random per-process secret is
# generated so the app still starts for local development, at the cost
# of every session being invalidated on restart.
JWT_SECRET = os.getenv("JWT_SECRET") or ""
if not JWT_SECRET:
    JWT_SECRET = secrets.token_urlsafe(64)
    logging.getLogger(__name__).warning(
        "JWT_SECRET is not set; using an ephemeral random secret. Sessions "
        "will not survive a restart. Set JWT_SECRET for any real deployment."
    )
JWT_ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = int(os.getenv("ACCESS_TOKEN_EXPIRE_MINUTES", str(8 * 60)))

_pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


def hash_password(password: str) -> str:
    return _pwd_context.hash(password)


def verify_password(plain_password: str, hashed_password: str) -> bool:
    return _pwd_context.verify(plain_password, hashed_password)


def create_access_token(subject: str) -> str:
    expire = datetime.now(timezone.utc) + timedelta(
        minutes=ACCESS_TOKEN_EXPIRE_MINUTES
    )
    payload = {"sub": subject, "exp": expire}
    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)


def decode_access_token(token: str) -> str | None:
    """Returns the token's subject (user id, as a string), or None if the
    token is missing, malformed, or expired."""

    try:
        payload = jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
    except JWTError:
        return None

    return payload.get("sub")


# Password reset tokens are signed, expire quickly, and carry a fingerprint
# of the password hash they were issued against -- so the moment the password
# changes (by this reset or any other) the token stops working. That makes
# each link single-use without storing anything.
RESET_TOKEN_MINUTES = int(os.getenv("PASSWORD_RESET_MINUTES", "30"))


def _password_fingerprint(hashed_password: str) -> str:
    return hashlib.sha256(hashed_password.encode()).hexdigest()[:20]


def create_reset_token(user_id: int, hashed_password: str) -> str:
    payload = {
        "sub": str(user_id),
        "purpose": "password-reset",
        "pwf": _password_fingerprint(hashed_password),
        "exp": datetime.now(timezone.utc) + timedelta(minutes=RESET_TOKEN_MINUTES),
    }
    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)


def read_reset_token(token: str) -> tuple[int, str] | None:
    """(user id, password fingerprint) for a valid, unexpired reset token."""

    try:
        payload = jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
        if payload.get("purpose") != "password-reset":
            return None
        return int(payload["sub"]), str(payload["pwf"])
    except (JWTError, KeyError, ValueError):
        return None


def reset_token_matches(token_fingerprint: str, hashed_password: str) -> bool:
    return secrets.compare_digest(token_fingerprint, _password_fingerprint(hashed_password))

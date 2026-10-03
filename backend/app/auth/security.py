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

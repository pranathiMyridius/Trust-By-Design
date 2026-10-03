"""
P7 (R19): transport and at-rest encryption requirements, checked in code.

* Database transport: a remote Postgres connection must use TLS
  (`sslmode` require / verify-ca / verify-full). app/database.py refuses to
  build an engine otherwise, unless DATABASE_ALLOW_INSECURE_TRANSPORT=true
  is set deliberately (never in production). Local databases (SQLite,
  Postgres on localhost) are exempt.
* Production: with APP_ENV=production the app refuses to start unless a
  real JWT_SECRET (>= 32 characters), a valid FILE_ENCRYPTION_KEY,
  FORCE_HTTPS=true and a TLS database connection are configured, and the
  insecure-transport override is off. Without APP_ENV=production these are
  reported by GET /api/system/security-posture instead of enforced.
* Database encryption at rest is the database provider's (Neon:
  provider-managed storage encryption). It can't be verified from the
  application; see docs/NON_FUNCTIONAL_REQUIREMENTS.md.

Nothing here prints or logs a secret, a key or a connection string.
"""

from __future__ import annotations

import os

from sqlalchemy.engine import make_url

TLS_SSLMODES = {"require", "verify-ca", "verify-full"}
LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1"}
PRODUCTION_ENVS = {"production", "prod"}
MIN_JWT_SECRET_LENGTH = 32


def _truthy(value: str | None) -> bool:
    return (value or "").strip().lower() in {"1", "true", "yes", "on"}


def app_env() -> str:
    return (os.getenv("APP_ENV") or "development").strip().lower()


def is_production() -> bool:
    return app_env() in PRODUCTION_ENVS


def insecure_transport_allowed() -> bool:
    return _truthy(os.getenv("DATABASE_ALLOW_INSECURE_TRANSPORT"))


def database_tls_problem(url: str) -> str | None:
    """Why `url` would connect without TLS, or None (TLS required, or a
    local database). Never includes the URL itself."""

    try:
        parsed = make_url(url)
    except Exception:  # noqa: BLE001 -- an unparsable URL is someone else's error
        return None
    if parsed.get_backend_name() != "postgresql":
        return None
    host = (parsed.host or "localhost").lower()
    if host in LOCAL_HOSTS:
        return None
    mode = (parsed.query.get("sslmode") or "").strip().lower()
    if mode in TLS_SSLMODES:
        return None
    return (
        f"The database at {host} is remote but its connection does not require TLS "
        f"(sslmode={mode or 'not set'}). Add ?sslmode=require (or verify-full) to DATABASE_URL."
    )


def file_key_problem(key: str | None = None) -> str | None:
    key = os.getenv("FILE_ENCRYPTION_KEY") if key is None else key
    if not key:
        return "FILE_ENCRYPTION_KEY is not set, so uploaded files are stored unencrypted."
    try:
        from cryptography.fernet import Fernet

        Fernet(key.encode() if isinstance(key, str) else key)
    except Exception:  # noqa: BLE001
        return "FILE_ENCRYPTION_KEY is not a valid Fernet key (generate one with Fernet.generate_key())."
    return previous_keys_problem()


def previous_keys_problem() -> str | None:
    """FILE_ENCRYPTION_PREVIOUS_KEYS (decrypt-only keys kept during a key
    rotation) must each be a valid Fernet key. Never echoes a key."""

    raw = os.getenv("FILE_ENCRYPTION_PREVIOUS_KEYS") or ""
    keys = [k.strip() for k in raw.split(",") if k.strip()]
    from cryptography.fernet import Fernet

    for position, key in enumerate(keys, start=1):
        try:
            Fernet(key.encode())
        except Exception:  # noqa: BLE001
            return f"FILE_ENCRYPTION_PREVIOUS_KEYS entry {position} is not a valid Fernet key."
    return None


def jwt_secret_problem() -> str | None:
    secret = os.getenv("JWT_SECRET") or ""
    if not secret:
        return "JWT_SECRET is not set (an ephemeral secret is used; sessions reset on restart)."
    if len(secret) < MIN_JWT_SECRET_LENGTH:
        return f"JWT_SECRET is shorter than {MIN_JWT_SECRET_LENGTH} characters."
    return None


def https_problem() -> str | None:
    if not _truthy(os.getenv("FORCE_HTTPS")):
        return "FORCE_HTTPS is not true; plain HTTP is not redirected to HTTPS."
    return None


def security_problems(database_url: str | None = None) -> list[str]:
    from app.database import DATABASE_URL

    url = database_url if database_url is not None else DATABASE_URL
    problems = [p for p in (jwt_secret_problem(), file_key_problem(), https_problem(), database_tls_problem(url)) if p]
    if insecure_transport_allowed():
        problems.append("DATABASE_ALLOW_INSECURE_TRANSPORT is set; it must never be used in production.")
    return problems


class InsecureConfiguration(RuntimeError):
    pass


def enforce_production_security(database_url: str | None = None) -> None:
    """Called before the app prepares its database. Refuses to start a
    production deployment with any encryption requirement unmet."""

    if not is_production():
        return
    problems = security_problems(database_url)
    if problems:
        raise InsecureConfiguration(
            "APP_ENV=production but the security configuration is incomplete: " + " ".join(problems)
        )

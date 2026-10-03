"""
P7 (R19): database TLS enforcement, production key requirements, file
encryption at rest, the security-posture report, and the two scripts.
Remote URLs here are fake (.invalid) and never connected to; database-
module imports run in subprocesses with their own environment.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest
from cryptography.fernet import Fernet

from app import security_settings as sec

BACKEND = Path(__file__).resolve().parents[2]
REMOTE = "postgresql://u:p@db.example.invalid:5432/app"


@pytest.mark.parametrize(
    "url, ok",
    [
        ("sqlite:///x.db", True),
        ("postgresql://u:p@localhost/app", True),
        ("postgresql://u:p@127.0.0.1/app", True),
        (REMOTE, False),
        (REMOTE + "?sslmode=disable", False),
        (REMOTE + "?sslmode=prefer", False),
        (REMOTE + "?sslmode=require", True),
        (REMOTE + "?sslmode=verify-full", True),
    ],
)
def test_remote_postgres_must_require_tls(url, ok):
    problem = sec.database_tls_problem(url)
    assert (problem is None) is ok
    if problem:
        assert "u:p@" not in problem and "db.example.invalid" in problem  # host only, never credentials


def _import_database(env: dict) -> subprocess.CompletedProcess:
    full = {k: v for k, v in os.environ.items() if k not in {"DATABASE_URL", "APP_ENV", "DATABASE_ALLOW_INSECURE_TRANSPORT"}}
    full.update(env)
    return subprocess.run([sys.executable, "-c", "import app.database"], cwd=BACKEND, env=full, capture_output=True, text=True, timeout=120)


def test_database_module_refuses_a_remote_url_without_tls():
    refused = _import_database({"DATABASE_URL": REMOTE})
    assert refused.returncode != 0 and "does not require TLS" in refused.stderr and "u:p@" not in refused.stderr
    assert _import_database({"DATABASE_URL": REMOTE + "?sslmode=require"}).returncode == 0
    # A deliberate non-production override is honoured ...
    assert _import_database({"DATABASE_URL": REMOTE, "DATABASE_ALLOW_INSECURE_TRANSPORT": "true"}).returncode == 0
    # ... but never in production.
    prod = _import_database({"DATABASE_URL": REMOTE, "DATABASE_ALLOW_INSECURE_TRANSPORT": "true", "APP_ENV": "production"})
    assert prod.returncode != 0


@pytest.fixture
def clean_env(monkeypatch):
    for name in ("APP_ENV", "JWT_SECRET", "FILE_ENCRYPTION_KEY", "FORCE_HTTPS", "DATABASE_ALLOW_INSECURE_TRANSPORT"):
        monkeypatch.delenv(name, raising=False)
    return monkeypatch


def test_production_refuses_to_start_until_every_requirement_is_met(clean_env):
    # Not production: reported, not enforced.
    sec.enforce_production_security(REMOTE)

    clean_env.setenv("APP_ENV", "production")
    with pytest.raises(sec.InsecureConfiguration) as refused:
        sec.enforce_production_security(REMOTE)
    message = str(refused.value)
    for fragment in ("JWT_SECRET is not set", "FILE_ENCRYPTION_KEY is not set", "FORCE_HTTPS", "does not require TLS"):
        assert fragment in message

    clean_env.setenv("JWT_SECRET", "short")
    clean_env.setenv("FILE_ENCRYPTION_KEY", "not-a-fernet-key")
    message = str(pytest.raises(sec.InsecureConfiguration, sec.enforce_production_security, REMOTE).value)
    assert "shorter than 32" in message and "not a valid Fernet key" in message
    assert "not-a-fernet-key" not in message  # the key itself is never echoed

    clean_env.setenv("JWT_SECRET", "x" * 48)
    clean_env.setenv("FILE_ENCRYPTION_KEY", Fernet.generate_key().decode())
    clean_env.setenv("FORCE_HTTPS", "true")
    sec.enforce_production_security(REMOTE + "?sslmode=require")  # all met: starts
    clean_env.setenv("DATABASE_ALLOW_INSECURE_TRANSPORT", "true")
    with pytest.raises(sec.InsecureConfiguration):
        sec.enforce_production_security(REMOTE + "?sslmode=require")


def test_files_are_encrypted_at_rest_and_read_back(clean_env, tmp_path):
    from app.file_processing import storage

    clean_env.setenv("FILE_ENCRYPTION_KEY", Fernet.generate_key().decode())
    clean_env.setattr(storage, "STORAGE_DIR", str(tmp_path))
    path = storage.save_file(9999, "evidence.txt", b"confidential evidence")
    assert Path(path).resolve().is_relative_to(tmp_path.resolve())
    raw = Path(path).read_bytes()
    assert raw.startswith(b"RAWENC1:") and b"confidential evidence" not in raw
    assert storage.read_file(path) == b"confidential evidence"


def test_security_posture_reports_p7_fields(client, auth):
    from tests.conftest import ok

    posture = ok(client.get("/api/system/security-posture", headers=auth("admin")))
    for key in ("app_env", "production_enforced", "production_ready", "jwt_secret_strong", "file_encryption_key_valid",
                "database_tls_required", "database_encryption_at_rest", "recommendations"):
        assert key in posture
    assert posture["production_enforced"] is False  # tests run in development mode
    assert posture["database_tls_required"] is True  # SQLite: no network hop
    assert client.get("/api/system/security-posture", headers=auth("analyst")).status_code == 403


def test_encrypt_existing_files_dry_run_then_apply(tmp_path):
    key = Fernet.generate_key().decode()
    (tmp_path / "a").mkdir()
    (tmp_path / "a" / "legacy.pdf").write_bytes(b"plaintext legacy upload")
    already = b"RAWENC1:" + Fernet(key.encode()).encrypt(b"new upload")
    (tmp_path / "a" / "new.pdf").write_bytes(already)
    env = {**os.environ, "FILE_ENCRYPTION_KEY": key}
    script = str(BACKEND / "scripts" / "encrypt_existing_files.py")

    dry = subprocess.run([sys.executable, script, "--root", str(tmp_path)], env=env, capture_output=True, text=True)
    assert dry.returncode == 0 and "1 plaintext" in dry.stdout and "Dry run" in dry.stdout
    assert (tmp_path / "a" / "legacy.pdf").read_bytes() == b"plaintext legacy upload"

    applied = subprocess.run([sys.executable, script, "--root", str(tmp_path), "--apply"], env=env, capture_output=True, text=True)
    assert applied.returncode == 0 and "Encrypted 1 file" in applied.stdout
    converted = (tmp_path / "a" / "legacy.pdf").read_bytes()
    assert converted.startswith(b"RAWENC1:") and Fernet(key.encode()).decrypt(converted[8:]) == b"plaintext legacy upload"
    assert (tmp_path / "a" / "new.pdf").read_bytes() == already  # untouched
    assert "legacy" not in applied.stdout  # no file names printed

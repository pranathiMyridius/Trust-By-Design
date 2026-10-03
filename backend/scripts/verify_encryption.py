"""
P7 (R19): READ-ONLY evidence of encryption in transit and at rest.

    python scripts/verify_encryption.py --target staging     # STAGING_DATABASE_URL
    python scripts/verify_encryption.py --target database    # DATABASE_URL (the configured app database)
    python scripts/verify_encryption.py --target none        # files and keys only

Checks, each reported (never a secret, key, file name or connection string):
  * database transport: the URL requires TLS, and a live read-only
    connection is actually encrypted (the driver's own view: protocol and
    cipher), then `SELECT 1` in a READ ONLY transaction;
  * uploaded files: how many carry the encryption marker vs plaintext;
  * backups: whether backup files are encrypted (marker files and raw
    Fernet-token snapshot files; manifests are reported as metadata);
  * keys: whether FILE_ENCRYPTION_KEY is valid and JWT_SECRET is strong.
Database encryption at rest is provider-managed and is reported as such,
not as verified.

Writes eval_results/encryption_verification_<target>.json.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

from dotenv import dotenv_values  # noqa: E402
from sqlalchemy import create_engine, text  # noqa: E402
from sqlalchemy.engine import make_url  # noqa: E402

MARKER = b"RAWENC1:"
FERNET_PREFIX = b"gAAAAA"  # a raw Fernet token: version byte 0x80 + timestamp, base64url


def _env(name: str) -> str | None:
    return os.environ.get(name) or dotenv_values(BACKEND / ".env").get(name)


def _tls_problem(url: str) -> str | None:
    # Same rule as app/security_settings.py, without importing the app
    # (importing app.database would bind an engine to DATABASE_URL).
    parsed = make_url(url)
    if parsed.get_backend_name() != "postgresql" or (parsed.host or "localhost").lower() in {"localhost", "127.0.0.1", "::1"}:
        return None
    mode = (parsed.query.get("sslmode") or "").lower()
    return None if mode in {"require", "verify-ca", "verify-full"} else f"sslmode={mode or 'not set'}"


def check_database(url: str) -> dict:
    parsed = make_url(url)
    out = {"backend": parsed.get_backend_name(), "endpoint": (parsed.host or "local").split(".")[0],
           "url_requires_tls": _tls_problem(url) is None}
    if parsed.get_backend_name() != "postgresql":
        out["note"] = "SQLite file: no network transport; the file itself is not encrypted."
        return out
    engine = create_engine(url)
    try:
        with engine.connect() as connection:
            connection.execute(text("SET TRANSACTION READ ONLY"))
            raw = connection.connection.dbapi_connection
            info = raw.info
            out.update(
                ssl_in_use=bool(info.ssl_in_use),
                ssl_protocol=info.ssl_attribute("protocol"),
                ssl_cipher=info.ssl_attribute("cipher"),
                ssl_key_bits=info.ssl_attribute("key_bits"),
                server_version=connection.execute(text("SHOW server_version")).scalar(),
                read_only_query_ok=connection.execute(text("SELECT 1")).scalar() == 1,
            )
    finally:
        engine.dispose()
    out["database_encryption_at_rest"] = "PROVIDER_MANAGED_NOT_VERIFIABLE_IN_APP"
    return out


def check_files(root: Path) -> dict:
    files = [p for p in root.rglob("*") if p.is_file()] if root.exists() else []
    encrypted = 0
    for p in files:
        with p.open("rb") as handle:
            if handle.read(len(MARKER)) == MARKER:
                encrypted += 1
    return {"directory": root.name, "files": len(files), "encrypted": encrypted, "plaintext": len(files) - encrypted}


def check_backups(root: Path) -> dict:
    """Backups hold two encrypted forms: RAWENC1-marker files (application
    backups) and raw Fernet tokens (`*.enc` in logical snapshots). Each
    backup's manifest (names, hashes, counts; no records) is metadata and
    is counted separately, not as plaintext data."""

    files = [p for p in root.rglob("*") if p.is_file()] if root.exists() else []
    encrypted = metadata = 0
    for p in files:
        if p.name.lower() == "manifest.json":
            metadata += 1
            continue
        with p.open("rb") as handle:
            head = handle.read(len(MARKER))
        if head == MARKER or head.startswith(FERNET_PREFIX):
            encrypted += 1
    data_files = len(files) - metadata
    return {"directory": root.name, "files": data_files, "encrypted": encrypted,
            "plaintext": data_files - encrypted, "metadata_files": metadata}


def check_keys() -> dict:
    key = _env("FILE_ENCRYPTION_KEY")
    valid = False
    if key:
        try:
            from cryptography.fernet import Fernet

            Fernet(key.encode())
            valid = True
        except Exception:  # noqa: BLE001
            valid = False
    secret = _env("JWT_SECRET") or ""
    return {"file_encryption_key_set": bool(key), "file_encryption_key_valid": valid,
            "jwt_secret_set": bool(secret), "jwt_secret_at_least_32_chars": len(secret) >= 32,
            "force_https": (_env("FORCE_HTTPS") or "").lower() in {"1", "true", "yes"}}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--target", choices=["staging", "database", "none"], default="none")
    args = parser.parse_args(argv)

    report = {"run_at": datetime.now(timezone.utc).isoformat(), "target": args.target, "checks": []}

    def check(name, ok, detail=""):
        report["checks"].append({"check": name, "ok": bool(ok), "detail": detail})
        print(f"[{'PASS' if ok else 'FAIL'}] {name}" + (f" -- {detail}" if detail else ""))

    if args.target != "none":
        url = _env("STAGING_DATABASE_URL" if args.target == "staging" else "DATABASE_URL")
        if not url:
            print("The target's connection string is not configured.")
            return 2
        db = check_database(url)
        report["database"] = db
        check("database URL requires TLS", db["url_requires_tls"], db["endpoint"])
        if db["backend"] == "postgresql":
            check("live connection is encrypted (driver view)", db.get("ssl_in_use"),
                  f"{db.get('ssl_protocol')} {db.get('ssl_cipher')} ({db.get('ssl_key_bits')}-bit)")
            check("read-only query over the encrypted connection", db.get("read_only_query_ok"))
    files = check_files(BACKEND / "uploaded_files")
    report["uploaded_files"] = files
    check("all uploaded files encrypted at rest", files["plaintext"] == 0, f"{files['encrypted']}/{files['files']} encrypted")
    backups = check_backups(BACKEND / os.path.basename(_env("BACKUP_DIR") or "backups"))
    report["backups"] = backups
    check("backups encrypted at rest", backups["plaintext"] == 0 or backups["files"] == 0, f"{backups['encrypted']}/{backups['files']} encrypted")
    keys = check_keys()
    report["keys"] = keys
    check("FILE_ENCRYPTION_KEY set and valid", keys["file_encryption_key_valid"])
    check("JWT_SECRET set and at least 32 characters", keys["jwt_secret_set"] and keys["jwt_secret_at_least_32_chars"])
    check("FORCE_HTTPS enabled", keys["force_https"], "local development normally runs without it")

    out = BACKEND / "eval_results" / f"encryption_verification_{args.target}.json"
    out.parent.mkdir(exist_ok=True)
    out.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
    failed = [c for c in report["checks"] if not c["ok"]]
    print(f"\n{len(report['checks']) - len(failed)}/{len(report['checks'])} checks passed. Report: {out}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())

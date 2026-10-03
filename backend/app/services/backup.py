"""
Stage 19 (Availability and Recovery): backup, verification and recovery
objectives.

A backup is one folder under BACKUP_DIR (default backend/backups/):

    backup-20260923T101500Z/
        database.sqlite   (SQLite: consistent online copy via the backup API)
        database.dump     (Postgres: pg_dump -Fc, if pg_dump is on PATH)
        uploaded_files/   (every stored evidence file)
        manifest.json     (SHA-256 of every file, row counts, timings)

Encryption at rest (R19): when FILE_ENCRYPTION_KEY is set, the database
copy is taken into memory and written only encrypted, as
database.sqlite.enc / database.dump.enc (RAWENC1 marker + Fernet token,
the same format as uploaded files), so no plaintext copy of the database
touches the disk. Uploads still in plaintext are encrypted as they are
copied. manifest.json holds only metadata (names, hashes, counts) and is
not encrypted. Without the key a backup is written in plaintext, flagged
`encrypted: false`, and a warning is logged. Restoring an encrypted
backup needs the key it was made with (current or in
FILE_ENCRYPTION_PREVIOUS_KEYS).

verify_backup() re-hashes every file against the manifest and, for
SQLite, runs PRAGMA integrity_check on the copy (decrypted in memory)
-- a backup that can't be verified is not counted towards the
recovery point objective.

Restoring is deliberately NOT exposed over the API (it replaces the live
database); use `python restore_backup.py <name> --confirm` from backend/,
which takes a safety backup of the current state first. See
docs/BACKUP_AND_RECOVERY.md.

Recovery objectives (defaults, overridable via env):
    RECOVERY_POINT_OBJECTIVE_HOURS = 24  -- max tolerated data loss
    RECOVERY_TIME_OBJECTIVE_HOURS  = 4   -- max tolerated time to restore
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import shutil
import sqlite3
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path

from cryptography.fernet import InvalidToken
from sqlalchemy import inspect, text

from app.database import DATABASE_URL, IS_POSTGRES, engine
from app.file_processing.storage import decrypt_bytes, encrypt_bytes, encryption_enabled, is_encrypted

logger = logging.getLogger(__name__)

BACKEND_DIR = Path(__file__).resolve().parents[2]
BACKUP_DIR = Path(os.getenv("BACKUP_DIR") or (BACKEND_DIR / "backups"))
BACKUP_KEEP = int(os.getenv("BACKUP_KEEP", "14"))

RPO_HOURS = float(os.getenv("RECOVERY_POINT_OBJECTIVE_HOURS", "24"))
RTO_HOURS = float(os.getenv("RECOVERY_TIME_OBJECTIVE_HOURS", "4"))

MANIFEST = "manifest.json"


def _uploads_dir() -> Path:
    from app.file_processing.storage import STORAGE_DIR

    return Path(STORAGE_DIR)


def _sqlite_path() -> Path:
    raw = DATABASE_URL.replace("sqlite:///", "", 1)
    path = Path(raw)
    return path if path.is_absolute() else (Path.cwd() / path).resolve()


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _row_counts() -> dict[str, int]:
    counts: dict[str, int] = {}
    with engine.connect() as conn:
        for table in inspect(engine).get_table_names():
            try:
                counts[table] = conn.execute(text(f'SELECT COUNT(*) FROM "{table}"')).scalar() or 0
            except Exception:  # noqa: BLE001 -- counts are informational
                counts[table] = -1
    return counts


def _database_bytes() -> tuple[str, bytes]:
    """(file name, contents) of a consistent database copy, held in memory
    so it can be encrypted before anything is written."""

    if IS_POSTGRES:
        pg_dump = shutil.which("pg_dump")
        if not pg_dump:
            raise RuntimeError(
                "pg_dump was not found on PATH, so the Postgres database can't be "
                "backed up from the application. Install the PostgreSQL client tools "
                "or rely on the database platform's own backups."
            )
        url = DATABASE_URL.replace("postgresql+psycopg2://", "postgresql://", 1)
        dump = subprocess.run([pg_dump, "-Fc", url], check=True, capture_output=True)
        return "database.dump", dump.stdout

    src = sqlite3.connect(_sqlite_path())
    mem = sqlite3.connect(":memory:")
    try:
        # Online, transactionally consistent copy even while the app writes.
        src.backup(mem)
        return "database.sqlite", _without_wal_flag(mem.serialize())
    finally:
        mem.close()
        src.close()


def _dump_database(target_dir: Path, encrypt: bool) -> str:
    name, data = _database_bytes()
    if encrypt:
        name, data = name + ".enc", encrypt_bytes(data)
    (target_dir / name).write_bytes(data)
    return name


def _copy_uploads(source: Path, target: Path, encrypt: bool) -> tuple[int, int]:
    """Copies every upload; with `encrypt`, any still in plaintext is
    written encrypted. Returns (files copied, files encrypted on the way)."""

    copied = encrypted = 0
    for path in source.rglob("*"):
        if not path.is_file():
            continue
        out = target / path.relative_to(source)
        out.parent.mkdir(parents=True, exist_ok=True)
        data = path.read_bytes()
        if encrypt and not is_encrypted(data):
            out.write_bytes(encrypt_bytes(data))
            encrypted += 1
        else:
            shutil.copy2(path, out)
        copied += 1
    return copied, encrypted


def create_backup(label: str = "manual", created_by: str | None = None) -> dict:
    started = time.perf_counter()
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)

    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    name = f"backup-{stamp}"
    target = BACKUP_DIR / name
    work = BACKUP_DIR / f".{name}.partial"
    work.mkdir(parents=True)

    encrypt = encryption_enabled()
    if not encrypt:
        logger.warning("FILE_ENCRYPTION_KEY is not set: backup %s is written unencrypted.", name)

    try:
        database_file = _dump_database(work, encrypt)

        uploads = _uploads_dir()
        file_count = encrypted_on_copy = 0
        if uploads.exists():
            file_count, encrypted_on_copy = _copy_uploads(uploads, work / "uploaded_files", encrypt)

        files = {
            str(p.relative_to(work)).replace("\\", "/"): _sha256(p)
            for p in sorted(work.rglob("*"))
            if p.is_file()
        }

        manifest = {
            "name": name,
            "label": label,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "created_by": created_by or "System",
            "database_engine": "postgresql" if IS_POSTGRES else "sqlite",
            "database_file": database_file,
            "encrypted": encrypt,
            "uploaded_file_count": file_count,
            "uploads_encrypted_during_backup": encrypted_on_copy,
            "row_counts": _row_counts(),
            "files": files,
            "duration_seconds": round(time.perf_counter() - started, 3),
        }
        (work / MANIFEST).write_text(json.dumps(manifest, indent=2), encoding="utf-8")

        # Only a fully written backup ever gets its final name.
        work.rename(target)
    except Exception:
        shutil.rmtree(work, ignore_errors=True)
        raise

    _prune_old_backups()
    return summarize(target)


def _prune_old_backups() -> None:
    if BACKUP_KEEP <= 0:
        return
    backups = sorted(p for p in BACKUP_DIR.glob("backup-*") if p.is_dir())
    for old in backups[:-BACKUP_KEEP]:
        shutil.rmtree(old, ignore_errors=True)


def _read_manifest(path: Path) -> dict | None:
    try:
        return json.loads((path / MANIFEST).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def summarize(path: Path) -> dict:
    manifest = _read_manifest(path) or {}
    size = sum(p.stat().st_size for p in path.rglob("*") if p.is_file())
    return {
        "name": path.name,
        "label": manifest.get("label"),
        "created_at": manifest.get("created_at"),
        "created_by": manifest.get("created_by"),
        "database_engine": manifest.get("database_engine"),
        "encrypted": bool(manifest.get("encrypted", False)),
        "uploaded_file_count": manifest.get("uploaded_file_count"),
        "size_bytes": size,
        "duration_seconds": manifest.get("duration_seconds"),
        "valid_manifest": bool(manifest),
    }


def list_backups() -> list[dict]:
    if not BACKUP_DIR.exists():
        return []
    return [
        summarize(p)
        for p in sorted(BACKUP_DIR.glob("backup-*"), reverse=True)
        if p.is_dir()
    ]


def backup_path(name: str) -> Path:
    # Names come from the API: only accept our own folder names.
    if not name.startswith("backup-") or "/" in name or "\\" in name or ".." in name:
        raise ValueError("Invalid backup name.")
    path = BACKUP_DIR / name
    if not path.is_dir():
        raise FileNotFoundError(name)
    return path


def verify_backup(name: str) -> dict:
    path = backup_path(name)
    manifest = _read_manifest(path)
    problems: list[str] = []

    if manifest is None:
        return {"name": name, "valid": False, "problems": ["manifest.json is missing or unreadable."]}

    for relative, expected in manifest.get("files", {}).items():
        file_path = path / relative
        if not file_path.is_file():
            problems.append(f"Missing file: {relative}")
        elif _sha256(file_path) != expected:
            problems.append(f"Checksum mismatch (corrupted): {relative}")

    db_file = path / manifest.get("database_file", "database.sqlite")
    if db_file.is_file() and not problems:
        try:
            plain = decrypt_bytes(db_file.read_bytes())
            if manifest.get("database_engine") == "sqlite":
                conn = _in_memory_sqlite(plain)
                try:
                    result = conn.execute("PRAGMA integrity_check").fetchone()[0]
                finally:
                    conn.close()
                if result != "ok":
                    problems.append(f"SQLite integrity check failed: {result}")
        except InvalidToken:
            problems.append(
                "The database copy can't be decrypted with the configured keys "
                "(FILE_ENCRYPTION_KEY / FILE_ENCRYPTION_PREVIOUS_KEYS)."
            )
        except RuntimeError as exc:  # encrypted, but no key configured
            problems.append(str(exc))

    return {"name": name, "valid": not problems, "problems": problems}


def _in_memory_sqlite(data: bytes) -> sqlite3.Connection:
    """An in-memory database loaded from a backup's (decrypted) bytes, so
    a decrypted copy never touches the disk."""

    conn = sqlite3.connect(":memory:")
    conn.deserialize(_without_wal_flag(data))
    return conn


def _without_wal_flag(data: bytes) -> bytes:
    """A copy of a WAL-mode database records WAL in its header (bytes 18
    and 19 = 2), and SQLite can't open such an image in memory. Setting
    them back to 1 (rollback journal) changes nothing else; the database
    a backup is restored into keeps its own journal mode."""

    if len(data) > 19 and data[18] == 2 and data[19] == 2:
        patched = bytearray(data)
        patched[18] = patched[19] = 1
        return bytes(patched)
    return data


def restore_backup(name: str) -> dict:
    """Replaces the live SQLite database and uploaded files with a backup.
    CLI only (restore_backup.py). Takes a safety backup first."""

    if IS_POSTGRES:
        raise RuntimeError(
            "Automatic restore is only supported for SQLite. For Postgres, restore "
            "database.dump with pg_restore --clean -d <url> (see docs/BACKUP_AND_RECOVERY.md)."
        )

    verification = verify_backup(name)
    if not verification["valid"]:
        raise RuntimeError("Backup failed verification: " + "; ".join(verification["problems"]))

    safety = create_backup(label=f"pre-restore safety copy (before restoring {name})")

    path = backup_path(name)
    manifest = _read_manifest(path) or {}
    engine.dispose()

    source_db = path / manifest.get("database_file", "database.sqlite")
    src = _in_memory_sqlite(decrypt_bytes(source_db.read_bytes()))
    dst = sqlite3.connect(_sqlite_path())
    try:
        src.backup(dst)
    finally:
        src.close()
        dst.close()

    uploads = _uploads_dir()
    backup_uploads = path / "uploaded_files"
    if backup_uploads.exists():
        uploads.mkdir(parents=True, exist_ok=True)
        # Additive: files present now but not in the backup are kept.
        shutil.copytree(backup_uploads, uploads, dirs_exist_ok=True)

    return {"restored": name, "safety_backup": safety["name"]}


def check_database_integrity() -> dict:
    from app.database import SessionLocal
    from app.models.assessment_document import AssessmentDocument

    result: dict = {"database_engine": "postgresql" if IS_POSTGRES else "sqlite"}

    with engine.connect() as conn:
        if IS_POSTGRES:
            conn.execute(text("SELECT 1"))
            result["database_check"] = "ok"
        else:
            result["database_check"] = conn.execute(text("PRAGMA quick_check")).scalar()

    db = SessionLocal()
    try:
        missing = [
            {"document_id": doc_id, "assessment_id": assessment_id, "filename": filename}
            for doc_id, assessment_id, filename, file_path in db.query(
                AssessmentDocument.id,
                AssessmentDocument.assessment_id,
                AssessmentDocument.filename,
                AssessmentDocument.file_path,
            )
            if file_path and not os.path.exists(file_path)
        ]
    finally:
        db.close()

    result["documents_missing_files"] = missing
    result["healthy"] = result["database_check"] == "ok" and not missing
    return result


def recovery_status() -> dict:
    backups = list_backups()
    latest = backups[0] if backups else None

    age_hours = None
    if latest and latest.get("created_at"):
        created = datetime.fromisoformat(latest["created_at"])
        age_hours = round((datetime.now(timezone.utc) - created).total_seconds() / 3600, 2)

    return {
        "recovery_point_objective_hours": RPO_HOURS,
        "recovery_time_objective_hours": RTO_HOURS,
        "backup_interval_hours": float(os.getenv("BACKUP_INTERVAL_HOURS", "24")),
        "backup_directory": str(BACKUP_DIR),
        "backups_retained": len(backups),
        "latest_backup": latest,
        "latest_backup_age_hours": age_hours,
        "rpo_met": age_hours is not None and age_hours <= RPO_HOURS,
        "soft_delete_only": True,
        "audit_log_append_only": True,
    }

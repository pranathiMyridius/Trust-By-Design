"""
P7 follow-up (R19): encrypted application backups and file-encryption key
rotation. Everything runs against the test SQLite database and temporary
directories; restores go to a scratch database file, never the test one.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest
from cryptography.fernet import Fernet

from app import security_settings as sec
from app.file_processing import storage
from app.services import backup

BACKEND = Path(__file__).resolve().parents[2]
ROTATE = str(BACKEND / "scripts" / "rotate_file_encryption_key.py")


@pytest.fixture
def backup_env(client, monkeypatch, tmp_path):
    """Backups and uploads in temporary directories; keys cleared."""

    for name in ("FILE_ENCRYPTION_KEY", "FILE_ENCRYPTION_PREVIOUS_KEYS"):
        monkeypatch.delenv(name, raising=False)
    uploads = tmp_path / "uploads"
    uploads.mkdir()
    monkeypatch.setattr(backup, "BACKUP_DIR", tmp_path / "backups")
    monkeypatch.setattr(backup, "_uploads_dir", lambda: uploads)
    return monkeypatch, uploads


def _manifest(name: str) -> dict:
    return json.loads((backup.backup_path(name) / "manifest.json").read_text())


def test_backup_is_encrypted_when_a_key_is_set(backup_env):
    monkeypatch, uploads = backup_env
    key = Fernet.generate_key().decode()
    monkeypatch.setenv("FILE_ENCRYPTION_KEY", key)
    (uploads / "legacy.txt").write_bytes(b"legacy plaintext evidence")
    (uploads / "new.txt").write_bytes(storage.encrypt_bytes(b"new evidence"))

    created = backup.create_backup(label="test")
    assert created["encrypted"] is True
    path = backup.backup_path(created["name"])
    manifest = _manifest(created["name"])
    assert manifest["database_file"] == "database.sqlite.enc" and manifest["encrypted"] is True
    assert manifest["uploaded_file_count"] == 2 and manifest["uploads_encrypted_during_backup"] == 1
    assert not (path / "database.sqlite").exists()

    db = (path / "database.sqlite.enc").read_bytes()
    assert db.startswith(b"RAWENC1:") and b"SQLite format 3" not in db
    assert storage.decrypt_bytes(db).startswith(b"SQLite format 3")

    # Nothing but the manifest is plaintext; the legacy upload was encrypted on the way.
    for file in path.rglob("*"):
        if file.is_file() and file.name != "manifest.json":
            assert file.read_bytes().startswith(b"RAWENC1:"), "plaintext file in an encrypted backup"
    assert storage.decrypt_bytes((path / "uploaded_files" / "legacy.txt").read_bytes()) == b"legacy plaintext evidence"
    assert (uploads / "legacy.txt").read_bytes() == b"legacy plaintext evidence"  # the live file is untouched

    assert backup.verify_backup(created["name"]) == {"name": created["name"], "valid": True, "problems": []}


def test_verify_needs_a_key_that_can_decrypt_the_backup(backup_env):
    monkeypatch, _ = backup_env
    old = Fernet.generate_key().decode()
    monkeypatch.setenv("FILE_ENCRYPTION_KEY", old)
    name = backup.create_backup()["name"]

    monkeypatch.setenv("FILE_ENCRYPTION_KEY", Fernet.generate_key().decode())
    refused = backup.verify_backup(name)
    assert not refused["valid"] and any("can't be decrypted" in p for p in refused["problems"])
    assert old not in json.dumps(refused)

    # During a rotation the old key is still accepted for reading.
    monkeypatch.setenv("FILE_ENCRYPTION_PREVIOUS_KEYS", old)
    assert backup.verify_backup(name)["valid"]

    monkeypatch.delenv("FILE_ENCRYPTION_KEY")
    monkeypatch.delenv("FILE_ENCRYPTION_PREVIOUS_KEYS")
    no_key = backup.verify_backup(name)
    assert not no_key["valid"] and any("FILE_ENCRYPTION_KEY is not configured" in p for p in no_key["problems"])


def test_backup_without_a_key_is_plaintext_and_flagged(backup_env, caplog):
    _, uploads = backup_env
    (uploads / "doc.txt").write_bytes(b"evidence")
    with caplog.at_level(logging.WARNING, logger="app.services.backup"):
        created = backup.create_backup()
    assert created["encrypted"] is False
    assert _manifest(created["name"])["database_file"] == "database.sqlite"
    assert "written unencrypted" in caplog.text
    assert backup.verify_backup(created["name"])["valid"]


def test_encrypted_backup_restores_to_a_scratch_database(backup_env, tmp_path):
    monkeypatch, uploads = backup_env
    monkeypatch.setenv("FILE_ENCRYPTION_KEY", Fernet.generate_key().decode())
    (uploads / "doc.txt").write_bytes(storage.encrypt_bytes(b"evidence"))
    name = backup.create_backup()["name"]
    expected_tables = set(_manifest(name)["row_counts"])

    scratch = tmp_path / "restored.db"
    monkeypatch.setattr(backup, "_sqlite_path", lambda: scratch)
    (uploads / "doc.txt").unlink()

    result = backup.restore_backup(name)
    assert result["restored"] == name and result["safety_backup"].startswith("backup-")
    conn = sqlite3.connect(scratch)
    try:
        tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
    finally:
        conn.close()
    assert expected_tables <= tables
    assert storage.read_file(str(uploads / "doc.txt")) == b"evidence"


def test_storage_reads_with_previous_keys_and_writes_with_the_current_one(monkeypatch, tmp_path):
    old, new = Fernet.generate_key().decode(), Fernet.generate_key().decode()
    monkeypatch.setattr(storage, "STORAGE_DIR", str(tmp_path))
    monkeypatch.setenv("FILE_ENCRYPTION_KEY", old)
    monkeypatch.delenv("FILE_ENCRYPTION_PREVIOUS_KEYS", raising=False)
    old_file = storage.save_file(1, "a.txt", b"written under the old key")

    monkeypatch.setenv("FILE_ENCRYPTION_KEY", new)
    with pytest.raises(Exception):
        storage.read_file(old_file)  # the new key alone can't read it
    monkeypatch.setenv("FILE_ENCRYPTION_PREVIOUS_KEYS", f" {old} ,")
    assert storage.read_file(old_file) == b"written under the old key"

    new_file = Path(storage.save_file(1, "b.txt", b"written under the new key")).read_bytes()
    assert Fernet(new.encode()).decrypt(new_file[len(b"RAWENC1:"):]) == b"written under the new key"


def test_previous_keys_are_validated_without_echoing_them(monkeypatch):
    monkeypatch.setenv("FILE_ENCRYPTION_KEY", Fernet.generate_key().decode())
    monkeypatch.setenv("FILE_ENCRYPTION_PREVIOUS_KEYS", f"{Fernet.generate_key().decode()},not-a-key")
    problem = sec.file_key_problem()
    assert problem and "entry 2" in problem and "not-a-key" not in problem
    monkeypatch.setenv("FILE_ENCRYPTION_PREVIOUS_KEYS", Fernet.generate_key().decode())
    assert sec.file_key_problem() is None


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _run(args, env):
    return subprocess.run([sys.executable, ROTATE, *args], env=env, capture_output=True, text=True, timeout=120)


def test_rotation_script_re_encrypts_files_and_backup_manifests(tmp_path):
    old, new, stray = (Fernet.generate_key() for _ in range(3))
    f_old, f_new = Fernet(old), Fernet(new)

    uploads = tmp_path / "uploads"
    uploads.mkdir()
    (uploads / "secret-upload.pdf").write_bytes(b"RAWENC1:" + f_old.encrypt(b"upload"))
    (uploads / "already-new.pdf").write_bytes(b"RAWENC1:" + f_new.encrypt(b"current"))

    app_backup = tmp_path / "backups" / "backup-20261003T000000000000Z"
    app_backup.mkdir(parents=True)
    db = b"RAWENC1:" + f_old.encrypt(b"SQLite format 3 ...")
    (app_backup / "database.sqlite.enc").write_bytes(db)
    (app_backup / "manifest.json").write_text(json.dumps({"files": {"database.sqlite.enc": _sha(db)}}))

    snapshot = tmp_path / "backups" / "staging-pre-0021-x"
    snapshot.mkdir()
    (snapshot / "users.json.enc").write_bytes(f_old.encrypt(b'[{"id": 1}]'))
    snapshot_manifest = json.dumps({"tables": {"users": {"rows": 1}}}).encode()
    (snapshot / "MANIFEST.json").write_bytes(snapshot_manifest)

    env = {**os.environ, "FILE_ENCRYPTION_KEY": new.decode(), "FILE_ENCRYPTION_PREVIOUS_KEYS": old.decode()}
    roots = ["--root", str(uploads), "--root", str(tmp_path / "backups")]

    dry = _run(roots, env)
    assert dry.returncode == 0, dry.stdout + dry.stderr
    assert "1 under the current key, 3 under a previous key, 0 unreadable" in dry.stdout and "Dry run" in dry.stdout
    assert (app_backup / "database.sqlite.enc").read_bytes() == db

    applied = _run([*roots, "--apply"], env)
    assert applied.returncode == 0, applied.stdout + applied.stderr
    assert "Re-encrypted 3 file(s)" in applied.stdout and "updated 1 backup manifest" in applied.stdout
    for secret in ("secret-upload", "users.json", "database.sqlite", old.decode(), new.decode()):
        assert secret not in applied.stdout + applied.stderr

    assert f_new.decrypt((uploads / "secret-upload.pdf").read_bytes()[8:]) == b"upload"
    rotated_db = (app_backup / "database.sqlite.enc").read_bytes()
    assert rotated_db.startswith(b"RAWENC1:") and f_new.decrypt(rotated_db[8:]) == b"SQLite format 3 ..."
    assert json.loads((app_backup / "manifest.json").read_text())["files"]["database.sqlite.enc"] == _sha(rotated_db)
    assert f_new.decrypt((snapshot / "users.json.enc").read_bytes()) == b'[{"id": 1}]'
    assert (snapshot / "MANIFEST.json").read_bytes() == snapshot_manifest  # plaintext hashes: unchanged

    again = _run(roots, env)
    assert "4 under the current key, 0 under a previous key, 0 unreadable" in again.stdout

    # A file no configured key can read is reported, never touched, and fails the run.
    (uploads / "orphan.pdf").write_bytes(b"RAWENC1:" + Fernet(stray).encrypt(b"x"))
    orphan = _run(roots, env)
    assert orphan.returncode == 1 and "1 unreadable" in orphan.stdout and "do NOT retire" in orphan.stdout


def test_verify_encryption_counts_snapshot_tokens_as_encrypted(tmp_path):
    sys.path.insert(0, str(BACKEND / "scripts"))
    try:
        import verify_encryption
    finally:
        sys.path.remove(str(BACKEND / "scripts"))

    f = Fernet(Fernet.generate_key())
    (tmp_path / "snap").mkdir()
    (tmp_path / "snap" / "users.json.enc").write_bytes(f.encrypt(b"[]"))
    (tmp_path / "snap" / "MANIFEST.json").write_text("{}")
    (tmp_path / "app").mkdir()
    (tmp_path / "app" / "database.sqlite.enc").write_bytes(b"RAWENC1:" + f.encrypt(b"db"))
    (tmp_path / "app" / "manifest.json").write_text("{}")
    assert verify_encryption.check_backups(tmp_path) == {
        "directory": tmp_path.name, "files": 2, "encrypted": 2, "plaintext": 0, "metadata_files": 2,
    }
    (tmp_path / "app" / "database.sqlite").write_bytes(b"SQLite format 3")
    assert verify_encryption.check_backups(tmp_path)["plaintext"] == 1

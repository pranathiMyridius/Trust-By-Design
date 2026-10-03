"""
P7 (R19): rotate the file encryption key.

    1. Generate a new key:
         python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
    2. Configure it (secret store / backend/.env), keeping the old one for reading:
         FILE_ENCRYPTION_KEY=<new key>
         FILE_ENCRYPTION_PREVIOUS_KEYS=<old key>[,<older key>...]
       The app now writes with the new key and still reads everything.
    3. python scripts/rotate_file_encryption_key.py            # dry run: counts only
       python scripts/rotate_file_encryption_key.py --apply    # re-encrypt under the new key
    4. When a dry run reports 0 files under a previous key and 0 unreadable,
       remove FILE_ENCRYPTION_PREVIOUS_KEYS. Keep the old key in the secret
       store for as long as any copy made with it (an off-site backup, for
       example) may need restoring.

Covers uploaded_files/ and the backup directory (BACKUP_DIR, default
backups/). Both encrypted forms are handled: RAWENC1-marker files
(uploads, application backups) and raw Fernet-token files (`*.enc` in
logical snapshots). Plaintext and metadata files are left alone.

--apply, for each file still under a previous key: re-encrypts the token
under the current key, checks the new token decrypts with the current key
to exactly the original plaintext, writes it to a temporary file and
atomically replaces the original. A backup folder's manifest.json
checksum for a rotated file is updated the same way, so verify_backup()
still passes. Safe to re-run.

Never prints file names, contents or keys.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]

from dotenv import dotenv_values  # noqa: E402

MARKER = b"RAWENC1:"
FERNET_PREFIX = b"gAAAAA"  # version byte 0x80 + timestamp, base64url
TEMP_SUFFIXES = (".encrypting", ".rotating")
MANIFEST = "manifest.json"


def _env(name: str) -> str | None:
    return os.environ.get(name) or dotenv_values(BACKEND / ".env").get(name)


def _split_token(data: bytes) -> tuple[bytes, bytes] | None:
    """(prefix, token) for an encrypted file, None for plaintext/metadata."""

    if data.startswith(MARKER):
        return MARKER, data[len(MARKER):]
    if data.startswith(FERNET_PREFIX):
        return b"", data.strip()
    return None


def _manifest_for(path: Path, root: Path) -> Path | None:
    """The application-backup manifest that lists `path`, if any."""

    for folder in path.parents:
        candidate = folder / MANIFEST
        if candidate.is_file():
            return candidate
        if folder == root:
            break
    return None


def _write_atomically(path: Path, data: bytes) -> None:
    temp = path.with_name(path.name + ".rotating")
    temp.write_bytes(data)
    os.replace(temp, path)


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--root", action="append", help="directory to process (repeatable); default: uploads and backups")
    args = parser.parse_args(argv)

    current_key = _env("FILE_ENCRYPTION_KEY")
    previous = [k.strip() for k in (_env("FILE_ENCRYPTION_PREVIOUS_KEYS") or "").split(",") if k.strip()]
    if not current_key:
        print("FILE_ENCRYPTION_KEY is not set; nothing can be rotated.")
        return 2

    from cryptography.fernet import Fernet, InvalidToken, MultiFernet

    try:
        current = Fernet(current_key.encode())
        olds = [Fernet(k.encode()) for k in previous]
    except Exception:  # noqa: BLE001 -- never echo the key
        print("FILE_ENCRYPTION_KEY or an entry of FILE_ENCRYPTION_PREVIOUS_KEYS is not a valid Fernet key.")
        return 2
    rotator = MultiFernet([current, *olds]) if olds else None

    if args.root:
        roots = [Path(r) for r in args.root]
    else:
        roots = [BACKEND / "uploaded_files", Path(_env("BACKUP_DIR") or (BACKEND / "backups"))]

    counts = {"files": 0, "current_key": 0, "previous_key": 0, "unreadable": 0, "not_encrypted": 0}
    to_rotate: list[tuple[Path, Path, bytes, bytes]] = []  # (root, path, prefix, token)
    for root in roots:
        if not root.exists():
            continue
        for path in sorted(root.rglob("*")):
            if not path.is_file() or path.name.endswith(TEMP_SUFFIXES):
                continue
            counts["files"] += 1
            split = _split_token(path.read_bytes())
            if split is None:
                counts["not_encrypted"] += 1
                continue
            prefix, token = split
            try:
                current.decrypt(token)
                counts["current_key"] += 1
                continue
            except InvalidToken:
                pass
            if rotator is not None:
                try:
                    rotator.decrypt(token)
                    counts["previous_key"] += 1
                    to_rotate.append((root, path, prefix, token))
                    continue
                except InvalidToken:
                    pass
            counts["unreadable"] += 1

    print(
        f"{counts['files']} file(s): {counts['current_key']} under the current key, "
        f"{counts['previous_key']} under a previous key, {counts['unreadable']} unreadable with the "
        f"configured keys, {counts['not_encrypted']} not encrypted (plaintext or metadata)."
    )
    if counts["unreadable"]:
        print("Some files can't be decrypted with any configured key: do NOT retire an old key until they are explained.")

    if not args.apply:
        print("Dry run: nothing changed. Re-run with --apply to re-encrypt files under the current key.")
        return 1 if counts["unreadable"] else 0

    rotated = 0
    manifests: dict[Path, dict] = {}
    for root, path, prefix, token in to_rotate:
        plaintext = rotator.decrypt(token)
        new_token = rotator.rotate(token)
        if current.decrypt(new_token) != plaintext:
            print("Verification failed for a file; it was left unchanged. Stopping.")
            return 1
        new_data = prefix + new_token
        _write_atomically(path, new_data)
        rotated += 1

        # Application backups list each file's on-disk SHA-256; logical
        # snapshots (MANIFEST.json, plaintext hashes) need no change. Only
        # a manifest that actually lists this file is rewritten.
        manifest_path = _manifest_for(path, root)
        if manifest_path is not None:
            manifest = manifests.get(manifest_path) or json.loads(manifest_path.read_text(encoding="utf-8"))
            relative = str(path.relative_to(manifest_path.parent)).replace("\\", "/")
            files = manifest.get("files")
            if isinstance(files, dict) and relative in files:
                files[relative] = _sha256(new_data)
                manifests[manifest_path] = manifest

    for manifest_path, manifest in manifests.items():
        _write_atomically(manifest_path, json.dumps(manifest, indent=2).encode("utf-8"))

    print(f"Re-encrypted {rotated} file(s) under the current key; updated {len(manifests)} backup manifest(s).")
    if counts["unreadable"] == 0:
        print("Every encrypted file now uses the current key. FILE_ENCRYPTION_PREVIOUS_KEYS can be removed "
              "(keep the old key in the secret store while older off-site copies may need restoring).")
        return 0
    return 1


if __name__ == "__main__":
    sys.exit(main())

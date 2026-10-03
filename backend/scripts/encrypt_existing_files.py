"""
P7 (R19): encrypt uploaded files written before FILE_ENCRYPTION_KEY was set.

    python scripts/encrypt_existing_files.py            # dry run: counts only, changes nothing
    python scripts/encrypt_existing_files.py --apply    # encrypt the plaintext files

--apply, for each plaintext file: writes the encrypted copy to a temporary
file next to it, checks that it decrypts back to exactly the original
bytes, then atomically replaces the original. Files that already carry the
marker are skipped, so it is safe to re-run. Reading back uses
app/file_processing/storage.read_file, which handles both forms. Without
the key nothing can be decrypted afterwards -- keep FILE_ENCRYPTION_KEY
in your secret store and back it up separately from the data.

Never prints file contents, file names or the key.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]

from dotenv import dotenv_values  # noqa: E402

MARKER = b"RAWENC1:"


def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--root", default=str(BACKEND / "uploaded_files"))
    args = parser.parse_args(argv)

    key = os.environ.get("FILE_ENCRYPTION_KEY") or dotenv_values(BACKEND / ".env").get("FILE_ENCRYPTION_KEY")
    if not key:
        print("FILE_ENCRYPTION_KEY is not set; nothing can be encrypted.")
        return 2
    from cryptography.fernet import Fernet

    fernet = Fernet(key.encode())
    root = Path(args.root)
    plaintext = []
    total = 0
    for path in root.rglob("*"):
        if not path.is_file() or path.name.endswith(".encrypting"):
            continue
        total += 1
        with path.open("rb") as handle:
            if handle.read(len(MARKER)) != MARKER:
                plaintext.append(path)
    print(f"{total} file(s); {len(plaintext)} plaintext; {total - len(plaintext)} already encrypted.")
    if not args.apply:
        print("Dry run: nothing changed. Re-run with --apply to encrypt the plaintext files.")
        return 0

    done = 0
    for path in plaintext:
        original = path.read_bytes()
        payload = MARKER + fernet.encrypt(original)
        temporary = path.with_name(path.name + ".encrypting")
        temporary.write_bytes(payload)
        if fernet.decrypt(temporary.read_bytes()[len(MARKER):]) != original:
            temporary.unlink()
            print("Verification failed for one file; stopped without replacing it.")
            return 1
        os.replace(temporary, path)
        done += 1
    print(f"Encrypted {done} file(s).")
    return 0


if __name__ == "__main__":
    sys.exit(main())

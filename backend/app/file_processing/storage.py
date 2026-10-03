import logging
import os
import uuid

from dotenv import load_dotenv

load_dotenv()

STORAGE_DIR = os.path.join(os.getcwd(), "uploaded_files")
os.makedirs(STORAGE_DIR, exist_ok=True)

# Stage 19 (Security -- protection at rest): when FILE_ENCRYPTION_KEY is
# set (a Fernet key: `python -c "from cryptography.fernet import Fernet;
# print(Fernet.generate_key().decode())"`), uploaded evidence files are
# encrypted on disk. Encrypted files carry ENCRYPTED_MARKER as a prefix so
# files written before encryption was switched on still read back fine.
# The key must come from the environment / a secrets manager, never code.
#
# Key rotation: put the new key in FILE_ENCRYPTION_KEY and the old one(s)
# in FILE_ENCRYPTION_PREVIOUS_KEYS (comma-separated). New data is always
# encrypted with FILE_ENCRYPTION_KEY; previous keys are only used to read.
# scripts/rotate_file_encryption_key.py then re-encrypts existing files
# under the new key, after which the previous keys can be retired.
ENCRYPTED_MARKER = b"RAWENC1:"

_logger = logging.getLogger(__name__)


def _key_bytes(key: str | bytes) -> bytes:
    return key.encode() if isinstance(key, str) else key


def previous_keys() -> list[str]:
    raw = os.getenv("FILE_ENCRYPTION_PREVIOUS_KEYS") or ""
    return [k.strip() for k in raw.split(",") if k.strip()]


def _fernet():
    """A MultiFernet: encrypts with FILE_ENCRYPTION_KEY, decrypts with it or
    any previous key. None when no current key is configured."""

    key = os.getenv("FILE_ENCRYPTION_KEY")
    if not key:
        return None

    from cryptography.fernet import Fernet, MultiFernet

    return MultiFernet([Fernet(_key_bytes(k)) for k in [key, *previous_keys()]])


def encryption_enabled() -> bool:
    return bool(os.getenv("FILE_ENCRYPTION_KEY"))


def is_encrypted(data: bytes) -> bool:
    return data.startswith(ENCRYPTED_MARKER)


def encrypt_bytes(data: bytes) -> bytes:
    """Marker + Fernet token under the current key. Raises if no key is set."""

    fernet = _fernet()
    if fernet is None:
        raise RuntimeError("FILE_ENCRYPTION_KEY is not configured; nothing can be encrypted.")
    return ENCRYPTED_MARKER + fernet.encrypt(data)


def decrypt_bytes(data: bytes) -> bytes:
    """The plaintext of marker-encrypted data (any configured key); data
    without the marker is returned unchanged."""

    if not is_encrypted(data):
        return data
    fernet = _fernet()
    if fernet is None:
        raise RuntimeError(
            "This file is encrypted at rest but FILE_ENCRYPTION_KEY is not configured."
        )
    return fernet.decrypt(data[len(ENCRYPTED_MARKER):])


def save_file(assessment_id: int, filename: str, file_content: bytes) -> str:
    # Only the basename of the client-supplied name is kept, so a crafted
    # filename can't write outside STORAGE_DIR.
    base_name = os.path.basename(filename.replace("\\", "/")) or "upload"
    safe_name = f"{assessment_id}_{uuid.uuid4().hex}_{base_name}"
    full_path = os.path.join(STORAGE_DIR, safe_name)

    payload = encrypt_bytes(file_content) if encryption_enabled() else file_content

    with open(full_path, "wb") as f:
        f.write(payload)

    return full_path


def save_source_file(filename: str, file_content: bytes) -> str:
    """An approved-source library document (R5.1), stored like evidence:
    same directory, same encryption at rest, a "source_" prefix."""

    return save_file("source", filename, file_content)  # type: ignore[arg-type]


def read_file(path: str) -> bytes:
    """Reads a stored file, transparently decrypting it if it was saved
    encrypted. Raises RuntimeError if it's encrypted but no key is set."""

    with open(path, "rb") as f:
        return decrypt_bytes(f.read())


def content_disposition(disposition: str, filename: str) -> str:
    """A Content-Disposition header that can't be broken by the stored
    filename: an ASCII fallback with quotes, backslashes and control
    characters removed, plus the RFC 5987 UTF-8 form for the real name."""

    from urllib.parse import quote

    fallback = "".join(
        char if 32 <= ord(char) < 127 and char not in '"\\' else "_" for char in filename
    ) or "document"
    return f"{disposition}; filename=\"{fallback}\"; filename*=UTF-8''{quote(filename, safe='')}"

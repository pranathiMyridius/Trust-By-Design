"""
Source Library document processing: validate a PDF upload, extract its
text page by page, split it into chunks that keep their page and section,
and embed the chunks for semantic retrieval.

Everything here is deterministic apart from the embedding call, which is
best effort: a failure leaves the chunks keyword-searchable and marks the
version's embedding_status, it never fails the upload.
"""

from __future__ import annotations

import hashlib
import io
import logging
import os
import re
from dataclasses import dataclass

from sqlalchemy.orm import Session

from app.ai import embeddings as embeddings_module
from app.ai.embeddings import EMBEDDING_DIM, EmbeddingError, embed_texts
from app.database import IS_POSTGRES
from app.models.source_library import SourceChunk, SourceEmbedding, SourceVersion

logger = logging.getLogger(__name__)

MAX_UPLOAD_BYTES = int(float(os.getenv("SOURCE_MAX_UPLOAD_MB", "25")) * 1024 * 1024)
MAX_PAGES = int(os.getenv("SOURCE_MAX_PDF_PAGES", "1500"))
ALLOWED_CONTENT_TYPES = {"application/pdf", "application/x-pdf", "application/octet-stream", ""}

CHUNK_SIZE_WORDS = 220
CHUNK_OVERLAP_WORDS = 40
EMBED_BATCH = 64


class DocumentError(Exception):
    """A document that can't be accepted or processed, with a stable code
    and a message safe to show the user."""

    def __init__(self, code: str, message: str, status_code: int = 422):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code


@dataclass
class ChunkDraft:
    text: str
    page_start: int | None
    page_end: int | None
    section: str | None


def embeddings_enabled() -> bool:
    """Embeddings are stored and searched on PostgreSQL + pgvector. Elsewhere
    (SQLite: local development and tests) they are off unless
    SOURCE_EMBEDDINGS_ENABLED=true, so a request never waits on the embedding
    provider while holding a database write lock."""

    raw = os.getenv("SOURCE_EMBEDDINGS_ENABLED")
    if raw is not None and raw.strip():
        return raw.strip().lower() in {"1", "true", "yes", "on"}
    return IS_POSTGRES


def clean_text(text: str) -> str:
    # NUL bytes are legal in a PDF string but rejected by PostgreSQL text.
    return (text or "").replace("\x00", "").replace("￾", "").replace("￿", "")


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


# -- validation ------------------------------------------------------------------


def validate_pdf_upload(filename: str, content_type: str | None, data: bytes) -> str:
    """Raises DocumentError unless this is plausibly an acceptable PDF.
    Returns the sanitised display filename."""

    base = os.path.basename((filename or "").replace("\\", "/")).strip()
    base = re.sub(r"[\x00-\x1f]", "", base)[:255]
    if not base or not base.lower().endswith(".pdf"):
        raise DocumentError("UNSUPPORTED_FILE_TYPE", "Only PDF documents (.pdf) can be uploaded.", 400)
    if (content_type or "").split(";")[0].strip().lower() not in ALLOWED_CONTENT_TYPES:
        raise DocumentError("UNSUPPORTED_FILE_TYPE", "The file is not declared as a PDF.", 400)
    if not data:
        raise DocumentError("EMPTY_FILE", "The uploaded file is empty.", 400)
    if len(data) > MAX_UPLOAD_BYTES:
        raise DocumentError(
            "FILE_TOO_LARGE",
            f"{base} is {len(data) / (1024 * 1024):.1f} MB, over the {MAX_UPLOAD_BYTES // (1024 * 1024)} MB limit.",
            413,
        )
    # The extension and declared type are the client's claim; the content decides.
    if b"%PDF-" not in data[:1024]:
        raise DocumentError("NOT_A_PDF", f"{base} is not a valid PDF (it has no PDF header).", 400)
    return base


# -- extraction -------------------------------------------------------------------


def extract_pdf_pages(data: bytes) -> list[str]:
    """The text of each page, in order. Raises DocumentError for an
    unreadable, encrypted or text-less (scanned) PDF."""

    from pypdf import PdfReader
    from pypdf.errors import PdfReadError

    try:
        reader = PdfReader(io.BytesIO(data), strict=False)
        if reader.is_encrypted:
            try:
                if not reader.decrypt(""):
                    raise DocumentError("PDF_ENCRYPTED", "The PDF is password-protected; upload an unprotected copy.")
            except DocumentError:
                raise
            except Exception:  # noqa: BLE001
                raise DocumentError("PDF_ENCRYPTED", "The PDF is password-protected; upload an unprotected copy.")
        page_total = len(reader.pages)
        if page_total == 0:
            raise DocumentError("PDF_EMPTY", "The PDF has no pages.")
        if page_total > MAX_PAGES:
            raise DocumentError("PDF_TOO_LONG", f"The PDF has {page_total} pages; the limit is {MAX_PAGES}.")
        pages: list[str] = []
        for page in reader.pages:
            try:
                pages.append(clean_text(page.extract_text() or ""))
            except Exception:  # noqa: BLE001 -- one bad page must not lose the rest
                logger.warning("A PDF page could not be read; continuing with the others.")
                pages.append("")
    except DocumentError:
        raise
    except (PdfReadError, ValueError, KeyError, OSError, TypeError, RecursionError) as exc:
        raise DocumentError("PDF_UNREADABLE", f"The PDF could not be read ({type(exc).__name__}).") from exc
    except Exception as exc:  # noqa: BLE001 -- parser errors vary widely
        raise DocumentError("PDF_UNREADABLE", f"The PDF could not be read ({type(exc).__name__}).") from exc

    if not any(page.strip() for page in pages):
        raise DocumentError(
            "NO_TEXT_LAYER",
            "No readable text was found. A scanned PDF has no text layer; upload a text-based copy.",
        )
    return pages


# -- sections and chunking --------------------------------------------------------

_NUMBERED_HEADING = re.compile(r"^\d{1,2}(?:\.\d{1,2}){0,3}\.?\s+[A-Z][^.!?;:]{2,100}$")
_KEYWORD_HEADING = re.compile(
    r"^(?:part|chapter|section|article|annex|annexure|appendix|schedule|recommendation|rule|regulation|paragraph)"
    r"\s+[\w.\-()]+(?:\s*[:\-–—]\s*.{0,90})?$",
    re.IGNORECASE,
)
_CAPS_HEADING = re.compile(r"^[A-Z][A-Z0-9 ,&'\-/()]{4,80}$")


def heading_of(line: str) -> str | None:
    """A line that looks like a section heading, else None. Heuristic: PDFs
    carry no structure, so this is best effort."""

    line = " ".join(line.split())
    if not line or len(line) > 110:
        return None
    if _NUMBERED_HEADING.match(line) or _KEYWORD_HEADING.match(line) or _CAPS_HEADING.match(line):
        # A bare page number or short all-caps running header is not a section.
        if re.fullmatch(r"[\d\s.\-]+", line) or len(line) < 5:
            return None
        return line[:255]
    return None


def build_chunks(pages: list[str]) -> list[ChunkDraft]:
    """Word-window chunks, each tagged with the page(s) it spans and the
    section in force where it starts."""

    words: list[tuple[str, int, str | None]] = []
    section: str | None = None
    for page_number, page_text in enumerate(pages, start=1):
        for line in page_text.splitlines():
            heading = heading_of(line)
            if heading:
                section = heading
            for word in line.split():
                words.append((word, page_number, section))
    return _window(words)


def build_plain_chunks(text: str) -> list[ChunkDraft]:
    """Chunks of text with no page structure (legacy and pasted sources)."""

    words: list[tuple[str, int, str | None]] = []
    section: str | None = None
    for line in clean_text(text).splitlines():
        heading = heading_of(line)
        if heading:
            section = heading
        words.extend((word, 0, section) for word in line.split())
    chunks = _window(words)
    for chunk in chunks:
        chunk.page_start = chunk.page_end = None
    return chunks


def _window(words: list[tuple[str, int, str | None]]) -> list[ChunkDraft]:
    """Windows of CHUNK_SIZE_WORDS, never spanning two sections, so a chunk's
    section is the section all of its text belongs to."""

    chunks: list[ChunkDraft] = []
    group: list[tuple[str, int, str | None]] = []
    for item in words:
        if group and item[2] != group[-1][2]:
            chunks.extend(_window_group(group))
            group = []
        group.append(item)
    if group:
        chunks.extend(_window_group(group))
    return chunks


def _window_group(words: list[tuple[str, int, str | None]]) -> list[ChunkDraft]:
    chunks: list[ChunkDraft] = []
    start = 0
    while start < len(words):
        end = min(start + CHUNK_SIZE_WORDS, len(words))
        window = words[start:end]
        chunks.append(
            ChunkDraft(
                text=" ".join(word for word, _, _ in window),
                page_start=window[0][1],
                page_end=window[-1][1],
                section=window[0][2],
            )
        )
        if end >= len(words):
            break
        start = end - CHUNK_OVERLAP_WORDS
    return chunks


# -- persistence -------------------------------------------------------------------


def replace_chunks(db: Session, version: SourceVersion, drafts: list[ChunkDraft]) -> list[SourceChunk]:
    """Replaces a version's chunks (and their embeddings) with `drafts`."""

    delete_chunks(db, version)
    rows = []
    for index, draft in enumerate(drafts):
        rows.append(
            SourceChunk(
                version_id=version.id,
                record_id=version.record_id,
                chunk_index=index,
                text=draft.text,
                page_start=draft.page_start,
                page_end=draft.page_end,
                section=draft.section,
                text_sha256=sha256_hex(draft.text.encode("utf-8")),
            )
        )
    db.add_all(rows)
    db.flush()
    version.chunk_count = len(rows)
    version.embedding_status = "NONE"
    return rows


def delete_chunks(db: Session, version: SourceVersion) -> None:
    chunk_ids = [row[0] for row in db.query(SourceChunk.id).filter(SourceChunk.version_id == version.id).all()]
    if chunk_ids:
        db.query(SourceEmbedding).filter(SourceEmbedding.chunk_id.in_(chunk_ids)).delete(synchronize_session=False)
        db.query(SourceChunk).filter(SourceChunk.version_id == version.id).delete(synchronize_session=False)
    version.chunk_count = 0
    version.embedding_status = "NONE"


def embed_chunks(db: Session, version: SourceVersion, chunks: list[SourceChunk]) -> str:
    """Embeds `chunks` and stores the vectors. Returns (and records) the
    version's embedding_status: COMPLETE, PARTIAL, UNAVAILABLE or NONE."""

    if not chunks or not embeddings_enabled():
        version.embedding_status = "NONE"
        return "NONE"

    stored = 0
    for offset in range(0, len(chunks), EMBED_BATCH):
        batch = chunks[offset : offset + EMBED_BATCH]
        try:
            vectors = embed_texts([chunk.text for chunk in batch])
        except EmbeddingError as exc:
            logger.warning("Embedding unavailable for source version %s: %s", version.id, exc)
            break
        if len(vectors) != len(batch) or any(len(vector) != EMBEDDING_DIM for vector in vectors):
            logger.warning("Embedding service returned vectors of an unexpected shape for version %s.", version.id)
            break
        for chunk, vector in zip(batch, vectors):
            db.add(
                SourceEmbedding(
                    chunk_id=chunk.id,
                    model=embeddings_module.OPENROUTER_EMBEDDING_MODEL,
                    dimensions=len(vector),
                    embedding=[float(value) for value in vector],
                )
            )
            stored += 1
    status = "COMPLETE" if stored == len(chunks) else ("PARTIAL" if stored else "UNAVAILABLE")
    version.embedding_status = status
    return status


def process_pdf(db: Session, version: SourceVersion, data: bytes) -> None:
    """Extracts, chunks and embeds `data` for `version`. Never raises for a
    bad document: the outcome is recorded on the version (processing_status
    FAILED with a message) so the upload can be corrected."""

    version.processing_status = "PENDING"
    version.processing_error = None
    try:
        pages = extract_pdf_pages(data)
    except DocumentError as exc:
        delete_chunks(db, version)
        version.processing_status = "FAILED"
        version.processing_error = exc.message
        version.extracted_text = None
        version.page_count = None
        return

    drafts = build_chunks(pages)
    full_text = "\n\n".join(f"[Page {number}]\n{text}" for number, text in enumerate(pages, start=1) if text.strip())
    version.extracted_text = full_text
    version.text_sha256 = sha256_hex(full_text.encode("utf-8"))
    version.page_count = len(pages)
    chunks = replace_chunks(db, version, drafts)
    embed_chunks(db, version, chunks)
    version.processing_status = "PROCESSED"


def ensure_chunks(db: Session, version: SourceVersion) -> bool:
    """Chunks (and tries to embed) a text-only version that has none yet --
    sources migrated from the earlier library. Returns True if it created
    any."""

    if version.chunk_count or not (version.extracted_text or "").strip():
        return False
    chunks = replace_chunks(db, version, build_plain_chunks(version.extracted_text or ""))
    embed_chunks(db, version, chunks)
    return bool(chunks)


__all__ = [
    "ChunkDraft",
    "DocumentError",
    "build_chunks",
    "build_plain_chunks",
    "ensure_chunks",
    "process_pdf",
    "sha256_hex",
    "validate_pdf_upload",
]

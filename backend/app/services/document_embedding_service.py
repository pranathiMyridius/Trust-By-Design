"""
Indexes assessment text (uploaded document extracts, or the assessment's
own description/evidence when there's no document) into pgvector so it
can be semantically searched later -- see semantic_risk_search.py. Only
does anything when running against Postgres (app.database.IS_POSTGRES);
callers should check that themselves before calling in, since indexing
requires the document_embeddings table to exist.
"""

import logging

from sqlalchemy.orm import Session

from app.ai.embeddings import EmbeddingError, embed_texts
from app.ai.metering import ai_assessment_context
from app.models.document_embedding import DocumentEmbedding

logger = logging.getLogger(__name__)

# Word-based chunking (not token-based) -- simple, dependency-free, and
# good enough for the business documents this app handles. Overlap keeps
# a sentence that straddles a chunk boundary searchable from either side.
CHUNK_SIZE_WORDS = 220
CHUNK_OVERLAP_WORDS = 40


def chunk_text(text: str) -> list[str]:
    words = text.split()

    if not words:
        return []

    chunks = []
    start = 0

    while start < len(words):
        end = start + CHUNK_SIZE_WORDS
        chunks.append(" ".join(words[start:end]))

        if end >= len(words):
            break

        start = end - CHUNK_OVERLAP_WORDS

    return chunks


def index_text(
    db: Session,
    assessment_id: int,
    text: str,
    document_id: int | None = None,
) -> int:
    """
    Chunks and embeds `text`, replacing any previously-indexed chunks for
    the same (assessment_id, document_id) pair -- so re-uploading a
    corrected version of a document doesn't leave stale chunks behind.
    Returns the number of chunks indexed. Swallows embedding failures
    (logs and returns 0) rather than raising, so a document upload never
    fails just because semantic indexing couldn't reach OpenRouter --
    this is a search-quality feature, not a correctness-critical one.
    """

    chunks = chunk_text(text or "")

    if not chunks:
        return 0

    db.query(DocumentEmbedding).filter(
        DocumentEmbedding.assessment_id == assessment_id,
        DocumentEmbedding.document_id == document_id,
    ).delete(synchronize_session=False)

    try:
        with ai_assessment_context(assessment_id):
            vectors = embed_texts(chunks)
    except EmbeddingError as exc:
        logger.warning(
            "Skipping semantic indexing for assessment %s (document %s): %s",
            assessment_id,
            document_id,
            exc,
        )
        return 0

    for index, (chunk, vector) in enumerate(zip(chunks, vectors)):
        db.add(
            DocumentEmbedding(
                assessment_id=assessment_id,
                document_id=document_id,
                chunk_index=index,
                chunk_text=chunk,
                embedding=vector,
            )
        )

    return len(chunks)

"""
Vector search over previously-indexed assessment evidence (pgvector),
used to surface how similar past business changes were risk-assessed --
context the AI risk engine (app/ai/risk_factor_analyzer.py) and human
reviewers can use when identifying risks for a new assessment.
"""

import logging

from sqlalchemy.orm import Session

from app.ai.embeddings import EmbeddingError, embed_texts
from app.models.assessment import Assessment
from app.models.document_embedding import DocumentEmbedding

logger = logging.getLogger(__name__)


def find_similar_risk_context(
    db: Session,
    query_text: str,
    exclude_assessment_id: int | None = None,
    limit: int = 5,
) -> list[dict]:
    """
    Returns up to `limit` chunks from *other* assessments' indexed
    evidence that are semantically closest to `query_text`, each paired
    with that source assessment's id/title/risk outcome -- e.g. "this
    reads like assessment #14 (Fleet Platform Expansion), which was
    rated HIGH". Ordered nearest-first by cosine distance. Returns []
    (rather than raising) if embedding the query fails, so this is
    always safe to call as a best-effort enrichment step.
    """

    query_text = (query_text or "").strip()

    if not query_text:
        return []

    try:
        [query_vector] = embed_texts([query_text])
    except EmbeddingError as exc:
        logger.warning("Semantic search skipped: %s", exc)
        return []

    query = (
        db.query(
            DocumentEmbedding,
            Assessment,
            DocumentEmbedding.embedding.cosine_distance(query_vector).label(
                "distance"
            ),
        )
        .join(Assessment, Assessment.id == DocumentEmbedding.assessment_id)
    )

    if exclude_assessment_id is not None:
        query = query.filter(
            DocumentEmbedding.assessment_id != exclude_assessment_id
        )

    rows = query.order_by("distance").limit(limit).all()

    return [
        {
            "assessment_id": assessment.id,
            "assessment_title": assessment.title,
            "assessment_reference_id": assessment.reference_id,
            "risk_level": assessment.risk_level,
            "overall_score": assessment.overall_score,
            "chunk_text": chunk.chunk_text,
            # Cosine distance is in [0, 2]; expose the more intuitive
            # similarity (1 - distance, clamped) instead.
            "similarity": max(0.0, min(1.0, 1 - float(distance))),
        }
        for chunk, assessment, distance in rows
    ]

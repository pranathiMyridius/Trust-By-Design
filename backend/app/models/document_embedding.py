from datetime import datetime, timezone

from pgvector.sqlalchemy import Vector
from sqlalchemy import Column, DateTime, ForeignKey, Integer, Text

from app.ai.embeddings import EMBEDDING_DIM
from app.database import Base


class DocumentEmbedding(Base):
    """
    Semantic-search index over uploaded evidence documents (Postgres +
    pgvector only -- see app/database.py's IS_POSTGRES and
    docker-compose.yml). Each row is one chunk of a document's extracted
    text plus its embedding vector, so a new assessment's evidence can be
    compared against previously-assessed evidence to surface similar
    past risks during Risk Identification (see
    app/services/semantic_risk_search.py).
    """

    __tablename__ = "document_embeddings"

    id = Column(Integer, primary_key=True, index=True)

    assessment_id = Column(
        Integer,
        ForeignKey("assessments.id"),
        nullable=False,
        index=True,
    )

    # Nullable: not every embedded chunk comes from an uploaded file --
    # the assessment's own description/evidence text is indexed too, so
    # it can also be found as "similar past context" for a later
    # assessment even when no document was attached.
    document_id = Column(
        Integer,
        ForeignKey("assessment_documents.id"),
        nullable=True,
        index=True,
    )

    chunk_index = Column(Integer, nullable=False)
    chunk_text = Column(Text, nullable=False)

    embedding = Column(Vector(EMBEDDING_DIM), nullable=False)

    created_at = Column(
        DateTime,
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )

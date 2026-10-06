"""
Retrieval of approved Source Library passages for risk assessments.

What may be returned (all of it enforced here, in the query):

* the chunk's version is APPROVED -- never a draft, in-review, rejected,
  superseded or retired version, and never a newer version that has not
  been approved (the approved one stays in force until its successor is);
* the record is ACTIVE;
* the version is already in effect (effective_date is not in the future);
* the record's jurisdiction applies: it is unspecified/global, the home
  jurisdiction (app/governance/policy.py "source_library"), or one the
  assessment names (a member state also matches "European Union");
* the optional category / topic filters.

Ranking is semantic (pgvector cosine distance on Postgres; the same
cosine computed in Python on SQLite) when the query can be embedded and
the candidates have vectors; otherwise a transparent keyword score. A
passage is returned with everything needed to cite it: source ID and
title, authority, version, page and section, the text itself, and whether
the source is past its review date.
"""

from __future__ import annotations

import logging
import math
import re
import uuid
from dataclasses import dataclass, field
from datetime import date
from typing import Any

from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.ai.embeddings import EmbeddingError, embed_texts
from app.database import IS_POSTGRES
from app.models.source_library import SourceChunk, SourceEmbedding, SourceRecord, SourceVersion
from app.reference_data.country_normalizer import resolve_country
from app.services import source_documents, source_governance
from app.services.source_library import _terms

logger = logging.getLogger(__name__)

GLOBAL_JURISDICTIONS = {"", "global", "international", "worldwide", "all"}
EU_MEMBERS = {
    "AT", "BE", "BG", "HR", "CY", "CZ", "DK", "EE", "FI", "FR", "DE", "GR", "HU", "IE", "IT", "LV",
    "LT", "LU", "MT", "NL", "PL", "PT", "RO", "SK", "SI", "ES", "SE",
}
_ALIASES = {
    "european union": "EU", "eu": "EU", "uk": "GB", "united kingdom": "GB", "great britain": "GB",
    "us": "US", "usa": "US", "united states": "US", "u.s.": "US",
}
MAX_CANDIDATES = 4000


@dataclass
class Passage:
    chunk_id: uuid.UUID
    record_id: uuid.UUID
    version_id: uuid.UUID
    source_code: str
    title: str
    authority: str
    category: str
    jurisdiction: str | None
    version_label: str
    version_number: int
    effective_date: date | None
    review_date: date | None
    outdated: bool
    source_url: str | None
    page_start: int | None
    page_end: int | None
    section: str | None
    text: str
    text_sha256: str
    score: float
    method: str
    topics: list[str] = field(default_factory=list)

    def location(self) -> str:
        parts = []
        if self.page_start:
            parts.append(f"p. {self.page_start}" if self.page_start == self.page_end else f"pp. {self.page_start}-{self.page_end}")
        if self.section:
            parts.append(self.section)
        return ", ".join(parts)


def _code(value: str | None) -> str | None:
    """A jurisdiction label -> a comparable code ('IN', 'EU', ...)."""

    text = (value or "").strip()
    if not text or text.lower() in GLOBAL_JURISDICTIONS:
        return None
    alias = _ALIASES.get(text.lower())
    if alias:
        return alias
    return resolve_country(text) or text.lower()


def applicable_jurisdictions(countries_text: str | None) -> set[str]:
    """Codes of the jurisdictions an assessment touches, plus the home
    jurisdiction; 'EU' is added for any EU member state."""

    codes: set[str] = set()
    home = _code(source_governance.home_jurisdiction())
    if home:
        codes.add(home)
    for part in re.split(r"[,;/\n]| and ", countries_text or ""):
        code = _code(part)
        if code:
            codes.add(code)
    if codes & EU_MEMBERS:
        codes.add("EU")
    return codes


def jurisdiction_applies(source_jurisdiction: str | None, applicable: set[str]) -> bool:
    code = _code(source_jurisdiction)
    return code is None or code in applicable


def _cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    norm = math.sqrt(sum(x * x for x in a)) * math.sqrt(sum(y * y for y in b))
    return dot / norm if norm else 0.0


def approved_versions_query(db: Session, today: date | None = None):
    today = today or date.today()
    return (
        db.query(SourceVersion, SourceRecord)
        .join(SourceRecord, SourceRecord.id == SourceVersion.record_id)
        .filter(
            SourceVersion.status == "APPROVED",
            SourceRecord.status == "ACTIVE",
            or_(SourceVersion.effective_date.is_(None), SourceVersion.effective_date <= today),
        )
    )


def list_approved(
    db: Session,
    *,
    jurisdictions: set[str] | None = None,
    categories: list[str] | None = None,
    topic: str | None = None,
    today: date | None = None,
) -> list[tuple[SourceVersion, SourceRecord]]:
    """Approved, in-effect (version, record) pairs matching the filters."""

    query = approved_versions_query(db, today)
    if categories:
        query = query.filter(SourceRecord.category.in_(categories))
    rows = query.order_by(SourceRecord.title).all()
    result = []
    for version, record in rows:
        if jurisdictions is not None and not jurisdiction_applies(record.jurisdiction, jurisdictions):
            continue
        if topic and topic.lower() not in {t.lower() for t in (record.topics or [])}:
            continue
        result.append((version, record))
    return result


def retrieve(
    db: Session,
    query_text: str,
    *,
    jurisdictions: set[str] | None = None,
    categories: list[str] | None = None,
    topics: list[str] | None = None,
    limit: int = 8,
    today: date | None = None,
    use_embeddings: bool = True,
) -> list[Passage]:
    """Ranked passages from approved, applicable source versions."""

    pairs = list_approved(db, jurisdictions=jurisdictions, categories=categories, today=today)
    if not pairs:
        return []
    by_version = {version.id: (version, record) for version, record in pairs}

    # Migrated, text-only versions are chunked the first time they're needed.
    created = False
    for version, _ in pairs:
        if version.chunk_count == 0 and (version.extracted_text or "").strip():
            created = source_documents.ensure_chunks(db, version) or created
    if created:
        db.commit()

    chunks = (
        db.query(SourceChunk)
        .filter(SourceChunk.version_id.in_(list(by_version)))
        .order_by(SourceChunk.version_id, SourceChunk.chunk_index)
        .limit(MAX_CANDIDATES)
        .all()
    )
    if not chunks:
        return []

    wanted_topics = {t.lower() for t in topics or []}
    scores, method = _vector_scores(db, query_text, chunks, use_embeddings)
    if not scores:
        scores, method = _keyword_scores(query_text, chunks), "keyword"

    passages: list[Passage] = []
    for chunk in chunks:
        raw = scores.get(chunk.id)
        if raw is None:
            continue
        version, record = by_version[chunk.version_id]
        boost = 0.05 * len(wanted_topics & {t.lower() for t in (record.topics or [])})
        passages.append(
            Passage(
                chunk_id=chunk.id,
                record_id=record.id,
                version_id=version.id,
                source_code=record.source_code,
                title=record.title,
                authority=record.authority,
                category=record.category,
                jurisdiction=record.jurisdiction,
                version_label=version.version_label,
                version_number=version.version_number,
                effective_date=version.effective_date,
                review_date=version.review_date,
                outdated=source_governance.is_outdated(version, today),
                source_url=version.source_url or record.source_url,
                page_start=chunk.page_start,
                page_end=chunk.page_end,
                section=chunk.section,
                text=chunk.text,
                text_sha256=chunk.text_sha256,
                score=round(raw + boost, 4),
                method=method,
                topics=list(record.topics or []),
            )
        )
    passages.sort(key=lambda p: (-p.score, p.source_code, p.page_start or 0))
    return passages[: max(1, limit)]


def _keyword_scores(query_text: str, chunks: list[SourceChunk]) -> dict[uuid.UUID, float]:
    terms = set(_terms(query_text))
    if not terms:
        return {}
    chunk_terms = {chunk.id: set(_terms(chunk.text)) for chunk in chunks}
    total = len(chunks)
    idf = {
        term: math.log(1 + total / (1 + sum(1 for t in chunk_terms.values() if term in t)))
        for term in terms
    }
    scores = {}
    for chunk in chunks:
        matched = terms & chunk_terms[chunk.id]
        if matched:
            scores[chunk.id] = sum(idf[term] for term in matched)
    return scores


def _vector_scores(
    db: Session, query_text: str, chunks: list[SourceChunk], use_embeddings: bool
) -> tuple[dict[uuid.UUID, float], str]:
    if not use_embeddings or not source_documents.embeddings_enabled() or not (query_text or "").strip():
        return {}, "keyword"
    chunk_ids = [chunk.id for chunk in chunks]
    if not db.query(SourceEmbedding.id).filter(SourceEmbedding.chunk_id.in_(chunk_ids)).first():
        return {}, "keyword"
    try:
        query_vector = embed_texts([query_text[:8000]])[0]
    except (EmbeddingError, IndexError) as exc:
        logger.info("Query embedding unavailable (%s); using keyword retrieval.", exc)
        return {}, "keyword"

    if IS_POSTGRES:
        distance = SourceEmbedding.embedding.cosine_distance(query_vector)
        rows = (
            db.query(SourceEmbedding.chunk_id, distance)
            .filter(SourceEmbedding.chunk_id.in_(chunk_ids))
            .all()
        )
        return {chunk_id: 1.0 - float(dist) for chunk_id, dist in rows}, "vector"

    rows = db.query(SourceEmbedding.chunk_id, SourceEmbedding.embedding).filter(SourceEmbedding.chunk_id.in_(chunk_ids)).all()
    return {chunk_id: _cosine(query_vector, list(vector)) for chunk_id, vector in rows}, "vector"


def passage_dict(passage: Passage, *, include_text: bool = True) -> dict[str, Any]:
    data = {
        "chunk_id": str(passage.chunk_id),
        "record_id": str(passage.record_id),
        "version_id": str(passage.version_id),
        "source_code": passage.source_code,
        "title": passage.title,
        "authority": passage.authority,
        "category": passage.category,
        "jurisdiction": passage.jurisdiction,
        "version_label": passage.version_label,
        "effective_date": passage.effective_date.isoformat() if passage.effective_date else None,
        "review_date": passage.review_date.isoformat() if passage.review_date else None,
        "outdated": passage.outdated,
        "source_url": passage.source_url,
        "page_start": passage.page_start,
        "page_end": passage.page_end,
        "section": passage.section,
        "location": passage.location(),
        "score": passage.score,
        "method": passage.method,
    }
    if include_text:
        data["text"] = passage.text
    return data

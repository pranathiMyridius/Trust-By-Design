"""
R5.1-R5.5: search of the approved evidence-source library.

A transparent keyword search -- not semantic matching -- so every result
can be explained: the source text is split into passages (paragraphs,
long ones into sentences), each scored by how many distinct query terms
it contains, weighted by how rare the term is across the library. Only
APPROVED sources are searched (R5.2). Every result carries the source
name, version, effective date, reference and retrieval time (R5.3) and
says whether the source is past its review date (Stage 5 acceptance
criteria: outdated policy evidence warns).
"""

from __future__ import annotations

import math
import re
from datetime import date, datetime, timezone

from sqlalchemy.orm import Session

from app.models.approved_source import ApprovedSource

SOURCE_TYPES = {
    "INTERNAL_POLICY": "Internal policy",
    "PROCEDURE": "Procedure",
    "CONTROL_STANDARD": "Control standard",
    "REGULATORY_GUIDANCE": "Regulatory guidance",
    "RISK_FRAMEWORK": "Approved risk framework",
    "PREVIOUS_ASSESSMENT": "Previous assessment",
    "VENDOR_CONTROL_DOCUMENTATION": "Vendor-control documentation",
}
SOURCE_STATUSES = {"DRAFT", "APPROVED", "RETIRED"}

# Search terms implied by each risk category, so a factor finds its
# policies even when its rationale uses different words.
CATEGORY_TERMS = {
    "PRODUCT_SERVICE_RISK": ["product", "service", "new product approval"],
    "CUSTOMER_SEGMENT_RISK": ["customer", "due diligence", "kyc", "edd", "segment"],
    "GEOGRAPHIC_RISK": ["country", "jurisdiction", "high-risk third country", "cross-border", "geographic"],
    "DELIVERY_CHANNEL_RISK": ["channel", "remote", "non-face-to-face", "onboarding", "authentication"],
    "TRANSACTION_ACTIVITY_RISK": ["transaction", "monitoring", "limits", "payment"],
    "TECHNOLOGY_DEVELOPMENT_RISK": ["technology", "new technology", "platform", "digital"],
    "THIRD_PARTY_VENDOR_RISK": ["third party", "vendor", "outsourcing", "processor"],
    "OWNERSHIP_ENTITY_COMPLEXITY_RISK": ["beneficial owner", "ownership", "legal entity", "ubo"],
    "FINANCIAL_CRIME_TYPOLOGY_RISK": ["money laundering", "terrorist financing", "fraud", "typology", "sanctions"],
    "CONTROL_ENVIRONMENT_RISK": ["control", "governance", "assurance"],
}

_STOPWORDS = {
    "a", "an", "and", "are", "as", "at", "be", "by", "for", "from", "has", "have", "in", "is", "it",
    "its", "of", "on", "or", "that", "the", "this", "to", "was", "were", "will", "with", "which",
    "risk", "risks", "may", "can", "should", "must", "not", "any", "all", "such", "their",
}

_MAX_PASSAGE = 600


def _terms(text: str) -> list[str]:
    words = re.findall(r"[a-z0-9][a-z0-9\-]+", (text or "").lower())
    return [word for word in words if word not in _STOPWORDS and len(word) > 2]


def _passages(content: str) -> list[str]:
    passages = []
    for paragraph in re.split(r"\n\s*\n", content or ""):
        paragraph = " ".join(paragraph.split())
        if not paragraph:
            continue
        if len(paragraph) <= _MAX_PASSAGE:
            passages.append(paragraph)
            continue
        current = ""
        for sentence in re.split(r"(?<=[.!?])\s+", paragraph):
            if current and len(current) + len(sentence) > _MAX_PASSAGE:
                passages.append(current)
                current = sentence
            else:
                current = f"{current} {sentence}".strip()
        if current:
            passages.append(current)
    return passages


def is_outdated(source: ApprovedSource, today: date | None = None) -> bool:
    return bool(source.review_date and source.review_date < (today or date.today()))


def search(
    db: Session,
    query: str,
    *,
    extra_phrases: list[str] | None = None,
    source_types: list[str] | None = None,
    limit: int = 8,
) -> list[dict]:
    """Ranked passages from APPROVED sources matching `query` (and any
    extra phrases), best first."""

    phrases = [phrase.lower() for phrase in extra_phrases or [] if phrase]
    query_terms = set(_terms(query)) | {term for phrase in phrases for term in _terms(phrase)}
    if not query_terms and not phrases:
        return []

    sources_query = db.query(ApprovedSource).filter(ApprovedSource.status == "APPROVED")
    if source_types:
        sources_query = sources_query.filter(ApprovedSource.source_type.in_(source_types))
    sources = sources_query.all()
    if not sources:
        return []

    candidates = [(source, passage) for source in sources for passage in _passages(source.content)]
    if not candidates:
        return []

    # Rarer terms count for more (inverse document frequency over passages).
    passage_terms = [set(_terms(passage)) for _, passage in candidates]
    total = len(candidates)
    idf = {
        term: math.log(1 + total / (1 + sum(1 for terms in passage_terms if term in terms)))
        for term in query_terms
    }

    retrieved_at = datetime.now(timezone.utc)
    results = []
    for (source, passage), terms in zip(candidates, passage_terms):
        matched = sorted(term for term in query_terms if term in terms)
        lowered = passage.lower()
        matched_phrases = [phrase for phrase in phrases if " " in phrase and phrase in lowered]
        if not matched and not matched_phrases:
            continue
        score = sum(idf[term] for term in matched) + 2.0 * len(matched_phrases)
        results.append(
            {
                "source_id": source.id,
                "source_title": source.title,
                "source_type": source.source_type,
                "source_type_label": SOURCE_TYPES.get(source.source_type, source.source_type),
                "issuer": source.issuer,
                "source_version": source.version,
                "effective_date": source.effective_date,
                "review_date": source.review_date,
                "reference": source.reference,
                "passage": passage,
                "matched_terms": matched + matched_phrases,
                "score": round(score, 3),
                "outdated": is_outdated(source),
                "retrieved_at": retrieved_at,
            }
        )

    results.sort(key=lambda row: (-row["score"], row["source_title"]))
    return results[:limit]


def search_for_factor(db: Session, factor, limit: int = 5) -> list[dict]:
    """R5.1: passages relevant to one identified risk factor, from its
    category, indicators and rationale."""

    indicators = [indicator.replace("_", " ").lower() for indicator in (factor.get_indicators() or [])]
    query = " ".join([factor.rationale or "", factor.misuse_scenario or "", " ".join(indicators)])
    return search(
        db,
        query,
        extra_phrases=CATEGORY_TERMS.get(factor.category, []) + indicators,
        limit=limit,
    )

"""
Deterministic evidence verification for AI-identified risk factors.

The model is asked to back every applicable factor, and every indicator
it asserts, with a verbatim quote from a named source (an intake field
of the assessment, or an uploaded document). Nothing the model says is
trusted on its own: this module checks each quote against the source
text itself and decides, by plain string matching, which findings stand.

    quote found in the named source   -> EXACT_VERIFIED, may support the factor
    quote not found / source unknown  -> rejected, supports nothing
    indicator with no verified quote  -> rejected, never stored on the factor

A factor's evidence_status is derived here too, never taken from the
model:

    NOT_APPLICABLE         the model found the category does not apply
    EVIDENCE_FOUND         applicable, with at least one verified quote
    CONFLICTING_EVIDENCE   applicable, verified quotes, model flagged conflict
    NOT_VERIFIED           applicable, quotes given but none verified
    INSUFFICIENT_EVIDENCE  applicable, no quotes at all

None of these is a rating. Every AI factor starts unrated; an analyst's
likelihood x impact rating is the only thing that scores it (see
app/risk_engine/scoring.py::calculate_inherent_risk), so an unresolved
factor keeps the assessment provisional instead of reading as low risk.

Location anchors are limited to what the extractor reliably produces
(app/file_processing/extractor.py emits plain text, with "[Page N]"
markers for PDFs only): source id, document id/version, a SHA-256 of the
normalized source text the quote was checked against, the offset within
that normalized text, and the PDF page where a marker precedes it.
"""

import hashlib
import re
import unicodedata
from typing import Any, Iterable

from app.schemas.risk_factor import RISK_INDICATORS


class EvidenceStatus:
    EVIDENCE_FOUND = "EVIDENCE_FOUND"
    INSUFFICIENT_EVIDENCE = "INSUFFICIENT_EVIDENCE"
    CONFLICTING_EVIDENCE = "CONFLICTING_EVIDENCE"
    NOT_VERIFIED = "NOT_VERIFIED"
    NOT_APPLICABLE = "NOT_APPLICABLE"


# Statuses that leave a factor unresolved: it needs a human before it can
# be relied on, and it must never be treated as a low-risk reading.
UNRESOLVED_STATUSES = {
    EvidenceStatus.INSUFFICIENT_EVIDENCE,
    EvidenceStatus.CONFLICTING_EVIDENCE,
    EvidenceStatus.NOT_VERIFIED,
}


class QuoteVerification:
    EXACT_VERIFIED = "EXACT_VERIFIED"
    NOT_FOUND = "NOT_FOUND"
    UNKNOWN_SOURCE = "UNKNOWN_SOURCE"
    TOO_SHORT = "TOO_SHORT"


# A quote shorter than this proves nothing: "cash" or "Germany" appears
# in plenty of texts that say nothing risky about either.
MIN_QUOTE_CHARS = 12

# Intake fields the model may quote from, as FIELD:<name> sources.
ASSESSMENT_SOURCE_FIELDS = [
    "title",
    "description",
    "evidence",
    "product_or_service_name",
    "customer_segment",
    "countries_jurisdictions",
    "delivery_channels",
    "expected_transaction_volume",
    "expected_transaction_value",
    "transaction_types",
    "third_party_vendor_usage",
    "technology_process_changes",
]

_QUOTE_CHARS = {
    "‘": "'", "’": "'", "‚": "'", "‛": "'",
    "“": '"', "”": '"', "„": '"', "‟": '"',
    "–": "-", "—": "-", "−": "-",
    " ": " ",
}

def normalize_text(text: str) -> str:
    """
    Normalization applied identically to source and quote before
    matching: Unicode NFKC, typographic quotes/dashes folded to ASCII,
    whitespace runs collapsed, case folded. Deliberately nothing looser:
    no stemming, no fuzzy matching, so a "verified" quote really is in
    the source.
    """

    text = unicodedata.normalize("NFKC", text or "")
    text = "".join(_QUOTE_CHARS.get(char, char) for char in text)
    text = re.sub(r"\s+", " ", text)
    return text.strip().casefold()


def _sha256(text: str) -> str:
    return "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest()


def build_evidence_sources(
    assessment: dict[str, Any],
    documents: Iterable[Any] = (),
) -> dict[str, dict[str, Any]]:
    """
    Every source a quote may be checked against, keyed by the source id
    the model is told to cite: FIELD:<name> for intake fields, DOC:<id>
    for current uploaded documents.
    """

    sources: dict[str, dict[str, Any]] = {}

    for field in ASSESSMENT_SOURCE_FIELDS:
        value = assessment.get(field)
        if value and str(value).strip():
            sources[f"FIELD:{field}"] = {
                "source_type": "ASSESSMENT_FIELD",
                "label": field,
                "text": str(value),
                "document_id": None,
                "document_version": None,
            }

    for document in documents:
        text = getattr(document, "extracted_text", None) or ""
        if not text.strip():
            continue
        sources[f"DOC:{document.id}"] = {
            "source_type": "DOCUMENT",
            "label": getattr(document, "filename", None) or f"Document {document.id}",
            "text": text,
            "document_id": document.id,
            "document_version": getattr(document, "version", None),
        }

    for source in sources.values():
        source["normalized"] = normalize_text(source["text"])
        source["checksum"] = _sha256(source["normalized"])

    return sources


def _page_at(raw_text: str, normalized_offset: int, normalized_text: str) -> int | None:
    """
    The PDF page a match falls on, read from the nearest "[Page N]"
    marker before it. Markers survive normalization as "[page n]", so
    they are located in the same normalized text the offset refers to.
    """

    if "[Page " not in raw_text:
        return None

    page = None
    for match in re.finditer(r"\[page (\d+)\]", normalized_text):
        if match.start() > normalized_offset:
            break
        page = int(match.group(1))
    return page


def verify_quote(
    source_id: str,
    quote: str,
    sources: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    """Checks one quote against one named source. Pure string matching."""

    source = sources.get(source_id)
    normalized_quote = normalize_text(quote)

    record: dict[str, Any] = {
        "source_id": source_id,
        "source_type": source["source_type"] if source else None,
        "source_label": source["label"] if source else None,
        "document_id": source["document_id"] if source else None,
        "document_version": source["document_version"] if source else None,
        "source_checksum": source["checksum"] if source else None,
        "verbatim_quote": quote,
        "normalized_offset": None,
        "page": None,
        "verification": None,
        "quote_verified": False,
    }

    if source is None:
        record["verification"] = QuoteVerification.UNKNOWN_SOURCE
        return record

    if len(normalized_quote) < MIN_QUOTE_CHARS:
        record["verification"] = QuoteVerification.TOO_SHORT
        return record

    offset = source["normalized"].find(normalized_quote)
    if offset == -1:
        record["verification"] = QuoteVerification.NOT_FOUND
        return record

    record["verification"] = QuoteVerification.EXACT_VERIFIED
    record["quote_verified"] = True
    record["normalized_offset"] = offset
    record["page"] = _page_at(source["text"], offset, source["normalized"])
    return record


# Indicators that decide an outcome on their own: a verified
# SANCTIONS_EXPOSURE forces CRITICAL (SANCTIONS_EXPOSURE_001 in
# app/risk_engine/scoring.py). For these a verbatim quote is not enough --
# "card usable internationally" is real text but says nothing about
# sanctions. The sentence the quote sits in must be on topic, or the
# indicator is refused and referred to a human instead.
INDICATOR_TOPIC_CUES: dict[str, tuple[str, ...]] = {
    "SANCTIONS_EXPOSURE": (
        "sanction",
        "embargo",
        "designated person",
        "designated entit",
        "designated part",
        "asset freeze",
        "frozen asset",
        "screening hit",
        "screening match",
        "restricted part",
        "restricted jurisdiction",
        "prohibited jurisdiction",
        "blocked person",
        "denied part",
    ),
}

_SENTENCE_END = re.compile(r"[.!?;]\s")


def _sentence_around(normalized: str, offset: int, length: int) -> str:
    """The sentence(s) of normalized source text containing a match."""

    start = 0
    for match in _SENTENCE_END.finditer(normalized, 0, offset):
        start = match.end()
    end_match = _SENTENCE_END.search(normalized, offset + length)
    end = end_match.start() + 1 if end_match else len(normalized)
    return normalized[start:end]


def supports_indicator(record: dict[str, Any], sources: dict[str, dict[str, Any]]) -> bool:
    """Whether a verified quote can back the indicator it is tagged with."""

    cues = INDICATOR_TOPIC_CUES.get(record.get("indicator") or "")
    if not cues:
        return True
    source = sources.get(record.get("source_id") or "")
    if not source or record.get("normalized_offset") is None:
        return False
    context = _sentence_around(
        source["normalized"],
        record["normalized_offset"],
        len(normalize_text(record.get("verbatim_quote") or "")),
    )
    return any(cue in context for cue in cues)


def verify_factor_evidence(
    factor: dict[str, Any],
    sources: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    """
    Verifies one model-returned factor's evidence and returns the fields
    to persist: the verified/rejected evidence records, the indicators
    that survived (each needs a verified quote tagged with it), the ones
    that did not and why, and the derived evidence_status.
    """

    applicable = bool(factor.get("applicable"))
    raw_evidence = factor.get("evidence") or []
    if not isinstance(raw_evidence, list):
        raw_evidence = []

    records: list[dict[str, Any]] = []

    for item in raw_evidence:
        if not isinstance(item, dict):
            continue
        quote = str(item.get("quote") or "").strip()
        if not quote:
            continue
        source_id = str(item.get("source_id") or "").strip()
        indicator = str(item.get("indicator") or "").strip().upper() or None
        if indicator is not None and indicator not in RISK_INDICATORS:
            indicator = None

        record = verify_quote(source_id, quote, sources)
        record["indicator"] = indicator
        records.append(record)

    verified = [record for record in records if record["quote_verified"]]
    for record in verified:
        record["supports_indicator"] = supports_indicator(record, sources)
    verified_indicators = {
        record["indicator"] for record in verified if record["indicator"] and record["supports_indicator"]
    }
    off_topic: list[str] = []

    accepted_indicators: list[str] = []
    rejected_indicators: list[dict[str, str]] = []

    for indicator in factor.get("indicators") or []:
        if not applicable:
            break
        if indicator in verified_indicators:
            if indicator not in accepted_indicators:
                accepted_indicators.append(indicator)
            continue

        tagged = [record for record in records if record["indicator"] == indicator]
        if any(record["quote_verified"] for record in tagged):
            reason = (
                "The quoted text is in the source but does not refer to this "
                "indicator's subject, so it cannot support it on its own."
            )
            off_topic.append(indicator)
        elif tagged:
            reason = "The quote given for this indicator was not found in the cited source."
        else:
            reason = "No quote was given for this indicator."
        rejected_indicators.append({"indicator": indicator, "reason": reason})

    if not applicable:
        status = EvidenceStatus.NOT_APPLICABLE
    elif verified and factor.get("conflicting_evidence"):
        status = EvidenceStatus.CONFLICTING_EVIDENCE
    elif verified:
        status = EvidenceStatus.EVIDENCE_FOUND
    elif records:
        status = EvidenceStatus.NOT_VERIFIED
    else:
        status = EvidenceStatus.INSUFFICIENT_EVIDENCE

    missing = factor.get("missing_information") or []
    if not isinstance(missing, list):
        missing = [missing]
    # Refused, but never silently: a human decides whether it applies.
    missing = list(missing) + [
        f"{indicator.replace('_', ' ').lower()} was proposed by the AI, but the cited "
        "text does not mention it directly; confirm whether it applies."
        for indicator in off_topic
    ]

    return {
        "evidence": records,
        "indicators": accepted_indicators,
        "rejected_indicators": rejected_indicators,
        "evidence_status": status,
        "missing_information": [str(item).strip() for item in missing if str(item).strip()],
        "verified_quote_count": len(verified),
        "rejected_quote_count": len(records) - len(verified),
    }


def prompt_sources_block(
    sources: dict[str, dict[str, Any]],
    max_chars_per_source: int = 8000,
) -> str:
    """
    The citable sources, rendered for the prompt. Long documents are
    truncated here only; verification always checks the full text.
    """

    blocks = []
    for source_id, source in sources.items():
        text = source["text"]
        if len(text) > max_chars_per_source:
            text = text[:max_chars_per_source] + "\n[... truncated ...]"
        blocks.append(f'<source id="{source_id}" label="{source["label"]}">\n{text}\n</source>')
    return "\n\n".join(blocks)

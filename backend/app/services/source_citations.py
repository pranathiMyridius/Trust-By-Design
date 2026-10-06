"""
Grounding AI risk findings in the approved Source Library -- without
trusting the model about what a source says.

Retrieved passages are given to the model as a REFERENCE LIBRARY, each
with an id (LIB:1, LIB:2 ...). They are deliberately *not* added to the
"citable sources" whose quotes prove facts about the assessment
(app/risk_engine/evidence.py): a regulation that mentions sanctions says
nothing about whether *this change* has sanctions exposure, and must never
tag an indicator or move a score. Library passages can only appear as
citations, which never affect indicators, evidence status, ratings or
scores. A human still rates every factor and decides the assessment.

Every citation the model returns is checked here, by plain string
matching, before it is kept:

    unknown library id            -> rejected (invented source)
    quote not in that passage     -> rejected (the source does not say that)
    quote too short to mean much  -> rejected
    quote found verbatim          -> VERIFIED, stored with the source's ID,
                                     title, version, page/section and quote

and a factor whose rationale names a library source it did not verifiably
cite gets a warning, so a claim like "RBI requires X" without a verified
quote is visibly unsupported.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from sqlalchemy.orm import Session

from app.risk_engine.evidence import MIN_QUOTE_CHARS, normalize_text
from app.services import source_retrieval

logger = logging.getLogger(__name__)

MAX_LIBRARY_PASSAGES = 10
MAX_PASSAGE_CHARS = 1800


class Verification:
    VERIFIED = "EXACT_VERIFIED"
    NOT_FOUND = "NOT_FOUND"
    UNKNOWN_SOURCE = "UNKNOWN_SOURCE"
    TOO_SHORT = "TOO_SHORT"


def build_library_sources(db: Session, assessment: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Approved passages applicable to this assessment, keyed LIB:<n>.
    Best effort: any failure returns {} and the analysis proceeds without
    library context."""

    try:
        query = " ".join(
            str(assessment.get(field) or "")
            for field in (
                "title", "description", "evidence", "product_or_service_name", "customer_segment",
                "countries_jurisdictions", "delivery_channels", "transaction_types",
                "third_party_vendor_usage", "technology_process_changes",
            )
        ).strip()
        if not query:
            return {}
        jurisdictions = source_retrieval.applicable_jurisdictions(assessment.get("countries_jurisdictions"))
        passages = source_retrieval.retrieve(db, query, jurisdictions=jurisdictions, limit=MAX_LIBRARY_PASSAGES)
    except Exception:  # noqa: BLE001 -- retrieval must never break the analysis
        logger.exception("Source Library retrieval failed; continuing without library context.")
        db.rollback()
        return {}

    retrieved_at = datetime.now(timezone.utc).isoformat()
    sources: dict[str, dict[str, Any]] = {}
    for index, passage in enumerate(passages, start=1):
        sources[f"LIB:{index}"] = {
            "passage": passage,
            "text": passage.text,
            "normalized": normalize_text(passage.text),
            "retrieved_at": retrieved_at,
        }
    return sources


def prompt_block(library: dict[str, dict[str, Any]]) -> str:
    if not library:
        return ""
    blocks = []
    for library_id, entry in library.items():
        passage = entry["passage"]
        text = passage.text if len(passage.text) <= MAX_PASSAGE_CHARS else passage.text[:MAX_PASSAGE_CHARS] + " [...]"
        where = f", {passage.location()}" if passage.location() else ""
        blocks.append(
            f'<passage id="{library_id}" source="{passage.source_code}" title="{_attr(passage.title)}" '
            f'version="{_attr(passage.version_label)}"{(" location=" + chr(34) + _attr(passage.location()) + chr(34)) if where else ""}>\n'
            f"{text}\n</passage>"
        )
    return "\n\n".join(blocks)


def _attr(value: str) -> str:
    return (value or "").replace('"', "'").replace("\n", " ")


def verify_citations(raw: Any, library: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    """The model's citations, each checked against the passage it names.
    Verified and rejected ones are both returned (the rejected stay on
    record, like rejected evidence quotes)."""

    if not isinstance(raw, list):
        return []
    records: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for item in raw[:20]:
        if not isinstance(item, dict):
            continue
        library_id = str(item.get("source_id") or "").strip()
        quote = str(item.get("quote") or "").strip()
        if not quote:
            continue
        key = (library_id, normalize_text(quote))
        if key in seen:
            continue
        seen.add(key)

        entry = library.get(library_id)
        record: dict[str, Any] = {
            "library_id": library_id,
            "quote": quote,
            "ai_relevance_note": str(item.get("relevance") or "").strip()[:400] or None,
            "quote_verified": False,
            "verification": None,
        }
        if entry is None:
            record["verification"] = Verification.UNKNOWN_SOURCE
            records.append(record)
            continue
        passage = entry["passage"]
        record.update(
            {
                "chunk_id": str(passage.chunk_id),
                "record_id": str(passage.record_id),
                "version_id": str(passage.version_id),
                "source_code": passage.source_code,
                "source_title": passage.title,
                "authority": passage.authority,
                "version_label": passage.version_label,
                "page_start": passage.page_start,
                "page_end": passage.page_end,
                "section": passage.section,
                "source_url": passage.source_url,
                "outdated_source": passage.outdated,
                "passage_sha256": passage.text_sha256,
                "retrieved_at": entry["retrieved_at"],
            }
        )
        normalized_quote = normalize_text(quote)
        if len(normalized_quote) < MIN_QUOTE_CHARS:
            record["verification"] = Verification.TOO_SHORT
        elif normalized_quote not in entry["normalized"]:
            record["verification"] = Verification.NOT_FOUND
        else:
            record["verification"] = Verification.VERIFIED
            record["quote_verified"] = True
        records.append(record)
    return records


def unsupported_source_claims(factor: dict[str, Any], citations: list[dict[str, Any]], library: dict[str, dict[str, Any]]) -> list[str]:
    """Warnings for library sources the factor's own prose names without a
    verified citation to them."""

    if not library:
        return []
    prose = normalize_text(" ".join(str(factor.get(key) or "") for key in ("rationale", "misuse_scenario")))
    cited = {c.get("source_code") for c in citations if c.get("quote_verified")}
    warnings: list[str] = []
    named: set[str] = set()
    for entry in library.values():
        passage = entry["passage"]
        if passage.source_code in named or passage.source_code in cited:
            continue
        if normalize_text(passage.title) in prose or normalize_text(passage.source_code) in prose:
            named.add(passage.source_code)
            warnings.append(
                f"The AI's explanation refers to '{passage.title}' ({passage.source_code}) without a verified "
                "quote from it; treat that statement as unsupported until checked against the source."
            )
    return warnings


def apply_to_factor(factor: dict[str, Any], library: dict[str, dict[str, Any]]) -> None:
    """Verifies the factor's raw citations and stores the result on it
    (`source_citations`, `citation_warnings`). Citations never change
    anything else about the factor."""

    raw = factor.pop("source_citations_raw", None)
    if not factor.get("applicable"):
        factor["source_citations"] = []
        return
    citations = verify_citations(raw, library)
    factor["source_citations"] = citations
    warnings = unsupported_source_claims(factor, citations, library)
    if warnings:
        factor["missing_information"] = list(factor.get("missing_information") or []) + warnings

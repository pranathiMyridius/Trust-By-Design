"""
P4 (R2.4): provenance of every extracted profile field.

For each field the extractor returned, this records where the value came
from (document, version, page or sheet), when it was extracted, how, and a
confidence level. The confidence is derived here by plain string matching
against the documents' own text -- the same normalization the risk-factor
evidence check uses (app/risk_engine/evidence.py) -- and never from
anything the model says about its own certainty:

    HIGH    a quote the model cited was found verbatim in a document, and
            the value itself appears in that quote
    MEDIUM  the value appears verbatim in a document, but no verified quote
            contains it (always the case for the rule-based extractor,
            which cites nothing); or a verified quote supports the field
            but the value is the model's paraphrase of it
    LOW     the value does not appear in any document, and no verified
            quote supports it -- treat as unsupported until a person checks

List fields are checked item by item; the field takes its weakest item.
Values a person typed (the intake form, or a correction) are USER_PROVIDED:
they are not extraction, so no confidence is computed for them.

STATUS: the three-level scale and its thresholds are a design default of
this phase (not specified by the brief), recorded as open in
docs/REMAINING_REQUIREMENTS.md.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Iterable

from app.risk_engine.evidence import MIN_QUOTE_CHARS, _page_at, normalize_text

HIGH = "HIGH"
MEDIUM = "MEDIUM"
LOW = "LOW"
USER_PROVIDED = "USER_PROVIDED"
_RANK = {HIGH: 3, MEDIUM: 2, LOW: 1}

# How each item was verified.
QUOTE_CONTAINS_VALUE = "QUOTE_CONTAINS_VALUE"
VALUE_FOUND_IN_SOURCE = "VALUE_FOUND_IN_SOURCE"
QUOTE_SUPPORTS_INTERPRETATION = "QUOTE_SUPPORTS_INTERPRETATION"
NOT_FOUND_IN_SOURCE = "NOT_FOUND_IN_SOURCE"

AI_EXTRACTION = "AI_EXTRACTION"
RULE_EXTRACTION = "RULE_EXTRACTION"
INTAKE_FORM = "INTAKE_FORM"
USER_CORRECTION = "USER_CORRECTION"

LIST_FIELDS = [
    "channels",
    "countries",
    "customer_segments",
    "third_party_vendors",
    "data_shared",
    "technologies",
    "regulatory_considerations",
    "existing_controls",
    "additional_risk_factors",
    "payment_methods",
]

# Extracted fields that get provenance. title / business_description /
# evidence are the model's own summaries, reviewed and edited by the
# requester on the intake form before submission; change_type is a
# normalized classification. Neither is quoted from a document.
TEXT_FIELDS = [
    "business_line",
    "transaction_volume",
    "average_transaction_size",
    "maximum_transaction_limit",
    "product_or_service_name",
    "business_owner",
    "legal_entity",
    "transaction_types",
    "expected_launch_date",
    "submitted_by",
    "customer_type",
    "transaction_origin",
    "transaction_destination",
    "transaction_frequency",
    "onboarding_approach",
    "ownership_entity_structure",
]

PROVENANCE_FIELDS = TEXT_FIELDS + LIST_FIELDS

_BASIS = {
    QUOTE_CONTAINS_VALUE: "A quoted passage was found verbatim in the document and contains this value.",
    VALUE_FOUND_IN_SOURCE: "The value appears verbatim in the document; no verified quote was cited for it.",
    QUOTE_SUPPORTS_INTERPRETATION: (
        "A quoted passage was found verbatim in the document, but the value is a paraphrase of it."
    ),
    NOT_FOUND_IN_SOURCE: "The value does not appear in any uploaded document and no verified quote supports it.",
}


@dataclass
class SourceText:
    document_id: int | None
    document_version: int | None
    filename: str
    text: str

    def __post_init__(self) -> None:
        self.normalized = normalize_text(self.text)


def sources_from_documents(documents: Iterable[Any]) -> list[SourceText]:
    return [
        SourceText(
            getattr(document, "id", None),
            getattr(document, "version", None),
            getattr(document, "filename", None) or f"Document {getattr(document, 'id', '')}",
            getattr(document, "extracted_text", None) or "",
        )
        for document in documents
        if (getattr(document, "extracted_text", None) or "").strip()
    ]


def _find_value(normalized: str, value: str) -> int | None:
    needle = normalize_text(value)
    if len(needle) < 2:
        return None
    # Whole words only, so "UK" is not found inside "Ukraine".
    match = re.search(r"(?<![0-9a-z])" + re.escape(needle) + r"(?![0-9a-z])", normalized)
    return match.start() if match else None


def _sheet_at(source: SourceText, offset: int) -> str | None:
    """The spreadsheet sheet a match falls in (the extractor writes
    "[Sheet: name]" before each sheet), with its original spelling."""

    raw_names = re.findall(r"\[Sheet: ([^\]]*)\]", source.text)
    if not raw_names:
        return None
    index = -1
    for count, match in enumerate(re.finditer(r"\[sheet: [^\]]*\]", source.normalized)):
        if match.start() > offset:
            break
        index = count
    return raw_names[index] if 0 <= index < len(raw_names) else None


def _location(source: SourceText, offset: int) -> dict[str, Any]:
    return {
        "document_id": source.document_id,
        "document_version": source.document_version,
        "filename": source.filename,
        "page": _page_at(source.text, offset, source.normalized),
        "sheet": _sheet_at(source, offset),
    }


def _verified_quotes(raw: Any, sources: list[SourceText]) -> list[dict[str, Any]]:
    """The model's cited quotes for one field that are really in a document.
    Anything else the model attached (a confidence, a score) is ignored."""

    if raw is None:
        return []
    items = raw if isinstance(raw, list) else [raw]
    verified = []
    for item in items:
        if isinstance(item, str):
            quote, named = item, None
        elif isinstance(item, dict):
            quote, named = str(item.get("quote") or ""), str(item.get("document") or "").strip() or None
        else:
            continue
        normalized_quote = normalize_text(quote)
        if len(normalized_quote) < MIN_QUOTE_CHARS:
            continue
        # The named document first, then any other; the quote must be in one.
        ordered = sorted(sources, key=lambda s: 0 if named and s.filename == named else 1)
        for source in ordered:
            offset = source.normalized.find(normalized_quote)
            if offset != -1:
                verified.append({"quote": quote.strip(), "normalized": normalized_quote, "offset": offset, "source": source})
                break
    return verified


def _verify_item(value: str, quotes: list[dict[str, Any]], sources: list[SourceText]) -> dict[str, Any]:
    needle = normalize_text(value)
    for quote in quotes:
        if needle and needle in quote["normalized"]:
            return {
                "value": value,
                "verification": QUOTE_CONTAINS_VALUE,
                "confidence": HIGH,
                "quote": quote["quote"],
                **_location(quote["source"], quote["offset"]),
            }
    for source in sources:
        offset = _find_value(source.normalized, value)
        if offset is not None:
            return {"value": value, "verification": VALUE_FOUND_IN_SOURCE, "confidence": MEDIUM, "quote": None, **_location(source, offset)}
    if quotes:
        quote = quotes[0]
        return {
            "value": value,
            "verification": QUOTE_SUPPORTS_INTERPRETATION,
            "confidence": MEDIUM,
            "quote": quote["quote"],
            **_location(quote["source"], quote["offset"]),
        }
    return {
        "value": value,
        "verification": NOT_FOUND_IN_SOURCE,
        "confidence": LOW,
        "quote": None,
        "document_id": None,
        "document_version": None,
        "filename": None,
        "page": None,
        "sheet": None,
    }


def _now_iso(at: datetime | None) -> str:
    return (at or datetime.now(timezone.utc)).isoformat()


def build_field_provenance(
    extraction: Any,
    sources: list[SourceText],
    *,
    method: str,
    extracted_at: datetime | None = None,
) -> dict[str, dict[str, Any]]:
    """Provenance for every non-empty extracted field. `extraction` is a
    DocumentAssessmentExtraction (or dict); `method` is AI or RULES."""

    data = extraction.model_dump() if hasattr(extraction, "model_dump") else dict(extraction)
    field_evidence = data.get("field_evidence") or {}
    origin = AI_EXTRACTION if method == "AI" else RULE_EXTRACTION
    at = _now_iso(extracted_at)

    provenance: dict[str, dict[str, Any]] = {}
    for field in PROVENANCE_FIELDS:
        if field not in data:
            continue
        value = data.get(field)
        values = [str(v) for v in value if str(v).strip()] if isinstance(value, list) else ([str(value)] if value not in (None, "") else [])
        if not values:
            continue
        quotes = _verified_quotes(field_evidence.get(field), sources) if method == "AI" else []
        items = [_verify_item(item, quotes, sources) for item in values]
        weakest = min(items, key=lambda item: _RANK[item["confidence"]])
        first_located = next((item for item in items if item["document_id"] is not None or item["filename"]), weakest)
        provenance[field] = {
            "field": field,
            "origin": origin,
            "extraction_method": method,
            "extracted_at": at,
            "value": value,
            "confidence": weakest["confidence"],
            "verification": weakest["verification"],
            "confidence_basis": _BASIS[weakest["verification"]],
            "document_id": first_located["document_id"],
            "document_version": first_located["document_version"],
            "filename": first_located["filename"],
            "page": first_located["page"],
            "sheet": first_located["sheet"],
            "quote": first_located["quote"],
            "items": items if isinstance(value, list) else [],
        }
    return provenance


def intake_form_provenance(values: dict[str, Any], *, actor: str | None, at: datetime | None = None) -> dict[str, dict[str, Any]]:
    """A profile seeded from the requester's own intake answers."""

    provenance = {}
    for field, value in values.items():
        if value in (None, "", []):
            continue
        provenance[field] = {
            "field": field,
            "origin": INTAKE_FORM,
            "extraction_method": "INTAKE_FORM",
            "extracted_at": _now_iso(at),
            "value": value,
            "confidence": USER_PROVIDED,
            "verification": None,
            "confidence_basis": "Stated by the requester on the intake form; not extracted from a document.",
            "document_id": None,
            "document_version": None,
            "filename": None,
            "page": None,
            "sheet": None,
            "quote": None,
            "items": [],
            "entered_by": actor,
        }
    return provenance


def correction_record(
    previous: dict[str, Any] | None,
    field: str,
    value: Any,
    *,
    actor: str,
    actor_id: int | None = None,
    at: datetime | None = None,
) -> dict[str, Any]:
    """A field corrected by a person. The record it replaces is kept inside
    it, so the extracted provenance stays visible beside the correction."""

    return {
        "field": field,
        "origin": USER_CORRECTION,
        "extraction_method": None,
        "extracted_at": (previous or {}).get("extracted_at"),
        "corrected_at": _now_iso(at),
        "corrected_by": actor,
        "corrected_by_id": actor_id,
        "value": value,
        "confidence": USER_PROVIDED,
        "verification": None,
        "confidence_basis": "Corrected by a person; the extracted value and its provenance are kept as 'replaces'.",
        "document_id": None,
        "document_version": None,
        "filename": None,
        "page": None,
        "sheet": None,
        "quote": None,
        "items": [],
        "replaces": previous,
    }


def load(raw: str | None) -> dict[str, dict[str, Any]]:
    try:
        value = json.loads(raw) if raw else {}
    except (TypeError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


def dump(provenance: dict[str, dict[str, Any]]) -> str:
    return json.dumps(provenance, default=str)


def summarize(provenance: dict[str, dict[str, Any]]) -> dict[str, int]:
    counts = {HIGH: 0, MEDIUM: 0, LOW: 0, USER_PROVIDED: 0}
    for record in provenance.values():
        level = record.get("confidence")
        if level in counts:
            counts[level] += 1
    return counts

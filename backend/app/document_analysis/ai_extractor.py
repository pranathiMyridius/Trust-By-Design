"""
AI-driven document extraction (R2.3).

Reads the raw text pulled from an uploaded document (see
app/file_processing/extractor.py) and asks an LLM, via the same
OpenRouter integration already used for risk analysis, to return the
structured fields the intake screen shows the user for confirmation.

This module never talks to the database and never decides business
logic on its own -- it only turns document text into a
DocumentAssessmentExtraction. The caller (app/document_analysis/analyzer.py)
is responsible for falling back to the rule-based extractor if this
fails, and for whatever happens with the result afterwards.
"""

import json
import os
import re
from typing import Any

import requests

from app.ai import provider
from app.ai.metering import metered_post
import truststore
from dotenv import load_dotenv

from app.schemas.document_analysis import DocumentAssessmentExtraction


# Load variables from backend/.env (harmless if already loaded elsewhere).
load_dotenv()
truststore.inject_into_ssl()


# The configured provider (OpenAI or OpenRouter): app/ai/provider.py.
OPENROUTER_API_KEY = provider.API_KEY

OPENROUTER_MODEL = provider.MODEL

OPENROUTER_URL = provider.CHAT_COMPLETIONS_URL

# Keep prompts bounded. Very large uploads (big spreadsheets, long PDFs)
# would otherwise blow past the model's context window; the first ~20k
# characters is more than enough for an intake document that is a few
# pages long, and every field we ask for should show up early in a
# well-structured business-requirement document.
MAX_EXCERPT_CHARS = 20000

# R1.2: the 9 canonical change types the intake form offers, plus the 2
# legacy values already stored on existing assessments (kept selectable
# so those records still round-trip correctly).
VALID_CHANGE_TYPES = {
    "NEW_PRODUCT",
    "NEW_SERVICE",
    "NEW_CUSTOMER_SEGMENT",
    "NEW_GEOGRAPHY",
    "PROCESS_CHANGE",
    "TECHNOLOGY_CHANGE",
    "THIRD_PARTY_INTRODUCTION",
    "TRANSACTION_LIMIT_OR_CHANNEL_CHANGE",
    "PERIODIC_REASSESSMENT",
    "MATERIAL_CHANGE",
    "THIRD_PARTY",
}

# Loose keyword fallback, checked in order, for when the model's answer
# doesn't normalize directly to one of VALID_CHANGE_TYPES. Longer/more
# specific phrases are listed before the shorter/looser ones they
# contain (e.g. "new customer segment" before "new"), since
# _normalize_change_type matches on first hit.
CHANGE_TYPE_KEYWORDS = {
    "periodic reassessment": "PERIODIC_REASSESSMENT",
    "reassessment": "PERIODIC_REASSESSMENT",
    "transaction limit": "TRANSACTION_LIMIT_OR_CHANNEL_CHANGE",
    "transaction-limit": "TRANSACTION_LIMIT_OR_CHANNEL_CHANGE",
    "channel change": "TRANSACTION_LIMIT_OR_CHANNEL_CHANGE",
    "third party introduction": "THIRD_PARTY_INTRODUCTION",
    "third-party introduction": "THIRD_PARTY_INTRODUCTION",
    "new geography": "NEW_GEOGRAPHY",
    "new country": "NEW_GEOGRAPHY",
    "geography": "NEW_GEOGRAPHY",
    "third party": "THIRD_PARTY",
    "third-party": "THIRD_PARTY",
    "vendor": "THIRD_PARTY",
    "technology change": "TECHNOLOGY_CHANGE",
    "technology": "TECHNOLOGY_CHANGE",
    "process change": "PROCESS_CHANGE",
    "process": "PROCESS_CHANGE",
    "material change": "MATERIAL_CHANGE",
    "material": "MATERIAL_CHANGE",
    "new customer segment": "NEW_CUSTOMER_SEGMENT",
    "customer segment": "NEW_CUSTOMER_SEGMENT",
    "new service": "NEW_SERVICE",
    "service": "NEW_SERVICE",
    "new product": "NEW_PRODUCT",
}

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
]

OPTIONAL_TEXT_FIELDS = [
    "business_line",
    "transaction_volume",
    "average_transaction_size",
    "maximum_transaction_limit",
    # R1.1 fields with no other structured counterpart -- see
    # DocumentAssessmentExtraction.
    "product_or_service_name",
    "business_owner",
    "legal_entity",
    "transaction_types",
    "expected_launch_date",
    # R1.5: who the document names as the submitter, if stated.
    "submitted_by",
]

# Guardrails for the "evidence" field. Some models -- especially a
# free/low-quality one behind an "auto" router -- ignore the "write a
# short summary" instruction and either paste back most of the source
# document or leave the field empty. Either way we never want
# "evidence" to end up being the raw document dump, so it's capped and
# checked for near-duplication of the source text here rather than
# trusted at face value.
MAX_EVIDENCE_CHARS = 800

# If the model's "evidence" text is at least this fraction of the
# length of the (possibly truncated) source excerpt, treat it as an
# echo of the document rather than an actual summary.
EVIDENCE_ECHO_RATIO = 0.6


def _build_extraction_prompt(text: str) -> str:
    excerpt = text[:MAX_EXCERPT_CHARS]

    return f"""
You are an AI assistant supporting a bank's business-change risk
assessment intake process. Read the SOURCE DOCUMENT below and extract
the structured fields a risk analyst needs in order to start an
assessment.

SOURCE DOCUMENT:
{excerpt}

Return ONLY valid JSON with exactly this shape:

{{
  "title": "Name of the proposed business change or initiative.",
  "change_type": "NEW_PRODUCT | NEW_SERVICE | NEW_CUSTOMER_SEGMENT | NEW_GEOGRAPHY | PROCESS_CHANGE | TECHNOLOGY_CHANGE | THIRD_PARTY_INTRODUCTION | TRANSACTION_LIMIT_OR_CHANNEL_CHANGE | PERIODIC_REASSESSMENT",
  "business_description": "Clear summary of what the change is and what the business intends to do.",
  "evidence": "A SHORT synthesis (2-5 sentences, or short bullet-style clauses separated by semicolons) of the key facts that support the assessment -- not the document's contents.",
  "business_line": "string or null",
  "product_or_service_name": "Name of the specific product or service being introduced or changed, or null.",
  "business_owner": "Name/role of the person or team accountable for this change, or null.",
  "legal_entity": "Legal entity or business unit this change sits under, or null.",
  "transaction_types": "The kinds of transactions involved (e.g. card payments, bank transfers), as a short comma-separated string, or null.",
  "expected_launch_date": "Expected launch/implementation date as an ISO date (YYYY-MM-DD) if stated, else null.",
  "submitted_by": "Name (and role/title, if given) of the person submitting this request, e.g. from a 'Submitted By:' line, or null if the document doesn't state one.",
  "channels": ["string", "..."],
  "countries": ["string", "..."],
  "customer_segments": ["string", "..."],
  "transaction_volume": "string or null",
  "average_transaction_size": "string or null",
  "maximum_transaction_limit": "string or null",
  "third_party_vendors": ["string", "..."],
  "data_shared": ["string", "..."],
  "technologies": ["string", "..."],
  "regulatory_considerations": ["string", "..."],
  "existing_controls": ["string", "..."],
  "additional_risk_factors": ["string", "..."],
  "field_evidence": {{
    "<field name from above>": [
      {{"document": "file name from the '--- name ---' header, if any", "quote": "exact text copied from the document"}}
    ]
  }}
}}

Rules:
- Only use information that is actually present in the document. Do
  not invent facts, numbers, countries, vendors, or controls that are
  not stated or clearly implied in the text.
- If a field is not mentioned in the document, return null for a
  single-value field or an empty array [] for a list field. Do not
  omit any key.
- "change_type" must be exactly one of: NEW_PRODUCT, NEW_SERVICE,
  NEW_CUSTOMER_SEGMENT, NEW_GEOGRAPHY, PROCESS_CHANGE,
  TECHNOLOGY_CHANGE, THIRD_PARTY_INTRODUCTION,
  TRANSACTION_LIMIT_OR_CHANNEL_CHANGE, PERIODIC_REASSESSMENT. Pick the
  closest match based on the document's content.
- "title" and "business_description" must always be non-empty strings;
  if the document does not state a clear name, summarize the change in
  your own words instead of leaving it blank.
- "evidence" is a SUMMARY you write, not a copy of the source. Never
  return the full document, a large block of it, or most of it as
  "evidence" -- distill it down to the handful of facts that actually
  matter for a risk analyst (e.g. what changed, scale, geography,
  vendors, data involved). Keep it under roughly 600 characters.
- "field_evidence": for every field you filled in (other than title,
  change_type, business_description and evidence), give one or more
  verbatim quotes, copied character for character from the SOURCE
  DOCUMENT, that state the value. Quotes are checked against the document
  automatically; a paraphrased or invented quote is rejected. Do not give
  confidence scores -- reliability is determined by that check.
- Return raw JSON only. No markdown code fences, no commentary before
  or after the JSON.
"""


def _clean_json_response(text: str) -> str:
    """Remove Markdown code fences if the model wraps its JSON in them."""

    text = text.strip()

    if text.startswith("```json"):
        text = text[7:]
    elif text.startswith("```"):
        text = text[3:]

    if text.endswith("```"):
        text = text[:-3]

    return text.strip()


def _parse_json_payload(content: str) -> dict[str, Any]:
    raw_text = _clean_json_response(content)

    try:
        return json.loads(raw_text)
    except json.JSONDecodeError:
        pass

    # Fallback: pull the first {...} object out of the response, in
    # case the model added stray commentary around the JSON.
    start = raw_text.find("{")
    end = raw_text.rfind("}")

    if start == -1 or end == -1 or end <= start:
        raise RuntimeError(
            "The AI model returned invalid JSON.\n"
            f"Model response:\n{content}"
        )

    json_text = raw_text[start : end + 1]

    try:
        return json.loads(json_text)
    except json.JSONDecodeError as exc:
        raise RuntimeError(
            "The AI model returned invalid JSON.\n"
            f"Model response:\n{content}"
        ) from exc


def _as_string_list(value: Any) -> list[str]:
    if value is None:
        return []

    if not isinstance(value, list):
        value = [value]

    return [str(item).strip() for item in value if str(item).strip()]


def _as_optional_string(value: Any) -> str | None:
    if value is None:
        return None

    text = str(value).strip()

    return text or None


def _normalize_change_type(value: Any) -> str:
    """
    Coerce whatever the model returned into one of VALID_CHANGE_TYPES.

    Models are asked for the exact enum spelling (e.g. "NEW_PRODUCT")
    but sometimes return a close variant instead ("New Product",
    "new-product", "Material change"). Normalize punctuation/case
    first so those still match, and only fall back to the NEW_PRODUCT
    default when nothing recognizable was returned at all.
    """

    text = str(value or "").strip().upper()

    normalized = re.sub(r"[\s-]+", "_", text)

    if normalized in VALID_CHANGE_TYPES:
        return normalized

    # Loose keyword match as a second pass, mirroring the mapping the
    # rule-based extractor uses.
    lowered = text.lower()

    for keyword, mapped in CHANGE_TYPE_KEYWORDS.items():
        if keyword in lowered:
            return mapped

    return "NEW_PRODUCT"


def _condense_evidence(
    raw_evidence: str | None,
    fallback_text: str,
    business_description: str,
    list_values: dict[str, list[str]],
) -> str:
    """
    Make sure "evidence" is actually a short summary and never a copy
    of the source document.

    - If the model gave a reasonably short, distinct summary, use it
      (trimmed to MAX_EVIDENCE_CHARS as a hard cap).
    - If it's missing, empty, or looks like it just echoed most of the
      source text back, build a short synthesized summary instead of
      falling back to the raw document text.
    """

    excerpt = fallback_text[:MAX_EXCERPT_CHARS]

    looks_like_an_echo = bool(raw_evidence) and (
        len(raw_evidence) >= EVIDENCE_ECHO_RATIO * len(excerpt)
        or raw_evidence.strip() in excerpt
    )

    if raw_evidence and not looks_like_an_echo:
        return raw_evidence[:MAX_EVIDENCE_CHARS].rstrip()

    # Synthesize a short summary from the other structured fields
    # rather than dumping the document.
    parts = [business_description]

    highlight_labels = {
        "countries": "Countries",
        "third_party_vendors": "Vendors",
        "technologies": "Technologies",
        "data_shared": "Data shared",
        "existing_controls": "Existing controls",
    }

    for field, label in highlight_labels.items():
        values = list_values.get(field) or []

        if values:
            parts.append(f"{label}: {', '.join(values[:5])}.")

    summary = " ".join(part for part in parts if part).strip()

    return summary[:MAX_EVIDENCE_CHARS].rstrip()


def _normalize_extraction(
    payload: dict[str, Any],
    fallback_text: str,
) -> DocumentAssessmentExtraction:
    title = _as_optional_string(payload.get("title")) or "Untitled Assessment"

    change_type = _normalize_change_type(payload.get("change_type"))

    business_description = (
        _as_optional_string(payload.get("business_description"))
        or "Business change extracted from uploaded document."
    )

    list_values = {
        field: _as_string_list(payload.get(field))
        for field in LIST_FIELDS
    }

    evidence = _condense_evidence(
        raw_evidence=_as_optional_string(payload.get("evidence")),
        fallback_text=fallback_text,
        business_description=business_description,
        list_values=list_values,
    )

    normalized: dict[str, Any] = {
        "title": title,
        "change_type": change_type,
        "business_description": business_description,
        "evidence": evidence,
        **list_values,
    }

    for field in OPTIONAL_TEXT_FIELDS:
        normalized[field] = _as_optional_string(payload.get(field))

    normalized["field_evidence"] = _normalize_field_evidence(payload.get("field_evidence"))
    normalized["extraction_method"] = "AI"

    return DocumentAssessmentExtraction(**normalized)


def _normalize_field_evidence(raw: Any) -> dict[str, list[dict[str, str]]]:
    """Keeps only {field: [{document, quote}]} for known fields; anything
    else the model sent (confidence values included) is dropped unread."""

    if not isinstance(raw, dict):
        return {}
    known = set(LIST_FIELDS) | set(OPTIONAL_TEXT_FIELDS)
    cleaned: dict[str, list[dict[str, str]]] = {}
    for field, items in raw.items():
        if field not in known:
            continue
        entries = items if isinstance(items, list) else [items]
        quotes = []
        for entry in entries:
            if isinstance(entry, str) and entry.strip():
                quotes.append({"document": "", "quote": entry.strip()})
            elif isinstance(entry, dict) and str(entry.get("quote") or "").strip():
                quotes.append(
                    {"document": str(entry.get("document") or "").strip(), "quote": str(entry["quote"]).strip()}
                )
        if quotes:
            cleaned[field] = quotes[:5]
    return cleaned


def extract_with_ai(extracted_text: str) -> DocumentAssessmentExtraction:
    """
    Extract structured assessment fields from raw document text using
    the configured AI model (OpenRouter).

    Raises RuntimeError / ValueError on any failure -- callers that
    want a non-AI fallback should catch these themselves.
    """

    text = extracted_text.strip()

    if not text:
        raise ValueError("No document text to analyze.")

    if not OPENROUTER_API_KEY:
        raise RuntimeError(
            f"{provider.KEY_NAME} is not configured. "
            "Add it to backend/.env"
        )

    prompt = _build_extraction_prompt(text)

    headers = {
        "Authorization": f"Bearer {OPENROUTER_API_KEY}",
        "Content-Type": "application/json",
    }

    payload = {
        "model": OPENROUTER_MODEL,
        "messages": [
            {
                "role": "user",
                "content": prompt,
            }
        ],
        "temperature": 0.1,
        "response_format": {
            "type": "json_object"
        },
    }

    try:
        response = metered_post("DOCUMENT_EXTRACTION",
            OPENROUTER_URL,
            headers=headers,
            json=payload,
            timeout=120,
        )
    except requests.RequestException as exc:
        raise RuntimeError(
            f"OpenRouter request failed: {exc}"
        ) from exc

    if response.status_code != 200:
        raise RuntimeError(
            "OpenRouter API returned an error.\n"
            f"Status: {response.status_code}\n"
            f"Response: {response.text}"
        )

    try:
        response_data = response.json()
    except ValueError as exc:
        raise RuntimeError(
            "OpenRouter returned invalid JSON.\n"
            f"Response: {response.text}"
        ) from exc

    try:
        assistant_message = response_data["choices"][0]["message"]
    except (KeyError, IndexError, TypeError) as exc:
        raise RuntimeError(
            "Unexpected OpenRouter response format.\n"
            f"Response: {response_data}"
        ) from exc

    content = assistant_message.get("content")

    if not content:
        raise RuntimeError(
            "OpenRouter returned an empty model response."
        )

    result_payload = _parse_json_payload(content)

    if not isinstance(result_payload, dict):
        raise RuntimeError("Model response must be a JSON object.")

    return _normalize_extraction(result_payload, fallback_text=text)

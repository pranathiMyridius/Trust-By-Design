"""
AI evidence check: do the documents uploaded to an assessment support a
given control?

The model reads the control and the extracted text of the assessment's
current documents and, per document, says whether it supports the control
(SUPPORTED / PARTIAL / NONE) with a verbatim quote. The quote is checked
against the document text here: a result whose quote cannot be found is
discarded, so a suggestion never rests on invented evidence. The output is
a suggestion only -- an analyst accepts or rejects it.
"""

import json
import re
from typing import Any

import requests
from dotenv import load_dotenv
import truststore

from app.ai import provider
from app.ai.metering import metered_post

load_dotenv()
truststore.inject_into_ssl()

SUPPORT_LEVELS = ("SUPPORTED", "PARTIAL", "NONE")
CONFIDENCES = ("LOW", "MEDIUM", "HIGH")
EFFECTIVENESS_VALUES = ("EFFECTIVE", "PARTIALLY_EFFECTIVE", "INEFFECTIVE", "UNVERIFIED")

# Per-document and total excerpt caps keep one control's prompt bounded.
MAX_DOCUMENT_CHARS = 6000
MAX_TOTAL_CHARS = 24000


def _normalise(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip().lower()


def quote_in_text(quote: str, text: str) -> bool:
    quote = _normalise(quote)
    return len(quote) >= 12 and quote in _normalise(text)


def _excerpt_documents(documents: list[dict[str, Any]]) -> tuple[str, dict[int, str]]:
    blocks: list[str] = []
    seen: dict[int, str] = {}
    budget = MAX_TOTAL_CHARS
    for document in documents:
        if budget <= 0:
            break
        text = (document.get("text") or "")[: min(MAX_DOCUMENT_CHARS, budget)]
        budget -= len(text)
        seen[document["id"]] = text
        blocks.append(f'<document id="{document["id"]}" name="{document.get("filename", "")}">\n{text}\n</document>')
    return "\n".join(blocks), seen


def check_control_evidence(
    control_type: str,
    control_description: str | None,
    risk_rationale: str | None,
    documents: list[dict[str, Any]],
) -> dict[str, Any] | None:
    """
    Returns {"results": [...], "suggested_effectiveness": str | None,
    "model": str | None}, or None when the check could not run (no key,
    provider error, unusable answer). Each result is {document_id,
    support_level, confidence, quote, rationale, shortfalls}; documents the
    model judged irrelevant are omitted.
    """

    if not provider.API_KEY or not documents:
        return None

    document_block, excerpts = _excerpt_documents(documents)

    prompt = f"""You are a financial crime control assurance reviewer. Decide whether the
uploaded documents below contain evidence that the control is in place and operating.

CONTROL TYPE: {control_type}
CONTROL DESCRIPTION: {control_description or "not provided"}
RISK IT MITIGATES: {risk_rationale or "not provided"}

DOCUMENTS:
{document_block}

Rules:
- Use ONLY what the documents say. Do not assume a control exists because it is common practice.
- A document SUPPORTS the control only if it shows the control is defined or operating
  (e.g. a policy, procedure, test result, system configuration, report or attestation).
  Use PARTIAL if it covers the control in part or only describes intent.
- "quote" must be copied exactly from that document (one passage, under 300 characters).
- List in "shortfalls" what the evidence does not show (frequency, scope, testing, ownership...).
- Omit documents that say nothing relevant to this control.
- "suggested_effectiveness" is your view of the control's operating effectiveness from the
  evidence: EFFECTIVE only when the evidence shows it operating, otherwise PARTIALLY_EFFECTIVE,
  INEFFECTIVE or UNVERIFIED.

Respond with ONLY a JSON object, no additional text:
{{"results": [{{"document_id": 1, "support_level": "SUPPORTED | PARTIAL", "confidence": "LOW | MEDIUM | HIGH", "quote": "exact passage", "rationale": "why this supports the control", "shortfalls": ["what is missing"]}}], "suggested_effectiveness": "EFFECTIVE | PARTIALLY_EFFECTIVE | INEFFECTIVE | UNVERIFIED"}}"""

    try:
        response = metered_post(
            "CONTROL_EVIDENCE_CHECK",
            provider.CHAT_COMPLETIONS_URL,
            headers={
                "Authorization": f"Bearer {provider.API_KEY}",
                "Content-Type": "application/json",
            },
            json={
                "model": provider.MODEL,
                "messages": [{"role": "user", "content": prompt}],
                "temperature": 0.1,
            },
            timeout=120,
        )
        response.raise_for_status()
        body = response.json()
        if not body.get("choices"):
            return None
        content = body["choices"][0].get("message", {}).get("content", "")
        model = body.get("model")

        try:
            payload = json.loads(content)
        except json.JSONDecodeError:
            if "```json" in content:
                content = content.split("```json")[1].split("```")[0]
            elif "```" in content:
                content = content.split("```")[1].split("```")[0]
            payload = json.loads(content.strip())
    except (requests.RequestException, json.JSONDecodeError, KeyError, ValueError, IndexError) as exc:
        print(f"Control evidence check failed: {exc}")
        return None

    if not isinstance(payload, dict):
        return None

    results: list[dict[str, Any]] = []
    for item in payload.get("results") or []:
        if not isinstance(item, dict):
            continue
        try:
            document_id = int(item.get("document_id"))
        except (TypeError, ValueError):
            continue
        level = str(item.get("support_level") or "").upper()
        if document_id not in excerpts or level not in ("SUPPORTED", "PARTIAL"):
            continue
        quote = str(item.get("quote") or "").strip()
        # The quote must really be in the document, or the result is dropped.
        if not quote_in_text(quote, excerpts[document_id]):
            continue
        confidence = str(item.get("confidence") or "").upper()
        shortfalls = [str(s).strip() for s in (item.get("shortfalls") or []) if str(s).strip()]
        results.append(
            {
                "document_id": document_id,
                "support_level": level,
                "confidence": confidence if confidence in CONFIDENCES else "LOW",
                "quote": quote[:600],
                "rationale": str(item.get("rationale") or "").strip(),
                "shortfalls": shortfalls[:8],
            }
        )

    effectiveness = str(payload.get("suggested_effectiveness") or "").upper()
    if effectiveness not in EFFECTIVENESS_VALUES:
        effectiveness = None
    # Nothing verifiable found: never suggest a rating from no evidence.
    if not results:
        effectiveness = None

    return {"results": results, "suggested_effectiveness": effectiveness, "model": model}

"""
Stage 9 (R9.1, R9.2): LLM-written narrative sections of an assessment
draft -- executive summary, business-change description, per-category
risk statements, and the analyst recommendation -- synthesized from
already-computed structured data (business profile, risk factors,
Stage 6 inherent-risk calculation, controls/effectiveness/gaps, residual
score). The model never invents or recalculates a score; it only writes
prose about numbers/facts it is given, and separately flags uncertainty
(low-confidence information, unsupported conclusions, conflicting
evidence, unresolved questions) for R9.2.

Same OpenRouter-backed call shape as app/ai/risk_factor_analyzer.py.
Callers should catch DraftNarrativeError and fall back to the
deterministic templates in app/services/assessment_draft_service.py so
draft generation never hard-fails just because the model is unavailable.
"""

import json
import os
from typing import Any

import requests

from app.ai import provider
from app.ai.metering import metered_post
import truststore
from dotenv import load_dotenv

load_dotenv()
truststore.inject_into_ssl()

# The configured provider (OpenAI or OpenRouter): app/ai/provider.py.
OPENROUTER_API_KEY = provider.API_KEY
OPENROUTER_MODEL = provider.MODEL
OPENROUTER_URL = provider.CHAT_COMPLETIONS_URL
# Seconds to wait for a model reply. Free-tier models can be slow; raise
# this (or pick a faster OPENROUTER_MODEL) if requests time out.
OPENROUTER_TIMEOUT_SECONDS = float(os.getenv("OPENROUTER_TIMEOUT_SECONDS") or 120)


class DraftNarrativeError(Exception):
    pass


def _clean_json_response(text: str) -> str:
    text = text.strip()
    if text.startswith("```json"):
        text = text[7:]
    elif text.startswith("```"):
        text = text[3:]
    if text.endswith("```"):
        text = text[:-3]
    return text.strip()


def _build_prompt(context: dict[str, Any]) -> str:
    return f"""
You are an AI financial-crime risk analyst writing the narrative
sections of a decision-ready risk assessment for a bank's risk
committee. You are given the FULL structured data already computed for
this assessment (business profile, risk factors, an inherent-risk score
that was calculated deterministically -- NOT by you --, mapped controls
and their effectiveness, a residual-risk score, and evidence gaps).

Your job is ONLY to write prose about this data and flag uncertainty.
Never invent facts, never state a different number than what is given,
never recalculate or contradict the scores/bands provided.

STRUCTURED ASSESSMENT DATA:
{json.dumps(context, indent=2, default=str)}

Return ONLY valid JSON with this exact shape:
{{
  "executive_summary": "2-4 sentence summary of the change, its overall risk, and the recommended path forward.",
  "business_change_description": "1-3 sentence plain-language description of what is changing.",
  "risk_statements": [
    {{"category": "CATEGORY_NAME", "statement": "One clear sentence stating the risk for this category, referencing its score/band and rationale."}}
  ],
  "assumptions": ["explicit assumption made because information was incomplete, or an empty list"],
  "unresolved_questions": ["a specific open question a reviewer should ask, or an empty list"],
  "low_confidence_items": ["a specific piece of information that is uncertain/unverified, or an empty list"],
  "unsupported_conclusions": ["a conclusion in the data that lacks direct evidence, or an empty list"],
  "analyst_recommendation": "1-3 sentences suggesting an outcome (Approve / Approve with Conditions / Escalate / Reject) for the human reviewers to consider, consistent with the given residual risk band and any open gaps/conditions. Phrase it as a suggestion (e.g. 'Suggested outcome: ...'), never as a decision that has been made."
}}

Rules:
- One risk_statements entry per applicable risk category given in the data, in the order given.
- Only use information present in the provided data. Do not invent facts.
- Return raw JSON only. No markdown code fences, no commentary.
"""


def generate_draft_narrative(context: dict[str, Any]) -> dict[str, Any]:
    if not OPENROUTER_API_KEY:
        raise DraftNarrativeError(
            f"{provider.KEY_NAME} is not configured. Add it to backend/.env"
        )

    prompt = _build_prompt(context)

    headers = {
        "Authorization": f"Bearer {OPENROUTER_API_KEY}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": OPENROUTER_MODEL,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.2,
        "response_format": {"type": "json_object"},
    }

    try:
        response = metered_post("DRAFT_NARRATIVE", OPENROUTER_URL, headers=headers, json=payload, timeout=OPENROUTER_TIMEOUT_SECONDS)
    except requests.RequestException as exc:
        raise DraftNarrativeError(f"OpenRouter request failed: {exc}") from exc

    if response.status_code != 200:
        raise DraftNarrativeError(
            f"OpenRouter API returned an error.\nStatus: {response.status_code}\n"
            f"Response: {response.text}"
        )

    try:
        response_data = response.json()
        content = response_data["choices"][0]["message"].get("content")
    except (ValueError, KeyError, IndexError, TypeError) as exc:
        raise DraftNarrativeError(f"Unexpected OpenRouter response format: {exc}") from exc

    if not content:
        raise DraftNarrativeError("OpenRouter returned an empty model response.")

    raw_text = _clean_json_response(content)

    try:
        result = json.loads(raw_text)
    except json.JSONDecodeError:
        start, end = raw_text.find("{"), raw_text.rfind("}")
        if start == -1 or end == -1 or end <= start:
            raise DraftNarrativeError(f"The model returned invalid JSON:\n{content}")
        try:
            result = json.loads(raw_text[start : end + 1])
        except json.JSONDecodeError as exc:
            raise DraftNarrativeError(f"The model returned invalid JSON:\n{content}") from exc

    result.setdefault("executive_summary", "")
    result.setdefault("business_change_description", "")
    result.setdefault("risk_statements", [])
    result.setdefault("assumptions", [])
    result.setdefault("unresolved_questions", [])
    result.setdefault("low_confidence_items", [])
    result.setdefault("unsupported_conclusions", [])
    result.setdefault("analyst_recommendation", "")

    # Stage 19 (Explainability): an automated recommendation must never
    # read as a final approval/rejection, whatever the model returned.
    from app.services.advisory import ensure_advisory_wording

    result["analyst_recommendation"] = ensure_advisory_wording(result["analyst_recommendation"])

    return result

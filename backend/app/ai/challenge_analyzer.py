"""
AI challenge analysis: look at an assessment the way a compliance challenger
would and raise gaps the rule-based checks do not.

The model is given a structured snapshot (risks, mapped controls and their
assessments, the gaps the rules already found, and document excerpts) and
returns findings. Everything it returns is checked here: references must
point at real risks, controls and documents, and a quote is kept only if it
appears verbatim in the cited document. A CONTRADICTION that cannot be
backed by a verified quote is dropped. The output is advisory -- a reviewer
confirms or dismisses each finding.
"""

import json
from typing import Any

import requests
from dotenv import load_dotenv
import truststore

from app.ai import provider
from app.ai.evidence_checker import quote_in_text
from app.ai.metering import metered_post

load_dotenv()
truststore.inject_into_ssl()

CATEGORIES = ("MISSING_CONTROL", "CONTROL_MISMATCH", "EVIDENCE_GAP", "CONTRADICTION", "COVERAGE", "OTHER")
SEVERITIES = ("LOW", "MEDIUM", "HIGH")
MAX_FINDINGS = 10
MAX_DOCUMENT_CHARS = 3000
MAX_TOTAL_DOCUMENT_CHARS = 15000


def analyze_assessment_gaps(snapshot: dict[str, Any]) -> dict[str, Any] | None:
    """
    `snapshot` keys: assessment, risk_factors, controls, existing_gaps,
    documents ([{id, filename, text}]). Returns {"findings": [...],
    "model": str | None}, or None when the analysis could not run (no key,
    provider error, unusable answer). An empty findings list is a valid
    "nothing further to raise".
    """

    if not provider.API_KEY:
        return None

    documents = snapshot.get("documents") or []
    excerpts: dict[int, str] = {}
    blocks: list[str] = []
    budget = MAX_TOTAL_DOCUMENT_CHARS
    for document in documents:
        if budget <= 0:
            break
        text = (document.get("text") or "")[: min(MAX_DOCUMENT_CHARS, budget)]
        budget -= len(text)
        excerpts[document["id"]] = text
        blocks.append(f'<document id="{document["id"]}" name="{document.get("filename", "")}">\n{text}\n</document>')

    structured = {key: snapshot.get(key) for key in ("assessment", "risk_factors", "controls", "existing_gaps")}

    prompt = f"""You are a financial crime compliance challenger. Review the assessment below and
raise gaps a careful second-line reviewer would challenge. Be specific and conservative:
raise a finding only when the material supports it.

ASSESSMENT SNAPSHOT (JSON):
{json.dumps(structured, indent=2, default=str)}

DOCUMENTS:
{chr(10).join(blocks) if blocks else "(none uploaded)"}

Look for:
- MISSING_CONTROL: a risk with no control, or an obvious risk-driver in the material with no control for it.
- CONTROL_MISMATCH: a mapped control that does not actually address the risk it is mapped to.
- EVIDENCE_GAP: a conclusion or control rating the evidence does not support.
- CONTRADICTION: the documents contradict the assessment or each other (quote the contradicting passage).
- COVERAGE: a control that covers only part of the risk, or a single control relied on for a high risk.
- OTHER: anything else a challenger would raise.

Rules:
- Do NOT repeat a gap already listed under existing_gaps; add something new or sharper.
- Use only ids that appear in the snapshot/documents. Leave an id null if it does not apply.
- "quote" must be copied exactly from the cited document (under 300 characters); omit it otherwise.
- Raise at most {MAX_FINDINGS} findings, most important first. If there is nothing further, return an empty list.

Respond with ONLY a JSON object, no additional text:
{{"findings": [{{"category": "MISSING_CONTROL | CONTROL_MISMATCH | EVIDENCE_GAP | CONTRADICTION | COVERAGE | OTHER", "severity": "LOW | MEDIUM | HIGH", "title": "short title", "detail": "what is wrong and why it matters", "risk_factor_id": 1, "control_id": 1, "document_id": 1, "quote": "exact passage"}}]}}"""

    try:
        response = metered_post(
            "AI_CHALLENGE_ANALYSIS",
            provider.CHAT_COMPLETIONS_URL,
            headers={
                "Authorization": f"Bearer {provider.API_KEY}",
                "Content-Type": "application/json",
            },
            json={
                "model": provider.MODEL,
                "messages": [{"role": "user", "content": prompt}],
                "temperature": 0.2,
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
        print(f"AI challenge analysis failed: {exc}")
        return None

    if not isinstance(payload, dict) or not isinstance(payload.get("findings"), list):
        return None

    risk_ids = {r.get("id") for r in snapshot.get("risk_factors") or []}
    control_ids = {c.get("id") for c in snapshot.get("controls") or []}

    findings: list[dict[str, Any]] = []
    for item in payload["findings"]:
        if not isinstance(item, dict):
            continue
        category = str(item.get("category") or "").upper()
        category = category if category in CATEGORIES else "OTHER"
        severity = str(item.get("severity") or "").upper()
        severity = severity if severity in SEVERITIES else "MEDIUM"
        title = str(item.get("title") or "").strip()[:255]
        detail = str(item.get("detail") or "").strip()[:1500]
        if not title or not detail:
            continue

        def ref(key: str, valid: set) -> int | None:
            try:
                value = int(item.get(key))
            except (TypeError, ValueError):
                return None
            return value if value in valid else None

        document_id = ref("document_id", set(excerpts))
        quote = str(item.get("quote") or "").strip()
        verified = bool(document_id and quote_in_text(quote, excerpts[document_id]))
        if category == "CONTRADICTION" and not verified:
            continue

        findings.append(
            {
                "category": category,
                "severity": severity,
                "title": title,
                "detail": detail,
                "risk_factor_id": ref("risk_factor_id", risk_ids),
                "control_id": ref("control_id", control_ids),
                "document_id": document_id if verified else None,
                "quote": quote[:600] if verified else None,
            }
        )
        if len(findings) >= MAX_FINDINGS:
            break

    return {"findings": findings, "model": model}

"""
AI-driven likelihood and impact estimation for risk factors.

Uses an LLM to evaluate the likelihood of a risk occurring and the
potential impact, mapping to the configured scales (typically 1-5).
"""

import json
import os
from typing import Any

import requests
from dotenv import load_dotenv
import truststore

from app.ai import provider
from app.ai.metering import metered_post

load_dotenv()
truststore.inject_into_ssl()

# The configured provider (OpenAI or OpenRouter): app/ai/provider.py.
OPENROUTER_API_KEY = provider.API_KEY
OPENROUTER_MODEL = provider.MODEL
OPENROUTER_URL = provider.CHAT_COMPLETIONS_URL
OPENROUTER_TIMEOUT_SECONDS = float(os.getenv("OPENROUTER_TIMEOUT_SECONDS") or 120)


def _fallback(reason: str) -> dict[str, Any]:
    """
    Neutral mid-scale placeholder, explicitly marked as such so callers
    never present it to an analyst as a model-produced suggestion.
    """

    return {"likelihood": 3, "impact": 3, "reasoning": reason, "fallback": True}


def estimate_likelihood_impact(
    risk_category: str,
    risk_rationale: str,
    misuse_scenario: str,
    assessment_context: dict[str, Any] | None = None,
    likelihood_scale: list[dict[str, Any]] | None = None,
    impact_scale: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """
    Estimate likelihood (1-5) and impact (1-5) for a specific risk factor
    based on the risk category, rationale, and misuse scenario.

    Args:
        risk_category: The risk category name (e.g., "CUSTOMER", "OPERATIONAL")
        risk_rationale: Why this category applies to the change
        misuse_scenario: How the change could be misused
        assessment_context: Optional context about the assessment/change
        likelihood_scale: Scale definition (default 1-5: Rare to Almost Certain)
        impact_scale: Scale definition (default 1-5: Negligible to Severe)

    Returns:
        {"likelihood": 1-5, "impact": 1-5, "reasoning": str, "fallback": bool}

        `fallback` is True when the values are the neutral mid-scale stub
        rather than a real model estimate (no API key, or the call
        failed). Callers must surface that rather than presenting a stub
        as a suggestion -- an analyst pre-filled with an invented 3x3 they
        believe came from the model is worse off than with no suggestion.
    """

    if not OPENROUTER_API_KEY:
        return _fallback("No OPENROUTER_API_KEY is configured, so no estimate could be made.")

    if likelihood_scale is None:
        likelihood_scale = [
            {"value": 1, "label": "Rare"},
            {"value": 2, "label": "Unlikely"},
            {"value": 3, "label": "Possible"},
            {"value": 4, "label": "Likely"},
            {"value": 5, "label": "Almost Certain"},
        ]

    if impact_scale is None:
        impact_scale = [
            {"value": 1, "label": "Negligible"},
            {"value": 2, "label": "Minor"},
            {"value": 3, "label": "Moderate"},
            {"value": 4, "label": "Major"},
            {"value": 5, "label": "Severe"},
        ]

    context_block = ""
    if assessment_context:
        context_block = f"\nASSESSMENT CONTEXT:\n{json.dumps(assessment_context, indent=2, default=str)}\n"

    likelihood_options = "\n".join(
        f"  {item['value']}: {item['label']}"
        for item in sorted(likelihood_scale, key=lambda x: x["value"])
    )

    impact_options = "\n".join(
        f"  {item['value']}: {item['label']}"
        for item in sorted(impact_scale, key=lambda x: x["value"])
    )

    prompt = f"""You are a risk assessment expert. Based on the risk factor details below,
estimate the likelihood that this risk will occur and the potential impact if it does.

RISK CATEGORY: {risk_category}

RISK RATIONALE: {risk_rationale}

MISUSE SCENARIO: {misuse_scenario}
{context_block}

Likelihood Scale (how likely is this risk to occur?):
{likelihood_options}

Impact Scale (if this risk occurs, what would be the impact?):
{impact_options}

Respond with ONLY a JSON object in this format, no additional text:
{{"likelihood": <1-5>, "impact": <1-5>, "reasoning": "brief explanation"}}"""

    try:
        response = metered_post(
            "LIKELIHOOD_IMPACT_SUGGESTION",
            OPENROUTER_URL,
            headers={
                "Authorization": f"Bearer {OPENROUTER_API_KEY}",
                "Content-Type": "application/json",
            },
            json={
                "model": OPENROUTER_MODEL,
                "messages": [{"role": "user", "content": prompt}],
                "temperature": 0.3,
            },
            timeout=OPENROUTER_TIMEOUT_SECONDS,
        )

        response.raise_for_status()
        data = response.json()

        if not data.get("choices"):
            return _fallback("The model returned no choices.")

        content = data["choices"][0].get("message", {}).get("content", "")

        # Extract JSON from response
        try:
            # Try to parse as JSON directly first
            result = json.loads(content)
        except json.JSONDecodeError:
            # Try to extract JSON from markdown code blocks
            if "```json" in content:
                json_str = content.split("```json")[1].split("```")[0].strip()
            elif "```" in content:
                json_str = content.split("```")[1].split("```")[0].strip()
            else:
                json_str = content

            result = json.loads(json_str)

        # Validate and clamp values to valid range
        likelihood = int(result.get("likelihood", 3))
        impact = int(result.get("impact", 3))

        max_likelihood = max((item["value"] for item in likelihood_scale), default=5)
        max_impact = max((item["value"] for item in impact_scale), default=5)

        likelihood = max(1, min(likelihood, max_likelihood))
        impact = max(1, min(impact, max_impact))

        return {
            "likelihood": likelihood,
            "impact": impact,
            "reasoning": str(result.get("reasoning") or "").strip(),
            "fallback": False,
        }

    except (requests.RequestException, json.JSONDecodeError, KeyError, ValueError) as e:
        print(f"Likelihood/impact estimation failed: {e}")
        return _fallback(f"Estimation failed: {e}")

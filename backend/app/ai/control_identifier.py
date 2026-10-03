"""
AI-driven control identification and assessment for risk factors.

Uses an LLM to identify appropriate controls from the control library
that mitigate identified risks, and optionally assess their design
adequacy and operating effectiveness.
"""

import json
import os
from typing import Any

import requests
from dotenv import load_dotenv
import truststore

from app.ai import provider
from app.ai.metering import metered_post
from app.control_engine.library import CONTROL_LIBRARY

load_dotenv()
truststore.inject_into_ssl()

# The configured provider (OpenAI or OpenRouter): app/ai/provider.py.
OPENROUTER_API_KEY = provider.API_KEY
OPENROUTER_MODEL = provider.MODEL
OPENROUTER_URL = provider.CHAT_COMPLETIONS_URL
OPENROUTER_TIMEOUT_SECONDS = float(os.getenv("OPENROUTER_TIMEOUT_SECONDS") or 120)


def identify_applicable_controls(
    risk_category: str,
    risk_rationale: str,
    misuse_scenario: str,
    assessment_context: dict[str, Any] | None = None,
) -> list[str]:
    """
    Identify which controls from the control library would be appropriate
    to mitigate an identified risk factor.

    Args:
        risk_category: The risk category (e.g., "CUSTOMER", "OPERATIONAL")
        risk_rationale: Why this risk applies to the change
        misuse_scenario: How the risk could be misused
        assessment_context: Optional context about the assessment/change

    Returns:
        List of control_type codes from CONTROL_LIBRARY
    """

    if not OPENROUTER_API_KEY:
        # Fallback: return empty list if no API key configured
        return []

    context_block = ""
    if assessment_context:
        context_block = f"\nASSESSMENT CONTEXT:\n{json.dumps(assessment_context, indent=2, default=str)}\n"

    control_list = "\n".join(f"- {control}" for control in CONTROL_LIBRARY)

    prompt = f"""You are a financial crime risk control expert. Based on the identified risk
factor below, recommend which controls from the available library would be most appropriate
to mitigate this risk.

RISK CATEGORY: {risk_category}

RISK RATIONALE: {risk_rationale}

MISUSE SCENARIO: {misuse_scenario}
{context_block}

Available Controls:
{control_list}

Recommend 2-4 controls that would be most effective at mitigating this specific risk.
These are the primary control types the organization should implement or enhance.

Respond with ONLY a JSON object in this format, no additional text:
{{"recommended_controls": ["CONTROL_TYPE_1", "CONTROL_TYPE_2"], "reasoning": "brief explanation of why these controls address the risk"}}"""

    try:
        response = metered_post(
            "CONTROL_IDENTIFICATION",
            OPENROUTER_URL,
            headers={
                "Authorization": f"Bearer {OPENROUTER_API_KEY}",
                "Content-Type": "application/json",
            },
            json={
                "model": OPENROUTER_MODEL,
                "messages": [{"role": "user", "content": prompt}],
                "temperature": 0.5,
            },
            timeout=OPENROUTER_TIMEOUT_SECONDS,
        )

        response.raise_for_status()
        data = response.json()

        if not data.get("choices"):
            return []

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

        controls = result.get("recommended_controls", [])

        # Validate that returned controls are in the library
        valid_controls = [c for c in controls if c in CONTROL_LIBRARY]

        return valid_controls

    except (requests.RequestException, json.JSONDecodeError, KeyError, ValueError) as e:
        # Fallback if API call fails
        print(f"Control identification failed: {e}")
        return []


def assess_control_design(
    control_type: str,
    risk_rationale: str,
    assessment_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """
    Use AI to assess the design adequacy of a control for a specific risk.

    Args:
        control_type: The control type being assessed
        risk_rationale: Why the risk applies
        assessment_context: Optional context about the assessment/change

    Returns:
        {"design_adequacy": "DESIGN_ADEQUATE" | "DESIGN_INADEQUATE" | "NOT_ASSESSED",
         "rationale": "explanation"}
    """

    if not OPENROUTER_API_KEY:
        return {"design_adequacy": "NOT_ASSESSED", "rationale": "API not configured"}

    context_block = ""
    if assessment_context:
        context_block = f"\nASSESSMENT CONTEXT:\n{json.dumps(assessment_context, indent=2, default=str)}\n"

    prompt = f"""You are a financial crime control design expert. Assess whether the
following control is adequately designed to mitigate the identified risk.

CONTROL TYPE: {control_type}

RISK CONTEXT: {risk_rationale}
{context_block}

Based on the control's typical characteristics and the specific risk context,
determine if this control's design is adequate to address the risk.

Consider:
1. Does the control directly address the root cause of the risk?
2. Is the control scope and frequency appropriate?
3. Are there any design gaps or limitations?

Respond with ONLY a JSON object in this format, no additional text:
{{"design_adequacy": "DESIGN_ADEQUATE" | "DESIGN_INADEQUATE", "rationale": "brief explanation"}}"""

    try:
        response = metered_post(
            "CONTROL_DESIGN_ASSESSMENT",
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
            return {"design_adequacy": "NOT_ASSESSED", "rationale": "Assessment failed"}

        content = data["choices"][0].get("message", {}).get("content", "")

        # Extract JSON from response
        try:
            result = json.loads(content)
        except json.JSONDecodeError:
            if "```json" in content:
                json_str = content.split("```json")[1].split("```")[0].strip()
            elif "```" in content:
                json_str = content.split("```")[1].split("```")[0].strip()
            else:
                json_str = content

            result = json.loads(json_str)

        design_adequacy = result.get("design_adequacy", "NOT_ASSESSED")
        # Validate the value
        if design_adequacy not in ["DESIGN_ADEQUATE", "DESIGN_INADEQUATE", "NOT_ASSESSED"]:
            design_adequacy = "NOT_ASSESSED"

        return {
            "design_adequacy": design_adequacy,
            "rationale": result.get("rationale", "")
        }

    except (requests.RequestException, json.JSONDecodeError, KeyError, ValueError) as e:
        print(f"Control design assessment failed: {e}")
        return {"design_adequacy": "NOT_ASSESSED", "rationale": f"Assessment error: {str(e)}"}

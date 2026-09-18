import json
import os
from typing import Any
import truststore


import requests
from dotenv import load_dotenv


# Load variables from backend/.env
load_dotenv()
truststore.inject_into_ssl()


# OpenRouter configuration
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY")

OPENROUTER_MODEL = os.getenv(
    "OPENROUTER_MODEL",
    "openrouter/free",
)

OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
print(f"OpenRouter model configured: {OPENROUTER_MODEL}")

# Risk dimensions used by the application
RISK_DIMENSIONS = [
    "CUSTOMER",
    "OPERATIONAL",
    "FINANCIAL",
    "COMPLIANCE",
    "TECHNOLOGY",
    "THIRD_PARTY",
]


def _build_prompt(
    assessment: dict[str, Any],
    intelligence: Any = None,
) -> str:
    """
    Build the prompt sent to the OpenRouter model.
    """

    intelligence_data: Any = {}

    if intelligence is not None:

        if isinstance(intelligence, dict):

            intelligence_data = intelligence

        elif hasattr(intelligence, "__dict__"):

            intelligence_data = {
                key: value
                for key, value in vars(intelligence).items()
                if not key.startswith("_")
            }

        else:

            intelligence_data = str(intelligence)

    return f"""
You are an AI-powered third-party risk analyst
supporting a banking risk assessment process.

Analyze the following assessment and business intelligence.

ASSESSMENT:
{json.dumps(assessment, indent=2, default=str)}

BUSINESS INTELLIGENCE:
{json.dumps(intelligence_data, indent=2, default=str)}

Evaluate the third party across exactly these six
risk dimensions:

1. CUSTOMER
2. OPERATIONAL
3. FINANCIAL
4. COMPLIANCE
5. TECHNOLOGY
6. THIRD_PARTY

For each dimension:

- Provide a risk score from 0 to 100.
- 0 means very low risk.
- 100 means very high risk.
- Assign a severity:
  - LOW: 0-39
  - MEDIUM: 40-59
  - HIGH: 60-79
  - CRITICAL: 80-100
- Provide a concise explanation.
- Provide evidence from the supplied information.
- Do not invent facts.

Return ONLY valid JSON.

Use exactly this structure:

{{
  "risks": [
    {{
      "dimension": "CUSTOMER",
      "score": 0,
      "severity": "LOW",
      "reason": "Explanation of the risk.",
      "evidence": [
        "Supporting evidence."
      ]
    }},
    {{
      "dimension": "OPERATIONAL",
      "score": 0,
      "severity": "LOW",
      "reason": "Explanation of the risk.",
      "evidence": [
        "Supporting evidence."
      ]
    }},
    {{
      "dimension": "FINANCIAL",
      "score": 0,
      "severity": "LOW",
      "reason": "Explanation of the risk.",
      "evidence": [
        "Supporting evidence."
      ]
    }},
    {{
      "dimension": "COMPLIANCE",
      "score": 0,
      "severity": "LOW",
      "reason": "Explanation of the risk.",
      "evidence": [
        "Supporting evidence."
      ]
    }},
    {{
      "dimension": "TECHNOLOGY",
      "score": 0,
      "severity": "LOW",
      "reason": "Explanation of the risk.",
      "evidence": [
        "Supporting evidence."
      ]
    }},
    {{
      "dimension": "THIRD_PARTY",
      "score": 0,
      "severity": "LOW",
      "reason": "Explanation of the risk.",
      "evidence": [
        "Supporting evidence."
      ]
    }}
  ]
}}
"""


def _clean_json_response(text: str) -> str:
    """
    Remove Markdown code fences if the model returns
    JSON inside ```json ... ```
    """

    text = text.strip()

    if text.startswith("```json"):
        text = text[7:]

    elif text.startswith("```"):
        text = text[3:]

    if text.endswith("```"):
        text = text[:-3]

    return text.strip()


def _validate_risk_result(
    result: dict[str, Any],
) -> dict[str, Any]:
    """
    Validate and normalize one risk result.
    """

    dimension = str(
        result.get("dimension", "")
    ).upper()

    if dimension not in RISK_DIMENSIONS:

        raise ValueError(
            f"Invalid risk dimension returned by model: "
            f"{dimension}"
        )

    try:

        score = float(
            result.get("score", 0)
        )

    except (TypeError, ValueError) as exc:

        raise ValueError(
            f"Invalid score returned for {dimension}"
        ) from exc

    # Keep score between 0 and 100.
    score = max(
        0.0,
        min(100.0, score),
    )

    # Derive severity from the score.
    # This prevents the AI from returning an inconsistent
    # severity value.
    if score >= 80:

        severity = "CRITICAL"

    elif score >= 60:

        severity = "HIGH"

    elif score >= 40:

        severity = "MEDIUM"

    else:

        severity = "LOW"

    reason = str(
        result.get(
            "reason",
            "No explanation was provided.",
        )
    )

    evidence = result.get(
        "evidence",
        [],
    )

    if not isinstance(evidence, list):

        evidence = [
            str(evidence)
        ]

    evidence = [
        str(item)
        for item in evidence
    ]

    return {
        "dimension": dimension,
        "score": round(score, 2),
        "severity": severity,
        "reason": reason,
        "evidence": evidence,
    }


def analyze_risks(
    assessment: dict[str, Any],
    intelligence: Any = None,
) -> list[dict[str, Any]]:
    """
    Analyze the assessment using OpenRouter.

    OpenRouter hosts the selected AI model.
    LangGraph calls this function from the
    identify_risks node.
    """

    # Make sure the key exists.
    if not OPENROUTER_API_KEY:

        raise RuntimeError(
            "OPENROUTER_API_KEY is not configured. "
            "Add it to backend/.env"
        )

    prompt = _build_prompt(
        assessment=assessment,
        intelligence=intelligence,
    )

    headers = {
        "Authorization": (
            f"Bearer {OPENROUTER_API_KEY}"
        ),
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
        "temperature": 0.2,
        "response_format": {
            "type": "json_object"
        },
    }   

    try:

        response = requests.post(
            OPENROUTER_URL,
            headers=headers,
            json=payload,
            timeout=120,
        )

    except requests.RequestException as exc:

        raise RuntimeError(
            f"OpenRouter request failed: {exc}"
        ) from exc

    # OpenRouter returned an HTTP error.
    if response.status_code != 200:

        raise RuntimeError(
            "OpenRouter API returned an error.\n"
            f"Status: {response.status_code}\n"
            f"Response: {response.text}"
        )

    # Parse OpenRouter response.
    try:

        response_data = response.json()

    except ValueError as exc:

        raise RuntimeError(
            "OpenRouter returned invalid JSON.\n"
            f"Response: {response.text}"
        ) from exc

    # Extract assistant message.
    try:

        assistant_message = (
            response_data[
                "choices"
            ][0][
                "message"
            ]
        )

    except (
        KeyError,
        IndexError,
        TypeError,
    ) as exc:

        raise RuntimeError(
            "Unexpected OpenRouter response format.\n"
            f"Response: {response_data}"
        ) from exc

    content = assistant_message.get(
        "content"
    )

    if not content:

        raise RuntimeError(
            "OpenRouter returned an empty model response."
        )

    # Remove ```json fences if present.
    raw_text = _clean_json_response(content)

    try:
        result_payload = json.loads(raw_text)

    except json.JSONDecodeError:
        # Fallback: extract the JSON object from the response
        start = raw_text.find("{")
        end = raw_text.rfind("}")

        if start == -1 or end == -1 or end <= start:
            raise RuntimeError(
                "The OpenRouter model returned invalid JSON.\n"
                f"Model response:\n{content}"
            )

        json_text = raw_text[start:end + 1]

        try:
            result_payload = json.loads(json_text)

        except json.JSONDecodeError as exc:
            raise RuntimeError(
                "The OpenRouter model returned invalid JSON.\n"
                f"Model response:\n{content}"
            ) from exc

    if not isinstance(
        result_payload,
        dict,
    ):

        raise RuntimeError(
            "Model response must be a JSON object."
        )

    risks = result_payload.get(
        "risks"
    )

    if not isinstance(
        risks,
        list,
    ):

        raise RuntimeError(
            "Model response does not contain "
            "a valid 'risks' array."
        )

    # Validate every risk result.
    validated_results = []

    for risk in risks:

        if not isinstance(
            risk,
            dict,
        ):
            continue

        validated_results.append(
            _validate_risk_result(risk)
        )

    # Check that all six dimensions exist.
    returned_dimensions = {
        result["dimension"]
        for result in validated_results
    }

    missing_dimensions = [
        dimension
        for dimension in RISK_DIMENSIONS
        if dimension not in returned_dimensions
    ]

    if missing_dimensions:

        raise RuntimeError(
            "Model did not return all required "
            "risk dimensions. "
            f"Missing: {', '.join(missing_dimensions)}"
        )

    # Return dimensions in application order.
    ordered_results = []

    for dimension in RISK_DIMENSIONS:

        for result in validated_results:

            if result["dimension"] == dimension:

                ordered_results.append(
                    result
                )

                break

    return ordered_results
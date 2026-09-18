from typing import Any


# Risk weights for each dimension.
# These should add up to 1.0.
RISK_WEIGHTS = {
    "CUSTOMER": 0.15,
    "OPERATIONAL": 0.20,
    "FINANCIAL": 0.20,
    "COMPLIANCE": 0.20,
    "TECHNOLOGY": 0.15,
    "THIRD_PARTY": 0.10,
}


def calculate_overall_score(results: list[dict[str, Any]]) -> float:
    """
    Calculate the overall risk score using deterministic application
    weights.

    Gemini provides the individual dimension scores.
    This function calculates the final weighted score.
    """

    if not results:
        return 0.0

    weighted_score = 0.0
    total_weight = 0.0

    for result in results:
        dimension = str(result.get("dimension", "")).upper()
        score = float(result.get("score", 0))

        weight = RISK_WEIGHTS.get(dimension)

        if weight is None:
            continue

        # Keep scores within the expected 0-100 range.
        score = max(0.0, min(100.0, score))

        weighted_score += score * weight
        total_weight += weight

    if total_weight == 0:
        return 0.0

    # Handles cases where only some dimensions are returned.
    overall_score = weighted_score / total_weight

    return round(overall_score, 2)


def determine_risk_level(score: float) -> str:
    """
    Convert the deterministic overall score into a risk level.
    """

    if score >= 80:
        return "CRITICAL"

    if score >= 60:
        return "HIGH"

    if score >= 40:
        return "MEDIUM"

    return "LOW"
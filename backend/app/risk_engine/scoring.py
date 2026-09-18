RISK_WEIGHTS = {
    "CUSTOMER": 0.18,
    "OPERATIONAL": 0.18,
    "FINANCIAL": 0.14,
    "COMPLIANCE": 0.23,
    "TECHNOLOGY": 0.17,
    "THIRD_PARTY": 0.10,
}


def calculate_overall_score(results):
    applicable_results = [
        result
        for result in results
        if result.dimension in RISK_WEIGHTS
    ]

    if not applicable_results:
        return 0

    total_weight = sum(
        RISK_WEIGHTS[result.dimension]
        for result in applicable_results
    )

    weighted_score = sum(
        result.score * RISK_WEIGHTS[result.dimension]
        for result in applicable_results
    )

    return round(weighted_score / total_weight, 2)


def determine_risk_level(score: float):
    if score >= 80:
        return "CRITICAL"

    if score >= 60:
        return "HIGH"

    if score >= 30:
        return "MEDIUM"

    return "LOW"
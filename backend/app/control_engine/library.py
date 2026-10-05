# R7.1: the control library. Fixed catalogue of control types the system
# maintains -- a Control instance's control_type must be one of these,
# same relationship as RiskFactor.category is to RISK_CATEGORIES.
CONTROL_LIBRARY = [
    "KYC_CUSTOMER_DUE_DILIGENCE",
    "ENHANCED_DUE_DILIGENCE",
    "BENEFICIAL_OWNERSHIP_VERIFICATION",
    "SANCTIONS_SCREENING",
    "TRANSACTION_MONITORING",
    "FRAUD_MONITORING",
    "TRANSACTION_LIMITS",
    "GEOGRAPHIC_RESTRICTIONS",
    "CUSTOMER_RISK_MONITORING",
    "VENDOR_DUE_DILIGENCE",
    "INVESTIGATION_PROCESSES",
    "REGULATORY_REPORTING",
]

# Suggested control types per risk category (RISK_CATEGORIES in
# app/schemas/risk_factor.py) -- a starting point offered when mapping
# controls to a newly identified risk, not an enforced restriction (any
# control in CONTROL_LIBRARY may be mapped to any risk).
SUGGESTED_CONTROLS_BY_CATEGORY: dict[str, list[str]] = {
    "PRODUCT_SERVICE_RISK": ["KYC_CUSTOMER_DUE_DILIGENCE", "TRANSACTION_MONITORING"],
    "CUSTOMER_SEGMENT_RISK": [
        "KYC_CUSTOMER_DUE_DILIGENCE",
        "ENHANCED_DUE_DILIGENCE",
        "CUSTOMER_RISK_MONITORING",
    ],
    "GEOGRAPHIC_RISK": [
        "SANCTIONS_SCREENING",
        "GEOGRAPHIC_RESTRICTIONS",
        "ENHANCED_DUE_DILIGENCE",
    ],
    "DELIVERY_CHANNEL_RISK": ["KYC_CUSTOMER_DUE_DILIGENCE", "FRAUD_MONITORING"],
    "TRANSACTION_ACTIVITY_RISK": ["TRANSACTION_MONITORING", "TRANSACTION_LIMITS"],
    "TECHNOLOGY_DEVELOPMENT_RISK": ["FRAUD_MONITORING", "INVESTIGATION_PROCESSES"],
    "THIRD_PARTY_VENDOR_RISK": ["VENDOR_DUE_DILIGENCE"],
    "OWNERSHIP_ENTITY_COMPLEXITY_RISK": ["BENEFICIAL_OWNERSHIP_VERIFICATION"],
    "FINANCIAL_CRIME_TYPOLOGY_RISK": [
        "SANCTIONS_SCREENING",
        "TRANSACTION_MONITORING",
        "INVESTIGATION_PROCESSES",
    ],
    "CONTROL_ENVIRONMENT_RISK": ["REGULATORY_REPORTING", "INVESTIGATION_PROCESSES"],
}


# How many controls an automatically identified risk gets, and how many of
# those the AI may add beyond the category's suggested set. The same few
# controls (transaction monitoring, vendor due diligence) otherwise end up
# mapped to every risk, which multiplies the assessment work without adding
# protection.
MAX_CONTROLS_PER_RISK = 3
MAX_AI_ADDITIONS = 1


def select_controls(category: str, ai_suggested: list[str]) -> list[str]:
    """
    The controls to map automatically to a risk of `category`: the category's
    suggested set first (deterministic and in the library's own order), then
    at most MAX_AI_ADDITIONS of the AI's recommendations that the set doesn't
    already contain, up to MAX_CONTROLS_PER_RISK in all.

    A category with no suggested set (or an unknown one) falls back to the
    AI's own top recommendations. With no AI answer at all the suggested set
    is returned on its own, so a risk is never left without a starting point.
    """

    valid_ai = [control for control in ai_suggested if control in CONTROL_LIBRARY]
    defaults = list(SUGGESTED_CONTROLS_BY_CATEGORY.get(category, []))

    if not defaults:
        return valid_ai[:MAX_CONTROLS_PER_RISK]

    additions = [control for control in valid_ai if control not in defaults][:MAX_AI_ADDITIONS]
    return (defaults + additions)[:MAX_CONTROLS_PER_RISK]

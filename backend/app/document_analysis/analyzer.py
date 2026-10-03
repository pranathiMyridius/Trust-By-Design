import logging
import re

from app.schemas.document_analysis import (
    DocumentAssessmentExtraction,
)


logger = logging.getLogger(__name__)


# R1.2: the 9 canonical change types, plus the legacy values already
# stored on existing assessments.
CHANGE_TYPE_MAP = {
    "new product & technology integration": "NEW_PRODUCT",
    "new product": "NEW_PRODUCT",
    "new service": "NEW_SERVICE",
    "new customer segment": "NEW_CUSTOMER_SEGMENT",
    "customer segment": "NEW_CUSTOMER_SEGMENT",
    "new geography": "NEW_GEOGRAPHY",
    "new country": "NEW_GEOGRAPHY",
    "process change": "PROCESS_CHANGE",
    "technology change": "TECHNOLOGY_CHANGE",
    "third party introduction": "THIRD_PARTY_INTRODUCTION",
    "transaction limit": "TRANSACTION_LIMIT_OR_CHANNEL_CHANGE",
    "channel change": "TRANSACTION_LIMIT_OR_CHANNEL_CHANGE",
    "periodic reassessment": "PERIODIC_REASSESSMENT",
    "reassessment": "PERIODIC_REASSESSMENT",
    "material change": "MATERIAL_CHANGE",
    "third party": "THIRD_PARTY",
    "third-party": "THIRD_PARTY",
    "vendor": "THIRD_PARTY",
}


def analyze_document_text(
    extracted_text: str,
) -> DocumentAssessmentExtraction:
    """
    R2.3: extract structured assessment fields from an uploaded
    document.

    Primary path is AI extraction (app.document_analysis.ai_extractor),
    using the same OpenRouter-backed model already used for risk
    analysis. If the AI call fails for any reason -- no API key
    configured, the provider is unreachable, a malformed response --
    this falls back to the original rule-based/regex extractor below
    so that uploading a document never breaks the intake flow.

    Either way, the caller still shows the result to the user for
    confirmation before anything is saved (R2.3's second half).
    """

    text = extracted_text.strip()

    try:
        from app.document_analysis.ai_extractor import extract_with_ai

        return extract_with_ai(text)

    except Exception as exc:
        logger.warning(
            "AI document extraction failed; falling back to the "
            "rule-based extractor. Reason: %s",
            exc,
        )

        return _analyze_document_text_rule_based(text)


def _analyze_document_text_rule_based(
    text: str,
) -> DocumentAssessmentExtraction:
    """
    Fallback extractor used only when AI extraction is unavailable.
    Regex/keyword based, tuned to the sample intake document format
    this app was originally built against.
    """

    title = _extract_project_name(text)

    change_type = _extract_change_type(text)

    business_description = _extract_description(text)

    countries = _extract_countries(text)

    vendors = _extract_vendors(text)

    data_shared = _extract_data_shared(text)

    return DocumentAssessmentExtraction(
        title=title,
        change_type=change_type,
        business_description=business_description,
        evidence=text,
        business_line=_extract_business_line(text),
        product_or_service_name=_extract_value(text, "Product Name:")
        or _extract_value(text, "Service Name:"),
        business_owner=_extract_value(text, "Business Owner:"),
        legal_entity=_extract_value(text, "Legal Entity:"),
        transaction_types=_extract_value(text, "Transaction Types:"),
        expected_launch_date=_extract_value(text, "Launch Date:")
        or _extract_value(text, "Implementation Date:"),
        submitted_by=_extract_value(text, "Submitted By:"),
        channels=_extract_channels(text),
        countries=countries,
        customer_segments=_extract_customer_segments(text),
        transaction_volume=_extract_value(
            text,
            "Projected Volume:",
        ),
        average_transaction_size=_extract_value(
            text,
            "Average Transaction Size:",
        ),
        maximum_transaction_limit=_extract_value(
            text,
            "Maximum Daily Single Transaction Limit:",
        ),
        third_party_vendors=vendors,
        data_shared=data_shared,
        technologies=_extract_technologies(text),
        regulatory_considerations=_extract_regulations(text),
        existing_controls=_extract_controls(text),
        additional_risk_factors=_extract_risk_factors(text),
        extraction_method="RULES",
    )


def _extract_project_name(text: str) -> str:

    match = re.search(
        r"Project Name:\s*(.+)",
        text,
        re.IGNORECASE,
    )

    if match:
        return match.group(1).strip()

    return "Untitled Assessment"


def _extract_change_type(text: str) -> str:

    match = re.search(
        r"Initiative Type:\s*(.+)",
        text,
        re.IGNORECASE,
    )

    if match:
        initiative_type = (
            match.group(1)
            .strip()
            .lower()
        )

        for key, value in CHANGE_TYPE_MAP.items():
            if key in initiative_type:
                return value

    return "NEW_PRODUCT"


def _extract_description(text: str) -> str:

    match = re.search(
        r"Description:\s*(.+?)(?=\n\d+\.|\n2\.)",
        text,
        re.IGNORECASE | re.DOTALL,
    )

    if match:
        return " ".join(
            match.group(1).split()
        )

    return "Business change extracted from uploaded document."


def _extract_business_line(text: str):

    return _extract_value(
        text,
        "Business Line:",
    )


def _extract_channels(text: str):

    value = _extract_value(
        text,
        "Channels:",
    )

    if not value:
        return []

    return [
        item.strip()
        for item in re.split(
            r",| and ",
            value,
        )
        if item.strip()
    ]


def _extract_countries(text: str):

    match = re.search(
        r"Target Destination Jurisdictions:\s*(.+)",
        text,
        re.IGNORECASE,
    )

    if not match:
        return []

    value = match.group(1).strip()

    return [
        item.strip()
        for item in re.split(
            r",| and ",
            value,
        )
        if item.strip()
    ]


def _extract_customer_segments(text: str):

    value = _extract_value(
        text,
        "Target Customer Segment:",
    )

    if not value:
        return []

    return [
        item.strip()
        for item in re.split(
            r",| and ",
            value,
        )
        if item.strip()
    ]


def _extract_vendors(text: str):

    value = _extract_value(
        text,
        "Primary Vendor:",
    )

    if not value:
        return []

    vendor = value.split("(")[0].strip()

    return [vendor]


def _extract_data_shared(text: str):

    match = re.search(
        r"Data Sharing:\s*(.+)",
        text,
        re.IGNORECASE,
    )

    if not match:
        return []

    value = match.group(1).strip()

    value = value.replace(
        " sent to vendor servers via JSON payloads.",
        "",
    )

    return [
        item.strip()
        for item in re.split(
            r",| and ",
            value,
        )
        if item.strip()
    ]


def _extract_technologies(text: str):

    technologies = []

    keywords = [
        "REST APIs",
        "AWS",
        "e-KYC",
        "API",
        "JSON",
        "Mobile Application",
    ]

    for keyword in keywords:
        if keyword.lower() in text.lower():
            technologies.append(keyword)

    return list(dict.fromkeys(technologies))


def _extract_regulations(text: str):

    regulations = []

    keywords = [
        "OFAC",
        "UN watchlists",
        "EU watchlists",
        "PEP",
        "cross-border",
        "higher-risk jurisdictions",
    ]

    for keyword in keywords:
        if keyword.lower() in text.lower():
            regulations.append(keyword)

    return regulations


def _extract_controls(text: str):

    controls = []

    control_patterns = [
        "corporate registry check",
        "remote e-KYC",
        "Real-time API check",
        "transaction monitoring",
        "SOC 2 Type II",
        "audit logs",
    ]

    for pattern in control_patterns:
        if pattern.lower() in text.lower():
            controls.append(pattern)

    return controls


def _extract_risk_factors(text: str):

    risk_factors = []

    patterns = [
        ("cross-border payments", "Cross-border payments"),
        ("150,000 transactions", "High transaction volume"),
        ("third-party", "Third-party dependency"),
        ("FX conversion", "Foreign exchange exposure"),
        ("higher-risk jurisdictions", "Higher-risk jurisdictions"),
        ("non-face-to-face", "Remote digital onboarding"),
    ]

    lower_text = text.lower()

    for search_text, label in patterns:
        if search_text.lower() in lower_text:
            risk_factors.append(label)

    return risk_factors


def _extract_value(
    text: str,
    label: str,
):

    pattern = re.escape(label) + r"\s*(.+)"

    match = re.search(
        pattern,
        text,
        re.IGNORECASE,
    )

    if match:
        return match.group(1).strip()

    return None
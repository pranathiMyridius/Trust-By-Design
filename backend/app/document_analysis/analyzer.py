import re

from app.schemas.document_analysis import (
    DocumentAssessmentExtraction,
)


CHANGE_TYPE_MAP = {
    "new product": "NEW_PRODUCT",
    "new product & technology integration": "NEW_PRODUCT",
    "material change": "MATERIAL_CHANGE",
    "new geography": "NEW_GEOGRAPHY",
    "third party": "THIRD_PARTY",
    "third-party": "THIRD_PARTY",
    "vendor": "THIRD_PARTY",
}


def analyze_document_text(
    extracted_text: str,
) -> DocumentAssessmentExtraction:

    text = extracted_text.strip()

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
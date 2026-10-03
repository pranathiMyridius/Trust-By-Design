"""
R3.2 (Intake Validation and Structuring): detects contradictions between
the form responses an owner typed at intake (Assessment's R1.1 fields)
and the structured profile that was extracted/derived for this
assessment (AssessmentIntelligence). A conflict is flagged when both
sides have a non-blank value for the same concept but share no common
token -- e.g. the intake form says "Germany, Netherlands" but the
extracted profile says "France".

This is a transparent, explainable heuristic (comma-split, case
-insensitive token overlap) rather than fuzzy/semantic matching, so a
flagged conflict is always traceable to the two exact values compared.
"""

import re
from typing import Any

from sqlalchemy.orm import object_session

from app.models.assessment import Assessment
from app.models.assessment_intelligence import AssessmentIntelligence


def _tokens(value: str | None) -> set[str]:
    if not value:
        return set()
    return {
        token.strip().lower()
        for token in value.replace(";", ",").split(",")
        if token.strip()
    }


def _list_tokens(values: list[str] | None) -> set[str]:
    if not values:
        return set()
    return {value.strip().lower() for value in values if value and value.strip()}


# (form field on Assessment, extracted field on AssessmentIntelligence,
# human-readable label, whether the intelligence field is a list column).
_COMPARISONS: list[tuple[str, str, str, bool]] = [
    ("countries_jurisdictions", "countries", "Countries and jurisdictions", True),
    ("delivery_channels", "channels", "Delivery channels", True),
    ("customer_segment", "customer_segments", "Customer segment", True),
    ("expected_transaction_volume", "transaction_volume", "Expected transaction volume", False),
    ("third_party_vendor_usage", "third_party_vendors", "Third-party vendor usage", True),
    ("technology_process_changes", "technologies", "Technology or process changes", True),
]


def detect_inconsistencies(
    assessment: Assessment,
    intelligence: AssessmentIntelligence | None,
) -> list[dict[str, Any]]:
    # R3.2 / Stage 11 acceptance criteria: documents that contradict each
    # other are a conflict whether or not a profile exists yet.
    conflicts = detect_document_conflicts(assessment)

    if intelligence is None:
        return conflicts

    for form_field, intel_field, label, is_list in _COMPARISONS:
        form_value = getattr(assessment, form_field, None)
        form_tokens = _tokens(form_value)

        if is_list:
            intel_value = intelligence.get_list(intel_field)
            intel_tokens = _list_tokens(intel_value)
            intel_display = ", ".join(intel_value) if intel_value else None
        else:
            intel_value = getattr(intelligence, intel_field, None)
            intel_tokens = _tokens(intel_value)
            intel_display = intel_value

        if not form_tokens or not intel_tokens:
            # Nothing to compare -- one side is blank, not a contradiction.
            continue

        if form_tokens & intel_tokens:
            continue

        conflicts.append(
            {
                "field": label,
                "form_value": form_value,
                "extracted_value": intel_display,
                "message": (
                    f"{label}: intake form says {form_value!r} but the "
                    f"structured profile says {intel_display!r}."
                ),
            }
        )

    return conflicts


# ---------------------------------------------------------------------------
# R3.2: contradictions between uploaded documents.
#
# Document text is combined before AI extraction, so per-document values
# are otherwise lost. This reads the two figures the Stage 11 acceptance
# criteria call out -- transaction volume and value -- from each current
# document with transparent patterns, normalises them to a monthly
# amount, and flags documents that disagree by more than 10%. Every
# conflict quotes the passage it came from in each document.
# ---------------------------------------------------------------------------

_MULTIPLIERS = {"k": 1e3, "thousand": 1e3, "m": 1e6, "mn": 1e6, "million": 1e6, "bn": 1e9, "billion": 1e9}
_PER_MONTH = {"day": 30.0, "daily": 30.0, "week": 52 / 12, "weekly": 52 / 12, "month": 1.0, "monthly": 1.0, "year": 1 / 12, "annum": 1 / 12, "annually": 1 / 12, "yearly": 1 / 12}

_AMOUNT = r"(\d{1,3}(?:[,\s]\d{3})+|\d+(?:\.\d+)?)\s*(k|thousand|mn|m|million|bn|billion)?\b"
_PERIOD = r"(?:(?:per|a|each|/)\s*(day|week|month|year|annum)|(daily|weekly|monthly|annually|yearly))"

_VOLUME_PATTERN = re.compile(
    _AMOUNT + r"\s*(?:card\s+)?(?:transactions?|payments?|txns?|transfers?)\s*" + _PERIOD,
    re.IGNORECASE,
)
_VALUE_PATTERN = re.compile(
    r"(?:EUR|USD|GBP|CHF|€|\$|£)\s*" + _AMOUNT + r"\s*(?:in\s+)?(?:value\s+)?" + _PERIOD,
    re.IGNORECASE,
)

_TOLERANCE = 1.10


def _number(amount: str, unit: str | None) -> float | None:
    try:
        value = float(re.sub(r"[,\s]", "", amount))
    except ValueError:
        return None
    return value * _MULTIPLIERS.get((unit or "").lower(), 1.0)


def _monthly_figures(text: str, pattern: re.Pattern) -> list[tuple[float, str]]:
    figures = []
    for match in pattern.finditer(text or ""):
        amount, unit, period_word, period_adverb = match.groups()
        value = _number(amount, unit)
        period = (period_word or period_adverb or "").lower()
        if value is None or period not in _PER_MONTH:
            continue
        start, end = max(match.start() - 40, 0), min(match.end() + 20, len(text))
        figures.append((value * _PER_MONTH[period], " ".join(text[start:end].split())))
    return figures


def _format(value: float) -> str:
    return f"{value:,.0f}"


def detect_document_conflicts(assessment: Assessment) -> list[dict[str, Any]]:
    from app.models.assessment_document import AssessmentDocument

    db = object_session(assessment)
    if db is None or assessment.id is None:
        return []

    documents = (
        db.query(AssessmentDocument)
        .filter(
            AssessmentDocument.assessment_id == assessment.id,
            AssessmentDocument.is_current.is_(True),
        )
        .order_by(AssessmentDocument.id.asc())
        .all()
    )
    if len(documents) < 2:
        return []

    conflicts = []
    for label, pattern in (
        ("Transaction volume (per month)", _VOLUME_PATTERN),
        ("Transaction value (per month)", _VALUE_PATTERN),
    ):
        # One figure per document: the first it states.
        stated = []
        for document in documents:
            figures = _monthly_figures(document.extracted_text or "", pattern)
            if figures:
                stated.append((document, *figures[0]))

        if len(stated) < 2:
            continue

        low = min(stated, key=lambda row: row[1])
        high = max(stated, key=lambda row: row[1])
        if low[1] <= 0 or high[1] / low[1] <= _TOLERANCE:
            continue

        conflicts.append(
            {
                "field": label,
                "source": "DOCUMENTS",
                "form_value": f"{low[0].filename}: {_format(low[1])}",
                "extracted_value": f"{high[0].filename}: {_format(high[1])}",
                "documents": [
                    {"document_id": document.id, "filename": document.filename, "monthly_value": value, "passage": passage}
                    for document, value, passage in stated
                ],
                "message": (
                    f"{label}: {low[0].filename} says about {_format(low[1])} "
                    f"(\"{low[2]}\") but {high[0].filename} says about {_format(high[1])} "
                    f"(\"{high[2]}\")."
                ),
            }
        )

    return conflicts

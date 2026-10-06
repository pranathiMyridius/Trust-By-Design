from datetime import datetime
from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, field_validator

# R4.1: the 10 canonical risk categories the assessment must consider.
RISK_CATEGORIES = [
    "PRODUCT_SERVICE_RISK",
    "CUSTOMER_SEGMENT_RISK",
    "GEOGRAPHIC_RISK",
    "DELIVERY_CHANNEL_RISK",
    "TRANSACTION_ACTIVITY_RISK",
    "TECHNOLOGY_DEVELOPMENT_RISK",
    "THIRD_PARTY_VENDOR_RISK",
    "OWNERSHIP_ENTITY_COMPLEXITY_RISK",
    "FINANCIAL_CRIME_TYPOLOGY_RISK",
    "CONTROL_ENVIRONMENT_RISK",
]

# R4.2: the 12 detailed risk indicators an analyst can attach to a
# category.
RISK_INDICATORS = [
    "CROSS_BORDER_CAPABILITY",
    "CASH_ACCESS",
    "ANONYMITY",
    "MULTIPLE_CURRENCIES",
    "UNKNOWN_PARTY_PAYMENTS",
    "REMOTE_ONBOARDING",
    "TRANSACTION_VELOCITY",
    "TRANSACTION_VALUE_VOLUME",
    "COMPLEX_OWNERSHIP_STRUCTURES",
    "SANCTIONS_EXPOSURE",
    "THIRD_PARTY_DEPENDENCIES",
    "DATA_MONITORING_LIMITATIONS",
]


# ---------------------------------------------------------------------------
# OCC supervisory risk categories (OCC NR 96-2a, "Categories of Risk").
#
# This is a *reporting lens*, not a second scoring taxonomy. RISK_CATEGORIES
# above is a financial-crime inherent-risk taxonomy (product / customer /
# geography / channel / transaction -- the FATF & BSA model) and remains the
# only thing analysts rate, the only thing stored on RiskFactor.category, and
# the only input to the inherent-risk calculation. The nine OCC categories are
# derived from those same rated factors by the static map below, so no factor
# is rated twice and nothing here can change an assessment's score.
#
# The OCC text is explicit that these categories are *not* mutually exclusive
# -- "any product or service may expose the bank to multiple risks" -- so the
# map is deliberately many-to-many.
# ---------------------------------------------------------------------------

OCC_RISK_CATEGORIES = [
    "CREDIT",
    "INTEREST_RATE",
    "LIQUIDITY",
    "PRICE",
    "FOREIGN_EXCHANGE",
    "TRANSACTION",
    "COMPLIANCE",
    "STRATEGIC",
    "REPUTATION",
]

# Abridged from the source document, for display and report footnotes.
OCC_RISK_CATEGORY_DEFINITIONS: dict[str, str] = {
    "CREDIT": (
        "The risk to earnings or capital arising from an obligor's failure to "
        "meet the terms of any contract with the bank or otherwise fail to "
        "perform as agreed."
    ),
    "INTEREST_RATE": (
        "The risk to earnings or capital arising from movements in interest "
        "rates -- repricing, basis, yield curve and options risk."
    ),
    "LIQUIDITY": (
        "The risk to earnings or capital arising from a bank's inability to "
        "meet its obligations when they come due, without incurring "
        "unacceptable losses."
    ),
    "PRICE": (
        "The risk to earnings or capital arising from changes in the value of "
        "portfolios of financial instruments."
    ),
    "FOREIGN_EXCHANGE": (
        "The risk to earnings or capital arising from movement of foreign "
        "exchange rates, found in cross-border investing and operating "
        "activities."
    ),
    "TRANSACTION": (
        "The risk to earnings or capital arising from problems with service or "
        "product delivery -- a function of internal controls, information "
        "systems, employee integrity and operating processes. Also referred to "
        "as operational risk."
    ),
    "COMPLIANCE": (
        "The risk to earnings or capital arising from violations of, or "
        "non-conformance with, laws, rules, regulations, prescribed practices "
        "or ethical standards."
    ),
    "STRATEGIC": (
        "The risk to earnings or capital arising from adverse business "
        "decisions or improper implementation of those decisions."
    ),
    "REPUTATION": (
        "The risk to earnings or capital arising from negative public opinion, "
        "affecting the institution's ability to establish new relationships or "
        "services, or continue servicing existing ones."
    ),
}

# Which OCC categories each of the 10 assessed categories rolls up into.
# Each entry is a judgement call about the source definitions; it is kept as
# one readable table precisely so it can be argued with and tuned.
CATEGORY_TO_OCC: dict[str, list[str]] = {
    # A new product or service is a business decision (strategic), is
    # delivered through operating processes (transaction), carries product
    # -level regulatory exposure (compliance) and attaches the bank's name to
    # it (reputation).
    "PRODUCT_SERVICE_RISK": ["TRANSACTION", "COMPLIANCE", "STRATEGIC", "REPUTATION"],
    # Who the customers are drives counterparty performance (credit), KYC/CDD
    # obligations (compliance) and association risk (reputation).
    "CUSTOMER_SEGMENT_RISK": ["CREDIT", "COMPLIANCE", "REPUTATION"],
    # The OCC text names country and sovereign exposure as a credit risk
    # source explicitly, and cross-border activity as the home of FX risk.
    "GEOGRAPHIC_RISK": ["CREDIT", "FOREIGN_EXCHANGE", "COMPLIANCE", "REPUTATION"],
    # A delivery channel is, in the OCC's terms, service/product delivery.
    "DELIVERY_CHANNEL_RISK": ["TRANSACTION", "COMPLIANCE", "REPUTATION"],
    # Volume and velocity of flows are processed daily (transaction) under
    # reporting obligations (compliance), and unplanned changes in those flows
    # are exactly the funding shifts the liquidity definition describes.
    "TRANSACTION_ACTIVITY_RISK": ["TRANSACTION", "COMPLIANCE", "LIQUIDITY"],
    # "Information systems" is named in the transaction-risk definition;
    # "operating systems" and "quality of implementation" in the strategic one.
    "TECHNOLOGY_DEVELOPMENT_RISK": ["TRANSACTION", "STRATEGIC", "REPUTATION"],
    "THIRD_PARTY_VENDOR_RISK": ["TRANSACTION", "COMPLIANCE", "STRATEGIC", "REPUTATION"],
    # Opaque ownership obstructs beneficial-ownership obligations (compliance)
    # and the indirect/guarantor exposure the credit definition calls out.
    "OWNERSHIP_ENTITY_COMPLEXITY_RISK": ["CREDIT", "COMPLIANCE", "REPUTATION"],
    "FINANCIAL_CRIME_TYPOLOGY_RISK": ["TRANSACTION", "COMPLIANCE", "REPUTATION"],
    # "Internal controls" is the first term in the transaction-risk definition.
    "CONTROL_ENVIRONMENT_RISK": ["TRANSACTION", "COMPLIANCE", "STRATEGIC"],
}

# INTEREST_RATE and PRICE are intentionally unmapped: nothing in the 10
# assessed categories measures repricing or mark-to-market exposure, so the
# roll-up reports them as uncovered rather than implying a zero reading. See
# OccRiskCategoryRollup.covered.
OCC_CATEGORIES_NOT_COVERED = [
    category
    for category in OCC_RISK_CATEGORIES
    if not any(category in mapped for mapped in CATEGORY_TO_OCC.values())
]


class RiskFactorResponse(BaseModel):
    id: int
    assessment_id: int
    category: str
    applicable: bool
    score: float = 0.0
    severity: str = "LOW"
    likelihood: Optional[int] = None
    impact: Optional[int] = None
    rated_by: Optional[str] = None
    rated_at: Optional[datetime] = None
    # The AI's suggested rating, kept separate from the analyst's own
    # likelihood/impact above -- it pre-fills the rating form and never
    # feeds the calculation. See RiskFactor's column comments.
    ai_suggested_likelihood: Optional[int] = None
    ai_suggested_impact: Optional[int] = None
    ai_suggestion_rationale: Optional[str] = None
    ai_suggested_at: Optional[datetime] = None
    rating_source: Optional[str] = None
    indicators: list[str] = []
    # Evidence contract (app/risk_engine/evidence.py). evidence_status is
    # None for factors that predate quote verification and for manual
    # factors, which carry the analyst's rationale instead.
    evidence_status: Optional[str] = None
    evidence: list[dict[str, Any]] = []
    rejected_indicators: list[dict[str, Any]] = []
    missing_information: list[str] = []
    # Source Library citations the AI offered (verified and rejected); they
    # never feed indicators, evidence status or scores.
    source_citations: list[dict[str, Any]] = []
    # P4: fixed Stage 4 rules that required this category.
    rule_triggers: list[dict[str, Any]] = []
    rationale: str
    misuse_scenario: Optional[str] = None
    source: str
    added_by: Optional[str] = None
    excluded: bool
    exclusion_reason: Optional[str] = None
    excluded_by: Optional[str] = None
    excluded_at: Optional[datetime] = None
    version: int
    is_current: bool
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class RiskFactorManualCreate(BaseModel):
    """R4.5: an analyst adding a risk factor that wasn't identified automatically."""

    category: str
    applicable: bool = True
    # Display-only starting value. Like every factor, a manual one counts
    # toward the official score only once it is rated likelihood x impact.
    score: float = 0.0
    indicators: list[str] = []
    rationale: str
    misuse_scenario: Optional[str] = None
    added_by: Optional[str] = "System"

    @field_validator("category")
    @classmethod
    def category_must_be_known(cls, value: str) -> str:
        if value not in RISK_CATEGORIES:
            raise ValueError(
                f"category must be one of: {', '.join(RISK_CATEGORIES)}"
            )
        return value

    @field_validator("rationale")
    @classmethod
    def rationale_required(cls, value: str) -> str:
        if not value or not value.strip():
            raise ValueError("A rationale is required for a manually added risk factor.")
        return value

    @field_validator("indicators")
    @classmethod
    def indicators_must_be_known(cls, values: list[str]) -> list[str]:
        unknown = [value for value in values if value not in RISK_INDICATORS]
        if unknown:
            raise ValueError(f"Unknown indicator(s): {', '.join(unknown)}")
        return values


class RiskFactorSuggestRatingsRequest(BaseModel):
    """
    Ask the AI to suggest a likelihood/impact rating for this
    assessment's factors. By default only factors the analyst hasn't
    rated yet are touched; `include_rated` re-suggests for all of them
    (the analyst's own rating is still never overwritten).
    """

    include_rated: bool = False
    requested_by: Optional[str] = "System"


class RiskFactorSuggestRatingsResponse(BaseModel):
    """
    The factors after suggestion, plus a count of how many suggestions
    could not be produced. `degraded` is True when any factor fell back
    to the neutral stub, so the UI can say so instead of showing an
    invented 3x3 as though the model had produced it.
    """

    factors: list[RiskFactorResponse]
    suggested_count: int
    fallback_count: int
    degraded: bool


class RiskFactorIndicatorsUpdate(BaseModel):
    """P4: an analyst sets a factor's indicators (R4.2), with a reason."""

    indicators: list[str]
    reason: str

    @field_validator("indicators")
    @classmethod
    def indicators_must_be_known(cls, values: list[str]) -> list[str]:
        unknown = [value for value in values if value not in RISK_INDICATORS]
        if unknown:
            raise ValueError(f"Unknown indicator(s): {', '.join(unknown)}")
        return list(dict.fromkeys(values))

    @field_validator("reason")
    @classmethod
    def reason_required(cls, value: str) -> str:
        if not value or not value.strip():
            raise ValueError("A reason is required to change a factor's indicators.")
        return value.strip()


class RiskFactorExclude(BaseModel):
    """R4.5: excluding a factor/category requires a reason."""

    reason: str
    excluded_by: Optional[str] = "System"

    @field_validator("reason")
    @classmethod
    def reason_required(cls, value: str) -> str:
        if not value or not value.strip():
            raise ValueError("A reason is required to exclude a risk factor.")
        return value


class OccContributingFactor(BaseModel):
    """One assessed risk factor as it contributes to an OCC category."""

    risk_factor_id: int
    category: str
    score: float
    severity: str
    rated: bool
    excluded: bool


class OccRiskCategoryRollup(BaseModel):
    """
    One of the nine OCC categories, as derived from this assessment's
    rated risk factors.

    `covered` is False when no assessed category maps into this OCC
    category at all (INTEREST_RATE and PRICE). Those rows are reported
    with a null score rather than 0.0 -- "this taxonomy does not measure
    it" and "it measured zero" are different statements, and collapsing
    them would be the more misleading of the two.
    """

    occ_category: str
    label: str
    definition: str
    covered: bool
    score: Optional[float] = None
    risk_band: Optional[str] = None
    max_factor_score: Optional[float] = None
    is_provisional: bool = False
    factor_count: int = 0
    contributing_factors: list[OccContributingFactor] = []


class OccRiskProfileResponse(BaseModel):
    """
    The full nine-category OCC lens over an assessment, plus the
    provenance needed to read it honestly.
    """

    assessment_id: int
    categories: list[OccRiskCategoryRollup]
    uncovered_categories: list[str]
    is_provisional: bool
    source: str = (
        "Derived from this assessment's rated risk factors. Category "
        "definitions from OCC NR 96-2a, 'Categories of Risk' (1996)."
    )

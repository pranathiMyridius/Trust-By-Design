from dataclasses import dataclass


@dataclass
class RiskResult:
    dimension: str
    score: float
    severity: str
    reason: str


class RiskEngine:

    def assess(
        self,
        change_type: str,
        description: str,
        evidence: str,
        intelligence=None,
    ):
        text = f"{description} {evidence}".lower()

        results = []

        if change_type == "NEW_GEOGRAPHY":
            results.extend(
                self._assess_new_geography(text)
            )

        elif change_type == "NEW_PRODUCT":
            results.extend(
                self._assess_new_product(text)
            )

        elif change_type == "MATERIAL_CHANGE":
            results.extend(
                self._assess_material_change(text)
            )

        elif change_type == "THIRD_PARTY":
            results.extend(
                self._assess_third_party(text)
            )

        # Apply additional context-aware rules
        # when structured intelligence is available.
        if intelligence:
            results.extend(
                self._assess_intelligence(intelligence)
            )

        return self._merge_results(results)

    # ---------------------------------------------------------
    # Existing change-type assessments
    # ---------------------------------------------------------

    def _assess_new_geography(self, text: str):
        results = []

        results.append(
            RiskResult(
                dimension="GEOGRAPHIC",
                score=80,
                severity="HIGH",
                reason=(
                    "The proposed change introduces the "
                    "product into a new geographic market."
                ),
            )
        )

        results.append(
            RiskResult(
                dimension="COMPLIANCE",
                score=85,
                severity="HIGH",
                reason=(
                    "Entering a new geography may introduce "
                    "new regulatory and compliance requirements."
                ),
            )
        )

        if any(
            keyword in text
            for keyword in [
                "customer data",
                "personal data",
                "location data",
                "driver information",
            ]
        ):
            results.append(
                RiskResult(
                    dimension="CUSTOMER",
                    score=75,
                    severity="HIGH",
                    reason=(
                        "The change involves customer or "
                        "potentially sensitive operational data."
                    ),
                )
            )
        else:
            results.append(
                RiskResult(
                    dimension="CUSTOMER",
                    score=55,
                    severity="MEDIUM",
                    reason=(
                        "Existing customers may be affected "
                        "by geographic expansion."
                    ),
                )
            )

        results.append(
            RiskResult(
                dimension="OPERATIONAL",
                score=65,
                severity="HIGH",
                reason=(
                    "A new market may require changes to "
                    "support and operational processes."
                ),
            )
        )

        results.append(
            RiskResult(
                dimension="TECHNOLOGY",
                score=60,
                severity="HIGH",
                reason=(
                    "Geographic expansion may require changes "
                    "to integrations, identity, localization, "
                    "or platform configuration."
                ),
            )
        )

        results.append(
            RiskResult(
                dimension="FINANCIAL",
                score=45,
                severity="MEDIUM",
                reason=(
                    "New market entry can introduce billing, "
                    "taxation, and revenue considerations."
                ),
            )
        )

        return results

    def _assess_new_product(self, text: str):
        return [
            RiskResult(
                dimension="CUSTOMER",
                score=70,
                severity="HIGH",
                reason=(
                    "A new product can change the customer "
                    "experience and support requirements."
                ),
            ),
            RiskResult(
                dimension="OPERATIONAL",
                score=65,
                severity="HIGH",
                reason=(
                    "New products generally require new "
                    "operational processes."
                ),
            ),
            RiskResult(
                dimension="FINANCIAL",
                score=60,
                severity="HIGH",
                reason=(
                    "New products can introduce pricing, "
                    "billing, and revenue impacts."
                ),
            ),
            RiskResult(
                dimension="TECHNOLOGY",
                score=70,
                severity="HIGH",
                reason=(
                    "New functionality may require application "
                    "and integration changes."
                ),
            ),
        ]

    def _assess_material_change(self, text: str):
        return [
            RiskResult(
                dimension="CUSTOMER",
                score=65,
                severity="HIGH",
                reason=(
                    "A material change can affect existing customers."
                ),
            ),
            RiskResult(
                dimension="OPERATIONAL",
                score=60,
                severity="HIGH",
                reason=(
                    "Existing operational processes may "
                    "require modification."
                ),
            ),
            RiskResult(
                dimension="FINANCIAL",
                score=70,
                severity="HIGH",
                reason=(
                    "Material changes may affect pricing, "
                    "revenue, or billing."
                ),
            ),
        ]

    def _assess_third_party(self, text: str):
        return [
            RiskResult(
                dimension="THIRD_PARTY",
                score=80,
                severity="HIGH",
                reason=(
                    "The proposed change introduces a dependency "
                    "on an external third party."
                ),
            ),
            RiskResult(
                dimension="TECHNOLOGY",
                score=70,
                severity="HIGH",
                reason=(
                    "Third-party integrations introduce technical "
                    "dependency and availability considerations."
                ),
            ),
            RiskResult(
                dimension="COMPLIANCE",
                score=70,
                severity="HIGH",
                reason=(
                    "Third-party services may introduce additional "
                    "security, privacy, and compliance obligations."
                ),
            ),
        ]

    # ---------------------------------------------------------
    # Context-aware intelligence rules
    # ---------------------------------------------------------

    def _assess_intelligence(self, intelligence):
        results = []

        # ---------------------------------------------
        # High transaction volume
        # ---------------------------------------------
        transaction_volume = (
            intelligence.transaction_volume or ""
        ).lower()

        if (
            "150,000" in transaction_volume
            or "100,000" in transaction_volume
            or "high" in transaction_volume
        ):
            results.append(
                RiskResult(
                    dimension="OPERATIONAL",
                    score=85,
                    severity="HIGH",
                    reason=(
                        "The proposed change has high transaction "
                        "volume, increasing processing, monitoring, "
                        "capacity, and operational resilience risk."
                    ),
                )
            )

        # ---------------------------------------------
        # Large transaction limit
        # ---------------------------------------------
        transaction_limit = (
            intelligence.maximum_transaction_limit or ""
        ).lower()

        if "$100,000" in transaction_limit:
            results.append(
                RiskResult(
                    dimension="FINANCIAL",
                    score=80,
                    severity="HIGH",
                    reason=(
                        "The proposed change allows transactions "
                        "up to $100,000, increasing financial exposure "
                        "and transaction control requirements."
                    ),
                )
            )

        # ---------------------------------------------
        # Cross-border activity
        # ---------------------------------------------
        regulatory = " ".join(
            intelligence.get_list(
                "regulatory_considerations"
            )
        ).lower()

        countries = " ".join(
            intelligence.get_list("countries")
        ).lower()

        if (
            "cross-border" in regulatory
            or len(intelligence.get_list("countries")) > 1
        ):
            results.append(
                RiskResult(
                    dimension="COMPLIANCE",
                    score=90,
                    severity="CRITICAL",
                    reason=(
                        "The proposed change involves cross-border "
                        "activity across multiple jurisdictions, "
                        "increasing regulatory and compliance complexity."
                    ),
                )
            )

        # ---------------------------------------------
        # Higher-risk jurisdictions
        # ---------------------------------------------
        if "higher-risk" in regulatory:
            results.append(
                RiskResult(
                    dimension="COMPLIANCE",
                    score=95,
                    severity="CRITICAL",
                    reason=(
                        "The proposed change includes exposure to "
                        "higher-risk jurisdictions, increasing "
                        "regulatory, sanctions, and financial crime risk."
                    ),
                )
            )

        # ---------------------------------------------
        # Third-party vendor
        # ---------------------------------------------
        vendors = intelligence.get_list(
            "third_party_vendors"
        )

        if vendors:
            results.append(
                RiskResult(
                    dimension="THIRD_PARTY",
                    score=90,
                    severity="CRITICAL",
                    reason=(
                        "The proposed change depends on an external "
                        "third-party vendor for payment processing "
                        "and transaction routing."
                    ),
                )
            )

        # ---------------------------------------------
        # Customer data shared externally
        # ---------------------------------------------
        data_shared = intelligence.get_list(
            "data_shared"
        )

        if data_shared:
            results.append(
                RiskResult(
                    dimension="CUSTOMER",
                    score=85,
                    severity="HIGH",
                    reason=(
                        "Customer and transaction information is "
                        "shared with an external service provider, "
                        "increasing privacy and data-handling risk."
                    ),
                )
            )

        # ---------------------------------------------
        # Cloud/API integration
        # ---------------------------------------------
        technologies = " ".join(
            intelligence.get_list("technologies")
        ).lower()

        if (
            "rest api" in technologies
            or "aws" in technologies
            or "api" in technologies
        ):
            results.append(
                RiskResult(
                    dimension="TECHNOLOGY",
                    score=85,
                    severity="HIGH",
                    reason=(
                        "The proposed solution uses external API "
                        "integrations and cloud infrastructure, "
                        "increasing integration, availability, "
                        "security, and resilience considerations."
                    ),
                )
            )

        return results

    # ---------------------------------------------------------
    # Combine multiple rules for the same dimension
    # ---------------------------------------------------------

    def _merge_results(self, results):
        grouped = {}

        for result in results:
            grouped.setdefault(
                result.dimension,
                []
            ).append(result)

        merged_results = []

        for dimension, dimension_results in grouped.items():

            highest_score = max(
                result.score
                for result in dimension_results
            )

            highest_result = max(
                dimension_results,
                key=lambda result: result.score,
            )

            reasons = []

            for result in dimension_results:
                if result.reason not in reasons:
                    reasons.append(result.reason)

            merged_results.append(
                RiskResult(
                    dimension=dimension,
                    score=highest_score,
                    severity=self._severity_from_score(
                        highest_score
                    ),
                    reason=" ".join(reasons),
                )
            )

        return merged_results

    # ---------------------------------------------------------
    # Severity helper
    # ---------------------------------------------------------

    def _severity_from_score(self, score: float):
        if score >= 80:
            return "CRITICAL"

        if score >= 60:
            return "HIGH"

        if score >= 30:
            return "MEDIUM"

        return "LOW"
from datetime import datetime, timezone

from sqlalchemy import Boolean, Column, DateTime, Float, ForeignKey, Integer, String, Text

from app.database import Base


class InherentRiskCalculation(Base):
    """
    Stage 6 (R6.2, R6.5, R6.7): one immutable, versioned snapshot of a
    deterministic inherent-risk calculation for an assessment.

    Every recalculation (a factor rating changes, a factor is added or
    excluded) supersedes the previous current row rather than overwriting
    it -- same pattern as RiskFactor/RiskResult -- so "given a change to a
    factor value, the overall score is recalculated and the previous
    result is retained" (R6.7 acceptance criterion) holds without a
    separate history table.

    `inputs`/`weights`/`risk_bands`/`escalation_rules` are frozen JSON
    snapshots of exactly what was used, so a past calculation stays
    reproducible/inspectable even if the methodology is edited later
    (R6.5 calculation transparency).
    """

    __tablename__ = "inherent_risk_calculations"

    id = Column(Integer, primary_key=True, index=True)

    assessment_id = Column(
        Integer,
        ForeignKey("assessments.id"),
        nullable=False,
        index=True,
    )

    methodology_id = Column(Integer, nullable=True)
    methodology_name = Column(String(255), nullable=True)

    # JSON list of per-factor breakdown dicts: category, weight,
    # likelihood, impact, score, rated.
    inputs = Column(Text, nullable=False)

    # JSON dict snapshot of the factor weights actually applied.
    weights = Column(Text, nullable=False)

    # JSON dict snapshot of the score thresholds (legacy 6-dim scheme,
    # kept for reference) actually in force at calculation time.
    thresholds = Column(Text, nullable=True)

    # JSON list snapshot of the risk bands actually applied.
    risk_bands = Column(Text, nullable=False)

    # JSON list snapshot of the escalation rules evaluated.
    escalation_rules = Column(Text, nullable=True)

    calculation_method = Column(
        String(100),
        nullable=False,
        default="weighted_average_likelihood_impact",
    )

    final_score = Column(Float, nullable=False, default=0.0)
    risk_band = Column(String(30), nullable=False, default="LOW")

    # R6.7: an applicable, non-excluded factor is missing a manual rating.
    is_provisional = Column(Boolean, nullable=False, default=False)

    # R6.6: whether a mandatory escalation rule fired (and forced the
    # band up), plus why -- JSON list of human-readable reasons.
    escalated = Column(Boolean, nullable=False, default=False)
    escalation_reasons = Column(Text, nullable=True)

    # JSON list of every policy rule that fired: rule_code, rule_type,
    # version, the band before and after, and which factors triggered it.
    # NULL on calculations that predate the rule library.
    triggered_rules = Column(Text, nullable=True)

    # A fired rule marked mandatory_review requires a human reviewer
    # regardless of where the band ends up.
    mandatory_review = Column(Boolean, nullable=False, default=False)

    # Which methodology version and exact settings produced this result
    # ("builtin" for the code defaults), and which reference-data
    # snapshots were in force: JSON list of snapshot references, plus the
    # jurisdiction designations matched from them. NULL on older rows.
    methodology_version = Column(String(30), nullable=True)
    methodology_fingerprint = Column(String(80), nullable=True)
    reference_data = Column(Text, nullable=True)
    jurisdiction_matches = Column(Text, nullable=True)

    calculated_by = Column(String(255), nullable=True)
    calculated_at = Column(
        DateTime,
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )

    # R6.7: an authorized analyst's override of the calculated result.
    overridden = Column(Boolean, nullable=False, default=False)
    calculated_score = Column(Float, nullable=True)
    calculated_band = Column(String(30), nullable=True)
    override_value = Column(Float, nullable=True)
    override_band = Column(String(30), nullable=True)
    override_reason = Column(Text, nullable=True)
    override_by = Column(String(255), nullable=True)
    # Since migration 0017: the overriding user's id (from the session).
    override_by_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    override_at = Column(DateTime, nullable=True)

    version = Column(Integer, nullable=False, default=1)
    is_current = Column(Boolean, nullable=False, default=True)
    superseded_at = Column(DateTime, nullable=True)

    def get_inputs(self) -> list:
        import json

        try:
            return json.loads(self.inputs)
        except (TypeError, ValueError):
            return []

    def get_weights(self) -> dict:
        import json

        try:
            return json.loads(self.weights)
        except (TypeError, ValueError):
            return {}

    def get_risk_bands(self) -> list:
        import json

        try:
            return json.loads(self.risk_bands)
        except (TypeError, ValueError):
            return []

    def get_escalation_rules(self) -> list:
        import json

        try:
            return json.loads(self.escalation_rules) if self.escalation_rules else []
        except (TypeError, ValueError):
            return []

    def get_reference_data(self) -> dict:
        import json

        try:
            return json.loads(self.reference_data) if self.reference_data else {}
        except (TypeError, ValueError):
            return {}

    def get_jurisdiction_matches(self) -> list:
        import json

        try:
            return json.loads(self.jurisdiction_matches) if self.jurisdiction_matches else []
        except (TypeError, ValueError):
            return []

    def get_triggered_rules(self) -> list:
        import json

        try:
            return json.loads(self.triggered_rules) if self.triggered_rules else []
        except (TypeError, ValueError):
            return []

    def get_escalation_reasons(self) -> list:
        import json

        try:
            return json.loads(self.escalation_reasons) if self.escalation_reasons else []
        except (TypeError, ValueError):
            return []

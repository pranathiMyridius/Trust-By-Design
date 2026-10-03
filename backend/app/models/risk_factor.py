from datetime import datetime, timezone

from sqlalchemy import Boolean, Column, DateTime, Float, ForeignKey, Integer, String, Text

from app.database import Base


class RiskFactor(Base):
    """
    Stage 4 (R4.1-R4.5): per-category risk-factor identification.

    This is now also the source of the assessment's numeric score: the
    10 canonical categories are a fully dynamic, AI-determined set (only
    whichever the AI marks applicable actually count), replacing the
    old fixed 6-dimension analyze_risks() call. overall_score/risk_level
    are computed as the average of applicable, non-excluded factors'
    scores (see app/risk_engine/scoring.py::calculate_overall_score_from_factors),
    and RiskResult rows are populated from these same factors so
    existing dimension-keyed UI (Manual Scoring Calculator, Controls/
    Residual stages) keeps working against whatever dynamic set of
    categories actually applies to a given assessment.
    """

    __tablename__ = "risk_factors"

    id = Column(Integer, primary_key=True, index=True)

    assessment_id = Column(
        Integer,
        ForeignKey("assessments.id"),
        nullable=False,
        index=True,
    )

    # R4.1: one of RISK_CATEGORIES (see app/schemas/risk_factor.py).
    category = Column(String(50), nullable=False)

    # R4.1: whether this category actually applies to this change. A
    # non-applicable category is still recorded (with a rationale --
    # R4.3) rather than simply omitted, so "we considered this and it
    # doesn't apply, here's why" stays visible.
    applicable = Column(Boolean, nullable=False, default=True)

    # Numeric risk score (0-100) and derived severity for this category
    # -- 0/LOW when not applicable. Drives overall_score/risk_level (see
    # module docstring above).
    #
    # Stage 6 (R6.2): score/severity stay 0/LOW until an analyst rates
    # likelihood x impact below. The AI may *suggest* a rating (see
    # ai_suggested_likelihood/impact), but a suggestion alone never
    # moves score/severity -- only an analyst's own rating does. The
    # score itself is always computed deterministically by
    # app/risk_engine/scoring.py, never emitted by the LLM.
    score = Column(Float, nullable=False, default=0.0)
    severity = Column(String(30), nullable=False, default="LOW")

    # R6.2/R6.3: the analyst's own likelihood/impact rating (values from
    # the active methodology's likelihood_scale/impact_scale). None until
    # an analyst rates this factor -- an applicable, non-excluded factor
    # with no rating makes the overall calculation provisional (R6.7).
    likelihood = Column(Integer, nullable=True)
    impact = Column(Integer, nullable=True)
    rated_by = Column(String(255), nullable=True)
    # P3: the rater's user id, so involvement in the assessment can be
    # determined exactly (challenge-review independence). NULL on ratings
    # made before migration 0018 -- rated_by (a name) is used for those.
    rated_by_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    rated_at = Column(DateTime, nullable=True)

    # The AI's *suggested* rating, kept deliberately separate from the
    # analyst's likelihood/impact above so the audit trail can always
    # answer "what did the model propose, and what did the human decide?"
    # These pre-fill the rating form; they never feed the calculation and
    # never clear the provisional flag on their own -- a factor is only
    # "rated" once a human has saved a rating of their own.
    ai_suggested_likelihood = Column(Integer, nullable=True)
    ai_suggested_impact = Column(Integer, nullable=True)
    ai_suggestion_rationale = Column(Text, nullable=True)
    ai_suggested_at = Column(DateTime, nullable=True)

    # How the stored likelihood/impact came about, set when an analyst
    # saves a rating:
    #   ANALYST_CONFIRMED -- accepted the AI's suggestion unchanged
    #   ANALYST_OVERRIDE  -- changed one or both values away from it
    #   ANALYST_RATED     -- rated with no AI suggestion on the factor
    # None while unrated. This is what distinguishes "a human agreed"
    # from "a human never looked", which raw presence of a value cannot.
    rating_source = Column(String(30), nullable=True)

    # R4.2: JSON list of indicator codes (subset of RISK_INDICATORS)
    # that apply to this category.
    indicators = Column(Text, nullable=True)

    # Evidence contract (see app/risk_engine/evidence.py). All nullable:
    # NULL means the factor predates evidence verification, which the
    # API reports as such rather than inventing a status for it.
    #
    # evidence_status: EVIDENCE_FOUND | INSUFFICIENT_EVIDENCE |
    #   CONFLICTING_EVIDENCE | NOT_VERIFIED | NOT_APPLICABLE -- derived by
    #   deterministic quote verification, never taken from the model.
    evidence_status = Column(String(30), nullable=True)
    # JSON list of evidence records, verified and rejected alike, each
    # with its source id, document id/version, source checksum, quote and
    # verification result -- rejected ones are kept so the audit trail
    # shows what the model claimed and why it was refused.
    evidence = Column(Text, nullable=True)
    # JSON list[{indicator, reason}]: indicators the model asserted that
    # no verified quote supported, so they were not stored in
    # `indicators` and cannot trigger an escalation rule.
    rejected_indicators = Column(Text, nullable=True)
    # JSON list of strings: what the model said it would need to decide.
    missing_information = Column(Text, nullable=True)

    # P4 (Stage 4 AC): JSON list of the fixed Stage 4 rules that required
    # this category, each {rule_id, ruleset_version, effect, signals,
    # considerations} -- see app/risk_engine/stage4_rules.py. NULL when no
    # rule fired (or the factor predates the rules).
    rule_triggers = Column(Text, nullable=True)

    # R4.3: why this category/these indicators apply (or don't).
    rationale = Column(Text, nullable=False)

    # R4.4: free-text description of how the change could be misused,
    # specific to this category.
    misuse_scenario = Column(Text, nullable=True)

    # Provenance: "AI" (identified by the risk-factor analyzer) or
    # "MANUAL" (added by an analyst -- R4.5).
    source = Column(String(20), nullable=False, default="AI")

    added_by = Column(String(255), nullable=True)

    # R4.5: an analyst can exclude a category/factor, but must give a
    # reason -- exclusion_reason is required whenever excluded=True
    # (enforced at the API layer, not the DB, so existing rows aren't
    # affected).
    excluded = Column(Boolean, nullable=False, default=False)
    exclusion_reason = Column(Text, nullable=True)
    excluded_by = Column(String(255), nullable=True)
    excluded_at = Column(DateTime, nullable=True)

    # Re-analysis versioning, same pattern as RiskResult/AssessmentDocument.
    version = Column(Integer, nullable=False, default=1)
    is_current = Column(Boolean, nullable=False, default=True)
    superseded_at = Column(DateTime, nullable=True)

    created_at = Column(
        DateTime,
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )

    def set_indicators(self, values: list[str]) -> None:
        import json

        self.indicators = json.dumps(values)

    def get_indicators(self) -> list[str]:
        import json

        if not self.indicators:
            return []

        try:
            return json.loads(self.indicators)
        except json.JSONDecodeError:
            return []

    def set_evidence_fields(self, factor: dict) -> None:
        """Copies the verified-evidence fields from an analyzer result."""
        import json

        if "evidence_status" not in factor:
            return

        self.evidence_status = factor.get("evidence_status")
        self.evidence = json.dumps(factor.get("evidence") or [])
        self.rejected_indicators = json.dumps(factor.get("rejected_indicators") or [])
        self.missing_information = json.dumps(factor.get("missing_information") or [])

    @staticmethod
    def _json_list(raw: str | None) -> list:
        import json

        if not raw:
            return []

        try:
            value = json.loads(raw)
        except json.JSONDecodeError:
            return []

        return value if isinstance(value, list) else []

    def get_evidence(self) -> list:
        return self._json_list(self.evidence)

    def get_rejected_indicators(self) -> list:
        return self._json_list(self.rejected_indicators)

    def get_missing_information(self) -> list:
        return self._json_list(self.missing_information)

    def get_rule_triggers(self) -> list:
        return self._json_list(self.rule_triggers)

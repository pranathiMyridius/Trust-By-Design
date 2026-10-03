import json
from datetime import datetime, timezone

from sqlalchemy import Boolean, Column, DateTime, Float, ForeignKey, Integer, String, Text

from app.database import Base


class AIUsageLog(Base):
    """
    Stage 17 (R17.4: processing time, model usage and cost): one row per
    outbound AI/LLM request, written by app/ai/metering.py's
    metered_post() -- successful or not -- so usage, latency, failure
    rate and cost can be reported per model and per purpose.
    """

    __tablename__ = "ai_usage_logs"

    id = Column(Integer, primary_key=True, index=True)

    # Null when the call isn't tied to an assessment yet (e.g. document
    # extraction that runs before the assessment record exists).
    assessment_id = Column(Integer, ForeignKey("assessments.id"), nullable=True, index=True)

    # DOCUMENT_EXTRACTION, RISK_FACTOR_IDENTIFICATION, RISK_ANALYSIS,
    # DRAFT_NARRATIVE, EMBEDDING, LIKELIHOOD_IMPACT_SUGGESTION,
    # CONTROL_IDENTIFICATION, CONTROL_DESIGN_ASSESSMENT.
    purpose = Column(String(50), nullable=False, index=True)

    provider = Column(String(50), nullable=False, default="openrouter")
    # The model that was requested vs the one the provider says served
    # the call (a router model such as "openrouter/free" resolves to a
    # concrete one).
    requested_model = Column(String(255), nullable=True)
    response_model = Column(String(255), nullable=True)
    # R16.1: fingerprint of the prompt template used (see
    # app/ai/metering.py::prompt_version); None for embeddings.
    prompt_version = Column(String(80), nullable=True)

    success = Column(Boolean, nullable=False, default=False)
    http_status = Column(Integer, nullable=True)
    error = Column(Text, nullable=True)

    prompt_tokens = Column(Integer, nullable=True)
    completion_tokens = Column(Integer, nullable=True)
    total_tokens = Column(Integer, nullable=True)

    cost_usd = Column(Float, nullable=True)
    # REPORTED (provider returned it), ESTIMATED (AI_MODEL_PRICING),
    # UNKNOWN (neither available).
    cost_source = Column(String(20), nullable=False, default="UNKNOWN")

    duration_ms = Column(Integer, nullable=False, default=0)

    created_at = Column(
        DateTime,
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
        index=True,
    )


class AIEvaluationRecord(Base):
    """
    Stage 17 (R17.4, acceptance: "given AI-generated assessments, the
    system stores the metrics required for evaluation").

    One row per AI risk-identification run. The `ai_*` columns are a
    frozen snapshot of what the AI itself produced -- before any human
    rating, exclusion, addition or override -- so the comparison can
    always be made later even though RiskFactor rows and scores keep
    changing. The comparison columns below are (re)computed by
    app/services/ai_evaluation.py from what humans subsequently did.
    Only the latest run per assessment is `is_current`.
    """

    __tablename__ = "ai_evaluation_records"

    id = Column(Integer, primary_key=True, index=True)
    assessment_id = Column(Integer, ForeignKey("assessments.id"), nullable=False, index=True)

    # --- Snapshot of the AI run (never modified after creation). ---
    run_at = Column(DateTime, nullable=False, default=lambda: datetime.now(timezone.utc), index=True)
    model = Column(String(255), nullable=True)
    # False when the AI call failed and the pipeline fell back to
    # "every category needs manual review" -- such runs are excluded
    # from quality metrics but still counted for availability.
    ai_available = Column(Boolean, nullable=False, default=True)
    processing_ms = Column(Integer, nullable=True)
    ai_overall_score = Column(Float, nullable=True)
    ai_risk_band = Column(String(30), nullable=True)
    ai_factor_count = Column(Integer, nullable=False, default=0)
    ai_applicable_count = Column(Integer, nullable=False, default=0)
    # JSON list[{category, applicable, indicators, rationale, model_score, model_severity}]
    ai_factors = Column(Text, nullable=False, default="[]")
    # True for rows reconstructed by migrate_stage17_reporting.py from
    # AI factors that predate this table (no timing/model captured).
    backfilled = Column(Boolean, nullable=False, default=False)

    # --- Human comparison (recomputed). Null = not measurable yet. ---
    human_score = Column(Float, nullable=True)
    human_band = Column(String(30), nullable=True)
    band_agreement = Column(Boolean, nullable=True)
    score_difference = Column(Float, nullable=True)  # human - AI
    # Applicability agreement: of the categories the AI classified, how
    # many the human-reviewed outcome classified the same way (0 compared
    # until a human has reviewed the factors).
    applicability_compared = Column(Integer, nullable=True)
    applicability_agreeing = Column(Integer, nullable=True)
    # Factor band agreement -- only where the model supplied an advisory
    # severity (the methodology has humans rate risks, R6.2).
    factors_rated = Column(Integer, nullable=True)
    factors_agreeing = Column(Integer, nullable=True)
    human_added_factors = Column(Integer, nullable=True)  # risks the AI missed
    ai_factors_excluded = Column(Integer, nullable=True)  # AI risks a human rejected
    grounded_factors = Column(Integer, nullable=True)
    groundedness_score = Column(Float, nullable=True)  # mean 0..1
    unsupported_findings = Column(Integer, nullable=True)
    human_overrides = Column(Integer, nullable=True)
    controls_ai_extracted = Column(Integer, nullable=True)
    controls_matched = Column(Integer, nullable=True)
    evaluated_at = Column(DateTime, nullable=True)

    is_current = Column(Boolean, nullable=False, default=True)

    def get_ai_factors(self) -> list[dict]:
        try:
            value = json.loads(self.ai_factors or "[]")
        except (TypeError, ValueError):
            return []
        return value if isinstance(value, list) else []

from typing import Any, TypedDict


class RiskAssessmentState(TypedDict, total=False):
    assessment_id: int
    assessment: dict[str, Any]
    intelligence: Any
    risk_results: list[Any]
    # Stage 4 (R4.1-R4.4): per-category risk factors, separate from the
    # 6-dimension risk_results scoring above.
    risk_factors: list[Any]
    # Source Library passages supplied to the analysis as reference (id,
    # source, version, location) -- recorded in the analysis audit event.
    library_passages: list[dict[str, Any]]
    overall_score: float
    risk_level: str
    previous_status: str
    status: str
    error: str
    # Stage 17: False when the AI call failed and the run fell back to
    # the deterministic rule engine (or produced nothing at all).
    ai_available: bool
    # Degraded-mode contract (see app/risk_engine/degraded.py). Set by
    # identify_risks, written onto the Assessment by persist_results, and
    # read by the API/frontend instead of matching on rationale text.
    #   assessment_mode  "ai_assisted" | "rules_only" | "unavailable"
    #   ai_status        "success" | "failed" | "timeout" |
    #                    "rate_limited" | "invalid_response" |
    #                    "not_attempted"
    #   score_source     "ai_and_rules" | "deterministic_rules" |
    #                    "not_available"
    assessment_mode: str
    ai_status: str
    score_source: str
    is_provisional: bool
    requires_human_review: bool
    # Safe, user-readable. Never carries a provider name, prompt content
    # or a stack trace.
    degraded_reason: str
    # Short stable token for logs/admins only, e.g. "AI_TIMEOUT".
    technical_error_code: str
    # Canonical categories this run could not evaluate at all. Distinct
    # from a category scored 0.0, which means it *was* evaluated.
    unevaluated_categories: list[str]

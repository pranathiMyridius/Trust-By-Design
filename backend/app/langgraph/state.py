from typing import Any, TypedDict


class RiskAssessmentState(TypedDict, total=False):
    assessment_id: int
    assessment: dict[str, Any]
    intelligence: Any
    risk_results: list[Any]
    overall_score: float
    risk_level: str
    previous_status: str
    status: str
    error: str

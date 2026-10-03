from typing import Any

from sqlalchemy.orm import Session
from langgraph.graph import END, START, StateGraph

from app.observability import tracing

from . import progress
from .nodes import (
    calculate_scores,
    gather_intelligence,
    identify_risks,
    load_assessment,
    persist_results,
)
from .state import RiskAssessmentState

# State keys safe to put on a trace span: short status codes and numbers,
# never assessment text, rationale or evidence quotes.
_SPAN_STATE_KEYS = (
    "status",
    "assessment_mode",
    "ai_status",
    "score_source",
    "technical_error_code",
    "overall_score",
    "risk_level",
    "is_provisional",
    "requires_human_review",
    "unevaluated_categories",
)


def node_summary(update: dict[str, Any]) -> dict[str, Any]:
    summary = {key: update[key] for key in _SPAN_STATE_KEYS if key in update}
    factors = update.get("risk_factors")
    if isinstance(factors, list):
        summary["risk_factor_count"] = len(factors)
        summary["applicable_categories"] = [
            factor.get("category") for factor in factors if factor.get("applicable")
        ]
    return summary


def _traced(name: str, node, db: Session):
    # Each node becomes a child span of the workflow trace (see
    # service.py); a no-op when Langfuse is not configured.
    # It also reports live progress to a tracking background job
    # (progress.py); a no-op otherwise.
    def run(state):
        progress.notify(name, progress.RUNNING)
        try:
            with tracing.observe(
                name, metadata={"assessment_id": state.get("assessment_id")}
            ) as span:
                update = node(state, db)
                span.update(output=node_summary(update))
        except Exception:
            progress.notify(name, progress.FAILED)
            raise
        progress.notify(name, progress.COMPLETED)
        return update

    return run


def build_risk_assessment_graph(db: Session):
    # Nodes receive the same request-scoped SQLAlchemy session.
    builder = StateGraph(RiskAssessmentState)

    builder.add_node("load_assessment", _traced("load_assessment", load_assessment, db))
    builder.add_node("gather_intelligence", _traced("gather_intelligence", gather_intelligence, db))
    builder.add_node("identify_risks", _traced("identify_risks", identify_risks, db))
    builder.add_node("calculate_scores", _traced("calculate_scores", calculate_scores, db))
    builder.add_node("persist_results", _traced("persist_results", persist_results, db))

    builder.add_edge(START, "load_assessment")
    builder.add_edge("load_assessment", "gather_intelligence")
    builder.add_edge("gather_intelligence", "identify_risks")
    builder.add_edge("identify_risks", "calculate_scores")
    builder.add_edge("calculate_scores", "persist_results")
    builder.add_edge("persist_results", END)

    return builder.compile()

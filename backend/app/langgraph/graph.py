from sqlalchemy.orm import Session
from langgraph.graph import END, START, StateGraph

from .nodes import (
    calculate_scores,
    gather_intelligence,
    identify_risks,
    load_assessment,
    persist_results,
)
from .state import RiskAssessmentState


def build_risk_assessment_graph(db: Session):
    # Nodes receive the same request-scoped SQLAlchemy session.
    def load(state):
        return load_assessment(state, db)

    def intelligence(state):
        return gather_intelligence(state, db)

    def risks(state):
        return identify_risks(state, db)

    def scores(state):
        return calculate_scores(state, db)

    def persist(state):
        return persist_results(state, db)

    builder = StateGraph(RiskAssessmentState)

    builder.add_node("load_assessment", load)
    builder.add_node("gather_intelligence", intelligence)
    builder.add_node("identify_risks", risks)
    builder.add_node("calculate_scores", scores)
    builder.add_node("persist_results", persist)

    builder.add_edge(START, "load_assessment")
    builder.add_edge("load_assessment", "gather_intelligence")
    builder.add_edge("gather_intelligence", "identify_risks")
    builder.add_edge("identify_risks", "calculate_scores")
    builder.add_edge("calculate_scores", "persist_results")
    builder.add_edge("persist_results", END)

    return builder.compile()

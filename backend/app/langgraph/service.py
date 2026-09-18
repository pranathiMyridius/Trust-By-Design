from typing import Any

from sqlalchemy.orm import Session

from .graph import build_risk_assessment_graph


def run_risk_assessment_workflow(
    assessment_id: int,
    db: Session,
) -> dict[str, Any]:
    graph = build_risk_assessment_graph(db)

    return graph.invoke(
        {
            "assessment_id": assessment_id,
        }
    )

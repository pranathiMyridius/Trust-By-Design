import logging
import time
from typing import Any

from sqlalchemy.orm import Session

from app.ai.metering import ai_assessment_context
from app.observability import tracing

from .graph import build_risk_assessment_graph, node_summary

logger = logging.getLogger(__name__)


def run_risk_assessment_workflow(
    assessment_id: int,
    db: Session,
) -> dict[str, Any]:
    graph = build_risk_assessment_graph(db)

    started = time.perf_counter()
    # Optional Langfuse trace for the whole run; nodes and AI calls nest
    # under it (app/observability/tracing.py). A no-op when unconfigured.
    with tracing.observe(
        "risk_assessment_workflow",
        as_type="chain",
        input={"assessment_id": assessment_id},
        metadata={"assessment_id": assessment_id},
    ) as trace:
        # Stage 17: every AI call made during this run is metered against
        # this assessment (see app/ai/metering.py).
        with ai_assessment_context(assessment_id):
            result = graph.invoke(
                {
                    "assessment_id": assessment_id,
                }
            )
        processing_ms = int((time.perf_counter() - started) * 1000)
        trace.update(output={**node_summary(result), "processing_ms": processing_ms})

    if trace.trace_id:
        result["trace_id"] = trace.trace_id
        logger.info("Risk assessment %s traced as %s", assessment_id, trace.trace_id)

    # Stage 17 (R17.4): snapshot what the AI produced for later
    # AI-vs-human evaluation. Best-effort -- the analysis itself has
    # already been committed by persist_results and must not be undone
    # by a metrics failure.
    try:
        from app.ai.risk_factor_analyzer import OPENROUTER_MODEL
        from app.services.ai_evaluation import latest_model_for, record_ai_run

        record_ai_run(
            db,
            assessment_id,
            result,
            processing_ms,
            latest_model_for(db, assessment_id, "RISK_FACTOR_IDENTIFICATION") or OPENROUTER_MODEL,
        )
        db.commit()
    except Exception as exc:  # noqa: BLE001
        db.rollback()
        logger.warning("Could not record AI evaluation for assessment %s: %s", assessment_id, exc)

    return result

from typing import Any

from sqlalchemy.orm import Session

from app.ai.gemini_risk_analyzer import analyze_risks
from app.models.assessment import Assessment
from app.models.assessment_intelligence import AssessmentIntelligence
from app.models.risk_result import RiskResult
from app.risk_engine.scoring import (
    calculate_overall_score,
    determine_risk_level,
)
from app.services.audit_service import AuditAction, log_audit_event

from .state import RiskAssessmentState


def load_assessment(state: RiskAssessmentState, db: Session) -> dict[str, Any]:
    assessment = (
        db.query(Assessment)
        .filter(Assessment.id == state["assessment_id"])
        .first()
    )

    if not assessment:
        raise ValueError("Assessment not found.")

    return {
        "assessment": {
            "id": assessment.id,
            "title": assessment.title,
            "change_type": assessment.change_type,
            "description": assessment.description,
            "evidence": assessment.evidence,
            "status": assessment.status,
        },
        "previous_status": assessment.status,
        "status": "LOADED",
    }


def gather_intelligence(state: RiskAssessmentState, db: Session) -> dict[str, Any]:
    intelligence = (
        db.query(AssessmentIntelligence)
        .filter(
            AssessmentIntelligence.assessment_id
            == state["assessment_id"]
        )
        .first()
    )

    return {
        "intelligence": intelligence,
        "status": "INTELLIGENCE_GATHERED",
    }


def identify_risks(state: RiskAssessmentState, db: Session) -> dict[str, Any]:
    """
    LangGraph AI node.

    Gemini analyzes the assessment and the extracted business intelligence.
    The node returns structured risk results; it does not calculate the
    overall score.
    """
    results = analyze_risks(
        assessment=state["assessment"],
        intelligence=state.get("intelligence"),
    )
    overall_score = calculate_overall_score(results)

    return {
        "risk_results": results,
        "overall_score": overall_score,
        "status": "RISKS_IDENTIFIED_BY_AI",
    }


def calculate_scores(state: RiskAssessmentState, db: Session) -> dict[str, Any]:
    """
    Keep score aggregation deterministic.

    Claude supplies the per-dimension inherent-risk scores. The application
    applies the configured risk weights and risk-level thresholds.
    """
    results = state.get("risk_results", [])

    overall_score = calculate_overall_score(results)
    risk_level = determine_risk_level(overall_score)

    return {
        "overall_score": overall_score,
        "risk_level": risk_level,
        "status": "SCORES_CALCULATED",
    }


def persist_results(state: RiskAssessmentState, db: Session) -> dict[str, Any]:
    assessment = (
        db.query(Assessment)
        .filter(Assessment.id == state["assessment_id"])
        .first()
    )

    if not assessment:
        raise ValueError("Assessment not found.")

    # Re-analysis is idempotent: replace previous risk results.
    db.query(RiskResult).filter(
        RiskResult.assessment_id == assessment.id
    ).delete(synchronize_session=False)

    for result in state.get("risk_results", []):
        db.add(
            RiskResult(
                assessment_id=assessment.id,
                dimension=result["dimension"],
                score=result["score"],
                severity=result["severity"],
                reason=result["reason"],
            )
        )

    assessment.overall_score = state["overall_score"]
    assessment.risk_level = state["risk_level"]
    assessment.status = "READY_FOR_REVIEW"

    log_audit_event(
        db=db,
        assessment_id=assessment.id,
        action=AuditAction.ANALYSIS,
        previous_status=state.get("previous_status"),
        new_status="READY_FOR_REVIEW",
        details=(
            "Risk assessment analyzed through the LangGraph workflow "
            "using Gemini-generated per-dimension risk results and "
            "deterministic application scoring."
        ),
    )

    db.commit()
    db.refresh(assessment)

    return {
        "status": "COMPLETED",
    }

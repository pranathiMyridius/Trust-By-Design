"""
Stage 8 human review of the residual result:

R8.6  recommended conditions for elevated residual risk -- accept, modify
      or reject, each with a reason.
R8    the human-confirmed residual risk, stored beside the calculated one
      so the difference is visible.
"""

from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, field_validator
from sqlalchemy.orm import Session

from app.api.assessments import require_pipeline_role
from app.database import get_db
from app.models.assessment import Assessment
from app.models.recommended_condition import RecommendedCondition
from app.models.user import User
from app.risk_engine.methodology import get_methodology_config
from app.risk_engine.scoring import determine_risk_band
from app.services import residual_conditions
from app.services.audit_service import AuditAction, actor_name, log_audit_event
from app.services.decision_lock import ensure_assessment_editable
from app.services.residual_risk_service import current_residual_calculation, residual_response

router = APIRouter(prefix="/api/assessments", tags=["Residual Review"])

RISK_BANDS = ["LOW", "MEDIUM", "HIGH", "CRITICAL"]


class RecommendedConditionResponse(BaseModel):
    id: int
    assessment_id: int
    condition_type: str
    condition_label: str
    recommended_text: str
    rationale: str
    status: str
    final_text: Optional[str] = None
    decision_reason: Optional[str] = None
    decided_by: Optional[str] = None
    decided_at: Optional[datetime] = None
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class ConditionDecision(BaseModel):
    decision: str
    reason: str
    # Required when modifying: the condition as it will be adopted.
    text: Optional[str] = None

    @field_validator("decision")
    @classmethod
    def decision_known(cls, value: str) -> str:
        if value not in residual_conditions.DECISIONS:
            raise ValueError("decision must be one of: accept, modify, reject")
        return value

    @field_validator("reason")
    @classmethod
    def reason_required(cls, value: str) -> str:
        if not value or not value.strip():
            raise ValueError("A reason is required to accept, modify or reject a recommended condition.")
        return value.strip()


class ResidualConfirmation(BaseModel):
    band: str
    score: Optional[float] = None
    reason: str

    @field_validator("band")
    @classmethod
    def band_known(cls, value: str) -> str:
        if value not in RISK_BANDS:
            raise ValueError(f"band must be one of: {', '.join(RISK_BANDS)}")
        return value

    @field_validator("score")
    @classmethod
    def score_in_range(cls, value: Optional[float]) -> Optional[float]:
        if value is not None and not 0 <= value <= 100:
            raise ValueError("score must be between 0 and 100.")
        return value

    @field_validator("reason")
    @classmethod
    def reason_required(cls, value: str) -> str:
        if not value or not value.strip():
            raise ValueError("A reason is required to confirm the residual risk.")
        return value.strip()


def _assessment_or_404(db: Session, assessment_id: int) -> Assessment:
    assessment = db.get(Assessment, assessment_id)
    if assessment is None:
        raise HTTPException(status_code=404, detail="Assessment not found")
    return assessment


def _response(row: RecommendedCondition) -> RecommendedConditionResponse:
    return RecommendedConditionResponse.model_validate(
        {
            **{column.name: getattr(row, column.name) for column in row.__table__.columns},
            "condition_label": residual_conditions.CONDITION_TYPES.get(row.condition_type, row.condition_type),
        }
    )


@router.get(
    "/{assessment_id}/recommended-conditions",
    response_model=list[RecommendedConditionResponse],
)
def list_recommended_conditions(assessment_id: int, db: Session = Depends(get_db)):
    """R8.6: the current recommendations, refreshed against the rules (a
    decided recommendation is never changed by a refresh)."""

    assessment = _assessment_or_404(db, assessment_id)
    rows = residual_conditions.sync_recommendations(db, assessment)
    db.commit()
    return [_response(row) for row in rows]


@router.post(
    "/{assessment_id}/recommended-conditions/{condition_id}/decision",
    response_model=RecommendedConditionResponse,
)
def decide_recommended_condition(
    assessment_id: int,
    condition_id: int,
    payload: ConditionDecision,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_pipeline_role),
):
    """R8 acceptance criteria: an analyst can accept, modify, or reject a
    recommended condition with a reason."""

    assessment = _assessment_or_404(db, assessment_id)
    ensure_assessment_editable(assessment)

    row = (
        db.query(RecommendedCondition)
        .filter(RecommendedCondition.id == condition_id, RecommendedCondition.assessment_id == assessment_id)
        .first()
    )
    if row is None or not row.is_current:
        raise HTTPException(status_code=404, detail="Recommended condition not found")

    text = (payload.text or "").strip() or None
    if payload.decision == "modify" and not text:
        raise HTTPException(status_code=422, detail="Give the modified wording of the condition.")

    previous = row.status
    residual_conditions.decide(row, payload.decision, payload.reason, text, actor_name(current_user), current_user.id)

    log_audit_event(
        db=db,
        assessment_id=assessment_id,
        action=AuditAction.STATUS_CHANGE,
        previous_status=previous,
        new_status=row.status,
        actor=actor_name(current_user),
        actor_id=current_user.id,
        details=(
            f"Recommended condition '{residual_conditions.CONDITION_TYPES.get(row.condition_type, row.condition_type)}' "
            f"{row.status.lower()}"
            + (f" as: {row.final_text}" if row.status == "MODIFIED" else "")
            + f". Reason: {payload.reason}"
        ),
    )
    db.commit()
    db.refresh(row)
    return _response(row)


@router.post("/{assessment_id}/residual-risk/confirm")
def confirm_residual_risk(
    assessment_id: int,
    payload: ResidualConfirmation,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_pipeline_role),
):
    """
    R8: the analyst confirms -- or adjusts -- the calculated residual risk,
    with a reason. The calculated band and score are never overwritten;
    the confirmed values sit beside them and the difference is reported.
    """

    assessment = _assessment_or_404(db, assessment_id)
    ensure_assessment_editable(assessment)

    calc = current_residual_calculation(db, assessment_id)
    if calc is None or not calc.frozen:
        raise HTTPException(
            status_code=409,
            detail=(
                "The residual risk can be confirmed once it has been calculated -- "
                "advance the assessment to the Residual Risk stage first."
            ),
        )

    score = payload.score
    bands = get_methodology_config(db)["risk_bands"]
    if score is not None and payload.band != determine_risk_band(score, bands):
        # A score from another band would make the record contradict itself.
        raise HTTPException(
            status_code=422,
            detail=f"A score of {score:g} falls in the {determine_risk_band(score, bands)} band, not {payload.band}.",
        )

    # R10.4: every confirmation also lands in the override ledger, so a
    # later re-confirmation never hides an earlier one. residual_band /
    # residual_score are never changed.
    from app.services.override_ledger import record_applied

    record_applied(
        db,
        assessment_id,
        "RESIDUAL_RISK",
        "band",
        calc.id,
        calc.residual_band if calc.residual_score is None else f"{calc.residual_score:g} ({calc.residual_band})",
        payload.band if score is None else f"{score:g} ({payload.band})",
        payload.reason,
        current_user,
    )

    calc.confirmed_band = payload.band
    calc.confirmed_score = score
    calc.confirmation_reason = payload.reason
    calc.confirmed_by = actor_name(current_user)
    calc.confirmed_by_id = current_user.id
    calc.confirmed_at = datetime.now(timezone.utc)

    agrees = payload.band == calc.residual_band
    log_audit_event(
        db=db,
        assessment_id=assessment_id,
        action=AuditAction.STATUS_CHANGE,
        previous_status=calc.residual_band,
        new_status=payload.band,
        actor=actor_name(current_user),
        actor_id=current_user.id,
        details=(
            f"Residual risk {'confirmed' if agrees else 'adjusted'}: calculated {calc.residual_band}"
            + (f" ({calc.residual_score:g})" if calc.residual_score is not None else "")
            + f", confirmed {payload.band}"
            + (f" ({score:g})" if score is not None else "")
            + f". Reason: {payload.reason}"
        ),
    )
    db.commit()
    db.refresh(calc)
    return residual_response(calc)

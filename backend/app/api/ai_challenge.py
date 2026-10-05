"""
AI challenge analysis endpoints: advisory gap findings the model raises
about an assessment, which a reviewer confirms or dismisses. They never
block progression or feed a score (see app/models/ai_challenge.py).
"""

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.auth.dependencies import require_role
from app.database import get_db
from app.models.ai_challenge import AIChallengeFinding
from app.models.assessment import Assessment
from app.models.user import User, UserRole
from app.services.ai_challenge_service import decide_finding, run_ai_challenge
from app.services.audit_service import AuditAction, actor_name, log_audit_event
from app.services.decision_lock import ensure_assessment_editable

# Same analyst-work restriction as the rest of the AI pipeline.
require_pipeline_role = require_role(UserRole.FCRM_ANALYST, UserRole.MANAGER, UserRole.ADMIN)

router = APIRouter(prefix="/api/assessments", tags=["AI Challenge"])


class AIChallengeFindingResponse(BaseModel):
    id: int
    run_id: str
    category: str
    severity: str
    title: str
    detail: str
    risk_factor_id: int | None
    control_id: int | None
    document_id: int | None
    quote: str | None
    status: str
    model: str | None
    generated_by: str | None
    generated_at: datetime
    decided_by: str | None
    decided_at: datetime | None
    decision_note: str | None

    model_config = {"from_attributes": True, "protected_namespaces": ()}


class AIChallengeRunResponse(BaseModel):
    status: str
    findings_raised: int
    findings: list[AIChallengeFindingResponse]


class AIChallengeDecision(BaseModel):
    decision: str  # CONFIRM | DISMISS
    note: str | None = None


def _assessment_or_404(db: Session, assessment_id: int) -> Assessment:
    assessment = db.query(Assessment).filter(Assessment.id == assessment_id).first()
    if not assessment:
        raise HTTPException(status_code=404, detail="Assessment not found")
    return assessment


def _findings(db: Session, assessment_id: int) -> list[AIChallengeFinding]:
    return (
        db.query(AIChallengeFinding)
        .filter(AIChallengeFinding.assessment_id == assessment_id, AIChallengeFinding.status != "SUPERSEDED")
        .order_by(AIChallengeFinding.id.asc())
        .all()
    )


@router.get("/{assessment_id}/ai-challenge", response_model=list[AIChallengeFindingResponse])
def list_ai_challenge_findings(assessment_id: int, db: Session = Depends(get_db)):
    _assessment_or_404(db, assessment_id)
    return _findings(db, assessment_id)


@router.post("/{assessment_id}/ai-challenge/run", response_model=AIChallengeRunResponse)
def run_ai_challenge_analysis(
    assessment_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_pipeline_role),
):
    """Run the AI gap analysis. Unreviewed findings from an earlier run are
    replaced; confirmed and dismissed ones are kept."""

    assessment = _assessment_or_404(db, assessment_id)
    ensure_assessment_editable(assessment)
    name = actor_name(current_user)
    summary = run_ai_challenge(db, assessment, name)
    log_audit_event(
        db=db,
        assessment_id=assessment_id,
        action=AuditAction.AI_CHALLENGE_RUN,
        actor=name,
        actor_id=current_user.id,
        details=f"AI challenge analysis ({summary['status']}): {summary['findings_raised']} finding(s) raised.",
    )
    db.commit()
    return AIChallengeRunResponse(**summary, findings=_findings(db, assessment_id))


@router.post(
    "/{assessment_id}/ai-challenge/{finding_id}/decision",
    response_model=AIChallengeFindingResponse,
)
def decide_ai_challenge_finding(
    assessment_id: int,
    finding_id: int,
    payload: AIChallengeDecision,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_pipeline_role),
):
    """Confirm (the gap is real and needs follow-up) or dismiss (a reason is required)."""

    assessment = _assessment_or_404(db, assessment_id)
    ensure_assessment_editable(assessment)

    decision = (payload.decision or "").upper()
    if decision not in ("CONFIRM", "DISMISS"):
        raise HTTPException(status_code=400, detail="decision must be CONFIRM or DISMISS.")
    note = (payload.note or "").strip() or None
    if decision == "DISMISS" and not note:
        raise HTTPException(status_code=400, detail="A reason is required to dismiss a finding.")

    finding = (
        db.query(AIChallengeFinding)
        .filter(AIChallengeFinding.id == finding_id, AIChallengeFinding.assessment_id == assessment_id)
        .first()
    )
    if not finding:
        raise HTTPException(status_code=404, detail="Finding not found")
    if finding.status != "OPEN":
        raise HTTPException(status_code=409, detail=f"This finding is already {finding.status.lower()}.")

    name = actor_name(current_user)
    decide_finding(finding, decision, name, current_user.id, note)
    log_audit_event(
        db=db,
        assessment_id=assessment_id,
        action=AuditAction.AI_CHALLENGE_DECIDED,
        actor=name,
        actor_id=current_user.id,
        details=f"{finding.status.title()} AI challenge finding {finding.id}: {finding.title}",
    )
    db.commit()
    db.refresh(finding)
    return finding

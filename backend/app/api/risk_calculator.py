import json
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.auth.dependencies import get_current_user
from app.database import get_db
from app.models.calculator_draft import CalculatorDraft
from app.models.user import User
from app.schemas.assessment import ManualScoreDraftSave

# The standalone Risk Calculator page's saved inputs, per user -- so the
# values entered there are still there the next time the page is opened.
router = APIRouter(prefix="/api/risk-calculator", tags=["Risk Calculator"])


@router.get("/draft")
def get_calculator_draft(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    draft = db.query(CalculatorDraft).filter(CalculatorDraft.user_id == current_user.id).first()

    if draft is None:
        return {"draft": None, "updated_at": None}

    try:
        payload = json.loads(draft.payload)
    except ValueError:
        payload = None

    return {"draft": payload, "updated_at": draft.updated_at}


@router.put("/draft")
def save_calculator_draft(
    payload: ManualScoreDraftSave,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    draft = db.query(CalculatorDraft).filter(CalculatorDraft.user_id == current_user.id).first()

    if draft is None:
        draft = CalculatorDraft(user_id=current_user.id, payload="{}")
        db.add(draft)

    draft.payload = json.dumps(payload.model_dump())
    db.commit()
    db.refresh(draft)

    return {"draft": payload.model_dump(), "updated_at": draft.updated_at}

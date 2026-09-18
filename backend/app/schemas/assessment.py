from datetime import datetime
from typing import Optional

from pydantic import BaseModel, ConfigDict


class AssessmentCreate(BaseModel):
    title: str
    change_type: str
    description: str
    evidence: str


class AssessmentResponse(BaseModel):
    id: int
    title: str
    change_type: str
    description: str
    evidence: str
    status: str
    overall_score: Optional[float] = None
    risk_level: Optional[str] = None
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)
class AssessmentUpdate(BaseModel):
    title: str
    change_type: str
    description: str
    evidence: str
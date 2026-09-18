from datetime import datetime
from typing import Optional

from pydantic import BaseModel


class ChallengeFinding(BaseModel):
    title: str
    severity: str
    finding: str


class AssessmentChallengeResponse(BaseModel):
    id: int
    assessment_id: int
    challenge_id: str
    status: str
    outcome: Optional[str] = None
    comment: Optional[str] = None
    challenged_by: Optional[str] = None
    created_at: datetime
    updated_at: datetime

    findings: list[ChallengeFinding]
    inherent_score: float
    residual_score: float
    residual_level: str
    control_reduction: float


class ChallengeUpdate(BaseModel):
    outcome: str
    comment: str
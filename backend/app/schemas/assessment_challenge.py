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
    # Preview through the residual grid; None (with residual_reason) when
    # the inherent result is provisional or there are no risks to rate
    # controls against. The value of record is frozen at RESIDUAL_RISK.
    residual_score: Optional[float] = None
    residual_level: Optional[str] = None
    residual_reason: Optional[str] = None
    control_rating: Optional[str] = None
    control_reduction: float


class ChallengeUpdate(BaseModel):
    outcome: str
    comment: str
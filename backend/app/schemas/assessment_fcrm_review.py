from datetime import datetime
from typing import Optional

from pydantic import BaseModel


class FcrmReviewResponse(BaseModel):
    assessment_id: int
    justification: str
    human_ratings: dict[int, str]
    reviewed_by: Optional[str] = None
    updated_at: Optional[datetime] = None


class FcrmReviewUpdate(BaseModel):
    justification: str
    human_ratings: dict[int, str]

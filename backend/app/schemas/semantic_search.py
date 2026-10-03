from typing import Optional

from pydantic import BaseModel


class SimilarAssessmentMatch(BaseModel):
    assessment_id: int
    assessment_title: str
    assessment_reference_id: Optional[str] = None
    risk_level: Optional[str] = None
    overall_score: Optional[float] = None
    chunk_text: str
    similarity: float

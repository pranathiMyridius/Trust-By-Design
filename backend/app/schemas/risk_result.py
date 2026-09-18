from pydantic import BaseModel, ConfigDict


class RiskResultResponse(BaseModel):
    id: int
    assessment_id: int
    dimension: str
    score: float
    severity: str
    reason: str

    model_config = ConfigDict(from_attributes=True)
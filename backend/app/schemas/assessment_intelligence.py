from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel


class AssessmentIntelligenceResponse(BaseModel):
    id: int
    assessment_id: int

    business_line: Optional[str] = None

    channels: List[str] = []
    countries: List[str] = []
    customer_segments: List[str] = []

    transaction_volume: Optional[str] = None
    average_transaction_size: Optional[str] = None
    maximum_transaction_limit: Optional[str] = None

    third_party_vendors: List[str] = []
    data_shared: List[str] = []
    technologies: List[str] = []
    regulatory_considerations: List[str] = []
    existing_controls: List[str] = []
    additional_risk_factors: List[str] = []

    created_at: datetime

    class Config:
        from_attributes = True
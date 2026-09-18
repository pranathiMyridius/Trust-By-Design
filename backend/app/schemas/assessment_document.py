from datetime import datetime

from pydantic import BaseModel, ConfigDict


class AssessmentDocumentResponse(BaseModel):
    id: int
    assessment_id: int
    filename: str
    file_type: str
    extracted_text: str
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)
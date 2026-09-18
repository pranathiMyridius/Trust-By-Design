from datetime import datetime

from pydantic import BaseModel, ConfigDict


class AuditEventResponse(BaseModel):
    id: int
    assessment_id: int
    action: str
    previous_status: str | None = None
    new_status: str | None = None
    details: str | None = None
    actor: str | None = None
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)

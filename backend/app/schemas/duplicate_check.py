from datetime import datetime
from typing import Optional

from pydantic import BaseModel, ConfigDict


class DuplicateMatch(BaseModel):
    id: int
    reference_id: Optional[str] = None
    title: str
    change_type: str
    status: str
    is_draft: bool
    business_owner: Optional[str] = None
    legal_entity: Optional[str] = None
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)

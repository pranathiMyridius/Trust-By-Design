from datetime import datetime
from typing import Optional

from pydantic import BaseModel, ConfigDict


class DelegationCreate(BaseModel):
    """AW.7: the seven fields the requirement names."""

    delegate_id: int
    authority: str
    scope_type: str = "ALL"
    scope_assessment_id: Optional[int] = None
    start_at: datetime
    end_at: datetime
    reason: str
    # Admins may record a delegation on an absent approver's behalf.
    # Everyone else delegates their own authority and leaves this empty.
    delegator_id: Optional[int] = None


class DelegationRevoke(BaseModel):
    reason: str


class DelegationResponse(BaseModel):
    id: int
    delegator_id: int
    delegator_name: Optional[str] = None
    delegate_id: int
    delegate_name: Optional[str] = None
    authority: str
    scope_type: str
    scope_assessment_id: Optional[int] = None
    start_at: datetime
    end_at: datetime
    reason: str
    created_by_id: int
    created_at: datetime
    revoked_at: Optional[datetime] = None
    revoked_by_id: Optional[int] = None
    revoke_reason: Optional[str] = None
    # SCHEDULED | ACTIVE | EXPIRED | REVOKED, computed at read time.
    state: str

    model_config = ConfigDict(from_attributes=True)


class DelegationUserOption(BaseModel):
    id: int
    full_name: Optional[str] = None
    email: str
    role: str

    model_config = ConfigDict(from_attributes=True)


class DelegationOptions(BaseModel):
    # Users the caller may delegate to.
    delegates: list[DelegationUserOption]
    # Users whose authority the caller may delegate: just themselves,
    # or every eligible approver for an Admin.
    delegators: list[DelegationUserOption]
    max_days: int

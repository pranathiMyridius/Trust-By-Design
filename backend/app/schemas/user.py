from datetime import datetime
from typing import Optional

from pydantic import BaseModel, ConfigDict, field_validator

from app.governance.policy import POLICY_STATUS
from app.models.user import UserRole

VALID_ROLES = {role.value for role in UserRole}


def _validate_email(value: str) -> str:
    # Avoids pulling in the email-validator package: a minimal shape check
    # is enough here, real deliverability isn't a concern for this app.
    if not value or "@" not in value or value.startswith("@") or value.endswith("@"):
        raise ValueError("Enter a valid email address.")
    return value


class UserCreate(BaseModel):
    email: str
    password: str
    full_name: Optional[str] = None
    role: str
    manager_id: Optional[int] = None
    # R15.4: optional access restrictions; empty means unrestricted.
    scope_legal_entities: list[str] = []
    scope_business_units: list[str] = []
    scope_countries: list[str] = []

    @field_validator("email")
    @classmethod
    def email_must_be_valid(cls, value: str) -> str:
        return _validate_email(value)

    @field_validator("role")
    @classmethod
    def role_must_be_known(cls, value: str) -> str:
        if value not in VALID_ROLES:
            raise ValueError(f"role must be one of: {', '.join(sorted(VALID_ROLES))}")
        return value

    @field_validator("password")
    @classmethod
    def password_must_not_be_blank(cls, value: str) -> str:
        if not value or len(value) < 8:
            raise ValueError("Password must be at least 8 characters.")
        return value


class UserUpdate(BaseModel):
    full_name: Optional[str] = None
    role: Optional[str] = None
    manager_id: Optional[int] = None
    is_active: Optional[bool] = None
    scope_legal_entities: Optional[list[str]] = None
    scope_business_units: Optional[list[str]] = None
    scope_countries: Optional[list[str]] = None
    # Admin-initiated password reset. Never returned by any endpoint --
    # write-only, same as UserCreate.password.
    password: Optional[str] = None

    @field_validator("role")
    @classmethod
    def role_must_be_known(cls, value: Optional[str]) -> Optional[str]:
        if value is not None and value not in VALID_ROLES:
            raise ValueError(f"role must be one of: {', '.join(sorted(VALID_ROLES))}")
        return value

    @field_validator("password")
    @classmethod
    def password_must_be_long_enough(cls, value: Optional[str]) -> Optional[str]:
        if value is not None and len(value) < 8:
            raise ValueError("Password must be at least 8 characters.")
        return value


class UserResponse(BaseModel):
    id: int
    email: str
    full_name: Optional[str] = None
    role: str
    manager_id: Optional[int] = None
    is_active: bool
    scope_legal_entities: list[str] = []
    scope_business_units: list[str] = []
    scope_countries: list[str] = []
    # P3: governance designations held on top of the role (provisional).
    governance_designations: list[str] = []
    # Status of the rules governing designations (which exist, which base
    # role each needs, what they authorize): provisional, pending approval.
    # Describes the policy, not the user.
    policy_status: str = POLICY_STATUS
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)

    @field_validator("scope_legal_entities", "scope_business_units", "scope_countries", "governance_designations", mode="before")
    @classmethod
    def scope_from_json(cls, value):
        if isinstance(value, str):
            import json

            try:
                value = json.loads(value)
            except ValueError:
                return []
        return value or []


class LoginRequest(BaseModel):
    email: str
    password: str



class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: UserResponse


class DesignationsUpdate(BaseModel):
    """P3: set a user's governance designations (replaces the list)."""

    designations: list[str]
    reason: str

    @field_validator("reason")
    @classmethod
    def reason_required(cls, value: str) -> str:
        if not value or not value.strip():
            raise ValueError("A reason is required to change governance designations.")
        return value.strip()

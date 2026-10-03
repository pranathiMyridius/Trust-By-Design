import json
from datetime import datetime, timezone
from enum import Enum

from sqlalchemy import Boolean, Column, DateTime, ForeignKey, Integer, String, Text

from app.database import Base


class UserRole(str, Enum):
    BUSINESS_USER = "BUSINESS_USER"
    # Stage 10: qualified analyst who validates, challenges, and approves
    # an assessment for committee review (see api/assessments.py's
    # require_pipeline_role, which now includes this role).
    FCRM_ANALYST = "FCRM_ANALYST"
    MANAGER = "MANAGER"
    COMMITTEE_MEMBER = "COMMITTEE_MEMBER"
    ADMIN = "ADMIN"
    # Stage 15 (R15.1) roles that see without changing: an Auditor reads
    # every assessment, its history and the audit export; a Read-only
    # Executive reads assessments and reports. Neither can modify anything
    # (enforced for every non-read request in app/auth/access.py).
    AUDITOR = "AUDITOR"
    EXECUTIVE = "EXECUTIVE"
    # Stage 15: a Control Owner sees the assessments it is scoped to and can
    # evidence the closure of the actions assigned to it; a Policy Admin
    # maintains the approved evidence-source library (R5.1/R5.2).
    CONTROL_OWNER = "CONTROL_OWNER"
    POLICY_ADMIN = "POLICY_ADMIN"


# Roles that can read but never change anything.
READ_ONLY_ROLES = {UserRole.AUDITOR.value, UserRole.EXECUTIVE.value}

# R15.4: the dimensions a user's access can be restricted to. Each is a
# JSON list on the user; an empty list means "not restricted on this".
SCOPE_FIELDS = ("scope_legal_entities", "scope_business_units", "scope_countries")


class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)

    email = Column(String(255), nullable=False, unique=True, index=True)

    hashed_password = Column(String(255), nullable=False)

    full_name = Column(String(255), nullable=True)

    # Plain string column (like Assessment.status) rather than a DB enum,
    # so new roles (e.g. FCRM_ANALYST, AUDITOR) can be added later without
    # a migration. See UserRole above for the values currently in use.
    role = Column(String(30), nullable=False)

    # AW.1: the reporting relationship a Business User's submissions are
    # routed through. Self-referential -- also used to identify a
    # MANAGER's own manager, if any, though that's not exercised yet.
    manager_id = Column(Integer, ForeignKey("users.id"), nullable=True)

    is_active = Column(Boolean, nullable=False, default=True)

    # R15.4: optional restriction to particular legal entities, business
    # units and countries (JSON lists). Applied on top of the role's own
    # visibility in app/api/assessments.py::_scope_assessments_for_user.
    scope_legal_entities = Column(Text, nullable=True)
    scope_business_units = Column(Text, nullable=True)
    scope_countries = Column(Text, nullable=True)

    # P3 (provisional governance policy): admin-assigned governance
    # designations held on top of the base role -- e.g. SENIOR_ANALYST,
    # QA_REVIEWER, CHALLENGE_REVIEWER, HEAD_OF_FCRM, FCRM_GOVERNANCE_OWNER,
    # COMPLIANCE_MANAGER, COMMITTEE_CHAIR (app/governance/policy.py). JSON
    # list; NULL means none. Base roles still drive visibility.
    governance_designations = Column(Text, nullable=True)

    created_at = Column(
        DateTime,
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )

    def get_scope(self, field: str) -> list[str]:
        try:
            values = json.loads(getattr(self, field) or "[]")
        except (TypeError, ValueError):
            return []
        return [str(value).strip() for value in values if str(value).strip()] if isinstance(values, list) else []

    def set_scope(self, field: str, values: list[str] | None) -> None:
        cleaned = sorted({value.strip() for value in values or [] if value and value.strip()})
        setattr(self, field, json.dumps(cleaned) if cleaned else None)

    def get_designations(self) -> list[str]:
        try:
            values = json.loads(self.governance_designations or "[]")
        except (TypeError, ValueError):
            return []
        return sorted({str(v) for v in values}) if isinstance(values, list) else []

    def set_designations(self, values: list[str] | None) -> None:
        cleaned = sorted({v for v in values or [] if v})
        self.governance_designations = json.dumps(cleaned) if cleaned else None

    def has_designation(self, *designations: str) -> bool:
        held = set(self.get_designations())
        return any(d in held for d in designations)

    @property
    def is_scoped(self) -> bool:
        return any(self.get_scope(field) for field in SCOPE_FIELDS)

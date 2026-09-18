from enum import Enum
from sqlalchemy.orm import Session
from app.models.audit_event import AuditEvent


class AuditAction(str, Enum):
    CREATED = "CREATED"
    ANALYSIS = "ANALYSIS"
    STATUS_CHANGE = "STATUS_CHANGE"
    APPROVAL = "APPROVAL"
    REMEDIATION = "REMEDIATION"
    REJECTION = "REJECTION"


def log_audit_event(
    db: Session,
    assessment_id: int,
    action: AuditAction,
    previous_status: str | None = None,
    new_status: str | None = None,
    actor: str | None = None,
    details: str | None = None,
):
    event = AuditEvent(
        assessment_id=assessment_id,
        action=action.value,
        previous_status=previous_status,
        new_status=new_status,
        actor=actor or "System",
        details=details,
    )
    db.add(event)
    return event
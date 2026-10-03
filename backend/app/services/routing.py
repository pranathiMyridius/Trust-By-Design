"""
Automated routing/task assignment: maps an intake request's change type
(and computed priority — see app/services/triage.py) onto an owning
review team and a work queue, so a submitted assessment lands with the
right group without manual triage.
"""

from typing import Any

# change_type -> owning review team. Falls back to "General Risk Intake"
# for anything not listed (including legacy change types).
TEAM_ROUTING = {
    "NEW_GEOGRAPHY": "Compliance - Cross Border",
    "THIRD_PARTY_INTRODUCTION": "Vendor Risk Team",
    "TECHNOLOGY_CHANGE": "Technology Risk Team",
    "NEW_CUSTOMER_SEGMENT": "Customer Risk Team",
    "TRANSACTION_LIMIT_OR_CHANNEL_CHANGE": "Financial Crime Risk Team",
    "NEW_PRODUCT": "Product Risk Team",
    "NEW_SERVICE": "Product Risk Team",
    "PROCESS_CHANGE": "Operational Risk Team",
    "PERIODIC_REASSESSMENT": "General Risk Intake",
}


def compute_routing(
    data: dict[str, Any], priority: str
) -> tuple[str, str]:
    """
    Returns (assigned_team, assigned_queue) for an intake request's field
    data plus its already-computed priority level.
    """

    team = TEAM_ROUTING.get(
        str(data.get("change_type") or "").upper(), "General Risk Intake"
    )

    queue = "Priority Queue" if priority in ("URGENT", "HIGH") else "Standard Queue"

    return team, queue

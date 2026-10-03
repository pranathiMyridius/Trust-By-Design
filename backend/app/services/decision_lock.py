"""
Stage 12 acceptance criterion: "given a final decision, the assessment
becomes read-only except through a controlled amendment process."

A neutral module (no dependency on any one API router) so both
app/api/assessments.py and app/api/controls.py can share the same
definition of "finally decided" without importing each other.
"""

from fastapi import HTTPException

from app.models.assessment import Assessment

# Terminal committee/manager decisions. Not REMEDIATION -- that status
# IS the controlled-amendment path back into an editable state (see
# POST /{assessment_id}/amend in app/api/approvals.py), so it must stay
# editable. DEFERRED also stays editable: it is a committee "not yet"
# rather than a final decision.
FINAL_DECISION_STATUSES = {
    "APPROVED",
    "APPROVED_WITH_CONDITIONS",
    "REJECTED",
    "MANAGER_REJECTED",
    # Stage 14: a closed assessment is permanently read-only (no
    # amendment path out of CLOSED -- see app/services/workflow.py).
    "CLOSED",
}


def ensure_assessment_editable(assessment: Assessment) -> None:
    if assessment.status in FINAL_DECISION_STATUSES:
        raise HTTPException(
            status_code=409,
            detail=(
                f"This assessment has a final decision ({assessment.status}) "
                "and is read-only. Open a controlled amendment via POST "
                "/api/assessments/{assessment_id}/amend before making "
                "further changes."
            ),
        )

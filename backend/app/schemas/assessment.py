from datetime import date, datetime
from typing import Optional

from pydantic import BaseModel, ConfigDict, field_validator


# R1.1: extra fields captured on an assessment request, beyond the core
# title/change_type/description/evidence. Shared between AssessmentCreate
# and AssessmentUpdate since a request may be edited while still a draft.
class AssessmentRequestFields(BaseModel):
    product_or_service_name: Optional[str] = None
    business_owner: Optional[str] = None
    legal_entity: Optional[str] = None
    # Stage 19: optional business unit within the legal entity.
    business_unit: Optional[str] = None
    customer_segment: Optional[str] = None
    countries_jurisdictions: Optional[str] = None
    delivery_channels: Optional[str] = None
    expected_transaction_volume: Optional[str] = None
    expected_transaction_value: Optional[str] = None
    transaction_types: Optional[str] = None
    third_party_vendor_usage: Optional[str] = None
    technology_process_changes: Optional[str] = None
    expected_launch_date: Optional[str] = None
    # Flagged as a potential shell entity; None = not answered.
    shell_company_indicator: Optional[bool] = None


class AssessmentCreate(AssessmentRequestFields):
    # A title is required so a saved draft can still be found and
    # recognized later in the assessments list. Description and evidence
    # may be left blank so the request can be saved as a draft and
    # completed later (R1.3).
    title: str
    change_type: str = "NEW_PRODUCT"
    description: Optional[str] = None
    evidence: Optional[str] = None

    # R1.3/R1.4: true (the default) saves this request as a draft, no
    # mandatory-field validation. False submits it -- the API rejects
    # the request with the list of missing mandatory fields if any of
    # R1.1's required fields are blank.
    is_draft: bool = True

    # R1.5: who is submitting the request. Only meaningful (and only
    # persisted) when is_draft is False.
    submitted_by: Optional[str] = None

    @field_validator("title")
    @classmethod
    def title_must_not_be_blank(cls, value: str) -> str:
        if not value or not value.strip():
            raise ValueError("Title is required, even for a draft.")
        return value


class AssessmentResponse(AssessmentRequestFields):
    id: int
    title: str
    change_type: str
    description: Optional[str] = None
    evidence: Optional[str] = None
    is_draft: bool
    submitted_by: Optional[str] = None
    submitted_at: Optional[datetime] = None
    status: str
    overall_score: Optional[float] = None
    risk_level: Optional[str] = None
    # Frozen snapshot taken on arrival at INHERENT_RISK_ASSESSMENT.
    inherent_score: Optional[float] = None
    inherent_risk_level: Optional[str] = None
    # Frozen snapshot taken on arrival at RESIDUAL_RISK.
    residual_score: Optional[float] = None
    residual_risk_level: Optional[str] = None
    # Intake automation: case reference id, triage, routing (see
    # app/services/reference_id.py, triage.py, routing.py).
    reference_id: Optional[str] = None
    priority: Optional[str] = None
    priority_score: Optional[int] = None
    assigned_team: Optional[str] = None
    assigned_queue: Optional[str] = None
    # Raw JSON text of the Manual Scoring Calculator's last-saved draft
    # for this assessment (see Assessment.manual_score_draft). The
    # frontend parses this; null until a draft has been saved.
    manual_score_draft: Optional[str] = None
    # AW: hierarchical approval workflow.
    owner_id: Optional[int] = None
    manager_id: Optional[int] = None
    manager_decision: Optional[str] = None
    manager_decided_by_id: Optional[int] = None
    manager_decided_at: Optional[datetime] = None
    manager_comment: Optional[str] = None
    # AW.7: set when a delegate made the decision for this manager.
    manager_decided_on_behalf_of_id: Optional[int] = None
    manager_delegation_id: Optional[int] = None
    committee_decision: Optional[str] = None
    committee_decided_by_id: Optional[int] = None
    committee_decided_at: Optional[datetime] = None
    # AW.7: set when a delegate signed off for this committee member.
    committee_decided_on_behalf_of_id: Optional[int] = None
    committee_delegation_id: Optional[int] = None
    committee_rationale: Optional[str] = None
    committee_conditions: Optional[str] = None
    # Stage 10 (R10.5): "Request more information" flow.
    information_request_target: Optional[str] = None
    information_request_note: Optional[str] = None
    information_requested_by: Optional[str] = None
    information_requested_at: Optional[datetime] = None
    information_response: Optional[str] = None
    information_responded_at: Optional[datetime] = None
    pre_information_request_status: Optional[str] = None
    # Stage 14: lifecycle status, SLA and escalation (see
    # app/services/workflow.py). sla_state is computed, not stored.
    workflow_status: Optional[str] = None
    workflow_status_label: Optional[str] = None
    sla_state: Optional[str] = None
    status_entered_at: Optional[datetime] = None
    status_due_at: Optional[datetime] = None
    target_date: Optional[date] = None
    current_assignee_id: Optional[int] = None
    escalation_level: Optional[int] = 0
    escalated_at: Optional[datetime] = None
    escalated_to_id: Optional[int] = None
    escalation_note: Optional[str] = None
    closed_at: Optional[datetime] = None
    # Stage 18: Reassessment and Change Management.
    parent_assessment_id: Optional[int] = None
    next_review_date: Optional[date] = None
    # P6: NULL/IN_FORCE, UNDER_REASSESSMENT or SUPERSEDED (a flag, not a status).
    reassessment_state: Optional[str] = None
    superseded_by_id: Optional[int] = None
    # How the last risk analysis was produced. This is the contract the
    # frontend branches on -- it must never go back to matching on
    # rationale text to discover that the AI was down.
    #   assessment_mode: "ai_assisted" | "rules_only" | "unavailable"
    #   ai_status:       "success" | "failed" | "timeout" |
    #                    "rate_limited" | "invalid_response" |
    #                    "not_attempted"
    #   score_source:    "ai_and_rules" | "deterministic_rules" |
    #                    "not_available"
    # degraded_reason is safe to display; technical_error_code is not
    # user-facing copy and is only meaningful to an administrator.
    assessment_mode: Optional[str] = None
    ai_status: Optional[str] = None
    score_source: Optional[str] = None
    analysis_is_provisional: bool = False
    requires_human_review: bool = False
    degraded_reason: Optional[str] = None
    technical_error_code: Optional[str] = None
    analysis_mode_at: Optional[datetime] = None
    degraded_acknowledged_by: Optional[str] = None
    degraded_acknowledged_at: Optional[datetime] = None
    # Categories the run could not evaluate at all -- not the same thing
    # as a category that scored zero.
    unevaluated_categories: list[str] = []
    created_at: datetime
    updated_at: datetime

    @field_validator("unevaluated_categories", mode="before")
    @classmethod
    def _parse_unevaluated(cls, value):
        # The column stores JSON text; the API exposes a real list.
        import json

        if value is None or value == "":
            return []
        if isinstance(value, list):
            return value
        try:
            parsed = json.loads(value)
        except (TypeError, ValueError):
            return []
        return parsed if isinstance(parsed, list) else []

    model_config = ConfigDict(from_attributes=True)


class ManualScoreDraftSave(BaseModel):
    """
    Body for PATCH /api/assessments/{assessment_id}/manual-score/draft —
    persists the Manual Scoring Calculator's current inputs (not
    necessarily applied as the official score yet) so they're restored
    the next time this assessment is opened, instead of resetting to the
    AI-assessed defaults.
    """

    scores: dict[str, float]
    included: dict[str, bool]
    weights: dict[str, float]


class AdvanceStageRequest(BaseModel):
    # Only required (and only meaningful) when the assessment is currently
    # in COMMITTEE_DECISION, which is the one stage that branches instead
    # of always moving to the single next stage in STAGE_ORDER.
    decision: Optional[str] = None


class AssessmentUpdate(AssessmentRequestFields):
    # Same relaxed requirements as AssessmentCreate: this schema is used
    # both to finish a draft (still incomplete) and to edit a complete
    # assessment while it's in INTAKE or REMEDIATION.
    title: str
    change_type: str = "NEW_PRODUCT"
    description: Optional[str] = None
    evidence: Optional[str] = None

    # R1.3/R1.4: same meaning as on AssessmentCreate. Defaults to True
    # (no validation) so a plain field edit of an already-submitted
    # assessment doesn't need to pass this at all; pass False to submit
    # a draft that was previously saved with is_draft=True.
    is_draft: bool = True
    submitted_by: Optional[str] = None

    # P4 (R3.4): why the request was changed. Required once its business
    # profile has been validated (confirmed by the business owner).
    change_reason: Optional[str] = None

    @field_validator("title")
    @classmethod
    def title_must_not_be_blank(cls, value: str) -> str:
        if not value or not value.strip():
            raise ValueError("Title is required, even for a draft.")
        return value


class DegradedResultAcknowledgement(BaseModel):
    """
    A reviewer's acknowledgement that they have read a provisional,
    rules-only risk result and accept it as a basis for advancing.
    """

    note: Optional[str] = None

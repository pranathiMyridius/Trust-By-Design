from datetime import datetime, timezone

from sqlalchemy import Boolean, Column, Date, DateTime, Float, ForeignKey, Integer, String, Text

from app.database import Base


class Assessment(Base):
    __tablename__ = "assessments"

    id = Column(Integer, primary_key=True, index=True)

    title = Column(String(255), nullable=False)

    change_type = Column(
        String(50),
        nullable=False
    )

    # Nullable so an assessment can be saved as an incomplete draft
    # (R1.3: users can save a request as a draft and finish it later).
    description = Column(Text, nullable=True)

    evidence = Column(Text, nullable=True)

    # --- R1.1: additional fields captured on an assessment request. ---
    # All nullable so a request can still be saved as a draft (R1.3)
    # before every field is filled in.
    product_or_service_name = Column(String(255), nullable=True)

    business_owner = Column(String(255), nullable=True)

    # Stage 19 (Scalability): indexed, and paired with a separate
    # business_unit, so list views can be filtered per legal entity /
    # business unit as the number of units and assessments grows.
    legal_entity = Column(String(255), nullable=True, index=True)

    business_unit = Column(String(255), nullable=True, index=True)

    customer_segment = Column(String(255), nullable=True)

    countries_jurisdictions = Column(Text, nullable=True)

    delivery_channels = Column(Text, nullable=True)

    expected_transaction_volume = Column(String(255), nullable=True)

    expected_transaction_value = Column(String(255), nullable=True)

    transaction_types = Column(Text, nullable=True)

    third_party_vendor_usage = Column(Text, nullable=True)

    technology_process_changes = Column(Text, nullable=True)

    # Stored as an ISO date string (YYYY-MM-DD) rather than DateTime so a
    # partially-filled draft never fails to save because of date parsing.
    expected_launch_date = Column(String(20), nullable=True)

    # Whether the requester flagged the counterparty as a potential shell
    # entity. NULL means not answered (e.g. a draft or an older record),
    # which is kept distinct from an explicit "no".
    shell_company_indicator = Column(Boolean, nullable=True)

    # R1.3/R1.4: whether this request is still a draft (mandatory-field
    # validation is skipped while true) or has been formally submitted
    # (validated -- see api/assessments.py's MANDATORY_INTAKE_FIELDS).
    # Kept independent of `status` below so the 9-stage pipeline status
    # machine doesn't need a special-cased "not really started yet" state.
    is_draft = Column(Boolean, nullable=False, default=True)

    # R1.5: who submitted the request and when. Null while is_draft is
    # true; set once, at the moment the request is first submitted, and
    # never changed afterwards.
    submitted_by = Column(String(255), nullable=True)
    submitted_at = Column(DateTime, nullable=True)

    # --- Intake automation: reference id, triage, routing. ---
    # Human-readable case reference (e.g. "RAW-2026-00007"), generated
    # once at creation (see app/services/reference_id.py) and never
    # changed. Acts as the intake "acknowledgment" id.
    reference_id = Column(String(50), nullable=True, unique=True)

    # Deterministic intake-time triage (see app/services/triage.py),
    # computed when the request is submitted (is_draft flips to False) --
    # independent of the AI risk engine, which only runs later at
    # RISK_IDENTIFICATION.
    priority = Column(String(20), nullable=True)
    priority_score = Column(Integer, nullable=True)

    # Automated routing/task assignment (see app/services/routing.py),
    # computed alongside priority at submission time.
    assigned_team = Column(String(100), nullable=True)
    assigned_queue = Column(String(50), nullable=True)

    # Stage-pipeline status. Tracked pipeline stages (in order):
    # INTAKE, EVIDENCE_COLLECTION, RISK_IDENTIFICATION,
    # INHERENT_RISK_ASSESSMENT, CONTROL_ASSESSMENT, RESIDUAL_RISK,
    # HUMAN_REVIEW, COMMITTEE_DECISION, AUDIT.
    # Non-tracked special values: REMEDIATION (transient loop-back from a
    # COMMITTEE_DECISION "remediation required" outcome, pending the
    # business owner returning to INTAKE) and REJECTED (terminal).
    status = Column(
        String(50),
        nullable=False,
        default="INTAKE",
        index=True,
    )

    overall_score = Column(
        Float,
        nullable=True
    )

    risk_level = Column(
        String(30),
        nullable=True
    )

    # Frozen snapshot of the inherent (pre-control) risk assessment, taken
    # when the assessment arrives at INHERENT_RISK_ASSESSMENT. Distinct from
    # overall_score/risk_level so a later re-analysis (e.g. re-running
    # RISK_IDENTIFICATION) can't silently drift a rating that has already
    # been passed downstream to Control Assessment / committee.
    inherent_score = Column(
        Float,
        nullable=True
    )

    inherent_risk_level = Column(
        String(30),
        nullable=True
    )

    # Persisted residual (post-control) risk, computed and frozen when the
    # assessment arrives at RESIDUAL_RISK. Source of truth for Human
    # Review / Committee Decision, instead of being recomputed ad hoc.
    residual_score = Column(
        Float,
        nullable=True
    )

    residual_risk_level = Column(
        String(30),
        nullable=True
    )

    # Persisted "what-if" state of the Manual Scoring Calculator for this
    # assessment — JSON text: {"scores": {...}, "included": {...},
    # "weights": {...}}. Lets the calculator's inputs survive navigating
    # away and back, independent of whether they've ever been applied as
    # the official score. Null until the user has saved a draft at least
    # once.
    manual_score_draft = Column(Text, nullable=True)

    # --- AW: hierarchical approval workflow. ---
    # The Business User who owns this assessment. Nullable because rows
    # created before auth existed have no owner on record.
    owner_id = Column(Integer, ForeignKey("users.id"), nullable=True, index=True)

    # Snapshot of the assigned Manager, captured at submit-to-manager time
    # (not at creation) -- see AW.1: a later change to the owner's
    # reporting line must not rewrite an already-submitted assessment's
    # history.
    manager_id = Column(Integer, ForeignKey("users.id"), nullable=True, index=True)

    manager_decision = Column(String(20), nullable=True)
    manager_decided_by_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    manager_decided_at = Column(DateTime, nullable=True)
    manager_comment = Column(Text, nullable=True)
    # AW.7: set when the decision was made by a delegate. Then
    # manager_decided_by_id is the delegate who acted and
    # manager_decided_on_behalf_of_id is the manager whose authority they
    # used, so the record identifies both.
    manager_decided_on_behalf_of_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    manager_delegation_id = Column(
        Integer,
        # use_alter: approval_delegations also references assessments,
        # so this constraint is added after both tables exist.
        ForeignKey("approval_delegations.id", use_alter=True, name="fk_assessments_manager_delegation"),
        nullable=True,
    )

    committee_decision = Column(String(30), nullable=True)
    committee_decided_by_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    committee_decided_at = Column(DateTime, nullable=True)
    committee_rationale = Column(Text, nullable=True)
    # AW.7: as for the manager decision above.
    committee_decided_on_behalf_of_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    committee_delegation_id = Column(
        Integer,
        # use_alter: approval_delegations also references assessments,
        # so this constraint is added after both tables exist.
        ForeignKey("approval_delegations.id", use_alter=True, name="fk_assessments_committee_delegation"),
        nullable=True,
    )
    # Only meaningful when committee_decision == "APPROVED_WITH_CONDITIONS".
    committee_conditions = Column(Text, nullable=True)

    # R10.5: the FCRM analyst can return the assessment to the business
    # owner (or another team) for clarification instead of continuing
    # review. Status flips to "INFORMATION_REQUESTED" (AC4);
    # pre_information_request_status remembers what status to resume once
    # a response is provided (see request-information/provide-information
    # in api/assessments.py).
    information_request_target = Column(String(255), nullable=True)
    information_request_note = Column(Text, nullable=True)
    information_requested_by = Column(String(255), nullable=True)
    information_requested_at = Column(DateTime, nullable=True)
    information_response = Column(Text, nullable=True)
    information_responded_at = Column(DateTime, nullable=True)
    pre_information_request_status = Column(String(50), nullable=True)

    # --- Stage 14: workflow and status management. ---
    # The business-facing lifecycle status (DRAFT, SUBMITTED, ...,
    # CLOSED), derived from `status` + is_draft + profile confirmation
    # and persisted on every transition by app/services/workflow.py --
    # never set directly anywhere else.
    workflow_status = Column(String(50), nullable=True, index=True)

    # R14.4: service-level tracking for the current lifecycle status.
    # status_due_at is status_entered_at + that status's SLA (see
    # workflow.STATUS_SLA_DAYS); null for statuses with no SLA.
    status_entered_at = Column(DateTime, nullable=True)
    status_due_at = Column(DateTime, nullable=True)

    # R14.4: overall target completion date for the whole assessment.
    # Defaulted from priority at submission; adjustable by a reviewer.
    target_date = Column(Date, nullable=True)

    # R14.3: a specific user who has picked up / been given the current
    # task. Null means "whoever holds the responsible role" (see
    # workflow.RESPONSIBILITY). Cleared whenever the lifecycle status
    # changes, since the next task usually belongs to someone else.
    current_assignee_id = Column(Integer, ForeignKey("users.id"), nullable=True)

    # Escalation of an overdue status (0 = not escalated). Reset on
    # every lifecycle status change.
    escalation_level = Column(Integer, nullable=False, default=0)
    escalated_at = Column(DateTime, nullable=True)
    escalated_to_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    escalation_note = Column(Text, nullable=True)

    closed_at = Column(DateTime, nullable=True)

    # --- Stage 18: Reassessment and Change Management ---
    # R18.2: when this assessment IS a reassessment, the approved
    # assessment it supersedes -- lets old vs. new be compared as two
    # full, independently-pipelined records rather than diffing against
    # an overwritten row or an audit-log snapshot.
    parent_assessment_id = Column(Integer, ForeignKey("assessments.id"), nullable=True)

    # R18.1: when the current approval expires / is next due for
    # periodic review. Set automatically 365 days after an APPROVED or
    # APPROVED_WITH_CONDITIONS committee decision (see
    # app/services/reassessment_service.py); editable by a reviewer.
    next_review_date = Column(Date, nullable=True)

    # R18.3: for a reassessment, which of its intake fields were reused
    # unchanged from the parent rather than newly proposed -- JSON dict
    # {field_name: {"source_assessment_id": int, "source_date": iso str}}
    # -- so the UI can show "carried over from Assessment #X, dated Y"
    # instead of presenting reused information as if it were fresh.
    reused_field_sources = Column(Text, nullable=True)

    # P6 (R18.4): where this approval stands while it is reassessed -- a
    # flag, never a pipeline status, so existing approved records keep
    # their status. NULL = in force; UNDER_REASSESSMENT = a reassessment
    # is open; SUPERSEDED = a reassessment was approved (terminal). See
    # app/services/reassessment_lifecycle.py.
    reassessment_state = Column(String(30), nullable=True, index=True)
    superseded_by_id = Column(Integer, ForeignKey("assessments.id"), nullable=True)
    superseded_at = Column(DateTime, nullable=True)

    # ------------------------------------------------------------------
    # How the most recent risk analysis was produced.
    #
    # Before these existed, an AI outage was written back as ten
    # categories at score 0.0 / not-applicable, which is indistinguishable
    # from a genuine low-risk finding. These fields make the difference
    # explicit and machine-readable, so no consumer has to infer it from
    # rationale text. See app/risk_engine/degraded.py.
    # ------------------------------------------------------------------

    # "ai_assisted" | "rules_only" | "unavailable". Null on assessments
    # created before this existed, and on any that have not been analysed.
    assessment_mode = Column(String(20), nullable=True, index=True)

    # "success" | "failed" | "timeout" | "rate_limited" |
    # "invalid_response" | "not_attempted".
    ai_status = Column(String(30), nullable=True)

    # "ai_and_rules" | "deterministic_rules" | "not_available" -- the
    # provenance of overall_score/risk_level.
    score_source = Column(String(30), nullable=True)

    # Named analysis_is_provisional rather than is_provisional to avoid
    # colliding with the inherent-risk calculation's own provisional flag
    # (R6.7, "an applicable factor is still unrated"), which is a
    # different statement about a different thing.
    analysis_is_provisional = Column(Boolean, nullable=False, default=False)

    # True after a degraded run. Blocks stage advancement until a
    # reviewer acknowledges the result (see the acknowledge-degraded
    # endpoint in app/api/assessments.py).
    requires_human_review = Column(Boolean, nullable=False, default=False)

    # Safe to show to any user: no provider name, prompt content, model
    # id or stack trace.
    degraded_reason = Column(Text, nullable=True)

    # Logs/admin only, e.g. "AI_TIMEOUT", "RULE_ENGINE_FAILURE".
    technical_error_code = Column(String(60), nullable=True)

    # JSON list of canonical categories the run could not evaluate.
    # Deliberately not stored as factors at score 0.0: "could not
    # evaluate" and "evaluated, no material risk" are different findings.
    unevaluated_categories = Column(Text, nullable=True)

    analysis_mode_at = Column(DateTime, nullable=True)

    # Reviewer acknowledgement of a degraded result. Cleared whenever a
    # new degraded run lands, because what was acknowledged was a
    # different result.
    degraded_acknowledged_by = Column(String(255), nullable=True)
    degraded_acknowledged_at = Column(DateTime, nullable=True)

    def set_unevaluated_categories(self, values: list[str]) -> None:
        import json

        self.unevaluated_categories = json.dumps(values or [])

    def get_unevaluated_categories(self) -> list[str]:
        import json

        if not self.unevaluated_categories:
            return []

        try:
            parsed = json.loads(self.unevaluated_categories)
        except json.JSONDecodeError:
            return []

        return parsed if isinstance(parsed, list) else []

    @property
    def sla_state(self) -> str:
        from app.services.workflow import compute_sla_state

        return compute_sla_state(self)

    @property
    def workflow_status_label(self) -> str:
        from app.services.workflow import label_for

        return label_for(self.workflow_status)

    created_at = Column(
        DateTime,
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
        index=True,
    )

    updated_at = Column(
        DateTime,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
        nullable=False
    )

import json
from datetime import datetime, timezone

from sqlalchemy import Boolean, Column, DateTime, ForeignKey, Integer, String, Text

from app.database import Base


class AssessmentDraft(Base):
    """
    Stage 9 (R9.1-R9.4): a structured, decision-ready assessment draft.

    Assembled from every prior stage's already-computed data (business
    profile, risk factors/indicators, the Stage 6 inherent-risk
    calculation, mapped controls/effectiveness/gaps, the frozen residual
    score) plus LLM-written narrative sections (executive summary, risk
    statements, analyst recommendation) -- see
    app/services/assessment_draft_service.py. The LLM only ever writes
    prose from data that was already computed deterministically
    elsewhere; it never invents or recalculates a score.

    Versioned the same supersede-don't-delete way as RiskFactor/
    InherentRiskCalculation/Control, which is what satisfies R9.3's
    "the original generated content is retained" on an analyst edit --
    editing creates a new current version rather than mutating the
    as-generated one.
    """

    __tablename__ = "assessment_drafts"

    id = Column(Integer, primary_key=True, index=True)

    assessment_id = Column(
        Integer,
        ForeignKey("assessments.id"),
        nullable=False,
        index=True,
    )

    # --- R9.1 structured sections ---
    executive_summary = Column(Text, nullable=False, default="")
    business_change_description = Column(Text, nullable=False, default="")
    business_profile = Column(Text, nullable=False, default="{}")  # JSON dict
    applicable_risk_categories = Column(Text, nullable=False, default="[]")  # JSON list[str]
    risk_indicators = Column(Text, nullable=False, default="[]")  # JSON list[str]
    risk_statements = Column(Text, nullable=False, default="[]")  # JSON list[{category, statement}]
    inherent_risk = Column(Text, nullable=False, default="{}")  # JSON {score, band, calculation_id}
    evidence_references = Column(Text, nullable=False, default="[]")  # JSON list
    mapped_controls = Column(Text, nullable=False, default="[]")  # JSON list
    control_effectiveness = Column(Text, nullable=False, default="{}")  # JSON summary
    residual_risk = Column(Text, nullable=False, default="{}")  # JSON {score, band}
    risk_gaps = Column(Text, nullable=False, default="[]")  # JSON list
    assumptions = Column(Text, nullable=False, default="[]")  # JSON list[str]
    missing_information = Column(Text, nullable=False, default="[]")  # JSON list[str]
    recommended_conditions = Column(Text, nullable=False, default="[]")  # JSON list[str]
    analyst_recommendation = Column(Text, nullable=False, default="")
    required_approvals = Column(Text, nullable=False, default="[]")  # JSON list[str]

    # R9.2: uncertainty the AI/system could not resolve on its own --
    # low-confidence info, unsupported conclusions, conflicting evidence,
    # unresolved questions (assumptions/missing_information have their
    # own top-level sections above per the required content list).
    uncertainty = Column(Text, nullable=False, default="{}")  # JSON dict

    # --- R9.4: generation provenance ---
    generated_at = Column(
        DateTime,
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )
    generation_method = Column(String(100), nullable=False, default="deterministic-template")
    model_version = Column(String(100), nullable=True)
    config_version = Column(String(255), nullable=True)
    source_evidence = Column(Text, nullable=False, default="[]")  # JSON list
    generated_by = Column(String(255), nullable=True)

    # --- R9.3: analyst edit / acceptance tracking ---
    is_edited = Column(Boolean, nullable=False, default=False)
    edited_by = Column(String(255), nullable=True)
    edited_at = Column(DateTime, nullable=True)
    accepted_by = Column(String(255), nullable=True)
    accepted_at = Column(DateTime, nullable=True)

    version = Column(Integer, nullable=False, default=1)
    is_current = Column(Boolean, nullable=False, default=True)
    superseded_at = Column(DateTime, nullable=True)

    created_at = Column(
        DateTime,
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )

    def _get_json(self, field_name: str, default):
        raw = getattr(self, field_name)
        if not raw:
            return default
        try:
            return json.loads(raw)
        except (TypeError, ValueError):
            return default

    def get_business_profile(self) -> dict:
        return self._get_json("business_profile", {})

    def get_applicable_risk_categories(self) -> list:
        return self._get_json("applicable_risk_categories", [])

    def get_risk_indicators(self) -> list:
        return self._get_json("risk_indicators", [])

    def get_risk_statements(self) -> list:
        return self._get_json("risk_statements", [])

    def get_inherent_risk(self) -> dict:
        return self._get_json("inherent_risk", {})

    def get_evidence_references(self) -> list:
        return self._get_json("evidence_references", [])

    def get_mapped_controls(self) -> list:
        return self._get_json("mapped_controls", [])

    def get_control_effectiveness(self) -> dict:
        return self._get_json("control_effectiveness", {})

    def get_residual_risk(self) -> dict:
        return self._get_json("residual_risk", {})

    def get_risk_gaps(self) -> list:
        return self._get_json("risk_gaps", [])

    def get_assumptions(self) -> list:
        return self._get_json("assumptions", [])

    def get_missing_information(self) -> list:
        return self._get_json("missing_information", [])

    def get_recommended_conditions(self) -> list:
        return self._get_json("recommended_conditions", [])

    def get_required_approvals(self) -> list:
        return self._get_json("required_approvals", [])

    def get_uncertainty(self) -> dict:
        return self._get_json("uncertainty", {})

    def get_source_evidence(self) -> list:
        return self._get_json("source_evidence", [])

"""
P3 (R-GOV-03/04): separation of duties on the override ledger.

  proposal  -- FCRM Analyst (incl. Senior Analyst)
  review    -- an independent Senior Analyst, QA Reviewer or FCRM Manager
  approval  -- material: FCRM Manager or Head of FCRM; critical: Head of FCRM
               (critical overrides are also listed to the Committee)

The proposer, the reviewer, the approver and the assessment's owner (the
beneficiary) are always different people. A typed change (a factor
rating, the inherent override, the residual confirmation, a control remap,
an FCRM review rating) takes effect at once -- the calculation itself is
never changed -- but a material one still needs the independent review and
approval before the committee. Rules are PROVISIONAL configuration.
"""

from __future__ import annotations

import json

from sqlalchemy.orm import Session

from app.governance.materiality import CRITICAL, MATERIAL, NONMATERIAL, classify
from app.governance.policy import describe, holds, policy
from app.models.assessment import Assessment
from app.models.assessment_override import (
    REVIEW_APPLIED,
    REVIEW_CONFIRMED,
    REVIEW_PROPOSED,
    REVIEW_REJECTED,
    AssessmentOverride,
)
from app.models.user import User

ORIGIN_TYPED = "TYPED"
ORIGIN_PROPOSAL = "PROPOSAL"

APPROVAL_NOT_REQUIRED = "NOT_REQUIRED"
APPROVAL_PENDING = "PENDING"
APPROVAL_APPROVED = "APPROVED"
APPROVAL_REJECTED = "REJECTED"


def classify_entry(db: Session, entry: AssessmentOverride) -> None:
    """Sets materiality from the actual change. Does not commit."""

    level, reasons = classify(entry.section, entry.field_name, entry.ai_value, entry.human_value, db)
    entry.materiality = level
    entry.materiality_reasons = json.dumps(reasons)
    entry.approval_status = APPROVAL_NOT_REQUIRED if level == NONMATERIAL else None


def is_material(entry: AssessmentOverride) -> bool:
    return entry.materiality in {MATERIAL, CRITICAL}


# -- eligibility ----------------------------------------------------------------


def review_problem(assessment: Assessment, entry: AssessmentOverride, user: User) -> str | None:
    if entry.materiality is None:
        return "This override was recorded before independent review existed and cannot be reviewed."
    reviewable = entry.review_status == REVIEW_PROPOSED or (
        entry.review_status == REVIEW_APPLIED and is_material(entry)
    )
    if not reviewable:
        return f"This override is {(entry.review_status or 'legacy').lower()} and has nothing left to review."
    rule = policy()["override_reviewers"]
    if not holds(user, rule):
        return f"Overrides are reviewed by: {describe(rule)}."
    if user.id == entry.overridden_by_id:
        return "You proposed this override and cannot also review it."
    if user.id == assessment.owner_id:
        return "You own this assessment (it benefits from the override) and cannot review it."
    return None


def approval_problem(assessment: Assessment, entry: AssessmentOverride, user: User) -> str | None:
    if not is_material(entry):
        return "Only material and critical overrides need approval."
    if entry.review_status != REVIEW_CONFIRMED or entry.approval_status != APPROVAL_PENDING:
        if entry.review_status in {REVIEW_PROPOSED, REVIEW_APPLIED}:
            return "The independent review must confirm the override before it can be approved."
        return f"Nothing to approve: the override is {entry.review_status.lower()}, approval {(entry.approval_status or 'n/a').lower()}."
    rule = policy()["override_critical_approvers" if entry.materiality == CRITICAL else "override_material_approvers"]
    if not holds(user, rule):
        return f"A {entry.materiality.lower()} override is approved by: {describe(rule)}."
    if user.id == entry.overridden_by_id:
        return "You proposed this override and cannot also approve it."
    if user.id == entry.reviewed_by_id:
        return "You reviewed this override; a different person must approve it."
    if user.id == assessment.owner_id:
        return "You own this assessment (it benefits from the override) and cannot approve it."
    return None


def propose_problem(user: User) -> str | None:
    rule = policy()["override_proposers"]
    if not holds(user, rule):
        return f"Overrides are proposed by: {describe(rule)}."
    return None


# -- what is still in effect ---------------------------------------------------


def in_effect(db: Session, entry: AssessmentOverride) -> bool:
    """Whether the human value is what the record holds now."""

    from app.models.assessment_fcrm_review import AssessmentFcrmReview
    from app.models.control import CONTROL_CONFIG_FIELDS, Control
    from app.models.risk_factor import RiskFactor
    from app.services.residual_risk_service import current_inherent_calculation, current_residual_calculation

    if entry.origin != ORIGIN_TYPED:
        return entry.review_status == REVIEW_CONFIRMED and entry.approval_status in {APPROVAL_NOT_REQUIRED, APPROVAL_APPROVED}

    if entry.section == "INHERENT_RISK":
        calc = current_inherent_calculation(db, entry.assessment_id)
        return bool(calc and calc.overridden and calc.override_value is not None
                    and f"{calc.override_value:g} ({calc.override_band})" == entry.human_value)
    if entry.section == "RESIDUAL_RISK":
        calc = current_residual_calculation(db, entry.assessment_id)
        if not calc or not calc.confirmed_band:
            return False
        current = calc.confirmed_band if calc.confirmed_score is None else f"{calc.confirmed_score:g} ({calc.confirmed_band})"
        return current == entry.human_value
    if entry.section == "FACTOR_RATING":
        factor = db.get(RiskFactor, int(entry.entity_id)) if entry.entity_id else None
        return bool(factor and factor.is_current and f"{factor.likelihood} x {factor.impact}" == entry.human_value)
    if entry.section == "FCRM_REVIEW":
        review = db.query(AssessmentFcrmReview).filter(AssessmentFcrmReview.assessment_id == entry.assessment_id).first()
        try:
            ratings = {str(k): str(v) for k, v in json.loads(review.human_ratings or "{}").items()} if review else {}
        except ValueError:
            ratings = {}
        return ratings.get(str(entry.entity_id)) == entry.human_value
    if entry.section == "CONTROL_MAPPING":
        control = db.get(Control, int(entry.entity_id)) if entry.entity_id else None
        if control is None:
            return False
        if entry.field_name == "unmapped":
            return not control.is_current
        try:
            wanted = json.loads(entry.human_value)
        except ValueError:
            return False
        return control.is_current and all(
            str(getattr(control, k)) == str(v) for k, v in wanted.items() if k in CONTROL_CONFIG_FIELDS
        )
    return True


def state(db: Session, entry: AssessmentOverride) -> str:
    """One label for where the entry stands in the governance workflow."""

    if entry.materiality is None:
        return "LEGACY"
    if entry.review_status in {REVIEW_REJECTED} or entry.approval_status == APPROVAL_REJECTED:
        if is_material(entry) and entry.origin == ORIGIN_TYPED and in_effect(db, entry):
            return "REJECTED_STILL_IN_EFFECT"
        return "REJECTED_RESOLVED"
    if not is_material(entry):
        return "PROPOSED_NONMATERIAL" if entry.review_status == REVIEW_PROPOSED else "NONMATERIAL_DONE"
    if entry.review_status in {REVIEW_PROPOSED, REVIEW_APPLIED}:
        return "PENDING_REVIEW"
    if entry.approval_status == APPROVAL_PENDING:
        return "PENDING_APPROVAL"
    return "APPROVED"


def actions_for(db: Session, assessment: Assessment, entry: AssessmentOverride, user: User) -> dict:
    def entry_of(problem):
        return {"allowed": problem is None, "reason": problem}

    return {
        "review": entry_of(review_problem(assessment, entry, user)),
        "approve": entry_of(approval_problem(assessment, entry, user)),
    }

"""
P3 governance policy: who may do what in the governance workflow.

STATUS: PROVISIONAL -- pending governance approval. These defaults
implement the recommendations R-GOV-01 to R-GOV-04 (P3 brief, 2026-10-02)
as configuration. They are NOT approved policy; every API that applies
them reports `policy_status`, and docs/REMAINING_REQUIREMENTS.md lists them
as open decisions.

Configuration: the defaults below, overridden key by key by a JSON file
named in GOVERNANCE_POLICY_FILE (top-level keys replace the default for
that key). Nothing here is read from a request.
"""

from __future__ import annotations

import copy
import json
import os
from functools import lru_cache

POLICY_STATUS = "PROVISIONAL_PENDING_GOVERNANCE_APPROVAL"

# -- governance designations held on top of a base role --------------------

SENIOR_ANALYST = "SENIOR_ANALYST"
QA_REVIEWER = "QA_REVIEWER"
CHALLENGE_REVIEWER = "CHALLENGE_REVIEWER"
HEAD_OF_FCRM = "HEAD_OF_FCRM"
FCRM_GOVERNANCE_OWNER = "FCRM_GOVERNANCE_OWNER"
COMPLIANCE_MANAGER = "COMPLIANCE_MANAGER"
COMMITTEE_CHAIR = "COMMITTEE_CHAIR"
# Committee seats for the quorum composition (G-5, user direction
# 2026-10-03): the FCRM/Compliance and the Business Risk representative.
COMMITTEE_FCRM_REP = "COMMITTEE_FCRM_COMPLIANCE_REP"
COMMITTEE_BUSINESS_RISK_REP = "COMMITTEE_BUSINESS_RISK_REP"

DESIGNATIONS = [
    SENIOR_ANALYST,
    QA_REVIEWER,
    CHALLENGE_REVIEWER,
    HEAD_OF_FCRM,
    FCRM_GOVERNANCE_OWNER,
    COMPLIANCE_MANAGER,
    COMMITTEE_CHAIR,
    COMMITTEE_FCRM_REP,
    COMMITTEE_BUSINESS_RISK_REP,
]

DEFAULT_POLICY: dict = {
    # Base roles each designation may be held with.
    "designation_base_roles": {
        SENIOR_ANALYST: ["FCRM_ANALYST"],
        QA_REVIEWER: ["FCRM_ANALYST", "MANAGER"],
        CHALLENGE_REVIEWER: ["FCRM_ANALYST", "MANAGER"],
        HEAD_OF_FCRM: ["MANAGER"],
        FCRM_GOVERNANCE_OWNER: ["MANAGER"],
        COMPLIANCE_MANAGER: ["MANAGER"],
        COMMITTEE_CHAIR: ["COMMITTEE_MEMBER"],
        COMMITTEE_FCRM_REP: ["COMMITTEE_MEMBER"],
        COMMITTEE_BUSINESS_RISK_REP: ["COMMITTEE_MEMBER"],
    },
    # -- SoD exceptions (R-GOV-01) --
    "exception_approvers": {
        # Standard / temporary exceptions.
        "STANDARD": {"designations": [FCRM_GOVERNANCE_OWNER, HEAD_OF_FCRM, COMPLIANCE_MANAGER]},
        # High-risk, repeated, long-term or enterprise-wide exceptions.
        "COMMITTEE": {"designations": [COMMITTEE_CHAIR]},
    },
    "exception_committee_tier": {
        "risk_levels": ["HIGH", "CRITICAL"],
        # The assessment's own band also routes to the Committee tier.
        "assessment_bands": ["HIGH", "CRITICAL"],
        "max_standard_days": 30,
        "enterprise_wide": True,
        "repeat_threshold": 2,  # other exceptions for the same person ...
        "repeat_window_days": 365,  # ... within this many days
    },
    # Assumption (not in the brief): mirrors the 90-day delegation limit.
    "exception_max_days": 90,
    "exception_expiry_warning_days": 7,
    # Who may revoke an approved exception (besides its approver tier).
    "exception_revokers": {"roles": ["ADMIN"], "designations": [FCRM_GOVERNANCE_OWNER, HEAD_OF_FCRM, COMMITTEE_CHAIR]},
    # -- Admin vs committee (R-GOV-02) --
    # Mutually exclusive by default; an approved ADMIN_COMMITTEE_DUAL_ROLE
    # exception (with a conflict-of-interest declaration) is the only route.
    "admin_committee_exclusive": True,
    # -- Overrides (R-GOV-03) --
    "override_proposers": {"roles": ["FCRM_ANALYST"]},
    "override_reviewers": {"roles": ["MANAGER"], "designations": [SENIOR_ANALYST, QA_REVIEWER]},
    "override_material_approvers": {"roles": ["MANAGER"], "designations": [HEAD_OF_FCRM]},
    "override_critical_approvers": {"designations": [HEAD_OF_FCRM]},
    # -- Challenge review (R-GOV-03) --
    "challenge_reviewers": {"designations": [CHALLENGE_REVIEWER, SENIOR_ANALYST, QA_REVIEWER]},
    "challenge_signoff": {"roles": ["MANAGER"], "designations": [HEAD_OF_FCRM]},
    # CRITICAL cases are flagged to the Committee on sign-off.
    "challenge_escalation_bands": ["CRITICAL"],
    # -- Readiness (R-GOV-04) --
    # OPEN findings at these severities block; LOW never blocks (listed
    # only). User direction 2026-10-03 (G-4/G-5):
    #  * HIGH and CRITICAL block committee submission until RESOLVED; they
    #    can't be accepted (an acceptance recorded under the earlier rule
    #    no longer counts).
    #  * Only MEDIUM may be accepted, as a documented Committee exception
    #    by an eligible committee member while the Committee holds the
    #    case. An open MEDIUM finding doesn't stop submission; it blocks
    #    the final decision until resolved or so accepted.
    "blocking_finding_severities": ["CRITICAL", "HIGH", "MEDIUM"],
    "acceptable_finding_severities": ["MEDIUM"],
    "finding_acceptors": {"roles": ["COMMITTEE_MEMBER"]},
    "finding_acceptance_statuses": ["READY_FOR_COMMITTEE", "COMMITTEE_REVIEW", "DEFERRED"],
    # -- Committee quorum (G-5, user direction 2026-10-03) --
    # A final decision (approve, approve with conditions, reject) needs
    # current votes from `min_members` eligible members, including one
    # FCRM/Compliance and one independent Business Risk representative
    # (different people). Never eligible: the requester, the requester's
    # manager, the case's manager, anyone who prepared the case, and
    # anyone conflicted (an SoD exception may let them vote, but they
    # never count). Abstentions don't count. On submission the committee
    # must be able to form such a quorum at all.
    "committee_quorum": {
        "enabled": True,
        "min_members": 3,
        "fcrm_compliance_designations": [COMMITTEE_FCRM_REP],
        "business_risk_designations": [COMMITTEE_BUSINESS_RISK_REP],
        "count_abstentions": False,
        "check_availability_on_submission": True,
    },
    # Intake / profile fields whose correction is material (they feed
    # triage, scoping or escalation).
    "material_intake_fields": [
        "countries_jurisdictions",
        "countries",
        "delivery_channels",
        "channels",
        "customer_segment",
        "customer_segments",
        "customer_type",
        "expected_transaction_volume",
        "expected_transaction_value",
        "transaction_volume",
        "transaction_types",
        "third_party_vendor_usage",
        "third_party_vendors",
        "technology_process_changes",
        "technologies",
        "shell_company_indicator",
        "ownership_entity_structure",
        "onboarding_approach",
        "payment_methods",
        "change_type",
    ],
    "material_control_fields": ["risk_factor_id", "control_type", "operating_status", "unmapped"],
    # Indicators whose change is CRITICAL (they drive escalation rules);
    # the active methodology's escalation-rule indicators are added.
    "critical_indicators": ["SANCTIONS_EXPOSURE", "COMPLEX_OWNERSHIP_STRUCTURES"],
    # -- Retention (P5, R16.4) -- PROVISIONAL, pending governance approval.
    # No period is compliance-approved (Q-3). See app/governance/retention.py.
    "retention": {
        # Configured record types and what their period runs from. Only
        # ASSESSMENT has a period; future types (DOCUMENT, AUDIT_EVENT,
        # AI_USAGE_LOG) are added here with an approved period, never
        # invented. An unconfigured type has no policy and is never eligible.
        "record_types": {"ASSESSMENT": {"basis": "FINAL_DECISION_DATE"}},
        "proposers": {"roles": ["ADMIN"]},
        "approvers": {"designations": [FCRM_GOVERNANCE_OWNER, COMPLIANCE_MANAGER]},
        # D-1: a change takes effect only when someone other than the
        # proposer, holding an approver designation, approves it.
        "require_independent_approval": True,
        "legal_hold_setters": {"roles": ["ADMIN"], "designations": [COMPLIANCE_MANAGER]},
        "legal_hold_releasers": {"roles": ["ADMIN"], "designations": [COMPLIANCE_MANAGER]},
        # D-2: the user who set a hold can't release it.
        "release_requires_different_user": True,
        # D-3: placeholder bounds.
        "min_retention_days": 365,
        "max_retention_days": 36500,
        "min_reason_length": 20,
        "soft_deleters": {"roles": ["ADMIN"]},
        # D-4: Auditors read the report (read-only, no assessment content).
        "eligibility_report_readers": {"roles": ["ADMIN", "AUDITOR"]},
    },
}


@lru_cache(maxsize=1)
def _loaded() -> dict:
    policy = copy.deepcopy(DEFAULT_POLICY)
    path = os.getenv("GOVERNANCE_POLICY_FILE")
    if path:
        with open(path, encoding="utf-8") as handle:
            overrides = json.load(handle)
        for key, value in overrides.items():
            if key in policy:
                policy[key] = value
    return policy


def policy() -> dict:
    return copy.deepcopy(_loaded())


def reload() -> None:
    _loaded.cache_clear()


def holds(user, rule: dict) -> bool:
    """Whether `user` satisfies a {"roles": [...], "designations": [...]} rule.
    A designation only counts when held with a permitted base role."""

    if user is None:
        return False
    if user.role in rule.get("roles", []):
        return True
    base_roles = _loaded()["designation_base_roles"]
    for designation in rule.get("designations", []):
        if user.has_designation(designation) and user.role in base_roles.get(designation, []):
            return True
    return False


def describe(rule: dict) -> str:
    parts = [r.replace("_", " ").title() for r in rule.get("roles", [])]
    parts += [d.replace("_", " ").title() for d in rule.get("designations", [])]
    return " or ".join(parts) or "nobody"

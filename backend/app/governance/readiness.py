"""
P3 (R-GOV-04): committee readiness -- one authoritative, server-side,
explainable evaluation used for committee submission (entry into Ready for
Committee / Committee Review) and for the final committee decision. The
transition guard, the decision endpoint and GET .../readiness all call
`evaluate`; the UI only displays what it returns.

Each blocker names the reason, the responsible role and the next action.
Rules are PROVISIONAL configuration (app/governance/policy.py).
"""

from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from app.governance.policy import POLICY_STATUS, policy
from app.models.assessment import Assessment

COMMITTEE_SUBMISSION = "COMMITTEE_SUBMISSION"
FINAL_DECISION = "FINAL_DECISION"


def _blocker(code: str, message: str, responsible: str, next_action: str, items: list | None = None) -> dict:
    return {"code": code, "message": message, "responsible_role": responsible, "next_action": next_action, "items": items or []}


def _override_item(entry, state: str) -> dict:
    return {
        "id": entry.id,
        "section": entry.section,
        "field_name": entry.field_name,
        "entity_id": entry.entity_id,
        "materiality": entry.materiality,
        "state": state,
        "proposed_by": entry.overridden_by,
        "system_value": entry.ai_value,
        "human_value": entry.human_value,
    }


def evaluate(db: Session, assessment: Assessment, purpose: str = COMMITTEE_SUBMISSION) -> dict[str, Any]:
    from app.api.assessments import get_missing_mandatory_fields
    from app.challenge_engine.engine import recompute_challenge_review
    from app.governance import overrides as override_rules
    from app.models.assessment_comment import AssessmentComment
    from app.models.assessment_override import AssessmentOverride
    from app.models.challenge_review import ChallengeFinding
    from app.models.committee_vote import CommitteeVote
    from app.models.sod_exception import STATUS_APPROVED, STATUS_PENDING, SodException
    from app.services.challenge_signoff import current_review, current_signoff, signoff_payload, signoff_problem

    rules = policy()
    blockers: list[dict] = []
    warnings: list[dict] = []

    # -- prerequisites carried over from Stage 14 -------------------------------
    missing = get_missing_mandatory_fields(
        {column.name: getattr(assessment, column.name) for column in Assessment.__table__.columns}
    )
    if missing:
        blockers.append(_blocker("MANDATORY_FIELDS", "Mandatory intake fields missing: " + ", ".join(missing) + ".",
                                 "Business owner", "Complete the intake fields."))
    if assessment.residual_risk_level is None:
        blockers.append(_blocker("RESIDUAL_NOT_CALCULATED", "Residual risk has not been calculated.",
                                 "FCRM Analyst", "Advance the assessment to the Residual Risk stage."))
    unresolved = [
        c for c in db.query(AssessmentComment).filter(
            AssessmentComment.assessment_id == assessment.id, AssessmentComment.resolved.is_(False)
        ).all()
        if not (c.exception_reason or "").strip()
    ]
    if unresolved:
        blockers.append(_blocker("UNRESOLVED_COMMENTS", f"{len(unresolved)} unresolved analyst review comment(s).",
                                 "FCRM Analyst / Manager", "Resolve the comments or accept an exception with a reason.",
                                 [c.id for c in unresolved]))

    # -- challenge findings -----------------------------------------------------
    recompute_challenge_review(db, assessment.id)
    db.flush()
    findings = db.query(ChallengeFinding).filter(ChallengeFinding.assessment_id == assessment.id).all()
    blocking_severities = set(rules["blocking_finding_severities"])
    open_findings = [f for f in findings if f.resolution_status == "OPEN"]
    by_severity: dict[str, dict[str, int]] = {}
    for f in findings:
        by_severity.setdefault(f.severity, {}).setdefault(f.resolution_status, 0)
        by_severity[f.severity][f.resolution_status] += 1
    acceptable = set(rules["acceptable_finding_severities"])
    # HIGH/CRITICAL (any severity that can't be accepted) block until
    # RESOLVED: an acceptance recorded under the earlier rule doesn't count.
    high = [
        f for f in findings
        if f.severity in blocking_severities and f.severity not in acceptable and f.resolution_status != "RESOLVED"
    ]
    # MEDIUM: open, or accepted under the earlier rule (not as a Committee
    # exception), until resolved or accepted by the Committee.
    medium = [
        f for f in findings
        if f.severity in blocking_severities & acceptable
        and (f.resolution_status == "OPEN" or (f.resolution_status == "ACCEPTED" and f.acceptance_authority != "COMMITTEE"))
    ]
    low = [f for f in open_findings if f.severity not in blocking_severities]
    if high:
        blockers.append(_blocker("HIGH_FINDINGS_OPEN",
                                 f"{len(high)} high/critical challenge finding(s) are not resolved. They can't be accepted.",
                                 "FCRM Analyst", "Resolve each high/critical finding before committee submission.",
                                 [{"id": f.id, "severity": f.severity, "category": f.category, "status": f.resolution_status} for f in high]))
    if medium:
        medium_items = [{"id": f.id, "severity": f.severity, "category": f.category, "status": f.resolution_status} for f in medium]
        message = (f"{len(medium)} medium-severity challenge finding(s) are neither resolved nor accepted "
                   "as a documented Committee exception.")
        if purpose == FINAL_DECISION:
            blockers.append(_blocker("MEDIUM_FINDINGS_OPEN", message, "FCRM Analyst (resolve) / Committee (accept)",
                                     "Resolve each finding, or have an eligible committee member accept it as a documented Committee exception.",
                                     medium_items))
        else:
            warnings.append({"code": "MEDIUM_FINDINGS_OPEN",
                             "message": message + " Submission is allowed; the final decision is blocked until they are dealt with.",
                             "items": medium_items})
    if low:
        warnings.append({"code": "LOW_FINDINGS_OPEN", "message": f"{len(low)} low-severity finding(s) open (not blocking).",
                         "items": [{"id": f.id, "severity": f.severity, "category": f.category} for f in low]})
    accepted = [f for f in findings if f.resolution_status == "ACCEPTED" and f.acceptance_authority == "COMMITTEE"]
    if accepted:
        warnings.append({"code": "FINDINGS_ACCEPTED", "message": f"{len(accepted)} finding(s) accepted as documented Committee exceptions rather than resolved.",
                         "items": [{"id": f.id, "severity": f.severity, "accepted_by": f.accepted_by, "reason": f.accepted_reason} for f in accepted]})

    # -- challenge review and sign-off ----------------------------------------
    problem = signoff_problem(db, assessment)
    if problem:
        responsible = "Challenge Reviewer / Senior Analyst / QA Reviewer" if "review has not been completed" in problem or "review must be completed" in problem else "FCRM Manager / Head of FCRM"
        blockers.append(_blocker("CHALLENGE_REVIEW_INCOMPLETE", problem, responsible,
                                 "Complete the independent challenge review, then have it signed off."))
    review, signoff = current_review(db, assessment.id), current_signoff(db, assessment.id)
    if signoff is not None and signoff.committee_escalation:
        warnings.append({"code": "CRITICAL_ESCALATED", "message": "Critical case: escalated to the Committee by the challenge sign-off."})

    # -- overrides ---------------------------------------------------------------
    pending_review, pending_approval, rejected_in_effect, legacy, critical = [], [], [], [], []
    for entry in db.query(AssessmentOverride).filter(AssessmentOverride.assessment_id == assessment.id).all():
        state = override_rules.state(db, entry)
        item = _override_item(entry, state)
        if state == "PENDING_REVIEW":
            pending_review.append(item)
        elif state == "PENDING_APPROVAL":
            pending_approval.append(item)
        elif state == "REJECTED_STILL_IN_EFFECT":
            rejected_in_effect.append(item)
        elif state == "LEGACY":
            legacy.append(item)
        if entry.materiality == "CRITICAL":
            critical.append(item)
    if pending_review:
        blockers.append(_blocker("OVERRIDE_PENDING_REVIEW", f"{len(pending_review)} material/critical override(s) await independent review.",
                                 "Senior Analyst / QA Reviewer / FCRM Manager", "Review each override (confirm or reject, with a note).", pending_review))
    if pending_approval:
        blockers.append(_blocker("OVERRIDE_PENDING_APPROVAL", f"{len(pending_approval)} material/critical override(s) await approval.",
                                 "FCRM Manager / Head of FCRM (critical: Head of FCRM)", "Approve or reject each override with a rationale.", pending_approval))
    if rejected_in_effect:
        blockers.append(_blocker("OVERRIDE_REJECTED_IN_EFFECT",
                                 f"{len(rejected_in_effect)} rejected material override(s) are still applied to the assessment.",
                                 "FCRM Analyst", "Correct the value (e.g. remove or redo the override), so the rejected change no longer applies.",
                                 rejected_in_effect))
    if critical:
        warnings.append({"code": "CRITICAL_OVERRIDES", "message": f"{len(critical)} critical override(s) -- escalated to the Committee for visibility.", "items": critical})
    if legacy:
        warnings.append({"code": "LEGACY_OVERRIDES", "message": f"{len(legacy)} override(s) recorded before independent review existed (not classified).", "items": legacy})

    # -- separation of duties ----------------------------------------------------
    conflicted = {assessment.owner_id, assessment.manager_id, assessment.manager_decided_by_id} - {None}
    active = db.query(SodException).filter(SodException.assessment_id == assessment.id, SodException.status == STATUS_APPROVED).all()
    excepted = {e.affected_user_id for e in active if e.exception_type == "COMMITTEE_SEPARATION"}
    bad_votes = [
        v for v in db.query(CommitteeVote).filter(CommitteeVote.assessment_id == assessment.id, CommitteeVote.is_current.is_(True)).all()
        if (v.cast_by_id or v.member_id) in conflicted - excepted or v.member_id in conflicted - excepted
    ]
    if bad_votes:
        blockers.append(_blocker("SOD_CONFLICT_UNRESOLVED",
                                 f"{len(bad_votes)} committee vote(s) were cast by a conflicted person without an approved exception.",
                                 "Committee Chair", "The conflicted member must re-cast as ABSTAIN, or an SoD exception must be approved.",
                                 [{"vote_id": v.id, "member_id": v.member_id} for v in bad_votes]))
    pending_exceptions = db.query(SodException).filter(SodException.assessment_id == assessment.id, SodException.status == STATUS_PENDING).all()
    if pending_exceptions:
        warnings.append({"code": "SOD_EXCEPTION_PENDING", "message": f"{len(pending_exceptions)} SoD exception request(s) for this assessment await approval.",
                         "items": [e.reference for e in pending_exceptions]})

    # -- committee quorum (G-5) ---------------------------------------------------
    from app.governance import quorum as quorum_rules

    quorum = quorum_rules.evaluate(db, assessment)
    if quorum["enabled"]:
        if purpose == FINAL_DECISION and not quorum["met"]:
            blockers.append(_blocker("COMMITTEE_QUORUM_NOT_MET",
                                     "The committee quorum is not met: " + "; ".join(quorum["missing"]) + ".",
                                     "Committee Chair",
                                     "Eligible members, including the FCRM/Compliance and Business Risk representatives, cast their votes.",
                                     quorum["seats"]))
        if purpose == COMMITTEE_SUBMISSION and quorum_rules.rules()["check_availability_on_submission"]:
            pool = quorum_rules.availability(db, assessment)
            if not pool["available"]:
                blockers.append(_blocker("COMMITTEE_QUORUM_UNAVAILABLE",
                                         "The committee can't form an eligible quorum for this case: " + "; ".join(pool["missing"]) + ".",
                                         "Admin / Committee Chair",
                                         "Appoint eligible committee members and assign the FCRM/Compliance and Business Risk representative designations."))

    return {
        "assessment_id": assessment.id,
        "purpose": purpose,
        "ready": not blockers,
        "status": "READY" if not blockers else "BLOCKED",
        "blockers": blockers,
        "warnings": warnings,
        "next_action": blockers[0]["next_action"] if blockers else "Ready for the committee.",
        "overrides": {"pending_review": pending_review, "pending_approval": pending_approval, "rejected_in_effect": rejected_in_effect},
        "challenge": {
            "review": signoff_payload(review) if review else None,
            "signoff": signoff_payload(signoff) if signoff else None,
            "problem": problem,
        },
        "findings_by_severity": by_severity,
        "quorum": quorum,
        "sod": {
            "active_exceptions": [
                {"id": e.id, "reference": e.reference, "type": e.exception_type, "affected_user_id": e.affected_user_id,
                 "end_at": e.end_at.isoformat(), "declared": e.declared_at is not None}
                for e in active
            ],
            "pending_exceptions": [e.reference for e in pending_exceptions],
        },
        "policy_status": POLICY_STATUS,
    }


def blocker_messages(db: Session, assessment: Assessment, purpose: str = COMMITTEE_SUBMISSION) -> list[str]:
    return [b["message"] for b in evaluate(db, assessment, purpose)["blockers"]]


def log_block(assessment_id: int, user, purpose: str, blockers: list[dict]) -> None:
    """Audit a refused committee submission / decision. Written in its own
    session, because the refused request's transaction is rolled back.
    Never raises."""

    from app.database import SessionLocal
    from app.services.audit_service import AuditAction, log_audit_event

    db = SessionLocal()
    try:
        log_audit_event(
            db,
            assessment_id=assessment_id,
            action=AuditAction.GOVERNANCE_READINESS_BLOCKED,
            actor=(user.full_name or user.email) if user else "System",
            actor_id=user.id if user else None,
            details=f"{purpose} refused: " + "; ".join(f"[{b['code']}] {b['message']}" for b in blockers),
        )
        db.commit()
    except Exception:  # noqa: BLE001 -- logging must not change the response
        db.rollback()
    finally:
        db.close()

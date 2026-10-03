"""
G-5 (user direction 2026-10-03): the committee quorum.

A final committee decision (approve, approve with conditions, reject)
needs current votes from at least `min_members` ELIGIBLE members, among
them one FCRM/Compliance representative and one independent Business Risk
representative, who must be different people. Deferral needs no quorum.

Never eligible to count (even when an SoD exception lets them vote):
  * the requester (owner) and the requester's own manager;
  * the case's manager and whoever took the manager decision;
  * anyone who prepared the case (app/governance/independence.py: factor
    raters, override proposers, the FCRM reviewer, control assessors);
  * a delegate or member who is any of the above (a delegate's vote
    fills the absent member's seat: both people must be eligible).
Abstentions don't count unless configured.

A representative is identified by a designation an Admin assigns (held
with the Committee Member role): COMMITTEE_FCRM_COMPLIANCE_REP and
COMMITTEE_BUSINESS_RISK_REP. "Independent" means not a party to, nor a
preparer of, the case; the system holds no home business unit per user,
so a same-business-unit test is not applied.

STATUS: PROVISIONAL (policy key `committee_quorum`).
"""

from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from app.governance.policy import policy
from app.models.assessment import Assessment
from app.models.user import User, UserRole


def rules() -> dict:
    return policy()["committee_quorum"]


def excluded(db: Session, assessment: Assessment) -> tuple[dict[int, str], set[str]]:
    """({user id: why}, {recorded names}) of people who never count toward
    this case's quorum."""

    from app.governance.independence import involved

    why: dict[int, str] = {}

    def add(user_id: int | None, reason: str) -> None:
        if user_id and user_id not in why:
            why[user_id] = reason

    add(assessment.owner_id, "requester")
    if assessment.owner_id:
        owner = db.get(User, assessment.owner_id)
        if owner is not None:
            add(owner.manager_id, "the requester's manager")
    add(assessment.manager_id, "the case's manager")
    add(assessment.manager_decided_by_id, "took the manager decision")
    ids, names = involved(db, assessment)
    for user_id in ids:
        add(user_id, "prepared the case")
    return why, names


def ineligible_reason(user: User | None, why: dict[int, str], names: set[str]) -> str | None:
    if user is None:
        return "unknown user"
    if user.id in why:
        return why[user.id]
    if (user.full_name or user.email) in names or user.email in names:
        return "prepared the case"
    return None


def _is_rep(user: User, designations: list[str]) -> bool:
    return user.role == UserRole.COMMITTEE_MEMBER.value and any(user.has_designation(d) for d in designations)


def _composition(members: list[User], cfg: dict) -> tuple[bool, list[str]]:
    """Whether `members` (eligible, distinct) form a quorum, and what's missing."""

    missing: list[str] = []
    fcrm = [m for m in members if _is_rep(m, cfg["fcrm_compliance_designations"])]
    business = [m for m in members if _is_rep(m, cfg["business_risk_designations"])]
    if len(members) < cfg["min_members"]:
        missing.append(f"{cfg['min_members']} eligible voting members are needed ({len(members)} so far)")
    if not fcrm:
        missing.append("an FCRM/Compliance representative")
    if not business:
        missing.append("an independent Business Risk representative")
    # The two representatives must be different people.
    if fcrm and business and not any(f.id != b.id for f in fcrm for b in business):
        missing.append("the FCRM/Compliance and Business Risk representatives must be different people")
    return not missing, missing


def evaluate(db: Session, assessment: Assessment) -> dict[str, Any]:
    """The quorum formed by the current votes on `assessment`."""

    from app.models.committee_vote import CommitteeVote

    cfg = rules()
    why, names = excluded(db, assessment)
    counted: list[User] = []
    seats: list[dict] = []
    for vote in (
        db.query(CommitteeVote)
        .filter(CommitteeVote.assessment_id == assessment.id, CommitteeVote.is_current.is_(True))
        .order_by(CommitteeVote.id)
        .all()
    ):
        member = db.get(User, vote.member_id)
        caster = db.get(User, vote.cast_by_id or vote.delegate_id or vote.member_id)
        reason = ineligible_reason(member, why, names) or (
            None if caster is None or caster.id == vote.member_id else ineligible_reason(caster, why, names)
        )
        if reason is None and vote.vote == "ABSTAIN" and not cfg["count_abstentions"]:
            reason = "abstained"
        if reason is None and member is not None and member.id in {m.id for m in counted}:
            reason = "already counted"
        seats.append({"member_id": vote.member_id, "member_name": vote.member_name, "vote": vote.vote,
                      "counts": reason is None, "reason": reason})
        if reason is None and member is not None:
            counted.append(member)

    met, missing = _composition(counted, cfg)
    return {
        "enabled": cfg["enabled"],
        "met": met or not cfg["enabled"],
        "required_members": cfg["min_members"],
        "counted_members": len(counted),
        "missing": missing,
        "seats": seats,
    }


def availability(db: Session, assessment: Assessment) -> dict[str, Any]:
    """Whether the active committee could form an eligible quorum for this
    case at all (checked on committee submission)."""

    cfg = rules()
    why, names = excluded(db, assessment)
    pool = [
        u
        for u in db.query(User).filter(User.role == UserRole.COMMITTEE_MEMBER.value, User.is_active.is_(True)).all()
        if ineligible_reason(u, why, names) is None
    ]
    met, missing = _composition(pool, cfg)
    return {"available": met or not cfg["enabled"], "eligible_members": len(pool), "missing": missing}

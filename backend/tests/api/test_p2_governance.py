"""
P2 governance records (docs/REMAINING_REQUIREMENTS.md):

R12.6        committee votes are append-only; a re-cast supersedes, with a reason
Q-6          administration is not committee membership (configurable)
R6.7/R10.2-4 overrides: system value read server-side, independent review,
             calculated values never changed, comparison in the decision package
R11          mandatory challenge-review sign-off gates the committee
R7.2/R10.2   versioned control edit / remap / unmap; design inadequacy -> gap;
             a control change re-freezes a frozen residual
R15          role, scope and separation-of-duties enforcement, denials logged
"""

import json
from datetime import datetime, timedelta, timezone

import pytest

import app.challenge_engine.engine as challenge_engine
from app.auth.security import hash_password
from app.control_engine.scoring import points_for_risk
from app.database import SessionLocal
from app.models.assessment import Assessment
from app.models.assessment_override import AssessmentOverride
from app.models.audit_event import AuditEvent
from app.models.challenge_review import ChallengeFinding
from app.models.committee_vote import CommitteeVote
from app.models.control import ControlRevision
from app.models.inherent_risk_calculation import InherentRiskCalculation
from app.models.residual_risk_calculation import ResidualRiskCalculation
from app.models.risk_factor import RiskFactor
from app.models.user import User
from app.services.data_protection import ProtectedDataError
from tests.conftest import PASSWORD, ok

# -- helpers -----------------------------------------------------------------


def _user(email: str, role: str, **scope) -> int:
    db = SessionLocal()
    try:
        user = db.query(User).filter(User.email == email).first()
        if user is None:
            user = User(email=email, hashed_password=hash_password(PASSWORD), full_name=email.split("@")[0].title(), role=role)
            db.add(user)
        for field, values in scope.items():
            user.set_scope(field, values)
        db.commit()
        return user.id
    finally:
        db.close()


def _login(client, email: str) -> dict:
    token = ok(client.post("/api/auth/login", json={"email": email, "password": PASSWORD}))["access_token"]
    return {"Authorization": f"Bearer {token}"}


def _events(assessment_id: int, action: str, actor_id: int | None = None) -> list[AuditEvent]:
    db = SessionLocal()
    try:
        query = db.query(AuditEvent).filter(AuditEvent.assessment_id == assessment_id, AuditEvent.action == action)
        if actor_id is not None:
            query = query.filter(AuditEvent.actor_id == actor_id)
        return query.all()
    finally:
        db.close()


def _at(aid: int, status: str, seed: bool = True, **fields) -> int:
    """Place an assessment at `status` with genuine decision inputs."""

    from decision_fixture import seed_decision_inputs
    from app.services import workflow

    db = SessionLocal()
    try:
        assessment = db.get(Assessment, aid)
        if seed:
            seed_decision_inputs(db, aid)
        assessment.status = status
        for key, value in fields.items():
            setattr(assessment, key, value)
        assessment.workflow_status = workflow.derive_workflow_status(db, assessment)
        db.commit()
    finally:
        db.close()
    return aid


@pytest.fixture
def no_challenge_triggers(monkeypatch):
    """A challenge evaluation in which nothing fires and nothing is found."""

    import app.api.approvals as approvals_api

    evaluation = lambda db, assessment_id: {  # noqa: E731
        "triggered": False,
        "triggers": [{"name": "RISK_HIGH_OR_CRITICAL", "fired": False}],
        "findings": [],
    }
    for module in (challenge_engine, approvals_api):
        monkeypatch.setattr(module, "recompute_challenge_review", evaluation)
        monkeypatch.setattr(module, "has_blocking_findings", lambda db, assessment_id: False)


def _review(client, auth, aid, who="senior", reason="Independent challenge review completed."):
    """P3: the independent challenge review (Challenge Reviewer / Senior
    Analyst / QA Reviewer who did not prepare the case)."""

    return client.post(f"/api/assessments/{aid}/challenge-review/review", json={"reason": reason}, headers=auth(who))


def _sign_off(client, auth, aid, who="manager", reason="Challenge review signed off."):
    """P3: the sign-off that follows it (FCRM Manager / Head of FCRM)."""

    return client.post(f"/api/assessments/{aid}/challenge-review/signoff", json={"reason": reason}, headers=auth(who))


def _complete_challenge(client, auth, aid):
    ok(_review(client, auth, aid), 201)
    ok(_sign_off(client, auth, aid), 201)


def _committee_ready(client, auth, users, create_assessment) -> int:
    aid = create_assessment()["id"]
    _at(aid, "SUBMITTED_TO_MANAGER", manager_id=users["manager"])
    _complete_challenge(client, auth, aid)
    ok(client.post(f"/api/assessments/{aid}/manager-decision", json={"decision": "approve", "comment": "ok"}, headers=auth("manager")))
    return aid


# -- R12.6 append-only committee votes ---------------------------------------


def test_recast_vote_keeps_the_full_history(client, auth, users, create_assessment, no_challenge_triggers):
    aid = _committee_ready(client, auth, users, create_assessment)

    first = ok(client.post(f"/api/assessments/{aid}/committee-votes", json={"vote": "APPROVE", "comment": "fine"}, headers=auth("committee")))
    assert first["version"] == 1 and first["is_current"] and first["cast_by_id"] == users["committee"]

    no_reason = client.post(f"/api/assessments/{aid}/committee-votes", json={"vote": "DISSENT"}, headers=auth("committee"))
    assert no_reason.status_code == 422 and "recast_reason" in no_reason.json()["detail"]

    second = ok(
        client.post(
            f"/api/assessments/{aid}/committee-votes",
            json={"vote": "DISSENT", "comment": "control gap", "recast_reason": "New evidence on vendor controls."},
            headers=auth("committee"),
        )
    )
    assert second["version"] == 2 and second["recast_reason"] == "New evidence on vendor controls."

    current = ok(client.get(f"/api/assessments/{aid}/committee-votes", headers=auth("committee")))
    assert [(v["vote"], v["version"]) for v in current] == [("DISSENT", 2)]
    history = ok(client.get(f"/api/assessments/{aid}/committee-votes?include_history=true", headers=auth("committee")))
    assert [(v["vote"], v["version"], v["is_current"]) for v in history] == [("APPROVE", 1, False), ("DISSENT", 2, True)]
    # The original vote is untouched: same value, comment and time.
    assert history[0]["comment"] == "fine" and history[0]["voted_at"] == first["voted_at"]
    assert history[0]["superseded_by_id"] == second["id"]

    events = _events(aid, "COMMITTEE_VOTE_CAST", users["committee"])
    assert len(events) == 2 and "replaces v1: APPROVE" in events[1].details

    package = ok(client.get(f"/api/assessments/{aid}/decision-package", headers=auth("committee")))
    assert {(v["version"], v["is_current"]) for v in package["committee_votes"]} == {(1, False), (2, True)}


def test_vote_records_cannot_be_rewritten(client, auth, users, create_assessment, no_challenge_triggers):
    aid = _committee_ready(client, auth, users, create_assessment)
    ok(client.post(f"/api/assessments/{aid}/committee-votes", json={"vote": "APPROVE"}, headers=auth("committee")))
    ok(client.post(f"/api/assessments/{aid}/committee-votes", json={"vote": "ABSTAIN", "recast_reason": "conflict"}, headers=auth("committee")))

    db = SessionLocal()
    try:
        old, new = db.query(CommitteeVote).filter(CommitteeVote.assessment_id == aid).order_by(CommitteeVote.version).all()
        old.vote = "DISSENT"
        with pytest.raises(ProtectedDataError):
            db.flush()
        db.rollback()
        old = db.get(CommitteeVote, old.id)
        old.is_current = True
        with pytest.raises(ProtectedDataError):
            db.flush()
        db.rollback()
        with pytest.raises(ProtectedDataError):
            db.delete(db.get(CommitteeVote, new.id))
            db.flush()
    finally:
        db.rollback()
        db.close()


def test_admin_is_not_a_committee_member_even_with_the_retired_switch(client, auth, users, create_assessment, no_challenge_triggers, monkeypatch):
    aid = _committee_ready(client, auth, users, create_assessment)
    admin_id = _user("admin@example.com", "ADMIN")

    refused = client.post(f"/api/assessments/{aid}/committee-votes", json={"vote": "APPROVE"}, headers=auth("admin"))
    assert refused.status_code == 403 and "committee membership" in refused.json()["detail"]
    decision = client.post(
        f"/api/assessments/{aid}/committee-decision",
        json={"decision": "approve", "rationale": "x"},
        headers=auth("admin"),
    )
    assert decision.status_code == 403
    assert len(_events(aid, "ACCESS_DENIED", admin_id)) >= 2

    # P3 (R-GOV-02): the P2 switch is retired -- only an approved, declared
    # dual-role SoD exception can let an Admin act as a committee member
    # (tests/api/test_p3_sod.py).
    monkeypatch.setenv("COMMITTEE_ADMIN_AUTHORITY", "true")
    still = client.post(f"/api/assessments/{aid}/committee-votes", json={"vote": "ABSTAIN"}, headers=auth("admin"))
    assert still.status_code == 403


def test_conflicted_committee_member_is_refused_and_logged(client, auth, users, create_assessment, no_challenge_triggers):
    aid = _committee_ready(client, auth, users, create_assessment)
    db = SessionLocal()
    try:
        db.get(Assessment, aid).owner_id = users["committee"]
        db.commit()
    finally:
        db.close()

    refused = client.post(f"/api/assessments/{aid}/committee-votes", json={"vote": "APPROVE"}, headers=auth("committee"))
    assert refused.status_code == 403 and "submitted this assessment" in refused.json()["detail"]
    assert _events(aid, "ACCESS_DENIED", users["committee"])


# -- R6.7 / R10.2-R10.4 overrides ----------------------------------------------


def _first_factor(aid: int) -> RiskFactor:
    db = SessionLocal()
    try:
        return (
            db.query(RiskFactor)
            .filter(RiskFactor.assessment_id == aid, RiskFactor.is_current.is_(True), RiskFactor.applicable.is_(True))
            .order_by(RiskFactor.id)
            .first()
        )
    finally:
        db.close()


def test_override_reads_the_system_value_and_changes_nothing(client, auth, analysed_assessment):
    aid = analysed_assessment()["id"]
    factor = _first_factor(aid)

    proposed = ok(
        client.post(
            f"/api/assessments/{aid}/overrides",
            json={
                "section": "RISK_RATIONALE",
                "field_name": "rationale",
                "entity_id": str(factor.id),
                "ai_value": "a value the client made up",
                "human_value": "Analyst's corrected rationale.",
                "reason": "The AI rationale misses the sanctions nexus.",
            },
            headers=auth("analyst"),
        ),
        201,
    )
    assert proposed["ai_value"] == factor.rationale
    assert proposed["ai_value_source"] == "SYSTEM" and proposed["review_status"] == "PROPOSED"
    assert "client-supplied system value was ignored" in _events(aid, "OVERRIDE_APPLIED")[-1].details

    # The record itself is unchanged.
    assert _first_factor(aid).rationale == factor.rationale


@pytest.mark.parametrize(
    "body, message",
    [
        ({"section": "RISK_RATIONALE", "field_name": "rationale"}, "entity_id"),
        ({"section": "RISK_RATIONALE", "field_name": "rationale", "entity_id": "999999"}, "not a current risk factor"),
        ({"section": "FACTOR_RATING", "field_name": "colour", "entity_id": "FACTOR"}, "field that can be overridden"),
        ({"section": "INTAKE_FIELD", "field_name": "no_such_field"}, "not an intake"),
    ],
)
def test_override_must_name_a_real_value(client, auth, analysed_assessment, body, message):
    aid = analysed_assessment()["id"]
    if body.get("entity_id") == "FACTOR":
        body = {**body, "entity_id": str(_first_factor(aid).id)}
    response = client.post(
        f"/api/assessments/{aid}/overrides",
        json={**body, "human_value": "x", "reason": "because"},
        headers=auth("analyst"),
    )
    assert response.status_code == 422 and message in response.json()["detail"]


def test_override_needs_an_independent_reviewer(client, auth, users, analysed_assessment):
    aid = analysed_assessment()["id"]
    db = SessionLocal()
    try:
        db.get(Assessment, aid).manager_id = users["manager"]
        db.commit()
    finally:
        db.close()
    factor = _first_factor(aid)
    override = ok(
        client.post(
            f"/api/assessments/{aid}/overrides",
            json={"section": "RISK_RATIONALE", "field_name": "rationale", "entity_id": str(factor.id), "human_value": "Better.", "reason": "Clearer."},
            headers=auth("analyst"),
        ),
        201,
    )
    review = f"/api/assessments/{aid}/overrides/{override['id']}/review"

    for who, why in (("analyst", "Overrides are reviewed by"), ("admin", "Overrides are reviewed by")):
        refused = client.patch(review, json={"decision": "CONFIRM", "note": "ok"}, headers=auth(who))
        assert refused.status_code == 403 and why in refused.json()["detail"], who
    assert client.patch(review, json={"decision": "CONFIRM", "note": "ok"}, headers=auth("owner")).status_code == 403
    assert len(_events(aid, "ACCESS_DENIED")) >= 3

    confirmed = ok(client.patch(review, json={"decision": "CONFIRM", "note": "Agreed with the analyst."}, headers=auth("manager")))
    assert confirmed["review_status"] == "CONFIRMED" and confirmed["reviewed_by_id"] == users["manager"]
    assert client.patch(review, json={"decision": "REJECT", "note": "changed my mind"}, headers=auth("manager")).status_code == 409
    assert _events(aid, "OVERRIDE_REVIEWED", users["manager"])

    # The ledger row itself is write-once.
    db = SessionLocal()
    try:
        row = db.get(AssessmentOverride, override["id"])
        row.human_value = "rewritten"
        with pytest.raises(ProtectedDataError):
            db.flush()
    finally:
        db.rollback()
        db.close()

    package = ok(client.get(f"/api/assessments/{aid}/decision-package", headers=auth("manager")))
    entry = next(e for e in package["value_comparisons"] if e["section"] == "RISK_RATIONALE")
    assert entry["review_status"] == "CONFIRMED" and entry["counts_in_decision"] is True
    assert entry["calculated_value"] == factor.rationale and entry["human_value"] == "Better."


def test_business_user_cannot_propose_an_override(client, auth, analysed_assessment):
    aid = analysed_assessment()["id"]
    response = client.post(
        f"/api/assessments/{aid}/overrides",
        json={"section": "INTAKE_FIELD", "field_name": "customer_segment", "human_value": "Retail", "reason": "x"},
        headers=auth("owner"),
    )
    assert response.status_code == 403


def test_inherent_override_keeps_the_calculation_and_every_override(client, auth, users, create_assessment):
    aid = create_assessment()["id"]
    _at(aid, "INHERENT_RISK_ASSESSMENT")

    def current_calc():
        db = SessionLocal()
        try:
            return (
                db.query(InherentRiskCalculation)
                .filter(InherentRiskCalculation.assessment_id == aid, InherentRiskCalculation.is_current.is_(True))
                .one()
            )
        finally:
            db.close()

    calculated = current_calc()
    for value, reason in ((70.0, "Sanctions nexus understated."), (65.0, "Revised after vendor review.")):
        ok(
            client.post(
                f"/api/assessments/{aid}/inherent-risk/override",
                json={"override_value": value, "reason": reason},
                headers=auth("analyst"),
            )
        )

    after = current_calc()
    assert (after.calculated_score, after.calculated_band) == (calculated.calculated_score, calculated.calculated_band)
    assert after.override_value == 65.0 and after.override_by_id == users["analyst"]

    db = SessionLocal()
    try:
        ledger = (
            db.query(AssessmentOverride)
            .filter(AssessmentOverride.assessment_id == aid, AssessmentOverride.section == "INHERENT_RISK")
            .order_by(AssessmentOverride.id)
            .all()
        )
    finally:
        db.close()
    assert [row.reason for row in ledger] == ["Sanctions nexus understated.", "Revised after vendor review."]
    assert all(row.review_status == "APPLIED" and row.ai_value_source == "SYSTEM" for row in ledger)
    assert ledger[0].ai_value.startswith(f"{calculated.calculated_score:g}")

    package = ok(client.get(f"/api/assessments/{aid}/decision-package", headers=auth("analyst")))
    inherent = next(e for e in package["value_comparisons"] if e["section"] == "INHERENT_RISK")
    assert inherent["calculated_value"].startswith(f"{calculated.calculated_score:g}")
    assert inherent["human_value"].startswith("65") and inherent["counts_in_decision"] is True


def test_lowering_the_band_by_override_does_not_switch_off_the_mandatory_challenge(client, auth, create_assessment):
    aid = create_assessment()["id"]
    _at(aid, "INHERENT_RISK_ASSESSMENT")
    db = SessionLocal()
    try:
        calc = db.query(InherentRiskCalculation).filter(InherentRiskCalculation.assessment_id == aid).one()
        calc.calculated_band = "CRITICAL"
        db.get(Assessment, aid).inherent_risk_level = "LOW"
        db.commit()
        level = challenge_engine._challenge_risk_level(db, db.get(Assessment, aid))
    finally:
        db.close()
    assert level == "CRITICAL"


# -- R11 mandatory challenge-review sign-off ---------------------------------


def test_signoff_is_mandatory_even_when_no_trigger_fires(client, auth, users, create_assessment, no_challenge_triggers):
    aid = create_assessment()["id"]
    _at(aid, "SUBMITTED_TO_MANAGER", manager_id=users["manager"])

    summary = ok(client.get(f"/api/assessments/{aid}/workflow", headers=auth("manager")))
    assert any("challenge review has not been completed" in issue for issue in summary["mandatory_issues"])
    blocked = client.post(f"/api/assessments/{aid}/manager-decision", json={"decision": "approve", "comment": "ok"}, headers=auth("manager"))
    assert blocked.status_code == 409

    # P3: the sign-off can't come before the independent review.
    early = _sign_off(client, auth, aid)
    assert early.status_code == 409 and "must be completed" in early.json()["detail"]

    reviewed = ok(_review(client, auth, aid, reason="No trigger fired; profile and evidence reviewed."), 201)
    assert reviewed["stage"] == "REVIEW" and reviewed["outcome"] == "NO_TRIGGERS_FIRED"
    assert reviewed["reviewer_id"] == users["senior"]
    assert reviewed["triggers"]["triggered"] is False and reviewed["findings"] == []
    signed = ok(_sign_off(client, auth, aid), 201)
    assert signed["stage"] == "SIGNOFF" and signed["reviewer_id"] == users["manager"]
    assert _events(aid, "CHALLENGE_REVIEW_COMPLETED", users["senior"])
    assert _events(aid, "CHALLENGE_REVIEW_SIGNED_OFF", users["manager"])

    approved = ok(client.post(f"/api/assessments/{aid}/manager-decision", json={"decision": "approve", "comment": "ok"}, headers=auth("manager")))
    assert approved["workflow_status"] == "READY_FOR_COMMITTEE"


def test_signoff_is_refused_while_high_findings_are_open(client, auth, users, create_assessment, monkeypatch):
    aid = create_assessment()["id"]
    _at(aid, "SUBMITTED_TO_MANAGER", manager_id=users["manager"])
    ok(_review(client, auth, aid), 201)
    monkeypatch.setattr(challenge_engine, "has_blocking_findings", lambda db, assessment_id: True)
    refused = _sign_off(client, auth, aid)
    assert refused.status_code == 409 and "can't be accepted" in refused.json()["detail"]


@pytest.mark.parametrize("who", ["owner", "admin", "committee", "other_manager"])
def test_only_an_independent_reviewer_can_sign_off(client, auth, users, create_assessment, no_challenge_triggers, who):
    aid = create_assessment()["id"]
    _at(aid, "SUBMITTED_TO_MANAGER", manager_id=users["manager"])
    if who == "other_manager":
        _user("p2othermanager@test.io", "MANAGER")
        headers = _login(client, "p2othermanager@test.io")
    else:
        headers = auth(who)
    response = client.post(f"/api/assessments/{aid}/challenge-review/signoff", json={"reason": "done"}, headers=headers)
    assert response.status_code == 403
    assert _events(aid, "ACCESS_DENIED")


def test_a_finding_after_signoff_requires_a_new_signoff(client, auth, users, create_assessment, no_challenge_triggers):
    aid = create_assessment()["id"]
    _at(aid, "SUBMITTED_TO_MANAGER", manager_id=users["manager"])
    _complete_challenge(client, auth, aid)

    db = SessionLocal()
    try:
        db.add(
            ChallengeFinding(
                assessment_id=aid,
                category="CONTRADICTION",
                description="Volumes differ between documents.",
                related_section="EVIDENCE",
                severity="MEDIUM",
                detected_at=datetime.now(timezone.utc) + timedelta(seconds=5),
            )
        )
        db.commit()
    finally:
        db.close()

    state = ok(client.get(f"/api/assessments/{aid}/challenge-review/signoff", headers=auth("manager")))
    assert state["valid"] is False and "after the challenge review" in state["problem"]
    blocked = client.post(f"/api/assessments/{aid}/manager-decision", json={"decision": "approve", "comment": "ok"}, headers=auth("manager"))
    assert blocked.status_code == 409


def test_manager_return_supersedes_the_signoff(client, auth, users, create_assessment, no_challenge_triggers):
    aid = create_assessment()["id"]
    _at(aid, "SUBMITTED_TO_MANAGER", manager_id=users["manager"])
    _complete_challenge(client, auth, aid)
    ok(client.post(f"/api/assessments/{aid}/manager-decision", json={"decision": "return", "comment": "Rework the controls."}, headers=auth("manager")))

    state = ok(client.get(f"/api/assessments/{aid}/challenge-review/signoff", headers=auth("manager")))
    assert state["current"] is None and state["review"] is None and state["valid"] is False
    assert {row["stage"] for row in state["history"]} == {"REVIEW", "SIGNOFF"}
    assert all(not row["is_current"] and "returned" in row["superseded_reason"] for row in state["history"])


def test_legacy_assessment_already_before_the_committee_can_be_signed_off(client, auth, users, create_assessment, no_challenge_triggers):
    aid = create_assessment()["id"]
    _at(aid, "READY_FOR_COMMITTEE", manager_id=users["manager"], manager_decision="APPROVE", manager_decided_by_id=users["manager"])

    blocked = client.post(f"/api/assessments/{aid}/committee-votes", json={"vote": "APPROVE"}, headers=auth("committee"))
    assert blocked.status_code == 409
    _complete_challenge(client, auth, aid)
    ok(client.post(f"/api/assessments/{aid}/committee-votes", json={"vote": "APPROVE"}, headers=auth("committee")))


# -- R7.2 / R10.2 control versioning ------------------------------------------


def _two_factors(aid: int) -> tuple[int, int]:
    db = SessionLocal()
    try:
        ids = [
            f.id
            for f in db.query(RiskFactor)
            .filter(RiskFactor.assessment_id == aid, RiskFactor.is_current.is_(True), RiskFactor.applicable.is_(True), RiskFactor.excluded.is_(False))
            .order_by(RiskFactor.id)
            .limit(2)
        ]
    finally:
        db.close()
    assert len(ids) == 2
    return ids[0], ids[1]


def _gaps(client, auth, aid):
    return ok(client.get(f"/api/assessments/{aid}/control-summary", headers=auth("analyst")))["gaps"]


def test_control_edit_remap_and_unmap_keep_history(client, auth, users, analysed_assessment):
    aid = analysed_assessment()["id"]
    first, second = _two_factors(aid)
    control = ok(
        client.post(
            f"/api/assessments/{aid}/controls",
            json={"risk_factor_id": first, "control_type": "SANCTIONS_SCREENING", "owner": "Ops"},
            headers=auth("analyst"),
        ),
        201,
    )
    base = f"/api/assessments/{aid}/controls/{control['id']}"

    assert client.patch(base, json={"owner": "Compliance"}, headers=auth("analyst")).status_code == 422  # no reason
    edited = ok(client.patch(base, json={"owner": "Compliance", "reason": "Ownership moved."}, headers=auth("analyst")))
    assert edited["owner"] == "Compliance" and edited["version"] == 2

    remapped = ok(client.patch(base, json={"risk_factor_id": second, "reason": "Addresses the other risk."}, headers=auth("analyst")))
    assert remapped["risk_factor_id"] == second and remapped["version"] == 3
    assert any(g["gap_type"] == "NO_CONTROL" and g["risk_factor_id"] == first for g in _gaps(client, auth, aid))

    unmapped = ok(client.post(f"{base}/unmap", json={"reason": "Not relevant to this product."}, headers=auth("analyst")))
    assert unmapped["is_current"] is False and unmapped["version"] == 4
    assert all(c["id"] != control["id"] for c in ok(client.get(f"/api/assessments/{aid}/controls", headers=auth("analyst"))))
    assert any(c["id"] == control["id"] for c in ok(client.get(f"/api/assessments/{aid}/controls?include_unmapped=true", headers=auth("analyst"))))
    assert client.patch(base, json={"owner": "x", "reason": "y"}, headers=auth("analyst")).status_code == 404

    revisions = ok(client.get(f"{base}/revisions", headers=auth("analyst")))
    assert [(r["change_type"], r["version"]) for r in revisions] == [("EDIT", 2), ("REMAP", 3), ("UNMAP", 4)]
    assert revisions[0]["previous_config"]["owner"] == "Ops" and revisions[0]["new_config"]["owner"] == "Compliance"
    assert revisions[1]["changed_fields"] == ["risk_factor_id"]
    assert revisions[2]["new_config"] is None and revisions[2]["reason"] == "Not relevant to this product."
    assert all(r["changed_by_id"] == users["analyst"] for r in revisions)

    for action in ("CONTROL_UPDATED", "CONTROL_REMAPPED", "CONTROL_UNMAPPED"):
        assert _events(aid, action, users["analyst"]), action

    db = SessionLocal()
    try:
        row = db.query(ControlRevision).filter(ControlRevision.control_id == control["id"]).first()
        row.reason = "rewritten"
        with pytest.raises(ProtectedDataError):
            db.flush()
    finally:
        db.rollback()
        db.close()


def test_design_inadequacy_raises_a_gap_and_earns_no_reduction(client, auth, analysed_assessment):
    aid = analysed_assessment()["id"]
    first, _ = _two_factors(aid)
    control = ok(
        client.post(f"/api/assessments/{aid}/controls", json={"risk_factor_id": first, "control_type": "TRANSACTION_MONITORING"}, headers=auth("analyst")),
        201,
    )
    # The API's value (INADEQUATE), with or without operating evidence.
    for has_evidence in (False, True):
        ok(
            client.post(
                f"/api/assessments/{aid}/controls/{control['id']}/assessments",
                json={
                    "design_adequacy": "INADEQUATE",
                    # Without evidence the API requires UNVERIFIED (R7.6).
                    "operating_effectiveness": "EFFECTIVE" if has_evidence else "UNVERIFIED",
                    "has_evidence": has_evidence,
                },
                headers=auth("analyst"),
            ),
            201,
        )
        design_gaps = [
            g for g in _gaps(client, auth, aid)
            if g["control_id"] == control["id"] and g["gap_type"] == "INEFFECTIVE" and "design" in g["description"]
        ]
        assert design_gaps, has_evidence

    for value in ("INADEQUATE", "DESIGN_INADEQUATE"):
        assert points_for_risk([{"design_adequacy": value, "operating_effectiveness": "EFFECTIVE", "has_evidence": True}]) == 0
    assert points_for_risk([{"design_adequacy": "ADEQUATE", "operating_effectiveness": "EFFECTIVE", "has_evidence": True}]) > 0


def test_a_control_change_refreezes_a_frozen_residual(client, auth, users, create_assessment):
    aid = create_assessment()["id"]
    _at(aid, "HUMAN_REVIEW")  # residual frozen by the fixture

    db = SessionLocal()
    try:
        factor_id = db.query(RiskFactor.id).filter(RiskFactor.assessment_id == aid, RiskFactor.is_current.is_(True)).scalar()
        before = db.query(ResidualRiskCalculation).filter(ResidualRiskCalculation.assessment_id == aid).count()
    finally:
        db.close()

    control = ok(
        client.post(f"/api/assessments/{aid}/controls", json={"risk_factor_id": factor_id, "control_type": "KYC_CUSTOMER_DUE_DILIGENCE"}, headers=auth("analyst")),
        201,
    )
    ok(
        client.post(
            f"/api/assessments/{aid}/controls/{control['id']}/assessments",
            json={"design_adequacy": "ADEQUATE", "operating_effectiveness": "EFFECTIVE", "has_evidence": True},
            headers=auth("analyst"),
        ),
        201,
    )

    db = SessionLocal()
    try:
        rows = (
            db.query(ResidualRiskCalculation)
            .filter(ResidualRiskCalculation.assessment_id == aid)
            .order_by(ResidualRiskCalculation.version)
            .all()
        )
    finally:
        db.close()
    assert len(rows) == before + 2  # one per control change; earlier versions kept
    assert [r.is_current for r in rows].count(True) == 1 and rows[-1].frozen
    assert any("Residual risk re-frozen" in (e.details or "") for e in _events(aid, "STATUS_CHANGE"))


@pytest.mark.parametrize("who", ["owner", "committee"])
def test_only_the_pipeline_can_change_controls(client, auth, analysed_assessment, who):
    aid = analysed_assessment()["id"]
    first, _ = _two_factors(aid)
    control = ok(
        client.post(f"/api/assessments/{aid}/controls", json={"risk_factor_id": first, "control_type": "SANCTIONS_SCREENING"}, headers=auth("analyst")),
        201,
    )
    base = f"/api/assessments/{aid}/controls/{control['id']}"
    assert client.patch(base, json={"owner": "x", "reason": "y"}, headers=auth(who)).status_code == 403
    assert client.post(f"{base}/unmap", json={"reason": "y"}, headers=auth(who)).status_code == 403


# -- R15.4 scope on the new operations ----------------------------------------


def test_out_of_scope_users_reach_none_of_the_new_operations(client, auth, users, analysed_assessment):
    aid = analysed_assessment()["id"]
    analyst_id = _user("p2scoped@test.io", "FCRM_ANALYST", scope_legal_entities=["Some Other Entity"])
    headers = _login(client, "p2scoped@test.io")
    factor = _first_factor(aid)

    attempts = [
        ("post", f"/api/assessments/{aid}/overrides", {"section": "RISK_RATIONALE", "field_name": "rationale", "entity_id": str(factor.id), "human_value": "x", "reason": "y"}),
        ("post", f"/api/assessments/{aid}/challenge-review/signoff", {"reason": "done"}),
        ("post", f"/api/assessments/{aid}/challenge-review/review", {"reason": "done"}),
        ("get", f"/api/assessments/{aid}/readiness", None),
        ("post", f"/api/assessments/{aid}/committee-votes", {"vote": "APPROVE"}),
        ("get", f"/api/assessments/{aid}/committee-votes?include_history=true", None),
        ("get", f"/api/assessments/{aid}/challenge-review/signoff", None),
    ]
    for method, path, body in attempts:
        response = getattr(client, method)(path, json=body, headers=headers) if body is not None else client.get(path, headers=headers)
        assert response.status_code == 403, (path, response.text)
    assert len(_events(aid, "ACCESS_DENIED", analyst_id)) >= len(attempts)


def test_read_only_roles_cannot_use_the_new_operations(client, auth, analysed_assessment):
    aid = analysed_assessment()["id"]
    _user("p2auditor@test.io", "AUDITOR")
    headers = _login(client, "p2auditor@test.io")
    assert client.post(f"/api/assessments/{aid}/challenge-review/signoff", json={"reason": "x"}, headers=headers).status_code == 403
    assert client.get(f"/api/assessments/{aid}/challenge-review/signoff", headers=headers).status_code == 200

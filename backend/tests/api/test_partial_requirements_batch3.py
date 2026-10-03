"""
Partly-done requirements completed in batch 3:

R16.1  every AI call records the prompt (template) version it used
R16.3  the audit export carries configuration, people, explanation and
       an integrity hash
R13.3  overdue action items escalate under configurable rules and reach
       the assessment owner's and reviewer's work queues
R2.3   the original extracted value stays visible next to a correction
R3.2   contradictions between two uploaded documents are detected and
R11.4  raised as a challenge finding quoting both passages
"""

import json
from datetime import date, timedelta

import app.ai.metering as metering
from app.database import SessionLocal
from app.models.action_item import ActionItem
from app.models.ai_metrics import AIUsageLog
from app.models.assessment import Assessment
from app.models.assessment_intelligence import AssessmentIntelligence
from app.services import audit_export
from app.services.escalation import escalation_grace_days
from tests.conftest import ok


# -- R16.1 --------------------------------------------------------------------


def test_ai_calls_record_the_prompt_version(analysed_assessment):
    aid = analysed_assessment()["id"]
    db = SessionLocal()
    try:
        logs = db.query(AIUsageLog).filter(AIUsageLog.assessment_id == aid).all()
    finally:
        db.close()

    identification = [log for log in logs if log.purpose == "RISK_FACTOR_IDENTIFICATION"]
    assert identification
    assert all(log.prompt_version == metering.prompt_version("RISK_FACTOR_IDENTIFICATION") for log in identification)
    assert identification[0].prompt_version.startswith("_build_prompt@")
    assert metering.prompt_version("EMBEDDING") is None


# -- R16.3 --------------------------------------------------------------------


def test_audit_export_is_complete_and_tamper_evident(client, auth, users, analysed_assessment):
    aid = analysed_assessment()["id"]
    response = client.get(f"/api/assessments/{aid}/audit-export", headers=auth("manager"))
    if response.status_code == 403:  # managers only see their own queue
        db = SessionLocal()
        try:
            db.get(Assessment, aid).manager_id = users["manager"]
            db.commit()
        finally:
            db.close()
        response = client.get(f"/api/assessments/{aid}/audit-export", headers=auth("manager"))
    package = ok(response)

    assert "risk_methodologies" in package["configuration"]
    assert "challenge_trigger_config_at_export" in package["configuration"]
    people = {person["id"]: person for person in package["people"]}
    assert users["owner"] in people
    assert all("hashed_password" not in person for person in package["people"])
    assert package["explanation"] is not None

    assert package["integrity"]["algorithm"] == "sha256"
    assert package["integrity"]["digest"] == audit_export.package_digest(package)
    tampered = json.loads(json.dumps(package))
    tampered["assessment"]["title"] = "Something else"
    assert audit_export.package_digest(tampered) != package["integrity"]["digest"]


# -- R13.3 --------------------------------------------------------------------


def test_escalation_rules_are_configurable(monkeypatch):
    assert escalation_grace_days()["LOW"] == 7
    monkeypatch.setenv("ACTION_ESCALATION_GRACE_DAYS", '{"LOW": 14, "HIGH": 0}')
    rules = escalation_grace_days()
    assert rules["LOW"] == 14 and rules["HIGH"] == 0 and rules["CRITICAL"] == 0
    monkeypatch.setenv("ACTION_ESCALATION_GRACE_DAYS", "not json")
    assert escalation_grace_days()["LOW"] == 7


def test_overdue_action_reaches_owner_and_reviewer_work_queues(client, auth, users, create_assessment):
    aid = create_assessment()["id"]
    db = SessionLocal()
    try:
        db.get(Assessment, aid).manager_id = users["manager"]
        db.add(
            ActionItem(
                assessment_id=aid,
                source_type="VENDOR_REMEDIATION",
                title="Obtain processor SOC 2 report",
                priority="CRITICAL",
                due_date=date.today() - timedelta(days=2),
                status="OPEN",
            )
        )
        db.commit()
    finally:
        db.close()

    for who in ("owner", "manager"):
        queue = ok(client.get("/api/workflow/work-queue", headers=auth(who)))
        mine = [item for item in queue["action_escalations"] if item["assessment_id"] == aid]
        assert len(mine) == 1, who
        assert "Owner" in mine[0]["escalation_note"] and "Manager" in mine[0]["escalation_note"]

    other = ok(client.get("/api/workflow/work-queue", headers=auth("other_owner")))
    assert not [item for item in other["action_escalations"] if item["assessment_id"] == aid]


# -- R2.3 ---------------------------------------------------------------------


def test_original_extracted_value_is_shown_after_a_correction(client, auth, analysed_assessment):
    aid = analysed_assessment()["id"]
    db = SessionLocal()
    try:
        intelligence = db.query(AssessmentIntelligence).filter(AssessmentIntelligence.assessment_id == aid).one()
        intelligence.raw_extraction = json.dumps({"customer_type": "SME merchants", "title": "ignored"})
        intelligence.customer_type = "SME merchants"
        db.commit()
    finally:
        db.close()

    before = ok(client.get(f"/api/assessments/{aid}/intelligence", headers=auth("analyst")))
    assert before["original_values"] == {}

    corrected = ok(
        client.patch(
            f"/api/assessments/{aid}/intelligence",
            json={"customer_type": "Large corporates", "change_reason": "Segment confirmed with the owner."},
            headers=auth("analyst"),
        )
    )
    assert corrected["customer_type"] == "Large corporates"
    assert corrected["original_values"] == {"customer_type": "SME merchants"}


# -- R3.2 / R11.4 -----------------------------------------------------------------


def _upload(client, auth, aid, name, text):
    return ok(
        client.post(
            f"/api/assessments/{aid}/documents",
            files={"file": (name, text.encode(), "text/plain")},
            headers=auth("owner"),
        )
    )


def test_contradicting_documents_are_flagged(client, auth, create_assessment):
    aid = create_assessment()["id"]
    _upload(client, auth, aid, "business-case.txt", "We expect about 50,000 transactions per month at launch.")
    _upload(client, auth, aid, "vendor-spec.txt", "The processor is sized for 1.8 million transactions a year.")
    _upload(client, auth, aid, "notes.txt", "Settlement is in EUR. No figures here.")

    # R3.2 applies before a structured profile exists, too.
    conflicts = ok(client.get(f"/api/assessments/{aid}/consistency-check", headers=auth("owner")))
    document_conflicts = [c for c in conflicts if c["source"] == "DOCUMENTS"]
    assert len(document_conflicts) == 1
    conflict = document_conflicts[0]
    assert conflict["field"] == "Transaction volume (per month)"
    assert "business-case.txt" in conflict["message"] and "vendor-spec.txt" in conflict["message"]
    assert {d["filename"] for d in conflict["documents"]} == {"business-case.txt", "vendor-spec.txt"}

    review = ok(client.get(f"/api/assessments/{aid}/challenge-review", headers=auth("analyst")))
    finding = next(
        f for f in review["findings"] if f["category"] == "CONTRADICTION" and "vendor-spec.txt" in f["description"]
    )
    assert finding["severity"] == "HIGH"
    assert "1.8 million transactions a year" in finding["supporting_evidence"]


def test_agreeing_documents_are_not_a_conflict(client, auth, create_assessment):
    aid = create_assessment()["id"]
    _upload(client, auth, aid, "a.txt", "Roughly 50,000 transactions per month.")
    _upload(client, auth, aid, "b.txt", "Volumes of 52k transactions/month are planned.")

    conflicts = ok(client.get(f"/api/assessments/{aid}/consistency-check", headers=auth("owner")))
    assert not [c for c in conflicts if c["source"] == "DOCUMENTS"]

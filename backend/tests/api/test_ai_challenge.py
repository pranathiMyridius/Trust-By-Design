"""
AI challenge analysis: advisory gap findings the model raises, which a
reviewer confirms or dismisses. The model call is faked at the metering
seam; the findings must never touch the rule-based challenge review.
"""

import json

import pytest

from app.ai import provider
from app.database import SessionLocal
from app.models.assessment_document import AssessmentDocument
from tests.conftest import ok
from tests.support.fake_llm import chat

DOC = (
    "Merchant onboarding is fully remote. Merchants are onboarded without a video call or any "
    "in-person check, and settlement to Poland starts on the first day of trading."
)
QUOTE = "Merchants are onboarded without a video call or any in-person check"


@pytest.fixture(autouse=True)
def _ai_key(monkeypatch):
    monkeypatch.setattr(provider, "API_KEY", "test-key-not-real")


def _setup(client, auth, analysed_assessment):
    aid = analysed_assessment()["id"]
    factor = ok(client.get(f"/api/assessments/{aid}/risk-factors", headers=auth("analyst")))[0]
    control = ok(
        client.post(
            f"/api/assessments/{aid}/controls",
            json={"risk_factor_id": factor["id"], "control_type": "SANCTIONS_SCREENING"},
            headers=auth("analyst"),
        ),
        201,
    )
    db = SessionLocal()
    try:
        document = AssessmentDocument(assessment_id=aid, filename="onboarding.txt", file_type="txt", extracted_text=DOC)
        db.add(document)
        db.commit()
        document_id = document.id
    finally:
        db.close()
    return aid, factor["id"], control["id"], document_id


def _reply(findings):
    return lambda url, payload: chat(json.dumps({"findings": findings}))


def _rule_findings(client, auth, aid) -> int:
    review = ok(client.get(f"/api/assessments/{aid}/challenge-review", headers=auth("analyst")))
    return len(review["findings"])


def test_run_stores_verified_findings_and_drops_unverifiable_contradictions(client, auth, analysed_assessment, fake_llm):
    aid, factor_id, control_id, document_id = _setup(client, auth, analysed_assessment)
    fake_llm.transport = _reply(
        [
            {
                "category": "CONTRADICTION",
                "severity": "HIGH",
                "title": "Remote onboarding contradicts the control description",
                "detail": "The document says no video check is done.",
                "risk_factor_id": factor_id,
                "control_id": control_id,
                "document_id": document_id,
                "quote": QUOTE,
            },
            {
                "category": "CONTRADICTION",
                "severity": "HIGH",
                "title": "Invented contradiction",
                "detail": "Not backed by the text.",
                "document_id": document_id,
                "quote": "Customers are always met in person",
            },
            {
                "category": "MISSING_CONTROL",
                "severity": "MEDIUM",
                "title": "No remote identity verification",
                "detail": "Remote onboarding has no identity verification control.",
                "risk_factor_id": 999999,
                "control_id": 999999,
                "document_id": document_id,
                "quote": "made up passage here",
            },
        ]
    )
    before = _rule_findings(client, auth, aid)

    run = ok(client.post(f"/api/assessments/{aid}/ai-challenge/run", headers=auth("analyst")))
    assert run["status"] == "OK" and run["findings_raised"] == 2
    by_title = {f["title"]: f for f in run["findings"]}
    contradiction = by_title["Remote onboarding contradicts the control description"]
    assert contradiction["quote"] == QUOTE and contradiction["document_id"] == document_id
    assert contradiction["control_id"] == control_id and contradiction["status"] == "OPEN"
    missing = by_title["No remote identity verification"]
    # Unknown ids and an unverifiable quote are cleared, not stored.
    assert missing["risk_factor_id"] is None and missing["control_id"] is None
    assert missing["quote"] is None and missing["document_id"] is None
    assert "Invented contradiction" not in by_title

    # Advisory only: the rule-based challenge review is untouched.
    assert _rule_findings(client, auth, aid) == before


def test_confirm_dismiss_and_rerun_keep_decisions(client, auth, analysed_assessment, fake_llm):
    aid, factor_id, _control_id, _document_id = _setup(client, auth, analysed_assessment)
    one = {
        "category": "COVERAGE",
        "severity": "MEDIUM",
        "title": "Single control relied on",
        "detail": "Only one control.",
        "risk_factor_id": factor_id,
    }
    two = {"category": "OTHER", "severity": "LOW", "title": "Minor wording gap", "detail": "Unclear scope."}
    fake_llm.transport = _reply([one, two])
    run = ok(client.post(f"/api/assessments/{aid}/ai-challenge/run", headers=auth("analyst")))
    first, second = run["findings"]

    no_reason = client.post(
        f"/api/assessments/{aid}/ai-challenge/{second['id']}/decision",
        json={"decision": "DISMISS"},
        headers=auth("analyst"),
    )
    assert no_reason.status_code == 400

    confirmed = ok(
        client.post(
            f"/api/assessments/{aid}/ai-challenge/{first['id']}/decision",
            json={"decision": "CONFIRM"},
            headers=auth("analyst"),
        )
    )
    assert confirmed["status"] == "CONFIRMED" and confirmed["decided_by"]
    dismissed = ok(
        client.post(
            f"/api/assessments/{aid}/ai-challenge/{second['id']}/decision",
            json={"decision": "DISMISS", "note": "Out of scope."},
            headers=auth("analyst"),
        )
    )
    assert dismissed["status"] == "DISMISSED" and dismissed["decision_note"] == "Out of scope."

    again = client.post(
        f"/api/assessments/{aid}/ai-challenge/{first['id']}/decision",
        json={"decision": "DISMISS", "note": "x"},
        headers=auth("analyst"),
    )
    assert again.status_code == 409

    # Re-run: decided findings are not duplicated; the new one is added.
    three = {"category": "EVIDENCE_GAP", "severity": "HIGH", "title": "Rating not evidenced", "detail": "No evidence."}
    fake_llm.transport = _reply([one, two, three])
    rerun = ok(client.post(f"/api/assessments/{aid}/ai-challenge/run", headers=auth("analyst")))
    assert rerun["findings_raised"] == 1
    assert sorted((f["title"], f["status"]) for f in rerun["findings"]) == [
        ("Minor wording gap", "DISMISSED"),
        ("Rating not evidenced", "OPEN"),
        ("Single control relied on", "CONFIRMED"),
    ]


def test_unavailable_and_failed_runs_leave_findings_alone(client, auth, analysed_assessment, fake_llm, monkeypatch):
    aid, *_ = _setup(client, auth, analysed_assessment)
    fake_llm.transport = _reply([{"category": "OTHER", "severity": "LOW", "title": "Something", "detail": "Detail."}])
    ok(client.post(f"/api/assessments/{aid}/ai-challenge/run", headers=auth("analyst")))

    fake_llm.transport = fake_llm.timeout
    failed = ok(client.post(f"/api/assessments/{aid}/ai-challenge/run", headers=auth("analyst")))
    assert failed["status"] == "FAILED"
    assert [f["title"] for f in failed["findings"]] == ["Something"]

    monkeypatch.setattr(provider, "API_KEY", "")
    unavailable = ok(client.post(f"/api/assessments/{aid}/ai-challenge/run", headers=auth("analyst")))
    assert unavailable["status"] == "AI_UNAVAILABLE"
    assert [f["title"] for f in unavailable["findings"]] == ["Something"]


def test_only_pipeline_roles_can_run(client, auth, analysed_assessment, fake_llm):
    aid, *_ = _setup(client, auth, analysed_assessment)
    assert client.get(f"/api/assessments/{aid}/ai-challenge", headers=auth("analyst")).status_code == 200
    assert client.post(f"/api/assessments/{aid}/ai-challenge/run", headers=auth("owner")).status_code == 403

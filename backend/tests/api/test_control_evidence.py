"""
AI evidence check: suggestions that uploaded documents support a control.

The model call is faked at the metering seam. The AI must only suggest --
nothing about a control changes until an analyst accepts a suggestion.
"""

import json

import pytest

from app.ai import provider
from app.database import SessionLocal
from app.models.assessment_document import AssessmentDocument
from app.models.control import ControlAssessment
from tests.conftest import ok
from tests.support.fake_llm import chat

POLICY = (
    "Sanctions Screening Procedure v3. All customers and payments are screened against the "
    "OFAC and EU lists in real time before onboarding or release. Alerts are reviewed by the "
    "Financial Crime Operations team within one business day."
)
QUOTE = "All customers and payments are screened against the OFAC and EU lists in real time"


@pytest.fixture(autouse=True)
def _ai_key(monkeypatch):
    monkeypatch.setattr(provider, "API_KEY", "test-key-not-real")


def _document(aid: int, text: str = POLICY, name: str = "sanctions-procedure.txt") -> int:
    db = SessionLocal()
    try:
        document = AssessmentDocument(assessment_id=aid, filename=name, file_type="txt", extracted_text=text)
        db.add(document)
        db.commit()
        return document.id
    finally:
        db.close()


def _control(client, auth, aid: int) -> int:
    factor = ok(client.get(f"/api/assessments/{aid}/risk-factors", headers=auth("analyst")))[0]
    created = ok(
        client.post(
            f"/api/assessments/{aid}/controls",
            json={"risk_factor_id": factor["id"], "control_type": "SANCTIONS_SCREENING"},
            headers=auth("analyst"),
        ),
        201,
    )
    return created["id"]


def _reply(document_id: int, quote: str = QUOTE, level: str = "SUPPORTED"):
    def transport(url, payload):
        return chat(
            json.dumps(
                {
                    "results": [
                        {
                            "document_id": document_id,
                            "support_level": level,
                            "confidence": "HIGH",
                            "quote": quote,
                            "rationale": "The procedure defines real-time screening.",
                            "shortfalls": ["No test results"],
                        }
                    ],
                    "suggested_effectiveness": "PARTIALLY_EFFECTIVE",
                }
            )
        )

    return transport


def _current_assessment(control_id: int):
    db = SessionLocal()
    try:
        return (
            db.query(ControlAssessment)
            .filter(ControlAssessment.control_id == control_id, ControlAssessment.is_current.is_(True))
            .first()
        )
    finally:
        db.close()


def test_check_suggests_but_changes_nothing_until_accepted(client, auth, analysed_assessment, fake_llm):
    aid = analysed_assessment()["id"]
    control_id = _control(client, auth, aid)
    document_id = _document(aid)
    fake_llm.transport = _reply(document_id)

    summary = ok(client.post(f"/api/assessments/{aid}/evidence-check", headers=auth("analyst")))
    assert summary["status"] == "OK"
    link = next(item for item in summary["links"] if item["control_id"] == control_id)
    assert link["status"] == "SUGGESTED"
    assert link["document_name"] == "sanctions-procedure.txt"
    assert link["support_level"] == "SUPPORTED"
    assert link["quote"] == QUOTE
    assert link["shortfalls"] == ["No test results"]
    assert link["document_is_current"] is True

    # A suggestion alone records no evidence.
    current = _current_assessment(control_id)
    assert current is None or not current.has_evidence

    accepted = ok(
        client.post(
            f"/api/assessments/{aid}/evidence-links/{link['id']}/decision",
            json={"decision": "ACCEPT"},
            headers=auth("analyst"),
        )
    )
    assert accepted["status"] == "ACCEPTED" and accepted["decided_by"]
    assert _current_assessment(control_id).has_evidence is True

    again = client.post(
        f"/api/assessments/{aid}/evidence-links/{link['id']}/decision",
        json={"decision": "REJECT"},
        headers=auth("analyst"),
    )
    assert again.status_code == 409


def test_invented_quote_is_discarded(client, auth, analysed_assessment, fake_llm):
    aid = analysed_assessment()["id"]
    control_id = _control(client, auth, aid)
    document_id = _document(aid)
    fake_llm.transport = _reply(document_id, quote="The bank screens every customer against a private watchlist daily")

    summary = ok(client.post(f"/api/assessments/{aid}/evidence-check", headers=auth("analyst")))
    links = [item for item in summary["links"] if item["control_id"] == control_id]
    assert [item["support_level"] for item in links] == ["NONE"]
    assert links[0]["document_id"] is None

    # A "nothing found" result has no document to accept.
    refused = client.post(
        f"/api/assessments/{aid}/evidence-links/{links[0]['id']}/decision",
        json={"decision": "ACCEPT"},
        headers=auth("analyst"),
    )
    assert refused.status_code == 400


def test_reject_leaves_the_control_unchanged_and_rerun_keeps_the_decision(client, auth, analysed_assessment, fake_llm):
    aid = analysed_assessment()["id"]
    control_id = _control(client, auth, aid)
    document_id = _document(aid)
    fake_llm.transport = _reply(document_id)

    first = ok(client.post(f"/api/assessments/{aid}/evidence-check", headers=auth("analyst")))
    link = next(item for item in first["links"] if item["control_id"] == control_id)
    ok(
        client.post(
            f"/api/assessments/{aid}/evidence-links/{link['id']}/decision",
            json={"decision": "REJECT", "note": "Not the current procedure."},
            headers=auth("analyst"),
        )
    )
    current = _current_assessment(control_id)
    assert current is None or not current.has_evidence

    second = ok(client.post(f"/api/assessments/{aid}/evidence-check", headers=auth("analyst")))
    mine = [item for item in second["links"] if item["control_id"] == control_id]
    # The rejected document is not suggested again.
    assert [item["status"] for item in mine] == ["REJECTED"]


def test_a_replaced_document_is_flagged(client, auth, analysed_assessment, fake_llm):
    aid = analysed_assessment()["id"]
    control_id = _control(client, auth, aid)
    document_id = _document(aid)
    fake_llm.transport = _reply(document_id)
    ok(client.post(f"/api/assessments/{aid}/evidence-check", headers=auth("analyst")))

    db = SessionLocal()
    try:
        db.get(AssessmentDocument, document_id).is_current = False
        db.commit()
    finally:
        db.close()

    links = ok(client.get(f"/api/assessments/{aid}/evidence-links", headers=auth("analyst")))
    link = next(item for item in links if item["control_id"] == control_id)
    assert link["document_is_current"] is False


def test_no_documents_or_no_key_means_no_check(client, auth, analysed_assessment, monkeypatch):
    aid = analysed_assessment()["id"]
    _control(client, auth, aid)

    none = ok(client.post(f"/api/assessments/{aid}/evidence-check", headers=auth("analyst")))
    assert none["status"] == "NO_DOCUMENTS" and none["links"] == []

    _document(aid)
    monkeypatch.setattr(provider, "API_KEY", "")
    unavailable = ok(client.post(f"/api/assessments/{aid}/evidence-check", headers=auth("analyst")))
    assert unavailable["status"] == "AI_UNAVAILABLE"


def test_provider_failure_does_not_break_the_check(client, auth, analysed_assessment, fake_llm):
    aid = analysed_assessment()["id"]
    _control(client, auth, aid)
    _document(aid)
    fake_llm.transport = fake_llm.timeout

    summary = ok(client.post(f"/api/assessments/{aid}/evidence-check", headers=auth("analyst")))
    assert summary["controls_checked"] == 0 and summary["controls_failed"] >= 1
    assert summary["links"] == []

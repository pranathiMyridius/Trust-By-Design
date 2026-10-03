"""
P4 evidence traceability, through the API:

  * field provenance (R2.4) on documents uploaded at creation
  * expired documents need an acknowledgement before use (R2.6)
  * intake snapshots with old/new values and reasons (R3.4)
  * the five R5.4 evidence categories on explainability statements
  * fixed Stage 4 rules and analyst indicator edits
"""

import json
from datetime import date, timedelta

import pytest

import app.document_analysis.ai_extractor as ai_extractor
from app.database import SessionLocal
from app.models.assessment import Assessment
from app.models.audit_event import AuditEvent
from app.models.evidence_traceability import IntakeSnapshot
from app.services.data_protection import ProtectedDataError
from tests.conftest import FULL_REQUEST, ok
from tests.support.fake_llm import chat, default_reply, factors_for, prompt_of

BRIEF = (
    "Project Name: Merchant Acquiring DE\n"
    "Target Destination Jurisdictions: Germany, Poland\n"
    "Primary Vendor: Acme Processing Ltd (card processor)\n"
    "Projected Volume: 50,000 transactions per month\n"
)
ANNEX = "Annex. Data Sharing: card numbers and merchant identifiers sent to the processor.\n"


def _events(aid, action):
    db = SessionLocal()
    try:
        return db.query(AuditEvent).filter(AuditEvent.assessment_id == aid, AuditEvent.action == action).all()
    finally:
        db.close()


def _create_with_documents(client, auth, files, who="owner", **form):
    data = {**FULL_REQUEST, "is_draft": "false", **form}
    return ok(
        client.post(
            "/api/assessments/create-with-document",
            data=data,
            files=[("files", (name, text.encode(), "text/plain")) for name, text in files],
            headers=auth(who),
        )
    )


def _extraction_reply(url, payload):
    """The AI extractor's answer, with one quote that is in the document,
    one that is invented and a confidence the server must ignore."""

    prompt = prompt_of(payload)
    if "risk assessment\nintake process" not in prompt and "assessment intake process" not in prompt.replace("\n", " "):
        return default_reply(url, payload)
    return chat(
        json.dumps(
            {
                "title": "Merchant Acquiring DE",
                "change_type": "NEW_GEOGRAPHY",
                "business_description": "Card acquiring.",
                "evidence": "Card acquiring for German merchants.",
                "countries": ["Germany", "Poland"],
                "third_party_vendors": ["Acme Processing Ltd"],
                "transaction_volume": "50,000 transactions per month",
                "legal_entity": "Imaginary Holdings Plc",
                "field_evidence": {
                    "countries": [{"document": "brief.txt", "quote": "Target Destination Jurisdictions: Germany, Poland"}],
                    "legal_entity": [{"quote": "The legal entity is Imaginary Holdings Plc", "confidence": 0.98}],
                },
            }
        )
    )


@pytest.fixture
def ai_extraction(fake_llm, monkeypatch):
    monkeypatch.setattr(ai_extractor, "OPENROUTER_API_KEY", "test-key-not-real")
    fake_llm.transport = _extraction_reply
    return fake_llm


# -- R2.4 field provenance ---------------------------------------------------


def test_extracted_fields_carry_provenance_from_every_uploaded_document(client, auth, ai_extraction):
    aid = _create_with_documents(client, auth, [("brief.txt", BRIEF), ("annex.txt", ANNEX)])["id"]
    docs = ok(client.get(f"/api/assessments/{aid}/documents", headers=auth("owner")))
    ids = {d["filename"]: d["id"] for d in docs}

    profile = ok(client.get(f"/api/assessments/{aid}/intelligence", headers=auth("owner")))
    assert sorted(profile["source_document_ids"]) == sorted(ids.values())
    assert profile["extraction_method"] == "AI" and profile["extracted_at"]

    provenance = profile["field_provenance"]
    countries = provenance["countries"]
    assert countries["confidence"] == "HIGH" and countries["verification"] == "QUOTE_CONTAINS_VALUE"
    assert countries["document_id"] == ids["brief.txt"] and countries["document_version"] == 1
    assert provenance["transaction_volume"]["confidence"] == "MEDIUM"
    # The invented quote and the model's own confidence count for nothing.
    assert provenance["legal_entity"]["confidence"] == "LOW"
    assert "0.98" not in json.dumps(provenance)
    assert profile["provenance_summary"]["HIGH"] >= 1 and profile["provenance_summary"]["LOW"] >= 1
    assert countries["confirmed_by_user"] is False

    ok(client.post(f"/api/assessments/{aid}/intelligence/confirm", json={}, headers=auth("owner")))
    confirmed = ok(client.get(f"/api/assessments/{aid}/intelligence", headers=auth("owner")))["field_provenance"]["countries"]
    assert confirmed["confirmed_by_user"] is True and confirmed["confirmed_by"] == "Owner"


def test_preview_extraction_returns_provenance_before_anything_is_saved(client, auth, ai_extraction):
    body = ok(
        client.post(
            "/api/assessments/analyze-document",
            files=[("files", ("brief.txt", BRIEF.encode(), "text/plain"))],
            headers=auth("owner"),
        )
    )
    assert body["field_provenance"]["countries"]["confidence"] == "HIGH"
    assert body["field_provenance"]["countries"]["filename"] == "brief.txt"


def test_rule_based_extraction_is_marked_and_never_high(client, auth, monkeypatch):
    monkeypatch.setattr(ai_extractor, "OPENROUTER_API_KEY", None)
    aid = _create_with_documents(client, auth, [("brief.txt", BRIEF)])["id"]
    profile = ok(client.get(f"/api/assessments/{aid}/intelligence", headers=auth("owner")))
    assert profile["extraction_method"] == "RULES"
    assert profile["field_provenance"]["countries"]["origin"] == "RULE_EXTRACTION"
    assert all(record["confidence"] != "HIGH" for record in profile["field_provenance"].values())


def test_correction_replaces_provenance_and_keeps_old_and_new_values(client, auth, ai_extraction):
    aid = _create_with_documents(client, auth, [("brief.txt", BRIEF)])["id"]
    ok(
        client.patch(
            f"/api/assessments/{aid}/intelligence",
            json={"countries": ["Germany", "France"]},
            headers=auth("analyst"),
        )
    )
    profile = ok(client.get(f"/api/assessments/{aid}/intelligence", headers=auth("owner")))
    record = profile["field_provenance"]["countries"]
    assert record["origin"] == "USER_CORRECTION" and record["corrected_by"] == "Analyst"
    assert record["replaces"]["confidence"] == "HIGH"

    versions = ok(client.get(f"/api/assessments/{aid}/intake-history?record_type=BUSINESS_PROFILE", headers=auth("owner")))["versions"]
    assert [v["trigger"] for v in versions] == ["PROFILE_EXTRACTED", "PROFILE_CORRECTED"]
    assert versions[1]["changes"] == [{"field": "countries", "old": ["Germany", "Poland"], "new": ["Germany", "France"]}]
    assert versions[1]["changed_by"] == "Analyst"
    corrected = _events(aid, "PROFILE_CORRECTED")
    assert corrected and "Poland" in corrected[-1].details and "France" in corrected[-1].details


# -- R3.4 intake snapshots -------------------------------------------------------


def test_request_edits_keep_the_original_and_need_a_reason_after_validation(client, auth, ai_extraction):
    aid = _create_with_documents(client, auth, [("brief.txt", BRIEF)])["id"]
    body = {**FULL_REQUEST, "is_draft": False, "customer_segment": "Large merchants"}

    ok(client.patch(f"/api/assessments/{aid}", json=body, headers=auth("owner")))
    ok(client.post(f"/api/assessments/{aid}/intelligence/confirm", json={}, headers=auth("owner")))

    # After validation a change needs a reason.
    refused = client.patch(f"/api/assessments/{aid}", json={**body, "countries_jurisdictions": "Germany, France"}, headers=auth("owner"))
    assert refused.status_code == 422 and "change_reason" in refused.text
    ok(
        client.patch(
            f"/api/assessments/{aid}",
            json={**body, "countries_jurisdictions": "Germany, France", "change_reason": "France replaces Poland in scope."},
            headers=auth("owner"),
        )
    )

    history = ok(client.get(f"/api/assessments/{aid}/intake-history", headers=auth("analyst")))["versions"]
    request = [v for v in history if v["record_type"] == "ASSESSMENT_REQUEST"]
    assert [v["trigger"] for v in request] == ["CREATED", "EDITED", "EDITED"]
    assert request[0]["snapshot"]["countries_jurisdictions"] == "Germany, Poland"  # the original is kept
    last = request[-1]
    assert last["was_validated"] is True and last["reason"] == "France replaces Poland in scope."
    assert last["changes"] == [{"field": "countries_jurisdictions", "old": "Germany, Poland", "new": "Germany, France"}]

    # A material field changed, so the owner confirms the profile again.
    profile = ok(client.get(f"/api/assessments/{aid}/intelligence", headers=auth("owner")))
    assert profile["confirmed"] is False
    assert [v["trigger"] for v in history if v["record_type"] == "BUSINESS_PROFILE"][-1] == "PROFILE_UNCONFIRMED"
    event = _events(aid, "INTAKE_UPDATED")[-1]
    assert "Germany, France" in event.details and "France replaces Poland" in event.details


def test_record_from_before_p4_gets_a_baseline_before_its_first_change(client, auth, users):
    db = SessionLocal()
    try:
        legacy = Assessment(**{k: v for k, v in FULL_REQUEST.items()}, is_draft=False, status="INTAKE", owner_id=users["owner"])
        db.add(legacy)
        db.commit()
        aid = legacy.id
    finally:
        db.close()

    ok(client.patch(f"/api/assessments/{aid}", json={**FULL_REQUEST, "is_draft": False, "title": "Renamed"}, headers=auth("owner")))
    versions = ok(client.get(f"/api/assessments/{aid}/intake-history", headers=auth("owner")))["versions"]
    assert [(v["trigger"], v["version"]) for v in versions] == [("BASELINE", 1), ("EDITED", 2)]
    assert versions[0]["snapshot"]["title"] == FULL_REQUEST["title"]


def test_snapshots_are_append_only(client, auth, create_assessment):
    aid = create_assessment()["id"]
    db = SessionLocal()
    try:
        row = db.query(IntakeSnapshot).filter(IntakeSnapshot.assessment_id == aid).first()
        row.reason = "rewritten"
        with pytest.raises(ProtectedDataError):
            db.flush()
        db.rollback()
        row = db.query(IntakeSnapshot).filter(IntakeSnapshot.assessment_id == aid).first()
        db.delete(row)
        with pytest.raises(ProtectedDataError):
            db.flush()
    finally:
        db.rollback()
        db.close()


# -- R2.6 expired evidence ---------------------------------------------------------


def _upload(client, auth, aid, name, text, who="owner", **form):
    return ok(
        client.post(
            f"/api/assessments/{aid}/documents",
            data=form,
            files={"file": (name, text.encode(), "text/plain")},
            headers=auth(who),
        )
    )


def _to_evidence_collection(client, auth, create_assessment):
    aid = create_assessment()["id"]
    ok(client.patch(f"/api/assessments/{aid}/advance-stage", json={}, headers=auth("owner")))
    client.patch(f"/api/assessments/{aid}/advance-stage", json={}, headers=auth("analyst"))  # creates the profile
    ok(client.post(f"/api/assessments/{aid}/intelligence/confirm", json={}, headers=auth("owner")))
    return aid


def test_expired_document_blocks_analysis_until_acknowledged(client, auth, create_assessment, fake_llm):
    aid = _to_evidence_collection(client, auth, create_assessment)
    expired = (date.today() - timedelta(days=10)).isoformat()
    doc = _upload(client, auth, aid, "old-policy.txt", "Vendor control policy for the cross-border processor.", expiry_date=expired)

    gaps = ok(client.get(f"/api/assessments/{aid}/evidence-gaps", headers=auth("owner")))
    assert gaps["acknowledgements_required"] == 1
    assert gaps["document_warnings"][0]["state"] == "ACKNOWLEDGEMENT_REQUIRED"

    blocked = client.patch(f"/api/assessments/{aid}/advance-stage", json={}, headers=auth("analyst"))
    assert blocked.status_code == 409 and blocked.json()["detail"]["code"] == "EXPIRED_EVIDENCE_UNACKNOWLEDGED"
    assert client.post(f"/api/assessments/{aid}/analyze", headers=auth("analyst")).status_code == 409

    url = f"/api/assessments/{aid}/documents/{doc['id']}/expiry-acknowledgement"
    assert client.post(url, json={"decision": "USE_AS_EVIDENCE", "reason": "ok"}, headers=auth("owner")).status_code == 422
    assert client.post(url, json={"decision": "MAYBE", "reason": "long enough reason"}, headers=auth("owner")).status_code == 422
    other = client.post(url, json={"decision": "USE_AS_EVIDENCE", "reason": "long enough reason"}, headers=auth("other_owner"))
    assert other.status_code == 403
    warning = ok(
        client.post(url, json={"decision": "EXCLUDE_FROM_EVIDENCE", "reason": "Superseded by the 2026 policy."}, headers=auth("owner")),
        201,
    )
    assert warning["state"] == "EXCLUDED_FROM_EVIDENCE" and warning["usable_as_evidence"] is False
    assert _events(aid, "EVIDENCE_EXPIRY_ACKNOWLEDGED")

    ok(client.patch(f"/api/assessments/{aid}/advance-stage", json={}, headers=auth("analyst")))
    # The excluded document was not offered to the model as a source.
    assert f'DOC:{doc["id"]}' not in prompt_of(fake_llm.factor_calls()[-1]["json"])


def test_acknowledged_expired_document_is_used_and_shown_in_statements(client, auth, create_assessment, fake_llm):
    aid = _to_evidence_collection(client, auth, create_assessment)
    expired = (date.today() - timedelta(days=3)).isoformat()
    doc = _upload(client, auth, aid, "old-terms.txt", "Remote onboarding terms for the processor.", expiry_date=expired)
    ok(
        client.post(
            f"/api/assessments/{aid}/documents/{doc['id']}/expiry-acknowledgement",
            json={"decision": "USE_AS_EVIDENCE", "reason": "Terms unchanged; renewal in progress."},
            headers=auth("analyst"),
        ),
        201,
    )
    ok(client.patch(f"/api/assessments/{aid}/advance-stage", json={}, headers=auth("analyst")))
    assert f'DOC:{doc["id"]}' in prompt_of(fake_llm.factor_calls()[-1]["json"])

    statements = ok(client.get(f"/api/assessments/{aid}/explain/statements", headers=auth("analyst")))["statements"]
    on_file = next(s for s in statements if s["reference"] == {"type": "document", "id": doc["id"]})
    assert "acknowledgement" in on_file["statement"]


def test_only_the_current_version_can_be_superseded(client, auth, create_assessment):
    aid = create_assessment()["id"]
    first = _upload(client, auth, aid, "a.txt", "version one")
    _upload(client, auth, aid, "a.txt", "version two", supersedes_id=str(first["id"]))
    third = client.post(
        f"/api/assessments/{aid}/documents",
        data={"supersedes_id": str(first["id"])},
        files={"file": ("a.txt", b"fork", "text/plain")},
        headers=auth("owner"),
    )
    assert third.status_code == 409


# -- Stage 4 rules and indicators ----------------------------------------------------


def _all_not_applicable(url, payload):
    prompt = prompt_of(payload)
    if "Work through EVERY one of the 10 risk categories" not in prompt:
        return default_reply(url, payload)
    factors = [{**f, "applicable": False, "indicators": [], "evidence": []} for f in factors_for(prompt)]
    return chat(json.dumps({"factors": factors}))


def test_fixed_rules_require_categories_even_when_the_ai_says_not_applicable(client, auth, analysed_assessment, fake_llm):
    fake_llm.transport = _all_not_applicable
    aid = analysed_assessment()["id"]
    factors = {f["category"]: f for f in ok(client.get(f"/api/assessments/{aid}/risk-factors", headers=auth("analyst")))}

    for category in ("GEOGRAPHIC_RISK", "PRODUCT_SERVICE_RISK", "TRANSACTION_ACTIVITY_RISK", "DELIVERY_CHANNEL_RISK", "THIRD_PARTY_VENDOR_RISK"):
        factor = factors[category]
        assert factor["applicable"] is True, category
        assert factor["rule_triggers"] and factor["rule_triggers"][0]["effect"] == "FORCED_APPLICABLE"
        assert factor["likelihood"] is None  # still unrated: an analyst must rate or exclude it
    assert "SANCTIONS_EXPOSURE" not in factors["GEOGRAPHIC_RISK"]["indicators"]
    assert factors["OWNERSHIP_ENTITY_COMPLEXITY_RISK"]["applicable"] is False
    analysis = _events(aid, "ANALYSIS")[-1]
    assert "S4-01-CROSS-BORDER-PAYMENT" in analysis.details

    rules = ok(client.get("/api/risk-methodologies/active/stage4-rules", headers=auth("analyst")))
    assert rules["status"] == "PROVISIONAL_PENDING_BUSINESS_VALIDATION" and len(rules["rules"]) == 3

    inherent = ok(client.get(f"/api/assessments/{aid}/inherent-risk", headers=auth("analyst")))
    assert inherent["is_provisional"] is True


def test_analyst_edits_indicators_through_the_ledger(client, auth, analysed_assessment):
    aid = analysed_assessment()["id"]
    factor = next(f for f in ok(client.get(f"/api/assessments/{aid}/risk-factors", headers=auth("analyst"))) if f["category"] == "GEOGRAPHIC_RISK")
    url = f"/api/assessments/{aid}/risk-factors/{factor['id']}/indicators"

    assert client.patch(url, json={"indicators": ["SANCTIONS_EXPOSURE"], "reason": ""}, headers=auth("analyst")).status_code == 422
    assert client.patch(url, json={"indicators": ["NOT_REAL"], "reason": "x"}, headers=auth("analyst")).status_code == 422
    assert client.patch(url, json={"indicators": ["SANCTIONS_EXPOSURE"], "reason": "x"}, headers=auth("owner")).status_code == 403

    updated = ok(
        client.patch(
            url,
            json={"indicators": factor["indicators"] + ["SANCTIONS_EXPOSURE"], "reason": "Corridor includes a sanctioned bank."},
            headers=auth("analyst"),
        )
    )
    assert "SANCTIONS_EXPOSURE" in updated["indicators"]
    ledger = ok(client.get(f"/api/assessments/{aid}/overrides", headers=auth("analyst")))
    row = next(r for r in ledger if r["section"] == "RISK_CATEGORY" and r["field_name"] == "indicators")
    assert row["materiality"] == "CRITICAL" and row["entity_id"] == str(factor["id"])
    assert "SANCTIONS_EXPOSURE" not in json.loads(row["ai_value"])
    assert _events(aid, "RISK_INDICATORS_CHANGED")


# -- R5.4 evidence categories --------------------------------------------------------


def test_statements_carry_the_five_evidence_categories(client, auth, ai_extraction):
    aid = _create_with_documents(client, auth, [("brief.txt", BRIEF)])["id"]
    ok(client.patch(f"/api/assessments/{aid}/advance-stage", json={}, headers=auth("owner")))
    ok(client.post(f"/api/assessments/{aid}/intelligence/confirm", json={}, headers=auth("owner")))
    ok(client.patch(f"/api/assessments/{aid}/advance-stage", json={}, headers=auth("analyst")))

    result = ok(client.get(f"/api/assessments/{aid}/explain/statements", headers=auth("analyst")))
    assert set(result["evidence_categories"]) == {
        "DIRECT_EVIDENCE", "EXTRACTED_INFORMATION", "SYSTEM_INTERPRETATION", "ANALYST_COMMENTARY", "ASSUMPTION"
    }
    by_category = {}
    for statement in result["statements"]:
        if statement["kind"] == "DECISION":
            assert statement["evidence_category"] is None
        else:
            assert statement["evidence_category"] in result["evidence_categories"]
        by_category.setdefault(statement["evidence_category"], []).append(statement)

    extracted = by_category["EXTRACTED_INFORMATION"]
    countries = next(s for s in extracted if s["statement"].startswith("Countries"))
    assert "Confidence HIGH" in countries["statement"] and countries["location"]["filename"] == "brief.txt"
    assert any(s["source"].startswith("Verified quote") for s in by_category["DIRECT_EVIDENCE"])
    assert by_category["SYSTEM_INTERPRETATION"] and by_category["ASSUMPTION"]
    assert result["evidence_category_counts"]["EXTRACTED_INFORMATION"] == len(extracted)

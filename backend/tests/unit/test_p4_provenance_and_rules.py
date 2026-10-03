"""
P4 unit tests: deterministic field provenance (app/governance/provenance.py)
and the fixed Stage 4 rules (app/risk_engine/stage4_rules.py). Pure
functions -- no database, no model.
"""

import json

import pytest

from app.governance import provenance as p
from app.risk_engine import stage4_rules
from app.schemas.document_analysis import DocumentAssessmentExtraction

PDF_TEXT = (
    "[Page 1]\nProject overview.\n[Page 2]\n"
    "Target Destination Jurisdictions: Germany, Poland\n"
    "Primary Vendor: Acme Processing Ltd handles card settlement."
)
SHEET_TEXT = "[Sheet: Summary]\nOverview | x\n[Sheet: Volumes]\nProjected Volume | 50,000 transactions per month"


def _extraction(**fields) -> DocumentAssessmentExtraction:
    base = {"title": "T", "change_type": "NEW_PRODUCT", "business_description": "D", "evidence": "E"}
    return DocumentAssessmentExtraction(**{**base, **fields})


def _sources():
    return [p.SourceText(11, 2, "brief.pdf", PDF_TEXT), p.SourceText(12, 1, "volumes.xlsx", SHEET_TEXT)]


def test_verified_quote_containing_the_value_is_high_with_document_and_page():
    extraction = _extraction(
        third_party_vendors=["Acme Processing Ltd"],
        field_evidence={"third_party_vendors": [{"document": "brief.pdf", "quote": "Primary Vendor: Acme Processing Ltd handles card"}]},
    )
    record = p.build_field_provenance(extraction, _sources(), method="AI")["third_party_vendors"]
    assert record["confidence"] == p.HIGH and record["verification"] == p.QUOTE_CONTAINS_VALUE
    assert (record["document_id"], record["document_version"], record["page"]) == (11, 2, 2)
    assert record["origin"] == p.AI_EXTRACTION and record["extracted_at"]


def test_value_in_document_without_a_quote_is_medium_and_sheet_is_located():
    extraction = _extraction(transaction_volume="50,000 transactions per month")
    record = p.build_field_provenance(extraction, _sources(), method="AI")["transaction_volume"]
    assert record["confidence"] == p.MEDIUM and record["verification"] == p.VALUE_FOUND_IN_SOURCE
    assert record["filename"] == "volumes.xlsx" and record["sheet"] == "Volumes" and record["page"] is None


def test_paraphrase_backed_by_a_verified_quote_is_medium():
    extraction = _extraction(
        business_line="Card acquiring for merchants",
        field_evidence={"business_line": [{"quote": "Acme Processing Ltd handles card settlement."}]},
    )
    record = p.build_field_provenance(extraction, _sources(), method="AI")["business_line"]
    assert record["confidence"] == p.MEDIUM and record["verification"] == p.QUOTE_SUPPORTS_INTERPRETATION


def test_value_not_in_any_document_is_low_even_with_an_invented_quote_or_model_confidence():
    extraction = _extraction(
        legal_entity="Bank Holdings Plc",
        field_evidence={"legal_entity": [{"quote": "Legal entity: Bank Holdings Plc", "confidence": 0.99}]},
    )
    record = p.build_field_provenance(extraction, _sources(), method="AI")["legal_entity"]
    assert record["confidence"] == p.LOW and record["verification"] == p.NOT_FOUND_IN_SOURCE
    assert "0.99" not in json.dumps(record)


def test_list_field_takes_its_weakest_item_and_matches_whole_words():
    extraction = _extraction(countries=["Germany", "UK"])
    record = p.build_field_provenance(
        extraction, [p.SourceText(1, 1, "a.txt", "Payments to Germany and Ukraine.")], method="RULES"
    )["countries"]
    items = {item["value"]: item["confidence"] for item in record["items"]}
    # "UK" is not found inside "Ukraine".
    assert items == {"Germany": p.MEDIUM, "UK": p.LOW}
    assert record["confidence"] == p.LOW


def test_rule_based_extraction_never_reaches_high_and_ignores_quotes():
    extraction = _extraction(
        third_party_vendors=["Acme Processing Ltd"],
        field_evidence={"third_party_vendors": [{"quote": "Primary Vendor: Acme Processing Ltd handles card"}]},
        extraction_method="RULES",
    )
    record = p.build_field_provenance(extraction, _sources(), method="RULES")["third_party_vendors"]
    assert record["confidence"] == p.MEDIUM and record["origin"] == p.RULE_EXTRACTION


def test_empty_fields_and_summaries_get_no_provenance():
    provenance = p.build_field_provenance(_extraction(countries=[], business_line=None), _sources(), method="AI")
    assert provenance == {}


def test_correction_keeps_the_extracted_record():
    original = p.build_field_provenance(_extraction(countries=["Germany"]), _sources(), method="AI")["countries"]
    corrected = p.correction_record(original, "countries", ["Germany", "France"], actor="Owner", actor_id=5)
    assert corrected["origin"] == p.USER_CORRECTION and corrected["confidence"] == p.USER_PROVIDED
    assert corrected["replaces"]["confidence"] == p.MEDIUM and corrected["corrected_by_id"] == 5


def test_ai_extractor_drops_unknown_fields_and_confidence_from_field_evidence():
    from app.document_analysis.ai_extractor import _normalize_field_evidence

    cleaned = _normalize_field_evidence(
        {"countries": [{"document": "a", "quote": "Germany", "confidence": 1}], "not_a_field": ["x"], "business_line": "Retail line"}
    )
    assert cleaned == {"countries": [{"document": "a", "quote": "Germany"}], "business_line": [{"document": "", "quote": "Retail line"}]}


# -- Stage 4 rules -----------------------------------------------------------

CROSS_BORDER = {
    "title": "Card acquiring",
    "description": "Card acquiring for German merchants.",
    "countries_jurisdictions": "Germany, Poland",
    "transaction_types": "Card acquiring",
    "delivery_channels": "Branch",
    "third_party_vendor_usage": "None",
}


def _na(category):
    return {"category": category, "applicable": False, "indicators": [], "rationale": "Not relevant.", "missing_information": []}


def test_cross_border_payment_forces_geographic_product_and_transaction_and_flags_sanctions():
    factors = [_na("GEOGRAPHIC_RISK"), {**_na("PRODUCT_SERVICE_RISK"), "applicable": True}]
    applied = stage4_rules.apply(factors, CROSS_BORDER)
    by_category = {f["category"]: f for f in factors}

    assert {a["category"] for a in applied} == {"GEOGRAPHIC_RISK", "PRODUCT_SERVICE_RISK", "TRANSACTION_ACTIVITY_RISK"}
    geographic = by_category["GEOGRAPHIC_RISK"]
    assert geographic["applicable"] is True and geographic["evidence_status"] == "INSUFFICIENT_EVIDENCE"
    assert "Not relevant." in geographic["rationale"]  # the AI's view is kept
    assert geographic["rule_triggers"][0]["effect"] == "FORCED_APPLICABLE"
    # Sanctions are considered, never asserted (a forced indicator would escalate to CRITICAL).
    assert "SANCTIONS_EXPOSURE" not in geographic["indicators"]
    assert any("Sanctions exposure" in note for note in geographic["missing_information"])
    assert by_category["PRODUCT_SERVICE_RISK"]["rule_triggers"][0]["effect"] == "CONFIRMED"
    assert by_category["TRANSACTION_ACTIVITY_RISK"]["rule_triggers"][0]["effect"] == "ADDED"
    trigger = geographic["rule_triggers"][0]
    assert trigger["ruleset_version"] == stage4_rules.ruleset()["version"]
    assert trigger["ruleset_status"] == "PROVISIONAL_PENDING_BUSINESS_VALIDATION"
    assert {s["field"] for s in trigger["signals"]} >= {"multiple_countries"}


def test_single_country_without_cross_border_words_does_not_fire():
    fired = stage4_rules.evaluate({**CROSS_BORDER, "countries_jurisdictions": "Germany"})
    assert "S4-01-CROSS-BORDER-PAYMENT" not in {rule["rule_id"] for rule in fired}


def test_remote_digital_channel_requires_channel_risk_with_authentication_consideration():
    factors = []
    stage4_rules.apply(factors, {**CROSS_BORDER, "delivery_channels": "Web portal; API"})
    channel = next(f for f in factors if f["category"] == "DELIVERY_CHANNEL_RISK")
    assert channel["applicable"] is True
    assert any("Authentication risk" in note for note in channel["missing_information"])


def test_keywords_match_whole_words_only():
    # "app" must not fire on "approval", nor "api" on "capital".
    fired = stage4_rules.evaluate({"delivery_channels": "Branch approval; capital desk", "description": "x"})
    assert "S4-02-REMOTE-DIGITAL-CHANNEL" not in {rule["rule_id"] for rule in fired}


def test_third_party_processor_is_included_but_none_is_not_a_vendor():
    fired = {rule["rule_id"] for rule in stage4_rules.evaluate({"third_party_vendor_usage": "External acquiring processor"})}
    assert "S4-03-THIRD-PARTY-PROCESSOR" in fired
    fired = {rule["rule_id"] for rule in stage4_rules.evaluate({"third_party_vendor_usage": "None"})}
    assert "S4-03-THIRD-PARTY-PROCESSOR" not in fired


def test_profile_fields_are_signals_too():
    class Profile:
        def get_list(self, field):
            return {"third_party_vendors": ["Acme"], "channels": ["Mobile app"]}.get(field, [])

        onboarding_approach = None
        transaction_origin = None
        transaction_destination = None

    fired = {rule["rule_id"] for rule in stage4_rules.evaluate({}, Profile())}
    assert {"S4-02-REMOTE-DIGITAL-CHANNEL", "S4-03-THIRD-PARTY-PROCESSOR"} <= fired


def test_ruleset_is_configurable_and_versioned(tmp_path, monkeypatch):
    custom = {
        "version": "test-9",
        "rules": [
            {
                "rule_id": "X-1",
                "description": "Crypto",
                "signal_groups": [{"name": "crypto", "fields": ["description"], "keywords": ["crypto*"], "checks": []}],
                "required": [{"category": "FINANCIAL_CRIME_TYPOLOGY_RISK", "considerations": []}],
            }
        ],
    }
    path = tmp_path / "rules.json"
    path.write_text(json.dumps(custom), encoding="utf-8")
    monkeypatch.setenv("STAGE4_RULES_FILE", str(path))
    stage4_rules.reload()
    try:
        factors = []
        stage4_rules.apply(factors, {"description": "A cryptocurrency wallet"})
        assert factors[0]["category"] == "FINANCIAL_CRIME_TYPOLOGY_RISK"
        assert factors[0]["rule_triggers"][0]["ruleset_version"] == "test-9"
        assert factors[0]["rule_triggers"][0]["ruleset_status"] == stage4_rules.RULESET_STATUS
    finally:
        monkeypatch.delenv("STAGE4_RULES_FILE")
        stage4_rules.reload()


@pytest.mark.parametrize("field", ["description", "evidence", "title"])
def test_cross_border_words_in_narrative_fields_count(field):
    fired = stage4_rules.evaluate({field: "International money transfers", "transaction_types": "wire transfers"})
    assert "S4-01-CROSS-BORDER-PAYMENT" in {rule["rule_id"] for rule in fired}

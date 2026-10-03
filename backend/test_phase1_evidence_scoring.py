"""
Phase 1 acceptance checks: deterministic scoring from verified evidence.

  - the model's numbers never reach overall_score, the band or routing
  - every stored indicator is backed by a quote found in its source;
    fabricated quotes are rejected and support nothing
  - a factor with no usable evidence is unrated, never low, and keeps the
    result provisional; a provisional result cannot advance
  - policy rules come from the approved rule set only, and each firing
    is recorded on the calculation and in the audit log
  - legacy rule records behave exactly as before

Part A exercises the pure functions; part B drives the real API, with
only the HTTP call beneath app/ai/metering.py faked. Run from backend/:

    python test_phase1_evidence_scoring.py
"""

import json
import os
import tempfile

_DB_FILE = os.path.join(tempfile.mkdtemp(), "phase1_test.db")
# Tests never use the real AI provider/key from backend/.env (their HTTP
# calls are faked or absent); pin the offline OpenRouter configuration.
os.environ["LLM_PROVIDER"] = "openrouter"
os.environ["OPENAI_API_KEY"] = ""
os.environ["DATABASE_URL"] = f"sqlite:///{_DB_FILE}"
os.environ["WORKFLOW_ESCALATION_INTERVAL_SECONDS"] = "0"
os.environ["ADMIN_BOOTSTRAP_PASSWORD"] = "ChangeMe123!"
os.environ["BACKUP_INTERVAL_HOURS"] = "0"
os.environ["PROCESSING_JOBS_INLINE"] = "true"
os.environ["OPENROUTER_API_KEY"] = "test-key-not-real"
os.environ["OPENROUTER_MAX_ATTEMPTS"] = "1"
os.environ["OPENROUTER_BACKOFF_SECONDS"] = "0"

from fastapi.testclient import TestClient  # noqa: E402

import app.ai.metering as metering  # noqa: E402
from app.auth.security import hash_password  # noqa: E402
from app.database import SessionLocal  # noqa: E402
from app.main import app, prepare_database  # noqa: E402

# The schema is prepared explicitly; importing the app never migrates.
prepare_database()
from app.models.audit_event import AuditEvent  # noqa: E402
from app.models.user import User  # noqa: E402
from app.risk_engine.evidence import (  # noqa: E402
    EvidenceStatus,
    QuoteVerification,
    build_evidence_sources,
    verify_factor_evidence,
    verify_quote,
)
from app.risk_engine.scoring import (  # noqa: E402
    DEFAULT_ESCALATION_RULES,
    calculate_inherent_risk,
    compute_factor_score,
    normalize_rule,
)

PASSED: list[str] = []


def check(name: str, condition: bool, detail: str = "") -> None:
    assert condition, f"FAILED: {name}. {detail}"
    PASSED.append(name)
    print(f"  ok  {name}")


# ===========================================================================
# A. Pure functions
# ===========================================================================
print("\nA1. Quote verification")


class _Doc:
    def __init__(self, id, text, filename="policy.pdf", version=2):
        self.id, self.extracted_text, self.filename, self.version = id, text, filename, version


SOURCES = build_evidence_sources(
    {"description": "Card acquiring with cross-border  settlement to Poland."},
    [_Doc(7, "[Page 1]\nIntro text.\n[Page 2]\nMerchants are onboarded remotely via the web portal.")],
)

exact = verify_quote("FIELD:description", "cross-border settlement to Poland", SOURCES)
check("an exact quote is verified", exact["verification"] == QuoteVerification.EXACT_VERIFIED)
check("verification ignores whitespace runs and case", exact["quote_verified"])
check("a verified quote records the source checksum", exact["source_checksum"].startswith("sha256:"))

curly = verify_quote("DOC:7", "Merchants are onboarded remotely via the web portal", SOURCES)
check("a document quote is verified with its document id", curly["quote_verified"] and curly["document_id"] == 7)
check("a PDF quote gets its page from the extractor's marker", curly["page"] == 2, str(curly["page"]))
check("a document quote records the document version", curly["document_version"] == 2)

fabricated = verify_quote("FIELD:description", "settlement to sanctioned jurisdictions", SOURCES)
check("a fabricated quote is rejected", fabricated["verification"] == QuoteVerification.NOT_FOUND and not fabricated["quote_verified"])

altered = verify_quote("FIELD:description", "cross-border settlements to Poland", SOURCES)
check("an altered quote is rejected (no fuzzy matching)", not altered["quote_verified"])

wrong_source = verify_quote("DOC:7", "cross-border settlement to Poland", SOURCES)
check("a real quote cited to the wrong source is rejected", not wrong_source["quote_verified"])

unknown = verify_quote("DOC:999", "cross-border settlement to Poland", SOURCES)
check("an unknown source id is rejected", unknown["verification"] == QuoteVerification.UNKNOWN_SOURCE)

short = verify_quote("FIELD:description", "Poland", SOURCES)
check("a trivially short quote proves nothing", short["verification"] == QuoteVerification.TOO_SHORT)


print("\nA2. Factor evidence status")


def status_of(**factor):
    factor.setdefault("applicable", True)
    factor.setdefault("indicators", [])
    return verify_factor_evidence(factor, SOURCES)


found = status_of(
    indicators=["CROSS_BORDER_CAPABILITY", "SANCTIONS_EXPOSURE"],
    evidence=[
        {"source_id": "FIELD:description", "quote": "cross-border settlement to Poland", "indicator": "CROSS_BORDER_CAPABILITY"},
        {"source_id": "FIELD:description", "quote": "payments to sanctioned parties", "indicator": "SANCTIONS_EXPOSURE"},
    ],
)
check("verified evidence -> EVIDENCE_FOUND", found["evidence_status"] == EvidenceStatus.EVIDENCE_FOUND)
check("an indicator with a verified quote is kept", found["indicators"] == ["CROSS_BORDER_CAPABILITY"], str(found["indicators"]))
check(
    "an indicator whose only quote is fabricated is rejected",
    [item["indicator"] for item in found["rejected_indicators"]] == ["SANCTIONS_EXPOSURE"],
)
check("rejected quotes are kept on the record", found["rejected_quote_count"] == 1 and len(found["evidence"]) == 2)

untagged = status_of(indicators=["REMOTE_ONBOARDING"], evidence=[])
check("an indicator with no quote at all is rejected", untagged["indicators"] == [] and untagged["rejected_indicators"])
check("applicable with no evidence -> INSUFFICIENT_EVIDENCE", untagged["evidence_status"] == EvidenceStatus.INSUFFICIENT_EVIDENCE)

not_verified = status_of(evidence=[{"source_id": "FIELD:description", "quote": "invented sentence about cash"}])
check("only fabricated quotes -> NOT_VERIFIED", not_verified["evidence_status"] == EvidenceStatus.NOT_VERIFIED)

conflict = status_of(
    conflicting_evidence=True,
    evidence=[{"source_id": "FIELD:description", "quote": "cross-border settlement to Poland"}],
)
check("verified quotes + model-flagged conflict -> CONFLICTING_EVIDENCE", conflict["evidence_status"] == EvidenceStatus.CONFLICTING_EVIDENCE)

na = status_of(applicable=False, indicators=["CASH_ACCESS"])
check("not applicable -> NOT_APPLICABLE, no indicators", na["evidence_status"] == EvidenceStatus.NOT_APPLICABLE and na["indicators"] == [])


print("\nA3. Deterministic scoring")


def rated(category, likelihood, impact, **extra):
    return {
        "category": category, "applicable": True, "excluded": False, "rated": True,
        "likelihood": likelihood, "impact": impact,
        "score": compute_factor_score(likelihood, impact), "indicators": [], **extra,
    }


def unrated(category, **extra):
    return {"category": category, "applicable": True, "excluded": False, "rated": False,
            "score": 0.0, "indicators": [], **extra}


low = calculate_inherent_risk([rated("PRODUCT_SERVICE_RISK", 1, 2), rated("DELIVERY_CHANNEL_RISK", 2, 2)])
check("LOW scenario", low["risk_band"] == "LOW" and low["final_score"] == 12.0, str(low["final_score"]))

medium = calculate_inherent_risk([rated("PRODUCT_SERVICE_RISK", 3, 3), rated("DELIVERY_CHANNEL_RISK", 3, 4)])
check("MEDIUM scenario", medium["risk_band"] == "MEDIUM", f"{medium['final_score']} {medium['risk_band']}")

high = calculate_inherent_risk([rated("PRODUCT_SERVICE_RISK", 4, 4), rated("DELIVERY_CHANNEL_RISK", 4, 4)])
check("HIGH scenario", high["risk_band"] == "HIGH" and high["final_score"] == 64.0)

critical = calculate_inherent_risk([rated("PRODUCT_SERVICE_RISK", 5, 5), rated("DELIVERY_CHANNEL_RISK", 4, 5)])
check("CRITICAL scenario", critical["risk_band"] == "CRITICAL")

diluted = calculate_inherent_risk([rated("PRODUCT_SERVICE_RISK", 4, 4), unrated("CUSTOMER_SEGMENT_RISK")])
check("an unrated factor is left out of the average, not counted as 0", diluted["final_score"] == 64.0, str(diluted["final_score"]))
check("an unrated factor makes the result provisional", diluted["is_provisional"])

nothing_rated = calculate_inherent_risk([unrated("GEOGRAPHIC_RISK", evidence_status="INSUFFICIENT_EVIDENCE")])
check("insufficient evidence and nothing rated -> no score, no band", nothing_rated["final_score"] is None and nothing_rated["risk_band"] is None)
check("... and provisional", nothing_rated["is_provisional"])

none_applicable = calculate_inherent_risk([{**unrated("GEOGRAPHIC_RISK"), "applicable": False}])
check("every category not applicable is a genuine 0 / LOW", none_applicable["final_score"] == 0.0 and none_applicable["risk_band"] == "LOW")

ignores_model = calculate_inherent_risk([unrated("GEOGRAPHIC_RISK", score=95.0, model_score=95)])
check("a model-supplied score on an unrated factor is never used", ignores_model["final_score"] is None)


print("\nA4. Policy rules")

sanctioned = calculate_inherent_risk([rated("PRODUCT_SERVICE_RISK", 1, 1), rated("GEOGRAPHIC_RISK", 1, 2, indicators=["SANCTIONS_EXPOSURE"])])
check("sanctions override forces CRITICAL over a LOW average", sanctioned["risk_band"] == "CRITICAL")
rule = sanctioned["triggered_rules"][0]
check("the fired rule is recorded with code, version and before/after", rule["rule_code"] == "SANCTIONS_EXPOSURE_001" and rule["band_before"] == "LOW" and rule["band_after"] == "CRITICAL" and rule["version"] == "1.0")
check("the sanctions rule requires mandatory review", sanctioned["mandatory_review"])

pre_rating = calculate_inherent_risk([unrated("GEOGRAPHIC_RISK", indicators=["SANCTIONS_EXPOSURE"])])
check("a verified sanctions indicator escalates even before rating", pre_rating["risk_band"] == "CRITICAL" and pre_rating["final_score"] is None)

key_high = [rated("PRODUCT_SERVICE_RISK", 1, 1), rated("PRODUCT_SERVICE_RISK", 1, 1), rated("GEOGRAPHIC_RISK", 4, 4)]
draft_only = calculate_inherent_risk(key_high)
check("the key-factor floor ships as draft and does not fire", not any(r["rule_code"] == "MIN_BAND_KEY_FACTOR_001" for r in draft_only["triggered_rules"]))

approved_rules = [{**r, "status": "approved"} for r in DEFAULT_ESCALATION_RULES]
floored = calculate_inherent_risk(key_high, escalation_rules=approved_rules)
check("once approved, a HIGH key factor sets a HIGH floor", floored["risk_band"] == "HIGH", f"{floored['final_score']} {floored['risk_band']}")

non_key = calculate_inherent_risk(
    [rated("PRODUCT_SERVICE_RISK", 1, 1), rated("PRODUCT_SERVICE_RISK", 1, 1), rated("TECHNOLOGY_DEVELOPMENT_RISK", 4, 4)],
    escalation_rules=approved_rules,
)
check("a HIGH non-key factor sets no floor", non_key["risk_band"] == "LOW" and not non_key["triggered_rules"])

unrated_key = calculate_inherent_risk([rated("PRODUCT_SERVICE_RISK", 1, 1), unrated("GEOGRAPHIC_RISK")], escalation_rules=approved_rules)
check("an unrated key factor cannot trigger a rating threshold", not unrated_key["triggered_rules"])

disabled = calculate_inherent_risk(
    [rated("GEOGRAPHIC_RISK", 1, 1, indicators=["SANCTIONS_EXPOSURE"])],
    escalation_rules=[{**DEFAULT_ESCALATION_RULES[0], "status": "disabled"}],
)
check("a disabled rule is ignored", disabled["risk_band"] == "LOW")

legacy_rule = {"id": "sanctions_exposure", "description": "old", "indicator": "SANCTIONS_EXPOSURE", "min_band": "CRITICAL"}
check("a legacy rule record is read as approved", normalize_rule(legacy_rule)["status"] == "approved")
legacy = calculate_inherent_risk([rated("GEOGRAPHIC_RISK", 1, 1, indicators=["SANCTIONS_EXPOSURE"])], escalation_rules=[legacy_rule])
check("a legacy rule behaves as before", legacy["risk_band"] == "CRITICAL" and legacy["escalation_reasons"] == ["old"])


# ===========================================================================
# B. Through the API
# ===========================================================================
print("\nB. End to end")

client = TestClient(app)
PASSWORD = "Passw0rd!"

REQUEST = {
    "title": "Merchant acquiring in Germany",
    "change_type": "NEW_GEOGRAPHY",
    "product_or_service_name": "Merchant Acquiring DE",
    "description": (
        "Card acquiring for German merchants with cross-border settlement "
        "to Poland, using an external processor."
    ),
    "evidence": (
        "Merchants in Germany receive cross-border settlement; onboarding "
        "is remote and customer data is shared with the processor."
    ),
    "business_owner": "Merchant Services",
    "legal_entity": "Bank DE GmbH",
    "customer_segment": "SME merchants",
    "countries_jurisdictions": "Germany, Poland",
    "delivery_channels": "Web portal; API",
    "expected_transaction_volume": "50k/month",
    "expected_transaction_value": "EUR 20m/month",
    "transaction_types": "Card acquiring",
    "third_party_vendor_usage": "Acquiring processor",
    "technology_process_changes": "New acquiring platform",
    "expected_launch_date": "2027-03-01",
}

NOT_APPLICABLE = [
    {"category": c, "applicable": False, "rationale": f"{c} does not apply to card acquiring."}
    for c in ["PRODUCT_SERVICE_RISK", "TRANSACTION_ACTIVITY_RISK", "OWNERSHIP_ENTITY_COMPLEXITY_RISK",
              "FINANCIAL_CRIME_TYPOLOGY_RISK", "CONTROL_ENVIRONMENT_RISK"]
]
# TECHNOLOGY_DEVELOPMENT_RISK is omitted on purpose.


def model_factors(sanctions_quote: str) -> list[dict]:
    return NOT_APPLICABLE + [
        {
            "category": "GEOGRAPHIC_RISK",
            "applicable": True,
            "score": 99, "severity": "CRITICAL",  # must be ignored
            "indicators": ["CROSS_BORDER_CAPABILITY", "SANCTIONS_EXPOSURE"],
            "evidence": [
                {"source_id": "FIELD:description", "quote": "cross-border settlement to Poland", "indicator": "CROSS_BORDER_CAPABILITY"},
                {"source_id": "FIELD:evidence", "quote": sanctions_quote, "indicator": "SANCTIONS_EXPOSURE"},
            ],
            "rationale": "Settlement crosses borders.",
        },
        {
            "category": "DELIVERY_CHANNEL_RISK",
            "applicable": True,
            "score": 95,
            "indicators": ["REMOTE_ONBOARDING"],
            "evidence": [{"source_id": "FIELD:evidence", "quote": "onboarding is remote", "indicator": "REMOTE_ONBOARDING"}],
            "rationale": "Remote onboarding.",
        },
        {
            "category": "THIRD_PARTY_VENDOR_RISK",
            "applicable": True,
            "indicators": ["THIRD_PARTY_DEPENDENCIES"],
            "evidence": [{"source_id": "FIELD:description", "quote": "the processor is located offshore", "indicator": "THIRD_PARTY_DEPENDENCIES"}],
            "rationale": "External processor.",
        },
        {
            "category": "CUSTOMER_SEGMENT_RISK",
            "applicable": True,
            "evidence": [],
            "missing_information": ["Merchant categories and ownership"],
            "rationale": "Customer profile unclear.",
        },
    ]


class _Response:
    def __init__(self, body):
        self.status_code = 200
        self._body = body
        self.text = json.dumps(body)
        self.content = self.text.encode()

    def json(self):
        return self._body


_factors = model_factors("sanctioned")


def _dispatch(url, headers=None, json=None, timeout=None, **kwargs):
    return _Response({
        "model": "test/model",
        "choices": [{"message": {"content": __import__("json").dumps({"factors": _factors})}}],
        "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
    })


metering.requests.post = _dispatch


def _users() -> None:
    db = SessionLocal()
    try:
        manager = User(email="manager@test.io", hashed_password=hash_password(PASSWORD), full_name="Manager", role="MANAGER")
        db.add(manager)
        db.flush()
        for email, role in [("owner@test.io", "BUSINESS_USER"), ("analyst@test.io", "FCRM_ANALYST")]:
            db.add(User(email=email, hashed_password=hash_password(PASSWORD), full_name=email.split("@")[0].title(), role=role, manager_id=manager.id))
        db.commit()
    finally:
        db.close()


_users()
_TOKENS: dict[str, str] = {}


def auth(who: str) -> dict:
    if who not in _TOKENS:
        response = client.post("/api/auth/login", json={"email": f"{who}@test.io", "password": PASSWORD})
        assert response.status_code == 200, response.text
        _TOKENS[who] = response.json()["access_token"]
    return {"Authorization": f"Bearer {_TOKENS[who]}"}


def ok(response, status=200):
    assert response.status_code == status, f"{response.status_code}: {response.text}"
    return response.json()


def analysed(factors: list[dict], **request) -> int:
    global _factors
    _factors = factors
    aid = ok(client.post("/api/assessments", json={**REQUEST, **request, "is_draft": False}, headers=auth("owner")), 201)["id"]
    ok(client.patch(f"/api/assessments/{aid}/advance-stage", json={}, headers=auth("owner")))
    client.patch(f"/api/assessments/{aid}/advance-stage", json={}, headers=auth("analyst"))
    ok(client.post(f"/api/assessments/{aid}/intelligence/confirm", json={"confirmed_by": "Owner"}, headers=auth("owner")))
    ok(client.patch(f"/api/assessments/{aid}/advance-stage", json={}, headers=auth("analyst")))
    return aid


aid = analysed(model_factors("customer data is shared with sanctioned banks"))
assessment = ok(client.get(f"/api/assessments/{aid}", headers=auth("analyst")))
factors = {f["category"]: f for f in ok(client.get(f"/api/assessments/{aid}/risk-factors", headers=auth("analyst")))}

check("after AI analysis there is no score: nothing is rated yet", assessment["overall_score"] is None and assessment["risk_level"] is None, f"{assessment['overall_score']} {assessment['risk_level']}")
check("the model's 99/95 scores were discarded", all(f["score"] == 0.0 for f in factors.values()))

geo = factors["GEOGRAPHIC_RISK"]
check("GEOGRAPHIC_RISK: EVIDENCE_FOUND", geo["evidence_status"] == "EVIDENCE_FOUND")
check("GEOGRAPHIC_RISK: fabricated sanctions quote refused, indicator not stored", geo["indicators"] == ["CROSS_BORDER_CAPABILITY"], str(geo["indicators"]))
check("GEOGRAPHIC_RISK: refusal is on the record", geo["rejected_indicators"][0]["indicator"] == "SANCTIONS_EXPOSURE")
check("GEOGRAPHIC_RISK: verified quote carries source and checksum", any(e["quote_verified"] and e["source_id"] == "FIELD:description" and e["source_checksum"] for e in geo["evidence"]))
check("THIRD_PARTY_VENDOR_RISK: only a fabricated quote -> NOT_VERIFIED", factors["THIRD_PARTY_VENDOR_RISK"]["evidence_status"] == "NOT_VERIFIED")
check("CUSTOMER_SEGMENT_RISK: INSUFFICIENT_EVIDENCE with missing info", factors["CUSTOMER_SEGMENT_RISK"]["evidence_status"] == "INSUFFICIENT_EVIDENCE" and factors["CUSTOMER_SEGMENT_RISK"]["missing_information"])
omitted = factors["TECHNOLOGY_DEVELOPMENT_RISK"]
check("a category the model omitted is applicable and unresolved, not 'no risk'", omitted["applicable"] and omitted["evidence_status"] == "INSUFFICIENT_EVIDENCE")
check("not-applicable categories are NOT_APPLICABLE", factors["OWNERSHIP_ENTITY_COMPLEXITY_RISK"]["evidence_status"] == "NOT_APPLICABLE")
# P4: a cross-border payment product -- the fixed Stage 4 rule puts back the
# product category the model called not applicable, unrated and unresolved.
product = factors["PRODUCT_SERVICE_RISK"]
check(
    "Stage 4 rule: a model 'not applicable' on a required category is overridden, unrated",
    product["applicable"] and product["evidence_status"] == "INSUFFICIENT_EVIDENCE" and product["likelihood"] is None
    and product["rule_triggers"][0]["effect"] == "FORCED_APPLICABLE",
    str(product.get("rule_triggers")),
)

calc = ok(client.get(f"/api/assessments/{aid}/inherent-risk", headers=auth("analyst")))
check("the calculation reports no score, provisional", calc["final_score"] is None and calc["is_provisional"] and calc["risk_band"] == "UNRATED")
check("no rule fired: the fabricated sanctions quote cannot escalate", calc["triggered_rules"] == [])

blocked = client.patch(f"/api/assessments/{aid}/advance-stage", json={}, headers=auth("analyst"))
check("an unrated result cannot advance to Inherent Risk Assessment", blocked.status_code == 400, blocked.text)

rate = lambda fid, l, i: ok(client.patch(f"/api/assessments/{aid}/risk-factors/{fid}/rating", json={"likelihood": l, "impact": i, "rated_by": "Analyst"}, headers=auth("analyst")))
rate(geo["id"], 4, 4)          # 64
rate(factors["DELIVERY_CHANNEL_RISK"]["id"], 3, 3)  # 36
partial = ok(client.get(f"/api/assessments/{aid}", headers=auth("analyst")))
check("partial rating: score from rated factors only, still provisional", partial["overall_score"] == 50.0, str(partial["overall_score"]))
check("partial result still cannot advance", client.patch(f"/api/assessments/{aid}/advance-stage", json={}, headers=auth("analyst")).status_code == 400)

rate(factors["THIRD_PARTY_VENDOR_RISK"]["id"], 2, 2)  # 16
rate(factors["CUSTOMER_SEGMENT_RISK"]["id"], 2, 3)    # 24
ok(client.patch(f"/api/assessments/{aid}/risk-factors/{omitted['id']}/exclude", json={"reason": "No technology change beyond vendor platform, covered under third party.", "excluded_by": "Analyst"}, headers=auth("analyst")))
# P4: the categories the cross-border payment rule put back still need an
# analyst's decision; here the analyst rules them out, with a reason (R4.5).
for category in ("PRODUCT_SERVICE_RISK", "TRANSACTION_ACTIVITY_RISK"):
    check(f"{category} was required by a Stage 4 rule", bool(factors[category]["rule_triggers"]))
    ok(client.patch(f"/api/assessments/{aid}/risk-factors/{factors[category]['id']}/exclude", json={"reason": "Considered under the fixed Stage 4 rule; covered by the geographic rating.", "excluded_by": "Analyst"}, headers=auth("analyst")))
final = ok(client.get(f"/api/assessments/{aid}", headers=auth("analyst")))
check("all resolved: overall = weighted mean of analyst ratings", final["overall_score"] == 35.0 and final["risk_level"] == "LOW", f"{final['overall_score']} {final['risk_level']}")
calc = ok(client.get(f"/api/assessments/{aid}/inherent-risk", headers=auth("analyst")))
check("... and not provisional", not calc["is_provisional"] and calc["final_score"] == 35.0)
check("a fully rated result can advance", client.patch(f"/api/assessments/{aid}/advance-stage", json={}, headers=auth("analyst")).status_code == 200)

# A real quote that is not about sanctions cannot carry SANCTIONS_EXPOSURE:
# it would force CRITICAL on text that never mentions sanctions.
aid_off = analysed(model_factors("customer data is shared with the processor"))
factors_off = {f["category"]: f for f in ok(client.get(f"/api/assessments/{aid_off}/risk-factors", headers=auth("analyst")))}
check("an off-topic quote cannot carry SANCTIONS_EXPOSURE", "SANCTIONS_EXPOSURE" not in factors_off["GEOGRAPHIC_RISK"]["indicators"])
check("... and is referred to a human instead", any("sanctions exposure" in m for m in factors_off["GEOGRAPHIC_RISK"]["missing_information"]))
a_off = ok(client.get(f"/api/assessments/{aid_off}", headers=auth("analyst")))
check("... so no CRITICAL is forced", a_off["risk_level"] is None, str(a_off["risk_level"]))

# A real sanctions quote.
aid2 = analysed(
    model_factors("one settlement bank appears on the sanctions screening list"),
    evidence=(
        "Merchants in Germany receive cross-border settlement; onboarding is "
        "remote and customer data is shared with the processor. During due "
        "diligence, one settlement bank appears on the sanctions screening list."
    ),
)
factors2 = {f["category"]: f for f in ok(client.get(f"/api/assessments/{aid2}/risk-factors", headers=auth("analyst")))}
check("a verified quote keeps the SANCTIONS_EXPOSURE indicator", "SANCTIONS_EXPOSURE" in factors2["GEOGRAPHIC_RISK"]["indicators"])
a2 = ok(client.get(f"/api/assessments/{aid2}", headers=auth("analyst")))
check("the approved sanctions rule sets CRITICAL before any rating", a2["risk_level"] == "CRITICAL" and a2["overall_score"] is None, f"{a2['overall_score']} {a2['risk_level']}")
calc2 = ok(client.get(f"/api/assessments/{aid2}/inherent-risk", headers=auth("analyst")))
check("the fired rule is on the calculation", [r["rule_code"] for r in calc2["triggered_rules"]] == ["SANCTIONS_EXPOSURE_001"] and calc2["mandatory_review"])

db = SessionLocal()
try:
    details = [e.details or "" for e in db.query(AuditEvent).filter(AuditEvent.assessment_id == aid2).all()]
finally:
    db.close()
check("the fired rule is in the audit log", any("SANCTIONS_EXPOSURE_001" in d for d in details))
check("the analysis audit entry summarises evidence verification", any("Evidence verification" in d for d in details))

rules = ok(client.get("/api/risk-methodologies/active/escalation-rules", headers=auth("analyst")))
check(
    "the rule library is readable with statuses",
    {r["rule_code"]: r["status"] for r in rules["rules"]}
    == {
        "SANCTIONS_EXPOSURE_001": "approved",
        "MIN_BAND_KEY_FACTOR_001": "draft",
        "GEO_FATF_CALL_FOR_ACTION_001": "draft",
        "GEO_HIGH_RISK_THIRD_COUNTRY_001": "draft",
    },
)

print(f"\nAll {len(PASSED)} checks passed.")

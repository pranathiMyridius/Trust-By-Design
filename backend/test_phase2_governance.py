"""
Phase 2 acceptance checks: controls vs inherent risk, the residual grid,
versioned methodology, reference data in scoring, and the decision record.

  - the control environment is a mitigant, outside the inherent average
  - residual = grid[inherent band][control rating]; it never exceeds the
    inherent band, and non-mitigable rules hold it at their floor
  - a methodology that has produced a result is locked; revisions are
    clones, and activation records who approved it and why
  - only reviewer-attested reference snapshots feed scoring rules, and
    every calculation records which snapshots it used or held back
  - approval is refused until the decision record is complete, and the
    frozen record detects tampering

Part A exercises the pure functions; part B drives the real API with only
the HTTP call beneath app/ai/metering.py faked. Run from backend/:

    python test_phase2_governance.py
"""

import json
import os
import tempfile

_DB_FILE = os.path.join(tempfile.mkdtemp(), "phase2_test.db")
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
from app.control_engine.scoring import overall_control_rating, rating_for_risk  # noqa: E402
from app.database import SessionLocal  # noqa: E402
from app.main import app, prepare_database  # noqa: E402

# The schema is prepared explicitly; importing the app never migrates.
prepare_database()
from app.models.assessment import Assessment  # noqa: E402
from app.models.decision_record import DecisionRecord  # noqa: E402
from app.models.user import User  # noqa: E402
from app.risk_engine.methodology import config_from_row  # noqa: E402
from app.risk_engine.scoring import (  # noqa: E402
    DEFAULT_ESCALATION_RULES,
    DEFAULT_RESIDUAL_GRID,
    calculate_inherent_risk,
    calculate_residual_risk,
    compute_factor_score,
    methodology_fingerprint,
    residual_position_score,
    validate_residual_grid,
)

PASSED: list[str] = []


def check(name: str, condition: bool, detail: str = "") -> None:
    assert condition, f"FAILED: {name}. {detail}"
    PASSED.append(name)
    print(f"  ok  {name}")


def rated(category, likelihood, impact, **extra):
    return {
        "category": category, "applicable": True, "excluded": False, "rated": True,
        "likelihood": likelihood, "impact": impact,
        "score": compute_factor_score(likelihood, impact), "indicators": [], **extra,
    }


# ===========================================================================
# A. Pure functions
# ===========================================================================
print("\nA1. Control environment is a mitigant")

with_env = calculate_inherent_risk([rated("GEOGRAPHIC_RISK", 2, 2), rated("CONTROL_ENVIRONMENT_RISK", 5, 5)])
check("a rated control-environment factor does not enter the inherent score", with_env["final_score"] == 16.0, str(with_env["final_score"]))
unrated_env = calculate_inherent_risk(
    [rated("GEOGRAPHIC_RISK", 2, 2), {"category": "CONTROL_ENVIRONMENT_RISK", "applicable": True, "excluded": False, "rated": False, "score": 0}]
)
check("an unrated control-environment factor does not make inherent provisional", not unrated_env["is_provisional"])
legacy = calculate_inherent_risk([rated("GEOGRAPHIC_RISK", 2, 2), rated("CONTROL_ENVIRONMENT_RISK", 5, 5)], mitigant_categories=[])
check("with no mitigant categories configured, the old behaviour returns", legacy["final_score"] == 58.0, str(legacy["final_score"]))


print("\nA2. Control rating")

effective = {"operating_effectiveness": "EFFECTIVE", "has_evidence": True, "coverage_complete": True}
check("an evidenced, complete, effective control -> EFFECTIVE", rating_for_risk([effective]) == "EFFECTIVE")
check("an effective control without evidence -> PARTIAL", rating_for_risk([{**effective, "has_evidence": False}]) == "PARTIAL")
check("an unverified control -> WEAK", rating_for_risk([{"operating_effectiveness": "UNVERIFIED"}]) == "WEAK")
check("no controls at all -> WEAK", rating_for_risk([]) == "WEAK")
check("overall rating is the weakest risk's", overall_control_rating(["EFFECTIVE", "PARTIAL", "EFFECTIVE"]) == "PARTIAL")
check("no risks -> no rating", overall_control_rating([]) is None)


print("\nA3. Residual grid")

expected = {
    ("LOW", "WEAK"): "LOW", ("MEDIUM", "WEAK"): "MEDIUM", ("MEDIUM", "EFFECTIVE"): "LOW",
    ("HIGH", "PARTIAL"): "HIGH", ("HIGH", "EFFECTIVE"): "MEDIUM",
    ("CRITICAL", "PARTIAL"): "CRITICAL", ("CRITICAL", "EFFECTIVE"): "HIGH",
}
for (band, rating), residual in expected.items():
    got = calculate_residual_risk(band, rating)["residual_band"]
    check(f"grid {band} x {rating} -> {residual}", got == residual, str(got))

order = ["LOW", "MEDIUM", "HIGH", "CRITICAL"]
check(
    "no default cell ever exceeds its inherent band",
    all(order.index(cell) <= order.index(band) for band, row in DEFAULT_RESIDUAL_GRID["cells"].items() for cell in row.values()),
)
check("the default grid validates", validate_residual_grid(DEFAULT_RESIDUAL_GRID) == [])
raising = json.loads(json.dumps(DEFAULT_RESIDUAL_GRID))
raising["cells"]["MEDIUM"]["WEAK"] = "HIGH"
check("a grid that raises risk is rejected", any("exceeds" in e for e in validate_residual_grid(raising)))
try:
    calculate_residual_risk("MEDIUM", "WEAK", residual_grid=raising)
    raised = False
except AssertionError:
    raised = True
check("a raising grid can never produce a result", raised)

sanctions_rule = {"rule_code": "SANCTIONS_EXPOSURE_001", "min_band": "CRITICAL", "non_mitigable": True}
floored = calculate_residual_risk("CRITICAL", "EFFECTIVE", triggered_rules=[sanctions_rule])
check("a non-mitigable rule holds residual at CRITICAL despite effective controls", floored["grid_band"] == "HIGH" and floored["residual_band"] == "CRITICAL")
check("the floor is recorded", floored["floors_applied"] == [{"rule_code": "SANCTIONS_EXPOSURE_001", "band_before": "HIGH", "band_after": "CRITICAL"}])
mitigable = calculate_residual_risk("CRITICAL", "EFFECTIVE", triggered_rules=[{**sanctions_rule, "non_mitigable": False}])
check("a mitigable rule does not hold residual up", mitigable["residual_band"] == "HIGH")

no_inherent = calculate_residual_risk(None, "EFFECTIVE")
check("no inherent band -> no residual, with a reason", no_inherent["residual_band"] is None and no_inherent["reason"])
no_rating = calculate_residual_risk("HIGH", None)
check("no control rating -> no residual, with a reason", no_rating["residual_band"] is None and no_rating["reason"])

from app.risk_engine.scoring import residual_exceeds_tolerance  # noqa: E402

check("a policy-set CRITICAL residual with no score still exceeds tolerance", residual_exceeds_tolerance(None, "CRITICAL", 60))
check("a policy-set MEDIUM residual does not", not residual_exceeds_tolerance(None, "MEDIUM", 60))
check("a scored residual is compared by score", residual_exceeds_tolerance(65, "HIGH", 60) and not residual_exceeds_tolerance(55, "MEDIUM", 60))
check("indicative residual score sits inside the residual band", residual_position_score(90, 30, "HIGH") == 60.0)
check("... clamped down into the band when controls reduce little", residual_position_score(90, 4, "HIGH") == 79.0)
check("... none without a band", residual_position_score(90, 4, None) is None)


print("\nA4. Jurisdiction rules and fingerprints")

approved_geo = [{**r, "status": "approved"} for r in DEFAULT_ESCALATION_RULES if r["rule_code"].startswith("GEO_")]
iran = [{"iso_code": "IR", "tier": "CALL_FOR_ACTION", "source": "FATF"}]
geo_fired = calculate_inherent_risk([rated("PRODUCT_SERVICE_RISK", 1, 1)], escalation_rules=approved_geo, jurisdiction_matches=iran)
check("an approved call-for-action rule fires on a verified match", geo_fired["risk_band"] == "CRITICAL")
check("... and names the designation that triggered it", geo_fired["triggered_rules"][0]["triggered_by"] == ["IR:CALL_FOR_ACTION (FATF)"])
check("... non-mitigable", geo_fired["triggered_rules"][0]["non_mitigable"])
draft_geo = calculate_inherent_risk([rated("PRODUCT_SERVICE_RISK", 1, 1)], jurisdiction_matches=iran)
check("as shipped (draft) the jurisdiction rules do not fire", draft_geo["risk_band"] == "LOW")
no_match = calculate_inherent_risk([rated("PRODUCT_SERVICE_RISK", 1, 1)], escalation_rules=approved_geo, jurisdiction_matches=[])
check("without verified matches the rule does not fire", no_match["risk_band"] == "LOW")

base = config_from_row(None)
changed = {**base, "residual_grid": raising}
check("the built-in methodology has a stable fingerprint", base["methodology_fingerprint"] == config_from_row(None)["methodology_fingerprint"])
check("any setting change changes the fingerprint", methodology_fingerprint(changed) != base["methodology_fingerprint"])
check("the built-in methodology reports version 'builtin'", base["methodology_version"] == "builtin")


# ===========================================================================
# B. Through the API
# ===========================================================================
print("\nB. End to end")

client = TestClient(app)
PASSWORD = "Passw0rd!"

REQUEST = {
    "title": "Remittance corridor Germany to Iran",
    "change_type": "NEW_GEOGRAPHY",
    "product_or_service_name": "Remit DE-IR",
    "description": "Consumer remittances from Germany with cross-border payout to recipients abroad.",
    "evidence": "Customers are onboarded remotely and send funds cross-border.",
    "business_owner": "Payments",
    "legal_entity": "Bank DE GmbH",
    "customer_segment": "Retail",
    "countries_jurisdictions": "Germany, Iran",
    "delivery_channels": "Mobile app",
    "expected_transaction_volume": "10k/month",
    "expected_transaction_value": "EUR 2m/month",
    "transaction_types": "Remittance",
    "third_party_vendor_usage": "Payout partner",
    "technology_process_changes": "New payout integration",
    "expected_launch_date": "2027-03-01",
}

FACTORS = [
    {"category": c, "applicable": False, "rationale": "Does not apply."}
    for c in ["PRODUCT_SERVICE_RISK", "CUSTOMER_SEGMENT_RISK", "DELIVERY_CHANNEL_RISK", "TRANSACTION_ACTIVITY_RISK",
              "TECHNOLOGY_DEVELOPMENT_RISK", "THIRD_PARTY_VENDOR_RISK", "OWNERSHIP_ENTITY_COMPLEXITY_RISK",
              "FINANCIAL_CRIME_TYPOLOGY_RISK"]
] + [
    {
        "category": "GEOGRAPHIC_RISK", "applicable": True, "indicators": ["CROSS_BORDER_CAPABILITY"],
        "evidence": [{"source_id": "FIELD:description", "quote": "cross-border payout to recipients abroad", "indicator": "CROSS_BORDER_CAPABILITY"}],
        "rationale": "Cross-border payout.",
    },
    {
        "category": "CONTROL_ENVIRONMENT_RISK", "applicable": True,
        "evidence": [{"source_id": "FIELD:evidence", "quote": "Customers are onboarded remotely"}],
        "rationale": "Remote onboarding controls are new.",
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


metering.requests.post = lambda *a, **k: _Response({
    "model": "test/model",
    "choices": [{"message": {"content": json.dumps({"factors": FACTORS})}}],
    "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
})


def _users() -> None:
    db = SessionLocal()
    try:
        manager = User(email="manager@test.io", hashed_password=hash_password(PASSWORD), full_name="Manager", role="MANAGER")
        db.add(manager)
        db.flush()
        for email, role in [("owner@test.io", "BUSINESS_USER"), ("analyst@test.io", "FCRM_ANALYST"), ("committee@test.io", "COMMITTEE_MEMBER")]:
            db.add(User(email=email, hashed_password=hash_password(PASSWORD), full_name=email.split("@")[0].title(), role=role, manager_id=manager.id))
        db.commit()
    finally:
        db.close()


_users()
_TOKENS: dict[str, str] = {}


def auth(who: str) -> dict:
    if who not in _TOKENS:
        email, password = ("admin@example.com", "ChangeMe123!") if who == "admin" else (f"{who}@test.io", PASSWORD)
        response = client.post("/api/auth/login", json={"email": email, "password": password})
        assert response.status_code == 200, response.text
        _TOKENS[who] = response.json()["access_token"]
    return {"Authorization": f"Bearer {_TOKENS[who]}"}


def ok(response, status=200):
    assert response.status_code == status, f"{response.status_code}: {response.text}"
    return response.json()


print("\nB1. Reference data snapshots")

loaded = ok(client.post("/api/reference-data/reload", headers=auth("admin")))
check("bundled snapshots load as new snapshots", {r["source"] for r in loaded if not r["unchanged"]} == {"FATF", "EU"})
snapshots = {s["source"]: s for s in ok(client.get("/api/reference-data/snapshots", headers=auth("analyst")))}
check("a snapshot records its checksum", snapshots["FATF"]["checksum"].startswith("sha256:"))
check("the EU file claims to be verified, and that claim is recorded", snapshots["EU"]["source_claims_verified"])
check("... but a file's own claim does not attest it", not snapshots["EU"]["attested"] and not snapshots["EU"]["used_in_scoring"])
check("the FATF file is not verified by its own account either", not snapshots["FATF"]["source_claims_verified"])
check("attesting needs an admin", client.post(f"/api/reference-data/snapshots/{snapshots['FATF']['id']}/attest", json={"note": "x" * 30}, headers=auth("analyst")).status_code == 403)
check("attesting needs a real note", client.post(f"/api/reference-data/snapshots/{snapshots['FATF']['id']}/attest", json={"note": "ok"}, headers=auth("admin")).status_code == 422)


print("\nB2. A versioned methodology with an approved jurisdiction rule")

v1 = ok(client.post("/api/risk-methodologies", json={"name": "FCRM", "weights": {"ALL": 1.0}, "thresholds": {"CRITICAL": 80, "HIGH": 60, "MEDIUM": 40}}, headers=auth("admin")), 201)
check("a new methodology is version 1, unlocked", v1["version"] == 1 and not v1["locked"])
rules = ok(client.get("/api/risk-methodologies/active/escalation-rules", headers=auth("analyst")))["rules"]
for rule in rules:
    if rule["rule_code"] == "GEO_FATF_CALL_FOR_ACTION_001":
        rule["status"] = "approved"
ok(client.put(f"/api/risk-methodologies/{v1['id']}/escalation-rules", json={"rules": rules, "reason": "Compliance approved the FATF black-list override."}, headers=auth("admin")))
check("activation requires a reason", client.patch(f"/api/risk-methodologies/{v1['id']}/activate", json={}, headers=auth("admin")).status_code == 422)
check("activation requires an admin", client.patch(f"/api/risk-methodologies/{v1['id']}/activate", json={"reason": "go"}, headers=auth("analyst")).status_code == 403)
active = ok(client.patch(f"/api/risk-methodologies/{v1['id']}/activate", json={"reason": "Approved at risk committee 2026-09."}, headers=auth("admin")))
check("activation records the approver and reason", active["approved_by"] and active["approval_reason"] == "Approved at risk committee 2026-09.")


def analysed() -> int:
    aid = ok(client.post("/api/assessments", json={**REQUEST, "is_draft": False}, headers=auth("owner")), 201)["id"]
    ok(client.patch(f"/api/assessments/{aid}/advance-stage", json={}, headers=auth("owner")))
    client.patch(f"/api/assessments/{aid}/advance-stage", json={}, headers=auth("analyst"))
    ok(client.post(f"/api/assessments/{aid}/intelligence/confirm", json={"confirmed_by": "Owner"}, headers=auth("owner")))
    ok(client.patch(f"/api/assessments/{aid}/advance-stage", json={}, headers=auth("analyst")))
    return aid


aid = analysed()
factors = {f["category"]: f for f in ok(client.get(f"/api/assessments/{aid}/risk-factors", headers=auth("analyst")))}
rate = lambda fid, l, i: ok(client.patch(f"/api/assessments/{aid}/risk-factors/{fid}/rating", json={"likelihood": l, "impact": i, "rated_by": "Analyst"}, headers=auth("analyst")))
rate(factors["GEOGRAPHIC_RISK"]["id"], 2, 2)
rate(factors["CONTROL_ENVIRONMENT_RISK"]["id"], 5, 5)
calc = ok(client.get(f"/api/assessments/{aid}/inherent-risk", headers=auth("analyst")))
# P4: the fixed Stage 4 rules also put categories on this cross-border
# request; they are unrated, so they don't score. What matters here is that
# the control-environment factor is never an input, however it is rated.
rated_inputs = [item for item in calc["inputs"] if item["rated"]]
check(
    "the control-environment rating stays out of inherent risk",
    calc["final_score"] == 16.0
    and [item["category"] for item in rated_inputs] == ["GEOGRAPHIC_RISK"]
    and all(item["category"] != "CONTROL_ENVIRONMENT_RISK" for item in calc["inputs"]),
    f"{calc['final_score']} {calc['inputs']}",
)
check("the calculation records methodology v1 and its fingerprint", calc["methodology_version"] == "v1" and calc["methodology_fingerprint"].startswith("sha256:"))
check("an unattested FATF snapshot does not score", calc["triggered_rules"] == [] and calc["risk_band"] == "LOW")
skipped = {s["source"]: s for s in calc["reference_data"]["snapshots_skipped"]}
check("... and what it held back is on record", "IR:CALL_FOR_ACTION" in skipped["FATF"]["would_match"], str(skipped))

locked = client.put(f"/api/risk-methodologies/{v1['id']}/escalation-rules", json={"rules": rules, "reason": "tweak"}, headers=auth("admin"))
check("a methodology that produced a result is locked", locked.status_code == 409, locked.text)

attested = ok(client.post(f"/api/reference-data/snapshots/{snapshots['FATF']['id']}/attest", json={"note": "Checked against the FATF plenary statement on fatf-gafi.org."}, headers=auth("admin")))
check("attestation records the reviewer", attested["attested"] and attested["attested_by"] and attested["used_in_scoring"])
rate(factors["GEOGRAPHIC_RISK"]["id"], 2, 2)  # recalculate
calc = ok(client.get(f"/api/assessments/{aid}/inherent-risk", headers=auth("analyst")))
check("once attested, the approved rule fires: CRITICAL", calc["risk_band"] == "CRITICAL" and [r["rule_code"] for r in calc["triggered_rules"]] == ["GEO_FATF_CALL_FOR_ACTION_001"])
check("the jurisdiction match names its snapshot", any(m["iso_code"] == "IR" and m["snapshot_id"] == snapshots["FATF"]["id"] for m in calc["jurisdiction_matches"]))
check("the snapshot used is on record with its checksum", any(s["source"] == "FATF" and s["checksum"] == snapshots["FATF"]["checksum"] for s in calc["reference_data"]["snapshots_used"]))

again = ok(client.post("/api/reference-data/reload", headers=auth("admin")))
check("reloading unchanged content keeps the attestation", all(r["unchanged"] for r in again) and ok(client.get("/api/reference-data/snapshots", headers=auth("analyst")))[0]["id"] in {snapshots["EU"]["id"], snapshots["FATF"]["id"]})


print("\nB3. Revising a methodology by clone")

v2 = ok(client.post(f"/api/risk-methodologies/{v1['id']}/clone", json={"reason": "Tighten the residual grid."}, headers=auth("admin")), 201)
check("a clone is the next version, unlocked, inactive, linked to its parent", v2["version"] == 2 and not v2["locked"] and not v2["is_active"] and v2["parent_id"] == v1["id"])
check("a clone carries the same settings until changed", v2["fingerprint"] == active["fingerprint"])
bad_grid = json.loads(json.dumps(DEFAULT_RESIDUAL_GRID))
bad_grid["cells"]["LOW"]["WEAK"] = "MEDIUM"
check("a residual grid that raises risk is refused", client.put(f"/api/risk-methodologies/{v2['id']}/residual-grid", json={"residual_grid": bad_grid, "reason": "x"}, headers=auth("admin")).status_code == 400)
tighter = json.loads(json.dumps(DEFAULT_RESIDUAL_GRID))
tighter["version"] = "1.1"
tighter["cells"]["MEDIUM"]["EFFECTIVE"] = "MEDIUM"
ok(client.put(f"/api/risk-methodologies/{v2['id']}/residual-grid", json={"residual_grid": tighter, "reason": "Effective controls no longer take MEDIUM to LOW."}, headers=auth("admin")))
ok(client.patch(f"/api/risk-methodologies/{v2['id']}/activate", json={"reason": "Grid v1.1 approved."}, headers=auth("admin")))
listing = {m["id"]: m for m in ok(client.get("/api/risk-methodologies", headers=auth("admin")))}
check("activating v2 retires v1", not listing[v1["id"]]["is_active"] and listing[v1["id"]]["retired_at"] and listing[v2["id"]]["is_active"])
check("v1 stays exactly as it was", listing[v1["id"]]["fingerprint"] == active["fingerprint"])


print("\nB4. Residual risk frozen through the grid")

preview = ok(client.get(f"/api/assessments/{aid}/residual-risk", headers=auth("analyst")))
check("before the residual stage, residual is a preview", preview["frozen"] is False)
# P4: categories the fixed Stage 4 rules required are still unrated; the
# analyst rules them out with a reason (R4.5) so the case can advance.
for factor in ok(client.get(f"/api/assessments/{aid}/risk-factors", headers=auth("analyst"))):
    if factor["applicable"] and not factor["excluded"] and factor["likelihood"] is None and factor["rule_triggers"]:
        ok(client.patch(f"/api/assessments/{aid}/risk-factors/{factor['id']}/exclude", json={"reason": "Considered under the fixed Stage 4 rule; not material for this change.", "excluded_by": "Analyst"}, headers=auth("analyst")))
ok(client.patch(f"/api/assessments/{aid}/advance-stage", json={}, headers=auth("analyst")))  # -> INHERENT_RISK_ASSESSMENT
ok(client.patch(f"/api/assessments/{aid}/advance-stage", json={}, headers=auth("analyst")))  # -> CONTROL_ASSESSMENT
ok(client.patch(f"/api/assessments/{aid}/challenge", json={"outcome": "ACCEPTED", "comment": "Controls reviewed."}, headers=auth("analyst")))
moved = ok(client.patch(f"/api/assessments/{aid}/advance-stage", json={}, headers=auth("analyst")))  # -> RESIDUAL_RISK
check("the assessment reaches Residual Risk", moved["status"] == "RESIDUAL_RISK", moved["status"])
residual = ok(client.get(f"/api/assessments/{aid}/residual-risk", headers=auth("analyst")))
check("residual is frozen", residual["frozen"] is True)
check("no controls -> WEAK", residual["control_rating"] == "WEAK", str(residual["control_rating"]))
check("CRITICAL x WEAK -> CRITICAL", residual["residual_band"] == "CRITICAL")
check("the control-environment factor is not rated as a risk needing controls", all(r["category"] != "CONTROL_ENVIRONMENT_RISK" for r in residual["control_ratings"]))
check("the frozen result names methodology v2 and grid v1.1", residual["methodology_version"] == "v2" and residual["grid_version"] == "1.1")
record_assessment = ok(client.get(f"/api/assessments/{aid}", headers=auth("analyst")))
check("the assessment's residual level of record is the grid result", record_assessment["residual_risk_level"] == "CRITICAL")
check(
    "a rule-set band carries no misleading number (16 inherent must not print as 80 residual)",
    residual["residual_score"] is None and record_assessment["residual_score"] is None,
    str(residual["residual_score"]),
)


print("\nB5. The decision record")

readiness = ok(client.get(f"/api/assessments/{aid}/decision-record/readiness", headers=auth("analyst")))
check("a fully governed assessment is ready for a decision record", readiness["ready"], str(readiness["missing"]))

from app.services.decision_record_service import freeze_decision_record, verify_record  # noqa: E402
from datetime import datetime, timezone  # noqa: E402

db = SessionLocal()
try:
    row = freeze_decision_record(db, db.get(Assessment, aid), {"decision": "APPROVED", "decided_by": "Committee", "decided_at": datetime.now(timezone.utc)})
    db.commit()
    record = row.get_record()
    check("the record carries methodology version and fingerprint", record["methodology"]["version"] == "v2" and record["methodology"]["fingerprint"])
    check("the record carries the reference snapshots used", any(s["source"] == "FATF" for s in record["reference_data"]["snapshots_used"]))
    geo = next(f for f in record["factors"] if f["category"] == "GEOGRAPHIC_RISK")
    check("the record carries ratings, indicators and verified evidence", geo["likelihood"] == 2 and geo["accepted_indicators"] == ["CROSS_BORDER_CAPABILITY"] and geo["evidence"][0]["quote_verified"])
    check("the record carries the triggered rules", record["inherent"]["triggered_rules"][0]["rule_code"] == "GEO_FATF_CALL_FOR_ACTION_001")
    check("the record carries controls and residual", record["residual"]["control_rating"] == "WEAK" and record["residual"]["residual_band"] == "CRITICAL")
    check("the record carries the AI model used", record["ai"]["risk_identification_model"] == "test/model")
    check("an intact record verifies", verify_record(row))
    row.record = row.record.replace('"CRITICAL"', '"LOW"')
    check("a tampered record fails verification", not verify_record(row))
    db.rollback()
finally:
    db.close()

# An assessment fast-forwarded to committee with nothing on record.
bare = ok(client.post("/api/assessments", json={**REQUEST, "title": "Bare", "is_draft": False}, headers=auth("owner")), 201)["id"]
db = SessionLocal()
try:
    from app.services import workflow  # noqa: E402

    assessment = db.get(Assessment, bare)
    assessment.status = "READY_FOR_COMMITTEE"
    assessment.workflow_status = workflow.derive_workflow_status(db, assessment)
    db.commit()
finally:
    db.close()
refused = client.post(f"/api/assessments/{bare}/committee-decision", json={"decision": "approve", "rationale": "Looks fine."}, headers=auth("committee"))
check("approval is refused without a complete decision record", refused.status_code == 400, refused.text)
missing = refused.json()["detail"]["missing"]
check("the refusal lists what is missing", any("inherent-risk" in m for m in missing) and any("Residual" in m for m in missing), str(missing))
db = SessionLocal()
try:
    check("a refused approval writes no record and leaves the status alone", db.query(DecisionRecord).filter(DecisionRecord.assessment_id == bare).count() == 0 and db.get(Assessment, bare).status == "READY_FOR_COMMITTEE")
finally:
    db.close()

print(f"\nAll {len(PASSED)} checks passed.")

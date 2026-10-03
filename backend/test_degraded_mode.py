"""
Degraded-mode (AI outage) acceptance checks.

The behaviour under test is the one that used to be wrong: when the AI
provider failed, the pipeline wrote all ten risk categories back as
`applicable=False, score=0.0, severity="LOW"`, which reads -- to an
analyst and to the scoring code alike -- as a genuine low-risk finding.

These checks exercise the real analyzer, the real deterministic
RiskEngine and the real persistence path; only the HTTP layer beneath
app/ai/metering.py is faked, so each failure mode (timeout, 429,
malformed JSON) is injected exactly where a real one would occur.

Graceful-degradation paths are the ones that rarely run and therefore
rarely get tested, which is why they are worth testing on purpose. Run
from backend/:

    python test_degraded_mode.py
"""

import json
import os
import tempfile

_DB_FILE = os.path.join(tempfile.mkdtemp(), "degraded_test.db")
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
# Keep the bounded retry from making these checks slow: one attempt is
# enough to prove the classification and the fallback.
os.environ["OPENROUTER_MAX_ATTEMPTS"] = "1"
os.environ["OPENROUTER_BACKOFF_SECONDS"] = "0"

import requests  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

import app.ai.metering as metering  # noqa: E402
import app.langgraph.nodes as nodes  # noqa: E402
from app.auth.security import hash_password  # noqa: E402
from app.database import SessionLocal  # noqa: E402
from app.main import app, prepare_database  # noqa: E402

# The schema is prepared explicitly; importing the app never migrates.
prepare_database()
from app.models.assessment import Assessment  # noqa: E402
from app.models.audit_event import AuditEvent  # noqa: E402
from app.models.risk_factor import RiskFactor  # noqa: E402
from app.models.user import User  # noqa: E402
from app.risk_engine.degraded import (  # noqa: E402
    DIMENSION_TO_CATEGORY,
    AiStatus,
    AssessmentMode,
    ScoreSource,
)
from app.risk_engine.scoring import calculate_inherent_risk  # noqa: E402

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


# ---------------------------------------------------------------------------
# Fake provider responses. `_transport` is swapped per test to inject the
# failure mode under examination.
# ---------------------------------------------------------------------------

GOOD_FACTORS = [
    {
        "category": "GEOGRAPHIC_RISK",
        "applicable": True,
        "indicators": ["CROSS_BORDER_CAPABILITY"],
        "rationale": "Cross-border settlement between Germany and Poland.",
        "score": 70,
        "severity": "HIGH",
    },
    {
        "category": "THIRD_PARTY_VENDOR_RISK",
        "applicable": True,
        "indicators": ["THIRD_PARTY_DEPENDENCIES"],
        "rationale": "Settlement depends on an external acquiring processor.",
        "score": 60,
        "severity": "HIGH",
    },
]


class _Response:
    def __init__(self, status_code: int, body, text: str | None = None):
        self.status_code = status_code
        self._body = body
        self.text = text if text is not None else json.dumps(body)
        self.content = self.text.encode()

    def json(self):
        if self._body is None:
            raise ValueError("no json")
        return self._body


def _chat(content: str) -> _Response:
    return _Response(
        200,
        {
            "model": "test-vendor/test-model-1",
            "choices": [{"message": {"content": content}}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 10, "total_tokens": 20},
        },
    )


def ai_success(*_a, **_k):
    return _chat(json.dumps({"factors": GOOD_FACTORS}))


def ai_timeout(*_a, **_k):
    raise requests.Timeout("simulated provider timeout")


def ai_rate_limited(*_a, **_k):
    return _Response(429, {"error": "rate limit exceeded"})


def ai_malformed(*_a, **_k):
    return _chat("Sure! Here is your risk analysis: <not json at all>")


_transport = ai_success


def _dispatch(url, headers=None, json=None, timeout=None, **kwargs):
    return _transport(url, headers=headers, json=json, timeout=timeout, **kwargs)


metering.requests.post = _dispatch


# ---------------------------------------------------------------------------
# Users and helpers
# ---------------------------------------------------------------------------

def _users() -> dict:
    db = SessionLocal()
    try:
        def add(email, role, manager_id=None):
            user = User(
                email=email,
                hashed_password=hash_password(PASSWORD),
                full_name=email.split("@")[0].title(),
                role=role,
                manager_id=manager_id,
            )
            db.add(user)
            db.flush()
            return user.id

        manager = add("manager@test.io", "MANAGER")
        ids = {
            "manager": manager,
            "owner": add("owner@test.io", "BUSINESS_USER", manager),
            "analyst": add("analyst@test.io", "FCRM_ANALYST", manager),
        }
        db.commit()
        return ids
    finally:
        db.close()


USERS = _users()
_TOKENS: dict[str, str] = {}


def auth(who: str) -> dict:
    if who not in _TOKENS:
        email, password = (
            ("admin@example.com", "ChangeMe123!")
            if who == "admin"
            else (f"{who}@test.io", PASSWORD)
        )
        response = client.post(
            "/api/auth/login", json={"email": email, "password": password}
        )
        assert response.status_code == 200, response.text
        _TOKENS[who] = response.json()["access_token"]
    return {"Authorization": f"Bearer {_TOKENS[who]}"}


def ok(response, status=200):
    assert response.status_code == status, f"{response.status_code}: {response.text}"
    return response.json()


def analysed_assessment(transport) -> dict:
    """
    Create -> intake -> confirm profile -> run risk identification with
    `transport` standing in for the AI provider. Returns the assessment
    as the API reports it afterwards.
    """

    global _transport

    created = ok(
        client.post(
            "/api/assessments",
            json={**REQUEST, "is_draft": False},
            headers=auth("owner"),
        ),
        201,
    )
    aid = created["id"]

    ok(client.patch(f"/api/assessments/{aid}/advance-stage", json={}, headers=auth("owner")))
    client.patch(f"/api/assessments/{aid}/advance-stage", json={}, headers=auth("analyst"))
    ok(
        client.post(
            f"/api/assessments/{aid}/intelligence/confirm",
            json={"confirmed_by": "Owner"},
            headers=auth("owner"),
        )
    )

    previous, _transport = _transport, transport
    try:
        ok(
            client.patch(
                f"/api/assessments/{aid}/advance-stage", json={}, headers=auth("analyst")
            )
        )
    finally:
        _transport = previous

    return ok(client.get(f"/api/assessments/{aid}", headers=auth("analyst")))


def factors_of(aid: int) -> list[RiskFactor]:
    db = SessionLocal()
    try:
        return (
            db.query(RiskFactor)
            .filter(
                RiskFactor.assessment_id == aid,
                RiskFactor.is_current.is_(True),
            )
            .all()
        )
    finally:
        db.close()


def audit_actions(aid: int) -> list[str]:
    db = SessionLocal()
    try:
        return [
            event.action
            for event in db.query(AuditEvent)
            .filter(AuditEvent.assessment_id == aid)
            .all()
        ]
    finally:
        db.close()


PASSED: list[str] = []


def check(name: str, condition: bool, detail: str = "") -> None:
    assert condition, f"FAILED: {name}. {detail}"
    PASSED.append(name)
    print(f"  ok  {name}")


# ---------------------------------------------------------------------------
# 1. The AI answers normally.
# ---------------------------------------------------------------------------
print("\n1. AI available")

normal = analysed_assessment(ai_success)

check(
    "a successful run is ai_assisted",
    normal["assessment_mode"] == AssessmentMode.AI_ASSISTED,
    str(normal.get("assessment_mode")),
)
check("ai_status is success", normal["ai_status"] == AiStatus.SUCCESS)
check("score_source is ai_and_rules", normal["score_source"] == ScoreSource.AI_AND_RULES)
check("a successful run is not provisional", normal["analysis_is_provisional"] is False)
check("a successful run needs no acknowledgement", normal["requires_human_review"] is False)
check("a successful run has no degraded_reason", normal["degraded_reason"] is None)
# The AI rates nothing: until an analyst rates the factors there is no
# score, rather than one made of the model's numbers.
check(
    "a successful run leaves the score to the analyst",
    normal["overall_score"] is None and normal["risk_level"] is None,
    f"{normal.get('overall_score')} / {normal.get('risk_level')}",
)
check(
    "AI factors carry a deterministic evidence status",
    all(factor.evidence_status for factor in factors_of(normal["id"])),
)


# ---------------------------------------------------------------------------
# 2. The provider times out.
# ---------------------------------------------------------------------------
print("\n2. Provider timeout -> rules-only")

timed_out = analysed_assessment(ai_timeout)
aid = timed_out["id"]

check(
    "a timeout falls back to rules_only",
    timed_out["assessment_mode"] == AssessmentMode.RULES_ONLY,
    str(timed_out.get("assessment_mode")),
)
check("the timeout is classified as such", timed_out["ai_status"] == AiStatus.TIMEOUT)
check(
    "the score is attributed to the rule engine",
    timed_out["score_source"] == ScoreSource.DETERMINISTIC_RULES,
)
check("a rules-only result is provisional", timed_out["analysis_is_provisional"] is True)
check("a rules-only result needs review", timed_out["requires_human_review"] is True)
check(
    "the user-facing reason says nothing technical",
    "AI analysis was temporarily unavailable" in (timed_out["degraded_reason"] or "")
    and "openrouter" not in (timed_out["degraded_reason"] or "").lower()
    and "timeout" not in (timed_out["degraded_reason"] or "").lower(),
    timed_out.get("degraded_reason"),
)
check(
    "the technical code is kept separately for admins",
    timed_out["technical_error_code"] == "AI_TIMEOUT",
    str(timed_out.get("technical_error_code")),
)

all_factors = factors_of(aid)
# P4: a fixed Stage 4 rule may add a category the rule engine can't reach
# (here the cross-border payment rule adds PRODUCT_SERVICE_RISK). Such a
# factor is applicable, unrated and unresolved -- never a zero reading --
# and the category is still reported as not evaluated by the engine.
rule_added = [
    factor for factor in all_factors
    if any(trigger.get("effect") == "ADDED" for trigger in factor.get_rule_triggers())
]
rules_factors = [factor for factor in all_factors if factor not in rule_added]
check(
    "a factor added by a fixed Stage 4 rule is applicable, unrated and unresolved",
    all(
        factor.applicable and factor.likelihood is None and factor.evidence_status == "INSUFFICIENT_EVIDENCE"
        for factor in rule_added
    ),
    str([(factor.category, factor.evidence_status) for factor in rule_added]),
)
check("the rule engine produced factors", len(rules_factors) > 0)
check(
    "rules-only factors are recorded as RULES, not AI",
    all(factor.source == "RULES" for factor in rules_factors),
    str([factor.source for factor in rules_factors]),
)
check(
    "every rules-only factor maps to a canonical category",
    all(
        factor.category in set(DIMENSION_TO_CATEGORY.values())
        for factor in rules_factors
    ),
)

# The heart of it: an AI failure must not produce zeroes.
check(
    "no category is silently written as a zero score",
    all(factor.score > 0 for factor in rules_factors),
    str([(factor.category, factor.score) for factor in rules_factors]),
)
check(
    "no category is silently written as not-applicable",
    all(factor.applicable for factor in rules_factors),
)
check(
    "a rules-only run never yields a false zero: no score until rated",
    timed_out["overall_score"] is None,
    str(timed_out.get("overall_score")),
)
check(
    "rules-only factors are flagged as not evidence-verified",
    all(factor.evidence_status == "NOT_VERIFIED" for factor in rules_factors),
)
check(
    "categories the rules cannot reach are reported, not zeroed",
    set(timed_out["unevaluated_categories"])
    == {
        factor
        for factor in [
            "PRODUCT_SERVICE_RISK",
            "OWNERSHIP_ENTITY_COMPLEXITY_RISK",
            "FINANCIAL_CRIME_TYPOLOGY_RISK",
        ]
    },
    str(timed_out["unevaluated_categories"]),
)
check(
    "no factor row exists for an unevaluated category (other than one a Stage 4 rule requires)",
    not (
        {factor.category for factor in rules_factors}
        & set(timed_out["unevaluated_categories"])
    ),
)
check(
    "the degradation is on the audit trail",
    "AI_ANALYSIS_DEGRADED" in audit_actions(aid),
    str(audit_actions(aid)),
)


# ---------------------------------------------------------------------------
# 3. The provider rate-limits.
# ---------------------------------------------------------------------------
print("\n3. Provider rate limit -> rules-only")

limited = analysed_assessment(ai_rate_limited)

check(
    "a 429 falls back to rules_only",
    limited["assessment_mode"] == AssessmentMode.RULES_ONLY,
)
check(
    "a 429 is classified as rate_limited",
    limited["ai_status"] == AiStatus.RATE_LIMITED,
    str(limited.get("ai_status")),
)
check(
    "the rate limit is on the audit trail",
    "AI_ANALYSIS_DEGRADED" in audit_actions(limited["id"]),
)


# ---------------------------------------------------------------------------
# 4. The provider answers with something unparseable.
# ---------------------------------------------------------------------------
print("\n4. Malformed model response -> rules-only")

malformed = analysed_assessment(ai_malformed)

check(
    "malformed JSON falls back to rules_only",
    malformed["assessment_mode"] == AssessmentMode.RULES_ONLY,
)
check(
    "malformed JSON is classified as invalid_response",
    malformed["ai_status"] == AiStatus.INVALID_RESPONSE,
    str(malformed.get("ai_status")),
)
check(
    "a malformed response still yields rule-engine factors, not a false zero",
    malformed["overall_score"] is None and len(factors_of(malformed["id"])) > 0,
)


# ---------------------------------------------------------------------------
# 5. The AI fails AND the rule engine fails -> unavailable.
# ---------------------------------------------------------------------------
print("\n5. Both paths fail -> unavailable")


class _BrokenEngine:
    def assess(self, *_a, **_k):
        raise RuntimeError("simulated rule-engine failure")


real_engine = nodes.RiskEngine
nodes.RiskEngine = _BrokenEngine
try:
    both_failed = analysed_assessment(ai_timeout)
finally:
    nodes.RiskEngine = real_engine

check(
    "a broken fallback yields unavailable, not a rating",
    both_failed["assessment_mode"] == AssessmentMode.UNAVAILABLE,
    str(both_failed.get("assessment_mode")),
)
check(
    "no risk rating is recorded",
    both_failed["overall_score"] is None and both_failed["risk_level"] is None,
    f"{both_failed.get('overall_score')} / {both_failed.get('risk_level')}",
)
check("no risk factors are recorded", factors_of(both_failed["id"]) == [])
check(
    "score_source says not_available",
    both_failed["score_source"] == ScoreSource.NOT_AVAILABLE,
)
check(
    "the failure is on the audit trail",
    "ANALYSIS_UNAVAILABLE" in audit_actions(both_failed["id"]),
    str(audit_actions(both_failed["id"])),
)
check(
    "an unavailable assessment cannot advance",
    client.patch(
        f"/api/assessments/{both_failed['id']}/advance-stage",
        json={},
        headers=auth("analyst"),
    ).status_code
    == 400,
)


# ---------------------------------------------------------------------------
# 6. A non-provider failure must NOT be answered with the rules fallback.
# ---------------------------------------------------------------------------
print("\n6. Non-provider failure -> unavailable, fallback not used")


def _broken_analyzer(*_a, **_k):
    # Stands in for corrupt assessment data or an invalid methodology
    # configuration: not something a deterministic rule engine can
    # compensate for.
    raise KeyError("methodology config is missing 'risk_bands'")


real_analyzer = nodes.identify_risk_factors
nodes.identify_risk_factors = _broken_analyzer
try:
    bad_input = analysed_assessment(ai_success)
finally:
    nodes.identify_risk_factors = real_analyzer

check(
    "a non-AI failure is unavailable, not rules_only",
    bad_input["assessment_mode"] == AssessmentMode.UNAVAILABLE,
    str(bad_input.get("assessment_mode")),
)
check(
    "it is recorded as an unexpected failure",
    bad_input["technical_error_code"] == "UNEXPECTED_ANALYSIS_FAILURE",
    str(bad_input.get("technical_error_code")),
)
check("it produces no factors", factors_of(bad_input["id"]) == [])


# ---------------------------------------------------------------------------
# 7. A rules-only result cannot be finalized without acknowledgement.
# ---------------------------------------------------------------------------
print("\n7. Reviewer acknowledgement gate")

blocked = client.patch(
    f"/api/assessments/{aid}/advance-stage", json={}, headers=auth("analyst")
)
check(
    "a provisional result is blocked from advancing",
    blocked.status_code == 400,
    blocked.text,
)
check(
    "the block explains itself",
    "acknowledge" in blocked.text.lower(),
    blocked.text,
)

acknowledged = ok(
    client.post(
        f"/api/assessments/{aid}/acknowledge-degraded",
        json={"note": "Reviewed the rules-only output; accepted as provisional."},
        headers=auth("analyst"),
    )
)
check(
    "the acknowledgement records who",
    acknowledged["degraded_acknowledged_by"] == "Analyst",
    str(acknowledged.get("degraded_acknowledged_by")),
)
check(
    "acknowledging does not erase that the run was degraded",
    acknowledged["requires_human_review"] is True
    and acknowledged["assessment_mode"] == AssessmentMode.RULES_ONLY,
)
check(
    "the acknowledgement is on the audit trail",
    "DEGRADED_RESULT_ACKNOWLEDGED" in audit_actions(aid),
)

after = client.patch(
    f"/api/assessments/{aid}/advance-stage", json={}, headers=auth("analyst")
)
check(
    "the degraded gate no longer blocks after acknowledgement",
    "acknowledge" not in after.text.lower(),
    after.text,
)


# ---------------------------------------------------------------------------
# 8. Hard-floor escalation still applies to a rules-only result.
# ---------------------------------------------------------------------------
print("\n8. Escalation floors survive the fallback")

floored = calculate_inherent_risk(
    factors=[
        {
            "category": "GEOGRAPHIC_RISK",
            "applicable": True,
            "excluded": False,
            "score": 10.0,
            "rated": True,
            "indicators": [],
        },
        {
            "category": "THIRD_PARTY_VENDOR_RISK",
            "applicable": True,
            "excluded": False,
            "score": 90.0,
            "rated": True,
            "indicators": ["SANCTIONS_EXPOSURE"],
        },
    ],
    escalation_rules=[
        {
            "id": "SANCTIONS",
            "indicator": "SANCTIONS_EXPOSURE",
            "min_band": "CRITICAL",
            "description": "Sanctions exposure forces a critical rating.",
        }
    ],
)
check(
    "a mandatory escalation overrides the averaged band",
    floored["escalated"] and floored["risk_band"] == "CRITICAL",
    str(floored),
)


# ---------------------------------------------------------------------------
# 9. The API contract needs no string matching.
# ---------------------------------------------------------------------------
print("\n9. Explicit contract")

for field in (
    "assessment_mode",
    "ai_status",
    "score_source",
    "analysis_is_provisional",
    "requires_human_review",
    "degraded_reason",
    "unevaluated_categories",
):
    check(f"the response exposes {field}", field in timed_out, str(timed_out.keys()))

check(
    "degradation is detectable without reading any rationale text",
    timed_out["assessment_mode"] != AssessmentMode.AI_ASSISTED,
)


# ---------------------------------------------------------------------------
# 10. Admin observability.
# ---------------------------------------------------------------------------
print("\n10. Observability")

reliability = ok(client.get("/api/system/ai-reliability", headers=auth("admin")))
check(
    "rules-only runs are counted",
    reliability["analyses"]["rules_only"] >= 3,
    str(reliability["analyses"]),
)
check(
    "unavailable runs are counted",
    reliability["analyses"]["unavailable"] >= 2,
    str(reliability["analyses"]),
)
check(
    "the fallback rate is reported",
    reliability["analyses"]["rules_only_rate"] is not None,
)
check(
    "a high fallback rate raises the alert",
    reliability["alert"] is True,
    str(reliability),
)

print(f"\nAll {len(PASSED)} degraded-mode checks passed.")

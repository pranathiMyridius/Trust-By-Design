"""
R6.1        factor weights, scales, risk bands and required approvals are
            configurable (admin, reason, unlocked methodology only)
R6.1        required approvals are checked before committee approval
R5.1-R5.5   approved-source library: only approved sources are searched,
            results carry their source details, outdated sources warn,
            and a passage can be attached to a risk factor as evidence
"""

from datetime import date, timedelta

from app.auth.security import hash_password
from app.database import SessionLocal
from app.models.assessment import Assessment
from app.models.residual_risk_calculation import ResidualRiskCalculation
from app.models.user import User
from app.services.decision_record_service import _missing_required_approvals
from tests.conftest import PASSWORD, ok

BANDS = [
    {"name": "LOW", "min": 0, "max": 29},
    {"name": "MEDIUM", "min": 30, "max": 54},
    {"name": "HIGH", "min": 55, "max": 74},
    {"name": "CRITICAL", "min": 75, "max": 100},
]


def _methodology(client, auth) -> int:
    created = ok(
        client.post(
            "/api/risk-methodologies",
            json={"name": "Scoring config test", "weights": {"GEOGRAPHIC_RISK": 1.0}, "thresholds": {}},
            headers=auth("admin"),
        ),
        201,
    )
    return created["id"]


def test_scoring_config_is_validated_and_saved(client, auth):
    mid = _methodology(client, auth)
    url = f"/api/risk-methodologies/{mid}/scoring-config"

    assert client.put(url, json={"risk_bands": BANDS, "reason": "x"}, headers=auth("analyst")).status_code == 403
    assert client.put(url, json={"risk_bands": BANDS, "reason": " "}, headers=auth("admin")).status_code == 422

    gap = [dict(band) for band in BANDS]
    gap[1]["min"] = 35
    bad = client.put(url, json={"risk_bands": gap, "reason": "Recalibration."}, headers=auth("admin"))
    assert bad.status_code == 400 and any("start one point after" in error for error in bad.json()["detail"])

    bad_scale = client.put(
        url,
        json={"likelihood_scale": [{"value": 2, "label": "a"}, {"value": 1, "label": "b"}], "reason": "x"},
        headers=auth("admin"),
    )
    assert bad_scale.status_code == 400

    bad_approvals = client.put(
        url, json={"required_approvals": {"LOW": ["CEO"]}, "reason": "x"}, headers=auth("admin")
    )
    assert bad_approvals.status_code == 400

    saved = ok(
        client.put(
            url,
            json={
                "risk_bands": BANDS,
                "factor_weights": {"GEOGRAPHIC_RISK": 0.3, "PRODUCT_SERVICE_RISK": 0.7},
                "likelihood_scale": [{"value": n, "label": f"L{n}"} for n in range(1, 5)],
                "required_approvals": {
                    "LOW": ["ANALYST"],
                    "MEDIUM": ["ANALYST", "REVIEWER"],
                    "HIGH": ["REVIEWER", "COMMITTEE"],
                    "CRITICAL": ["REVIEWER", "COMMITTEE"],
                },
                "reason": "Annual recalibration.",
            },
            headers=auth("admin"),
        )
    )
    assert saved["config"]["risk_bands"] == BANDS
    assert len(saved["config"]["likelihood_scale"]) == 4
    assert saved["config"]["required_approvals"]["CRITICAL"] == ["REVIEWER", "COMMITTEE"]

    config = ok(client.get(f"/api/risk-methodologies/{mid}/config", headers=auth("admin")))["config"]
    assert config["factor_weights"] == {"GEOGRAPHIC_RISK": 0.3, "PRODUCT_SERVICE_RISK": 0.7}


def test_methodology_create_needs_an_admin(client, auth):
    response = client.post(
        "/api/risk-methodologies", json={"name": "x", "weights": {}, "thresholds": {}}, headers=auth("manager")
    )
    assert response.status_code == 403


def test_required_approvals_are_checked(create_assessment):
    aid = create_assessment()["id"]
    db = SessionLocal()
    try:
        calc = ResidualRiskCalculation(assessment_id=aid, residual_band="MEDIUM", frozen=True, is_current=True, version=1)
        db.add(calc)
        db.commit()
        assessment = db.get(Assessment, aid)

        # Default methodology: MEDIUM needs ANALYST and REVIEWER.
        missing = _missing_required_approvals(db, assessment, calc)
        assert any("FCRM analyst review" in item for item in missing)
        assert any("reviewing manager" in item for item in missing)

        assessment.manager_decision = "APPROVE"
        missing = _missing_required_approvals(db, assessment, calc)
        assert not any("reviewing manager" in item for item in missing)

        calc.confirmed_band = "CRITICAL"  # the confirmed band of record decides
        missing = _missing_required_approvals(db, assessment, calc)
        assert missing == []
    finally:
        db.close()


# -- R5.1-R5.5 --------------------------------------------------------------------

POLICY = (
    "Customer due diligence must be completed before any account is opened.\n\n"
    "Beneficial ownership of corporate customers must be verified against an independent source "
    "for every owner above 25 percent."
)


def _policy_admin(client) -> dict:
    db = SessionLocal()
    try:
        if not db.query(User).filter(User.email == "policy@test.io").first():
            db.add(User(email="policy@test.io", hashed_password=hash_password(PASSWORD), full_name="Policy Admin", role="POLICY_ADMIN"))
            db.commit()
    finally:
        db.close()
    token = ok(client.post("/api/auth/login", json={"email": "policy@test.io", "password": PASSWORD}))["access_token"]
    return {"Authorization": f"Bearer {token}"}


def test_only_approved_sources_are_searched(client, auth):
    admin = _policy_admin(client)
    body = {
        "title": "Group KYC Policy",
        "source_type": "INTERNAL_POLICY",
        "issuer": "Group Compliance",
        "version": "4.2",
        "effective_date": "2026-01-01",
        "review_date": str(date.today() + timedelta(days=200)),
        "reference": "POL-KYC-004",
        "content": POLICY,
    }
    assert client.post("/api/sources", json=body, headers=auth("analyst")).status_code == 403
    source = ok(client.post("/api/sources", json=body, headers=admin), 201)
    assert source["status"] == "DRAFT"

    assert ok(client.get("/api/sources/search?q=beneficial%20ownership", headers=auth("analyst"))) == []
    assert all(s["id"] != source["id"] for s in ok(client.get("/api/sources", headers=auth("analyst"))))

    ok(client.post(f"/api/sources/{source['id']}/approve", json={"reason": "Signed off by CCO."}, headers=admin))
    results = ok(client.get("/api/sources/search?q=beneficial%20ownership", headers=auth("analyst")))
    assert results and results[0]["source_title"] == "Group KYC Policy"
    top = results[0]
    assert top["source_version"] == "4.2" and top["reference"] == "POL-KYC-004"
    assert "Beneficial ownership" in top["passage"] and top["outdated"] is False

    edit = client.patch(f"/api/sources/{source['id']}", json={"content": "changed"}, headers=admin)
    assert edit.status_code == 409  # approved sources are immutable


def test_factor_evidence_from_the_library(client, auth, analysed_assessment):
    admin = _policy_admin(client)
    stale = ok(
        client.post(
            "/api/sources",
            json={
                "title": "FATF Guidance on Beneficial Ownership",
                "source_type": "REGULATORY_GUIDANCE",
                "issuer": "FATF",
                "version": "2023",
                "review_date": str(date.today() - timedelta(days=1)),
                "content": "Countries should ensure that beneficial ownership information is adequate, "
                "accurate and up to date for legal persons operating cross-border.",
            },
            headers=admin,
        ),
        201,
    )
    ok(client.post(f"/api/sources/{stale['id']}/approve", json={"reason": "Published guidance."}, headers=admin))

    aid = analysed_assessment()["id"]
    factors = ok(client.get(f"/api/assessments/{aid}/risk-factors", headers=auth("analyst")))
    geographic = next(f for f in factors if f["category"] == "GEOGRAPHIC_RISK")
    url = f"/api/assessments/{aid}/risk-factors/{geographic['id']}/source-evidence"

    evidence = ok(client.get(url, headers=auth("analyst")))
    assert evidence["supported"] is False
    suggestion = next(s for s in evidence["suggestions"] if s["source_id"] == stale["id"])
    assert suggestion["outdated"] is True

    assert client.post(url, json={"source_id": stale["id"], "passage": "made up text"}, headers=auth("analyst")).status_code == 422
    # P4 (Stage 5 AC): an outdated source is refused until acknowledged.
    refused = client.post(url, json={"source_id": stale["id"], "passage": suggestion["passage"]}, headers=auth("analyst"))
    assert refused.status_code == 409 and refused.json()["detail"]["code"] == "OUTDATED_SOURCE_UNACKNOWLEDGED"
    link = ok(
        client.post(
            url,
            json={
                "source_id": stale["id"],
                "passage": suggestion["passage"],
                "acknowledge_outdated": True,
                "acknowledgement_reason": "Still the current FATF text; review pending.",
            },
            headers=auth("analyst"),
        ),
        201,
    )
    assert link["source_version"] == "2023" and link["retrieved_by"] == "Analyst"
    assert link["outdated"] is True
    assert link["outdated_at_attach"] is True and link["outdated_acknowledgement_reason"].startswith("Still")

    after = ok(client.get(url, headers=auth("analyst")))
    assert after["supported"] is True and len(after["linked"]) == 1
    assert all(s["passage"] != suggestion["passage"] for s in after["suggestions"])

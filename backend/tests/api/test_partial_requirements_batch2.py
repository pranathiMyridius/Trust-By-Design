"""
Partly-done requirements completed in batch 2 (backend side):

R6.5   every inherent-risk calculation version can be read back
R9.3   any draft version can be read back in full
R9.1   the draft carries the whole R3.1 profile and an override-aware
       inherent section
R12.2  the decision package lists the main risk drivers
R7.4/R7.6  inadequate design and partial/inactive operation are control
       gaps and earn no full credit
"""

from app.control_engine import gap_detection, scoring
from tests.conftest import ok


def _rate_all(client, auth, aid, likelihood=3, impact=3):
    factors = ok(client.get(f"/api/assessments/{aid}/risk-factors", headers=auth("analyst")))
    applicable = [f for f in factors if f["applicable"] and not f["excluded"]]
    for factor in applicable:
        ok(
            client.patch(
                f"/api/assessments/{aid}/risk-factors/{factor['id']}/rating",
                json={"likelihood": likelihood, "impact": impact},
                headers=auth("analyst"),
            )
        )
    return applicable


def test_inherent_risk_history_keeps_every_version(client, auth, analysed_assessment):
    aid = analysed_assessment()["id"]
    applicable = _rate_all(client, auth, aid)
    ok(
        client.patch(
            f"/api/assessments/{aid}/risk-factors/{applicable[0]['id']}/rating",
            json={"likelihood": 5, "impact": 5},
            headers=auth("analyst"),
        )
    )

    history = ok(client.get(f"/api/assessments/{aid}/inherent-risk/history", headers=auth("analyst")))
    versions = [row["version"] for row in history]
    assert versions == sorted(versions, reverse=True) and len(versions) >= 2
    assert [row["is_current"] for row in history].count(True) == 1 and history[0]["is_current"]
    assert history[0]["calculated_score"] != history[1]["calculated_score"]


def test_draft_versions_can_be_read_back_and_package_has_drivers(client, auth, analysed_assessment):
    aid = analysed_assessment()["id"]
    _rate_all(client, auth, aid, 4, 4)
    ok(
        client.post(
            f"/api/assessments/{aid}/inherent-risk/override",
            json={"override_value": 95, "reason": "Sanctions exposure understated."},
            headers=auth("analyst"),
        )
    )

    generated = ok(client.post(f"/api/assessments/{aid}/draft/generate", headers=auth("analyst")))
    assert generated["inherent_risk"]["overridden"] is True
    assert generated["inherent_risk"]["score"] == 95
    assert generated["inherent_risk"]["override_reason"] == "Sanctions exposure understated."
    for field in ("transaction_frequency", "payment_methods", "onboarding_approach", "ownership_entity_structure"):
        assert field in generated["business_profile"]

    edited = ok(
        client.patch(
            f"/api/assessments/{aid}/draft",
            json={"executive_summary": "Edited summary."},
            headers=auth("analyst"),
        )
    )
    assert edited["version"] == generated["version"] + 1

    original = ok(
        client.get(f"/api/assessments/{aid}/draft/versions/{generated['id']}", headers=auth("analyst"))
    )
    assert original["executive_summary"] == generated["executive_summary"]
    assert original["is_current"] is False
    assert client.get(f"/api/assessments/{aid}/draft/versions/999999", headers=auth("analyst")).status_code == 404

    package = ok(client.get(f"/api/assessments/{aid}/decision-package", headers=auth("analyst")))
    drivers = package["main_risk_drivers"]
    assert drivers and all(driver["likelihood"] == 4 and driver["impact"] == 4 for driver in drivers)


def _assessed(**overrides):
    base = {
        "design_adequacy": "DESIGN_ADEQUATE",
        "operating_effectiveness": "EFFECTIVE",
        "has_evidence": True,
        "coverage_complete": True,
        "depends_on_unavailable_data": False,
        "operating_status": "ACTIVE",
    }
    return {**base, **overrides}


def _gap_types(assessment):
    controls = [gap_detection.ControlWithAssessment(control_id=1, current_assessment=assessment)]
    return [gap.gap_type for gap in gap_detection.detect_gaps_for_risk(7, controls)]


def test_design_and_operating_status_drive_gaps_and_credit():
    assert _gap_types(_assessed()) == []
    assert scoring.rating_for_risk([_assessed()]) == "EFFECTIVE"

    inadequate = _assessed(design_adequacy="DESIGN_INADEQUATE")
    assert "INEFFECTIVE" in _gap_types(inadequate)
    assert scoring.rating_for_risk([inadequate]) == "WEAK"

    inactive = _assessed(operating_status="INACTIVE")
    assert "INEFFECTIVE" in _gap_types(inactive)
    assert scoring.rating_for_risk([inactive]) == "WEAK"

    for status in ("PARTIALLY_IMPLEMENTED", "PLANNED"):
        partial = _assessed(operating_status=status)
        assert "PARTIAL" in _gap_types(partial)
        assert scoring.rating_for_risk([partial]) == "PARTIAL"

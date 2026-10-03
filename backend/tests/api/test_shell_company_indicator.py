"""
The intake form's "potential shell entity" flag: stored as true / false /
not answered (None) through both create paths and on edit.
"""

from tests.conftest import FULL_REQUEST, ok


def test_flag_defaults_to_not_answered(create_assessment):
    assert create_assessment()["shell_company_indicator"] is None


def test_json_create_and_update_store_the_flag(client, auth, create_assessment):
    created = create_assessment(shell_company_indicator=True)
    assert created["shell_company_indicator"] is True

    updated = ok(
        client.patch(
            f"/api/assessments/{created['id']}",
            json={**FULL_REQUEST, "shell_company_indicator": False},
            headers=auth("owner"),
        )
    )
    assert updated["shell_company_indicator"] is False

    fetched = ok(client.get(f"/api/assessments/{created['id']}", headers=auth("owner")))
    assert fetched["shell_company_indicator"] is False


def _create_with_document(client, auth, **form):
    data = {**FULL_REQUEST, "is_draft": "false", **form}
    return client.post(
        "/api/assessments/create-with-document",
        data=data,
        files={"files": ("brief.txt", b"Merchant acquiring brief.", "text/plain")},
        headers=auth("owner"),
    )


def test_document_create_parses_the_flag(client, auth):
    flagged = ok(_create_with_document(client, auth, shell_company_indicator="true", business_unit="Payments"))
    assert flagged["shell_company_indicator"] is True
    # Sent by the form all along, but previously dropped on this path.
    assert flagged["business_unit"] == "Payments"

    blank = ok(_create_with_document(client, auth, shell_company_indicator=""))
    assert blank["shell_company_indicator"] is None


def test_document_create_rejects_a_non_boolean_flag(client, auth):
    assert _create_with_document(client, auth, shell_company_indicator="maybe").status_code == 422


# -- triage ---------------------------------------------------------------------

# Everything else about this request scores low (only the mandatory
# technology field adds anything).
BENIGN = {
    "change_type": "PERIODIC_REASSESSMENT",
    "countries_jurisdictions": "Germany",
    "third_party_vendor_usage": "None",
}


def test_flag_raises_priority_and_routing_at_submission(create_assessment):
    plain = create_assessment(**BENIGN)
    flagged = create_assessment(**BENIGN, shell_company_indicator=True)

    assert plain["priority"] == "LOW"
    assert flagged["priority"] == "HIGH"
    assert flagged["priority_score"] > plain["priority_score"]
    assert flagged["assigned_queue"] == "Priority Queue"


def test_document_create_triages_the_flag(client, auth):
    flagged = ok(_create_with_document(client, auth, **BENIGN, shell_company_indicator="true"))
    assert flagged["priority"] == "HIGH"


def _edit(client, auth, aid, **fields):
    current = ok(client.get(f"/api/assessments/{aid}", headers=auth("owner")))
    body = {key: current[key] for key in (*FULL_REQUEST.keys(), "title", "change_type")}
    return ok(client.patch(f"/api/assessments/{aid}", json={**body, **fields}, headers=auth("owner")))


def test_flagging_after_submission_retriages_and_tightens_the_sla(client, auth, create_assessment):
    created = create_assessment(**BENIGN)
    assert created["priority"] == "LOW"

    raised = _edit(client, auth, created["id"], shell_company_indicator=True)
    assert raised["priority"] == "HIGH"
    assert raised["assigned_queue"] == "Priority Queue"
    assert raised["status_due_at"] < created["status_due_at"]

    events = ok(client.get(f"/api/assessments/{created['id']}/audit", headers=auth("owner")))
    assert any("Priority re-triaged" in (event.get("details") or "") for event in events)

    lowered = _edit(client, auth, created["id"], shell_company_indicator=False)
    assert lowered["priority"] == "LOW"


def test_edit_that_leaves_the_flag_out_keeps_it(client, auth, create_assessment):
    created = create_assessment(**BENIGN, shell_company_indicator=True)

    body = {key: value for key, value in FULL_REQUEST.items()}
    body.update(BENIGN)
    edited = ok(client.patch(f"/api/assessments/{created['id']}", json=body, headers=auth("owner")))

    assert edited["shell_company_indicator"] is True
    assert edited["priority"] == "HIGH"

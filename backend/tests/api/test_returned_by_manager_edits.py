"""
After the manager returns an assessment (RETURNED_BY_MANAGER, shown as
"Amendment Required") the owner can correct the request fields and add or
replace documents, then resubmit. Other statuses stay closed to both.
"""

from datetime import datetime, timedelta, timezone

from app.database import SessionLocal
from app.models.assessment import Assessment
from tests.conftest import ok


def _status(aid: int, status: str) -> None:
    db = SessionLocal()
    try:
        db.get(Assessment, aid).status = status
        db.commit()
    finally:
        db.close()


def _upload(client, auth, aid: int):
    return client.post(
        f"/api/assessments/{aid}/documents",
        files={"file": ("corrected-procedure.txt", b"Corrected onboarding procedure.")},
        headers=auth("owner"),
    )


def test_owner_can_correct_the_request_and_add_evidence_after_a_return(client, auth, create_assessment):
    aid = create_assessment()["id"]
    _status(aid, "RETURNED_BY_MANAGER")

    edited = ok(
        client.patch(
            f"/api/assessments/{aid}",
            json={"title": "Cross-border merchant acquiring in Germany (corrected)", "is_draft": False},
            headers=auth("owner"),
        )
    )
    assert edited["title"].endswith("(corrected)")
    # Editing never moves the assessment: only resubmitting does.
    assert edited["status"] == "RETURNED_BY_MANAGER"

    uploaded = ok(_upload(client, auth, aid))
    assert uploaded["filename"] == "corrected-procedure.txt"
    assert ok(client.get(f"/api/assessments/{aid}", headers=auth("owner")))["status"] == "RETURNED_BY_MANAGER"


def test_a_returned_assessment_still_refuses_edits_from_other_users(client, auth, create_assessment):
    aid = create_assessment()["id"]
    _status(aid, "RETURNED_BY_MANAGER")
    other = client.patch(
        f"/api/assessments/{aid}", json={"title": "Not mine", "is_draft": False}, headers=auth("other_owner")
    )
    assert other.status_code in (403, 404)


def test_other_in_flight_statuses_stay_closed(client, auth, create_assessment):
    aid = create_assessment()["id"]
    for status in ("SUBMITTED_TO_MANAGER", "READY_FOR_COMMITTEE", "RISK_IDENTIFICATION"):
        _status(aid, status)
        edit = client.patch(f"/api/assessments/{aid}", json={"title": "Changed", "is_draft": False}, headers=auth("owner"))
        assert edit.status_code in (400, 403), (status, edit.text)
        if status != "RISK_IDENTIFICATION":
            assert _upload(client, auth, aid).status_code == 400, status


# -- re-analysis on resubmit --------------------------------------------------


def _returned(aid: int, minutes_ago: int = 0) -> None:
    """Put the assessment in the returned state, as of `minutes_ago`."""

    db = SessionLocal()
    try:
        assessment = db.get(Assessment, aid)
        assessment.status = "RETURNED_BY_MANAGER"
        assessment.manager_decision = "RETURN"
        assessment.manager_decided_at = datetime.now(timezone.utc) - timedelta(minutes=minutes_ago)
        db.commit()
    finally:
        db.close()


def _amendment(client, auth, aid: int) -> dict:
    return ok(client.get(f"/api/assessments/{aid}/amendment-status", headers=auth("owner")))


def test_resubmit_with_nothing_changed_goes_straight_back_to_the_manager(client, auth, create_assessment):
    aid = create_assessment()["id"]
    _returned(aid)
    status = _amendment(client, auth, aid)
    assert status["requires_reanalysis"] is False and status["changes"] == []

    resubmitted = ok(client.post(f"/api/assessments/{aid}/submit-to-manager", headers=auth("owner")))
    assert resubmitted["status"] == "SUBMITTED_TO_MANAGER"


def test_documents_added_before_the_return_do_not_count(client, auth, create_assessment):
    aid = create_assessment()["id"]
    _status(aid, "EVIDENCE_COLLECTION")
    ok(_upload(client, auth, aid))
    _returned(aid)
    assert _amendment(client, auth, aid)["requires_reanalysis"] is False


def test_a_new_document_after_the_return_sends_the_resubmission_back_through_analysis(client, auth, create_assessment):
    aid = create_assessment()["id"]
    _returned(aid, minutes_ago=5)
    ok(_upload(client, auth, aid))

    status = _amendment(client, auth, aid)
    assert status["requires_reanalysis"] is True
    assert [change["kind"] for change in status["changes"]] == ["DOCUMENT"]
    assert "corrected-procedure.txt" in status["changes"][0]["description"]

    # Uploading alone changed nothing: the assessment is still returned.
    assert ok(client.get(f"/api/assessments/{aid}", headers=auth("owner")))["status"] == "RETURNED_BY_MANAGER"

    resubmitted = ok(client.post(f"/api/assessments/{aid}/submit-to-manager", headers=auth("owner")))
    assert resubmitted["status"] == "EVIDENCE_COLLECTION"

    audit = ok(client.get(f"/api/assessments/{aid}/audit", headers=auth("owner")))
    assert any(event["action"] == "REANALYSIS_REQUIRED" for event in audit)


def test_request_detail_changes_after_the_return_also_require_reanalysis(client, auth, create_assessment):
    aid = create_assessment()["id"]
    _returned(aid, minutes_ago=5)
    ok(
        client.patch(
            f"/api/assessments/{aid}",
            json={
                "title": "Cross-border merchant acquiring in Germany",
                "expected_transaction_volume": "500k/month",
                "is_draft": False,
                "change_reason": "Volumes were understated.",
            },
            headers=auth("owner"),
        )
    )
    status = _amendment(client, auth, aid)
    assert status["requires_reanalysis"] is True
    assert any("expected_transaction_volume" in change["description"] for change in status["changes"])


def test_only_the_owner_can_resubmit(client, auth, create_assessment):
    aid = create_assessment()["id"]
    _returned(aid)
    assert client.post(f"/api/assessments/{aid}/submit-to-manager", headers=auth("analyst")).status_code == 403


def test_the_analysis_can_be_walked_again_after_a_re_analysis_resubmission(client, auth, analysed_assessment):
    aid = analysed_assessment()["id"]
    before = ok(client.get(f"/api/assessments/{aid}/risk-factors", headers=auth("analyst")))
    assert before

    _returned(aid, minutes_ago=5)
    ok(_upload(client, auth, aid))
    resubmitted = ok(client.post(f"/api/assessments/{aid}/submit-to-manager", headers=auth("owner")))
    assert resubmitted["status"] == "EVIDENCE_COLLECTION"

    # The owner (and analysts) re-run risk identification from Evidence Collection.
    advanced = ok(client.patch(f"/api/assessments/{aid}/advance-stage", json={}, headers=auth("owner")))
    assert advanced["status"] == "RISK_IDENTIFICATION"
    after = ok(client.get(f"/api/assessments/{aid}/risk-factors", headers=auth("analyst")))
    assert after

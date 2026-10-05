"""Lifecycle email notifications (app/services/notifications.py)."""

import pytest

from app.services import notifications
from tests.api.test_p2_governance import _at, _review, _sign_off, no_challenge_triggers  # noqa: F401
from tests.conftest import ok


class _Inline:
    """threading.Thread stand-in that runs the target straight away."""

    def __init__(self, target, args=(), daemon=None):
        self._target, self._args = target, args

    def start(self):
        self._target(*self._args)


@pytest.fixture
def sent(monkeypatch):
    monkeypatch.setenv("NOTIFY_EMAIL_ENABLED", "true")
    monkeypatch.setenv("SMTP_HOST", "smtp.example.test")
    monkeypatch.setenv("SMTP_USER", "u")
    monkeypatch.setenv("SMTP_PASSWORD", "p")
    box: list[dict] = []
    monkeypatch.setattr(
        notifications, "send_email",
        lambda to, subject, text, page=None: box.append({"to": to, "subject": subject, "text": text, "html": page}) or True,
    )
    monkeypatch.setattr(notifications.threading, "Thread", _Inline)
    return box


def test_every_template_renders_with_every_placeholder_filled():
    context = {key: f"<b>{key}</b>" for key in notifications.CONTEXT_KEYS}
    context["app_url"] = "http://app.test"
    for key in notifications.TEMPLATES:
        subject, text, page = notifications.render(key, context)
        assert subject and text and page
        assert "{{" not in subject + text + page, key
        assert "<b>title</b>" not in page, "values must be HTML-escaped in the HTML body"


def test_template_wording_can_be_overridden_from_a_file(tmp_path, monkeypatch):
    path = tmp_path / "templates.json"
    path.write_text('{"ASSIGNED_OWNER": {"heading": "Custom heading"}, "NOT_A_TEMPLATE": {"x": "y"}}')
    monkeypatch.setenv("EMAIL_TEMPLATES_FILE", str(path))
    context = {key: "v" for key in notifications.CONTEXT_KEYS}
    assert "Custom heading" in notifications.render("ASSIGNED_OWNER", context)[1]


def test_nothing_is_queued_when_email_is_off(client, auth, create_assessment, monkeypatch):
    monkeypatch.delenv("NOTIFY_EMAIL_ENABLED", raising=False)
    calls = []
    monkeypatch.setattr(notifications, "send_email", lambda *a, **k: calls.append(a))
    create_assessment()
    assert calls == []


def test_submitting_an_assessment_emails_the_owner_and_analysts(create_assessment, sent):
    create_assessment()
    assert any(m["to"] == "owner@test.io" and "submitted" in m["subject"].lower() for m in sent)
    assert any(m["to"] == "analyst@test.io" and "analyse" in m["subject"].lower() for m in sent)


def test_a_draft_sends_nothing(create_assessment, sent):
    create_assessment(is_draft=True)
    assert sent == []


def test_assignment_email_is_worded_for_the_role_and_goes_after_commit(client, auth, users, create_assessment, sent):
    aid = create_assessment()["id"]
    sent.clear()
    ok(client.post(f"/api/assessments/{aid}/workflow/assign", json={"user_id": users["analyst"], "reason": "x"}, headers=auth("admin")))
    mails = [m for m in sent if m["to"] == "analyst@test.io"]
    assert len(mails) == 1
    assert "Assigned to you" in mails[0]["subject"]
    assert "Hello Analyst" in mails[0]["text"]


def test_manager_approval_emails_the_owner_and_the_committee(client, auth, users, create_assessment, sent, no_challenge_triggers):
    aid = create_assessment()["id"]
    _at(aid, "SUBMITTED_TO_MANAGER", manager_id=users["manager"])
    ok(_review(client, auth, aid), 201)
    ok(_sign_off(client, auth, aid), 201)
    sent.clear()
    ok(client.post(f"/api/assessments/{aid}/manager-decision", json={"decision": "approve", "comment": "Looks sound."}, headers=auth("manager")))
    assert any(m["to"] == "owner@test.io" and "Approved by manager" in m["subject"] and "Looks sound." in m["text"] for m in sent)
    assert any("Ready for committee" in m["subject"] for m in sent)


def test_manager_return_emails_the_owner_with_the_comment(client, auth, users, create_assessment, sent, no_challenge_triggers):
    aid = create_assessment()["id"]
    _at(aid, "SUBMITTED_TO_MANAGER", manager_id=users["manager"])
    sent.clear()
    ok(client.post(f"/api/assessments/{aid}/manager-decision", json={"decision": "return", "comment": "Add the sanctions screening."}, headers=auth("manager")))
    returned = [m for m in sent if m["to"] == "owner@test.io"]
    assert len(returned) == 1
    assert "Returned for changes" in returned[0]["subject"]
    assert "Add the sanctions screening." in returned[0]["html"]

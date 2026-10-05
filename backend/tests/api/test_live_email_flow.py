"""
LIVE end-to-end run of the email notifications: real SMTP, throwaway database.

Skipped unless LIVE_EMAIL_TEST=1. Needs NOTIFY_EMAIL_OVERRIDE_TO (so every
message goes to one inbox, never to the fake @test.io users) plus the SMTP_*
settings from backend/.env. Run from backend/:

    LIVE_EMAIL_TEST=1 python -m pytest tests/api/test_live_email_flow.py -s
"""

import os
import threading

import pytest

from app.services import notifications
from tests.api.test_p2_governance import _at, _complete_challenge, no_challenge_triggers  # noqa: F401
from tests.conftest import ok

pytestmark = pytest.mark.skipif(
    os.environ.get("LIVE_EMAIL_TEST") != "1" or not os.environ.get("NOTIFY_EMAIL_OVERRIDE_TO"),
    reason="live email test: set LIVE_EMAIL_TEST=1 and NOTIFY_EMAIL_OVERRIDE_TO",
)


@pytest.fixture
def mail(monkeypatch):
    """Real sending, but record each message and wait for the threads."""
    threads: list[threading.Thread] = []
    outbox: list[str] = []
    real_send = notifications.send_email

    class Tracked(threading.Thread):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            threads.append(self)

    def recording_send(to, subject, text, page=None):
        delivered = real_send(to, subject, text, page)
        outbox.append(f"{'OK  ' if delivered else 'FAIL'} {subject}")
        return delivered

    monkeypatch.setattr(notifications.threading, "Thread", Tracked)
    monkeypatch.setattr(notifications, "send_email", recording_send)

    class Mail:
        def wait(self, label):
            for thread in threads:
                thread.join(timeout=60)
            threads.clear()
            print(f"\n--- {label}: {len(outbox)} email(s)")
            for line in outbox:
                print("   ", line)
            sent = list(outbox)
            outbox.clear()
            return sent

    return Mail()


def _vote(client, auth, aid, who):
    return client.post(f"/api/assessments/{aid}/committee-votes", json={"vote": "APPROVE", "comment": "Reviewed."}, headers=auth(who))


def test_full_lifecycle_emails(client, auth, users, create_assessment, mail, no_challenge_triggers):
    # 1. Create + submit
    aid = create_assessment()["id"]
    sent = mail.wait("1. assessment created")
    assert any("Assessment submitted" in s for s in sent) and any("New assessment to analyse" in s for s in sent)

    # 2. Assign the task to an analyst
    ok(client.post(f"/api/assessments/{aid}/workflow/assign", json={"user_id": users["analyst"], "reason": "Please take this."}, headers=auth("admin")))
    assert any("Assigned to you" in s for s in mail.wait("2. task assigned"))

    # 3. Owner submits to the manager
    _at(aid, "HUMAN_REVIEW")
    ok(client.post(f"/api/assessments/{aid}/submit-to-manager", headers=auth("owner")))
    sent = mail.wait("3. submitted to manager")
    assert any("Review requested" in s for s in sent)

    # 4. Manager approves -> committee is told
    _complete_challenge(client, auth, aid)
    ok(client.post(f"/api/assessments/{aid}/manager-decision", json={"decision": "approve", "comment": "Sound analysis; proceed to committee."}, headers=auth("manager")))
    sent = mail.wait("4. manager approved")
    assert any("Approved by manager" in s for s in sent) and any("Ready for committee" in s for s in sent)

    # 5. Committee decides
    for who in ("committee", "fcrm_rep", "business_rep"):
        ok(_vote(client, auth, aid, who))
    mail.wait("(votes)")
    ok(client.post(f"/api/assessments/{aid}/committee-decision", json={"decision": "approve", "rationale": "Within appetite with the agreed monitoring."}, headers=auth("committee")))
    sent = mail.wait("5. committee decision")
    assert any("Approved by committee" in s for s in sent)

    # 6. A second assessment the manager returns
    other = create_assessment()["id"]
    mail.wait("6a. second assessment created")
    _at(other, "SUBMITTED_TO_MANAGER", manager_id=users["manager"])
    ok(client.post(f"/api/assessments/{other}/manager-decision", json={"decision": "return", "comment": "Please add the sanctions screening evidence."}, headers=auth("manager")))
    sent = mail.wait("6b. manager returned it")
    assert any("Returned for changes" in s for s in sent)

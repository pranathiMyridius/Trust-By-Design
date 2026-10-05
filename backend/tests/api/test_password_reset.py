"""Self-service password reset by emailed link (app/api/auth.py)."""

import re
from datetime import datetime, timedelta, timezone

import pytest
from jose import jwt

from app.api import auth as auth_api
from app.auth import security
from app.database import SessionLocal
from app.models.user import User
from app.services import notifications

EMAIL = "resetme@test.io"
OLD, NEW = "Old-Passw0rd!", "Brand-New-Passw0rd!"


class _Inline:
    def __init__(self, target, args=(), daemon=None):
        self._target, self._args = target, args

    def start(self):
        self._target(*self._args)


@pytest.fixture
def mailbox(monkeypatch):
    monkeypatch.setenv("NOTIFY_EMAIL_ENABLED", "true")
    monkeypatch.setenv("SMTP_HOST", "smtp.example.test")
    monkeypatch.setenv("SMTP_USER", "u")
    monkeypatch.setenv("SMTP_PASSWORD", "p")
    monkeypatch.setenv("APP_BASE_URL", "https://workbench.example.test")
    box: list[dict] = []
    monkeypatch.setattr(
        notifications, "send_email",
        lambda to, subject, text, page=None: box.append({"to": to, "subject": subject, "text": text, "html": page}) or True,
    )
    monkeypatch.setattr(notifications.threading, "Thread", _Inline)
    auth_api._last_request.clear()
    return box


@pytest.fixture
def account(users):
    db = SessionLocal()
    try:
        user = db.query(User).filter(User.email == EMAIL).first()
        if user is None:
            user = User(email=EMAIL, hashed_password=security.hash_password(OLD), full_name="Reset Me", role="BUSINESS_USER")
            db.add(user)
        user.hashed_password = security.hash_password(OLD)
        user.is_active = True
        db.commit()
        return user.id
    finally:
        db.close()


def _token(mailbox) -> str:
    return re.search(r"#reset=(\S+)", mailbox[-1]["text"]).group(1)


def _login(client, password):
    return client.post("/api/auth/login", json={"email": EMAIL, "password": password})


def test_a_known_and_an_unknown_address_get_the_same_answer(client, account, mailbox):
    known = client.post("/api/auth/forgot-password", json={"email": EMAIL})
    unknown = client.post("/api/auth/forgot-password", json={"email": "nobody@test.io"})
    assert known.status_code == unknown.status_code == 200
    assert known.json() == unknown.json()
    assert [m["to"] for m in mailbox] == [EMAIL]


def test_the_email_carries_a_link_with_the_token_in_the_fragment(client, account, mailbox):
    client.post("/api/auth/forgot-password", json={"email": EMAIL})
    mail = mailbox[0]
    assert "Reset your" in mail["subject"]
    assert "https://workbench.example.test/#reset=" in mail["text"]
    assert "Hello Reset Me" in mail["text"] and "30 minutes" in mail["text"]


def test_reset_sets_the_new_password_and_the_link_works_once(client, account, mailbox):
    client.post("/api/auth/forgot-password", json={"email": EMAIL})
    token = _token(mailbox)

    done = client.post("/api/auth/reset-password", json={"token": token, "new_password": NEW})
    assert done.status_code == 200
    assert _login(client, NEW).status_code == 200
    assert _login(client, OLD).status_code == 401

    again = client.post("/api/auth/reset-password", json={"token": token, "new_password": "Another-Passw0rd!"})
    assert again.status_code == 400 and "invalid or has expired" in again.json()["detail"]
    assert _login(client, NEW).status_code == 200  # unchanged by the replay


def test_expired_and_tampered_and_wrong_purpose_tokens_are_refused(client, account):
    db = SessionLocal()
    try:
        user = db.query(User).filter(User.email == EMAIL).one()
        uid, hashed = user.id, user.hashed_password
    finally:
        db.close()

    expired = jwt.encode(
        {"sub": str(uid), "purpose": "password-reset", "pwf": security._password_fingerprint(hashed),
         "exp": datetime.now(timezone.utc) - timedelta(minutes=1)},
        security.JWT_SECRET, algorithm=security.JWT_ALGORITHM,
    )
    login_token = security.create_access_token(str(uid))  # a normal session token is not a reset token
    for bad in (expired, login_token, "not-a-token", security.create_reset_token(uid, hashed) + "x"):
        refused = client.post("/api/auth/reset-password", json={"token": bad, "new_password": NEW})
        assert refused.status_code == 400, bad
    assert _login(client, OLD).status_code == 200


def test_a_short_password_is_refused(client, account, mailbox):
    client.post("/api/auth/forgot-password", json={"email": EMAIL})
    refused = client.post("/api/auth/reset-password", json={"token": _token(mailbox), "new_password": "short"})
    assert refused.status_code == 422
    assert _login(client, OLD).status_code == 200


def test_a_password_changed_another_way_voids_an_outstanding_link(client, account, mailbox):
    client.post("/api/auth/forgot-password", json={"email": EMAIL})
    token = _token(mailbox)
    db = SessionLocal()
    try:
        db.query(User).filter(User.email == EMAIL).one().hashed_password = security.hash_password("Admin-Set-Passw0rd!")
        db.commit()
    finally:
        db.close()
    assert client.post("/api/auth/reset-password", json={"token": token, "new_password": NEW}).status_code == 400


def test_an_inactive_account_gets_no_email(client, account, mailbox):
    db = SessionLocal()
    try:
        db.query(User).filter(User.email == EMAIL).one().is_active = False
        db.commit()
    finally:
        db.close()
    assert client.post("/api/auth/forgot-password", json={"email": EMAIL}).status_code == 200
    assert mailbox == []


def test_repeat_requests_within_a_minute_send_one_email(client, account, mailbox):
    for _ in range(3):
        assert client.post("/api/auth/forgot-password", json={"email": EMAIL.upper()}).status_code == 200
    assert len(mailbox) == 1


def test_the_reset_is_recorded_in_the_audit_trail(client, account, mailbox):
    from app.models.audit_event import AuditEvent

    client.post("/api/auth/forgot-password", json={"email": EMAIL})
    client.post("/api/auth/reset-password", json={"token": _token(mailbox), "new_password": NEW})
    db = SessionLocal()
    try:
        events = db.query(AuditEvent).filter(AuditEvent.details.like("%password reset by email link%")).all()
        assert events and EMAIL in events[-1].details and NEW not in events[-1].details
    finally:
        db.close()

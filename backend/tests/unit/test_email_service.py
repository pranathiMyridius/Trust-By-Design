from unittest.mock import MagicMock, patch

from app.services import email_service


def _enable(monkeypatch):
    monkeypatch.setenv("NOTIFY_EMAIL_ENABLED", "true")
    monkeypatch.setenv("SMTP_HOST", "smtp.example.test")
    monkeypatch.setenv("SMTP_USER", "noreply@example.test")
    monkeypatch.setenv("SMTP_PASSWORD", "secret")


def test_disabled_by_default_sends_nothing(monkeypatch):
    monkeypatch.delenv("NOTIFY_EMAIL_ENABLED", raising=False)
    with patch("app.services.email_service.smtplib.SMTP") as smtp:
        assert email_service.send_email("a@b.test", "s", "b") is False
        smtp.assert_not_called()


def test_sends_over_starttls_when_enabled(monkeypatch):
    _enable(monkeypatch)
    with patch("app.services.email_service.smtplib.SMTP") as smtp:
        server = MagicMock()
        smtp.return_value.__enter__.return_value = server
        assert email_service.send_email("a@b.test", "s", "b") is True
        server.starttls.assert_called_once()
        server.login.assert_called_once_with("noreply@example.test", "secret")
        server.send_message.assert_called_once()


def test_smtp_failure_is_swallowed(monkeypatch):
    _enable(monkeypatch)
    with patch("app.services.email_service.smtplib.SMTP", side_effect=OSError("down")):
        assert email_service.send_email("a@b.test", "s", "b") is False

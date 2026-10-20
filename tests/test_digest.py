from __future__ import annotations

from datetime import datetime, timedelta, timezone

from price_monitor.services import digest as digest_module
from price_monitor.services.digest import CollectionDigest


def test_five_hour_digest_groups_collection_results(empty_database):
    start = datetime(2026, 9, 25, 12, 0, tzinfo=timezone.utc)
    elapsed = [0.0]
    sent = []
    digest = CollectionDigest(
        empty_database,
        send=lambda subject, body: sent.append((subject, body)),
        now=lambda: start + timedelta(seconds=elapsed[0]),
        monotonic=lambda: elapsed[0],
    )
    for index, status in enumerate(("success", "partial", "partial", "error")):
        at = (start + timedelta(minutes=index)).isoformat()
        message = "Provider unavailable" if status != "success" else "Collection completed"
        empty_database.log_collection(status, 3 if status != "error" else 0, message, at, at)

    elapsed[0] = 5 * 3600 - 1
    assert not digest.maybe_send()
    assert sent == []
    elapsed[0] += 1
    assert digest.maybe_send()
    subject, body = sent[0]
    assert "Collection report" in subject
    assert "Total runs: 4" in body
    assert "Successful: 1" in body
    assert "Partial: 2" in body
    assert "Failed: 1" in body
    assert "Records collected: 9" in body
    assert "3 run(s): Provider unavailable" in body
    assert "Latest run: Error" in body

    elapsed[0] += 5 * 3600
    assert digest.maybe_send()
    assert "Total runs: 0" in sent[1][1]


def test_failed_delivery_keeps_pending_collections_and_waits_five_hours(empty_database):
    start = datetime(2026, 9, 25, 12, 0, tzinfo=timezone.utc)
    elapsed = [0.0]
    attempts = []

    def send(subject, body):
        attempts.append(body)
        if len(attempts) == 1:
            raise RuntimeError("SMTP temporarily unavailable")

    digest = CollectionDigest(
        empty_database,
        send=send,
        now=lambda: start + timedelta(seconds=elapsed[0]),
        monotonic=lambda: elapsed[0],
    )
    at = start.isoformat()
    empty_database.log_collection("error", 0, "Provider unavailable", at, at)
    elapsed[0] = 5 * 3600
    assert not digest.maybe_send()
    assert digest.last_log_id == 0
    elapsed[0] += 60
    assert not digest.maybe_send()
    assert len(attempts) == 1
    elapsed[0] = 10 * 3600
    assert digest.maybe_send()
    assert "Total runs: 1" in attempts[1]
    assert digest.last_log_id == 1


def test_digest_email_uses_configured_recipient_and_smtp(monkeypatch):
    sent = []

    class FakeSMTP:
        def __init__(self, host, port, timeout):
            assert (host, port) == ("smtp.example.com", 587)

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def starttls(self, context):
            pass

        def login(self, user, password):
            assert (user, password) == ("sender@example.com", "app-password")

        def send_message(self, message):
            sent.append(message)

    monkeypatch.setattr(digest_module, "DIGEST_EMAIL_TO", ("recipient@example.com",))
    monkeypatch.setattr(digest_module, "ALERT_SMTP_HOST", "smtp.example.com")
    monkeypatch.setattr(digest_module, "ALERT_SMTP_USER", "sender@example.com")
    monkeypatch.setattr(digest_module, "ALERT_SMTP_PASSWORD", "app-password")
    monkeypatch.setattr(digest_module, "ALERT_SMTP_FROM", "sender@example.com")
    monkeypatch.setattr(digest_module.smtplib, "SMTP", FakeSMTP)

    digest_module.send_digest_email("Price Monitor report", "Collection summary")

    assert len(sent) == 1
    assert sent[0]["To"] == "recipient@example.com"
    assert sent[0]["Subject"] == "Price Monitor report"
    assert "Collection summary" in sent[0].get_content()

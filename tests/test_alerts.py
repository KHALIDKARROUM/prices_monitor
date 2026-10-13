from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from price_monitor.services.alerts import AlertManager
from price_monitor.services.scheduler import _next_live_check_delay


class FakeNotifier:
    def __init__(self) -> None:
        self.batches = []

    def send(self, events):
        self.batches.append(events)
        return True


def test_price_alert_notifies_on_crossing_and_recovery(empty_database):
    now = datetime(2026, 9, 15, 12, 0, tzinfo=timezone.utc)
    notifier = FakeNotifier()
    manager = AlertManager(
        empty_database,
        notifier=notifier,
        thresholds={"brent": {"above": 90}},
        now=lambda: now,
    )

    triggered = manager.evaluate_prices([{"product": "brent", "price": 91}])
    unchanged = manager.evaluate_prices([{"product": "brent", "price": 92}])
    recovered = manager.evaluate_prices([{"product": "brent", "price": 80}])

    assert [event.status for event in triggered] == ["triggered"]
    assert unchanged == []
    assert [event.status for event in recovered] == ["recovered"]
    assert [[event.status for event in batch] for batch in notifier.batches] == [
        ["triggered"],
        ["recovered"],
    ]


def test_source_failure_alert_recovers_after_success(empty_database):
    now = datetime(2026, 9, 15, 12, 0, tzinfo=timezone.utc)
    notifier = FakeNotifier()
    manager = AlertManager(empty_database, notifier=notifier, now=lambda: now)
    failed_source = {
        "code": "fred_brent",
        "label": "Brent",
        "frequency": "quotidienne",
        "active": 1,
        "last_success_at": None,
        "last_error": "Service indisponible",
    }
    healthy_source = {
        **failed_source,
        "last_success_at": "2026-09-15T11:00:00+00:00",
        "last_error": None,
    }

    failed = manager.evaluate_sources([failed_source])
    recovered = manager.evaluate_sources([healthy_source])

    assert [event.kind for event in failed] == ["source_failure"]
    assert [event.status for event in recovered] == ["recovered"]
    assert recovered[0].kind == "source_failure"


def test_stale_source_alert_uses_expected_frequency(empty_database):
    now = datetime(2026, 9, 15, 12, 0, tzinfo=timezone.utc)
    notifier = FakeNotifier()
    manager = AlertManager(empty_database, notifier=notifier, now=lambda: now)
    stale_source = {
        "code": "fred_diesel",
        "label": "Gasoil",
        "frequency": "quotidienne",
        "active": 1,
        "last_success_at": (now - timedelta(days=3)).isoformat(),
        "last_error": None,
    }

    events = manager.evaluate_sources([stale_source])

    assert len(events) == 1
    assert events[0].kind == "source_stale"
    assert events[0].status == "triggered"


def test_live_quote_freshness_uses_exchange_time_and_sends_email_on_recovery(empty_database):
    now = datetime.now(timezone.utc).replace(microsecond=0)
    rule = empty_database.create_alert_rule({
        "product": "brent", "direction": "stale", "threshold": 15, "channel": "email",
    })
    old_quote = (now - timedelta(minutes=20)).isoformat()
    empty_database.update_source_health("live_brent", True, now.isoformat(), quote_at=old_quote)
    notifier = FakeNotifier()
    manager = AlertManager(empty_database, notifier=notifier, now=lambda: now)

    triggered = manager.evaluate_live_freshness(empty_database.source_health())
    unchanged = manager.evaluate_live_freshness(empty_database.source_health())
    fresh_quote = (now - timedelta(minutes=1)).isoformat()
    empty_database.update_source_health("live_brent", True, now.isoformat(), quote_at=fresh_quote)
    recovered = manager.evaluate_live_freshness(empty_database.source_health())

    assert rule["direction"] == "stale"
    assert [(event.status, event.channel) for event in triggered] == [("triggered", "email")]
    assert unchanged == []
    assert [(event.status, event.channel) for event in recovered] == [("recovered", "email")]
    assert len(notifier.batches) == 2
    assert empty_database.alert_rules()[0]["status"] == "recovered"


def test_live_quote_alert_triggers_at_five_minute_boundary(empty_database):
    now = datetime.now(timezone.utc).replace(microsecond=0)
    empty_database.create_alert_rule({
        "product": "brent", "direction": "stale", "threshold": 5, "channel": "email",
    })
    empty_database.update_source_health(
        "live_brent", True, now.isoformat(), quote_at=(now - timedelta(minutes=5)).isoformat(),
    )
    notifier = FakeNotifier()
    manager = AlertManager(empty_database, notifier=notifier, now=lambda: now)

    assert [event.status for event in manager.evaluate_live_freshness(empty_database.source_health())] == ["triggered"]
    assert manager.evaluate_live_freshness(empty_database.source_health()) == []
    assert len(notifier.batches) == 1


def test_live_quote_check_is_scheduled_for_stale_deadline(empty_database):
    now = datetime.now(timezone.utc).replace(microsecond=0)
    empty_database.create_alert_rule({
        "product": "gasoil", "direction": "stale", "threshold": 5, "channel": "email",
    })
    empty_database.update_source_health(
        "live_gasoil_proxy", True, now.isoformat(), quote_at=(now - timedelta(minutes=4, seconds=30)).isoformat(),
    )

    assert 28 <= _next_live_check_delay(empty_database) <= 30


def test_live_quote_freshness_retries_an_undelivered_email(empty_database):
    now = datetime.now(timezone.utc).replace(microsecond=0)
    empty_database.create_alert_rule({"product": "gasoil", "direction": "stale", "threshold": 5, "channel": "email"})
    empty_database.update_source_health("live_gasoil_proxy", True, now.isoformat(), quote_at=(now - timedelta(minutes=10)).isoformat())

    class FailingNotifier(FakeNotifier):
        def send(self, events):
            self.batches.append(events)
            return len(self.batches) > 1

    notifier = FailingNotifier()
    manager = AlertManager(empty_database, notifier=notifier, now=lambda: now)
    manager.evaluate_live_freshness(empty_database.source_health())
    manager.evaluate_live_freshness(empty_database.source_health())
    manager.evaluate_live_freshness(empty_database.source_health())

    assert len(notifier.batches) == 2


def test_live_quote_rule_requires_live_product_and_email(empty_database):
    with pytest.raises(ValueError, match="gasoil et le Brent"):
        empty_database.create_alert_rule({"product": "bitume", "direction": "stale", "threshold": 15, "channel": "email"})
    with pytest.raises(ValueError, match="email"):
        empty_database.create_alert_rule({"product": "brent", "direction": "stale", "threshold": 15, "channel": "webhook"})

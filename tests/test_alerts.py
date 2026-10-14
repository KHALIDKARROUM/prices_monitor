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
    now = datetime(2026, 10, 13, 12, tzinfo=timezone.utc)
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
    now = datetime(2026, 10, 13, 12, tzinfo=timezone.utc)
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
    now = datetime(2026, 10, 13, 12, tzinfo=timezone.utc)
    empty_database.create_alert_rule({
        "product": "gasoil", "direction": "stale", "threshold": 5, "channel": "email",
    })
    empty_database.update_source_health(
        "live_gasoil_proxy", True, now.isoformat(), quote_at=(now - timedelta(minutes=4, seconds=30)).isoformat(),
    )

    assert _next_live_check_delay(empty_database, now) == 30


def test_live_quote_freshness_retries_an_undelivered_email(empty_database):
    now = datetime(2026, 10, 13, 12, tzinfo=timezone.utc)
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


def test_market_closure_and_reopening_do_not_trigger_or_recover_stale_quotes(empty_database, monkeypatch):
    from price_monitor import market_calendar
    monkeypatch.setattr(market_calendar, "CLOSURES", [])
    now = datetime.fromisoformat("2026-10-16T20:55:00+00:00")
    empty_database.create_alert_rule({"product": "brent", "direction": "stale", "threshold": 5, "channel": "email"})
    empty_database.update_source_health("live_brent", True, now.isoformat(), quote_at=now.isoformat())
    notifier = FakeNotifier()
    manager = AlertManager(empty_database, notifier=notifier, now=lambda: now)
    assert manager.evaluate_live_freshness(empty_database.source_health()) == []
    now = datetime.fromisoformat("2026-10-17T12:00:00+00:00")
    assert manager.evaluate_live_freshness(empty_database.source_health()) == []
    assert manager.evaluate_sources(empty_database.source_health()) == []
    now = datetime.fromisoformat("2026-10-18T22:04:59+00:00")
    assert manager.evaluate_live_freshness(empty_database.source_health()) == []
    now += timedelta(seconds=1)
    assert [event.status for event in manager.evaluate_live_freshness(empty_database.source_health())] == ["triggered"]
    # Pausing a previously active alert must not announce a recovery.
    now = datetime.fromisoformat("2026-10-19T21:30:00+00:00")
    assert manager.evaluate_live_freshness(empty_database.source_health()) == []
    assert empty_database.alert_rules()[0]["active"]
    now = datetime.fromisoformat("2026-10-19T22:01:00+00:00")
    assert manager.evaluate_live_freshness(empty_database.source_health()) == []
    assert empty_database.alert_rules()[0]["active"]
    now = datetime.fromisoformat("2026-10-19T22:05:00+00:00")
    empty_database.update_source_health("live_brent", True, now.isoformat(), quote_at=now.isoformat())
    assert [event.status for event in manager.evaluate_live_freshness(empty_database.source_health())] == ["recovered"]
    assert len(notifier.batches) == 2


def test_parallel_workers_do_not_duplicate_a_pending_freshness_notification(empty_database):
    from concurrent.futures import ThreadPoolExecutor
    import threading
    now = datetime.fromisoformat("2026-10-13T12:00:00+00:00")
    empty_database.create_alert_rule({"product": "brent", "direction": "stale", "threshold": 5, "channel": "email"})
    empty_database.update_source_health("live_brent", True, now.isoformat(), quote_at=(now - timedelta(minutes=10)).isoformat())
    entered, release, second_started, second_done = (threading.Event() for _ in range(4))
    class SlowNotifier(FakeNotifier):
        def send(self, events):
            self.batches.append(events)
            entered.set()
            assert release.wait(3)
            return True
    notifier = SlowNotifier()
    first = AlertManager(empty_database, notifier=notifier, now=lambda: now)
    second = AlertManager(empty_database, notifier=notifier, now=lambda: now)
    def second_check():
        second_started.set()
        result = second.evaluate_live_freshness(empty_database.source_health())
        second_done.set()
        return result
    with ThreadPoolExecutor(max_workers=2) as pool:
        initial = pool.submit(first.evaluate_live_freshness, empty_database.source_health())
        try:
            assert entered.wait(2)
            concurrent = pool.submit(second_check)
            assert second_started.wait(2)
            assert not second_done.wait(0.1)
        finally:
            release.set()
        assert len(initial.result()) == 1
        assert concurrent.result() == []
    assert len(notifier.batches) == 1


def test_long_freshness_threshold_is_not_reset_by_every_daily_reopening(empty_database):
    now = datetime.fromisoformat("2026-10-18T22:05:00+00:00")
    empty_database.create_alert_rule({"product": "brent", "direction": "stale", "threshold": 1440, "channel": "email"})
    empty_database.update_source_health("live_brent", True, now.isoformat(), quote_at="2026-10-16T20:00:00+00:00")
    notifier = FakeNotifier()
    manager = AlertManager(empty_database, notifier=notifier, now=lambda: now)
    assert [event.status for event in manager.evaluate_live_freshness(empty_database.source_health())] == ["triggered"]


def test_live_quote_rule_requires_live_product_and_email(empty_database):
    with pytest.raises(ValueError, match="diesel and Brent"):
        empty_database.create_alert_rule({"product": "bitume", "direction": "stale", "threshold": 15, "channel": "email"})
    with pytest.raises(ValueError, match="email"):
        empty_database.create_alert_rule({"product": "brent", "direction": "stale", "threshold": 15, "channel": "webhook"})

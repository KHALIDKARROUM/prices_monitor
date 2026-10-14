from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from email.message import Message
import io
import json
import threading
import time
from urllib.error import HTTPError

import pytest

from price_monitor import config, market_calendar
from price_monitor.database import Database
from price_monitor.services import collectors, scheduler as scheduling
from price_monitor.services.collectors import CollectionBatch
from price_monitor.services.connectivity import WebsiteStatus
from price_monitor.services.provider_requests import guarded_requests
from price_monitor.services.scheduler import CollectionCoordinator, CollectionScheduler


class Clock:
    def __init__(self):
        self.value = datetime(2026, 10, 13, 12, tzinfo=timezone.utc).timestamp()

    def __call__(self):
        return self.value

    def advance(self, seconds):
        self.value += seconds


@pytest.fixture(autouse=True)
def isolated_providers(monkeypatch):
    def unexpected_request(*args, **kwargs):
        raise AssertionError("Test attempted an unmocked network request")
    monkeypatch.setattr(collectors, "urlopen", unexpected_request)
    monkeypatch.setattr(collectors, "DEMO_MODE", False)
    monkeypatch.setattr(collectors, "FRED_API_KEY", "")
    monkeypatch.setattr(collectors, "EIA_API_KEY", "test")
    monkeypatch.setattr(collectors, "ALPHA_VANTAGE_API_KEY", "test")
    monkeypatch.setattr(config, "EIA_API_KEY", "test")
    monkeypatch.setattr(config, "ALPHA_VANTAGE_API_KEY", "test")
    monkeypatch.setattr(scheduling, "_evaluate_collection_alerts", lambda *args: None)
    monkeypatch.setattr(scheduling, "evaluate_source_alerts", lambda *args: None)
    monkeypatch.setattr(scheduling, "evaluate_live_alerts", lambda *args: None)
    monkeypatch.setattr(scheduling, "DIGEST_EMAIL_TO", ())
    monkeypatch.setattr(scheduling, "website_status", WebsiteStatus())
    monkeypatch.setattr(scheduling, "COLLECTION_BACKOFF_SECONDS", 60)
    monkeypatch.setattr(scheduling, "COLLECTION_MAX_BACKOFF_SECONDS", 21600)
    monkeypatch.setattr(market_calendar, "CLOSURES", [])


def alpha_response():
    return io.BytesIO(json.dumps({"data": [{"date": "2026-10-12", "value": "80"}]}).encode())


def test_daily_provider_is_not_polled_on_the_live_minute_schedule(empty_database, monkeypatch):
    clock = Clock()
    calls = []
    monkeypatch.setattr(collectors, "urlopen", lambda *args, **kwargs: calls.append(True) or alpha_response())
    coordinator = CollectionCoordinator(empty_database, now=clock, providers=["alpha_vantage"])
    assert coordinator.collect_provider("alpha_vantage")["rows"] == 1
    clock.advance(60)
    assert coordinator.collect_provider("alpha_vantage")["status"] == "deferred"
    # Recreating the coordinator cannot reset the saved schedule.
    restarted = CollectionCoordinator(Database(empty_database.path), now=clock, providers=["alpha_vantage"])
    assert restarted.collect_provider("alpha_vantage")["status"] == "deferred"
    clock.advance(719 * 60)
    assert restarted.collect_provider("alpha_vantage")["rows"] == 1
    assert len(calls) == 2


def test_manual_refresh_and_restart_cannot_bypass_alpha_quota(empty_database, monkeypatch):
    clock = Clock()
    calls = []
    monkeypatch.setattr(collectors, "urlopen", lambda *args, **kwargs: calls.append(True) or alpha_response())
    coordinator = CollectionCoordinator(empty_database, now=clock, providers=["alpha_vantage"])
    for _ in range(25):
        assert coordinator.collect_now()["rows"] == 1
    restarted = CollectionCoordinator(Database(empty_database.path), now=clock, providers=["alpha_vantage"])
    assert restarted.collect_now()["status"] == "deferred"
    status = restarted.snapshot()[0]
    assert status["requests_last_24h"] == status["request_limit_24h"] == 25
    health = next(row for row in empty_database.source_health() if row["code"] == "alpha_brent")
    assert health["last_error"] is None  # a local skip is not a source failure
    clock.advance(86400)
    assert restarted.collect_now()["rows"] == 1
    assert len(calls) == 26


def test_failed_http_attempts_count_and_backoff_survives_restart(empty_database, monkeypatch):
    clock = Clock()
    calls = []
    def fail(*args, **kwargs):
        calls.append(True)
        raise OSError("network unavailable")
    monkeypatch.setattr(collectors, "urlopen", fail)
    coordinator = CollectionCoordinator(empty_database, now=clock, providers=["alpha_vantage"])
    assert coordinator.collect_now()["status"] == "error"
    assert coordinator.collect_now()["status"] == "deferred"
    assert empty_database.request_budget("alpha_vantage", clock(), 25)["used"] == 1
    clock.advance(60)
    restarted = CollectionCoordinator(Database(empty_database.path), now=clock, providers=["alpha_vantage"])
    assert restarted.collect_now()["status"] == "error"
    clock.advance(119)
    assert restarted.collect_now()["status"] == "deferred"
    clock.advance(1)
    monkeypatch.setattr(collectors, "urlopen", lambda *args, **kwargs: calls.append(True) or alpha_response())
    assert restarted.collect_now()["status"] == "success"
    state = empty_database.collection_state("alpha_vantage")
    assert state["failures"] == 0
    assert state["next_due"] == clock() + 720 * 60
    assert len(calls) == 3


@pytest.mark.parametrize("notice", [
    {"Note": "Our standard API call frequency is 25 requests per day."},
    {"Information": "Thank you! Rate limit reached; please subscribe to premium."},
])
def test_alpha_http_200_quota_messages_start_a_persistent_daily_cooldown(empty_database, monkeypatch, notice):
    clock = Clock()
    calls = []
    monkeypatch.setattr(collectors, "urlopen", lambda *args, **kwargs: calls.append(True) or io.BytesIO(json.dumps(notice).encode()))
    coordinator = CollectionCoordinator(empty_database, now=clock, providers=["alpha_vantage"])
    assert coordinator.collect_provider("alpha_vantage")["status"] == "error"
    clock.advance(720 * 60)
    restarted = CollectionCoordinator(Database(empty_database.path), now=clock, providers=["alpha_vantage"])
    assert restarted.collect_now()["status"] == "deferred"
    assert restarted.snapshot()[0]["next_collection_at"] == datetime.fromtimestamp(clock() + 720 * 60, timezone.utc).isoformat()
    assert len(calls) == 1


@pytest.mark.parametrize("retry_after", ["3600", "Tue, 13 Oct 2026 13:00:00 GMT"])
def test_retry_after_blocks_the_other_source_and_manual_retries(empty_database, monkeypatch, retry_after):
    clock = Clock()
    calls = []
    headers = Message()
    headers["Retry-After"] = retry_after
    def limited(request, **kwargs):
        calls.append(True)
        raise HTTPError(request.full_url, 429, "Too Many Requests", headers, io.BytesIO())
    monkeypatch.setattr(collectors, "urlopen", limited)
    coordinator = CollectionCoordinator(empty_database, now=clock, providers=["eia"])
    result = coordinator.collect_provider("eia")
    assert result["status"] == "error"
    assert result["source_outcomes"][1]["deferred"]
    assert empty_database.collection_state("eia")["next_due"] == clock() + 3600
    clock.advance(1800)
    assert coordinator.collect_now()["status"] == "deferred"
    health = {row["code"]: row for row in empty_database.source_health()}
    assert health["eia_diesel"]["last_error"]
    assert health["eia_brent"]["last_error"] is None
    assert len(calls) == 1


def test_fred_rate_limits_do_not_bypass_the_cooldown_via_csv(empty_database, monkeypatch):
    clock = Clock()
    monkeypatch.setattr(collectors, "FRED_API_KEY", "test")
    calls = []
    def limited(request, **kwargs):
        calls.append(request.full_url.split("?")[0])
        raise HTTPError(request.full_url, 429, "limited", Message(), io.BytesIO())
    monkeypatch.setattr(collectors, "urlopen", limited)
    coordinator = CollectionCoordinator(empty_database, now=clock, providers=["fred"])
    assert coordinator.collect_provider("fred")["status"] == "error"
    assert calls == ["https://api.stlouisfed.org/fred/series/observations"]


def test_concurrent_budget_reservations_never_exceed_the_cap(empty_database):
    clock = Clock()
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(lambda _: empty_database.reserve_provider_request("alpha_vantage", clock(), 25), range(50)))
    assert sum(row["allowed"] for row in results) == 25
    assert empty_database.request_budget("alpha_vantage", clock(), 25)["used"] == 25


def test_reduced_quota_waits_until_enough_requests_expire(empty_database):
    clock = Clock()
    for _ in range(5):
        assert empty_database.reserve_provider_request("alpha_vantage", clock(), 25)["allowed"]
        clock.advance(60)
    budget = empty_database.request_budget("alpha_vantage", clock(), 3)
    assert budget["retry_at"] == clock() - 180 + 86400


def test_same_provider_cannot_run_concurrently(empty_database, monkeypatch):
    clock = Clock()
    entered, release = threading.Event(), threading.Event()
    def blocked(*args, **kwargs):
        entered.set()
        assert release.wait(3)
        return alpha_response()
    monkeypatch.setattr(collectors, "urlopen", blocked)
    first = CollectionCoordinator(empty_database, now=clock, providers=["alpha_vantage"])
    second = CollectionCoordinator(Database(empty_database.path), now=clock, providers=["alpha_vantage"])
    with ThreadPoolExecutor(max_workers=1) as pool:
        running = pool.submit(first.collect_now)
        try:
            assert entered.wait(2)
            assert second.collect_now()["status"] == "deferred"
        finally:
            release.set()
        assert running.result()["rows"] == 1
    assert empty_database.request_budget("alpha_vantage", clock(), 25)["used"] == 1


def test_startup_serves_saved_data_and_fast_providers_while_a_provider_blocks(empty_database, monkeypatch):
    clock = Clock()
    entered, release, fast_finished = threading.Event(), threading.Event(), threading.Event()
    def batch(provider):
        if provider == "alpha_vantage":
            entered.set()
            assert release.wait(4)
            return CollectionBatch([], [], [])
        fast_finished.set()
        return CollectionBatch([{"product": "brent", "price": 80, "source": "EIA/FRED - DCOILBRENTEU",
                                 "source_date": "2026-10-12", "source_code": "fred_brent"}], [],
                               [{"code": "fred_brent", "success": True}])
    monkeypatch.setattr(scheduling, "collect_all", batch)
    coordinator = CollectionCoordinator(empty_database, now=clock, providers=["alpha_vantage", "fred"])
    scheduler = CollectionScheduler(empty_database, coordinator)
    started = time.monotonic()
    try:
        scheduler.start()
        assert time.monotonic() - started < 0.5
        assert entered.wait(2) and fast_finished.wait(2)
        # Reads remain available while the slow collector holds its own lease.
        assert empty_database.dashboard(days=3650)["quality"]
        assert scheduler.is_running
    finally:
        release.set()
        scheduler.stop()
        for worker in scheduler._workers:
            worker.join(3)
        scheduler._thread.join(3)


@pytest.mark.parametrize("stamp, opened", [
    ("2026-10-16T20:59:59+00:00", True),
    ("2026-10-16T21:00:00+00:00", False),  # Friday 17:00 ET
    ("2026-10-17T12:00:00+00:00", False),
    ("2026-10-18T21:59:59+00:00", False),
    ("2026-10-18T22:00:00+00:00", True),
    ("2026-10-13T21:30:00+00:00", False),  # maintenance
    ("2026-10-13T22:00:00+00:00", True),
    ("2026-03-08T22:00:00+00:00", True),  # DST Sunday
    ("2026-11-01T22:00:00+00:00", False),  # standard time Sunday
    ("2026-11-01T23:00:00+00:00", True),
])
def test_energy_market_sessions_and_dst(stamp, opened):
    assert market_calendar.market_session(datetime.fromisoformat(stamp))["open"] is opened


def test_configured_early_closure_and_reopening(monkeypatch):
    start = datetime.fromisoformat("2026-10-13T17:00:00+00:00")
    end = datetime.fromisoformat("2026-10-14T22:00:00+00:00")
    monkeypatch.setattr(market_calendar, "CLOSURES", [(start, end)])
    session = market_calendar.market_session(start + timedelta(hours=1))
    assert not session["open"] and session["next_open_at"] == end
    assert market_calendar.market_session(end)["open"]


def test_closed_market_skips_http_without_changing_health(empty_database, monkeypatch):
    clock = Clock()
    clock.value = datetime.fromisoformat("2026-10-17T12:00:00+00:00").timestamp()
    calls = []
    monkeypatch.setattr(scheduling, "collect_all", lambda provider: calls.append(provider))
    empty_database.update_source_health("live_brent", True, "2026-10-16T20:59:00+00:00")
    before = empty_database.source_health()
    coordinator = CollectionCoordinator(empty_database, now=clock, providers=["yahoo"])
    assert coordinator.collect_now()["status"] == "deferred"
    assert empty_database.source_health() == before
    assert not calls
    assert coordinator.snapshot()[0]["next_collection_at"] == "2026-10-18T22:00:00+00:00"


def test_one_provider_failure_does_not_replace_the_aggregate_health(empty_database):
    clock = Clock()
    stamp = datetime.fromtimestamp(clock(), timezone.utc).isoformat()
    empty_database.update_source_health("fred_brent", True, stamp)
    empty_database.update_source_health("alpha_brent", False, stamp, "temporary failure")
    coordinator = CollectionCoordinator(empty_database, now=clock, providers=["fred", "alpha_vantage"])
    assert coordinator.availability()["status"] == "partial"
    clock.advance(60)
    assert coordinator.availability()["status"] == "partial"
    empty_database.update_source_health("alpha_brent", True, stamp)
    assert coordinator.availability()["status"] == "success"


def test_independent_provider_arrivals_keep_the_canonical_price_for_alerts(empty_database, monkeypatch):
    clock = Clock()
    received = []
    def batch(provider):
        source, code, price = ("EIA/FRED - DCOILBRENTEU", "fred_brent", 95) if provider == "fred" else ("U.S. EIA - PET.RBRTE.D", "eia_brent", 80)
        return CollectionBatch([{"product": "brent", "source": source, "source_code": code, "price": price,
                                 "source_date": "2026-10-12"}], [], [{"code": code, "success": True}])
    monkeypatch.setattr(scheduling, "collect_all", batch)
    monkeypatch.setattr(scheduling, "_evaluate_collection_alerts", lambda database, rows: received.extend(row["price"] for row in rows))
    coordinator = CollectionCoordinator(empty_database, now=clock, providers=["fred", "eia"])
    coordinator.collect_provider("fred")
    result = coordinator.collect_provider("eia")
    assert received == [95, 95]
    assert result["source_comparison"]["mismatch_count"] == 1
    assert "differ" in empty_database.logs(limit=1)[0]["message"]


def test_bls_fallback_requests_share_the_fred_budget(empty_database, monkeypatch):
    clock = Clock()
    calls = []
    def response(request, **kwargs):
        calls.append(request.full_url.split("?")[0])
        if "bls.gov" in request.full_url:
            return io.BytesIO(json.dumps({"status": "REQUEST_NOT_PROCESSED", "message": ["Daily threshold reached"]}).encode())
        return io.BytesIO(b"observation_date,WPU058\n2026-08-01,546.938\n")
    monkeypatch.setattr(collectors, "urlopen", response)
    coordinator = CollectionCoordinator(empty_database, now=clock, providers=["bls"], limits={"bls": 25, "fred": 1})
    assert coordinator.collect_provider("bls")["rows"] == 1
    assert empty_database.request_budget("bls", clock(), 25)["used"] == 1
    assert empty_database.request_budget("fred", clock(), 1)["used"] == 1
    assert len(calls) == 2
    with guarded_requests(coordinator.guard):
        with pytest.raises(RuntimeError, match="quota"):
            collectors._request_text("https://fred.stlouisfed.org/graph/fredgraph.csv?id=WPU058")
    assert len(calls) == 2


def test_bls_quota_does_not_prevent_collection_from_its_fred_mirror(empty_database, monkeypatch):
    clock = Clock()
    empty_database.defer_provider_requests("bls", clock() + 86400, "Daily quota reached")
    calls = []
    monkeypatch.setattr(collectors, "urlopen", lambda request, **kwargs: calls.append(request.full_url) or
                        io.BytesIO(b"observation_date,WPU058\n2026-08-01,546.938\n"))
    coordinator = CollectionCoordinator(empty_database, now=clock, providers=["bls"])
    assert coordinator.collect_now()["rows"] == 1
    assert len(calls) == 1 and "stlouisfed.org" in calls[0]
    health_before = empty_database.source_health()
    # Both transports deferred: no extra HTTP, error log or fake recovery.
    empty_database.defer_provider_requests("fred", clock() + 3600, "Daily quota reached")
    assert coordinator.collect_now()["status"] == "deferred"
    assert len(calls) == 1
    assert empty_database.source_health() == health_before
    assert len(empty_database.logs()) == 1


def test_http_dashboard_is_available_during_initial_collection(empty_database, monkeypatch):
    from price_monitor.http_wsgi import WSGIServer
    from urllib.request import urlopen
    from types import SimpleNamespace
    from price_monitor import app
    empty_database.insert_observation({"product": "brent", "price": 80, "source": "EIA/FRED - DCOILBRENTEU",
                                       "source_date": "2026-10-12"})
    entered, release, bound = threading.Event(), threading.Event(), threading.Event()
    def blocked(provider):
        entered.set()
        assert release.wait(5)
        return CollectionBatch([], [], [])
    monkeypatch.setattr(scheduling, "collect_all", blocked)
    coordinator = CollectionCoordinator(empty_database, providers=["fred"])
    actual_scheduler = CollectionScheduler(empty_database, coordinator)
    servers = []
    def server_factory(*args):
        server = WSGIServer(("127.0.0.1", 0), app.Handler)
        servers.append(server)
        bound.set()
        return server
    monkeypatch.setattr(app, "database", empty_database)
    monkeypatch.setattr(app, "scheduler", actual_scheduler)
    monkeypatch.setattr(app, "connectivity", SimpleNamespace(start=lambda: None, stop=lambda: None))
    monkeypatch.setattr(app, "validate_security_configuration", lambda: None)
    monkeypatch.setattr(app, "WSGIServer", server_factory)
    thread = threading.Thread(target=app.main, daemon=True)
    try:
        thread.start()
        assert bound.wait(2) and entered.wait(2)
        url = f"http://127.0.0.1:{servers[0].server_port}/api/dashboard"
        with urlopen(url, timeout=1) as response:
            saved = json.loads(response.read())
        assert saved["latest"][0]["price"] == 80
        with urlopen(f"http://127.0.0.1:{servers[0].server_port}/api/collection-schedules", timeout=1) as response:
            schedules = json.loads(response.read())["providers"]
        assert next(row for row in schedules if row["provider"] == "fred")["status"] == "running"
        assert not release.is_set()
    finally:
        release.set()
        if servers:
            servers[0].shutdown()
        thread.join(3)
        actual_scheduler.stop()
        for worker in actual_scheduler._workers:
            worker.join(3)
        if actual_scheduler._thread:
            actual_scheduler._thread.join(3)

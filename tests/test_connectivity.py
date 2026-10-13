from io import BytesIO
from urllib.error import HTTPError, URLError

from price_monitor.services import connectivity as connectivity_module


def test_reachability_uses_a_second_provider_when_the_first_is_unreachable(monkeypatch):
    calls = []

    def request(request, timeout):
        calls.append((request.full_url, request.method, timeout))
        if len(calls) == 1:
            raise URLError("DNS unavailable")
        return BytesIO()

    monkeypatch.setattr(connectivity_module, "urlopen", request)

    assert connectivity_module.internet_reachable() is True
    assert len(calls) == 2
    assert all(method == "HEAD" and timeout == 2 for _, method, timeout in calls)


def test_http_refusal_proves_connectivity_and_closes_the_response(monkeypatch):
    body = BytesIO()

    def request(*args, **kwargs):
        raise HTTPError("https://provider.example/", 429, "Too Many Requests", {}, body)

    monkeypatch.setattr(connectivity_module, "urlopen", request)

    assert connectivity_module.internet_reachable() is True
    assert body.closed


def test_unreachable_providers_report_offline(monkeypatch):
    def request(*args, **kwargs):
        raise TimeoutError("network unavailable")

    monkeypatch.setattr(connectivity_module, "urlopen", request)

    assert connectivity_module.internet_reachable() is False


def test_monitor_announces_loss_once_and_waits_for_collection_after_recovery(monkeypatch, caplog):
    outcomes = iter([False, False, True, True, False])
    monkeypatch.setattr(connectivity_module, "internet_reachable", lambda: next(outcomes))
    notifier = connectivity_module.WebsiteStatus()
    monitor = connectivity_module.ConnectivityMonitor(notifier)
    assert monitor.snapshot() == {"status": "checking", "checked_at": None, "changed_at": None}

    with caplog.at_level("INFO"):
        monitor.check()
        first = monitor.snapshot()
        monitor.check()
        repeated = monitor.snapshot()
        assert first["changed_at"] == repeated["changed_at"]
        assert repeated["checked_at"] >= first["checked_at"]
        monitor.check()
        assert monitor.snapshot()["status"] == "online"
        monitor.check()
        assert [record.getMessage() for record in caplog.records] == ["no collections , try again"]
        notifier.record_collection(True, notifier.begin_collection())
        notifier.record_collection(True, notifier.begin_collection())
        monitor.check()

    assert [record.getMessage() for record in caplog.records] == [
        "no collections , try again",
        "the website is up to date",
        "no collections , try again",
    ]
    assert monitor.snapshot()["status"] == "offline"


def test_monitor_starts_once_and_can_stop_without_waiting_for_the_next_interval(monkeypatch):
    checked = connectivity_module.threading.Event()
    monkeypatch.setattr(connectivity_module, "internet_reachable", lambda: checked.set() or True)
    monitor = connectivity_module.ConnectivityMonitor(connectivity_module.WebsiteStatus())
    monitor.start()
    try:
        assert checked.wait(1)
        thread = monitor._thread
        monitor.start()
        assert monitor._thread is thread
    finally:
        monitor.stop()
        monitor._thread.join(timeout=1)
    assert not monitor._thread.is_alive()


def test_probe_success_does_not_announce_api_success_before_a_collection(caplog):
    notifier = connectivity_module.WebsiteStatus()
    with caplog.at_level("INFO"):
        notifier.record_internet(True)
        notifier.record_internet(True)
        assert caplog.records == []
        notifier.record_collection(True, notifier.begin_collection())
        notifier.record_collection(True, notifier.begin_collection())
    assert [record.getMessage() for record in caplog.records] == ["the website is up to date"]


def test_internet_and_api_failures_share_one_notice_and_stale_requests_cannot_recover(caplog):
    notifier = connectivity_module.WebsiteStatus()
    with caplog.at_level("INFO"):
        notifier.record_internet(True)
        notifier.record_collection(True, notifier.begin_collection())
        old_generation = notifier.begin_collection()
        notifier.record_internet(False)
        notifier.record_collection(False, old_generation)
        notifier.record_internet(False)
        notifier.record_internet(True)
        notifier.record_collection(True, old_generation)
        notifier.record_collection(False, notifier.begin_collection())
        assert [record.getMessage() for record in caplog.records] == [
            "the website is up to date", "no collections , try again",
        ]
        notifier.record_collection(True, notifier.begin_collection())
    assert [record.getMessage() for record in caplog.records] == [
        "the website is up to date", "no collections , try again", "the website is up to date",
    ]


def test_a_collection_cannot_announce_success_while_internet_is_offline(caplog):
    notifier = connectivity_module.WebsiteStatus()
    with caplog.at_level("INFO"):
        notifier.record_internet(False)
        notifier.record_collection(True, notifier.begin_collection())
        notifier.record_internet(True)
        assert [record.getMessage() for record in caplog.records] == ["no collections , try again"]
        notifier.record_collection(True, notifier.begin_collection())
    assert caplog.records[-1].getMessage() == "the website is up to date"

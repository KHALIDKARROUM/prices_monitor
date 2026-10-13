from __future__ import annotations

import pytest

from price_monitor.services import collectors as collectors_module
from price_monitor.services import scheduler as scheduler_module
from price_monitor.services.connectivity import WebsiteStatus


@pytest.fixture(autouse=True)
def disable_live_quotes_for_isolated_collector_tests(monkeypatch):
    monkeypatch.setattr(collectors_module, "LIVE_QUOTES_ENABLED", False)
    monkeypatch.setattr(scheduler_module, "website_status", WebsiteStatus())
    monkeypatch.setattr(scheduler_module, "DEMO_MODE", False)


def gasoil_observation():
    return {
        "product": "gasoil",
        "price": 2.4,
        "unit": "USD/gallon",
        "source": "FRED test source",
        "source_date": "2026-09-13",
        "collected_at": "2026-09-13T12:00:00+00:00",
    }


def bitumen_benchmark() -> dict:
    return {
        "code": "bls_wpu058",
        "product": "bitume",
        "label": "Asphalt PPI",
        "value": 546.938,
        "unit": "indice (déc. 1984 = 100)",
        "geography": "États-Unis",
        "source": "U.S. BLS Public Data API - WPU058",
        "source_code": "bls_asphalt_ppi",
        "source_date": "2026-08-01",
    }


def test_collect_all_keeps_successful_source_when_another_source_fails(monkeypatch):
    monkeypatch.setattr(collectors_module, "DEMO_MODE", False)
    monkeypatch.setattr(collectors_module, "collect_gasoil", gasoil_observation)
    monkeypatch.setattr(collectors_module, "collect_bitumen_benchmark", bitumen_benchmark)

    def failing_brent():
        raise RuntimeError("Brent service unavailable")

    monkeypatch.setattr(collectors_module, "collect_brent", failing_brent)

    observations, messages = collectors_module.collect_all()

    assert observations == [gasoil_observation()]
    assert messages == ["Brent service unavailable"]


def test_collect_bitumen_benchmark_keeps_index_separate_from_price(monkeypatch):
    monkeypatch.setattr(
        collectors_module,
        "_request_json",
        lambda url: {
            "status": "REQUEST_SUCCEEDED",
            "Results": {"series": [{"data": [{"year": "2026", "period": "M08", "value": "546.938"}]}]},
        },
    )

    result = collectors_module.collect_bitumen_benchmark()

    assert result["product"] == "bitume"
    assert result["value"] == 546.938
    assert result["unit"].startswith("indice")
    assert result["source_date"] == "2026-08-01"
    assert "USD/tonne" in result["notes"]


def test_public_fred_csv_fallback_collects_latest_prices_without_an_api_key(monkeypatch):
    monkeypatch.setattr(collectors_module, "FRED_API_KEY", "")
    monkeypatch.setattr(collectors_module, "ALPHA_VANTAGE_API_KEY", "")
    monkeypatch.setattr(
        collectors_module,
        "_request_text",
        lambda url: (
            "observation_date,DDFUELNYH\n2026-09-10,.\n2026-09-11,2.543\n"
            if "DDFUELNYH" in url
            else "observation_date,DCOILBRENTEU\n2026-09-10,67.10\n2026-09-11,68.25\n"
        ),
    )

    gasoil = collectors_module.collect_gasoil()
    brent = collectors_module.collect_brent()

    assert (gasoil["price"], gasoil["source_date"], gasoil["notes"]) == (2.543, "2026-09-11", "Collecte FRED publique")
    assert (brent["price"], brent["source_date"], brent["notes"]) == (68.25, "2026-09-11", "Collecte FRED publique")


def test_direct_eia_connectors_read_their_own_series(monkeypatch):
    monkeypatch.setattr(collectors_module, "EIA_API_KEY", "test-key")
    monkeypatch.setattr(
        collectors_module,
        "_request_json",
        lambda url: {"response": {"data": [{"period": "2026-09-21", "value": "73.45"}]}},
    )

    diesel = collectors_module.collect_eia_gasoil()
    brent = collectors_module.collect_eia_brent()

    assert (diesel["source_code"], diesel["price"], diesel["source_date"]) == ("eia_diesel", 73.45, "2026-09-21")
    assert (brent["source_code"], brent["price"], brent["source_date"]) == ("eia_brent", 73.45, "2026-09-21")


def test_public_csv_chooses_latest_valid_value_even_when_rows_are_reversed(monkeypatch):
    monkeypatch.setattr(collectors_module, "FRED_API_KEY", "")
    monkeypatch.setattr(collectors_module, "_request_text", lambda url: "observation_date,DCOILBRENTEU\n9999-01-01,999\n2026-09-15,nan\n2026-09-14,inf\n2026-09-13,-1\nnot-a-date,88\n2026-09-12,75\n2026-09-11,70\n")
    result = collectors_module.collect_brent()
    assert (result["source_date"], result["price"]) == ("2026-09-12", 75)


def test_eia_requests_newest_dates_and_skips_invalid_latest_rows(monkeypatch):
    from urllib.parse import parse_qs, urlparse

    monkeypatch.setattr(collectors_module, "EIA_API_KEY", "test-key")
    urls = []
    def response(url):
        urls.append(url)
        return {"response": {"data": [{"period": "2026-09-11", "value": "70"}, {"period": "2026-09-13", "value": "nan"}, {"period": "2026-09-12", "value": "75"}]}}
    monkeypatch.setattr(collectors_module, "_request_json", response)
    result = collectors_module.collect_eia_brent()
    assert (result["source_date"], result["price"]) == ("2026-09-12", 75)
    query = parse_qs(urlparse(urls[0]).query)
    assert query["sort[0][column]"] == ["period"] and query["sort[0][direction]"] == ["desc"]


def test_alpha_selects_latest_valid_observation_without_relying_on_response_order(monkeypatch):
    monkeypatch.setattr(collectors_module, "ALPHA_VANTAGE_API_KEY", "test-key")
    monkeypatch.setattr(collectors_module, "_request_json", lambda url: {"data": [{"date": "2026-09-11", "value": "70"}, {"date": "2026-09-13", "value": "."}, {"date": "2026-09-12", "value": "75"}]})
    result = collectors_module.collect_alpha_brent()
    assert (result["source_date"], result["price"]) == ("2026-09-12", 75)


def test_live_quote_connectors_keep_market_timestamp_and_proxy_label(monkeypatch):
    monkeypatch.setattr(
        collectors_module,
        "_request_json",
        lambda url: {"chart": {"result": [{"meta": {"regularMarketPrice": 98.58, "regularMarketTime": 1790118281, "exchangeDataDelayedBy": 10}}]}},
    )

    brent = collectors_module.collect_live_brent()
    gasoil = collectors_module.collect_live_gasoil()

    assert (brent["source_code"], brent["price"]) == ("live_brent", 98.58)
    assert brent["source_date"] == "2026-09-22"
    assert gasoil["source_code"] == "live_gasoil_proxy"
    assert "proxy" in gasoil["source"].lower()


def test_partial_collection_is_persisted_and_logged_as_partial(empty_database, monkeypatch):
    monkeypatch.setattr(
        scheduler_module,
        "collect_all",
        lambda: ([gasoil_observation()], ["Brent service unavailable"]),
    )

    result = scheduler_module.run_collection(empty_database)

    assert {key: result[key] for key in ("status", "rows", "message")} == {
        "status": "partial",
        "rows": 1,
        "message": "Brent service unavailable",
    }
    assert result["source_comparison"]["status"] == "unverified"
    assert result["source_comparison"]["warnings"] == []
    assert len(empty_database.observations(days=3650)) == 1
    log = empty_database.logs(limit=1)[0]
    assert log["status"] == "partial"
    assert log["rows_collected"] == 1
    assert log["message"] == "Brent service unavailable"


def test_failed_collection_is_logged_without_persisting_rows(empty_database, monkeypatch):
    def failing_collection():
        raise RuntimeError("All market sources unavailable")

    monkeypatch.setattr(scheduler_module, "collect_all", failing_collection)

    result = scheduler_module.run_collection(empty_database)

    assert {key: result[key] for key in ("status", "rows", "message")} == {
        "status": "error",
        "rows": 0,
        "message": "All market sources unavailable",
    }
    assert result["source_comparison"]["status"] == "unverified"
    assert result["source_comparison"]["comparable_pair_count"] == 0
    assert empty_database.observations(days=3650) == []
    log = empty_database.logs(limit=1)[0]
    assert log["status"] == "error"
    assert log["rows_collected"] == 0
    assert log["message"] == "All market sources unavailable"


def test_repeated_collection_errors_are_saved_without_repeating_console_alerts(empty_database, monkeypatch, caplog):
    def failing_collection():
        raise RuntimeError("All market sources unavailable")

    monkeypatch.setattr(scheduler_module, "collect_all", failing_collection)
    with caplog.at_level("INFO"):
        scheduler_module.run_collection(empty_database)
        scheduler_module.run_collection(empty_database)
        scheduler_module.run_collection(empty_database)
        monkeypatch.setattr(scheduler_module, "collect_all", lambda: ([gasoil_observation()], []))
        scheduler_module.run_collection(empty_database)
        monkeypatch.setattr(scheduler_module, "collect_all", failing_collection)
        scheduler_module.run_collection(empty_database)

    assert [record.getMessage() for record in caplog.records] == [
        "no collections , try again", "the website is up to date", "no collections , try again",
    ]
    assert [log["status"] for log in empty_database.logs()] == ["error", "success", "error", "error", "error"]


def test_repeated_successful_and_partial_collections_announce_once(empty_database, monkeypatch, caplog):
    monkeypatch.setattr(scheduler_module, "collect_all", lambda: ([gasoil_observation()], []))
    with caplog.at_level("INFO"):
        for _ in range(3):
            scheduler_module.run_collection(empty_database)
        monkeypatch.setattr(
            scheduler_module, "collect_all", lambda: ([gasoil_observation()], ["Brent service unavailable"]),
        )
        for _ in range(3):
            scheduler_module.run_collection(empty_database)
        monkeypatch.setattr(scheduler_module, "collect_all", lambda: ([gasoil_observation()], []))
        scheduler_module.run_collection(empty_database)
    assert [record.getMessage() for record in caplog.records] == ["the website is up to date"]
    assert len(empty_database.logs()) == 7
    assert sum(log["status"] == "partial" for log in empty_database.logs()) == 3


def test_restart_announces_status_even_when_the_previous_saved_collection_succeeded(empty_database, monkeypatch, caplog):
    monkeypatch.setattr(scheduler_module, "collect_all", lambda: ([gasoil_observation()], []))
    scheduler_module.run_collection(empty_database)
    monkeypatch.setattr(scheduler_module, "website_status", WebsiteStatus())
    caplog.clear()
    with caplog.at_level("INFO"):
        scheduler_module.run_collection(empty_database)
    assert [record.getMessage() for record in caplog.records] == ["the website is up to date"]


@pytest.mark.parametrize("demo_mode, observations", [(False, []), (True, [gasoil_observation()])])
def test_empty_or_demo_collections_do_not_announce_api_success(empty_database, monkeypatch, caplog, demo_mode, observations):
    monkeypatch.setattr(scheduler_module, "DEMO_MODE", demo_mode)
    monkeypatch.setattr(scheduler_module, "collect_all", lambda: (observations, []))
    with caplog.at_level("INFO"):
        scheduler_module.run_collection(empty_database)
        scheduler_module.run_collection(empty_database)
    assert [record.getMessage() for record in caplog.records] == ["no collections , try again"]


def test_scheduler_persists_bitumen_benchmark_in_its_separate_table(empty_database, monkeypatch):
    monkeypatch.setattr(
        scheduler_module,
        "collect_all",
        lambda: collectors_module.CollectionBatch(
            [],
            [],
            [{"code": "bls_asphalt_ppi", "success": True, "error": None}],
            [bitumen_benchmark()],
        ),
    )

    result = scheduler_module.run_collection(empty_database)

    assert result["status"] == "success"
    assert result["rows"] == 1
    assert result["benchmarks"] == 1
    assert empty_database.latest_benchmarks("bitume")[0]["value"] == 546.938
    readings = empty_database.observations(product="bitume", days=3650)
    assert len(readings) == 1 and readings[0]["data_kind"] == "index"
    assert empty_database.latest() == []


def test_collection_updates_source_health_and_quality_score(empty_database, monkeypatch):
    monkeypatch.setattr(
        scheduler_module,
        "collect_all",
        lambda: collectors_module.CollectionBatch(
            [gasoil_observation()],
            ["Brent service unavailable"],
            [
                {"code": "fred_diesel", "success": True, "error": None},
                {"code": "fred_brent", "success": False, "error": "Brent service unavailable"},
            ],
        ),
    )

    result = scheduler_module.run_collection(empty_database)

    assert result["status"] == "partial"
    health = {row["code"]: row for row in empty_database.source_health()}
    assert health["fred_diesel"]["last_success_at"]
    assert health["fred_diesel"]["last_error"] is None
    assert health["fred_brent"]["last_success_at"] is None
    assert health["fred_brent"]["last_error"] == "Brent service unavailable"
    assert empty_database.dashboard(days=30)["quality"]["score"] < 100


def test_successful_collection_clears_previous_source_error(empty_database):
    empty_database.update_source_health(
        "fred_diesel", False, "2026-09-13T11:00:00+00:00", "temporary outage"
    )
    empty_database.update_source_health("fred_diesel", True, "2026-09-13T12:00:00+00:00")

    source = next(row for row in empty_database.source_health() if row["code"] == "fred_diesel")
    assert source["last_success_at"] == "2026-09-13T12:00:00+00:00"
    assert source["last_error"] is None


def test_scheduler_collects_immediately_when_the_service_starts(empty_database, monkeypatch):
    import threading
    called = threading.Event()
    coordinator = scheduler_module.CollectionCoordinator(empty_database, providers=["fred"])
    def fake_collection(provider):
        called.set()
        return {"next_collection_at": "2099-01-01T00:00:00+00:00"}
    monkeypatch.setattr(coordinator, "collect_provider", fake_collection)
    scheduler = scheduler_module.CollectionScheduler(empty_database, coordinator)
    try:
        scheduler.start()
        assert called.wait(2)
    finally:
        scheduler.stop()
        for worker in scheduler._workers:
            worker.join(2)
        scheduler._thread.join(2)


def test_collect_all_fails_when_no_source_succeeds_and_demo_mode_is_off(monkeypatch):
    monkeypatch.setattr(collectors_module, "DEMO_MODE", False)
    monkeypatch.setattr(
        collectors_module,
        "collect_bitumen_benchmark",
        lambda: (_ for _ in ()).throw(RuntimeError("bitumen benchmark down")),
    )
    monkeypatch.setattr(
        collectors_module,
        "collect_gasoil",
        lambda: (_ for _ in ()).throw(RuntimeError("diesel down")),
    )
    monkeypatch.setattr(
        collectors_module,
        "collect_brent",
        lambda: (_ for _ in ()).throw(RuntimeError("brent down")),
    )

    with pytest.raises(RuntimeError, match="Aucune source publique disponible"):
        collectors_module.collect_all()

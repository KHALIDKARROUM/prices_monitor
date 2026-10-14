from __future__ import annotations

import csv
import io
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from price_monitor import database as database_module
from price_monitor.bitumen import INDEX_UNIT, benchmark_payload
from price_monitor.database import Database
from price_monitor.services import collectors
from price_monitor.services.alerts import AlertManager
from price_monitor.services.export import csv_bytes
from price_monitor.services.forecasting import build_forecast
from price_monitor.services.comparison import compare_api_prices


def test_bitumen_snapshot_is_visible_everywhere_and_imports_idempotently(tmp_path, monkeypatch):
    snapshot = Path(__file__).resolve().parents[1] / "data/processed/market_prices.csv"
    monkeypatch.setattr(database_module, "REAL_DATA_PATH", snapshot)
    monkeypatch.setattr(database_module, "DEMO_MODE", False)
    path = tmp_path / "bitume.db"
    db = Database(path)
    market = db.dashboard(days=30)
    assert market["metrics"]["bitume"]["current"] > 0
    assert market["metrics"]["bitume"]["unit"] == INDEX_UNIT
    assert market["metrics"]["bitume"]["data_kind"] == "index"
    assert market["metrics"]["bitume"]["count"] >= 1
    assert all(row["data_kind"] == "index" for row in market["series"]["bitume"])
    assert any(row["product"] == "bitume" for row in market["latest"])
    assert not any(row["product"] == "bitume" for row in db.latest())
    history = db.history(product="bitume", page_size=100)
    assert history["total"] == 60
    monthly_pages = [db.history(product="bitume", data_kind="index", page=page, page_size=12) for page in range(1, 6)]
    assert all(page["total"] == 60 and page["pages"] == 5 for page in monthly_pages)
    months = [row["source_date"] for page in monthly_pages for row in page["items"]]
    assert len(set(months)) == 60
    assert months[0] == "2026-08-01" and months[-1] == "2021-09-01"
    assert all(row["currency"] == "" for row in history["items"])
    exported = list(csv.DictReader(io.StringIO(csv_bytes(history["items"]).decode("utf-8-sig"))))
    assert len(exported) == 60 and exported[0]["data_kind"] == "index"
    forecast = build_forecast(db)["products"]["bitume"]
    assert forecast["status"] == "ready"
    assert 30 <= forecast["history_count"] <= 60
    assert forecast["horizon_unit"] == "months"
    assert forecast["unit"] == INDEX_UNIT
    origin = datetime.fromisoformat(forecast["forecast_origin"])
    for horizon in (1, 3, 6):
        assert len(forecast["forecasts"][str(horizon)]) == horizon
        month_index = origin.year * 12 + origin.month - 1 + horizon
        assert forecast["forecasts"][str(horizon)][-1]["date"] == f"{month_index // 12:04d}-{month_index % 12 + 1:02d}-01"
    assert forecast["backtest"]["3"]["available"]
    for horizon, selection in forecast["model_selection"].items():
        assert selection["selected_model"] == "naive"
        assert set(selection["candidates"]) == {"naive", "holt"}
        assert {
            "selection_unavailable",
            "limited_evaluation",
            "uncalibrated_intervals",
            "limited_monthly_uncertainty_history",
        } <= {warning["code"] for warning in selection["warnings"]}
        assert all(point["value"] == forecast["last_actual"]["value"] for point in forecast["forecasts"][horizon])
        assert all(point["lower"] is None and point["upper"] is None for point in forecast["forecasts"][horizon])
    assert Database(path).history(product="bitume")["total"] == 60


def test_index_and_supplier_prices_stay_separate_in_comparisons_and_budget(empty_database):
    db = empty_database
    for day, value in [("2026-06-01", 500), ("2026-07-01", 550)]:
        db.insert_benchmark(benchmark_payload(day, value))
        db.insert_observation({"product": "bitume", "source": "Devis ABC", "supplier": "ABC", "source_date": day, "price": value + 200})
    assert db.history(product="bitume", data_kind="index")["total"] == 2
    assert db.history(product="bitume", data_kind="quote")["total"] == 2
    assert db.history(product="bitume", supplier="ABC")["total"] == 2
    compared = db.compare_periods("2026-06-01", "2026-06-30", "2026-07-01", "2026-07-31", product="bitume")["products"]
    assert compared["bitume"]["period_a"]["average"] == 500
    assert compared["bitume"]["unit"] == INDEX_UNIT
    assert compared["bitume_quotes"]["period_a"]["average"] == 700
    assert compared["bitume_quotes"]["unit"] == "USD/tonne"
    assert db.latest()[0]["price"] == 750
    purchase = db.create_procurement({"product": "bitume", "supplier": "ABC", "quantity": 10, "unit_price": 760, "currency": "USD"})
    assert purchase["market_price_usd"] == 750
    assert purchase["price_impact_unit_usd"] == 10
    # The main market card shows the index while still exposing the quote.
    card = db.dashboard(days=365)["metrics"]["bitume"]
    assert card["current"] == 550 and card["supplier_quote"]["price"] == 750


def test_bls_rate_limit_uses_real_fred_mirror_and_keeps_provenance(monkeypatch):
    monkeypatch.setattr(collectors, "_request_json", lambda url: {"status": "REQUEST_NOT_PROCESSED", "message": ["Daily threshold reached"]})
    monkeypatch.setattr(collectors, "_request_text", lambda url: "observation_date,WPU058\n2026-07-01,557.994\n2026-08-01,546.938\n2026-09-01,.\n")
    result = collectors.collect_bitumen_benchmark()
    assert result["value"] == 546.938
    assert result["source_date"] == "2026-08-01"
    assert result["source"] == "BLS/FRED - WPU058"
    assert result["unit"] == INDEX_UNIT
    assert "Daily threshold reached" in result["notes"]
    source = next(row for row in compare_api_prices([], [result])["sources"] if row["product"] == "bitume")
    assert source["price"] == 546.938 and source["provider"] == "BLS via FRED"
    assert source["currency"] == ""


def test_bls_quota_pauses_direct_requests_but_keeps_fetching_fresh_mirror_data(monkeypatch):
    clock = [1000.0]
    requests = []
    mirrors = []
    monkeypatch.setattr(collectors.time, "monotonic", lambda: clock[0])
    def direct(url):
        requests.append(url)
        if len(requests) == 1:
            return {"status": "REQUEST_NOT_PROCESSED", "message": ["Daily threshold reached"]}
        return {"status": "REQUEST_SUCCEEDED", "Results": {"series": [{"data": [{"year": "2026", "period": "M08", "value": "550"}]}]}}
    def mirror(url):
        mirrors.append(url)
        return f"observation_date,WPU058\n2026-08-01,{546 + len(mirrors)}\n"
    monkeypatch.setattr(collectors, "_request_json", direct)
    monkeypatch.setattr(collectors, "_request_text", mirror)
    first = collectors.collect_bitumen_benchmark()
    clock[0] += 60
    second = collectors.collect_bitumen_benchmark()
    assert len(requests) == 1 and len(mirrors) == 2
    assert first["value"] == 547 and second["value"] == 548
    assert second["source"] == "BLS/FRED - WPU058"
    clock[0] += 24 * 3600
    recovered = collectors.collect_bitumen_benchmark()
    assert len(requests) == 2
    assert recovered["source"] == "U.S. BLS Public Data API - WPU058" and recovered["value"] == 550


def test_successful_bls_queries_are_spaced_six_hours_apart(monkeypatch):
    clock = [1000.0]
    requests = []
    monkeypatch.setattr(collectors.time, "monotonic", lambda: clock[0])
    def direct(url):
        requests.append(url)
        return {"status": "REQUEST_SUCCEEDED", "Results": {"series": [{"data": [{"year": "2026", "period": "M08", "value": "546.938"}]}]}}
    monkeypatch.setattr(collectors, "_request_json", direct)
    monkeypatch.setattr(collectors, "_request_text", lambda url: "observation_date,WPU058\n2026-08-01,546.938\n")
    assert collectors.collect_bitumen_benchmark()["source"] == "U.S. BLS Public Data API - WPU058"
    for _ in range(20):
        clock[0] += 60
        assert collectors.collect_bitumen_benchmark()["source"] == "BLS/FRED - WPU058"
    assert len(requests) == 1
    clock[0] += 6 * 3600
    collectors.collect_bitumen_benchmark()
    assert len(requests) == 2


def test_latest_bls_month_is_selected_and_annual_or_invalid_values_ignored(monkeypatch):
    monkeypatch.setattr(collectors, "_request_json", lambda url: {
        "status": "REQUEST_SUCCEEDED", "Results": {"series": [{"data": [
            {"year": "2026", "period": "M06", "value": "500"},
            {"year": "2026", "period": "M13", "value": "999"},
            {"year": "2026", "period": "M09", "value": "nan"},
            {"year": "2026", "period": "M08", "value": "546.938"},
        ]}]}})
    assert collectors.collect_bitumen_benchmark()["source_date"] == "2026-08-01"


def test_index_revision_is_single_month_in_history_and_variation_uses_previous_month(empty_database):
    db = empty_database
    db.insert_benchmark(benchmark_payload("2026-06-01", 500))
    db.insert_benchmark(benchmark_payload("2026-07-01", 550))
    db.insert_benchmark(benchmark_payload("2026-06-01", 510))
    assert db.history(product="bitume")["total"] == 2
    revision = db.insert_benchmark(benchmark_payload("2026-07-01", 560))
    assert revision["variation"] == 50
    assert db.history(product="bitume")["items"][0]["price"] == 560
    assert db.history(product="bitume")["items"][0]["variation"] == 50


def test_index_alert_does_not_use_or_trigger_supplier_price_rules(empty_database):
    db = empty_database
    quote_rule = db.create_alert_rule({"product": "bitume", "direction": "above", "threshold": 600, "channel": "email", "basis": "quote", "muted": True})
    index_rule = db.create_alert_rule({"product": "bitume", "direction": "above", "threshold": 540, "channel": "email", "basis": "index", "muted": True})
    manager = AlertManager(db)
    events = manager.evaluate_prices([{"product": "bitume", "price": 650, "data_kind": "index"}], dispatch=False)
    assert len(events) == 1 and events[0].kind == "index_threshold"
    assert INDEX_UNIT in events[0].message
    assert db.get_alert_state(quote_rule["alert_key"]) is None
    assert db.get_alert_state(index_rule["alert_key"])["active"]
    events = manager.evaluate_prices([{"product": "bitume", "price": 650, "unit": "USD/tonne"}], dispatch=False)
    assert len(events) == 1 and events[0].kind == "price_threshold"
    assert "USD/tonne" in events[0].message


def test_index_alert_basis_rejected_for_other_products(empty_database):
    with pytest.raises(ValueError, match="Index basis"):
        empty_database.create_alert_rule({"product": "brent", "basis": "index", "direction": "above", "threshold": 540})


def test_short_period_anchor_preserves_real_source_month(empty_database):
    month = (datetime.now(timezone.utc).date().replace(day=1) - timedelta(days=1)).replace(day=1)
    empty_database.insert_benchmark(benchmark_payload(month.isoformat(), 500))
    row = empty_database.dashboard(days=7)["series"]["bitume"][0]
    assert row["source_date"] == month.isoformat()
    assert row["is_period_anchor"] is True
    assert row["chart_date"] > row["source_date"]

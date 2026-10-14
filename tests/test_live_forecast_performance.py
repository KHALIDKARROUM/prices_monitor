from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

import pytest

from price_monitor.services.forecasting import build_forecast


def observation(instrument: str, price: float, source_date: str) -> dict:
    sources = {
        "brent_spot": "EIA/FRED - DCOILBRENTEU",
        "brent_futures": "Yahoo Finance - Brent futures BZ=F",
    }
    return {
        "product": "brent", "instrument": instrument, "source": sources[instrument],
        "price": price, "source_date": source_date,
        "collected_at": f"{source_date}T16:00:00+00:00",
    }


def issued_forecast(**changes) -> dict:
    return {
        "product": "brent", "instrument": "brent_spot", "horizon": 7,
        "horizon_unit": "calendar_days", "model": "holt", "model_version": "test-model-v1",
        "origin_date": "2026-01-01", "target_date": "2026-01-08",
        "issued_at": "2026-01-01T16:00:00+00:00", "prediction": 110,
        "baseline_prediction": 100, "lower_bound": 105, "upper_bound": 115,
        **changes,
    }


def test_live_forecast_ledger_keeps_issue_and_scores_only_the_same_instrument(empty_database):
    first = empty_database.record_live_forecast(issued_forecast())
    duplicate = empty_database.record_live_forecast(issued_forecast(issued_at="2026-01-02T16:00:00+00:00"))
    assert duplicate["id"] == first["id"]
    assert duplicate["issued_at"] == first["issued_at"]

    # A futures quote on the target day cannot resolve a spot forecast.
    empty_database.insert_observation(observation("brent_futures", 108, "2026-01-08"))
    pending = empty_database.live_forecast_performance(product="brent", instrument="brent_spot", horizon=7)
    assert pending["metrics"]["samples"] == 0
    assert pending["metrics"]["pending"] == 1

    empty_database.insert_observation(observation("brent_spot", 108, "2026-01-08"))
    performance = empty_database.live_forecast_performance(product="brent", instrument="brent_spot", horizon=7)
    metrics = performance["metrics"]
    assert metrics["samples"] == 1
    assert metrics["mae"] == pytest.approx(2)
    assert metrics["bias"] == pytest.approx(2)
    assert metrics["naive_mae"] == pytest.approx(8)
    assert metrics["improvement_over_baseline"] == pytest.approx(.75)
    assert metrics["interval_coverage"] == 1
    assert metrics["interval_samples"] == 1
    assert metrics["interval_status"] == "limited_sample"
    assert metrics["interval_target_coverage"] == .95
    assert performance["records"][0]["actual_source_date"] == "2026-01-08"
    assert performance["records"][0]["actual_value"] == 108


def test_live_forecast_reconciliation_uses_first_publication_within_daily_grace(empty_database):
    empty_database.record_live_forecast(issued_forecast(target_date="2026-02-08", horizon=30))
    # Simulate a process that was stopped while the observation arrived.
    with empty_database.connect() as conn:
        conn.execute(
            """INSERT INTO price_observations
            (product, instrument, price, unit, currency, source, supplier, source_date, collected_at,
             variation, variation_pct, is_unchanged, notes)
            VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            ("brent", "brent_spot", 106, "USD/baril", "USD", "EIA/FRED - DCOILBRENTEU", "",
             "2026-02-10", "2026-02-10T16:00:00+00:00", None, None, 0, ""),
        )
    assert empty_database.reconcile_live_forecasts() == 1
    record = empty_database.live_forecast_performance(product="brent", horizon=30)["records"][0]
    assert record["actual_source_date"] == "2026-02-10"
    assert record["error"] == pytest.approx(4)


def test_reconciliation_replaces_an_early_lower_priority_provider(empty_database):
    empty_database.record_live_forecast(issued_forecast())
    lower_priority = observation("brent_spot", 120, "2026-01-08")
    lower_priority["source"] = "Alpha Vantage - BRENT"
    empty_database.insert_observation(lower_priority)
    assert empty_database.live_forecast_performance(product="brent", horizon=7)["records"][0]["actual_value"] == 120
    empty_database.insert_observation(observation("brent_spot", 108, "2026-01-08"))
    empty_database.reconcile_live_forecasts()
    record = empty_database.live_forecast_performance(product="brent", horizon=7)["records"][0]
    assert record["actual_value"] == 108
    assert record["error"] == pytest.approx(2)


def test_monthly_index_forecast_requires_the_target_month(empty_database):
    empty_database.record_live_forecast({
        "product": "bitume", "instrument": "asphalt_index", "horizon": 1, "horizon_unit": "months",
        "model": "naive", "model_version": "test-model-v1", "origin_date": "2026-01-01",
        "target_date": "2026-02-01", "issued_at": "2026-01-02T00:00:00+00:00",
        "prediction": 200, "baseline_prediction": 198,
    })
    empty_database.insert_benchmark({
        "code": "bls_wpu058", "product": "bitume", "label": "Bitumen index", "value": 205,
        "unit": "indice (déc. 1984 = 100)", "source": "U.S. BLS Public Data API - WPU058",
        "source_date": "2026-03-01", "collected_at": "2026-03-15T00:00:00+00:00",
    })
    assert empty_database.live_forecast_performance(product="bitume", horizon=1)["metrics"]["samples"] == 0
    empty_database.insert_benchmark({
        "code": "bls_wpu058", "product": "bitume", "label": "Bitumen index", "value": 202,
        "unit": "indice (déc. 1984 = 100)", "source": "U.S. BLS Public Data API - WPU058",
        "source_date": "2026-02-01", "collected_at": "2026-03-15T00:00:00+00:00",
    })
    performance = empty_database.live_forecast_performance(product="bitume", instrument="asphalt_index", horizon=1)
    assert performance["metrics"]["samples"] == 1
    assert performance["records"][0]["actual_value"] == 202


def test_build_forecast_persists_each_selected_endpoint_once(empty_database):
    today = date.today()
    for offset in range(90, 0, -1):
        source_date = (today - timedelta(days=offset)).isoformat()
        empty_database.insert_observation(observation("brent_spot", 80 + (90 - offset) * .05, source_date))

    first = build_forecast(empty_database)["products"]["brent"]
    assert first["status"] == "ready"
    for horizon in (7, 30, 90):
        performance = first["live_performance"]["horizons"][str(horizon)]
        assert performance["metrics"]["pending"] == 1
        assert performance["records"] == []

    second = build_forecast(empty_database)["products"]["brent"]
    for horizon in (7, 30, 90):
        performance = second["live_performance"]["horizons"][str(horizon)]
        assert performance["metrics"]["pending"] == 1
    with empty_database.connect() as conn:
        records = conn.execute("SELECT model_version, prediction, lower_bound, upper_bound FROM live_forecasts WHERE product='brent'").fetchall()
    # Each live endpoint is accompanied by its independently versioned shadow
    # candidate. Repeating the build remains idempotent for both models.
    assert len(records) == 6
    assert all("live_forecast_ledger_v1" in record["model_version"] and record["prediction"] > 0 for record in records)

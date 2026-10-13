from __future__ import annotations

from datetime import datetime, timedelta, timezone

from price_monitor.services.forecasting import build_forecast, daily_series, forecast_product
from price_monitor.services.forecasting import _damped_holt, _backtest


def synthetic_rows(count: int = 220) -> list[dict]:
    start = datetime(2026, 1, 1, 12, tzinfo=timezone.utc)
    return [
        {
            "product": "gasoil",
            "price": round(2.0 + index * 0.004 + (index % 5) * 0.002, 5),
            "source_date": (start + timedelta(days=index)).date().isoformat(),
            "collected_at": (start + timedelta(days=index)).isoformat(),
        }
        for index in range(count)
    ]


def test_daily_series_keeps_latest_intraday_observation():
    rows = [
        {"price": 80, "source_date": "2026-01-01", "collected_at": "2026-01-01T08:00:00+00:00"},
        {"price": 82, "source_date": "2026-01-01", "collected_at": "2026-01-01T17:00:00+00:00"},
    ]

    assert daily_series(rows) == [{"date": "2026-01-01", "value": 82.0}]


def test_forecast_returns_all_horizons_confidence_bands_and_backtest_metrics():
    result = forecast_product(synthetic_rows(), "gasoil")

    assert result["status"] == "ready"
    assert result["model"] == "damped_holt"
    assert result["history_count"] == 220
    for horizon in (7, 30, 90):
        points = result["forecasts"][str(horizon)]
        assert len(points) == horizon
        assert points[0]["lower"] <= points[0]["value"] <= points[0]["upper"]
        assert points[-1]["date"] == (datetime(2026, 8, 8) + timedelta(days=horizon)).date().isoformat()
        assert result["backtest"][str(horizon)]["available"] is True
        assert result["backtest"][str(horizon)]["mape"] is not None


def test_forecast_marks_short_history_as_insufficient():
    result = forecast_product(synthetic_rows(12), "brent")

    assert result["status"] == "insufficient_data"
    assert result["forecasts"] == {}


class ForecastDatabase:
    def observations(self, product=None, days=30, limit=600, instrument=None):
        rows = synthetic_rows()
        for row in rows:
            row["product"] = product
        return rows


def test_build_forecast_returns_both_market_products():
    result = build_forecast(ForecastDatabase())

    assert result["horizons"] == [7, 30, 90]
    assert set(result["products"]) == {"gasoil", "brent", "bitume"}
    assert result["products"]["bitume"]["status"] == "insufficient_data"
    assert result["products"]["gasoil"]["status"] == "ready"


def test_damped_trend_converges_instead_of_turning_back_to_level():
    predictions = _damped_holt([1.0, 2.0], 400)
    assert all(b >= a for a, b in zip(predictions, predictions[1:]))
    first_increment = predictions[1] - predictions[0]
    next_increment = predictions[2] - predictions[1]
    assert abs(next_increment / first_increment - .985) < 1e-10


def test_missing_days_advance_the_calendar_model_without_observations():
    # Equal prices separated by longer time must not imply the same daily trend.
    daily = _damped_holt([10., 20.], 7, [0, 1])
    sparse = _damped_holt([10., 20.], 7, [0, 10])
    assert sparse[-1] < daily[-1]


def test_calendar_backtest_counts_only_actual_prices_inside_horizon():
    values = [80.] * 80
    days = [i*7 for i in range(80)]
    result = _backtest(values, 7, days)
    assert result["samples"] == result["folds"]
    assert result["mae"] == result["naive_mae"] == 0
    assert result["interval_coverage"] == 1
    assert result["horizon_unit"] == "calendar_days"


def test_forecast_exposes_age_origin_and_honest_interval_metadata():
    result = forecast_product(synthetic_rows(), "gasoil")
    assert result["forecast_origin"] == result["last_actual"]["date"]
    assert result["interval_method"] == "heuristic_residual_sqrt_time"
    assert isinstance(result["is_stale"], bool)

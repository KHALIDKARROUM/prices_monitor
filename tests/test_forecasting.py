from __future__ import annotations

from datetime import datetime, timedelta, timezone
import math
import pytest

from price_monitor.services.forecasting import build_forecast, daily_series, forecast_product
from price_monitor.services.forecasting import _damped_holt, _backtest
from price_monitor.forecast_evaluation import evaluate_baselines, evaluation_plan, historical_stress_radius, interval_radius, select_model


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
    result = forecast_product(synthetic_rows(1000), "gasoil")

    assert result["status"] == "ready"
    assert result["model"] in {"naive", "holt", "per_horizon"}
    assert result["history_count"] == 1000
    for horizon in (7, 30, 90):
        points = result["forecasts"][str(horizon)]
        assert len(points) == horizon
        assert points[0]["lower"] <= points[0]["value"] <= points[0]["upper"]
        assert points[-1]["date"] == (datetime.fromisoformat(result["last_actual"]["date"]) + timedelta(days=horizon)).date().isoformat()
        assert result["backtest"][str(horizon)]["available"] is True
        assert result["backtest"][str(horizon)]["mape"] is not None


def test_forecast_marks_short_history_as_insufficient():
    result = forecast_product(synthetic_rows(12), "brent")

    assert result["status"] == "insufficient_data"
    assert result["forecasts"] == {}


class ForecastDatabase:
    def observations(self, product=None, days=30, limit=600, instrument=None, daily=False):
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


def test_calendar_backtest_scores_actual_endpoints_in_final_period():
    values = [80.] * 80
    days = [i*7 for i in range(80)]
    result = _backtest(values, 7, days)
    assert result["samples"] == 15
    assert result["mae"] == result["naive_mae"] == 0
    assert result["interval_coverage"] is None  # fewer than 19 calibration errors
    assert result["horizon_unit"] == "calendar_days"
    assert result["evaluation"] == "first_publication_endpoint"
    assert result["period"] == "final_holdout"


def test_forecast_exposes_age_origin_and_honest_interval_metadata():
    result = forecast_product(synthetic_rows(), "gasoil")
    assert result["forecast_origin"] == result["last_actual"]["date"]
    assert result["interval_method"] == "split_conformal_plus_historical_stress_envelope"
    assert result["interval_guarantee"] is False
    assert isinstance(result["is_stale"], bool)


def test_constant_series_uses_persistence_and_flags_no_proven_gain():
    rows = synthetic_rows(1000)
    for row in rows:
        row["price"] = 80.
    result = forecast_product(rows, "brent")
    for horizon, selection in result["model_selection"].items():
        assert selection["selected_model"] == "naive"
        assert {w["code"] for w in selection["warnings"]} & {"no_predictive_gain", "selection_unavailable"}
        assert all(point["value"] == 80 for point in result["forecasts"][horizon])


def test_trend_must_earn_live_selection_on_validation():
    result = forecast_product(synthetic_rows(1000), "gasoil")
    selection = result["model_selection"]["7"]
    assert selection["selected_model"] == "holt"
    assert selection["validation_mae"]["holt"] < .98 * selection["validation_mae"]["naive"]
    assert result["forecasts"]["7"][-1]["value"] == pytest.approx(_damped_holt([row["price"] for row in synthetic_rows(1000)], 7)[-1], abs=1e-6)


def test_selection_requires_at_least_two_percent_and_prefers_baseline_on_ties():
    assert select_model({"naive": 100., "holt": 98.}) == "holt"
    assert select_model({"naive": 100., "holt": 98.01}) == "naive"
    assert select_model({"holt": 0., "naive": 0.}) == "naive"


def test_short_history_keeps_forecast_but_omits_unsupported_bands():
    result = forecast_product(synthetic_rows(41), "gasoil")
    assert result["status"] == "ready"
    for horizon, selection in result["model_selection"].items():
        assert selection["selected_model"] == "naive"
        assert not result["backtest"][horizon]["available"]
        assert all(p["lower"] is None and p["upper"] is None for p in result["forecasts"][horizon])
        assert {"selection_unavailable", "uncalibrated_intervals"} <= {w["code"] for w in selection["warnings"]}


def test_final_period_changes_neither_selection_nor_calibration():
    values = [row["price"] for row in synthetic_rows(1000)]
    days = list(range(len(values)))
    before = evaluate_baselines(values, days, 7, _damped_holt)
    altered = values[:800] + [v * 100 for v in values[800:]]
    after = evaluate_baselines(altered, days, 7, _damped_holt)
    assert before["selected_model"] == after["selected_model"]
    assert before["validation_mae"] == after["validation_mae"]
    assert before["radii"] == after["radii"]
    assert before["candidates"]["holt"]["mae"] != after["candidates"]["holt"]["mae"]


def test_endpoint_gaps_and_split_boundaries_are_not_imputed():
    days = [i for i in range(1000) if i % 7 < 5 and not 400 <= i <= 410]
    plan = evaluation_plan(days, 7)
    for pair in plan["pairs"]:
        assert 7 <= days[pair["target"]] - days[pair["origin"]] <= 11
    for fold in plan["folds"]:
        assert max(days[plan["pairs"][i]["target"]] for i in fold["train"]) < min(days[plan["pairs"][i]["origin"]] for i in fold["validation"])
    assert all(days[plan["pairs"][i]["target"]] < plan["test_start"] for i in plan["calibration"])


def test_late_publication_uses_actual_step_and_requested_horizon_window():
    days = list(range(700)) + [702, 704, 706]
    values = [80 + .01 * day for day in days]
    result = evaluate_baselines(values, days, 7, _damped_holt)
    i = next(i for i, pair in enumerate(result["plan"]["pairs"]) if pair["step"] > 7)
    pair = result["plan"]["pairs"][i]
    origin = pair["origin"]
    expected = _damped_holt(values[:origin+1], pair["step"], days[:origin+1], model_horizon=7)[-1]
    assert result["predictions"]["holt"][i] == expected


def test_intervals_match_selected_model_earlier_horizon_errors():
    values = [row["price"] for row in synthetic_rows(1000)]
    evaluation = evaluate_baselines(values, list(range(1000)), 7, _damped_holt)
    selected = evaluation["selected_model"]
    pairs = evaluation["plan"]["pairs"]
    errors = [math.log(values[pairs[i]["target"]] / evaluation["predictions"][selected][i]) for i in evaluation["plan"]["calibration"]]
    assert evaluation["radii"]["7"] == interval_radius(errors)
    assert evaluation["candidates"][selected]["calibration_log_radius"] == interval_radius(errors)
    forecast = forecast_product(synthetic_rows(1000), "gasoil")["forecasts"]["7"][-1]
    assert forecast["upper"] / forecast["value"] == pytest.approx(math.exp(interval_radius(errors)), abs=1e-6)


def test_undercoverage_and_holdout_regression_are_prominent():
    values = [row["price"] for row in synthetic_rows(1000)]
    # The development trend wins selection; an unseen constant regime fails it.
    # Persistence MAE is zero, so a relative skill cannot be calculated.
    values[800:] = [5.] * 200
    result = evaluate_baselines(values, list(range(1000)), 7, _damped_holt)
    codes = {w["code"] for w in result["warnings"]}
    assert result["selected_model"] == "holt"
    assert "undercoverage" in codes
    assert "weak_holdout" in codes
    assert result["candidates"]["holt"]["interval_coverage"] < .95


def test_long_horizons_use_non_overlapping_historical_stress_blocks():
    radius, blocks = historical_stress_radius(
        [.02, .12, .03, .08, .04, .09, .05, .07],
        [0, 1, 30, 31, 60, 61, 90, 91], 30, minimum_blocks=4,
    )
    assert blocks == 4
    assert radius == pytest.approx(.12)


@pytest.mark.parametrize("product", ["brent", "gasoil"])
def test_supplied_long_horizon_holdout_uses_a_conservative_stress_envelope(product):
    from pathlib import Path
    from price_monitor.analysis.data import load_market_data

    frame = load_market_data(Path(__file__).resolve().parents[1] / "data" / "processed" / "market_prices.csv")
    series = frame[product].dropna()
    result = evaluate_baselines(series.tolist(), [day.toordinal() for day in series.index], 90, _damped_holt)
    naive = result["candidates"]["naive"]
    assert naive["interval_method"] == "split_conformal_plus_historical_stress"
    assert naive["historical_stress_blocks"] >= 8
    # The old recent-slice-only band covered about 52% for Brent and 57% for
    # gasoil in this untouched holdout.
    assert naive["interval_coverage"] >= .9

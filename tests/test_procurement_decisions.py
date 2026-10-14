from __future__ import annotations

import pytest

pd = pytest.importorskip("pandas")

from price_monitor.analysis.procurement_decision import evaluate_rfq_timing


def test_rfq_timing_compares_forecast_actions_with_a_simple_momentum_rule():
    prices = pd.Series(
        [96, 100, 100, 100, 100, 100],
        index=pd.to_datetime(["2026-01-01", "2026-01-02", "2026-01-03", "2026-01-08", "2026-01-09", "2026-01-10"]),
    )
    predictions = pd.DataFrame([
        {"origin_date": "2026-01-08", "target_date": "2026-01-15", "current_price": 100, "prediction": 103, "actual": 104},
        {"origin_date": "2026-01-09", "target_date": "2026-01-16", "current_price": 100, "prediction": 100, "actual": 104},
        {"origin_date": "2026-01-10", "target_date": "2026-01-17", "current_price": 100, "prediction": 103, "actual": 99},
    ])

    summary, decisions = evaluate_rfq_timing(
        predictions, prices, product="brent", horizon_days=7, forecast_model="holt",
    )

    forecast = summary.loc[summary.policy == "forecast"].iloc[0]
    simple = summary.loc[summary.policy == "seven_day_momentum"].iloc[0]
    assert len(decisions) == 3
    assert (forecast["rfq_requests"], forecast["correct_early_rfq"], forecast["unnecessary_rfq"], forecast["missed_material_rise"]) == (2, 1, 1, 1)
    assert (simple["rfq_requests"], simple["correct_early_rfq"], simple["unnecessary_rfq"], simple["missed_material_rise"]) == (1, 1, 0, 1)
    assert forecast["upside_capture_pct"] == pytest.approx(0.5)
    assert forecast["forecast_minus_momentum_unnecessary_rfq"] == 1


def test_rfq_timing_uses_only_prices_available_before_each_origin():
    index = pd.date_range("2026-01-01", periods=15, freq="D")
    prices = pd.Series(range(100, 115), index=index, dtype=float)
    predictions = pd.DataFrame([{
        "origin_date": "2026-01-10", "target_date": "2026-01-17", "current_price": 109, "prediction": 112, "actual": 111,
    }])

    before, decisions_before = evaluate_rfq_timing(predictions, prices, product="brent", horizon_days=7, forecast_model="holt")
    altered = prices.copy()
    altered.loc[altered.index > pd.Timestamp("2026-01-10")] *= 100
    after, decisions_after = evaluate_rfq_timing(predictions, altered, product="brent", horizon_days=7, forecast_model="holt")

    pd.testing.assert_frame_equal(before, after)
    pd.testing.assert_frame_equal(decisions_before, decisions_after)

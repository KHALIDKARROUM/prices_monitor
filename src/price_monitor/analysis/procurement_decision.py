"""Evaluate a procurement action rather than forecast error alone.

The use case is deliberately narrow: should a buyer request or refresh
competitive supplier quotes now, ahead of a potentially material market rise?
It does not assume that a forecast authorizes a purchase or that a market
benchmark is an executable supplier price.
"""

from __future__ import annotations

import numpy as np
import pandas as pd


RFQ_TRIGGER_PCT = 0.02
MOMENTUM_LOOKBACK_DAYS = 7
USE_CASE = "request_or_refresh_quotes_before_a_material_market_rise"


def _ratio(numerator: float, denominator: float) -> float | None:
    return None if denominator == 0 else float(numerator / denominator)


def _policy_metrics(decisions: pd.DataFrame, action_column: str, policy: str) -> dict:
    action = decisions[action_column].astype(bool)
    rise = decisions["actual_material_rise"].astype(bool)
    true_positive = int((action & rise).sum())
    false_positive = int((action & ~rise).sum())
    false_negative = int((~action & rise).sum())
    true_negative = int((~action & ~rise).sum())
    total_upside = float(decisions.loc[rise, "actual_upside_per_unit"].sum())
    identified_upside = float(decisions.loc[action & rise, "actual_upside_per_unit"].sum())
    return {
        "policy": policy,
        "samples": int(len(decisions)),
        "rfq_requests": int(action.sum()),
        "material_rise_opportunities": int(rise.sum()),
        "correct_early_rfq": true_positive,
        "unnecessary_rfq": false_positive,
        "missed_material_rise": false_negative,
        "correct_wait": true_negative,
        "precision": _ratio(true_positive, true_positive + false_positive),
        "recall": _ratio(true_positive, true_positive + false_negative),
        "f1": _ratio(2 * true_positive, 2 * true_positive + false_positive + false_negative),
        # This is a market-price exposure proxy for one procurement unit. It
        # is not claimed as realised savings because no historical supplier
        # quote can be assumed to track the benchmark exactly.
        "identified_upside_per_unit": identified_upside,
        "possible_upside_per_unit": total_upside,
        "upside_capture_pct": _ratio(identified_upside, total_upside),
    }


def evaluate_rfq_timing(
    predictions: pd.DataFrame,
    prices: pd.Series,
    *,
    product: str,
    horizon_days: int,
    forecast_model: str,
    trigger_pct: float = RFQ_TRIGGER_PCT,
    momentum_lookback_days: int = MOMENTUM_LOOKBACK_DAYS,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Compare forecast-led RFQ timing with a simple momentum rule.

    The forecast policy requests a quote when its target price is at least
    ``trigger_pct`` above the observed price.  The simple comparison policy
    requests a quote when the market has already risen by the same amount over
    the previous ``momentum_lookback_days``.  The actual outcome is a material
    rise at the recorded target publication.  Both policies can therefore be
    scored on the frozen holdout without using the target value to decide.
    """
    if not 0 < trigger_pct < 1:
        raise ValueError("RFQ trigger must be between zero and one.")
    if momentum_lookback_days < 1:
        raise ValueError("RFQ momentum lookback must be at least one day.")
    required = {"origin_date", "target_date", "current_price", "prediction", "actual"}
    missing = required.difference(predictions.columns)
    if missing:
        raise ValueError("RFQ decision predictions are missing: " + ", ".join(sorted(missing)))
    series = pd.Series(prices, copy=True).dropna().astype(float).sort_index()
    if series.empty or not np.isfinite(series.to_numpy()).all() or (series <= 0).any():
        raise ValueError("RFQ decision prices must be finite and positive.")

    rows = []
    for row in predictions.loc[:, list(required)].itertuples(index=False):
        origin = pd.Timestamp(row.origin_date)
        target = pd.Timestamp(row.target_date)
        current, prediction, actual = map(float, (row.current_price, row.prediction, row.actual))
        if not np.isfinite([current, prediction, actual]).all() or min(current, prediction, actual) <= 0:
            raise ValueError("RFQ decision values must be finite and positive.")
        earlier = series.loc[:origin - pd.Timedelta(days=momentum_lookback_days)]
        if earlier.empty:
            # A policy cannot form its simple baseline without a historical
            # reference. This is a missing decision, not a zero-change guess.
            continue
        prior = float(earlier.iloc[-1])
        predicted_change = prediction / current - 1
        actual_change = actual / current - 1
        momentum_change = current / prior - 1
        rows.append({
            "product": product,
            "horizon_days": int(horizon_days),
            "forecast_model": forecast_model,
            "origin_date": origin.date().isoformat(),
            "target_date": target.date().isoformat(),
            "current_price": current,
            "forecast_price": prediction,
            "actual_price": actual,
            "momentum_reference_price": prior,
            "predicted_change_pct": predicted_change * 100,
            "actual_change_pct": actual_change * 100,
            "momentum_change_pct": momentum_change * 100,
            "forecast_requests_rfq": predicted_change >= trigger_pct,
            "simple_requests_rfq": momentum_change >= trigger_pct,
            "actual_material_rise": actual_change >= trigger_pct,
            "actual_upside_per_unit": max(actual - current, 0.0),
        })
    decisions = pd.DataFrame(rows)
    if decisions.empty:
        empty = pd.DataFrame(columns=["product", "horizon_days", "forecast_model", "policy"])
        return empty, decisions
    forecast = _policy_metrics(decisions, "forecast_requests_rfq", "forecast")
    simple = _policy_metrics(decisions, "simple_requests_rfq", "seven_day_momentum")
    comparison = {
        "forecast_minus_momentum_correct_early_rfq": forecast["correct_early_rfq"] - simple["correct_early_rfq"],
        "forecast_minus_momentum_unnecessary_rfq": forecast["unnecessary_rfq"] - simple["unnecessary_rfq"],
        "forecast_minus_momentum_missed_rises": forecast["missed_material_rise"] - simple["missed_material_rise"],
        "forecast_minus_momentum_upside_capture_pp": (
            None if forecast["upside_capture_pct"] is None or simple["upside_capture_pct"] is None
            else (forecast["upside_capture_pct"] - simple["upside_capture_pct"]) * 100
        ),
    }
    summary = pd.DataFrame([forecast, simple])
    summary.insert(0, "forecast_model", forecast_model)
    summary.insert(0, "horizon_days", int(horizon_days))
    summary.insert(0, "product", product)
    summary["trigger_pct"] = trigger_pct * 100
    summary["momentum_lookback_days"] = momentum_lookback_days
    for key, value in comparison.items():
        summary[key] = value
    return summary, decisions

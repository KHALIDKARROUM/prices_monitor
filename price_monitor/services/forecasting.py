"""Short-horizon market-price forecasting and walk-forward validation.

The service uses only the Python standard library, keeping notebook dependencies
out of the production runtime. Holt must beat persistence on purged validation
folds before it is selected for a production horizon. Bands are calibrated on a
separate earlier period. Missing calendar days propagate model state without
adding invented observations.
"""

from __future__ import annotations

import math
from datetime import date, datetime, timedelta, timezone
from typing import Any, Iterable
from ..forecast_evaluation import PROTOCOL, NOMINAL_LEVEL, evaluate_baselines
from ..forecast_store import LIVE_FORECAST_MODEL_VERSION, LIVE_FORECAST_WINDOW
from ..bitumen import INDEX_LABEL, INDEX_UNIT
from ..instruments import DEFAULT_INSTRUMENTS, INSTRUMENTS, canonical_daily_rows, row_instrument


FORECAST_PRODUCTS = ("gasoil", "brent", "bitume")
FORECAST_HORIZONS = (7, 30, 90)
DEFAULT_HISTORY_DAYS = 1825
MAX_HISTORY_ROWS = 5000
MIN_OBSERVATIONS = 30
MIN_MONTHLY_UNCERTAINTY_HISTORY = 84

PRODUCT_META = {
    "gasoil": {"label": "Gasoil / diesel", "unit": "USD/gallon"},
    "brent": {"label": "Brent crude oil", "unit": "USD/baril"},
    "bitume": {"label": INDEX_LABEL, "unit": INDEX_UNIT},
}


def _parse_datetime(value: Any) -> datetime:
    if isinstance(value, datetime):
        parsed = value
    else:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _row_day(row: dict[str, Any]) -> date:
    source_date = row.get("source_date")
    if source_date:
        return date.fromisoformat(str(source_date)[:10])
    return _parse_datetime(row["collected_at"]).date()


def daily_series(rows: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    """Collapse repeated intraday observations to the latest value per day."""
    latest: dict[date, tuple[datetime, float]] = {}
    for row in rows:
        try:
            price = float(row["price"])
            if not math.isfinite(price) or price <= 0:
                continue
            collected_at = _parse_datetime(row.get("collected_at", row.get("source_date")))
            day = _row_day(row)
        except (KeyError, TypeError, ValueError, OverflowError):
            continue
        previous = latest.get(day)
        if previous is None or collected_at >= previous[0]:
            latest[day] = (collected_at, price)
    return [{"date": day.isoformat(), "value": value} for day, (_, value) in sorted(latest.items())]


def _damped_holt(values: list[float], horizon: int, days: list[int] | None = None, *, model_horizon: int | None = None) -> list[float]:
    """Forecast with a damped trend exponential-smoothing model."""
    if not values:
        return []
    if len(values) == 1:
        return [values[0]] * horizon

    # A recent window limits the effect of old regimes while retaining enough
    # history for the 90-day forecast to estimate a stable trend.
    window_size = min(len(values), max(90, (model_horizon or horizon) * 3, 180))
    series = values[-window_size:]
    alpha = 0.32
    beta = 0.08
    damping = 0.985
    level = series[0]
    timeline = days[-window_size:] if days is not None else list(range(len(series)))
    if len(timeline) != len(series) or any(b <= a for a, b in zip(timeline, timeline[1:])):
        raise ValueError("Dates must match observations and be strictly increasing")
    trend = (series[1] - series[0]) / (timeline[1] - timeline[0])
    for index, observed in enumerate(series[1:], start=1):
        # Advance latent state across missing days, without measurement updates.
        missing = timeline[index] - timeline[index - 1] - 1
        if missing:
            level += trend * damping * (1 - damping ** missing) / (1 - damping)
            trend *= damping ** missing
        previous_level = level
        level = alpha * observed + (1 - alpha) * (level + damping * trend)
        trend = beta * (level - previous_level) + (1 - beta) * damping * trend

    predictions: list[float] = []
    damped_trend = 0.0
    for step in range(1, horizon + 1):
        damped_trend += damping ** step
        prediction = max(0.000001, level + damped_trend * trend)
        predictions.append(prediction)
    return predictions


def _backtest(values: list[float], horizon: int, days: list[int] | None = None) -> dict[str, Any]:
    """Selected model's final-period endpoint score under the shared protocol."""
    timeline = days if days is not None else list(range(len(values)))
    evaluation = evaluate_baselines(values, timeline, horizon, _damped_holt)
    return _backtest_payload(evaluation, "calendar_days")


def _backtest_payload(evaluation: dict, horizon_unit: str) -> dict:
    score = evaluation["candidates"][evaluation["selected_model"]]
    return {**score, "available": score["samples"] > 0, "mape": score["mape_pct"],
            "model": evaluation["selected_model"], "horizon_unit": horizon_unit,
            "evaluation": "first_publication_endpoint", "protocol": PROTOCOL,
            "period": "final_holdout", "folds": len(evaluation["plan"]["folds"])}


def _round(value: float | None) -> float | None:
    return round(value, 6) if value is not None else None


def _forecast_points(
    predictions: list[float], *, evaluation: dict[str, Any], model: str, last_day: date, monthly: bool,
) -> list[dict[str, Any]]:
    """Return issue-time points and model-specific calibration bands."""
    points = []
    radii = evaluation["candidate_radii"][model]
    for index, prediction in enumerate(predictions, start=1):
        radius = radii[str(index)]
        month_number = last_day.year * 12 + last_day.month - 1 + index
        forecast_day = date(month_number // 12, month_number % 12 + 1, 1) if monthly else last_day + timedelta(days=index)
        points.append({
            "date": forecast_day.isoformat(), "value": _round(prediction),
            "lower": _round(prediction * math.exp(-radius)) if radius is not None else None,
            "upper": _round(prediction * math.exp(radius)) if radius is not None else None,
        })
    return points


def forecast_product(rows: Iterable[dict[str, Any]], product: str, horizons: tuple[int, ...] = FORECAST_HORIZONS) -> dict[str, Any]:
    monthly = product == "bitume"
    instrument = "asphalt_index" if monthly else DEFAULT_INSTRUMENTS[product]
    rows = list(rows)
    if monthly:
        # Supplier transaction prices cannot be combined with the BLS index.
        rows = [row for row in rows if row.get("data_kind") == "index"]
        horizons = (1, 3, 6)
    else:
        rows = canonical_daily_rows([dict(row, product=product) for row in rows if row_instrument(dict(row, product=product)) == instrument])
    series = daily_series(rows)
    meta = PRODUCT_META[product]
    base: dict[str, Any] = {
        "product": product,
        "label": INSTRUMENTS[instrument]["label"],
        "instrument": instrument,
        "unit": meta["unit"],
        "model": "per_horizon",
        "model_version": "4_stress_calibrated_intervals",
        "evaluation_protocol": PROTOCOL,
        "model_selection": {},
        "horizon_unit": "months" if monthly else "calendar_days",
        "horizons": list(horizons),
        "frequency": "monthly" if monthly else "daily",
        "data_kind": "index" if monthly else "quote",
        "interval_method": "split_conformal_plus_historical_stress_envelope",
        "interval_nominal_level": NOMINAL_LEVEL,
        "interval_guarantee": False,
        "history_count": len(series),
        "history": series[-120:],
        "forecasts": {},
        "backtest": {},
        # The lifecycle needs both endpoints before they are written to the
        # ledger. This internal value is removed before API responses are sent.
        "_candidate_forecasts": {},
        "_candidate_metadata": {},
    }
    if not series:
        base.update({"status": "insufficient_data", "message": "No history available."})
        return base
    base["last_actual"] = series[-1]
    if len(series) < MIN_OBSERVATIONS:
        base.update({
            "status": "insufficient_data",
            "message": f"At least {MIN_OBSERVATIONS} {'monthly' if monthly else 'daily'} observations are required.",
        })
        return base

    values = [float(item["value"]) for item in series]
    last_day = date.fromisoformat(series[-1]["date"])
    days = [date.fromisoformat(item["date"]).year * 12 + date.fromisoformat(item["date"]).month if monthly else date.fromisoformat(item["date"]).toordinal() for item in series]
    age_days = (datetime.now(timezone.utc).date() - last_day).days
    base.update({
        "status": "ready",
        "training_start": series[0]["date"],
        "training_end": series[-1]["date"],
        "age_days": age_days, "is_stale": age_days > (75 if monthly else 7),
        "forecast_origin": last_day.isoformat(),
    })
    source_dates = dict(zip(days, (item["date"] for item in series)))
    for horizon in horizons:
        evaluation = evaluate_baselines(values, days, horizon, _damped_holt, monthly=monthly)
        selected = evaluation["selected_model"]
        plan = evaluation["plan"]
        base["model_selection"][str(horizon)] = {
            key: evaluation[key] for key in ("protocol", "selection_rule", "selected_model",
                                              "selection_available", "validation_mae", "candidates", "warnings")
        }
        base["model_selection"][str(horizon)].update({
            "calibration_start": source_dates.get(plan["calibration_start"]),
            "test_start": source_dates.get(plan["test_start"]),
            "development_samples": len(plan["development"]),
            "calibration_samples": len(plan["calibration"]),
            "holdout_samples": len(plan["holdout"]),
            "target_max_delay": plan["max_delay"],
            "calibration_samples_by_step": evaluation["calibration_counts"],
            "interval_details_by_step": evaluation["candidate_interval_details"][selected],
        })
        if monthly and len(series) < MIN_MONTHLY_UNCERTAINTY_HISTORY:
            base["model_selection"][str(horizon)]["warnings"] = [
                *base["model_selection"][str(horizon)]["warnings"],
                {
                    "code": "limited_monthly_uncertainty_history",
                    "message": (
                        f"Only {len(series)} published monthly index values are available. At least "
                        f"{MIN_MONTHLY_UNCERTAINTY_HISTORY} are required before long-horizon uncertainty can be trusted."
                    ),
                },
            ]
        candidate_points = {
            "naive": _forecast_points([values[-1]] * horizon, evaluation=evaluation, model="naive", last_day=last_day, monthly=monthly),
            "holt": _forecast_points(_damped_holt(values, horizon, days), evaluation=evaluation, model="holt", last_day=last_day, monthly=monthly),
        }
        base["_candidate_forecasts"][str(horizon)] = candidate_points
        base["_candidate_metadata"][str(horizon)] = [
            {
                "model": name, "model_version": base["model_version"],
                "validation_mae": evaluation["candidates"][name].get("mae"),
                "validation_samples": evaluation["candidates"][name].get("samples"),
                "calibration_coverage": evaluation["candidates"][name].get("interval_coverage"),
            }
            for name in ("naive", "holt")
        ]
        base["forecasts"][str(horizon)] = candidate_points[selected]
        base["backtest"][str(horizon)] = _backtest_payload(evaluation, base["horizon_unit"])
    selected_models = {item["selected_model"] for item in base["model_selection"].values()}
    base["model"] = next(iter(selected_models)) if len(selected_models) == 1 else "per_horizon"
    return base


def build_forecast(database: Any, history_days: int = DEFAULT_HISTORY_DAYS) -> dict[str, Any]:
    """Build daily price and monthly Bitume-index forecasts separately."""
    history_days = max(90, min(DEFAULT_HISTORY_DAYS, int(history_days)))
    if hasattr(database, "reconcile_live_forecasts"):
        database.reconcile_live_forecasts()
    products: dict[str, Any] = {}
    for product in FORECAST_PRODUCTS:
        instrument = "asphalt_index" if product == "bitume" else DEFAULT_INSTRUMENTS[product]
        rows = (
            database.bitumen_series(days=history_days, include_anchor=True)
            if product == "bitume" and hasattr(database, "bitumen_series")
            else database.observations(product, days=history_days, limit=MAX_HISTORY_ROWS, instrument=instrument, daily=True)
        )
        products[product] = forecast_product(rows, product)
    if hasattr(database, "record_live_forecast"):
        for product, forecast in products.items():
            if forecast.get("status") != "ready":
                forecast.pop("_candidate_forecasts", None)
                forecast.pop("_candidate_metadata", None)
                continue
            lifecycle_planner = getattr(database, "prepare_model_lifecycle", None)
            lifecycle_by_horizon: dict[str, Any] = {}
            for horizon, points in forecast.get("forecasts", {}).items():
                if not points:
                    continue
                selection = forecast.get("model_selection", {}).get(horizon, {})
                candidate_points = forecast["_candidate_forecasts"][horizon]
                plan = None
                if callable(lifecycle_planner):
                    plan = lifecycle_planner(
                        product=product, instrument=forecast["instrument"], horizon=int(horizon),
                        horizon_unit=forecast["horizon_unit"], candidates=forecast["_candidate_metadata"][horizon],
                        preferred_model=selection.get("selected_model", "naive"),
                    )
                    production = plan["production"]
                    selected_before_control = selection.get("selected_model")
                    forecast["forecasts"][horizon] = candidate_points[production["model"]]
                    if production["model"] != selected_before_control:
                        selection["validation_selected_model"] = selected_before_control
                        selection["selected_model"] = production["model"]
                    lifecycle_by_horizon[horizon] = {
                        "production": {key: production[key] for key in ("model", "candidate_version", "status", "promoted_at", "reason")},
                        "shadow_models": [shadow["model"] for shadow in plan["shadows"]],
                        "criteria": plan["criteria"],
                    }
                    issuance = [production, *plan["shadows"]]
                else:
                    issuance = [{
                        "model": selection.get("selected_model", forecast.get("model", "unknown")),
                        "forecast_model_version": f"{forecast.get('model_version', 'unknown')}:{LIVE_FORECAST_MODEL_VERSION}",
                    }]
                for candidate in issuance:
                    model = candidate["model"]
                    endpoint = candidate_points[model][-1]
                    database.record_live_forecast({
                    "product": product,
                    "instrument": forecast["instrument"],
                    "horizon": int(horizon),
                    "horizon_unit": forecast["horizon_unit"],
                    "model": model,
                    "model_version": candidate["forecast_model_version"],
                    "origin_date": forecast["forecast_origin"],
                    "target_date": endpoint["date"],
                    "prediction": endpoint["value"],
                    "baseline_prediction": forecast["last_actual"]["value"],
                    "lower_bound": endpoint.get("lower"),
                    "upper_bound": endpoint.get("upper"),
                    })
            forecast.pop("_candidate_forecasts", None)
            forecast.pop("_candidate_metadata", None)
            if lifecycle_by_horizon:
                forecast["model_lifecycle"] = lifecycle_by_horizon
            forecast["live_performance"] = {
                "window": LIVE_FORECAST_WINDOW,
                "horizons": {
                    str(horizon): database.live_forecast_performance(
                        LIVE_FORECAST_WINDOW, product=product, instrument=forecast["instrument"], horizon=int(horizon),
                        model_version=(
                            lifecycle_by_horizon[str(horizon)]["production"].get("candidate_version", "")
                            + f":{LIVE_FORECAST_MODEL_VERSION}"
                            if str(horizon) in lifecycle_by_horizon else None
                        ),
                    )
                    for horizon in forecast["horizons"]
                },
            }
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "history_days": history_days,
        "horizons": list(FORECAST_HORIZONS),
        "live_forecast_model_version": LIVE_FORECAST_MODEL_VERSION,
        "products": products,
    }

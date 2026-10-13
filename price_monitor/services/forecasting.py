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
from ..bitumen import INDEX_LABEL, INDEX_UNIT
from ..instruments import DEFAULT_INSTRUMENTS, INSTRUMENTS, canonical_daily_rows, row_instrument


FORECAST_PRODUCTS = ("gasoil", "brent", "bitume")
FORECAST_HORIZONS = (7, 30, 90)
DEFAULT_HISTORY_DAYS = 1825
MAX_HISTORY_ROWS = 5000
MIN_OBSERVATIONS = 30

PRODUCT_META = {
    "gasoil": {"label": "Gasoil / diesel", "unit": "USD/gallon"},
    "brent": {"label": "Pétrole Brent", "unit": "USD/baril"},
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
        "model_version": "3_chronological_selection",
        "evaluation_protocol": PROTOCOL,
        "model_selection": {},
        "horizon_unit": "months" if monthly else "calendar_days",
        "horizons": list(horizons),
        "frequency": "monthly" if monthly else "daily",
        "data_kind": "index" if monthly else "quote",
        "interval_method": "chronological_absolute_log_error_quantile",
        "interval_nominal_level": NOMINAL_LEVEL,
        "interval_guarantee": False,
        "history_count": len(series),
        "history": series[-120:],
        "forecasts": {},
        "backtest": {},
    }
    if not series:
        base.update({"status": "insufficient_data", "message": "Aucun historique disponible."})
        return base
    base["last_actual"] = series[-1]
    if len(series) < MIN_OBSERVATIONS:
        base.update({
            "status": "insufficient_data",
            "message": f"Au moins {MIN_OBSERVATIONS} relevés {'mensuels' if monthly else 'journaliers'} sont nécessaires.",
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
        })
        predictions = [values[-1]] * horizon if selected == "naive" else _damped_holt(values, horizon, days)
        points = []
        for index, prediction in enumerate(predictions, start=1):
            radius = evaluation["radii"][str(index)]
            month_number = last_day.year * 12 + last_day.month - 1 + index
            forecast_day = date(month_number // 12, month_number % 12 + 1, 1) if monthly else last_day + timedelta(days=index)
            points.append({
                "date": forecast_day.isoformat(), "value": _round(prediction),
                "lower": _round(prediction * math.exp(-radius)) if radius is not None else None,
                "upper": _round(prediction * math.exp(radius)) if radius is not None else None,
            })
        base["forecasts"][str(horizon)] = points
        base["backtest"][str(horizon)] = _backtest_payload(evaluation, base["horizon_unit"])
    selected_models = {item["selected_model"] for item in base["model_selection"].values()}
    base["model"] = next(iter(selected_models)) if len(selected_models) == 1 else "per_horizon"
    return base


def build_forecast(database: Any, history_days: int = DEFAULT_HISTORY_DAYS) -> dict[str, Any]:
    """Build daily price and monthly Bitume-index forecasts separately."""
    history_days = max(90, min(DEFAULT_HISTORY_DAYS, int(history_days)))
    products: dict[str, Any] = {}
    for product in FORECAST_PRODUCTS:
        instrument = "asphalt_index" if product == "bitume" else DEFAULT_INSTRUMENTS[product]
        rows = database.observations(product, days=history_days, limit=MAX_HISTORY_ROWS, instrument=instrument, daily=True)
        products[product] = forecast_product(rows, product)
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "history_days": history_days,
        "horizons": list(FORECAST_HORIZONS),
        "products": products,
    }

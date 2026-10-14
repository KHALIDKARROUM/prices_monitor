"""Shared chronological endpoint protocol for research and live forecasts.

Selection uses purged development folds, intervals use later calibration errors,
and reported performance uses an untouched final period. Overlapping forecast
pairs are dependent; nominal interval coverage is not a statistical guarantee.
This module deliberately has no scientific-Python runtime dependencies.
"""
from __future__ import annotations

import math
from bisect import bisect_left
from statistics import mean
from typing import Callable

PROTOCOL = "chronological_endpoints_v1"
SELECTION_RULE = "Lowest mean purged-CV endpoint MAE; require 2% improvement over persistence."
NOMINAL_LEVEL = .95


def forecast_metrics(actual, predicted, current) -> dict:
    actual, predicted, current = [list(map(float, values)) for values in (actual, predicted, current)]
    if not (len(actual) == len(predicted) == len(current)):
        raise ValueError("Forecast and actual sample counts must match")
    if not actual:
        return {"samples": 0, "mae": None, "rmse": None, "bias": None,
                "mape_pct": None, "smape_pct": None, "direction_accuracy": None,
                "naive_mae": None, "mae_skill_vs_naive": None}
    errors = [p - a for a, p in zip(actual, predicted)]
    mae = mean(map(abs, errors))
    naive_mae = mean(abs(c - a) for c, a in zip(current, actual))
    sign = lambda v: (v > 0) - (v < 0)
    return {"samples": len(actual), "mae": mae,
            "rmse": math.sqrt(mean(e * e for e in errors)), "bias": mean(errors),
            "mape_pct": 100 * mean(abs(e / a) for e, a in zip(errors, actual)),
            "smape_pct": 200 * mean(abs(e) / (abs(a) + abs(p)) for e, a, p in zip(errors, actual, predicted)),
            "direction_accuracy": mean(sign(p - c) == sign(a - c) for a, p, c in zip(actual, predicted, current)),
            "naive_mae": naive_mae, "mae_skill_vs_naive": 1 - mae / naive_mae if naive_mae else None}


def interval_radius(residuals, level: float = NOMINAL_LEVEL) -> float:
    """Finite-sample absolute log-error quantile from calibration only."""
    errors = sorted(abs(float(v)) for v in residuals)
    if not 0 < level < 1 or any(not math.isfinite(v) for v in errors):
        raise ValueError("Invalid interval level or calibration errors")
    rank = math.ceil((len(errors) + 1) * level)
    if not errors or rank > len(errors):
        raise ValueError("Not enough calibration observations for the requested level")
    return errors[rank - 1]


def select_model(scores: dict[str, float], baseline: str = "naive") -> str:
    if baseline not in scores:
        return baseline
    best = min(scores, key=scores.get)
    return best if scores[best] <= .98 * scores[baseline] and scores[best] < scores[baseline] else baseline


def interval_metrics(actual, predicted, radius: float | None) -> dict:
    actual, predicted = list(map(float, actual)), list(map(float, predicted))
    if radius is None or not actual:
        return {"interval_coverage": None, "mean_interval_width": None}
    return {"interval_coverage": mean(abs(math.log(a / p)) <= radius for a, p in zip(actual, predicted)),
            "mean_interval_width": mean(p * (math.exp(radius) - math.exp(-radius)) for p in predicted)}


def target_position(timeline: list[int], origin: int, horizon: int, max_delay: int) -> int | None:
    requested = timeline[origin] + horizon
    position = bisect_left(timeline, requested, lo=origin + 1)
    return position if position < len(timeline) and timeline[position] <= requested + max_delay else None


def expanding_folds(pairs: list[dict], development: list[int], timeline: list[int], min_train: int = 60) -> list[dict]:
    """Three expanding folds, with training targets before validation origins."""
    size = len(development) // 4
    if size < 3:
        return []
    folds = []
    for start in range(len(development) - 3 * size, len(development), size):
        validation = development[start:start + size]
        first_origin = timeline[pairs[validation[0]]["origin"]]
        train = [i for i in development[:start] if timeline[pairs[i]["target"]] < first_origin]
        if len(train) < min_train:
            return []
        folds.append({"train": train, "validation": validation})
    return folds


def evaluation_plan(timeline: list[int], horizon: int, *, warmup: int = 60,
                    max_delay: int = 4, min_train: int = 60) -> dict:
    if horizon < 1 or any(b <= a for a, b in zip(timeline, timeline[1:])):
        raise ValueError("Positive horizon and strictly increasing source dates required")
    if not timeline:
        return {"pairs": [], "development": [], "calibration": [], "holdout": [], "folds": [],
                "calibration_start": None, "test_start": None, "max_delay": max_delay}
    calibration_start, test_start = timeline[int(len(timeline) * .65)], timeline[int(len(timeline) * .80)]
    pairs = []
    for origin in range(warmup, len(timeline)):
        target = target_position(timeline, origin, horizon, max_delay)
        if target is not None:
            pairs.append({"origin": origin, "target": target, "step": timeline[target] - timeline[origin]})
    development = [i for i, pair in enumerate(pairs) if timeline[pair["target"]] < calibration_start]
    calibration = [i for i, pair in enumerate(pairs) if timeline[pair["origin"]] >= calibration_start
                   and timeline[pair["target"]] < test_start]
    holdout = [i for i, pair in enumerate(pairs) if timeline[pair["origin"]] >= test_start]
    return {"pairs": pairs, "development": development, "calibration": calibration, "holdout": holdout,
            "folds": expanding_folds(pairs, development, timeline, min_train),
            "calibration_start": calibration_start, "test_start": test_start, "max_delay": max_delay}


def evaluate_baselines(values: list[float], timeline: list[int], horizon: int, predictor: Callable,
                       *, monthly: bool = False) -> dict:
    """Compare persistence/Holt on identical origins, dates, metrics and splits.

    Holt uses the requested horizon's fitting window even when a publication is
    late. Both research and the API call this function, rather than interpreting
    a precomputed report or selecting from final-test results.
    """
    plan = evaluation_plan(timeline, horizon, warmup=29 if monthly else 60,
                           max_delay=0 if monthly else 4, min_train=3 if monthly else 60)
    paths = {}
    predictions = {"naive": [], "holt": []}
    for pair in plan["pairs"]:
        origin = pair["origin"]
        path = predictor(values[:origin + 1], horizon + plan["max_delay"], timeline[:origin + 1], model_horizon=horizon)
        paths[origin] = path
        predictions["naive"].append(values[origin])
        predictions["holt"].append(path[pair["step"] - 1])

    def score(name, indices):
        return forecast_metrics([values[plan["pairs"][i]["target"]] for i in indices],
                                [predictions[name][i] for i in indices],
                                [values[plan["pairs"][i]["origin"]] for i in indices])

    cv = [{"model": name, "fold": number, **score(name, fold["validation"])}
          for number, fold in enumerate(plan["folds"], 1) for name in predictions]
    ranking = {name: mean(row["mae"] for row in cv if row["model"] == name) for name in predictions} if cv else {}
    selected = select_model(ranking)
    candidates = {}
    for name in predictions:
        cal = plan["calibration"]
        errors = [math.log(values[plan["pairs"][i]["target"]] / predictions[name][i]) for i in cal]
        try:
            radius = interval_radius(errors)
        except ValueError:
            radius = None
        holdout = plan["holdout"]
        candidates[name] = {**score(name, holdout),
                            **interval_metrics([values[plan["pairs"][i]["target"]] for i in holdout],
                                               [predictions[name][i] for i in holdout], radius),
                            "calibration_log_radius": radius, "calibration_samples": len(cal)}

    # Calibrate each displayed step for the selected horizon model, using only
    # the earlier calibration origins whose full endpoint is already observed.
    radii, counts = {}, {}
    for step in range(1, horizon + 1):
        errors = []
        for i in plan["calibration"]:
            origin = plan["pairs"][i]["origin"]
            target = target_position(timeline, origin, step, plan["max_delay"])
            if target is None:
                continue
            prediction = values[origin] if selected == "naive" else paths[origin][timeline[target] - timeline[origin] - 1]
            errors.append(math.log(values[target] / prediction))
        counts[str(step)] = len(errors)
        try:
            radii[str(step)] = interval_radius(errors)
        except ValueError:
            radii[str(step)] = None

    warnings = []
    if not cv:
        warnings.append({"code": "selection_unavailable", "message": "Insufficient history to compare models across three validation periods. Using the last known price by default."})
    elif selected == "naive":
        warnings.append({"code": "no_predictive_gain", "message": "Holt did not improve validation accuracy by at least 2%. Projecting the last known price; no predictive trend was demonstrated."})
    chosen = candidates[selected]
    if chosen["samples"] < 30:
        warnings.append({"code": "limited_evaluation", "message": f"Limited evaluation: {chosen['samples']} forecasts tested in the final period."})
    if selected != "naive" and chosen["mae"] is not None and chosen["mae"] >= chosen["naive_mae"]:
        warnings.append({"code": "weak_holdout", "message": "The selected model performed worse than the last known price in the final period."})
    if any(radius is None for radius in radii.values()):
        warnings.append({"code": "uncalibrated_intervals", "message": "Insufficient calibration data for some 95% bands. Those bands are not shown."})
    if chosen["interval_coverage"] is not None and chosen["interval_coverage"] < NOMINAL_LEVEL:
        warnings.append({"code": "undercoverage", "message": f"The nominal 95% band covered only {chosen['interval_coverage']:.0%} of tested prices. Uncertainty was underestimated in this period."})
    return {"protocol": PROTOCOL, "selection_rule": SELECTION_RULE, "selected_model": selected,
            "selection_available": bool(cv), "validation_mae": ranking, "candidates": candidates,
            "warnings": warnings, "radii": radii, "calibration_counts": counts,
            "plan": plan, "predictions": predictions, "cv": cv}

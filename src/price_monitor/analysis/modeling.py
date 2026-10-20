from __future__ import annotations

import os
import numpy as np
import pandas as pd

# Some minimal Windows environments do not expose the legacy `wmic` command
# used by joblib's physical-core probe. Fixing the limit also makes experiments
# less sensitive to the host machine.
os.environ.setdefault("LOKY_MAX_CPU_COUNT", "2")

from sklearn.base import clone
from sklearn.ensemble import HistGradientBoostingRegressor, RandomForestRegressor
from sklearn.impute import SimpleImputer
from sklearn.linear_model import Ridge
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

from .features import make_supervised
from ..services.forecasting import _damped_holt
from ..forecast_evaluation import evaluate_baselines, forecast_metrics, interval_radius, interval_metrics, select_model

SEED = 42


def candidates() -> dict:
    models = {"naive": None, "holt": None}
    for alpha in (1.0, 10.0, 100.0):
        models[f"ridge_{alpha:g}"] = make_pipeline(
            SimpleImputer(strategy="median", add_indicator=True), StandardScaler(), Ridge(alpha=alpha))
    for leaves in (7, 15):
        models[f"boosting_{leaves}"] = make_pipeline(
            SimpleImputer(strategy="median", add_indicator=True),
            HistGradientBoostingRegressor(max_iter=120, learning_rate=.05,
                max_leaf_nodes=leaves, min_samples_leaf=25, l2_regularization=10,
                early_stopping=False, random_state=SEED))
    models["random_forest"] = make_pipeline(
        SimpleImputer(strategy="median", add_indicator=True),
        RandomForestRegressor(n_estimators=120, min_samples_leaf=12, max_features=.7,
                              max_depth=8, random_state=SEED, n_jobs=1))
    return models


def metrics(actual, predicted, current) -> dict:
    return forecast_metrics(actual, predicted, current)


def price_predictions(model, x: pd.DataFrame, labels: pd.DataFrame) -> np.ndarray:
    log_prediction = model.predict(x)
    result = labels.current_price.to_numpy() * np.exp(log_prediction)
    if not np.isfinite(result).all():
        raise ValueError("Model generated non-finite forecasts")
    return result


def holt_predictions(series: pd.Series, origins: pd.DatetimeIndex, horizon: int, targets=None) -> np.ndarray:
    result = []
    for index, origin in enumerate(origins):
        past = series.loc[:origin].dropna()
        step = (targets[index] - origin).days if targets is not None else horizon
        result.append(_damped_holt(past.tolist(), step, [d.toordinal() for d in past.index], model_horizon=horizon)[-1])
    return np.array(result)


def run_experiment(frame: pd.DataFrame, product: str, horizon: int) -> dict:
    x, labels = make_supervised(frame, product, horizon)
    series = frame[product].dropna()
    production = evaluate_baselines(series.tolist(), [day.toordinal() for day in series.index], horizon, _damped_holt)
    plan = production["plan"]
    calibration_start = pd.Timestamp.fromordinal(plan["calibration_start"])
    test_start = pd.Timestamp.fromordinal(plan["test_start"])
    development = labels.index[plan["development"]]
    calibration = labels.index[plan["calibration"]]
    holdout = labels.index[plan["holdout"]]
    if min(len(development), len(calibration), len(holdout)) < 30:
        raise ValueError(f"Insufficient development/calibration/test history for {product}/{horizon}")
    models = candidates()
    baseline = {k: pd.Series(v, index=labels.index) for k, v in production["predictions"].items()}
    folds, cv = [], []
    if not plan["folds"]:
        raise ValueError(f"Insufficient purged validation history for {product}/{horizon}")
    for fold, split in enumerate(plan["folds"], 1):
        train, valid = split["train"], split["validation"]
        train_idx, valid_idx = labels.index[train], labels.index[valid]
        folds.append({"fold": fold, "train_rows": len(train), "validation_rows": len(valid),
            "train_start": str(train_idx[0].date()), "train_end": str(train_idx[-1].date()),
            "last_train_target": str(labels.loc[train_idx].target_date.max().date()),
            "validation_start": str(valid_idx[0].date()), "validation_end": str(valid_idx[-1].date())})
        for name, estimator in models.items():
            if estimator is None:
                prediction = baseline[name].loc[valid_idx].to_numpy()
            else:
                fitted = clone(estimator).fit(x.loc[train_idx], labels.loc[train_idx].target_log_return)
                prediction = price_predictions(fitted, x.loc[valid_idx], labels.loc[valid_idx])
            cv.append({"model": name, "fold": fold, **metrics(labels.loc[valid_idx].target_price,
                                  prediction, labels.loc[valid_idx].current_price)})
    cv = pd.DataFrame(cv)
    ranking = cv.groupby("model").mae.mean().sort_values(kind="stable")
    # Prefer persistence unless the candidate improves validation MAE by >=2%.
    selected = select_model(ranking.to_dict())
    fitted_models, predictions, calibration_predictions, results = {}, {}, {}, []
    for name, estimator in models.items():
        fitted = None if estimator is None else clone(estimator).fit(x.loc[development], labels.loc[development].target_log_return)
        fitted_models[name] = fitted
        def predict(indices):
            return baseline[name].loc[indices].to_numpy() if fitted is None else price_predictions(fitted, x.loc[indices], labels.loc[indices])
        cal_prediction, prediction = predict(calibration), predict(holdout)
        calibration_predictions[name] = cal_prediction
        if name in production["candidates"]:
            # Keep the research baseline bands identical to the production
            # evaluator. This includes the independent long-horizon stress
            # floor, and prevents the report from overstating certainty.
            radius = production["candidates"][name]["calibration_log_radius"]
            interval_method = production["candidates"][name]["interval_method"]
        else:
            local_radius = interval_radius(np.log(labels.loc[calibration].target_price.to_numpy() / cal_prediction))
            stress_floor = production["candidates"]["naive"]["historical_stress_radius"]
            radius = max(local_radius, stress_floor) if stress_floor is not None else local_radius
            interval_method = (
                "split_conformal_plus_baseline_stress_floor" if stress_floor is not None else "split_conformal"
            )
        if radius is None:
            lower = np.full_like(prediction, np.nan, dtype=float)
            upper = np.full_like(prediction, np.nan, dtype=float)
        else:
            lower, upper = prediction * np.exp(-radius), prediction * np.exp(radius)
        actual = labels.loc[holdout].target_price.to_numpy()
        results.append({"model": name, "selected_on_validation": name == selected,
            **metrics(actual, prediction, labels.loc[holdout].current_price),
            **interval_metrics(actual, prediction, radius), "calibration_log_radius": radius,
            "interval_method": interval_method})
        predictions[name] = pd.DataFrame({"origin_date": holdout, "target_date": labels.loc[holdout].target_date.to_numpy(),
            "actual": actual, "current_price": labels.loc[holdout].current_price.to_numpy(),
            "prediction": prediction, "lower": lower, "upper": upper})
    return {"product": product, "horizon": horizon, "features": list(x.columns),
        "x": x, "labels": labels, "development": development, "calibration": calibration, "holdout": holdout,
        "calibration_start": str(calibration_start.date()), "test_start": str(test_start.date()),
        "selected": selected, "models": fitted_models, "cv": cv, "folds": folds,
        "production": {key: production[key] for key in ("protocol", "selected_model", "selection_available", "validation_mae", "candidates", "warnings")},
        "metrics": pd.DataFrame(results), "predictions": predictions}

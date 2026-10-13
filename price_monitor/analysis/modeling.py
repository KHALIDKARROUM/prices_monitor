from __future__ import annotations

import math
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

from .features import make_supervised, purged_splits
from ..services.forecasting import _damped_holt

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
    actual, predicted, current = map(lambda v: np.asarray(v, dtype=float), (actual, predicted, current))
    error = predicted - actual
    baseline_mae = np.mean(np.abs(current - actual))
    return {
        "samples": len(actual), "mae": float(np.mean(np.abs(error))),
        "rmse": float(np.sqrt(np.mean(error ** 2))), "bias": float(np.mean(error)),
        "mape_pct": float(100 * np.mean(np.abs(error / actual))),
        "smape_pct": float(200 * np.mean(np.abs(error) / (np.abs(actual) + np.abs(predicted)))),
        "direction_accuracy": float(np.mean(np.sign(predicted-current) == np.sign(actual-current))),
        "mae_skill_vs_naive": float(1 - np.mean(np.abs(error))/baseline_mae) if baseline_mae else None,
    }


def price_predictions(model, x: pd.DataFrame, labels: pd.DataFrame) -> np.ndarray:
    log_prediction = model.predict(x)
    result = labels.current_price.to_numpy() * np.exp(log_prediction)
    if not np.isfinite(result).all():
        raise ValueError("Model generated non-finite forecasts")
    return result


def holt_predictions(series: pd.Series, origins: pd.DatetimeIndex, horizon: int) -> np.ndarray:
    result = []
    for origin in origins:
        past = series.loc[:origin].dropna()
        result.append(_damped_holt(past.tolist(), horizon, [d.toordinal() for d in past.index])[-1])
    return np.array(result)


def interval_radius(residuals, level: float = .95) -> float:
    """Finite-sample absolute-log-error quantile, on earlier calibration data.

    Serial dependence violates exchangeability: coverage is measured on holdout,
    never presented as a distribution-free guarantee for this time series.
    """
    residuals = np.asarray(residuals, dtype=float)
    rank = math.ceil((len(residuals) + 1) * level)
    if not len(residuals) or rank > len(residuals):
        raise ValueError("Not enough calibration observations for the requested level")
    return float(np.sort(np.abs(residuals))[rank - 1])


def run_experiment(frame: pd.DataFrame, product: str, horizon: int) -> dict:
    x, labels = make_supervised(frame, product, horizon)
    # Shared raw-calendar boundaries across products and horizons, independent of
    # which labels happen to exist near the right edge of the snapshot.
    calibration_start = frame.index[int(len(frame) * .65)]
    test_start = frame.index[int(len(frame) * .80)]
    development = labels.index[labels.target_date < calibration_start]
    calibration = labels.index[(labels.index >= calibration_start) & (labels.target_date < test_start)]
    holdout = labels.index[labels.index >= test_start]
    if min(len(development), len(calibration), len(holdout)) < 30:
        raise ValueError(f"Insufficient development/calibration/test history for {product}/{horizon}")
    models = candidates()
    baseline = {"naive": labels.current_price.to_numpy(),
                "holt": holt_predictions(frame[product], labels.index, horizon)}
    baseline = {k: pd.Series(v, index=labels.index) for k, v in baseline.items()}
    folds, cv = [], []
    dev_labels = labels.loc[development]
    for fold, (train, valid) in enumerate(purged_splits(dev_labels), 1):
        train_idx, valid_idx = development[train], development[valid]
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
    best = str(ranking.index[0])
    selected = best if ranking[best] < .98 * ranking["naive"] else "naive"
    fitted_models, predictions, calibration_predictions, results = {}, {}, {}, []
    for name, estimator in models.items():
        fitted = None if estimator is None else clone(estimator).fit(x.loc[development], labels.loc[development].target_log_return)
        fitted_models[name] = fitted
        def predict(indices):
            return baseline[name].loc[indices].to_numpy() if fitted is None else price_predictions(fitted, x.loc[indices], labels.loc[indices])
        cal_prediction, prediction = predict(calibration), predict(holdout)
        calibration_predictions[name] = cal_prediction
        radius = interval_radius(np.log(labels.loc[calibration].target_price.to_numpy() / cal_prediction))
        lower, upper = prediction * np.exp(-radius), prediction * np.exp(radius)
        actual = labels.loc[holdout].target_price.to_numpy()
        results.append({"model": name, "selected_on_validation": name == selected,
            **metrics(actual, prediction, labels.loc[holdout].current_price),
            "interval_coverage": float(np.mean((actual >= lower) & (actual <= upper))),
            "mean_interval_width": float(np.mean(upper-lower)), "calibration_log_radius": radius})
        predictions[name] = pd.DataFrame({"origin_date": holdout, "target_date": labels.loc[holdout].target_date.to_numpy(),
            "actual": actual, "current_price": labels.loc[holdout].current_price.to_numpy(),
            "prediction": prediction, "lower": lower, "upper": upper})
    return {"product": product, "horizon": horizon, "features": list(x.columns),
        "x": x, "labels": labels, "development": development, "calibration": calibration, "holdout": holdout,
        "calibration_start": str(calibration_start.date()), "test_start": str(test_start.date()),
        "selected": selected, "models": fitted_models, "cv": cv, "folds": folds,
        "metrics": pd.DataFrame(results), "predictions": predictions}

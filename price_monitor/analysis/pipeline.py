"""Generate an auditable offline research report, figures and experiment records."""
from __future__ import annotations

import hashlib
import importlib.metadata
import json
import platform
import warnings
from datetime import datetime, timezone
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from sklearn.inspection import permutation_importance
from statsmodels.tsa.stattools import acf, adfuller, kpss
from threadpoolctl import threadpool_limits

from .data import PRODUCTS, UNITS, audit_data, load_market_data, load_bitumen_data
from ..services.forecasting import forecast_product
from ..forecast_evaluation import PROTOCOL, SELECTION_RULE
from ..bitumen import INDEX_UNIT
from .modeling import SEED, run_experiment


def write_json(path: Path, data) -> None:
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False, allow_nan=False) + "\n", encoding="utf-8")


def _save(fig, output: Path, name: str) -> None:
    fig.tight_layout()
    fig.savefig(output / name, dpi=140, bbox_inches="tight")
    plt.close(fig)


def exploratory_analysis(frame: pd.DataFrame, output: Path) -> dict:
    """Model diagnostics use only the first 65%; final outcomes stay unseen."""
    train = frame.iloc[:int(len(frame) * .65)]
    returns = pd.DataFrame({p: np.log(train[p].dropna()).diff() for p in PRODUCTS})
    # Co-movement uses identical start AND end dates. A missing diesel day must
    # not silently pair its multi-day return with Brent's single-day return.
    paired_prices = train.dropna()
    paired_returns = np.log(paired_prices).diff().dropna()
    train.describe().T.to_csv(output / "descriptive_statistics.csv", index_label="product")
    missing = frame.isna().sum().rename("missing_rows").to_frame()
    missing.to_csv(output / "missingness.csv", index_label="product")
    fig, axes = plt.subplots(2, 2, figsize=(13, 8))
    for row, product in enumerate(PRODUCTS):
        s = train[product].dropna()
        axes[row, 0].plot(s.index, s, lw=.8, label="Observed")
        axes[row, 0].plot(s.rolling(30).mean(), lw=1.5, label="30-observation mean")
        axes[row, 0].set(title=f"{product} — {UNITS[product]}", ylabel=UNITS[product])
        axes[row, 0].legend()
        axes[row, 1].hist(returns[product].dropna() * 100, bins=45, color="#528c80")
        axes[row, 1].set(title=f"{product} — log returns", xlabel="Return per observed interval (%)")
    _save(fig, output, "eda_prices_returns.png")
    fig, axes = plt.subplots(2, 2, figsize=(13, 8))
    stationarity, anomalies = [], []
    for row, product in enumerate(PRODUCTS):
        s, r = train[product].dropna(), returns[product].dropna()
        axes[row, 0].plot(r.rolling(30).std() * 100, color="#528c80")
        axes[row, 0].set(title=f"{product} — volatility", ylabel="30-observation std. (%)")
        ac = acf(r, nlags=min(40, len(r)//4), fft=True)
        axes[row, 1].stem(np.arange(len(ac)), ac)
        axes[row, 1].set(title=f"{product} — return autocorrelation", xlabel="Observation lag")
        for label, values in [("log_price", np.log(s)), ("log_return", r)]:
            with warnings.catch_warnings(record=True) as caught:
                warnings.simplefilter("always")
                adf_result = adfuller(values, autolag="AIC")
                kpss_result = kpss(values, regression="c", nlags="auto")
            stationarity.append({"product": product, "transform": label,
                "adf_statistic": adf_result[0], "adf_pvalue": adf_result[1],
                "kpss_statistic": kpss_result[0], "kpss_pvalue": kpss_result[1],
                "warnings": " | ".join(str(w.message) for w in caught)})
        median, mad = r.median(), (r-r.median()).abs().median()
        z = .6745*(r-median)/mad if mad else r*0
        for day, score in z[z.abs() > 3.5].items():
            anomalies.append({"product": product, "date": str(day.date()),
                              "log_return_pct": r.loc[day]*100, "robust_z": score})
    _save(fig, output, "eda_volatility_acf.png")
    pd.DataFrame(stationarity).to_csv(output / "stationarity.csv", index=False)
    pd.DataFrame(anomalies, columns=["product", "date", "log_return_pct", "robust_z"]).to_csv(output / "anomalies.csv", index=False)
    fig, axes = plt.subplots(1, 2, figsize=(13, 4))
    paired_returns.rolling(60).corr().loc[(slice(None), "brent"), "gasoil"].droplevel(1).plot(ax=axes[0])
    axes[0].set(title="Aligned-return 60-observation correlation", ylim=(-1, 1))
    seasonal = paired_returns.groupby(paired_returns.index.month).mean()*100
    seasonal.plot.bar(ax=axes[1])
    axes[1].set(title="Mean log return by month (descriptive)", ylabel="%", xlabel="Month")
    _save(fig, output, "eda_correlation_seasonality.png")
    seasonal.to_csv(output / "seasonality.csv", index_label="month")
    return {"diagnostic_end": str(train.index[-1].date()),
        "aligned_return_correlation": float(paired_returns.corr().loc["brent", "gasoil"]),
        "anomaly_flags": len(anomalies), "anomalies_removed": 0}


def _markdown_table(frame: pd.DataFrame) -> str:
    def cell(v):
        return f"{v:.4f}" if isinstance(v, (float, np.floating)) else str(v)
    rows = ["| " + " | ".join(map(str, frame.columns)) + " |",
            "| " + " | ".join("---" for _ in frame.columns) + " |"]
    rows += ["| " + " | ".join(cell(v) for v in row) + " |" for row in frame.itertuples(index=False, name=None)]
    return "\n".join(rows)


def run_bitumen_analysis(data_path: Path, output: Path) -> dict:
    """Monthly index statistics and walk-forward baseline, in its own units."""
    series = load_bitumen_data(data_path)
    output.mkdir(parents=True, exist_ok=True)
    rows = [{"price": float(value), "source_date": str(day.date()),
             "collected_at": f"{day.date()}T00:00:00+00:00", "data_kind": "index"}
            for day, value in series.items()]
    forecast = forecast_product(rows, "bitume")
    write_json(output / "bitumen_forecast.json", forecast)
    series.describe().rename("bitume_index").to_csv(output / "bitumen_statistics.csv")
    audit = {"series": "WPU058", "unit": INDEX_UNIT, "frequency": "monthly",
             "sha256": hashlib.sha256(data_path.read_bytes()).hexdigest(),
             "observations": len(series), "start": str(series.index[0].date()),
             "end": str(series.index[-1].date()),
             "missing_months": len(pd.date_range(series.index[0], series.index[-1], freq="MS").difference(series.index)),
             "publication_time_available": False}
    raw_path = data_path.parent.parent / "raw" / "FRED_WPU058.csv"
    if raw_path.exists():
        raw = pd.read_csv(raw_path, na_values=["."], index_col=0, parse_dates=True)["WPU058"]
        raw = pd.to_numeric(raw, errors="raise").reindex(series.index)
        audit["raw_mismatches"] = int((~np.isclose(series, raw, rtol=0, atol=1e-9)).sum())
        audit["raw_sha256"] = hashlib.sha256(raw_path.read_bytes()).hexdigest()
        if audit["raw_mismatches"]:
            raise ValueError("Bitume snapshot differs from its bundled raw series")
    write_json(output / "bitumen_data_quality.json", audit)
    backtest = pd.DataFrame.from_dict(forecast["backtest"], orient="index")
    backtest.to_csv(output / "bitumen_backtest.csv", index_label="horizon_months")
    comparison = pd.DataFrame([
        {"horizon_months": int(horizon), "model": name, "selected": name == selection["selected_model"],
         "validation_mae": selection["validation_mae"].get(name), **score}
        for horizon, selection in forecast["model_selection"].items()
        for name, score in selection["candidates"].items()
    ])
    comparison.to_csv(output / "bitumen_model_comparison.csv", index=False)
    warnings = "\n".join(f"- **{horizon} month(s):** " + " ".join(w["message"] for w in selection["warnings"])
                         for horizon, selection in forecast["model_selection"].items())
    fig, axes = plt.subplots(2, 1, figsize=(11, 7))
    axes[0].plot(series.index, series, color="#8c78b6", marker=".", label="Published monthly index")
    axes[0].set(title="Bitume — BLS/FRED WPU058", ylabel="Index Dec 1984=100")
    axes[1].bar(series.index, series.pct_change().mul(100), width=20, color="#8c78b6")
    axes[1].set(ylabel="Monthly change (%)", xlabel="Source month")
    axes[1].axhline(0, color="#888", lw=.7)
    _save(fig, output, "eda_bitumen_index.png")
    report = f"""# Bitume — monthly index analysis

Source: [BLS/FRED WPU058](https://fred.stlouisfed.org/series/WPU058), **{audit['start']} → {audit['end']}**, {len(series)} observed months. Unit: **Index Dec 1984=100**. Missing months: {audit['missing_months']}. Processed SHA-256: `{audit['sha256']}`.

![Monthly index and changes](eda_bitumen_index.png)

Persistence and fixed-parameter damped Holt are compared separately at 1, 3 and 6 calendar months. The dashboard uses this same `{PROTOCOL}` endpoint protocol and selection rule: {SELECTION_RULE} Supplier USD/tonne prices stay separate; no daily index observations are invented. Statistics: [bitumen_statistics.csv](bitumen_statistics.csv). Forecast points, selected models and warnings: [bitumen_forecast.json](bitumen_forecast.json). Raw/processed audit: [bitumen_data_quality.json](bitumen_data_quality.json).

## Performance limitations

{warnings}

## Same-date endpoint comparison: final holdout

{_markdown_table(comparison[['horizon_months', 'model', 'selected', 'samples', 'mae', 'mae_skill_vs_naive', 'calibration_samples', 'interval_coverage']])}

MAE/RMSE are in index points. Positive MAE skill means improvement over persistence; negative skill means worse. The first 65% of source months are used for purged validation, the next 15% for calibration, and the last 20% for final testing. Only an actual publication at the requested month is scored. Insufficient validation defaults to persistence; insufficient calibration leaves bands absent. Full comparison: [bitumen_model_comparison.csv](bitumen_model_comparison.csv). Historical publication timestamps and vintages are unavailable, so these checks use source months and cannot establish real-time availability. Overlapping pairs are dependent and nominal 95% bands are not coverage guarantees.
"""
    (output / "bitumen_report.md").write_text(report, encoding="utf-8")
    return audit


def run_analysis(data_path: Path, output: Path, horizons=(1, 7, 30, 90), save_models=False) -> dict:
    data_path, output = Path(data_path).resolve(), Path(output).resolve()
    output.mkdir(parents=True, exist_ok=True)
    frame = load_market_data(data_path)
    audit = audit_data(frame, data_path)
    if any(info.get("raw_mismatches", 0) for info in audit["products"].values()):
        raise ValueError("Processed snapshot does not match its bundled source CSVs")
    write_json(output / "data_quality.json", audit)
    eda = exploratory_analysis(frame, output)
    results, cv_results, all_folds, selections, importances, predictions, production_results = [], [], [], [], [], [], []
    plt.rcParams.update({"axes.spines.top": False, "axes.spines.right": False})
    for product in PRODUCTS:
        for horizon in horizons:
            print(f"Evaluating {product}, {horizon} calendar day(s)...", flush=True)
            with threadpool_limits(limits=2):
                experiment = run_experiment(frame, product, horizon)
            selected = experiment["selected"]
            production = experiment["production"]
            write_json(output / f"production_selection_{product}_{horizon}.json",
                       {"product": product, "horizon_days": horizon, "snapshot_sha256": audit["sha256"], **production})
            production_results.extend({"product": product, "horizon_days": horizon, "model": name,
                "production_selected": name == production["selected_model"],
                "validation_mae": production["validation_mae"].get(name), **score}
                for name, score in production["candidates"].items())
            result = experiment["metrics"].assign(product=product, horizon_days=horizon)
            results.append(result)
            cv_results.append(experiment["cv"].assign(product=product, horizon_days=horizon))
            all_folds += [{"product": product, "horizon_days": horizon, **f} for f in experiment["folds"]]
            card = {"product": product, "horizon_days": horizon, "selected_model": selected,
                "selection_rule": SELECTION_RULE, "evaluation_protocol": PROTOCOL,
                "production_selected_model": production["selected_model"],
                "development_rows": len(experiment["development"]), "calibration_rows": len(experiment["calibration"]),
                "holdout_rows": len(experiment["holdout"]), "calibration_start": experiment["calibration_start"],
                "test_start": experiment["test_start"],
                "last_training_target": str(experiment["labels"].loc[experiment["development"]].target_date.max().date()),
                "feature_names": experiment["features"], "seed": SEED,
                "snapshot_sha256": audit["sha256"], "production_promoted": False}
            selections.append(card)
            write_json(output / f"model_card_{product}_{horizon}.json", card)
            for model_name, prediction in experiment["predictions"].items():
                predictions.append(prediction.assign(product=product, horizon_days=horizon, model=model_name))
            # Explain the best ML candidate on calibration only, even when the
            # persistence baseline wins selection. Correlated lags share signal.
            ranking = experiment["cv"].groupby("model").mae.mean().sort_values()
            ml_name = next(name for name in ranking.index if experiment["models"][name] is not None)
            cal = experiment["calibration"]
            with threadpool_limits(limits=2):
                importance = permutation_importance(experiment["models"][ml_name], experiment["x"].loc[cal],
                    experiment["labels"].loc[cal].target_log_return, n_repeats=5,
                    random_state=SEED, scoring="neg_mean_absolute_error", n_jobs=1)
            importances.append(pd.DataFrame({"feature": experiment["features"],
                "importance_mean": importance.importances_mean, "importance_std": importance.importances_std,
                "product": product, "horizon_days": horizon, "model": ml_name,
                "split": "calibration", "metric": "increase_in_log_return_mae"}))
            if save_models:
                import joblib
                model_dir = output / "models"
                model_dir.mkdir(exist_ok=True)
                joblib.dump({"model": experiment["models"][selected], "metadata": card},
                            model_dir / f"{product}_{horizon}.joblib")
            # Model and calibration are frozen throughout the final holdout.
            chosen = experiment["predictions"][selected]
            fig, axes = plt.subplots(2, 1, figsize=(12, 7), sharex=True)
            axes[0].plot(chosen.target_date, chosen.actual, label="Actual", color="#222")
            axes[0].plot(chosen.target_date, chosen.prediction, label=selected, color="#388776")
            axes[0].fill_between(chosen.target_date, chosen.lower, chosen.upper, color="#388776", alpha=.15,
                                 label="95% nominal, calibrated on earlier data")
            axes[0].set(title=f"{product} — {horizon}-day target, frozen holdout", ylabel=UNITS[product])
            axes[0].legend(fontsize=8)
            axes[1].plot(chosen.target_date, chosen.prediction-chosen.actual, color="#ad6538")
            axes[1].axhline(0, color="#888", lw=.7)
            axes[1].set(ylabel="Prediction − actual", xlabel="Actual target publication date")
            _save(fig, output, f"holdout_{product}_{horizon}.png")
    table, cv_table = pd.concat(results, ignore_index=True), pd.concat(cv_results, ignore_index=True)
    production_table = pd.DataFrame(production_results)
    production_table.to_csv(output / "production_comparison.csv", index=False)
    table.to_csv(output / "holdout_metrics.csv", index=False)
    cv_table.to_csv(output / "cross_validation.csv", index=False)
    pd.DataFrame(all_folds).to_csv(output / "split_audit.csv", index=False)
    pd.concat(importances).to_csv(output / "feature_importance.csv", index=False)
    pd.concat(predictions).to_csv(output / "holdout_predictions.csv", index=False)
    versions = {name: importlib.metadata.version(name) for name in
                ("numpy", "pandas", "scikit-learn", "scipy", "statsmodels", "matplotlib", "joblib")}
    source_files = [*Path(__file__).parent.glob("*.py"), Path(__file__).parents[1] / "services" / "forecasting.py",
                    Path(__file__).parents[1] / "forecast_evaluation.py"]
    manifest = {"generated_at": datetime.now(timezone.utc).isoformat(), "python": platform.python_version(),
        "packages": versions, "seed": SEED, "horizons_calendar_days": list(horizons),
        "data_sha256": audit["sha256"], "data_start": audit["start"], "data_end": audit["end"],
        "code_sha256": {str(p.relative_to(Path(__file__).parents[1])): hashlib.sha256(p.read_bytes()).hexdigest() for p in source_files},
        "eda": eda, "experiments": selections}
    bitumen_path = data_path.parent / "bitumen_index.csv"
    if bitumen_path.exists():
        manifest["bitumen_index"] = run_bitumen_analysis(bitumen_path, output)
    write_json(output / "manifest.json", manifest)
    selected_rows = table[table.selected_on_validation]
    bitumen_note = (
        "Bitumen is covered separately by its monthly BLS/FRED WPU058 index: [monthly report](bitumen_report.md), statistics, 1/3/6-month forecasts and walk-forward scores. It is not mixed into the daily price ML experiment or relabeled as USD/tonne."
        if "bitumen_index" in manifest else
        "No monthly Bitume snapshot was supplied; its separate analysis was not run."
    )
    bitumen_visual = "![Bitume monthly index and changes](eda_bitumen_index.png)" if "bitumen_index" in manifest else ""
    columns = ["product", "horizon_days", "model", "mae", "rmse", "mae_skill_vs_naive", "interval_coverage"]
    report = f"""# Data science audit and results

Generated from the local snapshot **{audit['start']} → {audit['end']}**, {audit['rows']} rows.
SHA-256: `{audit['sha256']}`. Full versions, split dates and code fingerprints: [manifest.json](manifest.json).

## Findings and corrections

- The original notebook had partial execution, no engineered features, and no ML comparison. Its purported next-observation baseline was a single-origin constant prediction across 60 observations; it now uses rolling one-step persistence.
- The live Holt implementation damped its accumulated forecast trend twice. The geometric sum is corrected. Missing calendar days now advance model state without creating observations; validation uses the same calendar-day horizons as the API.
- The live service and this research now share `{PROTOCOL}`: the same actual endpoint labels, chronological splits, purged folds, MAE selection rule, metrics and calibration quantiles. Production chooses between persistence and Holt for each horizon using validation only. ML candidates remain research artifacts.
- Raw/processed local CSV agreement, missingness, finite positive prices, date uniqueness and chronology are checked before fitting. Local consistency does not authenticate the upstream provider or resolve historical revisions.

## Feature engineering and evaluation

Features cover lagged log returns, price/rolling-mean ratios, momentum, volatility, calendar cycles, gap length, and strictly earlier observations of the other product. Rolling windows count observations. Own price at origin is assumed published; cross-product features older than seven days are missing. Targets are actual observed prices, never interpolated.

Each direct model predicts the first publication on or after 1, 7, 30 or 90 calendar days (at most four days late). The exact label date is recorded. The live service scores these same endpoints; Holt advances to the actual publication date without changing its requested-horizon fitting window.

For each instrument's observed source dates, first 65%: development and expanding-window cross-validation. Next 15%: interval calibration. Last 20%: frozen final holdout. Training labels crossing a split boundary are purged. Imputation and scaling are fitted only on training folds. Boosting early stopping is disabled to avoid a random internal validation split. Candidates: persistence, corrected Holt, regularized linear regression (Ridge), histogram gradient boosting, and random forest. Hyperparameters and seed are fixed in source.

Selection uses validation MAE only, requiring at least 2% improvement over persistence. All candidate holdout scores are diagnostic; the holdout winner is not used to change the selection. Models stay frozen across calibration and holdout; baseline states use observations available at each origin, as real-time persistence does.

## Selected models: untouched holdout results

{_markdown_table(selected_rows[columns])}

MAE/RMSE use each product's own price unit; they must not be averaged across products. Positive skill means improvement over persistence; negative skill means worse. Interval coverage is a fraction. See [all model metrics](holdout_metrics.csv), [validation scores](cross_validation.csv), [split audit](split_audit.csv), and [predictions](holdout_predictions.csv).

## Live-model comparison and weak performance

{_markdown_table(production_table[['product', 'horizon_days', 'model', 'production_selected', 'validation_mae', 'samples', 'mae', 'mae_skill_vs_naive', 'interval_coverage']])}

Persistence selection means no validated trend improvement. Negative final-test skill means the candidate underperformed the last known price. Coverage below 0.95 means the nominal 95% band missed more prices than intended; it must not be read as reliable 95% coverage. These warnings are prominent in the dashboard, alongside missing-band and small-sample warnings. [Production comparison](production_comparison.csv) and `production_selection_*.json` are generated by the same evaluator called by the API. The live selection is recomputed on its current instrument history, so a later snapshot may choose differently.

Bands use finite-sample absolute log-error quantiles from the separate calibration period, specific to the selected model and displayed forecast step. They replace the former Holt residual / square-root-time heuristic. At least 19 calibration errors are needed for a finite nominal 95% quantile; otherwise the band is absent. Final-test prices never choose the model or widen calibration bands.

## EDA and interpretation

Model diagnostics use development dates only (through {eda['diagnostic_end']}). Price/return distributions, rolling volatility, ACF, calendar seasonality, ADF/KPSS stationarity tests, aligned-return correlation and robust shock flags are included. ADF's null is a unit root; KPSS's null is level stationarity. Their assumptions and structural breaks limit conclusions. Warnings and bounded p-values are retained. Seasonal patterns are descriptive, not evidence of a repeatable trading effect.

Aligned-return correlation: **{eda['aligned_return_correlation']:.3f}**. Both products use exactly the same start/end dates for this calculation. Flagged shocks: **{eda['anomaly_flags']}**, automatically removed: **0**. Missing weekdays include holidays and must not all be called data failures.

![Prices and returns](eda_prices_returns.png)
![Volatility and autocorrelation](eda_volatility_acf.png)
![Correlation and seasonality](eda_correlation_seasonality.png)

Calibration-set permutation importance is saved in [feature_importance.csv](feature_importance.csv). It measures changes in log-return MAE, not causal effects. Correlated features can substitute for one another, so importance is not a feature-deletion rule.

## Limits and next decisions

- No ML model is automatically promoted to the live app. Review validation consistency, holdout skill, freshness and interval coverage before a separate deployment decision.
- Historical provider publication timestamps and vintages are unavailable. Source-date causality prevents mechanical future leakage, but a true as-of backtest requires release timestamps/revision history. Data imported into SQL uses reconstructed collection times; those cannot establish actual availability.
- Prices are market benchmarks. They omit executable quotes, transaction costs, freight, FX and supplier terms. No profitability or purchasing recommendation is inferred from forecast accuracy.
- {bitumen_note}
- Overlapping horizons and serial dependence mean fold errors are not independent. Calibration quantiles have a 95% nominal level; time-series dependence and regime changes remove exchangeability guarantees. Use measured holdout coverage.
- Check fresh-data errors, missingness, feature ranges and interval coverage after deployment; reassess on new chronological data, not by repeatedly tuning to this holdout. Use block-bootstrap uncertainty or non-overlapping evaluation when more history is available.
- Serialized models, if requested, are research artifacts fitted on development only, with their training cutoff in each model card. Never load untrusted pickle/joblib files. Baseline artifacts contain `model=None` and an explicit baseline name.

## References

{bitumen_visual}

- [BLS/FRED WPU058 monthly index](https://fred.stlouisfed.org/series/WPU058)

- [scikit-learn: lagged features and time-series validation](https://scikit-learn.org/stable/auto_examples/applications/plot_time_series_lagged_features.html)
- [Forecasting: Principles and Practice — damped Holt equations](https://otexts.com/fpp3/holt.html)
- [Forecasting: Principles and Practice — time-series cross-validation](https://otexts.com/fpp3/tscv.html)
- [Ryan Tibshirani — finite-sample conformal calibration](https://www.stat.cmu.edu/~ryantibs/talks/conformal-2022.pdf)
- [scikit-learn: permutation importance](https://scikit-learn.org/stable/modules/generated/sklearn.inspection.permutation_importance.html)
"""
    (output / "report.md").write_text(report, encoding="utf-8")
    return manifest

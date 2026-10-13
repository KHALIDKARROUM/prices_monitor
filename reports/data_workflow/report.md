# Data science audit and results

Generated from the local snapshot **2021-09-10 → 2026-09-09**, 1287 rows.
SHA-256: `98aa047ab90dfec93ab188f50c79fd33b4b60188047560b6f132458ebb12aa26`. Full versions, split dates and code fingerprints: [manifest.json](manifest.json).

## Findings and corrections

- The original notebook had partial execution, no engineered features, and no ML comparison. Its purported next-observation baseline was a single-origin constant prediction across 60 observations; it now uses rolling one-step persistence.
- The live Holt implementation damped its accumulated forecast trend twice. The geometric sum is corrected. Missing calendar days now advance model state without creating observations; validation uses the same calendar-day horizons as the API.
- The live service and this research now share `chronological_endpoints_v1`: the same actual endpoint labels, chronological splits, purged folds, MAE selection rule, metrics and calibration quantiles. Production chooses between persistence and Holt for each horizon using validation only. ML candidates remain research artifacts.
- Raw/processed local CSV agreement, missingness, finite positive prices, date uniqueness and chronology are checked before fitting. Local consistency does not authenticate the upstream provider or resolve historical revisions.

## Feature engineering and evaluation

Features cover lagged log returns, price/rolling-mean ratios, momentum, volatility, calendar cycles, gap length, and strictly earlier observations of the other product. Rolling windows count observations. Own price at origin is assumed published; cross-product features older than seven days are missing. Targets are actual observed prices, never interpolated.

Each direct model predicts the first publication on or after 1, 7, 30 or 90 calendar days (at most four days late). The exact label date is recorded. The live service scores these same endpoints; Holt advances to the actual publication date without changing its requested-horizon fitting window.

For each instrument's observed source dates, first 65%: development and expanding-window cross-validation. Next 15%: interval calibration. Last 20%: frozen final holdout. Training labels crossing a split boundary are purged. Imputation and scaling are fitted only on training folds. Boosting early stopping is disabled to avoid a random internal validation split. Candidates: persistence, corrected Holt, regularized linear regression (Ridge), histogram gradient boosting, and random forest. Hyperparameters and seed are fixed in source.

Selection uses validation MAE only, requiring at least 2% improvement over persistence. All candidate holdout scores are diagnostic; the holdout winner is not used to change the selection. Models stay frozen across calibration and holdout; baseline states use observations available at each origin, as real-time persistence does.

## Selected models: untouched holdout results

| product | horizon_days | model | mae | rmse | mae_skill_vs_naive | interval_coverage |
| --- | --- | --- | --- | --- | --- | --- |
| brent | 1 | naive | 2.3758 | 3.6631 | 0.0000 | 0.7897 |
| brent | 7 | naive | 5.4512 | 7.7257 | 0.0000 | 0.8710 |
| brent | 30 | naive | 12.4511 | 17.8633 | 0.0000 | 0.6595 |
| brent | 90 | naive | 23.9427 | 30.7276 | 0.0000 | 0.5211 |
| gasoil | 1 | naive | 0.0866 | 0.1252 | 0.0000 | 0.8554 |
| gasoil | 7 | naive | 0.1862 | 0.2548 | 0.0000 | 0.8618 |
| gasoil | 30 | naive | 0.4587 | 0.6310 | 0.0000 | 0.7293 |
| gasoil | 90 | naive | 0.7799 | 1.0459 | 0.0000 | 0.5661 |

MAE/RMSE use each product's own price unit; they must not be averaged across products. Positive skill means improvement over persistence; negative skill means worse. Interval coverage is a fraction. See [all model metrics](holdout_metrics.csv), [validation scores](cross_validation.csv), [split audit](split_audit.csv), and [predictions](holdout_predictions.csv).

## Live-model comparison and weak performance

| product | horizon_days | model | production_selected | validation_mae | samples | mae | mae_skill_vs_naive | interval_coverage |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| brent | 1 | naive | True | 1.2610 | 252 | 2.3758 | 0.0000 | 0.7897 |
| brent | 1 | holt | False | 1.8074 | 252 | 3.2090 | -0.3507 | 0.8532 |
| brent | 7 | naive | True | 2.8499 | 248 | 5.4512 | 0.0000 | 0.8710 |
| brent | 7 | holt | False | 3.3204 | 248 | 5.8296 | -0.0694 | 0.8548 |
| brent | 30 | naive | True | 5.0693 | 232 | 12.4511 | 0.0000 | 0.6595 |
| brent | 30 | holt | False | 7.1622 | 232 | 15.1669 | -0.2181 | 0.7414 |
| brent | 90 | naive | True | 7.6980 | 190 | 23.9427 | 0.0000 | 0.5211 |
| brent | 90 | holt | False | 12.4233 | 190 | 31.1468 | -0.3009 | 0.7158 |
| gasoil | 1 | naive | True | 0.0561 | 249 | 0.0866 | 0.0000 | 0.8554 |
| gasoil | 1 | holt | False | 0.0766 | 249 | 0.1163 | -0.3425 | 0.8193 |
| gasoil | 7 | naive | True | 0.1252 | 246 | 0.1862 | 0.0000 | 0.8618 |
| gasoil | 7 | holt | False | 0.1408 | 246 | 0.2103 | -0.1297 | 0.8374 |
| gasoil | 30 | naive | True | 0.2616 | 229 | 0.4587 | 0.0000 | 0.7293 |
| gasoil | 30 | holt | False | 0.3555 | 229 | 0.6083 | -0.3261 | 0.6201 |
| gasoil | 90 | naive | True | 0.4099 | 189 | 0.7799 | 0.0000 | 0.5661 |
| gasoil | 90 | holt | False | 0.6297 | 189 | 1.1887 | -0.5242 | 0.6032 |

Persistence selection means no validated trend improvement. Negative final-test skill means the candidate underperformed the last known price. Coverage below 0.95 means the nominal 95% band missed more prices than intended; it must not be read as reliable 95% coverage. These warnings are prominent in the dashboard, alongside missing-band and small-sample warnings. [Production comparison](production_comparison.csv) and `production_selection_*.json` are generated by the same evaluator called by the API. The live selection is recomputed on its current instrument history, so a later snapshot may choose differently.

Bands use finite-sample absolute log-error quantiles from the separate calibration period, specific to the selected model and displayed forecast step. They replace the former Holt residual / square-root-time heuristic. At least 19 calibration errors are needed for a finite nominal 95% quantile; otherwise the band is absent. Final-test prices never choose the model or widen calibration bands.

## EDA and interpretation

Model diagnostics use development dates only (through 2024-12-05). Price/return distributions, rolling volatility, ACF, calendar seasonality, ADF/KPSS stationarity tests, aligned-return correlation and robust shock flags are included. ADF's null is a unit root; KPSS's null is level stationarity. Their assumptions and structural breaks limit conclusions. Warnings and bounded p-values are retained. Seasonal patterns are descriptive, not evidence of a repeatable trading effect.

Aligned-return correlation: **0.558**. Both products use exactly the same start/end dates for this calculation. Flagged shocks: **33**, automatically removed: **0**. Missing weekdays include holidays and must not all be called data failures.

![Prices and returns](eda_prices_returns.png)
![Volatility and autocorrelation](eda_volatility_acf.png)
![Correlation and seasonality](eda_correlation_seasonality.png)

Calibration-set permutation importance is saved in [feature_importance.csv](feature_importance.csv). It measures changes in log-return MAE, not causal effects. Correlated features can substitute for one another, so importance is not a feature-deletion rule.

## Limits and next decisions

- No ML model is automatically promoted to the live app. Review validation consistency, holdout skill, freshness and interval coverage before a separate deployment decision.
- Historical provider publication timestamps and vintages are unavailable. Source-date causality prevents mechanical future leakage, but a true as-of backtest requires release timestamps/revision history. Data imported into SQL uses reconstructed collection times; those cannot establish actual availability.
- Prices are market benchmarks. They omit executable quotes, transaction costs, freight, FX and supplier terms. No profitability or purchasing recommendation is inferred from forecast accuracy.
- Bitumen is covered separately by its monthly BLS/FRED WPU058 index: [monthly report](bitumen_report.md), statistics, 1/3/6-month forecasts and walk-forward scores. It is not mixed into the daily price ML experiment or relabeled as USD/tonne.
- Overlapping horizons and serial dependence mean fold errors are not independent. Calibration quantiles have a 95% nominal level; time-series dependence and regime changes remove exchangeability guarantees. Use measured holdout coverage.
- Check fresh-data errors, missingness, feature ranges and interval coverage after deployment; reassess on new chronological data, not by repeatedly tuning to this holdout. Use block-bootstrap uncertainty or non-overlapping evaluation when more history is available.
- Serialized models, if requested, are research artifacts fitted on development only, with their training cutoff in each model card. Never load untrusted pickle/joblib files. Baseline artifacts contain `model=None` and an explicit baseline name.

## References

![Bitume monthly index and changes](eda_bitumen_index.png)

- [BLS/FRED WPU058 monthly index](https://fred.stlouisfed.org/series/WPU058)

- [scikit-learn: lagged features and time-series validation](https://scikit-learn.org/stable/auto_examples/applications/plot_time_series_lagged_features.html)
- [Forecasting: Principles and Practice — damped Holt equations](https://otexts.com/fpp3/holt.html)
- [Forecasting: Principles and Practice — time-series cross-validation](https://otexts.com/fpp3/tscv.html)
- [Ryan Tibshirani — finite-sample conformal calibration](https://www.stat.cmu.edu/~ryantibs/talks/conformal-2022.pdf)
- [scikit-learn: permutation importance](https://scikit-learn.org/stable/modules/generated/sklearn.inspection.permutation_importance.html)

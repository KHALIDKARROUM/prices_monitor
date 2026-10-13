# Data science audit and results

Generated from the local snapshot **2021-09-10 → 2026-09-09**, 1287 rows.
SHA-256: `98aa047ab90dfec93ab188f50c79fd33b4b60188047560b6f132458ebb12aa26`. Full versions, split dates and code fingerprints: [manifest.json](manifest.json).

## Findings and corrections

- The original notebook had partial execution, no engineered features, and no ML comparison. Its purported next-observation baseline was a single-origin constant prediction across 60 observations; it now uses rolling one-step persistence.
- The live Holt implementation damped its accumulated forecast trend twice. The geometric sum is corrected. Missing calendar days now advance model state without creating observations; validation uses the same calendar-day horizons as the API.
- The live forecast now reports persistence skill, empirical interval coverage, forecast origin, and data age. Its residual-based bands are explicitly heuristic, not validated confidence guarantees.
- Raw/processed local CSV agreement, missingness, finite positive prices, date uniqueness and chronology are checked before fitting. Local consistency does not authenticate the upstream provider or resolve historical revisions.

## Feature engineering and evaluation

Features cover lagged log returns, price/rolling-mean ratios, momentum, volatility, calendar cycles, gap length, and strictly earlier observations of the other product. Rolling windows count observations. Own price at origin is assumed published; cross-product features older than seven days are missing. Targets are actual observed prices, never interpolated.

Each direct model predicts the first publication on or after 1, 7, 30 or 90 calendar days (at most four days late). The exact label date is recorded. This endpoint experiment differs from the live service's evaluation of all observed points inside a forecast window.

First 65%: development and expanding-window cross-validation. Next 15%: interval calibration. Last 20%: frozen final holdout. Training labels crossing a split boundary are purged. Imputation and scaling are fitted only on training folds. Boosting early stopping is disabled to avoid a random internal validation split. Candidates: persistence, corrected Holt, regularized linear regression (Ridge), histogram gradient boosting, and random forest. Hyperparameters and seed are fixed in source.

Selection uses validation MAE only, requiring at least 2% improvement over persistence. All candidate holdout scores are diagnostic; the holdout winner is not used to change the selection. Models stay frozen across calibration and holdout; baseline states use observations available at each origin, as real-time persistence does.

## Selected models: untouched holdout results

| product | horizon_days | model | mae | rmse | mae_skill_vs_naive | interval_coverage |
| --- | --- | --- | --- | --- | --- | --- |
| brent | 1 | naive | 2.3677 | 3.6559 | 0.0000 | 0.7905 |
| brent | 7 | naive | 5.4362 | 7.7109 | 0.0000 | 0.8715 |
| brent | 30 | naive | 12.4009 | 17.8250 | 0.0000 | 0.6609 |
| brent | 90 | naive | 23.8418 | 30.6490 | 0.0000 | 0.5236 |
| gasoil | 1 | naive | 0.0866 | 0.1252 | 0.0000 | 0.8554 |
| gasoil | 7 | naive | 0.1862 | 0.2548 | 0.0000 | 0.8618 |
| gasoil | 30 | naive | 0.4587 | 0.6310 | 0.0000 | 0.7293 |
| gasoil | 90 | naive | 0.7799 | 1.0459 | 0.0000 | 0.5661 |

MAE/RMSE use each product's own price unit; they must not be averaged across products. Positive skill means improvement over persistence; negative skill means worse. Interval coverage is a fraction. See [all model metrics](holdout_metrics.csv), [validation scores](cross_validation.csv), [split audit](split_audit.csv), and [predictions](holdout_predictions.csv).

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
- Bitumen is covered by a separate monthly BLS/FRED WPU058 analysis, with 1/3/6-month forecasts and walk-forward checks: [monthly report](bitumen_report.md). The index keeps its published unit and is separate from the daily price ML experiments and supplier USD/tonne quotes.
- Overlapping horizons and serial dependence mean fold errors are not independent. Calibration quantiles have a 95% nominal level; time-series dependence and regime changes remove exchangeability guarantees. Use measured holdout coverage.
- Check fresh-data errors, missingness, feature ranges and interval coverage after deployment; reassess on new chronological data, not by repeatedly tuning to this holdout. Use block-bootstrap uncertainty or non-overlapping evaluation when more history is available.
- Serialized models, if requested, are research artifacts fitted on development only, with their training cutoff in each model card. Never load untrusted pickle/joblib files. Baseline artifacts contain `model=None` and an explicit baseline name.

## References

- [scikit-learn: lagged features and time-series validation](https://scikit-learn.org/stable/auto_examples/applications/plot_time_series_lagged_features.html)
- [Forecasting: Principles and Practice — damped Holt equations](https://otexts.com/fpp3/holt.html)
- [scikit-learn: permutation importance](https://scikit-learn.org/stable/modules/generated/sklearn.inspection.permutation_importance.html)


## Bitume monthly index

The project includes 60 observed months from September 2021 to August 2026, with zero missing months and zero differences from the bundled raw FRED WPU058 series. See the [monthly report](bitumen_report.md), [audit](bitumen_data_quality.json), [statistics](bitumen_statistics.csv), [forecasts](bitumen_forecast.json) and [walk-forward scores](bitumen_backtest.csv). These results retain their own source hash and monthly frequency.

![Bitume index and monthly changes](eda_bitumen_index.png)

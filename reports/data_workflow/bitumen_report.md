# Bitume — monthly index analysis

Source: [BLS/FRED WPU058](https://fred.stlouisfed.org/series/WPU058), **2021-09-01 → 2026-08-01**, 60 observed months. Unit: **Index Dec 1984=100**. Missing months: 0. Processed SHA-256: `4805cbb27ca7cd86b0fe1978cbd10a6166b602776cdba1b225bf4444a5d1ce71`.

![Monthly index and changes](eda_bitumen_index.png)

The fixed-parameter damped-Holt model projects 1, 3 and 6 calendar months from the last source month. The dashboard uses the same calculation. Supplier USD/tonne prices stay separate; no daily index observations are invented. Statistics: [bitumen_statistics.csv](bitumen_statistics.csv). Forecast points and bands: [bitumen_forecast.json](bitumen_forecast.json). Raw/processed audit: [bitumen_data_quality.json](bitumen_data_quality.json).

## Walk-forward checks

| horizon_months | available | folds | samples | horizon_unit | evaluation | mae | rmse | mape | bias | naive_mae | mae_skill_vs_naive | interval_coverage |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 1 | True | 5 | 5 | months | observed_points_in_forecast_window | 21.3737 | 26.0691 | 6.2131 | -4.6400 | 16.9102 | -0.2640 | 1 |
| 3 | True | 5 | 15 | months | observed_points_in_forecast_window | 28.0709 | 34.3485 | 6.9135 | -12.8512 | 20.0957 | -0.3969 | 1 |
| 6 | True | 5 | 30 | months | observed_points_in_forecast_window | 46.7199 | 61.3019 | 11.1228 | -40.1817 | 35.5706 | -0.3134 | 1 |

MAE/RMSE are in index points. Positive MAE skill means improvement over persistence; negative skill means worse. Bands are heuristic with a 95% nominal level; empirical coverage is reported separately. These are baseline diagnostics, not a model-selection or trading-profitability result. Historical publication timestamps and vintages are unavailable, so these checks use source months and cannot establish real-time availability.

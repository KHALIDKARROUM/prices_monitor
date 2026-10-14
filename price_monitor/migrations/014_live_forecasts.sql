CREATE TABLE IF NOT EXISTS live_forecasts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    product TEXT NOT NULL CHECK(product IN ('gasoil', 'brent', 'bitume')),
    instrument TEXT NOT NULL,
    horizon INTEGER NOT NULL CHECK(horizon > 0),
    horizon_unit TEXT NOT NULL CHECK(horizon_unit IN ('calendar_days', 'months')),
    model TEXT NOT NULL,
    model_version TEXT NOT NULL,
    origin_date TEXT NOT NULL,
    target_date TEXT NOT NULL,
    issued_at TEXT NOT NULL,
    prediction REAL NOT NULL,
    baseline_prediction REAL NOT NULL,
    lower_bound REAL,
    upper_bound REAL,
    actual_value REAL,
    actual_source_date TEXT,
    actual_collected_at TEXT,
    evaluated_at TEXT,
    error REAL,
    absolute_error REAL,
    baseline_error REAL,
    baseline_absolute_error REAL,
    interval_covered INTEGER,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(instrument, origin_date, target_date, horizon, model_version)
);
CREATE INDEX IF NOT EXISTS idx_live_forecasts_pending
    ON live_forecasts(product, instrument, target_date, actual_value);
CREATE INDEX IF NOT EXISTS idx_live_forecasts_evaluated
    ON live_forecasts(product, instrument, evaluated_at, id);

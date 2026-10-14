CREATE TABLE IF NOT EXISTS forecast_model_candidates (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    product TEXT NOT NULL CHECK(product IN ('gasoil', 'brent', 'bitume')),
    instrument TEXT NOT NULL,
    horizon INTEGER NOT NULL CHECK(horizon > 0),
    horizon_unit TEXT NOT NULL CHECK(horizon_unit IN ('calendar_days', 'months')),
    model TEXT NOT NULL CHECK(model IN ('naive', 'holt')),
    implementation_version TEXT NOT NULL,
    candidate_version TEXT NOT NULL,
    status TEXT NOT NULL CHECK(status IN ('candidate', 'shadow', 'production', 'rolled_back', 'rejected')),
    validation_mae REAL,
    validation_samples INTEGER NOT NULL DEFAULT 0 CHECK(validation_samples >= 0),
    calibration_coverage REAL,
    shadow_min_samples INTEGER NOT NULL DEFAULT 30 CHECK(shadow_min_samples > 0),
    promotion_min_improvement REAL NOT NULL DEFAULT 0.05 CHECK(promotion_min_improvement >= 0),
    rollback_max_degradation REAL NOT NULL DEFAULT 0.10 CHECK(rollback_max_degradation >= 0),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    promoted_at TEXT,
    rolled_back_at TEXT,
    reason TEXT NOT NULL DEFAULT '',
    UNIQUE(instrument, horizon, candidate_version)
);
CREATE INDEX IF NOT EXISTS idx_forecast_model_candidates_scope
    ON forecast_model_candidates(product, instrument, horizon, status);
CREATE TABLE IF NOT EXISTS forecast_model_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    candidate_id INTEGER NOT NULL REFERENCES forecast_model_candidates(id),
    event_type TEXT NOT NULL,
    from_status TEXT,
    to_status TEXT,
    reason TEXT NOT NULL DEFAULT '',
    samples INTEGER,
    production_mae REAL,
    candidate_mae REAL,
    baseline_mae REAL,
    improvement REAL,
    interval_coverage REAL,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_forecast_model_events_candidate
    ON forecast_model_events(candidate_id, id DESC);

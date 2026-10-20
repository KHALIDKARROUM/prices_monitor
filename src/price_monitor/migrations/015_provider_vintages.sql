CREATE TABLE IF NOT EXISTS provider_value_vintages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    data_kind TEXT NOT NULL CHECK(data_kind IN ('quote', 'index')),
    product TEXT NOT NULL CHECK(product IN ('gasoil', 'brent', 'bitume')),
    instrument TEXT NOT NULL,
    source TEXT NOT NULL,
    source_date TEXT NOT NULL,
    value REAL NOT NULL CHECK(value > 0),
    unit TEXT NOT NULL,
    currency TEXT NOT NULL DEFAULT '',
    available_at TEXT NOT NULL,
    availability_basis TEXT NOT NULL CHECK(availability_basis IN ('provider_timestamp', 'collection_observed', 'manual_entry', 'reconstructed')),
    captured_at TEXT NOT NULL,
    revision_number INTEGER NOT NULL CHECK(revision_number > 0),
    previous_vintage_id INTEGER REFERENCES provider_value_vintages(id),
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(data_kind, product, instrument, source, source_date, revision_number)
);
CREATE INDEX IF NOT EXISTS idx_provider_vintages_as_of
    ON provider_value_vintages(data_kind, product, instrument, available_at, source_date);
CREATE INDEX IF NOT EXISTS idx_provider_vintages_series
    ON provider_value_vintages(data_kind, product, instrument, source, source_date, revision_number);

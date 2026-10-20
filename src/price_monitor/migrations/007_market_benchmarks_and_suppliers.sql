-- Keep non-tradable market indicators separate from executable supplier quotes.
CREATE TABLE IF NOT EXISTS market_benchmarks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    code TEXT NOT NULL,
    product TEXT NOT NULL CHECK(product IN ('gasoil', 'bitume')),
    label TEXT NOT NULL,
    value REAL NOT NULL CHECK(value > 0),
    unit TEXT NOT NULL,
    geography TEXT NOT NULL,
    source TEXT NOT NULL,
    source_date TEXT NOT NULL,
    collected_at TEXT NOT NULL,
    source_url TEXT NOT NULL DEFAULT '',
    notes TEXT NOT NULL DEFAULT '',
    variation REAL,
    variation_pct REAL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(code, source_date, value)
);
CREATE INDEX IF NOT EXISTS idx_market_benchmarks_code_date
  ON market_benchmarks(code, source_date);

CREATE TABLE IF NOT EXISTS supplier_channels (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    product TEXT NOT NULL CHECK(product IN ('gasoil', 'bitume')),
    region TEXT NOT NULL,
    coverage TEXT NOT NULL,
    channel TEXT NOT NULL,
    typical_terms TEXT NOT NULL,
    specifications TEXT NOT NULL,
    website_url TEXT NOT NULL DEFAULT '',
    contact_url TEXT NOT NULL DEFAULT '',
    buyer_note TEXT NOT NULL DEFAULT '',
    active INTEGER NOT NULL DEFAULT 1,
    sort_order INTEGER NOT NULL DEFAULT 100,
    UNIQUE(name, product, region)
);

UPDATE data_sources SET active=0 WHERE code='fred_asphalt';

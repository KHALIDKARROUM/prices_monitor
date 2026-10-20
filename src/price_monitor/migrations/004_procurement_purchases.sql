-- Store purchase estimates and their market/budget comparisons.
CREATE TABLE IF NOT EXISTS procurement_purchases (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    product TEXT NOT NULL CHECK(product IN ('gasoil', 'brent', 'bitume')),
    supplier TEXT NOT NULL,
    quantity REAL NOT NULL CHECK(quantity > 0),
    unit TEXT NOT NULL,
    currency TEXT NOT NULL,
    unit_price REAL NOT NULL CHECK(unit_price > 0),
    exchange_rate REAL NOT NULL CHECK(exchange_rate > 0),
    transport_cost REAL NOT NULL DEFAULT 0 CHECK(transport_cost >= 0),
    budget_amount REAL,
    purchase_date TEXT NOT NULL,
    total_cost REAL NOT NULL,
    total_cost_usd REAL NOT NULL,
    budget_variance REAL,
    budget_variance_usd REAL,
    market_price_usd REAL,
    price_impact_unit_usd REAL,
    price_impact_total_usd REAL,
    price_impact_pct REAL,
    notes TEXT DEFAULT '',
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_procurement_purchase_date
  ON procurement_purchases(purchase_date, product);

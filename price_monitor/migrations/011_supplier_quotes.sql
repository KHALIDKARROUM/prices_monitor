-- Structured supplier quotes, immutable revisions, documents and landed-cost estimates.
CREATE TABLE IF NOT EXISTS supplier_quotes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    supplier TEXT NOT NULL,
    reference TEXT NOT NULL,
    grade TEXT NOT NULL,
    specification TEXT NOT NULL,
    origin TEXT NOT NULL,
    incoterm_place TEXT NOT NULL,
    destination TEXT NOT NULL,
    packaging_detail TEXT NOT NULL,
    payment_terms TEXT NOT NULL,
    notes TEXT NOT NULL,
    product TEXT NOT NULL,
    unit TEXT NOT NULL,
    incoterm TEXT NOT NULL,
    document_type TEXT NOT NULL,
    packaging TEXT NOT NULL,
    status TEXT NOT NULL,
    currency TEXT NOT NULL,
    quote_date TEXT NOT NULL,
    valid_until TEXT NOT NULL,
    delivery_date TEXT NOT NULL,
    quantity REAL NOT NULL,
    unit_price REAL NOT NULL,
    transport_cost REAL NOT NULL,
    insurance_cost REAL NOT NULL,
    inspection_cost REAL NOT NULL,
    duties_cost REAL NOT NULL,
    taxes_cost REAL NOT NULL,
    storage_cost REAL NOT NULL,
    heating_cost REAL NOT NULL,
    finance_cost REAL NOT NULL,
    exchange_rate REAL NOT NULL,
    other_costs_json TEXT NOT NULL,
    version INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS quote_revisions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    quote_id INTEGER NOT NULL,
    version INTEGER NOT NULL,
    action VARCHAR(32) NOT NULL,
    actor VARCHAR(100) NOT NULL,
    reason VARCHAR(1000) NOT NULL,
    changed_fields_json TEXT NOT NULL,
    snapshot_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE (quote_id, version),
    FOREIGN KEY (quote_id) REFERENCES supplier_quotes(id)
);
CREATE TABLE IF NOT EXISTS quote_documents (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    quote_id INTEGER NOT NULL,
    filename VARCHAR(180) NOT NULL,
    document_type VARCHAR(32) NOT NULL,
    content_type VARCHAR(150) NOT NULL,
    size_bytes INTEGER NOT NULL,
    sha256 CHAR(64) NOT NULL,
    content BLOB NOT NULL,
    created_at TEXT NOT NULL,
    removed_at TEXT,
    FOREIGN KEY (quote_id) REFERENCES supplier_quotes(id)
);
ALTER TABLE procurement_purchases ADD COLUMN insurance_cost REAL NOT NULL DEFAULT 0;
ALTER TABLE procurement_purchases ADD COLUMN inspection_cost REAL NOT NULL DEFAULT 0;
ALTER TABLE procurement_purchases ADD COLUMN duties_cost REAL NOT NULL DEFAULT 0;
ALTER TABLE procurement_purchases ADD COLUMN taxes_cost REAL NOT NULL DEFAULT 0;
ALTER TABLE procurement_purchases ADD COLUMN storage_cost REAL NOT NULL DEFAULT 0;
ALTER TABLE procurement_purchases ADD COLUMN heating_cost REAL NOT NULL DEFAULT 0;
ALTER TABLE procurement_purchases ADD COLUMN finance_cost REAL NOT NULL DEFAULT 0;
ALTER TABLE procurement_purchases ADD COLUMN other_costs_json TEXT NOT NULL DEFAULT '[]';
ALTER TABLE procurement_purchases ADD COLUMN quote_id INTEGER;
ALTER TABLE procurement_purchases ADD COLUMN quote_version INTEGER;
ALTER TABLE procurement_purchases ADD COLUMN quote_snapshot_json TEXT;

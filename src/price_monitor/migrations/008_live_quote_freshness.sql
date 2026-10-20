-- Track the exchange timestamp, which may remain unchanged across collections.
ALTER TABLE data_sources ADD COLUMN last_quote_at TEXT;

-- SQLite cannot widen a CHECK constraint in place.
CREATE TABLE alert_rules_new (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    product TEXT NOT NULL CHECK(product IN ('gasoil', 'brent', 'bitume')),
    direction TEXT NOT NULL CHECK(direction IN ('above', 'below', 'stale')),
    threshold REAL NOT NULL CHECK(threshold > 0),
    channel TEXT NOT NULL DEFAULT 'webhook' CHECK(channel IN ('webhook', 'email', 'both')),
    muted INTEGER NOT NULL DEFAULT 0,
    enabled INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(product, direction)
);
INSERT INTO alert_rules_new (id, product, direction, threshold, channel, muted, enabled, created_at, updated_at)
SELECT id, product, direction, threshold, channel, muted, enabled, created_at, updated_at FROM alert_rules;
DROP TABLE alert_rules;
ALTER TABLE alert_rules_new RENAME TO alert_rules;
CREATE INDEX idx_alert_rules_enabled ON alert_rules(enabled, product);

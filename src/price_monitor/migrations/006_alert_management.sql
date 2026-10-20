-- Persist dashboard-managed alert rules and the last transition status.
ALTER TABLE alert_states ADD COLUMN last_status TEXT NOT NULL DEFAULT 'normal';

CREATE TABLE IF NOT EXISTS alert_rules (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    product TEXT NOT NULL CHECK(product IN ('gasoil', 'brent', 'bitume')),
    direction TEXT NOT NULL CHECK(direction IN ('above', 'below')),
    threshold REAL NOT NULL CHECK(threshold > 0),
    channel TEXT NOT NULL DEFAULT 'webhook' CHECK(channel IN ('webhook', 'email', 'both')),
    muted INTEGER NOT NULL DEFAULT 0,
    enabled INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(product, direction)
);
CREATE INDEX IF NOT EXISTS idx_alert_rules_enabled ON alert_rules(enabled, product);

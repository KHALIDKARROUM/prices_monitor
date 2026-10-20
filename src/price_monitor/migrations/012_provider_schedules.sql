CREATE TABLE IF NOT EXISTS provider_collection_state (
    provider TEXT PRIMARY KEY,
    next_due REAL NOT NULL DEFAULT 0,
    failures INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'pending',
    last_error TEXT NOT NULL DEFAULT '',
    last_started REAL NOT NULL DEFAULT 0,
    last_finished REAL NOT NULL DEFAULT 0,
    lease_until REAL NOT NULL DEFAULT 0,
    lease_token TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS provider_request_limits (
    provider TEXT PRIMARY KEY,
    serial INTEGER NOT NULL DEFAULT 0,
    blocked_until REAL NOT NULL DEFAULT 0,
    reason TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS provider_requests (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    provider TEXT NOT NULL,
    attempted_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_provider_requests_window ON provider_requests(provider, attempted_at);
INSERT OR IGNORE INTO provider_collection_state(provider) VALUES ('fred'),('eia'),('alpha_vantage'),('bls'),('yahoo'),('demo');
INSERT OR IGNORE INTO provider_request_limits(provider) VALUES ('fred'),('eia'),('alpha_vantage'),('bls'),('yahoo'),('demo');

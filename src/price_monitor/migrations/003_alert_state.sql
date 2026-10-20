-- Persist notification state so alerts fire on transitions, not every poll.
CREATE TABLE IF NOT EXISTS alert_states (
    alert_key TEXT PRIMARY KEY,
    active INTEGER NOT NULL DEFAULT 0,
    last_value REAL,
    last_notified_at TEXT,
    updated_at TEXT NOT NULL
);

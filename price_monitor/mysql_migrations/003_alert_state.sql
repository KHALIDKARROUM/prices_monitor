-- Persist notification state so alerts fire on transitions, not every poll.
CREATE TABLE IF NOT EXISTS alert_states (
    alert_key VARCHAR(191) PRIMARY KEY,
    active BOOLEAN NOT NULL DEFAULT FALSE,
    `last_value` DECIMAL(14,4) NULL,
    last_notified_at DATETIME NULL,
    updated_at DATETIME NOT NULL
);

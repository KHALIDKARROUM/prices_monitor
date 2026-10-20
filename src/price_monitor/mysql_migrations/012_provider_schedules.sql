CREATE TABLE IF NOT EXISTS provider_collection_state (
    provider VARCHAR(32) PRIMARY KEY,
    next_due DOUBLE NOT NULL DEFAULT 0,
    failures INT NOT NULL DEFAULT 0,
    status VARCHAR(32) NOT NULL DEFAULT 'pending',
    last_error VARCHAR(500) NOT NULL DEFAULT '',
    last_started DOUBLE NOT NULL DEFAULT 0,
    last_finished DOUBLE NOT NULL DEFAULT 0,
    lease_until DOUBLE NOT NULL DEFAULT 0,
    lease_token VARCHAR(32) NOT NULL DEFAULT ''
) ENGINE=InnoDB;
CREATE TABLE IF NOT EXISTS provider_request_limits (
    provider VARCHAR(32) PRIMARY KEY,
    serial BIGINT NOT NULL DEFAULT 0,
    blocked_until DOUBLE NOT NULL DEFAULT 0,
    reason VARCHAR(500) NOT NULL DEFAULT ''
) ENGINE=InnoDB;
CREATE TABLE IF NOT EXISTS provider_requests (
    id BIGINT PRIMARY KEY AUTO_INCREMENT,
    provider VARCHAR(32) NOT NULL,
    attempted_at DOUBLE NOT NULL,
    INDEX idx_provider_requests_window (provider, attempted_at)
) ENGINE=InnoDB;
INSERT IGNORE INTO provider_collection_state(provider) VALUES ('fred'),('eia'),('alpha_vantage'),('bls'),('yahoo'),('demo');
INSERT IGNORE INTO provider_request_limits(provider) VALUES ('fred'),('eia'),('alpha_vantage'),('bls'),('yahoo'),('demo');

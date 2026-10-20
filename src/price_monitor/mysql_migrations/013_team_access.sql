CREATE TABLE IF NOT EXISTS team_users (
    id BIGINT PRIMARY KEY AUTO_INCREMENT,
    username VARCHAR(80) NOT NULL UNIQUE,
    full_name VARCHAR(100) NOT NULL,
    password_hash VARCHAR(255) NOT NULL,
    role VARCHAR(16) NOT NULL,
    active INT NOT NULL DEFAULT 1,
    version INT NOT NULL DEFAULT 1,
    created_at DOUBLE NOT NULL,
    updated_at DOUBLE NOT NULL
) ENGINE=InnoDB;
CREATE TABLE IF NOT EXISTS team_sessions (
    token_hash CHAR(64) PRIMARY KEY,
    user_id BIGINT NOT NULL,
    csrf_token VARCHAR(64) NOT NULL,
    created_at DOUBLE NOT NULL,
    expires_at DOUBLE NOT NULL,
    last_seen_at DOUBLE NOT NULL,
    FOREIGN KEY (user_id) REFERENCES team_users(id)
) ENGINE=InnoDB;
CREATE TABLE IF NOT EXISTS team_api_tokens (
    id BIGINT PRIMARY KEY AUTO_INCREMENT,
    user_id BIGINT NOT NULL,
    token_hash CHAR(64) NOT NULL UNIQUE,
    label VARCHAR(100) NOT NULL,
    created_at DOUBLE NOT NULL,
    expires_at DOUBLE NOT NULL,
    last_used_at DOUBLE,
    revoked_at DOUBLE,
    FOREIGN KEY (user_id) REFERENCES team_users(id)
) ENGINE=InnoDB;
CREATE TABLE IF NOT EXISTS auth_attempts (
    id BIGINT PRIMARY KEY AUTO_INCREMENT,
    client_hash CHAR(64) NOT NULL,
    username_hash CHAR(64) NOT NULL,
    attempted_at DOUBLE NOT NULL,
    INDEX idx_auth_attempt_client (client_hash, attempted_at),
    INDEX idx_auth_attempt_user (username_hash, attempted_at)
) ENGINE=InnoDB;
CREATE TABLE IF NOT EXISTS security_guard (
    id INT PRIMARY KEY,
    serial BIGINT NOT NULL DEFAULT 0
) ENGINE=InnoDB;
INSERT IGNORE INTO security_guard(id) VALUES(1);
CREATE TABLE IF NOT EXISTS audit_events (
    id BIGINT PRIMARY KEY AUTO_INCREMENT,
    occurred_at DOUBLE NOT NULL,
    actor_id BIGINT,
    actor VARCHAR(100) NOT NULL,
    action VARCHAR(80) NOT NULL,
    resource VARCHAR(80) NOT NULL,
    resource_id VARCHAR(80) NOT NULL,
    request_id VARCHAR(64) NOT NULL,
    client_ip VARCHAR(64) NOT NULL,
    outcome VARCHAR(32) NOT NULL,
    details_json LONGTEXT NOT NULL,
    INDEX idx_audit_time (occurred_at, id),
    FOREIGN KEY (actor_id) REFERENCES team_users(id)
) ENGINE=InnoDB;

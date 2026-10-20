ALTER TABLE data_sources ADD COLUMN last_quote_at DATETIME NULL;
ALTER TABLE alert_rules MODIFY direction ENUM('above','below','stale') NOT NULL;

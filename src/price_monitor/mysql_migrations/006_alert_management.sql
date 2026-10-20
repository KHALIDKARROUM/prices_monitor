-- Persist dashboard-managed alert rules and the last transition status.
ALTER TABLE alert_states ADD COLUMN last_status VARCHAR(16) NOT NULL DEFAULT 'normal';

CREATE TABLE IF NOT EXISTS alert_rules (
  id BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
  product ENUM('gasoil','brent','bitume') NOT NULL,
  direction ENUM('above','below') NOT NULL,
  threshold DECIMAL(14,4) NOT NULL,
  channel ENUM('webhook','email','both') NOT NULL DEFAULT 'webhook',
  muted BOOLEAN NOT NULL DEFAULT FALSE,
  enabled BOOLEAN NOT NULL DEFAULT TRUE,
  created_at DATETIME NOT NULL,
  updated_at DATETIME NOT NULL,
  UNIQUE KEY uq_alert_rule_product_direction (product, direction),
  INDEX idx_alert_rules_enabled (enabled, product)
);

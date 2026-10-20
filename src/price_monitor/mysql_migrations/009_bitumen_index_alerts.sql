ALTER TABLE alert_rules ADD COLUMN basis ENUM('quote','index') NOT NULL DEFAULT 'quote';
ALTER TABLE alert_rules DROP INDEX uq_alert_rule_product_direction;
ALTER TABLE alert_rules ADD UNIQUE KEY uq_alert_rule_product_direction (product, direction, basis);

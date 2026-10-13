-- Packaged copy of the MySQL 8 schema used when Price Monitor is installed as a wheel.

CREATE TABLE IF NOT EXISTS price_observations (
  id BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
  product ENUM('gasoil','brent','bitume') NOT NULL,
  price DECIMAL(14,4) NOT NULL,
  unit VARCHAR(32) NOT NULL,
  currency CHAR(3) NOT NULL DEFAULT 'USD',
  source VARCHAR(150) NOT NULL,
  source_date DATE NOT NULL,
  collected_at DATETIME NOT NULL,
  variation DECIMAL(14,6) NULL,
  variation_pct DECIMAL(10,4) NULL,
  is_unchanged BOOLEAN NOT NULL DEFAULT FALSE,
  notes VARCHAR(500) NULL,
  created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  UNIQUE KEY uq_observation_identity (product, source, source_date, price),
  INDEX idx_product_collected (product, collected_at)
);

CREATE TABLE IF NOT EXISTS data_sources (
  id BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
  code VARCHAR(64) NOT NULL UNIQUE,
  label VARCHAR(120) NOT NULL,
  provider VARCHAR(160) NOT NULL,
  frequency VARCHAR(32) NOT NULL,
  active BOOLEAN NOT NULL DEFAULT TRUE,
  last_success_at DATETIME NULL,
  last_error VARCHAR(500) NULL
);

CREATE TABLE IF NOT EXISTS collection_logs (
  id BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
  started_at DATETIME NOT NULL,
  finished_at DATETIME NULL,
  status ENUM('success','partial','error') NOT NULL,
  rows_collected INT NOT NULL DEFAULT 0,
  message VARCHAR(500) NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS schema_migrations (
  version VARCHAR(32) PRIMARY KEY,
  applied_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS alert_states (
  alert_key VARCHAR(191) PRIMARY KEY,
  active BOOLEAN NOT NULL DEFAULT FALSE,
  last_value DECIMAL(14,4) NULL,
  last_notified_at DATETIME NULL,
  updated_at DATETIME NOT NULL
);

CREATE TABLE IF NOT EXISTS procurement_purchases (
  id BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
  product ENUM('gasoil','brent','bitume') NOT NULL,
  supplier VARCHAR(150) NOT NULL,
  quantity DECIMAL(18,6) NOT NULL,
  unit VARCHAR(32) NOT NULL,
  currency CHAR(3) NOT NULL,
  unit_price DECIMAL(18,6) NOT NULL,
  exchange_rate DECIMAL(18,8) NOT NULL,
  transport_cost DECIMAL(18,6) NOT NULL DEFAULT 0,
  budget_amount DECIMAL(18,6) NULL,
  purchase_date DATE NOT NULL,
  total_cost DECIMAL(18,6) NOT NULL,
  total_cost_usd DECIMAL(18,6) NOT NULL,
  budget_variance DECIMAL(18,6) NULL,
  budget_variance_usd DECIMAL(18,6) NULL,
  market_price_usd DECIMAL(18,6) NULL,
  price_impact_unit_usd DECIMAL(18,6) NULL,
  price_impact_total_usd DECIMAL(18,6) NULL,
  price_impact_pct DECIMAL(12,4) NULL,
  notes VARCHAR(500) NULL,
  created_at DATETIME NOT NULL,
  INDEX idx_procurement_purchase_date (purchase_date, product)
);

CREATE TABLE IF NOT EXISTS market_benchmarks (
  id BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
  code VARCHAR(64) NOT NULL,
  product ENUM('gasoil','bitume') NOT NULL,
  label VARCHAR(180) NOT NULL,
  value DECIMAL(18,6) NOT NULL,
  unit VARCHAR(64) NOT NULL,
  geography VARCHAR(100) NOT NULL,
  source VARCHAR(180) NOT NULL,
  source_date DATE NOT NULL,
  collected_at DATETIME NOT NULL,
  source_url VARCHAR(500) NOT NULL DEFAULT '',
  notes VARCHAR(500) NOT NULL DEFAULT '',
  variation DECIMAL(18,6) NULL,
  variation_pct DECIMAL(12,4) NULL,
  created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  UNIQUE KEY uq_market_benchmark_identity (code, source_date, value),
  INDEX idx_market_benchmarks_code_date (code, source_date)
);

CREATE TABLE IF NOT EXISTS supplier_channels (
  id BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
  name VARCHAR(180) NOT NULL,
  product ENUM('gasoil','bitume') NOT NULL,
  region VARCHAR(32) NOT NULL,
  coverage VARCHAR(300) NOT NULL,
  channel VARCHAR(250) NOT NULL,
  typical_terms VARCHAR(300) NOT NULL,
  specifications VARCHAR(300) NOT NULL,
  website_url VARCHAR(500) NOT NULL DEFAULT '',
  contact_url VARCHAR(500) NOT NULL DEFAULT '',
  buyer_note VARCHAR(500) NOT NULL DEFAULT '',
  active BOOLEAN NOT NULL DEFAULT TRUE,
  sort_order INT NOT NULL DEFAULT 100,
  UNIQUE KEY uq_supplier_channel (name, product, region)
);

-- Supplier quote tables; purchase additions are applied by migration 011.
CREATE TABLE IF NOT EXISTS supplier_quotes (
    id BIGINT UNSIGNED PRIMARY KEY AUTO_INCREMENT,
    supplier VARCHAR(150) NOT NULL,
    reference VARCHAR(100) NOT NULL,
    grade VARCHAR(100) NOT NULL,
    specification VARCHAR(150) NOT NULL,
    origin VARCHAR(100) NOT NULL,
    incoterm_place VARCHAR(200) NOT NULL,
    destination VARCHAR(200) NOT NULL,
    packaging_detail VARCHAR(150) NOT NULL,
    payment_terms VARCHAR(200) NOT NULL,
    notes VARCHAR(2000) NOT NULL,
    product VARCHAR(32) NOT NULL,
    unit VARCHAR(32) NOT NULL,
    incoterm VARCHAR(32) NOT NULL,
    document_type VARCHAR(32) NOT NULL,
    packaging VARCHAR(32) NOT NULL,
    status VARCHAR(32) NOT NULL,
    currency VARCHAR(32) NOT NULL,
    quote_date DATE NOT NULL,
    valid_until DATE NOT NULL,
    delivery_date DATE NOT NULL,
    quantity DECIMAL(18,6) NOT NULL,
    unit_price DECIMAL(18,6) NOT NULL,
    transport_cost DECIMAL(18,6) NOT NULL,
    insurance_cost DECIMAL(18,6) NOT NULL,
    inspection_cost DECIMAL(18,6) NOT NULL,
    duties_cost DECIMAL(18,6) NOT NULL,
    taxes_cost DECIMAL(18,6) NOT NULL,
    storage_cost DECIMAL(18,6) NOT NULL,
    heating_cost DECIMAL(18,6) NOT NULL,
    finance_cost DECIMAL(18,6) NOT NULL,
    exchange_rate DECIMAL(18,8) NOT NULL,
    other_costs_json TEXT NOT NULL,
    version INTEGER NOT NULL DEFAULT 1,
    created_at DATETIME(6) NOT NULL,
    updated_at DATETIME(6) NOT NULL
);
CREATE TABLE IF NOT EXISTS quote_revisions (
    id BIGINT UNSIGNED PRIMARY KEY AUTO_INCREMENT,
    quote_id BIGINT UNSIGNED NOT NULL,
    version INTEGER NOT NULL,
    action VARCHAR(32) NOT NULL,
    actor VARCHAR(100) NOT NULL,
    reason VARCHAR(1000) NOT NULL,
    changed_fields_json TEXT NOT NULL,
    snapshot_json LONGTEXT NOT NULL,
    created_at DATETIME(6) NOT NULL,
    UNIQUE (quote_id, version),
    FOREIGN KEY (quote_id) REFERENCES supplier_quotes(id)
);
CREATE TABLE IF NOT EXISTS quote_documents (
    id BIGINT UNSIGNED PRIMARY KEY AUTO_INCREMENT,
    quote_id BIGINT UNSIGNED NOT NULL,
    filename VARCHAR(180) NOT NULL,
    document_type VARCHAR(32) NOT NULL,
    content_type VARCHAR(150) NOT NULL,
    size_bytes INTEGER NOT NULL,
    sha256 CHAR(64) NOT NULL,
    content MEDIUMBLOB NOT NULL,
    created_at DATETIME(6) NOT NULL,
    removed_at DATETIME(6),
    FOREIGN KEY (quote_id) REFERENCES supplier_quotes(id)
);

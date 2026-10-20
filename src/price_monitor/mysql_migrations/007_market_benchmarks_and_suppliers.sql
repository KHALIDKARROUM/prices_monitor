-- Packaged copy of the MySQL migration.
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

UPDATE data_sources SET active=FALSE WHERE code='fred_asphalt';

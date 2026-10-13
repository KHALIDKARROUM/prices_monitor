-- Structured supplier quotes, immutable revisions, documents and landed-cost estimates.
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
ALTER TABLE procurement_purchases ADD COLUMN insurance_cost DECIMAL(18,6) NOT NULL DEFAULT 0;
ALTER TABLE procurement_purchases ADD COLUMN inspection_cost DECIMAL(18,6) NOT NULL DEFAULT 0;
ALTER TABLE procurement_purchases ADD COLUMN duties_cost DECIMAL(18,6) NOT NULL DEFAULT 0;
ALTER TABLE procurement_purchases ADD COLUMN taxes_cost DECIMAL(18,6) NOT NULL DEFAULT 0;
ALTER TABLE procurement_purchases ADD COLUMN storage_cost DECIMAL(18,6) NOT NULL DEFAULT 0;
ALTER TABLE procurement_purchases ADD COLUMN heating_cost DECIMAL(18,6) NOT NULL DEFAULT 0;
ALTER TABLE procurement_purchases ADD COLUMN finance_cost DECIMAL(18,6) NOT NULL DEFAULT 0;
ALTER TABLE procurement_purchases ADD COLUMN other_costs_json TEXT;
ALTER TABLE procurement_purchases ADD COLUMN quote_id BIGINT UNSIGNED;
ALTER TABLE procurement_purchases ADD COLUMN quote_version INTEGER;
ALTER TABLE procurement_purchases ADD COLUMN quote_snapshot_json LONGTEXT;
ALTER TABLE procurement_purchases ADD CONSTRAINT fk_purchase_quote FOREIGN KEY (quote_id) REFERENCES supplier_quotes(id);

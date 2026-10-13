-- Separate instruments while retaining all observations and rule IDs.
CREATE TABLE price_observations_new (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    product TEXT NOT NULL CHECK(product IN ('gasoil', 'brent', 'bitume')),
    instrument TEXT NOT NULL,
    price REAL NOT NULL CHECK(price > 0),
    unit TEXT NOT NULL, currency TEXT NOT NULL DEFAULT 'USD',
    source TEXT NOT NULL, supplier TEXT NOT NULL DEFAULT '',
    source_date TEXT NOT NULL, collected_at TEXT NOT NULL,
    variation REAL, variation_pct REAL, is_unchanged INTEGER NOT NULL DEFAULT 0,
    notes TEXT DEFAULT '', created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(product, instrument, source, source_date, price)
);
INSERT INTO price_observations_new
    (id, product, instrument, price, unit, currency, source, supplier, source_date, collected_at,
     variation, variation_pct, is_unchanged, notes, created_at)
SELECT id, product, CASE
    WHEN source='Yahoo Finance - Brent futures BZ=F' THEN 'brent_futures'
    WHEN source='Yahoo Finance - NY Harbor Heating Oil HO=F (proxy gasoil)' THEN 'heating_oil_futures'
    WHEN product='brent' THEN 'brent_spot'
    WHEN product='gasoil' THEN 'diesel_spot'
    ELSE 'bitumen_quote' END, price, unit, currency, source, supplier, source_date, collected_at,
       variation, variation_pct, is_unchanged, notes, created_at FROM price_observations;
DROP TABLE price_observations;
ALTER TABLE price_observations_new RENAME TO price_observations;
CREATE INDEX idx_observations_product_date ON price_observations(product, collected_at);
CREATE INDEX idx_observations_instrument_date ON price_observations(instrument, source_date, collected_at);
CREATE TABLE alert_rules_new (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    product TEXT NOT NULL CHECK(product IN ('gasoil', 'brent', 'bitume')),
    instrument TEXT NOT NULL,
    direction TEXT NOT NULL CHECK(direction IN ('above', 'below', 'stale')),
    threshold REAL NOT NULL CHECK(threshold > 0),
    channel TEXT NOT NULL DEFAULT 'webhook' CHECK(channel IN ('webhook', 'email', 'both')),
    muted INTEGER NOT NULL DEFAULT 0, enabled INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
    basis TEXT NOT NULL DEFAULT 'quote' CHECK(basis IN ('quote', 'index')),
    UNIQUE(product, instrument, direction, basis)
);
INSERT INTO alert_rules_new
    (id, product, instrument, direction, threshold, channel, muted, enabled, created_at, updated_at, basis)
SELECT id, product, CASE
    WHEN product='brent' AND direction='stale' THEN 'brent_futures'
    WHEN product='gasoil' AND direction='stale' THEN 'heating_oil_futures'
    WHEN product='brent' THEN 'brent_spot'
    WHEN product='gasoil' THEN 'diesel_spot'
    WHEN basis='index' THEN 'asphalt_index'
    ELSE 'bitumen_quote' END, direction, threshold, channel, muted, enabled, created_at, updated_at, basis FROM alert_rules;
DROP TABLE alert_rules;
ALTER TABLE alert_rules_new RENAME TO alert_rules;
CREATE INDEX idx_alert_rules_enabled ON alert_rules(enabled, product);
-- Old product-wide threshold states cannot describe an individual instrument.
DELETE FROM alert_states WHERE alert_key LIKE 'price:brent:%' OR alert_key LIKE 'price:gasoil:%';
WITH previous AS (
    SELECT id, price, source_date,
        LAG(price) OVER (PARTITION BY instrument, source ORDER BY source_date, collected_at, id) AS prior_price,
        LAG(source_date) OVER (PARTITION BY instrument, source ORDER BY source_date, collected_at, id) AS prior_date
    FROM price_observations
)
UPDATE price_observations SET
    variation=(SELECT ROUND(price-prior_price, 6) FROM previous WHERE previous.id=price_observations.id),
    variation_pct=(SELECT ROUND((price-prior_price)/prior_price*100, 4) FROM previous WHERE previous.id=price_observations.id),
    is_unchanged=(SELECT CASE WHEN source_date=prior_date THEN 1 ELSE 0 END FROM previous WHERE previous.id=price_observations.id);

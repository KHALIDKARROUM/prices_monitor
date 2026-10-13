-- Preserve all observations; price alerts migrate to their spot instrument.
ALTER TABLE price_observations ADD COLUMN instrument VARCHAR(64) NOT NULL DEFAULT '';
UPDATE price_observations SET instrument=CASE
    WHEN source='Yahoo Finance - Brent futures BZ=F' THEN 'brent_futures'
    WHEN source='Yahoo Finance - NY Harbor Heating Oil HO=F (proxy gasoil)' THEN 'heating_oil_futures'
    WHEN product='brent' THEN 'brent_spot'
    WHEN product='gasoil' THEN 'diesel_spot'
    ELSE 'bitumen_quote' END;
ALTER TABLE price_observations DROP INDEX uq_observation_identity;
ALTER TABLE price_observations ADD UNIQUE KEY uq_observation_identity (product, instrument, source, source_date, price);
CREATE INDEX idx_observations_instrument_date ON price_observations(instrument, source_date, collected_at);
ALTER TABLE alert_rules ADD COLUMN instrument VARCHAR(64) NOT NULL DEFAULT '';
UPDATE alert_rules SET instrument=CASE
    WHEN product='brent' AND direction='stale' THEN 'brent_futures'
    WHEN product='gasoil' AND direction='stale' THEN 'heating_oil_futures'
    WHEN product='brent' THEN 'brent_spot'
    WHEN product='gasoil' THEN 'diesel_spot'
    WHEN basis='index' THEN 'asphalt_index'
    ELSE 'bitumen_quote' END;
ALTER TABLE alert_rules DROP INDEX uq_alert_rule_product_direction;
ALTER TABLE alert_rules ADD UNIQUE KEY uq_alert_rule_product_direction (product, instrument, direction, basis);
DELETE FROM alert_states WHERE alert_key LIKE 'price:brent:%' OR alert_key LIKE 'price:gasoil:%';
UPDATE price_observations p JOIN (
    SELECT id, price, source_date,
        LAG(price) OVER (PARTITION BY instrument, source ORDER BY source_date, collected_at, id) AS prior_price,
        LAG(source_date) OVER (PARTITION BY instrument, source ORDER BY source_date, collected_at, id) AS prior_date
    FROM price_observations
) previous ON previous.id=p.id SET
    p.variation=ROUND(previous.price-previous.prior_price, 6),
    p.variation_pct=ROUND((previous.price-previous.prior_price)/previous.prior_price*100, 4),
    p.is_unchanged=CASE WHEN previous.source_date=previous.prior_date THEN 1 ELSE 0 END;

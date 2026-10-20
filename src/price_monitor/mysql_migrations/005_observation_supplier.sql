-- Add supplier data for filtered purchase and bitumen history.
ALTER TABLE price_observations ADD COLUMN supplier VARCHAR(150) NOT NULL DEFAULT '' AFTER source;

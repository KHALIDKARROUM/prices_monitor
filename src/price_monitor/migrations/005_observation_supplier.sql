-- Add supplier data for filtered purchase and bitumen history.
ALTER TABLE price_observations ADD COLUMN supplier TEXT NOT NULL DEFAULT '';

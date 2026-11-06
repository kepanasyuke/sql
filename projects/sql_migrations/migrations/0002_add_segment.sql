-- +migrate up
ALTER TABLE dim_client ADD COLUMN segment TEXT;
CREATE INDEX idx_client_segment ON dim_client (segment);

-- +migrate down
DROP INDEX IF EXISTS idx_client_segment;
ALTER TABLE dim_client DROP COLUMN segment;

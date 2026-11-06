-- +migrate up
CREATE VIEW v_active_clients AS
SELECT client_id, name, segment
  FROM dim_client
 WHERE segment IS NOT NULL;

-- +migrate down
DROP VIEW v_active_clients;

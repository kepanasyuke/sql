-- +migrate up
CREATE TABLE dim_client (
    client_id TEXT PRIMARY KEY,
    name      TEXT NOT NULL
);

-- +migrate down
DROP TABLE dim_client;

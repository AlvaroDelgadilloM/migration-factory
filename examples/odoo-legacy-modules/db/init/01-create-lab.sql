CREATE DATABASE odoo12_lab OWNER odoo;

\connect odoo12_lab

CREATE TABLE IF NOT EXISTS legacy_migration_audit (
    id SERIAL PRIMARY KEY,
    event_type VARCHAR(64) NOT NULL,
    reference VARCHAR(128),
    payload TEXT,
    create_date TIMESTAMP NOT NULL DEFAULT NOW()
);

INSERT INTO legacy_migration_audit(event_type, reference, payload)
VALUES
('BOOTSTRAP', 'ODOO12-LAB', '{"source":"postgres-init","version":"12"}'),
('CUSTOM_EVENT', 'LEGACY-001', '{"message":"raw table outside ORM"}');

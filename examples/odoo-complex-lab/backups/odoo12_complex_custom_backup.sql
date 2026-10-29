-- Odoo 12 Complex Migration Lab
-- CUSTOM DATABASE BACKUP
--
-- This is a restorable SQL backup for the custom PostgreSQL objects/data
-- used by the migration lab. It is intentionally included in the project
-- so Migration Factory can analyze database artifacts without first
-- running Odoo.
--
-- IMPORTANT:
-- It does NOT contain the full Odoo ORM-generated core database.
-- A full pg_dump of Odoo core + custom modules is generated after running
-- the Odoo 12 stack with ./scripts/backup_db.sh.

BEGIN;

CREATE SCHEMA IF NOT EXISTS legacy_ext;

CREATE SEQUENCE IF NOT EXISTS legacy_ext.message_seq
    START WITH 1000
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;

CREATE TABLE IF NOT EXISTS legacy_ext.integration_message (
    id BIGINT PRIMARY KEY DEFAULT nextval('legacy_ext.message_seq'),
    message_type VARCHAR(64) NOT NULL,
    external_id VARCHAR(128),
    payload JSONB NOT NULL,
    status VARCHAR(32) NOT NULL DEFAULT 'PENDING',
    retry_count INTEGER NOT NULL DEFAULT 0,
    error_message TEXT,
    created_at TIMESTAMP NOT NULL DEFAULT NOW(),
    processed_at TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_legacy_integration_status
    ON legacy_ext.integration_message(status, created_at);

CREATE TABLE IF NOT EXISTS legacy_ext.credit_snapshot (
    id BIGSERIAL PRIMARY KEY,
    partner_odoo_id INTEGER NOT NULL,
    snapshot_date DATE NOT NULL,
    credit_limit NUMERIC(16,2) NOT NULL DEFAULT 0,
    exposure NUMERIC(16,2) NOT NULL DEFAULT 0,
    risk_level VARCHAR(16),
    UNIQUE(partner_odoo_id, snapshot_date)
);

CREATE TABLE IF NOT EXISTS legacy_ext.quality_event (
    id BIGSERIAL PRIMARY KEY,
    lot_odoo_id INTEGER,
    product_odoo_id INTEGER,
    event_type VARCHAR(32) NOT NULL,
    score NUMERIC(8,2),
    notes TEXT,
    created_at TIMESTAMP NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS legacy_ext.invoice_audit (
    id BIGSERIAL PRIMARY KEY,
    invoice_odoo_id INTEGER,
    external_invoice_id VARCHAR(128),
    legacy_number VARCHAR(64),
    amount_total NUMERIC(16,2),
    event_type VARCHAR(32),
    payload JSONB,
    created_at TIMESTAMP NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS legacy_ext.commission_ledger (
    id BIGSERIAL PRIMARY KEY,
    salesperson_odoo_id INTEGER,
    sale_order_odoo_id INTEGER,
    period VARCHAR(7),
    base_amount NUMERIC(16,2),
    commission_rate NUMERIC(8,4),
    commission_amount NUMERIC(16,2),
    status VARCHAR(32) DEFAULT 'OPEN',
    created_at TIMESTAMP DEFAULT NOW()
);

CREATE OR REPLACE FUNCTION legacy_ext.calculate_risk(
    p_limit NUMERIC,
    p_exposure NUMERIC
) RETURNS VARCHAR AS $$
BEGIN
    IF COALESCE(p_limit,0) <= 0 THEN
        RETURN 'BLOCKED';
    ELSIF p_exposure >= p_limit THEN
        RETURN 'HIGH';
    ELSIF p_exposure >= p_limit * 0.70 THEN
        RETURN 'MEDIUM';
    ELSE
        RETURN 'LOW';
    END IF;
END;
$$ LANGUAGE plpgsql IMMUTABLE;

CREATE OR REPLACE FUNCTION legacy_ext.set_processed_at()
RETURNS TRIGGER AS $$
BEGIN
    IF NEW.status = 'DONE' AND OLD.status IS DISTINCT FROM 'DONE' THEN
        NEW.processed_at = NOW();
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_message_processed
ON legacy_ext.integration_message;

CREATE TRIGGER trg_message_processed
BEFORE UPDATE ON legacy_ext.integration_message
FOR EACH ROW
EXECUTE PROCEDURE legacy_ext.set_processed_at();

DROP VIEW IF EXISTS legacy_ext.v_pending_messages;

CREATE VIEW legacy_ext.v_pending_messages AS
SELECT
    id,
    message_type,
    external_id,
    status,
    retry_count,
    created_at
FROM legacy_ext.integration_message
WHERE status IN ('PENDING','ERROR');

DROP MATERIALIZED VIEW IF EXISTS legacy_ext.mv_commission_monthly;

CREATE MATERIALIZED VIEW legacy_ext.mv_commission_monthly AS
SELECT
    period,
    salesperson_odoo_id,
    SUM(base_amount) AS base_amount,
    SUM(commission_amount) AS commission_amount
FROM legacy_ext.commission_ledger
GROUP BY period, salesperson_odoo_id;

INSERT INTO legacy_ext.integration_message
(message_type, external_id, payload, status, retry_count)
VALUES
('CUSTOMER_SYNC','CUST-001','{"name":"Legacy Customer"}','PENDING',0),
('ORDER_EXPORT','SO-001','{"amount":1500}','PENDING',0),
('INVOICE_EXPORT','INV-001','{"amount":895.50}','ERROR',1);

INSERT INTO legacy_ext.credit_snapshot
(partner_odoo_id, snapshot_date, credit_limit, exposure, risk_level)
VALUES
(101, '2026-01-31', 50000, 12500, 'LOW'),
(102, '2026-01-31', 25000, 21000, 'MEDIUM'),
(103, '2026-01-31', 10000, 13000, 'HIGH');

INSERT INTO legacy_ext.quality_event
(lot_odoo_id, product_odoo_id, event_type, score, notes)
VALUES
(501, 201, 'APPROVED', 98.50, 'Seed quality event'),
(502, 202, 'REJECTED', 42.00, 'Seed rejection');

INSERT INTO legacy_ext.invoice_audit
(invoice_odoo_id, external_invoice_id, legacy_number, amount_total, event_type, payload)
VALUES
(701, 'EXT-INV-001', 'INV/2026/001', 1200.00, 'OPEN', '{"source":"seed"}'),
(702, 'EXT-INV-002', 'INV/2026/002', 9800.00, 'EXPORT', '{"source":"seed"}');

INSERT INTO legacy_ext.commission_ledger
(salesperson_odoo_id, sale_order_odoo_id, period, base_amount, commission_rate, commission_amount, status)
VALUES
(10, 801, '2026-01', 10000, 0.03, 300, 'OPEN'),
(10, 802, '2026-01', 25000, 0.03, 750, 'OPEN'),
(11, 803, '2026-01', 18000, 0.025, 450, 'PAID');

REFRESH MATERIALIZED VIEW legacy_ext.mv_commission_monthly;

COMMIT;

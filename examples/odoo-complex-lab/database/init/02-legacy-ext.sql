\connect odoo12_complex

CREATE SCHEMA IF NOT EXISTS legacy_ext AUTHORIZATION odoo;

CREATE SEQUENCE legacy_ext.message_seq START 1000;

CREATE TABLE legacy_ext.integration_message (
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

CREATE TABLE legacy_ext.credit_snapshot (
    id BIGSERIAL PRIMARY KEY,
    partner_odoo_id INTEGER NOT NULL,
    snapshot_date DATE NOT NULL,
    credit_limit NUMERIC(16,2) NOT NULL DEFAULT 0,
    exposure NUMERIC(16,2) NOT NULL DEFAULT 0,
    risk_level VARCHAR(16),
    UNIQUE(partner_odoo_id, snapshot_date)
);

CREATE TABLE legacy_ext.quality_event (
    id BIGSERIAL PRIMARY KEY,
    lot_odoo_id INTEGER,
    product_odoo_id INTEGER,
    event_type VARCHAR(32) NOT NULL,
    score NUMERIC(8,2),
    notes TEXT,
    created_at TIMESTAMP NOT NULL DEFAULT NOW()
);

CREATE TABLE legacy_ext.invoice_audit (
    id BIGSERIAL PRIMARY KEY,
    invoice_odoo_id INTEGER,
    external_invoice_id VARCHAR(128),
    legacy_number VARCHAR(64),
    amount_total NUMERIC(16,2),
    event_type VARCHAR(32),
    payload JSONB,
    created_at TIMESTAMP NOT NULL DEFAULT NOW()
);

CREATE TABLE legacy_ext.commission_ledger (
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

CREATE OR REPLACE FUNCTION legacy_ext.calculate_risk(p_limit NUMERIC, p_exposure NUMERIC)
RETURNS VARCHAR AS $$
BEGIN
  IF COALESCE(p_limit,0) <= 0 THEN RETURN 'BLOCKED';
  ELSIF p_exposure >= p_limit THEN RETURN 'HIGH';
  ELSIF p_exposure >= p_limit * 0.70 THEN RETURN 'MEDIUM';
  ELSE RETURN 'LOW';
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

CREATE TRIGGER trg_message_processed
BEFORE UPDATE ON legacy_ext.integration_message
FOR EACH ROW EXECUTE PROCEDURE legacy_ext.set_processed_at();

CREATE VIEW legacy_ext.v_pending_messages AS
SELECT id, message_type, external_id, status, retry_count, created_at
FROM legacy_ext.integration_message
WHERE status IN ('PENDING','ERROR');

CREATE MATERIALIZED VIEW legacy_ext.mv_commission_monthly AS
SELECT period, salesperson_odoo_id,
       SUM(base_amount) base_amount,
       SUM(commission_amount) commission_amount
FROM legacy_ext.commission_ledger
GROUP BY period, salesperson_odoo_id;

INSERT INTO legacy_ext.integration_message(message_type, external_id, payload)
VALUES
('CUSTOMER_SYNC','CUST-001','{"name":"Legacy Customer"}'),
('ORDER_EXPORT','SO-001','{"amount":1500}');

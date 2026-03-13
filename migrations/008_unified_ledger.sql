-- Migration 008: Add reference_id column + cash event indexes
--
-- Prepares payment_events for cash events (extrato reconciliation).
-- reference_id stores the original transaction reference as text,
-- decoupling from ml_payment_id (which defaults to 0 for non-payment events).

ALTER TABLE payment_events ADD COLUMN IF NOT EXISTS reference_id TEXT;
UPDATE payment_events SET reference_id = ml_payment_id::text WHERE reference_id IS NULL;
ALTER TABLE payment_events ALTER COLUMN ml_payment_id SET DEFAULT 0;
CREATE INDEX IF NOT EXISTS idx_pe_seller_ref ON payment_events (seller_slug, reference_id) WHERE reference_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_pe_cash_events ON payment_events (seller_slug, event_date) WHERE event_type LIKE 'cash_%';

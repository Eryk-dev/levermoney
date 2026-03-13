-- Migration 006: Create payment_events table (event ledger)
--
-- Append-only ledger of financial events per payment.
-- Each event is immutable with a signed amount:
--   positive = money in (receita, estorno taxa/frete, subsidy)
--   negative = money out (fee, shipping, refund)
--   zero     = flag events (ca_sync, money_released, mediation)
--
-- Balance at any date = SUM(signed_amount) WHERE competencia_date <= date

CREATE TABLE IF NOT EXISTS payment_events (
    id               BIGSERIAL PRIMARY KEY,
    seller_slug      TEXT NOT NULL REFERENCES sellers(slug),
    ml_payment_id    BIGINT NOT NULL,
    ml_order_id      BIGINT,

    event_type       TEXT NOT NULL,
    signed_amount    NUMERIC(12,2) NOT NULL DEFAULT 0,

    competencia_date DATE NOT NULL,
    event_date       DATE NOT NULL,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    source           TEXT NOT NULL DEFAULT 'processor',
    idempotency_key  TEXT NOT NULL,
    metadata         JSONB,

    CONSTRAINT uq_payment_event_idempotency UNIQUE (idempotency_key)
);

-- Primary lookups: events for a specific payment
CREATE INDEX idx_pe_seller_payment ON payment_events (seller_slug, ml_payment_id);

-- DRE queries: aggregate by seller + accounting date
CREATE INDEX idx_pe_seller_comp ON payment_events (seller_slug, competencia_date);

-- Cash flow queries: aggregate by seller + event date
CREATE INDEX idx_pe_seller_event ON payment_events (seller_slug, event_date);

-- Filter by event type
CREATE INDEX idx_pe_type ON payment_events (event_type);

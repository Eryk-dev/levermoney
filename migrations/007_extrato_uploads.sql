-- Migration 007: extrato_uploads — historico de uploads de extratos CSV
--
-- Armazena o resultado de cada ingestao de extrato (account_statement)
-- enviada via Admin Panel. Permite rastrear quais meses foram carregados
-- e quantas linhas foram ingeridas por seller.
--
-- Idempotencia: upsert por (seller_slug, month).
-- Re-upload do mesmo mes atualiza o registro (nao duplica mp_expenses
-- pois o ingester usa composite keys para deduplicacao).

CREATE TABLE IF NOT EXISTS extrato_uploads (
    id                   BIGSERIAL PRIMARY KEY,
    seller_slug          TEXT NOT NULL REFERENCES sellers(slug),
    month                TEXT NOT NULL,              -- "2026-01" (YYYY-MM)
    filename             TEXT,                        -- nome original do arquivo
    uploaded_at          TIMESTAMPTZ DEFAULT NOW(),
    lines_total          INT,
    lines_ingested       INT,
    lines_skipped        INT,
    lines_already_covered INT,
    initial_balance      NUMERIC(12,2),
    final_balance        NUMERIC(12,2),
    status               TEXT DEFAULT 'processing',  -- processing | completed | failed | error
    error_message        TEXT,
    summary              JSONB,                       -- breakdown completo por tipo (by_type dict)
    UNIQUE (seller_slug, month)
);

-- Lookup por seller (aba Extratos do admin)
CREATE INDEX IF NOT EXISTS idx_extrato_uploads_seller
    ON extrato_uploads (seller_slug, month DESC);

-- Lookup por status (para alertas de falha)
CREATE INDEX IF NOT EXISTS idx_extrato_uploads_status
    ON extrato_uploads (status);

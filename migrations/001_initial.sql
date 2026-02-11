-- API Conciliador V2 - Initial Schema
-- Rodar no Supabase SQL Editor

-- Sellers: config por seller ML
CREATE TABLE IF NOT EXISTS sellers (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    slug TEXT UNIQUE NOT NULL,               -- ex: "141air"
    name TEXT NOT NULL,                       -- ex: "141AIR"
    active BOOLEAN DEFAULT false,

    -- ML/MP tokens
    ml_user_id BIGINT,
    ml_access_token TEXT,
    ml_refresh_token TEXT,
    ml_token_expires_at TIMESTAMPTZ,

    -- Conta Azul IDs
    ca_conta_mp_retido TEXT,                 -- UUID da conta "MP Retido - X" no CA
    ca_conta_mp_disponivel TEXT NOT NULL,     -- UUID da conta "X - MP" no CA
    ca_centro_custo_variavel TEXT NOT NULL,   -- UUID do centro de custo VARIÁVEL

    created_at TIMESTAMPTZ DEFAULT now(),
    updated_at TIMESTAMPTZ DEFAULT now()
);

-- Webhook events: log de todos os webhooks recebidos
CREATE TABLE IF NOT EXISTS webhook_events (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    seller_slug TEXT NOT NULL REFERENCES sellers(slug),
    topic TEXT NOT NULL,
    action TEXT,
    resource TEXT,
    data_id TEXT,
    raw_payload JSONB,
    status TEXT DEFAULT 'received',          -- received, processed, error
    created_at TIMESTAMPTZ DEFAULT now()
);

-- Payments: tracking de payments ML processados
CREATE TABLE IF NOT EXISTS payments (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    seller_slug TEXT NOT NULL REFERENCES sellers(slug),
    ml_payment_id BIGINT NOT NULL,
    ml_status TEXT,                           -- approved, refunded, etc
    amount DECIMAL(12,2),
    net_amount DECIMAL(12,2),
    money_release_date DATE,
    status TEXT DEFAULT 'pending',            -- pending, synced, error_ca_receita, refunded, skipped
    error TEXT,
    ca_evento_id TEXT,                        -- ID do evento no Conta Azul
    raw_payment JSONB,
    created_at TIMESTAMPTZ DEFAULT now(),
    updated_at TIMESTAMPTZ DEFAULT now()
);

-- Unique constraint: um payment_id por seller
CREATE UNIQUE INDEX IF NOT EXISTS idx_payments_unique
    ON payments(seller_slug, ml_payment_id);

-- Indexes para queries comuns
CREATE INDEX IF NOT EXISTS idx_payments_status ON payments(status);
CREATE INDEX IF NOT EXISTS idx_payments_release ON payments(money_release_date) WHERE status = 'synced';
CREATE INDEX IF NOT EXISTS idx_webhook_events_seller ON webhook_events(seller_slug, created_at DESC);

-- Sync log: registro de operações de sincronização
CREATE TABLE IF NOT EXISTS sync_log (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    seller_slug TEXT NOT NULL REFERENCES sellers(slug),
    operation TEXT NOT NULL,                  -- payment_approved, payment_refunded, release, withdrawal
    reference_id TEXT,                        -- ml_payment_id ou outro ID
    ca_action TEXT,                           -- criar_conta_receber, criar_conta_pagar, etc
    ca_response JSONB,
    status TEXT DEFAULT 'success',            -- success, error
    error TEXT,
    created_at TIMESTAMPTZ DEFAULT now()
);

-- Insert 141AIR como seller piloto
INSERT INTO sellers (slug, name, ca_conta_mp_disponivel, ca_centro_custo_variavel)
VALUES (
    '141air',
    '141AIR',
    'f0e9908c-2735-4843-8d29-10b8b27f7ff8',     -- 141AIR - MP (disponível)
    'f7c214a6-be2f-11f0-8080-ab23c683d2a1'      -- CC003.1 141AIR - VARIÁVEL
)
ON CONFLICT (slug) DO NOTHING;

-- Nota: ca_conta_mp_retido será preenchido depois que Eryk criar a conta no CA

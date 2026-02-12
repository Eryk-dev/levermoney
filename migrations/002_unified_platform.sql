-- Unified Platform Migration
-- Expands lever money to include dashatt + dash functionality
-- Run in Supabase SQL Editor (project wrbrbhuhsaaupqsimkqz)

-- ============================================================
-- 1. Expand sellers table for onboarding + dashboard link
-- ============================================================

-- Relax NOT NULL constraints for pending sellers
ALTER TABLE sellers ALTER COLUMN ca_conta_mp_disponivel DROP NOT NULL;
ALTER TABLE sellers ALTER COLUMN ca_centro_custo_variavel DROP NOT NULL;

ALTER TABLE sellers ADD COLUMN IF NOT EXISTS email TEXT;
ALTER TABLE sellers ADD COLUMN IF NOT EXISTS onboarding_status TEXT DEFAULT 'active';
  -- pending_approval | approved | active | suspended
ALTER TABLE sellers ADD COLUMN IF NOT EXISTS approved_at TIMESTAMPTZ;
ALTER TABLE sellers ADD COLUMN IF NOT EXISTS dashboard_empresa TEXT;
  -- Nome usado na tabela faturamento (ex: "141AIR")
ALTER TABLE sellers ADD COLUMN IF NOT EXISTS dashboard_grupo TEXT;
ALTER TABLE sellers ADD COLUMN IF NOT EXISTS dashboard_segmento TEXT DEFAULT 'OUTROS';
ALTER TABLE sellers ADD COLUMN IF NOT EXISTS source TEXT DEFAULT 'ml';
  -- 'ml' | 'manual'
ALTER TABLE sellers ADD COLUMN IF NOT EXISTS ml_app_id TEXT;
ALTER TABLE sellers ADD COLUMN IF NOT EXISTS ml_secret_key TEXT;
  -- Per-seller ML app credentials (dashatt uses different apps)
ALTER TABLE sellers ADD COLUMN IF NOT EXISTS ca_contato_ml TEXT;
  -- UUID do contato "MERCADO LIVRE" no CA

-- Set dashboard_empresa for existing 141AIR seller
UPDATE sellers SET dashboard_empresa = '141AIR', dashboard_grupo = 'NETAIR',
  dashboard_segmento = 'AR CONDICIONADO', onboarding_status = 'active'
WHERE slug = '141air';

-- ============================================================
-- 2. New tables
-- ============================================================

-- Faturamento (migrated from dashatt Supabase)
CREATE TABLE IF NOT EXISTS faturamento (
    id BIGSERIAL PRIMARY KEY,
    empresa TEXT NOT NULL,
    data DATE NOT NULL,
    valor NUMERIC DEFAULT 0,
    source TEXT DEFAULT 'sync', -- 'sync' | 'manual'
    created_at TIMESTAMPTZ DEFAULT now(),
    updated_at TIMESTAMPTZ DEFAULT now(),
    UNIQUE(empresa, data)
);
CREATE INDEX IF NOT EXISTS idx_faturamento_data ON faturamento(data);
CREATE INDEX IF NOT EXISTS idx_faturamento_empresa ON faturamento(empresa);

-- Revenue lines (replaces hardcoded COMPANIES + localStorage)
CREATE TABLE IF NOT EXISTS revenue_lines (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    empresa TEXT UNIQUE NOT NULL,
    grupo TEXT NOT NULL DEFAULT 'OUTROS',
    segmento TEXT NOT NULL DEFAULT 'OUTROS',
    seller_id UUID REFERENCES sellers(id),  -- NULL for manual lines
    source TEXT DEFAULT 'manual',            -- 'ml' | 'manual'
    active BOOLEAN DEFAULT true,
    created_at TIMESTAMPTZ DEFAULT now()
);

-- Goals (replaces localStorage)
CREATE TABLE IF NOT EXISTS goals (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    empresa TEXT NOT NULL,
    grupo TEXT NOT NULL,
    year INTEGER NOT NULL DEFAULT 2026,
    month INTEGER NOT NULL,  -- 1-12
    valor NUMERIC NOT NULL DEFAULT 0,
    UNIQUE(empresa, year, month)
);

-- MeLi tokens (migrated from dashatt Supabase)
CREATE TABLE IF NOT EXISTS meli_tokens (
    account_name TEXT PRIMARY KEY,
    seller_id UUID REFERENCES sellers(id),
    refresh_token TEXT NOT NULL,
    access_token TEXT,
    access_token_expires_at TIMESTAMPTZ,
    updated_at TIMESTAMPTZ DEFAULT now()
);

-- Admin config
CREATE TABLE IF NOT EXISTS admin_config (
    id INTEGER PRIMARY KEY DEFAULT 1,
    password_hash TEXT NOT NULL
);

-- ============================================================
-- 3. RLS Policies
-- ============================================================

-- Enable RLS
ALTER TABLE faturamento ENABLE ROW LEVEL SECURITY;
ALTER TABLE revenue_lines ENABLE ROW LEVEL SECURITY;
ALTER TABLE goals ENABLE ROW LEVEL SECURITY;
ALTER TABLE meli_tokens ENABLE ROW LEVEL SECURITY;
ALTER TABLE admin_config ENABLE ROW LEVEL SECURITY;

-- faturamento: anon can SELECT, service_role has ALL
CREATE POLICY "faturamento_anon_select" ON faturamento FOR SELECT TO anon USING (true);
CREATE POLICY "faturamento_service_all" ON faturamento FOR ALL TO service_role USING (true);

-- revenue_lines: anon can SELECT, service_role has ALL
CREATE POLICY "revenue_lines_anon_select" ON revenue_lines FOR SELECT TO anon USING (true);
CREATE POLICY "revenue_lines_service_all" ON revenue_lines FOR ALL TO service_role USING (true);

-- goals: anon can SELECT, service_role has ALL
CREATE POLICY "goals_anon_select" ON goals FOR SELECT TO anon USING (true);
CREATE POLICY "goals_service_all" ON goals FOR ALL TO service_role USING (true);

-- meli_tokens: service_role only
CREATE POLICY "meli_tokens_service_all" ON meli_tokens FOR ALL TO service_role USING (true);

-- admin_config: service_role only
CREATE POLICY "admin_config_service_all" ON admin_config FOR ALL TO service_role USING (true);

-- Enable realtime for faturamento, revenue_lines, goals
ALTER PUBLICATION supabase_realtime ADD TABLE faturamento;
ALTER PUBLICATION supabase_realtime ADD TABLE revenue_lines;
ALTER PUBLICATION supabase_realtime ADD TABLE goals;

-- ============================================================
-- 4. Seed data: Revenue Lines (24 lines from fallbackData.ts)
-- ============================================================

INSERT INTO revenue_lines (empresa, grupo, segmento, source) VALUES
  -- NETAIR group
  ('NETAIR', 'NETAIR', 'AR CONDICIONADO', 'ml'),
  ('NETPARTS', 'NETAIR', 'AR CONDICIONADO', 'ml'),
  ('141AIR', 'NETAIR', 'AR CONDICIONADO', 'ml'),
  ('SHOPEE NETAIR', 'NETAIR', 'AR CONDICIONADO', 'manual'),
  ('VITAO', 'NETAIR', 'AR CONDICIONADO', 'manual'),
  ('VINICIUS', 'NETAIR', 'AR CONDICIONADO', 'manual'),
  ('ARTHUR', 'NETAIR', 'AR CONDICIONADO', 'manual'),
  ('JONATHAN', 'NETAIR', 'AR CONDICIONADO', 'manual'),
  -- ACA group
  ('AUTOFY (CONDENSADORES )', 'ACA', 'AR CONDICIONADO', 'ml'),
  ('AUTOMY', 'ACA', 'AR CONDICIONADO', 'ml'),
  ('SHOPEE ACA', 'ACA', 'AR CONDICIONADO', 'manual'),
  -- EASY group
  ('EASYPEASY SP', 'EASY', 'UTILIDADES', 'ml'),
  ('EASYPEASY CWB', 'EASY', 'UTILIDADES', 'ml'),
  ('SHOPEE EASY', 'EASY', 'UTILIDADES', 'manual'),
  -- BELLATOR group
  ('BELLATOR CWB', 'BELLATOR', 'BALESTRA', 'ml'),
  ('BELLATOR - JUNIOR', 'BELLATOR', 'BALESTRA', 'manual'),
  ('BELLATOR - SITE', 'BELLATOR', 'BALESTRA', 'manual'),
  -- UNIQUE group
  ('ML 1 - UNIQUE', 'UNIQUE', 'PRESENTES', 'ml'),
  ('ML 2 - UNIQUE', 'UNIQUE', 'PRESENTES', 'ml'),
  ('UNIQUEKIDS', 'UNIQUE', 'PRESENTES', 'ml'),
  ('UNIQUEBOX', 'UNIQUE', 'PRESENTES', 'ml'),
  ('MANU', 'UNIQUE', 'PRESENTES', 'manual'),
  ('REPRESENTANTES', 'UNIQUE', 'PRESENTES', 'manual'),
  ('SITE TERCEIROS', 'UNIQUE', 'PRESENTES', 'manual')
ON CONFLICT (empresa) DO NOTHING;

-- ============================================================
-- 5. Seed data: Goals 2026 (from goals.ts)
-- ============================================================

-- NETAIR
INSERT INTO goals (empresa, grupo, year, month, valor) VALUES
  ('NETAIR', 'NETAIR', 2026, 1, 1000000), ('NETAIR', 'NETAIR', 2026, 2, 1000000),
  ('NETAIR', 'NETAIR', 2026, 3, 1000000), ('NETAIR', 'NETAIR', 2026, 4, 800000),
  ('NETAIR', 'NETAIR', 2026, 5, 700000), ('NETAIR', 'NETAIR', 2026, 6, 700000),
  ('NETAIR', 'NETAIR', 2026, 7, 700000), ('NETAIR', 'NETAIR', 2026, 8, 700000),
  ('NETAIR', 'NETAIR', 2026, 9, 700000), ('NETAIR', 'NETAIR', 2026, 10, 800000),
  ('NETAIR', 'NETAIR', 2026, 11, 1100000), ('NETAIR', 'NETAIR', 2026, 12, 1100000)
ON CONFLICT (empresa, year, month) DO NOTHING;

-- NETPARTS
INSERT INTO goals (empresa, grupo, year, month, valor) VALUES
  ('NETPARTS', 'NETAIR', 2026, 1, 800000), ('NETPARTS', 'NETAIR', 2026, 2, 800000),
  ('NETPARTS', 'NETAIR', 2026, 3, 800000), ('NETPARTS', 'NETAIR', 2026, 4, 700000),
  ('NETPARTS', 'NETAIR', 2026, 5, 600000), ('NETPARTS', 'NETAIR', 2026, 6, 600000),
  ('NETPARTS', 'NETAIR', 2026, 7, 600000), ('NETPARTS', 'NETAIR', 2026, 8, 600000),
  ('NETPARTS', 'NETAIR', 2026, 9, 800000), ('NETPARTS', 'NETAIR', 2026, 10, 800000),
  ('NETPARTS', 'NETAIR', 2026, 11, 1000000), ('NETPARTS', 'NETAIR', 2026, 12, 1000000)
ON CONFLICT (empresa, year, month) DO NOTHING;

-- SHOPEE NETAIR
INSERT INTO goals (empresa, grupo, year, month, valor) VALUES
  ('SHOPEE NETAIR', 'NETAIR', 2026, 1, 70000), ('SHOPEE NETAIR', 'NETAIR', 2026, 2, 80000),
  ('SHOPEE NETAIR', 'NETAIR', 2026, 3, 90000), ('SHOPEE NETAIR', 'NETAIR', 2026, 4, 80000),
  ('SHOPEE NETAIR', 'NETAIR', 2026, 5, 70000), ('SHOPEE NETAIR', 'NETAIR', 2026, 6, 70000),
  ('SHOPEE NETAIR', 'NETAIR', 2026, 7, 70000), ('SHOPEE NETAIR', 'NETAIR', 2026, 8, 70000),
  ('SHOPEE NETAIR', 'NETAIR', 2026, 9, 70000), ('SHOPEE NETAIR', 'NETAIR', 2026, 10, 90000),
  ('SHOPEE NETAIR', 'NETAIR', 2026, 11, 100000), ('SHOPEE NETAIR', 'NETAIR', 2026, 12, 110000)
ON CONFLICT (empresa, year, month) DO NOTHING;

-- 141AIR
INSERT INTO goals (empresa, grupo, year, month, valor) VALUES
  ('141AIR', 'NETAIR', 2026, 1, 140000), ('141AIR', 'NETAIR', 2026, 2, 140000),
  ('141AIR', 'NETAIR', 2026, 3, 120000), ('141AIR', 'NETAIR', 2026, 4, 120000),
  ('141AIR', 'NETAIR', 2026, 5, 100000), ('141AIR', 'NETAIR', 2026, 6, 100000),
  ('141AIR', 'NETAIR', 2026, 7, 120000), ('141AIR', 'NETAIR', 2026, 8, 130000),
  ('141AIR', 'NETAIR', 2026, 9, 140000), ('141AIR', 'NETAIR', 2026, 10, 150000),
  ('141AIR', 'NETAIR', 2026, 11, 160000), ('141AIR', 'NETAIR', 2026, 12, 170000)
ON CONFLICT (empresa, year, month) DO NOTHING;

-- VITAO
INSERT INTO goals (empresa, grupo, year, month, valor) VALUES
  ('VITAO', 'NETAIR', 2026, 1, 20000), ('VITAO', 'NETAIR', 2026, 2, 30000),
  ('VITAO', 'NETAIR', 2026, 3, 40000), ('VITAO', 'NETAIR', 2026, 4, 50000),
  ('VITAO', 'NETAIR', 2026, 5, 60000), ('VITAO', 'NETAIR', 2026, 6, 70000),
  ('VITAO', 'NETAIR', 2026, 7, 80000), ('VITAO', 'NETAIR', 2026, 8, 90000),
  ('VITAO', 'NETAIR', 2026, 9, 100000), ('VITAO', 'NETAIR', 2026, 10, 110000),
  ('VITAO', 'NETAIR', 2026, 11, 120000), ('VITAO', 'NETAIR', 2026, 12, 130000)
ON CONFLICT (empresa, year, month) DO NOTHING;

-- VINICIUS
INSERT INTO goals (empresa, grupo, year, month, valor) VALUES
  ('VINICIUS', 'NETAIR', 2026, 1, 80000), ('VINICIUS', 'NETAIR', 2026, 2, 80000),
  ('VINICIUS', 'NETAIR', 2026, 3, 70000), ('VINICIUS', 'NETAIR', 2026, 4, 70000),
  ('VINICIUS', 'NETAIR', 2026, 5, 70000), ('VINICIUS', 'NETAIR', 2026, 6, 70000),
  ('VINICIUS', 'NETAIR', 2026, 7, 70000), ('VINICIUS', 'NETAIR', 2026, 8, 70000),
  ('VINICIUS', 'NETAIR', 2026, 9, 80000), ('VINICIUS', 'NETAIR', 2026, 10, 90000),
  ('VINICIUS', 'NETAIR', 2026, 11, 100000), ('VINICIUS', 'NETAIR', 2026, 12, 110000)
ON CONFLICT (empresa, year, month) DO NOTHING;

-- ARTHUR
INSERT INTO goals (empresa, grupo, year, month, valor) VALUES
  ('ARTHUR', 'NETAIR', 2026, 1, 20000), ('ARTHUR', 'NETAIR', 2026, 2, 25000),
  ('ARTHUR', 'NETAIR', 2026, 3, 30000), ('ARTHUR', 'NETAIR', 2026, 4, 35000),
  ('ARTHUR', 'NETAIR', 2026, 5, 40000), ('ARTHUR', 'NETAIR', 2026, 6, 45000),
  ('ARTHUR', 'NETAIR', 2026, 7, 50000), ('ARTHUR', 'NETAIR', 2026, 8, 55000),
  ('ARTHUR', 'NETAIR', 2026, 9, 60000), ('ARTHUR', 'NETAIR', 2026, 10, 65000),
  ('ARTHUR', 'NETAIR', 2026, 11, 70000), ('ARTHUR', 'NETAIR', 2026, 12, 75000)
ON CONFLICT (empresa, year, month) DO NOTHING;

-- JONATHAN
INSERT INTO goals (empresa, grupo, year, month, valor) VALUES
  ('JONATHAN', 'NETAIR', 2026, 1, 0), ('JONATHAN', 'NETAIR', 2026, 2, 0),
  ('JONATHAN', 'NETAIR', 2026, 3, 20000), ('JONATHAN', 'NETAIR', 2026, 4, 25000),
  ('JONATHAN', 'NETAIR', 2026, 5, 30000), ('JONATHAN', 'NETAIR', 2026, 6, 35000),
  ('JONATHAN', 'NETAIR', 2026, 7, 40000), ('JONATHAN', 'NETAIR', 2026, 8, 45000),
  ('JONATHAN', 'NETAIR', 2026, 9, 50000), ('JONATHAN', 'NETAIR', 2026, 10, 55000),
  ('JONATHAN', 'NETAIR', 2026, 11, 60000), ('JONATHAN', 'NETAIR', 2026, 12, 65000)
ON CONFLICT (empresa, year, month) DO NOTHING;

-- AUTOFY (CONDENSADORES )
INSERT INTO goals (empresa, grupo, year, month, valor) VALUES
  ('AUTOFY (CONDENSADORES )', 'ACA', 2026, 1, 80000), ('AUTOFY (CONDENSADORES )', 'ACA', 2026, 2, 200000),
  ('AUTOFY (CONDENSADORES )', 'ACA', 2026, 3, 280000), ('AUTOFY (CONDENSADORES )', 'ACA', 2026, 4, 360000),
  ('AUTOFY (CONDENSADORES )', 'ACA', 2026, 5, 440000), ('AUTOFY (CONDENSADORES )', 'ACA', 2026, 6, 520000),
  ('AUTOFY (CONDENSADORES )', 'ACA', 2026, 7, 600000), ('AUTOFY (CONDENSADORES )', 'ACA', 2026, 8, 680000),
  ('AUTOFY (CONDENSADORES )', 'ACA', 2026, 9, 760000), ('AUTOFY (CONDENSADORES )', 'ACA', 2026, 10, 840000),
  ('AUTOFY (CONDENSADORES )', 'ACA', 2026, 11, 920000), ('AUTOFY (CONDENSADORES )', 'ACA', 2026, 12, 1000000)
ON CONFLICT (empresa, year, month) DO NOTHING;

-- AUTOMY
INSERT INTO goals (empresa, grupo, year, month, valor) VALUES
  ('AUTOMY', 'ACA', 2026, 1, 20000), ('AUTOMY', 'ACA', 2026, 2, 50000),
  ('AUTOMY', 'ACA', 2026, 3, 70000), ('AUTOMY', 'ACA', 2026, 4, 90000),
  ('AUTOMY', 'ACA', 2026, 5, 110000), ('AUTOMY', 'ACA', 2026, 6, 130000),
  ('AUTOMY', 'ACA', 2026, 7, 150000), ('AUTOMY', 'ACA', 2026, 8, 170000),
  ('AUTOMY', 'ACA', 2026, 9, 190000), ('AUTOMY', 'ACA', 2026, 10, 210000),
  ('AUTOMY', 'ACA', 2026, 11, 230000), ('AUTOMY', 'ACA', 2026, 12, 250000)
ON CONFLICT (empresa, year, month) DO NOTHING;

-- SHOPEE ACA (all zeros)
INSERT INTO goals (empresa, grupo, year, month, valor) VALUES
  ('SHOPEE ACA', 'ACA', 2026, 1, 0), ('SHOPEE ACA', 'ACA', 2026, 2, 0),
  ('SHOPEE ACA', 'ACA', 2026, 3, 0), ('SHOPEE ACA', 'ACA', 2026, 4, 0),
  ('SHOPEE ACA', 'ACA', 2026, 5, 0), ('SHOPEE ACA', 'ACA', 2026, 6, 0),
  ('SHOPEE ACA', 'ACA', 2026, 7, 0), ('SHOPEE ACA', 'ACA', 2026, 8, 0),
  ('SHOPEE ACA', 'ACA', 2026, 9, 0), ('SHOPEE ACA', 'ACA', 2026, 10, 0),
  ('SHOPEE ACA', 'ACA', 2026, 11, 0), ('SHOPEE ACA', 'ACA', 2026, 12, 0)
ON CONFLICT (empresa, year, month) DO NOTHING;

-- EASYPEASY SP
INSERT INTO goals (empresa, grupo, year, month, valor) VALUES
  ('EASYPEASY SP', 'EASY', 2026, 1, 30000), ('EASYPEASY SP', 'EASY', 2026, 2, 25000),
  ('EASYPEASY SP', 'EASY', 2026, 3, 30000), ('EASYPEASY SP', 'EASY', 2026, 4, 25000),
  ('EASYPEASY SP', 'EASY', 2026, 5, 20000), ('EASYPEASY SP', 'EASY', 2026, 6, 20000),
  ('EASYPEASY SP', 'EASY', 2026, 7, 30000), ('EASYPEASY SP', 'EASY', 2026, 8, 25000),
  ('EASYPEASY SP', 'EASY', 2026, 9, 20000), ('EASYPEASY SP', 'EASY', 2026, 10, 30000),
  ('EASYPEASY SP', 'EASY', 2026, 11, 25000), ('EASYPEASY SP', 'EASY', 2026, 12, 30000)
ON CONFLICT (empresa, year, month) DO NOTHING;

-- EASYPEASY CWB
INSERT INTO goals (empresa, grupo, year, month, valor) VALUES
  ('EASYPEASY CWB', 'EASY', 2026, 1, 250000), ('EASYPEASY CWB', 'EASY', 2026, 2, 208000),
  ('EASYPEASY CWB', 'EASY', 2026, 3, 250000), ('EASYPEASY CWB', 'EASY', 2026, 4, 208000),
  ('EASYPEASY CWB', 'EASY', 2026, 5, 167000), ('EASYPEASY CWB', 'EASY', 2026, 6, 167000),
  ('EASYPEASY CWB', 'EASY', 2026, 7, 250000), ('EASYPEASY CWB', 'EASY', 2026, 8, 208000),
  ('EASYPEASY CWB', 'EASY', 2026, 9, 167000), ('EASYPEASY CWB', 'EASY', 2026, 10, 250000),
  ('EASYPEASY CWB', 'EASY', 2026, 11, 208000), ('EASYPEASY CWB', 'EASY', 2026, 12, 250000)
ON CONFLICT (empresa, year, month) DO NOTHING;

-- SHOPEE EASY
INSERT INTO goals (empresa, grupo, year, month, valor) VALUES
  ('SHOPEE EASY', 'EASY', 2026, 1, 20000), ('SHOPEE EASY', 'EASY', 2026, 2, 17000),
  ('SHOPEE EASY', 'EASY', 2026, 3, 20000), ('SHOPEE EASY', 'EASY', 2026, 4, 17000),
  ('SHOPEE EASY', 'EASY', 2026, 5, 13000), ('SHOPEE EASY', 'EASY', 2026, 6, 13000),
  ('SHOPEE EASY', 'EASY', 2026, 7, 20000), ('SHOPEE EASY', 'EASY', 2026, 8, 17000),
  ('SHOPEE EASY', 'EASY', 2026, 9, 13000), ('SHOPEE EASY', 'EASY', 2026, 10, 20000),
  ('SHOPEE EASY', 'EASY', 2026, 11, 17000), ('SHOPEE EASY', 'EASY', 2026, 12, 20000)
ON CONFLICT (empresa, year, month) DO NOTHING;

-- BELLATOR CWB
INSERT INTO goals (empresa, grupo, year, month, valor) VALUES
  ('BELLATOR CWB', 'BELLATOR', 2026, 1, 80000), ('BELLATOR CWB', 'BELLATOR', 2026, 2, 70000),
  ('BELLATOR CWB', 'BELLATOR', 2026, 3, 130000), ('BELLATOR CWB', 'BELLATOR', 2026, 4, 120000),
  ('BELLATOR CWB', 'BELLATOR', 2026, 5, 80000), ('BELLATOR CWB', 'BELLATOR', 2026, 6, 140000),
  ('BELLATOR CWB', 'BELLATOR', 2026, 7, 120000), ('BELLATOR CWB', 'BELLATOR', 2026, 8, 100000),
  ('BELLATOR CWB', 'BELLATOR', 2026, 9, 100000), ('BELLATOR CWB', 'BELLATOR', 2026, 10, 180000),
  ('BELLATOR CWB', 'BELLATOR', 2026, 11, 180000), ('BELLATOR CWB', 'BELLATOR', 2026, 12, 200000)
ON CONFLICT (empresa, year, month) DO NOTHING;

-- BELLATOR - JUNIOR (all zeros)
INSERT INTO goals (empresa, grupo, year, month, valor) VALUES
  ('BELLATOR - JUNIOR', 'BELLATOR', 2026, 1, 0), ('BELLATOR - JUNIOR', 'BELLATOR', 2026, 2, 0),
  ('BELLATOR - JUNIOR', 'BELLATOR', 2026, 3, 0), ('BELLATOR - JUNIOR', 'BELLATOR', 2026, 4, 0),
  ('BELLATOR - JUNIOR', 'BELLATOR', 2026, 5, 0), ('BELLATOR - JUNIOR', 'BELLATOR', 2026, 6, 0),
  ('BELLATOR - JUNIOR', 'BELLATOR', 2026, 7, 0), ('BELLATOR - JUNIOR', 'BELLATOR', 2026, 8, 0),
  ('BELLATOR - JUNIOR', 'BELLATOR', 2026, 9, 0), ('BELLATOR - JUNIOR', 'BELLATOR', 2026, 10, 0),
  ('BELLATOR - JUNIOR', 'BELLATOR', 2026, 11, 0), ('BELLATOR - JUNIOR', 'BELLATOR', 2026, 12, 0)
ON CONFLICT (empresa, year, month) DO NOTHING;

-- BELLATOR - SITE (all zeros)
INSERT INTO goals (empresa, grupo, year, month, valor) VALUES
  ('BELLATOR - SITE', 'BELLATOR', 2026, 1, 0), ('BELLATOR - SITE', 'BELLATOR', 2026, 2, 0),
  ('BELLATOR - SITE', 'BELLATOR', 2026, 3, 0), ('BELLATOR - SITE', 'BELLATOR', 2026, 4, 0),
  ('BELLATOR - SITE', 'BELLATOR', 2026, 5, 0), ('BELLATOR - SITE', 'BELLATOR', 2026, 6, 0),
  ('BELLATOR - SITE', 'BELLATOR', 2026, 7, 0), ('BELLATOR - SITE', 'BELLATOR', 2026, 8, 0),
  ('BELLATOR - SITE', 'BELLATOR', 2026, 9, 0), ('BELLATOR - SITE', 'BELLATOR', 2026, 10, 0),
  ('BELLATOR - SITE', 'BELLATOR', 2026, 11, 0), ('BELLATOR - SITE', 'BELLATOR', 2026, 12, 0)
ON CONFLICT (empresa, year, month) DO NOTHING;

-- ML 1 - UNIQUE
INSERT INTO goals (empresa, grupo, year, month, valor) VALUES
  ('ML 1 - UNIQUE', 'UNIQUE', 2026, 1, 25000), ('ML 1 - UNIQUE', 'UNIQUE', 2026, 2, 35000),
  ('ML 1 - UNIQUE', 'UNIQUE', 2026, 3, 45000), ('ML 1 - UNIQUE', 'UNIQUE', 2026, 4, 55000),
  ('ML 1 - UNIQUE', 'UNIQUE', 2026, 5, 65000), ('ML 1 - UNIQUE', 'UNIQUE', 2026, 6, 100000),
  ('ML 1 - UNIQUE', 'UNIQUE', 2026, 7, 85000), ('ML 1 - UNIQUE', 'UNIQUE', 2026, 8, 95000),
  ('ML 1 - UNIQUE', 'UNIQUE', 2026, 9, 105000), ('ML 1 - UNIQUE', 'UNIQUE', 2026, 10, 115000),
  ('ML 1 - UNIQUE', 'UNIQUE', 2026, 11, 125000), ('ML 1 - UNIQUE', 'UNIQUE', 2026, 12, 135000)
ON CONFLICT (empresa, year, month) DO NOTHING;

-- ML 2 - UNIQUE
INSERT INTO goals (empresa, grupo, year, month, valor) VALUES
  ('ML 2 - UNIQUE', 'UNIQUE', 2026, 1, 50000), ('ML 2 - UNIQUE', 'UNIQUE', 2026, 2, 60000),
  ('ML 2 - UNIQUE', 'UNIQUE', 2026, 3, 70000), ('ML 2 - UNIQUE', 'UNIQUE', 2026, 4, 80000),
  ('ML 2 - UNIQUE', 'UNIQUE', 2026, 5, 90000), ('ML 2 - UNIQUE', 'UNIQUE', 2026, 6, 100000),
  ('ML 2 - UNIQUE', 'UNIQUE', 2026, 7, 110000), ('ML 2 - UNIQUE', 'UNIQUE', 2026, 8, 120000),
  ('ML 2 - UNIQUE', 'UNIQUE', 2026, 9, 130000), ('ML 2 - UNIQUE', 'UNIQUE', 2026, 10, 140000),
  ('ML 2 - UNIQUE', 'UNIQUE', 2026, 11, 150000), ('ML 2 - UNIQUE', 'UNIQUE', 2026, 12, 160000)
ON CONFLICT (empresa, year, month) DO NOTHING;

-- UNIQUEKIDS
INSERT INTO goals (empresa, grupo, year, month, valor) VALUES
  ('UNIQUEKIDS', 'UNIQUE', 2026, 1, 100000), ('UNIQUEKIDS', 'UNIQUE', 2026, 2, 110000),
  ('UNIQUEKIDS', 'UNIQUE', 2026, 3, 120000), ('UNIQUEKIDS', 'UNIQUE', 2026, 4, 130000),
  ('UNIQUEKIDS', 'UNIQUE', 2026, 5, 140000), ('UNIQUEKIDS', 'UNIQUE', 2026, 6, 150000),
  ('UNIQUEKIDS', 'UNIQUE', 2026, 7, 160000), ('UNIQUEKIDS', 'UNIQUE', 2026, 8, 170000),
  ('UNIQUEKIDS', 'UNIQUE', 2026, 9, 250000), ('UNIQUEKIDS', 'UNIQUE', 2026, 10, 250000),
  ('UNIQUEKIDS', 'UNIQUE', 2026, 11, 200000), ('UNIQUEKIDS', 'UNIQUE', 2026, 12, 210000)
ON CONFLICT (empresa, year, month) DO NOTHING;

-- UNIQUEBOX
INSERT INTO goals (empresa, grupo, year, month, valor) VALUES
  ('UNIQUEBOX', 'UNIQUE', 2026, 1, 30000), ('UNIQUEBOX', 'UNIQUE', 2026, 2, 30000),
  ('UNIQUEBOX', 'UNIQUE', 2026, 3, 30000), ('UNIQUEBOX', 'UNIQUE', 2026, 4, 50000),
  ('UNIQUEBOX', 'UNIQUE', 2026, 5, 300000), ('UNIQUEBOX', 'UNIQUE', 2026, 6, 500000),
  ('UNIQUEBOX', 'UNIQUE', 2026, 7, 30000), ('UNIQUEBOX', 'UNIQUE', 2026, 8, 30000),
  ('UNIQUEBOX', 'UNIQUE', 2026, 9, 30000), ('UNIQUEBOX', 'UNIQUE', 2026, 10, 30000),
  ('UNIQUEBOX', 'UNIQUE', 2026, 11, 50000), ('UNIQUEBOX', 'UNIQUE', 2026, 12, 70000)
ON CONFLICT (empresa, year, month) DO NOTHING;

-- MANU
INSERT INTO goals (empresa, grupo, year, month, valor) VALUES
  ('MANU', 'UNIQUE', 2026, 1, 70000), ('MANU', 'UNIQUE', 2026, 2, 75000),
  ('MANU', 'UNIQUE', 2026, 3, 80000), ('MANU', 'UNIQUE', 2026, 4, 85000),
  ('MANU', 'UNIQUE', 2026, 5, 90000), ('MANU', 'UNIQUE', 2026, 6, 95000),
  ('MANU', 'UNIQUE', 2026, 7, 100000), ('MANU', 'UNIQUE', 2026, 8, 105000),
  ('MANU', 'UNIQUE', 2026, 9, 110000), ('MANU', 'UNIQUE', 2026, 10, 115000),
  ('MANU', 'UNIQUE', 2026, 11, 120000), ('MANU', 'UNIQUE', 2026, 12, 125000)
ON CONFLICT (empresa, year, month) DO NOTHING;

-- REPRESENTANTES
INSERT INTO goals (empresa, grupo, year, month, valor) VALUES
  ('REPRESENTANTES', 'UNIQUE', 2026, 1, 0), ('REPRESENTANTES', 'UNIQUE', 2026, 2, 15000),
  ('REPRESENTANTES', 'UNIQUE', 2026, 3, 20000), ('REPRESENTANTES', 'UNIQUE', 2026, 4, 25000),
  ('REPRESENTANTES', 'UNIQUE', 2026, 5, 30000), ('REPRESENTANTES', 'UNIQUE', 2026, 6, 35000),
  ('REPRESENTANTES', 'UNIQUE', 2026, 7, 40000), ('REPRESENTANTES', 'UNIQUE', 2026, 8, 45000),
  ('REPRESENTANTES', 'UNIQUE', 2026, 9, 50000), ('REPRESENTANTES', 'UNIQUE', 2026, 10, 55000),
  ('REPRESENTANTES', 'UNIQUE', 2026, 11, 60000), ('REPRESENTANTES', 'UNIQUE', 2026, 12, 65000)
ON CONFLICT (empresa, year, month) DO NOTHING;

-- SITE TERCEIROS
INSERT INTO goals (empresa, grupo, year, month, valor) VALUES
  ('SITE TERCEIROS', 'UNIQUE', 2026, 1, 0), ('SITE TERCEIROS', 'UNIQUE', 2026, 2, 0),
  ('SITE TERCEIROS', 'UNIQUE', 2026, 3, 15000), ('SITE TERCEIROS', 'UNIQUE', 2026, 4, 20000),
  ('SITE TERCEIROS', 'UNIQUE', 2026, 5, 25000), ('SITE TERCEIROS', 'UNIQUE', 2026, 6, 30000),
  ('SITE TERCEIROS', 'UNIQUE', 2026, 7, 35000), ('SITE TERCEIROS', 'UNIQUE', 2026, 8, 40000),
  ('SITE TERCEIROS', 'UNIQUE', 2026, 9, 45000), ('SITE TERCEIROS', 'UNIQUE', 2026, 10, 50000),
  ('SITE TERCEIROS', 'UNIQUE', 2026, 11, 55000), ('SITE TERCEIROS', 'UNIQUE', 2026, 12, 60000)
ON CONFLICT (empresa, year, month) DO NOTHING;

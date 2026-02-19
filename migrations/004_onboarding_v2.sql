-- Migration: onboarding_v2
--
-- Adds support for two-mode seller operation introduced in Onboarding V2:
--   - integration_mode: 'dashboard_only' (faturamento sync only) |
--                       'dashboard_ca'   (full CA reconciliation)
--   - ca_start_date: first day of the month from which CA backfill begins
--   - ca_backfill_*: progress tracking columns for the activation backfill
--
-- Also adds mp_expenses.source to distinguish rows ingested from the
-- Payments API (expense_classifier) vs. the account statement extrato
-- (extrato_ingester), and updates the expense_type comment to include
-- the 10 new extrato-derived types identified in Sessao 2 analysis.
--
-- Idempotent: all statements use IF NOT EXISTS / IF EXISTS guards so
-- this migration can be re-applied safely against a database that was
-- partially migrated.

begin;

-- ------------------------------------------------------------------ --
-- 1. New columns on sellers
-- ------------------------------------------------------------------ --

alter table public.sellers
    add column if not exists integration_mode text not null default 'dashboard_only';

alter table public.sellers
    add column if not exists ca_start_date date;

alter table public.sellers
    add column if not exists ca_backfill_status text;
    -- values: 'pending' | 'running' | 'completed' | 'failed' | null

alter table public.sellers
    add column if not exists ca_backfill_started_at timestamptz;

alter table public.sellers
    add column if not exists ca_backfill_completed_at timestamptz;

alter table public.sellers
    add column if not exists ca_backfill_progress jsonb;
    -- example: {"total": 520, "processed": 450, "orders_processed": 380,
    --           "expenses_classified": 60, "skipped": 10, "errors": 5,
    --           "baixas_created": 350, "last_payment_id": 144370799868}

-- ------------------------------------------------------------------ --
-- 2. CHECK constraint on integration_mode
--    Uses a DO block so the statement is safe to run more than once.
-- ------------------------------------------------------------------ --

do $$
begin
    if not exists (
        select 1
        from   pg_constraint
        where  conname      = 'chk_integration_mode'
          and  conrelid     = 'public.sellers'::regclass
    ) then
        alter table public.sellers
            add constraint chk_integration_mode
            check (integration_mode in ('dashboard_only', 'dashboard_ca'));
    end if;
end;
$$;

-- ------------------------------------------------------------------ --
-- 3. New column on mp_expenses
-- ------------------------------------------------------------------ --

alter table public.mp_expenses
    add column if not exists source text default 'payments_api';
    -- 'payments_api' : row originated from expense_classifier (Payments API)
    -- 'extrato'      : row originated from extrato_ingester (account_statement CSV)

-- ------------------------------------------------------------------ --
-- 4. Index for mp_expenses.source
-- ------------------------------------------------------------------ --

create index if not exists idx_mp_expenses_seller_source
    on public.mp_expenses (seller_slug, source);

-- ------------------------------------------------------------------ --
-- 5. Updated COMMENT on mp_expenses.expense_type
--    Documents all valid values including the 10 new extrato-derived
--    types from the Sessao 2 reconciliation analysis.
-- ------------------------------------------------------------------ --

comment on column public.mp_expenses.expense_type is
    'Classifier (Payments API) types: '
    'bill_payment | subscription | darf | cashback | collection | '
    'transfer_pix | transfer_intra | deposit | savings_pot | other. '
    'Extrato ingester (account_statement) types: '
    'difal | faturas_ml | reembolso_disputa | dinheiro_retido | '
    'entrada_dinheiro | debito_envio_ml | liberacao_cancelada | '
    'reembolso_generico | deposito_avulso | debito_divida_disputa.';

commit;

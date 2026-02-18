-- Support tables for:
-- 1) XLSX export/import tracking (manual lane)
-- 2) Daily sync cursor persistence

begin;

create table if not exists public.expense_batches (
    batch_id text primary key,
    seller_slug text not null,
    company text not null,
    status text not null default 'generated',
    rows_count integer not null default 0,
    amount_total_signed numeric(14,2) not null default 0,
    date_from date,
    date_to date,
    exported_at timestamptz,
    imported_at timestamptz,
    notes text,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now()
);

create index if not exists idx_expense_batches_seller_updated
    on public.expense_batches (seller_slug, updated_at desc);

create index if not exists idx_expense_batches_seller_status
    on public.expense_batches (seller_slug, status);

create table if not exists public.expense_batch_items (
    batch_id text not null,
    seller_slug text not null,
    expense_id bigint not null,
    payment_id bigint,
    expense_date date,
    expense_direction text,
    amount_signed numeric(14,2) not null default 0,
    status_snapshot text,
    created_at timestamptz not null default now(),
    primary key (batch_id, expense_id),
    constraint fk_expense_batch_items_batch
        foreign key (batch_id) references public.expense_batches (batch_id)
        on delete cascade
);

create index if not exists idx_expense_batch_items_seller_date
    on public.expense_batch_items (seller_slug, expense_date);

create index if not exists idx_expense_batch_items_batch_payment
    on public.expense_batch_items (batch_id, payment_id);

create table if not exists public.sync_state (
    sync_key text not null,
    seller_slug text not null,
    state jsonb not null default '{}'::jsonb,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now(),
    primary key (sync_key, seller_slug)
);

create index if not exists idx_sync_state_seller
    on public.sync_state (seller_slug, updated_at desc);

commit;

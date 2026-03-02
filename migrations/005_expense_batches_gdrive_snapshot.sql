-- Add GDrive backup tracking columns to expense_batches
-- and snapshot_payload to expense_batch_items for deterministic re-download.

begin;

-- GDrive backup status tracking on expense_batches
alter table public.expense_batches
    add column if not exists gdrive_status text,
    add column if not exists gdrive_folder_link text,
    add column if not exists gdrive_file_id text,
    add column if not exists gdrive_file_link text,
    add column if not exists gdrive_error text,
    add column if not exists gdrive_updated_at timestamptz;

-- Snapshot of expense fields at export time for deterministic re-download
alter table public.expense_batch_items
    add column if not exists snapshot_payload jsonb;

commit;

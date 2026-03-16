-- Migration 010: add extrato_missing columns to sellers
--
-- Tracks whether a seller was activated without providing
-- the mandatory extrato CSV (account statement from MP).
--
-- extrato_missing = TRUE means seller was activated with skip_extrato flag.
-- extrato_uploaded_at records when the extrato was successfully uploaded.

ALTER TABLE sellers ADD COLUMN IF NOT EXISTS extrato_missing BOOLEAN DEFAULT FALSE;
ALTER TABLE sellers ADD COLUMN IF NOT EXISTS extrato_uploaded_at TIMESTAMPTZ NULL;

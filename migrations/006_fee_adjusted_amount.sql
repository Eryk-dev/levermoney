-- migrations/006_fee_adjusted_amount.sql
ALTER TABLE payments ADD COLUMN IF NOT EXISTS fee_adjusted_amount numeric DEFAULT 0;

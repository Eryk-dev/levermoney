-- Migration 009: Deprecate mp_expenses table
--
-- Renames mp_expenses → mp_expenses_deprecated (preserves data as backup).
-- Creates a compatibility view mp_expenses that reads from payment_events,
-- mapping event ledger fields to the original column interface.
--
-- Status is derived from the presence of downstream events:
--   expense_exported   → 'exported'
--   expense_reviewed   → 'manually_categorized'
--   expense_classified → 'auto_categorized'
--   (none)             → 'pending_review'
--
-- After 1+ month without errors, run: DROP TABLE mp_expenses_deprecated;

-- 1. Rename the original table
ALTER TABLE mp_expenses RENAME TO mp_expenses_deprecated;

-- 2. Create compatibility view
CREATE VIEW mp_expenses AS
SELECT
    pe.id,
    pe.seller_slug,
    pe.reference_id AS payment_id,
    ABS(pe.signed_amount) AS amount,
    pe.metadata->>'expense_type' AS expense_type,
    pe.metadata->>'expense_direction' AS expense_direction,
    pe.metadata->>'ca_category' AS ca_category,
    (pe.metadata->>'auto_categorized')::boolean AS auto_categorized,
    pe.metadata->>'description' AS description,
    pe.metadata->>'business_branch' AS business_branch,
    pe.metadata->>'operation_type' AS operation_type,
    pe.metadata->>'payment_method' AS payment_method,
    pe.metadata->>'external_reference' AS external_reference,
    pe.metadata->>'beneficiary_name' AS beneficiary_name,
    pe.metadata->>'notes' AS notes,
    pe.metadata->>'febraban_code' AS febraban_code,
    pe.metadata->>'source' AS source,
    pe.competencia_date::text AS date_approved,
    pe.competencia_date::text AS date_created,
    pe.metadata->'raw_payment' AS raw_payment,
    -- Derive status from downstream events
    CASE
        WHEN EXISTS (
            SELECT 1 FROM payment_events pe2
            WHERE pe2.seller_slug = pe.seller_slug
              AND pe2.reference_id = pe.reference_id
              AND pe2.event_type = 'expense_exported'
        ) THEN 'exported'
        WHEN EXISTS (
            SELECT 1 FROM payment_events pe2
            WHERE pe2.seller_slug = pe.seller_slug
              AND pe2.reference_id = pe.reference_id
              AND pe2.event_type = 'expense_reviewed'
        ) THEN 'manually_categorized'
        WHEN EXISTS (
            SELECT 1 FROM payment_events pe2
            WHERE pe2.seller_slug = pe.seller_slug
              AND pe2.reference_id = pe.reference_id
              AND pe2.event_type = 'expense_classified'
        ) THEN 'auto_categorized'
        ELSE 'pending_review'
    END AS status,
    pe.created_at,
    pe.created_at AS updated_at
FROM payment_events pe
WHERE pe.event_type = 'expense_captured';

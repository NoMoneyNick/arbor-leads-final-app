-- migrations/0005_actual_cost_accounting.sql
--
-- Actual-cost postage accounting (2 Oct 2026). These are the exact statements
-- fulfilment.init_fulfilment_schema already runs on every startup, copied here
-- for operator review. You do NOT need to run this by hand: deploying the code
-- applies it. Every statement is ADD COLUMN IF NOT EXISTS -- additive, nullable,
-- safe against a live database with existing rows, safe to re-run. Nothing is
-- changed or dropped; no data migration; no funding-table changes.
--
--   estimated_cost_pence  what funding reserved before the letter was sent
--   provider_cost_pence   the provider's confirmed VAT-inclusive cost (pence)
--   cost_status           confirmed | shortfall | unresolved_missing |
--                         unresolved_invalid | unresolved_no_reservation |
--                         unresolved_accounting_error  (NULL = not yet accepted)
--   cost_shortfall_pence  part of the charge not covered by confirmed budget

ALTER TABLE letter_obligations ADD COLUMN IF NOT EXISTS estimated_cost_pence INT;
ALTER TABLE letter_obligations ADD COLUMN IF NOT EXISTS provider_cost_pence INT;
ALTER TABLE letter_obligations ADD COLUMN IF NOT EXISTS cost_status TEXT;
ALTER TABLE letter_obligations ADD COLUMN IF NOT EXISTS cost_shortfall_pence INT;

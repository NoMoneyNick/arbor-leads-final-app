-- migrations/0004_first_introduction_offer.sql
--
-- Schema for the GBP 4.99 first-introduction offer (2026-09-30). These are the
-- exact statements the app already runs on every startup (database.init_db's
-- ALTER list), copied here for operator review. You do NOT need to run this by
-- hand: deploying the code applies it. Every statement is ADD COLUMN IF NOT
-- EXISTS -- additive, nullable, safe against a live database with existing
-- rows, safe to re-run. Nothing is changed or dropped.
--
--   offer_kind          'first_introduction' on the ONE order row that is a
--                       first-introduction redemption; NULL on every ordinary
--                       order. The order row IS the redemption record: 'pending'
--                       holds the offer for ~40 minutes, 'paid' consumes it
--                       permanently, 'failed'/'refunded' release it.
--   offer_phone_key     normalised telephone identity of the business at the
--                       time of redemption (abuse control only, never shown).
--   offer_business_key  normalised business-name identity (abuse control only).

ALTER TABLE payments ADD COLUMN IF NOT EXISTS offer_kind TEXT;
ALTER TABLE payments ADD COLUMN IF NOT EXISTS offer_phone_key TEXT;
ALTER TABLE payments ADD COLUMN IF NOT EXISTS offer_business_key TEXT;

-- migrations/0003_signup_pending.sql
--
-- Schema for the integrated first-time signup (2026-09-30). This is the
-- exact set of statements the app already runs on every startup
-- (database.init_db's ALTER list and letter_content.init_letter_content_schema),
-- copied here for operator review. You do NOT need to run this by hand:
-- deploying the code applies it. Every statement is ADD COLUMN IF NOT EXISTS,
-- purely additive, nullable, safe against a live database with existing rows,
-- and safe to re-run. No existing row is changed, no table is dropped.
--
--   pending_signup            details a new visitor submitted, held on the ONE
--                             emailed verification record until it is verified
--                             (then applied and cleared) or expires (cleared).
--   responsible_contact_name  the account's responsible person (required at
--                             signup; NULL for accounts that pre-date it).
--   terms_accepted_at         when the Terms checkbox was ticked at signup
--                             (NULL for accounts that pre-date it; an ordinary
--                             settings save never writes it).

ALTER TABLE contractor_auth_tokens ADD COLUMN IF NOT EXISTS pending_signup TEXT;
ALTER TABLE contractor_letter_settings ADD COLUMN IF NOT EXISTS responsible_contact_name TEXT;
ALTER TABLE contractor_letter_settings ADD COLUMN IF NOT EXISTS terms_accepted_at TIMESTAMPTZ;

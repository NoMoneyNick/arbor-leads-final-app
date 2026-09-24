-- migrations/0001_letter_fulfilment.sql
--
-- Schema for TreeKey's bundled lead-and-letter fulfilment pipeline, built
-- 2026-09-18. This is the exact SQL that fulfilment.init_fulfilment_schema,
-- funding.init_funding_schema, suppression.init_suppression_schema, and
-- letter_content.init_letter_content_schema execute -- copied here verbatim
-- for operator review/manual application, NOT auto-generated separately
-- from them. If you edit table shapes, edit the Python functions first and
-- re-copy from there, so the .py and .sql never drift apart.
--
-- WHAT THIS DOES NOT TOUCH: no existing table (leads, letter_dispatches,
-- payments, contractor_subscriptions, etc.) is renamed or dropped, and no
-- existing column is altered or removed. Every statement below is CREATE
-- TABLE IF NOT EXISTS / CREATE INDEX IF NOT EXISTS / ALTER TABLE ... ADD
-- COLUMN IF NOT EXISTS -- purely additive, safe to run against a live
-- database with existing data, and safe to re-run (idempotent).
--
-- 2026-09-23 update, Request D Part 2: the ONE exception to "no existing
-- table is altered" above is a purely additive one -- retention_dispatch_
-- purge.init_dispatch_purge_schema adds a nullable purged_at column to the
-- pre-existing leads and letter_dispatches tables (alongside letter_
-- obligations above), purely for the new 72-hour post-dispatch
-- personal-data purge to track what it's already processed. See the
-- ALTER TABLE statements inline below, near each table they apply to.
--
-- HAS THIS BEEN RUN AGAINST PRODUCTION? No. Nothing in this session's work
-- has connected to a real database at all -- see docs/handoff.md. This
-- file exists so a human operator can review and run it deliberately
-- (`psql $SUPABASE_DB_URL -f migrations/0001_letter_fulfilment.sql`, or
-- equivalent), OR so the equivalent Python (database.init_db(), which
-- calls the four init_*_schema functions this file mirrors) can be
-- invoked directly -- either path produces the same schema.
--
-- ROLLBACK: since nothing here alters an existing table, rolling back is
-- just dropping the five new tables. Commented out by default -- this is
-- destructive (drops any letter_obligations/lead_allocations/etc. rows
-- created since this migration ran) and is NOT something to run
-- reflexively. Uncomment deliberately, after confirming nothing in
-- production depends on the data in these tables yet.
--
-- ROLLBACK SQL (commented out on purpose):
--   DROP TABLE IF EXISTS payment_allocation_reconciliation;
--   DROP TABLE IF EXISTS letter_obligations;
--   DROP TABLE IF EXISTS lead_allocations;
--   DROP TABLE IF EXISTS mailing_budget_confirmations;
--   DROP TABLE IF EXISTS postal_suppressions;
--   DROP TABLE IF EXISTS contractor_letter_settings;
--   DROP TABLE IF EXISTS address_disclosure_decisions;

BEGIN;

-- ---------------------------------------------------------------------
-- fulfilment.py: lead_allocations, letter_obligations,
-- payment_allocation_reconciliation
-- ---------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS lead_allocations (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    lead_reference TEXT NOT NULL,
    lead_id TEXT,
    buyer_email TEXT NOT NULL,
    allocation_type TEXT NOT NULL,   -- single_purchase | subscription_dispatch | free_lead | admin_grant
    source_payment_ref TEXT,         -- Stripe session/payment_intent id, subscription id, free-code, or admin note
    stripe_event_id TEXT,            -- the specific webhook event that caused this, when applicable (idempotency)
    idempotency_key TEXT NOT NULL UNIQUE,
    created_at TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_lead_allocations_buyer ON lead_allocations(buyer_email);
-- 2026-09-22 handoff: replaces the old plain idx_lead_allocations_reference
-- index -- a unique index serves every lookup the plain one did, plus
-- enforces atomic allocation at the database level. See fulfilment.py's
-- init_fulfilment_schema for the full reasoning.
CREATE UNIQUE INDEX IF NOT EXISTS idx_lead_allocations_reference_unique ON lead_allocations(lead_reference);

CREATE TABLE IF NOT EXISTS letter_obligations (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    allocation_id UUID REFERENCES lead_allocations(id),
    lead_reference TEXT NOT NULL,
    address TEXT NOT NULL,
    applicant_name TEXT,
    buyer_email TEXT NOT NULL,
    sale_context TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending_approval',
    is_dry_run BOOLEAN NOT NULL DEFAULT TRUE,
    content_fingerprint TEXT,
    approved_content_html TEXT,  -- 2026-09-18 review, Section 7: the exact rendered HTML approved
    -- at promotion time; sent verbatim, never re-rendered -- see fulfilment.py/worker.py.
    template_version INT,
    idempotency_key TEXT NOT NULL UNIQUE,
    provider_name TEXT,
    provider_reference TEXT,
    attempts INT NOT NULL DEFAULT 0,
    last_error TEXT,
    provider_accepted_at TIMESTAMPTZ,
    dispatched_at TIMESTAMPTZ,
    delivered_at TIMESTAMPTZ,
    failed_at TIMESTAMPTZ,
    suppressed_at TIMESTAMPTZ,
    suppressed_reason TEXT,
    claimed_by_worker TEXT,          -- set atomically when a worker begins submission, cleared on completion
    claimed_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_letter_obligations_status ON letter_obligations(status);
CREATE INDEX IF NOT EXISTS idx_letter_obligations_reference ON letter_obligations(lead_reference);

-- 2026-09-23, Request D Part 2 ("schedule removal of homeowner personal
-- data and personalised mailing content within 72 hours of
-- provider-confirmed dispatch"): retention_dispatch_purge.
-- init_dispatch_purge_schema -- purged_at tracks which rows have already
-- had their personal data cleared, so a repeated sweep is a no-op for
-- them. Idempotent ADD COLUMN IF NOT EXISTS, same pattern as every other
-- migration in this file.
ALTER TABLE letter_obligations ADD COLUMN IF NOT EXISTS purged_at TIMESTAMPTZ;
CREATE INDEX IF NOT EXISTS idx_letter_obligations_purge_eligible
    ON letter_obligations(dispatched_at) WHERE status = 'dispatched' AND purged_at IS NULL;

CREATE TABLE IF NOT EXISTS payment_allocation_reconciliation (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    stripe_event_id TEXT,
    stripe_reference TEXT,
    buyer_email TEXT,
    lead_reference TEXT,
    reason TEXT NOT NULL,
    resolved BOOLEAN NOT NULL DEFAULT FALSE,
    resolved_by TEXT,
    resolved_at TIMESTAMPTZ,
    resolution_note TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_reconciliation_unresolved ON payment_allocation_reconciliation(resolved) WHERE resolved = FALSE;

-- ---------------------------------------------------------------------
-- funding.py: mailing_budget_confirmations
-- ---------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS mailing_budget_confirmations (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    amount_pence INT NOT NULL,
    confirmed_by TEXT NOT NULL,
    note TEXT,
    spent_pence INT NOT NULL DEFAULT 0,
    reserved_pence INT NOT NULL DEFAULT 0,  -- 2026-09-18 review, Section 6
    active BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_budget_active ON mailing_budget_confirmations(active) WHERE active = TRUE;

CREATE TABLE IF NOT EXISTS funding_reservations (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    obligation_id TEXT NOT NULL,
    budget_confirmation_id UUID NOT NULL REFERENCES mailing_budget_confirmations(id),
    amount_pence INT NOT NULL,
    status TEXT NOT NULL DEFAULT 'reserved',
    created_at TIMESTAMPTZ DEFAULT NOW(),
    resolved_at TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS idx_funding_reservations_obligation ON funding_reservations(obligation_id);
CREATE INDEX IF NOT EXISTS idx_funding_reservations_reserved ON funding_reservations(status) WHERE status = 'reserved';

-- ---------------------------------------------------------------------
-- suppression.py: postal_suppressions
-- ---------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS postal_suppressions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    normalized_address TEXT,         -- 2026-09-22 handoff: no longer written by add_suppression
    -- (kept nullable, not dropped, only for rows written before this change -- see
    -- suppression.backfill_address_suppression_hashes). A new row leaves this NULL.
    address_hash TEXT,               -- HMAC-SHA256(key, normalized_address) -- see suppression._address_digest
    address_person_hash TEXT,        -- HMAC-SHA256(key, normalized_address+applicant_name) -- see suppression._address_person_digest
    applicant_name TEXT,             -- NULL = applies to the address regardless of named person
    reason TEXT NOT NULL,
    scope TEXT NOT NULL DEFAULT 'this_person',  -- 'this_person' | 'this_address_anyone'
    recorded_by TEXT NOT NULL,
    source_contact TEXT,             -- how the objection arrived (email address, phone note, etc.) -- never logged elsewhere
    created_at TIMESTAMPTZ DEFAULT NOW(),
    resolved BOOLEAN NOT NULL DEFAULT FALSE,
    resolved_by TEXT,
    resolved_at TIMESTAMPTZ,
    resolution_note TEXT
);
CREATE INDEX IF NOT EXISTS idx_postal_suppressions_address_hash ON postal_suppressions(address_hash) WHERE resolved = FALSE;
CREATE INDEX IF NOT EXISTS idx_postal_suppressions_address_person_hash ON postal_suppressions(address_person_hash) WHERE resolved = FALSE;
-- On a database where this table already exists under the old schema
-- (normalized_address TEXT NOT NULL, no hash columns), also run:
--   ALTER TABLE postal_suppressions ALTER COLUMN normalized_address DROP NOT NULL;
--   ALTER TABLE postal_suppressions ADD COLUMN IF NOT EXISTS address_hash TEXT;
--   ALTER TABLE postal_suppressions ADD COLUMN IF NOT EXISTS address_person_hash TEXT;
-- (all idempotent -- suppression.init_suppression_schema runs these automatically)
-- then run suppression.backfill_address_suppression_hashes once to migrate
-- existing rows off plaintext storage -- see that function's own docstring.

-- ---------------------------------------------------------------------
-- letter_content.py: contractor_letter_settings
-- ---------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS contractor_letter_settings (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    contractor_email TEXT NOT NULL UNIQUE,
    business_name TEXT NOT NULL,
    phone TEXT NOT NULL,
    service_area_note TEXT,
    insurance_note TEXT,             -- freeform, contractor-supplied; never invented by TreeKey
    qualifications_note TEXT,        -- freeform, contractor-supplied; never invented by TreeKey
    template_version INT NOT NULL DEFAULT 1,
    template_key TEXT NOT NULL DEFAULT 'friendly_introduction',  -- 2026-09-23 handoff: one of letter_content.TEMPLATE_REGISTRY's keys
    business_intro TEXT,             -- freeform, contractor-supplied
    services_note TEXT,              -- freeform, contractor-supplied ("relevant services")
    contact_email TEXT,
    approved BOOLEAN NOT NULL DEFAULT FALSE,
    approved_fingerprint TEXT,
    approved_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);
-- Existing deployments: run these idempotently against an already-created
-- table (same as letter_content.init_letter_content_schema does automatically):
--   ALTER TABLE contractor_letter_settings ADD COLUMN IF NOT EXISTS template_key TEXT NOT NULL DEFAULT 'friendly_introduction';
--   ALTER TABLE contractor_letter_settings ADD COLUMN IF NOT EXISTS business_intro TEXT;
--   ALTER TABLE contractor_letter_settings ADD COLUMN IF NOT EXISTS services_note TEXT;
--   ALTER TABLE contractor_letter_settings ADD COLUMN IF NOT EXISTS contact_email TEXT;

-- ---------------------------------------------------------------------
-- address_release.py: address_disclosure_decisions -- 2026-09-18 review,
-- Section 2 (second pass). One explicit, audited, human decision per
-- lead_reference recording whether a NEW allocation's address may be
-- disclosed -- checked ONLY for leads not covered by the historical
-- letter_dispatches fallback (see address_release.py's module docstring
-- for the full three-tier decision this feeds into). Nothing writes to
-- this table automatically; it is populated only via an operator
-- explicitly calling address_release.set_allocation_address_eligible.
-- ---------------------------------------------------------------------

-- ---------------------------------------------------------------------
-- retention_dispatch_purge.py: purged_at on the two PRE-EXISTING tables
-- this file's own CREATE TABLE statements don't otherwise touch (letter_
-- dispatches -- the legacy pipeline, defined in database.py's core
-- schema, not here -- and leads, the original scraped-lead table). See
-- this file's own "2026-09-23 update" note near the top. Idempotent, safe
-- against a live database with existing rows -- every existing row simply
-- gets purged_at/personal_data_purged_at = NULL, meaning "not yet
-- processed", which is the correct starting state.
-- ---------------------------------------------------------------------

ALTER TABLE letter_dispatches ADD COLUMN IF NOT EXISTS purged_at TIMESTAMPTZ;
CREATE INDEX IF NOT EXISTS idx_letter_dispatches_purge_eligible
    ON letter_dispatches(sent_at) WHERE purged_at IS NULL;

ALTER TABLE leads ADD COLUMN IF NOT EXISTS personal_data_purged_at TIMESTAMPTZ;

CREATE TABLE IF NOT EXISTS address_disclosure_decisions (
    lead_reference TEXT PRIMARY KEY,
    eligible BOOLEAN NOT NULL,
    decided_by TEXT NOT NULL,        -- who made this call -- required, for auditing
    note TEXT,
    decided_at TIMESTAMPTZ DEFAULT NOW()
);

COMMIT;

-- ---------------------------------------------------------------------
-- NOT included in this migration, deliberately:
--
-- 1. No backfill of historical letter_dispatches rows into
--    lead_allocations/letter_obligations. fulfilment.get_lead_owner()
--    falls back to letter_dispatches.buyer_email at query time instead --
--    see fulfilment.py's MIGRATION NOTES. A backfill is optional future
--    work, not required for correctness.
--
-- 2. No data migration of contractor company details (business name,
--    phone, insurance) into contractor_letter_settings. Every contractor
--    starts with NO row here and must explicitly save + approve their
--    letter settings (see letter_content.py) before any obligation of
--    theirs can be promoted past 'pending_approval' -- see worker.py's
--    promote_pending_approvals. This is intentional: it is the mechanism
--    that gets genuine per-contractor approval of what a live letter
--    says, rather than assuming the old hardcoded placeholder copy
--    ("Your Local Tree Specialists", "£5,000,000 Public Liability
--    Insurance") was ever actually correct for any given contractor.
-- ---------------------------------------------------------------------

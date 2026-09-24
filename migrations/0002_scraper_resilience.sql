-- migrations/0002_scraper_resilience.sql
--
-- Schema for the scraper incident/repair-attempt tracking and scan-
-- checkpoint/backfill bookkeeping added 2026-09-22, in response to an
-- external architecture review (ChatGPT/Astra, relayed by Nick the same
-- day) that Claude's own review of the daily warning digest had prompted.
-- This is the exact SQL that scraper_resilience.init_scraper_resilience_
-- schema executes -- copied here verbatim for operator review/manual
-- application, NOT auto-generated separately from it. If you edit table
-- shapes, edit scraper_resilience.py first and re-copy from there, so the
-- .py and .sql never drift apart (same discipline as 0001_letter_fulfilment.sql).
--
-- 2026-09-22 UPDATE (same day, second pass -- Astra's follow-up review):
-- added last_verification_outcome/last_verification_failure_reason/
-- reopen_count to source_incident, switched council_scan_checkpoint's key
-- from (council) alone to (council, search_definition), and added
-- backward-movement protection to the checkpoint upsert (see
-- scraper_resilience.py::advance_council_scan_checkpoint's own docstring).
-- See the "UPGRADING FROM THE FIRST VERSION" note near the bottom if this
-- file was already run somewhere before this update.
--
-- WHAT THIS DOES NOT TOUCH: no existing table (leads, system_warnings,
-- etc.) is altered, renamed, or dropped. Every CREATE statement below is
-- CREATE TABLE IF NOT EXISTS / CREATE INDEX IF NOT EXISTS -- purely
-- additive, safe to run against a live database with existing data, and
-- safe to re-run (idempotent).
--
-- WHAT THIS DOES NOT DO: create any wiring into the actual scraper.
-- mesh_scrapers.py (the module that would call these functions) is not
-- present in this repo -- see ERROR_LOG.md. This is schema and bookkeeping
-- functions only, ready for that future wiring, not yet connected to any
-- real scrape.
--
-- HAS THIS BEEN RUN AGAINST PRODUCTION? No. Nothing in this session's work
-- has connected to a real database at all. This file exists so a human
-- operator can review and run it deliberately
-- (`psql $SUPABASE_DB_URL -f migrations/0002_scraper_resilience.sql`, or
-- equivalent), OR so the equivalent Python (database.init_db(), which now
-- also calls scraper_resilience.init_scraper_resilience_schema) can be
-- invoked directly -- either path produces the same schema.
--
-- ROLLBACK SQL (commented out on purpose -- read the same caution
-- 0001_letter_fulfilment.sql's own header gives before ever uncommenting):
--   DROP TABLE IF EXISTS repair_attempt;
--   DROP TABLE IF EXISTS source_incident;
--   DROP TABLE IF EXISTS council_scan_checkpoint;
--   DROP TABLE IF EXISTS council_scan_pass_metrics;

BEGIN;

CREATE TABLE IF NOT EXISTS source_incident (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    council TEXT NOT NULL,
    platform TEXT NOT NULL,
    failure_type TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'open',
    note TEXT,
    first_seen TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    last_seen TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    last_verified_success TIMESTAMPTZ,
    last_verification_outcome TEXT,
    last_verification_failure_reason TEXT,
    reopen_count INT NOT NULL DEFAULT 0,
    resolved_by TEXT,
    resolved_at TIMESTAMPTZ,
    resolution_note TEXT
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_source_incident_open_unique
    ON source_incident(council, failure_type)
    WHERE status != 'resolved';
CREATE INDEX IF NOT EXISTS idx_source_incident_status ON source_incident(status);

CREATE TABLE IF NOT EXISTS repair_attempt (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    incident_id UUID NOT NULL REFERENCES source_incident(id),
    parser_version TEXT,
    action TEXT NOT NULL,
    outcome TEXT NOT NULL DEFAULT 'pending',
    evidence_reference TEXT,
    started_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    completed_at TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS idx_repair_attempt_incident ON repair_attempt(incident_id);

CREATE TABLE IF NOT EXISTS council_scan_checkpoint (
    council TEXT NOT NULL,
    search_definition TEXT NOT NULL DEFAULT 'default',
    platform TEXT NOT NULL,
    last_completed_window_start TIMESTAMPTZ,
    last_completed_window_end TIMESTAMPTZ,
    last_completed_at TIMESTAMPTZ,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (council, search_definition)
);

CREATE TABLE IF NOT EXISTS council_scan_pass_metrics (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    council TEXT NOT NULL,
    platform TEXT NOT NULL,
    pages_fetched INT NOT NULL DEFAULT 0,
    links_discovered INT NOT NULL DEFAULT 0,
    records_parsed INT NOT NULL DEFAULT 0,
    records_rejected_validation INT NOT NULL DEFAULT 0,
    records_excluded_business_filters INT NOT NULL DEFAULT 0,
    new_sellable_leads INT NOT NULL DEFAULT 0,
    recorded_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_scan_pass_metrics_council ON council_scan_pass_metrics(council, recorded_at DESC);

COMMIT;

-- ---------------------------------------------------------------------------
-- UPGRADING FROM THE FIRST VERSION OF THIS MIGRATION (only relevant if this
-- file was already run, e.g. against a dev/staging database, before this
-- 2026-09-22 second-pass update -- NOT relevant on a fresh run, since the
-- "CREATE TABLE IF NOT EXISTS" tables created above already come out in
-- the current shape when starting from nothing):
--
-- The three new source_incident columns are safe to add with IF NOT EXISTS,
-- uncomment and run:
--   ALTER TABLE source_incident ADD COLUMN IF NOT EXISTS last_verification_outcome TEXT;
--   ALTER TABLE source_incident ADD COLUMN IF NOT EXISTS last_verification_failure_reason TEXT;
--   ALTER TABLE source_incident ADD COLUMN IF NOT EXISTS reopen_count INT NOT NULL DEFAULT 0;
--
-- The council_scan_checkpoint primary-key change (council alone -> council,
-- search_definition) is deliberately NOT scripted here as an automatic
-- ALTER: dropping and re-adding a primary key constraint needs that
-- constraint's actual name, which depends on how Postgres auto-named it
-- when the table was first created (typically council_scan_checkpoint_pkey,
-- but don't assume that without checking
-- `\d council_scan_checkpoint` first). If you're in this situation:
--   ALTER TABLE council_scan_checkpoint ADD COLUMN IF NOT EXISTS search_definition TEXT NOT NULL DEFAULT 'default';
--   ALTER TABLE council_scan_checkpoint DROP CONSTRAINT <the actual pkey constraint name>;
--   ALTER TABLE council_scan_checkpoint ADD PRIMARY KEY (council, search_definition);
-- ---------------------------------------------------------------------------

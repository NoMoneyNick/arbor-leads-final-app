-- 0007_internal_digest_queue.sql (14 Oct 2026). Mirrors internal_digest.DDL. DEPLOYMENT PREREQUISITE: run this ONCE in the Supabase SQL
-- editor (as the project owner, "postgres") BEFORE running DEPLOY_DIGEST.bat. The app skips all table DDL when the table already exists,
-- so it then needs no CREATE rights. Safe to re-run. Starts EMPTY: nothing already sent is replayed.
-- Only if the app connects as a role OTHER than "postgres" (unverified), also run, with that role's name:
--   GRANT SELECT, INSERT, UPDATE, DELETE ON internal_digest_queue TO <app_role>;
--   GRANT USAGE, SELECT ON SEQUENCE internal_digest_queue_id_seq TO <app_role>;
CREATE TABLE IF NOT EXISTS internal_digest_queue (
    id BIGSERIAL PRIMARY KEY,
    topic TEXT NOT NULL,
    kind TEXT NOT NULL,               -- lead | issue | note | resolved
    item_key TEXT NOT NULL,
    severity TEXT,
    category TEXT,
    title TEXT,
    source TEXT,
    detail TEXT,
    payload JSONB,
    link TEXT,
    occurred_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    claim_token TEXT,
    claimed_at TIMESTAMPTZ,
    sent_at TIMESTAMPTZ
);
CREATE UNIQUE INDEX IF NOT EXISTS uq_idq_once ON internal_digest_queue(topic, item_key) WHERE kind IN ('lead','note');
CREATE INDEX IF NOT EXISTS idx_idq_pending ON internal_digest_queue(topic) WHERE sent_at IS NULL;

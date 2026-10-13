-- 0006_login_link_limits.sql (13 Oct 2026). Mirrors the statements added to database.init_db.
-- requester_ip_hash is a salted hash, never the raw visitor address.
ALTER TABLE contractor_auth_tokens ADD COLUMN IF NOT EXISTS requester_ip_hash TEXT;
CREATE INDEX IF NOT EXISTS idx_auth_email_created ON contractor_auth_tokens(customer_email, created_at);
CREATE INDEX IF NOT EXISTS idx_auth_ip_created ON contractor_auth_tokens(requester_ip_hash, created_at);
CREATE INDEX IF NOT EXISTS idx_auth_created ON contractor_auth_tokens(created_at);

import os
import re
import requests
import psycopg2
import logging
from typing import Optional, Dict, Any, Tuple, List
from dotenv import load_dotenv


load_dotenv()
SURL = os.getenv("SUPABASE_DB_URL", "").strip()
logger = logging.getLogger("vector-data-labs")


def get_db_conn():
    """Opens a connection to the Supabase database with automated incident tripwire."""
    try:
        return psycopg2.connect(SURL, connect_timeout=10)

    except Exception as e:
        logger.error(f"[DB] Connection failed: {e}")
        try:
            import notifications
            notifications.send_system_incident_alert(
                category="DATABASE INFRASTRUCTURE",
                title="SUPABASE POSTGRESQL CONNECTION FAILED",
                description=f"CRITICAL: Application failed to connect to Supabase PostgreSQL database at SUPABASE_DB_URL. Error: {str(e)[:150]}",
                impact="All lead insertions, partner discovery lookups, and customer payment webhook events are currently blocked.",
                action_required="1. Check Supabase project status at supabase.com. 2. Verify SUPABASE_DB_URL connection string and password in Render Environment Settings. 3. Check if database project has been paused or reached storage limits.",
                metric_details={"Host": "Supabase PostgreSQL", "Error": str(e)[:80]},
                severity="CRITICAL",
                throttle_hours=2.0
            )
        except Exception:
            pass
        raise e



def _run_ddl_statements_resiliently(conn, statements: list, phase_label: str,
                                     lock_timeout_ms: int = 4000, max_attempts: int = 5) -> list:
    """Sep 2 2026 (production incident fix): runs each DDL statement in its
    OWN transaction with a short SET LOCAL lock_timeout, retrying with
    backoff on lock/statement-timeout errors instead of giving up instantly.

    Why this exists: every statement in resilience_cols used to share ONE
    transaction with a single commit at the very end of Phase 1. On Sep 2
    2026 the newly-added `vertical` column's ALTER TABLE had to wait for an
    ACCESS EXCLUSIVE lock on the `leads` table -- which the scan jobs write
    to constantly -- and Postgres's own statement_timeout eventually killed
    it ("canceling statement due to statement timeout") after roughly two
    minutes. That rolled back the ENTIRE Phase 1 transaction, undoing every
    other column/index that had already succeeded in this same call, and
    left `vertical` missing in production. Every lead insert then failed
    with "column vertical does not exist" (scanners._insert_lead lists it
    unconditionally) and the marketplace query went the same way (its
    COALESCE(vertical, ...) SELECT), so lead capture AND the public
    marketplace both silently went to zero the moment this deployed --
    caught only because Nick noticed the live site hadn't picked up an
    unrelated privacy-policy change and asked about it.

    Fix, two parts: (1) each statement gets its own transaction, so one
    statement that's still stuck can never undo a sibling that already
    landed; (2) a short SET LOCAL lock_timeout (auto-reverts at
    commit/rollback, so it can't leak into later phases) makes a blocked
    ALTER fail in ~4s instead of queuing for the full statement_timeout --
    which also matters because while an ACCESS EXCLUSIVE-lock DDL statement
    waits, it blocks every OTHER new query against that table that arrives
    behind it, so failing fast limits the blast radius too. Failures are
    retried with backoff (a few seconds' contention from a scan job's own
    transaction is normal and often clears on the next attempt) and, if a
    statement still hasn't landed after all attempts, it's logged
    individually and returned to the caller instead of being buried inside
    one opaque "Phase 1 failed" catch-all.
    """
    import time
    import re as _re
    failed = []
    for stmt in statements:
        # Sep 2 2026 audit: a real production incident (Sep 2, this same
        # day) showed every single "ADD COLUMN IF NOT EXISTS" statement
        # against potential_partners fail all 5 retries at once, because a
        # 90+ minute-long autonomous enrichment run on the OLD instance was
        # still hammering that exact table with writes while this deploy's
        # migration tried to start up alongside it (Render keeps the old
        # instance live until the new one passes its health check). Every
        # one of those 7 columns already existed from a much earlier
        # deploy -- the ALTER was a no-op that couldn't even acquire the
        # ACCESS EXCLUSIVE lock needed to confirm that, not a real missing-
        # column problem -- but there's no way to tell the two cases apart
        # from the alert alone, and it fired a CRITICAL incident email for
        # what turned out to be nothing. Fix: for the extremely common
        # "ADD COLUMN IF NOT EXISTS" shape, check information_schema first
        # -- a plain SELECT needs no exclusive lock at all -- and skip the
        # ALTER entirely when the column is already there. This removes the
        # lock-contention risk for the 99% case (nothing new to add) and
        # only ever attempts the real, lock-needing ALTER for a column
        # that's genuinely missing for the first time, which is rare enough
        # that the existing retry/backoff below is still the right
        # fallback for it.
        m = _re.match(
            r"ALTER TABLE (\w+) ADD COLUMN IF NOT EXISTS (\w+)",
            stmt.strip(), _re.IGNORECASE
        )
        if m:
            table_name, column_name = m.group(1), m.group(2)
            try:
                precheck_cur = conn.cursor()
                precheck_cur.execute(
                    "SELECT 1 FROM information_schema.columns WHERE table_name = %s AND column_name = %s;",
                    (table_name, column_name)
                )
                already_exists = precheck_cur.fetchone() is not None
                precheck_cur.close()
                conn.commit()
                if already_exists:
                    continue
            except Exception as precheck_err:
                conn.rollback()
                logger.warning(
                    f"[DB:{phase_label}] information_schema precheck failed for "
                    f"{table_name}.{column_name}, falling back to direct ALTER: {precheck_err}"
                )

        # Sep 2 2026: same precheck idea, applied to "ENABLE ROW LEVEL
        # SECURITY" statements after this exact shape caused a live Phase 2
        # failure -- one table's statement hit the 4s lock_timeout and,
        # because Phase 2 used to run all 13 ALTERs in one shared
        # transaction, took the other 12 (which may have already landed
        # fine, or would have landed fine on retry) down with it. Routing
        # Phase 2 through this same resilient runner fixes the all-or-
        # nothing rollback; this precheck additionally skips the lock-
        # needing ALTER entirely for a table where RLS is already on, same
        # rationale as the ADD COLUMN case above -- a plain SELECT against
        # pg_tables needs no exclusive lock.
        m_rls = _re.match(
            r"ALTER TABLE (\w+) ENABLE ROW LEVEL SECURITY",
            stmt.strip(), _re.IGNORECASE
        )
        if m_rls:
            table_name = m_rls.group(1)
            try:
                precheck_cur = conn.cursor()
                precheck_cur.execute(
                    "SELECT rowsecurity FROM pg_tables WHERE schemaname = 'public' AND tablename = %s;",
                    (table_name,)
                )
                row = precheck_cur.fetchone()
                precheck_cur.close()
                conn.commit()
                if row is not None and row[0]:
                    continue
            except Exception as precheck_err:
                conn.rollback()
                logger.warning(
                    f"[DB:{phase_label}] pg_tables RLS precheck failed for "
                    f"{table_name}, falling back to direct ALTER: {precheck_err}"
                )
        last_err = None
        landed = False
        for attempt in range(1, max_attempts + 1):
            cur = conn.cursor()
            try:
                cur.execute(f"SET LOCAL lock_timeout = '{lock_timeout_ms}ms';")
                cur.execute(stmt)
                conn.commit()
                landed = True
                break
            except Exception as e:
                conn.rollback()
                last_err = e
                if attempt < max_attempts:
                    wait_s = min(2 ** attempt, 15)
                    logger.warning(
                        f"[DB:{phase_label}] Attempt {attempt}/{max_attempts} failed, "
                        f"retrying in {wait_s}s: {stmt.strip()[:90]}... -- {e}"
                    )
                    time.sleep(wait_s)
            finally:
                cur.close()
        if landed:
            continue
        logger.error(
            f"[DB:{phase_label}] Gave up after {max_attempts} attempts -- statement did NOT land: "
            f"{stmt.strip()[:150]} -- {last_err}"
        )
        failed.append((stmt, str(last_err)))
    return failed


def init_db():
    """Ensures all required tables and columns exist. Safe to run on every startup.

    Aug 31 2026: this used to be ONE giant transaction with a single commit
    at the very end -- discovered live when agent_is_tree_surgeon was added
    to resilience_cols below, the app deployed fine, but every single PlanIt
    insert then failed all afternoon with 'column agent_is_tree_surgeon does
    not exist'. Root cause: SOME statement further down in the old single
    block (never confirmed which -- the exception was only logged, never
    surfaced) raised, which rolled back the ENTIRE transaction including the
    schema change, silently -- the app kept running as if startup had
    succeeded. Splitting into independent phases, each with its own
    commit/rollback, means a schema change always lands even if an unrelated
    later phase (RLS, hygiene cleanup) fails, and each phase's own failure is
    now individually visible in the logs instead of one opaque catch-all.
    """
    if not SURL:
        logger.warning("[DB] No SUPABASE_DB_URL found. Running in blind mode.")
        return

    conn = get_db_conn()

    # Phase 1: schema (tables, indices, columns) -- the part that MUST land
    # for the rest of the app to work at all, so it's isolated and committed
    # first, before anything riskier (RLS, data cleanup) gets a chance to
    # take it down with it.
    try:
        cur = conn.cursor()
        cur.execute("""
            CREATE TABLE IF NOT EXISTS potential_partners (
                id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                company_name TEXT,
                company_number TEXT UNIQUE,
                status TEXT,
                address TEXT,
                distance_miles NUMERIC,
                target_city TEXT,
                sic_codes TEXT[],
                md_name TEXT,
                phone_number TEXT,
                google_rating NUMERIC,
                created_at TIMESTAMPTZ DEFAULT NOW()
            );

            CREATE TABLE IF NOT EXISTS leads (
                id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                reference TEXT UNIQUE,
                address TEXT,
                summary TEXT,
                score INT DEFAULT 50,
                council_source TEXT,
                lead_score TEXT DEFAULT 'small',
                lead_price NUMERIC DEFAULT 25,
                status TEXT DEFAULT 'new',
                discovered_at TIMESTAMPTZ DEFAULT NOW()
            );

            CREATE TABLE IF NOT EXISTS payments (
                id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                stripe_session_id TEXT UNIQUE,
                plan TEXT,
                amount_pence INT,
                customer_email TEXT,
                status TEXT DEFAULT 'pending',
                created_at TIMESTAMPTZ DEFAULT NOW()
            );

            CREATE TABLE IF NOT EXISTS api_usage (
                id SERIAL PRIMARY KEY,
                api_name TEXT NOT NULL,
                period_month TEXT NOT NULL,
                call_count INT DEFAULT 0,
                warning_sent BOOLEAN DEFAULT FALSE,
                updated_at TIMESTAMPTZ DEFAULT NOW(),
                UNIQUE (api_name, period_month)
            );

            CREATE TABLE IF NOT EXISTS territory_claims (
                id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                outcode TEXT UNIQUE NOT NULL,
                customer_email TEXT NOT NULL,
                customer_name TEXT,
                stripe_subscription_id TEXT,
                active BOOLEAN DEFAULT TRUE,
                claimed_at TIMESTAMPTZ DEFAULT NOW(),
                expires_at TIMESTAMPTZ
            );

            CREATE TABLE IF NOT EXISTS contractor_suggestions (
                id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                contractor_name TEXT,
                phone_or_email TEXT,
                suggestion TEXT NOT NULL,
                created_at TIMESTAMPTZ DEFAULT NOW()
            );

            CREATE TABLE IF NOT EXISTS contractor_subscriptions (
                id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                customer_email TEXT UNIQUE NOT NULL,
                customer_name TEXT,
                phone TEXT,
                tier TEXT DEFAULT 'climber_domestic',
                center_outcode TEXT NOT NULL,
                radius_miles INT DEFAULT 15,
                stripe_subscription_id TEXT,
                monthly_quota INT DEFAULT 20,
                delivered_this_month INT DEFAULT 0,
                active BOOLEAN DEFAULT TRUE,
                subscribed_at TIMESTAMPTZ DEFAULT NOW(),
                last_dispatched_at TIMESTAMPTZ
            );

            CREATE TABLE IF NOT EXISTS lead_dispatches (
                id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                lead_id UUID NOT NULL,
                contractor_id UUID REFERENCES contractor_subscriptions(id) ON DELETE CASCADE,
                contractor_email TEXT NOT NULL,
                dispatch_type TEXT DEFAULT 'standard',
                dispatched_at TIMESTAMPTZ DEFAULT NOW()
            );

            CREATE TABLE IF NOT EXISTS contractor_ledger_entries (
                id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                contractor_id UUID REFERENCES contractor_subscriptions(id) ON DELETE CASCADE,
                contractor_email TEXT,
                job_name TEXT NOT NULL,
                client_type TEXT DEFAULT 'domestic',
                gross_amount NUMERIC NOT NULL,
                labor_amount NUMERIC DEFAULT 0,
                materials_plant_amount NUMERIC DEFAULT 0,
                cis_rate_pct NUMERIC DEFAULT 0,
                cis_tax_deducted NUMERIC DEFAULT 0,
                tipping_costs NUMERIC DEFAULT 0,
                fuel_consumables NUMERIC DEFAULT 0,
                net_profit NUMERIC,
                profit_margin_pct NUMERIC,
                job_date DATE DEFAULT CURRENT_DATE,
                created_at TIMESTAMPTZ DEFAULT NOW()
            );

            CREATE TABLE IF NOT EXISTS machinery_assets (
                id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                contractor_id UUID REFERENCES contractor_subscriptions(id) ON DELETE CASCADE,
                contractor_email TEXT,
                asset_name TEXT NOT NULL,
                purchase_price NUMERIC NOT NULL,
                purchase_date DATE DEFAULT CURRENT_DATE,
                aia_tax_shield_val NUMERIC,
                created_at TIMESTAMPTZ DEFAULT NOW()
            );

            CREATE TABLE IF NOT EXISTS contractor_auth_tokens (
                id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                customer_email TEXT NOT NULL,
                token TEXT UNIQUE NOT NULL,
                otp_code TEXT NOT NULL,
                created_at TIMESTAMPTZ DEFAULT NOW(),
                expires_at TIMESTAMPTZ DEFAULT (NOW() + INTERVAL '15 minutes'),
                used BOOLEAN DEFAULT FALSE
            );

            -- Sep 5 2026, Nick's "limbo account" ask: a free account (no
            -- Stripe subscription) created via /free-account -- gets ONE
            -- real free lead on signup (see burn_lead_inventory), then
            -- 1-2x/week teaser emails with the address blurred out, as an
            -- upgrade prompt. Deliberately a SEPARATE table from
            -- contractor_subscriptions rather than a fake "active=False"
            -- row there -- that table's `active` flag already means
            -- something specific elsewhere (a real subscription that
            -- lapsed/was cancelled), and conflating "never paid" with
            -- "used to pay" would corrupt every quota/seniority/cancellation
            -- check built around it.
            CREATE TABLE IF NOT EXISTS limbo_accounts (
                id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                email TEXT UNIQUE NOT NULL,
                customer_name TEXT,
                phone TEXT,
                center_outcode TEXT NOT NULL,
                lat FLOAT,
                lon FLOAT,
                free_lead_ref TEXT,
                last_teaser_lead_ref TEXT,
                signed_up_at TIMESTAMPTZ DEFAULT NOW(),
                last_teaser_sent_at TIMESTAMPTZ,
                unsubscribed BOOLEAN DEFAULT FALSE
            );

            CREATE TABLE IF NOT EXISTS chip_drop_spots (
                id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                site_name TEXT NOT NULL,
                contact_name TEXT,
                phone TEXT NOT NULL,
                outcode TEXT NOT NULL,
                town TEXT NOT NULL,
                address TEXT NOT NULL,
                material_accepted TEXT DEFAULT 'fresh_woodchip',
                max_vehicle_size TEXT DEFAULT '3.5t_transit',
                access_instructions TEXT,
                active BOOLEAN DEFAULT TRUE,
                created_at TIMESTAMPTZ DEFAULT NOW()
            );

            CREATE TABLE IF NOT EXISTS storm_weather_alerts (
                id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                region_name TEXT NOT NULL,
                outcode_prefixes TEXT[] NOT NULL,
                wind_gust_mph INT NOT NULL,
                warning_level TEXT DEFAULT 'amber',
                summary TEXT NOT NULL,
                valid_from TIMESTAMPTZ DEFAULT NOW(),
                valid_to TIMESTAMPTZ DEFAULT (NOW() + INTERVAL '48 hours'),
                dispatched BOOLEAN DEFAULT FALSE,
                created_at TIMESTAMPTZ DEFAULT NOW()
            );

            -- Sep 2 2026, master_expansion_plan_v2.md build-order step 4
            -- (the tiered classifier), Tier 4: "manual review queue for
            -- anything that fails all three [tiers] -- visible, never
            -- silently dropped." Before this table existed, every scan call
            -- site's `if vertical is None: continue` meant an application
            -- matching no vertical's keywords or structured fields was
            -- discarded completely and permanently -- not wrong for the
            -- ~majority of genuinely irrelevant applications (rear
            -- extensions, adverts, telecoms), but it also meant a real
            -- tree/HMO application phrased in a way none of the first two
            -- tiers anticipated vanished with zero trace, not even a log
            -- line, and could never be found again once the source API's
            -- own lookback window passed. This table is the landing zone
            -- Tier 3 (a cheap LLM classification pass, run separately/
            -- rate-limited against this queue, not inline in the scan loop)
            -- reads from and writes back to -- see scanners.process_
            -- review_queue_with_llm's docstring for why Tier 3 runs here
            -- rather than per-item during the live scan.
            CREATE TABLE IF NOT EXISTS unclassified_applications (
                id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                reference TEXT UNIQUE,
                address TEXT,
                description TEXT,
                source TEXT,
                app_type TEXT,
                status TEXT DEFAULT 'pending_review',
                resolved_vertical TEXT,
                llm_attempts INT DEFAULT 0,
                discovered_at TIMESTAMPTZ DEFAULT NOW(),
                reviewed_at TIMESTAMPTZ
            );

            -- Sep 2 2026: tiny durable key/value store for the autonomous
            -- scheduler (see main.py's _autonomous_scheduler_loop) to
            -- record when the daily cycle last actually ran. Needed
            -- because Render restarts this process on every redeploy --
            -- an in-memory "have I run today" flag would forget on every
            -- restart and could re-fire the full pipeline (and hammer
            -- every council portal again) minutes after Nick's last
            -- redeploy. A persisted timestamp survives restarts.
            CREATE TABLE IF NOT EXISTS system_state (
                key TEXT PRIMARY KEY,
                value TEXT,
                updated_at TIMESTAMPTZ DEFAULT NOW()
            );

            -- Sep 3 2026: backs the /partner-offer QR-code landing page
            -- (main.py). `src` is a short campaign code baked into each
            -- printed QR image (business card, trade-show stand, a future
            -- letter run -- see MARKETING_OUTREACH_IDEAS.md) so response
            -- can be measured per batch/channel without building a full
            -- per-recipient tracking system before any real campaign has
            -- actually launched.
            CREATE TABLE IF NOT EXISTS qr_campaign_leads (
                id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                src TEXT,
                name TEXT,
                phone TEXT,
                email TEXT,
                town TEXT,
                created_at TIMESTAMPTZ DEFAULT NOW()
            );

            -- Sep 4 2026: backs the WARNING-severity half of
            -- notifications.send_system_incident_alert() (see that
            -- function's own Sep 4 comment for the full reasoning). Nick's
            -- own words: "if the warning emails are mostly ignored i will
            -- never know when to pay attention" -- CRITICAL alerts still
            -- email immediately as before, but a WARNING now gets logged
            -- here instead of emailed, and only escalates to a real email
            -- once the SAME category+title has recurred on 3+ distinct
            -- calendar days within a rolling week (get_warning_recurrence_
            -- days below) -- a genuinely sustained problem, not a one-off
            -- blip or a burst of stale alerts re-firing after a redeploy
            -- resets the in-memory per-key throttle (exactly what happened
            -- Sep 3/4: a wave of TLS-fallback/page-structure warnings across
            -- ~15 unrelated councils fired in one batch purely because the
            -- throttle cache was wiped, live-verified as false alarms on
            -- every council spot-checked).
            CREATE TABLE IF NOT EXISTS system_warnings (
                id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                category TEXT NOT NULL,
                title TEXT NOT NULL,
                description TEXT,
                occurred_at TIMESTAMPTZ DEFAULT NOW()
            );

            -- Sep 8 2026: small persistent cache so the live-feed ticker and
            -- area-scoped radar results can show a real place name (e.g.
            -- "CR5, Croydon, London") without calling postcodes.io's free
            -- outcodes API on every single pageview -- once per outcode ever
            -- seen, reused after that.
            CREATE TABLE IF NOT EXISTS outcode_area_cache (
                outcode TEXT PRIMARY KEY,
                district TEXT,
                is_london BOOLEAN DEFAULT FALSE,
                cached_at TIMESTAMPTZ DEFAULT NOW()
            );

            -- Sep 8 2026, Nick's ask: the public "Today" counter shouldn't
            -- jump by (say) 100 the instant a big morning scan lands -- it
            -- should look like those same real leads are arriving steadily
            -- across the working day. This is the persisted, server-side
            -- pacing state (one row per day) so every visitor sees the same
            -- number and a page refresh can't jump the queue -- see
            -- get_public_lead_counts below. Never stores a count higher
            -- than what's actually real; only paces WHEN the true number is
            -- revealed.
            CREATE TABLE IF NOT EXISTS public_counter_reveal (
                reveal_date DATE PRIMARY KEY,
                revealed_today INT DEFAULT 0,
                last_reveal_at TIMESTAMPTZ DEFAULT NOW(),
                next_reveal_at TIMESTAMPTZ DEFAULT NOW()
            );
        """)

        # Performance Indices for Instant High-Volume Queries
        indices = [
            "CREATE INDEX IF NOT EXISTS idx_leads_discovered_at ON leads(discovered_at DESC);",
            "CREATE INDEX IF NOT EXISTS idx_leads_council ON leads(council_source);",
            "CREATE INDEX IF NOT EXISTS idx_leads_reference ON leads(reference);",
            "CREATE INDEX IF NOT EXISTS idx_subs_active ON contractor_subscriptions(active, center_outcode, subscribed_at ASC);",
            "CREATE INDEX IF NOT EXISTS idx_dispatches_lead ON lead_dispatches(lead_id);",
            "CREATE INDEX IF NOT EXISTS idx_dispatches_contractor ON lead_dispatches(contractor_id);",
            "CREATE INDEX IF NOT EXISTS idx_ledger_contractor ON contractor_ledger_entries(contractor_email, job_date DESC);",
            "CREATE INDEX IF NOT EXISTS idx_assets_contractor ON machinery_assets(contractor_email);",
            "CREATE INDEX IF NOT EXISTS idx_auth_token ON contractor_auth_tokens(token);",
            "CREATE INDEX IF NOT EXISTS idx_auth_otp ON contractor_auth_tokens(otp_code, customer_email);",
            "CREATE INDEX IF NOT EXISTS idx_limbo_accounts_email ON limbo_accounts(email);",
            "CREATE INDEX IF NOT EXISTS idx_limbo_accounts_teaser_due ON limbo_accounts(unsubscribed, last_teaser_sent_at);",
            "CREATE INDEX IF NOT EXISTS idx_chip_drop_outcode ON chip_drop_spots(outcode, active);",
            "CREATE INDEX IF NOT EXISTS idx_storm_alerts_active ON storm_weather_alerts(valid_to, warning_level);",
            "CREATE INDEX IF NOT EXISTS idx_partners_city ON potential_partners(target_city);",
            "CREATE INDEX IF NOT EXISTS idx_partners_created ON potential_partners(created_at DESC);",
            "CREATE INDEX IF NOT EXISTS idx_partners_company_number ON potential_partners(company_number);",
            "CREATE INDEX IF NOT EXISTS idx_leads_source_type ON leads(lead_source_type);",
            "CREATE INDEX IF NOT EXISTS idx_territory_outcode ON territory_claims(outcode);",
            "CREATE INDEX IF NOT EXISTS idx_unclassified_status ON unclassified_applications(status, discovered_at ASC);",
            "CREATE INDEX IF NOT EXISTS idx_system_warnings_lookup ON system_warnings(category, title, occurred_at DESC);",
        ]
        for idx in indices:
            cur.execute(idx)

        conn.commit()
        cur.close()

        # Resilience: add any missing columns safely. Sep 2 2026: pulled out of the
        # CREATE TABLE/index transaction above and run through
        # _run_ddl_statements_resiliently -- see that function's docstring for the
        # exact production incident (a single contended ALTER TABLE silently rolling
        # back this entire phase, taking lead capture and the marketplace to zero)
        # that made a shared all-or-nothing transaction here unsafe.
        resilience_cols = [
            "ALTER TABLE potential_partners ADD COLUMN IF NOT EXISTS phone_number TEXT;",
            "ALTER TABLE potential_partners ADD COLUMN IF NOT EXISTS md_name TEXT;",
            "ALTER TABLE potential_partners ADD COLUMN IF NOT EXISTS google_rating NUMERIC;",
            "ALTER TABLE potential_partners ADD COLUMN IF NOT EXISTS website TEXT;",
            "ALTER TABLE potential_partners ADD COLUMN IF NOT EXISTS email TEXT;",
            "ALTER TABLE potential_partners ADD COLUMN IF NOT EXISTS enriched_at TIMESTAMPTZ;",
            # Sep 2 2026: sole traders never appear in Companies House at all,
            # so they'll always have company_number = NULL here (harmless --
            # NULL never conflicts with itself under a UNIQUE constraint).
            # place_id (the Google Places result ID) is their dedup key
            # instead, so research.discover_sole_traders_via_google_places
            # can ON CONFLICT (place_id) DO UPDATE instead of re-inserting
            # the same sole trader as a fresh duplicate every autonomous cycle.
            "ALTER TABLE potential_partners ADD COLUMN IF NOT EXISTS place_id TEXT UNIQUE;",
            "ALTER TABLE leads ADD COLUMN IF NOT EXISTS lead_score TEXT DEFAULT 'small';",
            "ALTER TABLE leads ADD COLUMN IF NOT EXISTS lead_price NUMERIC DEFAULT 25;",
            "ALTER TABLE leads ADD COLUMN IF NOT EXISTS lead_source_type TEXT DEFAULT 'council_planning';",
            "ALTER TABLE leads ADD COLUMN IF NOT EXISTS homeowner_contact TEXT;",
            "ALTER TABLE leads ADD COLUMN IF NOT EXISTS registered_date DATE;",
            "ALTER TABLE leads ADD COLUMN IF NOT EXISTS statutory_deadline DATE;",
            "ALTER TABLE leads ADD COLUMN IF NOT EXISTS planning_status TEXT DEFAULT 'pending';",
            "ALTER TABLE leads ADD COLUMN IF NOT EXISTS lifecycle_stage TEXT DEFAULT 'stage_1_application';",
            # Aug 30 2026: added so we can tell a genuinely open lead (no agent
            # listed on the planning application) from one where a contractor
            # has already been hired to file it (Agent Name/Agent Company Name
            # present). applicant_name is the real named person/business who
            # filed it -- publicly published on the council's own application
            # page, unlike a phone number or email which councils never publish.
            "ALTER TABLE leads ADD COLUMN IF NOT EXISTS applicant_name TEXT;",
            "ALTER TABLE leads ADD COLUMN IF NOT EXISTS agent_name TEXT;",
            "ALTER TABLE leads ADD COLUMN IF NOT EXISTS agent_company TEXT;",
            "ALTER TABLE leads ADD COLUMN IF NOT EXISTS has_agent BOOLEAN;",
            # Aug 31 2026: has_agent alone conflated two very different
            # situations -- a tree surgeon already hired to file the
            # application (job genuinely taken) vs. an architect, planning
            # consultant, block management company, or the council itself
            # acting as "agent" purely to handle the paperwork (the tree work
            # itself may still be wide open). mesh_scrapers.classify_agent_as_tree_surgeon()
            # gives a best-effort True/False/unknown read on which case this
            # is, stored separately so has_agent keeps meaning exactly what
            # it always meant ("an agent is on record") without losing that
            # distinction.
            "ALTER TABLE leads ADD COLUMN IF NOT EXISTS agent_is_tree_surgeon BOOLEAN;",
            # Sep 2 2026: multi-vertical build, step 1 (master_expansion_plan_v2.md).
            # Which VERTICALS config key (scanners.py) this lead was classified into
            # -- 'tree' or 'hmo' today, more later. Defaults to 'tree' so every
            # existing row and every scan call site written before this column
            # existed is completely unaffected -- ON CONFLICT (reference) still
            # only supports one row per application reference, not yet one row per
            # matched vertical (see scanners._resolve_vertical's docstring for the
            # interim tree-priority-wins rule this implies).
            "ALTER TABLE leads ADD COLUMN IF NOT EXISTS vertical TEXT DEFAULT 'tree';",
            # Sep 2 2026: lead tagging system (Nick's "total control of our
            # data" request) -- a Postgres native array column so one lead
            # can carry many independent, overlapping tags (locale, region,
            # job size, job type, agent status) at once. See
            # scanners._generate_tags for what gets written in here and
            # get_leads_by_tags below for how it's queried. Each existing
            # row defaults to an empty array (not NULL, so array operators
            # like @> and && behave predictably without extra NULL checks
            # at every call site) until backfill_lead_tags() populates it.
            "ALTER TABLE leads ADD COLUMN IF NOT EXISTS tags TEXT[] DEFAULT '{}';",
            # GIN index is what makes @>/&& array queries fast at scale --
            # without it, get_leads_by_tags would do a full table scan on
            # every filter. IF NOT EXISTS makes this idempotent same as
            # every other statement in this list; CREATE INDEX (not
            # CONCURRENTLY) is fine here since it runs through the same
            # resilient-per-statement/retry path as every other DDL
            # statement in this list, not inside one shared transaction.
            "CREATE INDEX IF NOT EXISTS idx_leads_tags ON leads USING GIN (tags);",
            # Sep 2 2026: same tagging pattern, applied to partners. Nick's
            # call: a partner we have no way to actually contact is "dead to
            # us" exactly like an unclassified lead, and that must be a
            # queryable fact, not something only visible by eyeballing NULL
            # columns. See research._generate_partner_tags for what's
            # written and database.get_partner_tag_counts for the report.
            "ALTER TABLE potential_partners ADD COLUMN IF NOT EXISTS tags TEXT[] DEFAULT '{}';",
            "CREATE INDEX IF NOT EXISTS idx_partners_tags ON potential_partners USING GIN (tags);",
            # Add lat/lon columns for geographic radius matching (safe, idempotent)
            "ALTER TABLE contractor_subscriptions ADD COLUMN IF NOT EXISTS lat FLOAT;",
            "ALTER TABLE contractor_subscriptions ADD COLUMN IF NOT EXISTS lon FLOAT;",
            # Contractor Portal Upgrades (Phase 2, part 1 — PROJECT_STATE.md item 8):
            # preferred lead-notification format. 'email' = plain email (current default
            # behaviour), 'whatsapp'/'both' = the batch lead email also includes a
            # click-to-forward WhatsApp button per lead. Note: this is a forward-to-self
            # convenience link (create_whatsapp_link), not push delivery via WhatsApp's
            # Business API — no such integration exists in this codebase.
            "ALTER TABLE contractor_subscriptions ADD COLUMN IF NOT EXISTS notification_preference TEXT DEFAULT 'email';",
            # Sep 8 2026, Nick's proximity-system rework: a subscriber previously
            # only ever gave a bare outcode (e.g. "CR5"), geocoded to that
            # outcode's centroid -- fine for matching, but not precise enough to
            # honestly say "this job is 4.2 miles from you". full_postcode
            # stores the exact postcode when the customer gave one (e.g. "CR5
            # 2LE"); lat/lon above then get the exact postcodes.io pin instead
            # of just the outcode centroid when it's present. center_outcode is
            # still always kept as the bare outcode (derived even from a full
            # postcode) since territory_claims and the existing prefix-matching
            # logic in dispatch_lead_alerts are keyed on outcode, not full
            # postcode -- this only adds precision, it doesn't change what
            # "your territory" means. job_size_preference lets a contractor
            # only be matched to jobs at the scale they actually want.
            "ALTER TABLE contractor_subscriptions ADD COLUMN IF NOT EXISTS full_postcode TEXT;",
            "ALTER TABLE contractor_subscriptions ADD COLUMN IF NOT EXISTS job_size_preference TEXT DEFAULT 'all';",
            # Sep 8 2026: the radar's "leads in radius" figure was matching
            # only the exact outcode typed against address text, completely
            # ignoring the radius slider and the map circle -- see
            # classify_leads_by_radius below for the real fix. Piggybacks on
            # the outcode_area_cache lookup that already runs for every
            # ticker/notices row (get_outcode_area_label), so this costs no
            # extra postcodes.io calls beyond what was already happening.
            "ALTER TABLE outcode_area_cache ADD COLUMN IF NOT EXISTS lat FLOAT;",
            "ALTER TABLE outcode_area_cache ADD COLUMN IF NOT EXISTS lon FLOAT;",
        ]
        failed_ddl = _run_ddl_statements_resiliently(conn, resilience_cols, phase_label="Phase1-columns")

        if failed_ddl:
            logger.error(f"[DB] Phase 1 columns: {len(failed_ddl)}/{len(resilience_cols)} statement(s) never landed after retries.")
            try:
                import notifications
                notifications.send_system_incident_alert(
                    category="DATABASE SCHEMA MIGRATION",
                    title="init_db() Phase 1 column migration failed after retries",
                    description=f"{len(failed_ddl)} ALTER TABLE statement(s) did not land after retrying with backoff: " +
                                 "; ".join(f"{s.strip()[:80]} -> {err[:100]}" for s, err in failed_ddl[:5]),
                    impact="Any code path that reads/writes a column added here will fail (or fall back to legacy behavior, where a fallback exists) until this is fixed.",
                    action_required="Check Supabase for long-running/blocking transactions on the affected table(s), then let init_db() retry on the next restart, or run the ALTER manually with a short lock_timeout.",
                    severity="CRITICAL",
                    throttle_hours=1.0
                )
            except Exception:
                pass
        else:
            logger.info("[DB] Phase 1 OK: tables, indices, and columns are up to date.")
    except Exception as e:
        conn.rollback()
        logger.error(f"[DB] Phase 1 (tables/indices) FAILED -- app is likely broken until this is fixed: {e}")
        try:
            import notifications
            notifications.send_system_incident_alert(
                category="DATABASE SCHEMA MIGRATION",
                title="init_db() schema phase failed",
                description=f"A CREATE TABLE / index statement failed: {str(e)[:300]}",
                impact="Any code path that reads/writes a table/index added here will fail until this is fixed.",
                action_required="Check the exact error above and fix the migration SQL, then redeploy.",
                severity="CRITICAL",
                throttle_hours=1.0
            )
        except Exception:
            pass

    # Phase 2: Row-Level Security. Independent of Phase 1/3 -- a failure
    # here should not be able to block schema changes or hygiene cleanup.
    #
    # Sep 2 2026 fix: this used to run all 13 ALTERs in one shared
    # transaction/commit, so a single statement hitting a lock timeout
    # ("canceling statement due to statement timeout", seen live in
    # production) rolled back all 13 -- including any that had already
    # landed or would have landed fine on their own. Now routed through the
    # same per-statement-transaction + precheck + retry runner already
    # proven for Phase 1, so one stuck table can no longer take the other
    # twelve down with it, and tables that already have RLS on are skipped
    # without ever taking a lock.
    try:
        rls_statements = [
            "ALTER TABLE potential_partners ENABLE ROW LEVEL SECURITY;",
            "ALTER TABLE leads ENABLE ROW LEVEL SECURITY;",
            "ALTER TABLE payments ENABLE ROW LEVEL SECURITY;",
            "ALTER TABLE api_usage ENABLE ROW LEVEL SECURITY;",
            "ALTER TABLE territory_claims ENABLE ROW LEVEL SECURITY;",
            "ALTER TABLE contractor_subscriptions ENABLE ROW LEVEL SECURITY;",
            "ALTER TABLE lead_dispatches ENABLE ROW LEVEL SECURITY;",
            "ALTER TABLE contractor_suggestions ENABLE ROW LEVEL SECURITY;",
            "ALTER TABLE contractor_ledger_entries ENABLE ROW LEVEL SECURITY;",
            "ALTER TABLE machinery_assets ENABLE ROW LEVEL SECURITY;",
            "ALTER TABLE contractor_auth_tokens ENABLE ROW LEVEL SECURITY;",
            "ALTER TABLE chip_drop_spots ENABLE ROW LEVEL SECURITY;",
            "ALTER TABLE storm_weather_alerts ENABLE ROW LEVEL SECURITY;"
        ]
        failed_rls = _run_ddl_statements_resiliently(conn, rls_statements, phase_label="Phase 2 (RLS)")
        if failed_rls:
            logger.error(f"[DB] Phase 2 (RLS): {len(failed_rls)}/{len(rls_statements)} statement(s) never landed after retries.")
            try:
                import notifications
                notifications.send_system_incident_alert(
                    category="DATABASE SECURITY",
                    title="init_db() Phase 2 (RLS) failed to enable on one or more tables",
                    description=f"RLS ALTER statement(s) did not land after retries: {[s[:80] for s, _ in failed_rls]}",
                    impact="The affected table(s) may be running without Row-Level Security until this is fixed.",
                    action_required="Check for long-running writers against the affected table(s) and retry, or run manually during a quiet period.",
                    severity="CRITICAL",
                    throttle_hours=1.0
                )
            except Exception:
                pass
        else:
            logger.info("[DB] Phase 2 OK: RLS lockout applied (or already active) on all tables.")
    except Exception as e:
        logger.error(f"[DB] Phase 2 (RLS) FAILED -- schema changes from Phase 1 are unaffected: {e}")

    # Phase 3: hygiene cleanup (DELETE/UPDATE junk rows). The riskiest phase
    # (regex-based, touches existing data) -- isolated last so a bad regex or
    # data-shape surprise here can NEVER take the schema or RLS phases down
    # with it, unlike before.
    try:
        cur = conn.cursor()
        # DATA QUALITY HYGIENE: Purge any old blank or uninformative placeholder leads
        cur.execute("""
            DELETE FROM leads
            WHERE summary IS NULL
               OR LOWER(TRIM(summary)) IN ('tree-preservation-order', 'tpo', 'work to trees', 'works to trees', 'trees', '')
               OR (LENGTH(TRIM(summary)) < 15 AND (address = 'Greater London' OR address = 'London' OR address IS NULL));
        """)

        # PARTNER QUALITY HYGIENE: Purge non-tree businesses (nurseries, cafes, law, RTMs, etc.)
        cur.execute("""
            DELETE FROM potential_partners
            WHERE LOWER(company_name) ~* '(nursery|nurseries|preschool|pre-school|childcare|daycare|kindergarten|restaurant|cafe|café|bakery|food|bar|pub|coffee|chippy|catering|hotel|inn|inns|lodges|wealth|mortgage|finance|financial|solicitor|law|legal|accountant|accountancy|tax|management company|management co|court management|close management|mews management|gardens management|rtm company|freehold|residents|virtual|software|pictures|films|film|music|literary|yoga|padel|tennis|gym|cleaning|plumbing|electrical|roofing|flooring|tiles|bathrooms|machinery|equipment ltd)';
        """)

        # Clean corrupted JS package emails and placeholder addresses
        cur.execute("""
            UPDATE potential_partners
            SET email = NULL
            WHERE email ~* '(@[0-9]+\\.|intl-segmenter|slick-carousel|tailwindcss|leaflet|bootstrap|aos@|yourname|example|mysite|webador)';
        """)

        # Clean spam websites
        cur.execute("""
            UPDATE potential_partners
            SET website = NULL
            WHERE website ~* '(10summersheatingandcoolingllc|airflexheatingandcoolinginc|alabamaurbanforestryservice|companiesmadesimple)';
        """)

        conn.commit()
        cur.close()
        logger.info("[DB] Phase 3 OK: lead/partner hygiene cleanup applied.")
    except Exception as e:
        conn.rollback()
        logger.error(f"[DB] Phase 3 (hygiene cleanup) FAILED -- schema and RLS from Phases 1-2 are unaffected: {e}")

    conn.close()


# ── Manual review queue (Tier 4) -- master_expansion_plan_v2.md build-order
# step 4. See unclassified_applications' own CREATE TABLE comment in init_db
# for why this table exists at all: an application matching neither Tier 1
# (keyword) nor Tier 2 (structured field) used to be silently discarded by
# every scan call site with no trace left anywhere.

def insert_unclassified_application(reference: str, address: str, description: str,
                                     source: str, app_type: str = None) -> bool:
    """Queues an application that cleared neither Tier 1 nor Tier 2 for
    later review (Tier 3's LLM pass, or Nick looking at it directly).
    ON CONFLICT (reference) DO NOTHING -- the same still-open application
    reappears in a source's "recent" search every day it stays live, and
    this must not re-queue (or reset an already-reviewed) row every single
    day. Returns False (not an error) for a duplicate -- callers shouldn't
    treat "already queued" as a failure."""
    if not SURL or not reference or not description:
        return False
    try:
        conn = get_db_conn()
        cur = conn.cursor()
        try:
            cur.execute("""
                INSERT INTO unclassified_applications (reference, address, description, source, app_type)
                VALUES (%s, %s, %s, %s, %s)
                ON CONFLICT (reference) DO NOTHING
                RETURNING id;
            """, (reference, address, description[:500] if description else description, source, app_type))
            row = cur.fetchone()
            conn.commit()
            return bool(row)
        finally:
            cur.close()
            conn.close()
    except Exception as e:
        logger.error(f"[ReviewQueue] Error queuing application {reference}: {e}")
        return False


def get_pending_review_queue(limit: int = 50, max_llm_attempts: int = None) -> list:
    """Returns the oldest `limit` still-pending review-queue rows, oldest
    first -- so a backlog gets worked down in the order applications first
    appeared, not newest-first (which could let old ones age past their
    source's own lookback window before ever being looked at).

    max_llm_attempts (Tier 3's own use, not the default): excludes rows
    whose llm_attempts already meets/exceeds this cap. Genuinely ambiguous
    text can come back uncertain from Gemini every time it's asked -- without
    a cap, Tier 3 would keep re-spending a real API call on the exact same
    stuck row forever. Left None (the default) for anything reading the
    queue for a human to look at -- Nick must still be able to see every
    still-open row regardless of how many LLM attempts it's had; 'never
    silently dropped' means visible to a person too, not just to Tier 3."""
    if not SURL:
        return []
    try:
        conn = get_db_conn()
        cur = conn.cursor()
        try:
            sql = """
                SELECT id, reference, address, description, source, app_type, llm_attempts, discovered_at
                FROM unclassified_applications
                WHERE status = 'pending_review'
            """
            params = []
            if max_llm_attempts is not None:
                sql += " AND llm_attempts < %s"
                params.append(max_llm_attempts)
            sql += " ORDER BY discovered_at ASC LIMIT %s;"
            params.append(limit)
            cur.execute(sql, tuple(params))
            cols = ["id", "reference", "address", "description", "source", "app_type", "llm_attempts", "discovered_at"]
            return [dict(zip(cols, r)) for r in cur.fetchall()]
        finally:
            cur.close()
            conn.close()
    except Exception as e:
        logger.error(f"[ReviewQueue] Error fetching pending queue: {e}")
        return []


def get_lead_source_health_report(recent_days: int = 3, baseline_days: int = 30, min_baseline_daily_rate: float = 0.1) -> list:
    """Sep 3 2026: the failsafe Nick asked for after the Leeds ArcGIS
    incident -- 'predict possible future issues... even if they have never
    shown an error.' Leeds' bug never raised a single exception in months;
    it just silently produced zero real leads while quietly stuffing junk
    into the manual-review queue instead. No error-rate alert would ever
    have caught that, because there was no error. The only signal that
    incident actually left behind was a source that used to produce leads
    and then, quietly, stopped -- so that's what this checks for directly,
    per council_source, instead of only watching for exceptions.

    For each council_source that has ever produced a lead: compares its
    historical daily rate (leads/day over the `baseline_days` window,
    EXCLUDING the most recent `recent_days` so a real ongoing problem
    doesn't get averaged away) against how many it actually produced in
    that most recent window. Flags a source as `possible_silent_failure`
    only when BOTH (a) its historical rate clears `min_baseline_daily_rate`
    -- so a genuinely low-volume source (a small council that might
    legitimately go a week between applications) isn't flagged just for
    having a quiet few days -- AND (b) it produced literally zero in the
    recent window. Read-only, never raises: returns [] on any DB error so
    a broken health check can never itself become an outage."""
    if not SURL:
        return []
    try:
        conn = get_db_conn()
        cur = conn.cursor()
        try:
            cur.execute(
                """
                SELECT
                    council_source,
                    COUNT(*) FILTER (
                        WHERE discovered_at >= NOW() - (%s || ' days')::interval
                          AND discovered_at <  NOW() - (%s || ' days')::interval
                    ) AS baseline_leads,
                    COUNT(*) FILTER (
                        WHERE discovered_at >= NOW() - (%s || ' days')::interval
                    ) AS recent_leads
                FROM leads
                GROUP BY council_source;
                """,
                (baseline_days, recent_days, recent_days)
            )
            rows = cur.fetchall()
        finally:
            cur.close()
            conn.close()

        baseline_window_days = max(1, baseline_days - recent_days)
        report = []
        for council_source, baseline_leads, recent_leads in rows:
            baseline_daily_rate = (baseline_leads or 0) / baseline_window_days
            flagged = baseline_daily_rate >= min_baseline_daily_rate and (recent_leads or 0) == 0
            report.append({
                "council_source": council_source,
                "baseline_daily_rate": round(baseline_daily_rate, 3),
                "recent_leads": recent_leads or 0,
                "recent_days": recent_days,
                "possible_silent_failure": flagged,
            })
        return report
    except Exception as e:
        logger.error(f"[LeadSourceHealth] Error building health report: {e}")
        return []


def log_system_warning(category: str, title: str, description: str = "") -> bool:
    """Sep 4 2026: persists a WARNING-severity incident (see
    notifications.send_system_incident_alert's own comment for the full
    reasoning) instead of emailing it immediately. Best-effort -- returns
    False and logs on any DB error rather than raising, since a warning
    that fails to log should never itself become the incident."""
    if not SURL:
        return False
    try:
        conn = get_db_conn()
        cur = conn.cursor()
        try:
            cur.execute(
                "INSERT INTO system_warnings (category, title, description) VALUES (%s, %s, %s);",
                (category, title, description),
            )
            conn.commit()
            return True
        finally:
            cur.close()
            conn.close()
    except Exception as e:
        logger.error(f"[SystemWarnings] Error logging warning {category}:{title}: {e}")
        return False


def get_warning_recurrence_days(category: str, title: str, window_days: int = 7) -> int:
    """Counts the number of DISTINCT calendar days this exact category+
    title has been logged within the trailing `window_days` -- the signal
    notifications.send_system_incident_alert uses to tell a genuinely
    sustained, worsening problem (the same thing, day after day) apart from
    a one-off blip or a burst of unrelated alerts all re-firing at once
    (e.g. every in-memory alert throttle resetting on the same redeploy --
    live-confirmed Sep 3/4 2026 to look identical to a real incident from
    the alert titles alone, but NOT be one). Returns 0 on any DB error so a
    health-check failure can never itself block or wrongly escalate an
    alert."""
    if not SURL:
        return 0
    try:
        conn = get_db_conn()
        cur = conn.cursor()
        try:
            cur.execute(
                """
                SELECT COUNT(DISTINCT occurred_at::date)
                FROM system_warnings
                WHERE category = %s AND title = %s
                  AND occurred_at >= NOW() - (%s || ' days')::interval;
                """,
                (category, title, window_days),
            )
            row = cur.fetchone()
            return row[0] if row else 0
        finally:
            cur.close()
            conn.close()
    except Exception as e:
        logger.error(f"[SystemWarnings] Error counting recurrence for {category}:{title}: {e}")
        return 0


def get_all_recurring_warnings(window_days: int = 7, min_days: int = 3, active_within_hours: int = 24) -> list:
    """Sep 8 2026: Nick's ask -- too many separate WARNING emails (a dozen+
    council portals all independently crossing the 3-of-7-day recurrence
    threshold on the same morning, each firing its own email under the old
    per-title escalation in send_system_incident_alert). This is the one
    query behind the new daily digest (notifications.send_daily_warning_
    digest): every (category, title) pair that is CURRENTLY a real,
    sustained problem -- recurred on min_days+ distinct days in the
    trailing window AND has fired at least once in the last
    active_within_hours (so a warning that recurred 3+ days last week but
    hasn't happened since doesn't keep appearing forever). Returns [] on
    any DB error, same failsafe pattern as this file's other health checks
    -- a broken health check must never itself become an incident."""
    if not SURL:
        return []
    try:
        conn = get_db_conn()
        cur = conn.cursor()
        try:
            cur.execute(
                """
                SELECT category, title, MAX(description) AS description,
                       COUNT(DISTINCT occurred_at::date) AS days_seen,
                       MAX(occurred_at) AS last_seen
                FROM system_warnings
                WHERE occurred_at >= NOW() - (%s || ' days')::interval
                GROUP BY category, title
                HAVING COUNT(DISTINCT occurred_at::date) >= %s
                   AND MAX(occurred_at) >= NOW() - (%s || ' hours')::interval
                ORDER BY category, days_seen DESC;
                """,
                (window_days, min_days, active_within_hours),
            )
            cols = ["category", "title", "description", "days_seen", "last_seen"]
            return [dict(zip(cols, r)) for r in cur.fetchall()]
        finally:
            cur.close()
            conn.close()
    except Exception as e:
        logger.error(f"[SystemWarnings] Error fetching recurring warnings: {e}")
        return []


def get_recent_warnings(hours: int = 24) -> list:
    """Read-only lookup for a human to check on their own schedule (e.g. a
    /recent-warnings dashboard route) -- everything logged by
    log_system_warning in the last `hours`, most recent first. Returns []
    on any DB error, same failsafe pattern as get_lead_source_health_report
    above."""
    if not SURL:
        return []
    try:
        conn = get_db_conn()
        cur = conn.cursor()
        try:
            cur.execute(
                """
                SELECT category, title, description, occurred_at
                FROM system_warnings
                WHERE occurred_at >= NOW() - (%s || ' hours')::interval
                ORDER BY occurred_at DESC
                LIMIT 500;
                """,
                (hours,),
            )
            cols = ["category", "title", "description", "occurred_at"]
            return [dict(zip(cols, r)) for r in cur.fetchall()]
        finally:
            cur.close()
            conn.close()
    except Exception as e:
        logger.error(f"[SystemWarnings] Error fetching recent warnings: {e}")
        return []


def increment_review_queue_llm_attempts(reference: str) -> bool:
    """Records that Tier 3 looked at this row and could NOT confidently
    classify it -- the row stays 'pending_review' (still visible to a human,
    still in the queue) but counts toward max_llm_attempts so a future Tier 3
    run doesn't keep re-spending a call on the same stuck item forever."""
    if not SURL or not reference:
        return False
    try:
        conn = get_db_conn()
        cur = conn.cursor()
        try:
            cur.execute("""
                UPDATE unclassified_applications SET llm_attempts = llm_attempts + 1
                WHERE reference = %s RETURNING id;
            """, (reference,))
            row = cur.fetchone()
            conn.commit()
            return bool(row)
        finally:
            cur.close()
            conn.close()
    except Exception as e:
        logger.error(f"[ReviewQueue] Error incrementing llm_attempts for {reference}: {e}")
        return False


def resolve_unclassified_application(reference: str, resolved_vertical: str) -> bool:
    """Marks a review-queue row as confidently classified by Tier 3 -- the
    ONLY way a row leaves 'pending_review' status (an uncertain Tier 3 pass
    uses increment_review_queue_llm_attempts instead and leaves it queued).
    Caller is responsible for actually inserting the real lead via
    scanners._insert_lead first; this just closes out the queue row."""
    if not SURL or not reference or not resolved_vertical:
        return False
    try:
        conn = get_db_conn()
        cur = conn.cursor()
        try:
            cur.execute("""
                UPDATE unclassified_applications
                SET status = 'llm_classified', resolved_vertical = %s, reviewed_at = NOW()
                WHERE reference = %s
                RETURNING id;
            """, (resolved_vertical, reference))
            row = cur.fetchone()
            conn.commit()
            return bool(row)
        finally:
            cur.close()
            conn.close()
    except Exception as e:
        logger.error(f"[ReviewQueue] Error resolving application {reference}: {e}")
        return False


def get_leads_by_tags(tags: list, match_all: bool = True, limit: int = 200) -> list:
    """Sep 2 2026: the query side of the lead tagging system (see
    scanners._generate_tags for how tags are built at insert time). Pass a
    list of tag strings like ["locale:bromley", "job:crown-work", "size:large"]
    and get back every lead carrying all of them (match_all=True, Postgres
    '@>' contains-all) or any of them (match_all=False, '&&' overlap). Both
    operators are what the GIN index on leads.tags actually accelerates --
    without it, this degrades to a full table scan at real data volume, not
    a slow query at small scale only.

    Deliberately does NOT accept a date range here -- that's a direct query
    against discovered_at/registered_date/statutory_deadline instead (see
    that column's own comment where it's added: a baked-in "recent" tag
    would go stale the moment it's no longer true, which nothing would ever
    notice or fix). Callers wanting both a tag filter and a date range
    should combine this function's tag list with their own WHERE clause, or
    ask for that to be built as a real combined query once there's an actual
    caller (admin dashboard, marketplace filter) that needs it -- not
    speculatively here.
    """
    if not SURL or not tags:
        return []
    try:
        conn = get_db_conn()
        cur = conn.cursor()
        try:
            op = "@>" if match_all else "&&"
            cur.execute(f"""
                SELECT id, reference, address, summary, lead_score, lead_price,
                       council_source, vertical, has_agent, tags, discovered_at, status
                FROM leads
                WHERE tags {op} %s
                ORDER BY discovered_at DESC
                LIMIT %s;
            """, (tags, limit))
            cols = ["id", "reference", "address", "summary", "lead_score", "lead_price",
                    "council_source", "vertical", "has_agent", "tags", "discovered_at", "status"]
            rows = cur.fetchall()
            return [dict(zip(cols, r)) for r in rows]
        finally:
            cur.close()
            conn.close()
    except Exception as e:
        logger.error(f"[LeadTags] Error querying by tags {tags}: {e}")
        return []


def backfill_lead_tags(batch_size: int = 500) -> dict:
    """Sep 2 2026: recomputes scanners._generate_tags for every lead whose
    `tags` column is still empty -- covers every row inserted before this
    column existed, and is safe to re-run any time the job-type/region
    keyword lists improve later (only ever touches rows still sitting at
    '{}', same 'only touch what's actually unresolved' pattern as
    trigger_backfill_tree_surgeon). Imports scanners lazily to avoid a
    circular import (scanners.py imports database.py at module level)."""
    if not SURL:
        return {"error": "no database configured"}
    updated = 0
    errors = 0
    try:
        conn = get_db_conn()
        cur = conn.cursor()
        try:
            cur.execute("""
                SELECT id, address, summary, council_source, vertical, lead_score, has_agent
                FROM leads
                WHERE tags IS NULL OR tags = '{}'
                LIMIT %s;
            """, (batch_size,))
            rows = cur.fetchall()
            if not rows:
                conn.commit()
                return {"updated": 0, "errors": 0, "batch_size": batch_size,
                        "note": "no untagged rows found."}
            import scanners as _scanners  # only imported when there's actually work to do
            for lead_id, address, summary, council_source, vertical, lead_score, has_agent in rows:
                try:
                    tags = _scanners._generate_tags(
                        address, summary, council_source, vertical or "tree",
                        lead_score or "small", has_agent
                    )
                    cur.execute("UPDATE leads SET tags = %s WHERE id = %s;", (tags, lead_id))
                    updated += 1
                except Exception as row_err:
                    errors += 1
                    logger.warning(f"[LeadTags] Backfill error on lead {lead_id}: {row_err}")
            conn.commit()
        finally:
            cur.close()
            conn.close()
    except Exception as e:
        logger.error(f"[LeadTags] Backfill error: {e}")
        return {"error": str(e), "updated": updated, "errors": errors}
    return {"updated": updated, "errors": errors, "batch_size": batch_size,
            "note": "re-run if 'updated' == batch_size -- there may be more rows left untagged."}


def resync_all_lead_tags(commit_every: int = 500) -> dict:
    """Sep 2 2026: same 'recompute every row, not just untagged ones'
    pattern as resync_all_partner_tags -- added for the agent_type/
    agent_guess tag split (see scanners._generate_tags), which only NEW
    lookups pick up on their own; this is what makes it reach every lead
    tagged before that split existed. Pure local recompute from columns
    already in the row (has_agent, agent_is_tree_surgeon, summary, etc.),
    no external API calls, so it's safe and cheap to process the whole
    table in one call. Commits every `commit_every` rows so a mid-run
    interruption only costs the current chunk."""
    if not SURL:
        return {"error": "no database configured"}
    updated = 0
    unchanged = 0
    errors = 0
    try:
        conn = get_db_conn()
        cur = conn.cursor()
        try:
            cur.execute("""
                SELECT id, address, summary, council_source, vertical, lead_score,
                       has_agent, agent_is_tree_surgeon, tags
                FROM leads;
            """)
            rows = cur.fetchall()
            import scanners as _scanners
            for i, (lead_id, address, summary, council_source, vertical, lead_score,
                    has_agent, agent_is_tree_surgeon, old_tags) in enumerate(rows, 1):
                try:
                    new_tags = _scanners._generate_tags(
                        address, summary, council_source, vertical or "tree",
                        lead_score or "small", has_agent, agent_is_tree_surgeon
                    )
                    if sorted(new_tags) != sorted(old_tags or []):
                        cur.execute("UPDATE leads SET tags = %s WHERE id = %s;", (new_tags, lead_id))
                        updated += 1
                    else:
                        unchanged += 1
                except Exception as row_err:
                    errors += 1
                    logger.warning(f"[LeadTags] Resync-all error on lead {lead_id}: {row_err}")
                if i % commit_every == 0:
                    conn.commit()
            conn.commit()
        finally:
            cur.close()
            conn.close()
    except Exception as e:
        logger.error(f"[LeadTags] Resync-all error: {e}")
        return {"error": str(e), "updated": updated, "unchanged": unchanged, "errors": errors}
    return {"updated": updated, "unchanged": unchanged, "errors": errors,
            "note": "full resync complete -- every lead's tags recomputed from current column values."}


def resync_region_tags(batch_size: int = 2000) -> dict:
    """Sep 2 2026: one-time correction pass for every lead already carrying
    region:unclassified from before region resolution was made
    postcode-based (see scanners._resolve_region's docstring for why the
    old council_source-trusting method left ~79% of leads unclassified).
    Recomputes just the region tag per row and swaps it in, leaving every
    other tag alone. Safe to re-run repeatedly -- only ever touches rows
    still carrying region:unclassified, same 'only touch what's actually
    unresolved' pattern as backfill_lead_tags."""
    if not SURL:
        return {"error": "no database configured"}
    try:
        conn = get_db_conn()
        cur = conn.cursor()
        try:
            cur.execute("""
                SELECT id, address, council_source, tags FROM leads
                WHERE 'region:unclassified' = ANY(tags)
                LIMIT %s;
            """, (batch_size,))
            rows = cur.fetchall()
            if not rows:
                conn.commit()
                return {"updated": 0, "unchanged": 0, "errors": 0, "batch_size": batch_size,
                        "note": "no region:unclassified rows found."}
            import scanners as _scanners  # only imported when there's actually work to do
            from concurrent.futures import ThreadPoolExecutor

            def resolve_one(row):
                lead_id, address, council_source, tags = row
                try:
                    region = _scanners._resolve_region(address, council_source)
                    return (lead_id, tags, f"region:{_scanners._slugify_tag(region)}")
                except Exception as e:
                    logger.warning(f"[LeadTags] resync_region_tags resolve error on lead {lead_id}: {e}")
                    return (lead_id, tags, None)

            with ThreadPoolExecutor(max_workers=10) as executor:
                results = list(executor.map(resolve_one, rows))

            updated = 0
            unchanged = 0
            errors = 0
            for lead_id, old_tags, new_region_tag in results:
                if new_region_tag is None:
                    errors += 1
                    continue
                if new_region_tag == "region:unclassified":
                    unchanged += 1
                    continue
                new_tags = [t for t in (old_tags or []) if not t.startswith("region:")] + [new_region_tag]
                cur.execute("UPDATE leads SET tags = %s WHERE id = %s;", (new_tags, lead_id))
                updated += 1
            conn.commit()
        finally:
            cur.close()
            conn.close()
    except Exception as e:
        logger.error(f"[LeadTags] resync_region_tags error: {e}")
        return {"error": str(e)}
    return {"updated": updated, "unchanged": unchanged, "errors": errors, "batch_size": batch_size,
            "note": "re-run if updated+unchanged+errors == batch_size -- there may be more unclassified rows left."}


def get_tag_counts() -> dict:
    """Sep 2 2026: reporting side of the tagging system -- counts leads per
    tag (unnest + GROUP BY, accelerated by the same GIN index) plus overall
    totals, so a plain-numbers report doesn't require a one-off manual query.
    Returns tags grouped by their prefix (locale/region/job/size/vertical/
    agent) since that's how Nick actually thinks about them."""
    if not SURL:
        return {"error": "no database configured"}
    try:
        conn = get_db_conn()
        cur = conn.cursor()
        try:
            cur.execute("SELECT COUNT(*) FROM leads;")
            total = cur.fetchone()[0]
            cur.execute("SELECT COUNT(*) FROM leads WHERE tags IS NULL OR tags = '{}';")
            untagged = cur.fetchone()[0]
            cur.execute("""
                SELECT tag, COUNT(*) AS n
                FROM leads, unnest(tags) AS tag
                GROUP BY tag
                ORDER BY tag;
            """)
            rows = cur.fetchall()
            grouped: dict = {}
            for tag, n in rows:
                prefix = tag.split(":", 1)[0] if ":" in tag else "other"
                grouped.setdefault(prefix, {})[tag] = n
            return {"total_leads": total, "untagged_leads": untagged, "categories": grouped}
        finally:
            cur.close()
            conn.close()
    except Exception as e:
        logger.error(f"[LeadTags] get_tag_counts error: {e}")
        return {"error": str(e)}


def backfill_partner_tags(batch_size: int = 500) -> dict:
    """Sep 2 2026: same 'only touch what's actually unresolved' backfill
    pattern as backfill_lead_tags, for every partner inserted before the
    tags column existed. See research._generate_partner_tags for what gets
    written. Imports research lazily -- only when there's actually a row to
    process -- for the same reason backfill_lead_tags imports scanners
    lazily (avoids an unnecessary import, and keeps this importable in
    isolation for tests that don't stub research.py's own dependencies)."""
    if not SURL:
        return {"error": "no database configured"}
    updated = 0
    errors = 0
    try:
        conn = get_db_conn()
        cur = conn.cursor()
        try:
            cur.execute("""
                SELECT id, sic_codes, md_name, phone_number, email, company_name
                FROM potential_partners
                WHERE tags IS NULL OR tags = '{}'
                LIMIT %s;
            """, (batch_size,))
            rows = cur.fetchall()
            if not rows:
                conn.commit()
                return {"updated": 0, "errors": 0, "batch_size": batch_size,
                        "note": "no untagged partners found."}
            import research as _research
            for partner_id, sic_codes, md_name, phone_number, email, company_name in rows:
                try:
                    tags = _research._generate_partner_tags(sic_codes, md_name, phone_number, email, company_name=company_name)
                    cur.execute("UPDATE potential_partners SET tags = %s WHERE id = %s;", (tags, partner_id))
                    updated += 1
                except Exception as row_err:
                    errors += 1
                    logger.warning(f"[PartnerTags] Backfill error on partner {partner_id}: {row_err}")
            conn.commit()
        finally:
            cur.close()
            conn.close()
    except Exception as e:
        logger.error(f"[PartnerTags] Backfill error: {e}")
        return {"error": str(e), "updated": updated, "errors": errors}
    return {"updated": updated, "errors": errors, "batch_size": batch_size,
            "note": "re-run if 'updated' == batch_size -- there may be more rows left untagged."}


def resync_all_partner_tags(commit_every: int = 500) -> dict:
    """Sep 2 2026: recomputes tags for EVERY partner row from its current
    sic_codes/md_name/phone_number/email columns -- unlike
    backfill_partner_tags, this isn't limited to rows that are still
    untagged. Added specifically for the director-name-quality audit fix
    (get_director_from_ch no longer returns corporate/nominee officers as
    'the boss', and _is_realistic_person_name rejects blank/single-word/
    corporate-looking values) -- that fix only changes what NEW lookups
    write, so this is what makes it reach every partner tagged before the
    fix existed. Pure local recompute from columns already in the row, no
    external API calls, so (unlike resync_region_tags) it's safe and cheap
    to process the whole table in one call rather than only a stuck
    subset. Commits every `commit_every` rows so a mid-run interruption
    (e.g. a redeploy) only costs the current chunk, not the whole run --
    same lesson as the enrich_existing_partners chunking fix."""
    if not SURL:
        return {"error": "no database configured"}
    updated = 0
    unchanged = 0
    errors = 0
    try:
        conn = get_db_conn()
        cur = conn.cursor()
        try:
            cur.execute("SELECT id, sic_codes, md_name, phone_number, email, tags, company_name FROM potential_partners;")
            rows = cur.fetchall()
            import research as _research
            for i, (partner_id, sic_codes, md_name, phone_number, email, old_tags, company_name) in enumerate(rows, 1):
                try:
                    new_tags = _research._generate_partner_tags(sic_codes, md_name, phone_number, email, company_name=company_name)
                    if sorted(new_tags) != sorted(old_tags or []):
                        cur.execute("UPDATE potential_partners SET tags = %s WHERE id = %s;", (new_tags, partner_id))
                        updated += 1
                    else:
                        unchanged += 1
                except Exception as row_err:
                    errors += 1
                    logger.warning(f"[PartnerTags] Resync-all error on partner {partner_id}: {row_err}")
                if i % commit_every == 0:
                    conn.commit()
            conn.commit()
        finally:
            cur.close()
            conn.close()
    except Exception as e:
        logger.error(f"[PartnerTags] Resync-all error: {e}")
        return {"error": str(e), "updated": updated, "unchanged": unchanged, "errors": errors}
    return {"updated": updated, "unchanged": unchanged, "errors": errors,
            "note": "full resync complete -- every partner's tags recomputed from current column values."}


def requeue_dead_contact_enrichment() -> dict:
    """Sep 2 2026: clears enriched_at for partners that were processed but
    found NEITHER a phone nor an email (contact:dead), so the next
    autonomous cycle's enrich_existing_partners(limit=0) call gives them a
    genuine second attempt instead of skipping them forever.

    Why this is needed, not just theoretical: enrich_existing_partners only
    ever selects rows WHERE enriched_at IS NULL -- once a partner has been
    processed even once, it is never looked at again regardless of whether
    the attempt actually succeeded. Meanwhile every phone/website lookup in
    this file went through get_google_places_info(), which scraped DDG's
    html.duckduckgo.com with a `time.sleep(1.2)` INSIDE the function body --
    but every call site runs that function inside a ThreadPoolExecutor with
    8-20 worker threads, so the sleep only throttled each thread against
    itself, not the group. That let bursts of up to 20 simultaneous
    requests hit DDG together, well within range of getting rate-limited or
    served a block page -- which fails silently (a non-200 response just
    falls through to 'no website found', identical in the data to a
    company that genuinely has no discoverable web presence). Nick caught
    this directly: pasted production logs showing "Phone: N/A | Email: N/A"
    for essentially every single company in a row, including established
    real businesses that almost certainly have a findable number --
    inconsistent with "no data available" and consistent with the DDG
    throttle being ineffective. That throttle is now a shared cross-thread
    lock (_DDG_MIN_INTERVAL in research.py), but every partner already
    marked enriched_at under the OLD broken throttle is permanently stuck
    with whatever it found (often nothing) and will never be retried by
    enrich_existing_partners on its own. This function is the one-time
    catch-up: only partners with zero contact info found (phone_number IS
    NULL AND email IS NULL) are re-queued -- anything that already has a
    phone or email is left alone, since those already worked and don't
    need re-checking.
    """
    if not SURL:
        return {"error": "no database configured"}
    try:
        conn = get_db_conn()
        cur = conn.cursor()
        try:
            cur.execute(
                """
                UPDATE potential_partners
                SET enriched_at = NULL
                WHERE enriched_at IS NOT NULL
                  AND phone_number IS NULL
                  AND email IS NULL;
                """
            )
            requeued = cur.rowcount
            conn.commit()
        finally:
            cur.close()
            conn.close()
    except Exception as e:
        logger.error(f"[Enrichment] Requeue-dead-contacts error: {e}")
        return {"error": str(e)}
    return {"requeued": requeued,
            "note": "these partners will be re-attempted on the next enrich_existing_partners run, now under the fixed cross-thread DDG throttle."}


def get_partner_tag_counts() -> dict:
    """Sep 2 2026: reporting side of the partner tagging system -- see
    research._generate_partner_tags. 'dead' here (contact:dead) is Nick's
    own word for a partner with no realistic phone AND no realistic email
    -- reachable through neither channel, so worthless to the business
    regardless of how complete anything else about the record is."""
    if not SURL:
        return {"error": "no database configured"}
    try:
        conn = get_db_conn()
        cur = conn.cursor()
        try:
            cur.execute("SELECT COUNT(*) FROM potential_partners;")
            total = cur.fetchone()[0]
            cur.execute("SELECT COUNT(*) FROM potential_partners WHERE tags IS NULL OR tags = '{}';")
            untagged = cur.fetchone()[0]
            cur.execute("""
                SELECT tag, COUNT(*) AS n
                FROM potential_partners, unnest(tags) AS tag
                GROUP BY tag
                ORDER BY tag;
            """)
            rows = cur.fetchall()
            grouped: dict = {}
            for tag, n in rows:
                prefix = tag.split(":", 1)[0] if ":" in tag else "other"
                grouped.setdefault(prefix, {})[tag] = n
            return {"total_partners": total, "untagged_partners": untagged, "categories": grouped}
        finally:
            cur.close()
            conn.close()
    except Exception as e:
        logger.error(f"[PartnerTags] get_partner_tag_counts error: {e}")
        return {"error": str(e)}


def get_system_state(key: str) -> Optional[str]:
    """Reads one value from the tiny durable key/value store (see
    system_state's own comment for why this exists -- surviving Render
    restarts is the entire point). Returns None if unset or on any error,
    same 'never crash the caller over a non-critical read' posture as the
    rest of this file's small helpers."""
    if not SURL:
        return None
    try:
        conn = get_db_conn()
        cur = conn.cursor()
        cur.execute("SELECT value FROM system_state WHERE key = %s;", (key,))
        row = cur.fetchone()
        cur.close()
        conn.close()
        return row[0] if row else None
    except Exception as e:
        logger.debug(f"[SystemState] get_system_state({key!r}) error: {e}")
        return None


def set_system_state(key: str, value: str) -> bool:
    """Writes one value to the durable key/value store. Returns False (not
    an exception) on failure -- callers (the autonomous scheduler) should
    degrade to 'try again next check' rather than crash."""
    if not SURL:
        return False
    try:
        conn = get_db_conn()
        cur = conn.cursor()
        cur.execute("""
            INSERT INTO system_state (key, value, updated_at) VALUES (%s, %s, NOW())
            ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, updated_at = NOW();
        """, (key, value))
        conn.commit()
        cur.close()
        conn.close()
        return True
    except Exception as e:
        logger.warning(f"[SystemState] set_system_state({key!r}) error: {e}")
        return False


def save_qr_campaign_lead(src: Optional[str], name: str, phone: Optional[str],
                           email: Optional[str], town: Optional[str]) -> bool:
    """Sep 3 2026: records one interest-capture submission from the
    /partner-offer QR landing page. Returns False (never raises) on any
    failure -- a lost marketing-form submission is a real shame but must
    never 500 the page the person is looking at on their phone."""
    if not SURL:
        return False
    try:
        conn = get_db_conn()
        cur = conn.cursor()
        cur.execute("""
            INSERT INTO qr_campaign_leads (src, name, phone, email, town)
            VALUES (%s, %s, %s, %s, %s);
        """, (src, name, phone, email, town))
        conn.commit()
        cur.close()
        conn.close()
        return True
    except Exception as e:
        logger.warning(f"[QR Campaign] save_qr_campaign_lead error: {e}")
        return False


def get_qr_campaign_stats() -> list:
    """Sep 3 2026: submission counts grouped by campaign src code, most
    recent first -- lets Nick see which printed batch/channel is actually
    generating interest without needing direct DB access. Returns [] on
    any error rather than raising."""
    if not SURL:
        return []
    try:
        conn = get_db_conn()
        cur = conn.cursor()
        cur.execute("""
            SELECT COALESCE(src, '(none)') AS src, COUNT(*) AS submissions,
                   MAX(created_at) AS last_submission
            FROM qr_campaign_leads
            GROUP BY src
            ORDER BY last_submission DESC;
        """)
        rows = cur.fetchall()
        cur.close()
        conn.close()
        return [{"src": r[0], "submissions": r[1], "last_submission": r[2]} for r in rows]
    except Exception as e:
        logger.warning(f"[QR Campaign] get_qr_campaign_stats error: {e}")
        return []


def reset_monthly_quotas_if_needed() -> int:
    """
    Resets delivered_this_month to 0 for all active subscribers at the start of each new month.
    Safe to call daily — only resets when the last dispatch was in a previous calendar month.
    Returns number of subscriber rows reset.
    """
    import datetime
    if not SURL:
        return 0
    try:
        conn = get_db_conn()
        cur = conn.cursor()
        try:
            cur.execute("""
                UPDATE contractor_subscriptions
                SET delivered_this_month = 0
                WHERE active = TRUE
                  AND delivered_this_month > 0
                  AND (
                    last_dispatched_at IS NULL
                    OR DATE_TRUNC('month', last_dispatched_at) < DATE_TRUNC('month', NOW())
                  )
                RETURNING id;
            """)
            reset_rows = cur.fetchall()
            conn.commit()
            count = len(reset_rows)
            if count > 0:
                logger.info(f"[Monthly Reset] Reset delivered_this_month for {count} subscribers.")
            return count
        finally:
            cur.close()
            conn.close()
    except Exception as e:
        logger.error(f"[Monthly Reset] Error resetting quotas: {e}")
        return 0


# ── Tier quotas: realistic monthly lead limits per plan ───────────────────────
TIER_QUOTAS = {
    "stump_pro": 3,
    "climber_domestic": 5,
    "arb_consultant": 8,
    "commercial_forestry": 12,
    "treekey_elite": 18,
    "sole_trader": 5,
    "commercial_pro": 14,
    "regional_elite": 25,
}

# ── Tier radius caps: server-side enforcement so a cheaper tier can't select a
# larger radius than it's entitled to (the checkout form previously offered the
# same 10-50mi choice to every plan with nothing enforcing it). Elite (30mi) and
# Regional Elite (50mi) match the radius figures already advertised in their copy.
TIER_MAX_RADIUS = {
    "stump_pro": 15,
    "climber_domestic": 15,
    "arb_consultant": 20,
    "commercial_forestry": 25,
    "treekey_elite": 30,
    "sole_trader": 15,
    "commercial_pro": 25,
    "regional_elite": 50,
}

# ── Tier dispatch priority: higher tiers are sold "priority routing" (e.g. Elite's
# "first-priority API routing"), but dispatch previously ordered strictly by
# subscribed_at with no tier weighting at all. Used as a stable sort key ahead of
# seniority — ties within the same priority band still resolve by subscribed_at.
TIER_PRIORITY = {
    "treekey_elite": 5,
    "regional_elite": 5,
    "commercial_forestry": 4,
    "commercial_pro": 4,
    "arb_consultant": 3,
    "climber_domestic": 2,
    "sole_trader": 2,
    "stump_pro": 1,
}


def _geocode_failure(kind: str, value: str, err: Exception) -> None:
    """Sep 8 2026: shared handler for genuine (non-'not found') geocoding
    errors -- network failures, timeouts, or bugs in this process itself.
    Previously these were swallowed at logger.debug (invisible in
    production) which is exactly how a missing `import requests` at the
    top of this file went unnoticed: every single call to
    lookup_outcode_centroid/lookup_full_postcode_centroid was raising
    NameError, silently caught here, always returning (None, None) --
    meaning haversine-radius matching (a core tier of dispatch_lead_alerts'
    matching logic, not just the new full-postcode feature) had been
    silently non-functional. Now logged at ERROR (visible in Render logs)
    AND recorded as a WARNING-severity system incident so a recurrence
    surfaces in the daily digest automatically, per Nick's "build fail-safe
    systems... a different auto fix system for every unique issue" ask --
    this is the geocoding-specific one."""
    logger.error(f"[postcodes.io] {kind} lookup failed for '{value}': {err}")
    try:
        log_system_warning(
            "GEOCODING API FAILURE",
            f"postcodes.io {kind} lookup error",
            f"{kind} lookup for '{value}' raised {type(err).__name__}: {err}",
        )
    except Exception:
        pass  # never let incident logging itself become the incident


def lookup_outcode_centroid(outcode: str) -> tuple:
    """
    Returns (lat, lon) centroid for a UK outcode via the free postcodes.io API.
    Returns (None, None) if not found or API unavailable.
    """
    try:
        clean = outcode.strip().upper().replace(" ", "")
        resp = requests.get(
            f"https://api.postcodes.io/outcodes/{clean}",
            timeout=8
        )
        if resp.status_code == 200:
            result = resp.json().get("result", {})
            lat = result.get("latitude")
            lon = result.get("longitude")
            if lat and lon:
                return (float(lat), float(lon))
        # A non-200 (typically 404 "not a real outcode") is normal, expected
        # behaviour for bad user input -- not an incident, just no result.
    except Exception as e:
        _geocode_failure("outcode centroid", outcode, e)
    return (None, None)


_FULL_POSTCODE_RE = re.compile(r'^([A-Z]{1,2}[0-9][A-Z0-9]?)\s*([0-9][A-Z]{2})$')


def lookup_full_postcode_centroid(postcode: str) -> tuple:
    """Sep 8 2026: exact-pin counterpart to lookup_outcode_centroid above --
    same free postcodes.io API, but /postcodes/{full postcode} instead of
    /outcodes/{outcode}, so this returns the real point for that address's
    postcode unit rather than the centroid of its whole outcode (which can
    span several miles in rural areas). Returns (None, None) if not a real,
    resolvable full postcode."""
    try:
        clean = postcode.strip().upper().replace(" ", "")
        resp = requests.get(f"https://api.postcodes.io/postcodes/{clean}", timeout=8)
        if resp.status_code == 200:
            result = resp.json().get("result", {})
            lat = result.get("latitude")
            lon = result.get("longitude")
            if lat and lon:
                return (float(lat), float(lon))
    except Exception as e:
        _geocode_failure("full postcode", postcode, e)
    return (None, None)


def resolve_location(raw_input: str) -> dict:
    """Sep 8 2026, Nick's proximity-system rework: one place that decides
    whether a customer typed a full UK postcode ("CR5 2LE") or just an
    outcode ("CR5" / "B1"), and geocodes it the right way either way --
    exact pin for a full postcode, outcode centroid otherwise. Used by
    checkout signup (register_or_update_subscription) so a customer who
    gives their full postcode gets an exact-distance pin, while one who
    only gives an outcode still works exactly as before (nothing regresses
    for the common case). Mirrors the dual-format handling already proven
    in api_check_postcode's map-search box, so there's one consistent
    definition of "postcode or outcode" across the whole app.

    Returns {"outcode": str, "full_postcode": str|None, "lat": float|None,
    "lon": float|None, "precision": "exact"|"area"|"none"}."""
    cleaned = (raw_input or "").strip().upper()
    no_space = cleaned.replace(" ", "")
    # _FULL_POSTCODE_RE's `\s*` already tolerates "CR5 2LE", "CR5  2LE" or
    # "CR52LE" typed with no space at all -- match against the raw cleaned
    # string first (keeps any space the customer typed), falling back to
    # the space-stripped form for the no-space case.
    m = _FULL_POSTCODE_RE.match(cleaned) or _FULL_POSTCODE_RE.match(no_space)
    if m:
        outcode = m.group(1)
        lat, lon = lookup_full_postcode_centroid(no_space)
        if lat is not None:
            return {"outcode": outcode, "full_postcode": f"{m.group(1)} {m.group(2)}",
                    "lat": lat, "lon": lon, "precision": "exact"}
        # Looked like a full postcode but postcodes.io couldn't resolve it
        # (typo, or a real-but-unlisted postcode) -- fall back to treating
        # just the outcode part as the area, same as if they'd only typed that.
        lat, lon = lookup_outcode_centroid(outcode)
        return {"outcode": outcode, "full_postcode": None, "lat": lat, "lon": lon,
                "precision": "area" if lat is not None else "none"}

    # Not a full-postcode shape -- treat the whole thing as an outcode.
    outcode = no_space[:4] if no_space else ""
    lat, lon = lookup_outcode_centroid(outcode) if outcode else (None, None)
    return {"outcode": outcode, "full_postcode": None, "lat": lat, "lon": lon,
            "precision": "area" if lat is not None else "none"}


def haversine_miles(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Returns great-circle distance in miles between two lat/lon points."""
    import math
    R = 3958.8  # Earth radius in miles
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2
    return 2 * R * math.asin(math.sqrt(a))


_OSM_CHIP_DROP_TAGS = [
    ("landuse", "farmyard", "🚜 Farm"),
    ("landuse", "allotments", "🌱 Allotments"),
    ("leisure", "horse_riding", "🐴 Stables / Equestrian"),
    ("shop", "garden_centre", "🌳 Garden Centre"),
]


def find_chip_drop_candidates_via_osm(lat: float, lon: float, radius_miles: float = 10.0, limit: int = 15) -> list:
    """Sep 8 2026, Nick's ask: an automated "find nearby chip-drop
    candidates" layer on top of the self-registration directory (see
    get_chip_drop_spots), inspired by the original Gemini Pro brief.
    Deliberately built on OpenStreetMap's free, keyless Overpass API
    rather than Google Places -- Nick's explicit instruction was not to
    spend even a small amount of the existing GOOGLE_PLACES_MONTHLY_PAID_CALL_CAP
    quota in research.py (that cap is carefully budgeted for the core
    contractor-enrichment pipeline) on this secondary feature. This is a
    completely separate API, on a separate free tier, so it cannot touch
    that budget at all.

    IMPORTANT: unlike get_chip_drop_spots, these are NOT confirmed,
    opted-in drop sites -- they're real places (farms, allotments,
    stables, garden centres) tagged in OpenStreetMap near the given point,
    surfaced as candidates worth a phone call, nothing more. Callers must
    label them as unconfirmed (main.py's /chip-drop route does this with a
    distinct badge) -- presenting them as equivalent to a real registered
    listing would repeat exactly the fabricated-data mistake this
    session's earlier /chip-drop fix removed, just with real place names
    attached instead of invented ones.

    Returns a list of {name, category, lat, lon, distance_miles,
    address_hint, osm_url} dicts, nearest first. Returns [] on any
    failure (network, no results, bad coords) -- this is an assistive
    discovery tool, never something a caller should treat as required to
    succeed."""
    if lat is None or lon is None:
        return []
    radius_m = int(min(radius_miles, 30.0) * 1609.34)  # capped at 30mi -- Overpass gets slow/heavy on huge radii

    clauses = "".join([
        f'node["{k}"="{v}"]["name"](around:{radius_m},{lat},{lon});\n'
        f'way["{k}"="{v}"]["name"](around:{radius_m},{lat},{lon});\n'
        for k, v, _ in _OSM_CHIP_DROP_TAGS
    ])
    query = f"[out:json][timeout:20];\n(\n{clauses}\n);\nout center tags {limit * 4};"

    try:
        # Overpass's fair-use policy asks API consumers to identify
        # themselves with a real User-Agent rather than a generic client
        # default -- helps avoid being mistaken for anonymous scraping
        # traffic and throttled.
        resp = requests.post(
            "https://overpass-api.de/api/interpreter",
            data={"data": query},
            headers={"User-Agent": "TreeKey/1.0 (treekey.uk; contact@treekey.uk)"},
            timeout=25,
        )
        if resp.status_code != 200:
            logger.warning(f"[ChipDrop/OSM] Overpass returned {resp.status_code} for ({lat},{lon})")
            return []
        elements = resp.json().get("elements", [])
    except Exception as e:
        logger.warning(f"[ChipDrop/OSM] Overpass lookup failed for ({lat},{lon}): {e}")
        return []

    tag_to_label = {(k, v): label for k, v, label in _OSM_CHIP_DROP_TAGS}
    candidates = []
    for el in elements:
        tags = el.get("tags", {}) or {}
        name = tags.get("name")
        if not name:
            continue
        el_lat = el.get("lat") or (el.get("center") or {}).get("lat")
        el_lon = el.get("lon") or (el.get("center") or {}).get("lon")
        if el_lat is None or el_lon is None:
            continue

        category = "📍 Possible Site"
        for k, v, label in _OSM_CHIP_DROP_TAGS:
            if tags.get(k) == v:
                category = label
                break

        addr_parts = [tags.get("addr:housenumber", ""), tags.get("addr:street", ""), tags.get("addr:city", "") or tags.get("addr:town", ""), tags.get("addr:postcode", "")]
        address_hint = " ".join(p for p in addr_parts if p).strip() or None

        dist = haversine_miles(lat, lon, el_lat, el_lon)
        if dist > radius_miles:
            continue

        osm_type = el.get("type", "node")  # Overpass always includes "type" ("node"/"way"/"relation") on every element
        candidates.append({
            "name": name,
            "category": category,
            "lat": el_lat,
            "lon": el_lon,
            "distance_miles": round(dist, 1),
            "address_hint": address_hint,
            "osm_url": f"https://www.openstreetmap.org/{osm_type}/{el.get('id')}",
        })

    candidates.sort(key=lambda c: c["distance_miles"])
    return candidates[:limit]


def increment_api_usage(api_name: str = "UK Planning API", increment: int = 1, cap: int = 500) -> dict:
    """
    Increments monthly API counter and runs predictive velocity forecasting.
    Detects weeks in advance if current daily request pace will breach the 500 cap.
    Returns: {
        "count": int,
        "projected_monthly": int,
        "days_left": int,
        "warning_needed": bool,
        "reason": str
    }
    """
    import datetime
    import calendar
    now = datetime.datetime.now(datetime.timezone.utc)
    period = now.strftime("%Y-%m")
    day_of_month = now.day
    _, num_days_in_month = calendar.monthrange(now.year, now.month)
    days_left = num_days_in_month - day_of_month

    out = {
        "count": 0,
        "projected_monthly": 0,
        "days_left": days_left,
        "warning_needed": False,
        "reason": ""
    }
    if not SURL:
        return out
    try:
        conn = get_db_conn()
        cur = conn.cursor()
        cur.execute("""
            INSERT INTO api_usage (api_name, period_month, call_count, warning_sent, updated_at)
            VALUES (%s, %s, %s, FALSE, NOW())
            ON CONFLICT (api_name, period_month)
            DO UPDATE SET 
                call_count = api_usage.call_count + EXCLUDED.call_count,
                updated_at = NOW()
            RETURNING call_count, warning_sent;
        """, (api_name, period, increment))
        row = cur.fetchone()
        if row:
            count, warning_sent = row
            out["count"] = count
            
            # Predictive Burn Rate Calculation:
            daily_burn_rate = count / max(day_of_month, 1)
            projected_monthly = int(round(daily_burn_rate * num_days_in_month))
            out["projected_monthly"] = projected_monthly

            # Trigger conditions for early warning:
            # 1. Projected total >= 480 (pace breaches cap) and at least 30 calls made
            # 2. OR absolute usage >= 350 requests (70% threshold)
            if not warning_sent:
                if count >= 350:
                    out["warning_needed"] = True
                    out["reason"] = f"CRITICAL: {count}/{cap} requests used ({round(count/cap*100)}% of limit)."
                elif projected_monthly >= 480 and count >= 30:
                    out["warning_needed"] = True
                    out["reason"] = f"PREDICTIVE PACE: Burn rate of {round(daily_burn_rate, 1)} req/day projects {projected_monthly} total requests by day {num_days_in_month}, breaching the {cap} cap."

                if out["warning_needed"]:
                    cur.execute("""
                        UPDATE api_usage SET warning_sent = TRUE 
                        WHERE api_name = %s AND period_month = %s;
                    """, (api_name, period))
        conn.commit()
        cur.close()
        conn.close()
    except Exception as e:
        logger.debug(f"[API Usage] Tracking error: {e}")
    return out


def is_territory_claimed(outcode: str, conn=None) -> bool:
    """Checks whether a given UK postcode district is already locked by an active subscriber.

    Sep 9 2026: optional `conn` lets a caller that already has a connection
    open (api_check_postcode, on every map click) reuse it instead of
    opening yet another one -- see the matching note on
    classify_leads_by_radius. Callers that don't pass one keep the old
    self-contained open/close behaviour."""
    if not SURL or not outcode:
        return False
    owns_conn = conn is None
    try:
        if owns_conn:
            conn = get_db_conn()
        cur = conn.cursor()
        try:
            cur.execute("SELECT active FROM territory_claims WHERE outcode = %s AND active = TRUE", (outcode.strip().upper(),))
            row = cur.fetchone()
            return bool(row and row[0])
        finally:
            cur.close()
            if owns_conn:
                conn.close()
    except Exception as e:
        logger.error(f"[Territory] Check error for {outcode}: {e}")
        return False


def claim_territory_atomically(outcode: str, customer_email: str, stripe_sub_id: str = "") -> bool:
    """
    Atomically claims a 15-mile radial territory district for a paying customer.
    Prevents race conditions: returns True if successfully locked, False if already claimed.
    """
    if not SURL or not outcode:
        return False
    try:
        conn = get_db_conn()
        cur = conn.cursor()
        try:
            cur.execute("""
                INSERT INTO territory_claims (outcode, customer_email, stripe_subscription_id, active, claimed_at)
                VALUES (%s, %s, %s, TRUE, NOW())
                ON CONFLICT (outcode) DO UPDATE SET
                    customer_email = EXCLUDED.customer_email,
                    stripe_subscription_id = EXCLUDED.stripe_subscription_id,
                    active = TRUE,
                    claimed_at = NOW()
                WHERE territory_claims.active = FALSE
                RETURNING id;
            """, (outcode.strip().upper(), customer_email.strip().lower(), stripe_sub_id))
            row = cur.fetchone()
            conn.commit()
            return bool(row)
        finally:
            cur.close()
            conn.close()
    except Exception as e:
        logger.error(f"[Territory] Atomic claim error for {outcode}: {e}")
        return False


def unlock_territory_by_subscription(stripe_sub_id: str) -> bool:
    """
    Unlocks a territory when a customer's Stripe subscription is cancelled or fails.
    """
    if not SURL or not stripe_sub_id:
        return False
    try:
        conn = get_db_conn()
        cur = conn.cursor()
        try:
            cur.execute("""
                UPDATE territory_claims
                SET active = FALSE
                WHERE stripe_subscription_id = %s
                RETURNING outcode;
            """, (stripe_sub_id,))
            row = cur.fetchone()
            # Also deactivate the subscription record itself — this is what login/dashboard
            # gating (get_contractor_subscription) actually reads, and previously stayed
            # active=TRUE forever after cancellation (ghost session persisted).
            cur.execute("""
                UPDATE contractor_subscriptions
                SET active = FALSE
                WHERE stripe_subscription_id = %s;
            """, (stripe_sub_id,))
            conn.commit()
            if row:
                logger.info(f"[Territory] Unlocked territory {row[0]} due to subscription cancellation: {stripe_sub_id}")
                return True
            return False
        finally:
            cur.close()
            conn.close()
    except Exception as e:
        logger.error(f"[Territory] Unlock error for sub {stripe_sub_id}: {e}")
        return False


def get_active_territory_claims() -> list:
    """
    Returns a list of dictionaries containing active territory claims for lead routing.
    Format: [{"outcode": "NG22", "customer_email": "...", "customer_name": "..."}]
    """
    if not SURL:
        return []
    try:
        conn = get_db_conn()
        cur = conn.cursor()
        try:
            cur.execute("SELECT outcode, customer_email, customer_name FROM territory_claims WHERE active = TRUE;")
            rows = cur.fetchall()
            return [{"outcode": r[0], "customer_email": r[1], "customer_name": r[2]} for r in rows]
        finally:
            cur.close()
            conn.close()
    except Exception as e:
        logger.error(f"[Territory] Error fetching active claims: {e}")
        return []


def save_contractor_suggestion(name: str, contact: str, suggestion_text: str) -> bool:
    """Saves contractor suggestions and feedback to the database."""
    if not SURL or not suggestion_text:
        return False
    try:
        conn = get_db_conn()
        cur = conn.cursor()
        try:
            cur.execute("""
                INSERT INTO contractor_suggestions (contractor_name, phone_or_email, suggestion)
                VALUES (%s, %s, %s)
                RETURNING id;
            """, (name.strip() if name else "Anonymous Arborist", contact.strip() if contact else None, suggestion_text.strip()))
            row = cur.fetchone()
            conn.commit()
            return bool(row)
        finally:
            cur.close()
            conn.close()
    except Exception as e:
        logger.error(f"[Feedback] Error saving contractor suggestion: {e}")
        return False


def burn_lead_inventory(lead_id: str, buyer_email: str) -> dict:
    """
    Single-Sale Inventory Burn Protocol:
    The millisecond a lead is purchased or claimed, it is permanently marked as 'claimed'
    and assigned to buyer_email so it is impossible to be displayed or sold to anyone else.
    """
    if not SURL or not lead_id:
        return None
    try:
        conn = get_db_conn()
        cur = conn.cursor()
        try:
            cur.execute("""
                UPDATE leads
                SET status = 'claimed'
                WHERE (id::text = %s OR reference = %s) AND (status = 'new' OR status IS NULL)
                RETURNING id, reference, address, summary, council_source, lead_score, lead_price,
                          applicant_name, agent_name, agent_company, has_agent;
            """, (lead_id, lead_id))
            row = cur.fetchone()
            conn.commit()
            if row:
                logger.info(f"[Inventory Burn] Lead {lead_id} permanently claimed & burned by {buyer_email}.")
                return {
                    "id": row[0],
                    "reference": row[1],
                    "address": row[2],
                    "summary": row[3],
                    "council_source": row[4],
                    "lead_score": row[5],
                    "lead_price": row[6],
                    "applicant_name": row[7],
                    "agent_name": row[8],
                    "agent_company": row[9],
                    "has_agent": row[10],
                }
            return None
        finally:
            cur.close()
            conn.close()
    except Exception as e:
        logger.error(f"[Inventory Burn] Error burning lead {lead_id}: {e}")
        return None


def _extract_outcodes(address: str) -> list:
    """Sep 5 2026: pulled out of notifications.dispatch_lead_alerts' inline
    regex so the free-signup lead-matching code below can find a lead's
    rough location the exact same way the paid-subscriber dispatch path
    already does, instead of inventing a second, possibly-inconsistent
    way of reading a UK outcode out of a free-text address."""
    import re
    return [m.group(1) for m in re.finditer(r'\b([A-Z]{1,2}[0-9][A-Z0-9]?)\s*([0-9][A-Z]{2})\b', (address or "").upper())]


# Sep 8 2026: the 32 London boroughs plus the City of London, lower-cased,
# used only to decide whether an outcode's postcodes.io admin_district
# counts as "London" for the ticker's geographic mix (see
# select_diverse_ticker_leads below). Deliberately a fixed, checkable list
# rather than a guess from the outcode letters themselves (postcode areas
# like "BR"/"CR"/"KT"/"TW" straddle Greater London and neighbouring
# counties, so only the actual admin_district name is reliable).
_LONDON_BOROUGHS = frozenset({
    "westminster", "camden", "islington", "hackney", "tower hamlets", "greenwich",
    "lewisham", "southwark", "lambeth", "wandsworth", "hammersmith and fulham",
    "hammersmith & fulham", "kensington and chelsea", "kensington & chelsea",
    "city of london", "barking and dagenham", "barnet", "bexley", "brent", "bromley",
    "croydon", "ealing", "enfield", "haringey", "harrow", "havering", "hillingdon",
    "hounslow", "kingston upon thames", "merton", "newham", "redbridge",
    "richmond upon thames", "sutton", "waltham forest",
})


def get_outcode_area_label(outcode: str) -> dict:
    """Sep 8 2026, Nick's ask: the ticker and radar notices should show a
    real place name next to the postcode area (e.g. "CR5, Croydon, London"),
    not just the bare outcode. Resolves an outcode's district via the same
    free postcodes.io API already used for geocoding, cached in
    outcode_area_cache so this is a one-time lookup per outcode, not a live
    API call on every pageview. Returns {"district": str|None,
    "is_london": bool, "label": str, "lat": float|None, "lon": float|None}
    -- label degrades gracefully to just the outcode if the district can't
    be resolved. lat/lon (Sep 8 2026 addition) are the outcode's centroid,
    read from the exact same postcodes.io response already fetched here for
    district -- no extra API call -- so classify_leads_by_radius below can
    do a genuine distance check using data this function was already
    caching."""
    outcode = (outcode or "").strip().upper()
    if not outcode:
        return {"district": None, "is_london": False, "label": "UK", "lat": None, "lon": None}

    def _label(district, is_london):
        if not district:
            return outcode
        return f"{outcode}, {district}, London" if is_london else f"{outcode}, {district}"

    try:
        conn = get_db_conn()
        cur = conn.cursor()
        cur.execute("SELECT district, is_london, lat, lon FROM outcode_area_cache WHERE outcode = %s", (outcode,))
        row = cur.fetchone()
        cur.close()
        conn.close()
        if row:
            district, is_london, lat, lon = row
            return {"district": district, "is_london": bool(is_london), "label": _label(district, is_london),
                    "lat": lat, "lon": lon}
    except Exception as e:
        logger.debug(f"[Area Label] cache read failed for {outcode}: {e}")

    district = None
    lat = lon = None
    try:
        # Sep 8 2026: shortened from 6s -- Nick flagged the radar as "very
        # slow to update", and this call can now run up to max_fresh_lookups
        # times in a single classify_leads_by_radius request, so a slow
        # timeout compounds fast. 3s is still generous for a normally-quick
        # free API and bounds the worst case.
        resp = requests.get(f"https://api.postcodes.io/outcodes/{outcode}", timeout=3)
        if resp.status_code == 200:
            result = resp.json().get("result", {}) or {}
            districts = result.get("admin_district") or []
            district = districts[0] if districts else None
            lat = result.get("latitude")
            lon = result.get("longitude")
    except Exception as e:
        _geocode_failure("outcode area label", outcode, e)

    is_london = bool(district) and district.strip().lower() in _LONDON_BOROUGHS
    try:
        conn = get_db_conn()
        cur = conn.cursor()
        cur.execute(
            """INSERT INTO outcode_area_cache (outcode, district, is_london, lat, lon)
               VALUES (%s, %s, %s, %s, %s)
               ON CONFLICT (outcode) DO UPDATE SET district = EXCLUDED.district, is_london = EXCLUDED.is_london,
                   lat = EXCLUDED.lat, lon = EXCLUDED.lon""",
            (outcode, district, is_london, lat, lon),
        )
        conn.commit()
        cur.close()
        conn.close()
    except Exception as e:
        logger.debug(f"[Area Label] cache write failed for {outcode}: {e}")

    return {"district": district, "is_london": is_london, "label": _label(district, is_london), "lat": lat, "lon": lon}


def get_lead_discovery_counts() -> dict:
    """Real counts of leads discovered today / this week / this calendar
    month -- for the honest "counting up" ticker display. Never padded;
    zero is shown as zero."""
    try:
        conn = get_db_conn()
        cur = conn.cursor()
        cur.execute("""
            SELECT
                COUNT(*) FILTER (WHERE discovered_at >= date_trunc('day', NOW())),
                COUNT(*) FILTER (WHERE discovered_at >= date_trunc('week', NOW())),
                COUNT(*) FILTER (WHERE discovered_at >= date_trunc('month', NOW()))
            FROM leads
        """)
        row = cur.fetchone()
        cur.close()
        conn.close()
        return {"today": row[0] or 0, "week": row[1] or 0, "month": row[2] or 0}
    except Exception as e:
        logger.error(f"[Counters] Error fetching lead discovery counts: {e}")
        return {"today": 0, "week": 0, "month": 0}


def get_public_lead_counts() -> dict:
    """Sep 8 2026, Nick's ask: "if we get 100 leads in the scan in the
    morning in one go, I wanted that counter to tick through the day rather
    than dumping the leads in one go... its not disingenuous [because] its a
    counter, they will not be able to see the leads are already live in the
    marketplace." This never invents a lead or shows a number bigger than
    what's real -- it only paces WHEN the true, already-real "today" total
    is revealed to the public counter. State is persisted per calendar day
    (public_counter_reveal) so every visitor sees the same number and a page
    refresh can't jump the queue. Outside the 6am-11pm working window (or on
    any error) it just shows the real total -- nothing left to pace by then.
    Week/month are always the real totals, unthrottled -- Nick's ask was
    specifically about the "ticks up through the day" Today figure."""
    real = get_lead_discovery_counts()
    try:
        import random
        import datetime
        from zoneinfo import ZoneInfo
        now_uk = datetime.datetime.now(ZoneInfo("Europe/London"))
    except Exception as e:
        logger.debug(f"[Public Counters] tz setup failed, falling back to real counts: {e}")
        return real

    real_today = real["today"]
    if now_uk.hour < 6 or now_uk.hour >= 23:
        return real

    today_date = now_uk.date()
    window_end = now_uk.replace(hour=23, minute=0, second=0, microsecond=0)
    now_ts = datetime.datetime.now(datetime.timezone.utc)

    try:
        conn = get_db_conn()
        cur = conn.cursor()
        cur.execute("SELECT revealed_today, next_reveal_at FROM public_counter_reveal WHERE reveal_date = %s", (today_date,))
        row = cur.fetchone()

        if row is None:
            revealed, next_at = 0, now_ts
            cur.execute(
                """INSERT INTO public_counter_reveal (reveal_date, revealed_today, last_reveal_at, next_reveal_at)
                   VALUES (%s, %s, %s, %s) ON CONFLICT (reveal_date) DO NOTHING""",
                (today_date, revealed, now_ts, next_at),
            )
            conn.commit()
        else:
            revealed, next_at = row
            if next_at and next_at.tzinfo is None:
                next_at = next_at.replace(tzinfo=datetime.timezone.utc)

        revealed = min(revealed, real_today)  # never let a stale reveal outrun a real total that dropped (e.g. data fix)
        backlog = real_today - revealed

        if backlog > 0 and now_ts >= (next_at or now_ts):
            bump = random.choice([1, 1, 1, 2, 2, 3])
            revealed = min(real_today, revealed + bump)

            # Adaptive pacing: a big backlog with little of the working day
            # left drains faster (so it can genuinely finish by 11pm); a
            # small backlog with hours left drips lazily. Either way, always
            # "sometimes fast, sometimes slower" via the random jitter.
            remaining_seconds = max(60.0, (window_end - now_uk).total_seconds())
            still_owed = max(1, real_today - revealed)
            ideal_interval = remaining_seconds / max(1.0, still_owed / 2.0)
            interval_seconds = max(45.0, min(900.0, ideal_interval)) * random.uniform(0.6, 1.4)
            next_at = now_ts + datetime.timedelta(seconds=interval_seconds)

            cur.execute(
                """UPDATE public_counter_reveal SET revealed_today = %s, last_reveal_at = %s, next_reveal_at = %s
                   WHERE reveal_date = %s""",
                (revealed, now_ts, next_at, today_date),
            )
            conn.commit()

        cur.close()
        conn.close()
        return {"today": revealed, "week": real["week"], "month": real["month"]}
    except Exception as e:
        logger.error(f"[Public Counters] reveal-throttle error, falling back to real counts: {e}")
        return real


_BARE_OUTCODE_RE = re.compile(r'\b([A-Z]{1,2}[0-9][A-Z0-9]?)\b')


def _extract_outcodes_lenient(address: str) -> list:
    """Sep 8 2026: Nick flagged the radar radius classification showing
    "null leads in areas where there are definitely leads". Root cause --
    classify_leads_by_radius (below) was using _extract_outcodes, which only
    matches a FULL postcode (outcode + incode, e.g. "CR5 2AB") in the
    address text. Plenty of real scraped addresses only carry the bare
    outcode ("...Croydon CR5...") with no incode -- exactly what the proven
    `address ~* '\\yCR[0-9]'` SQL matching elsewhere in this file already
    relies on -- so a real, already-matched lead could still fail to
    extract an outcode here and get silently dropped from both radius
    buckets. Tries the strict full-postcode match first (most precise when
    present), falls back to a bare standalone-outcode token otherwise."""
    full = _extract_outcodes(address)
    if full:
        return full
    return _BARE_OUTCODE_RE.findall((address or "").upper())


def classify_leads_by_radius(pool_addresses: list, target_lat: Optional[float], target_lng: Optional[float],
                              radius_miles: float, max_fresh_lookups: int = 20, conn=None) -> dict:
    """Sep 8 2026, Nick's ask: the radar's "X Active Leads in radius" figure
    was matching leads against the EXACT outcode typed (e.g. "CR6" only) --
    it never actually used the radius dropdown or the map circle at all, so
    a real nearby lead at CR5 or CR8 never counted as "in radius" for a CR6
    search, even though the same leads correctly showed up in "connected
    zones" and the notices table right next to it. This does a genuine
    haversine-distance check against each lead's outcode centroid (same
    proven approach as find_nearest_unclaimed_lead / dispatch_lead_alerts).

    Sep 8 2026 (same day, follow-up): Nick reported this as "very slow to
    update" -- a wide postcode-letter pool (e.g. all of "B" for Birmingham)
    can span dozens of distinct outcodes, and the first version looked each
    one up one-by-one, even cache hits. Now resolves every distinct outcode
    in ONE batched cache read, and only falls back to live postcodes.io
    calls (capped at `max_fresh_lookups` per request) for outcodes that
    have genuinely never been seen before -- those get cached permanently
    by get_outcode_area_label, so it's only ever slow once per outcode,
    ever, not once per request.

    `pool_addresses` is a list of address strings (or single-item tuples --
    either is accepted) already filtered to the wider postcode-letter area.
    Returns {"in_radius": int, "nearby": int} -- leads whose outcode can't
    be resolved to a real coordinate are excluded from both counts, never
    guessed into either bucket.

    Sep 9 2026, Nick's ask: the lag "between selecting a new pin and the
    info being updated" was still too long. This function's own batched
    query was already fast, but the endpoint that calls it (a map click in
    api_check_postcode) was opening a SEPARATE fresh Postgres connection for
    this, plus another for is_territory_claimed, plus the one it already
    held open for its own queries -- 3+ connections opened back-to-back for
    one click. Added an optional `conn` so a caller that already has a
    connection open (as api_check_postcode does) can hand it in and reuse
    it instead of opening a new one; callers that don't pass one keep the
    old self-contained behaviour exactly as before.

    Sep 9 2026 (same day, follow-up), CRITICAL fix, Nick's ask ("its been
    going on a long time now im concerned we are not making progress"): a
    25-mile search on IG3 was showing 10 in-radius leads and genuinely 0
    "nearby", despite real leads sitting right next door in IG8/Epping
    Forest and visible in the notices panel the whole time. Root cause was
    upstream in api_check_postcode: `pool_addresses` was still restricted to
    leads sharing the search postcode's own 2-letter area prefix before this
    function ever ran, so a neighbouring-but-differently-lettered outcode
    (IG3 vs RM/E/CM, say) could never reach the haversine check at all. The
    caller now passes an unrestricted, nationwide pool instead. That makes
    this function's own "nearby" bucket the only thing standing between a
    sane "connected zones" count and an accidental nationwide one, since
    without a ceiling here a Glasgow lead would now count as "nearby" to a
    London search. So "nearby" is now capped to leads within radius_miles +
    20 -- genuinely just outside the selected radius -- and anything further
    out is excluded from both buckets, same as an unresolvable outcode."""
    in_radius = 0
    nearby = 0
    if target_lat is None or target_lng is None:
        return {"in_radius": 0, "nearby": 0}

    lead_outcodes = []
    distinct = set()
    for row in pool_addresses:
        address = row[0] if isinstance(row, (tuple, list)) else row
        outcodes = _extract_outcodes_lenient(address)
        oc = outcodes[0] if outcodes else None
        lead_outcodes.append(oc)
        if oc:
            distinct.add(oc)

    coords = {}
    if distinct:
        owns_conn = conn is None
        try:
            if owns_conn:
                conn = get_db_conn()
            cur = conn.cursor()
            cur.execute("SELECT outcode, lat, lon FROM outcode_area_cache WHERE outcode = ANY(%s)", (list(distinct),))
            for oc, lat, lon in cur.fetchall():
                if lat is not None and lon is not None:
                    coords[oc] = (lat, lon)
            cur.close()
            if owns_conn:
                conn.close()
        except Exception as e:
            logger.debug(f"[Radar Radius] batch cache read failed: {e}")

        missing = [oc for oc in distinct if oc not in coords]
        for oc in missing[:max_fresh_lookups]:
            area = get_outcode_area_label(oc)  # also writes through to outcode_area_cache
            if area.get("lat") is not None and area.get("lon") is not None:
                coords[oc] = (area["lat"], area["lon"])

    for oc in lead_outcodes:
        if not oc or oc not in coords:
            continue
        lead_lat, lead_lon = coords[oc]
        dist = haversine_miles(target_lat, target_lng, lead_lat, lead_lon)
        if dist <= radius_miles:
            in_radius += 1
        elif dist <= radius_miles + 20:
            nearby += 1
        # else: genuinely too far away to call "nearby" -- excluded from
        # both buckets, same treatment as a lead whose outcode can't be
        # resolved at all.
    return {"in_radius": in_radius, "nearby": nearby}


def _size_mix_select(pool: list, limit: int) -> list:
    """Best-effort small/medium/large variety from an already-filtered pool
    (used for the area-scoped radar notices, where the pool is inherently
    local so a geographic mix doesn't apply). Never invents an entry --
    just orders the real pool to avoid 5 leads of the same size in a row
    when a mix is available."""
    targets = {"small": 2, "medium": 2, "large": 1}
    selected, used = [], set()
    for size_name in list(targets.keys()):
        while targets[size_name] > 0:
            cand = next((l for l in pool if l["reference"] not in used and l["lead_score"] == size_name), None)
            if not cand:
                break
            selected.append(cand)
            used.add(cand["reference"])
            targets[size_name] -= 1
    while len(selected) < limit:
        cand = next((l for l in pool if l["reference"] not in used), None)
        if not cand:
            break
        selected.append(cand)
        used.add(cand["reference"])
    import datetime
    selected.sort(key=lambda l: l["discovered_at"] or datetime.datetime.min, reverse=True)
    return selected[:limit]


def select_diverse_ticker_leads(limit: int = 5, enforce_geo_mix: bool = True,
                                 pool_rows: list = None, pool_limit: int = 150,
                                 max_fresh_lookups: int = 20) -> list:
    """Sep 8 2026, Nick's ask: the live feed used to just show the last 5
    leads discovered, full stop -- which meant a busy scan of one council
    could fill the whole ticker with near-identical entries from a single
    low-demand area, making the site look far narrower than it is. This
    picks a genuinely diverse handful from the real, currently-open lead
    pool: a preferred small/medium/large size mix always, and (for the
    sitewide ticker only -- enforce_geo_mix=True) a preferred 2 London
    borough / 1 Birmingham / 2 other-large-area geographic mix. Every lead
    returned is real; if the pool can't fill a target slot (e.g. no
    Birmingham lead is currently open), that slot is filled with the next
    best real lead instead of being left empty or faked.

    Pass pool_rows (list of the raw DB tuples: address, summary, lead_score,
    lead_price, council_source, reference, discovered_at) to select from an
    already-fetched, already-filtered set (e.g. leads within a radar search
    radius) instead of the sitewide pool."""
    if pool_rows is None:
        try:
            conn = get_db_conn()
            cur = conn.cursor()
            cur.execute("""SELECT address, summary, lead_score, lead_price, council_source, reference, discovered_at
                           FROM leads WHERE (status = 'new' OR status IS NULL)
                           ORDER BY discovered_at DESC LIMIT %s""", (pool_limit,))
            pool_rows = cur.fetchall()
            cur.close()
            conn.close()
        except Exception as e:
            logger.error(f"[Ticker] Error fetching lead pool: {e}")
            return []

    # Sep 8 2026: Nick flagged the homepage as "taking so long to load".
    # Root cause -- this loop used to call get_outcode_area_label() once per
    # lead in the pool (up to pool_limit=150 leads, on every single
    # pageview), and that function opens its own fresh Postgres connection
    # per call (two, on a cache miss) plus a live postcodes.io HTTP call on
    # any cache miss. That's up to 150 fresh DB connections opened
    # sequentially just to render the homepage -- a classic N+1, and almost
    # certainly the actual cause of the slow load (not image size or the
    # Leaflet script tags, which are comparatively minor). Same fix as the
    # one already applied to classify_leads_by_radius today: pull every
    # distinct outcode once, batch-read their cache rows in a single query,
    # and only fall back to a live lookup for a capped number of genuine
    # cache misses.
    parsed_rows = []
    distinct_outcodes = set()
    for address, summary, lead_score, lead_price, council_source, reference, discovered_at in pool_rows:
        outcodes = _extract_outcodes(address)
        outcode = outcodes[0] if outcodes else None
        parsed_rows.append((address, summary, lead_score, lead_price, council_source, reference, discovered_at, outcode))
        if outcode:
            distinct_outcodes.add(outcode)

    area_by_outcode = {}
    if distinct_outcodes:
        try:
            conn = get_db_conn()
            cur = conn.cursor()
            cur.execute("SELECT outcode, district, is_london, lat, lon FROM outcode_area_cache WHERE outcode = ANY(%s)",
                        (list(distinct_outcodes),))
            for oc, district, is_london, lat, lon in cur.fetchall():
                label = f"{oc}, {district}, London" if (district and is_london) else (f"{oc}, {district}" if district else oc)
                area_by_outcode[oc] = {"district": district, "is_london": bool(is_london), "label": label,
                                        "lat": lat, "lon": lon}
            cur.close()
            conn.close()
        except Exception as e:
            logger.debug(f"[Ticker] batch area-cache read failed: {e}")

        missing = [oc for oc in distinct_outcodes if oc not in area_by_outcode]
        for oc in missing[:max_fresh_lookups]:
            area_by_outcode[oc] = get_outcode_area_label(oc)  # live lookup; also writes through to the cache

    pool = []
    for address, summary, lead_score, lead_price, council_source, reference, discovered_at, outcode in parsed_rows:
        area = area_by_outcode.get(outcode) or {"district": None, "is_london": False, "label": (outcode or "UK")}
        pool.append({
            "address": address, "summary": summary, "lead_score": (lead_score or "small"),
            "lead_price": lead_price, "council_source": council_source, "reference": reference,
            "discovered_at": discovered_at, "outcode": outcode or "UK",
            "district": area["district"], "is_london": area["is_london"], "area_label": area["label"],
        })

    if not pool:
        return []

    if not enforce_geo_mix:
        return _size_mix_select(pool, limit)

    size_targets = {"small": 2, "medium": 2, "large": 1}
    geo_defs = [
        ("london", lambda l: l["is_london"], 2),
        ("birmingham", lambda l: (l["district"] or "").strip().lower() == "birmingham", 1),
        ("other", lambda l: not l["is_london"] and (l["district"] or "").strip().lower() != "birmingham", 2),
    ]
    selected, used = [], set()
    remaining_size = dict(size_targets)
    remaining_geo = {name: count for name, _, count in geo_defs}

    # Pass 1: a lead that satisfies both a still-open geo slot AND a
    # still-open size slot -- the ideal case.
    for geo_name, geo_fn, _ in geo_defs:
        while remaining_geo[geo_name] > 0:
            cand = next((l for l in pool if l["reference"] not in used
                         and geo_fn(l) and remaining_size.get(l["lead_score"], 0) > 0), None)
            if not cand:
                break
            selected.append(cand)
            used.add(cand["reference"])
            remaining_size[cand["lead_score"]] -= 1
            remaining_geo[geo_name] -= 1

    # Pass 2: fill any still-open geo slots regardless of size.
    for geo_name, geo_fn, _ in geo_defs:
        while remaining_geo[geo_name] > 0:
            cand = next((l for l in pool if l["reference"] not in used and geo_fn(l)), None)
            if not cand:
                break
            selected.append(cand)
            used.add(cand["reference"])
            if remaining_size.get(cand["lead_score"], 0) > 0:
                remaining_size[cand["lead_score"]] -= 1
            remaining_geo[geo_name] -= 1

    # Pass 3: top up to `limit` with whatever real leads are left, preferring
    # still-needed sizes, so a thin pool (e.g. nothing from Birmingham today)
    # still returns a full, honest selection instead of coming up short.
    while len(selected) < limit:
        cand = next((l for l in pool if l["reference"] not in used
                     and remaining_size.get(l["lead_score"], 0) > 0), None)
        if not cand:
            cand = next((l for l in pool if l["reference"] not in used), None)
        if not cand:
            break
        selected.append(cand)
        used.add(cand["reference"])
        if remaining_size.get(cand["lead_score"], 0) > 0:
            remaining_size[cand["lead_score"]] -= 1

    import datetime
    selected.sort(key=lambda l: l["discovered_at"] or datetime.datetime.min, reverse=True)
    return selected[:limit]


def find_nearest_unclaimed_lead(lat: float, lon: float, max_miles: float = 25.0, exclude_refs: list = None) -> Optional[dict]:
    """Sep 5 2026, free-signup feature: given a signup's lat/lon, finds the
    single closest still-available ('new') lead, using the same outcode-
    extraction + postcodes.io-centroid + haversine approach already proven
    in notifications.dispatch_lead_alerts -- just run once, ad hoc, for one
    person, instead of against a whole batch of freshly-scraped leads.
    Bounded to the 60 most recently discovered unclaimed leads so this
    doesn't hammer postcodes.io on every signup."""
    if not SURL or lat is None or lon is None:
        return None
    exclude_refs = set(exclude_refs or [])
    try:
        conn = get_db_conn()
        cur = conn.cursor()
        try:
            cur.execute("""
                SELECT id, reference, address, summary, council_source, lead_score, lead_price,
                       registered_date, vertical, applicant_name, agent_name, agent_company, has_agent
                FROM leads
                WHERE (status = 'new' OR status IS NULL)
                ORDER BY discovered_at DESC
                LIMIT 60;
            """)
            rows = cur.fetchall()
        finally:
            cur.close()
            conn.close()

        best = None
        best_dist = None
        for row in rows:
            reference = row[1]
            if reference in exclude_refs:
                continue
            address = row[2]
            for oc in _extract_outcodes(address):
                lead_lat, lead_lon = lookup_outcode_centroid(oc)
                if lead_lat is None or lead_lon is None:
                    continue
                dist = haversine_miles(lat, lon, lead_lat, lead_lon)
                if dist <= max_miles and (best_dist is None or dist < best_dist):
                    best_dist = dist
                    best = {
                        "id": row[0], "reference": row[1], "address": row[2], "summary": row[3],
                        "council_source": row[4], "lead_score": row[5], "lead_price": row[6],
                        "registered_date": row[7], "vertical": row[8], "applicant_name": row[9],
                        "agent_name": row[10], "agent_company": row[11], "has_agent": row[12],
                    }
                break  # first extractable outcode on this address is enough
        return best
    except Exception as e:
        logger.error(f"[Free Signup] Error finding nearest lead for ({lat}, {lon}): {e}")
        return None


def lead_distance_miles(sub_lat: float, sub_lon: float, lead_address: str) -> Optional[float]:
    """Sep 8 2026: honest "X.X miles from you" distance for display (dashboard,
    dispatch emails), reusing the exact same outcode-extraction + centroid +
    haversine approach already proven in dispatch_lead_alerts / find_nearest_
    unclaimed_lead -- not a new way of measuring distance, just exposing the
    number instead of only using it internally for a yes/no radius check.
    Returns None (never a wrong number) if either side can't be resolved."""
    if sub_lat is None or sub_lon is None or not lead_address:
        return None
    for oc in _extract_outcodes(lead_address):
        lead_lat, lead_lon = lookup_outcode_centroid(oc)
        if lead_lat is not None and lead_lon is not None:
            return round(haversine_miles(sub_lat, sub_lon, lead_lat, lead_lon), 1)
    return None


def simulate_customer_leads(location_input: str, tier: str = "climber_domestic", job_size: str = "all",
                             radius: float = None, limit: int = 10) -> dict:
    """Sep 8 2026, Nick's ask: "let's do some dummy runs ... you tell me what
    exactly I would receive by email". A read-only simulation of the real
    dispatch_lead_alerts matching logic (exact outcode / haversine radius /
    regional-prefix fallback) for a hypothetical subscriber at a given
    location, tier and job-size preference -- against real, currently-
    unclaimed leads, but WITHOUT burning, dispatching or emailing anything.
    Safe to run any number of times for testing/demoing what a given
    postcode + package would actually receive.

    Returns {"location": {...resolve_location output...}, "radius_miles": N,
    "matches": [ {reference, address_area, summary, lead_score, distance_miles,
    match_reason}, ... ] } -- address is deliberately reduced to its area
    (same _area_label-style redaction as the public homepage fix) rather
    than the real street address, since this is a sales/ops tool, not a
    reason to expose an unclaimed lead's exact address outside a real sale."""
    location = resolve_location(location_input)
    lat, lon = location["lat"], location["lon"]
    effective_radius = radius if radius is not None else TIER_MAX_RADIUS.get(tier, 15)
    job_size = (job_size or "all").strip().lower()
    if job_size not in ("small", "medium", "large", "all"):
        job_size = "all"

    result = {"location": location, "radius_miles": effective_radius, "tier": tier,
              "job_size_preference": job_size, "matches": []}
    if lat is None or lon is None:
        return result

    if not SURL:
        return result
    try:
        conn = get_db_conn()
        cur = conn.cursor()
        try:
            cur.execute("""
                SELECT reference, address, summary, council_source, lead_score, discovered_at
                FROM leads
                WHERE (status = 'new' OR status IS NULL)
                ORDER BY discovered_at DESC
                LIMIT 300;
            """)
            rows = cur.fetchall()
        finally:
            cur.close()
            conn.close()
    except Exception as e:
        logger.error(f"[Simulate] Error fetching candidate leads: {e}")
        return result

    sub_outcode = location["outcode"]
    matches = []
    for reference, address, summary, council_source, lead_score, discovered_at in rows:
        if job_size != "all" and (lead_score or "small") != job_size:
            continue
        addr_upper = (address or "").upper()
        extracted = _extract_outcodes(addr_upper)
        match_reason = None

        if sub_outcode and (sub_outcode in extracted or re.search(r'\b' + re.escape(sub_outcode) + r'\b', addr_upper)):
            match_reason = "exact_outcode"

        dist = None
        if not match_reason:
            for oc in extracted:
                lead_lat, lead_lon = lookup_outcode_centroid(oc)
                if lead_lat is not None and lead_lon is not None:
                    d = haversine_miles(lat, lon, lead_lat, lead_lon)
                    if dist is None or d < dist:
                        dist = d
                    if d <= effective_radius:
                        match_reason = "within_radius"
                        break

        if not match_reason:
            prefix_m = re.match(r'^([A-Z]{1,2})', sub_outcode or "")
            if prefix_m:
                prefix = prefix_m.group(1)
                for oc in extracted:
                    oc_prefix_m = re.match(r'^([A-Z]{1,2})', oc)
                    if oc_prefix_m and oc_prefix_m.group(1) == prefix:
                        match_reason = "regional_prefix"
                        break

        if not match_reason:
            continue

        if dist is None:
            dist = lead_distance_miles(lat, lon, address)

        matches.append({
            "reference": reference,
            "area": f"{extracted[0]} area" if extracted else (council_source or "UK"),
            "summary": summary,
            "lead_score": lead_score or "small",
            "distance_miles": dist,
            "match_reason": match_reason,
            "discovered_at": discovered_at,
        })
        if len(matches) >= limit:
            break

    matches.sort(key=lambda m: (m["distance_miles"] is None, m["distance_miles"] if m["distance_miles"] is not None else 0))
    result["matches"] = matches
    return result


def create_or_update_limbo_account(email: str, name: str = None, phone: str = None,
                                    outcode: str = None, lat: float = None, lon: float = None) -> Optional[dict]:
    """Sep 5 2026: creates (or refreshes contact details on) a free,
    no-subscription account row. Deliberately never touches free_lead_ref
    or last_teaser_lead_ref on conflict -- a repeat visit to the signup
    form must never re-arm or reset either of those."""
    if not SURL or not email or not outcode:
        return None
    try:
        conn = get_db_conn()
        cur = conn.cursor()
        try:
            cur.execute("""
                INSERT INTO limbo_accounts (email, customer_name, phone, center_outcode, lat, lon)
                VALUES (%s, %s, %s, %s, %s, %s)
                ON CONFLICT (email) DO UPDATE SET
                    customer_name = EXCLUDED.customer_name,
                    phone = EXCLUDED.phone,
                    center_outcode = EXCLUDED.center_outcode,
                    lat = EXCLUDED.lat,
                    lon = EXCLUDED.lon
                RETURNING id, email, customer_name, phone, center_outcode, lat, lon,
                          free_lead_ref, last_teaser_lead_ref, signed_up_at, last_teaser_sent_at, unsubscribed;
            """, (email.strip().lower(), name, phone, outcode.strip().upper(), lat, lon))
            row = cur.fetchone()
            conn.commit()
            if not row:
                return None
            return {
                "id": row[0], "email": row[1], "customer_name": row[2], "phone": row[3],
                "center_outcode": row[4], "lat": row[5], "lon": row[6], "free_lead_ref": row[7],
                "last_teaser_lead_ref": row[8], "signed_up_at": row[9], "last_teaser_sent_at": row[10],
                "unsubscribed": row[11],
            }
        finally:
            cur.close()
            conn.close()
    except Exception as e:
        logger.error(f"[Free Signup] Error creating/updating limbo account for {email}: {e}")
        return None


def get_limbo_account(email: str) -> Optional[dict]:
    if not SURL or not email:
        return None
    try:
        conn = get_db_conn()
        cur = conn.cursor()
        try:
            cur.execute("""
                SELECT id, email, customer_name, phone, center_outcode, lat, lon,
                       free_lead_ref, last_teaser_lead_ref, signed_up_at, last_teaser_sent_at, unsubscribed
                FROM limbo_accounts WHERE email = %s;
            """, (email.strip().lower(),))
            row = cur.fetchone()
            if not row:
                return None
            return {
                "id": row[0], "email": row[1], "customer_name": row[2], "phone": row[3],
                "center_outcode": row[4], "lat": row[5], "lon": row[6], "free_lead_ref": row[7],
                "last_teaser_lead_ref": row[8], "signed_up_at": row[9], "last_teaser_sent_at": row[10],
                "unsubscribed": row[11],
            }
        finally:
            cur.close()
            conn.close()
    except Exception as e:
        logger.error(f"[Free Signup] Error fetching limbo account for {email}: {e}")
        return None


def record_free_lead_grant(email: str, lead_ref: str) -> bool:
    """Guarded so a double-submit or a retried request can never grant a
    second free lead to the same account -- only writes if free_lead_ref
    is still NULL."""
    if not SURL or not email or not lead_ref:
        return False
    try:
        conn = get_db_conn()
        cur = conn.cursor()
        try:
            cur.execute("""
                UPDATE limbo_accounts SET free_lead_ref = %s
                WHERE email = %s AND free_lead_ref IS NULL
                RETURNING id;
            """, (lead_ref, email.strip().lower()))
            row = cur.fetchone()
            conn.commit()
            return row is not None
        finally:
            cur.close()
            conn.close()
    except Exception as e:
        logger.error(f"[Free Signup] Error recording free lead grant for {email}: {e}")
        return False


def get_limbo_accounts_due_for_teaser(min_hours_since_last: float = 72.0) -> list:
    """Sep 5 2026: the weekly/twice-weekly teaser-email cohort. Excludes
    anyone who has since become a real paying subscriber (checked live
    against contractor_subscriptions.active rather than a stored flag on
    this table, so it can never drift out of sync with a Stripe webhook)
    and anyone who unsubscribed."""
    if not SURL:
        return []
    try:
        conn = get_db_conn()
        cur = conn.cursor()
        try:
            cur.execute("""
                SELECT la.id, la.email, la.customer_name, la.center_outcode, la.lat, la.lon,
                       la.free_lead_ref, la.last_teaser_lead_ref
                FROM limbo_accounts la
                LEFT JOIN contractor_subscriptions cs ON cs.customer_email = la.email AND cs.active = TRUE
                WHERE la.unsubscribed = FALSE
                  AND cs.id IS NULL
                  AND (la.last_teaser_sent_at IS NULL OR la.last_teaser_sent_at < NOW() - (%s || ' hours')::interval);
            """, (min_hours_since_last,))
            rows = cur.fetchall()
            return [{
                "id": r[0], "email": r[1], "customer_name": r[2], "center_outcode": r[3],
                "lat": r[4], "lon": r[5], "free_lead_ref": r[6], "last_teaser_lead_ref": r[7],
            } for r in rows]
        finally:
            cur.close()
            conn.close()
    except Exception as e:
        logger.error(f"[Free Signup] Error fetching teaser cohort: {e}")
        return []


def set_limbo_account_unsubscribed(email: str) -> bool:
    if not SURL or not email:
        return False
    try:
        conn = get_db_conn()
        cur = conn.cursor()
        try:
            cur.execute("UPDATE limbo_accounts SET unsubscribed = TRUE WHERE email = %s RETURNING id;", (email.strip().lower(),))
            row = cur.fetchone()
            conn.commit()
            return row is not None
        finally:
            cur.close()
            conn.close()
    except Exception as e:
        logger.error(f"[Free Signup] Error unsubscribing {email}: {e}")
        return False


def mark_teaser_sent(email: str, lead_ref: str = None) -> None:
    if not SURL or not email:
        return
    try:
        conn = get_db_conn()
        cur = conn.cursor()
        try:
            cur.execute("""
                UPDATE limbo_accounts SET last_teaser_sent_at = NOW(), last_teaser_lead_ref = COALESCE(%s, last_teaser_lead_ref)
                WHERE email = %s;
            """, (lead_ref, email.strip().lower()))
            conn.commit()
        finally:
            cur.close()
            conn.close()
    except Exception as e:
        logger.error(f"[Free Signup] Error marking teaser sent for {email}: {e}")


def get_lead_by_reference(reference: str) -> Optional[dict]:
    """Sep 5 2026, free-signup feature: read-only lookup (no status change)
    for re-displaying a lead someone was already granted -- e.g. a free
    signup returning to /free-dashboard, or re-submitting the signup form
    a second time. Same field shape as burn_lead_inventory's return so
    callers can treat both interchangeably."""
    if not SURL or not reference:
        return None
    try:
        conn = get_db_conn()
        cur = conn.cursor()
        try:
            cur.execute("""
                SELECT id, reference, address, summary, council_source, lead_score, lead_price,
                       applicant_name, agent_name, agent_company, has_agent
                FROM leads WHERE reference = %s;
            """, (reference,))
            row = cur.fetchone()
            if not row:
                return None
            return {
                "id": row[0], "reference": row[1], "address": row[2], "summary": row[3],
                "council_source": row[4], "lead_score": row[5], "lead_price": row[6],
                "applicant_name": row[7], "agent_name": row[8], "agent_company": row[9], "has_agent": row[10],
            }
        finally:
            cur.close()
            conn.close()
    except Exception as e:
        logger.error(f"[Free Signup] Error looking up lead {reference}: {e}")
        return None


def register_or_update_subscription(customer_email: str, outcode: str, tier: str = "climber_domestic",
                                     stripe_sub_id: str = None, radius: int = 15, name: str = None, phone: str = None,
                                     job_size_preference: str = "all") -> bool:
    """Registers or updates a contractor subscription with seniority timestamp, lat/lon pin, and tier quota.

    Sep 8 2026: `outcode` here is actually "whatever the customer typed" --
    resolve_location works out whether that's a full postcode ("CR5 2LE",
    giving an exact pin) or just a bare outcode ("CR5", the pre-existing
    centroid-only behaviour) and geocodes accordingly. center_outcode is
    always stored as the plain outcode either way, since territory_claims
    and dispatch_lead_alerts' prefix-matching are keyed on outcode -- this
    only makes the stored lat/lon more precise, it doesn't change what
    "your territory" means."""
    if not SURL or not customer_email or not outcode:
        return False

    location = resolve_location(outcode)
    resolved_outcode = location["outcode"] or outcode.strip().upper()[:4]
    lat, lon = location["lat"], location["lon"]
    full_postcode = location["full_postcode"]

    job_size_preference = (job_size_preference or "all").strip().lower()
    if job_size_preference not in ("small", "medium", "large", "all"):
        job_size_preference = "all"

    # Set realistic monthly quota based on tier
    quota = TIER_QUOTAS.get(tier, 5)

    try:
        conn = get_db_conn()
        cur = conn.cursor()
        try:
            cur.execute("""
                INSERT INTO contractor_subscriptions (
                    customer_email, customer_name, phone, tier, center_outcode, radius_miles,
                    stripe_subscription_id, active, subscribed_at, monthly_quota, lat, lon,
                    full_postcode, job_size_preference
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, TRUE, NOW(), %s, %s, %s, %s, %s)
                ON CONFLICT (customer_email) DO UPDATE SET
                    tier = EXCLUDED.tier,
                    center_outcode = EXCLUDED.center_outcode,
                    radius_miles = EXCLUDED.radius_miles,
                    stripe_subscription_id = EXCLUDED.stripe_subscription_id,
                    monthly_quota = EXCLUDED.monthly_quota,
                    lat = EXCLUDED.lat,
                    lon = EXCLUDED.lon,
                    full_postcode = EXCLUDED.full_postcode,
                    job_size_preference = EXCLUDED.job_size_preference,
                    active = TRUE
                RETURNING id;
            """, (customer_email.strip().lower(), name, phone, tier,
                  resolved_outcode, radius, stripe_sub_id, quota, lat, lon,
                  full_postcode, job_size_preference))
            row = cur.fetchone()
            conn.commit()
            logger.info(f"[Subscription] Registered {customer_email} — {tier} | {resolved_outcode}"
                        f"{' (' + full_postcode + ')' if full_postcode else ''} ±{radius}mi | "
                        f"job_size={job_size_preference} | quota={quota} | coords=({lat},{lon})")
            return bool(row)
        finally:
            cur.close()
            conn.close()
    except Exception as e:
        logger.error(f"[Subscription] Error registering subscription for {customer_email}: {e}")
        return False


def update_notification_preference(email: str, preference: str) -> bool:
    """
    Sets the contractor's preferred lead-notification format. Valid values:
    'email' (default), 'whatsapp', 'both'. Anything else is rejected rather than
    silently stored, since this drives what gets rendered into the dispatch email.
    """
    if not SURL or not email:
        return False
    preference = (preference or "").strip().lower()
    if preference not in ("email", "whatsapp", "both"):
        return False
    try:
        conn = get_db_conn()
        cur = conn.cursor()
        try:
            cur.execute("""
                UPDATE contractor_subscriptions
                SET notification_preference = %s
                WHERE customer_email = %s
                RETURNING id;
            """, (preference, email.strip().lower()))
            row = cur.fetchone()
            conn.commit()
            return bool(row)
        finally:
            cur.close()
            conn.close()
    except Exception as e:
        logger.error(f"[Settings] Error updating notification preference for {email}: {e}")
        return False


def get_contractor_settings(email: str) -> dict:
    """Returns the contractor's current settings (currently just notification_preference)."""
    if not SURL or not email:
        return {"notification_preference": "email"}
    try:
        conn = get_db_conn()
        cur = conn.cursor()
        try:
            cur.execute("""
                SELECT notification_preference FROM contractor_subscriptions WHERE customer_email = %s
            """, (email.strip().lower(),))
            row = cur.fetchone()
            return {"notification_preference": (row[0] if row and row[0] else "email")}
        finally:
            cur.close()
            conn.close()
    except Exception as e:
        logger.error(f"[Settings] Error fetching settings for {email}: {e}")
        return {"notification_preference": "email"}


def update_subscription_tier_by_stripe_id(stripe_sub_id: str, new_tier: str) -> bool:
    """
    Updates tier + monthly_quota for an existing subscription (a Stripe Billing Portal
    plan upgrade/downgrade), keyed by Stripe subscription ID. Does not touch
    outcode/radius/lat/lon since those don't change on a plan switch.
    """
    if not SURL or not stripe_sub_id or not new_tier:
        return False
    quota = TIER_QUOTAS.get(new_tier, 5)
    try:
        conn = get_db_conn()
        cur = conn.cursor()
        try:
            cur.execute("""
                UPDATE contractor_subscriptions
                SET tier = %s, monthly_quota = %s
                WHERE stripe_subscription_id = %s
                RETURNING customer_email;
            """, (new_tier, quota, stripe_sub_id))
            row = cur.fetchone()
            conn.commit()
            if row:
                logger.info(f"[Subscription] Tier updated to {new_tier} (quota={quota}) for stripe_sub_id={stripe_sub_id}")
                return True
            return False
        finally:
            cur.close()
            conn.close()
    except Exception as e:
        logger.error(f"[Subscription] Error updating tier for stripe_sub_id {stripe_sub_id}: {e}")
        return False


def get_active_subscribers_by_seniority(outcode: str = None) -> list:
    """
    Returns active subscribers sorted strictly by Seniority (subscribed_at ASC).
    Ensures long-term subscribers receive first priority allocation.
    Includes lat/lon for geographic radius matching.
    """
    if not SURL:
        return []
    try:
        conn = get_db_conn()
        cur = conn.cursor()
        try:
            base_sql = """
                SELECT id, customer_email, customer_name, phone, tier, center_outcode, radius_miles,
                       monthly_quota, delivered_this_month, subscribed_at, lat, lon, notification_preference,
                       full_postcode, job_size_preference
                FROM contractor_subscriptions
                WHERE active = TRUE
            """
            if outcode:
                cur.execute(base_sql + " AND center_outcode = %s ORDER BY subscribed_at ASC;",
                            (outcode.strip().upper(),))
            else:
                cur.execute(base_sql + " ORDER BY subscribed_at ASC;")
            cols = ["id", "email", "name", "phone", "tier", "outcode", "radius", "quota", "delivered", "subscribed_at", "lat", "lon", "notification_preference",
                    "full_postcode", "job_size_preference"]
            rows = [dict(zip(cols, r)) for r in cur.fetchall()]
            # Higher-tier subscribers get first look (sold as "priority routing"); within
            # the same tier band, longest-tenured subscriber still wins (stable sort).
            rows.sort(key=lambda r: (-TIER_PRIORITY.get(r["tier"], 1), r["subscribed_at"]))
            return rows
        finally:
            cur.close()
            conn.close()
    except Exception as e:
        logger.error(f"[Subscription] Error fetching subscribers by seniority: {e}")
        return []


def get_contractor_subscription(email: str) -> dict:
    """Returns the contractor subscription record for this email, or empty dict. Used for login validation."""
    if not SURL or not email:
        return {}
    try:
        conn = get_db_conn()
        cur = conn.cursor()
        try:
            cur.execute("""
                SELECT id, tier, center_outcode, radius_miles, active, monthly_quota, delivered_this_month
                FROM contractor_subscriptions WHERE customer_email = %s
            """, (email.strip().lower(),))
            row = cur.fetchone()
            if row:
                cols = ["id", "tier", "outcode", "radius", "active", "quota", "delivered"]
                return dict(zip(cols, row))
            return {}
        finally:
            cur.close()
            conn.close()
    except Exception as e:
        logger.error(f"[Subscription] Error fetching sub for {email}: {e}")
        return {}


def record_lead_dispatch_and_burn(lead_id: str, sub_id: str, contractor_email: str, dispatch_type: str = "standard") -> bool:
    """
    Atomically dispatches a lead to a subscriber, logs the dispatch audit record,
    increments contractor delivery count, and burns the lead permanently.
    """
    if not SURL or not lead_id or not contractor_email:
        return False
    try:
        conn = get_db_conn()
        cur = conn.cursor()
        try:
            # 1. Burn the lead
            cur.execute("""
                UPDATE leads
                SET status = 'claimed'
                WHERE (id::text = %s OR reference = %s) AND (status = 'new' OR status IS NULL)
                RETURNING id;
            """, (lead_id, lead_id))
            burned_lead = cur.fetchone()
            if not burned_lead:
                conn.rollback()
                return False  # Already burned or claimed by someone else

            real_lead_uuid = burned_lead[0]

            # 2. Record dispatch audit log
            cur.execute("""
                INSERT INTO lead_dispatches (lead_id, contractor_id, contractor_email, dispatch_type)
                VALUES (%s, %s, %s, %s);
            """, (real_lead_uuid, sub_id, contractor_email, dispatch_type))

            # 3. Atomically increment monthly delivery count. The WHERE guard is what
            # actually enforces the quota (the caller's in-memory check can go stale
            # within a single batch, or race against a second concurrent dispatch run) —
            # this UPDATE is the only place quota is really enforced.
            if sub_id:
                cur.execute("""
                    UPDATE contractor_subscriptions
                    SET delivered_this_month = delivered_this_month + 1,
                        last_dispatched_at = NOW()
                    WHERE id = %s AND delivered_this_month < monthly_quota
                    RETURNING delivered_this_month;
                """, (sub_id,))
                if not cur.fetchone():
                    # Quota was already hit (possibly by a concurrent dispatch run) —
                    # release the lead instead of burning it against a subscriber who
                    # can't legally receive it, so the caller falls through to the next
                    # matching subscriber.
                    conn.rollback()
                    logger.warning(f"[Seniority Router] Quota hit for sub {sub_id} — releasing lead {lead_id} back to pool.")
                    return False

            conn.commit()
            logger.info(f"[Seniority Router] Dispatched & burned lead {lead_id} -> {contractor_email} ({dispatch_type})")
            return True
        finally:
            cur.close()
            conn.close()
    except Exception as e:
        logger.error(f"[Seniority Router] Error dispatching lead {lead_id}: {e}")
        return False


def get_closest_unallocated_leads(outcode: str, limit: int = 5) -> list:
    """
    Finds nearest unallocated leads for overflow compensation.
    First tries to match the same regional outcode prefix (e.g. 'EX' for Exeter area),
    then falls back to most recent nationwide unallocated leads if none found nearby.
    """
    if not SURL or not outcode:
        return []
    try:
        conn = get_db_conn()
        cur = conn.cursor()
        try:
            # Extract alphabetic prefix (e.g. 'EX' from 'EX4', 'NG' from 'NG22')
            import re
            prefix_match = re.match(r'^([A-Z]{1,2})', outcode.strip().upper())
            prefix = prefix_match.group(1) if prefix_match else outcode[:2]

            # Try regional match first
            cur.execute("""
                SELECT id, reference, address, summary, council_source, lead_score, lead_price
                FROM leads
                WHERE (status = 'new' OR status IS NULL)
                  AND (reference ILIKE %s OR address ILIKE %s OR council_source ILIKE %s)
                ORDER BY discovered_at DESC
                LIMIT %s;
            """, (f"%{prefix}%", f"%{prefix}%", f"%{prefix}%", limit))
            rows = cur.fetchall()

            # Fall back to newest nationwide if no regional matches
            if not rows:
                cur.execute("""
                    SELECT id, reference, address, summary, council_source, lead_score, lead_price
                    FROM leads
                    WHERE (status = 'new' OR status IS NULL)
                    ORDER BY discovered_at DESC
                    LIMIT %s;
                """, (limit,))
                rows = cur.fetchall()

            cols = ["id", "ref", "addr", "summary", "council", "score", "price"]
            return [dict(zip(cols, r)) for r in rows]
        finally:
            cur.close()
            conn.close()
    except Exception as e:
        logger.error(f"[Seniority Router] Error fetching fallback leads: {e}")
        return []


def calculate_lead_freshness(discovered_at, planning_status: str = "pending", summary: str = "", source_type: str = "council_planning", registered_date=None) -> dict:
    """
    Calculates statutory lead freshness, countdown timer, color badge, and dynamic decay price:
    - Flash Hot (Day 0-3): £29 unlock (0 competitors aware)
    - Active Quoting (Day 4-14): £19 unlock (Prime window)
    - Clearance / Late Window (Day 15-30): £9 unlock (Consultation closing)
    - Granted / Approved: £25 unlock (Permitted felling ready to start)

    registered_date (Sep 3 2026, Nick's explicit ask): the real date the
    application was filed with the council, when a scan source provided one
    -- takes priority over discovered_at (when TreeKey's own scraper found
    it) for starting the countdown, since the two can differ by however
    long a scan cycle lagged. Only mesh/Idox, PlanIt, and ukplanningapi.co.uk
    leads carry this so far (see _insert_lead's docstring in scanners.py);
    every other source leaves it None and this falls back to discovered_at
    exactly as before this parameter existed.
    """
    import datetime
    now = datetime.datetime.now(datetime.timezone.utc)
    
    if planning_status and planning_status.lower() in ["granted", "approved"]:
        return {
            "tier": "granted",
            "badge_color": "#059669",
            "badge_bg": "#ecfdf5",
            "badge_text": "✅ Officially Approved (Ready to Fell)",
            "price": 25,
            "days_left": "Approved by Council",
            "plan_key": "single_lead_medium"
        }
    days_old = 0

    # Sep 3 2026: prefer the real filed date over discovered_at whenever a
    # source actually gave us one -- see this function's docstring. A DATE
    # column comes back from psycopg2 as a plain datetime.date (no time
    # component), which is why that case is handled separately from the
    # full-timestamp cases below rather than falling into the
    # hasattr(..., "timestamp") branch (datetime.date has no .timestamp()).
    clock_start = registered_date if registered_date else discovered_at

    if clock_start:
        if isinstance(clock_start, str):
            try:
                dt = datetime.datetime.fromisoformat(clock_start.replace("Z", "+00:00"))
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=datetime.timezone.utc)
                days_old = (now - dt).days
            except Exception:
                days_old = 0
        elif isinstance(clock_start, datetime.date) and not isinstance(clock_start, datetime.datetime):
            days_old = (now.date() - clock_start).days
        elif hasattr(clock_start, "timestamp"):
            if clock_start.tzinfo is None:
                clock_start = clock_start.replace(tzinfo=datetime.timezone.utc)
            days_old = (now - clock_start).days

    # 1. Domestic Jobs: Strict 7-Day Expiration Guardrail
    if source_type in ("direct_homeowner", "domestic_classified"):
        days_left = max(0, 7 - days_old)
        if days_old > 7:
            return {
                "tier": "expired",
                "badge_color": "#64748b",
                "badge_bg": "#f1f5f9",
                "badge_text": "🛑 Expired Domestic Job",
                "price": 0,
                "days_left": "Expired (>7 days old)",
                "plan_key": "expired"
            }
        elif days_old <= 2:
            return {
                "tier": "flash_hot",
                "badge_color": "#059669",
                "badge_bg": "#ecfdf5",
                "badge_text": "🔥 Fresh Homeowner Quote (Urgent: Day 0–2)",
                "price": 35,
                "days_left": f"{days_left} days left before quote closes",
                "plan_key": "single_lead_medium"
            }
        else:
            return {
                "tier": "active",
                "badge_color": "#d97706",
                "badge_bg": "#fffbeb",
                "badge_text": "⚡ Active Homeowner Quote (Day 3–7)",
                "price": 25,
                "days_left": f"{days_left} days left before quote closes",
                "plan_key": "single_lead_small"
            }


    # 2. Council Statutory Planning Notices - multi-tier time decay
    is_tpo = "tpo" in (summary or "").lower() or "preservation" in (summary or "").lower()
    total_window = 56 if is_tpo else 42
    days_left = max(0, total_window - days_old)

    if days_old > 56:
        return {
            "tier": "expired",
            "badge_color": "#64748b",
            "badge_bg": "#f1f5f9",
            "badge_text": "🛑 Expired Statutory Notice (>56 Days)",
            "price": 0,
            "days_left": "Expired (>56 days)",
            "plan_key": "expired"
        }

    if days_old <= 3:
        return {
            "tier": "flash_hot",
            "badge_color": "#dc2626",
            "badge_bg": "#fef2f2",
            "badge_text": "🔥 Flash Hot (Day 0–3 • 0 Competitors Aware)",
            "price": 29,
            "days_left": f"{days_left} days left in consultation",
            "plan_key": "single_lead_medium"
        }
    elif days_old <= 14:
        return {
            "tier": "active",
            "badge_color": "#d97706",
            "badge_bg": "#fffbeb",
            "badge_text": "⚡ Prime Quoting Window (Day 4–14)",
            "price": 19,
            "days_left": f"{days_left} days left in consultation",
            "plan_key": "single_lead_small"
        }
    elif days_old <= 42:
        return {
            "tier": "clearance",
            "badge_color": "#ca8a04",
            "badge_bg": "#fefce8",
            "badge_text": f"⏳ Late Window Clearance (Closing Soon)",
            "price": 9,
            "days_left": f"{days_left} days until determination",
            "plan_key": "single_lead_small"
        }
    else:
        return {
            "tier": "clearance",
            "badge_color": "#64748b",
            "badge_bg": "#f8fafc",
            "badge_text": f"📋 Final Determination (Day 43–56)",
            "price": 9,
            "days_left": f"{days_left} days left",
            "plan_key": "single_lead_small"
        }


# Aug 31 2026: Nick's point -- "some leads are more vital than others...
# surely a fallen tree would need immediate action". Honest caveat baked
# into this design: a genuinely fallen/dangerous tree is legally EXEMPT
# from needing planning permission at all (immediate risk to safety), so
# most of these words never show up in most council-application leads --
# this doesn't create a new category of leads, it just flags the subset
# whose own description already uses danger/urgency language (a
# retrospective consent application, a dangerous-tree notification, etc).
# False positives here just mean an extra badge on an ordinary lead --
# far lower stakes than the has_agent exclusion, so this list is
# deliberately broader/more liberal than that one.
_URGENT_KEYWORDS = (
    "dangerous", "danger", "fallen", "fall down", "collapsed", "collapse",
    "storm damage", "storm-damaged", "wind damage", "risk to public",
    "risk to life", "public safety", "emergency", "urgent", "immediate action",
    "immediate risk", "blocking the road", "blocking highway", "obstructing highway",
    "structurally unsound", "split trunk", "hanging branch", "dead and dangerous",
    "high risk", "unsafe", "hazardous", "subsidence risk",
)


def is_urgent_lead(summary: str) -> bool:
    """Best-effort flag for a lead whose own description already signals
    danger/urgency -- see _URGENT_KEYWORDS comment above for what this is
    and isn't. Used to badge and sort marketplace leads, not to exclude
    anything."""
    if not summary:
        return False
    text = summary.lower()
    return any(kw in text for kw in _URGENT_KEYWORDS)


def _sort_key_discovered_at(lead: dict) -> float:
    """Descending-time sort key (most recent first) for use as the secondary
    key alongside urgency in get_marketplace_leads_with_freshness. Missing
    or unparseable dates sort last within their urgency group rather than
    raising."""
    import datetime
    dt = lead.get("discovered_at")
    if dt is None:
        return 0.0
    try:
        if isinstance(dt, str):
            dt = datetime.datetime.fromisoformat(dt.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=datetime.timezone.utc)
        return -dt.timestamp()
    except Exception:
        return 0.0


def _is_agent_already_handling_the_job(lead: dict) -> bool:
    """Sep 2 2026: extracted out of get_marketplace_leads_with_freshness's
    inline check below (kept verbatim, see its own history for the Aug 30/31
    reasoning) and scoped to the tree vertical only.

    Why: agent_is_tree_surgeon (mesh_scrapers.classify_agent_as_tree_surgeon)
    is a tree-surgeon-NAME classifier -- it has no idea what an HMO
    conversion contractor's name looks like, so for a real HMO agent it
    returns None (indeterminate) almost every time. The original inline
    check treated None the same as "confirmed tree surgeon, still
    excluded" -- correct for tree (deliberately conservative given almost
    the entire pool used to sit unconfirmed), but applied vertical-agnostic
    it would have silently excluded essentially every HMO lead with any
    agent on record at all from the marketplace, the moment HMO leads
    started flowing, looking exactly like "the vertical just isn't
    producing many leads" rather than an obvious bug.

    Every non-tree vertical (hmo today, whatever comes next) is exempt from
    this exclusion until a real vertical-specific "is this job already
    taken" classifier exists -- deliberately the safe-default direction
    (never wrongly excluding a sellable lead) over the risky one (never
    wrongly reselling a taken job), a documented policy choice, not an
    oversight. A missing `vertical` key (a row fetched without it, or from
    before the column existed) defaults to "tree" to match the DB column's
    own DEFAULT and keep every pre-existing call site's behaviour exactly
    as it was before this function existed."""
    if lead.get("vertical", "tree") != "tree":
        return False
    if not lead.get("has_agent"):
        return False
    return lead.get("agent_is_tree_surgeon") is not False


def get_marketplace_leads_with_freshness(filter_tier: str = None, limit: int = 40) -> list:
    """
    Returns unallocated leads enriched with their dynamic statutory freshness calculation.
    Supports filtering by tier ('council', 'domestic', 'flash_hot', 'active', 'clearance', 'granted').
    Enforces strict separation so council planning notices and private domestic leads are never conflated.
    """
    if not SURL:
        return []
    try:
        conn = get_db_conn()
        cur = conn.cursor()
        try:
            # Aug 30 2026: has_agent was captured by the scraper and shown to a
            # buyer only AFTER they'd already paid (the "lead unlocked" email)
            # -- this query, which feeds the public pre-purchase marketplace
            # listing, never selected it at all. Nick flagged this directly:
            # a contractor could pay £19-49 for a lead and only find out from
            # the unlock email that a tree surgeon was already on record for
            # that job. Added here so marketplace_view can show the same
            # honest yes/no/unconfirmed signal before checkout -- without
            # revealing WHICH agent/company (that detail stays part of what
            # unlocking pays for).
            try:
                cur.execute("""
                    SELECT id, reference, address, summary, council_source, lead_score, lead_price,
                           discovered_at, planning_status, registered_date,
                           COALESCE(lead_source_type, 'council_planning') as source_type,
                           has_agent, agent_is_tree_surgeon, COALESCE(vertical, 'tree') as vertical
                    FROM leads
                    WHERE status = 'new' OR status IS NULL
                    ORDER BY discovered_at DESC
                    LIMIT 150;
                """)
                cols = ["id", "ref", "addr", "summary", "council", "score", "base_price", "discovered_at", "status", "reg_date", "source_type", "has_agent", "agent_is_tree_surgeon", "vertical"]
            except Exception as e:
                # Sep 2 2026 (production incident fix): if the `vertical`
                # column's migration hasn't landed yet (see
                # _run_ddl_statements_resiliently's docstring), this SELECT
                # used to raise "column vertical does not exist" straight
                # into the outer except below, which returns [] -- i.e. the
                # ENTIRE public marketplace shows zero leads for every
                # customer, for every vertical, not just HMO. Fall back to
                # the pre-Sep-2 SELECT (no vertical column) and default it to
                # 'tree' in Python instead, so the marketplace keeps working
                # exactly as it did before this column existed.
                if "vertical" not in str(e).lower():
                    raise
                conn.rollback()
                logger.warning(f"[Marketplace] 'vertical' column not available yet ({e}) -- falling back to legacy SELECT without it.")
                cur.execute("""
                    SELECT id, reference, address, summary, council_source, lead_score, lead_price,
                           discovered_at, planning_status, registered_date,
                           COALESCE(lead_source_type, 'council_planning') as source_type,
                           has_agent, agent_is_tree_surgeon
                    FROM leads
                    WHERE status = 'new' OR status IS NULL
                    ORDER BY discovered_at DESC
                    LIMIT 150;
                """)
                cols = ["id", "ref", "addr", "summary", "council", "score", "base_price", "discovered_at", "status", "reg_date", "source_type", "has_agent", "agent_is_tree_surgeon"]
            rows = cur.fetchall()
            raw_leads = [dict(zip(cols, r)) for r in rows]
            for l in raw_leads:
                l.setdefault("vertical", "tree")

            # Sep 9 2026, CRITICAL fix -- Nick caught the marketplace showing
            # the exact street address of unpurchased leads. The template
            # (marketplace_view in main.py) was trying to mask it itself by
            # splitting the raw address text on commas and keeping only the
            # last two segments -- but real scraped council addresses are
            # very often a single unpunctuated string ("14 OAK AVENUE
            # CROYDON CR5 2AB", no commas at all), and for those the split
            # produces just ONE segment, which the old code's fallback
            # (`if len(parts) > 1 else addr`) then showed in full -- house
            # number and street included, exactly what buying the lead is
            # supposed to be for. Fixed at the data layer instead, using the
            # same reliable, already-proven method the public homepage
            # ticker uses: resolve each lead's outcode and show only
            # "OUTCODE, District" (see get_outcode_area_label) -- never the
            # address text itself, regardless of its punctuation. Batched
            # the same way as select_diverse_ticker_leads/
            # classify_leads_by_radius: one query for every distinct outcode
            # instead of one per lead.
            _outcodes_by_lead = []
            _distinct_outcodes = set()
            for l in raw_leads:
                oc_list = _extract_outcodes_lenient(l.get("addr") or "")
                oc = oc_list[0] if oc_list else None
                _outcodes_by_lead.append(oc)
                if oc:
                    _distinct_outcodes.add(oc)

            _area_by_outcode = {}
            if _distinct_outcodes:
                try:
                    cur.execute("SELECT outcode, district, is_london FROM outcode_area_cache WHERE outcode = ANY(%s)",
                                (list(_distinct_outcodes),))
                    for oc, district, is_london in cur.fetchall():
                        label = f"{oc}, {district}, London" if (district and is_london) else (f"{oc}, {district}" if district else oc)
                        _area_by_outcode[oc] = label
                except Exception as e:
                    logger.debug(f"[Marketplace] batch area-cache read failed: {e}")

                _missing = [oc for oc in _distinct_outcodes if oc not in _area_by_outcode]
                for oc in _missing[:20]:
                    _area_by_outcode[oc] = get_outcode_area_label(oc)["label"]

            for l, oc in zip(raw_leads, _outcodes_by_lead):
                l["area_label"] = _area_by_outcode.get(oc, oc or "Area unavailable")

            enriched = []
            for l in raw_leads:
                freshness = calculate_lead_freshness(l["discovered_at"], l["status"], l["summary"], source_type=l.get("source_type", "council_planning"), registered_date=l.get("reg_date"))
                if freshness.get("tier") == "expired":
                    continue
                l.update(freshness)

                # Aug 30 2026: Nick was explicit -- "I can't sell leads to
                # jobs that already have someone signed up for them." A
                # has_agent=True lead means the council record itself already
                # names an agent/contractor, which is a genuine, confirmed
                # signal (not a guess) that this job may already be taken.
                # Showing a warning badge next to it (as of the marketplace
                # fix earlier today) isn't enough on its own if we're still
                # taking someone's money for it -- pull it from sale outright.
                # has_agent is True / False / None; only True is excluded --
                # None ("never checked") still needs a real decision from
                # Nick on how strict to be, since almost the entire current
                # lead pool is in that unconfirmed state, not a confirmed
                # False.
                #
                # Aug 31 2026: Nick's follow-up -- "an agent" on the
                # application isn't always a tree surgeon. Architects,
                # planning consultants, block management companies, and even
                # the council itself all show up as "agent" too, and in those
                # cases the tree work itself is very likely still open even
                # though has_agent is technically True. agent_is_tree_surgeon
                # is a best-effort classification of the agent name/company
                # text (see mesh_scrapers.classify_agent_as_tree_surgeon):
                # only exclude here when it's True or unknown (never
                # classified, or genuinely ambiguous) -- when it's explicitly
                # False (clearly NOT a tree company), keep the lead for sale,
                # since we now have real evidence the job may still be open
                # despite technically having "an agent" on record.
                #
                # Sep 2 2026: scoped to tree only -- see
                # _is_agent_already_handling_the_job's own docstring above
                # for why applying this vertical-agnostic would have
                # silently zeroed out HMO revenue.
                if _is_agent_already_handling_the_job(l):
                    continue

                # Custom source badges
                if l["source_type"] == "direct_homeowner":
                    l["badge_bg"] = "#ecfdf5"
                    l["badge_color"] = "#065f46"
                    l["badge_text"] = "🏡 Direct Homeowner (Verified Phone)"
                elif l["source_type"] == "domestic_classified":
                    l["badge_bg"] = "#eff6ff"
                    l["badge_color"] = "#1d4ed8"
                    l["badge_text"] = "🏡 Private Domestic"
                    if "Tender" in l["summary"] or "Estate" in l["summary"]:
                        l["badge_bg"] = "#fdf4ff"
                        l["badge_color"] = "#86198f"
                        l["badge_text"] = "🏢 Estate Tender"
                elif l["source_type"] == "council_planning":
                    if not l.get("badge_text") or l["badge_text"] == "Lead":
                        l["badge_text"] = "🏛️ Council Statutory"

                l["is_urgent"] = is_urgent_lead(l.get("summary"))

                # Filter routing
                if not filter_tier or filter_tier == "all":
                    enriched.append(l)
                elif filter_tier == "council" and l["source_type"] == "council_planning":
                    enriched.append(l)
                elif filter_tier == "domestic" and l["source_type"] in ("domestic_classified", "direct_homeowner"):
                    enriched.append(l)
                elif l["tier"] == filter_tier:
                    enriched.append(l)

                # Aug 31 2026: this used to break out of the loop as soon as
                # `limit` matching leads were collected, in discovered_at-DESC
                # order -- which meant an urgent lead sitting just past the
                # cutoff could never surface at all. Now the loop runs to
                # completion over the (already capped at 150) SQL rows, and
                # urgent leads are sorted to the front before the limit is
                # applied, so urgency can't be starved out by recency.

            enriched.sort(key=lambda x: (not x.get("is_urgent"), _sort_key_discovered_at(x)))
            return enriched[:limit]
        finally:
            cur.close()
            conn.close()
    except Exception as e:
        logger.error(f"[Marketplace] Error fetching enriched leads: {e}")
        return []


def calculate_crew_job_cost(climbers: int = 1, groundies: int = 1, tipping_loads: int = 1, 
                            fuel_cost: float = 30.0, days: float = 1.0) -> dict:
    """
    Vertical Arborist Job Costing Model:
    Calculates true operating cost based on day rates, tipping fees, and 2-stroke/diesel consumables.
    """
    climber_rate = 180.0  # £180/day standard UK climber
    groundy_rate = 120.0  # £120/day standard UK groundy
    tipping_rate = 90.0   # £90/load commercial transfer station fee

    labor_cost = (climbers * climber_rate + groundies * groundy_rate) * days
    tipping_total = tipping_loads * tipping_rate
    fuel_total = fuel_cost * days
    base_cost = labor_cost + tipping_total + fuel_total

    # Overheads (insurance, chains, ropes, PPE reserve): 15%
    overhead = base_cost * 0.15
    total_true_cost = base_cost + overhead

    # Recommended quotes based on profit margins
    quote_40pct = total_true_cost / 0.60   # 40% net margin
    quote_55pct = total_true_cost / 0.45   # 55% premium margin

    return {
        "labor_cost": round(labor_cost, 2),
        "tipping_cost": round(tipping_total, 2),
        "fuel_cost": round(fuel_total, 2),
        "overhead_reserve": round(overhead, 2),
        "total_true_cost": round(total_true_cost, 2),
        "recommended_quote_standard": round(quote_40pct, 2),
        "recommended_quote_premium": round(quote_55pct, 2),
        "estimated_net_profit": round(quote_40pct - total_true_cost, 2)
    }


def save_ledger_entry(contractor_email: str, job_name: str, client_type: str, 
                      gross_amount: float, labor_amount: float = 0.0, 
                      materials_plant: float = 0.0, cis_rate: float = 0.0, 
                      tipping_cost: float = 0.0, fuel_cost: float = 0.0) -> bool:
    """
    Saves an arborist invoice entry, automatically calculating CIS deductions and true net profit.
    """
    if not SURL or not contractor_email or not job_name:
        return False
    try:
        conn = get_db_conn()
        cur = conn.cursor()
        try:
            # CIS is only deducted from the LABOR portion, never materials or plant machinery
            cis_tax_deducted = (labor_amount * (cis_rate / 100.0)) if client_type == "commercial_cis" else 0.0
            cash_received = gross_amount - cis_tax_deducted
            net_profit = cash_received - (tipping_cost + fuel_cost)
            margin_pct = (net_profit / gross_amount * 100.0) if gross_amount > 0 else 0.0

            cur.execute("""
                INSERT INTO contractor_ledger_entries (
                    contractor_email, job_name, client_type, gross_amount, labor_amount,
                    materials_plant_amount, cis_rate_pct, cis_tax_deducted, tipping_costs,
                    fuel_consumables, net_profit, profit_margin_pct
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                RETURNING id;
            """, (
                contractor_email.strip().lower(), job_name.strip(), client_type,
                gross_amount, labor_amount, materials_plant, cis_rate, cis_tax_deducted,
                tipping_cost, fuel_cost, net_profit, margin_pct
            ))
            row = cur.fetchone()
            conn.commit()
            return bool(row)
        finally:
            cur.close()
            conn.close()
    except Exception as e:
        logger.error(f"[Ledger] Error saving ledger entry for {contractor_email}: {e}")
        return False


def get_contractor_financial_summary(contractor_email: str) -> dict:
    """
    Aggregates financial performance, CIS deductions held by developers, and proximity to the £90,000 UK VAT threshold.
    """
    if not SURL or not contractor_email:
        return {
            "rolling_turnover": 0.0,
            "vat_threshold": 90000.0,
            "vat_headroom": 90000.0,
            "vat_status": "Safe Zone (Unregistered Sole Trader)",
            "cis_tax_held": 0.0,
            "net_profit_total": 0.0,
            "job_count": 0
        }
    try:
        conn = get_db_conn()
        cur = conn.cursor()
        try:
            # 12-Month rolling aggregate
            cur.execute("""
                SELECT 
                    COALESCE(SUM(gross_amount), 0),
                    COALESCE(SUM(cis_tax_deducted), 0),
                    COALESCE(SUM(net_profit), 0),
                    COUNT(*)
                FROM contractor_ledger_entries
                WHERE contractor_email = %s AND job_date >= CURRENT_DATE - INTERVAL '12 months';
            """, (contractor_email.strip().lower(),))
            row = cur.fetchone()
            turnover = float(row[0])
            cis_held = float(row[1])
            net_profit = float(row[2])
            job_count = int(row[3])

            vat_limit = 90000.0
            headroom = max(0.0, vat_limit - turnover)
            
            if turnover >= vat_limit:
                vat_status = "🚨 EXCEEDED: Mandatory VAT Registration Required with HMRC"
                vat_color = "#dc2626"
            elif turnover >= 80000.0:
                vat_status = f"⚠️ WARNING: Only £{headroom:,.0f} Headroom Remaining Before £90k VAT Trap"
                vat_color = "#ea580c"
            else:
                vat_status = f"✅ Safe Zone: £{headroom:,.0f} Remaining in VAT Exemption"
                vat_color = "#059669"

            return {
                "rolling_turnover": round(turnover, 2),
                "vat_threshold": vat_limit,
                "vat_headroom": round(headroom, 2),
                "vat_status": vat_status,
                "vat_color": vat_color,
                "cis_tax_held": round(cis_held, 2),
                "net_profit_total": round(net_profit, 2),
                "job_count": job_count
            }
        finally:
            cur.close()
            conn.close()
    except Exception as e:
        logger.error(f"[Ledger] Error generating summary for {contractor_email}: {e}")
        return {
            "rolling_turnover": 0.0,
            "vat_threshold": 90000.0,
            "vat_headroom": 90000.0,
            "vat_status": "Unknown",
            "cis_tax_held": 0.0,
            "net_profit_total": 0.0,
            "job_count": 0
        }


def create_magic_auth_token(email: str) -> Optional[dict]:
    """
    Generates a cryptographically secure 1-tap Magic Token and 6-digit OTP code.
    Valid for 15 minutes.
    """
    import secrets
    import random
    if not SURL or not email:
        return None
    try:
        token = secrets.token_urlsafe(32)
        otp = f"{random.randint(100000, 999999)}"
        conn = get_db_conn()
        cur = conn.cursor()
        try:
            cur.execute("""
                INSERT INTO contractor_auth_tokens (customer_email, token, otp_code)
                VALUES (%s, %s, %s)
                RETURNING token, otp_code, expires_at;
            """, (email.strip().lower(), token, otp))
            row = cur.fetchone()
            conn.commit()
            if row:
                return {"token": row[0], "otp": row[1], "email": email.strip().lower()}
            return None
        finally:
            cur.close()
            conn.close()
    except Exception as e:
        logger.error(f"[Auth] Error creating magic token for {email}: {e}")
        return None


def verify_magic_auth_token(token: str = None, otp: str = None, email: str = None) -> Optional[str]:
    """
    Verifies a magic token or OTP code, ensuring it is unexpired and unused.
    Marks used = TRUE upon successful verification to prevent replay attacks.
    """
    if not SURL or (not token and not (otp and email)):
        return None
    try:
        conn = get_db_conn()
        cur = conn.cursor()
        try:
            if token:
                cur.execute("""
                    UPDATE contractor_auth_tokens
                    SET used = TRUE
                    WHERE token = %s AND used = FALSE AND expires_at > NOW()
                    RETURNING customer_email;
                """, (token.strip(),))
            else:
                cur.execute("""
                    UPDATE contractor_auth_tokens
                    SET used = TRUE
                    WHERE otp_code = %s AND customer_email = %s AND used = FALSE AND expires_at > NOW()
                    RETURNING customer_email;
                """, (otp.strip(), email.strip().lower()))
            row = cur.fetchone()
            conn.commit()
            if row:
                return row[0]
            return None
        finally:
            cur.close()
            conn.close()
    except Exception as e:
        logger.error(f"[Auth] Error verifying auth token: {e}")
        return None


def get_contractor_dashboard_data(email: str) -> dict:
    """
    Fetches subscription info, single-sale lead dispatch history, and quick metrics for contractor dashboard.
    """
    if not SURL or not email:
        return {"email": email, "tier": "None", "leads": [], "active": False}
    try:
        conn = get_db_conn()
        cur = conn.cursor()
        try:
            # 1. Fetch Subscription details
            cur.execute("""
                SELECT tier, center_outcode, radius_miles, monthly_quota, delivered_this_month, active, subscribed_at, stripe_subscription_id
                FROM contractor_subscriptions
                WHERE customer_email = %s;
            """, (email.strip().lower(),))
            sub_row = cur.fetchone()
            
            sub_info = {
                "tier": sub_row[0] if sub_row else "Free / Pay-As-You-Go",
                "outcode": sub_row[1] if sub_row else "GB",
                "radius": sub_row[2] if sub_row else 15,
                "quota": sub_row[3] if sub_row else 0,
                "delivered": sub_row[4] if sub_row else 0,
                "active": sub_row[5] if sub_row else False,
                "stripe_sub_id": sub_row[7] if sub_row else None
            }

            # 2. Fetch dispatched leads
            cur.execute("""
                SELECT l.id, l.reference, l.address, l.summary, l.council_source, l.lead_score, l.lead_price,
                       d.dispatched_at, d.dispatch_type, l.applicant_name, l.agent_name, l.agent_company, l.has_agent
                FROM lead_dispatches d
                JOIN leads l ON l.id = d.lead_id
                WHERE d.contractor_email = %s
                ORDER BY d.dispatched_at DESC
                LIMIT 30;
            """, (email.strip().lower(),))
            leads_rows = cur.fetchall()
            cols = ["id", "ref", "addr", "summary", "council", "score", "price", "dispatched_at", "dispatch_type",
                     "applicant_name", "agent_name", "agent_company", "has_agent"]
            dispatched_leads = [dict(zip(cols, r)) for r in leads_rows]

            return {
                "email": email,
                "subscription": sub_info,
                "dispatched_leads": dispatched_leads,
                "total_leads_received": len(dispatched_leads)
            }
        finally:
            cur.close()
            conn.close()
    except Exception as e:
        logger.error(f"[Dashboard] Error fetching contractor data for {email}: {e}")
        return {"email": email, "tier": "Error", "leads": [], "active": False}


def register_chip_drop_spot(site_name: str, phone: str, outcode: str, town: str, 
                            address: str, material_accepted: str = "fresh_woodchip", 
                            max_vehicle: str = "3.5t_transit", access_notes: str = "",
                            contact_name: str = "") -> bool:
    """
    Registers a community green waste / timber drop site (allotment, farm, stables).
    """
    if not SURL or not site_name or not phone or not outcode:
        return False
    try:
        conn = get_db_conn()
        cur = conn.cursor()
        try:
            cur.execute("""
                INSERT INTO chip_drop_spots (
                    site_name, contact_name, phone, outcode, town, address,
                    material_accepted, max_vehicle_size, access_instructions
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                RETURNING id;
            """, (
                site_name.strip(), contact_name.strip(), phone.strip(),
                outcode.strip().upper(), town.strip(), address.strip(),
                material_accepted, max_vehicle, access_notes.strip()
            ))
            row = cur.fetchone()
            conn.commit()
            return bool(row)
        finally:
            cur.close()
            conn.close()
    except Exception as e:
        logger.error(f"[ChipDrop] Error registering site {site_name}: {e}")
        return False


def get_chip_drop_spots(outcode: str = None, material: str = None, limit: int = 40) -> list:
    """
    Fetches active verified green waste and timber drop spots, optionally filtered by outcode or material.
    """
    if not SURL:
        return []
    try:
        conn = get_db_conn()
        cur = conn.cursor()
        try:
            sql = "SELECT id, site_name, contact_name, phone, outcode, town, address, material_accepted, max_vehicle_size, access_instructions, created_at FROM chip_drop_spots WHERE active = TRUE"
            params = []
            
            if outcode and outcode != "ALL":
                sql += " AND (outcode = %s OR outcode ILIKE %s)"
                params.extend([outcode.strip().upper(), f"{outcode.strip().upper()}%"])
                
            if material and material != "all":
                sql += " AND (material_accepted = %s OR material_accepted = 'any')"
                params.append(material)

            sql += " ORDER BY created_at DESC LIMIT %s;"
            params.append(limit)

            cur.execute(sql, tuple(params))
            rows = cur.fetchall()
            cols = ["id", "site_name", "contact_name", "phone", "outcode", "town", "address", "material", "max_vehicle", "access_notes", "created_at"]
            return [dict(zip(cols, r)) for r in rows]
        finally:
            cur.close()
            conn.close()
    except Exception as e:
        logger.error(f"[ChipDrop] Error fetching drop spots: {e}")
        return []


def record_storm_alert(region_name: str, outcode_prefixes: list, wind_gust_mph: int, 
                       warning_level: str = "amber", summary: str = "") -> bool:
    """
    Records a high-wind storm alert for targeted contractor emergency mobilization.
    """
    if not SURL or not region_name:
        return False
    try:
        conn = get_db_conn()
        cur = conn.cursor()
        try:
            cur.execute("""
                INSERT INTO storm_weather_alerts (region_name, outcode_prefixes, wind_gust_mph, warning_level, summary)
                VALUES (%s, %s, %s, %s, %s)
                RETURNING id;
            """, (region_name.strip(), outcode_prefixes, wind_gust_mph, warning_level, summary.strip()))
            row = cur.fetchone()
            conn.commit()
            return bool(row)
        finally:
            cur.close()
            conn.close()
    except Exception as e:
        logger.error(f"[StormRadar] Error recording alert: {e}")
        return False


def get_active_storm_alerts() -> list:
    """
    Fetches active Met Office / Severe Weather alerts (45mph+ gusts).
    """
    if not SURL:
        return []
    try:
        conn = get_db_conn()
        cur = conn.cursor()
        try:
            cur.execute("""
                SELECT id, region_name, outcode_prefixes, wind_gust_mph, warning_level, summary, valid_from, valid_to
                FROM storm_weather_alerts
                WHERE valid_to > NOW()
                ORDER BY wind_gust_mph DESC;
            """)
            rows = cur.fetchall()
            cols = ["id", "region", "outcodes", "gust_mph", "level", "summary", "valid_from", "valid_to"]
            return [dict(zip(cols, r)) for r in rows]
        finally:
            cur.close()
            conn.close()
    except Exception as e:
        logger.error(f"[StormRadar] Error fetching alerts: {e}")
        return []
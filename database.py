import os
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



def init_db():
    """Ensures all required tables and columns exist. Safe to run on every startup."""
    if not SURL:
        logger.warning("[DB] No SUPABASE_DB_URL found. Running in blind mode.")
        return
    try:
        conn = get_db_conn()
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
            "CREATE INDEX IF NOT EXISTS idx_partners_city ON potential_partners(target_city);",
            "CREATE INDEX IF NOT EXISTS idx_partners_created ON potential_partners(created_at DESC);",
            "CREATE INDEX IF NOT EXISTS idx_partners_company_number ON potential_partners(company_number);",
            "CREATE INDEX IF NOT EXISTS idx_territory_outcode ON territory_claims(outcode);",
        ]
        for idx in indices:
            cur.execute(idx)

        # Resilience: add any missing columns safely
        resilience_cols = [
            "ALTER TABLE potential_partners ADD COLUMN IF NOT EXISTS phone_number TEXT;",
            "ALTER TABLE potential_partners ADD COLUMN IF NOT EXISTS md_name TEXT;",
            "ALTER TABLE potential_partners ADD COLUMN IF NOT EXISTS google_rating NUMERIC;",
            "ALTER TABLE potential_partners ADD COLUMN IF NOT EXISTS website TEXT;",
            "ALTER TABLE potential_partners ADD COLUMN IF NOT EXISTS email TEXT;",
            "ALTER TABLE potential_partners ADD COLUMN IF NOT EXISTS enriched_at TIMESTAMPTZ;",
            "ALTER TABLE leads ADD COLUMN IF NOT EXISTS lead_score TEXT DEFAULT 'small';",
            "ALTER TABLE leads ADD COLUMN IF NOT EXISTS lead_price NUMERIC DEFAULT 25;",
            "ALTER TABLE leads ADD COLUMN IF NOT EXISTS registered_date DATE;",
            "ALTER TABLE leads ADD COLUMN IF NOT EXISTS statutory_deadline DATE;",
            "ALTER TABLE leads ADD COLUMN IF NOT EXISTS planning_status TEXT DEFAULT 'pending';",
            "ALTER TABLE leads ADD COLUMN IF NOT EXISTS lifecycle_stage TEXT DEFAULT 'stage_1_application';",
        ]
        for stmt in resilience_cols:
            cur.execute(stmt)
            
        # SECURITY MANDATE: Enable Row-Level Security to block public REST API access
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
            "ALTER TABLE contractor_auth_tokens ENABLE ROW LEVEL SECURITY;"
        ]
        for stmt in rls_statements:
            cur.execute(stmt)

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
        conn.close()
        logger.info("[DB] Database initialized successfully with high-performance indices, strict RLS lockout, and lead hygiene filters.")
    except Exception as e:

        logger.error(f"[DB] Initialization error: {e}")


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


def is_territory_claimed(outcode: str) -> bool:
    """Checks whether a given UK postcode district is already locked by an active subscriber."""
    if not SURL or not outcode:
        return False
    try:
        conn = get_db_conn()
        cur = conn.cursor()
        try:
            cur.execute("SELECT active FROM territory_claims WHERE outcode = %s AND active = TRUE", (outcode.strip().upper(),))
            row = cur.fetchone()
            return bool(row and row[0])
        finally:
            cur.close()
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


def burn_lead_inventory(lead_id: str, buyer_email: str) -> bool:
    """
    Single-Sale Inventory Burn Protocol:
    The millisecond a lead is purchased or claimed, it is permanently marked as 'claimed'
    and assigned to buyer_email so it is impossible to be displayed or sold to anyone else.
    """
    if not SURL or not lead_id:
        return False
    try:
        conn = get_db_conn()
        cur = conn.cursor()
        try:
            cur.execute("""
                UPDATE leads 
                SET status = 'claimed'
                WHERE id = %s AND (status = 'new' OR status IS NULL)
                RETURNING id;
            """, (lead_id,))
            row = cur.fetchone()
            conn.commit()
            if row:
                logger.info(f"[Inventory Burn] Lead {lead_id} permanently claimed & burned by {buyer_email}.")
                return True
            return False
        finally:
            cur.close()
            conn.close()
    except Exception as e:
        logger.error(f"[Inventory Burn] Error burning lead {lead_id}: {e}")
        return False


def register_or_update_subscription(customer_email: str, outcode: str, tier: str = "climber_domestic", 
                                     stripe_sub_id: str = None, radius: int = 15, name: str = None, phone: str = None) -> bool:
    """Registers or updates a contractor subscription with seniority timestamp."""
    if not SURL or not customer_email or not outcode:
        return False
    try:
        conn = get_db_conn()
        cur = conn.cursor()
        try:
            cur.execute("""
                INSERT INTO contractor_subscriptions (
                    customer_email, customer_name, phone, tier, center_outcode, radius_miles, stripe_subscription_id, active, subscribed_at
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, TRUE, NOW())
                ON CONFLICT (customer_email) DO UPDATE SET
                    tier = EXCLUDED.tier,
                    center_outcode = EXCLUDED.center_outcode,
                    radius_miles = EXCLUDED.radius_miles,
                    stripe_subscription_id = EXCLUDED.stripe_subscription_id,
                    active = TRUE
                RETURNING id;
            """, (customer_email.strip().lower(), name, phone, tier, outcode.strip().upper(), radius, stripe_sub_id))
            row = cur.fetchone()
            conn.commit()
            return bool(row)
        finally:
            cur.close()
            conn.close()
    except Exception as e:
        logger.error(f"[Subscription] Error registering subscription for {customer_email}: {e}")
        return False


def get_active_subscribers_by_seniority(outcode: str = None) -> list:
    """
    Returns active subscribers sorted strictly by Seniority (subscribed_at ASC).
    Ensures long-term subscribers receive first priority allocation.
    """
    if not SURL:
        return []
    try:
        conn = get_db_conn()
        cur = conn.cursor()
        try:
            if outcode:
                cur.execute("""
                    SELECT id, customer_email, customer_name, phone, tier, center_outcode, radius_miles, 
                           monthly_quota, delivered_this_month, subscribed_at
                    FROM contractor_subscriptions
                    WHERE active = TRUE AND center_outcode = %s
                    ORDER BY subscribed_at ASC;
                """, (outcode.strip().upper(),))
            else:
                cur.execute("""
                    SELECT id, customer_email, customer_name, phone, tier, center_outcode, radius_miles, 
                           monthly_quota, delivered_this_month, subscribed_at
                    FROM contractor_subscriptions
                    WHERE active = TRUE
                    ORDER BY subscribed_at ASC;
                """)
            cols = ["id", "email", "name", "phone", "tier", "outcode", "radius", "quota", "delivered", "subscribed_at"]
            return [dict(zip(cols, r)) for r in cur.fetchall()]
        finally:
            cur.close()
            conn.close()
    except Exception as e:
        logger.error(f"[Subscription] Error fetching subscribers by seniority: {e}")
        return []


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
                WHERE (id = %s OR reference = %s) AND (status = 'new' OR status IS NULL)
                RETURNING id;
            """, (lead_id, lead_id))
            burned_lead = cur.fetchone()
            if not burned_lead:
                return False  # Already burned or claimed by someone else

            real_lead_uuid = burned_lead[0]

            # 2. Record dispatch audit log
            cur.execute("""
                INSERT INTO lead_dispatches (lead_id, contractor_id, contractor_email, dispatch_type)
                VALUES (%s, %s, %s, %s);
            """, (real_lead_uuid, sub_id, contractor_email, dispatch_type))

            # 3. Increment contractor monthly delivery count
            if sub_id:
                cur.execute("""
                    UPDATE contractor_subscriptions
                    SET delivered_this_month = delivered_this_month + 1,
                        last_dispatched_at = NOW()
                    WHERE id = %s;
                """, (sub_id,))

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
    Finds nearest unallocated leads in adjacent sectors when a local zone is under-supplied.
    """
    if not SURL or not outcode:
        return []
    try:
        conn = get_db_conn()
        cur = conn.cursor()
        try:
            # Match nearby prefix or council source
            cur.execute("""
                SELECT id, reference, address, summary, council_source, lead_score, lead_price
                FROM leads
                WHERE (status = 'new' OR status IS NULL)
                ORDER BY discovered_at DESC
                LIMIT %s;
            """, (limit,))
            cols = ["id", "ref", "addr", "summary", "council", "score", "price"]
            return [dict(zip(cols, r)) for r in cur.fetchall()]
        finally:
            cur.close()
            conn.close()
    except Exception as e:
        logger.error(f"[Seniority Router] Error fetching fallback leads: {e}")
        return []


def calculate_lead_freshness(discovered_at, planning_status: str = "pending", summary: str = "") -> dict:
    """
    Calculates statutory lead freshness, countdown timer, color badge, and dynamic decay price:
    - Flash Hot (Day 0-3): £29 unlock (0 competitors aware)
    - Active Quoting (Day 4-14): £19 unlock (Prime window)
    - Clearance / Late Window (Day 15-30): £9 unlock (Consultation closing)
    - Granted / Approved: £25 unlock (Permitted felling ready to start)
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

    # Calculate days since registration/discovery
    days_old = 0
    if discovered_at:
        if isinstance(discovered_at, str):
            try:
                dt = datetime.datetime.fromisoformat(discovered_at.replace("Z", "+00:00"))
                days_old = (now - dt).days
            except Exception:
                days_old = 0
        elif hasattr(discovered_at, "timestamp"):
            if discovered_at.tzinfo is None:
                discovered_at = discovered_at.replace(tzinfo=datetime.timezone.utc)
            days_old = (now - discovered_at).days

    # Consultation window is typically 42 days (6 weeks for S211) or 56 days (TPO)
    is_tpo = "tpo" in (summary or "").lower() or "preservation" in (summary or "").lower()
    total_window = 56 if is_tpo else 42
    days_left = max(0, total_window - days_old)

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
    else:
        return {
            "tier": "clearance",
            "badge_color": "#ca8a04",
            "badge_bg": "#fefce8",
            "badge_text": f"⏳ Late Window Clearance (Closing Soon)",
            "price": 9,
            "days_left": f"{days_left} days until determination",
            "plan_key": "single_lead_small"
        }


def get_marketplace_leads_with_freshness(filter_tier: str = None, limit: int = 40) -> list:
    """
    Returns unallocated leads enriched with their dynamic statutory freshness calculation.
    Supports filtering by tier ('flash_hot', 'active', 'clearance', 'granted').
    """
    if not SURL:
        return []
    try:
        conn = get_db_conn()
        cur = conn.cursor()
        try:
            cur.execute("""
                SELECT id, reference, address, summary, council_source, lead_score, lead_price, 
                       discovered_at, planning_status, registered_date
                FROM leads 
                WHERE status = 'new' OR status IS NULL 
                ORDER BY discovered_at DESC 
                LIMIT 100;
            """)
            rows = cur.fetchall()
            cols = ["id", "ref", "addr", "summary", "council", "score", "base_price", "discovered_at", "status", "reg_date"]
            raw_leads = [dict(zip(cols, r)) for r in rows]

            enriched = []
            for l in raw_leads:
                freshness = calculate_lead_freshness(l["discovered_at"], l["status"], l["summary"])
                l.update(freshness)
                if not filter_tier or filter_tier == "all" or l["tier"] == filter_tier:
                    enriched.append(l)
                if len(enriched) >= limit:
                    break

            return enriched
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
                SELECT l.id, l.reference, l.address, l.summary, l.council_source, l.lead_score, l.lead_price, d.dispatched_at, d.dispatch_type
                FROM lead_dispatches d
                JOIN leads l ON l.id = d.lead_id
                WHERE d.contractor_email = %s
                ORDER BY d.dispatched_at DESC
                LIMIT 30;
            """, (email.strip().lower(),))
            leads_rows = cur.fetchall()
            cols = ["id", "ref", "addr", "summary", "council", "score", "price", "dispatched_at", "dispatch_type"]
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
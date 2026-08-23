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
        """)

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
        ]
        for stmt in resilience_cols:
            cur.execute(stmt)

        conn.commit()
        cur.close()
        conn.close()
        logger.info("[DB] Database initialized successfully.")
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
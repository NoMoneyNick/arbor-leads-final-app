import os
import psycopg2
import logging
from dotenv import load_dotenv

load_dotenv()
SURL = os.getenv("SUPABASE_DB_URL", "").strip()
logger = logging.getLogger("vector-data-labs")


def get_db_conn():
    """Opens a connection to the Supabase database."""
    return psycopg2.connect(SURL)


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
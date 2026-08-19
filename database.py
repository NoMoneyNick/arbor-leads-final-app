import os
import psycopg2
import logging
from dotenv import load_dotenv

load_dotenv()
SURL = os.getenv("SUPABASE_DB_URL", "").strip()
logger = logging.getLogger("vector-data-labs")

def get_db_conn():
    """Opens a private tunnel to the Filing Cabinet (Supabase)."""
    return psycopg2.connect(SURL)

def init_db():
    """Ensures the Filing Cabinet has the correct drawers and locks."""
    if not SURL: 
        logger.warning("No Database URL found. Running in blind mode.")
        return
    try:
        conn = get_db_conn()
        cur = conn.cursor()
        # Create the basic tables
        cur.execute("""
            CREATE TABLE IF NOT EXISTS potential_partners (
                id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                company_name TEXT, company_number TEXT UNIQUE, status TEXT,
                address TEXT, distance_miles NUMERIC, target_city TEXT,
                sic_codes TEXT[], md_name TEXT, phone_number TEXT,
                google_rating NUMERIC, created_at TIMESTAMPTZ DEFAULT NOW()
            );
            CREATE TABLE IF NOT EXISTS leads (
                id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                reference TEXT UNIQUE, address TEXT, summary TEXT,
                score INT DEFAULT 50, council_source TEXT,
                status TEXT DEFAULT 'new', discovered_at TIMESTAMPTZ DEFAULT NOW()
            );
        """)
        # Resilience Logic: Add missing drawers if they were forgotten
        cur.execute("ALTER TABLE potential_partners ADD COLUMN IF NOT EXISTS phone_number TEXT;")
        cur.execute("ALTER TABLE potential_partners ADD COLUMN IF NOT EXISTS md_name TEXT;")
        cur.execute("ALTER TABLE potential_partners ADD COLUMN IF NOT EXISTS google_rating NUMERIC;")
        conn.commit()
        cur.close()
        conn.close()
        logger.info("[Filing Cabinet] Fully organized and secured.")
    except Exception as e:
        logger.error(f"[Filing Cabinet] Error: {e}")
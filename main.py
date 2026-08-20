import os
import logging
import secrets
import database
import scanners
import research
from fastapi import FastAPI, Query, BackgroundTasks, HTTPException, Depends
from fastapi.responses import HTMLResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from typing import Optional

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("vector-data-labs")

app = FastAPI(title="Vector Data Labs V3.8 Modular", docs_url=None, redoc_url=None)
database.init_db()

# ── Security ──────────────────────────────────────────────────────────────────

T_SEC = os.getenv("TRIGGER_SECRET", "").strip()
basic_auth = HTTPBasic()

def verify_dashboard_auth(credentials: HTTPBasicCredentials = Depends(basic_auth)):
    """
    Protects all dashboard routes with HTTP Basic Auth.
    Set DASHBOARD_USER and DASHBOARD_PASS in Render environment variables.
    """
    DASH_USER = os.getenv("DASHBOARD_USER", "admin").strip()
    DASH_PASS = os.getenv("DASHBOARD_PASS", "").strip()

    if not DASH_PASS:
        logger.error("[AUTH] CRITICAL: DASHBOARD_PASS is not set. Blocking all access.")
        raise HTTPException(
            status_code=503,
            detail="Server not configured. Set DASHBOARD_PASS in environment variables.",
        )

    correct_user = secrets.compare_digest(credentials.username.encode(), DASH_USER.encode())
    correct_pass = secrets.compare_digest(credentials.password.encode(), DASH_PASS.encode())

    if not (correct_user and correct_pass):
        logger.warning(f"[AUTH] DENIED: Failed login attempt for user '{credentials.username}'.")
        raise HTTPException(
            status_code=401,
            detail="Incorrect username or password.",
            headers={"WWW-Authenticate": "Basic"},
        )
    return credentials.username

def verify_cron_secret(secret: str):
    """
    Protects external cron/trigger routes using TRIGGER_SECRET.
    Used by Make.com, UptimeRobot, or any automated caller.
    """
    if not T_SEC:
        logger.error("[GATE] CRITICAL: TRIGGER_SECRET is not set.")
        raise HTTPException(status_code=500, detail="Server config error")
    if not secrets.compare_digest(secret.encode(), T_SEC.encode()):
        logger.warning(f"[GATE] DENIED: Wrong trigger secret.")
        raise HTTPException(status_code=401, detail="Unauthorized")
    logger.info("[GATE] GRANTED: Valid trigger secret.")


# ── Dashboard ─────────────────────────────────────────────────────────────────

@app.get("/", response_class=HTMLResponse)
def dashboard(user: str = Depends(verify_dashboard_auth)):
    stats = {"p": 0, "l": 0, "list": []}
    try:
        conn = database.get_db_conn(); cur = conn.cursor()
        cur.execute("SELECT count(*) FROM potential_partners"); stats["p"] = cur.fetchone()[0]
        cur.execute("SELECT count(*) FROM leads"); stats["l"] = cur.fetchone()[0]
        cur.execute("SELECT company_name, md_name, target_city, google_rating FROM potential_partners ORDER BY created_at DESC LIMIT 5")
        stats["list"] = cur.fetchall()
        cur.close(); conn.close()
    except Exception as e:
        logger.error(f"[DASHBOARD] Error: {e}")

    rows = "".join([
        f"<li><b>{p[0]}</b> — Director: {p[1] or 'Searching...'} | {p[2]} | ⭐ {p[3] or 'N/A'}</li>"
        for p in stats["list"]
    ])

    return f"""
    <html><head><title>Vector Data Labs</title></head>
    <body style="font-family:sans-serif; background:#f4f4f9; padding:40px;">
        <div style="max-width:640px; margin:auto; background:white; padding:40px; border-radius:20px; border-top:8px solid #1b5e20;">
            <h1>📊 Franchise Manager V3.8</h1>
            <p>Total Partners: <b>{stats['p']}</b> &nbsp;|&nbsp; Total Leads: <b>{stats['l']}</b></p>
            <p><a href="/status" style="font-size:13px; color:#555;">🔧 System Status</a></p>
            <hr>

            <h3>📍 Lead Scanners</h3>
            <p>
                <a href="/scan/leeds">▶ Scan Leeds Leads</a> &nbsp;|&nbsp;
                <a href="/scan/london">▶ Scan London Leads</a>
            </p>

            <h3>🔎 Partner Research</h3>
            <p>
                <a href="/research-leeds">▶ Find Leeds Partners</a> &nbsp;|&nbsp;
                <a href="/research-london">▶ Find London Partners</a>
            </p>

            <h3>🔄 Enrichment</h3>
            <p><a href="/enrich-all">▶ Enrich All Partners (fill missing director names)</a></p>

            <hr>
            <h4>Latest Enriched Partners</h4>
            <ul>{rows or "<li>None yet.</li>"}</ul>
        </div>
    </body></html>
    """


# ── Status ────────────────────────────────────────────────────────────────────

@app.get("/status", response_class=HTMLResponse)
def status(user: str = Depends(verify_dashboard_auth)):
    ENV_VARS = [
        ("SUPABASE_DB_URL",     "Database (Supabase)"),
        ("TRIGGER_SECRET",      "Cron Security Gate"),
        ("DASHBOARD_USER",      "Dashboard Username"),
        ("DASHBOARD_PASS",      "Dashboard Password"),
        ("COMPANIES_HOUSE_KEY", "Companies House API"),
        ("GOOGLE_MAPS_KEY",     "Google Maps (Pillar 3)"),
        ("GLA_API_KEY",         "London Datahub (GLA)"),
        ("RESEND_API_KEY",      "Email (Resend)"),
        ("TEST_EMAIL",          "Alert Email Address"),
        ("PUBLIC_APP_URL",      "Public App URL"),
    ]

    rows_html = ""
    for key, label in ENV_VARS:
        val = os.getenv(key, "").strip()
        icon, color, note = ("✅", "#1b5e20", "Set") if val else ("❌", "#b71c1c", "MISSING")
        rows_html += f"<tr><td style='padding:8px;'>{label}</td><td style='padding:8px; color:{color}; font-weight:bold;'>{icon} {note}</td></tr>"

    db_url = os.getenv("SUPABASE_DB_URL", "").strip()
    if db_url:
        try:
            conn = database.get_db_conn(); conn.close()
            db_status = "<span style='color:#1b5e20; font-weight:bold;'>✅ Connected</span>"
        except Exception as e:
            db_status = f"<span style='color:#b71c1c; font-weight:bold;'>❌ Failed: {e}</span>"
    else:
        db_status = "<span style='color:#b71c1c; font-weight:bold;'>❌ No URL set</span>"

    return f"""
    <html><head><title>System Status</title></head>
    <body style="font-family:sans-serif; background:#f4f4f9; padding:40px;">
        <div style="max-width:620px; margin:auto; background:white; padding:40px; border-radius:20px; border-top:8px solid #1b5e20;">
            <h2>🔧 System Status</h2>
            <p><a href="/">← Back to Dashboard</a></p>
            <h4>Database Connection</h4>
            <p>{db_status}</p>
            <h4>Environment Variables</h4>
            <table style="width:100%; border-collapse:collapse;">
                <tr style="background:#f4f4f9;">
                    <th style="text-align:left; padding:8px;">Service</th>
                    <th style="text-align:left; padding:8px;">Status</th>
                </tr>
                {rows_html}
            </table>
            <p style="margin-top:20px; font-size:12px; color:#888;">Keys are never displayed — only presence is checked.</p>
        </div>
    </body></html>
    """


# ── Scan Routes (dashboard-triggered, protected by Basic Auth) ────────────────

@app.get("/scan/leeds", response_class=HTMLResponse)
def scan_leeds(user: str = Depends(verify_dashboard_auth)):
    count = scanners.scan_leeds_leads()
    logger.info(f"[SCAN] Leeds complete. {count} new leads.")
    return f"<html><body><p>✅ Leeds scan complete. <b>{count}</b> new leads found.</p><a href='/'>← Back</a></body></html>"

@app.get("/scan/london", response_class=HTMLResponse)
def scan_london(user: str = Depends(verify_dashboard_auth)):
    count = scanners.scan_london_leads()
    logger.info(f"[SCAN] London complete. {count} new leads.")
    return f"<html><body><p>✅ London scan complete. <b>{count}</b> new leads found.</p><a href='/'>← Back</a></body></html>"


# ── Cron Routes (external trigger, protected by TRIGGER_SECRET) ───────────────

@app.get("/trigger-leads-leeds")
def cron_leeds(secret: Optional[str] = Query(None)):
    verify_cron_secret(secret)
    count = scanners.scan_leeds_leads()
    logger.info(f"[CRON] Leeds scan: {count} new leads.")
    return {"status": "success", "count": count}

@app.get("/trigger-leads-london")
def cron_london(secret: Optional[str] = Query(None)):
    verify_cron_secret(secret)
    count = scanners.scan_london_leads()
    logger.info(f"[CRON] London scan: {count} new leads.")
    return {"status": "success", "count": count}


# ── Research Routes (protected by Basic Auth) ─────────────────────────────────

@app.get("/research-leeds")
def leeds_partners(bg: BackgroundTasks, user: str = Depends(verify_dashboard_auth)):
    bg.add_task(research.perform_research, "Leeds")
    return {"status": "started", "city": "Leeds"}

@app.get("/research-london")
def london_partners(bg: BackgroundTasks, user: str = Depends(verify_dashboard_auth)):
    bg.add_task(research.perform_research, "London")
    return {"status": "started", "city": "London"}

@app.get("/enrich-all", response_class=HTMLResponse)
def enrich_all(bg: BackgroundTasks, user: str = Depends(verify_dashboard_auth)):
    bg.add_task(research.enrich_existing_partners)
    logger.info("[ENRICH] Retroactive enrichment job started.")
    return "<html><body><p>✅ Enrichment started. Check dashboard in 2-3 minutes.</p><a href='/'>← Back</a></body></html>"
import os
import logging
import database
import scanners
import research
from fastapi import FastAPI, Query, BackgroundTasks, HTTPException
from fastapi.responses import HTMLResponse
from typing import Optional

# Configure logging to see the Cron's signature in the logs
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("vector-data-labs")

app = FastAPI(title="Vector Data Labs V3.8 Modular")
database.init_db()

# Security gate secret
T_SEC = os.getenv("TRIGGER_SECRET", "").strip()

def verify_gate(secret: str):
    if not T_SEC:
        logger.error("[GATE] CRITICAL: TRIGGER_SECRET is not set in environment.")
        raise HTTPException(status_code=500, detail="Server config error")
    if secret != T_SEC:
        logger.warning(f"[GATE] DENIED: Access attempt with wrong secret: {secret}")
        raise HTTPException(status_code=401, detail="Unauthorized")
    logger.info("[GATE] GRANTED: Valid secret received.")

@app.get("/", response_class=HTMLResponse)
def dashboard():
    stats = {"p": 0, "l": 0, "list": []}
    try:
        conn = database.get_db_conn(); cur = conn.cursor()
        cur.execute("SELECT count(*) FROM potential_partners"); stats["p"] = cur.fetchone()[0]
        cur.execute("SELECT count(*) FROM leads"); stats["l"] = cur.fetchone()[0]
        cur.execute("SELECT company_name, md_name, target_city FROM potential_partners ORDER BY created_at DESC LIMIT 5"); stats["list"] = cur.fetchall()
        cur.close(); conn.close()
    except Exception as e:
        logger.error(f"[DASHBOARD] Error: {e}")

    rows = "".join([f"<li><b>{p[0]}</b> - Boss: {p[1] or 'Searching...'} ({p[2]})</li>" for p in stats["list"]])

    return f"""
    <html><body style="font-family:sans-serif; background:#f4f4f9; padding:40px;">
        <div style="max-width:600px; margin:auto; background:white; padding:40px; border-radius:20px; border-top:8px solid #1b5e20;">
            <h1>Franchise Manager V3.8</h1>
            <p>Total Partners: <b>{stats['p']}</b> | Total Leads: <b>{stats['l']}</b></p>
            <p><a href="/status" style="font-size:13px; color:#555;">🔧 System Status Check</a></p>
            <hr>
            <button onclick="document.getElementById('leeds').style.display='block'">📍 Leeds</button>
            <button onclick="document.getElementById('london').style.display='block'">📍 London</button>
            
            <div id="leeds" style="display:none; margin-top:20px;">
                <a href="/trigger-leads-leeds?secret={T_SEC}">Scan Leeds Leads</a> | 
                <a href="/research-leeds">Find Leeds Partners</a>
            </div>
            <div id="london" style="display:none; margin-top:20px;">
                <a href="/trigger-leads-london?secret={T_SEC}">Scan London Leads</a> | 
                <a href="/research-london">Find London Partners</a>
            </div>
            
            <h4>Latest Enriched Partners</h4>
            <ul>{rows or "None yet."}</ul>
            <hr>
            <p style="font-size:13px; color:#555;">
                🔄 <a href="/enrich-all?secret={T_SEC}">Enrich All Partners (fill missing director names)</a>
            </p>
        </div>
    </body></html>
    """

@app.get("/status", response_class=HTMLResponse)
def status():
    """Live system health check — shows which keys are set and whether Supabase is reachable."""
    ENV_VARS = [
        ("SUPABASE_DB_URL",       "Database (Supabase)"),
        ("TRIGGER_SECRET",        "Security Gate"),
        ("COMPANIES_HOUSE_KEY",   "Companies House API"),
        ("APOLLO_API_KEY",        "Apollo (Pillar 2)"),
        ("GOOGLE_MAPS_KEY",       "Google Maps (Pillar 3)"),
        ("GLA_API_KEY",           "London Datahub (GLA)"),
        ("RESEND_API_KEY",        "Email (Resend)"),
        ("TEST_EMAIL",            "Alert Email Address"),
        ("PUBLIC_APP_URL",        "Public App URL"),
    ]

    rows_html = ""
    for key, label in ENV_VARS:
        val = os.getenv(key, "").strip()
        if val:
            icon, color, note = "✅", "#1b5e20", "Set"
        else:
            icon, color, note = "❌", "#b71c1c", "MISSING"
        rows_html += f"<tr><td style='padding:8px;'>{label}</td><td style='padding:8px; color:{color}; font-weight:bold;'>{icon} {note}</td></tr>"

    # Live DB ping
    db_url = os.getenv("SUPABASE_DB_URL", "").strip()
    if db_url:
        try:
            conn = database.get_db_conn()
            conn.close()
            db_status = "<span style='color:#1b5e20; font-weight:bold;'>✅ Connected</span>"
        except Exception as e:
            db_status = f"<span style='color:#b71c1c; font-weight:bold;'>❌ Failed: {e}</span>"
    else:
        db_status = "<span style='color:#b71c1c; font-weight:bold;'>❌ No URL set</span>"

    return f"""
    <html><body style="font-family:sans-serif; background:#f4f4f9; padding:40px;">
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

@app.get("/trigger-leads-leeds")
def leeds_leads(secret: Optional[str] = Query(None)):
    verify_gate(secret)
    count = scanners.scan_leeds_leads()
    logger.info(f"[CRON] Leeds scan complete. Found {count} new leads.")
    return {"status": "success", "count": count}

@app.get("/trigger-leads-london")
def london_leads(secret: Optional[str] = Query(None)):
    verify_gate(secret)
    count = scanners.scan_london_leads()
    logger.info(f"[CRON] London scan complete. Found {count} new leads.")
    return {"status": "success", "count": count}

@app.get("/research-leeds")
def leeds_partners(bg: BackgroundTasks):
    bg.add_task(research.perform_research, "Leeds")
    return {"status": "started"}

@app.get("/research-london")
def london_partners(bg: BackgroundTasks):
    bg.add_task(research.perform_research, "London")
    return {"status": "started"}

@app.get("/enrich-all")
def enrich_all(bg: BackgroundTasks, secret: Optional[str] = Query(None)):
    """Retroactively enriches all existing partners missing a director name."""
    verify_gate(secret)
    bg.add_task(research.enrich_existing_partners)
    logger.info("[ENRICH] Retroactive enrichment job started.")
    return {"status": "started", "message": "Enriching all partners in background. Check dashboard in 2-3 minutes."}
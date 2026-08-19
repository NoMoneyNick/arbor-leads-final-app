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
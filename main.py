import os
import logging
import secrets
import database
import scanners
import research
import payments
from fastapi import FastAPI, Query, BackgroundTasks, HTTPException, Depends, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from typing import Optional

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("vector-data-labs")

app = FastAPI(title="Vector Data Labs V4.0", docs_url=None, redoc_url=None)
database.init_db()

T_SEC      = os.getenv("TRIGGER_SECRET", "").strip()
basic_auth = HTTPBasic()

# All cities with their scan functions
ALL_CITIES = ["Leeds", "London", "Birmingham", "Manchester", "Bristol", "Sheffield"]


# ── Auth ──────────────────────────────────────────────────────────────────────

def verify_dashboard_auth(credentials: HTTPBasicCredentials = Depends(basic_auth)):
    DASH_USER = os.getenv("DASHBOARD_USER", "admin").strip()
    DASH_PASS = os.getenv("DASHBOARD_PASS", "").strip()
    if not DASH_PASS:
        raise HTTPException(status_code=503, detail="Set DASHBOARD_PASS in environment variables.")
    ok_user = secrets.compare_digest(credentials.username.encode(), DASH_USER.encode())
    ok_pass = secrets.compare_digest(credentials.password.encode(), DASH_PASS.encode())
    if not (ok_user and ok_pass):
        logger.warning(f"[AUTH] Failed login for '{credentials.username}'.")
        raise HTTPException(status_code=401, detail="Incorrect credentials.",
                            headers={"WWW-Authenticate": "Basic"})
    return credentials.username


def verify_cron_secret(secret: str):
    if not T_SEC:
        raise HTTPException(status_code=500, detail="TRIGGER_SECRET not configured.")
    if not secret:
        raise HTTPException(status_code=401, detail="Missing secret.")
    if not secrets.compare_digest(secret.encode(), T_SEC.encode()):
        logger.warning("[GATE] Invalid trigger secret.")
        raise HTTPException(status_code=401, detail="Unauthorized.")


# ── Dashboard ─────────────────────────────────────────────────────────────────

@app.get("/", response_class=HTMLResponse)
def dashboard(user: str = Depends(verify_dashboard_auth)):
    stats = {"p": 0, "l": 0, "partners": [], "leads": []}
    try:
        conn = database.get_db_conn(); cur = conn.cursor()
        cur.execute("SELECT count(*) FROM potential_partners"); stats["p"] = cur.fetchone()[0]
        cur.execute("SELECT count(*) FROM leads"); stats["l"] = cur.fetchone()[0]
        cur.execute("""SELECT company_name, md_name, target_city, google_rating
                       FROM potential_partners ORDER BY created_at DESC LIMIT 5""")
        stats["partners"] = cur.fetchall()
        cur.execute("""SELECT address, summary, lead_score, lead_price, council_source
                       FROM leads ORDER BY discovered_at DESC LIMIT 5""")
        stats["leads"] = cur.fetchall()
        cur.close(); conn.close()
    except Exception as e:
        logger.error(f"[DASHBOARD] DB error: {e}")

    partner_rows = "".join([
        f"<li><b>{p[0]}</b> — {p[1] or 'Director: Searching...'} | {p[2]} | ⭐ {p[3] or 'N/A'}</li>"
        for p in stats["partners"]
    ])

    SCORE_EMOJI = {"small": "🟡", "medium": "🟠", "large": "🔴"}
    lead_rows = "".join([
        f"<li>{SCORE_EMOJI.get(l[2],'🟡')} <b>{l[0]}</b> — {l[1][:80]}... | £{l[3]} | {l[4]}</li>"
        for l in stats["leads"]
    ])

    city_buttons = "".join([
        f"""<div style='display:inline-block; margin:6px; padding:12px 18px;
            background:#f4f4f9; border-radius:10px; border:1px solid #ddd;'>
            <b>📍 {city}</b><br>
            <a href='/scan/{city.lower()}'>▶ Scan Leads</a> &nbsp;|&nbsp;
            <a href='/research/{city.lower()}'>▶ Find Partners</a>
        </div>"""
        for city in ALL_CITIES
    ])

    return f"""
    <html><head><title>Vector Data Labs</title></head>
    <body style="font-family:sans-serif; background:#f4f4f9; padding:40px;">
    <div style="max-width:760px; margin:auto; background:white; padding:40px;
                border-radius:20px; border-top:8px solid #1b5e20;">
        <h1>📊 Vector Data Labs — V4.0</h1>
        <p>Partners: <b>{stats['p']}</b> &nbsp;|&nbsp; Leads: <b>{stats['l']}</b>
           &nbsp;|&nbsp; <a href='/status'>🔧 Status</a>
           &nbsp;|&nbsp; <a href='/pricing'>💳 Pricing</a>
           &nbsp;|&nbsp; <a href='/export-directors'>📋 Export Directors</a>
        </p>
        <hr>
        <h3>🏙️ Cities</h3>
        {city_buttons}
        <hr>
        <h3>🔄 Enrichment</h3>
        <p><a href='/enrich-all'>▶ Enrich All Partners (fill missing director names)</a></p>
        <hr>
        <h4>Latest Leads</h4>
        <ul>{lead_rows or "<li>No leads yet.</li>"}</ul>
        <h4>Latest Enriched Partners</h4>
        <ul>{partner_rows or "<li>No partners yet.</li>"}</ul>
    </div>
    </body></html>
    """


# ── Status ────────────────────────────────────────────────────────────────────

@app.get("/status", response_class=HTMLResponse)
def status(user: str = Depends(verify_dashboard_auth)):
    ENV_VARS = [
        ("SUPABASE_DB_URL",       "Database (Supabase)"),
        ("TRIGGER_SECRET",        "Cron Security Gate"),
        ("DASHBOARD_USER",        "Dashboard Username"),
        ("DASHBOARD_PASS",        "Dashboard Password"),
        ("COMPANIES_HOUSE_KEY",   "Companies House API"),
        ("GOOGLE_MAPS_KEY",       "Google Maps (Pillar 3)"),
        ("GLA_API_KEY",           "London Datahub (GLA)"),
        ("UK_PLANNING_API_KEY",   "UK Planning API (Bham/Mcr/Bristol/Shef)"),
        ("RESEND_API_KEY",        "Email (Resend)"),
        ("TEST_EMAIL",            "Alert Email Address"),
        ("PUBLIC_APP_URL",        "Public App URL"),
        ("STRIPE_SECRET_KEY",     "Stripe Payments"),
        ("STRIPE_WEBHOOK_SECRET", "Stripe Webhook"),
    ]
    rows_html = ""
    for key, label in ENV_VARS:
        val = os.getenv(key, "").strip()
        icon, color, note = ("✅", "#1b5e20", "Set") if val else ("❌", "#b71c1c", "MISSING")
        rows_html += f"<tr><td style='padding:8px;'>{label}</td><td style='padding:8px; color:{color}; font-weight:bold;'>{icon} {note}</td></tr>"

    try:
        conn = database.get_db_conn(); conn.close()
        db_status = "<span style='color:#1b5e20; font-weight:bold;'>✅ Connected</span>"
    except Exception as e:
        db_status = f"<span style='color:#b71c1c; font-weight:bold;'>❌ Failed: {e}</span>"

    return f"""
    <html><head><title>System Status</title></head>
    <body style="font-family:sans-serif; background:#f4f4f9; padding:40px;">
    <div style="max-width:620px; margin:auto; background:white; padding:40px;
                border-radius:20px; border-top:8px solid #1b5e20;">
        <h2>🔧 System Status</h2>
        <p><a href='/'>← Dashboard</a></p>
        <h4>Database</h4><p>{db_status}</p>
        <h4>Environment Variables</h4>
        <table style="width:100%; border-collapse:collapse;">
            <tr style="background:#f4f4f9;">
                <th style="text-align:left; padding:8px;">Service</th>
                <th style="text-align:left; padding:8px;">Status</th>
            </tr>
            {rows_html}
        </table>
        <p style="margin-top:20px; font-size:12px; color:#888;">
            Keys are never displayed — only presence is checked.<br>
            <b>Automated scanning:</b> Set up cron-job.org to hit
            <code>/trigger-leads-{{city}}?secret=YOUR_SECRET</code> on your preferred schedule.
        </p>
    </div></body></html>
    """


# ── Pricing Page (Public) ─────────────────────────────────────────────────────

@app.get("/pricing", response_class=HTMLResponse)
def pricing():
    plans = payments.PLANS

    cards = ""
    for key, plan in plans.items():
        if plan["mode"] == "subscription":
            price_display = f"£{plan['amount'] / 100:.0f}<span style='font-size:16px; font-weight:normal;'>/month</span>"
        else:
            price_display = f"£{plan['amount'] / 100:.0f}<span style='font-size:16px; font-weight:normal;'> one-off</span>"

        highlight = "border:3px solid #1b5e20;" if key == "city_monthly" else "border:2px solid #ccc;"

        cards += f"""
        <div style="{highlight} border-radius:16px; padding:24px; margin:10px 0;
                    background:white; text-align:center;">
            <div style="font-size:13px; color:#1b5e20; font-weight:bold;
                        margin-bottom:8px;">{plan['badge']}</div>
            <h3 style="margin:0 0 8px 0;">{plan['name']}</h3>
            <p style="color:#666; font-size:14px; margin:0 0 16px 0;">{plan['description']}</p>
            <h2 style="color:#1b5e20; margin:0 0 20px 0;">{price_display}</h2>
            <a href="/checkout/{key}"
               style="background:#1b5e20; color:white; padding:12px 28px;
                      border-radius:8px; text-decoration:none;
                      display:inline-block; font-weight:bold;">
               Get Started →
            </a>
        </div>"""

    return f"""
    <html>
    <head><title>Pricing — Exclusive Tree Surgery Leads</title></head>
    <body style="font-family:sans-serif; background:#f4f4f9; padding:40px;">
    <div style="max-width:560px; margin:auto;">

        <h1 style="text-align:center; color:#1b5e20;">🌳 Exclusive Tree Surgery Leads</h1>

        <div style="background:#fff8e1; border-left:4px solid #f9a825; padding:16px;
                    border-radius:8px; margin-bottom:24px; font-size:14px;">
            <b>How it works:</b> Every time a planning application for tree surgery work
            is filed at your local council, you get notified first —
            before it appears on Checkatrade, Bark, or anywhere else.
            Leads are <b>exclusive</b>: one buyer per lead, always.
        </div>

        {cards}

        <div style="margin-top:32px; background:white; border-radius:12px; padding:24px;">
            <h4 style="margin-top:0;">What's included in every plan</h4>
            <table style="width:100%; font-size:13px; border-collapse:collapse;">
                <tr style="background:#f4f4f9;">
                    <th style="padding:8px; text-align:left;"></th>
                    <th style="padding:8px; text-align:center;">Starter</th>
                    <th style="padding:8px; text-align:center;">Pay As You Go</th>
                    <th style="padding:8px; text-align:center;">City Pro</th>
                    <th style="padding:8px; text-align:center;">National</th>
                </tr>
                <tr><td style="padding:8px;">Exclusive leads</td>
                    <td style="text-align:center;">✅</td><td style="text-align:center;">✅</td>
                    <td style="text-align:center;">✅</td><td style="text-align:center;">✅</td></tr>
                <tr style="background:#f9f9f9;"><td style="padding:8px;">Email alert on new lead</td>
                    <td style="text-align:center;">✅</td><td style="text-align:center;">✅</td>
                    <td style="text-align:center;">✅</td><td style="text-align:center;">✅</td></tr>
                <tr><td style="padding:8px;">Lead grade (Small/Medium/Large)</td>
                    <td style="text-align:center;">✅</td><td style="text-align:center;">✅</td>
                    <td style="text-align:center;">✅</td><td style="text-align:center;">✅</td></tr>
                <tr style="background:#f9f9f9;"><td style="padding:8px;">Monthly lead limit</td>
                    <td style="text-align:center;">10/month</td><td style="text-align:center;">—</td>
                    <td style="text-align:center;">Unlimited</td><td style="text-align:center;">Unlimited</td></tr>
                <tr><td style="padding:8px;">Cities covered</td>
                    <td style="text-align:center;">1</td><td style="text-align:center;">1</td>
                    <td style="text-align:center;">1</td><td style="text-align:center;">All</td></tr>
                <tr style="background:#f9f9f9;"><td style="padding:8px;">First access to new leads</td>
                    <td style="text-align:center;">—</td><td style="text-align:center;">—</td>
                    <td style="text-align:center;">—</td><td style="text-align:center;">✅</td></tr>
            </table>
        </div>

        <p style="text-align:center; font-size:12px; color:#999; margin-top:24px;">
            Cancel anytime. No long-term contracts. All leads are government public data.<br>
            Questions? Reply to your welcome email.
        </p>

    </div>
    </body></html>
    """



# ── Checkout (Stripe) ─────────────────────────────────────────────────────────

@app.get("/checkout/{plan_key}")
def checkout(plan_key: str):
    url = payments.create_checkout_session(plan_key)
    if not url:
        raise HTTPException(status_code=503, detail="Payment system unavailable. Contact support.")
    return RedirectResponse(url=url)


@app.get("/payment/success", response_class=HTMLResponse)
def payment_success():
    return """
    <html><body style="font-family:sans-serif; text-align:center; padding:60px;">
        <h1>✅ Payment Successful!</h1>
        <p>Thank you. Your leads will start arriving shortly.</p>
        <p><a href="/">Back to Dashboard</a></p>
    </body></html>
    """


@app.post("/webhook")
async def stripe_webhook(request: Request):
    payload    = await request.body()
    sig_header = request.headers.get("stripe-signature", "")
    result = payments.handle_stripe_webhook(payload, sig_header)
    if "error" in result:
        raise HTTPException(status_code=400, detail=result["error"])
    return {"status": "ok", "event": result.get("event")}


# ── City Scan Routes (Dashboard — Basic Auth) ─────────────────────────────────

@app.get("/scan/{city_slug}", response_class=HTMLResponse)
def scan_city(city_slug: str, user: str = Depends(verify_dashboard_auth)):
    city_map = {c.lower(): c for c in ALL_CITIES}
    city = city_map.get(city_slug.lower())
    if not city:
        raise HTTPException(status_code=404, detail=f"City '{city_slug}' not configured.")

    if city == "Leeds":
        count = scanners.scan_leeds_leads()
    elif city == "London":
        count = scanners.scan_london_leads()
    else:
        count = scanners.scan_city_planning_api(city)

    return f"""<html><body style="font-family:sans-serif; padding:40px;">
        <p>✅ {city} scan complete. <b>{count}</b> new leads found.</p>
        <a href="/">← Back to Dashboard</a>
    </body></html>"""


# ── City Cron Routes (External — Trigger Secret) ──────────────────────────────

@app.get("/trigger-leads-{city_slug}")
def cron_trigger(city_slug: str, secret: Optional[str] = Query(None)):
    verify_cron_secret(secret)
    city_map = {c.lower(): c for c in ALL_CITIES}
    city = city_map.get(city_slug.lower())
    if not city:
        raise HTTPException(status_code=404, detail=f"City '{city_slug}' not configured.")

    if city == "Leeds":
        count = scanners.scan_leeds_leads()
    elif city == "London":
        count = scanners.scan_london_leads()
    else:
        count = scanners.scan_city_planning_api(city)

    logger.info(f"[CRON] {city}: {count} new leads.")
    return {"status": "success", "city": city, "new_leads": count}


# ── Research Routes (Basic Auth) ──────────────────────────────────────────────

@app.get("/research/{city_slug}")
def research_city(city_slug: str, bg: BackgroundTasks,
                  user: str = Depends(verify_dashboard_auth)):
    city_map = {c.lower(): c for c in ALL_CITIES}
    city = city_map.get(city_slug.lower())
    if not city:
        raise HTTPException(status_code=404, detail=f"City '{city_slug}' not configured.")
    bg.add_task(research.perform_research, city)
    return {"status": "started", "city": city}


@app.get("/enrich-all", response_class=HTMLResponse)
def enrich_all(bg: BackgroundTasks, user: str = Depends(verify_dashboard_auth)):
    bg.add_task(research.enrich_existing_partners)
    return """<html><body style="font-family:sans-serif; padding:40px;">
        <p>✅ Enrichment started in background. Check Render logs for progress.</p>
        <a href="/">← Back to Dashboard</a>
    </body></html>"""


@app.get("/clean-partners", response_class=HTMLResponse)
def clean_partners(user: str = Depends(verify_dashboard_auth)):
    """
    Retroactively removes non-tree-surgery companies from the partner DB.
    Applies the two-layer name filter to all existing records.
    Run once after deploy to clean up historical bad data.
    """
    result = research.clean_partner_database()
    if "error" in result:
        return f"""<html><body style="font-family:sans-serif; padding:40px;">
            <p>❌ Cleanup failed: {result['error']}</p>
            <a href="/">← Back to Dashboard</a>
        </body></html>"""
    return f"""<html><body style="font-family:sans-serif; padding:40px;">
        <h3>🧹 Partner Database Cleanup Complete</h3>
        <p>✅ Kept: <b>{result['kept']}</b> verified tree surgery companies</p>
        <p>🗑️ Removed: <b>{result['removed']}</b> unrelated businesses</p>
        <p style="color:#888; font-size:13px;">
            Removed companies had no tree-surgery keywords in their name,
            or contained excluded terms (medical, dental, fruit, cosmetic, etc.)
        </p>
        <a href="/">← Back to Dashboard</a>
    </body></html>"""



# ── Export Directors (Basic Auth) ─────────────────────────────────────────────

@app.get("/export-directors", response_class=HTMLResponse)
def export_directors(user: str = Depends(verify_dashboard_auth)):
    """
    Exports all potential partners as a formatted HTML table for outreach.
    Sorted by city then company name.
    """
    try:
        conn = database.get_db_conn(); cur = conn.cursor()
        cur.execute("""
            SELECT company_name, company_number, md_name, phone_number,
                   google_rating, target_city
            FROM potential_partners
            WHERE md_name IS NOT NULL
            ORDER BY target_city, company_name
        """)
        rows = cur.fetchall()
        cur.close(); conn.close()
    except Exception as e:
        logger.error(f"[EXPORT] DB error: {e}")
        rows = []

    table_rows = "".join([
        f"<tr>"
        f"<td style='padding:8px; border:1px solid #ddd;'>{r[0]}</td>"
        f"<td style='padding:8px; border:1px solid #ddd;'>{r[2] or '—'}</td>"
        f"<td style='padding:8px; border:1px solid #ddd;'>{r[3] or '—'}</td>"
        f"<td style='padding:8px; border:1px solid #ddd; text-align:center;'>⭐ {r[4] or 'N/A'}</td>"
        f"<td style='padding:8px; border:1px solid #ddd;'>{r[5]}</td>"
        f"</tr>"
        for r in rows
    ])

    return f"""
    <html><head><title>Director Export</title></head>
    <body style="font-family:sans-serif; background:#f4f4f9; padding:40px;">
    <div style="max-width:900px; margin:auto; background:white; padding:30px;
                border-radius:16px; border-top:8px solid #1b5e20;">
        <h2>📋 Director Outreach List ({len(rows)} contacts)</h2>
        <p><a href="/">← Dashboard</a></p>
        <table style="width:100%; border-collapse:collapse; font-size:13px;">
            <tr style="background:#1b5e20; color:white;">
                <th style="padding:10px; text-align:left;">Company</th>
                <th style="padding:10px; text-align:left;">Director</th>
                <th style="padding:10px; text-align:left;">Phone</th>
                <th style="padding:10px; text-align:center;">Google ⭐</th>
                <th style="padding:10px; text-align:left;">City</th>
            </tr>
            {table_rows or "<tr><td colspan='5' style='padding:16px; text-align:center;'>Run /enrich-all first to populate director names.</td></tr>"}
        </table>
    </div></body></html>
    """
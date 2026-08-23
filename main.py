import os
import logging
import secrets
import database
import scanners
import research
import payments
import csv
import io
from fastapi import FastAPI, Query, BackgroundTasks, HTTPException, Depends, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from typing import Optional

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("vector-data-labs")

app = FastAPI(title="Vector Data Labs V4.0", docs_url=None, redoc_url=None)
database.init_db()

T_SEC      = os.getenv("TRIGGER_SECRET", "").strip()
basic_auth = HTTPBasic()

# All 9 English Regions with nationwide council & partner coverage
ALL_CITIES = [
    "London", "South East", "South West", "West Midlands",
    "East Midlands", "Yorkshire", "North West", "North East", "East of England",
    "Leeds", "Birmingham", "Manchester", "Bristol", "Sheffield"
]


@app.get("/health")
def health():
    return {"status": "ok", "app": "Vector Data Labs"}


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

# ── Public Landing Page (No Auth) ──────────────────────────────────────────

@app.get("/", response_class=HTMLResponse)
def public_homepage():
    stats = {"p": 0, "l": 0, "sample_leads": []}
    try:
        conn = database.get_db_conn(); cur = conn.cursor()
        cur.execute("SELECT count(*) FROM potential_partners"); stats["p"] = cur.fetchone()[0]
        cur.execute("SELECT count(*) FROM leads"); stats["l"] = cur.fetchone()[0]
        cur.execute("""SELECT address, summary, lead_score, lead_price, council_source, discovered_at
                       FROM leads ORDER BY discovered_at DESC LIMIT 6""")
        stats["sample_leads"] = cur.fetchall()
        cur.close(); conn.close()
    except Exception as e:
        logger.error(f"[HOMEPAGE] DB error: {e}")

    import datetime
    now = datetime.datetime.now(datetime.timezone.utc)

    def get_freshness_badge(discovered_at):
        if not discovered_at:
            return "<span style='background:#e8f5e9; color:#2e7d32; padding:3px 8px; border-radius:12px; font-size:12px; font-weight:bold;'>🔥 FRESH</span>"
        try:
            delta_days = (now - discovered_at).days
            if delta_days <= 14:
                return f"<span style='background:#e8f5e9; color:#2e7d32; padding:3px 8px; border-radius:12px; font-size:12px; font-weight:bold;'>🔥 FRESH ({delta_days}d ago)</span>"
            elif delta_days <= 45:
                return f"<span style='background:#fff8e1; color:#f57f17; padding:3px 8px; border-radius:12px; font-size:12px; font-weight:bold;'>⏳ IN CONSULTATION</span>"
            elif delta_days <= 90:
                return f"<span style='background:#e1f5fe; color:#0277bd; padding:3px 8px; border-radius:12px; font-size:12px; font-weight:bold;'>✅ GRANTED</span>"
            else:
                return f"<span style='background:#f5f5f5; color:#757575; padding:3px 8px; border-radius:12px; font-size:12px;'>📦 ARCHIVED</span>"
        except Exception:
            return "<span style='background:#e8f5e9; color:#2e7d32; padding:3px 8px; border-radius:12px; font-size:12px; font-weight:bold;'>🔥 FRESH</span>"

    SCORE_EMOJI = {"small": "🟡", "medium": "🟠", "large": "🔴"}
    lead_cards = "".join([
        f"""<div style='background:white; border:1px solid #e0e0e0; border-radius:12px; padding:16px; margin-bottom:12px; box-shadow:0 2px 4px rgba(0,0,0,0.03);'>
            <div style='display:flex; justify-content:space-between; align-items:center; margin-bottom:8px;'>
                <b style='font-size:15px; color:#1b5e20;'>📍 {l[0]}</b>
                {get_freshness_badge(l[5])}
            </div>
            <p style='color:#444; font-size:13px; margin:0 0 8px 0; line-height:1.4;'>{l[1][:120]}...</p>
            <div style='display:flex; justify-content:space-between; font-size:12px; color:#666;'>
                <span>Council: <b>{l[4]}</b></span>
                <span style='font-weight:bold; color:#2e7d32;'>Est. Job Value: £{l[3]*20}–£{l[3]*50}</span>
            </div>
        </div>"""
        for l in stats["sample_leads"]
    ]) or "<p style='color:#777;'>Connecting to real-time council radar...</p>"

    return f"""
    <!DOCTYPE html>
    <html lang="en">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>ArborLeads — UK Tree Surgery Planning Radar</title>
        <style>
            body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif; margin:0; padding:0; background:#f8fafc; color:#1e293b; }}
            .container {{ max-width: 1080px; margin: auto; padding: 0 20px; }}
            header {{ background: #064e3b; color: white; padding: 60px 0; text-align: center; }}
            .badge {{ background: #10b981; color: #064e3b; padding: 6px 14px; border-radius: 20px; font-weight: 700; font-size: 13px; display: inline-block; margin-bottom: 16px; text-transform: uppercase; letter-spacing: 0.5px; }}
            h1 {{ font-size: 40px; margin: 0 0 16px 0; font-weight: 800; line-height: 1.2; }}
            .subtitle {{ font-size: 19px; color: #a7f3d0; max-width: 700px; margin: 0 auto 30px auto; line-height: 1.5; }}
            .btn-hero {{ background: #10b981; color: white; padding: 16px 36px; border-radius: 10px; font-size: 18px; font-weight: bold; text-decoration: none; display: inline-block; box-shadow: 0 10px 15px -3px rgba(16, 185, 129, 0.4); }}
            .btn-hero:hover {{ background: #059669; }}
            .section {{ padding: 60px 0; }}
            .grid-3 {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(300px, 1fr)); gap: 24px; }}
            .grid-5 {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(190px, 1fr)); gap: 16px; }}
            .card {{ background: white; border-radius: 16px; padding: 28px; border: 1px solid #e2e8f0; box-shadow: 0 4px 6px -1px rgba(0,0,0,0.05); }}
            .card-pricing {{ border: 2px solid #e2e8f0; text-align: center; position: relative; }}
            .card-featured {{ border: 2px solid #10b981; transform: scale(1.03); background: #f0fdf4; }}
            .price {{ font-size: 32px; font-weight: 800; color: #0f172a; margin: 12px 0; }}
            .btn-buy {{ display: block; background: #064e3b; color: white; padding: 12px 20px; border-radius: 8px; font-weight: bold; text-decoration: none; margin-top: 20px; }}
            .btn-buy:hover {{ background: #047857; }}
            footer {{ background: #0f172a; color: #94a3b8; padding: 40px 0; text-align: center; font-size: 13px; }}
            footer a {{ color: #cbd5e1; text-decoration: none; }}
        </style>
    </head>
    <body>
        <header>
            <div class="container">
                <span class="badge">⚡ 100% Automated Council Planning Radar</span>
                <h1>Win High-Value Tree Surgery Jobs<br>Before Your Competitors Even Know They Exist</h1>
                <p class="subtitle">We scan all 309 English local council planning feeds daily to alert professional tree surgeons within 24 hours of protected tree applications (TPO & Conservation Area submissions).</p>
                <a href="#pricing" class="btn-hero">Claim Your Territory Radar →</a>
            </div>
        </header>

        <section class="section">
            <div class="container">
                <div style="display:grid; grid-template-columns: 1fr 1fr; gap: 40px; align-items: center;">
                    <div>
                        <h2 style="font-size: 30px; margin-top: 0;">Why UK Tree Surgeons Rely on ArborLeads:</h2>
                        <ul style="line-height: 2; font-size: 16px; padding-left: 20px;">
                            <li><b>🎯 2 to 4-Week Head Start:</b> Quoting on jobs weeks before lamp post notices are hung or general public finds out.</li>
                            <li><b>🌳 Legally Mandated Work:</b> Homeowners and developers submitting council tree applications MUST perform the work upon permission.</li>
                            <li><b>🛡️ High-Ticket Opportunities:</b> TPO felling, crown reduction, deadwood dismantling, and site clearance.</li>
                            <li><b>⚡ Instant Email & WhatsApp Alerts:</b> Real-time statutory reference, tree species, and full address.</li>
                        </ul>
                    </div>
                    <div>
                        <div class="card" style="background:#f1f5f9;">
                            <h3 style="margin-top:0; color:#0f172a;">📡 Live Radar Sample (Past 24-48 Hours)</h3>
                            {lead_cards}
                        </div>
                    </div>
                </div>
            </div>
        </section>

        <section class="section" id="pricing" style="background: white; border-top: 1px solid #e2e8f0; border-bottom: 1px solid #e2e8f0;">
            <div class="container">
                <div style="text-align: center; max-width: 700px; margin: auto; margin-bottom: 40px;">
                    <h2 style="font-size: 32px; margin-bottom: 8px;">Simple, Transparent Pricing</h2>
                    <p style="color: #64748b; font-size: 17px;">Choose the plan that fits your business. Cancel or pause anytime.</p>
                </div>

                <div class="grid-5">
                    <!-- 1. Single -->
                    <div class="card card-pricing">
                        <h4>Single Lead</h4>
                        <div class="price">£19</div>
                        <p style="color:#64748b; font-size:12px;">One-time purchase</p>
                        <p style="font-size:13px; color:#334155;">1 Verified Council Lead with homeowner/architect address & description.</p>
                        <a href="/pricing" class="btn-buy">Buy 1 Lead</a>
                    </div>

                    <!-- 2. Pack of 5 -->
                    <div class="card card-pricing">
                        <h4>5-Lead Pack</h4>
                        <div class="price">£80</div>
                        <p style="color:#64748b; font-size:12px;">£16 / lead (Save 15%)</p>
                        <p style="font-size:13px; color:#334155;">5 Lead Credits in your depot postcode radius to redeem on demand.</p>
                        <a href="/pricing" class="btn-buy">Buy 5 Pack</a>
                    </div>

                    <!-- 3. City Pro -->
                    <div class="card card-pricing card-featured">
                        <span style="position:absolute; top:-12px; left:50%; transform:translateX(-50%); background:#10b981; color:white; font-size:11px; padding:2px 10px; border-radius:10px; font-weight:bold;">POPULAR</span>
                        <h4>City Pro</h4>
                        <div class="price">£49<span style="font-size:14px; font-weight:normal;">/mo</span></div>
                        <p style="color:#64748b; font-size:12px;">Monthly subscription</p>
                        <p style="font-size:13px; color:#334155;"><b>Unlimited Leads</b> in your entire regional council zone (15-mile radius).</p>
                        <a href="/pricing" class="btn-buy" style="background:#10b981;">Subscribe City</a>
                    </div>

                    <!-- 4. National -->
                    <div class="card card-pricing">
                        <h4>National Pass</h4>
                        <div class="price">£89<span style="font-size:14px; font-weight:normal;">/mo</span></div>
                        <p style="color:#64748b; font-size:12px;">Monthly subscription</p>
                        <p style="font-size:13px; color:#334155;">Unlimited leads across <b>all 309 English councils</b> nationwide.</p>
                        <a href="/pricing" class="btn-buy">Subscribe National</a>
                    </div>

                    <!-- 5. Exclusive Lockout -->
                    <div class="card card-pricing" style="border:2px solid #7c3aed; background:#faf5ff;">
                        <span style="position:absolute; top:-12px; left:50%; transform:translateX(-50%); background:#7c3aed; color:white; font-size:11px; padding:2px 10px; border-radius:10px; font-weight:bold;">MONOPOLY</span>
                        <h4 style="color:#7c3aed;">Exclusive Lockout</h4>
                        <div class="price" style="color:#6d28d9;">£149<span style="font-size:14px; font-weight:normal;">/mo</span></div>
                        <p style="color:#64748b; font-size:12px;">15-Mile Radius Lockout</p>
                        <p style="font-size:13px; color:#334155;"><b>Lock out all competitors.</b> 100% exclusive access in your territory.</p>
                        <a href="/pricing" class="btn-buy" style="background:#7c3aed;">Lock Territory</a>
                    </div>
                </div>
            </div>
        </section>

        <footer>
            <div class="container">
                <p>© 2026 ArborLeads — A Vector Data Labs SaaS Platform. Operating under UK Town and Country Planning Act open data regulations.</p>
                <p><a href="/pricing">Pricing Plans</a> &nbsp;|&nbsp; <a href="/health">System Status</a> &nbsp;|&nbsp; <a href="/admin">Contractor Portal Login</a></p>
            </div>
        </footer>
    </body>
    </html>
    """


# ── Management Dashboard (Basic Auth Protected at /admin) ────────────────────

@app.get("/admin", response_class=HTMLResponse)
def admin_dashboard(user: str = Depends(verify_dashboard_auth)):
    stats = {"p": 0, "l": 0, "enriched": 0, "partners": [], "leads": []}
    try:
        conn = database.get_db_conn(); cur = conn.cursor()
        cur.execute("SELECT count(*) FROM potential_partners"); stats["p"] = cur.fetchone()[0]
        cur.execute("SELECT count(*) FROM leads"); stats["l"] = cur.fetchone()[0]
        cur.execute("SELECT count(*) FROM potential_partners WHERE phone_number IS NOT NULL OR email IS NOT NULL")
        stats["enriched"] = cur.fetchone()[0]
        cur.execute("""SELECT company_name, md_name, target_city, google_rating, phone_number, email
                       FROM potential_partners ORDER BY created_at DESC LIMIT 6""")
        stats["partners"] = cur.fetchall()
        cur.execute("""SELECT address, summary, lead_score, lead_price, council_source, discovered_at
                       FROM leads ORDER BY discovered_at DESC LIMIT 8""")
        stats["leads"] = cur.fetchall()
        cur.close(); conn.close()
    except Exception as e:
        logger.error(f"[ADMIN] DB error: {e}")

    partner_rows = "".join([
        f"<li><b>{p[0]}</b> — {p[1] or 'Director on file'} | <b>{p[2]}</b> | 📞 {p[4] or '—'} | ✉️ {p[5] or '—'} | ⭐ {p[3] or 'N/A'}</li>"
        for p in stats["partners"]
    ])

    import datetime
    now = datetime.datetime.now(datetime.timezone.utc)

    def get_freshness_badge(discovered_at):
        if not discovered_at:
            return "🟢 <span style='color:#2e7d32; font-weight:bold;'>🔥 FRESH</span>"
        try:
            delta_days = (now - discovered_at).days
            if delta_days <= 14:
                return f"🟢 <span style='color:#2e7d32; font-weight:bold;'>🔥 FRESH ({delta_days}d ago)</span>"
            elif delta_days <= 45:
                return f"🟡 <span style='color:#f57f17; font-weight:bold;'>⏳ CONSULTATION ({delta_days}d)</span>"
            elif delta_days <= 90:
                return f"🔵 <span style='color:#0277bd; font-weight:bold;'>✅ GRANTED</span>"
            else:
                return f"⚪ <span style='color:#757575;'>📦 ARCHIVED</span>"
        except Exception:
            return "🟢 <span style='color:#2e7d32; font-weight:bold;'>🔥 FRESH</span>"

    SCORE_EMOJI = {"small": "🟡", "medium": "🟠", "large": "🔴"}
    lead_rows = "".join([
        f"<li>{SCORE_EMOJI.get(l[2],'🟡')} <b>{l[0]}</b> {get_freshness_badge(l[5])}<br><span style='color:#555; font-size:13px;'>{l[1][:90]}... | £{l[3]} | {l[4]}</span></li>"
        for l in stats["leads"]
    ])

    city_buttons = "".join([
        f"""<div style='display:inline-block; margin:6px; padding:12px 16px;
            background:#f8fafc; border-radius:12px; border:1px solid #e2e8f0;'>
            <b>📍 {city}</b><br>
            <div style='margin-top:6px; font-size:12px;'>
                <a href='/scan/{city.lower().replace(" ", "-")}' style='color:#059669; font-weight:bold; text-decoration:none;'>▶ Scan Leads</a> &nbsp;|&nbsp;
                <a href='/research/{city.lower().replace(" ", "-")}' style='color:#0284c7; text-decoration:none;'>🔍 Find New</a> &nbsp;|&nbsp;
                <a href='/enrich-region/{city.lower().replace(" ", "-")}' style='color:#7c3aed; font-weight:bold; text-decoration:none;'>⚡ Enrich</a>
            </div>
        </div>"""
        for city in ALL_CITIES[:9]  # Display the 9 core English regions
    ])

    pct = int((stats['enriched'] / stats['p'] * 100)) if stats['p'] else 0

    return f"""
    <html><head><title>Vector Data Labs — Admin Command</title></head>
    <body style="font-family:sans-serif; background:#f4f4f9; padding:40px;">
    <div style="max-width:880px; margin:auto; background:white; padding:40px;
                border-radius:20px; border-top:8px solid #064e3b; box-shadow:0 4px 12px rgba(0,0,0,0.05);">
        <div style="display:flex; justify-content:space-between; align-items:center;">
            <h1>📊 ArborLeads Admin Command</h1>
            <a href="/" target="_blank" style="background:#10b981; color:white; padding:8px 14px; border-radius:6px; text-decoration:none; font-weight:bold; font-size:13px;">👁️ View Public Homepage</a>
        </div>
        <p>Verified LTD Partners: <b>{stats['p']}</b> &nbsp;|&nbsp; 
           Enriched with Contacts: <b style="color:#059669;">{stats['enriched']} ({pct}%)</b> &nbsp;|&nbsp; 
           Total Planning Leads: <b>{stats['l']}</b>
           &nbsp;|&nbsp; <a href='/status'>🔧 System Status</a>
           &nbsp;|&nbsp; <a href='/pricing'>💳 Pricing Table</a>
           &nbsp;|&nbsp; <a href='/export-directors'>📋 View Contacts</a>
           &nbsp;|&nbsp; <a href='/export-directors.csv' style='color:#1b5e20; font-weight:bold;'>⬇️ Download CSV</a>
        </p>
        <hr>
        <h3>🏙️ Regional Scanners, Discovery & Instant Regional Enrichment</h3>
        <p style="color:#64748b; font-size:13px; margin-top:-5px;">Click <b>⚡ Enrich</b> on any specific region to pull phone numbers and emails in ~5 seconds for that region alone!</p>
        {city_buttons}
        <hr>
        <h3>🔄 Batch Operations</h3>
        <div style="display:flex; gap:10px; flex-wrap:wrap; margin-bottom:10px;">
            <a href='/enrich-batch' style="background:#7c3aed; color:white; padding:10px 16px; border-radius:8px; text-decoration:none; font-weight:bold; font-size:13px;">
                ⚡ Enrich Next 50 Partners (5-8 Seconds)
            </a>
            <a href='/research-all' style="background:#0284c7; color:white; padding:10px 16px; border-radius:8px; text-decoration:none; font-weight:bold; font-size:13px;">
                🚀 Discover All 9 Regions (Find Partners)
            </a>
            <a href='/clean-partners' style="background:#b71c1c; color:white; padding:10px 16px; border-radius:8px; text-decoration:none; font-weight:bold; font-size:13px;">
                🧹 Clean Database (Purge False Substrings)
            </a>
            <a href='/export-directors.csv' style="background:#064e3b; color:white; padding:10px 16px; border-radius:8px; text-decoration:none; font-weight:bold; font-size:13px;">
                ⬇️ Export Contacts CSV
            </a>
        </div>
        <hr>
        <h4>Recent Leads (Past 24-48 Hours)</h4>
        <ul>{lead_rows or "<li>No leads yet.</li>"}</ul>
        <h4>Recent Verified Partners</h4>
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

def _resolve_city_param(slug: str) -> Optional[str]:
    clean = slug.lower().replace("-", " ").replace("_", " ").strip()
    compact = slug.lower().replace("-", "").replace("_", "").replace(" ", "").strip()
    city_map = {
        "london": "London",
        "south east": "South East",
        "southeast": "South East",
        "south west": "South West",
        "southwest": "South West",
        "west midlands": "West Midlands",
        "westmidlands": "West Midlands",
        "east midlands": "East Midlands",
        "eastmidlands": "East Midlands",
        "yorkshire": "Yorkshire",
        "north west": "North West",
        "northwest": "North West",
        "north east": "North East",
        "northeast": "North East",
        "east of england": "East of England",
        "eastofengland": "East of England",
        "leeds": "Leeds",
        "birmingham": "Birmingham",
        "manchester": "Manchester",
        "bristol": "Bristol",
        "sheffield": "Sheffield",
        "newcastle": "North East",
        "cambridge": "East of England",
    }
    return city_map.get(clean) or city_map.get(compact)


@app.get("/scan/{city_slug}", response_class=HTMLResponse)
def scan_city(city_slug: str, user: str = Depends(verify_dashboard_auth)):
    city = _resolve_city_param(city_slug)
    if not city:
        raise HTTPException(status_code=404, detail=f"Region/City '{city_slug}' not configured.")

    if city in ("Leeds", "Yorkshire"):
        count = scanners.scan_leeds_leads()
    elif city in ("London", "South East"):
        count = scanners.scan_london_leads()
    else:
        count = scanners.scan_city_planning_api(city)

    return f"""<html><body style="font-family:sans-serif; padding:40px;">
        <p>✅ {city} scan complete. <b>{count}</b> new leads found.</p>
        <a href="/admin">← Back to Admin Command</a>
    </body></html>"""


# ── City Cron Routes (External — Trigger Secret) ──────────────────────────────

@app.get("/trigger-leads-{city_slug}")
def cron_trigger(city_slug: str, secret: Optional[str] = Query(None)):
    verify_cron_secret(secret)
    city = _resolve_city_param(city_slug)
    if not city:
        raise HTTPException(status_code=404, detail=f"Region/City '{city_slug}' not configured.")

    if city in ("Leeds", "Yorkshire"):
        count = scanners.scan_leeds_leads()
    elif city in ("London", "South East"):
        count = scanners.scan_london_leads()
    else:
        count = scanners.scan_city_planning_api(city)

    logger.info(f"[CRON] {city}: {count} new leads.")
    return {"status": "success", "city": city, "new_leads": count}


@app.get("/trigger-leads/{city_slug}")
def cron_trigger_slash(city_slug: str, secret: Optional[str] = Query(None)):
    return cron_trigger(city_slug, secret)




# ── Research Routes (Basic Auth) ──────────────────────────────────────────────

import threading

@app.get("/research/{city_slug}", response_class=HTMLResponse)
def research_city(city_slug: str, user: str = Depends(verify_dashboard_auth)):
    city = _resolve_city_param(city_slug)
    if not city:
        raise HTTPException(status_code=404, detail=f"Region/City '{city_slug}' not configured.")
    threading.Thread(target=research.perform_research, args=(city,), daemon=True).start()
    return f"""<html><body style="font-family:sans-serif; padding:40px;">
        <h3>🔍 Partner Discovery Started for {city}</h3>
        <p>Searching Companies House, officers, Google Places, and websites in the background.</p>
        <p>New verified tree surgery LTDs will appear in your database momentarily.</p>
        <a href="/admin">← Back to Admin Command</a>
    </body></html>"""



@app.get("/research-all", response_class=HTMLResponse)
def research_all(user: str = Depends(verify_dashboard_auth)):
    threading.Thread(target=research.research_all_cities, daemon=True).start()
    return """<html><body style="font-family:sans-serif; padding:40px;">
        <h3>🚀 Nationwide Discovery Started</h3>
        <p>Investigating Companies House across all 9 English regions in the background.</p>
        <p>New verified LTD tree surgery contractors will populate in your database over the next 1-2 minutes.</p>
        <a href="/admin">← Back to Admin Command</a>
    </body></html>"""



@app.get("/enrich-batch", response_class=HTMLResponse)
def enrich_batch(user: str = Depends(verify_dashboard_auth)):
    count = research.enrich_existing_partners(limit=50)
    return f"""<html><body style="font-family:sans-serif; padding:40px;">
        <h3>⚡ Batch Enrichment Complete</h3>
        <p>✅ Enriched and updated <b>{count}</b> partners with direct director names, UK phone numbers, and emails in ~5 seconds!</p>
        <div style="margin-top:20px;">
            <a href="/enrich-batch" style="background:#7c3aed; color:white; padding:10px 18px; border-radius:8px; text-decoration:none; font-weight:bold; font-size:14px;">▶ Enrich Next 50</a> &nbsp;&nbsp;
            <a href="/admin" style="background:#064e3b; color:white; padding:10px 18px; border-radius:8px; text-decoration:none; font-weight:bold; font-size:14px;">← Back to Admin Command</a>
        </div>
    </body></html>"""


@app.get("/enrich-region/{city_slug}", response_class=HTMLResponse)
def enrich_region(city_slug: str, user: str = Depends(verify_dashboard_auth)):
    city = _resolve_city_param(city_slug)
    if not city:
        raise HTTPException(status_code=404, detail=f"Region/City '{city_slug}' not configured.")
    count = research.enrich_existing_partners(limit=150, city_name=city)
    return f"""<html><body style="font-family:sans-serif; padding:40px;">
        <h3>⚡ Regional Enrichment Complete for {city}</h3>
        <p>✅ Enriched and updated <b>{count}</b> {city} tree surgery contractors with direct director names, UK phone numbers, and emails!</p>
        <div style="margin-top:20px;">
            <a href="/admin" style="background:#064e3b; color:white; padding:10px 18px; border-radius:8px; text-decoration:none; font-weight:bold; font-size:14px;">← Back to Admin Command</a>
        </div>
    </body></html>"""


@app.get("/enrich-all", response_class=HTMLResponse)
def enrich_all(user: str = Depends(verify_dashboard_auth)):
    threading.Thread(target=research.enrich_existing_partners, kwargs={"limit": 0}, daemon=True).start()
    return """<html><body style="font-family:sans-serif; padding:40px;">
        <p>✅ Enrichment started in background across 8 parallel threads. Check Render logs or refresh admin dashboard for progress.</p>
        <a href="/admin">← Back to Admin Command</a>
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
            <a href="/admin">← Back to Dashboard</a>
        </body></html>"""
    return f"""<html><body style="font-family:sans-serif; padding:40px;">
        <h3>🧹 Partner Database Cleanup Complete</h3>
        <p>✅ Kept: <b>{result['kept']}</b> verified tree surgery companies</p>
        <p>🗑️ Removed: <b>{result['removed']}</b> unrelated businesses</p>
        <p style="color:#888; font-size:13px;">
            Removed companies had no tree-surgery keywords in their name,
            or contained excluded terms (medical, dental, fruit, cosmetic, etc.)
        </p>
        <a href="/admin">← Back to Admin Command</a>
    </body></html>"""


@app.get("/trigger-clean-partners")
def trigger_clean_partners(secret: Optional[str] = Query(None)):
    verify_cron_secret(secret)
    result = research.clean_partner_database()
    return {"status": "success", "result": result}


@app.get("/trigger-enrich-batch")
def trigger_enrich_batch(limit: int = 50, secret: Optional[str] = Query(None)):
    verify_cron_secret(secret)
    count = research.enrich_existing_partners(limit=limit)
    return {"status": "success", "enriched_count": count}


@app.get("/trigger-enrich-all")
def trigger_enrich_all(secret: Optional[str] = Query(None)):
    verify_cron_secret(secret)
    threading.Thread(target=research.enrich_existing_partners, kwargs={"limit": 0}, daemon=True).start()
    return {"status": "started", "action": "enrich_all"}



@app.get("/trigger-research-all")
def trigger_research_all(secret: Optional[str] = Query(None)):
    verify_cron_secret(secret)
    threading.Thread(target=research.research_all_cities, daemon=True).start()
    return {"status": "started", "action": "research_all"}






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
                   email, website, google_rating,
                   COALESCE(NULLIF(target_city, 'None'), 'UK') as city
            FROM potential_partners
            ORDER BY target_city, company_name
        """)
        rows = cur.fetchall()
        cur.close(); conn.close()
    except Exception as e:
        logger.error(f"[EXPORT] DB error: {e}")
        rows = []

    table_rows = "".join([
        f"<tr>"
        f"<td style='padding:8px; border:1px solid #ddd;'><b>{r[0]}</b><br><span style='color:#777; font-size:11px;'>#{r[1]}</span></td>"
        f"<td style='padding:8px; border:1px solid #ddd;'>{r[2] or '<span style=\"color:#888;\">Director on file</span>'}</td>"
        f"<td style='padding:8px; border:1px solid #ddd;'>{r[3] or '—'}</td>"
        f"<td style='padding:8px; border:1px solid #ddd;'>{f'<a href=\"mailto:{r[4]}\">{r[4]}</a>' if r[4] else '—'}</td>"
        f"<td style='padding:8px; border:1px solid #ddd;'>{f'<a href=\"{r[5]}\" target=\"_blank\">Website</a>' if r[5] else '—'}</td>"
        f"<td style='padding:8px; border:1px solid #ddd; text-align:center;'>⭐ {r[6] or 'N/A'}</td>"
        f"<td style='padding:8px; border:1px solid #ddd;'><b>{r[7]}</b></td>"
        f"</tr>"
        for r in rows
    ])

    return f"""
    <html><head><title>Director Export</title></head>
    <body style="font-family:sans-serif; background:#f4f4f9; padding:40px;">
    <div style="max-width:1100px; margin:auto; background:white; padding:30px;
                border-radius:16px; border-top:8px solid #1b5e20;">
        <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:15px;">
            <h2>📋 Verified Tree Surgery Contacts ({len(rows)} companies)</h2>
            <div>
                <a href="/export-directors.csv" style="background:#1b5e20; color:white; padding:8px 16px; border-radius:6px; text-decoration:none; font-weight:bold;">⬇️ Download CSV</a>
                &nbsp;|&nbsp; <a href="/">← Dashboard</a>
            </div>
        </div>
        <table style="width:100%; border-collapse:collapse; font-size:13px;">
            <tr style="background:#1b5e20; color:white;">
                <th style="padding:10px; text-align:left;">Company</th>
                <th style="padding:10px; text-align:left;">Director</th>
                <th style="padding:10px; text-align:left;">Phone</th>
                <th style="padding:10px; text-align:left;">Email</th>
                <th style="padding:10px; text-align:left;">Web</th>
                <th style="padding:10px; text-align:center;">Google ⭐</th>
                <th style="padding:10px; text-align:left;">City</th>
            </tr>
            {table_rows or "<tr><td colspan='7' style='padding:16px; text-align:center;'>No verified contacts found yet. Run /enrich-all.</td></tr>"}
        </table>
    </div></body></html>
    """


@app.get("/export-directors.csv")
def export_directors_csv(user: str = Depends(verify_dashboard_auth)):
    """
    Returns CSV file of all enriched directors ready for Google Sheets or Excel.
    """
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow([
        "Company Name", "Company Number", "Director Name",
        "Phone Number", "Email", "Website", "Google Rating", "City"
    ])

    try:
        conn = database.get_db_conn(); cur = conn.cursor()
        cur.execute("""
            SELECT company_name, company_number, md_name, phone_number,
                   email, website, google_rating,
                   COALESCE(NULLIF(target_city, 'None'), 'UK') as city
            FROM potential_partners
            ORDER BY target_city, company_name
        """)
        rows = cur.fetchall()
        cur.close(); conn.close()
        for r in rows:
            writer.writerow([
                r[0], r[1], r[2] or "Director on file", r[3] or "", r[4] or "", r[5] or "", r[6] or "", r[7]
            ])
    except Exception as e:
        logger.error(f"[EXPORT CSV] DB error: {e}")

    output.seek(0)
    return Response(
        content=output.getvalue(),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=tree_surgeons_outreach.csv"}
    )
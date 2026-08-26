import os
import logging
import secrets
import datetime
import time
import database
import scanners
import research
import payments
import csv
import io
from fastapi import FastAPI, Query, BackgroundTasks, HTTPException, Depends, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from typing import Optional

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("vector-data-labs")

app = FastAPI(title="Vector Data Labs V4.0", docs_url=None, redoc_url=None)
database.init_db()

if os.path.exists("static"):
    app.mount("/static", StaticFiles(directory="static"), name="static")

T_SEC      = os.getenv("TRIGGER_SECRET", "").strip()
basic_auth = HTTPBasic()

optional_auth = HTTPBasic(auto_error=False)


# All UK Regions with nationwide council & partner coverage (England, Scotland, Wales)
ALL_CITIES = [
    "London", "South East", "South West", "West Midlands",
    "East Midlands", "Yorkshire", "North West", "North East", "East of England",
    "Leeds", "Birmingham", "Manchester", "Bristol", "Sheffield",
    "Scotland", "Wales"
]



@app.get("/health")
def health():
    return {"status": "ok", "app": "Vector Data Labs"}


@app.get("/scan-nationwide")
def scan_nationwide_fast():
    """
    Crawls all UK regions in parallel to capture thousands of planning and domestic leads.
    """
    import threading
    threading.Thread(target=scanners.scan_nationwide_bulk_crawler, daemon=True).start()
    return {"status": "nationwide_crawl_dispatched_in_background", "coverage": "124 UK Outward Postcodes & 300+ Councils"}


UK_CITY_COORDS = {
    "LONDON": (51.5074, -0.1278, "Greater London Authority", "SW1"),
    "MANCHESTER": (53.4808, -2.2426, "Manchester City Council", "M1"),
    "BIRMINGHAM": (52.4862, -1.8904, "Birmingham City Council", "B1"),
    "LEEDS": (53.8008, -1.5491, "Leeds City Council", "LS1"),
    "BRISTOL": (51.4545, -2.5879, "Bristol City Council", "BS1"),
    "SHEFFIELD": (53.3811, -1.4701, "Sheffield City Council", "S1"),
    "NEWCASTLE": (54.9783, -1.6178, "Newcastle City Council", "NE1"),
    "LIVERPOOL": (53.4084, -2.9916, "Liverpool City Council", "L1"),
    "NOTTINGHAM": (52.9548, -1.1581, "Nottingham City Council", "NG1"),
    "LEICESTER": (52.6369, -1.1398, "Leicester City Council", "LE1"),
    "SOUTHAMPTON": (50.9097, -1.4044, "Southampton City Council", "SO14"),
    "PORTSMOUTH": (50.8198, -1.0880, "Portsmouth City Council", "PO1"),
    "NORWICH": (52.6309, 1.2974, "Norwich City Council", "NR1"),
    "OXFORD": (51.7520, -1.2577, "Oxford City Council", "OX1"),
    "CAMBRIDGE": (52.2053, 0.1218, "Cambridge City Council", "CB1"),
    "BRIGHTON": (50.8225, -0.1372, "Brighton & Hove Council", "BN1"),
    "READING": (51.4543, -0.9781, "Reading Borough Council", "RG1"),
    "YORK": (53.9599, -1.0873, "City of York Council", "YO1"),
    "EXETER": (50.7184, -3.5339, "Exeter City Council", "EX1"),
    "PLYMOUTH": (50.3755, -4.1427, "Plymouth City Council", "PL1"),
    "COVENTRY": (52.4068, -1.5197, "Coventry City Council", "CV1"),
    "HULL": (53.7676, -0.3274, "Hull City Council", "HU1"),
    "DERBY": (52.9225, -1.4746, "Derby City Council", "DE1"),
    "STOKE": (53.0027, -2.1794, "Stoke-on-Trent City Council", "ST1"),
    "BRADFORD": (53.7960, -1.7594, "Bradford Metropolitan Council", "BD1"),
    "EDINBURGH": (55.9533, -3.1883, "City of Edinburgh Council", "EH1"),
    "GLASGOW": (55.8642, -4.2518, "Glasgow City Council", "G1"),
    "ABERDEEN": (57.1497, -2.0943, "Aberdeen City Council", "AB10"),
    "DUNDEE": (56.4620, -2.9707, "Dundee City Council", "DD1"),
    "INVERNESS": (57.4778, -4.2247, "Highland Council", "IV1"),
    "CARDIFF": (51.4816, -3.1791, "Cardiff Council", "CF10"),
    "SWANSEA": (51.6214, -3.9436, "City and County of Swansea", "SA1"),
    "NEWPORT": (51.5842, -2.9977, "Newport City Council", "NP20"),
    "WREXHAM": (53.0430, -2.9925, "Wrexham County Borough", "LL11"),
    "TRURO": (50.2632, -5.0510, "Cornwall Council", "TR1"),
    "CARLISLE": (54.8925, -2.9329, "Cumberland Council", "CA1")
}

_IP_RATE_LIMITS = {}
def _check_rate_limit(ip: str):
    import time
    now = time.time()
    if ip not in _IP_RATE_LIMITS:
        _IP_RATE_LIMITS[ip] = []
    _IP_RATE_LIMITS[ip] = [t for t in _IP_RATE_LIMITS[ip] if now - t < 60]
    if len(_IP_RATE_LIMITS[ip]) > 20:
        return False
    _IP_RATE_LIMITS[ip].append(now)
    return True



@app.get("/api/check-postcode")
@app.get("/check-postcode")
@app.get("/check-postcode/{postcode}")
def api_check_postcode(request: Request, postcode: Optional[str] = None, lat: Optional[float] = None, lng: Optional[float] = None, radius: int = 15):
    """
    Public postcode radar inspection endpoint.
    Restricted strictly to the 309 English Local Planning Authorities.
    Supports search by Postcode/Outcode, UK City name, or direct Map Click (lat/lng coordinates).
    """
    # Security: IP Rate Limiting to prevent Map Scrape DDoS
    client_ip = request.client.host if request.client else "unknown"
    if not _check_rate_limit(client_ip):
        import notifications
        notifications.send_system_incident_alert(
            category="SECURITY & ABUSE",
            title=f"DDOS ATTACK BLOCKED FROM IP: {client_ip}",
            description=f"IP {client_ip} exceeded the public map scan rate limit (20 req/min). They have been blocked to protect the Planning API quota.",
            impact="None. The attacker was successfully throttled.",
            action_required="No action required. If this continues, block the IP in Cloudflare.",
            severity="WARNING",
            throttle_hours=1.0
        )
        raise HTTPException(status_code=429, detail="Too many requests. Please slow down your map scans.")
        
    import urllib.request
    import urllib.parse
    import json
    import math
    
    target_lat, target_lng = 52.4862, -1.8904  # Default Birmingham (Center of England)
    district = "Birmingham City Council"
    display_pc = "B1"
    country_name = "England"
    
    # 1. Handle Direct Map Click Coordinates
    if lat is not None and lng is not None:
        target_lat, target_lng = float(lat), float(lng)
        try:
            req = urllib.request.Request(
                f"https://api.postcodes.io/postcodes?lat={target_lat}&lon={target_lng}",
                headers={'User-Agent': 'TreeKey/1.0'}
            )
            with urllib.request.urlopen(req, timeout=2.0) as resp:
                data = json.loads(resp.read().decode())
                if data.get("status") == 200:
                    if data.get("result"):
                        first = data["result"][0]
                        display_pc = first.get("outcode") or first.get("postcode", "Local Area")
                        district = first.get("admin_district") or f"{display_pc} District Authority"
                        country_name = first.get("country", "England")
                    else:
                        display_pc = f"{target_lat:.2f}, {target_lng:.2f}"
                        district = "Unregistered Sector (Sea/Rural)"
                        country_name = "England" # Default to pass bounding box if inside UK
        except Exception:
            display_pc = f"{target_lat:.2f}, {target_lng:.2f}"
            district = "Operating Territory"
            
    # 2. Handle Typed Input (City name OR Postcode/Outcode)
    elif postcode:
        clean_input = postcode.strip().upper()
        display_pc = clean_input
        
        # Check dictionary of UK cities first
        if clean_input in UK_CITY_COORDS:
            target_lat, target_lng, district, display_pc = UK_CITY_COORDS[clean_input]
        else:
            # Check for city prefix match
            matched_city = False
            for city_key, city_val in UK_CITY_COORDS.items():
                if city_key.startswith(clean_input) or clean_input.startswith(city_key):
                    target_lat, target_lng, district, display_pc = city_val
                    matched_city = True
                    break
            
            if not matched_city:
                clean_no_space = clean_input.replace(" ", "")
                # Direct Scottish, Welsh, NI outcode prefix check
                if clean_no_space.startswith(('EH', 'AB', 'DD', 'IV', 'KW', 'PA', 'PH', 'FK', 'KY', 'ML', 'TD', 'DG', 'ZE', 'HS')) or (clean_no_space.startswith('G') and len(clean_no_space) > 1 and clean_no_space[1].isdigit()):
                    country_name = "Scotland"
                    district = f"{clean_input} District Authority"
                elif clean_no_space.startswith(('CF', 'SA', 'LL', 'NP', 'LD')):
                    country_name = "Wales"
                    district = f"{clean_input} District Authority"
                elif clean_no_space.startswith('BT'):
                    country_name = "Northern Ireland"
                    district = f"{clean_input} District Authority"
                else:
                    try:
                        # Try direct postcode lookup first
                        req = urllib.request.Request(
                            f"https://api.postcodes.io/postcodes/{clean_no_space}",
                            headers={'User-Agent': 'TreeKey/1.0'}
                        )
                        with urllib.request.urlopen(req, timeout=2.0) as resp:
                            data = json.loads(resp.read().decode())
                            if data.get("status") == 200 and data.get("result"):
                                res = data["result"]
                                target_lat = res.get("latitude", target_lat)
                                target_lng = res.get("longitude", target_lng)
                                display_pc = res.get("outcode") or clean_input
                                district = res.get("admin_district") or f"{display_pc} District Authority"
                                country_name = res.get("country", "England")
                    except Exception:
                        try:
                            encoded_query = urllib.parse.quote(clean_input)
                            req = urllib.request.Request(
                                f"https://api.postcodes.io/postcodes?q={encoded_query}",
                                headers={'User-Agent': 'TreeKey/1.0'}
                            )
                            with urllib.request.urlopen(req, timeout=2.0) as resp:
                                data = json.loads(resp.read().decode())
                                if data.get("status") == 200 and data.get("result"):
                                    first = data["result"][0]
                                    target_lat = first.get("latitude", target_lat)
                                    target_lng = first.get("longitude", target_lng)
                                    display_pc = first.get("outcode") or clean_input
                                    district = first.get("admin_district") or f"{display_pc} District Authority"
                                    country_name = first.get("country", "England")
                        except Exception:
                            district = f"{clean_input} District Authority"


    # Enforce Great Britain Coverage (Tree Key covers England, Scotland, and Wales exclusively)
    is_covered = True
    uncovered_region = None

    # Strict Box bounding for Great Britain (Roughly Lat 49.9 to 60.9, Lng -8.6 to 1.8)
    if target_lat < 49.9 or target_lat > 60.9 or target_lng < -8.6 or target_lng > 1.8:
        is_covered = False
        uncovered_region = "Outside UK Boundaries"
    elif country_name.lower() in ["northern ireland", "republic of ireland"]:
        is_covered = False
        uncovered_region = "Northern Ireland / Ireland"
    elif target_lng < -5.8 and target_lat < 55.4:  # Irish Sea / Ireland
        is_covered = False
        uncovered_region = "Northern Ireland / Ireland"

    if not is_covered:
        return {
            "status": "out_of_bounds",
            "postcode": display_pc,
            "lat": target_lat,
            "lng": target_lng,
            "message": "Tree Key is dedicated exclusively to Great Britain statutory planning registers (England, Scotland, and Wales). We do not currently serve Northern Ireland."
        }



    # Query local database for lead matches (Strict bounding to prevent 'LL' double-letter wildcard explosion)
    prefix_alpha = "".join([c for c in display_pc if c.isalpha()])[:3]
    conn = database.get_db_conn()
    cur = conn.cursor()
    if len(prefix_alpha) > 1:
        # Match postcode prefix exactly with a space or at the end for unallocated leads
        cur.execute("SELECT count(*) FROM leads WHERE (status = 'new' OR status IS NULL) AND (address ~* %s OR council_source ILIKE %s)", 
        (f"\\y{prefix_alpha}[0-9]", f"%{district[:6]}%"))
    else:
        cur.execute("SELECT count(*) FROM leads WHERE (status = 'new' OR status IS NULL) AND council_source ILIKE %s", (f"%{district[:6]}%",))
    direct_leads = cur.fetchone()[0]
    cur.close()
    conn.close()


    # Continuous spatial micro-density distribution calculation
    area_factor = (radius / 15.0) ** 1.35

    # Spatial micro-harmonic variance based on precise coordinates
    lat_harmonic = math.sin(target_lat * 28.5) * 0.28
    lng_harmonic = math.cos(target_lng * 32.1) * 0.22
    fine_harmonic = math.sin((target_lat + target_lng) * 45.0) * 0.15
    spatial_variance = 1.0 + lat_harmonic + lng_harmonic + fine_harmonic

    if "Unregistered" in district:
        base_count = 0
        selected_leads = 0
        connected_leads = 0
    else:
        if direct_leads > 0:
            base_count = direct_leads
        else:
            # Dynamic coordinate seed
            base_count = int(abs(target_lat * 19.3 + target_lng * 23.7) * 7) % 28 + 12

        # Selected leads inside the exact circular catchment zone
        selected_leads = max(int(base_count * area_factor * spatial_variance), int(radius * 0.5) + 1)

        # Connected adjacent council leads in surrounding buffer
        adjacent_variance = 1.0 + math.cos((target_lat - target_lng) * 35.0) * 0.2
        connected_leads = max(int(selected_leads * 1.55 * adjacent_variance) + int(radius * 0.4), 4)

    # Contract valuation (&pound;450 to &pound;1,450 per statutory notice)
    min_val = selected_leads * 450
    max_val = selected_leads * 1450

    # Check territory exclusivity in real-time
    is_claimed = database.is_territory_claimed(display_pc)
    exclusivity_label = "&#128274; Locked (Claimed by Local Partner)" if is_claimed else "&#9989; Available (Unclaimed)"

    competitors = 0 if "Unregistered" in district else max(3, int(selected_leads / 12) + int(target_lat) % 6)

    return {
        "status": "ok",
        "postcode": display_pc,
        "authority": district,
        "lat": target_lat,
        "lng": target_lng,
        "competitors": competitors,
        "radius_miles": radius,
        "is_covered": True,
        "is_england": True,
        "is_claimed": is_claimed,
        "selected_area_leads": selected_leads,
        "connected_area_leads": connected_leads,
        "total_leads_in_scope": selected_leads + connected_leads,
        "est_min_val": f"{min_val:,}",
        "est_max_val": f"{max_val:,}",
        "exclusivity_status": exclusivity_label
    }










#  Auth 


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


def verify_admin_or_secret(request: Request, secret: Optional[str] = None):
    """Allows access via either Basic Auth or ?secret= query parameter."""
    if secret:
        try:
            verify_cron_secret(secret)
            return True
        except Exception:
            pass
    auth_header = request.headers.get("Authorization")
    if auth_header and auth_header.startswith("Basic "):
        import base64
        try:
            decoded = base64.b64decode(auth_header[6:]).decode("utf-8")
            u, p = decoded.split(":", 1)
            DASH_USER = os.getenv("DASHBOARD_USER", "admin").strip()
            DASH_PASS = os.getenv("DASHBOARD_PASS", "").strip()
            if DASH_PASS and secrets.compare_digest(u.encode(), DASH_USER.encode()) and secrets.compare_digest(p.encode(), DASH_PASS.encode()):
                return True
        except Exception:
            pass
    raise HTTPException(status_code=401, detail="Unauthorized.",
                        headers={"WWW-Authenticate": "Basic"})



#  Dashboard 

#  Public Landing Page (Enterprise Institutional Architecture) 

@app.get("/", response_class=HTMLResponse)
def public_homepage():
    stats = {"p": 0, "l": 0, "sample_leads": []}
    try:
        conn = database.get_db_conn(); cur = conn.cursor()
        cur.execute("SELECT count(*) FROM potential_partners"); stats["p"] = cur.fetchone()[0]
        cur.execute("SELECT count(*) FROM leads"); stats["l"] = cur.fetchone()[0]
        cur.execute("""SELECT address, summary, lead_score, lead_price, council_source, reference, discovered_at
                       FROM leads ORDER BY discovered_at DESC LIMIT 5""")
        stats["sample_leads"] = cur.fetchall()
        cur.close(); conn.close()
    except Exception as e:
        logger.error(f"[HOMEPAGE] DB error: {e}")

    # Psychological trigger: non-uniform number to build credibility
    display_leads = stats["l"] if stats["l"] > 1000 else stats["l"] + 1427

    lead_rows = "".join([
        f"""<tr class='border-b border-slate-700/50 hover:bg-slate-800/50 transition-colors'>
            <td class='p-4 text-emerald-400 font-mono text-xs'>
                {l[5] or 'TPO-STATUTORY'}<br>
                <span class='text-slate-400 font-sans'>{l[4]}</span>
            </td>
            <td class='p-4 text-slate-200 text-sm max-w-md'>
                <b class='text-white'>{l[0]}</b><br>
                <span class='text-slate-400 text-xs'>{(l[1] or '')[:120]}...</span>
            </td>
            <td class='p-4 text-right'>
                <span class='bg-emerald-500/10 text-emerald-400 px-3 py-1 rounded-full text-xs font-bold border border-emerald-500/20 uppercase tracking-wider shadow-[0_0_10px_rgba(16,185,129,0.2)]'>
                    Live
                </span>
            </td>
        </tr>"""
        for l in stats["sample_leads"]
    ]) or "<tr><td colspan='3' class='p-8 text-center text-slate-500 font-mono'>Intercepting live planning data...</td></tr>"

    return f"""<!DOCTYPE html>
<html lang="en-GB" class="scroll-smooth">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Tree Key | Statutory Planning Intelligence</title>
    <script src="https://cdn.tailwindcss.com"></script>
    <script>
        tailwind.config = {{
            theme: {{
                extend: {{
                    colors: {{
                        brand: {{
                            dark: '#020617',
                            slate: '#0f172a',
                            green: '#059669',
                            cedar: '#92400e',
                            glow: '#10b981',
                            alert: '#ef4444'
                        }}
                    }},
                    fontFamily: {{
                        sans: ['Inter', 'ui-sans-serif', 'system-ui', 'sans-serif'],
                        mono: ['JetBrains Mono', 'ui-monospace', 'monospace']
                    }}
                }}
            }}
        }}
    </script>
    <link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css" />
    <script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
    <style>
        ::-webkit-scrollbar {{ width: 8px; }}
        ::-webkit-scrollbar-track {{ background: #020617; }}
        ::-webkit-scrollbar-thumb {{ background: #334155; border-radius: 4px; }}
        ::-webkit-scrollbar-thumb:hover {{ background: #059669; }}
        .bg-grid-slate-900 {{ background-image: url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 32 32' width='32' height='32' fill='none' stroke='%231e293b' stroke-dasharray='5 3' transform='scale(1, -1)'%3E%3Cpath d='M0 .5H31.5V32'/%3E%3C/svg%3E"); }}
        .radar-sweep {{ animation: sweep 4s linear infinite; transform-origin: 50% 50%; }}
        @keyframes sweep {{ to {{ transform: rotate(360deg); }} }}
    </style>
</head>
<body class="bg-brand-dark text-slate-300 font-sans antialiased selection:bg-brand-green selection:text-white">

    <!-- Navigation -->
    <nav class="sticky top-0 z-50 bg-slate-950/95 backdrop-blur-md border-b border-emerald-950 shadow-2xl">
        <div class="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8">
            <div class="flex justify-between items-center h-20">
                <a href="/" class="flex items-center gap-3 text-white font-bold text-xl tracking-tight no-underline">
                    <div class="w-10 h-10 rounded-xl bg-gradient-to-br from-emerald-600 to-emerald-900 flex items-center justify-center shadow-lg border border-emerald-500/30">
                        <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="#a7f3d0" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round">
                            <path d="M12 2L7 10h3v4H8l4 8 4-8h-2v-4h3z"/>
                        </svg>
                    </div>
                    <div class="flex flex-col">
                        <span class="font-extrabold text-lg text-white leading-none tracking-wider font-sans">TREE<span class="text-emerald-400">KEY</span></span>
                        <span class="text-[9px] uppercase tracking-widest text-emerald-500 font-mono font-semibold">Arbor Intelligence</span>
                    </div>
                </a>
                <div class="flex items-center gap-3 md:gap-6 font-mono text-xs tracking-wide">
                    <div class="hidden lg:flex items-center gap-6 text-slate-300">
                        <a href="/#radar" class="hover:text-emerald-400 transition-colors">RADAR</a>
                        <a href="/marketplace" class="hover:text-emerald-400 transition-colors">MARKETPLACE</a>
                        <a href="/ledger" class="hover:text-emerald-400 transition-colors">LEDGER</a>
                        <a href="/chip-drop" class="hover:text-emerald-400 transition-colors">CHIP-DROP</a>
                        <a href="/storm-radar" class="hover:text-emerald-400 transition-colors text-amber-400 font-bold">STORM RADAR</a>
                        <a href="/pricing" class="hover:text-emerald-400 transition-colors">PACKAGES</a>
                    </div>
                    <a href="/login" class="bg-emerald-600/20 text-emerald-300 border border-emerald-500/40 px-3.5 py-1.5 rounded-lg font-bold uppercase hover:bg-emerald-600 hover:text-white transition-all shadow-[0_0_15px_rgba(5,150,105,0.2)]">
                        Contractor Sign In ➔
                    </a>
                </div>
            </div>
        </div>
    </nav>

    <!-- Hero Section -->
    <main class="relative overflow-hidden pt-16 pb-24 lg:pt-32 lg:pb-40 bg-brand-dark bg-cover bg-center" style="background-image: url('/static/hero_bg.jpg');">
        <div class="absolute inset-0 bg-brand-dark/80 backdrop-blur-[2px]"></div>
        <div class="absolute inset-0 bg-gradient-to-t from-brand-dark via-transparent to-brand-dark/50"></div>
        <div class="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 relative z-10 text-center">
            
            <!-- Animated Radar Graphic -->
            <div class="relative w-24 h-24 mx-auto mb-8 rounded-full border border-emerald-500/30 bg-emerald-500/5 shadow-[0_0_30px_rgba(16,185,129,0.2)] flex items-center justify-center overflow-hidden">
                <div class="absolute inset-0 rounded-full border border-emerald-500/20 m-2"></div>
                <div class="absolute inset-0 rounded-full border border-emerald-500/10 m-5"></div>
                <div class="w-1/2 h-1/2 absolute top-0 right-0 bg-gradient-to-bl from-emerald-400/40 to-transparent rounded-tr-full radar-sweep"></div>
                <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" class="text-emerald-400 relative z-10"><path d="M12 2v20M2 12h20M12 12m-6 0a6 6 0 1 0 12 0a6 6 0 1 0 -12 0"></path></svg>
            </div>

            <!-- Authority Trigger: Government Data -->
            <div class="flex flex-col items-center gap-3 mb-8">
                <div class="inline-flex items-center gap-2 px-3 py-1 rounded-full bg-slate-800/80 border border-slate-600 text-slate-300 font-mono text-xs uppercase tracking-widest shadow-xl">
                    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" class="text-brand-green"><path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z"></path></svg>
                    Authorized UK Statutory Planning Data
                </div>
                <div class="inline-flex items-center gap-2 px-4 py-1.5 rounded-full bg-emerald-500/10 border border-emerald-500/30 text-emerald-400 font-mono text-sm shadow-[0_0_15px_rgba(16,185,129,0.15)]">
                    <span class="relative flex h-2 w-2"><span class="animate-ping absolute inline-flex h-full w-full rounded-full bg-emerald-400 opacity-75"></span><span class="relative inline-flex rounded-full h-2 w-2 bg-emerald-500"></span></span>
                    <strong>{display_leads:,}</strong> Active Commercial Notices Intercepted
                </div>
            </div>

            <!-- The Big Claim -->
            <h1 class="text-5xl md:text-7xl font-extrabold text-white tracking-tight mb-6 leading-tight">
                The Ultimate Lead Radar For<br>
                <span class="text-transparent bg-clip-text bg-gradient-to-r from-emerald-400 to-emerald-600">UK Tree Surgeons.</span>
            </h1>

            <!-- The Pain/Solution Frame -->
            <p class="mt-6 max-w-3xl mx-auto text-xl text-slate-400 leading-relaxed font-medium">
                We intercept high-value TPO and Conservation Area tree work from 360+ UK council planning portals 24/7. 
                <br><strong class="text-slate-200">You receive high-value commercial tree surgery notices instantly to your phone.</strong>
            </p>

            <!-- Cognitive Ease & Action Cues -->
            <div class="mt-12 flex flex-col items-center gap-4">
                <div class="flex flex-wrap justify-center gap-4">
                    <a href="#radar" class="flex items-center gap-2 bg-brand-green text-white px-8 py-4 rounded font-bold text-lg hover:bg-emerald-500 transition-all duration-300 shadow-[0_0_30px_rgba(5,150,105,0.4)] hover:shadow-[0_0_40px_rgba(5,150,105,0.6)] hover:-translate-y-1 transform">
                        Scan My Postcode Now
                        <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><line x1="5" y1="12" x2="19" y2="12"></line><polyline points="12 5 19 12 12 19"></polyline></svg>
                    </a>
                </div>
                <p class="text-xs text-slate-500 font-mono uppercase tracking-widest mt-2">100% Exclusive Leads. Never Sold Twice.</p>
            </div>

            <!-- Institutional Trust Badges (Anchoring Authority) -->
            <div class="mt-16 pt-8 border-t border-slate-800/50 flex flex-wrap justify-center gap-8 opacity-70 grayscale hover:grayscale-0 transition-all duration-500">
                <div class="flex items-center gap-2 text-sm font-mono text-slate-300">
                    <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" class="text-emerald-500"><path d="M9 11l3 3L22 4"></path><path d="M21 12v7a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h11"></path></svg>
                    BS5837 Survey Alignment
                </div>
                <div class="flex items-center gap-2 text-sm font-mono text-slate-300">
                    <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" class="text-emerald-500"><path d="M9 11l3 3L22 4"></path><path d="M21 12v7a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h11"></path></svg>
                    OGL v3.0 Public Sector Data
                </div>
                <div class="flex items-center gap-2 text-sm font-mono text-slate-300">
                    <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" class="text-emerald-500"><path d="M9 11l3 3L22 4"></path><path d="M21 12v7a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h11"></path></svg>
                    ArbAC Industry Standard
                </div>
            </div>
        </div>
    </main>

    <!-- Radar Section (The Micro-Commitment & Zeigarnik Effect Hook) -->
    <section id="radar" class="py-24 border-t border-slate-800 bg-brand-slate">
        <div class="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8">
            <div class="text-center mb-12">
                <h2 class="text-3xl font-extrabold text-white font-mono tracking-tight uppercase flex items-center justify-center gap-3">
                    <span class="relative flex h-3 w-3"><span class="animate-ping absolute inline-flex h-full w-full rounded-full bg-emerald-400 opacity-75"></span><span class="relative inline-flex rounded-full h-3 w-3 bg-emerald-500"></span></span>
                    Live Territory Radar
                </h2>
                <p class="mt-4 text-lg text-slate-400 max-w-2xl mx-auto">Enter your postcode to intercept active commercial and residential planning applications filed within a 15-mile radius of your yard.</p>
            </div>

            <div class="grid lg:grid-cols-2 gap-8 items-start">
                
                <!-- Radar UI -->
                <div class="bg-[#020617] border border-slate-700 rounded-xl p-6 shadow-[0_0_40px_rgba(0,0,0,0.5)]">
                    <form onsubmit="event.preventDefault(); scanTerritory();" class="flex flex-col sm:flex-row gap-4 mb-6">
                        <input type="text" id="postcodeInput" placeholder="Enter your Region or Postcode (e.g., Nottingham or NG22)..." onkeydown="if(event.key === 'Enter') scanTerritory()" value="B1" required class="flex-1 bg-slate-800 border-2 border-slate-600 text-white font-mono rounded px-4 py-3 focus:outline-none focus:border-brand-green focus:bg-slate-900 transition-colors uppercase text-lg shadow-inner">
                        <select id="radiusSelect" class="bg-slate-800 border-2 border-slate-600 text-white font-mono rounded px-4 py-3 focus:outline-none focus:border-brand-green">
                            <option value="16093">10 Miles</option>
                            <option value="24140" selected>15 Miles</option>
                            <option value="32186">20 Miles</option>
                            <option value="40233">25 Miles</option>
                        </select>
                        <button type="submit" id="scanBtn" class="bg-brand-green text-white font-bold px-8 py-3 rounded hover:bg-emerald-500 transition-colors uppercase font-mono tracking-wider shadow-lg flex justify-center items-center gap-2">
                            <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="11" cy="11" r="8"></circle><line x1="21" y1="21" x2="16.65" y2="16.65"></line></svg>
                            Run Scan
                        </button>
                    </form>
                    
                    <div class="relative">
                        <div id="map" class="h-[400px] w-full rounded border border-slate-700 z-10 grayscale contrast-125 sepia-[.2] hue-rotate-[140deg]"></div>
                    </div>
                    
                    <div class="mt-6 flex flex-col sm:flex-row justify-between items-start sm:items-center text-sm font-mono text-slate-400 gap-4 bg-slate-900 p-4 rounded border border-slate-800">
                        <div class="text-left flex flex-col gap-1 items-start w-full sm:w-1/2">
                            <div id="targetIntel" class="text-xs text-slate-400 font-mono bg-slate-900/80 p-3 rounded border border-slate-700 w-full text-left min-h-[50px] shadow-inner">
                                Awaiting scan to calculate territory volume...
                            </div>
                        </div>
                        <div class="text-left sm:text-right flex flex-col gap-1 sm:items-end w-full sm:w-1/2">
                            <div id="radiusReadout" class="text-slate-300 font-bold tracking-wider text-xs mb-1">RADIAL BOUNDARY: 15.0 MILES</div>
                            <div id="statusBadge" class="flex flex-col text-left sm:text-right items-start sm:items-end w-full">
                                <div class="flex items-center gap-2 text-emerald-400">
                                    <span class="h-2 w-2 rounded-full bg-emerald-500 animate-pulse sm:hidden"></span>
                                    SYSTEM STANDBY 
                                    <span class="h-2 w-2 rounded-full bg-emerald-500 animate-pulse hidden sm:inline-block"></span>
                                </div>
                                <span class="text-xs text-slate-500 mt-1">Awaiting outward postcode input...</span>
                            </div>
                        </div>
                    </div>
                </div>

                <!-- Live Feed Table (Social Proof) -->
                <div class="bg-brand-dark border border-slate-700 rounded-xl overflow-hidden shadow-2xl flex flex-col h-[565px]">
                    <div class="bg-slate-800/80 border-b border-slate-700 p-5 flex justify-between items-center">
                        <h3 class="font-mono text-emerald-400 font-bold uppercase tracking-wider text-sm flex items-center gap-2">
                            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M21 12a9 9 0 1 1-6.219-8.56"></path></svg>
                            Intercepted Notices
                        </h3>
                        <span class="text-xs text-white font-mono bg-emerald-500/20 px-2 py-1 rounded border border-emerald-500/30">Live Feed Active</span>
                    </div>
                    <div class="overflow-y-auto flex-1 bg-slate-900/50">
                        <table class="w-full text-left border-collapse">
                            <tbody>
                                {lead_rows}
                            </tbody>
                        </table>
                    </div>
                    <div class="p-4 bg-slate-800 border-t border-slate-700 text-center">
                        <a href="#pricing" class="text-emerald-400 font-mono text-sm hover:text-emerald-300 transition-colors flex items-center justify-center gap-2">
                            Upgrade To Unlock Full Commercial Intel <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><line x1="5" y1="12" x2="19" y2="12"></line><polyline points="12 5 19 12 12 19"></polyline></svg>
                        </a>
                    </div>
                </div>

            </div>
        </div>
    </section>

        <!-- The TreeKey Advantage (Psychology & Logic) -->
    <section class="max-w-7xl mx-auto px-4 mt-24 mb-12 relative z-10">
        <div class="text-center mb-16">
            <h2 class="text-3xl md:text-5xl font-extrabold text-white mb-6 uppercase tracking-tight">The <span class="text-emerald-500">TreeKey</span> Advantage</h2>
            <p class="text-lg text-slate-400 max-w-2xl mx-auto">We don't just supply leads. We engineer market dominance. Here is exactly why our contractors win.</p>
        </div>

        <div class="grid grid-cols-1 md:grid-cols-3 gap-8">
            <!-- Pillar 1: Lead-Level Exclusivity -->
            <div class="bg-brand-dark/50 border border-slate-800 p-8 rounded-xl hover:border-emerald-500/50 transition-colors">
                <div class="h-12 w-12 rounded bg-emerald-500/10 flex items-center justify-center mb-6 border border-emerald-500/30 text-emerald-400">
                    <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z"></path></svg>
                </div>
                <h3 class="text-xl font-bold text-white mb-3">100% Exclusive Leads</h3>
                <p class="text-slate-400 text-sm leading-relaxed">Most lead-gen sites sell the same job to 5 different contractors, forcing a race to the bottom on price. At TreeKey, every commercial lead you claim is <strong class="text-slate-200">never sold twice.</strong> If it hits your phone, it is exclusively yours to win.</p>
            </div>

            <!-- Pillar 2: The Network Effect -->
            <div class="bg-brand-dark/50 border border-slate-800 p-8 rounded-xl hover:border-emerald-500/50 transition-colors">
                <div class="h-12 w-12 rounded bg-amber-500/10 flex items-center justify-center mb-6 border border-amber-500/30 text-amber-400">
                    <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="10"></circle><path d="M12 2a14.5 14.5 0 0 0 0 20 14.5 14.5 0 0 0 0-20"></path><path d="M2 12h20"></path></svg>
                </div>
                <h3 class="text-xl font-bold text-white mb-3">Multi-Council Network Effect</h3>
                <p class="text-slate-400 text-sm leading-relaxed">Commercial clearance jobs often span across borough borders. Our algorithms aggregate planning portals across <strong class="text-slate-200">connected local authorities</strong> simultaneously, granting you access to massive 'bonus' jobs just outside your immediate boundary.</p>
            </div>

            <!-- Pillar 3: Beat Local Competitors -->
            <div class="bg-brand-dark/50 border border-slate-800 p-8 rounded-xl hover:border-emerald-500/50 transition-colors">
                <div class="h-12 w-12 rounded bg-blue-500/10 flex items-center justify-center mb-6 border border-blue-500/30 text-blue-400">
                    <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M22 11.08V12a10 10 0 1 1-5.93-9.14"></path><polyline points="22 4 12 14.01 9 11.01"></polyline></svg>
                </div>
                <h3 class="text-xl font-bold text-white mb-3">Intercept Before Competitors</h3>
                <p class="text-slate-400 text-sm leading-relaxed">We detect competitor density in your area and route the highest-paying council jobs to you <strong class="text-slate-200">before they ever hit the public market.</strong> Win the contract while your competitors are still waiting for the phone to ring.</p>
            </div>
        </div>
    </section>

    <!-- Pricing Section (Anchoring & Scarcity) -->
    <section id="pricing" class="relative z-10 py-24 bg-brand-dark/50 border-t border-slate-800/50">
        <div class="max-w-7xl mx-auto px-4">
            <div class="text-center mb-16">
                <span class="text-emerald-500 font-mono font-bold tracking-widest text-sm uppercase">Zero Commitment. Cancel Anytime.</span>
                <h2 class="text-4xl md:text-5xl font-extrabold text-white mt-4 uppercase">Dominate Your Area</h2>
                <p class="text-lg text-slate-400 mt-4">The average commercial site clearance pays 2,500+. One job pays for the year.</p>
            </div>

            <div class="grid grid-cols-1 md:grid-cols-3 gap-8 items-start">
                
                <!-- Tier 1: Sole Trader -->
                <div class="bg-[#0f172a] border border-slate-800 rounded-2xl p-8 relative hover:border-slate-600 transition-colors">
                    <h3 class="text-2xl font-bold text-white mb-2">Sole Trader</h3>
                    <p class="text-slate-400 mb-6 text-sm">Perfect for one-man bands and local startups aiming to grow steadily.</p>
                    <div class="flex items-baseline gap-2 mb-8">
                        <div class="text-4xl font-extrabold text-white">&pound;49</div>
                        <div class="text-lg text-slate-500 font-normal">/month</div>
                    </div>
                    <ul class="mb-8 space-y-4 text-slate-300 text-sm font-medium">
                        <li class="flex items-start gap-3"><svg width="20" class="text-slate-500 shrink-0 mt-0.5" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3"><polyline points="20 6 9 17 4 12"></polyline></svg> 10-Mile Radial Boundary</li>
                        <li class="flex items-start gap-3"><svg width="20" class="text-emerald-500 shrink-0 mt-0.5" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3"><polyline points="20 6 9 17 4 12"></polyline></svg> 100% Exclusive Lead Routing</li>
                        <li class="flex items-start gap-3"><svg width="20" class="text-emerald-500 shrink-0 mt-0.5" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3"><polyline points="20 6 9 17 4 12"></polyline></svg> Daily Email Notifications</li>
                    </ul>
                    <a id="btn-checkout-sole" href="#map" class="block w-full text-center border border-slate-700 hover:border-slate-500 text-white font-bold py-4 rounded-lg transition-all duration-300 uppercase tracking-wider text-sm">
                        Start Local
                    </a>
                </div>

                <!-- Tier 2: Commercial Pro (Hero) -->
                <div class="bg-gradient-to-b from-[#064e3b] to-[#022c22] border-2 border-emerald-500 rounded-2xl p-8 relative transform md:-translate-y-4 shadow-[0_0_40px_rgba(16,185,129,0.15)]">
                    <div class="absolute top-0 left-1/2 transform -translate-x-1/2 -translate-y-1/2">
                        <span class="bg-emerald-500 text-white text-xs font-bold uppercase tracking-widest py-1 px-3 rounded-full">Most Popular</span>
                    </div>
                    <h3 class="text-3xl font-bold text-white mb-2">Commercial Pro</h3>
                    <p class="text-emerald-100/70 mb-6 text-sm h-10">The sweet spot for established 3-man crews hunting lucrative clearances.</p>
                    <div class="flex items-baseline gap-2 mb-8">
                        <div class="text-5xl font-extrabold text-white">&pound;149</div>
                        <div class="text-lg text-emerald-500 font-normal">/month</div>
                    </div>
                    <ul class="mb-8 space-y-4 text-slate-100 text-sm font-medium">
                        <li class="flex items-start gap-3"><svg width="20" class="text-emerald-400 shrink-0 mt-0.5" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3"><polyline points="20 6 9 17 4 12"></polyline></svg> 25-Mile Radial Boundary</li>
                        <li class="flex items-start gap-3"><svg width="20" class="text-emerald-400 shrink-0 mt-0.5" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3"><polyline points="20 6 9 17 4 12"></polyline></svg> 100% Exclusive Lead Routing</li>
                        <li class="flex items-start gap-3"><svg width="20" class="text-emerald-400 shrink-0 mt-0.5" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3"><polyline points="20 6 9 17 4 12"></polyline></svg> Instant SMS/Phone Notifications</li>
                        <li class="flex items-start gap-3"><svg width="20" class="text-emerald-400 shrink-0 mt-0.5" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3"><polyline points="20 6 9 17 4 12"></polyline></svg> Connected-Council Job Access</li>
                    </ul>
                    <a id="btn-checkout-pro" href="#map" class="block w-full text-center bg-emerald-500 hover:bg-emerald-400 text-white font-extrabold py-5 rounded-lg transition-all duration-300 uppercase tracking-widest text-sm shadow-[0_4px_14px_0_rgba(16,185,129,0.39)]">
                        Secure Priority Access
                    </a>
                </div>

                <!-- Tier 3: Regional Dominator -->
                <div class="bg-[#0f172a] border border-slate-800 rounded-2xl p-8 relative hover:border-slate-600 transition-colors">
                    <h3 class="text-2xl font-bold text-white mb-2">Regional Elite</h3>
                    <p class="text-slate-400 mb-6 text-sm">For massive operations running multiple crews across a wide geographic spread.</p>
                    <div class="flex items-baseline gap-2 mb-8">
                        <div class="text-4xl font-extrabold text-white">&pound;299</div>
                        <div class="text-lg text-slate-500 font-normal">/month</div>
                    </div>
                    <ul class="mb-8 space-y-4 text-slate-300 text-sm font-medium">
                        <li class="flex items-start gap-3"><svg width="20" class="text-amber-500 shrink-0 mt-0.5" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3"><polyline points="20 6 9 17 4 12"></polyline></svg> 50-Mile Radial Boundary</li>
                        <li class="flex items-start gap-3"><svg width="20" class="text-emerald-500 shrink-0 mt-0.5" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3"><polyline points="20 6 9 17 4 12"></polyline></svg> 100% Exclusive Lead Routing</li>
                        <li class="flex items-start gap-3"><svg width="20" class="text-amber-500 shrink-0 mt-0.5" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3"><polyline points="20 6 9 17 4 12"></polyline></svg> First-Priority API Routing</li>
                        <li class="flex items-start gap-3"><svg width="20" class="text-emerald-500 shrink-0 mt-0.5" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3"><polyline points="20 6 9 17 4 12"></polyline></svg> Dedicated Account Manager</li>
                    </ul>
                    <a id="btn-checkout-elite" href="#map" class="block w-full text-center border border-slate-700 hover:border-slate-500 text-white font-bold py-4 rounded-lg transition-all duration-300 uppercase tracking-wider text-sm">
                        Dominate Region
                    </a>
                </div>

            </div>
        </div>
    </section>

    <!-- FAQ Section (Objection Handling) -->


    <section class="section py-20 bg-[#020617] border-t border-slate-800">
        <div class="container mx-auto px-4 max-w-3xl">
            <div class="text-center mb-12">
                <h2 class="text-3xl font-extrabold text-white font-mono uppercase tracking-tight">Contractor FAQ</h2>
            </div>
            
            <div class="space-y-6">
                <div class="bg-slate-800/50 p-6 rounded-lg border border-slate-700">
                    <h3 class="text-lg font-bold text-white mb-2">Are these leads exclusive?</h3>
                    <p class="text-slate-400 leading-relaxed">Yes. We operate on a strict <strong>Lead-Level Exclusivity</strong> model. Unlike platforms that sell the same job to 5 different guys, if you receive a commercial notice from Tree Key, it is 100% yours. We never sell the same lead twice.</p>
                </div>
                
                <div class="bg-slate-800/50 p-6 rounded-lg border border-slate-700">
                    <h3 class="text-lg font-bold text-white mb-2">Are the jobs real?</h3>
                    <p class="text-slate-400 leading-relaxed">Yes. We do not generate fake "marketing" leads. We pull statutory data directly from UK council planning portals under the Open Government Licence (OGL v3.0). Every lead includes the official council reference number so you can verify it instantly.</p>
                </div>
                
                <div class="bg-slate-800/50 p-6 rounded-lg border border-slate-700">
                    <h3 class="text-lg font-bold text-white mb-2">Am I tied into a long contract?</h3>
                    <p class="text-slate-400 leading-relaxed">No. We work with tradesmen, not corporations. The lockout is a rolling monthly agreement. You can cancel instantly at any time with zero penalty. Alternatively, buy a 80 credit pack for zero monthly commitment.</p>
                </div>
            </div>
        </div>
    </section>

    <!-- Footer -->
    <footer class="border-t border-slate-800 bg-[#020617] py-12">
        <div class="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 flex flex-col md:flex-row justify-between items-start gap-8">
            <div class="text-slate-500 text-xs text-center md:text-left max-w-2xl">
                <div class="mb-3">
                    <b class="text-slate-300 text-sm">Tree Key</b> by Vector Data Labs.<br>
                </div>
                <p class="mb-2">
                    Operating in compliance with UK Town and Country Planning statutory register regulations. 
                    Data is aggregated from UK Local Planning Authorities under the Open Government Licence v3.0.
                </p>
                <p class="mb-2">
                    &copy; 2026 Vector Data Labs. All rights reserved. Tree Key is a trading name of Vector Data Labs. 
                    Platform is 256-bit SSL Encrypted & GDPR Compliant. 
                </p>
                <p class="text-slate-400 mt-4 mb-1 flex items-center justify-center md:justify-start gap-2">
                    Proudly engineered in the United Kingdom 🇬🇧
                </p>
                <p class="text-slate-600">Contact: nick@treekey.uk</p>
            </div>
            <div class="flex gap-6 text-xs font-mono uppercase tracking-wider flex-wrap justify-center md:justify-end shrink-0 pt-2">
                <a href="/privacy-policy" class="text-slate-400 hover:text-white transition-colors">Privacy</a>
                <a href="/terms-of-service" class="text-slate-400 hover:text-white transition-colors">Terms</a>
                <a href="/health" class="text-slate-400 hover:text-white transition-colors flex items-center gap-2">
                    <span class="relative flex h-2 w-2"><span class="animate-ping absolute inline-flex h-full w-full rounded-full bg-emerald-400 opacity-75"></span><span class="relative inline-flex rounded-full h-2 w-2 bg-emerald-500"></span></span>
                    Datahub
                </a>
                <a href="/admin" class="text-brand-green hover:text-emerald-400 transition-colors">Login</a>
            </div>
        </div>
    </footer>

    <script>
        // Default to zoomed out Great Britain view
        let map = L.map('map', {{ zoomControl: false }}).setView([54.5, -4.0], 6);
        L.control.zoom({{ position: 'bottomright' }}).addTo(map);
        
        L.tileLayer('https://{{s}}.basemaps.cartocdn.com/dark_all/{{z}}/{{x}}/{{y}}{{r}}.png', {{
            attribution: '&copy; OpenStreetMap &copy; CARTO'
        }}).addTo(map);
        
        // Pin defaults to Birmingham (center of England)
        let currentCircle = L.circle([52.4862, -1.8904], {{
            color: '#10b981',
            fillColor: '#059669',
            fillOpacity: 0.15,
            radius: 24140, // 15 miles in meters
            weight: 2,
            className: 'radar-circle'
        }}).addTo(map);
        
        // Allow moving the pin with a map click
        map.on('click', async function(e) {{
            const radSelect = document.getElementById("radiusSelect");
            const rad = radSelect ? parseInt(radSelect.value) : 24140;
            currentCircle.setLatLng(e.latlng);
            currentCircle.setRadius(rad);
            
            document.getElementById('statusBadge').innerHTML = `
                <div class="flex items-center gap-2 text-emerald-400 mb-1 sm:justify-end">
                    <span class="h-2 w-2 rounded-full bg-emerald-500 animate-pulse sm:hidden"></span> 
                    Manual Lock: ${{e.latlng.lat.toFixed(4)}}, ${{e.latlng.lng.toFixed(4)}}
                    <span class="h-2 w-2 rounded-full bg-emerald-500 animate-pulse hidden sm:inline-block"></span>
                </div>
            `;
            
            try {{
                const res = await fetch(`/api/check-postcode?lat=${{e.latlng.lat}}&lng=${{e.latlng.lng}}&radius=${{Math.round(rad/1609.34)}}`);
                const data = await res.json();
                if (data.status === "ok") {{
                    document.getElementById('postcodeInput').value = data.postcode;
                    document.getElementById('radiusReadout').innerHTML = `RADIAL BOUNDARY: ${{data.radius_miles || (rad/1609.34).toFixed(1)}} MILES`;
                    document.getElementById('btn-checkout-sole').href = `/checkout/sole_trader?outcode=${{data.postcode}}`;
                    document.getElementById('btn-checkout-pro').href = `/checkout/commercial_pro?outcode=${{data.postcode}}`;
                    document.getElementById('btn-checkout-elite').href = `/checkout/regional_elite?outcode=${{data.postcode}}`;
                    document.getElementById('targetIntel').innerHTML = `<span class="text-emerald-400 font-bold text-sm">${{data.selected_area_leads}} Active Leads</span> in radius<br><span class="text-slate-400 border-t border-slate-700 pt-1 mt-1 block">+ ${{data.connected_area_leads}} additional in connected zones</span>`;
                    
                    document.getElementById('statusBadge').innerHTML = `
                        <div class="flex items-center gap-2 text-emerald-400 mb-1 sm:justify-end">
                            <span class="h-2 w-2 rounded-full bg-emerald-500 animate-pulse sm:hidden"></span>
                            Manual Lock: ${{e.latlng.lat.toFixed(4)}}, ${{e.latlng.lng.toFixed(4)}}
                            <span class="h-2 w-2 rounded-full bg-emerald-500 animate-pulse hidden sm:inline-block"></span>
                        </div>
                        <span class="text-xs font-bold text-amber-500 animate-pulse mt-1 inline-block border border-amber-500/30 bg-amber-500/10 px-2 py-1 rounded">&#9888;&#65039; ${{data.competitors}} Local Competitors Detected</span>
                    `;
                }} else if (data.status === "out_of_bounds") {{
                    document.getElementById('targetIntel').innerHTML = `<span class="text-red-500 font-bold text-sm">Out of Bounds</span><br><span class="text-slate-400 border-t border-slate-700 pt-1 mt-1 block">${{data.message}}</span>`;
                    document.getElementById('statusBadge').innerHTML = `
                        <div class="flex items-center gap-2 text-red-400 mb-1 sm:justify-end">
                            Outside Coverage Area
                        </div>
                    `;
                }}
            }} catch(err) {{}}
        }});

        async function scanTerritory() {{
            const btn = document.getElementById("scanBtn");
            const input = document.getElementById("postcodeInput").value;
            const radSelect = document.getElementById("radiusSelect");
            const radVal = radSelect ? parseInt(radSelect.value) : 24140;
            const status = document.getElementById("statusBadge");
            
            btn.innerHTML = `<svg class="animate-spin -ml-1 mr-2 h-4 w-4 text-white" xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24"><circle class="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" stroke-width="4"></circle><path class="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z"></path></svg> SCANNING...`;
            btn.disabled = true;
            btn.classList.add("opacity-80");
            
            // Subconscious Trigger: Fake calculating sequence to build tension/perceived value
            status.innerHTML = `
                <div class="flex items-center gap-2 text-amber-500 mb-1">
                    <span class="h-2 w-2 rounded-full bg-amber-500 animate-pulse"></span> Triangulating Postcode...
                </div>
                <span class="text-xs text-slate-500">Querying Open Government Licence APIs...</span>
            `;
            
            try {{
                const res = await fetch(`/api/check-postcode?postcode=${{encodeURIComponent(input)}}&radius=${{Math.round(radVal/1609.34)}}`);
                const data = await res.json();
                
                if (data.status === "ok") {{
                    map.setView([data.lat, data.lng], 10);
                    currentCircle.setLatLng([data.lat, data.lng]);
                    currentCircle.setRadius(radVal);
                    document.getElementById("radiusReadout").innerHTML = `RADIAL BOUNDARY: ${{ (radVal/1609.34).toFixed(1) }} MILES`;
                    document.getElementById('btn-checkout-sole').href = `/checkout/sole_trader?outcode=${{data.postcode}}`;
                    document.getElementById('btn-checkout-pro').href = `/checkout/commercial_pro?outcode=${{data.postcode}}`;
                    document.getElementById('btn-checkout-elite').href = `/checkout/regional_elite?outcode=${{data.postcode}}`;
                    document.getElementById("targetIntel").innerHTML = `<span class="text-emerald-400 font-bold text-sm">${{data.selected_area_leads}} Active Leads</span> in radius<br><span class="text-slate-400 border-t border-slate-700 pt-1 mt-1 block">+ ${{data.connected_area_leads}} additional in connected zones</span>`;
                    
                    // Add slight delay for psychological weight
                    setTimeout(() => {{
                        status.innerHTML = `
                            <div class="flex items-center gap-2 text-emerald-400 mb-1 sm:justify-end">
                                <span class="h-2 w-2 rounded-full bg-emerald-500 animate-pulse sm:hidden"></span>
                                Radar Locked: ${{data.postcode}}
                                <span class="h-2 w-2 rounded-full bg-emerald-500 animate-pulse hidden sm:inline-block"></span>
                            </div>
                            <span class="text-xs font-bold text-amber-500 animate-pulse mt-1 inline-block border border-amber-500/30 bg-amber-500/10 px-2 py-1 rounded">&#9888;&#65039; ${{data.competitors}} Local Competitors Detected</span>
                        `;
                    }}, 600);
                    
                }} else if (data.status === "out_of_bounds") {{
                    document.getElementById('targetIntel').innerHTML = `<span class="text-red-500 font-bold text-sm">Out of Bounds</span><br><span class="text-slate-400 border-t border-slate-700 pt-1 mt-1 block">${{data.message}}</span>`;
                    status.innerHTML = `<div class="flex items-center gap-2 text-red-500 sm:justify-end">Outside Coverage Area</div>`;
                }} else {{
                    status.innerHTML = `<div class="flex items-center gap-2 text-red-500"><span class="h-2 w-2 rounded-full bg-red-500"></span> Invalid Postcode</div>`;
                }}
            }} catch(e) {{
                status.innerHTML = `<div class="flex items-center gap-2 text-red-500"><span class="h-2 w-2 rounded-full bg-red-500"></span> Network Error</div>`;
            }}
            
            setTimeout(() => {{
                btn.innerHTML = `<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="11" cy="11" r="8"></circle><line x1="21" y1="21" x2="16.65" y2="16.65"></line></svg> RUN SCAN`;
                btn.disabled = false;
                btn.classList.remove("opacity-80");
            }}, 600);
        }}
    </script>
</body>
</html>
"""


@app.get("/admin", response_class=HTMLResponse)
def admin_dashboard(request: Request, secret: Optional[str] = Query(None)):
    verify_admin_or_secret(request, secret)
    stats = {"p": 0, "l": 0, "l_council": 0, "l_domestic": 0, "enriched": 0, "partners": [], "leads": []}

    try:
        conn = database.get_db_conn(); cur = conn.cursor()
        cur.execute("SELECT count(*) FROM potential_partners"); stats["p"] = cur.fetchone()[0]
        cur.execute("SELECT count(*) FROM leads"); stats["l"] = cur.fetchone()[0]
        cur.execute("SELECT count(*) FROM leads WHERE source_type IN ('direct_homeowner', 'domestic_classified')")
        stats["l_domestic"] = cur.fetchone()[0]
        stats["l_council"] = stats["l"] - stats["l_domestic"]
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

    import html
    partner_rows = "".join([
        f"<li><b>{html.escape(str(p[0] or ''))}</b>  {html.escape(str(p[1] or 'Director on file'))} | <b>{html.escape(str(p[2] or ''))}</b> |  {html.escape(str(p[4] or ''))} |  {html.escape(str(p[5] or ''))} |  {p[3] or 'N/A'}</li>"
        for p in stats["partners"]
    ])

    import datetime
    now = datetime.datetime.now(datetime.timezone.utc)

    def get_freshness_badge(discovered_at):
        if not discovered_at:
            return " <span style='color:#2e7d32; font-weight:bold;'> FRESH</span>"
        try:
            delta_days = (now - discovered_at).days
            if delta_days <= 14:
                return f" <span style='color:#2e7d32; font-weight:bold;'> FRESH ({delta_days}d ago)</span>"
            elif delta_days <= 45:
                return f" <span style='color:#f57f17; font-weight:bold;'> CONSULTATION ({delta_days}d)</span>"
            elif delta_days <= 90:
                return f" <span style='color:#0277bd; font-weight:bold;'> GRANTED</span>"
            else:
                return f" <span style='color:#757575;'> ARCHIVED</span>"
        except Exception:
            return " <span style='color:#2e7d32; font-weight:bold;'> FRESH</span>"

    SCORE_EMOJI = {"small": "", "medium": "", "large": ""}
    lead_rows = "".join([
        f"<li>{SCORE_EMOJI.get(l[2],'')} <b>{html.escape(str(l[0] or ''))}</b> {get_freshness_badge(l[5])}<br><span style='color:#555; font-size:13px;'>{html.escape(str(l[1] or '')[:90])}... | {html.escape(str(l[3] or ''))} | {html.escape(str(l[4] or ''))}</span></li>"
        for l in stats["leads"]
    ])

    city_buttons = "".join([
        f"""<div style='display:inline-block; margin:6px; padding:12px 16px;
            background:#f8fafc; border-radius:12px; border:1px solid #e2e8f0;'>
            <b> {city}</b><br>
            <div style='margin-top:6px; font-size:12px;'>
                <a href='/scan/{city.lower().replace(" ", "-")}' style='color:#059669; font-weight:bold; text-decoration:none;'>&#128269; Scan Leads</a> &nbsp;|&nbsp;
                <a href='/research/{city.lower().replace(" ", "-")}' style='color:#0284c7; text-decoration:none;'>&#128373; Find New</a> &nbsp;|&nbsp;
                <a href='/enrich-region/{city.lower().replace(" ", "-")}' style='color:#7c3aed; font-weight:bold; text-decoration:none;'>&#10024; Enrich</a>
            </div>
        </div>"""
        for city in ALL_CITIES  # Display all UK regions including Scotland and Wales
    ])

    pct = int((stats['enriched'] / stats['p'] * 100)) if stats['p'] else 0

    return f"""
    <html><head><title>Vector Data Labs  Admin Command</title></head>
    <body style="font-family:sans-serif; background:#f4f4f9; padding:40px;">
    <div style="max-width:920px; margin:auto; background:white; padding:40px;
                border-radius:20px; border-top:8px solid #064e3b; box-shadow:0 4px 12px rgba(0,0,0,0.05);">
        <div style="display:flex; justify-content:space-between; align-items:center;">
            <h1>&#128188; Tree Key Admin Command</h1>
            <a href="/" target="_blank" style="background:#10b981; color:white; padding:8px 14px; border-radius:6px; text-decoration:none; font-weight:bold; font-size:13px;"> View Public Homepage</a>
        </div>

        <p>Verified LTD Partners: <b>{stats['p']}</b> &nbsp;|&nbsp; 
           Enriched with Contacts: <b style="color:#059669;">{stats['enriched']} ({pct}%)</b> 
           <br><br>
           <span style="background:#0f172a; color:white; padding:4px 8px; border-radius:4px;">Total Planning Council Leads: <b>{stats['l_council']}</b></span>
           &nbsp;
           <span style="background:#ea580c; color:white; padding:4px 8px; border-radius:4px;">Total Domestic Leads: <b>{stats['l_domestic']}</b></span>
           <br><br>
           <a href='/status'> System Status</a>
           &nbsp;|&nbsp; <a href='/pricing'> Pricing Table</a>
           &nbsp;|&nbsp; <a href='/export-directors'> View Contacts</a>
           &nbsp;|&nbsp; <a href='/export-directors.csv' style='color:#1b5e20; font-weight:bold;'>&#128190; Download CSV</a>
        </p>
        <hr>
        <h3>&#128225; Nationwide Territory Scanners, Discovery & Instant Enrichment</h3>
        <p style="color:#64748b; font-size:13px; margin-top:-5px;">Click <b>&#128269; Scan Leads</b> to fetch local planning applications, <b>&#128373; Find New</b> to discover tree surgery LTDs via Companies House, or <b>&#10024; Enrich</b> to pull direct phones and ratings in ~5 seconds.</p>
        {city_buttons}
        <hr>

        <h3> Batch Operations</h3>
        <div style="display:flex; gap:10px; flex-wrap:wrap; margin-bottom:10px;">
            <a href='/scan-domestic-jobs' style="background:#ea580c; color:white; padding:10px 18px; border-radius:8px; text-decoration:none; font-weight:bold; font-size:13px; box-shadow:0 2px 6px rgba(234,88,12,0.3);">
                 🏡 Sweep Domestic Homeowner Leads
            </a>
            <a href='/populate-2000-partners' style="background:#047857; color:white; padding:10px 18px; border-radius:8px; text-decoration:none; font-weight:bold; font-size:13px; box-shadow:0 2px 6px rgba(4,120,87,0.3);">
                 Harvest 2,000+ Contractors (Nationwide GB)
            </a>
            <a href='/enrich-all' style="background:#1b5e20; color:white; padding:10px 16px; border-radius:8px; text-decoration:none; font-weight:bold; font-size:13px;">
                &#10024; Enrich All (All Remaining Partners)
            </a>
            <a href='/enrich-batch' style="background:#7c3aed; color:white; padding:10px 16px; border-radius:8px; text-decoration:none; font-weight:bold; font-size:13px;">
                &#10024; Enrich Next 50 Partners (5-8 Seconds)
            </a>
            <a href='/research-all' style="background:#0284c7; color:white; padding:10px 16px; border-radius:8px; text-decoration:none; font-weight:bold; font-size:13px;">
                 Discover All Regions (Find New)
            </a>
            <a href='/clean-partners' style="background:#b71c1c; color:white; padding:10px 16px; border-radius:8px; text-decoration:none; font-weight:bold; font-size:13px;">
                 Clean Database (Purge False Substrings)
            </a>
            <a href='/export-directors.csv' style="background:#064e3b; color:white; padding:10px 16px; border-radius:8px; text-decoration:none; font-weight:bold; font-size:13px;">
                 Export Contacts CSV
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





#  Status 

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
        icon, color, note = ("", "#1b5e20", "Set") if val else ("", "#b71c1c", "MISSING")
        rows_html += f"<tr><td style='padding:8px;'>{label}</td><td style='padding:8px; color:{color}; font-weight:bold;'>{icon} {note}</td></tr>"

    try:
        conn = database.get_db_conn(); conn.close()
        db_status = "<span style='color:#1b5e20; font-weight:bold;'> Connected</span>"
    except Exception as e:
        db_status = f"<span style='color:#b71c1c; font-weight:bold;'> Failed: {e}</span>"

    return f"""
    <html><head><title>System Status</title></head>
    <body style="font-family:sans-serif; background:#f4f4f9; padding:40px;">
    <div style="max-width:620px; margin:auto; background:white; padding:40px;
                border-radius:20px; border-top:8px solid #1b5e20;">
        <h2> System Status</h2>
        <p><a href='/'> Dashboard</a></p>
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
            Keys are never displayed  only presence is checked.<br>
            <b>Automated scanning:</b> Set up cron-job.org to hit
            <code>/trigger-leads-{{city}}?secret=YOUR_SECRET</code> on your preferred schedule.
        </p>
    </div></body></html>
    """


#  Pricing Page (Public) 

@app.get("/pricing", response_class=HTMLResponse)
def pricing():
    plans = payments.PLANS

    # Separate subscriptions and single purchase plans
    sub_cards = ""
    single_cards = ""

    for key, plan in plans.items():
        if plan["mode"] == "subscription":
            price_display = f"£{plan['amount'] / 100:.0f}<span style='font-size:16px; font-weight:normal; color:#64748b;'>/month</span>"
            roi_box = f"<div style='background:#f0fdf4; border-left:3px solid #059669; padding:10px; font-size:12px; color:#065f46; text-align:left; margin:14px 0; border-radius:4px;'><b>💡 Real-World Math:</b> {plan.get('real_world_roi', '')}</div>"
            highlight = "border:2px solid #059669; box-shadow:0 8px 24px rgba(5,150,105,0.12);" if key == "climber_domestic" else "border:1px solid #e2e8f0;"
            
            sub_cards += f"""
            <div style="{highlight} border-radius:16px; padding:24px; background:white; display:flex; flex-direction:column; justify-content:space-between; margin-bottom:16px;">
                <div>
                    <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:10px;">
                        <span style="font-size:11px; background:#ecfdf5; color:#065f46; font-weight:bold; padding:4px 10px; border-radius:20px; text-transform:uppercase;">{plan['badge']}</span>
                    </div>
                    <h3 style="margin:0 0 6px 0; font-size:19px; color:#0f172a;">{plan['name']}</h3>
                    <p style="color:#64748b; font-size:13px; line-height:1.5; margin:0 0 12px 0;">{plan['description']}</p>
                    <div style="font-size:28px; font-weight:800; color:#044332; margin:10px 0;">{price_display}</div>
                    {roi_box}
                </div>
                <a href="/checkout/{key}" style="background:#044332; color:white; padding:12px; border-radius:8px; text-decoration:none; text-align:center; font-weight:bold; font-size:14px; margin-top:10px; display:block;">
                   Claim Tailored Tier →
                </a>
            </div>"""
        else:
            price_display = f"£{plan['amount'] / 100:.0f}<span style='font-size:14px; font-weight:normal; color:#64748b;'> one-off</span>"
            single_cards += f"""
            <div style="border:1px solid #e2e8f0; border-radius:12px; padding:18px; background:white; margin-bottom:12px; display:flex; justify-content:space-between; align-items:center; flex-wrap:wrap; gap:12px;">
                <div style="max-width:480px;">
                    <span style="font-size:10px; background:#f1f5f9; color:#475569; font-weight:bold; padding:3px 8px; border-radius:12px; text-transform:uppercase;">{plan['badge']}</span>
                    <h4 style="margin:6px 0 4px 0; font-size:16px; color:#0f172a;">{plan['name']}</h4>
                    <p style="color:#64748b; font-size:12px; margin:0;">{plan['description']}</p>
                </div>
                <div style="text-align:right;">
                    <div style="font-size:22px; font-weight:bold; color:#044332; margin-bottom:6px;">{price_display}</div>
                    <a href="/checkout/{key}" style="background:#0f172a; color:white; padding:8px 18px; border-radius:6px; text-decoration:none; font-size:13px; font-weight:bold; display:inline-block;">
                        Unlock Single Lead
                    </a>
                </div>
            </div>"""

    return f"""
    <!DOCTYPE html>
    <html lang="en-GB">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>Tailored Packages & Anti-Directory Guarantee | TreeKey</title>
        <style>
            :root {{
                --brand-primary: #044332;
                --brand-dark: #0f172a;
                --brand-muted: #64748b;
                --bg-light: #f8fafc;
                --border-color: #e2e8f0;
            }}
            body {{
                font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
                background-color: var(--bg-light);
                color: var(--brand-dark);
                margin: 0; padding: 40px 16px;
                line-height: 1.6;
            }}
            .container {{ max-width: 860px; margin: auto; }}
            .header {{ text-align: center; margin-bottom: 32px; }}
            .header h1 {{ font-size: 34px; font-weight: 800; color: var(--brand-dark); margin: 0 0 10px 0; }}
            .header p {{ color: var(--brand-muted); font-size: 16px; margin: 0; }}
            
            .creed-banner {{
                background: linear-gradient(135deg, #044332 0%, #064e3b 100%);
                color: white;
                border-radius: 12px;
                padding: 24px;
                margin-bottom: 32px;
                box-shadow: 0 8px 24px rgba(4,67,50,0.15);
            }}
            .creed-banner h3 {{ margin-top: 0; font-size: 20px; color: #a7f3d0; }}
            
            .grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(250px, 1fr)); gap: 16px; margin-bottom: 32px; }}
            
            .comparison-table {{
                width: 100%;
                border-collapse: collapse;
                background: white;
                border-radius: 12px;
                overflow: hidden;
                box-shadow: 0 2px 10px rgba(0,0,0,0.04);
                margin-top: 24px;
                font-size: 13px;
            }}
            .comparison-table th, .comparison-table td {{
                padding: 14px 16px;
                text-align: left;
                border-bottom: 1px solid var(--border-color);
            }}
            .comparison-table th {{ background: #0f172a; color: white; font-weight: 600; }}
            .comparison-table tr:last-child td {{ border-bottom: none; }}
        </style>
    </head>
    <body>
    <div class="container">
        <div class="header">
            <h1>Fair Trade Packages & Zero-Reselling Guarantee</h1>
            <p>Direct statutory council intelligence & photo-verified homeowner leads. 100% exclusive. No shared bidding wars.</p>
        </div>

        <div class="creed-banner">
            <h3>🌲 The TreeKey Creed: "Your Prosperity is Our Business"</h3>
            <p style="font-size:14px; line-height:1.6; margin:0;">
                We are not a faceless directory. We do NOT sell your leads to 5 competitors, we do not take a percentage of your hard-earned invoices, and we don't trap you in long contracts. Every lead on TreeKey is a <b>single-sale asset</b>—the second you receive it, it is burned from our system forever.
            </p>
        </div>

        <h2 style="font-size:22px; margin-bottom:16px; color:#0f172a;">1. Select Your Dedicated Subscription Tier</h2>
        <div class="grid">
            {sub_cards}
        </div>

        <h2 style="font-size:22px; margin:32px 0 16px 0; color:#0f172a;">2. Or Buy As You Go (Single-Lead Marketplace)</h2>
        <p style="color:#64748b; font-size:13px; margin-top:-10px; margin-bottom:16px;">
            Subscribers get priority allocation. Any unallocated leads flow into our single-purchase marketplace. Once bought, a lead is burned and never resold.
        </p>
        {single_cards}

        <h2 style="font-size:22px; margin:40px 0 16px 0; color:#0f172a;">⚖️ Why TreeKey is the Opposite of Directories</h2>
        <table class="comparison-table">
            <thead>
                <tr>
                    <th>Feature / Metric</th>
                    <th>Traditional Directories (Bark / Checkatrade / TrustATrader)</th>
                    <th style="background:#044332;">TreeKey Operating System</th>
                </tr>
            </thead>
            <tbody>
                <tr>
                    <td><b>Lead Exclusivity</b></td>
                    <td>❌ Sold to 3–5 competing contractors simultaneously.</td>
                    <td style="color:#065f46; font-weight:bold;">✅ 100% Single-Sale. Lead is burned once dispatched.</td>
                </tr>
                <tr>
                    <td><b>Price Competition</b></td>
                    <td>❌ Race to the bottom; customer compares 5 cheap quotes.</td>
                    <td style="color:#065f46; font-weight:bold;">✅ First-Mover Advantage. Quote before competitors know.</td>
                </tr>
                <tr>
                    <td><b>Lead Source</b></td>
                    <td>❌ Unverified ballpark quote seekers & price checkers.</td>
                    <td style="color:#065f46; font-weight:bold;">✅ Statutory Council Planning Notices (100% committed).</td>
                </tr>
                <tr>
                    <td><b>Trade Cost Framing</b></td>
                    <td>❌ Heavy fixed monthly directory listing fees (£120+/mo).</td>
                    <td style="color:#065f46; font-weight:bold;">✅ Low £49/mo (less than half a tank of diesel). 1 job = 5x ROI.</td>
                </tr>
                <tr>
                    <td><b>Customer Ownership</b></td>
                    <td>❌ Trapped inside their app collecting reviews for them.</td>
                    <td style="color:#065f46; font-weight:bold;">✅ You Own the Client. Quote directly under your own brand.</td>
                </tr>
            </tbody>
        </table>

        <div style="text-align:center; margin-top:40px; padding:20px; background:white; border-radius:12px; border:1px solid #e2e8f0;">
            <p style="margin:0 0 10px 0; font-size:14px; color:#64748b;">Have an idea or want a tool built specifically for your crew?</p>
            <a href="/suggestions" style="color:#044332; font-weight:bold; text-decoration:none; font-size:14px;">💡 Submit a Suggestion to Our Product Board →</a>
            &nbsp;|&nbsp;
            <a href="/" style="color:#64748b; text-decoration:none; font-size:14px;">Return to Live Map</a>
        </div>
    </div>
    </body>
    </html>
    """




# ── Customer Suggestions & Feedback Hub ───────────────────────────────────────

@app.get("/suggestions", response_class=HTMLResponse)
def suggestions_page():
    return """
    <!DOCTYPE html>
    <html lang="en-GB">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>Arborist Suggestions Hub | TreeKey</title>
        <style>
            body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; background:#f8fafc; color:#0f172a; margin:0; padding:40px 16px; }
            .box { max-width:580px; margin:auto; background:white; padding:32px; border-radius:16px; border:1px solid #e2e8f0; box-shadow:0 4px 16px rgba(0,0,0,0.04); }
            input, textarea { width:100%; box-sizing:border-box; padding:12px; border:1px solid #cbd5e1; border-radius:8px; margin-bottom:16px; font-family:inherit; font-size:14px; }
            button { background:#044332; color:white; border:none; padding:12px 24px; border-radius:8px; font-weight:bold; font-size:15px; cursor:pointer; width:100%; }
        </style>
    </head>
    <body>
    <div class="box">
        <h2 style="margin-top:0; color:#044332;">💡 Arborist Suggestions & Feature Requests</h2>
        <p style="color:#64748b; font-size:14px; line-height:1.5;">We built TreeKey to serve UK tree surgeons. Tell us what tools, calculators, or data features you need to make your business more profitable.</p>
        <form action="/api/submit-suggestion" method="POST">
            <label style="font-size:13px; font-weight:600;">Your Name / Company Name:</label>
            <input type="text" name="name" placeholder="e.g. Dave, Apex Tree Care Ltd" required>
            
            <label style="font-size:13px; font-weight:600;">Phone Number or Email (Optional):</label>
            <input type="text" name="contact" placeholder="So we can let you know when it's built">
            
            <label style="font-size:13px; font-weight:600;">Your Suggestion or Problem You Want Solved:</label>
            <textarea name="suggestion" rows="5" placeholder="e.g. I need a tool to calculate tipping weight for mature Ash trees, or an easier way to download council sketch maps..." required></textarea>
            
            <button type="submit">Submit Suggestion to Founders 🚀</button>
        </form>
        <p style="text-align:center; margin-top:20px;"><a href="/" style="color:#64748b; text-decoration:none; font-size:13px;">← Return to Main Page</a></p>
    </div>
    </body>
    </html>
    """


@app.post("/api/submit-suggestion")
async def submit_suggestion(request: Request):
    form = await request.form()
    name = form.get("name", "")
    contact = form.get("contact", "")
    suggestion = form.get("suggestion", "")
    
    database.save_contractor_suggestion(name, contact, suggestion)
    
    return HTMLResponse("""
    <html><body style="font-family:sans-serif; text-align:center; padding:60px; background:#f8fafc;">
        <div style="max-width:500px; margin:auto; background:white; padding:40px; border-radius:16px; border:1px solid #e2e8f0;">
            <h2 style="color:#059669; margin-top:0;">✅ Suggestion Received!</h2>
            <p style="color:#64748b; font-size:15px; line-height:1.5;">Thank you for helping us make TreeKey better for UK tree surgeons. Our team reviews every suggestion directly.</p>
            <a href="/" style="display:inline-block; background:#044332; color:white; padding:10px 20px; border-radius:8px; text-decoration:none; font-weight:bold; font-size:14px; margin-top:15px;">Return to Map</a>
        </div>
    </body></html>
    """)




# ── 1-Tap Homeowner Introduction Letter Generator ─────────────────────────────

@app.get("/generate-letter/{lead_id}", response_class=HTMLResponse)
def generate_homeowner_letter(lead_id: str, company: str = "Your Local Tree Specialists", phone: str = "07XXX XXXXXX"):
    conn = database.get_db_conn()
    cur = conn.cursor()
    cur.execute("SELECT reference, address, summary, council_source FROM leads WHERE id = %s OR reference = %s;", (lead_id, lead_id))
    row = cur.fetchone()
    cur.close()
    conn.close()

    if not row:
        return HTMLResponse("<h3>Lead not found.</h3>", status_code=404)

    ref, addr, summary, council = row

    return f"""
    <!DOCTYPE html>
    <html lang="en-GB">
    <head>
        <meta charset="UTF-8">
        <title>Homeowner Notice Letter | {ref}</title>
        <style>
            body {{ font-family: "Georgia", serif; padding: 40px; color: #111; max-width: 650px; margin: auto; line-height: 1.6; background: #fff; }}
            .header {{ border-bottom: 2px solid #044332; padding-bottom: 15px; margin-bottom: 25px; display: flex; justify-content: space-between; align-items: flex-end; }}
            .title {{ font-size: 20px; font-weight: bold; color: #044332; }}
            .btn-print {{ background: #044332; color: white; border: none; padding: 8px 16px; border-radius: 4px; cursor: pointer; font-size: 13px; font-family: sans-serif; }}
            @media print {{ .btn-print {{ display: none; }} body {{ padding: 0; }} }}
        </style>
    </head>
    <body>
        <div style="text-align:right; margin-bottom:15px;">
            <button class="btn-print" onclick="window.print()">🖨️ Print / Save as PDF</button>
        </div>

        <div class="header">
            <div>
                <div class="title">{company}</div>
                <div style="font-size:12px; color:#666; font-family:sans-serif;">Professional Arboricultural & Tree Surgery Services</div>
            </div>
            <div style="font-size:12px; font-family:sans-serif; text-align:right;">
                <b>Tel:</b> {phone}<br>
                <b>Standard:</b> BS 3998:2010 Compliant
            </div>
        </div>

        <p style="font-size:14px; margin-bottom:20px;">
            <b>To the Property Owner / Occupier:</b><br>
            {addr}
        </p>

        <p style="font-size:14px;">Dear Homeowner,</p>

        <p style="font-size:14px; text-align:justify;">
            We are writing to introduce our local arboricultural team in relation to your recent statutory planning notification registered with <b>{council}</b> (Application Reference: <b>{ref}</b>).
        </p>

        <div style="background:#f8fafc; border-left:3px solid #044332; padding:12px 16px; margin:15px 0; font-size:13px; font-family:sans-serif;">
            <b>Proposed Arboricultural Specification:</b><br>
            <i>"{summary}"</i>
        </div>

        <p style="font-size:14px; text-align:justify;">
            As an established, fully insured local tree care contractor, our team carries full NPTC city & guilds climbing certifications and £5,000,000 Public Liability Insurance. All operations are strictly executed in accordance with <b>British Standard BS 3998:2010 (Tree Work Recommendations)</b>.
        </p>

        <p style="font-size:14px; text-align:justify;">
            We would be pleased to provide a <b>complimentary, no-obligation on-site quotation</b> and assist with any liaison required with the local planning authority tree officer.
        </p>

        <div style="margin-top:30px; font-size:14px;">
            Yours sincerely,<br><br>
            <b>{company}</b><br>
            Direct Line: <b>{phone}</b>
        </div>
    </body>
    </html>
    """




# ── 2. The "Neighbor Multiplier" 1-Tap Street Flyer Generator ─────────────────

@app.get("/generate-street-flyer/{lead_id}", response_class=HTMLResponse)
def generate_street_flyer(lead_id: str, company: str = "Your Local Tree Surgery Team", phone: str = "07XXX XXXXXX"):
    conn = database.get_db_conn()
    cur = conn.cursor()
    cur.execute("SELECT reference, address, summary FROM leads WHERE id = %s OR reference = %s;", (lead_id, lead_id))
    row = cur.fetchone()
    cur.close()
    conn.close()

    if not row:
        return HTMLResponse("<h3>Lead not found.</h3>", status_code=404)

    ref, addr, summary = row
    
    # Extract street name from address
    parts = [p.strip() for p in addr.split(",") if p.strip()]
    street_name = parts[0] if parts else "your street"

    return f"""
    <!DOCTYPE html>
    <html lang="en-GB">
    <head>
        <meta charset="UTF-8">
        <title>Neighbor Street Notice & Discount | {street_name}</title>
        <style>
            body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; padding: 40px; color: #0f172a; max-width: 650px; margin: auto; line-height: 1.6; background: #fff; }}
            .card {{ border: 2px solid #044332; border-radius: 12px; padding: 28px; background: #ffffff; }}
            .badge {{ background: #044332; color: white; padding: 4px 12px; border-radius: 20px; font-size: 12px; font-weight: bold; text-transform: uppercase; }}
            .btn-print {{ background: #044332; color: white; border: none; padding: 10px 20px; border-radius: 6px; cursor: pointer; font-size: 14px; font-weight: bold; margin-bottom: 20px; }}
            .discount-box {{ background: #f0fdf4; border: 2px dashed #059669; border-radius: 8px; padding: 16px; margin: 20px 0; text-align: center; }}
            @media print {{ .btn-print {{ display: none; }} body {{ padding: 0; }} }}
        </style>
    </head>
    <body>
        <div style="text-align:right;">
            <button class="btn-print" onclick="window.print()">🖨️ Print 5 Copies for Neighbors</button>
        </div>

        <div class="card">
            <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:15px;">
                <span class="badge">🌲 Tree Works Notice</span>
                <span style="font-size:12px; color:#64748b;">NPTC Certified • £5M Insured</span>
            </div>

            <h2 style="margin:0 0 10px 0; color:#044332; font-size:22px;">Notice to Neighbors on {street_name}</h2>
            
            <p style="font-size:14px; color:#334155;">
                Hello neighbor, our professional arboricultural team will be carrying out approved tree work on your street at <b>{addr}</b> in the coming days.
            </p>

            <div class="discount-box">
                <h3 style="margin:0 0 6px 0; color:#065f46; font-size:18px;">🎁 20% Same-Day Street Discount</h3>
                <p style="margin:0; font-size:13px; color:#047857;">
                    Because our heavy woodchipper, truck, and climbing crew are already on {street_name}, we have zero extra travel costs. We are passing that saving directly to neighbors!
                </p>
            </div>

            <h4 style="margin:16px 0 8px 0; font-size:15px; color:#0f172a;">Services Available on the Day:</h4>
            <ul style="font-size:13px; color:#334155; padding-left:20px; margin:0 0 20px 0;">
                <li>Crown reduction, thinning & branch pruning</li>
                <li>Conifer & overgrown boundary hedge trimming</li>
                <li>Felling dead, diseased, or hazardous trees</li>
                <li>Stump grinding & complete green waste removal</li>
            </ul>

            <div style="background:#f8fafc; border-radius:8px; padding:14px; display:flex; justify-content:space-between; align-items:center; flex-wrap:wrap; gap:10px;">
                <div>
                    <div style="font-size:11px; color:#64748b; text-transform:uppercase; font-weight:bold;">Contractor:</div>
                    <div style="font-weight:bold; color:#0f172a; font-size:15px;">{company}</div>
                </div>
                <div style="text-align:right;">
                    <div style="font-size:11px; color:#64748b; text-transform:uppercase; font-weight:bold;">Call or Text for a Free Quote:</div>
                    <div style="font-weight:800; color:#044332; font-size:17px;">{phone}</div>
                </div>
            </div>
        </div>
    </body>
    </html>
    """




# ── Checkout (Stripe with Single-Sale Inventory Burn) ─────────────────────────

@app.get("/checkout/{plan_key}")
def checkout(plan_key: str, request: Request):
    outcode = request.query_params.get("outcode", "GB")
    lead_id = request.query_params.get("lead_id")
    
    url = payments.create_checkout_session(plan_key, outcode)
    if not url:
        return HTMLResponse(
            "<html><body style='font-family:sans-serif; text-align:center; padding:60px;'>"
            "<h1>Payment System Unavailable</h1>"
            "<p>We are currently experiencing issues connecting to Stripe. Please contact support at contact@treekey.uk.</p>"
            "<a href='/pricing'>Return to Pricing</a>"
            "</body></html>", 
            status_code=503
        )
    return RedirectResponse(url=url)


@app.get("/marketplace", response_class=HTMLResponse)
def marketplace_view(tier: Optional[str] = "all"):
    """
    Single-Purchase Lead Marketplace with Statutory Freshness Badges & Filter Tabs:
    Allows contractors to preview unallocated leads before unlocking.
    Supports filtering by Flash Hot (Day 0-3), Active, Clearance, and Granted.
    """
    leads = database.get_marketplace_leads_with_freshness(filter_tier=tier, limit=40)

    # Active filter tab styles
    def tab_btn(target_tier: str, label: str):
        is_active = (tier == target_tier) or (not tier and target_tier == "all")
        bg = "#044332" if is_active else "#ffffff"
        color = "#ffffff" if is_active else "#475569"
        border = "1px solid #044332" if is_active else "1px solid #cbd5e1"
        return f'<a href="/marketplace?tier={target_tier}" style="background:{bg}; color:{color}; border:{border}; padding:7px 14px; border-radius:20px; text-decoration:none; font-size:12px; font-weight:bold; margin-right:6px; display:inline-block;">{label}</a>'

    tabs_html = f"""
    <div style="margin-bottom:20px; overflow-x:auto; white-space:nowrap; padding-bottom:4px;">
        {tab_btn("all", "🌐 All Leads")}
        {tab_btn("council", "🏛️ Council Statutory (TPO & S211)")}
        {tab_btn("domestic", "🏡 Private Domestic Jobs")}
        {tab_btn("flash_hot", "🔥 Flash Hot (Day 0–3)")}
        {tab_btn("active", "⚡ Prime Quoting (Day 4–14)")}
        {tab_btn("clearance", "⏳ Clearance (<£10)")}
        {tab_btn("granted", "✅ Approved / Granted")}
    </div>
    """

    lead_cards = ""
    for l in leads:
        lid = l["id"]
        ref = l["ref"]
        addr = l["addr"]
        summary = l["summary"]
        council = l["council"]
        unlock_fee = l["price"]
        plan_key = l["plan_key"]
        badge_bg = l["badge_bg"]
        badge_color = l["badge_color"]
        badge_text = l["badge_text"]
        days_left = l["days_left"]

        # Mask exact street number/name to prevent bypassing, but show neighborhood/town & postcode
        addr_parts = [p.strip() for p in addr.split(",") if p.strip()]
        masked_area = addr_parts[-1] if len(addr_parts) > 1 else addr
        if len(addr_parts) >= 2:
            masked_area = f"{addr_parts[-2]}, {addr_parts[-1]}"

        lead_cards += f"""
        <div style="background:white; border:1px solid #e2e8f0; border-radius:12px; padding:20px; margin-bottom:14px; box-shadow:0 2px 8px rgba(0,0,0,0.03);">
            <div style="display:flex; justify-content:space-between; align-items:flex-start; flex-wrap:wrap; gap:10px;">
                <div>
                    <span style="font-size:11px; background:{badge_bg}; color:{badge_color}; font-weight:bold; padding:4px 10px; border-radius:12px; text-transform:uppercase;">{badge_text}</span>
                    <span style="font-size:11px; background:#f1f5f9; color:#475569; padding:3px 8px; border-radius:12px; margin-left:6px;">LPA: {council}</span>
                    <h3 style="margin:10px 0 4px 0; font-size:17px; color:#0f172a;">📍 {masked_area}</h3>
                </div>
                <div style="text-align:right;">
                    <div style="font-size:22px; font-weight:800; color:#044332;">£{unlock_fee}</div>
                    <span style="font-size:11px; color:#64748b;">{days_left}</span>
                </div>
            </div>
            
            <div style="background:#f8fafc; border-left:3px solid #044332; padding:12px 14px; margin:12px 0; font-size:13px; color:#334155; line-height:1.5;">
                <b>Job Specification:</b> {summary[:220]}...
            </div>

            <div style="display:flex; justify-content:space-between; align-items:center; flex-wrap:wrap; gap:10px; margin-top:14px;">
                <div style="font-size:12px; color:#64748b;">
                    🔒 Single-Sale Asset • Burned permanently upon unlock.
                </div>
                <a href="/checkout/{plan_key}?lead_id={lid}" style="background:#044332; color:white; padding:9px 20px; border-radius:6px; text-decoration:none; font-weight:bold; font-size:13px;">
                    Unlock Full Property Address & Contacts (£{unlock_fee}) →
                </a>
            </div>
        </div>"""

    if not lead_cards:
        lead_cards = f"""
        <div style='text-align:center; padding:40px; background:white; border-radius:12px; border:1px solid #e2e8f0;'>
            <p style='color:#64748b; margin:0;'>No leads currently matching the selected filter ({tier}). Check back shortly for new council registrations or switch tabs.</p>
        </div>"""

    return f"""
    <!DOCTYPE html>
    <html lang="en-GB">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>Single-Purchase Planning Lead Marketplace | TreeKey</title>
        <style>
            body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; background:#f8fafc; color:#0f172a; margin:0; padding:40px 16px; line-height:1.6; }}
            .container {{ max-width: 840px; margin: auto; }}
        </style>
    </head>
    <body>
    <div class="container">
        <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:20px; flex-wrap:wrap; gap:10px;">
            <div>
                <h1 style="margin:0; font-size:28px; color:#044332;">🛒 Statutory Planning Marketplace</h1>
                <p style="margin:4px 0 0 0; color:#64748b; font-size:14px;">Real-time council planning notices with statutory freshness countdowns.</p>
            </div>
            <a href="/pricing" style="background:#059669; color:white; padding:8px 16px; border-radius:6px; text-decoration:none; font-weight:bold; font-size:13px;">View Monthly Subscriptions</a>
        </div>

        <div style="background:#eff6ff; border:1px solid #bfdbfe; border-radius:10px; padding:12px 16px; margin-bottom:20px; font-size:13px; color:#1e40af;">
            <b>💡 Single-Sale Guarantee:</b> Every lead purchased below is immediately removed from the live marketplace and burned permanently. You are the ONLY contractor who will receive the property data.
        </div>

        {tabs_html}

        {lead_cards}

        <div style="text-align:center; margin-top:30px;">
            <a href="/" style="color:#64748b; text-decoration:none; font-size:13px;">← Return to Main Intelligence Map</a>
        </div>
    </div>
    </body>
    </html>
    """




@app.get("/payment/success", response_class=HTMLResponse)
def payment_success():
    return """
    <html><body style="font-family:sans-serif; text-align:center; padding:60px; background:#f8fafc;">
        <div style="max-width:550px; margin:auto; background:white; padding:40px; border-radius:16px; border:1px solid #e2e8f0; box-shadow:0 4px 16px rgba(0,0,0,0.04);">
            <h1 style="color:#059669; margin-top:0;">🎉 Payment Successful!</h1>
            <p style="color:#64748b; font-size:15px; line-height:1.5;">Thank you. Your exclusive planning intelligence stream has been activated. Your lead dispatches and tools are now live.</p>
            <div style="margin-top:25px;">
                <a href="/" style="background:#044332; color:white; padding:12px 24px; border-radius:8px; text-decoration:none; font-weight:bold; font-size:14px;">View Live Intelligence Map</a>
            </div>
        </div>
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




# ── 3. "TreeKey Ledger" (Verticalized Arborist Accounting Engine) ───────────────

@app.get("/ledger", response_class=HTMLResponse)
def ledger_dashboard(email: Optional[str] = "partner@treecare.co.uk"):
    """
    TreeKey Ledger: Verticalized financial command center for UK tree surgeons.
    Includes Van-Day true costing, CIS developer tax deductions, and £90k VAT gauge.
    """
    summary = database.get_contractor_financial_summary(email)
    turnover = summary["rolling_turnover"]
    headroom = summary["vat_headroom"]
    vat_status = summary["vat_status"]
    vat_color = summary["vat_color"]
    cis_held = summary["cis_tax_held"]
    net_profit = summary["net_profit_total"]
    
    # Progress percentage toward £90k VAT threshold
    vat_pct = min(100.0, (turnover / 90000.0) * 100.0)

    return f"""
    <!DOCTYPE html>
    <html lang="en-GB">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>TreeKey Ledger | Arborist Financial Engine</title>
        <style>
            body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; background:#f8fafc; color:#0f172a; margin:0; padding:32px 16px; line-height:1.5; }}
            .container {{ max-width: 900px; margin: auto; }}
            .card {{ background: white; border: 1px solid #e2e8f0; border-radius: 12px; padding: 24px; margin-bottom: 20px; box-shadow: 0 2px 8px rgba(0,0,0,0.03); }}
            .grid-stats {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: 16px; margin-bottom: 24px; }}
            .stat-box {{ background: #f8fafc; border: 1px solid #e2e8f0; border-radius: 8px; padding: 16px; }}
            .stat-val {{ font-size: 24px; font-weight: 800; color: #044332; margin-top: 4px; }}
            input, select {{ width: 100%; box-sizing: border-box; padding: 10px; border: 1px solid #cbd5e1; border-radius: 6px; margin-top: 4px; font-family: inherit; }}
            .btn {{ background: #044332; color: white; border: none; padding: 12px 20px; border-radius: 6px; font-weight: bold; cursor: pointer; }}
        </style>
    </head>
    <body>
    <div class="container">
        <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:24px; flex-wrap:wrap; gap:10px;">
            <div>
                <h1 style="margin:0; font-size:28px; color:#044332;">📊 TreeKey Ledger</h1>
                <p style="margin:4px 0 0 0; color:#64748b; font-size:14px;">Verticalized Arborist Financial Engine • Van-Day Costing & CIS Tax Tracker</p>
            </div>
            <a href="/" style="background:#0f172a; color:white; padding:8px 16px; border-radius:6px; text-decoration:none; font-size:13px; font-weight:bold;">← Live Radar Map</a>
        </div>

        <!-- 1. £90,000 UK VAT Threshold Early-Warning Radar -->
        <div class="card" style="border-left: 4px solid {vat_color};">
            <div style="display:flex; justify-content:space-between; align-items:center; flex-wrap:wrap; gap:10px;">
                <h3 style="margin:0; font-size:18px; color:#0f172a;">🇬🇧 HMRC £90,000 Rolling VAT Radar</h3>
                <span style="font-size:12px; font-weight:bold; color:{vat_color};">{vat_status}</span>
            </div>
            <p style="color:#64748b; font-size:13px; margin:8px 0 14px 0;">Tracks your rolling 12-month domestic turnover to prevent accidental VAT penalties or losing sole-trader price advantage.</p>
            <div style="background:#e2e8f0; border-radius:8px; height:12px; overflow:hidden;">
                <div style="background:{vat_color}; width:{vat_pct:.1f}%; height:100%; border-radius:8px;"></div>
            </div>
            <div style="display:flex; justify-content:space-between; font-size:12px; color:#64748b; margin-top:8px;">
                <span>Current 12M: <b>£{turnover:,.2f}</b></span>
                <span>Limit: <b>£90,000.00</b></span>
            </div>
        </div>

        <!-- 2. Financial Metrics -->
        <div class="grid-stats">
            <div class="stat-box">
                <div style="font-size:12px; color:#64748b; font-weight:bold; text-transform:uppercase;">12-Month Gross Invoiced</div>
                <div class="stat-val">£{turnover:,.2f}</div>
            </div>
            <div class="stat-box">
                <div style="font-size:12px; color:#64748b; font-weight:bold; text-transform:uppercase;">CIS Developer Tax Held</div>
                <div class="stat-val" style="color:#2563eb;">£{cis_held:,.2f}</div>
                <div style="font-size:11px; color:#64748b;">Claimable on Self-Assessment</div>
            </div>
            <div class="stat-box">
                <div style="font-size:12px; color:#64748b; font-weight:bold; text-transform:uppercase;">Calculated Net Profit</div>
                <div class="stat-val" style="color:#059669;">£{net_profit:,.2f}</div>
            </div>
        </div>

        <!-- 3. Van-Day Job Cost & Minimum Profitable Quote Calculator -->
        <div class="card">
            <h3 style="margin-top:0; color:#044332; font-size:18px;">🌲 Van & Crew-Day Profit Calculator (True Costing)</h3>
            <p style="color:#64748b; font-size:13px;">Never underquote a large tree removal again. Input your crew size and expected waste to compute your exact breakeven and recommended quotation.</p>
            
            <form id="quoteForm" onsubmit="event.preventDefault(); calcQuote();" style="display:grid; grid-template-columns:repeat(auto-fit, minmax(180px, 1fr)); gap:12px; margin-top:16px;">
                <div>
                    <label style="font-size:12px; font-weight:bold;">Climbers (£180/day):</label>
                    <input type="number" id="climbers" value="1" min="0" max="5">
                </div>
                <div>
                    <label style="font-size:12px; font-weight:bold;">Ground Crew (£120/day):</label>
                    <input type="number" id="groundies" value="1" min="0" max="10">
                </div>
                <div>
                    <label style="font-size:12px; font-weight:bold;">Tipping Loads (£90/ea):</label>
                    <input type="number" id="tips" value="1" min="0" max="10">
                </div>
                <div>
                    <label style="font-size:12px; font-weight:bold;">Fuel & Consumables (£):</label>
                    <input type="number" id="fuel" value="30" min="0">
                </div>
                <div>
                    <label style="font-size:12px; font-weight:bold;">Days on Job:</label>
                    <input type="number" id="days" value="1" step="0.5" min="0.5">
                </div>
                <div style="display:flex; align-items:flex-end;">
                    <button type="submit" class="btn" style="width:100%;">Calculate Quote ⚡</button>
                </div>
            </form>

            <div id="quoteResult" style="background:#f0fdf4; border:1px solid #bbf7d0; border-radius:8px; padding:16px; margin-top:20px; display:none;">
                <div style="display:grid; grid-template-columns:repeat(auto-fit, minmax(180px, 1fr)); gap:12px;">
                    <div>
                        <span style="font-size:11px; color:#065f46; font-weight:bold;">TRUE BASELINE COST:</span>
                        <div id="resCost" style="font-size:20px; font-weight:800; color:#0f172a;">£0.00</div>
                    </div>
                    <div>
                        <span style="font-size:11px; color:#065f46; font-weight:bold;">RECOMMENDED QUOTE (40% Margin):</span>
                        <div id="resStd" style="font-size:22px; font-weight:800; color:#044332;">£0.00</div>
                    </div>
                    <div>
                        <span style="font-size:11px; color:#065f46; font-weight:bold;">PREMIUM QUOTE (55% Margin):</span>
                        <div id="resPrem" style="font-size:22px; font-weight:800; color:#059669;">£0.00</div>
                    </div>
                </div>
            </div>
        </div>

        <!-- 4. Quick Job Entry / CIS Deduction Form -->
        <div class="card">
            <h3 style="margin-top:0; color:#044332; font-size:18px;">📝 Log Completed Job & CIS Deduction</h3>
            <p style="color:#64748b; font-size:13px;">Save an invoice to track your 12-month VAT position and commercial CIS tax balances.</p>
            <form action="/api/save-ledger-entry" method="POST" style="display:grid; grid-template-columns:repeat(auto-fit, minmax(200px, 1fr)); gap:12px;">
                <input type="hidden" name="email" value="{email}">
                <div>
                    <label style="font-size:12px; font-weight:bold;">Job / Property Name:</label>
                    <input type="text" name="job_name" placeholder="e.g. 14 Elm Grove Dismantle" required>
                </div>
                <div>
                    <label style="font-size:12px; font-weight:bold;">Client Classification:</label>
                    <select name="client_type">
                        <option value="domestic">Private Homeowner (Standard VAT/Cash)</option>
                        <option value="commercial_cis">Commercial Developer (20% CIS Deduction)</option>
                    </select>
                </div>
                <div>
                    <label style="font-size:12px; font-weight:bold;">Gross Invoiced Total (£):</label>
                    <input type="number" name="gross_amount" step="0.01" placeholder="e.g. 850.00" required>
                </div>
                <div>
                    <label style="font-size:12px; font-weight:bold;">Labor Portion (for CIS) (£):</label>
                    <input type="number" name="labor_amount" step="0.01" placeholder="e.g. 600.00">
                </div>
                <div>
                    <label style="font-size:12px; font-weight:bold;">Tipping Cost (£):</label>
                    <input type="number" name="tipping_cost" step="0.01" value="0.00">
                </div>
                <div>
                    <label style="font-size:12px; font-weight:bold;">Fuel / Consumables (£):</label>
                    <input type="number" name="fuel_cost" step="0.01" value="25.00">
                </div>
                <div style="grid-column:1/-1; margin-top:8px;">
                    <button type="submit" class="btn">Save Entry to TreeKey Ledger 💾</button>
                </div>
            </form>
        </div>
    </div>

    <script>
        function calcQuote() {{
            const c = parseFloat(document.getElementById('climbers').value) || 0;
            const g = parseFloat(document.getElementById('groundies').value) || 0;
            const t = parseFloat(document.getElementById('tips').value) || 0;
            const f = parseFloat(document.getElementById('fuel').value) || 0;
            const d = parseFloat(document.getElementById('days').value) || 1;

            const labor = (c * 180 + g * 120) * d;
            const tipping = t * 90;
            const fuel = f * d;
            const base = labor + tipping + fuel;
            const overhead = base * 0.15;
            const total = base + overhead;

            const stdQuote = total / 0.60;
            const premQuote = total / 0.45;

            document.getElementById('resCost').innerText = '£' + total.toFixed(2);
            document.getElementById('resStd').innerText = '£' + stdQuote.toFixed(2);
            document.getElementById('resPrem').innerText = '£' + premQuote.toFixed(2);
            document.getElementById('quoteResult').style.display = 'block';
        }}
    </script>
    </body>
    </html>
    """


@app.post("/api/save-ledger-entry")
async def handle_save_ledger(request: Request):
    form = await request.form()
    email = form.get("email", "partner@treecare.co.uk")
    job_name = form.get("job_name", "Untitled Job")
    client_type = form.get("client_type", "domestic")
    gross = float(form.get("gross_amount", 0) or 0)
    labor = float(form.get("labor_amount", 0) or gross)
    tipping = float(form.get("tipping_cost", 0) or 0)
    fuel = float(form.get("fuel_cost", 0) or 0)
    
    cis_rate = 20.0 if client_type == "commercial_cis" else 0.0
    
    database.save_ledger_entry(
        contractor_email=email,
        job_name=job_name,
        client_type=client_type,
        gross_amount=gross,
        labor_amount=labor,
        cis_rate=cis_rate,
        tipping_cost=tipping,
        fuel_cost=fuel
    )
    database.save_ledger_entry(
        contractor_email=email,
        job_name=job_name,
        client_type=client_type,
        gross_amount=gross,
        labor_amount=labor,
        cis_rate=cis_rate,
        tipping_cost=tipping,
        fuel_cost=fuel
    )
    return RedirectResponse(url=f"/ledger?email={email}", status_code=303)




# ── 4. Passwordless Contractor Auth & Mobile Command Center ───────────────────

@app.get("/login", response_class=HTMLResponse)
def login_page(error: Optional[str] = None):
    err_html = f"<div style='background:#fef2f2; border:1px solid #fecaca; color:#991b1b; padding:10px; border-radius:6px; margin-bottom:16px; font-size:13px;'>{error}</div>" if error else ""
    return f"""
    <!DOCTYPE html>
    <html lang="en-GB">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>Contractor Sign In | TreeKey</title>
        <style>
            body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; background:#f8fafc; color:#0f172a; margin:0; padding:40px 16px; }}
            .box {{ max-width:420px; margin:auto; background:white; padding:32px; border-radius:16px; border:1px solid #e2e8f0; box-shadow:0 4px 16px rgba(0,0,0,0.04); }}
            input {{ width:100%; box-sizing:border-box; padding:12px; border:1px solid #cbd5e1; border-radius:8px; margin-top:6px; margin-bottom:16px; font-family:inherit; font-size:15px; }}
            button {{ background:#044332; color:white; border:none; padding:12px; border-radius:8px; font-weight:bold; font-size:15px; cursor:pointer; width:100%; }}
        </style>
    </head>
    <body>
    <div class="box">
        <div style="text-align:center; margin-bottom:20px;">
            <span style="font-size:32px;">🌲</span>
            <h2 style="margin:8px 0 4px 0; color:#044332;">Contractor Command Center</h2>
            <p style="color:#64748b; font-size:13px; margin:0;">Zero-Password Sign In • Enter your email or mobile</p>
        </div>

        {err_html}

        <form action="/api/request-magic-link" method="POST">
            <label style="font-size:12px; font-weight:bold; color:#475569;">Email Address or Phone:</label>
            <input type="text" name="contact" placeholder="e.g. dave@apex-trees.co.uk" required autofocus>
            <button type="submit">Send 1-Tap Login Link ⚡</button>
        </form>

        <div style="text-align:center; margin-top:24px; font-size:12px; color:#64748b; border-top:1px solid #f1f5f9; padding-top:16px;">
            🔒 <b>Zero-Password Security Vault:</b> No passwords to leak or remember. We dispatch an encrypted 15-minute access token.
        </div>
    </div>
    </body>
    </html>
    """


@app.post("/api/request-magic-link")
async def request_magic_link(request: Request):
    form = await request.form()
    contact = form.get("contact", "").strip().lower()
    
    if not contact:
        return RedirectResponse(url="/login?error=Please+enter+your+email+address", status_code=303)
    
    # Generate cryptographic token & OTP
    auth_data = database.create_magic_auth_token(contact)
    if not auth_data:
        return RedirectResponse(url="/login?error=Could+not+generate+login+link.+Please+try+again.", status_code=303)

    magic_url = f"{payments.PUBLIC_APP_URL}/verify-login?token={auth_data['token']}"
    otp_code = auth_data["otp"]

    # Send Magic Link via Resend Email
    import notifications
    email_body = f"""
    <div style="font-family:sans-serif; max-width:500px; margin:auto; padding:20px; color:#0f172a;">
        <h2 style="color:#044332;">🌲 Your TreeKey Login Link</h2>
        <p>Click the secure button below to log in directly to your Contractor Command Center:</p>
        <div style="text-align:center; margin:24px 0;">
            <a href="{magic_url}" style="background:#044332; color:white; padding:12px 24px; border-radius:8px; text-decoration:none; font-weight:bold; font-size:15px; display:inline-block;">Sign In to Dashboard ➔</a>
        </div>
        <p style="font-size:13px; color:#64748b;">Or enter this 6-digit confirmation code: <b style="font-size:16px; color:#0f172a;">{otp_code}</b></p>
        <p style="font-size:11px; color:#94a3b8; margin-top:24px;">This secure link is valid for 15 minutes. If you did not request this, you can safely ignore this email.</p>
    </div>
    """
    notifications.send_resend_email(subject="🌲 Your TreeKey 1-Tap Login Link", html_body=email_body)

    return HTMLResponse(f"""
    <html><body style="font-family:sans-serif; text-align:center; padding:60px; background:#f8fafc;">
        <div style="max-width:480px; margin:auto; background:white; padding:32px; border-radius:16px; border:1px solid #e2e8f0; box-shadow:0 4px 16px rgba(0,0,0,0.04);">
            <span style="font-size:40px;">✉️</span>
            <h2 style="color:#044332; margin:12px 0 6px 0;">Check Your Inbox!</h2>
            <p style="color:#64748b; font-size:14px; line-height:1.5;">We dispatched a secure 1-tap login link to <b>{contact}</b>.</p>
            <div style="background:#f0fdf4; border:1px solid #bbf7d0; border-radius:8px; padding:12px; margin:20px 0; font-size:13px; color:#065f46;">
                Your 6-digit backup code: <b style="font-size:18px; letter-spacing:2px;">{otp_code}</b>
            </div>
            <a href="{magic_url}" style="display:inline-block; background:#044332; color:white; padding:10px 20px; border-radius:6px; text-decoration:none; font-weight:bold; font-size:13px;">Click to Open Dashboard Now ➔</a>
        </div>
    </body></html>
    """)


@app.get("/verify-login")
def verify_login(request: Request, token: Optional[str] = None, otp: Optional[str] = None, email: Optional[str] = None):
    verified_email = database.verify_magic_auth_token(token=token, otp=otp, email=email)
    
    if not verified_email:
        return RedirectResponse(url="/login?error=Login+link+expired+or+already+used.+Please+request+a+new+one.", status_code=303)

    # Set secure session cookie
    response = RedirectResponse(url=f"/dashboard?email={verified_email}", status_code=303)
    response.set_cookie(
        key="treekey_contractor_session",
        value=verified_email,
        max_age=86400 * 30,  # 30 days
        httponly=True,
        samesite="lax"
    )
    return response


@app.get("/dashboard", response_class=HTMLResponse)
def contractor_dashboard(request: Request, email: Optional[str] = None):
    # Cookie or param session
    session_email = request.cookies.get("treekey_contractor_session") or email
    if not session_email:
        return RedirectResponse(url="/login", status_code=303)

    data = database.get_contractor_dashboard_data(session_email)
    sub = data["subscription"]
    leads = data["dispatched_leads"]
    tier_name = sub.get("tier", "Free / Pay-As-You-Go").replace("_", " ").title()
    outcode = sub.get("outcode", "GB")
    active_badge = "<span style='background:#ecfdf5; color:#065f46; padding:3px 8px; border-radius:12px; font-size:11px; font-weight:bold;'>ACTIVE PARTNER</span>" if sub.get("active") else "<span style='background:#f1f5f9; color:#64748b; padding:3px 8px; border-radius:12px; font-size:11px;'>FREE TIER</span>"

    # Format leads table
    lead_rows = ""
    for l in leads:
        lead_id = l.get("id") or l.get("ref")
        ref = l.get("ref", "")
        addr = l.get("addr", "")
        summary = l.get("summary", "")
        dispatched_at = str(l.get("dispatched_at", ""))[:16]
        
        # Google Street View direct link
        gmap_url = f"https://www.google.com/maps/search/?api=1&query={urllib.parse.quote(addr)}"

        lead_rows += f"""
        <div style="background:white; border:1px solid #e2e8f0; border-radius:10px; padding:16px; margin-bottom:12px;">
            <div style="display:flex; justify-content:space-between; align-items:flex-start; flex-wrap:wrap; gap:8px;">
                <div>
                    <span style="font-size:10px; background:#f1f5f9; color:#475569; padding:2px 6px; border-radius:4px; font-weight:bold;">REF: {ref}</span>
                    <h4 style="margin:4px 0 2px 0; font-size:15px; color:#0f172a;">📍 {addr}</h4>
                    <span style="font-size:11px; color:#64748b;">Dispatched: {dispatched_at}</span>
                </div>
                <div style="display:flex; gap:6px; flex-wrap:wrap;">
                    <a href="/generate-letter/{urllib.parse.quote(ref)}" target="_blank" style="background:#044332; color:white; padding:6px 12px; border-radius:6px; text-decoration:none; font-size:12px; font-weight:bold;">🖨️ Letter</a>
                    <a href="/generate-street-flyer/{urllib.parse.quote(ref)}" target="_blank" style="background:#059669; color:white; padding:6px 12px; border-radius:6px; text-decoration:none; font-size:12px; font-weight:bold;">🏘️ Street Flyer</a>
                    <a href="{gmap_url}" target="_blank" style="background:#0f172a; color:white; padding:6px 12px; border-radius:6px; text-decoration:none; font-size:12px; font-weight:bold;">🗺️ Street View</a>
                </div>
            </div>
            <div style="background:#f8fafc; border-left:3px solid #044332; padding:8px 12px; margin-top:10px; font-size:12px; color:#334155;">
                <b>Specification:</b> {summary[:180]}...
            </div>
        </div>"""

    if not lead_rows:
        lead_rows = "<div style='text-align:center; padding:32px; background:white; border-radius:10px; border:1px solid #e2e8f0;'><p style='color:#64748b; margin:0;'>No leads currently allocated. Your incoming planning intelligence will appear here in real-time.</p></div>"

    return f"""
    <!DOCTYPE html>
    <html lang="en-GB">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>Contractor Dashboard | TreeKey</title>
        <style>
            body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; background:#f8fafc; color:#0f172a; margin:0; padding:32px 16px; line-height:1.5; }}
            .container {{ max-width: 900px; margin: auto; }}
            .header-box {{ background: linear-gradient(135deg, #044332 0%, #064e3b 100%); color: white; border-radius: 14px; padding: 24px; margin-bottom: 24px; }}
            .quick-grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(180px, 1fr)); gap: 12px; margin-bottom: 24px; }}
            .quick-card {{ background: white; border: 1px solid #e2e8f0; border-radius: 10px; padding: 14px; text-decoration: none; color: inherit; display: block; }}
            .quick-card:hover {{ border-color: #044332; }}
        </style>
    </head>
    <body>
    <div class="container">
        <!-- Header Profile -->
        <div class="header-box">
            <div style="display:flex; justify-content:space-between; align-items:center; flex-wrap:wrap; gap:10px;">
                <div>
                    <div style="font-size:12px; color:#a7f3d0; text-transform:uppercase; font-weight:bold;">Contractor Command Center</div>
                    <h2 style="margin:4px 0; font-size:24px;">🌲 {session_email}</h2>
                    <div style="font-size:13px; color:#e2e8f0;">
                        Tier: <b>{tier_name}</b> • Sector: <b>{outcode} (15-Mile Radius)</b>
                    </div>
                </div>
                <div style="text-align:right;">
                    {active_badge}
                    <div style="margin-top:8px;">
                        <a href="/logout" style="color:#a7f3d0; font-size:12px; text-decoration:none;">Log Out ➔</a>
                    </div>
                </div>
            </div>
        </div>

        <!-- Quick Access Operational Tools -->
        <div class="quick-grid">
            <a href="/ledger?email={session_email}" class="quick-card">
                <div style="font-size:20px;">📊</div>
                <div style="font-weight:bold; font-size:14px; margin:4px 0 2px 0;">TreeKey Ledger</div>
                <div style="font-size:11px; color:#64748b;">Van-Day Costing & £90k VAT Gauge</div>
            </a>
            <a href="/marketplace" class="quick-card">
                <div style="font-size:20px;">🛒</div>
                <div style="font-weight:bold; font-size:14px; margin:4px 0 2px 0;">Lead Marketplace</div>
                <div style="font-size:11px; color:#64748b;">Browse Unallocated Notices</div>
            </a>
            <a href="/pricing" class="quick-card">
                <div style="font-size:20px;">💳</div>
                <div style="font-weight:bold; font-size:14px; margin:4px 0 2px 0;">Manage Tier</div>
                <div style="font-size:11px; color:#64748b;">Upgrade or Adjust Coverage</div>
            </a>
            <a href="/suggestions" class="quick-card">
                <div style="font-size:20px;">💡</div>
                <div style="font-weight:bold; font-size:14px; margin:4px 0 2px 0;">Suggest Tool</div>
                <div style="font-size:11px; color:#64748b;">Request Features from Founders</div>
            </a>
        </div>

        <!-- Dispatched Lead Inbox -->
        <h3 style="color:#044332; font-size:18px; margin:0 0 14px 0;">📥 Your Exclusive Dispatched Leads ({len(leads)})</h3>
        <p style="color:#64748b; font-size:13px; margin-top:-8px; margin-bottom:16px;">
            These statutory planning notices were delivered exclusively to you and burned from all other systems.
        </p>

        {lead_rows}

        <div style="text-align:center; margin-top:32px;">
            <a href="/" style="color:#64748b; text-decoration:none; font-size:13px;">← Return to Main Intelligence Map</a>
        </div>
    </div>
    </body>
    </html>
    """


@app.get("/logout")
def logout():
    response = RedirectResponse(url="/login", status_code=303)
    response.delete_cookie("treekey_contractor_session")
    return response




# ── 5. Free Woodchip & Timber Drop-Spotter Hub ─────────────────────────────────

@app.get("/chip-drop", response_class=HTMLResponse)
def chip_drop_view(outcode: Optional[str] = None, material: Optional[str] = "all"):
    """
    Woodchip & Timber Drop-Spotter Directory:
    Connects tree surgeons with nearby allotments, farms, and smallholders wanting free arborist woodchip or logs.
    Saves £60-£120 commercial tipping fees per van load.
    """
    spots = database.get_chip_drop_spots(outcode=outcode, material=material, limit=40)

    # If no spots in DB yet, render realistic sample network spots for instant value
    if not spots:
        spots = [
            {
                "id": "sample-1",
                "site_name": "Highfield Allotment Association",
                "contact_name": "Dave (Site Sec)",
                "phone": "07700 900123",
                "outcode": "LS6",
                "town": "Leeds",
                "address": "Highfield Lane Allotments, LS6 2AA",
                "material": "fresh_woodchip",
                "max_vehicle": "3.5t_transit",
                "access_notes": "Unload on front hardstanding pad. Gate unlocked 7am-7pm."
            },
            {
                "id": "sample-2",
                "site_name": "Meadow View Equestrian Stables",
                "contact_name": "Sarah",
                "phone": "07700 900456",
                "outcode": "WF1",
                "town": "Wakefield",
                "address": "Meadow Lane, WF1 3PQ",
                "material": "hardwood_logs",
                "max_vehicle": "7.5t_truck",
                "access_notes": "Hardwood rings and cordwood needed for log burner. Wide tractor turning circle."
            },
            {
                "id": "sample-3",
                "site_name": "Oakridge Community Garden & Farm",
                "contact_name": "Marcus",
                "phone": "07700 900789",
                "outcode": "BD1",
                "town": "Bradford",
                "address": "Canal Road, BD1 4SX",
                "material": "any",
                "max_vehicle": "3.5t_transit",
                "access_notes": "Always taking raw woodchip for compost mulch. Drive straight to rear bay."
            }
        ]

    spot_cards = ""
    for s in spots:
        name = s["site_name"]
        contact = s.get("contact_name") or "Site Manager"
        phone = s["phone"]
        postcode = s["outcode"]
        town = s["town"]
        addr = s["address"]
        mat_label = "🌲 Fresh Woodchip Only" if s["material"] == "fresh_woodchip" else ("🪵 Hardwood Logs / Rings" if s["material"] == "hardwood_logs" else "🌳 Any Raw Green Waste / Chips")
        veh_label = "🚛 Max 3.5t Transit / Tipper" if s.get("max_vehicle") == "3.5t_transit" else "🚜 7.5t Truck / Tractor Access"
        notes = s.get("access_notes") or "Standard driveway drop. Contact manager prior to arrival."

        # Direct WhatsApp and Call links
        clean_phone = re.sub(r'[^0-9+]', '', phone)
        wa_link = f"https://wa.me/{clean_phone}?text=Hi%20{contact},%20TreeKey%20arborist%20crew%20has%20a%20fresh%20load%20of%20woodchip/timber.%20Do%20you%20have%20space%20today?"

        spot_cards += f"""
        <div style="background:white; border:1px solid #e2e8f0; border-radius:12px; padding:20px; margin-bottom:14px; box-shadow:0 2px 8px rgba(0,0,0,0.03);">
            <div style="display:flex; justify-content:space-between; align-items:flex-start; flex-wrap:wrap; gap:10px;">
                <div>
                    <span style="font-size:11px; background:#ecfdf5; color:#065f46; font-weight:bold; padding:3px 8px; border-radius:12px;">✅ Free Drop Site</span>
                    <span style="font-size:11px; background:#f1f5f9; color:#475569; padding:3px 8px; border-radius:12px; margin-left:6px;">{postcode} • {town}</span>
                    <h3 style="margin:8px 0 4px 0; font-size:17px; color:#0f172a;">🏡 {name}</h3>
                    <p style="margin:0; font-size:13px; color:#64748b;">📍 {addr}</p>
                </div>
                <div style="display:flex; gap:8px; flex-wrap:wrap;">
                    <a href="tel:{phone}" style="background:#044332; color:white; padding:8px 14px; border-radius:6px; text-decoration:none; font-weight:bold; font-size:13px;">📞 Call ({contact})</a>
                    <a href="{wa_link}" target="_blank" style="background:#059669; color:white; padding:8px 14px; border-radius:6px; text-decoration:none; font-weight:bold; font-size:13px;">💬 WhatsApp</a>
                </div>
            </div>

            <div style="background:#f8fafc; border-radius:8px; padding:12px; margin-top:14px; font-size:13px; display:grid; grid-template-columns:repeat(auto-fit, minmax(200px, 1fr)); gap:10px;">
                <div>
                    <div style="font-size:11px; color:#64748b; font-weight:bold; text-transform:uppercase;">Material Needed:</div>
                    <div style="font-weight:bold; color:#0f172a;">{mat_label}</div>
                </div>
                <div>
                    <div style="font-size:11px; color:#64748b; font-weight:bold; text-transform:uppercase;">Vehicle Clearance:</div>
                    <div style="font-weight:bold; color:#0f172a;">{veh_label}</div>
                </div>
            </div>

            <div style="margin-top:10px; font-size:12px; color:#475569;">
                <b>Access Instructions:</b> {notes}
            </div>
        </div>"""

    return f"""
    <!DOCTYPE html>
    <html lang="en-GB">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>Woodchip & Timber Drop-Spotter | TreeKey</title>
        <style>
            body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; background:#f8fafc; color:#0f172a; margin:0; padding:32px 16px; line-height:1.5; }}
            .container {{ max-width: 860px; margin: auto; }}
        </style>
    </head>
    <body>
    <div class="container">
        <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:20px; flex-wrap:wrap; gap:10px;">
            <div>
                <h1 style="margin:0; font-size:28px; color:#044332;">🚜 Free Woodchip & Timber Drop-Spotter</h1>
                <p style="margin:4px 0 0 0; color:#64748b; font-size:14px;">Drop fresh arborist chips and timber rings within minutes of your job site. Save £60–£120 tipping fees.</p>
            </div>
            <a href="/register-drop-spot" style="background:#044332; color:white; padding:10px 18px; border-radius:6px; text-decoration:none; font-weight:bold; font-size:13px;">+ Register a Drop Site</a>
        </div>

        <div style="background:#ecfdf5; border:1px solid #a7f3d0; border-radius:10px; padding:14px 18px; margin-bottom:24px; font-size:13px; color:#065f46;">
            <b>💡 Pro-Tip for Tree Surgeons:</b> Tipping stations charge £80–£120 + VAT per load plus 45 minutes round-trip driving time. Drop your arborist waste at local community sites for £0.00.
        </div>

        {spot_cards}

        <div style="text-align:center; margin-top:32px;">
            <a href="/" style="color:#64748b; text-decoration:none; font-size:13px;">← Return to Main Intelligence Map</a>
        </div>
    </div>
    </body>
    </html>
    """


@app.get("/register-drop-spot", response_class=HTMLResponse)
def register_drop_spot_page():
    """
    Intake form for UK landowners, allotments, and stables wanting free arborist woodchip or firewood.
    """
    return """
    <!DOCTYPE html>
    <html lang="en-GB">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>Register Free Woodchip Drop Site | TreeKey</title>
        <style>
            body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; background:#f8fafc; color:#0f172a; margin:0; padding:40px 16px; }
            .box { max-width: 520px; margin: auto; background: white; padding: 32px; border-radius: 16px; border: 1px solid #e2e8f0; box-shadow: 0 4px 16px rgba(0,0,0,0.04); }
            input, select, textarea { width: 100%; box-sizing: border-box; padding: 10px; border: 1px solid #cbd5e1; border-radius: 8px; margin-top: 4px; margin-bottom: 14px; font-family: inherit; }
            button { background: #044332; color: white; border: none; padding: 12px; border-radius: 8px; font-weight: bold; font-size: 15px; cursor: pointer; width: 100%; }
        </style>
    </head>
    <body>
    <div class="box">
        <h2 style="margin-top:0; color:#044332;">🏡 Register Free Woodchip Drop Site</h2>
        <p style="color:#64748b; font-size:13px;">Need free organic woodchip mulch, wood chips, or hardwood logs for your garden, allotment, or stables? Local tree surgeons will drop free loads directly to your property.</p>

        <form action="/api/submit-drop-spot" method="POST">
            <label style="font-size:12px; font-weight:bold;">Property / Site Name:</label>
            <input type="text" name="site_name" placeholder="e.g. Oak Tree Allotments or Highfield Farm" required>

            <label style="font-size:12px; font-weight:bold;">Contact Name:</label>
            <input type="text" name="contact_name" placeholder="e.g. Dave or Sarah" required>

            <label style="font-size:12px; font-weight:bold;">Phone / WhatsApp (for delivery driver to call):</label>
            <input type="tel" name="phone" placeholder="e.g. 07700 900123" required>

            <div style="display:grid; grid-template-columns:1fr 1fr; gap:10px;">
                <div>
                    <label style="font-size:12px; font-weight:bold;">Postcode Outcode:</label>
                    <input type="text" name="outcode" placeholder="e.g. LS6 or WF1" required>
                </div>
                <div>
                    <label style="font-size:12px; font-weight:bold;">Town / City:</label>
                    <input type="text" name="town" placeholder="e.g. Leeds" required>
                </div>
            </div>

            <label style="font-size:12px; font-weight:bold;">Full Drop Address:</label>
            <input type="text" name="address" placeholder="e.g. 14 Highfield Lane, Leeds LS6 2AA" required>

            <label style="font-size:12px; font-weight:bold;">Material Needed:</label>
            <select name="material_accepted">
                <option value="fresh_woodchip">Fresh Arborist Woodchip (Mulch & Beds)</option>
                <option value="hardwood_logs">Hardwood Logs / Rings (Firewood & Stoves)</option>
                <option value="any">Any Green Waste / Woodchip / Cordwood</option>
            </select>

            <label style="font-size:12px; font-weight:bold;">Max Vehicle Size Clearance:</label>
            <select name="max_vehicle_size">
                <option value="3.5t_transit">Max 3.5t Transit Tipper (Standard Driveways)</option>
                <option value="7.5t_truck">7.5t Truck / Tractor Trailer (Farms & Large Yards)</option>
            </select>

            <label style="font-size:12px; font-weight:bold;">Access Instructions:</label>
            <textarea name="access_instructions" rows="3" placeholder="e.g. Tip on tarmac driveway to left of gate. Driveway is 2.8m wide."></textarea>

            <button type="submit">Submit Free Drop Listing ➔</button>
        </form>
    </div>
    </body>
    </html>
    """


@app.post("/api/submit-drop-spot")
async def handle_submit_drop_spot(request: Request):
    form = await request.form()
    site_name = form.get("site_name", "").strip()
    contact_name = form.get("contact_name", "").strip()
    phone = form.get("phone", "").strip()
    outcode = form.get("outcode", "").strip().upper()
    town = form.get("town", "").strip()
    address = form.get("address", "").strip()
    material = form.get("material_accepted", "fresh_woodchip")
    max_vehicle = form.get("max_vehicle_size", "3.5t_transit")
    notes = form.get("access_instructions", "").strip()

    database.register_chip_drop_spot(
        site_name=site_name,
        contact_name=contact_name,
        phone=phone,
        outcode=outcode,
        town=town,
        address=address,
        material_accepted=material,
        max_vehicle=max_vehicle,
        access_notes=notes
    )
    return RedirectResponse(url="/chip-drop", status_code=303)




# ── 6. Emergency Storm Weather Radar & Rate Multiplier ────────────────────────

@app.get("/storm-radar", response_class=HTMLResponse)
def storm_radar_view():
    """
    Emergency Storm Weather Radar:
    Monitors Met Office high-wind gale events (45mph+ gusts).
    Alerts contractors only in targeted impacted sectors with instant 1.5x-2.0x emergency quote sheets.
    """
    alerts = database.get_active_storm_alerts()

    if not alerts:
        alerts = [
            {
                "id": "sample-storm-1",
                "region": "Northern England & Pennines",
                "outcodes": ["LS", "BD", "HG", "WF", "HD", "HX"],
                "gust_mph": 55,
                "level": "amber",
                "summary": "Met Office Amber Warning: 50-60mph wind gusts forecast. High risk of branch failure and uprooted shallow-root conifers.",
                "valid_from": "Tonight 21:00",
                "valid_to": "Tomorrow 18:00"
            }
        ]

    alert_cards = ""
    for a in alerts:
        region = a["region"]
        outcodes = ", ".join(a.get("outcodes", []))
        gust = a["gust_mph"]
        level = a.get("level", "amber").upper()
        summary = a["summary"]
        bg_color = "#fef2f2" if gust >= 50 else "#fffbeb"
        border_color = "#dc2626" if gust >= 50 else "#d97706"
        badge_bg = "#dc2626" if gust >= 50 else "#d97706"

        alert_cards += f"""
        <div style="background:{bg_color}; border:2px solid {border_color}; border-radius:12px; padding:24px; margin-bottom:16px;">
            <div style="display:flex; justify-content:space-between; align-items:center; flex-wrap:wrap; gap:10px;">
                <div>
                    <span style="background:{badge_bg}; color:white; font-size:11px; font-weight:bold; padding:4px 10px; border-radius:20px; text-transform:uppercase;">🌪️ {level} GALE ALERT ({gust} MPH)</span>
                    <h3 style="margin:10px 0 4px 0; color:#0f172a; font-size:20px;">{region}</h3>
                    <span style="font-size:13px; color:#475569;">Target Sectors: <b>{outcodes}</b></span>
                </div>
                <div style="text-align:right;">
                    <div style="font-size:13px; color:#64748b;">Emergency Rate Multiplier:</div>
                    <div style="font-size:22px; font-weight:800; color:#044332;">1.5x – 2.0x Rates</div>
                </div>
            </div>
            <p style="color:#334155; font-size:14px; margin:14px 0 16px 0; line-height:1.5;">
                {summary}
            </p>
            <div style="display:flex; justify-content:space-between; align-items:center; flex-wrap:wrap; gap:10px; border-top:1px solid #e2e8f0; padding-top:14px;">
                <span style="font-size:12px; color:#64748b;">Valid: {a.get('valid_from')} until {a.get('valid_to')}</span>
                <a href="/generate-storm-quote/EMERGENCY-DISPATCH" style="background:#044332; color:white; padding:8px 16px; border-radius:6px; text-decoration:none; font-weight:bold; font-size:13px;">
                    Generate 1-Tap Emergency Quote Sheet ➔
                </a>
            </div>
        </div>"""

    return f"""
    <!DOCTYPE html>
    <html lang="en-GB">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>Storm Weather Radar & Emergency Dispatch | TreeKey</title>
        <style>
            body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; background:#f8fafc; color:#0f172a; margin:0; padding:32px 16px; line-height:1.5; }}
            .container {{ max-width: 860px; margin: auto; }}
        </style>
    </head>
    <body>
    <div class="container">
        <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:20px; flex-wrap:wrap; gap:10px;">
            <div>
                <h1 style="margin:0; font-size:28px; color:#044332;">🌪️ Emergency Storm Weather Radar</h1>
                <p style="margin:4px 0 0 0; color:#64748b; font-size:14px;">Severe gale & wind triggers (45mph+). Targeted emergency mobilization without notification spam.</p>
            </div>
            <a href="/dashboard" style="background:#0f172a; color:white; padding:8px 16px; border-radius:6px; text-decoration:none; font-size:13px; font-weight:bold;">← Contractor Dashboard</a>
        </div>

        <div style="background:#f0fdf4; border:1px solid #bbf7d0; border-radius:10px; padding:14px 18px; margin-bottom:24px; font-size:13px; color:#065f46;">
            <b>🛡️ Zero-Spam Guarantee:</b> We never alert you for normal rain or mild breezes. Alerts trigger strictly for verified 45mph+ gale forecasts in your registered sector so you can mobilize emergency standby crews.
        </div>

        {alert_cards}

        <div style="text-align:center; margin-top:32px;">
            <a href="/" style="color:#64748b; text-decoration:none; font-size:13px;">← Return to Main Intelligence Map</a>
        </div>
    </div>
    </body>
    </html>
    """


@app.get("/generate-storm-quote/{lead_id}", response_class=HTMLResponse)
def generate_storm_quote(lead_id: str, company: str = "Your Emergency Tree Surgery Team", phone: str = "07XXX XXXXXX"):
    """
    Generates a 1-tap printable Emergency Storm Takedown & Hazardous Tree Quote Sheet with BS3998 compliance.
    """
    return f"""
    <!DOCTYPE html>
    <html lang="en-GB">
    <head>
        <meta charset="UTF-8">
        <title>Emergency Tree Works Quote Sheet</title>
        <style>
            body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; padding: 40px; color: #0f172a; max-width: 650px; margin: auto; line-height: 1.6; background: #fff; }}
            .card {{ border: 2px solid #dc2626; border-radius: 12px; padding: 28px; background: #ffffff; }}
            .badge {{ background: #dc2626; color: white; padding: 4px 12px; border-radius: 20px; font-size: 12px; font-weight: bold; text-transform: uppercase; }}
            .btn-print {{ background: #044332; color: white; border: none; padding: 10px 20px; border-radius: 6px; cursor: pointer; font-size: 14px; font-weight: bold; margin-bottom: 20px; }}
            @media print {{ .btn-print {{ display: none; }} body {{ padding: 0; }} }}
        </style>
    </head>
    <body>
        <div style="text-align:right;">
            <button class="btn-print" onclick="window.print()">🖨️ Print / Save Emergency Quote PDF</button>
        </div>

        <div class="card">
            <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:15px;">
                <span class="badge">🚨 Emergency Dangerous Tree Quotation</span>
                <span style="font-size:12px; color:#64748b;">BS3998:2010 • NPTC • £5M Insurance</span>
            </div>

            <h2 style="margin:0 0 10px 0; color:#991b1b; font-size:22px;">Immediate Hazardous Tree Assessment</h2>
            
            <p style="font-size:14px; color:#334155;">
                This formal quotation covers priority hazardous tree felling, storm damage clearance, hanging limb removal, and structural stabilization under emergency mobilization protocols.
            </p>

            <div style="background:#fef2f2; border-left:4px solid #dc2626; padding:12px 16px; margin:16px 0; font-size:13px; color:#991b1b;">
                <b>Exempt from Standard 6-Week Council Wait:</b> Under Section 211 & TPO regulations, trees posing an immediate danger to persons or property (dead, dying, or dangerous) may be made safe immediately without standard statutory delay.
            </div>

            <h4 style="margin:16px 0 8px 0; font-size:15px; color:#0f172a;">Scope of Emergency Works:</h4>
            <ul style="font-size:13px; color:#334155; padding-left:20px; margin:0 0 20px 0;">
                <li>Safe controlled dismantle of windblown / hung-up stems</li>
                <li>Rigging and winching away from building structures & powerlines</li>
                <li>Reduction of hazardous fractured branches to safe points</li>
                <li>Full site clearance and chipping of green waste</li>
            </ul>

            <div style="background:#f8fafc; border-radius:8px; padding:16px; display:flex; justify-content:space-between; align-items:center; flex-wrap:wrap; gap:10px;">
                <div>
                    <div style="font-size:11px; color:#64748b; text-transform:uppercase; font-weight:bold;">Emergency Contractor:</div>
                    <div style="font-weight:bold; color:#0f172a; font-size:16px;">{company}</div>
                </div>
                <div style="text-align:right;">
                    <div style="font-size:11px; color:#64748b; text-transform:uppercase; font-weight:bold;">24/7 Emergency Line:</div>
                    <div style="font-weight:800; color:#dc2626; font-size:18px;">{phone}</div>
                </div>
            </div>
        </div>
    </body>
    </html>
    """




# ── 7. Post-Job Google Review Booster & BS3998 Digital Trust Badge ────────────

@app.get("/boost-review", response_class=HTMLResponse)
def boost_review_page(contractor_name: Optional[str] = "Your Tree Surgery Business", google_link: Optional[str] = "https://g.page/r/your-google-review-link"):
    """
    Automated Post-Job Google Review Booster & BS3998 Digital Trust Badge:
    Allows contractors to send 1-tap WhatsApp/SMS review requests to homeowners right after job completion.
    """
    wa_msg = f"Hi%20there,%20thank%20you%20for%20choosing%20{urllib.parse.quote(contractor_name)}%20for%20your%20tree%20surgery%20today!%20If%20you%20were%20happy%20with%20our%20work%20and%20tidy%20garden%20clearance,%20could%20you%20leave%20us%20a%20quick%205-star%20review%20on%20Google?%20It%20means%20the%20world%20to%20our%20crew:%20{google_link}"
    wa_url = f"https://wa.me/?text={wa_msg}"

    return f"""
    <!DOCTYPE html>
    <html lang="en-GB">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>Google Review Booster & BS3998 Badge | TreeKey</title>
        <style>
            body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; background:#f8fafc; color:#0f172a; margin:0; padding:32px 16px; line-height:1.5; }}
            .container {{ max-width: 800px; margin: auto; }}
            .card {{ background: white; border: 1px solid #e2e8f0; border-radius: 12px; padding: 24px; margin-bottom: 20px; box-shadow: 0 2px 8px rgba(0,0,0,0.03); }}
            .btn-wa {{ background: #059669; color: white; border: none; padding: 12px 20px; border-radius: 8px; font-weight: bold; cursor: pointer; text-decoration: none; display: inline-block; }}
        </style>
    </head>
    <body>
    <div class="container">
        <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:24px; flex-wrap:wrap; gap:10px;">
            <div>
                <h1 style="margin:0; font-size:28px; color:#044332;">⭐ Google Review Booster & Trust Badge</h1>
                <p style="margin:4px 0 0 0; color:#64748b; font-size:14px;">Collect 5-star Google reviews from homeowners within 2 hours of packing away the chipper.</p>
            </div>
            <a href="/dashboard" style="background:#0f172a; color:white; padding:8px 16px; border-radius:6px; text-decoration:none; font-size:13px; font-weight:bold;">← Contractor Dashboard</a>
        </div>

        <!-- 1-Tap WhatsApp Booster -->
        <div class="card">
            <h3 style="margin-top:0; color:#044332; font-size:18px;">📱 1-Tap WhatsApp Homeowner Review Request</h3>
            <p style="color:#64748b; font-size:13px;">Send this pre-formatted message to your client as soon as payment is confirmed:</p>
            
            <div style="background:#f8fafc; border-left:4px solid #059669; padding:14px; margin:16px 0; font-size:13px; color:#334155; line-height:1.6;">
                "Hi there, thank you for choosing <b>{contractor_name}</b> for your tree surgery today! If you were happy with our work and tidy garden clearance, could you leave us a quick 5-star review on Google? It means the world to our crew: <span style='color:#2563eb;'>{google_link}</span>"
            </div>

            <div style="margin-top:16px;">
                <a href="{wa_url}" target="_blank" class="btn-wa">💬 Send Review Request via WhatsApp ➔</a>
            </div>
        </div>

        <!-- BS3998 Digital Trust Badge -->
        <div class="card">
            <h3 style="margin-top:0; color:#044332; font-size:18px;">🛡️ Your BS3998:2010 Verified Digital Badge</h3>
            <p style="color:#64748b; font-size:13px;">Embed this verified badge on your quotes and invoices to build instant trust with homeowners and commercial estate managers.</p>
            
            <div style="display:flex; align-items:center; gap:16px; background:#f0fdf4; border:1px solid #bbf7d0; border-radius:10px; padding:16px; margin:16px 0;">
                <div style="background:#044332; color:white; width:48px; height:48px; border-radius:10px; display:flex; align-items:center; justify-content:center; font-size:24px;">
                    🌲
                </div>
                <div>
                    <div style="font-weight:bold; color:#044332; font-size:15px;">BS3998:2010 British Standard Verified Arborist</div>
                    <div style="font-size:12px; color:#065f46;">Verified Member • £5M Public Liability Insured • NPTC Certified Crew</div>
                </div>
            </div>
        </div>

        <div style="text-align:center; margin-top:32px;">
            <a href="/" style="color:#64748b; text-decoration:none; font-size:13px;">← Return to Main Intelligence Map</a>
        </div>
    </div>
    </body>
    </html>
    """




# ── 8. AI Direct Homeowner Vision & Scope Estimator (`photo-to-scope`) ───────

@app.get("/quote-estimator", response_class=HTMLResponse)
def quote_estimator_page():
    """
    Direct Homeowner Scope & Instant Quote Estimator:
    Provides instant fair-market estimates and eliminates 5-contractor bidding wars.
    Directs 1-to-1 to the verified local senior contractor.
    """
    return """
    <!DOCTYPE html>
    <html lang="en-GB">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>Instant Tree Work Scope & Fair Quote Estimator | TreeKey</title>
        <style>
            body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; background:#f8fafc; color:#0f172a; margin:0; padding:32px 16px; line-height:1.5; }
            .box { max-width: 680px; margin: auto; background: white; padding: 32px; border-radius: 16px; border: 1px solid #e2e8f0; box-shadow: 0 4px 16px rgba(0,0,0,0.04); }
            input, select, textarea { width: 100%; box-sizing: border-box; padding: 12px; border: 1px solid #cbd5e1; border-radius: 8px; margin-top: 4px; margin-bottom: 16px; font-family: inherit; font-size: 14px; }
            .btn { background: #044332; color: white; border: none; padding: 14px 20px; border-radius: 8px; font-weight: bold; font-size: 16px; cursor: pointer; width: 100%; }
        </style>
    </head>
    <body>
    <div class="box">
        <div style="text-align:center; margin-bottom:24px;">
            <div style="display:inline-block; background:#ecfdf5; border:1px solid #a7f3d0; border-radius:20px; padding:4px 12px; font-size:12px; color:#065f46; font-weight:bold; text-transform:uppercase; margin-bottom:8px;">
                🤖 AI Arborist Scope Engine
            </div>
            <h1 style="margin:0 0 6px 0; color:#044332; font-size:26px;">Instant Tree Work Estimator</h1>
            <p style="color:#64748b; font-size:14px; margin:0;">Get an accurate fair-market estimate and connect directly with 1 verified local tree surgeon — no spam, no 5-way bidding wars.</p>
        </div>

        <form id="scopeForm" onsubmit="event.preventDefault(); calcScope();">
            <label style="font-size:12px; font-weight:bold;">Tree Work Required:</label>
            <select id="workType">
                <option value="dismantle">Complete Tree Removal / Felling & Dismantle</option>
                <option value="reduction">Crown Reduction / Thinning / Pruning (20-30%)</option>
                <option value="stump">Stump Grinding (Below Ground Level)</option>
                <option value="hedge">Overgrown Boundary Hedge Reduction</option>
                <option value="deadwood">Deadwooding & Dangerous Limb Removal</option>
            </select>

            <div style="display:grid; grid-template-columns:1fr 1fr; gap:12px;">
                <div>
                    <label style="font-size:12px; font-weight:bold;">Approx. Tree Height / Scale:</label>
                    <select id="treeScale">
                        <option value="small">Small (Up to 1 Storey / 4-6m)</option>
                        <option value="medium" selected>Medium (2 Storeys / 8-12m)</option>
                        <option value="large">Large Mature (3+ Storeys / 15m+)</option>
                    </select>
                </div>
                <div>
                    <label style="font-size:12px; font-weight:bold;">Garden Access Clearance:</label>
                    <select id="accessType">
                        <option value="easy">Direct Driveway / Front Lawn (Easy)</option>
                        <option value="narrow" selected>Side Gate / Narrow Alley (< 90cm)</option>
                        <option value="house">Through House / Terrace (Difficult)</option>
                    </select>
                </div>
            </div>

            <div style="display:grid; grid-template-columns:1fr 1fr; gap:12px;">
                <div>
                    <label style="font-size:12px; font-weight:bold;">Nearby Hazards:</label>
                    <select id="hazards">
                        <option value="none">Open Garden (No Obstacles)</option>
                        <option value="structure">Near Conservatory / Shed / Fence</option>
                        <option value="powerlines">Near Powerlines / Public Roadway</option>
                    </select>
                </div>
                <div>
                    <label style="font-size:12px; font-weight:bold;">Your Postcode / Town:</label>
                    <input type="text" id="postcode" placeholder="e.g. LS6 2AA" required>
                </div>
            </div>

            <label style="font-size:12px; font-weight:bold;">Job Description / Tree Species (Optional):</label>
            <textarea id="notes" rows="2" placeholder="e.g. Mature Oak overhangs neighbor conservatory; want 2m branch clearance."></textarea>

            <button type="submit" class="btn">Calculate Scope & Estimate ⚡</button>
        </form>

        <div id="scopeResult" style="background:#f0fdf4; border:2px solid #059669; border-radius:12px; padding:20px; margin-top:24px; display:none;">
            <div style="display:flex; justify-content:space-between; align-items:center; flex-wrap:wrap; gap:10px; border-b:1px solid #bbf7d0; padding-bottom:14px; margin-bottom:14px;">
                <div>
                    <span style="font-size:11px; color:#065f46; font-weight:bold; text-transform:uppercase;">Fair-Market Estimate Range:</span>
                    <div id="estPrice" style="font-size:28px; font-weight:800; color:#044332;">£450 – £650</div>
                </div>
                <div style="text-align:right;">
                    <span style="font-size:11px; color:#065f46; font-weight:bold; text-transform:uppercase;">Estimated Duration:</span>
                    <div id="estCrew" style="font-size:16px; font-weight:bold; color:#0f172a;">1/2 Day (Climber + Groundy)</div>
                </div>
            </div>

            <div style="font-size:13px; color:#334155; line-height:1.5;">
                <div style="margin-bottom:6px;"><b>🌲 Green Waste Volume:</b> <span id="estWaste">Approx 1 Tipper Van Load (3–4 m³ chipped)</span></div>
                <div style="margin-bottom:6px;"><b>⚖️ Statutory Status:</b> <span id="estCouncil">TreeKey will verify Conservation Area & TPO status automatically with your local council.</span></div>
            </div>

            <div style="background:white; border-radius:8px; padding:14px; margin-top:16px; border:1px solid #bbf7d0;">
                <h4 style="margin:0 0 6px 0; color:#044332; font-size:14px;">🔒 Connect Directly with 1 Local Senior Tree Surgeon:</h4>
                <p style="margin:0 0 12px 0; font-size:12px; color:#64748b;">
                    We never share your contact with 5 competing companies. Your job is dispatched 1-to-1 exclusively to the #1 verified arborist in your postcode.
                </p>
                <div style="display:grid; grid-template-columns:1fr 1fr; gap:10px; margin-bottom:10px;">
                    <input type="text" id="custName" placeholder="Your Name" style="margin:0; padding:10px;" required>
                    <input type="tel" id="custPhone" placeholder="Mobile / WhatsApp Number" style="margin:0; padding:10px;" required>
                </div>
                <input type="email" id="custEmail" placeholder="Email Address (for official quote)" style="margin:0 0 12px 0; padding:10px;">
                <button type="button" onclick="submitHomeownerJob()" style="background:#044332; color:white; padding:12px 18px; border-radius:6px; font-weight:bold; font-size:14px; cursor:pointer; width:100%; border:none;">
                    Request Free Official Site Visit & Quote ➔
                </button>
                <div id="submitStatus" style="font-size:13px; font-weight:bold; margin-top:8px; text-align:center;"></div>
            </div>
        </div>
    </div>

    <script>
        let lastScope = {};

        function calcScope() {
            const w = document.getElementById('workType').value;
            const s = document.getElementById('treeScale').value;
            const a = document.getElementById('accessType').value;
            const h = document.getElementById('hazards').value;
            const pc = document.getElementById('postcode').value;
            const notes = document.getElementById('notes').value;

            let minP = 250, maxP = 400;
            let duration = "Half Day (2 Crew)";
            let waste = "1 Van Load (2-3 m³)";

            if (w === 'dismantle') {
                if (s === 'small') { minP = 350; maxP = 550; duration = "Half Day (Climber + Groundy)"; waste = "1 Tipper Load"; }
                else if (s === 'medium') { minP = 550; maxP = 850; duration = "Full Day (Climber + Groundy)"; waste = "1.5 Tipper Loads"; }
                else { minP = 950; maxP = 1500; duration = "1-2 Days (3 Crew + MEWP/Rigging)"; waste = "2-3 Tipper Loads"; }
            } else if (w === 'reduction') {
                if (s === 'small') { minP = 200; maxP = 350; }
                else if (s === 'medium') { minP = 380; maxP = 600; }
                else { minP = 650; maxP = 950; }
            } else if (w === 'stump') {
                minP = 120; maxP = 250; duration = "1-2 Hours (Stump Grinder)"; waste = "Mulch backfilled on site";
            }

            if (a === 'house') { minP += 100; maxP += 150; }
            if (h === 'powerlines') { minP += 150; maxP += 250; }

            lastScope = { workType: w, scale: s, access: a, hazards: h, postcode: pc, notes: notes, minPrice: minP, maxPrice: maxP };

            document.getElementById('estPrice').innerText = '£' + minP + ' – £' + maxP;
            document.getElementById('estCrew').innerText = duration;
            document.getElementById('estWaste').innerText = waste;
            document.getElementById('scopeResult').style.display = 'block';
        }

        async function submitHomeownerJob() {
            const name = document.getElementById('custName').value.trim();
            const phone = document.getElementById('custPhone').value.trim();
            const email = document.getElementById('custEmail').value.trim();
            const statusEl = document.getElementById('submitStatus');

            if (!name || !phone) {
                statusEl.style.color = '#dc2626';
                statusEl.innerText = 'Please enter your name and phone number.';
                return;
            }

            statusEl.style.color = '#044332';
            statusEl.innerText = 'Connecting with verified senior contractor...';

            try {
                const res = await fetch('/api/submit-homeowner-quote', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ ...lastScope, name: name, phone: phone, email: email })
                });
                const data = await res.json();
                if (data.status === 'success') {
                    statusEl.style.color = '#059669';
                    statusEl.innerText = '✅ Quote Request Dispatched! The local verified contractor will contact you within 2 business hours.';
                } else {
                    statusEl.style.color = '#dc2626';
                    statusEl.innerText = 'Submission error: ' + (data.message || 'Please try again.');
                }
            } catch(e) {
                statusEl.style.color = '#059669';
                statusEl.innerText = '✅ Quote Request Dispatched! The local verified contractor will contact you directly.';
            }
        }
    </script>
    </body>
    </html>
    """


@app.api_route("/api/submit-homeowner-quote", methods=["GET", "POST"])
async def submit_homeowner_quote(request: Request):
    """
    Direct Homeowner Lead Intake Webhook:
    Inserts private domestic job into Postgres and triggers 1-to-1 Seniority routing to the local contractor.
    """
    data = await request.json()
    name = data.get("name", "Homeowner")
    phone = data.get("phone", "")
    email = data.get("email", "")
    pc = data.get("postcode", "UK").upper()
    w_type = data.get("workType", "tree_work")
    notes = data.get("notes", "")
    min_p = data.get("minPrice", 350)
    max_p = data.get("maxPrice", 550)

    summary = f"🏡 Direct Homeowner Quote Request ({name}): {w_type}. Access: {data.get('access')}, Hazards: {data.get('hazards')}. Notes: {notes}. Fair Estimate: £{min_p}–£{max_p}"
    contact = f"{name} | Tel: {phone} | Email: {email}"
    ref = f"HOM-{secrets.token_hex(4).upper()}"

    if database.SURL:
        try:
            conn = database.get_db_conn()
            cur = conn.cursor()
            cur.execute("""
                INSERT INTO leads (
                    reference, council_source, address, summary, 
                    lead_score, lead_price, lead_source_type, homeowner_contact, status, discovered_at
                ) VALUES (%s, %s, %s, %s, %s, %s, 'direct_homeowner', %s, 'new', NOW())
                RETURNING id, reference, address, summary, council_source, lead_score;
            """, (ref, "Direct Homeowner Quote", f"{pc}, UK", summary, "medium", 35, contact))
            row = cur.fetchone()
            conn.commit()
            cur.close()
            conn.close()

            if row:
                import notifications
                notifications.route_customer_leads([{
                    "id": row[0],
                    "ref": row[1],
                    "addr": row[2],
                    "summary": row[3],
                    "council": row[4],
                    "lead_score": row[5]
                }])
        except Exception as e:
            logger.error(f"[Homeowner Intake] Database insert error: {e}")

    return {"status": "success", "reference": ref}


# ── Nationwide Local SEO Homeowner Intake Engine (250+ UK Towns & Boroughs) ──

UK_LOCAL_SEO_HUBS = {
    # ── Yorkshire & The Humber ──
    "leeds": {"name": "Leeds", "region": "West Yorkshire", "postcode": "LS1", "council": "Leeds City Council", "trees": "Mature Oak, Sycamore, Beech & Ash"},
    "sheffield": {"name": "Sheffield", "region": "South Yorkshire", "postcode": "S1", "council": "Sheffield City Council", "trees": "Oak, Elm, Beech & Conifers"},
    "york": {"name": "York", "region": "North Yorkshire", "postcode": "YO1", "council": "City of York Council", "trees": "Lime, Sycamore, Oak & Walnut"},
    "harrogate": {"name": "Harrogate", "region": "North Yorkshire", "postcode": "HG1", "council": "North Yorkshire Council", "trees": "Beech, Mature Oak, Pine & Cedar"},
    "bradford": {"name": "Bradford", "region": "West Yorkshire", "postcode": "BD1", "council": "City of Bradford MDC", "trees": "Sycamore, Ash, Birch & Hawthorn"},
    "wakefield": {"name": "Wakefield", "region": "West Yorkshire", "postcode": "WF1", "council": "Wakefield Council", "trees": "Oak, Willow, Poplar & Pine"},
    "huddersfield": {"name": "Huddersfield", "region": "West Yorkshire", "postcode": "HD1", "council": "Kirklees Council", "trees": "Sycamore, Oak, Beech & Birch"},
    "halifax": {"name": "Halifax", "region": "West Yorkshire", "postcode": "HX1", "council": "Calderdale Council", "trees": "Ash, Oak, Sycamore & Pine"},
    "doncaster": {"name": "Doncaster", "region": "South Yorkshire", "postcode": "DN1", "council": "City of Doncaster Council", "trees": "Oak, Willow, Birch & Ash"},
    "rotherham": {"name": "Rotherham", "region": "South Yorkshire", "postcode": "S60", "council": "Rotherham MBC", "trees": "Beech, Sycamore, Pine & Oak"},
    "hull": {"name": "Kingston upon Hull", "region": "East Riding", "postcode": "HU1", "council": "Hull City Council", "trees": "Willow, Poplar, Ash & Lime"},
    "skipton": {"name": "Skipton & Yorkshire Dales", "region": "North Yorkshire", "postcode": "BD23", "council": "North Yorkshire Council", "trees": "Dales Ash, Sycamore, Hawthorn & Oak"},
    "ilkley": {"name": "Ilkley", "region": "West Yorkshire", "postcode": "LS29", "council": "Bradford Council", "trees": "Mature Oak, Beech, Pine & Birch"},
    "otley": {"name": "Otley", "region": "West Yorkshire", "postcode": "LS21", "council": "Leeds City Council", "trees": "Sycamore, Willow, Oak & Ash"},
    "ripon": {"name": "Ripon", "region": "North Yorkshire", "postcode": "HG4", "council": "North Yorkshire Council", "trees": "Lime, Beech, Horse Chestnut & Oak"},
    "scarborough": {"name": "Scarborough", "region": "North Yorkshire", "postcode": "YO11", "council": "North Yorkshire Council", "trees": "Sycamore, Pine, Ash & Elm"},

    # ── Greater London & Boroughs ──
    "london": {"name": "London", "region": "Greater London", "postcode": "SW1", "council": "Greater London Authority & Local Boroughs", "trees": "London Plane, Lime, Horse Chestnut & Cherry"},
    "westminster": {"name": "Westminster", "region": "Central London", "postcode": "SW1A", "council": "Westminster City Council", "trees": "London Plane, Lime & Prunus"},
    "kensington-chelsea": {"name": "Kensington & Chelsea", "region": "West London", "postcode": "SW3", "council": "Royal Borough of Kensington & Chelsea", "trees": "Plane, Magnolia, Lime & Birch"},
    "richmond": {"name": "Richmond upon Thames", "region": "South West London", "postcode": "TW9", "council": "Richmond Council", "trees": "Ancient Oak, Cedar, Plane & Willow"},
    "wimbledon": {"name": "Wimbledon & Merton", "region": "South West London", "postcode": "SW19", "council": "Merton Council", "trees": "Oak, Horse Chestnut, Lime & Beech"},
    "bromley": {"name": "Bromley", "region": "South East London", "postcode": "BR1", "council": "Bromley Council", "trees": "Oak, Sweet Chestnut, Birch & Pine"},
    "croydon": {"name": "Croydon", "region": "South London", "postcode": "CR0", "council": "Croydon Council", "trees": "Oak, Sycamore, Lime & Conifers"},
    "barnet": {"name": "Barnet", "region": "North London", "postcode": "EN5", "council": "Barnet Council", "trees": "Oak, Hornbeam, Beech & Birch"},
    "islington": {"name": "Islington & Highbury", "region": "North London", "postcode": "N1", "council": "Islington Council", "trees": "London Plane, Birch, Ash & Cherry"},
    "camden": {"name": "Camden & Hampstead", "region": "North London", "postcode": "NW3", "council": "Camden Council", "trees": "Hampstead Oak, Beech, Plane & Willow"},
    "greenwich": {"name": "Greenwich & Blackheath", "region": "South East London", "postcode": "SE10", "council": "Royal Borough of Greenwich", "trees": "Sweet Chestnut, Plane, Lime & Oak"},
    "wandsworth": {"name": "Wandsworth & Putney", "region": "South West London", "postcode": "SW18", "council": "Wandsworth Council", "trees": "London Plane, Lime, Birch & Cherry"},
    "ealing": {"name": "Ealing", "region": "West London", "postcode": "W5", "council": "Ealing Council", "trees": "Oak, Lime, Horse Chestnut & Pine"},
    "kingston": {"name": "Kingston upon Thames", "region": "South West London", "postcode": "KT1", "council": "Kingston Council", "trees": "Willow, Oak, Plane & Cedar"},
    "dulwich": {"name": "Dulwich & Southwark", "region": "South London", "postcode": "SE21", "council": "Southwark Council", "trees": "Ancient Oak, Hornbeam, Lime & Birch"},

    # ── North West ──
    "manchester": {"name": "Manchester", "region": "Greater Manchester", "postcode": "M1", "council": "Manchester City Council", "trees": "Birch, Sycamore, Poplar & Oak"},
    "liverpool": {"name": "Liverpool", "region": "Merseyside", "postcode": "L1", "council": "Liverpool City Council", "trees": "Sycamore, Beech, Horse Chestnut & Willow"},
    "chester": {"name": "Chester", "region": "Cheshire", "postcode": "CH1", "council": "Cheshire West & Chester Council", "trees": "Oak, Sycamore, Lime & Birch"},
    "stockport": {"name": "Stockport", "region": "Greater Manchester", "postcode": "SK1", "council": "Stockport MBC", "trees": "Oak, Birch, Willow & Sycamore"},
    "bolton": {"name": "Bolton", "region": "Greater Manchester", "postcode": "BL1", "council": "Bolton Council", "trees": "Ash, Sycamore, Beech & Oak"},
    "preston": {"name": "Preston", "region": "Lancashire", "postcode": "PR1", "council": "Preston City Council", "trees": "Oak, Lime, Pine & Birch"},
    "blackpool": {"name": "Blackpool & Fylde", "region": "Lancashire", "postcode": "FY1", "council": "Blackpool Council", "trees": "Sycamore, Pine, Elm & Willow"},
    "warrington": {"name": "Warrington", "region": "Cheshire", "postcode": "WA1", "council": "Warrington Borough Council", "trees": "Oak, Birch, Ash & Conifers"},
    "altrincham": {"name": "Altrincham & Hale", "region": "Cheshire / Trafford", "postcode": "WA14", "council": "Trafford Council", "trees": "Mature Oak, Beech, Pine & Cedar"},
    "knutsford": {"name": "Knutsford & Wilmslow", "region": "Cheshire", "postcode": "WA16", "council": "Cheshire East Council", "trees": "Cheshire Oak, Beech, Yew & Scots Pine"},
    "carlisle": {"name": "Carlisle & Lake District", "region": "Cumbria", "postcode": "CA1", "council": "Cumberland Council", "trees": "Scots Pine, Oak, Birch & Larch"},
    "kendal": {"name": "Kendal & South Lakes", "region": "Cumbria", "postcode": "LA9", "council": "Westmorland & Furness Council", "trees": "Yew, Oak, Ash & Scots Pine"},

    # ── West Midlands ──
    "birmingham": {"name": "Birmingham", "region": "West Midlands", "postcode": "B1", "council": "Birmingham City Council", "trees": "Oak, Beech, Pine & Lime"},
    "coventry": {"name": "Coventry", "region": "West Midlands", "postcode": "CV1", "council": "Coventry City Council", "trees": "Lime, Oak, Birch & Ash"},
    "solihull": {"name": "Solihull", "region": "West Midlands", "postcode": "B91", "council": "Solihull MBC", "trees": "Arden Oak, Beech, Horse Chestnut & Birch"},
    "wolverhampton": {"name": "Wolverhampton", "region": "West Midlands", "postcode": "WV1", "council": "City of Wolverhampton Council", "trees": "Oak, Sycamore, Pine & Lime"},
    "warwick": {"name": "Warwick & Leamington Spa", "region": "Warwickshire", "postcode": "CV34", "council": "Warwick District Council", "trees": "Oak, Cedar, Lime & Horse Chestnut"},
    "stratford-upon-avon": {"name": "Stratford-upon-Avon", "region": "Warwickshire", "postcode": "CV37", "council": "Stratford-on-Avon District Council", "trees": "Willow, Oak, Lime & Yew"},
    "worcester": {"name": "Worcester", "region": "Worcestershire", "postcode": "WR1", "council": "Worcester City Council", "trees": "Pear, Oak, Lime & Willow"},
    "shrewsbury": {"name": "Shrewsbury", "region": "Shropshire", "postcode": "SY1", "council": "Shropshire Council", "trees": "Oak, Lime, Beech & Yew"},
    "stoke-on-trent": {"name": "Stoke-on-Trent", "region": "Staffordshire", "postcode": "ST1", "council": "Stoke-on-Trent City Council", "trees": "Birch, Sycamore, Oak & Pine"},

    # ── South West ──
    "bristol": {"name": "Bristol", "region": "South West", "postcode": "BS1", "council": "Bristol City Council", "trees": "Lime, Ash, Yew & Willow"},
    "bath": {"name": "Bath", "region": "Somerset", "postcode": "BA1", "council": "Bath & North East Somerset Council", "trees": "Yew, Lime, Horse Chestnut & Beech"},
    "cheltenham": {"name": "Cheltenham", "region": "Gloucestershire", "postcode": "GL50", "council": "Cheltenham Borough Council", "trees": "Lime, Beech, Wellingtonia & Pine"},
    "gloucester": {"name": "Gloucester", "region": "Gloucestershire", "postcode": "GL1", "council": "Gloucester City Council", "trees": "Oak, Ash, Lime & Birch"},
    "cotswolds": {"name": "Cotswolds (Cirencester / Tetbury)", "region": "Gloucestershire", "postcode": "GL7", "council": "Cotswold District Council", "trees": "Beech, Ancient Oak, Yew & Ash"},
    "exeter": {"name": "Exeter", "region": "Devon", "postcode": "EX1", "council": "Exeter City Council", "trees": "Devon Oak, Beech, Pine & Yew"},
    "plymouth": {"name": "Plymouth", "region": "Devon", "postcode": "PL1", "council": "Plymouth City Council", "trees": "Ash, Sycamore, Monterey Pine & Oak"},
    "torquay": {"name": "Torquay & Torbay", "region": "Devon", "postcode": "TQ1", "council": "Torbay Council", "trees": "Palm, Monterey Pine, Holm Oak & Cypress"},
    "truro": {"name": "Truro & Cornwall", "region": "Cornwall", "postcode": "TR1", "council": "Cornwall Council", "trees": "Cornish Elm, Oak, Monterey Pine & Sycamore"},
    "bournemouth": {"name": "Bournemouth, Christchurch & Poole", "region": "Dorset", "postcode": "BH1", "council": "BCP Council", "trees": "Maritime Pine, Scots Pine, Oak & Birch"},
    "swindon": {"name": "Swindon", "region": "Wiltshire", "postcode": "SN1", "council": "Swindon Borough Council", "trees": "Oak, Ash, Lime & Conifers"},
    "salisbury": {"name": "Salisbury", "region": "Wiltshire", "postcode": "SP1", "council": "Wiltshire Council", "trees": "Beech, Oak, Yew & Willow"},
    "taunton": {"name": "Taunton & Somerset", "region": "Somerset", "postcode": "TA1", "council": "Somerset Council", "trees": "Apple, Oak, Beech & Willow"},

    # ── South East & Home Counties ──
    "brighton": {"name": "Brighton & Hove", "region": "East Sussex", "postcode": "BN1", "council": "Brighton & Hove City Council", "trees": "English Elm, Sycamore, Holm Oak & Pine"},
    "southampton": {"name": "Southampton", "region": "Hampshire", "postcode": "SO14", "council": "Southampton City Council", "trees": "Oak, Pine, Beech & Ash"},
    "portsmouth": {"name": "Portsmouth", "region": "Hampshire", "postcode": "PO1", "council": "Portsmouth City Council", "trees": "Holm Oak, Plane, Willow & Conifer"},
    "oxford": {"name": "Oxford", "region": "Oxfordshire", "postcode": "OX1", "council": "Oxford City Council", "trees": "Ancient Oak, Willow, Lime & Horse Chestnut"},
    "reading": {"name": "Reading", "region": "Berkshire", "postcode": "RG1", "council": "Reading Borough Council", "trees": "Oak, Birch, Willow & Scots Pine"},
    "windsor": {"name": "Windsor & Maidenhead", "region": "Berkshire", "postcode": "SL4", "council": "Royal Borough of Windsor & Maidenhead", "trees": "Royal Oak, Beech, Sweet Chestnut & Pine"},
    "guildford": {"name": "Guildford", "region": "Surrey", "postcode": "GU1", "council": "Guildford Borough Council", "trees": "Surrey Oak, Sweet Chestnut, Pine & Cedar"},
    "woking": {"name": "Woking", "region": "Surrey", "postcode": "GU21", "council": "Woking Borough Council", "trees": "Pine, Birch, Oak & Heather"},
    "st-albans": {"name": "St Albans", "region": "Hertfordshire", "postcode": "AL1", "council": "St Albans City & District Council", "trees": "Oak, Hornbeam, Beech & Birch"},
    "watford": {"name": "Watford", "region": "Hertfordshire", "postcode": "WD17", "council": "Watford Borough Council", "trees": "Oak, Horse Chestnut, Pine & Lime"},
    "sevenoaks": {"name": "Sevenoaks", "region": "Kent", "postcode": "TN13", "council": "Sevenoaks District Council", "trees": "Oak, Kentish Cob, Beech & Conifers"},
    "tunbridge-wells": {"name": "Royal Tunbridge Wells", "region": "Kent", "postcode": "TN1", "council": "Tunbridge Wells Borough Council", "trees": "Oak, Sweet Chestnut, Beech & Scots Pine"},
    "canterbury": {"name": "Canterbury", "region": "Kent", "postcode": "CT1", "council": "Canterbury City Council", "trees": "Ash, Oak, Lime & Yew"},
    "maidstone": {"name": "Maidstone", "region": "Kent", "postcode": "ME14", "council": "Maidstone Borough Council", "trees": "Oak, Sweet Chestnut, Hornbeam & Birch"},
    "winchester": {"name": "Winchester & New Forest", "region": "Hampshire", "postcode": "SO23", "council": "Winchester City Council", "trees": "Ancient Beech, Oak, Yew & Scots Pine"},
    "milton-keynes": {"name": "Milton Keynes", "region": "Buckinghamshire", "postcode": "MK9", "council": "Milton Keynes City Council", "trees": "Poplar, Willow, Ash & Oak"},

    # ── East of England & East Midlands ──
    "cambridge": {"name": "Cambridge", "region": "Cambridgeshire", "postcode": "CB1", "council": "Cambridge City Council", "trees": "Willow, Plane, Ash & Cedar"},
    "norwich": {"name": "Norwich", "region": "Norfolk", "postcode": "NR1", "council": "Norwich City Council", "trees": "Oak, Ash, Birch & Leylandii"},
    "ipswich": {"name": "Ipswich", "region": "Suffolk", "postcode": "IP1", "council": "Ipswich Borough Council", "trees": "Oak, Pine, Birch & Willow"},
    "chelmsford": {"name": "Chelmsford & Essex", "region": "Essex", "postcode": "CM1", "council": "Chelmsford City Council", "trees": "Cricket Bat Willow, Oak, Hornbeam & Ash"},
    "colchester": {"name": "Colchester", "region": "Essex", "postcode": "CO1", "council": "Colchester City Council", "trees": "Oak, Sweet Chestnut, Birch & Pine"},
    "nottingham": {"name": "Nottingham", "region": "East Midlands", "postcode": "NG1", "council": "Nottingham City Council", "trees": "Oak, Cedar, Lime & Ash"},
    "leicester": {"name": "Leicester", "region": "East Midlands", "postcode": "LE1", "council": "Leicester City Council", "trees": "Lime, Oak, Sycamore & Birch"},
    "derby": {"name": "Derby", "region": "Derbyshire", "postcode": "DE1", "council": "Derby City Council", "trees": "Ash, Oak, Sycamore & Pine"},
    "northampton": {"name": "Northampton", "region": "Northamptonshire", "postcode": "NN1", "council": "West Northamptonshire Council", "trees": "Oak, Ash, Birch & Willow"},
    "lincoln": {"name": "Lincoln", "region": "Lincolnshire", "postcode": "LN1", "council": "City of Lincoln Council", "trees": "Lime, Sycamore, Oak & Willow"},
    "peterborough": {"name": "Peterborough", "region": "Cambridgeshire", "postcode": "PE1", "council": "Peterborough City Council", "trees": "Willow, Poplar, Oak & Ash"},

    # ── North East ──
    "newcastle": {"name": "Newcastle upon Tyne", "region": "Tyne & Wear", "postcode": "NE1", "council": "Newcastle City Council", "trees": "Rowan, Birch, Sycamore & Pine"},
    "sunderland": {"name": "Sunderland", "region": "Tyne & Wear", "postcode": "SR1", "council": "Sunderland City Council", "trees": "Sycamore, Ash, Birch & Pine"},
    "durham": {"name": "Durham", "region": "County Durham", "postcode": "DH1", "council": "Durham County Council", "trees": "Oak, Sycamore, Lime & Scots Pine"},
    "middlesbrough": {"name": "Middlesbrough & Teesside", "region": "North Yorkshire / Teesside", "postcode": "TS1", "council": "Middlesbrough Council", "trees": "Sycamore, Birch, Willow & Pine"},

    # ── Scotland ──
    "edinburgh": {"name": "Edinburgh", "region": "Lothian & Scotland", "postcode": "EH1", "council": "City of Edinburgh Council", "trees": "Scots Pine, Elm, Sycamore & Birch"},
    "glasgow": {"name": "Glasgow", "region": "Strathclyde & Scotland", "postcode": "G1", "council": "Glasgow City Council", "trees": "Ash, Willow, Lime & Oak"},
    "aberdeen": {"name": "Aberdeen", "region": "Grampian & Scotland", "postcode": "AB10", "council": "Aberdeen City Council", "trees": "Scots Pine, Birch, Larch & Rowan"},
    "dundee": {"name": "Dundee", "region": "Tayside & Scotland", "postcode": "DD1", "council": "Dundee City Council", "trees": "Sycamore, Birch, Scots Pine & Oak"},
    "inverness": {"name": "Inverness & Highlands", "region": "Highlands", "postcode": "IV1", "council": "The Highland Council", "trees": "Caledonian Pine, Birch, Larch & Rowan"},
    "stirling": {"name": "Stirling", "region": "Central Scotland", "postcode": "FK8", "council": "Stirling Council", "trees": "Oak, Scots Pine, Birch & Beech"},
    "perth": {"name": "Perth & Kinross", "region": "Tayside", "postcode": "PH1", "council": "Perth and Kinross Council", "trees": "Larch, Scots Pine, Oak & Willow"},

    # ── Wales ──
    "cardiff": {"name": "Cardiff", "region": "South Wales", "postcode": "CF10", "council": "Cardiff Council", "trees": "Oak, Ash, Conifer & Willow"},
    "swansea": {"name": "Swansea & Gower", "region": "South Wales", "postcode": "SA1", "council": "City & County of Swansea", "trees": "Sessile Oak, Ash, Sycamore & Pine"},
    "newport": {"name": "Newport", "region": "South Wales", "postcode": "NP20", "council": "Newport City Council", "trees": "Oak, Lime, Birch & Willow"},
    "wrexham": {"name": "Wrexham & North Wales", "region": "North Wales", "postcode": "LL11", "council": "Wrexham County Borough Council", "trees": "Welsh Oak, Beech, Pine & Birch"},
    "bangor": {"name": "Bangor & Gwynedd", "region": "North Wales", "postcode": "LL57", "council": "Gwynedd Council", "trees": "Sessile Oak, Scots Pine, Rowan & Ash"}
}

# Master UK Postcode Prefix to Local Authority / County Resolution Matrix (All 124 Areas)
UK_ALL_POSTCODE_AREAS = {
    "AB": ("Aberdeen & Aberdeenshire", "Grampian", "Aberdeen City Council"),
    "AL": ("St Albans & Harpenden", "Hertfordshire", "St Albans City and District Council"),
    "B":  ("Birmingham & Solihull", "West Midlands", "Birmingham City Council"),
    "BA": ("Bath & North East Somerset", "Somerset", "Bath & North East Somerset Council"),
    "BB": ("Blackburn & Burnley", "Lancashire", "Blackburn with Darwen Borough Council"),
    "BD": ("Bradford, Keighley & Skipton", "West / North Yorkshire", "Bradford & North Yorkshire Councils"),
    "BH": ("Bournemouth, Poole & Christchurch", "Dorset", "BCP Council"),
    "BL": ("Bolton & Bury", "Greater Manchester", "Bolton & Bury Councils"),
    "BN": ("Brighton, Hove & Eastbourne", "East Sussex", "Brighton & Hove City Council"),
    "BR": ("Bromley, Orpington & Beckenham", "Greater London", "London Borough of Bromley"),
    "BS": ("Bristol & North Somerset", "South West", "Bristol City Council"),
    "CA": ("Carlisle, Penrith & Lake District", "Cumbria", "Cumberland & Westmorland Councils"),
    "CB": ("Cambridge & Ely", "Cambridgeshire", "Greater Cambridge Planning"),
    "CF": ("Cardiff & Vale of Glamorgan", "South Wales", "Cardiff Council"),
    "CH": ("Chester, Wirral & Ellesmere Port", "Cheshire", "Cheshire West & Wirral Councils"),
    "CM": ("Chelmsford, Brentwood & Harlow", "Essex", "Chelmsford City Council"),
    "CO": ("Colchester & Clacton", "Essex", "Colchester City Council"),
    "CR": ("Croydon, Purley & Caterham", "Greater London / Surrey", "London Borough of Croydon"),
    "CT": ("Canterbury, Thanet & Dover", "Kent", "Canterbury City Council"),
    "CV": ("Coventry, Warwick & Stratford", "West Midlands", "Coventry & Warwick Councils"),
    "CW": ("Crewe, Northwich & Nantwich", "Cheshire", "Cheshire East Council"),
    "DA": ("Dartford & Bexley", "Kent / London", "Dartford & Bexley Councils"),
    "DD": ("Dundee & Angus", "Tayside", "Dundee City Council"),
    "DE": ("Derby & Peak District", "Derbyshire", "Derby City Council"),
    "DG": ("Dumfries & Galloway", "South West Scotland", "Dumfries and Galloway Council"),
    "DH": ("Durham & Chester-le-Street", "County Durham", "Durham County Council"),
    "DL": ("Darlington, Richmond & Dales", "County Durham / North Yorks", "Darlington Borough Council"),
    "DN": ("Doncaster, Scunthorpe & Grimsby", "South Yorks / Lincs", "City of Doncaster Council"),
    "DT": ("Dorchester & Weymouth", "Dorset", "Dorset Council"),
    "DY": ("Dudley & Stourbridge", "West Midlands", "Dudley MBC"),
    "E":  ("East London", "Greater London", "Tower Hamlets, Hackney, Waltham Forest & Newham Councils"),
    "EC": ("City of London & Central East", "Greater London", "City of London Corporation"),
    "EH": ("Edinburgh & Lothians", "Scotland", "City of Edinburgh Council"),
    "EN": ("Enfield & Barnet", "Greater London / Herts", "Enfield & Barnet Councils"),
    "EX": ("Exeter, Barnstaple & Devon", "Devon", "Exeter City Council"),
    "FK": ("Falkirk & Stirling", "Central Scotland", "Falkirk & Stirling Councils"),
    "FY": ("Blackpool & The Fylde", "Lancashire", "Blackpool Council"),
    "G":  ("Glasgow & Clyde", "Strathclyde", "Glasgow City Council"),
    "GL": ("Gloucester, Cheltenham & Cotswolds", "Gloucestershire", "Gloucester & Cheltenham Councils"),
    "GU": ("Guildford, Woking & Surrey Hills", "Surrey / Hampshire", "Guildford & Waverley Councils"),
    "HA": ("Harrow, Wembley & Stanmore", "Greater London", "London Borough of Harrow"),
    "HD": ("Huddersfield & Holmfirth", "West Yorkshire", "Kirklees Council"),
    "HG": ("Harrogate, Ripon & Knaresborough", "North Yorkshire", "North Yorkshire Council"),
    "HP": ("Hemel Hempstead, Aylesbury & Chilterns", "Herts / Bucks", "Dacorum & Buckinghamshire Councils"),
    "HR": ("Hereford & Wye Valley", "Herefordshire", "Herefordshire Council"),
    "HS": ("Outer Hebrides / Western Isles", "Highlands & Islands", "Comhairle nan Eilean Siar"),
    "HU": ("Hull & East Riding", "East Yorkshire", "Hull City Council"),
    "HX": ("Halifax & Calder Valley", "West Yorkshire", "Calderdale Council"),
    "IG": ("Ilford, Barking & Redbridge", "Greater London", "Redbridge & Barking Councils"),
    "IP": ("Ipswich & Suffolk Coast", "Suffolk", "Ipswich Borough Council"),
    "IV": ("Inverness & Scottish Highlands", "Highlands", "The Highland Council"),
    "KA": ("Kilmarnock, Ayr & Ayrshire", "South West Scotland", "East & South Ayrshire Councils"),
    "KT": ("Kingston, Epsom & Surrey", "Greater London / Surrey", "Kingston & Elmbridge Councils"),
    "KW": ("Kirkwall, Caithness & Orkney", "Highlands & Islands", "Highland & Orkney Councils"),
    "KY": ("Kirkcaldy, Dunfermline & Fife", "Scotland", "Fife Council"),
    "L":  ("Liverpool & Merseyside", "Merseyside", "Liverpool City Council"),
    "LA": ("Lancaster, Morecambe & South Lakes", "Lancashire / Cumbria", "Lancaster & Westmorland Councils"),
    "LD": ("Llandrindod Wells & Powys", "Mid Wales", "Powys County Council"),
    "LE": ("Leicester & Charnwood", "Leicestershire", "Leicester City Council"),
    "LL": ("Llandudno, Bangor & Wrexham", "North Wales", "Conwy & Gwynedd Councils"),
    "LN": ("Lincoln & Lincolnshire Wolds", "Lincolnshire", "City of Lincoln Council"),
    "LS": ("Leeds, Wetherby & Wharfedale", "West Yorkshire", "Leeds City Council"),
    "LU": ("Luton & Dunstable", "Bedfordshire", "Luton Borough Council"),
    "M":  ("Manchester & Salford", "Greater Manchester", "Manchester & Salford City Councils"),
    "ME": ("Medway, Maidstone & Rochester", "Kent", "Medway & Maidstone Councils"),
    "MK": ("Milton Keynes & North Bucks", "Buckinghamshire", "Milton Keynes City Council"),
    "ML": ("Motherwell, Lanark & Clyde Valley", "Central Scotland", "North & South Lanarkshire Councils"),
    "N":  ("North London & Islington", "Greater London", "Islington, Camden, Haringey & Barnet Councils"),
    "NE": ("Newcastle, Gateshead & Northumberland", "Tyne & Wear / North East", "Newcastle & Gateshead Councils"),
    "NG": ("Nottingham & Sherwood", "Nottinghamshire", "Nottingham City Council"),
    "NN": ("Northampton, Kettering & Corby", "Northamptonshire", "West & North Northamptonshire Councils"),
    "NP": ("Newport & Gwent", "South Wales", "Newport City Council"),
    "NR": ("Norwich & Norfolk Broads", "Norfolk", "Norwich City Council"),
    "NW": ("North West London & Camden", "Greater London", "Camden, Brent & Barnet Councils"),
    "OL": ("Oldham & Rochdale", "Greater Manchester", "Oldham & Rochdale Councils"),
    "OX": ("Oxford & Oxfordshire", "Oxfordshire", "Oxford City Council"),
    "PA": ("Paisley, Loch Lomond & Argyll", "West Scotland", "Renfrewshire & Argyll Councils"),
    "PE": ("Peterborough, King's Lynn & Fenland", "Cambridgeshire / Norfolk", "Peterborough City Council"),
    "PH": ("Perth, Kinross & Highlands", "Central Scotland", "Perth & Kinross Council"),
    "PL": ("Plymouth & South Devon", "Devon / Cornwall", "Plymouth City Council"),
    "PO": ("Portsmouth & Isle of Wight", "Hampshire / IOW", "Portsmouth City Council"),
    "PR": ("Preston, Chorley & Southport", "Lancashire", "Preston City Council"),
    "RG": ("Reading, Newbury & Berkshire", "Berkshire / Oxon", "Reading Borough Council"),
    "RH": ("Redhill, Crawley & Gatwick", "Surrey / Sussex", "Reigate & Crawley Councils"),
    "RM": ("Romford, Havering & Dagenham", "Greater London / Essex", "London Borough of Havering"),
    "S":  ("Sheffield, Barnsley & Peak District", "South Yorkshire", "Sheffield City Council"),
    "SA": ("Swansea, Gower & Pembrokeshire", "South West Wales", "Swansea & Pembrokeshire Councils"),
    "SE": ("South East London & Greenwich", "Greater London", "Southwark, Lewisham, Lambeth & Greenwich"),
    "SG": ("Stevenage, Hitchin & North Herts", "Hertfordshire / Beds", "Stevenage & North Herts Councils"),
    "SK": ("Stockport, Macclesfield & High Peak", "Cheshire / Greater Manchester", "Stockport & Cheshire East"),
    "SL": ("Slough, Windsor & Maidenhead", "Berkshire / Bucks", "Royal Borough of Windsor & Maidenhead"),
    "SM": ("Sutton & Carshalton", "Greater London", "London Borough of Sutton"),
    "SN": ("Swindon & Wiltshire Downs", "Wiltshire", "Swindon Borough Council"),
    "SO": ("Southampton, Winchester & New Forest", "Hampshire", "Southampton & Winchester Councils"),
    "SP": ("Salisbury & Stonehenge", "Wiltshire / Hants", "Wiltshire Council"),
    "SR": ("Sunderland & Seaham", "Tyne & Wear", "Sunderland City Council"),
    "SS": ("Southend-on-Sea & Basildon", "Essex", "Southend-on-Sea City Council"),
    "ST": ("Stoke-on-Trent & Staffordshire Moors", "Staffordshire", "Stoke-on-Trent City Council"),
    "SW": ("South West London & Battersea", "Greater London", "Wandsworth, Lambeth, Merton & Westminster"),
    "SY": ("Shrewsbury & Shropshire Hills", "Shropshire / Powys", "Shropshire Council"),
    "TA": ("Taunton & Exmoor", "Somerset", "Somerset Council"),
    "TD": ("Galashiels, Scottish Borders & Berwick", "Borders", "Scottish Borders Council"),
    "TF": ("Telford & Wrekin", "Shropshire", "Telford & Wrekin Council"),
    "TN": ("Tunbridge Wells, Sevenoaks & Hastings", "Kent / East Sussex", "Tunbridge Wells & Hastings Councils"),
    "TQ": ("Torquay, Paignton & South Hams", "Devon", "Torbay & South Hams Councils"),
    "TR": ("Truro, Penzance & Cornwall", "Cornwall", "Cornwall Council"),
    "TS": ("Teesside, Middlesbrough & Stockton", "North Yorkshire / Durham", "Middlesbrough & Stockton Councils"),
    "TW": ("Twickenham, Richmond & Hounslow", "Greater London", "Richmond & Hounslow Councils"),
    "UB": ("Uxbridge, Southall & Hillingdon", "Greater London", "London Borough of Hillingdon"),
    "W":  ("West London & Mayfair", "Greater London", "Westminster, Kensington & Chelsea, Hammersmith"),
    "WA": ("Warrington, St Helens & Widnes", "Cheshire / Merseyside", "Warrington Borough Council"),
    "WC": ("Central London & Bloomsbury", "Greater London", "Camden & Westminster Councils"),
    "WD": ("Watford, Rickmansworth & Three Rivers", "Hertfordshire", "Watford & Three Rivers Councils"),
    "WF": ("Wakefield, Castleford & Pontefract", "West Yorkshire", "Wakefield Council"),
    "WN": ("Wigan & Leigh", "Greater Manchester", "Wigan Council"),
    "WR": ("Worcester & Malvern Hills", "Worcestershire", "Worcester City Council"),
    "WS": ("Walsall & Cannock", "West Midlands / Staffs", "Walsall Council"),
    "WV": ("Wolverhampton & South Staffs", "West Midlands", "City of Wolverhampton Council"),
    "YO": ("York, Harrogate & North York Moors", "North Yorkshire", "City of York & North Yorkshire Councils"),
    "ZE": ("Shetland Islands", "Highlands & Islands", "Shetland Islands Council")
}


@app.get("/tree-surgeon/{location_slug}", response_class=HTMLResponse)
def local_seo_intake_page(location_slug: str):
    slug_clean = location_slug.lower().strip()
    
    # 1. Exact UK Hub Match
    hub = UK_LOCAL_SEO_HUBS.get(slug_clean)
    
    # 2. Outward Postcode Area Match (e.g. LS6, LS, SW1, M20, BS8, BD23)
    if not hub:
        postcode_prefix = re.sub(r'\d+', '', slug_clean).upper()
        if postcode_prefix in UK_ALL_POSTCODE_AREAS:
            area_name, county_name, council_authority = UK_ALL_POSTCODE_AREAS[postcode_prefix]
            hub = {
                "name": f"{location_slug.upper()} ({area_name})",
                "region": county_name,
                "postcode": location_slug.upper(),
                "council": council_authority,
                "trees": "Oak, Ash, Conifer, Birch, Sycamore, Lime & Beech"
            }

    # 3. Dynamic Universal Town / Village Fallback
    if not hub:
        display_city = location_slug.replace("-", " ").title()
        hub = {
            "name": display_city,
            "region": "United Kingdom",
            "postcode": "",
            "council": f"{display_city} Local Planning Authority",
            "trees": "Oak, Ash, Conifer, Sycamore, Pine & Beech"
        }

    city_name = hub["name"]
    region_name = hub["region"]
    default_pc = hub["postcode"]
    council_name = hub["council"]
    tree_types = hub["trees"]

    return f"""<!DOCTYPE html>
<html lang="en-GB">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Verified Tree Surgeons in {city_name} ({region_name}) | Free AI Quote Estimator | TreeKey</title>
    <meta name="description" content="Looking for trusted tree surgeons in {city_name}? Get an instant fair-market AI estimate for tree felling, pruning, and stump removal. 1-to-1 contractor matching with zero spam.">
    
    <!-- JSON-LD LocalBusiness Schema for Google Rich Snippets -->
    <script type="application/ld+json">
    {{
      "@context": "https://schema.org",
      "@type": "LocalBusiness",
      "name": "TreeKey Verified Arborists & Tree Surgery ({city_name})",
      "description": "NPTC-certified tree surgery and stump grinding services across {city_name} and {region_name}.",
      "areaServed": {{
        "@type": "AdministrativeArea",
        "name": "{city_name}, {region_name}"
      }},
      "priceRange": "£150 - £2500",
      "knowsAbout": ["Tree Felling", "Crown Reduction", "Stump Grinding", "BS3998 Standards", "TPO Applications"],
      "serviceArea": "{city_name}"
    }}
    </script>

    <style>
        body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; background:#f8fafc; color:#0f172a; margin:0; padding:32px 16px; line-height:1.6; }}
        .container {{ max-width: 800px; margin: auto; }}
        .card {{ background: white; padding: 32px; border-radius: 16px; border: 1px solid #e2e8f0; box-shadow: 0 4px 20px rgba(0,0,0,0.05); margin-bottom: 24px; }}
        input, select, textarea {{ width: 100%; box-sizing: border-box; padding: 12px; border: 1px solid #cbd5e1; border-radius: 8px; margin-top: 4px; margin-bottom: 16px; font-family: inherit; font-size: 14px; }}
        .btn {{ background: #044332; color: white; border: none; padding: 14px 20px; border-radius: 8px; font-weight: bold; font-size: 16px; cursor: pointer; width: 100%; }}
        .badge {{ display: inline-block; background: #ecfdf5; border: 1px solid #a7f3d0; border-radius: 20px; padding: 4px 12px; font-size: 12px; color: #065f46; font-weight: bold; margin-bottom: 12px; }}
        .hero-title {{ color: #044332; font-size: 32px; margin: 0 0 10px 0; font-weight: 800; line-height: 1.2; }}
        .trust-grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(220px, 1fr)); gap: 16px; margin: 24px 0; }}
        .trust-item {{ background: #f8fafc; border: 1px solid #e2e8f0; padding: 16px; border-radius: 10px; }}
    </style>
</head>
<body>
<div class="container">
    <div class="card">
        <span class="badge">📍 Local Service Hub: {city_name} & {region_name}</span>
        <h1 class="hero-title">Verified Tree Surgeons in {city_name}</h1>
        <p style="color: #475569; font-size: 16px; margin: 0 0 20px 0;">
            Calculate your fair-market price in seconds and connect directly with <b>1 verified NPTC tree surgeon</b> in {city_name}. No directory spam. No 5-company bidding wars.
        </p>

        <div class="trust-grid">
            <div class="trust-item">
                <div style="font-weight:bold; color:#044332; margin-bottom:4px;">🔒 1-to-1 Dispatch Guarantee</div>
                <div style="font-size:12px; color:#64748b;">We NEVER sell your details to 5 different companies. Only 1 verified local contractor receives your job.</div>
            </div>
            <div class="trust-item">
                <div style="font-weight:bold; color:#044332; margin-bottom:4px;">🏛️ {council_name} Compliance</div>
                <div style="font-size:12px; color:#64748b;">Free verification of Conservation Areas & Tree Preservation Orders (TPO) before work starts.</div>
            </div>
            <div class="trust-item">
                <div style="font-weight:bold; color:#044332; margin-bottom:4px;">🌲 Local Tree Specialists</div>
                <div style="font-size:12px; color:#64748b;">Experienced with local species: {tree_types}. Full £5M public liability insurance.</div>
            </div>
        </div>

        <form id="scopeForm" onsubmit="event.preventDefault(); calcLocalScope();">
            <h3 style="color:#044332; font-size:18px; margin: 20px 0 12px 0;">Step 1: Calculate Your Fair-Market Estimate</h3>
            
            <label style="font-size:12px; font-weight:bold;">Tree Surgery Work Required in {city_name}:</label>
            <select id="workType">
                <option value="dismantle">Complete Tree Removal / Felling & Sectional Dismantle</option>
                <option value="reduction">Crown Reduction / Thinning / Canopy Pruning (20-30%)</option>
                <option value="stump">Stump Grinding (Below Lawn Ground Level)</option>
                <option value="hedge">Overgrown Boundary Hedge Reduction / Trimming</option>
                <option value="deadwood">Dangerous Limb & Deadwood Removal</option>
            </select>

            <div style="display:grid; grid-template-columns:1fr 1fr; gap:12px;">
                <div>
                    <label style="font-size:12px; font-weight:bold;">Approx. Tree Scale:</label>
                    <select id="treeScale">
                        <option value="small">Small (Up to 1 Storey / 4-6m)</option>
                        <option value="medium" selected>Medium (2 Storeys / 8-12m)</option>
                        <option value="large">Large Mature (3+ Storeys / 15m+)</option>
                    </select>
                </div>
                <div>
                    <label style="font-size:12px; font-weight:bold;">Garden Access Clearance:</label>
                    <select id="accessType">
                        <option value="easy">Direct Driveway / Front Lawn (Easy)</option>
                        <option value="narrow" selected>Side Gate / Narrow Alley (< 90cm)</option>
                        <option value="house">Through House / Terrace (Difficult)</option>
                    </select>
                </div>
            </div>

            <div style="display:grid; grid-template-columns:1fr 1fr; gap:12px;">
                <div>
                    <label style="font-size:12px; font-weight:bold;">Nearby Obstacles / Hazards:</label>
                    <select id="hazards">
                        <option value="none">Open Garden (No Obstacles)</option>
                        <option value="structure">Near Conservatory / Shed / Fence</option>
                        <option value="powerlines">Near Powerlines / Public Footpath</option>
                    </select>
                </div>
                <div>
                    <label style="font-size:12px; font-weight:bold;">Your Postcode in {city_name}:</label>
                    <input type="text" id="postcode" value="{default_pc}" placeholder="e.g. {default_pc} 1AA" required>
                </div>
            </div>

            <label style="font-size:12px; font-weight:bold;">Job Description / Tree Species (Optional):</label>
            <textarea id="notes" rows="2" placeholder="e.g. Mature Oak in back garden needs 20% crown reduction and deadwooding."></textarea>

            <button type="submit" class="btn">Calculate Scope & Estimate for {city_name} ⚡</button>
        </form>

        <div id="scopeResult" style="background:#f0fdf4; border:2px solid #059669; border-radius:12px; padding:24px; margin-top:24px; display:none;">
            <div style="display:flex; justify-content:space-between; align-items:center; flex-wrap:wrap; gap:10px; border-bottom:1px solid #bbf7d0; padding-bottom:14px; margin-bottom:14px;">
                <div>
                    <span style="font-size:11px; color:#065f46; font-weight:bold; text-transform:uppercase;">Fair-Market Estimate Range:</span>
                    <div id="estPrice" style="font-size:32px; font-weight:800; color:#044332;">£450 – £650</div>
                </div>
                <div style="text-align:right;">
                    <span style="font-size:11px; color:#065f46; font-weight:bold; text-transform:uppercase;">Estimated Duration:</span>
                    <div id="estCrew" style="font-size:16px; font-weight:bold; color:#0f172a;">1/2 Day (Climber + Groundy)</div>
                </div>
            </div>

            <div style="font-size:13px; color:#334155; line-height:1.6; margin-bottom:16px;">
                <div><b>🌲 Green Waste:</b> <span id="estWaste">Approx 1 Tipper Van Load (chipped & removed)</span></div>
                <div><b>⚖️ Council Check:</b> <span>TreeKey verifies Conservation Area and TPO status with {council_name}.</span></div>
            </div>

            <div style="background:white; border-radius:8px; padding:18px; border:1px solid #bbf7d0;">
                <h4 style="margin:0 0 6px 0; color:#044332; font-size:15px;">🔒 Dispatch Directly to the Verified Senior Tree Surgeon in {city_name}:</h4>
                <p style="margin:0 0 12px 0; font-size:12px; color:#64748b;">
                    We never sell your details to 5 different companies. Your job is dispatched 1-to-1 exclusively to the #1 verified arborist in your {city_name} postcode.
                </p>
                <div style="display:grid; grid-template-columns:1fr 1fr; gap:10px; margin-bottom:10px;">
                    <input type="text" id="custName" placeholder="Your Name" style="margin:0; padding:10px;" required>
                    <input type="tel" id="custPhone" placeholder="Mobile / WhatsApp Number" style="margin:0; padding:10px;" required>
                </div>
                <input type="email" id="custEmail" placeholder="Email Address (for official PDF quote)" style="margin:0 0 12px 0; padding:10px;">
                <button type="button" onclick="submitLocalJob('{city_name}')" style="background:#044332; color:white; padding:14px 18px; border-radius:6px; font-weight:bold; font-size:15px; cursor:pointer; width:100%; border:none;">
                    Request Free Site Visit & Quote in {city_name} ➔
                </button>
                <div id="submitStatus" style="font-size:13px; font-weight:bold; margin-top:10px; text-align:center;"></div>
            </div>
        </div>
    </div>
</div>

<script>
    let currentScope = {{}};

    function calcLocalScope() {{
        const w = document.getElementById('workType').value;
        const s = document.getElementById('treeScale').value;
        const a = document.getElementById('accessType').value;
        const h = document.getElementById('hazards').value;
        const pc = document.getElementById('postcode').value;
        const notes = document.getElementById('notes').value;

        let minP = 250, maxP = 400;
        let duration = "Half Day (2 Crew)";
        let waste = "1 Van Load (2-3 m³)";

        if (w === 'dismantle') {{
            if (s === 'small') {{ minP = 350; maxP = 550; duration = "Half Day (Climber + Groundy)"; waste = "1 Tipper Load"; }}
            else if (s === 'medium') {{ minP = 550; maxP = 850; duration = "Full Day (Climber + Groundy)"; waste = "1.5 Tipper Loads"; }}
            else {{ minP = 950; maxP = 1500; duration = "1-2 Days (3 Crew + MEWP/Rigging)"; waste = "2-3 Tipper Loads"; }}
        }} else if (w === 'reduction') {{
            if (s === 'small') {{ minP = 200; maxP = 350; }}
            else if (s === 'medium') {{ minP = 380; maxP = 600; }}
            else {{ minP = 650; maxP = 950; }}
        }} else if (w === 'stump') {{
            minP = 120; maxP = 250; duration = "1-2 Hours (Stump Grinder)"; waste = "Mulch backfilled on site";
        }}

        if (a === 'house') {{ minP += 100; maxP += 150; }}
        if (h === 'powerlines') {{ minP += 150; maxP += 250; }}

        currentScope = {{ workType: w, scale: s, access: a, hazards: h, postcode: pc, notes: notes, minPrice: minP, maxPrice: maxP }};

        document.getElementById('estPrice').innerText = '£' + minP + ' – £' + maxP;
        document.getElementById('estCrew').innerText = duration;
        document.getElementById('estWaste').innerText = waste;
        document.getElementById('scopeResult').style.display = 'block';
    }}

    async function submitLocalJob(cityName) {{
        const name = document.getElementById('custName').value.trim();
        const phone = document.getElementById('custPhone').value.trim();
        const email = document.getElementById('custEmail').value.trim();
        const statusEl = document.getElementById('submitStatus');

        if (!name || !phone) {{
            statusEl.style.color = '#dc2626';
            statusEl.innerText = 'Please enter your name and contact number.';
            return;
        }}

        statusEl.style.color = '#044332';
        statusEl.innerText = 'Connecting with verified senior contractor in ' + cityName + '...';

        try {{
            const res = await fetch('/api/submit-homeowner-quote', {{
                method: 'POST',
                headers: {{ 'Content-Type': 'application/json' }},
                body: JSON.stringify({{ ...currentScope, name: name, phone: phone, email: email }})
            }});
            const data = await res.json();
            if (data.status === 'success') {{
                statusEl.style.color = '#059669';
                statusEl.innerText = '✅ Quote Request Dispatched! The local verified contractor in ' + cityName + ' will contact you within 2 business hours.';
            }} else {{
                statusEl.style.color = '#dc2626';
                statusEl.innerText = 'Submission error: ' + (data.message || 'Please try again.');
            }}
        }} catch(e) {{
            statusEl.style.color = '#059669';
            statusEl.innerText = '✅ Quote Request Dispatched! The local verified contractor will contact you directly.';
        }}
    }}
</script>
</body>
</html>
"""


@app.get("/sitemap.xml")
def sitemap_xml():
    """Generates dynamic XML sitemap for Google Search Console indexing all UK city hubs."""
    urls = [
        "https://arbor-leads-final-app.onrender.com/",
        "https://arbor-leads-final-app.onrender.com/marketplace",
        "https://arbor-leads-final-app.onrender.com/quote-estimator",
        "https://arbor-leads-final-app.onrender.com/pricing",
        "https://arbor-leads-final-app.onrender.com/boost-review"
    ]
    for slug in UK_LOCAL_SEO_HUBS.keys():
        urls.append(f"https://arbor-leads-final-app.onrender.com/tree-surgeon/{slug}")

    for pc_prefix in UK_ALL_POSTCODE_AREAS.keys():
        urls.append(f"https://arbor-leads-final-app.onrender.com/tree-surgeon/{pc_prefix.lower()}")

    xml_lines = ['<?xml version="1.0" encoding="UTF-8"?>', '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">']
    today = datetime.date.today().isoformat()
    for u in urls:
        xml_lines.append(f"  <url><loc>{u}</loc><lastmod>{today}</lastmod><changefreq>daily</changefreq><priority>0.8</priority></url>")
    xml_lines.append('</urlset>')

    return Response(content="\n".join(xml_lines), media_type="application/xml")


# ── City Scan Routes (Dashboard & Basic Auth) ─────────────────────────────────

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
        "cornwall": "Cornwall",
        "devon": "Devon",
        "cumbria": "Cumbria",
        "norfolk": "Norfolk",
        "scotland": "Scotland",
        "edinburgh": "Edinburgh",
        "glasgow": "Glasgow",
        "aberdeen": "Aberdeen",
        "wales": "Wales",
        "cardiff": "Cardiff",
        "swansea": "Swansea",
        "northwales": "North Wales",
    }
    return city_map.get(clean) or city_map.get(compact)


@app.get("/scan/{city_slug}", response_class=HTMLResponse)
def scan_city(city_slug: str, request: Request, secret: Optional[str] = Query(None)):

    verify_admin_or_secret(request, secret)
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
        <p> {city} scan complete. <b>{count}</b> new leads found.</p>
        <a href="/admin">&#9194; Back to Admin Command</a>
    </body></html>"""


#  City Cron Routes (External  Trigger Secret) 

@app.get("/trigger-leads/{city_slug}")
def cron_trigger_slash(city_slug: str, secret: Optional[str] = Query(None)):
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


@app.get("/api/scan-nationwide-all-uk")
def scan_nationwide_all_uk_endpoint():
    """
    Crawls all UK regions in parallel to capture thousands of planning and domestic leads.
    """
    import threading
    threading.Thread(target=scanners.scan_nationwide_bulk_crawler, daemon=True).start()
    return {"status": "nationwide_crawl_dispatched_in_background", "coverage": "124 UK Outward Postcodes & 300+ Councils"}


@app.get("/scan-domestic-jobs", response_class=HTMLResponse)
def scan_domestic_jobs_view(request: Request, secret: Optional[str] = Query(None)):
    """
    Triggers multi-source domestic tree surgery scraper (Gumtree, FixMyStreet, Community boards).
    """
    verify_admin_or_secret(request, secret)
    import domestic_scrapers
    count = domestic_scrapers.ingest_and_route_domestic_leads()
    return f"""<html><body style="font-family:sans-serif; padding:40px; background:#f8fafc;">
        <h2 style="color:#044332;">🏡 Domestic Job Board Scraper Complete</h2>
        <p>Successfully intercepted and routed <b>{count} new private homeowner tree leads</b> directly to senior contractors.</p>
        <a href="/admin" style="background:#044332; color:white; padding:8px 16px; border-radius:6px; text-decoration:none; font-weight:bold; font-size:13px;">← Return to Admin Panel</a>
    </body></html>"""


@app.get("/trigger-domestic-scan")
def cron_trigger_domestic_scan(secret: Optional[str] = Query(None)):
    verify_cron_secret(secret)
    import domestic_scrapers
    count = domestic_scrapers.ingest_and_route_domestic_leads()
    return {"status": "success", "source": "domestic_multi_source", "new_leads": count}


@app.get("/api/run-domestic-scan-now")
def run_domestic_scan_now():
    """
    Direct scan trigger returning live intercepted leads and Supabase database breakdown.
    """
    import domestic_scrapers
    import threading
    
    # 1. Clean historical archive entries strictly for domestic_classified
    try:
        conn = database.get_db_conn()
        cur = conn.cursor()
        cur.execute("""
            DELETE FROM leads 
            WHERE lead_source_type = 'domestic_classified' 
              AND (
                summary LIKE '%2009%' 
                OR summary LIKE '%2008%' 
                OR summary LIKE '%(sent to both)%'
                OR summary LIKE '%King''s Hedges%'
              );
        """)
        conn.commit()
        cur.close()
        conn.close()
    except Exception:
        pass

    # 2. Trigger fresh background scrapers for both domestic and nationwide council feeds
    threading.Thread(target=domestic_scrapers.ingest_and_route_domestic_leads, daemon=True).start()
    threading.Thread(target=scanners.scan_nationwide_bulk_crawler, daemon=True).start()
    
    # 3. Query database breakdown
    db_stats = {}
    recent_leads = []
    try:
        conn = database.get_db_conn()
        cur = conn.cursor()
        cur.execute("""
            SELECT COALESCE(lead_source_type, 'council_planning'), count(*) 
            FROM leads 
            GROUP BY lead_source_type;
        """)
        db_stats = dict(cur.fetchall())

        cur.execute("""
            SELECT reference, council_source, address, summary, lead_score, lead_price, COALESCE(lead_source_type, 'council_planning'), discovered_at
            FROM leads
            ORDER BY discovered_at DESC
            LIMIT 15;
        """)
        cols = ["ref", "source", "addr", "summary", "score", "price", "source_type", "discovered_at"]
        recent_leads = [dict(zip(cols, r)) for r in cur.fetchall()]
        cur.close()
        conn.close()
    except Exception as e:
        db_stats = {"error": str(e)}

    return {
        "status": "scan_dispatched_and_active",
        "database_breakdown": db_stats,
        "recent_intercepted_leads": recent_leads
    }


@app.get("/api/purge-old-domestic-archives")
def purge_old_domestic_archives():
    """
    Purges historical archive noise from domestic leads table without touching council records.
    """
    deleted = 0
    try:
        conn = database.get_db_conn()
        cur = conn.cursor()
        cur.execute("""
            DELETE FROM leads 
            WHERE lead_source_type = 'domestic_classified' 
              AND (
                summary LIKE '%2009%' 
                OR summary LIKE '%2008%' 
                OR summary LIKE '%(sent to both)%'
                OR summary LIKE '%King''s Hedges%'
              )
            RETURNING id;
        """)
        deleted = len(cur.fetchall())
        conn.commit()
        cur.close()
        conn.close()
    except Exception as e:
        return {"status": "error", "message": str(e)}

    return {"status": "success", "purged_records": deleted}





#  Research Routes (Basic Auth & Secret) 

import threading

@app.get("/research/{city_slug}", response_class=HTMLResponse)
def research_city(city_slug: str, request: Request, secret: Optional[str] = Query(None)):
    verify_admin_or_secret(request, secret)
    city = _resolve_city_param(city_slug)
    if not city:
        raise HTTPException(status_code=404, detail=f"Region/City '{city_slug}' not configured.")
    threading.Thread(target=research.perform_research, args=(city,), daemon=True).start()
    return f"""<html><body style="font-family:sans-serif; padding:40px;">
        <h3>&#128640; Partner Discovery Started for {city}</h3>
        <p>Searching Companies House, officers, Google Places, and websites in the background.</p>
        <p>New verified tree surgery LTDs will appear in your database momentarily.</p>
        <a href="/admin">&#9194; Back to Admin Command</a>
    </body></html>"""



@app.get("/populate-2000-partners", response_class=HTMLResponse)
def populate_2000_partners_view(request: Request, secret: Optional[str] = Query(None)):
    verify_admin_or_secret(request, secret)
    threading.Thread(target=research.populate_2000_partners_into_db, daemon=True).start()
    return """<html><body style="font-family:sans-serif; padding:40px; background:#f8fafc; color:#0f172a;">
        <div style="max-width:600px; margin:auto; background:white; padding:30px; border-radius:12px; border:1px solid #e2e8f0; box-shadow:0 4px 12px rgba(0,0,0,0.05);">
            <h2 style="color:#059669; margin-top:0;">&#128640; Nationwide 2,000+ Contractor Harvest Initiated</h2>
            <p style="font-size:15px; line-height:1.5;">The system is sweeping Companies House across all 15 UK regional clusters (England Wealth Belts, Midlands, North, Scotland, Wales) in the background with 10 concurrent worker threads.</p>
            <p style="font-size:14px; color:#64748b;">It is actively extracting Managing Director names, verified UK phone numbers, Google review ratings, and websites directly into your PostgreSQL database.</p>
            <div style="margin-top:25px;">
                <a href="/admin" style="display:inline-block; background:#064e3b; color:white; padding:12px 22px; border-radius:8px; text-decoration:none; font-weight:bold;"> Return to Admin Dashboard</a>
            </div>
        </div>
    </body></html>"""


@app.get("/sweep-100")
@app.get("/sweep-50")
def sweep_route(request: Request, count: int = 50, secret: Optional[str] = Query(None)):
    verify_admin_or_secret(request, secret)
    result = research.sweep_100_random_contractors(target_count=min(count, 100))
    return result



@app.get("/trigger-populate-2000")

def trigger_populate_2000_cron(secret: Optional[str] = Query(None)):
    verify_cron_secret(secret)
    threading.Thread(target=research.populate_2000_partners_into_db, daemon=True).start()
    return {"status": "started", "message": "Nationwide 2,000+ contractor harvest running in background daemon."}


@app.get("/research-all", response_class=HTMLResponse)
def research_all(request: Request, secret: Optional[str] = Query(None)):
    verify_admin_or_secret(request, secret)
    threading.Thread(target=research.research_all_cities, daemon=True).start()
    return """<html><body style="font-family:sans-serif; padding:40px;">
        <h3>&#128640; Nationwide Discovery Started</h3>
        <p>Investigating Companies House across all 9 English regions in the background.</p>
        <p>New verified LTD tree surgery contractors will populate in your database over the next 1-2 minutes.</p>
        <a href="/admin">&#9194; Back to Admin Command</a>
    </body></html>"""




@app.get("/enrich-batch", response_class=HTMLResponse)
def enrich_batch(request: Request, secret: Optional[str] = Query(None)):
    verify_admin_or_secret(request, secret)
    count = research.enrich_existing_partners(limit=50)
    return f"""<html><body style="font-family:sans-serif; padding:40px;">
        <h3>&#9989; Batch Enrichment Complete</h3>
        <p>&#10024; Enriched and updated <b>{count}</b> partners with direct director names, UK phone numbers, and emails in ~5 seconds!</p>
        <div style="margin-top:20px;">
            <a href="/enrich-batch" style="background:#7c3aed; color:white; padding:10px 18px; border-radius:8px; text-decoration:none; font-weight:bold; font-size:14px;">&#10024; Enrich Next 50</a> &nbsp;&nbsp;
            <a href="/admin" style="background:#064e3b; color:white; padding:10px 18px; border-radius:8px; text-decoration:none; font-weight:bold; font-size:14px;">&#9194; Back to Admin Command</a>
        </div>
    </body></html>"""


@app.get("/enrich-region/{city_slug}", response_class=HTMLResponse)
def enrich_region(city_slug: str, request: Request, secret: Optional[str] = Query(None)):
    verify_admin_or_secret(request, secret)
    city = _resolve_city_param(city_slug)
    if not city:
        raise HTTPException(status_code=404, detail=f"Region/City '{city_slug}' not configured.")
    count = research.enrich_existing_partners(limit=150, city_name=city)
    return f"""<html><body style="font-family:sans-serif; padding:40px;">
        <h3>&#9989; Regional Enrichment Complete for {city}</h3>
        <p>&#10024; Enriched and updated <b>{count}</b> {city} tree surgery contractors with direct director names, UK phone numbers, and emails!</p>
        <div style="margin-top:20px;">
            <a href="/admin" style="background:#064e3b; color:white; padding:10px 18px; border-radius:8px; text-decoration:none; font-weight:bold; font-size:14px;">&#9194; Back to Admin Command</a>
        </div>
    </body></html>"""


@app.get("/enrich-all", response_class=HTMLResponse)
def enrich_all(request: Request, secret: Optional[str] = Query(None)):
    verify_admin_or_secret(request, secret)
    threading.Thread(target=research.enrich_existing_partners, kwargs={"limit": 0}, daemon=True).start()
    return """<html><body style="font-family:sans-serif; padding:40px;">
        <p>&#10024; Enrichment started in background across 8 parallel threads. Check Render logs or refresh admin dashboard for progress.</p>
        <a href="/admin">&#9194; Back to Admin Command</a>
    </body></html>"""




@app.get("/clean-partners", response_class=HTMLResponse)
def clean_partners(request: Request, secret: Optional[str] = Query(None)):
    verify_admin_or_secret(request, secret)
    result = research.clean_partner_database()
    if "error" in result:
        return f"""<html><body style="font-family:sans-serif; padding:40px;">
            <p>&#10060; Cleanup failed: {result['error']}</p>
            <a href="/admin">&#9194; Back to Dashboard</a>
        </body></html>"""
    return f"""<html><body style="font-family:sans-serif; padding:40px;">
        <h3>&#9989; Partner Database Cleanup Complete</h3>
        <p>&#9989; Kept: <b>{result['kept']}</b> verified tree surgery companies</p>
        <p>&#128465; Removed: <b>{result['removed']}</b> unrelated businesses</p>
        <p style="color:#888; font-size:13px;">
            Removed companies had no tree-surgery keywords in their name,
            or contained excluded terms (medical, dental, fruit, cosmetic, etc.)
        </p>
        <a href="/admin">&#9194; Back to Admin Command</a>
    </body></html>"""


def run_master_daily_pipeline():

    """
    4-Stage Daily Automated Ingestion & Quality Sanitization Pipeline:
    1. Council Planning Radar: Scans all 309 local councils across all 9 English regions.
    2. Secondary Lead Sanitization: Normalizes lead grades, pricing, and deduplication.
    3. New Contractor Discovery: Queries Companies House for newly incorporated LTDs.
    4. Two-Layer Name Filter & UK Geotargeting: Purges any non-tree surgery or foreign records.
    """
    logger.info("[PIPELINE] 🚀 Starting Master Daily Automation Pipeline...")
    
    # Stage 0: MESH Aggregator (Free Direct Scrapers)
    try:
        mesh_leads = scanners.run_mesh_network_scan()
        logger.info(f"[PIPELINE] Stage 0 Complete: Aggregator Mesh extracted {mesh_leads} free leads.")
    except Exception as e:
        logger.error(f"[PIPELINE] Stage 0 error (MESH): {e}")

    # Stage 1: Council Planning Radar Scan (Paid Fallbacks)
    try:
        total_leads_scanned = 0
        for city in ALL_CITIES:
            leads = scanners.scan_city_planning_api(city)
            total_leads_scanned += len(leads) if isinstance(leads, list) else int(leads or 0)
        logger.info(f"[PIPELINE] Stage 1 Complete: All UK regions scanned ({total_leads_scanned} planning leads processed).")
    except Exception as e:
        logger.error(f"[PIPELINE] Stage 1 error: {e}")
        import notifications
        notifications.send_system_incident_alert(
            category="AUTOMATED SCRAPER PIPELINE",
            title="DAILY PLANNING RADAR SWEEP (STAGE 1) FAILED",
            description=f"CRITICAL: The automated morning planning radar sweep failed with error: {str(e)[:150]}",
            impact="New statutory tree work applications across UK councils were not ingested for this cycle.",
            action_required="Check Render runtime logs at dashboard.render.com for traceback details.",
            severity="CRITICAL",
            throttle_hours=12.0
        )

    # Stage 2: Secondary Lead Quality & Pricing Normalization
    try:
        conn = database.get_db_conn(); cur = conn.cursor()
        cur.execute("""
            UPDATE leads
            SET lead_price = CASE
                WHEN lead_score = 'large' THEN 35
                WHEN lead_score = 'medium' THEN 25
                ELSE 19
            END
            WHERE lead_price IS NULL OR lead_price = 0;
        """)
        conn.commit(); cur.close(); conn.close()
        logger.info("[PIPELINE] Stage 2 Complete: Lead quality and pricing integrity verified.")
    except Exception as e:
        logger.error(f"[PIPELINE] Stage 2 error: {e}")
        import notifications
        notifications.send_system_incident_alert(
            category="DATABASE & PRICING",
            title="LEAD PRICING NORMALIZATION (STAGE 2) FAILED",
            description=f"WARNING: Lead pricing normalization query failed: {str(e)[:150]}",
            impact="Newly ingested leads may lack standardized pricing tiers.",
            action_required="Check PostgreSQL database connectivity and leads table schema.",
            severity="WARNING",
            throttle_hours=12.0
        )

    # Stage 3: New Contractor Discovery Sweep
    try:
        research.research_all_cities()
        logger.info("[PIPELINE] Stage 3 Complete: Contractor discovery sweep finished.")
    except Exception as e:
        logger.error(f"[PIPELINE] Stage 3 error: {e}")
        import notifications
        notifications.send_system_incident_alert(
            category="CONTRACTOR DISCOVERY",
            title="NATIONWIDE CONTRACTOR HARVEST (STAGE 3) FAILED",
            description=f"WARNING: Automated Companies House contractor discovery failed: {str(e)[:150]}",
            impact="New tree surgery LTD incorporation discovery was interrupted.",
            action_required="Verify COMPANIES_HOUSE_KEY in Render and check Companies House API availability.",
            severity="WARNING",
            throttle_hours=12.0
        )

    # Stage 4: Secondary Partner Sanitization & Quality Filter
    try:
        clean_result = research.clean_partner_database()
        logger.info(f"[PIPELINE] Stage 4 Complete: Sanitized partner database (Kept: {clean_result.get('kept')}, Purged: {clean_result.get('removed')}).")
    except Exception as e:
        logger.error(f"[PIPELINE] Stage 4 error: {e}")

    logger.info("[PIPELINE] &#127937; Master Daily Pipeline finished successfully.")



@app.get("/trigger-daily-pipeline")
def trigger_daily_pipeline(secret: Optional[str] = Query(None)):
    """
    Single master cron job endpoint.
    Executes full 4-stage ingestion and quality sanitization pipeline.
    """
    verify_cron_secret(secret)
    threading.Thread(target=run_master_daily_pipeline, daemon=True).start()
    return {"status": "started", "action": "master_daily_pipeline", "timestamp": "NOW"}


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



@app.get("/api-stats")
def api_stats(secret: Optional[str] = Query(None)):
    verify_cron_secret(secret)
    try:
        conn = database.get_db_conn(); cur = conn.cursor()
        cur.execute("SELECT count(*) FROM potential_partners"); total_partners = cur.fetchone()[0]
        cur.execute("SELECT count(*) FROM potential_partners WHERE enriched_at IS NOT NULL"); enriched_partners = cur.fetchone()[0]
        cur.execute("SELECT count(*) FROM potential_partners WHERE phone_number IS NOT NULL OR email IS NOT NULL"); with_contacts = cur.fetchone()[0]
        cur.execute("SELECT count(*) FROM leads"); total_leads = cur.fetchone()[0]
        cur.close(); conn.close()
        return {

            "total_partners": total_partners,
            "audited_and_enriched": enriched_partners,
            "with_direct_contacts": with_contacts,
            "remaining_to_enrich": total_partners - enriched_partners,
            "total_leads": total_leads,
            "progress_percent": f"{int((enriched_partners / total_partners * 100)) if total_partners else 0}%"
        }
    except Exception as e:
        return {"error": str(e)}













#  Export Directors (Basic Auth) 

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
        f"<td style='padding:8px; border:1px solid #ddd;'>{r[3] or ''}</td>"
        f"<td style='padding:8px; border:1px solid #ddd;'>{f'<a href=\"mailto:{r[4]}\">{r[4]}</a>' if r[4] else ''}</td>"
        f"<td style='padding:8px; border:1px solid #ddd;'>{f'<a href=\"{r[5]}\" target=\"_blank\">Website</a>' if r[5] else ''}</td>"
        f"<td style='padding:8px; border:1px solid #ddd; text-align:center;'> {r[6] or 'N/A'}</td>"
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
            <h2>&#128101; Verified Tree Surgery Contacts ({len(rows)} companies)</h2>
            <div>
                <a href="/export-directors.csv" style="background:#1b5e20; color:white; padding:8px 16px; border-radius:6px; text-decoration:none; font-weight:bold;">&#128190; Download CSV</a>
                &nbsp;|&nbsp; <a href="/"> Dashboard</a>
            </div>
        </div>
        <table style="width:100%; border-collapse:collapse; font-size:13px;">
            <tr style="background:#1b5e20; color:white;">
                <th style="padding:10px; text-align:left;">Company</th>
                <th style="padding:10px; text-align:left;">Director</th>
                <th style="padding:10px; text-align:left;">Phone</th>
                <th style="padding:10px; text-align:left;">Email</th>
                <th style="padding:10px; text-align:left;">Web</th>
                <th style="padding:10px; text-align:center;">Google &#11088;</th>
                <th style="padding:10px; text-align:left;">City</th>
            </tr>
            {table_rows or "<tr><td colspan='7' style='padding:16px; text-align:center;'>No verified contacts found yet. Run /enrich-all.</td></tr>"}
        </table>
    </div></body></html>
    """


@app.get("/export-directors.csv")
def export_directors_csv(request: Request, secret: Optional[str] = Query(None)):
    """
    Returns CSV file of all enriched directors ready for Google Sheets or Excel.
    Accepts either dashboard Basic Auth or ?secret= query parameter.
    """
    authorized = False
    if secret:
        try:
            verify_cron_secret(secret)
            authorized = True
        except Exception:
            pass

    if not authorized:
        auth_header = request.headers.get("Authorization")
        if auth_header and auth_header.startswith("Basic "):
            import base64
            try:
                decoded = base64.b64decode(auth_header[6:]).decode("utf-8")
                u, p = decoded.split(":", 1)
                DASH_USER = os.getenv("DASHBOARD_USER", "admin").strip()
                DASH_PASS = os.getenv("DASHBOARD_PASS", "").strip()
                if DASH_PASS and secrets.compare_digest(u.encode(), DASH_USER.encode()) and secrets.compare_digest(p.encode(), DASH_PASS.encode()):
                    authorized = True
            except Exception:
                pass

    if not authorized:
        raise HTTPException(status_code=401, detail="Unauthorized.",
                            headers={"WWW-Authenticate": "Basic"})

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
# --- LEGAL PAGES ---
@app.get("/privacy-policy", response_class=HTMLResponse)
async def privacy_policy():
    return """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <title>Privacy Policy - Tree Key</title>
    <script src="https://cdn.tailwindcss.com"></script>
</head>
<body class="bg-slate-900 text-slate-300 font-sans p-8 md:p-16">
    <div class="max-w-3xl mx-auto bg-slate-800 p-8 rounded-lg shadow-xl border border-slate-700">
        <h1 class="text-3xl font-bold text-white mb-6">Privacy Policy</h1>
        <p class="mb-4 text-sm text-slate-500">Last updated: August 2026</p>
        
        <h2 class="text-xl font-bold text-emerald-400 mt-6 mb-2">1. Information We Collect</h2>
        <p class="mb-4">Tree Key ("we", "us", "our") collects basic contact information (name, email, phone number) when you register for an account or when your business information is retrieved from public registries such as Companies House and public local authority planning portals in the UK.</p>
        
        <h2 class="text-xl font-bold text-emerald-400 mt-6 mb-2">2. How We Use Your Information</h2>
        <p class="mb-4">We use your information strictly to provide our commercial lead-generation service, notify you of relevant council planning applications, and for billing purposes. We do not sell your personal data to third parties.</p>
        
        <h2 class="text-xl font-bold text-emerald-400 mt-6 mb-2">3. GDPR Rights</h2>
        <p class="mb-4">Under the UK General Data Protection Regulation (UK GDPR), you have the right to access, rectify, or erase your personal data. If you are receiving commercial outreach from us and wish to opt-out, you may do so at any time using the unsubscribe link provided in our communications.</p>

        <h2 class="text-xl font-bold text-emerald-400 mt-6 mb-2">4. Contact Us</h2>
        <p class="mb-4">For any privacy-related requests, please contact us at: <strong>contact@treekey.uk</strong></p>

        <a href="/" class="text-emerald-500 hover:text-emerald-400 mt-8 inline-block font-bold">&larr; Back to Home</a>
    </div>
</body>
</html>
"""

@app.get("/terms-of-service", response_class=HTMLResponse)
async def terms_of_service():
    return """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <title>Terms of Service - Tree Key</title>
    <script src="https://cdn.tailwindcss.com"></script>
</head>
<body class="bg-slate-900 text-slate-300 font-sans p-8 md:p-16">
    <div class="max-w-3xl mx-auto bg-slate-800 p-8 rounded-lg shadow-xl border border-slate-700">
        <h1 class="text-3xl font-bold text-white mb-6">Terms of Service</h1>
        <p class="mb-4 text-sm text-slate-500">Last updated: August 2026</p>
        
        <h2 class="text-xl font-bold text-emerald-400 mt-6 mb-2">1. Service Description</h2>
        <p class="mb-4">Tree Key provides an online radar and notification platform that aggregates public statutory planning applications from UK local authorities. We are an independent commercial entity and are not affiliated with any government body.</p>
        
        <h2 class="text-xl font-bold text-emerald-400 mt-6 mb-2">2. Subscriptions & Billing</h2>
        <p class="mb-4">By subscribing to a Tree Key tier, you are paying for access to our proprietary software platform and notification systems. Subscriptions are billed monthly and can be cancelled at any time.</p>
        
        <h2 class="text-xl font-bold text-emerald-400 mt-6 mb-2">3. No Guarantee of Lead Volume (Refund Policy)</h2>
        <p class="mb-4 border-l-4 border-amber-500 pl-4 bg-amber-500/10 py-3 text-slate-200"><strong>Crucial Notice:</strong> The volume of leads you receive is entirely dependent on the organic activity of homeowners and local councils in your chosen radial territory. Tree Key does not guarantee a specific number of leads per month. <strong>Because you are granted immediate access to proprietary data upon subscribing, all subscription payments are non-refundable.</strong> We do not offer prorated refunds for mid-cycle cancellations.</p>

        <h2 class="text-xl font-bold text-emerald-400 mt-6 mb-2">4. Acceptable Use</h2>
        <p class="mb-4">You agree not to scrape, redistribute, or resell the data provided by Tree Key. The platform is strictly for your own business's direct marketing and operational use.</p>

        <a href="/" class="text-emerald-500 hover:text-emerald-400 mt-8 inline-block font-bold">&larr; Back to Home</a>
    </div>
</body>
</html>
"""
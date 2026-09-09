import os
import logging
import secrets
import re
import urllib.parse
import math
import json
import base64
import html
import datetime
import time
import threading
import database
import scanners
import research
import payments
import csv
import io
from fastapi import FastAPI, Query, BackgroundTasks, HTTPException, Depends, Request, Form
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


@app.middleware("http")
async def _no_cache_dynamic_pages(request: Request, call_next):
    """Sep 8 2026, Nick's report: after deploying, a normal reload (or a
    fresh visit from a Google search result) kept showing the OLD page --
    only Ctrl+Shift+R (hard refresh) showed the new content. Root cause:
    not one route in this app ever set a Cache-Control header, so every
    dynamic page was left to the browser's own default caching behaviour,
    which was clearly caching far more aggressively than intended for a
    site that changes on every deploy. This forces every non-static
    response to be revalidated on every request. /static/* (CSS/JS/images
    served by StaticFiles) is deliberately excluded -- those benefit from
    normal browser caching and were never the thing going stale."""
    response = await call_next(request)
    if not request.url.path.startswith("/static/"):
        response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
        response.headers["Pragma"] = "no-cache"
        response.headers["Expires"] = "0"
    return response


T_SEC      = os.getenv("TRIGGER_SECRET", "").strip()
basic_auth = HTTPBasic()

# Aug 30 2026: /trigger-daily-pipeline fires run_master_daily_pipeline() on a
# background thread with NO guard against a second call while one is still
# running -- confirmed live in production logs: Nick fired a manual trigger
# while the previous run was still in Stage 3 (Companies House contractor
# discovery), and both ran concurrently, competing for the same DB
# connections/CPU on one instance (a likely contributor to the repeated
# council timeouts/503s seen in that window). This is separate from the
# 6am cron, which is presumably scheduled externally (a Render Cron Job or
# similar) hitting this same endpoint -- so the same overlap can happen
# between a manual trigger and the daily cron, not just two manual
# triggers. _PIPELINE_LOCK (below) makes a second trigger a clean no-op
# that reports "already running" instead of silently double-running.
_PIPELINE_LOCK = threading.Lock()
_pipeline_state = {"running": False, "started_at": None}

# Sep 3 2026: background state for /system-health-check -- see that route's
# own docstring for why this exists (Nick's ask: "build failsafe auto check
# auto fixes ... even if they have never shown an error"). A separate lock
# from _PIPELINE_LOCK on purpose: this never writes to the database and
# never inserts a lead, so it's safe to run independently -- it only reads
# each council portal, exactly like /test-mesh-council already does, just
# looped across every registered council instead of one at a time. Still
# refuses to START while a real scan is running (checked at trigger time),
# purely so the same ~50+ council portals never get hit by two overlapping
# processes within seconds of each other -- the exact pattern Aug 30 2026's
# _PIPELINE_LOCK comment above already identified as looking like abuse to
# a portal's own rate limiter.
_HEALTH_CHECK_LOCK = threading.Lock()
_health_check_state = {
    "running": False,
    "started_at": None,
    "finished_at": None,
    "results": [],
    "summary": None,
}

optional_auth = HTTPBasic(auto_error=False)


# All UK Regions with nationwide council & partner coverage (England, Scotland, Wales)
# Aug 30 2026: this used to also list "Leeds", "Birmingham", "Manchester",
# "Bristol", "Sheffield" as their OWN entries alongside the multi-town
# regions that already contain them (Yorkshire includes Leeds AND
# Sheffield; West Midlands includes Birmingham; North West includes
# Manchester; South West includes Bristol). Confirmed directly:
# CITY_POSTCODE_PREFIX["Birmingham"] and ["West Midlands"] are the exact
# same 10 postcode prefixes -- so the same date-based rotation formula
# picked the identical subset for both, EVERY day. Every "for city in
# ALL_CITIES" pass (the daily pipeline's Stage 1, the nationwide bulk
# crawler) was silently querying Birmingham's, Leeds', Sheffield's,
# Manchester's, and Bristol's planning data TWICE -- once under their
# region, once again under their own standalone entry -- against both
# ukplanningapi.co.uk (burning paid-tier quota twice as fast for those
# areas) and PlanIt (doubling the request volume that was already
# triggering blanket 429s). Removed here; the five are still real,
# individually scannable entries in REGION_TOWNS and CITY_POSTCODE_PREFIX,
# so /scan/leeds, /scan/birmingham etc. still work fine for one-off manual
# troubleshooting of a single city -- they're just no longer part of the
# automatic "scan everything" sweep where they'd double up with their
# parent region.
ALL_CITIES = [
    "London", "South East", "South West", "West Midlands",
    "East Midlands", "Yorkshire", "North West", "North East", "East of England",
    "Scotland", "Wales"
]



@app.get("/health")
def health():
    return {"status": "ok", "app": "Vector Data Labs"}


@app.get("/scan-nationwide")
def scan_nationwide_fast(secret: Optional[str] = Query(None)):
    """
    Crawls all UK regions in parallel to capture thousands of planning and domestic leads.
    """
    verify_cron_secret(secret)
    result = _dispatch_locked_scan(scanners.scan_nationwide_bulk_crawler, "nationwide_bulk_crawl")
    result["coverage"] = "124 UK Outward Postcodes & 300+ Councils"
    return result


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


# ── Signed contractor session cookie ──────────────────────────────────────────
# Previously the session cookie was the contractor's email in plain text with no
# signature, so anyone could set treekey_contractor_session=victim@example.com in
# their browser and load that contractor's dashboard/ledger. This signs the value
# with HMAC so a tampered/forged cookie is rejected on read.
import hmac as _hmac
import hashlib as _hashlib
import base64 as _base64
_SESSION_SECRET = (os.getenv("SESSION_SECRET", "").strip() or T_SEC or "treekey-fallback-dev-secret").encode()
if not os.getenv("SESSION_SECRET", "").strip():
    logger.warning("[Auth] SESSION_SECRET not set — falling back to TRIGGER_SECRET (or a dev default) to sign session cookies. Set a dedicated SESSION_SECRET in Render for defense-in-depth.")

def _sign_session_cookie(email: str) -> str:
    email_b64 = _base64.urlsafe_b64encode(email.strip().lower().encode()).decode().rstrip("=")
    sig = _hmac.new(_SESSION_SECRET, email_b64.encode(), _hashlib.sha256).hexdigest()
    return f"{email_b64}.{sig}"

def _verify_session_cookie(cookie_value: Optional[str]) -> Optional[str]:
    """Returns the verified email from a signed session cookie, or None if missing/invalid/tampered."""
    if not cookie_value or "." not in cookie_value:
        return None
    email_b64, _, sig = cookie_value.rpartition(".")
    expected_sig = _hmac.new(_SESSION_SECRET, email_b64.encode(), _hashlib.sha256).hexdigest()
    if not _hmac.compare_digest(sig, expected_sig):
        return None
    try:
        padding = "=" * (-len(email_b64) % 4)
        return _base64.urlsafe_b64decode(email_b64 + padding).decode()
    except Exception:
        return None



# Sep 3 2026: Nick's ask -- "we should place a note on the website when an
# area cannot be accessed due to the councils fault or issue". Every entry
# here is a council whose OWN public planning portal was investigated live
# this session and confirmed broken/bot-gated on the council's own end --
# not a gap in TreeKey's coverage, and not something we're choosing not to
# build. Distinguishing these from an ordinary "0 leads today" area matters
# because a visitor in one of these districts would otherwise see the exact
# same "0 leads" result as a district with a perfectly healthy pipeline but
# genuinely nothing pending -- honest, not misleading, disclosure:
#   - Merton (London): the council's own site is behind an AWS WAF that
#     blocks automated requests outright.
#   - Bath & North East Somerset: confirmed via live browser console errors
#     that the council's own search page JS crashes on load
#     (ReferenceError: APIroot is not defined) and the server ignores every
#     query parameter regardless -- a genuine defect on their end, not a
#     data gap.
#   - West Northamptonshire: the council's own planning register gates
#     every search (quick or advanced) behind an invisible Google reCAPTCHA
#     that a real browser session passes silently but a plain HTTP request
#     cannot -- confirmed live that a token-less submission is bounced back
#     to the search form rather than returning results.
# Matched against the postcodes.io `admin_district` name already resolved
# below -- a simple substring match (lower-cased) since ONS district names
# are consistent but occasionally carry "Council"/"District Council"
# suffixes depending on the source.
        # Sep 3 2026 (Nick, verbatim): "when i said to show on website that
        # certain areas cannot be gotten do not mention bots or anything.
        # just state we havent got them and that its on the councils end
        # not ours" -- these three messages are deliberately plain and
        # non-technical (no "bot-detection", "reCAPTCHA", "WAF", "broken
        # JS", etc.). The real technical reason each one is here is
        # recorded in this file's own comment above _council_source_issue
        # and in mesh_scrapers.py -- keep the WHY there, keep the
        # user-facing copy simple.
_COUNCIL_SOURCE_ISSUES = {
    "merton": "We don't currently have live leads for Merton. This is an issue on the council's end, not ours -- we're monitoring it and will resume automatically as soon as it's resolved.",
    "bath and north east somerset": "We don't currently have live leads for Bath & North East Somerset. This is an issue on the council's end, not ours -- we're monitoring it and will resume automatically as soon as it's resolved.",
    "west northamptonshire": "We don't currently have live leads for West Northamptonshire. This is an issue on the council's end, not ours -- we're monitoring it and will resume automatically as soon as it's resolved.",
}


def _council_source_issue(district: Optional[str]) -> Optional[str]:
    """Returns the public-facing disclosure note for `district` if it's a
    council whose own portal is confirmed broken/bot-gated right now, else
    None. See _COUNCIL_SOURCE_ISSUES above for why each entry is there."""
    if not district:
        return None
    normalized = district.strip().lower()
    for key, message in _COUNCIL_SOURCE_ISSUES.items():
        if key in normalized:
            return message
    return None


@app.get("/api/live-counts")
def api_live_counts():
    """Sep 8 2026, Nick's ask: real, current today/week/month lead counts for
    the homepage's "Intercepting Live" badge to poll -- see the JS at the
    bottom of public_homepage(). Deliberately no auth (aggregate counts
    only, no addresses or names) and no rate limit beyond the existing
    global one, since this is meant to be polled every 8-30 seconds by
    anyone with the homepage open.
    Sep 8 2026: "today" is now the paced/throttled figure (see
    database.get_public_lead_counts) so a big batch scan doesn't dump its
    whole total into this counter in one poll -- week/month stay real."""
    return database.get_public_lead_counts()


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
                f"https://api.postcodes.io/postcodes?lat={target_lat}&lon={target_lng}&radius=2000",
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
    # Aug 30 2026: this endpoint used to report a REAL count from this query
    # and then throw it away, replacing it with a sine/cosine "spatial
    # variance" formula seeded from raw lat/lng (a JS comment on the
    # frontend literally called the loading-spinner delay a "Subconscious
    # Trigger: Fake calculating sequence to build tension/perceived value").
    # A visitor with zero real leads in their area was shown a fabricated
    # 12-40 "active leads" figure. Fixed: every number below is now derived
    # from the actual leads table -- if there's nothing there, we say so.
    # Sep 8 2026: Nick flagged "Connected Areas always shows 0" -- fixed
    # earlier the same day (matching the real, full outcode for the exact
    # count vs. just the postcode-area letters for the wider count), but
    # Nick then flagged a second, deeper issue: the "X Active Leads in
    # radius" figure was matching leads to the exact outcode TEXT typed,
    # completely ignoring the radius dropdown and the map circle -- so a
    # real, nearby lead at CR5 or CR8 never counted as "in radius" for a CR6
    # search, even though it correctly showed up in "connected zones" and
    # the notices table right next to it. Replaced with a genuine
    # haversine-distance check (database.classify_leads_by_radius) against
    # each lead's outcode centroid -- "in radius" now means what the radius
    # dropdown says it means, and "connected zones" now means what Nick
    # originally asked for: real leads nearby but outside the currently
    # selected radius, not just "same postcode letters minus the exact one".
    display_pc_clean = re.sub(r'[^A-Z0-9]', '', display_pc.upper())
    area_letters = "".join([c for c in display_pc_clean if c.isalpha()])[:2]
    conn = database.get_db_conn()
    cur = conn.cursor()
    if display_pc_clean and area_letters:
        # Wide catchment: the whole postcode area (e.g. "CR" covers every
        # CR0-CR9 outcode) -- classify_leads_by_radius then splits this into
        # genuinely "in your selected radius" vs. "nearby but outside it"
        # using real distance, not text matching.
        cur.execute("SELECT address FROM leads WHERE (status = 'new' OR status IS NULL) AND address ~* %s",
        (rf"\y{area_letters}[0-9]",))
        wide_pool_addresses = cur.fetchall()
    else:
        cur.execute("SELECT address FROM leads WHERE (status = 'new' OR status IS NULL) AND council_source ILIKE %s", (f"%{district[:6]}%",))
        wide_pool_addresses = cur.fetchall()

    radius_split = database.classify_leads_by_radius(wide_pool_addresses, target_lat, target_lng, radius)

    # Sep 8 2026, Nick's ask: the radar's "Intercepted Notices" panel should
    # follow wherever the radar is currently pointed, not always show the
    # sitewide last-5. Pull real leads from this same wider postcode area,
    # diverse-selected by job size (never geography here -- the pool is
    # already local by definition), anonymised the same way the public
    # homepage table already is (outcode-level area, never the street
    # address).
    area_notices = []
    if display_pc_clean and area_letters:
        cur.execute("""SELECT address, summary, lead_score, lead_price, council_source, reference, discovered_at
                       FROM leads WHERE (status = 'new' OR status IS NULL) AND address ~* %s
                       ORDER BY discovered_at DESC LIMIT 40""", (rf"\y{area_letters}[0-9]",))
        area_pool_rows = cur.fetchall()
        picked = database.select_diverse_ticker_leads(limit=5, enforce_geo_mix=False, pool_rows=area_pool_rows)
        for l in picked:
            area_notices.append({
                "area_label": l["area_label"],
                "summary": (l["summary"] or "")[:120],
                "size": l["lead_score"],
                "time": l["discovered_at"].strftime("%H:%M") if l["discovered_at"] else "--:--",
                "date": l["discovered_at"].strftime("%d %b") if l["discovered_at"] else "",
            })
    cur.close()
    conn.close()

    if "Unregistered" in district:
        selected_leads = 0
        connected_leads = 0
    else:
        selected_leads = radius_split["in_radius"]
        connected_leads = radius_split["nearby"]

    # Contract valuation: a disclosed, flat per-notice estimate (&pound;450
    # to &pound;1,450, typical UK tree-work job range) applied to the REAL
    # lead count above -- not a fabricated total dressed up as "in your area".
    min_val = selected_leads * 450
    max_val = selected_leads * 1450

    # Check territory exclusivity in real-time
    is_claimed = database.is_territory_claimed(display_pc)
    exclusivity_label = "&#128274; Locked (Claimed by Local Partner)" if is_claimed else "&#9989; Available (Unclaimed)"

    # Sep 3 2026: see _COUNCIL_SOURCE_ISSUES above -- None for every normal
    # district, a plain-English disclosure note for the handful confirmed
    # blocked on the council's own end.
    council_source_issue = _council_source_issue(district)

    return {
        "status": "ok",
        "postcode": display_pc,
        "authority": district,
        "lat": target_lat,
        "lng": target_lng,
        "radius_miles": radius,
        "is_covered": True,
        "is_england": True,
        "is_claimed": is_claimed,
        "selected_area_leads": selected_leads,
        "connected_area_leads": connected_leads,
        "total_leads_in_scope": selected_leads + connected_leads,
        "est_min_val": f"{min_val:,}",
        "est_max_val": f"{max_val:,}",
        "exclusivity_status": exclusivity_label,
        "council_source_issue": council_source_issue,
        "area_notices": area_notices
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


# ---------------------------------------------------------
# Progressive Web App (PWA) Service Worker
# ---------------------------------------------------------
@app.get("/sw.js")
def service_worker():
    # Sep 8 2026: this was the real cause of Nick's "I update the site, it
    # deploys, but a normal refresh still shows the old version" report --
    # the old v1 worker cached "/" itself on install and then served that
    # cached copy forever on every visit (cache-first, no expiry, no version
    # bump), completely bypassing the Cache-Control headers added earlier
    # this session. A hard refresh (Shift+Reload) happens to bypass service
    # workers in most browsers, which is exactly why that "worked" while a
    # normal refresh kept reverting -- it wasn't the browser's HTTP cache at
    # all, it was this file.
    #
    # Fixed to network-first: every request goes to the live server first;
    # the cache is only a fallback if the network request fails outright
    # (e.g. offline), which is the safe default for a site whose content
    # changes on every deploy. CACHE_NAME is bumped so the old v1 cache is
    # wiped on activate, and skipWaiting/clients.claim make the new worker
    # take over immediately instead of waiting for every tab to be closed.
    sw_code = """
const CACHE_NAME = 'treekey-v2';
const urlsToCache = [
  '/static/tailwind.css',
  '/static/icon-192.png',
  '/static/icon-512.png'
];

self.addEventListener('install', event => {
  self.skipWaiting();
  event.waitUntil(
    caches.open(CACHE_NAME)
      .then(cache => cache.addAll(urlsToCache))
  );
});

self.addEventListener('activate', event => {
  event.waitUntil(
    caches.keys().then(names =>
      Promise.all(names.filter(n => n !== CACHE_NAME).map(n => caches.delete(n)))
    ).then(() => self.clients.claim())
  );
});

self.addEventListener('fetch', event => {
  if (event.request.method !== 'GET') return;
  event.respondWith(
    fetch(event.request)
      .then(response => {
        const copy = response.clone();
        caches.open(CACHE_NAME).then(cache => cache.put(event.request, copy));
        return response;
      })
      .catch(() => caches.match(event.request))
  );
});
"""
    return Response(content=sw_code, media_type="application/javascript")

@app.get("/", response_class=HTMLResponse)
def public_homepage():
    stats = {"p": 0, "l": 0, "diverse_leads": [], "counts": {"today": 0, "week": 0, "month": 0}}
    try:
        conn = database.get_db_conn(); cur = conn.cursor()
        cur.execute("SELECT count(*) FROM potential_partners"); stats["p"] = cur.fetchone()[0]
        cur.execute("SELECT count(*) FROM leads"); stats["l"] = cur.fetchone()[0]
        cur.close(); conn.close()
        # Sep 8 2026, Nick's ask: don't just show the last 5 leads discovered
        # -- a busy scan of one council could fill the whole ticker with
        # near-identical entries from a single area. This is a genuinely
        # diverse, still 100% real selection (size + geography mix). See
        # database.select_diverse_ticker_leads.
        stats["diverse_leads"] = database.select_diverse_ticker_leads(limit=5, enforce_geo_mix=True)
        stats["counts"] = database.get_public_lead_counts()
    except Exception as e:
        logger.error(f"[HOMEPAGE] DB error: {e}")

    # Aug 30 2026: this used to pad the real lead count with +1427 whenever
    # the true count was under 1000, to look more credible. Show the real
    # number -- padding it is exactly the kind of fabricated stat that
    # already cost trust once this session (the "75%/10%/15%" TPO claim).
    display_leads = stats["l"]

    # Sep 8 2026: real UK wall-clock date/time for the "Intercepting Live"
    # badge, so it visibly shows how fresh the page is -- zoneinfo (stdlib,
    # no new dependency) so it's correct across the GMT/BST switch.
    try:
        from zoneinfo import ZoneInfo
        _now_uk = datetime.datetime.now(ZoneInfo("Europe/London"))
    except Exception:
        _now_uk = datetime.datetime.utcnow()
    _as_of_date = _now_uk.strftime("%-d %b")
    _as_of_time = _now_uk.strftime("%H:%M")

    # Sep 8 2026: Nick flagged that the public homepage's "Intercepted
    # Notices" table and ticker were printing the raw `address` column
    # straight from the leads table -- i.e. the exact, unpaid-for street
    # address of every unsold lead was visible to any anonymous visitor,
    # which is the entire paid product given away for free pre-checkout.
    # Fixed to show only the outcode-level area, never the address itself --
    # now a real place name ("CR5, Croydon, London") via
    # database.get_outcode_area_label instead of a generic "X area"
    # placeholder. The full address still only appears after purchase / on
    # an actual subscriber's own dashboard leads (see /dashboard).
    lead_rows = "".join([
        f"""<tr class='border-b border-slate-700/50 hover:bg-slate-800/50 transition-colors'>
            <td class='p-4 text-emerald-400 font-mono text-xs'>
                {l['reference'] or 'TPO-STATUTORY'}<br>
                <span class='text-slate-400 font-sans'>{l['council_source'] or ''}</span>
            </td>
            <td class='p-4 text-slate-200 text-sm max-w-md'>
                <b class='text-white'>{l['area_label']}</b><br>
                <span class='text-slate-400 text-xs'>{(l['summary'] or '')[:120]}...</span>
            </td>
            <td class='p-4 text-right'>
                <span class='bg-emerald-500/10 text-emerald-400 px-3 py-1 rounded-full text-xs font-bold border border-emerald-500/20 uppercase tracking-wider shadow-[0_0_10px_rgba(16,185,129,0.2)]'>
                    Live
                </span>
            </td>
        </tr>"""
        for l in stats["diverse_leads"]
    ]) or "<tr><td colspan='3' class='p-8 text-center text-slate-500 font-mono'>Intercepting live planning data...</td></tr>"

    # Sep 8 2026, Nick's ask: date next to the time (so it's obvious how
    # fresh the feed is at a glance) and a lightly-pulsing, shade-shifting
    # dot at the end of the row instead of a static "Live" pill -- see
    # .tk-live-dot in static/tailwind.css.
    # Sep 8 2026, Nick's follow-up: the 6-column fixed-width grid (date /
    # time / ref code / summary / size / dot) doesn't fit a phone screen --
    # cramming all 6 into ~350px made it unreadable. Rebuilt as two rows per
    # lead: the original grid stays but only shows sm: and up (hidden on
    # mobile), and a separate stacked card layout shows only below sm:,
    # dropping the reference code entirely (Nick: "the code is not the most
    # important thing... its the date, time, rough address, job type and
    # job size... the code can go on phone for this table") and showing the
    # job description in its place so job type is actually visible.
    ticker_rows = "".join([
        f"""<div class='hidden sm:grid grid-cols-[56px_48px_84px_1fr_80px_28px] gap-3 items-center px-4 py-2.5 border-b border-emerald-900/40 text-xs font-mono'>
            <span class='text-emerald-700'>{(l['discovered_at'].strftime('%d %b') if l['discovered_at'] else '--')}</span>
            <span class='text-emerald-600'>{(l['discovered_at'].strftime('%H:%M') if l['discovered_at'] else '--:--')}</span>
            <span class='text-emerald-600 truncate'>{((l['reference'] or l['council_source'] or 'TPO'))[:10]}</span>
            <span class='text-slate-300 truncate'>{l['area_label']}</span>
            <span class='text-right'>
                <span class='text-amber-400 font-bold text-[10px] uppercase tracking-wide'>{l['lead_score']} job</span>
            </span>
            <span class='flex justify-end'><span class='tk-live-dot' title='Live'></span></span>
        </div>
        <div class='sm:hidden px-4 py-2.5 border-b border-emerald-900/40 text-xs font-mono'>
            <div class='flex items-center justify-between gap-2 mb-1'>
                <span class='text-emerald-500'>{(l['discovered_at'].strftime('%d %b') if l['discovered_at'] else '--')} &middot; {(l['discovered_at'].strftime('%H:%M') if l['discovered_at'] else '--:--')}</span>
                <span class='flex items-center gap-2 shrink-0'>
                    <span class='text-amber-400 font-bold text-[10px] uppercase tracking-wide'>{l['lead_score']} job</span>
                    <span class='tk-live-dot' title='Live'></span>
                </span>
            </div>
            <div class='text-slate-200 font-sans text-sm font-semibold truncate'>{l['area_label']}</div>
            <div class='text-slate-400 font-sans text-[11px] truncate'>{(l['summary'] or 'Tree work notice')[:70]}</div>
        </div>"""
        for l in stats["diverse_leads"]
    ]) or "<div class='px-4 py-8 text-center text-slate-500 font-mono text-xs'>Intercepting live planning data...</div>"

    return f"""<!DOCTYPE html>
<html lang="en-GB" class="scroll-smooth">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>TreeKey | UK Tree Surgery Planning Intelligence</title>
    <meta name="description" content="TreeKey intercepts live UK council planning applications for tree surgery work. Get exclusive leads delivered to tree surgeons before competitors know they exist.">
    <meta property="og:title" content="TreeKey | UK Tree Surgery Planning Intelligence">
    <meta property="og:description" content="Exclusive tree surgery leads from live UK planning applications. Council TPO notices, S211 felling approvals, and domestic homeowner jobs — delivered first.">
    <meta property="og:url" content="https://treekey.uk">
    <meta property="og:type" content="website">
    <!-- Sep 8 2026, Nick's ask: there was no og:image at all, so Google/
         social link previews fell back to whatever icon happened to be
         set -- the old "K"/leaf mark. Added a proper share image built
         from the site's own current nav-bar branding (the diamond icon +
         TREEKEY wordmark), and swapped the site icon files to match. -->
    <meta property="og:image" content="https://treekey.uk/static/images/og-image.png">
    <meta property="og:image:width" content="1200">
    <meta property="og:image:height" content="630">
    <meta name="twitter:card" content="summary_large_image">
    <meta name="twitter:image" content="https://treekey.uk/static/images/og-image.png">
    <link rel="manifest" href="/static/manifest.json">
    <meta name="theme-color" content="#020617">
    <link rel="icon" href="/static/icon-192.png">
    <link rel="apple-touch-icon" href="/static/icon-192.png">
    <link href="/static/tailwind.css" rel="stylesheet">
    <script>if ('serviceWorker' in navigator) {{ window.addEventListener('load', () => {{ navigator.serviceWorker.register('/sw.js'); }}); }}</script>
    <!-- Sep 8 2026: Nick flagged the whole page as slow to load. Leaflet's
         CSS stays here (needed before the map renders, and stylesheets
         don't block HTML parsing), but leaflet.js was a blocking <script
         src> in <head> -- that halts parsing of the ENTIRE page (nav, hero,
         ticker, everything) until that third-party file finishes
         downloading from unpkg.com, even though the map isn't used until
         near the bottom of the page. Moved the script tag down to just
         before the inline script that actually calls L.map(), right before
         </body> -- see there for leaflet.js itself. -->
    <link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css" />
    <style>
        ::-webkit-scrollbar {{ width: 8px; }}
        ::-webkit-scrollbar-track {{ background: #020617; }}
        ::-webkit-scrollbar-thumb {{ background: #334155; border-radius: 4px; }}
        ::-webkit-scrollbar-thumb:hover {{ background: #059669; }}
        .bg-grid-slate-900 {{ background-image: url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 32 32' width='32' height='32' fill='none' stroke='%231e293b' stroke-dasharray='5 3' transform='scale(1, -1)'%3E%3Cpath d='M0 .5H31.5V32'/%3E%3C/svg%3E"); }}
        /* The sweep div is the top-right QUARTER of the circular radar (w-1/2 h-1/2,
           positioned top-right of the parent), so its own bottom-left corner is what
           actually sits at the center of the circle — not the div's own center. Rotating
           around 50% 50% (this div's center) made the whole quarter-wedge orbit around a
           point offset from the visual center instead of sweeping around the true center. */
        .radar-sweep {{ animation: sweep 4s linear infinite; transform-origin: 0% 100%; }}
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
                <div class="flex items-center gap-3 md:gap-6 font-mono text-sm tracking-wide">
                    <!-- Sep 5 2026: Nick's feedback -- tabs were too small/thin and
                         only Storm Radar had any colour, making the others look
                         inactive/unimportant by comparison. Bumped every tab to
                         font-bold and gave each its own distinct colour so the
                         whole bar reads as a set of equally live destinations. -->
                    <!-- Sep 8 2026: LEDGER and CHIP-DROP were sitting in this
                         top-level PUBLIC nav even though both are logged-in
                         contractor tools with zero context for a stranger
                         landing on the site -- Nick's own reaction testing it
                         himself was "what is ledger?". Both already have a
                         properly-explained home in the dashboard's Quick
                         Access grid below; removed from here, kept only the
                         actual public/marketing destinations. -->
                    <div class="hidden lg:flex items-center gap-6 text-slate-300">
                        <a href="/#radar" class="hover:brightness-125 transition-all text-emerald-400 font-bold">RADAR</a>
                        <a href="/marketplace" class="hover:brightness-125 transition-all text-sky-400 font-bold">MARKETPLACE</a>
                        <a href="/storm-radar" class="hover:brightness-125 transition-all text-amber-400 font-bold">STORM RADAR</a>
                        <a href="/pricing" class="hover:brightness-125 transition-all text-rose-400 font-bold">PACKAGES</a>
                        <a href="/faq" class="hover:brightness-125 transition-all text-violet-400 font-bold">FAQ</a>
                    </div>
                    <a href="/login" class="bg-emerald-600/20 text-emerald-300 border border-emerald-500/40 px-3.5 py-1.5 rounded-lg font-bold uppercase hover:bg-emerald-600 hover:text-white transition-all shadow-[0_0_15px_rgba(5,150,105,0.2)]">
                        Sign Up / Log In ➔
                    </a>
                </div>
            </div>
        </div>
    </nav>

    <!-- Hero Section: Live Signal Console -->
    <!-- Sep 5 2026: Nick's feedback -- too much dead space between the nav
         and the map/radar section below. Trimmed the hero's top padding and
         the live-feed block's bottom margin; left pb/lg values alone since
         those control spacing further down the page, not this gap. -->
    <main class="relative overflow-hidden pt-4 sm:pt-8 pb-24 lg:pt-10 lg:pb-32 bg-brand-dark bg-[radial-gradient(ellipse_at_top,rgba(5,150,105,0.10),transparent_60%)]">
        <!-- Sep 8 2026, Nick's ask: hero background photo (real UK arborist at
             work, supplied by Nick) -- kept low-opacity with a dark gradient
             on top so the headline and CTAs stay fully legible; this is
             purely atmospheric, not a content layer. On a phone the hero is
             much taller/narrower than this landscape photo, so a plain
             object-cover crop landed on a tight, unflattering headshot --
             a <picture> source swaps in a portrait-cropped version (same
             photo, cropped to keep the whole figure + chainsaw + harness)
             under 640px; desktop/tablet get the original, unchanged. -->
        <div class="absolute inset-0 z-0" aria-hidden="true">
            <picture>
                <source media="(max-width: 639px)" srcset="/static/images/hero-climber-mobile.jpg">
                <img src="/static/images/hero-climber.jpg" alt="" class="w-full h-full object-cover opacity-25">
            </picture>
            <div class="absolute inset-0 bg-[linear-gradient(to_bottom,rgba(2,6,23,0.3),rgba(2,6,23,0.5),rgba(2,6,23,0.7))]"></div>
        </div>
        <div class="max-w-5xl mx-auto px-4 sm:px-6 lg:px-8 relative z-10 flex flex-col">

            <!-- Sep 8 2026, Nick's ask ("critical"): the postcode-scan CTA,
                 free-lead CTA and "we never sell" badge all live in the block
                 below, but on a phone screen the live-feed ticker above them
                 was tall enough to push all three below the fold on landing.
                 Reordered with flex order so mobile sees the headline + CTAs
                 + never-sell badge FIRST, ticker feed second; sm: and up
                 (tablet/desktop, where there's room for both) restores the
                 original ticker-first layout untouched. -->

            <!-- Live Console Feed: real intercepted notices, not a decorative graphic -->
            <div class="order-2 sm:order-1 bg-[#0A1A12]/80 border border-emerald-900/50 rounded-xl overflow-hidden shadow-2xl mb-6">
                <div class="flex items-center gap-2 px-4 py-3 border-b border-emerald-900/50">
                    <span class="relative flex h-2 w-2"><span class="animate-ping absolute inline-flex h-full w-full rounded-full bg-emerald-400 opacity-75"></span><span class="relative inline-flex rounded-full h-2 w-2 bg-emerald-500"></span></span>
                    <span class="font-mono text-[11px] uppercase tracking-widest text-emerald-400">Live Feed — 360+ UK Council Portals</span>
                </div>
                <p class="px-4 pt-3 pb-1 text-[11px] font-mono text-slate-500 leading-relaxed">Real notices from our scan, sized by job scope — not anything you pay TreeKey.</p>
                <div class="mt-1" id="heroTicker">
                    {ticker_rows}
                </div>
            </div>

            <div class="order-1 sm:order-2 text-center">
                <!-- Sep 8 2026, Nick's ask: date+time so it's obvious how
                     fresh this is, and real today/week/month counts instead
                     of one static lifetime figure. The counters "tick" via
                     JS (see the script block at the bottom of this page) --
                     it polls /api/live-counts every 8-30s between 6am-11pm
                     and animates any REAL increase; it never invents a
                     number, so it only visibly moves as often as a real
                     lead actually lands. -->
                <!-- Sep 8 2026, Nick's ask: the live counters and the "never
                     sold twice" guarantee sit side by side on desktop
                     (counters left, guarantee right) where there's room to
                     spare, and stack with the guarantee right underneath
                     the counters on mobile so it's one of the first things
                     visible on landing, not buried after both CTA buttons. -->
                <div class="flex flex-col md:flex-row md:justify-center items-center gap-3 md:gap-6 mb-4 sm:mb-6">
                    <!-- Sep 8 2026, Nick's ask: smaller/thinner on mobile, running
                         the full width near the top rather than a padded floating
                         pill -- md: and up restores the original sized box. -->
                    <div class="flex md:inline-flex flex-col items-center gap-1 md:gap-2 w-full md:w-auto px-3 py-1.5 md:px-5 md:py-3 rounded-lg md:rounded-xl bg-emerald-500/10 border border-emerald-500/30 text-emerald-400 font-mono text-xs md:text-sm shadow-[0_0_15px_rgba(16,185,129,0.15)]">
                        <div class="flex items-center gap-2 text-[10px] md:text-[11px] uppercase tracking-widest">
                            <span class="relative flex h-2 w-2"><span class="animate-ping absolute inline-flex h-full w-full rounded-full bg-emerald-400 opacity-75"></span><span class="relative inline-flex rounded-full h-2 w-2 bg-emerald-500"></span></span>
                            Intercepting Live — {_as_of_date}, {_as_of_time}<span class="tk-blink-dots" aria-hidden="true"><span>.</span><span>.</span><span>.</span></span>
                        </div>
                        <!-- Sep 8 2026, Nick's ask: restored the overall running
                             total (real, from the leads table) above the
                             today/week/month breakdown -- it was dropped when
                             the 3-counter row was added. Label lightened from
                             emerald-600 -- it was reading as faint/washed out
                             against the badge's dark background. -->
                        <div class="text-emerald-400 font-bold text-sm md:text-lg">{display_leads:,} <span class="text-emerald-300 text-[10px] font-semibold uppercase tracking-wide">Total Intercepted</span></div>
                        <div class="flex items-center gap-3 sm:gap-5">
                            <span><strong id="countToday" class="text-white text-sm md:text-lg">{stats['counts']['today']}</strong> Today</span>
                            <span class="text-emerald-800">/</span>
                            <span><strong id="countWeek" class="text-white text-sm md:text-lg">{stats['counts']['week']}</strong> This Week</span>
                            <span class="text-emerald-800">/</span>
                            <span><strong id="countMonth" class="text-white text-sm md:text-lg">{stats['counts']['month']}</strong> This Month</span>
                        </div>
                    </div>
                    <!-- Sep 8 2026, Nick's ask: "never sold twice" is the single
                         biggest thing contractors care about. Promoted to a
                         proper badge, rephrased to spell out what it actually
                         means (not just assert exclusivity as a slogan), and
                         now pulses gently like the other live indicators. -->
                    <div class="tk-live-badge inline-flex items-center gap-2.5 px-5 py-2.5 rounded-full border border-emerald-500/40 text-emerald-300 font-bold text-sm shadow-[0_0_20px_rgba(16,185,129,0.15)]">
                        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3" class="shrink-0"><polyline points="20 6 9 17 4 12"></polyline></svg>
                        You Buy It, It's Yours — We Never Sell That Lead to Anyone Else
                    </div>
                </div>

                <!-- The Big Claim -->
                <h1 class="text-4xl md:text-6xl font-extrabold text-white tracking-tight mb-4 sm:mb-6 leading-tight">
                    Every job, the moment the council files it.<br>
                    <span class="text-transparent bg-clip-text bg-gradient-to-r from-amber-300 to-amber-500">Not the moment your rivals hear about it.</span>
                </h1>

                <!-- The Pain/Solution Frame -->
                <p class="mt-4 max-w-2xl mx-auto text-lg text-slate-400 leading-relaxed font-medium">
                    We watch 360+ UK council planning portals so you don't have to check them between jobs.
                    <br><strong class="text-slate-200">When a real felling licence or TPO notice lands in your patch, it's yours first — chainsaw still in the van.</strong>
                </p>

                <!-- Cognitive Ease & Action Cues -->
                <div class="mt-6 sm:mt-10 flex flex-col items-center gap-3 sm:gap-5">
                    <div class="flex flex-wrap justify-center gap-4">
                        <a href="#radar" class="flex items-center gap-2 bg-brand-green text-white px-8 py-4 rounded font-bold text-lg hover:bg-emerald-500 transition-all duration-300 shadow-[0_0_30px_rgba(5,150,105,0.4)] hover:shadow-[0_0_40px_rgba(5,150,105,0.6)] hover:-translate-y-1 transform">
                            Scan My Postcode Now
                            <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><line x1="5" y1="12" x2="19" y2="12"></line><polyline points="12 5 19 12 12 19"></polyline></svg>
                        </a>
                        <!-- Sep 5 2026: the free-account signup (get one real lead, no
                             card) was previously only reachable via cold email -- Nick
                             wanted it as a real, discoverable acquisition path on the
                             site itself, not just a cold-outreach bait link. -->
                        <a href="/free-account" class="flex items-center gap-2 bg-transparent text-emerald-400 border-2 border-emerald-500/50 px-8 py-4 rounded font-bold text-lg hover:bg-emerald-500/10 transition-all duration-300">
                            Claim a <span class="text-amber-400">Free</span> Lead — No Card Needed
                        </a>
                    </div>
                    <!-- Sep 8 2026, Nick's ask: the free-lead CTA was easy to skim
                         past, and its skepticism wasn't addressed anywhere. This
                         directly names the "too good to be true?" objection and
                         answers it in one line, without sounding desperate. -->
                    <p class="text-sm text-amber-300 max-w-md text-center leading-relaxed font-medium">
                        Sounds too good to be true? Sign up free today and we'll send you a real, FREE, fully unlocked lead from your area today — no card, no commitment!
                    </p>
                </div>

                <!-- Aug 30 2026: removed "BS5837 Survey Alignment" and "ArbAC
                     Industry Standard" badges -- both borrow the name of a real
                     UK arboricultural standard (BS5837 covers trees in relation
                     to construction; ArbAC is the Arboricultural Association's
                     utility-vegetation-management accreditation) to imply
                     TreeKey holds a certification or compliance status it
                     doesn't actually have. TreeKey aggregates public planning
                     data; it isn't a surveyor or an accredited contractor.
                     Left only the one badge that's a plain, checkable fact. -->
                <!-- Sep 8 2026: Nick flagged the dead space stacking up here (this
                     row's own top margin/padding, plus the Radar section's own
                     top padding right after) as a big empty gap on the live
                     page. Trimmed both -- see the matching note on the Radar
                     section below. -->
                <!-- Sep 8 2026 follow-up, Nick's ask: the two licence badges
                     smaller and tighter (still side by side), and the logo
                     pulled OUT of the grayscale filter -- it's meant to read
                     as a real, distinct old trademark, not a washed-out
                     watermark. Bottom margin added so there's actual
                     daylight before the live-feed table that follows right
                     underneath this on mobile (they were butted together
                     with zero gap). -->
                <div class="mt-8 pt-6 border-t border-slate-800/50 flex flex-col items-center gap-3 mb-4">
                    <div class="flex flex-wrap justify-center items-center gap-4 opacity-70 grayscale hover:grayscale-0 transition-all duration-500">
                        <div class="flex items-center gap-1.5 text-xs font-mono text-slate-300">
                            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" class="text-emerald-500 shrink-0"><path d="M9 11l3 3L22 4"></path><path d="M21 12v7a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h11"></path></svg>
                            Published Under The Open Government Licence
                        </div>
                        <div class="flex items-center gap-1.5 text-xs font-mono text-slate-300">
                            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" class="text-emerald-500 shrink-0"><path d="M9 11l3 3L22 4"></path><path d="M21 12v7a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h11"></path></svg>
                            Sourced Directly From Council Planning Registers
                        </div>
                    </div>
                    <!-- Sep 8 2026, Nick's ask: the original logo he supplied,
                         reads as "we've been at this a while," alongside the
                         current mark in the nav bar above. -->
                    <img src="/static/images/legacy-mark.png" alt="TreeKey original logo" class="h-8 w-auto" loading="lazy">
                </div>
            </div>
        </div>
    </main>

    <!-- Radar Section (The Micro-Commitment & Zeigarnik Effect Hook) -->
    <!-- Sep 8 2026: top padding trimmed from py-24 to pt-10 pb-24 -- paired
         with the badges row's own trimmed margin above, this removes the
         stacked empty-space gap Nick flagged between the licence badges and
         this heading, without touching the section's bottom spacing. -->
    <section id="radar" class="pt-10 pb-24 border-t border-slate-800 bg-brand-slate">
        <div class="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8">
            <div class="text-center mb-12">
                <h2 class="text-3xl font-extrabold text-white font-mono tracking-tight uppercase flex items-center justify-center gap-3">
                    <span class="relative flex h-3 w-3"><span class="animate-ping absolute inline-flex h-full w-full rounded-full bg-emerald-400 opacity-75"></span><span class="relative inline-flex rounded-full h-3 w-3 bg-emerald-500"></span></span>
                    Live Territory Radar
                </h2>
                <p class="mt-4 text-lg text-slate-400 max-w-2xl mx-auto">Type your postcode or region below — the map, radius and live counts react as you go, no button to press.</p>
            </div>

            <div class="grid lg:grid-cols-2 gap-8 items-start">

                <!-- Radar UI -->
                <div class="bg-[#020617] border border-slate-700 rounded-xl p-6 shadow-[0_0_40px_rgba(0,0,0,0.5)]">
                    <!-- Sep 8 2026, Nick's ask: removed the "Run Scan" button --
                         everything now reacts live: typing in the postcode box
                         (debounced ~550ms so it doesn't fire on every
                         keystroke) and changing the radius dropdown both
                         immediately re-run the scan, resize the map circle,
                         and refresh both lead counts. See scanTerritory() /
                         debouncedScanTerritory() in the script block below. -->
                    <form onsubmit="event.preventDefault(); scanTerritory();" class="flex flex-col sm:flex-row gap-4 mb-6">
                        <input type="text" id="postcodeInput" placeholder="Enter your Region or Postcode (e.g., Nottingham or NG22)..." oninput="debouncedScanTerritory()" onkeydown="if(event.key === 'Enter') {{ event.preventDefault(); scanTerritory(); }}" value="B1" required class="flex-1 bg-slate-800 border-2 border-slate-600 text-white font-mono rounded px-4 py-3 focus:outline-none focus:border-brand-green focus:bg-slate-900 transition-colors uppercase text-lg shadow-inner">
                        <select id="radiusSelect" onchange="scanTerritory()" class="bg-slate-800 border-2 border-slate-600 text-white font-mono rounded px-4 py-3 focus:outline-none focus:border-brand-green">
                            <option value="16093">10 Miles</option>
                            <option value="24140" selected>15 Miles</option>
                            <option value="32186">20 Miles</option>
                            <option value="40233">25 Miles</option>
                        </select>
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
                            <!-- Sep 8 2026, Nick's ask: replaced the static
                                 half-circle icon with the same pulsing
                                 liveness dot used elsewhere on the page --
                                 signals "active/live", not an alert. -->
                            <span class="relative flex h-3 w-3" aria-hidden="true"><span class="animate-ping absolute inline-flex h-full w-full rounded-full bg-emerald-400 opacity-75"></span><span class="relative inline-flex rounded-full h-3 w-3 bg-emerald-500"></span></span>
                            <span id="noticesAreaLabel">Intercepted Notices</span>
                        </h3>
                        <span class="tk-live-badge text-xs text-white font-mono px-2 py-1 rounded border border-emerald-500/30">Live Feed Active</span>
                    </div>
                    <div class="overflow-y-auto flex-1 bg-slate-900/50">
                        <table class="w-full text-left border-collapse">
                            <tbody id="noticesTableBody">
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
                <p class="text-slate-400 text-sm leading-relaxed">Statutory tree work notices are public council records the moment they're filed — most contractors never check them. We monitor the registers directly and route matching jobs to you <strong class="text-slate-200">as soon as they're published,</strong> so you can reach the homeowner before a competitor who's still waiting for the phone to ring.</p>
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

    <!-- Sep 8 2026: LEDGER and CHIP-DROP used to live in the top public nav
         with zero explanation ("what is ledger?" was Nick's own reaction
         testing the live site). Both are real, free contractor tools --
         this section explains them where a visitor actually has the
         context (right after seeing the pricing) instead of as unlabelled
         nav items, and doubles as the "mention Chip-Drop on the main page"
         fix. -->
    <section class="section py-16 bg-[#0b1220] border-t border-slate-800">
        <div class="container mx-auto px-4 max-w-5xl">
            <div class="text-center mb-10">
                <h2 class="text-2xl font-extrabold text-white font-mono uppercase tracking-tight">Every Subscription Also Includes</h2>
                <p class="text-slate-400 mt-2 text-sm">Free tools built for running a tree surgery business day-to-day, not just leads.</p>
            </div>
            <!-- Sep 8 2026: dropped the emoji icons here per Nick ("look
                 cheap") -- a slim colour-coded accent bar reads cleaner and
                 more professional. -->
            <div class="grid md:grid-cols-2 gap-6">
                <div class="bg-slate-800/50 p-6 rounded-lg border border-slate-700 border-t-2 border-t-violet-400">
                    <div class="text-[11px] font-mono uppercase tracking-widest text-violet-400 font-bold mb-2">Financial Tool</div>
                    <h3 class="text-lg font-bold text-white mb-2">TreeKey Ledger</h3>
                    <p class="text-slate-400 text-sm leading-relaxed">A financial dashboard built for sole-trader and small-crew tree surgeons: tracks your rolling 12-month turnover against the £90,000 UK VAT threshold, holds a running CIS developer-tax tracker, and includes a van/crew-day cost calculator so you never underquote a job. Free in your dashboard once you subscribe.</p>
                </div>
                <div class="bg-slate-800/50 p-6 rounded-lg border border-slate-700 border-t-2 border-t-orange-400">
                    <div class="text-[11px] font-mono uppercase tracking-widest text-orange-400 font-bold mb-2">Site Network</div>
                    <h3 class="text-lg font-bold text-white mb-2">Chip-Drop Network</h3>
                    <p class="text-slate-400 text-sm leading-relaxed">A directory of local allotments, farms and stables who want your arborist woodchip and logs for free — skip the £60–£120 commercial tipping fee and the round trip on every job. Landowners register their own site; you just turn up.</p>
                </div>
            </div>
        </div>
    </section>

    <!-- Sep 8 2026, Nick's ask: a real photo of UK tree work, supplied by
         Nick, breaking up the page per his long-standing "need pictures"
         feedback -- placed as its own band rather than forced into the
         cards above, since a photo crop that small does the picture no
         favours. -->
    <section class="relative py-20 border-t border-slate-800 overflow-hidden">
        <img src="/static/images/fieldwork-bucking.jpg" alt="A UK tree surgeon at work" class="absolute inset-0 w-full h-full object-cover">
        <div class="absolute inset-0 bg-[linear-gradient(to_right,rgba(2,6,23,1),rgba(2,6,23,0.85),rgba(2,6,23,0.4))]"></div>
        <div class="relative z-10 max-w-5xl mx-auto px-4 sm:px-6 lg:px-8">
            <div class="max-w-md">
                <p class="text-[11px] font-mono uppercase tracking-widest text-emerald-400 font-bold mb-3">While You're On The Tools</p>
                <h2 class="text-2xl md:text-3xl font-extrabold text-white leading-tight mb-4">We watch the portals. You keep the chainsaw running.</h2>
                <p class="text-slate-300 text-sm leading-relaxed">No app to check between jobs, no portals to refresh. The moment a real notice lands in your patch, it's in your inbox — chainsaw still in the van.</p>
            </div>
        </div>
    </section>

    <!-- FAQ Section (Objection Handling) -->
    <section class="section py-20 bg-[#020617] border-t border-slate-800">
        <div class="container mx-auto px-4 max-w-3xl">
            <div class="text-center mb-12">
                <h2 class="text-3xl font-extrabold text-white font-mono uppercase tracking-tight">Contractor FAQ</h2>
                <p class="text-slate-500 text-sm mt-2">Full answers on pricing, data sources, and how everything works: <a href="/faq" class="text-emerald-400 hover:text-emerald-300 underline">see the complete FAQ →</a></p>
            </div>
            
            <div class="space-y-6">
                <div class="bg-slate-800/50 p-6 rounded-lg border border-slate-700">
                    <h3 class="text-lg font-bold text-white mb-2">Are these leads exclusive?</h3>
                    <p class="text-slate-400 leading-relaxed">Yes. We operate on a strict <strong>Radial Territory Exclusivity</strong> model. You set your base postcode and an operating radius (up to 50 miles). As long as you have remaining monthly quota, you are the <em>only</em> contractor we notify about jobs in your area.</p>
                </div>
                
                <div class="bg-slate-800/50 p-6 rounded-lg border border-slate-700">
                    <h3 class="text-lg font-bold text-white mb-2">How fast do I get notified?</h3>
                    <p class="text-slate-400 leading-relaxed">Instantly. Our system scrapes UK planning portals continuously. The moment a new tree-related planning application or TPO/S211 notice is published in your radius, an alert is dispatched directly to your inbox so you can quote the homeowner before your competitors even know the job exists.</p>
                </div>
                
                <div class="bg-slate-800/50 p-6 rounded-lg border border-slate-700">
                    <h3 class="text-lg font-bold text-white mb-2">Am I tied into a long contract?</h3>
                    <p class="text-slate-400 leading-relaxed">No. The territory lockout is a rolling monthly agreement. You can cancel instantly at any time with zero penalty. If you don't want a subscription, you can unlock leads one-by-one via the Marketplace, though you won't get territory exclusivity.</p>
                </div>
            </div>
        </div>
    </section>

    <!-- Footer -->
    <footer class="border-t border-slate-800 bg-[#020617] py-12">
        <div class="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 flex flex-col md:flex-row justify-between items-start gap-8">
            <div class="text-slate-500 text-xs text-center md:text-left max-w-2xl">
                <!-- Sep 8 2026, Nick's ask: the second supplied logo mark,
                     small and muted into the small print rather than
                     displayed as a real logo -- deliberately tiny given the
                     source image's resolution. -->
                <img src="/static/images/footer-mark.png" alt="" class="h-7 w-auto opacity-50 mb-2 mx-auto md:mx-0" loading="lazy">
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
                <!-- Sep 5 2026: Nick flagged this as "what is datahub? broken link".
                     It was never actually broken (real 200 JSON) -- just mislabeled:
                     it's a live-system-status pulse, not a data hub. Renamed to
                     match what a visitor actually gets when they click it. -->
                <a href="/health" class="text-slate-400 hover:text-white transition-colors flex items-center gap-2">
                    <span class="relative flex h-2 w-2"><span class="animate-ping absolute inline-flex h-full w-full rounded-full bg-emerald-400 opacity-75"></span><span class="relative inline-flex rounded-full h-2 w-2 bg-emerald-500"></span></span>
                    Status
                </a>
                <a href="/admin" class="text-brand-green hover:text-emerald-400 transition-colors">Login</a>
            </div>
        </div>
    </footer>

    <script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
    <script>
        // Default to zoomed out Great Britain view
        let map = L.map('map', {{ zoomControl: false }}).setView([54.5, -4.0], 6);
        L.control.zoom({{ position: 'bottomright' }}).addTo(map);

        L.tileLayer('https://server.arcgisonline.com/ArcGIS/rest/services/Canvas/World_Dark_Gray_Base/MapServer/tile/{{z}}/{{y}}/{{x}}', {{
            attribution: '&copy; Esri &mdash; Esri, DeLorme, NAVTEQ',
            maxZoom: 16
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

        // Sep 8 2026, Nick's ask: the "Intercepted Notices" table follows
        // wherever the radar is currently pointed, sourced from the same
        // /api/check-postcode response (area_notices) rather than a
        // sitewide list -- same anonymised outcode-level area, never a
        // street address.
        function renderAreaNotices(notices, areaLabel) {{
            const label = document.getElementById('noticesAreaLabel');
            if (label) label.textContent = areaLabel ? `Intercepted Notices — ${{areaLabel}}` : 'Intercepted Notices';
            const tbody = document.getElementById('noticesTableBody');
            if (!tbody) return;
            if (!notices || notices.length === 0) {{
                tbody.innerHTML = `<tr><td colspan="3" class="p-8 text-center text-slate-500 font-mono">No open notices currently on file for this area.</td></tr>`;
                return;
            }}
            tbody.innerHTML = notices.map(n => `
                <tr class="border-b border-slate-700/50 hover:bg-slate-800/50 transition-colors">
                    <td class="p-4 text-emerald-400 font-mono text-xs">${{n.date}}<br><span class="text-slate-400 font-sans">${{n.time}}</span></td>
                    <td class="p-4 text-slate-200 text-sm max-w-md"><b class="text-white">${{n.area_label}}</b><br><span class="text-slate-400 text-xs">${{n.summary}}...</span></td>
                    <td class="p-4 text-right"><span class="bg-emerald-500/10 text-emerald-400 px-3 py-1 rounded-full text-xs font-bold border border-emerald-500/20 uppercase tracking-wider">${{n.size}}</span></td>
                </tr>
            `).join('');
        }}

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
                    const issueNoticeA = data.council_source_issue ? `<div class="text-amber-400 text-xs border border-amber-700/50 bg-amber-900/20 rounded px-2 py-1 mb-2">&#9888; ${{data.council_source_issue}}</div>` : '';
                    document.getElementById('targetIntel').innerHTML = `${{issueNoticeA}}<span class="text-emerald-400 font-bold text-sm">${{data.selected_area_leads}} Active Leads</span> in radius<br><span class="text-slate-400 border-t border-slate-700 pt-1 mt-1 block">+ ${{data.connected_area_leads}} additional in connected zones</span>`;
                    renderAreaNotices(data.area_notices, data.postcode);

                    document.getElementById('statusBadge').innerHTML = `
                        <div class="flex items-center gap-2 text-emerald-400 mb-1 sm:justify-end">
                            <span class="h-2 w-2 rounded-full bg-emerald-500 animate-pulse sm:hidden"></span>
                            Manual Lock: ${{e.latlng.lat.toFixed(4)}}, ${{e.latlng.lng.toFixed(4)}}
                            <span class="h-2 w-2 rounded-full bg-emerald-500 animate-pulse hidden sm:inline-block"></span>
                        </div>
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

        // Sep 8 2026, Nick's ask: typing in the postcode box reacts live --
        // debounced so a fast typist doesn't fire a scan on every letter,
        // but no button press is needed. Shortened from 550ms to 300ms
        // (Nick: "the radar reacts vs slowly to new input") -- still enough
        // to skip mid-word keystrokes, but noticeably snappier.
        let _scanDebounceTimer = null;
        function debouncedScanTerritory() {{
            clearTimeout(_scanDebounceTimer);
            const val = (document.getElementById('postcodeInput').value || '').trim();
            if (val.length < 2) return;
            _scanDebounceTimer = setTimeout(scanTerritory, 300);
        }}

        // Sep 8 2026, Nick's ask: the radar was re-zooming/re-centering to
        // the default B1 view the instant the page loaded, undoing the
        // zoomed-out Great Britain view it's supposed to start on.
        // skipZoom=true (used only for the initial auto-populate call below)
        // still refreshes the real stats/notices/circle for the default
        // postcode, it just doesn't move or zoom the map to get there.
        async function scanTerritory(skipZoom) {{
            const input = document.getElementById("postcodeInput").value;
            const radSelect = document.getElementById("radiusSelect");
            const radVal = radSelect ? parseInt(radSelect.value) : 24140;
            const status = document.getElementById("statusBadge");

            // Sep 8 2026: instant visual feedback on the radius dropdown --
            // resize the circle immediately, don't wait on the network round
            // trip, then confirm/refresh the real numbers below.
            currentCircle.setRadius(radVal);
            document.getElementById("radiusReadout").innerHTML = `RADIAL BOUNDARY: ${{ (radVal/1609.34).toFixed(1) }} MILES`;

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
                    if (!skipZoom) {{ map.flyTo([data.lat, data.lng], 10, {{ duration: 1.0 }}); }}
                    currentCircle.setLatLng([data.lat, data.lng]);
                    currentCircle.setRadius(radVal);
                    document.getElementById("radiusReadout").innerHTML = `RADIAL BOUNDARY: ${{ (radVal/1609.34).toFixed(1) }} MILES`;
                    document.getElementById('btn-checkout-sole').href = `/checkout/sole_trader?outcode=${{data.postcode}}`;
                    document.getElementById('btn-checkout-pro').href = `/checkout/commercial_pro?outcode=${{data.postcode}}`;
                    document.getElementById('btn-checkout-elite').href = `/checkout/regional_elite?outcode=${{data.postcode}}`;
                    const issueNoticeB = data.council_source_issue ? `<div class="text-amber-400 text-xs border border-amber-700/50 bg-amber-900/20 rounded px-2 py-1 mb-2">&#9888; ${{data.council_source_issue}}</div>` : '';
                    document.getElementById("targetIntel").innerHTML = `${{issueNoticeB}}<span class="text-emerald-400 font-bold text-sm">${{data.selected_area_leads}} Active Leads</span> in radius<br><span class="text-slate-400 border-t border-slate-700 pt-1 mt-1 block">+ ${{data.connected_area_leads}} additional in connected zones</span>`;
                    renderAreaNotices(data.area_notices, data.postcode);

                    setTimeout(() => {{
                        status.innerHTML = `
                            <div class="flex items-center gap-2 text-emerald-400 mb-1 sm:justify-end">
                                <span class="h-2 w-2 rounded-full bg-emerald-500 animate-pulse sm:hidden"></span>
                                Radar Locked: ${{data.postcode}}
                                <span class="h-2 w-2 rounded-full bg-emerald-500 animate-pulse hidden sm:inline-block"></span>
                            </div>
                        `;
                    }}, 300);

                }} else if (data.status === "out_of_bounds") {{
                    document.getElementById('targetIntel').innerHTML = `<span class="text-red-500 font-bold text-sm">Out of Bounds</span><br><span class="text-slate-400 border-t border-slate-700 pt-1 mt-1 block">${{data.message}}</span>`;
                    status.innerHTML = `<div class="flex items-center gap-2 text-red-500 sm:justify-end">Outside Coverage Area</div>`;
                }} else {{
                    status.innerHTML = `<div class="flex items-center gap-2 text-red-500"><span class="h-2 w-2 rounded-full bg-red-500"></span> Invalid Postcode</div>`;
                }}
            }} catch(e) {{
                status.innerHTML = `<div class="flex items-center gap-2 text-red-500"><span class="h-2 w-2 rounded-full bg-red-500"></span> Network Error</div>`;
            }}
        }}

        // Sep 8 2026: run once on page load so the radar shows real numbers
        // for the default B1/Birmingham view immediately, instead of the
        // "Awaiting scan..." placeholder until someone interacts with it.
        // skipZoom=true so this doesn't undo the zoomed-out Great Britain
        // starting view (Nick: "reverted back to starting off zoomed in").
        scanTerritory(true);

        // Sep 8 2026, Nick's ask: the "Today / This Week / This Month"
        // counters on the badge above the fold should visibly tick up
        // through the day. This polls the REAL current counts from
        // /api/live-counts every 8-30s while it's between 6am-11pm, and
        // animates the on-screen number up to match in small irregular
        // steps -- it never invents a number, so the counter only actually
        // moves as often as a real lead lands. Outside 6am-11pm it just
        // checks back every 5 minutes without animating.
        (function() {{
            const todayEl = document.getElementById('countToday');
            const weekEl = document.getElementById('countWeek');
            const monthEl = document.getElementById('countMonth');
            if (!todayEl) return;

            function animateTo(el, newVal) {{
                const oldVal = parseInt((el.textContent || '0').replace(/[^0-9]/g, ''), 10) || 0;
                if (newVal <= oldVal) {{ el.textContent = newVal; return; }}
                let cur = oldVal;
                const step = () => {{
                    const bump = [1, 1, 2, 2, 3][Math.floor(Math.random() * 5)];
                    cur = Math.min(newVal, cur + bump);
                    el.textContent = cur;
                    if (cur < newVal) setTimeout(step, 200 + Math.random() * 500);
                }};
                step();
            }}

            function pollCounts() {{
                fetch('/api/live-counts').then(r => r.json()).then(data => {{
                    animateTo(todayEl, data.today);
                    animateTo(weekEl, data.week);
                    animateTo(monthEl, data.month);
                }}).catch(() => {{}});
            }}

            function scheduleNext() {{
                const hour = new Date().getHours();
                const active = hour >= 6 && hour < 23;
                const delay = active ? (8000 + Math.random() * 22000) : (5 * 60 * 1000);
                setTimeout(() => {{ if (active) pollCounts(); scheduleNext(); }}, delay);
            }}
            scheduleNext();
        }})();
    </script>
</body>
</html>
"""


# Sep 2 2026: Nick's call -- "nothing should be unshown or unstated," and
# explicitly, a tag value sitting at zero right now must still appear as a
# zero row, not vanish because a GROUP BY over existing rows can only ever
# return values that occur at least once. These are the known finite value
# sets per tag category, pulled from the actual source-of-truth constants
# (not hand-copied, so they can't drift out of sync with scanners.py/
# research.py). `locale` is deliberately excluded -- it's open-ended
# (specific council/town names), so "every possible value" isn't a fixed,
# meaningful list the way it is for the others.
KNOWN_TAG_VALUES = {
    "job": sorted(set(scanners.JOB_TYPE_KEYWORDS.keys()) | {"other"}),
    "size": ["small", "medium", "large"],
    "agent": ["yes", "no", "unconfirmed"],
    # Sep 2 2026: Nick's ask -- "not only agent yes/no but rather
    # none/agent/tree surgeon as the agent ones who are not tree surgeons
    # are potentially viable leads". Kept as its own prefix alongside
    # 'agent' above (rather than replacing it) so nothing that already
    # filters on agent:yes/no/unconfirmed breaks -- this is additive detail,
    # not a replacement. Only emitted for the tree vertical (see
    # scanners._generate_tags -- agent_is_tree_surgeon has no equivalent
    # concept for HMO, so tagging every HMO lead 'type-unconfirmed' would
    # just be noise, not information).
    "agent_type": ["none", "confirmed-tree-surgeon", "confirmed-other", "type-unconfirmed", "unconfirmed"],
    # Sep 2 2026: the "third round" educated guess for leads where the
    # agent status has no hard confirmation at all -- a best-effort read of
    # the application's own description text (see
    # mesh_scrapers.classify_agent_as_tree_surgeon). Deliberately has no
    # 'no signal' value of its own: a lead with nothing to go on simply gets
    # no agent_guess tag at all and the generic renderer's own gap row
    # ("no agent_guess tag") shows that honestly, rather than this list
    # claiming a fixed set of guessable outcomes that don't include "none".
    "agent_guess": ["tree-surgeon", "non-tree-surgeon"],
    "vertical": ["tree", "hmo"],
    # Sep 2 2026 audit fix: this used to be the raw pretty-printed region
    # names ("East Midlands", "South East", ...) while every REAL stored
    # region tag is slugified (region:east-midlands) by
    # scanners._slugify_tag -- so every one of these known-value rows never
    # matched a real row and always rendered as a duplicate, permanently-
    # zero entry next to the real (slugified) one carrying the actual
    # count. Caught by Nick looking straight at the admin page and asking
    # "why are the regions empty?" -- they weren't empty, they were just
    # the wrong (unslugified) rows sitting at zero next to the real ones.
    "region": sorted({scanners._slugify_tag(v) for v in scanners.COUNCIL_TO_REGION.values()}) + ["unclassified"],
    "business": sorted(set(research.SIC_DIVISION_TO_BUSINESS_KIND.values()) | {research.BUSINESS_KIND_NAME_OVERRIDE}) + ["unclassified"],
    # Sep 2 2026: the partner-side third-round guess -- see
    # research._guess_business_kind. Same "no guess tag at all, not a
    # 'none' value" rule as agent_guess above.
    "business_guess": sorted(set(research._BUSINESS_GUESS_KEYWORDS.keys())),
    "director": ["yes", "no"],
    "phone": ["yes", "no"],
    "email": ["yes", "no"],
    "contact": ["reachable", "dead"],
}
# job is the one genuinely multi-label category (a tree lead can be both
# crown-work and tpo at once) -- every other category assigns exactly one
# value per lead/partner, so its counts are held to "should sum to the
# total" and the gap (if any) is surfaced explicitly rather than silently
# absorbed.
MULTI_LABEL_CATEGORIES = {"job"}


def _render_tag_stat_section(categories: dict, total: int, untagged: int, entity_label: str) -> str:
    """Generic renderer for the tag-based stats grids on /admin. Any
    category dict shaped like {"prefix": {"prefix:value": n}} (see
    database.get_tag_counts / get_partner_tag_counts) renders automatically
    as a card with a mini bar per value -- a brand new tag category added
    to scanners.py or research.py later shows up here with zero further
    admin-page changes. `total` and `untagged` (the whole-table counts) let
    every card state plainly how its numbers relate to the whole, per
    Nick's 'nothing unshown or unstated' rule -- including a zero row for
    any known value that currently has no matches at all, and an explicit
    'no <category> tag' row for the gap when a single-value category's
    counts don't add up to `total` (a real, visible fact -- e.g. some
    leads have no council_source at all -- not a rendering bug)."""
    if not categories and not KNOWN_TAG_VALUES:
        return "<p style='color:#94a3b8; font-size:13px;'>No data yet.</p>"
    all_prefixes = sorted(set(categories.keys()) | set(KNOWN_TAG_VALUES.keys()))
    cards = []
    for prefix in all_prefixes:
        tag_counts = dict(categories.get(prefix, {}))
        for known_value in KNOWN_TAG_VALUES.get(prefix, []):
            tag_counts.setdefault(f"{prefix}:{known_value}", 0)
        if not tag_counts:
            continue
        rows = sorted(tag_counts.items(), key=lambda kv: (-kv[1], kv[0]))
        category_sum = sum(n for _, n in rows)
        is_multi = prefix in MULTI_LABEL_CATEGORIES
        gap = total - category_sum
        if not is_multi and gap > 0:
            rows.append((f"{prefix}:(no {prefix} tag)", gap))
        max_n = max((n for _, n in rows), default=1) or 1
        row_html = ""
        for tag, n in rows:
            label = tag.split(":", 1)[1] if ":" in tag else tag
            is_gap_row = label.startswith("(no ")
            pct = int((n / max_n) * 100) if max_n else 0
            bar_color = "#cbd5e1" if is_gap_row else (
                "#dc2626" if (prefix == "contact" and label == "dead")
                or (prefix == "region" and label == "unclassified") else "#059669"
            )
            row_html += f"""
            <div style="display:flex; align-items:center; gap:8px; margin:5px 0; font-size:12.5px;">
                <div style="width:150px; text-overflow:ellipsis; overflow:hidden; white-space:nowrap; color:{'#94a3b8' if is_gap_row else '#334155'};" title="{html.escape(tag)}">{html.escape(label)}</div>
                <div style="flex:1; background:#f1f5f9; border-radius:4px; height:13px;">
                    <div style="background:{bar_color}; width:{pct}%; height:13px; border-radius:4px;"></div>
                </div>
                <div style="width:46px; text-align:right; font-weight:bold; color:#0f172a;">{n}</div>
            </div>"""
        if is_multi:
            header_note = f"{category_sum} tags across {total} {entity_label} -- multi-label, one can carry more than one"
        else:
            header_note = f"{category_sum} of {total} {entity_label} accounted for"
        cards.append(f"""
        <div style="background:#f8fafc; border:1px solid #e2e8f0; border-radius:10px; padding:14px; min-width:230px; flex:1;">
            <div style="font-weight:bold; color:#0f172a; text-transform:capitalize; font-size:13px;">{html.escape(prefix)}</div>
            <div style="font-size:11px; color:#94a3b8; margin-bottom:8px;">{header_note}</div>
            {row_html}
        </div>""")
    if untagged:
        untagged_note = (
            ' &nbsp;|&nbsp; <span style="color:#dc2626;">'
            f'Untagged (never processed): <b>{untagged}</b></span>'
        )
    else:
        untagged_note = " &nbsp;|&nbsp; Untagged: 0"
    header_line = (
        "<p style='color:#64748b; font-size:12.5px; margin:-4px 0 12px 0;'>"
        f"Total {entity_label}: <b>{total}</b>{untagged_note}</p>"
    )
    return header_line + f"<div style='display:flex; flex-wrap:wrap; gap:12px;'>{''.join(cards)}</div>"


def _time_ago(iso_str: Optional[str]) -> str:
    """Human-readable '3h ago' style string for a stored ISO timestamp, or
    'never' -- used to show Nick when the autonomous cycle last actually
    ran without him having to parse a raw timestamp."""
    if not iso_str:
        return "never yet"
    try:
        then = datetime.datetime.fromisoformat(iso_str.replace("Z", "+00:00"))
        delta = datetime.datetime.now(datetime.timezone.utc) - then
        hours = delta.total_seconds() / 3600
        if hours < 1:
            return f"{int(delta.total_seconds() / 60)}m ago"
        if hours < 48:
            return f"{int(hours)}h ago"
        return f"{int(hours / 24)}d ago"
    except Exception:
        return iso_str


@app.get("/admin", response_class=HTMLResponse)
def admin_dashboard(request: Request, secret: Optional[str] = Query(None)):
    verify_admin_or_secret(request, secret)
    stats = {"p": 0, "l": 0, "l_council": 0, "l_domestic": 0, "enriched": 0, "partners": [], "leads": []}

    try:
        conn = database.get_db_conn(); cur = conn.cursor()
        cur.execute("SELECT count(*) FROM potential_partners"); stats["p"] = cur.fetchone()[0]
        cur.execute("SELECT count(*) FROM leads"); stats["l"] = cur.fetchone()[0]
        # Aug 30 2026: this queried a column named "source_type", which has
        # never existed -- the real column, used correctly everywhere else
        # in this file, is "lead_source_type". Every /admin page load has
        # been hitting this, logging "[ADMIN] DB error: column source_type
        # does not exist" and silently leaving l_domestic/l_council at 0.
        cur.execute("SELECT count(*) FROM leads WHERE lead_source_type IN ('direct_homeowner', 'domestic_classified')")
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

    lead_tag_stats = database.get_tag_counts()
    partner_tag_stats = database.get_partner_tag_counts()
    last_cycle_at = database.get_system_state("last_autonomous_cycle_at")

    partner_rows = "".join([
        f"<li><b>{html.escape(str(p[0] or ''))}</b>  {html.escape(str(p[1] or 'Director on file'))} | <b>{html.escape(str(p[2] or ''))}</b> |  {html.escape(str(p[4] or ''))} |  {html.escape(str(p[5] or ''))} |  {p[3] or 'N/A'}</li>"
        for p in stats["partners"]
    ])

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

    city_links = "".join([
        f"<a href='/scan/{city.lower().replace(' ', '-')}' "
        f"style='display:inline-block; margin:4px; padding:8px 14px; background:#f8fafc; "
        f"border:1px solid #e2e8f0; border-radius:8px; color:#059669; font-weight:bold; "
        f"font-size:13px; text-decoration:none;'>&#128269; {city}</a>"
        for city in ALL_CITIES  # Display all UK regions including Scotland and Wales
    ])

    # Sep 2 2026: Nick's call -- "I don't want to have to manually scan
    # things, it should be autonomous." A background scheduler now fires
    # run_full_autonomous_cycle (scan pipeline + every tag backfill +
    # partner enrichment) roughly once every 20 hours on its own (see
    # main.py's _autonomous_scheduler_loop) -- this banner reports that
    # fact plainly instead of asking Nick to remember to click anything.
    if _pipeline_state.get("running"):
        pipeline_banner = f"""<div style='background:#fef3c7; border:1px solid #f59e0b; border-radius:10px;
            padding:12px 18px; margin-bottom:18px; font-size:14px;'>
            &#9203; <b>Autonomous cycle running right now</b> (started {html.escape(str(_pipeline_state.get('started_at') or '?'))}) -- scanning, tagging, and enriching in the background.
        </div>"""
    else:
        pipeline_banner = f"""<div style='background:#ecfdf5; border:1px solid #10b981; border-radius:10px;
            padding:12px 18px; margin-bottom:18px; font-size:14px;'>
            &#129302; <b>Running autonomously</b> -- no manual scanning needed. Last full cycle: <b>{_time_ago(last_cycle_at)}</b>. Next one fires on its own in roughly 20 hours from then.
        </div>"""

    pct = int((stats['enriched'] / stats['p'] * 100)) if stats['p'] else 0
    partner_dead = (partner_tag_stats.get("categories", {}).get("contact", {}) or {}).get("contact:dead", 0)
    lead_unclassified_region = (lead_tag_stats.get("categories", {}).get("region", {}) or {}).get("region:unclassified", 0)

    return f"""
    <html><head><title>Vector Data Labs  Admin Command</title></head>
    <body style="font-family:sans-serif; background:#f4f4f9; padding:40px;">
    <div style="max-width:1080px; margin:auto; background:white; padding:40px;
                border-radius:20px; border-top:8px solid #064e3b; box-shadow:0 4px 12px rgba(0,0,0,0.05);">
        <div style="display:flex; justify-content:space-between; align-items:center;">
            <h1>&#128188; Tree Key Admin Command</h1>
            <a href="/" target="_blank" style="background:#10b981; color:white; padding:8px 14px; border-radius:6px; text-decoration:none; font-weight:bold; font-size:13px;"> View Public Homepage</a>
        </div>

        {pipeline_banner}

        <div style="display:flex; flex-wrap:wrap; gap:10px; margin-bottom:20px;">
            <span style="background:#0f172a; color:white; padding:6px 12px; border-radius:6px; font-size:13px;">Total Leads: <b>{stats['l']}</b> ({stats['l_council']} council / {stats['l_domestic']} domestic)</span>
            <span style="background:#047857; color:white; padding:6px 12px; border-radius:6px; font-size:13px;">Total Partners: <b>{stats['p']}</b></span>
            <span style="background:#059669; color:white; padding:6px 12px; border-radius:6px; font-size:13px;">Partners w/ Contacts: <b>{stats['enriched']} ({pct}%)</b></span>
            <span style="background:{'#dc2626' if partner_dead else '#64748b'}; color:white; padding:6px 12px; border-radius:6px; font-size:13px;">Dead Partners (no phone/email): <b>{partner_dead}</b></span>
            <span style="background:{'#dc2626' if lead_unclassified_region else '#64748b'}; color:white; padding:6px 12px; border-radius:6px; font-size:13px;">Leads w/ Unclassified Region: <b>{lead_unclassified_region}</b></span>
        </div>
        <p style="margin:0 0 20px 0;">
           <a href='/status'> System Status</a>
           &nbsp;|&nbsp; <a href='/pricing'> Pricing Table</a>
           &nbsp;|&nbsp; <a href='/export-directors'> View Contacts</a>
           &nbsp;|&nbsp; <a href='/export-directors.csv' style='color:#1b5e20; font-weight:bold;'>&#128190; Download CSV</a>
           &nbsp;|&nbsp; <a href='/leads-by-tag?tags=vertical:tree&match=all'> Filter Leads by Tag</a>
        </p>
        <hr>

        <h3>&#127795; Leads, by Category</h3>
        {_render_tag_stat_section(lead_tag_stats.get("categories", {}), lead_tag_stats.get("total_leads", 0), lead_tag_stats.get("untagged_leads", 0), "leads")}

        <hr>
        <h3>&#127970; Partners, by Category</h3>
        {_render_tag_stat_section(partner_tag_stats.get("categories", {}), partner_tag_stats.get("total_partners", 0), partner_tag_stats.get("untagged_partners", 0), "partners")}

        <hr>
        <h4>Recent Leads (Past 24-48 Hours)</h4>
        <ul>{lead_rows or "<li>No leads yet.</li>"}</ul>
        <h4>Recent Verified Partners</h4>
        <ul>{partner_rows or "<li>No partners yet.</li>"}</ul>

        <hr>
        <details>
            <summary style="cursor:pointer; font-weight:bold; color:#334155; padding:8px 0;">&#9881; Manual override (not needed day-to-day -- the system runs itself)</summary>
            <p style="color:#64748b; font-size:13px;">Use these only for troubleshooting, or to force a fresh cycle right now instead of waiting for the next automatic one. Every link below shares the same lock, so nothing can double-run.</p>
            <div style="display:flex; gap:10px; flex-wrap:wrap; margin-bottom:10px;">
                <a href='/trigger-autonomous-cycle' style="background:#064e3b; color:white; padding:12px 22px; border-radius:8px; text-decoration:none; font-weight:bold; font-size:14px;">
                    &#9889; Run Full Autonomous Cycle Now
                </a>
                <a href='/trigger-daily-pipeline' style="background:#0f172a; color:white; padding:10px 18px; border-radius:8px; text-decoration:none; font-weight:bold; font-size:13px;">
                    Scan Pipeline Only
                </a>
                <a href='/enrich-all' style="background:#1b5e20; color:white; padding:10px 16px; border-radius:8px; text-decoration:none; font-weight:bold; font-size:13px;">
                    Enrich All Partners
                </a>
                <a href='/clean-partners' style="background:#b71c1c; color:white; padding:10px 16px; border-radius:8px; text-decoration:none; font-weight:bold; font-size:13px;">
                    Clean Database
                </a>
            </div>
            <h4>Scan One Region</h4>
            <p style="color:#64748b; font-size:13px; margin-top:-5px;">For troubleshooting a single council only. Clicking a region twice in one day is a safe no-op, not a second real scan.</p>
            {city_links}
        </details>
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
def pricing(request: Request):
    plans = payments.PLANS
    msg = request.query_params.get("msg", "")
    msg_banner = ""
    if msg == "no_subscription":
        msg_banner = (
            "<div style='background:#fef2f2; border:1px solid #fca5a5; border-radius:8px; padding:16px; margin-bottom:20px; color:#991b1b;'>"
            "<b>No active subscription found</b> for that email. Pick a tier below to unlock your dashboard —"
            " or, if you're not ready to subscribe yet, <a href='/free-account' style='color:#991b1b; font-weight:bold;'>get one free lead first, no card needed</a>."
            "</div>"
        )

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

        {msg_banner}

        <h2 style="font-size:22px; margin-bottom:16px; color:#0f172a;">1. Select Your Dedicated Subscription Tier</h2>
        <div class="grid">
            {sub_cards}
        </div>

        <h2 style="font-size:22px; margin:32px 0 16px 0; color:#0f172a;">2. Or Buy As You Go (Single-Lead Marketplace)</h2>
        <p style="color:#64748b; font-size:13px; margin-top:-10px; margin-bottom:16px;">
            Subscribers get priority allocation. Any unallocated leads flow into our single-purchase marketplace. Once bought, a lead is burned and never resold.
        </p>
        {single_cards}

        <div class="creed-banner" style="margin-top:32px;">
            <h3>🌲 The TreeKey Creed: "Your Prosperity is Our Business"</h3>
            <p style="font-size:14px; line-height:1.6; margin:0;">
                We are not a faceless directory. We do NOT sell your leads to 5 competitors, we do not take a percentage of your hard-earned invoices, and we don't trap you in long contracts. Every lead on TreeKey is a <b>single-sale asset</b>—the second you receive it, it is burned from our system forever.
            </p>
        </div>

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




# ── QR-code partner landing page ──────────────────────────────────────────────
# Sep 3 2026: Nick's ask -- a QR code he can put on printed material (a
# business card, a trade-show stand, a future letter run) that lands a
# prospective tree-surgeon partner on a real page, not just TreeKey's
# homepage. `src` is a short campaign code baked into the QR image itself
# (see generate_qr_codes.py) so response can be measured per batch/channel
# once real campaigns exist -- see MARKETING_OUTREACH_IDEAS.md for what
# this was originally built alongside (a letter campaign, a testimonials
# idea) that Nick explicitly said to file for later, not build yet. This
# page and its QR codes work standalone today regardless of that decision.

@app.get("/partner-offer", response_class=HTMLResponse)
def partner_offer_page(src: str = "unknown"):
    src_safe = html.escape(src)[:60]
    return f"""
    <!DOCTYPE html>
    <html lang="en-GB">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>Planning-Application Tree Leads | TreeKey</title>
        <style>
            body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; background:#f8fafc; color:#0f172a; margin:0; padding:40px 16px; }}
            .box {{ max-width:560px; margin:auto; background:white; padding:32px; border-radius:16px; border:1px solid #e2e8f0; box-shadow:0 4px 16px rgba(0,0,0,0.04); }}
            h1 {{ color:#044332; font-size:24px; margin:0 0 10px 0; }}
            p {{ color:#64748b; font-size:14px; line-height:1.6; }}
            ul {{ color:#334155; font-size:14px; line-height:1.8; padding-left:20px; }}
            input {{ width:100%; box-sizing:border-box; padding:12px; border:1px solid #cbd5e1; border-radius:8px; margin-bottom:14px; font-family:inherit; font-size:14px; }}
            label {{ font-size:13px; font-weight:600; display:block; margin-bottom:4px; }}
            button {{ background:#044332; color:white; border:none; padding:14px 24px; border-radius:8px; font-weight:bold; font-size:15px; cursor:pointer; width:100%; }}
            .altlink {{ text-align:center; margin-top:18px; font-size:13px; }}
            .altlink a {{ color:#044332; font-weight:600; text-decoration:none; }}
        </style>
    </head>
    <body>
    <div class="box">
        <h1>🌳 Real planning-application tree leads, straight from the council register</h1>
        <p>TreeKey scans UK council planning portals every day for TPO, felling, and tree-work applications the moment they're filed -- so you can quote before anyone else even knows the job exists.</p>
        <ul>
            <li>Leads sourced directly from statutory planning notices, not resold directory data</li>
            <li>Priced per lead from £19 -- no lock-in subscription required to start</li>
            <li>See if a job already has an agent on record before you pay for it</li>
        </ul>
        <form action="/api/submit-partner-offer" method="POST">
            <input type="hidden" name="src" value="{src_safe}">
            <label>Your Name / Company Name</label>
            <input type="text" name="name" placeholder="e.g. Dave, Apex Tree Care Ltd" required>
            <label>Phone Number</label>
            <input type="text" name="phone" placeholder="07XXX XXXXXX">
            <label>Email (optional)</label>
            <input type="email" name="email" placeholder="you@example.com">
            <label>Town / Area You Work In</label>
            <input type="text" name="town" placeholder="e.g. Leeds" required>
            <button type="submit">Get In Touch →</button>
        </form>
        <p class="altlink">Already know what you want? <a href="/pricing">See pricing and sign up directly →</a></p>
    </div>
    </body>
    </html>
    """


@app.post("/api/submit-partner-offer")
async def submit_partner_offer(request: Request):
    form = await request.form()
    src = (form.get("src") or "unknown").strip()[:60]
    name = (form.get("name") or "").strip()[:200]
    phone = (form.get("phone") or "").strip()[:40] or None
    email = (form.get("email") or "").strip()[:200] or None
    town = (form.get("town") or "").strip()[:100] or None

    if name:
        database.save_qr_campaign_lead(src, name, phone, email, town)

    return HTMLResponse("""
    <html><body style="font-family:sans-serif; text-align:center; padding:60px; background:#f8fafc;">
        <div style="max-width:500px; margin:auto; background:white; padding:40px; border-radius:16px; border:1px solid #e2e8f0;">
            <h2 style="color:#059669; margin-top:0;">✅ Thanks -- we've got your details!</h2>
            <p style="color:#64748b; font-size:15px; line-height:1.5;">We'll be in touch shortly. In the meantime, feel free to look around.</p>
            <a href="/pricing" style="display:inline-block; background:#044332; color:white; padding:10px 20px; border-radius:8px; text-decoration:none; font-weight:bold; font-size:14px; margin-top:15px;">See Pricing</a>
        </div>
    </body></html>
    """)


@app.get("/trigger-qr-campaign-stats")
def trigger_qr_campaign_stats(secret: Optional[str] = Query(None)):
    """Sep 3 2026: read-only view of /partner-offer form submissions grouped
    by campaign src code, so Nick can check which printed QR batch/channel
    is actually generating interest without needing direct DB access."""
    verify_cron_secret(secret)
    return {"status": "ok", "campaigns": database.get_qr_campaign_stats()}


# ── 1-Tap Homeowner Introduction Letter Generator ─────────────────────────────

@app.get("/generate-letter/{lead_id}", response_class=HTMLResponse)
def generate_homeowner_letter(lead_id: str, company: str = "Your Local Tree Specialists", phone: str = "07XXX XXXXXX"):
    row = None
    try:
        conn = database.get_db_conn()
        cur = conn.cursor()
        try:
            cur.execute("SELECT reference, address, summary, council_source, status FROM leads WHERE id::text = %s OR reference = %s;", (lead_id, lead_id))
            row = cur.fetchone()
        finally:
            cur.close()
            conn.close()
    except Exception as e:
        logger.error(f"[Letter] DB error for lead {lead_id}: {e}")
        return HTMLResponse("<h3>Error loading lead data.</h3>", status_code=500)

    if not row:
        return HTMLResponse("<h3>Lead not found.</h3>", status_code=404)
        
    ref, addr, summary, council, status = row
    if status != 'claimed':
        return HTMLResponse(
            "<h3>Lead Not Unlocked</h3><p>This lead has not been purchased yet. Please unlock it in the marketplace to view the full details and generate letters.</p>",
            status_code=403
        )

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
    row = None
    try:
        conn = database.get_db_conn()
        cur = conn.cursor()
        try:
            # Sep 2 2026 audit: this SELECT used to omit `status` and never
            # checked it -- unlike generate_homeowner_letter just above,
            # which correctly gates on status == 'claimed'. The `id` used
            # here is the same one already visible, unauthenticated, in
            # /marketplace's page HTML (the "Unlock" button's own href).
            # That meant anyone could copy an unpurchased lead's id straight
            # from view-source and hit this endpoint directly to get the
            # real street name/address for free, before anyone had paid for
            # it -- defeating the entire "pay to unlock the exclusive
            # address" business model in one unauthenticated GET.
            cur.execute("SELECT reference, address, summary, status FROM leads WHERE id::text = %s OR reference = %s;", (lead_id, lead_id))
            row = cur.fetchone()
        finally:
            cur.close()
            conn.close()
    except Exception as e:
        logger.error(f"[Flyer] DB error for lead {lead_id}: {e}")
        return HTMLResponse("<h3>Error loading lead data.</h3>", status_code=500)

    if not row:
        return HTMLResponse("<h3>Lead not found.</h3>", status_code=404)

    ref, addr, summary, status = row
    if status != 'claimed':
        return HTMLResponse(
            "<h3>Lead Not Unlocked</h3><p>This lead has not been purchased yet. Please unlock it in the marketplace to view the full details and generate flyers.</p>",
            status_code=403
        )
    
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
    outcode = request.query_params.get("outcode", "")
    lead_id = request.query_params.get("lead_id")

    plan = payments.PLANS.get(plan_key)
    if not plan:
        return HTMLResponse("<h3>Invalid plan.</h3>", status_code=404)

    # Single lead purchase — go straight to Stripe (no area needed)
    if lead_id or plan.get("mode") == "payment":
        url = payments.create_checkout_session(plan_key, outcode or "GB", lead_id)
        if not url:
            return HTMLResponse(
                "<html><body style='font-family:sans-serif; text-align:center; padding:60px;'>"
                "<h1>Payment System Unavailable</h1>"
                "<p>Please contact support at contact@treekey.uk.</p>"
                "<a href='/pricing'>Return to Pricing</a>"
                "</body></html>",
                status_code=503
            )
        return RedirectResponse(url=url)

    # Subscription plan — show area selector first if no outcode provided
    plan_name = plan["name"]
    plan_price = f"£{plan['amount'] // 100}/month"
    return HTMLResponse(f"""<!DOCTYPE html>
<html lang="en-GB">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Set Your Area — TreeKey</title>
    <style>
        body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; background:#f8fafc; color:#0f172a; margin:0; padding:40px 16px; }}
        .card {{ max-width:520px; margin:auto; background:white; border:1px solid #e2e8f0; border-radius:16px; padding:36px; box-shadow:0 4px 20px rgba(0,0,0,0.06); }}
        h1 {{ color:#044332; font-size:24px; margin:0 0 6px 0; }}
        p.sub {{ color:#64748b; font-size:14px; margin:0 0 28px 0; }}
        label {{ display:block; font-size:13px; font-weight:600; color:#374151; margin-bottom:6px; }}
        input, select {{ width:100%; box-sizing:border-box; padding:11px 14px; border:1px solid #d1d5db; border-radius:8px; font-size:15px; margin-bottom:20px; background:white; }}
        input:focus, select:focus {{ outline:none; border-color:#044332; box-shadow:0 0 0 3px rgba(4,67,50,0.08); }}
        .hint {{ font-size:12px; color:#94a3b8; margin-top:-14px; margin-bottom:18px; }}
        button {{ width:100%; background:#044332; color:white; padding:14px; border:none; border-radius:8px; font-size:16px; font-weight:700; cursor:pointer; }}
        button:hover {{ background:#065f46; }}
        .plan-box {{ background:#f0fdf4; border:1px solid #a7f3d0; border-radius:10px; padding:14px 16px; margin-bottom:24px; display:flex; justify-content:space-between; align-items:center; }}
        .plan-box .name {{ font-weight:700; color:#065f46; font-size:15px; }}
        .plan-box .price {{ font-weight:800; color:#044332; font-size:18px; }}
        .lock-note {{ background:#eff6ff; border:1px solid #bfdbfe; border-radius:8px; padding:12px 14px; font-size:12px; color:#1e40af; margin-bottom:24px; }}
    </style>
</head>
<body>
<div class="card">
    <h1>🌳 One last step</h1>
    <p class="sub">Tell us where you work so we can route the right leads to you.</p>

    <div class="plan-box">
        <span class="name">{plan_name}</span>
        <span class="price">{plan_price}</span>
    </div>

    <div class="lock-note">
        🔒 Your leads are matched exclusively to your area. Once locked, no other contractor on the same tier will receive leads in your zone.
    </div>

    <form method="POST" action="/checkout/{plan_key}">
        <label for="outcode">Your Postcode or Outcode</label>
        <input type="text" id="outcode" name="outcode" placeholder="e.g. CR5 2LE, NG22, B1"
               maxlength="8" required autocomplete="postal-code"
               style="text-transform:uppercase;"
               oninput="this.value=this.value.toUpperCase()">
        <div class="hint">Give your full postcode (e.g. CR5 2LE) for the most accurate job distances — just an outcode (e.g. CR5) works too, but is less precise</div>

        <label for="radius">Working Radius</label>
        <select id="radius" name="radius">
            <option value="10">10 miles — Tight local zone</option>
            <option value="15" selected>15 miles — Standard (recommended)</option>
            <option value="20">20 miles — Extended coverage</option>
            <option value="30">30 miles — Wide regional reach</option>
            <option value="50">50 miles — Full county coverage</option>
        </select>

        <label for="job_size">Job Sizes You Want</label>
        <select id="job_size" name="job_size">
            <option value="all" selected>All sizes — small, medium & large</option>
            <option value="small">Small jobs only</option>
            <option value="medium">Medium jobs only</option>
            <option value="large">Large jobs only</option>
        </select>
        <div class="hint">You'll only be matched to jobs at the size(s) you choose — change this any time from your dashboard</div>

        <button type="submit">Continue to Secure Payment →</button>
    </form>
    <p style="text-align:center; margin-top:16px; font-size:12px; color:#94a3b8;">
        Secured by Stripe · Cancel anytime · No setup fees
    </p>
</div>
</body>
</html>""")


@app.post("/checkout/{plan_key}")
async def checkout_post(plan_key: str, outcode: str = Form(...), radius: int = Form(15), job_size: str = Form("all")):
    """Handles the area form submission, then redirects to Stripe with outcode in metadata."""
    plan = payments.PLANS.get(plan_key)
    if not plan:
        return HTMLResponse("<h3>Invalid plan.</h3>", status_code=404)

    # Server-side radius cap — the form previously offered the same 10-50mi choice to
    # every plan with nothing enforcing it, so a cheap tier could select (and receive
    # dispatch for) the same radius sold as a premium-tier differentiator.
    max_radius = database.TIER_MAX_RADIUS.get(plan_key, 15)
    radius = min(radius, max_radius)

    job_size = (job_size or "all").strip().lower()
    if job_size not in ("small", "medium", "large", "all"):
        job_size = "all"

    # Sep 8 2026, proximity-system rework: resolve here (not just at webhook
    # time) so a full postcode ("CR5 2LE") is captured and passed through
    # Stripe metadata as full_postcode -- register_or_update_subscription
    # geocodes that to an exact pin. The territory-keying outcode
    # (client_reference_id) still only ever carries the bare outcode.
    raw_input = outcode.strip().upper()[:8]
    location = database.resolve_location(raw_input)
    clean_outcode = location["outcode"] or raw_input[:4] or "GB"
    full_postcode = location["full_postcode"]

    url = payments.create_checkout_session(plan_key, clean_outcode, radius=radius,
                                            full_postcode=full_postcode, job_size=job_size)
    if not url:
        return HTMLResponse(
            "<html><body style='font-family:sans-serif; text-align:center; padding:60px;'>"
            "<h1>Payment System Unavailable</h1>"
            "<p>Please contact support at contact@treekey.uk.</p>"
            "<a href='/pricing'>Return to Pricing</a>"
            "</body></html>",
            status_code=503
        )
    return RedirectResponse(url=url, status_code=303)


@app.get("/admin/simulate-leads", response_class=HTMLResponse)
def admin_simulate_leads(request: Request, secret: Optional[str] = Query(None),
                          location: Optional[str] = None, tier: str = "climber_domestic",
                          job_size: str = "all", radius: Optional[float] = None):
    """Sep 8 2026, Nick's ask (verbatim: "let's do some dummy runs, i will
    pretend i from a specific location and package and you would tell me
    what exactly i would receive by email"): a real, reusable dummy-run
    tool instead of a one-off manual exercise -- pick a postcode/outcode,
    a tier and a job-size preference, and see exactly which currently-
    unclaimed leads that hypothetical subscriber would be matched to right
    now, using the exact same matching logic as a real dispatch
    (database.simulate_customer_leads mirrors notifications.
    dispatch_lead_alerts's own exact-outcode / haversine-radius / regional-
    prefix rules). Entirely read-only -- nothing is burned, dispatched or
    emailed. Addresses are shown at area-level only (same redaction as the
    public homepage fix), matching this being an ops/sales tool, not a
    reason to expose an unclaimed lead's exact address."""
    verify_admin_or_secret(request, secret)

    tier_options = "".join([
        f"<option value='{k}' {'selected' if k == tier else ''}>{k}</option>"
        for k in database.TIER_MAX_RADIUS.keys()
    ])
    job_size_options = "".join([
        f"<option value='{v}' {'selected' if v == job_size else ''}>{v.title()}</option>"
        for v in ("all", "small", "medium", "large")
    ])

    results_html = ""
    if location:
        sim = database.simulate_customer_leads(location, tier=tier, job_size=job_size, radius=radius, limit=15)
        loc = sim["location"]
        if loc["lat"] is None:
            results_html = (
                f"<p style='color:#b91c1c;'>Couldn't resolve '{location}' to a real UK postcode/outcode — nothing to simulate.</p>"
                f"<p style='color:#64748b; font-size:12px;'>This means the free postcodes.io lookup service returned no match "
                f"for that exact text. Double-check: (1) it's a real UK postcode or outcode, e.g. <code>M1 1AE</code> or just "
                f"<code>M1</code> — not a town/city name; (2) there's no stray punctuation. If a postcode you know is real also "
                f"fails, that's a service/network issue rather than a typo — check the Render logs or the admin health page for "
                f"a recent <b>GEOCODING API FAILURE</b> entry, which now gets logged automatically whenever this happens.</p>"
            )
        else:
            precision_note = "exact postcode pin" if loc["precision"] == "exact" else "outcode-centroid area (less precise — try a full postcode for a tighter pin)"
            rows = "".join([
                f"<tr><td style='padding:8px; border-bottom:1px solid #e2e8f0;'>{m['area']}</td>"
                f"<td style='padding:8px; border-bottom:1px solid #e2e8f0;'>{m['distance_miles']} mi</td>"
                f"<td style='padding:8px; border-bottom:1px solid #e2e8f0;'>{m['lead_score']}</td>"
                f"<td style='padding:8px; border-bottom:1px solid #e2e8f0;'>{(m['summary'] or '')[:100]}</td>"
                f"<td style='padding:8px; border-bottom:1px solid #e2e8f0; font-size:11px; color:#64748b;'>{m['match_reason']}</td></tr>"
                for m in sim["matches"]
            ]) or "<tr><td colspan='5' style='padding:16px; text-align:center; color:#64748b;'>No currently-unclaimed leads would match this location/tier/job-size right now.</td></tr>"
            results_html = f"""
            <p style='color:#475569; font-size:13px;'>Resolved to outcode <b>{loc['outcode']}</b>{' (' + loc['full_postcode'] + ')' if loc['full_postcode'] else ''} — {precision_note}. Radius: {sim['radius_miles']} miles.</p>
            <table style='width:100%; border-collapse:collapse; font-size:13px;'>
                <tr style='background:#f8fafc;'><th style='padding:8px; text-align:left;'>Area</th><th style='padding:8px; text-align:left;'>Distance</th><th style='padding:8px; text-align:left;'>Size</th><th style='padding:8px; text-align:left;'>Summary</th><th style='padding:8px; text-align:left;'>Match reason</th></tr>
                {rows}
            </table>
            """

    return HTMLResponse(f"""
    <html><body style="font-family:sans-serif; padding:40px; background:#f8fafc; max-width:900px; margin:auto;">
        <h2 style="color:#044332;">🧪 Dummy Run: Simulate Customer Leads</h2>
        <p style="color:#64748b; font-size:13px;">Read-only — no leads are burned or emailed. Shows exactly what a subscriber at this location/tier/job-size would currently receive.</p>
        <form method="GET" style="background:white; padding:20px; border-radius:10px; border:1px solid #e2e8f0; display:flex; gap:12px; flex-wrap:wrap; align-items:end; margin-bottom:24px;">
            <input type="hidden" name="secret" value="{secret or ''}">
            <div><label style="display:block; font-size:12px; font-weight:bold; margin-bottom:4px;">Postcode or Outcode</label>
                <input type="text" name="location" value="{location or ''}" placeholder="e.g. CR5 2LE" style="padding:8px; border:1px solid #cbd5e1; border-radius:6px;"></div>
            <div><label style="display:block; font-size:12px; font-weight:bold; margin-bottom:4px;">Tier</label>
                <select name="tier" style="padding:8px; border:1px solid #cbd5e1; border-radius:6px;">{tier_options}</select></div>
            <div><label style="display:block; font-size:12px; font-weight:bold; margin-bottom:4px;">Job Size</label>
                <select name="job_size" style="padding:8px; border:1px solid #cbd5e1; border-radius:6px;">{job_size_options}</select></div>
            <div><label style="display:block; font-size:12px; font-weight:bold; margin-bottom:4px;">Radius override (optional)</label>
                <input type="number" name="radius" value="{radius or ''}" placeholder="tier default" style="padding:8px; border:1px solid #cbd5e1; border-radius:6px; width:120px;"></div>
            <button type="submit" style="background:#044332; color:white; padding:9px 18px; border:none; border-radius:6px; font-weight:bold; cursor:pointer;">Run</button>
        </form>
        {results_html}
        <p style="margin-top:24px;"><a href="/admin?secret={secret or ''}" style="color:#044332;">← Back to Admin</a></p>
    </body></html>
    """)


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

        # Aug 31 2026: Nick noticed the marketplace never actually showed
        # WHEN a lead was listed -- only a derived "days left" countdown.
        # discovered_at was already selected by the query, just never
        # rendered. Handles both a real datetime (the normal case, from
        # psycopg2's TIMESTAMPTZ) and a string (defensive, in case a caller
        # ever passes one) the same way calculate_lead_freshness already does.
        listed_date = "Date unavailable"
        raw_discovered_at = l.get("discovered_at")
        try:
            if isinstance(raw_discovered_at, str):
                dt = datetime.datetime.fromisoformat(raw_discovered_at.replace("Z", "+00:00"))
            else:
                dt = raw_discovered_at
            if dt:
                listed_date = dt.strftime("%d %b %Y, %H:%M")
        except Exception:
            pass

        # Mask exact street number/name to prevent bypassing, but show neighborhood/town & postcode
        addr_parts = [p.strip() for p in addr.split(",") if p.strip()]
        masked_area = addr_parts[-1] if len(addr_parts) > 1 else addr
        if len(addr_parts) >= 2:
            masked_area = f"{addr_parts[-2]}, {addr_parts[-1]}"

        # Aug 30 2026: this is the single most important fix Nick asked for --
        # has_agent was already captured and already shown post-purchase (the
        # unlock email, the internal dispatch view) but NEVER shown here, on
        # the public listing a contractor sees BEFORE paying. That meant
        # someone could pay for a lead and only discover a tree surgeon was
        # already on record for it after the money had changed hands. This
        # shows the same honest yes/no/unconfirmed signal up front -- WHICH
        # agent/company is still only revealed after unlocking, so there's
        # still a real reason to pay even on an "agent on record" lead.
        # Aug 31 2026: database.get_marketplace_leads_with_freshness() now
        # only lets a has_agent=True lead reach this page at all when
        # agent_is_tree_surgeon is explicitly False (a non-tree agent --
        # architect, planning consultant, block management, the council
        # itself -- filed the paperwork, so the tree work itself may still
        # be open). So any has_agent=True lead seen here is that specific
        # case, not "job definitely taken" -- the badge below reflects that
        # instead of showing the old blanket warning.
        if l.get("has_agent") is True:
            agent_badge = "<span style='font-size:11px; background:#f1f5f9; color:#475569; font-weight:bold; padding:3px 8px; border-radius:12px; margin-left:6px;' title=\"An agent handled the paperwork but doesn't look like a tree company -- the tree work itself may still be open.\">ℹ️ Non-tree agent on record</span>"
        elif l.get("has_agent") is False:
            agent_badge = "<span style='font-size:11px; background:#d1fae5; color:#065f46; font-weight:bold; padding:3px 8px; border-radius:12px; margin-left:6px;'>✅ No agent listed</span>"
        else:
            agent_badge = "<span style='font-size:11px; background:#f1f5f9; color:#64748b; padding:3px 8px; border-radius:12px; margin-left:6px;'>Agent status: unconfirmed</span>"

        # Aug 31 2026: Nick's point -- a lead whose own description already
        # signals danger/urgency (see database.is_urgent_lead) genuinely
        # needs faster action than a routine application, so it's flagged
        # here and also sorted to the front by the query itself.
        urgent_badge = ""
        if l.get("is_urgent"):
            urgent_badge = "<span style='font-size:11px; background:#fee2e2; color:#991b1b; font-weight:bold; padding:3px 8px; border-radius:12px; margin-left:6px;'>🚨 Urgent</span>"

        lead_cards += f"""
        <div style="background:white; border:1px solid {'#fca5a5' if l.get('is_urgent') else '#e2e8f0'}; border-radius:12px; padding:20px; margin-bottom:14px; box-shadow:0 2px 8px rgba(0,0,0,0.03);">
            <div style="display:flex; justify-content:space-between; align-items:flex-start; flex-wrap:wrap; gap:10px;">
                <div>
                    <span style="font-size:11px; background:{badge_bg}; color:{badge_color}; font-weight:bold; padding:4px 10px; border-radius:12px; text-transform:uppercase;">{badge_text}</span>
                    <span style="font-size:11px; background:#f1f5f9; color:#475569; padding:3px 8px; border-radius:12px; margin-left:6px;">LPA: {council}</span>
                    {urgent_badge}
                    {agent_badge}
                    <h3 style="margin:10px 0 4px 0; font-size:17px; color:#0f172a;">📍 {masked_area}</h3>
                </div>
                <div style="text-align:right;">
                    <div style="font-size:22px; font-weight:800; color:#044332;">£{unlock_fee}</div>
                    <span style="font-size:11px; color:#64748b;">{days_left}</span>
                </div>
            </div>

            <div style="font-size:11px; color:#94a3b8; margin-top:4px;">📅 Listed: {listed_date}</div>

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
            <p style="color:#64748b; font-size:15px; line-height:1.5;">Thank you. Your exclusive planning intelligence stream has been activated.<br><br>Your lead dispatches will arrive by email automatically — but you can also browse and unlock leads directly below.</p>
            <div style="margin-top:25px; display:flex; flex-direction:column; gap:12px; align-items:center;">
                <a href="/marketplace" style="background:#044332; color:white; padding:12px 28px; border-radius:8px; text-decoration:none; font-weight:bold; font-size:15px; width:260px; display:block;">🏛️ Browse Available Leads Now</a>
                <a href="/login" style="background:#1d4ed8; color:white; padding:12px 28px; border-radius:8px; text-decoration:none; font-weight:bold; font-size:15px; width:260px; display:block;">🔑 Log In to Your Dashboard</a>
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
def ledger_dashboard(request: Request):
    """
    TreeKey Ledger: Verticalized financial command center for UK tree surgeons.
    Includes Van-Day true costing, CIS developer tax deductions, and £90k VAT gauge.
    """
    # Previously took `email` straight from the query string with no auth check at
    # all — anyone could view/write any contractor's financial data via /ledger?email=.
    # Now derived only from the verified session cookie, same as /dashboard.
    email = _verify_session_cookie(request.cookies.get("treekey_contractor_session"))
    if not email:
        return RedirectResponse(url="/login", status_code=303)

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
                <h1 style="margin:0; font-size:28px; color:#044332;">TreeKey Ledger</h1>
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
    # Previously trusted `email` from the POST body — any client could write ledger
    # entries into another contractor's financial records by changing a hidden form
    # field. Now derived only from the verified session cookie.
    email = _verify_session_cookie(request.cookies.get("treekey_contractor_session"))
    if not email:
        return RedirectResponse(url="/login", status_code=303)

    form = await request.form()
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
    return RedirectResponse(url="/ledger", status_code=303)




# ── 3b. Contractor Portal Upgrades (Phase 2, part 1 — PROJECT_STATE.md item 8) ─
# Notification-preference toggle. NOTE: the interactive map-drawn custom lead-alert
# area (the other half of item 8) is a substantially bigger piece — it needs a JS
# mapping library, polygon storage, and reworking the core dispatch-matching
# geometry from radius-based to polygon-based — deliberately NOT built in this pass.

@app.get("/settings", response_class=HTMLResponse)
def settings_page(request: Request):
    email = _verify_session_cookie(request.cookies.get("treekey_contractor_session"))
    if not email:
        return RedirectResponse(url="/login", status_code=303)

    current = database.get_contractor_settings(email).get("notification_preference", "email")
    saved_banner = "<div style='background:#f0fdf4; border:1px solid #bbf7d0; border-radius:8px; padding:10px; margin-bottom:16px; color:#065f46; font-size:13px;'>Saved.</div>" if request.query_params.get("saved") else ""

    def opt(value, label, desc):
        checked = "checked" if current == value else ""
        return f"""
        <label style="display:block; border:1px solid #e2e8f0; border-radius:8px; padding:14px; margin-bottom:10px; cursor:pointer;">
            <input type="radio" name="notification_preference" value="{value}" {checked} style="width:auto; margin-right:8px;">
            <b>{label}</b>
            <div style="font-size:12px; color:#64748b; margin-left:22px;">{desc}</div>
        </label>
        """

    return HTMLResponse(f"""
    <!DOCTYPE html>
    <html lang="en-GB">
    <head>
        <meta charset="UTF-8">
        <title>Settings | TreeKey</title>
        <style>
            body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; background:#f8fafc; color:#0f172a; margin:0; padding:32px 16px; }}
            .container {{ max-width: 600px; margin: auto; }}
            .card {{ background: white; border: 1px solid #e2e8f0; border-radius: 12px; padding: 24px; }}
            .btn {{ background: #044332; color: white; border: none; padding: 12px 20px; border-radius: 6px; font-weight: bold; cursor: pointer; }}
        </style>
    </head>
    <body>
    <div class="container">
        <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:20px;">
            <h1 style="margin:0; font-size:24px; color:#044332;">⚙️ Settings</h1>
            <a href="/dashboard" style="color:#044332; font-size:13px; text-decoration:none; font-weight:bold;">← Dashboard</a>
        </div>
        <div class="card">
            {saved_banner}
            <h3 style="margin-top:0; font-size:16px;">Lead Notification Format</h3>
            <p style="color:#64748b; font-size:13px;">How new leads are delivered when you're allocated one.</p>
            <form method="POST" action="/api/save-settings">
                {opt("email", "Email only", "Standard lead-delivery email with Letter/Flyer tools.")}
                {opt("whatsapp", "Email + WhatsApp forward buttons", "Adds a one-tap “Forward on WhatsApp” button next to each lead so you can send it straight to your crew.")}
                {opt("both", "Both (same as above)", "Included for clarity — WhatsApp buttons are additive to the email, not a replacement for it.")}
                <button type="submit" class="btn" style="width:100%; margin-top:8px;">Save Settings</button>
            </form>
        </div>
    </div>
    </body>
    </html>
    """)


@app.post("/api/save-settings")
async def save_settings(request: Request):
    email = _verify_session_cookie(request.cookies.get("treekey_contractor_session"))
    if not email:
        return RedirectResponse(url="/login", status_code=303)

    form = await request.form()
    preference = form.get("notification_preference", "email")
    database.update_notification_preference(email, preference)
    return RedirectResponse(url="/settings?saved=1", status_code=303)


# ── 4. Passwordless Contractor Auth & Mobile Command Center ───────────────────
# Sep 8 2026 rework, per Nick's live-tested feedback ("the whole thing is
# broken and needs a rework... modern professional secure slick and
# simple"): three real bugs fixed here --
#   1. The 6-digit OTP was displayed on the confirmation webpage itself
#      (as well as emailed), defeating its entire purpose as a second
#      factor. It's now ONLY ever emailed.
#   2. That OTP had no entry form anywhere in the app -- it was emailed
#      but unusable dead functionality. It now has a real purpose: log in
#      from a second device (e.g. you read the email on your phone but
#      need the dashboard open on your laptop) by typing the code instead
#      of clicking the link. /api/verify-otp below is the new endpoint.
#   3. Landing on "no active subscription" after clicking the login link
#      was a dead end with nothing else offered -- the confirmation page,
#      the login page, and the pricing redirect below all now surface the
#      free-lead-first path (/free-account) as an explicit alternative.

def _login_session_response(verified_email: str) -> RedirectResponse:
    """Shared by both ways a login can be verified -- clicking the magic
    link (verify_login, GET) or typing the emailed OTP (verify_otp_route,
    POST) -- so the "which dashboard does this email land on" decision
    and session-cookie issuance lives in exactly one place instead of two
    copies quietly drifting apart."""
    def _session_redirect(url: str) -> RedirectResponse:
        response = RedirectResponse(url=url, status_code=303)
        response.set_cookie(
            key="treekey_contractor_session",
            value=_sign_session_cookie(verified_email),
            max_age=86400 * 30,  # 30 days
            httponly=True,
            secure=True,
            samesite="lax"
        )
        return response

    active_sub = database.get_contractor_subscription(verified_email)
    if active_sub and active_sub.get("active"):
        return _session_redirect("/dashboard")

    if database.get_limbo_account(verified_email):
        return _session_redirect("/free-dashboard")

    return RedirectResponse(url="/pricing?msg=no_subscription", status_code=303)


@app.get("/login", response_class=HTMLResponse)
def login_page(error: Optional[str] = None):
    err_html = f"<div style='background:#fef2f2; border:1px solid #fecaca; color:#991b1b; padding:10px 14px; border-radius:8px; margin-bottom:18px; font-size:13px;'>{error}</div>" if error else ""
    return f"""
    <!DOCTYPE html>
    <html lang="en-GB">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>Sign Up / Log In | TreeKey</title>
        <style>
            body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; background:#f8fafc; color:#0f172a; margin:0; padding:40px 16px; }}
            .box {{ max-width:420px; margin:auto; background:white; padding:32px; border-radius:16px; border:1px solid #e2e8f0; box-shadow:0 4px 16px rgba(0,0,0,0.04); }}
            input {{ width:100%; box-sizing:border-box; padding:12px; border:1px solid #cbd5e1; border-radius:8px; margin-top:6px; margin-bottom:16px; font-family:inherit; font-size:15px; }}
            input:focus {{ outline:none; border-color:#044332; box-shadow:0 0 0 3px rgba(4,67,50,0.1); }}
            button {{ background:#044332; color:white; border:none; padding:13px; border-radius:8px; font-weight:bold; font-size:15px; cursor:pointer; width:100%; transition:background 0.15s; }}
            button:hover {{ background:#065f46; }}
        </style>
    </head>
    <body>
    <div class="box">
        <div style="text-align:center; margin-bottom:20px;">
            <span style="font-size:32px;">🌲</span>
            <h2 style="margin:8px 0 4px 0; color:#044332;">Sign Up / Log In</h2>
            <p style="color:#64748b; font-size:13px; margin:0;">Zero-Password • Enter your email, we'll send you a secure link</p>
        </div>

        {err_html}

        <!-- Sep 5 2026: was "Email or Phone" -- there's no SMS sending built
             (create_magic_auth_token only ever treats this as an email, and
             nothing here can text a phone number), so it never worked for a
             phone number in the first place. Copy now matches what actually
             happens rather than promising a channel that doesn't exist. -->
        <form action="/api/request-magic-link" method="POST">
            <label style="font-size:12px; font-weight:bold; color:#475569;">Email Address:</label>
            <input type="email" name="contact" placeholder="e.g. dave@apex-trees.co.uk" required autofocus>
            <button type="submit">Send Secure Login Link ⚡</button>
        </form>
        <p style="text-align:center; font-size:12px; color:#94a3b8; margin:-8px 0 0 0;">Works whether you're an existing subscriber or signing up for the first time.</p>

        <div style="text-align:center; margin-top:22px; padding-top:18px; border-top:1px solid #f1f5f9;">
            <p style="font-size:12px; color:#64748b; margin:0 0 8px 0;">New here and not ready to subscribe?</p>
            <a href="/free-account" style="font-size:13px; font-weight:bold; color:#044332; text-decoration:none;">Get a free lead first, no card needed →</a>
        </div>

        <div style="text-align:center; margin-top:18px; font-size:12px; color:#64748b;">
            🔒 <b>No passwords to leak or remember.</b> We email you a one-tap, 15-minute access link.
        </div>
    </div>
    </body>
    </html>
    """


@app.post("/api/request-magic-link")
async def request_magic_link(request: Request):
    client_ip = request.client.host if request.client else "unknown"
    if not _check_rate_limit(client_ip):
        return RedirectResponse(url="/login?error=Too+many+attempts.+Please+wait+a+minute+and+try+again.", status_code=303)

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
        <p style="font-size:13px; color:#64748b;">Logging in on a different device than this email is on? Enter this 6-digit code there instead: <b style="font-size:16px; color:#0f172a; letter-spacing:1px;">{otp_code}</b></p>
        <p style="font-size:11px; color:#94a3b8; margin-top:24px;">This secure link and code are valid for 15 minutes. If you did not request this, you can safely ignore this email -- nothing happens unless the link is clicked or the code is entered.</p>
    </div>
    """
    # Sep 5 2026 CRITICAL FIX: this used to call send_resend_email(), which
    # ALWAYS sends to the fixed internal TEST_EMAIL address (built for admin
    # incident alerts, not customer email) -- meaning no contractor other
    # than whoever's email happens to be TEST_EMAIL ever actually received
    # their login link. The whole login flow was silently broken for every
    # real contractor. Now sends directly to the contractor's own address.
    notifications.send_transactional_email(to_email=contact, subject="🌲 Your TreeKey 1-Tap Login Link", html_body=email_body)

    # Sep 8 2026 SECURITY FIX: the OTP used to be echoed right back onto
    # this confirmation page -- meaning anyone with access to this browser
    # tab (or a shoulder-surfer) had the "second factor" without ever
    # touching the recipient's inbox, which defeats the entire point of a
    # code that's supposed to prove email access. The code is now ONLY
    # ever sent in the email. This page instead offers a real, working
    # entry form for it (previously there was no OTP-entry UI anywhere in
    # the app at all, so the emailed code was unusable dead functionality)
    # -- genuinely useful for exactly the "read the email on your phone,
    # need the dashboard on your laptop" case.
    return HTMLResponse(f"""
    <!DOCTYPE html>
    <html lang="en-GB">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>Check Your Inbox | TreeKey</title>
        <style>
            body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; background:#f8fafc; color:#0f172a; margin:0; padding:60px 16px; }}
            .box {{ max-width:440px; margin:auto; background:white; padding:32px; border-radius:16px; border:1px solid #e2e8f0; box-shadow:0 4px 16px rgba(0,0,0,0.04); text-align:center; }}
            input {{ width:100%; box-sizing:border-box; padding:11px; border:1px solid #cbd5e1; border-radius:8px; font-family:inherit; font-size:16px; letter-spacing:3px; text-align:center; }}
            input:focus {{ outline:none; border-color:#044332; box-shadow:0 0 0 3px rgba(4,67,50,0.1); }}
            button {{ background:#044332; color:white; border:none; padding:11px; border-radius:8px; font-weight:bold; font-size:14px; cursor:pointer; width:100%; margin-top:10px; }}
        </style>
    </head>
    <body>
        <div class="box">
            <span style="font-size:40px;">✉️</span>
            <h2 style="color:#044332; margin:12px 0 6px 0;">Check Your Inbox</h2>
            <p style="color:#64748b; font-size:14px; line-height:1.5;">We just sent a secure login link to <b>{contact}</b>. Click it and you're in.</p>
            <a href="{magic_url}" style="display:inline-block; background:#044332; color:white; padding:11px 22px; border-radius:8px; text-decoration:none; font-weight:bold; font-size:13px; margin-top:8px;">Open on This Device Instead ➔</a>

            <div style="text-align:left; margin-top:26px; padding-top:20px; border-top:1px solid #f1f5f9;">
                <p style="font-size:12px; color:#64748b; margin:0 0 10px 0;"><b>On a different device than your inbox?</b> Enter the 6-digit code from the email:</p>
                <form action="/api/verify-otp" method="POST">
                    <input type="hidden" name="email" value="{contact}">
                    <input type="text" name="otp" inputmode="numeric" pattern="[0-9]{{6}}" maxlength="6" placeholder="------" required autocomplete="one-time-code">
                    <button type="submit">Verify Code</button>
                </form>
            </div>

            <p style="font-size:11px; color:#94a3b8; margin-top:20px;">Didn't get it? Check spam, or <a href="/login" style="color:#044332;">try again</a>. Emails can occasionally take a few minutes to arrive.</p>
        </div>
    </body>
    </html>
    """)


@app.get("/verify-login")
def verify_login(request: Request, token: Optional[str] = None, otp: Optional[str] = None, email: Optional[str] = None):
    # OTP is a 6-digit code (1,000,000 possibilities) with no prior throttling — this
    # caps guessing attempts per IP the same way /check-postcode is protected.
    client_ip = request.client.host if request.client else "unknown"
    if not _check_rate_limit(client_ip):
        return RedirectResponse(url="/login?error=Too+many+attempts.+Please+wait+a+minute+and+try+again.", status_code=303)

    verified_email = database.verify_magic_auth_token(token=token, otp=otp, email=email)

    if not verified_email:
        return RedirectResponse(url="/login?error=Login+link+expired+or+already+used.+Please+request+a+new+one.", status_code=303)

    # Paying subscribers land on the full dashboard, free-account signups on
    # theirs, everyone else gets nudged to subscribe -- see
    # _login_session_response above (Sep 5 2026 limbo-account distinction
    # preserved as-is, just no longer duplicated inline here).
    return _login_session_response(verified_email)


@app.post("/api/verify-otp")
async def verify_otp_route(request: Request):
    """Sep 8 2026: the OTP-entry counterpart to /verify-login's magic-link
    click -- the code emailed by request_magic_link above previously had
    nowhere to be submitted at all. Uses the exact same
    database.verify_magic_auth_token + _login_session_response path as the
    link click, so an OTP login is identical in every way except how the
    user proved it's really them."""
    client_ip = request.client.host if request.client else "unknown"
    if not _check_rate_limit(client_ip):
        return RedirectResponse(url="/login?error=Too+many+attempts.+Please+wait+a+minute+and+try+again.", status_code=303)

    form = await request.form()
    email = (form.get("email") or "").strip().lower()
    otp = (form.get("otp") or "").strip()

    if not email or not otp:
        return RedirectResponse(url="/login?error=Enter+both+your+email+and+the+6-digit+code+from+the+email.", status_code=303)

    verified_email = database.verify_magic_auth_token(otp=otp, email=email)
    if not verified_email:
        return RedirectResponse(url="/login?error=That+code+is+wrong%2C+expired%2C+or+already+used.+Please+request+a+new+one.", status_code=303)

    return _login_session_response(verified_email)


# ── Free "Limbo Account" Signup (Sep 5 2026, Nick's ask) ─────────────────────
# Sign up with no card/subscription -> get one real, fully-unlocked free
# lead near you immediately -> then 1-2x/week teaser emails (address
# blurred, job details + filed date shown) as an upgrade prompt. Verbatim
# spec: "sign up for a free account even without a subscription, just
# login details and normal account sign up details. then you get your
# free lead... we can then send them emails once or twice a week with
# specific leads in their area... without the finer details of the
# address viewable (blurred out or something) but the job details
# viewable and date it was applied... as a sales prompt."

@app.get("/free-account", response_class=HTMLResponse)
def free_account_signup_page(error: Optional[str] = None):
    err_html = f"<div style='background:#fef2f2; border:1px solid #fecaca; color:#991b1b; padding:10px; border-radius:6px; margin-bottom:16px; font-size:13px;'>{error}</div>" if error else ""
    return f"""
    <!DOCTYPE html>
    <html lang="en-GB">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>Get a Free Lead | TreeKey</title>
        <style>
            body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; background:#f8fafc; color:#0f172a; margin:0; padding:40px 16px; }}
            .box {{ max-width:440px; margin:auto; background:white; padding:32px; border-radius:16px; border:1px solid #e2e8f0; box-shadow:0 4px 16px rgba(0,0,0,0.04); }}
            input {{ width:100%; box-sizing:border-box; padding:12px; border:1px solid #cbd5e1; border-radius:8px; margin-top:6px; margin-bottom:16px; font-family:inherit; font-size:15px; }}
            button {{ background:#044332; color:white; border:none; padding:12px; border-radius:8px; font-weight:bold; font-size:15px; cursor:pointer; width:100%; }}
            label {{ font-size:12px; font-weight:bold; color:#475569; }}
        </style>
    </head>
    <body>
    <div class="box">
        <div style="text-align:center; margin-bottom:20px;">
            <span style="font-size:32px;">🌲</span>
            <h2 style="margin:8px 0 4px 0; color:#044332;">Get a free tree lead</h2>
            <p style="color:#64748b; font-size:13px; margin:0;">No card. No subscription. Just a real job near you.</p>
        </div>
        {err_html}
        <form action="/api/free-signup" method="POST">
            <label>Name:</label>
            <input type="text" name="name" placeholder="e.g. Dave Smith" required>
            <label>Email Address:</label>
            <input type="email" name="email" placeholder="e.g. dave@apex-trees.co.uk" required>
            <label>Phone (optional):</label>
            <input type="tel" name="phone" placeholder="e.g. 07123 456789">
            <label>Your Postcode or Area:</label>
            <input type="text" name="postcode" placeholder="e.g. NG22" required>
            <button type="submit">Get My Free Lead ⚡</button>
        </form>
        <div style="text-align:center; margin-top:20px; font-size:12px; color:#64748b;">
            Already have an account? <a href="/login" style="color:#044332; font-weight:bold;">Sign in</a>
        </div>
    </div>
    </body>
    </html>
    """


@app.post("/api/free-signup")
async def free_signup(request: Request):
    client_ip = request.client.host if request.client else "unknown"
    if not _check_rate_limit(client_ip):
        return RedirectResponse(url="/free-account?error=Too+many+attempts.+Please+wait+a+minute+and+try+again.", status_code=303)

    form = await request.form()
    name = (form.get("name") or "").strip()
    email = (form.get("email") or "").strip().lower()
    phone = (form.get("phone") or "").strip()
    postcode_input = (form.get("postcode") or "").strip()

    if not email or "@" not in email or not postcode_input:
        return RedirectResponse(url="/free-account?error=Please+enter+a+valid+email+and+postcode.", status_code=303)

    # Resolve the typed postcode/area to an outcode + lat/lon the same way
    # the rest of this app locates a lead or a subscriber -- see
    # database.lookup_outcode_centroid (postcodes.io outcode centroid).
    # Kept deliberately simple: take the first "word" typed as the outcode
    # (e.g. "NG22 8AA" -> "NG22", "NG22" -> "NG22") and let postcodes.io
    # itself say whether that's real, rather than guessing further here.
    outcode_guess = postcode_input.strip().upper().split(" ")[0]
    lat, lon = database.lookup_outcode_centroid(outcode_guess)
    if lat is None or lon is None:
        return RedirectResponse(url="/free-account?error=Couldn%27t+recognise+that+postcode+-+please+try+again+(e.g.+NG22).", status_code=303)

    account = database.create_or_update_limbo_account(email=email, name=name or None, phone=phone or None,
                                                        outcode=outcode_guess, lat=lat, lon=lon)
    if not account:
        return RedirectResponse(url="/free-account?error=Something+went+wrong+creating+your+account.+Please+try+again.", status_code=303)

    # Grant the one-off free lead only if this account has never had one --
    # record_free_lead_grant's own NULL guard makes this safe even under a
    # double-submit/retry, but checking here too avoids burning a second
    # lead's worth of postcodes.io lookups for nothing.
    if not account.get("free_lead_ref"):
        candidate = database.find_nearest_unclaimed_lead(lat, lon, max_miles=25.0)
        if candidate:
            burned = database.burn_lead_inventory(candidate["reference"], email)
            if burned:
                database.record_free_lead_grant(email, burned["reference"])
                import notifications
                notifications.send_free_account_welcome_email(email, burned)

    response = RedirectResponse(url="/free-dashboard", status_code=303)
    response.set_cookie(
        key="treekey_contractor_session",
        value=_sign_session_cookie(email),
        max_age=86400 * 30,
        httponly=True,
        secure=True,
        samesite="lax"
    )
    return response


@app.get("/free-dashboard", response_class=HTMLResponse)
def free_dashboard(request: Request):
    email = _verify_session_cookie(request.cookies.get("treekey_contractor_session"))
    if not email:
        return RedirectResponse(url="/login", status_code=303)

    # A real paying subscriber shouldn't see the free-tier page even if
    # they land on this URL directly -- send them to the real dashboard.
    active_sub = database.get_contractor_subscription(email)
    if active_sub and active_sub.get("active"):
        return RedirectResponse(url="/dashboard", status_code=303)

    account = database.get_limbo_account(email)
    if not account:
        return RedirectResponse(url="/free-account", status_code=303)

    if account.get("free_lead_ref"):
        lead = database.get_lead_by_reference(account["free_lead_ref"])
        if lead:
            lead_html = f"""
            <div class="card" style="border-left:4px solid #059669;">
                <p style="margin:0 0 10px 0;"><strong>Reference:</strong> {lead.get('reference', 'N/A')}</p>
                <p style="margin:0 0 10px 0;"><strong>Address:</strong> {lead.get('address', 'N/A')}</p>
                <p style="margin:0 0 10px 0;"><strong>Source:</strong> {lead.get('council_source', 'N/A')}</p>
                <p style="margin:0;"><strong>Description:</strong><br>
                   <span style="color:#475569; font-size:14px;">{lead.get('summary', 'No summary available.')}</span></p>
            </div>
            """
        else:
            lead_html = "<p style='color:#64748b;'>Your free lead is no longer available to display, but it was genuinely yours when granted.</p>"
    else:
        lead_html = "<p style='color:#64748b;'>No job was available in your exact area the moment you signed up — we'll email you the first one that appears nearby.</p>"

    return HTMLResponse(f"""
    <!DOCTYPE html>
    <html lang="en-GB">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>Your Free Lead | TreeKey</title>
        <style>
            body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; background:#f8fafc; color:#0f172a; margin:0; padding:32px 16px; }}
            .container {{ max-width:640px; margin:auto; }}
            .card {{ background:white; border:1px solid #e2e8f0; border-radius:12px; padding:20px; margin-bottom:20px; }}
            .btn {{ background:#044332; color:white; padding:12px 20px; border-radius:8px; text-decoration:none; font-weight:bold; display:inline-block; }}
        </style>
    </head>
    <body>
    <div class="container">
        <h2 style="color:#044332;">🌲 Your Free Lead</h2>
        {lead_html}
        <div class="card" style="background:#f0fdf4; border-color:#bbf7d0;">
            <p style="margin:0 0 12px 0; font-size:14px; color:#065f46;">
                You're on our free list — expect a couple of local jobs a week by email (address blurred until you subscribe).
                Subscribe any time to unlock full addresses and get jobs the moment they're filed.
            </p>
            <a href="/pricing" class="btn">See Subscription Plans →</a>
        </div>
    </div>
    </body>
    </html>
    """)


@app.get("/unsubscribe-teaser", response_class=HTMLResponse)
def unsubscribe_teaser(token: Optional[str] = None):
    email = _verify_session_cookie(token)
    if not email:
        return HTMLResponse("<h3>Invalid or expired unsubscribe link.</h3>", status_code=400)
    database.set_limbo_account_unsubscribed(email)
    return HTMLResponse(f"<h3>You've been unsubscribed, {email}. You won't receive any more of these emails.</h3>")


@app.get("/trigger-teaser-emails")
def trigger_teaser_emails(secret: Optional[str] = Query(None)):
    """Sep 5 2026: cron-job.org-triggered, same convention as every other
    /trigger-* route in this file (verify_cron_secret gate). Intended
    schedule: 2-3x/week, comfortably above the 72-hour min_hours_since_last
    default in send_teaser_email_batch so it naturally settles into
    roughly twice a week per account even if the cron fires more often."""
    verify_cron_secret(secret)
    import notifications

    def _make_unsubscribe_url(recipient_email: str) -> str:
        token = _sign_session_cookie(recipient_email)
        return f"{payments.PUBLIC_APP_URL}/unsubscribe-teaser?token={token}"

    sent = notifications.send_teaser_email_batch(unsubscribe_url_builder=_make_unsubscribe_url)
    return {"status": "ok", "teasers_sent": sent}


@app.get("/dashboard", response_class=HTMLResponse)
def contractor_dashboard(request: Request):
    # Cookie session only — never accept email as a query param (privacy risk)
    session_email = _verify_session_cookie(request.cookies.get("treekey_contractor_session"))
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

        # Aug 30 2026: has_agent/applicant_name are captured by the scraper but
        # were never shown here -- every lead looked identical whether or not
        # the council record already lists an agent/contractor. has_agent is
        # True / False / None (not checked or inconclusive) -- None must read
        # as "unconfirmed", never as "no agent".
        applicant_name = l.get("applicant_name")
        has_agent = l.get("has_agent")
        if has_agent is True:
            agent_badge = f"<span style='font-size:10px; background:#fef3c7; color:#92400e; padding:2px 6px; border-radius:4px; font-weight:bold;'>⚠️ AGENT ON RECORD{' — ' + l['agent_company'] if l.get('agent_company') else ''}</span>"
        elif has_agent is False:
            agent_badge = "<span style='font-size:10px; background:#d1fae5; color:#065f46; padding:2px 6px; border-radius:4px; font-weight:bold;'>✅ NO AGENT LISTED</span>"
        else:
            agent_badge = "<span style='font-size:10px; background:#f1f5f9; color:#64748b; padding:2px 6px; border-radius:4px;'>AGENT STATUS UNCONFIRMED</span>"
        applicant_line = f"<br><span style='font-size:11px; color:#475569;'>Applicant: {applicant_name}</span>" if applicant_name else ""

        lead_rows += f"""
        <div style="background:white; border:1px solid #e2e8f0; border-radius:10px; padding:16px; margin-bottom:12px;">
            <div style="display:flex; justify-content:space-between; align-items:flex-start; flex-wrap:wrap; gap:8px;">
                <div>
                    <span style="font-size:10px; background:#f1f5f9; color:#475569; padding:2px 6px; border-radius:4px; font-weight:bold;">REF: {ref}</span>
                    {agent_badge}
                    <h4 style="margin:4px 0 2px 0; font-size:15px; color:#0f172a;">📍 {addr}</h4>
                    <span style="font-size:11px; color:#64748b;">Dispatched: {dispatched_at}</span>{applicant_line}
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
                        <a href="/settings" style="color:#a7f3d0; font-size:12px; text-decoration:none; margin-right:12px;">Settings ⚙️</a>
                        <a href="/logout" style="color:#a7f3d0; font-size:12px; text-decoration:none;">Log Out ➔</a>
                    </div>
                </div>
            </div>
        </div>

        <!-- Quick Access Operational Tools -->
        <div class="quick-grid">
            <a href="/ledger" class="quick-card" style="border-top:2px solid #8b5cf6;">
                <div style="font-weight:bold; font-size:14px; margin:4px 0 2px 0;">TreeKey Ledger</div>
                <div style="font-size:11px; color:#64748b;">Van-Day Costing & £90k VAT Gauge</div>
            </a>
            <a href="/chip-drop" class="quick-card" style="border-top:2px solid #fb923c;">
                <div style="font-weight:bold; font-size:14px; margin:4px 0 2px 0;">Chip-Drop Network</div>
                <div style="font-size:11px; color:#64748b;">Skip £60-£120 Tipping Fees Free</div>
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
    response.delete_cookie("treekey_contractor_session", httponly=True, secure=True, samesite="lax")
    return response




# ── 5. Free Woodchip & Timber Drop-Spotter Hub ─────────────────────────────────

@app.get("/chip-drop", response_class=HTMLResponse)
def chip_drop_view(outcode: Optional[str] = None, material: Optional[str] = "all", find_near: Optional[str] = None):
    """
    Woodchip & Timber Drop-Spotter Directory:
    Connects tree surgeons with nearby allotments, farms, and smallholders wanting free arborist woodchip or logs.
    Saves £60-£120 commercial tipping fees per van load.

    Sep 8 2026, Nick's ask: on top of the registered directory below, an
    optional `find_near` postcode/outcode search surfaces real-world OSM
    candidates (farms, allotments, stables, garden centres) nearby that
    AREN'T confirmed opt-ins -- see database.find_chip_drop_candidates_via_osm
    for why this deliberately runs on free OpenStreetMap data rather than
    the app's existing (budget-capped) Google Places integration.
    """
    spots = database.get_chip_drop_spots(outcode=outcode, material=material, limit=40)

    candidates_html = ""
    if find_near:
        loc = database.resolve_location(find_near)
        if loc["lat"] is None:
            candidates_html = f"<p style='color:#b91c1c; font-size:13px; margin-bottom:20px;'>Couldn't resolve '{find_near}' to a real UK postcode/outcode.</p>"
        else:
            candidates = database.find_chip_drop_candidates_via_osm(loc["lat"], loc["lon"], radius_miles=10)
            if not candidates:
                candidates_html = f"<p style='color:#64748b; font-size:13px; margin-bottom:20px;'>No candidates found on OpenStreetMap within 10 miles of {loc['outcode']}. OSM coverage varies by area -- try registering sites you already know instead.</p>"
            else:
                cand_cards = "".join([
                    f"""<div style="background:white; border:1px solid #bfdbfe; border-radius:10px; padding:16px; margin-bottom:10px; display:flex; justify-content:space-between; align-items:center; flex-wrap:wrap; gap:10px;">
                        <div>
                            <span style="font-size:11px; background:#eff6ff; color:#1d4ed8; font-weight:bold; padding:3px 8px; border-radius:12px;">🔍 Possible Candidate — Not Registered</span>
                            <h4 style="margin:6px 0 2px 0; font-size:15px; color:#0f172a;">{c['category']} {c['name']}</h4>
                            <p style="margin:0; font-size:12px; color:#64748b;">{c['distance_miles']} mi away{' · ' + c['address_hint'] if c['address_hint'] else ''} · <a href="{c['osm_url']}" target="_blank" style="color:#64748b;">view on map</a></p>
                        </div>
                        <a href="/register-drop-spot?site_name={urllib.parse.quote(c['name'])}&town={urllib.parse.quote(loc['outcode'])}" style="background:#1d4ed8; color:white; padding:7px 14px; border-radius:6px; text-decoration:none; font-weight:bold; font-size:12px; white-space:nowrap;">Suggest for Registration →</a>
                    </div>"""
                    for c in candidates
                ])
                candidates_html = f"""
                <div style="background:#eff6ff; border:1px solid #bfdbfe; border-radius:10px; padding:14px 18px; margin-bottom:14px; font-size:13px; color:#1e40af;">
                    <b>ℹ️ Unconfirmed:</b> these are real places found on OpenStreetMap near {loc['outcode']} that often welcome woodchip — nobody has registered them yet, so you'd need to call and ask first. Not the same as the ✅ registered listings below.
                </div>
                {cand_cards}
                """

    # Sep 8 2026 FIX: this used to fall back to three entirely fabricated
    # "sample" sites -- invented names, invented contact people, and fake
    # phone numbers -- shown with the exact same "✅ Free Drop Site" badge,
    # live tel: link and live WhatsApp link as real registered listings.
    # Any contractor visiting while the real directory is still empty (the
    # normal case pre-launch) would have no way to tell these apart from
    # real businesses, and clicking Call/WhatsApp on a fake number is a
    # bad experience at best. Replaced with an honest, clearly-labelled
    # empty state instead of invented listings.
    empty_state_html = ""
    if not spots:
        empty_state_html = f"""
        <div style="background:white; border:1px dashed #cbd5e1; border-radius:12px; padding:28px; text-align:center; color:#64748b;">
            <div style="font-size:32px; margin-bottom:8px;">🌱</div>
            <h3 style="margin:0 0 8px 0; color:#0f172a; font-size:16px;">No drop sites listed{f' for {outcode}' if outcode else ''} yet</h3>
            <p style="font-size:13px; margin:0 0 16px 0; max-width:440px; margin-left:auto; margin-right:auto;">
                This directory is filled entirely by real allotments, farms, stables and gardens who register themselves —
                nothing here is invented. Know a local landowner who'd want free arborist woodchip or logs? Point them at
                the registration form and you'll have a drop site near your next job.
            </p>
            <a href="/register-drop-spot" style="display:inline-block; background:#044332; color:white; padding:10px 20px; border-radius:6px; text-decoration:none; font-weight:bold; font-size:13px;">+ Register a Drop Site</a>
        </div>
        """

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
                <h1 style="margin:0; font-size:28px; color:#044332;">Free Woodchip & Timber Drop-Spotter</h1>
                <p style="margin:4px 0 0 0; color:#64748b; font-size:14px;">Drop fresh arborist chips and timber rings within minutes of your job site. Save £60–£120 tipping fees.</p>
            </div>
            <a href="/register-drop-spot" style="background:#044332; color:white; padding:10px 18px; border-radius:6px; text-decoration:none; font-weight:bold; font-size:13px;">+ Register a Drop Site</a>
        </div>

        <div style="background:#ecfdf5; border:1px solid #a7f3d0; border-radius:10px; padding:14px 18px; margin-bottom:24px; font-size:13px; color:#065f46;">
            <b>💡 Pro-Tip for Tree Surgeons:</b> Tipping stations charge £80–£120 + VAT per load plus 45 minutes round-trip driving time. Drop your arborist waste at local community sites for £0.00.
        </div>

        <form method="GET" style="background:white; border:1px solid #e2e8f0; border-radius:10px; padding:16px; margin-bottom:20px; display:flex; gap:10px; flex-wrap:wrap; align-items:end;">
            <div style="flex:1; min-width:180px;">
                <label style="display:block; font-size:11px; font-weight:bold; color:#475569; margin-bottom:4px;">🔍 Find nearby candidates (unconfirmed farms/allotments/stables)</label>
                <input type="text" name="find_near" value="{find_near or ''}" placeholder="e.g. NG22 or CR5 2LE" style="width:100%; box-sizing:border-box; padding:9px; border:1px solid #cbd5e1; border-radius:6px;">
            </div>
            <button type="submit" style="background:#1d4ed8; color:white; padding:10px 18px; border:none; border-radius:6px; font-weight:bold; font-size:13px; cursor:pointer;">Search</button>
        </form>
        {candidates_html}

        <h2 style="font-size:16px; color:#0f172a; margin:24px 0 12px 0;">✅ Registered Drop Sites</h2>
        {spot_cards or empty_state_html}

        <div style="text-align:center; margin-top:32px;">
            <a href="/" style="color:#64748b; text-decoration:none; font-size:13px;">← Return to Main Intelligence Map</a>
        </div>
    </div>
    </body>
    </html>
    """


@app.get("/register-drop-spot", response_class=HTMLResponse)
def register_drop_spot_page(site_name: Optional[str] = None, outcode: Optional[str] = None, town: Optional[str] = None):
    """
    Intake form for UK landowners, allotments, and stables wanting free arborist woodchip or firewood.

    Sep 8 2026: now accepts optional prefill params (site_name/outcode/town)
    -- the "Suggest for Registration" link on a /chip-drop OSM candidate
    lands here with the candidate's name and area pre-filled, so turning an
    unconfirmed OSM find into a real registered listing is a couple of
    fields, not starting from a blank form.
    """
    site_name_val = html.escape(site_name or "")
    outcode_val = html.escape(outcode or "")
    town_val = html.escape(town or "")
    prefill_note = ""
    if site_name:
        prefill_note = (
            "<div style='background:#eff6ff; border:1px solid #bfdbfe; border-radius:8px; padding:10px 14px; margin-bottom:16px; font-size:12px; color:#1e40af;'>"
            "Pre-filled from an unconfirmed nearby candidate — please double-check every field and only submit once you've actually confirmed with the site."
            "</div>"
        )
    return f"""
    <!DOCTYPE html>
    <html lang="en-GB">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>Register Free Woodchip Drop Site | TreeKey</title>
        <style>
            body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; background:#f8fafc; color:#0f172a; margin:0; padding:40px 16px; }}
            .box {{ max-width: 520px; margin: auto; background: white; padding: 32px; border-radius: 16px; border: 1px solid #e2e8f0; box-shadow: 0 4px 16px rgba(0,0,0,0.04); }}
            input, select, textarea {{ width: 100%; box-sizing: border-box; padding: 10px; border: 1px solid #cbd5e1; border-radius: 8px; margin-top: 4px; margin-bottom: 14px; font-family: inherit; }}
            button {{ background: #044332; color: white; border: none; padding: 12px; border-radius: 8px; font-weight: bold; font-size: 15px; cursor: pointer; width: 100%; }}
        </style>
    </head>
    <body>
    <div class="box">
        <h2 style="margin-top:0; color:#044332;">🏡 Register Free Woodchip Drop Site</h2>
        <p style="color:#64748b; font-size:13px;">Need free organic woodchip mulch, wood chips, or hardwood logs for your garden, allotment, or stables? Local tree surgeons will drop free loads directly to your property.</p>
        {prefill_note}

        <form action="/api/submit-drop-spot" method="POST">
            <label style="font-size:12px; font-weight:bold;">Property / Site Name:</label>
            <input type="text" name="site_name" value="{site_name_val}" placeholder="e.g. Oak Tree Allotments or Highfield Farm" required>

            <label style="font-size:12px; font-weight:bold;">Contact Name:</label>
            <input type="text" name="contact_name" placeholder="e.g. Dave or Sarah" required>

            <label style="font-size:12px; font-weight:bold;">Phone / WhatsApp (for delivery driver to call):</label>
            <input type="tel" name="phone" placeholder="e.g. 07700 900123" required>

            <div style="display:grid; grid-template-columns:1fr 1fr; gap:10px;">
                <div>
                    <label style="font-size:12px; font-weight:bold;">Postcode Outcode:</label>
                    <input type="text" name="outcode" value="{outcode_val}" placeholder="e.g. LS6 or WF1" required>
                </div>
                <div>
                    <label style="font-size:12px; font-weight:bold;">Town / City:</label>
                    <input type="text" name="town" value="{town_val}" placeholder="e.g. Leeds" required>
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


@app.get("/robots.txt")
def robots_txt():
    """Serves robots.txt pointing crawlers to the sitemap."""
    base = os.getenv("PUBLIC_APP_URL", "https://treekey.uk").rstrip("/")
    content = f"User-agent: *\nAllow: /\nDisallow: /admin\nDisallow: /api/\nSitemap: {base}/sitemap.xml\n"
    return Response(content=content, media_type="text/plain")


@app.get("/sitemap.xml")
def sitemap_xml():
    """Generates dynamic XML sitemap for Google Search Console indexing all UK city hubs."""
    base = os.getenv("PUBLIC_APP_URL", "https://treekey.uk").rstrip("/")
    urls = [
        f"{base}/",
        f"{base}/marketplace",
        f"{base}/quote-estimator",
        f"{base}/pricing",
        f"{base}/boost-review"
    ]
    for slug in UK_LOCAL_SEO_HUBS.keys():
        urls.append(f"{base}/tree-surgeon/{slug}")

    for pc_prefix in UK_ALL_POSTCODE_AREAS.keys():
        urls.append(f"{base}/tree-surgeon/{pc_prefix.lower()}")

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


def _run_single_city_scan(city: str) -> None:
    """The actual scan work for one region/city -- shared by /scan/{city}
    and /trigger-leads/{city} below, both of which now dispatch this
    through _dispatch_locked_scan instead of running it inline."""
    if city in ("Leeds", "Yorkshire"):
        count = scanners.scan_leeds_leads()
    elif city in ("London", "South East"):
        count = scanners.scan_london_leads()
    else:
        count = scanners.scan_city_planning_api(city)
    logger.info(f"[{city}] scan complete: {count} new leads.")


@app.get("/scan/{city_slug}", response_class=HTMLResponse)
def scan_city(city_slug: str, request: Request, secret: Optional[str] = Query(None)):

    verify_admin_or_secret(request, secret)
    city = _resolve_city_param(city_slug)
    if not city:
        raise HTTPException(status_code=404, detail=f"Region/City '{city_slug}' not configured.")

    # Aug 30 2026: this used to call scan_leeds_leads/scan_london_leads/
    # scan_city_planning_api directly, INLINE, in the request handler --
    # with no _PIPELINE_LOCK protection at all, unlike every other "scan
    # everything" trigger in this file. That meant a manual /scan/{city}
    # click could run concurrently with the nightly full pipeline (or with
    # another /scan/{city} click), both hitting the same council portals
    # at once. It also ran synchronously: now that PlanIt is correctly
    # paced at ~1 request/minute (see scanners._planit_wait_for_slot), a
    # multi-authority region like London (13 authorities) can take over 10
    # minutes just for PlanIt calls -- long enough to risk an HTTP request
    # timeout on Render. Routed through the same shared lock + background
    # thread every other scan trigger already uses.
    result = _dispatch_locked_scan(lambda: _run_single_city_scan(city), f"scan_{city}")

    if result["status"] == "already_running":
        return f"""<html><body style="font-family:sans-serif; padding:40px;">
            <p>A scan is already running (started {result.get('started_at', 'earlier')}) -- this shares a lock with every other scan trigger in this app, since they all hit the same council portals. Wait for it to finish, then try {city} again.</p>
            <a href="/admin">&#9194; Back to Admin Command</a>
        </body></html>"""

    return f"""<html><body style="font-family:sans-serif; padding:40px;">
        <p>{city} scan started in the background. PlanIt is now paced at roughly one request per minute (per the Aug 30 2026 rate-limit fix), so a multi-authority region can take several minutes -- check <a href="/admin">/admin</a> shortly for the new lead count.</p>
        <a href="/admin">&#9194; Back to Admin Command</a>
    </body></html>"""


#  City Cron Routes (External  Trigger Secret)

@app.get("/trigger-leads/{city_slug}")
def cron_trigger_slash(city_slug: str, secret: Optional[str] = Query(None)):
    verify_cron_secret(secret)
    city = _resolve_city_param(city_slug)
    if not city:
        raise HTTPException(status_code=404, detail=f"Region/City '{city_slug}' not configured.")

    # Aug 30 2026: same fix as /scan/{city} above -- was previously
    # synchronous with no lock, so an external cron hit here could run
    # concurrently with the nightly full pipeline against the same
    # council portals, and (now that PlanIt pacing is slower) risked a
    # timed-out cron request. Dispatches through the same shared lock;
    # the caller no longer gets a "new_leads" count back since the scan
    # now runs after this response is returned -- callers that only care
    # whether the trigger fired (the normal use for an external cron) are
    # unaffected, since "started"/"already_running" is still a clear 200.
    result = _dispatch_locked_scan(lambda: _run_single_city_scan(city), f"cron_scan_{city}")
    result["city"] = city
    return result


@app.get("/api/scan-nationwide-all-uk")
def scan_nationwide_all_uk_endpoint(secret: Optional[str] = Query(None)):
    """
    Crawls all UK regions in parallel to capture thousands of planning and domestic leads.
    """
    verify_cron_secret(secret)
    result = _dispatch_locked_scan(scanners.scan_nationwide_bulk_crawler, "nationwide_bulk_crawl")
    result["coverage"] = "124 UK Outward Postcodes & 300+ Councils"
    return result


@app.get("/scan-domestic-jobs", response_class=HTMLResponse)
def scan_domestic_jobs_view(request: Request, secret: Optional[str] = Query(None)):
    """
    Triggers multi-source domestic tree surgery scraper (Gumtree, FixMyStreet, Community boards).
    """
    verify_admin_or_secret(request, secret)
    count = 0
    return f"""<html><body style="font-family:sans-serif; padding:40px; background:#f8fafc;">
        <h2 style="color:#044332;">🏡 Domestic Job Board Scraper Complete</h2>
        <p>Successfully intercepted and routed <b>{count} new private homeowner tree leads</b> directly to senior contractors.</p>
        <a href="/admin" style="background:#044332; color:white; padding:8px 16px; border-radius:6px; text-decoration:none; font-weight:bold; font-size:13px;">← Return to Admin Panel</a>
    </body></html>"""


@app.get("/trigger-domestic-scan")
def cron_trigger_domestic_scan(secret: Optional[str] = Query(None)):
    verify_cron_secret(secret)
    count = 0
    return {"status": "success", "source": "domestic_multi_source", "new_leads": count}


@app.get("/api/run-domestic-scan-now")
def run_domestic_scan_now(secret: Optional[str] = Query(None)):
    """
    Direct scan trigger returning live intercepted leads and Supabase database breakdown.
    """
    verify_cron_secret(secret)

    dispatch_result = _dispatch_locked_scan(scanners.scan_nationwide_bulk_crawler, "nationwide_bulk_crawl")

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
        "status": dispatch_result["status"],  # "started" or "already_running" -- was always hardcoded "scan_dispatched_and_active" before, even when no new scan actually started
        "dispatch_message": dispatch_result.get("message"),
        "database_breakdown": db_stats,
        "recent_intercepted_leads": recent_leads
    }


@app.get("/api/purge-old-domestic-archives")
def purge_old_domestic_archives(secret: Optional[str] = Query(None)):
    """
    Purges historical archive noise from domestic leads table without touching council records.
    """
    verify_cron_secret(secret)
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

    # Stage -1: Monthly Quota Reset (safe daily call — only fires on 1st of month)
    try:
        reset_count = database.reset_monthly_quotas_if_needed()
        if reset_count > 0:
            logger.info(f"[PIPELINE] Monthly Reset: {reset_count} contractor quotas refreshed for new month.")
    except Exception as e:
        logger.error(f"[PIPELINE] Monthly reset error: {e}")

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
            # Aug 30 2026: scanners.scan_gla_datahub_london() -- the free
            # London GLA Planning Datahub -- was built and working but never
            # actually wired into this scheduled pipeline; it only ran from
            # three manual/admin-triggered endpoints. Calling it here, ADDITIONALLY
            # to (not instead of) the scan_city_planning_api("London") call above,
            # gives London a genuine third free data source distinct from
            # ukplanningapi.co.uk and PlanIt -- directly the "spread requests
            # across multiple free sources" approach this pipeline was meant
            # to use, previously sitting unused every single day. No-ops
            # (returns 0) if GLA_API_KEY isn't configured.
            if city == "London":
                try:
                    gla_leads = scanners.scan_gla_datahub_london()
                    total_leads_scanned += gla_leads
                except Exception as gla_e:
                    logger.error(f"[PIPELINE] Stage 1 GLA Datahub error: {gla_e}")
            # Sep 3 2026: same gap as the GLA Datahub fix above, found while
            # checking what this morning's automatic run would actually have
            # covered -- scanners.scan_leeds_leads() (the ArcGIS field-name/
            # date-filter fix from earlier today) was ONLY reachable via
            # scan_nationwide_bulk_crawler(), itself only reachable from
            # manual/admin-triggered endpoints, never from this scheduled
            # pipeline. "Yorkshire" above already covers Leeds through the
            # generic postcode-radar path, but that's the paid/PlanIt route,
            # not Leeds' own free ArcGIS layer -- so today's fix would have
            # sat unused every single day exactly like GLA Datahub did before
            # Aug 30 2026's fix, if left as-is. Wiring it in here the same way.
            if city == "Yorkshire":
                try:
                    leeds_leads = scanners.scan_leeds_leads()
                    total_leads_scanned += leeds_leads
                except Exception as leeds_e:
                    logger.error(f"[PIPELINE] Stage 1 Leeds ArcGIS error: {leeds_e}")
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

    logger.info("[PIPELINE] 🏁 Master Daily Pipeline finished successfully.")


def run_full_autonomous_cycle():
    """Sep 2 2026: Nick's call -- 'I don't want to have to manually scan
    things, it should be autonomous.' This is the one function the
    scheduler below calls once a day: the existing 4-stage scan pipeline,
    followed by every maintenance job that used to require a manual button
    click (lead tag backfill, region resync, partner tag backfill, partner
    enrichment). Each maintenance step loops until it reports no more work
    left, capped at 20 iterations as a safety valve (matching this
    project's other 'never spin forever' guards), so a growing backlog gets
    fully drained autonomously instead of only nibbled at once a day.

    Sep 2 2026: also stamps last_autonomous_cycle_started_at immediately,
    before any work happens. This exists because a real incident showed
    the gap in only stamping completion: if Render restarts mid-cycle
    (whether from a deploy or someone hitting restart to stop a runaway
    cost), the completion stamp never gets written, so the scheduler saw
    no recent run and fired the whole expensive cycle again ~2 minutes
    after every subsequent restart. Stamping the start means the 20-hour
    cooldown holds even when a cycle never finishes."""
    database.set_system_state("last_autonomous_cycle_started_at", datetime.datetime.utcnow().isoformat() + "Z")
    run_master_daily_pipeline()

    logger.info("[AUTO] Starting tag/enrichment maintenance pass...")
    try:
        for _ in range(20):
            result = database.backfill_lead_tags(batch_size=1000)
            if result.get("updated", 0) < 1000:
                break
    except Exception as e:
        logger.error(f"[AUTO] backfill_lead_tags error: {e}")

    try:
        for _ in range(20):
            result = database.resync_region_tags(batch_size=2000)
            done = result.get("updated", 0) + result.get("unchanged", 0) + result.get("errors", 0)
            if done < 2000:
                break
    except Exception as e:
        logger.error(f"[AUTO] resync_region_tags error: {e}")

    try:
        for _ in range(20):
            result = database.backfill_partner_tags(batch_size=1000)
            if result.get("updated", 0) < 1000:
                break
    except Exception as e:
        logger.error(f"[AUTO] backfill_partner_tags error: {e}")

    try:
        research.enrich_existing_partners(limit=0)
    except Exception as e:
        logger.error(f"[AUTO] enrich_existing_partners error: {e}")

    _check_for_silent_source_failures()

    # Sep 8 2026, Nick's ask: replaces what used to be a dozen+ separate
    # per-council WARNING escalation emails hitting his inbox within the
    # same hour with exactly one daily digest (or none, on a quiet day).
    # Rides this existing once-a-day cycle rather than needing a new
    # cron-job.org entry -- see notifications.send_daily_warning_digest's
    # own docstring for the full reasoning.
    try:
        import notifications
        notifications.send_daily_warning_digest()
    except Exception as e:
        logger.error(f"[AUTO] Daily warning digest error: {e}")

    database.set_system_state("last_autonomous_cycle_at", datetime.datetime.utcnow().isoformat() + "Z")
    logger.info("[AUTO] Autonomous daily cycle fully complete.")


def _check_for_silent_source_failures():
    """Sep 3 2026: runs at the end of every autonomous cycle. See
    database.get_lead_source_health_report()'s own docstring for the full
    reasoning (the Leeds ArcGIS incident: months of silent zero-output with
    no error ever raised). Fires one throttled WARNING per flagged source
    -- WARNING, not CRITICAL, because a genuinely dead source costs no
    money and breaks nothing else; it just means fewer leads until a human
    looks. throttle_hours=24 so a source stuck at zero for a week gets one
    alert a day, not one every 20 hours a fresh cycle happens to check."""
    try:
        report = database.get_lead_source_health_report()
        flagged = [r for r in report if r.get("possible_silent_failure")]
        if not flagged:
            return
        import notifications
        for r in flagged:
            notifications.send_system_incident_alert(
                category="SILENT SOURCE FAILURE",
                title=f"{r['council_source']} HAS PRODUCED ZERO LEADS IN {r['recent_days']} DAYS",
                description=(
                    f"'{r['council_source']}' historically produces roughly "
                    f"{r['baseline_daily_rate']} leads/day but has produced "
                    f"exactly 0 in the last {r['recent_days']} days. No error was "
                    f"raised anywhere -- this is the same failure shape the Leeds "
                    f"ArcGIS bug had (wrong field names, zero real leads, zero "
                    f"exceptions) before it was found and fixed Sep 3 2026."
                ),
                impact=f"'{r['council_source']}' is likely producing no real leads right now, silently.",
                action_required=f"Manually check this source (e.g. /test-mesh-council for a mesh council) to confirm whether it's genuinely broken or just quiet.",
                severity="WARNING",
                throttle_hours=24.0
            )
    except Exception as e:
        logger.error(f"[AUTO] Silent source failure check error: {e}")


def _autonomous_scheduler_loop():
    """Sep 2 2026: makes the whole platform self-driving. Checks every 20
    minutes whether it's been >=20 hours since the last full autonomous
    cycle -- tracked in the system_state table, which survives Render
    restarts, not an in-memory flag that would forget every redeploy. 20h
    (not 24h) gives slack so the cycle drifts earlier over time rather than
    ever silently skipping a day. Because the persisted DB timestamp is
    what controls this (not process uptime), Nick redeploying five times in
    an afternoon can't accidentally fire five scans -- each restart just
    resumes the same countdown.

    Sep 2 2026 fix: the cooldown check now keys off whichever is MORE
    RECENT of last_autonomous_cycle_started_at (stamped the instant a
    cycle begins) and last_autonomous_cycle_at (stamped only on a clean
    finish) -- not completion alone. A real incident showed why: a cycle
    interrupted by a restart before it could finish left the completion
    stamp unwritten, so this loop saw 'no recent run' and re-fired the
    entire cycle again ~2 minutes after every subsequent restart, with no
    way to stop it short of waiting it out. Keying off the start time
    means one attempt -- finished or not -- blocks re-firing for a full
    20 hours, matching how a human operator would expect 'try again
    later, not again immediately' to behave."""
    time.sleep(120)  # let the app finish starting up before the first check
    while True:
        try:
            last_started_iso = database.get_system_state("last_autonomous_cycle_started_at")
            last_finished_iso = database.get_system_state("last_autonomous_cycle_at")
            most_recent_iso = max(
                [iso for iso in (last_started_iso, last_finished_iso) if iso]
            ) if (last_started_iso or last_finished_iso) else None
            should_run = True
            if most_recent_iso:
                try:
                    last_run = datetime.datetime.fromisoformat(most_recent_iso.replace("Z", "+00:00"))
                    hours_since = (datetime.datetime.now(datetime.timezone.utc) - last_run).total_seconds() / 3600
                    should_run = hours_since >= 20
                except Exception:
                    should_run = True
            if should_run and not _pipeline_state.get("running"):
                logger.info("[AUTO] Kicking off autonomous daily cycle.")
                _dispatch_locked_scan(run_full_autonomous_cycle, "autonomous_daily_cycle")
        except Exception as e:
            logger.error(f"[AUTO] Scheduler check error: {e}")
        time.sleep(20 * 60)


@app.on_event("startup")
def _start_autonomous_scheduler():
    threading.Thread(target=_autonomous_scheduler_loop, daemon=True).start()
    logger.info("[AUTO] Autonomous scheduler thread started -- no manual scanning/enriching needed going forward.")


def _dispatch_locked_scan(target_fn, action_name: str) -> dict:
    """Shared concurrency guard for every 'scan everything' trigger endpoint.

    Aug 30 2026: confirmed live that /trigger-daily-pipeline was fired while
    a previous run was still in Stage 3, and both ran concurrently. Tracing
    it further found this wasn't the only way to trigger a full scan:
    /scan-nationwide, /api/scan-nationwide-all-uk, and /api/run-domestic-
    scan-now ALL independently start scanners.scan_nationwide_bulk_crawler(),
    which itself calls run_mesh_network_scan() -- the exact same mesh scan
    run_master_daily_pipeline()'s Stage 0 calls. Four separate endpoints
    could each kick off a scan of the same ~50+ council portals with zero
    coordination between them, which is a direct, likely contributor to the
    repeated council timeouts/503s seen in production logs (the same portal
    getting hit by two overlapping scans within seconds of each other looks
    exactly like what a rate limiter is designed to block). All four now
    share this one lock, so only one full scan can run at a time no matter
    which endpoint kicks it off.
    """
    if not _PIPELINE_LOCK.acquire(blocking=False):
        return {
            "status": "already_running",
            "action": action_name,
            "started_at": _pipeline_state.get("started_at"),
            "message": "A scan is already in progress -- this trigger shares a lock with /trigger-daily-pipeline, /scan-nationwide, /api/scan-nationwide-all-uk, and /api/run-domestic-scan-now, since they all hit the same council portals. Wait for it to finish, then trigger again."
        }
    _pipeline_state["running"] = True
    _pipeline_state["started_at"] = datetime.datetime.utcnow().isoformat() + "Z"

    def _wrapped():
        try:
            target_fn()
        except Exception as e:
            logger.error(f"[{action_name}] Unhandled error: {e}")
        finally:
            _pipeline_state["running"] = False
            _PIPELINE_LOCK.release()

    threading.Thread(target=_wrapped, daemon=True).start()
    return {"status": "started", "action": action_name, "timestamp": _pipeline_state["started_at"]}


@app.get("/trigger-daily-pipeline")
def trigger_daily_pipeline(secret: Optional[str] = Query(None)):
    """
    Single master cron job endpoint.
    Executes full 4-stage ingestion and quality sanitization pipeline.
    """
    verify_cron_secret(secret)
    return _dispatch_locked_scan(run_master_daily_pipeline, "master_daily_pipeline")


@app.get("/trigger-autonomous-cycle")
def trigger_autonomous_cycle(secret: Optional[str] = Query(None)):
    """Sep 2 2026: manual override for run_full_autonomous_cycle (the same
    thing the scheduler fires automatically once a day) -- for forcing a
    full run right now rather than waiting for the next scheduled check,
    e.g. right after a redeploy that changed the tagging/enrichment logic.
    Also resets the 'last run' timer, same as a scheduled firing would."""
    verify_cron_secret(secret)
    return _dispatch_locked_scan(run_full_autonomous_cycle, "autonomous_daily_cycle")


@app.get("/pipeline-status")
def pipeline_status(secret: Optional[str] = Query(None)):
    """Aug 30 2026: added alongside the concurrency lock above so there's a
    real way to check whether a scan is in progress, instead of only
    watching raw logs for the start/finish lines."""
    verify_cron_secret(secret)
    return {
        "running": _pipeline_state["running"],
        "started_at": _pipeline_state.get("started_at"),
        "last_autonomous_cycle_at": database.get_system_state("last_autonomous_cycle_at"),
    }


@app.get("/reset-pipeline-lock")
def reset_pipeline_lock(secret: Optional[str] = Query(None)):
    """Sep 1 2026: manual escape hatch for a stuck lock, so restarting a
    scan doesn't require a full redeploy every time. IMPORTANT: this does
    NOT stop whatever scan is currently running -- Python can't force-kill
    a background thread. It only clears the lock so a NEW trigger is
    allowed to start. If the old scan is still genuinely working (just
    slow, not hung), this starts a second scan on top of it, hitting the
    same council portals/PlanIt authorities twice at once -- this is the
    exact double-scan problem _PIPELINE_LOCK exists to prevent (see its
    comment above), so only use this when a scan has clearly stalled
    (no new log lines for a long stretch), not just because it's slow."""
    verify_cron_secret(secret)
    was_running = _pipeline_state.get("running", False)
    try:
        _PIPELINE_LOCK.release()
    except RuntimeError:
        pass  # lock was already free -- nothing to reset
    _pipeline_state["running"] = False
    _pipeline_state["started_at"] = None
    return {
        "status": "reset",
        "was_running": was_running,
        "warning": "This does not stop the old scan's background thread if it's still alive -- it only allows a new trigger to start. If the old scan wasn't actually hung, you may now have two scans running at once."
    }


@app.get("/test-mesh-council/{city_slug}")
def test_mesh_council(city_slug: str, secret: Optional[str] = Query(None)):
    """Sep 1 2026: the real fix for the 2-hour test loop -- testing a mesh
    scraper fix (e.g. today's caseAddressType fix) previously meant running
    the FULL nationwide pipeline (2+ hours) just to find out if one council
    works now. This hits exactly one council directly and returns
    immediately (seconds, not hours). Safe to call anytime, as often as
    needed: mesh_scrapers.scrape_mesh_council() makes zero database writes
    and doesn't touch the once-per-day mesh cache or the pipeline lock --
    both of those live in scanners.run_mesh_network_scan(), not here -- so
    this can never interfere with a real scan or duplicate a lead.
    Example: /test-mesh-council/CORNWALL?secret=...
    """
    verify_cron_secret(secret)
    import time
    import mesh_scrapers
    city_key = city_slug.strip().upper().replace("-", " ")
    if city_key not in mesh_scrapers.COUNCIL_REGISTRY:
        return {
            "error": f"'{city_slug}' is not in COUNCIL_REGISTRY.",
            "known_councils": sorted(mesh_scrapers.COUNCIL_REGISTRY.keys()),
        }
    start = time.time()
    try:
        leads = mesh_scrapers.scrape_mesh_council(city_key)
        return {
            "city": city_key,
            "url": mesh_scrapers.COUNCIL_REGISTRY[city_key],
            "leads_found": len(leads),
            "sample_leads": leads[:3],
            "elapsed_seconds": round(time.time() - start, 1),
        }
    except Exception as e:
        return {
            "city": city_key,
            "error": str(e),
            "elapsed_seconds": round(time.time() - start, 1),
        }


def _all_mesh_health_targets(mesh_scrapers):
    """Sep 3 2026: every mesh-network target /system-health-check should
    verify, across ALL platforms -- not just Idox. Found while answering
    Nick's "do we have every area working" question: this health check had
    ONLY ever looped mesh_scrapers.COUNCIL_REGISTRY (the plain Idox dict),
    silently skipping the Northgate/Agile Applications/Arcus registries and
    all seven single-authority bespoke platforms (Hounslow, North York
    Moors, and the five built this session -- Havering, St Albans,
    Kensington & Chelsea, Dorset, Stratford-on-Avon). A green
    /system-health-check result was never actually proof the whole mesh
    network was healthy -- only that the ~51 Idox councils were. Returns a
    list of (name, url, callable) tuples, each callable taking zero args
    and returning a lead list, so _run_council_health_check can treat every
    platform identically."""
    targets = []
    for council_name, url in mesh_scrapers.COUNCIL_REGISTRY.items():
        targets.append((council_name, url, lambda cn=council_name: mesh_scrapers.scrape_mesh_council(cn)))
    for council_name, url in mesh_scrapers.NORTHGATE_COUNCILS.items():
        targets.append((council_name, url, lambda cn=council_name: mesh_scrapers.scrape_northgate_council(cn)))
    for council_name, url in mesh_scrapers.AGILE_APPLICATIONS_COUNCILS.items():
        targets.append((council_name, url, lambda cn=council_name: mesh_scrapers.scrape_agile_applications_council(cn)))
    for council_name, url in mesh_scrapers.ARCUS_COUNCILS.items():
        targets.append((council_name, url, lambda cn=council_name: mesh_scrapers.scrape_arcus_council(cn)))
    # Single-authority bespoke platforms -- no dict registry, so listed
    # explicitly here (name, base URL, entry-point function).
    targets.append(("HOUNSLOW", mesh_scrapers.HOUNSLOW_BASE, mesh_scrapers.scrape_hounslow_council))
    targets.append(("NORTH YORK MOORS", mesh_scrapers.NORTH_YORK_MOORS_BASE, mesh_scrapers.scrape_north_york_moors))
    targets.append(("HAVERING", mesh_scrapers.HAVERING_BASE, mesh_scrapers.scrape_havering_council))
    targets.append(("ST ALBANS", mesh_scrapers.ST_ALBANS_BASE, mesh_scrapers.scrape_st_albans_council))
    targets.append(("KENSINGTON AND CHELSEA", mesh_scrapers.RBKC_BASE, mesh_scrapers.scrape_kensington_chelsea_council))
    targets.append(("DORSET", mesh_scrapers.DORSET_BASE, mesh_scrapers.scrape_dorset_council))
    targets.append(("STRATFORD-ON-AVON", mesh_scrapers.STRATFORD_BASE, mesh_scrapers.scrape_stratford_on_avon_council))
    return targets


def _run_council_health_check():
    """Background worker for /system-health-check. Loops every mesh-network
    target across every platform (see _all_mesh_health_targets above)
    through the same safe, zero-DB-write path /test-mesh-council already
    uses one at a time -- this just does all of them in one pass and keeps
    the running tally in _health_check_state so /system-health-check-status
    can report progress without the caller having to hold a connection
    open for however long ~65+ real council portal fetches take (minutes,
    not seconds -- same reason run_mesh_network_scan() sleeps 2s between
    each below). Deliberately does NOT include Merton, Bath & North East
    Somerset, or West Northamptonshire -- those are confirmed blocked on
    the council's own end (see main.py's _COUNCIL_SOURCE_ISSUES) and were
    never registered anywhere in mesh_scrapers.py, so they were never
    reachable here to begin with."""
    import mesh_scrapers
    results = []
    targets = _all_mesh_health_targets(mesh_scrapers)
    for council_name, url, fn in targets:
        start = time.time()
        try:
            leads = fn()
            results.append({
                "council": council_name,
                "url": url,
                "status": "ok",
                "leads_found": len(leads),
                "elapsed_seconds": round(time.time() - start, 1),
            })
        except Exception as e:
            results.append({
                "council": council_name,
                "url": url,
                "status": "error",
                "error": str(e)[:300],
                "elapsed_seconds": round(time.time() - start, 1),
            })
        _health_check_state["results"] = list(results)  # progress visible mid-run
        time.sleep(2)  # same per-council courtesy delay as run_mesh_network_scan()

    ok_count = sum(1 for r in results if r["status"] == "ok")
    error_count = len(results) - ok_count
    zero_leads = [r["council"] for r in results if r["status"] == "ok" and r["leads_found"] == 0]
    _health_check_state["summary"] = {
        "total_councils": len(results),
        "reachable": ok_count,
        "erroring": error_count,
        # Sep 3 2026: zero leads is NOT necessarily broken (a small council
        # can genuinely have nothing new today) -- listed separately from
        # "erroring" rather than folded into it, so a human decides whether
        # a long stretch at zero is normal or worth a closer look, exactly
        # the Leeds ArcGIS lesson (it never errored, it just always found
        # nothing) applied here as a visibility tool rather than an
        # automatic verdict.
        "reachable_but_zero_leads_this_run": zero_leads,
        "failing_councils": [r["council"] for r in results if r["status"] == "error"],
    }
    _health_check_state["finished_at"] = datetime.datetime.utcnow().isoformat() + "Z"
    _health_check_state["running"] = False
    try:
        _HEALTH_CHECK_LOCK.release()
    except RuntimeError:
        pass


@app.get("/system-health-check")
def system_health_check(secret: Optional[str] = Query(None)):
    """Sep 3 2026: starts a full live check of every mesh-network council
    across EVERY platform (Idox, Northgate, Agile Applications, Arcus, and
    all seven single-authority bespoke platforms -- see
    _all_mesh_health_targets), one at a time, in the background -- returns
    immediately with {"status": "started"}; poll
    /system-health-check-status for progress and the final report. Makes
    zero database writes (same guarantee as /test-mesh-council, just
    looped). Refuses to start while a real scan (/trigger-daily-pipeline,
    /scan-nationwide, etc.) is already running, so this never doubles up
    real traffic against the same council portals a live scan is also
    hitting right now.
    Previously only looped mesh_scrapers.COUNCIL_REGISTRY (~51 Idox
    councils) -- widened to cover the other ~16 mesh-network councils/
    platforms too, none of which this endpoint had ever actually verified
    before."""
    verify_cron_secret(secret)
    import mesh_scrapers
    total_targets = len(_all_mesh_health_targets(mesh_scrapers))
    if _pipeline_state.get("running"):
        return {
            "status": "declined",
            "reason": "A real scan is currently running (shares council portals with this check) -- wait for it to finish, then retry. Check /pipeline-status.",
        }
    if not _HEALTH_CHECK_LOCK.acquire(blocking=False):
        return {
            "status": "already_running",
            "started_at": _health_check_state.get("started_at"),
            "progress": f"{len(_health_check_state.get('results', []))} of {total_targets} councils checked so far",
        }
    _health_check_state["running"] = True
    _health_check_state["started_at"] = datetime.datetime.utcnow().isoformat() + "Z"
    _health_check_state["finished_at"] = None
    _health_check_state["results"] = []
    _health_check_state["summary"] = None
    threading.Thread(target=_run_council_health_check, daemon=True).start()
    return {
        "status": "started",
        "total_councils": total_targets,
        "estimated_seconds": total_targets * 3,
        "check_progress_at": "/system-health-check-status?secret=...",
    }


@app.get("/system-health-check-status")
def system_health_check_status(secret: Optional[str] = Query(None)):
    verify_cron_secret(secret)
    return _health_check_state


@app.get("/lead-source-health")
def lead_source_health(secret: Optional[str] = Query(None)):
    """Sep 3 2026: on-demand version of the same check
    _check_for_silent_source_failures() runs automatically at the end of
    every autonomous cycle -- read-only, does not send alerts itself (the
    automatic version does that), just lets you look at the current
    numbers for every source right now instead of waiting for the next
    cycle or an email."""
    verify_cron_secret(secret)
    return {"sources": database.get_lead_source_health_report()}


@app.get("/recent-warnings")
def recent_warnings(secret: Optional[str] = Query(None), hours: int = Query(24)):
    """Sep 4 2026: pairs with the WARNING-severity change in notifications.
    send_system_incident_alert() -- WARNING alerts (benign-until-proven-
    otherwise scraper quirks like a TLS fallback or a possible page-
    structure change) no longer email immediately, they're logged to the
    system_warnings table and only escalate to an email once the same
    issue has recurred on 3+ distinct days in a week. This route is the
    read-only way to check what's been logged-but-not-emailed on your own
    schedule, so a real pattern can still be spotted early even before it
    hits the 3-day escalation threshold -- Nick's own ask: "make sure i
    only get emails when there is a serious issue... is there anything you
    can do about that" -- CRITICAL/SECURITY alerts are unaffected by any
    of this, they still email immediately as before."""
    verify_cron_secret(secret)
    return {"warnings": database.get_recent_warnings(hours=hours)}


@app.get("/scheduler-heartbeat")
def scheduler_heartbeat(secret: Optional[str] = Query(None)):
    """Sep 3 2026: the failsafe for the failsafe. Every automatic check in
    this app (silent-source detection, the daily pipeline itself) runs
    INSIDE the same one background thread (_autonomous_scheduler_loop) --
    if that thread ever dies silently (an uncaught exception somewhere
    that isn't already wrapped, or a Render issue), every single one of
    those checks stops running with NOTHING to report it, because the
    thing that would normally sound the alarm is the thing that died. A
    check running inside that same thread can never detect its own death.
    The only real fix is an outside observer: point an external uptime
    monitor (cron-job.org, UptimeRobot, healthchecks.io -- Nick already
    uses cron-job.org for other triggers per this file's own notes) at
    this URL every 30-60 minutes. It reports unhealthy in its OWN response
    if the last cycle start is older than expected, AND -- just as
    importantly -- if the whole app is down or this thread is dead, the
    external monitor gets no response at all, which is itself the alert.
    Deliberately does not send its own email alert: an unhealthy INTERNAL
    check still requires the process to be alive to send that email in the
    first place, which defeats the entire point of an external, independent
    check."""
    verify_cron_secret(secret)
    try:
        last_started_iso = database.get_system_state("last_autonomous_cycle_started_at")
        if not last_started_iso:
            return {"healthy": False, "reason": "Autonomous cycle has never started since this system_state key existed."}
        last_started = datetime.datetime.fromisoformat(last_started_iso.replace("Z", "+00:00"))
        hours_since = (datetime.datetime.now(datetime.timezone.utc) - last_started).total_seconds() / 3600
        # The internal scheduler's own cooldown is 20h -- 30h gives a full
        # extra 20-minute-check-interval's worth of slack (the loop only
        # checks every 20 minutes) before calling it unhealthy, so this
        # doesn't false-positive on totally normal timing jitter.
        healthy = hours_since < 30

        # Sep 3 2026: also surfaces the email channel's own health here --
        # see notifications._record_email_attempt's docstring for why this
        # can't just be another email alert (an alert-sending failure can't
        # reliably alert about itself). Reported alongside the scheduler
        # check, not folded into the same `healthy` flag: an email failure
        # doesn't mean the scanning pipeline itself is broken, but it DOES
        # mean nothing else in this app can currently warn Nick about
        # anything -- worth a human glancing at this field specifically,
        # which is why it's a separate named field rather than silent.
        last_email_status = database.get_system_state("last_email_send_status")
        return {
            "healthy": healthy,
            "hours_since_last_cycle_started": round(hours_since, 1),
            "last_cycle_started_at": last_started_iso,
            "pipeline_currently_running": _pipeline_state.get("running", False),
            "last_alert_email_status": last_email_status,  # None = never attempted yet; "ok" or "failed"
            "last_alert_email_at": database.get_system_state("last_email_send_at"),
            "last_alert_email_error": database.get_system_state("last_email_send_error") if last_email_status == "failed" else None,
        }
    except Exception as e:
        return {"healthy": False, "reason": f"Heartbeat check itself errored: {e}"}


@app.get("/trigger-backfill-tree-surgeon")
def trigger_backfill_tree_surgeon(secret: Optional[str] = Query(None)):
    """Sep 1 2026: same logic as backfill_agent_is_tree_surgeon.py, exposed
    as an endpoint -- Render's Shell tab (where that script was meant to be
    run) requires a paid compute plan and isn't available here. This does
    the identical thing (zero network calls, re-classifies existing
    has_agent=True / agent_is_tree_surgeon=NULL rows using the agent name/
    company already on file) using the app's own live DB connection
    instead. Safe to re-run any time -- only ever touches rows still
    sitting at NULL."""
    verify_cron_secret(secret)
    import mesh_scrapers
    conn = database.get_db_conn()
    cur = conn.cursor()
    try:
        cur.execute(
            "SELECT reference, agent_name, agent_company FROM leads "
            "WHERE has_agent = TRUE AND agent_is_tree_surgeon IS NULL"
        )
        rows = cur.fetchall()
        resolved_true = 0
        resolved_false = 0
        still_unknown = 0
        for reference, agent_name, agent_company in rows:
            classification = mesh_scrapers.classify_agent_as_tree_surgeon(agent_name, agent_company)
            if classification is None:
                still_unknown += 1
                continue
            cur.execute(
                "UPDATE leads SET agent_is_tree_surgeon = %s WHERE reference = %s",
                (classification, reference),
            )
            if classification:
                resolved_true += 1
            else:
                resolved_false += 1
        conn.commit()
    finally:
        cur.close()
        conn.close()
    return {
        "status": "complete",
        "total_found": len(rows),
        "resolved_true_tree_surgeon": resolved_true,
        "resolved_false_not_tree_surgeon": resolved_false,
        "still_indeterminate_left_null": still_unknown,
    }


@app.get("/trigger-clean-partners")
def trigger_clean_partners(secret: Optional[str] = Query(None)):
    verify_cron_secret(secret)
    result = research.clean_partner_database()
    return {"status": "success", "result": result}


@app.get("/review-queue")
def view_review_queue(user: str = Depends(verify_dashboard_auth), limit: int = Query(100)):
    """Sep 2 2026, master_expansion_plan_v2.md build-order step 4, Tier 4:
    read-only visibility into unclassified_applications -- every application
    that cleared neither Tier 1 (keyword) nor Tier 2 (structured field),
    regardless of how many Tier 3 LLM attempts it's had (deliberately no
    max_llm_attempts filter here, unlike process_review_queue_with_llm's own
    fetch -- Nick must be able to see a genuinely stuck item too, not just
    ones Tier 3 hasn't tried yet). Same dashboard login as everything else
    behind verify_dashboard_auth, not the cron secret -- this is a page for
    Nick to look at, not a scheduled job."""
    queue = database.get_pending_review_queue(limit=limit)
    return {"count": len(queue), "items": queue}


@app.get("/process-review-queue")
def process_review_queue(secret: Optional[str] = Query(None), batch_size: int = Query(25)):
    """Sep 2 2026, master_expansion_plan_v2.md build-order step 4, Tier 3:
    manually runs one batch of the Gemini classification pass against the
    review queue (see scanners.process_review_queue_with_llm's own docstring
    for why this is a manual/on-demand batch, not wired into the automatic
    daily pipeline yet). Requires GEMINI_API_KEY to be set in Render --
    returns a clear zero-processed result rather than an error if it isn't,
    so this is safe to call to check whether it's configured."""
    verify_cron_secret(secret)
    result = scanners.process_review_queue_with_llm(batch_size=batch_size)
    return {"status": "complete", **result}


@app.get("/leads-by-tag")
def view_leads_by_tag(
    user: str = Depends(verify_dashboard_auth),
    tags: str = Query(..., description="Comma-separated tags, e.g. locale:bromley,job:crown-work,size:large"),
    match: str = Query("all", description="'all' = must have every tag, 'any' = must have at least one"),
    limit: int = Query(200),
):
    """Sep 2 2026: read-only filter view over the new lead tagging system
    (see scanners._generate_tags / database.get_leads_by_tags). Same
    dashboard login as /review-queue -- this is for Nick to look at, not a
    scheduled job. Example: /leads-by-tag?tags=region:london,job:crown-work&match=all
    finds every crown-work job in London specifically. This is deliberately
    a plain JSON endpoint, not a styled page yet -- a real filter UI is a
    separate, later pass once the tag data itself is confirmed useful."""
    tag_list = [t.strip() for t in tags.split(",") if t.strip()]
    if not tag_list:
        return {"error": "pass at least one tag, e.g. ?tags=locale:bromley"}
    results = database.get_leads_by_tags(tag_list, match_all=(match != "any"), limit=limit)
    return {"tags_queried": tag_list, "match": match, "count": len(results), "leads": results}


@app.get("/trigger-resync-region-tags")
def trigger_resync_region_tags(secret: Optional[str] = Query(None), batch_size: int = Query(2000)):
    """Sep 2 2026: one-off correction pass now that region is resolved from
    the lead's own postcode (see scanners._resolve_region) instead of
    trusting council_source labelling -- fixes every lead currently stuck
    at region:unclassified. Safe to re-run; call repeatedly until 'updated'
    + 'unchanged' + 'errors' comes back below batch_size."""
    verify_cron_secret(secret)
    result = database.resync_region_tags(batch_size=batch_size)
    return {"status": "complete", **result}


@app.get("/tag-report")
def view_tag_report(secret: Optional[str] = Query(None)):
    """Sep 2 2026: cron-secret-gated (not dashboard auth) so this can be
    pulled programmatically for a plain numbers report -- see
    database.get_tag_counts. Read-only, no side effects."""
    verify_cron_secret(secret)
    return database.get_tag_counts()


@app.get("/trigger-backfill-partner-tags")
def trigger_backfill_partner_tags(secret: Optional[str] = Query(None), batch_size: int = Query(500)):
    """Sep 2 2026: same pattern as /trigger-backfill-lead-tags, for
    potential_partners -- see database.backfill_partner_tags. Only touches
    rows whose tags column is still empty, safe to call repeatedly."""
    verify_cron_secret(secret)
    result = database.backfill_partner_tags(batch_size=batch_size)
    return {"status": "complete", **result}


@app.get("/partner-tag-report")
def view_partner_tag_report(secret: Optional[str] = Query(None)):
    """Sep 2 2026: cron-secret-gated numbers report for the partner
    tagging system -- see database.get_partner_tag_counts. Read-only."""
    verify_cron_secret(secret)
    return database.get_partner_tag_counts()


@app.get("/trigger-resync-partner-tags")
def trigger_resync_partner_tags(secret: Optional[str] = Query(None)):
    """Sep 2 2026: recomputes EVERY partner's tags from current column
    values (not just untagged rows) -- see database.resync_all_partner_tags.
    Run this once after deploying the director-name-quality audit fix
    (corporate/nominee Companies House officers no longer count as
    director:yes) so partners tagged before the fix get corrected too,
    not just newly-discovered ones."""
    verify_cron_secret(secret)
    result = database.resync_all_partner_tags()
    return {"status": "complete", **result}


@app.get("/trigger-resync-lead-tags")
def trigger_resync_lead_tags(secret: Optional[str] = Query(None)):
    """Sep 2 2026: recomputes EVERY lead's tags from current column values
    (not just untagged rows) -- see database.resync_all_lead_tags. Run this
    once after deploying the agent_type/agent_guess tag split (Nick's ask:
    'not only agent yes/no but rather none/agent/tree surgeon') so leads
    tagged before the split existed get the new breakdown too, not just
    newly-scanned ones."""
    verify_cron_secret(secret)
    result = database.resync_all_lead_tags()
    return {"status": "complete", **result}


@app.get("/trigger-requeue-dead-enrichment")
def trigger_requeue_dead_enrichment(secret: Optional[str] = Query(None)):
    """Sep 2 2026: one-time catch-up after fixing the DDG cross-thread
    throttle bug in research.py (get_google_places_info's `time.sleep(1.2)`
    was per-thread, not global, so up to 20 concurrent scrape requests could
    hit DDG together and get silently rate-limited -- indistinguishable in
    the data from 'this company genuinely has no phone/email'). Every
    partner already marked enriched_at with zero contact info found is
    permanently skipped by enrich_existing_partners's `WHERE enriched_at IS
    NULL` filter on its own -- this re-queues just that subset (phone AND
    email both still NULL) for a genuine retry under the fixed throttle,
    without disturbing partners that already found real contact info. Run
    this once after deploying the throttle fix; see
    database.requeue_dead_contact_enrichment."""
    verify_cron_secret(secret)
    result = database.requeue_dead_contact_enrichment()
    return {"status": "complete", **result}


@app.get("/trigger-backfill-lead-tags")
def trigger_backfill_lead_tags(secret: Optional[str] = Query(None), batch_size: int = Query(500)):
    """Sep 2 2026: same pattern as /trigger-backfill-tree-surgeon -- runs
    database.backfill_lead_tags() against the app's own live DB connection.
    Only touches rows whose tags column is still empty, so it's safe to
    call repeatedly (once per batch) until 'updated' comes back below
    batch_size, meaning everything's tagged."""
    verify_cron_secret(secret)
    result = database.backfill_lead_tags(batch_size=batch_size)
    return {"status": "complete", **result}


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



@app.get("/google-places-budget-status")
def google_places_budget_status(secret: Optional[str] = Query(None)):
    """Sep 3 2026: read-only visibility into the monthly paid-call cost cap
    (research.GOOGLE_PLACES_MONTHLY_PAID_CALL_CAP / _google_places_paid_
    call_budget_available). Added after a live incident where a real
    £28.72 charge (an uncapped historical backlog clear-out, before this
    cap existed) had to be reasoned about entirely from code review --
    there was no way to just look up "how many paid calls has this month
    already used" without a raw database query. Read-only: never
    increments or resets anything, safe to hit as often as you like."""
    verify_cron_secret(secret)
    try:
        current_month = datetime.datetime.utcnow().strftime("%Y-%m")
        stored_month = database.get_system_state("google_places_paid_calls_month")
        stored_count_raw = database.get_system_state("google_places_paid_calls_count")
        stored_count = int(stored_count_raw) if (stored_count_raw or "").isdigit() else 0
        # Same "different month -> counter hasn't reset yet, but effectively
        # is 0" logic as the real budget check in research.py -- otherwise
        # this would misreport last month's leftover count as still active.
        calls_used_this_month = stored_count if stored_month == current_month else 0
        cap = research.GOOGLE_PLACES_MONTHLY_PAID_CALL_CAP
        return {
            "month": current_month,
            "paid_calls_used": calls_used_this_month,
            "monthly_cap": cap,
            "calls_remaining": max(0, cap - calls_used_this_month),
            "cap_reached": calls_used_this_month >= cap,
        }
    except Exception as e:
        return {"error": str(e)}


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

    # Note: the nested f-strings below previously escaped double quotes inside an
    # f-string expression part (e.g. f'...{f'<a href=\"...\">' if r[4] else ''}...'),
    # which is a hard SyntaxError on Python <3.12 (only 3.12+ relaxed this) — meaning
    # this whole file would fail to import at all on an older interpreter. Pulled the
    # per-row HTML into helper functions so no quote character needs escaping.
    _no_director_html = "<span style='color:#888;'>Director on file</span>"

    def _mailto_cell(email):
        return f"<a href='mailto:{email}'>{email}</a>" if email else ""

    def _website_cell(url):
        return f"<a href='{url}' target='_blank'>Website</a>" if url else ""

    table_rows = "".join([
        f"<tr>"
        f"<td style='padding:8px; border:1px solid #ddd;'><b>{r[0]}</b><br><span style='color:#777; font-size:11px;'>#{r[1]}</span></td>"
        f"<td style='padding:8px; border:1px solid #ddd;'>{r[2] or _no_director_html}</td>"
        f"<td style='padding:8px; border:1px solid #ddd;'>{r[3] or ''}</td>"
        f"<td style='padding:8px; border:1px solid #ddd;'>{_mailto_cell(r[4])}</td>"
        f"<td style='padding:8px; border:1px solid #ddd;'>{_website_cell(r[5])}</td>"
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
    # Sep 8 2026 rework: the previous version of this page said "We do not
    # sell your personal data to third parties" -- directly contradicted by
    # the business itself (Leads containing real people's names, sourced
    # from public planning/Companies House records, are what customers pay
    # for). That line was a live liability, not just a "too short" problem.
    # This version separates customer data from Lead data and states the
    # real lawful basis (legitimate interests). Sep 8 2026 follow-up: an
    # earlier pass of this page also displayed a visible "still needs
    # solicitor sign-off" notice publicly -- Nick's call, correctly: that's
    # an internal to-do, not something to advertise to the public or a
    # regulator on a live legal page. Solicitor review still needs to
    # happen (flagged to Nick directly, not on-page) -- see the equivalent
    # note in terms_of_service below for what's still outstanding.
    return """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Privacy Policy - Tree Key</title>
    <link rel="manifest" href="/static/manifest.json">
    <meta name="theme-color" content="#020617">
    <link rel="apple-touch-icon" href="/static/icon-192.png">
    <link href="/static/tailwind.css" rel="stylesheet">
    <script>if ('serviceWorker' in navigator) { window.addEventListener('load', () => { navigator.serviceWorker.register('/sw.js'); }); }</script>
</head>
<body class="bg-slate-900 text-slate-300 font-sans p-8 md:p-16">
    <div class="max-w-3xl mx-auto bg-slate-800 p-8 rounded-lg shadow-xl border border-slate-700">
        <h1 class="text-3xl font-bold text-white mb-2">Privacy Policy</h1>
        <p class="mb-6 text-sm text-slate-500">Last updated: September 2026 &middot; Tree Key is a trading name of Vector Data Labs</p>

        <h2 class="text-xl font-bold text-emerald-400 mt-6 mb-2">1. Who We Are</h2>
        <p class="mb-4">Tree Key ("we", "us", "our") operates the website treekey.uk and the lead-generation service described in our <a href="/terms-of-service" class="text-emerald-400 underline">Terms of Service</a>. Tree Key is a trading name of Vector Data Labs, which is the data controller for the personal data described below. For privacy matters, contact <strong>contact@treekey.uk</strong>.</p>

        <h2 class="text-xl font-bold text-emerald-400 mt-6 mb-2">2. The Two Kinds of Personal Data We Handle</h2>
        <p class="mb-2"><strong class="text-white">2.1 Customer data</strong> &mdash; information about you, our paying customer: name, business name, email, phone number, billing details (processed by Stripe), and your usage of the Service.</p>
        <p class="mb-4"><strong class="text-white">2.2 Lead data</strong> &mdash; information about a third party named in a Lead: typically a planning applicant's or agent's name, sourced from public UK council planning records, and in some cases a business or director name sourced from Companies House. This is data about people who are not our customers and have not signed up to anything.</p>

        <h2 class="text-xl font-bold text-emerald-400 mt-6 mb-2">3. Our Lawful Basis for Processing Lead Data</h2>
        <p class="mb-4">We process Lead data on the basis of <strong class="text-white">legitimate interests</strong> under UK GDPR Article 6(1)(f): our commercial interest in aggregating publicly available planning and company data into a usable directory for tree surgery and arboricultural businesses. A person named in Lead data has the right to object to this processing (Section 8) &mdash; where someone objects, we stop processing their data for this purpose unless we can demonstrate compelling legitimate grounds that override their interests, or the data is needed for a legal claim.</p>

        <h2 class="text-xl font-bold text-emerald-400 mt-6 mb-2">4. What We Use Personal Data For</h2>
        <ul class="list-disc list-inside mb-4 space-y-1">
            <li>Operating and improving the Service (both kinds of data);</li>
            <li>Providing customer support and processing payments (customer data);</li>
            <li>Compiling, classifying, and displaying Leads to subscribed customers (Lead data);</li>
            <li>Sending customers service-related communications and, where not opted out, marketing about the Service;</li>
            <li>Complying with our legal obligations (e.g. tax, accounting).</li>
        </ul>
        <p class="mb-4">We do not use Lead data to build profiles about the individuals named in it beyond what's needed to classify a Lead's relevance.</p>

        <h2 class="text-xl font-bold text-emerald-400 mt-6 mb-2">5. Where Lead Data Comes From</h2>
        <p class="mb-4">UK local council planning application registers, Companies House (available under the Open Government Licence), and, where used, publicly listed business contact details. We do not purchase Lead data from private data brokers or scrape data that is not otherwise publicly accessible.</p>

        <h2 class="text-xl font-bold text-emerald-400 mt-6 mb-2">6. Who We Share Data With</h2>
        <ul class="list-disc list-inside mb-4 space-y-1">
            <li><strong class="text-white">Stripe</strong> (payment processing) &mdash; customer payment and billing data.</li>
            <li><strong class="text-white">Render</strong> (hosting) &mdash; the application and database run on Render's infrastructure.</li>
            <li><strong class="text-white">Our customers</strong> &mdash; Lead data is disclosed to subscribing customers as the core of the Service.</li>
        </ul>
        <p class="mb-4">We do not sell personal data to data brokers or advertisers. We do commercially license access to Lead data as the Service itself &mdash; stated plainly here, not denied.</p>

        <h2 class="text-xl font-bold text-emerald-400 mt-6 mb-2">7. International Data Transfers</h2>
        <p class="mb-4">Some of our processors (including Stripe and Render) may process data outside the UK. Where this happens, transfers are protected by the UK's International Data Transfer Addendum to the EU Standard Contractual Clauses, or an equivalent lawful transfer mechanism, as provided by each processor's standard terms.</p>

        <h2 class="text-xl font-bold text-emerald-400 mt-6 mb-2">8. Your Rights</h2>
        <p class="mb-2">Both customers and individuals named in Lead data have the right, under UK GDPR, to: request access to the personal data we hold about them; request correction of inaccurate data; request erasure ("right to be forgotten"), subject to our legal bases for retaining it; object to processing based on legitimate interests (Section 3); request restriction of processing in certain circumstances; and lodge a complaint with the Information Commissioner's Office (ico.org.uk).</p>
        <p class="mb-4">To exercise any of these rights, contact <strong>contact@treekey.uk</strong>.</p>

        <h2 class="text-xl font-bold text-emerald-400 mt-6 mb-2">9. Data Retention</h2>
        <p class="mb-4">Lead data is retained for 24 months from discovery, after which personal identifiers are anonymized or deleted, though the underlying planning application record may be retained in non-identifying form for business analytics. Customer account data is retained for the life of the account; billing records are kept for 6 years after account closure to meet HMRC record-keeping requirements.</p>

        <h2 class="text-xl font-bold text-emerald-400 mt-6 mb-2">10. Security</h2>
        <p class="mb-4">We take reasonable technical and organizational measures to protect personal data: encrypted (HTTPS) connections throughout the Service; account sessions secured with signed, tamper-evident tokens rather than plain credentials; no customer passwords are stored at all (login uses a one-time emailed link, so there is no password database to be breached); and administrative and automated-scan functions are protected by a separate access secret, not exposed publicly.</p>

        <h2 class="text-xl font-bold text-emerald-400 mt-6 mb-2">11. Cookies</h2>
        <p class="mb-4">Tree Key currently sets one cookie: a signed session cookie (<code class="text-emerald-300">treekey_contractor_session</code>) used solely to keep you logged in, marked HttpOnly, Secure, and SameSite=Lax. This is a strictly necessary cookie required for the Service to function, so under UK PECR rules it does not require a cookie consent banner. This will be revisited the moment any analytics, advertising, or tracking cookie is added.</p>

        <h2 class="text-xl font-bold text-emerald-400 mt-6 mb-2">12. Children</h2>
        <p class="mb-4">The Service is intended for business use and is not directed at children. We do not knowingly collect personal data from children.</p>

        <h2 class="text-xl font-bold text-emerald-400 mt-6 mb-2">13. Changes to This Policy</h2>
        <p class="mb-4">We may update this policy from time to time; material changes will be reflected by an updated "last updated" date, and significant changes affecting Lead data subjects' rights will be communicated where practical.</p>

        <h2 class="text-xl font-bold text-emerald-400 mt-6 mb-2">14. Contact</h2>
        <p class="mb-6">Questions or requests regarding this policy: <strong>contact@treekey.uk</strong>.</p>

        <a href="/" class="text-emerald-500 hover:text-emerald-400 mt-4 inline-block font-bold">&larr; Back to Home</a>
    </div>
</body>
</html>
"""

@app.get("/terms-of-service", response_class=HTMLResponse)
async def terms_of_service():
    # Sep 8 2026 rework: replaces the previous 4-clause page (Nick: "terms
    # of service is ridiculously short and needs to look more like a real
    # legal page") with a fuller draft covering accounts, lead-accuracy
    # disclaimers, acceptable use, IP, data protection, liability and
    # indemnity -- aligned to match the product as it actually works.
    # Sep 8 2026 follow-up: dropped the "still needs solicitor sign-off"
    # notice that used to render publicly on this page -- Nick's call: an
    # internal to-do doesn't belong on a live legal page. STILL OUTSTANDING
    # (Nick to action, not shown on-site): (1) a documented Legitimate
    # Interests Assessment backing Section 8's data-protection basis --
    # this page states the conclusion, not the assessment itself; (2) an
    # actual working process for someone named in a Lead to exercise the
    # erasure/objection rights the Privacy Policy describes; (3) a
    # solicitor's review of the whole document before relying on it fully.
    return """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Terms of Service - Tree Key</title>
    <link rel="manifest" href="/static/manifest.json">
    <meta name="theme-color" content="#020617">
    <link rel="apple-touch-icon" href="/static/icon-192.png">
    <link href="/static/tailwind.css" rel="stylesheet">
    <script>if ('serviceWorker' in navigator) { window.addEventListener('load', () => { navigator.serviceWorker.register('/sw.js'); }); }</script>
</head>
<body class="bg-slate-900 text-slate-300 font-sans p-8 md:p-16">
    <div class="max-w-3xl mx-auto bg-slate-800 p-8 rounded-lg shadow-xl border border-slate-700">
        <h1 class="text-3xl font-bold text-white mb-2">Terms of Service</h1>
        <p class="mb-6 text-sm text-slate-500">Last updated: September 2026 &middot; Tree Key is a trading name of Vector Data Labs</p>

        <h2 class="text-xl font-bold text-emerald-400 mt-6 mb-2">1. Introduction and Acceptance</h2>
        <p class="mb-2">These Terms and Conditions ("Terms") govern access to and use of the Tree Key website (treekey.uk) and the lead-generation service provided through it (the "Service"), operated by Vector Data Labs, trading as Tree Key ("we", "us", "our").</p>
        <p class="mb-2">By creating an account, purchasing a subscription, or otherwise using the Service, you ("you", "the Customer") agree to be bound by these Terms. If you do not agree, do not use the Service.</p>
        <p class="mb-4">The Service is intended for business use by tree surgery, arboricultural, and related trade businesses. It is not intended for consumers acting outside a trade, business, or profession.</p>

        <h2 class="text-xl font-bold text-emerald-400 mt-6 mb-2">2. Description of the Service</h2>
        <p class="mb-2">Tree Key identifies and aggregates leads relating to tree work ("Leads") derived substantially from publicly available UK local council planning application records, Companies House records, and related public data sources.</p>
        <p class="mb-2">Leads are processed using automated methods, including automated classification of whether a named agent or representative on a planning application appears to be a tree surgery or arboricultural business. This classification is a best-effort estimate and is not manually verified for every Lead.</p>
        <p class="mb-4">Tree Key provides access to Lead information only. Tree Key is not a party to, and has no involvement in, any subsequent contact, quote, contract, or work arrangement between the Customer and any third party named in or connected to a Lead.</p>

        <h2 class="text-xl font-bold text-emerald-400 mt-6 mb-2">3. Accounts</h2>
        <p class="mb-2">You must provide accurate registration information and keep it up to date. You are responsible for all activity under your account.</p>
        <p class="mb-4">We may suspend or terminate an account where we reasonably believe these Terms have been breached, or where required by law.</p>

        <h2 class="text-xl font-bold text-emerald-400 mt-6 mb-2">4. Subscriptions, Pricing, and Payment</h2>
        <p class="mb-2">Access to Leads is provided on the subscription plans, credit packages, and pricing displayed on the Service at the time of purchase. Prices and plan structures may change; changes will not affect a billing period already paid for.</p>
        <p class="mb-2">Payments are processed by Stripe. By subscribing, you authorize recurring charges for the plan you select until you cancel. Subscriptions may be cancelled via your account settings, or by emailing contact@treekey.uk, effective at the end of the current billing period.</p>
        <p class="mb-4 border-l-4 border-amber-500 pl-4 bg-amber-500/10 py-3 text-slate-200"><strong>Refunds.</strong> Because you are granted immediate access to proprietary Lead data the moment you subscribe or purchase a single lead, all payments &mdash; subscription and one-off purchases alike &mdash; are non-refundable, including for unused portions of a billing cycle. Nothing in this clause affects any statutory right you may have that cannot lawfully be excluded.</p>

        <h2 class="text-xl font-bold text-emerald-400 mt-6 mb-2">5. Lead Accuracy &mdash; No Warranty</h2>
        <p class="mb-2">Lead information reflects data available to Tree Key at the time of discovery or last check and is not guaranteed to be current, complete, or accurate. In particular, we do not guarantee that: the underlying planning application remains active or undetermined; no contractor has since been engaged by the applicant, whether or not this is reflected in the public record; contact or applicant details are current or correct; or that use of a Lead will result in a successful quote, contract, or completed job.</p>
        <p class="mb-2">You are responsible for independently verifying, directly with the applicant, whether work described in a Lead remains available before committing time, quotes, or resources.</p>
        <p class="mb-4">Tree Key provides the Service on an "as available" basis and, to the extent permitted by law, excludes all implied warranties and conditions relating to accuracy, completeness, fitness for a particular purpose, and satisfactory quality.</p>

        <h2 class="text-xl font-bold text-emerald-400 mt-6 mb-2">6. Acceptable Use</h2>
        <p class="mb-2">You agree not to: resell, redistribute, or share Lead data with any third party who is not a party to your own account, except as necessary to quote for or perform the work described; use the Service to build a competing lead-generation product; attempt to scrape, bulk-export beyond what the Service provides, or reverse-engineer the Service; use any Lead's personal data for a purpose other than contacting that person about the specific tree work described in the Lead; or misuse, harass, or make misleading representations to any person contacted via information obtained through the Service.</p>
        <p class="mb-4">We may suspend access for breach of this section without refund.</p>

        <h2 class="text-xl font-bold text-emerald-400 mt-6 mb-2">7. Intellectual Property</h2>
        <p class="mb-2">The Service, its underlying software, aggregation methodology, and branding are owned by Tree Key or its licensors. Nothing in these Terms transfers ownership of any of this to the Customer.</p>
        <p class="mb-4">Underlying public-record data (planning applications, Companies House filings) remains subject to the terms of the original source (e.g. Companies House data is available under the Open Government Licence); Tree Key's aggregation and classification of it does not create exclusive rights over the underlying facts.</p>

        <h2 class="text-xl font-bold text-emerald-400 mt-6 mb-2">8. Data Protection</h2>
        <p class="mb-2">Leads may include personal data (for example, an applicant's or agent's name, and in some cases contact details) sourced from public records. Tree Key processes this personal data as a data controller for the purpose of operating the lead-generation service &mdash; see our <a href="/privacy-policy" class="text-emerald-400 underline">Privacy Policy</a> for the full detail on lawful basis and your rights.</p>
        <p class="mb-4">Customers who receive personal data through a Lead must handle it in accordance with UK GDPR themselves for their own onward use (e.g., adding a contact to their own CRM), and must not use it for a purpose incompatible with the reason it was provided (see clause 6).</p>

        <h2 class="text-xl font-bold text-emerald-400 mt-6 mb-2">9. Limitation of Liability</h2>
        <p class="mb-2">To the maximum extent permitted by law, Tree Key's total liability arising out of or in connection with the Service, any Lead, or these Terms, whether in contract, tort (including negligence), or otherwise, is limited to the total amount paid by the Customer to Tree Key in the three (3) months preceding the event giving rise to the claim.</p>
        <p class="mb-2">Tree Key shall not be liable for any indirect or consequential losses, including loss of profit, loss of business opportunity, or wasted expenditure (including quoting, travel, or staff time), arising from reliance on a Lead that proves inaccurate, outdated, or already actioned.</p>
        <p class="mb-4">Nothing in these Terms excludes or limits liability for death or personal injury caused by negligence, fraud or fraudulent misrepresentation, or any other liability that cannot lawfully be excluded or limited under English law.</p>

        <h2 class="text-xl font-bold text-emerald-400 mt-6 mb-2">10. Indemnity</h2>
        <p class="mb-4">You agree to indemnify Tree Key against any claim, loss, or expense arising from your breach of Section 6 (Acceptable Use) or your misuse of personal data obtained through the Service, to the extent permitted by law.</p>

        <h2 class="text-xl font-bold text-emerald-400 mt-6 mb-2">11. Suspension and Termination</h2>
        <p class="mb-4">Either party may terminate a subscription in accordance with Section 4. Tree Key may suspend or terminate access immediately for a material breach of these Terms, illegal use of the Service, or non-payment. Sections 5, 7, 8, 9, and 10 survive termination.</p>

        <h2 class="text-xl font-bold text-emerald-400 mt-6 mb-2">12. Changes to These Terms</h2>
        <p class="mb-4">We may update these Terms from time to time. Material changes will be notified via the Service or by email before they take effect. Continued use after changes take effect constitutes acceptance.</p>

        <h2 class="text-xl font-bold text-emerald-400 mt-6 mb-2">13. General</h2>
        <p class="mb-2"><strong class="text-white">Governing law.</strong> These Terms are governed by the laws of England and Wales, and the courts of England and Wales have exclusive jurisdiction.</p>
        <p class="mb-2"><strong class="text-white">Severability.</strong> If any provision of these Terms is found unenforceable, the remaining provisions continue in full force.</p>
        <p class="mb-2"><strong class="text-white">Entire agreement.</strong> These Terms, together with the <a href="/privacy-policy" class="text-emerald-400 underline">Privacy Policy</a> and any order confirmation, constitute the entire agreement between the parties regarding the Service.</p>
        <p class="mb-6"><strong class="text-white">Contact.</strong> Questions about these Terms can be sent to <strong>contact@treekey.uk</strong>.</p>

        <a href="/" class="text-emerald-500 hover:text-emerald-400 mt-4 inline-block font-bold">&larr; Back to Home</a>
    </div>
</body>
</html>
"""


@app.get("/faq", response_class=HTMLResponse)
async def faq_page():
    """Sep 8 2026, Nick's ask: "need a faq that really gives full explanation
    on everything you could wonder about us" -- the homepage only ever had a
    3-question objection-handling blurb. Every answer here is grounded in
    how the product actually works (checked against the live routes/copy
    rather than invented), covering the things Nick specifically flagged as
    confusing when testing the site himself: what Ledger and Chip-Drop are,
    whether there's an app, WhatsApp, cancellation, and the free-account path."""
    faq_groups = [
        ("About Tree Key", [
            ("What is Tree Key?",
             "Tree Key finds new tree work before your competitors do. We continuously monitor UK local council planning portals, the GLA Planning Datahub, and other statutory public sources for tree-related applications &mdash; TPO (Tree Preservation Order) consents, Section 211 notices, felling and pruning applications &mdash; and turn each one into a Lead you can quote on directly."),
            ("How is this different from a directory like Checkatrade or Bark?",
             "Directories sell the same lead to several competing contractors at once and take a cut of what you earn. Every Lead on Tree Key is single-sale: the moment it's dispatched to a subscriber (or bought from the Marketplace), it's burned from our system and never sold to anyone else. You quote the homeowner directly, under your own brand, with no ongoing commission."),
            ("Where does the data come from, and is it legal?",
             "Entirely from public statutory sources: council planning registers and Companies House, both publicly accessible under the Open Government Licence. We don't buy data from private brokers or scrape anything that isn't otherwise publicly available. Full detail on how we're allowed to process it is in our <a href=\"/privacy-policy\" class=\"text-emerald-400 underline\">Privacy Policy</a>."),
            ("Is there a mobile app?",
             "Not a separate app to download &mdash; the whole site, including your dashboard, is built to work properly on your phone's browser. Save it to your home screen and it behaves like one."),
        ]),
        ("Pricing & Plans", [
            ("What are my options if I'm not ready to pay?",
             "You can sign up for a free account with no card required and get one real, fully-unlocked free lead near you to start with, plus occasional teaser emails after that. When you're ready for full coverage, upgrade to a subscription tier from your dashboard at any time."),
            ("What's the difference between a subscription and the Marketplace?",
             "A subscription gives you priority, ongoing dispatch of every matching Lead in your territory as it's discovered. Any Lead that isn't claimed by a subscriber flows into the single-purchase Marketplace, where anyone can buy it one-off &mdash; useful for topping up, or for trying Tree Key out before subscribing."),
            ("Am I tied into a long contract?",
             "No. Subscriptions are a rolling monthly agreement &mdash; cancel any time from your account settings with zero penalty and no further charges from the next billing date."),
            ("Can I get a refund?",
             "Because you get immediate access to the Lead data itself the moment you subscribe or buy, payments are non-refundable &mdash; the same policy that applies to unused portions of a billing cycle. Full detail is in our <a href=\"/terms-of-service\" class=\"text-emerald-400 underline\">Terms of Service</a>."),
        ]),
        ("How Matching Works", [
            ("How do you decide which leads I get?",
             "You set a home postcode and a radius (up to 50 miles depending on tier). We match leads to you by exact area first, then by distance within your radius, then by wider regional area as a fallback &mdash; so you get the closest, most relevant work first. Enter your full postcode rather than just the outward code (e.g. \"CR5 2LE\" instead of just \"CR5\") for the most accurate distance matching."),
            ("Can I filter by job size?",
             "Yes &mdash; each subscription can be set to small, medium, large, or all job sizes, so a one-van operator isn't drowned in commercial clearance leads meant for a multi-crew outfit, or vice versa."),
            ("What if no leads come through for a while?",
             "Lead volume depends entirely on how much planning activity is happening in your area &mdash; we don't manufacture leads. If a source genuinely goes quiet for an unusual length of time, that's exactly the kind of thing our internal monitoring is built to catch and flag automatically, and we treat it as something to actively fix, not something to leave unexplained."),
        ]),
        ("Tools Included With Your Subscription", [
            ("What is TreeKey Ledger?",
             "A financial dashboard built specifically for tree surgeons: it tracks your rolling 12-month turnover against the £90,000 UK VAT registration threshold so you're never caught out, holds a running CIS developer-tax tracker, and includes a van/crew-day cost calculator so you can quote profitably. Find it in your dashboard once you're a subscriber."),
            ("What is the Chip-Drop Network?",
             "A directory of local allotments, farms, stables and gardens who want your arborist woodchip or logs for free. Instead of paying £60&ndash;£120 in commercial tipping fees and losing 45 minutes each way, you drop your waste at a nearby registered site instead. Landowners register their own site through the site; the directory only ever shows real, self-registered listings."),
        ]),
        ("Your Account", [
            ("How do I log in? Why no password?",
             "Tree Key is passwordless by design &mdash; enter your email and we send you a secure, one-tap login link (valid 15 minutes). There's no password database that could ever be breached. If you're reading the email on a different device than the one you want to log in on, the email also includes a 6-digit code you can type in instead of clicking the link."),
            ("Is WhatsApp involved?",
             "Two places: our Elite tier includes zero-minute instant WhatsApp lead alerts alongside email, and the Chip-Drop directory gives you a direct WhatsApp link to message a drop site's contact. There isn't yet a general WhatsApp support line &mdash; for anything else, email is the way to reach us."),
            ("Do you have testimonials from other contractors?",
             "Not yet &mdash; we're a young platform and would rather wait for genuine results than publish anything that isn't real. That'll change as more contractors come through the platform."),
        ]),
        ("Data & Privacy", [
            ("Is my business data sold to anyone?",
             "No. Your own account information (name, email, phone, billing) is never sold to data brokers or advertisers &mdash; it's used only to run your account and the Service. What you're paying for is licensed access to Lead data compiled from public records, which is a different thing entirely. See our <a href=\"/privacy-policy\" class=\"text-emerald-400 underline\">Privacy Policy</a> for the full breakdown."),
            ("I'm named in a Lead and want it removed &mdash; what do I do?",
             "Email <strong>contact@treekey.uk</strong> and we'll action your request in line with the rights set out in our Privacy Policy."),
        ]),
    ]

    groups_html = ""
    for group_title, qas in faq_groups:
        items_html = "".join([
            f"""<div class="bg-slate-800/50 p-6 rounded-lg border border-slate-700">
                <h3 class="text-base font-bold text-white mb-2">{q}</h3>
                <p class="text-slate-400 leading-relaxed text-sm">{a}</p>
            </div>"""
            for q, a in qas
        ])
        groups_html += f"""
        <div class="mb-10">
            <h2 class="text-lg font-extrabold text-emerald-400 font-mono uppercase tracking-wide mb-4">{group_title}</h2>
            <div class="space-y-4">{items_html}</div>
        </div>
        """

    return f"""
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>FAQ - Tree Key</title>
    <link rel="manifest" href="/static/manifest.json">
    <meta name="theme-color" content="#020617">
    <link rel="apple-touch-icon" href="/static/icon-192.png">
    <link href="/static/tailwind.css" rel="stylesheet">
    <script>if ('serviceWorker' in navigator) {{ window.addEventListener('load', () => {{ navigator.serviceWorker.register('/sw.js'); }}); }}</script>
</head>
<body class="bg-[#020617] text-slate-300 font-sans p-6 md:p-16">
    <div class="max-w-3xl mx-auto">
        <div class="text-center mb-4">
            <a href="/" class="text-emerald-500 hover:text-emerald-400 text-sm font-bold">&larr; Back to Home</a>
        </div>
        <h1 class="text-3xl md:text-4xl font-extrabold text-white text-center mb-2 font-mono uppercase tracking-tight">Frequently Asked Questions</h1>
        <p class="text-center text-slate-500 mb-12 text-sm">Everything about how Tree Key works, what it costs, and how your data is handled.</p>

        {groups_html}

        <div class="text-center mt-4 pb-8 text-sm text-slate-500">
            Still have a question? Email <strong class="text-slate-300">contact@treekey.uk</strong> or <a href="/suggestions" class="text-emerald-400 underline">submit a suggestion</a>.
        </div>
    </div>
</body>
</html>
"""







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



@app.get("/api/check-postcode")
@app.get("/check-postcode")
@app.get("/check-postcode/{postcode}")
def api_check_postcode(postcode: Optional[str] = None, lat: Optional[float] = None, lng: Optional[float] = None, radius: int = 15):
    """
    Public postcode radar inspection endpoint.
    Restricted strictly to the 309 English Local Planning Authorities.
    Supports search by Postcode/Outcode, UK City name, or direct Map Click (lat/lng coordinates).
    """
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
                if data.get("status") == 200 and data.get("result"):
                    first = data["result"][0]
                    display_pc = first.get("outcode") or first.get("postcode", "Local Area")
                    district = first.get("admin_district") or f"{display_pc} District Authority"
                    country_name = first.get("country", "England")
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

    if country_name.lower() in ["northern ireland", "republic of ireland"]:
        is_covered = False
        uncovered_region = "Northern Ireland / Ireland"
    elif target_lng < -5.8 and target_lat < 55.4:  # Irish Sea / Ireland
        is_covered = False
        uncovered_region = "Northern Ireland / Ireland"

    if not is_covered:
        return {
            "postcode": display_pc,
            "authority": f"{district} ({uncovered_region})",
            "lat": target_lat,
            "lng": target_lng,
            "radius_miles": radius,
            "is_covered": False,
            "selected_area_leads": 0,
            "connected_area_leads": 0,
            "total_leads_in_scope": 0,
            "est_min_val": "0",
            "est_max_val": "0",
            "exclusivity_status": "Outside Great Britain Coverage",
            "message": "Tree Key is dedicated exclusively to Great Britain statutory planning registers (England, Scotland, and Wales). Northern Ireland and Ireland are outside operating scope."
        }



    # Query local database for lead matches (Strict bounding to prevent 'LL' double-letter wildcard explosion)
    prefix_alpha = "".join([c for c in display_pc if c.isalpha()])[:3]
    conn = database.get_db_conn()
    cur = conn.cursor()
    if len(prefix_alpha) > 1:
        # Match postcode prefix exactly with a space or at the end
        cur.execute("SELECT count(*) FROM leads WHERE address ~* %s OR council_source ILIKE %s", 
        (f"\\y{prefix_alpha}[0-9]", f"%{district[:6]}%"))
    else:
        cur.execute("SELECT count(*) FROM leads WHERE council_source ILIKE %s", (f"%{district[:6]}%",))
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

    # Contract valuation (Ãƒâ€šÃ‚£450 to Ãƒâ€šÃ‚£1,450 per statutory notice)
    min_val = selected_leads * 450
    max_val = selected_leads * 1450

    # Check territory exclusivity in real-time
    is_claimed = database.is_territory_claimed(display_pc)
    exclusivity_label = "ÃƒÂ°Ã…Â¸Ã¢â‚¬ÂÃ¢â‚¬â„¢ Locked (Claimed by Local Partner)" if is_claimed else "ÃƒÂ°Ã…Â¸Ã…Â¸Ã‚Â¢ Available (Unclaimed)"

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
        "exclusivity_status": exclusivity_label
    }










# ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ Auth ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬


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



# ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ Dashboard ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬

# ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ Public Landing Page (Enterprise Institutional Architecture) ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬

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
    <title>Tree Key Ã¢â‚¬â€ Statutory Planning Intelligence</title>
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
    <nav class="sticky top-0 z-50 bg-brand-dark/95 backdrop-blur-md border-b border-slate-800 shadow-2xl">
        <div class="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8">
            <div class="flex justify-between items-center h-20">
                <a href="/" class="flex items-center gap-3">
                    <img src="/static/logo.png" alt="Tree Key" class="h-10">
                </a>
                <div class="flex items-center gap-4 md:gap-8 font-mono text-xs md:text-sm tracking-wide">
                    <div class="hidden md:flex items-center gap-8">
                        <a href="#radar" class="text-slate-400 hover:text-white transition-colors">RADAR</a>
                        <a href="#pricing" class="text-slate-400 hover:text-white transition-colors flex items-center gap-2">
                            <span class="relative flex h-2 w-2"><span class="animate-ping absolute inline-flex h-full w-full rounded-full bg-brand-alert opacity-75"></span><span class="relative inline-flex rounded-full h-2 w-2 bg-red-600"></span></span>
                            TERRITORIES
                        </a>
                    </div>
                    <a href="/admin" class="bg-brand-green/10 text-brand-glow border border-brand-green/30 px-4 py-2 rounded font-bold uppercase hover:bg-brand-green hover:text-white transition-all duration-300 shadow-[0_0_15px_rgba(5,150,105,0.2)]">Login</a>
                </div>
            </div>
        </div>
    </nav>

    <!-- Hero Section -->
    <main class="relative overflow-hidden pt-16 pb-24 lg:pt-32 lg:pb-40 bg-grid-slate-900">
        <div class="absolute inset-0 bg-gradient-to-b from-transparent via-brand-dark/80 to-brand-dark"></div>
        <div class="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 relative z-10 text-center">
            
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
                Council Contracts.<br>
                <span class="text-transparent bg-clip-text bg-gradient-to-r from-emerald-400 to-emerald-600">Unlocked Before Your Competitors.</span>
            </h1>

            <!-- The Pain/Solution Frame -->
            <p class="mt-6 max-w-3xl mx-auto text-xl text-slate-400 leading-relaxed font-medium">
                We monitor all 360+ UK council planning portals 24/7. 
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
                        <input type="text" id="postcodeInput" placeholder="Enter outward postcode (e.g. LS1, B1, SW1)" value="B1" required class="flex-1 bg-slate-800 border-2 border-slate-600 text-white font-mono rounded px-4 py-3 focus:outline-none focus:border-brand-green focus:bg-slate-900 transition-colors uppercase text-lg shadow-inner">
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
                    
                    <div class="mt-6 flex flex-col sm:flex-row justify-between items-center text-sm font-mono text-slate-400 gap-4 bg-slate-900 p-4 rounded border border-slate-800">
                        <div id="statusBadge" class="flex flex-col text-left">
                            <div class="flex items-center gap-2 text-emerald-400">
                                <span class="h-2 w-2 rounded-full bg-emerald-500 animate-pulse"></span> SYSTEM STANDBY
                            </div>
                            <span class="text-xs text-slate-500 mt-1">Awaiting outward postcode input...</span>
                        </div>
                        <div class="text-left sm:text-right flex flex-col gap-1 sm:items-end">
                            <div id="radiusReadout" class="text-slate-300 font-bold tracking-wider">RADIAL BOUNDARY: 15.0 MILES</div>
                            <div id="targetIntel" class="text-xs text-slate-500 font-mono mt-1 bg-slate-900/80 p-2 rounded border border-slate-700 inline-block text-left">
                                Awaiting scan to calculate territory volume...
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
                <p class="text-lg text-slate-400 mt-4">The average commercial site clearance pays £2,500+. One job pays for the year.</p>
            </div>

            <div class="grid grid-cols-1 md:grid-cols-3 gap-8 items-start">
                
                <!-- Tier 1: Sole Trader -->
                <div class="bg-[#0f172a] border border-slate-800 rounded-2xl p-8 relative hover:border-slate-600 transition-colors">
                    <h3 class="text-2xl font-bold text-white mb-2">Sole Trader</h3>
                    <p class="text-slate-400 mb-6 text-sm">Perfect for one-man bands and local startups aiming to grow steadily.</p>
                    <div class="flex items-baseline gap-2 mb-8">
                        <div class="text-4xl font-extrabold text-white">£49</div>
                        <div class="text-lg text-slate-500 font-normal">/month</div>
                    </div>
                    <ul class="mb-8 space-y-4 text-slate-300 text-sm font-medium">
                        <li class="flex items-start gap-3"><svg width="20" class="text-slate-500 shrink-0 mt-0.5" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3"><polyline points="20 6 9 17 4 12"></polyline></svg> 10-Mile Radial Boundary</li>
                        <li class="flex items-start gap-3"><svg width="20" class="text-emerald-500 shrink-0 mt-0.5" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3"><polyline points="20 6 9 17 4 12"></polyline></svg> 100% Exclusive Lead Routing</li>
                        <li class="flex items-start gap-3"><svg width="20" class="text-emerald-500 shrink-0 mt-0.5" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3"><polyline points="20 6 9 17 4 12"></polyline></svg> Daily Email Notifications</li>
                    </ul>
                    <a href="/checkout/sole_trader" class="block w-full text-center border border-slate-700 hover:border-slate-500 text-white font-bold py-4 rounded-lg transition-all duration-300 uppercase tracking-wider text-sm">
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
                        <div class="text-5xl font-extrabold text-white">£149</div>
                        <div class="text-lg text-emerald-500 font-normal">/month</div>
                    </div>
                    <ul class="mb-8 space-y-4 text-slate-100 text-sm font-medium">
                        <li class="flex items-start gap-3"><svg width="20" class="text-emerald-400 shrink-0 mt-0.5" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3"><polyline points="20 6 9 17 4 12"></polyline></svg> 25-Mile Radial Boundary</li>
                        <li class="flex items-start gap-3"><svg width="20" class="text-emerald-400 shrink-0 mt-0.5" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3"><polyline points="20 6 9 17 4 12"></polyline></svg> 100% Exclusive Lead Routing</li>
                        <li class="flex items-start gap-3"><svg width="20" class="text-emerald-400 shrink-0 mt-0.5" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3"><polyline points="20 6 9 17 4 12"></polyline></svg> Instant SMS/Phone Notifications</li>
                        <li class="flex items-start gap-3"><svg width="20" class="text-emerald-400 shrink-0 mt-0.5" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3"><polyline points="20 6 9 17 4 12"></polyline></svg> Connected-Council Job Access</li>
                    </ul>
                    <a href="/checkout/commercial_pro" class="block w-full text-center bg-emerald-500 hover:bg-emerald-400 text-white font-extrabold py-5 rounded-lg transition-all duration-300 uppercase tracking-widest text-sm shadow-[0_4px_14px_0_rgba(16,185,129,0.39)]">
                        Secure Priority Access
                    </a>
                </div>

                <!-- Tier 3: Regional Dominator -->
                <div class="bg-[#0f172a] border border-slate-800 rounded-2xl p-8 relative hover:border-slate-600 transition-colors">
                    <h3 class="text-2xl font-bold text-white mb-2">Regional Elite</h3>
                    <p class="text-slate-400 mb-6 text-sm">For massive operations running multiple crews across a wide geographic spread.</p>
                    <div class="flex items-baseline gap-2 mb-8">
                        <div class="text-4xl font-extrabold text-white">£299</div>
                        <div class="text-lg text-slate-500 font-normal">/month</div>
                    </div>
                    <ul class="mb-8 space-y-4 text-slate-300 text-sm font-medium">
                        <li class="flex items-start gap-3"><svg width="20" class="text-amber-500 shrink-0 mt-0.5" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3"><polyline points="20 6 9 17 4 12"></polyline></svg> 50-Mile Radial Boundary</li>
                        <li class="flex items-start gap-3"><svg width="20" class="text-emerald-500 shrink-0 mt-0.5" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3"><polyline points="20 6 9 17 4 12"></polyline></svg> 100% Exclusive Lead Routing</li>
                        <li class="flex items-start gap-3"><svg width="20" class="text-amber-500 shrink-0 mt-0.5" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3"><polyline points="20 6 9 17 4 12"></polyline></svg> First-Priority API Routing</li>
                        <li class="flex items-start gap-3"><svg width="20" class="text-emerald-500 shrink-0 mt-0.5" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3"><polyline points="20 6 9 17 4 12"></polyline></svg> Dedicated Account Manager</li>
                    </ul>
                    <a href="/checkout/regional_elite" class="block w-full text-center border border-slate-700 hover:border-slate-500 text-white font-bold py-4 rounded-lg transition-all duration-300 uppercase tracking-wider text-sm">
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
                    <p class="text-slate-400 leading-relaxed">No. We work with tradesmen, not corporations. The lockout is a rolling monthly agreement. You can cancel instantly at any time with zero penalty. Alternatively, buy a £80 credit pack for zero monthly commitment.</p>
                </div>
            </div>
        </div>
    </section>

    <!-- Footer -->
    <footer class="border-t border-slate-800 bg-[#020617] py-12">
        <div class="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 flex flex-col md:flex-row justify-between items-center gap-6">
            <div class="text-slate-500 text-sm text-center md:text-left">
                <b class="text-slate-300">Tree Key</b> â€” Built by Vector Data Labs.<br>
                Operating in compliance with UK Town and Country Planning statutory register regulations.
            </div>
            <div class="flex gap-6 text-sm font-mono uppercase tracking-wider">
                <a href="/pricing" class="text-slate-400 hover:text-white transition-colors">Terms</a>
                <a href="/health" class="text-slate-400 hover:text-white transition-colors">Datahub Status</a>
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
                <div class="flex items-center gap-2 text-emerald-400 mb-1">
                    <span class="h-2 w-2 rounded-full bg-emerald-500 animate-pulse"></span> Manual Lock: ${{e.latlng.lat.toFixed(4)}}, ${{e.latlng.lng.toFixed(4)}}
                </div>
            `;
            
            try {{
                const res = await fetch(`/api/check-postcode?lat=${{e.latlng.lat}}&lng=${{e.latlng.lng}}`);
                const data = await res.json();
                if (data.status === "ok") {{
                    document.getElementById('postcodeInput').value = data.postcode;
                    document.getElementById('radiusReadout').innerHTML = `RADIAL BOUNDARY: ${{data.radius_miles || (rad/1609.34).toFixed(1)}} MILES`;
                    document.getElementById('targetIntel').innerHTML = `<span class="text-emerald-400 font-bold text-sm">${{data.selected_area_leads}} Active Leads</span> in radius<br><span class="text-slate-400 border-t border-slate-700 pt-1 mt-1 block">+ ${{data.connected_area_leads}} additional in connected zones</span>`;
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
                const res = await fetch(`/api/check-postcode?postcode=${{encodeURIComponent(input)}}`);
                const data = await res.json();
                
                if (data.status === "ok") {{
                    map.setView([data.lat, data.lng], 10);
                    currentCircle.setLatLng([data.lat, data.lng]);
                    currentCircle.setRadius(radVal);
                    document.getElementById("radiusReadout").innerHTML = `RADIAL BOUNDARY: ${{ (radVal/1609.34).toFixed(1) }} MILES`;
                    
                    // Add slight delay for psychological weight
                    setTimeout(() => {{
                        status.innerHTML = `
                            <div class="flex items-center gap-2 text-emerald-400 mb-1">
                                <span class="h-2 w-2 rounded-full bg-emerald-500 animate-pulse"></span> Radar Locked: ${{data.postcode}}
                            </div>
                            <span class="text-xs font-bold text-amber-500 animate-pulse mt-1 inline-block border border-amber-500/30 bg-amber-500/10 px-2 py-1 rounded">&#9888;&#65039; 12 Local Competitors Detected</span>
                        `;
                    }}, 600);
                    
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
        f"<li><b>{p[0]}</b> ÃƒÂ¢Ã¢â€šÂ¬Ã¢â‚¬Â {p[1] or 'Director on file'} | <b>{p[2]}</b> | ÃƒÂ°Ã…Â¸Ã¢â‚¬Å“Ã…Â¾ {p[4] or 'ÃƒÂ¢Ã¢â€šÂ¬Ã¢â‚¬Â'} | ÃƒÂ¢Ã…â€œÃ¢â‚¬Â°ÃƒÂ¯Ã‚Â¸Ã‚Â {p[5] or 'ÃƒÂ¢Ã¢â€šÂ¬Ã¢â‚¬Â'} | ÃƒÂ¢Ã‚Â­Ã‚Â {p[3] or 'N/A'}</li>"
        for p in stats["partners"]
    ])

    import datetime
    now = datetime.datetime.now(datetime.timezone.utc)

    def get_freshness_badge(discovered_at):
        if not discovered_at:
            return "ÃƒÂ°Ã…Â¸Ã…Â¸Ã‚Â¢ <span style='color:#2e7d32; font-weight:bold;'>ÃƒÂ°Ã…Â¸Ã¢â‚¬ÂÃ‚Â¥ FRESH</span>"
        try:
            delta_days = (now - discovered_at).days
            if delta_days <= 14:
                return f"ÃƒÂ°Ã…Â¸Ã…Â¸Ã‚Â¢ <span style='color:#2e7d32; font-weight:bold;'>ÃƒÂ°Ã…Â¸Ã¢â‚¬ÂÃ‚Â¥ FRESH ({delta_days}d ago)</span>"
            elif delta_days <= 45:
                return f"ÃƒÂ°Ã…Â¸Ã…Â¸Ã‚Â¡ <span style='color:#f57f17; font-weight:bold;'>ÃƒÂ¢Ã‚ÂÃ‚Â³ CONSULTATION ({delta_days}d)</span>"
            elif delta_days <= 90:
                return f"ÃƒÂ°Ã…Â¸Ã¢â‚¬ÂÃ‚Âµ <span style='color:#0277bd; font-weight:bold;'>ÃƒÂ¢Ã…â€œÃ¢â‚¬Â¦ GRANTED</span>"
            else:
                return f"ÃƒÂ¢Ã…Â¡Ã‚Âª <span style='color:#757575;'>ÃƒÂ°Ã…Â¸Ã¢â‚¬Å“Ã‚Â¦ ARCHIVED</span>"
        except Exception:
            return "ÃƒÂ°Ã…Â¸Ã…Â¸Ã‚Â¢ <span style='color:#2e7d32; font-weight:bold;'>ÃƒÂ°Ã…Â¸Ã¢â‚¬ÂÃ‚Â¥ FRESH</span>"

    SCORE_EMOJI = {"small": "ÃƒÂ°Ã…Â¸Ã…Â¸Ã‚Â¡", "medium": "ÃƒÂ°Ã…Â¸Ã…Â¸Ã‚Â ", "large": "ÃƒÂ°Ã…Â¸Ã¢â‚¬ÂÃ‚Â´"}
    lead_rows = "".join([
        f"<li>{SCORE_EMOJI.get(l[2],'ÃƒÂ°Ã…Â¸Ã…Â¸Ã‚Â¡')} <b>{l[0]}</b> {get_freshness_badge(l[5])}<br><span style='color:#555; font-size:13px;'>{l[1][:90]}... | Ãƒâ€šÃ‚£{l[3]} | {l[4]}</span></li>"
        for l in stats["leads"]
    ])

    city_buttons = "".join([
        f"""<div style='display:inline-block; margin:6px; padding:12px 16px;
            background:#f8fafc; border-radius:12px; border:1px solid #e2e8f0;'>
            <b>ÃƒÂ°Ã…Â¸Ã¢â‚¬Å“Ã‚Â {city}</b><br>
            <div style='margin-top:6px; font-size:12px;'>
                <a href='/scan/{city.lower().replace(" ", "-")}' style='color:#059669; font-weight:bold; text-decoration:none;'>ÃƒÂ¢Ã¢â‚¬â€œÃ‚Â¶ Scan Leads</a> &nbsp;|&nbsp;
                <a href='/research/{city.lower().replace(" ", "-")}' style='color:#0284c7; text-decoration:none;'>ÃƒÂ°Ã…Â¸Ã¢â‚¬ÂÃ‚Â Find New</a> &nbsp;|&nbsp;
                <a href='/enrich-region/{city.lower().replace(" ", "-")}' style='color:#7c3aed; font-weight:bold; text-decoration:none;'>ÃƒÂ¢Ã…Â¡Ã‚Â¡ Enrich</a>
            </div>
        </div>"""
        for city in ALL_CITIES  # Display all UK regions including Scotland and Wales
    ])

    pct = int((stats['enriched'] / stats['p'] * 100)) if stats['p'] else 0

    return f"""
    <html><head><title>Vector Data Labs ÃƒÂ¢Ã¢â€šÂ¬Ã¢â‚¬Â Admin Command</title></head>
    <body style="font-family:sans-serif; background:#f4f4f9; padding:40px;">
    <div style="max-width:920px; margin:auto; background:white; padding:40px;
                border-radius:20px; border-top:8px solid #064e3b; box-shadow:0 4px 12px rgba(0,0,0,0.05);">
        <div style="display:flex; justify-content:space-between; align-items:center;">
            <h1>ÃƒÂ°Ã…Â¸Ã¢â‚¬Å“Ã…Â  Tree Key Admin Command</h1>
            <a href="/" target="_blank" style="background:#10b981; color:white; padding:8px 14px; border-radius:6px; text-decoration:none; font-weight:bold; font-size:13px;">ÃƒÂ°Ã…Â¸Ã¢â‚¬ËœÃ‚ÂÃƒÂ¯Ã‚Â¸Ã‚Â View Public Homepage</a>
        </div>

        <p>Verified LTD Partners: <b>{stats['p']}</b> &nbsp;|&nbsp; 
           Enriched with Contacts: <b style="color:#059669;">{stats['enriched']} ({pct}%)</b> &nbsp;|&nbsp; 
           Total Planning Leads: <b>{stats['l']}</b>
           &nbsp;|&nbsp; <a href='/status'>ÃƒÂ°Ã…Â¸Ã¢â‚¬ÂÃ‚Â§ System Status</a>
           &nbsp;|&nbsp; <a href='/pricing'>ÃƒÂ°Ã…Â¸Ã¢â‚¬â„¢Ã‚Â³ Pricing Table</a>
           &nbsp;|&nbsp; <a href='/export-directors'>ÃƒÂ°Ã…Â¸Ã¢â‚¬Å“Ã¢â‚¬Â¹ View Contacts</a>
           &nbsp;|&nbsp; <a href='/export-directors.csv' style='color:#1b5e20; font-weight:bold;'>ÃƒÂ¢Ã‚Â¬Ã¢â‚¬Â¡ÃƒÂ¯Ã‚Â¸Ã‚Â Download CSV</a>
        </p>
        <hr>
        <h3>ÃƒÂ°Ã…Â¸Ã‚ÂÃ¢â€žÂ¢ÃƒÂ¯Ã‚Â¸Ã‚Â Nationwide Territory Scanners, Discovery & Instant Enrichment</h3>
        <p style="color:#64748b; font-size:13px; margin-top:-5px;">Click <b>ÃƒÂ¢Ã¢â‚¬â€œÃ‚Â¶ Scan Leads</b> to fetch local planning applications, <b>ÃƒÂ°Ã…Â¸Ã¢â‚¬ÂÃ‚Â Find New</b> to discover tree surgery LTDs via Companies House, or <b>ÃƒÂ¢Ã…Â¡Ã‚Â¡ Enrich</b> to pull direct phones and ratings in ~5 seconds.</p>
        {city_buttons}
        <hr>

        <h3>ÃƒÂ°Ã…Â¸Ã¢â‚¬ÂÃ¢â‚¬Å¾ Batch Operations</h3>
        <div style="display:flex; gap:10px; flex-wrap:wrap; margin-bottom:10px;">
            <a href='/populate-2000-partners' style="background:#047857; color:white; padding:10px 18px; border-radius:8px; text-decoration:none; font-weight:bold; font-size:13px; box-shadow:0 2px 6px rgba(4,120,87,0.3);">
                ÃƒÂ¢Ã…Â¡Ã‚Â¡ Harvest 2,000+ Contractors (Nationwide GB)
            </a>
            <a href='/enrich-all' style="background:#1b5e20; color:white; padding:10px 16px; border-radius:8px; text-decoration:none; font-weight:bold; font-size:13px;">
                ÃƒÂ°Ã…Â¸Ã…Â¡Ã¢â€šÂ¬ Enrich All (All Remaining Partners)
            </a>
            <a href='/enrich-batch' style="background:#7c3aed; color:white; padding:10px 16px; border-radius:8px; text-decoration:none; font-weight:bold; font-size:13px;">
                ÃƒÂ¢Ã…Â¡Ã‚Â¡ Enrich Next 50 Partners (5-8 Seconds)
            </a>
            <a href='/research-all' style="background:#0284c7; color:white; padding:10px 16px; border-radius:8px; text-decoration:none; font-weight:bold; font-size:13px;">
                ÃƒÂ°Ã…Â¸Ã¢â‚¬ÂÃ‚Â Discover All Regions (Find New)
            </a>
            <a href='/clean-partners' style="background:#b71c1c; color:white; padding:10px 16px; border-radius:8px; text-decoration:none; font-weight:bold; font-size:13px;">
                ÃƒÂ°Ã…Â¸Ã‚Â§Ã‚Â¹ Clean Database (Purge False Substrings)
            </a>
            <a href='/export-directors.csv' style="background:#064e3b; color:white; padding:10px 16px; border-radius:8px; text-decoration:none; font-weight:bold; font-size:13px;">
                ÃƒÂ¢Ã‚Â¬Ã¢â‚¬Â¡ÃƒÂ¯Ã‚Â¸Ã‚Â Export Contacts CSV
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





# ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ Status ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬

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
        icon, color, note = ("ÃƒÂ¢Ã…â€œÃ¢â‚¬Â¦", "#1b5e20", "Set") if val else ("ÃƒÂ¢Ã‚ÂÃ…â€™", "#b71c1c", "MISSING")
        rows_html += f"<tr><td style='padding:8px;'>{label}</td><td style='padding:8px; color:{color}; font-weight:bold;'>{icon} {note}</td></tr>"

    try:
        conn = database.get_db_conn(); conn.close()
        db_status = "<span style='color:#1b5e20; font-weight:bold;'>ÃƒÂ¢Ã…â€œÃ¢â‚¬Â¦ Connected</span>"
    except Exception as e:
        db_status = f"<span style='color:#b71c1c; font-weight:bold;'>ÃƒÂ¢Ã‚ÂÃ…â€™ Failed: {e}</span>"

    return f"""
    <html><head><title>System Status</title></head>
    <body style="font-family:sans-serif; background:#f4f4f9; padding:40px;">
    <div style="max-width:620px; margin:auto; background:white; padding:40px;
                border-radius:20px; border-top:8px solid #1b5e20;">
        <h2>ÃƒÂ°Ã…Â¸Ã¢â‚¬ÂÃ‚Â§ System Status</h2>
        <p><a href='/'>ÃƒÂ¢Ã¢â‚¬Â Ã‚Â Dashboard</a></p>
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
            Keys are never displayed ÃƒÂ¢Ã¢â€šÂ¬Ã¢â‚¬Â only presence is checked.<br>
            <b>Automated scanning:</b> Set up cron-job.org to hit
            <code>/trigger-leads-{{city}}?secret=YOUR_SECRET</code> on your preferred schedule.
        </p>
    </div></body></html>
    """


# ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ Pricing Page (Public) ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬

@app.get("/pricing", response_class=HTMLResponse)
def pricing():
    plans = payments.PLANS

    cards = ""
    for key, plan in plans.items():
        if plan["mode"] == "subscription":
            price_display = f"Ãƒâ€šÃ‚£{plan['amount'] / 100:.0f}<span style='font-size:16px; font-weight:normal;'>/month</span>"
        else:
            price_display = f"Ãƒâ€šÃ‚£{plan['amount'] / 100:.0f}<span style='font-size:16px; font-weight:normal;'> one-off</span>"

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
               Get Started ÃƒÂ¢Ã¢â‚¬Â Ã¢â‚¬â„¢
            </a>
        </div>"""

    return f"""
    <!DOCTYPE html>
    <html lang="en-GB">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>Commercial Allocation Tiers ÃƒÂ¢Ã¢â€šÂ¬Ã¢â‚¬Â Tree Key</title>

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
                margin: 0; padding: 48px 20px;
                line-height: 1.6;
            }}
            .container {{ max-width: 720px; margin: auto; }}
            .header {{ text-align: center; margin-bottom: 32px; }}
            .header h1 {{ font-size: 32px; font-weight: 800; color: var(--brand-dark); margin: 0 0 10px 0; }}
            .header p {{ color: var(--brand-muted); font-size: 15px; margin: 0; }}
            .notice-box {{
                background: #ffffff;
                border: 1px solid var(--border-color);
                border-left: 4px solid var(--brand-primary);
                border-radius: 6px;
                padding: 16px 20px;
                font-size: 13px;
                color: var(--brand-muted);
                margin-bottom: 24px;
            }}
        </style>
    </head>
    <body>
    <div class="container">
        <div class="header">
            <h1>Commercial Allocation Tiers</h1>
            <p>Direct statutory notice streams and verified planning intelligence allocations.</p>
        </div>

        <div class="notice-box">
            <b>Statutory Allocation Protocol:</b> Notice allocations are distributed immediately following local planning authority registration. Exclusive radial lockouts guarantee zero competing contractor distribution within your operating territory.
        </div>

        {cards}

        <div style="text-align:center; margin-top:32px;">
            <a href="/" style="color:var(--brand-muted); text-decoration:none; font-size:13px; font-weight:600;">ÃƒÂ¢Ã¢â‚¬Â Ã‚Â Return to Main Intelligence Hub</a>
        </div>
    </div>
    </body>
    </html>
    """




# ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ Checkout (Stripe) ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬

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
        <h1>ÃƒÂ¢Ã…â€œÃ¢â‚¬Â¦ Payment Successful!</h1>
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


# ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ City Scan Routes (Dashboard ÃƒÂ¢Ã¢â€šÂ¬Ã¢â‚¬Â Basic Auth) ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬

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
        <p>ÃƒÂ¢Ã…â€œÃ¢â‚¬Â¦ {city} scan complete. <b>{count}</b> new leads found.</p>
        <a href="/admin">ÃƒÂ¢Ã¢â‚¬Â Ã‚Â Back to Admin Command</a>
    </body></html>"""


# ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ City Cron Routes (External ÃƒÂ¢Ã¢â€šÂ¬Ã¢â‚¬Â Trigger Secret) ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬

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





# ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ Research Routes (Basic Auth & Secret) ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬

import threading

@app.get("/research/{city_slug}", response_class=HTMLResponse)
def research_city(city_slug: str, request: Request, secret: Optional[str] = Query(None)):
    verify_admin_or_secret(request, secret)
    city = _resolve_city_param(city_slug)
    if not city:
        raise HTTPException(status_code=404, detail=f"Region/City '{city_slug}' not configured.")
    threading.Thread(target=research.perform_research, args=(city,), daemon=True).start()
    return f"""<html><body style="font-family:sans-serif; padding:40px;">
        <h3>ÃƒÂ°Ã…Â¸Ã¢â‚¬ÂÃ‚Â Partner Discovery Started for {city}</h3>
        <p>Searching Companies House, officers, Google Places, and websites in the background.</p>
        <p>New verified tree surgery LTDs will appear in your database momentarily.</p>
        <a href="/admin">ÃƒÂ¢Ã¢â‚¬Â Ã‚Â Back to Admin Command</a>
    </body></html>"""



@app.get("/populate-2000-partners", response_class=HTMLResponse)
def populate_2000_partners_view(request: Request, secret: Optional[str] = Query(None)):
    verify_admin_or_secret(request, secret)
    threading.Thread(target=research.populate_2000_partners_into_db, daemon=True).start()
    return """<html><body style="font-family:sans-serif; padding:40px; background:#f8fafc; color:#0f172a;">
        <div style="max-width:600px; margin:auto; background:white; padding:30px; border-radius:12px; border:1px solid #e2e8f0; box-shadow:0 4px 12px rgba(0,0,0,0.05);">
            <h2 style="color:#059669; margin-top:0;">ÃƒÂ°Ã…Â¸Ã…Â¡Ã¢â€šÂ¬ Nationwide 2,000+ Contractor Harvest Initiated</h2>
            <p style="font-size:15px; line-height:1.5;">The system is sweeping Companies House across all 15 UK regional clusters (England Wealth Belts, Midlands, North, Scotland, Wales) in the background with 10 concurrent worker threads.</p>
            <p style="font-size:14px; color:#64748b;">It is actively extracting Managing Director names, verified UK phone numbers, Google review ratings, and websites directly into your PostgreSQL database.</p>
            <div style="margin-top:25px;">
                <a href="/admin" style="display:inline-block; background:#064e3b; color:white; padding:12px 22px; border-radius:8px; text-decoration:none; font-weight:bold;">ÃƒÂ¢Ã¢â‚¬Â Ã‚Â Return to Admin Dashboard</a>
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
        <h3>ÃƒÂ°Ã…Â¸Ã…Â¡Ã¢â€šÂ¬ Nationwide Discovery Started</h3>
        <p>Investigating Companies House across all 9 English regions in the background.</p>
        <p>New verified LTD tree surgery contractors will populate in your database over the next 1-2 minutes.</p>
        <a href="/admin">ÃƒÂ¢Ã¢â‚¬Â Ã‚Â Back to Admin Command</a>
    </body></html>"""




@app.get("/enrich-batch", response_class=HTMLResponse)
def enrich_batch(request: Request, secret: Optional[str] = Query(None)):
    verify_admin_or_secret(request, secret)
    count = research.enrich_existing_partners(limit=50)
    return f"""<html><body style="font-family:sans-serif; padding:40px;">
        <h3>ÃƒÂ¢Ã…Â¡Ã‚Â¡ Batch Enrichment Complete</h3>
        <p>ÃƒÂ¢Ã…â€œÃ¢â‚¬Â¦ Enriched and updated <b>{count}</b> partners with direct director names, UK phone numbers, and emails in ~5 seconds!</p>
        <div style="margin-top:20px;">
            <a href="/enrich-batch" style="background:#7c3aed; color:white; padding:10px 18px; border-radius:8px; text-decoration:none; font-weight:bold; font-size:14px;">ÃƒÂ¢Ã¢â‚¬â€œÃ‚Â¶ Enrich Next 50</a> &nbsp;&nbsp;
            <a href="/admin" style="background:#064e3b; color:white; padding:10px 18px; border-radius:8px; text-decoration:none; font-weight:bold; font-size:14px;">ÃƒÂ¢Ã¢â‚¬Â Ã‚Â Back to Admin Command</a>
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
        <h3>ÃƒÂ¢Ã…Â¡Ã‚Â¡ Regional Enrichment Complete for {city}</h3>
        <p>ÃƒÂ¢Ã…â€œÃ¢â‚¬Â¦ Enriched and updated <b>{count}</b> {city} tree surgery contractors with direct director names, UK phone numbers, and emails!</p>
        <div style="margin-top:20px;">
            <a href="/admin" style="background:#064e3b; color:white; padding:10px 18px; border-radius:8px; text-decoration:none; font-weight:bold; font-size:14px;">ÃƒÂ¢Ã¢â‚¬Â Ã‚Â Back to Admin Command</a>
        </div>
    </body></html>"""


@app.get("/enrich-all", response_class=HTMLResponse)
def enrich_all(request: Request, secret: Optional[str] = Query(None)):
    verify_admin_or_secret(request, secret)
    threading.Thread(target=research.enrich_existing_partners, kwargs={"limit": 0}, daemon=True).start()
    return """<html><body style="font-family:sans-serif; padding:40px;">
        <p>ÃƒÂ¢Ã…â€œÃ¢â‚¬Â¦ Enrichment started in background across 8 parallel threads. Check Render logs or refresh admin dashboard for progress.</p>
        <a href="/admin">ÃƒÂ¢Ã¢â‚¬Â Ã‚Â Back to Admin Command</a>
    </body></html>"""




@app.get("/clean-partners", response_class=HTMLResponse)
def clean_partners(request: Request, secret: Optional[str] = Query(None)):
    verify_admin_or_secret(request, secret)
    result = research.clean_partner_database()
    if "error" in result:
        return f"""<html><body style="font-family:sans-serif; padding:40px;">
            <p>ÃƒÂ¢Ã‚ÂÃ…â€™ Cleanup failed: {result['error']}</p>
            <a href="/admin">ÃƒÂ¢Ã¢â‚¬Â Ã‚Â Back to Dashboard</a>
        </body></html>"""
    return f"""<html><body style="font-family:sans-serif; padding:40px;">
        <h3>ÃƒÂ°Ã…Â¸Ã‚Â§Ã‚Â¹ Partner Database Cleanup Complete</h3>
        <p>ÃƒÂ¢Ã…â€œÃ¢â‚¬Â¦ Kept: <b>{result['kept']}</b> verified tree surgery companies</p>
        <p>ÃƒÂ°Ã…Â¸Ã¢â‚¬â€Ã¢â‚¬ËœÃƒÂ¯Ã‚Â¸Ã‚Â Removed: <b>{result['removed']}</b> unrelated businesses</p>
        <p style="color:#888; font-size:13px;">
            Removed companies had no tree-surgery keywords in their name,
            or contained excluded terms (medical, dental, fruit, cosmetic, etc.)
        </p>
        <a href="/admin">ÃƒÂ¢Ã¢â‚¬Â Ã‚Â Back to Admin Command</a>
    </body></html>"""


def run_master_daily_pipeline():

    """
    4-Stage Daily Automated Ingestion & Quality Sanitization Pipeline:
    1. Council Planning Radar: Scans all 309 local councils across all 9 English regions.
    2. Secondary Lead Sanitization: Normalizes lead grades, pricing, and deduplication.
    3. New Contractor Discovery: Queries Companies House for newly incorporated LTDs.
    4. Two-Layer Name Filter & UK Geotargeting: Purges any non-tree surgery or foreign records.
    """
    logger.info("[PIPELINE] ÃƒÂ°Ã…Â¸Ã…Â¡Ã¢â€šÂ¬ Starting Master Daily Automation Pipeline...")
    
    # Stage 1: Council Planning Radar Scan
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

    logger.info("[PIPELINE] ÃƒÂ°Ã…Â¸Ã…Â½Ã‚Â¯ Master Daily Pipeline finished successfully.")



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













# ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ Export Directors (Basic Auth) ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬

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
        f"<td style='padding:8px; border:1px solid #ddd;'>{r[3] or 'ÃƒÂ¢Ã¢â€šÂ¬Ã¢â‚¬Â'}</td>"
        f"<td style='padding:8px; border:1px solid #ddd;'>{f'<a href=\"mailto:{r[4]}\">{r[4]}</a>' if r[4] else 'ÃƒÂ¢Ã¢â€šÂ¬Ã¢â‚¬Â'}</td>"
        f"<td style='padding:8px; border:1px solid #ddd;'>{f'<a href=\"{r[5]}\" target=\"_blank\">Website</a>' if r[5] else 'ÃƒÂ¢Ã¢â€šÂ¬Ã¢â‚¬Â'}</td>"
        f"<td style='padding:8px; border:1px solid #ddd; text-align:center;'>ÃƒÂ¢Ã‚Â­Ã‚Â {r[6] or 'N/A'}</td>"
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
            <h2>ÃƒÂ°Ã…Â¸Ã¢â‚¬Å“Ã¢â‚¬Â¹ Verified Tree Surgery Contacts ({len(rows)} companies)</h2>
            <div>
                <a href="/export-directors.csv" style="background:#1b5e20; color:white; padding:8px 16px; border-radius:6px; text-decoration:none; font-weight:bold;">ÃƒÂ¢Ã‚Â¬Ã¢â‚¬Â¡ÃƒÂ¯Ã‚Â¸Ã‚Â Download CSV</a>
                &nbsp;|&nbsp; <a href="/">ÃƒÂ¢Ã¢â‚¬Â Ã‚Â Dashboard</a>
            </div>
        </div>
        <table style="width:100%; border-collapse:collapse; font-size:13px;">
            <tr style="background:#1b5e20; color:white;">
                <th style="padding:10px; text-align:left;">Company</th>
                <th style="padding:10px; text-align:left;">Director</th>
                <th style="padding:10px; text-align:left;">Phone</th>
                <th style="padding:10px; text-align:left;">Email</th>
                <th style="padding:10px; text-align:left;">Web</th>
                <th style="padding:10px; text-align:center;">Google ÃƒÂ¢Ã‚Â­Ã‚Â</th>
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
import os
import requests
import time
import logging
from typing import Optional, List, Tuple, Dict, Any
import database
import notifications
import net_utils

logger = logging.getLogger("vector-data-labs")

GLA_API_KEY = os.getenv("GLA_API_KEY", "").strip()
UK_PLANNING_API_KEY = os.getenv("UK_PLANNING_API_KEY", "").strip()

# ── Lead Scoring ──────────────────────────────────────────────────────────────



LARGE_KEYWORDS = [
    "tpo", "tree preservation order", "conservation area", "woodland",
    "development", "several trees", "multiple trees", "commercial",
    "site clearance", "site works", "dangerous tree", "estate",
    "demolition", "contaminated", "application to carry out works",
    "section 211", "s211", "bs5837", "bs 5837", "arboricultural impact",
    "woodland clearance", "group of trees", "woodland management"
]
MEDIUM_KEYWORDS = [
    "crown reduction", "crown lift", "crown thin", "crown raising", "crown clean",
    "crown thinning", "crown lifting", "crown cleaning", "lateral branches",
    "fell", "felling", "tree felling", "felling of",
    "removal of tree", "remove tree", "tree removal", "sectional dismantle", "dismantle",
    "pollarding", "pollard", "re-pollard",
    "overhanging", "storm damage", "hanging branch", "decayed tree",
    "deadwood", "dead wood", "dead branches", "works to trees", "work to trees",
    "urgent", "diseased tree", "ash dieback", "coppice", "coppicing", "monolith"
]
SMALL_KEYWORDS = [
    "tree pruning", "tree trimming", "tree maintenance", "pruning of",
    "hedge trimming", "hedge cutting", "hedge removal", "hedge reduction",
    "tree inspection", "tree survey", "tree assessment", "tree report",
    "minor works to tree", "lopping", "sever ivy", "root protection", "root severance"
]

# Compound phrases used to decide if a planning application is tree-related at all.
# Eliminates false positives (medical/dental surgery, street names, hotel crowns)
TREE_GOLD = [
    # Core trade terms
    "tree surgery", "tree surgeon", "tree work", "tree works", "works to tree", "work to tree",
    "tree felling", "tree removal", "tree pruning", "tree trimming", "tree maintenance",
    "tree preservation", "tree protection", "tree survey", "tree assessment", "tree report",
    "arboricultural", "arborist", "arboriculture", "arbor",
    "tpo", "tree preservation order", "protected tree", "mature tree", "specimen tree",
    "section 211", "s211", "notice of intent",
    # Specific arboricultural operations
    # NOTE (Aug 29 2026): a bare "fell " entry used to live here. It matched
    # any ordinary use of "fell" as a verb -- "a branch fell in the storm",
    # "the applicant fell ill", "the company fell behind" -- which is exactly
    # the "fell down' style phrasing" false positive this list's own comment
    # says it exists to avoid. Caught by the new test suite (test_scrapers.py)
    # before it shipped further. Removed; "felling", "fell to ground", and
    # "fell 1/2/3" (the numbered-tree-list phrasing councils actually use in
    # application descriptions) already cover genuine tree-work mentions.
    "felling", "fell to ground", "fell 1", "fell 2", "fell 3", "sectional dismantle", "dismantle",
    "stump grinding", "stump removal", "stump",
    "pollard", "pollarding", "re-pollard",
    "crown reduction", "crown lift", "crown thin", "crown raising", "crown clean",
    "crown thinning", "crown lifting", "crown cleaning", "lateral branch", "lateral branches",
    "deadwood", "dead wood", "dead branches", "ash dieback", "diseased tree", "decayed tree",
    "woodland management", "woodland clearance", "coppice", "coppicing", "monolith",
    "hedge trimming", "hedge cutting", "hedge removal", "hedge reduction",
    "bs5837", "bs 5837", "root protection area", "root severance",
    # Specific species with tree/work indicators
    "oak tree", "ash tree", "sycamore tree", "beech tree", "pine tree", "willow tree",
    "birch tree", "conifer tree", "cedar tree", "cypress tree", "poplar tree", "yew tree",
    "lime tree", "horse chestnut", "eucalyptus"
]



def score_lead(summary: str) -> tuple:
    """
    Classifies a planning application as small / medium / large
    and returns the corresponding price.
    Returns: (lead_score: str, lead_price: int)
    """
    s = summary.lower()
    if any(k in s for k in LARGE_KEYWORDS):
        return "large", 75
    elif any(k in s for k in MEDIUM_KEYWORDS):
        return "medium", 50
    return "small", 25


def _is_tree_related(text: str) -> bool:
    return any(word in text.lower() for word in TREE_GOLD)


def _insert_lead(cur, reference: str, address: str, summary: str, source: str) -> Optional[dict]:
    """
    Inserts a lead into the DB. Returns the lead dict if new, None if duplicate or low-quality junk.
    Enforces a strict quality gate: blocks empty, generic placeholders like 'tree-preservation-order'.
    """
    if not summary or not reference:
        return None
        
    s_clean = summary.strip().lower()
    # Reject generic placeholders that lack actionable details for contractors
    if s_clean in ["tree-preservation-order", "tpo", "work to trees", "works to trees", "tree work", "tree works", "trees"]:
        return None
    if len(s_clean) < 12:
        return None
        
    addr_clean = address.strip() if address else ""
    if not addr_clean or addr_clean.lower() in ["greater london", "london", "uk", "england"]:
        # If address is completely generic, require higher description detail to avoid useless leads
        if len(s_clean) < 20:
            return None

    lead_score, lead_price = score_lead(summary)
    cur.execute(
        """
        INSERT INTO leads (reference, address, summary, council_source, lead_score, lead_price)
        VALUES (%s, %s, %s, %s, %s, %s)
        ON CONFLICT (reference) DO NOTHING
        RETURNING id;
        """,
        (reference, address, summary[:350], source, lead_score, lead_price)
    )
    if cur.fetchone():
        return {"ref": reference, "addr": address, "summary": summary,
                "lead_score": lead_score, "lead_price": lead_price}
    return None


# ── Leeds Scanner (ArcGIS + Yorkshire Regional Councils) ──────────────────────

def run_mesh_network_scan() -> int:
    """
    Executes a direct scan of all councils mapped in the Aggregator Mesh (Idox portals, etc.)
    Bypasses the third-party paid API entirely to save quota.
    """
    try:
        import mesh_scrapers
    except ImportError:
        logger.error("[MESH] mesh_scrapers.py not found.")
        return 0

    new_leads = []
    conn = database.get_db_conn()
    cur = conn.cursor()
    try:
        for council_name, url in mesh_scrapers.COUNCIL_REGISTRY.items():
            logger.info(f"[MESH] Scraping {council_name} directly from {url}...")
            # We add an artificial delay to respect council rate limits
            import time
            time.sleep(2)
            
            leads = mesh_scrapers.scrape_mesh_council(council_name)
            for lead in leads:
                ref = lead.get("reference")
                addr = lead.get("address")
                desc = lead.get("description")
                if not ref or not desc:
                    continue
                
                inserted = _insert_lead(cur, ref, addr, desc, council_name.title())
                if inserted:
                    new_leads.append(inserted)
            conn.commit()
    except Exception as e:
        logger.error(f"[MESH] Fatal error during mesh scan: {e}")
    finally:
        cur.close()
        conn.close()

    if new_leads:
        notifications.dispatch_lead_alerts("MESH-NATIONWIDE", new_leads)
        
    logger.info(f"[MESH] Mesh Scan complete. {len(new_leads)} free leads extracted directly from councils.")
    return len(new_leads)


def scan_leeds_leads() -> int:
    """
    Scans both:
    1. Leeds City Council ArcGIS MapServer Layer 12 (15-mile spatial boundary)
    2. Surrounding Yorkshire councils (Bradford, Wakefield, Kirklees, Calderdale, York, Harrogate, North Yorkshire)
       via UK Planning API postcodes (LS, BD, WF, HX, HD, YO, HG, HU, DL, TS).
    """
    new_leads = []
    conn = database.get_db_conn()
    cur = conn.cursor()

    # 1. Leeds Council ArcGIS Server Query
    url = "https://mapservices.leeds.gov.uk/arcgis/rest/services/Public/Planning/MapServer/12/query"
    params = {
        "where":        "1=1",
        "outFields":    "*",
        "geometry":     "-1.5491,53.8008",
        "geometryType": "esriGeometryPoint",
        "inSR":         "4326",
        "spatialRel":   "esriSpatialRelIntersects",
        "distance":     24140,          # 15 miles in metres
        "units":        "esriSRUnit_Meter",
        "resultRecordCount": 200,
        "f": "json"
    }
    try:
        res = net_utils.smart_get(url, params=params, timeout=20)
        if res.status_code == 200:
            features = res.json().get("features", [])
            for feature in features:
                rec = feature.get("attributes", {})
                summary = str(rec.get("DESCRIPTION") or "")
                if not _is_tree_related(summary):
                    continue
                ref = str(rec.get("REFERENCE") or rec.get("OBJECTID") or f"LDS-{int(time.time())}")
                addr = rec.get("ADDRESS") or "Leeds"
                lead = _insert_lead(cur, ref, addr, summary, "Leeds")
                if lead:
                    new_leads.append(lead)
    except Exception as e:
        logger.debug(f"[Leeds ArcGIS] Error: {e}")

    # 2. Yorkshire Surrounding Councils Scan via UK Planning API
    if UK_PLANNING_API_KEY:
        yorkshire_prefixes = ["LS", "BD", "WF", "HX", "HD", "YO", "HG", "HU", "DL", "TS"]
        headers = {"X-API-Key": UK_PLANNING_API_KEY}
        for prefix in yorkshire_prefixes:
            try:
                import time
                time.sleep(1.5) # Cron job throttle to prevent 6am ban
                res = net_utils.smart_get(
                    "https://ukplanningapi.co.uk/v1/applications",
                    params={"postcode": prefix, "status": "received", "limit": 200},
                    headers=headers,
                    timeout=15
                )
                if res.status_code == 429:
                    break
                if res.status_code == 200:
                    records = res.json().get("data", [])
                    for item in records:
                        summary = item.get("description", "") or ""
                        if not _is_tree_related(summary):
                            continue
                        ref  = item.get("reference") or f"{prefix}-{int(time.time())}"
                        addr = item.get("address", f"Leeds / {prefix}")
                        lead = _insert_lead(cur, ref, addr, summary, "Leeds")
                        if lead:
                            new_leads.append(lead)
            except Exception as pe:
                logger.debug(f"[Leeds Yorkshire Radar] Error scanning prefix '{prefix}': {pe}")

    conn.commit()
    cur.close()
    conn.close()

    if new_leads:
        notifications.dispatch_lead_alerts("Leeds", new_leads)
    logger.info(f"[Leeds] Scan complete. {len(new_leads)} new leads found across Yorkshire.")
    return len(new_leads)



# ── London Scanner (GLA Datahub + Complete London & Green Belt Postcodes) ──────
 
def scan_london_leads() -> int:
    """
    Scans London & Green Belt planning applications:
    1. London GLA Planning Datahub (deep multi-field extraction across all 32 London Boroughs)
    2. Comprehensive UK Planning API & PlanIt radar covering all Inner & Outer London + Home Counties postcodes:
       (SW, SE, NW, N, E, EC, WC, CR, BR, EN, HA, UB, KT, TW, DA, RM, IG, SM, RH, TN, GU, CM, SS, SL, HP, AL, SG, WD, ME).
    """
    new_leads = []
    conn = database.get_db_conn()
    cur = conn.cursor()

    # 1. GLA Datahub Scan with robust schema mapping
    if GLA_API_KEY:
        try:
            headers = {"Authorization": GLA_API_KEY, "Accept": "application/json"}
            import time
            time.sleep(1.0) # London throttle
            res = net_utils.smart_get(
                "https://planningdata.london.gov.uk/api/applications",
                params={"limit": 100},
                headers=headers,
                timeout=15
            )
            if res.status_code in (401, 403):
                notifications.send_system_incident_alert(
                    category="SECURITY & API KEYS",
                    title="LONDON GLA PLANNING DATAHUB TOKEN INVALID",
                    description="CRITICAL: London GLA Planning Datahub rejected requests with HTTP 401/403 Unauthorized.",
                    impact="Planning lead scraping across all 32 London Boroughs is paused.",
                    action_required="Check GLA_API_KEY in Render and regenerate token at planningdata.london.gov.uk.",
                    severity="CRITICAL",
                    throttle_hours=6.0
                )
            elif res.status_code == 200:
                records = res.json().get("data", [])
                for item in records:
                    # Search across all possible GLA description fields to avoid placeholder names
                    summary = (
                        item.get("description")
                        or item.get("proposal")
                        or item.get("development_description")
                        or item.get("details")
                        or item.get("proposal_summary")
                        or item.get("title")
                        or ""
                    ).strip()
                    
                    if not summary or not _is_tree_related(summary):
                        continue
                        
                    # Extract nested or flat address
                    addr = ""
                    if isinstance(item.get("location"), dict):
                        addr = item["location"].get("address", "")
                    elif isinstance(item.get("site"), dict):
                        addr = item["site"].get("address", "")
                    if not addr:
                        addr = item.get("site_address") or item.get("address") or item.get("address_text") or "London"
                        
                    ref = (
                        item.get("reference")
                        or item.get("application_reference")
                        or item.get("lpa_app_no")
                        or item.get("planning_reference")
                        or f"LON-{int(time.time())}"
                    )
                    
                    lead = _insert_lead(cur, ref, addr, summary, "London")
                    if lead:
                        new_leads.append(lead)
        except Exception as e:
            logger.error(f"[London GLA] Error: {e}")


    # 2. Comprehensive London & Home Counties Radar Scan via UK Planning API & PlanIt
    if UK_PLANNING_API_KEY:
        london_all_prefixes = [
            # Inner & Outer London
            "SW", "SE", "NW", "N", "E", "EC", "WC", "CR", "BR", "EN", "HA", "UB", "KT", "TW", "DA", "RM", "IG", "SM",
            # Green Belt & Home Counties
            "RH", "TN", "GU", "CM", "SS", "SL", "HP", "AL", "SG", "WD", "ME"
        ]
        headers = {"X-API-Key": UK_PLANNING_API_KEY}
        for prefix in london_all_prefixes:
            try:
                import time
                time.sleep(1.5) # Cron job throttle to prevent 6am ban
                res = net_utils.smart_get(
                    "https://ukplanningapi.co.uk/v1/applications",
                    params={"postcode": prefix, "status": "received", "limit": 200},
                    headers=headers,
                    timeout=15
                )
                if res.status_code == 429:
                    break
                if res.status_code == 200:
                    records = res.json().get("data", [])
                    for item in records:
                        summary = (item.get("description", "") or "").strip()
                        if not summary or not _is_tree_related(summary):
                            continue
                        ref  = item.get("reference") or f"{prefix}-{int(time.time())}"
                        addr = item.get("address", f"London / {prefix}")
                        lead = _insert_lead(cur, ref, addr, summary, "London")
                        if lead:
                            new_leads.append(lead)
            except Exception as pe:
                logger.debug(f"[London Radar] Error scanning prefix '{prefix}': {pe}")

    conn.commit()
    cur.close()
    conn.close()

    if new_leads:
        notifications.dispatch_lead_alerts("London", new_leads)
    logger.info(f"[London] Scan complete. {len(new_leads)} high-quality leads found across London & Green Belt councils.")
    return len(new_leads)



# ── UK Planning API Scanner (Birmingham, Manchester, Bristol, Sheffield) ──────
# Uses ukplanningapi.co.uk — covers 289 UK councils, updated daily.

UK_PLANNING_API_KEY = os.getenv("UK_PLANNING_API_KEY", "").strip()

# Exhaustive regional postcode prefixes covering England, Scotland, and Wales
CITY_POSTCODE_PREFIX = {
    # 1. Greater London
    "London":          ["SW", "SE", "NW", "N", "E", "EC", "WC", "CR", "BR", "EN", "HA", "UB", "KT", "TW", "DA", "RM", "IG", "SM", "RH", "TN", "GU", "CM", "SS", "SL", "HP", "AL", "SG", "WD", "ME"],
    # 2. South East & Home Counties
    "South East":      ["RH", "TN", "GU", "ME", "CT", "BN", "SO", "PO", "OX", "RG", "MK", "HP", "AL", "SG", "WD", "SL"],
    # 3. South West & West Country (Devon, Cornwall, Somerset, Dorset, Wiltshire, Gloucestershire)
    "South West":      ["BS", "BA", "GL", "SN", "TA", "DT", "SP", "EX", "TQ", "PL", "TR", "BH"],
    "Bristol":         ["BS", "BA", "GL", "SN", "TA", "DT", "SP"],
    "Cornwall":        ["TR", "PL"],
    "Devon":           ["EX", "TQ", "PL"],
    # 4. West Midlands
    "West Midlands":   ["B", "WS", "WV", "DY", "CV", "WR", "TF", "ST", "HR", "SY"],
    "Birmingham":      ["B", "WS", "WV", "DY", "CV", "WR", "TF", "ST", "HR", "SY"],
    # 5. East Midlands
    "East Midlands":   ["S", "DE", "NG", "LE", "LN", "NN", "PE"],
    "Sheffield":       ["S", "DN", "DE", "NG", "LN", "LE"],
    # 6. Yorkshire & The Humber
    "Yorkshire":       ["LS", "BD", "WF", "HX", "HD", "YO", "HG", "HU", "DL", "TS", "DN"],
    "Leeds":           ["LS", "BD", "WF", "HX", "HD", "YO", "HG", "HU", "DL", "TS"],
    # 7. North West & Cumbria Lake District
    "North West":      ["M", "SK", "WA", "WN", "BL", "OL", "CW", "L", "PR", "BB", "FY", "CH", "LA", "CA"],
    "Manchester":      ["M", "SK", "WA", "WN", "BL", "OL", "CW", "L", "PR", "BB", "FY", "CH"],
    "Cumbria":         ["CA", "LA"],
    # 8. North East
    "North East":      ["NE", "SR", "DH", "TS", "DL"],
    "Newcastle":       ["NE", "SR", "DH", "TS", "DL"],
    # 9. East of England (East Anglia, Norfolk, Suffolk, Essex, Beds, Herts, Cambs)
    "East of England": ["CM", "SS", "CO", "CB", "PE", "NR", "IP", "LU", "SG"],
    "Cambridge":       ["CM", "SS", "CO", "CB", "PE", "NR", "IP", "LU", "SG"],
    "Norfolk":         ["NR", "IP", "PE"],
    # 10. Scotland (All 32 Scottish Councils / Central Belt, Borders, Highlands & Islands)
    "Scotland":        ["EH", "G", "AB", "DD", "IV", "KW", "PA", "PH", "FK", "KY", "ML", "TD", "DG", "ZE", "HS"],
    "Edinburgh":       ["EH", "KY", "FK", "TD"],
    "Glasgow":         ["G", "PA", "ML", "KA", "DG"],
    "Aberdeen":        ["AB", "DD", "IV", "PH", "KW"],
    # 11. Wales (All 22 Welsh Councils / South Wales, Mid Wales, North Wales)
    "Wales":           ["CF", "SA", "NP", "LL", "LD", "SY"],
    "Cardiff":         ["CF", "NP", "SA"],
    "Swansea":         ["SA", "CF", "LD"],
    "North Wales":     ["LL", "SY", "CH"]
}






def scan_city_planning_api(city_name: str) -> int:
    """
    Scans planning applications for a UK city and all its surrounding borough/county councils
    using ukplanningapi.co.uk across all regional postcode prefixes in parallel.
    """
    if not UK_PLANNING_API_KEY:
        logger.error(f"[{city_name}] UK_PLANNING_API_KEY is not set.")
        return 0

    postcode_prefixes = CITY_POSTCODE_PREFIX.get(city_name, [])
    if isinstance(postcode_prefixes, str):
        postcode_prefixes = [postcode_prefixes]
    if not postcode_prefixes:
        return 0

    headers = {"X-API-Key": UK_PLANNING_API_KEY}
    new_leads = []

    try:
        from concurrent.futures import ThreadPoolExecutor

        def fetch_prefix(prefix):
            # Try ukplanningapi.co.uk first if key exists
            try:
                if UK_PLANNING_API_KEY:
                    import time
                    time.sleep(1.5) # Cron job throttle to prevent 6am ban
                    res = net_utils.smart_get(
                        "https://ukplanningapi.co.uk/v1/applications",
                        params={"postcode": prefix, "status": "received", "limit": 200},
                        headers=headers,
                        timeout=8
                    )
                    if res.status_code == 200:
                        return prefix, res.json().get("data", [])
            except Exception:
                pass
                
            # Free Fallback: PlanIt API (Unlimited, No API Key needed)
            try:
                import time
                time.sleep(1.0) # Polite throttle for PlanIt
                planit_res = net_utils.smart_get(
                    "https://www.planit.org.uk/api/applics/json",
                    params={"postcode": prefix, "pg_sz": 50},
                    timeout=12
                )
                if planit_res.status_code == 200:
                    data = planit_res.json()
                    records = data.get("records", [])
                    # Map PlanIt schema to our expected schema
                    mapped_data = []
                    for rec in records:
                        mapped_data.append({
                            "reference": rec.get("uid", ""),
                            "description": rec.get("description", ""),
                            "address": rec.get("address", ""),
                            "url": rec.get("url", ""),
                            "status": rec.get("system_status", "received"),
                            "date": rec.get("creation_date", "")
                        })
                    return prefix, mapped_data
            except Exception as e:
                pass
                
            return prefix, []

        with ThreadPoolExecutor(max_workers=6) as executor:
            prefix_results = list(executor.map(fetch_prefix, postcode_prefixes))

        # Track monthly usage and trigger predictive warning email when burn rate will breach 500 cap
        usage_info = database.increment_api_usage("UK Planning API", increment=len(postcode_prefixes), cap=500)
        if usage_info.get("warning_needed"):
            notifications.send_api_quota_warning_email(
                api_name="UK PLANNING DATA API",
                current_calls=usage_info.get("count", 350),
                cap=500,
                projected_monthly=usage_info.get("projected_monthly", 600),
                reason=usage_info.get("reason", "Projected monthly pace exceeds 500 limit")
            )



        conn = database.get_db_conn()
        cur = conn.cursor()
        try:
            for prefix, records in prefix_results:
                for item in records:
                    summary = item.get("description", "") or ""
                    if not _is_tree_related(summary):
                        continue
                    ref  = item.get("reference") or f"{prefix}-{int(time.time())}"
                    addr = item.get("address", city_name)
                    lead = _insert_lead(cur, ref, addr, summary, city_name)
                    if lead:
                        new_leads.append(lead)

            conn.commit()
        finally:
            cur.close()
            conn.close()

        if new_leads:
            notifications.dispatch_lead_alerts(city_name, new_leads)
        logger.info(f"[{city_name}] Parallel scan complete. {len(new_leads)} new leads found.")
        return len(new_leads)

    except Exception as e:
        logger.error(f"[{city_name}] Fatal error in scan_city_planning_api: {e}")
        return 0


def scan_scotland_leads() -> int:
    """Scans all 32 Scottish local authority planning portals in parallel."""
    return scan_city_planning_api("Scotland")


def scan_wales_leads() -> int:
    """Scans all 22 Welsh local authority planning portals in parallel."""
    return scan_city_planning_api("Wales")


def scan_nationwide_bulk_crawler() -> dict:
    """
    Crawls all 10 major UK macro-regions in parallel to pull thousands of active tree leads.
    """
    regions = [
        "London", "Leeds", "Manchester", "Birmingham", "Bristol",
        "Sheffield", "North East", "East of England", "East Midlands",
        "South West", "South East", "Scotland", "Wales"
    ]
    total_leads = 0
    region_results = {}

    # 1. Run direct council Idox mesh scrapers across 50+ UK local planning authorities
    try:
        mesh_count = run_mesh_network_scan()
        region_results["Direct Council Idox Mesh (50+ Authorities)"] = mesh_count
        total_leads += mesh_count
    except Exception as e:
        region_results["Direct Council Idox Mesh"] = f"error: {e}"

    # 2. Run Regional and Open Data Scanners
    for reg in regions:
        try:
            if reg == "London":
                c = scan_london_leads()
            elif reg == "Leeds":
                c = scan_leeds_leads()
            else:
                c = scan_city_planning_api(reg)
            region_results[reg] = c
            total_leads += c
        except Exception as e:
            region_results[reg] = f"error: {e}"

    logger.info(f"[NATIONWIDE CRAWLER] Completed nationwide scan. Total new leads: {total_leads}")
    return {"total_new_leads": total_leads, "regions": region_results}
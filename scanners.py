import os
import requests
import time
import logging
import database
import notifications

logger = logging.getLogger("vector-data-labs")

GLA_API_KEY = os.getenv("GLA_API_KEY", "").strip()

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
    "felling", "fell ", "fell to ground", "fell 1", "fell 2", "fell 3", "sectional dismantle", "dismantle",
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


def _insert_lead(cur, reference: str, address: str, summary: str, source: str) -> dict | None:
    """
    Inserts a lead into the DB. Returns the lead dict if new, None if duplicate.
    """
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
        res = requests.get(url, params=params, timeout=20, verify=False)
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
                res = requests.get(
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



# ── London Scanner (GLA Datahub + Green Belt Councils) ────────────────────────

def scan_london_leads() -> int:
    """
    Scans both:
    1. London GLA Planning Datahub (all 32 London Boroughs)
    2. Surrounding Green Belt councils (Surrey/Tandridge/Oxted, Kent, Essex, Herts)
       via UK Planning API postcodes (RH, TN, GU, CR, BR, KT, SM, TW, UB, HA, EN, IG, RM, DA, CM, AL, WD, SL, ME).
    """
    new_leads = []
    conn = database.get_db_conn()
    cur = conn.cursor()

    # 1. GLA Datahub Scan
    if GLA_API_KEY:
        try:
            headers = {"Authorization": GLA_API_KEY, "Accept": "application/json"}
            res = requests.get(
                "https://planningdata.london.gov.uk/api/applications",
                params={"limit": 50},
                headers=headers,
                timeout=15
            )
            if res.status_code == 200:
                records = res.json().get("data", [])
                for item in records:
                    summary = item.get("proposal", "")
                    if not _is_tree_related(summary):
                        continue
                    ref = item.get("reference", f"LON-{int(time.time())}")
                    addr = item.get("address", "Greater London")
                    lead = _insert_lead(cur, ref, addr, summary, "London")
                    if lead:
                        new_leads.append(lead)
        except Exception as e:
            logger.error(f"[London GLA] Error: {e}")

    # 2. Green Belt & Border Councils Scan (Tandridge/Oxted RH, Sevenoaks TN, Surrey GU, etc.)
    if UK_PLANNING_API_KEY:
        green_belt_prefixes = ["RH", "TN", "GU", "CR", "BR", "KT", "SM", "TW", "UB", "HA", "EN", "IG", "RM", "DA", "CM", "AL", "WD", "SL", "ME"]
        headers = {"X-API-Key": UK_PLANNING_API_KEY}
        for prefix in green_belt_prefixes:
            try:
                res = requests.get(
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
                        addr = item.get("address", f"London / {prefix}")
                        lead = _insert_lead(cur, ref, addr, summary, "London")
                        if lead:
                            new_leads.append(lead)
            except Exception as pe:
                logger.debug(f"[London Green Belt] Error scanning prefix '{prefix}': {pe}")

    conn.commit()
    cur.close()
    conn.close()

    if new_leads:
        notifications.dispatch_lead_alerts("London", new_leads)
    logger.info(f"[London] Scan complete. {len(new_leads)} new leads found across London & Green Belt councils.")
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
            try:
                res = requests.get(
                    "https://ukplanningapi.co.uk/v1/applications",
                    params={"postcode": prefix, "status": "received", "limit": 200},
                    headers=headers,
                    timeout=8
                )
                if res.status_code == 429:
                    # 429 indicates limit hit — send immediate alert
                    notifications.send_api_quota_warning_email("UK Planning API", 500, cap=500)
                    return prefix, []
                if res.status_code == 200:
                    return prefix, res.json().get("data", [])
            except Exception:
                pass
            return prefix, []

        with ThreadPoolExecutor(max_workers=6) as executor:
            prefix_results = list(executor.map(fetch_prefix, postcode_prefixes))

        # Track monthly usage and trigger warning email when nearing 500 cap (400 / 80% threshold)
        usage_info = database.increment_api_usage("UK Planning API", increment=len(postcode_prefixes), warning_threshold=400)
        if usage_info.get("warning_needed"):
            notifications.send_api_quota_warning_email("UK Planning API", usage_info.get("count", 400), cap=500)


        conn = database.get_db_conn()
        cur = conn.cursor()

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
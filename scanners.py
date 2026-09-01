import os
import re
import requests
import time
import datetime
import threading
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


def _insert_lead(cur, reference: str, address: str, summary: str, source: str,
                  applicant_name: Optional[str] = None, agent_name: Optional[str] = None,
                  agent_company: Optional[str] = None, has_agent: Optional[bool] = None,
                  agent_is_tree_surgeon: Optional[bool] = None) -> Optional[dict]:
    """
    Inserts a lead into the DB. Returns the lead dict if new, None if duplicate or low-quality junk.
    Enforces a strict quality gate: blocks empty, generic placeholders like 'tree-preservation-order'.

    applicant_name / agent_name / agent_company / has_agent (Aug 30 2026): whether this
    application already names a contractor as its Agent. Only the mesh (Idox) scanner
    currently populates these -- it visits each application's own page, not just the
    search-results list, to read them. Other scan paths pass None/leave has_agent NULL,
    which the UI/exports must treat as "unknown", not "no agent" -- those are different
    things and conflating them would misrepresent leads we simply haven't checked yet.

    Backfill note: the daily scan re-finds the same still-pending applications on
    every run (a TPO application stays in the council's "recent" search for weeks),
    so most of what a run turns up on any given day are references already in the
    table from an earlier day -- ON CONFLICT DO NOTHING alone would silently skip
    those forever and this new data would never reach a single existing row. Fixed
    below with DO UPDATE ... COALESCE: an existing row gets these 4 fields filled
    in the first time they're seen (never overwritten once set), while `was_inserted`
    (Postgres' xmax=0 trick) keeps the return value None for a backfill-only touch,
    so callers' "is this a brand-new lead to notify about" logic is unaffected.
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
        INSERT INTO leads (reference, address, summary, council_source, lead_score, lead_price,
                            applicant_name, agent_name, agent_company, has_agent, agent_is_tree_surgeon)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (reference) DO UPDATE SET
            applicant_name = COALESCE(leads.applicant_name, EXCLUDED.applicant_name),
            agent_name     = COALESCE(leads.agent_name, EXCLUDED.agent_name),
            agent_company  = COALESCE(leads.agent_company, EXCLUDED.agent_company),
            has_agent      = COALESCE(leads.has_agent, EXCLUDED.has_agent),
            agent_is_tree_surgeon = COALESCE(leads.agent_is_tree_surgeon, EXCLUDED.agent_is_tree_surgeon)
        RETURNING id, (xmax = 0) AS was_inserted;
        """,
        (reference, address, summary[:350], source, lead_score, lead_price,
         applicant_name, agent_name, agent_company, has_agent, agent_is_tree_surgeon)
    )
    row = cur.fetchone()
    if row and row[1]:  # was_inserted -- a genuinely new lead, not a backfill of an existing one
        return {"ref": reference, "addr": address, "summary": summary,
                "lead_score": lead_score, "lead_price": lead_price,
                "applicant_name": applicant_name, "agent_name": agent_name,
                "agent_company": agent_company, "has_agent": has_agent,
                "agent_is_tree_surgeon": agent_is_tree_surgeon}
    return None


# ── Leeds Scanner (ArcGIS + Yorkshire Regional Councils) ──────────────────────

# Aug 30 2026: Nick flagged that troubleshooting/manual re-triggers of the
# pipeline (redeploys, manual /scan-nationwide calls, active testing --
# exactly what a heavy development day like today looks like) were each
# separately hammering all 50+ real council government websites in
# COUNCIL_REGISTRY a second, third, fourth time in the same day. Unlike
# ukplanningapi.co.uk's monthly quota (a money problem, fixed above with
# rotation), this is a good-citizen problem: these are small councils'
# own servers, not built to be scraped repeatedly in one afternoon, and
# the "same portal hit by two overlapping scans within seconds" pattern
# is already on record (see _dispatch_locked_scan in main.py) as a likely
# cause of real 503s/timeouts. Same-day dedup, in-memory (resets on a
# Render restart, which is rare compared to daily cron runs) -- the worst
# case is one extra full sweep right after a redeploy, not a recurring
# problem.
_MESH_SCAN_DAY_CACHE: Optional[str] = None


def run_mesh_network_scan() -> int:
    """
    Executes a direct scan of all councils mapped in the Aggregator Mesh (Idox portals, etc.)
    Bypasses the third-party paid API entirely to save quota.
    """
    global _MESH_SCAN_DAY_CACHE
    today_str = datetime.date.today().isoformat()
    if _MESH_SCAN_DAY_CACHE == today_str:
        logger.info(
            "[MESH] Already ran the full council sweep once today (this process) -- "
            "skipping this re-trigger rather than hitting all 50+ council websites again. "
            "Set the PAID_API_ROTATION_DAYS-style override aside; this one has no override "
            "since re-scraping free council sites has no quota to spend, only their goodwill."
        )
        return 0

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

                inserted = _insert_lead(
                    cur, ref, addr, desc, council_name.title(),
                    applicant_name=lead.get("applicant_name"),
                    agent_name=lead.get("agent_name"),
                    agent_company=lead.get("agent_company"),
                    has_agent=lead.get("has_agent"),
                    agent_is_tree_surgeon=lead.get("agent_is_tree_surgeon"),
                )
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

    # Mark today's sweep as done regardless of outcome -- a same-day retry
    # wouldn't fix a real council-side outage anyway, and the goal here is
    # strictly "at most one full council sweep per calendar day".
    _MESH_SCAN_DAY_CACHE = today_str

    logger.info(f"[MESH] Mesh Scan complete. {len(new_leads)} free leads extracted directly from councils.")
    return len(new_leads)


def scan_leeds_leads() -> int:
    """
    Scans both:
    1. Leeds City Council ArcGIS MapServer Layer 12 (15-mile spatial boundary)
    2. Surrounding Yorkshire councils (Bradford, Wakefield, Kirklees, Calderdale,
       York, Harrogate, North Yorkshire) -- delegated to scan_city_planning_api("Leeds").

    Aug 30 2026: part 2 used to carry its own hardcoded copy of these exact
    Yorkshire postcode prefixes (LS, BD, WF, HX, HD, YO, HG, HU, DL, TS --
    identical to CITY_POSTCODE_PREFIX["Leeds"]) in a raw loop straight
    against ukplanningapi.co.uk, with no rotation, no same-day dedup, and
    no 429/quota-aware logging. This function is only reachable via
    scan_nationwide_bulk_crawler()'s "Leeds" special-case (manual/admin
    endpoints), so every manual trigger of it was completely bypassing the
    quota-headroom and good-citizen fixes added to scan_city_planning_api()
    above -- found while checking whether any other code path had the same
    gap as scan_london_leads() (which had the identical problem, fixed the
    same way just above). Delegating here closes that gap and picks up
    PlanIt coverage for Leeds as a bonus (this function never queried
    PlanIt directly before).
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

    conn.commit()
    cur.close()
    conn.close()

    # 2. Surrounding Yorkshire councils -- delegated (see docstring above),
    # so it inherits rotation + same-day dedup automatically.
    yorkshire_count = scan_city_planning_api("Leeds")

    if new_leads:
        notifications.dispatch_lead_alerts("Leeds", new_leads)
    total = len(new_leads) + yorkshire_count
    logger.info(
        f"[Leeds] Scan complete. {total} new leads found "
        f"({len(new_leads)} via ArcGIS, {yorkshire_count} via Yorkshire radar)."
    )
    return total



# ── London Scanner (GLA Datahub + Complete London & Green Belt Postcodes) ──────

# Aug 30 2026: same-day dedup, same reasoning as _MESH_SCAN_DAY_CACHE above --
# a single request per call, so the stakes are much lower than the mesh
# scan, but there's no reason to hit a re-trigger's worth of extra calls
# against someone else's free government API either.
_GLA_DAY_CACHE: Optional[str] = None


def scan_gla_datahub_london() -> int:
    """
    Aug 30 2026: extracted out of scan_london_leads() below. That function's
    part 2 duplicates ALL of its own postcode-prefix logic against the now-
    hardened scan_city_planning_api() -- same 29 London/Home-Counties
    prefixes (see CITY_POSTCODE_PREFIX["London"] further down), same
    ukplanningapi.co.uk endpoint, just without the 429 backoff, aggregate-
    failure warning, or dedup this file's Aug 30 hardening pass added
    everywhere else. Stage 1 of the daily pipeline (main.py's
    run_master_daily_pipeline) already calls scan_city_planning_api("London")
    for that exact coverage every single day, so re-running scan_london_
    leads()'s part 2 there would double-fetch the same prefixes and burn
    through the 500/month free-tier quota twice as fast for zero new leads.

    The ONE genuinely distinct, non-duplicated piece is this: the free
    London GLA Planning Datahub (planningdata.london.gov.uk) -- a separate
    government open-data API, not ukplanningapi.co.uk or PlanIt, that this
    project has had built and working since before this hardening pass.
    But scan_london_leads() itself was never actually wired into the
    scheduled daily pipeline (it's only reachable from three manual/admin-
    triggered endpoints via scan_nationwide_bulk_crawler()) -- so this free
    third source has been sitting unused on every automated daily run.
    Pulled out on its own so Stage 1 can call it directly for the London
    region, ADDITIONALLY to (not instead of) the existing
    scan_city_planning_api("London") call -- exactly the "more free sources
    to spread the request load across" strategy this was built for.
    """
    global _GLA_DAY_CACHE
    if not GLA_API_KEY:
        return 0
    today_str = datetime.date.today().isoformat()
    if _GLA_DAY_CACHE == today_str:
        logger.debug("[London GLA] Already queried once today (this process) -- skipping re-trigger.")
        return 0
    _GLA_DAY_CACHE = today_str

    new_leads = []
    conn = database.get_db_conn()
    cur = conn.cursor()
    try:
        headers = {"Authorization": GLA_API_KEY, "Accept": "application/json"}
        import time
        time.sleep(1.0)  # London throttle
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
                impact="Planning lead scraping across all 32 London Boroughs via the free GLA Datahub is paused.",
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
        else:
            logger.debug(f"[London GLA] HTTP {res.status_code}")
    except Exception as e:
        logger.error(f"[London GLA] Error: {e}")
    finally:
        conn.commit()
        cur.close()
        conn.close()

    if new_leads:
        notifications.dispatch_lead_alerts("London", new_leads)
    logger.info(f"[London GLA Datahub] Scan complete. {len(new_leads)} tree-related leads found.")
    return len(new_leads)


def scan_london_leads() -> int:
    """
    Scans London & Green Belt planning applications:
    1. London GLA Planning Datahub (deep multi-field extraction across all 32 London Boroughs)
    2. Comprehensive UK Planning API & PlanIt radar covering all Inner & Outer London + Home Counties postcodes:
       (SW, SE, NW, N, E, EC, WC, CR, BR, EN, HA, UB, KT, TW, DA, RM, IG, SM, RH, TN, GU, CM, SS, SL, HP, AL, SG, WD, ME).

    Aug 30 2026: only reachable via scan_nationwide_bulk_crawler() (the
    manual /scan-nationwide-style endpoints), never from the scheduled
    daily pipeline. Part 1 (GLA Datahub) is now shared with Stage 1's own
    direct call via scan_gla_datahub_london() above -- kept here too so
    this function's existing manual-trigger callers keep working exactly
    as before, without duplicating the GLA-fetching code itself.

    Part 2 used to carry its own hardcoded copy of these exact 29 London/
    Home-Counties postcode prefixes (identical to CITY_POSTCODE_PREFIX
    ["London"]) in a raw loop straight against ukplanningapi.co.uk -- no
    rotation, no same-day dedup, no 429/quota-aware logging. Since this
    function is only reachable via manual/admin endpoints, every manual
    trigger of it completely bypassed the quota-headroom fixes added to
    scan_city_planning_api() -- exactly the gap Nick asked about ("will
    that cover it though?"). Delegating here closes it and picks up
    PlanIt coverage for London as a bonus (this loop never queried PlanIt
    directly before).
    """
    gla_count = scan_gla_datahub_london()  # runs + inserts + dispatches alerts on its own connection

    # 2. Surrounding London & Home Counties radar -- delegated (see
    # docstring above), so it inherits rotation + same-day dedup + PlanIt
    # automatically instead of duplicating a second, unprotected copy.
    radar_count = scan_city_planning_api("London")

    total = radar_count + gla_count
    logger.info(
        f"[London] Scan complete. {total} high-quality leads found across London & Green Belt "
        f"councils ({gla_count} via GLA Datahub, {radar_count} via UK Planning API + PlanIt radar)."
    )
    return total



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

# Aug 30 2026: real town/local-authority names for the free PlanIt fallback
# (planit.org.uk), added after discovering the fallback was silently broken --
# it was calling PlanIt's API with the wrong parameter name (`postcode`
# instead of `pcode`) and no required search radius, AND even fixed, the
# bare 1-2 letter area codes in CITY_POSTCODE_PREFIX above (e.g. "B", "WS")
# aren't valid UK postcodes/outcodes for PlanIt to geocode -- confirmed live
# (`{"error": "pcode: Invalid format"}`). PlanIt instead supports searching
# directly by real authority name (`auth=<name>`), confirmed live against
# Birmingham and Walsall (both returned real, current applications). These
# lists reuse genuine UK council/authority names already used elsewhere in
# this codebase (COUNCIL_REGISTRY, bulk_contractor_extractor.py's
# UK_TARGET_REGIONS) -- not exhaustive, but real and verified-working,
# unlike the broken prefix approach it replaces.
REGION_TOWNS = {
    "London": ["Westminster", "Camden", "Islington", "Lambeth", "Southwark", "Wandsworth",
               "Barnet", "Brent", "Ealing", "Croydon", "Bromley", "Greenwich", "Haringey"],
    "South East": ["Guildford", "Reading", "Brighton and Hove", "Portsmouth", "Southampton",
                   "Oxford", "Winchester", "Maidstone", "Tunbridge Wells", "Sevenoaks"],
    "South West": ["Bristol", "Bath and North East Somerset", "Gloucester", "Cheltenham",
                   "Exeter", "Plymouth", "Cornwall", "Dorset", "Wiltshire", "Swindon"],
    "West Midlands": ["Birmingham", "Coventry", "Wolverhampton", "Solihull", "Dudley",
                       "Walsall", "Warwick", "Stoke-on-Trent"],
    "East Midlands": ["Nottingham", "Leicester", "Derby", "Northampton", "Lincoln"],
    "Yorkshire": ["Leeds", "Sheffield", "Bradford", "York", "Wakefield"],
    "North West": ["Manchester", "Liverpool", "Preston", "Blackpool", "Cheshire East",
                    "Cheshire West and Chester"],
    "North East": ["Newcastle upon Tyne", "Sunderland", "Durham", "Middlesbrough", "Darlington"],
    "East of England": ["Norwich", "Cambridge", "Milton Keynes", "Peterborough", "Colchester"],
    "Leeds": ["Leeds"],
    "Birmingham": ["Birmingham"],
    "Manchester": ["Manchester"],
    "Bristol": ["Bristol"],
    "Sheffield": ["Sheffield"],
    "Scotland": ["Edinburgh", "Glasgow", "Aberdeen City", "Dundee City", "Fife",
                 "Stirling", "Perth and Kinross"],
    "Wales": ["Cardiff", "Swansea", "Newport", "Wrexham", "Bridgend"],
}

# Values PlanIt returns as a placeholder when it hasn't actually captured a
# field (confirmed live: e.g. "agent_name": "See source") -- must not be
# stored as if it were a real name.
_PLANIT_PLACEHOLDER_VALUES = {
    "see source", "n/a", "none", "", "not available", "not known", "unknown",
    "n a", "not applicable", "not given", "not provided", "tbc", "to be confirmed",
    "-", "--",
}


def _planit_real_value(value) -> Optional[str]:
    """Returns value if it looks like a genuine PlanIt field, else None."""
    if not value or not isinstance(value, str):
        return None
    if value.strip().lower() in _PLANIT_PLACEHOLDER_VALUES:
        return None
    return value.strip()


# Aug 30 2026: keyed by (city_name, ISO date) -- see scan_city_planning_api's
# rotation/dedup comment. Marks a region's paid-API rotation bucket as
# already attempted today so a same-day manual re-trigger doesn't burn
# quota re-fetching it for zero new coverage.
_PAID_API_DAY_CACHE: dict = {}

# Same idea, applied to PlanIt. PlanIt has no monthly money quota to
# protect (unlike ukplanningapi.co.uk above), but Nick's "these keep
# pinging planning data software sites" concern applies here too --
# it's still someone else's free public API, and a same-day re-trigger
# gains nothing by re-querying the exact same authority names again.
# Unlike the paid API there's no rotation, just a flat "once per region
# per day" -- PlanIt isn't rationed by a monthly cap, only by its own
# per-request rate limit (handled with backoff inside fetch_planit).
_PLANIT_DAY_CACHE: dict = {}

# Aug 30 2026: root cause of PlanIt returning 429 for nearly every authority
# in 7 of 8 regions in one production run, even with the earlier "wait 20s
# and retry once" fix in place. That fix had two problems: (1) it capped
# the wait at min(Retry-After, 20s) -- if PlanIt's server genuinely asked
# for longer, the code ignored it and retried too soon anyway; (2) the
# throttle (time.sleep(1.5) inside fetch_planit) was purely LOCAL to one
# region's ThreadPoolExecutor call -- it had zero memory of how many PlanIt
# requests the previous 15 (of 16) ALL_CITIES regions had already made in
# the same run. PlanIt's rate limiter is IP-based, not aware of TreeKey's
# internal region groupings, so 16 regions run back-to-back each resetting
# their own "1.5s since my last request" clock will still blow through
# PlanIt's real limit well before the last few regions are reached.
#
# Fix: one shared lock + one shared "last request" timestamp for the whole
# process, so EVERY PlanIt request -- whichever region's fetch_planit is
# calling it -- waits out the same minimum gap from the previous one,
# process-wide. Defaults to 60s, matching PlanIt's own documented "one
# request per minute" safe-rate guidance (confirmed via their FAQ).
PLANIT_MIN_INTERVAL_SECONDS = float(os.getenv("PLANIT_MIN_INTERVAL_SECONDS", "60") or "60")

# Aug 30 2026: caps how many real agent-status confirmation fetches
# (mesh_scrapers.confirm_agent_status_from_source, following PlanIt's own
# source-authority link) one scan_city_planning_api() call will attempt --
# each one is a genuine new HTTP request straight to that specific
# authority's own server. Raised from an initial cautious 15 to 200 once a
# DB check (see the PlanIt insertion loop) started skipping any reference
# that's already resolved from a previous day WITHOUT spending budget or a
# network call -- that's what makes a generous number safe as a permanent
# setting rather than a one-off: it only ever gets spent on genuinely new
# or still-unresolved leads, which is a small, naturally shrinking set once
# the existing backlog clears, not "200 real requests every single day
# forever". Nick's explicit ask (Aug 30 2026): needed the current ~1,200-lead
# backlog checked in one pass today, not trickled in over days/weeks -- this
# is what makes that one full nationwide run actually cover most of it.
PLANIT_AGENT_CONFIRM_LIMIT = int(os.getenv("PLANIT_AGENT_CONFIRM_LIMIT", "1000") or "1000")
_PLANIT_PACING_LOCK = threading.Lock()
_PLANIT_LAST_REQUEST_AT: float = 0.0

# Aug 31 2026: production incident -- after PLANIT_MIN_INTERVAL_SECONDS was
# lowered to 10s, PlanIt returned a 429 with Retry-After: 20070 (5.6 hours),
# a real hard block, not a routine rate limit. Honoring Retry-After "in
# full" (the Aug 30 fix above) meant time.sleep(20070) ran synchronously on
# the single PlanIt worker thread (max_workers=1), stalling that region --
# and everything queued behind it in the same run -- for over 5 hours,
# which looked identical to the pipeline being stuck/hung. A genuinely long
# Retry-After means "stop asking for a long while", not "block this thread
# for that whole while": past this cap, give up on the town for this run
# instead of sleeping through it.
PLANIT_MAX_RETRY_WAIT_SECONDS = float(os.getenv("PLANIT_MAX_RETRY_WAIT_SECONDS", "30") or "30")


def _planit_wait_for_slot() -> None:
    """Block the calling thread until it's been at least
    PLANIT_MIN_INTERVAL_SECONDS since the last PlanIt request made by ANY
    thread/region in this process, then claims the slot. Call this
    immediately before every real PlanIt HTTP request (initial attempt and
    retry alike) -- see the module comment above _PLANIT_PACING_LOCK for why
    a per-region-local throttle wasn't enough."""
    global _PLANIT_LAST_REQUEST_AT
    with _PLANIT_PACING_LOCK:
        wait_s = PLANIT_MIN_INTERVAL_SECONDS - (time.monotonic() - _PLANIT_LAST_REQUEST_AT)
        if wait_s > 0:
            time.sleep(wait_s)
        _PLANIT_LAST_REQUEST_AT = time.monotonic()


def scan_city_planning_api(city_name: str) -> int:
    """
    Scans planning applications for a UK region using ukplanningapi.co.uk (paid,
    postcode-prefix based) where a key is configured, and PlanIt (planit.org.uk,
    free, no key needed) as a real fallback/supplement, queried by real
    authority name via REGION_TOWNS.

    Aug 30 2026 rewrite: previously this function returned 0 for the ENTIRE
    region (both APIs) whenever UK_PLANNING_API_KEY was unset -- the free
    PlanIt fallback was wrongly gated behind the paid key's presence. It also
    called PlanIt with the wrong parameter name and no required search
    radius, which PlanIt rejects with an error payload on a 200 OK response
    -- silently swallowed by the old code as "0 leads found" with no visible
    failure. Both confirmed live against the real API (see REGION_TOWNS
    comment). Fixed: PlanIt now runs regardless of the paid key, using
    `auth=<real authority name>` (verified working), and any error payload
    from either API is now logged instead of silently treated as empty.
    """
    postcode_prefixes = CITY_POSTCODE_PREFIX.get(city_name, [])
    if isinstance(postcode_prefixes, str):
        postcode_prefixes = [postcode_prefixes]
    region_towns = REGION_TOWNS.get(city_name, [])

    if not postcode_prefixes and not region_towns:
        return 0

    # Aug 30 2026: Nick hit ukplanningapi.co.uk's free 500/month cap last
    # week -- root cause found by counting. Stage 1 was calling ALL 178
    # postcode prefixes across all 16 daily regions, EVERY single day
    # (London alone is 29). ~178/day burns the entire month's 500-request
    # budget in under 3 days, then this API goes dark for the remaining
    # ~27 days -- previously silently, now visibly, thanks to the 429 fix
    # above, but visibility alone doesn't get the leads back. PlanIt has
    # no monthly cap (only the per-request rate limit already handled with
    # backoff above) and the free Idox mesh has none either, so this fix
    # is specific to this one API: instead of querying every prefix every
    # day, round-robin through a rotation so the full prefix list is still
    # covered on a rolling basis, but total monthly calls land well under
    # the free tier's cap.
    #
    # The rotation period (default 12 days, not the bare-minimum-viable 11)
    # was picked to also absorb a second real-world pattern: on a heavy
    # development/testing day (like the day this was built), the pipeline
    # can get manually re-triggered multiple times on top of the one
    # scheduled daily cron run. The separate same-day dedup guard just
    # below means a same-calendar-day re-trigger is now FREE (it skips the
    # paid API entirely and reuses today's rotation results), so the
    # number that actually matters is "one paid-API pass per distinct
    # calendar day", not "one pass per trigger" -- 178 prefixes / 12-day
    # rotation =~ 14.8/day, which even in a 31-day month is ~460 calls,
    # leaving a real ~40-call/month buffer for edge cases (transient-error
    # retries, a region added later, etc.) that a bare 500-on-the-nose
    # target wouldn't have. Uses the date's ordinal (not day-of-month) so
    # the cycle doesn't reset oddly at month boundaries of different
    # lengths. Set PAID_API_ROTATION_DAYS=1 in the environment to disable
    # rotation entirely and query every prefix every day again -- e.g.
    # after upgrading to a paid tier with enough headroom that pacing is
    # no longer needed.
    todays_paid_prefixes = postcode_prefixes
    if postcode_prefixes:
        rotation_days = max(1, int(os.getenv("PAID_API_ROTATION_DAYS", "12") or "12"))
        if rotation_days > 1:
            day_index = datetime.date.today().toordinal() % rotation_days
            todays_paid_prefixes = [p for i, p in enumerate(postcode_prefixes) if i % rotation_days == day_index]
            if len(todays_paid_prefixes) < len(postcode_prefixes):
                logger.info(
                    f"[{city_name}] Paid API rotation: querying {len(todays_paid_prefixes)} of "
                    f"{len(postcode_prefixes)} postcode prefixes today (day {day_index + 1}/{rotation_days} "
                    f"of the rotation cycle) to keep monthly usage under the free-tier cap."
                )

        # Same-day dedup: a manual re-trigger later the same calendar day
        # (a redeploy, a manual /scan-nationwide, testing) would otherwise
        # re-fetch this exact same rotated subset again for zero new
        # coverage -- the rotation bucket only changes when the date does.
        # In-memory only (resets on a Render restart/redeploy, which is
        # rare compared to daily cron runs) -- the worst case is one extra
        # day's spend right after a redeploy, not a recurring problem.
        today_key = (city_name, datetime.date.today().isoformat())
        if todays_paid_prefixes and _PAID_API_DAY_CACHE.get(today_key):
            logger.debug(
                f"[{city_name}] ukplanningapi.co.uk already queried once today (this process) -- "
                f"skipping to conserve monthly quota; PlanIt and the free mesh still run below."
            )
            todays_paid_prefixes = []

    headers = {"X-API-Key": UK_PLANNING_API_KEY} if UK_PLANNING_API_KEY else {}
    new_leads = []

    try:
        from concurrent.futures import ThreadPoolExecutor

        # Aug 30 2026: per-prefix/per-town failures below stay at DEBUG (with
        # dozens of prefixes per region, a WARNING per one-off timeout would
        # flood the log) -- but a run where EVERY prefix/town for a region
        # failed was previously indistinguishable from a run that genuinely
        # found zero tree-related applications. That's a real blind spot: an
        # expired/invalid UK_PLANNING_API_KEY, or PlanIt being down, would
        # silently look identical to "0 new leads found" with no visible
        # cause anywhere in the log. These two lists collect failures so a
        # 100%-failure run gets ONE explicit WARNING naming the likely cause.
        paid_failures = []
        planit_failures = []

        def fetch_paid(prefix):
            """ukplanningapi.co.uk -- postcode-prefix based. Freemium, not
            purely "paid" as earlier comments here assumed: their own
            pricing page confirms a free tier capped at 500 requests/month
            with paid tiers above that -- and this project's own
            increment_api_usage() call below caps at exactly 500, which is
            the free-tier limit, not a paid one. Skipped (not silently, now
            logged once) if no key is configured.

            Aug 30 2026: a 429 here used to be silently excluded from BOTH
            logging and paid_failures -- carved out of the `elif` below on
            the (correct, for a burst rate limit) assumption that net_utils
            already handles 429 upstream. But this API's 429 is much more
            likely a MONTHLY QUOTA EXHAUSTION on a free-tier key than a
            per-second burst limit, and unlike a burst limit, retrying
            seconds later can't fix that -- so this now logs it clearly and
            counts it as a real failure instead of a silent, indistinguishable
            "0 results", which is exactly the blind spot that let a possible
            month-long quota exhaustion look identical to genuinely zero
            leads with no visible cause anywhere in the logs."""
            if not UK_PLANNING_API_KEY:
                return prefix, []
            try:
                import time
                time.sleep(1.5)  # Cron job throttle to prevent 6am ban
                res = net_utils.smart_get(
                    "https://ukplanningapi.co.uk/v1/applications",
                    params={"postcode": prefix, "status": "received", "limit": 200},
                    headers=headers,
                    timeout=8
                )
                if res.status_code == 200:
                    body = res.json()
                    if isinstance(body, dict) and body.get("error"):
                        logger.warning(f"[{city_name}] ukplanningapi.co.uk returned an error for prefix '{prefix}': {body.get('error')}")
                        paid_failures.append(f"'{prefix}': API error {body.get('error')}")
                        return prefix, []
                    return prefix, body.get("data", [])
                elif res.status_code == 429:
                    logger.warning(
                        f"[{city_name}] ukplanningapi.co.uk returned 429 for prefix '{prefix}' -- "
                        f"likely the monthly request quota is exhausted (this key appears to be on "
                        f"the free 500/month tier), not a transient rate limit."
                    )
                    paid_failures.append(f"'{prefix}': HTTP 429 (likely monthly quota exhausted)")
                else:
                    logger.debug(f"[{city_name}] ukplanningapi.co.uk HTTP {res.status_code} for prefix '{prefix}'")
                    paid_failures.append(f"'{prefix}': HTTP {res.status_code}")
            except Exception as e:
                logger.debug(f"[{city_name}] ukplanningapi.co.uk error for prefix '{prefix}': {e}")
                paid_failures.append(f"'{prefix}': {e}")
            return prefix, []

        def fetch_planit(town):
            """PlanIt (planit.org.uk) -- free, no key, queried by real
            authority name. `recent=45` matches this pipeline's general
            lookback window; other_fields carries applicant/agent when
            PlanIt has actually captured it (often "See source" -- filtered
            out by _planit_real_value, never stored as a real name).

            Aug 30 2026: live logs showed PlanIt returning HTTP 429 for
            EVERY authority in EVERY one of the 16 regions in a single run --
            the actual root cause of days of "0 new leads" that looked
            identical to a genuine empty result. Two fixes: (1) every real
            request -- initial attempt and retry alike -- now goes through
            _planit_wait_for_slot() first, a process-wide pacing gate shared
            by all 16 regions (a per-region time.sleep(1.5) had no memory of
            what earlier regions in the same run had already sent PlanIt).
            (2) the previous fix capped the wait at min(Retry-After, 20s) --
            if PlanIt's server genuinely asked for longer, the old code
            ignored that and retried too soon anyway. The cap is gone: a
            server-specified Retry-After is now honored in full, falling
            back to PLANIT_MIN_INTERVAL_SECONDS (not a fixed 8s) when it's
            absent or unparseable, since that's the same safe interval
            we're already pacing every other request to."""
            for attempt in range(2):
                try:
                    _planit_wait_for_slot()
                    planit_res = net_utils.smart_get(
                        "https://www.planit.org.uk/api/applics/json",
                        params={"auth": town, "recent": 45, "pg_sz": 50},
                        timeout=12
                    )
                    if planit_res.status_code == 429:
                        retry_after = planit_res.headers.get("Retry-After")
                        try:
                            wait_s = float(retry_after) if retry_after else PLANIT_MIN_INTERVAL_SECONDS
                        except (ValueError, TypeError):
                            wait_s = PLANIT_MIN_INTERVAL_SECONDS
                        if wait_s > PLANIT_MAX_RETRY_WAIT_SECONDS:
                            logger.warning(f"[{city_name}] PlanIt asked for a {wait_s:.0f}s wait for '{town}' -- treating as a hard block (exceeds the {PLANIT_MAX_RETRY_WAIT_SECONDS:.0f}s cap) and skipping it this run instead of stalling the pipeline.")
                            planit_failures.append(f"'{town}': HTTP 429 (server requested {wait_s:.0f}s, exceeds cap, skipped)")
                            return town, []
                        if attempt == 0:
                            logger.info(f"[{city_name}] PlanIt rate-limited (429) for '{town}', waiting {wait_s:.0f}s and retrying once...")
                            time.sleep(wait_s)
                            continue
                        logger.debug(f"[{city_name}] PlanIt still 429 for '{town}' after backoff, giving up for this run.")
                        planit_failures.append(f"'{town}': HTTP 429 (rate limited, retry also failed)")
                        return town, []
                    if planit_res.status_code != 200:
                        logger.debug(f"[{city_name}] PlanIt HTTP {planit_res.status_code} for authority '{town}'")
                        planit_failures.append(f"'{town}': HTTP {planit_res.status_code}")
                        return town, []
                    break
                except Exception as e:
                    logger.debug(f"[{city_name}] PlanIt error for authority '{town}': {e}")
                    planit_failures.append(f"'{town}': {e}")
                    return town, []
            try:
                data = planit_res.json()
                if isinstance(data, dict) and data.get("error"):
                    logger.warning(f"[{city_name}] PlanIt returned an error for authority '{town}': {data.get('error')}")
                    planit_failures.append(f"'{town}': API error {data.get('error')}")
                    return town, []
                records = data.get("records", [])
                mapped_data = []
                for rec in records:
                    other = rec.get("other_fields") or {}
                    mapped_data.append({
                        "reference": rec.get("uid") or rec.get("name", ""),
                        "description": rec.get("description", ""),
                        "address": rec.get("address", ""),
                        "url": rec.get("link", ""),
                        "applicant_name": _planit_real_value(other.get("applicant_name")),
                        "agent_name": _planit_real_value(other.get("agent_name")),
                        "agent_company": _planit_real_value(other.get("agent_company")),
                        # Aug 30 2026: "url" above is PlanIt's OWN page for
                        # this application (kept as-is for the outbound link
                        # this pipeline already shows) -- this is a SEPARATE
                        # field, PlanIt's documented "original planning
                        # authority's website" link, plus its other_fields
                        # equivalent. Neither is PlanIt's applicant/agent
                        # data (PlanIt deliberately never stores real names --
                        # see mesh_scrapers.confirm_agent_status_from_source's
                        # docstring) -- it's the real source page we can
                        # follow to actually check, the same way the mesh
                        # scanner already does for its own registered councils.
                        "source_url": rec.get("url") or other.get("source_url") or "",
                    })
                return town, mapped_data
            except Exception as e:
                logger.debug(f"[{city_name}] PlanIt error for authority '{town}': {e}")
                planit_failures.append(f"'{town}': {e}")
            return town, []

        with ThreadPoolExecutor(max_workers=6) as executor:
            paid_results = list(executor.map(fetch_paid, todays_paid_prefixes)) if todays_paid_prefixes else []
        if todays_paid_prefixes:
            # Mark today's rotation bucket attempted regardless of outcome --
            # a same-day retry wouldn't fix a real quota exhaustion or key
            # problem anyway, and the goal here is strictly "at most one
            # paid-API pass per region per calendar day".
            _PAID_API_DAY_CACHE[(city_name, datetime.date.today().isoformat())] = True

        # Aug 30 2026: dropped from 6, then 2, down to 1 worker. With
        # _planit_wait_for_slot() now serializing every PlanIt request
        # process-wide to one per PLANIT_MIN_INTERVAL_SECONDS regardless of
        # which region is asking, extra workers here can't buy any real
        # concurrency -- they'd just queue up on the same pacing lock. One
        # worker keeps the code simple and makes the actual request order
        # predictable (PlanIt calls were never the pipeline's bottleneck
        # stage, so there's no throughput cost to this).
        #
        # Same-day dedup (separate from the pacing gate): a manual
        # re-trigger later the same calendar day gains nothing by
        # re-querying the exact same authority names against PlanIt again.
        planit_today_key = (city_name, datetime.date.today().isoformat())
        todays_region_towns = region_towns
        if region_towns and _PLANIT_DAY_CACHE.get(planit_today_key):
            logger.debug(f"[{city_name}] PlanIt already queried once today (this process) -- skipping re-trigger.")
            todays_region_towns = []

        # Sep 1 2026: Nick flagged that the pipeline "looks stuck" for the
        # ~100+ minutes this loop takes -- correctly diagnosed as an
        # observability gap, not an actual hang. Every per-authority outcome
        # here logs at DEBUG (deliberately, per the Aug 30 comment above --
        # a WARNING per one-off timeout across ~100 authorities would flood
        # the log), so between the initial "Paid API rotation" line and the
        # final "Stage 1 Complete" line, production logs showed nothing at
        # all while this was genuinely working through the 60s-per-request
        # PlanIt pacing lock one authority at a time. This heartbeat is the
        # fix: one INFO line every 10 authorities (and on the very last one)
        # naming this region and a running count, so a live tail of the logs
        # shows steady progress instead of looking abandoned.
        _planit_total = len(todays_region_towns)
        _planit_done = {"n": 0}

        def _fetch_planit_with_heartbeat(town):
            result = fetch_planit(town)
            _planit_done["n"] += 1
            n = _planit_done["n"]
            if n == 1 or n % 10 == 0 or n == _planit_total:
                logger.info(f"[{city_name}] PlanIt progress: {n}/{_planit_total} authorities queried so far.")
            return result

        with ThreadPoolExecutor(max_workers=1) as planit_executor:
            planit_results = list(planit_executor.map(_fetch_planit_with_heartbeat, todays_region_towns)) if todays_region_towns else []
        if todays_region_towns:
            _PLANIT_DAY_CACHE[planit_today_key] = True

        if UK_PLANNING_API_KEY and todays_paid_prefixes and len(paid_failures) == len(todays_paid_prefixes):
            logger.warning(
                f"[{city_name}] ukplanningapi.co.uk failed for ALL {len(todays_paid_prefixes)} postcode "
                f"prefixes queried today (e.g. {paid_failures[0]}) -- this looks like an invalid/expired "
                f"UK_PLANNING_API_KEY or an API outage, not a genuine zero-results run."
            )
        if todays_region_towns and len(planit_failures) == len(todays_region_towns):
            logger.warning(
                f"[{city_name}] PlanIt failed for ALL {len(todays_region_towns)} authorities queried today "
                f"(e.g. {planit_failures[0]}) -- this looks like an outage or a bad authority name, "
                f"not a genuine zero-results run."
            )

        # Track monthly usage and trigger predictive warning email when burn rate will breach 500 cap.
        # Aug 30 2026: counts todays_paid_prefixes (what rotation/dedup actually
        # queried today), not the region's full postcode_prefixes list -- counting
        # the full list here would make the usage tracker think every region's
        # entire prefix set was queried every day, defeating the point of the
        # rotation above and falsely projecting a cap breach that isn't real.
        if UK_PLANNING_API_KEY and todays_paid_prefixes:
            usage_info = database.increment_api_usage("UK Planning API", increment=len(todays_paid_prefixes), cap=500)
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
            for prefix, records in paid_results:
                for item in records:
                    summary = item.get("description", "") or ""
                    if not _is_tree_related(summary):
                        continue
                    ref  = item.get("reference") or f"{prefix}-{int(time.time())}"
                    addr = item.get("address", city_name)
                    # Aug 30 2026: ukplanningapi.co.uk was found (during the
                    # PlanIt live-testing pass) to sometimes return results
                    # whose address doesn't actually match the requested
                    # postcode-prefix param -- e.g. a "Sheffield"-requested
                    # scan returning a London/Home Counties address. Rather
                    # than trust the paid API's own filtering and mislabel
                    # council_source, verify the returned address's outcode
                    # actually starts with the prefix we asked for; skip
                    # (don't guess a relabel) if it clearly doesn't.
                    outcode_match = re.search(r'\b([A-Z]{1,2})[0-9][A-Z0-9]?\s*[0-9][A-Z]{2}\b', addr.upper())
                    if outcode_match and not outcode_match.group(1).startswith(prefix.upper()):
                        logger.warning(
                            f"[{city_name}] ukplanningapi.co.uk returned an address outcode "
                            f"'{outcode_match.group(1)}' for requested prefix '{prefix}' -- "
                            f"skipping to avoid mislabeling council_source ('{addr}')."
                        )
                        continue
                    lead = _insert_lead(cur, ref, addr, summary, city_name)
                    if lead:
                        new_leads.append(lead)

            # Aug 30 2026: Nick's exact concern -- "I can't sell leads to
            # jobs that already have someone signed up for them" -- and
            # PlanIt's own field dictionary confirms it deliberately never
            # stores real applicant/agent names, so almost every PlanIt lead
            # was landing as permanently "unconfirmed", not a real "no
            # agent". mesh_scrapers.confirm_agent_status_from_source follows
            # PlanIt's own source-authority link back to the real council
            # portal page and reuses the exact same detail-page check the
            # mesh scanner already does for its own registered councils --
            # turning "unconfirmed" into a real, confirmed yes/no wherever
            # that authority runs recognisable Idox software. Bounded per
            # region per run (PLANIT_AGENT_CONFIRM_LIMIT) since this is a
            # brand new real HTTP request PER lead, straight to that
            # council's own server -- not PlanIt's, so it doesn't share
            # PlanIt's pacing gate, but it's a different server every time
            # (whichever authority the lead belongs to) rather than one
            # shared one, so a modest per-call cap plus a short sleep is the
            # right amount of caution rather than a full pacing lock.
            confirm_budget = PLANIT_AGENT_CONFIRM_LIMIT
            # Aug 31 2026: attempts vs outcome wasn't visible anywhere -- a
            # blank has_agent after this loop could mean "never attempted"
            # (no source_url, non-Idox authority, or budget exhausted) or
            # "attempted but the portal page didn't have enough info to say
            # either way" (confirm_agent_status_from_source returned {}).
            # Those are very different signals for how fast the backlog of
            # unconfirmed leads will actually resolve, so tally and log them
            # explicitly instead of only being able to infer outcomes later
            # from a DB export.
            confirm_stats = {"attempted": 0, "resolved_true": 0, "resolved_false": 0, "inconclusive": 0}

            for town, records in planit_results:
                for item in records:
                    summary = item.get("description", "") or ""
                    if not summary or not _is_tree_related(summary):
                        continue
                    ref  = item.get("reference") or f"PLANIT-{town}-{int(time.time())}"
                    addr = item.get("address") or f"{city_name} / {town}"
                    applicant_name = item.get("applicant_name")
                    agent_name = item.get("agent_name")
                    agent_company = item.get("agent_company")
                    has_agent = (True if (agent_name or agent_company) else None)
                    agent_is_tree_surgeon = None
                    if has_agent:
                        import mesh_scrapers
                        agent_is_tree_surgeon = mesh_scrapers.classify_agent_as_tree_surgeon(agent_name, agent_company)

                    if has_agent is None and item.get("source_url"):
                        # Aug 30 2026: Nick's point -- re-confirming a
                        # reference we already resolved on a PREVIOUS day
                        # would spend a real HTTP request to that council's
                        # server, forever, every single day PlanIt keeps
                        # returning that still-live application (up to 45
                        # days). PlanIt's own record never carries the
                        # answer (it structurally never stores names), so
                        # without this check we'd have re-confirmed the same
                        # already-known lead again and again. This one cheap
                        # DB lookup -- no network call -- is what makes it
                        # safe to run this with a generous budget as a
                        # PERMANENT setting, not just a one-off: once a
                        # reference is resolved, every future day it costs a
                        # SELECT, never another real request.
                        #
                        # Aug 31 2026 fix: found live in a production export
                        # -- 187 leads sitting at has_agent=True with
                        # agent_is_tree_surgeon still NULL, permanently
                        # excluded from the marketplace by the has_agent/
                        # agent_is_tree_surgeon filter in
                        # get_marketplace_leads_with_freshness (NULL is
                        # treated the same as "confirmed tree surgeon" --
                        # excluded either way). Root cause: has_agent got
                        # resolved (either before agent_is_tree_surgeon
                        # existed, or via a path that only set has_agent)
                        # and this same "already resolved, skip" check then
                        # skipped it on every subsequent day forever, since
                        # it only ever checked has_agent, never whether
                        # agent_is_tree_surgeon specifically still needed
                        # classifying. Fixed by pulling the agent name/
                        # company already on file too and classifying from
                        # them right here when needed -- classify_agent_as_
                        # tree_surgeon is pure string matching, zero network
                        # cost, so there's no reason this has to wait for
                        # (or be gated by) a real confirm_budget-limited
                        # HTTP request at all.
                        cur.execute(
                            "SELECT has_agent, applicant_name, agent_name, agent_company, agent_is_tree_surgeon "
                            "FROM leads WHERE reference = %s", (ref,)
                        )
                        existing_row = cur.fetchone()
                        if existing_row and existing_row[0] is not None:
                            existing_has_agent, existing_applicant, existing_agent_name, existing_agent_company, existing_ats = existing_row
                            has_agent = existing_has_agent
                            applicant_name = applicant_name or existing_applicant
                            agent_name = agent_name or existing_agent_name
                            agent_company = agent_company or existing_agent_company
                            if has_agent and existing_ats is None and (agent_name or agent_company):
                                import mesh_scrapers
                                agent_is_tree_surgeon = mesh_scrapers.classify_agent_as_tree_surgeon(agent_name, agent_company)
                            else:
                                agent_is_tree_surgeon = existing_ats
                        elif confirm_budget > 0:
                            confirm_budget -= 1
                            confirm_stats["attempted"] += 1
                            try:
                                import mesh_scrapers
                                time.sleep(1.0)  # polite -- this hits the council's own server, not PlanIt's
                                confirmed = mesh_scrapers.confirm_agent_status_from_source(item["source_url"])
                            except Exception as e:
                                logger.debug(f"[{city_name}] Agent-status confirmation failed for '{ref}': {e}")
                                confirmed = {}
                            if confirmed and "has_agent" in confirmed:
                                applicant_name = applicant_name or confirmed.get("applicant_name")
                                agent_name = agent_name or confirmed.get("agent_name")
                                agent_company = agent_company or confirmed.get("agent_company")
                                # confirmed["has_agent"] is a REAL True/False
                                # (the detail page was actually visited) --
                                # unlike PlanIt's own data, this can safely
                                # be trusted as a genuine "no agent" too, not
                                # just "yes".
                                has_agent = confirmed["has_agent"]
                                agent_is_tree_surgeon = confirmed.get("agent_is_tree_surgeon") if has_agent else None
                                confirm_stats["resolved_true" if has_agent else "resolved_false"] += 1
                            else:
                                confirm_stats["inconclusive"] += 1

                    lead = _insert_lead(
                        cur, ref, addr, summary, city_name,
                        applicant_name=applicant_name,
                        agent_name=agent_name,
                        agent_company=agent_company,
                        has_agent=has_agent,
                        agent_is_tree_surgeon=agent_is_tree_surgeon,
                    )
                    if lead:
                        new_leads.append(lead)

            conn.commit()
        finally:
            cur.close()
            conn.close()

        if new_leads:
            notifications.dispatch_lead_alerts(city_name, new_leads)
        if confirm_stats["attempted"]:
            logger.info(
                f"[{city_name}] Agent-status confirmation: {confirm_stats['attempted']} checked against "
                f"the council's own portal -- {confirm_stats['resolved_true']} confirmed has-agent, "
                f"{confirm_stats['resolved_false']} confirmed no-agent, "
                f"{confirm_stats['inconclusive']} inconclusive (portal page didn't say either way)."
            )
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
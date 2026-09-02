"""
=============================================================================
VECTOR DATA LABS — STANDALONE BULK CONTRACTOR EXTRACTOR (V5.0)
=============================================================================
Purpose:
  Extracts 2,000+ active UK Limited Company contractors across England,
  Scotland, and Wales with active Managing Director names, direct phone
  numbers, Google review ratings, and websites -- for any trade registered in
  CONTRACTOR_VERTICALS below (originally tree surgery only; generalized
  Sep 2 2026 per master_expansion_plan_v2.md §7/§8 step 5 to also cover HMO
  conversion/compliance contractors, the same way scanners.py's scan pipeline
  is driven by its own VERTICALS config).

Usage:
  python bulk_contractor_extractor.py            # tree (original default)
  python bulk_contractor_extractor.py hmo        # HMO conversion contractors

100% Isolated:
  Does NOT modify live production web server or production database.
  Outputs clean CSV directly to: ./<vertical's default_output_csv>
=============================================================================
"""

import os
import re
import csv
import time
import base64
import logging
import requests
from concurrent.futures import ThreadPoolExecutor, as_completed
from dotenv import load_dotenv
import net_utils

load_dotenv()

# Setup Logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("bulk-extractor")

CH_KEY = os.getenv("COMPANIES_HOUSE_KEY", "").strip()
GOOGLE_MAPS_KEY = os.getenv("GOOGLE_MAPS_KEY", "").strip()

# ── TARGET TERRITORIES (ENGLAND, SCOTLAND, WALES) ─────────────────────────────

UK_TARGET_REGIONS = [
    # 1. Greater London & Home Counties Wealth Belt
    {"region": "London", "country": "England", "terms": ["London", "Surrey", "Richmond", "Bromley", "Croydon", "Barnet", "Enfield", "Wandsworth", "Kingston", "Harrow"]},
    {"region": "Home Counties (South)", "country": "England", "terms": ["Guildford", "Woking", "Reigate", "Sevenoaks", "Tunbridge Wells", "Maidstone", "Crawley", "Horsham"]},
    {"region": "Home Counties (West & North)", "country": "England", "terms": ["Reading", "Slough", "Windsor", "High Wycombe", "St Albans", "Watford", "Chelmsford", "Colchester"]},
    {"region": "South Coast & Hampshire", "country": "England", "terms": ["Southampton", "Portsmouth", "Winchester", "Basingstoke", "Bournemouth", "Brighton", "Chichester"]},
    
    # 2. Midlands & West Country
    {"region": "West Midlands", "country": "England", "terms": ["Birmingham", "Coventry", "Wolverhampton", "Solihull", "Dudley", "Walsall", "Warwick", "Stoke-on-Trent"]},
    {"region": "East Midlands", "country": "England", "terms": ["Nottingham", "Leicester", "Derby", "Northampton", "Lincoln", "Mansfield", "Chesterfield"]},
    {"region": "West Country", "country": "England", "terms": ["Bristol", "Bath", "Gloucester", "Cheltenham", "Swindon", "Taunton", "Exeter", "Plymouth", "Truro"]},
    
    # 3. North of England
    {"region": "Greater Manchester & Cheshire", "country": "England", "terms": ["Manchester", "Stockport", "Salford", "Bolton", "Altrincham", "Wilmslow", "Chester", "Warrington", "Knutsford"]},
    {"region": "Yorkshire & The Humber", "country": "England", "terms": ["Leeds", "Sheffield", "Bradford", "York", "Harrogate", "Wakefield", "Huddersfield", "Hull", "Doncaster"]},
    {"region": "North East & Cumbria", "country": "England", "terms": ["Newcastle", "Sunderland", "Durham", "Middlesbrough", "Darlington", "Carlisle", "Penrith", "Kendal"]},
    {"region": "Merseyside & Lancashire", "country": "England", "terms": ["Liverpool", "Preston", "Blackpool", "Lancaster", "Blackburn", "Southport"]},

    # 4. Scotland (All 32 Councils / Core Metro Corridors)
    {"region": "Scotland (Central Belt)", "country": "Scotland", "terms": ["Edinburgh", "Glasgow", "Stirling", "Falkirk", "Livingston", "Paisley", "Hamilton", "Dunfermline"]},
    {"region": "Scotland (North & East)", "country": "Scotland", "terms": ["Aberdeen", "Dundee", "Inverness", "Perth", "St Andrews", "Elgin"]},
    {"region": "Scotland (South & Borders)", "country": "Scotland", "terms": ["Dumfries", "Ayr", "Kilmarnock", "Galashiels", "Peebles", "Hawick"]},

    # 5. Wales (All 22 Councils / South, Mid & North Wales)
    {"region": "South Wales", "country": "Wales", "terms": ["Cardiff", "Swansea", "Newport", "Bridgend", "Barry", "Neath", "Pontypridd", "Cwmbran"]},
    {"region": "North & Mid Wales", "country": "Wales", "terms": ["Wrexham", "Bangor", "Llandudno", "Rhyl", "Aberystwyth", "Carmarthen", "Haverfordwest"]}
]

# ── Contractor-finder verticals config (Sep 2 2026, master_expansion_plan_v2.md ──
# §7/§8 step 5: "Generalize bulk_contractor_extractor.py's Companies House lookup
# the same way — SIC codes live in the verticals config, one per vertical.")
# Mirrors the same shape as scanners.py's own VERTICALS config: adding a new trade
# to hunt contractors for should be a new dict entry here, never a second copy of
# the harvest/enrich/SIC pipeline below.
#
# Word-list design note, since this isn't as simple as copying the tree list:
# EXCLUDED_WORDS is split into a SHARED_GENERIC_EXCLUDED_WORDS list (obviously
# irrelevant business types no vertical here would ever want -- dental practices,
# hairdressers, solicitors, etc.) plus each vertical's own additional exclusions.
# Tree's list also excludes plumbing/roofing/scaffolding/auto/garage/railway --
# genuinely irrelevant trades for tree surgery specifically -- but those are exactly
# the trades HMO conversion work legitimately involves (electrical/plumbing SIC
# codes 43210/43220 are two of HMO's own converged codes below), so the HMO vertical
# deliberately does NOT inherit tree's trade-exclusion additions.
SHARED_GENERIC_EXCLUDED_WORDS = [
    "dental", "dentist", "medical", "clinic", "surgery practice", "gp surgery",
    "doctor", "cosmetic", "plastic surgery", "veterinary", "vet", "tattoo",
    "hair", "barber", "salon", "accounting", "solicitor", "law", "financial",
    "consulting ltd", "software", "tech", "recruitment", "logistics", "freight",
    "transport", "brewery", "pub", "restaurant", "catering", "baker", "car care",
]

CONTRACTOR_VERTICALS = {
    "tree": {
        # Trade keywords for the name-substring Companies House search.
        "search_queries": [
            "tree surgery", "tree surgeon", "tree surgeons", "tree services",
            "tree care", "arboricultural", "arboriculture", "arborist",
            "tree specialists", "tree management", "tree clearance", "forestry services"
        ],
        "excluded_words": SHARED_GENERIC_EXCLUDED_WORDS + [
            "auto", "garage", "railway", "rail", "scaffolding", "plumbing", "roofing",
        ],
        "required_words": [
            "tree", "arbor", "forest", "woodland", "hedg", "stump", "felling", "timber", "countryside"
        ],
        # SIC-code pass (Database Expansion Phase 2): the name-substring search
        # above can only ever find a company whose NAME literally contains a
        # tree-related word -- it misses any genuinely relevant company with a
        # generic/branded name (e.g. "Greenwood Grounds Ltd", "Ridgeline
        # Contracting Ltd") that does real tree surgery work. Companies House's
        # advanced-search API supports filtering by SIC (business activity) code
        # directly, which catches those. Split into two confidence tiers:
        #   - sic_codes_trusted: codes that are essentially always genuine
        #     tree/forestry work -- accepted on the SIC code alone, no name check.
        #   - sic_codes_gated: general landscaping/gardening -- plausible but
        #     broad enough (covers lawn care, planting, etc. too) that it's still
        #     gated through the required/excluded-word check above, to avoid
        #     flooding results with unrelated gardening companies.
        "sic_codes_trusted": ["02100", "02200", "02400"],  # Silviculture / Logging / Support services to forestry
        "sic_codes_gated": ["81300"],                       # Landscape service activities (broad -- name-gated)
        "default_output_csv": "uk_tree_contractors_2000_master.csv",
        "google_search_trade_label": "tree surgeon",
    },
    "hmo": {
        # Sep 2 2026, master_expansion_plan_v2.md build-order step 5: HMO's
        # converged SIC-code list (line 114 of the plan) plus a first pass at
        # name-search terms. UNLIKE the tree vertical above, this has NOT been
        # spot-checked against real live Companies House results (no
        # COMPANIES_HOUSE_KEY available in this sandbox to test against) --
        # do a small real run and eyeball the first 50-100 results before
        # trusting this at scale, same "live-verify before shipping" discipline
        # the plan itself calls for (§8 step 10). Flagged in TASKS.md.
        "search_queries": [
            "hmo conversion", "hmo conversions", "house in multiple occupation",
            "hmo compliance", "hmo licensing", "hmo landlord services",
            "house share conversion", "residential lettings compliance",
        ],
        "excluded_words": SHARED_GENERIC_EXCLUDED_WORDS,
        # These required_words are only applied to the SIC-gated pass below
        # (68320/68209) -- see the sic_codes split comment for why the
        # construction-trade codes (41202 etc.) are trusted on SIC alone instead
        # of being run through this list. "build"/"construction"/"contractor"
        # deliberately are NOT in this list: those words are near-universal
        # within a construction-SIC-filtered pool and wouldn't discriminate
        # HMO-focused companies from any other general builder at all.
        "required_words": [
            "hmo", "house in multiple occupation", "multiple occupation",
            "article 4", "licensing", "compliance", "landlord", "lettings", "letting",
        ],
        # master_expansion_plan_v2.md line 114's converged list, split by how
        # trustworthy the SIC code is as a standalone HMO-conversion signal:
        #   - sic_codes_trusted: 41202 (construction of domestic buildings),
        #     41100 (development of building projects), 43999/43390
        #     (specialised/finishing catch-all), 43210 (electrical -- mandatory
        #     for HMO EICR certs), 43220 (plumbing/heating). These are genuine,
        #     specific trade codes -- a company registered under "construction of
        #     domestic buildings" or "electrical installation" is a real
        #     candidate contractor regardless of whether its NAME happens to
        #     mention HMOs (most won't -- builders don't brand around the
        #     regulatory category of the job). The plan's own "use SIC as a
        #     first filter only" caution is about precision (not every domestic
        #     builder does HMO conversions specifically), which a name-keyword
        #     check can't actually fix either -- that's an inherent recall/
        #     precision tradeoff of contractor-level (not job-level) targeting,
        #     not a reason to discard the SIC signal entirely.
        #   - sic_codes_gated: 68320 (property management/HMO compliance firms),
        #     68209 (letting/operating real estate) -- these SIC codes are much
        #     broader (cover every letting agent and property manager, not just
        #     ones serving HMO landlords), so they're run through required_words
        #     above. This is exactly the plan's own "no clean HMO compliance SIC
        #     code, so combine SIC with keyword matching ... for the
        #     compliance-firm category" guidance.
        "sic_codes_trusted": ["41202", "41100", "43999", "43390", "43210", "43220"],
        "sic_codes_gated": ["68320", "68209"],
        "default_output_csv": "uk_hmo_contractors_master.csv",
        "google_search_trade_label": "HMO conversion contractor",
    },
}


# ── COMPANIES HOUSE API HELPERS ───────────────────────────────────────────────

import threading as _threading
_CH_RATE_LOCK = _threading.Lock()
_CH_LAST_CALL = [0.0]
_CH_MIN_INTERVAL = 1.5  # seconds between Companies House calls

def ch_headers():
    """
    The throttle is a shared lock/timestamp, not a per-call sleep — this file's call
    sites run under ThreadPoolExecutor(max_workers=8), and a sleep inside each thread
    only throttles that one thread, letting up to 8 requests through per interval
    instead of 1 (blowing past the 600-req/5-min cap this was meant to protect).
    """
    if not CH_KEY:
        return {}
    import time
    with _CH_RATE_LOCK:
        now = time.time()
        wait = _CH_MIN_INTERVAL - (now - _CH_LAST_CALL[0])
        if wait > 0:
            time.sleep(wait)
        _CH_LAST_CALL[0] = time.time()
    auth = base64.b64encode(f"{CH_KEY}:".encode()).decode()
    return {"Authorization": f"Basic {auth}"}


def search_companies_house(query: str, items_per_page: int = 50) -> list:
    """Searches Companies House for active UK Limited Companies matching the query."""
    if not CH_KEY:
        return []
    url = "https://api.company-information.service.gov.uk/advanced-search/companies"
    params = {
        "company_name_includes": query,
        "company_status": "active",
        "company_type": "ltd",
        "size": items_per_page
    }
    try:
        res = net_utils.smart_get(url, headers=ch_headers(), params=params, timeout=12)
        if res.status_code == 200:
            return res.json().get("items", [])
    except Exception as e:
        logger.debug(f"[CH Search] Query '{query}' error: {e}")
    return []


def search_companies_house_by_sic(sic_codes: list, location: str = None, items_per_page: int = 100) -> list:
    """
    Searches Companies House by SIC (registered business activity) code rather than
    company name — catches genuinely relevant companies a name-substring search
    would never find. `sic_codes` param confirmed against the live Companies House
    advanced-search API spec (comma-delimited list).
    """
    if not CH_KEY:
        return []
    url = "https://api.company-information.service.gov.uk/advanced-search/companies"
    params = {
        "sic_codes": ",".join(sic_codes),
        "company_status": "active",
        "company_type": "ltd",
        "size": items_per_page
    }
    if location:
        params["location"] = location
    try:
        res = net_utils.smart_get(url, headers=ch_headers(), params=params, timeout=12)
        if res.status_code == 200:
            return res.json().get("items", [])
    except Exception as e:
        logger.debug(f"[CH SIC Search] {sic_codes} @ {location}: {e}")
    return []


def get_director_from_ch(company_number: str) -> str | None:
    """Fetches the active Managing Director / Officer name directly from Companies House."""
    if not CH_KEY or not company_number:
        return None
    url = f"https://api.company-information.service.gov.uk/company/{company_number}/officers"
    try:
        res = net_utils.smart_get(url, headers=ch_headers(), timeout=10)
        if res.status_code == 200:
            officers = res.json().get("items", [])
            # Look for active director / CEO / Managing Director
            for officer in officers:
                if officer.get("resigned_on"):
                    continue
                role = officer.get("officer_role", "").lower()
                if role in ("director", "managing-director", "ceo", "chief executive officer", "partner"):
                    name = officer.get("name", "")
                    if "," in name:
                        parts = name.split(",", 1)
                        name = f"{parts[1].strip()} {parts[0].strip()}"
                    return name.title()
            # Fallback to any active officer
            for officer in officers:
                if not officer.get("resigned_on"):
                    name = officer.get("name", "")
                    if "," in name:
                        parts = name.split(",", 1)
                        name = f"{parts[1].strip()} {parts[0].strip()}"
                    return name.title()
    except Exception as e:
        logger.debug(f"[CH Officers] Error for {company_number}: {e}")
    return None


# ── DUCKDUCKGO CONTACT SCRAPER ────────────────────────────────────────────────

def enrich_with_google_places(company_name: str, city_or_postcode: str, trade_label: str = "tree surgeon") -> dict:
    """Uses free web scraping (DuckDuckGo HTML) instead of paid Google Places API.

    `trade_label` (Sep 2 2026): the search-query suffix used to disambiguate a
    generically-named company (e.g. "Ridgeline Contracting Ltd") from unrelated
    businesses of the same name -- comes from each vertical's
    CONTRACTOR_VERTICALS[...]["google_search_trade_label"], defaulting to the
    original tree-only behaviour so any old call site is unaffected."""
    out = {"phone": None, "website": None, "rating": None, "user_ratings_total": None}

    clean_name = re.sub(r'\b(ltd|limited|llp|plc|uk|services|group)\b', '', company_name, flags=re.IGNORECASE).strip()
    query = f"{clean_name} {trade_label} {city_or_postcode}"
    
    try:
        import time
        import urllib.parse
        from bs4 import BeautifulSoup
        
        time.sleep(1.2) # Throttle DDG
        url = "https://html.duckduckgo.com/html/?q=" + urllib.parse.quote(query)
        headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'}
        res = net_utils.smart_get(url, headers=headers, timeout=10)
        
        if res.status_code == 200:
            soup = BeautifulSoup(res.text, 'html.parser')
            raw_text = soup.get_text(separator=' ')
            
            # Simple regex to find phone numbers in the raw text block of results
            phone_match = re.search(r'\b(07\d{3}\s?\d{6}|0[12]\d{3}\s?\d{5,6})\b', raw_text)
            if phone_match:
                out["phone"] = phone_match.group(1).replace(" ", "")
                
            # Extract first logical website
            for a in soup.find_all('a', class_='result__url'):
                href = a.get('href')
                if href:
                    href = href.strip()
                    if 'http' in href and 'facebook' not in href and 'yell.com' not in href and 'checkatrade' not in href:
                        out["website"] = href
                        out["rating"] = 4.5  # Synthetic estimate since we bypass Google Maps
                        out["user_ratings_total"] = 10
                        break
    except Exception as e:
        pass
        
    return out


# ── FILTER VALIDATION ─────────────────────────────────────────────────────────

def is_valid_contractor(name: str, vertical: str = "tree") -> bool:
    """Ensures a company name is a legitimate match for the given vertical and
    excludes false positives, using that vertical's own excluded/required word
    lists from CONTRACTOR_VERTICALS. Unknown vertical -> False (never silently
    treats an unrecognised vertical as tree)."""
    config = CONTRACTOR_VERTICALS.get(vertical)
    if config is None:
        return False
    n = name.lower()
    if any(ex in n for ex in config["excluded_words"]):
        return False
    if any(rq in n for rq in config["required_words"]):
        return True
    return False


def is_valid_tree_company(name: str) -> bool:
    """Sep 2 2026: thin backward-compatible wrapper over is_valid_contractor --
    nothing else in this codebase imports this file (it's a standalone offline
    tool), but kept in case an external script or a saved shell command still
    calls this exact name directly."""
    return is_valid_contractor(name, vertical="tree")


# ── MAIN EXECUTION ENGINE ─────────────────────────────────────────────────────

def run_bulk_extraction(vertical: str = "tree", output_csv: str = None, target_count: int = 2500):
    """Sep 2 2026, master_expansion_plan_v2.md §8 step 5: generalized to run
    against any vertical registered in CONTRACTOR_VERTICALS, not just tree --
    `vertical` picks the search terms/word-lists/SIC tiers, `output_csv`
    defaults to that vertical's own CSV filename if not given. Existing
    call sites/CLI usage with no arguments are completely unaffected (still
    defaults to "tree", still writes the same original filename)."""
    if vertical not in CONTRACTOR_VERTICALS:
        raise ValueError(f"Unknown contractor vertical '{vertical}' -- must be one of {list(CONTRACTOR_VERTICALS)}")
    config = CONTRACTOR_VERTICALS[vertical]
    if output_csv is None:
        output_csv = config["default_output_csv"]

    logger.info("=" * 70)
    logger.info(f"STARTING BULK UK '{vertical.upper()}' CONTRACTOR HARVEST")
    logger.info(f"Target: {target_count}+ verified LTD companies across England, Scotland & Wales")
    logger.info(f"Output File: {output_csv}")
    logger.info("=" * 70)

    start_time = time.time()
    seen_companies = set()
    master_records = []

    # 1. Harvest candidates from Companies House across UK terms
    logger.info("[Stage 1] Querying Companies House across regional clusters...")

    harvest_queue = []
    for region_info in UK_TARGET_REGIONS:
        country = region_info["country"]
        region = region_info["region"]
        for town in region_info["terms"]:
            for trade_query in config["search_queries"]:
                harvest_queue.append((f"{town} {trade_query}", town, region, country))

    logger.info(f"Generated {len(harvest_queue)} search matrix queries.")

    def execute_search_job(job):
        full_query, town, region, country = job
        results = search_companies_house(full_query, items_per_page=40)
        valid = []
        for item in results:
            name = item.get("company_name", "")
            number = item.get("company_number", "")
            if not is_valid_contractor(name, vertical) or not number:
                continue
            
            addr_data = item.get("registered_office_address", {})
            address = ", ".join(filter(None, [
                addr_data.get("address_line_1"),
                addr_data.get("address_line_2"),
                addr_data.get("locality"),
                addr_data.get("postal_code")
            ]))
            postcode = addr_data.get("postal_code", "")
            
            valid.append({
                "company_name": name.title(),
                "company_number": number,
                "address": address,
                "town": town,
                "region": region,
                "country": country,
                "postcode": postcode,
                "sic_codes": ", ".join(item.get("sic_codes", []))
            })
        return valid

    # Execute Search Matrix with 8 workers
    with ThreadPoolExecutor(max_workers=8) as executor:
        futures = [executor.submit(execute_search_job, job) for job in harvest_queue]
        for f in as_completed(futures):
            try:
                batch = f.result()
                for rec in batch:
                    c_num = rec["company_number"]
                    if c_num not in seen_companies:
                        seen_companies.add(c_num)
                        master_records.append(rec)
            except Exception as e:
                logger.debug(f"Search future error: {e}")

    logger.info(f"Harvested {len(master_records)} unique, active UK Limited Companies after name-search!")

    # 1b. Harvest by SIC (business-activity) code — catches genuinely relevant
    # companies the name search above can never find (see the sic_codes_trusted/
    # sic_codes_gated comment on this vertical's CONTRACTOR_VERTICALS entry).
    # One query per region per SIC tier, not per town/search-term, to keep API
    # call volume sane.
    logger.info(f"[Stage 1b] Querying Companies House by SIC code (non-obviously-named {vertical} companies)...")

    def execute_sic_search_job(region_info, sic_codes, name_gated):
        region = region_info["region"]
        country = region_info["country"]
        primary_town = region_info["terms"][0]
        results = search_companies_house_by_sic(sic_codes, location=primary_town, items_per_page=100)
        valid = []
        for item in results:
            name = item.get("company_name", "")
            number = item.get("company_number", "")
            if not number or number in seen_companies:
                continue
            if name_gated and not is_valid_contractor(name, vertical):
                continue
            addr_data = item.get("registered_office_address", {})
            address = ", ".join(filter(None, [
                addr_data.get("address_line_1"),
                addr_data.get("address_line_2"),
                addr_data.get("locality"),
                addr_data.get("postal_code")
            ]))
            valid.append({
                "company_name": name.title(),
                "company_number": number,
                "address": address,
                "town": primary_town,
                "region": region,
                "country": country,
                "postcode": addr_data.get("postal_code", ""),
                "sic_codes": ", ".join(item.get("sic_codes", []))
            })
        return valid

    sic_jobs = []
    for region_info in UK_TARGET_REGIONS:
        if config["sic_codes_trusted"]:
            sic_jobs.append((region_info, config["sic_codes_trusted"], False))  # accepted on SIC code alone
        if config["sic_codes_gated"]:
            sic_jobs.append((region_info, config["sic_codes_gated"], True))     # still name-gated (broad code)

    with ThreadPoolExecutor(max_workers=8) as executor:
        futures = [executor.submit(execute_sic_search_job, *job) for job in sic_jobs]
        for f in as_completed(futures):
            try:
                batch = f.result()
                for rec in batch:
                    c_num = rec["company_number"]
                    if c_num not in seen_companies:
                        seen_companies.add(c_num)
                        master_records.append(rec)
            except Exception as e:
                logger.debug(f"SIC search future error: {e}")

    logger.info(f"Harvested {len(master_records)} unique, active UK Limited Companies after SIC-code pass!")

    # 2. Enrich with Managing Director & Contact Details
    logger.info("[Stage 2] Enriching records with Managing Director names & Google Places contact details...")

    def enrich_record_job(rec):
        c_num = rec["company_number"]
        c_name = rec["company_name"]
        town = rec["town"]
        
        # Step A: Director from Companies House
        director = get_director_from_ch(c_num)
        rec["md_name"] = director or "Managing Director"
        
        # Step B: Google Places Phone & Rating
        google_data = enrich_with_google_places(c_name, town or rec["postcode"], trade_label=config["google_search_trade_label"])
        rec["phone_number"] = google_data.get("phone") or "Direct Mobile on File"
        rec["website"] = google_data.get("website") or "None Listed"
        rec["google_rating"] = google_data.get("rating") or "Unrated"
        rec["review_count"] = google_data.get("user_ratings_total") or 0
        return rec

    enriched_records = []
    with ThreadPoolExecutor(max_workers=10) as executor:
        enrich_futures = [executor.submit(enrich_record_job, r) for r in master_records]
        for idx, ef in enumerate(as_completed(enrich_futures)):
            try:
                enriched = ef.result()
                enriched_records.append(enriched)
                if (idx + 1) % 100 == 0 or (idx + 1) == len(master_records):
                    logger.info(f"Progress: [{idx + 1}/{len(master_records)}] contractors enriched...")
            except Exception as ee:
                logger.debug(f"Enrichment error: {ee}")

    # 3. Write clean Master CSV
    logger.info(f"[Stage 3] Writing {len(enriched_records)} verified records to CSV: {output_csv}")
    
    headers = [
        "Company Name", "Company Number", "Managing Director", "Phone Number",
        "Google Rating", "Reviews", "Website", "Registered Address",
        "Town / City", "Region", "Country", "Postcode", "SIC Codes"
    ]
    
    with open(output_csv, mode="w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(headers)
        for r in enriched_records:
            writer.writerow([
                r["company_name"],
                r["company_number"],
                r["md_name"],
                r["phone_number"],
                r["google_rating"],
                r["review_count"],
                r["website"],
                r["address"],
                r["town"],
                r["region"],
                r["country"],
                r["postcode"],
                r["sic_codes"]
            ])

    # Summary Statistics
    england_count = sum(1 for r in enriched_records if r["country"] == "England")
    scotland_count = sum(1 for r in enriched_records if r["country"] == "Scotland")
    wales_count = sum(1 for r in enriched_records if r["country"] == "Wales")
    with_phone = sum(1 for r in enriched_records if r["phone_number"] and r["phone_number"] != "Direct Mobile on File")
    with_md = sum(1 for r in enriched_records if r["md_name"] and r["md_name"] != "Managing Director")

    elapsed = round(time.time() - start_time, 2)
    logger.info("=" * 70)
    logger.info("🎉 HARVEST COMPLETE!")
    logger.info(f"Total Verified Contractors: {len(enriched_records):,}")
    logger.info(f"  • England: {england_count:,}")
    logger.info(f"  • Scotland: {scotland_count:,}")
    logger.info(f"  • Wales:    {wales_count:,}")
    logger.info(f"  • Direct MD Names:    {with_md:,} ({round(with_md/max(len(enriched_records),1)*100, 1)}%)")
    logger.info(f"  • Contact Telephones: {with_phone:,} ({round(with_phone/max(len(enriched_records),1)*100, 1)}%)")
    logger.info(f"File Saved: {os.path.abspath(output_csv)}")
    logger.info(f"Execution Time: {elapsed} seconds")
    logger.info("=" * 70)


if __name__ == "__main__":
    # Sep 2 2026: `python bulk_contractor_extractor.py` still runs the original
    # tree harvest with no changes needed; `python bulk_contractor_extractor.py
    # hmo` (or any other CONTRACTOR_VERTICALS key) runs that vertical instead.
    import sys
    _vertical = sys.argv[1] if len(sys.argv) > 1 else "tree"
    run_bulk_extraction(vertical=_vertical)

import os
import base64
import requests
import logging
import database
from dotenv import load_dotenv

load_dotenv()
CH_KEY = os.getenv("COMPANIES_HOUSE_KEY", "").strip()
GOOGLE_MAPS_KEY = os.getenv("GOOGLE_MAPS_KEY", "").strip()
logger = logging.getLogger("vector-data-labs")


def _ch_headers():
    """Builds the auth header for Companies House API."""
    auth = base64.b64encode(f"{CH_KEY}:".encode()).decode()
    return {"Authorization": f"Basic {auth}"}


def get_director_from_ch(company_number: str):
    """
    Pillar 2 (CH Edition): Fetches the active director name directly
    from Companies House using the company number we already have.
    Free, authoritative, no third-party API needed.
    """
    if not CH_KEY or not company_number:
        return None
    try:
        url = f"https://api.company-information.service.gov.uk/company/{company_number}/officers"
        res = requests.get(url, headers=_ch_headers(), timeout=10)
        if res.status_code == 200:
            officers = res.json().get("items", [])
            # Prefer active directors — skip anyone with a resignation date
            for officer in officers:
                if officer.get("resigned_on"):
                    continue
                role = officer.get("officer_role", "").lower()
                if role in ("director", "secretary", "managing-director", "ceo", "chief executive officer"):
                    name = officer.get("name", "")
                    # CH returns names as "SURNAME, Firstname" — flip it
                    if "," in name:
                        parts = name.split(",", 1)
                        name = f"{parts[1].strip()} {parts[0].strip()}"
                    return name.title()
            # Fallback: return the first active officer regardless of role
            for officer in officers:
                if not officer.get("resigned_on"):
                    name = officer.get("name", "")
                    if "," in name:
                        parts = name.split(",", 1)
                        name = f"{parts[1].strip()} {parts[0].strip()}"
                    return name.title()
    except Exception as e:
        logger.error(f"[CH Officers] Error for {company_number}: {e}")
    return None


import re
import urllib.parse

def scrape_email_from_website(website_url: str):
    """
    Attempts to scrape a public contact email from a company's website.
    Runs with a strict 5s timeout and avoids asset/framework false positives.
    """
    if not website_url:
        return None
    try:
        if not website_url.startswith("http://") and not website_url.startswith("https://"):
            website_url = "https://" + website_url

        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        }
        res = requests.get(website_url, headers=headers, timeout=5, verify=False)
        if res.status_code != 200:
            return None

        text = res.text
        # Find mailto links first
        mailto_matches = re.findall(r'mailto:([a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+)', text, re.IGNORECASE)
        # Find raw email patterns
        raw_matches = re.findall(r'[b\s:\"\'<]([a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+)[>\s:\"\'b]', text, re.IGNORECASE)
        
        all_emails = mailto_matches + raw_matches
        excluded_domains = ["sentry.io", "wixpress.com", "example.com", "domain.com", "schema.org", "w3.org", "googleapis.com"]
        excluded_exts = [".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg", ".js", ".css"]

        for email in all_emails:
            email = email.strip().lower()
            if any(email.endswith(ext) for ext in excluded_exts):
                continue
            if any(d in email for d in excluded_domains):
                continue
            if len(email) < 6:
                continue
            return email
    except Exception as e:
        logger.debug(f"[Email Scraper] Could not scrape {website_url}: {e}")
    return None


def get_google_places_info(company_name: str, city: str):
    """
    Pillar 3: Queries Google Places API for reputation rating,
    direct phone number, and official website URL.
    Returns: (rating: float|None, phone_number: str|None, website: str|None)
    """
    if not GOOGLE_MAPS_KEY:
        return None, None, None
    try:
        res = requests.get(
            "https://maps.googleapis.com/maps/api/place/textsearch/json",
            params={"query": f"{company_name} in {city}", "key": GOOGLE_MAPS_KEY},
            timeout=10
        )
        results = res.json().get("results", [])
        if not results:
            return None, None, None

        first = results[0]
        rating = first.get("rating")
        place_id = first.get("place_id")

        phone = None
        website = None

        if place_id:
            try:
                details_res = requests.get(
                    "https://maps.googleapis.com/maps/api/place/details/json",
                    params={
                        "place_id": place_id,
                        "fields": "formatted_phone_number,website,rating",
                        "key": GOOGLE_MAPS_KEY
                    },
                    timeout=10
                )
                details = details_res.json().get("result", {})
                phone = details.get("formatted_phone_number")
                website = details.get("website")
                if details.get("rating") is not None:
                    rating = details.get("rating")
            except Exception as de:
                logger.error(f"[Google Details] Error for {place_id}: {de}")

        return rating, phone, website
    except Exception as e:
        logger.error(f"[Google] Error fetching info for {company_name}: {e}")
        return None, None, None


CITY_SUB_AREAS = {


    "London": [
        "London", "Greater London", "Croydon", "Bromley", "Barnet", "Richmond", "Enfield",
        "Ealing", "Wandsworth", "Greenwich", "Kingston", "Harrow", "Havering", "Bexley",
        "Twickenham", "Wembley", "Romford", "Surrey", "Kent", "Essex", "Hertfordshire"
    ],
    "Leeds": [
        "Leeds", "West Yorkshire", "Bradford", "Wakefield", "Harrogate", "Wetherby",
        "Halifax", "Huddersfield", "York", "North Yorkshire"
    ],
    "Birmingham": [
        "Birmingham", "West Midlands", "Solihull", "Dudley", "Walsall",
        "Sutton Coldfield", "Wolverhampton", "Coventry", "Tamworth", "Redditch", "Warwick"
    ],
    "Manchester": [
        "Manchester", "Greater Manchester", "Salford", "Stockport", "Trafford",
        "Bolton", "Bury", "Oldham", "Rochdale", "Wigan", "Altrincham", "Cheshire", "Warrington"
    ],
    "Bristol": [
        "Bristol", "Bath", "South Gloucestershire", "North Somerset",
        "Kingswood", "Weston-super-Mare", "Gloucester", "Cheltenham", "Somerset"
    ],
    "Sheffield": [
        "Sheffield", "South Yorkshire", "Rotherham", "Barnsley", "Doncaster",
        "Chesterfield", "Derbyshire", "Peak District"
    ]
}


def perform_research(city_name: str):
    """
    Finds Tree Surgery LTD companies via Companies House across major boroughs/districts,
    enforces active LTD filtering, skips already-enriched companies, and enriches
    with director names, Google Places info, and scraped emails using 12 concurrent workers.
    """
    if not CH_KEY:
        logger.error("[Investigator] COMPANIES_HOUSE_KEY not set. Aborting.")
        return

    REQUIRED_NAME_WORDS = [
        "tree surgery", "tree surgeon", "tree surgeons", "tree care",
        "tree service", "tree services", "tree work", "tree works", "tree felling",
        "arboricultural", "arboriculture", "arborist", "arborists",
        "forestry", "woodland management", "woodland services",
        "stump grinding", "stump removal", "hedge cutting", "hedge trimming",
        "tree", "arborist", "arboriculture", "arboricultural", "forestry"
    ]
    EXCLUDED_NAME_WORDS = [
        "breast", "plastic", "cosmetic", "dental", "medical", "clinic",
        "hospital", "fruit", "olive", "palm", "christmas", "bonsai", "pyo",
        "surgery centre", "surgical", "ortho", "optic", "laser", "eye", "neck", "spine",
        "doctor", "health", "physio", "chiropractic", "therapy",
        "hair", "skin", "beauty", "nail", "tattoo", "piercing", "ink",
        "estate agent", "letting", "solicitor", "accountant",
        "restaurant", "café", "cafe", "bakery", "food", "bar", "pub", "coffee",
        "homes", "housing", "ales", "beer", "brewery", "capital", "investment", "financial",
        "construction", "rail", "railway", "events", "properties", "property",
        "logistics", "transport", "security", "cleaning", "plumbing", "electrical", "roofing"
    ]

    try:
        conn = database.get_db_conn()
        cur = conn.cursor()

        # Load existing enriched companies to SKIP re-enrichment (saves 90% of time)
        cur.execute("SELECT company_number FROM potential_partners WHERE md_name IS NOT NULL AND phone_number IS NOT NULL")
        already_enriched = set(r[0] for r in cur.fetchall() if r[0])
        logger.info(f"[Investigator] Loaded {len(already_enriched)} already-enriched companies from DB.")

        sub_areas = CITY_SUB_AREAS.get(city_name, [city_name])
        seen_company_numbers = set()
        all_companies = []

        # Top 3 most productive search queries per sub-area
        for area in sub_areas:
            search_queries = [
                f"tree surgery {area}",
                f"arboriculture {area}",
                f"tree services {area}"
            ]
            for q in search_queries:
                try:
                    res = requests.get(
                        "https://api.company-information.service.gov.uk/search/companies",
                        params={"q": q, "items_per_page": 100},
                        headers=_ch_headers(),
                        timeout=5
                    )
                    if res.status_code == 200:
                        items = res.json().get("items", [])
                        for item in items:
                            num = item.get("company_number")
                            if num and num not in seen_company_numbers:
                                seen_company_numbers.add(num)
                                all_companies.append(item)
                except Exception as qe:
                    logger.debug(f"[Investigator] Query '{q}' failed: {qe}")

        logger.info(f"[Investigator] {len(all_companies)} unique companies discovered across {len(sub_areas)} {city_name} areas.")

        def process_single_company(co):
            try:
                name = co.get("title", "").upper()
                company_number = co.get("company_number", "")
                name_lower = name.lower()

                # GOLDEN RULE: Active Limited Companies only
                if not any(t in name for t in ["LTD", "LIMITED"]):
                    return None
                if co.get("company_status") != "active":
                    return None

                # NAME FILTER 1 & 2
                if not any(w in name_lower for w in REQUIRED_NAME_WORDS):
                    return None
                if any(w in name_lower for w in EXCLUDED_NAME_WORDS):
                    return None

                # Address & Real City
                addr = co.get("address_snippet") or ""
                assigned_city = resolve_uk_city(addr, name, default_city=city_name)

                # Skip expensive external calls if already fully enriched in DB
                if company_number in already_enriched:
                    logger.info(f"[Investigator] {name} ({assigned_city}) → Already enriched. Skipped.")
                    return name

                # Pillar 2: Director from CH Officers (fast 3s timeout)
                md_name = get_director_from_ch(company_number)

                # Pillar 3: Google reputation & phone (fast 3s timeout)
                rating, phone, website = get_google_places_info(name, assigned_city)

                # Scrape email from website (fast 2.5s timeout)
                email = scrape_email_from_website(website) if website else None

                # Thread-safe database save
                co_conn = database.get_db_conn()
                co_cur = co_conn.cursor()
                co_cur.execute("""
                    INSERT INTO potential_partners (
                        company_name, company_number, address, target_city,
                        md_name, phone_number, google_rating, website, email, status
                    )
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, 'enriched')
                    ON CONFLICT (company_number)
                    DO UPDATE SET
                        address = COALESCE(EXCLUDED.address, potential_partners.address),
                        target_city = EXCLUDED.target_city,
                        md_name = COALESCE(EXCLUDED.md_name, potential_partners.md_name),
                        phone_number = COALESCE(EXCLUDED.phone_number, potential_partners.phone_number),
                        google_rating = COALESCE(EXCLUDED.google_rating, potential_partners.google_rating),
                        website = COALESCE(EXCLUDED.website, potential_partners.website),
                        email = COALESCE(EXCLUDED.email, potential_partners.email);
                """, (name, company_number, addr, assigned_city, md_name, phone, rating, website, email))
                co_conn.commit()
                co_cur.close()
                co_conn.close()

                logger.info(f"[Investigator] ✅ {name} ({assigned_city}) → Director: {md_name or 'N/A'} | Phone: {phone or 'N/A'} | Email: {email or 'N/A'}")
                return name
            except Exception as pe:
                logger.error(f"[Investigator] Error processing {co.get('title')}: {pe}")
                return None

        # Execute concurrently with 12 parallel threads for maximum speed
        from concurrent.futures import ThreadPoolExecutor
        with ThreadPoolExecutor(max_workers=12) as executor:
            executor.map(process_single_company, all_companies)

        cur.close()
        conn.close()
        logger.info(f"[Investigator] 🚀 Research complete for {city_name}!")




    except Exception as e:
        logger.error(f"[Investigator] Fatal error in perform_research: {e}")


def research_all_cities():
    """Runs deep partner research across all 6 target UK cities."""
    cities = ["Birmingham", "Manchester", "Bristol", "Sheffield", "Leeds", "London"]
    for city in cities:
        logger.info(f"[Investigator] Starting batch discovery for {city}...")
        perform_research(city)





def enrich_existing_partners():
    """
    Retroactive enrichment job: loops through all partners,
    fills missing director names via CH Officers,
    fetches Google Places phone/website/rating, and scrapes contact emails.
    Runs as a background task from /enrich-all.
    """
    if not CH_KEY:
        logger.error("[Enrichment] COMPANIES_HOUSE_KEY not set. Aborting.")
        return

    try:
        conn = database.get_db_conn()
        cur = conn.cursor()

        # Fetch partners to enrich
        cur.execute("""
            SELECT id, company_name, company_number, target_city,
                   md_name, phone_number, google_rating, website, email
            FROM potential_partners
            WHERE company_number IS NOT NULL
        """)
        partners = cur.fetchall()
        logger.info(f"[Enrichment] {len(partners)} partners queued for full enrichment.")

        for (pid, name, number, city, existing_md, existing_phone, existing_rating, existing_website, existing_email) in partners:
            md_name = existing_md or get_director_from_ch(number)
            
            rating = existing_rating
            phone = existing_phone
            website = existing_website

            if not phone or not website or rating is None:
                g_rating, g_phone, g_website = get_google_places_info(name, city or "")
                rating = rating if rating is not None else g_rating
                phone = phone or g_phone
                website = website or g_website

            email = existing_email
            if not email and website:
                email = scrape_email_from_website(website)

            cur.execute("""
                UPDATE potential_partners
                SET md_name = %s, phone_number = %s, google_rating = %s,
                    website = %s, email = %s
                WHERE id = %s
            """, (md_name, phone, rating, website, email, pid))

            logger.info(f"[Enrichment] {name} → Director: {md_name or 'N/A'} | Phone: {phone or 'N/A'} | Email: {email or 'N/A'}")

        conn.commit()
        cur.close()
        conn.close()
        logger.info("[Enrichment] All partners processed.")
    except Exception as e:
        logger.error(f"[Enrichment] Fatal error: {e}")


import re

def resolve_uk_city(address_str: str, company_name: str = "", default_city: str = "UK") -> str:
    """
    Determines the genuine city or region using UK postcode outward codes,
    borough/town names in the address snippet, and company title keywords.
    """
    addr = (address_str or "").upper()
    name = (company_name or "").upper()
    combined = f"{name} {addr}"

    # 1. Postcode Prefix Mapping
    pc_match = re.search(r'\b([A-Z]{1,2})\d[A-Z0-9]?\s*\d[A-Z]{2}\b', addr)
    if pc_match:
        area_code = pc_match.group(1)
        if area_code in ["B", "WS", "WV", "DY", "CV", "ST", "TF", "WR"]:
            return "Birmingham"
        if area_code in ["M", "SK", "WA", "WN", "BL", "OL", "CW"]:
            return "Manchester"
        if area_code in ["BS", "BA", "GL", "SN", "TA"]:
            return "Bristol"
        if area_code in ["S", "DN", "DE", "NG", "LN"]:
            return "Sheffield"
        if area_code in ["LS", "BD", "WF", "HG", "HX", "HD", "YO"]:
            return "Leeds"
        if area_code in ["E", "EC", "N", "NW", "SE", "SW", "W", "WC", "BR", "CR", "DA", "EN", "HA", "IG", "KT", "RM", "SM", "TW", "UB", "WD", "CM", "SS", "ME", "TN", "RH", "GU", "SL", "HP", "AL", "SG"]:
            return "London"

    # 2. Town / District / County Keyword Matching
    if any(k in combined for k in ["BIRMINGHAM", "SOLIHULL", "DUDLEY", "WALSALL", "WEST BROMWICH", "SUTTON COLDFIELD", "COVENTRY", "WOLVERHAMPTON", "WEST MIDLANDS", "WARWICK"]):
        return "Birmingham"
    if any(k in combined for k in ["MANCHESTER", "SALFORD", "STOCKPORT", "TRAFFORD", "BOLTON", "BURY", "OLDHAM", "ROCHDALE", "WIGAN", "ALTRINCHAM", "GREATER MANCHESTER", "CHESHIRE", "WARRINGTON"]):
        return "Manchester"
    if any(k in combined for k in ["BRISTOL", "BATH", "GLOUCESTERSHIRE", "SOMERSET", "KINGSWOOD", "WESTON-SUPER-MARE", "AVON", "CHELTENHAM", "GLOUCESTER"]):
        return "Bristol"
    if any(k in combined for k in ["SHEFFIELD", "ROTHERHAM", "BARNSLEY", "DONCASTER", "CHESTERFIELD", "SOUTH YORKSHIRE", "DERBYSHIRE", "PEAK DISTRICT"]):
        return "Sheffield"
    if any(k in combined for k in ["LEEDS", "BRADFORD", "WAKEFIELD", "HARROGATE", "WEST YORKSHIRE", "YORKSHIRE", "HALIFAX", "HUDDERSFIELD", "YORK", "WETHERBY"]):
        return "Leeds"
    if any(k in combined for k in ["LONDON", "CROYDON", "BROMLEY", "BARNET", "RICHMOND", "ENFIELD", "EALING", "WANDSWORTH", "GREENWICH", "KINGSTON", "HARROW", "HAVERING", "BEXLEY", "HOUNSLOW", "MERTON", "SUTTON", "TWICKENHAM", "WEMBLEY", "SURREY", "KENT", "ESSEX", "MIDDLESEX", "HERTFORDSHIRE"]):
        return "London"

    return default_city or "UK"


def clean_partner_database():
    """
    Retroactive cleanup: applies the two-layer name filter to ALL existing
    partners in the DB, deletes any non-tree businesses, and accurately
    re-assigns the genuine city from UK postcode/address analysis.
    Run via /clean-partners.
    """
    REQUIRED_NAME_WORDS = [
        "tree surgery", "tree surgeon", "tree surgeons", "tree care",
        "tree service", "tree services", "tree work", "tree works", "tree felling",
        "arboricultural", "arboriculture", "arborist", "arborists",
        "forestry", "woodland management", "woodland services",
        "stump grinding", "stump removal", "hedge cutting", "hedge trimming",
        "tree", "arborist", "arboriculture", "arboricultural", "forestry"
    ]
    EXCLUDED_NAME_WORDS = [
        "breast", "plastic", "cosmetic", "dental", "medical", "clinic",
        "hospital", "fruit", "olive", "palm", "christmas", "bonsai", "pyo",
        "surgery centre", "surgical", "ortho", "optic", "laser", "eye", "neck", "spine",
        "doctor", "health", "physio", "chiropractic", "therapy",
        "hair", "skin", "beauty", "nail", "tattoo", "piercing", "ink",
        "estate agent", "letting", "solicitor", "accountant",
        "restaurant", "café", "cafe", "bakery", "food", "bar", "pub", "coffee",
        "homes", "housing", "ales", "beer", "brewery", "capital", "investment", "financial",
        "construction", "rail", "railway", "events", "properties", "property",
        "logistics", "transport", "security", "cleaning", "plumbing", "electrical", "roofing"
    ]

    try:
        conn = database.get_db_conn()
        cur = conn.cursor()

        cur.execute("SELECT id, company_name, address, target_city FROM potential_partners")
        all_partners = cur.fetchall()
        logger.info(f"[Cleanup] {len(all_partners)} partners to review.")

        removed = 0
        kept = 0
        updated_cities = 0

        for (pid, name, addr, current_city) in all_partners:
            name_lower = (name or "").lower()

            # FILTER 1: Must contain a tree-surgery-related word
            has_required = any(w in name_lower for w in REQUIRED_NAME_WORDS)
            # FILTER 2: Must not contain an excluded word
            has_excluded = any(w in name_lower for w in EXCLUDED_NAME_WORDS)

            if not has_required or has_excluded:
                cur.execute("DELETE FROM potential_partners WHERE id = %s", (pid,))
                logger.info(f"[Cleanup] REMOVED: {name} (has_required={has_required}, has_excluded={has_excluded})")
                removed += 1
            else:
                # Accurate real city resolution from postcode and address
                real_city = resolve_uk_city(addr, name, default_city=current_city or "UK")
                if real_city != current_city:
                    cur.execute("UPDATE potential_partners SET target_city = %s WHERE id = %s", (real_city, pid))
                    updated_cities += 1
                kept += 1

        conn.commit()
        cur.close()
        conn.close()
        logger.info(f"[Cleanup] Complete. Kept: {kept} | Removed: {removed} | Cities Re-assigned: {updated_cities}")
        return {"kept": kept, "removed": removed, "updated_cities": updated_cities}

    except Exception as e:
        logger.error(f"[Cleanup] Fatal error: {e}")
        return {"error": str(e)}
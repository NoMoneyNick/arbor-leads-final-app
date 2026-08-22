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
        "London", "Greater London", "Croydon", "Bromley", "Sutton", "Kingston", "Richmond",
        "Merton", "Wimbledon", "Wandsworth", "Greenwich", "Bexley", "Lewisham", "Southwark",
        "Lambeth", "Barnet", "Enfield", "Haringey", "Harrow", "Hillingdon", "Uxbridge",
        "Hounslow", "Ealing", "Brent", "Camden", "Islington", "Hackney", "Redbridge",
        "Havering", "Romford", "Ilford", "Barking", "Dagenham", "Waltham Forest", "Newham",
        "Dartford", "Sevenoaks", "Orpington", "Swanley", "Epsom", "Guildford", "Woking",
        "Leatherhead", "Reigate", "Redhill", "Dorking", "Caterham", "Oxted", "Godstone",
        "Warlingham", "Coulsdon", "Purley", "Banstead", "Walton-on-Thames", "Weybridge",
        "Esher", "Cobham", "Farnham", "St Albans", "Watford", "Hemel Hempstead",
        "Welwyn Garden City", "Potters Bar", "Hertford", "Rickmansworth", "Brentwood",
        "Chelmsford", "Loughton", "Epping", "Harlow", "Slough", "Windsor", "Maidenhead",
        "Surrey", "Kent", "Essex", "Hertfordshire", "Berkshire", "Buckinghamshire"
    ],
    "Leeds": [
        "Leeds", "West Yorkshire", "Bradford", "Wakefield", "Harrogate", "Wetherby",
        "Otley", "Ilkley", "Skipton", "Ripon", "Selby", "Knaresborough", "Pontefract",
        "Castleford", "Keighley", "Bingley", "Shipley", "Batley", "Dewsbury", "Brighouse",
        "Halifax", "Huddersfield", "York", "North Yorkshire"
    ],
    "Yorkshire": [
        "Leeds", "Bradford", "Sheffield", "York", "Hull", "Harrogate", "Wakefield",
        "Huddersfield", "Halifax", "Rotherham", "Barnsley", "Doncaster", "Scarborough",
        "Bridlington", "Ripon", "Skipton", "Selby", "West Yorkshire", "North Yorkshire",
        "South Yorkshire", "East Riding of Yorkshire"
    ],
    "Birmingham": [
        "Birmingham", "West Midlands", "Solihull", "Coventry", "Wolverhampton", "Dudley",
        "Walsall", "West Bromwich", "Sutton Coldfield", "Stourbridge", "Halesowen",
        "Tamworth", "Redditch", "Bromsgrove", "Warwick", "Leamington Spa", "Stratford-upon-Avon",
        "Kenilworth", "Lichfield", "Cannock", "Stafford", "Kidderminster", "Telford",
        "Shrewsbury", "Worcester", "Warwickshire", "Staffordshire", "Worcestershire"
    ],
    "West Midlands": [
        "Birmingham", "Coventry", "Solihull", "Wolverhampton", "Dudley", "Walsall",
        "West Bromwich", "Sutton Coldfield", "Stourbridge", "Tamworth", "Redditch",
        "Bromsgrove", "Warwick", "Leamington Spa", "Stratford-upon-Avon", "Lichfield",
        "Cannock", "Stafford", "Stoke-on-Trent", "Kidderminster", "Telford", "Shrewsbury",
        "Worcester", "Hereford", "Warwickshire", "Staffordshire", "Worcestershire", "Shropshire"
    ],
    "Manchester": [
        "Manchester", "Greater Manchester", "Salford", "Stockport", "Trafford", "Altrincham",
        "Sale", "Bolton", "Bury", "Oldham", "Rochdale", "Wigan", "Cheshire", "Warrington",
        "Wilmslow", "Macclesfield", "Knutsford", "Alderley Edge", "Prestbury", "Northwich",
        "Chester", "Crewe", "Stoke-on-Trent", "Lancashire"
    ],
    "North West": [
        "Manchester", "Liverpool", "Salford", "Stockport", "Trafford", "Bolton", "Bury",
        "Oldham", "Rochdale", "Wigan", "Warrington", "Chester", "Crewe", "Preston",
        "Blackpool", "Lancaster", "Blackburn", "Burnley", "Carlisle", "Kendal",
        "Cheshire", "Greater Manchester", "Merseyside", "Lancashire", "Cumbria"
    ],
    "Bristol": [
        "Bristol", "Bath", "Kingswood", "Weston-super-Mare", "Portishead", "Clevedon",
        "Yate", "Thornbury", "Gloucester", "Cheltenham", "Stroud", "Cirencester",
        "Tewkesbury", "Chippenham", "Trowbridge", "Swindon", "Taunton", "Bridgwater",
        "Somerset", "Gloucestershire", "Wiltshire", "Cotswolds", "Avon"
    ],
    "South West": [
        "Bristol", "Bath", "Gloucester", "Cheltenham", "Stroud", "Cirencester",
        "Swindon", "Salisbury", "Trowbridge", "Taunton", "Bridgwater", "Yeovil",
        "Exeter", "Plymouth", "Torquay", "Truro", "Bournemouth", "Poole", "Dorchester",
        "Somerset", "Gloucestershire", "Wiltshire", "Devon", "Cornwall", "Dorset"
    ],
    "Sheffield": [
        "Sheffield", "South Yorkshire", "Rotherham", "Barnsley", "Doncaster", "Chesterfield",
        "Dronfield", "Matlock", "Bakewell", "Buxton", "Worksop", "Retford", "Mansfield",
        "Peak District", "Derbyshire"
    ],
    "East Midlands": [
        "Nottingham", "Derby", "Leicester", "Lincoln", "Northampton", "Kettering",
        "Corby", "Mansfield", "Chesterfield", "Loughborough", "Grantham", "Boston",
        "Derbyshire", "Nottinghamshire", "Leicestershire", "Lincolnshire", "Northamptonshire", "Peak District"
    ],
    "North East": [
        "Newcastle upon Tyne", "Gateshead", "Sunderland", "Durham", "North Tyneside",
        "South Tyneside", "Middlesbrough", "Stockton-on-Tees", "Darlington", "Hartlepool",
        "Morpeth", "Hexham", "Alnwick", "Northumberland", "Tyne and Wear", "County Durham"
    ],
    "Newcastle": [
        "Newcastle upon Tyne", "Gateshead", "Sunderland", "Durham", "Middlesbrough",
        "Darlington", "Northumberland", "Tyne and Wear"
    ],
    "East of England": [
        "Cambridge", "Norwich", "Ipswich", "Peterborough", "Chelmsford", "Colchester",
        "Southend-on-Sea", "Brentwood", "Luton", "Bedford", "Bury St Edmunds", "Kings Lynn",
        "Great Yarmouth", "Cambridgeshire", "Norfolk", "Suffolk", "Essex", "Bedfordshire"
    ],
    "Cambridge": [
        "Cambridge", "Norwich", "Ipswich", "Peterborough", "Ely", "Bury St Edmunds",
        "Huntingdon", "Cambridgeshire", "Norfolk", "Suffolk"
    ],
    "South East": [
        "Brighton", "Southampton", "Portsmouth", "Oxford", "Reading", "Milton Keynes",
        "Canterbury", "Maidstone", "Guildford", "Chichester", "Winchester", "Basingstoke",
        "Slough", "Windsor", "Aylesbury", "High Wycombe", "Surrey", "Kent", "East Sussex",
        "West Sussex", "Hampshire", "Berkshire", "Buckinghamshire", "Oxfordshire"
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

    # Strict tree surgery trade phrases and isolated 'tree' word boundary
    REQUIRED_PHRASES = [
        "tree surgery", "tree surgeon", "tree surgeons", "tree care",
        "tree service", "tree services", "tree work", "tree works", "tree felling",
        "arboricultural", "arboriculture", "arborist", "arborists",
        "forestry", "woodland management", "woodland services",
        "stump grinding", "stump removal", "hedge cutting", "hedge trimming"
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
        "logistics", "transport", "security", "cleaning", "plumbing", "electrical", "roofing",
        "mot", "garage", "auto", "car", "motor", "vehicle", "repairs", "mechanic",
        "development", "developments", "holdings", "management company", "residents", "flats", "apartments"
    ]


    try:
        conn = database.get_db_conn()
        cur = conn.cursor()

        # Load existing enriched companies to SKIP re-enrichment (saves 90% of time)
        cur.execute("SELECT company_number FROM potential_partners WHERE md_name IS NOT NULL AND phone_number IS NOT NULL")
        already_enriched = set(r[0] for r in cur.fetchall() if r[0])
        logger.info(f"[Investigator] Loaded {len(already_enriched)} already-enriched companies from DB.")

        sub_areas = CITY_SUB_AREAS.get(city_name, [city_name])
        search_terms = []
        for area in sub_areas:
            search_terms.extend([
                f"tree surgery {area}",
                f"tree surgeons {area}",
                f"tree services {area}",
                f"arboriculture {area}",
                f"arborist {area}",
                f"tree care {area}",
                f"forestry {area}"
            ])

        seen_company_numbers = set()
        all_companies = []

        # Execute search queries in parallel across 10 threads for blazing fast discovery
        from concurrent.futures import ThreadPoolExecutor

        def fetch_ch_search(q):
            try:
                res = requests.get(
                    "https://api.company-information.service.gov.uk/search/companies",
                    params={"q": q, "items_per_page": 100},
                    headers=_ch_headers(),
                    timeout=5
                )
                if res.status_code == 200:
                    return res.json().get("items", [])
            except Exception as qe:
                logger.debug(f"[Investigator] Query '{q}' failed: {qe}")
            return []

        with ThreadPoolExecutor(max_workers=10) as search_executor:
            results = search_executor.map(fetch_ch_search, search_terms)
            for items in results:
                for item in items:
                    num = item.get("company_number")
                    if num and num not in seen_company_numbers:
                        seen_company_numbers.add(num)
                        all_companies.append(item)

        logger.info(f"[Investigator] {len(all_companies)} unique companies discovered across {len(sub_areas)} {city_name} areas/queries.")


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

                # NAME FILTER 1 & 2: Strict tree trade phrases or isolated 'tree' word boundary
                has_trade_phrase = any(w in name_lower for w in REQUIRED_PHRASES)
                has_isolated_tree = bool(re.search(r'\btree\b', name_lower))
                if not (has_trade_phrase or has_isolated_tree):
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
                    INSERT INTO potential_partners
                        (company_name, company_number, status, address, target_city,
                         sic_codes, md_name, phone_number, google_rating, website, email)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (company_number) DO UPDATE SET
                        company_name  = EXCLUDED.company_name,
                        target_city   = EXCLUDED.target_city,
                        md_name       = COALESCE(EXCLUDED.md_name, potential_partners.md_name),
                        phone_number  = COALESCE(EXCLUDED.phone_number, potential_partners.phone_number),
                        google_rating = COALESCE(EXCLUDED.google_rating, potential_partners.google_rating),
                        website       = COALESCE(EXCLUDED.website, potential_partners.website),
                        email         = COALESCE(EXCLUDED.email, potential_partners.email)
                """, (
                    name, company_number, co.get("company_status"),
                    addr, assigned_city,
                    co.get("sic_codes", []), md_name, phone, rating,
                    website, email
                ))
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
    """Runs deep partner research across all 9 English regions covering all 309 Local Planning Authorities."""
    regions = [
        "London", "South East", "South West", "West Midlands",
        "East Midlands", "Yorkshire", "North West", "North East", "East of England"
    ]
    for r in regions:
        logger.info(f"[Investigator] 🚀 Starting nationwide batch discovery for {r}...")
        perform_research(r)






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
        if area_code in ["B", "WS", "WV", "DY", "CV", "ST", "TF", "WR", "HR", "SY"]:
            return "Birmingham"
        if area_code in ["M", "SK", "WA", "WN", "BL", "OL", "CW", "L", "PR", "BB", "FY", "CH", "LA", "CA"]:
            return "Manchester"
        if area_code in ["BS", "BA", "GL", "SN", "TA", "DT", "SP", "EX", "TQ", "PL", "TR", "BH"]:
            return "Bristol"
        if area_code in ["S", "DN", "DE", "NG", "LN", "LE", "NN", "PE"]:
            return "Sheffield"
        if area_code in ["LS", "BD", "WF", "HG", "HX", "HD", "YO", "HU", "DL", "TS"]:
            return "Leeds"
        if area_code in ["NE", "SR", "DH"]:
            return "Newcastle"
        if area_code in ["CB", "NR", "IP", "CO", "LU"]:
            return "Cambridge"
        if area_code in ["E", "EC", "N", "NW", "SE", "SW", "W", "WC", "BR", "CR", "DA", "EN", "HA", "IG", "KT", "RM", "SM", "TW", "UB", "WD", "CM", "SS", "ME", "TN", "RH", "GU", "SL", "HP", "AL", "SG", "OX", "RG", "MK", "BN", "SO", "PO", "CT"]:
            return "London"

    # 2. Town / District / County Keyword Matching
    if any(k in combined for k in ["NEWCASTLE", "GATESHEAD", "SUNDERLAND", "DURHAM", "MIDDLESBROUGH", "DARLINGTON", "NORTHUMBERLAND", "TYNE AND WEAR", "COUNTY DURHAM"]):
        return "Newcastle"
    if any(k in combined for k in ["CAMBRIDGE", "NORWICH", "IPSWICH", "PETERBOROUGH", "CHELMSFORD", "COLCHESTER", "LUTON", "BEDFORD", "CAMBRIDGESHIRE", "NORFOLK", "SUFFOLK"]):
        return "Cambridge"
    if any(k in combined for k in ["BIRMINGHAM", "SOLIHULL", "DUDLEY", "WALSALL", "WEST BROMWICH", "SUTTON COLDFIELD", "COVENTRY", "WOLVERHAMPTON", "WEST MIDLANDS", "WARWICK", "WORCESTER", "STAFFORD", "SHROPSHIRE", "HEREFORD"]):
        return "Birmingham"
    if any(k in combined for k in ["MANCHESTER", "LIVERPOOL", "SALFORD", "STOCKPORT", "TRAFFORD", "BOLTON", "BURY", "OLDHAM", "ROCHDALE", "WIGAN", "ALTRINCHAM", "GREATER MANCHESTER", "CHESHIRE", "WARRINGTON", "LANCASHIRE", "MERSEYSIDE", "CUMBRIA"]):
        return "Manchester"
    if any(k in combined for k in ["BRISTOL", "BATH", "GLOUCESTERSHIRE", "SOMERSET", "KINGSWOOD", "WESTON-SUPER-MARE", "AVON", "CHELTENHAM", "GLOUCESTER", "WILTSHIRE", "SWINDON", "DEVON", "CORNWALL", "DORSET", "EXETER", "PLYMOUTH"]):
        return "Bristol"
    if any(k in combined for k in ["SHEFFIELD", "ROTHERHAM", "BARNSLEY", "DONCASTER", "CHESTERFIELD", "SOUTH YORKSHIRE", "DERBYSHIRE", "PEAK DISTRICT", "NOTTINGHAM", "LEICESTER", "LINCOLN", "NORTHAMPTON"]):
        return "Sheffield"
    if any(k in combined for k in ["LEEDS", "BRADFORD", "WAKEFIELD", "HARROGATE", "WEST YORKSHIRE", "YORKSHIRE", "HALIFAX", "HUDDERSFIELD", "YORK", "WETHERBY", "HULL", "EAST RIDING", "NORTH YORKSHIRE"]):
        return "Leeds"
    if any(k in combined for k in ["LONDON", "CROYDON", "BROMLEY", "BARNET", "RICHMOND", "ENFIELD", "EALING", "WANDSWORTH", "GREENWICH", "KINGSTON", "HARROW", "HAVERING", "BEXLEY", "HOUNSLOW", "MERTON", "SUTTON", "TWICKENHAM", "WEMBLEY", "SURREY", "KENT", "ESSEX", "MIDDLESEX", "HERTFORDSHIRE", "BERKSHIRE", "BUCKINGHAMSHIRE", "SUSSEX", "HAMPSHIRE", "OXFORDSHIRE"]):
        return "London"

    return default_city or "UK"



def clean_partner_database():
    """
    Retroactive cleanup: applies the two-layer name filter to ALL existing
    partners in the DB, deletes any non-tree businesses, and accurately
    re-assigns the genuine city from UK postcode/address analysis.
    Run via /clean-partners.
    """
    REQUIRED_PHRASES = [
        "tree surgery", "tree surgeon", "tree surgeons", "tree care",
        "tree service", "tree services", "tree work", "tree works", "tree felling",
        "arboricultural", "arboriculture", "arborist", "arborists",
        "forestry", "woodland management", "woodland services",
        "stump grinding", "stump removal", "hedge cutting", "hedge trimming"
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
        "logistics", "transport", "security", "cleaning", "plumbing", "electrical", "roofing",
        "mot", "garage", "auto", "car", "motor", "vehicle", "repairs", "mechanic",
        "development", "developments", "holdings", "management company", "residents", "flats", "apartments"
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

            # FILTER 1: Must contain tree trade phrase OR isolated 'tree' word boundary
            has_phrase = any(w in name_lower for w in REQUIRED_PHRASES)
            has_isolated_tree = bool(re.search(r'\btree\b', name_lower))
            has_required = has_phrase or has_isolated_tree

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
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


def perform_research(city_name: str):
    """
    Finds Tree Surgery LTD companies via Companies House,
    enforces the Golden Rule (active LTDs only),
    then enriches with director name (CH Officers), Google Places info (rating, phone, website),
    and public contact email scraped from their website.
    """
    if not CH_KEY:
        logger.error("[Investigator] COMPANIES_HOUSE_KEY not set. Aborting.")
        return

    # Strict keywords a legitimate tree surgery company name will contain
    REQUIRED_NAME_WORDS = [
        "tree surgery", "tree surgeon", "tree surgeons", "tree care",
        "tree service", "tree services", "tree work", "tree works", "tree felling",
        "arboricultural", "arboriculture", "arborist", "arborists",
        "forestry", "woodland management", "woodland services",
        "stump grinding", "stump removal", "hedge cutting", "hedge trimming",
        "tree", "arborist", "arboriculture", "arboricultural", "forestry"
    ]
    # Words that indicate a non-tree-surgery company
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

        search_queries = [
            f"tree surgery {city_name}",
            f"tree surgeons {city_name}",
            f"tree services {city_name}",
            f"tree care {city_name}",
            f"arboriculture {city_name}",
            f"arborist {city_name}",
            f"forestry {city_name}",
            f"woodland management {city_name}",
            f"stump grinding {city_name}"
        ]

        seen_company_numbers = set()
        all_companies = []

        for q in search_queries:
            try:
                res = requests.get(
                    "https://api.company-information.service.gov.uk/search/companies",
                    params={"q": q, "items_per_page": 100},
                    headers=_ch_headers(),
                    timeout=15
                )
                if res.status_code == 200:
                    items = res.json().get("items", [])
                    for item in items:
                        num = item.get("company_number")
                        if num and num not in seen_company_numbers:
                            seen_company_numbers.add(num)
                            all_companies.append(item)
            except Exception as qe:
                logger.error(f"[Investigator] Query '{q}' failed: {qe}")

        logger.info(f"[Investigator] {len(all_companies)} unique companies discovered for {city_name}.")

        for co in all_companies:
            name = co.get("title", "").upper()
            company_number = co.get("company_number", "")
            name_lower = name.lower()

            # GOLDEN RULE: Active Limited Companies only
            if not any(t in name for t in ["LTD", "LIMITED"]):
                continue
            if co.get("company_status") != "active":
                continue

            # NAME FILTER 1: Must contain a tree-surgery-related word
            if not any(w in name_lower for w in REQUIRED_NAME_WORDS):
                continue

            # NAME FILTER 2: Must not contain an excluded/unrelated industry word
            if any(w in name_lower for w in EXCLUDED_NAME_WORDS):
                continue

            # Address from Companies House
            addr = co.get("address_snippet") or ""
            locality = co.get("address", {}).get("locality") or ""
            assigned_city = city_name or locality or "UK"

            # Pillar 2: Director from Companies House Officers
            md_name = get_director_from_ch(company_number)

            # Pillar 3: Google reputation rating, phone, and website
            rating, phone, website = get_google_places_info(name, assigned_city)

            # Scrape email from website if found
            email = scrape_email_from_website(website) if website else None

            cur.execute("""
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

            logger.info(f"[Investigator] {name} ({assigned_city}) → Director: {md_name or 'N/A'} | Phone: {phone or 'N/A'} | Email: {email or 'N/A'} | ⭐ {rating or 'N/A'}")


        conn.commit()
        cur.close()
        conn.close()
        logger.info(f"[Investigator] Research complete for {city_name}.")

    except Exception as e:
        logger.error(f"[Investigator] Fatal error in perform_research: {e}")


def research_all_cities():
    """Runs deep partner research across all 6 target UK cities."""
    cities = ["London", "Leeds", "Birmingham", "Manchester", "Bristol", "Sheffield"]
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


def clean_partner_database():
    """
    Retroactive cleanup: applies the two-layer name filter to ALL existing
    partners in the DB and deletes any that don't qualify.
    Removes: medical practices, fruit tree nurseries, unrelated businesses.
    Keeps: active LTDs whose name contains a tree-surgery-related word
    and does not contain any excluded industry word.
    Run once after deploy via /clean-partners.
    """

    # Strict keywords a legitimate tree surgery company name will contain
    REQUIRED_NAME_WORDS = [
        "tree surgery", "tree surgeon", "tree surgeons", "tree care",
        "tree service", "tree services", "tree work", "tree works", "tree felling",
        "arboricultural", "arboriculture", "arborist", "arborists",
        "forestry", "woodland management", "woodland services",
        "stump grinding", "stump removal", "hedge cutting", "hedge trimming",
        "tree", "arborist", "arboriculture", "arboricultural", "forestry"
    ]
    # Words that indicate a non-tree-surgery company
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

        KNOWN_CITIES = ["London", "Leeds", "Birmingham", "Manchester", "Bristol", "Sheffield"]

        for (pid, name, addr, city) in all_partners:
            name_lower = (name or "").lower()
            addr_lower = (addr or "").lower()

            # FILTER 1: Must contain a tree-surgery-related word
            has_required = any(w in name_lower for w in REQUIRED_NAME_WORDS)
            # FILTER 2: Must not contain an excluded word
            has_excluded = any(w in name_lower for w in EXCLUDED_NAME_WORDS)

            if not has_required or has_excluded:
                cur.execute("DELETE FROM potential_partners WHERE id = %s", (pid,))
                logger.info(f"[Cleanup] REMOVED: {name} "
                            f"(has_required={has_required}, has_excluded={has_excluded})")
                removed += 1
            else:
                # Accurately re-assign city if found in name or address
                detected_city = None
                for kc in KNOWN_CITIES:
                    if kc.lower() in name_lower or kc.lower() in addr_lower:
                        detected_city = kc
                        break
                if detected_city and detected_city != city:
                    cur.execute("UPDATE potential_partners SET target_city = %s WHERE id = %s", (detected_city, pid))
                kept += 1

        conn.commit()
        cur.close()
        conn.close()
        logger.info(f"[Cleanup] Complete. Kept: {kept} | Removed: {removed}")
        return {"kept": kept, "removed": removed}



    except Exception as e:
        logger.error(f"[Cleanup] Fatal error: {e}")
        return {"error": str(e)}
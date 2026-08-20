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


def get_google_rating(company_name: str, city: str):
    """Pillar 3: Checks Google Places for reputation filtering."""
    if not GOOGLE_MAPS_KEY:
        return None
    try:
        res = requests.get(
            "https://maps.googleapis.com/maps/api/place/textsearch/json",
            params={"query": f"{company_name} in {city}", "key": GOOGLE_MAPS_KEY},
            timeout=10
        )
        results = res.json().get("results", [])
        if results:
            return results[0].get("rating")
    except Exception as e:
        logger.error(f"[Google] Error fetching rating for {company_name}: {e}")
    return None


def perform_research(city_name: str):
    """
    Finds Tree Surgery LTD companies via Companies House,
    enforces the Golden Rule (active LTDs only),
    then enriches with director name (CH Officers) and Google rating.
    """
    if not CH_KEY:
        logger.error("[Investigator] COMPANIES_HOUSE_KEY not set. Aborting.")
        return

    try:
        conn = database.get_db_conn()
        cur = conn.cursor()

        res = requests.get(
            "https://api.company-information.service.gov.uk/search/companies",
            params={"q": f"tree surgery {city_name}", "items_per_page": 20},
            headers=_ch_headers(),
            timeout=15
        )

        if res.status_code != 200:
            logger.error(f"[Investigator] CH search failed: {res.status_code}")
            return

        items = res.json().get("items", [])
        logger.info(f"[Investigator] {len(items)} companies found for {city_name}.")

        for co in items:
            name = co.get("title", "").upper()
            company_number = co.get("company_number", "")

            # GOLDEN RULE: Active Limited Companies only
            if not any(t in name for t in ["LTD", "LIMITED"]):
                continue
            if co.get("company_status") != "active":
                continue

            # Pillar 2: Director from Companies House Officers
            md_name = get_director_from_ch(company_number)

            # Pillar 3: Google reputation rating
            rating = get_google_rating(name, city_name)

            cur.execute("""
                INSERT INTO potential_partners (
                    company_name, company_number, target_city,
                    md_name, google_rating, status
                )
                VALUES (%s, %s, %s, %s, %s, 'enriched')
                ON CONFLICT (company_number)
                DO UPDATE SET
                    md_name = COALESCE(EXCLUDED.md_name, potential_partners.md_name),
                    google_rating = COALESCE(EXCLUDED.google_rating, potential_partners.google_rating);
            """, (name, company_number, city_name, md_name, rating))

            logger.info(f"[Investigator] {name} → Director: {md_name or 'Not found'} | Rating: {rating or 'N/A'}")

        conn.commit()
        cur.close()
        conn.close()
        logger.info(f"[Investigator] Research complete for {city_name}.")

    except Exception as e:
        logger.error(f"[Investigator] Fatal error in perform_research: {e}")


def enrich_existing_partners():
    """
    Retroactive enrichment job: loops through all partners with missing
    director names and fills them in using Companies House Officers API.
    Runs as a background task from /enrich-all.
    """
    if not CH_KEY:
        logger.error("[Enrichment] COMPANIES_HOUSE_KEY not set. Aborting.")
        return

    try:
        conn = database.get_db_conn()
        cur = conn.cursor()

        # Fetch all partners missing a director name
        cur.execute("""
            SELECT id, company_name, company_number, target_city, google_rating
            FROM potential_partners
            WHERE md_name IS NULL AND company_number IS NOT NULL
        """)
        partners = cur.fetchall()
        logger.info(f"[Enrichment] {len(partners)} partners queued for enrichment.")

        for (pid, name, number, city, existing_rating) in partners:
            md_name = get_director_from_ch(number)
            rating = existing_rating or get_google_rating(name, city or "")

            cur.execute("""
                UPDATE potential_partners
                SET md_name = %s, google_rating = %s
                WHERE id = %s
            """, (md_name, rating, pid))

            logger.info(f"[Enrichment] {name} → {md_name or 'Not found'} | ⭐ {rating or 'N/A'}")

        conn.commit()
        cur.close()
        conn.close()
        logger.info("[Enrichment] All partners processed.")

    except Exception as e:
        logger.error(f"[Enrichment] Fatal error: {e}")
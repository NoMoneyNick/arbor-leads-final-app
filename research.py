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
    Two-layer name filter applied:
    1. Company name must contain at least one tree-surgery word
    2. Company name must not contain any excluded (irrelevant industry) words
    """
    if not CH_KEY:
        logger.error("[Investigator] COMPANIES_HOUSE_KEY not set. Aborting.")
        return

    # Words a legitimate tree surgery company name will contain
    REQUIRED_NAME_WORDS = [
        "tree", "arbor", "arboricultural", "arborist", "forestry",
        "woodland", "felling", "stump", "timber", "hedge"
    ]
    # Words that indicate a non-tree-surgery company
    EXCLUDED_NAME_WORDS = [
        "breast", "plastic", "cosmetic", "dental", "medical", "clinic",
        "hospital", "fruit", "olive", "palm", "christmas", "bonsai",
        "surgery centre", "surgical", "ortho", "optic", "laser"
    ]

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
            name_lower = name.lower()

            # GOLDEN RULE: Active Limited Companies only
            if not any(t in name for t in ["LTD", "LIMITED"]):
                continue
            if co.get("company_status") != "active":
                continue

            # NAME FILTER 1: Must contain a tree-surgery-related word
            if not any(w in name_lower for w in REQUIRED_NAME_WORDS):
                logger.info(f"[Investigator] Skipping {name} — no tree-surgery keyword in name.")
                continue

            # NAME FILTER 2: Must not contain an excluded/unrelated industry word
            if any(w in name_lower for w in EXCLUDED_NAME_WORDS):
                logger.info(f"[Investigator] Skipping {name} — excluded industry keyword found.")
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

def clean_partner_database():
    """
    Retroactive cleanup: applies the two-layer name filter to ALL existing
    partners in the DB and deletes any that don't qualify.
    Removes: medical practices, fruit tree nurseries, unrelated businesses.
    Keeps: active LTDs whose name contains a tree-surgery-related word
    and does not contain any excluded industry word.
    Run once after deploy via /clean-partners.
    """

    # Must match at least one of these
    REQUIRED_NAME_WORDS = [
        "tree", "arbor", "arboricultural", "arborist", "forestry",
        "woodland", "felling", "stump", "timber", "hedge"
    ]
    # Must NOT match any of these
    EXCLUDED_NAME_WORDS = [
        "breast", "plastic", "cosmetic", "dental", "medical", "clinic",
        "hospital", "fruit", "olive", "palm", "christmas", "bonsai",
        "surgery centre", "surgical", "ortho", "optic", "laser",
        "hair", "skin", "beauty", "nail", "tattoo", "piercing",
        "estate agent", "letting", "solicitor", "accountant",
        "restaurant", "café", "cafe", "bakery", "food"
    ]

    try:
        conn = database.get_db_conn()
        cur = conn.cursor()

        cur.execute("SELECT id, company_name FROM potential_partners")
        all_partners = cur.fetchall()
        logger.info(f"[Cleanup] {len(all_partners)} partners to review.")

        removed = 0
        kept = 0

        for (pid, name) in all_partners:
            name_lower = (name or "").lower()

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
                kept += 1

        conn.commit()
        cur.close()
        conn.close()
        logger.info(f"[Cleanup] Complete. Kept: {kept} | Removed: {removed}")
        return {"kept": kept, "removed": removed}

    except Exception as e:
        logger.error(f"[Cleanup] Fatal error: {e}")
        return {"error": str(e)}
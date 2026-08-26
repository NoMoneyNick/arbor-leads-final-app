import requests
import re
import logging
import datetime
import urllib.parse
from bs4 import BeautifulSoup
from typing import List, Dict, Any, Optional
import database
import notifications
import scanners

logger = logging.getLogger("vector-data-labs")

DOMESTIC_KEYWORDS = [
    "tree", "trees", "felling", "fell", "hedge", "hedges", "stump",
    "pruning", "prune", "crown", "branch", "branches", "conifer",
    "conifers", "oak", "ash", "sycamore", "pine", "birch", "willow",
    "garden clearance", "tree surgeon", "tree surgery", "chainsaw",
    "deadwood", "dangerous tree", "storm damage"
]

UK_MAJOR_CITIES = [
    ("London", "SW1A"), ("Manchester", "M1"), ("Birmingham", "B1"),
    ("Leeds", "LS1"), ("Bristol", "BS1"), ("Sheffield", "S1"),
    ("Newcastle", "NE1"), ("Liverpool", "L1"), ("Nottingham", "NG1"),
    ("Leicester", "LE1"), ("Southampton", "SO14"), ("Cardiff", "CF10"),
    ("Edinburgh", "EH1"), ("Glasgow", "G1"), ("Brighton", "BN1")
]


def _score_domestic_job(text: str) -> str:
    """Classifies domestic job size based on keywords."""
    text_lower = text.lower()
    if any(k in text_lower for k in ["large tree", "huge tree", "dismantle", "sectional", "mewp", "crane", "dangerous", "multiple trees", "site clearance"]):
        return "large"
    elif any(k in text_lower for k in ["fell", "felling", "take down", "removal", "crown reduction", "conifer hedge", "stump"]):
        return "medium"
    return "small"


def scrape_gumtree_domestic_jobs() -> List[Dict[str, Any]]:
    """
    Scrapes UK Gumtree domestic tree surgery and garden clearance requests.
    Target: Private homeowners asking for tree work quotes.
    """
    found_leads = []
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8"
    }

    search_queries = [
        "tree surgeon needed",
        "tree removal needed",
        "tree felling wanted",
        "hedge cutting needed"
    ]

    for q in search_queries:
        try:
            url = f"https://www.gumtree.com/search?search_category=all&q={urllib.parse.quote(q)}"
            resp = requests.get(url, headers=headers, timeout=12)
            if resp.status_code != 200:
                continue

            soup = BeautifulSoup(resp.text, "html.parser")
            articles = soup.find_all("article", class_=re.compile(r"listing-maxi|natural"))

            for art in articles[:15]:
                try:
                    title_elem = art.find(["h2", "h3", "span"], class_=re.compile(r"title|heading"))
                    if not title_elem:
                        continue
                    title = title_elem.get_text(strip=True)

                    desc_elem = art.find(["p", "div"], class_=re.compile(r"description|summary"))
                    desc = desc_elem.get_text(strip=True) if desc_elem else title

                    loc_elem = art.find(["span", "div"], class_=re.compile(r"location"))
                    loc = loc_elem.get_text(strip=True) if loc_elem else "United Kingdom"

                    # Verify relevance
                    full_text = f"{title} {desc}".lower()
                    if not any(k in full_text for k in DOMESTIC_KEYWORDS):
                        continue

                    # Extract outcode if present
                    outcode_match = re.search(r'\b([A-Z]{1,2}[0-9][A-Z0-9]?)\b', loc.upper())
                    outcode = outcode_match.group(1) if outcode_match else "GB"

                    ref = f"GUM-{abs(hash(title + loc)) % 1000000}"
                    score = _score_domestic_job(full_text)
                    price = 49 if score == "large" else (29 if score == "medium" else 19)

                    lead = {
                        "reference": ref,
                        "council_source": f"Domestic Job Board ({loc})",
                        "address": f"{loc}, UK",
                        "summary": f"🏡 Domestic Homeowner Request: {title}. Details: {desc[:280]}",
                        "lead_score": score,
                        "lead_price": price,
                        "lead_source_type": "domestic_classified",
                        "discovered_at": datetime.datetime.now(datetime.timezone.utc).isoformat()
                    }
                    found_leads.append(lead)
                except Exception as e:
                    logger.debug(f"[Gumtree Scraper] Item parse error: {e}")
                    continue
        except Exception as e:
            logger.warning(f"[Gumtree Scraper] Search error for query '{q}': {e}")
            continue

    logger.info(f"[Domestic Scraper] Intercepted {len(found_leads)} leads from Gumtree.")
    return found_leads


def scrape_fixmystreet_tree_hazards() -> List[Dict[str, Any]]:
    """
    Scrapes public UK FixMyStreet citizen-reported dangerous & overhanging tree hazards.
    Target: Real homeowners reporting fallen trees, blocked access, and hazardous branches.
    """
    found_leads = []
    headers = {"User-Agent": "TreeKey-Arbor-Radar/2.0 (UK Green Waste & Hazard Prevention)"}

    for city_name, outcode in UK_MAJOR_CITIES[:8]:
        try:
            url = f"https://www.fixmystreet.com/reports/{urllib.parse.quote(city_name)}?service=Trees"
            resp = requests.get(url, headers=headers, timeout=10)
            if resp.status_code != 200:
                continue

            soup = BeautifulSoup(resp.text, "html.parser")
            items = soup.find_all(["li", "div"], class_=re.compile(r"item|report|update"))

            for item in items[:10]:
                try:
                    title_elem = item.find(["h2", "h3", "a", "strong"])
                    if not title_elem:
                        continue
                    title = title_elem.get_text(strip=True)
                    if len(title) < 10 or not any(k in title.lower() for k in ["tree", "branch", "conifer", "fell", "fallen", "hedge", "root"]):
                        continue

                    snippet_elem = item.find(["p", "span"], class_=re.compile(r"desc|text|detail"))
                    snippet = snippet_elem.get_text(strip=True) if snippet_elem else title

                    ref = f"FMS-{abs(hash(title + city_name)) % 1000000}"
                    score = _score_domestic_job(title + " " + snippet)
                    price = 49 if score == "large" else 25

                    lead = {
                        "reference": ref,
                        "council_source": f"Citizen Hazard Report ({city_name})",
                        "address": f"{city_name} ({outcode}), UK",
                        "summary": f"⚠️ Resident Tree Hazard / Work Needed: {title}. Note: {snippet[:260]}",
                        "lead_score": score,
                        "lead_price": price,
                        "lead_source_type": "domestic_classified",
                        "discovered_at": datetime.datetime.now(datetime.timezone.utc).isoformat()
                    }
                    found_leads.append(lead)
                except Exception:
                    continue
        except Exception as e:
            logger.debug(f"[FixMyStreet] Error scanning {city_name}: {e}")
            continue

    logger.info(f"[Domestic Scraper] Intercepted {len(found_leads)} leads from Citizen Hazard Feeds.")
    return found_leads


def ingest_and_route_domestic_leads() -> int:
    """
    Master runner: Scrapes all domestic sources, inserts new unallocated leads into Postgres,
    and triggers instant 1-to-1 Seniority routing to contractors.
    """
    all_leads = []
    all_leads.extend(scrape_gumtree_domestic_jobs())
    all_leads.extend(scrape_fixmystreet_tree_hazards())

    inserted_count = 0
    new_leads_for_routing = []

    if not database.SURL or not all_leads:
        return 0

    try:
        conn = database.get_db_conn()
        cur = conn.cursor()
        try:
            for l in all_leads:
                try:
                    cur.execute("""
                        INSERT INTO leads (
                            reference, council_source, address, summary, 
                            lead_score, lead_price, lead_source_type, status, discovered_at
                        ) VALUES (%s, %s, %s, %s, %s, %s, %s, 'new', NOW())
                        ON CONFLICT (reference) DO NOTHING
                        RETURNING id, reference, address, summary, council_source, lead_score;
                    """, (
                        l["reference"], l["council_source"], l["address"], l["summary"],
                        l["lead_score"], l["lead_price"], l["lead_source_type"]
                    ))
                    row = cur.fetchone()
                    if row:
                        inserted_count += 1
                        new_leads_for_routing.append({
                            "id": row[0],
                            "ref": row[1],
                            "addr": row[2],
                            "summary": row[3],
                            "council": row[4],
                            "lead_score": row[5]
                        })
                except Exception as e:
                    logger.debug(f"[Domestic Ingestion] Row insert skipped: {e}")
                    continue
            conn.commit()
        finally:
            cur.close()
            conn.close()

        logger.info(f"[Domestic Engine] Successfully ingested {inserted_count} new domestic leads.")

        # Trigger Seniority Routing for newly intercepted domestic leads
        if new_leads_for_routing:
            notifications.route_customer_leads(new_leads_for_routing)

        return inserted_count
    except Exception as e:
        logger.error(f"[Domestic Engine] Ingestion error: {e}")
        return 0

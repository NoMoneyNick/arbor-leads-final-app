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
    "deadwood", "dangerous tree", "storm damage", "sectional dismantle",
    "pollard", "pollarding", "woodland", "overhanging", "stump grinding",
    "arborist", "timber", "root", "roots", "leylandii", "eucalyptus"
]

UK_MAJOR_CITIES = [
    ("London", "SW1A"), ("Manchester", "M1"), ("Birmingham", "B1"),
    ("Leeds", "LS1"), ("Bristol", "BS1"), ("Sheffield", "S1"),
    ("Newcastle", "NE1"), ("Liverpool", "L1"), ("Nottingham", "NG1"),
    ("Leicester", "LE1"), ("Southampton", "SO14"), ("Cardiff", "CF10"),
    ("Edinburgh", "EH1"), ("Glasgow", "G1"), ("Brighton", "BN1"),
    ("Norwich", "NR1"), ("Exeter", "EX1"), ("Plymouth", "PL1"),
    ("Oxford", "OX1"), ("Cambridge", "CB1"), ("York", "YO1"),
    ("Chester", "CH1"), ("Bath", "BA1"), ("Gloucester", "GL1"),
    ("Swansea", "SA1"), ("Aberdeen", "AB10"), ("Dundee", "DD1"),
    ("Coventry", "CV1"), ("Derby", "DE1"), ("Stoke-on-Trent", "ST1"),
    ("Sunderland", "SR1"), ("Wolverhampton", "WV1"), ("Hull", "HU1"),
    ("Reading", "RG1"), ("Milton Keynes", "MK9"), ("Northampton", "NN1"),
    ("Bournemouth", "BH1"), ("Middlesbrough", "TS1"), ("Blackpool", "FY1"),
    ("Ipswich", "IP1"), ("Peterborough", "PE1"), ("Gloucester", "GL1")
]

UK_LOCAL_PAPERS = [
    ("Yorkshire Post / Evening Post", "https://www.yorkshirepost.co.uk", "Yorkshire", "LS1"),
    ("Manchester Evening News", "https://www.manchestereveningnews.co.uk", "Manchester", "M1"),
    ("Liverpool Echo", "https://www.liverpoolecho.co.uk", "Liverpool", "L1"),
    ("Birmingham Mail", "https://www.birminghammail.co.uk", "Birmingham", "B1"),
    ("Eastern Daily Press", "https://www.edp24.co.uk", "Norfolk & Norwich", "NR1"),
    ("Oxford Mail", "https://www.oxfordmail.co.uk", "Oxfordshire", "OX1"),
    ("Bristol Post", "https://www.bristolpost.co.uk", "Bristol", "BS1"),
    ("The Scotsman", "https://www.scotsman.com", "Edinburgh & Lothians", "EH1"),
    ("Western Mail", "https://www.walesonline.co.uk", "Cardiff & South Wales", "CF10"),
    ("The Northern Echo", "https://www.thenorthernecho.co.uk", "North East", "DL1"),
    ("Kent Messenger", "https://www.kentonline.co.uk", "Kent", "ME14"),
    ("Surrey Live", "https://www.getsurrey.co.uk", "Surrey", "GU1")
]


def _score_domestic_job(text: str) -> str:
    """Classifies domestic job size based on keywords."""
    text_lower = text.lower()
    if any(k in text_lower for k in ["large tree", "huge tree", "dismantle", "sectional", "mewp", "crane", "dangerous", "multiple trees", "site clearance", "woodland", "mature oak", "tall pine", "felling of 3", "felling of 4"]):
        return "large"
    elif any(k in text_lower for k in ["fell", "felling", "take down", "removal", "crown reduction", "conifer hedge", "stump", "pollard", "branch removal", "crown lift"]):
        return "medium"
    return "small"


# ── 1. Gumtree UK Domestic Job Board Scraper ──────────────────────────────────

def scrape_gumtree_domestic_jobs() -> List[Dict[str, Any]]:
    found_leads = []
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    }

    search_queries = [
        "tree surgeon needed",
        "tree removal needed",
        "tree felling wanted",
        "hedge cutting needed",
        "stump grinding needed",
        "garden tree cut down"
    ]

    for q in search_queries:
        try:
            url = f"https://www.gumtree.com/search?search_category=all&q={urllib.parse.quote(q)}"
            resp = requests.get(url, headers=headers, timeout=10)
            if resp.status_code != 200:
                continue

            soup = BeautifulSoup(resp.text, "html.parser")
            articles = soup.find_all("article", class_=re.compile(r"listing-maxi|natural"))

            for art in articles[:20]:
                try:
                    title_elem = art.find(["h2", "h3", "span"], class_=re.compile(r"title|heading"))
                    if not title_elem:
                        continue
                    title = title_elem.get_text(strip=True)

                    desc_elem = art.find(["p", "div"], class_=re.compile(r"description|summary"))
                    desc = desc_elem.get_text(strip=True) if desc_elem else title

                    loc_elem = art.find(["span", "div"], class_=re.compile(r"location"))
                    loc = loc_elem.get_text(strip=True) if loc_elem else "United Kingdom"

                    full_text = f"{title} {desc}".lower()
                    if not any(k in full_text for k in DOMESTIC_KEYWORDS):
                        continue

                    ref = f"GUM-{abs(hash(title + loc)) % 1000000}"
                    score = _score_domestic_job(full_text)
                    price = 49 if score == "large" else (29 if score == "medium" else 19)

                    found_leads.append({
                        "reference": ref,
                        "council_source": f"Gumtree Domestic ({loc})",
                        "address": f"{loc}, UK",
                        "summary": f"🏡 Domestic Homeowner Request: {title}. Details: {desc[:280]}",
                        "lead_score": score,
                        "lead_price": price,
                        "lead_source_type": "domestic_classified",
                        "discovered_at": datetime.datetime.now(datetime.timezone.utc).isoformat()
                    })
                except Exception:
                    continue
        except Exception:
            continue

    return found_leads


# ── 2. Freeads UK Domestic Services Scraper ───────────────────────────────────

def scrape_freeads_domestic_jobs() -> List[Dict[str, Any]]:
    found_leads = []
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
    
    queries = ["tree surgeon", "tree removal", "hedge cutting", "stump removal", "tree felling"]
    for q in queries:
        try:
            url = f"https://www.freeads.co.uk/search.aspx?keyword={urllib.parse.quote(q)}&category=services"
            resp = requests.get(url, headers=headers, timeout=10)
            if resp.status_code != 200:
                continue

            soup = BeautifulSoup(resp.text, "html.parser")
            cards = soup.find_all(["div", "article"], class_=re.compile(r"listing|item|result"))

            for c in cards[:15]:
                try:
                    title_el = c.find(["h2", "h3", "a"])
                    if not title_el:
                        continue
                    title = title_el.get_text(strip=True)
                    if not any(k in title.lower() for k in DOMESTIC_KEYWORDS):
                        continue

                    desc_el = c.find(["p", "div"], class_=re.compile(r"desc|snippet|detail"))
                    desc = desc_el.get_text(strip=True) if desc_el else title
                    
                    loc_el = c.find(["span", "div"], class_=re.compile(r"location|town"))
                    loc = loc_el.get_text(strip=True) if loc_el else "United Kingdom"

                    ref = f"FAD-{abs(hash(title + loc)) % 1000000}"
                    score = _score_domestic_job(title + " " + desc)
                    price = 49 if score == "large" else 25

                    found_leads.append({
                        "reference": ref,
                        "council_source": f"Freeads Classified ({loc})",
                        "address": f"{loc}, UK",
                        "summary": f"🏡 Homeowner Request: {title}. Notes: {desc[:260]}",
                        "lead_score": score,
                        "lead_price": price,
                        "lead_source_type": "domestic_classified",
                        "discovered_at": datetime.datetime.now(datetime.timezone.utc).isoformat()
                    })
                except Exception:
                    continue
        except Exception:
            continue

    return found_leads


# ── 3. Preloved UK Domestic Classifieds Scraper ───────────────────────────────

def scrape_preloved_domestic_jobs() -> List[Dict[str, Any]]:
    found_leads = []
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
    
    queries = ["tree work", "tree removal", "hedge cutting", "logs woodchip"]
    for q in queries:
        try:
            url = f"https://www.preloved.co.uk/classifieds/all/uk/{urllib.parse.quote(q)}"
            resp = requests.get(url, headers=headers, timeout=10)
            if resp.status_code != 200:
                continue

            soup = BeautifulSoup(resp.text, "html.parser")
            items = soup.find_all(["li", "div"], class_=re.compile(r"search-result|listing"))

            for item in items[:15]:
                try:
                    title_el = item.find(["h2", "h3", "span"], class_=re.compile(r"title|header"))
                    if not title_el:
                        continue
                    title = title_el.get_text(strip=True)
                    if not any(k in title.lower() for k in DOMESTIC_KEYWORDS):
                        continue

                    loc_el = item.find(["span", "div"], class_=re.compile(r"location|town"))
                    loc = loc_el.get_text(strip=True) if loc_el else "United Kingdom"

                    ref = f"PRE-{abs(hash(title + loc)) % 1000000}"
                    score = _score_domestic_job(title)
                    price = 39 if score == "large" else 25

                    found_leads.append({
                        "reference": ref,
                        "council_source": f"Preloved Classified ({loc})",
                        "address": f"{loc}, UK",
                        "summary": f"🏡 Preloved Classified Request: {title}",
                        "lead_score": score,
                        "lead_price": price,
                        "lead_source_type": "domestic_classified",
                        "discovered_at": datetime.datetime.now(datetime.timezone.utc).isoformat()
                    })
                except Exception:
                    continue
        except Exception:
            continue

    return found_leads


# ── 4. UK Local Newspaper Public Notices & Trade Classifieds Network ──────────

def scrape_local_newspaper_notices() -> List[Dict[str, Any]]:
    """
    Scrapes UK regional press public notice feeds and local paper community classifieds.
    Captures tree surgery tenders, hazardous roadside notices, and private domestic adverts.
    """
    found_leads = []
    headers = {"User-Agent": "TreeKey-Regional-Paper-Monitor/2.0 (UK Editorial Research)"}

    for paper_name, domain, region, outcode in UK_LOCAL_PAPERS:
        try:
            # Query the search feed of the regional news group
            search_url = f"{domain}/search/?q=tree+felling"
            resp = requests.get(search_url, headers=headers, timeout=8)
            if resp.status_code != 200:
                continue

            soup = BeautifulSoup(resp.text, "html.parser")
            articles = soup.find_all(["article", "div", "li"], class_=re.compile(r"article|teaser|story|card"))

            for art in articles[:8]:
                try:
                    head_el = art.find(["h2", "h3", "a", "strong"])
                    if not head_el:
                        continue
                    headline = head_el.get_text(strip=True)

                    if len(headline) < 12 or not any(k in headline.lower() for k in ["tree", "fell", "felling", "branch", "conifer", "tpo", "conservation", "woodland"]):
                        continue

                    snippet_el = art.find(["p", "div"], class_=re.compile(r"summary|desc|text"))
                    snippet = snippet_el.get_text(strip=True) if snippet_el else headline

                    ref = f"NEWS-{abs(hash(headline + region)) % 1000000}"
                    score = _score_domestic_job(headline + " " + snippet)
                    price = 49 if score == "large" else 29

                    found_leads.append({
                        "reference": ref,
                        "council_source": f"{paper_name} ({region})",
                        "address": f"{region} ({outcode}), UK",
                        "summary": f"📰 Local Press Notice: {headline}. Details: {snippet[:260]}",
                        "lead_score": score,
                        "lead_price": price,
                        "lead_source_type": "domestic_classified",
                        "discovered_at": datetime.datetime.now(datetime.timezone.utc).isoformat()
                    })
                except Exception:
                    continue
        except Exception:
            continue

    return found_leads


# ── 5. Reddit UK Homeowner Gardening & Trade Board Harvester ──────────────────

def scrape_reddit_domestic_leads() -> List[Dict[str, Any]]:
    found_leads = []
    subreddits = ["GardeningUK", "DIYUK", "HousingUK", "UKPersonalFinance"]
    headers = {"User-Agent": "TreeKey-Arbor-Monitor/2.0 (UK Public Forestry Research)"}

    search_terms = [
        "tree surgeon cost",
        "remove tree",
        "fell tree",
        "conifer hedge reduction",
        "stump grinding"
    ]

    for sub in subreddits:
        for q in search_terms[:2]:
            try:
                url = f"https://www.reddit.com/r/{sub}/search.json?q={urllib.parse.quote(q)}&sort=new&restrict_sr=on&limit=15"
                resp = requests.get(url, headers=headers, timeout=8)
                if resp.status_code != 200:
                    continue

                data = resp.json()
                children = data.get("data", {}).get("children", [])

                for post in children:
                    p = post.get("data", {})
                    title = p.get("title", "")
                    body = p.get("selftext", "")
                    permalink = p.get("permalink", "")
                    
                    full_text = f"{title} {body}".lower()
                    if not any(k in full_text for k in DOMESTIC_KEYWORDS):
                        continue

                    city_match = "United Kingdom"
                    for city, outcode in UK_MAJOR_CITIES:
                        if city.lower() in full_text or outcode.lower() in full_text:
                            city_match = f"{city} ({outcode})"
                            break

                    ref = f"RED-{p.get('id', str(abs(hash(title)) % 1000000))}"
                    score = _score_domestic_job(full_text)
                    price = 49 if score == "large" else 25

                    found_leads.append({
                        "reference": ref,
                        "council_source": f"Reddit UK Homeowner (r/{sub})",
                        "address": f"{city_match}, UK",
                        "summary": f"🏡 Homeowner Request: {title}. Context: {body[:240]}... Link: https://reddit.com{permalink}",
                        "lead_score": score,
                        "lead_price": price,
                        "lead_source_type": "domestic_classified",
                        "discovered_at": datetime.datetime.now(datetime.timezone.utc).isoformat()
                    })
            except Exception:
                continue

    return found_leads


# ── 6. FixMyStreet UK Dangerous & Fallen Tree Hazard Scraper ───────────────────

def scrape_fixmystreet_tree_hazards() -> List[Dict[str, Any]]:
    found_leads = []
    headers = {"User-Agent": "TreeKey-Arbor-Radar/2.0 (UK Green Waste & Hazard Prevention)"}

    for city_name, outcode in UK_MAJOR_CITIES[:25]:
        try:
            url = f"https://www.fixmystreet.com/reports/{urllib.parse.quote(city_name)}?service=Trees"
            resp = requests.get(url, headers=headers, timeout=10)
            if resp.status_code != 200:
                continue

            soup = BeautifulSoup(resp.text, "html.parser")
            items = soup.find_all(["li", "div"], class_=re.compile(r"item|report|update"))

            for item in items[:15]:
                try:
                    title_elem = item.find(["h2", "h3", "a", "strong"])
                    if not title_elem:
                        continue
                    title = title_elem.get_text(strip=True)
                    if len(title) < 10 or not any(k in title.lower() for k in ["tree", "branch", "conifer", "fell", "fallen", "hedge", "root", "decay"]):
                        continue

                    snippet_elem = item.find(["p", "span"], class_=re.compile(r"desc|text|detail"))
                    snippet = snippet_elem.get_text(strip=True) if snippet_elem else title

                    ref = f"FMS-{abs(hash(title + city_name)) % 1000000}"
                    score = _score_domestic_job(title + " " + snippet)
                    price = 49 if score == "large" else 25

                    found_leads.append({
                        "reference": ref,
                        "council_source": f"Citizen Hazard Report ({city_name})",
                        "address": f"{city_name} ({outcode}), UK",
                        "summary": f"⚠️ Resident Tree Hazard / Work Needed: {title}. Note: {snippet[:260]}",
                        "lead_score": score,
                        "lead_price": price,
                        "lead_source_type": "domestic_classified",
                        "discovered_at": datetime.datetime.now(datetime.timezone.utc).isoformat()
                    })
                except Exception:
                    continue
        except Exception:
            continue

    return found_leads


# ── Master Ingestion & 1-to-1 Seniority Router ────────────────────────────────

def ingest_and_route_domestic_leads() -> int:
    """
    Master runner: Scrapes all domestic classified sources, deduplicates in Postgres,
    and dispatches newly intercepted leads exclusively 1-to-1 to senior contractors.
    """
    all_leads = []
    all_leads.extend(scrape_gumtree_domestic_jobs())
    all_leads.extend(scrape_freeads_domestic_jobs())
    all_leads.extend(scrape_preloved_domestic_jobs())
    all_leads.extend(scrape_local_newspaper_notices())
    all_leads.extend(scrape_reddit_domestic_leads())
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
                except Exception:
                    continue
            conn.commit()
        finally:
            cur.close()
            conn.close()

        logger.info(f"[Domestic Engine] Ingested {inserted_count} new domestic classified leads.")

        # Trigger Seniority Routing for newly intercepted domestic leads
        if new_leads_for_routing:
            notifications.route_customer_leads(new_leads_for_routing)

        return inserted_count
    except Exception as e:
        logger.error(f"[Domestic Engine] Ingestion error: {e}")
        return 0

import os
import requests
import re
import logging
import datetime
import urllib.parse
import random
from bs4 import BeautifulSoup
from typing import List, Dict, Any, Optional
import database
import notifications
import scanners

logger = logging.getLogger("vector-data-labs")

SCRAPER_API_KEY = os.getenv("SCRAPER_API_KEY", "").strip()
SCRAPINGBEE_API_KEY = os.getenv("SCRAPINGBEE_API_KEY", "").strip()
ZENROWS_API_KEY = os.getenv("ZENROWS_API_KEY", "").strip()
PROXY_URL = os.getenv("PROXY_URL", "").strip()

DOMESTIC_KEYWORDS = [
    "tree", "trees", "felling", "fell", "hedge", "hedges", "stump",
    "pruning", "prune", "crown", "branch", "branches", "conifer",
    "conifers", "oak", "ash", "sycamore", "pine", "birch", "willow",
    "garden clearance", "tree surgeon", "tree surgery", "chainsaw",
    "deadwood", "dangerous tree", "storm damage", "sectional dismantle",
    "pollard", "pollarding", "woodland", "overhanging", "stump grinding",
    "arborist", "timber", "root", "roots", "leylandii", "eucalyptus",
    "communal grounds", "residents association", "parish council", "estate grounds"
]

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:123.0) Gecko/20100101 Firefox/123.0",
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_3_1 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.2 Mobile/15E148 Safari/604.1"
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
    ("Ipswich", "IP1"), ("Peterborough", "PE1")
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

UK_PARISH_COMMUNITIES = [
    ("Harrogate District Parish Council", "HG1", "North Yorkshire"),
    ("Stratford-upon-Avon Town Council", "CV37", "Warwickshire"),
    ("Winchester City & Parishes", "SO23", "Hampshire"),
    ("St Albans District Communities", "AL1", "Hertfordshire"),
    ("Guildford & Rural Parishes", "GU1", "Surrey"),
    ("Cotswold District Parishes", "GL7", "Gloucestershire"),
    ("Sevenoaks Town & Parishes", "TN13", "Kent"),
    ("South Lakeland Rural Councils", "LA9", "Cumbria"),
    ("New Forest Parishes", "SO43", "Hampshire"),
    ("Chilterns Rural Communities", "HP6", "Buckinghamshire")
]


def fetch_unblocked_html(target_url: str, render_js: bool = False) -> Optional[str]:
    """
    Universal Unblocking Gateway:
    Routes requests through residential proxy providers (ScraperAPI / ScrapingBee / ZenRows)
    to bypass Cloudflare and Akamai bot-protection walls.
    Falls back to rotating stealth headers.
    """
    # 1. ScraperAPI Gateway (Residential UK IP)
    if SCRAPER_API_KEY:
        try:
            params = {
                "api_key": SCRAPER_API_KEY,
                "url": target_url,
                "country_code": "gb",
                "render": "true" if render_js else "false"
            }
            resp = requests.get("https://api.scraperapi.com", params=params, timeout=25)
            if resp.status_code == 200:
                return resp.text
        except Exception as e:
            logger.debug(f"[ScraperAPI] Error: {e}")

    # 2. ScrapingBee Gateway
    if SCRAPINGBEE_API_KEY:
        try:
            params = {
                "api_key": SCRAPINGBEE_API_KEY,
                "url": target_url,
                "country_code": "gb",
                "render_js": "true" if render_js else "false"
            }
            resp = requests.get("https://app.scrapingbee.com/api/v1/", params=params, timeout=25)
            if resp.status_code == 200:
                return resp.text
        except Exception as e:
            logger.debug(f"[ScrapingBee] Error: {e}")

    # 3. ZenRows Gateway
    if ZENROWS_API_KEY:
        try:
            params = {
                "apikey": ZENROWS_API_KEY,
                "url": target_url,
                "premium_proxy": "true",
                "proxy_country": "gb"
            }
            resp = requests.get("https://api.zenrows.com/v1/", params=params, timeout=25)
            if resp.status_code == 200:
                return resp.text
        except Exception as e:
            logger.debug(f"[ZenRows] Error: {e}")

    # 4. Stealth Direct Fallback with Rotating Fingerprint
    headers = {
        "User-Agent": random.choice(USER_AGENTS),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
        "Accept-Language": "en-GB,en-US;q=0.9,en;q=0.8",
        "DNT": "1",
        "Upgrade-Insecure-Requests": "1"
    }
    proxies = {"http": PROXY_URL, "https": PROXY_URL} if PROXY_URL else None

    try:
        resp = requests.get(target_url, headers=headers, proxies=proxies, timeout=12)
        if resp.status_code == 200:
            return resp.text
    except Exception as e:
        logger.debug(f"[Direct Stealth] Fetch failed for {target_url}: {e}")

    return None


def _score_domestic_job(text: str) -> str:
    text_lower = text.lower()
    if any(k in text_lower for k in ["large tree", "huge tree", "dismantle", "sectional", "mewp", "crane", "dangerous", "multiple trees", "site clearance", "woodland", "mature oak", "tall pine", "felling of 3", "felling of 4", "communal grounds", "estate trees"]):
        return "large"
    elif any(k in text_lower for k in ["fell", "felling", "take down", "removal", "crown reduction", "conifer hedge", "stump", "pollard", "branch removal", "crown lift", "parish"]):
        return "medium"
    return "small"


# ── 1. Gumtree UK Domestic Job Board Scraper (Unblocked) ──────────────────────

def scrape_gumtree_domestic_jobs() -> List[Dict[str, Any]]:
    found_leads = []
    search_queries = [
        "tree surgeon needed",
        "tree removal needed",
        "tree felling wanted",
        "hedge cutting needed",
        "stump grinding needed"
    ]

    for q in search_queries[:3]:
        url = f"https://www.gumtree.com/search?search_category=all&q={urllib.parse.quote(q)}"
        html = fetch_unblocked_html(url, render_js=False)
        if not html:
            continue

        try:
            soup = BeautifulSoup(html, "html.parser")
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


# ── 2. Freeads & Preloved UK Domestic Classifieds ────────────────────────────

def scrape_freeads_and_preloved() -> List[Dict[str, Any]]:
    found_leads = []
    queries = ["tree surgeon", "tree removal", "hedge cutting", "stump removal", "tree felling"]

    for q in queries[:2]:
        url = f"https://www.freeads.co.uk/search.aspx?keyword={urllib.parse.quote(q)}&category=services"
        html = fetch_unblocked_html(url, render_js=False)
        if not html:
            continue

        try:
            soup = BeautifulSoup(html, "html.parser")
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
                    found_leads.append({
                        "reference": ref,
                        "council_source": f"Freeads Classified ({loc})",
                        "address": f"{loc}, UK",
                        "summary": f"🏡 Homeowner Request: {title}. Notes: {desc[:260]}",
                        "lead_score": score,
                        "lead_price": 49 if score == "large" else 25,
                        "lead_source_type": "domestic_classified",
                        "discovered_at": datetime.datetime.now(datetime.timezone.utc).isoformat()
                    })
                except Exception:
                    continue
        except Exception:
            continue

    return found_leads


# ── 3. Facebook & Social Neighborhood Group Public Harvester ──────────────────

def scrape_public_social_community_groups() -> List[Dict[str, Any]]:
    found_leads = []
    social_queries = ["recommend a tree surgeon", "looking for tree surgery quotes", "need tree removed from garden"]

    for city, outcode in UK_MAJOR_CITIES[:10]:
        for q in social_queries[:1]:
            search_query = f"{city} {q}"
            url = f"https://html.duckduckgo.com/html/?q={urllib.parse.quote(search_query + ' site:facebook.com OR site:nextdoor.co.uk')}"
            html = fetch_unblocked_html(url, render_js=False)
            if not html:
                continue

            try:
                soup = BeautifulSoup(html, "html.parser")
                results = soup.find_all("div", class_=re.compile(r"result__body|web-result"))

                for r in results[:5]:
                    try:
                        title_el = r.find("a", class_=re.compile(r"result__title|result__url"))
                        snippet_el = r.find(["a", "div"], class_=re.compile(r"result__snippet"))
                        if not title_el or not snippet_el:
                            continue
                        
                        title = title_el.get_text(strip=True)
                        snippet = snippet_el.get_text(strip=True)
                        full_text = f"{title} {snippet}".lower()

                        if not any(k in full_text for k in ["tree", "fell", "prun", "hedge", "stump", "branch", "surgeon"]):
                            continue

                        ref = f"SOC-{abs(hash(title + city)) % 1000000}"
                        score = _score_domestic_job(full_text)

                        found_leads.append({
                            "reference": ref,
                            "council_source": f"Local Facebook / Nextdoor Community ({city})",
                            "address": f"{city} ({outcode}), UK",
                            "summary": f"💬 Resident Social Request: {title}. Context: {snippet[:260]}",
                            "lead_score": score,
                            "lead_price": 49 if score == "large" else 29,
                            "lead_source_type": "domestic_classified",
                            "discovered_at": datetime.datetime.now(datetime.timezone.utc).isoformat()
                        })
                    except Exception:
                        continue
            except Exception:
                continue

    return found_leads


# ── 4. Residents Association & Communal Estate Grounds Tenders ─────────────────

def scrape_residents_associations_and_estates() -> List[Dict[str, Any]]:
    found_leads = []
    queries = ["residents association tree surgery tender", "communal grounds tree maintenance quote"]

    for city, outcode in UK_MAJOR_CITIES[:8]:
        for q in queries[:1]:
            search_query = f"{city} {q}"
            url = f"https://html.duckduckgo.com/html/?q={urllib.parse.quote(search_query)}"
            html = fetch_unblocked_html(url, render_js=False)
            if not html:
                continue

            try:
                soup = BeautifulSoup(html, "html.parser")
                results = soup.find_all("div", class_=re.compile(r"result__body|web-result"))

                for r in results[:4]:
                    try:
                        title_el = r.find("a", class_=re.compile(r"result__title"))
                        snippet_el = r.find(["a", "div"], class_=re.compile(r"result__snippet"))
                        if not title_el or not snippet_el:
                            continue
                        
                        title = title_el.get_text(strip=True)
                        snippet = snippet_el.get_text(strip=True)
                        full_text = f"{title} {snippet}".lower()

                        if not any(k in full_text for k in ["tree", "grounds", "estate", "residents", "tender", "maintenance", "hedge"]):
                            continue

                        ref = f"RMC-{abs(hash(title + city)) % 1000000}"
                        found_leads.append({
                            "reference": ref,
                            "council_source": f"Residents Association / Estate Grounds ({city})",
                            "address": f"{city} ({outcode}), UK",
                            "summary": f"🏢 Residents Association / Estate Tender: {title}. Notes: {snippet[:280]}",
                            "lead_score": "large",
                            "lead_price": 49,
                            "lead_source_type": "domestic_classified",
                            "discovered_at": datetime.datetime.now(datetime.timezone.utc).isoformat()
                        })
                    except Exception:
                        continue
            except Exception:
                continue

    return found_leads


# ── 5. UK Local Newspaper & Regional Press Syndicate Scraper ──────────────────

def scrape_local_newspaper_notices() -> List[Dict[str, Any]]:
    found_leads = []
    for paper_name, domain, region, outcode in UK_LOCAL_PAPERS[:6]:
        search_url = f"{domain}/search/?q=tree+felling"
        html = fetch_unblocked_html(search_url, render_js=False)
        if not html:
            continue

        try:
            soup = BeautifulSoup(html, "html.parser")
            articles = soup.find_all(["article", "div", "li"], class_=re.compile(r"article|teaser|story|card"))

            for art in articles[:6]:
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

                    found_leads.append({
                        "reference": ref,
                        "council_source": f"{paper_name} ({region})",
                        "address": f"{region} ({outcode}), UK",
                        "summary": f"📰 Local Press Notice: {headline}. Details: {snippet[:260]}",
                        "lead_score": score,
                        "lead_price": 49 if score == "large" else 29,
                        "lead_source_type": "domestic_classified",
                        "discovered_at": datetime.datetime.now(datetime.timezone.utc).isoformat()
                    })
                except Exception:
                    continue
        except Exception:
            continue

    return found_leads


# ── 6. Reddit UK Homeowner & Gardening Community Harvester ────────────────────

def scrape_reddit_domestic_leads() -> List[Dict[str, Any]]:
    found_leads = []
    subreddits = ["GardeningUK", "DIYUK", "HousingUK"]
    search_terms = ["tree surgeon cost", "remove tree", "fell tree"]

    for sub in subreddits:
        for q in search_terms[:1]:
            url = f"https://www.reddit.com/r/{sub}/search.json?q={urllib.parse.quote(q)}&sort=new&restrict_sr=on&limit=10"
            html = fetch_unblocked_html(url, render_js=False)
            if not html:
                continue

            try:
                import json
                data = json.loads(html)
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

                    found_leads.append({
                        "reference": ref,
                        "council_source": f"Reddit UK Homeowner (r/{sub})",
                        "address": f"{city_match}, UK",
                        "summary": f"🏡 Homeowner Request: {title}. Context: {body[:240]}... Link: https://reddit.com{permalink}",
                        "lead_score": score,
                        "lead_price": 49 if score == "large" else 25,
                        "lead_source_type": "domestic_classified",
                        "discovered_at": datetime.datetime.now(datetime.timezone.utc).isoformat()
                    })
            except Exception:
                continue

    return found_leads


# ── 7. FixMyStreet UK Citizen Tree Hazard Scraper ─────────────────────────────

def scrape_fixmystreet_tree_hazards() -> List[Dict[str, Any]]:
    found_leads = []
    for city_name, outcode in UK_MAJOR_CITIES:
        url = f"https://www.fixmystreet.com/reports/{urllib.parse.quote(city_name)}?service=Trees"
        html = fetch_unblocked_html(url, render_js=False)
        if not html:
            continue

        try:
            soup = BeautifulSoup(html, "html.parser")
            items = soup.find_all(["li", "div"], class_=re.compile(r"item|report|update"))

            for item in items[:10]:
                try:
                    title_elem = item.find(["h2", "h3", "a", "strong"])
                    if not title_elem:
                        continue
                    title = title_elem.get_text(strip=True)
                    
                    snippet_elem = item.find(["p", "span"], class_=re.compile(r"desc|text|detail"))
                    snippet = snippet_elem.get_text(strip=True) if snippet_elem else title
                    full_raw = f"{title} {snippet}"
                    
                    # Strict 7-day age filter for domestic leads
                    dt_str = item.get("data-lastupdate")
                    if dt_str:
                        try:
                            report_dt = datetime.datetime.fromisoformat(dt_str)
                            if report_dt.tzinfo is None:
                                report_dt = report_dt.replace(tzinfo=datetime.timezone.utc)
                            if (datetime.datetime.now(datetime.timezone.utc) - report_dt).days > 7:
                                continue
                        except Exception:
                            pass
                    
                    # Reject historical archive years if date parsing failed
                    if any(y in full_raw for y in ["2008", "2009", "2010", "2011", "2012", "2013", "2014", "2015", "2016", "2017", "2018", "2019", "2020", "2021", "2022", "2023", "2024", "2025"]):
                        continue

                    # Require active tree surgery action verbs
                    action_verbs = ["fell", "felling", "cut", "cutting", "prune", "pruning", "overgrown", "dangerous", "fallen", "hazard", "blocking", "dismantle", "stump", "reduce", "reduction", "trim", "trimming"]
                    if not any(v in full_raw.lower() for v in action_verbs):
                        continue

                    clean_title = re.sub(r'\d{1,2}:\d{2}.*', '', title).strip()
                    clean_snippet = re.sub(r'\(sent to both\).*', '', snippet).replace('\n', ' ').strip()
                    clean_snippet = re.sub(r'\s+', ' ', clean_snippet)

                    ref = f"FMS-{abs(hash(clean_title + city_name)) % 1000000}"
                    score = _score_domestic_job(clean_title + " " + clean_snippet)

                    found_leads.append({
                        "reference": ref,
                        "council_source": f"Citizen Hazard Report ({city_name})",
                        "address": f"{city_name} ({outcode}), UK",
                        "summary": f"⚠️ Resident Tree Hazard / Work Needed: {clean_title}. Note: {clean_snippet[:220]}",
                        "lead_score": score,
                        "lead_price": 49 if score == "large" else 25,
                        "lead_source_type": "domestic_classified",
                        "discovered_at": datetime.datetime.now(datetime.timezone.utc).isoformat()
                    })
                except Exception:
                    continue
        except Exception:
            continue

    return found_leads


# ── 8. Parish & Town Council Open Grounds Tenders ─────────────────────────────

def scrape_parish_council_grounds_notices() -> List[Dict[str, Any]]:
    found_leads = []
    for parish_name, outcode, county in UK_PARISH_COMMUNITIES[:6]:
        query = f"{parish_name} tree surgery work maintenance tender"
        url = f"https://html.duckduckgo.com/html/?q={urllib.parse.quote(query)}"
        html = fetch_unblocked_html(url, render_js=False)
        if not html:
            continue

        try:
            soup = BeautifulSoup(html, "html.parser")
            results = soup.find_all("div", class_=re.compile(r"result__body|web-result"))

            for r in results[:3]:
                try:
                    title_el = r.find("a", class_=re.compile(r"result__title"))
                    snippet_el = r.find(["a", "div"], class_=re.compile(r"result__snippet"))
                    if not title_el or not snippet_el:
                        continue
                    
                    title = title_el.get_text(strip=True)
                    snippet = snippet_el.get_text(strip=True)
                    full_text = f"{title} {snippet}".lower()

                    if not any(k in full_text for k in ["tree", "felling", "pruning", "grounds", "parish", "survey", "works"]):
                        continue

                    ref = f"PAR-{abs(hash(title + parish_name)) % 1000000}"
                    found_leads.append({
                        "reference": ref,
                        "council_source": f"Parish / Town Council Notice ({parish_name})",
                        "address": f"{county} ({outcode}), UK",
                        "summary": f"🏛️ Parish & Town Council Grounds Work: {title}. Details: {snippet[:280]}",
                        "lead_score": "large",
                        "lead_price": 49,
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
    Master runner: Scrapes all 8 domestic & social channels via universal unblocking gateway,
    deduplicates in Postgres, and dispatches newly intercepted leads exclusively 1-to-1 to senior contractors.
    """
    all_leads = []
    all_leads.extend(scrape_gumtree_domestic_jobs())
    all_leads.extend(scrape_freeads_and_preloved())
    all_leads.extend(scrape_public_social_community_groups())
    all_leads.extend(scrape_residents_associations_and_estates())
    all_leads.extend(scrape_local_newspaper_notices())
    all_leads.extend(scrape_reddit_domestic_leads())
    all_leads.extend(scrape_fixmystreet_tree_hazards())
    all_leads.extend(scrape_parish_council_grounds_notices())

    inserted_count = 0
    new_leads_for_routing = []

    if not database.SURL:
        return 0

    try:
        conn = database.get_db_conn()
        cur = conn.cursor()
        try:
            # Clean historical archive entries strictly for domestic_classified (leaving council_planning intact)
            cur.execute("""
                DELETE FROM leads 
                WHERE lead_source_type = 'domestic_classified' 
                  AND (summary LIKE '%2009%' OR summary LIKE '%2008%' OR summary LIKE '%(sent to both)%');
            """)

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

        logger.info(f"[Domestic Engine] Ingested {inserted_count} new domestic classified & community leads.")

        # Trigger Seniority Routing for newly intercepted domestic leads
        if new_leads_for_routing:
            notifications.dispatch_lead_alerts("Nationwide Domestic", new_leads_for_routing)

        return inserted_count
    except Exception as e:
        logger.error(f"[Domestic Engine] Ingestion error: {e}")
        return 0

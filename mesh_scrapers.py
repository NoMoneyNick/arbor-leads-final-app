import logging
import requests
import datetime
import urllib3
import re
import time
from typing import List, Dict

try:
    from bs4 import BeautifulSoup
except ImportError:
    BeautifulSoup = None

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
logger = logging.getLogger("vector-data-labs")

# The Council Registry maps city/region names to their direct Idox Public Access URLs.
# This cuts out the 3rd party API middleman for these regions.
COUNCIL_REGISTRY = {
    # England & Regional Metros
    "CORNWALL": "https://planning.cornwall.gov.uk/online-applications",
    "NOTTINGHAM": "https://publicaccess.nottinghamcity.gov.uk/online-applications",
    "MANCHESTER": "https://pa.manchester.gov.uk/online-applications",
    "EDINBURGH": "https://citydev-portal.edinburgh.gov.uk/idoxpa-web",
    "GLASGOW": "https://publicaccess.glasgow.gov.uk/online-applications",
    "FIFE": "https://planning.fife.gov.uk/online",
    "BRISTOL": "https://planningonline.bristol.gov.uk/online-applications",
    "LEEDS": "https://publicaccess.leeds.gov.uk/online-applications",
    # London Boroughs (Direct Public Access Mesh)
    "WESTMINSTER": "https://idoxpa.westminster.gov.uk/online-applications",
    "BROMLEY": "https://searchapplications.bromley.gov.uk/online-applications",
    "CROYDON": "https://publicaccess3.croydon.gov.uk/online-applications",
    "SOUTHWARK": "https://planning.southwark.gov.uk/online-applications",
    "ISLINGTON": "https://planning.islington.gov.uk/online-applications",
    "LAMBETH": "https://planning.lambeth.gov.uk/online-applications",
    "BARNET": "https://publicaccess.barnet.gov.uk/online-applications",
    "BRENT": "https://pa.brent.gov.uk/online-applications",
    "EALING": "https://pam.ealing.gov.uk/online-applications",
    "HOUNSLOW": "https://planning.hounslow.gov.uk/online-applications",
    "KINGSTON": "https://publicaccess.kingston.gov.uk/online-applications",
    "MERTON": "https://planning.merton.gov.uk/online-applications",
    "GREENWICH": "https://planning.royalgreenwich.gov.uk/online-applications",
    "HARINGEY": "https://planning.haringey.gov.uk/online-applications",
    "REDBRIDGE": "https://planning.redbridge.gov.uk/online-applications",
    "BEXLEY": "https://pa.bexley.gov.uk/online-applications",
    "HAVERING": "https://development.havering.gov.uk/online-applications",
    "SUTTON": "https://planning.sutton.gov.uk/online-applications",
    # Home Counties / Green Belt
    "SURREY HEATH": "https://publicaccess.surreyheath.gov.uk/online-applications",
    "GUILDFORD": "https://publicaccess.guildford.gov.uk/online-applications",
    "SEVENOAKS": "https://pa.sevenoaks.gov.uk/online-applications",
    "DARTFORD": "https://publicaccess.dartford.gov.uk/online-applications",
    "MAIDSTONE": "https://pa.midkent.gov.uk/online-applications"
}

def is_tree_related(description: str) -> bool:
    """Checks if a planning description is relevant to tree surgeons."""
    desc = description.lower()
    keywords = ["tree", "tpo", "crown", "fell", "prune", "branch", "oak", "ash", "sycamore", "coppice", "pollard"]
    return any(kw in desc for kw in keywords)

class IdoxScraper:
    def __init__(self, base_url: str):
        self.base_url = base_url.rstrip("/")
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/115.0.0.0 Safari/537.36",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-GB,en;q=0.5"
        })

    def get_csrf_token(self, html_text: str) -> str:
        if not BeautifulSoup:
            return ""
        soup = BeautifulSoup(html_text, 'html.parser')
        csrf_input = soup.find('input', {'name': '_csrf'})
        if csrf_input and csrf_input.get('value'):
            return csrf_input['value']
        return ""

    def search_tree_applications(self, days_back: int = 7) -> List[Dict]:
        if not BeautifulSoup:
            logger.error("[MESH] BeautifulSoup not installed. Cannot run Idox Scraper.")
            return []

        leads = []
        try:
            # Step 1: Establish session and get CSRF token
            adv_search_url = f"{self.base_url}/search.do?action=advanced"
            res = self.session.get(adv_search_url, verify=False, timeout=15)
            if res.status_code != 200:
                logger.warning(f"[MESH] Failed to connect to {self.base_url}. Status: {res.status_code}")
                return leads

            csrf_token = self.get_csrf_token(res.text)

            # Step 2: Prepare POST payload for Advanced Search
            end_date = datetime.datetime.now()
            start_date = end_date - datetime.timedelta(days=days_back)
            
            payload = {
                "searchCriteria.description": "tree",
                "date(applicationReceivedStart)": start_date.strftime("%d/%m/%Y"),
                "date(applicationReceivedEnd)": end_date.strftime("%d/%m/%Y"),
                "searchType": "Application"
            }
            if csrf_token:
                payload["_csrf"] = csrf_token

            # Step 3: Execute Search
            search_url = f"{self.base_url}/advancedSearchResults.do?action=searchCriteria"
            res_post = self.session.post(search_url, data=payload, verify=False, timeout=20)
            
            # Step 4: Parse Results
            soup = BeautifulSoup(res_post.text, 'html.parser')
            
            # Idox results are typically in a <ul id="searchresults">
            results_list = soup.find('ul', id='searchresults')
            if not results_list:
                # Some councils redirect directly to a single application if only 1 match is found
                if "applicationDetails.do" in res_post.url:
                    ref_tag = soup.find('th', string=re.compile("Reference", re.I))
                    addr_tag = soup.find('th', string=re.compile("Address", re.I))
                    desc_tag = soup.find('th', string=re.compile("Proposal", re.I))
                    
                    if ref_tag and desc_tag:
                        ref = ref_tag.find_next_sibling('td').text.strip()
                        addr = addr_tag.find_next_sibling('td').text.strip() if addr_tag else "Unknown Address"
                        desc = desc_tag.find_next_sibling('td').text.strip()
                        if is_tree_related(desc):
                            leads.append({"reference": ref, "address": addr, "description": desc})
                return leads

            # Parse multiple results
            for li in results_list.find_all('li', class_='searchresult'):
                a_tag = li.find('a')
                if not a_tag:
                    continue
                
                title_text = a_tag.text.strip()
                parts = title_text.split('|', 1)
                if len(parts) == 2:
                    ref = parts[0].strip()
                    desc = parts[1].strip()
                else:
                    ref = f"MESH-{int(time.time())}"
                    desc = title_text
                
                address_p = li.find('p', class_='address')
                addr = address_p.text.strip() if address_p else "Unknown Address"
                
                if is_tree_related(desc):
                    leads.append({
                        "reference": ref,
                        "address": addr,
                        "description": desc
                    })

            logger.info(f"[MESH] Successfully scraped {len(leads)} tree leads from {self.base_url}")
            return leads

        except requests.exceptions.Timeout:
            logger.warning(f"[MESH] Timeout accessing {self.base_url}")
        except Exception as e:
            logger.error(f"[MESH] Idox scraping error on {self.base_url}: {e}")
            
        return leads

def scrape_mesh_council(city_name: str) -> List[Dict]:
    """
    Entry point for the MESH orchestrator. 
    Returns a list of leads, or [] if no leads/failure.
    """
    city_upper = city_name.strip().upper()
    
    # Handle known IDOX implementations
    base_url = COUNCIL_REGISTRY.get(city_upper)
    if base_url and "online-applications" in base_url.lower():
        logger.info(f"[MESH] Routing {city_upper} to free Idox Engine...")
        scraper = IdoxScraper(base_url)
        return scraper.search_tree_applications(days_back=7)
        
    return []

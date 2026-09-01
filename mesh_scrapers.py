import logging
import requests
import datetime
import urllib3
import re
import time
from typing import List, Dict, Optional, Tuple

import net_utils

try:
    from bs4 import BeautifulSoup
except ImportError:
    BeautifulSoup = None

# Aug 31 2026: council portals routinely fill an empty Agent/Applicant field
# with placeholder text ("Not Available", "N/A", "-") instead of leaving it
# blank -- confirmed live: 8 of 186 "has agent" leads in one export had
# agent_company literally equal to "Not Available", wrongly flagging a
# genuinely open lead as taken. Shared by _fetch_applicant_and_agent below.
_PLACEHOLDER_FIELD_VALUES = {
    "see source", "n/a", "none", "", "not available", "not known", "unknown",
    "n a", "not applicable", "not given", "not provided", "tbc", "to be confirmed",
    "-", "--",
}


def _looks_like_real_value(value: Optional[str]) -> bool:
    """False for empty/placeholder text (see _PLACEHOLDER_FIELD_VALUES), True otherwise."""
    if not value or not isinstance(value, str):
        return False
    return value.strip().lower() not in _PLACEHOLDER_FIELD_VALUES


# Aug 31 2026: Nick's point -- "an agent" on a planning application isn't
# always a tree surgeon. Architects, planning consultants, block management
# companies, and even the council itself all get filed as the "Agent" too
# (they handled the paperwork), and in those cases the actual tree work may
# still be wide open even though has_agent is technically True. This is a
# best-effort keyword classifier of the agent's name/company text -- company
# naming isn't standardised, so it can't be perfect -- but it's strictly
# better than treating every non-empty agent field as "job taken".
#
# Deliberately asymmetric: a wrong "still open" call risks selling a lead
# that's actually taken (a refund + trust problem), while a wrong "already
# taken" call only means an ambiguous lead is held back from sale (no harm
# done, just conservative). So ambiguous or unmatched text is treated as
# "can't tell" (None), and callers should keep excluding it from sale --
# only a CLEAR non-tree keyword match (with no tree keyword present) flips
# a lead back to sellable.
_TREE_SURGEON_KEYWORDS = (
    "tree", "arb", "arboricultur", "forestry", "woodland", "hedge", "treecare",
    "treescape", "surgeon", "grounds maintenance", "grounds care",
)
_NON_TREE_AGENT_KEYWORDS = (
    "architect", "planning consult", "town planning", "planning ltd",
    "surveyor", "block management", "management company", "development",
    "developments", "properties", "estate agent", "estates", "solicitor",
    "legal", "council", "borough", "chartered", "design", "associates",
)


def classify_agent_as_tree_surgeon(agent_name: Optional[str], agent_company: Optional[str]) -> Optional[bool]:
    """
    Best-effort guess at whether the agent on record is actually a tree
    surgeon (job genuinely taken) or a different kind of agent entirely
    (in which case the tree work itself may still be open). Returns:
      True  -- looks like a tree/arb company.
      False -- looks like a clearly non-tree agent (architect, planning
               consultant, block management, the council itself, etc.).
      None  -- can't tell (bare personal name, or text matching neither/both
               keyword lists) -- treat the same as "unknown", not "open".
    """
    text = f"{agent_name or ''} {agent_company or ''}".strip().lower()
    if not text:
        return None
    has_tree_kw = any(kw in text for kw in _TREE_SURGEON_KEYWORDS)
    has_non_tree_kw = any(kw in text for kw in _NON_TREE_AGENT_KEYWORDS)
    if has_tree_kw and not has_non_tree_kw:
        return True
    if has_non_tree_kw and not has_tree_kw:
        return False
    return None

# Reuse the same false-positive-safe compound-phrase list scanners.py already
# built for the UK Planning API / GLA feeds (Aug 28 2026). The old local
# keyword list here was single bare words ("crown", "branch", "oak", "ash",
# "fell") which false-positive on street names, bank branches, and "fell
# down" style phrasing -- TREE_GOLD's compound phrases ("crown reduction",
# "oak tree", "fell 1") were specifically built to avoid that.
try:
    from scanners import TREE_GOLD
except ImportError:
    # Defensive fallback if this module is ever imported standalone
    # without scanners.py present (e.g. isolated unit tests).
    TREE_GOLD = ["tree surgery", "tree work", "tpo", "tree preservation order",
                 "felling", "pollard", "crown reduction", "hedge trimming"]

# Idox's basic advanced-search "description" field only takes one plain-text
# term (no boolean OR), so a single search for "tree" misses genuine tree-work
# applications worded around a species/operation without the literal word
# "tree" (e.g. "TPO: pollard protected oak", "Crown reduction of specimen").
# Mirrors the multi-pass SIC-code insight from bulk_contractor_extractor.py's
# item-5 expansion: run several narrow, high-signal server-side searches per
# council instead of one, then dedupe and apply the same TREE_GOLD filter.
# NOT yet load-tested against live council portals at this term count --
# run once and watch for 429s/bans before trusting it at full national scale,
# same caveat as the SIC-code pass.
IDOX_SEARCH_TERMS = ["tree", "tpo", "hedge"]

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
logger = logging.getLogger("vector-data-labs")

# The Council Registry maps city/region names to their direct Idox Public Access URLs.
# This cuts out the 3rd party API middleman for these regions.
COUNCIL_REGISTRY = {
    # England Metros & Regions
    "CORNWALL": "https://planning.cornwall.gov.uk/online-applications",
    "NOTTINGHAM": "https://publicaccess.nottinghamcity.gov.uk/online-applications",
    "MANCHESTER": "https://pa.manchester.gov.uk/online-applications",
    "EDINBURGH": "https://citydev-portal.edinburgh.gov.uk/idoxpa-web",
    "GLASGOW": "https://publicaccess.glasgow.gov.uk/online-applications",
    "FIFE": "https://planning.fife.gov.uk/online",
    "BRISTOL": "https://planningonline.bristol.gov.uk/online-applications",
    "LEEDS": "https://publicaccess.leeds.gov.uk/online-applications",
    "SHEFFIELD": "https://planningapps.sheffield.gov.uk/online-applications",

    # Removed Aug 30 2026 -- same "confirmed dead against this exact URL,
    # not a transient blip" situation as the London-borough block further
    # down, verified against today's live scan logs plus an independent web
    # check before removing:
    #   "NEWCASTLE": DNS failed in logs (3x NameResolutionError). Newcastle's
    #       own site confirms the search moved to a new host entirely --
    #       portal.newcastle.gov.uk/planning/index.html, described as a
    #       "Public Access" system (so likely still Idox-based software, just
    #       a new hostname). Tried the obvious guess of swapping in
    #       portal.newcastle.gov.uk/online-applications directly, but it
    #       returned an HTTP 406 rather than confirming a working advanced
    #       search page -- not safe to assume that's the right path without
    #       manually confirming the real URL structure first.
    #   "CAMBRIDGE": Connection refused in logs (3x, not a DNS failure --
    #       the domain still resolves but nothing is listening on port 443
    #       anymore, consistent with an old server being decommissioned
    #       after a migration). Greater Cambridge Shared Planning announced
    #       a full redesign of their planning search on Dec 10 2025, now
    #       living at greatercambridgeplanning.org with postcode-based
    #       search -- a different, non-Idox interface, so this needs a
    #       dedicated scraper, not a URL swap.
    "OXFORD": "https://public.oxford.gov.uk/online-applications",
    "BATH & NORTH EAST SOMERSET": "https://www.bathnes.gov.uk/developmentmanagement/Detail.aspx",
    "YORK": "https://planningaccess.york.gov.uk/online-applications",
    "EXETER": "https://publicaccess.exeter.gov.uk/online-applications",
    "PLYMOUTH": "https://planning.plymouth.gov.uk/online-applications",
    "NORWICH": "https://planning.norwich.gov.uk/online-applications",
    "SOUTHAMPTON": "https://planningpublicaccess.southampton.gov.uk/online-applications",
    "PORTSMOUTH": "https://publicaccess.portsmouth.gov.uk/online-applications",
    "BRIGHTON": "https://planningapps.brighton-hove.gov.uk/online-applications",
    "COVENTRY": "https://planapp.coventry.gov.uk/online-applications",
    "DERBY": "https://eplanning.derby.gov.uk/active-applications",
    "LEICESTER": "https://planning.leicester.gov.uk/online-applications",
    "CHESHIRE EAST": "https://planning.cheshireeast.gov.uk/online-applications",
    "CHESHIRE WEST": "https://pa.cheshirewestandchester.gov.uk/online-applications",
    "NORTH NORTHAMPTONSHIRE": "https://publicaccess.northnorthants.gov.uk/online-applications",
    "WEST NORTHAMPTONSHIRE": "https://wnc.planning-register.co.uk",
    "MILTON KEYNES": "https://publicaccess.milton-keynes.gov.uk/online-applications",
    "WARWICK": "https://planningdocuments.warwickdc.gov.uk/online-applications",
    "STRATFORD-ON-AVON": "https://apps.stratford.gov.uk/eplanning",
    "CHELTENHAM": "https://publicaccess.cheltenham.gov.uk/online-applications",
    "GLOUCESTER": "https://planning.gloucester.gov.uk/online-applications",
    "WILTSHIRE": "https://development.wiltshire.gov.uk/pr/s",
    "DORSET": "https://planning.dorsetcouncil.gov.uk",

    # London Boroughs Mesh (Direct Public Access)
    "WESTMINSTER": "https://idoxpa.westminster.gov.uk/online-applications",
    "BROMLEY": "https://searchapplications.bromley.gov.uk/online-applications",
    "CROYDON": "https://publicaccess3.croydon.gov.uk/online-applications",
    "SOUTHWARK": "https://planning.southwark.gov.uk/online-applications",
    "LAMBETH": "https://planning.lambeth.gov.uk/online-applications",
    "BARNET": "https://publicaccess.barnet.gov.uk/online-applications",
    "BRENT": "https://pa.brent.gov.uk/online-applications",
    "EALING": "https://pam.ealing.gov.uk/online-applications",
    "KINGSTON": "https://publicaccess.kingston.gov.uk/online-applications",
    "GREENWICH": "https://planning.royalgreenwich.gov.uk/online-applications",
    "BEXLEY": "https://pa.bexley.gov.uk/online-applications",
    "RICHMOND": "https://www2.richmond.gov.uk/lbrplanning/Planning_Search.aspx",

    # Removed Aug 30 2026 -- confirmed dead against this exact "/online-applications"
    # Idox path, not a transient network blip. Live scan logs from today showed each
    # of these failing every single run (either DNS not resolving at all, or a flat
    # 404), and a web check today confirms these councils have migrated off this
    # Idox URL to a *different* portal platform entirely -- scrape_mesh_council()
    # would need a brand-new scraper for each (different HTML/API, not just a new
    # hostname), which IdoxScraper cannot handle. Leaving these registered was pure
    # waste: every scan burned 3 retries + a long timeout on each, on every run,
    # for zero leads, ever. Re-add only once a scraper matching the NEW platform
    # exists for each:
    #   "HOUNSLOW": DNS failed in logs; hounslow.gov.uk's own site now points
    #       planning search at https://planning.hounslow.gov.uk/ (root, no
    #       /online-applications path) -- may still be Idox under a different path,
    #       worth a manual check before writing off entirely.
    #   "MERTON": DNS failed in logs; council's current search lives at
    #       merton.gov.uk/planning-and-buildings/planning/find, not this subdomain.
    #   "SUTTON": DNS failed in logs; London Borough of Sutton's current portal
    #       needs re-identifying (search results only surfaced an unrelated
    #       Cambridgeshire parish council of the same name).
    #   "HARINGEY": DNS failed in logs; Haringey has moved through more than one
    #       replacement system (a legacy servlet-based search, and a newer
    #       Salesforce-style "Public Register" at publicregister.haringey.gov.uk) --
    #       neither is Idox, so this needs a dedicated scraper, not a URL swap.
    #   "REDBRIDGE": 404 in logs; Redbridge now runs a "Citizen Portal" planning
    #       system, a different platform from Idox's online-applications.
    #   "HAVERING": 404 in logs; Havering's current search lives at
    #       msp.havering.gov.uk/planning/search-applications, a different platform.
    #   "ISLINGTON": DNS failed in logs (added to this list after a second scan run
    #       caught it -- the first pass through this log only sampled part of a run
    #       and missed it). islington.gov.uk's own site now points planning search
    #       at islington.gov.uk/planningsearch, off the old Idox subdomain entirely.
    #   "ST ALBANS": clean 404 x3 in the newest logs (this exact
    #       /rpa/online-applications path). Not yet re-identified where St Albans'
    #       planning search actually lives now -- needs a manual check before any
    #       replacement scraper can be written.

    "WANDSWORTH": "https://planning1.wandsworth.gov.uk/Northgate/PlanningExplorer/GeneralSearch.aspx",
    "HAMMERSMITH & FULHAM": "https://public-access.lbhf.gov.uk/online-applications",
    "KENSINGTON & CHELSEA": "https://www.rbkc.gov.uk/planning/searches/default.aspx",
    "CAMDEN": "https://planningrecords.camden.gov.uk",

    # Home Counties / Green Belt
    "SURREY HEATH": "https://publicaccess.surreyheath.gov.uk/online-applications",
    "GUILDFORD": "https://publicaccess.guildford.gov.uk/online-applications",
    "SEVENOAKS": "https://pa.sevenoaks.gov.uk/online-applications",
    "DARTFORD": "https://publicaccess.dartford.gov.uk/online-applications",
    "MAIDSTONE": "https://pa.midkent.gov.uk/online-applications",
    "TUNBRIDGE WELLS": "https://twbcpa.midkent.gov.uk/online-applications",
    "WINCHESTER": "https://planningapps.winchester.gov.uk/online-applications",
    "NEW FOREST": "https://forms.newforest.gov.uk/planning",
    "DACORUM": "https://planning.dacorum.gov.uk/publicaccess"
}

def is_tree_related(description: str) -> bool:
    """Checks if a planning description is relevant to tree surgeons.
    Uses the same compound-phrase TREE_GOLD list as scanners.py to avoid
    false positives from bare single words (street names, bank "branches",
    "fell down", etc.)."""
    desc = description.lower()
    return any(phrase in desc for phrase in TREE_GOLD)

class IdoxScraper:
    def __init__(self, base_url: str):
        self.base_url = base_url.rstrip("/")
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/115.0.0.0 Safari/537.36",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-GB,en;q=0.5"
        })

    # Throttle so one council's unusual page doesn't send an alert per search
    # term, per city, every single scan cycle -- class-level (shared across
    # instances) since a new IdoxScraper is constructed per scan.
    _structure_alert_throttle: dict = {}
    _structure_alert_throttle_hours = 24.0

    def _alert_possible_structure_change(self, search_term: str, page_text_snippet: str = ""):
        now = time.time()
        last = IdoxScraper._structure_alert_throttle.get(self.base_url, 0)
        if now - last < IdoxScraper._structure_alert_throttle_hours * 3600:
            return
        IdoxScraper._structure_alert_throttle[self.base_url] = now
        # Aug 31 2026: the alert used to say only "no recognisable page" with
        # no way to tell, without live-browsing the portal ourselves, whether
        # the real cause was already-known (e.g. a "too many results" wording
        # variant not yet in the phrase list) or something genuinely new.
        # Including a snippet of the actual page text turns every alert into
        # its own diagnostic -- the fix for a future recurrence of this exact
        # council can usually be read straight off the snippet instead of
        # requiring a manual portal visit first.
        snippet = (page_text_snippet or "")[:400]
        try:
            import notifications
            notifications.send_system_incident_alert(
                category="SCRAPER PAGE STRUCTURE",
                title=f"{self.base_url} may have changed its Idox page layout",
                description=(
                    f"A search for '{search_term}' on {self.base_url} returned a page "
                    f"with no recognisable results list and no 'no results' message "
                    f"either. This usually means either a genuinely unusual empty "
                    f"result, or the council has changed their Idox theme/markup and "
                    f"our parser (which looks for <ul id='searchresults'>) no longer "
                    f"matches anything on this portal -- which would mean leads from "
                    f"this specific council are being silently missed.\n\n"
                    f"First ~400 chars of the page's visible text (use this to spot a "
                    f"wording variant we should add to the known-phrase lists, e.g. "
                    f"another way of saying 'too many results' or 'no results'):\n"
                    f"{snippet}"
                ),
                impact="Possible silent lead loss from this one council if it's a structure change, not a genuine empty result.",
                action_required=f"Check the page text snippet above first -- if it's a wording variant of a known case, add the phrase to mesh_scrapers.py's phrase lists. Otherwise manually open {self.base_url}/search.do?action=advanced to compare page structure.",
                severity="WARNING",
                throttle_hours=IdoxScraper._structure_alert_throttle_hours
            )
        except Exception as e:
            logger.debug(f"[MESH] Could not send structure-change alert for {self.base_url}: {e}")

    def get_csrf_token(self, html_text: str) -> str:
        if not BeautifulSoup:
            return ""
        soup = BeautifulSoup(html_text, 'html.parser')
        csrf_input = soup.find('input', {'name': '_csrf'})
        if csrf_input and csrf_input.get('value'):
            return csrf_input['value']
        return ""

    def _fetch_applicant_and_agent(self, key_val: str) -> Dict:
        """
        Aug 30 2026: opens one application's own "Details" tab (the summary/
        results-list view does NOT include Applicant/Agent -- confirmed live
        against Cornwall Council's portal, which is the same Idox theme every
        council in COUNCIL_REGISTRY runs) and reads:
          - Applicant Name / Applicant Company Name: the real person or business
            who filed it. Councils never publish a phone number or email --
            this name is the most identifying thing publicly available.
          - Agent Name / Agent Company Name: present only when someone (usually
            a tree surgeon) has already been hired to file the application on
            the applicant's behalf. Its presence is what tells us a lead is
            already taken rather than genuinely open.
        Returns {} on any failure -- callers must treat a missing key as
        "unknown", never silently as "no agent".
        """
        out = {}
        try:
            detail_url = f"{self.base_url}/applicationDetails.do?keyVal={key_val}&activeTab=details"
            res = net_utils.smart_get(detail_url, session=self.session, timeout=12)
            if res.status_code != 200:
                return out
            soup = BeautifulSoup(res.text, 'html.parser')
            for th in soup.find_all('th'):
                label = th.get_text(strip=True)
                td = th.find_next_sibling('td')
                if not td:
                    continue
                value = td.get_text(strip=True)
                if not value:
                    continue
                # Aug 31 2026: councils routinely fill this cell with a
                # placeholder like "Not Available" / "N/A" / "-" instead of
                # leaving it blank when there's genuinely no agent on record.
                # Treated as a real name before this fix, that placeholder
                # text was silently flipping has_agent to True for leads that
                # actually have no agent at all -- confirmed live: 8 of 186
                # "has agent" leads in one export had agent_company literally
                # equal to "Not Available". _looks_like_real_value() below is
                # the same defensive filter scanners.py applies to PlanIt's
                # own placeholder values, applied here too since this
                # function feeds the same has_agent field from a different
                # source (the council portal itself, not PlanIt).
                if not _looks_like_real_value(value):
                    continue
                if label == "Applicant Name":
                    out["applicant_name"] = value
                elif label == "Applicant Company Name" and not out.get("applicant_name"):
                    # Aug 30 2026: when the applicant is a company/organisation
                    # rather than a person, Idox's own detail page labels this
                    # row "Applicant Company Name", not "Applicant Name" -- the
                    # docstring above already anticipated this second label,
                    # but only the first was ever actually matched, so every
                    # company-applicant case (a business filing its own
                    # planning application) silently lost its name here. Only
                    # used as a fallback so a genuine "Applicant Name" row
                    # (when both happen to be present) always wins.
                    out["applicant_name"] = value
                elif label == "Agent Name":
                    out["agent_name"] = value
                elif label == "Agent Company Name":
                    out["agent_company"] = value
            out["has_agent"] = bool(out.get("agent_name") or out.get("agent_company"))
            if out["has_agent"]:
                out["agent_is_tree_surgeon"] = classify_agent_as_tree_surgeon(
                    out.get("agent_name"), out.get("agent_company")
                )
        except requests.exceptions.Timeout:
            logger.debug(f"[MESH] Timeout fetching applicant/agent detail for keyVal={key_val} on {self.base_url}")
        except Exception as e:
            logger.debug(f"[MESH] Could not fetch applicant/agent detail for keyVal={key_val} on {self.base_url}: {e}")
        return out

    def search_tree_applications(self, days_back: int = 30, search_term: str = "tree") -> List[Dict]:
        if not BeautifulSoup:
            logger.error("[MESH] BeautifulSoup not installed. Cannot run Idox Scraper.")
            return []

        leads = []
        try:
            # Step 1: Establish session and get CSRF token
            adv_search_url = f"{self.base_url}/search.do?action=advanced"
            res = net_utils.smart_get(adv_search_url, session=self.session, timeout=12)
            if res.status_code != 200:
                logger.warning(f"[MESH] Failed to connect to {self.base_url}. Status: {res.status_code}")
                return leads

            csrf_token = self.get_csrf_token(res.text)

            # Step 2: Prepare POST payload for Advanced Search
            end_date = datetime.datetime.now()
            start_date = end_date - datetime.timedelta(days=days_back)

            payload = {
                "searchCriteria.description": search_term,
                "date(applicationReceivedStart)": start_date.strftime("%d/%m/%Y"),
                "date(applicationReceivedEnd)": end_date.strftime("%d/%m/%Y"),
                "searchType": "Application"
            }
            if csrf_token:
                payload["_csrf"] = csrf_token

            # Step 3: Execute Search
            search_url = f"{self.base_url}/advancedSearchResults.do?action=searchCriteria"
            res_post = net_utils.smart_post(search_url, session=self.session, data=payload, timeout=15)

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
                            lead = {"reference": ref, "address": addr, "description": desc}
                            key_match = re.search(r'keyVal=([^&]+)', res_post.url)
                            if key_match:
                                lead.update(self._fetch_applicant_and_agent(key_match.group(1)))
                            leads.append(lead)
                    return leads

                # Not the single-result redirect either. Before this fix, this
                # silently returned [] whether the council genuinely had zero
                # matches OR their Idox theme/markup had changed and our
                # selectors no longer match anything -- those two cases look
                # identical from here (both "found nothing") but mean very
                # different things: one is normal, the other means we've been
                # silently missing leads from that council. Best-effort
                # heuristic to tell them apart: Idox's own "no results" pages
                # almost always say so somewhere in the page text. If that
                # phrase is absent, this is more likely a real structural
                # break, so flag it (throttled per-council) instead of
                # staying silent. This is a heuristic, not a verified check
                # against every council's theme -- it can still be wrong in
                # either direction, but it's strictly better than no signal
                # at all.
                page_text = soup.get_text(" ", strip=True).lower()
                looks_like_genuine_no_results = any(
                    phrase in page_text for phrase in (
                        "no results", "0 results", "no application", "your search did not match",
                        "did not return any results", "no records"
                    )
                )
                # Aug 30 2026: Idox also returns a valid, non-error page when a
                # search is too broad -- "too many results, please narrow your
                # search" -- distinct from both "genuine zero results" and a
                # real structural break. Recurring false "SCRAPER PAGE
                # STRUCTURE" alerts on Cornwall/Nottingham/Glasgow/Bristol/
                # Guildford/Dartford/Maidstone/Tunbridge Wells/Winchester were
                # traced to this response not being recognized. It's logged
                # distinctly (not silently folded into "no results") because
                # unlike a genuine zero, it means real matching applications
                # likely exist but weren't returned -- a future improvement
                # (narrower date range or search term) could recover them.
                looks_like_too_many_results = any(
                    phrase in page_text for phrase in (
                        "too many results", "narrow your search", "refine your search",
                        "please refine", "more specific search"
                    )
                )
                if looks_like_too_many_results:
                    logger.info(
                        f"[{self.base_url}] Idox search for '{search_term}' matched too many "
                        f"results and was not returned -- consider a narrower search term or "
                        f"date range for this council."
                    )
                elif not looks_like_genuine_no_results:
                    self._alert_possible_structure_change(search_term, page_text_snippet=page_text)
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
                    lead = {
                        "reference": ref,
                        "address": addr,
                        "description": desc
                    }
                    # One extra request per real lead to read its Applicant/Agent
                    # fields (see _fetch_applicant_and_agent) -- small delay to
                    # stay gentle on the council's server since this multiplies
                    # request volume for busy councils with many results.
                    href = a_tag.get('href', '')
                    key_match = re.search(r'keyVal=([^&]+)', href)
                    if key_match:
                        time.sleep(0.4)
                        lead.update(self._fetch_applicant_and_agent(key_match.group(1)))
                    leads.append(lead)

            logger.info(f"[MESH] Successfully scraped {len(leads)} tree leads from {self.base_url} (term='{search_term}')")
            return leads

        except requests.exceptions.Timeout:
            logger.warning(f"[MESH] Timeout accessing {self.base_url}")
        except Exception as e:
            logger.error(f"[MESH] Idox scraping error on {self.base_url}: {e}")
            
        return leads

# Aug 30 2026: Idox's "Public Access" product is the same software regardless
# of which base path a council's IT department chose to mount it at when they
# set it up -- search.do, advancedSearchResults.do, applicationDetails.do,
# and the <ul id="searchresults"> markup IdoxScraper parses are all part of
# the underlying Idox application itself, not something a council can
# customise. "online-applications" is by far the most common convention, but
# "publicaccess" (a literal reference to the product's own name) and
# "idoxpa-web" are also standard Idox deployments. Before this fix,
# scrape_mesh_council() only recognised "online-applications" and silently
# returned [] for anything else -- no error, no log line, nothing -- which
# meant every council using one of these other two conventions (confirmed:
# Edinburgh on idoxpa-web, Dacorum on publicaccess) was being scraped for
# precisely zero leads on every single run despite being a perfectly working
# Idox portal. Checked the other 12 currently-registered non-matching URLs
# (Fife, Bath & North East Somerset, Derby, West Northamptonshire,
# Stratford-on-Avon, Wiltshire, Dorset, Richmond, Wandsworth, Kensington &
# Chelsea, Camden, New Forest) individually -- none of their URLs match any
# known Idox path convention, so they're very likely genuinely different
# software (Northgate/NEC for at least Wandsworth, going by its URL) and
# are NOT silently-broken Idox instances -- left alone here, tracked
# separately for dedicated scrapers.
_KNOWN_IDOX_PATH_MARKERS = ("online-applications", "publicaccess", "idoxpa-web")


def _parse_idox_detail_url(source_url: str) -> Optional[Tuple[str, str]]:
    """Aug 30 2026: PlanIt's own published field dictionary confirms
    applicant_name/agent_name are DELIBERATELY never stored by PlanIt itself
    ("For Data Protection reasons this value is not stored but there is a
    note if it is available in the source") -- that's why they're empty on
    essentially every PlanIt-sourced lead; it isn't a scraper bug. But
    PlanIt's own "url" field (the original planning authority's own
    website) and other_fields.source_url point straight back to the real
    council portal page it scraped from -- which, for the large majority of
    UK authorities, is the exact same Idox software this file already knows
    how to read directly (see COUNCIL_REGISTRY / IdoxScraper above).

    Given one of those URLs, returns (base_url, key_val) -- the two
    arguments IdoxScraper needs -- if it's a recognisable Idox detail page
    with a keyVal, else None (a non-Idox authority, or an unparseable URL --
    both leave the lead exactly as "unconfirmed" as before this function
    existed; this can only ever ADD confirmations, never remove
    information)."""
    if not source_url or not isinstance(source_url, str):
        return None
    lower = source_url.lower()
    key_match = re.search(r'keyVal=([^&]+)', source_url, re.I)
    if not key_match:
        return None
    for marker in _KNOWN_IDOX_PATH_MARKERS:
        idx = lower.find(marker)
        if idx != -1:
            base_url = source_url[:idx + len(marker)]
            return base_url.rstrip("/"), key_match.group(1)
    return None


def is_confirmable_idox_url(source_url: str) -> bool:
    """Cheap (no network) pre-check for whether confirm_agent_status_from_source
    below would actually make a real HTTP request for this URL, or return {}
    immediately because it's not a recognisable Idox detail page. Sep 1 2026:
    added so scanners.py's confirmation loop can skip its polite time.sleep(1.0)
    for the non-Idox case -- live logs showed ~94% of confirmation attempts
    across a full regional sweep (London 68, South East 43, South West 40,
    etc.) coming back "inconclusive", well above what the large-majority-are-
    Idox assumption in confirm_agent_status_from_source's docstring would
    predict. Whether that gap is mostly non-Idox authorities, PlanIt records
    missing a source_url, or genuinely-empty detail pages is still an open
    question (worth a closer look with real data), but regardless of the
    cause, sleeping a full second before a call that was always going to
    return {} with zero network activity was pure wasted time -- across ~180
    non-hits in one run, that's several real minutes of the "scans take
    hours" complaint that bought nothing."""
    return _parse_idox_detail_url(source_url) is not None


def confirm_agent_status_from_source(source_url: str) -> Dict:
    """Reuses IdoxScraper._fetch_applicant_and_agent -- built for
    mesh_scrapers.py's own directly-registered councils -- against ANY
    Idox authority PlanIt covers, by following PlanIt's own link back to
    the original source page. This is what turns most of PlanIt's
    structural "we don't know" into a real, confirmed yes/no, at the scale
    of however many of PlanIt's 420 authorities run recognisable Idox
    software -- not just the handful in COUNCIL_REGISTRY. Returns {} --
    unconfirmed, exactly as if this function didn't exist -- for a
    non-Idox authority, an unparseable URL, or any fetch failure."""
    parsed = _parse_idox_detail_url(source_url)
    if not parsed:
        return {}
    base_url, key_val = parsed
    scraper = IdoxScraper(base_url)
    return scraper._fetch_applicant_and_agent(key_val)


def scrape_mesh_council(city_name: str) -> List[Dict]:
    """
    Entry point for the MESH orchestrator.
    Returns a list of leads, or [] if no leads/failure.
    """
    city_upper = city_name.strip().upper()

    # Handle known IDOX implementations
    base_url = COUNCIL_REGISTRY.get(city_upper)
    if base_url and any(marker in base_url.lower() for marker in _KNOWN_IDOX_PATH_MARKERS):
        logger.info(f"[MESH] Routing {city_upper} to free Idox Engine...")
        scraper = IdoxScraper(base_url)

        # Multi-pass search: Idox's basic description field only accepts one
        # plain-text term, so run it once per high-signal term and dedupe by
        # reference (mirrors the SIC-code multi-pass pattern used for item 5's
        # Companies House expansion).
        seen_refs = set()
        merged_leads = []
        for term in IDOX_SEARCH_TERMS:
            try:
                term_leads = scraper.search_tree_applications(days_back=7, search_term=term)
            except Exception as e:
                logger.debug(f"[MESH] {city_upper} search term '{term}' failed: {e}")
                continue
            for lead in term_leads:
                ref = lead.get("reference")
                if ref and ref in seen_refs:
                    continue
                if ref:
                    seen_refs.add(ref)
                merged_leads.append(lead)
            time.sleep(1)  # be polite to the council portal between passes

        return merged_leads

    return []

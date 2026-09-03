import os
import time
import datetime
import base64
import requests
import logging
import re
import html
import random
import urllib.parse
from concurrent.futures import ThreadPoolExecutor
import database
from typing import Optional, List, Dict, Tuple, Set, Any
from dotenv import load_dotenv
import net_utils




load_dotenv()
CH_KEY = os.getenv("COMPANIES_HOUSE_KEY", "").strip()
GOOGLE_MAPS_KEY = os.getenv("GOOGLE_MAPS_KEY", "").strip()
logger = logging.getLogger("vector-data-labs")


import threading as _threading
_CH_RATE_LOCK = _threading.Lock()
_CH_LAST_CALL = [0.0]
_CH_MIN_INTERVAL = 0.6  # seconds between Companies House calls

# Sep 2 2026: same shared-lock pattern as the Companies House throttle above,
# applied to DuckDuckGo. get_google_places_info() used to just do
# `time.sleep(1.2)` inside the function body -- but every call site that
# calls it runs that function inside a ThreadPoolExecutor with anywhere from
# 8 to 20 workers (see call sites in this file), so a sleep local to one
# thread does nothing to slow down the OTHER 7-19 threads hitting DDG's
# html.duckduckgo.com endpoint at the same moment. That means real request
# bursts of up to 20 simultaneous scrapes against an anti-bot-hardened
# search endpoint every ~1.2s -- exactly the kind of load that gets a
# scraper rate-limited or served a block/CAPTCHA page instead of results.
# This was caught by Nick directly, live in production: pasted logs showed
# "Phone: N/A | Email: N/A" for essentially every single company being
# enriched in a row, including long-established real businesses that
# almost certainly have a findable phone number -- a pattern consistent
# with DDG silently blocking/rate-limiting the batch rather than those
# specific ~40 companies coincidentally having no discoverable contact
# info. A single shared lock/timestamp (like _CH_RATE_LOCK) makes the
# throttle apply across ALL worker threads at once, not per-thread.
_DDG_RATE_LOCK = _threading.Lock()
_DDG_LAST_CALL = [0.0]
_DDG_MIN_INTERVAL = 2.0  # seconds between DDG HTML-scrape requests, globally


def _ddg_throttle():
    with _DDG_RATE_LOCK:
        now = time.time()
        wait = _DDG_MIN_INTERVAL - (now - _DDG_LAST_CALL[0])
        if wait > 0:
            time.sleep(wait)
        _DDG_LAST_CALL[0] = time.time()

def _ch_headers():
    """
    Builds the auth header for Companies House API. The throttle is a shared lock/
    timestamp, not a per-call sleep — this file's call sites run under a
    ThreadPoolExecutor(max_workers=10), and a sleep inside each thread only throttles
    that one thread, letting up to 10 requests through per interval instead of 1
    (blowing past the 600-req/5-min cap this was meant to protect).
    """
    import time
    with _CH_RATE_LOCK:
        now = time.time()
        wait = _CH_MIN_INTERVAL - (now - _CH_LAST_CALL[0])
        if wait > 0:
            time.sleep(wait)
        _CH_LAST_CALL[0] = time.time()
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
        res = net_utils.smart_get(url, headers=_ch_headers(), timeout=10)
        if res.status_code == 401:
            import notifications
            notifications.send_system_incident_alert(
                category="SECURITY & API KEYS",
                title="COMPANIES HOUSE API KEY INVALID / 401 UNAUTHORIZED",
                description="CRITICAL: Companies House API rejected requests with HTTP 401 Unauthorized.",
                impact="Director extraction from Companies House is failing. Newly discovered LTDs will lack verified director names.",
                action_required="Log into Companies House Developer Hub (developer.company-information.service.gov.uk) and update COMPANIES_HOUSE_KEY in Render.",
                severity="CRITICAL",
                throttle_hours=4.0
            )
            return None
        elif res.status_code == 429:
            import notifications
            notifications.send_system_incident_alert(
                category="API RATE LIMIT",
                title="COMPANIES HOUSE RATE LIMIT HIT (600 REQ/5 MIN)",
                description="WARNING: Companies House API rate limit threshold reached (HTTP 429).",
                impact="Partner discovery will pause momentarily until the 5-minute rolling window resets.",
                action_required="No action required if temporary. If frequent, throttle batch research frequency.",
                severity="WARNING",
                throttle_hours=1.0
            )
            return None
        if res.status_code == 200:
            officers = res.json().get("items", [])
            # Sep 2 2026: Companies House officer_role also includes
            # corporate officers -- another company acting as director/
            # secretary/LLP member, not a person -- plus judicial/receiver
            # roles that aren't "the boss" either. Skip these outright so
            # neither loop below can ever hand back a company name dressed
            # up as a director's name. See _is_realistic_person_name for
            # the second, independent check on the name text itself.
            def _is_individual_officer(o: dict) -> bool:
                role = str(o.get("officer_role", "")).lower()
                return not role.startswith(("corporate-", "judicial-", "receiver"))
            # Prefer active directors — skip anyone with a resignation date
            for officer in officers:
                if officer.get("resigned_on") or not _is_individual_officer(officer):
                    continue
                role = officer.get("officer_role", "").lower()
                if role in ("director", "secretary", "managing-director", "ceo", "chief executive officer"):
                    name = officer.get("name", "")
                    # CH returns names as "SURNAME, Firstname" — flip it
                    if "," in name:
                        parts = name.split(",", 1)
                        name = f"{parts[1].strip()} {parts[0].strip()}"
                    return name.title()
            # Fallback: return the first active individual officer regardless of role
            for officer in officers:
                if not officer.get("resigned_on") and _is_individual_officer(officer):
                    name = officer.get("name", "")
                    if "," in name:
                        parts = name.split(",", 1)
                        name = f"{parts[1].strip()} {parts[0].strip()}"
                    return name.title()
    except Exception as e:
        logger.error(f"[CH Officers] Error for {company_number}: {e}")
    return None


def search_companies_house(query: str, items_per_page: int = 50, start_index: int = 0):
    """Searches Companies House for LTD companies matching a query with pagination support."""
    if not CH_KEY or not query:
        return []
    try:
        url = "https://api.company-information.service.gov.uk/search/companies"
        params = {"q": query, "items_per_page": items_per_page}
        if start_index > 0:
            params["start_index"] = start_index
        res = net_utils.smart_get(url, params=params, headers=_ch_headers(), timeout=5)
        if res.status_code == 200:
            return res.json().get("items", [])
    except Exception as e:
        logger.debug(f"[CH Search] Query '{query}' (start_index={start_index}) failed: {e}")
    return []



REQUIRED_TREE_PHRASES = [
    "tree surgery", "tree surgeon", "tree surgeons", "tree care",
    "tree service", "tree services", "tree work", "tree works", "tree felling",
    "arboricultural", "arboriculture", "arborist", "arborists",
    "forestry", "woodland management", "woodland services",
    "stump grinding", "stump removal", "hedge cutting", "hedge trimming"
]

EXCLUDED_TREE_WORDS = [
    # Medical, Surgical & Health
    "breast", "plastic", "cosmetic", "dental", "medical", "clinic", "hospital",
    "surgery centre", "surgical", "ortho", "optic", "laser", "eye", "neck", "spine",
    "doctor", "health", "physio", "chiropractic", "therapy", "psychology", "psychological",
    "social work", "counselling", "counseling", "care home", "nursing home", "hospice",
    # Beauty & Personal Care
    "hair", "skin", "beauty", "nail", "tattoo", "piercing", "ink", "spa", "wellness",
    # Childcare, Education & Schools
    "nursery", "nurseries", "preschool", "pre-school", "childcare", "children",
    "school", "education", "learning", "tots", "playgroup", "daycare", "kindergarten",
    "family centre", "family center", "tuition", "tutoring",
    # Food, Beverage, Pubs & Hospitality
    "restaurant", "café", "cafe", "bakery", "food", "bar", "pub", "coffee", "bistro",
    "tea", "teahouse", "dining", "pizza", "dessert", "cake", "cakes", "chippy", "catering",
    "inn", "inns", "hotel", "lodges", "guest house", "b&b", "bed and breakfast", "brewery", "ales", "beer",
    "fruit", "olive", "palm", "citrus", "apple & tree", "almond tree", "peach tree", "banana",
    # Financial, Legal & Corporate
    "estate agent", "letting", "solicitor", "solicitors", "law", "legal", "lawyer",
    "accountant", "accountants", "accountancy", "accounting", "tax", "payroll",
    "wealth", "mortgage", "mortgages", "finance", "financial", "capital", "investment",
    "investments", "fund", "holdings", "holding", "asset management", "portfolio",
    # Property, Block Management & Residents Associations
    "management company", "management co", "mews management", "court management",
    "close management", "gardens management", "house management", "park management",
    "rtm company", "residents", "freehold", "freeholders", "flats", "apartments", "tenants",
    # IT, Media, Creative & Leisure
    "virtual", "it services", "software", "technologies", "telecom", "communications", "comms",
    "pictures", "films", "film", "music", "productions", "literary", "media ltd", "studios",
    "records", "photography", "design and build", "interiors", "flooring", "tiles", "bathrooms",
    "yoga", "padel", "tennis", "sports", "gym", "fitness", "games ltd", "giftshop", "gifts", "clothing",
    # Non-Contractor / Government / Supply & Machinery
    "commissioners", "commission", "forum", "coalition", "trust", "federation",
    "machinery", "parts direct", "equipment ltd", "hire ltd", "plant hire", "dealers", "sales ltd"
]

TRADE_QUALIFIERS = [
    "surgeon", "surgery", "services", "service", "care", "work", "works",
    "felling", "lopping", "pruning", "arbor", "forestr", "timber", "stump",
    "specialist", "specialists", "management", "solutions", "consultan",
    "contractor", "contractors", "clearance", "hedge", "woodland"
]

def _is_valid_tree_company_name(name: str) -> bool:
    """Deprecated Sep 2 2026 audit: this was an OLDER, narrower name filter
    (EXCLUDED_TREE_WORDS) that three call sites (clean_partner_database,
    sweep_100_random_contractors, populate_2000_partners_into_db) kept
    calling even after is_tree_trade_company_name (EXCLUDED_NAME_WORDS)
    was fixed on Aug 31 2026 to also catch construction/property/
    insurance/conservation/recruitment/web-design company names -- see
    is_tree_trade_company_name's own docstring for the real production
    junk that fix was written for. Two independently-maintained gates for
    the same rule meant that fix only ever reached ONE of four call
    sites. All four now call is_tree_trade_company_name directly; this
    function is kept as a thin forwarding alias only in case anything
    else still imports the old name, so it can never silently drift back
    out of sync again."""
    return is_tree_trade_company_name(name)




import re
import urllib.parse
import html

def _is_valid_uk_phone(phone_str: Optional[str]) -> Optional[str]:
    """Validates and formats UK phone numbers. Rejects US, Australian, NZ numbers."""
    if not phone_str:
        return None
    clean = re.sub(r'[\s\(\)\-\.]', '', phone_str)
    if clean.startswith("+44"):
        clean = "0" + clean[3:]
    elif clean.startswith("0044"):
        clean = "0" + clean[4:]

    # Must start with 01, 02, 03, 07, 08 and be 10 or 11 digits
    if clean.startswith(("01", "02", "03", "07", "08")) and len(clean) in (10, 11):
        # Sep 2 2026 audit: this shape check alone used to be the ONLY
        # gate for what gets written into the phone_number COLUMN itself
        # (scrape_contact_info_from_website / get_google_places_info both
        # call this, not the stricter check). _is_realistic_uk_phone's
        # placeholder-denylist/repeated-digit/second-digit-zero checks were
        # only ever applied later, for the phone:yes/no TAG -- so a
        # placeholder like "01234567890" (right shape, obviously fake)
        # could pass here and sit in the column as if it were the
        # partner's real number, even though the tag correctly says
        # phone:no/contact:dead. Anything reading phone_number directly
        # instead of filtering by tag would still get handed the fake
        # number. Gating on the same realism check here closes that gap at
        # the one place new numbers actually enter the database.
        if not _is_realistic_uk_phone(phone_str):
            return None
        return phone_str.strip()
    return None


def _extract_emails_from_html(html_text: str) -> list[str]:
    """Helper to extract clean, valid email addresses from raw HTML text."""
    if not html_text:
        return []
        
    # Strip script, style, and svg blocks to eliminate JavaScript/npm package version tags (@1.0, @11.7, etc.)
    sanitized = re.sub(r'<(script|style|svg|noscript|iframe)[^>]*>.*?</\1>', '', html_text, flags=re.DOTALL | re.IGNORECASE)
    decoded = html.unescape(sanitized)
    
    # 1. Mailto links
    mailto_matches = re.findall(r'mailto:([a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+)', decoded, re.IGNORECASE)
    # 2. General regex patterns with valid letter-based TLD requirement (2-6 chars)
    raw_matches = re.findall(r'\b[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z]{2,6}(?:\.[a-zA-Z]{2,4})?\b', decoded, re.IGNORECASE)
    # 3. Obfuscated [at] or (at) patterns
    obfuscated = re.findall(r'([a-zA-Z0-9_.+-]+)\s*(?:\[at\]|\(at\)|\s+at\s+)\s*([a-zA-Z0-9-]+\.[a-zA-Z]{2,6})', decoded, re.IGNORECASE)
    obf_emails = [f"{user}@{dom}" for user, dom in obfuscated]

    all_emails = mailto_matches + raw_matches + obf_emails
    excluded_domains = [
        "sentry.io", "wixpress.com", "example.com", "example.org", "domain.com", 
        "schema.org", "w3.org", "googleapis.com", "cloudflare.com", "wordpress.org", 
        "godaddy.com", "webador.com", "mysite.com", "gmail.com.au"
    ]
    excluded_exts = [".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg", ".js", ".css", ".ico", ".woff", ".woff2", ".au", ".nz", ".us", ".ca"]
    placeholder_users = ["yourname", "user", "username", "email", "example", "info.example", "admin@mysite"]

    valid_emails = []
    for email in all_emails:
        clean = email.strip().lower().rstrip(".")
        # Block version-tag style matches from JS (e.g. package@1.2.3)
        if re.search(r'@\d+\.', clean):
            continue
        if any(clean.endswith(ext) for ext in excluded_exts):
            continue
        if any(d in clean for d in excluded_domains):
            continue
        user_part = clean.split("@")[0]
        if user_part in placeholder_users:
            continue
        if len(clean) < 7 or "@" not in clean:
            continue
        valid_emails.append(clean)
    return valid_emails


def scrape_contact_info_from_website(website_url: str):
    """
    Fetches a company's own website and pulls both email and phone from the
    same page fetch (no extra HTTP requests over the old email-only version).

    Aug 30 2026: added phone extraction here after tracing why production
    logs showed "Phone: N/A" for essentially every single contractor found
    (30/30 in one sample window) -- get_google_places_info() below was
    migrated from the paid Google Places API to scraping DuckDuckGo's
    organic HTML search results, but DDG's html.duckduckgo.com endpoint
    returns organic links + snippets, not a business-directory/knowledge-
    panel API -- it has no structured phone field the way Google Places'
    formatted_phone_number did, so its phone regex searching page-snippet
    text was very rarely going to match anything. A company's OWN website is
    a far more reliable place to find a real published phone number than a
    search engine's result blurb, and this function was already fetching
    that page for email -- so it now checks for both there instead of
    leaving phone entirely dependent on the DDG snippet. Also loosened the
    original 2.0s/1.5s timeouts to 4.0s/3.0s: small business sites on cheap
    hosting routinely take longer than 2s, and these calls already run
    inside a ThreadPoolExecutor, so the tighter timeout was trading away
    real matches for a speedup this codebase doesn't actually need serially.

    Returns (email, phone) -- either can be None.
    """
    if not website_url:
        return None, None
    email, phone = None, None
    try:
        # Ignore obvious foreign TLDs
        lower_url = website_url.lower()
        if any(lower_url.endswith(ext) or f"{ext}/" in lower_url for ext in [".com.au", ".net.au", ".co.nz", ".nz", ".au"]):
            return None, None

        if not website_url.startswith("http"):
            website_url = "https://" + website_url.lstrip("/")

        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        }

        def _scan_page(html_text):
            nonlocal email, phone
            if not email:
                emails = _extract_emails_from_html(html_text)
                if emails:
                    email = emails[0]
            if not phone:
                phone_match = re.search(r'\b(07\d{3}\s?\d{6}|0[12]\d{3}\s?\d{5,6})\b', html_text)
                if phone_match:
                    phone = _is_valid_uk_phone(phone_match.group(1))

        # 1. Fetch Homepage (4s timeout)
        try:
            res = net_utils.smart_get(website_url, headers=headers, timeout=4.0)
            if res.status_code == 200:
                _scan_page(res.text)
        except Exception:
            pass

        # 2. Check /contact sub-page (3s timeout) if either is still missing
        if not (email and phone):
            base_url = website_url.rstrip("/")
            try:
                sub_res = net_utils.smart_get(base_url + "/contact", headers=headers, timeout=3.0)
                if sub_res.status_code == 200:
                    _scan_page(sub_res.text)
            except Exception:
                pass

    except Exception as e:
        logger.debug(f"[Contact Scraper] Could not scrape {website_url}: {e}")
    return email, phone


def scrape_email_from_website(website_url: str) -> Optional[str]:
    """Back-compat wrapper around scrape_contact_info_from_website for any
    caller that only wants the email. Prefer the combined function directly
    when you also need phone, so the site is only fetched once."""
    email, _phone = scrape_contact_info_from_website(website_url)
    return email


# Sep 2 2026: hoisted to a single module-level constant. This used to be
# defined separately, inline, in two different functions (the DDG scrape
# below, and the partner-hygiene cleanup pass) -- and when the Places API
# path was added as a third source of website/phone/email data, it never
# got this same filter at all. Confirmed live the same night: several
# genuinely different UK tree surgery companies all got matched to the
# exact same US "10 Summers Heating and Cooling" listing by Google's Text
# Search -- a known pattern with spammy/keyword-stuffed Google Business
# Profile listings returned when no strong real match exists. Accepting
# that at face value would have written a wrong phone/email/website into
# the database as if verified, which is worse than leaving the company
# unconfirmed: a customer would end up calling or emailing a completely
# unrelated business. One shared list now, used everywhere a
# website/phone/email gets accepted from any source.
SPAM_WEBSITE_DOMAINS = [
    "10summersheatingandcoolingllc.pro", "airflexheatingandcoolinginc.xyz",
    "alabamaurbanforestryservice.com", "companiesmadesimple.com",
    "facebook.com", "yell.com", "checkatrade.com", "trustatrader.com",
    "linkedin.com", "instagram.com", "cylex-uk.co.uk", "freeindex.co.uk",
    "thomsonlocal.com", "192.com", "thephonebook.bt.com", "webador.com",
    "mysite.com", "wix.com", "squarespace.com", "wordpress.com"
]


def get_google_places_info(company_name: str, city_or_addr: str = ""):
    """
    Sep 2 2026: restored to the REAL Places API (New) when GOOGLE_MAPS_KEY
    is configured -- and it already IS, sitting unused in this app's own
    environment since "Pillar 3" switched this function over to scraping
    DuckDuckGo's HTML search results instead, purely to dodge the API's
    cost. That trade caught up with this project live: after fixing the
    DDG cross-thread throttle bug (a real, separate issue), Nick reported
    phone/email STILL blank for every single company in a 30+ row sample,
    with no rate-limit error anywhere in the logs -- DuckDuckGo was
    returning normal 200 responses that just weren't parsing into usable
    results (a markup change, a soft block, or both -- impossible to tell
    for certain from this environment, which has no way to fetch
    DuckDuckGo directly to inspect what it's actually sending back). Every
    further DDG-side patch would have been another guess against a search
    engine that has no obligation to keep working for a scraper. The real
    fix is to stop guessing and use the documented, supported API this
    project already pays for.

    Also restores a REAL rating: the Sep 2 2026 audit fix earlier today
    correctly nulled out the old rating field because DDG-scraped HTML
    never had one (it was a hardcoded fake 4.8) -- but Places API (New)
    returns an actual aggregate customer rating, so populating it here is
    now honest, not a regression back to fabricating it.

    Falls back to the old DuckDuckGo scrape ONLY when GOOGLE_MAPS_KEY isn't
    configured at all (local dev/test without the key set), so nothing
    breaks where the key genuinely isn't available.

    Returns: (rating: float|None, phone_number: str|None, website: str|None)
    """
    if GOOGLE_MAPS_KEY and time.time() >= _GOOGLE_PLACES_DAILY_QUOTA_RESET_AT[0]:
        if _google_places_paid_call_budget_available():
            return _get_google_places_info_via_api(company_name, city_or_addr)
        _warn_monthly_cap_exhausted_throttled()
    return _get_google_places_info_via_ddg_scrape(company_name, city_or_addr)


# Sep 2 2026, same evening as the API switch above -- a real incident, not
# a hypothetical: Nick manually triggered a full nationwide autonomous
# cycle to backfill months of "dead" (no phone/email) contacts. That cycle
# calls get_google_places_info once per company -- potentially thousands
# of times in one run -- and every one of those calls bills at Google's
# "Enterprise" SKU rate. Corrected against Google's live pricing table
# this same evening: it's $35 per 1,000 calls after the first 1,000 free
# EACH MONTH, not the $5-per-1,000-after-10,000 this project's own earlier
# research had assumed (a real mistake, caught and fixed the same night by
# checking Google's actual current pricing page rather than trusting the
# earlier assumption). Nick does not have room in his budget to absorb a
# surprise bill, and Google's own daily quota (self-service raised to 75k
# earlier tonight, entirely to fix a DIFFERENT throttling problem) is
# nowhere near tight enough to act as a cost guardrail -- it protects
# Google's infrastructure from abuse, not Nick's wallet.
#
# Fix: a hard, code-level monthly cap on paid Places API calls, enforced
# BEFORE a single call is made, independent of anything configured on
# Google's side. Stored in the `system_state` table (the same durable
# key/value store the autonomous scheduler already uses for
# `last_autonomous_cycle_at`) rather than an in-memory counter -- Render
# restarted TWICE within about ten minutes during tonight's incident, and
# an in-memory counter would have silently reset to zero each time,
# providing zero protection across exactly the failure mode that just
# happened. Once the cap is hit for the current calendar month, every
# further call automatically falls back to the free DuckDuckGo scrape
# (same fallback already used above for Google's own daily quota) instead
# of failing outright -- enrichment keeps producing *some* data, it just
# stops costing money for the rest of the month.
#
# 900 is a deliberately conservative default: comfortably under the 1,000
# free Enterprise-tier calls Google grants each month, leaving headroom
# for any counting slop, so the out-of-the-box behaviour costs
# approximately nothing unless Nick explicitly raises this number here.
GOOGLE_PLACES_MONTHLY_PAID_CALL_CAP = 900
_GOOGLE_PLACES_CAP_WARNING_LAST_LOGGED = [0.0]


def _google_places_paid_call_budget_available() -> bool:
    """Checks AND reserves one slot against the persistent monthly cap in
    one step. This function runs inside a ThreadPoolExecutor across many
    concurrent workers -- the same concurrency shape that caused the DDG
    cross-thread throttle bug earlier this session -- so a plain
    read-then-write here is not perfectly atomic against a genuine
    simultaneous race between threads. Accepted tradeoff: the failure mode
    of that small race is 'a handful of calls over the cap in the worst
    case,' not 'the cap does nothing' -- fine for a cost guardrail, and a
    real database-level atomic increment isn't worth the extra complexity
    for a limit this coarse (hundreds, not a handful).

    Fails CLOSED (assumes no budget) on any error -- including a database
    read failure -- rather than failing open, because for this specific
    guardrail, wrongly skipping a paid call costs nothing, while wrongly
    allowing one when the count can't even be verified is the exact
    mistake this fix exists to prevent."""
    try:
        current_month = datetime.datetime.utcnow().strftime("%Y-%m")
        stored_month = database.get_system_state("google_places_paid_calls_month")
        stored_count_raw = database.get_system_state("google_places_paid_calls_count")
        stored_count = int(stored_count_raw) if (stored_count_raw or "").isdigit() else 0
        if stored_month != current_month:
            stored_count = 0
        if stored_count >= GOOGLE_PLACES_MONTHLY_PAID_CALL_CAP:
            return False
        database.set_system_state("google_places_paid_calls_month", current_month)
        database.set_system_state("google_places_paid_calls_count", str(stored_count + 1))
        return True
    except Exception as e:
        logger.warning(f"[Google Places] Monthly cost-cap check failed ({e}) -- defaulting to NOT spending, safety-first.")
        return False


def _warn_monthly_cap_exhausted_throttled():
    """A big backfill run can call get_google_places_info thousands of
    times in a row -- once the monthly cap is hit, logging every single
    one of those would spam the logs uselessly. Logs at most once per hour
    while the cap stays exhausted."""
    now = time.time()
    if now - _GOOGLE_PLACES_CAP_WARNING_LAST_LOGGED[0] > 3600:
        _GOOGLE_PLACES_CAP_WARNING_LAST_LOGGED[0] = now
        logger.warning(
            f"[Google Places] Monthly paid-call cap ({GOOGLE_PLACES_MONTHLY_PAID_CALL_CAP}) reached for "
            f"{datetime.datetime.utcnow().strftime('%Y-%m')} -- falling back to the free DuckDuckGo scrape "
            f"for the rest of the month. Raise GOOGLE_PLACES_MONTHLY_PAID_CALL_CAP in research.py to spend more."
        )


# Sep 2 2026: live production logs showed the Places API switch above
# working correctly for a while, then every single call starting failing
# with "429 ... Quota exceeded for quota metric 'SearchTextRequest' and
# limit 'SearchTextRequest per day'" -- a project-level daily request cap
# (self-service adjustable in Google Cloud Console under Google Maps
# Platform > Quotas, separate from the monthly free-tier billing
# allowance; confirmed against Google's own current documentation, not
# assumed), most likely left at a low default from when this key sat
# unused. Before this fix, `_get_google_places_info_via_api` treated a 429
# exactly like any other non-200 and returned (None, None, None) -- so the
# instant the daily cap was hit, EVERY remaining company that day silently
# went back to blank phone/email again, the exact symptom this whole
# switch was meant to fix, and every one of those doomed API calls still
# cost a full network round-trip before giving up. Fix: detect this
# specific "per day" quota message, fall back to the free DDG scrape for
# that company (so today's run keeps producing real data instead of
# blanks), and remember it for a cooldown window so later calls this run
# skip the API entirely and go straight to the free path -- no point
# retrying a quota that resets on Google's clock, not ours.
_GOOGLE_PLACES_DAILY_QUOTA_RESET_AT = [0.0]  # epoch seconds; 0 = not currently tripped
_GOOGLE_PLACES_QUOTA_COOLDOWN_SECONDS = 6 * 60 * 60  # conservative guess at "rest of today" -- cheap to re-check, so erring short is safe


def _get_google_places_info_via_api(company_name: str, city_or_addr: str = ""):
    """Places API (New) Text Search, requesting phone/website/rating
    directly in the field mask -- one call gets everything this project
    needs, no separate Place Details call required. See
    get_google_places_info's docstring for why this replaced the DDG
    scrape as the primary path."""
    try:
        query = f"{company_name} {city_or_addr} tree surgery UK".strip()
        url = "https://places.googleapis.com/v1/places:searchText"
        headers = {
            "Content-Type": "application/json",
            "X-Goog-Api-Key": GOOGLE_MAPS_KEY,
            "X-Goog-FieldMask": "places.nationalPhoneNumber,places.websiteUri,places.rating",
        }
        body = {"textQuery": query, "pageSize": 1}
        res = net_utils.smart_post(url, json=body, headers=headers, timeout=10)
        if res.status_code == 429:
            body_text = res.text[:300]
            if "per day" in body_text.lower():
                _GOOGLE_PLACES_DAILY_QUOTA_RESET_AT[0] = time.time() + _GOOGLE_PLACES_QUOTA_COOLDOWN_SECONDS
                logger.warning(
                    f"[Google Places] Daily SearchText quota exhausted (429 'per day') on '{company_name}' -- "
                    f"falling back to the free DuckDuckGo scrape for roughly the next "
                    f"{_GOOGLE_PLACES_QUOTA_COOLDOWN_SECONDS // 3600}h instead of sending more doomed API calls. "
                    f"Fix the underlying cap in Google Cloud Console under Google Maps Platform > Quotas."
                )
            else:
                logger.warning(f"[Google Places] Non-200 (429, not a daily-quota message) for '{company_name}': {body_text}")
            return _get_google_places_info_via_ddg_scrape(company_name, city_or_addr)
        if res.status_code != 200:
            logger.warning(f"[Google Places] Non-200 ({res.status_code}) for '{company_name}': {res.text[:200]}")
            return None, None, None
        places = (res.json() or {}).get("places") or []
        if not places:
            return None, None, None
        place = places[0]
        rating = place.get("rating")
        website = place.get("websiteUri")
        raw_phone = place.get("nationalPhoneNumber")
        phone = _is_valid_uk_phone(raw_phone) if raw_phone else None
        # Sep 2 2026: confirmed live the same night this API switch shipped
        # -- Google's Text Search occasionally returns a completely
        # unrelated business (several different UK tree surgery companies
        # all matched to the same US "10 Summers Heating and Cooling"
        # listing, a known spammy-Google-Business-Profile pattern) rather
        # than genuinely finding nothing. The DDG scrape below already had
        # this exact SPAM_WEBSITE_DOMAINS filter; this path never got it
        # when it was built earlier today. If the returned website matches
        # a known-bad domain, the whole result is untrustworthy -- not just
        # the website field -- since the phone/email came from the same
        # wrong listing, so all three are discarded together rather than
        # writing a wrong phone/email into the database as if verified.
        if website and any(spam in website.lower() for spam in SPAM_WEBSITE_DOMAINS):
            logger.warning(
                f"[Google Places] Rejected a known-spam listing for '{company_name}': {website} -- "
                f"treating as no confident match rather than storing a wrong phone/email/website."
            )
            return None, None, None
        return rating, phone, website
    except Exception as e:
        logger.debug(f"[Google Places] Error fetching info for {company_name}: {e}")
        return None, None, None


def _get_google_places_info_via_ddg_scrape(company_name: str, city_or_addr: str = ""):
    """The original DuckDuckGo HTML scrape -- kept as a fallback for when
    GOOGLE_MAPS_KEY isn't configured (e.g. local dev/test), not as the
    primary path anymore. See get_google_places_info's docstring."""
    try:
        import time
        import urllib.parse
        from bs4 import BeautifulSoup
        import re
        import requests

        _ddg_throttle()  # global cross-thread throttle -- see _DDG_MIN_INTERVAL comment above
        query = f"{company_name} {city_or_addr} tree surgery UK".strip()
        url = "https://html.duckduckgo.com/html/?q=" + urllib.parse.quote(query)
        headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'}
        res = net_utils.smart_get(url, headers=headers, timeout=10)

        if res.status_code != 200:
            # Previously silent: a non-200 (DDG rate-limit/block page, etc.)
            # just fell through to the code below, found no result__url
            # links, and returned (None, None, None) with zero trace of
            # WHY -- indistinguishable in the logs from "this company
            # genuinely has no findable website/phone". Logging it means a
            # future spike in blocked/rate-limited requests is visible
            # instead of silently masquerading as bad enrichment data.
            logger.warning(f"[DDG Scrape] Non-200 ({res.status_code}) for '{company_name}' -- treating as no result, not an error.")

        phone = None
        website = None
        SPAM_DOMAINS = SPAM_WEBSITE_DOMAINS  # Sep 2 2026: now one shared module-level list, see its definition

        if res.status_code == 200:
            soup = BeautifulSoup(res.text, 'html.parser')
            raw_text = soup.get_text(separator=' ')
            
            phone_match = re.search(r'\b(07\d{3}\s?\d{6}|0[12]\d{3}\s?\d{5,6})\b', raw_text)
            if phone_match:
                candidate_phone = _is_valid_uk_phone(phone_match.group(1))
                if candidate_phone:
                    phone = candidate_phone
                
            for a in soup.find_all('a', class_='result__url'):
                href = a.get('href')
                if href:
                    href = href.strip()
                    # Handle DuckDuckGo redirect wrappers
                    if "uddg=" in href:
                        try:
                            parsed_qs = urllib.parse.parse_qs(urllib.parse.urlparse(href).query)
                            if "uddg" in parsed_qs:
                                href = parsed_qs["uddg"][0]
                        except Exception:
                            pass
                            
                    if href.startswith("http") and not any(spam in href.lower() for spam in SPAM_DOMAINS):
                        # Ensure not foreign AU / NZ / US
                        if not any(href.lower().endswith(ext) or f"{ext}/" in href.lower() for ext in [".com.au", ".net.au", ".co.nz", ".nz", ".au"]):
                            website = href
                            break
        
        # Sep 2 2026 audit: this used to be `rating = 4.8 if (phone or
        # website) else None` -- a hardcoded, entirely fabricated number
        # with zero connection to any real review data, written straight
        # into potential_partners.google_rating and presented as if it
        # were a genuine third-party rating. This function only ever
        # scrapes DuckDuckGo HTML search results (see its own docstring --
        # the "Google Places" name is legacy from before that swap), which
        # never carries a real rating at all, so there is no honest value
        # to put here. Returning None is the correct "we don't have this"
        # answer, not a regression -- every partner already stored with
        # google_rating exactly 4.8 is this same fabricated value, not a
        # coincidence, and is a known cleanup item (not fixed here to keep
        # this change to stopping the fabrication at the source first).
        return None, phone, website
    except Exception as e:
        logger.debug(f"[DDG Scrape] Error fetching info for {company_name}: {e}")
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
        "Bridlington", "Ripon", "Skipton", "Selby", "Ilkley", "Otley", "Wetherby",
        "Knaresborough", "Malton", "Thirsk", "Beverley", "Driffield", "Whitby",
        "West Yorkshire", "North Yorkshire", "South Yorkshire", "East Riding of Yorkshire"
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
        "Bromsgrove", "Warwick", "Leamington Spa", "Stratford-upon-Avon", "Kenilworth",
        "Lichfield", "Cannock", "Stafford", "Stoke-on-Trent", "Kidderminster", "Telford",
        "Shrewsbury", "Worcester", "Hereford", "Malvern", "Evesham", "Bridgnorth",
        "Ludlow", "Oswestry", "Warwickshire", "Staffordshire", "Worcestershire", "Shropshire"
    ],
    "Manchester": [
        "Manchester", "Greater Manchester", "Salford", "Stockport", "Trafford", "Altrincham",
        "Sale", "Bolton", "Bury", "Oldham", "Rochdale", "Wigan", "Cheshire", "Warrington",
        "Wilmslow", "Macclesfield", "Knutsford", "Alderley Edge", "Prestbury", "Northwich",
        "Chester", "Crewe", "Stoke-on-Trent", "Lancashire"
    ],
    "North West": [
        "Manchester", "Liverpool", "Salford", "Stockport", "Trafford", "Altrincham",
        "Bowdon", "Hale", "Sale", "Bolton", "Bury", "Oldham", "Rochdale", "Wigan",
        "Warrington", "Chester", "Crewe", "Wilmslow", "Alderley Edge", "Prestbury",
        "Knutsford", "Macclesfield", "Poynton", "Northwich", "Nantwich", "Tarporley",
        "Preston", "Blackpool", "Lancaster", "Blackburn", "Burnley", "Clitheroe",
        "Ribble Valley", "Lytham St Annes", "Southport", "Formby", "Ormskirk",
        "Carlisle", "Kendal", "Windermere", "Keswick", "Penrith",
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
        "Tetbury", "Moreton-in-Marsh", "Stow-on-the-Wold", "Chipping Campden", "Swindon",
        "Marlborough", "Salisbury", "Chippenham", "Trowbridge", "Bradford-on-Avon",
        "Taunton", "Bridgwater", "Yeovil", "Glastonbury", "Wells", "Frome",
        "Minehead", "Exeter", "Plymouth", "Torquay", "Exmouth", "Barnstaple",
        "Tiverton", "Totnes", "Dartmouth", "Truro", "Falmouth", "Penzance",
        "St Ives", "Newquay", "St Austell", "Bournemouth", "Poole", "Christchurch",
        "Dorchester", "Weymouth", "Sherborne", "Wimborne",
        "Somerset", "Gloucestershire", "Wiltshire", "Devon", "Cornwall", "Dorset"
    ],
    "Cornwall": [
        "Truro", "Falmouth", "Penzance", "St Ives", "Newquay", "St Austell", "Bodmin",
        "Camborne", "Redruth", "Helston", "Bude", "Saltash", "Liskeard", "Launceston",
        "Wadebridge", "Padstow", "Hayle", "Torpoint", "Cornwall"
    ],
    "Devon": [
        "Exeter", "Plymouth", "Torquay", "Paignton", "Exmouth", "Barnstaple", "Tiverton",
        "Totnes", "Dartmouth", "Newton Abbot", "Bideford", "Ilfracombe", "Tavistock",
        "Honiton", "Sidmouth", "Teignmouth", "Brixham", "Dawlish", "Devon"
    ],
    "Cumbria": [
        "Carlisle", "Kendal", "Barrow-in-Furness", "Workington", "Whitehaven", "Penrith",
        "Keswick", "Windermere", "Ambleside", "Ulverston", "Cockermouth", "Cumbria", "Lake District"
    ],
    "Sheffield": [
        "Sheffield", "South Yorkshire", "Rotherham", "Barnsley", "Doncaster", "Chesterfield",

        "Dronfield", "Matlock", "Bakewell", "Buxton", "Worksop", "Retford", "Mansfield",
        "Peak District", "Derbyshire"
    ],
    "East Midlands": [
        "Nottingham", "Derby", "Leicester", "Lincoln", "Northampton", "Kettering",
        "Corby", "Wellingborough", "Daventry", "Towcester", "Mansfield", "Chesterfield",
        "Dronfield", "Matlock", "Bakewell", "Ashbourne", "Buxton", "Glossop",
        "Belper", "Loughborough", "Melton Mowbray", "Market Harborough", "Oakham",
        "Uppingham", "Grantham", "Stamford", "Boston", "Sleaford", "Spalding",
        "Newark-on-Trent", "Southwell", "Derbyshire", "Nottinghamshire", "Leicestershire",
        "Lincolnshire", "Northamptonshire", "Rutland", "Peak District"
    ],
    "North East": [
        "Newcastle upon Tyne", "Gateshead", "Sunderland", "Durham", "North Tyneside",
        "South Tyneside", "Middlesbrough", "Stockton-on-Tees", "Darlington", "Hartlepool",
        "Morpeth", "Hexham", "Corbridge", "Ponteland", "Alnwick", "Berwick-upon-Tweed",
        "Cramlington", "Blyth", "Ashington", "Barnard Castle", "Bishop Auckland",
        "Chester-le-Street", "Newton Aycliffe", "Yarm", "Guisborough", "Saltburn-by-the-Sea",
        "Northumberland", "Tyne and Wear", "County Durham"
    ],
    "Newcastle": [
        "Newcastle upon Tyne", "Gateshead", "Sunderland", "Durham", "Middlesbrough",
        "Darlington", "Hexham", "Morpeth", "Alnwick", "Northumberland", "Tyne and Wear"
    ],
    "East of England": [
        "Cambridge", "Norwich", "Ipswich", "Peterborough", "Chelmsford", "Colchester",
        "Southend-on-Sea", "Brentwood", "Billericay", "Basildon", "Epping", "Loughton",
        "Harlow", "Saffron Walden", "Great Dunmow", "Braintree", "Maldon", "Luton",
        "Bedford", "Biggleswade", "Leighton Buzzard", "Dunstable", "St Albans", "Harpenden",
        "Hitchin", "Letchworth", "Stevenage", "Hertford", "Ware", "Bishop's Stortford",
        "Royston", "Ely", "Huntingdon", "St Neots", "Wisbech", "March",
        "Bury St Edmunds", "Newmarket", "Haverhill", "Sudbury", "Stowmarket",
        "Woodbridge", "Felixstowe", "Aldeburgh", "Southwold", "Kings Lynn", "Swaffham",
        "Fakenham", "Cromer", "Holt", "Dereham", "Thetford", "Diss", "Great Yarmouth",
        "Lowestoft", "Cambridgeshire", "Norfolk", "Suffolk", "Essex", "Bedfordshire"
    ],
    "Cambridge": [
        "Cambridge", "Norwich", "Ipswich", "Peterborough", "Ely", "Bury St Edmunds",
        "Huntingdon", "Saffron Walden", "Cambridgeshire", "Norfolk", "Suffolk"
    ],
    "South East": [
        "Brighton", "Hove", "Worthing", "Eastbourne", "Hastings", "Lewes", "Rye",
        "Bexhill", "Southampton", "Portsmouth", "Winchester", "Andover", "Basingstoke",
        "Farnborough", "Aldershot", "Alton", "Petersfield", "Fareham", "Gosport",
        "Havant", "Eastleigh", "Romsey", "Lymington", "New Forest", "Oxford", "Banbury",
        "Bicester", "Witney", "Abingdon", "Didcot", "Henley-on-Thames", "Thame",
        "Reading", "Bracknell", "Wokingham", "Maidenhead", "Windsor", "Slough",
        "Newbury", "Hungerford", "Milton Keynes", "Aylesbury", "High Wycombe",
        "Buckingham", "Amersham", "Chesham", "Beaconsfield", "Marlow", "Gerrards Cross",
        "Canterbury", "Maidstone", "Tunbridge Wells", "Tonbridge", "Sevenoaks",
        "Dartford", "Gravesend", "Ashford", "Folkestone", "Dover", "Deal", "Sandwich",
        "Thanet", "Margate", "Ramsgate", "Sittingbourne", "Faversham", "Cranbrook",
        "Tenterden", "Guildford", "Woking", "Epsom", "Ewell", "Leatherhead",
        "Dorking", "Reigate", "Redhill", "Caterham", "Oxted", "Godstone", "Warlingham",
        "Weybridge", "Walton-on-Thames", "Esher", "Cobham", "Camberley", "Farnham",
        "Haslemere", "Godalming", "Chichester", "Bognor Regis", "Littlehampton",
        "Horsham", "Crawley", "Midhurst", "Petworth", "Haywards Heath", "Burgess Hill",
        "East Grinstead", "Surrey", "Kent", "East Sussex", "West Sussex", "Hampshire",
        "Berkshire", "Buckinghamshire", "Oxfordshire"
    ],
    "Scotland": [
        "Edinburgh", "Glasgow", "Aberdeen", "Dundee", "Inverness", "Perth", "Stirling",
        "Paisley", "East Kilbride", "Livingston", "Hamilton", "Dunfermline", "Kirkcaldy",
        "Ayr", "Kilmarnock", "Greenock", "Coatbridge", "Glenrothes", "Airdrie", "Falkirk",
        "Dumfries", "Motherwell", "Cumbernauld", "Elgin", "St Andrews", "Galashiels",
        "Peebles", "Scottish Borders", "Highlands", "Fife", "Lanarkshire", "Lothian"
    ],
    "Edinburgh": [
        "Edinburgh", "Livingston", "Dunfermline", "Kirkcaldy", "Musselburgh", "Dalkeith",
        "Penicuik", "Queensferry", "Linlithgow", "Bathgate", "Lothian", "Fife"
    ],
    "Glasgow": [
        "Glasgow", "Paisley", "East Kilbride", "Hamilton", "Coatbridge", "Airdrie",
        "Motherwell", "Cumbernauld", "Greenock", "Kilmarnock", "Ayr", "Lanarkshire"
    ],
    "Aberdeen": [
        "Aberdeen", "Dundee", "Inverness", "Perth", "Elgin", "Peterhead", "Fraserburgh",
        "Inverurie", "Stonehaven", "Aberdeenshire", "Highlands"
    ],
    "Wales": [
        "Cardiff", "Swansea", "Newport", "Wrexham", "Barry", "Neath", "Cwmbran",
        "Bridgend", "Llanelli", "Merthyr Tydfil", "Caerphilly", "Pontypridd",
        "Aberystwyth", "Bangor", "Llandudno", "Rhyl", "Carmarthen", "Haverfordwest",
        "Pembrokeshire", "Snowdonia", "Anglesey", "Monmouth", "Abergavenny", "Chepstow"
    ],
    "Cardiff": [
        "Cardiff", "Barry", "Penarth", "Caerphilly", "Pontypridd", "Bridgend", "Cowbridge",
        "Llantrisant", "Vale of Glamorgan", "Rhondda Cynon Taf"
    ],
    "Swansea": [
        "Swansea", "Neath", "Port Talbot", "Llanelli", "Carmarthen", "Gower", "Ammanford"
    ]
}





# Strict tree surgery trade phrases -- a match here is trusted on its own,
# see is_tree_trade_company_name()'s docstring for why.
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
    "development", "developments", "holdings", "management company", "residents", "flats", "apartments",
    # Aug 31 2026: added after a real production run enriched all of
    # these as "new partners" -- see is_tree_trade_company_name()'s
    # docstring for the exact company names this caught live.
    "psychology", "counselling",
    # NOT "consultancy"/"consultants" alone -- "___ Tree Consultancy"
    # is a genuine, common real arboricultural-consultancy naming
    # pattern (confirmed against actual production agent names, e.g.
    # "JN Tree Consultancy"); excluding it wrongly would create a new
    # false negative to fix the psychology one, which "psychology"
    # above already catches on its own.
    "children", "nursery", "childcare", "montessori", "school",
    "mortgage", "broker", "insurance", "pension",
    "court", "rtm company", "right to manage", "leaseholders",
    "padel", "tennis", "gym", "fitness", "leisure centre",
    "protectors", "conservation", "wildlife trust", "friends of",
    "recruitment", "staffing", "training academy",
    "it services", "it support", "software", "web design", "web development",
]


def is_tree_trade_company_name(name: str) -> bool:
    """
    Aug 31 2026 fix: found live in production -- a real scan enriched
    "ACORN TREE PSYCHOLOGY AND CONSULTANCY SERVICES LTD", "APPLE TREE
    CHILDREN'S SERVICES LIMITED", "APPLE TREE IT SERVICES LTD", "APPLE
    TREE MORTGAGE SERVICES LTD", "APPLE TREE COURT (LEWISHAM) RTM COMPANY
    LIMITED", "THE HERTFORDSHIRE PADEL TREE LTD" and more, all as "new
    partners" -- burning real Companies House/Google Places/website-scrape
    calls on a psychology practice, a nursery, an IT company, a mortgage
    broker, a leaseholders' management company, and a padel court, none of
    which do tree work.

    Root cause: the bare `tree` word fallback treated ANY company with
    "tree" somewhere in its name as sufficient evidence on its own -- and
    "tree" is an extremely common, unrelated branding word in the UK
    (nurseries, restaurants/pubs, retirement/managed developments named
    "___ Court", conservation groups). A denylist can never fully
    anticipate every such category on its own.

    Fixed two ways:
    1. A genuine trade-phrase match (REQUIRED_PHRASES: "tree surgery",
       "arborist", "forestry", "hedge cutting", ...) is trusted on its own
       and skips EXCLUDED_NAME_WORDS entirely -- those phrases are
       specific enough to the trade that a real match should never be
       vetoed by an unrelated word elsewhere in the name (a genuine "XYZ
       Arboricultural Consultancy Ltd" must not be thrown out just because
       "consultancy" is also a useful exclusion word for the weak signal
       below).
    2. The weak bare-"tree" fallback still requires clearing
       EXCLUDED_NAME_WORDS, which now also catches the specific non-trade
       categories proven live above.

    Known remaining gap (pre-existing, not introduced by this fix): this
    only matches "tree" as a separate word, so a concatenated brand name
    like "TreeCare" or "TreeRangers" (no space) won't match here even
    though mesh_scrapers.classify_agent_as_tree_surgeon's plain substring
    match would catch it. Left as-is rather than switching to a substring
    match, which would also match unrelated words like "entree" -- flagged
    for a future pass rather than risking a new false-positive class here.
    """
    name_lower = name.lower()
    if any(w in name_lower for w in REQUIRED_PHRASES):
        return True
    if not re.search(r'\btree\b', name_lower):
        return False
    return not any(w in name_lower for w in EXCLUDED_NAME_WORDS)


def _enrich_and_upsert_partner_candidate(item):
    """Sep 2 2026: pulled out of perform_research's local process_single_candidate
    closure so perform_research_by_sic (the new SIC-code discovery path below)
    can share the exact same enrichment/upsert logic instead of drifting its
    own copy -- the same "one shared gate, never a second copy that rots"
    discipline already applied to is_tree_trade_company_name and
    SPAM_WEBSITE_DOMAINS earlier this session. Behaviour is unchanged from
    the original nested function."""
    co, name, company_number, addr, assigned_city = item
    try:
        md_name = get_director_from_ch(company_number)
        rating, phone, website = get_google_places_info(name, f"{addr} {assigned_city}")
        # DDG's search-snippet phone regex above rarely matches (see
        # scrape_contact_info_from_website's docstring) -- the
        # company's own site is a much better source for both.
        email, site_phone = scrape_contact_info_from_website(website) if website else (None, None)
        phone = phone or site_phone
        sic_codes = co.get("sic_codes", [])
        tags = _generate_partner_tags(sic_codes, md_name, phone, email, company_name=name)

        co_conn = database.get_db_conn()
        co_cur = co_conn.cursor()
        co_cur.execute("""
            INSERT INTO potential_partners
                (company_name, company_number, status, address, target_city,
                 sic_codes, md_name, phone_number, google_rating, website, email, tags)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (company_number) DO UPDATE SET
                company_name  = EXCLUDED.company_name,
                target_city   = EXCLUDED.target_city,
                md_name       = COALESCE(EXCLUDED.md_name, potential_partners.md_name),
                phone_number  = COALESCE(EXCLUDED.phone_number, potential_partners.phone_number),
                google_rating = COALESCE(EXCLUDED.google_rating, potential_partners.google_rating),
                website       = COALESCE(EXCLUDED.website, potential_partners.website),
                email         = COALESCE(EXCLUDED.email, potential_partners.email),
                tags          = EXCLUDED.tags
        """, (
            name, company_number, co.get("company_status") or "active",
            addr, assigned_city,
            sic_codes, md_name, phone, rating,
            website, email, tags
        ))
        co_conn.commit()
        co_cur.close()
        co_conn.close()

        # Sep 2 2026: added Website to this line specifically to debug
        # the DDG throttle fix's real-world effect -- after fixing the
        # cross-thread rate-limit bug, Nick reported EVERY company in a
        # 30+ row live sample still coming back Phone: N/A | Email: N/A,
        # with no "[DDG Scrape] Non-200" warning anywhere in the same
        # logs (that warning is the other thing this same fix added).
        # 200-but-empty and non-200-and-logged are two different
        # failure modes needing two different fixes, and this one line
        # is what tells them apart on the next live run: "Website:
        # NONE" every time means get_google_places_info itself never
        # finds a result link (DDG search/parsing problem -- markup
        # changed, or DDG serves a 200 OK soft-block/consent page
        # instead of real results, which a bare status-code check
        # can't detect); a real URL there with Phone/Email still N/A
        # means the website WAS found but scrape_contact_info_from_
        # website can't extract anything from that specific page.
        logger.info(f"[Investigator] ✅ {name} ({assigned_city}) → "
                    f"Director: {md_name or 'N/A'} | Website: {website or 'NONE'} | "
                    f"Phone: {phone or 'N/A'} | Email: {email or 'N/A'}")
        return name
    except Exception as pe:
        logger.error(f"[Investigator] Error on company {name}: {pe}")
        return None


def perform_research(city_name: str):
    """
    Finds Tree Surgery LTD companies via Companies House across major boroughs/districts,
    enforces active LTD filtering, skips already-enriched companies, and enriches
    with director names, Google Places info, and scraped emails using 12 concurrent workers.
    """
    if not CH_KEY:
        logger.error("[Investigator] COMPANIES_HOUSE_KEY not set. Aborting.")
        return

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

        from concurrent.futures import ThreadPoolExecutor
        def fetch_ch_search(q):
            try:
                res = net_utils.smart_get(
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

        candidates_to_enrich = []
        REGIONAL_ALIASES = {
            "newcastle": "north east",
            "north east": "north east",
            "cambridge": "east of england",
            "east of england": "east of england",
            "leeds": "yorkshire",
            "yorkshire": "yorkshire",
            "birmingham": "west midlands",
            "west midlands": "west midlands",
            "manchester": "north west",
            "north west": "north west",
            "bristol": "south west",
            "south west": "south west",
            "sheffield": "east midlands",
            "east midlands": "east midlands",
            "london": "london",
            "south east": "south east",
        }
        target_norm = REGIONAL_ALIASES.get(city_name.lower(), city_name.lower())

        for co in all_companies:
            name = co.get("title", "").upper()
            company_number = co.get("company_number", "")
            if not company_number:
                continue

            if not any(t in name for t in ["LTD", "LIMITED"]):
                continue
            if co.get("company_status") != "active":
                continue

            if company_number in already_enriched:
                continue

            if not is_tree_trade_company_name(name):
                continue

            addr = co.get("address_snippet") or ""
            assigned_city = resolve_uk_city(addr, name, default_city=city_name)
            candidates_to_enrich.append((co, name, company_number, addr, assigned_city))

        logger.info(f"[Investigator] ⚡ {len(candidates_to_enrich)} brand new tree surgery LTDs to enrich for {city_name} (out of {len(all_companies)} raw search items).")


        if candidates_to_enrich:
            from concurrent.futures import ThreadPoolExecutor
            with ThreadPoolExecutor(max_workers=10) as executor:
                executor.map(_enrich_and_upsert_partner_candidate, candidates_to_enrich)

        cur.close()
        conn.close()
        logger.info(f"[Investigator] 🚀 Research complete for {city_name}! Enriched {len(candidates_to_enrich)} new partners.")

    except Exception as e:
        logger.error(f"[Investigator] Fatal error in perform_research: {e}")


# Sep 2 2026: SIC (business-activity) code discovery -- catches genuinely
# relevant tree surgery companies perform_research's name-substring search
# can never find, because their registered name is generic/branded (e.g.
# "Ridgeline Contracting Ltd", "Greenwood Grounds Ltd") and says nothing
# about trees. Companies House's advanced-search endpoint supports filtering
# by SIC code directly. Two confidence tiers, same design already proven in
# bulk_contractor_extractor.py's CONTRACTOR_VERTICALS['tree'] config (that
# file is a standalone, isolated offline CSV tool with its own inferior DDG-
# based enrichment -- including a hardcoded synthetic 4.5 rating placeholder
# -- kept deliberately disconnected from the live DB, so only the SIC-code
# values and the trusted/gated split are reused here, not that file's code):
#   - TREE_SIC_CODES_TRUSTED: silviculture/logging/forestry-support-service
#     codes. Essentially never anything other than genuine tree/forestry
#     work -- accepted on the SIC code alone, no name check.
#   - TREE_SIC_CODES_GATED: general landscape service activities. Broad
#     enough to also cover lawn care, planting, and garden design, so still
#     run through is_tree_trade_company_name (the same single gate every
#     other discovery path in this file uses) to avoid flooding
#     potential_partners with unrelated gardening companies.
TREE_SIC_CODES_TRUSTED = ["02100", "02200", "02400"]  # Silviculture / Logging / Support services to forestry
TREE_SIC_CODES_GATED = ["81300"]                       # Landscape service activities (broad -- name-gated)


def _search_companies_house_by_sic(sic_codes: list, location: str = None, items_per_page: int = 100) -> list:
    """Searches Companies House's advanced-search endpoint by SIC code rather
    than company name. Confirmed live against Companies House's own
    advanced-search API spec (comma-delimited sic_codes param). Returns the
    advanced-search response shape: items carry `company_name` (not `title`)
    and a structured `registered_office_address` dict (not `address_snippet`)
    -- different from the basic /search/companies endpoint perform_research
    uses, so callers must read the right keys for each."""
    if not CH_KEY:
        return []
    url = "https://api.company-information.service.gov.uk/advanced-search/companies"
    params = {
        "sic_codes": ",".join(sic_codes),
        "company_status": "active",
        "company_type": "ltd",
        "size": items_per_page,
    }
    if location:
        params["location"] = location
    try:
        res = net_utils.smart_get(url, headers=_ch_headers(), params=params, timeout=12)
        if res.status_code == 200:
            return res.json().get("items", [])
    except Exception as e:
        logger.debug(f"[Investigator SIC] {sic_codes} @ {location}: {e}")
    return []


def perform_research_by_sic(city_name: str):
    """Runs alongside perform_research (name-search) rather than replacing
    it -- finds tree surgery LTDs whose registered name doesn't mention
    trees at all, via SIC code instead. Shares the exact same dedup/
    enrichment/upsert path (_enrich_and_upsert_partner_candidate) so a SIC-
    code find is indistinguishable in data quality from a name-search find."""
    if not CH_KEY:
        logger.error("[Investigator SIC] COMPANIES_HOUSE_KEY not set. Aborting.")
        return

    try:
        conn = database.get_db_conn()
        cur = conn.cursor()
        cur.execute("SELECT company_number FROM potential_partners WHERE company_number IS NOT NULL")
        already_known = set(r[0] for r in cur.fetchall() if r[0])
        cur.close()
        conn.close()
    except Exception as e:
        logger.error(f"[Investigator SIC] DB error loading known companies: {e}")
        return

    sub_areas = CITY_SUB_AREAS.get(city_name, [city_name])
    primary_area = sub_areas[0]

    jobs = [(TREE_SIC_CODES_TRUSTED, False), (TREE_SIC_CODES_GATED, True)]
    all_candidates = []

    for sic_codes, name_gated in jobs:
        items = _search_companies_house_by_sic(sic_codes, location=primary_area, items_per_page=100)
        for co in items:
            name = (co.get("company_name") or "").upper()
            company_number = co.get("company_number", "")
            if not company_number or company_number in already_known:
                continue
            if not any(t in name for t in ["LTD", "LIMITED"]):
                continue
            if name_gated and not is_tree_trade_company_name(name):
                continue
            already_known.add(company_number)  # dedup within this same run too

            addr_data = co.get("registered_office_address", {}) or {}
            addr = ", ".join(filter(None, [
                addr_data.get("address_line_1"),
                addr_data.get("address_line_2"),
                addr_data.get("locality"),
                addr_data.get("postal_code"),
            ]))
            assigned_city = resolve_uk_city(addr, name, default_city=city_name)
            all_candidates.append((co, name, company_number, addr, assigned_city))

    logger.info(f"[Investigator SIC] ⚡ {len(all_candidates)} new SIC-code-matched tree "
                f"companies to enrich for {city_name} (branded/non-obvious names a "
                f"keyword search would miss).")

    if all_candidates:
        from concurrent.futures import ThreadPoolExecutor
        with ThreadPoolExecutor(max_workers=10) as executor:
            executor.map(_enrich_and_upsert_partner_candidate, all_candidates)

    logger.info(f"[Investigator SIC] 🚀 SIC-code research complete for {city_name}! "
                f"Enriched {len(all_candidates)} new partners.")


def discover_sole_traders_via_google_places(city_name: str, limit_per_area: int = 15) -> int:
    """Sep 2 2026: sole traders never register with Companies House at all --
    perform_research and perform_research_by_sic search Companies House, so
    no amount of tuning those two can ever find a sole trader, structurally,
    regardless of name or SIC code. This is a genuinely different discovery
    method: a Google Places category search ("tree surgeon in <area>")
    instead of a company lookup, storing whatever real business it finds
    even with no company number at all.

    Shares the SAME monthly cost cap as every other paid Places call
    (_google_places_paid_call_budget_available()) -- one hard ceiling
    covering all Places spend combined, matching the fail-closed design the
    cap was built around after the incident that created it. Unlike
    per-company enrichment, ONE budgeted call here can return up to
    `limit_per_area` businesses at once (Google bills per request, not per
    result returned), so this is actually the most budget-efficient way to
    grow the partner list -- worth knowing given the cap is finite.

    Returns the count of new/updated partners written."""
    if not GOOGLE_MAPS_KEY:
        logger.error("[Sole Trader Discovery] GOOGLE_MAPS_KEY not set. Aborting.")
        return 0

    sub_areas = CITY_SUB_AREAS.get(city_name, [city_name])
    primary_area = sub_areas[0]

    if not _google_places_paid_call_budget_available():
        _warn_monthly_cap_exhausted_throttled()
        logger.info(f"[Sole Trader Discovery] Monthly Places budget exhausted -- skipping {city_name} this cycle.")
        return 0

    try:
        query = f"tree surgeon in {primary_area}, UK"
        url = "https://places.googleapis.com/v1/places:searchText"
        headers = {
            "Content-Type": "application/json",
            "X-Goog-Api-Key": GOOGLE_MAPS_KEY,
            "X-Goog-FieldMask": (
                "places.id,places.displayName,places.formattedAddress,"
                "places.nationalPhoneNumber,places.websiteUri,places.rating"
            ),
        }
        body = {"textQuery": query, "pageSize": limit_per_area}
        res = net_utils.smart_post(url, json=body, headers=headers, timeout=10)
        if res.status_code != 200:
            logger.warning(f"[Sole Trader Discovery] Non-200 ({res.status_code}) for '{query}': {res.text[:200]}")
            return 0
        places = (res.json() or {}).get("places") or []
    except Exception as e:
        logger.error(f"[Sole Trader Discovery] Error searching '{city_name}': {e}")
        return 0

    if not places:
        return 0

    try:
        conn = database.get_db_conn()
        cur = conn.cursor()
        cur.execute("SELECT phone_number FROM potential_partners WHERE phone_number IS NOT NULL")
        # _is_valid_uk_phone() returns the number in whatever formatting it
        # arrived in (spaces/dashes preserved) -- the same real number can
        # reach us as "07911 123456" from Google Places and "07911123456"
        # from a website scrape. Dedup on digits-only so those two don't
        # look like different people.
        known_phones = set(
            re.sub(r'[\s\(\)\-\.]', '', r[0]) for r in cur.fetchall() if r[0]
        )
        cur.close()
        conn.close()
    except Exception as e:
        logger.error(f"[Sole Trader Discovery] DB error loading known phones: {e}")
        known_phones = set()

    written = 0
    for place in places:
        place_id = place.get("id")
        name = ((place.get("displayName") or {}).get("text") or "").strip()
        if not place_id or not name:
            continue
        if not is_tree_trade_company_name(name.upper()):
            # Places' own category search is already scoped to "tree
            # surgeon", but a handful of loosely-related results (a garden
            # centre, a general landscaper) slip through relevance ranking
            # -- apply the same single name gate every other discovery path
            # uses rather than trusting Google's relevance alone.
            continue

        website = place.get("websiteUri")
        if website and any(spam in website.lower() for spam in SPAM_WEBSITE_DOMAINS):
            continue

        raw_phone = place.get("nationalPhoneNumber")
        phone = _is_valid_uk_phone(raw_phone) if raw_phone else None
        if phone and re.sub(r'[\s\(\)\-\.]', '', phone) in known_phones:
            # Same real business already known under a Ltd company record
            # found via Companies House -- don't duplicate it as a second,
            # contact-less sole-trader row.
            continue

        address = place.get("formattedAddress") or ""
        rating = place.get("rating")
        assigned_city = resolve_uk_city(address, name, default_city=city_name)
        email, site_phone = scrape_contact_info_from_website(website) if website else (None, None)
        phone = phone or site_phone
        tags = _generate_partner_tags(None, None, phone, email, company_name=name)
        tags.append("registration:sole_trader")

        try:
            w_conn = database.get_db_conn()
            w_cur = w_conn.cursor()
            w_cur.execute("""
                INSERT INTO potential_partners
                    (company_name, company_number, place_id, status, address, target_city,
                     phone_number, google_rating, website, email, tags)
                VALUES (%s, NULL, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (place_id) DO UPDATE SET
                    company_name  = EXCLUDED.company_name,
                    target_city   = EXCLUDED.target_city,
                    phone_number  = COALESCE(EXCLUDED.phone_number, potential_partners.phone_number),
                    google_rating = COALESCE(EXCLUDED.google_rating, potential_partners.google_rating),
                    website       = COALESCE(EXCLUDED.website, potential_partners.website),
                    email         = COALESCE(EXCLUDED.email, potential_partners.email),
                    tags          = EXCLUDED.tags
            """, (
                name.upper(), place_id, "active", address, assigned_city,
                phone, rating, website, email, tags
            ))
            w_conn.commit()
            w_cur.close()
            w_conn.close()
            written += 1
            if phone:
                known_phones.add(re.sub(r'[\s\(\)\-\.]', '', phone))
            logger.info(f"[Sole Trader Discovery] ✅ {name} ({assigned_city}) → "
                        f"Website: {website or 'NONE'} | Phone: {phone or 'N/A'} | Email: {email or 'N/A'}")
        except Exception as we:
            logger.error(f"[Sole Trader Discovery] DB write error for {name}: {we}")

    logger.info(f"[Sole Trader Discovery] 🚀 Sole-trader discovery complete for {city_name}! "
                f"Wrote {written} of {len(places)} Places results (rest were duplicates, spam, or off-trade).")
    return written


def research_all_cities():
    """Runs deep partner research across all UK regions (England, Scotland, Wales).

    Sep 2 2026: now runs three discovery methods per region instead of one --
    the original name-substring search (perform_research), the new SIC-code
    search (perform_research_by_sic) for branded/generically-named Ltd
    companies a keyword search can't find, and Google Places category
    discovery (discover_sole_traders_via_google_places) for sole traders,
    who never appear in Companies House at all no matter how either of the
    first two searches is tuned. All hooked into the same autonomous cycle
    with no separate manual step required. The Places call is
    budget-gated by the same monthly cost cap as every other paid Places
    call in this file -- it will simply do nothing once that's exhausted
    for the month, same fail-closed behaviour as everywhere else."""
    regions = [
        "London", "South East", "South West", "West Midlands",
        "East Midlands", "Yorkshire", "North West", "North East", "East of England",
        "Scotland", "Wales"
    ]
    for r in regions:
        logger.info(f"[Investigator] 🚀 Starting nationwide batch discovery for {r}...")
        perform_research(r)
        perform_research_by_sic(r)
        discover_sole_traders_via_google_places(r)







# Sep 2 2026: enrich_existing_partners commits in chunks of this size
# rather than one giant batch at the very end -- see that function's
# docstring for the incident (progress invisible + a redeploy losing an
# entire in-progress run) this fixes.
COMMIT_CHUNK_SIZE = 50


# Sep 2 2026: partner tagging system, same "total control of our data"
# philosophy as the lead tagging system in scanners.py. Nick's call: a
# partner we can't actually reach (no working phone, no email) is dead to
# us exactly like an unclassified lead -- that has to be a queryable, tagged
# fact, not just an absent column someone has to notice. "Has a phone
# number" also isn't good enough on its own -- enrichment sources
# occasionally hand back junk (a scraped placeholder, a malformed Google
# Places result), so a phone tag means it passed a real sanity check, not
# just "the column isn't NULL".
_UK_PHONE_PLACEHOLDER_NUMBERS = {
    "00000000000", "01111111111", "01234567890", "07000000000", "01212121212",
}
_EMAIL_SHAPE_RE = re.compile(r'^[^@\s]+@[^@\s]+\.[^@\s]+$')
_PLACEHOLDER_EMAIL_DOMAINS = {
    "example.com", "test.com", "none.com", "domain.com", "email.com", "yourcompany.com",
}
# Sep 2 2026: caught during the "don't trust anything inherited, verify it"
# audit Nick asked for after the region-tag issue -- get_director_from_ch's
# fallback loop returns the first active officer REGARDLESS of role when no
# individual director/secretary is found, which includes Companies House
# officer roles that are themselves companies, not people ("corporate-
# director", "corporate-nominee-director", "corporate-secretary", etc). A
# corporate officer's "name" field is just the other company's registered
# name -- title-cased, it looks exactly like a real person's name, so
# without this check director:yes was firing on things like "Acme Trustees
# Limited" and presenting it to Nick as "the boss's name."
_CORPORATE_NAME_MARKERS = {
    "LTD", "LIMITED", "LLP", "PLC", "LLC", "INC", "TRUSTEES", "TRUST",
    "NOMINEES", "NOMINEE", "HOLDINGS", "GROUP", "COMPANY", "CORP",
    "CORPORATION", "SERVICES", "MANAGEMENT", "SECRETARIES", "SECRETARIAL",
}


def _is_realistic_person_name(name: Optional[str]) -> bool:
    """Sanity check for a Companies House officer name that's supposed to
    be a real human being (a 'boss's name', per Nick) rather than another
    company acting as a corporate officer, or a blank/placeholder value
    that happened to survive as a truthy string. Not full validation --
    mirrors the same 'reject the obviously-fake, don't try to prove the
    positive' approach as _is_realistic_uk_phone/_is_realistic_email."""
    if not name:
        return False
    cleaned = str(name).strip()
    if not cleaned:
        return False
    words = cleaned.split()
    # A real full name needs at least a first and last part -- a single
    # token is more often a placeholder ("Unknown", "N/A", "Vacant") than
    # a genuine mononym in this dataset.
    if len(words) < 2:
        return False
    upper_words = {w.strip(".,").upper() for w in words}
    if upper_words & _CORPORATE_NAME_MARKERS:
        return False
    return True
# 2-digit SIC division -> the "kind of business" bucket Nick asked for.
# Coarse on purpose -- this only needs to separate genuine tree-surgery/
# arboriculture/forestry partners from the landscaping/construction/generic
# businesses that occasionally survive clean_partner_database's name-based
# filter, not reproduce the full ONS SIC hierarchy.
SIC_DIVISION_TO_BUSINESS_KIND = {
    "01": "forestry-agriculture", "02": "forestry-agriculture",
    "81": "landscaping-grounds-maintenance",
    "43": "construction-specialist-trade",
    "77": "equipment-hire",
    "96": "other-personal-service",
}

# Sep 2 2026 -- Nick, looking at real data, found AA GARDENING TREE SURGEONS
# LTD (company 12026615) sitting at business:unclassified because its SIC
# code is 91040 (Botanical and zoological gardens and nature reserves
# activities). That's not a fluke of one weird company: ONS's SIC taxonomy
# has no code dedicated to "tree surgery" at all -- everyone in the trade
# registers under whatever adjacent code fits (81300 landscaping, 91040
# botanical/nature reserves, 02 forestry, etc.), so SIC alone can never
# reliably separate a tree surgeon from a general landscaper or a park
# maintenance company. Nick's rule: "if a company has the words 'tree
# surgeon' in their name they are always tree surgeons regardless of sic" --
# the name is a stronger, more specific signal than the SIC code ever can be
# here, so it must be checked FIRST and win outright, not just break a tie.
# is_tree_trade_company_name() already has exactly this decisive phrase list
# (REQUIRED_PHRASES: "tree surgery", "tree surgeon(s)", "arborist", etc.) --
# built for the discovery filter, reused here unchanged rather than keeping
# a second copy of the same judgement call that could drift out of sync.
BUSINESS_KIND_NAME_OVERRIDE = "tree-surgery"


def _is_realistic_uk_phone(phone: Optional[str]) -> bool:
    """Lightweight sanity check, not full validation -- rejects the
    obviously-fake numbers enrichment sources occasionally hand back (a
    scraped placeholder, a malformed listing) so a 'has phone' tag actually
    means something. Not a substitute for calling it."""
    if not phone:
        return False
    digits = re.sub(r'\D', '', str(phone))
    if digits.startswith('44'):
        digits = '0' + digits[2:]
    elif digits.startswith('0044'):
        digits = '0' + digits[4:]
    if len(digits) != 11 or not digits.startswith('0'):
        return False
    if len(set(digits)) == 1:
        return False
    if digits in _UK_PHONE_PLACEHOLDER_NUMBERS:
        return False
    if digits[1] == '0':  # no real UK area/mobile code starts with a second 0
        return False
    return True


def _is_realistic_email(email: Optional[str]) -> bool:
    """Shape check plus a placeholder-domain denylist -- catches the
    'info@example.com' style junk that occasionally comes back from a
    scrape rather than a real inbox."""
    if not email:
        return False
    e = str(email).strip().lower()
    if not _EMAIL_SHAPE_RE.match(e):
        return False
    domain = e.rsplit("@", 1)[-1]
    return domain not in _PLACEHOLDER_EMAIL_DOMAINS


def _classify_business_kind(sic_codes: Optional[list], company_name: Optional[str] = None) -> str:
    """Coarse 'kind of business' bucket. Company name is checked FIRST and
    wins outright when it's decisive (see BUSINESS_KIND_NAME_OVERRIDE's
    comment above -- SIC has no dedicated tree-surgery code at all, so it
    can never be trusted to override a name that plainly says otherwise).
    Only when the name isn't decisive does this fall back to the SIC codes
    Companies House already gave us at discovery time -- zero extra API
    cost, always available for any partner that has sic_codes stored.
    Returns 'unclassified' (not a guess -- see _guess_business_kind for
    the separate lower-confidence pass over that bucket) if neither the
    name nor any SIC division matches."""
    if company_name and is_tree_trade_company_name(company_name):
        return BUSINESS_KIND_NAME_OVERRIDE
    for code in (sic_codes or []):
        division = str(code or "").strip()[:2]
        kind = SIC_DIVISION_TO_BUSINESS_KIND.get(division)
        if kind:
            return kind
    return "unclassified"


# Sep 2 2026: Nick's "third round" idea -- for a partner that comes out of
# _classify_business_kind still 'unclassified' (name wasn't decisive enough
# for is_tree_trade_company_name, and no SIC division matched either),
# make an explicitly-labelled EDUCATED GUESS from the same company name
# using softer, non-decisive keywords, rather than just leaving it as a
# dead end. This is deliberately a SEPARATE, lower-confidence tag
# (business_guess:*, not business:*) -- never silently upgraded to a
# confirmed classification -- so a human glancing at the tags can always
# tell "we know this" from "we suspect this". A name with genuinely no
# signal at all (neither trade-specific nor these softer hints) gets no
# guess tag and stays plainly unclassified, per Nick's own rule: "if there
# is really nothing to go on they are placed as totally unconfirmed."
_BUSINESS_GUESS_KEYWORDS = {
    "tree-surgery": ("tree", "arb", "timber"),
    "landscaping-grounds-maintenance": ("landscap", "garden", "grounds", "lawn", "turf"),
    "forestry-agriculture": ("forest", "woodland", "farm", "agri"),
}


def _guess_business_kind(company_name: Optional[str]) -> Optional[str]:
    """Softer, best-effort guess for a company name that didn't clear
    is_tree_trade_company_name's decisive bar and has no matching SIC
    division. Checked in a fixed order so a name matching more than one
    bucket's keywords (rare, but "Tree & Garden Services" is a real naming
    pattern) resolves to the most tree-specific bucket first rather than
    an arbitrary dict-iteration order. Returns None -- no guess -- when
    nothing matches at all."""
    if not company_name:
        return None
    name_lower = company_name.lower()
    for kind in ("tree-surgery", "landscaping-grounds-maintenance", "forestry-agriculture"):
        if any(kw in name_lower for kw in _BUSINESS_GUESS_KEYWORDS[kind]):
            return kind
    return None


def _generate_partner_tags(sic_codes: Optional[list], md_name: Optional[str],
                            phone_number: Optional[str], email: Optional[str],
                            company_name: Optional[str] = None) -> list:
    """Builds the full tag list for one partner -- mirrors
    scanners._generate_tags' 'floating bubble' design: every tag is an
    independent fact, callers combine whichever ones they want.

    company_name (Sep 2 2026, optional/keyword so every existing positional
    call site keeps working unchanged): lets _classify_business_kind apply
    Nick's name-overrides-SIC rule, and adds a separate, lower-confidence
    business_guess:* tag when the confirmed classification comes out
    unclassified but the name still hints at a trade -- see
    _guess_business_kind's docstring for why that's a distinct tag, not a
    silent upgrade."""
    has_phone = _is_realistic_uk_phone(phone_number)
    has_email = _is_realistic_email(email)
    business_kind = _classify_business_kind(sic_codes, company_name)
    tags = [
        f"business:{business_kind}",
        "director:yes" if _is_realistic_person_name(md_name) else "director:no",
        "phone:yes" if has_phone else "phone:no",
        "email:yes" if has_email else "email:no",
    ]
    if business_kind == "unclassified":
        guess = _guess_business_kind(company_name)
        if guess:
            tags.append(f"business_guess:{guess}")
    if has_phone or has_email:
        tags.append("contact:reachable")
    else:
        # Per Nick (Sep 2 2026): no working phone AND no working email
        # means this partner is dead to us -- flag it as such rather than
        # leaving it to be noticed only by the absence of a tag.
        tags.append("contact:dead")
    return tags


def enrich_existing_partners(limit: int = 50, city_name: Optional[str] = None) -> int:
    """
    Enriches partners needing contact info (default 50, or every one still
    unenriched if limit=0/None, optionally scoped to one city_name).

    Sep 2 2026: this used to fetch the WHOLE batch, run all of it through
    the Companies House/Google Places/website-scrape pipeline, and only
    write to the database once, right at the end, in one big execute_batch.
    Companies House calls are globally rate-limited to ~1 every 0.6s
    (_CH_RATE_LOCK) regardless of thread count, so a limit=0 run against
    Nick's real backlog (1195+ partners) takes well over 10 minutes, not
    the "5-10 seconds" this docstring used to (wrongly) promise. Writing
    only at the very end meant two real problems: (1) /api-stats showed
    zero progress for the entire run, making a genuinely-still-working job
    indistinguishable from a stuck/dead one, and (2) if the process got
    killed mid-run (a Render redeploy -- which happened twice to this exact
    job) EVERY partner already looked up was thrown away, not just the
    ones still in flight. Now writes in COMMIT_CHUNK_SIZE-sized chunks as
    it goes: progress is visible in real time, and a mid-run kill only
    loses the current small chunk instead of the whole batch.
    """
    if not CH_KEY:
        logger.error("[Enrichment] COMPANIES_HOUSE_KEY not set. Aborting.")
        return 0

    try:
        conn = database.get_db_conn()
        cur = conn.cursor()

        query = """
            SELECT id, company_name, company_number, target_city, address,
                   md_name, phone_number, google_rating, website, email, sic_codes
            FROM potential_partners
            WHERE company_number IS NOT NULL
              AND enriched_at IS NULL
        """
        params = []
        if city_name and city_name.lower() != "uk":
            query += " AND LOWER(target_city) = LOWER(%s)"
            params.append(city_name)

        query += " ORDER BY created_at DESC"

        if limit and limit > 0:
            query += " LIMIT %s"
            params.append(limit)

        cur.execute(query, tuple(params))
        partners = cur.fetchall()
        cur.close()
        conn.close()

        if not partners:
            logger.info(f"[Enrichment] All partners {f'in {city_name}' if city_name else ''} are already enriched!")
            return 0

        logger.info(f"[Enrichment] 🚀 Processing {len(partners)} partners {f'for {city_name}' if city_name else ''} in chunks of {COMMIT_CHUNK_SIZE}...")

        from psycopg2.extras import execute_batch

        def enrich_single_partner(row):
            (pid, name, number, city, addr, existing_md, existing_phone, existing_rating,
             existing_website, existing_email, sic_codes) = row
            try:
                md_name = existing_md or get_director_from_ch(number)
                rating = existing_rating
                phone = existing_phone
                website = existing_website

                if not phone or not website or rating is None:
                    lookup_loc = f"{addr or ''} {city or ''}".strip()
                    g_rating, g_phone, g_website = get_google_places_info(name, lookup_loc)
                    rating = rating if rating is not None else g_rating
                    phone = phone or g_phone
                    website = website or g_website

                email = existing_email
                if (not email or not phone) and website:
                    site_email, site_phone = scrape_contact_info_from_website(website)
                    email = email or site_email
                    phone = phone or site_phone

                tags = _generate_partner_tags(sic_codes, md_name, phone, email, company_name=name)
                return (md_name, phone, rating, website, email, tags, pid)
            except Exception as e:
                logger.debug(f"[Enrichment] Error on {name}: {e}")
                # Still mark enriched_at so it doesn't loop infinitely on faulty records
                tags = _generate_partner_tags(sic_codes, existing_md, existing_phone, existing_email, company_name=name)
                return (existing_md, existing_phone, existing_rating, existing_website, existing_email, tags, pid)

        from concurrent.futures import ThreadPoolExecutor
        total_saved = 0
        for chunk_start in range(0, len(partners), COMMIT_CHUNK_SIZE):
            chunk = partners[chunk_start:chunk_start + COMMIT_CHUNK_SIZE]
            with ThreadPoolExecutor(max_workers=8) as executor:
                chunk_results = list(executor.map(enrich_single_partner, chunk))

            valid_updates = [r for r in chunk_results if r is not None]
            if valid_updates:
                p_conn = database.get_db_conn()
                p_cur = p_conn.cursor()
                execute_batch(p_cur, """
                    UPDATE potential_partners
                    SET md_name = %s, phone_number = %s, google_rating = %s,
                        website = %s, email = %s, tags = %s, enriched_at = NOW()
                    WHERE id = %s
                """, valid_updates, page_size=25)
                p_conn.commit()
                p_cur.close()
                p_conn.close()
                total_saved += len(valid_updates)

            logger.info(
                f"[Enrichment] Progress: {chunk_start + len(chunk)}/{len(partners)} processed, "
                f"{total_saved} saved so far {f'for {city_name}' if city_name else ''}."
            )

        logger.info(f"[Enrichment] 🎯 Complete! Enriched and saved {total_saved} partners in {city_name or 'batch'}.")
        return total_saved

    except Exception as e:
        logger.error(f"[Enrichment] Fatal error: {e}")
        return 0




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
    Retroactive cleanup: applies the comprehensive negative keyword filter to ALL existing
    partners in the DB, deletes non-tree businesses (nurseries, cafes, law, property mgmt, etc.),
    cleans up corrupted npm-package emails, strips spam websites, and accurately
    re-assigns the genuine city from UK postcode/address analysis.
    Run via /clean-partners.
    """
    try:
        conn = database.get_db_conn()
        cur = conn.cursor()

        cur.execute("SELECT id, company_name, address, target_city, phone_number, website, email FROM potential_partners")
        all_partners = cur.fetchall()
        logger.info(f"[Cleanup] {len(all_partners)} partners to review.")

        from psycopg2.extras import execute_batch

        delete_ids = []
        update_rows = []
        updated_cities = 0
        
        SPAM_DOMAINS = SPAM_WEBSITE_DOMAINS  # Sep 2 2026: now one shared module-level list, see its definition

        for (pid, name, addr, current_city, raw_phone, raw_website, raw_email) in all_partners:
            # Sep 2 2026 audit: this was calling the OLDER, narrower
            # _is_valid_tree_company_name (EXCLUDED_TREE_WORDS) despite its
            # own comment here claiming it was "the unified, bulletproof
            # validation gate" -- it was never updated when
            # is_tree_trade_company_name (EXCLUDED_NAME_WORDS) was fixed on
            # Aug 31 2026 to also catch construction/property/insurance/
            # conservation/recruitment/web-design company names. That meant
            # a row like "OAK TREE INSURANCE BROKERS LTD" already sitting
            # in the table would survive a /clean-partners run untouched.
            # Now calls the same, current, single gate every discovery path
            # uses.
            if not is_tree_trade_company_name(name):
                delete_ids.append((pid,))
                continue

            # 2. Sanitize phone numbers to strictly UK
            valid_phone = _is_valid_uk_phone(raw_phone)
            
            # 3. Sanitize websites
            cleaned_website = raw_website
            if raw_website:
                lower_web = raw_website.lower()
                if any(spam in lower_web for spam in SPAM_DOMAINS) or any(ext in lower_web for ext in [".com.au", ".net.au", ".co.nz", ".nz", ".au"]):
                    cleaned_website = None

            # 4. Sanitize emails (purge JS/npm package version tags like intl-segmenter@11.7.10)
            cleaned_email = raw_email
            if raw_email:
                lower_em = raw_email.lower().strip()
                # Check for version-tag style matches or dummy/spam emails
                if re.search(r'@\d+\.', lower_em) or any(x in lower_em for x in ["intl-segmenter", "slick-carousel", "tailwindcss", "leaflet", "bootstrap", "aos@", "yourname@", "example@", "@example.", "mysite.com", "webador.com"]):
                    cleaned_email = None
                elif not re.search(r'@[a-z0-9-]+\.[a-z]{2,6}', lower_em):
                    cleaned_email = None

            # 5. Accurate real city resolution from postcode and address
            real_city = resolve_uk_city(addr, name, default_city=current_city or "UK")
            update_rows.append((real_city, valid_phone, cleaned_website, cleaned_email, pid))
            if real_city != current_city:
                updated_cities += 1

        if delete_ids:
            execute_batch(cur, "DELETE FROM potential_partners WHERE id = %s", delete_ids, page_size=100)
            logger.info(f"[Cleanup] Batch deleted {len(delete_ids)} non-tree companies.")

        if update_rows:
            execute_batch(cur, """
                UPDATE potential_partners
                SET target_city = %s, phone_number = %s, website = %s, email = %s
                WHERE id = %s
            """, update_rows, page_size=100)
            logger.info(f"[Cleanup] Batch sanitized {len(update_rows)} verified partners.")

        conn.commit()
        kept = len(update_rows)
        removed = len(delete_ids)

        cur.close()
        conn.close()

        logger.info(f"[Cleanup] Complete. Kept: {kept} | Removed: {removed} | Cities Re-assigned: {updated_cities}")
        return {"kept": kept, "removed": removed, "updated_cities": updated_cities}

    except Exception as e:
        logger.error(f"[Cleanup] Error: {e}")
        return {"error": str(e)}


def sweep_100_random_contractors(target_count: int = 50) -> dict:
    """
    Ultra-Fast Non-Blocking Contractor Discovery:
    Discovers `target_count` brand new UK tree surgery LTDs across Great Britain,
    enriches directors and phones in parallel, and commits them in a single batch.
    Completes in ~5-10 seconds without hitting timeouts.
    """
    if not CH_KEY:
        return {"error": "COMPANIES_HOUSE_KEY missing"}

    SWEEP_TARGETS = [
        ("London & Surrey", ["London", "Surrey", "Richmond", "Bromley", "Croydon", "Barnet", "Enfield", "Wandsworth", "Kingston", "Harrow"]),
        ("Home Counties South", ["Guildford", "Woking", "Reigate", "Sevenoaks", "Tunbridge Wells", "Maidstone", "Crawley", "Horsham"]),
        ("Home Counties North", ["Reading", "Slough", "Windsor", "High Wycombe", "St Albans", "Watford", "Chelmsford", "Colchester"]),
        ("South Coast", ["Southampton", "Portsmouth", "Winchester", "Basingstoke", "Bournemouth", "Brighton", "Chichester"]),
        ("West Country", ["Bristol", "Bath", "Gloucester", "Cheltenham", "Swindon", "Taunton", "Exeter", "Plymouth", "Truro", "Dorset", "Somerset", "Cornwall"]),
        ("West Midlands", ["Birmingham", "Coventry", "Wolverhampton", "Solihull", "Dudley", "Walsall", "Warwick", "Stoke-on-Trent", "Shropshire", "Worcester"]),
        ("East Midlands", ["Nottingham", "Leicester", "Derby", "Northampton", "Lincoln", "Mansfield", "Chesterfield"]),
        ("East of England", ["Norwich", "Ipswich", "Cambridge", "Colchester", "Chelmsford", "Peterborough", "Norfolk", "Suffolk"]),
        ("Manchester & Cheshire", ["Manchester", "Stockport", "Salford", "Bolton", "Altrincham", "Wilmslow", "Chester", "Warrington", "Knutsford", "Macclesfield"]),
        ("Yorkshire", ["Leeds", "Sheffield", "Bradford", "York", "Harrogate", "Wakefield", "Huddersfield", "Hull", "Doncaster"]),
        ("North East & Cumbria", ["Newcastle", "Sunderland", "Durham", "Middlesbrough", "Darlington", "Carlisle", "Penrith", "Kendal", "Northumberland"]),
        ("Lancashire & Merseyside", ["Liverpool", "Preston", "Blackpool", "Lancaster", "Blackburn", "Southport", "Wigan"]),
        ("Scotland Central Belt", ["Edinburgh", "Glasgow", "Stirling", "Falkirk", "Livingston", "Paisley", "Hamilton", "Dunfermline"]),
        ("Scotland Highlands & Coast", ["Aberdeen", "Dundee", "Inverness", "Perth", "St Andrews", "Elgin", "Dumfries", "Ayr", "Kilmarnock", "Galashiels"]),
        ("South & Mid Wales", ["Cardiff", "Swansea", "Newport", "Bridgend", "Barry", "Neath", "Pontypridd", "Cwmbran", "Carmarthen", "Aberystwyth"]),
        ("North Wales", ["Wrexham", "Bangor", "Llandudno", "Rhyl", "Flintshire", "Conwy", "Gwynedd", "Anglesey"])
    ]


    KEYWORDS = ["tree surgery", "tree surgeon", "arboricultural", "tree services", "tree care", "forestry"]

    try:
        t_start = time.time()
        conn = database.get_db_conn()
        cur = conn.cursor()
        cur.execute("SELECT company_number FROM potential_partners")
        existing_numbers = set(r[0] for r in cur.fetchall() if r[0])

        candidates = []
        seen = set(existing_numbers)

        # Paginated trade queries across nationwide tree surgery categories
        TRADE_QUERIES = [
            "tree surgery", "tree surgeon", "tree surgeons", "tree care",
            "tree services", "arboricultural", "arboriculture", "arborist",
            "forestry services", "woodland management", "stump grinding", "hedge trimming",
            "tree work", "tree felling", "tree specialists", "tree management"
        ]

        query_tasks = []
        for kw in TRADE_QUERIES:
            for s_idx in [0, 50, 100, 150, 200, 250, 300, 350, 400, 450, 500]:
                query_tasks.append((kw, s_idx))

        random.shuffle(query_tasks)
        selected_queries = query_tasks[:25]

        def fetch_candidates(q_task):
            kw, s_idx = q_task
            items = search_companies_house(kw, items_per_page=50, start_index=s_idx)
            found = []
            for co in items:
                cnum = co.get("company_number")
                if not cnum:
                    continue
                if co.get("company_status") != "active":
                    continue
                cname = co.get("title", "")
                # Sep 2 2026 audit: was the stale _is_valid_tree_company_name
                # -- see the identical fix/comment in clean_partner_database.
                if not is_tree_trade_company_name(cname):
                    continue
                addr = co.get("address_snippet", "")
                assigned = resolve_uk_city(addr, cname, default_city="UK")
                found.append((co, cname, cnum, addr, assigned))
            return found

        with ThreadPoolExecutor(max_workers=12) as search_executor:
            for found_list in search_executor.map(fetch_candidates, selected_queries):
                for item in found_list:
                    cnum = item[2]
                    if cnum not in seen:
                        seen.add(cnum)
                        candidates.append(item)
                        if len(candidates) >= target_count:
                            break
                if len(candidates) >= target_count:
                    break

        logger.info(f"[Fast Sweep] Found {len(candidates)} brand new candidates in {time.time() - t_start:.2f}s.")


        # Enrich in parallel with 20 workers (no per-thread DB overhead)
        def enrich_item(item):
            co, name, company_number, addr, assigned_region = item
            md_name = get_director_from_ch(company_number)
            rating, phone, website = get_google_places_info(name, f"{addr} {assigned_region}")
            # Aug 30 2026: this used to hardcode email = None and never even
            # attempt to scrape it, despite website often being found above --
            # same bug fixed in process_single_candidate/enrich_single_partner.
            email, site_phone = scrape_contact_info_from_website(website) if website else (None, None)
            phone = phone or site_phone
            sic_codes = co.get("sic_codes", [])
            tags = _generate_partner_tags(sic_codes, md_name, phone, email, company_name=name)
            return (
                name, company_number, co.get("company_status"),
                addr, assigned_region,
                sic_codes, md_name, phone, rating,
                website, email, tags
            )

        with ThreadPoolExecutor(max_workers=20) as enrich_executor:
            enriched_rows = list(enrich_executor.map(enrich_item, candidates))


        # Single batch insert into PostgreSQL
        from psycopg2.extras import execute_batch
        execute_batch(cur, """
            INSERT INTO potential_partners
                (company_name, company_number, status, address, target_city,
                 sic_codes, md_name, phone_number, google_rating, website, email, tags)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (company_number) DO UPDATE SET
                company_name  = EXCLUDED.company_name,
                target_city   = EXCLUDED.target_city,
                md_name       = COALESCE(EXCLUDED.md_name, potential_partners.md_name),
                phone_number  = COALESCE(EXCLUDED.phone_number, potential_partners.phone_number),
                google_rating = COALESCE(EXCLUDED.google_rating, potential_partners.google_rating),
                website       = COALESCE(EXCLUDED.website, potential_partners.website),
                email         = COALESCE(EXCLUDED.email, potential_partners.email),
                tags          = EXCLUDED.tags
        """, enriched_rows, page_size=100)
        conn.commit()

        cur.execute("SELECT COUNT(*) FROM potential_partners")
        total_now = cur.fetchone()[0]
        cur.close()
        conn.close()

        elapsed = round(time.time() - t_start, 2)
        directors_found = sum(1 for r in enriched_rows if r[6])
        phones_found = sum(1 for r in enriched_rows if r[7])

        logger.info(f"[Fast Sweep] Finished {len(enriched_rows)} in {elapsed}s. Total in DB: {total_now}")
        return {
            "status": "success",
            "new_inserted": len(enriched_rows),
            "directors_found": directors_found,
            "phones_found": phones_found,
            "total_db_partners": total_now,
            "time_seconds": elapsed
        }

    except Exception as e:
        logger.error(f"[Fast Sweep] Fatal error: {e}")
        return {"error": str(e)}


def populate_2000_partners_into_db() -> dict:
    """
    Nationwide High-Capacity Discovery Engine (Autonomous Background Daemon):
    Sweeps paginated trade categories across all of Great Britain in self-regulating cycles,
    enriches with Companies House directors and Google Places in parallel (20 workers),
    and commits in single bulk transactions.
    Safe to run autonomously in the background with zero freezing.
    """
    if not CH_KEY:
        logger.error("[Bulk Harvest] COMPANIES_HOUSE_KEY not set.")
        return {"error": "COMPANIES_HOUSE_KEY missing"}

    TRADE_QUERIES = [
        "tree surgery", "tree surgeon", "tree surgeons", "tree care",
        "tree services", "arboricultural", "arboriculture", "arborist",
        "forestry services", "woodland management", "stump grinding", "hedge trimming",
        "tree work", "tree felling", "tree specialists", "tree management"
    ]

    try:
        t_start = time.time()
        conn = database.get_db_conn()
        cur = conn.cursor()

        cur.execute("SELECT company_number FROM potential_partners")
        existing_numbers = set(r[0] for r in cur.fetchall() if r[0])
        logger.info(f"[Bulk Harvest] Starting harvest. {len(existing_numbers)} partners currently in DB.")

        seen_numbers = set(existing_numbers)
        total_inserted = 0

        # Build list of paginated trade query tasks
        all_query_tasks = []
        for kw in TRADE_QUERIES:
            for s_idx in [0, 50, 100, 150, 200, 250, 300, 350, 400, 450, 500, 550, 600]:
                all_query_tasks.append((kw, s_idx))

        random.shuffle(all_query_tasks)

        # Process in chunks of 20 queries with a polite 2-second rate-limit pause between cycles
        chunk_size = 20
        for cycle_idx in range(0, min(len(all_query_tasks), 80), chunk_size):
            cycle_tasks = all_query_tasks[cycle_idx:cycle_idx + chunk_size]
            candidates = []

            def fetch_paginated(task):
                kw, s_idx = task
                items = search_companies_house(kw, items_per_page=50, start_index=s_idx)
                found = []
                for co in items:
                    cnum = co.get("company_number")
                    if not cnum or cnum in seen_numbers:
                        continue
                    if co.get("company_status") != "active":
                        continue
                    cname = co.get("title", "")
                    # Sep 2 2026 audit: was the stale _is_valid_tree_company_name
                    # -- see the identical fix/comment in clean_partner_database.
                    if not is_tree_trade_company_name(cname):
                        continue
                    addr = co.get("address_snippet", "")
                    assigned = resolve_uk_city(addr, cname, default_city="UK")
                    found.append((co, cname, cnum, addr, assigned))
                return found

            with ThreadPoolExecutor(max_workers=10) as s_exec:
                for found_list in s_exec.map(fetch_paginated, cycle_tasks):
                    for item in found_list:
                        cnum = item[2]
                        if cnum not in seen_numbers:
                            seen_numbers.add(cnum)
                            candidates.append(item)

            if not candidates:
                continue

            logger.info(f"[Bulk Harvest Cycle {cycle_idx//chunk_size + 1}] Found {len(candidates)} fresh candidates. Enriching...")

            def enrich_item(item):
                co, name, company_number, addr, assigned_region = item
                md_name = get_director_from_ch(company_number)
                rating, phone, website = get_google_places_info(name, f"{addr} {assigned_region}")
                # Aug 30 2026: same fix as the other enrich_item -- this used
                # to hardcode email=None and never attempt to scrape it.
                email, site_phone = scrape_contact_info_from_website(website) if website else (None, None)
                phone = phone or site_phone
                sic_codes = co.get("sic_codes", [])
                tags = _generate_partner_tags(sic_codes, md_name, phone, email, company_name=name)
                return (
                    name, company_number, co.get("company_status"),
                    addr, assigned_region,
                    sic_codes, md_name, phone, rating,
                    website, email, tags
                )

            with ThreadPoolExecutor(max_workers=20) as enrich_exec:
                enriched_rows = list(enrich_exec.map(enrich_item, candidates))

            # Batch insert
            from psycopg2.extras import execute_batch
            execute_batch(cur, """
                INSERT INTO potential_partners
                    (company_name, company_number, status, address, target_city,
                     sic_codes, md_name, phone_number, google_rating, website, email, tags)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (company_number) DO UPDATE SET
                    company_name  = EXCLUDED.company_name,
                    target_city   = EXCLUDED.target_city,
                    md_name       = COALESCE(EXCLUDED.md_name, potential_partners.md_name),
                    phone_number  = COALESCE(EXCLUDED.phone_number, potential_partners.phone_number),
                    google_rating = COALESCE(EXCLUDED.google_rating, potential_partners.google_rating),
                    website       = COALESCE(EXCLUDED.website, potential_partners.website),
                    email         = COALESCE(EXCLUDED.email, potential_partners.email),
                    tags          = EXCLUDED.tags
            """, enriched_rows, page_size=100)
            conn.commit()

            total_inserted += len(enriched_rows)
            logger.info(f"[Bulk Harvest Cycle {cycle_idx//chunk_size + 1}] Inserted {len(enriched_rows)} contractors. Total added so far: {total_inserted}")
            time.sleep(2.0)  # Polite pause between cycles

        cur.execute("SELECT COUNT(*) FROM potential_partners")
        total_now = cur.fetchone()[0]
        cur.close()
        conn.close()

        logger.info(f"[Bulk Harvest Complete] Total inserted: {total_inserted} | Total in DB: {total_now} in {round(time.time() - t_start, 2)}s.")
        return {"new_inserted": total_inserted, "total_partners": total_now}

    except Exception as e:
        logger.error(f"[Bulk Harvest] Fatal error: {e}")
        return {"error": str(e)}
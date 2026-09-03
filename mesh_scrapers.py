import logging
import requests
import datetime
import urllib3
import re
import time
from typing import List, Dict, Optional, Tuple
from urllib.parse import urljoin, unquote

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


# Sep 3 2026: Idox's own "Important Dates" table renders its date cells as
# plain text, not a machine-readable attribute -- format varies by council
# theme (seen live: "02/09/2026", "2 Sep 2026", "02-Sep-2026"). No new
# dependency added for this (python-dateutil isn't in requirements.txt, and
# this project has already been burned once by a production import that
# worked locally but wasn't actually installed on Render) -- a short list of
# explicit formats covers every variant seen so far, and an unparseable
# value safely returns None (registered_date just stays unset, exactly
# today's behaviour) rather than crashing the whole lead insert.
_IDOX_DATE_FORMATS = ("%d/%m/%Y", "%d-%m-%Y", "%d %b %Y", "%d-%b-%Y", "%d %B %Y", "%d-%B-%Y")


def _parse_idox_date(raw: Optional[str]) -> Optional[str]:
    """Parses an Idox detail-page date cell into an ISO 'YYYY-MM-DD' string,
    or None if it doesn't match any known format (e.g. a placeholder like
    'Not Available' already filtered out by the caller, or a genuinely new
    format this hasn't seen yet)."""
    if not raw or not isinstance(raw, str):
        return None
    cleaned = raw.strip()
    for fmt in _IDOX_DATE_FORMATS:
        try:
            return datetime.datetime.strptime(cleaned, fmt).date().isoformat()
        except ValueError:
            continue
    return None


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

# Sep 2 2026, master_expansion_plan_v2.md build-order step 2's flagged gap:
# "building an actual HMO-priority mesh scraper (new search terms, new
# council list) is separate future work, not a wiring gap." This is that
# work. Reuses scanners.py's own multi-vertical resolver instead of a second,
# locally-duplicated classifier -- same defensive fallback pattern as
# TREE_GOLD above (tree-only behaviour, unchanged) if scanners.py isn't
# importable.
try:
    from scanners import _resolve_vertical
except ImportError:
    def _resolve_vertical(text):
        return "tree" if is_tree_related(text) else None

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
#
# Sep 2 2026: added "woodland" and "hedgerow". "Hedgerow Removal Notice" is a
# genuinely distinct consent type (Hedgerows Regulations 1997, not a TPO or a
# tree-in-conservation-area notice) that "hedge" alone may not reliably
# surface if a council's search backend tokenizes by whole word rather than
# substring -- cheap to add explicitly rather than assume. "Woodland
# management"/"woodland" applications are a similarly real, separate wording
# pattern. This takes the per-council term count from 3 to 5 (+67% request
# volume for this scraper) -- watch logs after deploying for any new 429s/
# bans on top of the ones already being watched for at the 3-term count.
IDOX_SEARCH_TERMS = ["tree", "tpo", "hedge", "hedgerow", "woodland"]

# Sep 2 2026: HMO-specific search terms, run ONLY against councils confirmed
# below to actually have HMO application volume worth searching for (running
# these against every registered council would triple/quadruple this file's
# already-throttled request volume for near-zero yield at councils with no
# Article 4 HMO direction in force, since most HMO conversions there are
# permitted development and never generate a planning application at all).
IDOX_HMO_SEARCH_TERMS = ["hmo", "house in multiple occupation", "multiple occupation"]

# Real, government-sourced list (NOT the AI-research-derived list flagged as
# error-prone in TASKS.md) -- pulled live from planning.data.gov.uk's
# "article-4-direction" dataset (3,234 records, paginated in full via its
# entity.json API, keyword-matched on name/notes/description for HMO/C4/
# "multiple occupation" phrasing, organisation-entity IDs resolved to real
# council names) on Sep 2 2026. That pull found 35 councils nationally with a
# confirmed HMO-related Article 4 direction; this set is the intersection of
# those 35 with COUNCIL_REGISTRY's already-live-verified Idox portals, i.e.
# zero new portal-verification risk to enable HMO search on. The other ~22
# confirmed-HMO councils not yet in COUNCIL_REGISTRY (Crawley, Newcastle,
# Sefton, Harlow, Salford, Fenland, Tower Hamlets, Barking & Dagenham,
# Rother, Rossendale, Tendring, Hillingdon, Halton, North Warwickshire,
# Ipswich, Burnley, Newcastle-under-Lyme, Basingstoke & Deane, Bury -- plus
# Hounslow, which IS HMO-confirmed but was already removed from this registry
# as a confirmed Northgate/NEC portal, not Idox) are real future-work
# candidates, each needing the same live-browser portal verification every
# other entry in this file already went through before being added -- not
# done here. Stevenage was deliberately excluded even though it appeared in
# the raw 35: its one matching direction has an end-date of 2017-09-20 with
# no confirmed live replacement, so it's very likely lapsed.
COUNCILS_WITH_CONFIRMED_HMO_ARTICLE_4 = {
    "BRISTOL", "EXETER", "MILTON KEYNES", "SOUTHWARK", "LEICESTER", "BARNET",
    "PLYMOUTH", "DARTFORD", "OXFORD", "BRENT", "COVENTRY", "YORK", "SOUTHAMPTON",
}

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
logger = logging.getLogger("vector-data-labs")

# The Council Registry maps city/region names to their direct Idox Public Access URLs.
# This cuts out the 3rd party API middleman for these regions.
COUNCIL_REGISTRY = {
    # England Metros & Regions
    "CORNWALL": "https://planning.cornwall.gov.uk/online-applications",
    "NOTTINGHAM": "https://publicaccess.nottinghamcity.gov.uk/online-applications",
    "EDINBURGH": "https://citydev-portal.edinburgh.gov.uk/idoxpa-web",
    "GLASGOW": "https://publicaccess.glasgow.gov.uk/online-applications",
    # Sep 1 2026: this was flagged as "genuinely different software, not a
    # silently-broken Idox instance" in the Aug 30 audit comment below --
    # that turned out to be wrong for this one specifically. Verified live
    # via browser: planning.fife.gov.uk/online is a real, working Idox
    # portal (identical "Simple Search / My Profile / Saved Searches /
    # Applications / Appeals / Enforcements / Weekly-Monthly Lists /
    # Property Map" UI as every other confirmed Idox entry here) -- Idox
    # just isn't mounted at the usual "/online-applications" path for this
    # council, only "/online". See _CONFIRMED_IDOX_EXCEPTIONS below for why
    # this needed code, not just a URL edit, to actually start scraping.
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
    #
    # Sep 1 2026 correction: a SEPARATE, later audit of this file's other
    # "confirmed non-Idox" comment blocks (below, in the London section)
    # found that several of those calls were made by URL-shape reasoning
    # alone, never actually opened in a browser -- and were wrong for Fife
    # and Derby specifically (both real, live Idox, just at non-standard
    # paths; fixed above and in the London section below) and for New
    # Forest (never flagged as dead at all, just registered at the wrong
    # subdomain the whole time). Newcastle and Cambridge above WERE
    # verified against real HTTP behaviour (406 / connection refused) each
    # backed by an independent web check of the replacement platform, so
    # those two conclusions stand -- flagging this here so a future pass
    # doesn't need to redo the entire audit, only knows which parts of it
    # were actually verified live.
    #   "MANCHESTER" (Sep 1 2026): the old pa.manchester.gov.uk Idox URL
    #       failed DNS resolution entirely (flagged in TASKS.md). Verified
    #       live via browser against manchester.gov.uk's own current "See or
    #       comment on planning applications" page: the council's own
    #       "View planning applications online" link now points to
    #       https://arcusbe.manchester.gov.uk/pr/s/register-view -- Arcus
    #       Global's "Arcus BE" platform (a Salesforce Experience Cloud
    #       community, going by the URL shape), not Idox at all. Manchester
    #       has fully migrated off Idox, so there is no correct Idox URL to
    #       swap in here -- scrape_mesh_council's own _KNOWN_IDOX_PATH_
    #       MARKERS guard would silently no-op on an Arcus URL anyway (same
    #       zero-leads outcome as leaving it dead), so removing the entry is
    #       more honest than leaving a URL that can never work. Re-adding
    #       Manchester needs a genuine Arcus BE adapter (a different search
    #       API/interface entirely, most likely a Salesforce REST/Apex
    #       endpoint rather than an HTML form POST) -- real, separate build
    #       work, not a config-only fix. Tracked in TASKS.md.
    "OXFORD": "https://public.oxford.gov.uk/online-applications",
    "YORK": "https://planningaccess.york.gov.uk/online-applications",
    "EXETER": "https://publicaccess.exeter.gov.uk/online-applications",
    "PLYMOUTH": "https://planning.plymouth.gov.uk/online-applications",
    "NORWICH": "https://planning.norwich.gov.uk/online-applications",
    "SOUTHAMPTON": "https://planningpublicaccess.southampton.gov.uk/online-applications",
    "PORTSMOUTH": "https://publicaccess.portsmouth.gov.uk/online-applications",
    "BRIGHTON": "https://planningapps.brighton-hove.gov.uk/online-applications",
    "COVENTRY": "https://planapp.coventry.gov.uk/online-applications",
    # Sep 1 2026: registered at "/active-applications", a guessed path that
    # 404s. Verified live via browser -- the domain root itself
    # (eplanning.derby.gov.uk, no path at all) is a genuine, live Idox
    # "Simple Search" portal. See _CONFIRMED_IDOX_EXCEPTIONS below: a bare
    # root URL matches none of _KNOWN_IDOX_PATH_MARKERS, so the path fix
    # alone isn't enough to make scrape_mesh_council recognise it.
    "DERBY": "https://eplanning.derby.gov.uk",
    "LEICESTER": "https://planning.leicester.gov.uk/online-applications",
    "CHESHIRE EAST": "https://planning.cheshireeast.gov.uk/online-applications",
    "CHESHIRE WEST": "https://pa.cheshirewestandchester.gov.uk/online-applications",
    "NORTH NORTHAMPTONSHIRE": "https://publicaccess.northnorthants.gov.uk/online-applications",
    "MILTON KEYNES": "https://publicaccess.milton-keynes.gov.uk/online-applications",
    "WARWICK": "https://planningdocuments.warwickdc.gov.uk/online-applications",
    "CHELTENHAM": "https://publicaccess.cheltenham.gov.uk/online-applications",
    # Sep 1 2026: the old "planning.gloucester.gov.uk" subdomain failed DNS
    # resolution entirely (flagged in TASKS.md). Verified live via browser --
    # the correct current host is "publicaccess.gloucester.gov.uk" (matches
    # this registry's normal Idox naming convention, e.g. Nottingham above);
    # confirmed it's a genuine, live Idox advanced-search form (Description
    # Keyword, Application Type incl. Tree Preservation Order, Agent, Ward,
    # Status fields all present) before shipping the swap.
    "GLOUCESTER": "https://publicaccess.gloucester.gov.uk/online-applications",

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
    #   "HOUNSLOW": DNS failed in logs. Sep 1 2026 live-browser re-check (part
    #       of the same second-pass audit that caught the Fife/Derby/New Forest
    #       wrong calls below): confirmed non-Idox -- the current portal at
    #       https://planningandbuilding.hounslow.gov.uk/NECSWS/ES/Presentation/
    #       Planning/OnlinePlanning/OnlinePlanningSearch is Northgate/NEC's
    #       product (NECSWS in the URL), the same platform family as
    #       Wandsworth and Camden below. Three confirmed Northgate boroughs
    #       now on file.
    #   "MERTON": DNS failed in logs. Sep 1 2026 live-browser re-check:
    #       confirmed non-Idox -- current search lives at
    #       https://rspandlp.merton.gov.uk/planning/index.html?fa=search, a
    #       bespoke "Regulatory Services Hub" single-page app, not Idox.
    #   "HARINGEY": DNS failed in logs. Sep 1 2026 live-browser re-check:
    #       confirmed non-Idox and confirmed the old planningservices.
    #       haringey.gov.uk/portal Idox-era URL is genuinely dead (navigation
    #       failed outright, not just a permission block). Haringey's current
    #       planning page links to a bespoke in-house map viewer at
    #       my.haringey.gov.uk/custom/haringeyLiteMap.html?layer=
    #       planning_current_apps -- a custom GIS layer, not a text-search
    #       portal of any kind, let alone Idox. No scraper exists for this
    #       shape of platform.
    #   "REDBRIDGE": 404 in logs; Redbridge now runs a "Citizen Portal" planning
    #       system, a different platform from Idox's online-applications.
    #   "HAVERING": 404 in logs. Sep 1 2026 live-browser re-check: confirmed
    #       non-Idox -- https://msp.havering.gov.uk/planning/search-applications
    #       is a bespoke search form (field names like "Applicant House
    #       Number" / "Location Line 1-5" match no Idox convention seen
    #       anywhere else in this registry), no ".do" endpoints anywhere.
    #   "ISLINGTON": DNS failed in logs (added to this list after a second scan run
    #       caught it -- the first pass through this log only sampled part of a run
    #       and missed it). Sep 1 2026 live-browser re-check: confirmed non-Idox --
    #       the council's planning page links to
    #       https://planning.agileapplications.co.uk/islington, which is Agile
    #       Applications' "Citizen Portal" product (page title: "Citizen Portal
    #       Planning") -- the SAME platform already confirmed for Redbridge and
    #       Richmond, just a different council instance of it.
    #   "ST ALBANS": clean 404 x3 in the newest logs (this exact
    #       /rpa/online-applications path). Sep 1 2026 live-browser re-check:
    #       confirmed non-Idox -- current portal at
    #       https://planningapplications.stalbans.gov.uk/planning is branded
    #       "Portal360" (page title confirms it), a different vendor entirely.
    #
    # Sep 1 2026 additions -- same category, found while auditing every
    # registry URL that doesn't match _KNOWN_IDOX_PATH_MARKERS (not from a
    # log error this time, since these never even attempt a request -- see
    # _KNOWN_IDOX_PATH_MARKERS' own comment for why that's worse, not
    # better, than a visible DNS failure). Each verified live via browser
    # before removing, not assumed from the URL string alone:
    #   "RICHMOND": redirects to planning.richmond.gov.uk, a "Citizen
    #       Portal Planning" site -- same non-Idox platform as Redbridge
    #       above, not Idox.
    #   "WANDSWORTH": URL already named it -- .../Northgate/PlanningExplorer/...
    #       is Northgate/NEC's own product, confirmed live.
    #   "KENSINGTON & CHELSEA": redirects to rbkc.gov.uk/planningsearch, a
    #       modern JS search widget ("Time period / Search / All Filters"),
    #       not Idox's classic form.
    #   "CAMDEN": redirects to .../Northgate/PlanningExplorer/GeneralSearch.aspx
    #       -- the same Northgate platform as Wandsworth. Two confirmed
    #       Northgate boroughs now on file -- worth weighing a Northgate
    #       adapter once the HMO build needs London coverage these two
    #       currently can't provide at all.
    #
    # "WEST NORTHAMPTONSHIRE", "STRATFORD-ON-AVON", "BATH & NORTH EAST
    # SOMERSET", "WILTSHIRE" and "DORSET" (formerly registered earlier in
    # this file, non-London) were removed the same way, same day, for the
    # same reason -- confirmed live via browser to be, respectively: a
    # third-party "planning-register.co.uk" vendor platform; a "MyDistrict"
    # branded e-planning system; a bespoke ASP.NET webforms app
    # (app.bathnes.gov.uk); Arcus Global's "Arcus BE" (same platform as
    # Manchester above); and a legacy "dorsetforyou.com" ASP.NET portal
    # (disclaimer.aspx -- Idox always uses Java ".do" servlet endpoints,
    # never ".aspx", a useful quick tell for future checks like this one).

    "HAMMERSMITH & FULHAM": "https://public-access.lbhf.gov.uk/online-applications",

    # Sep 1 2026: re-added after the second-pass live-browser audit (see the
    # "SUTTON" bullet that used to sit in the removed-entries block above --
    # now deleted from there since it's back in service here). The original
    # Aug 30 removal reasoned from a web search that only surfaced an
    # unrelated Cambridgeshire parish council of the same name and gave up;
    # a proper search this time found the real portal directly, and a live
    # browser check confirmed it: title "Applications Search | Sutton
    # Council" (the exact Idox title pattern already confirmed for
    # Gloucester and Fife), a form with the classic Idox "Simple/Advanced"
    # tabs and Application Type/Ward/Agent/Status fields, and a search URL
    # containing both "online-applications" and the Java ".do" servlet
    # signature -- the two strongest Idox tells this whole audit has used.
    "SUTTON": "https://planningregister.sutton.gov.uk/online-applications",

    # Home Counties / Green Belt
    "SURREY HEATH": "https://publicaccess.surreyheath.gov.uk/online-applications",
    "GUILDFORD": "https://publicaccess.guildford.gov.uk/online-applications",
    "SEVENOAKS": "https://pa.sevenoaks.gov.uk/online-applications",
    "DARTFORD": "https://publicaccess.dartford.gov.uk/online-applications",
    "MAIDSTONE": "https://pa.midkent.gov.uk/online-applications",
    "TUNBRIDGE WELLS": "https://twbcpa.midkent.gov.uk/online-applications",
    "WINCHESTER": "https://planningapps.winchester.gov.uk/online-applications",
    # Sep 1 2026: the old "forms.newforest.gov.uk/planning" path 404s.
    # Verified live via browser -- the correct current host is
    # "planning.newforest.gov.uk" (found via a direct web search hit on
    # "planning.newforest.gov.uk/online-applications/search.do", already in
    # the standard Idox convention this registry uses everywhere else);
    # confirmed a genuine, live "Simple Search" Idox portal before shipping.
    "NEW FOREST": "https://planning.newforest.gov.uk/online-applications",
    "DACORUM": "https://planning.dacorum.gov.uk/publicaccess",

    # Sep 3 2026: National Park Authorities -- Nick's explicit ask ("are
    # there any smaller places than councils jobs may be listed?"). Each of
    # the UK's 13 National Parks is legally its OWN planning authority,
    # separate from the underlying district/county council -- e.g. a tree
    # job inside the South Downs falls under South Downs NPA, not
    # Winchester/Chichester/whichever district it geographically sits in,
    # so none of these were ever covered by this registry no matter how
    # many ordinary councils it already had. Checked all 13 individually
    # (live search + fetch, not assumed) before adding anything -- only
    # these 3 actually run Idox "online-applications" software this
    # scraper can read. The other 10 (Peak District: bespoke/migrated
    # system; Lake District, Yorkshire Dales, Eryri/Snowdonia,
    # Pembrokeshire Coast: Agile Applications; Dartmoor: Tascomi;
    # North York Moors: Northgate; Northumberland: planning-register.co.uk;
    # Exmoor: no portal of its own, defers entirely to North Devon/Somerset
    # West & Taunton's own councils) run different vendor software this
    # file has no adapter for -- a real future opportunity, not wired in
    # here rather than guess at 5 different platforms' markup.
    "SOUTH DOWNS": "https://planningpublicaccess.southdowns.gov.uk/online-applications",
    "BROADS AUTHORITY": "https://planning.broads-authority.gov.uk/online-applications",
    "BRECON BEACONS": "https://planningonline.beacons-npa.gov.uk/online-applications",
}

def is_tree_related(description: str) -> bool:
    """Checks if a planning description is relevant to tree surgeons.
    Uses the same compound-phrase TREE_GOLD list as scanners.py to avoid
    false positives from bare single words (street names, bank "branches",
    "fell down", etc.).

    Sep 2 2026 audit: this is only ever reached via _resolve_vertical's own
    ImportError fallback above (in normal operation scanners.py imports
    fine and this whole function is dead code) -- but a defensive fallback
    that's silently wrong the one time it's actually needed is still a real
    bug, so it gets the same fix as the live path. It used to do its own
    plain `phrase in desc` substring check independently of scanners.py's
    _matches_vertical, which is exactly the "two independently maintained
    gates for the same rule" shape: scanners.py's word-boundary fix
    (_keyword_hit, added this same audit pass -- see its docstring for why
    bare "arbor" matching inside the place name "Harborne" was a real
    false-positive) would NOT have automatically reached this second,
    separate check. Reusing _keyword_hit directly (with the same
    ImportError-safe fallback pattern as _resolve_vertical above) instead
    of a third reimplementation keeps this one gate instead of two."""
    try:
        from scanners import _keyword_hit
        return _keyword_hit(description, TREE_GOLD)
    except ImportError:
        desc = str(description or "").lower()
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
          - registered_date (Sep 3 2026): the real date the application was
            received/validated by the council, read from the same "Important
            Dates" table -- used to start the statutory countdown from the
            actual filing date instead of whenever TreeKey's scraper happened
            to find it. Absent (key not set) when the page has no recognisable
            date field, exactly like every other field here.
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
                # Sep 3 2026: Nick's explicit ask -- the statutory countdown
                # should start from the date the application was actually
                # FILED, not the date TreeKey's own scraper happened to find
                # it. Idox's own detail page carries this in its "Important
                # Dates" table; the exact label varies by council theme, so
                # match the common variants in order of how close each one
                # is to the true filing moment (received > validated).
                if label in ("Date Received", "Application Received", "Received Date") and not out.get("registered_date"):
                    parsed = _parse_idox_date(value)
                    if parsed:
                        out["registered_date"] = parsed
                elif label in ("Registration Date", "Valid Date", "Application Validated", "Date Valid") and not out.get("registered_date"):
                    parsed = _parse_idox_date(value)
                    if parsed:
                        out["registered_date"] = parsed
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
        # Sep 2 2026: name kept as-is (only internal caller is scrape_mesh_council
        # below, plus test_scrapers.py references it by this name) but the filter
        # inside is no longer tree-only -- it now tags each lead with whichever
        # vertical _resolve_vertical resolves (tree, hmo, or nothing at all),
        # so a real HMO application found via an HMO search term isn't silently
        # discarded by a hardcoded tree-only gate.
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

            # Sep 1 2026: root-caused via a live browser session against three
            # independent Idox councils (Cornwall, Glasgow, Nottingham) after
            # a near-simultaneous "0 leads / SCRAPER PAGE STRUCTURE" alert
            # wave hit almost every council in COUNCIL_REGISTRY the same day
            # -- too broad and too synchronized to be unrelated councils each
            # redesigning their site independently, which pointed at a
            # platform-wide Idox change instead of a per-council wording
            # issue (the theory the Aug 30 "too many results" fix was built
            # on). Inspecting the real advanced-search <form> on all three
            # live sites (identical HTML across all three, confirming a
            # shared Idox template) found a hidden `caseAddressType:
            # "Application"` field present on every one of them that this
            # payload never sent -- the actual page returned "a server
            # problem prevented..." (an Idox-side error, not a "too many/no
            # results" page), consistent with the server now rejecting a
            # request missing a field its own form always includes. Added
            # below. Also corrected the POST target to match what all three
            # live forms actually submit to (?action=firstPage) instead of
            # the previous ?action=searchCriteria, which may predate a
            # platform update too. `date(applicationReceivedStart/End)` was
            # left as-is -- confirmed still present on 2 of 3 sites checked
            # (Glasgow, Nottingham); Cornwall's form only exposes an
            # equivalent `applicationValidatedStart/End` pair, but a POST
            # field a form doesn't declare is normally just ignored by the
            # server rather than erroring, unlike a required field being
            # absent -- so this isn't being treated as the same class of bug
            # as the missing caseAddressType field above.
            payload = {
                "searchCriteria.description": search_term,
                "date(applicationReceivedStart)": start_date.strftime("%d/%m/%Y"),
                "date(applicationReceivedEnd)": end_date.strftime("%d/%m/%Y"),
                "searchType": "Application",
                "caseAddressType": "Application",
            }
            if csrf_token:
                payload["_csrf"] = csrf_token

            # Step 3: Execute Search
            search_url = f"{self.base_url}/advancedSearchResults.do?action=firstPage"
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
                        vertical = _resolve_vertical(desc)
                        if vertical:
                            lead = {"reference": ref, "address": addr, "description": desc, "vertical": vertical}
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

                vertical = _resolve_vertical(desc)
                if vertical:
                    lead = {
                        "reference": ref,
                        "address": addr,
                        "description": desc,
                        "vertical": vertical,
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
# Idox portal.
#
# Aug 30 2026 note (since corrected, see below): the other 12
# currently-registered non-matching URLs at the time (Fife, Bath & North
# East Somerset, Derby, West Northamptonshire, Stratford-on-Avon,
# Wiltshire, Dorset, Richmond, Wandsworth, Kensington & Chelsea, Camden,
# New Forest) were checked by URL-shape reasoning alone and assumed to all
# be genuinely different software.
#
# Sep 1 2026 correction: actually opening each of those 12 in a browser
# (not just reasoning about the URL string) found that assumption was
# wrong for 3 of them -- Fife (/online) and Derby (bare domain root) are
# both real, live, working Idox portals Idox just doesn't put at the usual
# "/online-applications" path; New Forest was never non-Idox at all, just
# registered at the wrong subdomain. The other 9 (Bath & North East
# Somerset, West Northamptonshire, Stratford-on-Avon, Wiltshire, Dorset,
# Richmond, Wandsworth, Kensington & Chelsea, Camden) really are different
# platforms (Northgate, Arcus BE, Citizen Portal, and assorted bespoke
# .aspx systems -- see the removed-entries comments in COUNCIL_REGISTRY
# above for which is which) and stay excluded, now for a verified reason
# instead of an assumed one.
#
# New Forest's fix was a path/subdomain correction (its corrected URL
# already contains "online-applications", so the marker list alone now
# recognises it -- no code change needed). Fife and Derby can't be fixed
# by editing _KNOWN_IDOX_PATH_MARKERS itself: there is no single short
# substring that would recognise "/online" and a bare domain root without
# ALSO matching things that are almost certainly not Idox (bare "online"
# in particular is common enough in ordinary council URLs to risk false
# positives on councils never actually checked). Rather than loosen the
# general-purpose marker list on unverified guesswork -- the exact mistake
# this correction is fixing -- both are instead named explicitly in
# _CONFIRMED_IDOX_EXCEPTIONS below, added only after being individually
# opened and confirmed as the genuine Idox UI, same discipline as every
# other live-verified fix in this file.
_KNOWN_IDOX_PATH_MARKERS = ("online-applications", "publicaccess", "idoxpa-web")

_CONFIRMED_IDOX_EXCEPTIONS = frozenset({
    "https://planning.fife.gov.uk/online",
    "https://eplanning.derby.gov.uk",
})


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
    is_known_idox = base_url and (
        any(marker in base_url.lower() for marker in _KNOWN_IDOX_PATH_MARKERS)
        or base_url in _CONFIRMED_IDOX_EXCEPTIONS
    )
    if is_known_idox:
        logger.info(f"[MESH] Routing {city_upper} to free Idox Engine...")
        scraper = IdoxScraper(base_url)

        # Multi-pass search: Idox's basic description field only accepts one
        # plain-text term, so run it once per high-signal term and dedupe by
        # reference (mirrors the SIC-code multi-pass pattern used for item 5's
        # Companies House expansion). Sep 2 2026: HMO terms are added to this
        # same pass -- but ONLY for councils in COUNCILS_WITH_CONFIRMED_HMO_
        # ARTICLE_4, so every other registered council's request volume is
        # completely unaffected by this change.
        search_terms = list(IDOX_SEARCH_TERMS)
        if city_upper in COUNCILS_WITH_CONFIRMED_HMO_ARTICLE_4:
            search_terms += IDOX_HMO_SEARCH_TERMS

        seen_refs = set()
        merged_leads = []
        for term in search_terms:
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


# ============================================================================
# Northgate/NEC "PlanningExplorer" scraper
# ============================================================================
# Sep 3 2026: built during the "which councils are we getting zero leads
# from, like Manchester" audit Nick asked for. Wandsworth, Camden and
# Hounslow were previously REMOVED from COUNCIL_REGISTRY above after being
# confirmed live to run a completely different platform from Idox --
# Northgate/NEC's "PlanningExplorer" product, an older ASP.NET WebForms app,
# not Idox's Java/.do servlet stack. IdoxScraper's search.do/advancedSearch
# flow cannot talk to this at all (confirmed live: 404s), so those councils
# sat at zero leads indefinitely with no separate engine ever built for them
# -- exactly the gap Nick flagged with Manchester.
#
# Verified live (real browser session, Sep 3 2026) against Wandsworth and
# Camden specifically (both confirmed running the same PlanningExplorer
# skin/theme -- search form fields, results table, detail-page layout all
# identical):
#   - GeneralSearch.aspx is a classic ASP.NET WebForms POST -- __VIEWSTATE/
#     __VIEWSTATEGENERATOR/__EVENTVALIDATION must be read from the page's own
#     GET response and posted back alongside the search criteria, or the
#     server rejects the postback. _serialize_form() below reads ALL of the
#     form's current field values (not just these three) rather than
#     guessing which of the many optional fields are safe to omit.
#   - The Application Type dropdown (id="cboApplicationTypeCode") lists a
#     DIFFERENT set of codes per council (Wandsworth: "DD"/"TC"/"TPO";
#     Camden: "20"/"20A"/"19"/"18") -- there is no shared/stable code across
#     councils, so tree-relevant options are found live by matching the
#     option TEXT ("tree"/"tpo"/"preservation"), never a hardcoded code.
#   - Results land in a single <table class="display_table">; each data
#     row's first cell links to StdDetails.aspx?...&PARAM0=<internal id>&...
#     -- that link is what has to be followed for Applicant/Agent, which the
#     results table itself never includes (confirmed empty on every row).
#   - The detail page has no <table>/<th> layout at all (unlike Idox) --
#     each field is `<div><span>Label</span>Value</div>`, confirmed live for
#     "Applicant" (real company name, e.g. "Treehab Arboricultural
#     Contractors") and "Agent" (blank when genuinely no agent on record).
#   - Camden's search FORM was confirmed live the same way; its results/
#     detail pages were not independently re-verified beyond that (both
#     councils share the same XSLT skin family, so this is a reasonable
#     rather than a fully live-confirmed assumption for Camden specifically
#     -- worth a quick live check if Camden ever comes back with 0 leads).
#   - Hounslow's registered URL (planningandbuilding.hounslow.gov.uk/NECSWS/...)
#     is a DIFFERENT, newer Northgate product line ("NEC Synergy" /
#     OnlinePlanningSearch) with a completely different UI and no
#     PlanningExplorer form at all -- NOT covered by this class, deliberately
#     left unregistered rather than guessed at. Genuinely separate build.
#     North York Moors NPA (also flagged as Northgate in the National Parks
#     audit) hasn't been checked against this class live yet either -- add
#     it to NORTHGATE_COUNCILS only once confirmed to run this same
#     PlanningExplorer skin, not assumed from the "Northgate" label alone.
NORTHGATE_COUNCILS = {
    "WANDSWORTH": "https://planning.wandsworth.gov.uk/Northgate/PlanningExplorer",
    "CAMDEN": "https://planningrecords.camden.gov.uk/Northgate/PlanningExplorer",
}

_NORTHGATE_TREE_TYPE_RE = re.compile(r'tree|tpo|preservation', re.IGNORECASE)

# Northgate's own "Status" dropdown value for a genuinely undecided
# application -- confirmed live (Wandsworth: value="1", text="NEW"). Kept as
# a module constant since both councils checked so far share this value/
# label pairing (the same "PL.xml" skin family) -- verify live before
# trusting it for a council whose Status options don't pair "1" with "NEW".
_NORTHGATE_UNDECIDED_STATUS_VALUE = "1"


class NorthgateScraper:
    def __init__(self, base_url: str):
        self.base_url = base_url.rstrip("/")
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/115.0.0.0 Safari/537.36",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-GB,en;q=0.5",
        })

    def _serialize_form(self, soup) -> Dict[str, str]:
        """Reads every field's CURRENT (server-rendered default) value out of
        the search form, exactly as a real browser submission would send
        them unless explicitly overridden below -- avoids guessing which of
        the form's many optional fields (Street Name, Ward, Development
        Type, Date Search...) are safe to omit, and carries the required
        __VIEWSTATE/__VIEWSTATEGENERATOR/__EVENTVALIDATION hidden fields
        through automatically since they're just ordinary hidden inputs."""
        form = soup.find("form")
        data: Dict[str, str] = {}
        if not form:
            return data
        for inp in form.find_all("input"):
            name = inp.get("name")
            if not name:
                continue
            itype = (inp.get("type") or "text").lower()
            if itype in ("checkbox", "radio"):
                if inp.get("checked") is not None:
                    data[name] = inp.get("value", "on")
            elif itype in ("submit", "button", "image", "reset"):
                continue
            else:
                data[name] = inp.get("value", "")
        for sel in form.find_all("select"):
            name = sel.get("name")
            if not name:
                continue
            selected = sel.find("option", selected=True)
            if selected is not None:
                data[name] = selected.get("value", "")
            else:
                first = sel.find("option")
                data[name] = first.get("value", "") if first else ""
        return data

    def _fetch_applicant_and_agent(self, detail_url: str) -> Dict:
        """Northgate's detail page has no table -- each field is a
        `<div><span>Label</span>Value</div>` pair, confirmed live against
        Wandsworth. Same defensive rules as IdoxScraper's own version of
        this method: placeholder text is filtered out (_looks_like_real_
        value, shared module-level helper), and any failure returns {}
        rather than a false 'no agent' answer."""
        out: Dict = {}
        try:
            res = net_utils.smart_get(detail_url, session=self.session, timeout=12)
            if res.status_code != 200:
                return out
            soup = BeautifulSoup(res.text, "html.parser")

            def _label_value(label: str) -> Optional[str]:
                for div in soup.find_all("div"):
                    span = div.find("span", recursive=False)
                    if span and span.get_text(strip=True) == label:
                        full = div.get_text(" ", strip=True)
                        span_text = span.get_text(strip=True)
                        value = full[len(span_text):].strip() if full.startswith(span_text) else full.replace(span_text, "", 1).strip()
                        return value or None
                return None

            applicant = _label_value("Applicant")
            if applicant and _looks_like_real_value(applicant):
                out["applicant_name"] = applicant
            agent = _label_value("Agent")
            if agent and _looks_like_real_value(agent):
                out["agent_name"] = agent
            registered = _label_value("Application Registered")
            parsed = _parse_idox_date(registered)
            if parsed:
                out["registered_date"] = parsed
            out["has_agent"] = bool(out.get("agent_name"))
            if out["has_agent"]:
                out["agent_is_tree_surgeon"] = classify_agent_as_tree_surgeon(
                    out.get("agent_name"), None
                )
        except requests.exceptions.Timeout:
            logger.debug(f"[NORTHGATE] Timeout fetching detail page {detail_url}")
        except Exception as e:
            logger.debug(f"[NORTHGATE] Could not fetch detail page {detail_url}: {e}")
        return out

    def search_tree_applications(self, max_rows_per_type: int = 15) -> List[Dict]:
        """Finds every tree-relevant Application Type this specific
        council's own dropdown actually offers (codes are NOT shared across
        councils -- see NORTHGATE_COUNCILS' own comment), searches each one
        filtered to undecided ('NEW') status, and follows each result's own
        detail-page link for Applicant/Agent -- mirrors IdoxScraper's
        search_tree_applications() output shape exactly (same dict keys) so
        scrape_northgate_council() below can feed leads through the same
        _insert_lead() call site as every other mesh source."""
        if not BeautifulSoup:
            logger.error("[NORTHGATE] BeautifulSoup not installed. Cannot run Northgate Scraper.")
            return []

        leads: List[Dict] = []
        search_url = f"{self.base_url}/GeneralSearch.aspx"
        try:
            res = net_utils.smart_get(search_url, session=self.session, timeout=15)
            if res.status_code != 200:
                logger.warning(f"[NORTHGATE] Failed to load {search_url}. Status: {res.status_code}")
                return leads
            soup = BeautifulSoup(res.text, "html.parser")

            type_select = soup.find("select", {"id": "cboApplicationTypeCode"})
            if not type_select:
                logger.warning(f"[NORTHGATE] {self.base_url} has no cboApplicationTypeCode select -- page structure may have changed.")
                return leads
            tree_codes = [
                opt.get("value", "")
                for opt in type_select.find_all("option")
                if opt.get("value") and _NORTHGATE_TREE_TYPE_RE.search(opt.get_text())
            ]
            if not tree_codes:
                logger.info(f"[NORTHGATE] {self.base_url} lists no tree-related Application Type options.")
                return leads

            base_form_data = self._serialize_form(soup)
            submit_name = "csbtnSearch"
            submit_value = "Search"

            for code in tree_codes:
                time.sleep(1)  # be polite between searches on the same council
                payload = dict(base_form_data)
                payload["cboApplicationTypeCode"] = code
                if "cboStatusCode" in payload:
                    payload["cboStatusCode"] = _NORTHGATE_UNDECIDED_STATUS_VALUE
                payload[submit_name] = submit_value

                try:
                    res_post = net_utils.smart_post(search_url, session=self.session, data=payload, timeout=15)
                except Exception as e:
                    logger.debug(f"[NORTHGATE] {self.base_url} search for type '{code}' failed: {e}")
                    continue

                results_soup = BeautifulSoup(res_post.text, "html.parser")
                table = results_soup.find("table", class_="display_table")
                if not table:
                    # A genuine zero-result search on this platform still
                    # returns a normal page, just without this table -- not
                    # treated as an error, only as "nothing found this pass".
                    continue

                rows = table.find_all("tr")[1:]  # skip header row
                for row in rows[:max_rows_per_type]:
                    cells = row.find_all("td")
                    if len(cells) < 4:
                        continue
                    link = cells[0].find("a")
                    if not link or not link.get("href"):
                        continue
                    reference = link.get_text(strip=True)
                    clean_href = link["href"].replace("\r", "").replace("\t", "").replace("\n", "")
                    detail_url = urljoin(f"{self.base_url}/", clean_href)
                    address = cells[1].get_text(strip=True)
                    description = cells[2].get_text(strip=True)
                    if not reference or not description:
                        continue

                    vertical = _resolve_vertical(description)
                    if not vertical:
                        continue

                    lead = {"reference": reference, "address": address, "description": description, "vertical": vertical}
                    time.sleep(1)  # be polite before the detail-page fetch too
                    lead.update(self._fetch_applicant_and_agent(detail_url))
                    leads.append(lead)
        except Exception as e:
            logger.error(f"[NORTHGATE] Fatal error scraping {self.base_url}: {e}")
        return leads


def scrape_northgate_council(council_name: str) -> List[Dict]:
    """Entry point mirroring scrape_mesh_council() above, for the separate
    NORTHGATE_COUNCILS registry -- kept as its own function/dict rather than
    merged into COUNCIL_REGISTRY since Northgate and Idox need genuinely
    different scraper classes, not just a different base URL."""
    base_url = NORTHGATE_COUNCILS.get(council_name.strip().upper())
    if not base_url:
        return []
    logger.info(f"[NORTHGATE] Scraping {council_name} directly from {base_url}...")
    scraper = NorthgateScraper(base_url)
    try:
        return scraper.search_tree_applications()
    except Exception as e:
        logger.error(f"[NORTHGATE] {council_name} scrape failed: {e}")
        return []


# Sep 3 2026: Agile Applications "Citizen Portal" -- a third planning-portal
# platform, distinct from both Idox and Northgate. Frontend is an AngularJS
# SPA; live browser network inspection (XHR capture during a real UI search
# on Islington's portal) found the actual backend it calls is a plain JSON
# REST API, not something that needs a browser to drive:
#
#   GET {base}//api/application/search?applicationTypeId=<id>&registrationDateFrom=<YYYY-MM-DD>
#   (double slash after the base host is correct -- confirmed literal in the
#   real captured request URL, not a typo)
#
# Required headers, confirmed by monkey-patching XMLHttpRequest.open/
# setRequestHeader/send on the live page and reading back exactly what the
# app itself sent (earlier guesses at a single "x-service" value all got a
# 401 "Client has not beeing selected" -- the app actually sends three
# separate headers together):
#   x-client:  per-council tenant code (e.g. "IS" for Islington)
#   x-product: "CITIZENPORTAL"
#   x-service: "PA"
# No cookies/session are involved -- confirmed via document.cookie being
# empty on a successful call from the page's own console.
#
# IMPORTANT, HONEST CAVEAT: WebFetch and this project's own cloud tooling
# both got HTTP 403 hitting this same domain from outside a real browser,
# while the browser itself (no special auth, just these 3 headers) worked
# cleanly. That could mean either (a) this API blocks any non-browser
# client by IP/ASN reputation -- in which case a plain requests-based
# scraper would ALSO get blocked once deployed, or (b) the 403 was specific
# to this sandbox's own outbound network path and a normal server (e.g.
# Render, this project's actual host) will succeed fine. This has NOT been
# verified from Render -- it can only be confirmed after deployment, by
# checking whether this scraper actually returns leads in production logs
# rather than silently erroring out every run (it fails soft, see below).
#
# Response shape (confirmed live):
#   {"total": N, "results": [{"id", "applicationType", "reference",
#    "webReference", "proposal", "location", "username", "applicantSurname",
#    "agentName", "decisionText", "registrationDate", "validDate",
#    "decisionDate", "finalGrantDate", "status", "statusOwner", ...}]}
# No pagination -- len(results) == total always, confirmed up to ~4000.
#
# Application-type IDs confirmed live for Islington -- these are Agile's
# OWN ids and are NOT shared across councils on this platform (confirmed
# Sep 3 2026 by live-checking five more Agile councils: the same numeric id
# means a completely different, unrelated application type on each tenant --
# e.g. id 72 is "Tree Works Unspecified" on Islington but "Camp Site
# Temporary Use - Prior Approval" on Yorkshire Dales). So every council
# below carries its OWN "tree_type_ids" tuple, individually verified live
# (fetched each council's /api/application/types list, then confirmed each
# tree-looking id actually returns real tree-work proposals with a
# meaningful total, not a stale/unused type sitting at 0 results):
#   72 = Tree Works Unspecified            (real data)
#   73 = Tree Works Notice Dead/Dangerous  (real data)
#   76 = Works application to tree w/ TPO  (real data, huge volume --
#        needs the registrationDateFrom cutoff to stay a sane page size)
#   74 = Tree Works Notification in Conservation Area -- returns HTTP 400
#        on every attempt, a genuine live API inconsistency, not a bug on
#        our end. Deliberately excluded.
# Comma-separating multiple ids in one request also returns 400 -- one
# request per type id is required, same restriction as Northgate's one-
# search-per-application-type-code.
#
# Sep 3 2026 expansion -- Nick asked specifically whether Redbridge,
# Richmond, and the 4 National Parks (Lake District, Yorkshire Dales,
# Snowdonia, Pembrokeshire Coast) had "a way in that isn't as costly and
# complex as feared." Live-checked all 6: every single one turned out to
# already be on this exact same Agile Applications platform (Redbridge and
# Richmond white-label it on their own council domain -- e.g.
# planning.redbridge.gov.uk -- rather than the agileapplications.co.uk
# domain, but it's genuinely the identical backend, confirmed via the same
# x-client/x-product/x-service headers and planningapi.agileapplications.co.uk
# host). So no new platform work was needed for any of them -- only per-
# council registration below, same as this whole section already does.
#
# Of those 6, two are a genuine dead end and are NOT registered below,
# confirmed rather than assumed: Yorkshire Dales NPA (x-client "YD") and
# Snowdonia/Eryri NPA (x-client "SNOWDONIA") both have a full, real
# /api/application/types list on this same API, but NEITHER lists any
# tree/TPO/woodland application type at all -- checked every type name in
# both lists by eye, and cross-checked with a free-text proposal=tree
# search back to 2020, which only turned up incidental mentions of "trees"
# inside unrelated landscaping proposals, never an actual dedicated tree-
# works application. These two National Park Authorities simply do not
# route tree-preservation-order/conservation-area tree work through their
# planning register the way every other council on this platform does --
# not a scraper limitation, a genuine absence of the underlying data.
AGILE_APPLICATIONS_COUNCILS = {
    "ISLINGTON": {
        "base": "https://planningapi.agileapplications.co.uk",
        "x_client": "IS",
        "tree_type_ids": (72, 73, 76),
    },
    # Redbridge (London) -- white-labelled at planning.redbridge.gov.uk,
    # same backend. 23/24 both high-volume and confirmed real; 100011
    # ("Tree Preservation Order") is a smaller, differently-named type but
    # also confirmed live with real tree-work proposals, so included too.
    "REDBRIDGE": {
        "base": "https://planningapi.agileapplications.co.uk",
        "x_client": "REDBRIDGE",
        "tree_type_ids": (23, 24, 100011),
    },
    # Richmond upon Thames (London) -- white-labelled at
    # planning.richmond.gov.uk. Id 190 ("Works to Trees TPO in Conservation
    # Area") exists in this tenant's type list but returned 0 results live
    # back to 2025-01-01 -- an unused/legacy type, deliberately excluded
    # the same way Islington's dead id 74 is excluded above.
    "RICHMOND UPON THAMES": {
        "base": "https://planningapi.agileapplications.co.uk",
        "x_client": "RICHMONDUPONTHAMES",
        "tree_type_ids": (26, 28),
    },
    # Lake District National Park Authority -- reached via a "Licence
    # Agreement" click-through page on lakedistrict.gov.uk before landing
    # on planning.agileapplications.co.uk/ldnpa; same backend underneath.
    # Ids 100010/100011/100012/106721 exist in this tenant's type list but
    # all returned 0 live results back to 2024-01-01 -- legacy/unused,
    # excluded. 94/98/106723 are all live and real.
    "LAKE DISTRICT": {
        "base": "https://planningapi.agileapplications.co.uk",
        "x_client": "LDNPA",
        "tree_type_ids": (94, 98, 106723),
    },
    # Pembrokeshire Coast National Park Authority (Wales) -- the one Welsh
    # National Park of the two checked that DOES have dedicated tree types.
    "PEMBROKESHIRE COAST": {
        "base": "https://planningapi.agileapplications.co.uk",
        "x_client": "PEMBROKESHIRECOAST",
        "tree_type_ids": (29, 30),
    },
}

# How far back to ask the API for -- keeps id 76 (huge historical volume)
# to a manageable page size. Re-evaluated relative to "today" on every
# call rather than hardcoded to one date, so this stays a rolling window.
_AGILE_LOOKBACK_DAYS = 240

# Agile's API returns ISO-ish datetime strings (e.g. "2026-09-03T00:00:00"),
# a different shape from Idox's human-formatted detail-page text -- kept as
# its own small format list/parser rather than overloading _parse_idox_date
# (which would just silently fail to match and drop the date).
_AGILE_DATE_FORMATS = ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M:%S.%f", "%Y-%m-%d")


def _parse_agile_date(raw: Optional[str]) -> Optional[str]:
    if not raw or not isinstance(raw, str):
        return None
    cleaned = raw.strip()
    for fmt in _AGILE_DATE_FORMATS:
        try:
            return datetime.datetime.strptime(cleaned, fmt).date().isoformat()
        except ValueError:
            continue
    return _parse_idox_date(raw)  # harmless fallback if the API ever sends the other shape


def _agile_registration_date_from() -> str:
    cutoff = datetime.datetime.now() - datetime.timedelta(days=_AGILE_LOOKBACK_DAYS)
    return cutoff.strftime("%Y-%m-%d")


def scrape_agile_applications_council(council_name: str, max_rows: int = 40) -> List[Dict]:
    """Entry point mirroring scrape_northgate_council() above, for the
    Agile Applications Citizen Portal API. Kept as its own registry/function
    (not merged into COUNCIL_REGISTRY or NORTHGATE_COUNCILS) since this is a
    third, architecturally distinct platform -- a direct JSON API call, not
    an HTML form scrape."""
    entry = AGILE_APPLICATIONS_COUNCILS.get(council_name.strip().upper())
    if not entry:
        return []
    base_url = entry["base"]
    headers = {
        "x-client": entry["x_client"],
        "x-product": "CITIZENPORTAL",
        "x-service": "PA",
        # Hedge against a possible Origin/Referer-checking WAF rule --
        # costs nothing if the API doesn't check these, and the real
        # browser request did send both.
        "Origin": "https://planning.agileapplications.co.uk",
        "Referer": "https://planning.agileapplications.co.uk/",
        "Accept": "application/json",
    }
    date_from = _agile_registration_date_from()
    leads: List[Dict] = []
    logger.info(f"[AGILE] Scraping {council_name} directly from {base_url}...")
    for type_id in entry["tree_type_ids"]:
        time.sleep(1)  # be polite between requests to the same council
        search_url = f"{base_url}//api/application/search"
        params = {"applicationTypeId": type_id, "registrationDateFrom": date_from}
        try:
            res = net_utils.smart_get(search_url, headers=headers, params=params, timeout=15)
        except Exception as e:
            logger.debug(f"[AGILE] {base_url} search for type {type_id} failed: {e}")
            continue
        if res.status_code != 200:
            # Type id 74 is a confirmed, permanent 400 on this platform --
            # log at debug, not error, so it doesn't look like a fault.
            logger.debug(f"[AGILE] {base_url} type {type_id} returned HTTP {res.status_code}, skipping.")
            continue
        try:
            data = res.json()
        except Exception as e:
            logger.debug(f"[AGILE] {base_url} type {type_id} returned unparseable JSON: {e}")
            continue

        for item in (data.get("results") or [])[:max_rows]:
            # Only genuinely undecided applications are worth selling as
            # leads -- Agile's own decisionDate/decisionText fields are the
            # signal for "this has already been decided", mirroring the
            # "undecided only" filter every other scraper in this project
            # applies (Idox's status text, Northgate's status dropdown).
            if _looks_like_real_value(item.get("decisionText")) or _looks_like_real_value(item.get("decisionDate")):
                continue
            reference = item.get("reference") or item.get("webReference")
            description = item.get("proposal")
            address = item.get("location")
            if not reference or not description:
                continue

            applicant_name = item.get("applicantSurname") if _looks_like_real_value(item.get("applicantSurname")) else None
            agent_name = item.get("agentName") if _looks_like_real_value(item.get("agentName")) else None
            has_agent = agent_name is not None

            lead = {
                "reference": reference,
                "address": address,
                "description": description,
                # Agile's own application-type ids are already tree-specific
                # by definition (see the id list above) -- hardcoding this
                # avoids a _resolve_vertical() false-negative on a proposal
                # sentence that happens not to contain a tree keyword.
                "vertical": "tree",
                "applicant_name": applicant_name,
                "agent_name": agent_name,
                "has_agent": has_agent,
                "agent_is_tree_surgeon": classify_agent_as_tree_surgeon(agent_name, None),
                "registered_date": _parse_agile_date(item.get("registrationDate")),
            }
            leads.append(lead)
    return leads


# Sep 3 2026: Arcus Global "Arcus BE" (Manchester) -- a fourth planning-
# portal platform. Its front end is a Salesforce Experience Cloud site
# (Aura/Lightning Web Components), which looked, on first inspection,
# architecturally much harder than the other three platforms -- Aura calls
# are normally assumed to need a fresh, per-page-load session/CSRF token.
# Live browser network inspection (capturing the exact outgoing request via
# an XMLHttpRequest.send monkey-patch, then replaying it with fetch(...,
# {credentials: 'omit'}) to strip out cookies/session entirely) found this
# assumption doesn't hold for THIS deployment: it's a genuinely anonymous,
# guest-accessible public register, aura.token is literally the string
# "null" in every real request, and the "fwuid"/"app"/"loaded" values in
# aura.context stayed byte-for-byte identical across every page load and
# every request captured this session -- they're pinned to the org's
# current Salesforce release build, not randomly regenerated per visit.
# That means they CAN be hardcoded here, the same way a third-party API
# version string would be -- they'll only go stale when Manchester's
# Salesforce org next upgrades, at which point every request will start
# failing loudly (caught below, logged, zero leads that run) rather than
# silently returning wrong data, so a stale value is safe, just eventually
# in need of a re-capture using the same browser-network-inspection method.
#
# Two Apex actions do the real work, both called the same way as any other
# Aura action -- POST to {base}/pr/s/sfsites/aura?r=<n>&aura.ApexAction.execute=1
# with a form-encoded body of message/aura.context/aura.pageURI/aura.token:
#   1. arcuscommunity.PR_SearchService.search(request={registerName,
#      searchType: "quick", searchTerm, searchName: "Planning_Applications"})
#      -- a free-text substring search across the register's "Planning
#      Applications" category (there's also a "Tree_Preservation_Orders"
#      category, but that's the TPO ORDERS themselves, historical and
#      often decades old -- not the applications to do tree work, which is
#      what's worth selling as a lead). Confirmed live: searching "Works to
#      trees" (the literal phrase Manchester's own system prefixes onto
#      every genuine tree-work proposal description) returns real, current,
#      undecided applications ("Under Consideration"/"Under Consultation")
#      with real references, addresses, and descriptions. Returns up to a
#      250-record threshold (thresholdHit: true when the cap is hit) sorted
#      newest-first by Valid Date, which suits "give me the newest leads"
#      exactly -- nothing beyond the cap is ever the freshest work anyway.
#   2. arcuscommunity.PublicRegisterViewService.getRecordDetails(recordId,
#      registerName) -- per-record detail fetch, confirmed live to return
#      Applicant Name / Agent Name fields (as a flat list of {label, name,
#      value} dicts grouped into sections, not a fixed table -- read
#      generically by label, same defensive approach as every other
#      scraper in this project).
#
# SAME HONEST CAVEAT AS AGILE APPLICATIONS ABOVE: this could only be
# confirmed to work from inside the browser's own origin. A cloud-bash curl
# attempt at this exact domain from this project's own dev sandbox didn't
# even get a response -- the sandbox's own outbound network policy refused
# the connection outright (not even a 403 from Manchester's server, a
# rejection before the request left this environment). That's now the
# SECOND UK council domain in this session (after Agile Applications) that
# this sandbox's own tooling can't reach at all while a real browser can --
# consistent with this being specific to this dev sandbox's own network
# path, not necessarily true of Render (this project's actual production
# host), but still genuinely UNCONFIRMED until this runs live in
# production and either returns leads or logs errors every run.

# Sep 3 2026: Wiltshire runs the same Arcus BE product as Manchester but on
# a differently-configured path -- confirmed live via the same browser-
# network-inspection method: base host, URL path prefix ("pr3" not "pr"),
# and the search-page slug ("be-register-view" not "register-view") are
# all council-specific, but the aura.context fwuid/app-loaded values came
# back byte-for-byte IDENTICAL to Manchester's. That's a strong signal
# these are pinned to Arcus's shared vendor framework build, not to each
# council's own Salesforce org -- so they're kept as one shared constant
# below rather than re-typed per council, though each new council should
# still verify this live rather than assume it (a mismatch just shows up
# as a non-SUCCESS Aura state, logged and skipped, never a crash).
# Wiltshire's own detail page exposes no Applicant/Agent fields at all
# (confirmed live -- a genuine difference in what this council chose to
# publish, not a bug) -- _arcus_fetch_applicant_and_agent already handles
# that the same way as any other "field just isn't there" case: has_agent
# stays False/unknown rather than erroring.
_ARCUS_SHARED_FWUID = "MzNzN1lSdDZQRXpUcEpsWHBlZGd5UWtVMjdnTGFERUU2S3FfSVdrcU92bkExNC4xOTIuODM4ODYwOA"
_ARCUS_SHARED_APP_LOADED_KEY = "APPLICATION@markup://siteforce:communityApp"
_ARCUS_SHARED_APP_LOADED_VALUE = "1712_xZHiuQoc1HHcvGz4vs6mGA"

ARCUS_COUNCILS = {
    "MANCHESTER": {
        "base": "https://arcusbe.manchester.gov.uk",
        "path_prefix": "pr",
        "register_view_page": "register-view",
        "register_name": "Arcus_BE_Public_Register",
        "fwuid": _ARCUS_SHARED_FWUID,
        "app_loaded_key": _ARCUS_SHARED_APP_LOADED_KEY,
        "app_loaded_value": _ARCUS_SHARED_APP_LOADED_VALUE,
    },
    "WILTSHIRE": {
        "base": "https://development.wiltshire.gov.uk",
        "path_prefix": "pr3",
        "register_view_page": "be-register-view",
        "register_name": "Arcus_BE_Public_Register",
        "fwuid": _ARCUS_SHARED_FWUID,
        "app_loaded_key": _ARCUS_SHARED_APP_LOADED_KEY,
        "app_loaded_value": _ARCUS_SHARED_APP_LOADED_VALUE,
    },
}

_ARCUS_TREE_SEARCH_TERMS = ("Works to trees",)
_ARCUS_UNDECIDED_STATUSES = {"under consideration", "under consultation"}


def _arcus_aura_context(entry: Dict) -> str:
    import json as _json
    return _json.dumps({
        "mode": "PROD",
        "fwuid": entry["fwuid"],
        "app": "siteforce:communityApp",
        "loaded": {entry["app_loaded_key"]: entry["app_loaded_value"]},
        "dn": [],
        "globals": {"srcdoc": True},
        "uad": True,
    })


def _arcus_apex_call(entry: Dict, classname: str, method: str, params: Dict, page_uri: str) -> Optional[Dict]:
    """POSTs one Aura ApexAction.execute call and returns the action's
    returnValue.returnValue, or None on any failure (network error, HTTP
    error, an Aura-level ERROR state, or a response shape that doesn't
    parse) -- every caller treats None as "skip this, don't crash the
    scan", same failure philosophy as every other scraper here."""
    import json as _json
    message = _json.dumps({"actions": [{
        "id": "1;a",
        "descriptor": "aura://ApexActionController/ACTION$execute",
        "callingDescriptor": "UNKNOWN",
        "params": {
            "namespace": "arcuscommunity",
            "classname": classname,
            "method": method,
            "params": params,
            "cacheable": False,
            "isContinuation": False,
        },
    }]})
    payload = {
        "message": message,
        "aura.context": _arcus_aura_context(entry),
        "aura.pageURI": page_uri,
        "aura.token": "null",
    }
    headers = {
        "Content-Type": "application/x-www-form-urlencoded",
        # Hedge against a possible Origin/Referer-checking WAF rule --
        # confirmed unnecessary from inside the browser's own origin, but
        # this call is cross-origin from wherever this scraper actually
        # runs, so send both anyway; costs nothing if unchecked.
        "Origin": entry["base"],
        "Referer": f"{entry['base']}/{entry['path_prefix']}/s/{entry['register_view_page']}",
    }
    url = f"{entry['base']}/{entry['path_prefix']}/s/sfsites/aura?r=1&aura.ApexAction.execute=1"
    try:
        res = net_utils.smart_post(url, headers=headers, data=payload, timeout=15)
    except Exception as e:
        logger.debug(f"[ARCUS] {entry['base']} {classname}.{method} request failed: {e}")
        return None
    if res.status_code != 200:
        logger.debug(f"[ARCUS] {entry['base']} {classname}.{method} returned HTTP {res.status_code}.")
        return None
    try:
        data = res.json()
        action = data["actions"][0]
    except Exception as e:
        logger.debug(f"[ARCUS] {entry['base']} {classname}.{method} returned unparseable JSON: {e}")
        return None
    if action.get("state") != "SUCCESS":
        # A stale fwuid/app hash (Manchester's Salesforce org upgraded
        # since this was captured) shows up here, not as an HTTP error --
        # logged at info so it's actually noticed if it starts happening
        # on every run, unlike the many expected per-request debug skips.
        logger.info(f"[ARCUS] {entry['base']} {classname}.{method} returned non-SUCCESS state -- pinned fwuid/app hash may need re-capturing: {action.get('error')}")
        return None
    return action.get("returnValue", {}).get("returnValue")


def _arcus_fetch_applicant_and_agent(entry: Dict, record_id: str) -> Dict:
    result = {"applicant_name": None, "agent_name": None, "has_agent": False, "agent_is_tree_surgeon": None}
    detail = _arcus_apex_call(
        entry, "PublicRegisterViewService", "getRecordDetails",
        {"recordId": record_id, "registerName": entry["register_name"]},
        f"{entry['base']}/{entry['path_prefix']}/s/detail/{record_id}",
    )
    if not detail:
        return result
    for section in detail.get("sections") or []:
        for field in section.get("fields") or []:
            label = (field.get("primaryLanguageLabel") or field.get("label") or "").strip().lower()
            value = field.get("value")
            if not _looks_like_real_value(value):
                continue
            if label == "applicant":
                result["applicant_name"] = value
            elif label == "agent":
                result["agent_name"] = value
    result["has_agent"] = result["agent_name"] is not None
    result["agent_is_tree_surgeon"] = classify_agent_as_tree_surgeon(result["agent_name"], None)
    return result


def scrape_arcus_council(council_name: str, max_rows: int = 40) -> List[Dict]:
    """Entry point mirroring the other three platforms' scrape_*_council()
    functions, for the Arcus BE "Public Register" Aura API. Own registry
    (ARCUS_COUNCILS), same reasoning as the others: a fourth, architecturally
    distinct platform, not a variant of any already-registered one."""
    entry = ARCUS_COUNCILS.get(council_name.strip().upper())
    if not entry:
        return []
    leads: List[Dict] = []
    seen_ids = set()
    logger.info(f"[ARCUS] Scraping {council_name} directly from {entry['base']}...")
    for term in _ARCUS_TREE_SEARCH_TERMS:
        time.sleep(1)  # be polite between searches on the same council
        result = _arcus_apex_call(
            entry, "PR_SearchService", "search",
            {"request": {"registerName": entry["register_name"], "searchType": "quick",
                         "searchTerm": term, "searchName": "Planning_Applications"}},
            f"/{entry['path_prefix']}/s/{entry['register_view_page']}",
        )
        if not result:
            continue
        for rec in (result.get("records") or [])[:max_rows]:
            record_id = rec.get("Id")
            status = (rec.get("arcusbuiltenv__Status__c") or "").strip().lower()
            reference = rec.get("Name")
            description = rec.get("arcusbuiltenv__Proposal__c")
            address = rec.get("arcusbuiltenv__Site_Address__c")
            valid_date = rec.get("arcusbuiltenv__Valid_Date__c")
            if not record_id or record_id in seen_ids:
                continue
            if not reference or not description:
                continue
            if status not in _ARCUS_UNDECIDED_STATUSES:
                # "Recommendation Made" / "Final" / anything else -- this
                # application has moved past the point of being a genuinely
                # open lead, same "undecided only" filter every other
                # scraper here applies.
                continue
            seen_ids.add(record_id)
            time.sleep(1)  # be polite before the per-record detail fetch too
            lead = {
                "reference": reference,
                "address": address,
                "description": description,
                "vertical": "tree",  # both matched application types are tree-specific by definition
                "registered_date": valid_date if _looks_like_real_value(valid_date) else None,
            }
            lead.update(_arcus_fetch_applicant_and_agent(entry, record_id))
            leads.append(lead)
    return leads


# Sep 3 2026: Hounslow -- confirmed live to be a DIFFERENT, newer Northgate
# product ("NEC Synergy"/"Online Planning") from Wandsworth/Camden's classic
# "PlanningExplorer" -- a modern JS UI, not the ASP.NET WebForms/VIEWSTATE
# app NorthgateScraper above targets. Originally flagged as needing its own
# investigation rather than assuming it fit the existing NorthgateScraper.
# Turned out to be the SIMPLEST of the four+ platforms in this file: a
# plain ASP.NET MVC form POST returning an HTML fragment, no VIEWSTATE, no
# Aura, no session/cookies at all (confirmed live with credentials
# stripped out). The site has a first-class "Works to Trees" search
# category (radio button `SearchFor=WorksToTrees`) that already covers all
# three real tree-work application types in one bucket -- confirmed live:
# "Works to Trees in Conservation Area", "5 Day tree notification", and
# "Works to a Tree covered by a Tree Preservation Order" all came back from
# one search, so no keyword/category juggling is needed the way Manchester/
# Wiltshire/Hounslow's own general search needed one.
#
# Same "serialize the real form, then override just the fields we need"
# approach as NorthgateScraper (its own _serialize_form is VIEWSTATE-
# specific and not reused here -- this form has no VIEWSTATE, just plain
# checkboxes/radios, and a naive full-hidden-field replication was needed
# because a hand-picked minimal field set 500'd -- the server's model
# binder apparently needs the complete field set, not just the ones that
# logically matter).
#
# Search endpoint: POST OnlinePlanningSearchResults with the search page's
# own serialized form, overriding SearchFor=WorksToTrees, Validated=true
# (the "not yet decided" status filter -- Hounslow's own equivalent of
# Northgate's status=NEW or Agile's empty decisionDate), StatusOptions
# (a rolling window -- ReceivedAnyTime hits a "too many results" server-
# side cap, PastMonth does not), SearchInput='' (blank keyword is fine once
# a category+status are set, unlike a keyword-only search).
# Detail endpoint: GET OnlinePlanningOverview?applicationNumber=<ref> --
# confirmed live the "guid" query param results link ALSO includes is not
# required (a bare applicationNumber loads the exact same page), so it's
# not tracked or replicated here at all.
HOUNSLOW_BASE = "https://planningandbuilding.hounslow.gov.uk"
_HOUNSLOW_SEARCH_PATH = "/NECSWS/ES/Presentation/Planning/OnlinePlanning/OnlinePlanningSearch"
_HOUNSLOW_RESULTS_PATH = "/NECSWS/ES/Presentation/Planning/OnlinePlanning/OnlinePlanningSearchResults"
_HOUNSLOW_OVERVIEW_PATH = "/NECSWS/ES/Presentation/Planning/OnlinePlanning/OnlinePlanningOverview"


class HounslowScraper:
    def __init__(self, base_url: str = HOUNSLOW_BASE):
        self.base_url = base_url
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"})

    def _serialize_search_form(self, soup) -> Dict[str, str]:
        """Reads the live search page's hidden fields verbatim and the
        checked state of every checkbox/radio -- a hand-picked minimal
        field set 500'd live (the server's model binder needs the full
        shape), so this replicates the whole form rather than guessing
        which fields matter, same philosophy as NorthgateScraper's own
        _serialize_form just adapted for checkboxes/radios instead of
        VIEWSTATE/selects."""
        data: Dict[str, str] = {}
        for inp in soup.find_all("input"):
            name = inp.get("name")
            if not name:
                continue
            input_type = (inp.get("type") or "text").lower()
            if input_type in ("checkbox", "radio"):
                if inp.get("checked") is not None:
                    data[name] = inp.get("value", "true")
            else:
                data[name] = inp.get("value", "")
        return data

    def _fetch_applicant_and_agent(self, application_number: str) -> Dict:
        result = {"applicant_name": None, "agent_name": None, "has_agent": False,
                   "agent_is_tree_surgeon": None, "registered_date": None}
        url = f"{self.base_url}{_HOUNSLOW_OVERVIEW_PATH}"
        try:
            res = net_utils.smart_get(url, session=self.session, params={"applicationNumber": application_number}, timeout=15)
        except Exception as e:
            logger.debug(f"[HOUNSLOW] detail fetch for {application_number} failed: {e}")
            return result
        if res.status_code != 200:
            return result
        soup = BeautifulSoup(res.text, "html.parser")

        def _label_value(label_text: str) -> Optional[str]:
            for label in soup.find_all("label"):
                if label.get_text(strip=True).lower() == label_text.lower():
                    value_td = label.find_parent("td").find_next_sibling("td")
                    if value_td:
                        value_label = value_td.find("label")
                        text = (value_label or value_td).get_text(strip=True)
                        return text if _looks_like_real_value(text) else None
            return None

        result["applicant_name"] = _label_value("Applicant")
        result["agent_name"] = _label_value("Agent")
        result["has_agent"] = result["agent_name"] is not None
        result["agent_is_tree_surgeon"] = classify_agent_as_tree_surgeon(result["agent_name"], None)
        result["registered_date"] = _parse_idox_date(_label_value("Validated") or _label_value("Received"))
        return result

    def search_tree_applications(self, max_rows: int = 40) -> List[Dict]:
        leads: List[Dict] = []
        try:
            res = net_utils.smart_get(f"{self.base_url}{_HOUNSLOW_SEARCH_PATH}", session=self.session, timeout=15)
        except Exception as e:
            logger.error(f"[HOUNSLOW] Could not load search page: {e}")
            return leads
        soup = BeautifulSoup(res.text, "html.parser")
        payload = self._serialize_search_form(soup)
        payload.update({
            "SearchFor": "WorksToTrees",
            "Validated": "true",
            "StatusOptions": "PastMonth",  # a rolling window -- ReceivedAnyTime hits a server-side result cap
            "SearchInput": "",
        })
        try:
            res = net_utils.smart_post(
                f"{self.base_url}{_HOUNSLOW_RESULTS_PATH}", session=self.session,
                headers={"X-Requested-With": "XMLHttpRequest"}, data=payload, timeout=15,
            )
        except Exception as e:
            logger.error(f"[HOUNSLOW] Search request failed: {e}")
            return leads
        if res.status_code != 200:
            logger.debug(f"[HOUNSLOW] Search returned HTTP {res.status_code}.")
            return leads

        results_soup = BeautifulSoup(res.text, "html.parser")
        # The whole fragment is wrapped in one containing <div> with every
        # result's own "row" div as its direct child (plus the "N Results"
        # summary row, and later a nested "row padding-left-15" INSIDE each
        # result for its status -- recursive=False on the outer container,
        # not on the soup root itself, is what keeps those nested status
        # rows from being mistaken for top-level result rows).
        container = results_soup.find("div")
        rows = container.find_all("div", class_="row", recursive=False) if container else []
        for row in rows[:max_rows + 1]:  # +1 -- row[0] is just the "N Results" summary, not a record
            cells = row.find_all("div", class_="col-xs-12", recursive=False)
            if len(cells) < 4:
                continue  # the summary row, or a layout this hasn't seen before -- skip, don't guess
            addr_link = cells[0].find("a", href=re.compile(r"applicationNumber="))
            if not addr_link:
                continue
            ref_match = re.search(r"applicationNumber=([^&]+)", addr_link["href"])
            if not ref_match:
                continue
            reference = unquote(ref_match.group(1))
            addr_span = addr_link.find("span")
            address = addr_span.get_text(strip=True) if addr_span else None

            type_span = cells[1].find("span")
            application_type = type_span.get_text(strip=True) if type_span else ""

            desc_span = cells[2].find("span")
            description = (desc_span.get("title") or desc_span.get_text(strip=True)) if desc_span else None
            if not description:
                continue

            vertical = "tree"  # WorksToTrees is a dedicated category -- every result here is tree work by definition
            lead = {
                "reference": reference,
                "address": address,
                "description": description.strip(),
                "vertical": vertical,
            }
            time.sleep(1)  # be polite before the per-record detail fetch
            lead.update(self._fetch_applicant_and_agent(reference))
            leads.append(lead)
        return leads


def scrape_hounslow_council(council_name: str = "HOUNSLOW") -> List[Dict]:
    """Entry point mirroring the other platforms' scrape_*_council()
    functions. Hounslow is the only council on this particular NEC Online
    Planning product registered so far -- kept as its own function (not a
    dict-keyed registry like the others) since there's exactly one, but
    still named/shaped the same way so scanners.py's calling code doesn't
    need a special case."""
    if council_name.strip().upper() != "HOUNSLOW":
        return []
    logger.info(f"[HOUNSLOW] Scraping Hounslow directly from {HOUNSLOW_BASE}...")
    scraper = HounslowScraper()
    try:
        return scraper.search_tree_applications()
    except Exception as e:
        logger.error(f"[HOUNSLOW] Scrape failed: {e}")
        return []


# Sep 3 2026: North York Moors NPA -- confirmed live to run a StatMap
# product suite. The embedded map widget on the council's own site
# ("Aurora") turned out to be a dead end for lead-gen use -- its search
# results carry only a description and X/Y coordinates, no reference
# number, status, or date, so there's no reliable way to tell a genuinely
# open application from one decided decades ago through that interface
# alone. Its "More Info" popup links, though, pointed at a SEPARATE,
# much more capable system: a full public register ("HorizoNext Public
# Portal") with proper Reference/Status/Application-Type/date-range
# search -- this is the one actually used below, not Aurora.
#
# Search endpoint: POST .../horizoNext/api/publicportal/planningApplications/
# pageRequest -- a plain JSON REST API (confirmed live with credentials
# stripped out, no cookies/session needed), filtering by exact appType text
# plus status="Live" (confirmed live: "Live" holds genuinely undecided
# cases with decision:"PENDING"/decisionDate:null; "Received" returned
# zero in testing -- not a real status value currently in use here, kept
# out rather than guessed at). Three tree-related appType strings
# confirmed to exist in the site's own autocomplete list; only two
# ("Conservation Areas (CA)" plural, and "TPO") had any live results at
# capture time, but the third is kept since a type having zero results
# today doesn't mean it always will.
# Detail endpoint: GET .../horizoNext/api/publicportal/planningApplications/
# <id> -- confirmed live to return applicantFullName/agentFullName/
# agentCompany directly (also credential-free), a much richer record than
# the search list alone provides.
NORTH_YORK_MOORS_BASE = "https://northyorkmoors-publicportal.statmap.co.uk"
_NYM_SEARCH_PATH = "/horizoNext/api/publicportal/planningApplications/pageRequest"
_NYM_DETAIL_PATH = "/horizoNext/api/publicportal/planningApplications"
_NYM_TREE_APP_TYPES = (
    "Works to Trees in Conservation Areas (CA)",
    "Works to Trees subject to a Preservation Order (TPO)",
    "Works to Trees in Conservation Area",
)
_NYM_DATE_FORMATS = ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M:%S.%f")


def _parse_nym_date(raw: Optional[str]) -> Optional[str]:
    if not raw or not isinstance(raw, str):
        return None
    for fmt in _NYM_DATE_FORMATS:
        try:
            return datetime.datetime.strptime(raw.strip(), fmt).date().isoformat()
        except ValueError:
            continue
    return None


def _nym_fetch_applicant_and_agent(record_id) -> Dict:
    result = {"applicant_name": None, "agent_name": None, "agent_company": None,
              "has_agent": False, "agent_is_tree_surgeon": None}
    url = f"{NORTH_YORK_MOORS_BASE}{_NYM_DETAIL_PATH}/{record_id}"
    try:
        res = net_utils.smart_get(url, timeout=15)
    except Exception as e:
        logger.debug(f"[NYM] Detail fetch for {record_id} failed: {e}")
        return result
    if res.status_code != 200:
        return result
    try:
        data = res.json()
    except Exception as e:
        logger.debug(f"[NYM] Detail fetch for {record_id} returned unparseable JSON: {e}")
        return result
    applicant = data.get("applicantFullName")
    agent = data.get("agentFullName")
    agent_company = data.get("agentCompany")
    result["applicant_name"] = applicant if _looks_like_real_value(applicant) else None
    result["agent_name"] = agent if _looks_like_real_value(agent) else None
    result["agent_company"] = agent_company if _looks_like_real_value(agent_company) else None
    result["has_agent"] = result["agent_name"] is not None or result["agent_company"] is not None
    result["agent_is_tree_surgeon"] = classify_agent_as_tree_surgeon(result["agent_name"], result["agent_company"])
    return result


def scrape_north_york_moors(max_rows: int = 40) -> List[Dict]:
    """Entry point mirroring the other platforms' scrape_*_council()
    functions -- no council_name argument since (like Hounslow) there's
    exactly one authority on this particular platform registered so far."""
    leads: List[Dict] = []
    seen_ids = set()
    logger.info(f"[NYM] Scraping North York Moors directly from {NORTH_YORK_MOORS_BASE}...")
    for app_type in _NYM_TREE_APP_TYPES:
        time.sleep(1)  # be polite between searches on the same authority
        payload = {
            "select": {"name": True, "initialAppRef": True, "address": True, "proposal": True,
                       "receivedDate": True, "status": True, "decision": True, "id": True},
            "pageSize": max_rows,
            "offset": 0,
            "filter": {"parts": [{"filterItems": [
                {"columnName": "appType", "value": app_type, "operator": "="},
                {"columnName": "status", "value": "Live", "operator": "="},
                {"columnName": "searchType", "value": "Advanced", "operator": "="},
            ]}]},
            "order": {},
            "advancedFilter": {"parts": []},
            "entityName": "P_APPLICATION",
        }
        try:
            res = net_utils.smart_post(
                f"{NORTH_YORK_MOORS_BASE}{_NYM_SEARCH_PATH}",
                headers={"Content-Type": "application/json"}, json=payload, timeout=15,
            )
        except Exception as e:
            logger.debug(f"[NYM] Search for '{app_type}' failed: {e}")
            continue
        if res.status_code != 200:
            logger.debug(f"[NYM] Search for '{app_type}' returned HTTP {res.status_code}.")
            continue
        try:
            data = res.json()
        except Exception as e:
            logger.debug(f"[NYM] Search for '{app_type}' returned unparseable JSON: {e}")
            continue

        for rec in (data.get("records") or []):
            record_id = rec.get("id")
            reference = rec.get("name")
            description = rec.get("proposal")
            address = rec.get("address")
            if not record_id or record_id in seen_ids:
                continue
            if not reference or not description:
                continue
            seen_ids.add(record_id)
            time.sleep(1)  # be polite before the per-record detail fetch too
            lead = {
                "reference": reference,
                "address": address,
                "description": description,
                "vertical": "tree",  # every _NYM_TREE_APP_TYPES entry is tree-specific by definition
                "registered_date": _parse_nym_date(rec.get("receivedDate")),
            }
            lead.update(_nym_fetch_applicant_and_agent(record_id))
            leads.append(lead)
    return leads


# Sep 3 2026: Havering (London) -- the first of Nick's 8 "bespoke one-off"
# councils, and a SEVENTH distinct platform: "Civica" (msp.havering.gov.uk
# -- scripts literally named civica.loader.js/civica.forms.js). Reached via
# merton.gov.uk's own /planningexplorer redirect page -- Merton itself
# turned out to run a DIFFERENT bespoke system ("Regulatory Services Hub")
# that's protected by an AWS WAF bot-detection JS challenge (confirmed live:
# the first request comes back HTTP 202 with a WAF challenge token exchange
# before the real 200 page loads). Bypassing or completing bot-detection is
# off-limits, so Merton is NOT built and is not registered anywhere --
# flagged to Nick as a genuine block, not attempted.
#
# Havering itself has no such protection. Live browser network capture
# (submitting the real search form, then replaying with plain fetch()) found
# a clean, anonymous JSON API:
#   POST {base}/civica/Resource/Civica/Handler.ashx/keyobject/pagedsearch
#   body: {"refType": "PLANNINGCASE", "fromRow": 1, "toRow": N,
#          "searchFields": {"DateRcvdFrom": "DD/MM/YYYY",
#                            "DateRcvdTo": "DD/MM/YYYY"},
#          "NoTotalRows": true}
# No cookies, no session, no CSRF token -- confirmed by calling it fresh via
# fetch() with no prior page state.
#
# Response shape: {"KeyObjects": [{"Items": [{"FieldName", "Label", "Value",
# "DataType"}, ...]}, ...]}. Deliberately NOT a flat dict per record --
# reshaped into one below for convenience. Includes DIRECT applicant AND
# agent phone/email in every record (ApplicantEmail/ApplicantTelephone/
# AgentEmail/AgentTelephone) -- confirmed live with real values -- so unlike
# every other platform in this file, Havering leads need no separate
# enrichment step at all.
#
# IMPORTANT, confirmed live rather than assumed:
#   - The search form's own UI exposes no "Application Type" filter field at
#     all (checked the live page's inputs) -- but the JSON response DOES
#     carry a "Type" field per record, with real values including "TREE WORK
#     APP TPO" and "TREE WORK APP IN CA" (confirmed by pulling a full
#     unfiltered month and inspecting every distinct Type value that came
#     back). Adding an unsupported "Type" key into `searchFields` is silently
#     ignored by the server (confirmed: still returns the same unfiltered
#     mix) -- so type filtering has to happen client-side, after fetching a
#     full date-range page, not server-side.
#   - toRow:1000 in one request comes back as an HTML error page instead of
#     JSON (confirmed live) -- toRow:300 is confirmed reliable and safely
#     covers this council's real monthly volume (a live full-month pull
#     returned 271 total rows, under the 300 cap). Paging by calendar month
#     rather than a single big date range keeps every request under that
#     cap without needing real pagination logic.
#   - "Decision" is an empty string "" on every genuinely undecided
#     application (confirmed live against August 2026's still-open TPO
#     applications) and a real value ("TREE WORK APP + COND", "TREE WORK
#     REFUSED", etc.) once decided -- same "undecided means blank" pattern
#     every other scraper in this project already uses.
HAVERING_BASE = "https://msp.havering.gov.uk"
_HAVERING_SEARCH_PATH = "/civica/Resource/Civica/Handler.ashx/keyobject/pagedsearch"
_HAVERING_TREE_TYPES = {"TREE WORK APP TPO", "TREE WORK APP IN CA"}
_HAVERING_LOOKBACK_MONTHS = 8  # roughly matches other platforms' ~240-day rolling window
_HAVERING_ROWS_PER_MONTH = 300  # confirmed safe live; 1000 in one request errors instead of returning JSON


def _havering_month_windows() -> List[Tuple[datetime.date, datetime.date]]:
    """The last _HAVERING_LOOKBACK_MONTHS calendar months (including the
    current, partial one), oldest first isn't required -- order doesn't
    matter since every window is queried independently and leads are
    simply concatenated."""
    windows = []
    year, month = datetime.date.today().year, datetime.date.today().month
    for _ in range(_HAVERING_LOOKBACK_MONTHS):
        first_day = datetime.date(year, month, 1)
        next_month_first = datetime.date(year + 1, 1, 1) if month == 12 else datetime.date(year, month + 1, 1)
        last_day = next_month_first - datetime.timedelta(days=1)
        windows.append((first_day, last_day))
        month -= 1
        if month == 0:
            month = 12
            year -= 1
    return windows


def _parse_havering_date(raw: Optional[str]) -> Optional[str]:
    if not raw or not isinstance(raw, str):
        return None
    try:
        return datetime.datetime.strptime(raw.strip(), "%d/%m/%Y").date().isoformat()
    except ValueError:
        return None


def scrape_havering_council() -> List[Dict]:
    """Entry point for Havering's Civica platform. Single council, no
    registry -- same pattern as scrape_hounslow_council() and
    scrape_north_york_moors() above."""
    headers = {
        "Content-Type": "application/json; charset=utf-8",
        "Accept": "application/json, text/javascript, */*; q=0.01",
        "X-Requested-With": "XMLHttpRequest",
    }
    leads: List[Dict] = []
    logger.info(f"[HAVERING] Scraping Havering directly from {HAVERING_BASE}...")
    for first_day, last_day in _havering_month_windows():
        time.sleep(1)  # be polite between months
        payload = {
            "refType": "PLANNINGCASE",
            "fromRow": 1,
            "toRow": _HAVERING_ROWS_PER_MONTH,
            "searchFields": {
                "DateRcvdFrom": first_day.strftime("%d/%m/%Y"),
                "DateRcvdTo": last_day.strftime("%d/%m/%Y"),
            },
            "NoTotalRows": True,
        }
        try:
            res = net_utils.smart_post(f"{HAVERING_BASE}{_HAVERING_SEARCH_PATH}", headers=headers, json=payload, timeout=15)
        except Exception as e:
            logger.debug(f"[HAVERING] Search for {first_day}-{last_day} failed: {e}")
            continue
        if res.status_code != 200:
            logger.debug(f"[HAVERING] Search for {first_day}-{last_day} returned HTTP {res.status_code}.")
            continue
        try:
            data = res.json()
        except Exception as e:
            logger.debug(f"[HAVERING] Search for {first_day}-{last_day} returned unparseable JSON: {e}")
            continue

        key_objects = data.get("KeyObjects") or []
        if len(key_objects) >= _HAVERING_ROWS_PER_MONTH:
            # Confirmed live this council's real monthly volume sits well
            # under this cap -- if it's ever actually hit, that month is
            # silently truncated rather than erroring, worth knowing about.
            logger.info(f"[HAVERING] {first_day}-{last_day} hit the {_HAVERING_ROWS_PER_MONTH}-row cap -- some records this month may be missing.")

        for key_object in key_objects:
            by_field = {item.get("FieldName"): item.get("Value") for item in (key_object.get("Items") or [])}
            if by_field.get("Type") not in _HAVERING_TREE_TYPES:
                continue
            if _looks_like_real_value(by_field.get("Decision")):
                continue  # already decided -- only genuinely undecided leads are worth selling
            reference = by_field.get("KeyNo")
            description = by_field.get("DevelopmentDescription")
            if not reference or not description:
                continue

            address = ", ".join(
                p for p in (by_field.get("LocationLine1"), by_field.get("LocationLine2"), by_field.get("LocationPostcode"))
                if _looks_like_real_value(p)
            ) or None
            applicant_name = by_field.get("ApplicantName") if _looks_like_real_value(by_field.get("ApplicantName")) else None
            agent_name = by_field.get("AgentName") if _looks_like_real_value(by_field.get("AgentName")) else None

            leads.append({
                "reference": reference,
                "address": address,
                "description": description,
                "vertical": "tree",  # both registered Types are tree-specific by definition
                "applicant_name": applicant_name,
                "agent_name": agent_name,
                "has_agent": agent_name is not None,
                "agent_is_tree_surgeon": classify_agent_as_tree_surgeon(agent_name, None),
                "registered_date": _parse_havering_date(by_field.get("DateRcvd")),
            })
    return leads


# Sep 3 2026: St Albans (Herts) -- second of Nick's 8 bespoke one-offs, and
# a variant of the SAME Civica platform as Havering ("Portal360" branding,
# civica.common.js/civica.forms.js again) -- but genuinely different enough
# underneath to need its own registration, not reuse of Havering's function:
# a different handler path (/w2webparts/... not /civica/...), a different
# refType ("PBDC" not "PLANNINGCASE"), snake_case field names instead of
# PascalCase, and critically -- confirmed live -- server-side filtering on
# BOTH application type AND received date actually works here (Havering's
# server silently ignores a "Type" search field; St Albans' genuinely
# narrows results, confirmed by comparing TotalRows with/without each
# filter). This page also loads a Barracuda WAF script (cdn.infisecure.com/
# barracuda.js) -- unlike Merton's AWS WAF, this one did NOT intervene on a
# plain fetch() call (no challenge page, straight 200 with real JSON),
# confirmed by calling the endpoint fresh with no prior page state.
#
#   POST {base}/w2webparts/Resource/Civica/Handler.ashx/keyobject/pagedsearch
#   body: {"refType": "PBDC", "fromRow": 1, "toRow": N,
#          "searchFields": {"app_type": "TPO"|"TCA",
#                            "received_dateFrom": "DD/MM/YYYY"},
#          "NoTotalRows": true}
#
# Application-type short codes (confirmed live via the real UI's Application
# Type dropdown, which submits invisible internal codes, not the visible
# label text -- the <option> elements' own `value` attributes are all
# blank, so these were only discoverable by actually selecting each option
# and reading the real outgoing request):
#   TPO = "Tree works to TPO trees(s)"
#   TCA = "Tree works in conservation area"
# ("Planning Portal Tree Submission", a third tree-adjacent option in the
# same dropdown, was not separately verified -- the two confirmed types
# already cover TPO and conservation-area tree work, which is the same
# pairing every other platform in this project targets.)
#
# "decision_notice_type" is blank or literally "PENDING" on every
# genuinely undecided application (confirmed live: 31 PENDING vs 199
# "Treeworks approval" + other decided values, in one real TPO pull) --
# same "undecided means blank/absent" pattern as everywhere else in this
# file, just written as an explicit allow-list here since "PENDING" is
# itself a non-empty string _looks_like_real_value would otherwise treat
# as "decided".
ST_ALBANS_BASE = "https://planningapplications.stalbans.gov.uk"
_ST_ALBANS_SEARCH_PATH = "/w2webparts/Resource/Civica/Handler.ashx/keyobject/pagedsearch"
_ST_ALBANS_TREE_APP_TYPES = ("TPO", "TCA")
_ST_ALBANS_LOOKBACK_DAYS = 240  # same rolling-window convention as Agile Applications
_ST_ALBANS_UNDECIDED_MARKERS = {"", "PENDING"}


def _st_albans_received_date_from() -> str:
    cutoff = datetime.datetime.now() - datetime.timedelta(days=_ST_ALBANS_LOOKBACK_DAYS)
    return cutoff.strftime("%d/%m/%Y")


def _parse_st_albans_date(raw: Optional[str]) -> Optional[str]:
    if not raw or not isinstance(raw, str):
        return None
    try:
        return datetime.datetime.strptime(raw.strip(), "%d/%m/%Y").date().isoformat()
    except ValueError:
        return None


def scrape_st_albans_council() -> List[Dict]:
    """Entry point for St Albans' Portal360/Civica platform. Single
    council, no registry -- same pattern as scrape_havering_council()."""
    headers = {
        "Content-Type": "application/json; charset=utf-8",
        "Accept": "application/json, text/javascript, */*; q=0.01",
        "X-Requested-With": "XMLHttpRequest",
    }
    date_from = _st_albans_received_date_from()
    leads: List[Dict] = []
    logger.info(f"[ST ALBANS] Scraping St Albans directly from {ST_ALBANS_BASE}...")
    for app_type in _ST_ALBANS_TREE_APP_TYPES:
        time.sleep(1)  # be polite between the two type queries
        payload = {
            "refType": "PBDC",
            "fromRow": 1,
            "toRow": 300,  # confirmed live: comfortably covers this council's real per-type volume
            "searchFields": {"app_type": app_type, "received_dateFrom": date_from},
            "NoTotalRows": True,
        }
        try:
            res = net_utils.smart_post(f"{ST_ALBANS_BASE}{_ST_ALBANS_SEARCH_PATH}", headers=headers, json=payload, timeout=15)
        except Exception as e:
            logger.debug(f"[ST ALBANS] Search for type {app_type} failed: {e}")
            continue
        if res.status_code != 200:
            logger.debug(f"[ST ALBANS] Search for type {app_type} returned HTTP {res.status_code}.")
            continue
        try:
            data = res.json()
        except Exception as e:
            logger.debug(f"[ST ALBANS] Search for type {app_type} returned unparseable JSON: {e}")
            continue

        for key_object in (data.get("KeyObjects") or []):
            by_field = {item.get("FieldName"): item.get("Value") for item in (key_object.get("Items") or [])}
            if by_field.get("decision_notice_type", "") not in _ST_ALBANS_UNDECIDED_MARKERS:
                continue  # already decided
            reference = by_field.get("ref_no")
            description = by_field.get("proposal")
            if not reference or not description:
                continue

            address = by_field.get("application_address") or by_field.get("UPRNDisplay") or None
            applicant_name = by_field.get("ApplicantContactNoName") if _looks_like_real_value(by_field.get("ApplicantContactNoName")) else None
            agent_name = by_field.get("AgentContactNoName") if _looks_like_real_value(by_field.get("AgentContactNoName")) else None

            leads.append({
                "reference": reference,
                "address": address,
                "description": description,
                "vertical": "tree",  # both registered app_type codes are tree-specific by definition
                "applicant_name": applicant_name,
                "agent_name": agent_name,
                "has_agent": agent_name is not None,
                "agent_is_tree_surgeon": classify_agent_as_tree_surgeon(agent_name, None),
                "registered_date": _parse_st_albans_date(by_field.get("received_date")),
            })
    return leads


# --- Kensington & Chelsea (RBKC) -- bespoke "RBKC Planning Portal" SPA ---
# Sep 3 2026: live-reverse-engineered via browser network capture against
# rbkc.gov.uk/planningsearch. This is a genuinely custom-built platform (not
# Idox/Northgate/Civica/Agile/Arcus) with two distinct APIs:
#
# 1. A lightweight LIST endpoint (`/planningsearch/api/cases/everywhere`)
#    that returns every matching case as a bespoke length-prefixed BINARY
#    blob, not JSON -- each field is a little-endian uint32 byte-length
#    immediately followed by that many UTF-8 bytes, interleaved with binary
#    numeric fields (doubles for lat/lng, an int32 case id, etc). Confirmed
#    live via a byte-level scan: treating any 4-byte LE uint32 N where the
#    following N bytes are all printable ASCII as a string field reliably
#    recovers every reference/address/description in the response. This
#    list endpoint carries NO applicant/agent details at all.
# 2. A per-case DETAIL endpoint (`/planningsearch/_server/...getCaseQuery...`)
#    that DOES carry the applicant name, confirmed live by clicking into a
#    real result. Its response is not JSON either -- it's the "seroval"
#    serialization format used by this SPA's framework: a JS expression
#    literal (`$R[0]={applicantName:"...", hasDecision:!1, ...}`) rather
#    than `{"applicantName": "..."}`. Parsed here with targeted regexes
#    rather than a JSON parser.
#
# `planningType=2` was confirmed live (via a `caseCounts` brute-force sweep
# of every power-of-two bit 1..65536, cross-checked against the live UI's
# own "ARB (Tree applications)" checkbox) to be the exact bitmask value for
# tree/ARB-prefixed applications -- a same-date-range live pull returned
# 933/933 ARB-prefixed references at that single bit value with zero noise.
# `caseStatus=1` (of the platform's own 5-bit Awaiting/Granted/Refused/
# Split/Withdrawn status mask, base 31=all) isolates still-open cases,
# confirmed to return only "Open"-status records live (80 of 80 in one
# real pull) -- this makes an explicit received-date lookback window
# unnecessary (unlike every Civica-family platform above): we only ever
# ask for what's still awaiting a decision, and downstream _insert_lead()
# dedups by reference the same way every other platform here does.
RBKC_BASE = "https://www.rbkc.gov.uk"
_RBKC_LIST_PATH = "/planningsearch/api/cases/everywhere"
_RBKC_DETAIL_PATH = "/planningsearch/_server/"
_RBKC_TREE_PLANNING_TYPE = 2  # confirmed live: "ARB (Tree applications)"
_RBKC_AWAITING_CASE_STATUS = 1  # confirmed live: "Awaiting" (undecided) only
_RBKC_ALL_CASE_TYPES = 7  # Application(1) + Decision(2) + Appeal(4), all three
_RBKC_REFERENCE_RE = re.compile(r"^[A-Z]{2,4}/\d{2}/\d+$")
_RBKC_LOOKBACK_YEARS = 5  # generous floor -- caseStatus=1 already limits to still-open cases


def _rbkc_date_from_floor_ms() -> int:
    floor_date = datetime.date.today() - datetime.timedelta(days=365 * _RBKC_LOOKBACK_YEARS)
    return int(datetime.datetime(floor_date.year, floor_date.month, floor_date.day).timestamp() * 1000)


def _decode_rbkc_binary_strings(data: bytes) -> List[str]:
    """Mirrors the live-tested JS byte scanner exactly: walk the buffer,
    and at every offset where the next 4 bytes (little-endian uint32) are a
    plausible string length AND every one of that many following bytes is
    printable ASCII, treat it as a length-prefixed UTF-8 string field and
    skip past it; otherwise advance one byte and keep scanning. Confirmed
    live to recover every reference/address/description with zero noise."""
    out: List[str] = []
    i = 0
    n = len(data)
    while i < n - 4:
        length = int.from_bytes(data[i:i + 4], "little")
        if 0 < length < 2000 and i + 4 + length <= n:
            chunk = data[i + 4:i + 4 + length]
            if all(32 <= b < 127 for b in chunk):
                out.append(chunk.decode("ascii", errors="strict"))
                i += 4 + length
                continue
        i += 1
    return out


def _rbkc_undecided_tree_references() -> List[str]:
    params = {
        "caseStatus": _RBKC_AWAITING_CASE_STATUS,
        "caseType": _RBKC_ALL_CASE_TYPES,
        "planningType": _RBKC_TREE_PLANNING_TYPE,
        "dateFrom": _rbkc_date_from_floor_ms(),
        "sort": 1,
    }
    try:
        res = net_utils.smart_get(f"{RBKC_BASE}{_RBKC_LIST_PATH}", params=params, timeout=15)
    except Exception as e:
        logger.debug(f"[RBKC] Case list request failed: {e}")
        return []
    if res.status_code != 200:
        logger.debug(f"[RBKC] Case list returned HTTP {res.status_code}.")
        return []
    try:
        strings = _decode_rbkc_binary_strings(res.content)
    except Exception as e:
        logger.debug(f"[RBKC] Case list response could not be decoded: {e}")
        return []
    references = [s for s in strings if _RBKC_REFERENCE_RE.match(s)]
    # dict.fromkeys preserves order while de-duping -- the binary format
    # confirmed live to repeat a reference string once per status-history
    # entry in some cases.
    return list(dict.fromkeys(references))


def _rbkc_case_detail_url(reference: str) -> str:
    import json as _json
    args = {"t": {"t": 9, "i": 0, "l": 1, "a": [{"t": 1, "s": reference}], "o": 0}, "f": 31, "m": []}
    query = (
        "id=src_data_getCaseQuery_ts--getCaseQuery_query"
        "&name=%2Fapp%2Fsrc%2Fdata%2FgetCaseQuery.ts%3Ftsr-directive-use-server%3D"
        f"&args={requests.utils.quote(_json.dumps(args, separators=(',', ':')))}"
    )
    return f"{RBKC_BASE}{_RBKC_DETAIL_PATH}?{query}"


def _rbkc_extract_field(text: str, field: str) -> Optional[str]:
    match = re.search(field + r':"((?:[^"\\]|\\.)*)"', text)
    if not match:
        return None
    return match.group(1).replace('\\"', '"')


def _rbkc_has_decision(text: str) -> bool:
    # seroval's minified boolean literals: !0 is JS shorthand for `true`,
    # !1 for `false` -- confirmed live against both a pending ("hasDecision:
    # !1") and a decided ("hasDecision:!0") real case detail response.
    match = re.search(r"hasDecision:(!0|!1)", text)
    return match is not None and match.group(1) == "!0"


def _rbkc_case_detail(reference: str) -> Optional[Dict]:
    try:
        res = net_utils.smart_get(_rbkc_case_detail_url(reference), timeout=15)
    except Exception as e:
        logger.debug(f"[RBKC] Detail request for {reference} failed: {e}")
        return None
    if res.status_code != 200:
        logger.debug(f"[RBKC] Detail request for {reference} returned HTTP {res.status_code}.")
        return None
    text = res.text
    if _rbkc_has_decision(text):
        return None  # already decided -- caseStatus=1 should already exclude this, extra safety net
    description = _rbkc_extract_field(text, "descriptionFull") or _rbkc_extract_field(text, "descriptionShort")
    address = _rbkc_extract_field(text, "address")
    if not description or not address:
        return None
    applicant_name = _rbkc_extract_field(text, "applicantName")
    if not _looks_like_real_value(applicant_name):
        applicant_name = _rbkc_extract_field(text, "applicantCompanyName")
    if not _looks_like_real_value(applicant_name):
        applicant_name = None
    date_match = re.search(r'dateReceived:\$R\[\d+\]=new Date\("(\d{4}-\d{2}-\d{2})', text)
    registered_date = date_match.group(1) if date_match else None
    return {
        "reference": reference,
        "address": address,
        "description": description,
        "vertical": "tree",  # planningType=2 (ARB) is tree-specific by definition
        "applicant_name": applicant_name,
        # RBKC's own system does not distinguish applicant from agent --
        # there is no separate agent field anywhere in the case detail
        # response (confirmed live) -- so every RBKC lead is agent-less by
        # construction, same convention as any other single-name platform.
        "agent_name": None,
        "has_agent": False,
        "agent_is_tree_surgeon": False,
        "registered_date": registered_date,
    }


def scrape_kensington_chelsea_council() -> List[Dict]:
    """Entry point for the Royal Borough of Kensington and Chelsea's
    bespoke planning portal. Single council, no registry -- same pattern
    as scrape_havering_council()/scrape_st_albans_council() above, but a
    two-step list-then-detail fetch since this platform's list endpoint
    carries no applicant information at all."""
    logger.info(f"[RBKC] Scraping Kensington & Chelsea directly from {RBKC_BASE}...")
    references = _rbkc_undecided_tree_references()
    leads: List[Dict] = []
    for reference in references:
        time.sleep(1)  # be polite between per-case detail requests
        lead = _rbkc_case_detail(reference)
        if lead is not None:
            leads.append(lead)
    return leads


# --- Dorset -- legacy "dorsetforyou.com" ASP.NET WebForms portal ---
# Sep 3 2026: live-reverse-engineered via browser network capture against
# planning.dorsetcouncil.gov.uk. This was already flagged earlier this
# session (see the removed-entries comment above COUNCIL_REGISTRY) as a
# bespoke ASP.NET webforms app, NOT Idox (Idox always uses Java ".do"
# servlet endpoints, never ".aspx") -- confirmed again here in full.
#
# Three-step flow, all classic ASP.NET WebForms postbacks (every response
# carries its own __VIEWSTATE/__VIEWSTATEGENERATOR/__EVENTVALIDATION that
# MUST be read out and replayed on the next request, same discipline as
# NorthgateScraper's _serialize_form() above -- a fresh helper is used here
# rather than sharing that one, since this section isn't part of that class
# and the two portals' form field names don't overlap at all):
#   1. GET "/" -> a disclaimer.aspx gate (confirmed live: every fresh
#      session lands here, no way to skip it) -- POST its own
#      "ctl00$ContentPlaceHolder1$btnAccept" button to get past it.
#   2. GET advsearch.aspx -> its "Application Type" dropdown
#      (ddlApplicationType) is confirmed live to be genuinely populated
#      (unlike Bath & NE Somerset's broken equivalent -- see COUNCIL_TO_
#      REGION's own comment on that) with real tree-specific codes: TRT
#      ("Tree Works- TPO"), TRC ("Tree Works- Conservation Area"), TRD
#      ("Tree Dead Or Dangerous") are genuine tree-work leads; TCO ("Tree
#      Works - Consultations") and TST ("Tree Works - Statutory
#      Undertakers") are deliberately excluded -- the former looks like an
#      internal consultation-only record type, the latter is utility-
#      company work, neither a residential/commercial lead. Checking
#      "chkOutstanding" (View outstanding applications only) and POSTing
#      cross-page to searchresults.aspx (the form's own PostBackUrl,
#      confirmed live) returns only undecided applications.
#   3. Each result renders as a `<div class="emphasise-area">` block (ref/
#      location/proposal/blank decision fields) linking to
#      `plandisp.aspx?recno=<id>` for the real Applicant/Agent -- confirmed
#      live to render as `<span class="applabel">Label</span><p
#      class="appdata">Value</p>` pairs. Dorset's own system copies the
#      Applicant straight into the Agent field verbatim when nobody
#      actually represents the case (confirmed live on a real homeowner-
#      submitted TRT case) -- has_agent here is therefore "Agent is a real
#      value AND differs from Applicant", not just "Agent is non-blank".
#   Pagination (RadDataPager postback links, e.g. "ctl00$ContentPlaceHolder1
#   $lvResults$RadDataPager1$ctl01$ctl01" for page 2) is capped at
#   _DORSET_MAX_PAGES_PER_TYPE pages per type to bound the number of
#   detail-page fetches a single scan makes -- outstanding TRT alone ran to
#   10 pages/~100 results live, and every result needs its own detail
#   fetch since the list view carries no applicant info at all.
DORSET_BASE = "https://planning.dorsetcouncil.gov.uk"
_DORSET_TREE_TYPE_CODES = ("TRT", "TRC", "TRD")
_DORSET_MAX_PAGES_PER_TYPE = 3


def _dorset_serialize_form(soup) -> Dict[str, str]:
    """Same discipline as NorthgateScraper._serialize_form() above: reads
    every field's current server-rendered value so the required ASP.NET
    postback tokens are carried through automatically alongside whichever
    fields this call site explicitly overrides."""
    form = soup.find("form")
    data: Dict[str, str] = {}
    if not form:
        return data
    for inp in form.find_all("input"):
        name = inp.get("name")
        if not name:
            continue
        itype = (inp.get("type") or "text").lower()
        if itype in ("checkbox", "radio"):
            if inp.get("checked") is not None:
                data[name] = inp.get("value", "on")
        elif itype in ("submit", "button", "image", "reset"):
            continue
        else:
            data[name] = inp.get("value", "")
    for sel in form.find_all("select"):
        name = sel.get("name")
        if not name:
            continue
        selected = sel.find("option", selected=True)
        if selected is not None:
            data[name] = selected.get("value", "")
        else:
            first = sel.find("option")
            data[name] = first.get("value", "") if first else ""
    return data


def _dorset_accept_disclaimer(session) -> bool:
    try:
        res = net_utils.smart_get(f"{DORSET_BASE}/", session=session, timeout=15)
    except Exception as e:
        logger.debug(f"[DORSET] Could not load landing page: {e}")
        return False
    if "disclaimer" not in res.url.lower() and "Disclaimer" not in res.text[:2000]:
        return True  # already past it somehow (e.g. a redirect straight to search)
    soup = BeautifulSoup(res.text, "html.parser")
    data = _dorset_serialize_form(soup)
    data["ctl00$ContentPlaceHolder1$btnAccept"] = "Accept"
    try:
        res = net_utils.smart_post(f"{DORSET_BASE}/disclaimer.aspx?returnURL=%2f", session=session, data=data, timeout=15)
    except Exception as e:
        logger.debug(f"[DORSET] Disclaimer accept POST failed: {e}")
        return False
    return res.status_code == 200


def _dorset_parse_results_page(html: str) -> Tuple[List[Dict], Optional[str]]:
    """Returns (rows, next_page_target) -- rows are {reference, address,
    description, recno} for every genuinely undecided (blank Decision)
    result on this page; next_page_target is the __EVENTTARGET postback
    name for "page 2" onward the FIRST time this is called for a fresh
    search (the pager's control naming is stable across pages), or None
    once no further pages remain."""
    soup = BeautifulSoup(html, "html.parser")
    rows: List[Dict] = []
    for block in soup.find_all("div", class_="emphasise-area"):
        link = block.find("a", href=re.compile(r"plandisp\.aspx\?recno="))
        if not link:
            continue
        reference = link.get_text(strip=True)
        recno_match = re.search(r"recno=(\d+)", link.get("href", ""))
        if not reference or not recno_match:
            continue
        headings = block.find_all("h3")
        by_heading = {}
        for h in headings:
            label = h.get_text(strip=True).rstrip(":")
            nxt = h.find_next_sibling()
            by_heading[label] = nxt.get_text(strip=True) if nxt and nxt.name == "p" else ""
        decision = by_heading.get("Decision", "")
        if _looks_like_real_value(decision):
            continue  # already decided
        description = by_heading.get("Proposal")
        address = by_heading.get("Location")
        if not description:
            continue
        rows.append({
            "reference": reference,
            "address": address,
            "description": description,
            "recno": recno_match.group(1),
        })
    next_target = None
    pager_link = soup.find("a", href=re.compile(r"RadDataPager1\$ctl01\$ctl01"))
    if pager_link:
        m = re.search(r"__doPostBack\('([^']+)'", pager_link.get("href", ""))
        if m:
            next_target = m.group(1)
    return rows, next_target


def _dorset_case_detail(session, recno: str) -> Dict:
    """Dorset's plandisp.aspx renders every field as a `<span
    class="applabel">Label</span><p class="appdata">Value</p>` pair --
    confirmed live for Applicant/Applicant's Address/Agent/Valid Date."""
    out: Dict = {}
    try:
        res = net_utils.smart_get(f"{DORSET_BASE}/plandisp.aspx", session=session, params={"recno": recno}, timeout=15)
    except Exception as e:
        logger.debug(f"[DORSET] Detail request for recno {recno} failed: {e}")
        return out
    if res.status_code != 200:
        return out
    soup = BeautifulSoup(res.text, "html.parser")

    def _label_value(label: str) -> Optional[str]:
        span = soup.find("span", class_="applabel", string=lambda s: s and s.strip() == label)
        if not span:
            return None
        nxt = span.find_next_sibling("p", class_="appdata")
        if not nxt:
            return None
        text = nxt.get_text(" ", strip=True)
        return text or None

    applicant = _label_value("Applicant")
    agent = _label_value("Agent")
    applicant_name = applicant if _looks_like_real_value(applicant) else None
    agent_name = agent if _looks_like_real_value(agent) else None
    # Dorset copies the Applicant straight into the Agent field verbatim
    # when there genuinely is no separate agent (confirmed live) -- an
    # identical (case/whitespace-insensitive) pair is not a real agent.
    if agent_name and applicant_name and agent_name.strip().casefold() == applicant_name.strip().casefold():
        agent_name = None
    out["applicant_name"] = applicant_name
    out["agent_name"] = agent_name
    out["has_agent"] = agent_name is not None
    out["agent_is_tree_surgeon"] = classify_agent_as_tree_surgeon(agent_name, None) if agent_name else False
    out["registered_date"] = _parse_idox_date(_label_value("Valid Date"))
    return out


def scrape_dorset_council() -> List[Dict]:
    """Entry point for Dorset's bespoke "dorsetforyou.com" ASP.NET
    WebForms portal. Single council, no registry."""
    session = requests.Session()
    session.headers.update({
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/115.0.0.0 Safari/537.36",
    })
    logger.info(f"[DORSET] Scraping Dorset directly from {DORSET_BASE}...")
    if not _dorset_accept_disclaimer(session):
        logger.debug("[DORSET] Could not get past the disclaimer gate -- skipping this run.")
        return []

    leads: List[Dict] = []
    for type_code in _DORSET_TREE_TYPE_CODES:
        time.sleep(1)  # be polite between the three type searches
        try:
            res = net_utils.smart_get(f"{DORSET_BASE}/advsearch.aspx", session=session, timeout=15)
        except Exception as e:
            logger.debug(f"[DORSET] Could not load advsearch.aspx for type {type_code}: {e}")
            continue
        if res.status_code != 200:
            continue
        soup = BeautifulSoup(res.text, "html.parser")
        data = _dorset_serialize_form(soup)
        data["ctl00$ContentPlaceHolder1$ddlApplicationType"] = type_code
        data["ctl00$ContentPlaceHolder1$chkOutstanding"] = "on"
        data["ctl00$ContentPlaceHolder1$btnSearch"] = "Search"
        try:
            res = net_utils.smart_post(f"{DORSET_BASE}/searchresults.aspx", session=session, data=data, timeout=15)
        except Exception as e:
            logger.debug(f"[DORSET] Search POST for type {type_code} failed: {e}")
            continue
        if res.status_code != 200:
            continue

        rows, next_target = _dorset_parse_results_page(res.text)
        collected = list(rows)
        page_soup = BeautifulSoup(res.text, "html.parser")
        for page_num in range(2, _DORSET_MAX_PAGES_PER_TYPE + 1):
            if not next_target:
                break
            page_data = _dorset_serialize_form(page_soup)
            page_data["__EVENTTARGET"] = next_target
            page_data["__EVENTARGUMENT"] = ""
            page_data.pop("ctl00$ContentPlaceHolder1$btnSearch", None)
            try:
                res = net_utils.smart_post(f"{DORSET_BASE}/searchresults.aspx", session=session, data=page_data, timeout=15)
            except Exception as e:
                logger.debug(f"[DORSET] Pagination request (page {page_num}) for type {type_code} failed: {e}")
                break
            if res.status_code != 200:
                break
            page_soup = BeautifulSoup(res.text, "html.parser")
            more_rows, next_target = _dorset_parse_results_page(res.text)
            collected.extend(more_rows)

        for row in collected:
            time.sleep(0.5)  # be polite between per-case detail requests
            detail = _dorset_case_detail(session, row["recno"])
            leads.append({
                "reference": row["reference"],
                "address": row["address"],
                "description": row["description"],
                "vertical": "tree",  # all three registered type codes are tree-specific by definition
                "applicant_name": detail.get("applicant_name"),
                "agent_name": detail.get("agent_name"),
                "has_agent": detail.get("has_agent", False),
                "agent_is_tree_surgeon": detail.get("agent_is_tree_surgeon", False),
                "registered_date": detail.get("registered_date"),
            })
    return leads


# --- Stratford-on-Avon District Council -- bespoke "E-Planning" Vue.js SPA ---
#
# Fifth bespoke one-off (Sep 3 2026). A genuinely custom-built Vue 3 SPA
# (apps.stratford.gov.uk/eplanningv2) with its own clean JSON REST API
# behind it -- confirmed live via AdvancedSearchV2ViewModel.js and direct
# API calls, no vendor platform, no disclaimer gate, and crucially NO
# CAPTCHA/bot-detection on either endpoint used here (unlike West
# Northamptonshire's invisible reCAPTCHA gate on a search of the same
# shape -- confirmed live to genuinely block a token-less POST, so that
# one was correctly left unbuilt rather than worked around):
#   1. GET {API_BASE}v1/Search?appType=<GUID>&activeOnly=true -- a plain
#      JSON array of {id, reference, proposal, address, status, validDate,
#      decisionDate, link, ...} for every case of the given application
#      type. Confirmed live: activeOnly=true reliably returns only
#      "Pending Consideration"/decisionDate:null rows (60/60 and 12/12
#      sampled undecided-only vs. the unfiltered 500-row page cap).
#   2. GET {API_BASE}v1/PlanningApplication/<id> -- per-case detail, plain
#      JSON with separate applicantDetails/agentDetails objects (each
#      {name, address, company} -- confirmed live on real cases to be
#      genuinely distinct, no Dorset-style Applicant-copied-into-Agent
#      quirk here) and importantDates (DD/MM/YYYY strings, the same format
#      _parse_idox_date already covers).
# Three tree-specific application type GUIDs (looked up live from the
# council's own v1/Search/Advanced/Filters endpoint, Sep 3 2026 -- these
# IDs are opaque and NOT derivable from the type name, so they must be
# re-verified against that endpoint if this ever needs updating):
#   35b07ba1-c797-c485-b166-08cef6b49711 = "Notification for Works to Trees in CA"
#   cafce51d-a91a-cabb-d236-08cef6b4f8a0 = "Tree Preservation Order"
#   912ec74a-d0e6-c4e9-9cb3-08dc70747cf9 = "5 day Notif. for Dead or Dangerous Tree"
#     (confirmed live to have zero records of any status as of Sep 3 2026 --
#     kept rather than dropped since querying it is free and the type may
#     see genuine use later)
# "Pre-application Advice - Trees" is deliberately excluded -- pre-app
# advice only, not a real works application.
STRATFORD_BASE = "https://apps.stratford.gov.uk"
_STRATFORD_API_BASE = "https://apps.stratford.gov.uk/EplanningV2/API/"
_STRATFORD_TREE_APPLICATION_TYPES = (
    "35b07ba1-c797-c485-b166-08cef6b49711",  # Notification for Works to Trees in CA
    "cafce51d-a91a-cabb-d236-08cef6b4f8a0",  # Tree Preservation Order
    "912ec74a-d0e6-c4e9-9cb3-08dc70747cf9",  # 5 day Notif. for Dead or Dangerous Tree
)


def _stratford_case_detail(case_id: str) -> Dict:
    """GET v1/PlanningApplication/<id> -- separate applicant/agent objects,
    no Dorset-style dedup quirk needed here (confirmed live: distinct real
    cases show genuinely distinct applicant vs agent names)."""
    out: Dict = {}
    try:
        res = net_utils.smart_get(f"{_STRATFORD_API_BASE}v1/PlanningApplication/{case_id}", timeout=15)
    except Exception as e:
        logger.debug(f"[STRATFORD] Detail request for case {case_id} failed: {e}")
        return out
    if res.status_code != 200:
        return out
    try:
        data = res.json()
    except ValueError:
        return out
    applicant = (data.get("applicantDetails") or {}).get("name")
    agent = (data.get("agentDetails") or {}).get("name")
    agent_company = (data.get("agentDetails") or {}).get("company")
    applicant_name = applicant if _looks_like_real_value(applicant) else None
    agent_name = agent if _looks_like_real_value(agent) else None
    agent_company_name = agent_company if _looks_like_real_value(agent_company) else None
    out["applicant_name"] = applicant_name
    out["agent_name"] = agent_name
    out["agent_company"] = agent_company_name
    out["has_agent"] = agent_name is not None or agent_company_name is not None
    out["agent_is_tree_surgeon"] = (
        classify_agent_as_tree_surgeon(agent_name, agent_company_name)
        if (agent_name or agent_company_name) else False
    )
    important_dates = data.get("importantDates") or {}
    out["registered_date"] = _parse_idox_date(important_dates.get("applicationValidDate"))
    return out


def scrape_stratford_on_avon_council() -> List[Dict]:
    """Entry point for Stratford-on-Avon District Council's bespoke
    "E-Planning" Vue.js SPA. Single council, no registry."""
    leads: List[Dict] = []
    for app_type in _STRATFORD_TREE_APPLICATION_TYPES:
        time.sleep(1)  # be polite between the type-filtered searches
        try:
            res = net_utils.smart_get(
                f"{_STRATFORD_API_BASE}v1/Search",
                params={"appType": app_type, "activeOnly": "true"},
                timeout=15,
            )
        except Exception as e:
            logger.debug(f"[STRATFORD] Search request for type {app_type} failed: {e}")
            continue
        if res.status_code != 200:
            continue
        try:
            rows = res.json()
        except ValueError:
            continue
        for row in rows:
            reference = row.get("reference")
            description = row.get("proposal")
            case_id = row.get("id")
            if not reference or not description or not case_id:
                continue
            time.sleep(0.5)  # be polite between per-case detail requests
            detail = _stratford_case_detail(case_id)
            leads.append({
                "reference": reference,
                "address": row.get("address"),
                "description": description,
                "vertical": "tree",  # all three registered type GUIDs are tree-specific by definition
                "applicant_name": detail.get("applicant_name"),
                "agent_name": detail.get("agent_name"),
                "agent_company": detail.get("agent_company"),
                "has_agent": detail.get("has_agent", False),
                "agent_is_tree_surgeon": detail.get("agent_is_tree_surgeon", False),
                "registered_date": detail.get("registered_date"),
            })
    return leads

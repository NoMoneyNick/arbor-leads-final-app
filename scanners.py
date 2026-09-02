import os
import re
import requests
import time
import datetime
import threading
import logging
from typing import Optional, List, Tuple, Dict, Any
import database
import notifications
import net_utils
import persistent_dedup_cache as dedup

logger = logging.getLogger("vector-data-labs")

GLA_API_KEY = os.getenv("GLA_API_KEY", "").strip()
UK_PLANNING_API_KEY = os.getenv("UK_PLANNING_API_KEY", "").strip()

# ── Lead Scoring ──────────────────────────────────────────────────────────────



LARGE_KEYWORDS = [
    "tpo", "tree preservation order", "conservation area", "woodland",
    "development", "several trees", "multiple trees", "commercial",
    "site clearance", "site works", "dangerous tree", "estate",
    "demolition", "contaminated", "application to carry out works",
    "section 211", "s211", "bs5837", "bs 5837", "arboricultural impact",
    "woodland clearance", "group of trees", "woodland management"
]
MEDIUM_KEYWORDS = [
    "crown reduction", "crown lift", "crown thin", "crown raising", "crown clean",
    "crown thinning", "crown lifting", "crown cleaning", "lateral branches",
    "fell", "felling", "tree felling", "felling of",
    "removal of tree", "remove tree", "tree removal", "sectional dismantle", "dismantle",
    "pollarding", "pollard", "re-pollard",
    "overhanging", "storm damage", "hanging branch", "decayed tree",
    "deadwood", "dead wood", "dead branches", "works to trees", "work to trees",
    "urgent", "diseased tree", "ash dieback", "coppice", "coppicing", "monolith"
]
SMALL_KEYWORDS = [
    "tree pruning", "tree trimming", "tree maintenance", "pruning of",
    "hedge trimming", "hedge cutting", "hedge removal", "hedge reduction",
    "tree inspection", "tree survey", "tree assessment", "tree report",
    "minor works to tree", "lopping", "sever ivy", "root protection", "root severance"
]

# Compound phrases used to decide if a planning application is tree-related at all.
# Eliminates false positives (medical/dental surgery, street names, hotel crowns)
TREE_GOLD = [
    # Core trade terms
    "tree surgery", "tree surgeon", "tree work", "tree works", "works to tree", "work to tree",
    "tree felling", "tree removal", "tree pruning", "tree trimming", "tree maintenance",
    "tree preservation", "tree protection", "tree survey", "tree assessment", "tree report",
    "arboricultural", "arborist", "arboriculture", "arbor",
    "tpo", "tree preservation order", "protected tree", "mature tree", "specimen tree",
    "section 211", "s211", "notice of intent",
    # Specific arboricultural operations
    # NOTE (Aug 29 2026): a bare "fell " entry used to live here. It matched
    # any ordinary use of "fell" as a verb -- "a branch fell in the storm",
    # "the applicant fell ill", "the company fell behind" -- which is exactly
    # the "fell down' style phrasing" false positive this list's own comment
    # says it exists to avoid. Caught by the new test suite (test_scrapers.py)
    # before it shipped further. Removed; "felling", "fell to ground", and
    # "fell 1/2/3" (the numbered-tree-list phrasing councils actually use in
    # application descriptions) already cover genuine tree-work mentions.
    "felling", "fell to ground", "fell 1", "fell 2", "fell 3", "sectional dismantle", "dismantle",
    # Sep 2 2026: sanity-checking HMO_GOLD against real live PlanIt data
    # (Nottingham, during the multi-vertical wiring verification pass) also
    # surfaced a genuine, currently-live TREE_GOLD false NEGATIVE -- a real
    # application literally titled "Fell a dead tree in rear garden." matched
    # none of the phrases above (not "felling"/"fell to ground"/"fell 1/2/3",
    # no species name, not "deadwood"/"dead wood"/"dead branches" either --
    # "dead tree" is a distinct phrase this list never covered). This
    # pre-dates and is unrelated to the multi-vertical work; found by luck
    # of sampling real data while checking something else, so fixed here
    # rather than filed away for later. Each addition below is a direct
    # article-variant ("a"/"the") of an existing safe phrase, or "dead
    # tree" itself -- all essentially unambiguous in real English (unlike
    # bare "fell", these are never used non-arboriculturally), so this adds
    # zero real false-positive risk while catching short, plain-English
    # descriptions (this one reads like the applicant's own wording, not
    # council-officer boilerplate) that the more formal phrasing above
    # doesn't reach.
    "dead tree", "fell a tree", "fell the tree", "remove a tree", "removal of a tree",
    "stump grinding", "stump removal", "stump",
    "pollard", "pollarding", "re-pollard",
    "crown reduction", "crown lift", "crown thin", "crown raising", "crown clean",
    "crown thinning", "crown lifting", "crown cleaning", "lateral branch", "lateral branches",
    "deadwood", "dead wood", "dead branches", "ash dieback", "diseased tree", "decayed tree",
    "woodland management", "woodland clearance", "coppice", "coppicing", "monolith",
    "hedge trimming", "hedge cutting", "hedge removal", "hedge reduction",
    "bs5837", "bs 5837", "root protection area", "root severance",
    # Specific species with tree/work indicators
    "oak tree", "ash tree", "sycamore tree", "beech tree", "pine tree", "willow tree",
    "birch tree", "conifer tree", "cedar tree", "cypress tree", "poplar tree", "yew tree",
    "lime tree", "horse chestnut", "eucalyptus"
]



def score_lead(summary: str) -> tuple:
    """
    Classifies a planning application as small / medium / large
    and returns the corresponding price.
    Returns: (lead_score: str, lead_price: int)
    """
    s = summary.lower()
    if any(k in s for k in LARGE_KEYWORDS):
        return "large", 75
    elif any(k in s for k in MEDIUM_KEYWORDS):
        return "medium", 50
    return "small", 25


# Sep 2 2026: first piece of the multi-vertical build (see
# master_expansion_plan_v2.md) -- HMO / change-of-use conversions as vertical
# 2, chosen for the smallest council footprint to prove the generalized
# pipeline end to end. Deliberately narrower than a bare "change of use"
# match: that phrase alone covers thousands of unrelated applications
# (shop-to-restaurant, office-to-gym, etc.) and would flood this vertical
# with false positives -- the same "avoid the too-broad bare word" lesson
# TREE_GOLD's own "fell" removal (Aug 29 2026 comment above) already taught
# this file once. Every "change of use" entry here is qualified by HMO/
# multiple-occupation wording specifically, never used bare.
HMO_GOLD = [
    "house in multiple occupation", "houses in multiple occupation",
    "hmo", "multiple occupation",
    "class c4", "use class c4", "c3 to c4", "c4 to c3",
    "large hmo", "small hmo",
    "7 or more unrelated", "seven or more unrelated",
    "unrelated individuals", "unrelated persons", "unrelated sharers",
    "change of use to a house in multiple occupation",
    "change of use to house in multiple occupation",
    "conversion to a house in multiple occupation",
    "conversion to house in multiple occupation",
    "conversion into a house in multiple occupation",
    # Sep 2 2026: an adversarial review pass (run before this multi-vertical
    # build ships) caught two bare, ambiguous entries here that were the same
    # class of bug as TREE_GOLD's bare-"fell" false positive above: a "large
    # HMO" (7+ unrelated occupants) is legally classed as "sui generis" use,
    # not C4 -- a real reason someone added the bare phrase -- but "sui
    # generis" is ALSO the general planning catch-all for nightclubs,
    # drive-throughs, casinos, scrapyards, betting shops, hostels and dozens
    # of other totally unrelated uses, so the bare phrase alone would tag a
    # description like "Change of use to sui generis (drive-through
    # restaurant)" as an HMO lead. Similarly, "Article 4 Direction" is a
    # general permitted-development-removal mechanism used for HMOs but also
    # for shopfronts in conservation areas, agricultural building
    # conversions, demolition control, and more -- a bare match would tag any
    # of those as HMO too. Replaced both with phrasing that requires the HMO
    # context to actually be present in the same description, which is how
    # a genuine large-HMO/Article-4-for-HMO application is actually worded in
    # practice (and is already additionally covered by the "hmo"/"house in
    # multiple occupation"/"c4" phrases above in the common case where an
    # application mentions both) -- this trades a small amount of unproven
    # recall for real, demonstrated precision.
    "sui generis hmo", "sui generis house in multiple occupation",
    "hmo (sui generis)", "hmo sui generis", "large hmo (sui generis)",
    "article 4 direction removing permitted development rights for a house in multiple occupation",
    "article 4 direction restricting hmo",
    "article 4 direction for hmo",
]

# Sep 2 2026: the verticals config the build plan calls for -- each entry's
# "keywords" list is what _matches_vertical checks against. Adding a THIRD
# vertical later should mean adding one more entry here, nothing else in
# this section -- if it needs more than that, the generalization isn't
# finished yet (see master_expansion_plan_v2.md's build-order note on this
# exact point).
#
# "capture_identity" (Sep 2 2026, master_expansion_plan_v2.md build-order
# step 3 -- the GDPR-safe lead format): tree's existing business model
# captures and displays the planning applicant's name to the paying
# contractor (see /dashboard's "Applicant: ..." line and the has_agent
# exclusion-filter logic in database.py, both tree-specific, both already
# live and unaffected by this flag). HMO is a deliberately different,
# stricter design, built in from day one per the plan rather than
# retrofitted later: never extract, store, or display the applicant's name
# or the agent's identity at all -- sell address + project type + council
# reference only, addressed to "The Owner/Occupier." This is real risk
# reduction regardless of the unsettled legal question of whether an
# address alone is personal data (see master_expansion_plan_v2.md's own
# note that "the Occupier" fully exempting a mailing from the rules is the
# weaker, less-cited claim across independent research -- this flag does
# NOT rely on that claim being true; a full LIA/privacy-notice/rights
# process still applies regardless, per the plan's own more conservative,
# converged position). Defaults to True (tree's exact existing behaviour,
# every call site written before this flag existed is unaffected) when a
# vertical doesn't set it explicitly.
VERTICALS = {
    "tree": {"keywords": TREE_GOLD, "capture_identity": True},
    "hmo": {"keywords": HMO_GOLD, "capture_identity": False},
}


def _matches_vertical(text: str, vertical_key: str) -> bool:
    """True if `text` matches the given vertical's keyword list. Unknown
    vertical_key -> False (never crashes a caller for a typo'd key).

    Sep 2 2026: `text` used to go straight into `.lower()` with no type
    check. Every call site was already extracting the source field with an
    `or ""` fallback, which only substitutes when the value is missing or
    falsy -- a genuinely messy upstream record (seen in practice from these
    government APIs) returning a truthy non-string for a description field
    (an int, a nested dict) passed straight through and raised AttributeError
    here, uncaught by any per-item guard in the paid-API/PlanIt loops in
    scan_city_planning_api -- which crashed the WHOLE loop, and since those
    loops only conn.commit() once at the very end, every lead already
    inserted earlier in that same run was implicitly rolled back too.
    Coercing defensively here, at the one shared root, protects every
    current and future caller in one place instead of relying on each call
    site to remember its own guard."""
    vertical = VERTICALS.get(vertical_key)
    if not vertical:
        return False
    s = str(text or "").lower()
    return any(word in s for word in vertical["keywords"])


def classify_verticals(text: str) -> List[str]:
    """Returns every vertical `text` matches, e.g. ["tree", "hmo"] for an
    application that's both an HMO conversion and involves tree removal.
    Empty list if it matches none. This is what makes the "sell one
    matching application into every vertical it qualifies for" monetization
    idea (master_expansion_plan_v2.md) possible -- the classification is
    already multi-label, not a single best-guess category."""
    return [key for key in VERTICALS if _matches_vertical(text, key)]


def _is_tree_related(text: str) -> bool:
    # Kept as a thin wrapper over the generalized matcher -- every existing
    # call site (score_lead's callers below, PlanIt/paid-API filtering)
    # keeps working completely unchanged. Do not remove without updating
    # those call sites to call _matches_vertical(text, "tree") directly.
    return _matches_vertical(text, "tree")


def _resolve_vertical(text: str) -> Optional[str]:
    """Which single vertical a live scan call site should tag a lead with.

    Sep 2 2026: classify_verticals() is already multi-label (an application
    can match both "tree" and "hmo"), but the `leads` table's
    ON CONFLICT (reference) clause makes `reference` the sole unique key --
    it does not yet support one row per matched vertical for the same
    application (that would need a composite unique constraint on
    (reference, vertical), tracked in TASKS.md as a future schema decision,
    not done here). Until that exists, each application becomes exactly one
    row, and this picks which vertical wins:

    - No match at all -> None. Callers skip the item exactly as every call
      site already did before verticals existed (zero behaviour change for
      non-matching applications).
    - "tree" is one of the matches -> "tree" always wins, even if HMO also
      matched. This is deliberate: it guarantees every existing tree lead's
      behaviour and economics are provably unchanged by this refactor --
      the whole point of shipping this as a foundation-first step.
    - Otherwise -> the first (only, today) other match, e.g. "hmo". These
      are leads that were previously silently discarded entirely (an
      HMO-only application used to fail _is_tree_related and never reach
      _insert_lead at all), so every one of these is new, additive
      pipeline output, not a change to anything already flowing.
    """
    matches = classify_verticals(text)
    if not matches:
        return None
    if "tree" in matches:
        return "tree"
    return matches[0]


# Sep 2 2026, master_expansion_plan_v2.md build-order step 4 (the tiered
# classifier): Tier 2, structured fields. Before writing this, sampled 500
# real, currently-live PlanIt records across 5 authorities (Nottingham,
# Leicester, Sheffield, Bristol, Cambridge) via the live API to check what
# the plan's candidate fields ("application type code, use class, agent SIC
# code") actually look like in practice, rather than assuming:
#
#   - PlanIt's own `app_type` field IS a real, independent, high-precision
#     signal for tree work: of 137 real "Trees"-app_type records sampled, 12
#     (~9%) had NO obvious tree keyword anywhere in their free-text
#     description -- bare arborist shorthand like "T1 - Cherry - Reduce
#     height by 4m." or "Removal of deadwood", which no keyword list (this
#     one included) can ever fully anticipate. That's genuine new recall,
#     not redundant with Tier 1.
#   - `app_type` does NOT help HMO at all: real HMO applications sampled had
#     app_type scattered across Full/Amendment/Outline/Heritage/Advertising
#     with no dedicated category of its own -- gating on any single value
#     (e.g. "Amendment", the closest thing to a pattern) would trade a small
#     amount of unproven recall for real false positives (the same
#     "Amendment" value also matched "Use of retail unit (Use Class E) as a
#     gaming lounge (Sui Generis)", nothing to do with HMO).
#   - The plan's other two candidate fields don't exist as usable structured
#     data today: "use class" (C3/C4/Sui Generis) only ever appears inside
#     the free-text description itself, which Tier 1 already reads; a real
#     agent SIC code needs a live Companies House lookup per agent name,
#     only possible when a genuine (non-"See source") name exists -- rare
#     via PlanIt specifically. Worth revisiting via the mesh scanner (which
#     DOES capture real agent names from each council's own page) once HMO
#     needs it -- not built here, and not worth blocking this tier on.
#
# Net result: this tier is tree-only and PlanIt-only for now, both
# deliberately -- built around fields actually confirmed to exist and help,
# not the plan's full candidate list assumed sight-unseen.
_STRUCTURED_TREE_APP_TYPES = {"trees"}


def _resolve_vertical_with_structured_fields(text: str, app_type: Optional[str] = None) -> Optional[str]:
    """Tier 1 (keyword, via _resolve_vertical) + Tier 2 (structured field)
    vertical resolution. Falls back to Tier 1 alone whenever no app_type is
    supplied, so every call site without one (paid-API loop, GLA, Leeds --
    none of which have a confirmed equivalent field, see the module comment
    above _STRUCTURED_TREE_APP_TYPES) is completely unaffected."""
    tier1 = _resolve_vertical(text)
    if tier1 is not None:
        return tier1
    if app_type and str(app_type).strip().lower() in _STRUCTURED_TREE_APP_TYPES:
        return "tree"
    return None


def _queue_for_manual_review(reference: str, address: str, description: str, source: str, app_type: str = None) -> None:
    """Sep 2 2026, master_expansion_plan_v2.md build-order step 4, Tier 4:
    "manual review queue for anything that fails all three [tiers] --
    visible, never silently dropped." Called from every scan call site's
    `if vertical is None:` branch, in place of the old bare `continue` that
    discarded the application completely and permanently with no trace.

    Applies the same minimal quality bar _insert_lead itself uses (a
    description under 12 chars is placeholder noise, not a real application
    worth a human's time or a future Tier 3 LLM call) so the queue holds
    real candidates, not blank junk. Best-effort and silent on failure by
    design -- a DB hiccup here must never interrupt the scan loop that's
    still working through the rest of its batch; this is a nice-to-have
    safety net, not the primary lead pipeline."""
    if not reference or not description or len(description.strip()) < 12:
        return
    try:
        database.insert_unclassified_application(reference, address, description, source, app_type=app_type)
    except Exception as e:
        logger.debug(f"[ReviewQueue] Could not queue {reference} for review: {e}")


# ── Tier 3: cheap LLM classification (master_expansion_plan_v2.md build- ──
# order step 4) ─────────────────────────────────────────────────────────────
#
# Runs as its OWN batch pass against the Tier 4 review queue
# (unclassified_applications), never inline in the live scan loops above.
# Why: the vast majority of planning applications nationwide match NEITHER
# vertical at all (rear extensions, adverts, telecoms, and so on) -- calling
# an LLM inline for every single one of those would mean thousands of real
# paid API calls a day for close to zero genuine yield, on top of real added
# latency to a pipeline that's already slow by design (PlanIt alone paces at
# one request per PLANIT_MIN_INTERVAL_SECONDS). Routing everything Tier 1+2
# can't place into the queue first, then classifying that queue in its own
# rate-limited batch (see process_review_queue_with_llm), reaches the
# identical end result with none of that cost or latency risk, and can never
# break the live scan pipeline itself even if Gemini is slow, down, or
# misconfigured.
#
# Library choice: Google's newer `google-genai` package exists and
# `google.generativeai` is officially marked deprecated upstream, but this
# session could not confirm the new package's exact current call shape with
# confidence -- documentation fetched while researching this gave two
# different, inconsistent method signatures for it. Rather than gamble on an
# unverified surface for code that can't be end-to-end tested without a live
# key, this deliberately reuses the OLDER library already proven working in
# production in Nick's own CRAWLER PROJECT
# (outreach_engine/services/reply_classifier.py -- identical
# configure/GenerativeModel/generate_content pattern). Worth migrating both
# together to google-genai if/when Nick wants to modernize -- not done here.
#
# Model default: "gemini-flash-latest" -- Google's own version-agnostic
# alias that gets hot-swapped to the newest Flash model automatically, so
# this doesn't need manual updates as models are superseded. Override via
# GEMINI_MODEL if a pinned, deterministic version is ever preferred instead.
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "").strip()
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-flash-latest").strip()

_gemini_model = None
if GEMINI_API_KEY:
    try:
        import google.generativeai as genai
        genai.configure(api_key=GEMINI_API_KEY)
        _gemini_model = genai.GenerativeModel(GEMINI_MODEL)
    except Exception as e:
        logger.error(f"[Tier3 LLM] Could not initialize Gemini client -- Tier 3 classification stays disabled until this is fixed: {e}")
        _gemini_model = None


def _classify_via_llm(description: str) -> Optional[str]:
    """Asks Gemini to pick a configured vertical for one queued
    application's description, or say it's genuinely neither. Returns the
    vertical key on a confident match, or None on an unavailable model, a
    genuinely uncertain answer, or any error -- callers must treat None as
    "still needs a human", never as "definitely not a lead"."""
    if not _gemini_model or not description:
        return None
    vertical_keys = list(VERTICALS.keys())
    prompt = f"""You are classifying a UK council planning application description for a lead-generation service. The service currently buys/sells leads for exactly these categories: {", ".join(vertical_keys)}.

"tree" means any tree surgery/arboriculture work: felling, pruning, crown reduction, pollarding, hedge work, anything covered by a Tree Preservation Order -- including terse professional shorthand with no obvious keyword, e.g. "T1 - Cherry - Reduce height by 4m."

"hmo" means a house in multiple occupation: change of use to an HMO, a Certificate of Lawfulness for existing HMO use, an HMO-specific Article 4 direction.

Application description:
"{description}"

Reply with EXACTLY ONE WORD: one of {vertical_keys}, or NONE if it is genuinely neither. No explanation."""
    try:
        response = _gemini_model.generate_content(prompt)
        answer = (getattr(response, "text", "") or "").strip().lower()
        return answer if answer in vertical_keys else None
    except Exception as e:
        logger.warning(f"[Tier3 LLM] Gemini classification failed, leaving item queued for review: {e}")
        return None


def process_review_queue_with_llm(batch_size: int = 25, max_llm_attempts: int = 2) -> dict:
    """Tier 3 entry point: pulls up to `batch_size` still-open review-queue
    rows (excluding ones that already exhausted max_llm_attempts, so a
    genuinely ambiguous item doesn't keep re-spending a real API call
    forever), asks Gemini to classify each, and for a confident answer
    inserts a real lead exactly as Tier 1/2 would have and closes the queue
    row. An uncertain or failed answer just increments the row's attempt
    count and leaves it queued -- never guesses, never silently drops.

    Deliberately NOT wired into the automatic daily pipeline -- exposed only
    via a manual-trigger endpoint (see main.py's /process-review-queue)
    until Nick has seen real cost/accuracy numbers and decides whether to
    schedule it."""
    result = {"processed": 0, "classified": 0, "still_uncertain": 0, "errors": 0}
    if not _gemini_model:
        logger.warning("[Tier3 LLM] GEMINI_API_KEY not configured (or client init failed) -- nothing to process.")
        return result

    queue = database.get_pending_review_queue(limit=batch_size, max_llm_attempts=max_llm_attempts)
    if not queue:
        return result

    conn = database.get_db_conn()
    cur = conn.cursor()
    try:
        for row in queue:
            result["processed"] += 1
            try:
                vertical = _classify_via_llm(row["description"])
                if vertical:
                    _insert_lead(cur, row["reference"], row["address"], row["description"],
                                  row["source"], vertical=vertical)
                    conn.commit()
                    database.resolve_unclassified_application(row["reference"], vertical)
                    result["classified"] += 1
                else:
                    database.increment_review_queue_llm_attempts(row["reference"])
                    result["still_uncertain"] += 1
            except Exception as e:
                conn.rollback()
                logger.warning(f"[Tier3 LLM] Error processing queued item {row.get('reference')}: {e}")
                result["errors"] += 1
    finally:
        cur.close()
        conn.close()

    logger.info(
        f"[Tier3 LLM] Processed {result['processed']} queued items: "
        f"{result['classified']} classified, {result['still_uncertain']} still uncertain, "
        f"{result['errors']} errors."
    )
    return result


# ── Lead tagging system (Sep 2 2026, Nick's request: "total control of all our
# data, nice and neat") ──────────────────────────────────────────────────────
# A "floating bubble" model, not a single rigid category column: each lead
# gets a bag of independent, overlapping tags (locale, region, job size,
# job type, agent status, vertical) stored in one Postgres TEXT[] column,
# so a single Bromley crown-work large job can be found by filtering on
# ANY of "locale:bromley", "region:london", "size:large", "job:crown-work",
# "agent:no", "vertical:tree" at once -- see database.get_leads_by_tags().
# Deliberately NOT included as a tag: date. A tag baked in at insert time
# ("recent") would silently go stale the moment it isn't true anymore --
# date/deadline filtering stays a direct query against the existing
# discovered_at/registered_date/statutory_deadline columns instead, so it's
# never wrong in a way nobody notices (same "never silently wrong" standard
# as everything else in this pipeline).

# Multi-label, qualified-phrase job-type keywords -- same false-positive
# discipline as TREE_GOLD/HMO_GOLD (no bare "fell", no bare "reduce").
# "tpo" is deliberately allowed to co-occur with any other job-type tag,
# since a TPO is a legal status on top of the actual work, not a work type
# itself -- e.g. a crown reduction covered by a TPO gets both job:crown-work
# and job:tpo.
JOB_TYPE_KEYWORDS = {
    # "felling" combines this phrase list (handles "tree removal" phrasing,
    # which doesn't contain the word "fell" at all) with a separate
    # whole-word regex check in _classify_job_types for fell/felled/felling/
    # fells -- see that function's docstring for why the regex is needed on
    # top of a fixed phrase list.
    "felling": [
        "tree removal", "removal of tree", "remove tree", "removal of a tree",
        "removal of the tree",
    ],
    "crown-work": [
        "crown reduction", "crown reduce", "crown thin", "crown lift",
        "crown raise", "crown clean", "pollard", "reduce height", "reduce crown",
    ],
    "hedge-work": [
        "hedge removal", "remove hedge", "reduce hedge", "hedge reduction",
        "trim hedge", "cut hedge back",
    ],
    "stump-work": [
        "stump grind", "stump removal", "remove stump", "grind stump",
        "stump grinding",
    ],
    "tpo": [
        "tree preservation order", "tpo ", "(tpo)", "protected tree",
        "tpo application",
    ],
    "disease-hazard": [
        "ash dieback", "diseased tree", "dangerous tree", "dead tree",
        "storm damage", "hazardous tree", "dying tree",
    ],
}


def _classify_job_types(text: str) -> list:
    """Multi-label job-type tagging for the tree vertical. Returns every
    matching category (a lead can be e.g. both crown-work AND tpo at once).
    Returns ["other"] if nothing matched, so every tree lead is visible in
    at least one job-type bucket -- never silently untagged, same principle
    as Tier 4's manual review queue.

    "felling" uses a whole-word regex (\\bfell\\w*\\b) rather than a fixed
    phrase list -- real descriptions vary too much ("fell a dead tree",
    "fell an oak", "felled", "fells") for a phrase list to keep up with.
    This is deliberately looser than TREE_GOLD's own bare-"fell" ban:
    TREE_GOLD's job is deciding whether text is about a tree AT ALL (where
    "the applicant fell ill" is a real, damaging false positive), while this
    function only ever runs on text ALREADY confirmed to be a genuine tree
    lead by Tier 1/2 -- mistagging a rare "fell ill" mention as job-type
    "felling" inside an already-real tree lead is a minor filtering
    inconvenience, not a false lead, so the tradeoff runs the other way."""
    import re as _re
    t = str(text or "").lower()
    matched = [key for key, phrases in JOB_TYPE_KEYWORDS.items() if any(p in t for p in phrases)]
    if "felling" not in matched and _re.search(r"\bfell\w*\b", t):
        matched.append("felling")
    return matched if matched else ["other"]


# Council -> official ONS region mapping. Covers every council currently in
# mesh_scrapers.COUNCIL_REGISTRY plus every town in REGION_TOWNS (the two
# sources of council_source values this pipeline actually produces today).
# Deliberately NOT extended to guess at councils outside these two lists --
# an unrecognised council gets "region:unclassified" rather than a guessed
# region, so a gap is visible rather than silently wrong (see
# _resolve_region below).
COUNCIL_TO_REGION = {
    # London boroughs (COUNCIL_REGISTRY)
    "WESTMINSTER": "London", "BROMLEY": "London", "CROYDON": "London",
    "SOUTHWARK": "London", "LAMBETH": "London", "BARNET": "London",
    "BRENT": "London", "EALING": "London", "KINGSTON": "London",
    "GREENWICH": "London", "BEXLEY": "London",
    "HAMMERSMITH & FULHAM": "London", "SUTTON": "London",
    # Scotland (COUNCIL_REGISTRY)
    "EDINBURGH": "Scotland", "GLASGOW": "Scotland", "FIFE": "Scotland",
    # South West (COUNCIL_REGISTRY)
    "CORNWALL": "South West", "BRISTOL": "South West", "EXETER": "South West",
    "PLYMOUTH": "South West", "CHELTENHAM": "South West",
    "GLOUCESTER": "South West",
    # Yorkshire and the Humber (COUNCIL_REGISTRY)
    "LEEDS": "Yorkshire and the Humber", "SHEFFIELD": "Yorkshire and the Humber",
    "YORK": "Yorkshire and the Humber",
    # South East (COUNCIL_REGISTRY)
    "OXFORD": "South East", "SOUTHAMPTON": "South East",
    "PORTSMOUTH": "South East", "BRIGHTON": "South East",
    "SURREY HEATH": "South East", "GUILDFORD": "South East",
    "SEVENOAKS": "South East", "DARTFORD": "South East",
    "MAIDSTONE": "South East", "TUNBRIDGE WELLS": "South East",
    "WINCHESTER": "South East", "NEW FOREST": "South East",
    "MILTON KEYNES": "South East",
    # East Midlands (COUNCIL_REGISTRY)
    "NOTTINGHAM": "East Midlands", "DERBY": "East Midlands",
    "LEICESTER": "East Midlands", "NORTH NORTHAMPTONSHIRE": "East Midlands",
    # West Midlands (COUNCIL_REGISTRY)
    "COVENTRY": "West Midlands", "WARWICK": "West Midlands",
    # North West (COUNCIL_REGISTRY)
    "CHESHIRE EAST": "North West", "CHESHIRE WEST": "North West",
    # East of England (COUNCIL_REGISTRY)
    "NORWICH": "East of England", "DACORUM": "East of England",
    # From REGION_TOWNS (paid-API/PlanIT authority names) -- towns not
    # already covered above.
    "READING": "South East", "GRIMSBY": "Yorkshire and the Humber",
    "WOLVERHAMPTON": "West Midlands", "SOLIHULL": "West Midlands",
    "DUDLEY": "West Midlands", "WALSALL": "West Midlands",
    "STOKE-ON-TRENT": "West Midlands", "NORTHAMPTON": "East Midlands",
    "LINCOLN": "East Midlands", "BRADFORD": "Yorkshire and the Humber",
    "WAKEFIELD": "Yorkshire and the Humber", "MANCHESTER": "North West",
    "LIVERPOOL": "North West", "PRESTON": "North West",
    "BLACKPOOL": "North West", "CHESHIRE EAST AND CHESTER": "North West",
    "CHESHIRE WEST AND CHESTER": "North West",
    "NEWCASTLE UPON TYNE": "North East", "SUNDERLAND": "North East",
    "DURHAM": "North East", "MIDDLESBROUGH": "North East",
    "DARLINGTON": "North East", "CAMBRIDGE": "East of England",
    "PETERBOROUGH": "East of England", "COLCHESTER": "East of England",
    "BIRMINGHAM": "West Midlands",
    "ABERDEEN CITY": "Scotland", "DUNDEE CITY": "Scotland",
    "STIRLING": "Scotland", "PERTH AND KINROSS": "Scotland",
    "CARDIFF": "Wales", "SWANSEA": "Wales", "NEWPORT": "Wales",
    "WREXHAM": "Wales", "BRIDGEND": "Wales",
    "BATH AND NORTH EAST SOMERSET": "South West", "SWINDON": "South West",
    "WILTSHIRE": "South West", "DORSET": "South West",
}

# Sep 2 2026: lets _resolve_region recognise council_source when it's
# already a clean region name (MESH's region-town sources pass the region
# itself through, e.g. council_source="South West") instead of a specific
# council -- see _resolve_region's own comment for the incident this fixes.
_CANONICAL_REGIONS_UPPER = {r.upper(): r for r in set(COUNCIL_TO_REGION.values())}
_REGION_NAME_ALIASES = {
    "YORKSHIRE": "Yorkshire and the Humber",
    "YORKSHIRE AND HUMBER": "Yorkshire and the Humber",
    "YORKSHIRE & HUMBER": "Yorkshire and the Humber",
    "THE HUMBER": "Yorkshire and the Humber",
}


# Sep 2 2026: region resolution used to trust whatever string a scraper put
# in council_source (COUNCIL_TO_REGION below is a fixed lookup table keyed
# on that string) -- but scrapers don't agree on what that string looks
# like (an official council name for some, a bare region name like "London"
# for MESH's region-town sources, a plain town name for others), so ~79% of
# real leads landed in region:unclassified even though the true answer was
# knowable. Nick's call (Sep 2 2026): never trust the source's own
# labelling for something this load-bearing -- verify it ourselves from
# data we already have. Every lead's `address` should carry a real UK
# postcode, and postcodes.io (the free ONS-backed postcode API already
# used elsewhere in this codebase -- see database.lookup_outcode_centroid,
# main.py's postcode-radius lookup) gives the authoritative region for any
# postcode, no key required. That's the primary path now; the old
# council-name table only catches the rare address with no postcode in it.
_POSTCODE_RE = re.compile(r'\b([A-Z]{1,2}\d[A-Z0-9]?)\s*(\d[A-Z]{2})\b', re.IGNORECASE)
_REGION_BY_OUTCODE_CACHE: Dict[str, str] = {}


def _extract_postcode(address: str) -> Optional[str]:
    """Pulls the first UK postcode-shaped substring out of a free-text
    address, e.g. '12 Elm Rd, Bromley BR1 3AB' -> 'BR1 3AB'. Returns None if
    nothing postcode-shaped is present -- never a guess."""
    m = _POSTCODE_RE.search(str(address or ""))
    if not m:
        return None
    return f"{m.group(1).upper()} {m.group(2).upper()}"


def _lookup_region_via_postcode(address: str) -> Optional[str]:
    """Resolves a real ONS region straight from the address's own postcode
    via postcodes.io -- authoritative, free, no key, and immune to whatever
    inconsistent label a scraper attached to council_source. Caches by
    outcode (the part before the space) since region is effectively
    constant within one outcode, which also means a cluster of leads in the
    same town costs one real HTTP call, not one per lead. Returns None
    (never a guess) if the address has no postcode or the lookup fails --
    caller falls back to COUNCIL_TO_REGION in that case."""
    postcode = _extract_postcode(address)
    if not postcode:
        return None
    outcode = postcode.split(" ")[0]
    if outcode in _REGION_BY_OUTCODE_CACHE:
        return _REGION_BY_OUTCODE_CACHE[outcode]
    try:
        resp = requests.get(
            f"https://api.postcodes.io/postcodes/{postcode.replace(' ', '')}",
            timeout=3,
        )
        if resp.status_code == 200:
            result = resp.json().get("result") or {}
            # England postcodes carry `region` (e.g. "South East"); Wales/
            # Scotland/NI don't -- `country` is the correct region-level
            # answer for those instead (e.g. "Wales", "Scotland").
            region = result.get("region") or result.get("country")
            if region:
                _REGION_BY_OUTCODE_CACHE[outcode] = region
                return region
    except Exception as e:
        logger.debug(f"[LeadTags] postcodes.io region lookup failed for {postcode}: {e}")
    return None


def _resolve_region(address: str, council_source: str) -> str:
    """Real ONS region for a lead, resolved in order of trust: (1) the
    address's own postcode via postcodes.io -- ground truth, independent of
    the scraper's own labelling; (2) COUNCIL_TO_REGION, for the rare
    address with no extractable postcode; (3) 'unclassified', logged as a
    warning so it gets reviewed rather than silently swallowed. Per Nick
    (Sep 2 2026): a lead we can't confidently place is effectively a dead
    lead, so this path should stay vanishingly rare, not the common case it
    was before postcode lookup existed."""
    region = _lookup_region_via_postcode(address)
    if region:
        return region
    key = str(council_source or "").strip().upper()
    region = COUNCIL_TO_REGION.get(key)
    if region:
        return region
    # Sep 2 2026 fix: live logs showed most of the remaining unclassified
    # leads had NO postcode in the address (land-parcel/site descriptions
    # like "Pine Walk Shaftesbury" rather than a building address) AND a
    # council_source that's already a clean region name -- MESH's
    # region-town sources pass the region itself straight through as
    # council_source instead of a specific council. COUNCIL_TO_REGION never
    # had those as keys (it maps councils/towns TO a region, not a region
    # to itself), so every one of these fell through to unclassified even
    # though the answer was sitting right there in council_source.
    if key in _CANONICAL_REGIONS_UPPER:
        return _CANONICAL_REGIONS_UPPER[key]
    alias = _REGION_NAME_ALIASES.get(key)
    if alias:
        return alias
    logger.warning(
        f"[LeadTags] Could not resolve region for address={address!r} "
        f"council_source={council_source!r} -- tagged unclassified."
    )
    return "unclassified"


def _slugify_tag(value: str) -> str:
    """'Hammersmith & Fulham' -> 'hammersmith-fulham'. Used for locale/region
    tag values so they're consistent, lowercase, and safe to use in a URL
    query string."""
    import re as _re
    v = str(value or "").strip().lower()
    v = _re.sub(r"[^a-z0-9]+", "-", v).strip("-")
    return v or "unknown"


def _generate_tags(address: str, summary: str, council_source: str, vertical: str,
                    lead_score: str, has_agent: Optional[bool]) -> list:
    """Builds the full 'floating bubble' tag list for one lead. Every tag is
    an independent fact about the lead -- callers filter by combining
    whichever ones they want (database.get_leads_by_tags), they aren't a
    single mutually-exclusive category."""
    tags = [f"vertical:{vertical}"]
    if lead_score:
        tags.append(f"size:{_slugify_tag(lead_score)}")
    if has_agent is True:
        tags.append("agent:yes")
    elif has_agent is False:
        tags.append("agent:no")
    else:
        tags.append("agent:unconfirmed")
    if council_source:
        tags.append(f"locale:{_slugify_tag(council_source)}")
    tags.append(f"region:{_slugify_tag(_resolve_region(address, council_source))}")
    if vertical == "tree":
        combined_text = f"{summary or ''} {address or ''}"
        for job_type in _classify_job_types(combined_text):
            tags.append(f"job:{job_type}")
    return tags


def _insert_lead(cur, reference: str, address: str, summary: str, source: str,
                  applicant_name: Optional[str] = None, agent_name: Optional[str] = None,
                  agent_company: Optional[str] = None, has_agent: Optional[bool] = None,
                  agent_is_tree_surgeon: Optional[bool] = None,
                  vertical: str = "tree") -> Optional[dict]:
    """
    Inserts a lead into the DB. Returns the lead dict if new, None if duplicate or low-quality junk.
    Enforces a strict quality gate: blocks empty, generic placeholders like 'tree-preservation-order'.

    applicant_name / agent_name / agent_company / has_agent (Aug 30 2026): whether this
    application already names a contractor as its Agent. Only the mesh (Idox) scanner
    currently populates these -- it visits each application's own page, not just the
    search-results list, to read them. Other scan paths pass None/leave has_agent NULL,
    which the UI/exports must treat as "unknown", not "no agent" -- those are different
    things and conflating them would misrepresent leads we simply haven't checked yet.

    Backfill note: the daily scan re-finds the same still-pending applications on
    every run (a TPO application stays in the council's "recent" search for weeks),
    so most of what a run turns up on any given day are references already in the
    table from an earlier day -- ON CONFLICT DO NOTHING alone would silently skip
    those forever and this new data would never reach a single existing row. Fixed
    below with DO UPDATE ... COALESCE: an existing row gets these 4 fields filled
    in the first time they're seen (never overwritten once set), while `was_inserted`
    (Postgres' xmax=0 trick) keeps the return value None for a backfill-only touch,
    so callers' "is this a brand-new lead to notify about" logic is unaffected.

    vertical (Sep 2 2026): which VERTICALS key this lead was classified
    into -- defaults to "tree" so every call site written before this
    parameter existed keeps inserting tree leads exactly as before. Also
    now governs data minimization: a vertical configured with
    capture_identity=False (currently only "hmo") has applicant_name,
    agent_name, agent_company, has_agent and agent_is_tree_surgeon all
    forced to None before the INSERT, regardless of what the caller passed
    in -- see the capture_identity block below and VERTICALS' own comment
    for why this lives here rather than at each call site.
    """
    if not summary or not reference:
        return None

    s_clean = summary.strip().lower()
    # Reject generic placeholders that lack actionable details for contractors
    if s_clean in ["tree-preservation-order", "tpo", "work to trees", "works to trees", "tree work", "tree works", "trees"]:
        return None
    if len(s_clean) < 12:
        return None

    addr_clean = address.strip() if address else ""
    if not addr_clean or addr_clean.lower() in ["greater london", "london", "uk", "england"]:
        # If address is completely generic, require higher description detail to avoid useless leads
        if len(s_clean) < 20:
            return None

    # Sep 2 2026, master_expansion_plan_v2.md build-order step 3 (the
    # GDPR-safe lead format): a vertical with capture_identity=False never
    # gets an applicant/agent identity stored at all, however a caller
    # invoked this function -- enforced here, at the one place every lead
    # of every vertical passes through, rather than trusting every current
    # and future call site to remember not to pass these fields for HMO.
    # This is what "built in from day one, not retrofitted" actually means
    # in code: it is not possible for an HMO row to ever end up with a name
    # in it, regardless of what any scan function's PlanIt/paid-API/mesh
    # extraction logic happens to pull out of the source record.
    if not VERTICALS.get(vertical, {}).get("capture_identity", True):
        applicant_name = None
        agent_name = None
        agent_company = None
        has_agent = None
        agent_is_tree_surgeon = None

    lead_score, lead_price = score_lead(summary)
    tags = _generate_tags(address, summary, source, vertical, lead_score, has_agent)
    try:
        cur.execute(
            """
            INSERT INTO leads (reference, address, summary, council_source, lead_score, lead_price,
                                applicant_name, agent_name, agent_company, has_agent, agent_is_tree_surgeon,
                                vertical, tags)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (reference) DO UPDATE SET
                applicant_name = COALESCE(leads.applicant_name, EXCLUDED.applicant_name),
                agent_name     = COALESCE(leads.agent_name, EXCLUDED.agent_name),
                agent_company  = COALESCE(leads.agent_company, EXCLUDED.agent_company),
                has_agent      = COALESCE(leads.has_agent, EXCLUDED.has_agent),
                agent_is_tree_surgeon = COALESCE(leads.agent_is_tree_surgeon, EXCLUDED.agent_is_tree_surgeon),
                tags = CASE WHEN leads.tags IS NULL OR leads.tags = '{}' THEN EXCLUDED.tags ELSE leads.tags END
            RETURNING id, (xmax = 0) AS was_inserted;
            """,
            (reference, address, summary[:350], source, lead_score, lead_price,
             applicant_name, agent_name, agent_company, has_agent, agent_is_tree_surgeon, vertical, tags)
        )
    except Exception as e:
        # Sep 2 2026 (production incident fix, extended for the new `tags`
        # column the same day): either the `vertical` or `tags` column's own
        # migration can be delayed by lock contention (see
        # database._run_ddl_statements_resiliently's docstring for the exact
        # incident this guards against) -- without this fallback, EVERY lead
        # insert across BOTH verticals fails with "column ... does not
        # exist" until that migration lands, taking lead capture to zero.
        # Detect specifically that failure mode and fall back to the
        # pre-Sep-2 11-column INSERT (both columns default correctly once
        # they exist, so no data is misrepresented in the meantime -- a lead
        # inserted this way just isn't tagged/vertical-classified until the
        # column lands and a rescan or backfill fixes it). Any OTHER error
        # still propagates unchanged.
        if "vertical" not in str(e).lower() and "tags" not in str(e).lower():
            raise
        cur.connection.rollback()
        logger.warning(
            f"[_insert_lead] 'vertical'/'tags' column not available yet ({e}) -- "
            f"falling back to legacy INSERT without them for reference={reference!r}."
        )
        cur.execute(
            """
            INSERT INTO leads (reference, address, summary, council_source, lead_score, lead_price,
                                applicant_name, agent_name, agent_company, has_agent, agent_is_tree_surgeon)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (reference) DO UPDATE SET
                applicant_name = COALESCE(leads.applicant_name, EXCLUDED.applicant_name),
                agent_name     = COALESCE(leads.agent_name, EXCLUDED.agent_name),
                agent_company  = COALESCE(leads.agent_company, EXCLUDED.agent_company),
                has_agent      = COALESCE(leads.has_agent, EXCLUDED.has_agent),
                agent_is_tree_surgeon = COALESCE(leads.agent_is_tree_surgeon, EXCLUDED.agent_is_tree_surgeon)
            RETURNING id, (xmax = 0) AS was_inserted;
            """,
            (reference, address, summary[:350], source, lead_score, lead_price,
             applicant_name, agent_name, agent_company, has_agent, agent_is_tree_surgeon)
        )
    row = cur.fetchone()
    if row and row[1]:  # was_inserted -- a genuinely new lead, not a backfill of an existing one
        return {"ref": reference, "addr": address, "summary": summary,
                "lead_score": lead_score, "lead_price": lead_price, "tags": tags,
                "applicant_name": applicant_name, "agent_name": agent_name,
                "agent_company": agent_company, "has_agent": has_agent,
                "agent_is_tree_surgeon": agent_is_tree_surgeon, "vertical": vertical}
    return None


# ── Leeds Scanner (ArcGIS + Yorkshire Regional Councils) ──────────────────────

# Aug 30 2026: Nick flagged that troubleshooting/manual re-triggers of the
# pipeline (redeploys, manual /scan-nationwide calls, active testing --
# exactly what a heavy development day like today looks like) were each
# separately hammering all 50+ real council government websites in
# COUNCIL_REGISTRY a second, third, fourth time in the same day. Unlike
# ukplanningapi.co.uk's monthly quota (a money problem, fixed above with
# rotation), this is a good-citizen problem: these are small councils'
# own servers, not built to be scraped repeatedly in one afternoon, and
# the "same portal hit by two overlapping scans within seconds" pattern
# is already on record (see _dispatch_locked_scan in main.py) as a likely
# cause of real 503s/timeouts. Same-day dedup.
#
# Sep 1 2026: this was an in-memory `_MESH_SCAN_DAY_CACHE` global, on the
# stated assumption that "Render restarts are rare compared to daily cron
# runs". That assumption held in steady state but broke down hard during
# this exact week of active development -- a live log showed the paid-API
# rotation's own identical in-memory guard (_PAID_API_DAY_CACHE, see below)
# already having let a single day's usage hit ~2x its intended pace, and
# the redeploy frequency this week is the same for all three of these day
# caches, this one included. Every redeploy silently re-enables one more
# full 50+-council sweep the same calendar day -- on a council-goodwill
# guard rather than a hard money cap, but the reasoning that broke the paid
# API guard applies equally here. Switched to persistent_dedup_cache,
# backed by the same Postgres DB this function already opens a connection
# to, so the guard survives a redeploy instead of resetting with it.
_MESH_SCAN_DEDUP_KEY = "mesh_scan:full_sweep"


def run_mesh_network_scan() -> int:
    """
    Executes a direct scan of all councils mapped in the Aggregator Mesh (Idox portals, etc.)
    Bypasses the third-party paid API entirely to save quota.
    """
    try:
        import mesh_scrapers
    except ImportError:
        logger.error("[MESH] mesh_scrapers.py not found.")
        return 0

    new_leads = []
    conn = database.get_db_conn()
    cur = conn.cursor()

    dedup.ensure_table(conn)
    if dedup.already_done_today(conn, _MESH_SCAN_DEDUP_KEY):
        logger.info(
            "[MESH] Already ran the full council sweep once today (any process, "
            "including one that has since restarted) -- skipping this re-trigger "
            "rather than hitting all 50+ council websites again. This guard has no "
            "override since re-scraping free council sites has no quota to spend, "
            "only their goodwill."
        )
        cur.close()
        conn.close()
        return 0
    try:
        for council_name, url in mesh_scrapers.COUNCIL_REGISTRY.items():
            logger.info(f"[MESH] Scraping {council_name} directly from {url}...")
            # We add an artificial delay to respect council rate limits
            import time
            time.sleep(2)
            
            leads = mesh_scrapers.scrape_mesh_council(council_name)
            for lead in leads:
                ref = lead.get("reference")
                addr = lead.get("address")
                desc = lead.get("description")
                if not ref or not desc:
                    continue

                inserted = _insert_lead(
                    cur, ref, addr, desc, council_name.title(),
                    applicant_name=lead.get("applicant_name"),
                    agent_name=lead.get("agent_name"),
                    agent_company=lead.get("agent_company"),
                    has_agent=lead.get("has_agent"),
                    agent_is_tree_surgeon=lead.get("agent_is_tree_surgeon"),
                    # Sep 2 2026: mesh_scrapers now tags each lead with its
                    # resolved vertical (tree or hmo) once a council has HMO
                    # search terms enabled -- defaults to "tree" only for
                    # leads from councils/paths that never ran an HMO search
                    # at all, matching this call site's original behaviour
                    # exactly for every council not in
                    # COUNCILS_WITH_CONFIRMED_HMO_ARTICLE_4.
                    vertical=lead.get("vertical", "tree"),
                )
                if inserted:
                    new_leads.append(inserted)
            conn.commit()
    except Exception as e:
        logger.error(f"[MESH] Fatal error during mesh scan: {e}")
    finally:
        cur.close()
        conn.close()

    if new_leads:
        notifications.dispatch_lead_alerts("MESH-NATIONWIDE", new_leads)

    # Mark today's sweep as done regardless of outcome -- a same-day retry
    # wouldn't fix a real council-side outage anyway, and the goal here is
    # strictly "at most one full council sweep per calendar day". Persisted
    # (see _MESH_SCAN_DEDUP_KEY comment above) so it survives a redeploy.
    dedup_conn = database.get_db_conn()
    dedup.mark_done_today(dedup_conn, _MESH_SCAN_DEDUP_KEY)
    dedup_conn.close()

    logger.info(f"[MESH] Mesh Scan complete. {len(new_leads)} free leads extracted directly from councils.")
    return len(new_leads)


def scan_leeds_leads() -> int:
    """
    Scans both:
    1. Leeds City Council ArcGIS MapServer Layer 12 (15-mile spatial boundary)
    2. Surrounding Yorkshire councils (Bradford, Wakefield, Kirklees, Calderdale,
       York, Harrogate, North Yorkshire) -- delegated to scan_city_planning_api("Leeds").

    Aug 30 2026: part 2 used to carry its own hardcoded copy of these exact
    Yorkshire postcode prefixes (LS, BD, WF, HX, HD, YO, HG, HU, DL, TS --
    identical to CITY_POSTCODE_PREFIX["Leeds"]) in a raw loop straight
    against ukplanningapi.co.uk, with no rotation, no same-day dedup, and
    no 429/quota-aware logging. This function is only reachable via
    scan_nationwide_bulk_crawler()'s "Leeds" special-case (manual/admin
    endpoints), so every manual trigger of it was completely bypassing the
    quota-headroom and good-citizen fixes added to scan_city_planning_api()
    above -- found while checking whether any other code path had the same
    gap as scan_london_leads() (which had the identical problem, fixed the
    same way just above). Delegating here closes that gap and picks up
    PlanIt coverage for Leeds as a bonus (this function never queried
    PlanIt directly before).
    """
    new_leads = []
    conn = database.get_db_conn()
    cur = conn.cursor()

    # 1. Leeds Council ArcGIS Server Query
    url = "https://mapservices.leeds.gov.uk/arcgis/rest/services/Public/Planning/MapServer/12/query"
    params = {
        "where":        "1=1",
        "outFields":    "*",
        "geometry":     "-1.5491,53.8008",
        "geometryType": "esriGeometryPoint",
        "inSR":         "4326",
        "spatialRel":   "esriSpatialRelIntersects",
        "distance":     24140,          # 15 miles in metres
        "units":        "esriSRUnit_Meter",
        "resultRecordCount": 200,
        "f": "json"
    }
    try:
        res = net_utils.smart_get(url, params=params, timeout=20)
        if res.status_code == 200:
            features = res.json().get("features", [])
            for feature in features:
                rec = feature.get("attributes", {})
                summary = str(rec.get("DESCRIPTION") or "")
                real_ref = str(rec.get("REFERENCE") or rec.get("OBJECTID") or "").strip()
                vertical = _resolve_vertical(summary)
                if vertical is None:
                    # Sep 2 2026, Tier 4: same reasoning as the GLA loop below --
                    # only queue with a real, stable reference, never a fabricated
                    # one that would flood the queue with duplicates every run.
                    if real_ref:
                        _queue_for_manual_review(real_ref, rec.get("ADDRESS") or "Leeds", summary, "Leeds")
                    continue
                ref = real_ref or f"LDS-{int(time.time())}"
                addr = rec.get("ADDRESS") or "Leeds"
                lead = _insert_lead(cur, ref, addr, summary, "Leeds", vertical=vertical)
                if lead:
                    new_leads.append(lead)
    except Exception as e:
        logger.debug(f"[Leeds ArcGIS] Error: {e}")

    conn.commit()
    cur.close()
    conn.close()

    # 2. Surrounding Yorkshire councils -- delegated (see docstring above),
    # so it inherits rotation + same-day dedup automatically.
    yorkshire_count = scan_city_planning_api("Leeds")

    if new_leads:
        notifications.dispatch_lead_alerts("Leeds", new_leads)
    total = len(new_leads) + yorkshire_count
    logger.info(
        f"[Leeds] Scan complete. {total} new leads found "
        f"({len(new_leads)} via ArcGIS, {yorkshire_count} via Yorkshire radar)."
    )
    return total



# ── London Scanner (GLA Datahub + Complete London & Green Belt Postcodes) ──────

# Aug 30 2026: same-day dedup, same reasoning as the mesh scan above --
# a single request per call, so the stakes are much lower, but there's no
# reason to hit a re-trigger's worth of extra calls against someone else's
# free government API either. Sep 1 2026: switched from an in-memory dict
# to persistent_dedup_cache for the same reason as the other three day
# caches in this file -- see the paid-API dedup comment further down for
# the live-log evidence that "restarts are rare" doesn't hold on an active
# development day.
_GLA_DEDUP_KEY = "gla_datahub:london"


def scan_gla_datahub_london() -> int:
    """
    Aug 30 2026: extracted out of scan_london_leads() below. That function's
    part 2 duplicates ALL of its own postcode-prefix logic against the now-
    hardened scan_city_planning_api() -- same 29 London/Home-Counties
    prefixes (see CITY_POSTCODE_PREFIX["London"] further down), same
    ukplanningapi.co.uk endpoint, just without the 429 backoff, aggregate-
    failure warning, or dedup this file's Aug 30 hardening pass added
    everywhere else. Stage 1 of the daily pipeline (main.py's
    run_master_daily_pipeline) already calls scan_city_planning_api("London")
    for that exact coverage every single day, so re-running scan_london_
    leads()'s part 2 there would double-fetch the same prefixes and burn
    through the 500/month free-tier quota twice as fast for zero new leads.

    The ONE genuinely distinct, non-duplicated piece is this: the free
    London GLA Planning Datahub (planningdata.london.gov.uk) -- a separate
    government open-data API, not ukplanningapi.co.uk or PlanIt, that this
    project has had built and working since before this hardening pass.
    But scan_london_leads() itself was never actually wired into the
    scheduled daily pipeline (it's only reachable from three manual/admin-
    triggered endpoints via scan_nationwide_bulk_crawler()) -- so this free
    third source has been sitting unused on every automated daily run.
    Pulled out on its own so Stage 1 can call it directly for the London
    region, ADDITIONALLY to (not instead of) the existing
    scan_city_planning_api("London") call -- exactly the "more free sources
    to spread the request load across" strategy this was built for.
    """
    if not GLA_API_KEY:
        return 0

    new_leads = []
    conn = database.get_db_conn()
    cur = conn.cursor()

    dedup.ensure_table(conn)
    if dedup.already_done_today(conn, _GLA_DEDUP_KEY):
        logger.debug("[London GLA] Already queried once today (any process, including one that has since restarted) -- skipping re-trigger.")
        cur.close()
        conn.close()
        return 0
    dedup.mark_done_today(conn, _GLA_DEDUP_KEY)
    try:
        headers = {"Authorization": GLA_API_KEY, "Accept": "application/json"}
        import time
        time.sleep(1.0)  # London throttle
        res = net_utils.smart_get(
            "https://planningdata.london.gov.uk/api/applications",
            params={"limit": 100},
            headers=headers,
            timeout=15
        )
        if res.status_code in (401, 403):
            notifications.send_system_incident_alert(
                category="SECURITY & API KEYS",
                title="LONDON GLA PLANNING DATAHUB TOKEN INVALID",
                description="CRITICAL: London GLA Planning Datahub rejected requests with HTTP 401/403 Unauthorized.",
                impact="Planning lead scraping across all 32 London Boroughs via the free GLA Datahub is paused.",
                action_required="Check GLA_API_KEY in Render and regenerate token at planningdata.london.gov.uk.",
                severity="CRITICAL",
                throttle_hours=6.0
            )
        elif res.status_code == 200:
            records = res.json().get("data", [])
            for item in records:
                try:
                    # Search across all possible GLA description fields to avoid placeholder names.
                    # Sep 2 2026: coerce with str(... or "") before .strip() -- a truthy
                    # non-string value (int, nested dict) in any of these fields used to
                    # raise AttributeError straight out of this loop for every remaining
                    # item in this batch; see the per-item try/except added below for why
                    # one bad record doing that is now bounded to just that one record.
                    summary = str(
                        item.get("description")
                        or item.get("proposal")
                        or item.get("development_description")
                        or item.get("details")
                        or item.get("proposal_summary")
                        or item.get("title")
                        or ""
                    ).strip()

                    # Extract nested or flat address
                    addr = ""
                    if isinstance(item.get("location"), dict):
                        addr = item["location"].get("address", "")
                    elif isinstance(item.get("site"), dict):
                        addr = item["site"].get("address", "")
                    if not addr:
                        addr = item.get("site_address") or item.get("address") or item.get("address_text") or "London"

                    real_ref = (
                        item.get("reference")
                        or item.get("application_reference")
                        or item.get("lpa_app_no")
                        or item.get("planning_reference")
                        or ""
                    )

                    vertical = _resolve_vertical(summary) if summary else None
                    if vertical is None:
                        # Sep 2 2026, Tier 4 (manual review queue): only queue when a
                        # real, stable reference exists -- a fabricated one (the
                        # f"LON-{time.time()}" fallback used for the actual lead
                        # insert below) would never dedupe day to day and would
                        # flood the queue with a fresh duplicate row for the same
                        # still-live application every single scan run.
                        if real_ref:
                            _queue_for_manual_review(real_ref, addr, summary, "London")
                        continue

                    ref = real_ref or f"LON-{int(time.time())}"
                    lead = _insert_lead(cur, ref, addr, summary, "London", vertical=vertical)
                except Exception as e:
                    # Sep 2 2026: one malformed GLA record must never cost the rest of
                    # this batch (up to 100 records) -- caught here instead of only at
                    # the outer `except Exception` below, which used to let a single bad
                    # item stop the whole loop partway through.
                    logger.warning(f"[London GLA] Skipping one malformed record: {e}")
                    continue
                if lead:
                    new_leads.append(lead)
        else:
            logger.debug(f"[London GLA] HTTP {res.status_code}")
    except Exception as e:
        logger.error(f"[London GLA] Error: {e}")
    finally:
        conn.commit()
        cur.close()
        conn.close()

    if new_leads:
        notifications.dispatch_lead_alerts("London", new_leads)
    logger.info(f"[London GLA Datahub] Scan complete. {len(new_leads)} tree-related leads found.")
    return len(new_leads)


def scan_london_leads() -> int:
    """
    Scans London & Green Belt planning applications:
    1. London GLA Planning Datahub (deep multi-field extraction across all 32 London Boroughs)
    2. Comprehensive UK Planning API & PlanIt radar covering all Inner & Outer London + Home Counties postcodes:
       (SW, SE, NW, N, E, EC, WC, CR, BR, EN, HA, UB, KT, TW, DA, RM, IG, SM, RH, TN, GU, CM, SS, SL, HP, AL, SG, WD, ME).

    Aug 30 2026: only reachable via scan_nationwide_bulk_crawler() (the
    manual /scan-nationwide-style endpoints), never from the scheduled
    daily pipeline. Part 1 (GLA Datahub) is now shared with Stage 1's own
    direct call via scan_gla_datahub_london() above -- kept here too so
    this function's existing manual-trigger callers keep working exactly
    as before, without duplicating the GLA-fetching code itself.

    Part 2 used to carry its own hardcoded copy of these exact 29 London/
    Home-Counties postcode prefixes (identical to CITY_POSTCODE_PREFIX
    ["London"]) in a raw loop straight against ukplanningapi.co.uk -- no
    rotation, no same-day dedup, no 429/quota-aware logging. Since this
    function is only reachable via manual/admin endpoints, every manual
    trigger of it completely bypassed the quota-headroom fixes added to
    scan_city_planning_api() -- exactly the gap Nick asked about ("will
    that cover it though?"). Delegating here closes it and picks up
    PlanIt coverage for London as a bonus (this loop never queried PlanIt
    directly before).
    """
    gla_count = scan_gla_datahub_london()  # runs + inserts + dispatches alerts on its own connection

    # 2. Surrounding London & Home Counties radar -- delegated (see
    # docstring above), so it inherits rotation + same-day dedup + PlanIt
    # automatically instead of duplicating a second, unprotected copy.
    radar_count = scan_city_planning_api("London")

    total = radar_count + gla_count
    logger.info(
        f"[London] Scan complete. {total} high-quality leads found across London & Green Belt "
        f"councils ({gla_count} via GLA Datahub, {radar_count} via UK Planning API + PlanIt radar)."
    )
    return total



# ── UK Planning API Scanner (Birmingham, Manchester, Bristol, Sheffield) ──────
# Uses ukplanningapi.co.uk — covers 289 UK councils, updated daily.

UK_PLANNING_API_KEY = os.getenv("UK_PLANNING_API_KEY", "").strip()

# Exhaustive regional postcode prefixes covering England, Scotland, and Wales
CITY_POSTCODE_PREFIX = {
    # 1. Greater London
    "London":          ["SW", "SE", "NW", "N", "E", "EC", "WC", "CR", "BR", "EN", "HA", "UB", "KT", "TW", "DA", "RM", "IG", "SM", "RH", "TN", "GU", "CM", "SS", "SL", "HP", "AL", "SG", "WD", "ME"],
    # 2. South East & Home Counties
    "South East":      ["RH", "TN", "GU", "ME", "CT", "BN", "SO", "PO", "OX", "RG", "MK", "HP", "AL", "SG", "WD", "SL"],
    # 3. South West & West Country (Devon, Cornwall, Somerset, Dorset, Wiltshire, Gloucestershire)
    "South West":      ["BS", "BA", "GL", "SN", "TA", "DT", "SP", "EX", "TQ", "PL", "TR", "BH"],
    "Bristol":         ["BS", "BA", "GL", "SN", "TA", "DT", "SP"],
    "Cornwall":        ["TR", "PL"],
    "Devon":           ["EX", "TQ", "PL"],
    # 4. West Midlands
    "West Midlands":   ["B", "WS", "WV", "DY", "CV", "WR", "TF", "ST", "HR", "SY"],
    "Birmingham":      ["B", "WS", "WV", "DY", "CV", "WR", "TF", "ST", "HR", "SY"],
    # 5. East Midlands
    "East Midlands":   ["S", "DE", "NG", "LE", "LN", "NN", "PE"],
    "Sheffield":       ["S", "DN", "DE", "NG", "LN", "LE"],
    # 6. Yorkshire & The Humber
    "Yorkshire":       ["LS", "BD", "WF", "HX", "HD", "YO", "HG", "HU", "DL", "TS", "DN"],
    "Leeds":           ["LS", "BD", "WF", "HX", "HD", "YO", "HG", "HU", "DL", "TS"],
    # 7. North West & Cumbria Lake District
    "North West":      ["M", "SK", "WA", "WN", "BL", "OL", "CW", "L", "PR", "BB", "FY", "CH", "LA", "CA"],
    "Manchester":      ["M", "SK", "WA", "WN", "BL", "OL", "CW", "L", "PR", "BB", "FY", "CH"],
    "Cumbria":         ["CA", "LA"],
    # 8. North East
    "North East":      ["NE", "SR", "DH", "TS", "DL"],
    "Newcastle":       ["NE", "SR", "DH", "TS", "DL"],
    # 9. East of England (East Anglia, Norfolk, Suffolk, Essex, Beds, Herts, Cambs)
    "East of England": ["CM", "SS", "CO", "CB", "PE", "NR", "IP", "LU", "SG"],
    "Cambridge":       ["CM", "SS", "CO", "CB", "PE", "NR", "IP", "LU", "SG"],
    "Norfolk":         ["NR", "IP", "PE"],
    # 10. Scotland (All 32 Scottish Councils / Central Belt, Borders, Highlands & Islands)
    "Scotland":        ["EH", "G", "AB", "DD", "IV", "KW", "PA", "PH", "FK", "KY", "ML", "TD", "DG", "ZE", "HS"],
    "Edinburgh":       ["EH", "KY", "FK", "TD"],
    "Glasgow":         ["G", "PA", "ML", "KA", "DG"],
    "Aberdeen":        ["AB", "DD", "IV", "PH", "KW"],
    # 11. Wales (All 22 Welsh Councils / South Wales, Mid Wales, North Wales)
    "Wales":           ["CF", "SA", "NP", "LL", "LD", "SY"],
    "Cardiff":         ["CF", "NP", "SA"],
    "Swansea":         ["SA", "CF", "LD"],
    "North Wales":     ["LL", "SY", "CH"]
}

# Aug 30 2026: real town/local-authority names for the free PlanIt fallback
# (planit.org.uk), added after discovering the fallback was silently broken --
# it was calling PlanIt's API with the wrong parameter name (`postcode`
# instead of `pcode`) and no required search radius, AND even fixed, the
# bare 1-2 letter area codes in CITY_POSTCODE_PREFIX above (e.g. "B", "WS")
# aren't valid UK postcodes/outcodes for PlanIt to geocode -- confirmed live
# (`{"error": "pcode: Invalid format"}`). PlanIt instead supports searching
# directly by real authority name (`auth=<name>`), confirmed live against
# Birmingham and Walsall (both returned real, current applications). These
# lists reuse genuine UK council/authority names already used elsewhere in
# this codebase (COUNCIL_REGISTRY, bulk_contractor_extractor.py's
# UK_TARGET_REGIONS) -- not exhaustive, but real and verified-working,
# unlike the broken prefix approach it replaces.
REGION_TOWNS = {
    "London": ["Westminster", "Camden", "Islington", "Lambeth", "Southwark", "Wandsworth",
               "Barnet", "Brent", "Ealing", "Croydon", "Bromley", "Greenwich", "Haringey"],
    "South East": ["Guildford", "Reading", "Brighton and Hove", "Portsmouth", "Southampton",
                   "Oxford", "Winchester", "Maidstone", "Tunbridge Wells", "Sevenoaks"],
    "South West": ["Bristol", "Bath and North East Somerset", "Gloucester", "Cheltenham",
                   "Exeter", "Plymouth", "Cornwall", "Dorset", "Wiltshire", "Swindon"],
    "West Midlands": ["Birmingham", "Coventry", "Wolverhampton", "Solihull", "Dudley",
                       "Walsall", "Warwick", "Stoke-on-Trent"],
    "East Midlands": ["Nottingham", "Leicester", "Derby", "Northampton", "Lincoln"],
    "Yorkshire": ["Leeds", "Sheffield", "Bradford", "York", "Wakefield"],
    "North West": ["Manchester", "Liverpool", "Preston", "Blackpool", "Cheshire East",
                    "Cheshire West and Chester"],
    "North East": ["Newcastle upon Tyne", "Sunderland", "Durham", "Middlesbrough", "Darlington"],
    "East of England": ["Norwich", "Cambridge", "Milton Keynes", "Peterborough", "Colchester"],
    "Leeds": ["Leeds"],
    "Birmingham": ["Birmingham"],
    "Manchester": ["Manchester"],
    "Bristol": ["Bristol"],
    "Sheffield": ["Sheffield"],
    "Scotland": ["Edinburgh", "Glasgow", "Aberdeen City", "Dundee City", "Fife",
                 "Stirling", "Perth and Kinross"],
    "Wales": ["Cardiff", "Swansea", "Newport", "Wrexham", "Bridgend"],
}

# Values PlanIt returns as a placeholder when it hasn't actually captured a
# field (confirmed live: e.g. "agent_name": "See source") -- must not be
# stored as if it were a real name.
_PLANIT_PLACEHOLDER_VALUES = {
    "see source", "n/a", "none", "", "not available", "not known", "unknown",
    "n a", "not applicable", "not given", "not provided", "tbc", "to be confirmed",
    "-", "--",
}


def _planit_real_value(value) -> Optional[str]:
    """Returns value if it looks like a genuine PlanIt field, else None."""
    if not value or not isinstance(value, str):
        return None
    if value.strip().lower() in _PLANIT_PLACEHOLDER_VALUES:
        return None
    return value.strip()


# See scan_city_planning_api's rotation/dedup comment. Marks a region's
# paid-API rotation bucket as already attempted today so a same-day manual
# re-trigger doesn't burn quota re-fetching it for zero new coverage.
#
# Sep 1 2026: BUG FOUND SAME DAY -- this and _PLANIT_DAY_CACHE below were
# in-memory dicts, on the stated assumption (Aug 30 comment, since removed)
# that "Render restarts are rare compared to daily cron runs". A live log
# from today caught the real-world failure mode directly: day 1 of the
# month, a single region's rotation share was only 3 prefixes, yet the
# cumulative monthly counter had already reached 31 and triggered a
# predictive-pace alert projecting ~930 calls against the 500 cap -- nearly
# double the ~460/month the rotation was deliberately sized for (see
# scan_city_planning_api's Aug 30 comment: 178 prefixes / 12-day rotation).
# The gap is explained by today's actual deploy pattern: this was an active
# development day with several redeploys, each one silently wiping this
# in-memory guard and re-enabling a full paid-API pass on the very next
# trigger, for every region that re-ran. "Restarts are rare" is only true
# in steady state -- it's false on exactly the days most likely to matter,
# since active development IS frequent redeploys. Switched both this and
# _PLANIT_DAY_CACHE to persistent_dedup_cache (backed by the same Postgres
# DB the api_usage counter already lives in) so the guard survives a
# redeploy instead of resetting with it.
_PAID_API_DEDUP_PREFIX = "paid_api_rotation"

# Same idea, applied to PlanIt. PlanIt has no monthly money quota to
# protect (unlike ukplanningapi.co.uk above), but Nick's "these keep
# pinging planning data software sites" concern applies here too --
# it's still someone else's free public API, and a same-day re-trigger
# gains nothing by re-querying the exact same authority names again.
# Unlike the paid API there's no rotation, just a flat "once per region
# per day" -- PlanIt isn't rationed by a monthly cap, only by its own
# per-request rate limit (handled with backoff inside fetch_planit).
_PLANIT_DEDUP_PREFIX = "planit_region"

# Aug 30 2026: root cause of PlanIt returning 429 for nearly every authority
# in 7 of 8 regions in one production run, even with the earlier "wait 20s
# and retry once" fix in place. That fix had two problems: (1) it capped
# the wait at min(Retry-After, 20s) -- if PlanIt's server genuinely asked
# for longer, the code ignored it and retried too soon anyway; (2) the
# throttle (time.sleep(1.5) inside fetch_planit) was purely LOCAL to one
# region's ThreadPoolExecutor call -- it had zero memory of how many PlanIt
# requests the previous 15 (of 16) ALL_CITIES regions had already made in
# the same run. PlanIt's rate limiter is IP-based, not aware of TreeKey's
# internal region groupings, so 16 regions run back-to-back each resetting
# their own "1.5s since my last request" clock will still blow through
# PlanIt's real limit well before the last few regions are reached.
#
# Fix: one shared lock + one shared "last request" timestamp for the whole
# process, so EVERY PlanIt request -- whichever region's fetch_planit is
# calling it -- waits out the same minimum gap from the previous one,
# process-wide. Defaults to 60s, matching PlanIt's own documented "one
# request per minute" safe-rate guidance (confirmed via their FAQ).
PLANIT_MIN_INTERVAL_SECONDS = float(os.getenv("PLANIT_MIN_INTERVAL_SECONDS", "60") or "60")

# Aug 30 2026: caps how many real agent-status confirmation fetches
# (mesh_scrapers.confirm_agent_status_from_source, following PlanIt's own
# source-authority link) one scan_city_planning_api() call will attempt --
# each one is a genuine new HTTP request straight to that specific
# authority's own server. Raised from an initial cautious 15 to 200 once a
# DB check (see the PlanIt insertion loop) started skipping any reference
# that's already resolved from a previous day WITHOUT spending budget or a
# network call -- that's what makes a generous number safe as a permanent
# setting rather than a one-off: it only ever gets spent on genuinely new
# or still-unresolved leads, which is a small, naturally shrinking set once
# the existing backlog clears, not "200 real requests every single day
# forever". Nick's explicit ask (Aug 30 2026): needed the current ~1,200-lead
# backlog checked in one pass today, not trickled in over days/weeks -- this
# is what makes that one full nationwide run actually cover most of it.
PLANIT_AGENT_CONFIRM_LIMIT = int(os.getenv("PLANIT_AGENT_CONFIRM_LIMIT", "1000") or "1000")
_PLANIT_PACING_LOCK = threading.Lock()
_PLANIT_LAST_REQUEST_AT: float = 0.0

# Aug 31 2026: production incident -- after PLANIT_MIN_INTERVAL_SECONDS was
# lowered to 10s, PlanIt returned a 429 with Retry-After: 20070 (5.6 hours),
# a real hard block, not a routine rate limit. Honoring Retry-After "in
# full" (the Aug 30 fix above) meant time.sleep(20070) ran synchronously on
# the single PlanIt worker thread (max_workers=1), stalling that region --
# and everything queued behind it in the same run -- for over 5 hours,
# which looked identical to the pipeline being stuck/hung. A genuinely long
# Retry-After means "stop asking for a long while", not "block this thread
# for that whole while": past this cap, give up on the town for this run
# instead of sleeping through it.
PLANIT_MAX_RETRY_WAIT_SECONDS = float(os.getenv("PLANIT_MAX_RETRY_WAIT_SECONDS", "30") or "30")


def _planit_wait_for_slot() -> None:
    """Block the calling thread until it's been at least
    PLANIT_MIN_INTERVAL_SECONDS since the last PlanIt request made by ANY
    thread/region in this process, then claims the slot. Call this
    immediately before every real PlanIt HTTP request (initial attempt and
    retry alike) -- see the module comment above _PLANIT_PACING_LOCK for why
    a per-region-local throttle wasn't enough."""
    global _PLANIT_LAST_REQUEST_AT
    with _PLANIT_PACING_LOCK:
        wait_s = PLANIT_MIN_INTERVAL_SECONDS - (time.monotonic() - _PLANIT_LAST_REQUEST_AT)
        if wait_s > 0:
            time.sleep(wait_s)
        _PLANIT_LAST_REQUEST_AT = time.monotonic()


def scan_city_planning_api(city_name: str) -> int:
    """
    Scans planning applications for a UK region using ukplanningapi.co.uk (paid,
    postcode-prefix based) where a key is configured, and PlanIt (planit.org.uk,
    free, no key needed) as a real fallback/supplement, queried by real
    authority name via REGION_TOWNS.

    Aug 30 2026 rewrite: previously this function returned 0 for the ENTIRE
    region (both APIs) whenever UK_PLANNING_API_KEY was unset -- the free
    PlanIt fallback was wrongly gated behind the paid key's presence. It also
    called PlanIt with the wrong parameter name and no required search
    radius, which PlanIt rejects with an error payload on a 200 OK response
    -- silently swallowed by the old code as "0 leads found" with no visible
    failure. Both confirmed live against the real API (see REGION_TOWNS
    comment). Fixed: PlanIt now runs regardless of the paid key, using
    `auth=<real authority name>` (verified working), and any error payload
    from either API is now logged instead of silently treated as empty.
    """
    postcode_prefixes = CITY_POSTCODE_PREFIX.get(city_name, [])
    if isinstance(postcode_prefixes, str):
        postcode_prefixes = [postcode_prefixes]
    region_towns = REGION_TOWNS.get(city_name, [])

    if not postcode_prefixes and not region_towns:
        return 0

    # Aug 30 2026: Nick hit ukplanningapi.co.uk's free 500/month cap last
    # week -- root cause found by counting. Stage 1 was calling ALL 178
    # postcode prefixes across all 16 daily regions, EVERY single day
    # (London alone is 29). ~178/day burns the entire month's 500-request
    # budget in under 3 days, then this API goes dark for the remaining
    # ~27 days -- previously silently, now visibly, thanks to the 429 fix
    # above, but visibility alone doesn't get the leads back. PlanIt has
    # no monthly cap (only the per-request rate limit already handled with
    # backoff above) and the free Idox mesh has none either, so this fix
    # is specific to this one API: instead of querying every prefix every
    # day, round-robin through a rotation so the full prefix list is still
    # covered on a rolling basis, but total monthly calls land well under
    # the free tier's cap.
    #
    # The rotation period (default 12 days, not the bare-minimum-viable 11)
    # was picked to also absorb a second real-world pattern: on a heavy
    # development/testing day (like the day this was built), the pipeline
    # can get manually re-triggered multiple times on top of the one
    # scheduled daily cron run. The separate same-day dedup guard just
    # below means a same-calendar-day re-trigger is now FREE (it skips the
    # paid API entirely and reuses today's rotation results), so the
    # number that actually matters is "one paid-API pass per distinct
    # calendar day", not "one pass per trigger" -- 178 prefixes / 12-day
    # rotation =~ 14.8/day, which even in a 31-day month is ~460 calls,
    # leaving a real ~40-call/month buffer for edge cases (transient-error
    # retries, a region added later, etc.) that a bare 500-on-the-nose
    # target wouldn't have. Uses the date's ordinal (not day-of-month) so
    # the cycle doesn't reset oddly at month boundaries of different
    # lengths. Set PAID_API_ROTATION_DAYS=1 in the environment to disable
    # rotation entirely and query every prefix every day again -- e.g.
    # after upgrading to a paid tier with enough headroom that pacing is
    # no longer needed.
    todays_paid_prefixes = postcode_prefixes
    if postcode_prefixes:
        rotation_days = max(1, int(os.getenv("PAID_API_ROTATION_DAYS", "12") or "12"))
        if rotation_days > 1:
            day_index = datetime.date.today().toordinal() % rotation_days
            todays_paid_prefixes = [p for i, p in enumerate(postcode_prefixes) if i % rotation_days == day_index]
            if len(todays_paid_prefixes) < len(postcode_prefixes):
                logger.info(
                    f"[{city_name}] Paid API rotation: querying {len(todays_paid_prefixes)} of "
                    f"{len(postcode_prefixes)} postcode prefixes today (day {day_index + 1}/{rotation_days} "
                    f"of the rotation cycle) to keep monthly usage under the free-tier cap."
                )

        # Same-day dedup: a manual re-trigger later the same calendar day
        # (a redeploy, a manual /scan-nationwide, testing) would otherwise
        # re-fetch this exact same rotated subset again for zero new
        # coverage -- the rotation bucket only changes when the date does.
        # Persisted via persistent_dedup_cache (see _PAID_API_DEDUP_PREFIX
        # comment above) so a redeploy mid-day doesn't silently re-enable a
        # second full paid-API pass for this region on the next trigger.
        paid_dedup_key = f"{_PAID_API_DEDUP_PREFIX}:{city_name}"
        _dedup_conn = database.get_db_conn()
        dedup.ensure_table(_dedup_conn)
        if todays_paid_prefixes and dedup.already_done_today(_dedup_conn, paid_dedup_key):
            logger.debug(
                f"[{city_name}] ukplanningapi.co.uk already queried once today (any process, "
                f"including one that has since restarted) -- skipping to conserve monthly "
                f"quota; PlanIt and the free mesh still run below."
            )
            todays_paid_prefixes = []
        _dedup_conn.close()

    headers = {"X-API-Key": UK_PLANNING_API_KEY} if UK_PLANNING_API_KEY else {}
    new_leads = []

    try:
        from concurrent.futures import ThreadPoolExecutor

        # Aug 30 2026: per-prefix/per-town failures below stay at DEBUG (with
        # dozens of prefixes per region, a WARNING per one-off timeout would
        # flood the log) -- but a run where EVERY prefix/town for a region
        # failed was previously indistinguishable from a run that genuinely
        # found zero tree-related applications. That's a real blind spot: an
        # expired/invalid UK_PLANNING_API_KEY, or PlanIt being down, would
        # silently look identical to "0 new leads found" with no visible
        # cause anywhere in the log. These two lists collect failures so a
        # 100%-failure run gets ONE explicit WARNING naming the likely cause.
        paid_failures = []
        planit_failures = []

        def fetch_paid(prefix):
            """ukplanningapi.co.uk -- postcode-prefix based. Freemium, not
            purely "paid" as earlier comments here assumed: their own
            pricing page confirms a free tier capped at 500 requests/month
            with paid tiers above that -- and this project's own
            increment_api_usage() call below caps at exactly 500, which is
            the free-tier limit, not a paid one. Skipped (not silently, now
            logged once) if no key is configured.

            Aug 30 2026: a 429 here used to be silently excluded from BOTH
            logging and paid_failures -- carved out of the `elif` below on
            the (correct, for a burst rate limit) assumption that net_utils
            already handles 429 upstream. But this API's 429 is much more
            likely a MONTHLY QUOTA EXHAUSTION on a free-tier key than a
            per-second burst limit, and unlike a burst limit, retrying
            seconds later can't fix that -- so this now logs it clearly and
            counts it as a real failure instead of a silent, indistinguishable
            "0 results", which is exactly the blind spot that let a possible
            month-long quota exhaustion look identical to genuinely zero
            leads with no visible cause anywhere in the logs."""
            if not UK_PLANNING_API_KEY:
                return prefix, []
            try:
                import time
                time.sleep(1.5)  # Cron job throttle to prevent 6am ban
                res = net_utils.smart_get(
                    "https://ukplanningapi.co.uk/v1/applications",
                    params={"postcode": prefix, "status": "received", "limit": 200},
                    headers=headers,
                    timeout=8
                )
                if res.status_code == 200:
                    body = res.json()
                    if isinstance(body, dict) and body.get("error"):
                        logger.warning(f"[{city_name}] ukplanningapi.co.uk returned an error for prefix '{prefix}': {body.get('error')}")
                        paid_failures.append(f"'{prefix}': API error {body.get('error')}")
                        return prefix, []
                    return prefix, body.get("data", [])
                elif res.status_code == 429:
                    logger.warning(
                        f"[{city_name}] ukplanningapi.co.uk returned 429 for prefix '{prefix}' -- "
                        f"likely the monthly request quota is exhausted (this key appears to be on "
                        f"the free 500/month tier), not a transient rate limit."
                    )
                    paid_failures.append(f"'{prefix}': HTTP 429 (likely monthly quota exhausted)")
                else:
                    logger.debug(f"[{city_name}] ukplanningapi.co.uk HTTP {res.status_code} for prefix '{prefix}'")
                    paid_failures.append(f"'{prefix}': HTTP {res.status_code}")
            except Exception as e:
                logger.debug(f"[{city_name}] ukplanningapi.co.uk error for prefix '{prefix}': {e}")
                paid_failures.append(f"'{prefix}': {e}")
            return prefix, []

        def fetch_planit(town):
            """PlanIt (planit.org.uk) -- free, no key, queried by real
            authority name. `recent=45` matches this pipeline's general
            lookback window; other_fields carries applicant/agent when
            PlanIt has actually captured it (often "See source" -- filtered
            out by _planit_real_value, never stored as a real name).

            Aug 30 2026: live logs showed PlanIt returning HTTP 429 for
            EVERY authority in EVERY one of the 16 regions in a single run --
            the actual root cause of days of "0 new leads" that looked
            identical to a genuine empty result. Two fixes: (1) every real
            request -- initial attempt and retry alike -- now goes through
            _planit_wait_for_slot() first, a process-wide pacing gate shared
            by all 16 regions (a per-region time.sleep(1.5) had no memory of
            what earlier regions in the same run had already sent PlanIt).
            (2) the previous fix capped the wait at min(Retry-After, 20s) --
            if PlanIt's server genuinely asked for longer, the old code
            ignored that and retried too soon anyway. The cap is gone: a
            server-specified Retry-After is now honored in full, falling
            back to PLANIT_MIN_INTERVAL_SECONDS (not a fixed 8s) when it's
            absent or unparseable, since that's the same safe interval
            we're already pacing every other request to."""
            for attempt in range(2):
                try:
                    _planit_wait_for_slot()
                    planit_res = net_utils.smart_get(
                        "https://www.planit.org.uk/api/applics/json",
                        params={"auth": town, "recent": 45, "pg_sz": 50},
                        timeout=12
                    )
                    if planit_res.status_code == 429:
                        retry_after = planit_res.headers.get("Retry-After")
                        try:
                            wait_s = float(retry_after) if retry_after else PLANIT_MIN_INTERVAL_SECONDS
                        except (ValueError, TypeError):
                            wait_s = PLANIT_MIN_INTERVAL_SECONDS
                        if wait_s > PLANIT_MAX_RETRY_WAIT_SECONDS:
                            logger.warning(f"[{city_name}] PlanIt asked for a {wait_s:.0f}s wait for '{town}' -- treating as a hard block (exceeds the {PLANIT_MAX_RETRY_WAIT_SECONDS:.0f}s cap) and skipping it this run instead of stalling the pipeline.")
                            planit_failures.append(f"'{town}': HTTP 429 (server requested {wait_s:.0f}s, exceeds cap, skipped)")
                            return town, []
                        if attempt == 0:
                            logger.info(f"[{city_name}] PlanIt rate-limited (429) for '{town}', waiting {wait_s:.0f}s and retrying once...")
                            time.sleep(wait_s)
                            continue
                        logger.debug(f"[{city_name}] PlanIt still 429 for '{town}' after backoff, giving up for this run.")
                        planit_failures.append(f"'{town}': HTTP 429 (rate limited, retry also failed)")
                        return town, []
                    if planit_res.status_code != 200:
                        logger.debug(f"[{city_name}] PlanIt HTTP {planit_res.status_code} for authority '{town}'")
                        planit_failures.append(f"'{town}': HTTP {planit_res.status_code}")
                        return town, []
                    break
                except Exception as e:
                    logger.debug(f"[{city_name}] PlanIt error for authority '{town}': {e}")
                    planit_failures.append(f"'{town}': {e}")
                    return town, []
            try:
                data = planit_res.json()
                if isinstance(data, dict) and data.get("error"):
                    logger.warning(f"[{city_name}] PlanIt returned an error for authority '{town}': {data.get('error')}")
                    planit_failures.append(f"'{town}': API error {data.get('error')}")
                    return town, []
                records = data.get("records", [])
                mapped_data = []
                for rec in records:
                    other = rec.get("other_fields") or {}
                    mapped_data.append({
                        "reference": rec.get("uid") or rec.get("name", ""),
                        "description": rec.get("description", ""),
                        "address": rec.get("address", ""),
                        "url": rec.get("link", ""),
                        # Sep 2 2026, master_expansion_plan_v2.md build-order step 4
                        # (tiered classifier, Tier 2 -- structured fields): PlanIt's
                        # own categorisation of the application, e.g. "Trees",
                        # "Full", "Outline", "Amendment". See
                        # _resolve_vertical_with_structured_fields' module comment
                        # for the real live-data evidence behind why only this one
                        # field, and only for tree, turned out to be usable here.
                        "app_type": rec.get("app_type"),
                        "applicant_name": _planit_real_value(other.get("applicant_name")),
                        "agent_name": _planit_real_value(other.get("agent_name")),
                        "agent_company": _planit_real_value(other.get("agent_company")),
                        # Aug 30 2026: "url" above is PlanIt's OWN page for
                        # this application (kept as-is for the outbound link
                        # this pipeline already shows) -- this is a SEPARATE
                        # field, PlanIt's documented "original planning
                        # authority's website" link, plus its other_fields
                        # equivalent. Neither is PlanIt's applicant/agent
                        # data (PlanIt deliberately never stores real names --
                        # see mesh_scrapers.confirm_agent_status_from_source's
                        # docstring) -- it's the real source page we can
                        # follow to actually check, the same way the mesh
                        # scanner already does for its own registered councils.
                        "source_url": rec.get("url") or other.get("source_url") or "",
                    })
                return town, mapped_data
            except Exception as e:
                logger.debug(f"[{city_name}] PlanIt error for authority '{town}': {e}")
                planit_failures.append(f"'{town}': {e}")
            return town, []

        with ThreadPoolExecutor(max_workers=6) as executor:
            paid_results = list(executor.map(fetch_paid, todays_paid_prefixes)) if todays_paid_prefixes else []
        if todays_paid_prefixes:
            # Mark today's rotation bucket attempted regardless of outcome --
            # a same-day retry wouldn't fix a real quota exhaustion or key
            # problem anyway, and the goal here is strictly "at most one
            # paid-API pass per region per calendar day". Persisted so a
            # redeploy doesn't undo this mark.
            _mark_conn = database.get_db_conn()
            dedup.mark_done_today(_mark_conn, f"{_PAID_API_DEDUP_PREFIX}:{city_name}")
            _mark_conn.close()

        # Aug 30 2026: dropped from 6, then 2, down to 1 worker. With
        # _planit_wait_for_slot() now serializing every PlanIt request
        # process-wide to one per PLANIT_MIN_INTERVAL_SECONDS regardless of
        # which region is asking, extra workers here can't buy any real
        # concurrency -- they'd just queue up on the same pacing lock. One
        # worker keeps the code simple and makes the actual request order
        # predictable (PlanIt calls were never the pipeline's bottleneck
        # stage, so there's no throughput cost to this).
        #
        # Same-day dedup (separate from the pacing gate): a manual
        # re-trigger later the same calendar day gains nothing by
        # re-querying the exact same authority names against PlanIt again.
        # Persisted (see _PLANIT_DEDUP_PREFIX comment above) so it survives
        # a redeploy instead of resetting with it.
        planit_dedup_key = f"{_PLANIT_DEDUP_PREFIX}:{city_name}"
        todays_region_towns = region_towns
        _planit_dedup_conn = database.get_db_conn()
        dedup.ensure_table(_planit_dedup_conn)
        if region_towns and dedup.already_done_today(_planit_dedup_conn, planit_dedup_key):
            logger.debug(f"[{city_name}] PlanIt already queried once today (any process, including one that has since restarted) -- skipping re-trigger.")
            todays_region_towns = []
        _planit_dedup_conn.close()

        # Sep 1 2026: Nick flagged that the pipeline "looks stuck" for the
        # ~100+ minutes this loop takes -- correctly diagnosed as an
        # observability gap, not an actual hang. Every per-authority outcome
        # here logs at DEBUG (deliberately, per the Aug 30 comment above --
        # a WARNING per one-off timeout across ~100 authorities would flood
        # the log), so between the initial "Paid API rotation" line and the
        # final "Stage 1 Complete" line, production logs showed nothing at
        # all while this was genuinely working through the 60s-per-request
        # PlanIt pacing lock one authority at a time. This heartbeat is the
        # fix: one INFO line every 10 authorities (and on the very last one)
        # naming this region and a running count, so a live tail of the logs
        # shows steady progress instead of looking abandoned.
        _planit_total = len(todays_region_towns)
        _planit_done = {"n": 0}

        def _fetch_planit_with_heartbeat(town):
            result = fetch_planit(town)
            _planit_done["n"] += 1
            n = _planit_done["n"]
            if n == 1 or n % 10 == 0 or n == _planit_total:
                logger.info(f"[{city_name}] PlanIt progress: {n}/{_planit_total} authorities queried so far.")
            return result

        with ThreadPoolExecutor(max_workers=1) as planit_executor:
            planit_results = list(planit_executor.map(_fetch_planit_with_heartbeat, todays_region_towns)) if todays_region_towns else []
        if todays_region_towns:
            _mark_planit_conn = database.get_db_conn()
            dedup.mark_done_today(_mark_planit_conn, planit_dedup_key)
            _mark_planit_conn.close()

        if UK_PLANNING_API_KEY and todays_paid_prefixes and len(paid_failures) == len(todays_paid_prefixes):
            logger.warning(
                f"[{city_name}] ukplanningapi.co.uk failed for ALL {len(todays_paid_prefixes)} postcode "
                f"prefixes queried today (e.g. {paid_failures[0]}) -- this looks like an invalid/expired "
                f"UK_PLANNING_API_KEY or an API outage, not a genuine zero-results run."
            )
        if todays_region_towns and len(planit_failures) == len(todays_region_towns):
            logger.warning(
                f"[{city_name}] PlanIt failed for ALL {len(todays_region_towns)} authorities queried today "
                f"(e.g. {planit_failures[0]}) -- this looks like an outage or a bad authority name, "
                f"not a genuine zero-results run."
            )

        # Track monthly usage and trigger predictive warning email when burn rate will breach 500 cap.
        # Aug 30 2026: counts todays_paid_prefixes (what rotation/dedup actually
        # queried today), not the region's full postcode_prefixes list -- counting
        # the full list here would make the usage tracker think every region's
        # entire prefix set was queried every day, defeating the point of the
        # rotation above and falsely projecting a cap breach that isn't real.
        if UK_PLANNING_API_KEY and todays_paid_prefixes:
            usage_info = database.increment_api_usage("UK Planning API", increment=len(todays_paid_prefixes), cap=500)
            if usage_info.get("warning_needed"):
                notifications.send_api_quota_warning_email(
                    api_name="UK PLANNING DATA API",
                    current_calls=usage_info.get("count", 350),
                    cap=500,
                    projected_monthly=usage_info.get("projected_monthly", 600),
                    reason=usage_info.get("reason", "Projected monthly pace exceeds 500 limit")
                )

        conn = database.get_db_conn()
        cur = conn.cursor()
        try:
            for prefix, records in paid_results:
                for item in records:
                    try:
                        # Sep 2 2026: str(... or "") instead of "x or ''" -- the latter
                        # only substitutes when the field is missing/falsy, so a truthy
                        # non-string value (int, nested dict) from this paid API used to
                        # pass straight through into _resolve_vertical's .lower() call
                        # and crash. The try/except around this whole item body is the
                        # other half of the fix: this loop only conn.commit()s once, at
                        # the very end, so an uncaught crash here used to roll back
                        # every lead already inserted earlier in this same run, for this
                        # entire region, not just skip the one bad record.
                        summary = str(item.get("description") or "")
                        real_ref = str(item.get("reference") or "").strip()
                        vertical = _resolve_vertical(summary)
                        if vertical is None:
                            # Sep 2 2026, Tier 4: only queue with a real, stable
                            # reference -- see the GLA loop's identical comment for why.
                            if real_ref:
                                _queue_for_manual_review(real_ref, str(item.get("address") or city_name), summary, city_name)
                            continue
                        ref  = real_ref or f"{prefix}-{int(time.time())}"
                        addr = str(item.get("address") or city_name)
                        # Aug 30 2026: ukplanningapi.co.uk was found (during the
                        # PlanIt live-testing pass) to sometimes return results
                        # whose address doesn't actually match the requested
                        # postcode-prefix param -- e.g. a "Sheffield"-requested
                        # scan returning a London/Home Counties address. Rather
                        # than trust the paid API's own filtering and mislabel
                        # council_source, verify the returned address's outcode
                        # actually starts with the prefix we asked for; skip
                        # (don't guess a relabel) if it clearly doesn't.
                        outcode_match = re.search(r'\b([A-Z]{1,2})[0-9][A-Z0-9]?\s*[0-9][A-Z]{2}\b', addr.upper())
                        if outcode_match and not outcode_match.group(1).startswith(prefix.upper()):
                            logger.warning(
                                f"[{city_name}] ukplanningapi.co.uk returned an address outcode "
                                f"'{outcode_match.group(1)}' for requested prefix '{prefix}' -- "
                                f"skipping to avoid mislabeling council_source ('{addr}')."
                            )
                            continue
                        lead = _insert_lead(cur, ref, addr, summary, city_name, vertical=vertical)
                        if lead:
                            new_leads.append(lead)
                    except Exception as e:
                        logger.warning(f"[{city_name}] Skipping one malformed paid-API record ({prefix}): {e}")
                        continue

            # Aug 30 2026: Nick's exact concern -- "I can't sell leads to
            # jobs that already have someone signed up for them" -- and
            # PlanIt's own field dictionary confirms it deliberately never
            # stores real applicant/agent names, so almost every PlanIt lead
            # was landing as permanently "unconfirmed", not a real "no
            # agent". mesh_scrapers.confirm_agent_status_from_source follows
            # PlanIt's own source-authority link back to the real council
            # portal page and reuses the exact same detail-page check the
            # mesh scanner already does for its own registered councils --
            # turning "unconfirmed" into a real, confirmed yes/no wherever
            # that authority runs recognisable Idox software. Bounded per
            # region per run (PLANIT_AGENT_CONFIRM_LIMIT) since this is a
            # brand new real HTTP request PER lead, straight to that
            # council's own server -- not PlanIt's, so it doesn't share
            # PlanIt's pacing gate, but it's a different server every time
            # (whichever authority the lead belongs to) rather than one
            # shared one, so a modest per-call cap plus a short sleep is the
            # right amount of caution rather than a full pacing lock.
            confirm_budget = PLANIT_AGENT_CONFIRM_LIMIT
            # Aug 31 2026: attempts vs outcome wasn't visible anywhere -- a
            # blank has_agent after this loop could mean "never attempted"
            # (no source_url, non-Idox authority, or budget exhausted) or
            # "attempted but the portal page didn't have enough info to say
            # either way" (confirm_agent_status_from_source returned {}).
            # Those are very different signals for how fast the backlog of
            # unconfirmed leads will actually resolve, so tally and log them
            # explicitly instead of only being able to infer outcomes later
            # from a DB export.
            confirm_stats = {"attempted": 0, "resolved_true": 0, "resolved_false": 0, "inconclusive": 0}

            for town, records in planit_results:
                for item in records:
                    try:
                        # Sep 2 2026: str(... or "") -- see the identical fix and its
                        # comment in the paid-API loop just above; same crash, same
                        # cause, same fix. The try/except wrapping this whole item body
                        # is the other half: this loop only conn.commit()s once, at the
                        # very end, so one malformed PlanIt record used to be able to
                        # roll back every lead already inserted earlier in this run.
                        summary = str(item.get("description") or "")
                        real_ref = str(item.get("reference") or "").strip()
                        # Sep 2 2026: Tier 2 addition -- see
                        # _resolve_vertical_with_structured_fields' module comment
                        # for why PlanIt's app_type is used here specifically.
                        vertical = _resolve_vertical_with_structured_fields(summary, item.get("app_type")) if summary else None
                        if vertical is None:
                            # Sep 2 2026, Tier 4: only queue with a real, stable
                            # reference -- see the GLA loop's identical comment for why.
                            if real_ref:
                                _queue_for_manual_review(
                                    real_ref, item.get("address") or f"{city_name} / {town}",
                                    summary, city_name, app_type=item.get("app_type"),
                                )
                            continue
                        ref  = real_ref or f"PLANIT-{town}-{int(time.time())}"
                        addr = item.get("address") or f"{city_name} / {town}"
                        applicant_name = item.get("applicant_name")
                        agent_name = item.get("agent_name")
                        agent_company = item.get("agent_company")
                        has_agent = (True if (agent_name or agent_company) else None)
                        agent_is_tree_surgeon = None
                        if has_agent:
                            import mesh_scrapers
                            agent_is_tree_surgeon = mesh_scrapers.classify_agent_as_tree_surgeon(agent_name, agent_company)

                        if has_agent is None and item.get("source_url"):
                            # Aug 30 2026: Nick's point -- re-confirming a
                            # reference we already resolved on a PREVIOUS day
                            # would spend a real HTTP request to that council's
                            # server, forever, every single day PlanIt keeps
                            # returning that still-live application (up to 45
                            # days). PlanIt's own record never carries the
                            # answer (it structurally never stores names), so
                            # without this check we'd have re-confirmed the same
                            # already-known lead again and again. This one cheap
                            # DB lookup -- no network call -- is what makes it
                            # safe to run this with a generous budget as a
                            # PERMANENT setting, not just a one-off: once a
                            # reference is resolved, every future day it costs a
                            # SELECT, never another real request.
                            #
                            # Aug 31 2026 fix: found live in a production export
                            # -- 187 leads sitting at has_agent=True with
                            # agent_is_tree_surgeon still NULL, permanently
                            # excluded from the marketplace by the has_agent/
                            # agent_is_tree_surgeon filter in
                            # get_marketplace_leads_with_freshness (NULL is
                            # treated the same as "confirmed tree surgeon" --
                            # excluded either way). Root cause: has_agent got
                            # resolved (either before agent_is_tree_surgeon
                            # existed, or via a path that only set has_agent)
                            # and this same "already resolved, skip" check then
                            # skipped it on every subsequent day forever, since
                            # it only ever checked has_agent, never whether
                            # agent_is_tree_surgeon specifically still needed
                            # classifying. Fixed by pulling the agent name/
                            # company already on file too and classifying from
                            # them right here when needed -- classify_agent_as_
                            # tree_surgeon is pure string matching, zero network
                            # cost, so there's no reason this has to wait for
                            # (or be gated by) a real confirm_budget-limited
                            # HTTP request at all.
                            cur.execute(
                                "SELECT has_agent, applicant_name, agent_name, agent_company, agent_is_tree_surgeon "
                                "FROM leads WHERE reference = %s", (ref,)
                            )
                            existing_row = cur.fetchone()
                            if existing_row and existing_row[0] is not None:
                                existing_has_agent, existing_applicant, existing_agent_name, existing_agent_company, existing_ats = existing_row
                                has_agent = existing_has_agent
                                applicant_name = applicant_name or existing_applicant
                                agent_name = agent_name or existing_agent_name
                                agent_company = agent_company or existing_agent_company
                                if has_agent and existing_ats is None and (agent_name or agent_company):
                                    import mesh_scrapers
                                    agent_is_tree_surgeon = mesh_scrapers.classify_agent_as_tree_surgeon(agent_name, agent_company)
                                else:
                                    agent_is_tree_surgeon = existing_ats
                            elif confirm_budget > 0:
                                confirm_budget -= 1
                                confirm_stats["attempted"] += 1
                                try:
                                    import mesh_scrapers
                                    # Sep 1 2026: only sleep when we're actually
                                    # about to hit the council's own server --
                                    # live logs showed ~94% of these attempts are
                                    # non-Idox authorities/unparseable URLs that
                                    # confirm_agent_status_from_source rejects
                                    # instantly with zero network activity (see
                                    # is_confirmable_idox_url's docstring). The
                                    # unconditional sleep before every attempt,
                                    # Idox or not, was burning real minutes per
                                    # run for nothing -- directly the "scans take
                                    # hours" complaint, for zero benefit since
                                    # there's no server to be polite to when no
                                    # request is being made.
                                    if mesh_scrapers.is_confirmable_idox_url(item["source_url"]):
                                        time.sleep(1.0)  # polite -- this hits the council's own server, not PlanIt's
                                    confirmed = mesh_scrapers.confirm_agent_status_from_source(item["source_url"])
                                except Exception as e:
                                    logger.debug(f"[{city_name}] Agent-status confirmation failed for '{ref}': {e}")
                                    confirmed = {}
                                if confirmed and "has_agent" in confirmed:
                                    applicant_name = applicant_name or confirmed.get("applicant_name")
                                    agent_name = agent_name or confirmed.get("agent_name")
                                    agent_company = agent_company or confirmed.get("agent_company")
                                    # confirmed["has_agent"] is a REAL True/False
                                    # (the detail page was actually visited) --
                                    # unlike PlanIt's own data, this can safely
                                    # be trusted as a genuine "no agent" too, not
                                    # just "yes".
                                    has_agent = confirmed["has_agent"]
                                    agent_is_tree_surgeon = confirmed.get("agent_is_tree_surgeon") if has_agent else None
                                    confirm_stats["resolved_true" if has_agent else "resolved_false"] += 1
                                else:
                                    confirm_stats["inconclusive"] += 1

                        lead = _insert_lead(
                            cur, ref, addr, summary, city_name,
                            applicant_name=applicant_name,
                            agent_name=agent_name,
                            agent_company=agent_company,
                            has_agent=has_agent,
                            agent_is_tree_surgeon=agent_is_tree_surgeon,
                            vertical=vertical,
                        )
                        if lead:
                            new_leads.append(lead)
                    except Exception as e:
                        # Sep 2 2026: one malformed PlanIt record must never cost the rest of
                        # this town's batch, or roll back leads already inserted earlier in
                        # this run -- see the paid-API loop's identical fix just above.
                        logger.warning(f"[{city_name}] Skipping one malformed PlanIt record ({town}): {e}")
                        continue

            conn.commit()
        finally:
            cur.close()
            conn.close()

        if new_leads:
            notifications.dispatch_lead_alerts(city_name, new_leads)
        if confirm_stats["attempted"]:
            logger.info(
                f"[{city_name}] Agent-status confirmation: {confirm_stats['attempted']} checked against "
                f"the council's own portal -- {confirm_stats['resolved_true']} confirmed has-agent, "
                f"{confirm_stats['resolved_false']} confirmed no-agent, "
                f"{confirm_stats['inconclusive']} inconclusive (portal page didn't say either way)."
            )
        logger.info(f"[{city_name}] Parallel scan complete. {len(new_leads)} new leads found.")
        return len(new_leads)

    except Exception as e:
        logger.error(f"[{city_name}] Fatal error in scan_city_planning_api: {e}")
        return 0


def scan_scotland_leads() -> int:
    """Scans all 32 Scottish local authority planning portals in parallel."""
    return scan_city_planning_api("Scotland")


def scan_wales_leads() -> int:
    """Scans all 22 Welsh local authority planning portals in parallel."""
    return scan_city_planning_api("Wales")


def scan_nationwide_bulk_crawler() -> dict:
    """
    Crawls all 10 major UK macro-regions in parallel to pull thousands of active tree leads.
    """
    regions = [
        "London", "Leeds", "Manchester", "Birmingham", "Bristol",
        "Sheffield", "North East", "East of England", "East Midlands",
        "South West", "South East", "Scotland", "Wales"
    ]
    total_leads = 0
    region_results = {}

    # 1. Run direct council Idox mesh scrapers across 50+ UK local planning authorities
    try:
        mesh_count = run_mesh_network_scan()
        region_results["Direct Council Idox Mesh (50+ Authorities)"] = mesh_count
        total_leads += mesh_count
    except Exception as e:
        region_results["Direct Council Idox Mesh"] = f"error: {e}"

    # 2. Run Regional and Open Data Scanners
    for reg in regions:
        try:
            if reg == "London":
                c = scan_london_leads()
            elif reg == "Leeds":
                c = scan_leeds_leads()
            else:
                c = scan_city_planning_api(reg)
            region_results[reg] = c
            total_leads += c
        except Exception as e:
            region_results[reg] = f"error: {e}"

    logger.info(f"[NATIONWIDE CRAWLER] Completed nationwide scan. Total new leads: {total_leads}")
    return {"total_new_leads": total_leads, "regions": region_results}
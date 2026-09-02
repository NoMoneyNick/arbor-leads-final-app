# AI HANDOFF — TreeKey / Vector Data Labs

**Written by:** Claude Sonnet 5 (Cowork/cloud session)
**Written:** 2 Sep 2026, at Nick's explicit request, at "the next large junction" after the HMO mesh-scraper build shipped
**Supersedes:** the previous version of this file (dated 28 Aug 2026, authored by Claude Sonnet 4.6 (Thinking)) — that version's 5 numbered tasks are resolved below in §7; its architecture description is stale and replaced by §3-§5 here
**Companion documents, still current, don't duplicate them — read them too:**
- `MANIFEST.md` — the original operational rules/brand/legal/revenue doc (dated 24 Aug 2026 — **stale on architecture**, see §9)
- `PROJECT_STATE.md` — dated changelog/sprint queue (numbered items 1-13 run through 30 Aug 2026; item 14 added by this session pointing here for what happened Sep 1-2)
- `master_expansion_plan_v2.md` — the actual current strategy doc for the multi-vertical expansion; this handoff reports *status against* that plan, it doesn't replace it

**Repo:** `https://github.com/NoMoneyNick/arbor-leads-final-app` | **Live:** `https://treekey.uk` | **Deploy:** `git push` → Render auto-deploys, no manual step | **Local folder:** `C:\Users\twobo.DESKTOP-DI088K1\OneDrive\Documents\VECTOR DATA LABS` (synced via OneDrive) | **Local deploy helper:** `UPDATE_WEBSITE.bat`

---

## 0. COMPRESSED STATE DUMP (dense notation — parse this first; §1-9 below is the same information decompressed to full prose/detail, read those only where you need more than this gives you)

Legend: `→`=leads to/then | `⇒`=causes/resulted in | `|`=alternative/separator | `+`=and/plus | `∅`=none/empty/null | `✅`=done+verified | `🔶`=done, unverified/flagged | `⛔`=blocked/not started | `⚠`=open risk, unresolved | `{k:v}`=config-shaped fact | `#N`=cross-ref to prose section N below.

```
ENTITY VectorDataLabs{parent,holding,payments} > BRAND TreeKey{customer-facing}
  LIVE=treekey.uk DEPLOY=git-push→Render-auto REPO=github:NoMoneyNick/arbor-leads-final-app
  LOCAL=OneDrive\VECTOR-DATA-LABS SIBLING-FOLDER=OneDrive\CRAWLER-PROJECT(tool-source, #6)
  DOCS{AI_HANDOFF.md=this,#0-9 | MANIFEST.md=biz/brand/legal,STALE-ARCH(24Aug) | PROJECT_STATE.md=dated-changelog,items1-14 | master_expansion_plan_v2.md=STRATEGY-SOURCE-OF-TRUTH}

MODEL: SaaS, sell(planning-application leads)→contractors, via{subscription(£/mo,radius-dispatch), marketplace(pay-per-lead,one-off)}

VERTICALS{
  tree: status=✅LIVE+REVENUE, capture_identity=true, gold_list=TREE_GOLD(keyword,live-data-verified)
  hmo:  status=🔶BUILT+TESTED+PRE-REVENUE(no pricing tier yet, deliberately gated on proving pipeline first), capture_identity=false(GDPR,enforced@_insert_lead,structural-not-trusted-per-caller), gold_list=HMO_GOLD(keyword,live-data-verified)
}
VERTICAL-MECHANISM: scanners.VERTICALS{config-dict}→_matches_vertical(text,key)→classify_verticals(text)[multi-label]→_resolve_vertical(text)[single-answer,tree-WINS-TIES(interim,not final design)]
ADD-NEW-VERTICAL = config-entry-only, NOT a code fork — proven across 3 subsystems(classifier,mesh_scrapers.py,bulk_contractor_extractor.py) #3

PIPELINE:
  SOURCES[Leeds(ArcGIS,spatial)+GLA-London(Datahub API,GLA_API_KEY⚠INVALID-needs-renewal-cadence-∅UNKNOWN)+paid-UKPlanningAPI(quota-capped)+free-PlanIT(no-key)+mesh(Idox-HTML-portals,COUNCIL_REGISTRY:48-councils,ALL-live-browser-verified)]
  →CLASSIFY[
      T1=keyword-match(GOLD-lists) ✅LIVE
      T2=structured-field(PlanIT-only:app_type=="trees"→tree; NOT-built for HMO[no usable field found]/GLA/paid-API[schemas unverified]) ✅LIVE
      T3=Gemini-LLM(_classify_via_llm; lib=google.generativeai[deliberately OLD lib not new google-genai,#6]; model=gemini-flash-latest[version-agnostic alias]; separate BATCH pass not inline[cost:most apps match neither vertical]) 🔶SHIPPED-TO-DISK+TESTED, ⛔NOT-LIVE(needs Render-redeploy+GEMINI_API_KEY env var; INERT/SAFE-NOOP if key absent, never errors/blocks)
      T4=manual-review-queue(unclassified_applications table; only queues w/ real stable ref, never timestamp-fallback; endpoints:/review-queue[dashboard-auth]+/process-review-queue[cron-secret,manual-trigger]) ✅LIVE
    ]
  →_insert_lead(vertical-tagged, capture_identity-enforced-per-vertical)
  →SELL(subscription-dispatch + marketplace-listing)

MESH-SCRAPER(mesh_scrapers.py):
  COUNCIL_REGISTRY=48✅live-verified(2 audit passes: found+fixed 12-silently-dead+2-wrongly-declared-non-Idox[Fife,Derby]+1-wrongly-removed[Sutton,reinstated]; permanent regression test guards registry-routability)
  HMO_ADD-ON: IDOX_HMO_SEARCH_TERMS × COUNCILS_WITH_CONFIRMED_HMO_ARTICLE_4{13 councils = govt-data(planning.data.gov.uk,35-council raw list,VOLUNTARY-dataset-103/317-LPAs-so-FLOOR-not-census) ∩ already-verified-48-registry = ZERO-incremental-verification-risk}
  ROOT-BUG-FIXED: IdoxScraper.search_tree_applications used to hardcode is_tree_related() filter regardless-of-search-term ⇒ would've silently discarded genuine HMO hits even w/ HMO terms added → replaced w/ shared _resolve_vertical(), each lead now carries own "vertical" key → run_mesh_network_scan now passes lead.get("vertical","tree") through to _insert_lead(previously hardcoded tree)
  ⚠OPEN: ~22 more govt-data-confirmed HMO councils found(Crawley,Newcastle,Sefton,Harlow,Salford,Fenland,TowerHamlets,Barking&Dagenham,Rother,Rossendale,Tendring,Hillingdon,Halton,N.Warwickshire,Ipswich,Burnley,Newcastle-under-Lyme,Basingstoke&Deane,Bury,+Hounslow[known-Northgate-not-Idox]) — NOT enabled, need same live-browser check every existing entry got. DO NOT add w/o this step — has already caused real bugs 2x.

CONTRACTOR-FINDER(bulk_contractor_extractor.py, standalone/offline, NOT in live pipeline):
  CONTRACTOR_VERTICALS{tree,hmo} config mirrors scanners.VERTICALS shape
  hmo.sic_codes_trusted=[41202,41100,43999,43390,43210,43220](SIC-alone-sufficient) vs hmo.sic_codes_gated=[68320,68209](needs required_words name-match too, since these SIC codes cover ALL letting/property-mgmt agents not just HMO-focused)
  CLI: `python bulk_contractor_extractor.py [tree|hmo]`, defaults tree, backward-compat
  ⚠OPEN: HMO word-lists NOT spot-checked against real live Companies House results(no COMPANIES_HOUSE_KEY in cloud sandbox to test) — do small real harvest, eyeball first 50-100 before scale

INCIDENT-LOG:
  Sep1 23:28-23:46 UTC(~18min) ⇒ migration(`ALTER TABLE leads ADD COLUMN vertical`) hit lock-timeout under live write-contention ⇒ rolled back ENTIRE Phase1 migration txn(all ALTERs shared one commit) ⇒ vertical column missing in prod ⇒ _insert_lead's INSERT unconditionally listed it ⇒ EVERY lead-insert failed silently(caught by try/except,logged-not-crashed,but ZERO leads captured) + get_marketplace_leads_with_freshness same missing-column error ⇒ its catch-all returns[] ⇒ PUBLIC MARKETPLACE SHOWED ZERO LEADS TO EVERY CUSTOMER. Real revenue-impacting outage. Caught only bc Nick happened to check unrelated privacy-policy change.
  FIX×3: (1)_run_ddl_statements_resiliently()=each ALTER own-txn+short-lock_timeout+retry-backoff,fails-fast not queue-behind-full-timeout (2)_insert_lead self-heals:catches missing-column error,retries w/ pre-migration column-list (3)get_marketplace_leads_with_freshness same fallback.
  STATUS=✅CONFIRMED-RESOLVED(redeploy log clean Phase1/2/3; /privacy-policy+/marketplace both verified live afterward)

TEST-SUITE: 188/188✅ as-of Sep2. RULE: full-suite-pass required before "done".

SYNC-DISCIPLINE(⚠hard-learned, see #8 for the incident that taught this): cloud-sandbox folder("/mnt/user-data/uploads/VECTOR DATA LABS") ≠ Nick's-real-disk(OneDrive, same folder-name, DO NOT CONFLATE — this exact mistake once left database.py+main.py stale-on-real-disk + 3 files MISSING ENTIRELY from real disk despite being marked "done", incl. one that would've crashed prod w/ ModuleNotFoundError). PROTOCOL=device_list_dir(fresh mtimes)→SendUserFile→device_commit_files(w/ expectedMtimeMs guard)→device_list_dir AGAIN→confirm byte-for-byte match. Never mark "done" from sandbox-test-pass alone.

MASTER-PLAN-STEPS(master_expansion_plan_v2.md, see #5 for full reasoning):
  1✅verticals-config+classifier | 2✅wired-into-4-scan-sites+mesh | 3✅GDPR-safe-HMO-format(⚠ICO-citation-unverified,see risk-list) | 4✅HMO_GOLD-live-data-verified+T1-4-classifier | 5✅contractor-finder-generalized(⚠unspotchecked) | 6⛔extensions/loft-3rd-vertical(correctly-blocked-on-HMO-proving-live) | 7⛔marketplace_engine-reuse(real-integration-cost-found,not-quick-win,#6) | 8=live-verification-discipline-itself(ONGOING not one-time) | 9⛔rebrand/combine-one-site(correctly-blocked)

CRAWLER-PROJECT-TOOLS-AUDIT(#6, corrects earlier-wrong note that scoring/reporting/notification/workflow_engine existed—they DON'T, were unbuilt ideas):
  entity_graph/graph.py=generic confidence-scored dedup store,anti-false-merge-biased,∅used-yet,candidate=contractor-growth-signal-intel
  marketplace_engine=SQLAlchemy Actor/SupplyRecord/DemandRecord,well-designed BUT separate-schema=real-integration-cost-not-drop-in
  escrow_engine=wrong-shape-for-lead-sale,SKIP(correctly)
  outreach_engine=working Gemini personalized-msg-generator, IS the pattern T3-classifier reused(reply_classifier.py)
  semantic_engine=LlamaIndex+chromadb+Gemini-embeddings,new-deps,candidate-future-Tier3-alt,∅urgent
  scraper/net.py+pacing.py(SmartFetcher/CircuitBreaker/DomainPacer)=concrete-still-not-ported fix for mesh-scraper traffic-scaling as council-count grows

OPEN-RISKS(#7, none resolved, do not assume otherwise):
  ⚠1 Gemini council/portal research: 17%-confirmed-wrong-rate(5/29 checked: Camden,Hounslow,Islington,Merton,Manchester all wrong) → treat remaining ~24 unverified rows same way, live-verify before use
  ⚠2 LIA's core compliance argument rests on unverified-ICO-quote(address-only-marketing=GDPR-exempt) — same claim plan's own earlier 4-AI-cross-check already flagged as weakest/least-cited — do NOT build outreach-strategy on it, solicitor must confirm/strike specifically
  ⚠3 privacy_policy_draft.md+terms_and_conditions_draft.md need: solicitor-review + LIA-written-as-real-doc + 2 fill-ins(legal-name/address, Render-hosting-region)
  ⚠4 ~22 HMO-candidate councils unverified(see MESH-SCRAPER above)
  ⚠5 HMO contractor-finder word-lists unspotchecked(see CONTRACTOR-FINDER above)
  ⚠6 GLA_API_KEY invalid,renewal-cadence-∅unknown,DO-NOT-assume-weekly
  ⚠7 old-handoff Task4(domestic/householder-scraper-overhaul) NEVER BUILT — domestic_scrapers.py still original 2469-byte file, confirmed-by-grep, no scrape_householder_planning_applications/scrape_rated_people_jobs exists anywhere
  ⚠8(low-pri,watch-only) Dacorum council RemoteDisconnected on every mesh term(likely TLS/WAF-fingerprinting requests-lib,not payload-bug,confirmed via real-browser same-request-succeeds;1-council-only,revisit w/ curl_cffi only-if-spreads) + occasional false-positive "SCRAPER PAGE STRUCTURE" alerts on working councils(Croydon,Cornwall),likely "no-results"-wording-variant,∅urgent

OLD-AI_HANDOFF-5-TASKS(from stale 28Aug version,VERIFIED-BY-GREP-Sep2,not just assumed):
  Task1(haversine-radius-matching)=✅DONE(found in database.py+notifications.py)
  Task2(quota-enforcement/TIER_QUOTAS)=✅DONE(found in database.py)
  Task3(login-ghost-session-prevention/get_contractor_subscription)=✅DONE(found in database.py+main.py)
  Task4(domestic/householder-scraper-overhaul)=⛔NOT DONE(domestic_scrapers.py unchanged,2469B,confirmed-empty-of-these-functions) ← SAME AS ⚠7 ABOVE, real gap
  Task5(lead-ID/ref-dispatch-burn-fix,`id::text`)=✅DONE(found in database.py+main.py)

HOW-WE-WORK(#2, compressed — full version has the concrete evidence for each of these, worth reading once):
  mandate=carte-blanche-on-code+bugfixes, NOT-carte-blanche-on-infra/cost/vendor-decisions(those get surfaced as real choices,e.g.Gemini-vs-OpenAI via AskUserQuestion)
  core-value="no stone unturned"=NEVER trust research(mine OR Gemini's OR anyone's) w/o live-verification(real browser/API/govt-dataset) before it ships — proven necessary repeatedly(17%-Gemini-error-rate above is the sharpest example)
  Nick checks my work + is usually right to push back — 2 concrete examples this session: (a)caught me overclaiming "this session burned a week's usage" from a UI banner alone w/o seeing his real numbers — I was wrong, corrected myself (b)caught, via one clarifying question, that my first "point a new session at TASKS.md" fix was hollow(that file never left my ephemeral sandbox) — led directly to THIS document existing
  cost-consciousness=real,explicit,tracks own usage%,directly asked how to reduce cross-session re-read overhead → this file IS that answer, re-read it instead of re-deriving context when starting fresh
  process-honesty>looks-done: caught+disclosed the sandbox-vs-real-disk conflation myself rather than letting it surface as a mystery prod bug later — valued more than a clean status report
  production=real+live+revenue-flowing(tree vertical) → treat every deploy w/ real caution, prefer additive/backward-compatible changes, add self-healing fallbacks where migration-failure-under-load is plausible(demonstrated,not theoretical,see INCIDENT-LOG)
  practical: git-push auto-deploys(no manual step) | local sync=OneDrive | Nick runs UPDATE_WEBSITE.bat locally | possibly works-nights-BST(carried from OLDER handoff,∅independently-verified-this-session) | separate not-yet-started "next project"=autonomous trading agent,see companion TRADING_BOT_HANDOFF.md(explicitly LOWER-confidence than this doc)
  comms-style-that-works: direct,no-fluff,show-tradeoffs-not-recommendations-dressed-as-fact,plain-statement-when-something's-wrong not softened
```

---

## 1. WHAT THIS PROJECT ACTUALLY IS NOW

TreeKey started as a single-vertical (tree surgery) lead-gen SaaS and is mid-expansion into a **multi-vertical planning-lead platform**, per `master_expansion_plan_v2.md`. It scrapes UK council planning application data, classifies each application into zero or more configured "verticals" (currently `tree` and `hmo`), and sells matching leads two ways:

- **Subscriptions** (tiered, £/month) — leads auto-dispatched to a contractor's postcode/radius
- **Pay-per-lead marketplace** — one-off purchases, lead burned after sale

The **tree vertical is live and revenue-capable today** — real leads, real councils, real classifier, real payment flow (Stripe). The **HMO vertical is built and tested but pre-revenue** — no pricing tier, no customer-facing product decision made yet, deliberately gated behind proving the pipeline finds genuine leads first (per the plan's own "smallest footprint to prove it" build order).

Business structure per `MANIFEST.md`: **Vector Data Labs** is the parent/holding entity (payments, infrastructure); **TreeKey** is the customer-facing brand.

---

## 2. HOW NICK AND I WORK TOGETHER — READ THIS BEFORE DOING ANYTHING

This is the part Nick specifically asked to be captured. It's not fluff — getting this wrong wastes his time and money.

**Standing mandate:** carte blanche to fix bugs and proceed through `master_expansion_plan_v2.md` without asking permission for code-level work. "Make sure everything is concrete and robust and of the highest quality... build a robust, functioning sustainable business model that is autonomous and actually makes money." Carte blanche does **not** mean no judgment — surface real infrastructure/cost decisions (see the Gemini-vs-OpenAI choice in §6) rather than picking silently, especially anything that adds a new paid dependency or touches production.

**"No stone unturned" / live-verification is a core value, not a one-off request.** Nick does not want research trusted just because an AI (me or Gemini) produced it. Concrete evidence this matters: a Gemini-research council/portal table came back with a confirmed ~17% wrong rate against councils I'd actually live-verified in a browser (Camden, Hounslow, Islington, Merton, Manchester were all wrong) — caught by cross-checking, not by assuming Gemini got it right. The house rule that follows: **any scraper/portal target or factual claim that will be built into shipped code gets live-verified (real browser, real API call, real government dataset) before it's trusted, no matter who or what produced the claim.**

**He checks your work, and he's right to.** He caught me making an unfounded claim (that "this session" had burned a week's usage) purely from re-reading a compose-box banner literally — he asked a direct, sharp question ("are you saying you burned through a week's usage in 6 hours?") and was right to push back; I hadn't actually seen his account's real usage numbers. He also caught, by asking a clarifying follow-up question, that my first suggested continuity fix ("just point a new session at TASKS.md") was hollow — that file only ever existed in my ephemeral cloud sandbox, never reached his real disk. Lesson: don't state something as settled/verified unless it actually is; when he pushes back, take it seriously rather than reassuring him — he is usually right to push.

**Cost-consciousness is real and explicit**, not paranoia. He tracks his own usage %, has directly asked "is there anything we can do to address [burning through usage]... anything to break up the work or to condense your need to reread?" This file — and the discipline of writing one at natural junctions — is the direct answer to that question. When usage is tight, don't burn a session re-deriving context that's already written down somewhere; read the handoff/manifest/plan docs first.

**Process-gap honesty over "looks done."** Earlier this session I found that several changes marked "DONE" had only ever landed in my own cloud sandbox, never actually reached Nick's real OneDrive-synced folder (a second, identically-named "VECTOR DATA LABS" folder existed in my own workspace and I'd been conflating the two). I told him directly and fixed it, rather than letting it surface later as "why doesn't this work in production." He values that kind of catch far more than a clean-looking status report. **The enforced habit now: before calling anything "done," run `device_list_dir` on Nick's actual folder and confirm the byte size/mtime matches what was just tested — not just "I wrote a file."**

**Production is real and live — treat every deploy with real caution.** This isn't a toy project; a bad migration genuinely took lead-capture and the public marketplace to zero for ~18 minutes on Sep 1 (§8 has the full incident writeup). Test thoroughly, prefer additive/backward-compatible changes, and add self-healing fallbacks where a migration could plausibly fail under production load (lock contention on a busy table is a real, demonstrated failure mode here, not theoretical).

**Practical facts:** git push auto-deploys via Render, no manual step. Local files sync through OneDrive; Nick runs `UPDATE_WEBSITE.bat` locally. He's mentioned working nights (BST) per a note from an earlier AI session — I haven't independently verified this myself this session, carrying it forward as plausible context rather than confirmed fact. He has a separate, not-yet-started "next project" (an autonomous trading agent) discussed on a different chat surface — see the companion `TRADING_BOT_HANDOFF.md` for what's known about it; treat that document as much lower-confidence than this one since it's reconstructed from a persistent-memory summary, not the original conversation.

**Communication style that works:** direct, no fluff, show the actual tradeoff rather than a recommendation dressed as a fact, use `AskUserQuestion`-style real choices for decisions that are genuinely his to make (cost, vendor, legal risk), and when something's wrong, say so plainly rather than softening it.

---

## 3. ARCHITECTURE — CURRENT FILE MAP

All paths relative to the VECTOR DATA LABS folder.

| File | Role | Size (Sep 2) |
|---|---|---|
| `main.py` | FastAPI app, all HTTP routes, homepage, admin, endpoints | 293,205 bytes |
| `database.py` | All DB queries: leads, subscriptions, review queue, dedup cache | 97,306 bytes |
| `scanners.py` | Classification engine + 4 live scan call sites (Leeds/GLA/paid-API/PlanIt) + mesh-scan orchestration | 107,686 bytes |
| `mesh_scrapers.py` | Idox council-portal scraper (48-council live-verified registry) | 52,892 bytes |
| `payments.py` | Stripe checkout + webhook | 22,457 bytes |
| `notifications.py` | Email dispatch, lead routing/matching | 31,465 bytes |
| `research.py` | Companies House + Google Places enrichment for scan pipeline | 69,458 bytes |
| `bulk_contractor_extractor.py` | Standalone offline batch tool: builds contractor contact lists (Companies House) — now multi-vertical | 31,035 bytes |
| `persistent_dedup_cache.py` | Generic DB-backed "already queried today" guard, survives redeploys | 5,739 bytes |
| `domestic_scrapers.py` | Domestic/homeowner job scrapers — **still the original file, essentially untouched, see §7** | 2,469 bytes |
| `net_utils.py` | Shared HTTP fetch helpers | 10,590 bytes |
| Test files | `test_scrapers.py` (152KB), `test_database.py` (19.9KB), `test_bulk_contractor_extractor.py` (9.4KB) | 188 tests total |

### The vertical system (the core generalization pattern)

`scanners.py`'s `VERTICALS` config dict is the single source of truth for what a "vertical" is: keyword gold-lists (`TREE_GOLD`, `HMO_GOLD`), a `capture_identity` flag (GDPR posture — see below), and matching logic. Adding a new vertical anywhere in the codebase (classifier, mesh scraper, contractor-finder) means adding a config entry, not forking code — this discipline was deliberately built and is now proven across 3 separate subsystems (classifier, mesh_scrapers.py, bulk_contractor_extractor.py).

`_matches_vertical(text, key)` / `classify_verticals(text)` (multi-label) / `_resolve_vertical(text)` (single-answer, tree wins ties — an interim design choice, see §5 step 2) are the shared resolvers. `leads.vertical` column defaults to `'tree'` for backward compatibility with every pre-existing row/call site.

**GDPR-safe-by-construction for HMO:** `capture_identity=False` for HMO in `VERTICALS`; enforced inside `_insert_lead` itself (not trusted to each caller) — any vertical configured this way has applicant_name/agent_name/agent_company/has_agent/agent_is_tree_surgeon force-nulled before the INSERT, structurally, regardless of what's passed in. Real caveat, not resolved: the plan's own converged compliance research includes an ICO-citation claim about address-only marketing being GDPR-exempt that I could not verify against the live ICO site — this code doesn't depend on that claim, but the business's actual outreach approach for HMO should not lean on it either without a solicitor confirming it specifically (see §7).

### The tiered classifier (Tiers 1-4)

1. **Tier 1 — keyword match** (`TREE_GOLD`/`HMO_GOLD` gold-lists, sanity-checked against real live PlanIt data from Nottingham/Leicester). Deliberately conservative phrasing (e.g. never bare "fell", never bare "sui generis") — false positives cost real classifier trust, proven by finding and fixing exactly this bug pattern twice this session.
2. **Tier 2 — structured field**, PlanIt-only: `app_type.lower() == "trees"` rescues real applications with zero tree keywords in the free text (bare arborist shorthand like "T1 - Cherry - Reduce height by 4m."). Checked and explicitly NOT built for HMO (no usable field exists) or for GLA/paid-API (schemas unverified, didn't want to burn live API quota just to test).
3. **Tier 3 — cheap LLM (Gemini)**, code-complete, shipped to disk, **not yet live**. `_classify_via_llm(description)` — picks a configured vertical key or NONE, never guesses on an unrecognized/error response. `google.generativeai` (not the newer `google-genai` — deliberately, see §6). Runs as a **separate manual batch pass** (`process_review_queue_with_llm`, triggered via `/process-review-queue`, cron-secret auth) against the Tier 4 queue — NOT inline in the scan hot path, since most applications match neither vertical and an inline LLM call per miss would mean thousands of real paid calls/day for near-zero yield. Needs `GEMINI_API_KEY` set in Render to activate; does nothing and errors nothing if absent.
4. **Tier 4 — manual review queue.** `unclassified_applications` table — every application that fails Tiers 1-2 with a real, stable reference gets queued (visible, never silently dropped) instead of the old bare `continue`. `/review-queue` (GET, dashboard-auth) for human visibility. Only queues with a genuine reference (never a timestamp fallback), so a still-live application doesn't re-flood the queue every scan run.

### Mesh scraper (Idox portal network)

`COUNCIL_REGISTRY` — 48 councils, every single entry **live-browser-verified** this session (not assumed from a URL shape) after two full audit passes found and fixed real problems: 12 silently-dead entries (zero error, zero log line — a genuinely dangerous failure mode), 2 wrongly-declared-non-Idox councils that were actually live (Fife, Derby, mounted at non-standard paths), 1 wrongly-removed council reinstated (Sutton — findable with a better search). A permanent regression test (`test_every_registry_entry_is_routable` or equivalent) guards against this exact class of bug recurring silently.

HMO search terms (`IDOX_HMO_SEARCH_TERMS`) run only against `COUNCILS_WITH_CONFIRMED_HMO_ARTICLE_4` (13 councils — a government-data-sourced 35-council HMO-Article-4 list intersected against the already-verified 48-council registry for zero incremental portal-verification risk). The actual bug fixed underneath this: `IdoxScraper.search_tree_applications`'s result filter used to hardcode `is_tree_related()` regardless of which search term found the result — meaning even after adding HMO search terms, a genuine HMO result would have been silently discarded at the filter. Now uses the shared `_resolve_vertical()` resolver and tags each lead with its own `"vertical"` key, which `run_mesh_network_scan` now actually passes through to `_insert_lead` (previously hardcoded to tree).

**~22 more government-data-confirmed HMO councils exist but are deliberately NOT enabled** (Crawley, Newcastle, Sefton, Harlow, Salford, Fenland, Tower Hamlets, Barking & Dagenham, Rother, Rossendale, Tendring, Hillingdon, Halton, North Warwickshire, Ipswich, Burnley, Newcastle-under-Lyme, Basingstoke & Deane, Bury, Hounslow(known Northgate not Idox)) — each needs the same live-browser portal check every existing entry got before being trusted. This is real, valuable, well-scoped future work, not a wiring gap.

### Contractor-finder (`bulk_contractor_extractor.py`)

Standalone offline tool, not part of the live pipeline. `CONTRACTOR_VERTICALS` config (mirrors `VERTICALS`' shape) now has `tree` and `hmo` entries with separate SIC-code trust tiers: `sic_codes_trusted` (name-independent — the SIC code alone is enough) vs `sic_codes_gated` (needs a `required_words` name match too, since e.g. property-management SIC codes cover every letting agent, not just HMO-focused ones). CLI: `python bulk_contractor_extractor.py [tree|hmo]`, defaults to tree, fully backward compatible. **Not yet spot-checked against real live Companies House results** (no `COMPANIES_HOUSE_KEY` available in the cloud sandbox to test against) — do a small real harvest and eyeball the first 50-100 HMO results before trusting this at scale.

---

## 4. CURRENT LIVE PRODUCTION STATE

- `treekey.uk` is live. Tree vertical is generating real leads from real councils today.
- **Sep 1 production incident, resolved:** a schema migration (`ALTER TABLE leads ADD COLUMN IF NOT EXISTS vertical`) hit a lock-timeout under real production write contention and rolled back the entire Phase 1 migration transaction (all ALTERs shared one transaction/commit). Consequence: `_insert_lead`'s INSERT unconditionally listed the `vertical` column → **every lead insert failed silently** (caught by existing per-item try/except, logged not crashed, but zero leads captured); `get_marketplace_leads_with_freshness` hit the same missing-column error → **the entire public marketplace showed zero leads to every visitor**. Real revenue-impacting outage, ~18 minutes (23:28-23:46 UTC), caught only because Nick happened to check an unrelated privacy-policy change and thought to verify. Fixed three ways: (1) `_run_ddl_statements_resiliently()` — each ALTER now its own transaction with a short lock_timeout + retry/backoff, so one contended statement can't roll back its siblings and fails fast instead of queuing behind the full statement_timeout; (2) `_insert_lead` self-heals — catches the specific missing-column error and retries with the old pre-migration column list; (3) `get_marketplace_leads_with_freshness` does the same fallback. **Confirmed resolved live** — redeploy log showed clean Phase 1/2/3, `/privacy-policy` and `/marketplace` both verified serving correctly afterward.
- Tier 3 (Gemini) is shipped to disk, tested, but **needs a Render redeploy plus a `GEMINI_API_KEY` env var to do anything** — until then it's inert by design (`_gemini_model` stays `None`, batch runner returns a zero-result dict immediately, never errors, never blocks anything else).
- `GLA_API_KEY` (GLA Planning Datahub, London coverage) is invalid — a CRITICAL email alert already fired. London still gets leads via PlanIt/paid-API in the meantime. Renewal cadence genuinely unknown — don't assume weekly; check GLA Planning Datahub's own documentation before committing to any recurring reminder.

---

## 5. MASTER EXPANSION PLAN — STATUS AGAINST `master_expansion_plan_v2.md`'S OWN BUILD ORDER

(Numbers below are the plan's own step numbers where identifiable; read the plan doc itself for the full reasoning behind the order.)

- **Step 1 — `verticals` config + generalized classifier**: DONE.
- **Step 2 — wired into all 4 live scan call sites + mesh scraper**: DONE (mesh scraper was explicitly flagged in the plan as separate future work and has now been done too — see §3).
- **Step 3 — GDPR-safe lead format for HMO from day one**: DONE, structurally enforced (see §3). Scope caveat carried forward, not resolved: the compliance strategy's supporting ICO citation is unverified (§7).
- **Step 4 — HMO_GOLD sanity-checked against real data, tiered classifier (T1-T4)**: DONE and live-data-verified (2/2 real HMO positives matched, 0/39 false positives sampled from Nottingham/Leicester).
- **Step 5 — contractor-finder generalized for HMO**: DONE, not yet live-spot-checked (§3/§7).
- **Step 6 — extensions/loft as a third vertical**: NOT STARTED. Plan itself gates this on "once HMO is proven live" — correctly still blocked.
- **Step 7 — reuse `marketplace_engine` from CRAWLER PROJECT**: NOT STARTED. See §6 — real integration cost identified (separate SQLAlchemy schema, not a drop-in), not a quick win.
- **Step 8 (the plan's own audit/verification standard)**: the live-verification discipline itself (§2) — actively being followed, not a one-time step.
- **Step 9 — rebrand/combine into one site**: NOT STARTED, correctly blocked behind HMO actually proving out.

---

## 6. CRAWLER PROJECT TOOLS — REUSE INVENTORY (audited, not all confirmed to exist before this)

An earlier note had wrongly claimed `scoring_engine`, `reporting_engine`, `notification_engine`, `workflow_engine` existed — they don't; they were unbuilt ideas. What's actually real, in `CRAWLER PROJECT`:

- **`entity_graph/graph.py`** — genuinely generic, confidence-scored entity dedup/relationship store, biased against false merges. Candidate for contractor growth-signal intelligence. Not yet used by TreeKey.
- **`marketplace_engine`** — SQLAlchemy Actor/SupplyRecord/DemandRecord models, well-designed but its own separate schema — real integration cost if it ever becomes TreeKey's lead-access/subscription layer, not a drop-in to the raw-psycopg2 codebase.
- **`escrow_engine`** — wrong shape for a lead-sale model, correctly skipped.
- **`outreach_engine`** — working Gemini-based personalized-message generator (`reply_classifier.py` is literally the pattern this session's Tier 3 classifier reused). Relevant both for a future print-and-post/email upsell and as the reference pattern for any future Gemini delegation.
- **`semantic_engine`** — LlamaIndex + chromadb + Gemini embeddings, real new dependencies, candidate for a future Tier 3 alternative or document parsing, not urgent.
- **`scraper/net.py` + `scraper/pacing.py`** (`SmartFetcher`/`CircuitBreaker`/`DomainPacer`) — the concrete, still-not-ported fix for the mesh-scraper traffic-scaling question as council count grows.

**Gemini choice for Tier 3, reasoning worth preserving:** deliberately used the older `google.generativeai` library (already proven in `outreach_engine`) over the newer `google-genai` SDK — two fetched docs gave conflicting call shapes for the new package and it couldn't be end-to-end tested without a live key, so didn't gamble on unverified surface for a production dependency. Model pinned to `gemini-flash-latest` (Google's version-agnostic alias) so it doesn't go stale as models are superseded. Nick chose Gemini over OpenAI himself, via a real tradeoff shown with `AskUserQuestion`, once it was clear this needed a genuine cost/infrastructure decision only he could make.

---

## 7. OPEN RISK ITEMS — NOT RESOLVED, DON'T ASSUME THEY ARE

1. **Gemini's council/portal research has a confirmed ~17% error rate** on the rows I could independently check (5 of ~29: Camden, Hounslow, Islington, Merton, Manchester all wrong). Treat the other ~24 unverified rows the same way — live-browser-check before any of it enters a `verticals`/registry config, per the plan's own rule that scraper/portal targets always need live verification regardless of who researched them.
2. **The LIA draft's core compliance argument leans on an ICO quote I could not verify** ("delivering marketing material to a specific address, but not to a named individual... does not involve processing of personal data") against the live ICO site. This is the same claim the plan's own earlier 4-AI cross-check had already flagged as "the weaker, less-cited claim." Don't build the HMO outreach approach around it; keep the LIA's general structure (reasonable boilerplate) but have a solicitor specifically confirm or strike that framing.
3. **Privacy Policy / Terms drafts** (`privacy_policy_draft.md`, `terms_and_conditions_draft.md`) are tightened against the actual codebase but need: solicitor review, the LIA actually written as a real document (not just referenced), and 2 fill-ins (legal name/address, confirming Render's hosting region).
4. **~22 government-data-confirmed HMO councils not yet live-verified** for the mesh registry — real, well-scoped future work (§3).
5. **HMO contractor-finder word lists never spot-checked against real Companies House results** — no API key available in the cloud sandbox to test with (§3).
6. **`GLA_API_KEY` needs renewal**, cadence unknown — check GLA Planning Datahub's own docs, don't guess weekly.
7. **Old-handoff Task 4 (domestic/householder scraper overhaul) was never built.** `domestic_scrapers.py` is still the original ~2.4KB file — confirmed by direct grep this session, no `scrape_householder_planning_applications` or `scrape_rated_people_jobs` function exists anywhere in it. This is real, not-yet-started work, distinct from (and probably lower priority than) the HMO vertical build.
8. **Two low-priority, watch-don't-fix items**: Dacorum council throwing `RemoteDisconnected` on every mesh search term (likely TLS/WAF fingerprinting of the `requests` library, not a payload bug — confirmed the same request succeeds from a real browser; only 1 council so far, revisit with a TLS-impersonating client like `curl_cffi` only if this spreads to more councils). Occasional false-positive "SCRAPER PAGE STRUCTURE" alerts on working councils (Croydon, Cornwall) for individual search terms — likely just a "no results" wording variant, not systemic; not urgent since leads are flowing.

---

## 8. THE PROCESS GAP THAT WAS FOUND AND FIXED — DON'T REPEAT IT

My cloud workspace has its own folder also named "VECTOR DATA LABS" (where I edit/test everything). Earlier this session I found `database.py`, `main.py` on Nick's *real* disk were stale — missing fixes marked "DONE" — and `master_expansion_plan_v2.md`, `persistent_dedup_cache.py`, `test_database.py` didn't exist on his real disk at all, despite being fully built and tested. Root cause: conflating the two identically-named folders; explicit sync calls had only actually reached his disk for a couple of files, not everything I'd been calling done. One of those un-synced files (`scanners.py`'s new `import persistent_dedup_cache` line) would have crashed the app with `ModuleNotFoundError` on startup if Nick had deployed in that state.

**Enforced discipline now, follow it every time:** `device_list_dir` (fresh mtimes on Nick's real folder) → `SendUserFile` → `device_commit_files` (with `expectedMtimeMs` guards) → `device_list_dir` again to confirm byte-for-byte match. Never mark something "done" purely because it tested clean in the sandbox.

---

## 9. RECOMMENDED NEXT STEPS FOR A FRESH SESSION

1. Read this file, then `master_expansion_plan_v2.md`, then skim §14 of `PROJECT_STATE.md` (the entry this session added) if more historical detail is needed.
2. Check whether Nick has set `GEMINI_API_KEY` in Render and redeployed — if so, Tier 3 is live; verify via `/review-queue` and `/process-review-queue`.
3. `MANIFEST.md`'s architecture section (§2, dated 24 Aug) is now meaningfully stale — it doesn't mention the vertical system, the tiered classifier, or the HMO mesh scraper at all. Worth a dedicated rewrite pass at some point, but wasn't done in this pass to control scope — flagging rather than silently leaving it wrong.
4. Before adding any new HMO council to `mesh_scrapers.COUNCIL_REGISTRY`, live-verify it in a real browser first — no exceptions, this exact shortcut has already caused real bugs twice.
5. Whatever gets done, update this file (or write the next version of it) at the next natural junction, the same way this one was written — Nick has explicitly said this is the continuity mechanism he wants.

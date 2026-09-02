2. **Planning Radar (Great Britain):** **800+ Live Statutory Planning Notices** actively monitored across all 309 English councils, all 32 Scottish local authorities, and all 22 Welsh unitary councils.
3. **Interactive Homepage Radar (`/`):** Click-to-move pin, smooth panning (zero auto-zoom), universal postcode/city lookup, continuous harmonic micro-density lead/value recalculations, and 5–25 mile radius selector.
4. **Master Autonomous Guardian & Alert Sentry:** Predictive burn-rate calculation for API quotas, ultra-bold ALL-CAPS incident email alerts across all failure tripwires (UK Planning API, Companies House, Google Places, London GLA, Supabase, Stripe).
5. **Standalone 2,000+ Contractor Extractor (`bulk_contractor_extractor.py`):** 100% isolated harvesting tool covering England, Scotland, and Wales.
6. **Master Daily Pipeline (`/trigger-daily-pipeline`):** Automated 6:00 AM morning sweep across England, Scotland, and Wales with automatic lead pricing, deduplication, and contractor discovery.

---

## 📋 MASTER SPRINT TO-DO QUEUE

### 1. 📧 Business Email & Domain Infrastructure (100% Complete)
- [x] **Custom Domain Setup:** `https://treekey.uk` live with active SSL on Cloudflare & Render.
- [x] **Primary Business Email:** `contact@treekey.uk` active and forwarding to personal inbox.
- [x] **Production Endpoints:** `PUBLIC_APP_URL` and Stripe webhooks updated to `treekey.uk`.

---

### 2. 💳 Payment Portals & Banking
- [x] **Stripe Bank Card / Apple Pay Engine:** 100% Live, dynamic checkout accepting Visa, Mastercard, Amex, Apple Pay, Google Pay.
- [ ] **PayPal Business Verification:** Clear photo ID verification in Resolution Centre to link Lloyds bank account.

---

### 3. 🎨 Website, Branding, Logo & Copy Polish
- [x] **Brand Identity & Logo:** Custom transparent vector logo and square app icons crafted, anti-aliased, and mounted to navigation bar.
- [x] **Homepage Radar Graphic Fix (Aug 28 2026):** The rotating sweep on the homepage
  radar icon was orbiting the center of its own quarter-wedge div instead of the
  circle's true center (`transform-origin` was `50% 50%`, needed `0% 100%` since the
  wedge is positioned top-right of the circle). Fixed in main.py's `.radar-sweep` CSS.
- [x] **NEXT ACTION (Aug 28 2026):** Tailwind is loaded from `cdn.tailwindcss.com` —
  the browser itself warns this "should not be used in production." Fine at near-zero
  traffic, but should be swapped for a proper built/compiled Tailwind before the cold
  email sequence sends real traffic to the homepage (affects load speed / first
  impression). Nick confirmed — do this one first, next session. **DONE (Aug 29 2026):** Compiled `static/tailwind.css` locally and updated `main.py` templates to use `<link rel="stylesheet">`.
- [ ] **PRIORITY 1 FOR NEXT SESSION (Nick confirmed Aug 28 2026):** the four items
  below — hero copy, trust badges, pricing/lockout copy, FAQ. Nick agreed these
  matter more than anything else content-wise since they directly affect whether a
  contractor converts on their first visit, which matters a lot once cold email
  traffic actually starts arriving.
  - [x] **Hero Section & Value Proposition — DONE (Aug 29 2026):** replaced with the
    "Live Signal" redesign (see below).
  - [x] **Trade Credibility & Badges — wording fixed (Aug 29 2026):** see "Wording
    precision" note below, folded into the same edit.
  - [ ] **Pricing Table & Lockout Text:** Fine-tune exclusive radial territory lockout copy and credit pack terms. NOT done yet.
  - [x] **FAQ Section Review — DONE (Aug 29 2026):** Replaced obsolete copy with explicit "Radial Territory Exclusivity" explanation, notice speed, and rolling monthly/credit pack options.
  - [x] **Wording precision — DONE (Aug 29 2026):** the homepage badge "Authorized UK
    Statutory Planning Data" reworded to "Published Under The Open Government
    Licence" — same true claim, no longer readable as government sanctioning
    TreeKey specifically.
- [x] **Homepage hero redesign — "Live Signal" (Concept A) implemented live (Aug 29
  2026):** Nick picked Concept A from the rebrand comparison artifact (a live
  console-feed hero instead of the old radar-icon-and-headline layout) after two
  rounds of design iteration. Replaced the hero in `main.py`'s `public_homepage()`:
  dropped the spinning radar icon and `/static/hero_bg.jpg` background, added a
  dark-green console panel showing the 5 most recent real leads from the DB
  (`stats["sample_leads"]`) as monospace rows (time / council ref / description /
  size tag / LIVE badge), then the headline+CTA+trust-badges below it, unchanged
  functionally (still links to `#radar`, `/checkout/...`, etc). **Important catch
  during implementation:** the design mockup's rows showed a specific £ "job value"
  per lead (e.g. "£1,900"). Checked whether that's backed by real data before
  shipping it — it is not. `leads.lead_price` (`scanners.py` `score_lead()`,
  £25/£50/£75) is the price of the *lead itself*, not an estimate of the
  underlying tree job's value — using it as "job worth" would have been actively
  misleading (exactly the "looks like a price they'd pay" confusion Nick flagged
  earlier, just with a real number attached). No field in the schema estimates
  actual job/contract value. **Fix:** dropped the £ figure from the live version
  entirely; each row instead shows the real `lead_score` tier as "SMALL/MEDIUM/LARGE
  JOB" — honest, since that field genuinely is a size classification, not invented.
  Verified: `python3 -m py_compile` passes, and the homepage function was extracted
  and executed standalone with mock DB rows (including `None` values) confirming it
  renders complete, balanced HTML with no unresolved template placeholders and none
  of the old "Authorized..." wording left behind. Not yet re-verified against a live
  deploy (that happens once Nick runs `UPDATE_WEBSITE.bat` / deploys on Render).
  Concept A was explicitly "subject to change" — Nick may still want further passes.
- [ ] **Marketplace / Ledger / Chip-Drop / Storm Radar nav items — CLARIFIED (Aug 28
  2026):** These are NOT meant to be generic subpages — they're meant to be given
  free to tree surgeons (whether linked from the homepage, or bundled in with a
  purchased lead), specifically so TreeKey feels like it understands and sympathises
  with tree surgeons' real problems, not like a site built just to grab quick cash
  fast. This intent isn't actually written down anywhere Claude could find — it's
  NOT in MANIFEST.md as it currently stands (checked Aug 28 2026) despite Nick
  believing it should be. **Action:** document this intent explicitly in MANIFEST.md
  (or here) so it isn't lost, and check these pages' actual current behaviour against
  it — right now they're plain top-nav links with no visible framing as "a free
  goodwill tool," which undersells the actual intent behind them.


---

### 4. 📨 Multi-Channel Outreach & Revenue Launch

- [ ] **Cold Email Sequence Copywriting (Operator using Claude):**
  - **Email 1 (The Free Lead Gift):** 1 free local planning notice in their postal district.
  - **Email 2 (The Competitive Moat):** Exclusive 15-mile radial territory lockout (£149/mo).
  - **Email 3 (The Soft Close):** £49/mo Regional Plan or £80 10-Lead Credit Pack.
  - **Personalization Tokens:** `{{director_name}}`, `{{company_name}}`, `{{city}}`, `{{recent_tpo_street}}`.
- [x] **Outreach Tool Warmup:** LIVE via Instantly.ai — started Aug 25 2026, nick@treekey.uk.
  Throttle raised Aug 28 2026: 2 emails/day (up from 1), max 40/day (up from 30),
  reply rate 35% (up from 30%).
- [ ] **Direct WhatsApp / SMS Campaign:** Draft conversational message templates targeting the **980 Managing Director mobile numbers** on file.

---

### 5. 🏡 Domestic Listing Section (Future — replaces the removed scraper)
- [ ] The old `domestic_scrapers.py` (Gumtree/Reddit/social/local-press scraping) was
  removed Aug 28 2026 — it carried real UK GDPR/PECR risk with no clean fix at scale
  (see project memory: `project_treekey_business_model.md`).
- [ ] Replace with a consensual **homeowner-submitted job listing page**
  (`/list-your-job` or similar): the homeowner posts their own job directly to
  TreeKey, same as Checkatrade/MyBuilder/Rated People — sidesteps the legal problem
  entirely since it's their own request, not a scraped third-party post.
- [ ] Needs: public form + route, a `domestic_listings` table, spam/abuse protection
  (reuse `_check_rate_limit`), and routing submissions into the existing
  `leads` table / `dispatch_lead_alerts` pipeline so no downstream code changes.
- [ ] `domestic_scrapers.py` currently a no-op stub (`ingest_and_route_domestic_leads()`
  returns 0) so the 3 existing call sites in main.py don't break in the meantime.

---

## 7. RENDER ENVIRONMENT VARIABLES

| Key | Status | Description |
|---|---|---|
| `SUPABASE_DB_URL` | ✅ Set | PostgreSQL database |
| `TRIGGER_SECRET` | ✅ Set | Cron security gate (`arsenal`) |
| `DASHBOARD_USER` | ✅ Set | Dashboard basic auth username |
| `DASHBOARD_PASS` | ✅ Set | Dashboard basic auth password |
| `COMPANIES_HOUSE_KEY` | ✅ Set | Companies House Search + Officers API |
| `GOOGLE_MAPS_KEY` | ✅ Set | Google Places search + place details (rating, phone, website) |
| `GLA_API_KEY` | ✅ Set | London GLA Planning Datahub API |
| `UK_PLANNING_API_KEY` | ✅ Set | UK Planning API for nationwide councils |
| `RESEND_API_KEY` | ✅ Set | Transactional email alerts & quota warnings |
| `TEST_EMAIL` | ✅ Set | Destination email for leads and guardian alerts |
| `PUBLIC_APP_URL` | ✅ Set | `https://treekey.uk` — used in sitemap, robots.txt, and email links |
| `STRIPE_SECRET_KEY` | ✅ Set | Stripe payments live & verified |
| `STRIPE_WEBHOOK_SECRET` | ✅ Set | Stripe webhook listener |

 
## REMAINING PRE-LAUNCH TO-DO LIST
1. **[BLOCKED] Overturn PayPal Ban:** Call PayPal UK Business Support (020 8080 6500) to demand a human review of the automated ID ban glitch. (Priority: Low - Stripe is functioning).
2. **DNS Email Deliverability — DONE (Aug 29 2026):** SPF, DKIM, and DMARC are fully configured in Cloudflare for Google Workspace. Verified live.
3. **Cold Email Copywriting:** Write the high-converting 3-step email sequence.
4. **ICO Registration:** Register Vector Data Labs with the ICO and pay the £40 fee.
   **Nick's call (Aug 28 2026): deferring until just before the cold-email sequence
   actually goes out**, not before.
5. **Database Expansion Strategy (Phase 2) — IN PROGRESS (Aug 28 2026):** Added a
   Companies House SIC-code search pass to `bulk_contractor_extractor.py` (Stage 1b),
   alongside the existing name-substring search — catches genuinely relevant
   companies whose name gives no clue what they do (e.g. "Greenwood Grounds Ltd"),
   which the old approach could never find. Uses SIC codes 02100/02200/02400
   (silviculture/logging/forestry support — accepted on SIC alone) and 81300
   (landscaping — broader, so still name-gated). **Not yet run/verified live** —
   the `sic_codes` and `location` params are confirmed real in the CH API spec, but
   exact `location` matching behaviour (postcode vs. free-text town name) hasn't
   been tested against the live API. Run it once and sanity-check the result count
   before trusting it at full scale. Other ideas surfaced but not built (would need
   live browser inspection to scrape reliably, or are separate paid-API integrations):
   trade-body directories (e.g. Arboricultural Association's Approved Contractor
   Directory — real, but is an interactive postcode/name search, not a static list,
   so needs per-postcode query automation), Google Places business listings.
6. **Lead Scope Expansion (Phase 2):** Domestic/residential source — see item 5 in
   the Master Sprint Queue above (old scraper removed for GDPR/PECR risk, replacement
   listing-page concept documented there). Industrial tree surgery leads —
   not yet explored.
7. **[PERSONAL — NOT a TreeKey/Vector Data Labs task]** Explore adapting the general scraping/dedup/enrichment pattern (not the UK-specific data sources) into a small personal, non-commercial tool for finding industrial metalwork/welding project leads in the Philippines, to occasionally help a friend. Likely data sources: PhilGEPS (notices.philgeps.gov.ph — public government procurement/tender notices, the closest Philippine parallel to UK council planning notices) for project listings, and SEC/DTI Philippines business registries for the Companies-House-style enrichment side. Not started.
8. **Contractor Portal Upgrades (Phase 2) — PART 1 DONE (Aug 28 2026):** Notification-preference
   toggle built: a `/settings` page (session-gated) where a contractor picks
   Email-only vs. Email+WhatsApp-forward-buttons, stored in
   `contractor_subscriptions.notification_preference` and read by
   `dispatch_lead_alerts` when building the lead-delivery email. Note: this adds a
   click-to-forward WhatsApp link per lead, it is NOT push delivery via WhatsApp's
   Business API (no such integration exists/is configured). **PART 2 NOT DONE:**
   interactively drawing/picking a custom lead-alert area on a map — deliberately
   deferred, it needs a JS mapping library (e.g. Leaflet), polygon storage, and
   reworking the core dispatch-matching geometry from radius-based to
   polygon-based. Worth its own dedicated pass rather than bolting on quickly.
9. **Council Lead-Detection Quality (Aug 28 2026) — DONE for mesh_scrapers.py, NOT
   applied to UK Planning API / GLA:** Nick asked whether item 5's Companies-House
   SIC-code insight (a structured field beats free-text keyword matching) applies
   to the council/lead-discovery side too. Findings:
   - `mesh_scrapers.py`'s direct Idox council scraper had its own separate, weaker
     keyword list (`"crown"`, `"branch"`, `"oak"`, `"fell"` as bare words) — riskier
     for false positives than `scanners.py`'s `TREE_GOLD` compound-phrase list
     (built specifically to avoid "Crown Street", "bank branch", "fell down", etc).
     **Fixed:** `mesh_scrapers.py` now imports and reuses `scanners.TREE_GOLD`.
   - Idox's advanced-search "description" field only accepts one plain-text term
     (no boolean OR), so a single search for "tree" was silently missing genuine
     tree-work applications worded without that literal word (e.g. "TPO: pollard
     protected oak"). **Fixed:** added a 3-term multi-pass search (`tree`, `tpo`,
     `hedge`) per council, deduped by reference — same multi-pass pattern as item
     5's SIC-code expansion. **Caveat, same as item 5:** not load-tested live —
     3x the requests per council portal, watch for 429s/soft-bans before trusting
     it at full national scale; can drop back to 1 term for any council that
     complains.
   - The UK Planning API (`ukplanningapi.co.uk`) and London GLA Datahub feeds in
     `scanners.py` are a different case: they're queried broadly with no filter at
     all (`status: "received"`, no keyword param) and rely entirely on the
     `TREE_GOLD` client-side filter. I did **not** guess at an undocumented
     "application type" query parameter for either — unlike Companies House
     (whose `sic_codes` param I verified live against their own API spec this
     session), I haven't checked these two APIs' actual docs, and a wrong guessed
     param name could silently narrow results with no error, quietly losing real
     leads. If Nick wants this pushed further, the next step is a live doc check
     of both APIs (same as was done for Companies House and CARTO) before
     changing anything — not a guess.
   - **Scraper foundation hardening pass (Aug 29 2026) — DONE:** Nick asked for an
     honest 0-100 rating of the scraper as an engineering foundation (separate
     from whether tree surgery is the right business). Initial rating: 62/100.
     Real strengths found: correct cross-thread rate limiting for Companies
     House (a shared lock/timestamp, not a naive per-thread sleep — catches a
     concurrency bug most multi-threaded scrapers get wrong), proactive
     `notifications.send_system_incident_alert()` calls on 401/429s, and
     correct CSRF/session handling for the Idox POST search flow. Real gaps:
     TLS verification disabled everywhere (`verify=False`), no retry/backoff
     on transient failures, HTML-scrape structural breakage (a council
     changing their Idox theme) was silently indistinguishable from a
     genuine zero-result search, and zero automated tests. Nick asked to fix
     as much as possible without changing current behaviour, then again to go
     as far as possible. **Built:**
     - `net_utils.py` (new, shared by scanners.py/mesh_scrapers.py/research.py/
       bulk_contractor_extractor.py): `smart_get`/`smart_post` — verify-TLS-
       first with a one-time unverified fallback (throttled per-domain alert
       on fallback, so a bad cert is finally visible instead of blanket-
       disabled forever), exponential-backoff-with-jitter retry on timeouts/
       connection errors/5xx (default 2 extra attempts), and a 429 is
       deliberately passed straight through untouched so each call site's
       existing bespoke rate-limit handling stays in charge. Drop-in: same
       `requests.Response` return type, same exceptions on final failure, so
       every call site only needed `requests.get(` → `net_utils.smart_get(`
       (and drop `verify=False`) with no other logic changes.
     - `mesh_scrapers.py`: added `IdoxScraper._alert_possible_structure_change()`
       — when a search returns HTTP 200 but the page is neither a results
       list, the single-result redirect, nor recognisable as a genuine
       "no results" page, it now fires a throttled WARNING alert instead of
       silently returning `[]` indistinguishably from a real empty search.
       Heuristic-based (checks for common Idox "no results" phrasing), not
       verified against every council's theme — documented as such in the
       code, same honesty standard as the rest of this project's caveats.
     - `test_scrapers.py` (new, stdlib `unittest` + `unittest.mock` only, no
       new dependency, runs via `python -m unittest test_scrapers.py -v`,
       currently 16 tests, all passing, ~0.02s, zero real network calls):
       covers TREE_GOLD true/false-positive filtering, lead scoring tiers,
       Idox CSRF/results/redirect/structure-change parsing against fixture
       HTML, the multi-pass dedup in `scrape_mesh_council`, and every branch
       of `net_utils`'s retry/TLS-fallback/429-passthrough/session-reuse
       logic. **Writing the false-positive test caught a real bug before it
       shipped further:** `TREE_GOLD` had a bare `"fell "` entry that matched
       almost any ordinary sentence using "fell" as a verb ("a branch fell in
       the storm", "the applicant fell ill", "the company fell behind") —
       directly contradicting the false-positive protection this list's own
       comment says it exists for. Removed; `"fell 1"/"fell 2"/"fell 3"`,
       `"fell to ground"`, and `"felling"` already cover genuine tree-work
       phrasing, confirmed via the new test suite.
     - **Deliberately NOT done, and why:** the near-duplicate per-region
       scanner functions in `scanners.py` (Leeds/London/etc — the same
       drift risk that caused the original `mesh_scrapers.py` vs
       `scanners.py` keyword-list gap) should be consolidated into one
       parameterized scanner, and the Companies House rate limiter is
       in-process memory only (fine on a single Render instance, would need
       Redis/DB-backed shared state to stay correct across more than one).
       Both are real architectural changes, not safe additions — a DRY
       refactor risks quietly changing one region's behaviour, and adding
       Redis is a new dependency for a scaling problem that doesn't exist
       yet at single-instance scale. Held back deliberately for their own
       dedicated pass with region-by-region verification, not bundled in.
     - Verification: `python3 -m py_compile` on all 5 touched/new files,
       plus the full `test_scrapers.py` suite, both run and passing in this
       session before shipping (not just "should work"). Not yet exercised
       against a live council portal in production — verified logic-correct,
       not yet load-tested live (same caveat as the rest of this item).
10. **PWA / "installable website" (Aug 29 2026):**
    - **PART 1 (DONE):** Added `manifest.json`, Service Worker caching, and iOS/Android meta tags. The app is now installable to the home screen.
    - **PART 2 (NOT STARTED):** Actual Web Push API push notifications. Good candidate for post-launch polish.
11. **Pre-launch data-integrity audit (Aug 30 2026) — Agent/Applicant capture DONE, both follow-on items now fixed in item 12 below:**
    Nick, anxious ahead of sending outreach emails, asked me to verify (not assume) whether
    the leads are real and whether "is this lead already taken by a contractor" is knowable.
    Live-checked Cornwall Council's actual Idox portal (not a guess): 101 real TPO
    applications filed in a 3-month window, and 6 individually opened — 3 had no Agent
    listed (genuinely open), 3 already named a tree-surgery company as Agent (already
    taken). Roughly 50/50 on that small real sample — nowhere near the fabricated
    "75% already gone" figure another AI tool gave Nick with no real data behind it
    (it admitted its own scrape attempts failed, then invented percentages anyway).
    Found three concrete issues while tracing this:
    - **DONE — Agent/Applicant capture added to `mesh_scrapers.py`:** the scraper
      previously only read the search-results listing, which Idox never puts
      Applicant/Agent info in. Added `IdoxScraper._fetch_applicant_and_agent()`,
      which opens each application's own `activeTab=details` page (confirmed live
      against Cornwall's real markup) and reads Applicant Name, Agent Name, and
      Agent Company Name. New `leads` columns via `ALTER TABLE ... ADD COLUMN IF
      NOT EXISTS` in `database.py`: `applicant_name`, `agent_name`,
      `agent_company`, `has_agent` (nullable boolean — NULL means "not checked
      yet", never treat it as "no agent"). `_insert_lead()` in `scanners.py`
      updated to accept and store these; only the mesh/Idox path currently
      populates them (~45 councils in `COUNCIL_REGISTRY`) — the Leeds/London/
      other bespoke scan functions in `scanners.py` don't go through
      `IdoxScraper` and still pass NULL for these fields, same as before.
      4 new tests added to `test_scrapers.py` (fixture HTML matches Cornwall's
      real page structure exactly) — full suite now 20/20 passing. Adds one
      extra HTTP request per real lead found (not per search), with a small
      delay between them; not yet load-tested against a live council at full
      scan volume — same caveat as the rest of this project's scraper work.
      **What this can never do:** councils do not publish a homeowner's phone
      number or email, ever — this was confirmed by checking what the existing
      `homeowner_contact` field actually contains across all 1,085 current
      leads: only 2 rows have anything in it, and both are placeholder/test
      data (an Ofcom-reserved fictional phone range, `@example.com` addresses).
      So Applicant Name + property address is the real ceiling of "contact
      info" this data source can ever provide — not a bug, a hard privacy-law
      limit. Product/marketing copy should describe leads as name+address
      leads (door/letter outreach), not phone-ready leads.
    - **DONE (Aug 30 2026, see item 12) — fabricated numbers shown to a
      prospect before they pay.**
    - **DONE (Aug 30 2026, see item 12) — `council_source` region-label
      mismatches.**

12. **"Fix everything" pass (Aug 30 2026)** — Nick's instruction after the
    audit above: "implement everything you think is a good idea and right,
    fix everything you think needs fixing... across the whole project." All
    items below verified with `python3 -m py_compile` on every touched file
    and the full `test_scrapers.py` suite (32/32 passing) before being
    written to disk; committed to device after.
    - **Root cause of "0 leads found everywhere" (both pipeline stages) —
      FIXED.** Live-tested directly against `planit.org.uk` (the free
      fallback used when a paid `UK_PLANNING_API_KEY` region search comes up
      short, or no key is set at all) and found three compounding bugs: (1)
      wrong query param — code sent `postcode`, PlanIt requires `pcode`; (2)
      a required `krad` search-radius param was missing entirely, confirmed
      live via `{"error": "P0001: No valid query field combination
      supplied"}`; (3) even fixed, the 1-2 letter area codes already in
      `CITY_POSTCODE_PREFIX` (e.g. `"B"`, `"WS"`) aren't valid values for
      PlanIt's `pcode` geocoder, confirmed live via `{"error": "pcode:
      Invalid format"}`. Separately, PlanIt returns these error bodies with
      HTTP 200, and the old code only checked `status_code == 200` — so
      every one of these failures was silently logged as "0 new leads
      found" with no visible error. `scan_city_planning_api()` in
      `scanners.py` rewritten: new `REGION_TOWNS` dict (real UK council/
      authority names per region, reusing names already used elsewhere in
      this codebase) + `auth=<authority name>` queries, confirmed live
      against Birmingham and Walsall. Any `{"error": ...}` payload from
      either PlanIt or the paid API is now logged, never silently treated as
      zero. The free PlanIt fallback also no longer requires
      `UK_PLANNING_API_KEY` to be set — it used to return 0 for the entire
      region (both APIs) whenever the paid key was absent. New
      `_planit_real_value()` helper filters PlanIt's `"See source"`
      placeholder (and similar) out of applicant/agent fields so they're
      never stored as if real. 8 new tests in `TestPlanitFallback` /
      `TestPlanitRealValueFilter`.
    - **`council_source` region-label mismatches — FIXED for the paid-API
      path.** Root cause found while live-testing PlanIt: `ukplanningapi.co.uk`
      was seen returning addresses that don't actually match the requested
      postcode-prefix param (the "Sheffield"-tagged-but-actually-Kent
      pattern this doc flagged as OPEN). Rather than trust the paid API's
      own filtering, the returned address's outcode is now checked against
      the requested prefix before insert; a mismatch is skipped (logged),
      never relabeled or guessed. PlanIt's own `auth=<authority>` results
      don't have this problem (PlanIt scopes by authority server-side), so
      only the paid-API branch needed the check. 1 new test.
    - **`/api/check-postcode` fabricated numbers — FIXED.** This endpoint
      computed a real lead count from the database, then threw it away and
      replaced it with a sine/cosine "spatial variance" formula seeded from
      raw lat/lng — a visitor with zero real leads nearby was shown a
      fabricated 12-40 "active leads" figure, plus an invented £ contract-
      value estimate and a "competitors detected" warning (`max(3,
      selected_leads/12 + lat%6)`, not backed by any real competitor data).
      The frontend JS literally commented its own loading-spinner delay as
      "Subconscious Trigger: Fake calculating sequence to build tension/
      perceived value." Fixed: `selected_leads` is now always the real DB
      count; `connected_leads` is a real second query against the wider
      postcode area instead of a formula; the fabricated "competitors"
      field and both "Local Competitors Detected" frontend banners were
      removed entirely (no real data exists to back that claim); the £
      value estimate is now a disclosed flat per-notice range (£450-£1,450)
      applied to the real count, not a hidden multiplier. Homepage
      `display_leads = stats["l"] + 1427` padding (whenever the real count
      was under 1,000) also removed — the real count is shown, always.
    - **Purchased/dispatched leads showed no applicant name and no agent
      status, despite both now being captured — FIXED.** `database.
      burn_lead_inventory()` (runs on Stripe purchase) and `database.
      get_contractor_dashboard_data()` (the `/dashboard` page) didn't select
      `applicant_name`/`agent_name`/`agent_company`/`has_agent` at all, so
      even leads where the scraper had found this data, the buyer never saw
      it. Both queries updated. `notifications.send_purchased_lead_email()`
      (the email sent right after Stripe payment), `notifications.
      dispatch_lead_alerts()` (the routed-lead table + individual-lead
      emails sent to subscribers), and the `/dashboard` lead cards in
      `main.py` all now show the applicant name (when the council recorded
      one) and an honest agent-status badge: "⚠️ Agent on record" (has_agent
      = True), "✅ No agent listed" (has_agent = False), or "Unconfirmed"
      (has_agent = None — not checked, or checked but inconclusive; never
      shown as "no agent"). Subscriber emails no longer blanket-label every
      lead "Exclusive" without qualification.
    - **False "SCRAPER PAGE STRUCTURE" alerts — FIXED.** Recurring alerts on
      Cornwall/Nottingham/Glasgow/Bristol/Guildford/Dartford/Maidstone/
      Tunbridge Wells/Winchester in production logs were traced to Idox's
      "too many results, please narrow your search" response — a real,
      valid page distinct from both "no results" and an actual structural
      break — not being recognized by the existing no-results heuristic.
      Now detected and logged distinctly (worth revisiting later: unlike a
      genuine zero, this means real matching applications likely exist but
      weren't returned). 1 new test.
    - **STILL OPEN — Resend email domain not verified.** `treekey.uk` is not
      verified on Resend, so every email — system alerts AND the customer-
      facing purchase-confirmation email above — fails with HTTP 403. This
      is an external account fix on resend.com/domains that only Nick can
      do; nothing in this pass touches it because there's no code fix for
      it. This remains the single most urgent pre-launch blocker.

13. **"Leave no stone unturned" follow-up pass (Aug 30 2026, same day)** —
    Nick asked me to fix everything I could on my own before we discuss the
    rest together. Went back through main.py, payments.py, and the
    supporting pipelines (research.py, bulk_contractor_extractor.py) looking
    specifically for more of the same pattern as items 11-12: numbers or
    claims presented as fact with nothing real behind them.
    - **FIXED — two fabricated-authority trust badges on the homepage.**
      "BS5837 Survey Alignment" and "ArbAC Industry Standard" both borrow
      the name of a real UK arboricultural standard (BS5837 covers trees in
      relation to construction/demolition; ArbAC is a genuine Arboricultural
      Association accreditation, confirmed by web search — but it's for
      utility vegetation-management *contractors*, not planning-data
      platforms) to imply TreeKey holds a certification it doesn't have.
      TreeKey aggregates public planning data; it isn't a surveyor or an
      accredited contractor. Removed both, kept the one trust badge that's a
      plain checkable fact (Open Government Licence) and added a second
      equally plain one (sourced directly from council registers).
    - **FIXED — "Intercept Before Competitors" homepage copy** claimed "we
      detect competitor density in your area," which was the same fabricated
      sine/cosine `competitors` number removed in item 12. Reworded to
      describe what's actually true: council notices are public the moment
      they're filed, and TreeKey monitors the registers directly.
    - **FIXED — three undeliverable promises in `payments.py`'s pricing
      copy:** (1) all three single-lead tiers promised "homeowner name" as a
      guaranteed instant unlock — it's only present when the council itself
      published one, so this now says "when the council has published one,"
      matching what `send_purchased_lead_email` actually tells the buyer
      per-lead. (2) The large single-lead tier promised "developer contact"
      outright — never deliverable, no phone/email exists in this data
      source, ever (same hard privacy-law limit documented in item 11) —
      removed entirely. (3) `arb_consultant`'s description promised "direct
      developer company contacts" — no code path in this project actually
      looks up or delivers a developer's contact details (Companies House
      enrichment in `bulk_contractor_extractor.py` is a separate pipeline
      for finding tree-surgery companies to sell subscriptions *to*, not for
      enriching a lead's developer applicant) — removed. Also reworded
      `commercial_pro`'s "The average commercial site clearance pays
      £2,500+" (stated as a verified average with no source) to match the
      hypothetical "if you land one job at £X" framing already used by
      every other tier's `real_world_roi`, rather than asserting an
      unsourced statistic.
    - **Checked and found clean:** `research.py` and
      `bulk_contractor_extractor.py` (the Companies House / director /
      phone-number contractor-discovery pipeline) — no fabricated numbers,
      fake ratings, or invented statistics found; `random.shuffle()` calls
      there are query-ordering only, not data generation.
    - **NOT done — extending Applicant/Agent capture to the Leeds/London
      bespoke scan functions.** These use ArcGIS (Leeds) and GLA Datahub
      (London) JSON APIs, not Idox HTML pages, so `_fetch_applicant_and_agent`
      doesn't apply as-is — it would need each API's actual field names
      confirmed live first (the same "verify, don't assume" standard used
      for the PlanIt fix), and I couldn't get a live browser session
      connected to check ArcGIS's real attribute schema in this pass. Left
      as a known, documented gap rather than guessing field names.
    - **DELIBERATELY NOT done — pricing/discount for `has_agent=True`
      leads.** These leads are now clearly flagged to the buyer (dashboard
      badge + dispatch email) *before* they pay, which is the part that
      was actually misleading. Whether to also discount the price or pull
      these leads from sale is a revenue decision, not a bug — Nick asked
      this exact pricing question earlier today and I'm answering it with
      him directly rather than guessing at a discount percentage.

    a native iOS/Android app (Apple Developer $99/yr + Google $25 one-time, app
    store review process outside our control, likely weeks of work, ongoing OS
    maintenance) against a PWA: add a web manifest + service worker to the
    existing site so it can be "installed" to a phone home screen with real push
    notifications, no app store involved. Verdict: the PWA route is a genuinely
    good idea — cheap, reuses the existing backend entirely, and would make the
    "you'll know before your competitors do" pitch literally true instead of just
    marketing copy (push notification vs. checking email). NOT urgent though —
    DNS/email deliverability (item 2 below) and the cold email copy (item 3) are
    what's actually gating the first paying contractor and matter more right now.
    Good candidate for right after launch, or folded into a later polish pass.

14. **Multi-vertical expansion build + a real production incident (Sep 1-2 2026)** —
    a full work segment happened between this entry and item 13 that this file's
    changelog never captured; full detail lives in `AI_HANDOFF.md` (rewritten Sep 2
    2026) and `master_expansion_plan_v2.md`, not repeated here in full. Headline
    points only:
    - **Multi-vertical architecture actually built**: a generalized `VERTICALS`
      config, tiered classifier (keyword → structured-field → Gemini LLM → manual
      review queue), GDPR-safe-by-construction lead format for HMO (no identity
      capture, enforced structurally in `_insert_lead`), the HMO vertical wired
      into all 4 live scan sources plus a dedicated HMO mesh scraper (13 councils
      enabled via a real government-data council list, ~22 more found but not yet
      live-verified), and `bulk_contractor_extractor.py` generalized for HMO too.
      188/188 tests passing.
    - **A real revenue-impacting production incident happened and was resolved**:
      a schema migration failure on Sep 1 took lead-capture AND the public
      marketplace to zero for ~18 minutes. Root-caused, fixed, and self-healing
      fallbacks added so the same failure mode can't recur silently. Full timeline
      in `AI_HANDOFF.md` §4.
    - **Real risk items still open, not resolved**: Gemini's council/portal
      research came back with a confirmed ~17% error rate and needs
      live-verification before trusting the rest of it; the LIA's core GDPR
      argument rests on an ICO citation I couldn't verify against the live ICO
      site; Privacy Policy/Terms still need a solicitor pass; `GLA_API_KEY` needs
      renewal. Full list in `AI_HANDOFF.md` §7.
    - Tier 3 (Gemini classification) is code-complete and on disk but not yet live
      — needs a Render redeploy plus a `GEMINI_API_KEY` env var to activate, and is
      fully inert (never errors, never blocks anything) until that's set.
    See `AI_HANDOFF.md` for the full writeup, including what's been learned about
    how Nick and this AI work together — worth reading at the start of any future
    session, not just this one.

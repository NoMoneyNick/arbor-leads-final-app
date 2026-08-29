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
10. **PWA / "installable website" (Aug 29 2026):**
    - **PART 1 (DONE):** Added `manifest.json`, Service Worker caching, and iOS/Android meta tags. The app is now installable to the home screen.
    - **PART 2 (NOT STARTED):** Actual Web Push API push notifications. Good candidate for post-launch polish.
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
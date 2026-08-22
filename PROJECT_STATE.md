# PROJECT STATE: VECTOR DATA LABS (V4.0-SECURE)

**Status:** Secure, Deployed, Pre-Revenue

**Last Updated:** 20 Aug 2026

**Live URL:** https://arbor-leads-final-app.onrender.com

---

## 1. FILE STRUCTURE

- `main.py` — Routes, dashboard, auth, Stripe, city scanning, director export.
- `database.py` — Supabase schema (potential_partners, leads, payments tables).
- `scanners.py` — Leeds + London verified. Birmingham/Manchester/Bristol/Sheffield scaffolded.
- `research.py` — Companies House search + Officers API + Google Places rating.
- `notifications.py` — Resend email alerts with grade/price. WhatsApp link generator.
- `payments.py` — Stripe checkout + webhook handler. Three pricing tiers defined.
- `PROJECT_STATE.md` — This file.
- `MANIFEST.md` — Operational rules, business model, legal framework.

---

## 2. PILLAR PROGRESS

- **Pillar 1 (WhatsApp):** ✅ Manual links active with lead grade + price in message.
- **Pillar 2 (Director Enrichment):** ✅ Companies House Officers API live. Apollo removed entirely.
- **Pillar 3 (Google Rating):** ✅ Google Places API active. Key confirmed live in Render.
- **Pillar 4 (Telegram / Make.com):** ⏳ TO DO — Webhook endpoint not built. Decision: Telegram bot (free, no personal number needed). Skip Twilio/WhatsApp for internal alerts.

---

## 3. SECURITY STATUS

- ✅ HTTP Basic Auth on all dashboard routes (DASHBOARD_USER / DASHBOARD_PASS in Render)
- ✅ TRIGGER_SECRET removed from HTML — never visible in page source
- ✅ Cron routes separated from dashboard routes
- ✅ Constant-time password comparison (secrets.compare_digest)
- ✅ API docs (/docs, /redoc) disabled

---

## 4. CITY COVERAGE

| City | Scanner | Status |
|---|---|---|
| Leeds | ArcGIS MapServer Layer 12 | ✅ Verified — returns real data |
| London | GLA Datahub API | ✅ Verified — returns real data |
| Birmingham | MHCLG Planning Data API | ⚠️ Scaffolded — API endpoint returned 404, needs correct source |
| Manchester | MHCLG Planning Data API | ⚠️ Scaffolded — unverified, needs correct source |
| Bristol | MHCLG Planning Data API | ⚠️ Scaffolded — unverified, needs correct source |
| Sheffield | MHCLG Planning Data API | ⚠️ Scaffolded — unverified, needs correct source |

**Note on additional cities:** Birmingham has an `Internet_Planning` ArcGIS MapServer at maps.birmingham.gov.uk but it returns 500 errors (likely requires auth token). Manchester has an ArcGIS Open Data Catalogue. Both need individual investigation to get to Leeds/London level of reliability. Consider planapi.co.uk as a paid aggregator to cover all UK councils with one API key.

---

## 5. DATABASE STATE (as of 20 Aug 2026)

- **Potential Partners:** 134 (Leeds + London LTDs, Companies House enriched)
- **Leads:** 137 (planning applications from Leeds + London)
- **Director names:** Mostly NULL — run `/enrich-all` after each Render deploy
- **Tables:** potential_partners, leads, payments
- **New columns added:** leads.lead_score (small/medium/large), leads.lead_price (25/50/75)

---

## 6. STRIPE / PAYMENTS STATUS

- ✅ `payments.py` built with three tiers: credits_10 (£80), city_monthly (£49), national_monthly (£89)
- ✅ Stripe checkout session creation working
- ✅ Webhook handler built at `POST /webhook`
- ✅ Public pricing page live at `/pricing`
- ⏳ **TO DO:** Add Starter tier (£19/month) to payments.py and pricing page
- ⏳ **TO DO:** User must update Stripe webhook URL to: `https://arbor-leads-final-app.onrender.com/webhook`
- ⏳ **TO DO:** Add STRIPE_SECRET_KEY, STRIPE_WEBHOOK_SECRET, PUBLIC_APP_URL to Render env vars

---

## 7. MARKET & STRATEGY (CONFIRMED 20 AUG 2026)

### What Works
- **Commercial planning applications** — large sites, development near trees, council/estate management. Job NOT already taken when it appears on the portal. This is the core product.
- **Arboricultural consultant leads** — development applications requiring BS5837 surveys. High-value, specialist market.
- **Education sell to small operators** — majority of small tree surgeons have never heard of planning data tools. BuildAlert/Planning Pipe don't market to them. Huge untapped audience.

### What Doesn't Work
- **Domestic TPO homeowner applications** — job is often already won before it appears on the portal (arborist writes the application for the client). Weak lead type.

### Competitive Position
- BuildAlert, Planning Pipe, PlanAPI = competitors, but they target builders not tree surgeons
- Your differentiator: exclusive leads + Companies House director enrichment + tree-surgery-specific product
- No direct competitor currently serves this niche the way this product does

---

## 8. ACTIVE OBJECTIVES & STATUS

### Completed:
- ✅ **Starter tier (£19/month)** added to `payments.py` and comparison pricing page.
- ✅ **Security & Basic Auth** on all internal dashboard routes; secret removed from HTML.
- ✅ **15-mile radius query** on Leeds ArcGIS scanner; documented London boundary scope.
- ✅ **UK Planning API scanner** deployed for Birmingham, Manchester, Bristol, Sheffield with `UK_PLANNING_API_KEY`.
- ✅ **Automated scheduling** configured on cron-job.org with staggered triggers + keep-alive ping.
- ✅ **Strict name/keyword filtering**: compound keywords for planning leads; 2-layer name filtering on Companies House partners (no breast surgeons, fruit tree specialists, medical/cosmetic clinics).
- ✅ **`/clean-partners` route**: retroactive purge of non-tree-surgery companies from database.
- ✅ **Enriched Contact Data (Pillar 2 & 3)**: Google Places Details retrieves direct phone number, website, and rating; auto-scrapes public contact email from company website.
- ✅ **CSV Export**: `/export-directors.csv` for 1-click spreadsheet download with company, director, phone, email, website, city.
- ✅ **Outreach Playbook created**: direct mail letter, short email sequence, and WhatsApp/phone pitch scripts.

### Next in Queue:
1. **Build Public Landing Page (`/`) & Admin Separation (`/admin`)**:
   - High-converting, professional landing page branded as **ArborLeads** (powered by Vector Data Labs parent umbrella).
   - Value proposition: *"Exclusive UK Council Planning Leads for Tree Surgeons & Arbs"*.
   - Live sample leads ticker, 4-tier pricing table, trust signals, and direct call to action.
   - Move admin management controls to `/admin` protected by Basic Auth.
2. **Build Customer Interactive Territory Map Selector**:
   - **Frontend:** Leaflet.js + OpenStreetMap (100% free, mobile-friendly).
   - **Postcode Lookup:** Free `postcodes.io` API (converts UK postcodes to latitude/longitude coordinates).
   - **Interactive Radius Slider:** 5 / 10 / 15 / 20 / 25 miles with live visual circle overlay expanding/shrinking on the map.
   - **Database:** `customers` table (postcode, lat, lon, radius_miles, email, stripe_customer_id, plan).
   - **Lead Router:** Automatically computes Haversine distance for new planning applications and routes alerts only to subscribers whose territory circle covers that job.
3. **Launch Outreach Campaign**: use the CSV export (`/export-directors.csv`) and [outreach_playbook.md](file:///C:/Users/twobo.DESKTOP-DI088K1/.gemini/antigravity/brain/46137285-d767-4297-93ad-b75b5cbb2fa0/outreach_playbook.md) for direct mail, cold email, and WhatsApp outreach.
4. **Build Pillar 4 (Telegram Bot)**: Make.com webhook → Telegram bot for internal lead alerts.
5. **Direct Council GIS Contact**: email Birmingham/Manchester GIS teams for official free feeds as volume grows.
6. **First Paying Subscriber & Customer Feedback Review**.


---

## 9. RENDER ENVIRONMENT VARIABLES

| Key | Status | Description |
|---|---|---|
| SUPABASE_DB_URL | ✅ Set | PostgreSQL database |
| TRIGGER_SECRET | ✅ Set | Cron security gate |
| DASHBOARD_USER | ✅ Set | Dashboard basic auth username |
| DASHBOARD_PASS | ✅ Set | Dashboard basic auth password |
| COMPANIES_HOUSE_KEY | ✅ Set | Companies House Search + Officers API |
| GOOGLE_MAPS_KEY | ✅ Set | Google Places search + place details (rating, phone, website) |
| GLA_API_KEY | ✅ Set | London GLA Planning Datahub API |
| UK_PLANNING_API_KEY | ✅ Set | UK Planning API for Birmingham, Manchester, Bristol, Sheffield |
| RESEND_API_KEY | ✅ Set | Transactional email alerts |
| PUBLIC_APP_URL | ✅ Set | `https://arbor-leads-final-app.onrender.com` |
| STRIPE_SECRET_KEY | ✅ Set | Stripe payments live & verified |
| STRIPE_WEBHOOK_SECRET | ✅ Set | Stripe webhook listener |



---

## 10. TECHNICAL NOTES (FOR NEXT SESSION — READ THIS)

### Deploy Convention
- User types `execute` → run: `git add .` then `git commit -m "..."` then `git push`
- No confirmation needed. Just run it.
- Working directory: `c:\Users\twobo.DESKTOP-DI088K1\OneDrive\Documents\VECTOR DATA LABS`
- PowerShell — use `;` not `&&` to chain commands

### Verified API Endpoints (DO NOT CHANGE — THEY WORK)
- **Leeds:** `https://mapservices.leeds.gov.uk/arcgis/rest/services/Public/Planning/MapServer/12/query` — ArcGIS, no auth, `verify=False` needed for SSL
- **London:** `https://planningdata.london.gov.uk/api/applications` — requires `GLA_API_KEY` header as `Authorization`

### Broken API Endpoints (DO NOT USE)
- `https://www.planning.data.gov.uk/api/v1/entity.json?dataset=planning-application` — returns 404
- `https://gis.birmingham.gov.uk/arcgis/...` — host does not exist
- `https://maps.birmingham.gov.uk/arcgis/rest/services/Internet_Planning/MapServer` — returns HTTP 500, likely needs auth token or is internal-only

### Birmingham Investigation Status
- Birmingham HAS an ArcGIS Internet_Planning MapServer at maps.birmingham.gov.uk — confirmed
- Returns HTTP 500 — likely behind firewall or needs token
- Alternative: planapi.co.uk is a paid UK-wide planning aggregator (investigate pricing next session)
- Same investigation needed for Manchester, Bristol, Sheffield

### Stripe Setup Notes
- Webhook endpoint in main.py is `POST /webhook` (NOT /webhook/stripe)
- Old webhook was pointing to `arbor-leads-agent-1.onrender.com` (old/wrong app)
- Correct webhook URL: `https://arbor-leads-final-app.onrender.com/webhook`
- User must update URL in Stripe Dashboard → Developers → Webhooks → empowering-inspiration
- **Starter tier (£19/month) NOT YET ADDED** — first objective next session
- Add to PLANS dict in `payments.py` and update `/pricing` HTML in `main.py`

### Companies House API
- Auth: `Authorization: Basic base64(API_KEY:)` — note the trailing colon
- Officers endpoint: `GET https://api.company-information.service.gov.uk/company/{company_number}/officers`
- Names returned as `SURNAME, Firstname` — research.py flips to `Firstname SURNAME`
- Apollo has been completely removed — do not reference it

### Database Schema
- `potential_partners`: id, company_name, company_number (UNIQUE), status, address, distance_miles, target_city, sic_codes, md_name, phone_number, google_rating, created_at
- `leads`: id, reference (UNIQUE), address, summary, score, council_source, lead_score, lead_price, status, discovered_at
- `payments`: id, stripe_session_id (UNIQUE), plan, amount_pence, customer_email, status, created_at

### Auth System
- Dashboard: HTTP Basic Auth via DASHBOARD_USER + DASHBOARD_PASS in Render
- Cron routes: TRIGGER_SECRET query param (for Make.com, cron-job.org, external callers)
- Both use `secrets.compare_digest()` — timing-attack safe
- If DASHBOARD_PASS not set in Render → all routes return 503 (intentional)

### Lead Scoring Keywords (scanners.py)
- **Large £75:** tpo, tree preservation order, conservation area, woodland, development, several trees, multiple trees, commercial, site clearance, dangerous tree, estate, demolition
- **Medium £50:** crown reduction, crown lift, fell, felling, removal, pollarding, overhanging, storm damage, deadwood, works to trees, urgent, diseased
- **Small £25:** pruning, hedge, trim, cutting, maintenance, inspection, minor works, lopping
- Default if no match: small / £25

### Key Business Decisions (Made 20 Aug 2026)
- TPO domestic homeowner leads are weak — job often already won before portal publishes it
- Primary target: commercial arboricultural LTDs + arboricultural consultants (BS5837 surveys for developers)
- Secondary target: small operators who have NEVER heard planning data tools exist — education sell
- Exclusive leads (never shared) = primary differentiator vs BuildAlert / Planning Pipe
- Director names from Companies House = secondary differentiator — no competitor does this
- Do NOT chase homeowner permit requests — chase commercial contracts and development applications

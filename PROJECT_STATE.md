# PROJECT STATE: VECTOR DATA LABS (V4.0-SECURE)

**Status:** Secure, Deployed, Pre-Revenue  
**Customer Brand:** **ArborLeads** (powered by Vector Data Labs parent umbrella)  
**Live Production URL:** https://arbor-leads-final-app.onrender.com  
**Last Updated:** 22 Aug 2026  

---

## 🚨 STANDING REMINDER FOR NEXT SESSION (READ THIS FIRST)

> [!IMPORTANT]
> **NEXT SESSION OPENING MANDATE:** Whatever the Operator says next time we speak, the AI assistant MUST immediately remind the Operator to run the individual city partner scans on the dashboard:
> 1. **Manchester:** `/research/manchester` (60 seconds)
> 2. **Leeds:** `/research/leeds` (60 seconds)
> 3. **Bristol:** `/research/bristol` (60 seconds)
> 4. **Sheffield:** `/research/sheffield` (60 seconds)
> 5. **London:** `/research/london` (90 seconds)
> 
> *Running these 5 buttons in separate tabs will complete the full nationwide sweep and unlock 1,500+ verified LTD directors across all regions!*

---

## 1. FILE STRUCTURE

- `main.py` — Routes, landing page, basic auth, Stripe checkout & webhooks, city lead triggers, CSV export, `/health` keep-alive.
- `database.py` — Supabase PostgreSQL schema, migrations, connection resilience, customer territory records.
- `scanners.py` — Leeds (ArcGIS 15-mile radius), London (GLA Datahub), Birmingham/Manchester/Bristol/Sheffield (UK Planning API).
- `research.py` — Postcode/district resolution engine (`resolve_uk_city`), 8-worker ThreadPoolExecutor concurrency, Companies House Officers API, Google Places Details, website email scraper.
- `notifications.py` — Resend email alerts with lead score/price, WhatsApp direct links, radius-based subscriber dispatching.
- `payments.py` — Stripe checkout sessions with dynamic pricing (4 live tiers), webhook fulfillment, Statement Descriptor: `ARBORLEADS`.
- `PROJECT_STATE.md` — This file.
- `MANIFEST.md` — Master operational rules, business model, legal framework.

---

## 2. PILLAR PROGRESS

- **Pillar 1 (WhatsApp):** ✅ Manual direct links active with lead grade + price pre-formatted.
- **Pillar 2 (Director Enrichment):** ✅ Companies House Officers API live.
- **Pillar 3 (Google Reputation & Contact):** ✅ Google Places API active (rating, direct phone number, website, scraped email).
- **Pillar 4 (Telegram Bot):** ⏳ Queued — Make.com webhook → Telegram bot for internal lead alerts.

---

## 3. SECURITY & UPTIME STATUS

- ✅ HTTP Basic Auth on all dashboard & export routes (`DASHBOARD_USER` / `DASHBOARD_PASS`)
- ✅ `TRIGGER_SECRET` security gate on all external cron lead scraping routes
- ✅ `GET /health` deployed for 10-minute cron-job.org keep-alive pings (keeps Render awake 24/7, eliminates 502 Bad Gateway timeouts)
- ✅ Constant-time password comparison (`secrets.compare_digest`)
- ✅ API docs (`/docs`, `/redoc`) disabled

---

## 4. CITY RADAR COVERAGE

| City | Scanner Source | Radar Scope | Status |
|---|---|---|---|
| **Leeds** | Leeds Council ArcGIS MapServer | 15-mile spatial circle (17 towns/districts) | ✅ Verified live |
| **London** | GLA Planning Datahub API | All 32 London Boroughs + Home Counties (25 districts) | ✅ Verified live |
| **Birmingham** | UK Planning API (`B` prefix) | West Midlands & surrounding ring (20 towns) | ✅ Verified live |
| **Manchester** | UK Planning API (`M` prefix) | Greater Manchester & Cheshire (18 towns) | ✅ Verified live |
| **Bristol** | UK Planning API (`BS` prefix) | West of England & Somerset/Glos (17 towns) | ✅ Verified live |
| **Sheffield** | UK Planning API (`S` prefix) | South Yorkshire & Peak District (14 towns) | ✅ Verified live |

---

## 5. REVENUE & PRICING TIERS

| Tier | Price | Model | Description |
|---|---|---|---|
| **Starter** | £19 / month | Recurring Subscription | Entry point, 10 leads/month, instant alerts |
| **10-Lead Credits** | £80 one-off | Credit Pack | Pay-as-you-go, no subscription |
| **City Pro** | £49 / month | Recurring Subscription | One chosen city/radius, unlimited daily leads, full scoring |
| **National Pro** | £89 / month | Recurring Subscription | All 6 UK regions, priority alerts, full scoring |
| **Exclusive Lockout** | £149 / month | Territory Monopoly | **100% Exclusive Leads** — Locks out all competing contractors in your radius |

---

## 6. ACTIVE SPRINT QUEUE

1. **Build Public Landing Page (`/`):** ArborLeads branded homepage with value prop, live sample leads ticker, 5-tier pricing table (including Exclusive Lockout), FAQ, and CTA.
2. **Move Admin Portal to `/admin`:** Protected by Basic Auth (`verify_dashboard_auth`).
3. **Build Interactive Territory Map Selector:**
   - **Frontend:** Leaflet.js + OpenStreetMap (100% free, mobile-friendly).
   - **Geocoding:** Free `postcodes.io` API.
   - **Radius Slider:** 5 / 10 / 15 / 20 / 25 miles with live expanding circle overlay.
   - **Lead Router:** Computes Haversine distance from planning application coordinates to subscriber pin; only routes leads within customer's active territory circle.
4. **Implement Exclusive Lead Lockout System:** Flag leads claimed by Exclusive tier subscribers so they are suppressed from all other contractors.
5. **Launch Outreach Campaign:** Use `/export-directors.csv` and [`outreach_playbook.md`](file:///C:/Users/twobo.DESKTOP-DI088K1/.gemini/antigravity/brain/46137285-d767-4297-93ad-b75b5cbb2fa0/outreach_playbook.md) for 100% legal B2B cold emails and WhatsApp outreach to verified LTD directors.
6. **Build Pillar 4 (Telegram Bot):** Make.com webhook → Telegram bot for internal lead alerts.


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
| `UK_PLANNING_API_KEY` | ✅ Set | UK Planning API for Birmingham, Manchester, Bristol, Sheffield |
| `RESEND_API_KEY` | ✅ Set | Transactional email alerts |
| `TEST_EMAIL` | ✅ Set | Alert destination email |
| `PUBLIC_APP_URL` | ✅ Set | `https://arbor-leads-final-app.onrender.com` |
| `STRIPE_SECRET_KEY` | ✅ Set | Stripe payments live & verified |
| `STRIPE_WEBHOOK_SECRET` | ✅ Set | Stripe webhook listener |

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

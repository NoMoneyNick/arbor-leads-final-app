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
## 1. CORE ARCHITECTURE OVERVIEW

Vector Data Labs operates **ArborLeads** — an automated, nationwide B2B lead intelligence and contractor radar platform tailored for the UK arboricultural sector.

- **Public Landing Page:** `GET /` — High-converting customer-facing SaaS homepage with live lead sample ticker and 5-tier pricing.
- **Admin Command Portal:** `GET /admin` — Protected behind HTTP Basic Auth (`verify_dashboard_auth`).
- **Database Status:** **935 Verified Limited Company Tree Surgeons** + **Hundreds of Fresh Council Planning Leads**.
- **Nationwide Council Coverage:** 100% of England's **309 Local Planning Authorities** across 9 economic regions.
- **Lead Freshness Lifecycle Badges:** 🟢 `🔥 FRESH (0–14d)`, 🟡 `⏳ IN CONSULTATION (15–45d)`, 🔵 `✅ GRANTED (45–90d)`, ⚪ `📦 ARCHIVED (90d+)`.
- **Pricing Matrix:**
  1. **Single Lead:** £19 (One-time)
  2. **5-Lead Pack:** £80 (Credits)
  3. **City Pro:** £49/month (Unlimited regional leads in 15-mile radius)
  4. **National Pass:** £89/month (Unlimited leads across all 309 English councils)
  5. **Exclusive Lockout:** £149/month (100% Territory Monopoly / Competitor lockout)

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

## 4. CITY & SURROUNDING COUNCIL RADAR COVERAGE

| Regional Zone | Lead Scanners & Postcode Radars | Partner Green Belt Sweep Scope | Status |
|---|---|---|---|
| **London & South East** | GLA Datahub (32 Boroughs) + UK Planning API (`SW, SE, NW, N, E, EC, WC, CR, BR, EN, HA, UB, KT, TW, DA, RM, IG, SM, RH, TN, GU, CM, SS, SL, HP, AL, SG, WD, ME`) | 75+ Towns (Surrey, Kent, Essex, Herts, Berks, Bucks) | ✅ Exhaustive nationwide radar |
| **Leeds & Yorkshire** | Leeds ArcGIS MapServer + UK Planning API (`LS, BD, WF, HX, HD, YO, HG, HU, DL, TS`) | 24 Districts (West, North, East, South Yorkshire) | ✅ Exhaustive nationwide radar |
| **Birmingham & West Midlands** | UK Planning API (`B, WS, WV, DY, CV, WR, TF, ST, HR, SY`) | 28 Towns (West Midlands, Staffs, Worcs, Shrops, Warks) | ✅ Exhaustive nationwide radar |
| **Manchester & North West** | UK Planning API (`M, SK, WA, WN, BL, OL, CW, L, PR, BB, FY, CH`) | 22 Towns (Greater Manchester, Cheshire, Merseyside, Lancs) | ✅ Exhaustive nationwide radar |
| **Bristol & West Country** | UK Planning API (`BS, BA, GL, SN, TA, DT, SP`) | 22 Towns (Bristol, Bath, Glos, Wilts, Somerset, Dorset) | ✅ Exhaustive nationwide radar |
| **Sheffield & South Yorkshire** | UK Planning API (`S, DN, DE, NG, LN, LE`) | 15 Towns (South Yorks, Derbyshire, Peak District, Notts, Lincs) | ✅ Exhaustive nationwide radar |

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

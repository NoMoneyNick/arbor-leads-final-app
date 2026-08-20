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

## 8. ACTIVE OBJECTIVES (PRIORITY ORDER)

1. **Add Starter tier (£19/month)** to `payments.py` and `/pricing` page
2. **Fix Birmingham scanner** — find correct ArcGIS or council API endpoint
3. **Fix Manchester scanner** — same process as Birmingham
4. **Fix Bristol + Sheffield scanners** — same process
5. **Set up automated scheduling** — use cron-job.org (free) to call `/trigger-leads-{city}?secret=X` daily
6. **Build Pillar 4** — Make.com webhook → Telegram bot for internal lead alerts
7. **Outreach campaign** — use `/export-directors` to email the 134 directors with the "did you know" pitch
8. **First sale**

---

## 9. RENDER ENVIRONMENT VARIABLES REQUIRED

| Key | Status |
|---|---|
| SUPABASE_DB_URL | ✅ Set |
| TRIGGER_SECRET | ✅ Set |
| DASHBOARD_USER | ✅ Set |
| DASHBOARD_PASS | ✅ Set |
| COMPANIES_HOUSE_KEY | ✅ Set |
| GOOGLE_MAPS_KEY | ✅ Set |
| GLA_API_KEY | ✅ Set |
| RESEND_API_KEY | ✅ Set |
| TEST_EMAIL | ✅ Set |
| PUBLIC_APP_URL | ⏳ Add: https://arbor-leads-final-app.onrender.com |
| STRIPE_SECRET_KEY | ⏳ Add from Stripe dashboard |
| STRIPE_WEBHOOK_SECRET | ⏳ Add from Stripe dashboard (update endpoint URL first) |

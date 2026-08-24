# PROJECT STATE: VECTOR DATA LABS & TREE KEY (V4.2-SECURE)

**Status:** 100% Deployed, Fully Enriched, Great Britain Coverage, Pre-Revenue  
**Customer Brand:** **Tree Key** (powered by Vector Data Labs parent umbrella)  
**Live Production URL:** https://treekey.uk  
**Admin Portal:** https://treekey.uk/admin  
**Backup URL:** https://arbor-leads-final-app.onrender.com  
**Last Updated:** 24 Aug 2026  

---

## 🎯 CURRENT PLATFORM MILESTONES (100% OPERATIONAL)

1. **Partner Database:** **1,883+ Verified UK Limited Company Tree Surgeons** (100% audited & enriched with direct Managing Director names and verified UK telephone numbers).
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
- [ ] **Hero Section & Value Proposition:** Review headline, subheadline, and trade authority copy for punchiness.
- [ ] **Trade Credibility & Badges:** Add badges for BS5837 compliance, Open Government Licence (OGL v3.0), and ArbAC alignment.
- [ ] **Pricing Table & Lockout Text:** Fine-tune exclusive 15-mile radial territory lockout copy (£149/mo) and credit pack terms (£80).
- [ ] **FAQ Section Review:** Ensure contractor questions regarding lead exclusivity, notice speed, and cancellation are answered clearly.


---

### 4. 📨 Multi-Channel Outreach & Revenue Launch

- [ ] **Cold Email Sequence Copywriting (Operator using Claude):**
  - **Email 1 (The Free Lead Gift):** 1 free local planning notice in their postal district.
  - **Email 2 (The Competitive Moat):** Exclusive 15-mile radial territory lockout (£149/mo).
  - **Email 3 (The Soft Close):** £49/mo Regional Plan or £80 10-Lead Credit Pack.
  - **Personalization Tokens:** `{{director_name}}`, `{{company_name}}`, `{{city}}`, `{{recent_tpo_street}}`.
- [ ] **Outreach Tool Warmup:** Load secondary domain mailboxes into **Instantly.ai** or **Smartlead.ai** for 14-day automated warmup.
- [ ] **Direct WhatsApp / SMS Campaign:** Draft conversational message templates targeting the **980 Managing Director mobile numbers** on file.

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
| `PUBLIC_APP_URL` | 🔄 Update to | `https://treekey.uk` |
| `STRIPE_SECRET_KEY` | ✅ Set | Stripe payments live & verified |
| `STRIPE_WEBHOOK_SECRET` | ✅ Set | Stripe webhook listener |

 
## TOMORROW TO-DO LIST
1. Fix Map Lead Bug: North Wales shows 779 leads. Algorithm must strictly clip to radius and not use national baseline.
2. PayPal Verification loop.
3. Add stock photos.

4. **Database Expansion Strategy:** Brainstorm and build new scraping pipelines to find maximum potential tree surgeon customers and emails (beyond the current 1,883).
5. **Lead Scope Expansion (Domestic & Industrial):** Research data sources for domestic/residential and industrial tree surgery leads to diversify away from just council planning portals.

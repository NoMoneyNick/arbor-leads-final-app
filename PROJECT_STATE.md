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
| `PUBLIC_APP_URL` | ✅ Set | `https://treekey.uk` — used in sitemap, robots.txt, and email links |
| `STRIPE_SECRET_KEY` | ✅ Set | Stripe payments live & verified |
| `STRIPE_WEBHOOK_SECRET` | ✅ Set | Stripe webhook listener |

 
## REMAINING PRE-LAUNCH TO-DO LIST
1. **[BLOCKED] Overturn PayPal Ban:** Call PayPal UK Business Support (020 8080 6500) to demand a human review of the automated ID ban glitch. (Priority: Low - Stripe is functioning).
2. **DNS Email Deliverability:** Configure SPF, DKIM, and DMARC on Cloudflare/Namecheap so the 2,172 cold emails do not go to spam.
3. **Cold Email Copywriting:** Write the high-converting 3-step email sequence.
4. **ICO Registration:** Register Vector Data Labs with the ICO and pay the Â£40 fee.
5. **Database Expansion Strategy (Phase 2):** Brainstorm and build new scraping pipelines to find maximum potential tree surgeon customers and emails (beyond the current 2,172).
6. **Lead Scope Expansion (Phase 2):** Research data sources for domestic/residential and industrial tree surgery leads to diversify away from just council planning portals.
7. **Contractor Portal Upgrades (Phase 2):** Build a custom account settings dashboard where paying tree surgeons can toggle their preferred notification methods (WhatsApp vs. Email) and interactively draw/pick their custom lead alert areas on a map.
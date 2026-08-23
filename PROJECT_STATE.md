# PROJECT STATE: VECTOR DATA LABS (V4.0-SECURE)

**Status:** 100% Deployed, Fully Enriched, Pre-Revenue  
**Customer Brand:** **ArborLeads** (powered by Vector Data Labs parent umbrella)  
**Live Production URL:** https://arbor-leads-final-app.onrender.com  
**Last Updated:** 23 Aug 2026  

---

## 🎯 CURRENT PLATFORM MILESTONES (100% OPERATIONAL)

1. **Partner Database:** **1,011 Verified UK Limited Company Tree Surgeons** (100% audited & enriched, 980 with direct phone numbers/details).
2. **Planning Radar:** **687 Live Planning Leads** across all 309 English Local Planning Authorities.
3. **Public SaaS Portal (`/`):** Institutional enterprise design with zero spam emojis, authoritative B2B trade copy, and Open Government Licence compliance.
4. **Master Daily Pipeline (`/trigger-daily-pipeline`):** 4-Stage automated morning sweep (Planning Scan -> Lead Quality Filter -> New Contractor Discovery -> Two-Layer Name & UK Phone Sanitization).
5. **Cron Schedule:** Simplified to 2 jobs (10-min heartbeat at `/health`, 6:00 AM daily pipeline at `/trigger-daily-pipeline?secret=arsenal`).

---

## 📋 OUTREACH & EMAIL DISPATCH TO-DO QUEUE

1. **[ACTIVE TO-DO] Cold Email Sequence Copywriting (Operator using Claude):**
   - Hook: Free local planning lead in their postcode district.
   - Core Offer: £49/mo Regional Plan or £149/mo Exclusive 15-Mile Radial Territory Lockout.
   - Personalization Tokens: `{{director_name}}`, `{{company_name}}`, `{{city}}`, `{{recent_tpo_street}}`.
2. **[ACTIVE TO-DO] Outreach Infrastructure Selection & Setup:**
   - **Recommended Approach:** Dedicated Cold Outreach Tool (**Instantly.ai** or **Smartlead.ai**) on a secondary domain (`getarborleads.co.uk`) with automated inbox warm-up to protect primary domain reputation.
   - **High-Converting Complement:** Direct WhatsApp / SMS outreach to the 980 Managing Director mobile numbers on file.
   - **In-App Option:** Throttled SMTP/SES dispatcher built directly into FastAPI with rate-limiting and unsubscribe management.



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

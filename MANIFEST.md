# MASTER PROJECT MANIFEST: VECTOR DATA LABS (V4.0)

**Project Status:** Secure, Deployed, Pre-Revenue  
**Customer-Facing Brand:** **Tree Key** (powered by Vector Data Labs parent umbrella)  
**Live Production URL:** https://treekey.uk  
**Admin Portal:** https://treekey.uk/admin  
**Last Updated:** 24 Aug 2026  

---

## 1. OPERATIONAL RULES & PROTOCOLS

*   **The No-Title Rule:** The AI must not refer to itself or the Operator with titles. Both parties are untitled operators.
*   **Communication Style:** Direct, concise, and operational. No introductory fluff.
*   **The "Full Code" Rule:** Never provide incomplete code snippets. Always rewrite the full block or file.
*   **The Human Logic Protocol:** When processing loops occur, Operator logic takes priority over automated loops.
*   **Deploy Trigger:** When the Operator types `execute` or `go`, run `git add . ; git commit -m "..." ; git push` immediately.

---

## 2. SYSTEM ARCHITECTURE & FILE DEPARTMENTS

1.  `main.py` — Reception Desk & Gateway.
    - Public Homepage (`/`): High-converting SaaS landing page branded as **Tree Key**.
    - Admin Portal (`/admin`): Secure management dashboard protected by HTTP Basic Auth.
    - Public Health Ping (`/health`): 24/7 unauthenticated keep-alive endpoint for cron-job.org uptime pings.
    - Payments & Webhooks: Stripe checkout redirects and `POST /webhook` fulfillment.
    - Data Exports: `/export-directors.csv` and `/export-directors` HTML table.
    - City Scanner Gates: `/trigger-leads-{city_slug}?secret=...` for morning lead scraping.
2.  `database.py` — Filing Cabinet.
    - Supabase PostgreSQL connection with auto-reconnecting schema migrations.
    - Tables: `leads`, `potential_partners`, `payments`, `customers`, `territory_claims`.
    - Columns: `md_name`, `phone_number`, `google_rating`, `website`, `email`, `lead_score`, `lead_price`, `target_city`.
3.  `scanners.py` — Scout & Lead Hunter.
    - **Leeds:** ArcGIS 15-mile spatial boundary circle query.
    - **London:** GLA Planning Datahub API (all 32 London Boroughs aggregated).
    - **Birmingham, Manchester, Bristol, Sheffield:** UK Planning API via postcode prefixes (`B`, `M`, `BS`, `S`).
    - Lead Grading: Small (£25), Medium (£50), Large (£75) with strict compound tree keyword filters.
4.  `research.py` — Deep Partner Investigator.
    - **Postcode & Address Parsing Engine (`resolve_uk_city`):** Parses UK outward postcodes (`B`, `M`, `BS`, `S`, `LS`, London postal codes) to accurately assign true home cities.
    - **Nationwide Coverage:** 16 regional clusters covering England, Scotland, and Wales.
    - **Parallel Concurrency:** Multi-threaded `ThreadPoolExecutor` for high-yield harvests.
    - **Enrichment:** Companies House Officers API (directors) + Google Places (rating, phone, website).
    - **Strict 2-Layer Filter:** LTDs only; requires tree surgery trade words; excludes medical, dental, cosmetic, housing, beer, finance, tattoo, and rail trades.
5.  `notifications.py` — Digital Postman.
    - Resend transactional email alerts with lead score/price.
    - Pre-formatted WhatsApp direct lead claim links.
6.  `payments.py` — Cashier.
    - 4 Live Tiers: Starter (£19/mo), 10-Lead Credits (£80 one-off), City Pro (£49/mo), National (£89/mo), Territory Lockout (£149/mo).
    - Statement Descriptor: `TREE KEY`.
7.  `PROJECT_STATE.md` — Active development progress, environment variables, and sprint queues.
8.  `MANIFEST.md` — This master operational guide.

---

## 3. BRAND & UMBRELLA ARCHITECTURE

*   **Umbrella Holding:** **Vector Data Labs** functions as the parent holding entity taking payments and managing infrastructure.
*   **Customer Brand:** **Tree Key** — the focused, professional commercial lead platform for tree surgeons.
*   **Stripe Settings:** Statement Descriptor set to `TREE KEY` so customer credit card statements are crystal clear.


---

## 4. LEGAL & OUTREACH COMPLIANCE FRAMEWORK

### 1. Limited Companies (LTDs) — 1,500+ Directors
*   **Legal Basis:** **PECR Regulation 22 (B2B Corporate Subscriber Exemption)** + **UK GDPR Article 6(1)(f) Legitimate Interests**.
*   **Channel:** **100% Legal to Cold Email, Call, and WhatsApp**.
*   **Requirements:** Relevant B2B subject matter (commercial tree jobs), sender identification, and simple opt-out/unsubscribe line.
*   **Cost:** **£0.00** (zero postage, zero ad spend).

### 2. Sole Traders — 6,000+ UK Operators
*   **Legal Basis:** **UK GDPR Article 6(1)(f) (Direct Postal Mail)**.
*   **Channel:** **Direct Mail Letters, Postcards, or Yard Drop-offs** (Cannot cold email without prior opt-in).
*   **Low-Cost Strategy:**
    - **A6 Full-Colour Glossy Postcards:** ~18p–25p total (printed via Solopress/Instantprint).
    - **Sniper Strategy:** Send 5–10 targeted letters/week only when a £5,000+ commercial felling job lands within 2 miles of a tree surgeon's yard.
    - **Local Yard Drops:** Print 50 flyers at home (~4p each) and drop through local workshop letterboxes (£0 postage).

---

## 5. REVENUE FUNNEL & CONVERSION BENCHMARKS

### B2B Cold Email Funnel (2,000 LTD Directors)
*   **Deliverability / Open Rate:** 40% – 55% (~800 – 1,100 opens) using personalized subject lines (`[Company] + [City] commercial tree jobs`).
*   **Click-through / Interest:** 8% – 12% (~90 – 140 warm clicks).
*   **Paying Conversion (Conservative: 2.0%):** 40 paying subscribers $\rightarrow$ **£760 to £1,360 / month MRR**.
*   **Paying Conversion (Moderate: 3.5%):** 70 paying subscribers $\rightarrow$ **£2,450 / month MRR (£29,400 / year)**.
*   **Credit Pack Upsells:** 10-lead credit packs (£80 each) generate immediate upfront cash injections.

---

## 6. ACTIVE SPRINT OBJECTIVES

1.  **Business Email Infrastructure:**
    - Configure custom domain email.
    - Set up Primary Domain and Secondary Outbound Domain.
    - Authenticate DNS records (SPF, DKIM, DMARC).
    - Update `TEST_EMAIL` in Render environment variables.
2.  **Website, Branding, Logo & Copy Polish:**
    - Refine Tree Key logo icon, SVG typography, and favicon.
    - Polish value proposition copy, trade trust badges (BS5837, OGL v3.0, ArbAC alignment).
    - Review pricing terms and FAQ clarity.
3.  **Multi-Channel Outreach Launch (1,800+ Contacts):**
    - 3-touch cold email sequence copywriting with personalization tokens.
    - Direct WhatsApp / SMS dispatch to Managing Director mobile numbers.
    - Automated warm-up and campaign launch via Instantly.ai / Smartlead.ai.






### 6.4 The First-Mover Edge (Early Adopter Scarcity)
Position the platform as a **new, unique, and highly disruptive technology**. Emphasize that contractors who secure their radius *now* gain a literal, structural advantage over their competitors who do not yet know this software exists. This isn't just sales talk�it is a factual reality. If they lock down their postcode today, they intercept the high-paying jobs while their competitors are left entirely in the dark. Once a territory is gone, it's gone.

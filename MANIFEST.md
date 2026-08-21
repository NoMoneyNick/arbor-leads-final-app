# MASTER PROJECT MANIFEST: VECTOR DATA LABS (V4.0)

**Project Status:** Secure, Deployed, Pre-Revenue

**Environment:** Modular Workspace (Antigravity / GitHub / Render)

**Last Updated:** 21 Aug 2026

---

## 1. OPERATIONAL RULES (AI PROTOCOL)

*   **The No-Title Rule:** The AI must not refer to itself or the User with titles. Both parties are untitled operators.

*   **Communication Style:** Direct and operational. No metaphors unless requested. No introductory fluff.

*   **The "Full Code" Rule:** Never provide code snippets. Always rewrite the entire content of the specific file being edited.

*   **The Human Logic Protocol:** When the AI is stuck or looping, the User provides logical direction. The AI must prioritise this over its own processing.

*   **Instruction Style:** Technical tasks broken into numbered, click-by-click steps.

*   **Deploy Trigger:** When the User types `execute`, run `git add .` → `git commit` → `git push` immediately, no confirmation.

---

## 2. FILE STRUCTURE (THE DEPARTMENTS)

1.  `main.py` — Reception Desk. API routes, Basic Auth dashboard, Stripe checkout & webhook, CSV export (`/export-directors.csv`), database cleanup (`/clean-partners`), city scanning & research triggers.
2.  `database.py` — Filing Cabinet. Supabase connection, schema migrations with resilience columns (`phone_number`, `md_name`, `google_rating`, `website`, `email`, `lead_score`, `lead_price`).
3.  `scanners.py` — Scout. Leeds (ArcGIS with 15-mile radius query), London (GLA boundary), Birmingham, Manchester, Bristol, Sheffield (UK Planning API with postcode prefix matching). Compound keyword lead scoring (£25/£50/£75).
4.  `research.py` — Investigator. Companies House search with two-layer name filtering + Officers API (director names) + Google Places Details (phone, rating, website) + website email scraper + retroactive DB cleanup.
5.  `notifications.py` — Digital Postman. Resend email alerts with lead score/price and WhatsApp direct links.
6.  `payments.py` — Cashier. Stripe checkout sessions (dynamic price_data), webhook handler, 4 live pricing tiers (Starter £19, 10-Lead Credits £80, City Pro £49, National £89).
7.  `PROJECT_STATE.md` — Persistent memory of progress, active queue, and environment variables.
8.  `MANIFEST.md` — This file. Operational rules, structure, mission, legal.

---

## 3. CORE MISSION & LEGAL

*   **Mission:** Package UK council planning application data as exclusive, scored leads for tree surgery businesses. Automate director-level enrichment via Companies House and Google Places.

*   **The Golden Rule:** Strictly Limited Companies (LTD) only. No Sole Traders or Partnerships. Sole traders are not the target customer.

*   **Authorised Path:** Contact corporate bodies via "Legitimate Interest" using Companies House numbers as unique identifiers.

*   **Data Source:** All planning data is legally public information under UK law.

---

## 4. BUSINESS MODEL (CONFIRMED)

### Target Markets (in order of priority)

1.  **Commercial arboricultural companies (LTDs)** — Larger operations with office managers and multiple crews seeking commercial contracts (development sites, council work, estates). These are the top 10–15% of the tree surgery market. They have budgets and marketing sophistication.

2.  **Arboricultural consultants** — Write BS5837 tree surveys for property developers. Need to know about development applications near trees immediately. High-value leads (£500–£5,000 per survey).

3.  **Small "unaware" operators** — One-man bands who have never heard of planning data tools. Entry-level pricing gets them in the door. They self-upgrade after winning one job.

### NOT the Target
- Homeowner TPO domestic jobs (often already won before they appear on portal)
- General trades / builders (that's BuildAlert's market, not ours)

### Competitive Position
- **Direct competitors:** BuildAlert, Planning Pipe, PlanAPI — but they target builders/architects, not tree surgeons. Tree surgeons are an underserved vertical.
- **Key differentiator:** Exclusive leads (never shared) + Companies House director enrichment + direct phone/email extraction (no competitor does this) + education sell to unaware small operators.

### Live Pricing Plans
| Tier | Price | For |
|---|---|---|
| Starter | £19/month | Small operators, entry point, 10 leads/mo |
| 10-Lead Credits | £80 one-off | Pay-as-you-go credit pack |
| City Pro | £49/month | One city, unlimited daily leads, full scoring |
| National | £89/month | All 6 UK cities, instant priority alerts |


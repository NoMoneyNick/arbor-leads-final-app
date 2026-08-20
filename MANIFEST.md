# MASTER PROJECT MANIFEST: VECTOR DATA LABS (V4.0)

**Project Status:** Secure, Deployed, Pre-Revenue

**Environment:** Modular Workspace (Antigravity / GitHub / Render)

**Last Updated:** 20 Aug 2026

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

1.  `main.py` — Reception Desk. API routes, dashboard, auth, Stripe checkout, city scanning UI.
2.  `database.py` — Filing Cabinet. Supabase connection, schema init, resilience columns.
3.  `scanners.py` — Scout. Leeds (ArcGIS), London (GLA), + 4 city scaffolds. Lead scoring logic.
4.  `research.py` — Investigator. Companies House search + Officers API (director names) + Google rating.
5.  `notifications.py` — Digital Postman. Resend email alerts with lead score/price. WhatsApp links.
6.  `payments.py` — Cashier. Stripe checkout sessions, webhook handler, pricing plan definitions.
7.  `PROJECT_STATE.md` — Persistent memory of progress, objectives, and decisions.
8.  `MANIFEST.md` — This file. Operational rules, structure, mission, legal.

---

## 3. CORE MISSION & LEGAL

*   **Mission:** Package UK council planning application data as exclusive, scored leads for tree surgery businesses. Automate director-level enrichment via Companies House.

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
- **Key differentiator:** Exclusive leads (never shared) + Companies House director enrichment (no competitor does this) + education sell to unaware small operators.

### Pricing Tiers (to implement)
| Tier | Price | For |
|---|---|---|
| Starter | £19/month | Small operators, skeptics, entry point |
| Professional | £49/month | One city, unlimited leads, full scoring |
| National | £89/month | All cities, first access, full suite |
| Pay-per-lead | £8 small / £30 medium / £75 large | Flexible option, converts to subscription |

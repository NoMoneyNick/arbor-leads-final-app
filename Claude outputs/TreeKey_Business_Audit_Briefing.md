# TreeKey / Vector Data Labs — Business Briefing for External Audit

*Prepared for handover to another AI (e.g. ChatGPT) for independent review. Written by Claude, working directly on Nick's codebase and business, as of 17 Sep 2026. No secrets, API keys, database credentials, or customer data are included below — safe to paste/upload as-is.*

## 1. What the business is

TreeKey (domain: treekey.co.uk, migrating off treekey.uk) is a UK arboricultural lead-generation SaaS. It scans UK council planning application registers nationwide (England) for applications that involve tree work — fellings, TPO (Tree Preservation Order) applications, crown reduction, BS5837 surveys required for development near trees — and sells access to those leads to tree surgeons, arboricultural consultants, and commercial/forestry contractors.

The business is registered as a UK Individual (not yet incorporated as a Ltd company). It is pre-revenue in any meaningful sense: at least one real paid sale has gone through (~£19, confirmed via Stripe), but no cold outreach email has been sent yet, so there is no real acquisition funnel or conversion data. The founder (Nick) is not a coder — all code has been written by AI tools (ChatGPT, Gemini, Claude, Google AI Studio, Antigravity, Cursor) under his direction and product decisions.

## 2. How a lead is actually produced

1. An automated scanner pipeline polls UK council planning portals (a mix of paid APIs, PlanIt aggregator data, and bespoke scrapers for councils not covered by those) roughly daily.
2. Each result is classified by **vertical** (tree-relevant vs not — e.g. HMO/care-home planning applications are explicitly excluded after a real bug let them leak into the tree marketplace).
3. Each tree-vertical lead is classified by **category** (crown work, felling & removal, storm/emergency, stump grinding, hedge work, general/other) and by a **value tier** (Standard/Priority/Elite) based on keyword signals in the application text (e.g. "TPO", "conservation area", "commercial", "section 211").
4. Real current volume (last-30-days snapshot, admin-reported, cross-validated): **~2,712 tree-vertical leads/month** — Elite ~513/mo (19%), Priority ~230/mo (8%), Standard ~1,969/mo (73%). By category: Crown Work 1,409, Felling & Removal 852, General/Other 273, Storm & Emergency 103, Stump Grinding 45, Hedge Work 30.
5. Leads are sold **exclusively** — once purchased/claimed, a lead is never resold to another buyer. This is the core stated differentiator against shared-lead marketplaces (see section 5).

## 3. Current live pricing (as of 17 Sep 2026)

**Subscriptions** (source of truth: `payments.py PLANS` dict):
- Starter — £39/mo
- Growth — £79/mo
- TreeKey Consultant (arb_consultant) — £99/mo
- Commercial & Forestry — £159/mo
- TreeKey Elite — £249/mo

Note: subscription tiers do **not** currently enforce a monthly lead quota in code (no counter, no reset, no cutoff). Any "X leads included per month" language would currently be aspirational/marketing copy, not an enforced mechanic.

**One-off single-lead purchases** (pay-per-lead, no subscription): £19 / £29 / £39 / £49, assigned via the value-tier + size classifiers described above. A past bug (found and fixed) let the displayed marketplace price drift from the actual Stripe checkout price — this is now fixed so display and charge are always derived from the same source.

## 4. Unit economics — what's known vs assumed

**Known, real:** at least one confirmed live sale at £19. Real lead volume and category/tier breakdown (section 2). Nothing else about buyer behaviour is real data yet — no tracked win-rate, no repeat-purchase data, no cold-email response data.

**External ballpark data gathered via web research (informational, not TreeKey-specific, treat as soft):**
- UK job costs: small tree work £250–1,200+; BS5837 survey £295–1,500+; commercial/forestry clearance £1,200–2,500/day or £3,000–12,000+/acre.
- Tree-service profit margins commonly cited in industry content as 10–30%, smaller operators often targeting 20–30%.
- Exclusive-lead conversion rates informally cited (non-UK-tree-specific sources) around 15–22%+.

**Competitor lead pricing (shared leads, i.e. sold to multiple buyers simultaneously, unlike TreeKey):**
- Bark: ~£1.20+VAT/credit, 5–20 credits per lead → roughly £7–£40 per lead depending on job size. Bark states it "heavily invests in Google AdWords" to generate the underlying customer demand.
- Checkatrade: £60–£500+/month membership *plus* £5–£40 per lead on top.
- Rated People: £35+VAT/month (12-month contract) + ~£15+VAT per lead.
- Lead Pronto (tree-specific): "from £15" per lead, pay-as-you-go, appears to be shared rather than exclusive.
- Felling UK, a tree-specific lead-gen competitor, appears to have gone out of business (domain now expired/parked).

**An open, unresolved methodological problem flagged during pricing discussion:** job-cost data describes what the end customer pays the tree surgeon — it does not establish what a *lead* is worth, what the win probability is, or whether a given category of lead (e.g. a "BS5837 survey" lead) still has outstanding, unfulfilled work by the time it's sold. In particular: BS5837 surveys are often a supporting document *required to be submitted with* a planning application, meaning the survey may already be complete by the time the application appears in public planning data — this has not yet been verified either way, and materially affects whether "consultant/survey" is a sellable lead category at all.

## 5. Launch gate (Nick's own stated sequence — nothing ships to cold email until all four are true)

1. **Cold email fires correctly** — sequence written and live-tested once successfully, but currently blocked: under UK PECR, sole traders count as "individual subscribers" requiring the same marketing consent as a private person (unlike Ltd companies, which are exempt as corporate subscribers). No compliant channel has been chosen yet for reaching sole-trader tree surgeons at scale (options considered: postal mail, TPS-screened cold calling, co-marketing with insurers/suppliers, trade directories, paid ads, trade shows — none built). Separately, cold-send email still shares a domain/reputation with real customer email rather than running on a fully separated warmed subdomain.
2. **Free-lead redemption** — built, tested, working.
3. **Subscriptions/Stripe checkout** — a critical live-payment bug chain (webhook failures) was found and fixed as of 17 Sep; a real customer payment has been confirmed successful end-to-end.
4. **Pricing packages sorted** — in progress; this document exists partly to get outside input on this.

## 6. Other known open risks

- Domain migration (treekey.uk → treekey.co.uk) is in progress: DNS, hosting, Stripe, and DMARC have been updated on the new domain, but the redirect from the old domain is serving as a 302 instead of the intended 301 (cause not yet confirmed), and Google Search Console's change-of-address validation has not been re-run to completion.
- Terms of Service and Privacy Policy pages are live on the site but have not been reviewed by a solicitor.
- A handful of production credentials (Stripe secret key, webhook secret, DB URL, Maps API key, admin dashboard password) were flagged for rotation; status not confirmed.
- No admin analytics dashboard yet (traffic, opens, conversion tracking) — decisions so far have relied on manual admin-panel queries.

## 7. Technical shape of the system (for context, not for code-level review)

FastAPI (Python) backend, PostgreSQL database, Stripe for payments, Resend for transactional email, Instantly.ai (paid tier) for planned cold outreach, hosted on Render, DNS on Cloudflare. Single-developer-equivalent codebase (all AI-written), deployed via manual git push. No automated test/CI pipeline beyond a local pytest suite run ad hoc.

---

## Suggested audit questions to run separately (not all at once)

Paste this document once, then ask these one at a time for sharper answers than one combined "audit everything" prompt would give:

1. **Pricing/unit economics:** "Given section 3 and 4 above, is the current £19–49 one-off pricing and £39–249 subscription pricing defensible? What specific evidence would you need to see before trusting any of these numbers, and what's the minimum viable way to get that evidence before changing prices again?"
2. **Legal/compliance risk:** "Given the PECR sole-trader problem in section 5, and the unreviewed legal docs in section 6, what is the actual regulatory exposure here, and what's the cheapest way to close the biggest risk first?"
3. **Go-to-market sequencing:** "Given the launch gate in section 5, is this the right order to solve these four things in, or would you resequence it? What's the highest-leverage next single action?"
4. **Lead category validity:** "Is a lead category built around 'this application will need a BS5837 survey' actually sellable, given that the survey may already be complete by the time the application is public? How would you test this without building anything first?"
5. **Competitive positioning:** "Given the competitor pricing in section 4, is 'exclusive, no ad-spend-funded' actually a strong enough differentiator to justify TreeKey's prices, or is the market telling us something the founder is missing?"

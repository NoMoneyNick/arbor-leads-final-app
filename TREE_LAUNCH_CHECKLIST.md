# TreeKey — Tree-Only Launch Checklist

Compiled 2 Sep 2026 because Nick is launching the tree vertical on its own first. Pulled together from `AI_HANDOFF.md`, `PROJECT_STATE.md`, and `TASKS.md`-equivalent session notes — this is the first time everything tree-relevant has been in one place. HMO-only items are deliberately excluded (see bottom).

## 🔴 CRITICAL — VERIFY BEFORE LAUNCH

1. ~~**Resend email domain verification**~~ — **RESOLVED.** Nick confirmed `treekey.uk` was verified on Resend as of ~3 days before 2 Sep 2026. Transactional email (purchase confirmations, lead-dispatch alerts) should be working.
2. **GLA_API_KEY — CORRECTED DIAGNOSIS, 2 Sep 2026**: this is not a simple expired-key issue. The GLA migrated their data platform to "London Datastore" (datapress.com/CKAN-based); the old endpoint the code calls (`planningdata.london.gov.uk/api/applications`) now redirects to a login page. Checked the dataset's new home directly (`data.london.gov.uk/dataset/planning-london-datahub-applications-236qk`) — it currently exposes **zero API resources/files**, only an embedded Power BI dashboard. Also checked whether planning.data.gov.uk (used elsewhere in this project) has picked this up — it hasn't; that listing is an empty placeholder. There may be no working machine-readable feed for this data right now at all, not just a credential to renew. Recommended: either send GLA-Datastore support (Tom Rees, tom@datapress.com — already in correspondence with Nick) a precise follow-up asking whether any real API resource exists behind that dataset ID, or accept the existing PlanIT/paid-API/mesh-scraper fallback for London and revisit only if London lead volume looks thin. **Not a tree-launch blocker either way** — London still gets leads through the other 3 sources.

## 🟡 SHOULD FIX SOON (not launch-blocking, but real)

3. **Pricing Table & Lockout copy was never finalized** — flagged as a priority item, not done as of the last record.
4. **Cold email sequence (the actual outreach copy) was never written.** Outreach tool (Instantly.ai) is live and warmed up, but there's no sequence loaded into it yet.
5. **PayPal Business Verification is stuck** (an automated ID-check ban) — low priority since Stripe is live and working, but worth a call to PayPal UK Business Support if you want it as a backup payment method.
6. **Pricing/discount decision for leads that already have an agent on record** — these are now honestly flagged to buyers before purchase (not a bug), but whether to discount or exclude them from sale entirely is a pricing call only you can make.
7. **Agent/Applicant name capture doesn't reach Leeds or London leads** — only the ~48-council mesh/Idox scraper captures this; Leeds (ArcGIS) and London (GLA) leads still show no applicant/agent info. Real data-completeness gap, not a crash risk.

## 🟢 DELIBERATELY DEFERRED (your own prior calls, not oversights)

8. **ICO registration** (£40 fee) — you explicitly said to defer this until right before the cold-email sequence actually goes out, not before.
9. **Domestic/homeowner listing page** — the old scraper was removed for GDPR/PECR risk; a consensual "submit your own job" replacement page was designed but not built. Post-launch feature, not a blocker.

## ⚪ LOW-PRIORITY, WATCH-ONLY

10. Dacorum council throwing connection-reset errors on scrapes (1 council, likely TLS fingerprinting, not urgent).
11. Occasional false "structure changed" scraper alerts on a few working councils (Croydon, Cornwall) — cosmetic log noise, leads still flow.

## NOT RELEVANT TO A TREE-ONLY LAUNCH (HMO vertical only — ignore these for now)

The HMO vertical (its GDPR-safe lead format, its dedicated mesh-scraper councils, the HMO side of the contractor-finder) is fully separate from tree and has no pricing tier yet — none of it needs to be resolved to launch tree. The Tier 1/2/4 classifier and manual review queue are already live and already benefit tree leads too (nothing to switch on); Tier 3 (Gemini) is optional and inert either way.

---

**Bottom line: item 1 (Resend) is the one thing I'd check before anything else** — it would silently break the exact moment someone pays you for a lead.

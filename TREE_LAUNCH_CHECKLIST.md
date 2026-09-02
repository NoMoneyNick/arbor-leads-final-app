# TreeKey — Tree-Only Launch Checklist

Compiled 2 Sep 2026 because Nick is launching the tree vertical on its own first. Pulled together from `AI_HANDOFF.md`, `PROJECT_STATE.md`, and `TASKS.md`-equivalent session notes — this is the first time everything tree-relevant has been in one place. HMO-only items are deliberately excluded (see bottom).

## 🔴 CRITICAL — VERIFY BEFORE LAUNCH

1. **Resend email domain verification — status UNKNOWN, last recorded as BROKEN.** As of 30 Aug 2026, `treekey.uk` was NOT verified on Resend, meaning every email sent through it — including the customer purchase-confirmation email and every lead-dispatch alert to subscribers — failed with HTTP 403. This was recorded at the time as "the single most urgent pre-launch blocker." I have found **no record since then that this was fixed**, and it's an external Resend.com account setting, not something I can check or fix from here. **You need to log into resend.com/domains and confirm `treekey.uk` shows as verified before launch** — if it isn't, contractors will pay for leads and never get the confirmation email, and subscribers will never receive dispatched leads at all.
2. **GLA_API_KEY is invalid.** This affects London tree leads specifically (not just HMO) — London currently falls back to PlanIT/paid-API only. Renew it in Render if you want London's GLA-sourced coverage back before/at launch. Renewal cadence is unknown — don't assume weekly, check GLA Planning Datahub's own docs.

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

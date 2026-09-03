# TreeKey — Tree-Only Launch Checklist

Compiled 2 Sep 2026, updated same day after a follow-up session fixing data-quality, security, and legal-copy issues. Pulled together from `AI_HANDOFF.md`, `PROJECT_STATE.md`, and this session's own work — this is the current single source of truth for "is tree ready to go public." HMO-only items are deliberately excluded (see bottom).

## 🔴 CRITICAL — VERIFY BEFORE LAUNCH

**Nick's explicit next priority (Sep 3 2026): item 9 below (the real Leeds/London applicant-data fix) — asked directly "have we fixed leeds and london?" and the honest answer is no, only the softer educated-guess tag layer exists. He asked for this to go on the urgent next-to-do list, not to be built in the same session as the National Park / QR code work. Do this next.**

1. ~~**Resend email domain verification**~~ — **RESOLVED.** Nick confirmed `treekey.uk` was verified on Resend as of ~3 days before 2 Sep 2026. Transactional email (purchase confirmations, lead-dispatch alerts) should be working.
2. **GLA_API_KEY — UPDATE, 2 Sep 2026**: Tom Rees (tom@datapress.com) replied — the key IS still valid, and the `data.london.gov.uk/dataset/...` page I'd checked was a red herring: that's just the data *catalogue* entry, a separate system from the real datahub. The actual live service is `https://planninglondondatahub.london.gov.uk/` — a JS web app I can't inspect directly (no browser tool in this session), so this needs Nick to open it and use its own help/contact screens to ask the datahub team directly whether/how the API is reachable now. **Not a tree-launch blocker either way** — London still gets leads through the other 3 sources (PlanIT/paid-API/mesh-scraper) regardless of how this resolves.
3. **Rotate your live credentials before going public.** You pasted your full Render environment variables into this chat, including a live `STRIPE_SECRET_KEY`, `STRIPE_WEBHOOK_SECRET`, and the full `SUPABASE_DB_URL` (with the database password in it). None of it was saved anywhere by me outside this session, but it was in plaintext in a chat log — rotate the Stripe key/webhook secret and the DB password in Render before you're live and depending on them. You already flagged this yourself; repeating it here so it's on the actual checklist, not just remembered.
4. **Google Places API hit its daily quota mid-scan today (429 "SearchTextRequest per day").** Code now auto-falls-back to the free scraper for the rest of the day when this happens, so enrichment doesn't just stop — but that fallback has its own known issue (the free scraper sometimes returns empty results with no error, which is *why* we switched to the paid API in the first place). To get consistently good data, raise the daily quota yourself: Google Cloud Console → Google Maps Platform → Quotas → find the Places API "SearchText requests per day" row → Edit → request a higher number → Submit. It's self-service and doesn't need Google's approval for a reasonable increase, and it's still comfortably inside your free monthly allowance (10,000 free/month) — you're just hitting an old, low per-day cap that was probably left over from when the key wasn't in active use.

## 🟡 SHOULD FIX SOON (not launch-blocking, but real)

5. **Pricing Table & Lockout copy was never finalized** — flagged as a priority item, not done as of the last record.
6. **Cold email sequence (the actual outreach copy) was never written.** Outreach tool (Instantly.ai) is live and warmed up, but there's no sequence loaded into it yet.
7. **PayPal Business Verification is stuck** (an automated ID-check ban) — low priority since Stripe is live and working, but worth a call to PayPal UK Business Support if you want it as a backup payment method.
8. **Pricing/discount decision for leads that already have an agent on record** — these are now honestly flagged to buyers before purchase (not a bug), but whether to discount or exclude them from sale entirely is a pricing call only you can make.
9. 🔴 **[URGENT — Nick's next priority, Sep 3 2026] Agent/Applicant name capture still doesn't directly reach Leeds or London leads** (only the ~48-council mesh/Idox scraper captures it from the source). **Partially softened today**: a new "educated guess" layer now looks at a lead's own description text for a company name that's classifiable as tree-surgeon-or-not, so a Leeds/London lead can at least get a `agent_guess:tree-surgeon` / `agent_guess:non-tree-surgeon` tag when the text gives a real clue — genuinely signal-less leads still get no guess, marked honestly as unconfirmed. Not a substitute for the real fix (reading Leeds' ArcGIS and London's GLA API's own applicant/agent fields directly), which is still not done.
10. **Legal pages need a proper pass before real outreach volume.** Full drafts already exist on disk (`terms_and_conditions_draft.md`, `privacy_policy_draft.md`) — these are meaningfully more complete than what's live, and flag two real open items: (a) a documented Legitimate Interests Assessment for processing planning-applicant data doesn't exist yet as an actual written document (only referenced), and (b) your legal name/trading address and confirmed Render hosting region need filling in. Both drafts say "get a solicitor to review before publishing" — that's still true. Separately, fixed a real gap in the *live* Terms of Service page today: it only described monthly subscriptions and never mentioned the one-off single-lead purchases you also sell, so the refund clause didn't clearly cover them — both are now described and both are stated as non-refundable.

## 🟢 DELIBERATELY DEFERRED (your own prior calls, not oversights)

11. **ICO registration** (£40 fee) — you explicitly said to defer this until right before the cold-email sequence actually goes out, not before.
12. **Domestic/homeowner listing page** — the old scraper was removed for GDPR/PECR risk; a consensual "submit your own job" replacement page was designed but not built. Post-launch feature, not a blocker.

## ⚪ LOW-PRIORITY, WATCH-ONLY

13. Dacorum council throwing connection-reset errors on scrapes (1 council, likely TLS fingerprinting, not urgent).
14. Occasional false "structure changed" scraper alerts on a few working councils (Croydon, Cornwall) — cosmetic log noise, leads still flow.

## ✅ FIXED THIS SESSION (for the record — no action needed from you except where noted)

- **Contact data quality (the "no email, no phone" problem)**: root cause was two-fold — a rate-limit bug in the free scraper (fixed), then a deeper issue where the free scraper started returning empty results with no visible error. Switched the actual enrichment call over to the real Google Places API you already had configured and paying for (well within its free tier). Today's daily-quota hiccup (item 4 above) is a side effect of that switch, now handled with an automatic fallback.
- **A production database bug that could silently disable Row-Level Security on some tables** — Phase 2 of the startup migration used to run all 13 security-lockdown statements in one shared transaction, so one table timing out under load could roll back all of them, including ones that had already succeeded. Now each table is handled independently with its own retry, matching the same fix already applied to the schema-migration phase after last week's real outage.
- **Category/classification redesign** — company name now overrides a misleading SIC code when the name itself says "tree surgeon" (e.g. AA GARDENING TREE SURGEONS LTD, SIC'd as a botanical garden); unconfirmed leads and contractors now get a soft "educated guess" tag based on their own name/description when there's a real signal, instead of being left in one flat "unconfirmed" bucket forever; a region-tag display bug (values never matching what was actually stored) is fixed.
- **Action needed from you for the above to apply to existing records**: run these once (`https://treekey.uk/trigger-...?secret=arsenal`): `trigger-requeue-dead-enrichment` (queues partners with no phone/email for a fresh try under the new API), then `trigger-autonomous-cycle` to actually re-run them, then `trigger-resync-lead-tags` and `trigger-resync-partner-tags` to apply the new category tags retroactively.

## NOT RELEVANT TO A TREE-ONLY LAUNCH (HMO vertical only — ignore these for now)

The HMO vertical (its GDPR-safe lead format, its dedicated mesh-scraper councils, the HMO side of the contractor-finder) is fully separate from tree and has no pricing tier yet — none of it needs to be resolved to launch tree. The Tier 1/2/4 classifier and manual review queue are already live and already benefit tree leads too (nothing to switch on); Tier 3 (Gemini) is optional and inert either way.

---

**Bottom line: items 3 and 4 (credential rotation, Places API daily quota) are the two things I'd do first** — one's a security exposure, the other is actively capping your data quality right now, today, mid-scan.

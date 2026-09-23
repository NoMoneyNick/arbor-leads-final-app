# TreeKey Cross-Audit — Quick Reference

*Bullet-point summary of all 5 audit threads (pricing, legal/compliance, launch sequencing, lead-category validity, competitive positioning). For the full reasoning behind each point, see TreeKey_Audit_Findings_Log.md.*

## 1. Pricing

- Hold current one-off prices: £19 / £29 / £39 / £49. Don't change them again yet.
- Don't build the 5-tier reclassification (Starter/Growth/Consultant/Commercial & Forestry/Elite) or add £35/£69 — shelved, not enough evidence.
- Don't sell more subscriptions until you've defined exactly what a subscriber gets (how many leads, what happens when they run out) — that doesn't exist yet even on paper.
- Track "total spent ÷ jobs actually won" as the real number that matters — not a guessed conversion rate.
- Test individual purchases first, subscriptions later — don't test both at once.
- Two of my own competitor numbers were wrong (fixed): Bark is £1.80/credit not £1.20; Checkatrade doesn't have a flat "£5-40 per lead on top" the way I first said.

## 2. Legal/compliance — the most serious thread

- Biggest new risk: TreeKey scrapes and sells homeowners' names/addresses, and UK GDPR likely requires you to proactively tell each of them within ~1 month — a privacy policy link on the website does NOT count as telling them. This has never been done.
- There's currently zero way for a homeowner to object or opt out, even if they wanted to.
- Non-tree applications (HMO etc.) are still being stored with people's names/addresses for no reason at all — should probably be deleted.
- Sold leads are kept forever; unsold ones auto-delete after 56 days (confirmed this isn't accidentally reset by rediscovery).
- One-to-one phone calls and free-lead signups are NOT automatically legal ways to recruit contractors for anything promotional — don't assume they're safe.
- Next step: get a solicitor to answer one specific question (drafted and ready) about exactly this data flow — this is more urgent than the pricing pilot.
- Also: confirm you actually rotated the flagged credentials (Stripe key, DB URL, etc.) — separate, immediate task.

## 3. Launch order (go-to-market)

- Reorder the 4 gates you had — cold email should be LAST, not first, since it recruits people into an offer that isn't proven safe or useful yet.
- New order: (1) legally safe to operate → (2) purchase actually delivers correctly (already working, just confirm) → (3) product is actually useful/available → (4) simple pricing → (5) small manual pilot.
- Free-lead redemption and full automated cold email are NOT required to get your first few paying customers.
- Highest-value single action: message your one real £19 buyer and ask if it worked out (script already written).

## 4. Is the "Consultant/survey" lead category even real?

- Big concern: many councils require the tree survey to be submitted WITH the planning application — meaning by the time you scrape it, the survey work may already be done. You might be selling something that doesn't exist.
- Same blind spot may apply to your whole "target arb consultants" strategy, not just this one category — worth taking seriously.
- Plan: pull ~20 real "consultant" and ~20 "commercial" leads (query written, ready to run), manually check each against the actual council website, classify as done/outstanding/already-appointed/unclear, then show a mix of good and bad examples to 2 real consultants and ask if they'd actually buy it.
- Don't build any new category or price until that test is done.

## 5. Competitive positioning

- Drop "we don't spend on ads" from any sales pitch — that's your cost saving, not the customer's benefit. Not persuasive to them.
- Don't claim exclusivity is unique — a competitor (PlanWatch, £149/mo) already sells a similar exclusive-territory idea, confirmed real.
- "Exclusive lead" doesn't mean "guaranteed nobody else can find it" — another company or the contractor themselves could still find the same public planning application independently.
- Being "first to find it" only matters if the specific work is actually still up for grabs — ties back to point 4 above.
- Bottom line: your prices aren't proven wrong, but they're not proven right either — the fix is proving real value through the pilot, not panicking and cutting price.

## Do this week, in order

1. Send the buyer follow-up message.
2. Start the solicitor evidence pack (more urgent than anything else here).
3. Run the 20-lead sample check.

Everything else waits.

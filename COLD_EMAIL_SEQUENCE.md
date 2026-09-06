# TreeKey Cold Email Sequence — Draft v1

**[NEXT AI NOTE: The user has requested that cold emails be highly targeted at each specific business type. Do not use a one-size-fits-all sequence. Instead, write specific sequences mapped to the packages (e.g., a "Stump Pro" sequence just for stump grinders, a "Commercial Pro" sequence for large crews, etc.). Each sequence should highlight the specific leads relevant to that niche.]**

Written 2 Sep 2026, pulling real pricing/copy from `payments.py` (not the older, now-stale figures in `PROJECT_STATE.md`'s original outline — those tiers/prices have since changed). Targets: LTD directors and sole-trader tree surgeons, legal basis per `MANIFEST.md` (PECR Reg 22 B2B exemption + UK GDPR Art 6(1)(f) legitimate interests for LTDs; sole traders need post/WhatsApp, not cold email, per that same doc).

**Reminder tied to sending this**: Nick's own recorded decision was to register with the ICO right before this sequence actually goes out, not before — do that first.

**Personalization tokens used:** `{{director_name}}`, `{{company_name}}`, `{{city}}`, `{{recent_tpo_street}}`

---

## Email 1 — The Free Lead (proof, not pitch)

**Subject:** {{company_name}} — a live tree job near {{city}} right now

Hi {{director_name}},

TreeKey watches every council planning register in the UK for tree work notices — TPO applications, felling licences, hedge removals — the moment they're filed, straight from the public record.

There's one live right now near {{recent_tpo_street}} in {{city}}. It's yours, free, no card needed: [link] — just the address and application details, same as every lead on the platform (name included only when the council itself published one — we don't guess or buy contact data).

No pitch in this email. Take the lead, see if it's useful, and I'll follow up in a few days.

— Nick
TreeKey (treekey.uk)

*Unsubscribe: [link]*

---

## Email 2 — What you're missing without it

**Subject:** How many of these did you miss this month?

Hi {{director_name}},

Council planning registers publish hundreds of tree-work notices a month across the UK — TPO applications, garden fellings, commercial site clearances — but almost nobody checks them daily. Whoever gets there first with a quote usually wins the job.

TreeKey does the checking for you. Real example of what a subscription looks like:

- **Climber (Domestic)** — £49/month — daily domestic crown reductions, pollards, garden fellings, plus 1-tap homeowner letters and a Street View brief on each lead. Our most-subscribed plan.
- **Stump Pro** — £29/month — if stump grinding and gate-clearance jobs are your bread and butter, this is filtered just for that.
- **Commercial & Forestry** — £139/month — multi-tree site clearances, Ash Dieback blocks, B2B tenders.

Every lead is the real address and application details the council published — no fabricated numbers, no guessed contact info, no locked-in contract.

Worth a look for {{company_name}}? Compare all the plans here: https://treekey.uk/pricing

— Nick
TreeKey

*Unsubscribe: [link]*

---

## Email 3 — Soft close, low-commitment option

**Subject:** No subscription needed if you'd rather try one first

Hi {{director_name}},

If a monthly plan isn't the right fit yet, you don't need one to get started — single leads are available one at a time, no commitment:

- £19 — domestic maintenance leads
- £29 — standard felling/tree removal
- £49 — commercial/site clearance/TPO leads

Each one's exclusive — once purchased it's removed from the platform and never resold. Grab one whenever a job in {{city}} looks worth it: https://treekey.uk/pricing

Either way, thanks for taking a look — happy to answer any questions directly.

— Nick
TreeKey

*Unsubscribe: [link]*

---

## Email 1 — Variants (deeper persuasion pass, drafted 2 Sep 2026)

Nick asked for these to use real, evidence-backed persuasion psychology — reciprocity, curiosity gaps, loss aversion, specificity, foot-in-the-door, the Zeigarnik effect (open loops), pattern interrupts — pushed as far as they'll go. **One boundary held throughout, matching the standard already enforced everywhere else on this site**: every technique here works on *genuine* information (a real address, a real deadline, real specificity) — nothing invents urgency, fake scarcity, fabricated numbers, or a claim the product can't back up. That's not a watered-down version of "every trick in the book" — the tricks that actually move B2B reply rates are structural (specificity, loss aversion, low-friction asks, curiosity), not fabricated stats, and fabrication is also the exact thing that already had to be ripped out of this codebase twice (see PROJECT_STATE.md items 12-13). Pick whichever variant's angle fits your gut best, or split-test two of them.

---

### Variant A — Specificity + Loss Aversion (the "proof of surveillance" angle)

*Mechanism: hyper-specific detail (their own street, their own council, a real filing date) reads as evidence of individual attention, not a mail-merge blast — this is the single strongest lever in cold B2B email because it defeats the recipient's spam-pattern-matching in the first three seconds. The loss-aversion line is true, not invented: another tree surgeon genuinely can and does grab jobs first.*

**Subject:** {{recent_tpo_street}}, {{city}} — filed this week, still unclaimed

{{director_name}},

{{city}} Council logged a tree work application on {{recent_tpo_street}} this week. I checked — no tree surgeon's shown up as agent on it yet, which usually means whoever calls first gets the quote.

It's yours, free, right now: [link]. Just the address and the application details, nothing else attached.

I watch every UK council register daily for exactly this — jobs the moment they're filed, before they're gone. More on that in a couple of days if you want it; no obligation either way.

— Nick, TreeKey

---

### Variant B — Curiosity Gap + Foot-in-the-Door (the "low-friction open loop" angle)

*Mechanism: the subject line creates an open question the brain wants resolved (Zeigarnik effect); the ask is deliberately tiny (click one link, no card, no form) so saying yes costs nothing — a small "yes" here makes a bigger "yes" later far more likely than asking for the big commitment up front.*

**Subject:** a question about {{company_name}} and {{city}}

{{director_name}},

Quick one — does {{company_name}} check council planning registers daily for new tree work applications, or mostly rely on word of mouth and repeat clients?

If it's the second one, there's a real job sitting on {{recent_tpo_street}} in {{city}} right now that nobody's claimed. I'll just send it over, no cost, no card: [link].

Take it or don't — either way, curious how you're finding jobs at the moment.

— Nick, TreeKey

---

### Variant C — Pattern Interrupt (the "doesn't read like a sales email" angle)

*Mechanism: B2B inboxes are trained to spam-filter anything shaped like marketing copy — short, plain, almost terse phrasing (no bold claims, no exclamation points, looks like it was typed by a person in a hurry) gets read specifically because it doesn't match that pattern.*

**Subject:** re: tree work, {{recent_tpo_street}}

Hi {{director_name}} — found a live one for you.

{{city}} Council: tree application on {{recent_tpo_street}}, filed this week, no agent listed yet. Address + details here, free: [link]

I run something that pulls these off every UK council register daily. Not selling anything in this email — just thought this one was worth sending over before someone else calls first.

Nick

---

### Variant D — Reciprocity-Led, Softest Ask (the "give first, ask never" angle — best for risk-averse sends)

*Mechanism: pure reciprocity with zero ask anywhere in the email, not even a soft one — this is the variant to use if Nick wants the safest possible first touch, since a genuinely no-strings gift is the hardest version of this email for a recipient to feel manipulated by, and reciprocity research consistently shows it lifts reply rates on the *next* touch even when this one gets no reply at all.*

**Subject:** free tree lead for {{company_name}}, no catch

{{director_name}},

Found a live tree application on {{recent_tpo_street}} in {{city}} while going through the council register — thought of {{company_name}} since you're local. It's yours: [link]. No card, no signup, no follow-up call.

I do this daily across UK council registers as part of what I run (TreeKey) — figured I'd rather just hand one over than explain it first.

— Nick

---

**Recommendation if you only run one:** Variant A. It carries the most real persuasion weight (specificity + true loss-aversion) while staying fully inside the honesty standard, and it's closest in tone to what already converts in B2B trade outreach. Variant D is the safest fallback if you're worried about coming across as pushy on a first touch.

## Notes for whoever sends this (v1)

1. Both `£49/month "Climber (Domestic)"` in Email 2 and the single-lead prices in Email 3 are pulled directly from the live `PLANS` dict in `payments.py` as of today — if pricing changes before this goes out, update the numbers here to match, don't let this draft drift out of sync with the real checkout prices.
2. The homepage's own postcode-selector flow currently points to a *different* set of live tiers (Sole Trader £49/mo, Commercial Pro £149/mo, Regional Elite £299/mo) — both tier sets are real and live on the site today, just on different pages. This draft deliberately routes to `/pricing` (the tailored-trade tiers) since that's the more natural landing page for a cold-email click; worth Nick's own call on which funnel he'd rather cold traffic hit first.
3. No claims here that aren't backed by what the product actually does — no accreditation badges, no fabricated "leads found near you" counts, no promised phone/email contact. That's a deliberate match to the fabricated-claims cleanup already done elsewhere on the site (see `PROJECT_STATE.md` items 11-13) — don't reintroduce that pattern here.

---
---

# V2 — Rewritten Email Sequence (Opus 4.6, 5 Sep 2026)

Written after a deep analysis of the product from both the customer (tree surgeon) and owner perspective. Fixes identified in that analysis: lead details placed inline instead of behind a link, exclusivity moved from Email 3 to Email 1, "no phone number" addressed head-on, lead volumes stated transparently, "calls first" language removed, director name fallback added, "re:" dark pattern removed.

**Sequence structure:** Initial + 2 follow-ups. Day 0 / Day 3 / Day 7.

**Additional personalisation tokens required (beyond v1):**
- `{{recent_tpo_work}}` — the application's work summary (e.g. "T1 Oak — Crown reduction by 3m")
- `{{recent_tpo_council}}` — the council name (e.g. "Leeds")
- `{{recent_tpo_ref}}` — the planning reference number (e.g. "26/04913/TR")

These must be exported by the enrichment script alongside the existing `{{director_name}}`, `{{company_name}}`, `{{city}}`, `{{recent_tpo_street}}` tokens.

---

## V2 Email 1 — Day 0 (with director name)

**Subject:** {{recent_tpo_street}}, {{city}} — tree job, no one on it

{{director_name}},

Found this on {{recent_tpo_council}} Council's register — no tree surgeon listed on it yet:

📍 {{recent_tpo_street}}, {{city}}
🌳 {{recent_tpo_work}}
📋 Ref: {{recent_tpo_ref}}

It's yours. No card, no signup.

I run TreeKey — we scan every UK council planning register daily and match tree work to local contractors. When someone buys a lead from us, it's permanently removed from the platform. No one else gets it. Not how Bark works.

If you want to reach the homeowner, there's a printable letter on the site that takes 10 seconds — drop it through the door before anyone else knows the job exists: [letter generator link]

More in a few days if you're interested. No obligation.

Nick
TreeKey (treekey.uk)

*Unsubscribe: [link]*

---

## V2 Email 1 — Day 0 (director name missing — fallback)

**Subject:** {{recent_tpo_street}}, {{city}} — tree job, no one on it

Live tree application on {{recent_tpo_council}} Council's register — no tree surgeon listed yet:

📍 {{recent_tpo_street}}, {{city}}
🌳 {{recent_tpo_work}}
📋 Ref: {{recent_tpo_ref}}

Sending this to {{company_name}} since you're local. It's yours — no card, no signup.

I run TreeKey — we scan every UK council planning register daily and match tree work to local contractors. When someone buys a lead from us, it's permanently removed from the platform. No one else gets it. Not how Bark works.

If you want to reach the homeowner, there's a printable letter on the site that takes 10 seconds — drop it through the door before anyone else knows the job exists: [letter generator link]

More in a few days if you're interested. No obligation.

Nick
TreeKey (treekey.uk)

*Unsubscribe: [link]*

---

## V2 Email 2 — Day 3

**Subject:** the lead I sent — did you use it?

{{director_name}},

Quick follow-up on the {{recent_tpo_street}} lead.

That wasn't a one-off. TreeKey finds those daily — TPO applications, felling licences, hedge removals, site clearances — from every council register in the country, the moment they're filed.

Three things worth knowing about how this works:

**1. Every lead is exclusive.** One buyer. Then it's permanently gone. No shared leads, no bidding wars, no Bark-style race to the bottom.

**2. No phone number** — councils don't publish them on planning applications, and we don't make them up. Each lead comes with a 1-tap printable letter and a Street View brief so you can size up the job and reach the homeowner the same day.

**3. You're there first.** These leads are filed days or weeks before the homeowner starts Googling "tree surgeon near me." You're knocking before your competition knows the job exists.

Plans:

- **Stump Pro** — £29/mo — 3 felling & stump leads
- **Climber** — £49/mo — 5 domestic leads (most popular)
- **Commercial** — £139/mo — 12 site clearance & TPO leads

One domestic job a month covers the subscription several times over.

Or grab single leads from £19 — no subscription needed.

https://treekey.uk/pricing

Nick
TreeKey

*Unsubscribe: [link]*

---

## V2 Email 3 — Day 7

**Subject:** last one from me

{{director_name}},

Last email from me on this.

If a monthly plan isn't right for {{company_name}} right now, single leads are available one at a time:

- £19 — domestic maintenance
- £29 — felling / tree removal
- £49 — commercial / site clearance / TPO

Each one's exclusive — once it's bought, it's burned from the system and never resold.

https://treekey.uk/pricing

Either way, the {{recent_tpo_street}} lead is still yours. Good luck with it.

Nick
TreeKey

*Unsubscribe: [link]*

---

## V1 vs V2 — Key Differences

| | V1 (Sonnet 5) | V2 (Opus 4.6) |
|-|---------------|----------------|
| **Lead details in Email 1** | Behind a [link] | In the email body (street, work, ref) |
| **Exclusivity** | First mentioned in Email 3 | Mentioned in Email 1 |
| **"No phone number"** | Never addressed | Explained head-on in Email 2 |
| **Lead volumes** | Not stated | Stated (3/5/12 per month) |
| **"Calls first"** | In Variants A & C | Removed — "drop it through the door" |
| **Director name fallback** | None (says "Hi Managing Director") | Separate version that skips greeting |
| **"re:" subject prefix** | Used in Variant C | Removed (dark pattern) |
| **Email 3 callback** | No reference to Email 1 | References the free lead by street |
| **Tone** | Polished, framework-labelled | Blunter, shorter, tradesperson-native |

## Notes for whoever sends this (v2)

1. The additional tokens (`recent_tpo_work`, `recent_tpo_council`, `recent_tpo_ref`) require an updated enrichment script — see `enrich_outreach_contacts.py` (to be built). Truncate `recent_tpo_work` to ~80 characters to keep Email 1 clean.
2. Pricing in Email 2 references the `/pricing` page tiers. If the homepage pricing is unified with `/pricing` before sending, update these numbers to match.
3. The letter generator link in Email 1 must be accessible without login. Verify this works before the first send.
4. For contacts where `recent_tpo_street` cannot be populated (no nearby unclaimed lead), segment them out — do not send Email 1 with a blank street. Either hold them for later or write a separate non-lead-specific Email 1.

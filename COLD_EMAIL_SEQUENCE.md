# TreeKey Cold Email Sequence — Draft v1

**[NEXT AI NOTE: The user has requested that cold emails be highly targeted at each specific business type. Do not use a one-size-fits-all sequence. Instead, write specific sequences mapped to the packages (e.g., a "Stump Pro" sequence just for stump grinders, a "Commercial Pro" sequence for large crews, etc.). Each sequence should highlight the specific leads relevant to that niche.]**

Written 2 Sep 2026, pulling real pricing/copy from `payments.py` (not the older, now-stale figures in `PROJECT_STATE.md`'s original outline — those tiers/prices have since changed). Targets: LTD directors and sole-trader tree surgeons, legal basis per `MANIFEST.md` (PECR Reg 22 B2B exemption + UK GDPR Art 6(1)(f) legitimate interests for LTDs; sole traders need post/WhatsApp, not cold email, per that same doc).

**Reminder tied to sending this**: Nick's own recorded decision was to register with the ICO right before this sequence actually goes out, not before — do that first.

**Styling instruction (10 Sep 2026):** in the actual HTML send template, the unsubscribe link must be very small (roughly 10-11px) and pushed well below the sign-off, visually separated from the body copy (e.g. extra top margin, muted grey colour, thin divider above it) — not a normal-weight line sitting right under "Nick, TreeKey".

**Unsubscribe infrastructure now real (10 Sep 2026):** every `[link]`/`Unsubscribe: [link]` placeholder in this document referred to a mechanism that didn't exist yet — the only working unsubscribe route required an existing limbo_accounts row, which a cold contact who's never touched the site doesn't have. That's now fixed: `GET /unsubscribe?token=...` works for any email address (see main.py), signed the same way as every other link in this app via `_sign_session_cookie(email)`. When the enrichment script mentioned below is actually built, every send must build this token per recipient and use the real URL, e.g. `f"{PUBLIC_APP_URL}/unsubscribe?token={_sign_session_cookie(recipient_email)}"` — not the `[link]` placeholder still shown throughout the drafts in this file.

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

---
---

# V3 — Claude's Selection (Sonnet 5, 10 Sep 2026)

Produced from the exact comparison prompt Nick is also running through ChatGPT and Gemini, so all three sets are directly comparable. Rules followed: 4 labeled variants per email (not one version each), real tokens only, real pricing only, no fabricated urgency/scarcity/numbers, UK spelling, no em dashes, under 150 words each, subject line on every variant. Same persuasion labels as the comparison prompt: Specificity + Loss Aversion, Curiosity Gap + Foot-in-the-Door, Pattern Interrupt, Reciprocity-Led (softest ask).

## V3 Email 1 — Day 0

### Variant A — Specificity + Loss Aversion

**Subject:** {{recent_tpo_ref}}: tree job on {{recent_tpo_street}}, unclaimed

{{recent_tpo_council}} Council logged {{recent_tpo_work}} on {{recent_tpo_street}} in {{city}}. No contractor listed on the application yet. It's free, no card: [link]. I watch every UK council register daily. Whoever calls the homeowner first usually gets the job, so I'm sending this before it sits any longer.

Nick, TreeKey

### Variant B — Curiosity Gap + Foot-in-the-Door

**Subject:** a question about how {{company_name}} finds jobs

Quick one. Does {{company_name}} check council planning registers daily for new tree work, or mostly rely on repeat clients and word of mouth?

If it's the second, there's a live one on {{recent_tpo_street}} in {{city}} (ref {{recent_tpo_ref}}) that nobody's claimed. I'll send the details over, free, no card: [link].

Curious how you're finding work at the moment either way.

Nick, TreeKey

### Variant C — Pattern Interrupt

**Subject:** {{recent_tpo_street}}, found this for you

Hi {{director_name}}, found a live one.

{{recent_tpo_council}} Council: {{recent_tpo_work}} on {{recent_tpo_street}}, filed this week, no contractor on it yet. Details here, free: [link]

I pull these off every UK council register daily. Not selling anything in this email, just thought it was worth sending before someone else calls first.

Nick

### Variant D — Reciprocity-Led (softest ask)

**Subject:** free tree lead for {{company_name}}, no catch

Found this going through {{recent_tpo_council}} Council's register: {{recent_tpo_work}} on {{recent_tpo_street}} in {{city}}. Thought of {{company_name}} since you're local. It's yours: [link]. No card, no signup, no follow-up call.

I run TreeKey, scanning UK council registers daily for tree work. Figured I'd rather hand one over than explain it first.

Nick

### Fallback — director name missing

**Subject:** {{recent_tpo_street}}, found this for {{company_name}}

Found this on {{recent_tpo_council}} Council's register: {{recent_tpo_work}} on {{recent_tpo_street}} in {{city}}. No contractor listed yet.

Sending it to {{company_name}} since you're local. It's yours, free, no card: [link]

I run TreeKey. We scan every UK council register daily for tree work notices. More in a few days if useful, no obligation either way.

Nick, TreeKey

---

## V3 Email 2 — Day 3

### Variant A — Specificity + Loss Aversion

**Subject:** did the {{recent_tpo_street}} lead go anywhere?

Checking in on the {{recent_tpo_street}} lead. That wasn't a one-off, TreeKey finds jobs like it daily across every UK council register: TPO applications, felling licences, hedge removals, site clearances, the moment they're filed.

Each lead is exclusive. Once bought, it's removed from the platform for good, no shared leads, no bidding.

Climber (Domestic) is £49/mo for 5 domestic leads a month, our most-subscribed plan. Stump Pro is £29/mo, Commercial & Forestry £139/mo. One job usually covers it several times over.

https://treekey.uk/pricing

Nick, TreeKey

### Variant B — Curiosity Gap + Foot-in-the-Door

**Subject:** how {{company_name}} could be first to call, every time

Following up on the {{recent_tpo_street}} lead. Here's the part I didn't explain: councils publish these notices days or weeks before a homeowner starts Googling "tree surgeon near me." Whoever gets there first usually wins the quote.

TreeKey watches every UK register daily so you're that first call. Leads are exclusive, and there's no phone number published (councils don't share them), so each one comes with a printable letter and a Street View brief instead.

Worth a look at the plans? https://treekey.uk/pricing

Nick, TreeKey

### Variant C — Pattern Interrupt

**Subject:** re the lead, one more thing

Quick one. The {{recent_tpo_street}} job I sent wasn't a fluke, I find those daily.

No phone numbers on these (councils don't publish them), so each lead ships with a 1-tap printable letter instead. Exclusive too, once someone buys a lead it's gone from the platform for good.

Plans start at £29/mo (Stump Pro), £49/mo covers 5 domestic leads (Climber), £139/mo for commercial and forestry.

https://treekey.uk/pricing

Nick

### Variant D — Reciprocity-Led (softest ask)

**Subject:** no pressure, just explaining what I do

Since the {{recent_tpo_street}} lead was useful (or even if it wasn't), thought I'd explain properly what TreeKey does. We scan every UK council register daily for tree work notices and pass them to local contractors, exclusively, one buyer per lead, then it's gone for good.

Plans from £29/mo, or grab single leads from £19 with no subscription at all if you'd rather try one first.

https://treekey.uk/pricing

No reply needed either way.

Nick, TreeKey

---

## V3 Email 3 — Day 7

### Variant A — Specificity + Loss Aversion

**Subject:** last one, {{recent_tpo_street}} is still there

Last email from me on this. The {{recent_tpo_street}} lead is still yours if you want it, free: [link].

If a monthly plan isn't right for {{company_name}} yet, single leads start at £19, no commitment.

https://treekey.uk/pricing

Good luck with it either way.

Nick, TreeKey

### Variant B — Curiosity Gap + Foot-in-the-Door

**Subject:** one last thing before I stop emailing

I'll stop after this one. Quick question: would {{company_name}} rather try a single lead first, no subscription, before deciding on a plan?

If so, single leads run £19 to £49 depending on job type, each one exclusive to whoever buys it.

https://treekey.uk/pricing

Either way, thanks for reading these, and the {{recent_tpo_street}} lead is still yours.

Nick, TreeKey

### Variant C — Pattern Interrupt

**Subject:** last one from me

Last email on this, promise.

If a plan's not right for {{company_name}} right now, single leads are £19, £29, or £49 depending on the job, no subscription needed.

https://treekey.uk/pricing

{{recent_tpo_street}} is still yours regardless. Good luck with it.

Nick

### Variant D — Reciprocity-Led (softest ask)

**Subject:** no more emails after this, just a thank you

This is the last one from me either way, no more chasing after this.

Whatever you decide on a plan, the {{recent_tpo_street}} lead from earlier is still yours to use, no strings. If single leads suit {{company_name}} better than a subscription, they start at £19 with nothing recurring.

https://treekey.uk/pricing

Thanks for reading this far.

Nick, TreeKey

---
---

# Cross-LLM Synthesis (Sonnet 5, 10 Sep 2026)

Ran the comparison prompt against all entries Nick collected: Sonnet 5 (V1/V2/V3), Gemini Pro 3.1 High, GPT Astra 6 Medium, and Grok. One entry was excluded from ranking for breaking the no-fabrication rule: Grok's Email 1 Variant 1 claims the reader is "racing to the bottom on price against five other firms" — an invented number with no basis, repeated nowhere else in that draft set.

## Step 1 — Rankings (top 3 per email, no long explanations)

**Email 1**
1. V2 (Opus) original — inline lead details, exclusivity stated upfront, names Bark as the contrast, real letter-generator CTA.
2. V1 Variant A (Sonnet) — tightest prose, honest loss aversion ("whoever calls first").
3. GPT Astra Variant 1 — "it's already public, anyone can see it" is a fresh honest-urgency angle.

**Email 2**
1. V2 (Opus) original — most substance: real lead volumes, explains the no-phone-number gap, first-mover framing.
2. GPT Astra Variant 1 — clean mechanic: public register vs. exclusivity-once-bought, no filler.
3. Grok Variant 3 — "we don't send this to a list of firms" is a good differentiator against directory sites.

**Email 3**
1. Grok Variant 3 ("I am closing this thread") — most memorable breakup email, "most follow-ups keep selling, this one is to stop" earns attention.
2. GPT Astra Variant 2 — binary "yes or no" ask, lowest-friction close in the set.
3. V2 (Opus) original — ties back to the specific street lead, feels like one continuous conversation.

## Step 2 — Final synthesised versions

### Final Email 1 — Day 0

**Subject:** {{recent_tpo_street}}, {{city}} — filed this week, unclaimed

{{director_name}},

{{recent_tpo_council}} logged {{recent_tpo_work}} on {{recent_tpo_street}} this week (ref {{recent_tpo_ref}}). No tree surgeon's listed as agent yet.

It's on the public register, so anyone can see it, but nobody's contacted the homeowner first. It's yours, free, no card: [link]. There's a printable letter on the site too, so you can be at the door before anyone else knows the job exists.

I run TreeKey. We watch every UK council register daily and sell each lead once, then it's gone from the platform for good.

More in a few days if useful. No obligation.

Nick, TreeKey

**Fallback, director name missing:** open with "Found this for {{company_name}}," instead of "{{director_name}},", everything else unchanged.

### Final Email 2 — Day 3

**Subject:** the {{recent_tpo_street}} lead, still yours

{{director_name}},

Checking in on {{recent_tpo_street}}. That wasn't a one-off, TreeKey finds jobs like it daily across every UK council register, and we don't send them to a list of firms. Each lead's bought once, then it's off the platform for good.

No phone number on these, councils don't publish them, so each lead comes with a printable letter and a Street View brief instead.

Stump Pro is £29/mo (3 leads), Climber £49/mo (5 leads, most popular), Commercial & Forestry £139/mo (12 leads). Or grab single leads from £19, no subscription.

https://treekey.uk/pricing

Nick, TreeKey

### Final Email 3 — Day 7

**Subject:** closing this out, no more chasing

{{director_name}},

Most follow-ups keep selling. This one's to stop.

The {{recent_tpo_street}} lead from earlier is still yours either way, free, no card. If a plan's not right for {{company_name}} yet, single leads run £19 to £49 with no subscription.

Quick one before I go: worth a single lead, yes or no? Either answer's fine, and I won't chase this again.

https://treekey.uk/pricing

Nick, TreeKey

---
---

# Final Verdict (after independent re-runs, 10 Sep 2026)

Nick ran the ranking prompt separately through Gemini, GPT Astra, and Grok on the notepad file (not just Sonnet 5). Their picks converged independently on the same core mechanics as the Cross-LLM Synthesis above: honest scarcity via "sold once, not resold" rather than invented urgency, the no-phone-number gap solved with the printable letter, and a low-friction close on Email 3. That convergence across four separate models is the strongest signal in this whole exercise that those elements genuinely work, not just one model's preference.

**Caught and rejected:** one of the reruns (labelled "gemini" in Nick's file) reused the line "no racing to the bottom against five other firms" in its Final Email 1. That's a fabricated, unsourced number, originally introduced in Grok's very first Email 1 draft and already excluded from ranking for that reason. It resurfaced here and must never go into a live send. This is now the second time this exact line has slipped through, so treat "against five/three/X other firms" as a banned phrase in any future draft or edit pass.

**Approved sequence to actually use**, the Cross-LLM Synthesis final emails above (Final Email 1 / 2 / 3), with one upgrade to Final Email 1: open with "Public notice, not a directory lead." before the TPO detail, rather than leading straight with the street. GPT's and Grok's independent reruns both converged on that same opening move on their own, and it's a sharper pattern interrupt than the original opening line.

### Final Email 1 — Day 0 (revised opening)

**Subject:** {{recent_tpo_street}}, {{city}} — filed this week, unclaimed

{{director_name}},

Public notice, not a directory lead.

{{recent_tpo_council}} logged {{recent_tpo_work}} on {{recent_tpo_street}} this week (ref {{recent_tpo_ref}}). No tree surgeon's listed as agent yet.

It's on the public register, so anyone can see it, but nobody's contacted the homeowner first. It's yours, free, no card: [link]. There's a printable letter on the site too, so you can be at the door before anyone else knows the job exists.

I run TreeKey. We watch every UK council register daily and sell each lead once, then it's gone from the platform for good.

More in a few days if useful. No obligation.

Nick, TreeKey

**Fallback, director name missing:** open with "Found this for {{company_name}}," instead of "{{director_name}},", everything else unchanged.

Final Email 2 and Final Email 3 stand as written above, unchanged.

---
---

# Live-test corrections (Sonnet 5, 10 Sep 2026)

Nick had this Final Email 1 actually sent to a real inbox with a real reserved lead and code. Two things the copy-only ranking exercise couldn't catch, only a real read could:

1. **"nobody's called the homeowner first"** — factually inconsistent with this product's own repeatedly-stated fact that leads never include a phone number (councils don't publish one). "Called" implies a phone action nothing here actually offers. This is the SAME issue V2's own notes (line 171 above) said had already been removed ("'calls first' language removed") — it quietly came back in the Cross-LLM Synthesis / Final Verdict passes above and both are now corrected to "contacted". Treat "called the homeowner" / "calls first" as a banned phrase in any future draft or edit pass, same category as the "racing against N other firms" line above — this is now the second wording fix that had to be re-applied after resurfacing.
2. **"Public notice, not a directory lead."** — read as unexplained jargon to an actual recipient with no prior context ("I don't even know if tree surgeons will know what a public notice is, or a directory lead"), not the pattern-interrupt hook it looked like on paper. Dropped from the live send. What Nick's own reaction confirmed DOES work: a real, recognisable place name in the opening line ("I recognise Banstead, that's near me") is what signals genuine individual attention — not an unexplained phrase. Live version opens with "Found a live tree job near {{area}} that nobody's claimed yet." instead.
3. **Street-level address detail removed entirely, replaced with the postcode-district area name** (e.g. "Reigate and Banstead" rather than a street name). Not a copy preference — a real send using the actual scraped `{{recent_tpo_street}}` value leaked the FULL address (house number, street, town, county, postcode) into the email, because that field isn't reliably comma-delimited the way it reads in this document's examples. The specificity mechanism still works at area level (confirmed by Nick's own reaction above) without the leak risk, so this document's remaining `{{recent_tpo_street}}` token should be treated as unsafe to use verbatim in a real send from here on -- resolve to an area/district name instead, never the raw scraped address.
4. **"...free for anyone to look up..."** (10 Sep 2026, Nick's own reaction to a live send) — undercut the pitch instead of building urgency: a reader's honest reaction to being told it's "free for anyone to look up" is "well, I'll just look it up myself" — inviting the DIY response this email exists to pre-empt. Corrected to lead with what TreeKey actually did (pulled it, verified it, set it aside) rather than pointing at the public register as something the reader could just go check themselves: "It's a live, unclaimed job on the council's register — we've already pulled it, verified it and set it aside for you, so you're not the one trawling every council portal yourself. Nobody's contacted the homeowner yet." Treat "free for anyone to look up" / "public [register/portal], free to check" framing as a banned pattern in future drafts — it should always read as TreeKey having done the finding, never as an invitation to self-serve.

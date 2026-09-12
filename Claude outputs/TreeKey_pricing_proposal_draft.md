# TreeKey pricing & packages — draft for review

This is a first full pass, built from the real numbers already in the codebase (current `PLANS`, `TIER_QUOTAS`, `TIER_MAX_RADIUS` in payments.py/database.py) plus the lead-volume and value-tier classifiers we shipped this week. Two things are estimates rather than verified facts, and I've flagged both clearly below — treat everything else as grounded in what the product actually does today.

## Two things to fix immediately, independent of any pricing decision

Checking the current live copy against the actual code turned up two claims on **TreeKey Elite (£179/mo)** that aren't true today. "Automated Direct Mailouts" is sold as an Elite feature, but there is no letter-sending code anywhere in the project — this is exactly the feature we discussed building last night and haven't started. And "Zero-Minute Instant WhatsApp Alerts" is described as real-time push notification, but the actual mechanism is a forward-to-yourself WhatsApp link inside the regular email (confirmed in notifications.py's own comments: "not push delivery via WhatsApp's Business API — no such integration exists here"), and that same forward-link option looks to be available to any subscriber who sets their notification preference to WhatsApp, not something exclusive to Elite. Anyone paying £179/mo today is being sold at least one feature that doesn't exist and possibly a second that isn't exclusive. Given the standing rule about never misleading a customer about what they're getting, I'd fix this copy today regardless of what happens with the rest of this proposal — happy to do it as soon as you say go.

## The problem with the current 8 tiers

Right now there are two overlapping tier families: a "tailored" set (Stump Pro, Climber Domestic, Arb Consultant, Commercial & Forestry, Elite) and a separate "homepage" set (Sole Trader, Commercial Pro, Regional Elite) that was built later and never merged in. Sole Trader and Climber Domestic are both £49/mo with near-identical copy. Stump Pro is a whole £29/mo tier built around one job type, which sits awkwardly next to your own realization from the volume report that a handful of stump jobs nationally isn't enough to justify a dedicated package — job-type filtering already exists inside every other tier, so a customer who only wants stump work can just filter for it rather than needing a separate priced product. Eight tiers is also just a lot for a non-technical tree surgeon to parse quickly, which cuts against the "explain the product simply" problem you raised last night.

## Proposed ladder: five tiers instead of eight

| Tier | Price/mo | Radius | Monthly quota* | Who it's for |
|---|---|---|---|---|
| Starter | £39 | 15mi | 6 | 1-2 van operators, domestic + small commercial, all job types (merges Sole Trader, Climber Domestic, Stump Pro) |
| Growth | £79 | 20mi | 10 | Established 2-3 man crews wanting more volume and a wider net |
| Consultant | £99 | 20mi | 8 | Qualified arborists/TechArb — BS5837 surveys, condition discharges, planning-stage intelligence (unchanged focus, since this is your primary strategic target per the pivot) |
| Commercial & Forestry | £159 | 30mi | 14 | Heavy machinery operators, multi-tree clearances, Ash Dieback contracts, tenders (merges Commercial Forestry + Commercial Pro) |
| Elite | £249 | 45mi | 20 | Full category access, top dispatch priority, first look at Elite-value-tier leads, once real: bonus letter credits (merges Elite + Regional Elite) |

*Quotas are interpolated from your current live values (3/5/8/12/18/14/25 across the old 8 tiers) — they are my best estimate, not verified against real demand. Before locking these in, run `/admin/lead-volume-report` and `/admin/lead-value-report` on the live site and send me the real weekly/monthly counts per category and region — that lets me size each tier's quota against what actually gets generated rather than a guess, and confirms whether Elite's 20/month is realistic supply or oversold.

## Single-lead pricing: use the value-tier work we just built

Today, one-off leads are priced purely by physical size: £19 small, £29 medium, £49 large. That ignores everything the new Elite/Priority/Standard value classifier tells you — a small job with a TPO attached is worth more to a buyer than a large but legally uneventful domestic clearance, and the current pricing can't tell them apart. Proposed grid, replacing the flat three prices:

| | Small | Medium | Large |
|---|---|---|---|
| Standard | £15 | £25 | £39 |
| Priority | £25 | £39 | £59 |
| Elite | £39 | £59 | £89 |

Standard-tier prices sit close to today's flat prices, so most leads don't get more expensive — this is additive revenue from the leads that are genuinely worth more, not a blanket price rise.

## The letter-posting add-on (from last night)

Positioned as an optional, per-letter paid add-on, not bundled into any tier and not mandatory — matching what we agreed. Pricing here is a placeholder: **£2.50 per letter** is my working estimate, based on BuildAlert's own public £2/letter reference point plus a small margin, but this is not yet grounded in a real vendor quote. Before this goes live, the actual per-letter cost from PostGrid UK or Stannp needs confirming (specifically whether it's true pay-per-piece billing with no prepaid credit requirement, per your cash-flow constraint) — I gave you a ready-to-paste question for that already. Once confirmed, the add-on price should be their real cost plus a margin that at minimum covers your Stripe fees, ideally a bit more.

## The exclusivity claim needs a decision

Several tiers' copy (Elite's "first-priority API routing," Regional Elite's "50-mile radial boundary... no other contractor... will receive leads in your zone") sell hard territorial exclusivity as the core differentiator against BuildAlert. The code to actually enforce that (`claim_territory_atomically`) exists but is never called anywhere — territory exclusivity is currently a promise, not a mechanism. Given exclusive, never-resold leads is the single biggest thing separating you from BuildAlert's shared-canvas model, I'd treat wiring this up as a priority alongside the pricing relaunch, not a someday item — selling exclusivity you can't yet enforce is the same category of problem as the two Elite claims above.

## What I need back from you

Real lead counts from the two admin reports (to size quotas properly), a decision on whether territory enforcement gets built before or alongside this pricing goes live, and your gut check on whether five tiers at these price points feels right for the market — then I'll turn this into the actual `PLANS`/`TIER_QUOTAS`/`TIER_MAX_RADIUS` code changes once you've marked this up.

# Multi-Vertical Lead Platform — Master Plan (v2, consolidated)

*Supersedes multi_vertical_expansion_research.md. Written after cross-checking the original research against 4 independent AI runs (Gemini Flash, GPT, Gemini Pro, Grok) — this is the clean, final version of the plan, not another layer on top.*

## 1. Verticals, in build order

1. **Tree work** — existing, live, working.
2. **HMO / change-of-use conversions** — build second. Reason: these applications cluster in ~40–50 dense urban councils (wherever an Article 4 direction is in force — most London boroughs, Manchester, Birmingham, Leeds, Bristol, Nottingham), vs ~380 councils needed for equivalent extension/loft volume. Smallest possible footprint to prove the generalized pipeline end-to-end. Customers are landlords/developers, not homeowners — more commercial, less price-sensitive.
3. **Extensions & loft conversions that exceed permitted development** — build third, as the volume play, once the pipeline is proven on vertical 2. Coverage is lower (~20–45% of real jobs, estimates vary), but leadability is higher: the homeowner usually hasn't hired anyone yet at application stage, unlike listed-building/new-build work where a specialist is often already appointed.
4. **Basement conversions/extensions** — candidate vertical 4. Near-100% planning coverage (basements almost always require full permission), very high ticket size (£100k+), and concentrated in a small number of wealthy London boroughs (Kensington & Chelsea, Westminster, Camden all have specific basement-development policies) — small council footprint like HMO, but a much higher-value customer.
5. **Short-term-let / Airbnb change-of-use** — candidate vertical 5. Several London boroughs now require planning permission for a dwelling switching to short-term letting, via the same Article 4 mechanism as HMO — rides the identical council list and portal set as vertical 2, near-zero extra scraping cost to add alongside it.
6. Everything else (listed buildings, demolition, agricultural, driveways, domestic solar) — deprioritized: either too small a pool, too specialist, or (solar) mostly permitted development and largely invisible to planning data. Commercial/ground-mounted solar and renewables are a real niche but a different customer (developers, not solo tradespeople) — parked, not ruled out, revisit once the core platform is proven.

## 2. Data model

- One `planning_applications` table, trade-agnostic.
- One `verticals` config: each vertical carries its keyword list, SIC codes (for contractor lookup), pricing, and freshness windows. Adding a vertical is a config row, never a code fork — if it needs scraper code changes, the generalization isn't finished.
- One `leads` table with a `vertical` column. A single application can match more than one vertical (an extension with tree removal) — sell it into every vertical it matches, not just the first.

## 3. Classification — tiered, nothing left unlabeled

1. Keyword match against the vertical's config (cheap, current approach, handles the majority).
2. Structured fields — application type code, use class, agent SIC code — for anything Tier 1 misses.
3. Cheap LLM call on description text, for the remainder.
4. Manual review queue for anything that fails all three — visible, never silently dropped.

## 4. The lead product — GDPR-safe by design, not by patch

Adopt what the most sophisticated existing competitors (PlanLeads, PlanWatch) already do: **never store or sell the applicant's name.** Sell address + project type + council reference + link, addressed to "The Owner/Occupier."

Why this matters more than it sounds: the real ICO risk in this business isn't "public data used commercially" — it's a homeowner getting six letters addressed to them *by name* from six different trades off one filing. The same six letters addressed to "The Occupier" is a much smaller intrusion and a much weaker complaint. This is the direct fix for the multi-trade risk that scales with every vertical you add — build it this way from vertical 2 onward, not as a later cleanup.

Marketing channel: postal only to homeowners (Legitimate Interests can support this; PECR blocks unsolicited email/SMS to individuals off public register data). Never email/text a homeowner sourced this way.

## 5. Pricing

Current per-lead range (£9–£75) stays for exclusive/high-freshness leads. Add a **flat territory subscription tier (roughly £35–£149/month)** alongside it — this is the dominant, proven model for outbound planning leads across nearly every competitor found (PlanLeads, District Contracts, PlanWatch, Planning Signal), because realized cost-per-lead for the buyer lands around £1–£5, which is what outbound (unrequested) leads can actually bear versus inbound Bark/Checkatrade leads at £15–£40+.

## 6. Maximizing what the data earns (nothing left unused)

- Multi-vertical resale: sell one matching application into every vertical it qualifies for.
- Cold-lead resale: once a lead ages past its fresh-pricing window, resell it cheaper as a bulk pack instead of discarding it.
- Aggregate market intelligence: anonymized regional/trade volume trends sold to suppliers/insurers/trade bodies — aggregate only, no personal data.
- Paid contractor directory placement via `marketplace_engine`'s two-sided model, on top of per-lead/subscription sales.
- Enrichment upsell: EPC rating, council tax band, property age bundled onto a lead for a higher-tier price.
- Raw API/feed tier for larger buyers who'd rather integrate than buy leads one at a time.

## 7. Contractor-finder

Generalize `bulk_contractor_extractor.py`'s Companies House lookup the same way — SIC codes live in the `verticals` config, one per vertical. Reuse the existing unused `entity_graph` tool for deduping/matching contractors once pulling from multiple trades/sources.

## 8. Build order (own checklist, to avoid repeating today's wasted cycles)

1. ✅ DONE Sep 2 — Generalize the data model into the `verticals` config — small, safe change to code that already works.
2. ✅ DONE Sep 2 — Add HMO/change-of-use as the first new vertical, config-only. Wired into all 4 live scan call sites, keyword list sanity-checked against real live PlanIt data (Nottingham/Leicester: 2/2 real positives caught, 0/39 false positives), plus a pre-ship adversarial review caught and fixed a crash-and-rollback risk and an HMO_GOLD precision bug (bare "sui generis"/"article 4 direction"). Council-list build-out (the actual ~40-50 target councils, §9a) not started yet — the pipeline proves out on whatever HMO applications the existing tree-focused scan sources happen to surface; a dedicated HMO council list/scraper is separate future work, and Gemini's first-pass research on that list has verified errors (see TASKS.md) that need re-checking before it's built.
3. ✅ DONE Sep 2 — Build the GDPR-safe lead format (§4) into this vertical from day one, not retrofitted later. `VERTICALS["hmo"]["capture_identity"] = False`, enforced inside `_insert_lead` itself so no applicant/agent identity can ever reach an HMO row regardless of call site. Note: this is risk reduction, not a claimed GDPR exemption — see TASKS.md for why the "the Occupier" framing from Gemini's LIA draft needs solicitor confirmation before being relied on, and the plan's own §9a note that this is already the more conservative, correct default.
4. Add the tiered classifier (§3).
5. Generalize the contractor-finder (§7) for HMO.
6. Add extensions/loft as vertical 3, same config pattern, once HMO is proven live.
7. Reuse `marketplace_engine` (already built, unused) as the real lead-access/subscription layer — one matching engine, one `MatchRuleSet` per vertical — instead of extending TreeKey's bespoke tables further.
8. Skip `escrow_engine` — wrong shape for a lead-sale model, don't force it in.
9. Rebrand and combine into one site last, once two verticals are proven — a name and landing page are cheap to redo, the data pipeline is not.
10. Testing discipline throughout, not after: live-verify any new scraper/portal target in the browser before shipping; ship a fast single-endpoint test (like `/test-mesh-council`) for every new integration point before wiring it into the full pipeline; grow the test suite alongside each change; land infra changes (verticals config, classifier, marketplace wiring) as separate deploys from scraper fixes so a break is easy to trace.

## 8a. Other council-data monetization angles — researched live

You asked specifically about lapsed/expired planning permission leads and unclaimed council grants, plus "any other" way to squeeze value from council data. Researched all of these live rather than guessing.

**Expired/lapsed planning permission — real, and cheap to add.** Standard rule: full planning permission must be commenced within 3 years of grant or it lapses entirely, with no automatic extension — the owner has to reapply from scratch. I found no existing business selling "lapsed permission" leads to developers specifically (the search results are all "how to sell my house with lapsed permission" homeowner guides, not a data-broker competitor) — this could be a genuine gap, or it could be under-served because nobody's found a buyer who pays for it yet; treat it as unproven demand, not a validated one. The good part: this needs **no new scraping**. It's a derived product from data you're already collecting — once you're storing planning history over time, "approved 3+ years ago, no commencement/building-control record since" is a query against your own database, not a new council target. Sell it to two different buyers: developers/land-assemblers looking for stalled sites, and planning consultants who pitch "renew this before it's gone entirely" to the original owner. Add as a low-cost resale product once you have enough history accumulated (needs time, not engineering — this doesn't work until you've been scraping a given council for 3+ years, so it's a future-value play, not a near-term one).

**Unclaimed council grants — the version you heard about isn't buildable, but there's a legitimate adjacent idea.** Councils don't publish who's eligible for a grant but hasn't claimed it — that's sensitive personal/financial data about identifiable individuals, exactly the category that can't be scraped or sold under UK GDPR, and it isn't published anywhere for that reason. What *can* be built legitimately is a "grant finder/matching" service — a catalog of currently-open public grant schemes, marketed to homeowners or small businesses who might qualify. But that market is already saturated: the ECO4/Boiler Upgrade Scheme space alone has a dozen well-ranked competitor sites, and the small-business side has well-funded platforms already dominant (Swoop Funding, FundBiz, gov.uk's own official "Find a Grant" service). It also doesn't use your council-scraping capability at all — it's a different, generic lead-gen business. Recommend against this one; it's exactly the "don't force a tool in just because it exists" trap from §8.

**Two genuinely new data sources worth adding:**

- **Empty commercial property / business rates registers.** Some councils already publish this as an open-data CSV, no scraping required at all — confirmed on Barnet's own open data portal (a full business rates register plus a separate empty-properties file, published under the same open licence as planning data). This is a real, much cheaper-to-acquire data source for a different customer: commercial landlords, refurbishment/shopfitting contractors, and investors looking for undervalued vacant units. Worth checking how many other councils publish the same thing before committing — Barnet is one confirmed example, not a proven national pattern yet.
- **Planning enforcement notices.** A genuine public per-council register (confirmed on Southampton, Buckinghamshire, Mid Devon, and others) tracking unauthorised development action — separate from ordinary planning applications. Small addressable market, but a real one: sell to planning consultants who specialise in regularising breaches retrospectively, and to surveyors/conveyancers doing due diligence before a purchase. Don't build this first — it's a small niche, worth a later addition once the core two verticals are proven, not a priority now.

**One infrastructure finding that matters more than either idea above: `planning.data.gov.uk`.** This is a government-run national aggregator (Ministry of Housing, Communities and Local Government's "Open Digital Planning" initiative) covering structured planning datasets — Article 4 directions, conservation areas, tree preservation orders, listed buildings, brownfield land — with a real bulk-download and API, not just a blog list. It does **not** cover day-to-day application casework (that still lives on each council's own Idox/Northgate system, so it doesn't replace the scraper), but it's a strong candidate to **replace the AI-research-derived Article 4 HMO council list with an authoritative government source**, and it's a free enrichment layer for the existing tree vertical — flagging whether a given property already sits inside a TPO zone or conservation area without an extra scrape. Worth checking before building the HMO council list into the `verticals` config, since it may be more current and more reliable than the Planning Geek/AI-cross-checked list currently in §9a.

## 8b. My own ideas — not prompted, using what's already built

You asked me to come up with ideas myself, not just react to yours. These specifically reuse tools/knowledge already in hand rather than starting fresh:

**Site-assembly radius alerts for developers.** Same scraper, a different alert config: instead of selling one lead once, let a developer "watch" a postcode/radius and get pinged on every new application there — useful for anyone quietly assembling adjacent plots. Near-zero extra engineering (it's a saved filter + a notification, not a new scrape target), and it's a higher-paying customer than a solo tradesperson.

**Planning-history "risk report" for conveyancing — checked, already taken.** I looked at whether a per-property planning report sold through conveyancing solicitors was a gap. It isn't: Groundsure Planning and Landmark Planning are already established, accredited products sold through major search providers (Severn Trent Searches, Geodesys) into every UK house purchase. This is a large, real, recurring market, but it's occupied by entrenched, accredited incumbents with search-industry relationships a solo operator can't easily match. Not recommending it as a target — but it's a useful confirmation that planning data has real value well beyond tradesperson leads, which is the more useful takeaway.

**Contractor growth-signal intelligence.** The unused `entity_graph` tool is built exactly for this: track which agents/contractors are appearing on a rising number of live applications over time, and you've detected which local firms are actually growing right now. Sell that signal — not a lead, a target list — to building-materials suppliers, plant-hire companies, and insurance brokers who want to pitch growing firms before their competitors do. Different customer entirely from anyone else in this plan, and it's built on data you're capturing already.

**The bigger realization: several "future build" items in this plan may already be built.** Going back through CRAWLER PROJECT's unused engines against what this plan calls for: `scoring_engine` could power a "prioritized/premium lead" tier without new work; `reporting_engine` could directly generate the aggregate market-intelligence product in §6; `semantic_engine` is a plausible fit for the Tier-3 LLM step in §3's classifier; `outreach_engine` could power a "we'll print and post the letter for you" upsell matching TradeMailer/PlanPost's model; `notification_engine` + `workflow_engine` together could power a real-time alert tier above today's batch scans. None of this is confirmed working code yet — it needs an actual read-through of each engine before relying on it — but if even half of these hold up, a meaningful chunk of this plan is wiring, not building.

## 8c. Delegating to Gemini (Google Pro, ~3 weeks left)

Good fit — hand these to Gemini and bring back the output, same as the research rounds already done:
- Wide, tedious enumeration: e.g. confirming portal software (Idox/Northgate/bespoke) across the full ~100+ Article 4 council list from §9a, one by one — exactly the kind of long lookup task that's slow for me to do council-by-council but is a single batch job for it.
- Deep-Research-style broad sweeps, the way the two research rounds already in this doc were done.
- First-draft copy: outreach letter/email templates for contractor recruitment, brand name/tagline batches, landing page copy drafts — all reviewed before use, not shipped as-is.
- Summarizing any long document you paste in (legal drafts, long competitor pages) where the length itself is the obstacle.

Keep with me, not delegated: anything that ships to production (a scraper fix still needs a live-browser verification against the real target before shipping — that discipline doesn't transfer), legal/compliance conclusions (still cross-checked against primary sources, not taken from one model), and the actual pricing/architecture decisions, which are ours to reason through together.

## 9a. Round-2 research results — the gaps are now filled

Ran the round-2 prompt through 4 AIs. Converging findings:

**Council list is bigger than assumed, but the first-50 cohort is clear.** There are ~75–190 authorities with some form of HMO Article 4 direction nationally, not 40–50 — but all four sources converge on the same practical first cohort: essentially all of London (Barking & Dagenham, Barnet, Bexley, Brent, Bromley, Camden, Croydon, Ealing, Enfield, Greenwich, Hackney, Hammersmith & Fulham, Haringey, Harrow, Havering, Hillingdon, Hounslow, Islington, Kensington & Chelsea, Kingston, Lambeth, Lewisham, Merton, Newham, Redbridge, Richmond, Southwark, Sutton, Tower Hamlets, Waltham Forest, Wandsworth, Westminster) plus the major university/metro cities (Birmingham, Manchester, Leeds, Sheffield, Nottingham, Leicester, Bristol, Brighton & Hove, Southampton, Portsmouth, Oxford, Cambridge, York, Newcastle, Exeter, Plymouth, Bath, Bournemouth/BCP, Reading, Coventry, Cardiff, Swansea, Durham). Use this as the literal first `verticals` config council list for HMO — it's ready to hardcode. Re-verify against Planning Geek's live register before building, since directions keep being added (Harrow, Oldham, Bury, Durham, Ribble Valley all went live in 2026).

**Portal split confirms the build plan.** Idox covers roughly 65–75% of the target list (existing scraper mostly reusable, config-only per council). Northgate/NEC is the main second system (~15–20%) — Birmingham is Northgate, confirmed by multiple sources, plus some London boroughs (Islington, Southwark per one source, though this conflicts with another source's claim of Southwark on Idox — verify per-council before build). Liverpool and Reading each run their own bespoke systems, not Idox or Northgate — treat as edge cases, skip in the first pass. Building Idox + one Northgate adapter covers 85%+ of the target list.

**Response-rate reality check — recalibrate expectations, not vendor claims.** Every source agrees the 10–38% conversion numbers on competitor marketing pages are vendor-selected, not independent. The realistic range across all four: roughly 1–5% response/enquiry rate on planning-derived postal mail (BuildAlert's own admitted baseline is 1–2%, "4–8% achievable with tight targeting"), and of those who respond, 10–30% become a won job. Build the financial model on 1–3% response as the planning number, not on any vendor's headline figure — and track the funnel stages (delivered → response → quote → win) rather than treating "response" as "sale," per §8 build order already.

**GDPR: sharper, and one important correction.** All sources agree address-only does NOT make the data anonymous — the ICO's test is whether the address can be linked to an identifiable person (e.g. via Land Registry), not whether a name field exists in your database. The correct read: this is real, defensible risk reduction (avoids the specific harm of a homeowner being named and spammed across 6 trades, avoids some data-inaccuracy risk) but it is not a GDPR exemption — a lawful basis, an LIA, and a privacy notice are still required. One source claimed "the Occupier" fully exempts the mailing from marketing rules; treat that as the weaker, less-cited claim and build to the more conservative standard the other three converge on: full compliance (LIA, transparency, retention, rights handling) built in from day one, exactly as §4 above already says — this research doesn't loosen that, it confirms it's the right call.

**SIC codes — ready to seed the vertical config.** Converged list: `41202` (construction of domestic buildings, core), `41100` (development of building projects), `43999`/`43390` (specialised/finishing catch-all), `43210` (electrical, for mandatory HMO EICR certs), `43220` (plumbing/heating), `68320` (property management/HMO compliance firms), `68209` (letting/operating real estate). Use SIC as a first filter only — one source specifically flags there's no clean "HMO compliance" SIC code, so combine SIC with keyword matching on company name/description for the compliance-firm category.

**Name candidates — a starting shortlist, not final.** One AI proactively generated and spot-checked names: **PermitLeads**, **ApprovalRadar**, and **ConsentRadar** came back clear of exact Companies House matches and available domains; **PlanScout** is clear in the UK but used elsewhere in finance (weaker pick); **SiteLens is already an active, registered UK competitor doing this exact business** — rule it out entirely, don't reuse or reference it. Treat all of these as a first pass only — run a real Companies House + domain check before committing to any of them.

## 9. Open questions for further research (see prompt below)

- Real, current list of UK councils with an Article 4 HMO direction in force (to pick the actual first ~40–50 to scrape).
- Whether address-only/"Occupier"-addressed marketing (no applicant name at all) meaningfully reduces UK GDPR obligations, or whether it still counts as processing personal data because the address itself can identify a person.
- Real-world response/conversion rates for postal outbound leads vs any other legally viable channel, for UK small tradespeople specifically.
- Concrete Companies House SIC codes for HMO-conversion contractors/builders, to seed the vertical 2 contractor-finder config.
- Which portal software (Idox vs Northgate vs custom) the target ~40–50 HMO-heavy councils actually run, to know how much of the existing Idox-specific scraper is reusable untouched vs needs a second scraper type.

---

# Prompt for another AI — round 2 (gap-filling, not re-asking what's answered)

```
I'm building UK planning-application-derived lead generation for small/solo contractors,
expanding from tree surgery into HMO/change-of-use conversions as the second vertical
(chosen because these applications cluster in a small number of dense urban councils
with Article 4 HMO directions, giving much lower scraping/maintenance burden than a
nationwide vertical like extensions). I've already researched trade rankings, competitor
pricing, and general UK GDPR/ICO exposure — I don't need those repeated. I need answers
to these specific remaining gaps:

1. Produce the most complete and CURRENT list you can of UK local planning authorities
   that have an Article 4 direction removing permitted development rights for small HMO
   (C3 to C4) conversions, as of 2026. Cite sources for each. I want to prioritize
   scraping these ~40-50 councils first.

2. Under UK GDPR, if a lead product never stores or displays an individual's name at
   all -- only the property address, application reference, and project type, and any
   direct mail is addressed to "The Owner/Occupier" rather than a named person -- does
   this meaningfully reduce the controller's obligations (versus a name-included
   product), or does an address alone still count as personal data triggering the same
   duties because it's linkable to an identifiable person? Cite ICO guidance directly if
   it exists.

3. Find any real-world evidence (case studies, contractor forum discussions, reviews of
   PlanLeads/PlanWatch/District Contracts/similar services) on actual response or
   conversion rates UK small tradespeople get from planning-data-sourced postal outreach,
   versus other lead channels. I want realistic expectations, not vendor marketing claims.

4. List the specific UK Companies House SIC codes most relevant to HMO conversion
   contractors, builders who specialize in change-of-use conversions, and licensed HMO
   management/compliance firms (fire safety, electrical certification for HMOs) --
   I need this to seed a contractor-matching config.

5. For the same ~40-50 councils identified in (1), identify which planning portal
   software each one runs (Idox Public Access, Northgate, a bespoke system, or other) --
   I need to know how much of an existing Idox-specific scraper can be reused unmodified
   versus how many councils will need a different scraper entirely.

6. Do a UK company name availability check (Companies House name search plus general
   domain availability) for these 5 candidate rebrand names for a multi-vertical
   planning-lead brand that doesn't box into "tree": [PLACEHOLDER -- insert 5 candidate
   names before sending]. Flag any that are already taken or confusingly similar to an
   existing UK company.

Cite sources for every factual claim and clearly flag anything you're estimating rather
than citing directly.
```

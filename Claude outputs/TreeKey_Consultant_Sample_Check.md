# TreeKey — Consultant/Survey Category Sample Check

*Attempted 18 Sep 2026, working from the live public marketplace only (no database credentials were available in this session, so this is not the DB-query version of the check described in the audit — see limitations at the end).*

## What this was testing

The audit flagged a risk: some "consultant/survey" work (BS5837 tree surveys, discharge of tree-protection planning conditions) may already be complete by the time TreeKey scrapes and sells the application — meaning the lead could be selling work that no longer exists. Goal: pull a real sample, check each against the actual council record, and classify.

## Step 1 — What's actually live right now

Pulled the full current marketplace inventory (40 leads, the entire live pool as of today — there is no larger hidden set to sample from; this is it). TreeKey doesn't have a distinct "Consultant" category in the live product — the categories are Crown Work, Felling & Removal, Storm & Emergency, Hedge Work, and General/Other. The "survey/discharge-of-conditions" pattern shows up as a signal *within* General/Other, not as its own bucket.

Of the 40 live leads, 4 carry a relevant signal:

| # | Council | Ref | Description | Signal |
|---|---|---|---|---|
| 17 | Huntingdonshire | 80/00007/TPO | Beech/Pine work | TPO reference — fell/reduction work itself, not a survey question |
| **18** | **Peterborough** | **23/01191/HHFUL** | **Discharge of conditions C5 (tree protection scheme), C6 (root protection area), C7 (foundation details)** | **Direct match — this is exactly the "was the survey already done" pattern** |
| **24** | **Sheffield** | (ref not shown pre-purchase) | Approval of CEMP/tree protection measures, condition 9 | **Direct match — same pattern** |
| 29 | North Yorkshire | TPO 2007/12 | Ash felling | TPO reference — fell work itself, not a survey question |

**First finding, on its own:** out of the entire current live pool, only 2 of 40 leads (5%) are the pattern the audit was worried about. That's a much smaller share than "a whole pricing category's worth" — useful evidence on its own that this isn't a large slice of current volume, whatever the per-lead verdict turns out to be.

## Step 2 — Checking #18 and #24 against the real council record

This is where the check stalled. I tried to reach both councils' public planning portals directly:

- Peterborough (`planning.agileapplications.co.uk/peterborough`) — blocked with a 403 on every request.
- Sheffield (`planningapps.sheffield.gov.uk`) — blocked by a robots/connection failure on every request.

Both portals are the same type of system TreeKey's own scanner is already built to handle (cookies, session state, sometimes JS rendering) — that's exactly why TreeKey has dedicated scraper code (`scanners.py`, `net_utils.py`, `mesh_scrapers.py`) instead of using generic fetches. A generic web-fetch tool can't reliably get past that; TreeKey's own scanner, or a human clicking through the portal directly, can.

**This step is not done.** It needs one of:
1. You (or anyone) manually opening those two references on the council sites and checking the documents tab for an already-submitted tree survey/protection plan — 5 minutes each, the most reliable option.
2. Running TreeKey's own scanner code against these two references specifically, in an environment with the right dependencies — more setup than it's worth for 2 records.

## Step 3 — What this means for the original plan

The original plan called for a 20-lead sample. Given the current live pool only contains 2 clear matches, a 20-lead sample isn't available right now from live inventory alone — it would need to pull from historical/sold leads too (which does need DB access) to get a meaningful sample size. Worth deciding whether 2 real, manually-checked examples is enough evidence to act on, or whether it's worth granting DB access next time so a proper historical sample can be pulled and queried directly instead of scraped one page at a time.

## Limitations of this pass

- No database access — this used only the public marketplace page, which is today's live snapshot, not history.
- Council portal checks are blocked from this environment specifically; not evidence either way on the underlying survey question for #18 or #24 yet.
- Didn't attempt outside-consultant feedback (step in the original plan) — no sample verdicts exist yet to show anyone.

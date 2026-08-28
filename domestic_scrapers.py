"""
domestic_scrapers.py — DISABLED (Aug 2026)

The previous version of this module scraped homeowner-facing sources (Gumtree,
Freeads, Facebook/Nextdoor-via-DuckDuckGo, Reddit, local press, residents'
associations, parish councils, FixMyStreet) to harvest individuals' public posts
about needing tree work, then sold that as a "lead" to a third-party contractor —
without the homeowner's knowledge that TreeKey existed or that their post would be
resold. On review this carries real UK GDPR/PECR exposure that doesn't shrink with
scale (no documented lawful-basis balancing test, and any resulting contact by a
contractor is direct marketing to an individual without their consent) — it was
deliberately removed rather than left running. Nick's decision, Aug 28 2026.

── FUTURE REPLACEMENT: "Domestic Listing Section" ──────────────────────────────
Planned direction instead of scraping: a homeowner-facing page/form where people
who want tree work done submit their own job directly (name, postcode, job
description — the same shape of data, but the person knowingly and consensually
gives it to TreeKey, the way Checkatrade/MyBuilder/Rated People operate). That
sidesteps the legitimate-interest/PECR problem entirely because it's the
homeowner's own request, not a scraped third-party post.

Not built yet — this file is a placeholder. When it's picked up, it will likely
need: a public route (e.g. `/list-your-job`) + form, a `domestic_listings` table,
basic spam/abuse protection (reuse `_check_rate_limit` from main.py), and routing
the submitted job into the same `leads` table / `dispatch_lead_alerts` pipeline
that council-sourced leads already use, so no downstream code needs to change.

The function below is kept as a no-op so the existing call sites in main.py
(/scan-domestic-jobs, /trigger-domestic-scan, /api/run-domestic-scan-now) don't
break — they'll just report 0 new leads until the listing section replaces this.
"""

import logging

logger = logging.getLogger("vector-data-labs")


def ingest_and_route_domestic_leads() -> int:
    """
    Disabled. See module docstring — the scraper approach was removed for GDPR/PECR
    risk; this will be replaced by a consensual homeowner-submitted listing section.
    """
    logger.info("[Domestic] Scraper disabled (removed Aug 2026) — awaiting the opt-in 'Domestic Listing Section' replacement. 0 leads.")
    return 0

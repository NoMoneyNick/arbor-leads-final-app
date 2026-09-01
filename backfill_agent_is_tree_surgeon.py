"""
One-off backfill for the Aug 31 2026 agent_is_tree_surgeon bug.

Background: scanners.py's PlanIt confirm loop was fixed on Aug 31 2026 so
that has_agent resolving no longer permanently blocks the (free, zero-
network-cost) agent_is_tree_surgeon classification from ever running. But
that fix only re-classifies a lead when its reference is encountered AGAIN
in a live PlanIt scan (see the `if has_agent is None and item.get(
"source_url")` branch in scanners.py) -- and PlanIt only returns
applications for a rolling window (documented elsewhere in scanners.py as
up to ~45 days). Leads already sitting in the DB with has_agent=True /
agent_is_tree_surgeon=NULL may have aged out of that window already, in
which case the live-scan fix will never reach them and they'd stay
permanently NULL (and therefore permanently excluded from the marketplace
by get_marketplace_leads_with_freshness's NULL-treated-as-excluded filter).

This script closes that gap directly: it re-runs the exact same,
already-tested, zero-network-cost classification
(mesh_scrapers.classify_agent_as_tree_surgeon) against every existing
has_agent=True / agent_is_tree_surgeon=NULL row, using the agent_name /
agent_company already on file. Safe to re-run any time -- it only ever
touches rows still sitting at NULL, and rows that classify as
still-indeterminate (bare personal name, no recognizable keyword) are
correctly left NULL rather than being force-set to a guess.

Run this once against production (e.g. from a Render shell, or locally
with the same DATABASE_URL the app uses) after confirming the scanners.py
fix itself has been committed and deployed.
"""
import logging

import database
import mesh_scrapers

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s:%(name)s:%(message)s")
logger = logging.getLogger("backfill_agent_is_tree_surgeon")


def run():
    conn = database.get_db_conn()
    cur = conn.cursor()

    cur.execute(
        "SELECT reference, agent_name, agent_company FROM leads "
        "WHERE has_agent = TRUE AND agent_is_tree_surgeon IS NULL"
    )
    rows = cur.fetchall()
    logger.info(f"Found {len(rows)} leads with has_agent=True and agent_is_tree_surgeon still NULL.")

    resolved_true = 0
    resolved_false = 0
    still_unknown = 0

    for reference, agent_name, agent_company in rows:
        classification = mesh_scrapers.classify_agent_as_tree_surgeon(agent_name, agent_company)
        if classification is None:
            still_unknown += 1
            continue
        cur.execute(
            "UPDATE leads SET agent_is_tree_surgeon = %s WHERE reference = %s",
            (classification, reference),
        )
        if classification:
            resolved_true += 1
        else:
            resolved_false += 1

    conn.commit()
    cur.close()
    conn.close()

    logger.info(
        f"Backfill complete. Resolved True (genuine tree surgeon): {resolved_true} | "
        f"Resolved False (not a tree surgeon): {resolved_false} | "
        f"Still indeterminate, left NULL: {still_unknown}"
    )


if __name__ == "__main__":
    run()

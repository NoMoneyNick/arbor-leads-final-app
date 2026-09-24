"""
scraper_resilience.py -- 2026-09-22 review: incident/repair-attempt
tracking and scan-checkpoint/backfill bookkeeping for the council scraper
subsystem (mesh_scrapers.py, net_utils.py).

WHERE THIS CAME FROM: Claude's 2026-09-22 review found that the existing
detection/alerting infrastructure (database.py's system_warnings table,
notifications.send_daily_warning_digest) only ever detects a problem and
asks a human to check manually -- it has no memory of whether a fix was
ever tried for a recurring issue, or whether that fix actually worked, and
no way to recover leads missed while a source was broken. Nick asked
ChatGPT/Astra for architecture advice on exactly this gap; that review
(relayed into this repo the same day -- see ERROR_LOG.md's entry for the
full advice) recommended, among other things, an incident/repair-attempt
lifecycle and per-council scan checkpointing with backfill. This module is
that -- deliberately scoped to tracking/bookkeeping only, per that review's
own "keep the first change small... don't rewrite the scraping stack"
instruction.

WHAT THIS DELIBERATELY DOES NOT DO: this does not implement response
classification, fallback parser strategies, or LLM-extraction quarantine
inside the actual scraper. Those all require reading and editing
mesh_scrapers.py, which is not present anywhere in this repo (see
ERROR_LOG.md's own entry on that). This module is the tracking layer those
future changes will call into once that file is available -- schema and
functions only, NOT YET WIRED into any real scrape. Nothing in this file
runs automatically yet; main.py does not call any of it. Wiring it in is
future work, listed honestly in ERROR_LOG.md rather than claimed as done.

DESIGN NOTES:
  - Every function here is self-contained (opens its own DB connection,
    commits, closes) rather than taking a shared cursor -- these aren't
    part of a payment/sale transaction the way fulfilment.py/funding.py's
    functions are, so there's no correctness reason to share one, and
    matches database.py's own log_system_warning/get_recent_warnings
    style for the same kind of cross-cutting operational bookkeeping.
  - `database` is bound once at module-import time (not per-call) --
    see address_release.py's own comment for the full explanation of why:
    a per-call `import database` inside a function can silently resolve to
    a stale sys.modules["database"] object under this test suite's full
    `unittest discover` run, because a couple of test files legitimately
    reassign sys.modules["database"] for their own isolation. Binding once,
    early, avoids repeating that exact bug in a new module.
  - Every function fails safe: returns None/[]/False and logs on any DB
    error rather than raising, so a broken tracking call can never itself
    become an incident, matching every other health/warning function in
    this codebase.
"""
from __future__ import annotations

import logging
from typing import Optional

import database

logger = logging.getLogger("vector-data-labs")

INCIDENT_STATUSES = ("open", "repair_attempted", "verifying", "resolved")
REPAIR_OUTCOMES = ("pending", "success", "failure")
# 2026-09-22 second review (Astra, relayed by Nick): outcomes for a
# verification RUN, distinct from REPAIR_OUTCOMES above (which describes
# whether a repair attempt itself succeeded/failed at build time, not
# whether a subsequent live check confirmed it). See
# record_verification_result's own docstring.
VERIFICATION_OUTCOMES = ("success", "failure")


def init_scraper_resilience_schema(cur) -> None:
    cur.execute("""
        CREATE TABLE IF NOT EXISTS source_incident (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            council TEXT NOT NULL,
            platform TEXT NOT NULL,
            failure_type TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'open',
            note TEXT,
            first_seen TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            last_seen TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            last_verified_success TIMESTAMPTZ,
            -- 2026-09-22 second review: the outcome/reason of the most
            -- recent verification RUN (see record_verification_result),
            -- kept separate from last_verified_success (which only ever
            -- records a *successful* verification's timestamp) so a
            -- failed verification is visible too, instead of the incident
            -- just silently sitting in 'verifying' with no record of why
            -- it didn't get resolved.
            last_verification_outcome TEXT,
            last_verification_failure_reason TEXT,
            -- 2026-09-22 second review: how many times this exact row has
            -- been reopened after a prior resolution -- a failure_type
            -- that keeps coming back after being marked "resolved" is a
            -- different (worse) signal than one seen for the first time,
            -- and this makes that visible without having to reconstruct
            -- it from resolved_at history.
            reopen_count INT NOT NULL DEFAULT 0,
            resolved_by TEXT,
            resolved_at TIMESTAMPTZ,
            resolution_note TEXT
        );
        -- At most one non-resolved incident per council+failure_type at a
        -- time -- open_or_touch_incident below relies on this to decide
        -- "touch the existing one" vs "open a new one" without a race.
        CREATE UNIQUE INDEX IF NOT EXISTS idx_source_incident_open_unique
            ON source_incident(council, failure_type)
            WHERE status != 'resolved';
        CREATE INDEX IF NOT EXISTS idx_source_incident_status ON source_incident(status);

        CREATE TABLE IF NOT EXISTS repair_attempt (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            incident_id UUID NOT NULL REFERENCES source_incident(id),
            parser_version TEXT,
            action TEXT NOT NULL,
            outcome TEXT NOT NULL DEFAULT 'pending',
            evidence_reference TEXT,
            started_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            completed_at TIMESTAMPTZ
        );
        CREATE INDEX IF NOT EXISTS idx_repair_attempt_incident ON repair_attempt(incident_id);

        -- Advanced only after a FULLY successful pass (see
        -- advance_council_scan_checkpoint's own docstring) -- this is what
        -- a future backfill uses to know how far back it needs to re-scan
        -- after a source comes back from an outage.
        --
        -- 2026-09-22 second review (Astra): a council can run more than
        -- one distinct search definition (e.g. separate tree-related vs.
        -- demolition-related searches against the same Idox instance),
        -- each on its own schedule/window -- a single checkpoint keyed by
        -- council alone would let one search definition's progress
        -- silently overwrite another's. search_definition is now part of
        -- the key; a single default value keeps this working for councils
        -- that only run one search until that's actually needed.
        CREATE TABLE IF NOT EXISTS council_scan_checkpoint (
            council TEXT NOT NULL,
            search_definition TEXT NOT NULL DEFAULT 'default',
            platform TEXT NOT NULL,
            last_completed_window_start TIMESTAMPTZ,
            last_completed_window_end TIMESTAMPTZ,
            last_completed_at TIMESTAMPTZ,
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            PRIMARY KEY (council, search_definition)
        );

        -- Per-pass counters so "zero new leads today" can be told apart
        -- from "the source is actually broken" -- see
        -- record_council_scan_pass_metrics's own docstring.
        CREATE TABLE IF NOT EXISTS council_scan_pass_metrics (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            council TEXT NOT NULL,
            platform TEXT NOT NULL,
            pages_fetched INT NOT NULL DEFAULT 0,
            links_discovered INT NOT NULL DEFAULT 0,
            records_parsed INT NOT NULL DEFAULT 0,
            records_rejected_validation INT NOT NULL DEFAULT 0,
            records_excluded_business_filters INT NOT NULL DEFAULT 0,
            new_sellable_leads INT NOT NULL DEFAULT 0,
            recorded_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        );
        CREATE INDEX IF NOT EXISTS idx_scan_pass_metrics_council ON council_scan_pass_metrics(council, recorded_at DESC);
    """)


# ---------------------------------------------------------------------------
# Incident lifecycle: open -> repair_attempted -> verifying -> resolved,
# reopened back to 'open' if the same failure_type recurs after 'resolved'.
# A proposed fix does NOT resolve an incident by itself -- only
# resolve_incident (called after a verified successful run) does.
# ---------------------------------------------------------------------------

def open_or_touch_incident(*, council: str, platform: str, failure_type: str, note: Optional[str] = None) -> Optional[str]:
    """Idempotent open: if a non-resolved incident already exists for this
    exact (council, failure_type), just bumps its last_seen and returns its
    id -- never duplicates. If the most recent incident for this pair was
    already 'resolved' and the same failure_type has come back, REOPENS it
    (status back to 'open', last_seen bumped) rather than creating a fresh
    row, so its repair_attempt history isn't lost -- this is what "reopen
    it when the same failure returns; don't suppress it merely because it
    was previously seen" (the review's own words) means in practice.
    Returns the incident id, or None on any DB error (fails safe -- a
    broken tracking call must never block or crash a real scrape).

    2026-09-22 second review correction (Astra, relayed by Nick): the
    original version of this function did a plain SELECT to decide "no row
    exists" and then, in that branch, a plain INSERT. Two concurrent
    callers for the SAME (council, failure_type) could both run that
    SELECT before either committed, both see "no row", and both attempt
    the INSERT -- the first would succeed, the second would violate
    idx_source_incident_open_unique (the partial unique index that exists
    specifically to prevent two non-resolved rows for the same pair) and
    raise, which the generic `except Exception` below would catch and turn
    into a silently-lost incident-open (returns None, logs an error, but
    the caller has no way to know a *different* row already tracks this
    exact failure). Fixed below: that specific branch now uses an atomic
    `INSERT ... ON CONFLICT ... DO UPDATE`, so Postgres itself resolves the
    race instead of two Python processes racing a SELECT-then-INSERT.

    The other race this function has -- two concurrent callers both seeing
    the SAME previously-'resolved' row and both trying to reopen it -- is
    NOT fixed by this pass. It's far rarer in practice (it requires the
    exact same already-resolved failure_type to reoccur on two overlapping
    scrape runs, not just any two concurrent opens) and the failure mode is
    much milder (a double UPDATE that both set the same result, not a
    thrown/lost write) -- recorded here honestly as an accepted, smaller
    remaining gap rather than silently claimed as fixed too."""
    if not database.SURL:
        return None
    try:
        conn = database.get_db_conn()
        cur = conn.cursor()
        try:
            cur.execute(
                "SELECT id, status FROM source_incident WHERE council = %s AND failure_type = %s "
                "ORDER BY last_seen DESC LIMIT 1;",
                (council, failure_type),
            )
            row = cur.fetchone()
            if row and row[1] != "resolved":
                cur.execute(
                    "UPDATE source_incident SET last_seen = NOW(), note = COALESCE(%s, note) WHERE id = %s;",
                    (note, row[0]),
                )
                conn.commit()
                return str(row[0])
            if row and row[1] == "resolved":
                cur.execute(
                    "UPDATE source_incident SET status = 'open', last_seen = NOW(), "
                    "note = COALESCE(%s, note), resolved_by = NULL, resolved_at = NULL, resolution_note = NULL, "
                    "reopen_count = reopen_count + 1 "
                    "WHERE id = %s RETURNING id;",
                    (note, row[0]),
                )
                conn.commit()
                logger.warning(f"[ScraperResilience] Re-opened incident {row[0]} for {council!r}/{failure_type!r} -- "
                                f"a previously-resolved failure has recurred.")
                return str(row[0])

            # No row at all (for either status) as of the SELECT above --
            # this is the branch with the race described above. ON CONFLICT
            # targets idx_source_incident_open_unique exactly (same columns,
            # same WHERE predicate), so if a concurrent caller's INSERT for
            # this same (council, failure_type) committed in the gap between
            # our SELECT and this INSERT, Postgres does the UPDATE instead
            # of raising -- both callers converge on the same single row.
            cur.execute(
                "INSERT INTO source_incident (council, platform, failure_type, note) "
                "VALUES (%s, %s, %s, %s) "
                "ON CONFLICT (council, failure_type) WHERE status != 'resolved' "
                "DO UPDATE SET last_seen = NOW(), "
                "note = COALESCE(EXCLUDED.note, source_incident.note) "
                "RETURNING id;",
                (council, platform, failure_type, note),
            )
            incident_id = cur.fetchone()[0]
            conn.commit()
            return str(incident_id)
        finally:
            cur.close()
            conn.close()
    except Exception as e:
        logger.error(f"[ScraperResilience] Error opening/touching incident for {council!r}/{failure_type!r}: {e}")
        return None


def record_repair_attempt(incident_id: str, *, action: str, parser_version: Optional[str] = None,
                           outcome: str = "pending", evidence_reference: Optional[str] = None) -> Optional[str]:
    """Logs one attempt to fix an incident. A repair attempt on its own does
    NOT resolve the incident (per the review: "a proposed patch does not
    resolve an incident") -- it only moves a still-'open' incident to
    'repair_attempted' so it's visible that someone is working on it.
    Resolving requires a separate, deliberate resolve_incident call after a
    verified successful run."""
    if not database.SURL or not incident_id:
        return None
    if outcome not in REPAIR_OUTCOMES:
        raise ValueError(f"Unknown repair outcome {outcome!r}, expected one of {REPAIR_OUTCOMES}")
    try:
        conn = database.get_db_conn()
        cur = conn.cursor()
        try:
            cur.execute(
                "INSERT INTO repair_attempt (incident_id, parser_version, action, outcome, evidence_reference, "
                "completed_at) VALUES (%s, %s, %s, %s, %s, CASE WHEN %s = 'pending' THEN NULL ELSE NOW() END) "
                "RETURNING id;",
                (incident_id, parser_version, action, outcome, evidence_reference, outcome),
            )
            attempt_id = cur.fetchone()[0]
            cur.execute(
                "UPDATE source_incident SET status = 'repair_attempted' WHERE id = %s AND status = 'open';",
                (incident_id,),
            )
            conn.commit()
            return str(attempt_id)
        finally:
            cur.close()
            conn.close()
    except Exception as e:
        logger.error(f"[ScraperResilience] Error recording repair attempt for incident {incident_id}: {e}")
        return None


def mark_incident_verifying(incident_id: str) -> bool:
    """Moves an incident to 'verifying' -- a repair has been attempted and
    a verification run is now in progress. Separate from 'resolved' on
    purpose: per the review, resolving requires a completed, successful,
    COMPLETENESS-checked run, not just "the fix was deployed"."""
    if not database.SURL or not incident_id:
        return False
    try:
        conn = database.get_db_conn()
        cur = conn.cursor()
        try:
            cur.execute(
                "UPDATE source_incident SET status = 'verifying' WHERE id = %s AND status = 'repair_attempted' "
                "RETURNING id;",
                (incident_id,),
            )
            row = cur.fetchone()
            conn.commit()
            return bool(row)
        finally:
            cur.close()
            conn.close()
    except Exception as e:
        logger.error(f"[ScraperResilience] Error marking incident {incident_id} as verifying: {e}")
        return False


def record_verification_result(incident_id: str, *, outcome: str, failure_reason: Optional[str] = None) -> bool:
    """Records the result of one verification run for an incident that's
    currently 'verifying' (see mark_incident_verifying). Added in the
    2026-09-22 second review (Astra, relayed by Nick), which pointed out
    the original lifecycle had no way to record a FAILED verification --
    an incident could sit in 'verifying' forever with no trace of the fact
    that a check was actually run and came back negative, forcing the next
    person to rediscover that from scratch.

    outcome='failure': moves the incident back to 'open' (never leaves it
    stranded in 'verifying') and stamps last_verification_failure_reason,
    while leaving its repair_attempt history exactly where it is --
    reopening isn't the same as forgetting what was already tried.

    outcome='success': does NOT itself move the incident to 'resolved' --
    that stays resolve_incident's job alone, on purpose (this module's
    existing "a proposed patch does not resolve an incident" discipline;
    see that function's own docstring). This only records that this one
    verification run passed, e.g. as evidence resolve_incident can later
    point to.

    Returns False (fails safe) on any DB error, an unknown incident id, or
    an unrecognised outcome value is a programming error and raises
    ValueError immediately, same convention as record_repair_attempt."""
    if not database.SURL or not incident_id:
        return False
    if outcome not in VERIFICATION_OUTCOMES:
        raise ValueError(f"Unknown verification outcome {outcome!r}, expected one of {VERIFICATION_OUTCOMES}")
    try:
        conn = database.get_db_conn()
        cur = conn.cursor()
        try:
            if outcome == "failure":
                cur.execute(
                    "UPDATE source_incident SET status = 'open', last_seen = NOW(), "
                    "last_verification_outcome = %s, last_verification_failure_reason = %s "
                    "WHERE id = %s RETURNING id;",
                    (outcome, failure_reason, incident_id),
                )
            else:
                cur.execute(
                    "UPDATE source_incident SET last_verification_outcome = %s, "
                    "last_verification_failure_reason = NULL WHERE id = %s RETURNING id;",
                    (outcome, incident_id),
                )
            row = cur.fetchone()
            conn.commit()
            return bool(row)
        finally:
            cur.close()
            conn.close()
    except Exception as e:
        logger.error(f"[ScraperResilience] Error recording verification result for incident {incident_id}: {e}")
        return False


def resolve_incident(incident_id: str, *, verified_by: str, note: str) -> bool:
    """The only function that marks an incident 'resolved' -- deliberately
    requires an explicit verified_by/note, same discipline as
    fulfilment.resolve_reconciliation_issue elsewhere in this codebase.
    Also stamps last_verified_success -- open_or_touch_incident reopens
    this exact row (not a fresh one) if the same failure_type recurs
    later, so this resolution's own history is never lost."""
    if not database.SURL or not incident_id:
        return False
    try:
        conn = database.get_db_conn()
        cur = conn.cursor()
        try:
            cur.execute(
                "UPDATE source_incident SET status = 'resolved', resolved_by = %s, resolved_at = NOW(), "
                "resolution_note = %s, last_verified_success = NOW() WHERE id = %s AND status != 'resolved' "
                "RETURNING id;",
                (verified_by, note, incident_id),
            )
            row = cur.fetchone()
            conn.commit()
            return bool(row)
        finally:
            cur.close()
            conn.close()
    except Exception as e:
        logger.error(f"[ScraperResilience] Error resolving incident {incident_id}: {e}")
        return False


def get_open_incidents() -> list:
    """Every incident not currently 'resolved', most recently seen first --
    the admin-visible worklist. Returns [] on any DB error (fail-safe, same
    pattern as every other read helper in this file)."""
    if not database.SURL:
        return []
    try:
        conn = database.get_db_conn()
        cur = conn.cursor()
        try:
            cur.execute(
                "SELECT id, council, platform, failure_type, status, note, first_seen, last_seen "
                "FROM source_incident WHERE status != 'resolved' ORDER BY last_seen DESC;"
            )
            cols = ["id", "council", "platform", "failure_type", "status", "note", "first_seen", "last_seen"]
            return [dict(zip(cols, (str(r[0]),) + r[1:])) for r in cur.fetchall()]
        finally:
            cur.close()
            conn.close()
    except Exception as e:
        logger.error(f"[ScraperResilience] Error fetching open incidents: {e}")
        return []


def get_repair_attempts(incident_id: str) -> list:
    """Every repair attempt logged for one incident, oldest first -- so the
    exact "previous fix on YYYY-MM-DD did not resolve this" pattern
    ERROR_LOG.md's format asks for is queryable for production incidents
    too, not just this repo's own dev history."""
    if not database.SURL or not incident_id:
        return []
    try:
        conn = database.get_db_conn()
        cur = conn.cursor()
        try:
            cur.execute(
                "SELECT id, parser_version, action, outcome, evidence_reference, started_at, completed_at "
                "FROM repair_attempt WHERE incident_id = %s ORDER BY started_at ASC;",
                (incident_id,),
            )
            cols = ["id", "parser_version", "action", "outcome", "evidence_reference", "started_at", "completed_at"]
            return [dict(zip(cols, (str(r[0]),) + r[1:])) for r in cur.fetchall()]
        finally:
            cur.close()
            conn.close()
    except Exception as e:
        logger.error(f"[ScraperResilience] Error fetching repair attempts for incident {incident_id}: {e}")
        return []


# ---------------------------------------------------------------------------
# Scan checkpointing -- "fixing today's parser doesn't recover yesterday's
# lost opportunities" (the review's own words). A future backfill reads
# this to know how far back it needs to re-scan a recovered source.
# ---------------------------------------------------------------------------

def get_council_scan_checkpoint(council: str, search_definition: str = "default") -> Optional[dict]:
    """search_definition defaults to 'default' so a council that only runs
    one search keeps working unchanged -- pass the real search definition
    name once a council runs more than one (see the schema's own comment,
    added in the 2026-09-22 second review, on why this can't be keyed by
    council alone)."""
    if not database.SURL or not council:
        return None
    try:
        conn = database.get_db_conn()
        cur = conn.cursor()
        try:
            cur.execute(
                "SELECT council, search_definition, platform, last_completed_window_start, "
                "last_completed_window_end, last_completed_at FROM council_scan_checkpoint "
                "WHERE council = %s AND search_definition = %s;",
                (council, search_definition),
            )
            row = cur.fetchone()
            if not row:
                return None
            cols = ["council", "search_definition", "platform", "last_completed_window_start",
                    "last_completed_window_end", "last_completed_at"]
            return dict(zip(cols, row))
        finally:
            cur.close()
            conn.close()
    except Exception as e:
        logger.error(f"[ScraperResilience] Error fetching scan checkpoint for {council!r}/{search_definition!r}: {e}")
        return None


def advance_council_scan_checkpoint(council: str, platform: str, *, window_start, window_end,
                                     search_definition: str = "default") -> bool:
    """Call this ONLY after a fully successful pass -- every required page
    and record for [window_start, window_end] processed without error, per
    the review's own "advance it only after all required pages and records
    are processed successfully." Advancing on a partial pass would let a
    later backfill believe a window was covered when it wasn't, silently
    reintroducing exactly the kind of quiet data loss this table exists to
    prevent.

    2026-09-22 second review additions (Astra, relayed by Nick):
      - search_definition scopes the checkpoint per search definition, not
        just per council (defaults to 'default' -- see the schema comment
        and get_council_scan_checkpoint's own docstring).
      - Backward-movement protection: the UPDATE branch of the upsert below
        now only fires when the new window_end is actually later than (or
        no checkpoint row exists, or the existing one has no window_end
        yet) the row's current last_completed_window_end. Without this, a
        slower concurrent scan for an OLDER window that finishes after a
        faster scan for a NEWER window would silently drag the checkpoint
        backwards, which would make a future backfill re-scan a window
        that was already covered -- wasted work, not data loss, but still
        exactly the kind of silent incorrectness this table exists to
        prevent."""
    if not database.SURL or not council:
        return False
    try:
        conn = database.get_db_conn()
        cur = conn.cursor()
        try:
            cur.execute(
                "INSERT INTO council_scan_checkpoint (council, search_definition, platform, "
                "last_completed_window_start, last_completed_window_end, last_completed_at, updated_at) "
                "VALUES (%s, %s, %s, %s, %s, NOW(), NOW()) "
                "ON CONFLICT (council, search_definition) DO UPDATE SET platform = EXCLUDED.platform, "
                "last_completed_window_start = EXCLUDED.last_completed_window_start, "
                "last_completed_window_end = EXCLUDED.last_completed_window_end, "
                "last_completed_at = NOW(), updated_at = NOW() "
                "WHERE council_scan_checkpoint.last_completed_window_end IS NULL "
                "OR EXCLUDED.last_completed_window_end > council_scan_checkpoint.last_completed_window_end;",
                (council, search_definition, platform, window_start, window_end),
            )
            conn.commit()
            if cur.rowcount == 0:
                logger.info(f"[ScraperResilience] Checkpoint for {council!r}/{search_definition!r} NOT advanced -- "
                            f"window_end {window_end!r} is not later than the existing checkpoint "
                            f"(backward-movement protection).")
            return True
        finally:
            cur.close()
            conn.close()
    except Exception as e:
        logger.error(f"[ScraperResilience] Error advancing scan checkpoint for {council!r}/{search_definition!r}: {e}")
        return False


# ---------------------------------------------------------------------------
# Pass metrics -- separates "the scraper is healthy" from "this produced
# sellable leads today", per the review's Section 3.
# ---------------------------------------------------------------------------

def record_council_scan_pass_metrics(*, council: str, platform: str, pages_fetched: int = 0,
                                      links_discovered: int = 0, records_parsed: int = 0,
                                      records_rejected_validation: int = 0,
                                      records_excluded_business_filters: int = 0,
                                      new_sellable_leads: int = 0) -> Optional[str]:
    """One row per scan pass. Lets 'zero new leads today' be told apart
    from 'zero pages fetched today' -- the former can be completely normal
    (every application already known, or excluded by business filters),
    the latter is a real problem. Purely additive logging -- never raises,
    never blocks a real scan on a logging failure."""
    if not database.SURL:
        return None
    try:
        conn = database.get_db_conn()
        cur = conn.cursor()
        try:
            cur.execute(
                "INSERT INTO council_scan_pass_metrics (council, platform, pages_fetched, links_discovered, "
                "records_parsed, records_rejected_validation, records_excluded_business_filters, new_sellable_leads) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s) RETURNING id;",
                (council, platform, pages_fetched, links_discovered, records_parsed,
                 records_rejected_validation, records_excluded_business_filters, new_sellable_leads),
            )
            row_id = cur.fetchone()[0]
            conn.commit()
            return str(row_id)
        finally:
            cur.close()
            conn.close()
    except Exception as e:
        logger.error(f"[ScraperResilience] Error recording scan pass metrics for {council!r}: {e}")
        return None


def get_recent_pass_metrics(council: str, limit: int = 10) -> list:
    if not database.SURL or not council:
        return []
    try:
        conn = database.get_db_conn()
        cur = conn.cursor()
        try:
            cur.execute(
                "SELECT pages_fetched, links_discovered, records_parsed, records_rejected_validation, "
                "records_excluded_business_filters, new_sellable_leads, recorded_at "
                "FROM council_scan_pass_metrics WHERE council = %s ORDER BY recorded_at DESC LIMIT %s;",
                (council, limit),
            )
            cols = ["pages_fetched", "links_discovered", "records_parsed", "records_rejected_validation",
                    "records_excluded_business_filters", "new_sellable_leads", "recorded_at"]
            return [dict(zip(cols, r)) for r in cur.fetchall()]
        finally:
            cur.close()
            conn.close()
    except Exception as e:
        logger.error(f"[ScraperResilience] Error fetching recent pass metrics for {council!r}: {e}")
        return []

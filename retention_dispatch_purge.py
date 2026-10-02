"""
retention_dispatch_purge.py -- 2026-09-23, Request D, Part 2: "Implement
the agreed deletion process. Schedule removal of homeowner personal data
and personalised mailing content within 72 hours of provider-confirmed
dispatch. Include the lead record, frozen approved HTML, legacy dispatch
records and other copies found during inspection. Preserve only explicitly
defined financial, suppression and minimal evidence records. Do not treat
provider acceptance as dispatch. Handle unknown outcomes through restricted
reconciliation, with retryable cleanup and failure alerts."

WHAT COUNTS AS "PROVIDER-CONFIRMED DISPATCH" (deliberately reused, not
reinvented -- see fulfilment.mark_provider_result and database.
process_pending_letter_dispatches, both already correct before this file
existed):
  - New pipeline (letter_obligations): status = 'dispatched' AND
    dispatched_at IS NOT NULL. mark_provider_result only ever sets these
    together, only for outcome == 'dispatched' (fulfilment.py's own
    OUTCOME_DISPATCHED, "provider confirmed the item has left their
    system" -- genuinely distinct from OUTCOME_ACCEPTED, "provider
    confirmed it will print/post this"). status = 'provider_accepted' /
    provider_accepted_at is a DIFFERENT, earlier state this module never
    treats as dispatch.
  - Legacy pipeline (letter_dispatches): sent_at IS NOT NULL. database.
    process_pending_letter_dispatches only ever sets sent_at when
    `result.ok and not is_dry_run_result` -- i.e. a genuine, non-dry-run
    success, never a dry-run queue or a failed attempt (those leave
    sent_at NULL and just increment attempts/last_error instead). There is
    no separate "accepted-but-not-dispatched" state in the legacy schema
    to confuse this with.
Both pipelines are swept regardless of fulfilment.active_pipeline() --
LETTER_DISPATCH_PIPELINE only controls which pipeline is used for NEW
sales going forward; a lead dispatched under the other pipeline (before a
switch, or if it's switched back later) still needs its personal data
purged on schedule. See fulfilment.py's own MIGRATION NOTES for the same
"never assume only one pipeline has rows" caution applied elsewhere.

2026-09-23 UPDATE (Request F: "inspect the fields retained after purging
... for identifying descriptions ... remove or restrict what defeats the
agreed privacy model"): a HISTORICAL claim (address_release.
is_historical_purchase -- a lead_reference resolved ONLY via the legacy
letter_dispatches fallback, no lead_allocations row) is now EXCLUDED from
this purge entirely, permanently, for both eligibility counting and the
actual sweep. This is not a new policy call -- it is fixing a real
contradiction with the 2026-09-23 Request D Part 1 instruction this same
session already implemented against: "Preserve the agreed historical-
access distinction, but do not treat historical disclosures as undone."
guarded_address_for_lead/guarded_applicant_name_for_lead_reference show a
historical claim's REAL address/name from whatever is currently stored in
leads.address/applicant_name -- so if this purge had cleared that
storage (which, before this fix, it did: every historical dispatch is by
definition already well past 72 hours old, so the very first run of this
purge in production would have wiped every historical claim's address in
one pass), a contractor with a previously-and-permanently-disclosed
address would suddenly see it redacted -- exactly "treating a historical
disclosure as undone." A NEW allocation (letter_obligations rows are
ALWAYS a new allocation -- they only ever exist via fulfilment.
create_allocation_and_obligation, which always creates the backing
lead_allocations row first) never had this problem: its address was
already redacted to the buyer from the start, so purging its underlying
storage doesn't undo anything a buyer could see.
Net effect: letter_obligations purging is UNCHANGED (never historical).
letter_dispatches purging now only touches a row whose lead_reference
also has a lead_allocations row (i.e. NOT historical, despite living in
the legacy table -- can happen during/after a pipeline switch); a
letter_dispatches row with no lead_allocations counterpart at all
(genuinely historical) is now never selected, never purged, and its
address/applicant_name/frozen content survive indefinitely -- consistent
with the historical grandfather clause being permanent and non-growing.
This is a real, unresolved retention decision in its own right, reported
back rather than invented further: NOTHING currently ever purges a
historical dispatch's personal data, on any schedule. If Nick wants that
data purged too -- just not within the same 72-hour window that would
undo the disclosure -- that needs an explicit, separate retention period
from him, not one invented here.

WHAT "UNKNOWN" MEANS HERE (restricted reconciliation, not auto-retry):
letter_obligations.status = 'unknown' is a dead end by design (fulfilment.
mark_provider_result's docstring: "deliberately never flips back to
'ready'" -- see that function). This module never purges an unknown-status
obligation (it isn't a confirmed dispatch, so the 72-hour clock never
starts) and never resends anything. reconcile_unknown_outcome_obligations
below is the ONLY thing that can move an obligation out of 'unknown', and
it does so by asking the SAME provider that produced the ambiguous result
for its own authoritative status (LetterProviderAdapter.check_status --
"best-effort reconciliation lookup... without resending"), via the exact
provider registry construction (letter_providers.registry.
build_registry_from_env) the sending path already uses. If the provider
has no status-lookup capability (check_status returns None, the base
class's own default), the obligation stays 'unknown' and is counted for a
human to look at -- see count_unknown_outcome_obligations_awaiting_
reconciliation. Nothing here invents a live provider's dispatch signal --
it only ever asks the CONFIGURED provider's own adapter, real or fake.

MINIMAL EVIDENCE PRESERVED (never touched by the purge below): letter_
obligations/letter_dispatches' own id, lead_reference, buyer_email,
sale_context, status, every timestamp column (created_at/updated_at/
provider_accepted_at/dispatched_at/delivered_at/failed_at/sent_at/
purged_at), provider_name, provider_reference, content_fingerprint,
template_version, attempts, last_error. Only address/applicant_name/
approved_content_html are cleared -- everything needed to answer "was this
lead sold, to whom, when, was it posted, for how much" (financial/
operational evidence) survives; only the homeowner's own personal data and
the personalised letter content do not. payments/lead_allocations/
postal_suppressions are never touched by this module at all -- postal_
suppressions in particular is a per-address opt-out record that must
outlive the address it references (see suppression.py's own module
docstring on why it's keyed on an HMAC digest, not the plaintext address,
precisely so it can survive this kind of purge).

2026-09-23 UPDATE (Request F follow-up, external review finding, "High":
"retention_dispatch_purge.py:314 clears address/name but not summary or
raw reference; retained data is not just a minimal non-identifying
receipt"): leads.summary is now ALSO overwritten with its redacted form
(database._redact_address_from_summary) at the same point address/
applicant_name are cleared on the leads row -- real scraped summaries very
often restate the exact address inline, so leaving the raw text in place
indefinitely defeated most of the point of clearing the address column
next to it. This is the SAME best-effort, regex-based scrub already
reused everywhere else in this codebase (never a stronger, verified
guarantee -- see address_release.guarded_summary_for_lead's own docstring
for the same explicitly-reported limitation). It is NOT applied to
letter_obligations.address/letter_dispatches.address (already placeholder
strings, not free text) and never touches a historical claim's summary
(every row reaching this step is guaranteed non-historical -- see the
inline comment at the call site).
Two items STILL NOT addressed here, reported rather than invented: (1) the
raw council lead_reference itself (letter_obligations/letter_dispatches/
lead_allocations/leads.reference) is still retained indefinitely,
unredacted, on every row, historical or not -- it is a real, if smaller,
indirect identifier (see buyer_facing_reference's own docstring), and this
module's purge/join logic (the NOT EXISTS guards above) currently depends
on it staying stable; scrubbing it needs a decision about what replaces it
for that logic, not something to invent unilaterally here. (2) A
historical claim's own summary is not, and by this module's design cannot
be, redacted -- its address is already fully disclosed on the same page it
would appear (see this module's own historical-exemption docstring
section above), so redacting only the summary text would hide nothing a
buyer can't already see in the address field right next to it.

DOES NOT DEPLOY OR RUN AGAINST PRODUCTION: this module's functions are
wired into database.init_db() (schema) and main.py's run_full_autonomous_
cycle / an admin route (the sweep itself), exactly like cleanup_stale_leads
already is -- neither runs unless main.py's own app actually starts
against a real database, which this sandbox never does.
"""
from __future__ import annotations

import logging
import re
from typing import Optional

import database

logger = logging.getLogger("treekey-dispatch-purge")

DISPATCH_PURGE_DELAY_HOURS = 72

# Used only where the column is NOT NULL (letter_obligations.address,
# letter_dispatches.address -- both `TEXT NOT NULL` in their original
# schema, see fulfilment.py/database.py's own CREATE TABLE statements) and
# so cannot simply be set to NULL without a schema-breaking ALTER. Nullable
# columns (applicant_name, approved_content_html, leads.address, leads.
# applicant_name) are set to NULL directly instead -- this placeholder is
# deliberately never used for leads.address, so a purged lead's row reads
# exactly like guarded_address_for_lead's own REDACTED_ADDRESS_PLACEHOLDER
# path (see address_release.py: "if not real_address: return
# REDACTED_ADDRESS_PLACEHOLDER" -- NULL there already degrades correctly).
PURGED_ADDRESS_PLACEHOLDER = "[address removed -- retention period elapsed]"


def init_dispatch_purge_schema(cur) -> None:
    """Idempotent ALTER TABLE ... ADD COLUMN IF NOT EXISTS block, same
    backward-compatible-migration pattern as every other module this
    session added (suppression.py, letter_content.py, address_release.py).
    purged_at tracks which rows this module has already processed, so a
    repeated run is a no-op for them (never re-purges, never re-alerts) and
    so count_dispatch_purge_eligible/admin visibility can distinguish
    "already purged" from "not yet eligible"."""
    cur.execute("""
        ALTER TABLE letter_obligations ADD COLUMN IF NOT EXISTS purged_at TIMESTAMPTZ;
        ALTER TABLE letter_dispatches ADD COLUMN IF NOT EXISTS purged_at TIMESTAMPTZ;
        ALTER TABLE leads ADD COLUMN IF NOT EXISTS personal_data_purged_at TIMESTAMPTZ;
        CREATE INDEX IF NOT EXISTS idx_letter_obligations_purge_eligible
            ON letter_obligations(dispatched_at) WHERE status = 'dispatched' AND purged_at IS NULL;
        CREATE INDEX IF NOT EXISTS idx_letter_dispatches_purge_eligible
            ON letter_dispatches(sent_at) WHERE purged_at IS NULL;
    """)


# ---------------------------------------------------------------------------
# Reporting / preview -- same "count first, admin can look before it runs
# automatically" convention as database.count_stale_leads.

def count_dispatch_purge_eligible() -> dict:
    """Preview counts for purge_dispatched_personal_data -- how many rows
    in each table are a confirmed dispatch, past the 72-hour clock, and not
    yet purged. Does not change anything."""
    conn = database.get_db_conn()
    cur = conn.cursor()
    try:
        cur.execute(f"""
            SELECT count(*) FROM letter_obligations
            WHERE status = 'dispatched' AND dispatched_at IS NOT NULL
              AND dispatched_at <= NOW() - INTERVAL '{DISPATCH_PURGE_DELAY_HOURS} hours'
              AND purged_at IS NULL;
        """)
        obligations = cur.fetchone()[0]
        # 2026-09-23, Request F: excludes a HISTORICAL letter_dispatches row
        # (no lead_allocations counterpart for the same reference) -- see
        # this module's own docstring for why. letter_obligations above
        # needs no equivalent filter: every row there is, by construction,
        # a new allocation, never historical.
        cur.execute(f"""
            SELECT count(*) FROM letter_dispatches
            WHERE sent_at IS NOT NULL
              AND sent_at <= NOW() - INTERVAL '{DISPATCH_PURGE_DELAY_HOURS} hours'
              AND purged_at IS NULL
              AND EXISTS (
                  SELECT 1 FROM lead_allocations la
                  WHERE la.lead_reference = letter_dispatches.lead_reference
              );
        """)
        dispatches = cur.fetchone()[0]
        return {"obligations": obligations, "dispatches": dispatches}
    finally:
        cur.close()
        conn.close()


def count_unknown_outcome_obligations_awaiting_reconciliation() -> int:
    """'Restricted reconciliation' visibility: obligations stuck at
    status='unknown' -- never auto-purged (no confirmed dispatch, the
    72-hour clock never started) and never auto-resent (see this module's
    own docstring). Exposed here the same way count_deletion_quarantined_
    leads exposes leads a human needs to look at, not something this
    module resolves silently on its own."""
    conn = database.get_db_conn()
    cur = conn.cursor()
    try:
        cur.execute("SELECT count(*) FROM letter_obligations WHERE status = 'unknown';")
        return cur.fetchone()[0]
    finally:
        cur.close()
        conn.close()


# ---------------------------------------------------------------------------
# The purge sweep itself.

def purge_dispatched_personal_data() -> dict:
    """Permanently clears homeowner personal data and personalised letter
    content for every row that is a genuine, provider-confirmed dispatch
    (see this module's own docstring for exactly what that means in each
    table) more than DISPATCH_PURGE_DELAY_HOURS ago and not already
    purged. Three tables, one pass, one transaction:

      1. letter_obligations: address -> placeholder (NOT NULL column),
         applicant_name -> NULL, approved_content_html -> NULL (the frozen
         rendered HTML this session's own earlier work froze specifically
         so it would never need re-rendering -- it must not survive past
         its own retention window either), purged_at -> NOW().
      2. letter_dispatches (legacy pipeline): address -> placeholder,
         applicant_name -> NULL, purged_at -> NOW().
      3. leads: for every lead_reference touched by (1) or (2) in this
         pass OR already purged in an earlier pass, clear address/
         applicant_name on the leads row too -- but ONLY when no OTHER
         letter_obligations/letter_dispatches row for that same reference
         is still unresolved (pending/ready/provider_accepted/unknown/not
         yet sent) -- see the NOT EXISTS guards below. In this codebase's
         actual model a lead is sold and dispatched at most once, so this
         guard should never actually block anything for a real row; it
         exists so a future edge case (a lead somehow re-queued) fails
         toward NOT purging early, never toward purging while something
         is still in flight. 2026-09-23, Request F follow-up (external
         review finding): also overwrites leads.summary with its
         redacted form (database._redact_address_from_summary -- the same
         regex-based scrub already relied on for every pre-purchase
         display) rather than leaving the raw scraped description, which
         very often restates the exact address inline, sitting in storage
         indefinitely after everything else has been cleared. Every row
         reaching this step is guaranteed NON-historical (see the inline
         comment at the call site), so this never touches a historical
         claim's summary -- matching address/applicant_name exactly.

    Retryable: does not raise on the ordinary "eligible rows found and
    purged" path. On any DB error mid-pass, rolls back the WHOLE pass
    (never a partial purge left half-applied), raises an admin-visible
    incident alert (same notifications.send_system_incident_alert channel
    database.cleanup_stale_leads already uses), and RE-RAISES so the
    caller (run_full_autonomous_cycle / the admin route) can decide to
    retry -- next run picks up exactly the same eligible set (purged_at
    IS NULL is unaffected by a rolled-back attempt), so a retry is always
    safe and idempotent."""
    conn = database.get_db_conn()
    cur = conn.cursor()
    try:
        cur.execute(f"""
            SELECT id, lead_reference FROM letter_obligations
            WHERE status = 'dispatched' AND dispatched_at IS NOT NULL
              AND dispatched_at <= NOW() - INTERVAL '{DISPATCH_PURGE_DELAY_HOURS} hours'
              AND purged_at IS NULL;
        """)
        obligation_rows = cur.fetchall()

        # 2026-09-23, Request F: same historical exclusion as
        # count_dispatch_purge_eligible above -- see this module's own
        # docstring.
        cur.execute(f"""
            SELECT id, lead_reference FROM letter_dispatches
            WHERE sent_at IS NOT NULL
              AND sent_at <= NOW() - INTERVAL '{DISPATCH_PURGE_DELAY_HOURS} hours'
              AND purged_at IS NULL
              AND EXISTS (
                  SELECT 1 FROM lead_allocations la
                  WHERE la.lead_reference = letter_dispatches.lead_reference
              );
        """)
        dispatch_rows = cur.fetchall()

        touched_references = set()

        for obligation_id, lead_reference in obligation_rows:
            cur.execute("""
                UPDATE letter_obligations
                SET address = %s, applicant_name = NULL, approved_content_html = NULL, purged_at = NOW()
                WHERE id = %s;
            """, (PURGED_ADDRESS_PLACEHOLDER, obligation_id))
            touched_references.add(lead_reference)

        for dispatch_id, lead_reference in dispatch_rows:
            cur.execute("""
                UPDATE letter_dispatches
                SET address = %s, applicant_name = NULL, purged_at = NOW()
                WHERE id = %s;
            """, (PURGED_ADDRESS_PLACEHOLDER, dispatch_id))
            touched_references.add(lead_reference)

        leads_purged = 0
        if touched_references:
            # Also reconsider any reference purged in an EARLIER pass whose
            # leads row somehow wasn't cleared yet (e.g. this function
            # failed after purging the obligation/dispatch row but before
            # reaching the leads UPDATE in a prior run) -- belt and braces
            # for the retry story above, not expected in the normal path.
            cur.execute("""
                SELECT DISTINCT lead_reference FROM letter_obligations WHERE purged_at IS NOT NULL
                UNION
                SELECT DISTINCT lead_reference FROM letter_dispatches WHERE purged_at IS NOT NULL;
            """)
            all_purged_references = {row[0] for row in cur.fetchall()} | touched_references

            # 2026-09-23, Request F follow-up (external review finding,
            # "High": "retention_dispatch_purge.py:314 clears address/name
            # but not summary or raw reference; retained data is not just
            # a minimal non-identifying receipt"): leads.summary is real
            # scraped free text that very often restates the exact address
            # inline (see address_release.guarded_summary_for_lead's own
            # docstring/database._redact_address_from_summary) -- clearing
            # address/applicant_name here while leaving the raw summary in
            # place indefinitely undoes most of the purge's own point.
            # Fetch each eligible row's current summary first (a bulk
            # single-value UPDATE can't apply a PER-ROW redaction), redact
            # it with the SAME existing mechanism guarded_summary_for_lead
            # already reuses for display, then clear address/applicant_name
            # and store the redacted (not blanked) summary in one pass per
            # row. every reference reaching this branch is guaranteed
            # NON-historical already: it only got here because an
            # obligation/dispatch row for it was actually purged above, and
            # (per this module's own historical-dispatch exemption) a
            # historical letter_dispatches row is now NEVER purged -- so no
            # separate is_historical_purchase check is needed here, unlike
            # address_release.guarded_summary_for_lead's own general-purpose
            # wrapper (not called here to avoid opening a second DB
            # connection mid-transaction).
            cur.execute("""
                SELECT reference, summary FROM leads
                WHERE reference = ANY(%s)
                  AND personal_data_purged_at IS NULL
                  AND NOT EXISTS (
                      SELECT 1 FROM letter_obligations lo
                      WHERE lo.lead_reference = leads.reference AND lo.purged_at IS NULL
                  )
                  AND NOT EXISTS (
                      SELECT 1 FROM letter_dispatches ld
                      WHERE ld.lead_reference = leads.reference AND ld.purged_at IS NULL
                  );
            """, (list(all_purged_references),))
            _leads_to_clear = cur.fetchall()

            for _lead_ref, _raw_summary in _leads_to_clear:
                _redacted_summary = database._redact_address_from_summary(_raw_summary) if _raw_summary else _raw_summary
                cur.execute("""
                    UPDATE leads
                    SET address = NULL, applicant_name = NULL, summary = %s, personal_data_purged_at = NOW()
                    WHERE reference = %s;
                """, (_redacted_summary, _lead_ref))
            leads_purged = len(_leads_to_clear)

        conn.commit()
        result = {
            "obligations_purged": len(obligation_rows),
            "dispatches_purged": len(dispatch_rows),
            "leads_purged": leads_purged,
        }
        if result["obligations_purged"] or result["dispatches_purged"] or result["leads_purged"]:
            logger.info(f"[Dispatch Purge] {result}")
        return result
    except Exception as e:
        try:
            conn.rollback()
        except Exception:
            pass
        logger.error(f"[Dispatch Purge] purge_dispatched_personal_data failed: {e}")
        try:
            import notifications
            notifications.send_system_incident_alert(
                category="DATA RETENTION",
                title="72-HOUR POST-DISPATCH PERSONAL-DATA PURGE FAILED",
                description=f"retention_dispatch_purge.purge_dispatched_personal_data failed: {str(e)[:200]}",
                impact=("Homeowner personal data and frozen letter content past the "
                        f"{DISPATCH_PURGE_DELAY_HOURS}-hour post-dispatch retention deadline were NOT "
                        "cleared this run. No sale/mailing was affected (this is a retention-only sweep, "
                        "not part of the send path) -- but retained personal data is not being cleared "
                        "down as intended until this is resolved."),
                action_required="Check application/database health, then re-run the purge (admin route, "
                                 "or wait for tomorrow's autonomous cycle) once resolved -- always safe to "
                                 "retry, it only ever re-selects rows still unpurged.",
                severity="WARNING",
                throttle_hours=12.0,
            )
        except Exception as alert_err:
            logger.error(f"[Dispatch Purge] Also failed to raise the incident alert for the above: {alert_err}")
        raise
    finally:
        cur.close()
        conn.close()


# ---------------------------------------------------------------------------
# Restricted reconciliation for 'unknown' outcomes.

_SUBMITTED_MODE_RE = re.compile(r"\[submitted_testmode=(true|false)\]")
_RECOVERY_TAG = " || RECOVERY:"


def _submitted_testmode(last_error):
    """The test/live mode an ambiguous submission was made in, read from the
    marker the Intelliprint adapter records on every UNKNOWN send result.
    Returns True/False only when exactly one distinct mode is recorded;
    otherwise None (not established reliably -> caller holds for review)."""
    modes = set(_SUBMITTED_MODE_RE.findall(last_error or ""))
    if len(modes) != 1:
        return None
    return modes.pop() == "true"


def _classify_result(result) -> str:
    """What a provider-reported result means for the order and its budget.
    'settle' (accepted/dispatched), 'release' (rejected AND adapter proves no
    charge), 'charge_unproven' (rejection-type status, charge not proven
    absent), 'unresolved' (unknown outcome)."""
    if result.outcome in ("accepted", "dispatched"):
        return "settle"
    if result.outcome == "rejected":
        return "release" if getattr(result, "confirmed_uncharged", False) else "charge_unproven"
    return "unresolved"


def _apply_resolution(fulfilment, gate, cur, obligation_id, result, kind) -> None:
    """Status change + budget change on the caller's cursor/transaction (the
    caller commits). settle()/release() only act on still-'reserved' rows."""
    if kind == "settle":
        fulfilment.mark_provider_result(
            cur, obligation_id, outcome=result.outcome, is_dry_run=False,
            provider_name=result.provider_name, provider_reference=result.provider_reference,
            error=result.message or None)
        # Records the provider's confirmed actual cost (see
        # funding.FundingGate.settle_actual). Raises on a DB error, which rolls
        # the whole order back (it stays 'unknown' and is retried later).
        gate.settle_actual(cur, obligation_id, result.cost_pence)
    else:  # 'release': rejected AND confirmed_uncharged
        fulfilment.mark_provider_result(
            cur, obligation_id, outcome="failed", is_dry_run=False,
            provider_name=result.provider_name, provider_reference=result.provider_reference,
            error=result.message or None)
        gate.release(cur, obligation_id)


def _record_review_reason(cur, obligation_id, reason, save_reference=None) -> None:
    """Keeps an order at 'unknown' and records WHY it needs a human, appended
    to last_error after a fixed tag (replaced, not accumulated, on repeat runs;
    the original submission text and mode marker are preserved). Optionally
    saves a recovered provider id so later runs use check_status directly.
    Guarded: only ever touches a row still 'unknown'."""
    cur.execute(
        "UPDATE letter_obligations SET "
        "last_error = split_part(COALESCE(last_error, ''), %s, 1) || %s || %s, "
        "provider_reference = COALESCE(provider_reference, %s), updated_at = NOW() "
        "WHERE id = %s AND status = 'unknown';",
        (_RECOVERY_TAG, _RECOVERY_TAG + " ", reason, save_reference, obligation_id))


def reconcile_unknown_outcome_obligations(limit: int = 50) -> dict:
    """The ONLY path that can move a letter_obligations row out of
    status='unknown'. Restricted, deliberately, in every direction:
      - never resends (only ever calls check_status / find_by_reference, both
        read-only; never .send()), never switches provider, and never sets a
        row back to 'ready';
      - only ever asks the SAME provider that produced the ambiguous result;
      - never guesses: an unresolved status, an unproven rejection, a failed
        or incomplete lookup, zero or several matches all leave the row AND
        its funding reservation exactly as they were (a review reason is
        recorded for the recovery-search cases);
      - `limit` bounds one call to a small batch.

    Orders WITH a provider_reference (fix A, 2 Oct 2026) -- check_status():
      - accepted / dispatched -> status set AND FundingGate.settle() once;
      - rejected AND result.confirmed_uncharged -> status 'failed' AND
        FundingGate.release() once;
      - rejected without proof of no charge (e.g. Intelliprint 'returned' /
        'cancelled': a returned letter was posted and paid for) -> held,
        counted `held_charge_unproven`;
      - unknown / no answer / lookup raised -> unchanged (`still_unknown`).

    Orders WITHOUT a provider_reference (no-reference recovery) -- only for
    adapters with supports_reference_lookup (Intelliprint), via
    find_by_reference() using the exact reference the submission used and the
    mode recorded at submission (absent/ambiguous mode -> hold):
      - exactly ONE valid match from a COMPLETE result set -> its provider id
        is saved and the result goes through the same logic as above;
      - zero matches, several matches, an incomplete/failed lookup -> held
        with a recorded reason (`recovery_held`). "No match" is NOT proof the
        letter was not submitted (the provider documents no idempotency and
        no guarantee), so it never authorises a resend.

    Every write happens in ONE transaction per order after re-checking the
    row under a lock (still 'unknown', and for recovery still no
    provider_reference), so a repeated or overlapping run cannot overwrite a
    resolved order or settle/release twice. Spend is still recorded at the
    reserved ESTIMATE, not the provider-reported cost (known open issue)."""
    import letter_providers.registry as provider_registry
    import fulfilment
    import funding

    conn = database.get_db_conn()
    cur = conn.cursor()
    try:
        cur.execute("""
            SELECT id, provider_name, provider_reference FROM letter_obligations
            WHERE status = 'unknown' ORDER BY updated_at ASC LIMIT %s;
        """, (limit,))
        rows = cur.fetchall()
        conn.commit()  # end the read-only transaction; no locks held across provider calls

        registry = provider_registry.build_registry_from_env()
        adapters_by_name = {slot.adapter.name: slot.adapter for slot in registry.slots}
        gate = funding.FundingGate()

        summary = {"resolved": 0, "still_unknown": 0, "skipped_no_adapter": 0,
                   "held_charge_unproven": 0, "errors": 0,
                   "references_recovered": 0, "recovery_held": 0, "review": []}

        for obligation_id, provider_name, provider_reference in rows:
            adapter = adapters_by_name.get(provider_name) if provider_name else None
            if adapter is None:
                summary["skipped_no_adapter"] += 1
                continue

            recovered_via_search = False
            try:
                if provider_reference:
                    result = adapter.check_status(provider_reference)
                else:
                    if not getattr(adapter, "supports_reference_lookup", False):
                        summary["skipped_no_adapter"] += 1
                        continue
                    cur.execute("SELECT idempotency_key, last_error FROM letter_obligations WHERE id = %s;",
                                (obligation_id,))
                    meta = cur.fetchone()
                    conn.commit()
                    testmode = _submitted_testmode(meta[1] if meta else None)
                    reason = None
                    if not meta or not meta[0]:
                        reason = "No idempotency key on record; cannot rebuild the submitted reference."
                    elif testmode is None:
                        reason = ("The test/live mode of the original submission is not recorded reliably; "
                                  "search not attempted. Check the Intelliprint dashboard.")
                    else:
                        lookup = adapter.find_by_reference(meta[0], testmode=testmode)
                        if lookup is None:
                            reason = "Provider has no reference search."
                        elif not lookup.complete:
                            reason = f"Reference search incomplete: {lookup.reason}"
                        elif len(lookup.matches) == 0:
                            reason = ("No job found under the submitted reference. This does NOT prove it was "
                                      "not submitted - check the Intelliprint dashboard before any resend.")
                        elif len(lookup.matches) > 1:
                            reason = (f"{len(lookup.matches)} jobs share the submitted reference; "
                                      f"manual review needed.")
                    if reason is not None:
                        cur.execute("SELECT status FROM letter_obligations WHERE id = %s FOR UPDATE;", (obligation_id,))
                        cur_state = cur.fetchone()
                        if cur_state and cur_state[0] == "unknown":
                            _record_review_reason(cur, obligation_id, reason)
                        conn.commit()
                        summary["recovery_held"] += 1
                        summary["review"].append((obligation_id, reason))
                        logger.warning(f"[Dispatch Purge] Recovery held for obligation {obligation_id}: {reason}")
                        continue
                    result = lookup.matches[0]
                    recovered_via_search = True
            except Exception as e:
                try:
                    conn.rollback()
                except Exception:
                    pass
                logger.warning(f"[Dispatch Purge] Status/recovery lookup raised for obligation {obligation_id} "
                                f"via provider {provider_name!r}: {e} -- leaving as 'unknown'.")
                summary["still_unknown"] += 1
                continue

            if result is None:
                summary["still_unknown"] += 1
                continue
            kind = _classify_result(result)

            try:
                # Re-check under a row lock: another reconciliation (or any
                # other writer) may have changed this row since we listed it.
                cur.execute("SELECT status, provider_reference FROM letter_obligations WHERE id = %s FOR UPDATE;",
                            (obligation_id,))
                current = cur.fetchone()
                if not current or current[0] != "unknown" or (recovered_via_search and current[1]):
                    conn.rollback()
                    continue

                if kind in ("settle", "release"):
                    _apply_resolution(fulfilment, gate, cur, obligation_id, result, kind)
                    conn.commit()
                    summary["resolved"] += 1
                    if recovered_via_search:
                        summary["references_recovered"] += 1
                elif recovered_via_search:
                    # One valid match found but its status does not resolve the
                    # order: save the provider id (so later runs use check_status)
                    # and record why it is still held.
                    why = ("Matching job found and its id saved, but its status does not prove acceptance"
                           if kind == "unresolved" else
                           "Matching job found and its id saved, but its rejection-type status does not prove "
                           "nothing was charged")
                    _record_review_reason(cur, obligation_id, why, save_reference=result.provider_reference)
                    conn.commit()
                    summary["references_recovered"] += 1
                    summary["recovery_held"] += 1
                    summary["review"].append((obligation_id, why))
                else:
                    conn.rollback()
                    if kind == "charge_unproven":
                        logger.warning(f"[Dispatch Purge] Obligation {obligation_id}: provider reports a "
                                        f"rejection-type status but that does not prove nothing was charged -- "
                                        f"leaving as 'unknown' with its funding reservation held; manual review needed.")
                        summary["held_charge_unproven"] += 1
                    else:
                        summary["still_unknown"] += 1
            except Exception as e:
                try:
                    conn.rollback()
                except Exception:
                    pass
                logger.error(f"[Dispatch Purge] Reconciling obligation {obligation_id} failed and was rolled "
                              f"back (status and budget unchanged): {e}")
                summary["errors"] += 1

        if summary["resolved"] or summary["references_recovered"]:
            logger.info(f"[Dispatch Purge] Reconciliation: { {k: v for k, v in summary.items() if k != 'review'} }")
        return summary
    except Exception as e:
        try:
            conn.rollback()
        except Exception:
            pass
        logger.error(f"[Dispatch Purge] reconcile_unknown_outcome_obligations failed: {e}")
        raise
    finally:
        cur.close()
        conn.close()

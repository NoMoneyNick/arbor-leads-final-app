"""
accepted_dispatch_check.py -- checks letters the provider has ACCEPTED, using their saved provider
reference, and (only when allowed) records that the provider reports them sent.

WHY: nothing used to move an order from provider_accepted to dispatched, so the post-dispatch
personal-data purge (retention_dispatch_purge) -- which acts only on 'dispatched' orders -- would
never have run for an accepted letter.

READ-ONLY toward the provider (check_status = one GET by saved id; never send(), never a resend).
Never touches money: no settle, no release, no reserve; attempts, provider_name, provider_reference,
costs and every funding record are left exactly as they are. Isolated from the purge: its own module,
its own connection, never raises (returns a summary); main.py calls it in its own try/except BEFORE
the purge so a failure here cannot stop the purge.

WHAT `sent` MEANS (settled 8 Oct 2026): Intelliprint's written reply, reported by Nick, confirms that `sent`
  means the item has been handed over to Royal Mail. That is the meaning this module, the manual
  reconcile path (retention_dispatch_purge.reconcile_unknown_outcome_obligations) and the customer-facing
  stage wording all use, through ONE shared mapping (letter_providers/intelliprint_provider.py maps
  `sent` -> dispatched). Handover is NOT delivery: there is still no delivered status for untracked post.
  SENT_MEANS_POSTAL_HANDOVER_VERIFIED is therefore True. It stays a code constant (never an environment
  variable) so it can still be switched off by a reviewed code change if the provider ever changes what
  `sent` means; when False a `sent` report is only observed and HELD with a note.

WHAT dispatched_at MEANS: the time TREEKEY OBSERVED the provider's `sent` status, not the provider's own
handover time, so the 72-hour purge clock starts at our observation.

A LETTER THAT LATER BECOMES `returned`: a returned letter was handed over first, so the dispatch record is
PRESERVED. An order that is already 'dispatched' is never selected again by this module (it reads only
'provider_accepted'), so nothing here changes, clears or re-opens it, moves money, or stops the purge. The
return itself is handled separately (not here). A `returned`/`cancelled`/`invalid_address` report seen while
an order is still 'provider_accepted' is held for a human, status and money unchanged, as before.

ELIGIBILITY (all must hold): status 'provider_accepted'; is_dry_run FALSE; a saved provider
reference; the order's own record shows a LIVE submission (testmode=False recorded in last_error --
absent, ambiguous or testmode=True means NOT eligible, so "not dry-run" alone never makes a test-mode
job eligible); the configured adapter for that provider is itself in live mode; not checked within
the last hour (updated_at is the throttle; no new column).

RESULTS: sent -> dispatched (once, under a row lock; if the gate constant is ever switched off, held
with a note instead); waiting_to_print/printing/enclosing/shipping or no answer or an unrecognised status -> unchanged,
checked again later; returned/cancelled/invalid_address -> status kept, held for a human, no money
moved. A provider reference that differs from the saved one is never acted on.

NOT HANDLED HERE (recorded separately): draft/unpaid jobs, what to do about a returned letter (policy and
any follow-up), cost re-checks.
"""
from __future__ import annotations

import logging
import re

import database

logger = logging.getLogger(__name__)

# See WHAT `sent` MEANS above. Code constant on purpose.
SENT_MEANS_POSTAL_HANDOVER_VERIFIED = True

CHECK_INTERVAL_HOURS = 1
BATCH_LIMIT = 25

_MODE_RE = re.compile(r"\(testmode=(True|False)\)|\[submitted_testmode=(true|false)\]")
_TAG = " || DISPATCH-CHECK:"


def recorded_live_submission(last_error) -> bool:
    """True ONLY if the order's own record shows exactly one submission mode and it is live
    (testmode=False). No marker, a test-mode marker, or conflicting markers -> False."""
    modes = {(a or b).lower() for a, b in _MODE_RE.findall(last_error or "")}
    return modes == {"false"}


def _touch(cur, oid) -> None:
    cur.execute("UPDATE letter_obligations SET updated_at = NOW() WHERE id = %s AND status = 'provider_accepted';",
                (oid,))


def _note(cur, oid, text) -> None:
    """Holds the order (status unchanged) and records why; replaces the text after the tag, keeps the rest."""
    cur.execute(
        "UPDATE letter_obligations SET last_error = split_part(COALESCE(last_error, ''), %s, 1) || %s || %s, "
        "updated_at = NOW() WHERE id = %s AND status = 'provider_accepted';",
        (_TAG, _TAG + " ", text, oid))


def check_accepted_orders(limit: int = BATCH_LIMIT, registry=None) -> dict:
    """One bounded pass. Never raises; failures are counted in the returned summary."""
    summary = {"checked": 0, "dispatched": 0, "dispatched_status_held": 0, "still_accepted": 0,
               "needs_review": 0, "skipped_not_live": 0, "skipped_no_adapter": 0,
               "reference_mismatch": 0, "errors": 0}
    conn = None
    try:
        conn = database.get_db_conn()
        cur = conn.cursor()
        cur.execute(f"""
            SELECT id, provider_name, provider_reference, last_error FROM letter_obligations
            WHERE status = 'provider_accepted' AND is_dry_run = FALSE AND provider_accepted_at IS NOT NULL
              AND provider_reference IS NOT NULL AND provider_reference <> ''
              AND updated_at <= NOW() - INTERVAL '{int(CHECK_INTERVAL_HOURS)} hours'
            ORDER BY updated_at ASC LIMIT %s;
        """, (int(limit),))
        rows = cur.fetchall()
        conn.commit()
        if not rows:
            return summary

        if registry is None:
            import letter_providers.registry as provider_registry
            registry = provider_registry.build_registry_from_env()
        adapters = {slot.adapter.name: slot.adapter for slot in registry.slots}
        import fulfilment

        for oid, provider_name, provider_reference, last_error in rows:
            try:
                adapter = adapters.get(provider_name) if provider_name else None
                if adapter is None:
                    summary["skipped_no_adapter"] += 1
                    _touch(cur, oid); conn.commit()
                    continue
                if not recorded_live_submission(last_error) or getattr(adapter, "test_mode", None) is not False:
                    summary["skipped_not_live"] += 1
                    _touch(cur, oid); conn.commit()
                    continue

                summary["checked"] += 1
                try:
                    result = adapter.check_status(provider_reference)   # read-only GET; never send()
                except Exception as exc:  # noqa: BLE001
                    logger.warning(f"[Accepted Check] status lookup raised for {oid}: {type(exc).__name__}")
                    result = None

                cur.execute("SELECT status FROM letter_obligations WHERE id = %s FOR UPDATE;", (oid,))
                cur_state = cur.fetchone()
                if not cur_state or cur_state[0] != "provider_accepted":
                    conn.rollback()
                    continue

                if result is None or str(result.provider_reference or "") != str(provider_reference):
                    if result is not None:
                        summary["reference_mismatch"] += 1
                    else:
                        summary["still_accepted"] += 1
                    _touch(cur, oid); conn.commit()
                elif result.outcome == "dispatched":
                    if SENT_MEANS_POSTAL_HANDOVER_VERIFIED:
                        note = ("Provider status 'sent' (handed to Royal Mail) observed by TreeKey; dispatched_at is the "
                                "time TreeKey observed this, not the provider's own dispatch time.")
                        if fulfilment.mark_dispatch_observed(cur, oid, note):
                            summary["dispatched"] += 1
                        conn.commit()
                    else:
                        _note(cur, oid, "Provider reports 'sent'. HELD: this check's handover switch is off in code, so "
                                        "postal handover is not treated as verified. Status unchanged.")
                        conn.commit()
                        summary["dispatched_status_held"] += 1
                elif result.outcome == "rejected":
                    _note(cur, oid, "Provider reports a returned/cancelled/invalid-address status after acceptance. "
                                    "HELD for a human; status and money unchanged.")
                    conn.commit()
                    summary["needs_review"] += 1
                    logger.warning(f"[Accepted Check] order {oid}: provider reports a rejection-type status; held.")
                else:   # accepted-in-pipeline, or unrecognised: leave it, look again later
                    _touch(cur, oid); conn.commit()
                    summary["still_accepted"] += 1
            except Exception as exc:  # noqa: BLE001
                try:
                    conn.rollback()
                except Exception:  # noqa: BLE001
                    pass
                summary["errors"] += 1
                logger.error(f"[Accepted Check] order {oid} failed and was rolled back: {type(exc).__name__}: {exc}")
    except Exception as exc:  # noqa: BLE001
        summary["errors"] += 1
        logger.error(f"[Accepted Check] pass failed: {type(exc).__name__}: {exc}")
    finally:
        try:
            if conn is not None:
                conn.close()
        except Exception:  # noqa: BLE001
            pass
    if summary["dispatched"] or summary["needs_review"] or summary["dispatched_status_held"]:
        logger.info(f"[Accepted Check] {summary}")
    return summary

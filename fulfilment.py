"""
fulfilment.py -- The one letter-fulfilment pipeline for TreeKey's bundled
lead-and-letter model: "one purchased lead includes one personalised
introduction letter printed and posted by TreeKey."

WHY THIS FILE EXISTS (read this before touching allocation code elsewhere):
Before this file, four different call sites (database.burn_lead_inventory,
database.confirm_reserved_lead_sale, database.record_lead_dispatch_and_burn,
database.redeem_free_lead_code) each called _queue_letter_dispatch directly,
and there was no single authoritative record of *who owns a claimed lead* --
the `leads` table itself has no buyer/contractor column. The only place
buyer_email was ever recorded was letter_dispatches.buyer_email, a queue
table never designed to double as an ownership registry.

This file introduces two new tables:

  lead_allocations   -- the authoritative "who owns this lead, and what
                         payment/entitlement produced that ownership" record.
                         One row per successful allocation. This is what
                         main.py's ownership checks query.

  letter_obligations -- supersedes letter_dispatches for anything allocated
                         through this module. letter_dispatches is NOT
                         touched, dropped, or migrated -- existing rows stay
                         exactly as they are (see MIGRATION NOTES below).

Both tables are created with CREATE TABLE IF NOT EXISTS, following the exact
idiom database.py already uses for every other table -- see database.py's
init_db(). Nothing here runs automatically against a live database: these
functions only execute when something explicitly calls them (init_fulfilment_schema,
or one of the allocation functions below) against a real connection, exactly
like every other function in database.py. No production migration has been
run by this work.

MIGRATION NOTES (ownership lookup over historical data):
Because lead_allocations only has rows for allocations made through this new
module, get_lead_owner() below falls back to letter_dispatches.buyer_email
(matched on lead_reference) for any lead claimed before this change shipped.
This preserves authorised access to historical purchases without requiring a
backfill migration. See docs/launch_checklist.md for the one-time backfill
that *would* let us retire that fallback later (optional, not required for
correctness).

STATE MODEL (section 4 of the brief -- separate fields, not one mega-enum):
  letter_obligations.status         -- coarse lifecycle, one of LETTER_STATUSES
  letter_obligations.is_dry_run     -- bool, independent of status
  letter_obligations.provider_accepted_at  -- set only on real provider acceptance
  letter_obligations.dispatched_at         -- set only on real dispatch confirmation
  letter_obligations.delivered_at          -- set only if a provider supplies delivery evidence
  letter_obligations.failed_at             -- confirmed rejection/failure
  letter_obligations.content_fingerprint   -- sha256 of the exact rendered letter used
  letter_obligations.idempotency_key       -- unique; stable business key for crash-safe retries

Acceptance != dispatch != delivery. A dry-run row must never get a real
dispatched_at/provider_accepted_at, and must never count toward a "sent"
total -- see mark_provider_result() below, which is the ONLY function
allowed to write those fields.
"""
from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass, field
from typing import Optional

logger = logging.getLogger("treekey-fulfilment")


# ---------------------------------------------------------------------------
# Pipeline selection -- added in response to a review pass on this session's
# earlier work, which correctly flagged that database.py's four allocation
# call sites called BOTH _queue_letter_dispatch (the OLD pipeline) AND
# fulfilment.create_allocation_and_obligation (this, the NEW pipeline) for
# every single sale, unconditionally. That is safe only as long as nothing
# ever acts on both queues for the same sale. It turns out something already
# does run the old queue automatically: main.py's run_full_autonomous_cycle
# calls database.process_pending_letter_dispatches once a day (an external
# scheduler hits /trigger-autonomous-cycle; see that route's own docstring,
# "the same thing the scheduler fires automatically once a day"). That is
# genuinely live, scheduled automation -- not hypothetical -- so "nothing
# currently processes the new obligations table" was true, but "therefore
# there's no double-dispatch risk" did not follow: the day something DOES
# start processing letter_obligations (worker.py, wired to any scheduler),
# both pipelines would be live and uncoordinated for the same sales.
#
# ACTIVE_PIPELINE (env var LETTER_DISPATCH_PIPELINE) makes "exactly one
# pipeline may act on a given sale" a property enforced by code, not by
# nobody having wired the second one up yet:
#   "legacy"     (default -- ZERO behaviour change from before this session
#                 for anything already deployed): database.py's four call
#                 sites write ONLY to letter_dispatches (_queue_letter_
#                 dispatch); fulfilment.create_allocation_and_obligation is
#                 NOT called, so letter_obligations never gets a new row.
#                 process_pending_letter_dispatches runs normally.
#                 worker.py's promote_*/run_batch functions refuse to run
#                 (see worker.py) -- there would be nothing for them to do
#                 anyway, but the refusal is an explicit, testable guarantee
#                 rather than an emergent one.
#   "fulfilment" : the reverse -- database.py's four call sites write ONLY
#                 to lead_allocations/letter_obligations; _queue_letter_
#                 dispatch is NOT called, so letter_dispatches never gets a
#                 new row (historical rows are untouched either way --
#                 fulfilment.get_lead_owner's fallback still reads them).
#                 process_pending_letter_dispatches refuses to run (see
#                 that function's own new guard in database.py). worker.py
#                 runs normally.
#
# Switching this is a real cutover decision -- see docs/launch_checklist.md
# item 2. The default is "legacy" specifically so that deploying this
# session's code changes nothing about what currently runs in production
# until a human deliberately sets LETTER_DISPATCH_PIPELINE=fulfilment.
ACTIVE_PIPELINES = ("legacy", "fulfilment")
LETTER_DISPATCH_PIPELINE_ENV = "LETTER_DISPATCH_PIPELINE"


def active_pipeline() -> str:
    """Reads LETTER_DISPATCH_PIPELINE fresh every call (deliberately not
    cached) so a config change takes effect on the next allocation/promotion
    call without a process restart. Unknown/unset values fall back to the
    safe default ('legacy') with a warning, never to 'fulfilment' -- an
    invalid config must never silently turn on the new pipeline."""
    import os
    value = os.getenv(LETTER_DISPATCH_PIPELINE_ENV, "legacy").strip().lower()
    if value not in ACTIVE_PIPELINES:
        logger.warning(f"[Fulfilment] Unknown {LETTER_DISPATCH_PIPELINE_ENV}={value!r}, falling back to 'legacy'.")
        return "legacy"
    return value


# ---------------------------------------------------------------------------
# 2026-09-18 review, Section 4: "Confirm that the public posting promise is
# controlled by executable configuration/feature gates -- not merely
# comments or launch-checklist wording."
#
# Before this, payments.py's PLANS dict had the "includes a posted
# introduction letter" sentence hardcoded directly into its static
# description strings, with the actual go-live condition living ONLY in a
# code comment (payments.py's own Sep 18 note) and docs/launch_checklist.md
# -- nothing would have stopped that copy going live the moment the file
# was deployed, regardless of whether real sending was actually possible.
# LETTER_SENDING_LIVE is the executable switch: payments.py/main.py call
# letter_sending_live() at render/request time (never cached, never baked
# into a module-level constant) and only include the posting-promise
# sentence when it's explicitly True. Defaults to False/off, matching the
# "missing configuration must fail safely" rule the rest of this session's
# work follows -- an operator must deliberately turn this on, the same way
# LETTER_DISPATCH_PIPELINE must be deliberately switched to 'fulfilment'.
#
# 2026-09-18 review, Section 3 (second pass): "Couple the public posting
# promise to the correct fulfilment pipeline and a configured, enabled real
# provider. Keep explicit launch approval as well. Test invalid
# configuration combinations so a flag alone cannot advertise an
# unavailable service." Before this fix, LETTER_SENDING_LIVE was the WHOLE
# decision -- an operator (or a misconfigured deploy) could set it true
# while LETTER_DISPATCH_PIPELINE was still 'legacy' (nothing would ever
# process the new obligations the promise implies), or while every
# LETTER_PROVIDER_* slot was empty/disabled (no provider could ever accept
# a send even if the pipeline were active) -- the flag alone was enough to
# show "we post a letter" copy with no code path that could ever make that
# true. letter_sending_live() below now ANDs three independent conditions,
# all of which must hold:
#   1. The explicit LETTER_SENDING_LIVE flag itself -- still required, on
#      purpose. Pipeline + provider being technically ready does NOT
#      auto-enable the public promise; an admin's deliberate go-live
#      decision is a separate fact from technical readiness, same
#      reasoning as the original design below.
#   2. active_pipeline() == 'fulfilment' -- the pipeline that actually
#      processes letter_obligations/provider sends. If still 'legacy',
#      nothing acts on the new pipeline's obligations at all (see
#      ACTIVE_PIPELINES above), so a promise gated only on the flag would
#      describe a service nothing is running.
#   3. _real_provider_configured_and_enabled() -- at least one
#      LETTER_PROVIDER_PRIMARY/BACKUP_1/BACKUP_2 slot is enabled AND
#      configured (real credentials present) AND NOT the fake_test
#      adapter. fake_test deliberately does not count here -- it exists
#      for local/dry-run testing (see letter_providers/fake_provider.py),
#      and a deploy that only has fake_test configured has no way to
#      actually post a real letter, so the public promise must not imply
#      one will be sent.
# These three are independent controls an operator can and should exercise
# in any order -- e.g. get the pipeline + a real provider configured and
# soak-tested with the promise still off, then flip LETTER_SENDING_LIVE
# last, once satisfied. Getting the config half right (e.g. provider set
# up but still on the legacy pipeline, or the flag flipped early by
# mistake) now fails safe to the promise staying OFF, not to it going live
# on a partial setup -- see tests/test_letter_promise_gate.py for the
# invalid-combination coverage.
# ---------------------------------------------------------------------------

LETTER_SENDING_LIVE_ENV = "LETTER_SENDING_LIVE"
_TRUTHY_ENV_VALUES = ("1", "true", "yes", "on")

# Provider adapter names (LetterProviderAdapter.name) that must never count
# as "a real provider" for the public-promise gate, even when configured
# and enabled -- see _real_provider_configured_and_enabled()'s docstring.
_NON_REAL_PROVIDER_ADAPTER_NAMES = ("fake_test",)


def _real_provider_configured_and_enabled() -> bool:
    """True only if at least one provider slot (primary/backup_1/backup_2,
    built fresh from the environment -- see letter_providers.registry.
    build_registry_from_env) is enabled, reports itself configured (real
    credentials present, not just a recognised provider kind), AND is not
    the fake/test adapter. Used ONLY to gate the public posting-promise
    copy below -- never to decide whether a real send should be attempted;
    that remains letter_providers.registry.ProviderRegistry.usable_slots(),
    called by the worker itself at send time, which correctly DOES allow
    fake_test (that's the whole point of the fake provider -- exercising
    the real send/fallback/reconciliation logic locally without a real
    vendor account).

    Local import, not a top-of-file one: letter_providers/registry.py
    itself does `import fulfilment` at its own top level (it needs
    active_pipeline()/logger), so a top-level import here would be
    circular. Importing inside the function, at call time, is safe --
    both modules are already fully loaded by the time this is ever
    called."""
    from letter_providers.registry import build_registry_from_env
    registry = build_registry_from_env()
    return any(
        slot.enabled
        and slot.adapter.is_configured()
        and getattr(slot.adapter, "name", "") not in _NON_REAL_PROVIDER_ADAPTER_NAMES
        for slot in registry.slots
    )


def letter_sending_live() -> bool:
    """Reads LETTER_SENDING_LIVE, LETTER_DISPATCH_PIPELINE and the
    provider registry fresh every call (same no-caching convention as
    active_pipeline()) and requires ALL THREE to hold -- see this
    function's module-level comment above for the full reasoning:
      1. the explicit LETTER_SENDING_LIVE flag (truthy -- see
         _TRUTHY_ENV_VALUES; unset/blank/anything else is False);
      2. active_pipeline() == 'fulfilment';
      3. _real_provider_configured_and_enabled() -- a real (non-fake_test)
         provider slot that is both enabled and actually configured.
    Any one of these being false makes this False, fail-safe -- there is
    no combination of the other two that can substitute for the explicit
    flag, and no way to set the flag alone and have this return True."""
    import os
    explicit_flag = os.getenv(LETTER_SENDING_LIVE_ENV, "").strip().lower() in _TRUTHY_ENV_VALUES
    if not explicit_flag:
        return False
    if active_pipeline() != "fulfilment":
        return False
    if not _real_provider_configured_and_enabled():
        return False
    return True


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------

LETTER_STATUSES = (
    "blocked_missing_data",   # can't queue at all -- no address/reference
    "pending_approval",       # waiting on contractor template/business-detail approval
    "pending_funding",        # approved, held by the funding gate
    "ready",                  # eligible for submission, not yet attempted
    "submitting",             # a worker has claimed it and is calling a provider now
    "provider_accepted",      # provider confirmed acceptance (not yet "dispatched")
    "dispatched",             # provider confirmed the physical item left their system
    "delivered",              # only ever set if a provider gives delivery evidence
    "failed",                 # confirmed rejection/non-acceptance by the provider
    "unknown",                # ambiguous outcome (timeout/crash/malformed response) -- needs reconciliation, NOT auto-retry
    "suppressed",             # blocked by the suppression/objection registry
    "cancelled",              # administratively cancelled (e.g. refund before submission)
    "dry_run",                # completed in dry-run/test mode; never a real send
)

# Statuses from which an automatic fallback-to-next-provider or safe retry is
# allowed. Deliberately excludes "unknown" and "submitting" -- see
# providers.registry.attempt_send's docstring for why.
RETRYABLE_STATUSES = ("ready", "failed")


def init_fulfilment_schema(cur) -> None:
    """Idempotent CREATE TABLE IF NOT EXISTS block, called once from
    database.init_db() (see the one-line addition there). Never called
    automatically against a live/production database by anything in this
    session's work -- it only runs when init_db() itself runs, exactly like
    every other table in the app."""
    cur.execute("""
        CREATE TABLE IF NOT EXISTS lead_allocations (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            lead_reference TEXT NOT NULL,
            lead_id TEXT,
            buyer_email TEXT NOT NULL,
            allocation_type TEXT NOT NULL,   -- single_purchase | subscription_dispatch | free_lead | admin_grant
            source_payment_ref TEXT,         -- Stripe session/payment_intent id, subscription id, free-code, or admin note
            stripe_event_id TEXT,            -- the specific webhook event that caused this, when applicable (idempotency)
            idempotency_key TEXT NOT NULL UNIQUE,
            created_at TIMESTAMPTZ DEFAULT NOW()
        );
        CREATE INDEX IF NOT EXISTS idx_lead_allocations_buyer ON lead_allocations(buyer_email);
        -- 2026-09-22 handoff, "atomic allocation enforced at the database
        -- level": every current caller (database.py's burn_lead_inventory,
        -- confirm_reserved_lead_sale, record_lead_dispatch_and_burn,
        -- redeem_free_lead_code) already wins an atomic
        -- UPDATE leads SET status='claimed' ... RETURNING compare-and-swap
        -- on the SAME cursor/transaction before ever reaching this table,
        -- which already serialises concurrent claims of one lead per row
        -- lock -- so this index should never actually reject a live
        -- INSERT. It exists as a hard backstop against a future caller
        -- (an admin tool, a script, a call added later) that creates an
        -- allocation without that upstream guard -- replaces the old
        -- plain (non-unique) idx_lead_allocations_reference index, since a
        -- unique index already serves every lookup the plain one did.
        CREATE UNIQUE INDEX IF NOT EXISTS idx_lead_allocations_reference_unique ON lead_allocations(lead_reference);

        CREATE TABLE IF NOT EXISTS letter_obligations (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            allocation_id UUID REFERENCES lead_allocations(id),
            lead_reference TEXT NOT NULL,
            address TEXT NOT NULL,
            applicant_name TEXT,
            buyer_email TEXT NOT NULL,
            sale_context TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending_approval',
            is_dry_run BOOLEAN NOT NULL DEFAULT TRUE,
            content_fingerprint TEXT,
            approved_content_html TEXT,  -- 2026-09-18 review, Section 7: the EXACT rendered
            -- HTML approved at promote_pending_approvals time (matching content_fingerprint
            -- above). worker.run_batch sends THIS, verbatim, never a fresh render -- see that
            -- function's own docstring for why re-rendering at send time is the bug this
            -- column closes.
            template_version INT,
            idempotency_key TEXT NOT NULL UNIQUE,
            provider_name TEXT,
            provider_reference TEXT,
            attempts INT NOT NULL DEFAULT 0,
            last_error TEXT,
            provider_accepted_at TIMESTAMPTZ,
            dispatched_at TIMESTAMPTZ,
            delivered_at TIMESTAMPTZ,
            failed_at TIMESTAMPTZ,
            suppressed_at TIMESTAMPTZ,
            suppressed_reason TEXT,
            claimed_by_worker TEXT,          -- set atomically when a worker begins submission, cleared on completion
            claimed_at TIMESTAMPTZ,
            created_at TIMESTAMPTZ DEFAULT NOW(),
            updated_at TIMESTAMPTZ DEFAULT NOW()
        );
        CREATE INDEX IF NOT EXISTS idx_letter_obligations_status ON letter_obligations(status);
        CREATE INDEX IF NOT EXISTS idx_letter_obligations_reference ON letter_obligations(lead_reference);

        CREATE TABLE IF NOT EXISTS payment_allocation_reconciliation (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            stripe_event_id TEXT,
            stripe_reference TEXT,
            buyer_email TEXT,
            lead_reference TEXT,
            reason TEXT NOT NULL,
            resolved BOOLEAN NOT NULL DEFAULT FALSE,
            resolved_by TEXT,
            resolved_at TIMESTAMPTZ,
            resolution_note TEXT,
            created_at TIMESTAMPTZ DEFAULT NOW()
        );
        CREATE INDEX IF NOT EXISTS idx_reconciliation_unresolved ON payment_allocation_reconciliation(resolved) WHERE resolved = FALSE;
    """)


class AllocationPersistenceError(Exception):
    """Raised by database.py's shared dispatch helper (_dispatch_via_active_pipeline)
    when a sale's UPDATE...RETURNING genuinely matched a row (a real
    reservation/free-code/quota slot was consumed) but persisting the
    letter obligation for it then failed -- a DB write error, not "no such
    reservation". Section 3 of the 2026-09-18 review: this MUST be
    distinguishable from the "nothing matched" case, because a caller (the
    Stripe webhook handler above all) that cannot tell the two apart will
    treat a transient DB failure the same as a genuinely lost/stolen
    reservation -- silently auto-refunding a customer whose payment and
    reservation were both completely valid, for a problem a retry might
    have fixed. See payments.py's handle_stripe_webhook and
    docs/launch_checklist.md item 3 for how callers must handle this."""


class LeadAlreadyAllocatedError(Exception):
    """Raised by create_allocation_and_obligation when the database's
    UNIQUE index on lead_allocations.lead_reference (idx_lead_allocations_
    reference_unique, see init_fulfilment_schema) rejects an INSERT
    because this exact lead_reference was already allocated under a
    DIFFERENT idempotency_key -- i.e. some other buyer/event already
    claimed this lead.

    2026-09-22 handoff, "atomic allocation enforced at the database
    level": every current caller of this function first wins an atomic
    UPDATE leads SET status='claimed' ... RETURNING compare-and-swap on
    the SAME cursor/transaction (see database.py's burn_lead_inventory,
    confirm_reserved_lead_sale, record_lead_dispatch_and_burn,
    redeem_free_lead_code) -- Postgres's row lock on that UPDATE already
    serialises concurrent claims of one lead, so this exception should be
    unreachable via any call path that exists today. It is a fail-safe
    database-level backstop, not a documented user-facing outcome: never
    caught and swallowed into a silent success. It is deliberately NOT a
    subclass of AllocationPersistenceError, but _dispatch_via_active_
    pipeline's generic `except Exception` re-wraps it into one anyway --
    which is the correct behaviour, since every existing caller already
    treats AllocationPersistenceError as 'roll back, alert an admin, do
    not refund, do not mark the sale fulfilled' (see that class's own
    docstring), which is exactly right for a state that should never
    happen and needs a human to look at it."""


# ---------------------------------------------------------------------------
# Allocation + obligation creation (the transactional core)
# ---------------------------------------------------------------------------

@dataclass
class AllocationResult:
    ok: bool
    allocation_id: Optional[str] = None
    obligation_id: Optional[str] = None
    obligation_status: Optional[str] = None
    reason: Optional[str] = None


def make_idempotency_key(*parts: str) -> str:
    """A stable, deterministic key from the parts that uniquely identify one
    allocation event -- e.g. (allocation_type, lead_reference, source_payment_ref).
    Same inputs always produce the same key, so calling the allocation
    function twice for the same real-world event (a retried webhook, a
    re-run admin action) is safe and produces exactly one row -- the second
    call hits the UNIQUE constraint and is treated as "already done", not as
    a new allocation. This mirrors standalone_mailer.mailer's request_key
    pattern (see that file's Mailer.send docstring)."""
    joined = "|".join(p.strip().lower() for p in parts if p)
    return hashlib.sha256(joined.encode()).hexdigest()[:40]


def get_lead_owner(cur, lead_reference: str) -> Optional[str]:
    """The single source of truth for 'who owns this claimed lead'. Checks
    the new authoritative table first, then falls back to
    letter_dispatches.buyer_email for leads claimed before this module
    existed (see MIGRATION NOTES at the top of this file). Returns the
    lower-cased buyer email, or None if no owner can be established --
    callers must treat None as 'deny', never as 'allow'."""
    cur.execute(
        "SELECT buyer_email FROM lead_allocations WHERE lead_reference = %s ORDER BY created_at DESC LIMIT 1;",
        (lead_reference,),
    )
    row = cur.fetchone()
    if row and row[0]:
        return row[0].strip().lower()

    # Historical fallback -- see MIGRATION NOTES.
    cur.execute(
        "SELECT buyer_email FROM letter_dispatches WHERE lead_reference = %s AND buyer_email IS NOT NULL "
        "ORDER BY created_at ASC LIMIT 1;",
        (lead_reference,),
    )
    row = cur.fetchone()
    if row and row[0]:
        return row[0].strip().lower()
    return None


def create_allocation_and_obligation(
    cur,
    *,
    lead_reference: str,
    lead_id: Optional[str],
    address: Optional[str],
    applicant_name: Optional[str],
    buyer_email: str,
    allocation_type: str,
    sale_context: str,
    source_payment_ref: Optional[str],
    stripe_event_id: Optional[str] = None,
    template_approved: bool = False,
    template_version: Optional[int] = None,
) -> AllocationResult:
    """The one place a new allocation + its letter obligation get created.
    Call this on the SAME cursor/transaction as the sale itself, before the
    caller's own conn.commit() -- exactly the discipline database.py's
    existing _queue_letter_dispatch already documents (database.py:5303-5306)
    and this function preserves.

    Idempotent: if idempotency_key already exists, returns the EXISTING
    allocation/obligation instead of raising or duplicating -- this is what
    makes "handle repeat and out-of-order Stripe events without duplicate
    allocations" (section 2) actually true, not just documented.

    Missing address/reference produces a 'blocked_missing_data' obligation
    row (loud, queryable, admin-visible) instead of _queue_letter_dispatch's
    current behaviour of only logging and returning nothing durable."""
    idem_key = make_idempotency_key(allocation_type, lead_reference, source_payment_ref or buyer_email)

    cur.execute("SELECT id FROM lead_allocations WHERE idempotency_key = %s;", (idem_key,))
    existing = cur.fetchone()
    if existing:
        cur.execute(
            "SELECT id, status FROM letter_obligations WHERE allocation_id = %s LIMIT 1;",
            (existing[0],),
        )
        ob = cur.fetchone()
        logger.info(f"[Fulfilment] Allocation for {lead_reference!r} ({allocation_type}) already exists "
                    f"(idempotency_key={idem_key}) -- returning existing record, not duplicating.")
        return AllocationResult(
            ok=True, allocation_id=str(existing[0]),
            obligation_id=str(ob[0]) if ob else None,
            obligation_status=ob[1] if ob else None,
            reason="already_allocated",
        )

    cur.execute("""
        INSERT INTO lead_allocations (lead_reference, lead_id, buyer_email, allocation_type,
                                       source_payment_ref, stripe_event_id, idempotency_key)
        VALUES (%s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT DO NOTHING
        RETURNING id;
    """, (lead_reference, lead_id, buyer_email.strip().lower(), allocation_type,
          source_payment_ref, stripe_event_id, idem_key))
    inserted = cur.fetchone()
    if inserted is None:
        # ON CONFLICT DO NOTHING (no target column) absorbs a violation of
        # EITHER unique constraint on this table -- idempotency_key or the
        # new lead_reference index -- without raising and poisoning the
        # transaction. Tell the two apart with a follow-up SELECT: same
        # idempotency_key means this exact call raced the pre-check SELECT
        # above (same event, retried/concurrent) -- return the winning row
        # exactly like the pre-check branch does. Any other row already
        # sitting on this lead_reference means a genuinely different
        # buyer/event got there first -- see LeadAlreadyAllocatedError's
        # own docstring for why every current caller should never actually
        # reach this.
        cur.execute(
            "SELECT id, idempotency_key FROM lead_allocations WHERE lead_reference = %s;",
            (lead_reference,),
        )
        winner = cur.fetchone()
        if winner and winner[1] == idem_key:
            cur.execute(
                "SELECT id, status FROM letter_obligations WHERE allocation_id = %s LIMIT 1;",
                (winner[0],),
            )
            ob = cur.fetchone()
            logger.info(f"[Fulfilment] Allocation for {lead_reference!r} ({allocation_type}) raced onto an "
                        f"existing idempotency_key={idem_key} between the pre-check and the INSERT -- "
                        f"returning the existing record, not duplicating.")
            return AllocationResult(
                ok=True, allocation_id=str(winner[0]),
                obligation_id=str(ob[0]) if ob else None,
                obligation_status=ob[1] if ob else None,
                reason="already_allocated",
            )
        raise LeadAlreadyAllocatedError(
            f"lead_reference={lead_reference!r} is already allocated under a different "
            f"idempotency_key -- refusing to create a second allocation for "
            f"buyer_email={buyer_email.strip().lower()!r} (allocation_type={allocation_type!r})."
        )
    allocation_id = inserted[0]

    if not address or not lead_reference:
        # Loud AND durable: an admin can query letter_obligations for
        # status='blocked_missing_data' -- this is what "actionable
        # blocked/error state, not an apparently completed sale with only a
        # log message" (section 2) means in practice.
        logger.error(f"[Fulfilment] Allocation {allocation_id} for reference={lead_reference!r} has no "
                      f"address -- creating a BLOCKED letter obligation for manual follow-up.")
        obligation_status = "blocked_missing_data"
    elif template_approved:
        obligation_status = "pending_funding"
    else:
        obligation_status = "pending_approval"

    ob_idem_key = make_idempotency_key("obligation", allocation_type, lead_reference, source_payment_ref or buyer_email)
    cur.execute("""
        INSERT INTO letter_obligations (allocation_id, lead_reference, address, applicant_name, buyer_email,
                                         sale_context, status, template_version, idempotency_key)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
        RETURNING id;
    """, (allocation_id, lead_reference, address or "", applicant_name, buyer_email.strip().lower(),
          sale_context, obligation_status, template_version, ob_idem_key))
    obligation_id = cur.fetchone()[0]

    return AllocationResult(ok=True, allocation_id=str(allocation_id), obligation_id=str(obligation_id),
                             obligation_status=obligation_status)


def record_reconciliation_issue(cur, *, reason: str, stripe_event_id: Optional[str] = None,
                                 stripe_reference: Optional[str] = None, buyer_email: Optional[str] = None,
                                 lead_reference: Optional[str] = None) -> str:
    """Stripe charged, but local allocation failed (DB error, app crash,
    whatever). This is the durable, admin-visible record of that gap --
    section 2's 'do not automatically charge again' means this is the ONLY
    thing that happens automatically; a human resolves it via
    resolve_reconciliation_issue below."""
    cur.execute("""
        INSERT INTO payment_allocation_reconciliation (stripe_event_id, stripe_reference, buyer_email, lead_reference, reason)
        VALUES (%s, %s, %s, %s, %s) RETURNING id;
    """, (stripe_event_id, stripe_reference, buyer_email, lead_reference, reason))
    issue_id = str(cur.fetchone()[0])
    logger.error(f"[Fulfilment] RECONCILIATION NEEDED ({issue_id}): {reason} "
                 f"(stripe_event={stripe_event_id}, buyer={buyer_email}, lead={lead_reference})")
    return issue_id


def has_unresolved_reconciliation_issue(cur, *, stripe_event_id: Optional[str] = None,
                                         stripe_reference: Optional[str] = None) -> bool:
    """2026-09-18 review, Section 4 (second pass): "Test successful Stripe
    payment followed by database failure and then a delayed retry after
    reservation expiry. Reconcile using durable purchase/payment identity."

    Answers "is there already an OPEN (unresolved) reconciliation record
    for this exact payment" -- by its durable Stripe identity
    (stripe_event_id, the webhook event id; and/or stripe_reference, the
    checkout session/reservation token that record_reconciliation_issue
    was given), never by the lead's own current, mutable status. That
    distinction is the whole point: after database.confirm_reserved_lead_
    sale raises AllocationPersistenceError (a genuine payment + a genuine
    reservation, but the local write failed), the LEAD's row goes back to
    'reserved' (rolled back) and, if a delayed retry arrives after
    RESERVATION_RELEASE_MINUTES, the reservation is swept to 'new' by
    release_expired_reservations -- at that point confirm_reserved_lead_
    sale's own UPDATE can no longer find anything to match, and returns
    None on retry, exactly as it would for a reservation that was
    genuinely never confirmed in the first place. Without this check,
    payments.py's webhook handler cannot tell those two situations apart
    from lead/reservation state alone, and would auto-refund a payment
    that already has an admin alerted, open reconciliation issue asking a
    human to complete the sale by hand -- contradicting that alert and
    risking a refund a human is already in the process of resolving
    differently. Matched on EITHER id (not both required) because
    record_reconciliation_issue's callers pass whichever ids they have on
    hand for a given failure -- see payments.py's two AllocationPersistenceError
    handlers, both of which pass the webhook's event_id as stripe_event_id
    and the checkout-session/reservation token as stripe_reference.

    Only ever used to decide "should this fall through to the automatic
    refund path", never to resolve or act on the issue itself -- resolving
    remains a deliberate human action via resolve_reconciliation_issue."""
    if not stripe_event_id and not stripe_reference:
        return False
    cur.execute("""
        SELECT 1 FROM payment_allocation_reconciliation
        WHERE resolved = FALSE
          AND ((%s IS NOT NULL AND stripe_event_id = %s)
               OR (%s IS NOT NULL AND stripe_reference = %s))
        LIMIT 1;
    """, (stripe_event_id, stripe_event_id, stripe_reference, stripe_reference))
    return cur.fetchone() is not None


def resolve_reconciliation_issue(cur, issue_id: str, *, resolved_by: str, note: str) -> bool:
    cur.execute("""
        UPDATE payment_allocation_reconciliation
        SET resolved = TRUE, resolved_by = %s, resolved_at = NOW(), resolution_note = %s
        WHERE id = %s AND resolved = FALSE
        RETURNING id;
    """, (resolved_by, note, issue_id))
    return cur.fetchone() is not None


# ---------------------------------------------------------------------------
# Provider-result recording -- the ONLY functions allowed to move an
# obligation between submitting/accepted/dispatched/failed/unknown, and the
# only place dry-run vs real outcomes are told apart.
# ---------------------------------------------------------------------------

def claim_for_submission(cur, obligation_id: str, worker_id: str) -> bool:
    """Atomic claim: only succeeds if the row is 'ready' and not already
    claimed. This is what section 6's 'atomic worker claims' means --
    replaces the old process_pending_letter_dispatches' unprotected SELECT.
    Returns False if another worker already has it (or it isn't ready),
    which the caller must treat as 'skip, not an error'."""
    cur.execute("""
        UPDATE letter_obligations
        SET status = 'submitting', claimed_by_worker = %s, claimed_at = NOW(), updated_at = NOW()
        WHERE id = %s AND status = 'ready'
        RETURNING id;
    """, (worker_id, obligation_id))
    return cur.fetchone() is not None


def mark_provider_result(cur, obligation_id: str, *, outcome: str, is_dry_run: bool,
                          provider_name: Optional[str] = None, provider_reference: Optional[str] = None,
                          error: Optional[str] = None, content_fingerprint: Optional[str] = None) -> None:
    """outcome is one of: 'accepted', 'dispatched', 'failed', 'unknown', 'dry_run'.
    This function is deliberately the ONLY writer of provider_accepted_at /
    dispatched_at / delivered_at, and it is the only place that decides
    whether a real timestamp gets set -- is_dry_run=True NEVER sets
    provider_accepted_at or dispatched_at, regardless of what outcome says,
    closing the exact gap in the old process_pending_letter_dispatches
    (database.py:5381-5388) where a dry-run 'ok=True' result set a real
    sent_at and incremented a 'sent' counter."""
    if outcome not in ("accepted", "dispatched", "failed", "unknown", "dry_run"):
        raise ValueError(f"Unknown outcome {outcome!r}")

    fields = {"attempts": "attempts + 1", "updated_at": "NOW()"}
    params = []
    sets = ["attempts = attempts + 1", "updated_at = NOW()", "provider_name = %s", "provider_reference = %s",
            "last_error = %s"]
    params.extend([provider_name, provider_reference, error])

    if content_fingerprint:
        sets.append("content_fingerprint = %s")
        params.append(content_fingerprint)

    if is_dry_run:
        sets.append("status = 'dry_run'")
        sets.append("is_dry_run = TRUE")
    elif outcome == "accepted":
        sets += ["status = 'provider_accepted'", "provider_accepted_at = NOW()"]
    elif outcome == "dispatched":
        sets += ["status = 'dispatched'", "dispatched_at = NOW()"]
    elif outcome == "failed":
        sets += ["status = 'failed'", "failed_at = NOW()"]
    elif outcome == "unknown":
        # Deliberately does NOT flip back to 'ready'. An unknown outcome
        # must be reconciled by a human/admin action, never auto-retried --
        # see letter_providers.registry for the enforcement of this.
        sets.append("status = 'unknown'")

    sql = f"UPDATE letter_obligations SET {', '.join(sets)} WHERE id = %s;"
    params.append(obligation_id)
    cur.execute(sql, params)


def mark_suppressed(cur, obligation_id: str, reason: str) -> None:
    cur.execute("""
        UPDATE letter_obligations
        SET status = 'suppressed', suppressed_at = NOW(), suppressed_reason = %s, updated_at = NOW()
        WHERE id = %s;
    """, (reason, obligation_id))


def mark_cancelled(cur, obligation_id: str, reason: str) -> bool:
    """Section 3: 'a refund does not automatically cancel a letter already
    submitted.' Only cancellable from states that haven't reached the
    provider yet."""
    cur.execute("""
        UPDATE letter_obligations
        SET status = 'cancelled', last_error = %s, updated_at = NOW()
        WHERE id = %s AND status IN ('pending_approval', 'pending_funding', 'ready', 'blocked_missing_data')
        RETURNING id;
    """, (reason, obligation_id))
    return cur.fetchone() is not None

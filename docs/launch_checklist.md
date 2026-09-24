# Launch checklist: genuinely outstanding decisions

This is a list of decisions that block turning real sending on -- not a
list of things this session left half-built. Everything listed as "done"
elsewhere in [`handoff.md`](handoff.md) is done (built, tested, verified
zero-regression). This file is specifically the *other* list: things a
human (Nick, and where marked, a UK data-protection adviser) has to decide
or do before this can go live, because they are not code problems.

Nothing on this list was invented or assumed by this session -- each item
either came directly from your own instructions, or is a genuine gap this
session found while implementing and is refusing to silently paper over.

## Must decide before ANY real letter is sent

1. **Provider selection and account setup.** No postal provider has a real,
   funded account. `letter_providers/stannp_provider.py` is adapted from
   `standalone_mailer/mailer.py`'s already-unverified integration -- its
   endpoint/field names have not been re-checked against Stannp's current
   API docs this session, and no live account exists to test against.
   `intelliprint_provider.py` and `postworks_provider.py` are honest stubs
   (`is_configured()` always `False`, `send()` raises `NotImplementedError`)
   -- see the note on `letter_provider.py` below for why these three
   candidates specifically. **Decision needed:** which provider(s) to
   actually open an account with, and someone needs to verify the Stannp
   integration against live docs (or build a different one) before it can
   be trusted with real mail.

2. **Funding reconciliation depth.** `funding.py`'s `hold` mode (the
   recommended default) requires an admin to manually call
   `confirm_budget()` -- there's no automated tie-back to actual Stripe
   payouts or bank balance, and nothing currently calls `gate.spend()`
   after a real send (see operator_guide.md section 2). **Decision
   needed:** is manual budget confirmation the accepted v1 process, or is
   automated Stripe-payout reconciliation required before launch?

2a. **CLOSED, 2026-09-18 review, Section 4 (second pass): payment/
    allocation reconciliation edge cases.** "Test successful Stripe payment
    followed by database failure and then a delayed retry after
    reservation expiry. Reconcile using durable purchase/payment identity.
    Also handle the case where the reconciliation-record write fails
    during a database outage: return a retryable response and do not mark
    the event fulfilled." Both genuinely tested end to end (not just
    described), against the real `payments.handle_stripe_webhook` and
    `database`/`fulfilment` reconciliation functions:
    - **Successful payment, then a local DB write failure, then a delayed
      retry after the reservation has since expired:** already partly
      protected before this pass (`database.confirm_reserved_lead_sale`
      raises `fulfilment.AllocationPersistenceError` instead of silently
      losing the sale, and a durable `payment_allocation_reconciliation`
      row is recorded -- first pass of this same review). What was
      genuinely missing: once `RESERVATION_RELEASE_MINUTES` passes and the
      reservation is swept back to `'new'` (see `database.
      release_expired_reservations`), a delayed webhook retry can no
      longer tell "this is the SAME failed sale catching up" apart from
      "this reservation was genuinely never confirmed" using lead/
      reservation state alone -- both look identical (`confirm_reserved_
      lead_sale` returns `None` either way). Before this fix, that
      collapsed to the SAME auto-refund path as a genuinely lost
      reservation -- which contradicts the already-open, admin-alerted
      reconciliation record asking a human to complete the sale by hand,
      and risks a double-refund if that human already acted. Fixed with
      `fulfilment.has_unresolved_reconciliation_issue`/`database.
      has_unresolved_payment_reconciliation_issue`, matched on the
      DURABLE Stripe identity (the webhook's own `event_id`, and the
      checkout-session/reservation token as `stripe_reference`) rather
      than the lead's mutable status -- checked before the auto-refund
      branch; an open match skips the refund entirely and returns the
      same retryable shape as the original persistence-failure branch.
      Tested end to end in `tests/test_payments_webhook.py`'s
      `TestDelayedRetryAfterReservationExpiryReconcilesByDurableIdentity`
      (drives the exact two-delivery sequence -- first delivery raises
      `AllocationPersistenceError` and records the issue, second delivery
      models the post-expiry retry and asserts no refund fires), plus unit
      coverage of the lookup function itself in `tests/test_fulfilment.py`
      and of the self-contained DB wrapper's fail-safe behaviour in
      `tests/test_reconciliation.py`.
    - **The reconciliation-record write itself failing during a database
      outage:** this was already correctly handled by the pre-existing
      code (`database.record_payment_reconciliation_issue` is documented
      best-effort, returning `None` rather than raising on its own write
      failure, and the webhook handler's retryable response/no-fulfilled-
      mark never depended on that write having succeeded) -- confirmed,
      not changed, and pinned down with an explicit regression test
      (`TestReconciliationRecordWriteFailsDuringOutage`) so it can't
      silently regress. The NEW `has_unresolved_payment_reconciliation_
      issue` check added by this pass has the same shape of failure mode
      (its own DB read can fail during an outage) and is handled the same
      way, but with the fail-safe direction deliberately INVERTED from
      most read helpers in this codebase: it defaults to `True` (assume an
      issue might exist) rather than `False`, specifically so an
      unanswerable check can never fall through to an automatic refund --
      see that function's own docstring in `database.py` for the full
      reasoning, and `TestReconciliationCheckItselfFailsDuringOutage` for
      the test.
    What this does NOT cover (out of scope for this pass, genuinely
    untested): real Stripe's actual retry timing/backoff behaviour (all
    "delayed retry" tests model it by calling the webhook handler twice
    with the same event id, in-process -- there is no real Stripe sandbox
    integration in this session's work, see item 1); and what an admin
    should actually DO to resolve an open reconciliation issue once
    Stripe's own retry window (~3 days) has passed with nothing resolving
    it automatically -- `fulfilment.resolve_reconciliation_issue` exists
    for that, direct-DB/script-only, same as the other manual-admin-action
    precedents in this checklist (item 10).

3. **Article 14 disclosure timing.** Carried over unresolved from the
   corrected legal assessment earlier this session: whether disclosing via
   a posted letter *after* a lead is sold satisfies Article 14's "first
   disclosure" timing requirement, versus needing disclosure at first
   processing (when the lead is scraped/discovered, before any sale).
   **Decision needed: requires a UK data-protection adviser**, not
   something resolvable in code.

4. **Unsold-lead policy.** Whether every unsold/never-purchased lead record
   also requires a postal Article 14 notice (versus relying on a
   disproportionate-effort exception, which has specific, unmet conditions
   -- see the earlier legal assessment). This session's fulfilment pipeline
   only creates a letter obligation when a lead is actually purchased/
   dispatched/granted -- **it does not send anything for unsold leads**,
   which is consistent with treating this as unresolved rather than
   assuming an exception applies. **Decision needed: requires the same
   legal review as item 3.**

5. **Copy go-live gate.** UPDATED 2026-09-18 review, Section 4: this is no
   longer just a checklist/wording matter -- it is now an executable gate.
   `fulfilment.letter_sending_live()` reads the `LETTER_SENDING_LIVE` env
   var fresh on every call (default: unset/false). `payments.plan_description()`
   and `payments.plan_roi()` are the ONLY places the "includes one printed &
   posted introduction letter" sentence is assembled -- every plan's raw
   `PLANS[...]["description"]`/`["real_world_roi"]` dict entry has that
   sentence stripped out into a separate `letter_suffix`/`roi_letter_suffix`
   key, appended only when `letter_sending_live()` is True. All known
   customer-facing read sites (marketplace/subscription tier cards in
   `main.py`, the single-lead checkout product description and its live-price
   variant in `payments.py`) now go through these two functions rather than
   reading the dict directly -- see `tests/test_letter_promise_gate.py` for
   both the disabled and enabled journeys. A pre-existing `payments.py` code
   comment claims a "Sep 12 2026" rule motivated removing this copy
   previously -- that comment is an unverified historical claim, not
   confirmed instruction (see Section 8 of the 2026-09-18 review); this
   executable gate is what actually enforces the underlying protection now,
   regardless of what any comment says.

   **UPDATED 2026-09-18 review, Section 3 (second pass): "Couple the public
   posting promise to the correct fulfilment pipeline and a configured,
   enabled real provider. Keep explicit launch approval as well. Test
   invalid configuration combinations so a flag alone cannot advertise an
   unavailable service."** The original version of this gate (previous
   paragraph) was ONLY the explicit flag -- which meant `LETTER_SENDING_
   LIVE=true` alone, on a deploy that was still on the `legacy` pipeline or
   had no provider configured at all, would show "we post a letter" copy
   with literally nothing behind it able to make that true. `fulfilment.
   letter_sending_live()` now ANDs three independent conditions, all
   required:
     1. the explicit `LETTER_SENDING_LIVE` flag (unchanged from above --
        still required, technical readiness never auto-enables the public
        promise on its own);
     2. `fulfilment.active_pipeline() == 'fulfilment'` (item 2's pipeline
        switch -- the pipeline that actually processes `letter_obligations`
        and attempts provider sends; `legacy` runs nothing on the new
        tables at all);
     3. at least one `LETTER_PROVIDER_PRIMARY`/`_BACKUP_1`/`_BACKUP_2` slot
        is enabled AND reports itself configured (real credentials
        present) AND is not the `fake_test` adapter -- see
        `letter_providers/registry.py`. `fake_test` deliberately never
        counts: it exists for local/dry-run testing, and a deploy with
        only that configured cannot actually post a letter.
   Every invalid two-out-of-three combination is tested explicitly in
   `tests/test_letter_promise_gate.py`'s `TestInvalidConfigurationCombinat
   ionsLeavePromiseOff` class (7 tests): flag on + legacy pipeline (both
   explicit and the real unset-defaults-to-legacy shape), flag on +
   correct pipeline + no provider configured, flag on + correct pipeline +
   only `fake_test` configured, flag on + a real provider kind named but
   incomplete credentials (not actually configured), and the reverse --
   pipeline and provider both genuinely ready but the flag itself left
   off. All leave the promise off; only all three together turn it on
   (also tested, as the class's positive control). Today, of the three
   provider adapters in `letter_providers/`, only `stannp` can ever report
   itself configured at all -- `intelliprint`/`postworks` are unimplemented
   placeholders hard-coded to always report unconfigured (see their own
   module docstrings) -- so in practice this condition currently requires
   `LETTER_PROVIDER_PRIMARY=stannp` (or a backup slot) plus
   `STANNP_API_KEY`/`STANNP_TEMPLATE_ID`, and Stannp itself is still an
   unverified-against-live-API placeholder (see item 1 above) -- so no
   real combination of config can make this gate true with a genuinely
   working provider yet. That remains exactly as unresolved as items 1-2
   above; this section only makes sure the PUBLIC COPY can never get
   ahead of that reality by way of the flag alone.

6. **Address-release gate.** 2026-09-18 review, Section 5: "Confirm that
   the separate address-release policy gate was implemented ... Do not
   treat payment, funding or provider acceptance as legal approval to
   disclose." `address_release.py` is now that separate gate --
   `address_release_live()` reads `ADDRESS_RELEASE_LIVE` fresh on every
   call (default: unset/false, addresses redacted), completely
   independent of `require_lead_ownership` (payment/ownership),
   `funding.py` (money reserved/spent) and provider acceptance -- none of
   those three is read by this gate, deliberately, per the instruction
   above. Wired into every known contractor/customer-facing route that
   renders a specific lead's exact address: `/generate-letter/{lead_id}`,
   `/generate-street-flyer/{lead_id}` (whole route refused when off --
   see below), `/street-view/{reference}` (the redirect URL itself would
   otherwise bake the address into a shareable link), `/dashboard`,
   `/my-leads` (both branches), `/free-dashboard`, and the two
   notifications.py transactional emails sent right after a purchase or a
   free-tier grant (`_send_purchased_lead_email_inner`,
   `_send_free_lead_granted_email_inner` -- these bypass the web app
   entirely, straight to an inbox, which is exactly the kind of
   "alternative route" the instruction called out). Admin-only views
   (basic-auth-gated `/admin/*`, cron-secret-gated scanner endpoints) are
   a separate, pre-existing trust boundary and are deliberately NOT
   redacted -- see `address_release.py`'s own module docstring for the
   reasoning. Tested both disabled and enabled for three representative
   routes end-to-end (letter preview, street flyer, street-view redirect)
   plus both transactional emails -- see `tests/test_address_release_gate.py`.
   **This gate exists precisely because item 3 above (Article 14
   disclosure timing) is unresolved** -- flipping `ADDRESS_RELEASE_LIVE=true`
   is a decision that should follow that legal review, not precede it, and
   is completely separate from turning on `LETTER_SENDING_LIVE` (item 5):
   TreeKey could show addresses to contractors without ever posting a
   letter, or post letters without ever showing the address on-screen --
   the two flags don't imply each other.

   **Known gap -- CLOSED, 2026-09-18 review, Section 1 (second pass):**
   `letter_content.approve_template` now has a wired UI:
   `/letter-settings` (setup), `/letter-settings/preview`, and
   `/letter-settings/approve` in `main.py`. The fingerprint is always
   computed server-side from the settings row actually stored under the
   authenticated session's own contractor_email -- never from a
   client-supplied value -- see `address_release.py`'s module docstring
   (the "CONTRACTOR-APPROVAL UI -- BUILT" note) for why this never
   interacts with the redaction gate above, and
   `tests/test_letter_settings_routes.py` /
   `tests/test_letter_settings_journey.py` for the auth/ownership/
   fingerprint-integrity tests and the full save -> preview -> approve ->
   two-different-real-leads -> fake-provider-acceptance journey.
   Building this also surfaced and fixed a real bug in the original
   approval-matching logic: it fingerprinted the full per-lead rendered
   letter, which meant an approval could only ever match the one specific
   lead it happened to be computed against -- not actually "reusable"
   across a contractor's future leads at all. `worker.promote_pending_
   approvals` now checks eligibility against `letter_content.
   template_fingerprint` (the contractor-controlled fields only) instead
   -- see that function's own module-docstring entry for the full
   explanation.

   **Known gap -- CLOSED, 2026-09-18 review, Section 2 (second pass):**
   the single `ADDRESS_RELEASE_LIVE` flag above used to be the entire
   disclosure decision -- on or off for every lead at once, with no way
   to preserve access for leads already purchased under the old flow
   while still deciding new allocations case-by-case, and no way to do
   the reverse either. It's now a three-tier decision: a lead claimed
   through the original `letter_dispatches` pipeline always shows its
   real address (historical access is never revoked by this change,
   regardless of the flag); a lead claimed through the new
   `lead_allocations` pipeline shows its real address only if an
   operator has explicitly recorded that specific `lead_reference` as
   eligible in the new `address_disclosure_decisions` table AND
   `ADDRESS_RELEASE_LIVE` is also true (the flag is now an *additional*
   kill-switch, not the decision itself); everything else -- including
   an allocation nobody has made a call on -- stays redacted. There is no
   default-to-eligible path anywhere in this: `get_allocation_address_
   eligible` returns `False` for a lead with no recorded decision, and an
   unclassifiable `lead_reference` fails toward "not historical" (the
   stricter path), never the reverse. See `docs/operator_guide.md`
   section 7a for how an operator actually records a decision (direct-DB/
   script call to `address_release.set_allocation_address_eligible`, no
   admin UI -- deliberate, see that section).

   **What this explicitly does NOT do, per the instruction that prompted
   it ("do not invent a legally sufficient release trigger; leave that
   policy configurable and explicitly unresolved"):** nothing in this
   codebase decides FOR an operator which allocations should be marked
   eligible, or when, or under what legal basis. There is no automatic
   rule (e.g. "N days after purchase", "once a letter is sent") that sets
   `eligible = true`. That remains exactly the same unresolved question
   as item 3 above (Article 14 disclosure timing) -- this section only
   makes the ALREADY-existing all-or-nothing flag safe to turn on
   selectively once that review concludes, without it either breaking
   historical access or silently exposing every new lead going forward.

   Genuinely tested (not just the pre-existing `tests/
   test_address_release_gate.py` route tests, which were found during
   this work to pass by coincidence rather than actually exercising the
   new logic -- see that finding below) in `tests/
   test_address_release_allocation_eligibility.py` (24 tests: the
   historical/new classification, the eligibility read/write including
   the never-defaults-to-true guarantee and the required-decider
   validation, all four combinations of historical/eligible x flag
   on/off, and the fail-safe-on-DB-error path for both self-contained
   wrapper functions).

   **Pre-existing test-infra issue found and fixed as part of this work
   (not a launch blocker, but worth recording):** `address_release.py`'s
   two new self-contained DB-opening helper functions originally did a
   per-call `import database`, which is fine in production but, under
   this test suite's `unittest discover` run, could resolve to a
   DIFFERENT "database" module object than the one `main.database` was
   bound to and than tests were patching -- because a couple of test
   files (`test_payments_webhook.py`, `test_letter_promise_gate.py`)
   deliberately replace `sys.modules["database"]` with a fresh object
   partway through test collection, for their own isolation reasons. The
   mismatch silently made every "enabled" test in `test_address_release_
   gate.py` fall through to the fail-safe redacted/denied branch instead
   of exercising the real logic, only under the full suite (not when run
   alone) -- caught by actually running `python -m unittest discover`,
   not just the new file in isolation, per this session's own standing
   practice of not trusting "passes alone" as sufficient (see
   docs/handoff.md). Fixed by having `address_release.py` bind `database`
   once at its own module-import time instead (same early, stable
   binding `main.py` already relies on) -- see that module's own comment
   for the full explanation. Full suite: 270/270 passing after the fix.

6a. **CLOSED, 2026-09-22 review, Section 6 (concurrency verification).**
    "Run funding/allocation concurrency tests against disposable local
    PostgreSQL if available (simultaneous workers, insufficient balance,
    transaction rollback, and uncertain submissions). Never run these
    against a production or shared database. If no local database is
    available, state that this test is outstanding, rather than skipping
    it silently." This was left genuinely outstanding at the end of the
    previous session (only research had been done -- `funding.py` and
    `fulfilment.claim_for_submission` had been read, but no test existed).
    It is now closed: `tests/postgres_concurrency/run_concurrency_tests.py`
    starts a real, disposable, local PostgreSQL 16 server (never
    production -- see that script's own module docstring for the explicit
    guardrails), applies the actual `migrations/0001_letter_fulfilment.sql`
    file verbatim, and drives the exact SQL from `FundingGate.reserve()`
    and `claim_for_submission()` through concurrent `psql` subprocesses
    (kept as a separate script rather than folded into `unittest discover`
    because `psycopg2`/`psycopg` cannot be installed in this sandbox --
    see `tests/postgres_concurrency/README.md` for the full reasoning).
    Five scenarios, all passing, verified twice in a row for reliability
    on 2026-09-22: (1) two simultaneous workers racing for a budget that
    can only cover one of them -- exactly one succeeds, the other reports
    insufficient funds and writes nothing, no double-commit; (2) a single
    worker requesting more than is available from the start -- all-or-
    nothing, nothing written; (3) a reservation whose transaction is
    rolled back instead of committed -- the reservation and the budget's
    `reserved_pence` both correctly revert; (4) an `'unknown'` provider
    outcome, which the code's own contract says must never be released --
    verified the money stays genuinely held (a second obligation cannot
    over-reserve into it), not just labelled as held; (5) two workers
    racing to `claim_for_submission` the same `'ready'` obligation --
    exactly one succeeds. The disposable server and its data directory are
    always torn down afterward, even on failure (confirmed by hand: no
    postgres process and no leftover temp directory after each run). This
    exercises Postgres's actual row-locking behaviour, not a mock of it.

    **Scope note, stated plainly rather than left implicit:** this proves
    the SQL's locking discipline is sound under real Postgres concurrency.
    It does not, and cannot from this sandbox, prove the full Python call
    path (`FundingGate.reserve()` itself, not just its SQL) behaves
    identically against a real `psycopg2` connection, since that library
    could not be installed here -- see `tests/postgres_concurrency/
    README.md`. The existing mocked unit tests (`tests/test_funding.py`,
    `tests/test_fulfilment.py`, etc.) already cover that the Python glue
    calls the right SQL in the right order; this closes the remaining gap
    of "does that SQL actually serialize correctly under contention",
    which mocks cannot answer.

## Should decide before launch, not strictly blocking

7. **Free-lead-grant inclusion.** `redeem_free_lead_code` (the free-tier
   signup grant) now creates a `letter_obligations` row through the same
   pipeline as a paid purchase, with no distinction by `allocation_type` in
   how it's processed. **This means, as currently wired, a free-tier
   homeowner's letter would be posted at TreeKey's real cost once sending
   is live -- was that the intent, or should free grants be excluded from
   real postage?** Not decided anywhere in your instructions this session;
   flagging rather than guessing.

8. **Address structuring.** `leads.address` is one free-text field. A real
   provider integration will likely need line1/city/postcode/country
   components -- see operator_guide.md section 6.

9. **Scheduler/cadence.** Nothing in this session's work calls
   `worker.promote_pending_approvals` / `promote_pending_funding` /
   `run_batch` automatically -- see operator_guide.md section 5 for the
   manual invocation. **Decision needed:** how often, and via what
   mechanism (cron, a long-running worker process, a platform scheduled
   job) on your actual hosting setup.

10. **Admin tooling.** There is no UI for: confirming a funding budget,
   adding a suppression row, reconciling an `unknown` obligation, or
   viewing `blocked_missing_data` rows. All of this is currently direct-SQL
   only (see operator_guide.md). Worth building before this scales past a
   handful of manual checks a day.

11. **Historical data.** No backfill of old `letter_dispatches` rows into
    `lead_allocations`/`letter_obligations` (optional -- ownership lookups
    already fall back correctly without it, see fulfilment.py). No
    migration of any contractor's business details into
    `contractor_letter_settings` -- every contractor starts with zero
    saved settings and must explicitly configure + approve their letter
    before any of their obligations can be promoted past
    `pending_approval`. Consider whether to proactively prompt existing
    active subscribers to do this before launch, versus letting it happen
    lazily per-obligation.

## Pre-existing gaps this session found but did not fix (out of scope)

12. **`test_main.py`'s fastapi/starlette stub is stale** relative to the
    current `main.py` (missing `PlainTextResponse` and the starlette
    exception-handler imports `main.py` now uses at module load). Worked
    around locally in `tests/test_access_control.py` (which pre-populates
    a fuller stub before importing `test_main`), documented rather than
    silently fixed in `test_main.py` itself, since that file is unrelated,
    pre-existing test infrastructure this session's brief did not ask to
    be touched.

13. **`letter_provider.py`** (the pre-existing file at the app root, not
    `letter_providers/` -- the new package this session built) -- see
    `docs/letter_provider_assessment.md` for the full assessment of why it
    was kept as-is rather than deleted or wired in.

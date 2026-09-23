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

5. **Copy go-live gate.** This session added "includes one printed & posted
   introduction letter" copy to the marketplace card, lead detail page,
   subscription checkout, and single-lead plan descriptions (Section 8).
   This directly reverses the Sep 12 2026 rule in `payments.py` ("remove...
   letter sending feature off anything public until its built and
   deployed... including any statements that are untrue") -- correctly,
   because your own instructions this session confirm the bundled model as
   the agreed design. **But the underlying protection that rule existed for
   still applies**: this copy must not be deployed live until items 1-2
   above are actually resolved and end-to-end real sending works. Deploying
   the copy before that would recreate the exact problem Sep 12 already
   caught once.

## Should decide before launch, not strictly blocking

6. **Free-lead-grant inclusion.** `redeem_free_lead_code` (the free-tier
   signup grant) now creates a `letter_obligations` row through the same
   pipeline as a paid purchase, with no distinction by `allocation_type` in
   how it's processed. **This means, as currently wired, a free-tier
   homeowner's letter would be posted at TreeKey's real cost once sending
   is live -- was that the intent, or should free grants be excluded from
   real postage?** Not decided anywhere in your instructions this session;
   flagging rather than guessing.

7. **Address structuring.** `leads.address` is one free-text field. A real
   provider integration will likely need line1/city/postcode/country
   components -- see operator_guide.md section 6.

8. **Scheduler/cadence.** Nothing in this session's work calls
   `worker.promote_pending_approvals` / `promote_pending_funding` /
   `run_batch` automatically -- see operator_guide.md section 5 for the
   manual invocation. **Decision needed:** how often, and via what
   mechanism (cron, a long-running worker process, a platform scheduled
   job) on your actual hosting setup.

9. **Admin tooling.** There is no UI for: confirming a funding budget,
   adding a suppression row, reconciling an `unknown` obligation, or
   viewing `blocked_missing_data` rows. All of this is currently direct-SQL
   only (see operator_guide.md). Worth building before this scales past a
   handful of manual checks a day.

10. **Historical data.** No backfill of old `letter_dispatches` rows into
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

11. **`test_main.py`'s fastapi/starlette stub is stale** relative to the
    current `main.py` (missing `PlainTextResponse` and the starlette
    exception-handler imports `main.py` now uses at module load). Worked
    around locally in `tests/test_access_control.py` (which pre-populates
    a fuller stub before importing `test_main`), documented rather than
    silently fixed in `test_main.py` itself, since that file is unrelated,
    pre-existing test infrastructure this session's brief did not ask to
    be touched.

12. **`letter_provider.py`** (the pre-existing file at the app root, not
    `letter_providers/` -- the new package this session built) -- see
    `docs/letter_provider_assessment.md` for the full assessment of why it
    was kept as-is rather than deleted or wired in.

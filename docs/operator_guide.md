# TreeKey letter-fulfilment: operator guide

This covers the system built this session (2026-09-18): the pipeline that
turns "a lead was purchased" into "a personalised introduction letter is
printed and posted to the homeowner". It assumes you're an admin/operator,
not a developer -- for the code-level design, read fulfilment.py's,
worker.py's, and letter_providers/registry.py's module docstrings, which
this guide summarises and cross-references rather than duplicates.

**Nothing described here is live.** No production migration has been run,
no provider has real credentials, and no code in this session's work calls
this pipeline from a scheduler or cron job. This guide describes how to
operate it once those decisions are made -- see
[`launch_checklist.md`](launch_checklist.md) for exactly what's still
outstanding.

## 1. The lifecycle, in plain terms

Every purchased/dispatched/free-granted lead gets one row in
`letter_obligations`, moving through these states in order:

1. **`blocked_missing_data`** -- the lead had no address on file. Dead end;
   needs manual investigation (why did a lead with no address get sold?).
2. **`pending_approval`** -- waiting for the buying contractor to have
   approved their current letter content (business name, phone, insurance
   note). A contractor does this themselves at `/letter-settings` ->
   `/letter-settings/preview` -> `/letter-settings/approve` (2026-09-18
   review, Section 1, second pass -- see section 1a below); nothing moves
   past this state without a genuine, current approval -- editing settings
   after approving resets `approved=FALSE` and drops any obligation still
   at this stage back to needing re-approval. One approval covers every
   future lead the contractor buys (it's checked against the reusable
   TEMPLATE fingerprint, not a specific lead's rendered letter -- see
   `letter_content.template_fingerprint`'s own docstring) until their
   settings actually change.
3. **`pending_funding`** -- template approved, waiting on the funding gate
   (see section 2).
4. **`ready`** -- eligible for submission. Not yet attempted.
5. **`submitting`** -- a worker has atomically claimed it and is (or, in
   dry-run, is pretending to be) calling a provider right now.
6. Terminal states: **`provider_accepted`**, **`dispatched`**,
   **`delivered`** (real success, in increasing order of confirmation),
   **`failed`** (every usable provider confirmed rejection), **`unknown`**
   (ambiguous -- see section 4, the most important one to understand),
   **`suppressed`** (blocked by an objection), **`cancelled`** (refund
   before submission), **`dry_run`** (completed in test mode, never a real
   send).

Query `letter_obligations` directly (`SELECT status, COUNT(*) FROM
letter_obligations GROUP BY status;`) to see where everything sits. There
is no admin UI for this yet -- see the launch checklist.

## 1a. Contractor letter setup, preview & approval

2026-09-18 review, Section 1 (second pass). Three contractor-facing routes
in `main.py`, each authenticated by the signed `treekey_contractor_session`
cookie only (never a query param or form field -- see the routes' own
comments):

- **`GET /letter-settings`** -- setup form: business name, phone, contact
  email, a template selector (`letter_content.TEMPLATE_REGISTRY` --
  Friendly Introduction / Professional and Factual / Short and Direct,
  currently placeholder wording pending Nick's final copy), a business
  introduction, relevant services, service area, and insurance/
  qualifications notes -- the last two (and the two new freeform fields)
  shown exactly as the contractor writes them; TreeKey never invents a
  claim, and never auto-adds "trusted"/"vetted"/"qualified"/"insured"/
  "free"/availability wording of its own (see
  `letter_content.BANNED_AUTO_CLAIM_WORDS` and
  `tests/test_letter_content.py`'s `TestNoAutoAddedClaimWords`). Every
  field has a length limit, plus a combined content budget across all of
  them, and rejects raw `<`/`>` characters outright (2026-09-23 handoff --
  see `ContractorLetterSettings.validate`). The locked privacy/data-
  source/contact-policy footer has no corresponding form field at all, on
  any template -- there is nothing for a contractor (or a tampered POST)
  to override.
- **`POST /letter-settings`** -- saves via
  `letter_content.upsert_contractor_settings`, always under the session's
  own `contractor_email`. Always resets `approved=FALSE` -- a material
  change requires renewed approval.
- **`GET /letter-settings/preview`** -- renders the exact template via
  `letter_content.render_preview_letter` (a thin wrapper around
  `render_letter`, the SAME function a real send uses) against fixed,
  clearly-illustrative sample lead data -- `contractor_letter_settings` is
  per-contractor, not per-lead, so there's no specific real lead to show
  at setup time.
- **`POST /letter-settings/approve`** -- records approval. The
  fingerprint passed to `letter_content.approve_template` is always
  computed server-side, in this same request, from whatever settings row
  is *currently* saved under the session's own `contractor_email` -- the
  request body isn't even parsed for one. This is what makes the
  approval genuinely correspond to the exact content/version being
  approved, rather than trusting a value the client could have supplied
  or that could have gone stale between page load and submission.

**Why one approval covers every future lead:** eligibility (checked by
`worker.promote_pending_approvals`) is matched against
`letter_content.template_fingerprint(settings)` -- the contractor-
controlled fields only (business name, phone, notes, template version) --
never a specific lead's full rendered letter. An earlier version of this
logic fingerprinted the full render, which meant an approval could only
ever match the one lead it happened to be computed against; every other
lead has a different address/summary/council baked into ITS render, so it
would never match and would sit in `pending_approval` forever regardless
of the contractor's settings being perfectly current. Fixed as part of
building this UI -- see `letter_content.template_fingerprint`'s own
docstring, and `tests/test_letter_settings_journey.py` for the test that
specifically proves reusability across two different real leads with only
one approval in between.

No admin UI exists yet to view/reconcile `blocked_missing_data` or
`unknown` rows, or to confirm a funding budget or add a suppression row --
see the launch checklist, item 10.

## 2. The funding gate (`funding.py`)

Controls whether any obligation can move from `pending_funding` to `ready`.
Set via the `FUNDING_MODE` environment variable (see
`.env.example.letter-fulfilment`):

- **`hold`** (default): nothing is eligible until an admin has run
  `FundingGate.confirm_budget(cur, amount_pence=..., confirmed_by=...,
  note=...)` -- there is currently no admin UI for this either; it must be
  called from a Python shell/admin script against the real database.
  Recommended starting mode.
- **`simulate`**: for local testing. `FundingGate.simulate_budget(pence)`
  sets an in-process budget that's never written to the database. Resets
  every time the process restarts.
- **`working_capital`**: always eligible, no confirmation needed at all.
  Not recommended without a real reconciliation process behind it -- this
  is "spend now, reconcile against the bank later."

**2026-09-18 review, Section 6 -- atomic reservation, not just a soft
check.** `gate.check()` above (used by `worker.promote_pending_funding` to
move `pending_funding` -> `ready`) is a cheap, non-atomic PRELIMINARY
filter -- it reads available budget but doesn't claim any of it. The real
protection happens one stage later, automatically, inside
`letter_providers.registry.attempt_send`, immediately before any provider
is called (and only for a real, non-dry-run send):

- `gate.reserve(cur, obligation_id, pence)` atomically sets pence aside
  (`mailing_budget_confirmations.reserved_pence`), so a second obligation's
  `reserve()` call can never see that headroom as still available. If the
  full amount can't be covered, the obligation is marked `failed` WITHOUT
  ever calling a provider -- an admin can re-attempt it (reset to `ready`)
  once more budget is confirmed.
- On a real provider ACCEPTED/DISPATCHED, `gate.settle()` converts that
  reservation into real spend (`spent_pence`).
- On every usable provider confirming REJECTED, `gate.release()` frees the
  reservation back to the pool.
- On an `unknown` outcome (ambiguous response or a provider that raised),
  the reservation is deliberately left untouched -- neither settled nor
  released -- until a human reconciles the `unknown` row (section 4 below).
  This is what "preserve reservations for uncertain submissions until
  reconciled" means in practice: the money stays set aside rather than
  either being spent on a letter that might not have gone out, or being
  freed and re-spent on a different letter while the first might still be
  in the post.

`worker.run_batch` builds a real `FundingGate()` automatically if its
caller doesn't pass one (defaulting to `FUNDING_MODE=hold`, fail-safe), so
this protection is on by default for the normal operating path. `gate.spend(cur,
pence)` remains available as a lower-level primitive for a manual/admin
reconciliation outside the obligation pipeline (see its own docstring) --
the automated pipeline no longer needs it directly, since reserve()+settle()
covers that.

**Not implemented in this sandbox:** true multi-connection concurrency (two
real workers, two real database connections, racing for the same budget
row) was never exercised against a live Postgres instance -- none is
available here. `reserve()`'s atomicity relies on `SELECT ... FOR UPDATE`
row locking, which is a standard, well-understood Postgres mechanism, but
the guarantee itself is only proven at the unit/logic level in this
session's tests (see `tests/test_funding.py`'s own docstring on this),
not against a real concurrent workload. Verify this against a real staging
database with actual concurrent workers before relying on it in production.

## 3. Suppression (`suppression.py`)

A homeowner who's objected (by any channel -- letter reply, email, phone)
gets a row in `postal_suppressions`, added via `suppression.add_suppression`.
Two scopes:

- **`this_person`**: blocks only that named applicant at that address.
- **`this_address_anyone`**: blocks the address regardless of who applies
  next (use when the objection was clearly about the property, not one
  individual -- e.g. "stop sending planning-related mail to this house").

Checked automatically at send time (`letter_providers.registry.attempt_send`,
step 1, before the obligation is even claimed) -- no manual step needed
once a suppression row exists. **There is no admin UI to add a suppression
row yet** -- this must be done via direct DB access or a small admin
script. Building that UI is outstanding work, not something this session
implemented (Section 7 of the original brief asked for the mechanism, not
a full admin surface).

## 4. `unknown` outcomes -- read this before touching anything

An obligation lands in `status = 'unknown'` when a provider's response was
genuinely ambiguous: a timeout, a crash mid-request, a malformed response,
or any exception raised by the adapter. **This is deliberately a dead end,
not a retry queue.** `worker.run_batch` only ever selects `status =
'ready'` rows -- an `unknown` row is never picked up again automatically,
and `letter_providers.registry.attempt_send` never falls back to a second
provider after an unknown outcome (see that function's own module
docstring for exactly why: the first attempt may already have been
accepted, and trying a second provider risks a duplicate physical letter).

**Handling an `unknown` row is a manual, human decision**: check the
provider's own dashboard/support channel for what actually happened to
that specific submission (`provider_reference` may or may not be populated
-- it often isn't, for a genuinely unknown outcome), then either:
- confirm it was actually accepted -- manually update the row's status and
  `provider_accepted_at`/`dispatched_at` to match reality, or
- confirm it was never received -- manually reset it to `ready` (or
  `pending_funding` if you want the gate re-checked) so the worker will
  retry it, or
- give up and mark it `cancelled` with a note explaining why.

There is currently no tooling to do this other than direct SQL -- an admin
reconciliation screen for `unknown` and `blocked_missing_data` rows is
outstanding work (see launch checklist).

## 5. Running the worker: local and deployed entry points

**2026-09-18 review, Section 6 (second pass):** the first pass of this
review built the three pipeline stage functions
(`promote_pending_approvals` / `promote_pending_funding` / `run_batch`) but
left them with zero callers anywhere in the codebase -- guarding those
functions (pipeline-active checks, atomic claims) is not the same thing as
anything ever actually invoking them. This second pass closes that: there
are now two real, tested entry points, described below, plus the single
sequencing function both of them call, `worker.run_one_pass` (see its own
docstring in `worker.py`). **Neither entry point is scheduled anywhere by
this session's own work** -- see item 9 of the launch checklist. Running
either one right now, by hand, does not turn anything on by itself; it
just runs one pass, once, against whatever this shell's environment
already has configured (dry-run by default either way).

### Local: `worker_runner.py` (no FastAPI process needed)

A standalone script at the repo root. Needs the same environment variables
the pipeline always needed (`DATABASE_URL`, `FUNDING_MODE`, the letter-
provider `LETTER_PROVIDER_*` slots if you want a real/fake provider
consulted) but nothing else running:

```bash
python3 worker_runner.py                  # one pass, dry-run (default, safe)
python3 worker_runner.py --live           # one pass, allows a real provider
                                           # call IF a provider slot is also
                                           # configured -- this flag alone
                                           # still sends nothing by itself
python3 worker_runner.py --loop                          # repeat every 900s (default) until Ctrl-C, dry-run
python3 worker_runner.py --loop --interval 300 --live    # every 5 minutes, live
python3 worker_runner.py --worker-id my-machine-1         # identifies attempts made by this run
```

This is the mechanism to use for local testing, a one-off manual run, or
running the worker as a long-lived process on a box you control (a small
VM, a container with `--loop` as its entrypoint) -- see
`tests/test_worker_runner.py` for its own test coverage (argument parsing,
commit-on-success/rollback-on-exception, the no-op case).

### Deployed: the `/trigger-letter-fulfilment-worker` HTTP route

`main.py` exposes this the same way every other pipeline in this codebase
exposes its cron-facing entry point (see the existing `/trigger-domestic-
scan` etc. routes) -- a GET route, authenticated by `?secret=` matching
`TRIGGER_SECRET` (query-param only, since an external cron service can
only ever supply a URL, never an Authorization header):

```
GET https://<your-deployment>/trigger-letter-fulfilment-worker?secret=<TRIGGER_SECRET>
GET https://<your-deployment>/trigger-letter-fulfilment-worker?secret=<TRIGGER_SECRET>&live=true
```

This is the mechanism to point an external scheduler at once you've
decided on one -- a cron-job.org ping, a Render Cron Job hitting the URL,
or any other platform-level scheduled HTTP call, the same pattern this
codebase already uses for the scanning pipeline. **Nothing in this
session's work sets one of those up** -- the route existing is not the
same as it being scheduled anywhere (see launch checklist item 9). It
returns a small JSON summary (`status`, `is_dry_run`, `approvals`,
`funding`, `send_outcomes`) rather than a redirect, since it's meant to be
hit by a machine, not clicked by a person -- see
`tests/test_worker_trigger_routes.py` for coverage of the auth requirement,
the `live=` -> `is_dry_run` mapping, and the rollback-and-JSON-error
behaviour on a mid-pass exception (never silently commits a partial pass).

There's also `/admin/run-letter-fulfilment-worker` (Basic Auth or
`?secret=`, always dry-run) -- a button on `/admin/letter-dispatches` for
Nick to trigger one pass by hand from the dashboard without needing the
cron secret in a raw URL, the same relationship `/admin/process-letter-
dispatches` has to the legacy pipeline's manual trigger.

### What run_one_pass actually does, either way

Both entry points above call exactly one function, `worker.run_one_pass`,
which sequences the three stages in order and returns a combined report:

```python
import database, worker
conn = database.get_db_conn()
cur = conn.cursor()
try:
    report = worker.run_one_pass(cur, worker_id="manual-run-1", is_dry_run=True)
    conn.commit()
except Exception:
    conn.rollback()
    raise
finally:
    cur.close(); conn.close()

print(report.approvals)      # PromotionReport(checked=.., promoted_to_pending_funding=.., ...)
print(report.funding)        # PromotionReport(checked=.., promoted_to_ready=.., ...)
print(report.send_outcomes)  # list[SendOutcome]
```

Internally this is still promote -> promote -> send, exactly as before:
`promote_pending_approvals`, then `promote_pending_funding` (against a
`funding.FundingGate()`, defaulting to `FUNDING_MODE=hold` when the caller
doesn't supply one), then `run_batch` (against a provider registry built
by `letter_providers.registry.build_registry_from_env()` when the caller
doesn't supply one). Calling the three stage functions directly, as
separate steps, still works exactly as it always did -- `run_one_pass` is
a convenience wrapper, not a replacement API; each stage function still
independently refuses to do anything unless `LETTER_DISPATCH_PIPELINE=
fulfilment`.

`worker.estimated_letter_cost_pence()` (used by the funding-check stage
when a caller doesn't pass `estimated_cost_pence` explicitly) now reads
from `ESTIMATED_LETTER_COST_PENCE` (pence, integer; default 95 -- a rough
2nd-class-stamp-plus-paper placeholder) instead of a hardcoded literal --
see `.env.example.letter-fulfilment`.

**Deliberately NOT auto-started as an in-process background thread** the
way `main.py`'s `_autonomous_scheduler_loop` starts the scanning pipeline
on every app boot (a daemon thread checking every 20 minutes). Switching
`LETTER_DISPATCH_PIPELINE` to `fulfilment` to test something else must
never, by itself, start a background loop that begins attempting sends --
starting either entry point above requires a human explicitly doing so.

## 6. Known limitation: unstructured addresses

`leads.address` is one free-text field, not
line1/city/postcode/country. `worker.run_batch` currently passes the whole
string as `address_lines['line1']` and leaves the rest blank. A real
postal provider integration will very likely need proper address
components -- either an address-parsing step before submission, or
capturing structured fields earlier, at planning-data ingestion. Not solved
in this session's work; flagged in the launch checklist.

## 7. The address-release gate (`address_release.py`)

Separate from everything above. Funding, providers and pipeline state
govern whether a letter gets POSTED to the homeowner; this gate governs
whether the exact address is ever DISPLAYED to a contractor or a customer
-- through the web app, or in a transactional email -- and it is
deliberately blind to payment, funding and provider state. See
`address_release.py`'s own module docstring for the full reasoning and the
full list of routes it's wired into (letter/flyer previews, the Street
View redirect, the dashboard/my-leads/free-dashboard pages, and the two
purchase/free-grant confirmation emails).

`ADDRESS_RELEASE_LIVE` (env var, default unset/false): when false, every
one of those surfaces shows a fixed redacted placeholder instead of the
real address, even to a contractor who genuinely owns the lead. This is
not a bug to "fix" by setting it true -- it exists because
docs/launch_checklist.md item 3 (Article 14 disclosure timing) is an
unresolved legal question. Only set `ADDRESS_RELEASE_LIVE=true` once that
review concludes address disclosure to contractors is clear; it is a
separate decision from `LETTER_SENDING_LIVE` (section on the letter-promise
copy gate) -- neither flag implies the other.

The contractor-approval UI (`/letter-settings`, section 1a above) does not
interact with this gate at all -- it always previews and approves against
fixed sample lead data, never a real lead's address, and approves a
template-level fingerprint independent of any address -- see
`address_release.py`'s docstring (the "CONTRACTOR-APPROVAL UI -- BUILT"
note) for the full reasoning.

### 7a. Allocation-level eligibility -- 2026-09-18 review, Section 2 (second pass)

`ADDRESS_RELEASE_LIVE` used to be the WHOLE decision: on meant every lead's
address was shown to whoever owned it, off meant none was, with no way to
turn it on for leads bought going forward without also exposing every
address ever purchased historically, or vice versa. That's now been split
into three tiers, checked in this order for every address-showing call:

1. **Historical purchase** -- a lead claimed through `letter_dispatches`
   (TreeKey's original pipeline, before `lead_allocations`/
   `letter_obligations` existed -- the same table `fulfilment.
   get_lead_owner` already falls back to for ownership) always shows the
   real address, regardless of `ADDRESS_RELEASE_LIVE` or anything in
   `address_disclosure_decisions`. This is what "preserve authorised
   historical purchase access" means in practice: a contractor who already
   paid for a lead under the old flow never loses access to what they paid
   for because of this later change.
2. **New allocation, explicit eligibility decision** -- a lead claimed
   through the new pipeline (`lead_allocations`) shows the real address
   only if BOTH an operator has explicitly recorded that specific
   `lead_reference` as eligible (`address_disclosure_decisions.eligible =
   true`) AND `ADDRESS_RELEASE_LIVE` is also true. Neither is sufficient
   alone -- the global flag is now only an additional kill-switch, not the
   decision itself.
3. **Everything else** (an allocation nobody has recorded a decision for,
   or a `lead_reference` that can't be classified into either table at
   all) shows the redacted placeholder. There is no default-to-eligible
   path anywhere in this code -- `get_allocation_address_eligible` returns
   `False` for a lead with no row, on purpose, per the explicit instruction
   not to invent a legally sufficient release trigger.

**What decides eligibility for a new allocation is deliberately left
unresolved and configurable, not invented by this codebase.** There is no
UI or automated rule that sets `address_disclosure_decisions.eligible =
true` -- it is a manual, audited, one-row-at-a-time operator action via
`address_release.set_allocation_address_eligible(cur, lead_reference,
eligible=True, decided_by="<your email>", note="<why>")`, run directly
against the database (a short Python REPL/script using
`database.get_db_conn()`, or a `psql` INSERT matching that function's
shape) -- there is no admin HTTP route for it, matching the existing
precedent for `funding.FundingGate.confirm_budget` and
`suppression.add_suppression`, both of which are also direct-DB/script-only
today (see sections 4 and the suppression notes elsewhere in this guide).
`decided_by` is required (raises `ValueError` if blank) so every decision
is attributable to a specific human, and `note` is free text for why. This
was a deliberate choice, not an oversight: building an admin UI for this
would itself be a policy decision about who's authorised to approve
disclosure and under what process, which is exactly the unresolved legal
question this section is not supposed to settle on its own.

New table: `address_disclosure_decisions` (`lead_reference` primary key,
`eligible boolean`, `decided_by`, `note`, `decided_at`) -- created by
`address_release.init_address_release_schema`, wired into
`database.init_db()` alongside the other schema functions, and mirrored in
`migrations/0001_letter_fulfilment.sql`.

Two self-contained helper functions do the full three-tier check and open
their own DB connection, for call sites with no cursor already open
(`address_release.guarded_address_for_lead_reference(lead_reference,
real_address)` returns either the real address or the redacted
placeholder; `address_release.lead_address_release_allowed(lead_reference)`
returns a plain bool, for routes with no safe reduced version to show at
all, e.g. the street-flyer and Street View routes). Both fail safe
(redacted/denied) on any DB error. Callers that already have an open
cursor should use `address_release.guarded_address_for_lead(cur,
lead_reference, real_address)` directly instead, to avoid a redundant
connection.

Tested in `tests/test_address_release_allocation_eligibility.py` (24
tests, genuinely exercising all three tiers with an explicit queued
FakeCursor -- see that file's own docstring for why the older
`tests/test_address_release_gate.py` route-level "enabled" tests alone
weren't sufficient evidence: they use a blind `MagicMock()` cursor whose
default truthy `fetchone()` coincidentally reproduced the OLD single-flag
behaviour rather than genuinely exercising the new decision paths, until
this dedicated file was added).

## 8. The public posting-promise gate (`fulfilment.letter_sending_live()`)

Separate again from the address-release gate above -- this one controls
whether the customer-facing COPY ("includes a posted introduction
letter") is shown at all, not whether an address is visible. See
`payments.plan_description()`/`payments.plan_roi()` for where the copy
itself lives (a `letter_suffix`/`roi_letter_suffix` appended only when
this gate is True).

**2026-09-18 review, Section 3 (second pass):** originally this was a
single flag (`LETTER_SENDING_LIVE`). That was real progress over a
hardcoded sentence, but it was still only one control -- a deploy could
have `LETTER_SENDING_LIVE=true` set while `LETTER_DISPATCH_PIPELINE` was
still `legacy` (nothing processes the new pipeline's obligations at all)
or while no `LETTER_PROVIDER_*` slot was configured (nothing could ever
accept a send), and the public copy would still confidently promise a
posted letter. `letter_sending_live()` now requires all three of the
following, every call, fresh (no caching):

1. **`LETTER_SENDING_LIVE=true`** (or another truthy spelling) -- the
   explicit admin approval. Still required on its own even when the other
   two are ready -- an admin's deliberate go-live decision stays a
   separate fact from technical readiness. Default unset/false.
2. **`LETTER_DISPATCH_PIPELINE=fulfilment`** (see section 5 above and
   `fulfilment.active_pipeline()`) -- the pipeline that actually attempts
   provider sends. Left on `legacy` (the default), this is always False
   regardless of the other two.
3. **A real, enabled, configured provider slot.** Built via
   `letter_providers.registry.build_registry_from_env()` -- at least one
   of `LETTER_PROVIDER_PRIMARY` / `_BACKUP_1` / `_BACKUP_2` must name a
   provider kind that reports `is_configured() == True` (real credentials
   present, not just a recognised name) AND is not `fake_test`. The fake
   adapter is excluded on purpose: it exists so the pipeline/worker/
   reconciliation logic can be tested locally with no real vendor account
   (see `letter_providers/fake_provider.py`), and a deploy with only that
   configured has no way to actually post a letter, so the public promise
   must not imply one will be sent. Today `stannp` is the only adapter
   that can ever report itself configured (`STANNP_API_KEY` +
   `STANNP_TEMPLATE_ID` both set) -- `intelliprint`/`postworks` are
   unimplemented placeholders hard-coded to always report unconfigured
   (see their own module docstrings), and Stannp itself is an
   unverified-against-live-API placeholder (section 5/1 above), so no real
   deploy can make this condition true with a genuinely working provider
   today. That's expected, not a bug in this gate -- it correctly stays
   off until that's actually resolved.

All three are independent operator actions, exercisable in any order --
e.g. get the pipeline switched and a real provider configured and
soak-tested with `LETTER_SENDING_LIVE` still off, verify real sends work,
then flip the public promise on last, deliberately. Getting only part of
this configured (any two of the three) always leaves the promise off, not
half-on -- see `tests/test_letter_promise_gate.py`'s
`TestInvalidConfigurationCombinationsLeavePromiseOff` class (7 tests, one
per way to have exactly one condition missing, plus a positive control
proving all three together do turn it on).

## 9. Payment/allocation reconciliation (`payment_allocation_reconciliation`)

Separate from every gate above -- this is what happens when a Stripe
payment genuinely succeeded but the LOCAL write that should have followed
it (creating the lead allocation and letter obligation) failed, e.g. a
transient database problem at exactly the wrong moment.

**How you'll notice:** a CRITICAL "PAYMENT RECEIVED, ALLOCATION FAILED"
alert (via whatever `notifications.send_system_incident_alert` is wired to
in this deployment), naming the customer, the lead, and a reconciliation
issue id. Nothing is refunded and nothing is marked sold automatically --
the customer's payment and the lead's reservation are both still good;
only the local write failed.

**What happens automatically, no admin action required:**
- The failure is durably recorded in `payment_allocation_reconciliation`
  (`stripe_event_id`, `stripe_reference` = the checkout session/
  reservation token, `buyer_email`, `lead_reference`, `reason`,
  `resolved = FALSE`).
- The Stripe webhook event is deliberately left un-fulfilled, so Stripe's
  own retry mechanism (retries for up to ~3 days) keeps redelivering it --
  most of these self-resolve the moment the underlying DB problem clears,
  with no admin action ever needed.
- If a retry arrives AFTER the lead's reservation has since expired
  (`RESERVATION_RELEASE_MINUTES`, see the reservation flow in
  `database.py`) and been swept back to the marketplace, the system does
  NOT fall back to auto-refunding the customer just because the
  reservation itself is gone -- it checks for exactly this open,
  unresolved record first (matched on the durable Stripe event id/
  session id, not the lead's own mutable status -- see `fulfilment.
  has_unresolved_reconciliation_issue`) and, finding one, stays in the
  same "retry, don't refund" state instead. Refunding would contradict
  the still-open alert asking a human to look at it, and risks a double
  refund if a human has already acted on it by hand.

**What needs an admin, eventually, if it doesn't self-resolve:**
Query unresolved issues directly (`SELECT * FROM
payment_allocation_reconciliation WHERE resolved = FALSE;`) -- there is no
admin UI for this yet, same as the other direct-DB/script-only precedents
in this guide (section 2's `confirm_budget`, section 3's suppression
rows). For each: either fix the underlying problem and let Stripe's
natural retry complete the sale, or complete/refund it by hand and then
call `fulfilment.resolve_reconciliation_issue(cur, issue_id,
resolved_by="<your email>", note="<what you did>")` to close it out and
let the system return to its normal auto-refund behaviour for that
payment identity if a further retry ever arrives.

## 10. PostgreSQL concurrency verification (2026-09-22 review, Section 6)

`funding.py`'s `FundingGate.reserve()`/`release()`/`settle()` and
`fulfilment.py`'s `claim_for_submission()` rely on real PostgreSQL row
locking (`SELECT ... FOR UPDATE`, and plain `UPDATE ... WHERE status = ...`)
to stay safe when more than one worker runs at once. The rest of this
repo's tests mock the database, so they verify the Python logic calls the
right SQL, but cannot verify the SQL's locking actually holds up under real
concurrent transactions.

`tests/postgres_concurrency/run_concurrency_tests.py` closes that gap: it
starts a disposable local PostgreSQL 16 server, applies the real
`migrations/0001_letter_fulfilment.sql` file, and drives the exact
`reserve()`/`claim_for_submission()` SQL through concurrent `psql`
processes. It is a standalone script, not part of `unittest discover` --
see `tests/postgres_concurrency/README.md` for why (in short: `psycopg2`
could not be installed in the sandbox this was built in, so the real
Python call path can't be driven against Postgres there; only the literal
SQL can be).

Run it with:

```bash
python3 tests/postgres_concurrency/run_concurrency_tests.py
```

As of 2026-09-22, all five scenarios pass (simultaneous workers with
insufficient combined balance, a single worker with insufficient balance
from the start, a rolled-back transaction, an unreleased "unknown" outcome,
and a two-worker claim race) -- see `docs/launch_checklist.md` item 6a for
the full write-up and `tests/postgres_concurrency/README.md` for what each
scenario actually checks. This has never been run against a real or
production database -- only against a disposable server the script itself
creates and destroys.

## 11. ERROR_LOG.md — cross-session error/fix continuity (2026-09-22)

`ERROR_LOG.md` (project root, alongside `main.py`) is a plain-text,
version-controlled log of every error found in this codebase and every fix
applied to it, one entry per issue. It exists specifically so a fresh AI
session (or a new developer) with no memory of prior work can read one file
and see what's already been tried, what worked, and what's still blocked --
see that file's own header for the full reasoning and the format for new
entries. It complements, and does not replace, the `system_warnings`
database table / Daily Warning Digest (which already tracks live
scraper/lead-source warnings over time) -- this file's job is everything
*else*: what was actually done about an error, across the whole codebase,
readable without database access.

Add an entry to it whenever you find a real bug or ship a real fix. It is
expected maintenance, not optional.

## 12. Scraper incident/repair-attempt tracking and scan checkpointing (2026-09-22)

`scraper_resilience.py` adds `source_incident`/`repair_attempt` tracking
(a real lifecycle -- `open -> repair_attempted -> verifying -> resolved`,
reopened rather than duplicated if a resolved failure recurs) and
`council_scan_checkpoint`/`council_scan_pass_metrics` bookkeeping (so a
future backfill can recover leads missed during an outage, and so "zero
new leads today" can be told apart from "the scraper is actually broken").
This closes a real gap: the existing `system_warnings` table/daily digest
detects and alerts but never remembered whether a fix was tried or worked.

Origin: an external architecture review (ChatGPT/Astra, prompted by Nick
with Claude's own findings) recommended this design on 2026-09-22 -- see
`ERROR_LOG.md`'s entries from that date for the full reasoning and what
was deliberately left out.

**Not yet wired into any real scrape.** This is schema and bookkeeping
functions only -- `main.py` does not call any of it yet, and can't be
usefully wired in without `mesh_scrapers.py` (still absent from this repo
-- see `ERROR_LOG.md`). The same review's other recommendations (response
classification before parsing, tested fallback parser strategies, an
LLM-extraction quarantine) are correctly un-built for the same reason: all
three need real Idox page structure in hand, not guesswork.

No admin UI exists for this yet, same precedent as `confirm_budget`
(section 2) and manual reconciliation resolution (section 9) -- use
`scraper_resilience.get_open_incidents()` / `get_repair_attempts(id)` /
`resolve_incident(id, verified_by=..., note=...)` directly.

**2026-09-22 update (same day, Astra's follow-up review):**
`source_incident` gained `last_verification_outcome`/
`last_verification_failure_reason`/`reopen_count`, plus
`record_verification_result(incident_id, outcome=..., failure_reason=...)`
so a FAILED verification is recorded and reopens the incident instead of
leaving it stuck in `verifying` forever. `open_or_touch_incident`'s "no row
exists" path now uses an atomic `INSERT ... ON CONFLICT` (closing a real
race two concurrent callers could hit). `council_scan_checkpoint` is now
keyed by `(council, search_definition)`, not `council` alone, with
backward-movement protection so a slower concurrent scan can't drag a
checkpoint back in time.

## 13. Idox fixture format, response classifier, and capture hook (2026-09-22)

Companion infrastructure to section 12, from the same follow-up review,
building toward the "tested fallback parser strategies from real saved
pages" work that's still blocked on `mesh_scrapers.py`:

- **`tests/fixtures/idox/`** -- the saved-response fixture format
  (`body.html`/`meta.json`/`expected.json` per case; full schema in that
  directory's own `README.md`). Every fixture declares `provenance`:
  `"captured"` (a real response) or `"synthetic"` (hand-built, clearly
  labeled, never fabricated real page content). Currently contains 3
  synthetic fixtures (429, 503, Cloudflare `cf-mitigated: challenge`) --
  no real captures exist yet.
- **`net_utils.classify_response(status_code=..., headers=..., body_text=...)`**
  -- classifies a response into `PAGE_OK`/`VALID_EMPTY`/`RATE_LIMITED`/
  `CHALLENGED`/`AUTH_REQUIRED`/`SERVER_ERROR`/`UNRECOGNISED_PAGE`, using
  only response shape (status + a small header allowlist) -- it cannot and
  does not try to recognise real Idox page content. `VALID_EMPTY` is only
  ever returned via an explicit `known_no_results=True` hint from a future
  caller that actually has real page knowledge.
- **Capture hook** -- `net_utils.smart_get`/`smart_post(..., capture_context=
  {"council": ..., "platform": ..., "page_kind": ..., "search_parameters":
  ...})` saves the response (body + metadata, deliberately never
  `expected.json`) to `captured_responses/<council>/<case>/` for later
  human review. **Off by default**: omitting `capture_context` (every
  existing call site in this repo) changes nothing. `NET_UTILS_
  DISABLE_CAPTURE=1` forces it off regardless, as a second safeguard.

**Still not wired into any real scrape** -- same reason as section 12.
Once `mesh_scrapers.py` exists, its Idox calls can start passing
`capture_context=...` to begin bootstrapping real fixtures; a human still
has to review each capture and hand-write its `expected.json` before it
counts as a trusted regression fixture (see the README's own "Where real
fixtures will come from" section).

## 14. Buyer-facing reference substitution and the 72-hour post-dispatch personal-data purge (2026-09-23)

**Buyer-facing reference, not the raw council reference.** `address_release.
buyer_facing_reference(cur, lead_reference)` (built 2026-09-22, wired in
2026-09-23) returns a `lead_allocations.id` UUID for a new allocation, or
the real council reference unchanged for a historical claim. Every
buyer-facing display/link on `/dashboard`, `/my-leads`, `/free-dashboard`,
and both transactional emails now goes through this (or its self-contained
wrapper, `buyer_facing_reference_standalone`) instead of the raw
`lead_reference` -- the raw council reference is itself an indirect
identifier, pasteable into a council portal's own search box to recover
the exact application, address, and applicant name the address-release
gate exists to hide. `/generate-letter/{id}`, `/generate-street-flyer/{id}`,
and `/street-view/{reference}` accept EITHER shape in their URL path: each
now calls `address_release.resolve_buyer_facing_reference(_standalone)`
first thing, which resolves a `lead_allocations.id` back to the real
`lead_reference` (falling through unchanged for anything it can't
classify, including a historical claim's own reference) before any
existing ownership/address-release logic runs. **Operator implication:**
an OLD bookmarked/shared link using the raw reference still works exactly
as before (the resolve step just falls through unchanged) -- nothing was
invalidated by this change.

**Applicant name is now gated the same way as address.**
`address_release.guarded_applicant_name_for_lead(_reference)` mirrors
`guarded_address_for_lead`'s historical/new two-tier decision: a
historical claim's applicant name (if the council published one) still
shows; a new allocation's never does, full stop -- no config value can
change this, same posture as the address gate.

**The 72-hour post-dispatch personal-data purge (`retention_dispatch_
purge.py`).** Once a mailing is a genuine, provider-CONFIRMED dispatch
(`letter_obligations.status='dispatched'`/`dispatched_at`, or `letter_
dispatches.sent_at` for the legacy pipeline -- never merely 'accepted'),
`purge_dispatched_personal_data()` clears the homeowner's address/
applicant name and the frozen `approved_content_html` on that row, and the
`leads` row's own address/applicant name once nothing else for that
reference is still unresolved, 72 hours later. Runs automatically as part
of `run_full_autonomous_cycle` (same place `cleanup_stale_leads` runs) and
on demand at `/admin/dispatch-data-purge?secret=...` (two-step confirm,
same convention as `/admin/cleanup-stale-leads`). **Preserved,
indefinitely, with no expiry currently set:** every row's own id,
lead_reference, buyer_email, sale_context, status, every timestamp column,
provider_name/reference, content_fingerprint, template_version, attempts,
last_error -- plus `payments`, `lead_allocations`, and `postal_
suppressions` in full (never touched by this module at all). **What this
means for a real deploy:** a lead's personal data and letter content are
gone 72 hours after a real dispatch confirmation; the transaction/evidence
trail is not, and currently has no planned expiry -- that's an open policy
decision for Nick, not something this module invented a number for.

**`status='unknown'` obligations are a dead end, deliberately.** The purge
never selects one (no confirmed dispatch = the 72-hour clock never
starts), and nothing auto-resends one. The ONLY way one moves is `/admin/
dispatch-reconciliation?secret=...&confirm=yes`
(`retention_dispatch_purge.reconcile_unknown_outcome_obligations`), which
asks the SAME provider that produced the ambiguous result for its current
status via `check_status()` (never `.send()`) and upgrades via the
existing `fulfilment.mark_provider_result` only when the provider actually
answers. An obligation whose provider has no status-lookup capability, or
whose `provider_name` doesn't match a currently configured slot, stays
'unknown' -- surfaced by `count_unknown_outcome_obligations_awaiting_
reconciliation()`, not silently retried forever.

**Testing without a real provider.** `letter_providers/fake_provider.py`'s
`FakeLetterProvider` now supports `force_outcome="dispatched"` (an
immediate dispatch confirmation, for tests that don't need the
accepted-then-dispatched distinction) and `simulate_dispatch_confirmed
(provider_reference)` (advances a prior 'accepted' send to 'dispatched',
discoverable via a later `check_status()` call -- the realistic shape of
how a real provider would actually confirm dispatch). Neither is used by
any real send path; nothing here invents a live provider's dispatch
signal, it only ever answers for this fake adapter, which nothing in
production selects.

**Privacy notice.** `/privacy-policy`'s Data Retention section now states
the actual implemented figures (60-day unsold-lead deletion, 72-hour
post-dispatch personal-data purge) instead of the previous, code-unbacked
"24 months... anonymized or deleted" / "6 years" billing claim. See
`ERROR_LOG.md`'s 2026-09-23 entry for the specific decisions this still
needs from Nick (the evidence-record's own retention period, an actual
enforced billing-record retention policy, and a closed-account data
policy -- none of the three exist as code today).

## 15. Letter setup is part of account setup, and gated before EVERY mailed purchase, including a first-time/anonymous buyer (2026-09-23, revised same day by Request F)

**What changed.** The existing `/letter-settings` setup/preview/approve
journey (template selector, business details, the reusable approval/
fingerprint mechanism -- all pre-existing) is now surfaced directly from
`/account` (a "Letter Template" card showing Not started / Saved, not yet
approved / Approved & in use, with an Edit/Set Up link), and an amber
banner appears at the top of `/account` whenever setup is incomplete. The
business-introduction field on `/letter-settings` now shows "(this is what
customers will see on your introduction letter)" directly beside it.

**The checkout gate now covers every buyer, not just one who is already
logged in.** This section originally shipped (same day, earlier in Request
E) checking `main._letter_setup_complete(account_email)` only for an
already-logged-in contractor, deliberately leaving an anonymous/first-time
buyer ungated -- see this file's git history / the superseded paragraph
this replaces. Request F ("require account creation/sign-in and completed
letter approval before all purchases that include mailing, including
first-time buyers") reverses that: `GET /checkout/{plan_key}` and
`POST /checkout/{plan_key}` (both the single-lead-purchase branch and the
subscription area-selector branch, since every plan includes a posted
letter) now check, in order, before reserving any lead or contacting
Stripe:
  1. Is there a valid session cookie at all? If not, redirect to
     `/login?next=<the exact checkout URL, percent-encoded>`. `/login`'s
     own existing page copy and flow ("Works whether you're an existing
     subscriber or signing up for the first time") already issues a
     session cookie for any email address, new or existing -- there is no
     separate "create an account" step, and none was built. No new
     account-creation flow exists; "sign in" and "create an account" are
     the same `/login` step for a new email.
  2. Is `main._letter_setup_complete(account_email)` true (approved AND
     fingerprint-current)? If not, redirect to
     `/letter-settings?next=<the exact checkout URL>`.
Only once both pass does the existing single-lead/subscription branch logic
run unchanged -- `payments.create_checkout_session` is called (and Stripe
is contacted, and a lead reservation is made) no earlier than before, now
strictly later. **The intended purchase is preserved through both
detours**, not lost: `next` carries the exact original checkout URL
(including its `outcode`/`lead_id` query params) through `/login` (via
`/login`'s own pre-existing `next` mechanism, unmodified) and then through
`/letter-settings` (a new `next` parameter added to
`letter_settings_form`/`save_letter_settings`/`letter_settings_preview`/
`approve_letter_settings`, validated by the same `_safe_next_url` guard
the pre-existing "sign in for your discount" flow already used -- reused,
not reinvented, and still never an arbitrary or off-site redirect target).
A buyer who signs in, sets up their letter, and approves it lands back on
the exact checkout page they started from and completes the purchase
normally.

**Operator implication.** `payments.create_checkout_session` itself is
unchanged (still accepts an optional `account_email=None` and doesn't
enforce this gate itself) -- its docstring now has a 2026-09-23 note that
its real caller, `main.py`'s checkout routes, no longer ever invokes it
without a logged-in, letter-setup-complete `account_email`, so "login is
never required to buy a lead" is no longer true of the live app even
though the function itself still permits it. Any other future caller of
`create_checkout_session` (an admin tool, a script) is NOT automatically
covered by this gate -- the gate lives in the HTTP routes, not the
function.

**Tests.** `tests/test_letter_setup_checkout_gate.py`, revised for this
reversal: `TestCheckoutGatesEveryBuyer` (renamed from
`TestCheckoutGatesTheAlreadyLoggedInBuyer`) now asserts an anonymous
visitor is sent to `/login?next=...` with `_letter_setup_complete` never
even called, for both GET branches and the POST defense-in-depth path; a
new `test_logged_in_complete_post_proceeds_past_the_gate` covers the
positive path. 24/24 passing.

## 16. The 72-hour purge now runs on a ~20-minute tick, a historical-dispatch purge exemption was added, and the retained-field list was audited end to end (2026-09-23, Request F, parts 1 and 2)

**Part 1 -- the purge schedule didn't actually match the published
72-hour commitment.** `retention_dispatch_purge.purge_dispatched_personal_
data()` (built earlier the same day, section 14 above) was only ever
called from inside `run_full_autonomous_cycle`, which `main.py`'s own
`_autonomous_scheduler_loop` fires at most once every ~20 HOURS (its own
separate `should_run` cooldown, tracked via `system_state`'s
`last_autonomous_cycle_started_at`) -- not once every 72 hours, once every
20 hours, gating a sweep that itself only catches records already past 72
hours. A record crossing the 72-hour mark moments after one daily sweep
could sit fully identifying for up to a further ~20 hours before the next
sweep caught it. **Fix:** moved the `purge_dispatched_personal_data()`
call out of `run_full_autonomous_cycle` and into `_autonomous_scheduler_
loop`'s own frequent tick (the same ~20-minute cadence the pre-existing
`database.sweep_expired_lead_reservations()` call already runs on),
independent of the daily full-cycle gate -- each wrapped in its own
try/except so one failing sweep never blocks the other. Worst-case gap
after a dispatch crosses 72 hours is now ~20 minutes, not ~20 hours.
`/privacy-policy` Section 9 now states this directly: "checks for
newly-eligible records approximately every 20 minutes," explains outage
handling ("if our systems are briefly unavailable... the check resumes as
soon as service is restored"), and explicitly does NOT claim an absolute
guarantee ("we do not guarantee deletion at the exact 72-hour mark in
every circumstance, only that it is not left to a manual or indefinite
process"). 5 new tests, `tests/test_purge_scheduling.py` -- drives
`_autonomous_scheduler_loop` for exactly one iteration (a mocked
`time.sleep` that raises after the loop body completes) and proves the
purge fires whether the daily-cycle gate is open or closed, plus three
tests asserting the privacy-policy page's actual wording.

**Part 2, finding A -- a real bug found during the field audit, not
something Nick flagged: historical dispatches would have been silently
purged too.** Before this fix, `purge_dispatched_personal_data()`'s
`letter_dispatches` eligibility query had no historical exemption --
every legacy-pipeline dispatch past 72 hours was purged, full stop. A
HISTORICAL claim (`address_release.is_historical_purchase` -- a
`lead_reference` resolvable only via the legacy `letter_dispatches` table,
with no `lead_allocations` row) is, by definition, always already well
past 72 hours old, so the very first production run of this purge would
have cleared every historical claim's stored address/applicant_name in
one pass. `guarded_address_for_lead`/`guarded_applicant_name_for_lead_
reference` would then have correctly-but-catastrophically shown that as
REDACTED to a contractor who was supposed to retain permanent access --
directly contradicting the explicit, already-implemented Request D Part 1
instruction: "preserve the agreed historical-access distinction, but do
not treat historical disclosures as undone." **Fix:** both
`count_dispatch_purge_eligible()` and `purge_dispatched_personal_data()`'s
`letter_dispatches` queries now add `AND EXISTS (SELECT 1 FROM
lead_allocations la WHERE la.lead_reference = letter_dispatches.
lead_reference)` -- a legacy-table row IS purged if it also has a
`lead_allocations` row (not historical, just living in the legacy table,
e.g. from before/after a pipeline switch), and is NEVER purged if it
doesn't (genuinely historical). `letter_obligations` needed no equivalent
filter -- every row there is a new allocation by construction (only ever
created via `fulfilment.create_allocation_and_obligation`, which always
creates the backing `lead_allocations` row first), never historical. 3
new tests, `tests/test_dispatch_purge.py::TestHistoricalDispatchesAreNever
Purged` -- SQL-text assertions that the `EXISTS`/`lead_allocations` clause
is present on the `letter_dispatches` queries and explicitly absent from
the `letter_obligations` queries (the asymmetry is intentional and now
pinned by a test), plus a behavioural no-op check. Full file: 16/16
passing (13 pre-existing + 3 new).

**Part 2, finding B -- unresolved retention decision, reported rather than
invented: NOTHING currently ever purges a historical dispatch's personal
data, on any schedule.** The fix above stops the purge from wrongly
touching historical data; it does not give historical data any retention
period at all -- that population's address/applicant_name/frozen letter
content now persist indefinitely, by design, exactly mirroring the
"historical disclosures are not undone" policy. If Nick wants historical
dispatches purged too, on some LONGER, separate schedule that doesn't
contradict "already-disclosed access persists" (e.g. purge only after the
contractor's own account is closed, or after some much longer fixed
period), that needs an explicit decision and a separate retention period
from him -- not one invented here.

**Part 2, finding C -- the raw council `lead_reference` persists
indefinitely, unpurged, in every table that stores it.** `buyer_facing_
reference` already stops the raw reference from being DISPLAYED to a
buyer for a new allocation (section 14 above); it was never a purge
mechanism, and nothing purges the raw value out of storage. It remains,
with no expiry, in `letter_obligations.lead_reference`, `letter_
dispatches.lead_reference`, `lead_allocations.lead_reference`, and
`leads.reference` -- forever, for both new and historical claims, purged
or not. **Reported as an unresolved decision, not changed unilaterally:**
this cuts both ways and is a real tradeoff, not an obvious fix. In favour
of leaving it: it is the same reference already public on the council's
own planning portal (not TreeKey-exclusive personal data the way a name or
street address is), and every join, audit trail, complaint investigation,
and the purge system itself (`purge_dispatched_personal_data`'s own `NOT
EXISTS` guards) depends on it staying stable and queryable. Against
leaving it: it is still an indirect identifier (see section 14 above --
"pasteable into a council portal's own search box to recover the address/
applicant name"), so its indefinite retention is a real, if smaller,
re-identification surface even after every other field is purged. No
change was made here pending Nick's call on whether/when the reference
itself should ever be scrubbed (and, if so, what would replace it for the
`NOT EXISTS`/join logic that currently depends on it).

**The exact retained-field list (per table, after a full purge pass has
run against a row).** This is the deliverable the ask specifically
requested -- see `ERROR_LOG.md`'s Request F entry for the same list with
the identifying/non-identifying call made explicit per field.

- `letter_obligations`: `id`, `allocation_id`, `lead_reference` (finding
  C, unresolved), `buyer_email`, `sale_context`, `status`, every
  timestamp column, `provider_name`, `provider_reference`,
  `content_fingerprint`, `template_version`, `attempts`, `last_error`,
  `purged_at` -- `address`/`applicant_name`/`approved_content_html`
  cleared.
- `letter_dispatches`: `id`, `lead_reference` (finding C), `buyer_email`,
  `sale_context`, `status`, `provider`, `provider_reference`, `attempts`,
  `last_error`, `created_at`, `sent_at`, `purged_at` --
  `address`/`applicant_name` cleared, but ONLY for a non-historical row
  (finding A/B above); a historical row's `address`/`applicant_name`
  survive in full, indefinitely, by design.
- `leads`: `id`, `reference` (finding C), `summary` (see the caveat
  below -- **superseded 2026-09-23, section 17**: for a non-historical
  reference, the purge now overwrites `summary` with its redacted form
  rather than leaving it raw; a historical reference's `summary` is
  unaffected, matching every other field's historical/new split), `score`,
  `council_source`, `lead_score`, `lead_price`, `status`,
  `discovered_at`, `lead_source_type`, `registered_date`,
  `statutory_deadline`, `planning_status`, `lifecycle_stage`, `agent_
  name`, `agent_company`, `has_agent`, `agent_is_tree_surgeon`,
  `vertical`, `tags`, `personal_data_purged_at` -- `address`/
  `applicant_name` cleared once no unresolved obligation/dispatch remains
  for the reference. `council_source` is a non-identifying label (a
  council name, e.g. "Leeds" -- confirmed by inspection, never a URL; see
  section 14/finding-C reasoning above, same non-identifying-reference
  logic doesn't apply here since it's not pasteable into anything). NONE
  of these other fields are ever cleared by this purge.
- `lead_allocations`: never touched by this module at all -- `id`,
  `lead_reference` (finding C), `lead_id`, `buyer_email`,
  `allocation_type`, `source_payment_ref`, `stripe_event_id`,
  `idempotency_key`, `created_at` all persist indefinitely, unconditionally.
- `payments`: never touched by this module at all -- `id`,
  `stripe_session_id`, `plan`, `amount_pence`, `customer_email`, `status`,
  `created_at`, `lead_id`, `account_email`, `fulfillment_outcome`,
  `updated_at` all persist indefinitely, unconditionally (this is the
  minimal financial/evidence record the privacy policy already describes
  as having no set expiry -- see `ERROR_LOG.md`'s prior Request D entry).

**`leads.summary` -- unable to verify here, reported not assumed-safe.**
`_redact_address_from_summary` (pre-existing, Sep 9 2026, `database.py`)
already runs a best-effort regex scrub (full postcodes, "<number> <street
name> Road/Street/Avenue/...") over every `summary` shown anywhere
BEFORE purchase (the marketplace card, `/marketplace/lead/{reference}`'s
full-description prepurchase page -- confirmed by reading `get_
marketplace_leads_with_freshness`, which applies it unconditionally to
every row it returns, including the `only_reference` single-lead path the
prepurchase detail page uses). It is deliberately NOT applied after
purchase (the dashboard, `/my-leads`, both transactional emails all show
the raw `summary` -- correct, since that's part of what buying the lead
pays for). What could not be verified in this working copy: `scanners.py`/
`research.py` (the actual scraper/summary-generation code that originally
populates `leads.summary` from a council's own listing) are not present
here, so whether the raw scraped summary text itself ever embeds
identifying detail beyond what the existing regex scrub catches (a name,
a less-common address format the regex doesn't match) cannot be audited
from this codebase snapshot. This is reported as "unable to verify," not
claimed as checked.

**Full buyer-output sweep (dashboard, my-leads, free-dashboard, both
transactional emails, generate-letter, generate-street-flyer,
street-view, marketplace, marketplace/lead/{reference}) -- no additional
fix needed.** Every one of these routes/emails was re-read specifically
for maps, council portal URLs, and identifying descriptions beyond what
Request D Part 1 already fixed. Result: `council_source` is never turned
into a clickable URL anywhere in the codebase (confirmed by grep -- no
council-domain link construction exists at all); the one Leaflet map in
the app (`main.py`'s public homepage, `/`) is an anonymised area/radius
SELECTOR for browsing by outcode, not an individual-property map -- it
plots fixed city-landmark pins and a buyer-drawn search radius, never a
lead's own coordinates or address; every address/applicant-name/reference
disclosure already goes through the guarded/buyer-facing primitives from
section 14. No new gating changes were needed for this part of the audit.

**Where.** `retention_dispatch_purge.py` (module docstring, `count_
dispatch_purge_eligible`, `purge_dispatched_personal_data`), `main.py`
(`run_full_autonomous_cycle`, `_autonomous_scheduler_loop`,
`privacy_policy`), `tests/test_purge_scheduling.py` (new),
`tests/test_dispatch_purge.py` (`TestHistoricalDispatchesAreNeverPurged`,
new).

## 17. Login-token leak fixed, `leads.summary` now gated/redacted the same way as address and applicant name, and the 72-hour→20-minute purge cadence was NOT reverted despite an external review asking for it (2026-09-23, response to an external code-review report)

**Context.** An external reviewer inspected the Request F checkpoint (the
ZIP built after section 16 above) and reported three findings. This
section records what changed in response and, just as importantly, what
did NOT change and why.

**Login token exposure -- fixed.** `request_magic_link` (`main.py`, POST
`/api/request-magic-link`) used to render the actual bearer token straight
into its own HTTP response as an "Open on This Device Instead" link --
`/verify-login` accepts that exact token, with no other check, to create a
full session. Anyone who could see that one response (the requester
themselves, a shared terminal, browser history, a proxy) had a working
credential without ever needing real access to the target inbox -- the
same class of bug as the already-documented Sep 8 2026 OTP-echo fix, just
for the token instead of the OTP. **Fix:** the credential-bearing link was
removed outright (there is no safe partial fix -- any link on that page
would have to embed the token). The token now reaches the contractor ONLY
via `notifications.send_transactional_email`, backed by a runtime
assertion in `main.py` itself (`assert auth_data["token"] not in
page_html`). New file `tests/test_magic_link_credential_exposure.py` (5
tests). **Operator implication:** if a real deploy is running the
pre-fix code, treat it the same as the Sep 8 2026 OTP issue -- any
magic-link page view since that deploy could have handed out a live
credential; this pass makes no claim about what a live deployment is
actually running, only about the code in this checkpoint (see this
file's own recurring caveat on that point elsewhere in this guide).

**`leads.summary` is now gated the same two-tier way as address and
applicant name -- closing the gap section 16 flagged as "unable to
verify."** New primitives `address_release.guarded_summary_for_lead(cur,
lead_reference, real_summary)` / `guarded_summary_for_lead_reference`
mirror `guarded_address_for_lead`'s existing historical/new split: a
historical claim's summary is shown unchanged; a new/unclassifiable
allocation's summary is run through the pre-existing `database.
_redact_address_from_summary` (Sep 9 2026 -- previously applied only
PRE-purchase, on the marketplace card and prepurchase detail page); any DB
error during classification fails safe toward the redacted form. Wired
into `contractor_dashboard`, `my_leads_view` (both the paid and free-tier
branches), `free_dashboard`, and both transactional emails -- every buyer
output that previously showed `summary` raw. `purge_dispatched_personal_
data()`'s leads-clearing step (section 16 above) now also redacts and
stores the redacted `summary` for every non-historical reference it
purges, instead of leaving it as the last raw copy anywhere in the
database. **This does NOT fully close the finding it responds to.** It is
still the same best-effort, regex-based scrub as before -- full postcodes
and a "`<number> <street> Road/Street/...`" pattern only, no guarantee
against a name mentioned in passing, an embedded portal reference, or a
link inside the free text. A structured/allow-listed description for new
buyers, or suppressing free text entirely pending a verified sanitisation
policy, would close the gap properly; that is a larger product decision
left open for Nick, not something a targeted pass invents on its own.
Nor does it write any new restricted-purpose/access/retention policy for
the fields section 16 already reported as retained indefinitely
(`lead_reference`, `council_source`, and the rest of `leads`' operational
columns) -- this pass only stops the free-text field from being a bigger
gap than those, it doesn't resolve them. Tests: `tests/test_indirect_
identifier_gating.py` gained `TestGuardedSummary` (6 tests, mirroring the
existing `TestGuardedApplicantName`), and the existing dashboard/email
"new allocation"/"historical claim" tests were extended to seed a real
address+postcode in `summary` and assert it is redacted for a new
allocation, unchanged for a historical one. `tests/test_dispatch_purge.py`
gained 2 tests on the same basis for the purge path.

**The purge cadence (20-minute tick, from section 16 Part 1) was NOT
reverted, despite the review asking for it -- flagged to Nick as an open
question, not silently decided either way.** The review claimed Nick had
"explicitly chosen a daily sweep after 72 hours, normally within 96
hours" and that the 20-minute-tick fix should be reverted "unless user
changes this choice." Checked against this session's own record: the ONLY
documented instruction is section 16 Part 1 above, which is Nick's own
request to FIX a gap where the purge could lag up to ~92 hours behind a
dispatch -- the opposite of asking for a 96-hour window -- and the fix he
asked for (and got, tested, and had reported to him) is the very
20-minute tick the review says to revert. No prior document, commit, or
message in this engagement records a 96-hour agreement. Given a direct
conflict between an unverified third-party claim and Nick's own prior
instruction, the cadence was left exactly as section 16 implemented it.
**Operator note:** if a genuine, separately-recorded 96-hour agreement
with Nick exists outside this session's own history, say so and it will
be reverted -- this was a deliberate "don't silently override the
principal's own words" call, not an assessment that the review's claim is
wrong.

**Where.** `main.py` (`request_magic_link`, `contractor_dashboard`,
`my_leads_view`, `free_dashboard`), `address_release.py` (new
`guarded_summary_for_lead`, `guarded_summary_for_lead_reference`),
`notifications.py` (`_send_purchased_lead_email_inner`, `_send_free_lead_
granted_email_inner`), `retention_dispatch_purge.py` (leads-clearing
restructure), new `tests/test_magic_link_credential_exposure.py`,
`tests/test_indirect_identifier_gating.py` (new `TestGuardedSummary`,
extended existing tests), `tests/test_dispatch_purge.py` (2 new tests).
No change to `_autonomous_scheduler_loop`'s purge cadence, `/privacy-
policy`'s Section 9 wording, or any database schema.

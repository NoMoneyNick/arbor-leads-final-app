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

## 18. The account "Letter" link's unbranded error pages fixed, a fabricated business-identity fallback removed, and one-time letter onboarding added (2026-09-24)

**Superseding note on section 15 above.** Section 15's "Letter Template"
card status line was `Not started / Saved, not yet approved / Approved &
in use`; it now also appends `(standard wording)` or `(personalised
wording)` once approved, based on whether `business_intro` is blank.
Everything else in section 15 (the checkout gate, its `next`-preservation
chain, `_letter_setup_complete`) is unchanged by this section.

**What changed.** Nick reported clicking "Letter" from his account gave a
404 whose own page was itself largely unstyled. The dashboard/my-leads
"Letter" link itself (`/generate-letter/{buyer_facing_ref}`) was traced
and empirically verified end-to-end and is not broken for the normal
case. What was actually broken, and matches the reported symptom far more
precisely than a literal 404 would: `generate_homeowner_letter` and
`generate_street_flyer` returned their own hand-rolled, completely bare
`HTMLResponse` snippets (no nav, no footer) for every one of their own
internal error cases, instead of the `_branded_message_page` helper
already used sitewide since 2026-09-16. All 8 such responses (4 per
route) now use it. See `ERROR_LOG.md`'s 2026-09-24 entry and
`START_HERE.md` section 1b for the full reasoning and the one caveat
(a literal 404 status was not reproduced from code, so if this recurs
post-deploy, the exact failing URL is the one thing needed to go
further).

**Also fixed, found during the same investigation:** both of those routes
fell back to hardcoded example business identity (`"Your Local Tree
Specialists"` / `"07XXX XXXXXX"`) for a real, logged-in contractor with no
saved `contractor_letter_settings` row, printing it as if it were their
actual business on a homeowner-facing letter/flyer. Now redirects such a
contractor to `/letter-settings` first (`next` carrying them back to the
exact letter/flyer), never fabricates. The admin/no-session preview path
used by `tests/test_address_release_gate.py` is unchanged.

**Onboarding/personalisation added, reusing the existing setup->preview->
approve pipeline (section 15) throughout -- no new approval mechanism:**
- `GET /letter-onboarding`: offered once, via `_login_session_response`,
  only the first time a contractor has no `contractor_letter_settings` row
  at all, and only when no `next` (checkout continuation) is already
  pending -- that case is left entirely to section 15's pre-existing gate.
  Copy: "Personalise the introduction homeowners will receive from your
  business. You can change this later in your account." with two choices,
  both landing on the real `/letter-settings` form (which still requires
  an explicit Save -> Preview -> Approve; skipping personalisation is
  never silently treated as approval).
- `/letter-settings` now prefills business name/phone from
  `limbo_accounts.company_name`/`.phone` or a subscriber's `.phone` --
  **never** a subscriber's personal `customer_name` as a business name.
  Every field now has a real example `placeholder=` attribute (never a
  submitted value).
- Section 15's forced-setup banner now reads "Add a personal
  introduction, or continue with your standard letter" once the essential
  fields are already saved, and its "Preview" link is relabelled
  "Continue with your standard letter" in that state -- the existing
  preview/approve flow already required no extra typing there; this only
  makes that visible.

**UPDATE 2026-09-24, second pass -- the gap above is now built, per
Nick's explicit authorisation.** `checkout()` now shows a compact
"Add a personal introduction, or continue with your standard letter"
choice the moment a contractor is already fully set up
(`_letter_setup_complete` true) but still on standard wording
(`business_intro` blank) -- before any lead reservation or Stripe call.
It is read-only (never writes to `contractor_letter_settings`), adds no
duplicate reservation or Stripe session ("Continue" is a link back to the
same checkout URL plus a `letter_nudge=continue` marker; the nudge itself
never reserves anything, so the follow-up hit is a single ordinary
purchase), skips entirely once personalised, and fails open on any
lookup error. `approve_letter_settings`'s own redirect back to a pending
checkout `next` now carries that same marker too -- added after actually
testing the brand-new-customer journey end to end and finding that,
without it, a buyer who had just chosen standard wording during the
forced setup detour would see the identical prompt again immediately
after approving. See `tests/test_letter_onboarding.py::
TestPurchaseTimePersonalisationNudge` for the full test coverage,
including that exact end-to-end journey.
`checkout_post` was not touched -- a contractor only reaches it after
already passing through the `GET` above.

**Also found, NOT fixed, flagged separately:** `generate_street_flyer`
renders its own, entirely separate, hardcoded flyer copy for every
contractor regardless of what they've told TreeKey, including a specific
`"NPTC Certified - GBP5M Insured"` claim and a `"20% Same-Day Street
Discount"` offer -- entirely outside `letter_content.py`'s governed
template system, and the same class of problem as the fake-identity fix
above (arguably worse: an invented certification/insurance figure and an
unconfirmed discount, not just a placeholder name). Rewriting this
route's content model is a materially bigger change than this pass
attempted.

**Where.** `main.py` (`generate_homeowner_letter`, `generate_street_
flyer`, `_login_session_response`, new `letter_onboarding`/`_letter_
onboarding_choice_html`, `letter_settings_form`, `_letter_settings_form_
html`, `my_account_view`, and -- second pass -- `checkout`/new
`_letter_purchase_nudge_response`/`approve_letter_settings`),
`tests/test_letter_onboarding.py` (19 tests total: 13 from the first
pass, 6 new for the purchase-time nudge), `tests/test_access_control.py`
(2 tests updated in the first pass -- they previously asserted the
fake-default fallback as correct, which was itself the bug),
`tests/test_letter_setup_checkout_gate.py` (2 tests updated in the
second pass for the new banner copy and the `approve_letter_settings`
dismiss-marker redirect). Full suite: 515/515 (`python -m unittest
discover -s tests -v`, run 2026-09-24, both passes). No change to
retention, provider integration, pricing, or live-sending settings;
nothing deployed.

## 19. Request D: the "Letter" 404 root-caused (multi-segment reference routing) and fixed; account/dashboard/my-leads links now distinguish historical from new-allocation leads; street-flyer fabricated claims removed; error-page stylesheet gap fixed (2026-09-24, later pass)

**Superseding note on section 18 above.** Section 18 correctly left the
404 unconfirmed at the code level and flagged the street-flyer claims as
"NOT fixed." Both are resolved by this section, per Nick's explicit
instruction not to mark the root cause resolved without a real
reproduction. Section 18's onboarding/personalisation work is unchanged.

**The routing bug, in full.** `generate_homeowner_letter`,
`generate_street_flyer`, and `street_view_redirect` all took their lead
identifier as `@app.get("/.../{param}")` -- Starlette's DEFAULT route
convertor, which matches exactly one URL path segment and cannot match a
literal `/`. A historical claim's buyer-facing reference is the real,
unmodified council planning reference (`address_release.buyer_facing_
reference`'s own docstring), and genuine UK planning references routinely
contain `/` (e.g. `"26/P/1118/S73"` -- taken verbatim from this
codebase's own `_SUSPECT_DISCHARGE_REFS_SEP11` admin constant, not a
synthetic worst case). Every link this app builds to one of these routes
goes through `urllib.parse.quote()`, whose default `safe='/'` leaves the
slash unescaped -- so the actual href rendered for this population was a
genuine multi-segment path. Pre-encoding it would not have helped either:
Starlette decodes a percent-encoded `%2F` back to `/` before route
matching runs, so it 404s against the default convertor identically to a
literal slash. Net effect: the ROUTER itself returned 404 before any of
these three handler functions ever ran, for any historical buyer whose
council reference happened to contain a `/`. This is precisely why
section 18's fix (which changed what those functions RETURN on their own
internal error paths) could not have reproduced or fixed this -- the
router never dispatched to them at all in this case.

**Fix.** All three decorators changed to Starlette's multi-segment `path`
convertor: `/generate-letter/{lead_id:path}`, `/generate-street-flyer/
{lead_id:path}`, `/street-view/{reference:path}`.

**Why this was invisible to the existing test suite, and how it was
verified instead.** Every test in this suite runs `main.py` against a
hand-rolled `fastapi` stub whose `@app.get(...)` is a pure pass-through
decorator -- it never parses or compiles a route's path template, so no
existing test could ever have caught this bug or proven a fix for it
(see `tests/test_access_control.py`'s own `_FakeFastAPI`). `fastapi`
itself cannot be installed in this sandbox (no PyPI access), so this was
verified against the genuinely-installed `starlette` package directly --
a faithful proxy, since FastAPI's `APIRoute` is an unmodified subclass of
`starlette.routing.Route` with no independent path-matching logic of its
own. New `tests/test_slash_reference_routing.py` (10 tests) extracts the
actual `@app.get(...)` path-template strings straight from `main.py`'s
own source text via regex (so the test fails the moment a decorator
drifts, rather than silently testing a stale copy), builds minimal
single-route Starlette apps from them, and drives a real
`starlette.testclient.TestClient` against both the old and new route
shapes -- confirming the real reference 404s under the old `{param}`
shape and succeeds under the new `{param:path}` shape, that a plain
reference and an opaque allocation UUID still work (no regression), and
that a percent-encoded slash still 404s against the default convertor
(proving pre-encoding alone was never a viable fix). **Stated plainly:**
this verifies real Starlette routing behaviour, not the actual installed
FastAPI app end-to-end, which cannot run in this sandbox at all.

**Account/dashboard/my-leads link audit.** `/account` (`my_account_view`)
was already correct (a separate, accurately-worded "Letter Template"
card) -- no change. `/dashboard`, `/my-leads`, and `/free-dashboard` each
offered the same "Letter"/"Street Flyer" button pair regardless of
whether the lead was historical (real address disclosed, both routes
genuinely usable) or a new allocation (address redacted --
`address_release.guarded_address_for_lead_reference` never discloses the
real address for anything but a historical claim). Fixed per-lead, reusing
the already-computed `addr` as a free historical-vs-new-allocation signal
(`addr != address_release.REDACTED_ADDRESS_PLACEHOLDER`) rather than an
extra DB call: a historical lead is untouched (same "Letter" + "Street
Flyer", both real); a new-allocation lead now shows "Preview Letter"
(never "Letter" -- it previews against a redacted address, it is not the
actual posted letter) and no "Street Flyer" button at all -- that route
is already gated by `address_release.lead_address_release_allowed(ref)`,
which is structurally `False` for anything but a historical claim, so
offering it to a new-allocation buyer was offering a tool guaranteed to
refuse them every time. In its place, a new honest mailing-status line
(below), shown only when there is something real to say. `free_dashboard`'s
single free-lead tool card is unconditionally treated as new-allocation
(`database.redeem_free_lead_code` always creates one via `fulfilment.
create_allocation_and_obligation`, never historical by construction) --
its "Street Flyer" card was removed outright and its letter card
relabelled "Preview Intro Letter".

**New: `fulfilment.get_letter_status_label_for_lead_reference(lead_
reference)`.** Looks up the most recent `letter_obligations` row for a
reference and maps `status`/`is_dry_run` to a short, homeowner-safe
label: `"Preparing to post"`, `"Being printed & posted"`, `"Posted"`,
`"Issue detected -- contact support"`, `"On hold"`, etc. `is_dry_run`
unconditionally overrides any status-based "posted" claim (this
environment never runs live sending -- a dry-run row must never read as
delivered). Fails toward `None` (the status line is omitted entirely,
never fabricated) on an empty reference, no row on record, or any DB
error -- the same fail-safe posture used throughout this codebase. Note
for future readers: this function does a LOCAL `import database` inside
its own body, not a module-level one -- `database.py` itself does
`import fulfilment` at its own top level, so a top-level `import
database` here would be a genuine circular import. This is intentional
and correct for the real app; it just means any test patching
`database.get_db_conn` via the plain string form needs `create=True` if
run under the full suite (documented in `tests/test_lead_action_links_
and_flyer_claims.py` itself).

**Street-flyer fabricated claims -- fixed, per explicit instruction this
pass (section 18 above left this flagged, not fixed).** The hardcoded
`"NPTC Certified • £5M Insured"` line and the `"20% Same-Day Street
Discount"` offer (including the `<title>`'s promise of it) are removed.
Following the instruction's own "hide or disable ... rather than
constructing an entirely new flyer product": the discount was removed
outright, with no replacement, since no underlying real setting exists to
condition it on and inventing one would be the same class of problem
being fixed. The credentials line now reuses the exact mechanism
`letter_content.render_letter` already uses for the homeowner letter --
the contractor's own real, freely-entered `insurance_note`/
`qualifications_note` (the schema's own comment: "never invented by
TreeKey"), shown only when actually saved and non-blank. **Explicitly
flagged, not fixed:** a different, unrelated route (`boost_review_page`,
the "Google Review Booster & BS3998 Digital Trust Badge" feature) carries
a very similar unconditional claim ("Verified Member • £5M Public
Liability Insured • NPTC Certified Crew"). The instruction named "the
separate street-flyer route" specifically -- this is a different route,
so it was deliberately left untouched (avoiding both scope creep and
silently ignoring it); needs its own explicit go-ahead.

**Error pages' shared styling -- root cause found and fixed, matching the
instruction's own "merely calling a branding helper is not enough."**
`_branded_message_page`, `_branded_404_handler`, and `_branded_500_
handler` all already called the shared nav/footer HTML helpers, but none
of the three linked `/static/tailwind.css` in their own `<head>` -- so
every Tailwind utility class those shared helpers emit rendered as bare,
unstyled markup (confirmed by comparing against real pages like
`privacy_policy`/`faq_page`, which do include the link). Fixed by adding
the missing `<link>` to all three.

**Test-infrastructure gaps found and fixed while building this pass's own
tests (test-only; no production code involved in any of these four):**
1. The shared stub `HTMLResponse` (`test_main.py`'s `_FakeHTMLResponse.
   __init__`) discards every constructor argument, including the content
   itself. Harmless for routes only ever checked via `assert_called_
   with`, but it meant `my_leads_view`/`free_dashboard` (both `return
   HTMLResponse(f"""...""")`, unlike `contractor_dashboard`/`generate_
   homeowner_letter`/`generate_street_flyer`, which return bare
   f-strings) could never have their rendered HTML actually inspected by
   any test before now. Fixed test-side only, via a small content-
   capturing fake patched in for exactly the tests that need it.
2. `patch("database.get_db_conn", ...)` (string form) broke under the
   full suite only, for the reason already noted above under the new
   fulfilment function -- `tests/test_letter_promise_gate.py`
   unconditionally replaces `sys.modules["database"]` with a bare module
   at its own collection time, and `unittest discover` finishes importing
   every test file before running any of them, so that replacement is
   already in place regardless of file execution order. Fixed with
   `create=True` on the affected patches.
3. Two new tests initially tried to simulate `generate_street_flyer`'s
   anonymous/admin preview path by mocking `fulfilment.get_lead_owner` to
   return `None` -- wrong, since `require_lead_ownership` 404s on "no
   session" before `get_lead_owner` is ever reached; the real bypass is
   `_admin_basic_auth_ok`. Fixed to patch that instead.
4. `tests/test_slash_reference_routing.py` needs the real, installed
   `starlette` package, but `tests/test_access_control.py` (collected
   first, alphabetically) registers a bare fake `starlette` as part of
   its own fastapi stub. Fixed by dropping any `starlette*` entries from
   `sys.modules` before importing, forcing a genuine fresh import of the
   real on-disk package.

**Where.** `main.py` (`generate_homeowner_letter`, `generate_street_
flyer`, `street_view_redirect` route decorators; `_branded_message_page`,
`_branded_404_handler`, `_branded_500_handler`; `my_leads_view`,
`contractor_dashboard`, `free_dashboard`), `fulfilment.py` (new `get_
letter_status_label_for_lead_reference`), new `tests/test_slash_
reference_routing.py` (10 tests), new `tests/test_lead_action_links_and_
flyer_claims.py` (17 tests). Full suite: **542/542** (`python3 -m
unittest discover -s tests -p "test_*.py"`, run 2026-09-24 -- up from
515 before this pass). No change to retention, provider integration,
pricing, or live-sending settings; nothing deployed.

**Decision needed from Nick, not invented:** whether `boost_review_page`'s
own separate unconditional credentials claim should be fixed the same
way -- out of scope for this pass since the instruction named only "the
separate street-flyer route."

## 20. Request E: marketplace privacy review for the mailed-introduction model -- raw council reference removed from public URLs/links, a second independent leak found in subscriber alert emails, free-text redaction extended to reference/TPO patterns (2026-09-24, later pass)

**The ask, in one line.** Nick's own instruction stated the concern
precisely: "Encoding the raw reference is not sufficient" -- the raw
council reference is not just a URL-encoding problem, it is itself the
exact search key someone could paste into the council's own public
planning portal to locate the original application, address, and
applicant. Fixing how it was transported (section 19's `{param:path}`
fix, which only made multi-segment references route correctly) would not
have addressed this at all -- a well-formed, `path`-routable URL like
`/marketplace/lead/23/00568/WTCA` still hands out the identifying value
itself, unauthenticated, to anyone who sees the link.

**Confirmed leak #1 (public, unauthenticated): the marketplace detail
page and its own card links.** `lead_detail_view` was
`@app.get("/marketplace/lead/{reference}")`, taking the raw
`leads.reference` straight from the URL and passing it to
`database.get_marketplace_leads_with_freshness(only_reference=reference)`.
`marketplace_view`'s card loop built every link to it the same way:
`f"/marketplace/lead/{ref}"`. Both were reachable with no login. **Fixed
by swapping the lookup key entirely, not by encoding the existing one.**
`leads.id` (`UUID PRIMARY KEY DEFAULT gen_random_uuid()`, confirmed via
the schema -- genuinely unguessable, and already used safely in checkout
links elsewhere in this codebase before this pass) is now the only key
either the route or the card links use. `lead_detail_view` is now
`@app.get("/marketplace/lead/{lead_id}")`, and
`get_marketplace_leads_with_freshness`'s matching parameter was renamed
`only_reference` -> `only_id`, with its `WHERE` clause changed to
`id::text = %s`. The council reference is still resolved -- internally,
server-side, only where the app itself needs it (letter generation,
fulfilment, admin tooling) -- it is simply never again placed in a public
URL, href, or query string.

**Confirmed leak #2 (authenticated, but customer-facing): the subscriber
lead-alert email's "Unlock" link -- found while auditing every place a
lead reference reaches an external output, not mentioned in Nick's own
"known concerns" list.** `notifications.dispatch_lead_alerts`'s
`_unlock_button` closure built its checkout link as
`f"{PUBLIC_APP_URL}/checkout/single_lead_medium?lead_id={urllib.parse.quote(str(ref))}"`,
where `ref` came from `l.get("ref", l.get("reference", ""))` -- the same
raw council reference, sent by email (via the direct
`requests.post(..., "to": [email], ...)` branch of that function -- the
"Master Digest for Admin" and "Individual emails per lead" branches both
route through `notifications.send_resend_email`, which only ever sends to
the fixed internal `TEST_EMAIL` address, so they were correctly ruled out
as not customer-facing) to every subscribed contractor. **This was also,
independently, a genuine functional bug, which raises confidence this was
a real, previously-undetected defect and not a theoretical concern:**
`payments._resolve_live_single_lead_price(lead_id)` does
`SELECT ... FROM leads WHERE id = %s` -- a raw reference could never have
matched that lookup, so the "Unlock" link in every such email was already
broken for any contractor who clicked it. Fixed the same way as leak #1:
the closure now uses `l.get("id")`, never a reference.

**Regex free-text redaction extended to reference/TPO patterns --
closing the gap Nick's own instruction named directly ("Regex redaction
currently covers only some patterns").** `database.
_redact_address_from_summary` previously redacted only postcodes and
house-number/street combinations from free-text lead summaries; it never
touched a TPO number or council reference appearing inline in descriptive
text (e.g. "...subject to TPO 45/2019, ref 23/00568/WTCA..."). Two new
patterns were added: `_TPO_REFERENCE_IN_TEXT_RE` (matches `TPO` followed
by a number, with or without a separating slash/dash) and
`_COUNCIL_REFERENCE_IN_TEXT_RE` (matches multi-segment alphanumeric
reference shapes -- 3 to 5 slash-separated groups, with a lookahead
requiring at least one letter somewhere in the match, specifically so a
plain `DD/MM/YYYY` date or a simple fraction is never mistaken for a
reference and redacted). Both were verified against real reference
examples already present in this codebase (`"26/P/1118/S73"`,
`"23/00568/WTCA"`) and against known non-matches (plain dates, fractions)
via a standalone script before being integrated. **Stated plainly, per
the instruction's own "If a field cannot be safely produced, omit it
rather than inventing it or relying on regex as a guarantee":** this
regex extension reduces the free-text leak surface, it does not
eliminate it as a guarantee -- an operator-entered summary containing a
reference in a shape neither pattern anticipates would still pass
through unredacted. The instruction's own preferred approach -- a
structured summary from reliably available fields (work category, broad
area, and other approved non-identifying attributes), omitting a field
that cannot be safely produced rather than inventing or trusting regex
alone -- is unchanged by this pass and remains the stronger long-term
fix; this extension only closes the specific pattern gap that was
flagged.

**Broad verification sweep -- confirmed already safe, no code change
needed (checked directly against current source, not assumed from any
earlier report).** Per the instruction's own list ("Inspect HTML, URLs,
query strings, data attributes, embedded JSON, API responses, emails,
PDFs, previews, downloads, maps and search metadata"):
- Homepage ticker and `/api/check-postcode` -- already use only
  non-identifying aggregate fields; no reference, address, or applicant
  name in either response.
- The Leaflet map -- plots only approximate/broad-area coordinates
  already gated the same way as the rest of the redaction pipeline; no
  marker payload carries a raw reference.
- `sitemap.xml` / `robots.txt` -- list only static route paths, never
  per-lead URLs.
- JSON-LD structured data on public pages -- carries only the
  non-identifying summary fields already covered above, no reference or
  address.
- `applicant_name` -- confirmed not exposed on any public or
  unauthenticated path (existing gating from section 17 unchanged and
  still in force).
- `/street-view/` -- already gated by `address_release.
  lead_address_release_allowed(reference)`, unchanged this pass (section
  19's `{param:path}` fix already covers its own multi-segment-reference
  routing separately).
- PDF generation (`letter_content.render_letter` and related) -- confirmed
  it only ever receives the resolved real address for a genuinely
  historical, address-released claim, per the existing address-release
  gate; unchanged.

**Found and explicitly flagged, not fixed -- out of scope for a
privacy-specific review.** `/generate-storm-quote/{lead_id}` takes an
unused `lead_id` parameter (no privacy exposure from it), but its output
carries the same fabricated-credentials pattern already fixed for the
street flyer in section 19 and still open for `boost_review_page`
("BS3998:2010 • NPTC • £5M Insurance") -- this is now a **third**
occurrence of that specific pattern across the codebase. Named here for
visibility, not fixed, since it is a fabricated-claims issue, not a
privacy/identifier issue, and therefore outside this pass's scope.

**Where.** `database.py` (`get_marketplace_leads_with_freshness`'s
`only_reference` -> `only_id` param/SQL change; new
`_TPO_REFERENCE_IN_TEXT_RE` / `_COUNCIL_REFERENCE_IN_TEXT_RE`;
`_redact_address_from_summary` extended to apply both), `main.py`
(`lead_detail_view` route/signature/lookup; `marketplace_view` card href),
`notifications.py` (`dispatch_lead_alerts`'s `_unlock_button` closure),
new `tests/test_marketplace_privacy_review.py` (20 tests: redaction
regex coverage including real reference/TPO examples and plain-date/
fraction non-matches, `only_id` SQL-text assertions, opaque-id-only
marketplace detail route and card link tests, the subscriber-alert
unlock-link fix). Full suite: **562/562** (`python3 -m unittest discover
-s tests -p "test_*.py"`, run 2026-09-24 -- up from 542 before this
pass). No change to retention, provider integration, pricing, or
live-sending settings; nothing deployed.

**Decisions needed from Nick, not invented:**
1. Whether the structured-summary-from-approved-fields approach (work
   category, broad area, other non-identifying attributes; omit rather
   than invent) should now be built as the primary public-summary
   mechanism, with regex redaction demoted to a defence-in-depth backstop
   rather than the main safeguard it still effectively is today.
2. Whether `/generate-storm-quote/{lead_id}`'s fabricated-credentials
   text (the third occurrence of the pattern flagged in section 19, still
   open for `boost_review_page` too) should be fixed the same way as the
   street flyer, and whether Nick wants all remaining occurrences handled
   together in one pass rather than piecemeal.

## 21. Bounded retention consistency check across the mailed-introduction model -- every established decision re-verified against current code, two documentation/wording gaps found and fixed, no code behaviour changed (2026-09-24, later pass, Request G)

**Purpose and posture.** This was a verification pass, explicitly scoped
by its own instruction not to reopen settled decisions or run a
production purge/change a schedule "simply to resolve stale
documentation." Every one of the ask's own "established decisions" was
independently re-checked against the actual current code (not re-cited
from the Request E/F sections above) before being reported as still
true.

**1. The 60-day unsold-lead clock -- re-confirmed true, unchanged.**
`database.UNSOLD_LEAD_DELETION_DAYS = 60`, scoped to `status IN (NULL,
'new')` only (a purchased/claimed lead is never touched regardless of
age), keyed off `COALESCE(registered_date, discovered_at)` -- council
registration, not TreeKey's own collection/discovery time, with
`discovered_at` only ever a fallback when `registered_date` is missing.
Confirmed genuinely separate from the 56-day maximum sale-eligibility
window (the module's own comment states this explicitly; re-run
`tests/test_lead_retention.py::TestDeletionClockIsSeparateFromSaleEligibility`,
15/15 passing in that file).

**2. The 72-hour post-dispatch purge cadence -- re-confirmed true,
unchanged, and still more frequent than the "accepted daily sweep."**
`retention_dispatch_purge.purge_dispatched_personal_data()` runs on
`_autonomous_scheduler_loop`'s own ~20-minute tick, independent of the
~20-hour-gated daily full cycle the 60-day sweep runs on -- re-confirmed
by reading the loop body directly. Per this ask's own instruction
("describe actual behaviour accurately without reopening this as a large
task"), this is reported as-is, not reverted -- same posture section 17
above already took when an external review asked for the opposite.

**3. Personal-data copy trace -- extended per this ask's own inspection
list, one real documentation gap found.** Re-confirmed the retained/
cleared field lists in section 16's own table still match current code
exactly. Newly checked: no email content or PII is ever written to any
database table by `notifications.py` (no `INSERT`/`CREATE TABLE`
anywhere in that file -- the only copies of a sent email are the
recipient's own inbox and the email provider's own logs, both correctly
treated as outside TreeKey's control, never claimed as deletable). No
log statement anywhere in `main.py`/`database.py`/
`retention_dispatch_purge.py`/`suppression.py` writes a real address,
applicant name, or summary into the application log. Two admin CSV
exports exist (`/export-directors.csv`, `/export-mail-list.csv`) --
confirmed by reading both handlers that they query `potential_partners`
(TreeKey's own contractor-prospect outreach list) only, never
`leads`/`letter_obligations`/`letter_dispatches` -- a separate personal-
data category (prospective business contacts, not homeowners/
applicants), out of scope for a review of the mailed-introduction model
specifically, named here for completeness rather than treated as a
finding. No export of `leads` table data exists anywhere in the
codebase.

**4. Suppression effectiveness after deletion/re-scraping -- confirmed
sound by design; one real documentation gap found and fixed.**
`suppression.py`'s matching is via a keyed digest computed and stored
independently of the `leads`/`letter_obligations` rows at the moment an
objection is recorded -- it does not reference or depend on the original
lead row at all, so purging that row via either retention clock above
cannot break a match, and a later re-scrape of the same real-world
address (a new `leads` row, new `id`) is still matched correctly because
matching is address/name-keyed, not lead-keyed. `is_suppressed()`'s
wiring immediately before every real send
(`letter_providers/registry.py::attempt_send`) was re-confirmed by direct
inspection. **Real gap found and fixed:** `SUPPRESSION_HASH_KEY` -- the
one secret this whole mechanism depends on, which fails LOUD (blocks
every send) if unset -- was never actually listed in
`.env.example.letter-fulfilment`, the file this codebase's own
conventions treat as the canonical "recognized environment variables"
reference (confirmed by grep: no mention of it existed). An operator
following only that file, as `START_HERE.md` section 6 directs, would
never learn this variable needs setting. Fixed by adding a full entry
documenting its purpose, that it must be handled like a signing secret
(restricted to whoever can already read the production database
directly; never logged or pasted into a ticket/chat), its rotation
behaviour (requires re-running `backfill_address_suppression_hashes`),
and its fail-loud default. Documentation-only -- `suppression.py` itself
was not touched; `tests/test_suppression.py` (13 tests) re-run and
confirmed passing unchanged.

**5. Historical/indefinitely-retained records -- confirmed unchanged, no
drift from Request E's marketplace-id change.** Re-verified
`address_release.is_historical_purchase`/`guarded_address_for_lead` are
untouched by Request E's lookup-key swap (that change was to the PUBLIC
marketplace route only, never to disclosure/retention logic). The three
previously-reported open items (section 16, findings A/B/C) remain open
and unresolved by this pass, exactly as instructed -- none were given an
invented retention period here.

**6. Deletion boundary (app DB vs. email/postal-provider/backups) --
wording gap found and fixed.** `/privacy-policy`'s Data Retention
paragraph said dispatched personal data is "permanently deleted" with no
stated scope -- read literally, an absolute claim reaching backups, the
mailing provider's own delivery records, and the physical letter already
in the homeowner's hands, none of which TreeKey controls or can reach.
Verified against actual behaviour: `purge_dispatched_personal_data()`
only ever executes against this app's own Postgres tables; nothing in
this codebase touches a database backup, calls the mailing provider's
API to delete anything, or reaches a letter after it's posted. Fixed by
scoping the sentence to "our live application database" and adding one
sentence naming what it does not reach -- without inventing a backup-
retention figure this codebase has no way to know (Render's own backup
policy is not verified anywhere in this session). New tests,
`tests/test_purge_scheduling.py::TestPrivacyPolicyDistinguishesDeletionFromSystemsWeDoNotControl`
(4 tests), pin the scoped wording against silent regression.

**7. Article 14 -- deliberately not reopened, per the ask's own
instruction.** Confirmed it remains listed as an unresolved item
requiring a data-protection adviser in `docs/launch_checklist.md` item 3
and `docs/handoff.md`, unchanged by this pass. Retention/deletion
mechanics (this pass's two fixes included) do not constitute or
substitute for an Article 14 "we collected your data" notice.

**The short consistency table the ask requested:**

| Data | Location | Trigger | Action | Exception | Verification status |
|---|---|---|---|---|---|
| Unsold lead (address/applicant name/summary, whole row) | `leads` | 60 days past `COALESCE(registered_date, discovered_at)`, status still `new`/blank | Row DELETEd (`cleanup_stale_leads`, ~daily via the autonomous cycle) | A purchased/claimed lead, at any age; a row with a missing/implausible/contradictory date (quarantined, not deleted) | Implemented and tested locally (15/15, `tests/test_lead_retention.py`) |
| Dispatched homeowner address/applicant name/frozen letter HTML | `letter_obligations`, `letter_dispatches` | 72 hours past provider-confirmed dispatch, checked ~every 20 min | Fields cleared (`purge_dispatched_personal_data`) | A historical claim's `letter_dispatches` row (no `lead_allocations` row) -- never purged, by design | Implemented and tested locally (23/23, `tests/test_dispatch_purge.py` + `test_purge_scheduling.py`) |
| `leads.summary` for the same reference, once purged | `leads` | Same 72-hour/20-min trigger as above, non-historical references only | Overwritten with its redacted form, not cleared to blank | Historical claim -- summary left unchanged | Implemented and tested locally (section 17 above) |
| Raw council `lead_reference` | `leads`, `lead_allocations`, `letter_obligations`, `letter_dispatches` | None | None -- retained indefinitely, both historical and new, purged rows or not | None | Missing / awaiting a business decision (section 16 finding C) |
| Historical dispatch's address/applicant name | `letter_dispatches` (historical rows only) | None | None -- retained indefinitely, by design ("historical disclosures are not undone") | N/A | Awaiting a business decision if this is ever to change (section 16 finding B) |
| Payment/allocation evidence record | `payments`, `lead_allocations` | None | None -- retained indefinitely | None | Awaiting a business decision on a retention period |
| Postal-suppression record (address/name digest) | `postal_suppressions` | Recorded manually by an operator; never expires | Blocks every future send matching the digest, survives the source lead's own deletion/re-scrape by design | None | Mechanism implemented and tested (13/13, `tests/test_suppression.py`); operator UI to actually record one is missing (direct-SQL/console only -- launch_checklist.md item 10, unchanged) |
| Email/letter content once sent | Recipient's inbox; mailing provider's own systems | N/A | N/A -- outside TreeKey's control | N/A | Correctly not claimed as deletable by TreeKey (verified: `notifications.py` writes no DB copy of sent content) |
| Database backups | Hosting platform (Render) | N/A | N/A -- outside TreeKey's control | N/A | Correctly not claimed as deletable by TreeKey (wording fixed this pass to state this explicitly) |

**Tests.** New: 4 tests, `tests/test_purge_scheduling.py`. Re-run without
modification to confirm no drift: `tests/test_lead_retention.py`
(15/15), `tests/test_dispatch_purge.py` + `tests/test_purge_scheduling.py`
combined (23/23 before the 4 new), `tests/test_suppression.py` (13/13).
Full suite: **566/566** (`python3 -m unittest discover -s tests -p
"test_*.py"`, run 2026-09-24 -- up from 562 before this pass). No
production purge was run, no schedule was changed, no database migration
was needed; every mechanism this ask asked to be verified was found
already correctly implemented -- only two documentation/wording gaps
needed fixing.

**Where.** `.env.example.letter-fulfilment` (new `SUPPRESSION_HASH_KEY`
section), `main.py` (`privacy_policy`'s Data Retention paragraph, one
added sentence), `tests/test_purge_scheduling.py` (new
`TestPrivacyPolicyDistinguishesDeletionFromSystemsWeDoNotControl`). No
changes to `database.py`, `retention_dispatch_purge.py`,
`suppression.py`, `address_release.py`, or any migration.

**Decisions needed from Nick, not invented, consolidated with everything
still open from earlier passes:**
1. The eventual retention period, if any, for the minimal financial/
   evidence record kept indefinitely (`payments`, `lead_allocations`, the
   retained columns on `letter_obligations`/`letter_dispatches`).
2. Whether/how to actually enforce a specific billing-record retention
   period, or a closed-account data policy -- neither exists today.
3. Whether historical dispatches should ever be purged on some separate,
   longer schedule.
4. Whether/how the raw `lead_reference` should ever be scrubbed from
   storage, and what would replace it for the `NOT EXISTS`/join logic
   that currently depends on it.
5. The Article 14 disclosure-timing question itself -- needs an actual
   data-protection adviser, not code.
6. Whether `generate_storm_quote`'s and `boost_review_page`'s fabricated-
   credentials claims (flagged in section 20/19 above) should be fixed.
7. Whether an operator-facing UI for adding a suppression row is worth
   building (currently direct-SQL only -- `docs/launch_checklist.md` item
   10, unchanged by this pass).

## 22. Letter template finalization -- the three templates now use the approved two-page A4 layout, real TreeKey branding, and a genuine reverse-page privacy notice; front-page wording is still Nick's placeholder (2026-09-24, later pass)

**Full narrative, asset-audit findings, launch-blocker/can-wait split and
the two questions still open for Nick are in `START_HERE.md` section 1f --
not duplicated here to avoid two copies drifting apart.** Summary for this
file's own purpose (the render pipeline itself):

`letter_content.render_letter()` (same signature, no caller changes) now
produces a genuine two-page HTML document -- front page (contractor
introduction, per-template wording unchanged) and reverse page (privacy/
supporting information only, no contractor content or advertisement) --
matching the two-page A4 layout Nick approved in
`handoff_inspect/TreeKey-branded-letter-draft-v2.pdf` (that PDF's own
**wording** is watermarked "DESIGN DRAFT -- NOT FOR POSTING" and is not
used verbatim; the reverse page's actual text is built from facts already
verified elsewhere in this codebase). Real branding assets are embedded as
base64 data-URIs (`app/assets/letter_branding/`, resized once offline with
Pillow -- not a new runtime dependency; `letter_content.py` only uses
stdlib `base64`). Fixed A4 CSS with `page-break-after: always`; no
automatic text-shrinking anywhere -- `validate()`'s existing `MAX_*` limits
are what rejects excessive content, clearly, before rendering.

`template_fingerprint()` was confirmed unaffected (it never calls
`render_letter()`) except for one real gap this pass closed: the new
reverse-page copy is TreeKey's own locked content, identical across every
contractor/template, but wasn't yet part of the fingerprinted material. A
new `REVERSE_PAGE_CONTENT_VERSION` constant is now included, so a future
edit to that wording invalidates every contractor's existing approval, the
same guarantee `template.version` already gave front-page wording changes.
`worker.py::promote_pending_approvals`'s freeze-once semantics (an
approved mailing's exact snapshot never re-renders) are unchanged.

A new optional, non-fail-loud `TREEKEY_CORRESPONDENCE_ADDRESS` env var was
added to `.env.example.letter-fulfilment` -- unlike the two required
privacy env vars already there, a missing value is omitted from the
reverse page's wording rather than blocking every render, because the
underlying business decision (Nick's PO Box) is still unresolved and real
posting is independently blocked regardless.

**Pagination/overflow was empirically verified, not assumed**: a
standalone script, `tests/letter_pagination_check/run_pagination_check.py`
(same convention as `tests/postgres_concurrency/` -- not part of
`unittest discover`; uses the sandbox's pre-installed headless Chromium via
Playwright, not a new application dependency), rendered all 3 templates x
7 edge cases (short/blank-optional/max-length/long-business-name/
punctuation/realistic-worst-case) to real PDFs and rasterised every page
for visual inspection. **All 21 renders produced exactly 2 A4 pages**, no
clipping, no blank pages, no page-scaling. Output is in
`tests/letter_pagination_check/_out/`.

**Tests**: `tests/test_letter_content.py` 41/41 (2 new, 1 replaced, 1
narrowed after a false-positive against the new branding `<img>` tags).
Full suite: **568/568**. No deployment, charge, real letter, or production
data change.

## 23. First postal provider -- real integration requirements, current config audit, and a verified PC2Paper-vs-Intelliprint comparison (2026-09-24, later pass; no adapter implemented this pass -- awaiting Nick's account-access decision)

**Current configuration, audited directly (no secrets read or exposed):** no `.env` file exists in this working copy; no `STANNP_*`/`INTELLIPRINT_*`/`POSTWORKS_*`/`LETTER_PROVIDER_*` variable is set in this session's environment. `letter_providers/registry.py`'s `_provider_kinds()` recognises exactly `fake_test`, `stannp`, `intelliprint`, `postworks` -- **`pc2paper` is not a recognised kind; no PC2Paper adapter exists anywhere in this codebase.** `stannp_provider.py` has real HTTP call logic but is explicitly flagged (in its own header) as unverified against Stannp's current live docs, with no account/API key -- and Stannp isn't one of the two providers actually under discussion for this business. `intelliprint_provider.py`/`postworks_provider.py` are intentional stubs (`is_configured()` hard-`False`, `send()` raises). **No postal provider is genuinely usable today, for any of the three providers this project has ever named.**

**PC2Paper vs. Intelliprint, verified against each provider's current official documentation this pass (URLs below) -- not recalled from training data, not assumed from either provider's marketing copy:**

| Requirement | PC2Paper | Intelliprint |
|---|---|---|
| API & auth | Best-documented interface (a versioned PDF spec, "Letter API Interfaces V3.2") is the **legacy** Form-Post/XML-RPC API: sends the account's **plaintext username+password on every request** (the doc's own warning: never call this from a front-end). Newer SOAP/JSON APIs are recommended for new integrations but their public pages don't disclose an auth method. | REST API, `https://api.intelliprint.net/v1`. `Authorization: Bearer <API key>`, separate test/live keys, rotatable from the dashboard. |
| Supported formats | PDF, or HTML/plain text letter body. | PDF, Word, RTF, JPG, PNG, or inline HTML/text/template. |
| Address placement / print constraints | Not documented in any public page found. | Documented precisely: C5/C4 window envelope address zone at **23mm from the left, 43mm from the top**. PDF: 300dpi images, CMYK or RGB, 3mm bleed, safe zone for trimming. |
| Payment / top-up | Prepaid balance; **minimum top-up £5**; PayPal, major cards, Nochex; some accounts can go into debt (not detailed further). | Pay-per-item; **no minimum order, no monthly fee**; billing/invoicing model itself marked "coming soon" in Intelliprint's own help centre -- not fully public yet. |
| Submission / status endpoints | `SendLetter` via Form-Post or XML-RPC (legacy); a newer JSON/SOAP API exists but **no status-check endpoint was found documented anywhere** on their site for any of the four API variants. | `POST /prints` (create), `GET /prints`/`GET /prints/{id}` (list/retrieve), `DELETE /prints/{id}` (cancel); JSON or multipart/form-data. |
| Idempotency / duplicate prevention | Not documented. | Not documented either -- a `reference` field exists but is described only as a "user-friendly identifier," not confirmed to block a duplicate submission. **Real gap on both providers.** |
| What accepted/printed/dispatched/delivered mean | Not documented -- only submission-time `OK`/`ERx` error codes were found (e.g. `ER5` login failed, `ER6` empty field, `ER11` incompatible postage combination). | Documented lifecycle: `draft` → `waiting_to_print` → `printing` → `enclosing` → `shipping` → `sent`, plus `returned` (undeliverable)/`cancelled`/`invalid_address`. **There is no "delivered" status for standard post** -- Intelliprint's own docs state most postage services aren't tracked once they leave the facility, so delivery is only knowable on a tracked-postage option, never by default. Webhook: `letter.updated`, fired on printed/dispatched/delivered/returned transitions for whichever of those a given letter's service actually supports. |
| Test / sandbox | An account-level test account exists but must be **requested by email**; letters submitted to it are cleared out daily. | **Self-service**: `testmode=true` on any `/prints` request runs the full pipeline (validation, address verification, pricing, webhook dispatch) with no charge and no real mail sent -- available immediately, no request needed. |
| Cancellation / failed-job handling | Not documented. | `DELETE /prints/{id}` -- only while `draft` or `waiting_to_print`; blocked once `printing` has started or later. Errors are structured JSON (`message`/`type`/`code`/`param`); no documented safe-retry/idempotency guidance for a timed-out request. |
| Provider-side document/address retention & deletion | Not documented anywhere found. | Not documented anywhere found either. **Real gap on both** -- would need asking each provider's support directly once an account is live. |
| Indicative UK letter cost | 1st class: ~£1.77 postage + £0.25 service charge + ~£0.12 paper/envelope (example calculation) ≈ ~£2.14, ex VAT. 2nd class listed but price not surfaced in this research. | 1st class **£1.94** ex VAT; 2nd class **£0.84** ex VAT ("most popular") -- both fully inclusive of printing, enveloping and postage; full colour standard on all documents. |
| What Nick can actually access today | A real, zero-balance account -- reached PDF upload, no live test letter confirmed sent. | Described as having "unresolved login friction" (`handoff_inspect/CLAUDE-TREEKEY-BUILD-PROMPT.md` line 91) -- current access status unknown to this session. |

**Recommendation, given before writing any adapter code, per the task's own instruction:** on automation fit and cost alone, **Intelliprint** is the stronger candidate against verified current docs -- real bearer-token auth vs. plaintext credentials on every legacy-API request; an instant self-service sandbox vs. an emailed request; a documented status lifecycle vs. none found; no minimum spend vs. a £5 top-up; cheaper 2nd-class pricing. The one factor this session cannot verify is "what we can actually access" -- presented as the comparison above and asked as the one required business-decision question via `AskUserQuestion`. **Nick's answer: "Let me get account access sorted first."** No adapter was implemented this pass, and none was guessed at or built speculatively against either provider while that's still open.

**Sources consulted (fetched and read this pass):**
- https://www.pc2paper.co.uk/api-and-developers.aspx
- https://www.pc2paper.co.uk/api-and-developers/api-faqs.aspx
- https://www.pc2paper.co.uk/api-and-developers/json-letter-api.aspx
- https://www.pc2paper.co.uk/api-and-developers/soap-letter-api.aspx
- https://www.pc2paper.co.uk/api-and-developers/api-errormessages.aspx
- https://www.pc2paper.co.uk/api-and-developers/letter-pricing-api.aspx
- https://www.pc2paper.co.uk/downloads/pc2paperAPI32.pdf ("Letter API Interfaces V3.2")
- https://www.pc2paper.co.uk/webapp/HowDoITopUp.asp
- https://www.pc2paper.co.uk/send-letters/postage-prices.aspx
- https://www.intelliprint.net/api, /api-docs, /reference, /reference/prints/create
- https://www.intelliprint.net/docs, /docs/cancelling-print-jobs, /docs/tracking-print-jobs, /docs/submitting-print-jobs, /docs/errors
- https://www.intelliprint.net/pricing
- https://www.intelliprint.net/help
- `handoff_inspect/CLAUDE-TREEKEY-BUILD-PROMPT.md` (Nick's own 2026-09-22 account-status notes)
- `docs/2026-09-22_handoff_status_report.md` Section 4 (confirms the same gap independently)

**What was NOT done this pass, deliberately:** no adapter code written for either provider (Nick's own choice, above); no `.env.example.letter-fulfilment` entries added yet for either provider's credentials (would be premature before the choice is made -- unlike the Stannp entries already there, which document an adapter that already exists in code); the rendered letter's actual address position was **not** checked against either provider's window-envelope zone (only Intelliprint publishes one to check against); provider-side retention/deletion was not asked of either provider directly (would need a live account first).

**Decisions needed from Nick, not invented:** (1) which account he can actually get working credentials for -- determines which adapter gets built first; (2) once decided, real API credentials supplied through secure local configuration, never in chat; (3) the third, still-unchosen provider slot (unchanged from the original build prompt).

## 24. Purchase-to-posting lifecycle review (provider-independent, fake/test only) -- every verified property already correctly implemented except one: refund-to-cancellation isn't wired to any trigger (2026-09-24, later pass)

**Full narrative and every code reference: `ERROR_LOG.md`'s matching 2026-09-24 entry.** This section is the operator-facing summary.

Nine properties were verified this pass by reading the current code directly (payment-confirmed vs. pending vs. funds-in-bank; funding-gate reservation before any send; duplicate-prevention across repeated clicks/webhooks/workers; approval-snapshot freeze; failed-DB-write recoverability; acceptance-vs-dispatch; no auto-resend on ambiguous outcomes; rejection follows the configured provider-slot order; unavailable funds produce a visible `pending_funding`/`failed` state plus the existing `/admin/live-provider-status` breakdown). **All nine were already correctly implemented** -- mostly from the 2026-09-18 review's Section 6/7 funding-reservation and content-freezing work, and Item 4's payment reconciliation, both re-confirmed here rather than trusted. No code changes were needed for any of them.

**One real gap, not fixed -- a business decision, not a bug to patch silently.** `fulfilment.mark_cancelled()` exists, is safe (structurally cannot cancel an obligation that has already reached a provider), and is unit-tested -- but nothing in this codebase ever calls it. `payments.py` does not handle a Stripe `charge.refunded` event. So a manual refund issued from the Stripe dashboard today does not stop the corresponding letter from being queued and sent. Decision table (also in `ERROR_LOG.md`):

| Question | Option A | Option B | Option C (today) |
|---|---|---|---|
| Refund before the letter reaches a provider -- cancel automatically? | Add a `charge.refunded` webhook handler calling the existing `mark_cancelled()` | Admin cancels manually via a new small admin action, same existing `mark_cancelled()` | No link -- refund and cancellation stay two independent manual actions |
| Refund after `provider_accepted`/`dispatched`? | `mark_cancelled()` already refuses this regardless of which option above is picked -- no decision needed | | |

**Wording previews delivered this pass**: all three templates' current wording, rendered through the real `letter_content.render_preview_letter()` path (fixed fictional sample data), via new `tests/letter_pagination_check/render_wording_previews.py`. Layout remains approved (`TreeKey-branded-letter-draft-v2.pdf`); wording approval is still outstanding and these previews are what that approval decision should be based on -- they are not evidence of postal address-window print compatibility, which stays unverified pending a chosen provider.

**Still blocked on a real provider, unchanged by this pass:** real HTTP behaviour, real timeouts, real status-webhook delivery, and real address-window print accuracy all remain unverifiable from this sandbox until Nick's Intelliprint-access decision (section 23 above) resolves.

## 25. Buyer-facing wording audit (mailed-introduction model) -- ~50 stale "buyer gets the address" instances fixed; one real gate-consistency gap in main.py fixed (2026-09-24, later still pass)

**Full narrative and every code reference: `ERROR_LOG.md`'s matching 2026-09-24 entry.** This section is the operator-facing summary.

Every buyer-facing surface was checked against the actual current model (a
contractor buys a lead; TreeKey arranges an approved postal introduction;
the homeowner decides whether to reply; a new purchaser does not receive
the homeowner's name or address) -- homepage, marketplace cards/details,
pricing, FAQ, free signup, account pages, checkout, and every transactional
email. All of the named outdated examples ("Unlock Address & Contacts",
"0 competitors aware", buyer-receives-the-data claims, "burned permanently"
paired with an address claim, instant unlocked free-lead promises,
address-dependent Street View/letter tools offered without checking the
address is actually released) were confirmed present and fixed. The
highest-severity new instance found (not on the original list): the FAQ's
non-refundable-payments justification itself claimed "you get immediate
access to the Lead data itself" -- the policy is unchanged, only its false
stated reason was fixed. Also fixed: `payments.py`'s Stripe-facing plan
names ("...Unlock..." → "...Purchase..."), a marketplace badge claiming
phone verification that doesn't exist, and the two actual post-purchase
emails, whose subject/heading/closing-note unconditionally claimed the
address was included even on leads where the shown value was already
correctly redacted -- the copy and the data disagreed; now they agree.

**One real gate-consistency gap found and fixed:** the marketplace card,
lead-detail page, checkout page, and letter-setup banner in `main.py` all
promised "includes 1 printed & posted intro letter" unconditionally,
despite code comments claiming they already used the same go-live gate
`payments.py`'s pricing page correctly uses
(`fulfilment.letter_sending_live()`). They didn't. All four now call that
same existing gate, so the checkout page itself can no longer promise an
operational posted letter while real sending stays dry-run-only. Payment
can still be taken today regardless (unchanged, not asked to change -- the
letter-approval step is a separate, intentional requirement before any
purchase) -- this fix stops the site claiming a guarantee it can't back.

**Nothing invented**: no price, allowance, dispatch time, refund term,
qualification, or guarantee was added; every fix reused the two gates that
already existed for exactly this purpose. No unresolved package detail
needing a decision was found. Full regression suite: 568/568 passing,
unchanged. Two pre-existing, unrelated test-environment gaps were
surfaced and confirmed (via this repo's own git baseline) to predate this
pass -- not fixed, as that would be unrelated-refactoring outside scope;
see `ERROR_LOG.md`'s entry for detail. Nothing deployed, charged, sent, or
configured live; live sending remains disabled throughout.

## 26. "My Introductions" account view -- a richer, structured per-introduction record built on the existing fulfilment tables; one genuine pre-existing gap (template_version always NULL) found and fixed (2026-09-24, later still pass)

**Full narrative and every code reference: `ERROR_LOG.md`'s matching 2026-09-24 entry.** This section is the operator-facing summary.

The contractor-facing "My Leads" page (`/my-leads`) now shows a richer
mailing-status block for any lead whose sale went through the fulfilment
pipeline (`fulfilment.get_introduction_record_for_lead_reference`): a plain-
English stage (awaiting approval / preparing / submitted to postal provider
/ dispatch confirmed / needs attention, mapped from the real
`letter_obligations.status` enum, never labelling acceptance as dispatch or
dispatch as delivery -- there is no "delivered" concept anywhere in this
codebase), a safe work category/area (reused from the marketplace's own
classifier, nothing new exposed), the letter template version used (or
"not yet finalised"), the provider's name/reference and accepted/dispatched/
failed timestamps with an explicit statement that no downloadable proof-of-
postage document exists, and a link to the contractor's own existing
address-free letter preview.

**Important operational note, unchanged from every earlier pass:**
`LETTER_DISPATCH_PIPELINE` still defaults to `"legacy"`. This new view is
built strictly from the new `lead_allocations`/`letter_obligations` tables,
so it correctly shows nothing extra for any lead sold under today's default
config -- it will only start showing real data once the pipeline is
switched to `"fulfilment"` for new sales. This is not a bug; it's the same
honest degrade the rest of this codebase already relies on rather than
fabricating a status for a sale that never wrote to these tables.

**One real, pre-existing gap found and fixed while building this:**
`letter_obligations.template_version` was defined in the schema and
accepted as a function parameter, but no caller anywhere ever actually
passed a value for it -- confirmed by reading every real call site, not
assumed. It has been NULL for the entire lifetime of the table. Fixed at
the one correct point (`worker.promote_pending_approvals`, where a specific
template is actually frozen for sending) -- one added column in an existing
UPDATE, nothing else changed.

**No certificate of posting is fabricated.** The codebase has no
provider-issued document/certificate mechanism at all (confirmed by reading
every provider adapter's return type) -- the view states this plainly
rather than implying one exists.

Full regression suite: 580/580 passing (up from 568 -- new coverage, no
regressions). Nothing deployed, charged, sent, or configured live; live
sending remains disabled throughout.

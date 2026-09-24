# START HERE — TreeKey / Vector Data Labs letter-fulfilment work

Originally written 2026-09-18 as a resumable checkpoint under a "stop and
save" instruction; updated 2026-09-22 after resuming and finishing the one
item that was left outstanding (Item 5, PostgreSQL concurrency). All 6
items from the original governing task are now implemented and tested.
This file is the resume point / map of the work. Read this before doing
anything else in this project.

## 1. Exact project location

- Working tree: `/home/claude/treekey_work/app`
- Git repo root (tracks a baseline snapshot, one level up): `/home/claude/treekey_work/.git`
- Run `git status` from `/home/claude/treekey_work` to see the exact diff against baseline.

### Modified (tracked) files
- `app/database.py`
- `app/main.py`
- `app/notifications.py`
- `app/payments.py`

### New (untracked) implementation files
- `app/address_release.py`
- `app/fulfilment.py`
- `app/funding.py`
- `app/letter_content.py`
- `app/letter_providers/` (package)
- `app/migrations/`
- `app/suppression.py`
- `app/worker.py`
- `app/worker_runner.py`
- `app/.env.example.letter-fulfilment`
- `app/docs/` (operator_guide.md, launch_checklist.md, handoff.md, letter_provider_assessment.md)
- `app/tests/` (full unit-test suite, plus `app/tests/postgres_concurrency/` — the
  standalone real-Postgres integration test added 2026-09-22)

## 1a. How to apply a delivered ZIP to your actual project (added 2026-09-24)

**Read this before extracting any checkpoint ZIP onto your own machine.**
Added after a live-site audit found `/letter-settings` 404ing and
`/privacy-policy` still showing the old 24-month wording, despite both
being implemented, tested, and delivered days earlier. Diagnosis: every
checkpoint ZIP (including this one) packages files under an `app/`
folder — `app/main.py`, `app/database.py`, etc. — because that's this
session's own working-directory layout. But the copy of your project
uploaded into this session (`VECTOR DATA LABS/`, a flat folder with
`main.py`, `database.py`, `notifications.py` etc. directly inside it, no
`app/` wrapper, alongside `PROJECT_STATE.md`'s own note that a real
deploy "happens once Nick runs `UPDATE_WEBSITE.bat` / deploys on Render")
shows your actual project is FLAT, not nested under an `app/` folder.

**If you extract a checkpoint ZIP and just copy or drop its whole `app/`
folder into your project as a subfolder, your deploy script/Render will
keep running your existing flat `main.py` and never see the updated one**
— which would exactly produce both symptoms above: a missing new route,
and old static text unchanged, with no code defect on either side. The
new/changed files are ALWAYS the ones inside the ZIP's `app/` folder —
copy their CONTENTS (not the `app/` folder itself) directly into your
flat project root, overwriting the matching files by name (`main.py` over
`main.py`, `database.py` over `database.py`, etc.), then run your normal
`UPDATE_WEBSITE.bat` / Render deploy step as usual.

**This is a plausible, evidence-backed diagnosis, not a confirmed one** —
this session cannot see your actual deploy folder or your Render
dashboard. It's equally possible the ZIP was simply never deployed yet at
all (no redeploy run since it was delivered), which would look identical
from a live-site audit. To tell the two apart: check your Render
service's "Last Deploy" timestamp/commit against when you last received a
checkpoint ZIP. Newer than the ZIP → the flattening issue above is the
likely cause. Older/unchanged → it just hasn't been deployed yet, and
this note doesn't apply.

## 2. Completed items — ALL SIX (implemented + tested)

- **Item 1** — Contractor setup / letter-preview / reusable-template-approval
  UI with auth/ownership/fingerprint checks, plus a fake-provider end-to-end
  customer-journey test.
- **Item 2** — `ADDRESS_RELEASE_LIVE` review. Historical purchase access is
  preserved unconditionally. New-allocation eligibility now requires BOTH an
  explicit per-`lead_reference` operator decision (new
  `address_disclosure_decisions` table, via `address_release.py`) AND the
  global `ADDRESS_RELEASE_LIVE` flag as an additional control. No automatic
  eligibility inference exists anywhere — the actual legal release trigger
  is deliberately left unresolved/configurable (see Section 6 below).
  - Also fixed a real, previously-undetected test-infrastructure bug: a
    cross-module `sys.modules["database"]` identity desync that let some
    pre-existing "enabled" tests pass without exercising their logic under
    full-suite `unittest discover`. Fix: `address_release.py` now binds
    `database` at module level (not per-call), matching how `main.py` binds
    it. See inline comments in `address_release.py` and
    `tests/test_address_release_gate.py` for the full explanation.
- **Item 3** — Public letter-posting promise (`fulfilment.letter_sending_live()`)
  now requires ALL THREE of: `LETTER_SENDING_LIVE` flag, correct pipeline
  (`active_pipeline() == "fulfilment"`), AND a real, enabled, configured
  provider (not `fake_test`) via `letter_providers.registry`. All invalid
  combinations are tested in
  `tests/test_letter_promise_gate.py::TestInvalidConfigurationCombinationsLeavePromiseOff`
  (7 tests) plus one positive control.
- **Item 4** — Payment/allocation reconciliation for the
  successful-payment→DB-failure→delayed-retry-after-reservation-expiry
  scenario:
  - `database.has_unresolved_payment_reconciliation_issue()` — durable-identity
    check by `stripe_event_id` OR `stripe_reference` against
    `payment_allocation_reconciliation WHERE resolved = FALSE`. Fails SAFE to
    `True` (opposite of most read helpers in this codebase) on any DB error
    or missing `DATABASE_URL`, so an unanswerable check never permits an
    automatic refund.
  - `fulfilment.has_unresolved_reconciliation_issue()` — thin DB-cursor
    wrapper around the same check, used inside a single transaction.
  - `payments.py`'s `handle_stripe_webhook` now checks this BEFORE falling
    through to the auto-refund branch, so a delayed retry after the
    reservation has already expired is recognized and left retryable
    (`{"retry": True}`, event NOT marked fulfilled, no refund issued)
    instead of silently refunding a payment that may still be fulfillable.
  - Reconciliation-record-write failure during a DB outage is also covered:
    if `record_payment_reconciliation_issue` itself returns `None` (its own
    write failed), the webhook still returns retryable and still does not
    mark the event fulfilled.
  - Tests: `tests/test_payments_webhook.py` (new classes
    `TestDelayedRetryAfterReservationExpiryReconcilesByDurableIdentity`,
    `TestReconciliationCheckItselfFailsDuringOutage`,
    `TestReconciliationRecordWriteFailsDuringOutage`),
    `tests/test_fulfilment.py::TestHasUnresolvedReconciliationIssue`,
    `tests/test_reconciliation.py::TestHasUnresolvedPaymentReconciliationIssueWrapper`.
- **Item 5 — CLOSED 2026-09-22** (was the one outstanding item as of the
  2026-09-18 checkpoint). `tests/postgres_concurrency/run_concurrency_tests.py`
  starts a real, disposable, local PostgreSQL 16 server, applies the actual
  `migrations/0001_letter_fulfilment.sql` file verbatim, and drives the exact
  SQL from `FundingGate.reserve()` and `claim_for_submission()` through
  concurrent `psql` subprocesses (kept as a standalone script, not part of
  `unittest discover`, because `psycopg2`/`psycopg` cannot be installed in
  this sandbox — see `tests/postgres_concurrency/README.md`). **5/5
  scenarios passing**, run twice in a row for reliability: (1) simultaneous
  workers racing for a budget that can only cover one of them; (2) a single
  worker requesting more than is available from the start; (3) a
  reservation whose transaction is rolled back; (4) an `'unknown'` provider
  outcome, verified to leave the reservation genuinely held; (5) two workers
  racing to claim the same `'ready'` obligation. Full detail in
  `docs/launch_checklist.md` item 6a and `tests/postgres_concurrency/README.md`.
- **Item 6** — Verified worker scheduling/entry points are genuinely invoked
  (not just defined) and documented local/deployed startup without
  deploying. See `app/worker.py`, `app/worker_runner.py`, and
  `docs/operator_guide.md`.

All of the above is documented in `docs/operator_guide.md` (sections 7a, 8,
9, 10) and `docs/launch_checklist.md` (items 2a, 5, 6, 6a).

## 3. Incomplete items / partially-edited code

**None.** As of 2026-09-22, every item from the original governing task is
implemented and tested. There is no half-finished edit anywhere in the
repo — confirmed via `ast.parse` across every `.py` file (zero syntax
errors) and a full passing test run (below).

The genuine scope boundaries that remain (not incomplete work, but
deliberately out of reach of this sandbox or deliberately left to a human)
are listed in Section 6 below (config defaults / outstanding decisions) and
in Section 8 (verified vs. assumed).

## 4. Last tests actually run, results, and changes since

- **Last full unit-test run**: `python3 -m unittest discover -s tests -p "test_*.py"`
  from `/home/claude/treekey_work/app` → **290/290 passing, "OK"** (same
  count as the 2026-09-18 checkpoint — Item 5's new work is a standalone
  script, not part of this suite, so it doesn't change this number).
- **Last PostgreSQL concurrency run**: `python3 tests/postgres_concurrency/run_concurrency_tests.py`
  → **5/5 scenarios passing**, run twice in a row on 2026-09-22 for
  reliability. Confirmed no postgres process and no leftover temp directory
  after each run.
- **Changes made since the last full-suite run**: documentation-only edits
  to `docs/launch_checklist.md` and `docs/operator_guide.md` (Item 5's
  write-up). No `.py` file was touched after the last full-suite run that
  produced 290/290 — that run was re-verified as the final action of this
  phase, alongside a full `ast.parse` sweep, both clean. The checkpoint ZIP
  reflects exactly this verified state.
- No adversarial/manually-reordered test invocation issues affect this
  state: a manually reordered invocation was tried in an earlier phase and
  showed 7 unrelated failures, root-caused to pre-existing test-infra
  fragility unrelated to this work, confirmed NOT present under the real
  alphabetical `unittest discover` run, and logged as explicitly out of
  scope (see `docs/launch_checklist.md` item 12 precedent).

## 5. Exact commands to reproduce the last verified state

```bash
cd /home/claude/treekey_work/app

# Full mocked unit-test suite (no external dependencies, runs in well under 1s)
python3 -m unittest discover -s tests -p "test_*.py"

# Real-PostgreSQL concurrency integration tests (Item 5) -- requires a local
# PostgreSQL 16 server + a non-root OS user to run it as (defaults to
# 'claude'); starts and fully tears down its own disposable server, never
# touches a real database. Exit code 2 (not a failure) if Postgres isn't
# available in the environment -- see that script's own docstring.
python3 tests/postgres_concurrency/run_concurrency_tests.py

# Syntax sweep
find . -name "*.py" -not -path "*/__pycache__/*" \
  -exec python3 -c "import ast,sys; ast.parse(open(sys.argv[1]).read())" {} \;
```

### If further payment-recovery verification is wanted (Item 4, beyond scope)

Item 4's core implementation and unit tests are complete (Section 2). What
remains is verification beyond what this sandbox can reach — not scoped as
required by the original task, but worth naming:

- Real Stripe test-mode webhook retry timing (this sandbox only simulates
  retries by calling the webhook handler twice in-process; it never talks
  to a real or sandboxed Stripe).
- The actual post-3-day admin reconciliation process referenced in
  `docs/launch_checklist.md` item 2a — a process/ops gap, not code.

## 6. Configuration defaults and outstanding provider/legal decisions

### Defaults (fail-safe / dry-run unless explicitly overridden)
- `LETTER_DISPATCH_PIPELINE` — defaults to `legacy` (NOT `fulfilment`).
- `LETTER_SENDING_LIVE` — defaults unset/false.
- `ADDRESS_RELEASE_LIVE` — defaults unset/false.
- `FUNDING_MODE` — defaults to `hold`.
- All `LETTER_PROVIDER_PRIMARY` / `_BACKUP_1` / `_BACKUP_2` slots — default
  unconfigured/disabled. See `.env.example.letter-fulfilment` for the full
  set of recognized environment variables and their safe defaults.

### Outstanding decisions this work explicitly did NOT make up
- No real postal provider account or credentials exist anywhere in this
  repo or environment. Stannp is used only as an illustrative/placeholder
  adapter name and has never been verified against a live API.
- The exact legal/Article-14 disclosure-timing trigger for address release
  was deliberately left unresolved and configurable (per the original
  instruction not to invent this) — requires a UK data-protection adviser's
  sign-off before `ADDRESS_RELEASE_LIVE` is ever set true in a real
  environment.
- Whether funding reconciliation should ever become automated vs. remaining
  a manual `confirm_budget` action is undecided.
- Policy for unsold leads is undecided.

## 7. Confirmation: nothing was deployed, charged, or posted

Throughout this entire body of work, including the 2026-09-22 resumption:
- **No deployment** occurred. No production migrations were run. No
  production records were modified.
- **No real charge** was made. No paid provider account was created.
- **No real letter** was sent or posted. All provider integrations remain
  in `fake_test` / dry-run mode by default, and the live-sending gate
  (Item 3) requires three independent conditions to all be true before any
  real send could occur — none of which are true in this checkpoint's
  default configuration.
- The PostgreSQL instances started during this work (once during Item 5's
  original research, and again by `run_concurrency_tests.py` during its
  test runs) were each disposable, local, self-created, and fully stopped
  and deleted afterward — confirmed by hand each time. Nothing is running.
- `run_concurrency_tests.py` itself never reads `DATABASE_URL` /
  `SUPABASE_DB_URL` or any variable that could point at a real database —
  see that script's own module docstring.

## 8. Distinguishing verified results from assumptions

**Verified this work** (directly observed via tool output):
- 290/290 unit tests passing under `python3 -m unittest discover -s tests -p "test_*.py"`.
- 5/5 PostgreSQL concurrency scenarios passing, run twice in a row, under
  `python3 tests/postgres_concurrency/run_concurrency_tests.py`.
- `ast.parse` clean (no syntax errors) across every `.py` file in the repo.
- The checkpoint ZIPs are valid (`unzip -t` reported no errors).
- Both prior checkpoint ZIPs (2026-09-18) were confirmed untouched
  (unchanged size/timestamp) after this phase's edits.
- PostgreSQL 16 can be started as the non-root `claude` user in this
  sandbox; `psycopg2`/`psycopg` cannot be installed via pip or apt here
  (both attempts failed with explicit, observed errors).
- No postgres process or leftover temp directory remained after any run of
  `run_concurrency_tests.py` (checked by hand each time).

**Assumptions / not verified** (stated as such, not fact):
- `run_concurrency_tests.py` proves the *SQL's* locking discipline is sound
  under real concurrency. It does not, and cannot from this sandbox, prove
  the full Python call path (`FundingGate.reserve()` itself, calling
  through a real `psycopg2` connection) behaves identically, since that
  library could not be installed here. The existing mocked unit tests
  cover that the Python glue calls the right SQL in the right order; this
  is the one remaining gap, stated plainly rather than papered over.
- Real Stripe webhook retry timing/behaviour matches what the in-process
  simulated double-delivery tests assume.
- Whether the existing `letter_providers.registry` code would work against
  a real Stannp (or other) account — never tested against a live API by
  design (no paid account exists, and creating one is out of scope).

## 9. Where the checkpoint files are

- **Latest implementation ZIP** (2026-09-22, includes the closed-out Item 5
  work — does NOT overwrite either earlier checkpoint):
  `/mnt/user-data/outputs/treekey_letter_fulfilment_implementation_20260922T151929Z.zip`
- **Previous checkpoint ZIP** (2026-09-18, end of that phase, untouched):
  `/mnt/user-data/outputs/treekey_letter_fulfilment_implementation_20260918T215016Z.zip`
- **Earliest checkpoint ZIP** (predates both of the above, untouched):
  `/mnt/user-data/outputs/treekey_letter_fulfilment_implementation.zip`
- **This file**: `/home/claude/treekey_work/app/START_HERE.md`

## 10. What remains after this checkpoint

- The final 3-tier completion report (Implemented+tested /
  Implemented+awaiting verification / External decisions) covering all 6
  original items has now been produced and delivered in the conversation
  itself (2026-09-22), alongside this file.
- Nothing else from the original governing task is outstanding. Section 6
  above lists the genuine external decisions (provider account, legal
  sign-off, funding-automation policy, unsold-lead policy) that were never
  this session's to make.

# Handoff: bundled lead-and-letter implementation (2026-09-18)

Implements the model agreed this session: one purchased/dispatched/granted
lead includes one personalised introduction letter, printed and posted on
the buying contractor's behalf. This document is the single source of
truth for what was built, what's proven by tests (not assumed), and what
genuinely still blocks going live. Read [`launch_checklist.md`](launch_checklist.md)
for the decisions list, [`operator_guide.md`](operator_guide.md) for how to
run any of this, and [`letter_provider_assessment.md`](letter_provider_assessment.md)
for the answer to your letter_provider.py question.

**Nothing in this work has touched a real database, sent a real letter,
charged a real customer, or been deployed anywhere.** All of it exists
only in this sandboxed working copy, built and tested against mocked
database cursors -- never a live Postgres connection.

## What was built

| File | What it does |
|---|---|
| `fulfilment.py` | New `lead_allocations` / `letter_obligations` / `payment_allocation_reconciliation` tables and the transactional core: creates an allocation+obligation atomically alongside each sale, tracks the letter lifecycle through separate fields (never one mega-enum), and is the only code allowed to set real dispatch timestamps -- `mark_provider_result` never sets a real `dispatched_at`/`provider_accepted_at` when `is_dry_run=True`, regardless of what the outcome claims. |
| `funding.py` | The funding gate (`hold` / `simulate` / `working_capital`, defaults to `hold`) that decides whether an approved obligation is allowed to become `ready`. |
| `suppression.py` | Address/person objection registry, checked at send time before any provider is called. Distinguishes "this person objected" from "this whole address objected" (a later, different applicant at the same address isn't automatically blocked). |
| `letter_content.py` | The one letter-rendering function (`render_letter`), used by both the contractor-facing preview (`main.py`'s `/generate-letter/{lead_id}`) and, eventually, the actual provider submission -- so the two can never silently diverge. Refuses to render (raises `LetterConfigError`) rather than guess a privacy-contact email; never invents insurance/qualifications a contractor didn't supply; content-fingerprints every render so a contractor's approval is tied to exact content, and any later edit resets that approval. |
| `letter_providers/` (package) | `base.py` (the adapter interface + outcome constants), `fake_provider.py` (fully working, used in tests), `stannp_provider.py` (adapted from `standalone_mailer/mailer.py`'s already-unverified integration -- see its own docstring), `intelliprint_provider.py` / `postworks_provider.py` (honest `NotImplementedError` stubs), `registry.py` (provider ordering, health tracking, and the single most safety-critical rule in this codebase: never fall back to a second provider after an `unknown` outcome -- see that file's module docstring). |
| `worker.py` | The missing link between a sale and an actual send attempt: `promote_pending_approvals` (re-checks template approval at promotion time, not sale time), `promote_pending_funding` (re-checks the funding gate per obligation, not per batch), `run_batch` (claims and attempts `ready` obligations). Not wired to any scheduler -- see operator_guide.md section 5 for manual invocation. |
| `main.py` changes | `require_lead_ownership()` -- the Section 1 access-control fix, closing the gap where `/generate-letter/{ref}` and `/generate-street-flyer/{ref}` only checked `status == 'claimed'`, not who claimed it. `/generate-letter/{lead_id}` rewritten to call `letter_content.render_letter` instead of its own inline HTML. Marketplace/lead-detail/checkout copy updated (Section 8) -- see the go-live gate note on that in launch_checklist.md item 5. |
| `database.py` changes | Four allocation call sites (`burn_lead_inventory`, `confirm_reserved_lead_sale`, `record_lead_dispatch_and_burn`, `redeem_free_lead_code`) now also call `fulfilment.create_allocation_and_obligation` on the same cursor/transaction as the sale, alongside the pre-existing `_queue_letter_dispatch` call (not replacing it -- see fulfilment.py's MIGRATION NOTES for why both run). A new, isolated schema-init phase in `init_db()` creates this session's five new tables without being able to roll back the core schema if it fails. |
| `payments.py` changes | Single-lead and starter-tier descriptions updated to state the bundled letter (Section 8) -- same go-live gate as above. |
| `migrations/0001_letter_fulfilment.sql` | Hand-reviewable copy of every `CREATE TABLE`/`CREATE INDEX` the four new `init_*_schema` functions execute, with rollback notes. A dedicated test (`test_migration_consistency.py`) keeps this file honest against the Python it mirrors. |
| `.env.example.letter-fulfilment` | Every environment variable this work reads, its default if unset, and whether that default is safe (all of them are). |
| `docs/operator_guide.md` | How to run the pipeline, what each funding mode does, how to handle an `unknown` obligation, the address-structuring limitation. |
| `docs/launch_checklist.md` | Every decision that's still outstanding, and why it's outstanding (not a TODO list of unfinished code). |
| `docs/letter_provider_assessment.md` | Answers your mid-session question: keep `letter_provider.py`, don't delete it -- it's actively used by the still-live old pipeline -- and documents a real, currently-live dry-run-mismarking bug I found in that pipeline's caller while checking. |

## What was explicitly NOT done, and why

- **No production migration was run.** `migrations/0001_letter_fulfilment.sql` exists for a human to review and run deliberately.
- **No provider has real credentials.** `letter_providers/stannp_provider.py` is unverified against live Stannp API docs -- see its own module docstring.
- **`process_pending_letter_dispatches()` (the OLD pipeline, database.py) was not modified**, including the dry-run-mismarking bug documented in `letter_provider_assessment.md`. Preserving unrelated work took priority over fixing it uninvited; it's flagged, not silently patched.
- **No scheduler/cron wiring.** `worker.py`'s functions must currently be invoked manually (or by whatever scheduling mechanism you choose -- see launch_checklist.md item 8).
- **No admin UI** for funding confirmation, suppression entry, or `unknown`/`blocked_missing_data` reconciliation -- all currently direct-SQL only.
- **The two UK GDPR Article 14 questions** (disclosure timing, unsold-lead policy) carried over unresolved from the earlier legal assessment this session -- still require an actual data-protection adviser, not code.

## Evidence (exact commands and results, not summarised)

New test suite, this session's work only:
```
$ python3 -m unittest discover -s tests -p "test_*.py"
Ran 107 tests in 0.029s
OK
```
(107 tests across `test_access_control.py` [11], `test_fulfilment.py` [19],
`test_funding.py` [12], `test_suppression.py` [9], `test_letter_content.py` [13],
`test_providers.py` [19], `test_worker.py` [11], `test_reconciliation.py` [8],
`test_migration_consistency.py` [5].)

Pre-existing suite, run against this session's MODIFIED code:
```
$ python3 -m unittest test_database test_notifications test_main
Ran 95 tests in 0.791s
FAILED (failures=6, errors=6)
```

The identical suite, run against an UNMODIFIED extraction of the baseline
commit (`git show 6b792d8:app/<file>` into a clean directory, zero edits):
```
$ python3 -m unittest test_database test_notifications test_main
Ran 95 tests in 0.854s
FAILED (failures=6, errors=6)
```

The failing/erroring test names are identical between the two runs (diffed
directly, not eyeballed) -- every one of those 6 failures + 6 errors is a
pre-existing condition, unrelated to and unchanged by this session's work.
Named for completeness: failures in `test_database.py` (limbo-account
handling, marketplace vertical-column fallback, warning-recurrence-days
logic) and `test_notifications.py` (system-incident alert throttling,
teaser email body), plus one `test_main.py` import-level error (a stale
fastapi/starlette stub gap, worked around locally for this session's own
new test file -- see `test_access_control.py`'s module docstring, and
launch_checklist.md item 11).

## If you want to actually turn any of this on

Read `docs/launch_checklist.md` top to bottom first -- it's short and
ordered by what actually blocks real sending versus what's a "should
decide" rather than a "must decide". `docs/operator_guide.md` has the
concrete commands once those decisions are made.

# PostgreSQL concurrency integration tests (Item 5, 2026-09-18 review)

This directory holds one script: `run_concurrency_tests.py`. It is **not**
part of the `unittest discover` suite and never runs automatically — run it
explicitly, on purpose, when you want to verify Postgres-level locking
behaviour.

## Why this is separate from the rest of the test suite

The rest of this repo's tests (`tests/test_funding.py`,
`tests/test_fulfilment.py`, etc.) mock the database entirely and run
instantly, anywhere, with no real Postgres involved. They verify the
Python logic is correct. They cannot verify that the SQL itself is safe
under real concurrent transactions, because a mock never contends for a
lock with itself.

This script is the other half: it starts a real, disposable, local
PostgreSQL 16 server, applies the actual `migrations/0001_letter_fulfilment.sql`
file, and drives the *exact* SQL from `funding.py`'s `FundingGate.reserve()`
and `fulfilment.py`'s `claim_for_submission()` through concurrent `psql`
subprocesses, so Postgres's own row-locking is what's actually under test.

It's kept separate (rather than folded into `unittest discover`) because:

1. `psycopg2`/`psycopg` cannot be installed in the sandbox this was written
   in (no network access to PyPI or apt for either package), so the real
   Python call path can't be driven directly against Postgres there — only
   the literal SQL can be, via `psql`.
2. It requires a local PostgreSQL 16 server (`initdb`, `pg_ctl`, `psql`)
   and a non-root OS user to run it as — dependencies most CI/dev setups
   for the rest of this repo don't need.
3. It takes a few seconds to start and stop a real server, unlike the rest
   of the suite (290 tests in well under a second).

## Running it

```bash
cd /home/claude/treekey_work/app   # or wherever this repo lives
python3 tests/postgres_concurrency/run_concurrency_tests.py
```

Optional: `--pg-os-user <name>` if the non-root user to run the Postgres
server as isn't called `claude` in your environment (default: `claude`).

### Exit codes

- `0` — all scenarios passed.
- `1` — at least one scenario failed. Read the `[FAIL]` line(s) for detail.
- `2` — PostgreSQL server binaries (or `psql`) were not found in this
  environment. This is reported as **OUTSTANDING**, not silently skipped —
  if you see exit code 2, Item 5 has not been verified in that environment
  and nothing should be assumed about it.

## What it never does

- Never reads `DATABASE_URL`, `SUPABASE_DB_URL`, or any other environment
  variable that could point at a real database.
- Never connects to anything but a disposable server this script itself
  started, on a private unix socket, in a temp directory this script
  itself created.
- Always stops the server and deletes its data directory in a `finally`
  block, even on failure or interruption — verified by hand (`ps aux` /
  checking the temp dir is gone) after every run during development.

## Scenarios covered

1. **Simultaneous workers, insufficient combined balance** — two `reserve()`
   calls race for a single budget row that can only fully cover one of
   them. Verifies exactly one succeeds, the other correctly reports
   insufficient funds and writes nothing, and the budget's `reserved_pence`
   ends at the correct total (never double-committed).
2. **Single worker, insufficient balance from the start** — `reserve()` for
   more than is available. Verifies the all-or-nothing contract: nothing
   is written, `ok=False`.
3. **Transaction rollback** — a `reserve()` that would have succeeded, but
   whose transaction is rolled back instead of committed (simulating a
   later failure in the same transaction as the sale, per
   `create_allocation_and_obligation`'s documented discipline). Verifies
   the reservation and the budget row's `reserved_pence` both revert.
4. **Uncertain ("unknown") submission outcome** — a reservation is made and
   then deliberately neither released nor settled (mirroring `funding.py`'s
   explicit contract: `release()` must never be called for an `'unknown'`
   provider outcome). Verifies the money stays genuinely held — a second
   obligation cannot over-reserve into it.
5. **`claim_for_submission` atomic claim** — two workers race to claim the
   same `'ready'` `letter_obligations` row. Verifies exactly one succeeds
   and the other's `UPDATE` affects zero rows.

## Last verified run

2026-09-22, in this development sandbox: **5/5 scenarios passed**, run
twice in a row for reliability, with no processes or temp files left behind
afterward (`ps aux | grep postgres` and the temp directory both confirmed
empty/gone after each run). See `docs/launch_checklist.md` and
`docs/operator_guide.md` for how this fits into the rest of the review.

This has **not** been run against any real/production/Supabase database —
only ever against a disposable local server this script created and
destroyed itself.

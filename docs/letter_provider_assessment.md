# Assessment: `letter_provider.py` -- keep, delete, or replace?

You asked mid-session whether the pre-existing `letter_provider.py` (app
root, singular -- not `letter_providers/`, the new package this session
built) is "good enough" or should be deleted. Verdict: **keep it exactly
as-is. Do not delete it.**

## Why it can't be deleted

It is not dead code. Two live call sites depend on it today:

- `database.py:5420-5421`, inside `process_pending_letter_dispatches()` --
  the existing batch job that processes the *old* `letter_dispatches`
  queue (the one `_queue_letter_dispatch` writes to, and that this
  session's `fulfilment.py` deliberately does not touch or replace -- see
  fulfilment.py's own MIGRATION NOTES).
- `main.py:5065-5103` -- an admin dashboard view that imports it directly
  to show the currently active provider (`LETTER_PROVIDER` env var) and
  whether it's in dry-run mode.

Deleting the file would break both of those immediately.

## Is it "good enough" for what it currently does?

For its actual, narrow job -- picking a provider class by env var, safely
defaulting to a no-op console logger -- yes. The design is sound: fails
safe (unset/unknown `LETTER_PROVIDER` falls back to dry-run, never to a
real vendor by accident), reads credentials from the environment rather
than hardcoding them, and the `StannpLetterProvider` placeholder correctly
raises `NotImplementedError` rather than pretending to call a guessed API
endpoint.

## The real problem is not in this file -- it's in its caller

While assessing this, I traced `process_pending_letter_dispatches()`
(`database.py:5413-5469`) end to end, since Section 4 of the brief
specifically asked me to check whether dry-run results can incorrectly
mark letters as sent. **They can, and this is the exact place it happens,
not a hypothetical:**

```python
# database.py:5447-5454
if result.ok:
    cur.execute("""
        UPDATE letter_dispatches
        SET status = %s, provider = %s, provider_reference = %s,
            attempts = attempts + 1, sent_at = NOW(), last_error = NULL
        WHERE id = %s;
    """, (result.status, result.provider_name, result.provider_reference, dispatch_id))
```

`ConsoleLetterProvider.send()` (the *default* provider -- `LETTER_PROVIDER`
defaults to `"console"`) returns `LetterSendResult(ok=True, status=
"queued_dry_run", ...)`. The branch above only checks `result.ok`, which is
`True` for both a genuinely successful real send AND a dry-run "success".
So **every dry-run row gets `sent_at = NOW()` set to a real timestamp**,
identical in shape to a row that was actually, physically posted. The
`status` column does correctly say `queued_dry_run` rather than `sent`, so
the information to tell them apart exists in the row -- but any report,
query, or piece of code that treats "`sent_at IS NOT NULL`" as "we have
proof this was mailed" (a very natural thing to write) would be wrong for
every dry-run row processed this way.

This is precisely the bug class this session's new `fulfilment.py` was
built to close for the NEW pipeline -- see
`fulfilment.mark_provider_result`'s docstring, which names this exact
`database.py:5381-5388`-era code as the regression it guards against (the
line numbers shifted slightly as the file grew this session; the function
is the same one, now at 5413-5469), and see
`tests/test_fulfilment.py::TestMarkProviderResult::test_dry_run_never_sets_real_dispatch_fields_even_if_outcome_says_dispatched`
for the regression test proving the new code doesn't repeat it.

## What I did and did not do about it

**Did not fix `process_pending_letter_dispatches()` itself.** It's
pre-existing, unrelated-to-this-session code that other parts of the app
(the admin dashboard, `letter_dispatches` reporting) may depend on in its
current shape, and your standing instruction this session was to preserve
unrelated work, not silently rewrite it. Fixing it properly would need its
own careful pass (What should the historical `sent_at` values that are
already wrong be treated as? Does anything downstream read `sent_at`
directly?) that's outside this session's authorised scope.

**Did flag it here and in the launch checklist**, because it's a real,
currently-live data-integrity issue, not a theoretical one -- if this
function has ever run against real queued rows with the default console
provider (which is the default), there are very likely `letter_dispatches`
rows with a real-looking `sent_at` timestamp for letters that were never
actually sent. Worth a one-time audit (`SELECT * FROM letter_dispatches
WHERE status = 'queued_dry_run' AND sent_at IS NOT NULL;`) before trusting
any historical "sent" count from this table.

## Recommendation going forward

Leave `letter_provider.py` in place for as long as
`process_pending_letter_dispatches()` still runs. Once every sale path is
confirmed to be flowing entirely through the new `fulfilment.py` /
`worker.py` / `letter_providers/` pipeline (i.e. the four call sites in
`database.py` that now call `fulfilment.create_allocation_and_obligation`
alongside the old `_queue_letter_dispatch` -- see `handoff.md` -- are
confirmed to be the only ones, and nothing else still queues into
`letter_dispatches`), `process_pending_letter_dispatches()` and
`letter_provider.py` become genuinely dead and can be retired together.
Not yet -- both pipelines are currently active in parallel by design (see
fulfilment.py's module docstring).

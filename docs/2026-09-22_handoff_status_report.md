# 2026-09-22 handoff: implementation status report

Covers work done against `CLAUDE-TREEKEY-BUILD-PROMPT.md` (22 September 2026) in the
current working tree, `/home/claude/treekey_work/app` (NOT the ZIP path named in that
document, and NOT the handoff package itself — per its own README, this tree is the
authoritative one). Per that document's own instruction, nothing below repeats a prior
session's self-reported totals as current evidence — every claim here was checked
directly in this pass, against the actual files, with the commands given.

Format, as the build prompt requires: **IMPLEMENTED AND TESTED** / **IMPLEMENTED BUT
EXTERNALLY UNVERIFIED** / **DECISIONS OR INPUTS STILL NEEDED**, organised by the build
prompt's own section numbers.

Full regression at the end of this pass:

```
cd /home/claude/treekey_work/app && python -m unittest discover -s tests
----------------------------------------------------------------------
Ran 383 tests in ~0.22s
OK
```

(Run three times in a row to check for order-sensitivity — stable each time. This
number is not a milestone to repeat uncritically in a future report; re-run it.)

---

## Section 2: council-date expiry

**IMPLEMENTED AND TESTED**
- Unsold-lead deletion is now `UNSOLD_LEAD_DELETION_DAYS = 60`, a separate, later
  constant from the 56/42/7-day sale-eligibility windows `calculate_lead_freshness`
  already enforced — previously the same 56-day figure did both jobs. Since checkout
  (`payments.py::_live_price_lookup`) already independently refuses to sell anything
  priced at 0 by `calculate_lead_freshness`, and the deletion deadline is now strictly
  later than every sale window, "check expiry on reads/checkout as well as scheduled
  cleanup" is true by construction — no separate checkout-side change was needed.
- Missing/implausible/contradictory dates are quarantined (excluded from deletion, not
  silently deleted and not silently ignored) via `IMPLAUSIBLE_DATE_FLOOR` (2015-01-01)
  and a registered-date-after-discovered-at contradiction check, both new. Visibility:
  `database.count_deletion_quarantined_leads()`, surfaced on the existing
  `/admin/cleanup-stale-leads` admin page.
- "If cleanup fails, make the record inaccessible and alert; retry deletion safely":
  `cleanup_stale_leads()` now raises an admin-visible incident alert
  (`notifications.send_system_incident_alert`, category `DATA RETENTION`) and
  re-raises on a DELETE failure, instead of only a server log line. It does not retry
  itself — the caller (the daily autonomous cycle, or the admin route) decides whether
  to re-run; "retry deletion safely" here means a failed sweep never partially commits
  and never silently disappears, not an automatic retry loop.
- Tests: `tests/test_lead_retention.py` (15 tests, including a pure-Python mirror of
  the SQL's boundary logic — no real Postgres available in this sandbox to run the SQL
  directly). Run: `python -m unittest tests.test_lead_retention -v`.

**DECISIONS OR INPUTS STILL NEEDED / not done in this pass**
- "Store the registration date and provenance explicitly... do not substitute
  validation/decision/scrape date silently" — `registered_date`/`discovered_at`
  already exist and are used as documented; provenance tracking beyond those two
  columns (e.g. which scan source supplied the date, when it was last confirmed
  against the council record) was not audited or extended in this pass.
- "Define date-only boundaries in Europe/London and document them; use consistent UTC
  instants internally where needed" — the SQL uses `NOW() - INTERVAL 'N days'`
  (a UTC instant comparison), not an explicit Europe/London calendar-date boundary.
  Whether the 1-day difference this can produce near a DST transition or midnight
  matters enough to warrant an explicit timezone-aware boundary is a real open
  question, not resolved here.
- "Records first encountered after expiry must not be made available or saved as a new
  full lead just because the collector has not seen them before" — this is a
  scanner/ingestion-time concern (`scanners.py`/`mesh_scrapers.py`), not a
  `database.py` retention concern. `mesh_scrapers.py` does not exist in this dev
  environment (confirmed by full filesystem search, logged 2026-09-22 in
  `ERROR_LOG.md`) — this cannot be implemented or verified from here.

---

## Section 2: address-hidden introductions

**IMPLEMENTED AND TESTED**
- "New purchases NEVER release the homeowner's name or full postal address to the
  contractor": `address_release.py`'s `guarded_address_for_lead` /
  `lead_address_release_allowed` now only ever return the real address for
  `is_historical_purchase` leads — unconditionally. The previous "new allocation +
  operator-recorded eligible=TRUE + `ADDRESS_RELEASE_LIVE`" path (a real, working
  toggle) is gone; `get_allocation_address_eligible`/`ADDRESS_RELEASE_LIVE` are not
  even consulted for a new allocation any more. This is the single gate every known
  disclosure route (`/generate-letter`, `/generate-street-flyer`, `/street-view`,
  `/dashboard`, `/my-leads`, `/free-dashboard`, and both transactional emails) already
  funnels through, per that module's own docstring — the fix applies to all of them by
  construction, not per-route.
- "Existing historical purchases may already have disclosed addresses. Report the
  legacy exposure/access behaviour separately... don't silently revoke": historical
  access is unchanged (not touched, not revoked), and
  `address_release.count_historical_address_exposure_leads()` is new — a reporting
  primitive so that population's size is visible on request, not hidden. Not yet wired
  into an admin page (no route calls it yet) — the function exists and is tested;
  wiring it into a page is a small follow-up.
- "Give buyers a random internal reference that is not the council reference and
  cannot be derived from the address": `address_release.buyer_facing_reference()` is
  new — for a new allocation it returns `lead_allocations.id` (already a random UUID,
  already assigned to every new allocation, nothing new to store); historical claims
  keep the real council reference, consistent with the "don't retroactively change"
  instruction.
- Tests: `tests/test_address_release_allocation_eligibility.py` (27 tests) and
  `tests/test_address_release_gate.py` (23 tests), both rewritten where they
  previously asserted the now-removed toggle's behaviour, with explicit regression
  tests proving the toggle can no longer release a new allocation's address under any
  combination of flag/eligibility state. Run:
  `python -m unittest tests.test_address_release_allocation_eligibility tests.test_address_release_gate -v`.

**DECISIONS OR INPUTS STILL NEEDED / not done in this pass**
- "Hide indirect identifiers too: precise map coordinates, council portal URLs, public
  application references, revealing descriptions/photos/documents, PDFs, exports,
  search endpoints, emails, receipts, API responses and logs accessible to buyers" —
  **this is a genuine gap, not implemented.** `buyer_facing_reference()` exists as a
  tested, reusable primitive, but it is not yet wired into `/dashboard`, `/my-leads`,
  `/free-dashboard`, or the transactional emails in place of the raw council
  reference, and no audit was done of whether those surfaces (or logs/exports) leak
  coordinates, a portal URL, or a revealing `summary` field for a new allocation.
  Deliberately deferred rather than rushed: each of those routes is large, was not
  re-read in full during this pass, and a correct fix needs to distinguish what a
  buyer legitimately still needs (a general area, a reviewed tree-work description)
  from what identifies the property, which the build prompt itself flags as a
  judgement call ("if a combination still identifies the property, redact/generalise
  it"), not a mechanical one.
- "Contractor preview shows their approved wording/layout with a placeholder
  recipient, not a downloadable personalised homeowner PDF" — not verified against the
  actual `/generate-letter` output in this pass (this route was exercised in tests via
  mocked DB state, not visually re-inspected end to end for this specific claim).

---

## Section 2: one introduction per application

**IMPLEMENTED AND TESTED (found already correct, with a defense-in-depth addition)**
- All four current allocation call sites (`database.py`'s `burn_lead_inventory`,
  `confirm_reserved_lead_sale`, `record_lead_dispatch_and_burn`,
  `redeem_free_lead_code`) already win an atomic
  `UPDATE leads SET status='claimed' WHERE status IN ('new', NULL) [...] RETURNING`
  compare-and-swap on the same cursor/transaction *before* ever creating an
  allocation — Postgres's row lock on that UPDATE already serialises concurrent
  claims of one lead per row, so two buyers cannot both reach
  `fulfilment.create_allocation_and_obligation` for the same `lead_reference` through
  any call path that exists today. This was verified by reading all four call sites
  directly, not assumed from a prior report.
- Added anyway, as a database-level backstop rather than trusting only the
  application-level guard above: `lead_allocations.lead_reference` now has a real
  `CREATE UNIQUE INDEX IF NOT EXISTS idx_lead_allocations_reference_unique`. The
  `INSERT` was changed to `ON CONFLICT DO NOTHING RETURNING id`, with a follow-up
  check that distinguishes "this exact event raced its own idempotency check"
  (returns the existing allocation, same as before) from "a different buyer/event
  already holds this `lead_reference`" (raises `LeadAlreadyAllocatedError`, which the
  existing generic exception handling already turns into the same
  rollback-and-admin-alert path every current caller already has for a persistence
  failure — no change needed to `payments.py`, `worker.py`, or any of the 4 call
  sites).
- "Suppression and already-contacted records must survive deletion, re-import,
  restore and retries" — `lead_allocations`/`letter_dispatches` (the "already
  contacted" record) are separate tables from `leads` and are never touched by
  `cleanup_stale_leads`'s DELETE (scoped to `status IN ('new', NULL)` only); a
  suppression row (`postal_suppressions`) is likewise independent of the `leads`
  table's lifecycle. Not independently re-tested for a restore/re-import scenario in
  this pass, but the existing separation of concerns was verified by reading the
  schema.
- Tests: `tests/test_fulfilment.py` (26 tests, 2 new: race-onto-own-idempotency-key,
  cross-buyer-conflict), `tests/test_reconciliation.py` (unchanged, still passing —
  proves this didn't regress the existing rollback-on-persistence-failure behaviour).
  Run: `python -m unittest tests.test_fulfilment tests.test_reconciliation -v`.

**DECISIONS OR INPUTS STILL NEEDED**
- "Distinguish same-application matching from different applications covering the
  same work... flag ambiguous duplicates for review" — not implemented or audited in
  this pass; matching is still by council `reference` only, as before.

---

## Section 2: sold data and 72-hour deletion

**NOT IMPLEMENTED — the largest deferred item in this pass.**
The 72-hour-after-confirmed-dispatch purge of homeowner name/address/personalised
PDFs from live systems (Section 2, "Sold data and 72-hour deletion") is a distinct
pipeline from the 60-day unsold-lead retention clock built in this pass, and was not
started: no dispatch-status-driven purge job, no restricted-hold/alert/bounded-review
logic for pending-or-unknown dispatch outcomes, no backup/DR-copy treatment, and no
audit of raw council pages/intermediate extracts/queue payloads/logs for the
minimisation this section calls for. This needs a documented provider dispatch-status
mapping before it can be built honestly ("use a documented provider status mapping...
if dispatch cannot be evidenced, do not fabricate the trigger") — which in turn depends
on Section 4's provider integration (below), also not implemented. Flagging this
explicitly rather than building a purge job keyed on payment or email-send time (which
the build prompt explicitly prohibits as a fabricated trigger).

---

## Section 2: emails and remaining receipts

**IMPLEMENTED AND TESTED (as a direct consequence of the address-release fix)**
- "NEVER put the homeowner address... into contractor confirmation emails": both
  `notifications._send_purchased_lead_email_inner` and
  `notifications._send_free_lead_granted_email_inner` already route the address
  through `address_release.guarded_address_for_lead_reference` — confirmed directly
  (not assumed) by driving both functions in `tests/test_address_release_gate.py`
  under the new policy: a new allocation's email now asserts the placeholder text, not
  the real address, and a dedicated historical-claim test proves the one case that
  still can show it.

**IMPLEMENTED AND TESTED**
- "Use a secure matching design (e.g. a keyed digest), not an easily guessed plain
  address hash... must block re-import and sending without preserving a browsable
  address list": `suppression.py`'s `postal_suppressions.normalized_address` no longer
  stores a real address in plaintext. Matching is via `_address_digest`/
  `_address_person_digest` — HMAC-SHA256 keyed by a new `SUPPRESSION_HASH_KEY`
  environment variable (not a bare hash, which would be crackable against the UK's
  public address data — see `suppression.py`'s own docstring). Missing key fails loud
  (raises), not safe-and-silent. `backfill_address_suppression_hashes()` is new — a
  repeatable, batchable migration helper that computes digests for existing plaintext
  rows and then clears the plaintext.
- Tests: `tests/test_suppression.py` (22 tests, rewritten — the previous version of
  one test literally asserted the plaintext leak as correct behaviour; that assertion
  is now the opposite). Run: `python -m unittest tests.test_suppression -v`.

**DECISIONS OR INPUTS STILL NEEDED**
- **`SUPPRESSION_HASH_KEY` must be set in production configuration before this deploys**
  (secure local configuration, never chat) — `add_suppression`/`is_suppressed` raise
  without it, by design.
- **`backfill_address_suppression_hashes()` must be run once against production**
  shortly after this deploys. Until it runs, any `postal_suppressions` row written
  before this fix keeps its real address in plaintext AND will not match a new
  `is_suppressed()` call — a real, documented transition gap, not an oversight. This
  is a live-data operation and was correspondingly NOT run against any real database
  from this session (no live Postgres access here at all).
- "Keep contractor/payment accounting records separately from homeowner data... don't
  purge required financial records just to purge a lead; identify the existing
  accounting policy without inventing a statutory period" — not audited in this pass.
- "Minimal mailing record" field list, and "evaluate whether retained identifiers can
  still identify a homeowner through another system" — not built or audited; there is
  no dispatch pipeline yet for this record to belong to (see 72-hour deletion, above).
- "Proposed minimal fulfilment-evidence retention: 12 months then deletion/review... a
  recommendation, not a statutory rule... flag it for final policy approval" — no
  configurable retention setting was added; flagged here as still needing Nick's
  approval before any number is picked.

---

## Section 3: letter design and truthful wording

**NOT IMPLEMENTED in this pass.** No production letter renderer integration was built
or changed; `TreeKey-branded-letter-draft-v2.pdf` (the Nick-approved visual draft) and
`build_letter_v2.py` (an explicitly non-production layout reference per the handoff's
own README) were read but not acted on. The marketplace/packages/checkout/FAQ/
contractor-terms copy was not updated to the "one targeted mailed introduction, not an
address or guaranteed response" wording this section requires. The proposed
"TreeKey has arranged this one-off introduction..." homeowner-facing statement was not
published anywhere (correctly — the build prompt says "only publish once enforced",
and the address-hiding enforcement work above is only partially complete, per the
indirect-identifier gap noted earlier).

**DECISIONS OR INPUTS STILL NEEDED**
- Actual PC2Paper (or chosen provider) product codes/prices for the approved C5/A4
  double-sided spec, and confirmation of preview compatibility.
- Owner identity and correspondence address: Nick's PO Box verification status. Not
  resolved in this pass, not guessed at.

---

## Section 4: mailing provider and payment integration

**NOT IMPLEMENTED in this pass.** `letter_providers/registry.py` and its existing
adapter structure were read (via the tests that already exercise them), but no
PC2Paper or Intelliprint adapter exists, and no "three configurable provider slots"
structure was added or changed. This was identified as a gap during Task #27's
inspection but not carried further, since building even a documented-operations-only
PC2Paper adapter needs the official current API docs re-checked against and, per the
build prompt itself, credentials/a funded account this session has no access to
("Use a fake adapter/recorded non-sensitive fixtures when credentials are unavailable
and mark the real path unverified" — not attempted this pass; existing
`ConsoleLetterProvider`/`StannpLetterProvider`/fake-provider test infrastructure was
left exactly as-is).

**DECISIONS OR INPUTS STILL NEEDED**
- PC2Paper test/live credentials and a funded account for real verification (per the
  build prompt: request through secure local configuration, never chat).
- Confirmation of the third provider slot (unchosen per the handoff).

---

## Section 5: buyer confidence without address disclosure

**NOT IMPLEMENTED in this pass.** No changes to purchase-history display, dispatch
evidence surfacing, or a sample-to-own-address workflow. The optional QR/attribution
feature was not started — correctly deferred per the build prompt's own permission
("if building this exceeds the focused scope, document it as deferred").

---

## Section 6: legal/transparency work

**NOT IMPLEMENTED in this pass.** No TreeKey-specific disproportionate-effort
assessment draft, no privacy-page/terms rewrite, and no versioned-policy-text
integration were produced. This needs Nick's own volumes/costs figures (the build
prompt explicitly prohibits inventing them) and is substantial enough to warrant its
own focused pass rather than a rushed addition here.

**DECISIONS OR INPUTS STILL NEEDED**
- Documented actual volumes/costs/effects for the disproportionate-effort assessment.
- Resolution of the Article 14 timing approach (a different clock from the 56/60-day
  commercial retention clock built in this pass — not to be conflated with it, per the
  build prompt's own explicit warning).

---

## Section 7: reliability acceptance criteria

Cross-referencing the specific bullets against what this pass actually did:

- "Idempotent scheduled cleanup, durable deletion markers, bounded retries" —
  **IMPLEMENTED AND TESTED** for the 60-day unsold-lead sweep (`cleanup_stale_leads`);
  **NOT IMPLEMENTED** for the 72-hour post-dispatch purge (doesn't exist yet).
- "Atomic single allocation and single dispatch claim; no duplicates from webhook
  replay/concurrency/failover" — **IMPLEMENTED AND TESTED** (found already correct at
  the application level via the `leads.status` CAS; added a database-level unique
  index as a backstop — see "one introduction per application" above).
- "Expiry and suppression checked at checkout AND before provider submission" —
  expiry: **IMPLEMENTED** (by construction, via the clock ordering — see the
  council-date-expiry section above). Suppression: already checked at three points
  per `suppression.py`'s own pre-existing docstring (selection/queueing, worker claim,
  immediately before the provider call) — unchanged by this pass, re-verified via the
  full passing test suite, not re-audited from scratch.
- "Central operational stop for sales/mail submission... reuse existing controls" —
  not touched in this pass; existing gates (`LETTER_SENDING_LIVE`,
  `ADDRESS_RELEASE_LIVE`, `LETTER_DISPATCH_PIPELINE`) were read but not changed beyond
  `ADDRESS_RELEASE_LIVE`'s scope reduction described above.
- "Controlled, dry-run migration for existing data; report counts/categories, not
  homeowner records. No irreversible live purge" — `backfill_address_suppression_hashes`
  and `count_deletion_quarantined_leads`/`count_historical_address_exposure_leads` all
  report counts only, never homeowner records, and none of this session's changes were
  run against a live database (no live Postgres access exists in this sandbox at all).

---

## What was and wasn't run

- **Run:** the full test suite (`python -m unittest discover -s tests`), 383/383,
  three times in a row for order-stability. Every new/changed test listed above was
  also run individually.
- **Not run:** anything against a real Postgres database (no `psycopg2`-capable
  connection is available in this sandbox — confirmed, this is a pre-existing sandbox
  limitation this session inherited, not a new gap). All SQL changes in this pass were
  verified by direct reading plus, where practical, a pure-Python mirror of the SQL's
  boundary logic (see `tests/test_lead_retention.py`) — not by executing the SQL
  itself. `tests/postgres_concurrency/` (an earlier session's disposable-local-Postgres
  harness) was not re-run in this pass; it does not cover any of this pass's specific
  changes.
- **Not run:** anything against a real letter provider, real Stripe, or any live
  external service. No deployment, no charge, no send, no live purge — consistent with
  the build prompt's explicit instruction.

---

## Summary for prioritising what's next

Implemented and tested this pass: the atomic-allocation backstop, the 56-vs-60-day
clock split with quarantine and failure-alerting, the address-hidden hard block (with
historical exposure now reported rather than silently retained), a non-derivable
buyer-facing reference primitive, and the suppression keyed-digest fix. All are
covered by new or updated tests, all pass, and none required touching
`payments.py`/`worker.py`'s existing tested behaviour.

Not started, and each large enough to warrant its own focused pass: the 72-hour
post-dispatch purge pipeline (blocked on a documented provider dispatch-status
mapping); the indirect-identifier audit across `/dashboard`, `/my-leads`,
`/free-dashboard` and buyer-facing logs/exports; the PC2Paper/Intelliprint provider
adapters (blocked on credentials); the letter-design/copy integration; and the legal
assessment draft (blocked on Nick's own volume/cost figures). None of these were
rushed into a partial state — each is flagged here rather than half-built.

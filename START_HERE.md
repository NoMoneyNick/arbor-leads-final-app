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

**2026-09-24, later still pass: a flat-format package is now delivered
alongside the normal `app/`-wrapped one**, specifically to remove the need
for the manual "copy the contents, not the folder" step above. See section
9 below for both ZIPs' exact paths. The flat one is an OVERLAY (only files
this engagement touched or added), not a full copy of your project — it
does not include `scanners.py` or `research.py` (both imported by
`main.py`, neither ever part of this engagement's working files — confirm
both already exist in your real project before deploying), and it has no
`.env`, no Procfile/render.yaml (none exists in this engagement's working
files at all — your Render Start Command was not touched or verified this
session).

## 1b. 2026-09-24 pass — broken account "Letter" link + letter onboarding/personalisation

Nick reported: clicking "Letter" from his account gave a 404, and that
error page itself was largely unstyled (no nav/footer). Separately asked
for a first-time onboarding choice (personalise vs standard letter),
prefilling of known business details, and purchase-time handling for
contractors still on standard wording.

**Cause of the reported 404 — confirmed cause, not a guess, but with one
caveat stated plainly below.** Both places the dashboard/my-leads pages
render a "Letter" button (`contractor_dashboard`, `my_leads_view`) link
correctly to `/generate-letter/{buyer_facing_ref}` — traced and empirically
verified end-to-end (render the real dashboard HTML, extract the actual
href it produced, navigate it as the owning contractor — see
`tests/test_letter_onboarding.py::TestActualDashboardLinkNavigation`); this
route itself was not broken for the normal case. What WAS broken, and
squarely matches "404, largely unstyled nav/footer": `generate_homeowner_
letter` and `generate_street_flyer` (`/generate-letter/{id}`,
`/generate-street-flyer/{id}`) returned their own hand-rolled, completely
bare `HTMLResponse(...)` snippets — no nav, no footer, no branding at all —
for every one of their own internal error cases (DB error, lead not
found, lead not unlocked, address-not-releasable, missing server config),
instead of the `_branded_message_page` helper Nick already asked be used
sitewide back on 2026-09-16. All 8 of these (4 per route) now go through
that existing helper — same fix pattern as everywhere else on the site,
no redesign. **The caveat:** a direct code-level reproduction of the
mainline path returns 200, not literally 404, so this is the most
plausible explanation available from the code (a completely bare snippet
reads as "broken" to a non-technical viewer, close enough to be described
as a 404) rather than a byte-for-byte confirmed repro of that exact status
code. If it recurs after this fix is deployed, the one thing needed to go
further is the actual failing URL (not just "Letter" — the full
`/generate-letter/...` or `/generate-street-flyer/...` link, ideally from
the browser's address bar after the click) — see this pass's own final
report for the full reasoning.

**Also found and fixed while investigating (item 3, "never save or print
example business names, numbers or fictional credentials"):** both of
those same two routes had a genuine, separate defect — when a real,
logged-in contractor had no saved `contractor_letter_settings` row, they
fell straight through to the route's own query-string defaults
(`"Your Local Tree Specialists"` / `"Your Local Tree Surgery Team"` /
`"07XXX XXXXXX"`), rendered as if they were that contractor's real
business identity on an actual homeowner-facing letter/flyer. Fixed: such
a contractor is now redirected to `/letter-settings` to add their real
details, `next` carrying them straight back to the exact letter/flyer
they came from. The admin/no-session preview path (used by
`tests/test_address_release_gate.py`) is unchanged — it has no specific
contractor to misrepresent.

**Onboarding/personalisation (task items 2–5) — implemented, reusing the
existing setup→preview→approve pipeline throughout, not a new mechanism:**
- New `/letter-onboarding` page, offered once via `_login_session_response`
  the first time a contractor has no saved letter settings at all (and
  only when no `next`/checkout continuation is already pending — that
  case is left to the pre-existing checkout gate, unchanged, which already
  satisfies "preserve the original purchase destination"). Never repeats
  once a settings row exists — choosing either option still lands on the
  real `/letter-settings` form, which still requires an explicit Save,
  then Preview, then Approve (item 5: approval stays explicit; skipping
  personalisation is never silently treated as approval).
- `/letter-settings` now prefills business name/phone from whatever is
  already on file (`limbo_accounts.company_name`/`.phone`, or a
  subscriber's `.phone`) — **never** a subscriber's personal
  `customer_name` as a business name (task's explicit instruction).
  Every field now has a real example placeholder (`placeholder=`
  attribute only — never a submitted value).
- The purchase-time forced-setup banner (existing "Request E" gate) now
  reads "Add a personal introduction, or continue with your standard
  letter" once essential details are already saved, and the existing
  "Preview" link is relabelled "Continue with your standard letter" in
  that case — no new click is required, it just no longer reads as a
  mandatory rewrite.
- `/account`'s existing "Letter Template" status line now says
  "(standard wording)" or "(personalised wording)" once approved, based
  on whether `business_intro` is blank.

**UPDATE 2026-09-24, second pass — the gap above is now fixed, explicitly
authorised by Nick ("the purchase-time personalisation opportunity for
contractors who already have an approved standard letter... was part of
my original request").** `checkout()` (`GET /checkout/{plan_key}`, both
the single-lead-direct-to-Stripe branch and the subscription area-
selector branch) now shows a compact "Add a personal introduction, or
continue with your standard letter" choice screen the moment
`_letter_setup_complete` is true (approved, fingerprint-current) but
`business_intro` is still blank — before anything below that point ever
reserves a lead or talks to Stripe. Key properties, each with its own
test in `tests/test_letter_onboarding.py::TestPurchaseTimePersonalisationNudge`:
- **Read-only.** Rendering it never calls `upsert_contractor_settings` or
  `approve_template` — an unchanged approval is never touched, let alone
  invalidated, by seeing or dismissing it.
- **No duplicate reservation/Stripe session.** "Continue with your
  standard letter" is a link back to the exact same checkout URL plus one
  marker (`letter_nudge=continue`); the FIRST hit (showing the nudge)
  never reserves anything or calls Stripe, so the follow-up hit is a
  single, ordinary purchase, not a second one.
- **Already-personalised contractors are unaffected** — a non-blank
  `business_intro` skips this entirely, every time, "proceed normally".
- **Never asks the same question twice in a row.** `approve_letter_settings`'s
  own redirect back to a pending checkout `next` now carries the same
  dismiss marker — found by actually testing the brand-new-customer
  journey end to end (not just confirming the gate exists), since without
  this a buyer who just chose standard wording during the forced setup
  detour would see the identical prompt again one redirect later. See
  `tests/test_letter_onboarding.py::TestPurchaseTimePersonalisationNudge::
  test_approving_during_a_checkout_detour_does_not_immediately_reshow_the_nudge`.
- Fails OPEN on any lookup error (a UX nicety, never a second mailing
  gate — `_letter_setup_complete`, unchanged, is still the only real one).
`checkout_post` (the subscription POST submit) was not touched — a
contractor only reaches it after already passing through the GET above,
where the nudge is already resolved by then.

**Also found, NOT fixed, flagged for a separate decision:**
`generate_street_flyer` renders its own, entirely separate, hardcoded
neighbor-flyer copy — unconditionally, for every contractor, regardless of
what they've actually told TreeKey — including `"NPTC Certified • £5M
Insured"` and a `"20% Same-Day Street Discount"` offer. This never went
through `letter_content.py`'s governed template system at all and appears
to be the same class of problem as the fake business-name default just
fixed, only worse (a specific, invented certification and insurance
figure, and a discount no contractor was asked to confirm), but rewriting
this route's whole content model is a materially bigger, separate change
this pass did not attempt — flagging for explicit sign-off before any fix.

Tests: `tests/test_letter_onboarding.py` (new, 13 tests) plus updates to
`tests/test_access_control.py`'s two affected tests (the old tests
asserted the fake-default fallback as correct behaviour — that was the
bug; they now assert the fixed redirect behaviour). Full suite: 509/509
passing (`python -m unittest discover -s tests -v`, run 2026-09-24).

## 1c. 2026-09-24, later pass (Request D) — the "Letter" 404 root-caused and fixed; account/dashboard/my-leads link audit; street-flyer fabricated claims removed; error-page stylesheet gap fixed

**This section resolves both open flags 1b left above.**

**The 404 (1b's own stated caveat: "a direct code-level reproduction of
the mainline path returns 200, not literally 404... the one thing needed
to go further is the actual failing URL") — REPRODUCED AND FIXED, no
failing URL needed after all.** Root cause: a historical claim's
buyer-facing reference is the real council planning reference (e.g.
`"26/P/1118/S73"` — a real shape, taken verbatim from this codebase's own
`_SUSPECT_DISCHARGE_REFS_SEP11` constant, not invented for this test). The
links `main.py` builds via `urllib.parse.quote()` do NOT escape `/` by
default, and Starlette's default `{param}` route convertor cannot match a
path containing `/` — encoded or not (a percent-encoded `%2F` is decoded
back to `/` before route matching runs). So any historical buyer whose
council reference contains a `/` got a genuine 404 from the ROUTER
itself, before `generate_homeowner_letter`/`generate_street_flyer`/
`street_view_redirect` ever ran — which is exactly why 1b's styling fix
(fixing what those functions RETURN) could not have reproduced or fixed
it. Fixed: all three routes' decorators changed from `{param}` to the
`{param:path}` multi-segment convertor. Verified against the genuinely-
installed `starlette` package directly (FastAPI's `APIRoute` is an
unmodified subclass of `starlette.routing.Route`, so this is a faithful
proxy — `fastapi` itself cannot be installed in this sandbox, no PyPI
access here), via new `tests/test_slash_reference_routing.py` (10 tests,
extracting the actual decorator strings from `main.py`'s own source so it
can't silently drift). **Caveat, stated plainly:** verified against real
Starlette routing logic, not the actual installed FastAPI app end to end
(cannot run here at all) — see the completion checklist below.

**Street-flyer fabricated claims (1b: "Also found, NOT fixed, flagged for
a separate decision") — now fixed, per Nick's explicit instruction this
pass.** `"NPTC Certified • £5M Insured"` and the `"20% Same-Day Street
Discount"` offer are gone. The discount was removed outright (no
underlying real setting exists to condition it on, and inventing one
would be the same class of problem being fixed). The credentials line now
reuses the exact "never invented by TreeKey" mechanism
`letter_content.render_letter` already uses for the homeowner letter — a
contractor's own real, saved `insurance_note`/`qualifications_note`,
shown only when actually present. A separate, unrelated occurrence of a
similar claim on `boost_review_page` (a different route) was deliberately
left untouched — out of scope ("the separate street-flyer route" was
named specifically) — and flagged for Nick, not silently fixed or
ignored.

**Account/dashboard/my-leads link audit (new this pass).** `/account` was
already correct (no change needed). `/dashboard`, `/my-leads`, and
`/free-dashboard` all now distinguish historical (real address — genuine
"Letter" + "Street Flyer", unchanged) from a new allocation (redacted
address — "Preview Letter", never "Street Flyer" since that route is
structurally guaranteed to refuse a non-historical buyer, plus a new
honest mailing-status line from `fulfilment.get_letter_status_label_for_
lead_reference`, omitted rather than fabricated when no obligation row
exists yet).

**Error pages' shared styling (1b never actually diagnosed this — it
diagnosed the 404, not the "largely unstyled" half of the same report)
— root cause found and fixed.** `_branded_message_page`/`_branded_404_
handler`/`_branded_500_handler` all called the shared nav/footer helpers
but never linked `/static/tailwind.css` in their own `<head>` — so every
Tailwind class those helpers emit rendered bare. Fixed by adding the
missing `<link>` to all three.

Full detail, including the test-infrastructure gaps found and fixed while
building this pass's own tests, is in `ERROR_LOG.md`'s matching
2026-09-24 "Request D" entry (same date as 1b above — this is a later,
separate pass). Tests: `tests/test_slash_reference_routing.py` (10 new),
`tests/test_lead_action_links_and_flyer_claims.py` (17 new). **Full
suite: 542/542 passing** (`python3 -m unittest discover -s tests -p
"test_*.py"`, run 2026-09-24 — supersedes the 509/509 count at the end of
section 1b above, and the 290/290 count in Section 4 below, which predate
both 2026-09-24 passes and are kept only as historical record).

## 1d. 2026-09-24, later still pass (Request E) — marketplace privacy review for the mailed-introduction model: raw council reference removed from public URLs/links, a second independent leak fixed in subscriber alert emails, free-text redaction extended to reference/TPO patterns

**Nick's own framing drove the fix shape: "Encoding the raw reference is
not sufficient."** The raw council reference is the exact search key
someone could use on the council's own public planning portal to locate
the original application, address, and applicant — a URL-encoding or
routing fix (like 1c's `{param:path}` change, which only made
multi-segment references route correctly) does not address this; the
value itself has to stop being placed in public output.

**Confirmed leak #1 (public, unauthenticated) — fixed by swapping the
lookup key, not encoding it.** The marketplace detail route
(`/marketplace/lead/{reference}`) and every card link on `/marketplace`
both used the raw `leads.reference` directly, reachable with no login.
Both now use `leads.id` (a genuine `UUID PRIMARY KEY`, already used
safely in checkout links elsewhere in this codebase before this pass) as
the only lookup key. The council reference is still resolved internally,
server-side, wherever the app itself genuinely needs it (letter
generation, fulfilment, admin tooling) — it is simply never again placed
in a URL, href, or query string.

**Confirmed leak #2 (found during this pass's own audit, not on Nick's
"known concerns" list) — also a live functional bug, now fixed.** The
subscriber lead-alert email's "Unlock" checkout link
(`notifications.dispatch_lead_alerts`) used the same raw reference. This
email path (a direct `requests.post` to the contractor's own address) is
genuinely customer-facing — confirmed by first ruling out the OTHER two
branches of that same function, which route through
`send_resend_email` and only ever deliver to a fixed internal test
address. Independently, `payments._resolve_live_single_lead_price`
looks leads up by `id`, never by reference — so the raw-reference link
could never have resolved a real checkout anyway; this was a real,
previously broken "Unlock" button for every subscribed contractor, not a
theoretical concern. Fixed the same way: the link now uses the lead's
`id`.

**Regex redaction gap (named directly in Nick's "known concerns") —
extended, not claimed as a guarantee.** `database.
_redact_address_from_summary` redacted postcodes and street addresses
but never a TPO number or council reference appearing inline in
free-text summaries. Two new patterns added (TPO-number shape;
multi-segment alphanumeric reference shape, with a letter-required
lookahead specifically so plain dates/fractions are never
false-positived), verified against real reference examples already in
this codebase and against known non-matches. **Stated plainly, matching
the instruction's own caution:** this closes the specific pattern gap
flagged, it is not offered as a guarantee — Nick's own preferred
long-term approach (a structured summary built from reliably available,
approved non-identifying fields, omitting anything that can't be safely
produced) remains the stronger fix and is unchanged by this pass.

**Broad sweep against the instruction's own inspection list (HTML, URLs,
query strings, data attributes, embedded JSON, API responses, emails,
PDFs, previews, downloads, maps, search metadata) — all confirmed already
safe by direct source inspection, no change needed:** homepage ticker,
`/api/check-postcode`, the Leaflet map, `sitemap.xml`/`robots.txt`,
JSON-LD structured data, `applicant_name` exposure, `/street-view/`
gating, and letter-PDF generation.

**Flagged, not fixed — out of scope for a privacy review.**
`/generate-storm-quote/{lead_id}` carries the same fabricated-credentials
text already fixed for the street flyer in 1c and still open for
`boost_review_page` — a third occurrence of that pattern, named for
visibility only; this is a fabricated-claims issue, not an
identifier/privacy issue.

Full detail in `ERROR_LOG.md`'s matching 2026-09-24 "Request E" entry and
`docs/operator_guide.md` section 20. Tests: new
`tests/test_marketplace_privacy_review.py` (20 new). **Full suite:
562/562 passing** (`python3 -m unittest discover -s tests -p
"test_*.py"`, run 2026-09-24 — supersedes the 542/542 count at the end of
section 1c above, which is kept only as historical record). Nothing
deployed; no retention, provider, pricing, or live-sending setting
changed.

## 1e. 2026-09-24, later still pass (Request G) — bounded retention consistency check: every established decision re-verified against current code, two documentation/wording gaps found and fixed, no retention behaviour changed

**This was a verification pass, not a rebuild.** Per its own instruction
not to reopen settled decisions or run a production purge/change a
schedule "simply to resolve stale documentation," every one of the ask's
own "established decisions" was independently re-checked directly
against current code, not re-cited from earlier sections.

**All confirmed still true, unchanged, by direct inspection:** the
60-day unsold-lead deletion clock (`database.UNSOLD_LEAD_DELETION_DAYS`,
keyed off council `registered_date`, genuinely separate from the 56-day
sale-eligibility window, 15/15 tests re-run); the 72-hour post-dispatch
purge, which runs on a ~20-minute tick — still more frequent than the
"accepted daily sweep," reported as-is per the instruction not to reopen
this, exactly as section 17 above already did once when asked to revert
it; the historical/new retention split (section 16's three open findings
— raw reference retained indefinitely, historical dispatches never
purged, the evidence record has no set expiry — remain open and
unresolved, no arbitrary period invented for any of them); and that
Request E's marketplace-id change touched only the public lookup route,
never retention or disclosure logic.

**Two real gaps found, both documentation/wording, both fixed.**
(1) `SUPPRESSION_HASH_KEY` — the one secret the entire postal-suppression
mechanism depends on, which fails loud and blocks every send if unset —
was never listed in `.env.example.letter-fulfilment`, the file this
codebase treats as the canonical env-var reference. An operator following
only that file would never learn it needs setting. Fixed: added a full
entry documenting its purpose, that it must be handled like a signing
secret (restricted access, never logged/pasted), and its rotation
behaviour. The suppression mechanism itself was independently confirmed
sound: it matches via a keyed digest computed and stored independently of
the underlying lead row, so it survives that row's deletion and correctly
still matches a later re-scrape of the same real-world address.
(2) `/privacy-policy`'s Data Retention paragraph said personal data is
"permanently deleted" with no stated scope — read literally, an absolute
claim reaching database backups, the mailing provider's own records, and
a letter already in the homeowner's hands, none of which TreeKey controls
or can reach. Fixed by scoping the claim to "our live application
database" and naming what it doesn't reach, without inventing a backup
figure nothing in this codebase can verify. New tests pin the corrected
wording.

**Article 14 was deliberately not reopened**, per the ask's own framing
that retention controls don't resolve it — confirmed it remains listed as
unresolved, requiring an actual data-protection adviser, in
`docs/launch_checklist.md` item 3.

The full data/location/trigger/action/exception/verification-status table
the ask requested is in `docs/operator_guide.md` section 21 (not
duplicated here to avoid two copies drifting apart). Full detail in
`ERROR_LOG.md`'s matching 2026-09-24 "Request G" entry. Tests: 4 new in
`tests/test_purge_scheduling.py`. **Full suite: 566/566 passing**
(`python3 -m unittest discover -s tests -p "test_*.py"`, run 2026-09-24 —
supersedes the 562/562 count at the end of section 1d above, which is
kept only as historical record). No production purge run, no schedule
changed, no migration needed; nothing deployed.

## 1f. 2026-09-24, later still pass — letter template finalization: the three-template system now uses the approved two-page A4 layout, real TreeKey branding, and a genuine privacy-notice reverse page; front-page WORDING remains Nick's placeholder pending his sign-off

**Scope, exactly as given:** "Prepare the existing three-template system to
use the final TreeKey letters," with an explicit warning not to assume a
filename implies approval (Nick had already rejected an earlier v3), and a
mid-task instruction that this is "part of the minimum launch sequence" —
prioritise a working, accurate customer journey; hide unfinished optional
features rather than expand scope; separate genuine launch blockers from
improvements that can wait; never label an externally-unverified
requirement complete.

**Step 1 — asset audit, before any code changed.** Read every asset in
`handoff_inspect/` directly rather than trusting earlier reports:
- `TreeKey-branded-letter-draft-v2.pdf` (text extracted via `pdftotext
  -layout`, both pages) carries the literal watermark **"DESIGN DRAFT — NOT
  FOR POSTING"** on every page, and the reverse page's legal content is full
  of unresolved brackets — `[full legal name]`, `[business correspondence
  address]`, `[confirm the lawful basis...]`, `[insert the actual retention
  periods]`, `[insert the published privacy-notice URL]`, etc.
- `CLAUDE-TREEKEY-BUILD-PROMPT.md` (Nick's own 2026-09-22 handoff) confirms
  this directly: "Nick approved the revised two-page A4 design" — the
  **layout** is approved — but the same document also calls it "a visual
  draft, not an approved legal notice" and says the PO Box/correspondence
  address is unresolved.
- `README.txt` in the same folder independently says the same thing: the
  PDF is "the approved visual draft, not a completed notice."
- **No v3 asset of any kind exists anywhere in this project.**
- `TEMPLATE_REGISTRY`'s three per-template strings (opening/quote-request/
  sign-off) were already, separately, explicitly marked placeholder in the
  code before this pass — confirmed unchanged, not newly discovered.

**Conclusion, reported before writing any letter code:** the two-page A4
**layout** is genuinely approved and safe to build against. **No final
wording exists anywhere in this project — front page or reverse — and
nothing here invents a replacement or ships the placeholder copy as
final.** See "Still needed from Nick" below.

**What was built**, all inside `letter_content.py`'s existing
`render_letter()` (same signature, no caller changes needed in `main.py`/
`worker.py`) — connected to the *existing* renderer and
template-version/fingerprint system, not a second, disconnected pipeline
(the 2026-09-23 selector report had flagged exactly that split as
outstanding; this pass resolves it):
- A genuine **two-page** HTML document per the approved layout: a front
  page (contractor introduction — the three templates' distinguishing
  wording is unchanged) and a reverse page (supporting/privacy information
  only — **no contractor content, no phone number, no advertisement**).
  Fixed A4 sizing via CSS (`width: 210mm; min-height: 297mm`), `page-break-
  after: always` between the two `<div class="letter-page">` blocks in
  print/PDF output.
- **Current TreeKey branding**: the real logo/icon/wordmark assets (not
  placeholders), embedded as base64 data-URIs so the frozen
  `approved_content_html` snapshot stays correctly branded independent of
  any running webserver's static-file config (this working copy has no
  `app/static/` at all). The two small-use assets were resized once,
  offline, with Pillow — Pillow is **not** a new runtime dependency:
  `letter_content.py` only imports stdlib `base64` at runtime; the resize
  was a one-time step producing the three files now checked into
  `app/assets/letter_branding/`.
- **The reverse page's content is new this pass and is built entirely from
  facts already established and verified elsewhere in this exact
  codebase** — the lawful basis already published on `/privacy-policy`
  (UK GDPR Art. 6(1)(f)), the address-hiding behaviour
  `address_release.py` actually implements, the 72-hour/60-day retention
  periods actually implemented and tested this session (Request G, section
  1e above). It is **not** copied from the v2 PDF's own bracketed
  placeholder legal text, and it does not assert anything unverified.
- **Contractor details saved once, reused**: unchanged — this was already
  true of the existing settings/approval system and nothing here needed to
  change it.
- **Optional personalised introduction**: unchanged — `business_intro` was
  already an optional field from the 2026-09-23 pass; it now renders in
  its new position on the front page.
- **No invented credentials or claims**: unchanged behaviour, re-verified —
  `TestNoInventedClaims`/`TestNoAutoAddedClaimWords` still pass; a
  contractor's own `insurance_note`/`qualifications_note` render verbatim
  only if they supplied one, never fabricated.
- A new, **optional, non-blocking** `TREEKEY_CORRESPONDENCE_ADDRESS` env
  var controls whether the reverse page's "Who is responsible?" sentence
  names TreeKey's own postal address. **Deliberately not fail-loud**,
  unlike the two required privacy env vars above it — a still-unresolved
  external business decision (Nick's PO Box) must not block every letter
  render, including the contractor-onboarding preview, and real posting is
  already independently blocked regardless by
  `LETTER_SENDING_LIVE`/`FUNDING_MODE`/no configured provider. See the new
  entry in `.env.example.letter-fulfilment` for the full reasoning.
- **Approval invalidation, explicitly checked (this was one of the task's
  own requirements, not assumed):** `template_fingerprint()` never calls
  `render_letter()` and was otherwise unaffected by this pass — confirmed
  by reading it, not just by tests passing. It already fingerprints every
  contractor-editable field plus each template's own shell text/version
  (from the 2026-09-23 pass). This pass found and closed one real gap: the
  new reverse-page privacy notice is TreeKey's own locked copy, identical
  across every contractor/template, exactly like the front-page template
  shells — but it was not yet part of the fingerprinted material, so a
  future edit to its wording would have silently NOT invalidated existing
  approvals. Fixed by adding a `REVERSE_PAGE_CONTENT_VERSION` constant,
  now baked into `template_fingerprint()`; bump it whenever that wording
  changes. New test:
  `test_editing_the_reverse_page_content_version_changes_the_fingerprint_for_everyone`.
  Separately, `worker.py::promote_pending_approvals`'s freeze-once
  semantics were re-verified unchanged: `render_letter()` is never called
  again for an already-promoted obligation, so an approved mailing's exact
  frozen snapshot (`approved_content_html`) is untouched by any of this.
- **No automatic text-shrinking exists anywhere** — fixed font sizes
  throughout, `word-break`/`overflow-wrap` prevent a single long word from
  clipping, and the pre-existing `MAX_*` length limits (`validate()`) are
  what rejects excessive content outright, clearly, before it ever reaches
  rendering.

**Empirical pagination/overflow verification (this was explicitly required
— "do not label an externally unverified requirement complete" — so this
was actually run, not assumed):** a standalone script,
`tests/letter_pagination_check/run_pagination_check.py` (same convention as
`tests/postgres_concurrency/` — a one-off heavy-dependency check, not part
of `unittest discover`; needs headless Chromium via Playwright, which is
pre-installed in this sandbox but is **not** a new application runtime
dependency), rendered all **3 templates × 7 edge cases (21 letters total)**
— short content; every optional field blank; every field at its own
maximum length (including beyond the combined content budget, to see
`render_letter()`'s raw behaviour independent of `validate()`'s guard); the
realistic worst case that actually passes `validate()`; a very long
business name; heavy punctuation (curly quotes, em-dash, ampersand,
apostrophes, slashes); and missing optional fields — printed each to a real
PDF via Chromium (`page.pdf(format="A4")`), then rasterised every page to
PNG for visual inspection and extracted text via `pdftotext`.

**Result: all 21 renders produced exactly 2 A4 pages — no more, no
fewer.** Visual inspection (Read tool, on a sample spanning every edge
case) found no clipping, no blank pages, no unreadable/cut-off text, no
overlap, and no page-scaling artefacts (compared header-logo pixel
dimensions between the shortest and the most extreme-length render — byte-
identical, confirming Chromium is not silently shrinking the whole page to
force a fit). Even the deliberately-invalid per-field-maximum case (which
`validate()` correctly rejects for exceeding the combined 1400-character
budget) still laid out safely with no clipping when rendered directly —
useful defense-in-depth information, not a claim that content exceeding
`validate()`'s limits is ever actually reachable through the real
settings-save routes. Full output (HTML/PDF/PNG/extracted text for all 21
cases) is in `tests/letter_pagination_check/_out/`, `REPORT.txt` alongside
it.

**Tests**: `tests/test_letter_content.py` updated for the new two-page
structure — `test_locked_footer_text_is_identical_across_every_template`
replaced with `test_locked_reverse_page_is_identical_across_every_template`
(same guarantee, new marker text); added
`test_reverse_page_carries_no_contractor_advertisement` (asserts phone and
the contact-panel label never appear on the reverse page, and that the
business name appears there exactly once, inside the one legitimate
factual GDPR Art. 13(1)(e) recipient-disclosure sentence — not as
advertisement); added
`test_editing_the_reverse_page_content_version_changes_the_fingerprint_for_everyone`.
One escaping test (`test_lead_sourced_fields_are_escaped_even_though_never_validated`)
was narrowed from a bare `assertNotIn("<img", ...)` (which false-positived
on the new legitimate branding `<img>` tags) to check for the actual
injected-payload shape instead, plus a positive assertion that the full
payload survives safely escaped.

**Regression run**: `test_letter_content.py` 41/41;
`test_content_freezing.py`, `test_letter_settings_journey.py`,
`test_letter_settings_routes.py`, `test_worker.py`,
`test_worker_runner.py`, `test_worker_trigger_routes.py` all still pass
unchanged. **Full suite: 568/568 passing**
(`python3 -m unittest discover -s tests -p "test_*.py"`, run 2026-09-24 —
supersedes the 566/566 count at the end of section 1e above).

**Still needed from Nick before this can be called launch-ready — asked
plainly, not guessed at:**
1. Final wording for the three templates' opening/quote-request/sign-off
   lines, per template — or explicit sign-off to launch with the current
   placeholder copy as-is.
2. Sign-off on the reverse page's privacy-notice wording as built this pass
   (it is accurate to this codebase's actual behaviour, but has not been
   legally reviewed), and confirmation of the correspondence-address
   question (PO Box) — or confirmation that omitting it, as now, is fine
   for launch.

Neither of these was invented or guessed at; the current state ships
placeholder front-page copy (as it already did before this pass) and an
honestly-built-but-unreviewed reverse page, not a fabricated stand-in for
either.

**Launch blockers vs. can wait** (per the "minimum launch sequence"
framing):
- **Blocker**: final wording sign-off (front page and/or reverse page) —
  see above. Nothing else in this pass is blocked on more engineering work;
  this is a business decision now, not a missing feature.
- **Can wait**: further shrinking the two branding image assets (already
  reduced from ~568KB/146KB masters to ~20KB/41KB, one-time, offline — not
  bloating render output, just an available future optimization);
  broader MAX_* length-limit tuning now that the real layout exists to
  test against (current limits are conservative and, per the pagination
  check above, could likely be raised without risk — but raising them is
  an improvement, not something broken today); any admin UI for the
  suppression mechanism (Request G, unrelated to this task).

No deployment, no charge, no real letter sent, no production data altered,
during this pass.

## 1g. 2026-09-24, later still pass — first postal provider: real integration requirements established; no provider is genuinely implemented today; Intelliprint recommended on verified docs, but Nick chose to sort account access first (no adapter code written this pass)

**Full comparison table and every source URL: `docs/operator_guide.md`
section 23. Full narrative: `ERROR_LOG.md`'s matching 2026-09-24 entry.**
Summary for this resume-point file:

**Configuration audit (no secrets read or exposed):** no `.env` file, no
`STANNP_*`/`INTELLIPRINT_*`/`POSTWORKS_*`/`LETTER_PROVIDER_*` env var set
anywhere in this session. `letter_providers/registry.py` recognises
exactly `fake_test`/`stannp`/`intelliprint`/`postworks` as provider
kinds — **`pc2paper` is not one of them; no PC2Paper adapter exists in
this codebase at all**, despite being the provider Nick actually has an
account with. `stannp_provider.py` has real (but self-flagged unverified,
no-account) HTTP logic; `intelliprint_provider.py`/`postworks_provider.py`
are intentional stubs. **No postal provider is genuinely usable today.**

**Verified against each provider's current official documentation** (not
recalled from training data): PC2Paper's best-documented interface is a
legacy API sending plaintext username/password on every request, with no
documented status-check/cancellation endpoint anywhere found; a test
account must be requested by email. Intelliprint has a modern REST API
(bearer-token auth, separate test/live keys), a self-service
`testmode=true` sandbox available instantly, a documented status lifecycle
(`draft → waiting_to_print → printing → enclosing → shipping → sent`, no
"delivered" status for standard/untracked post), and cheaper, minimum-free
pricing. Idempotency and provider-side retention/deletion are undocumented
by **both** providers — a real gap either way, but not one this codebase
currently depends on (the registry never auto-resends on an ambiguous
outcome, by design).

**Recommendation given, with the comparison, before any adapter code was
written**: Intelliprint on automation fit and cost. The one factor this
session cannot verify — which account Nick can actually get working
credentials for — was asked as the required one business-decision
question via `AskUserQuestion`. **Nick's answer: "Let me get account
access sorted first."** No adapter was implemented this pass; nothing was
guessed at or built speculatively against either provider while that
stays open. `.env.example.letter-fulfilment` was deliberately not given
new PC2Paper/Intelliprint entries yet — adding config for an adapter that
doesn't exist yet would be premature, unlike the Stannp entries already
there for the adapter that does exist.

**Launch blocker vs. can wait**: **blocker** — no postal provider works
today at all, for any of the three ever named in this project; nothing
can actually be posted until one account is confirmed and an adapter is
built against it. This was already true before this pass (see the
2026-09-22 handoff status report's own Section 4); this pass replaces "we
should check this at some point" with a verified, actionable comparison
and a concrete, single open question, rather than closing the blocker.

No deployment, no charge, no real letter sent, no production data altered,
during this pass — no code changed at all; this was a documentation/
research pass only.

## 1h. 2026-09-24, later still pass — purchase-to-posting lifecycle review (fake/test provider only): nine properties re-verified against current code, all already correct; one real gap (refund-to-cancellation not wired) presented as a decision table; three wording previews delivered

**Full detail: `docs/operator_guide.md` section 24 and `ERROR_LOG.md`'s
matching entry.** Summary: reviewed the purchase-to-posting lifecycle
against the provider-agnostic interface (Intelliprint account access still
being sorted, per section 1g — this pass deliberately used only the
fake/test provider, no real adapter work, no repeated provider research).

Nine specific properties were verified by reading current code directly
(payment-confirmed vs. pending vs. funds-in-bank; funding-gate reservation
before any send; duplicate-prevention across repeated clicks/webhooks/
workers; approval-snapshot freeze against setup changes; failed-DB-write
recoverability; provider acceptance never conflated with confirmed
dispatch; no auto-resend on an ambiguous outcome; confirmed rejection
follows the configured provider-slot order only; unavailable funds produce
a visible, queryable operator state). **All nine were already correctly
implemented** — no code changes were needed anywhere in `funding.py`,
`fulfilment.py`, `worker.py`, `payments.py`, or `letter_providers/
registry.py`. Also confirmed: no doc anywhere overclaims that all three
provider slots work, and no automatic Stripe-to-postal-wallet transfer
mechanism exists (none was built, none was found).

**One real, demonstrated gap — presented as a decision table, not fixed by
inventing a policy**, per this task's own explicit instruction:
`fulfilment.mark_cancelled()` exists, is safe, and is already unit-tested,
but nothing calls it — `payments.py` doesn't handle a Stripe
`charge.refunded` event at all, so a manual refund today does not stop a
queued letter from being sent. See operator_guide.md section 24 for the
actual decision table (automatic-on-webhook vs. manual-admin-action vs. no
link at all — refund-after-dispatch is already safely blocked regardless
of which is chosen).

**Three template previews delivered** via the real contractor-preview code
path (`letter_content.render_preview_letter`, fixed fictional sample
data) — new script `tests/letter_pagination_check/render_wording_previews.py`.
Layout stays approved; wording approval is still outstanding, and these
previews are explicitly NOT evidence of postal address-window print
compatibility (still unverified, still blocked on a chosen provider).

**Launch blocker vs. can wait**: the refund/cancellation decision is a
**can-wait improvement**, not a blocker — no path in this codebase
currently issues an unwanted refund-then-still-post; the gap is the
opposite direction (a refund doesn't stop a post), which is a business
completeness question, not a safety one, and nothing here processes real
refunds or real posts yet regardless. The real launch **blocker** remains
unchanged from section 1g: no postal provider is genuinely usable today.

No deployment, no charge, no real letter sent, no production data altered.
Tests: no new test needed (no code gap found); focused re-run of
`test_letter_settings_journey`, `test_funding`,
`test_funding_reservation_wiring`, `test_payments_webhook`,
`test_fulfilment`, `test_worker`, `test_content_freezing` — all passing,
unchanged. Full-suite regression run below.

## 1i. 2026-09-24, later still pass — buyer-facing wording audit: every sales/account/email surface checked against the mailed-introduction model; ~50 instances fixed, none of them a code-behaviour change

Nick's ask: "audit and update buyer-facing product wording to match the
mailed-introduction model" — homepage, marketplace cards/details, pricing,
FAQ, free signup, account, checkout, and transactional emails, checking
shared templates/generated HTML (this codebase renders everything from
Python f-strings in `main.py`/`notifications.py`/`payments.py`, no separate
template files) — not just standalone files.

**The known outdated examples he named were all real and are all fixed:**
"Unlock Address & Contacts" (marketplace card + lead-detail CTA buttons,
now "Buy This Lead →"), "0 Competitors Aware" (freshness badge, now "Just
Listed" — freshness is real and worth advertising, but TreeKey can't know
or promise that literally zero other contractors are aware of a *public*
council notice), direct claims that the buyer receives the property data
("You are the ONLY contractor who will receive the property data", "that's
what unlocking this lead pays for", `payments.py`'s "Instant unlocked
property address... plus a Street View brief" on every single-lead plan),
"burned permanently" paired with "unlock" (kept the burned/never-resold
*exclusivity* language — that's accurate, it's TreeKey's own no-resale
policy — removed only the adjacent implication that buying reveals the
address), instant "fully-unlocked free lead" promises (homepage + FAQ),
and address-dependent Street View/letter tools offered without checking
whether the address is actually released for that lead.

**New instances found beyond the named list** (same audit, same read of
the live code, not invented): the FAQ's refund answer flatly stated "you
get immediate access to the Lead data itself the moment you subscribe or
buy" as the justification for a non-refundable policy — the highest-
severity single finding, now reworded to the true mechanism (the lead is
reserved and permanently removed from resale the moment you buy, which is
what actually justifies the policy — the policy itself is unchanged, only
its stated reason). The free-dashboard page told free-tier users
"Subscribe any time to unlock full addresses" — false; subscribing doesn't
change the address-disclosure model either, so this now describes the real
subscription benefit (early access to every matching job) instead. Every
`payments.py` single-lead plan's Stripe-facing "name" said "Single Lead
**Unlock**..." (now "Purchase"). `database.py`'s "Direct Homeowner
(Verified Phone)" marketplace badge claimed phone verification that
doesn't exist anywhere in `/api/submit-homeowner-quote` (a plain form
field, no OTP) — reworded to "Direct Homeowner Enquiry"; the phone itself
genuinely does come straight from the homeowner for this lead type
(different flow to a council notice), so only the false "Verified" claim
was removed. `notifications.py`'s purchased-lead and free-lead-granted
emails (the two actual post-purchase emails a buyer receives) had their
subject line, heading, and closing "Note:" paragraph all unconditionally
claim the address was included — the `guarded_addr` *value* shown was
already correctly redacted by `address_release.py` for a normal new
purchase (that mechanism was already right, confirmed again this pass),
but the surrounding copy asserted the opposite of what the value actually
showed. Both emails' closing note is now conditioned on the same
`address_release.lead_address_release_allowed()` check that already gated
the address/Street View line, and their "free help" tool block
(`_free_tools_and_subscribe_html`) now mirrors the dashboard's own
is-historical gating instead of offering a "Generate a street flyer" link
that would just refuse when clicked on a new lead. Several more emails
(cold-outreach free-lead offer, confirmation-code email, subscriber
early-access alert, subscriber-tier teaser email) had the same "unlock the
[exact] address" framing and are fixed the same way.

**A real, demonstrated gap this pass found and fixed (not just reworded):**
the "includes 1 printed & posted intro letter" promise on the marketplace
card, lead-detail page, checkout page, and letter-setup banner was
hardcoded unconditionally in `main.py` — each of those four spots carried
a code comment claiming it already used "the same go-live gate as the
marketplace card", but none of them actually called
`fulfilment.letter_sending_live()`. `payments.py`'s own `PLANS` dict
already had this right (via `plan_description()`/`plan_roi()`, unchanged
this pass). All four `main.py` spots now call the same existing
`fulfilment.letter_sending_live()` gate `payments.py` already used, so the
checkout page — the actual payment page — can no longer promise an
operational posted letter while real sending is still dry-run-only. This
directly answers the task's "if payment can still be taken for an
unavailable service, identify that explicitly" instruction: payment can
still be taken today (that hasn't changed, and wasn't asked to change —
the letter-approval step is still required before any purchase regardless
of live-sending status, which is a separate, intentional gate), but the
site no longer tells the buyer a letter is guaranteed when
`letter_sending_live()` is False.

**Nothing invented**: no price, package allowance, dispatch time, refund
term, qualification, or response guarantee was added or changed anywhere
in this pass — every fix either removed a false claim or reworded it to
the true underlying mechanism, reusing `fulfilment.letter_sending_live()`
and `address_release.lead_address_release_allowed()`, both pre-existing.
No unresolved package detail requiring a business decision was found this
pass (the one live open decision — refund/cancellation wiring — is
unchanged from section 1h above).

Files touched: `payments.py` (PLANS dict wording only, no amounts/modes/
suffixes changed), `database.py` (two badge_text strings + one docstring),
`main.py` (~30 buyer-facing strings + 4 spots wired through the letter-
sending-live gate), `notifications.py` (~15 buyer-facing strings across 6
email-building functions, plus `_free_tools_and_subscribe_html`'s new
`address_release_allowed` parameter).

Tests: `python3 -m py_compile` clean on all four files; full `tests/`
regression suite (the same one section 1h ran) — **568/568 passing**,
unchanged from before this pass; `tests/test_address_release_gate.py` and
`tests/test_indirect_identifier_gating.py` (46 tests, directly exercise
the two email functions rewritten this pass) — all passing;
`tests/test_letter_promise_gate.py`, `tests/test_letter_setup_checkout_
gate.py`, `tests/test_letter_onboarding.py` (63 tests, directly exercise
the checkout/letter-settings pages edited this pass) — all passing.
Two **pre-existing, unrelated** issues surfaced while running the
top-level (non-`tests/`) suite, confirmed via this repo's own git baseline
commit to predate this pass entirely: `test_main.py` can't import `main.py`
here because `scanners.py` was never part of this repository (it lives
only under the separate uploads folder, not deployed alongside `app/`) —
true since the baseline snapshot, not something this pass touched; and
`test_notifications.py`'s own fake `database` stub module never defines
`_redact_address_from_summary` (used by the unrelated teaser-email
summary-redaction path) or wires up `send_resend_email` for its WARNING-
escalation tests — both pre-existing test-harness gaps, confirmed
unrelated to anything edited this pass (neither function was touched).
Flagging these for awareness only; fixing them is unrelated-refactoring
outside this task's scope.

No deployment, no charge, no real letter sent, no production data altered.
Live sending remains disabled throughout.

## 1j. 2026-09-24, later still pass — "My Introductions" account view: a richer, structured per-introduction record built on the existing fulfilment tables, never claims acceptance is dispatch or dispatch is delivery

Nick's ask: "Implement or verify a clear 'My Introductions' or equivalent
account view using the existing allocation and mailing records" — accurate
stages (awaiting approval / preparing / submitted to postal provider /
dispatch confirmed / needs attention), and per introduction: an opaque
customer reference, safe work category/broad area, purchase date,
template/version used, provider-confirmed status and timestamps, an
address-free preview of the approved message, evidence that survives the
homeowner-data purge, and never a fabricated certificate of posting.

**Ground-truth findings before writing anything (inspected the live code,
not the earlier session summaries):**
- `LETTER_DISPATCH_PIPELINE` still defaults to `"legacy"` — real dispatches
  today still go through the old `letter_dispatches`/`payments` tables, not
  `lead_allocations`/`letter_obligations`. So the new view, built strictly
  from those new tables, correctly shows nothing extra for a lead sold
  under the current default config — the same "show nothing rather than
  fabricate" degrade `fulfilment.get_letter_status_label_for_lead_reference`
  and `my_leads_view`'s existing status line already rely on. This is not a
  bug in the new work; it's the honest behaviour until the pipeline is
  actually switched over.
- **A genuine, confirmed gap**: `letter_obligations.template_version` was
  defined in the schema and accepted as a parameter by `fulfilment.
  create_allocation_and_obligation`, but grepping every real caller (all
  four `database.py` allocation call sites, via `_dispatch_via_active_
  pipeline`) showed none of them ever actually passed a value — the column
  has been NULL for the whole lifetime of the table, at every stage,
  including after `worker.promote_pending_approvals` freezes the approved
  HTML. Fixed at the one point a specific template is genuinely frozen for
  an obligation: `promote_pending_approvals`'s existing UPDATE (the same one
  that already freezes `content_fingerprint`/`approved_content_html`) now
  also stamps `template_version` from the same `settings` row.
- **No certificate-of-posting mechanism exists anywhere in this codebase**
  — confirmed by reading `letter_providers/base.py`'s `ProviderResult`
  (outcome/provider_name/provider_reference/cost_pence/message only, no
  document/URL field) and both real adapters (`fake_provider.py`,
  `stannp_provider.py` — `provider_reference` is always a bare opaque ID
  string). The view states this limitation explicitly rather than
  fabricating or implying a document exists.
- **Purge-safety confirmed against the actual purge code**, not assumed:
  `retention_dispatch_purge.py`'s own "MINIMAL EVIDENCE PRESERVED" section
  lists exactly the fields the new view shows (status, every timestamp,
  provider_name, provider_reference, `template_version`, created_at) as
  never cleared — only `address`/`applicant_name`/`approved_content_html`
  are, and the view never shows any of those three for a non-historical
  lead. The existing, already-passing
  `test_letter_obligation_purge_nulls_html_and_name_and_placeholders_address`
  test already asserts `template_version` is never named in the purge's SET
  clause — re-run this pass, still passing, now actually meaningful since
  the column is populated.

**What was built:**
- `fulfilment.get_introduction_record_for_lead_reference(lead_reference)`
  (new, self-contained-connection, fails toward `None`) — returns
  `stage_key`/`stage_label`/`stage_explanation` from a new `STAGE_MAP`
  (the 5 stages Nick named, mapped from the real `letter_obligations.status`
  enum), plus `is_dry_run`, `template_version`, `provider_name`,
  `provider_reference`, `provider_accepted_at`, `dispatched_at`,
  `failed_at`, `suppressed_at`, `suppressed_reason`, `purchase_date` (from
  `lead_allocations.created_at`), and `allocation_type`. Deliberately a
  SEPARATE function from the existing, already-tested, already-wired-in-
  three-places `get_letter_status_label_for_lead_reference` — that
  function's own short strings ("Being printed & posted", "Posted", ...)
  are untouched, so its three live call sites (dashboard/my-leads/
  free-dashboard) keep their established wording unchanged.
  `provider_accepted` is explicitly worded "acceptance, not dispatch";
  `dispatched` explicitly states TreeKey has no way to confirm actual
  delivery (there is no `delivered_at` writer anywhere in the codebase).
- `worker.promote_pending_approvals`: one added column in its existing
  freeze-UPDATE (see gap above) — no other behaviour change.
- `main.py`'s `my_leads_view`: the non-historical-lead branch (a NEW
  allocation, never a historical claim) now calls the new function and, when
  it returns a record, renders the stage + explanation, a safe category/
  area line (reusing the marketplace's own `database.classify_job_category`
  and `database.get_outcode_area_label`/`_extract_outcodes` — computed from
  the RAW pre-redaction address/summary already in scope server-side,
  exposing nothing beyond what this exact lead already shows to every
  visitor pre-purchase), the template version (or "not yet finalised" when
  still pending approval — never fabricated), the provider evidence line
  with the explicit no-document-exists caveat, and a link to the existing
  `/letter-settings/preview` route for the address-free letter preview
  (reused unchanged — no new rendering of letter content was written).
  When the new function returns nothing (legacy-pipeline lead, or any
  lookup failure), the page falls back to the existing plain status line,
  unchanged.

**Fields NOT shown, on purpose**: no address, no applicant name, no
`approved_content_html` (the real frozen letter — contains the actual
address baked into its front page, never safe to show a buyer directly, and
cleared by the purge anyway) — the address-free preview is the contractor's
own reusable template rendered against fixed fictional sample data via the
pre-existing `/letter-settings/preview` route, never a per-lead render.

Files touched: `fulfilment.py` (new function + `STAGE_MAP`, ~150 lines,
nothing else changed), `worker.py` (one UPDATE statement, +1 column +1
param), `main.py` (`my_leads_view`'s non-historical branch only).

Tests: `python3 -m py_compile` clean on all three files.
`tests/test_lead_action_links_and_flyer_claims.py` — added
`TestGetIntroductionRecordForLeadReference` (8 tests: empty ref, no row, DB
error, malformed row, dispatched-never-claims-delivery, provider-accepted-
never-worded-as-dispatch, dry-run test-mode note, null template_version not
fabricated) and `TestMyLeadsViewIntroductionRecord` (3 route-level tests:
full evidence block renders and never says "Delivered", acceptance is never
worded as dispatch, dry-run shows the test-mode note) — all passing, plus
every pre-existing test in that file (28 total) still passing, including
the two that exercise `my_leads_view`'s fallback path when no fulfilment-
pipeline record exists (they pass by the SAME graceful degrade the new
function itself provides — confirmed, not assumed, by reading why each
still passes). `tests/test_worker.py` — added
`test_promotion_stamps_template_version_onto_the_obligation` (asserts the
actual UPDATE params, not just that nothing crashed) — all 18 tests in that
file passing. `tests/test_dispatch_purge.py` — all 18 tests still passing
unchanged, including the purge-preserves-template_version assertion.
Fixed two OTHER test files whose hand-rolled fake cursors hard-coded the
old 3-param UPDATE shape and broke under the full suite only:
`tests/test_content_freezing.py` (added the new param to the unpack +
asserted it) and `tests/test_letter_settings_journey.py`'s in-memory fake-
DB harness (added `template_version` handling to its INSERT/UPDATE
simulation). Full regression suite: **580/580 passing** (was 568 before
this pass; net new tests from this pass and none removed). The two
pre-existing, unrelated top-level (non-`tests/`) issues documented in
section 1i above (`test_main.py`'s missing `scanners` module,
`test_notifications.py`'s stub gaps) were re-checked and are unchanged,
confirmed unrelated to this pass (neither file was touched).

No deployment, no charge, no real letter sent, no production data altered.
Live sending remains disabled throughout.

## 1k. 2026-09-24, later still pass — presentation/UX pass: mobile nav gap on homepage, missing nav/footer on 3 standalone pages, radar-widget silent JS bug, two fabricated-credential review tools fixed

Full detail in ERROR_LOG.md's matching dated entry. Summary:

**Genuine current-code bugs found and fixed:** homepage (`/`) had its own
separately-maintained nav copy that never got the mobile hamburger menu
other pages received; the homepage radar widget silently threw on every
scan (6 unguarded `.href` assignments on checkout-button ids that don't
exist on the homepage), surfacing a misleading "Network Error" even
though the scan succeeded, fixed with a new null-safe
`_setHrefIfPresent()` helper; `/faq`, `/privacy-policy`,
`/terms-of-service` had no nav/footer at all; `/boost-review` and
`/generate-storm-quote/{lead_id}` both defaulted to fabricated
placeholder business names/phone numbers plus a hardcoded, unconditional
"BS3998 / NPTC / £5M Insured" badge for every contractor -- both fixed
using the same real-settings-or-redirect-to-`/letter-settings` pattern
already established on `generate_street_flyer` earlier this engagement;
signup/login form `<label>`s had no `for`/`id` association, fixed on
both forms (a broader sitewide gap, ~57 of 69 site labels, was found but
NOT fixed beyond these two anonymous-journey forms, to avoid scope creep
-- flagged as a launch-improvement finding).

**Confirmed correct (verified functionally, not just HTTP 200):**
destination-preserving login links end-to-end (marketplace "sign in for
your discount" -> `/login?next=...` -> correct banner shown), the
unauthenticated `/checkout/...` entry redirect, marketplace
error/empty-search states (unrecognised outcode, zero-match filter
combination), and `/dashboard`/`/account`/`/letter-settings`/
`/letter-settings/preview`/`/letter-onboarding` reviewed in code
(self-contained app-style chrome by design, not a nav-consistency bug;
already-associated labels; working cross-navigation).

**Honest tooling limitation:** genuine mobile-viewport visual testing is
not possible in this sandbox's browser tool (`resize_window` doesn't
actually resize; `file://`/`data:` URLs are blocked) -- every
mobile-specific fix was verified via code inspection, Python syntax
checks, and isolated Node.js execution of the actual extracted JS
against a hand-rolled DOM stub with a negative control, never a real
mobile screenshot.

**Tests:** new `tests/test_boost_review_route.py` (8 tests) and
`tests/test_storm_quote_route.py` (5 tests), both 100% passing. Full
regression run twice this pass, most recently **593/593 passing**
(588 + 5 new), which is also the first full run after the label/`for`
fixes -- no regressions.

**Live-site caveat:** every fix in this section exists only in this
sandbox's working copy. The live site reflects the pre-this-pass
deployment and has not been re-checked against these fixes.

No deployment, no charge, no real letter sent, no production data altered.
Live sending remains disabled throughout.

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

**IMPORTANT — this section was stale until 2026-09-24 (later pass):** it had
never been updated past the 2026-09-22 ZIP despite several later passes
(Requests D, E, F, G, and the letter-template-finalization pass, section 1f
above) each changing files and delivering their own newer ZIP in
conversation. Corrected now with the full, accurate chain.

- **Latest FLAT deployment package (2026-09-24, recommended — no `app/`
  wrapper, files sit at the ZIP's root, ready to copy straight into
  `C:\Users\twobo\Projects\VECTOR DATA LABS`)**: same file contents as the
  wrapped ZIP below, just unwrapped, plus a `DEPLOY_README_FIRST.txt`
  explaining the `scanners.py`/`research.py` gap:
  `/mnt/user-data/outputs/treekey_FLAT_DEPLOY_20260924T174059Z.zip`
- **Latest `app/`-wrapped implementation ZIP (this session's own working-
  directory layout — see section 1a on why this needs the "contents, not
  the folder" step if you use this one instead)** — cumulative, includes
  everything through section 1j ("My Introductions" account view — a
  richer, structured per-introduction record on `my_leads_view`, the
  `template_version`-always-NULL gap found and fixed at the point a
  template is actually frozen, and an honest "no proof-of-postage document
  exists" statement) on top of section 1i (buyer-facing wording audit —
  ~50 stale "buyer gets the address" instances fixed in `main.py`/
  `notifications.py`/`payments.py`/`database.py`, plus a real gate-
  consistency fix so the checkout/marketplace/lead-detail/letter-setup
  pages only promise a posted letter when
  `fulfilment.letter_sending_live()` is actually True), section 1h
  (purchase-to-posting lifecycle review), section 1g (postal-provider
  research, documentation-only), and section 1f (letter template
  finalization) — does NOT overwrite any earlier checkpoint:
  `/mnt/user-data/outputs/treekey_letter_fulfilment_implementation_20260924T173515Z.zip`
  **Extract this ZIP's `app/` folder CONTENTS (not the `app/` folder
  itself) directly into your flat project root, overwriting matching
  files by name** — see section 1a above for exactly why, and how to tell
  whether this was already done for an earlier ZIP.
  (Excludes `app/tests/letter_pagination_check/_out/` — every script's own
  PDF/PNG/HTML/text output in that folder, fully regenerable by running
  the scripts; not shipped to keep the ZIP a reasonable size.)
- **Previous checkpoint ZIP** (2026-09-24, end of section 1i — buyer-facing
  wording audit):
  `/mnt/user-data/outputs/treekey_letter_fulfilment_implementation_20260924T171626Z.zip`
- **Earlier still** (2026-09-24, end of section 1h — purchase-
  to-posting lifecycle review):
  `/mnt/user-data/outputs/treekey_letter_fulfilment_implementation_20260924T165537Z.zip`
- **Earlier still** (2026-09-24, end of section 1g — postal
  provider research):
  `/mnt/user-data/outputs/treekey_letter_fulfilment_implementation_20260924T163550Z.zip`
- **Earlier still** (2026-09-24, end of section 1f — letter template
  finalization):
  `/mnt/user-data/outputs/treekey_letter_fulfilment_implementation_20260924T162315Z.zip`
- **Earlier still** (2026-09-24, end of Request G — retention consistency
  check):
  `/mnt/user-data/outputs/treekey_letter_fulfilment_implementation_20260924T030005Z.zip`
- **Earlier checkpoint ZIPs** (Requests D/E/F and the account-onboarding
  fix, all 2026-09-24, each superseded by the next): timestamped
  `treekey_letter_fulfilment_implementation_20260924T0{13038,14828,23131,24938}Z.zip`
  in the same output folder.
- **Earlier still** (2026-09-23, letter-settings/template-selector pass):
  timestamped `treekey_letter_fulfilment_implementation_20260923T{194644,201259,205451}Z.zip`.
- **Earlier still** (2026-09-22, closed out Item 5): timestamped
  `treekey_letter_fulfilment_implementation_20260922T{151929,160623,162412,164337,231800,234022}Z.zip`.
- **Earliest checkpoint ZIPs** (2026-09-18, end of that phase):
  `treekey_letter_fulfilment_implementation_20260918T215016Z.zip` and
  `treekey_letter_fulfilment_implementation.zip`.
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

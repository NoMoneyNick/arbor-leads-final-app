> **Pointer (30 Sep 2026):** **Current business rules and campaign copy:** see `CURRENT_BUSINESS_MODEL.md` (agreed model; what is reported-but-unverified; what is unresolved) and `OUTREACH_START_HERE.md` (approved outreach copy). Where this document disagrees with them, they win.
> **Status note (30 Sep 2026):** the letter checkpoint below is **installed in the project files**, but **not committed or deployed**, and live behaviour is unverified. This snapshot's original body describes the 26 Sep address-positioning pass.
>
> **Installed (files on disk):** banner/contact-panel design; optional `contact_first_name` (omitted when blank); letter number (sequence-backed `letter_obligations.letter_number`, shown on the letter and in My Introductions); optional contractor offer (`offer_text`/`offer_code`/`offer_conditions`, changes require reapproval for future letters); blank reserved QR/logo areas (features not built). Files: `main.py`, `fulfilment.py`, `letter_content.py`, `worker.py`, `migrations/0001_letter_fulfilment.sql`, and seven test files. Preview: `docs/letter_previews/letter_number_and_offer_preview.pdf` (+ zone overlay PNG).
>
> **Checks run before install:** 318 focused tests across letter content, onboarding, settings, checkout gate, content freezing, fulfilment, worker, access control, providers, promise gate and migration consistency, plus 66 privacy/identifier-gating tests. All passed in the sandbox. No full suite, no provider call.
>
> **Deployment prerequisites (not done):** deploy the code; the new columns and the letter-number sequence are created by the app's schema initialisers on first start (`fulfilment.py`, `letter_content.py`), mirrored in `migrations/0001_letter_fulfilment.sql`. The existing-rows backfill runs once at that point. Nothing was run against production. Then verify live: a letter shows its number, and the same number appears in My Introductions.
>
> **Integrated signup (30 Sep 2026), installed in the project files, NOT committed or deployed; live behaviour unverified.** One first-time signup form at `/free-account` (all "Create account" links point there): account email, responsible contact's full name, business name and telephone are required, plus the Terms tick box; the existing optional letter-personalisation fields sit on the same page, editable later in My Account. Submitting saves nothing to any account: the details wait on the ONE emailed verification record (`contractor_auth_tokens.pending_signup`), expire with it, are cleared on use/expiry, and are applied in the same transaction that consumes the link/code (`database.verify_magic_auth_token`), insert-only and serialised per email. An email with ANY existing account (letter settings, subscription, free/limbo account, lead dispatch, earlier verified login) is never changed; it just gets an ordinary login link and the response is identical, so registration status is not revealed. Log In never checks whether an address exists. After verification, any account with no saved letter details (including an unknown email typed at Log In) is sent to the same form (`/free-account`, email shown as confirmed, saved by `POST /api/signup/complete` under the session email only) with the checkout `next` preserved; `/letter-onboarding` is now just a redirect to it, and an ordinary `/letter-settings` save can no longer create an account's first settings. Letters are created unapproved; save, preview and explicit approval are unchanged. The signup Terms timestamp and responsible-contact name live on `contractor_letter_settings` (`terms_accepted_at`, `responsible_contact_name`); an ordinary settings save never writes them (`letter_content.get_account_identity`, `update_responsible_contact_name` exist for later editing; no My Account screen for it yet).
>
> **Free-lead offer retired:** `POST /api/free-signup`, `POST /api/request-new-code` and `GET /api/cold-email-1-test` now only redirect to `/free-account` (no account, lead reservation, code email, grant or cookie). Existing `limbo_accounts` rows, granted leads and `/free-dashboard` are untouched; `_issue_free_lead_code` and its database helpers remain with no callers. Free-lead wording removed from the homepage button, pricing banner, FAQ, Log In page and site header (header now shows "Create Account" and "Log In"). No £4.99 checkout or advertising was added.
>
> **Checks:** 39 checks against a real disposable PostgreSQL 16 (`python tests/postgres_concurrency/run_signup_real_db_tests.py`: applied once, expired/reused link and code, existing accounts with and without settings, competing links, unappliable details, terms/name untouched by an ordinary save, repeatable startup schema on existing rows); 216 focused unit tests (`tests/test_first_time_signup.py` plus the updated onboarding/settings/checkout-gate/journey tests and the `test_main.py` free-signup class). No full suite, no email sent.
>
> **DEPLOYMENT PREREQUISITES (nothing done yet):** (1) Deploy the code. Three nullable columns are added by the app's own startup schema code, repeatably, preserving all rows: `contractor_auth_tokens.pending_signup`, `contractor_letter_settings.responsible_contact_name`, `contractor_letter_settings.terms_accepted_at` (`database.init_db`, `letter_content.init_letter_content_schema`; operator copy in `migrations/0003_signup_pending.sql`, no manual step). (2) After deploy verify live, with a test address you control: sign up on `/free-account`, receive the email, click the link, confirm you land signed in and the details appear in My Account; repeat with the 6-digit code; try an unknown email at Log In; try an existing customer's email on the form and confirm nothing changes. (3) Do NOT send or load Cold Email 1 or any campaign that promises a free lead: the offer no longer exists and `/free-account` is now a plain signup form.
>
> **Unfinished / open:** wider customer-page presentation work (see the brief; page families other than the letter pages, signup form and shared footer are unverified); Log In still emails a link to any address (existing behaviour; nothing distinguishes new from existing until verified) and a verified login now also sets the session cookie for accounts with no subscription/free account (previously those got no cookie); no My Account screen to edit the responsible-contact name; the confirmation email still says "login link" for signup; the weekly free-lead teaser emails to existing free accounts and `notifications.py` cold-email wording are unaudited; legal identity, Article 14/LIA, provider retention, shortage renewals and refund timing remain unresolved; £4.99 checkout configuration is unverified and is not advertised.
>
> See `CURRENT_BUSINESS_MODEL.md` section B.

# CURRENT_HANDOFF.md — Intelliprint address/barcode positioning (ninth pass)

Written 2026-09-26. See `ERROR_LOG.md`'s "ninth pass" entry (top of the
Entries list) for the full technical account; this file is the short
status snapshot for "what state is this in right now."

## Completed changes (applied directly to this project folder)

- `letter_content.py`:
  - `.address-clear-zone` repositioned from `top:20mm; left:40mm;
    width:120mm;` to `top:46mm; left:19mm; width:55mm;` — measured
    directly off Nick's real Intelliprint output, not documentation.
  - `.brand-header`'s `margin-top:20mm` removed (was only clearing the
    old, wrong address-zone position; no longer needed).
  - New `.greeting-text` class (`margin-top:31mm`) added to the "Dear
    homeowner," paragraph only, so it starts below Intelliprint's real
    address/barcode zone instead of inside it.
- `tests/test_letter_content.py`: `TestAddressClearZoneMatchesIntelliprintTemplate`
  updated for the new coordinates; 3 new tests added.

Wording, letter content, the two-page front/reverse design, and the
file-based (base64 PDF) submission route from the previous pass are all
**unchanged** in this pass.

## Applied file paths (this project, i.e. what `UPDATE_WEBSITE.bat` deploys from)

- `C:\Users\twobo\Projects\VECTOR DATA LABS\letter_content.py`
- `C:\Users\twobo\Projects\VECTOR DATA LABS\tests\test_letter_content.py`
- `C:\Users\twobo\Projects\VECTOR DATA LABS\ERROR_LOG.md`

Backup of the pre-change versions of these files (plus
`letter_providers\intelliprint_provider.py`, unchanged this pass but
included for completeness) is at:
`C:\Users\twobo\Desktop\TreeKey_Backup_2026-09-26_addresspositioning\`

`letter_providers\intelliprint_provider.py` itself was **not** touched
this pass — the fix is entirely in `letter_content.py`'s CSS/markup, and
`send()`'s file-upload submission logic (two pages, one sheet, duplex,
forced testmode) is untouched from the previous pass.

## Tests actually run

- `tests/letter_pagination_check/run_pagination_check.py` (21 renders:
  7 edge cases x 3 templates) — same result as before this change: every
  `validate()`-accepting case is 2 pages across all 3 templates;
  `max_length_everything` (a case `validate()` itself rejects, so it can
  never reach a real send) is still 3 pages, unchanged, already disclosed
  as a known residual in earlier passes — not a new regression.
- `python3 -m unittest discover -s tests -p "test_*.py"` — 646/646 passing.
- Local, non-mocked render of the exact letter `scripts/intelliprint_test_send.py`
  would submit (same sample data, same `_render_html_to_pdf_bytes` path) —
  produced a real 2-page PDF. Saved as a preview and sent in chat.
- Playwright `getBoundingClientRect` measurements and a visual overlay of
  the measured real zone/barcode rectangles onto a fresh render, confirming
  our address sits inside the zone clear of the barcode, and the header/
  reference block/greeting all sit outside it.

None of the above is a real Intelliprint submission — the sandbox this
runs in cannot reach `api.intelliprint.net`. See "Unresolved" below.

## Unresolved / not attempted this pass

- **Not empirically confirmed against a real Intelliprint submission.**
  Everything above is local rendering and pixel measurement against the
  *previous* real submission's PDF. Per Nick's own instruction on this
  task ("Validate against the provider-generated PDF before calling the
  positioning fixed"), this is not called "fixed" until a fresh real
  test-mode submission is inspected the same way this one was.
- **Whether Intelliprint's orange guide outline itself prints, or is a
  preview-only diagnostic guide, is unanswered.** Nick asked this
  directly. The Intelliprint docs pages most likely to answer it
  (`design-specs`, `choose-a-content-strategy`) are blocked by their own
  `robots.txt` from this session's fetch tool, and no workaround around
  that block was attempted (against this project's rules). This does not
  block the fix itself — the zone and the barcode inside it are measured
  from Intelliprint's own real rendered output, not inferred from the
  outline's print status, and the eighth pass already established
  Intelliprint reads the address from this same area for file-route
  submissions.
- Whether real (non-test) sends will need `recipients[]` restored for
  postage-cost calculation, despite the file-route docs saying it isn't
  required — flagged in the eighth pass, still open, irrelevant to
  testmode.

## Exact next step

Run `RUN_INTELLIPRINT_TEST.bat` once (test mode is hard-coded on in that
script regardless of `.env`). Send back the console output/screenshot and,
if Intelliprint's dashboard lets you download the rendered PDF for that
job, that PDF specifically — the address-position claim above can only be
confirmed against Intelliprint's own real output, not this project's local
preview.

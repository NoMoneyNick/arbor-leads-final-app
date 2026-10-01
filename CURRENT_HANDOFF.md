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
> **Free-lead offer retired:** `POST /api/free-signup`, `POST /api/request-new-code` and `GET /api/cold-email-1-test` now only redirect to `/free-account` (no account, lead reservation, code email, grant or cookie). Existing `limbo_accounts` rows, granted leads and `/free-dashboard` are untouched; `_issue_free_lead_code` and its database helpers remain with no callers. Free-lead wording removed from the homepage button, pricing banner, FAQ, Log In page and site header (header now shows "Create Account" and "Log In"). No £4.99 checkout or advertising was added at that stage (superseded by the New-customer journey note below).
>
> **Checks:** 39 checks against a real disposable PostgreSQL 16 (`python tests/postgres_concurrency/run_signup_real_db_tests.py`: applied once, expired/reused link and code, existing accounts with and without settings, competing links, unappliable details, terms/name untouched by an ordinary save, repeatable startup schema on existing rows); 216 focused unit tests (`tests/test_first_time_signup.py` plus the updated onboarding/settings/checkout-gate/journey tests and the `test_main.py` free-signup class). No full suite, no email sent.
>
> **DEPLOYMENT PREREQUISITES (nothing done yet):** (1) Deploy the code. Three nullable columns are added by the app's own startup schema code, repeatably, preserving all rows: `contractor_auth_tokens.pending_signup`, `contractor_letter_settings.responsible_contact_name`, `contractor_letter_settings.terms_accepted_at` (`database.init_db`, `letter_content.init_letter_content_schema`; operator copy in `migrations/0003_signup_pending.sql`, no manual step). (2) After deploy verify live, with a test address you control: sign up on `/free-account`, receive the email, click the link, confirm you land signed in and the details appear in My Account; repeat with the 6-digit code; try an unknown email at Log In; try an existing customer's email on the form and confirm nothing changes. (3) Do NOT send or load Cold Email 1 or any campaign that promises a free lead: the offer no longer exists and `/free-account` is now a plain signup form.
>
> **Signup form corrections (30 Sep 2026, installed, not deployed):** (1) Required fields are now validated from their actual values at submit (`noValidate` + a submit handler; no disabled button, no key-event dependence), so browser-autofilled values count immediately; server-side validation and the Terms requirement are unchanged, and without JavaScript the native `required` attributes still apply. Checked in real Chromium with values and the Terms box set without any events; true Chrome autofill could not be triggered in headless Chromium, and Safari/iOS autofill is untested. (2) The signup form no longer has any letter-style/template choice; new accounts get the default style, unapproved, and an existing template choice or approval is never touched (insert-only). Style choice, preview and explicit approval remain in My Account and before purchase (`/letter-settings` -> preview -> approve; checkout gate unchanged). Business introduction, services, area, insurance, qualifications and contact fields stay on signup as optional information. (3) Each offer field has visible helper text, plus one shared note; the offer-change reapproval rule is unchanged and stated in My Account. Checks: 41 real-Postgres checks, 100 focused unit tests, 14 real-browser checks (`/tmp` script, not installed).
>
> **New-customer journey (30 Sep 2026), installed in the project files, NOT committed or deployed; live behaviour unverified.** *Cause of the `/pricing?msg=no_subscription` landing:* signup and details-saving DID complete; `_login_session_response` then sent any verified account without a subscription or free-lead row to the pricing "no subscription" banner. *Now:* such a valid account lands on the new `/welcome` page (signed-in, saved details required, no subscription needed; a `next` checkout continuation still wins; subscribers still go to `/dashboard`; an account with no saved details still goes to the signup form). The pricing banner no longer suggests creating another account. *First-introduction offer (£4.99), real:* `first_offer=1` on `/checkout/{plan}?lead_id=...` only REQUESTS it; `payments.create_checkout_session` decides everything server-side (price constant `FIRST_INTRO_PRICE_PENCE`; standard opportunities only = plans `single_lead_small`/`single_lead_medium`; not a subscriber; no earlier paid purchase; not already redeemed or held). The redemption record is the existing `payments` order row, written atomically (advisory locks per account, telephone and business name) by `database.claim_first_offer_order`: `pending` holds the offer ~40 minutes, `paid` consumes it for good, `failed`/`refunded` release it, `refund_failed` keeps it blocked. Three nullable columns on `payments` (`offer_kind`, `offer_phone_key`, `offer_business_key`) are added by `database.init_db` at startup (copy in `migrations/0004_first_introduction_offer.sql`, no manual step). The offer price appears on marketplace cards/detail only to an account the server has just confirmed eligible; the letter approval gate at checkout is unchanged (approval before payment); Stripe promo codes are switched off on an offer session; later purchases keep normal prices. No suitable stock: `/welcome` says so and offers no buy button; a stale lead creates no charge. *Validation:* `letter_content.validate_phone` (structural: 7-15 digits, optional leading +, spaces/brackets/dots/hyphens; UK or international; no phone library was added), enforced in `ContractorLetterSettings.validate()` (so signup and My Account settings both check it on the server) with an inline error and preserved values on the signup form; the Service-area field gets a non-blocking capitalisation suggestion ("Use this"/"Keep mine"; only all-lower-case words change; business name and letter wording are never touched). *Copy:* pricing page heading text and the four single-lead plan descriptions no longer say "photo-verified", "100% exclusive" or "bidding wars"; the pricing page states the postal-introduction description and the sold-once, TreeKey-only promise; My Account no longer says "One free lead per account". Autofill dark styling still applies on the touched forms (checked with forced `:autofill`; real browser autofill still unverified).
>
> **Checks (new-customer journey):** `tests/test_new_customer_journey.py` (36 tests) plus the related focused tests (293 in total, including those 36) across the signup, onboarding, settings, checkout-gate, marketplace-privacy, payments-webhook and migration-consistency files, all passing; 27 checks on a real disposable PostgreSQL 16 (`python tests/postgres_concurrency/run_first_offer_real_db_tests.py`: eligibility, duplicate/concurrent redemption by account and by telephone, business-name match, pending expiry, paid/failed/refunded/refund_failed, subscriber and prior-purchase exclusion, repeatable startup ALTERs); real Chromium checks of the signup phone/suggestion behaviour and the welcome page. No real Stripe call, charge, email or letter. The `test_main.py` magic-link test that failed was checked against the pre-change baseline and failed there too (see the corrections note below).
>
> **Still blocked / not verified (new-customer journey):** the £4.99 price and the abuse limits are untested against real Stripe (test-mode checkout not run); the abuse limit is one redemption per account, telephone number and normalised business name, which a determined person with a second phone number and business name can still get around (no company-number or address check exists); the £4.99 loses money against the £19/£29 price by design (business decision, not a code matter); a customer who abandons the offer checkout cannot start another offer checkout for about 40 minutes; the literal phrase "5-contractor bidding wars" / "5-way bidding wars" remains on the homeowner quote and city pages (three lines in `main.py`, not the homepage; not changed); the welcome stock count is capped by the marketplace page size (40).
>
> **Corrections to the new-customer journey (30 Sep 2026), installed, NOT deployed.** (1) *Standard rule:* the £4.99 offer now requires the listing's existing value classification to be `standard` (`database.calculate_lead_freshness` now also returns `value_tier`, from the same `scanners.classify_lead_value_tier` the price grid uses) AND a standard price point; price alone no longer decides. Checked with the real classifier: an aged Priority listing prices at £29 (`single_lead_medium`) but is `priority` and is now excluded. *Limitation:* the classification is keyword matching on the listing summary and falls back to `standard` when no Priority/Elite keyword matches, so an unrecognised high-value listing counts as Standard; no other rule was invented. (2) *Magic-link test:* `TestMagicLinkGoesToTheRealContractorNotTestEmail.test_login_link_is_sent_to_the_contact_address_supplied` failed on the PRE-change baseline too (baseline copy: the pre-change files plus the untouched rest; same error), so it was not introduced by this task. Cause: the test's fake request had no `cookies`, which the shared nav (already present before this task) reads when rendering the "Check Your Inbox" page. Real requests have cookies; the test fixture now sets `cookies = {}` and the test passes. The signup/login path itself is covered by the passing `test_first_time_signup.py` and `test_magic_link_credential_exposure.py` tests (link email sent, token/code never in the page). (3) *Homepage copy:* "100% Exclusive Leads", "100% Exclusive — every lead sold once, never resold" and the "same job to 5 different contractors" paragraph are replaced with "Sold to One Contractor — Each introduction is sold to one contractor only." (4) *Stripe test-mode checkout: BLOCKED, not run.* This session has no Stripe test key or webhook secret and no route to Stripe (the `stripe` package cannot be installed here), and the hosted Stripe page needs a test card typed into it, which I will not do; no secrets were read. What WAS run instead (`python tests/postgres_concurrency/run_first_offer_webhook_db_test.py`, 18 checks on a real disposable PostgreSQL 16, Stripe simulated locally): the real checkout code creates a session for exactly 499 pence GBP with promotion codes off; a Priority listing is refused; the real webhook handler then marks the order `paid`/`fulfilled` at 499, the lead `claimed`, creates one allocation and one letter obligation (status `pending_approval` because the letter is not approved; nothing dispatched, sending disabled, no provider called); the offer reads consumed; a replayed event creates no duplicate; a second offer attempt is refused without reserving a lead; a later ordinary purchase is priced normally. This proves our code's behaviour on a well-formed event, NOT that Stripe accepts the session or delivers/signs the event. *To finish it yourself:* with Stripe in TEST mode and the test webhook secret set on a local or staging copy, create an eligible account, open a Standard listing's £4.99 link, pay with Stripe's published test card, and confirm the `payments` row (`paid`, `offer_kind = first_introduction`, 499) and one `letter_obligations` row; `LETTER_SENDING_LIVE` must stay unset. Disclosed limitations kept: no company-number or address check for "one per business"; checkout reservations (including the ~40-minute offer hold after an abandoned checkout) are unchanged.
>
> **Done 1 Oct 2026 (installed locally, NOT deployed):** shared header/footer now carry one self-contained stylesheet (`_SHARED_NAV_CSS`/`_SHARED_FOOTER_CSS` in `main.py`), used by all 21 functions that call the shared nav/footer and now also by the homepage (its own footer was replaced by the shared one; its nav uses the same CSS); My Account and My Leads use the site font; phone header no longer overflows (secondary account links move into the menu); USP blocks added under the homepage hero and above the pricing cards; obsolete pricing creed/comparison table and the "one job covers it" ROI boxes removed (pricing and homepage). Browser-checked at 1280px and 390px on 15 pages (30 renders, no horizontal scroll, no default-colour links in the chrome, with and without tailwind.css); real-browser check of the live site still needed after Nick deploys. **Done 1 Oct 2026 (second pass, installed locally, NOT deployed):** homepage and /pricing package cards now share one layout (`_package_card_html`, `_PACKAGE_FACTS` in `main.py`): plan, who it suits, monthly price, included introductions, printing and postage, discount on ADDITIONAL marketplace introductions, plan-specific button; "Most Popular" removed; homepage labelled "Featured packages" with a "Compare all five packages" link; the conditional £4.99 invitation (`_first_offer_promo_html`) shows on both pages (visitors see it; signed-in accounts see it only if the server-side eligibility check passes, subscribers and ineligible accounts see nothing). Presentation only: payment logic and eligibility rules unchanged; homepage plan buttons still go to `#map` as before (flagged for Nick); the real Stripe test is still outstanding. **Still open:** (b) account closure / personal-data deletion request. NOT built: no reliable request mechanism exists (see the brief), so no link was added; decision needed before building. Remaining unverified page families: dashboards and other pages with their own inline styling. Earlier items: wider customer-page presentation work (see the brief; page families other than the letter pages, signup form and shared footer are unverified); Log In still emails a link to any address (existing behaviour; nothing distinguishes new from existing until verified) and a verified login now also sets the session cookie for accounts with no subscription/free account (previously those got no cookie); no My Account screen to edit the responsible-contact name; the confirmation email still says "login link" for signup; the weekly free-lead teaser emails to existing free accounts and `notifications.py` cold-email wording are unaudited; legal identity, Article 14/LIA, provider retention, shortage renewals and refund timing remain unresolved; £4.99 checkout configuration is unverified and is not advertised.
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

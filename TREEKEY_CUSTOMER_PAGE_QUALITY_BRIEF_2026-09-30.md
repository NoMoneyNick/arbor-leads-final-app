# Customer-page quality — priority requirement from Nick

30 September 2026. Recorded request and route inventory, not a completed visual audit or implementation.

## Observed screenshot

Letter-onboarding choice page: the centre card is styled but the header/navigation/footer appear unstyled, with browser-default blue/purple underlined links and an oversized low-resolution T/k footer mark. Missing/incomplete shared styling is a hypothesis to trace, not a confirmed CSS cause.

Nick explicitly requires the existing low-resolution T/k mark to be kept small wherever used, never enlarged. Preserve aspect ratio; choose one compact consistent display size after visual checking. Do not create a new logo or enlarge this asset to fill a header.

He rejects the current font/colour presentation. Use consistent readable typography, accessible contrast, intentional link/visited/focus states, spacing, buttons and form styling, with restrained TreeKey branding. All reachable customer pages and their error/empty/success states should look finished on mobile and desktop.

**Superseded by the correction below (30 Sep 2026):** the two-button onboarding choice is being removed, not restyled.

## Route inventory from main.py (local source, not proof of live behaviour)

- Discovery: /, /marketplace, /marketplace/lead/{lead_id}, /pricing, /storm-radar, /tree-surgeon/{location_slug}, /partner-offer.
- Account entry: /login, /free-account, /verify-login, /logout; signup/OTP/magic-link outcomes including invalid or expired links.
- Letter journey: /letter-onboarding (now the single integrated setup form), /letter-settings (the same form, as My Account editing), /letter-settings/preview, /letter-settings/approve outcomes, and the purchase-time "add a personal introduction or continue" screen.
- Account: /account, /dashboard, /free-dashboard, /my-leads, /settings, /ledger; My Introductions within the applicable account view.
- Purchase: /checkout/{plan_key}, /payment/success; return/cancel/failure, out-of-stock, setup-required and already-owned states where implemented. Check hosted payment handoff branding only where configurable; do not charge anything.
- Tools: /generate-letter/{lead_id:path}, /generate-street-flyer/{lead_id:path}, /generate-storm-quote/{lead_id}, /boost-review, /quote-estimator, /street-view/{reference:path}.
- Other public paths: /chip-drop, /register-drop-spot, /suggestions, /faq, /privacy-policy, /terms-of-service, /unsubscribe, /unsubscribe-teaser, /status if publicly linked.
- Shared states: loading, empty/no matches, invalid input, missing record/404, denied access, expired session, unavailable service, saved/approved confirmation and narrow-screen navigation.

Confirm remaining customer-linked routes from navigation/footer/forms and email links; this decorator inventory is not a guarantee of complete reachability. Do not execute admin, scraper, export or trigger routes just because they exist.

## Efficient implementation approach for Claude

1. Read current shared page-shell, nav/footer and onboarding code; compare a working page. Fix the demonstrated shared styling defect first. Reuse the established design rather than rebuilding each page independently.
2. Map route families to existing shared components. Apply consistent customer-facing styling, including the small logo constraint, without changing purchase/auth/approval/fulfilment behaviour.
3. Visually render each distinct layout family on desktop and mobile, plus routes with independent markup and relevant states. Shared siblings can use targeted structural checks; do not claim visually checked if not rendered. A 200 status alone is not visual verification.
4. Use local fixtures for authenticated/checkout views. No real customer signup, purchase, charge, email, provider submission or production mutation.
5. Preserve approved printed-letter design, address/barcode clearance, PDF renderer, duplex configuration and blank future QR/logo areas. Website page chrome must not be injected into the printable letter itself.
6. Follow CURRENT_BUSINESS_MODEL.md. Screenshot footer contains blanket GDPR compliance/OGL claims and an older operator identity: use only established accurate footer information, keep privacy/terms/contact links and do not invent legal identity or publish unresolved legal drafts.
7. Run focused checks justified by changed shared components; no blanket suite or repeated broad research. Keep a compact page-family matrix: fixed/checked/unverified and the remaining concrete issue.
8. Back up changed destination files outside the repository, preserve newer edits and install the completed changes with read-back verification. No commit, deployment, database migration, live-setting change or campaign operation.

## Completion criteria

- No raw/default-looking navigation or footer on customer pages.
- T/k footer mark stays small, proportionate and consistent.
- Fonts, colours, spacing, forms and link states are deliberate and readable.
- Mobile menus and layout work without clipping or horizontal overflow.
- User journeys provide clear actions and finished error/empty/success screens.
- Existing functionality and print layout remain intact.
- Report actual coverage and any incomplete routes; do not claim universal quality from one screenshot.

## Correction: one integrated signup form (30 Sep 2026)

Replace the two-button letter-onboarding choice with one integrated form. Do not redesign the two-button screen.

- **Required section:** essential account, business and contact fields (account email shown, business name, phone), reusing the existing fields, validation and save logic.
- **Optional section, same page, clearly labelled:** the existing letter-personalisation fields. Beside each, a concise purpose line, and a note that they can be edited later in My Account.
- **Blank optional wording fields** use TreeKey's complete standard template. Never print unresolved placeholders. Missing optional names/offers are simply omitted.
- **Personalisation is not approval.** The existing save, preview and explicit-approval pipeline is unchanged and still required before a letter is used.
- **Return destinations preserved:** signup, login and checkout `next` targets are carried through unchanged. Returning users do not repeat signup.
- **Not in scope:** the future business-website/QR and logo-upload features. Their reserved letter areas stay blank.
- No second settings system.

## Status of this correction and the presentation task

Implemented in the project files and included in the existing customer-page presentation task. Not committed or deployed; live behaviour is unverified.

| Page family | Status |
|---|---|
| Integrated first-time signup form (/free-account) and My Account letter settings (/letter-settings) | Signup form built and installed (see below); it uses the shared letter-page shell, which was rendered on desktop and mobile in an earlier step. The final signup form and its verified-session variant were checked structurally by tests, not separately re-rendered. |
| Letter preview page, purchase-time continue/personalise screen | Fixed (moved onto the same shared shell). Structural checks only; not separately rendered. |
| Shared footer | Fixed: T/k mark pinned small (28px high, 64px max width); blanket GDPR/OGL/SSL/operator claims removed; privacy, terms and contact links kept. Operator identity left unstated until confirmed. |
| Site header | Changed: "Sign Up / Log In" is now "Create Account" plus "Log In". Not rendered or checked at mobile width. |
| Root cause | Letter pages used the shared nav/footer without linking /static/tailwind.css. Every other page that uses the shared nav already links it. |
| /login | Title, heading and helper text changed; link to the signup form added (keeps checkout `next`). Not rendered. |
| Homepage, marketplace, pricing, checkout, account and dashboard pages, FAQ/privacy/terms, error pages | Unverified. Not rendered in this pass. Only the free-lead wording was removed from the homepage button, pricing banner and FAQ answer. |
| Live Terms page text | Still names the older operator identity. Left alone pending confirmation of legal identity. |

## Signup completed (30 Sep 2026, second stage)

The integrated form is now the actual first-time signup at `/free-account`; the earlier version only appeared after verification and left `/free-account` as the old free-lead form. Details are held on the emailed verification record and applied once on verification; existing accounts are never changed; unknown emails typed at Log In reach the same form after verification, with checkout `next` preserved; the free-lead routes are retired. Full account, deployment prerequisites and open items: `CURRENT_HANDOFF.md`.

**The wider customer-page presentation work is still outstanding.** Only the letter pages, the signup form and the shared footer/header have been worked on. Every other page family listed above as unverified still needs checking.

## Outstanding items added 30 Sep 2026 (NOT STARTED: recorded only)

- [x] **Shared footer/header presentation (done locally 1 Oct 2026, not deployed).** Root cause: My Account and My Leads never linked `/static/tailwind.css`, and the shared header/footer relied on its (pre-built, purged) classes. Fix: one self-contained stylesheet in the shared functions (site font stacks, readable colours, link/visited/hover/focus states, responsive layout, T/k mark 28px high / 64px max), also applied to the homepage. Coverage: 21 functions use the shared chrome (all covered); the homepage now shares the footer and nav CSS; rendered at 1280px and 390px for My Account (new, returning, legacy-free, subscribed), My Leads, Pricing, Login, Privacy, Terms, FAQ, Homepage, Marketplace, Storm Radar and a letter-shell page, each with and without tailwind.css: no horizontal scroll, no browser-default link colours in header/footer, focus outline present. NOT checked: dashboards and other pages with their own inline styling, the live deployed site, real mobile devices; the printable-letter body font is deliberately unchanged.
- [~] **Account closure / personal-data deletion request: BUILT 1 Oct 2026, installed locally, NOT deployed, live behaviour unverified.** My Account now ends with a discreet "Close account / request data deletion" link to `/account/close` (sign-in required, CSRF token bound to the account, close and/or delete, required confirmation, optional note). Submitting saves one row in the new `account_closure_requests` table (one OPEN request per account; a repeat is a no-op) and only then emails nick@treekey.uk; if the email fails the request stays saved and the operator sees it at `/admin/account-requests` (admin login, or open with `?secret=` to mark requests handled). Nothing is deleted or cancelled by it; a failed save says nothing was submitted. Later 1 Oct: a further option on an open request is merged (not swallowed); "Mark handled" works via admin Basic Auth with CSRF; Privacy Policy section 8 now mentions the form and nick@treekey.uk; the page states the ICO response period (without undue delay, at most one month from receipt, extendable by two months if complex, told within the first month; this is the period for RESPONDING, not a date for finishing deletion). A request whose notification email failed shows NOT SENT in `/admin/account-requests`. STILL OPEN: the handling itself (deleting, cancelling, retained records, outstanding purchases) is manual and undefined; the notification email has never been sent for real (Resend untested for this message); ICO guidance is flagged as under review (Data (Use and Access) Act 2025); other pages still show contact@treekey.co.uk. Earlier analysis follows. (Was: BLOCKED on a decision.) Checked for a reusable mechanism: none is reliable. (1) Privacy Policy section 8 only says to email contact@treekey.co.uk (manual, no record). (2) The Suggestions board (`/api/submit-suggestion` to `contractor_suggestions`) stores a row but has no request type, no link to the logged-in account, no alert or admin view, and shows "Received!" even if the save fails. (3) `notifications.send_system_incident_alert`/`system_warnings` are incident channels (throttled/deduplicated, recurring-digest only) so a one-off request could be dropped. A genuine solution needs a small new request record plus an operator notification: not built. Original note follows. Add a clearly labelled option in My Account. First check and reuse any existing request mechanism (starting points: the Privacy Policy's rights section and the erasure/objection handling near the suppression code in `main.py`; nothing in My Account exists today). It must distinguish three different things: (a) account closure, (b) subscription cancellation (shortage renewals/cancellation terms are an unsettled business question, so do not decide them), and (c) personal-data deletion (UK GDPR). It must account for outstanding purchases and letters not yet sent, and for records that must be kept (payment/accounting records, suppression and objection records). It must NOT run an immediate blanket database deletion and must NOT claim that a button alone completes the process: it should record a request and say plainly what happens next and by when (a period to be confirmed by Nick).

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

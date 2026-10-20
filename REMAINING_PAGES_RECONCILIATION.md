# Remaining pages - what was actually done

Method: the 44 pages in the preview's `source-audit.json` were matched to real routes in `main.py`. "Rendered" = I loaded the real route (through a stand-in test setup with fake data, because the real database/payment libraries can't run in my sandbox) at desktop and phone width and checked layout, overflow, links and that forms/inputs/scripts are unchanged. **Nothing has been run against your real database or in your own browser yet.**

Status key: **A** themed + rendered and checked | **B** themed, structure checked, not looked at visually | **C** not changed

| Preview page | Real route | Status | Notes |
|---|---|---|---|
| pricing | /pricing | A | Wording unchanged. Contact email in the "Lead-Quality Promise" is `nick@treekey.uk`, elsewhere `treekey.co.uk` - **please confirm which is right** |
| faq | /faq | A | |
| privacy-policy | /privacy-policy | A | |
| terms-of-service | /terms-of-service | A | |
| suggestions | /suggestions | A | |
| suggestion-sent | POST /api/submit-suggestion | A | |
| partner-offer | /partner-offer | A | |
| partner-sent | POST /api/submit-partner-offer | A | |
| login | /login | A | Green arrow icon replaced by crest |
| check-inbox | POST /api/request-magic-link | B | Needs a real token to render; wrapper applies, not seen |
| free-account | /free-account (+ /api/signup, /api/signup/complete) | A | |
| welcome | /welcome | B | |
| account | /account | A | |
| account-close | /account/close | B | |
| settings | /settings | B | |
| dashboard | /dashboard | A | |
| free-dashboard | /free-dashboard | A | **Correction 1**: Quick links are separate padded tiles, wrap, label+arrow kept together; checked at 1280/390/320 with and without a free letter |
| my-leads | /my-leads | A | |
| lead-detail | /marketplace/lead/{id} | A | Fake-lead data only |
| checkout | /checkout/{plan} | B | Offer/nudge variant rendered; the plain checkout form was not |
| offer-unavailable | (checkout helper page) | B | Not rendered |
| letter-settings | /letter-settings | A | |
| letter-preview | /letter-settings/preview | A | **Correction 2**: real letter renderer, fills panel width, scales to fit, min readable size 0.7 with sideways/down scroll on phones, placeholder text gone |
| payment-success | /payment/success | B | |
| chip-drop | /chip-drop | A | |
| register-drop-spot | /register-drop-spot | A | |
| storm-radar | /storm-radar | A | |
| ledger | /ledger | A | |
| boost-review | /boost-review | B | |
| quote-estimator | /quote-estimator | B | |
| tree-surgeon-location | /tree-surgeon/{slug} | B | |
| not-found | any 404 (non-API, non-admin) | A | |
| server-error | 500 handler | B | Wrapped in code; the response was not captured in tests |
| message | `_branded_message_page` | A | |
| unsubscribe | /unsubscribe | A | Was a bare line of text; now a full page. Wording kept. One-click POST untouched |
| unsubscribe-teaser | /unsubscribe-teaser | A | Same |
| admin | /admin | B | Internal; rougher match to preview |
| status | /status | B | Internal |
| admin-volume / -value / -classification / -vertical / -dispatches / -account-requests | /admin/... | B | Internal; restyled but lower fidelity |

## Deliberately not changed
- Homepage `/` and `/marketplace`: byte-identical to before (checked). Note the real ones still use the old look; I did not touch them as instructed.
- Printed documents (`/generate-letter`, `/generate-street-flyer`, `/generate-storm-quote`): the letter template still shows the OLD "TREEKEY / Arbor Intelligence" logo (separate work).
- Emails, favicon, `icon-192.png`, manifest, social image, `sw.js`.
- Other admin/ops routes without an approved design.

## Things to know / decide
- Footer keeps the real wording ("Tree Key", "Proudly engineered in the United Kingdom", etc.), not the preview's invented tagline.
- Nav "Tools" goes to /chip-drop (as in the preview).
- Mobile nav is now a wrapping row (no hamburger/JS).
- I could not check for uncommitted git work (no shell on your PC). I backed up your original `main.py` under a new name instead.
- Real-data states (paid vs free, live leads, errors) are unverified until you run it.

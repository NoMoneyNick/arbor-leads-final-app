# TreeKey business rules — decisions and follow-up
Date: 30 September 2026
Companion to TREEKEY_LAUNCH_LOGIC_AUDIT_2026-09-27.md. No implementation instruction or claim that these changes exist.

## User decisions
- Rules 1, 2, 5, 6, 8, 10 and 12 accepted, subject to questions and amendments below.
- Rule 3: require genuine company/business name, buyer/contact name and contact details. Offer personalisation at signup, in My Account, and a final opportunity before letter approval. Use finished standard copy if optional personalisation is skipped; never print example placeholders as actual facts.
- Rules 7 and 9: prefer replacement introductions, carry-forward or customer-approved wider-area alternatives over routine cash refunds. Final timing, cancellation treatment and exceptions remain to be settled.
- Rule 11: user does not accept unlimited free posted introductions. Requests a paid, discounted first introduction that covers postage and contributes to costs. Price not yet approved.
- Rule 4 requires clarification; not yet approved.

## Add to the existing to-do list: narrowly verify the three personalisation opportunities
Current deployment is NOT verified. Earlier Claude reports describe signup onboarding, account letter settings, and a purchase gate for incomplete settings. They explicitly said already-approved standard-letter users did not get a purchase-time nudge; later changes have not been verified. Signup with checkout continuation was also reported to skip onboarding. The user expects all three opportunities.
- Check actual signup onboarding, persistent My Account edit access, and a non-blocking final personalise-or-use-standard choice for first approval/purchase, including checkout-first signup.
- Do not require personalisation of every letter. Reuse approval unless material content changes.
- Check essential saved business identity/contact fields; reuse existing fields before proposing any schema change.
- Check blank optional fields produce approved complete default sentences or omit optional sections. Never print [Business name], sample phone numbers or example credentials.
- Only fix a demonstrated gap; do not rebuild the onboarding flow or run a broad audit.
This addendum records the to-do request; Claude's separate task list was not edited.

## Assistant recommendations — awaiting approval
1. Homeowner-to-purchase matching: print a short opaque TreeKey introduction reference on the letter and show the same reference with the contractor's purchased opportunity. Suggested letter text: "Please quote TK-7K4M9P when contacting us." Reuse existing buyer-facing identifiers if suitable; any shortened display value must remain unique. No council reference, address-derived code or public lookup exposing identities. If caller has no reference, contractor may ask which letter/approximate area and let the caller voluntarily share details. Reference is for matching, not authentication. Do not add call tracking or a new portal.
2. Letter approval meaning: once a standard/personalised letter is approved, reuse it. Material edits need reapproval for future letters; already-approved queued letters retain their frozen version. Explain this with a contact-number example.
3. Supply shortages: carry owed introductions forward without charging for them again; customer can opt into a wider radius. Fulfil oldest owed introductions first. Define a review deadline rather than an indefinite promise; suggested 30 days. Decide how to handle continuing renewal while arrears exist and owed introductions after cancellation. Replacements-first does not establish a universal no-refund entitlement.
4. Paid first-introduction offer: proposed options £4.99 introductory price (recommended), £2.99 lower-margin acquisition offer, or £9.99 more revenue but larger first-purchase hurdle. One per genuine business, eligible standard opportunity, no automatic subscription, approved letter included, offered only when a suitable opportunity is available. Free browsing/preview may remain. Current total mailing/payment costs and abuse prevention are not independently verified. Earlier test output reported 108 pence; this is not a guaranteed future cost.

## Usage and implementation boundary
No code, production settings, payments, letters or deployment changed. Agree business decisions before prompting Claude. Small focused tasks only, reuse existing functionality, no unrequested fields/migrations/features. The existing future QR and logo/advert tasks remain deferred.
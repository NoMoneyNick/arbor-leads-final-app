# TreeKey — launch logic audit

Date: 27 September 2026

Purpose: make the offer, customer journey, letters and operating rules agree before launch. This is a reference checklist, NOT a request to implement everything at once.

Scope: current public homepage, pricing, marketplace, FAQ, signup, terms and privacy pages compared with supplied letters and conversation reports. No code inspected or changed, accounts created, purchases made or letters submitted. This is not verification of private account behaviour, production fulfilment or legal compliance. Later deployments may change these findings.

## A. Confirmed public inconsistencies — prioritise before launch

- [ ] **One consistent product definition.** Marketplace sells a posted introduction without homeowner contact details. Terms describe providing lead information only and tell contractors to verify availability directly with applicants. Align every page and message around the agreed postal-introduction service.
- [ ] **Identifying information in public descriptions.** Observed descriptions contain names, address fragments and searchable references. Hiding the dedicated address field is insufficient. Provide useful descriptions without identifying passages.
- [ ] **Automatic allocation versus selection.** Homepage steps describe choosing opportunities. FAQ describes automatic subscription matching and dispatch with included letters. Distinguish subscriptions from individual marketplace purchases and explain what is authorised once versus chosen individually.
- [ ] **Insufficient monthly supply.** Packages include 6–20 opportunities monthly. FAQ acknowledges quiet areas but does not explain unfulfilled allowances. Decide carry-forward, credit/refund or another explicit arrangement. Do not silently fill quotas with unsuitable or out-of-area work.
- [ ] **Failure to fulfil versus no response.** Refund wording mainly covers non-tree work. Define remedies for invalid addresses, suppression, printing failure and letters that cannot be sent. Define when allowances are consumed/restored. No homeowner reply is different from TreeKey failing to provide the introduction.
- [ ] **Exclusivity scope.** Homepage says a lead is never shown to anyone else, while the marketplace publicly displays opportunities. Public records remain available elsewhere. Promise only the agreed single-purchasing-contractor rule through TreeKey, not absence of competitors.
- [ ] **Urgency versus postal delivery.** Emergency-labelled opportunities and instant/first-to-quote claims conflict with printing, posting and waiting for a homeowner response. Distinguish notification from delivery. Decide which urgent work is unsuitable. Notice age does not prove current availability.
- [ ] **Package, recipient and letter fit.** Domestic surgery, forestry, institutional tenders and planning consultancy are offered, but current letters address homeowners about tree work. Establish appropriate combinations or narrow initial scope.
- [ ] **Free-offer definition.** Homepage promises a free local lead today. Explain whether it includes a posted introduction, who pays postage, approval requirements and what happens when no suitable opportunity exists.
- [ ] **Identity, contacts and privacy consistency.** Supplied review draft names Nicholas Michael Secular and the PO Box; website names Vector Data Labs as controller. Versions use nick@, contact@ and privacy@. Confirm correct identity and functioning contacts. Letter promises deletion within 72 hours; website describes eligibility after 72 hours followed by scheduled checks. Privacy policy still describes lead-data disclosure to customers as the core service.

Sources read on audit date:
- Homepage: https://treekey.co.uk/
- Packages (actual linked route): https://treekey.co.uk/pricing
- Marketplace: https://treekey.co.uk/marketplace
- FAQ: https://treekey.co.uk/faq
- Free signup: https://treekey.co.uk/free-account
- Terms: https://treekey.co.uk/terms-of-service
- Privacy: https://treekey.co.uk/privacy-policy
- Letter comparisons: user-supplied TreeKey-short-review.pdf and subsequent previews.

## B. Operating decisions or evidence needed

These are unresolved from available evidence, not confirmed implementation defects.

- [ ] **Approval changes:** what happens to subscription allocations while changed details await approval? Preserve approved mailing snapshots.
- [ ] **Cancellation:** define treatment of future allocations and already queued, paid or submitted letters.
- [ ] **Repeat contact:** reconcile one introduction per application with one contractor per homeowner. Cover duplicate records, later applications and objections.
- [ ] **Proof after deletion:** define retained buyer-visible evidence without recipient identity. Distinguish provider dispatch from confirmed delivery.
- [ ] **Production journey:** verify deployed signup → approval → payment → allocation → worker → provider status. Local test-mode success does not prove the whole journey.
- [ ] **Server PDF rendering:** confirm the actual sending environment can generate PDFs, not only the local computer.
- [ ] **Final letter:** approve design and wording, remove review placeholders and inspect final provider output. A controlled physical sample remains needed for window alignment and print quality. Whether the orange outline prints has not been conclusively established.

## C. Secondary copy corrections

- [ ] **Sales arithmetic:** £39/month = £468/year. One £400 job does not cover annual fees, even before job costs. Distinguish revenue from profit in return-on-investment claims.
- [ ] **Unsupported claims:** substantiate or soften “100% committed”, “photo-verified” and broad competitor comparisons.

## D. Future features — not launch blockers

The user requested these at the top of Claude's existing feature list. This records the request; it does not confirm that Claude updated its separate list.

1. [ ] **Optional business link and automatic QR code.** Signup/account field for website, Facebook business page or another business-page link. Label: “Business website or social page”. Help: “Optional. Add a link for customers to visit through a QR code on your letters. Change it anytime in Account details.” Direct QR code in reserved lower-left area, no visible border. No link means blank. Include in preview and require reapproval after destination changes.
2. [ ] **Optional contractor logo/advertising image.** Save an optional image in account settings for the reserved lower area, reuse on future letters and include in approval. Leave blank until implemented and an image is supplied.

## E. Order of work

1. Agree the business rules: service purchased, allocation, shortages, failed fulfilment, cancellation and repeat contact.
2. Finalise letter design and wording against those rules.
3. Align public pages and customer messages.
4. Verify final provider output and a controlled physical sample.
5. Verify the deployed customer-to-mailing journey before launch.
6. Add optional QR/logo features later.

## F. Strict usage limits for future Claude prompts

- One narrowly bounded change per prompt. Do not send “fix this whole audit”.
- Use existing fields; omit unavailable optional information rather than silently building a feature.
- No new database fields, migrations, dependencies or adjacent fixes unless explicitly scoped.
- Minimal meaningful focused verification; no repeated full-suite runs without a new reason.
- Stop and report if broader changes are needed.
- Distinguish sandbox work, installed files and deployed behaviour.
- This checklist alone does not authorise deployment, charges, real mail or production-data changes.
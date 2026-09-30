# TreeKey — outdated copy and conflicting instructions

Reviewed 30 September 2026. Document-consistency review against Nick's current instructions. Not a code audit, legal opinion, production verification or deployment instruction.

Nick confirmed: no emails have been loaded into Instantly. Campaign setup is still to be done.

## Current business model used for comparison

- TreeKey sells a printed and posted contractor introduction. New buyers do not receive homeowner identities/addresses.
- First eligible introduction: £4.99, printing/postage included, no required subscription. Free browsing/account access is different from a free posted letter.
- Discussed packages: Starter £39/6 introductions; Growth £79/10; Consultant £99/8; Commercial & Forestry £159/14; Elite £249/20. Extra marketplace discounts: 10/15/15/20/25%. This does not verify checkout configuration.
- Contractor approves reusable wording. Genuine saved business/contact details are required; personalisation is optional. Simple letter number and optional contractor offer are agreed. Contractor QR/business-link and logo/advertising features are future work.
- Working provider approach: PDF submission, two content pages on one duplex sheet, preserving measured address/barcode clearance. Supplied screenshots demonstrate test-mode results, not physical delivery or production readiness.
- Supply shortages: wait/carry-forward or suitable nearby alternatives with consent; eventual applicable refund. No guaranteed replies/jobs. Exclusivity applies to TreeKey's sale/introduction, not public planning information or outside competitors.
- Agreed retention: ordinary sale eligibility ends at day 56; unsold deletion at day 60 from council registration. Addressed records become purge-eligible 72 hours after confirmed dispatch and are checked on schedule. Provider/backups and legal assessment remain separate unresolved matters; September 30 drafts are not approval or implementation evidence.

## Priority 1 — could misdescribe the product or reverse recent work

1. **Checkout promises the wrong product.**
   - `CHECKOUT_COPY_SINGLE_LEAD.md:11,23,33` promises property address, applicant name and Street View. Line 23 says nobody else quotes it.
   - Replace with the posted introduction, without household identity supplied to new buyers or a claim that outside competition cannot exist.

2. **Terms describe information access, blanket non-refunds and a free lead.**
   - `terms_and_conditions_draft.md:21,37,41-49` says information access only, all payments non-refundable, and a free-lead promotion.
   - Rewrite around postal fulfilment, the £4.99 first offer, and agreed shortage/replacement/refund options. This is a business-policy mismatch, not a new legal conclusion.

3. **Privacy draft describes the previous data-selling model.**
   - `privacy_policy_draft.md:23,33,51-52` describes a directory and licensing/disclosure of lead data to customers.
   - Line 73 proposes 24-month lead retention, conflicting with the agreed 60-day/72-hour framework.
   - Update the actual data flow and retention wording once implementation/provider facts are checked. Do not treat this old draft as an accurate current-site mirror.

4. **Disclaimer assumes contractors can contact applicants before committing.**
   - `lead_disclaimer_draft.md:20,24` tells buyers to confirm availability directly with applicants, says information access only, and broadly disclaims any lead volume.
   - Keep the no-guaranteed-response/job principle, but distinguish uncertain homeowner responses from paid package fulfilment and remove the identity/contact assumption.

5. **Project State can regenerate obsolete marketing.**
   - `PROJECT_STATE.md:93-95` specifies a free-lead sequence, territory lockout and obsolete credit-pack/plan prices.
   - Lines 307-310 explicitly tell writers to describe the product as name-and-address leads.
   - Mark these instructions superseded. Preserve genuine engineering history separately.

6. **The master Manifest is not a current business specification.**
   - `MANIFEST.md:38,49,58,88-90` contains old £25/£50/£75 grading, obsolete subscription/credit-pack tiers, umbrella-company assumptions and projections based on old prices.
   - Label historical; use current agreed pricing and postal costs for future projections. Confirm legal identity separately.

7. **Provider documentation could undo the working PDF fix.**
   - `docs/operator_guide.md:1782,1803,1822,1841` mixes historical stub/account-access statements with a September 26 update saying raw HTML needs no PDF renderer and address-window clearance is unnecessary.
   - `docs/launch_checklist.md:16-32` repeats that address-window statement and says a real API test has not happened.
   - These are superseded by the later PDF-route/two-page/one-sheet test evidence in the supplied conversation. Preserve the working clearance and treat earlier statements as dated history. No adapter rebuild is requested.

## Priority 2 — planning/status drift

8. **Pricing proposal survives beside the later decision.**
   - `Claude outputs/TreeKey_pricing_proposal_draft.md:7,27-39` claims no letter-sending code, proposes an old pricing grid and £2.50 postage as an optional add-on.
   - Historical proposal only. Posting is included in the current introduction offer.

9. **The pricing final-decision file is only partly current.**
   - `Claude outputs/TreeKey_pricing_final_decision.md:9-13` matches the discussed monthly tiers.
   - Lines 31-41 defer shortage rollover until after launch and describe exclusivity enforcement as not switched on.
   - Retain valid pricing decisions; update the shortage policy and date the implementation claims. Do not discard the whole file or reimplement exclusivity based on old prose.

10. **Old launch checklists point back to obsolete terms.**
    - `TREE_LAUNCH_CHECKLIST.md:20` and the matching `.txt` say the legal drafts have only a few blanks, no LIA document exists, and blanket non-refunds are settled.
    - A September 30 LIA draft now exists, but remains unresolved, not approved. The old legal drafts have substantial product mismatches listed above.
    - These checklists also contain old privileged trigger URLs. Do not execute them during cleanup or copy them into public documents. No credential values are reproduced here.

11. **Legal identity and contact versions differ.**
    - `privacy_policy_draft.md:9-11`, `terms_and_conditions_draft.md:9,35,124`, `AI_HANDOFF.md:116` and `MANIFEST.md:58` use older operator/controller descriptions.
    - The current discussion proposes Nicholas Michael Secular trading as TreeKey. Confirm the correct legal identity and functioning privacy/support mailboxes before making notices consistent.
    - Do not assume a .uk email mailbox fails because the website redirects to .co.uk.

12. **Old handovers can direct work to the wrong folder or business model.**
    - `AI_HANDOFF.md:11,21,116` and `Claude outputs/TREEKEY_HANDOVER.md:40,52,132-144` contain the old OneDrive location, obsolete pricing/no-quota statements, and free-lead/address-unlock descriptions.
    - Actual folder inspected here: `C:\Users\twobo\Projects\VECTOR DATA LABS`. Sandbox, local files and deployment are distinct.
    - Label dated business/configuration snapshots accordingly; useful technical history can remain.

13. **Local status documents lag behind Claude's reported sandbox work.**
    - `CURRENT_HANDOFF.md:1` still describes the September 26 address-positioning pass.
    - Focused searches of it and `PROJECT_STATE.md` did not find the later reported letter-number, contact-first-name, contractor-offer and QR/logo queue updates.
    - This is consistent with those later changes being sandbox-only, not proof they were lost or that code is missing. Sync the completed checkpoint once, then update one authoritative handoff rather than rebuilding blindly.

14. **Marketing ideas use old economics.**
    - `MARKETING_OUTREACH_IDEAS.md:10-12,23-35` discusses free-first-lead offers, reviews incentivised with free leads and near-zero marginal cost.
    - Historical, unapproved ideas; postal introductions have per-item costs.
    - Its marketing-campaign QR idea is distinct from the deferred contractor QR on homeowner letters.

## Do not mistake history or uncertainty for a new defect

- Dated reports and `ERROR_LOG.md` record earlier problems/fixes. An old 'not implemented' statement does not prove something is still missing.
- September 30 LIA/retention drafts explicitly mark proposals and uncertainty. Do not turn them into deployed-policy or compliance claims.
- `TREEKEY_RULES_DECISIONS_2026-09-30.md` is an earlier snapshot, not automatically more authoritative than later explicit decisions in this conversation.
- The .uk marketplace redirects successfully to .co.uk. The preceding task confirmed anonymous public listings/search. Prefer .co.uk for campaign links; do not label .uk broken.
- Support correspondence is not contractor campaign copy. Other-business research is not a TreeKey specification.
- Deployment ZIPs/PDFs were inventoried by filename, not unpacked/rendered for this review. Treat them as dated artifacts, not current instructions.

## Smallest sensible cleanup sequence

1. Add superseded/current labels to the high-authority documents above so future AI sessions do not resurrect old requirements.
2. Align checkout, terms and privacy/disclaimer descriptions together with the agreed postal model, keeping unresolved legal/provider facts explicit.
3. Update provider guidance to the latest PDF evidence without rebuilding the integration or rerunning broad tests.
4. Reconcile the completed Claude sandbox checkpoint with the actual project once; update one current handoff/task queue.
5. Load approved emails into Instantly for the first time; configure and test opt-outs and final-follow-up exclusions before sending.

## Scope

Reviewed project Markdown/text/HTML filenames and targeted content, with focused reads of the main business, legal, pricing, provider and handoff documents. Not an exhaustive line-by-line review of every file, application-code audit, live website-copy audit or new legal review. No credential/environment files were opened. Findings are flags; original documents have not been rewritten or deleted. This pass saves this report and corrects the outreach index's Instantly setup status only.

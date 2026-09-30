# TreeKey — retention and deletion plan (working draft)

Prepared: 30 September 2026. Owner: Nicholas Michael Secular, subject to confirmation of controller identity.

**Status: proposed policy plus evidence checklist; not a claim of implementation or a production deletion instruction.** Existing 60-day and 72-hour controls are reported by Claude, not independently tested here. New periods below are recommendations requiring approval and a narrow implementation check. This document does not resolve the LIA/Article 14 question.

## Recommended starting policy

Keep the agreed 60-day unsold and 72-hour post-dispatch thresholds. Remove unnecessary names and identifying narrative sooner. Separate minimal mailing evidence, contractor accounting records and suppression matching from the addressed letter. Do not use a tax-record period to justify retaining homeowner addresses.

The proposed 12-month evidence and 30-day backup periods below are operational choices, not legal requirements or confirmed provider settings. Review them against actual needs and plan capabilities before publication.

## Retention schedule

| Record | Purpose and minimum content | Trigger and proposed rule | Status / evidence needed |
|---|---|---|---|
| Unsold eligible opportunity | Restricted address, source/date needed for validation and duplicate control, minimal work classification | Delete no later than council registration + 60 days under the agreed policy; stop ordinary sale eligibility at day 56. Remove earlier if irrelevant, withdrawn, undeliverable or no longer needed. Re-fetching must not restart the clock. | Reported implemented, actual coverage/date handling unverified. Assess necessity of 4-day inactive gap. |
| Applicant/agent names, sensitive or identifying narrative | Usually not needed in an occupier-addressed letter | Prefer not collecting/persisting names or raw narrative beyond necessary classification. Restrict existing data pending a scoped minimisation fix. Do not claim regex scrubbing anonymises it. | Recommendation; current collection/data copies need checking. |
| Quarantined/invalid-date records | Diagnose date/source issues without marketing use | Exclude from sale/mail immediately. Recommended maximum 7 days for diagnosis, then delete unless a specific documented reason requires a limited extension. Never keep indefinitely because a date is missing. | New proposal, not implemented/approved. |
| Purchased, not yet dispatched | Minimum address and approved letter snapshot needed to fulfil | Retain until dispatch, confirmed cancellation/failure or justified restricted exception. Review unresolved records at least weekly; escalate at 30 days. Do not automatically resend unknown outcomes. | Reported worker logic partly covers this; exception expiry is not established. |
| Provider-confirmed dispatched letter | Operational reconciliation | At dispatch confirmation + 72 hours, eligible for purge at next scheduled check (reported ~20 minutes). Delete name/address, addressed HTML/PDF and unnecessary re-identifying source fields across live copies. Outages may delay a run; alert and catch up. | Threshold reportedly implemented. Scope includes more than the leads table; verify before claiming completion. |
| Definitively cancelled/rejected mailing | Resolve transaction without further marketing | Proposed purge of no-longer-needed household details within 72 hours of final resolution; keep minimal failure/credit evidence separately. Unknown status is not definitive failure. | New proposal requiring approval. |
| Minimal mailing evidence | Letter number, contractor/order link, amount/status, provider reference, dispatch time, non-recipient template/version and approved contractor text | Proposed 12 months from dispatch or final resolution, then delete/minimise. Specific complaints/claims may justify a restricted hold, reviewed monthly. Preserve tax-required transaction data separately. | Proposal; justify against real complaint/payment windows. IDs may remain personal data through linkage. |
| Raw council references, source links, geolocation, full summaries | Needed only for active validation/fulfilment | Remove with the related opportunity/dispatch identifiers unless a separately documented necessary purpose exists. Retain keyed minimum duplicate markers instead where justified. | Earlier reports said raw references persisted; do not assume fixed. |
| Contractor invoices and tax records | Required business accounting evidence, excluding homeowner identity | Assuming sole trader status: ordinary HMRC minimum is 5 years after the relevant tax year's 31 January filing deadline, with exceptions for enquiries/late returns etc. Confirm actual tax/VAT/entity requirements. | Statutory baseline from S1; this does not apply indiscriminately to all application data. |
| Direct-marketing objection matching | Minimum keyed address/person matching token, scope and objection date | Keep while TreeKey could otherwise market to that person/address; review annually. No automatic expiry that restarts unwanted marketing. Delete when the need genuinely ends. | Keyed HMAC reported implemented, production backfill/key handling unverified. Pseudonymous, not anonymous. |
| Already-contacted application matching | Prevent another introduction about the same application | Retain minimum keyed source/application token while that application could re-enter any source/import; review annually against ingestion rules. Do not use it to infer consent for later applications. | Separate from person/address objection; confirm actual matching coverage. |
| Raw objection email/support material | Handle request, then retain only necessary evidence | Proposed remove copied address/content within 30 days after resolution where matching record suffices; substantive disputes may need a documented restricted hold. | Recommendation, including email systems and attachments. |
| Contractor settings/account data | Operate service and approved reusable letters | Active account: retain needed settings. Proposed remove non-accounting profile content within 30 days after final closure and outstanding obligations resolve; keep separately justified records only. | Not reported implemented. Cancellation of subscription is not necessarily account closure. |
| Temporary PDFs, exports, print buffers | Render and submit letters | Delete promptly after use; clean failed leftovers within 24 hours as a proposed backstop. Never commit to Git or put real-recipient files in ordinary backup ZIPs. | Actual temporary-file lifecycle unknown. |
| Logs and monitoring | Diagnose failures/security without recipient content | Do not log addressed PDFs/HTML, credentials or signed preview URLs. Proposed 30-day routine operational log expiry; longer incident records only with a defined purpose. | Actual logging services/settings unverified. |
| Database/file backups | Restricted disaster recovery | Proposed target: rolling maximum 30 days, if supported and justified; use a shorter existing suitable schedule rather than extending it. Document actual maximum including exports and replicas. Restrict access; replay deletion/suppression before restored data returns to service. | Provider/plan settings unknown. Not a promise of implemented 30-day deletion. |
| Intelliprint uploaded PDFs, addresses and derived files | Print/post, operational handling and provider obligations | Request shortest appropriate configurable period; ask whether TreeKey's 72-hour-after-dispatch target is supported and how returns/reprints/backups affect it. Record actual agreed maximum. | Unresolved. Do not infer from 7-year customer-account policy. |
| Historical purchased records | Any continuing necessary contractual/evidential purpose | Review individually/by justified category; no blanket permanent-retention exemption because earlier buyers had access. Keep minimum justified evidence and define expiry. | Prior reports excluded historical dispatches from purge; scope and remedy need review. |

## Deletion scope and event rules

Apply the schedule to all copies: lead and allocation records, obligations, frozen content, legacy dispatch rows, attachments, email copies, temporary rendering files, exports, admin downloads, logs, monitoring and provider storage where contractually controllable. Removing a live database address is not deletion from every system.

Use provider-confirmed dispatch, never acceptance, to start the 72-hour dispatch clock. A returning letter does not authorise re-contact. If a provider later reports a return, retain minimal status and refund/replacement evidence; do not repopulate deleted household details without a justified need.

A hold needs: specific reason, permitted fields, owner, next review date and release event. Financial reconciliation often needs only payment/order IDs, not the household address. Monthly hold review is a recommendation; it is not permission for indefinite retention. Honour rights requests without waiting for routine retention deadlines where applicable.

An opaque letter number remains available with minimal evidence during its retention period, so a contractor can match a voluntary enquiry after address deletion. A letter number is not authentication. Do not retain a public lookup or reconstruct the homeowner's identity for the buyer.

## Backup and processor responsibilities

The ICO explains that backup data awaiting scheduled replacement should be beyond ordinary use and that individuals need an accurate explanation of what happens to it. [S2] A restore must not restart marketing or resurrect records already due for deletion. Keep the minimum protected deletion/suppression information needed to enforce this, and ensure it survives restoration from an older snapshot.

TreeKey remains responsible for selecting/configuring processors and giving appropriate instructions. “We cannot control provider systems” is not a sufficient replacement for a processing agreement. Separate a provider's processor copies from any records it independently must keep for its own legal/accounting purposes.

Determine the actual database provider first: earlier reports mentioned Supabase, whereas the public policy described Render hosting/database. Record database, object storage, email, log and backup vendors separately; the app host alone does not establish where all data is held.

## Intelliprint finding and precise enquiry

Official public privacy page: states a 7-year period after the last purchase for personal data connected with products/services bought, for accounting/legal/tax purposes. This appears to describe customer data; it does not establish the expiry of recipient PDFs and addresses. [S3]
Official security page: advertises configurable data-retention policies. This indicates a setting may exist, not that it is enabled in this account or that any particular period applies. [S4]

Draft enquiry (not sent):

“We use your API to upload addressed two-page PDFs for postal introductions. Please confirm separately: (1) your role and applicable data-processing agreement for recipient data; (2) retention of uploaded files, generated/preview PDFs, recipient addresses and job metadata; (3) available automatic-deletion settings and their trigger; (4) whether deletion 72 hours after confirmed dispatch is supported; (5) remaining backups, logs, returns/reprints and legal-retention exceptions, with their maximum periods; (6) how erasure requests are actioned; and (7) subprocessors, processing/storage countries and applicable transfer safeguards. Does your public 7-year customer-data policy apply to recipient documents or only customer/account records? Please provide the relevant documentation or account-setting location.”

Do not send an API key, signed PDF URL or real household data with that enquiry.

## Small evidence-gathering tasks — no broad coding prompt

1. Obtain account evidence of actual database/backup provider, enabled schedule, oldest recoverable backup and any manual exports. No production deletion needed.
2. Obtain Intelliprint's answers/settings above; do not promise provider deletion before confirmation.
3. Owner approves/revises proposed 12-month evidence period and short operational periods. Verify tax status and accounting record requirements.
4. Give Claude one narrow task to map the approved schedule to existing controls, reporting only gaps. Do not ask it to implement this entire document in one session.
5. For each approved implementation gap, use a separate bounded task and meaningful focused checks; no destructive production purge without explicit authorisation.
6. Update full privacy policy and the shared letter reverse together after verification.

## Interim letter-copy recommendation

Do not use “permanently deleted everywhere within 72 hours”. Proposed wording once live-system scope is verified:

“Your address and personalised letter become eligible for deletion from our active mailing records 72 hours after our postal provider confirms dispatch. Our automated process checks approximately every 20 minutes; outages may delay a check. Limited transaction and objection records are kept separately. Our full privacy notice explains their retention and the separate arrangements for backups and postal-provider copies.”

Do not publish the final sentence until that linked notice actually contains the confirmed arrangements. Do not state a clean approved policy while controls or periods remain unresolved. The business rulebook's agreed retention thresholds are not evidence that every copy is covered.

## Approval / verification record

- Controller identity and tax status: PENDING confirmation.
- Proposed new periods approved by owner: NOT YET.
- Intelliprint settings/DPA: PENDING.
- Actual backup expiry/restore procedure: PENDING.
- Deletion coverage verified: PENDING.
- Final policy and letter updated: NOT DONE.

## Sources checked 30 September 2026

S1: HMRC, self-employed record retention: https://www.gov.uk/self-employed-records/how-long-to-keep-your-records
S2: ICO, right to erasure and backups: https://ico.org.uk/for-organisations/uk-gdpr-guidance-and-resources/individual-rights/individual-rights/right-to-erasure/
S3: Intelliprint privacy: https://www.intelliprint.net/privacy
S4: Intelliprint security: https://www.intelliprint.net/security
S5: ICO, right to object and suppression: https://ico.org.uk/for-organisations/uk-gdpr-guidance-and-resources/individual-rights/individual-rights/right-to-object/
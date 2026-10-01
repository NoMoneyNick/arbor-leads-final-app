# TreeKey — Current Business Model

Authoritative summary of the agreed launch model. Written 30 September 2026.
Later explicit decisions outrank older documents. Current campaign copy: `OUTREACH_START_HERE.md`.
This file is not a legal opinion, a deployment record or proof of launch readiness.

## A. Agreed business rules

**Product**
- TreeKey sells a printed and posted contractor introduction, not homeowner identities. Contractors choose an opportunity, approve a letter, and TreeKey prints and posts it. The homeowner contacts the contractor directly.
- Contractors receive no homeowner name, address or contact details. There is no "unlock address" step.
- Free browsing, previews and account access are allowed. A free posted letter is not: unlimited free posted introductions were rejected.
- No guaranteed replies or jobs. Exclusivity applies only to TreeKey's own sale/introduction, not to public planning information or outside competitors.

**Pricing**
- First eligible introduction: **£4.99**, printing and postage included, no subscription required. One per genuine business; standard opportunity; offered only when a suitable opportunity is available.
- Monthly packages discussed: Starter £39 / 6 introductions; Growth £79 / 10; Consultant £99 / 8; Commercial & Forestry £159 / 14; Elite £249 / 20.
- Extra marketplace discounts respectively: 10%, 15%, 15%, 20%, 25%. Make no other savings claims.
- "£4.99 vs £19" is not approved advertising (it needs a genuine reference price). Package savings previously worked out against £19 were illustrative only.
- Subscriptions auto-match agreed preferences. Marketplace purchases are individually selected.

**Letter**
- Contractor approves reusable wording. Once approved it is reused. Material changes to approved letter content require reapproval for future letters; already-queued letters keep the version approved. (Agreed rule; whether it is implemented is a separate question, see B.)
- Genuine business name, responsible contact name and contact details are required before sending. Personalisation is optional (offered at signup, in My Account, and as a final "Personalise / Use standard letter" choice before first approval). Skipping it uses complete standard wording. Never print placeholders or example details as facts.
- A simple letter number is printed on each letter so a homeowner can quote it. It is for matching only, not authentication, and exposes no identity.
- An optional contractor offer line is allowed.
- Nick approved all three front-page wording versions in this conversation; approved wording is reusable. Reverse-page privacy wording remains subject to final review (locked privacy text is not to be rewritten).

**Fulfilment and shortages**
- Fulfilment counts at provider-confirmed dispatch. An uncertain outcome must never trigger an automatic duplicate mailing.
- Shortage or failed fulfilment: waiting/carry-forward or replacement first. **Review point: 30 days after the affected billing period ends.** At that point offer continued waiting, suitable available nearby alternatives (only with the customer's consent), or an applicable refund for the unfulfilled portion.
- Cancellation, repeat-contact limits, retained evidence and privacy wording must agree across the service.

**Provider**
- Working approach: PDF submission to Intelliprint, two content pages on one duplex sheet, keeping the measured address/barcode clearance (left 18.5 mm, top 45.0 mm, right 106.9 mm, bottom 90.3 mm).

**Retention (agreed framework)**
- Ordinary sale eligibility ends at day 56; unsold records are deleted at day 60 from council registration. Addressed records become purge-eligible 72 hours after confirmed dispatch and are checked on schedule.

## B. Installed code (not deployed) and other reported-but-unverified items

- **Installed in the project files (30 Sep 2026), not committed or deployed:** banner/contact-panel design, letter number, optional contractor offer, and contact first name, with their tests, migration entries and a preview. 318 focused tests passed in the sandbox before install. Sandbox code check of the three personalisation opportunities found no gap. **Live behaviour is unverified**; deployment and the schema initialisation on first start have not happened.
- **Intelliprint test-mode results** are supplied evidence only. They do not show physical delivery, print accuracy on real stock or launch readiness.
- **£4.99 offer and package pricing** are agreed. **Sandbox verified (1 Oct 2026, per CURRENT_HANDOFF.md):** a Stripe test-mode purchase at GBP 4.99 created one paid session, the forwarded completion event was answered 200 with one order, allocation and letter obligation (left pending_approval); replaying the event and a new event ID created no duplicates; reuse of the first-offer price was refused; no live charge or provider submission occurred. **Not verified:** live-mode checkout or a real charge on the deployed site, package-price checkout, worker promotion and posting, and the cost and abuse checks below.
- **Reapproval of material letter changes** and the **30-day shortage review** are agreed rules; their implementation is not verified.
- **Retention timings** are agreed; their full implementation and scheduled checks have not been verified here.
- **Future work, not built:** contractor business-link with automatic QR (reserved area) and contractor logo/advertising upload (reserved area). Both areas stay blank.
- **Instantly:** no emails have been loaded. Email 1 (single version, TREEKEY_EMAIL_1.md, approved 1 Oct 2026) and Emails 2 and 3 (A/B) are approved copy; none is configured or sent.
- **Application-generated emails:** `send_cold_email_1` in `notifications.py` references the superseded old sequence. Its behaviour and whether it is enabled have not been audited.

## C. Unresolved decisions and external confirmations

- **Legal identity:** the trading/controller identity (a proposal names Nicholas Michael Secular trading as TreeKey) and working privacy/support mailboxes need confirming. Older documents say "Vector Data Labs".
- **UK GDPR Article 14 assessment** and the Legitimate Interests Assessment: drafts exist, neither is approved. No legal clearance is claimed.
- **Provider retention:** Intelliprint and backup retention/deletion of letter content and addresses are unconfirmed and need asking the provider.
- **Shortage handling:** how renewals are treated while owed introductions exist, and how owed introductions are treated after cancellation, are not settled.
- **Reverse-page privacy wording:** subject to final review; do not rewrite locked privacy text.
- **Cost and abuse checks:** current total print/postage/payment cost and abuse prevention for the £4.99 offer are not independently verified. An earlier test showed 108 pence, not a guaranteed cost.
- **PECR:** sole-trader recipients need caution in cold email. Opt-out handling must be configured and tested in Instantly before sending.
- **Legal drafts** (terms, privacy, disclaimer, LIA) remain drafts requiring final review. They are not approval or evidence of implementation.

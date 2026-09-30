# Tree Key — Privacy Policy

*DRAFT requiring final review. Not legal advice, not approved, and not evidence of implemented policy. Product descriptions updated 30 Sep 2026 to the posted-introduction model (see `CURRENT_BUSINESS_MODEL.md`); it is **not** a mirror of the live privacy page and may diverge from it. Unresolved: legal identity/controller, UK GDPR Article 14 assessment, final review and approval of the Legitimate Interests Assessment (a draft exists), print-provider and backup retention, Render region. No legal clearance is claimed.*

---

## 1. Who We Are

Tree Key ("we", "us", "our") operates the website treekey.co.uk and the postal-introduction service described in our Terms and Conditions. Tree Key is a trading name of Vector Data Labs, which is the data controller for the personal data described below. **[UNRESOLVED: legal identity/controller and trading name must be confirmed before publication; the wording here is the older description.]**

Contact for privacy matters: **contact@treekey.uk** **[UNRESOLVED: confirm this privacy/support mailbox works.]**

## 2. The Two Different Kinds of Personal Data We Handle

This is the part the previous policy didn't separate out, and it matters because the lawful basis is different for each.

**2.1 Customer data** — information about you, our paying customer: name, business name, email, phone number, billing details (processed by Stripe, see Section 6), and records of your usage of the Service.

**2.2 Lead data** — information about a third party named in a Lead: typically the name of a planning applicant or their agent/representative, sourced from public UK council planning application records, and in some cases a business name and director name sourced from Companies House. This data is about people who are not our customers and who have not signed up to anything.

## 3. Our Lawful Basis for Processing Lead Data

**3.1** We process Lead data (Section 2.2) on the basis of **legitimate interests** under UK GDPR Article 6(1)(f): specifically, our commercial interest in using publicly available planning and company data to identify properties where tree work may be needed, so that approved contractor introductions can be printed and posted to the household. Contractors do not receive the household's name or address; homeowners contact the contractor directly. **[UNRESOLVED: whether this basis holds is subject to the LIA and the Article 14 assessment, neither of which is approved.]**

**3.2 [UNRESOLVED — Article 14 assessment also unresolved]:** a Legitimate Interests Assessment (LIA) draft exists (30 Sep 2026) but is unapproved and needs final review; this policy states an intended basis, not a cleared conclusion. Relying on legitimate interests requires a documented Legitimate Interests Assessment (LIA) — a written record showing you considered the purpose, necessity, and balanced it against the individual's rights and reasonable expectations. This policy states the conclusion; the assessment itself needs to actually exist as a document you can produce if asked by the ICO. Your solicitor or a data protection consultant should help produce this alongside finalizing this policy.

**3.3** A person named in Lead data has the right to object to this processing (see Section 8). Where someone objects, we will stop processing their data for this purpose unless we can demonstrate compelling legitimate grounds that override their interests, or the data is needed for a legal claim.

## 4. What We Use Personal Data For

- Operating and improving the Service (both kinds of data);
- Providing customer support and processing payments (customer data);
- Compiling and classifying Leads, offering them to customers as postal opportunities (without supplying household identity to new buyers), and addressing and posting approved introduction letters to the household (Lead data);
- Sending customers service-related communications and, where they have not opted out, marketing about the Service;
- Complying with our legal obligations (e.g. tax, accounting).

We do not use Lead data to build profiles about the individuals named in it beyond what's needed to classify a Lead's relevance (e.g., whether a named agent appears to be a tree surgery business).

## 5. Where Lead Data Comes From

- UK local council planning application registers (public records);
- Companies House (public register, available under the Open Government Licence);
- Where used, business contact enrichment sources (e.g. Google Places, publicly listed business websites).

We do not purchase Lead data from private data brokers or scrape data that is not otherwise publicly accessible.

## 6. Who We Share Data With

- **Stripe** (payment processing) — customer payment and billing data. Stripe's standard Data Processing Agreement is incorporated automatically into its Services Agreement for all merchants, so this is very likely already in place; worth a quick confirmation but not a gap to build from scratch.
- **Render** (hosting) — the application and database are hosted with Render. **[TODO: confirm which Render region your service runs in — this determines whether Section 7 below needs UK/EU-specific transfer wording or not.]**
- **Print and post provider (Intelliprint, working choice)** — receives the letter content and the household's delivery address in order to print and post the introduction. **[UNRESOLVED: the provider's retention and deletion of letter content and addresses is unconfirmed and must be asked of the provider.]**
- **Our customers** — new buyers are not given the household's name, address or contact details. The letter they approve carries their own business details and a letter number; the homeowner chooses whether to contact them.
- We do not sell personal data to data brokers or advertisers.

## 7. International Data Transfers

Some of our processors (including Stripe, and potentially Render depending on the hosting region confirmed in Section 6) may process data outside the UK. Where this happens, transfers are protected by the UK's International Data Transfer Addendum to the EU Standard Contractual Clauses, or an equivalent lawful transfer mechanism, as provided by each processor's standard terms. **[TODO: once Render's region is confirmed, state plainly here whether this section actually applies or can be simplified to "we do not transfer data outside the UK."]**

## 8. Your Rights

Both customers and individuals named in Lead data have the right, under UK GDPR, to:

- request access to the personal data we hold about them;
- request correction of inaccurate data;
- request erasure ("right to be forgotten"), subject to our legal bases for retaining it;
- object to processing based on legitimate interests (Section 3);
- request restriction of processing in certain circumstances;
- lodge a complaint with the Information Commissioner's Office (ico.org.uk).

To exercise any of these rights, contact **contact@treekey.uk**. **[TODO: verify the existing mechanism for a Lead data subject objecting or requesting erasure, and identify any remaining operational gaps. Its implementation has not been checked in this documentation pass.]**

## 9. Data Retention

Agreed business framework for Lead data (Section 2.2): ordinary sale eligibility ends at day 56 and unsold records are deleted at day 60 from council registration; addressed records become purge-eligible 72 hours after confirmed dispatch and are checked on schedule. This replaces the earlier 24-month proposal. **[UNRESOLVED: provider and backup retention; this draft does not verify that the timings are implemented.]** Customer account data (Section 2.1) is retained for the life of the account, and billing records are kept for 6 years after account closure to meet HMRC record-keeping requirements. **[TODO: confirm these periods match what you actually want and what the database is capable of enforcing — a stated policy that the system doesn't actually implement is its own compliance gap.]**

## 10. Security

We take reasonable technical and organizational measures to protect personal data, including: encrypted (HTTPS) connections throughout the Service; account sessions secured with signed, tamper-evident tokens rather than plain credentials; no customer passwords are stored at all (login uses a one-time emailed link rather than a stored password, so there is no password database to be breached); and administrative and automated-scan functions are protected by a separate access secret, not exposed publicly.

## 11. Cookies

Tree Key currently sets one cookie: a signed session cookie (`treekey_contractor_session`) used solely to keep you logged in, marked HttpOnly, Secure, and SameSite=Lax. This is a strictly necessary cookie required for the Service to function, so under UK PECR rules it does not require a cookie consent banner. **This section needs revisiting the moment any analytics, advertising, or tracking cookie is added to the site** — at that point a consent mechanism becomes legally required and this policy must be updated before that cookie goes live, not after.

## 12. Children

The Service is intended for business use and is not directed at children. We do not knowingly collect personal data from children.

## 13. Changes to This Policy

We may update this policy from time to time; material changes will be reflected by an updated "last updated" date, and significant changes affecting Lead data subjects' rights will be communicated where practical.

## 14. Contact

Questions or requests regarding this policy: **contact@treekey.uk**.

---

## Open points for final review (agrees with the notice at the top)

This remains a draft. Nothing here is legal clearance.

1. **Legitimate Interests Assessment (Section 3.2):** a draft exists (30 Sep 2026) but is unapproved. It needs final review before this policy relies on it.
2. **UK GDPR Article 14 assessment:** unresolved.
3. **Legal identity and contact:** confirm the correct controller/trading identity (top of document; the wording here is the older description) and that the privacy/support mailbox works.
4. **Print-provider and backup retention:** the provider's retention and deletion of letter content and addresses is unconfirmed (Section 6); backup retention is also open. Confirm the Render region (Sections 6/7).
5. **Data-subject requests (Section 8):** verify the existing mechanism for someone named in a Lead to object or request erasure, and identify any remaining operational gaps. Its implementation has not been checked in this documentation pass.
6. **Retention (Section 9):** the agreed 56-day / 60-day / 72-hour framework is a business rule; this draft does not verify that it is implemented.
7. **Live page:** compare the live privacy page with this draft and note where they differ.

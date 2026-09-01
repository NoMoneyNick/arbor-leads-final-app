# Tree Key — Privacy Policy (Draft for Solicitor Review)

*Not legal advice. Written to replace the current live `/privacy-policy` page, which is materially thin for what this business actually does with personal data. [TODO] items need a decision or fact from you before publishing.*

**Read this first:** the current live policy says "We do not sell your personal data to third parties." Given the business model is selling access to Leads containing personal data sourced from public records, that sentence is a real liability as written and should not stay live in its current form regardless of when this fuller rewrite is ready — flag it to your solicitor as urgent on its own.

---

## 1. Who We Are

Tree Key ("we", "us", "our") operates the website treekey.uk and the lead-generation service described in our Terms and Conditions. For data protection purposes, Tree Key is the data controller for the personal data described below.

**Fill in before publishing:** insert your full legal name and address here — "**[Your Full Legal Name]**, trading as Tree Key, of **[address]**" — same fill-in as the Terms document; a data controller must be identifiable, not just a trading name.

Contact for privacy matters: **contact@treekey.uk**

## 2. The Two Different Kinds of Personal Data We Handle

This is the part the previous policy didn't separate out, and it matters because the lawful basis is different for each.

**2.1 Customer data** — information about you, our paying customer: name, business name, email, phone number, billing details (processed by Stripe, see Section 6), and records of your usage of the Service.

**2.2 Lead data** — information about a third party named in a Lead: typically the name of a planning applicant or their agent/representative, sourced from public UK council planning application records, and in some cases a business name and director name sourced from Companies House. This data is about people who are not our customers and who have not signed up to anything.

## 3. Our Lawful Basis for Processing Lead Data

**3.1** We process Lead data (Section 2.2) on the basis of **legitimate interests** under UK GDPR Article 6(1)(f): specifically, our commercial interest in aggregating publicly available planning and company data into a usable directory for tree surgery and arboricultural businesses.

**3.2 [TODO — do not skip this]:** relying on legitimate interests requires a documented Legitimate Interests Assessment (LIA) — a written record showing you considered the purpose, necessity, and balanced it against the individual's rights and reasonable expectations. This policy states the conclusion; the assessment itself needs to actually exist as a document you can produce if asked by the ICO. Your solicitor or a data protection consultant should help produce this alongside finalizing this policy.

**3.3** A person named in Lead data has the right to object to this processing (see Section 8). Where someone objects, we will stop processing their data for this purpose unless we can demonstrate compelling legitimate grounds that override their interests, or the data is needed for a legal claim.

## 4. What We Use Personal Data For

- Operating and improving the Service (both kinds of data);
- Providing customer support and processing payments (customer data);
- Compiling, classifying, and displaying Leads to subscribed customers (Lead data);
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
- **Our customers** — Lead data is disclosed to subscribing customers as the core of the Service.
- We do not sell personal data to data brokers or advertisers. We do commercially license access to Lead data as the Service itself — this is described plainly here rather than denied, unlike the current live wording.

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

To exercise any of these rights, contact **contact@treekey.uk**. **[TODO: build an actual process for handling this — a Lead data subject objecting or requesting erasure needs a real mechanism to remove them from active Leads, not just a promise in a policy.]**

## 9. Data Retention

Proposed default, for you to confirm or adjust: Lead data (Section 2.2) is retained for 24 months from discovery, after which personal identifiers (names) are anonymized or deleted, though the underlying planning application record may be retained in non-identifying form for business analytics. Customer account data (Section 2.1) is retained for the life of the account, and billing records are kept for 6 years after account closure to meet HMRC record-keeping requirements. **[TODO: confirm these periods match what you actually want and what the database is capable of enforcing — a stated policy that the system doesn't actually implement is its own compliance gap.]**

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

## Priority order for your solicitor / DPO conversation

1. **Take down or rewrite the "we do not sell your personal data" line on the live site now** — this is the most urgent single item across all three documents, and doesn't need to wait for the rest of this rewrite.
2. **The Legitimate Interests Assessment (Section 3.2)** — the actual documented assessment, not just the policy's summary of its conclusion. Nothing in this session can substitute for that document actually being written.
3. **Insert your legal name/address** (top of document) and **confirm your Render hosting region** (Section 6/7) — the only two remaining fill-in-the-blanks; everything else in this draft has been resolved against your actual codebase rather than left as a placeholder.
4. **Build the actual mechanism** for someone named in a Lead to object or request erasure (Section 8), and confirm the retention periods proposed in Section 9 are both what you want and something the database can actually enforce.

# TreeKey — Data Journey & Solicitor Evidence Pack

*Prepared for a fixed-fee solicitor review of TreeKey's core data-collection-and-resale model. Facts below are pulled directly from the live application code (database.py, main.py, scanners.py), not from memory or documentation — dated 18 Sep 2026. No real customer or homeowner personal data is included; the example lead below is illustrative, built from the real database schema.*

## 1. What TreeKey does, in one sentence

TreeKey scans UK council planning application registers daily, identifies applications that involve tree work, and sells access to the application details (including the applicant's name and full address) to tree surgeons and arboricultural contractors — each lead sold to one buyer only, never resold.

## 2. The data journey (one page)

| Stage | What happens | Where in the system |
|---|---|---|
| **Collection** | A daily scan pulls new planning applications from ~350 UK council registers (paid APIs, aggregator data, and direct council scrapers). Every tree-relevant application is stored, along with a smaller population of non-tree applications (HMO conversions, extensions etc.) that get scraped incidentally and excluded from sale. | `scanners.py` |
| **Fields captured** | Applicant's full name, agent/company name (if any), full property address, the full text of the planning application/description, council name, application reference, registered date, discovery date. A `homeowner_contact` column exists in the schema but has never been populated by any code path — no phone or email for the homeowner is ever held. | `database.py`, `leads` table |
| **Storage before sale** | The lead sits in the marketplace with address and applicant name hidden from public view — visible fields pre-purchase are council, category, rough location (postcode district), a summary of the work, and price. | `main.py` marketplace view |
| **Disclosure at sale** | On purchase, the buyer (a contractor) receives the applicant's full name and full address. | `main.py` checkout/delivery flow |
| **Retention — unsold leads** | Auto-deleted after 56 days, if both the registered date and discovery date are that old. Confirmed in code: a daily rescan does **not** reset this clock. | `database.py` |
| **Retention — sold leads** | No deletion policy. Kept indefinitely once purchased. | `database.py` |
| **Non-tree applications** | Same personal-data fields (name, address) are captured and stored for applications that are excluded from the marketplace entirely (e.g. HMO applications) — currently no commercial purpose and no distinct retention rule. | `scanners.py` classification step |
| **Homeowner notice** | None. No applicant/homeowner is proactively told their application data has been collected and is being sold to a third party for commercial purposes. The only disclosure is a passive privacy-policy link on the TreeKey website. | — |
| **Homeowner objection** | No mechanism exists. The only opt-out/suppression system in the codebase (`email_suppressions`) is scoped to contractors/subscribers unsubscribing from TreeKey's own marketing emails — a different population, keyed by an email address homeowners don't have on file in the first place. | `database.py` |
| **Public redaction** | Address is stripped from public marketplace listings via best-effort regex; the code's own comments say this is not guaranteed complete. | `main.py` |

## 3. Illustrative example (not a real record)

Built from the real schema and a real category of live listing (a "discharge of planning conditions" application, Peterborough City Council), with names and exact address invented:

```
council:          Peterborough City Council
reference:        [illustrative] 23/0XXXX/HHFUL
applicant_name:    [illustrative] J. Smith
agent_name:        [illustrative] — (none)
address:           [illustrative] "14 Example Close, Peterborough, PE3 xxx"
description:       Discharge of conditions relating to tree protection
                    (root protection area, construction method statement)
registered_date:   2026-08-xx
discovered_at:     2026-09-18 (auto-scan)
status:            new (unsold) — visible to buyers as council/category/
                    price only until purchased
homeowner_contact: NULL (never populated)
```

Pre-purchase, a buyer sees only: council, category ("General/Other"), postcode district (PE3), price (£49), and a short work description — no name, no full address. Post-purchase, they receive the applicant's full name and address as shown above.

## 4. The question for the solicitor

*"Can TreeKey lawfully collect these exact planning-application fields (applicant name, agent name, full address, application text) from public council registers and sell each one once to a contractor for direct outreach — and precisely what must TreeKey tell the applicant, by when, and through what channel, to meet UK GDPR's Article 14 duty to inform data subjects when data wasn't collected from them directly?"*

Secondary, same review: what (if any) objection/suppression mechanism is legally required, and is the current 56-day/indefinite retention split defensible or does it need its own justification per data category (unsold leads vs. sold leads vs. non-tree applications vs. payment records)?

## 5. Not covered by this pack

This pack is scoped to the core product (collecting and selling homeowner planning data). It does **not** cover the separate, already-identified UK PECR problem with cold-emailing sole-trader contractors — that's a different regulatory question (marketing consent to a *business* recipient) and should be raised as a second, distinct question if the same solicitor is asked about it.

## 6. Action still outstanding, separate from this pack

Confirm production credential rotation (Stripe secret key, webhook secret, DB URL, Maps API key, admin dashboard password) — flagged previously, status not yet confirmed. This is an immediate security task, independent of the legal timeline above.

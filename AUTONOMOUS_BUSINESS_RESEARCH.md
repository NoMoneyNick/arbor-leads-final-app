# A Small Automated Recurring-Revenue Business

> Superseded on 13 September 2026. The user rejected website maintenance and services dependent on establishing business trust. Google Workspace is now confirmed. This document is retained as research history, not the current recommendation. See MARKETPLACE_INCOME_RESEARCH.md for the revised assessment.

## Budget revision: no new recurring software costs

The budget has changed: the business must use free software and resources already owned. Workspace is available, but its exact product and any existing hosting capacity have not yet been confirmed. The paid-stack recommendations and cost model below are retained as research history and competitor comparisons, not as an approved build plan. The shortlist is provisional until the free deployment can be specified without losing the required automation.

The revised implementation must not depend on a paid trial, a future paid upgrade, a monthly automation platform, paid SMS/voice, or paid runtime AI inference. Claude Code can build deterministic software; ongoing operation does not inherently require calling an AI model. Existing Workspace or Claude subscription costs are sunk resources for this incremental budget, not evidence that additional hosting or API usage is included.

If Workspace means Google Workspace, Apps Script can run scheduled jobs, fetch web resources and produce email notifications within account quotas. Google documents a six-minute limit per execution and other daily limits. This is suitable for lightweight checks, administration and reporting; it is not a general PHP server or a full browser-testing environment. [25][26]

MainWP Essentials is free and self-hosted, but needs a suitable WordPress/PHP host. It does not contain its own native backup engine, and paid add-ons cannot be assumed available. UpdraftPlus has free backup functionality, but any required feature must be checked against the free edition. Existing hosting or an appropriately maintained computer may make this route possible without another subscription; Google Workspace alone does not establish that hosting exists. [27][28][29]

Cloudflare Workers has a free plan with usage and CPU limits. Full browser execution has a separate free allowance of ten minutes daily, so website availability monitoring and complete browser/form testing must not be treated as equivalent free workloads. No paid upgrade is part of the revised plan; capacity must stay within actual free limits. [30][31]

Payment-processing fees when a sale occurs remain a separate budget category. No assumption has been made that the operator accepts those fees merely because monthly software charges are excluded. Support time, incident work and customer acquisition also remain real economic considerations even when software licence costs are zero.

### Additional sources for the budget revision

25. Google. [Apps Script quotas](https://developers.google.com/apps-script/guides/services/quotas).
26. Google. [Installable triggers](https://developers.google.com/apps-script/guides/triggers/installable).
27. MainWP. [Free features](https://mainwp.com/mainwp-free-features/).
28. MainWP. [Why backups are separate](https://docs.mainwp.com/sites/backups/why-doesnt-mainwp-include-backups-in-the-dashboard).
29. WordPress.org. [UpdraftPlus plugin listing](https://wordpress.org/plugins/updraftplus/).
30. Cloudflare. [Workers limits](https://developers.cloudflare.com/workers/platform/limits/).
31. Cloudflare. [Browser Run limits](https://developers.cloudflare.com/browser-run/limits/).

## Earlier assessment under the superseded software budget

The strongest practical candidate is a narrowly scoped WordPress website-care service for existing, straightforward business websites. The runner-up is managed contact-form monitoring for web agencies. Both can use existing infrastructure while Claude Code builds the onboarding, billing, evidence collection and exception-handling software.

Neither is a verified full pass against a requirement for consistent profit within fourteen days. The technical mechanisms, active competition and substantial underlying markets are observable. Customer acquisition speed, retention and the actual amount of human support remain unmeasured. A large market makes these credible candidates; it does not establish that a new entrant will acquire customers easily.

## Decision standard

This assessment uses the revised brief: competition is acceptable, the target is a few hundred pounds in monthly profit, fulfilment should become largely autonomous, and first recurring income should be possible within one or two weeks. AI resistance, originality and physical fulfilment are no longer mandatory.

Two interpretations of the timing requirement need separating. Starting a paid recurring subscription in fourteen days is technically possible for the shortlisted services. Establishing several hundred pounds of reliable monthly profit in the same period requires enough paying accounts, and that has not been demonstrated. Neither setup fees nor an annual payment collected upfront establishes consistent monthly profit.

Ideas were assessed for a specific payer, an observable recurring need, a short delivery path, controllable running costs, manageable exceptions and an acquisition route that does not depend on months of search-engine rankings. Competitor price pages establish available offers, not the profitability of their sellers. Vendor case studies are useful directional evidence and are identified as such.

## Candidate screening

| Candidate | Evidence and commercial assessment | Decision |
|---|---|---|
| WordPress care for simple business sites | Large installed base, existing paid care plans and established automation infrastructure. Repairs and acquisition are the main constraints. | Strongest practical candidate; conditional on operational capability and paid acquisition evidence. |
| Managed contact-form testing for agencies | CheckView and other products directly serve this need. The service could automate checks and reports while an agency handles repairs. | Best alternative when low operator involvement matters more than a broad service proposition. |
| Generic AI receptionist | Mature answering-service companies now offer AI, with Moneypenny advertising entry pricing from £25/month. Call quality, phone setup and differentiation require substantial attention. | Deprioritised against simpler monitoring and maintenance. |
| Automatic text after every missed call | Existing offers and low technical complexity, but a missed call cannot simply be assumed to satisfy messaging-provider consent requirements. | Reject the unconditional version. A consent-based redesign would need a fresh commercial test. |
| Quote follow-up software | QuoteFollow advertises a £9/month launch plan. Replies, accepted quotes and offline customer decisions need reliable synchronisation. | Viable category, but low price and integration work weaken the immediate-income case. |
| Review-request automation | NiceJob sells review automation at $75/month. It needs a dependable event indicating a completed customer job, appropriate messaging permissions and client setup. | Possible, but no demonstrated advantage over the leading candidates. |
| DMARC monitoring | Paid and free products exist. DDMARC includes two domains in a free monitoring tier. Diagnosing and changing email authentication can require skilled intervention. | A monitoring-only offer is easy to substitute; remediation weakens autonomy. |
| Replacement consumables | Physical repeat purchases can be automated, but supplier terms, delivered margin, returns and acquisition costs have not been established. | Do not promote to finalist on hypothetical wholesale margins. |
| Local service booking brokerage | Established supplier-network models exist, including skip-hire brokers. Availability, delivery failures and disputes create operational work. | Weaker fit for limited owner involvement. |
| Generic downloadable products or content sites | Low fulfilment costs, but no specific short-term distribution advantage was identified. | Deprioritised for this brief; not a claim that early sales are impossible. |

The original idea list remains useful as a source of mechanisms: identifying neglected recurring work, collecting evidence and automating follow-through. It does not supply verified acquisition costs or customer willingness to pay. Existing software-engine names and supplied ratings are treated as potential implementation resources, not proof of production readiness.

## Candidate one: WordPress care for simple business websites

The proposed offer is: “We maintain your existing WordPress site, keep recoverable backups, check agreed pages and enquiry journeys, and send you a clear monthly record of the work.”

The initial customer is an incorporated small business with an existing brochure-style WordPress website, no effective maintenance arrangement and an owner who wants someone else to operate the maintenance process. A suitable site has supported components, recoverable access and a small number of important customer journeys. The business should not initially accept shops, membership systems, custom applications or sites with unresolved compromise.

These boundaries determine whether the business can stay largely automated. Every additional payment flow, complex integration or custom component adds another possible exception. They are commercial admission criteria, not assertions that excluded websites cannot be maintained.

### Market and competition

W3Techs reported WordPress on approximately 40.3% of surveyed websites when checked, and 58.8% of sites with a known content-management system. This establishes a substantial installed base, not a count of businesses willing to buy this particular service. [1]

UK care-plan offers provide a relevant price reference. The WordPress Guy advertises an Essentials plan at £49/month, including backups, monitoring and staged updates. Pixelish advertises £49 and £99 plans, with hosting included and broader services at the higher tier. These are direct competitors and substitutes. Their advertised scope needs comparing carefully; £49 is not an underserved price point merely because the proposed service uses automation. [2][3]

A ManageWP case study published in December 2019 describes an agency introducing recurring maintenance plans for its existing clients and reducing part of its maintenance workload. It is historical, vendor-published evidence of the model, not evidence that a new entrant can win clients in a fortnight. The agency already had customer relationships, which is a significant difference. [4]

The opportunity is therefore a small share of an established service market. The defensible sales proposition would be clear scope, competent setup, demonstrable recovery procedures and reliable communication. It would not be novelty, a secret tool or a claim that an AI-built service is inherently superior.

### Proposed scope and price

Start with a single £49 monthly plan for an eligible existing site. Use a separate, quoted onboarding charge when an initial audit, configuration or remedial work requires it. Keep that charge out of recurring-profit calculations. The proposed subscription price is a test price anchored to competitor offers, not validated willingness to pay.

Routine scope:

- Scheduled backups and checks that backup jobs succeeded.
- Tested updates for an agreed set of supported components.
- Monitoring of availability, certificate status and selected important pages.
- Authorised testing of one agreed enquiry journey.
- An evidence-based monthly report.
- Initial incident diagnosis and recovery for failures caused by the managed update process, within explicit service terms.

Hosting, premium plugin licences, content changes, redesigns, complex remediation and unrelated third-party failures need explicit treatment. An inexpensive monthly plan cannot silently promise unlimited development or continuous human emergency cover. Conversely, an operator cannot advertise a managed service and abandon problems caused by their own updates merely because a time allowance has run out.

### What can be automated

ManageWP provides scheduled Safe Updates, backups, monitoring and reports. Its pricing lists premium backups at $2 per site per month, and several monitoring/reporting add-ons at $1 each. These are component prices, not the full operating cost. [5]

WP Umbrella advertises a €1.99 base subscription per site, with a separate €2 security add-on and €2.49 hourly-backup add-on. Its pricing page also advertises a public API and MCP access. Exact integration coverage still needs checking during implementation. [6]

MainWP is another possible foundation: its documentation describes a REST API with site, client, update and monitoring endpoints. Choosing it would also mean operating the management installation. It is an alternative, not an additional tool that must be purchased alongside the other platforms. [7]

Claude Code could build the surrounding workflow: onboarding forms, a client/site inventory, Stripe subscriptions, scheduled evidence checks, alert deduplication, reports, a support queue and an operator dashboard. The existing reporting, notification and workflow engines may shorten this work if their interfaces and reliability are suitable. The escrow and marketplace engines are unnecessary for this model.

The first version should use an established maintenance platform for its core functions. Rebuilding backup storage and recovery before acquiring the first customer would add delay and operational exposure without establishing demand.

### What remains human

Someone must initially check site eligibility, verify access, test recovery, agree the important pages and configure any authorised enquiry test. Someone must also handle unclear failures, compatibility disputes, commercial questions and changes outside the standard plan.

An AI-generated diagnosis does not establish that a repair is safe. A known recovery procedure can be automated within its tested conditions; a novel production repair needs review. If the operator cannot perform recovery and has no competent fallback, the care service is not ready to sell. A limited incident budget is included below, but no external support arrangement or quote has been secured.

“Largely autonomous” should be judged by measured owner time, not the percentage of individual tasks performed by software. Thousands of successful checks can coexist with one incident that consumes a whole day.

## Adversarial examination of website care

**Objection: WordPress already updates itself.** Correct. The paid value must include operating and checking the process, recovery readiness and clear accountability. WordPress's own documentation advises ensuring rollback capability before enabling plugin and theme auto-updates. This is a service proposition layered on existing functionality. [8]

**Objection: A homepage that loads can still have a broken form.** Correct. ManageWP's documentation describes pre-update backups, HTTP checks and screenshot comparison; its automatic-restore explanation identifies error-status detection. Those checks should not be represented as proof of every business function. Selected browser journeys and a verified test-notification route are separate controls. [9][10]

**Objection: Restoring the database could delete new enquiries.** Correct. Even a brochure site can receive data between a backup and a restore. Recovery should preserve intervening submissions, use component-level recovery where suitable and avoid blind database rollback. Reject sites whose data changes cannot be safely handled by the initial process.

**Objection: Rolling back a security update leaves the vulnerability present.** Correct. Rollback is temporary containment, not a completed fix. The incident stays open until there is a safe resolution, mitigation or explicit customer decision. A system that reports green simply because the old site loads is inadequate.

**Objection: Many cheap plans include more.** Correct. There is no proven price advantage. Target customers must value the delivered service and scope; the proposition fails if they prefer a competitor's broader package and there is no other meaningful reason to choose it.

**Objection: Customers will request content edits and unrelated repairs.** Likely enough to plan for, but the rate is unknown. Use clear scope and a separate quote process. Record all support time. Do not interpret reasonable questions about the subscribed service as billable redesign work.

**Objection: Credentials and platform access create serious responsibility.** Correct. Use restricted accounts where possible, isolate tenants, protect credentials, log changes and verify recovery before activation. Existing tools reduce engineering work but do not transfer all responsibility to their vendors.

**Objection: A stranger will struggle to gain access to a business website.** This is the strongest unresolved sales objection. A sample report, a working demonstration and an authorised onboarding process can help, but cannot prove trust or conversion. Existing designer referrals may improve the route; no referral partners are currently established.

None of these problems proves the model impossible. Several are mandatory operational design work. The acquisition and support assumptions require paid operating evidence, so the candidate is not presented as a completed validation.

## Economics and the fourteen-day condition

The following model is a proposed budget, not observed profit. It assumes £49 monthly revenue per customer, £8 per customer for tools and payment costs, £50 shared monthly overhead, £75 incident allowance, £60 ongoing acquisition/retention allowance and four hours of owner time valued at £25/hour. Setup labour, initial build costs and personal/business taxes are additional.

| Paying customers | Monthly revenue | Tools/payment allowance | Shared costs and allowances including owner time | Modelled monthly surplus |
|---:|---:|---:|---:|---:|
| 4 | £196 | £32 | £285 | -£121 |
| 8 | £392 | £64 | £285 | £43 |
| 12 | £588 | £96 | £285 | £207 |
| 16 | £784 | £128 | £285 | £371 |
| 20 | £980 | £160 | £285 | £535 |

The formula is £41 multiplied by paying customers, less £285. The reserves are planning allowances; actual accounting profit depends on actual expenditure. The table avoids treating owner labour as automatically free. The four-hour assumption is a target to test, not an estimate supported by a time study.

Stripe's UK standard-card price is 1.5% plus 20p, and pay-as-you-go Billing is separately priced at 0.7% of Billing volume. At £49 this combination is approximately £1.28 per successful standard UK-card subscription payment. Other card types, taxes and additional services change the result. This is part of the £8 allowance, not an additional deduction in the table. [11]

Sensitivity matters. At sixteen customers, another four hours of owner work reduces the modelled surplus to £271. An additional £200 repair beyond the allowance reduces £371 to £171. Two cancellations reduce monthly contribution by £82 before any replacement-acquisition cost. If marketing consumes £300 rather than £60, the surplus falls to £131.

The acquisition arithmetic is similarly explicit. Winning sixteen customers from 200 qualified approaches requires an 8% paid conversion rate; from 400, 4%; from 800, 2%. These are required rates, not expected rates. Researching prospects, obtaining replies, showing the service and completing onboarding all take time. A large global installed base does not establish any of these conversion rates.

### A bounded launch experiment

**Days 1–3:** Configure a demonstration site and one maintenance platform. Introduce controlled failures on the demonstration site, verify detection and recovery, and produce a real sample report. Record tool costs and time. Have clear service scope and a working support route before accepting a customer.

**Days 4–7:** Prepare a short offer and a modest, genuinely qualified prospect list. Prefer existing introductions and designer referrals if available. Where direct email is used, distinguish corporate subscribers from sole traders, use an appropriate lawful process and honour objections. The ICO's guidance expressly distinguishes these groups; a public business address is not universal permission for outreach. No messages have been sent as part of this research. [12]

**Days 7–14:** Complete authorised audits, activate only eligible sites and collect the first subscription payment when the service is ready. Measure paid conversion, onboarding time, support time, direct cost and the customer's reason for buying. A first subscription is evidence of a first buyer, not evidence of retention.

Use a pre-agreed spending and effort ceiling for this experiment. A reasonable initial decision rule would require multiple unrelated paying customers at the intended price, successfully completed recovery tests and a credible contribution margin. The exact customer count is a management threshold, not statistical proof. If interest is limited to free audits, setup projects or much lower prices, do not keep adding features to disguise weak demand.

Consistent monthly retention cannot be measured inside a fortnight. If the requirement is proof of consistent £300-plus monthly profit by day fourteen, this research has not identified a candidate that can honestly be certified to satisfy it.

## Candidate two: managed enquiry-form monitoring for agencies

The narrower offer is: “We configure and run scheduled tests of agreed enquiry forms across your client sites, confirm the part of delivery we can observe, and send you actionable failure reports.” The agency retains responsibility for repairing its sites. This avoids much of the production-change responsibility in a full care service.

There is direct competition. CheckView advertises $35/month for 250 tests and $70/month for 1,000 tests, with unlimited websites. FormStory advertises a free single-form tier and a $79/month agency tier; it has a different emphasis on submission tracking and capture. FormWatch also advertises delivery monitoring, although some paid tiers were marked coming soon when checked. These are not empty-market conditions. [13][14][15]

The service could charge a proposed £49/month for managed configuration and testing of up to five agreed, supported forms. Twelve agencies would generate £588 monthly revenue. That is revenue only: browser execution, email handling, support, acquisition and owner time still need deducting. The price is a hypothesis, and agencies can buy tools directly.

The main reason an agency might buy is to avoid configuring and maintaining tests. If its staff can obtain the same result easily using CheckView, the proposition is weak. The agency must see a useful managed outcome, not another generic uptime dashboard.

Claude Code can implement browser checks using established tools, plus billing, evidence storage and alerts. Checkly documents browser-based tests for customer journeys. Its price page separates check counts, frequency and overages; each retry and additional location can increase usage. Test costs need calculating from the actual schedule rather than an advertised entry price. [16]

Important limitations must be visible in the service. A form showing a success message does not prove that its notification reached the intended mailbox. A copy sent to a monitoring inbox proves only that route. Full delivery confirmation requires an authorised observation or forwarding rule at the relevant recipient, with its own limitations. CAPTCHA, site redesigns and changing field requirements can require test maintenance. Do not evade controls or test unrelated websites without permission.

This candidate has cleaner fulfilment autonomy than website care, but a narrower paid-service case. It is the better alternative if operating and recovering client websites is unattractive. Acquisition and retention remain unverified.

## Why the tempting phone options were not selected

The cheapest technical prototype was automatic SMS after a missed call. However, Twilio's current messaging policy requires consent and distinguishes an inbound text conversation from general informational messaging. Its downstream-customer provisions also need attention. A telephone call should not simply be labelled sufficient consent without confirming the compliant flow. This is a provider requirement as well as a question of local law. [17]

A consent-based alternative is possible, but changing the call flow can change conversion and setup friction. UK phone-number onboarding also requires regulatory information and review. These points do not make all missed-call services unlawful or unworkable; they invalidate the effortless universal version of the proposal. [18]

AI receptionists avoid that particular dependency if they collect messages on inbound calls and notify the business through an agreed route. Nevertheless, Moneypenny advertises an AI service from £25/month, and Smith.ai offers both free and paid AI plans. A new £99 service needs a concrete advantage beyond being configured through AI tools. [19][20]

Retell advertises usage-priced voice infrastructure at $0.07–$0.31 per minute. That broad range, plus telephony and selected add-ons, makes configuration and usage limits material to the margin. A headline per-minute minimum is not an all-inclusive cost. Inbound voice remains a possible business, but customer trust, conversational errors and support requirements make it a weaker first choice here. [21]

## Recommendation

Choose WordPress care only if the operator is willing to own a limited support service and can execute or arrange competent recovery. Use established tools, charge for a defined service and validate acquisition before substantial custom development. Approximately fifteen to twenty £49 accounts could support the desired income under the stated allowances; neither that customer count nor the labour allowance is already proven.

Choose managed form monitoring if avoiding production changes is the higher priority, accepting a narrower proposition and direct tool competition. Both candidates have repairable design issues, but neither is certified to produce easy, consistent profit inside fourteen days. The next evidence worth acquiring is paid commitment and measured operating time, not another speculative market-size calculation.

## Sources

Sources checked on 13 September 2026. Undated pages are current offers or documentation as retrieved. Historical and vendor-published evidence is identified above. Prices may change and currencies have not been silently converted.

1. W3Techs. [Web technology surveys](https://w3techs.com/) and [WordPress usage by ranking](https://w3techs.com/technologies/breakdown/cm-WordPress/ranking). Installed-base evidence; not addressable paying-customer counts.
2. The WordPress Guy. [Care plans](https://wpguy.uk/care-plans/). Direct UK price and scope reference.
3. Pixelish. [WordPress website maintenance and care plans](https://www.pixelish.co.uk/service/care-plans/). Direct UK competitor with hosting-inclusive offers.
4. Brooks Manley / ManageWP. [How ManageWP Helped Us Scale Our Service Plans](https://managewp.com/how-managewp-helped-us-scale-our-service-plans/), 3 December 2019. Historical vendor-published agency case study.
5. ManageWP. [Pricing](https://managewp.com/pricing/) and [Client reports](https://managewp.com/features/client-report/). Component pricing and reporting automation.
6. WP Umbrella. [Pricing](https://wp-umbrella.com/pricing/). Base plan, separate add-ons and advertised integration access.
7. MainWP. [REST API overview](https://docs.mainwp.com/api-reference/rest-api/overview) and [plans](https://mainwp.com/signup/). Alternative integration foundation.
8. WordPress.org. [Plugin and theme auto-updates](https://wordpress.org/documentation/article/plugins-themes-auto-updates/). Native automation and rollback preparation.
9. ManageWP. [Safe Updates documentation](https://managewp.com/guide/safe-updates/). Documented update sequence and scheduling.
10. ManageWP. [Safe Updates now come with an automatic restore](https://managewp.com/safe-updates-now-come-with-an-automatic-restore/). Historical explanation of HTTP-error-triggered recovery; not proof of functional test coverage.
11. Stripe. [UK pricing](https://stripe.com/gb/pricing). Standard UK-card processing and separate Billing fee.
12. Information Commissioner's Office. [Business-to-business marketing](https://ico.org.uk/for-organisations/direct-marketing-and-privacy-and-electronic-communications/business-to-business-marketing/). Corporate/individual subscriber distinctions; guidance notes ongoing review.
13. CheckView. [Pricing](https://checkview.io/pricing/). Direct form-testing competitor.
14. FormStory. [Form tracking and monitoring](https://formstory.io/). Alternative form-monitoring/capture offers.
15. FormWatch. [Form monitoring](https://formwatch.app/). Delivery-monitoring alternative; some paid tiers labelled coming soon.
16. Checkly. [Pricing and browser-check explanation](https://www.checklyhq.com/pricing/). Execution model and usage-based monitoring costs.
17. Twilio. [Messaging Policy](https://www.twilio.com/en-us/legal/messaging-policy), updated 13 April 2026. Provider consent conditions.
18. Twilio. [UK KYC regulatory framework](https://help.twilio.com/articles/21038555454875-Know-Your-Customer-KYC-in-the-United-Kingdom) and [regulatory bundle submission](https://help.twilio.com/articles/8338625205147-How-to-Submit-a-Regulatory-Bundle-for-Phone-Number-Regulatory-Compliance). Phone-number onboarding dependencies.
19. Moneypenny. [AI receptionist](https://moneypenny.com/uk/ai-receptionist/). UK competitor entry pricing; full scope requires quotation.
20. Smith.ai. [AI receptionist pricing](https://smith.ai/pricing/ai-receptionist). Direct alternative; USD offers are not assumed to be equivalent UK packages.
21. Retell AI. [Pricing](https://www.retellai.com/pricing). Infrastructure costs, variable configuration and additional components.
22. QuoteFollow. [Quote follow-up service](https://quotefollow.uk/). £9/month launch offer; not proof of sales.
23. NiceJob. [Pricing](https://get.nicejob.com/pricing). Review-request automation offer; vendor conversion claims not used.
24. DDMARC. [Pricing](https://ddmarc.com/pricing/). Free and paid monitoring alternatives.

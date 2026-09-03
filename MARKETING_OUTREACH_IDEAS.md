# Marketing / Outreach Ideas — Filed, Not Live

Written Sep 3 2026 per Nick's instruction: "the letter and testimonials can be added to file but not added to business yet." Nothing below is built or running. Both ideas need a real decision from Nick (a vendor, a price, a legal read) before any of it touches the live product. The QR code piece these ideas originally leaned on has since been built independently (see `templates/partner_offer.html` + `generate_qr_codes.py`) and works on its own without either idea below going live.

## 1. Physical letter campaign (Royal Mail)

**The problem it solves**: most UK tree surgeons are sole traders with no email on file anywhere public (Companies House has nothing on them, Google Places often has no website). PECR (UK cold-marketing law) blocks cold email and cold-calling for sole traders without consent — but physical post is NOT covered by PECR at all. It's the one channel that's unambiguously legal to use cold, today, no consent needed.

**What it would look like**:
- A short, printed letter to sole-trader tree surgeons found via the Google Places sole-trader discovery scan (already live — see `research.py`'s `discover_sole_traders_via_google_places`), addressed by business name.
- A QR code on the letter linking to a landing page with a clear offer (see idea 3 below — first-lead-free ties in naturally here).
- Concrete pricing shown as a real unit price ("£19 per lead," not a vague subscription pitch) — direct-mail response is generally better with one plain number than abstract value language.

**What's still an open decision, not a build task**:
- A fulfillment vendor (Royal Mail has no self-serve small-batch API for a business Nick's size — this needs a print-and-mail service like Docmail/Powered by PCL/similar, not Royal Mail directly). Needs a price-per-letter comparison before committing to volume.
- Real cost per batch (print + postage) vs. expected conversion — worth a small test batch (50-100 letters) before scaling, not a full run first.
- Whether letters go out addressed to the business generically or to a named contact — sole-trader discovery doesn't reliably get an owner's name, only the business name.

**Legal note**: post is exempt from PECR specifically. Ordinary consumer-protection and data-protection rules (accurate claims, honest pricing, GDPR-compliant record-keeping of who was mailed and why) still apply as they would to any marketing material — nothing here is a loophole, just a channel PECR itself doesn't restrict.

## 2. Testimonials in exchange for free leads

**The idea**: offer a small number of free leads to early tree-surgeon partners in exchange for a written testimonial/review, to build social proof while the product has no track record yet.

**Legal status (researched this session, confirmed via the CMA's own CMA208 guidance)**: this is legal under UK law provided three things hold —
1. The incentive is clearly disclosed (the review must say or show it was given in exchange for something, same as any sponsored content).
2. The review reflects the partner's genuine experience — it can't be scripted, edited to remove criticism, or written on their behalf.
3. Negative reviews aren't suppressed or cherry-picked out — if a partner's honest experience is mixed, that has to be allowed to show.

Enforced under the Digital Markets, Competition and Consumers Act (DMCCA), with the CMA actively investigating fake/incentivized-review practices as of 2026. The practice itself is fine; the trap is treating it as a way to manufacture positive-only reviews — that crosses into the kind of "genuine reviews" violation the CMA is actively pursuing right now.

**What's still an open decision, not a build task**:
- How many free leads per testimonial, and to how many partners total (a cost decision — free leads have real production cost even though marginal cost is near zero).
- Where the testimonials would actually go (the TreeKey site currently has no testimonials section built).
- A simple written disclosure line partners would need to include or that TreeKey would display alongside the quote (e.g. "given a free lead in exchange for this review") to stay clearly inside CMA208's disclosure requirement.

## 3. QR code (built independently, not blocked on the above)

See `templates/partner_offer.html` (landing page) and `generate_qr_codes.py` (image generator) — live and usable today on printed material (business cards, a stand at a trade event, an email signature) without needing either idea above to launch first. Each QR encodes a `?src=<campaign-code>` tag so response can be tracked per batch/channel once real campaigns (including a future letter run) start using it.

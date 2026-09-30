# TreeKey outreach — START HERE

Current campaign-copy index — 30 September 2026.
These are copy documents, not a deployed campaign or confirmation of launch readiness.
Business rules behind this copy: `CURRENT_BUSINESS_MODEL.md`.

## Authoritative saved follow-up copy

- Email 2 A/B: TREEKEY_EMAIL_2_VARIANTS.md
- Email 3 A/B: TREEKEY_EMAIL_3_VARIANTS.md
- Email 1: discussed in the current chat; this cleanup does not select or finalise its A/B pair. Confirm that pair before creating the campaign.

Use these follow-up files for the new postal-introduction campaign. Old AI outputs, general marketing ideas, audit notes and deployment ZIPs are not approved outreach copy. If wording changes, edit the authoritative file instead of creating competing FINAL/CURRENT copies.

CTA: https://treekey.co.uk/marketplace
Checked anonymously on 30 September 2026: HTTP 200 with opportunity listings and search, no login credentials used. https://treekey.uk/marketplace redirects here. This checks public browsing, not checkout or the £4.99 offer.

## Campaign setup

1. Confirm the agreed £4.99 offer and advertised service are implemented before sending.
2. Nick confirmed on 30 September 2026 that no emails have been loaded into Instantly. Create the campaign there using the approved versions; there is no known existing sequence to replace. Saving these files does not upload anything to Instantly.
3. Use the same thread for follow-ups; select one A/B version per recipient at each step, not both versions as consecutive emails.
4. Configure and test reply opt-out blocking/unsubscribe handling before sending. Review differently worded removal requests too. Keep suppression effective for future imports/campaigns.
5. End after Email 3; exclude completed recipients from future campaigns repeating this pitch to honour the last-email promise.
6. Test the actual sequence using only your own addresses before enabling a customer audience.

## Existing application emails — separate follow-up required

A targeted read found notifications.py contains send_cold_email_1, whose documentation references the superseded old sequence. Its current behaviour and whether it is enabled were not audited here. Do not assume deleting draft documents updates, disables or replaces application-generated emails. Before using that sender, check its actual copy and suppression behaviour against the agreed campaign. No application code was changed in this cleanup.

## Cleanup boundaries

Superseded COLD_EMAIL_SEQUENCE.md content has been replaced with a pointer here. Removed the old Claude outputs/TreeKey_Cold_Email_Sequence_CURRENT.txt and Claude outputs/email_drafts_preview.html from the working folder after verified backup outside the repository.
Support correspondence, general idea documents, audit records, application code, Git history and deployment ZIPs are preserved. They can contain historical email text; do not use it as current campaign copy.


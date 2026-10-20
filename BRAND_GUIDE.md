# TreeKey brand guide: where to change things

(Pages/web theme: see `STYLING_GUIDE.md`. This file covers emails, letters, flyer/quote, icons, share links.)

| I want to change... | Edit here |
|---|---|
| Brand colours for emails, print documents, icons; tagline; footer wording; icon file names | `brand.py` |
| Look of every customer email (header, footer, buttons, boxes) | `email_layout.py` |
| Look of the street flyer and storm quote | `document_print.py` (layout) and `generate_street_flyer` / `generate_storm_quote` in `main.py` (wording) |
| Look of the printed letter | `letter_content.py`, the `BRAND_*` constants and the CSS near the `.brand-header` rules. Wording is not touched by styling. |
| Crest, phone/browser icons, share image, launch screens | Replace `assets/brand/treekey-crest-master.png`, run `python tools/build_brand_assets.py`, then raise `ASSET_VERSION` in `brand.py` (and the `?v=` in `static/manifest.json`, and `CACHE_NAME` in `/sw.js` in `main.py`). |
| Share-link title/description/image | `brand.py` (`OG_*`). Pages that already have their own tags keep them; only the image URL is updated. |
| Check icons/manifest/tags are still valid | `python tools/brand_check.py` |
| Turn the icon/meta rewrite off in an emergency | Set `TREEKEY_BRAND_HEAD=off` |

Email wording lives next to the code that sends each email (`notifications.py`, and the sign-in email in `main.py`). `email_layout.py` only controls how it looks.

## Pre-launch checklist (not resolved by this work)

1. Outreach wording is not final. The approved copy is `TREEKEY_EMAIL_1/2/3` (sent through Instantly). `send_cold_email_1` in `notifications.py` is the retired old wording, restyled only.
2. The "Nick" sender identity is to be replaced. The only code places: `OUTREACH_SIGNATURE_*` in `email_layout.py` and the sender label in `send_cold_email_1`. Replacement name/address not chosen.
3. Flyer claims and the storm quote's "Exempt from Standard 6-Week Council Wait" wording need a content review.
4. Preview example phone numbers and privacy links were not copied; production values are used.
5. Not yet tested on real devices or in real email clients (Gmail, Outlook, Apple Mail, iPhone, Android).
6. The approved welcome email design exists in `email_layout.py` but no code sends a welcome email, so none was wired.

## Release, rollback and refreshing the phone app

* **Release:** double-click `DEPLOY_BRAND_RELEASE.bat`. It sends only the approved files (not `git add .`), tags the live version as `pre-brand-release-20261020`, shows what will be sent, and waits for a key press. It records the release commit in `brand_release_commit.txt`.
* **Rollback:** double-click `ROLLBACK_BRAND_RELEASE.bat` (adds a revert commit; no history rewriting). Original files are also in `brand-backups-2026-10-20`.
* **Phone app already installed:** the new service worker (`treekey-v3`) replaces the old cache on the next online visit. If the old icon still shows: Android: remove the app from the home screen and choose "Install app"/"Add to Home screen" again from Chrome. iPhone: delete the home-screen icon, open treekey.co.uk in Safari, Share, "Add to Home Screen". (iPhone caches the icon and launch screen hard, so reinstalling is the reliable way.)

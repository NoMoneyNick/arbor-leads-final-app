# TreeKey / Vector Data Labs — Full Handover
*Written 8 Sep 2026 for a fresh Claude chat. Paste this whole file as your first message.*

## 0. Read this first
Nick is **not a coder**. Give short, direct, plain-English answers — no long technical paragraphs unless he's asked for depth (like this doc). He's often under time pressure. Code work itself can take as long as needed to get right, but *explanations* to him must be short.

Standing rules Nick has given, carried forward indefinitely:
- **Fix every bug/concern you find without waiting for permission**, as long as it's a code-level fix. Don't ask first — just fix it, test it, sync it, then tell him.
- **Never guess at unverified schemas/URLs/API behaviour.** Verify live (WebFetch/WebSearch/actual API calls) before shipping a fix. If you can't verify something yourself, write Nick a ready-to-paste question he can run through another AI (he uses ChatGPT, Gemini, Google AI Studio, Antigravity, Cursor, and ClaudeAI besides this).
- **Sync every fix to Nick's real OneDrive disk before considering it done.** See §5 for the exact mechanism — don't skip this or the fix only exists in your sandbox.
- **Remind Nick of anything he asks you to do** — he wants open asks tracked across sessions so nothing falls through.
- Never bypass bot-detection/CAPTCHAs. Never enter his credentials, passwords, or regenerate his API keys/tokens — tell him exactly what to do and let him do it himself.
- Scheduled/recurring reminders must use the real scheduled-task tools (`mcp__claude-code-remote__create_trigger` etc.), never any session-local cron tool — those die when the session ends and silently never fire.
- Answer simple/quick questions fast without over-deliberating; only slow down for actual fixes/investigation.

## 0b. About Nick — how to get the best results working with him

**Who he is**: not a coder — every line of code in this project was written by AI on his direction. He also uses ChatGPT, Gemini, Google AI Studio, Antigravity, and Cursor alongside Claude, sometimes on this same codebase. This is very likely *why* the main.py revert in §8 happened — treat "another AI tool may have touched this file outside the current chat" as a real, recurring risk on this project, not a one-off fluke.

**His stated preferences** (apply these by default):
- Wants short, direct, simple answers, not long paragraphs — he's often working under time pressure. This applies to normal conversational replies. It does **not** apply when he explicitly asks for a detailed document (like this handover) — there, thoroughness wins over brevity, and he has explicitly said so twice.
- Wants fast answers to quick/simple questions without over-deliberating. Save real investigation time for actual fixes, not for something he could get a quick answer to.
- Wants to be reminded of anything he's asked Claude to do, so open items don't get lost between sessions/messages — keep a running punch list rather than letting an ask evaporate once it's been touched on once.
- When something needs external verification Claude genuinely can't do itself (e.g. a third-party API's undocumented behaviour), he wants a ready-to-paste question handed to him so he can run it through another AI — not a guess, and not silence.

**Patterns that work well in practice** (my own observations from working this project with him, not things he's stated outright — flag them as such if you're unsure they still hold):
- He often sends one long compound message covering several unrelated bugs, questions, and feature ideas at once. Triage it into a punch list and work through it methodically rather than trying to answer everything at once in one dense reply.
- When a request is genuinely ambiguous (what exactly a feature should do, what a page is for), asking one direct clarifying question gets a precise, concrete, usable answer back — he tends to respond with a full real spec once asked directly. Guessing instead of asking has cost more (a scrapped feature, a wrong assumption) than the small delay of asking ever has.
- He responds well to blunt, honest answers about limitations — e.g. being told a fix was silently undone, or that a fresh chat won't be 100% as sharp as an ongoing one without doing some homework first. He'd rather hear the real limitation than get reassured.
- Fixing things proactively without waiting for a go-ahead, then reporting afterward, has consistently been the right call — especially bugs found by accident while working on something else, and especially anything production-breaking. Don't sit on a found bug waiting for permission to fix it.
- Because other AI tools may be editing these same live files outside of any given chat, always re-check a file's actual current state (size/mtime) immediately before overwriting it — never trust that what you last synced is still what's there.

## 1. What the business is
**Vector Data Labs** (working umbrella name, not finalised/incorporated) is Nick's lead-gen SaaS. Its first product, **TreeKey** (domain **treekey.uk**, registered as a UK Individual — not yet a Ltd company), scans UK council planning-application data nationwide, finds tree-work-relevant jobs (fellings, TPO work, crown reduction, BS5837 arboricultural survey requirements etc.), and sells those leads to tree surgeons and arboricultural consultants via subscription.

Key business facts:
- Built in ~19 days using mostly free/cheap LLMs (Google Flash, AI Studio) plus Claude for harder work. Not a coder — every line of code in this repo was written by AI on his instruction.
- **Coverage: nationwide** (whole of England), not just the originally-scaffolded 6 cities. Uses multiple council data sources (ArcGIS-based scanners, GLA Datahub for London, PlanIt aggregator, direct council paid APIs) — see `scanners.py` / `mesh_scrapers.py`.
- **Pivot history**: domestic homeowner TPO leads were found to be weak (the tree job is often already agreed before the planning application ever appears), so the primary target shifted to **commercial arboricultural applications and arb consultants (BS5837 survey leads)**, with small operators as a secondary "you didn't know this tool existed" education-sales target.
- **Pricing** (see `payments.py` → `PLANS` dict for the live source of truth, verify before quoting): tiers include stump_pro £29, climber_domestic £49, arb_consultant £89, commercial_forestry £139, treekey_elite £179, sole_trader £49, commercial_pro £149, regional_elite £299, plus one-off single-lead purchases £19/£29/£49 (small/medium/large). **There is no per-tier lead-count/quota field anywhere in the code** — any marketing copy claiming "X leads per month" per tier is an unverified/unbacked number, flag it if you see it.
- **Competitors named by Nick**: BuildAlert, Planning Pipe, PlanAPI — all target builders, not tree surgeons. TreeKey's differentiators per Nick: leads sold exclusively (never resold), plus Companies House director-name enrichment on prospects.
- Has a Companies House–based prospect enrichment pipeline (director names, phone, email, website, Google rating) with CSV export, and a `/clean-partners` route to purge irrelevant businesses from the DB.
- **Status as of this handover**: technically live, deployed, Stripe checkout+webhook confirmed working live — but effectively **zero real paying customers**, because no outreach/marketing has actually reached buyers yet. "Live" is nominal, not yet commercially proven.
- Go-live date of "8 Sept 2026" was mentioned by Nick earlier but hasn't been confirmed/re-set since; treat it as soft.

### Adjacent future projects (not started, for context only — don't start work on these unprompted)
- **AI reusable tool library**: Nick wants a library of general-purpose "elite-level" components (e.g. entity_graph, workflow_core, escrow_engine style tools) built with Claude, reused across projects rather than rebuilt each time. Reference/reuse this library's existing pieces when relevant.
- **Trading agent** (next project after TreeKey): autonomous stock-trading agent, evolutionary/multi-agent design (10 agents run in parallel, top 3 cloned, bottom 7 culled, repeat), watching hundreds of tickers at once, reusing TreeKey's scraper/crawler skills for signal-gathering, trained via paper trading (e.g. Alpaca) before any real capital. A sports-betting variant was floated as a possible future spin-off. Not started.

## 2. Where everything lives
- **GitHub repo**: `NoMoneyNick/arbor-leads-final-app`.
- **Local/live folder** (synced via OneDrive on Nick's PC): `C:\Users\twobo.DESKTOP-DI088K1\OneDrive\Documents\VECTOR DATA LABS`. This is the **actual live codebase** — always work against this, not assumptions.
- **Hosting**: Render. Deploy flow is nominally `git add / commit / push` (there are `deploy.bat`, `UPDATE_WEBSITE.bat`, `FIX_GIT_PUSH.bat` scripts in the folder for this). **Caution**: the local `.git` reflog stops dead at **2 Sep 2026 ~15:04** even though files have been edited well past that date — meaning recent work (mid-Sep onward) has likely NOT been committed/pushed to GitHub/Render via the normal flow. Don't assume "synced to OneDrive" means "deployed" — those are two different steps. Worth clarifying with Nick whether he's still using the .bat-script deploy flow at all.
- Also present in the repo root: `PROJECT_STATE.md`, `AI_HANDOFF.md`, `MANIFEST.md` — other docs Nick/other AI sessions have written over time. **Check these too**, they may be more current than this handover on business-status details, and this handover doesn't attempt to duplicate their full content.
- **This session's sandbox mechanics** (only relevant if you're a cloud Claude session with the remote-devices bridge, same as this one): files staged from OneDrive land under `/mnt/user-data/uploads/VECTOR DATA LABS/`; anything you build to send back goes in `/mnt/user-data/outputs/`. Neither of those paths mean anything to Nick — never mention them to him.
- There's a 112MB `tailwindcss.exe` sitting in the repo root — don't try to read/copy/diff it, it's a build tool binary, not source.

## 3. Codebase map
Core modules (all plain Python, FastAPI app):
- **`main.py`** (~360KB) — the FastAPI app itself: all routes, HTML rendering (inline f-strings, no template engine for most pages), auth flow, admin pages. This is the single biggest and most-edited file.
- **`database.py`** (~156KB) — all DB access (Postgres via psycopg-style connection, see `get_db_conn()`), schema init (`init_db()`), and all business-logic helper functions (lead matching, geo lookups, account management).
- **`scanners.py`** (~169KB) — per-council/per-source scanning functions, one function per data source (e.g. `scan_gla_datahub_london()`), each normalizing to a common lead record shape and inserting via `database.py`.
- **`mesh_scrapers.py`** (~158KB) — broader scraping/classification logic (vertical classification — tree vs HMO vs other application types, agent/tree-surgeon detection heuristics, tag generation).
- **`research.py`** (~118KB) — Companies House enrichment, business-kind guessing, prospect research pipeline.
- **`notifications.py`** — all outbound email (via Resend), plus the two-tier WARNING/CRITICAL internal incident-alert system (`send_system_incident_alert()`).
- **`payments.py`** — Stripe integration, `PLANS` dict (pricing source of truth), checkout/webhook handling.
- **`net_utils.py`** — shared HTTP helpers (`smart_get`, `smart_post` — retry/timeout wrappers used by every scanner).
- **`persistent_dedup_cache.py`** — cross-run dedup so the same planning application isn't re-ingested as a new lead.
- **`domestic_scrapers.py`, `bulk_contractor_extractor.py`, `generate_qr_codes.py`, `backfill_agent_is_tree_surgeon.py`** — supporting/one-off scripts.
- **Static assets**: `static/manifest.json` (PWA manifest, `start_url: "/dashboard"`, `display: "standalone"`) + a registered service worker (`/sw.js`), linked in `<head>` at 3 places in main.py. **TreeKey is already a real installable PWA** ("Add to Home Screen") — there is no separate native app, and none is needed for what's built.

### Test suite
`test_main.py`, `test_database.py`, `test_notifications.py`, `test_scrapers.py`, `test_payments.py`, `test_research.py`, `test_bulk_contractor_extractor.py`, `test_idox.py` (near-empty). **Run the full suite** with:
```
python3 -m unittest discover -p "test_*.py" -v
```
As of this handover: **464 tests, all passing**, when run against the full current set of files together (individual files can pass/fail differently in isolation — see gotcha below).

**Critical test-infrastructure gotchas** (these have caused real bugs in test coverage before — read before editing tests):
1. `test_main.py`, `test_scrapers.py`, `test_payments.py` stub `sys.modules["database"]` / `sys.modules["notifications"]` as a plain `types.ModuleType` (not a MagicMock), then manually attach individual `MagicMock()` attributes. **Any function you call via `patch(...)`/`patch.object(...)` must already have a pre-declared attribute in that file's stub setup block**, or you get an `AttributeError` at test-run time, not at the call site — easy to misdiagnose.
2. `test_database.py` / `test_notifications.py` instead load the **real** module fresh via `importlib.util.spec_from_file_location` under a private name — specifically to avoid inheriting another test file's stale stub of the same module name. This is documented in both files' own docstrings.
3. Because the `sys.modules["database"]`/`["notifications"]` stubs are **process-wide singletons** shared across test files when run together via `unittest discover`, **MagicMock call-counts can leak between test classes/files** unless you add a `setUp()` that calls `.reset_mock(side_effect=True)` (and resets `.return_value`) on every shared mock before each test. This has bitten real test runs twice already (see §6).
4. There's a lightweight fake `RedirectResponse` class inside `test_main.py`'s FastAPI stub — if you need to assert on `response.url`/`.status_code`/cookies set via `set_cookie()`, make sure that fake class actually stores them (it didn't originally; was fixed once already).
5. Always run the **full** suite after any change, not just the file you touched — several real bugs (shape mismatches, cross-file mock pollution, unrealistic test fixtures) were only caught this way.

## 4. Key technical mechanisms (verified, don't re-derive from scratch)

**GLA Planning Datahub (London) — correct, verified-live integration**:
- Endpoint: `POST https://planningdata.london.gov.uk/api-guest/applications/_search`
- **No Authorization header needed** for guest access (confirmed live with zero auth headers — real data returned). There's a documented but non-secret courtesy header `X-API-AllowRequest: be2rmRnt&` — not a secret key, don't treat it as one.
- Response is a **standard Elasticsearch v7.9 envelope**: `{"hits": {"hits": [{"_source": {...}}]}}` — NOT a flat `{"data": [...]}` shape. If you ever see code assuming the flat shape again, it's wrong — this was a real bug fixed once already (an old incident alert wrongly blamed an "expired API key" when the real problem was wrong endpoint + wrong auth assumption + wrong response parsing).

**Auth / login flow**:
- Passwordless magic-link login. `database.create_magic_auth_token(email)` / `database.verify_magic_auth_token(token, otp, email)`, backed by `contractor_auth_tokens` table.
- **The login form always treats the submitted field as an email.** There is no SMS/phone channel actually implemented anywhere, despite old copy implying "email or phone" — that copy was corrected to just say "email."
- Session cookies are signed HMAC-SHA256 over base64(lowercased email) via `_sign_session_cookie(email)` / `_verify_session_cookie(cookie)` in `main.py`, keyed by `_SESSION_SECRET`. This same mechanism is reused for the unsubscribe-link token in the new teaser-email feature (§7) — don't invent a second token scheme.

**Account model — two separate tables, do not conflate**:
- `contractor_subscriptions` — the **paying** account table, keyed by `customer_email`, `active` boolean = "currently has a live Stripe subscription." This is the pre-existing table with real quota/seniority/cancellation logic tied to it.
- `limbo_accounts` (NEW, built 5-8 Sep 2026) — **free, no-payment** signup accounts. Deliberately a separate table so "never paid" isn't conflated with "used to pay, lapsed." See §7 for the full feature.

**Lead lifecycle**: `leads.status` column: `'new'`/NULL = available, `'claimed'` = permanently burned/sold.
- `burn_lead_inventory(lead_id, buyer_email)` — simple one-off burn+return, used by the free-signup grant.
- `record_lead_dispatch_and_burn(...)` — separate, subscriber-quota-aware burn function used for real paying dispatch. Don't mix these two up.

**Geo-matching pattern** (reused everywhere a free-text UK address needs a location):
1. Regex-extract a UK outcode: `r'\b([A-Z]{1,2}[0-9][A-Z0-9]?)\s*([0-9][A-Z]{2})\b'`
2. Resolve outcode centroid: `database.lookup_outcode_centroid(outcode)` (hits postcodes.io `/outcodes/{outcode}`)
3. Distance: `database.haversine_miles(lat1, lon1, lat2, lon2)`

**Cron-triggered routes convention**: every `/trigger-*` route is gated by `verify_cron_secret(secret)`, which checks against the `TRIGGER_SECRET` env var via `secrets.compare_digest`. These routes are hit externally by **cron-job.org** (an external free cron service Nick uses — not this session's scheduler). Follow this exact convention for any new scheduled route.

## 5. How to sync a file back to Nick's real OneDrive (do this every time, don't skip)
This is only relevant if you're a cloud session with the remote-devices bridge (same setup as this handover was written in). Steps, in order, every time:
1. Edit the file in your sandbox working copy.
2. **Before writing back**, run `device_list_dir` on the OneDrive folder to get the file's *current* `mtimeMs` — the commit step will reject a stale mtime, which is exactly the safety net that once caught an external change to `main.py` that would otherwise have been silently overwritten (see §8).
3. `SendUserFile` on your edited file to get a `file_uuid`.
4. `device_commit_files` with that `file_uuid`, the exact Windows `devicePath` (`C:\Users\twobo.DESKTOP-DI088K1\OneDrive\Documents\VECTOR DATA LABS\<filename>`), and `expectedMtimeMs` from step 2.
5. If it's rejected for a stale mtime: **stop, re-fetch the current file, diff it against your edit, and figure out what changed externally before overwriting** — do not force it blindly. See §8 for exactly this scenario happening.
6. After committing, re-list the directory to confirm the new size/mtime landed, and ideally re-run the test suite against the fresh state of *all* files (not just the one you changed) before telling Nick it's done.

**Always run the full test suite against the real current versions of every other file on OneDrive before syncing** (stage the others fresh, don't rely on stale cached copies) — this is what caught a previous full silent revert.

## 6. Errors already hit and fixed (don't repeat these)
- Test fixtures used fake postcodes like "FAR1"/"NEAR1" that don't match the real UK outcode regex shape (letter(s)-then-digit) — always use realistic fake outcodes (e.g. "ZZ99", "YY11") in geo-matching tests.
- Missing pre-declared stub attributes causing `AttributeError` inside `patch()` calls — see §3 gotcha #1.
- The fake `RedirectResponse` in `test_main.py`'s FastAPI stub originally discarded all constructor args — fixed to store `url`/`status_code` and support `set_cookie()`.
- Cross-test-file MagicMock call-count leakage via the shared `sys.modules` stub — fixed with `setUp()` resets (§3 gotcha #3).
- **The single biggest bug found and fixed**: `notifications.send_resend_email(subject, html_body)` hardcodes recipient to `TEST_EMAIL` (Nick's own address) — correct for internal admin/incident alerts, but `main.py`'s `request_magic_link()` was wrongly calling this exact function for the **customer-facing login email**. Result: every real contractor's login-link email was silently going to Nick's inbox instead of theirs — **the entire contractor login system was broken for everyone except Nick**. Fixed by adding `notifications.send_transactional_email(to_email, subject, html_body, from_label=...)` (explicit recipient) and switching the login call site to use it. All 4 other `send_resend_email` call sites were checked and are legitimately admin-only — left alone. **This fix has already been silently undone once by an external file change and had to be re-applied — see §8. If you ever see `request_magic_link()` calling `send_resend_email` again, that's this same regression back — fix it immediately, it's a critical/urgent production bug every time.**

## 7. The "limbo account" free-lead feature (built 5-8 Sep 2026, now live and synced)
Nick's verbatim spec: *"we should have a sort of limbo account, where you sign up for a free account even without a subscription, just login details and normal account sign up details. then you get your free lead. once we have them signed up without a subscription, we can then send them emails once or twice a week with specific leads in their area (best we currently have) without the finer details of the address viewable (blurred out or something) but the job details viewable and date it was applied (has to be super recent) as a sales prompt."*

What was built, exactly per that spec:
- **`database.py`**: new `limbo_accounts` table + indexes; `_extract_outcodes()`, `find_nearest_unclaimed_lead(lat, lon, max_miles=25.0, exclude_refs=None)`, `create_or_update_limbo_account(...)`, `get_limbo_account(email)`, `record_free_lead_grant(email, lead_ref)`, `get_limbo_accounts_due_for_teaser(min_hours_since_last=72.0)`, `mark_teaser_sent(email, lead_ref=None)`, `set_limbo_account_unsubscribed(email)`, `get_lead_by_reference(reference)`.
- **`notifications.py`**: `_blur_address_to_area(address)` (returns e.g. "Somewhere in the NG22 area — exact address unlocks with a subscription"), `send_free_account_welcome_email()`, `send_teaser_lead_email()`, `send_teaser_email_batch(min_hours_since_last=72.0, unsubscribe_url_builder=None)`.
- **`main.py`** new routes:
  - `GET /free-account` — signup form (name, email, phone optional, postcode/area).
  - `POST /api/free-signup` — validates, resolves postcode → lat/lon, creates/updates the limbo account, grants **one** free unclaimed lead within 25 miles via `burn_lead_inventory` + `record_free_lead_grant`, sends the welcome email, sets the session cookie, redirects to `/free-dashboard`.
  - `GET /free-dashboard` — shows the granted lead (or a "we'll email you the first nearby one" message if none was available at signup); paying subscribers hitting this URL get redirected to the real `/dashboard`.
  - `GET /unsubscribe-teaser?token=...` — verifies the signed token, calls `set_limbo_account_unsubscribed`.
  - `GET /trigger-teaser-emails?secret=...` — cron-gated (§4 convention), calls `notifications.send_teaser_email_batch`. **Intended schedule: 2-3x/week via cron-job.org. This is NOT yet actually scheduled anywhere externally — Nick needs to set this up on cron-job.org himself when ready to go live with the feature.**
  - `verify_login()` was extended: paying subscribers → `/dashboard` (unchanged); limbo accounts → `/free-dashboard` (new); unknown → `/pricing?msg=no_subscription` (unchanged).
  - New homepage CTA button: "Get a Free Lead — No Card Needed" linking to `/free-account`.
- Also added: 23 new tests across `test_main.py`/`test_database.py`/`test_notifications.py`.
- **This is fully built, unit-tested (464 tests passing), and synced to OneDrive as of 8 Sep 2026.** It has NOT been deployed to Render/production yet (see §2 deploy caveat) and the teaser-email cron is not yet scheduled externally. Nick has not yet reviewed/approved the actual page wording.

## 8. UNRESOLVED — main.py got silently reverted once; watch for it happening again
On 8 Sep 2026, while about to sync the finished limbo-account feature, a routine freshness check found the **live `main.py` on OneDrive had reverted to an old version** — smaller, older mtime, missing: the critical login-bug fix (§6), all recent nav/spacing/CTA fixes, and the entire limbo-account feature. It was missing content from as far back as *before* 2 Sep 2026 fixes (a `KNOWN_TAG_VALUES` consolidation with dated comments).

At the same time, two new files appeared on OneDrive that were **not created by any Claude session in this conversation**: `test_admin.py` and `test_admin3.py` — small, UTF-16-encoded, ad-hoc scripts running raw SQL count queries directly against the production DB (`SELECT count(*) FROM potential_partners`, `SELECT count(*) FROM leads`, etc.), created 1-2 minutes before main.py's revert timestamp.

Investigation so far: the local `.git` reflog stops dead at 2 Sep 2026 ~15:04 (no `checkout`/`reset` logged near the revert time), so **it wasn't caused by a git command in that repo**. Leading theory (unconfirmed): another AI session/tool had a stale cached copy of `main.py` from before 2 Sep and wrote it back wholesale while doing something else (possibly whatever produced `test_admin.py`/`test_admin3.py`). **This was NOT investigated to a firm conclusion — ask Nick directly**: did he restore an old backup, run any git command, or have another AI tool (ChatGPT/Gemini/Cursor/Antigravity) touch this folder around 8 Sep? The fix was safely re-applied and re-synced (confirmed: no legitimate new external work was lost, the revert was a strict downgrade), but **the root cause is still open** and could recur.

## 9. Punch list / open items Nick has raised, not yet done
- Marketplace page rework/relocation.
- A full FAQ page.
- Longer/more formal Terms of Service and Privacy Policy — drafts already exist in the repo (`terms_and_conditions_draft.md`, `privacy_policy_draft.md`) but Nick wants them more thorough/formal.
- Animations: storm-radar "lightning" effect, a "key into keyhole" visual concept for the brand.
- Testimonials section (for later, once there are real customers).
- Confirm ICO registration (data-protection registration, ~£40 fee per `PROJECT_STATE.md`) — a scheduled reminder about this fired and was relayed to Nick; **no confirmation received yet** that he's done it.
- Confirm credential rotation for: `STRIPE_SECRET_KEY`, `STRIPE_WEBHOOK_SECRET`, `SUPABASE_DB_URL`, `GOOGLE_MAPS_KEY`, `DASHBOARD_USER`, `DASHBOARD_PASS` — a scheduled reminder fired and was relayed; **no confirmation received yet**.
- Schedule `/trigger-teaser-emails` on cron-job.org (2-3x/week) once ready to go live with the free-lead feature (§7).
- Deploy the recent `main.py`/`database.py`/`notifications.py` changes to Render — confirm whether the `.bat`-script deploy flow is still actually being run, given the git reflog gap since 2 Sep.

## 10. Other live context worth knowing
- **`COLD_EMAIL_SEQUENCE.md`** in the repo has two cold-outreach drafts: a "V1" and a "V2" (V2 attributed to "Opus 4.6," rewritten with inline lead details and exclusivity framing). V2's second email makes explicit lead-volume claims ("3/5/12 leads per tier per month") — **this is an unverified number with no backing quota system in the code** (checked against `payments.py`'s `PLANS` dict directly). Flag this to Nick before it goes out if asked to review/send either sequence.
- **"Ledger"** (the `/ledger` nav tab) is a real, already-built financial tool for arborists — not a stray/broken feature. Nick had forgotten its origin; it came from an idea he got from Gemini Pro when he asked it to suggest free tools useful to tree surgeons.
- **WhatsApp**: the current implementation only uses `wa.me` click-to-chat deep links (opens the *clicking user's own* WhatsApp app with a pre-filled message) — this needs **no business number or SIM card from Nick at all**. A true WhatsApp Business Platform/Cloud API integration (not currently built) would need a dedicated number, but per Meta's own docs, **no physical SIM** is required even then — a VOIP number works via voice verification. Only relevant if Nick asks about upgrading WhatsApp integration.
- **PWA**: TreeKey is already a real, working installable web-app (`static/manifest.json` + `/sw.js`) — "where is the app?" is already answered; there's no separate native app and building one isn't necessary for what exists today.

## 11. Scheduled reminders currently active (via the real trigger system, not session-local)
- `trig_01URjWgsNmTBYpvFTqBZZsSt` — "Remind Nick: license before TreeKey go-live" (ICO registration/£40 fee). Has fired at least once; awaiting Nick's confirmation.
- `trig_018Duf2ww6fEZ2Z9YtHbZF65` — "Remind Nick: rotate credentials before cold email send" (the 6 credentials listed in §9). Has fired at least once; awaiting Nick's confirmation.
If a fresh session needs to check/update/cancel these, use `mcp__claude-code-remote__list_triggers` / `update_trigger` / `delete_trigger` — never a local cron tool.

---
*End of handover. If anything here conflicts with what you find live in the code or what Nick tells you directly, trust the live code/Nick over this document — this is a snapshot as of 8 Sep 2026, not a live source of truth.*

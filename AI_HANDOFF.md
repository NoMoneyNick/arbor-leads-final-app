# 🤖 AI HANDOFF DOCUMENT — TreeKey / Arbor Leads Codebase
**For:** Google Gemini Pro (or next AI taking over)  
**From:** Claude Sonnet 4.6 (Thinking)  
**Repo:** `c:\Users\twobo.DESKTOP-DI088K1\OneDrive\Documents\VECTOR DATA LABS`  
**GitHub:** `https://github.com/NoMoneyNick/arbor-leads-final-app`  
**Live URL:** `https://treekey.uk`  
**Deploy:** `git push` → Render auto-deploys. No manual step needed.  
**Last commit before handoff:** `044cbf7` (pre-checkout area selector)

---

## WHAT THIS PROJECT IS

TreeKey is a lead generation SaaS for UK tree surgeons. It scrapes council planning applications (TPO/S211 notices), scores them, and sells them to tree surgeon contractors via:
- **Subscriptions** (£29–£179/month) — leads auto-dispatched to their postcode area
- **Pay-per-lead marketplace** (£19–£49) — one-off lead purchases, lead burned after sale

**Key files:**
- `main.py` — FastAPI app, all routes (4200+ lines)
- `database.py` — All DB queries, lead freshness, subscription logic (~1355 lines)
- `payments.py` — Stripe checkout + webhook (~258 lines)
- `notifications.py` — Email dispatch, lead routing (~453 lines)
- `domestic_scrapers.py` — Domestic job scrapers (~677 lines)
- `scanners.py` — Council planning portal scrapers

**DB:** Supabase PostgreSQL. Connection via `SURL = os.getenv("SUPABASE_DB_URL")`.  
**Auth pattern:** Admin routes use `verify_admin_or_secret()`. Cron routes use `verify_cron_secret()`.

---

## 5 TASKS TO IMPLEMENT (IN ORDER)

---

### TASK 1 — Geographic Radius Matching

**Problem:** `dispatch_lead_alerts()` in `notifications.py` matches subscribers to leads using exact outcode string (`sub_outcode in extracted_outcodes`). A contractor in `NG22` with a 30-mile radius misses a lead in `NG1` (nearby). The `radius_miles` column exists in `contractor_subscriptions` but is never used.

**Solution — 3-tier matching priority:**
1. Exact outcode match (address literally contains their outcode string)
2. Haversine distance check (any outcode in the address within their radius)
3. Regional prefix fallback (same alphabetic prefix e.g. `NG`)

**Step 1 — Add lat/lon columns to DB** (in `database.py` `init_db()`, after other ALTER TABLE blocks, around line 260):
```python
cur.execute("""
    ALTER TABLE contractor_subscriptions
    ADD COLUMN IF NOT EXISTS lat FLOAT,
    ADD COLUMN IF NOT EXISTS lon FLOAT;
""")
```

**Step 2 — Add postcodes.io lookup helper** (add to `database.py` after `reset_monthly_quotas_if_needed()`):
```python
import math

def lookup_outcode_centroid(outcode: str) -> tuple:
    """
    Looks up lat/lon centroid for a UK outcode using the free postcodes.io API.
    Returns (lat, lon) or (None, None) if not found.
    """
    try:
        clean = outcode.strip().upper().replace(" ", "")
        resp = requests.get(
            f"https://api.postcodes.io/outcodes/{clean}",
            timeout=5
        )
        if resp.status_code == 200:
            result = resp.json().get("result", {})
            lat = result.get("latitude")
            lon = result.get("longitude")
            if lat and lon:
                return (float(lat), float(lon))
    except Exception as e:
        logger.warning(f"[postcodes.io] Failed lookup for {outcode}: {e}")
    return (None, None)


def haversine_miles(lat1, lon1, lat2, lon2) -> float:
    """Returns distance in miles between two lat/lon points."""
    R = 3958.8  # Earth radius in miles
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = math.sin(dphi/2)**2 + math.cos(phi1)*math.cos(phi2)*math.sin(dlambda/2)**2
    return 2 * R * math.asin(math.sqrt(a))
```

**Step 3 — Update `register_or_update_subscription()`** (around line 590 in `database.py`):
- Call `lookup_outcode_centroid(outcode)` to get lat/lon
- Pass lat, lon into the INSERT/UPDATE
- Current signature: `def register_or_update_subscription(customer_email, outcode, tier, stripe_sub_id, radius, name, phone)`
- Add `lat, lon` to the SQL: `lat = %s, lon = %s` in the UPDATE SET and VALUES

Also set `monthly_quota` based on tier at registration:
```python
TIER_QUOTAS = {
    "stump_pro": 3,
    "climber_domestic": 5,
    "arb_consultant": 8,
    "commercial_forestry": 12,
    "elite": 18,
}
quota = TIER_QUOTAS.get(tier, 5)
```
Add `monthly_quota = %s` to the SQL INSERT and UPDATE SET.

**Step 4 — Rewrite matching in `dispatch_lead_alerts()`** (notifications.py ~line 130):

Replace this block:
```python
for sub in subscribers:
    sub_outcode = sub["outcode"].upper()
    if sub_outcode in extracted_outcodes or re.search(r'\b' + re.escape(sub_outcode) + r'\b', addr):
        matching_subs.append(sub)
```

With this:
```python
for sub in subscribers:
    sub_outcode = sub["outcode"].upper()
    sub_lat = sub.get("lat")
    sub_lon = sub.get("lon")
    sub_radius = sub.get("radius", 15)
    matched = False

    # Priority 1: Exact outcode match
    if sub_outcode in extracted_outcodes or re.search(r'\b' + re.escape(sub_outcode) + r'\b', addr):
        matched = True

    # Priority 2: Haversine distance check
    if not matched and sub_lat and sub_lon:
        for oc in extracted_outcodes:
            lead_lat, lead_lon = database.lookup_outcode_centroid(oc)
            if lead_lat and lead_lon:
                dist = database.haversine_miles(sub_lat, sub_lon, lead_lat, lead_lon)
                if dist <= sub_radius:
                    matched = True
                    break

    # Priority 3: Regional prefix fallback
    if not matched:
        import re as _re
        sub_prefix = _re.match(r'^([A-Z]{1,2})', sub_outcode)
        if sub_prefix:
            prefix = sub_prefix.group(1)
            if any(oc.startswith(prefix) for oc in extracted_outcodes):
                matched = True

    if matched:
        matching_subs.append(sub)
```

Also need to add `lat, lon` to the `get_active_subscribers_by_seniority()` SELECT columns and `cols` list.

---

### TASK 2 — Quota Enforcement + Realistic Tier Quotas

**Problem:** Monthly quota is never checked before dispatching a lead. Contractors can receive unlimited leads.

**Fix in `dispatch_lead_alerts()` `notifications.py`** — inside the seniority allocation loop (around line 144):

Replace:
```python
if database.record_lead_dispatch_and_burn(lead_id, sub_id, email, dispatch_type="seniority_standard"):
```

With:
```python
# Check quota before burning
if sub.get("delivered", 0) >= sub.get("quota", 5):
    continue  # Skip — this subscriber is at their monthly quota
if database.record_lead_dispatch_and_burn(lead_id, sub_id, email, dispatch_type="seniority_standard"):
```

The tier-based quotas are set at registration time (see Task 1, Step 3 TIER_QUOTAS dict). The `get_active_subscribers_by_seniority()` already returns `delivered_this_month` as `"delivered"` and `monthly_quota` as `"quota"` in the dict (check cols list at line ~649 in database.py — verify field order matches).

---

### TASK 3 — Login Ghost Session Prevention

**Problem:** Any email address gets a magic link, clicks it, and gets a full dashboard session even with no subscription.

**Find the verify endpoint** in `main.py` — search for `verify_cron_secret` or `treekey_contractor_session` near line 2082.

**After** `verified_email = database.verify_login_token(token)` and `if not verified_email: return RedirectResponse(...)`:

Add this check **before** setting the cookie:
```python
# Verify they have an active subscription
active_sub = database.get_contractor_subscription(verified_email)
if not active_sub or not active_sub.get("active"):
    return RedirectResponse(
        url="/pricing?msg=no_subscription",
        status_code=303
    )
```

Also add `get_contractor_subscription(email)` to `database.py` if it doesn't exist:
```python
def get_contractor_subscription(email: str) -> dict:
    """Returns the contractor subscription record for this email, or empty dict."""
    if not SURL or not email:
        return {}
    try:
        conn = get_db_conn()
        cur = conn.cursor()
        try:
            cur.execute("""
                SELECT id, tier, center_outcode, radius_miles, active, monthly_quota, delivered_this_month
                FROM contractor_subscriptions WHERE customer_email = %s
            """, (email.strip().lower(),))
            row = cur.fetchone()
            if row:
                cols = ["id", "tier", "outcode", "radius", "active", "quota", "delivered"]
                return dict(zip(cols, row))
            return {}
        finally:
            cur.close()
            conn.close()
    except Exception as e:
        logger.error(f"[Subscription] Error fetching sub for {email}: {e}")
        return {}
```

Also update the `/pricing` page to show a message if `?msg=no_subscription` is in the query string. Find `def pricing()` in main.py (~line 1163) and add at top of the function:
```python
msg = request.query_params.get("msg", "")
msg_banner = ""
if msg == "no_subscription":
    msg_banner = "<div style='background:#fef2f2; border:1px solid #fca5a5; border-radius:8px; padding:14px; margin-bottom:20px; color:#991b1b;'><b>No active subscription found</b> for that email. Please subscribe below to access your dashboard.</div>"
```
And inject `{msg_banner}` into the pricing page HTML return.

Note: `pricing()` currently doesn't take a `request` param. Change its signature to `def pricing(request: Request)`.

---

### TASK 4 — Domestic Lead Sourcing Overhaul

**Core insight:** The highest-volume legitimate source is **householder planning applications** on the exact same council portals you're already scraping. When someone applies for a house extension, loft conversion, or outbuilding, trees are almost always involved. Filter by `applicationType=HOUS` or `type=Householder` in the existing UK Planning API calls.

**Add to `domestic_scrapers.py`** — new function after existing scrapers:

```python
def scrape_householder_planning_applications() -> List[Dict[str, Any]]:
    """
    Scrapes householder planning applications from UK Planning API.
    These are residential extensions/conversions that almost always require tree surveys or clearance.
    Returns leads with lead_source_type = 'direct_homeowner'.
    """
    UK_PLANNING_API_KEY = os.getenv("UK_PLANNING_API_KEY", "").strip()
    if not UK_PLANNING_API_KEY:
        logger.warning("[Householder] UK_PLANNING_API_KEY not set")
        return []

    leads = []
    # Cycle through UK postcode areas to get broad national coverage
    POSTCODE_SAMPLES = [
        "NG", "LE", "DE", "SK", "B", "CV", "WV", "WS", "DY",
        "BS", "GL", "SN", "BA", "SP", "BH", "DT", "EX", "PL", "TQ",
        "SO", "PO", "GU", "RH", "BN", "TN", "CT", "ME", "DA", "BR",
        "CR", "SM", "KT", "TW", "UB", "SL", "RG", "OX", "MK", "NN",
        "PE", "CB", "IP", "NR", "CO", "SS", "CM", "EN", "WD", "AL",
        "HP", "LU", "SG", "HR", "WR", "SY", "TF", "ST", "CW", "WA",
        "CH", "L", "PR", "FY", "BB", "BL", "WN", "M", "OL", "SK",
        "HD", "HX", "BD", "LS", "WF", "S", "DN", "HU", "YO", "TS",
        "DL", "SR", "NE", "DH", "CA", "LA", "HG", "YO",
        "EH", "G", "KA", "PA", "FK", "KY", "DD", "AB",
        "CF", "SA", "NP", "LD"
    ]

    import datetime
    today = datetime.date.today()
    date_from = (today - datetime.timedelta(days=7)).strftime("%Y-%m-%d")

    for prefix in POSTCODE_SAMPLES[:30]:  # limit per run to avoid quota burn
        try:
            url = "https://api.planning.data.gov.uk/entity.json"
            params = {
                "dataset": "development-policy",
                "geometry_relation": "intersects",
                "entries": "current",
                "limit": 20,
                "field": "reference,name,geometry,entry-date,description",
            }
            # Try UK Planning API householder endpoint
            hous_url = (
                f"https://www.planning.data.gov.uk/entity.json"
                f"?dataset=householder-application"
                f"&entry-date_gte={date_from}"
                f"&limit=25"
            )
            resp = requests.get(hous_url, headers={"key": UK_PLANNING_API_KEY}, timeout=15)
            if resp.status_code != 200:
                continue
            data = resp.json()
            entities = data.get("entities", [])
            for e in entities:
                ref = e.get("reference", "")
                addr = e.get("name", "") or e.get("address", "")
                desc = e.get("description", f"Householder planning application — potential tree survey/clearance required. Ref: {ref}")
                if not addr:
                    continue
                leads.append({
                    "ref": f"HOUS-{ref}",
                    "addr": addr,
                    "summary": desc[:350],
                    "council": f"{prefix} Planning Authority",
                    "lead_score": "medium",
                    "lead_price": 25,
                    "source_type": "direct_homeowner"
                })
        except Exception as e:
            logger.debug(f"[Householder] Error for prefix {prefix}: {e}")

    logger.info(f"[Householder] Found {len(leads)} householder application leads")
    return leads
```

**Also add `scrape_rated_people_jobs()`** — Rated People publishes jobs publicly:
```python
def scrape_rated_people_jobs() -> List[Dict[str, Any]]:
    """Scrapes Rated People tree surgery job requests."""
    leads = []
    search_terms = ["tree surgeon", "tree removal", "hedge cutting", "stump grinding", "tree felling"]
    for term in search_terms:
        url = f"https://www.ratedpeople.com/browse-jobs?q={urllib.parse.quote(term)}&category=gardening"
        html = fetch_unblocked_html(url)
        if not html:
            continue
        try:
            soup = BeautifulSoup(html, "html.parser")
            job_items = soup.find_all(["div", "article", "li"], class_=re.compile(r"job|listing|card", re.I))
            for item in job_items[:25]:
                text = item.get_text(separator=" ", strip=True)
                if not any(kw in text.lower() for kw in DOMESTIC_KEYWORDS):
                    continue
                title_el = item.find(["h2", "h3", "h4", "strong"])
                title = title_el.get_text(strip=True) if title_el else text[:80]
                loc_el = item.find(class_=re.compile(r"location|area|postcode", re.I))
                loc = loc_el.get_text(strip=True) if loc_el else "United Kingdom"
                ref = f"RP-{hash(title + loc) % 999999:06d}"
                score = _score_domestic_job(text)
                leads.append({
                    "ref": ref, "addr": loc, "summary": f"{title}. {text[:200]}",
                    "council": "Rated People Job Board",
                    "lead_score": score, "lead_price": 19 if score == "small" else 25,
                    "source_type": "domestic_classified"
                })
        except Exception as e:
            logger.debug(f"[RatedPeople] Error: {e}")
    logger.info(f"[RatedPeople] Found {len(leads)} domestic job leads")
    return leads
```

**Update `run_all_domestic_scrapers()`** at the bottom of `domestic_scrapers.py` to call both new functions and merge results.

---

### TASK 5 — Lead ID/Ref Fix in Dispatch Burn Query

**File:** `database.py` — `record_lead_dispatch_and_burn()` starting around line 659.

**Find the burn SQL** — look for `UPDATE leads SET status = 'claimed'`. Change:
```sql
WHERE id = %s
```
To:
```sql
WHERE id::text = %s OR reference = %s
```
And add the second `lead_id` parameter to the execute tuple: `(lead_id, lead_id, ...)`.

Also check the `INSERT INTO lead_dispatches` query in the same function — make sure it handles both UUID and string refs.

---

## COMMIT STRATEGY

Commit after each task:
```
git add . && git commit -m "feat(routing): geographic haversine radius matching via postcodes.io" && git push
git add . && git commit -m "feat(quota): tier-based quotas + quota enforcement before lead burn" && git push
git add . && git commit -m "fix(auth): prevent ghost sessions for non-subscribers on magic link login" && git push
git add . && git commit -m "feat(domestic): householder planning + rated people scrapers for higher lead volume" && git push
git add . && git commit -m "fix(dispatch): use id::text cast in lead burn query for ref compatibility" && git push
```

---

## THINGS NOT TO BREAK

- The `dispatch_lead_alerts(city, leads)` function signature must stay the same — called from `domestic_scrapers.py` and `main.py`
- `create_checkout_session(plan_key, outcode, lead_id, radius)` in `payments.py` — already updated, don't revert
- The area selector interstitial at `GET /checkout/{plan_key}` — recently added, keep it
- Admin auth: `verify_admin_or_secret(request, secret)` — do not remove from admin routes
- Session cookie: `secure=True, httponly=True, samesite="lax"` — recently fixed, keep as is
- `robots.txt` route and `sitemap.xml` using `PUBLIC_APP_URL` env var — recently fixed, keep

---

## ENVIRONMENT VARIABLES (All set in Render)

| Key | Purpose |
|-----|---------|
| `SUPABASE_DB_URL` | PostgreSQL connection string |
| `TRIGGER_SECRET` | Cron security gate |
| `STRIPE_SECRET_KEY` | Stripe payments |
| `STRIPE_WEBHOOK_SECRET` | Stripe webhook listener |
| `RESEND_API_KEY` | Transactional email |
| `TEST_EMAIL` | Admin alert destination |
| `UK_PLANNING_API_KEY` | Council scraper API |
| `PUBLIC_APP_URL` | Set to `https://treekey.uk` |
| `GOOGLE_MAPS_KEY` | Maps/geocoding |

**`SCRAPER_API_KEY`, `SCRAPINGBEE_API_KEY`, `ZENROWS_API_KEY`** — proxy scrapers, may not be set. The `fetch_unblocked_html()` function falls back gracefully if missing.

---

## RECENT SWEEP COMMITS (DO NOT REVERT)

```
044cbf7 feat: pre-checkout area selector
e73acf2 sweep[20]: DB connection leaks fixed in generate-letter/flyer
4cc4a4a sweep[18]: PROJECT_STATE updated
f631658 sweep[16]: logout delete_cookie flags fixed
ac24ae9 sweep[15]: secure=True on session cookie
9f78991 sweep[13]: dashboard email param removed (security)
ed77834 sweep[12]: homepage meta tags / title fixed
6b90ee8 sweep[11]: email from addresses / robots.txt
b2bc45f sweep[10]: sitemap domain fixed to treekey.uk
3cd4ff1 sweep[9]: removed dead openai dependency
578dc5b sweep[8]: proximity matching improved
a698b57 sweep[7]: overflow threshold fixed
363858f sweep[6]: monthly quota reset added
9d4fac5 sweep[5]: price labels fixed
```

---

## USER CONTEXT

- **Nick** — operator, works nights (BST timezone)
- Wants autonomous work with no permission requests for code changes
- `git push` deploys automatically — always push after commits
- Model preference: Claude Sonnet 4.6 (Thinking) > Opus > Gemini Pro for this project
- WhatsApp/email notification toggle + map area selector for contractors is a future Phase 2 feature (logged in PROJECT_STATE.md item #7)

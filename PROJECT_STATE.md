# PROJECT STATE: VECTOR DATA LABS (V4.0-SECURE)

**Status:** Secure, Deployed, Pre-Revenue

**Last Updated:** 20 Aug 2026

---

## 1. FILE STRUCTURE (THE DEPARTMENTS)

- `main.py`: Reception Desk (API Routes, Dashboard, HTTP Basic Auth).
- `database.py`: Filing Cabinet (Supabase & Schema).
- `notifications.py`: Digital Postman (Resend email & WhatsApp links).
- `research.py`: Investigator (Companies House search & Officers API).
- `scanners.py`: Scout (Leeds ArcGIS & London GLA lead generation).
- `PROJECT_STATE.md`: Persistent memory of progress and goals.

---

## 2. PILLAR PROGRESS

- **Pillar 1 (WhatsApp):** ✅ Manual links active. Batching logic installed.
- **Pillar 2 (Director Enrichment):** ✅ Companies House Officers API live. Apollo removed.
- **Pillar 3 (Google):** ✅ Google Places reputation rating active. Key live in Render.
- **Pillar 4 (Make.com / Telegram):** ⏳ TO DO — Webhook endpoint not yet built. Decision: Telegram bot for internal lead alerts (free, no personal number required). Skip Twilio.

---

## 3. SECURITY STATUS

- ✅ HTTP Basic Auth on all dashboard routes (DASHBOARD_USER / DASHBOARD_PASS)
- ✅ TRIGGER_SECRET removed from HTML — never exposed in page source
- ✅ Cron routes separated from dashboard routes
- ✅ Constant-time password comparison (secrets.compare_digest)
- ✅ API docs (/docs, /redoc) disabled publicly

---

## 4. CITIES LIVE

- ✅ Leeds (ArcGIS planning portal)
- ✅ London (GLA Datahub)
- ⏳ Birmingham — TO DO
- ⏳ Manchester — TO DO
- ⏳ Bristol — TO DO
- ⏳ Sheffield — TO DO

---

## 5. ACTIVE OBJECTIVES (PRIORITY ORDER)

1. Fix silent error handling in `scanners.py` (bare `except: pass`)
2. Add automated scan scheduling (Render cron job — no manual trigger needed)
3. Add lead scoring logic (Small £25 / Medium £50 / Large £75) based on application keywords
4. Add Birmingham + Manchester scanners
5. Build Stripe integration (pay-per-lead credits + monthly subscription)
6. Build Pillar 4: Make.com webhook → Telegram bot alerts
7. Add Bristol + Sheffield scanners
8. Build outreach email list from 134 directors in DB
9. First sale

---

## 6. KNOWN DB STATE

- Potential Partners: 134 (as of 20 Aug 2026)
- Leads: 137 (as of 20 Aug 2026)
- Director names: Mostly NULL — run /enrich-all after next Render deploy

"""internal_digest.py -- daily digests for TreeKey's INTERNAL notification emails to Nick (14 Oct 2026).

Why: every internal notification (a new-lead alert per lead or city, each scanner failure, quota warnings...) was its own
Resend email, eating the free quota. They are now queued in ONE table and sent as at most one email per topic per day
at 08:00 Europe/London (BST/GMT handled), and nothing is sent for an empty topic.

Scope: ONLY internal operational email to Nick (notifications.send_resend_email). Customer email, login/signup links,
outreach, receipts and customer-facing alerts do not touch this module.

Topics (fixed): LEADS, SCANNER, BILLING, SECURITY, SYSTEM.
Still immediate (see digest_topic_for_critical): security, database, payment/billing and any unrecognised CRITICAL alert,
now deduplicated across restarts. Routine scanner errors are queued.

Safety design:
  * Queue is a Postgres table, so it survives restarts. Rows are marked sent ONLY after Resend accepts the email.
  * claim(): one transaction under a Postgres advisory lock checks "already done today / back-off / another sender is
    mid-send" and claims the pending rows. A second worker or overlapping tick gets nothing and sends nothing.
  * Failure: claim released, controlled back-off (30 min, 1 h, 2 h; at most 4 attempts a day), rows kept for the next try.
  * Resend Idempotency-Key = topic + date + queued row ids, so a crash between "Resend accepted" and "marked sent"
    cannot produce a second copy of the same digest.
  * Leads are unique by (topic, lead ref): every lead appears exactly once, ever. Nothing old is replayed: the table
    starts empty.
"""
import base64
import csv
import datetime
import hashlib
import html
import io
import json
import logging
import os
import re
import threading
import time

log = logging.getLogger("vector-data-labs")

# The moment this process started. The lead cut-over is this time (first process to run the new code wins, later ones never
# overwrite it), so it is never later than the first lead this code could save, however late the database write succeeds.
_PROCESS_START = datetime.datetime.now(datetime.timezone.utc)

TOPICS = ("LEADS", "SCANNER", "BILLING", "SECURITY", "SYSTEM")
TOPIC_LABEL = {"LEADS": "New leads", "SCANNER": "Scanner & search issues", "BILLING": "Billing & payments",
               "SECURITY": "Security", "SYSTEM": "System & data checks"}
SEND_HOUR_LONDON = 8
CLAIM_TTL_MIN = 20          # a claim older than this is treated as a crashed sender and can be re-claimed
MAX_ATTEMPTS = 4            # sends attempted per topic per day
BACKOFF_MIN = (30, 60, 120)  # wait after the 1st, 2nd, 3rd failed attempt
INLINE_LIMIT = 30           # rows shown in the email body; everything is in the attached CSV beyond this
FALLBACK_PER_HOUR = 3       # if the queue is unusable, at most this many immediate fallback emails per hour (whole app, stored in the database)...
FALLBACK_PER_DAY = 6        # ...and per rolling 24 hours; each distinct alert at most once per 24 hours. No readable database -> NO fallback email.
FALLBACK_STATE_KEY = "digest_fallback"
BUFFER_MAX = 500            # in-memory holding area for items that could not be queued (see _buffer / flush_buffer)
BACKFILL_LIMIT = 500        # leads picked up per sweep
LEADS_SINCE_KEY = "digest_leads_since"

# ---------------------------------------------------------------------------------------------------------------- routing
_IMMEDIATE_WORDS = ("SECURITY", "DATABASE", "STRIPE", "REVENUE", "PAYMENT", "BILLING")
_SCANNER_WORDS = ("SCRAPER", "SILENT SOURCE", "PIPELINE", "AUTONOMOUS CYCLE", "CONTRACTOR DISCOVERY", "GEOCODING", "API RATE LIMIT", "PLANNING", "QUOTA")


def topic_for_category(category):
    """Topic a WARNING / queued item belongs in. Small fixed set; everything unrecognised is SYSTEM."""
    c = (category or "").upper()
    if "SECURITY" in c:
        return "SECURITY"
    if any(w in c for w in ("REVENUE", "PAYMENT", "BILLING", "STRIPE")):
        return "BILLING"
    if any(w in c for w in _SCANNER_WORDS):
        return "SCANNER"
    return "SYSTEM"


def digest_topic_for_critical(category):
    """CRITICAL/SECURITY alerts: returns a topic if the alert is routine (goes in the daily digest), or None if it must stay
    immediate. Only scanner/search categories are digested; security, database, payment and anything unrecognised stay immediate."""
    c = (category or "").upper()
    if any(w in c for w in _IMMEDIATE_WORDS):
        return None
    return "SCANNER" if topic_for_category(c) == "SCANNER" else None


_IN_SUFFIX = re.compile(r"^(?P<issue>.+?)\s+IN\s+(?P<src>[A-Za-z0-9][A-Za-z0-9 &'\-.]{1,40})$")
_ACTION_PREFIX = re.compile(r"^(?P<src>\S+)\s+(?P<issue>CRASHED BEFORE FINISHING)$", re.I)


def split_issue(title, source=""):
    """('FAILED FOR ALL PREFIXES', 'LEEDS') from 'X FAILED FOR ALL PREFIXES IN LEEDS' so the same fault in several places is one entry."""
    t = (title or "").strip()
    if source:
        return t, source
    m = _IN_SUFFIX.match(t) or _ACTION_PREFIX.match(t)
    if m:
        return m.group("issue").strip(), m.group("src").strip()
    return t, ""


def item_key(topic, category, issue):
    return hashlib.sha1(f"{topic}|{(category or '').upper()}|{(issue or '').upper()}".encode()).hexdigest()[:24]


# ---------------------------------------------------------------------------------------------------------------- time
def _last_sunday(year, month):
    d = datetime.date(year, month, 31)
    return d - datetime.timedelta(days=(d.weekday() + 1) % 7)


def to_london(dt):
    """UTC (or aware) datetime -> Europe/London local time, BST/GMT correct. Falls back to the EU rule if tz data is missing."""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=datetime.timezone.utc)
    try:
        from zoneinfo import ZoneInfo
        return dt.astimezone(ZoneInfo("Europe/London"))
    except Exception:
        u = dt.astimezone(datetime.timezone.utc)
        start = datetime.datetime.combine(_last_sunday(u.year, 3), datetime.time(1), datetime.timezone.utc)
        end = datetime.datetime.combine(_last_sunday(u.year, 10), datetime.time(1), datetime.timezone.utc)
        off = datetime.timedelta(hours=1 if start <= u < end else 0)
        return (u + off).replace(tzinfo=datetime.timezone(off))


def _as_dt(v):
    if isinstance(v, datetime.datetime):
        return v if v.tzinfo else v.replace(tzinfo=datetime.timezone.utc)
    if isinstance(v, str) and v:
        try:
            d = datetime.datetime.fromisoformat(v.replace("Z", "+00:00"))
            return d if d.tzinfo else d.replace(tzinfo=datetime.timezone.utc)
        except ValueError:
            pass
    return datetime.datetime.now(datetime.timezone.utc)


def _fmt(dt):
    return to_london(_as_dt(dt)).strftime("%d %b %H:%M")


# ---------------------------------------------------------------------------------------------------------------- storage
DDL = (
    """CREATE TABLE IF NOT EXISTS internal_digest_queue (
        id BIGSERIAL PRIMARY KEY,
        topic TEXT NOT NULL,
        kind TEXT NOT NULL,               -- lead | issue | note | resolved
        item_key TEXT NOT NULL,
        severity TEXT,
        category TEXT,
        title TEXT,
        source TEXT,
        detail TEXT,
        payload JSONB,
        link TEXT,
        occurred_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        claim_token TEXT,
        claimed_at TIMESTAMPTZ,
        sent_at TIMESTAMPTZ
    )""",
    "CREATE UNIQUE INDEX IF NOT EXISTS uq_idq_once ON internal_digest_queue(topic, item_key) WHERE kind IN ('lead','note')",
    "CREATE INDEX IF NOT EXISTS idx_idq_pending ON internal_digest_queue(topic) WHERE sent_at IS NULL",
)
INSERT_SQL = ("INSERT INTO internal_digest_queue (topic, kind, item_key, severity, category, title, source, detail, payload, link) "
              "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s)")
INSERT_ONCE_SQL = INSERT_SQL + " ON CONFLICT (topic, item_key) WHERE kind IN ('lead','note') DO NOTHING"
LOCK_SQL = "SELECT pg_advisory_xact_lock(hashtext(%s))"
STATE_GET_SQL = "SELECT value FROM system_state WHERE key = %s"
STATE_SET_SQL = ("INSERT INTO system_state (key, value, updated_at) VALUES (%s, %s, NOW()) "
                 "ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, updated_at = NOW()")
CUTOVER_SQL = ("INSERT INTO system_state (key, value, updated_at) VALUES (%s, %s, NOW()) ON CONFLICT (key) DO NOTHING")
EXISTS_SQL = ("SELECT to_regclass('public.internal_digest_queue') IS NOT NULL AND to_regclass('public.uq_idq_once') IS NOT NULL "
              "AND to_regclass('public.idx_idq_pending') IS NOT NULL")
BACKFILL_SQL = ("SELECT l.reference, l.council_source, l.address, l.summary, l.lead_score, l.lead_price FROM leads l "
                "WHERE l.reference IS NOT NULL AND l.discovered_at >= (SELECT value::timestamptz FROM system_state WHERE key = %s) "
                "AND NOT EXISTS (SELECT 1 FROM internal_digest_queue q WHERE q.topic = 'LEADS' AND q.item_key = l.reference) "
                "ORDER BY l.discovered_at LIMIT %s")
STATE_DEL_SQL = "DELETE FROM system_state WHERE key = %s"
LIVE_CLAIM_SQL = ("SELECT 1 FROM internal_digest_queue WHERE topic = %s AND sent_at IS NULL AND claim_token IS NOT NULL "
                  "AND claimed_at > NOW() - (%s || ' minutes')::interval LIMIT 1")
CLAIM_SQL = ("UPDATE internal_digest_queue SET claim_token = %s, claimed_at = NOW() WHERE topic = %s AND sent_at IS NULL "
             "RETURNING id, kind, item_key, severity, category, title, source, detail, payload, link, occurred_at")
SENT_SQL = "UPDATE internal_digest_queue SET sent_at = NOW() WHERE claim_token = %s"
RELEASE_SQL = "UPDATE internal_digest_queue SET claim_token = NULL, claimed_at = NULL WHERE claim_token = %s AND sent_at IS NULL"
DONE_TOPICS_SQL = "SELECT key FROM system_state WHERE LEFT(key, 12) = 'digest_done:' AND value = %s"
_COLS = ("id", "kind", "item_key", "severity", "category", "title", "source", "detail", "payload", "link", "occurred_at")


class PgStore:
    """Real storage (Postgres via database.get_db_conn)."""
    _schema_ready = False

    def _conn(self):
        import database
        return database.get_db_conn()

    def ensure_cutover(self):
        """Durable lead cut-over, stored in the long-standing system_state table (not the new queue table), so it can be
        written even if the queue table cannot be created. Written once; never overwritten."""
        conn = self._conn()
        try:
            cur = conn.cursor()
            cur.execute(CUTOVER_SQL, (LEADS_SINCE_KEY, _PROCESS_START.isoformat()))
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def ensure_schema(self):
        if PgStore._schema_ready:
            return
        conn = self._conn()
        try:
            cur = conn.cursor()
            # If the table and both indexes already exist (the normal case once migration 0007 has been run) do NOT run any
            # DDL: even 'CREATE ... IF NOT EXISTS' needs CREATE rights on the schema, which a restricted role may lack.
            cur.execute(EXISTS_SQL)
            row = cur.fetchone()
            if not (row and str(row[0]).lower() in ("t", "true", "1")):
                for stmt in DDL:
                    cur.execute(stmt)
            cur.execute(CUTOVER_SQL, (LEADS_SINCE_KEY, _PROCESS_START.isoformat()))
            conn.commit()
            PgStore._schema_ready = True
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def fallback_reserve(self, key, now_ts):
        """Durable fallback-email limiter: read, decide and record in ONE transaction under an advisory lock, so restarts and
        concurrent processes share the same limit. Any database problem raises -> caller fails closed (no email)."""
        conn = self._conn()
        try:
            cur = conn.cursor()
            cur.execute(LOCK_SQL, ("internal_digest:fallback",))
            cur.execute(STATE_GET_SQL, (FALLBACK_STATE_KEY,))
            r = cur.fetchone()
            allowed, new_state = _fallback_decide(r[0] if r else None, key, now_ts)
            if allowed:
                cur.execute(STATE_SET_SQL, (FALLBACK_STATE_KEY, new_state))
            conn.commit()
            return allowed
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def enqueue(self, rows):
        self.ensure_schema()
        conn = self._conn()
        try:
            cur = conn.cursor()
            for r in rows:
                once = r["kind"] in ("lead", "note")
                cur.execute(INSERT_ONCE_SQL if once else INSERT_SQL,
                            (r["topic"], r["kind"], r["item_key"], r.get("severity"), r.get("category"), r.get("title"),
                             r.get("source"), r.get("detail"), json.dumps(r.get("payload") or {}), r.get("link")))
            conn.commit()
            return True
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def missing_leads(self):
        """Leads stored in the leads table since the cut-over that have no queue row yet (their queueing failed earlier).
        No cut-over row -> NULL comparison -> nothing returned (safe)."""
        self.ensure_schema()
        conn = self._conn()
        try:
            cur = conn.cursor()
            cur.execute(BACKFILL_SQL, (LEADS_SINCE_KEY, BACKFILL_LIMIT))
            return list(cur.fetchall())
        finally:
            conn.close()

    def done_topics(self, today):
        conn = self._conn()
        try:
            cur = conn.cursor()
            cur.execute(DONE_TOPICS_SQL, (today,))
            return {k[0][len("digest_done:"):] for k in cur.fetchall()}
        finally:
            conn.close()

    def claim(self, topic, today, now, token):
        """('claimed', rows) or (reason, []). One transaction under an advisory lock, so concurrent senders cannot both claim."""
        self.ensure_schema()
        conn = self._conn()
        try:
            cur = conn.cursor()
            cur.execute(LOCK_SQL, ("internal_digest:" + topic,))
            cur.execute(STATE_GET_SQL, ("digest_done:" + topic,))
            r = cur.fetchone()
            if r and r[0] == today:
                conn.rollback()
                return "done_today", []
            cur.execute(STATE_GET_SQL, ("digest_retry:" + topic,))
            r = cur.fetchone()
            if r and r[0]:
                st = json.loads(r[0])
                if st.get("day") == today:
                    if st.get("n", 0) >= MAX_ATTEMPTS:
                        conn.rollback()
                        return "gave_up_today", []
                    if now < _as_dt(st.get("next")):
                        conn.rollback()
                        return "backoff", []
            cur.execute(LIVE_CLAIM_SQL, (topic, str(CLAIM_TTL_MIN)))
            if cur.fetchone():
                conn.rollback()
                return "in_progress", []
            cur.execute(CLAIM_SQL, (token, topic))
            rows = []
            for rec in cur.fetchall():
                d = dict(zip(_COLS, rec))
                if isinstance(d["payload"], str):
                    d["payload"] = json.loads(d["payload"] or "{}")
                d["payload"] = d["payload"] or {}
                d["occurred_at"] = _as_dt(d["occurred_at"])
                rows.append(d)
            conn.commit()
            rows.sort(key=lambda d: (d["occurred_at"], d["id"]))
            return "claimed", rows
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def finish_ok(self, topic, token, today):
        conn = self._conn()
        try:
            cur = conn.cursor()
            cur.execute(LOCK_SQL, ("internal_digest:" + topic,))
            cur.execute(SENT_SQL, (token,))
            cur.execute(STATE_SET_SQL, ("digest_done:" + topic, today))
            cur.execute(STATE_DEL_SQL, ("digest_retry:" + topic,))
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def finish_fail(self, topic, token, today, now, error=""):
        conn = self._conn()
        try:
            cur = conn.cursor()
            cur.execute(LOCK_SQL, ("internal_digest:" + topic,))
            cur.execute(RELEASE_SQL, (token,))
            cur.execute(STATE_GET_SQL, ("digest_retry:" + topic,))
            r = cur.fetchone()
            st = json.loads(r[0]) if r and r[0] else {}
            n = (st.get("n", 0) + 1) if st.get("day") == today else 1
            wait = BACKOFF_MIN[min(n - 1, len(BACKOFF_MIN) - 1)]
            nxt = (now + datetime.timedelta(minutes=wait)).isoformat()
            cur.execute(STATE_SET_SQL, ("digest_retry:" + topic, json.dumps({"day": today, "n": n, "next": nxt, "error": (error or "")[:120]})))
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def recurring_warnings(self):
        import database
        return database.get_all_recurring_warnings(window_days=7, min_days=3, active_within_hours=24) or []


_store = None


def _get_store():
    global _store
    if _store is None:
        _store = PgStore()
    return _store


# ---------------------------------------------------------------------------------------------------------------- producers
_fb_lock = threading.Lock()
_buffer = []            # rows that could not be queued (queue/database down); re-queued by flush_buffer()
_cutover_done = False


def _fallback_decide(state_json, key, t):
    """Pure rule shared by the real store and the tests. state: {"times": [...], "keys": {alert: last_send}}.
    Returns (allowed, new_state_json). Each distinct alert once per 24h; FALLBACK_PER_HOUR per hour; FALLBACK_PER_DAY per 24h."""
    try:
        st = json.loads(state_json) if state_json else {}
    except Exception:
        st = {}
    times = [x for x in st.get("times", []) if isinstance(x, (int, float)) and t - x < 86400]
    keys = {k: v for k, v in (st.get("keys") or {}).items() if isinstance(v, (int, float)) and t - v < 86400}
    if key in keys or len(times) >= FALLBACK_PER_DAY or sum(1 for x in times if t - x < 3600) >= FALLBACK_PER_HOUR:
        return False, json.dumps({"times": times, "keys": keys})
    times.append(t)
    keys[key] = t
    return True, json.dumps({"times": times, "keys": keys})


def fallback_allowed(key, now_ts=None, store=None):
    """Used ONLY when the queue is unusable and a routine alert would otherwise be emailed immediately.
    The limit lives in the database (system_state), so it survives restarts and is shared by every process. FAIL CLOSED: if
    the limit cannot be read and recorded (database down, permissions...), no fallback email is sent. A refused alert is not
    lost while the app keeps running: it sits in the in-memory buffer and goes into the next digest once the queue works."""
    t = time.time() if now_ts is None else now_ts
    try:
        return bool((store or _get_store()).fallback_reserve(key, t))
    except Exception as e:
        log.error(f"[InternalDigest] fallback email refused (cannot enforce the limit): {e}")
        return False


def ensure_cutover(store=None):
    """Make sure the durable lead cut-over exists. Cheap after the first success. Called at app start (before any scan can
    run), before any lead is queued, and on every scheduler tick, so a failed first attempt is simply retried."""
    global _cutover_done
    if _cutover_done:
        return True
    try:
        (store or _get_store()).ensure_cutover()
        _cutover_done = True
        return True
    except Exception as e:
        log.error(f"[InternalDigest] could not record the lead cut-over yet: {e}")
        return False


def _buffer_rows(rows):
    with _fb_lock:
        for r in rows:
            if len(_buffer) < BUFFER_MAX:
                _buffer.append(r)


def flush_buffer(store=None):
    """Re-queue items held in memory while the queue was unavailable. Called on every scheduler tick."""
    with _fb_lock:
        rows = list(_buffer)
    if not rows:
        return 0
    try:
        (store or _get_store()).enqueue(rows)
    except Exception as e:
        log.error(f"[InternalDigest] still cannot queue {len(rows)} buffered item(s): {e}")
        return 0
    with _fb_lock:
        del _buffer[:len(rows)]
    return len(rows)


def _num(v, default=0):
    """Database NUMERIC arrives as Decimal (not JSON-serialisable) or text: always a plain number."""
    try:
        f = float(v)
        return int(f) if f == int(f) else f
    except (TypeError, ValueError):
        return default


def backfill_leads(store=None):
    """Safety net for lead notifications whose queueing failed (database blip): every lead is already stored in the leads
    table, so anything stored after the cut-over with no queue row is queued now. Leads already queued or sent are
    skipped (unique per ref), and leads from before the cut-over are never touched. Returns how many were added."""
    store = store or _get_store()
    try:
        recs = store.missing_leads()
    except Exception as e:
        log.error(f"[InternalDigest] lead sweep failed: {e}")
        return 0
    if not recs:
        return 0
    try:
        from notifications import SCORE_TAG
    except Exception:
        SCORE_TAG = {"small": "Small", "medium": "Medium", "large": "Large"}
    rows = [{"ref": r[0], "city": r[1] or "", "addr": r[2] or "", "summary": r[3] or "", "score": SCORE_TAG.get(r[4] or "small", "Small"),
             "price": _num(r[5], 25), "link": ""} for r in recs]
    return len(rows) if enqueue_leads(rows, store=store) else 0


def _safe(fn, what):
    try:
        return fn()
    except Exception as e:      # queueing must never raise into scanner / payment code
        log.error(f"[InternalDigest] {what} failed: {e}")
        return False


def enqueue_issue(topic, category, title, description="", impact="", action="", severity="", source="", link="", metrics=None, store=None):
    """Queue one occurrence of an issue. Same (topic, category, issue) -> one digest entry with a count. Returns True if stored."""
    issue, src = split_issue(title, source)
    row = dict(topic=topic, kind="issue", item_key=item_key(topic, category, issue), severity=(severity or "").upper(), category=category,
               title=issue, source=src, detail=description, link=link,
               payload={"impact": impact, "action": action, "metrics": {str(k): str(v)[:200] for k, v in (metrics or {}).items()}})
    ok = _safe(lambda: (store or _get_store()).enqueue([row]), "enqueue_issue")
    if not ok:
        _buffer_rows([row])            # keep it for the next digest once the queue works again
    return ok


def enqueue_leads(rows, store=None):
    """rows: dicts with ref, city, addr, summary, score, price, link. One queue row per lead ref; a ref already queued/sent is skipped."""
    out = []
    for r in rows or []:
        ref = str(r.get("ref") or "").strip()
        if not ref:
            continue
        out.append(dict(topic="LEADS", kind="lead", item_key=ref, title=ref, source=r.get("city", ""), detail=r.get("summary", ""), link=r.get("link", ""),
                        payload={k: r.get(k) for k in ("city", "addr", "score", "price")}))
    if not out:
        return True
    ensure_cutover(store)                  # normally done at app start; retried here so it exists before any lead is queued
    ok = _safe(lambda: (store or _get_store()).enqueue(out), "enqueue_leads")
    if not ok:
        _buffer_rows(out)              # in-memory copy; the lead sweep (backfill_leads) is the durable safety net
    return ok


def enqueue_notes(topic, notes, store=None):
    """notes: dicts with key, title, detail, link, extra. Unique per (topic, key)."""
    out = [dict(topic=topic, kind="note", item_key=str(n["key"]), title=n.get("title", ""), detail=n.get("detail", ""), link=n.get("link", ""),
                payload=n.get("extra") or {}) for n in (notes or []) if n.get("key")]
    if not out:
        return True
    ok = _safe(lambda: (store or _get_store()).enqueue(out), "enqueue_notes")
    if not ok:
        _buffer_rows(out)
    return ok


def mark_resolved(topic, category, title, source="", store=None):
    """Record that the thing now works (call where success is known). Shown as 'resolved' on the matching digest entry."""
    issue, src = split_issue(title, source)
    row = dict(topic=topic, kind="resolved", item_key=item_key(topic, category, issue), category=category, title=issue, source=src)
    return _safe(lambda: (store or _get_store()).enqueue([row]), "mark_resolved")


# ---------------------------------------------------------------------------------------------------------------- rendering
_E = html.escape
_STYLE = "font-family:'Segoe UI',Tahoma,Verdana,sans-serif;max-width:720px;margin:auto;border:2px solid #cbd5e1;border-radius:12px;overflow:hidden;background:#fff;"
_TH = "padding:6px 8px;text-align:left;background:#f1f5f9;font-size:12px;color:#334155;"
_TD = "padding:6px 8px;border-top:1px solid #e2e8f0;font-size:13px;color:#0f172a;vertical-align:top;"


def _wrap(title, subtitle, body, base_url):
    foot = f"<a href='{_E(base_url)}/admin' style='color:#059669;'>Admin</a> &middot; <a href='{_E(base_url)}/recent-warnings' style='color:#059669;'>Recent warnings</a>" if base_url else ""
    return (f"<div style=\"{_STYLE}\"><div style='background:#0f172a;color:#fff;padding:16px 20px;'><div style='font-size:17px;font-weight:800;'>{_E(title)}</div>"
            f"<div style='font-size:12px;opacity:.85;margin-top:4px;'>{_E(subtitle)}</div></div><div style='padding:18px 20px;'>{body}"
            f"<p style='font-size:11px;color:#94a3b8;margin:16px 0 0;'>Daily digest, sent at most once a day per topic at 08:00 UK time. {foot}</p></div></div>")


def _csv_attachment(name, header, rows):
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(header)
    w.writerows(rows)
    return {"filename": name, "content": base64.b64encode(buf.getvalue().encode("utf-8")).decode()}


def aggregate_issues(rows):
    """Group queued issue rows: count, affected sources, first/last seen, resolved?  Returns (entries, unmatched_resolved_titles).
    An entry is resolved only if EVERY affected source has a later 'works again' marker (so one source recovering cannot hide another)."""
    groups, resolved = {}, {}
    for r in rows:
        if r["kind"] == "issue":
            g = groups.setdefault(r["item_key"], dict(key=r["item_key"], title=r["title"], category=r.get("category") or "", count=0, sources={}, last_by_src={},
                                                      first=r["occurred_at"], last=r["occurred_at"], severity="", detail="", impact="", action="", link="", metrics={}))
            g["count"] += 1
            src = r.get("source") or ""
            if src:
                g["sources"][src] = g["sources"].get(src, 0) + 1
            g["last_by_src"][src] = max(g["last_by_src"].get(src, r["occurred_at"]), r["occurred_at"])
            if r["occurred_at"] >= g["last"]:
                p = r.get("payload") or {}
                g.update(last=r["occurred_at"], severity=r.get("severity") or g["severity"], detail=r.get("detail") or g["detail"], impact=p.get("impact") or g["impact"],
                         action=p.get("action") or g["action"], link=r.get("link") or g["link"], metrics=p.get("metrics") or g["metrics"])
            g["first"] = min(g["first"], r["occurred_at"])
        elif r["kind"] == "resolved":
            k = (r["item_key"], r.get("source") or "")
            resolved[k] = max(resolved.get(k, r["occurred_at"]), r["occurred_at"])
    for k, g in groups.items():
        done = []
        for src, last in g["last_by_src"].items():
            t = resolved.get((k, src))
            if t is None and src:
                t = resolved.get((k, ""))      # a marker with no source covers the whole issue
            done.append(t if (t is not None and t >= last) else None)
        g["resolved_at"] = max(done) if done and all(d is not None for d in done) else None
    orphans = sorted({r["title"] for r in rows if r["kind"] == "resolved" and r["item_key"] not in groups})
    entries = sorted(groups.values(), key=lambda g: (g["resolved_at"] is not None, -g["count"], g["title"]))
    return entries, orphans


def render_issues(topic, rows, warnings, base_url):
    entries, orphans = aggregate_issues(rows)
    notes = [r for r in rows if r["kind"] == "note"]
    open_n = sum(1 for e in entries if not e["resolved_at"])
    parts = []
    if entries:
        trs = []
        for e in entries:
            srcs = ", ".join(f"{_E(s)}{f' x{n}' if n > 1 else ''}" for s, n in sorted(e["sources"].items())) or "&mdash;"
            status = (f"<b style='color:#15803d;'>Resolved</b> (worked again {_fmt(e['resolved_at'])})" if e["resolved_at"]
                      else "<b style='color:#b91c1c;'>Not resolved</b><br><span style='color:#64748b;font-size:11px;'>no success seen since</span>")
            todo = _E(e["action"]) if e["action"] else ""
            more = f"<br><span style='color:#64748b;font-size:11px;'>{_E(e['detail'][:220])}</span>" if e["detail"] else ""
            lnk = f"<br><a href='{_E(e['link'])}' style='color:#059669;font-size:11px;'>Open</a>" if e["link"] else ""
            trs.append(f"<tr><td style='{_TD}'><b>{_E(e['title'])}</b><br><span style='color:#64748b;font-size:11px;'>{_E(e['category'])}</span>{more}</td>"
                       f"<td style='{_TD}'>{e['count']}x<br><span style='color:#64748b;font-size:11px;'>{_fmt(e['first'])} - {_fmt(e['last'])}</span></td>"
                       f"<td style='{_TD}'>{srcs}</td><td style='{_TD}'>{status}</td><td style='{_TD}'>{todo}{lnk}</td></tr>")
        parts.append("<table style='width:100%;border-collapse:collapse;'><tr>" + "".join(f"<th style='{_TH}'>{h}</th>" for h in ("Issue", "Times", "Affected", "Status", "What to do")) + "</tr>" + "".join(trs) + "</table>")
    if notes:
        parts.append("<h3 style='font-size:14px;margin:18px 0 6px;'>Items to review</h3><table style='width:100%;border-collapse:collapse;'>" + "".join(
            f"<tr><td style='{_TD}'><b>{_E(n['title'])}</b></td><td style='{_TD}'>{_E((n['detail'] or '')[:200])}"
            + (f" <a href='{_E(n['link'])}' style='color:#059669;'>Open</a>" if n["link"] else "") + "</td></tr>" for n in notes) + "</table>")
    if warnings:
        parts.append("<h3 style='font-size:14px;margin:18px 0 6px;'>Recurring warnings (seen on 3+ of the last 7 days)</h3><table style='width:100%;border-collapse:collapse;'>" + "".join(
            f"<tr><td style='{_TD}'>{_E(w.get('title', ''))}<br><span style='color:#64748b;font-size:11px;'>{_E(w.get('category', ''))}</span></td>"
            f"<td style='{_TD};text-align:right;white-space:nowrap;'>{w.get('days_seen', '?')}/7 days</td></tr>" for w in warnings) + "</table>")
    if orphans:
        parts.append("<p style='font-size:12px;color:#15803d;'>Resolved since the last digest: " + _E("; ".join(orphans)) + "</p>")
    total = len(entries) + len(notes) + len(warnings)
    bits = ([f"{len(entries)} issue(s), {open_n} not resolved"] if entries else []) + ([f"{len(warnings)} recurring warning(s)"] if warnings else []) + ([f"{len(notes)} item(s) to review"] if notes else [])
    sub = "; ".join(bits)
    subject = f"TreeKey daily - {TOPIC_LABEL[topic]}: {total} item{'s' if total != 1 else ''}" + (f", {open_n} unresolved" if entries else "")
    return subject, _wrap(f"{TOPIC_LABEL[topic]} - daily digest", sub, "".join(parts), base_url), []


def render_leads(rows, today, base_url):
    leads = [r for r in rows if r["kind"] == "lead"]
    leads.sort(key=lambda r: ((r["payload"].get("city") or ""), -_num(r["payload"].get("price")), r["title"]))
    by_city = {}
    for r in leads:
        by_city[r["payload"].get("city") or "Unknown"] = by_city.get(r["payload"].get("city") or "Unknown", 0) + 1
    shown = leads[:INLINE_LIMIT]
    trs = "".join(
        f"<tr><td style='{_TD}'>{_E(str(r['payload'].get('city') or ''))}</td><td style='{_TD}'><b>{_E(str(r['payload'].get('addr') or ''))}</b><br>"
        f"<span style='color:#64748b;font-size:11px;'>{_E(r['title'])}</span></td><td style='{_TD}'>{_E((r['detail'] or '')[:140])}</td>"
        f"<td style='{_TD}white-space:nowrap;'>{_E(str(r['payload'].get('score') or ''))} &pound;{_E(str(r['payload'].get('price') or ''))}"
        + (f"<br><a href='{_E(r['link'])}' style='color:#059669;font-size:11px;'>WhatsApp</a>" if r["link"] else "") + "</td></tr>" for r in shown)
    body = ("<p style='font-size:13px;margin:0 0 10px;'>" + ", ".join(f"<b>{_E(c)}</b>: {n}" for c, n in sorted(by_city.items())) + "</p>"
            "<table style='width:100%;border-collapse:collapse;'><tr>" + "".join(f"<th style='{_TH}'>{h}</th>" for h in ("City", "Lead", "Description", "Grade / value")) + "</tr>" + trs + "</table>")
    attachments = []
    if len(leads) > INLINE_LIMIT:
        body += (f"<p style='font-size:13px;margin:12px 0 0;'><b>Showing {INLINE_LIMIT} of {len(leads)} leads.</b> The attached CSV "
                 f"(new_leads_{today}.csv) lists all {len(leads)}, each once.</p>")
        attachments.append(_csv_attachment(f"new_leads_{today}.csv", ["ref", "city", "address", "grade", "value_gbp", "description", "link"],
                                           [[r["title"], r["payload"].get("city"), r["payload"].get("addr"), r["payload"].get("score"), r["payload"].get("price"), r["detail"], r["link"]] for r in leads]))
    if base_url:
        body += f"<p style='font-size:13px;margin:12px 0 0;'><a href='{_E(base_url)}' style='color:#059669;font-weight:700;'>Open the dashboard</a></p>"
    n = len(leads)
    return (f"TreeKey daily - {n} new lead{'s' if n != 1 else ''}", _wrap("New leads - daily digest", f"{n} new lead{'s' if n != 1 else ''} across {len(by_city)} area(s)", body, base_url), attachments)


def render(topic, rows, warnings, today, base_url):
    live = [r for r in rows if r["kind"] != "resolved"]
    if topic == "LEADS":
        return render_leads(rows, today, base_url) if any(r["kind"] == "lead" for r in rows) else (None, None, [])
    if not live and not warnings:
        return None, None, []
    return render_issues(topic, rows, warnings, base_url)


# ---------------------------------------------------------------------------------------------------------------- orchestration
def _default_sender(subject, html_body, attachments, idem_key):
    import notifications
    return notifications.send_resend_email(subject, html_body, attachments=attachments or None, idempotency_key=idem_key)


def send_topic_digest(topic, now, today, store, sender, warnings=(), base_url=""):
    if topic == "LEADS":
        backfill_leads(store)
    token = hashlib.sha1(f"{topic}{today}{now.isoformat()}{os.getpid()}{os.urandom(4).hex()}".encode()).hexdigest()
    status, rows = store.claim(topic, today, now, token)
    if status != "claimed":
        return status
    try:
        subject, body, attachments = render(topic, rows, list(warnings), today, base_url)
        if subject is None:                      # nothing worth emailing: consume silently, send NOTHING
            store.finish_ok(topic, token, today)
            return "empty"
        ids = "-".join(str(r["id"]) for r in rows)
        idem = f"digest-{topic}-{today}-{hashlib.sha1(ids.encode()).hexdigest()[:16]}"
        ok = sender(subject, body, attachments, idem)
    except Exception as e:
        log.error(f"[InternalDigest] {topic}: build/send raised {e}")
        ok, err = False, str(e)
    else:
        err = "Resend did not accept the email"
    if ok:
        try:
            store.finish_ok(topic, token, today)
        except Exception as e:
            log.error(f"[InternalDigest] {topic}: sent but could not mark sent ({e}); idempotency key protects against a duplicate")
        return "sent"
    try:
        store.finish_fail(topic, token, today, now, err)
    except Exception as e:
        log.error(f"[InternalDigest] {topic}: could not release claim ({e}); it expires after {CLAIM_TTL_MIN} min")
    return "failed"


def run_due_digests(now=None, store=None, sender=None, topics=TOPICS, base_url=None):
    """Called from the existing 20-minute scheduler tick. After 08:00 London each topic is attempted at most once a day
    (plus controlled retries after a failed send). Returns {topic: status}."""
    now = now or datetime.datetime.now(datetime.timezone.utc)
    local = to_london(now)
    store = store or _get_store()
    ensure_cutover(store)
    try:                                     # first tick after a deploy: check (or create) the queue table
        if hasattr(store, "ensure_schema"):
            store.ensure_schema()
    except Exception as e:
        log.error(f"[InternalDigest] queue table not usable (run migration 0007 as the table owner): {e}")
    flush_buffer(store)
    if local.hour < SEND_HOUR_LONDON:
        return {}
    today = local.date().isoformat()
    store = store or _get_store()
    sender = sender or _default_sender
    if base_url is None:
        base_url = os.getenv("PUBLIC_APP_URL", "").strip().rstrip("/")
    try:
        done = store.done_topics(today)
    except Exception as e:
        log.error(f"[InternalDigest] cannot read state: {e}")
        return {}
    pending = [t for t in topics if t not in done]
    if not pending:
        return {}
    try:
        recurring = store.recurring_warnings()
    except Exception:
        recurring = []
    by_topic = {}
    for w in recurring:
        by_topic.setdefault(topic_for_category(w.get("category")), []).append(w)
    out = {}
    for t in pending:
        try:
            out[t] = send_topic_digest(t, now, today, store, sender, by_topic.get(t, []), base_url)
        except Exception as e:
            log.error(f"[InternalDigest] {t} failed: {e}")
            out[t] = "error"
    log.info(f"[InternalDigest] {out}")
    return out

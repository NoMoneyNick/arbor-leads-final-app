"""
Reply opt-out suppression (ADD-ONLY).

Reads new REPLIES to outreach from Instantly's API (webhooks are not on Nick's plan), and when a reply
clearly asks to be removed:
  1. adds the address to TreeKey's existing email_suppressions table (so it is excluded from every future
     recipient query / re-import), then
  2. adds it to Instantly's workspace blocklist.

Hard rules (each has a test in tests/test_reply_optout.py):
  * ADD-ONLY. Nothing here ever deletes or updates an email_suppressions row, and the Instantly client has no
    delete call at all. Wrong suppressions are undone by hand, never by this module.
  * Anything not clearly an opt-out is NOT acted on. Ambiguous replies are saved for manual review; ordinary
    replies and auto-replies are only recorded.
  * One audit row per reply, keyed on Instantly's email id, so a reply seen twice is handled once.
  * TreeKey's own suppression is written and committed BEFORE the Instantly blocklist call, so an Instantly
    failure can never leave an opted-out address un-suppressed in TreeKey. Failed blocklist calls are retried.
  * Disabled unless REPLY_OPTOUT_ENABLED=true AND INSTANTLY_API_KEY is set; nothing is scheduled by this file.

Key needs only these Instantly scopes: emails:read and block_list_entries:create.
Reply text is NOT stored, except a short excerpt on rows flagged for review (cleared once reviewed).
"""
import datetime
import html as _html
import json
import logging
import os
import re
import urllib.error
import urllib.parse
import urllib.request

logger = logging.getLogger(__name__)

API_BASE_DEFAULT = "https://api.instantly.ai/api/v2"
ENABLED_ENV = "REPLY_OPTOUT_ENABLED"
KEY_ENV = "INSTANTLY_API_KEY"
INITIAL_LOOKBACK_DAYS = 3
OVERLAP_MINUTES = 10          # re-read a little before the saved point; the unique id makes this safe
PAGE_LIMIT = 100
MAX_PAGES = 10                # 20 requests/minute is Instantly's limit on this endpoint; we use far fewer
MAX_BLOCKLIST_ATTEMPTS = 5
MAX_CLEAR_WORDS = 40
EXCERPT_CHARS = 400
OWN_DOMAINS = ("treekey.uk", "treekey.co.uk")
REASON_SUPPRESSED = "reply_optout"

# ---------------------------------------------------------------------------------------------------------
# Classification
# ---------------------------------------------------------------------------------------------------------
CLEAR_EXACT = {"stop", "unsubscribe", "remove", "no", "no thanks", "no thank you", "not interested",
               "remove me", "opt out"}
CLEAR_PHRASES = (
    "no thanks", "no thank you", "not interested", "unsubscribe", "remove me", "remove us", "take me off",
    "take us off", "stop emailing", "stop contacting", "stop sending", "stop email", "do not contact",
    "don't contact", "dont contact", "do not email", "don't email", "dont email", "opt out", "opt me out",
    "remove my email", "remove my address", "remove from your list", "off your list", "off your mailing list",
    "please stop", "no more emails", "never contact", "leave me alone",
)
# Someone engaging, asking, or negating the phrase: not safe to treat as a plain opt-out.
INTEREST_SIGNALS = (
    "?", "interested in", "sounds good", "sounds interesting", "tell me more", "call me", "phone me", "ring me",
    "more info", "more information", "send me", "sign me up", "how much", "price", "quote", "yes", "yes please",
    "don't stop", "dont stop", "do not stop", "keep sending", "keep me", "not now", "next month", "next year",
    "maybe", "who are you", "how did you", "where did you",
)
# Data-rights / complaint wording needs a person (and has a legal clock), so it is never auto-handled.
DATA_RIGHTS_SIGNALS = (
    "gdpr", "data protection", "subject access", "erase", "erasure", "delete my data", "delete my details",
    "delete my information", "right to be forgotten", "information commissioner", "ico ", "complain",
    "solicitor", "legal action", "pecr",
)
WEAK_SIGNALS = (
    "stop", "remove", "spam", "harass", "don't want", "dont want", "do not want", "not for us", "not relevant",
    "no longer", "wrong person", "wrong company", "annoying", "mailing list",
)
AUTO_SENDERS = ("mailer-daemon", "postmaster", "no-reply", "noreply", "donotreply")
AUTO_SUBJECT_BITS = ("out of office", "automatic reply", "auto-reply", "autoreply", "undeliverable",
                     "delivery status notification", "away from")

_QUOTE_CUT = re.compile(
    r"(^\s*>)|(^\s*on .{5,200}wrote:\s*$)|(^\s*-{2,}\s*(original|forwarded) message)|(^\s*from:\s.+)|"
    r"(^\s*sent from my )|(^\s*_{5,}\s*$)", re.IGNORECASE)
_EMAIL_RE = re.compile(r"^[^@\s,;<>]+@[^@\s,;<>]+\.[^@\s,;<>]+$")


def strip_quoted(text: str) -> str:
    """Keep only what the person actually wrote: cut at the first quoted-reply / signature-device marker."""
    kept = []
    for line in (text or "").replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        if _QUOTE_CUT.search(line):
            break
        kept.append(line)
    return "\n".join(kept).strip()


def _normalise(text: str) -> str:
    t = (text or "").lower().replace("’", "'").replace("‘", "'").replace("“", '"').replace("”", '"')
    return re.sub(r"\s+", " ", t).strip()


def _has(text: str, needles) -> str:
    for n in needles:
        if n == "?":
            if "?" in text:
                return "?"
        elif n.endswith(" "):
            if re.search(r"\b" + re.escape(n.strip()) + r"\b", text):
                return n.strip()
        elif re.search(r"(?<![a-z])" + re.escape(n) + r"(?![a-z])", text):
            return n
    return ""


def _html_to_text(raw: str) -> str:
    raw = re.sub(r"(?is)<(script|style).*?</\1>", " ", raw or "")
    raw = re.sub(r"(?i)<br\s*/?>|</p>|</div>", "\n", raw)
    return _html.unescape(re.sub(r"<[^>]+>", " ", raw))


def reply_text(item: dict) -> str:
    body = item.get("body")
    if isinstance(body, dict):
        text = body.get("text") or ""
        if not text.strip() and body.get("html"):
            text = _html_to_text(body["html"])
        return text
    return body if isinstance(body, str) else ""


def classify_reply(text: str) -> tuple:
    """Returns (decision, reason). decision is one of: suppressed | review | no_action.
    Auto-replies and bounces are handled before this is called."""
    mine = _normalise(strip_quoted(text))
    if not mine:
        return "no_action", "empty reply"
    right = _has(mine, DATA_RIGHTS_SIGNALS)
    if right:
        return "review", f"data-rights or complaint wording: '{right}'"
    bare = mine.strip(" .!,'\"")
    if bare in CLEAR_EXACT:
        return "suppressed", f"clear opt-out: '{bare}'"
    words = len(mine.split())
    phrase = _has(mine, CLEAR_PHRASES)
    if phrase:
        # "not interested in X" is an opt-out, not interest: neutralise negated forms before looking for interest
        interest = _has(re.sub(r"(?:not|n't|never) interested in", "not interested", mine), INTEREST_SIGNALS)
        if interest:
            return "review", f"opt-out wording ('{phrase}') mixed with '{interest}'"
        if words > MAX_CLEAR_WORDS:
            return "review", f"opt-out wording ('{phrase}') in a long reply ({words} words)"
        return "suppressed", f"clear opt-out: '{phrase}'"
    weak = _has(mine, WEAK_SIGNALS)
    if weak:
        return "review", f"possible removal wording: '{weak}'"
    return "no_action", "ordinary reply"


def decide(item: dict) -> tuple:
    """Full decision for one Instantly 'received' email -> (decision, reason, lead_email, excerpt).
    decision: suppressed | review | no_action | ignored_auto"""
    lead = str(item.get("lead") or "").strip().lower()
    sender = str(item.get("from_address_email") or "").strip().lower()
    eaccount = str(item.get("eaccount") or "").strip().lower()
    subject = _normalise(str(item.get("subject") or ""))
    if item.get("is_auto_reply") in (1, True, "1"):
        return "ignored_auto", "flagged auto-reply by Instantly", lead, ""
    if any(sender.startswith(s) for s in AUTO_SENDERS) or any(b in subject for b in AUTO_SUBJECT_BITS):
        return "ignored_auto", "auto-reply or bounce", lead, ""
    text = reply_text(item)
    excerpt = strip_quoted(text)[:EXCERPT_CHARS]
    if not _EMAIL_RE.match(lead):
        return "review", "reply has no usable lead address", lead, excerpt
    if lead.split("@", 1)[1] in OWN_DOMAINS or lead == eaccount:
        return "no_action", "own address", lead, ""
    decision, reason = classify_reply(text)
    if decision == "suppressed" and sender and sender not in (lead, eaccount) and _EMAIL_RE.match(sender):
        # Someone other than the lead answered. Unclear whose wish this is -> a person decides.
        return "review", f"reply came from a different address ({sender}); {reason}", lead, excerpt
    return decision, reason, lead, (excerpt if decision == "review" else "")


# ---------------------------------------------------------------------------------------------------------
# Instantly client (read replies, ADD to blocklist -- there is deliberately no delete method)
# ---------------------------------------------------------------------------------------------------------
class InstantlyError(Exception):
    def __init__(self, message, status=0):
        super().__init__(message)
        self.status = status


class InstantlyClient:
    def __init__(self, api_key: str, base: str = None, timeout: int = 30):
        self._key = api_key
        self.base = (base or os.getenv("INSTANTLY_API_BASE") or API_BASE_DEFAULT).rstrip("/")
        self.timeout = timeout

    def _call(self, method: str, path: str, body: dict = None):
        data = json.dumps(body).encode() if body is not None else None
        headers = {"Authorization": "Bearer " + self._key, "Accept": "application/json",
                   "User-Agent": "treekey-reply-optout/1"}
        if data is not None:           # only declare JSON when there IS a body (an empty one can be rejected)
            headers["Content-Type"] = "application/json"
        req = urllib.request.Request(self.base + path, data=data, method=method, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as r:
                raw = r.read().decode("utf-8", "replace")
                return json.loads(raw) if raw else {}
        except urllib.error.HTTPError as e:
            detail = ""
            try:
                detail = e.read().decode("utf-8", "replace")[:200]
            except Exception:
                pass
            hint = {401: "API key rejected or revoked", 402: "Instantly workspace has no active paid plan",
                    429: "Instantly rate limit"}.get(e.code, "")
            raise InstantlyError(f"HTTP {e.code} {hint} {detail}".strip(), e.code)
        except Exception as e:
            raise InstantlyError(f"network error: {type(e).__name__}", 0)

    def list_received(self, min_timestamp_iso: str, starting_after: str = None) -> dict:
        q = {"email_type": "received", "limit": PAGE_LIMIT, "sort_order": "asc",
             "min_timestamp_created": min_timestamp_iso}
        if starting_after:
            q["starting_after"] = starting_after
        return self._call("GET", "/emails?" + urllib.parse.urlencode(q))

    def add_to_blocklist(self, email: str) -> None:
        self._call("POST", "/block-lists-entries", {"bl_value": email})


# ---------------------------------------------------------------------------------------------------------
# Schema + audit
# ---------------------------------------------------------------------------------------------------------
def init_reply_optout_schema(cur) -> None:
    cur.execute("""
        CREATE TABLE IF NOT EXISTS outreach_reply_events (
            instantly_email_id TEXT PRIMARY KEY,
            lead_email TEXT,
            replied_at TIMESTAMPTZ,
            decision TEXT NOT NULL,
            reason TEXT,
            suppression_added BOOLEAN NOT NULL DEFAULT FALSE,
            blocklist_status TEXT,
            blocklist_attempts INT NOT NULL DEFAULT 0,
            last_error TEXT,
            review_status TEXT,
            review_excerpt TEXT,
            reviewed_at TIMESTAMPTZ,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        );
        CREATE INDEX IF NOT EXISTS idx_outreach_reply_events_review ON outreach_reply_events(decision, review_status);
        CREATE TABLE IF NOT EXISTS outreach_reply_state (
            id INT PRIMARY KEY,
            last_seen TIMESTAMPTZ,
            last_run_at TIMESTAMPTZ,
            last_run_status TEXT,
            last_error TEXT
        );
    """)


def is_enabled() -> bool:
    return os.getenv(ENABLED_ENV, "").strip().lower() == "true"


def _iso(dt: datetime.datetime) -> str:
    return dt.astimezone(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _parse_ts(value) -> datetime.datetime:
    s = str(value or "").strip().replace("Z", "+00:00")
    try:
        d = datetime.datetime.fromisoformat(s)
    except ValueError:
        return None
    return d if d.tzinfo else d.replace(tzinfo=datetime.timezone.utc)


def _get_state(cur):
    cur.execute("SELECT last_seen FROM outreach_reply_state WHERE id = 1;")
    row = cur.fetchone()
    return _parse_ts(row[0]) if row and row[0] else None


def _save_state(cur, *, last_seen, status, error):
    cur.execute("""
        INSERT INTO outreach_reply_state (id, last_seen, last_run_at, last_run_status, last_error)
        VALUES (1, %s::timestamptz, NOW(), %s, %s)
        ON CONFLICT (id) DO UPDATE SET
            last_seen = COALESCE(EXCLUDED.last_seen, outreach_reply_state.last_seen),
            last_run_at = NOW(), last_run_status = EXCLUDED.last_run_status, last_error = EXCLUDED.last_error;
    """, (_iso(last_seen) if last_seen else None, status, (error or "")[:300] or None))


def add_suppression(cur, email: str) -> None:
    """ADD-ONLY write into the existing email_suppressions table (same shape database.add_email_suppression uses)."""
    cur.execute("INSERT INTO email_suppressions (email, reason) VALUES (%s, %s) ON CONFLICT (email) DO NOTHING;",
                (email.strip().lower(), REASON_SUPPRESSED))


def _blocklist(conn, cur, client, event_id: str, email: str) -> bool:
    """Try the Instantly blocklist for an already-suppressed address; record the outcome. Never raises."""
    try:
        client.add_to_blocklist(email)
        status, err = "added", None
    except InstantlyError as e:
        status, err = "failed", str(e)[:250]
    except Exception as e:  # defensive: a blocklist problem must never stop suppression
        status, err = "failed", f"{type(e).__name__}"
    cur.execute("""UPDATE outreach_reply_events
                   SET blocklist_status = %s, blocklist_attempts = blocklist_attempts + 1, last_error = %s
                   WHERE instantly_email_id = %s;""", (status, err, event_id))
    conn.commit()
    return status == "added"


# ---------------------------------------------------------------------------------------------------------
# The check
# ---------------------------------------------------------------------------------------------------------
def run_check(conn, client, *, now: datetime.datetime = None) -> dict:
    """One pass. Returns a report dict. Raises nothing for Instantly problems (returned in report['error']);
    a TreeKey database failure stops the pass WITHOUT advancing the saved point, so the reply is re-read."""
    now = now or datetime.datetime.now(datetime.timezone.utc)
    cur = conn.cursor()
    rep = {"seen": 0, "duplicates": 0, "suppressed": 0, "review": 0, "no_action": 0, "ignored_auto": 0,
           "blocklist_failed": 0, "retried": 0, "data_rights": 0, "error": None}
    last_seen = _get_state(cur)
    start = (last_seen - datetime.timedelta(minutes=OVERLAP_MINUTES)) if last_seen \
        else now - datetime.timedelta(days=INITIAL_LOOKBACK_DAYS)
    newest = last_seen
    try:
        # 1. Retry blocklist additions that failed earlier (TreeKey suppression already exists for these).
        cur.execute("""SELECT instantly_email_id, lead_email FROM outreach_reply_events
                       WHERE suppression_added AND blocklist_status = 'failed' AND blocklist_attempts < %s
                       ORDER BY created_at LIMIT 20;""", (MAX_BLOCKLIST_ATTEMPTS,))
        for event_id, email in cur.fetchall():
            rep["retried"] += 1
            _blocklist(conn, cur, client, event_id, email)

        # 2. Read new replies, oldest first.
        after, pages = None, 0
        while pages < MAX_PAGES:
            pages += 1
            page = client.list_received(_iso(start), after)
            items = page.get("items") or []
            for item in items:
                event_id = str(item.get("id") or "").strip()
                ts = _parse_ts(item.get("timestamp_created")) or now
                if not event_id or item.get("ue_type") not in (None, 2, "2"):
                    continue
                rep["seen"] += 1
                decision, reason, lead, excerpt = decide(item)
                cur.execute("""INSERT INTO outreach_reply_events
                                 (instantly_email_id, lead_email, replied_at, decision, reason, review_status, review_excerpt)
                               VALUES (%s, %s, %s::timestamptz, %s, %s, %s, %s)
                               ON CONFLICT (instantly_email_id) DO NOTHING RETURNING instantly_email_id;""",
                            (event_id, lead or None, _iso(ts), decision, (reason or "")[:300],
                             "pending" if decision == "review" else None, excerpt or None))
                fresh = cur.fetchone() is not None
                if not fresh:
                    rep["duplicates"] += 1
                else:
                    if decision == "suppressed":
                        add_suppression(cur, lead)
                        cur.execute("UPDATE outreach_reply_events SET suppression_added = TRUE WHERE instantly_email_id = %s;",
                                    (event_id,))
                    conn.commit()            # TreeKey's own record is durable before Instantly is contacted
                    rep[decision] += 1
                    if decision == "review" and reason.startswith("data-rights"):
                        rep["data_rights"] += 1
                    if decision == "suppressed" and not _blocklist(conn, cur, client, event_id, lead):
                        rep["blocklist_failed"] += 1
                newest = ts if (newest is None or ts > newest) else newest
            after = page.get("next_starting_after")
            if not after or len(items) < PAGE_LIMIT:
                break
        _save_state(cur, last_seen=newest, status="ok", error=None)
        conn.commit()
    except InstantlyError as e:
        conn.rollback()
        rep["error"] = f"Instantly: {e}"
    except Exception as e:
        conn.rollback()
        rep["error"] = f"TreeKey database or code error: {type(e).__name__}: {str(e)[:200]}"
    if rep["error"]:
        try:
            # keep the saved point where it was; only the status is updated
            _save_state(cur, last_seen=None, status="error", error=rep["error"])
            conn.commit()
        except Exception:
            conn.rollback()
    cur.close()
    return rep


# ---------------------------------------------------------------------------------------------------------
# Manual review actions (still add-only)
# ---------------------------------------------------------------------------------------------------------
def review_suppress(conn, client, event_id: str) -> str:
    """Person decided a flagged reply IS an opt-out: add the suppression (+ blocklist). Returns a short status."""
    cur = conn.cursor()
    cur.execute("SELECT lead_email, decision, review_status FROM outreach_reply_events WHERE instantly_email_id = %s;",
                (event_id,))
    row = cur.fetchone()
    if not row or row[1] != "review" or not row[0]:
        cur.close()
        return "not_found"
    if row[2] != "pending":
        cur.close()
        return "already_reviewed"
    email = row[0]
    add_suppression(cur, email)
    cur.execute("""UPDATE outreach_reply_events SET suppression_added = TRUE, review_status = 'suppressed',
                   reviewed_at = NOW(), review_excerpt = NULL WHERE instantly_email_id = %s;""", (event_id,))
    conn.commit()
    if client:
        ok = _blocklist(conn, cur, client, event_id, email)
    else:   # no key configured: mark for the automatic retry once one is
        cur.execute("UPDATE outreach_reply_events SET blocklist_status = 'failed', last_error = 'no API key set' "
                    "WHERE instantly_email_id = %s;", (event_id,))
        conn.commit()
        ok = False
    cur.close()
    return "suppressed" if ok else "suppressed_blocklist_pending"


def review_dismiss(conn, event_id: str) -> str:
    """Person decided no action is needed. Adds nothing and removes nothing."""
    cur = conn.cursor()
    cur.execute("""UPDATE outreach_reply_events SET review_status = 'dismissed', reviewed_at = NOW(), review_excerpt = NULL
                   WHERE instantly_email_id = %s AND decision = 'review' AND review_status = 'pending'
                   RETURNING instantly_email_id;""", (event_id,))
    done = cur.fetchone() is not None
    conn.commit()
    cur.close()
    return "dismissed" if done else "not_found"


def build_client():
    key = os.getenv(KEY_ENV, "").strip()
    return InstantlyClient(key) if key else None


def alert_if_needed(report: dict) -> None:
    """Failures are emailed through the existing incident alert (throttled by it). Never raises."""
    try:
        import notifications
        if report.get("error"):
            notifications.send_system_incident_alert(
                category="REPLY_OPTOUT", title="Reply opt-out check failed",
                description=report["error"],
                impact="New 'please remove me' replies are not being added to the suppression list until this works.",
                action_required="Check INSTANTLY_API_KEY and Instantly plan status, then open /admin/reply-optouts.",
                severity="CRITICAL", throttle_hours=6.0)
        elif report.get("data_rights"):
            notifications.send_system_incident_alert(
                category="REPLY_OPTOUT", title="Outreach reply mentions data rights or a complaint",
                description=f"{report['data_rights']} new reply(ies) use data-protection or complaint wording. Nothing was done automatically.",
                impact="Data-rights requests have a legal deadline (one month).",
                action_required="Open /admin/reply-optouts, read the reply and handle it.",
                severity="CRITICAL", throttle_hours=24.0)
        elif report.get("blocklist_failed"):
            notifications.send_system_incident_alert(
                category="REPLY_OPTOUT", title="Instantly blocklist add failed",
                description=f"{report['blocklist_failed']} address(es) are suppressed in TreeKey but not yet on Instantly's blocklist.",
                impact="TreeKey will not re-import them; Instantly's own list is behind. Retries run automatically.",
                action_required="Open /admin/reply-optouts to see which; check the Instantly key if it persists.",
                severity="CRITICAL", throttle_hours=6.0)
    except Exception as e:
        logger.error(f"[ReplyOptout] could not send failure alert: {e}")

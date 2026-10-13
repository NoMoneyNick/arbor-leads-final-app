"""login_link_limits.py -- persistent, race-safe limits on emailed login links (13 Oct 2026).

Why: /api/request-magic-link and /api/signup both email a link to any address typed in. The only limit was an
in-memory per-IP counter (resets on every restart). About 99% of the 1,098 login records made up to 13 Oct went to
addresses with no account, up to 179 a day, against a small Resend allowance.

How: contractor_auth_tokens is the counter. A row is inserted ONLY for a request that passed every limit, in ONE
statement (INSERT ... SELECT ... WHERE counts are under the limits) run while holding a Postgres advisory lock, so two
simultaneous requests cannot both read the same count and both insert. Row count == emails we allowed.
The limits ignore whether an address has an account, so a throttle never reveals account existence.

These limits cover SIGN-IN / SIGN-UP emails only. Other TreeKey email (receipts, lead emails, alerts) also uses the
Resend allowance and is NOT counted here, so the site-wide numbers below must stay well under the plan's daily allowance.
Do not raise them automatically for launch: change LOGIN_LINK_ALL_PER_DAY / LOGIN_LINK_ALL_PER_HOUR in Render on purpose.

No third-party imports (unit-testable anywhere). Env vars override the defaults without a code change.
"""
import hashlib
import os

# name: (env var, default)
_LIMIT_SPEC = {
    "email_15m": ("LOGIN_LINK_EMAIL_PER_15MIN", 3),    # same address
    "email_day": ("LOGIN_LINK_EMAIL_PER_DAY", 8),
    "ip_hour":   ("LOGIN_LINK_IP_PER_HOUR", 10),       # same visitor address (hashed). Best effort, see README
    "ip_day":    ("LOGIN_LINK_IP_PER_DAY", 30),
    "all_hour":  ("LOGIN_LINK_ALL_PER_HOUR", 20),      # whole site, sign-in/sign-up emails only
    "all_day":   ("LOGIN_LINK_ALL_PER_DAY", 60),
}
_ORDER = ("email_15m", "email_day", "ip_hour", "ip_day", "all_hour", "all_day")

LOCK_SQL = "SELECT pg_advisory_xact_lock(hashtext('login_link_limits'))"

_COUNTS_SELECT = """
  SELECT
    COUNT(*) FILTER (WHERE customer_email = %(email)s AND created_at > NOW() - INTERVAL '15 minutes') AS email_15m,
    COUNT(*) FILTER (WHERE customer_email = %(email)s) AS email_day,
    COUNT(*) FILTER (WHERE requester_ip_hash = %(ip)s AND created_at > NOW() - INTERVAL '1 hour') AS ip_hour,
    COUNT(*) FILTER (WHERE requester_ip_hash = %(ip)s) AS ip_day,
    COUNT(*) FILTER (WHERE created_at > NOW() - INTERVAL '1 hour') AS all_hour,
    COUNT(*) AS all_day
  FROM contractor_auth_tokens
  WHERE created_at > NOW() - INTERVAL '24 hours'
"""

# Read-only: used only to name the limit that stopped a request.
COUNT_SQL = _COUNTS_SELECT

# The one atomic statement. Returns a row only if EVERY limit still has room.
INSERT_IF_ALLOWED_SQL = """
WITH c AS (""" + _COUNTS_SELECT + """)
INSERT INTO contractor_auth_tokens (customer_email, token, otp_code, pending_signup, requester_ip_hash)
SELECT %(email)s, %(token)s, %(otp)s, %(pending)s, %(ip)s
FROM c
WHERE c.email_15m < %(l_email_15m)s AND c.email_day < %(l_email_day)s
  AND c.ip_hour < %(l_ip_hour)s AND c.ip_day < %(l_ip_day)s
  AND c.all_hour < %(l_all_hour)s AND c.all_day < %(l_all_day)s
RETURNING token, otp_code, expires_at
"""


def get_limits(environ=None) -> dict:
    env = os.environ if environ is None else environ
    out = {}
    for key, (var, default) in _LIMIT_SPEC.items():
        try:
            val = int(str(env.get(var, "")).strip())
            out[key] = val if val >= 0 else default
        except ValueError:
            out[key] = default
    return out


def hash_ip(ip, environ=None):
    """Salted SHA-256 so raw visitor IPs are not stored. None if there is no usable address."""
    ip = (ip or "").strip()
    if not ip or ip == "unknown":
        return None
    env = os.environ if environ is None else environ
    salt = env.get("SESSION_SECRET", "") or "treekey-login-limits"
    return hashlib.sha256(f"{salt}|{ip}".encode()).hexdigest()[:32]


def insert_params(email, token, otp, pending_json, ip_hash, limits):
    p = {"email": email, "token": token, "otp": otp, "pending": pending_json, "ip": ip_hash}
    p.update({f"l_{k}": v for k, v in limits.items()})
    return p


def decide(counts: dict, limits: dict):
    """None if every limit has room, else the first full one ('email_*', then 'ip_*', then 'all_*')."""
    for key in _ORDER:
        if counts.get(key, 0) >= limits[key]:
            return key
    return None


def throttle_kind(reason):
    """'email' -> normal "check your inbox" page, nothing sent; 'busy' -> visible "try again shortly"; None -> not throttled."""
    if not reason:
        return None
    return "email" if reason.startswith("email_") else "busy"


def explain(cur, email, ip_hash, limits=None):
    """Which limit stopped this request (for the response type and the log). Read-only."""
    limits = limits or get_limits()
    cur.execute(COUNT_SQL, {"email": email, "ip": ip_hash})
    row = cur.fetchone() or (0,) * len(_ORDER)
    counts = dict(zip(_ORDER, (int(x or 0) for x in row)))
    return decide(counts, limits) or "all_day"

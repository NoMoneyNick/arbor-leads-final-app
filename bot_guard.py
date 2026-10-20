"""bot_guard.py -- stops automated sign-in / sign-up requests before any email can be sent (20 Oct 2026).

Why: every sign-in or sign-up request can cost one email, and the plan allows about 100 a day. Bots were posting
addresses straight at /api/request-magic-link and /api/signup. The volume limits in login_link_limits.py only cap the
damage (and lock real people out once full); this module stops the bots themselves.

Three layers, all checked BEFORE a login record is created or an email is sent:
  1. A hidden "trap" field that people never see or fill but simple bots do.
  2. A signed form token that must be present, must be a few seconds old (a person needs time to type; scripts
     post instantly), not too old, and must have been issued to the same visitor address. A script that posts
     straight to the URL without first loading the form has no token and is rejected.
  3. Cloudflare Turnstile (free, no puzzles for real people) when TURNSTILE_SITE_KEY and TURNSTILE_SECRET_KEY are
     set in Render. If it is set up and cannot be checked, the request is refused (fails closed) so no email is spent.

Switch everything off with BOT_GUARD=off (emergency only; tests set it). No third-party imports.
"""
import hashlib
import hmac
import html
import json
import logging
import os
import time
import urllib.parse
import urllib.request

logger = logging.getLogger("vector-data-labs")

HONEYPOT_FIELD = "tk_hp_note"
TOKEN_FIELD = "tk_form_token"
TURNSTILE_FIELD = "cf-turnstile-response"
MIN_SECONDS = 3
MAX_SECONDS = 12 * 3600
TURNSTILE_VERIFY_URL = "https://challenges.cloudflare.com/turnstile/v0/siteverify"

FAIL_MESSAGE = "We could not check that this request came from a person. Please reload the page and try again."


def enabled() -> bool:
    return os.getenv("BOT_GUARD", "").strip().lower() not in ("off", "0", "false", "no")


def _site_key() -> str:
    return os.getenv("TURNSTILE_SITE_KEY", "").strip()


def _turnstile_secret() -> str:
    return os.getenv("TURNSTILE_SECRET_KEY", "").strip()


def turnstile_enabled() -> bool:
    return bool(_site_key() and _turnstile_secret())


def _sign(secret: bytes, ts: int, ip: str) -> str:
    msg = f"botguard|{ts}|{ip}".encode()
    return hmac.new(secret, msg, hashlib.sha256).hexdigest()[:32]


def make_token(secret: bytes, ip: str, now=None) -> str:
    ts = int(now if now is not None else time.time())
    return f"{ts}.{_sign(secret, ts, ip)}"


def fields_html(secret: bytes, ip: str) -> str:
    """Hidden fields (and the Turnstile widget when configured) to place inside a <form>."""
    if not enabled():
        return ""
    token = html.escape(make_token(secret, ip), quote=True)
    out = (
        f'<div aria-hidden="true" style="position:absolute;left:-9999px;top:auto;width:1px;height:1px;overflow:hidden;">'
        f'<input type="text" name="{HONEYPOT_FIELD}" value="" tabindex="-1" autocomplete="off"></div>'
        f'<input type="hidden" name="{TOKEN_FIELD}" value="{token}">'
    )
    if turnstile_enabled():
        out += (
            '<div class="cf-turnstile" data-theme="dark" style="margin:12px 0;" '
            f'data-sitekey="{html.escape(_site_key(), quote=True)}"></div>'
            '<script src="https://challenges.cloudflare.com/turnstile/v0/api.js" async defer></script>'
        )
    return out


def _verify_turnstile(response_token: str, ip: str) -> bool:
    data = urllib.parse.urlencode({
        "secret": _turnstile_secret(), "response": response_token, "remoteip": ip,
    }).encode()
    req = urllib.request.Request(TURNSTILE_VERIFY_URL, data=data, method="POST")
    with urllib.request.urlopen(req, timeout=5) as resp:
        payload = json.loads(resp.read().decode("utf-8", "replace"))
    return bool(payload.get("success"))


def check(form, secret: bytes, ip: str, now=None):
    """Returns (ok, reason). `reason` is for the server log only; never shown to the visitor."""
    if not enabled():
        return True, "off"
    if (form.get(HONEYPOT_FIELD) or "").strip():
        return False, "honeypot"
    token = (form.get(TOKEN_FIELD) or "").strip()
    try:
        ts_text, sig = token.split(".", 1)
        ts = int(ts_text)
    except Exception:
        return False, "no_token"
    if not hmac.compare_digest(sig, _sign(secret, ts, ip)):
        return False, "bad_token"
    age = (now if now is not None else time.time()) - ts
    if age < MIN_SECONDS:
        return False, "too_fast"
    if age > MAX_SECONDS:
        return False, "token_expired"
    if turnstile_enabled():
        answer = (form.get(TURNSTILE_FIELD) or "").strip()
        if not answer:
            return False, "no_turnstile"
        try:
            if not _verify_turnstile(answer, ip):
                return False, "turnstile_failed"
        except Exception as exc:
            logger.warning(f"[BotGuard] Turnstile could not be checked; refusing request: {type(exc).__name__}")
            return False, "turnstile_unavailable"
    return True, "ok"

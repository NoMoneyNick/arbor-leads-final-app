"""
net_utils.py -- Shared HTTP resilience layer for every scraper in this
project (scanners.py, mesh_scrapers.py, research.py,
bulk_contractor_extractor.py).

WHY THIS EXISTS (Aug 2026 scraper-hardening pass -- see PROJECT_STATE.md):
Every one of those files previously called requests.get/post directly, with
TLS certificate verification disabled everywhere (verify=False) and no
retry logic at all -- a single dropped connection, timeout, or transient
5xx silently lost that one fetch for the entire run, with no second
attempt. This module retrofits both WITHOUT changing what any caller
receives: smart_get/smart_post return a normal requests.Response object
(or raise the same exception types requests already raises on final
failure), so every existing call site's `if res.status_code == 200` /
`except Exception as e:` handling keeps working completely unchanged.
Callers only need to swap `requests.get(url, ...)` for
`net_utils.smart_get(url, ...)` (and drop any `verify=False` kwarg --
this module owns that negotiation now).

WHAT ACTUALLY CHANGED, CONCRETELY:

  1. TLS is verified by default now, for the first time. Only on an actual
     SSLError does it fall back to a single unverified retry -- and only
     then does it fire a (throttled, per-domain) incident alert through the
     same notifications.send_system_incident_alert() channel already used
     for API-key/rate-limit failures elsewhere in this project. Previously
     verification was blanket-disabled everywhere, so a council portal with
     a broken cert was indistinguishable from one with a perfectly good
     cert -- both "worked", silently, with zero real security. Now the
     good ones get real verification, and the bad ones are finally visible
     by name instead of invisible-by-design.

  2. Transient failures -- a timeout, a connection error, or a
     500/502/503/504 response -- are retried with exponential backoff plus
     a little random jitter (so many councils failing at once don't all
     retry in lockstep) before giving up. Default is 2 extra attempts on
     top of the first, which is deliberately modest: this is for real
     transient network blips, not for hammering a struggling server.

  3. A 429 (rate limited) is NOT retried here -- it is returned to the
     caller immediately, untouched, exactly as requests.get() would have.
     Every call site in this project already has its own bespoke 429
     handling (stop this whole pass, alert on quota, etc.) and that logic
     is intentionally left in charge of what happens next.

WHAT THIS DOES NOT SOLVE (documented honestly, not hidden):
  - The TLS-fallback alert throttle below is in-process memory, same as
    notifications.py's own _ALERT_THROTTLE_CACHE -- it resets on every
    restart and doesn't share state across multiple instances. Fine at
    the current single-instance scale; would need a shared store (Redis,
    or a DB table) if this ever runs on more than one Render instance.
  - This only retries GET/POST calls that are safe to repeat (every call
    site this was applied to is a read-only "fetch/search" request, never
    a payment or a state-mutating submission -- those were deliberately
    left untouched and should stay that way if this module is ever reused
    elsewhere).
"""
import time
import random
import logging
import requests
from urllib.parse import urlparse

logger = logging.getLogger("vector-data-labs")

RETRYABLE_STATUS = {500, 502, 503, 504}

# Per-domain throttle for the "had to fall back to unverified TLS" alert,
# so one flaky council portal doesn't spam an alert on every single request.
_TLS_ALERT_THROTTLE = {}
_TLS_ALERT_THROTTLE_HOURS = 24.0


def _domain(url: str) -> str:
    try:
        return urlparse(url).netloc or url
    except Exception:
        return url


def _alert_tls_fallback(url: str):
    """Fires a throttled, low-severity incident alert the first time (per
    domain, per 24h) a request needed the unverified-TLS fallback. This is
    informational, not urgent -- the request still succeeded -- but it
    means a human can now actually find out which portals have a
    certificate problem, instead of it being silently papered over
    forever."""
    domain = _domain(url)
    now = time.time()
    last = _TLS_ALERT_THROTTLE.get(domain, 0)
    if now - last < _TLS_ALERT_THROTTLE_HOURS * 3600:
        return
    _TLS_ALERT_THROTTLE[domain] = now
    try:
        import notifications
        notifications.send_system_incident_alert(
            category="SCRAPER TLS FALLBACK",
            title=f"{domain} required unverified TLS fallback",
            description=(
                f"A request to {domain} failed HTTPS certificate verification "
                f"and was retried without it so the scrape could still complete. "
                f"The fetch succeeded, but this portal's certificate is likely "
                f"expired, self-signed, or missing an intermediate certificate."
            ),
            impact="No impact to this scrape -- the fallback request succeeded. Logged for visibility only.",
            action_required="No action needed unless this keeps recurring for the same domain.",
            severity="WARNING",
            throttle_hours=_TLS_ALERT_THROTTLE_HOURS
        )
    except Exception as e:
        logger.debug(f"[net_utils] Could not send TLS fallback alert for {domain}: {e}")


def _request(method: str, url: str, session=None, max_retries: int = 2, backoff_base: float = 0.6, **kwargs):
    """Core implementation behind smart_get/smart_post. Not called directly."""
    kwargs.pop("verify", None)  # this module owns the verify negotiation now
    caller = session.request if session is not None else requests.request

    verify_flag = True
    attempt = 0
    res = None

    while True:
        try:
            res = caller(method, url, verify=verify_flag, **kwargs)
        except requests.exceptions.SSLError:
            if verify_flag:
                # One-time, immediate downgrade -- doesn't consume a retry
                # slot, and can't loop (verify=False never raises SSLError).
                verify_flag = False
                _alert_tls_fallback(url)
                continue
            raise
        except (requests.exceptions.Timeout, requests.exceptions.ConnectionError):
            if attempt < max_retries:
                sleep_for = backoff_base * (2 ** attempt) + random.uniform(0, 0.3)
                logger.debug(f"[net_utils] {method} {url} raised a network error, retrying in {sleep_for:.1f}s (attempt {attempt + 1}/{max_retries})")
                time.sleep(sleep_for)
                attempt += 1
                continue
            raise

        if res.status_code in RETRYABLE_STATUS and attempt < max_retries:
            sleep_for = backoff_base * (2 ** attempt) + random.uniform(0, 0.3)
            logger.debug(f"[net_utils] {method} {url} returned {res.status_code}, retrying in {sleep_for:.1f}s (attempt {attempt + 1}/{max_retries})")
            time.sleep(sleep_for)
            attempt += 1
            continue

        return res


def smart_get(url: str, session=None, max_retries: int = 2, **kwargs):
    """Drop-in replacement for requests.get() / session.get() with
    verify-first TLS handling and retry-with-backoff on transient failures.
    Pass session=<requests.Session()> to preserve cookies/auth across calls
    (e.g. the Idox CSRF flow in mesh_scrapers.py)."""
    return _request("GET", url, session=session, max_retries=max_retries, **kwargs)


def smart_post(url: str, session=None, max_retries: int = 2, **kwargs):
    """Drop-in replacement for requests.post() / session.post(). Only use
    this for idempotent/read-only POSTs (e.g. a search form) -- it will
    retry on transient failure, which is safe for "run this search again"
    but would NOT be safe for something like a payment or a one-shot
    form submission with side effects. Every call site this was applied
    to in this project is a read-only search."""
    return _request("POST", url, session=session, max_retries=max_retries, **kwargs)

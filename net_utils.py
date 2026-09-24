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
import os
import time
import json
import random
import logging
import requests
from typing import Optional
from datetime import datetime, timezone
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
    forever.

    2026-09-22 review correction (external architecture review, relayed by
    Nick): an SSLError does not necessarily mean an expired certificate --
    a self-signed cert, a missing intermediate, a hostname mismatch, a
    TLS-version negotiation failure, or (less likely but not provably
    absent from a cert failure alone) a machine-in-the-middle are all
    possible causes this alert cannot distinguish between. The description
    below no longer asserts a specific cause. That review also flagged
    that content fetched via this fallback is currently trusted exactly
    the same as any normal fetch -- nothing here marks it for extra
    scrutiny before it can become a sellable lead. Deliberately NOT
    changed in this pass (quarantining fallback-fetched content is a real
    change to the lead-acceptance pipeline, which lives in mesh_scrapers.py
    -- not present in this repo, see ERROR_LOG.md) -- recorded honestly as
    open, not silently implemented or silently ignored."""
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
                f"The fetch succeeded, but the certificate problem's exact cause "
                f"(expired, self-signed, missing intermediate, hostname mismatch, "
                f"or something else) is not determined by this alert."
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
    # 2026-09-22 second review: optional, opt-in-per-call fixture capture --
    # see _maybe_capture_response's own module-level comment for the full
    # explanation. Popped here (before it ever reaches requests/session,
    # which would error on an unrecognised kwarg) and only acted on once,
    # against the final response actually returned below.
    capture_context = kwargs.pop("capture_context", None)
    caller = session.request if session is not None else requests.request

    # Sep 1 2026: identify ourselves on every request through this module
    # that doesn't already identify itself some other way. PlanIt's own
    # usage policy explicitly asks callers to self-identify via a
    # User-Agent with a contact email (confirmed via three independent
    # research passes -- see PROJECT_STATE.md), and PlanIt/ukplanningapi.co.uk
    # calls (scanners.py, no session, no explicit headers) were going out
    # with requests' bare default UA ("python-requests/x.y.z") -- this fills
    # that gap.
    #
    # BUG FOUND AND FIXED SAME DAY: the first version of this always built a
    # headers dict and passed it explicitly to `caller(...)`, even when
    # neither this call nor the session set one -- but requests' own
    # session-vs-per-call header merge means an EXPLICIT headers= dict on a
    # session.request() call overrides the session's own headers for any
    # key both set, not just fills gaps. mesh_scrapers.py's IdoxScraper
    # deliberately sets a realistic browser User-Agent on its session
    # (session=self.session is what every Idox council-portal call passes)
    # specifically so council WAFs/bot-protection see normal-looking
    # traffic. The first version silently clobbered that with the
    # bot-identifying "TreeKeyBot/1.0" string on every single Idox request
    # -- confirmed live: within seconds of that version deploying, Cornwall
    # and Nottingham (previously-working councils) both started throwing
    # "SCRAPER PAGE STRUCTURE"/"SCRAPER TLS FALLBACK" alerts on their very
    # first request of the run, exactly the signature of a portal blocking
    # or challenging traffic it no longer recognises as a browser. Fixed:
    # only inject the default UA when NEITHER this call's own headers NOR
    # the session's headers (if a session was passed) already set one, so
    # IdoxScraper's browser UA is left completely untouched, and only truly
    # anonymous callers (PlanIt, ukplanningapi.co.uk) pick up the new default.
    caller_headers = kwargs.pop("headers", None)
    has_explicit_ua = bool((caller_headers or {}).get("User-Agent"))
    has_session_ua = bool(session is not None and session.headers.get("User-Agent"))
    if not has_explicit_ua and not has_session_ua:
        caller_headers = dict(caller_headers or {})
        caller_headers["User-Agent"] = "TreeKeyBot/1.0 (+https://treekey.co.uk; contact@treekey.co.uk)"
    if caller_headers is not None:
        kwargs["headers"] = caller_headers

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

        if capture_context is not None:
            _maybe_capture_response(method, url, res, capture_context)
        return res


def smart_get(url: str, session=None, max_retries: int = 2, **kwargs):
    """Drop-in replacement for requests.get() / session.get() with
    verify-first TLS handling and retry-with-backoff on transient failures.
    Pass session=<requests.Session()> to preserve cookies/auth across calls
    (e.g. the Idox CSRF flow in mesh_scrapers.py).

    Pass capture_context={"council": ..., "platform": ..., "page_kind": ...,
    "search_parameters": ...} to additionally save this response as an
    unreviewed fixture -- see _maybe_capture_response's own module-level
    comment. Omit it (the default) for zero change in behaviour -- this is
    opt-in per call site, never automatic."""
    return _request("GET", url, session=session, max_retries=max_retries, **kwargs)


# ---------------------------------------------------------------------------
# Response classification -- 2026-09-22 second review (Astra, relayed by
# Nick). Astra's first review pointed out this project had no shared way to
# tell "this page is fine, parse it", "this is a rate limit", "this is a
# bot-mitigation challenge", etc. apart -- every failure mode got funneled
# into the same generic error handling, which is exactly how a Cloudflare
# challenge page or an auth-wall page could silently get "parsed" as if it
# were a normal empty search result. This is that classifier.
#
# SCOPE, STATED HONESTLY: this classifies on RESPONSE-SHAPE signals only --
# HTTP status code and a small set of response headers -- because that's all
# this module can know without mesh_scrapers.py's actual Idox page markup,
# which is not present in this repo (see ERROR_LOG.md). It deliberately does
# NOT try to guess what a real Idox "no results" page or a real Idox
# "application detail" page looks like from body text alone -- that would be
# fabricating knowledge this codebase doesn't have. VALID_EMPTY is reachable
# only via the explicit known_no_results hint below, for a future caller
# that DOES have real page knowledge (mesh_scrapers.py) to supply it -- this
# module never infers it on its own.
# ---------------------------------------------------------------------------

RESPONSE_CLASSIFICATIONS = (
    "PAGE_OK", "VALID_EMPTY", "RATE_LIMITED", "CHALLENGED",
    "AUTH_REQUIRED", "SERVER_ERROR", "UNRECOGNISED_PAGE",
)

# Cloudflare's own documented signal for a bot-mitigation challenge
# response (a doc link was included in Astra's review) -- checked in
# preference to matching challenge-page body text/title, which is fragile
# and can change without notice; a header is a much more stable contract.
_CF_CHALLENGE_HEADER = "cf-mitigated"
_CF_CHALLENGE_VALUE = "challenge"


def _header_ci(headers: Optional[dict], name: str) -> Optional[str]:
    """Case-insensitive header lookup -- a plain dict (as loaded from a
    fixture's meta.json) has whatever case it was saved with; a real
    requests.Response.headers is already case-insensitive, but this keeps
    classify_response usable against either."""
    if not headers:
        return None
    name_lower = name.lower()
    for k, v in headers.items():
        if k.lower() == name_lower:
            return v
    return None


def classify_response(*, status_code: int, headers: Optional[dict] = None,
                       body_text: Optional[str] = None, known_no_results: bool = False) -> str:
    """Classifies one HTTP response into RESPONSE_CLASSIFICATIONS, using
    only the response shape (status code, a couple of headers) -- see the
    module-level comment above for why it deliberately goes no further
    than that.

    known_no_results: an explicit hint, defaulting to False, for a future
    caller that has actually parsed the page and confirmed it's a valid
    "zero results this search" response (as opposed to an error page that
    merely happens to return 200) -- this function has no way to determine
    that on its own from status/headers alone, so it never guesses; a
    caller with real page knowledge (mesh_scrapers.py, once present) is
    what would set this.

    Order matters below: the Cloudflare challenge check runs first because
    Cloudflare can return a challenge on a status code (403, sometimes even
    200) that would otherwise be classified some other way -- the header is
    the more specific, more reliable signal, so it wins."""
    if _header_ci(headers, _CF_CHALLENGE_HEADER) == _CF_CHALLENGE_VALUE:
        return "CHALLENGED"
    if status_code == 429:
        return "RATE_LIMITED"
    if status_code in (401, 403):
        return "AUTH_REQUIRED"
    if status_code in RETRYABLE_STATUS:  # 500, 502, 503, 504
        return "SERVER_ERROR"
    if status_code == 200:
        if body_text is not None and not body_text.strip():
            # A 200 with an empty body is never a normal page -- something
            # upstream (a proxy, a WAF) is doing something unexpected.
            return "UNRECOGNISED_PAGE"
        if known_no_results:
            return "VALID_EMPTY"
        return "PAGE_OK"
    return "UNRECOGNISED_PAGE"


# ---------------------------------------------------------------------------
# Optional capture hook -- 2026-09-22 second review (Astra, relayed by
# Nick): "the cheapest accurate way to bootstrap real fixtures" is to save
# real responses from the existing shared HTTP path, before parsing, rather
# than hand-writing fixtures that guess at real Idox page structure. This is
# that hook.
#
# OFF BY DEFAULT, TWICE OVER:
#   1. It only ever runs for a call that explicitly passes
#      capture_context=... to smart_get/smart_post -- every existing call
#      site in this codebase keeps working completely unchanged (no
#      capture_context = no capturing, no behaviour change, no new
#      dependency). This is deliberately opt-in per call site, not a global
#      "capture everything" switch -- most traffic through this module
#      (PlanIt, ukplanningapi.co.uk) has nothing to do with the Idox
#      fixture work this exists for.
#   2. NET_UTILS_DISABLE_CAPTURE=1 (or "true") in the environment forces
#      capturing off everywhere regardless of what any call site passes --
#      an emergency kill switch in case capture_context is ever left
#      switched on somewhere it shouldn't be (e.g. accidentally shipped to
#      a production deploy).
#
# WHAT IT NEVER WRITES: expected.json. A captured response only ever
# produces body.html + meta.json (with "provenance": "captured" and a
# sanitisation note saying it's unreviewed) in NET_UTILS_CAPTURE_DIR
# (default "captured_responses/", NOT inside tests/fixtures/idox/ directly)
# -- a human has to review the body for anything sensitive, decide the
# correct classification, write expected.json by hand, and move the
# reviewed pair into tests/fixtures/idox/<council>/<case_name>/ themselves.
# See tests/fixtures/idox/README.md's own "Where real fixtures will come
# from" section. This function never promotes anything into the trusted
# fixture set on its own.
#
# FAILS SAFE: any error while capturing (disk full, permissions, an
# unexpected header type) is caught and logged, never raised -- a broken
# capture must never take down or corrupt the real fetch it's piggybacking
# on, which has already succeeded by the time this runs.
# ---------------------------------------------------------------------------

DEFAULT_CAPTURE_DIR = "captured_responses"
# Only these headers are kept -- see meta.json's own schema note in
# tests/fixtures/idox/README.md on why (classification-relevant only, not a
# verbatim dump that could capture something sensitive in an unrelated
# header value).
_CAPTURE_HEADER_ALLOWLIST = ("content-type", "cf-mitigated", "retry-after", "www-authenticate")
# Only capture bodies that are actually text -- skip images/binaries/etc.
# so this can't silently try to write gigabytes of binary content as if it
# were an HTML fixture.
_CAPTURE_TEXTUAL_CONTENT_TYPES = ("text/", "application/json", "application/xhtml")


def _capture_disabled_by_env() -> bool:
    return os.environ.get("NET_UTILS_DISABLE_CAPTURE", "").strip().lower() in ("1", "true", "yes")


def _slugify(value: str) -> str:
    # Deliberately does NOT strip leading/trailing underscores -- the
    # "_uncategorized"/"_synthetic" council names (see
    # tests/fixtures/idox/README.md) rely on that leading underscore to
    # stay visually unmistakable from a real council name.
    slug = "".join(c if (c.isalnum() or c in ("-", "_")) else "_" for c in value)
    return slug or "unknown"


def _maybe_capture_response(method: str, url: str, res, capture_context: Optional[dict]) -> Optional[str]:
    """Writes body.html + meta.json for one response under
    NET_UTILS_CAPTURE_DIR/<council>/<case_name>/, if (and only if)
    capture_context was supplied and the env kill switch isn't set. Returns
    the directory written, or None if capturing didn't happen (disabled,
    no context, non-textual content, or any error -- indistinguishable on
    purpose, since callers should never depend on this for correctness)."""
    if not capture_context or _capture_disabled_by_env():
        return None
    try:
        content_type = (res.headers.get("Content-Type") or "").lower()
        if content_type and not any(content_type.startswith(t) for t in _CAPTURE_TEXTUAL_CONTENT_TYPES):
            logger.debug(f"[net_utils] Skipping capture of {url} -- non-textual Content-Type {content_type!r}")
            return None

        council = capture_context.get("council") or "_uncategorized"
        platform = capture_context.get("platform") or "unknown"
        page_kind = capture_context.get("page_kind") or "unknown"
        search_parameters = capture_context.get("search_parameters")

        captured_at = datetime.now(timezone.utc)
        case_name = f"{_slugify(platform)}_{captured_at.strftime('%Y%m%dT%H%M%S%fZ')}_{res.status_code}"
        base_dir = os.environ.get("NET_UTILS_CAPTURE_DIR", DEFAULT_CAPTURE_DIR)
        dest_dir = os.path.join(base_dir, _slugify(council), case_name)
        os.makedirs(dest_dir, exist_ok=True)

        with open(os.path.join(dest_dir, "body.html"), "w", encoding="utf-8") as f:
            f.write(res.text or "")

        kept_headers = {k: v for k, v in res.headers.items() if k.lower() in _CAPTURE_HEADER_ALLOWLIST}
        meta = {
            "fixture_version": 1,
            "provenance": "captured",
            "captured_at": captured_at.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "platform": platform,
            "council": council,
            "page_kind": page_kind,
            "capture_type": "classifier_fixture",
            "request": {"method": method, "url": url, "search_parameters": search_parameters},
            "response": {
                "final_url": res.url,
                "status": res.status_code,
                "headers": kept_headers,
                "redirects": [r.url for r in res.history],
                "encoding": res.encoding,
                "tls_verified": bool(capture_context.get("tls_verified", True)),
            },
            "sanitisation": {
                "applied": False,
                "notes": (
                    "Auto-captured by net_utils.py's capture hook -- NOT yet reviewed. A human must "
                    "check body.html for anything sensitive, decide the correct classification, write "
                    "expected.json, and move this pair into tests/fixtures/idox/<council>/<case_name>/ "
                    "before it's a trusted fixture. See tests/fixtures/idox/README.md."
                ),
            },
        }
        with open(os.path.join(dest_dir, "meta.json"), "w", encoding="utf-8") as f:
            json.dump(meta, f, indent=2)

        logger.info(f"[net_utils] Captured response for {url} -> {dest_dir} (unreviewed, no expected.json)")
        return dest_dir
    except Exception as e:
        logger.warning(f"[net_utils] Capture hook failed for {url} (fetch itself was unaffected): {e}")
        return None


def smart_post(url: str, session=None, max_retries: int = 2, **kwargs):
    """Drop-in replacement for requests.post() / session.post(). Only use
    this for idempotent/read-only POSTs (e.g. a search form) -- it will
    retry on transient failure, which is safe for "run this search again"
    but would NOT be safe for something like a payment or a one-shot
    form submission with side effects. Every call site this was applied
    to in this project is a read-only search.

    Also accepts capture_context=... -- see smart_get's own docstring."""
    return _request("POST", url, session=session, max_retries=max_retries, **kwargs)

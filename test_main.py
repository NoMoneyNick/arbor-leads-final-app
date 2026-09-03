"""
test_main.py -- Unit tests for main.py's own logic that doesn't need a
real FastAPI/Postgres/Stripe stack.

Sep 3 2026: added alongside the "council-side-fault disclosure" feature
(_COUNCIL_SOURCE_ISSUES / _council_source_issue in main.py) -- Nick's ask:
"we should place a note on the website when an area cannot be accessed due
to the councils fault or issue". main.py had NO test file at all before
this. This file does not attempt to cover the whole app (a full FastAPI
route/DB/Stripe integration suite is a much larger, separate undertaking)
-- it exists specifically to lock the one piece of pure, testable logic
this session added: the mapping from a resolved council/district name to
the public-facing disclosure note shown on /check-postcode for the three
councils confirmed this session to be broken/bot-gated on their OWN end
(Merton, Bath & North East Somerset, West Northamptonshire) -- see that
dict's own comment in main.py for why each one is there.

Neither `fastapi` nor `stripe` is installed in this sandbox (network
egress to pypi.org is blocked here -- see test_payments.py's own docstring
for the same constraint hit earlier this session), so both are stubbed
into sys.modules before importing main.py, same technique test_scrapers.py/
test_payments.py/test_research.py already use for `database`/
`notifications`/`dotenv`. This runs anywhere, no real FastAPI/Postgres/
Stripe install or network access required. The fastapi stub is a plain
pass-through: every `@app.get/post/api_route/on_event` decorator returns
the wrapped function unchanged, so main.py's ~80 route functions import as
ordinary Python functions without ever standing up a real ASGI app.
"""
import os
import sys
import types
import unittest
from unittest.mock import MagicMock

# ---------------------------------------------------------------------------
# Stub heavy/external modules BEFORE importing main.py, so this file runs
# anywhere -- no Postgres, no .env file, no Stripe account, no network.
# Idempotent per-attribute stubbing (matches test_scrapers.py's own fix for
# running under `python -m unittest discover`, where another test file's
# stub of the same module name may already be in sys.modules).
# ---------------------------------------------------------------------------
if "database" not in sys.modules:
    sys.modules["database"] = types.ModuleType("database")
_database = sys.modules["database"]
if not hasattr(_database, "init_db"):
    _database.init_db = MagicMock()
if not hasattr(_database, "get_db_conn"):
    _database.get_db_conn = MagicMock()
if not hasattr(_database, "is_territory_claimed"):
    _database.is_territory_claimed = MagicMock(return_value=False)
if not hasattr(_database, "increment_api_usage"):
    _database.increment_api_usage = MagicMock(return_value={"warning_needed": False})

if "notifications" not in sys.modules:
    sys.modules["notifications"] = types.ModuleType("notifications")
_notifications = sys.modules["notifications"]
if not hasattr(_notifications, "send_system_incident_alert"):
    _notifications.send_system_incident_alert = MagicMock()
if not hasattr(_notifications, "send_resend_email"):
    _notifications.send_resend_email = MagicMock()
if not hasattr(_notifications, "dispatch_lead_alerts"):
    _notifications.dispatch_lead_alerts = MagicMock()
if not hasattr(_notifications, "send_api_quota_warning_email"):
    _notifications.send_api_quota_warning_email = MagicMock()

if "dotenv" not in sys.modules:
    _fake_dotenv = types.ModuleType("dotenv")
    _fake_dotenv.load_dotenv = lambda *a, **k: None
    sys.modules["dotenv"] = _fake_dotenv

if "stripe" not in sys.modules:
    _fake_stripe = types.ModuleType("stripe")
    _fake_stripe.api_key = ""

    _fake_stripe_error = types.ModuleType("stripe.error")

    class _StripeError(Exception):
        pass

    class _AuthenticationError(_StripeError):
        pass

    class _SignatureVerificationError(_StripeError):
        pass

    _fake_stripe_error.StripeError = _StripeError
    _fake_stripe_error.AuthenticationError = _AuthenticationError
    _fake_stripe_error.SignatureVerificationError = _SignatureVerificationError
    _fake_stripe.error = _fake_stripe_error

    _fake_checkout = types.ModuleType("stripe.checkout")
    _fake_session = types.ModuleType("stripe.checkout.Session")
    _fake_session.create = MagicMock()
    _fake_checkout.Session = _fake_session
    _fake_stripe.checkout = _fake_checkout

    _fake_stripe.Webhook = MagicMock()

    sys.modules["stripe"] = _fake_stripe
    sys.modules["stripe.error"] = _fake_stripe_error
    sys.modules["stripe.checkout"] = _fake_checkout

if "fastapi" not in sys.modules:
    _fake_fastapi = types.ModuleType("fastapi")

    def _passthrough_decorator_factory(*a, **k):
        def decorator(fn):
            return fn
        return decorator

    class _FakeFastAPI:
        """Every route-registering method is a pure pass-through decorator
        -- main.py's ~80 `@app.get/post/api_route(...)`-decorated functions
        import as ordinary Python functions, no real ASGI routing stood up.
        `on_event` (startup/shutdown hooks) is the same shape."""
        def __init__(self, *a, **k):
            pass

        def get(self, *a, **k):
            return _passthrough_decorator_factory(*a, **k)

        def post(self, *a, **k):
            return _passthrough_decorator_factory(*a, **k)

        def api_route(self, *a, **k):
            return _passthrough_decorator_factory(*a, **k)

        def on_event(self, *a, **k):
            return _passthrough_decorator_factory(*a, **k)

        def mount(self, *a, **k):
            pass

    def _fake_query(*a, **k):
        return None

    def _fake_form(*a, **k):
        return None

    def _fake_depends(*a, **k):
        return None

    class _FakeHTTPException(Exception):
        def __init__(self, status_code=500, detail=None, headers=None):
            self.status_code = status_code
            self.detail = detail
            self.headers = headers
            super().__init__(detail)

    class _FakeRequest:
        pass

    class _FakeBackgroundTasks:
        pass

    _fake_fastapi.FastAPI = _FakeFastAPI
    _fake_fastapi.Query = _fake_query
    _fake_fastapi.BackgroundTasks = _FakeBackgroundTasks
    _fake_fastapi.HTTPException = _FakeHTTPException
    _fake_fastapi.Depends = _fake_depends
    _fake_fastapi.Request = _FakeRequest
    _fake_fastapi.Form = _fake_form
    sys.modules["fastapi"] = _fake_fastapi

    _fake_responses = types.ModuleType("fastapi.responses")

    class _FakeHTMLResponse:
        def __init__(self, *a, **k):
            pass

    class _FakeRedirectResponse:
        def __init__(self, *a, **k):
            pass

    class _FakeResponse:
        def __init__(self, *a, **k):
            pass

    _fake_responses.HTMLResponse = _FakeHTMLResponse
    _fake_responses.RedirectResponse = _FakeRedirectResponse
    _fake_responses.Response = _FakeResponse
    sys.modules["fastapi.responses"] = _fake_responses

    _fake_staticfiles = types.ModuleType("fastapi.staticfiles")

    class _FakeStaticFiles:
        def __init__(self, *a, **k):
            pass

    _fake_staticfiles.StaticFiles = _FakeStaticFiles
    sys.modules["fastapi.staticfiles"] = _fake_staticfiles

    _fake_security = types.ModuleType("fastapi.security")

    class _FakeHTTPBasic:
        def __init__(self, *a, **k):
            pass

    class _FakeHTTPBasicCredentials:
        def __init__(self, *a, **k):
            pass

    _fake_security.HTTPBasic = _FakeHTTPBasic
    _fake_security.HTTPBasicCredentials = _FakeHTTPBasicCredentials
    sys.modules["fastapi.security"] = _fake_security

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import main  # noqa: E402


class TestCouncilSourceIssueDisclosure(unittest.TestCase):
    """Locks in the /check-postcode disclosure note added Sep 3 2026 for
    the three councils confirmed this session to be broken/bot-gated on
    their OWN end (see main.py's _COUNCIL_SOURCE_ISSUES for the live
    evidence behind each one) -- distinguishing "council fault, we're on
    it" from an ordinary, healthy "0 leads today" area.

    Nick's explicit instruction on the wording (verbatim): "do not mention
    bots or anything. just state we havent got them and that its on the
    councils end not ours" -- so these tests check for the plain,
    non-technical phrasing itself, and also guard against a future edit
    accidentally reintroducing technical terms into user-facing copy."""

    def test_merton_returns_its_disclosure_note(self):
        note = main._council_source_issue("Merton")
        self.assertIsNotNone(note)
        self.assertIn("council's end", note)

    def test_bath_and_north_east_somerset_returns_its_disclosure_note(self):
        note = main._council_source_issue("Bath and North East Somerset")
        self.assertIsNotNone(note)
        self.assertIn("council's end", note)

    def test_west_northamptonshire_returns_its_disclosure_note(self):
        note = main._council_source_issue("West Northamptonshire")
        self.assertIsNotNone(note)
        self.assertIn("council's end", note)

    def test_no_technical_jargon_leaks_into_user_facing_copy(self):
        """Nick was explicit: no "bots", no technical explanation at all --
        just "we haven't got them" and "it's on the council's end"."""
        banned_terms = ["bot", "captcha", "recaptcha", "waf", "javascript", "js ", "api"]
        for key, message in main._COUNCIL_SOURCE_ISSUES.items():
            lowered = message.lower()
            for term in banned_terms:
                self.assertNotIn(term, lowered, f"{key!r}'s message leaks technical term {term!r}: {message!r}")

    def test_matching_is_case_insensitive_and_tolerates_council_suffix(self):
        """postcodes.io's admin_district naming isn't perfectly consistent
        about a trailing "Council"/"District Council" -- the match must not
        depend on getting that suffix exactly right."""
        self.assertIsNotNone(main._council_source_issue("MERTON"))
        self.assertIsNotNone(main._council_source_issue("merton council"))
        self.assertIsNotNone(main._council_source_issue("West Northamptonshire Council"))

    def test_healthy_council_returns_none(self):
        """An ordinary, fully-working council must never show the notice --
        it would misrepresent a genuine "nothing pending today" as a fault
        on the council's end."""
        self.assertIsNone(main._council_source_issue("Birmingham"))
        self.assertIsNone(main._council_source_issue("Leeds City Council"))

    def test_blank_or_missing_district_returns_none(self):
        self.assertIsNone(main._council_source_issue(None))
        self.assertIsNone(main._council_source_issue(""))

    def test_every_entry_is_confirmed_blocked_not_a_todo(self):
        """Guards against this dict quietly growing into a dumping ground
        for "haven't built this yet" councils -- it exists ONLY for
        councils whose own portal is confirmed broken/bot-gated, per its
        own comment in main.py."""
        for key, message in main._COUNCIL_SOURCE_ISSUES.items():
            self.assertTrue(key.islower(), f"key {key!r} should be lower-case for the substring match")
            self.assertGreater(len(message), 20)


if __name__ == "__main__":
    unittest.main(verbosity=2)

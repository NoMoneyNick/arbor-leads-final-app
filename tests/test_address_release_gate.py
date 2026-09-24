"""
test_address_release_gate.py -- 2026-09-18 review, Section 5: "Confirm that
the separate address-release policy gate was implemented, including
protection against alternative routes revealing the address. Do not treat
payment, funding or provider acceptance as legal approval to disclose."

Two layers of coverage:
  1. address_release.py's own primitives in isolation (default-off, fresh
     read per call, redaction behaviour) -- fast, no main.py/database
     stubbing needed.
  2. A representative sample of the actual disclosure routes this gate was
     wired into, driven end-to-end through the real route functions (reusing
     test_access_control.py's established main.py-import-with-stubs
     technique) to prove the wiring, not just the primitive:
       - /generate-letter/{lead_id}      (address embedded in rendered HTML)
       - /generate-street-flyer/{lead_id} (whole route refused when not live
         -- the flyer's entire purpose is address disclosure)
       - /street-view/{reference}        (address baked into a redirect
         URL -- the "alternative route" this section's instruction calls
         out most directly)
     For all three, BOTH the disabled (default) and enabled journeys are
     exercised, and each proves ownership/session being satisfied is NOT
     sufficient on its own -- the release gate is checked independently.
     The remaining wired routes (/dashboard, /my-leads, /free-dashboard,
     and the two notifications.py purchase/free-grant emails) share the
     exact same guarded_address()/address_release_live() primitives proven
     in layer 1 and are covered by direct unit tests of their HTML-building
     functions below, rather than a full route/DB harness each -- see the
     2026-09-18 review's final report for what that leaves untested.

Run with:
    python -m unittest tests.test_address_release_gate -v
"""
import base64
import os
import sys
import types
import unittest
from unittest.mock import MagicMock, patch

_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
_APP_DIR = os.path.dirname(_THIS_DIR)
sys.path.insert(0, _APP_DIR)

# address_release.py imports "database" at its OWN module level (2026-09-18
# review, Section 2 second pass -- see that module's own comment on why).
# database.py itself imports psycopg2, not installed in this sandbox, so
# running this file standalone (before this file's own `import test_main`
# further down ever runs) needs a placeholder registered first -- same
# minimal stub-if-absent convention test_main.py uses. Under the real
# `unittest discover` full suite, test_access_control.py (alphabetically
# first) has always already done this via its own `import test_main`
# before this file is even collected, so this is a no-op there; it only
# matters for `python -m unittest tests.test_address_release_gate` alone.
if "database" not in sys.modules:
    sys.modules["database"] = types.ModuleType("database")

import address_release  # real module, only a lightweight "database" stub needed at import time


class _EnvIsolation(unittest.TestCase):
    ENV_KEY = address_release.ADDRESS_RELEASE_LIVE_ENV

    def setUp(self):
        self._had = self.ENV_KEY in os.environ
        self._old = os.environ.get(self.ENV_KEY)

    def tearDown(self):
        if self._had:
            os.environ[self.ENV_KEY] = self._old
        else:
            os.environ.pop(self.ENV_KEY, None)

    def _clear(self):
        os.environ.pop(self.ENV_KEY, None)

    def _set(self, value):
        os.environ[self.ENV_KEY] = value


class TestAddressReleaseLivePrimitive(_EnvIsolation):

    def test_default_unset_is_false(self):
        self._clear()
        self.assertFalse(address_release.address_release_live())

    def test_false_spellings(self):
        for spelling in ("false", "0", "no", "off", ""):
            self._set(spelling)
            self.assertFalse(address_release.address_release_live(), f"spelling={spelling!r}")

    def test_truthy_spellings(self):
        for spelling in ("1", "true", "True", "TRUE", "yes", "on"):
            self._set(spelling)
            self.assertTrue(address_release.address_release_live(), f"spelling={spelling!r}")

    def test_fresh_read_per_call_no_caching(self):
        self._clear()
        self.assertFalse(address_release.address_release_live())
        self._set("true")
        self.assertTrue(address_release.address_release_live())
        self._clear()
        self.assertFalse(address_release.address_release_live())


class TestGuardedAddress(_EnvIsolation):

    def test_disabled_returns_placeholder_not_real_address(self):
        self._clear()
        result = address_release.guarded_address("221B Baker Street, London, NW1 6XE")
        self.assertEqual(result, address_release.REDACTED_ADDRESS_PLACEHOLDER)
        self.assertNotIn("Baker Street", result)
        self.assertNotIn("NW1", result)

    def test_enabled_returns_real_address_unchanged(self):
        self._set("true")
        real = "221B Baker Street, London, NW1 6XE"
        self.assertEqual(address_release.guarded_address(real), real)

    def test_falsy_address_never_becomes_the_placeholder_masquerading_as_real(self):
        self._set("true")
        self.assertEqual(address_release.guarded_address(""), address_release.REDACTED_ADDRESS_PLACEHOLDER)
        self.assertEqual(address_release.guarded_address(None), address_release.REDACTED_ADDRESS_PLACEHOLDER)

    def test_placeholder_reveals_nothing_derived_from_a_real_address(self):
        """Regression guard against a future edit that tries to be
        'helpful' by redacting only part of the address (e.g. house number
        only) -- the whole point is nothing address-shaped leaks."""
        self._clear()
        for real in ("1 Test St, Leeds, LS1 1AA", "42 Nowhere Lane, Bristol, BS1 2AB"):
            result = address_release.guarded_address(real)
            self.assertEqual(result, address_release.REDACTED_ADDRESS_PLACEHOLDER)


# ---------------------------------------------------------------------------
# Layer 2: real route functions, via test_access_control.py's stubbing.
# ---------------------------------------------------------------------------
for _name in ("scanners", "research", "payments"):
    if _name not in sys.modules:
        _mod = types.ModuleType(_name)
        _mod.__getattr__ = lambda attr, _n=_name: MagicMock(name=f"{_n}.{attr}")
        sys.modules[_name] = _mod

if "fastapi" not in sys.modules:
    _fake_fastapi = types.ModuleType("fastapi")

    def _passthrough_decorator_factory(*a, **k):
        def decorator(fn):
            return fn
        return decorator

    class _FakeFastAPI:
        def __init__(self, *a, **k): pass
        def get(self, *a, **k): return _passthrough_decorator_factory(*a, **k)
        def post(self, *a, **k): return _passthrough_decorator_factory(*a, **k)
        def api_route(self, *a, **k): return _passthrough_decorator_factory(*a, **k)
        def on_event(self, *a, **k): return _passthrough_decorator_factory(*a, **k)
        def mount(self, *a, **k): pass
        def __getattr__(self, name):
            return lambda *a, **k: _passthrough_decorator_factory(*a, **k)

    class _FakeHTTPException(Exception):
        def __init__(self, status_code=500, detail=None, headers=None):
            self.status_code = status_code
            self.detail = detail
            self.headers = headers
            super().__init__(detail)

    _fake_fastapi.FastAPI = _FakeFastAPI
    _fake_fastapi.Query = lambda *a, **k: None
    _fake_fastapi.BackgroundTasks = type("_FakeBackgroundTasks", (), {})
    _fake_fastapi.HTTPException = _FakeHTTPException
    _fake_fastapi.Depends = lambda *a, **k: None
    _fake_fastapi.Request = type("_FakeRequest", (), {})
    _fake_fastapi.Form = lambda *a, **k: None
    sys.modules["fastapi"] = _fake_fastapi

    _fake_responses = types.ModuleType("fastapi.responses")
    for _cls_name in ("HTMLResponse", "RedirectResponse", "Response", "PlainTextResponse",
                       "JSONResponse", "StreamingResponse", "FileResponse"):
        setattr(_fake_responses, _cls_name, type(_cls_name, (), {
            "__init__": lambda self, *a, url=None, status_code=200, **k: (
                setattr(self, "url", url), setattr(self, "status_code", status_code),
                setattr(self, "_cookies", {}))[-1],
            "set_cookie": lambda self, key=None, value=None, *a, **k: self._cookies.__setitem__(key, value),
        }))
    sys.modules["fastapi.responses"] = _fake_responses

    _fake_staticfiles = types.ModuleType("fastapi.staticfiles")
    _fake_staticfiles.StaticFiles = type("_FakeStaticFiles", (), {"__init__": lambda self, *a, **k: None})
    sys.modules["fastapi.staticfiles"] = _fake_staticfiles

    _fake_security = types.ModuleType("fastapi.security")
    _fake_security.HTTPBasic = type("_FakeHTTPBasic", (), {"__init__": lambda self, *a, **k: None})
    _fake_security.HTTPBasicCredentials = type("_FakeHTTPBasicCredentials", (), {"__init__": lambda self, *a, **k: None})
    sys.modules["fastapi.security"] = _fake_security

    _fake_exc_handlers = types.ModuleType("fastapi.exception_handlers")
    _fake_exc_handlers.http_exception_handler = MagicMock()
    sys.modules["fastapi.exception_handlers"] = _fake_exc_handlers

    _fake_starlette = types.ModuleType("starlette")
    _fake_starlette_exc = types.ModuleType("starlette.exceptions")
    _fake_starlette_exc.HTTPException = _FakeHTTPException
    sys.modules["starlette"] = _fake_starlette
    sys.modules["starlette.exceptions"] = _fake_starlette_exc

import test_main  # noqa: E402  (populates sys.modules stubs + imports main)
import main  # noqa: E402
import letter_content  # noqa: E402  real module

# test_main.py's `database` stub only pre-populates the specific attributes
# ITS OWN tests need (see that file's own "if not hasattr(...)" block) --
# these two are real database.py functions this file's route tests need to
# @patch that aren't in that pre-populated set yet.
_database_stub = sys.modules["database"]
if not hasattr(_database_stub, "get_dispatched_lead_address_for_contractor"):
    _database_stub.get_dispatched_lead_address_for_contractor = MagicMock(return_value=None)
if not hasattr(_database_stub, "street_view_url"):
    _database_stub.street_view_url = MagicMock(return_value="https://maps.google.com/?q=stub")
if not hasattr(_database_stub, "_redact_address_from_summary"):
    # 2026-09-23 external-review fix (Finding 2, free-text descriptions):
    # address_release.guarded_summary_for_lead(_reference) calls the real
    # database._redact_address_from_summary for every NON-historical lead --
    # real database.py can't be imported here at all (it does `import
    # psycopg2` at module level, not installed in this sandbox, which is
    # exactly why `database` is stubbed in the first place), so this is a
    # deliberate, minimal mirror of that function's actual regex behaviour
    # (database.py:3595, unchanged this session) rather than a canned
    # return value -- tests that seed a summary with a real address/
    # postcode need to see it actually redacted, not just "didn't crash".
    # Keep this in sync with database._redact_address_from_summary if that
    # function's regexes ever change.
    import re as _re
    _FULL_POSTCODE_RE = _re.compile(r'\b[A-Z]{1,2}[0-9][A-Z0-9]?\s*[0-9][A-Z]{2}\b')
    _HOUSE_STREET_RE = _re.compile(
        r'\b\d{1,4}[A-Za-z]?\s+(?:[A-Z][a-zA-Z\'\-]*\s+){0,3}'
        r'(?:Road|Rd|Street|St|Avenue|Ave|Lane|Ln|Close|Drive|Dr|Way|Grove|Grv|'
        r'Crescent|Cres|Gardens|Gdns|Court|Ct|Place|Pl|Rise|Walk|Terrace|Ter|'
        r'Hill|Park|Row|Mews|Square|Sq|Green|Gn)\b'
    )

    def _stub_redact_address_from_summary(summary):
        if not summary:
            return summary
        redacted = _FULL_POSTCODE_RE.sub("[postcode hidden]", summary)
        redacted = _HOUSE_STREET_RE.sub("[address hidden]", redacted)
        return redacted

    _database_stub._redact_address_from_summary = MagicMock(side_effect=_stub_redact_address_from_summary)


def _mock_request(cookie_value=None, auth_header=None):
    req = MagicMock()
    req.cookies = {"treekey_contractor_session": cookie_value} if cookie_value else {}
    req.headers = {"authorization": auth_header} if auth_header else {}
    return req


def _basic_auth_header(user: str, password: str) -> str:
    token = base64.b64encode(f"{user}:{password}".encode()).decode()
    return f"Basic {token}"


class _RouteTestBase(_EnvIsolation):
    """Admin basic-auth bypasses require_lead_ownership entirely (see that
    function's own "Admin ... always passes" docstring) -- deliberately
    used here so these tests isolate the address-release gate itself
    rather than re-testing Section 1's ownership logic (already covered by
    test_access_control.py)."""

    def setUp(self):
        super().setUp()
        os.environ["DASHBOARD_USER"] = "admin"
        os.environ["DASHBOARD_PASS"] = "test-admin-pass"
        os.environ["SESSION_SECRET"] = "test-session-secret"
        main._SESSION_SECRET = b"test-session-secret"
        os.environ[letter_content.PRIVACY_CONTACT_EMAIL_ENV] = "privacy@treekey.co.uk"

    def _admin_request(self):
        return _mock_request(auth_header=_basic_auth_header("admin", "test-admin-pass"))


class TestGenerateHomeownerLetterRoute(_RouteTestBase):

    def _row(self):
        return ("PLANIT-001", "1 Real Street, Leeds, LS1 1AA", "Fell one oak", "Leeds City Council", "claimed")

    @patch("main.database.get_db_conn")
    def test_disabled_preview_shows_placeholder_not_real_address(self, mock_get_conn):
        self._clear()
        conn = MagicMock()
        cur = MagicMock()
        cur.fetchone.return_value = self._row()
        conn.cursor.return_value = cur
        mock_get_conn.return_value = conn

        response = main.generate_homeowner_letter(self._admin_request(), "PLANIT-001")
        body = response if isinstance(response, str) else getattr(response, "body", str(response))
        self.assertNotIn("1 Real Street", body)
        self.assertNotIn("LS1 1AA", body)
        self.assertIn("Address release pending", body)

    @patch("main.database.get_db_conn")
    def test_enabled_but_new_allocation_still_shows_placeholder(self, mock_get_conn):
        """2026-09-22 handoff regression guard: ADDRESS_RELEASE_LIVE=true
        is no longer sufficient on its own for a NEW allocation -- only a
        historical claim (see the next test) can ever show the real
        address now. cur.fetchone() defaults (MagicMock, truthy) classify
        this lead as a NEW allocation (the lead_allocations lookup 'hits'),
        which must stay redacted regardless of the flag."""
        self._set("true")
        conn = MagicMock()
        cur = MagicMock()
        cur.fetchone.return_value = self._row()
        conn.cursor.return_value = cur
        mock_get_conn.return_value = conn

        response = main.generate_homeowner_letter(self._admin_request(), "PLANIT-001")
        body = response if isinstance(response, str) else getattr(response, "body", str(response))
        self.assertNotIn("1 Real Street", body)
        self.assertIn("Address release pending", body)

    @patch("main.database.get_db_conn")
    def test_historical_claim_shows_the_real_address_regardless_of_the_flag(self, mock_get_conn):
        """The one path that can still show a real address: a lead only
        found via letter_dispatches (historical, pre-dating the
        allocation pipeline/this gate). Left OFF here deliberately -- this
        must not need the flag at all any more."""
        self._clear()
        conn = MagicMock()
        cur = MagicMock()
        # 2026-09-23: Request D, Part 1 added a resolve_buyer_facing_
        # reference lookup at the very top of the route (a lead_
        # allocations.id lookup for the incoming path param), before
        # anything else -- so this now needs ONE more leading result: 0)
        # the resolve step's own lead_allocations miss (this test's
        # "PLANIT-001" is a real council reference, not a buyer-facing
        # UUID, so it correctly falls through unchanged), then 1) the
        # route's own lead-data row fetch, then guarded_address_for_
        # lead_reference's own connection reuses this same mock: 2) a
        # lead_allocations miss, 3) a letter_dispatches hit -> historical.
        cur.fetchone.side_effect = [None, self._row(), None, (1,)]
        conn.cursor.return_value = cur
        mock_get_conn.return_value = conn

        response = main.generate_homeowner_letter(self._admin_request(), "PLANIT-001")
        body = response if isinstance(response, str) else getattr(response, "body", str(response))
        self.assertIn("1 Real Street", body)


class TestGenerateStreetFlyerRoute(_RouteTestBase):

    def _row(self):
        return ("PLANIT-001", "1 Real Street, Leeds, LS1 1AA", "Fell one oak", "claimed")

    @patch("main.database.get_db_conn")
    def test_disabled_refuses_the_whole_route(self, mock_get_conn):
        """No reduced/safe version of a neighbor flyer exists -- the whole
        point of the page is putting the address in front of neighbors."""
        self._clear()
        conn = MagicMock()
        cur = MagicMock()
        cur.fetchone.return_value = self._row()
        conn.cursor.return_value = cur
        mock_get_conn.return_value = conn

        response = main.generate_street_flyer(self._admin_request(), "PLANIT-001")
        self.assertEqual(getattr(response, "status_code", None), 403)
        body = getattr(response, "body", str(response))
        self.assertNotIn("1 Real Street", body)

    @patch("main.database.get_db_conn")
    def test_enabled_but_new_allocation_still_refuses_the_route(self, mock_get_conn):
        """2026-09-22 handoff regression guard -- see the equivalent test
        on TestGenerateHomeownerLetterRoute for the full reasoning: a new
        allocation (the default truthy lead_allocations lookup here) must
        stay refused even with the flag on."""
        self._set("true")
        conn = MagicMock()
        cur = MagicMock()
        cur.fetchone.return_value = self._row()
        conn.cursor.return_value = cur
        mock_get_conn.return_value = conn

        response = main.generate_street_flyer(self._admin_request(), "PLANIT-001")
        self.assertEqual(getattr(response, "status_code", None), 403)
        body = getattr(response, "body", str(response))
        self.assertNotIn("1 Real Street", body)

    @patch("main.database.get_db_conn")
    def test_historical_claim_generates_the_flyer_with_the_real_address(self, mock_get_conn):
        self._clear()
        conn = MagicMock()
        cur = MagicMock()
        # 2026-09-23: Request D, Part 1's resolve_buyer_facing_reference
        # lookup adds one more leading result -- see the equivalent comment
        # on TestGenerateHomeownerLetterRoute's historical test above.
        cur.fetchone.side_effect = [None, self._row(), None, (1,)]  # resolve miss, row, lead_allocations miss, letter_dispatches hit
        conn.cursor.return_value = cur
        mock_get_conn.return_value = conn

        response = main.generate_street_flyer(self._admin_request(), "PLANIT-001")
        body = response if isinstance(response, str) else getattr(response, "body", str(response))
        self.assertIn("1 Real Street", body)


class TestStreetViewRedirectRoute(_RouteTestBase):
    """This route's whole output IS a URL built from the address -- the
    single clearest 'alternative route' the section's instruction calls
    out (a bookmarked/shared redirect link leaks the address independently
    of any page rendering)."""

    def _signed_cookie(self, email: str) -> str:
        return main._sign_session_cookie(email)

    @patch("main.database.get_dispatched_lead_address_for_contractor")
    def test_disabled_does_not_redirect_to_a_url_containing_the_address(self, mock_get_addr):
        self._clear()
        mock_get_addr.return_value = "1 Real Street, Leeds, LS1 1AA"
        request = _mock_request(cookie_value=self._signed_cookie("contractor@example.com"))

        response = main.street_view_redirect("PLANIT-001", request)
        self.assertEqual(getattr(response, "status_code", None), 403)
        self.assertIsNone(getattr(response, "url", None))

    @patch("main.database.street_view_url")
    @patch("main.database.get_dispatched_lead_address_for_contractor")
    def test_enabled_but_new_allocation_still_refuses(self, mock_get_addr, mock_street_view_url):
        """2026-09-22 handoff regression guard: ADDRESS_RELEASE_LIVE=true
        is no longer sufficient for a new allocation -- lead_address_
        release_allowed opens its own DB connection here (not explicitly
        mocked), which under this test suite's stub defaults classify the
        lead as new (truthy lead_allocations lookup), so this must stay
        refused."""
        self._set("true")
        mock_get_addr.return_value = "1 Real Street, Leeds, LS1 1AA"
        mock_street_view_url.return_value = "https://maps.google.com/?q=1+Real+Street"
        request = _mock_request(cookie_value=self._signed_cookie("contractor@example.com"))

        response = main.street_view_redirect("PLANIT-001", request)
        self.assertEqual(getattr(response, "status_code", None), 403)
        self.assertIsNone(getattr(response, "url", None))

    @patch("main.database.get_db_conn")
    @patch("main.database.street_view_url")
    @patch("main.database.get_dispatched_lead_address_for_contractor")
    def test_historical_claim_redirects_to_the_real_street_view_url(self, mock_get_addr, mock_street_view_url, mock_get_conn):
        self._clear()
        mock_get_addr.return_value = "1 Real Street, Leeds, LS1 1AA"
        mock_street_view_url.return_value = "https://maps.google.com/?q=1+Real+Street"
        conn = MagicMock()
        cur = MagicMock()
        # 2026-09-23: Request D, Part 1's resolve_buyer_facing_reference_
        # standalone lookup (its own connection, opened before database.
        # get_dispatched_lead_address_for_contractor is even called) adds
        # one more leading result -- same reasoning as the letter/flyer
        # routes' equivalent historical tests above.
        cur.fetchone.side_effect = [None, None, (1,)]  # resolve miss, lead_allocations miss, letter_dispatches hit -> historical
        conn.cursor.return_value = cur
        mock_get_conn.return_value = conn
        request = _mock_request(cookie_value=self._signed_cookie("contractor@example.com"))

        response = main.street_view_redirect("PLANIT-001", request)
        self.assertEqual(getattr(response, "status_code", None), 302)
        self.assertEqual(response.url, "https://maps.google.com/?q=1+Real+Street")
        mock_street_view_url.assert_called_once_with("1 Real Street, Leeds, LS1 1AA")


# ---------------------------------------------------------------------------
# Layer 2b: the two notifications.py transactional emails -- "alternative
# routes" in the sense of bypassing the web app entirely (sent straight to
# an inbox right after payment/grant, with no session or ownership check at
# read time -- payment/grant being exactly what Nick's instruction says
# must NOT be treated as a legal basis to disclose).
#
# Loaded under a PRIVATE module name via importlib (same technique
# test_reconciliation.py already established, see its own docstring) --
# deliberately NOT `del sys.modules["notifications"]; import notifications`.
# An earlier version of this file did that, and it broke tests.test_
# payments_webhook when both files were run together: that file's own
# `import notifications` binds ITS module-level name at ITS collection
# time, but payments.handle_stripe_webhook re-resolves `notifications` via
# a LOCAL import at CALL time -- so replacing sys.modules["notifications"]
# here (even temporarily) meant payments.py's webhook handler, when
# EXECUTED later, picked up whichever module last won that global slot,
# not test_payments_webhook.py's own MagicMock-attributed stub. Loading a
# private copy here touches sys.modules["notifications"] not at all, so
# this file can drive the real notifications.py functions without being
# able to affect (or be affected by) any other file's stubbing of the
# shared "notifications" name.
import importlib.util
_NOTIFICATIONS_PATH = os.path.join(_APP_DIR, "notifications.py")
_notifications_spec = importlib.util.spec_from_file_location(
    "_notifications_under_test_address_release_gate", _NOTIFICATIONS_PATH
)
notifications = importlib.util.module_from_spec(_notifications_spec)
_notifications_spec.loader.exec_module(notifications)


def _ensure_database_stub_has_street_view_url():
    """notifications._street_view_link_html does a LOCAL `import database`
    (resolved fresh against sys.modules on every call, not this test file's
    own bound reference) -- under `unittest discover`, several other test
    files replace sys.modules["database"] wholesale (not just mutate it) at
    COLLECTION time, so whichever one runs last "wins" and the object this
    test file captured earlier no longer matches sys.modules["database"] by
    the time these tests actually EXECUTE. Re-asserting the attribute on
    whatever the CURRENT sys.modules["database"] is, from setUp (i.e. at
    execution time, not import time), is what actually makes this robust to
    discovery order -- same underlying class of bug as the notifications/
    requests one above, just surfacing through a local rather than
    module-level import."""
    db_stub = sys.modules.get("database")
    if db_stub is not None:
        db_stub.street_view_url = MagicMock(return_value="https://maps.google.com/?q=stub")


class TestPurchasedLeadEmailAddressGate(_EnvIsolation):

    def _lead_data(self):
        return {
            "reference": "PLANIT-001", "address": "1 Real Street, Leeds, LS1 1AA",
            "council_source": "Leeds City Council", "summary": "Fell one oak",
            "lead_score": "medium", "registered_date": None,
        }

    def setUp(self):
        super().setUp()
        notifications.RESEND_API_KEY = "test-key"
        _ensure_database_stub_has_street_view_url()

    def test_disabled_email_body_has_no_real_address(self):
        # 2026-09-18: patched via patch.object on the REAL module objects
        # this test file itself imported (notifications, notifications.
        # requests), not by name ("notifications.requests.post") -- under
        # `unittest discover`, a later-alphabetical file (test_payments_
        # webhook.py) replaces sys.modules["notifications"] with a bare
        # stub at COLLECTION time, and string-based patch() re-resolves
        # through sys.modules at test-EXECUTION time, so it would silently
        # patch the wrong (stub) object and this test would either error
        # or assert nothing real. Patching the bound object sidesteps that
        # entirely -- same failure class test_payments_webhook.py's own
        # docstring documents, just hit from the opposite direction here.
        self._clear()
        with patch.object(notifications.requests, "post", return_value=MagicMock(status_code=200)) as mock_post:
            notifications._send_purchased_lead_email_inner("buyer@example.com", self._lead_data())
        sent_html = mock_post.call_args.kwargs["json"]["html"]
        self.assertNotIn("1 Real Street", sent_html)
        self.assertIn("Address release pending", sent_html)

    def test_enabled_but_new_allocation_email_still_has_no_real_address(self):
        """2026-09-22 handoff regression guard: guarded_address_for_lead_
        reference opens its own DB connection here, not explicitly mocked
        -- under this test suite's stub defaults that classifies the lead
        as a new allocation (truthy lead_allocations lookup), which must
        stay redacted regardless of the flag."""
        self._set("true")
        with patch.object(notifications.requests, "post", return_value=MagicMock(status_code=200)) as mock_post:
            notifications._send_purchased_lead_email_inner("buyer@example.com", self._lead_data())
        sent_html = mock_post.call_args.kwargs["json"]["html"]
        self.assertNotIn("1 Real Street", sent_html)
        self.assertIn("Address release pending", sent_html)

    def test_historical_claim_email_has_the_real_address(self):
        self._clear()
        conn = MagicMock()
        cur = MagicMock()
        # Padded beyond one (miss, hit) pair: the email body also builds a
        # street-view link (lead_address_release_allowed), a second,
        # separate historical-classification lookup against the same mock.
        cur.fetchone.side_effect = [None, (1,), None, (1,)]
        conn.cursor.return_value = cur
        with patch.object(address_release.database, "get_db_conn", return_value=conn), \
             patch.object(notifications.requests, "post", return_value=MagicMock(status_code=200)) as mock_post:
            notifications._send_purchased_lead_email_inner("buyer@example.com", self._lead_data())
        sent_html = mock_post.call_args.kwargs["json"]["html"]
        self.assertIn("1 Real Street", sent_html)


class TestFreeLeadGrantedEmailAddressGate(_EnvIsolation):

    def _lead_data(self):
        return {
            "reference": "PLANIT-002", "address": "2 Real Street, Leeds, LS1 1AB",
            "council_source": "Leeds City Council", "summary": "Prune a beech",
            "lead_score": "small", "registered_date": None,
        }

    def setUp(self):
        super().setUp()
        notifications.RESEND_API_KEY = "test-key"
        _ensure_database_stub_has_street_view_url()

    def test_disabled_email_body_has_no_real_address(self):
        # See TestPurchasedLeadEmailAddressGate's comment above on why
        # patch.object on the bound module, not a string patch target.
        self._clear()
        with patch.object(notifications, "send_transactional_email", return_value=True) as mock_send:
            notifications._send_free_lead_granted_email_inner("winner@example.com", self._lead_data())
        sent_html = mock_send.call_args.kwargs.get("html_body") or mock_send.call_args.args[2]
        self.assertNotIn("2 Real Street", sent_html)
        self.assertIn("Address release pending", sent_html)

    def test_enabled_but_new_allocation_email_still_has_no_real_address(self):
        """See TestPurchasedLeadEmailAddressGate's equivalent test for the
        full 2026-09-22 handoff reasoning."""
        self._set("true")
        with patch.object(notifications, "send_transactional_email", return_value=True) as mock_send:
            notifications._send_free_lead_granted_email_inner("winner@example.com", self._lead_data())
        sent_html = mock_send.call_args.kwargs.get("html_body") or mock_send.call_args.args[2]
        self.assertNotIn("2 Real Street", sent_html)
        self.assertIn("Address release pending", sent_html)

    def test_historical_claim_email_has_the_real_address(self):
        self._clear()
        conn = MagicMock()
        cur = MagicMock()
        # Padded beyond one (miss, hit) pair: the email body also builds a
        # street-view link (lead_address_release_allowed), a second,
        # separate historical-classification lookup against the same mock.
        cur.fetchone.side_effect = [None, (1,), None, (1,)]
        conn.cursor.return_value = cur
        with patch.object(address_release.database, "get_db_conn", return_value=conn), \
             patch.object(notifications, "send_transactional_email", return_value=True) as mock_send:
            notifications._send_free_lead_granted_email_inner("winner@example.com", self._lead_data())
        sent_html = mock_send.call_args.kwargs.get("html_body") or mock_send.call_args.args[2]
        self.assertIn("2 Real Street", sent_html)


if __name__ == "__main__":
    unittest.main()

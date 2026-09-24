"""
test_access_control.py -- Section 1: the /generate-letter ownership fix.

Follows the exact stubbing convention test_main.py already established
(fastapi/stripe/database/scanners/research/payments stubbed into
sys.modules before `import main`, since neither fastapi nor stripe is
installed in this sandbox and this repo's own test suite already solved
that problem this way -- see test_main.py's own docstring). Run with:

    python -m unittest tests.test_access_control -v

Covers, per the brief's section 9 checklist item "buyer-only access and
unauthorised document access": anonymous, another buyer, the correct buyer,
and an authorised administrator.
"""
import base64
import os
import sys
import types
import unittest
import urllib.parse
from unittest.mock import MagicMock, patch

_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
_APP_DIR = os.path.dirname(_THIS_DIR)
sys.path.insert(0, _APP_DIR)

# --- scanners.py/research.py/payments.py were not mirrored into this local
# working copy (this session's edits don't touch them, and they pull in
# heavy third-party deps -- beautifulsoup4, lxml, google-generativeai,
# stripe -- not installed in this sandbox). test_main.py's own stub set
# doesn't cover these because it normally runs against the FULL real repo,
# where those files exist. Stubbed here the same way test_main.py stubs
# `database`: a permissive module whose attribute access never fails, so
# main.py's top-level `import scanners`/`import research`/`import payments`
# succeeds without needing their real implementations for THESE tests
# (which only exercise the new ownership-check code path). -----------------
for _name in ("scanners", "research", "payments"):
    if _name not in sys.modules:
        _mod = types.ModuleType(_name)
        _mod.__getattr__ = lambda attr, _n=_name: MagicMock(name=f"{_n}.{attr}")
        sys.modules[_name] = _mod

# --- test_main.py's own fastapi stub (written against an earlier version
# of main.py) is missing PlainTextResponse and the starlette exception-
# handler imports that the CURRENT main.py's top-of-file imports now use
# (main.py:24's PlainTextResponse, main.py:~1148's
# `from fastapi.exception_handlers import http_exception_handler` and
# `from starlette.exceptions import HTTPException as _StarletteHTTPException`).
# This is a pre-existing gap in test_main.py, not something introduced by
# this session's changes -- flagged in docs/handoff.md. Pre-populating a
# fuller stub here (test_main.py only builds its own when "fastapi" not in
# sys.modules) lets this test file run without editing test_main.py itself.
if "fastapi" not in sys.modules:
    _fake_fastapi = types.ModuleType("fastapi")

    def _passthrough_decorator_factory(*a, **k):
        def decorator(fn):
            return fn
        return decorator

    class _FakeFastAPI:
        """Any method not explicitly listed (middleware, exception_handler,
        add_middleware, etc.) falls through __getattr__ to a generic
        passthrough-decorator-returning callable, so an unrecognised
        FastAPI app-configuration call never breaks the import -- main.py
        is large and this stub only needs to make it IMPORTABLE, not
        behave like a real ASGI app."""
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

# --- Reuse test_main.py's stubbing exactly, by importing it for its side
# effects, before importing main.py ourselves. -----------------------------
import test_main  # noqa: E402  (populates sys.modules stubs + imports main)
import main  # noqa: E402
import fulfilment  # real module, no stubbing needed (stdlib only)


class _FakeURL:
    """2026-09-24 handoff: generate_homeowner_letter/generate_street_flyer
    now build a `next` redirect target from request.url.path/.query when a
    real, logged-in contractor has no saved letter settings (see those
    routes' own comments) -- a bare MagicMock's .url.path/.query are
    themselves MagicMocks, not strings, which breaks urllib.parse.quote.
    Same minimal fake already used for this in
    tests/test_letter_setup_checkout_gate.py's own _mock_request."""
    def __init__(self, path, query=""):
        self.path = path
        self.query = query


def _mock_request(cookie_value=None, auth_header=None, path="/generate-letter/PLANIT-REF-001", query=""):
    req = MagicMock()
    req.cookies = {"treekey_contractor_session": cookie_value} if cookie_value else {}
    req.headers = {"authorization": auth_header} if auth_header else {}
    req.url = _FakeURL(path, query)
    return req


def _basic_auth_header(user: str, password: str) -> str:
    token = base64.b64encode(f"{user}:{password}".encode()).decode()
    return f"Basic {token}"


class TestRequireLeadOwnership(unittest.TestCase):

    def setUp(self):
        os.environ["DASHBOARD_USER"] = "admin"
        os.environ["DASHBOARD_PASS"] = "test-admin-pass"
        # main.py signs cookies with SESSION_SECRET (or falls back);
        # _sign_session_cookie/_verify_session_cookie are real functions
        # under test here too -- not mocked -- so these tests exercise the
        # actual HMAC verification, not a stand-in.
        os.environ["SESSION_SECRET"] = "test-session-secret"
        main._SESSION_SECRET = b"test-session-secret"

    def _signed_cookie(self, email: str) -> str:
        return main._sign_session_cookie(email)

    def test_anonymous_visitor_is_denied(self):
        request = _mock_request(cookie_value=None)
        with self.assertRaises(main.HTTPException) as ctx:
            main.require_lead_ownership(request, "PLANIT-REF-001")
        self.assertEqual(ctx.exception.status_code, 404)

    def test_tampered_or_forged_cookie_is_denied(self):
        """A requester-supplied email must never be trusted -- this asserts
        the HMAC signature is actually checked, not just present."""
        forged = base64.urlsafe_b64encode(b"attacker@example.com").decode().rstrip("=") + ".notarealsignature"
        request = _mock_request(cookie_value=forged)
        with self.assertRaises(main.HTTPException) as ctx:
            main.require_lead_ownership(request, "PLANIT-REF-001")
        self.assertEqual(ctx.exception.status_code, 404)

    @patch("main.fulfilment.get_lead_owner")
    @patch("main.database.get_db_conn")
    def test_another_buyer_is_denied(self, mock_get_db_conn, mock_get_lead_owner):
        mock_get_db_conn.return_value = MagicMock()
        mock_get_lead_owner.return_value = "real-buyer@example.com"
        request = _mock_request(cookie_value=self._signed_cookie("someone-else@example.com"))
        with self.assertRaises(main.HTTPException) as ctx:
            main.require_lead_ownership(request, "PLANIT-REF-001")
        self.assertEqual(ctx.exception.status_code, 404)

    @patch("main.fulfilment.get_lead_owner")
    @patch("main.database.get_db_conn")
    def test_correct_buyer_is_allowed(self, mock_get_db_conn, mock_get_lead_owner):
        mock_get_db_conn.return_value = MagicMock()
        mock_get_lead_owner.return_value = "real-buyer@example.com"
        request = _mock_request(cookie_value=self._signed_cookie("Real-Buyer@Example.com"))
        owner = main.require_lead_ownership(request, "PLANIT-REF-001")
        self.assertEqual(owner, "real-buyer@example.com")

    @patch("main.fulfilment.get_lead_owner")
    @patch("main.database.get_db_conn")
    def test_unclaimed_or_unknown_reference_is_denied_even_for_a_real_session(self, mock_get_db_conn, mock_get_lead_owner):
        mock_get_db_conn.return_value = MagicMock()
        mock_get_lead_owner.return_value = None  # no allocation/dispatch row found at all
        request = _mock_request(cookie_value=self._signed_cookie("someone@example.com"))
        with self.assertRaises(main.HTTPException) as ctx:
            main.require_lead_ownership(request, "PLANIT-NEVER-CLAIMED")
        self.assertEqual(ctx.exception.status_code, 404)

    @patch("main.fulfilment.get_lead_owner")
    def test_authorised_admin_bypasses_ownership_check(self, mock_get_lead_owner):
        """Admin access is via a SEPARATE, restricted mechanism
        (DASHBOARD_USER/PASS Basic Auth) -- must not require, or fall
        through to, the buyer's own session cookie at all."""
        request = _mock_request(auth_header=_basic_auth_header("admin", "test-admin-pass"))
        owner = main.require_lead_ownership(request, "PLANIT-REF-001")
        self.assertEqual(owner, "admin")
        mock_get_lead_owner.assert_not_called()

    def test_wrong_admin_password_is_denied_and_falls_through_to_anonymous_check(self):
        request = _mock_request(auth_header=_basic_auth_header("admin", "wrong-password"))
        with self.assertRaises(main.HTTPException) as ctx:
            main.require_lead_ownership(request, "PLANIT-REF-001")
        self.assertEqual(ctx.exception.status_code, 404)

    def test_requester_supplied_email_is_never_trusted(self):
        """Guards against a regression where someone 'fixes' this by reading
        an email from a query param/header instead of the verified session
        -- require_lead_ownership's signature does not even accept one."""
        import inspect
        params = list(inspect.signature(main.require_lead_ownership).parameters)
        self.assertEqual(params, ["request", "lead_reference_or_id"])


class TestGenerateLetterRouteEnforcesOwnership(unittest.TestCase):
    """Integration-style: drives the actual route function (not just the
    helper) to prove the fix is wired in, not just defined and unused."""

    def setUp(self):
        os.environ["DASHBOARD_USER"] = "admin"
        os.environ["DASHBOARD_PASS"] = "test-admin-pass"
        main._SESSION_SECRET = b"test-session-secret"

    @patch("main.fulfilment.get_lead_owner")
    @patch("main.database.get_db_conn")
    def test_generate_letter_blocks_non_owning_session(self, mock_get_db_conn, mock_get_lead_owner):
        conn = MagicMock()
        cur = MagicMock()
        conn.cursor.return_value = cur
        # generate_homeowner_letter now makes THREE fetchone() calls while
        # the connection is open: (0) 2026-09-23 Request D, Part 1's new
        # resolve_buyer_facing_reference lookup at the very top of the
        # route (a lead_allocations.id lookup for the incoming path param
        # -- "PLANIT-REF-001" here is a real council reference, not a
        # buyer-facing UUID, so it correctly falls through unchanged), (1)
        # the route's own lead SELECT, (2) an optional letter_content.
        # get_contractor_settings lookup for the logged-in session (None
        # here = this contractor has no saved letter settings yet, which
        # is the common/expected case and exercises the fallback path in
        # the route).
        cur.fetchone.side_effect = [
            None,
            ("PLANIT-REF-001", "1 Real Street, Leeds", "Fell one oak", "Leeds City Council", "claimed"),
            None,
        ]
        mock_get_db_conn.return_value = conn
        mock_get_lead_owner.return_value = "real-buyer@example.com"

        request = _mock_request(cookie_value=main._sign_session_cookie("attacker@example.com"))
        with self.assertRaises(main.HTTPException) as ctx:
            main.generate_homeowner_letter(request, "PLANIT-REF-001")
        self.assertEqual(ctx.exception.status_code, 404)

    @patch("main.fulfilment.get_lead_owner")
    @patch("main.database.get_db_conn")
    def test_generate_letter_allows_the_real_buyer_with_saved_settings(self, mock_get_db_conn, mock_get_lead_owner):
        conn = MagicMock()
        cur = MagicMock()
        conn.cursor.return_value = cur
        cur.fetchone.side_effect = [
            None,  # 2026-09-23 Request D, Part 1: resolve_buyer_facing_reference miss (see comment above)
            ("PLANIT-REF-001", "1 Real Street, Leeds", "Fell one oak", "Leeds City Council", "claimed"),
            ("real-buyer@example.com", "Real Buyer Tree Care", "07700 900123", "", "", "", 1, True, "fp", "friendly_introduction", "", "", ""),
        ]
        mock_get_db_conn.return_value = conn
        mock_get_lead_owner.return_value = "real-buyer@example.com"
        os.environ["TREEKEY_PRIVACY_CONTACT_EMAIL"] = "privacy@treekey.co.uk"

        request = _mock_request(cookie_value=main._sign_session_cookie("real-buyer@example.com"))
        response = main.generate_homeowner_letter(request, "PLANIT-REF-001")
        # Doesn't raise -- HTMLResponse is stubbed to a plain object in this
        # sandbox (see test_main.py's _FakeHTMLResponse), so we only assert
        # "did not block", which is what this test exists to prove.
        self.assertIsNotNone(response)
        # And it's actually the successful f-string page (containing the
        # rendered letter iframe), not the LetterConfigError branch's
        # HTMLResponse(status_code=500) -- that branch would fail this
        # isinstance check, and would previously have been masked by a
        # swallowed IndexError (a static MagicMock fetchone.return_value
        # used to make BOTH calls return the 5-column lead row, which
        # get_contractor_settings would then misread as a 9-column
        # settings row and blow up on row[5..8]).
        self.assertIsInstance(response, str)
        self.assertIn("letter-frame", response)
        self.assertIn("Real Buyer Tree Care", response)

    @patch("main.fulfilment.get_lead_owner")
    @patch("main.database.get_db_conn")
    def test_generate_letter_sends_a_real_buyer_with_no_saved_settings_to_letter_settings(
            self, mock_get_db_conn, mock_get_lead_owner):
        """2026-09-24 handoff (Nick's task, item 3: 'never save or print
        example business names, numbers or fictional credentials'): this
        used to fall straight through to this route's own placeholder
        `company`/`phone` query-string defaults ("Your Local Tree
        Specialists" / "07XXX XXXXXX"), rendered as if they were this
        real, logged-in contractor's actual business identity -- see
        generate_homeowner_letter's own comment. It must now redirect them
        to add their real details first, `next` carrying them straight
        back to this exact letter."""
        conn = MagicMock()
        cur = MagicMock()
        conn.cursor.return_value = cur
        cur.fetchone.side_effect = [
            None,  # resolve_buyer_facing_reference miss
            ("PLANIT-REF-001", "1 Real Street, Leeds", "Fell one oak", "Leeds City Council", "claimed"),
            None,  # no saved contractor_letter_settings at all
        ]
        mock_get_db_conn.return_value = conn
        mock_get_lead_owner.return_value = "real-buyer@example.com"

        request = _mock_request(cookie_value=main._sign_session_cookie("real-buyer@example.com"),
                                 path="/generate-letter/PLANIT-REF-001")
        response = main.generate_homeowner_letter(request, "PLANIT-REF-001")
        self.assertEqual(getattr(response, "status_code", None), 303)
        self.assertIn("/letter-settings", response.url)
        self.assertIn("next=", response.url)
        self.assertIn(urllib.parse.quote("/generate-letter/PLANIT-REF-001", safe=""), response.url)

    @patch("main.fulfilment.get_lead_owner")
    @patch("main.database.get_db_conn")
    def test_generate_street_flyer_blocks_non_owning_session(self, mock_get_db_conn, mock_get_lead_owner):
        conn = MagicMock()
        cur = MagicMock()
        conn.cursor.return_value = cur
        # 2026-09-24 handoff: generate_street_flyer now also loads this
        # session's own contractor_letter_settings row (if any) while the
        # connection is open, same as generate_homeowner_letter already
        # did -- see that route's comment -- so this needs the same THREE-
        # call fetchone sequence: (0) resolve_buyer_facing_reference miss,
        # (1) the route's own lead SELECT, (2) the settings lookup (None
        # here; irrelevant to this test either way, since ownership is
        # checked and must reject this session before anything from
        # settings is ever used).
        cur.fetchone.side_effect = [
            None,
            ("PLANIT-REF-001", "1 Real Street, Leeds", "Fell one oak", "claimed"),
            None,
        ]
        mock_get_db_conn.return_value = conn
        mock_get_lead_owner.return_value = "real-buyer@example.com"

        request = _mock_request(cookie_value=main._sign_session_cookie("attacker@example.com"),
                                 path="/generate-street-flyer/PLANIT-REF-001")
        with self.assertRaises(main.HTTPException) as ctx:
            main.generate_street_flyer(request, "PLANIT-REF-001")
        self.assertEqual(ctx.exception.status_code, 404)


if __name__ == "__main__":
    unittest.main()

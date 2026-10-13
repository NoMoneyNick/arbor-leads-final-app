"""test_auth_pause.py -- the AUTH_EMAILS_PAUSED emergency switch (13 Oct 2026).

When AUTH_EMAILS_PAUSED is true, /api/request-magic-link and /api/signup must answer HTTP 503 and must NOT
create a login record or send any email. When it is unset/false, behaviour is unchanged.
Mocks only: no database, no email, no network.

Run with:  python -m unittest tests.test_auth_pause -v
"""
import asyncio
import os
import re
import sys
import unittest
from unittest.mock import MagicMock, patch

_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
_APP_DIR = os.path.dirname(_THIS_DIR)
sys.path.insert(0, _APP_DIR)
sys.path.insert(0, _THIS_DIR)

try:
    import test_access_control  # noqa: F401  (populates sys.modules stubs + imports main)
    import main
    from test_letter_onboarding import _mock_request
    _IMPORT_ERROR = None
except Exception as _e:  # FastAPI etc. not installed (e.g. a bare sandbox): route tests are skipped, source tests still run
    main, _mock_request, _IMPORT_ERROR = None, None, _e


def _run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


class _FormRequest:
    def __init__(self, fields, host="127.0.0.77"):
        self.client = MagicMock(host=host)
        self.cookies = {}
        self.headers = {}
        self._fields = fields

    async def form(self):
        return dict(self._fields)


def _capture():
    captured = {}

    def fn(content=None, *a, **k):
        captured.update(html=content, status=k.get("status_code", 200), headers=k.get("headers") or {})
        return captured
    return captured, fn


def _mail_module():
    mod = sys.modules.get("notifications")
    if mod is not None and not hasattr(mod, "send_transactional_email"):
        mod.send_transactional_email = MagicMock(return_value=True)
    return mod


@unittest.skipIf(main is None, f"main.py cannot be imported here: {_IMPORT_ERROR!r}")
class TestAuthEmailPauseRoutes(unittest.TestCase):

    def _post_login(self, env):
        captured, fn = _capture()
        with patch.dict(os.environ, env, clear=False), \
             patch.object(main, "_check_rate_limit", return_value=True), \
             patch.object(main.database, "create_magic_auth_token", MagicMock(return_value={"token": "DUMMY-TEST-VALUE-NOT-REAL-0001", "otp": "1", "email": "a@b.com"})) as create, \
             patch.object(_mail_module(), "send_transactional_email", return_value=True) as mail, \
             patch.object(main, "HTMLResponse", side_effect=fn):
            result = _run(main.request_magic_link(_FormRequest({"contact": "someone@example.com"})))
        return result, captured, create, mail

    def _post_signup(self, env):
        captured, fn = _capture()
        fields = {"email": "Dave@Apex-Trees.co.uk", "responsible_name": "Dave Smith", "business_name": "Apex Trees Ltd",
                  "phone": "01234 567890", "agree_terms": "yes"}
        with patch.dict(os.environ, env, clear=False), \
             patch.object(main, "_check_rate_limit", return_value=True), \
             patch.object(main.database, "email_has_existing_account", MagicMock(return_value=False), create=True), \
             patch.object(main.database, "create_magic_auth_token", MagicMock(return_value={"token": "DUMMY-TEST-VALUE-NOT-REAL-0001", "otp": "1", "email": "a@b.com"})) as create, \
             patch.object(_mail_module(), "send_transactional_email", return_value=True) as mail, \
             patch.object(main, "HTMLResponse", side_effect=fn):
            result = _run(main.first_time_signup(_FormRequest(fields)))
        return result, captured, create, mail

    def test_login_route_returns_503_and_sends_nothing_when_paused(self):
        for value in ("true", "TRUE", "1", "yes", "on"):
            _, cap, create, mail = self._post_login({"AUTH_EMAILS_PAUSED": value})
            self.assertEqual(cap["status"], 503, value)
            self.assertEqual(cap["headers"].get("Retry-After"), "900")
            create.assert_not_called()
            mail.assert_not_called()

    def test_signup_route_returns_503_and_sends_nothing_when_paused(self):
        _, cap, create, mail = self._post_signup({"AUTH_EMAILS_PAUSED": "true"})
        self.assertEqual(cap["status"], 503)
        create.assert_not_called()
        mail.assert_not_called()

    def test_paused_page_never_contains_a_token_or_the_address(self):
        _, cap, _, _ = self._post_login({"AUTH_EMAILS_PAUSED": "true"})
        self.assertNotIn("someone@example.com", cap["html"])
        self.assertNotIn("token=", cap["html"])
        self.assertIn("temporarily unavailable", cap["html"].lower())

    def test_unpaused_login_still_works_exactly_as_before(self):
        for value in ("", "false", "0", "no"):
            _, cap, create, mail = self._post_login({"AUTH_EMAILS_PAUSED": value})
            self.assertEqual(cap.get("status", 200), 200, value)
            create.assert_called_once()
            mail.assert_called_once()

    def test_unpaused_signup_still_works_exactly_as_before(self):
        _, cap, create, mail = self._post_signup({"AUTH_EMAILS_PAUSED": "false"})
        self.assertEqual(cap.get("status", 200), 200)
        create.assert_called_once()
        mail.assert_called_once()

    def test_deliver_login_link_itself_refuses_to_send_when_paused(self):
        captured, fn = _capture()
        with patch.dict(os.environ, {"AUTH_EMAILS_PAUSED": "true"}), \
             patch.object(_mail_module(), "send_transactional_email", return_value=True) as mail, \
             patch.object(main, "HTMLResponse", side_effect=fn):
            main._deliver_login_link(_FormRequest({}), "a@b.com", {"token": "DUMMY-TEST-VALUE-NOT-REAL-0001", "otp": "123456", "email": "a@b.com"}, None)
        mail.assert_not_called()
        self.assertEqual(captured["status"], 503)

    def test_login_and_signup_pages_show_the_notice_when_paused(self):
        with patch.dict(os.environ, {"AUTH_EMAILS_PAUSED": "true"}):
            self.assertIn("temporarily unavailable", main.login_page(_mock_request()).lower())
            self.assertIn("temporarily unavailable", main.free_account_signup_page(_mock_request()).lower())
        with patch.dict(os.environ, {"AUTH_EMAILS_PAUSED": "false"}):
            self.assertNotIn("temporarily unavailable", main.login_page(_mock_request()).lower())


class TestAuthPauseSource(unittest.TestCase):
    """Runs anywhere (reads main.py as text)."""
    @classmethod
    def setUpClass(cls):
        with open(os.path.join(_APP_DIR, "main.py"), encoding="utf-8") as f:
            cls.src = f.read()

    def test_switch_is_read_at_request_time_from_the_environment(self):
        self.assertIn('os.getenv("AUTH_EMAILS_PAUSED", "")', self.src)
        self.assertEqual(self.src.count("def _auth_emails_paused"), 1)

    def test_both_routes_check_the_switch_before_anything_else(self):
        for sig in ("async def request_magic_link(request: Request):\n", ):
            i = self.src.index(sig) + len(sig)
            self.assertTrue(self.src[i:].lstrip().startswith("if _auth_emails_paused():"))
        i = self.src.index('not reveal whether an address is registered."""\n') + len('not reveal whether an address is registered."""\n')
        self.assertTrue(self.src[i:].lstrip().startswith("if _auth_emails_paused():"))

    def test_the_sending_function_has_its_own_guard_before_building_the_link(self):
        i = self.src.index("def _deliver_login_link(")
        j = self.src.index("magic_url = f", i)
        self.assertIn("if _auth_emails_paused():", self.src[i:j])

    def test_503_with_retry_after(self):
        self.assertIn('status_code=503, headers={"Retry-After": "900"}', self.src)

    def test_only_two_routes_create_login_records(self):
        self.assertEqual(len(re.findall(r"database\.create_magic_auth_token\(", self.src)), 2)


if __name__ == "__main__":
    unittest.main()

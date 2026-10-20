"""test_bot_guard.py -- the bot check on sign-in / sign-up (20 Oct 2026).

Proves a request without the hidden form token, with the trap field filled, sent too fast, with a forged/old token,
from another address, or failing Turnstile is refused BEFORE any login record or email is created; and that a normal
request still goes through. Mocks only: no database, no email, no network.

Run with:  python -m unittest tests.test_bot_guard -v
"""
import asyncio
import os
import re
import sys
import time
import unittest
from unittest.mock import MagicMock, patch

_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
_APP_DIR = os.path.dirname(_THIS_DIR)
sys.path.insert(0, _APP_DIR)
sys.path.insert(0, _THIS_DIR)

import bot_guard  # noqa: E402

try:
    import test_access_control  # noqa: F401
    import main
    _IMPORT_ERROR = None
except Exception as _e:
    main, _IMPORT_ERROR = None, _e

SECRET = b"unit-test-secret"
IP = "203.0.113.9"
ON = {"BOT_GUARD": "on", "TURNSTILE_SITE_KEY": "", "TURNSTILE_SECRET_KEY": ""}
TS_ON = {"BOT_GUARD": "on", "TURNSTILE_SITE_KEY": "site-key", "TURNSTILE_SECRET_KEY": "secret-key"}


def _form(token=None, **extra):
    f = {bot_guard.TOKEN_FIELD: token} if token is not None else {}
    f.update(extra)
    return f


class TestBotGuardUnit(unittest.TestCase):
    def test_fresh_token_is_refused_as_too_fast_then_accepted_after_delay(self):
        with patch.dict(os.environ, ON):
            now = 1_000_000
            tok = bot_guard.make_token(SECRET, IP, now=now)
            self.assertEqual(bot_guard.check(_form(tok), SECRET, IP, now=now + 1), (False, "too_fast"))
            self.assertEqual(bot_guard.check(_form(tok), SECRET, IP, now=now + 10), (True, "ok"))

    def test_no_token_means_script_posting_straight_to_url(self):
        with patch.dict(os.environ, ON):
            self.assertEqual(bot_guard.check({}, SECRET, IP), (False, "no_token"))
            self.assertEqual(bot_guard.check(_form("garbage"), SECRET, IP), (False, "no_token"))

    def test_forged_other_address_and_expired_tokens(self):
        with patch.dict(os.environ, ON):
            now = 2_000_000
            good = bot_guard.make_token(SECRET, IP, now=now)
            self.assertEqual(bot_guard.check(_form(f"{now}.{'0' * 32}"), SECRET, IP, now=now + 10), (False, "bad_token"))
            self.assertEqual(bot_guard.check(_form(good), SECRET, "198.51.100.1", now=now + 10), (False, "bad_token"))
            self.assertEqual(bot_guard.check(_form(good), b"other", IP, now=now + 10), (False, "bad_token"))
            self.assertEqual(bot_guard.check(_form(good), SECRET, IP, now=now + bot_guard.MAX_SECONDS + 5), (False, "token_expired"))

    def test_trap_field_filled_is_refused(self):
        with patch.dict(os.environ, ON):
            now = 3_000_000
            tok = bot_guard.make_token(SECRET, IP, now=now)
            f = _form(tok, **{bot_guard.HONEYPOT_FIELD: "http://spam.example"})
            self.assertEqual(bot_guard.check(f, SECRET, IP, now=now + 10), (False, "honeypot"))

    def test_turnstile_required_and_checked_when_configured(self):
        with patch.dict(os.environ, TS_ON):
            now = 4_000_000
            tok = bot_guard.make_token(SECRET, IP, now=now)
            self.assertEqual(bot_guard.check(_form(tok), SECRET, IP, now=now + 10), (False, "no_turnstile"))
            f = _form(tok, **{bot_guard.TURNSTILE_FIELD: "abc"})
            with patch.object(bot_guard, "_verify_turnstile", return_value=True):
                self.assertEqual(bot_guard.check(f, SECRET, IP, now=now + 10), (True, "ok"))
            with patch.object(bot_guard, "_verify_turnstile", return_value=False):
                self.assertEqual(bot_guard.check(f, SECRET, IP, now=now + 10), (False, "turnstile_failed"))
            with patch.object(bot_guard, "_verify_turnstile", side_effect=OSError("down")):
                self.assertEqual(bot_guard.check(f, SECRET, IP, now=now + 10), (False, "turnstile_unavailable"))

    def test_fields_html_contains_trap_token_and_widget_only_when_configured(self):
        with patch.dict(os.environ, ON):
            h = bot_guard.fields_html(SECRET, IP)
            self.assertIn(bot_guard.HONEYPOT_FIELD, h)
            self.assertIn(bot_guard.TOKEN_FIELD, h)
            self.assertNotIn("turnstile", h.lower())
        with patch.dict(os.environ, TS_ON):
            h = bot_guard.fields_html(SECRET, IP)
            self.assertIn("cf-turnstile", h)
            self.assertIn('data-sitekey="site-key"', h)
            self.assertNotIn("secret-key", h)

    def test_off_switch(self):
        with patch.dict(os.environ, {"BOT_GUARD": "off"}):
            self.assertEqual(bot_guard.check({}, SECRET, IP), (True, "off"))
            self.assertEqual(bot_guard.fields_html(SECRET, IP), "")


def _run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


class _FormRequest:
    def __init__(self, fields, host=IP):
        self.client = MagicMock(host=host)
        self.cookies = {}
        self.headers = {}
        self._fields = fields

    async def form(self):
        return dict(self._fields)


@unittest.skipIf(main is None, f"main.py cannot be imported here: {_IMPORT_ERROR!r}")
class TestBotGuardRoutes(unittest.TestCase):
    def setUp(self):
        self._secret = main._SESSION_SECRET
        main._SESSION_SECRET = SECRET

    def tearDown(self):
        main._SESSION_SECRET = self._secret

    def _post_login(self, fields):
        create = MagicMock(return_value={"token": "DUMMY-TEST-VALUE-NOT-REAL-0001", "otp": "1", "email": "a@b.com"})
        with patch.dict(os.environ, ON), \
             patch.object(main, "_check_rate_limit", return_value=True), \
             patch.object(main.database, "create_magic_auth_token", create), \
             patch.object(main, "_deliver_login_link", MagicMock(return_value="DELIVERED")) as deliver:
            result = _run(main.request_magic_link(_FormRequest(fields)))
        return result, create, deliver

    def test_login_without_token_creates_nothing_and_sends_nothing(self):
        result, create, deliver = self._post_login({"contact": "victim@example.com"})
        create.assert_not_called()
        deliver.assert_not_called()
        self.assertEqual(getattr(result, "status_code", 303), 303)
        self.assertIn("/login?error=", str(getattr(result, "url", getattr(result, "headers", result))))

    def test_login_with_trap_field_creates_nothing(self):
        old = bot_guard.make_token(SECRET, IP, now=time.time() - 60)
        result, create, deliver = self._post_login({"contact": "x@example.com", bot_guard.TOKEN_FIELD: old,
                                                    bot_guard.HONEYPOT_FIELD: "spam"})
        create.assert_not_called()
        deliver.assert_not_called()

    def test_login_with_valid_token_goes_through(self):
        old = bot_guard.make_token(SECRET, IP, now=time.time() - 60)
        result, create, deliver = self._post_login({"contact": "dave@example.com", bot_guard.TOKEN_FIELD: old})
        create.assert_called_once()
        deliver.assert_called_once()

    def test_signup_without_token_creates_nothing(self):
        fields = {"email": "Dave@Apex-Trees.co.uk", "responsible_name": "Dave Smith", "business_name": "Apex Trees Ltd"}
        create = MagicMock()
        with patch.dict(os.environ, ON), \
             patch.object(main, "_check_rate_limit", return_value=True), \
             patch.object(main.database, "create_magic_auth_token", create), \
             patch.object(main, "_first_time_signup_page_html", MagicMock(return_value="PAGE")) as page, \
             patch.object(main, "HTMLResponse", side_effect=lambda content=None, *a, **k: (content, k.get("status_code", 200))):
            result = _run(main.first_time_signup(_FormRequest(fields)))
        create.assert_not_called()
        self.assertEqual(result, ("PAGE", 400))
        self.assertEqual(page.call_args.kwargs.get("error"), bot_guard.FAIL_MESSAGE)

    def test_login_and_signup_forms_carry_the_hidden_fields(self):
        src = open(os.path.join(_APP_DIR, "main.py"), encoding="utf-8").read()
        self.assertEqual(len(re.findall(r"\{_bot_fields\(request\)", src)), 2)
        with patch.dict(os.environ, ON):
            self.assertIn(bot_guard.TOKEN_FIELD, main._bot_fields(_FormRequest({})))


if __name__ == "__main__":
    unittest.main()

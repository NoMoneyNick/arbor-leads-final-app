"""
test_magic_link_credential_exposure.py -- 2026-09-23, CRITICAL SECURITY FIX
(external review finding on the previously-delivered checkpoint, verified
against the live code before fixing -- confirmed real, not assumed): the
confirmation page /api/request-magic-link (main.request_magic_link)
returned used to render an "Open on This Device Instead" link straight to
/verify-login?token=<the real bearer token> -- i.e. the actual login
credential, baked into the HTTP response returned to WHOEVER submitted the
form, with no check that they actually control the target inbox. That
token is exactly what database.verify_magic_auth_token accepts, with no
other check at all, to create a full logged-in session (see
main.verify_login). So the old page handed a working credential for
`contact`'s account to anyone who could see that one HTTP response --
the requester themselves regardless of whether they own that inbox, a
shared/logged terminal, a proxy, browser history -- without ever needing
real access to `contact`'s inbox. This is exactly the same class of bug as
the already-fixed Sep 8 2026 OTP-echo issue (see main.py's own comment on
that fix, right above the code this file tests) -- that earlier fix never
caught the token/magic_url, which is at least as sensitive since it's a
direct-use bearer credential, not a second-factor code that still needs
the account's own email address to redeem.

Fix (main.py, request_magic_link): the credential-bearing link was removed
from the rendered response outright, not just reworded -- there is no safe
way to offer a "click here" shortcut on that page without putting the
token in it. The token now reaches the caller ONLY via the private email
channel (notifications.send_transactional_email). A runtime assertion was
also added in main.py itself (`assert auth_data["token"] not in
page_html`) as a second line of defence, independent of this test file.

Covers exactly what the review asked for:
  1. The returned HTML never contains the raw token, the "token=" query
     fragment, or a /verify-login?token link -- with and without a `next`
     return-to param present (proving the `next`-threading code added
     earlier this session doesn't reintroduce it another way).
  2. The token still reaches the contractor via the one channel it always
     should have -- the private email body -- so the fix isn't an
     overcorrection that breaks login entirely.
  3. Session creation (main.verify_login) genuinely requires the
     privately-delivered credential: an unrecognised/guessed token is
     rejected, never silently authenticated.

Run with:
    python -m unittest tests.test_magic_link_credential_exposure -v

NOTE on patch targets (2026-09-23 fix, found while chasing a full-suite-only
failure): every database.* call below is patched as `main.database.X`, never
the string form `patch("database.X", ...)`. main.py does a module-level
`import database` (its own `main.database` name is bound once, at main's
first import, and never moves again), but the string form re-resolves
`sys.modules["database"]` at PATCH time -- and under `unittest discover`,
tests/test_letter_promise_gate.py unconditionally `del`s and replaces
sys.modules["database"] with a bare, attribute-less module (see its own
comment on why that swap is "safe" for main.py/address_release.py, which
bind their own `database` name once and don't look at sys.modules again).
Alphabetically, that file runs before this one, so by the time these tests
executed under the full suite, `patch("database.create_magic_auth_token")`
was resolving against that bare replacement -- not the module test_main.py
had already populated -- hence `AttributeError: <module 'database'> does
not have the attribute 'create_magic_auth_token'` in the full run despite
this file passing every time it was run standalone. Patching main.database
directly is the same fix test_indirect_identifier_gating.py's
`_ensure_database_stub_has_dashboard_attrs` already documents for the same
underlying class of bug.

A second, related instance of the exact same bug class was found (and
fixed) in the `notifications` module too, for a different reason: unlike
`database`, main.py's `request_magic_link` does NOT bind `notifications` at
module level -- it does a LOCAL `import notifications` inside the function
body (main.py:7132), which resolves fresh against sys.modules on every
call. So there is no stable `main.notifications` to patch here the way
`main.database` works above; whatever this file's own module-level `import
notifications` bound is irrelevant too, since the same
tests/test_letter_promise_gate.py file that wipes `sys.modules["database"]`
also wipes `sys.modules["notifications"]` with a bare, attribute-less
module. `_notifications_module()` below re-fetches sys.modules["notifications"]
at TEST EXECUTION time (matching when request_magic_link's own local import
runs) and idempotently ensures the one attribute these tests need is
present -- the same pattern test_indirect_identifier_gating.py's own
`_ensure_shared_notifications_stub_has_dashboard_attrs` already uses for
this exact class of bug.
"""
import os
import sys
import unittest
from unittest.mock import MagicMock, patch

_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
_APP_DIR = os.path.dirname(_THIS_DIR)
sys.path.insert(0, _APP_DIR)
sys.path.insert(0, _THIS_DIR)

import test_access_control  # noqa: E402  (populates sys.modules stubs + imports test_main + main)
import main  # noqa: E402


def _notifications_module():
    """See this file's own module docstring note on why this can't just be
    a plain `import notifications` reference patched directly."""
    mod = sys.modules.get("notifications")
    if mod is not None and not hasattr(mod, "send_transactional_email"):
        mod.send_transactional_email = MagicMock(return_value=True)
    return mod


class _FakeForm(dict):
    pass


class _FakeRequest:
    def __init__(self, contact, next_val=None, host="127.0.0.10"):
        self.client = MagicMock(host=host)
        # _shared_nav_html (rendered into the confirmation page) reads
        # request.cookies/.headers -- an anonymous visitor here, same as a
        # real first-time magic-link requester.
        self.cookies = {}
        self.headers = {}
        self._contact = contact
        self._next = next_val

    async def form(self):
        fields = {"contact": self._contact}
        if self._next:
            fields["next"] = self._next
        return _FakeForm(fields)


def _capture_html():
    """main.HTMLResponse (via test_access_control's fake fastapi.responses
    stub) has __init__(self, *a, url=None, status_code=200, **k) -- a
    positional content argument (request_magic_link calls
    `HTMLResponse(page_html)` positionally) is silently swallowed by `*a`
    and never stored anywhere readable. Patching main.HTMLResponse itself
    with this side_effect is the established way this session's other test
    files capture rendered content (see tests/test_letter_setup_checkout_
    gate.py's own note on the same stub gotcha)."""
    captured = {}

    def _capture(content=None, *a, **k):
        captured["html"] = content
        return content
    return captured, _capture


class TestConfirmationPageNeverLeaksTheCredential(unittest.IsolatedAsyncioTestCase):

    async def test_response_html_never_contains_the_raw_token(self):
        fake_request = _FakeRequest("dave@apex-trees.co.uk", host="127.0.0.10")
        captured, capture_fn = _capture_html()

        with patch.object(main, "_check_rate_limit", return_value=True), \
             patch.object(main.database, "create_magic_auth_token",
                   return_value={"token": "super-secret-bearer-token-xyz", "otp": "654321", "email": "dave@apex-trees.co.uk"}), \
             patch.object(_notifications_module(), "send_transactional_email", return_value=True), \
             patch.object(main, "HTMLResponse", side_effect=capture_fn):
            await main.request_magic_link(fake_request)

        html = captured.get("html") or ""
        self.assertTrue(html, "expected the confirmation page's HTML to have been captured")
        self.assertNotIn("super-secret-bearer-token-xyz", html)
        self.assertNotIn("token=", html)
        self.assertNotIn("/verify-login?token", html)
        self.assertNotIn("Open on This Device Instead", html)

    async def test_response_html_omits_the_credential_even_with_a_next_param(self):
        """Same assertion with the `next` return-to param present too --
        proving the checkout-gate `next`-threading work earlier this
        session doesn't reintroduce the leak via a different code path."""
        fake_request = _FakeRequest("dave@apex-trees.co.uk", next_val="/checkout/starter", host="127.0.0.11")
        captured, capture_fn = _capture_html()

        with patch.object(main, "_check_rate_limit", return_value=True), \
             patch.object(main.database, "create_magic_auth_token",
                   return_value={"token": "another-secret-token", "otp": "111111", "email": "dave@apex-trees.co.uk"}), \
             patch.object(_notifications_module(), "send_transactional_email", return_value=True), \
             patch.object(main, "HTMLResponse", side_effect=capture_fn):
            await main.request_magic_link(fake_request)

        html = captured.get("html") or ""
        self.assertNotIn("another-secret-token", html)
        self.assertNotIn("token=", html)

    async def test_the_emailed_body_still_carries_the_real_token(self):
        """Flow check that the fix didn't overcorrect: the token must
        still reach the contractor somehow -- just only via the private
        channel (the email), never the HTTP response. A real user must
        still be able to complete login from their own inbox."""
        fake_request = _FakeRequest("dave@apex-trees.co.uk", host="127.0.0.12")
        _, capture_fn = _capture_html()

        with patch.object(main, "_check_rate_limit", return_value=True), \
             patch.object(main.database, "create_magic_auth_token",
                   return_value={"token": "the-real-token", "otp": "222222", "email": "dave@apex-trees.co.uk"}), \
             patch.object(_notifications_module(), "send_transactional_email", return_value=True) as mock_send, \
             patch.object(main, "HTMLResponse", side_effect=capture_fn):
            await main.request_magic_link(fake_request)

        mock_send.assert_called_once()
        _, kwargs = mock_send.call_args
        self.assertEqual(kwargs.get("to_email"), "dave@apex-trees.co.uk")
        self.assertIn("the-real-token", kwargs.get("html_body", ""))


class TestSessionCreationRequiresThePrivatelyDeliveredCredential(unittest.TestCase):

    def test_verify_login_grants_no_session_for_an_unrecognised_token(self):
        """database.verify_magic_auth_token is the sole gate on session
        creation. An unrecognised/guessed token (never one that reached
        anyone via the HTTP response, now that the leak is fixed) is
        rejected -- redirected back to /login with an error, never
        silently treated as authenticated."""
        with patch.object(main.database, "verify_magic_auth_token", return_value=None), \
             patch.object(main, "_check_rate_limit", return_value=True):
            fake_request = MagicMock()
            fake_request.client = MagicMock(host="127.0.0.13")
            response = main.verify_login(fake_request, token="not-a-real-token")
        self.assertIn("/login", response.url)
        self.assertIn("error", response.url)

    def test_verify_login_grants_a_session_only_for_the_real_token(self):
        """Positive-path sanity check alongside the negative one above --
        the ONLY thing that succeeds is the actual, privately-delivered
        credential being presented back."""
        with patch.object(main.database, "verify_magic_auth_token", return_value="dave@apex-trees.co.uk") as mock_verify, \
             patch.object(main, "_check_rate_limit", return_value=True), \
             patch.object(main.database, "get_contractor_subscription", return_value={"active": True}):
            fake_request = MagicMock()
            fake_request.client = MagicMock(host="127.0.0.14")
            response = main.verify_login(fake_request, token="the-real-token-the-user-actually-received-by-email")
        mock_verify.assert_called_once_with(
            token="the-real-token-the-user-actually-received-by-email", otp=None, email=None)
        self.assertEqual(response.url, "/dashboard")


if __name__ == "__main__":
    unittest.main()

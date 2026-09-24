"""
test_letter_setup_checkout_gate.py -- 2026-09-23, Request E ("Also
integrate the existing letter selector/editor into account setup ...
Require completed letter setup before the first purchase that includes
mailing. Reuse the existing approval and fingerprint mechanisms."), REVISED
same day per Nick's explicit follow-up: "Require account creation/sign-in
and completed letter approval before all purchases that include mailing,
including first-time buyers. Preserve the intended purchase through
onboarding, but do not reserve a lead or contact Stripe until setup is
approved."

Covers the genuinely new pieces of behaviour these two requests added on
top of the pre-existing /letter-settings setup/preview/approve journey
(that journey's own auth/ownership/fingerprint-integrity properties are
already covered by tests/test_letter_settings_routes.py and
tests/test_letter_settings_journey.py -- not re-tested here):

  1. main._letter_setup_complete(account_email) -- the one new predicate
     this request adds, applying the exact same bar
     worker.promote_pending_approvals already enforces before any letter
     is actually sent (approved AND still-current fingerprint), fail-
     closed (treated as incomplete) on any DB error.

  2. main.checkout() (GET /checkout/{plan_key}) and checkout_post() (POST,
     the subscription area-form submit) now require BOTH a signed session
     cookie AND completed letter setup before reserving any inventory or
     talking to Stripe, for BOTH the single-lead-purchase branch and the
     subscription branch -- including a brand-new/anonymous visitor, who
     is sent through /login first. There is no separate "create an
     account" step: /login already issues a session cookie for any email,
     new or existing, so "require account creation/sign-in" is satisfied
     by requiring a session cookie before proceeding.

  3. The return-to ("next") mechanism, now chained across TWO detours: an
     anonymous visitor goes checkout -> /login?next=<checkout URL> (the
     pre-existing "sign in for your discount" mechanism, unmodified) ->
     back to checkout, which then re-checks and may send a logged-in-but-
     incomplete contractor on to checkout -> /letter-settings?next=
     <checkout URL> -> back to checkout. approve_letter_settings() sends
     the contractor straight back to a safe `next` (never an unsafe/
     foreign URL), reusing _safe_next_url -- the same validator the
     pre-existing /login flow already relies on -- rather than a second
     bespoke one.

Run with:
    python -m unittest tests.test_letter_setup_checkout_gate -v
"""
import os
import sys
import unittest
import urllib.parse
from unittest.mock import MagicMock, patch

_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
_APP_DIR = os.path.dirname(_THIS_DIR)
sys.path.insert(0, _APP_DIR)
sys.path.insert(0, _THIS_DIR)

import test_access_control  # noqa: E402  (populates sys.modules stubs + imports test_main + main)
import main  # noqa: E402
import letter_content  # noqa: E402


class _FakeURL:
    def __init__(self, path, query=""):
        self.path = path
        self.query = query


def _mock_request(cookie_value=None, path="/checkout/single_lead_small", query_params=None):
    query_params = query_params or {}
    req = MagicMock()
    req.cookies = {"treekey_contractor_session": cookie_value} if cookie_value else {}
    req.headers = {}
    req.query_params = query_params
    query_str = urllib.parse.urlencode(query_params)
    req.url = _FakeURL(path, query_str)

    async def _form():
        return {}
    req.form = _form
    return req


_PAYMENT_PLAN = {
    "single_lead_small": {"name": "Single Lead Unlock (Entry)", "amount": 1900, "mode": "payment"},
}
_SUBSCRIPTION_PLAN = {
    "starter": {"name": "TreeKey Starter", "amount": 3900, "mode": "subscription"},
}
_ALL_PLANS = {**_PAYMENT_PLAN, **_SUBSCRIPTION_PLAN}


class _SessionTestBase(unittest.TestCase):
    def setUp(self):
        os.environ["SESSION_SECRET"] = "test-session-secret"
        main._SESSION_SECRET = b"test-session-secret"

    def _signed_cookie(self, email: str) -> str:
        return main._sign_session_cookie(email)


class TestLetterSetupComplete(_SessionTestBase):
    """main._letter_setup_complete -- the new completeness predicate."""

    def _settings(self, approved=True, current=True, **overrides):
        s = letter_content.ContractorLetterSettings(
            contractor_email="contractor@example.com", business_name="Acme Trees", phone="07000000000",
            approved=approved, **overrides,
        )
        s.approved_fingerprint = letter_content.template_fingerprint(s) if current else "stale-fingerprint-value"
        return s

    def test_no_account_email_is_incomplete(self):
        self.assertFalse(main._letter_setup_complete(""))
        self.assertFalse(main._letter_setup_complete(None))

    @patch("main.database.get_db_conn")
    def test_no_saved_settings_row_is_incomplete(self, mock_get_db_conn):
        mock_get_db_conn.return_value = MagicMock()
        with patch("main.letter_content.get_contractor_settings", return_value=None):
            self.assertFalse(main._letter_setup_complete("contractor@example.com"))

    @patch("main.database.get_db_conn")
    def test_saved_but_not_approved_is_incomplete(self, mock_get_db_conn):
        mock_get_db_conn.return_value = MagicMock()
        settings = self._settings(approved=False, current=False)
        with patch("main.letter_content.get_contractor_settings", return_value=settings):
            self.assertFalse(main._letter_setup_complete("contractor@example.com"))

    @patch("main.database.get_db_conn")
    def test_approved_but_stale_fingerprint_is_incomplete(self, mock_get_db_conn):
        """Business details changed after approval (upsert_contractor_
        settings always resets approved=FALSE on a real change -- this
        models the same 'approved flag true, fingerprint no longer
        matches' edge case is_approval_current itself guards against)."""
        mock_get_db_conn.return_value = MagicMock()
        settings = self._settings(approved=True, current=False)
        with patch("main.letter_content.get_contractor_settings", return_value=settings):
            self.assertFalse(main._letter_setup_complete("contractor@example.com"))

    @patch("main.database.get_db_conn")
    def test_approved_and_current_is_complete(self, mock_get_db_conn):
        mock_get_db_conn.return_value = MagicMock()
        settings = self._settings(approved=True, current=True)
        with patch("main.letter_content.get_contractor_settings", return_value=settings):
            self.assertTrue(main._letter_setup_complete("contractor@example.com"))

    @patch("main.database.get_db_conn")
    def test_a_lookup_error_fails_closed_not_open(self, mock_get_db_conn):
        """A transient DB error while checking must block the purchase
        (return False / 'incomplete'), never silently let it through."""
        mock_get_db_conn.return_value = MagicMock()
        with patch("main.letter_content.get_contractor_settings", side_effect=RuntimeError("db down")):
            self.assertFalse(main._letter_setup_complete("contractor@example.com"))


class TestCheckoutGatesEveryBuyer(_SessionTestBase):
    """GET /checkout/{plan_key} -- both the single-lead-purchase branch and
    the subscription area-selector branch. Revised same day: an anonymous
    visitor is now sent through /login first (account creation/sign-in are
    the same step in this codebase -- /login issues a session cookie for
    any email, new or existing), then, once logged in, through the
    pre-existing letter-setup check."""

    def test_anonymous_single_lead_purchase_is_sent_through_login_first(self):
        """No session cookie at all -- Nick's follow-up instruction:
        'Require account creation/sign-in ... before all purchases that
        include mailing, including first-time buyers.' _letter_setup_
        complete must not even be consulted yet -- there's no account_email
        to check it against until login completes. The intended purchase
        is preserved via `next`, not lost."""
        request = _mock_request(cookie_value=None, query_params={"lead_id": "LEAD-1"})
        with patch.object(main.payments, "PLANS", _PAYMENT_PLAN), \
             patch("main._letter_setup_complete") as mock_complete, \
             patch.object(main.payments, "create_checkout_session") as mock_create:
            result = main.checkout("single_lead_small", request)
        mock_complete.assert_not_called()
        mock_create.assert_not_called()  # never reserves inventory / talks to Stripe
        self.assertEqual(result.status_code, 303)
        self.assertTrue(result.url.startswith("/login?next="))
        next_value = urllib.parse.parse_qs(urllib.parse.urlparse(result.url).query)["next"][0]
        self.assertEqual(next_value, "/checkout/single_lead_small?lead_id=LEAD-1")

    def test_anonymous_subscription_purchase_is_sent_through_login_first(self):
        request = _mock_request(cookie_value=None, path="/checkout/starter")
        with patch.object(main.payments, "PLANS", _SUBSCRIPTION_PLAN), \
             patch("main._letter_setup_complete") as mock_complete:
            result = main.checkout("starter", request)
        mock_complete.assert_not_called()
        self.assertEqual(result.status_code, 303)
        self.assertEqual(result.url, "/login?next=%2Fcheckout%2Fstarter")

    def test_logged_in_incomplete_single_lead_purchase_is_redirected_to_letter_settings(self):
        request = _mock_request(cookie_value=self._signed_cookie("contractor@example.com"),
                                 query_params={"lead_id": "LEAD-1"})
        with patch.object(main.payments, "PLANS", _PAYMENT_PLAN), \
             patch("main._letter_setup_complete", return_value=False) as mock_complete, \
             patch.object(main.payments, "create_checkout_session") as mock_create:
            result = main.checkout("single_lead_small", request)
        mock_complete.assert_called_once_with("contractor@example.com")
        mock_create.assert_not_called()  # never reserves inventory / talks to Stripe
        self.assertEqual(result.status_code, 303)
        self.assertTrue(result.url.startswith("/letter-settings?next="))
        # the return-to target is this exact checkout URL, safely quoted
        next_value = urllib.parse.parse_qs(urllib.parse.urlparse(result.url).query)["next"][0]
        self.assertEqual(next_value, "/checkout/single_lead_small?lead_id=LEAD-1")

    def test_logged_in_complete_single_lead_purchase_proceeds_to_stripe(self):
        request = _mock_request(cookie_value=self._signed_cookie("contractor@example.com"),
                                 query_params={"lead_id": "LEAD-1"})
        with patch.object(main.payments, "PLANS", _PAYMENT_PLAN), \
             patch("main._letter_setup_complete", return_value=True), \
             patch.object(main.payments, "create_checkout_session", return_value="https://stripe.example/session/xyz") as mock_create:
            result = main.checkout("single_lead_small", request)
        mock_create.assert_called_once()
        self.assertEqual(result.url, "https://stripe.example/session/xyz")

    def test_logged_in_incomplete_subscription_purchase_is_redirected_before_the_area_form_renders(self):
        request = _mock_request(cookie_value=self._signed_cookie("contractor@example.com"), path="/checkout/starter")
        with patch.object(main.payments, "PLANS", _SUBSCRIPTION_PLAN), \
             patch("main._letter_setup_complete", return_value=False):
            result = main.checkout("starter", request)
        self.assertEqual(result.status_code, 303)
        self.assertEqual(result.url, "/letter-settings?next=%2Fcheckout%2Fstarter")

    def test_logged_in_complete_subscription_purchase_sees_the_area_form(self):
        request = _mock_request(cookie_value=self._signed_cookie("contractor@example.com"), path="/checkout/starter")
        with patch.object(main.payments, "PLANS", _SUBSCRIPTION_PLAN), \
             patch("main._letter_setup_complete", return_value=True), \
             patch("main.HTMLResponse", side_effect=lambda content=None, *a, **k: content):
            result = main.checkout("starter", request)
        self.assertIn("One last step", result)


class TestCheckoutPostGatesAsDefenseInDepth(_SessionTestBase):
    """POST /checkout/{plan_key} (the subscription area-form submit) --
    same gate, redundant with the GET-time check above, in case that GET
    is ever bypassed (a direct/replayed POST)."""

    def _run(self, coro):
        import asyncio
        return asyncio.get_event_loop().run_until_complete(coro)

    def test_logged_in_incomplete_post_is_redirected_and_never_reaches_stripe(self):
        request = _mock_request(cookie_value=self._signed_cookie("contractor@example.com"), path="/checkout/starter")
        with patch.object(main.payments, "PLANS", _SUBSCRIPTION_PLAN), \
             patch("main._letter_setup_complete", return_value=False), \
             patch.object(main.payments, "create_checkout_session") as mock_create:
            result = self._run(main.checkout_post("starter", request, outcode="NG22", radius=15, job_size="all", agree_terms="yes"))
        mock_create.assert_not_called()
        self.assertEqual(result.status_code, 303)
        self.assertEqual(result.url, "/letter-settings?next=%2Fcheckout%2Fstarter")

    def test_anonymous_post_is_sent_through_login_first(self):
        request = _mock_request(cookie_value=None, path="/checkout/starter")
        with patch.object(main.payments, "PLANS", _SUBSCRIPTION_PLAN), \
             patch("main._letter_setup_complete") as mock_complete, \
             patch.object(main.payments, "create_checkout_session") as mock_create:
            result = self._run(main.checkout_post("starter", request, outcode="NG22", radius=15, job_size="all", agree_terms="yes"))
        mock_complete.assert_not_called()
        mock_create.assert_not_called()
        self.assertEqual(result.status_code, 303)
        self.assertEqual(result.url, "/login?next=%2Fcheckout%2Fstarter")

    def test_logged_in_complete_post_proceeds_past_the_gate(self):
        """Positive case for the POST-side gate: logged in, letter setup
        complete -- must reach the real downstream logic (Stripe), never
        redirected."""
        request = _mock_request(cookie_value=self._signed_cookie("contractor@example.com"), path="/checkout/starter")
        with patch.object(main.payments, "PLANS", _SUBSCRIPTION_PLAN), \
             patch("main._letter_setup_complete", return_value=True) as mock_complete, \
             patch.object(main.database, "resolve_location", return_value={"outcode": "NG22", "full_postcode": None, "lat": 1.0, "lon": 1.0}, create=True), \
             patch.object(main.database, "get_area_capacity_status", return_value={"status": "ok"}, create=True), \
             patch.object(main.database, "TIER_MAX_RADIUS", {}, create=True), \
             patch.object(main.database, "TIER_QUOTAS", {}, create=True), \
             patch.object(main.payments, "create_checkout_session", return_value="https://stripe.example/session/sub") as mock_create:
            result = self._run(main.checkout_post("starter", request, outcode="NG22", radius=15, job_size="all", agree_terms="yes"))
        mock_complete.assert_called_once_with("contractor@example.com")
        mock_create.assert_called_once()
        self.assertEqual(result.url, "https://stripe.example/session/sub")


class TestApproveLetterSettingsReturnsToCheckout(_SessionTestBase):
    """approve_letter_settings() honours a safe `next` and rejects an
    unsafe one, reusing _safe_next_url (the pre-existing /login return-to
    validator) rather than trusting the query string directly."""

    def _run(self, coro):
        import asyncio
        return asyncio.get_event_loop().run_until_complete(coro)

    def _approved_settings(self):
        s = letter_content.ContractorLetterSettings(
            contractor_email="contractor@example.com", business_name="Acme Trees", phone="07000000000",
        )
        return s

    @patch("main.letter_content.approve_template")
    @patch("main.letter_content.get_contractor_settings")
    @patch("main.database.get_db_conn")
    def test_a_safe_checkout_next_is_honoured(self, mock_get_db_conn, mock_get_settings, mock_approve):
        mock_get_db_conn.return_value = MagicMock()
        mock_get_settings.return_value = self._approved_settings()
        request = _mock_request(cookie_value=self._signed_cookie("contractor@example.com"),
                                 query_params={"next": "/checkout/single_lead_small?lead_id=LEAD-1"})
        result = self._run(main.approve_letter_settings(request))
        self.assertEqual(result.status_code, 303)
        # 2026-09-24, second pass: `letter_nudge=continue` is appended so
        # this immediate return to checkout doesn't re-ask the same
        # personalise-or-standard question this contractor just answered
        # by approving -- see approve_letter_settings' own comment.
        self.assertEqual(result.url, "/checkout/single_lead_small?lead_id=LEAD-1&letter_nudge=continue")

    @patch("main.letter_content.approve_template")
    @patch("main.letter_content.get_contractor_settings")
    @patch("main.database.get_db_conn")
    def test_an_unsafe_next_is_ignored_falls_back_to_the_default(self, mock_get_db_conn, mock_get_settings, mock_approve):
        """An off-site (or otherwise invalid) `next` must never be
        followed -- open-redirect prevention, same standard _safe_next_url
        already enforces for the /login flow."""
        mock_get_db_conn.return_value = MagicMock()
        mock_get_settings.return_value = self._approved_settings()
        request = _mock_request(cookie_value=self._signed_cookie("contractor@example.com"),
                                 query_params={"next": "https://evil.example/steal"})
        result = self._run(main.approve_letter_settings(request))
        self.assertEqual(result.status_code, 303)
        self.assertEqual(result.url, "/letter-settings?saved=approved")

    @patch("main.letter_content.approve_template")
    @patch("main.letter_content.get_contractor_settings")
    @patch("main.database.get_db_conn")
    def test_no_next_falls_back_to_the_default_saved_confirmation(self, mock_get_db_conn, mock_get_settings, mock_approve):
        mock_get_db_conn.return_value = MagicMock()
        mock_get_settings.return_value = self._approved_settings()
        request = _mock_request(cookie_value=self._signed_cookie("contractor@example.com"))
        result = self._run(main.approve_letter_settings(request))
        self.assertEqual(result.url, "/letter-settings?saved=approved")


class TestLetterSettingsFormMicrocopyAndAccountIntegration(_SessionTestBase):

    @patch("main.letter_content.get_contractor_settings")
    @patch("main.database.get_db_conn")
    def test_business_intro_microcopy_is_present(self, mock_get_db_conn, mock_get_settings):
        """Request E, verbatim: beside the introduction text box display
        '(this is what customers will see on your introduction letter)'."""
        mock_get_db_conn.return_value = MagicMock()
        mock_get_settings.return_value = None
        request = _mock_request(cookie_value=self._signed_cookie("contractor@example.com"), path="/letter-settings")
        with patch("main.HTMLResponse", side_effect=lambda content=None, *a, **k: content):
            # `next`/`saved` are real FastAPI query-param bindings on this
            # route's own signature (not read from request.query_params in
            # the body, unlike save_letter_settings/approve_letter_settings
            # above) -- passed explicitly here the same way FastAPI's
            # routing would supply them from the URL.
            html_out = main.letter_settings_form(request, next=None)
        self.assertIn("(this is what customers will see on your introduction letter)", html_out)

    @patch("main.letter_content.get_contractor_settings")
    @patch("main.database.get_db_conn")
    def test_forced_detour_via_next_shows_a_why_am_i_here_banner(self, mock_get_db_conn, mock_get_settings):
        mock_get_db_conn.return_value = MagicMock()
        mock_get_settings.return_value = None
        request = _mock_request(cookie_value=self._signed_cookie("contractor@example.com"), path="/letter-settings",
                                 query_params={"next": "/checkout/single_lead_small?lead_id=LEAD-1"})
        with patch("main.HTMLResponse", side_effect=lambda content=None, *a, **k: content):
            html_out = main.letter_settings_form(request, next="/checkout/single_lead_small?lead_id=LEAD-1")
        # 2026-09-24, second pass: wording now explicitly says
        # personalising is optional (matching /letter-onboarding's own
        # copy) rather than just "this is required" -- see
        # _letter_settings_form_html's own comment on why.
        self.assertIn("Add your business details below to continue with your purchase", html_out)
        self.assertIn("personalise its wording now, or leave the optional fields blank", html_out)
        # the form action and preview link both carry `next` forward
        self.assertIn('action="/letter-settings?next=%2Fcheckout%2Fsingle_lead_small', html_out)
        self.assertIn('href="/letter-settings/preview?next=%2Fcheckout%2Fsingle_lead_small', html_out)


class TestAccountPageLetterTemplateIntegration(_SessionTestBase):
    """GET /account (main.my_account_view) -- Request E: 'integrate the
    existing letter selector/editor into account setup ... Allow editing
    in account settings'. Covers the three status states and the amber
    'finish this before your next purchase' banner that only appears when
    setup is incomplete."""

    def _render(self, letter_settings):
        request = _mock_request(cookie_value=self._signed_cookie("contractor@example.com"), path="/account")
        with patch.object(main.database, "get_contractor_subscription", return_value=None, create=True), \
             patch.object(main.database, "get_limbo_account", return_value=None, create=True), \
             patch.object(main.database, "get_contractor_settings", return_value={"notification_preference": "email"}, create=True), \
             patch.object(main.database, "get_payment_history_for_contractor", return_value=[], create=True), \
             patch.object(main.database, "get_db_conn", return_value=MagicMock()), \
             patch("main.letter_content.get_contractor_settings", return_value=letter_settings), \
             patch("main.HTMLResponse", side_effect=lambda content=None, *a, **k: content):
            return main.my_account_view(request)

    def test_no_settings_row_shows_not_started_and_the_banner(self):
        html_out = self._render(None)
        self.assertIn("Not started", html_out)
        self.assertIn("Finish your letter template", html_out)
        self.assertIn("Set Up Letter Template", html_out)

    def test_saved_but_not_approved_shows_that_status_and_the_banner(self):
        s = letter_content.ContractorLetterSettings(
            contractor_email="contractor@example.com", business_name="Acme Trees", phone="07000000000", approved=False,
        )
        html_out = self._render(s)
        self.assertIn("Saved, not yet approved", html_out)
        self.assertIn("Finish your letter template", html_out)
        self.assertIn("Edit Letter Template", html_out)

    def test_approved_and_current_shows_in_use_and_hides_the_banner(self):
        s = letter_content.ContractorLetterSettings(
            contractor_email="contractor@example.com", business_name="Acme Trees", phone="07000000000", approved=True,
        )
        s.approved_fingerprint = letter_content.template_fingerprint(s)
        html_out = self._render(s)
        self.assertIn("Approved &amp; in use", html_out)
        self.assertNotIn("Finish your letter template", html_out)

    def test_approved_but_stale_fingerprint_still_shows_the_banner(self):
        """Details changed after approval -- upsert_contractor_settings
        resets approved=FALSE on any real change, but this proves the
        account page's own status check (like _letter_setup_complete)
        checks fingerprint currency too, not just the approved flag."""
        s = letter_content.ContractorLetterSettings(
            contractor_email="contractor@example.com", business_name="Acme Trees", phone="07000000000", approved=True,
        )
        s.approved_fingerprint = "stale-value"
        html_out = self._render(s)
        self.assertIn("Saved, not yet approved", html_out)
        self.assertIn("Finish your letter template", html_out)


if __name__ == "__main__":
    unittest.main()

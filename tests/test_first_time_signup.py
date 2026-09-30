"""
test_first_time_signup.py -- 2026-09-30, integrated first-time signup.

ONE form (/free-account) carries the account email, the required contact and
business details, the existing optional letter-personalisation fields and the
terms checkbox. What is submitted waits on the verification record and is
applied only when the emailed link/code is verified. Route behaviour is
tested here with mocks; the SQL behaviour (applied exactly once, expired /
reused tokens, existing accounts with and without settings) is proven against
a real disposable PostgreSQL by
tests/postgres_concurrency/run_signup_real_db_tests.py.

Run with:  python -m unittest tests.test_first_time_signup -v
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

import test_access_control  # noqa: E402,F401  (populates sys.modules stubs + imports main)
import main  # noqa: E402
from test_letter_onboarding import _mock_request  # noqa: E402


def _notifications_module():
    mod = sys.modules.get("notifications")
    if mod is not None and not hasattr(mod, "send_transactional_email"):
        mod.send_transactional_email = MagicMock(return_value=True)
    return mod


def _capture():
    captured = {}

    def fn(content=None, *a, **k):
        captured["html"] = content
        captured["status"] = k.get("status_code", 200)
        return content
    return captured, fn


def _run(coro):
    """Run a coroutine on a private loop WITHOUT touching the thread's
    current-event-loop setting (asyncio.run / IsolatedAsyncioTestCase reset
    it, which breaks older tests that call get_event_loop() and sort after
    this file)."""
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


class _FormRequest:
    def __init__(self, fields, host="127.0.0.20"):
        self.client = MagicMock(host=host)
        self.cookies = {}
        self.headers = {}
        self._fields = fields

    async def form(self):
        return dict(self._fields)


GOOD = {
    "email": "Dave@Apex-Trees.co.uk", "responsible_name": "Dave Smith", "business_name": "Apex Trees Ltd",
    "phone": "01234 567890", "agree_terms": "yes",
}
TOKEN = {"token": "SECRET-TOKEN-ABC", "otp": "424242", "email": "dave@apex-trees.co.uk"}


class TestSignupPage(unittest.TestCase):

    def setUp(self):
        self.html = main.free_account_signup_page(_mock_request(), next="/checkout/starter")

    def test_one_form_with_required_and_optional_sections(self):
        self.assertEqual(self.html.count("<form"), 1)
        self.assertIn('action="/api/signup"', self.html)
        self.assertIn("Required", self.html)
        self.assertIn("Optional", self.html)
        for name in ("email", "responsible_name", "business_name", "phone", "agree_terms",
                     "template_key", "contact_first_name", "contact_email", "business_intro", "services_note",
                     "service_area_note", "insurance_note", "qualifications_note",
                     "offer_text", "offer_code", "offer_conditions"):
            self.assertIn(f'name="{name}"', self.html, name)
        self.assertIn("My Account", self.html)
        self.assertIn("Saving is not approval", self.html)

    def test_required_fields_follow_the_agreed_rules(self):
        # email, full name, business name, telephone, terms -- and nothing optional
        required = re.findall(r'<(?:input|textarea|select)[^>]*\brequired\b[^>]*>', self.html)
        names = sorted(re.search(r'name="(\w+)"', r).group(1) for r in required)
        self.assertEqual(names, ["agree_terms", "business_name", "email", "phone", "responsible_name"])

    def test_no_second_choice_and_no_obsolete_free_lead_or_price_promise(self):
        low = self.html.lower()
        for bad in ("free lead", "free-lead", "use the standard letter", "personalise or skip", "intent=standard",
                    "4.99", "£4", "claim my lead", "postcode"):
            self.assertNotIn(bad, low, bad)

    def test_next_is_carried_and_login_link_keeps_it(self):
        self.assertIn('name="next" value="/checkout/starter"', self.html)
        self.assertIn('href="/login?next=%2Fcheckout%2Fstarter"', self.html)

    def test_unsafe_next_is_dropped(self):
        h = main.free_account_signup_page(_mock_request(), next="https://evil.example/x")
        self.assertNotIn("evil.example", h)
        self.assertNotIn('name="next"', h)

    def test_shared_shell_and_footer_mark(self):
        self.assertIn("/static/tailwind.css", self.html)
        self.assertIn("max-width:64px", self.html)

    def test_no_entry_point_still_advertises_the_free_lead(self):
        with open(os.path.join(_APP_DIR, "main.py"), encoding="utf-8") as fh:
            src = fh.read()
        for gone in ("Claim a <span", "Get a free lead first", "get one free lead first", "Get a free tree lead",
                     "Sounds too good to be true? Sign up free"):
            self.assertNotIn(gone, src, gone)

    def test_login_page_is_login_and_links_to_the_one_signup_form(self):
        h = main.login_page(_mock_request(), next="/checkout/starter")
        self.assertIn('href="/free-account?next=%2Fcheckout%2Fstarter"', h)
        self.assertNotIn("free lead", h.lower())
        self.assertNotIn("signing up for the first time", h)


class TestSignupSubmission(unittest.TestCase):

    def _post(self, fields, *, existing=False, token=TOKEN, host="127.0.0.21"):
        captured, fn = _capture()
        db = main.database
        with patch.object(main, "_check_rate_limit", return_value=True), \
             patch.object(db, "email_has_existing_account", MagicMock(return_value=existing), create=True), \
             patch.object(db, "create_magic_auth_token", MagicMock(return_value=token), create=True) as create, \
             patch.object(_notifications_module(), "send_transactional_email", return_value=True) as mail, \
             patch.object(main, "HTMLResponse", side_effect=fn):
            _run(main.first_time_signup(_FormRequest(fields, host=host)))
        return captured, create, mail

    def test_new_signup_holds_details_on_the_verification_record_only(self):
        fields = dict(GOOD, business_intro="Family-run.", contact_first_name="Dave", offer_text="")
        db = main.database
        saver = MagicMock()
        with patch.object(db, "create_or_update_limbo_account", saver, create=True):
            cap, create, mail = self._post(fields)
        create.assert_called_once()
        args, kwargs = create.call_args
        self.assertEqual(args[0], "dave@apex-trees.co.uk")
        pending = kwargs["pending_signup"]
        self.assertEqual(pending["responsible_contact_name"], "Dave Smith")
        self.assertTrue(pending["terms_accepted_at"])
        self.assertEqual(pending["letter"]["business_name"], "Apex Trees Ltd")
        self.assertEqual(pending["letter"]["phone"], "01234 567890")
        self.assertEqual(pending["letter"]["business_intro"], "Family-run.")
        self.assertEqual(pending["letter"]["contact_first_name"], "Dave")
        self.assertNotIn("email", pending["letter"])
        saver.assert_not_called()          # no limbo / free-lead account is created
        mail.assert_called_once()          # the ordinary verification email

    def test_response_never_contains_token_or_otp(self):
        cap, _, mail = self._post(GOOD)
        for secret in ("SECRET-TOKEN-ABC", "424242", "token="):
            self.assertNotIn(secret, cap["html"])
        self.assertIn("SECRET-TOKEN-ABC", mail.call_args.kwargs["html_body"])

    def test_existing_account_gets_a_plain_login_link_and_no_pending_details(self):
        cap, create, mail = self._post(dict(GOOD, business_name="INTRUDER LTD"), existing=True)
        self.assertIsNone(create.call_args.kwargs["pending_signup"])
        mail.assert_called_once()
        # identical page either way -- the form does not reveal whether an address is registered
        cap_new, _, _ = self._post(GOOD, existing=False)
        self.assertEqual(cap["html"].replace("dave@apex-trees.co.uk", ""), cap_new["html"].replace("dave@apex-trees.co.uk", ""))

    def test_each_required_field_is_enforced_and_nothing_is_created(self):
        for field, msg in (("email", "valid email"), ("responsible_name", "full name"),
                           ("business_name", "business_name"), ("phone", "phone"), ("agree_terms", "Terms")):
            fields = dict(GOOD)
            fields[field] = ""
            cap, create, mail = self._post(fields, host=f"127.0.1.{len(field)}")
            self.assertEqual(cap["status"], 400, field)
            self.assertIn(msg, cap["html"], field)
            create.assert_not_called()
            mail.assert_not_called()
            self.assertIn("Apex Trees Ltd" if field != "business_name" else "Dave Smith", cap["html"])  # values kept

    def test_optional_fields_may_all_be_blank(self):
        cap, create, _ = self._post(GOOD)
        create.assert_called_once()
        letter = create.call_args.kwargs["pending_signup"]["letter"]
        self.assertEqual(letter["business_intro"], "")
        self.assertEqual(letter["offer_text"], "")

    def test_invalid_optional_content_is_rejected_by_the_existing_validation(self):
        cap, create, _ = self._post(dict(GOOD, business_intro="x" * 900))
        self.assertEqual(cap["status"], 400)
        create.assert_not_called()

    def test_checkout_continuation_survives_signup(self):
        cap, create, mail = self._post(dict(GOOD, next="/checkout/starter"))
        body = mail.call_args.kwargs["html_body"]
        self.assertIn("verify-login?token=SECRET-TOKEN-ABC&next=/checkout/starter", body)
        self.assertIn('name="next" value="/checkout/starter"', cap["html"])

    def test_off_site_next_is_dropped(self):
        cap, _, mail = self._post(dict(GOOD, next="https://evil.example/"))
        self.assertNotIn("evil.example", mail.call_args.kwargs["html_body"])
        self.assertNotIn("evil.example", cap["html"])

    def test_rate_limit_stops_the_submission(self):
        captured, fn = _capture()
        with patch.object(main, "_check_rate_limit", return_value=False), \
             patch.object(main.database, "create_magic_auth_token", MagicMock(), create=True) as create, \
             patch.object(main, "HTMLResponse", side_effect=fn):
            _run(main.first_time_signup(_FormRequest(GOOD)))
        create.assert_not_called()
        self.assertEqual(captured["status"], 429)


class TestVerificationAndContinuation(unittest.TestCase):
    """Login/verification routes are unchanged: they still call
    database.verify_magic_auth_token (which now applies pending details in
    the same transaction) and then _login_session_response."""

    def test_verified_link_returns_to_checkout_with_a_session_cookie(self):
        with patch.object(main, "_check_rate_limit", return_value=True), \
             patch.object(main.database, "verify_magic_auth_token", return_value="dave@apex-trees.co.uk"):
            resp = main.verify_login(_mock_request(), token="t", next="/checkout/starter")
        self.assertEqual(resp.headers["location"] if hasattr(resp, "headers") else resp.url, "/checkout/starter")

    def test_expired_or_reused_link_is_rejected(self):
        with patch.object(main, "_check_rate_limit", return_value=True), \
             patch.object(main.database, "verify_magic_auth_token", return_value=None):
            resp = main.verify_login(_mock_request(), token="t", next="/checkout/starter")
        loc = resp.headers["location"] if hasattr(resp, "headers") else resp.url
        self.assertIn("/login?error=", loc)


class TestUnknownEmailViaLogIn(unittest.TestCase):
    """Log In never reveals whether an address is registered: the request
    step does no account lookup at all. Only AFTER the emailed link/code
    proves inbox access does an account without letter details continue to
    the one integrated signup form, keeping `next`."""

    def test_log_in_request_does_no_account_lookup_and_looks_the_same_for_everyone(self):
        pages = []
        for email in ("unknown@example.com", "registered@example.com"):
            captured, fn = _capture()
            req = _FormRequest({"contact": email, "next": "/checkout/starter"}, host="127.0.0.40")
            with patch.object(main, "_check_rate_limit", return_value=True), \
                 patch.object(main.database, "create_magic_auth_token", MagicMock(return_value=dict(TOKEN, email=email))) as create, \
                 patch.object(main.database, "email_has_existing_account", MagicMock(), create=True) as lookup, \
                 patch.object(main.letter_content, "get_contractor_settings", MagicMock()) as settings_lookup, \
                 patch.object(_notifications_module(), "send_transactional_email", return_value=True), \
                 patch.object(main, "HTMLResponse", side_effect=fn):
                _run(main.request_magic_link(req))
            lookup.assert_not_called()
            settings_lookup.assert_not_called()
            self.assertEqual(create.call_args.args, (email,))
            self.assertNotIn("pending_signup", create.call_args.kwargs)
            pages.append(captured["html"].replace(email, "X"))
        self.assertEqual(pages[0], pages[1])

    def test_verified_unknown_email_lands_on_the_integrated_form_with_next(self):
        with patch.object(main, "_check_rate_limit", return_value=True), \
             patch.object(main.database, "verify_magic_auth_token", return_value="unknown@example.com"), \
             patch.object(main.database, "get_db_conn", MagicMock()), \
             patch.object(main.letter_content, "get_contractor_settings", return_value=None):
            resp = main.verify_login(_mock_request(), token="t", next="/checkout/starter")
        self.assertEqual(resp.url, "/free-account?next=%2Fcheckout%2Fstarter")
        self.assertNotIn("letter-onboarding", resp.url)


class TestVerifiedSessionCompletion(unittest.TestCase):

    def _complete(self, fields, *, cookie_email="unknown@example.com", settings_after=None):
        req = _FormRequest(fields)
        req.cookies = {"treekey_contractor_session": main._sign_session_cookie(cookie_email)} if cookie_email else {}
        cur = MagicMock()
        conn = MagicMock()
        conn.cursor.return_value = cur
        captured, fn = _capture()
        with patch.object(main.database, "get_db_conn", MagicMock(return_value=conn)), \
             patch.object(main.letter_content, "insert_initial_contractor_settings", MagicMock(return_value=True)) as ins, \
             patch.object(main.letter_content, "upsert_contractor_settings", MagicMock()) as ups, \
             patch.object(main.letter_content, "get_contractor_settings", return_value=settings_after), \
             patch.object(main, "HTMLResponse", side_effect=fn):
            resp = _run(main.first_time_signup_complete(req))
        return resp, captured, ins, ups, cur, conn

    def test_saves_first_details_under_the_session_email_only_and_continues_to_checkout(self):
        fields = dict(GOOD, email="attacker@example.com", next="/checkout/starter", business_intro="Hi")
        existing = main.letter_content.ContractorLetterSettings(contractor_email="unknown@example.com",
                                                                business_name="Apex Trees Ltd", phone="01234")
        resp, _, ins, ups, cur, conn = self._complete(fields, settings_after=existing)
        ins.assert_called_once()
        settings = ins.call_args.args[1]
        self.assertEqual(settings.contractor_email, "unknown@example.com")     # never the form's email
        self.assertEqual(settings.business_name, "Apex Trees Ltd")
        self.assertEqual(settings.business_intro, "Hi")
        self.assertEqual(ins.call_args.kwargs["responsible_contact_name"], "Dave Smith")
        self.assertTrue(ins.call_args.kwargs["terms_accepted_at"])
        ups.assert_not_called()                                                # insert-only, never an overwrite
        self.assertTrue(any("pg_advisory_xact_lock" in str(c) for c in cur.execute.call_args_list))
        conn.commit.assert_called_once()
        self.assertEqual(resp.url, "/checkout/starter")

    def test_required_fields_and_terms_are_enforced(self):
        for field in ("responsible_name", "business_name", "phone", "agree_terms"):
            resp, cap, ins, _, _, _ = self._complete(dict(GOOD, **{field: ""}))
            self.assertEqual(cap["status"], 400, field)
            ins.assert_not_called()

    def test_no_session_goes_to_login_carrying_the_signup_destination(self):
        resp, _, ins, _, _, _ = self._complete(dict(GOOD, next="/checkout/starter"), cookie_email=None)
        ins.assert_not_called()
        self.assertTrue(resp.url.startswith("/login?next="))
        self.assertIn("free-account", resp.url)


class TestOrdinarySettingsSaveCannotCreateFirstSettings(unittest.TestCase):

    def test_first_ever_save_is_redirected_to_signup_and_writes_nothing(self):
        req = _FormRequest({"business_name": "X", "phone": "1"})
        req.cookies = {"treekey_contractor_session": main._sign_session_cookie("new@example.com")}
        req.query_params = {"next": "/checkout/starter"}
        with patch.object(main.database, "get_db_conn", MagicMock()), \
             patch.object(main.letter_content, "get_contractor_settings", return_value=None), \
             patch.object(main.letter_content, "upsert_contractor_settings", MagicMock()) as ups:
            resp = _run(main.save_letter_settings(req))
        ups.assert_not_called()
        self.assertEqual(resp.url, "/free-account?next=%2Fcheckout%2Fstarter")


class TestRetiredFreeLeadRoutes(unittest.TestCase):
    """/api/free-signup, /api/request-new-code and /api/cold-email-1-test used
    to create free accounts, reserve leads, email codes and grant free
    leads. They now only redirect to the signup form."""

    def test_no_side_effects(self):
        db = main.database
        names = ("create_or_update_limbo_account", "record_free_lead_grant", "burn_lead_inventory",
                 "redeem_free_lead_code", "generate_free_lead_code", "reserve_lead_as_pending",
                 "find_nearest_unclaimed_lead", "record_free_account_terms_acceptance", "get_limbo_account")
        mocks = {n: MagicMock() for n in names}
        req = _FormRequest({"email": "a@b.com", "name": "A", "phone": "1", "postcode": "NG22", "code": "ABC123",
                            "agree_terms": "yes"})
        req.query_params = {}
        with patch.multiple(db, create=True, **mocks), \
             patch.object(_notifications_module(), "send_free_lead_code_email", MagicMock(), create=True) as mail_code, \
             patch.object(_notifications_module(), "send_transactional_email", MagicMock(), create=True) as mail:
            responses = [_run(main.free_signup(req)), _run(main.request_new_code(req)),
                         main.cold_email_1_test(req)]
        for r in responses:
            self.assertEqual(r.status_code, 303)
            self.assertEqual(r.url, "/free-account")
            self.assertFalse(getattr(r, "headers", {}).get("set-cookie"))
        for n, m in mocks.items():
            m.assert_not_called()
        mail.assert_not_called()
        mail_code.assert_not_called()


if __name__ == "__main__":
    unittest.main()

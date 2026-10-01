"""
test_account_close_request.py -- 1 Oct 2026: "Close account / request data
deletion" in My Account.

The feature only RECORDS a request and tells the operator; it must never delete,
cancel or refund anything. Covers: authentication, CSRF, validation, successful
submission, duplicate submission, database failure (never reported as success),
operator-email failure (request retained, still reported as received), the
operator list page, and the My Account link. No real email, database or Stripe.

Run: python -m unittest test_account_close_request
"""
import asyncio
import os
import sys
import time
import unittest
from unittest.mock import MagicMock, patch

_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(_THIS_DIR))
sys.path.insert(0, _THIS_DIR)

import test_access_control  # noqa: E402,F401  (stubs)
import main  # noqa: E402
from test_letter_setup_checkout_gate import _mock_request  # noqa: E402

EMAIL = "dave@apex-trees.co.uk"


def _run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def _body(resp):
    return resp.body


def _post_request(form: dict, cookie=True, email=EMAIL):
    main._SESSION_SECRET = b"test-session-secret"
    req = _mock_request(main._sign_session_cookie(email) if cookie else None, path="/account/close")
    req.client = MagicMock(host="203.0.113.7")

    async def _form():
        return form
    req.form = _form
    return req


def _good_form(**over):
    f = {"csrf": None, "close": "1", "confirm": "1", "note": "please"}
    f.update(over)
    if f["csrf"] is None:
        f["csrf"] = main._account_request_csrf_token(EMAIL)
    return {k: v for k, v in f.items() if v is not False}


class _Base(unittest.TestCase):
    def setUp(self):
        main._SESSION_SECRET = b"test-session-secret"
        main._IP_RATE_LIMITS.clear()
        self.db = MagicMock()
        self.db.ACCOUNT_REQUEST_NOTE_MAX = 1000
        self.db.get_contractor_subscription.return_value = None
        self.db.get_open_account_request.return_value = None
        self.db.create_account_request.return_value = {"ok": True, "id": "abcd1234-0000", "duplicate": False,
                                                       "added": [], "close": True, "delete": False,
                                                       "created_at": "2026-10-01 10:00:00+00"}
        self.notif = MagicMock()
        self.notif.send_transactional_email.return_value = True
        from types import SimpleNamespace as _NS
        self.p3 = patch.object(main, "HTMLResponse",
                               side_effect=lambda content=None, status_code=200, **k: _NS(body=content, status_code=status_code))
        self.p4 = patch.object(main, "RedirectResponse",
                               side_effect=lambda url=None, status_code=307, **k: _NS(url=url, status_code=status_code,
                                                                                    headers={"location": url}))
        self.p3.start(); self.p4.start()
        self.addCleanup(self.p3.stop); self.addCleanup(self.p4.stop)
        self.p1 = patch.object(main, "database", self.db)
        self.p2 = patch.dict(sys.modules, {"notifications": self.notif})
        self.p1.start()
        self.p2.start()
        self.addCleanup(self.p1.stop)
        self.addCleanup(self.p2.stop)

    def submit(self, form, **kw):
        return _run(main.account_close_submit(_post_request(form, **kw)))


class TestAuthAndCsrf(_Base):
    def test_form_requires_sign_in(self):
        r = main.account_close_form(_mock_request(None, path="/account/close"))
        self.assertEqual(r.status_code, 303)
        self.assertIn("/login", r.headers["location"])

    def test_submit_requires_sign_in_and_saves_nothing(self):
        r = self.submit(_good_form(), cookie=False)
        self.assertEqual(r.status_code, 303)
        self.db.create_account_request.assert_not_called()

    def test_missing_wrong_and_expired_csrf_rejected(self):
        other = main._account_request_csrf_token("someone.else@example.com")
        with patch.object(main.time, "time", return_value=time.time() + 3 * 3600):
            expired = main._account_request_csrf_token(EMAIL)
        for token in ("", "garbage", other, expired):
            f = _good_form(csrf=token or "x")
            if not token:
                f.pop("csrf")
            r = self.submit(f)
            self.assertEqual(r.status_code, 403, token)
        self.db.create_account_request.assert_not_called()

    def test_expired_token_is_rejected_even_when_signature_is_valid(self):
        with patch.object(main.time, "time", return_value=time.time() - 3 * 3600):
            old = main._account_request_csrf_token(EMAIL)
        self.assertEqual(self.submit(_good_form(csrf=old)).status_code, 403)

    def test_request_is_tied_to_the_session_account_not_a_form_field(self):
        f = _good_form()
        f["email"] = "victim@example.com"
        self.submit(f)
        self.assertEqual(self.db.create_account_request.call_args[0][0], EMAIL)


class TestForm(_Base):
    def test_form_explains_request_not_deletion_and_has_csrf(self):
        html = _body(main.account_close_form(_mock_request(main._sign_session_cookie(EMAIL), path="/account/close")))
        self.assertIn("submits a request", html)
        self.assertIn("does not delete your records", html)
        self.assertIn('name="csrf"', html)
        self.assertIn('name="close"', html)
        self.assertIn('name="delete"', html)
        self.assertIn("outstanding", html)
        self.assertNotIn("Manage subscription", html)  # no subscription => no subscription link

    def test_subscriber_sees_existing_subscription_link_and_no_cancel_route(self):
        self.db.get_contractor_subscription.return_value = {"active": True}
        html = _body(main.account_close_form(_mock_request(main._sign_session_cookie(EMAIL), path="/account/close")))
        self.assertIn("does not cancel it", html)
        self.assertIn('href="/pricing"', html)
        self.assertNotIn("/cancel", html)

    def test_already_open_request_shows_notice_not_form(self):
        self.db.get_open_account_request.return_value = {"id": "x", "request_close": True, "request_delete": True,
                                                         "created_at": "2026-10-01 10:00:00"}
        html = _body(main.account_close_form(_mock_request(main._sign_session_cookie(EMAIL), path="/account/close")))
        self.assertIn("already have your request", html)
        self.assertNotIn('name="csrf"', html)


class TestSubmission(_Base):
    def test_success_saves_first_then_emails_operator_and_deletes_nothing(self):
        order = []
        self.db.create_account_request.side_effect = lambda *a, **k: (order.append("save") or
            {"ok": True, "id": "abcd1234-0000", "duplicate": False, "added": [], "close": True, "delete": True, "created_at": "x"})
        self.notif.send_transactional_email.side_effect = lambda *a, **k: (order.append("email") or True)
        r = self.submit(_good_form(delete="1"))
        self.assertEqual(r.status_code, 200)
        self.assertEqual(order, ["save", "email"])
        args = self.db.create_account_request.call_args[0]
        self.assertEqual(args, (EMAIL, True, True, "please"))
        self.assertEqual(self.notif.send_transactional_email.call_args[0][0], "nick@treekey.uk")
        self.db.record_account_request_notification.assert_called_once_with("abcd1234-0000", None)
        html = _body(r)
        self.assertIn("Request received", html)
        self.assertIn("Nothing has been deleted", html)
        # no destructive helper was touched
        touched = {c[0] for c in self.db.method_calls}
        # only reads (subscription / nav status) plus the two request helpers; nothing destructive
        self.assertTrue(touched <= {"get_contractor_subscription", "get_limbo_account", "get_open_account_request", "create_account_request",
                                    "record_account_request_notification"}, touched)

    def test_must_choose_an_option_and_confirm(self):
        self.assertEqual(self.submit(_good_form(close=False)).status_code, 400)
        self.assertEqual(self.submit(_good_form(confirm=False)).status_code, 400)
        self.db.create_account_request.assert_not_called()

    def test_duplicate_submission_is_not_a_second_request_or_second_email(self):
        self.db.create_account_request.return_value = {"ok": True, "id": "abcd1234-0000", "duplicate": True,
                                                       "added": [], "close": True, "delete": False, "created_at": "x"}
        r = self.submit(_good_form())
        self.assertEqual(r.status_code, 200)
        self.assertIn("already had your request", _body(r))
        self.notif.send_transactional_email.assert_not_called()

    def test_extra_option_on_open_request_is_kept_and_operator_is_told_again(self):
        self.db.get_open_account_request.return_value = {"id": "abcd1234-0000", "request_close": True,
                                                         "request_delete": False, "created_at": "2026-10-01 10:00:00"}
        self.db.create_account_request.return_value = {"ok": True, "id": "abcd1234-0000", "duplicate": True,
                                                       "added": ["delete"], "close": True, "delete": True, "created_at": "x"}
        r = self.submit(_good_form(close=False, delete="1"))
        self.assertEqual(r.status_code, 200)
        self.assertIn("added your extra option", _body(r))
        self.assertNotIn("already had your request", _body(r))
        self.assertEqual(self.db.create_account_request.call_args[0][:3], (EMAIL, False, True))
        subject, body = self.notif.send_transactional_email.call_args[0][1:3]
        self.assertIn("UPDATED", subject)
        self.assertIn("Close account + Delete personal data", subject)
        self.db.record_account_request_notification.assert_called_once_with("abcd1234-0000", None)

    def test_form_for_close_only_open_request_offers_deletion_and_keeps_close(self):
        self.db.get_open_account_request.return_value = {"id": "x", "request_close": True, "request_delete": False,
                                                         "created_at": "2026-10-01 10:00:00"}
        html = _body(main.account_close_form(_mock_request(main._sign_session_cookie(EMAIL), path="/account/close")))
        self.assertIn('name="csrf"', html)
        self.assertIn("(already requested)", html)
        self.assertIn("You can add the other option", html)
        self.assertEqual(html.count("checked disabled"), 1)

    def test_response_period_stated_accurately(self):
        html = _body(main.account_close_form(_mock_request(main._sign_session_cookie(EMAIL), path="/account/close")))
        self.assertIn("within one month", html)
        self.assertIn("two more months", html)

    def test_database_failure_is_never_reported_as_success_and_sends_no_email(self):
        self.db.create_account_request.return_value = {"ok": False}
        r = self.submit(_good_form())
        self.assertEqual(r.status_code, 503)
        html = _body(r)
        self.assertIn("Nothing has been submitted", html)
        self.assertNotIn("Request received", html)
        self.notif.send_transactional_email.assert_not_called()

    def test_email_failure_keeps_the_request_and_records_the_failure(self):
        self.notif.send_transactional_email.return_value = False
        r = self.submit(_good_form())
        self.assertEqual(r.status_code, 200)
        self.assertIn("Request received", _body(r))
        self.db.record_account_request_notification.assert_called_once_with("abcd1234-0000", "email send failed")

    def test_email_exception_keeps_the_request(self):
        self.notif.send_transactional_email.side_effect = RuntimeError("boom")
        r = self.submit(_good_form())
        self.assertEqual(r.status_code, 200)
        err = self.db.record_account_request_notification.call_args[0][1]
        self.assertIn("RuntimeError", err)

    def test_bookkeeping_failure_after_email_does_not_hide_the_request(self):
        self.db.record_account_request_notification.side_effect = RuntimeError("db down")
        self.assertEqual(self.submit(_good_form()).status_code, 200)

    def test_html_in_note_is_escaped_in_operator_email(self):
        self.submit(_good_form(note="<script>alert(1)</script>"))
        body = self.notif.send_transactional_email.call_args[0][2]
        self.assertNotIn("<script>", body)
        self.assertIn("&lt;script&gt;", body)


class TestOperatorView(_Base):
    ROW = {"id": "r1", "account_email": EMAIL, "request_close": True, "request_delete": True, "note": "n<b>",
           "status": "open", "created_at": "2026-10-01 10:00:00", "operator_notified_at": None,
           "notify_attempts": 1, "notify_error": "email send failed", "resolved_at": None, "resolution_note": None}

    def _basic(self, user="admin", pw="pw123"):
        import base64
        r = _mock_request(None, path="/admin/account-requests")
        r.headers = {"Authorization": "Basic " + base64.b64encode(f"{user}:{pw}".encode()).decode()}
        return r

    def setUp(self):
        super().setUp()
        self.env = patch.dict(os.environ, {"DASHBOARD_USER": "admin", "DASHBOARD_PASS": "pw123"})
        self.env.start()
        self.addCleanup(self.env.stop)

    def _resolve(self, req, form):
        async def _f():
            return form
        req.form = _f
        return _run(main.admin_account_requests_resolve(req))

    def test_list_requires_admin(self):
        from fastapi import HTTPException
        req = _mock_request(None, path="/admin/account-requests")
        req.headers = {}
        with patch.object(main, "T_SEC", "right-secret"):
            with self.assertRaises(HTTPException):
                main.admin_account_requests(req, secret="wrong", status=None)
        with self.assertRaises(HTTPException):
            main.admin_account_requests(self._basic(pw="bad"), secret=None, status=None)

    def test_basic_auth_lists_requests_with_mark_handled_and_no_secret_in_page(self):
        self.db.list_account_requests.return_value = [self.ROW]
        html = _body(main.admin_account_requests(self._basic(), secret=None, status=None))
        self.assertIn(EMAIL, html)
        self.assertIn("NOT SENT", html)
        self.assertIn("Mark handled", html)
        self.assertIn('name="csrf"', html)
        self.assertNotIn('name="secret"', html)
        self.assertNotIn("n<b>", html)

    def test_mark_handled_works_with_basic_auth_and_csrf_without_a_secret(self):
        token = main._admin_csrf_token()
        r = self._resolve(self._basic(), {"csrf": token, "request_id": "r1", "note": "done"})
        self.assertEqual(r.status_code, 303)
        self.assertEqual(r.headers["location"], "/admin/account-requests")
        self.db.resolve_account_request.assert_called_once_with("r1", "done")

    def test_mark_handled_needs_csrf_even_when_authenticated(self):
        from fastapi import HTTPException
        for bad in (None, "", "garbage", "1.deadbeef"):
            form = {"request_id": "r1"}
            if bad is not None:
                form["csrf"] = bad
            with self.assertRaises(HTTPException) as cm:
                self._resolve(self._basic(), form)
            self.assertEqual(cm.exception.status_code, 403)
        with patch.object(main.time, "time", return_value=time.time() - 5 * 3600):
            old = main._admin_csrf_token()
        with self.assertRaises(HTTPException):
            self._resolve(self._basic(), {"csrf": old, "request_id": "r1"})
        self.db.resolve_account_request.assert_not_called()

    def test_mark_handled_needs_authentication(self):
        from fastapi import HTTPException
        req = _mock_request(None, path="/admin/account-requests/resolve")
        req.headers = {}
        with patch.object(main, "T_SEC", "right-secret"):
            with self.assertRaises(HTTPException) as cm:
                self._resolve(req, {"csrf": main._admin_csrf_token(), "request_id": "r1", "secret": "wrong"})
        self.assertEqual(cm.exception.status_code, 401)
        self.db.resolve_account_request.assert_not_called()

    def test_existing_secret_path_still_works_with_csrf(self):
        req = _mock_request(None, path="/admin/account-requests/resolve")
        req.headers = {}
        with patch.object(main, "T_SEC", "right-secret"):
            r = self._resolve(req, {"csrf": main._admin_csrf_token(), "request_id": "r1", "secret": "right-secret"})
        self.assertEqual(r.status_code, 303)
        self.db.resolve_account_request.assert_called_once()


class TestPrivacyPolicy(_Base):
    def test_section_8_mentions_the_form_new_address_and_response_period(self):
        import inspect
        src = inspect.getsource(main)
        i = src.index("8. Your Rights")
        seg = src[i:i + 4500]
        self.assertIn("nick@treekey.uk", seg)
        self.assertIn('href="/account"', seg)
        self.assertIn("request form", seg)
        self.assertIn("within one month", seg)
        self.assertIn("two further months", seg)
        self.assertNotIn("contact@treekey.co.uk", seg[:seg.index("9. Data Retention")])


class TestMyAccountLink(_Base):
    def test_link_is_at_the_bottom_of_my_account(self):
        self.db.get_limbo_account.return_value = None
        self.db.get_contractor_settings.return_value = {}
        self.db.get_payment_history_for_contractor.return_value = []
        self.db.get_db_conn.return_value = MagicMock()
        with patch.object(main.letter_content, "get_contractor_settings", return_value=None):
            html = _body(main.my_account_view(_mock_request(main._sign_session_cookie(EMAIL), path="/account")))
        self.assertIn('href="/account/close"', html)
        self.assertIn("Close account / request data deletion", html)
        self.assertGreater(html.index("/account/close"), html.index("quick-grid"))
        self.assertLess(html.index("/account/close"), html.rindex("</body>"))


if __name__ == "__main__":
    unittest.main()

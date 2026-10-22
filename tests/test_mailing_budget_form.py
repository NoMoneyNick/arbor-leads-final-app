"""
test_mailing_budget_form.py -- the admin "Confirm mailing budget" page.

It only RECORDS funding already in Intelliprint (FundingGate.confirm_budget). Covers: unauthorised
access, CSRF, invalid amounts, exact pounds->pence conversion, duplicate submissions (same reference,
repeat, different amount), database failure, and that recording funding never runs the letter worker.
No real database. (Real-Postgres concurrency: tests/postgres_concurrency/run_budget_reference_real_db_test.py.)

Run: python -m unittest test_mailing_budget_form
"""
import asyncio
import os
import sys
import unittest
from unittest.mock import MagicMock, patch

_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(_THIS_DIR))
sys.path.insert(0, _THIS_DIR)

import test_access_control  # noqa: E402,F401  (stubs)
import main  # noqa: E402
import funding  # noqa: E402
from test_letter_setup_checkout_gate import _mock_request  # noqa: E402
from fastapi import HTTPException  # noqa: E402


def _run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


class TestPoundsToPence(unittest.TestCase):
    def test_exact_conversion(self):
        for text, pence in [("20", 2000), ("20.5", 2050), ("20.50", 2050), ("£20.50", 2050), ("0.07", 7),
                            ("0.1", 10), ("1.08", 108), ("19.99", 1999), ("9999.99", 999999),
                            ("10000", 1000000), (" 12 ", 1200), ("0.29", 29), ("1.15", 115)]:
            self.assertEqual(funding.parse_pounds_to_pence(text), pence, text)

    def test_rejects_invalid(self):
        for bad in ["", "   ", "0", "0.00", "-5", "+5", "1,000", "1.234", "1.", ".5", "abc", "12abc", "1e3",
                    "10000.01", "99999999", "NaN", "inf", "٣", "20 pounds", None]:
            with self.assertRaises(ValueError, msg=repr(bad)):
                funding.parse_pounds_to_pence(bad)

    def test_reference_rules(self):
        self.assertEqual(funding.normalise_funding_reference("  INTELLI   1234 "), "INTELLI 1234")
        for bad in ["", "  ", "ab", "x" * 65, "a;b", "<script>", None]:
            with self.assertRaises(ValueError, msg=repr(bad)):
                funding.normalise_funding_reference(bad)

    def test_format_pence(self):
        self.assertEqual(funding.format_pence(108), "£1.08")
        self.assertEqual(funding.format_pence(-108), "-£1.08")
        self.assertEqual(funding.format_pence(123456), "£1,234.56")


class _Cur:
    def __init__(self, rows=None):
        self.executed, self.rows = [], list(rows or [])

    def execute(self, sql, params=None):
        self.executed.append((" ".join(sql.split()), params))

    def fetchone(self):
        return self.rows.pop(0) if self.rows else None


class TestConfirmBudgetReference(unittest.TestCase):
    def test_without_reference_sql_is_unchanged(self):
        cur = _Cur([("abc",)])
        funding.FundingGate(mode="hold").confirm_budget(cur, amount_pence=108, confirmed_by="x", note="n")
        self.assertNotIn("funding_reference", cur.executed[0][0])

    def test_with_reference_inserts_once_and_duplicate_raises(self):
        cur = _Cur([("id1",)])
        funding.FundingGate(mode="hold").confirm_budget(cur, amount_pence=2000, confirmed_by="x", note="n",
                                                         funding_reference="REF-1")
        self.assertIn("ON CONFLICT DO NOTHING", cur.executed[0][0])
        self.assertEqual(cur.executed[0][1], (2000, "x", "n", "REF-1"))
        dup = _Cur([None, ("id1", 2000, 0, 0, True, "x", None)])      # insert yields no row -> lookup finds the first
        with self.assertRaises(funding.DuplicateFundingReference) as ctx:
            funding.FundingGate(mode="hold").confirm_budget(dup, amount_pence=2000, confirmed_by="x", note="n",
                                                             funding_reference="ref-1")
        self.assertEqual(ctx.exception.existing["amount_pence"], 2000)

    def test_summary_is_available_minus_reserved(self):
        cur = _Cur([(5000, 1000, 108)])
        s = funding.FundingGate(mode="hold").budget_summary(cur)
        self.assertEqual(s, {"confirmed_pence": 5000, "spent_pence": 1000, "reserved_pence": 108,
                             "available_pence": 3892})


class _Resp:
    """Stands in for HTMLResponse / RedirectResponse (this test environment stubs the real ones)."""
    def __init__(self, content="", status_code=200, url=None, **kw):
        self.body = content.encode() if isinstance(content, str) else b""
        self.status_code = status_code
        self.headers = {"location": url} if url else {}


_ZERO = {"confirmed_pence": 0, "spent_pence": 0, "reserved_pence": 0, "available_pence": 0}


class _FormBase(unittest.TestCase):
    def setUp(self):
        for name in ("HTMLResponse", "RedirectResponse"):
            p = patch.object(main, name, _Resp)
            p.start()
            self.addCleanup(p.stop)
        # the summary table is read from the database; keep it out of the way (the tests stub the connection)
        for meth, val in (("budget_summary", _ZERO), ("recent_budget_records", [])):
            p = patch.object(main.funding_module.FundingGate, meth, return_value=val)
            p.start()
            self.addCleanup(p.stop)
        main._SESSION_SECRET = b"test-session-secret"
        env = patch.dict(os.environ, {"DASHBOARD_USER": "admin", "DASHBOARD_PASS": "pw123"})
        env.start()
        self.addCleanup(env.stop)
        sec = patch.object(main, "T_SEC", "right-secret")
        sec.start()
        self.addCleanup(sec.stop)

    def req(self, form=None):
        r = _mock_request(None, path="/admin/mailing-budget")
        r.headers = {}
        if form is not None:
            async def _f():
                return form
            r.form = _f
        return r

    def form(self, **kw):
        d = {"secret": "right-secret", "csrf": main._admin_csrf_token(main._BUDGET_CSRF),
             "amount": "20.50", "reference": "TOPUP-001"}
        d.update(kw)
        return d

    def conn(self):
        conn, cur = MagicMock(), MagicMock()
        conn.cursor.return_value = cur
        return conn, cur


class TestAccess(_FormBase):
    def test_page_and_posts_reject_unauthorised(self):
        with self.assertRaises(HTTPException) as c:
            main.admin_mailing_budget(self.req(), secret="wrong", done=None)
        self.assertEqual(c.exception.status_code, 401)
        for fn in (main.admin_mailing_budget_review, main.admin_mailing_budget_confirm):
            with self.assertRaises(HTTPException) as c:
                _run(fn(self.req(self.form(secret="wrong"))))
            self.assertEqual(c.exception.status_code, 401)

    def test_bad_or_other_form_csrf_is_rejected_before_any_database_use(self):
        with patch.object(main.database, "get_db_conn") as g:
            for fn in (main.admin_mailing_budget_review, main.admin_mailing_budget_confirm):
                for token in ("", "1.abc", main._admin_csrf_token()):     # last one belongs to a different form
                    with self.assertRaises(HTTPException) as c:
                        _run(fn(self.req(self.form(csrf=token))))
                    self.assertEqual(c.exception.status_code, 403)
            g.assert_not_called()


class TestValidation(_FormBase):
    def test_invalid_amounts_and_references_record_nothing(self):
        with patch.object(main.database, "get_db_conn") as g, patch.object(main.funding_module.FundingGate, "confirm_budget") as cb:
            g.return_value = self.conn()[0]
            for amount in ("", "0", "-1", "1.005", "abc", "1,000", "10000.01"):
                resp = _run(main.admin_mailing_budget_confirm(self.req(self.form(amount=amount))))
                self.assertEqual(resp.status_code, 400, amount)
            resp = _run(main.admin_mailing_budget_confirm(self.req(self.form(reference=""))))
            self.assertEqual(resp.status_code, 400)
            cb.assert_not_called()


class TestRecording(_FormBase):
    def test_review_shows_exact_pence_and_changes_nothing(self):
        conn, cur = self.conn()
        with patch.object(main.database, "get_db_conn", return_value=conn), \
             patch.object(main.funding_module.FundingGate, "get_budget_by_reference", return_value=None), \
             patch.object(main.funding_module.FundingGate, "budget_summary",
                          return_value={"confirmed_pence": 0, "spent_pence": 0, "reserved_pence": 0, "available_pence": 0}), \
             patch.object(main.funding_module.FundingGate, "confirm_budget") as cb:
            resp = _run(main.admin_mailing_budget_review(self.req(self.form(amount="1.15"))))
        text = resp.body.decode()
        self.assertEqual(resp.status_code, 200)
        self.assertIn("115 pence", text)
        self.assertIn("does not top up Intelliprint", text)
        cb.assert_not_called()
        conn.commit.assert_not_called()

    def test_confirm_records_exact_pence_once_and_commits(self):
        conn, cur = self.conn()
        with patch.object(main.database, "get_db_conn", return_value=conn), \
             patch.object(main.funding_module.FundingGate, "confirm_budget", return_value="id1") as cb:
            resp = _run(main.admin_mailing_budget_confirm(self.req(self.form(amount="20.50", reference="  TOPUP   001 "))))
        self.assertEqual(resp.status_code, 303)
        self.assertIn("done=TOPUP%20001", resp.headers["location"])
        cb.assert_called_once()
        kw = cb.call_args.kwargs
        self.assertEqual((kw["amount_pence"], kw["funding_reference"]), (2050, "TOPUP 001"))
        conn.commit.assert_called_once()
        conn.rollback.assert_not_called()

    def test_duplicate_reference_adds_nothing(self):
        for amount, expected in (("20.50", "Nothing was added again"), ("30", "was already used")):
            conn, cur = self.conn()
            dup = main.funding_module.DuplicateFundingReference("TOPUP-001", {"amount_pence": 2050})
            with patch.object(main.database, "get_db_conn", return_value=conn), \
                 patch.object(main.funding_module.FundingGate, "confirm_budget", side_effect=dup), \
                 patch.object(main.funding_module.FundingGate, "budget_summary",
                              return_value={"confirmed_pence": 2050, "spent_pence": 0, "reserved_pence": 0, "available_pence": 2050}), \
                 patch.object(main.funding_module.FundingGate, "recent_budget_records", return_value=[]):
                resp = _run(main.admin_mailing_budget_confirm(self.req(self.form(amount=amount))))
            self.assertEqual(resp.status_code, 409)
            self.assertIn(expected, resp.body.decode())
            conn.rollback.assert_called_once()
            conn.commit.assert_not_called()

    def test_review_refuses_a_reference_already_used(self):
        conn, cur = self.conn()
        with patch.object(main.database, "get_db_conn", return_value=conn), \
             patch.object(main.funding_module.FundingGate, "get_budget_by_reference", return_value={"amount_pence": 2050}), \
             patch.object(main.funding_module.FundingGate, "budget_summary",
                          return_value={"confirmed_pence": 0, "spent_pence": 0, "reserved_pence": 0, "available_pence": 0}), \
             patch.object(main.funding_module.FundingGate, "recent_budget_records", return_value=[]):
            resp = _run(main.admin_mailing_budget_review(self.req(self.form())))
        self.assertEqual(resp.status_code, 409)

    def test_database_failure_is_reported_as_not_recorded(self):
        conn, cur = self.conn()
        with patch.object(main.database, "get_db_conn", return_value=conn), \
             patch.object(main.funding_module.FundingGate, "confirm_budget", side_effect=RuntimeError("db down")), \
             patch.object(main.funding_module.FundingGate, "budget_summary",
                          return_value={"confirmed_pence": 0, "spent_pence": 0, "reserved_pence": 0, "available_pence": 0}), \
             patch.object(main.funding_module.FundingGate, "recent_budget_records", return_value=[]):
            resp = _run(main.admin_mailing_budget_confirm(self.req(self.form())))
        self.assertEqual(resp.status_code, 500)
        self.assertIn("Not recorded", resp.body.decode())
        conn.rollback.assert_called_once()
        conn.commit.assert_not_called()

    def test_recording_never_runs_the_letter_worker(self):
        conn, cur = self.conn()
        with patch.object(main.database, "get_db_conn", return_value=conn), \
             patch.object(main.fulfilment_worker, "run_one_pass") as rp, \
             patch.object(main.funding_module.FundingGate, "confirm_budget", return_value="id1"):
            _run(main.admin_mailing_budget_confirm(self.req(self.form())))
        rp.assert_not_called()


if __name__ == "__main__":
    unittest.main()

"""test_login_link_limits.py -- persistent, race-safe, fail-closed limits on emailed login links (13 Oct 2026).

Mocks only: no real database, no email, no network. The real-Postgres concurrency proof is concurrency_test.py
(delivered with the patch). Run with:  python -m unittest tests.test_login_link_limits -v
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

import login_link_limits as lll  # pure python, always importable

try:
    import test_access_control  # noqa: F401  (stubs + imports main)
    import main
    from test_letter_onboarding import _mock_request  # noqa: F401
    _IMPORT_ERROR = None
except Exception as _e:
    main, _IMPORT_ERROR = None, _e


def _load_real_database():
    """The shared test setup replaces `database` with a stub, so load the REAL database.py from its file under another
    name, with its third-party imports (psycopg2, requests, dotenv, fulfilment) replaced by inert stand-ins just for the
    load. Nothing here connects to anything or reads any .env file."""
    import importlib.util
    path = os.path.join(_APP_DIR, "database.py")
    stand_ins = {name: MagicMock() for name in ("psycopg2", "psycopg2.extras", "psycopg2.pool", "psycopg2.errors",
                                                 "requests", "dotenv", "fulfilment")}
    try:
        with patch.dict(sys.modules, stand_ins):
            spec = importlib.util.spec_from_file_location("database_real_for_limits_tests", path)
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
        return mod, None
    except Exception as e:  # noqa: BLE001
        return None, e


database, _DB_ERROR = _load_real_database()


def _run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


class TestRules(unittest.TestCase):
    def test_defaults_are_conservative_and_site_cap_is_60_a_day(self):
        L = lll.get_limits({})
        self.assertEqual((L["email_15m"], L["email_day"], L["ip_hour"], L["ip_day"], L["all_hour"], L["all_day"]),
                         (3, 8, 10, 30, 20, 60))

    def test_env_can_change_a_limit_but_bad_values_fall_back_to_the_default(self):
        self.assertEqual(lll.get_limits({"LOGIN_LINK_ALL_PER_DAY": "25"})["all_day"], 25)
        for bad in ("abc", "", "-5", "1.5"):
            self.assertEqual(lll.get_limits({"LOGIN_LINK_ALL_PER_DAY": bad})["all_day"], 60, bad)

    def test_nothing_raises_the_cap_by_itself(self):
        # the same defaults apply whatever else is in the environment (no "launch mode" switch)
        self.assertEqual(lll.get_limits({"CAMPAIGN_LAUNCHED": "true", "LAUNCH": "1"}), lll.get_limits({}))

    def test_decide_names_the_first_full_limit_and_none_when_there_is_room(self):
        L = lll.get_limits({})
        self.assertIsNone(lll.decide({}, L))
        self.assertEqual(lll.decide({"email_15m": 3}, L), "email_15m")
        self.assertEqual(lll.decide({"all_day": 60}, L), "all_day")
        self.assertEqual(lll.decide({"all_day": 59, "ip_hour": 9}, L), None)

    def test_throttle_kinds(self):
        self.assertEqual(lll.throttle_kind("email_day"), "email")
        for k in ("ip_hour", "ip_day", "all_hour", "all_day"):
            self.assertEqual(lll.throttle_kind(k), "busy")
        self.assertIsNone(lll.throttle_kind(None))

    def test_ip_is_hashed_never_stored_raw(self):
        h = lll.hash_ip("203.0.113.9", {})
        self.assertNotIn("203.0.113", h)
        self.assertEqual(h, lll.hash_ip("203.0.113.9", {}))
        self.assertNotEqual(h, lll.hash_ip("203.0.113.10", {}))
        self.assertIsNone(lll.hash_ip("unknown", {}))
        self.assertIsNone(lll.hash_ip("", {}))


class TestSqlShape(unittest.TestCase):
    def test_check_and_insert_are_one_statement(self):
        sql = lll.INSERT_IF_ALLOWED_SQL
        self.assertEqual(sql.upper().count("INSERT INTO"), 1)
        self.assertIn("SELECT %(email)s", sql)          # INSERT ... SELECT ... WHERE counts < limits
        for k in ("email_15m", "email_day", "ip_hour", "ip_day", "all_hour", "all_day"):
            self.assertIn(f"%(l_{k})s", sql, k)
        self.assertIn("RETURNING token, otp_code, expires_at", sql)

    def test_lock_is_a_transaction_scoped_advisory_lock(self):
        self.assertIn("pg_advisory_xact_lock", lll.LOCK_SQL)

    def test_the_explain_query_never_inserts(self):
        self.assertNotIn("INSERT", lll.COUNT_SQL.upper())

    def test_every_named_parameter_is_supplied(self):
        p = lll.insert_params("a@b.com", "t", "1", None, "h", lll.get_limits({}))
        for name in set(re.findall(r"%\((\w+)\)s", lll.INSERT_IF_ALLOWED_SQL)):
            self.assertIn(name, p, name)


class _FakeCursor:
    def __init__(self, script):
        self.executed, self.script = [], script          # script(sql, params) -> row | raises
        self._row = None
    def execute(self, sql, params=None):
        self.executed.append((sql, params))
        self._row = self.script(sql, params)
    def fetchone(self):
        return self._row
    def close(self):
        pass


class _FakeConn:
    def __init__(self, script):
        self.cur, self.commits, self.rollbacks, self.closed = _FakeCursor(script), 0, 0, False
    def cursor(self):
        return self.cur
    def commit(self):
        self.commits += 1
    def rollback(self):
        self.rollbacks += 1
    def close(self):
        self.closed = True


@unittest.skipIf(database is None, f"database.py cannot be loaded here: {_DB_ERROR!r}")
class TestFailClosed(unittest.TestCase):
    def _call(self, script, **kw):
        conn = _FakeConn(script)
        with patch.object(database, "SURL", "postgres://x", create=True), patch.object(database, "get_db_conn", return_value=conn):
            return database.create_magic_auth_token("Someone@Example.com", requester_ip="203.0.113.9", enforce_limits=True, **kw), conn

    @staticmethod
    def _inserts(conn):
        return [s for s, _ in conn.cur.executed if "INSERT INTO contractor_auth_tokens" in s]

    def test_allowed_request_takes_the_lock_first_then_one_insert(self):
        def script(sql, params):
            return ("TOK", "123456", None) if "INSERT INTO contractor_auth_tokens" in sql else None
        res, conn = self._call(script)
        self.assertEqual((res["token"], res["otp"], res["email"]), ("TOK", "123456", "someone@example.com"))
        stmts = [s for s, _ in conn.cur.executed]
        self.assertLess(next(i for i, s in enumerate(stmts) if "pg_advisory_xact_lock" in s),
                        next(i for i, s in enumerate(stmts) if "INSERT INTO contractor_auth_tokens" in s))
        self.assertEqual(len(self._inserts(conn)), 1)
        self.assertEqual(conn.commits, 1)

    def test_full_limit_creates_no_record_and_names_the_limit(self):
        def script(sql, params):
            if "INSERT INTO contractor_auth_tokens" in sql:
                return None
            if "COUNT(*)" in sql:
                return (3, 3, 0, 0, 3, 3)               # email_15m is full
            return None
        res, conn = self._call(script)
        self.assertEqual(res["throttled"], "email_15m")
        self.assertNotIn("token", res)
        self.assertNotIn("otp", res)

    def test_missing_column_fails_closed_with_no_fallback_insert(self):
        def script(sql, params):
            if "INSERT INTO contractor_auth_tokens" in sql:
                raise Exception('column "requester_ip_hash" does not exist')
            return None
        res, conn = self._call(script)
        self.assertEqual(res.get("unavailable"), "limiter_error")
        self.assertNotIn("token", res)
        self.assertEqual(len(self._inserts(conn)), 1, "must not retry with an unprotected insert")
        self.assertGreaterEqual(conn.rollbacks, 1)
        self.assertEqual(conn.commits, 0)

    def test_lock_failure_fails_closed(self):
        def script(sql, params):
            if "pg_advisory_xact_lock" in sql:
                raise Exception("could not obtain lock")
            return None
        res, conn = self._call(script)
        self.assertEqual(res.get("unavailable"), "limiter_error")
        self.assertEqual(self._inserts(conn), [])

    def test_failure_while_explaining_a_throttle_also_fails_closed(self):
        def script(sql, params):
            if "INSERT INTO contractor_auth_tokens" in sql:
                return None
            if "COUNT(*)" in sql:
                raise Exception("boom")
            return None
        res, conn = self._call(script)
        self.assertEqual(res.get("unavailable"), "limiter_error")
        self.assertNotIn("token", res)

    def test_the_raw_visitor_address_is_never_sent_to_the_database(self):
        def script(sql, params):
            return ("T", "1", None) if "INSERT INTO contractor_auth_tokens" in sql else None
        _, conn = self._call(script)
        for _, params in conn.cur.executed:
            self.assertNotIn("203.0.113.9", repr(params))


def _req(fields, host="203.0.113.9"):
    r = MagicMock()
    r.client = MagicMock(host=host)
    r.cookies, r.headers = {}, {}
    async def form():
        return dict(fields)
    r.form = form
    return r


def _capture():
    cap = {}
    def fn(content=None, *a, **k):
        cap.update(html=content, status=k.get("status_code", 200))
        return cap
    return cap, fn


def _capture_redirect(cap):
    def rfn(url=None, status_code=307, *a, **k):
        cap.update(redirect_url=url, redirect_status=status_code)
        return cap
    return rfn


def _mail():
    mod = sys.modules.get("notifications")
    if mod is not None and not hasattr(mod, "send_transactional_email"):
        mod.send_transactional_email = MagicMock(return_value=True)
    return mod


@unittest.skipIf(main is None, f"main.py cannot be imported here: {_IMPORT_ERROR!r}")
class TestRoutes(unittest.TestCase):
    def _login(self, auth_data):
        cap, fn = _capture()
        with patch.dict(os.environ, {"AUTH_EMAILS_PAUSED": ""}), \
             patch.object(main, "_check_rate_limit", return_value=True), \
             patch.object(main.database, "create_magic_auth_token", MagicMock(return_value=auth_data)) as create, \
             patch.object(_mail(), "send_transactional_email", return_value=True) as mail, \
             patch.object(main, "HTMLResponse", side_effect=fn), \
             patch.object(main, "RedirectResponse", side_effect=_capture_redirect(cap)):
            result = _run(main.request_magic_link(_req({"contact": "someone@example.com"})))
        return result, cap, create, mail

    def test_limiter_failure_is_a_503_and_nothing_is_sent(self):
        _, cap, create, mail = self._login({"unavailable": "limiter_error", "email": "x"})
        self.assertEqual(cap["status"], 503)
        self.assertIn("temporarily unavailable", cap["html"].lower())
        mail.assert_not_called()

    def test_routes_pass_the_visitor_address_and_turn_limits_on(self):
        _, _, create, _ = self._login({"token": "DUMMY-TEST-VALUE-NOT-REAL-0001", "otp": "1", "email": "x"})
        kw = create.call_args.kwargs
        self.assertTrue(kw["enforce_limits"])
        self.assertEqual(kw["requester_ip"], "203.0.113.9")

    def test_per_address_throttle_sends_nothing_but_looks_like_a_normal_send(self):
        _, ok_cap, _, ok_mail = self._login({"token": "DUMMY-TEST-VALUE-NOT-REAL-0001", "otp": "123456", "email": "x"})
        _, th_cap, _, th_mail = self._login({"throttled": "email_15m", "email": "x"})
        ok_mail.assert_called_once()
        th_mail.assert_not_called()
        self.assertEqual(th_cap.get("status", 200), 200)
        self.assertIn("inbox", th_cap["html"].lower())
        self.assertNotIn("token=", th_cap["html"])

    def test_busy_throttle_sends_nothing_and_says_try_again(self):
        for reason in ("all_day", "all_hour", "ip_hour", "ip_day"):
            result, cap, _, mail = self._login({"throttled": reason, "email": "x"})
            mail.assert_not_called()
            self.assertEqual(cap.get("redirect_status"), 303, reason)
            self.assertIn("lot+of+requests", cap.get("redirect_url", ""), reason)

    def test_signup_limiter_failure_and_busy_send_nothing(self):
        fields = {"email": "Dave@Apex-Trees.co.uk", "responsible_name": "Dave Smith", "business_name": "Apex Trees Ltd",
                  "phone": "01234 567890", "agree_terms": "yes"}
        for auth in ({"unavailable": "limiter_error", "email": "x"}, {"throttled": "all_day", "email": "x"}):
            cap, fn = _capture()
            with patch.dict(os.environ, {"AUTH_EMAILS_PAUSED": ""}), \
                 patch.object(main, "_check_rate_limit", return_value=True), \
                 patch.object(main.database, "email_has_existing_account", MagicMock(return_value=False), create=True), \
                 patch.object(main.database, "create_magic_auth_token", MagicMock(return_value=auth)), \
                 patch.object(_mail(), "send_transactional_email", return_value=True) as mail, \
                 patch.object(main, "HTMLResponse", side_effect=fn):
                _run(main.first_time_signup(_req(fields)))
            mail.assert_not_called()


class TestSource(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with open(os.path.join(_APP_DIR, "main.py"), encoding="utf-8") as f:
            cls.main_src = f.read()
        with open(os.path.join(_APP_DIR, "database.py"), encoding="utf-8") as f:
            cls.db_src = f.read()

    def test_both_routes_turn_limits_on(self):
        calls = re.findall(r"database\.create_magic_auth_token\([^\n]*\)", self.main_src)
        self.assertEqual(len(calls), 2)
        for c in calls:
            self.assertIn("enforce_limits=True", c)
            self.assertIn("requester_ip=client_ip", c)

    def test_pause_switch_is_still_present(self):
        self.assertIn("def _auth_emails_paused", self.main_src)
        self.assertIn("if _auth_emails_paused():", self.main_src)

    def test_limiter_failure_has_no_unprotected_fallback(self):
        i = self.db_src.index("if enforce_limits:")
        j = self.db_src.index("INSERT INTO contractor_auth_tokens (customer_email, token, otp_code, pending_signup)\n                VALUES", i)
        block = self.db_src[i:j]
        self.assertIn('return {"unavailable"', block)
        self.assertEqual(block.count("INSERT INTO"), 0)

    def test_migration_adds_the_column_in_both_places(self):
        with open(os.path.join(_APP_DIR, "migrations", "0006_login_link_limits.sql"), encoding="utf-8") as f:
            sql = f.read()
        self.assertIn("ADD COLUMN IF NOT EXISTS requester_ip_hash", sql)
        self.assertIn("ADD COLUMN IF NOT EXISTS requester_ip_hash", self.db_src)

    def test_the_visitor_address_is_read_only_from_the_connection(self):
        # no code in main.py parses X-Forwarded-For / X-Real-IP itself
        self.assertNotRegex(self.main_src, r"(?i)x-forwarded-for|x-real-ip")


if __name__ == "__main__":
    unittest.main()

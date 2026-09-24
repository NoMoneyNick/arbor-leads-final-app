"""
test_worker_trigger_routes.py -- 2026-09-18 review, Section 6 (second pass):
tests for the two ACTUAL invocation surfaces wired up for
worker.run_one_pass -- main.py's /trigger-letter-fulfilment-worker (the
external-cron-facing entry point) and /admin/run-letter-fulfilment-worker
(the admin-page manual-run button). Guarding worker.py's functions is not
enough if nothing invokes them (the brief's own words) -- these tests prove
the invocation SURFACES themselves are wired correctly: auth, the live=
flag's mapping to is_dry_run, the JSON/redirect shapes, and that a mid-pass
exception rolls back and reports an error rather than silently committing a
partial pass or crashing the route.

Reuses test_access_control.py's stubbing (which itself reuses test_main.py's)
by importing it for its side effects before doing anything else -- same
convention test_access_control.py itself documents and follows.

Run with:
    python -m unittest tests.test_worker_trigger_routes -v
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
import worker  # noqa: E402  (real module -- WorkerPassReport/PromotionReport, no stubbing needed)
from letter_providers.registry import SendOutcome  # noqa: E402


def _mock_request(auth_header=None):
    req = MagicMock()
    req.headers = {"authorization": auth_header} if auth_header else {}
    return req


class _PositionalAwareRedirectResponse:
    """test_access_control.py's shared fake RedirectResponse only assigns
    `.url` from a URL= keyword argument (`lambda self, *a, url=None, ...`),
    because every OTHER call site in main.py happens to pass url= as a
    keyword. This file's two routes pass the URL positionally instead
    (RedirectResponse(f"...", status_code=302)) -- perfectly valid against
    the real starlette.RedirectResponse(url, status_code=...) signature,
    just not something the shared test stub anticipated. Patched in
    locally, only for the tests that need to inspect `.url`, rather than
    editing the shared stub (which other test files also depend on) or
    main.py itself (whose two routes intentionally mirror the exact
    pre-existing style of /admin/process-letter-dispatches just above
    them)."""
    def __init__(self, url=None, status_code=200, **kwargs):
        self.url = url
        self.status_code = status_code


class _TriggerRouteTestBase(unittest.TestCase):
    def setUp(self):
        self._old_t_sec = main.T_SEC
        main.T_SEC = "test-trigger-secret"

    def tearDown(self):
        main.T_SEC = self._old_t_sec


class TestTriggerLetterFulfilmentWorkerRoute(_TriggerRouteTestBase):
    """/trigger-letter-fulfilment-worker -- query-param-secret-only, same
    convention as every other /trigger-* route in this file (an external
    cron service can only ever supply a URL)."""

    def test_missing_secret_is_rejected(self):
        with self.assertRaises(main.HTTPException) as ctx:
            main.trigger_letter_fulfilment_worker(secret=None)
        self.assertEqual(ctx.exception.status_code, 401)

    def test_wrong_secret_is_rejected(self):
        with self.assertRaises(main.HTTPException) as ctx:
            main.trigger_letter_fulfilment_worker(secret="not-the-secret")
        self.assertEqual(ctx.exception.status_code, 401)

    @patch("main.fulfilment_worker.run_one_pass")
    @patch("main.database.get_db_conn")
    def test_correct_secret_defaults_to_dry_run(self, mock_get_db_conn, mock_run_one_pass):
        mock_conn = MagicMock()
        mock_get_db_conn.return_value = mock_conn
        mock_run_one_pass.return_value = worker.WorkerPassReport(
            pipeline_active=True, is_dry_run=True,
            approvals=worker.PromotionReport(checked=1, promoted_to_pending_funding=1),
            funding=worker.PromotionReport(checked=1, promoted_to_ready=1),
            send_outcomes=[SendOutcome(obligation_id="ob-1", final_status="dry_run", attempts_made=0)],
        )
        result = main.trigger_letter_fulfilment_worker(secret="test-trigger-secret")

        # `live` defaults to False -> is_dry_run must be True: this route
        # must never silently default to a real send.
        _, kwargs = mock_run_one_pass.call_args
        self.assertTrue(kwargs.get("is_dry_run"))
        mock_conn.commit.assert_called_once()

        self.assertEqual(result["status"], "ok")
        self.assertTrue(result["is_dry_run"])
        self.assertEqual(result["send_attempts"], 1)
        self.assertEqual(result["send_outcomes"], [{"obligation_id": "ob-1", "final_status": "dry_run", "attempts_made": 0}])
        self.assertEqual(result["approvals"]["promoted_to_pending_funding"], 1)
        self.assertEqual(result["funding"]["promoted_to_ready"], 1)

    @patch("main.fulfilment_worker.run_one_pass")
    @patch("main.database.get_db_conn")
    def test_live_query_param_maps_to_is_dry_run_false(self, mock_get_db_conn, mock_run_one_pass):
        """A flag alone must not be interpreted the wrong way round --
        ?live=true means is_dry_run=False must actually be passed through,
        not silently ignored."""
        mock_get_db_conn.return_value = MagicMock()
        mock_run_one_pass.return_value = worker.WorkerPassReport(pipeline_active=True, is_dry_run=False)
        result = main.trigger_letter_fulfilment_worker(secret="test-trigger-secret", live=True)
        _, kwargs = mock_run_one_pass.call_args
        self.assertFalse(kwargs.get("is_dry_run"))
        self.assertFalse(result["is_dry_run"])

    @patch("main.fulfilment_worker.run_one_pass")
    @patch("main.database.get_db_conn")
    def test_reports_noop_when_pipeline_not_active(self, mock_get_db_conn, mock_run_one_pass):
        mock_get_db_conn.return_value = MagicMock()
        mock_run_one_pass.return_value = worker.WorkerPassReport(pipeline_active=False)
        result = main.trigger_letter_fulfilment_worker(secret="test-trigger-secret")
        self.assertEqual(result["status"], "noop")

    @patch("main.fulfilment_worker.run_one_pass")
    @patch("main.database.get_db_conn")
    def test_exception_rolls_back_and_returns_error_not_a_crash(self, mock_get_db_conn, mock_run_one_pass):
        """A mid-pass exception (e.g. a DB outage partway through) must roll
        back rather than commit a half-finished pass, and the route itself
        must not propagate the exception (an external cron hitting this
        expects a JSON response, not a 500 with no explanation)."""
        mock_conn = MagicMock()
        mock_get_db_conn.return_value = mock_conn
        mock_run_one_pass.side_effect = RuntimeError("simulated DB outage mid-pass")

        result = main.trigger_letter_fulfilment_worker(secret="test-trigger-secret")

        mock_conn.rollback.assert_called_once()
        mock_conn.commit.assert_not_called()
        self.assertEqual(result["status"], "error")
        self.assertIn("simulated DB outage mid-pass", result["error"])

    @patch("main.fulfilment_worker.run_one_pass")
    @patch("main.database.get_db_conn")
    def test_cursor_and_connection_are_always_closed(self, mock_get_db_conn, mock_run_one_pass):
        """Both the happy path and the exception path must release the
        connection -- checked separately since a `finally` bug could pass
        one and silently leak on the other."""
        mock_conn = MagicMock()
        mock_cur = MagicMock()
        mock_conn.cursor.return_value = mock_cur
        mock_get_db_conn.return_value = mock_conn
        mock_run_one_pass.return_value = worker.WorkerPassReport(pipeline_active=True)
        main.trigger_letter_fulfilment_worker(secret="test-trigger-secret")
        mock_cur.close.assert_called_once()
        mock_conn.close.assert_called_once()

        mock_conn.reset_mock()
        mock_cur.reset_mock()
        mock_run_one_pass.side_effect = RuntimeError("boom")
        main.trigger_letter_fulfilment_worker(secret="test-trigger-secret")
        mock_cur.close.assert_called_once()
        mock_conn.close.assert_called_once()


class TestAdminRunLetterFulfilmentWorkerRoute(_TriggerRouteTestBase):
    """/admin/run-letter-fulfilment-worker -- the admin-page button, gated
    by verify_admin_or_secret (Basic Auth OR ?secret=), always dry-run."""

    def test_no_auth_at_all_is_rejected(self):
        request = _mock_request(auth_header=None)
        with self.assertRaises(main.HTTPException) as ctx:
            main.admin_run_letter_fulfilment_worker(request, secret=None)
        self.assertEqual(ctx.exception.status_code, 401)

    @patch("main.RedirectResponse", _PositionalAwareRedirectResponse)
    @patch("main.fulfilment_worker.run_one_pass")
    @patch("main.database.get_db_conn")
    def test_correct_secret_runs_dry_run_and_redirects_to_dashboard(self, mock_get_db_conn, mock_run_one_pass):
        mock_get_db_conn.return_value = MagicMock()
        mock_run_one_pass.return_value = worker.WorkerPassReport(pipeline_active=True, is_dry_run=True)
        request = _mock_request(auth_header=None)

        result = main.admin_run_letter_fulfilment_worker(request, secret="test-trigger-secret")

        _, kwargs = mock_run_one_pass.call_args
        self.assertTrue(kwargs.get("is_dry_run"))  # always dry-run from this button, never live
        self.assertIn("/admin/letter-dispatches", result.url)

    @patch("main.RedirectResponse", _PositionalAwareRedirectResponse)
    @patch("main.fulfilment_worker.run_one_pass")
    @patch("main.database.get_db_conn")
    def test_exception_rolls_back_but_still_redirects(self, mock_get_db_conn, mock_run_one_pass):
        """An admin clicking the button mid-outage should land back on the
        dashboard (not a raw 500), while the DB write itself is rolled
        back, not partially committed."""
        mock_conn = MagicMock()
        mock_get_db_conn.return_value = mock_conn
        mock_run_one_pass.side_effect = RuntimeError("simulated failure")
        request = _mock_request(auth_header=None)

        result = main.admin_run_letter_fulfilment_worker(request, secret="test-trigger-secret")

        mock_conn.rollback.assert_called_once()
        mock_conn.commit.assert_not_called()
        self.assertIn("/admin/letter-dispatches", result.url)


if __name__ == "__main__":
    unittest.main()

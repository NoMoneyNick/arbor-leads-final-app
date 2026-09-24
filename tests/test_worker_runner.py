"""
test_worker_runner.py -- 2026-09-18 review, Section 6 (second pass):
worker_runner.py is the LOCAL entry point for worker.run_one_pass, needing
no FastAPI process at all (main.py's /trigger-letter-fulfilment-worker
route is the deployed one -- see tests/test_worker_trigger_routes.py).
These tests exercise worker_runner.run_once and .main directly, stubbing
out the `database`/`worker`/`fulfilment` modules it lazily imports inside
run_once (so `--help` and argument parsing work even without a real
DATABASE_URL/psycopg2 configured -- see worker_runner.py's own docstring
on run_once for why those imports are deferred to call time).

Run with:
    python -m unittest tests.test_worker_runner -v
"""
import os
import sys
import types
import unittest
from unittest.mock import MagicMock, patch

_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
_APP_DIR = os.path.dirname(_THIS_DIR)
sys.path.insert(0, _APP_DIR)

import worker_runner  # noqa: E402


class _FakeWorkerPassReport:
    def __init__(self, pipeline_active=True, approvals=None, funding=None, send_outcomes=None):
        self.pipeline_active = pipeline_active
        self.approvals = approvals
        self.funding = funding
        self.send_outcomes = send_outcomes or []


class TestRunOnce(unittest.TestCase):
    """run_once imports database/worker/fulfilment lazily (inside the
    function body) so module import + --help works without a real DB
    configured -- patch those names inside sys.modules directly, the same
    'stub the module the function will import' idiom test_access_control.py
    and friends use, rather than trying to patch worker_runner.database
    (which doesn't exist as a module-level name until run_once executes)."""

    def setUp(self):
        self._saved_modules = {}
        for name in ("database", "worker", "fulfilment"):
            self._saved_modules[name] = sys.modules.get(name)

        self._fake_database = types.ModuleType("database")
        self._mock_conn = MagicMock()
        self._mock_cur = MagicMock()
        self._mock_conn.cursor.return_value = self._mock_cur
        self._fake_database.get_db_conn = MagicMock(return_value=self._mock_conn)
        sys.modules["database"] = self._fake_database

        self._fake_worker = types.ModuleType("worker")
        self._fake_worker.run_one_pass = MagicMock(return_value=_FakeWorkerPassReport())
        sys.modules["worker"] = self._fake_worker

        self._fake_fulfilment = types.ModuleType("fulfilment")
        self._fake_fulfilment.active_pipeline = MagicMock(return_value="fulfilment")
        sys.modules["fulfilment"] = self._fake_fulfilment

    def tearDown(self):
        for name, mod in self._saved_modules.items():
            if mod is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = mod

    def test_clean_pass_returns_zero_and_commits(self):
        rc = worker_runner.run_once(is_dry_run=True, worker_id="test-runner")
        self.assertEqual(rc, 0)
        self._mock_conn.commit.assert_called_once()
        self._mock_conn.rollback.assert_not_called()
        self._mock_cur.close.assert_called_once()
        self._mock_conn.close.assert_called_once()

    def test_passes_is_dry_run_and_worker_id_through_to_run_one_pass(self):
        worker_runner.run_once(is_dry_run=False, worker_id="my-worker-id")
        _, kwargs = self._fake_worker.run_one_pass.call_args
        self.assertFalse(kwargs.get("is_dry_run"))
        self.assertEqual(kwargs.get("worker_id"), "my-worker-id")

    def test_noop_pass_still_returns_zero(self):
        """pipeline_active=False (LETTER_DISPATCH_PIPELINE not 'fulfilment')
        is a clean no-op, not a failure -- must still exit 0 so a cron/CI
        wrapper doesn't treat a deliberately-unconfigured pipeline as an
        error."""
        self._fake_worker.run_one_pass.return_value = _FakeWorkerPassReport(pipeline_active=False)
        rc = worker_runner.run_once(is_dry_run=True, worker_id="test-runner")
        self.assertEqual(rc, 0)
        self._mock_conn.commit.assert_called_once()

    def test_exception_rolls_back_and_returns_one(self):
        self._fake_worker.run_one_pass.side_effect = RuntimeError("simulated failure")
        rc = worker_runner.run_once(is_dry_run=True, worker_id="test-runner")
        self.assertEqual(rc, 1)
        self._mock_conn.rollback.assert_called_once()
        self._mock_conn.commit.assert_not_called()
        # connection must still be released even on failure
        self._mock_cur.close.assert_called_once()
        self._mock_conn.close.assert_called_once()


class TestMainArgParsing(unittest.TestCase):
    """These only exercise argument parsing / dispatch to run_once (mocked
    out entirely) -- not a real pass. --loop's sleep-based repetition isn't
    exercised end-to-end here (that would need real wall-clock time or a
    time.sleep patch racing KeyboardInterrupt timing); the single-run path
    covers the argument-mapping logic that --loop shares."""

    @patch("worker_runner.run_once")
    def test_default_invocation_is_dry_run_single_pass(self, mock_run_once):
        mock_run_once.return_value = 0
        rc = worker_runner.main([])
        self.assertEqual(rc, 0)
        _, kwargs = mock_run_once.call_args
        self.assertTrue(kwargs.get("is_dry_run"))

    @patch("worker_runner.run_once")
    def test_live_flag_disables_dry_run(self, mock_run_once):
        mock_run_once.return_value = 0
        worker_runner.main(["--live"])
        _, kwargs = mock_run_once.call_args
        self.assertFalse(kwargs.get("is_dry_run"))

    @patch("worker_runner.run_once")
    def test_worker_id_flag_is_passed_through(self, mock_run_once):
        mock_run_once.return_value = 0
        worker_runner.main(["--worker-id", "custom-id"])
        _, kwargs = mock_run_once.call_args
        self.assertEqual(kwargs.get("worker_id"), "custom-id")

    @patch("worker_runner.run_once")
    def test_single_run_exit_code_propagates_failure(self, mock_run_once):
        mock_run_once.return_value = 1
        rc = worker_runner.main([])
        self.assertEqual(rc, 1)

    @patch("worker_runner.time.sleep", side_effect=KeyboardInterrupt)
    @patch("worker_runner.run_once")
    def test_loop_mode_runs_at_least_once_then_stops_cleanly_on_interrupt(self, mock_run_once, mock_sleep):
        mock_run_once.return_value = 0
        rc = worker_runner.main(["--loop", "--interval", "5"])
        self.assertEqual(rc, 0)
        mock_run_once.assert_called_once()
        mock_sleep.assert_called_once_with(5)


if __name__ == "__main__":
    unittest.main()

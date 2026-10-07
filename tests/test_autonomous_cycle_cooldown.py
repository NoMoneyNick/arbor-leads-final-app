"""
test_autonomous_cycle_cooldown.py -- the built-in scheduler and /trigger-autonomous-cycle share ONE
20-hour cooldown, checked inside the pipeline lock before a new start is recorded.

Everything here is fake: the cycle is a stand-in function (it never runs a real stage), the "database"
is an in-memory dict of the two timestamps, and no network or real pipeline is touched.

Cases: a recent completed cycle skips; an active cycle skips; an eligible cycle starts exactly once;
simultaneous callers cannot both start; and the optional force=true (route only) bypasses the cooldown
but never the secret or the lock.

Run with:
    python -m unittest tests.test_autonomous_cycle_cooldown -v
"""
import datetime
import inspect
import os
import sys
import threading
import time
import unittest
from unittest.mock import MagicMock, patch

_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
_APP_DIR = os.path.dirname(_THIS_DIR)
sys.path.insert(0, _APP_DIR)
sys.path.insert(0, _THIS_DIR)

import test_access_control  # noqa: E402,F401  (populates sys.modules stubs + imports main)
import main  # noqa: E402

STARTED = "last_autonomous_cycle_started_at"
FINISHED = "last_autonomous_cycle_at"
SECRET = "test-trigger-secret"


def _iso(hours_ago):
    t = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(hours=hours_ago)
    return t.isoformat().replace("+00:00", "Z")


class _Base(unittest.TestCase):
    def setUp(self):
        self.state = {}                       # the fake system_state table
        self.calls = []                       # one entry per time the fake cycle actually ran
        self.gate = threading.Event()         # a held cycle waits on this
        self.entered = threading.Event()      # set once a fake cycle has started
        self.hold = False
        self._old_secret = main.T_SEC
        main.T_SEC = SECRET

        def fake_get(key):
            return self.state.get(key)

        def fake_set(key, value):
            self.state[key] = value

        def fake_cycle():
            # Mirrors the real run_full_autonomous_cycle's recording behaviour: stamp the start first, then work,
            # then stamp the finish. (The real stages are NOT run.)
            self.calls.append(time.monotonic())
            fake_set(STARTED, _iso(0))
            self.entered.set()
            if self.hold:
                self.assertTrue(self.gate.wait(10), "test left a held cycle open")
            fake_set(FINISHED, _iso(0))

        patches = [
            patch.object(main.database, "get_system_state", side_effect=fake_get, create=True),
            patch.object(main.database, "set_system_state", side_effect=fake_set, create=True),
            patch.object(main, "run_full_autonomous_cycle", fake_cycle),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        self._wait_idle()

    def tearDown(self):
        self.gate.set()
        self._wait_idle()
        main.T_SEC = self._old_secret

    def _wait_idle(self, timeout=10):
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            if not main._pipeline_state.get("running") and not main._PIPELINE_LOCK.locked():
                return
            time.sleep(0.01)
        self.fail("pipeline lock never became free")

    def route(self, **kw):
        return main.trigger_autonomous_cycle(secret=kw.pop("secret", SECRET), **kw)

    def start_held_cycle(self):
        """Starts one cycle that stays running until self.gate is set."""
        self.hold = True
        self.entered.clear()
        r = self.route()
        self.assertEqual(r["status"], "started")
        self.assertTrue(self.entered.wait(5))
        return r


class TestRecentCompletedCycleSkips(_Base):
    def _recent_completed(self):
        self.state[STARTED] = _iso(5)
        self.state[FINISHED] = _iso(4)

    def test_route_skips_inside_the_cooldown(self):
        self._recent_completed()
        r = self.route()
        self.assertEqual(r["status"], "skipped_cooldown")
        self.assertEqual(r["action"], "autonomous_daily_cycle")
        self.assertGreater(r["eligible_in_hours"], 14)
        self.assertEqual(self.calls, [])
        self.assertFalse(main._PIPELINE_LOCK.locked())
        self.assertFalse(main._pipeline_state["running"])

    def test_the_schedulers_shared_function_skips_too(self):
        self._recent_completed()
        r = main.start_autonomous_cycle_if_due()
        self.assertEqual(r["status"], "skipped_cooldown")
        self.assertEqual(self.calls, [])
        self.assertFalse(main._PIPELINE_LOCK.locked())

    def test_a_cycle_that_started_but_never_finished_still_holds_the_cooldown(self):
        self.state[STARTED] = _iso(1)          # crashed or killed: no finish stamp
        self.assertEqual(self.route()["status"], "skipped_cooldown")
        self.assertEqual(self.calls, [])

    def test_the_cooldown_ends_after_twenty_hours(self):
        self.state[STARTED] = _iso(20.5)
        self.state[FINISHED] = _iso(14)        # finished 14h ago: the MORE RECENT stamp still decides
        self.assertEqual(self.route()["status"], "skipped_cooldown")
        self.state[FINISHED] = _iso(20.2)
        self.state[STARTED] = _iso(26)
        self.assertEqual(self.route()["status"], "started")


class TestActiveCycleSkips(_Base):
    def test_route_and_scheduler_function_refuse_while_a_cycle_runs(self):
        self.start_held_cycle()
        r1 = self.route()
        r2 = main.start_autonomous_cycle_if_due()
        self.assertEqual(r1["status"], "already_running")
        self.assertEqual(r2["status"], "already_running")
        self.assertEqual(len(self.calls), 1)
        self.gate.set()
        self._wait_idle()
        self.assertEqual(len(self.calls), 1)

    def test_the_lock_is_free_again_when_the_cycle_ends(self):
        self.start_held_cycle()
        self.gate.set()
        self._wait_idle()
        self.assertFalse(main._PIPELINE_LOCK.locked())
        self.assertEqual(self.route()["status"], "skipped_cooldown")   # and its fresh stamps now hold the cooldown


class TestEligibleCycleStartsOnce(_Base):
    def test_no_stamps_at_all_starts_once(self):
        r = self.route()
        self.assertEqual(r["status"], "started")
        self.assertNotIn("forced", r)
        self._wait_idle()
        self.assertEqual(len(self.calls), 1)
        self.assertIn(STARTED, self.state)
        self.assertIn(FINISHED, self.state)

    def test_old_stamps_start_once_and_the_new_start_is_recorded_before_the_lock_is_released(self):
        self.state[STARTED] = _iso(30)
        self.state[FINISHED] = _iso(29)
        old = self.state[STARTED]
        self.start_held_cycle()
        self.assertGreater(self.state[STARTED], old)                  # recorded while the cycle still holds the lock
        self.assertTrue(main._PIPELINE_LOCK.locked())
        self.gate.set()
        self._wait_idle()
        self.assertEqual(len(self.calls), 1)

    def test_an_unreadable_stamp_counts_as_due(self):
        self.state[STARTED] = "not a date"
        self.assertEqual(self.route()["status"], "started")
        self._wait_idle()

    def test_an_immediate_second_call_after_it_finishes_does_not_start_another(self):
        self.assertEqual(self.route()["status"], "started")
        self._wait_idle()
        self.assertEqual(self.route()["status"], "skipped_cooldown")
        self.assertEqual(main.start_autonomous_cycle_if_due()["status"], "skipped_cooldown")
        self.assertEqual(len(self.calls), 1)

    def test_a_failing_stamp_read_releases_the_lock_and_starts_nothing(self):
        with patch.object(main.database, "get_system_state", side_effect=RuntimeError("db down"), create=True):
            with self.assertRaises(RuntimeError):
                main.start_autonomous_cycle_if_due()
        self.assertFalse(main._PIPELINE_LOCK.locked())
        self.assertEqual(self.calls, [])


class TestSimultaneousCallersCannotBothStart(_Base):
    def _race(self, n, hold):
        self.hold = hold
        barrier = threading.Barrier(n)
        results, errors = [], []

        def caller(i):
            try:
                barrier.wait(5)
                results.append(main.start_autonomous_cycle_if_due() if i % 2 else self.route())
            except Exception as exc:               # pragma: no cover - would fail the assertions below
                errors.append(exc)

        threads = [threading.Thread(target=caller, args=(i,)) for i in range(n)]
        for t in threads:
            t.start()
        if hold:
            self.assertTrue(self.entered.wait(5))
            time.sleep(0.1)                         # let every loser finish while the winner still holds the lock
            self.gate.set()
        for t in threads:
            t.join(10)
        self._wait_idle()
        self.assertEqual(errors, [])
        return results

    def test_eight_callers_with_a_running_cycle_start_exactly_one(self):
        results = self._race(8, hold=True)
        statuses = sorted(r["status"] for r in results)
        self.assertEqual(statuses.count("started"), 1, statuses)
        self.assertEqual(statuses.count("already_running"), 7, statuses)
        self.assertEqual(len(self.calls), 1)

    def test_callers_racing_a_cycle_that_finishes_instantly_still_start_exactly_one(self):
        for round_no in range(25):
            self.state.clear()
            self.calls.clear()
            self.entered.clear()
            results = self._race(6, hold=False)
            statuses = [r["status"] for r in results]
            self.assertEqual(statuses.count("started"), 1, (round_no, statuses))
            self.assertEqual(len(self.calls), 1, (round_no, statuses))
            self.assertTrue(all(s in ("started", "already_running", "skipped_cooldown") for s in statuses), statuses)


class TestForceBypassesOnlyTheCooldown(_Base):
    def test_forced_recovery_during_the_cooldown_after_a_crashed_cycle(self):
        self.state[STARTED] = _iso(1)           # the crashed cycle stamped its start and never finished
        self.assertEqual(self.route()["status"], "skipped_cooldown")
        self.assertEqual(self.calls, [])
        r = self.route(force=True)
        self.assertEqual(r["status"], "started")
        self.assertTrue(r["forced"])
        self._wait_idle()
        self.assertEqual(len(self.calls), 1)
        self.assertIn(FINISHED, self.state)
        # force does not switch the cooldown off: the recovery run's own stamps hold it again
        self.assertEqual(self.route()["status"], "skipped_cooldown")
        self.assertEqual(len(self.calls), 1)

    def test_force_is_refused_while_another_cycle_is_running(self):
        self.start_held_cycle()
        r = self.route(force=True)
        self.assertEqual(r["status"], "already_running")
        self.assertNotIn("forced", r)
        self.assertEqual(len(self.calls), 1)
        self.gate.set()
        self._wait_idle()
        self.assertEqual(len(self.calls), 1)

    def test_force_is_refused_while_a_different_scan_holds_the_shared_lock(self):
        self.assertTrue(main._PIPELINE_LOCK.acquire(blocking=False))   # e.g. /trigger-daily-pipeline is running
        try:
            r = self.route(force=True)
        finally:
            main._PIPELINE_LOCK.release()
        self.assertEqual(r["status"], "already_running")
        self.assertEqual(self.calls, [])

    def test_force_never_bypasses_the_secret(self):
        self.state[STARTED] = _iso(1)
        for bad in (None, "", "not-the-secret"):
            with self.assertRaises(main.HTTPException) as ctx:
                main.trigger_autonomous_cycle(secret=bad, force=True)
            self.assertEqual(ctx.exception.status_code, 401)
        self.assertEqual(self.calls, [])
        self.assertFalse(main._PIPELINE_LOCK.locked())

    def test_default_is_not_forced_and_neither_the_scheduler_nor_the_plain_url_uses_it(self):
        self.assertFalse(inspect.signature(main.start_autonomous_cycle_if_due).parameters["force"].default)
        self.state[STARTED] = _iso(1)
        self.assertEqual(self.route()["status"], "skipped_cooldown")             # no force argument at all
        self.assertEqual(self.route(force=False)["status"], "skipped_cooldown")
        self.assertEqual(main.start_autonomous_cycle_if_due()["status"], "skipped_cooldown")
        self.assertNotIn("force", inspect.getsource(main._autonomous_scheduler_loop))
        self.assertEqual(self.calls, [])

    def test_force_is_not_available_on_other_triggers(self):
        self.assertNotIn("force", inspect.signature(main.trigger_daily_pipeline).parameters)

    def test_the_crash_alert_tells_the_operator_about_force(self):
        fake_notifications = MagicMock()

        def boom():
            raise RuntimeError("simulated crash")

        with patch.dict(sys.modules, {"notifications": fake_notifications}):
            main._dispatch_locked_scan(boom, "autonomous_daily_cycle")
            self._wait_idle()
        kwargs = fake_notifications.send_system_incident_alert.call_args.kwargs
        text = kwargs["action_required"]
        self.assertIn("/trigger-autonomous-cycle?force=true", text)
        self.assertIn("cooldown", text)
        self.assertIn("another cycle is running", text)


if __name__ == "__main__":
    unittest.main()

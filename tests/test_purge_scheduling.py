"""
test_purge_scheduling.py -- 2026-09-23, Request F ("Ensure the purge
schedule matches the published 72-hour commitment. A daily sweep of
records already 72 hours old is insufficient.").

Before this fix, retention_dispatch_purge.purge_dispatched_personal_data()
was only ever called from inside run_full_autonomous_cycle, which
main.py's own _autonomous_scheduler_loop only fires once every ~20 hours
(should_run = hours_since >= 20). A record crossing the 72-hour mark
moments after one daily sweep could therefore sit fully identifying for
up to a further ~20 hours before the next sweep caught it -- in practice
up to ~92 hours after dispatch, not the "within 72 hours" the privacy
policy states.

The fix moves the purge call onto _autonomous_scheduler_loop's own
20-minute tick (same cadence as the pre-existing expired-lead-reservation
sweep), independent of whether a full daily cycle is due. This test proves
that independence directly: it closes the daily-cycle gate (as if a full
cycle just ran a moment ago, so should_run is False and
run_full_autonomous_cycle must NOT fire) and asserts the purge still runs
on this tick anyway.

Run with:
    python -m unittest tests.test_purge_scheduling -v
"""
import datetime
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
import retention_dispatch_purge  # noqa: E402  real module, not stubbed anywhere


class _StopLoop(Exception):
    """Raised from the mocked time.sleep to escape main._autonomous_
    scheduler_loop's `while True:` after exactly one iteration."""
    pass


class TestPurgeRunsOnTheFrequentTickIndependentOfTheDailyCycle(unittest.TestCase):

    def test_purge_fires_even_when_the_daily_full_cycle_gate_is_closed(self):
        sleep_calls = {"n": 0}

        def _fake_sleep(_seconds):
            sleep_calls["n"] += 1
            if sleep_calls["n"] >= 2:  # 1st call = startup 120s sleep, 2nd = end-of-loop-body sleep
                raise _StopLoop()

        # A full cycle "just finished" -- should_run's own 20-hour cooldown
        # must be CLOSED (False) for this tick.
        recent_iso = datetime.datetime.now(datetime.timezone.utc).isoformat()

        with patch("main.time.sleep", side_effect=_fake_sleep), \
             patch.object(main.database, "sweep_expired_lead_reservations", create=True), \
             patch.object(main.database, "get_system_state", return_value=recent_iso, create=True), \
             patch.object(main, "_pipeline_state", {"running": False}), \
             patch.object(main, "_dispatch_locked_scan") as mock_dispatch, \
             patch.object(retention_dispatch_purge, "purge_dispatched_personal_data",
                           return_value={"obligations_purged": 0, "dispatches_purged": 0, "leads_purged": 0}) as mock_purge, \
             patch.object(retention_dispatch_purge, "count_unknown_outcome_obligations_awaiting_reconciliation",
                           return_value=0):
            with self.assertRaises(_StopLoop):
                main._autonomous_scheduler_loop()

        mock_purge.assert_called_once()
        # The daily-cycle gate was closed (a cycle "just ran") -- proves the
        # purge call above is NOT nested inside that gated branch.
        mock_dispatch.assert_not_called()

    def test_purge_still_fires_when_the_daily_cycle_gate_is_open_too(self):
        """Sanity check the other direction: an open daily-cycle gate
        (should_run True) doesn't somehow suppress the purge tick either --
        both run independently on the same iteration."""
        sleep_calls = {"n": 0}

        def _fake_sleep(_seconds):
            sleep_calls["n"] += 1
            if sleep_calls["n"] >= 2:
                raise _StopLoop()

        with patch("main.time.sleep", side_effect=_fake_sleep), \
             patch.object(main.database, "sweep_expired_lead_reservations", create=True), \
             patch.object(main.database, "get_system_state", return_value=None, create=True), \
             patch.object(main, "_pipeline_state", {"running": False}), \
             patch.object(main, "_dispatch_locked_scan") as mock_dispatch, \
             patch.object(retention_dispatch_purge, "purge_dispatched_personal_data",
                           return_value={"obligations_purged": 0, "dispatches_purged": 0, "leads_purged": 0}) as mock_purge, \
             patch.object(retention_dispatch_purge, "count_unknown_outcome_obligations_awaiting_reconciliation",
                           return_value=0):
            with self.assertRaises(_StopLoop):
                main._autonomous_scheduler_loop()

        mock_purge.assert_called_once()
        mock_dispatch.assert_called_once()  # no last-run timestamp at all -- should_run defaults True


class TestPrivacyPolicyExplainsActualTimingNotAnAbsoluteGuarantee(unittest.TestCase):
    """Request F, part 1's second sentence: 'Explain the actual timing and
    outage handling without claiming an absolute guarantee.'"""

    def _page_html(self):
        import asyncio
        return asyncio.run(main.privacy_policy())

    def test_states_the_actual_check_cadence(self):
        html = self._page_html()
        self.assertIn("approximately every 20 minutes", html)

    def test_explains_outage_handling(self):
        html = self._page_html()
        self.assertIn("briefly unavailable", html)
        self.assertIn("resumes as soon as service is restored", html)

    def test_does_not_claim_an_absolute_guarantee(self):
        html = self._page_html()
        self.assertIn("we do not guarantee deletion at the exact 72-hour mark", html)


if __name__ == "__main__":
    unittest.main()

"""Oct 22 2026: silent-source rule now scales with each source's own rate."""
import importlib.util
import os
import sys
import types
import unittest
from unittest.mock import MagicMock, patch

if "psycopg2" not in sys.modules:
    sys.modules["psycopg2"] = types.ModuleType("psycopg2")
# Load database.py privately so other tests' stub modules cannot interfere.
_spec = importlib.util.spec_from_file_location(
    "_database_health_under_test", os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "database.py"))
database = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(database)


def _report(rows):
    conn, cur = MagicMock(), MagicMock()
    cur.fetchall.return_value = rows
    conn.cursor.return_value = cur
    with patch.object(database, "get_db_conn", return_value=conn), patch.object(database, "SURL", "postgres://x"):
        return {r["council_source"]: r for r in database.get_lead_source_health_report()}


class TestSilentSourceRule(unittest.TestCase):
    def test_busy_source_quiet_three_days_is_flagged(self):
        r = _report([("Busy", 54, 0, 3.2)])          # 2/day, silent 3.2 days
        self.assertTrue(r["Busy"]["possible_silent_failure"])

    def test_slow_source_quiet_three_days_is_not_flagged(self):
        r = _report([("Slow", 8, 0, 3.5)])           # ~0.3/day: 3 quiet days is normal chance
        self.assertFalse(r["Slow"]["possible_silent_failure"])
        self.assertEqual(r["Slow"]["quiet_days_needed"], 16)

    def test_slow_source_quiet_long_enough_is_flagged(self):
        r = _report([("Slow", 8, 0, 17.0)])
        self.assertTrue(r["Slow"]["possible_silent_failure"])

    def test_source_too_sparse_to_judge_is_never_flagged(self):
        r = _report([("Sparse", 3, 0, 40.0)])        # ~0.11/day -> would need 42 days > 21
        self.assertFalse(r["Sparse"]["possible_silent_failure"])

    def test_source_with_recent_leads_is_not_flagged(self):
        r = _report([("Ok", 54, 4, 0.2)])
        self.assertFalse(r["Ok"]["possible_silent_failure"])


if __name__ == "__main__":
    unittest.main()

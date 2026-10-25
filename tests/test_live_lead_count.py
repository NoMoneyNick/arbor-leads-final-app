"""Live marketplace count (25 Oct 2026): counts what is on sale now, using the marketplace's own rules."""
import datetime
import importlib.util
import os
import sys
import types
import unittest
from unittest.mock import MagicMock, patch

_APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _APP_DIR)
for _m in ("psycopg2", "stripe"):
    if _m not in sys.modules:
        sys.modules[_m] = MagicMock() if _m == "stripe" else types.ModuleType(_m)
_spec = importlib.util.spec_from_file_location("_database_under_test_live_count", os.path.join(_APP_DIR, "database.py"))
db = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(db)

NOW = datetime.datetime.now(datetime.timezone.utc)


def row(days_old, source="council_planning", has_agent=None, agent_tree=None):
    d = NOW - datetime.timedelta(days=days_old)
    return (d, "Pending", "Fell one oak tree.", d, source, has_agent, agent_tree)


class TestLiveCount(unittest.TestCase):
    def _count(self, rows):
        cur = MagicMock(); cur.fetchall.return_value = rows
        conn = MagicMock(); conn.cursor.return_value = cur
        db._LIVE_COUNT_CACHE.update(t=0.0, data=None)
        with patch.object(db, "SURL", "postgres://fake"), patch.object(db, "get_db_conn", return_value=conn):
            out = db.count_live_marketplace_leads()
        return out, cur.execute.call_args[0][0]

    def test_only_sellable_leads_are_counted(self):
        rows = [row(5), row(10), row(200),                      # 2 live, 1 expired
                row(6, has_agent=True, agent_tree=True),        # tree-surgeon agent: not for sale
                row(6, has_agent=True, agent_tree=False),       # non-tree agent: for sale
                row(3, source="domestic_classified")]
        out, sql = self._count(rows)
        self.assertEqual(out, {"total": 4, "council": 3, "domestic": 1})
        self.assertIn("status = 'new' OR status IS NULL", sql)
        self.assertIn("'tree'", sql)

    def test_database_failure_shows_no_number(self):
        db._LIVE_COUNT_CACHE.update(t=0.0, data=None)
        with patch.object(db, "SURL", "postgres://fake"), patch.object(db, "get_db_conn", side_effect=RuntimeError("down")):
            self.assertEqual(db.count_live_marketplace_leads(), {"total": None, "council": None, "domestic": None})


if __name__ == "__main__":
    unittest.main()

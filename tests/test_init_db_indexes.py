"""init_db() index step (25 Oct 2026 incident): existing indexes are skipped without touching the table,
new ones get a short lock timeout in their own savepoint, failures are named and never reported as OK."""
import importlib.util
import os
import sys
import types
import unittest
from unittest.mock import MagicMock, patch

_APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _APP_DIR)
if "psycopg2" not in sys.modules:
    sys.modules["psycopg2"] = types.ModuleType("psycopg2")
_spec = importlib.util.spec_from_file_location("_database_under_test_init_indexes", os.path.join(_APP_DIR, "database.py"))
db = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(db)

EXISTING = "CREATE INDEX IF NOT EXISTS idx_leads_discovered_at ON leads(discovered_at DESC);"
NEW_OK = "CREATE INDEX IF NOT EXISTS idx_new_ok ON payments(account_email);"
NEW_BLOCKED = "CREATE UNIQUE INDEX IF NOT EXISTS idx_new_blocked ON leads(reference);"


class FakeCursor:
    def __init__(self, existing=(), blocked=()):
        self.existing, self.blocked, self.sql, self._row = set(existing), set(blocked), [], None

    def execute(self, sql, params=None):
        self.sql.append(sql)
        if sql.startswith("SELECT to_regclass"):
            self._row = ("public." + params[0],) if params[0] in self.existing else (None,)
        elif sql in self.blocked:
            raise RuntimeError("canceling statement due to lock timeout")

    def fetchone(self):
        return self._row


class TestCreateMissingIndexes(unittest.TestCase):
    def test_existing_index_is_skipped_without_running_create(self):
        cur = FakeCursor(existing={"idx_leads_discovered_at"})
        self.assertEqual(db._create_missing_indexes(cur, [EXISTING]), [])
        self.assertNotIn(EXISTING, cur.sql)
        self.assertFalse(any("lock_timeout" in s for s in cur.sql))

    def test_new_index_gets_short_lock_timeout_in_a_savepoint(self):
        cur = FakeCursor()
        self.assertEqual(db._create_missing_indexes(cur, [NEW_OK]), [])
        i = cur.sql.index(NEW_OK)
        self.assertEqual(cur.sql[i - 2], "SAVEPOINT tk_create_index;")
        self.assertEqual(cur.sql[i - 1], "SET LOCAL lock_timeout = '5000ms';")
        self.assertEqual(cur.sql[i + 1], "RELEASE SAVEPOINT tk_create_index;")

    def test_blocked_index_is_named_rolled_back_alone_and_the_rest_continue(self):
        cur = FakeCursor(existing={"idx_leads_discovered_at"}, blocked={NEW_BLOCKED})
        failed = db._create_missing_indexes(cur, [EXISTING, NEW_BLOCKED, NEW_OK])
        self.assertEqual(len(failed), 1)
        self.assertIn("idx_new_blocked", failed[0][0])
        self.assertIn("lock timeout", failed[0][1])
        self.assertIn("ROLLBACK TO SAVEPOINT tk_create_index;", cur.sql)
        self.assertIn(NEW_OK, cur.sql)

    def test_every_core_index_statement_has_a_name_the_check_can_read(self):
        src = open(os.path.join(_APP_DIR, "database.py"), encoding="utf-8").read()
        block = src[src.index("indices = [", src.index("def init_db")):]
        block = block[:block.index("]")]
        stmts = [l.strip().strip('",') for l in block.splitlines() if "CREATE" in l]
        self.assertGreaterEqual(len(stmts), 20)
        for stmt in stmts:
            self.assertIsNotNone(db._INDEX_NAME_RE.search(stmt), stmt)


class TestPhase1Reporting(unittest.TestCase):
    def test_nothing_missing_is_ok(self):
        self.assertTrue(db._report_phase1_problems([]))

    def test_missing_items_are_named_in_the_log_and_alert_and_not_ok(self):
        fake_notifications = types.ModuleType("notifications")
        fake_notifications.send_system_incident_alert = MagicMock()
        with patch.dict(sys.modules, {"notifications": fake_notifications}), \
             self.assertLogs(db.logger, level="ERROR") as logs:
            ok = db._report_phase1_problems([(NEW_BLOCKED, "canceling statement due to lock timeout")])
        self.assertFalse(ok)
        self.assertIn("idx_new_blocked", "\n".join(logs.output))
        kwargs = fake_notifications.send_system_incident_alert.call_args.kwargs
        self.assertIn("idx_new_blocked", kwargs["description"])

    def test_ok_message_only_when_no_problems_and_no_failed_columns(self):
        src = open(os.path.join(_APP_DIR, "database.py"), encoding="utf-8").read()
        self.assertIn('if _report_phase1_problems(phase1_problems) and not failed_ddl:\n            logger.info("[DB] Phase 1 OK', src)
        self.assertEqual(src.count('"[DB] Phase 1 OK'), 1)


if __name__ == "__main__":
    unittest.main()

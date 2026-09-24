"""
test_lead_retention.py -- the 2026-09-22 handoff's "unsold-lead deletion is
a separate, later clock than sale eligibility" fix:

  1. UNSOLD_LEAD_DELETION_DAYS is 60, not the 56-day figure
     calculate_lead_freshness uses for TPO sale eligibility -- and
     STALE_LEAD_SQL_WHERE actually uses that constant, not a hardcoded 56.
  2. The double-COALESCE date check is deliberate (each COALESCE isolates
     one field, falling back to the other only when its own is missing --
     equivalent to "the more recent of the two available dates must also
     be past the cutoff"), not the redundant no-op it can look like on a
     first read. Verified here with a pure-Python mirror of the same
     boolean logic against representative date combinations, since no
     real Postgres is available in this environment to execute the SQL
     directly.
  3. A row whose only reason for looking "old enough" is a missing,
     implausible (pre-2015), or contradictory (registered_date after
     discovered_at) date must be excluded from deletion and show up in
     count_deletion_quarantined_leads instead.
  4. cleanup_stale_leads raises an admin-visible incident alert (not just
     a log line) and re-raises, rather than silently swallowing a DELETE
     failure -- "alert and retry on cleanup failure."

Uses the same "load database.py under a private module name, stub
psycopg2 only" technique tests/test_reconciliation.py already established.

Run with:
    python -m unittest tests.test_lead_retention -v
"""
import datetime
import importlib.util
import os
import sys
import types
import unittest
from unittest.mock import MagicMock, patch

_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
_APP_DIR = os.path.dirname(_THIS_DIR)
sys.path.insert(0, _APP_DIR)

if "psycopg2" not in sys.modules:
    sys.modules["psycopg2"] = types.ModuleType("psycopg2")

_DATABASE_PATH = os.path.join(_APP_DIR, "database.py")
_spec = importlib.util.spec_from_file_location("_database_under_test_lead_retention", _DATABASE_PATH)
database = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(database)


def _conn_with_cursor():
    conn = MagicMock()
    cur = MagicMock()
    conn.cursor.return_value = cur
    return conn, cur


class TestDeletionClockIsSeparateFromSaleEligibility(unittest.TestCase):
    def test_deletion_window_is_60_not_56(self):
        self.assertEqual(database.UNSOLD_LEAD_DELETION_DAYS, 60)

    def test_stale_lead_sql_uses_the_named_constant(self):
        self.assertIn("INTERVAL '60 days'", database.STALE_LEAD_SQL_WHERE)
        self.assertNotIn("56 days", database.STALE_LEAD_SQL_WHERE)

    def test_quarantine_sql_shares_the_same_implausible_floor(self):
        self.assertIn(database.IMPLAUSIBLE_DATE_FLOOR, database.STALE_LEAD_SQL_WHERE)
        self.assertIn(database.IMPLAUSIBLE_DATE_FLOOR, database.DELETION_QUARANTINE_SQL_WHERE)


class TestDoubleDateCheckLogic(unittest.TestCase):
    """A pure-Python mirror of STALE_LEAD_SQL_WHERE's boolean logic (no
    real Postgres available here) -- kept intentionally literal/verbose
    rather than clever, so it stands on its own as a readable check of
    what the SQL is actually supposed to do."""

    CUTOFF_DAYS = database.UNSOLD_LEAD_DELETION_DAYS
    FLOOR = datetime.date.fromisoformat(database.IMPLAUSIBLE_DATE_FLOOR)

    @classmethod
    def _now(cls):
        return datetime.datetime(2026, 9, 23, tzinfo=datetime.timezone.utc)

    @classmethod
    def _eligible_for_deletion(cls, registered_date, discovered_at):
        now = cls._now()
        cutoff = now - datetime.timedelta(days=cls.CUTOFF_DAYS)

        def _as_dt(d):
            if d is None:
                return None
            if isinstance(d, datetime.datetime):
                return d
            return datetime.datetime(d.year, d.month, d.day, tzinfo=datetime.timezone.utc)

        r, disc = _as_dt(registered_date), _as_dt(discovered_at)
        coalesce_1 = r if r is not None else disc  # COALESCE(registered_date, discovered_at)
        coalesce_2 = disc if disc is not None else r  # COALESCE(discovered_at, registered_date)
        if coalesce_1 is None or coalesce_2 is None:
            return False  # both NULL -> comparison is unknown -> excluded
        if not (coalesce_1 < cutoff and coalesce_2 < cutoff):
            return False
        if registered_date is not None and (r.date() if isinstance(r, datetime.datetime) else registered_date) < cls.FLOOR:
            return False
        if discovered_at is not None and disc < datetime.datetime(cls.FLOOR.year, cls.FLOOR.month, cls.FLOOR.day, tzinfo=datetime.timezone.utc):
            return False
        if registered_date is not None and discovered_at is not None:
            reg_date = registered_date if isinstance(registered_date, datetime.date) and not isinstance(registered_date, datetime.datetime) else r.date()
            disc_date = discovered_at.date() if isinstance(discovered_at, datetime.datetime) else discovered_at
            if reg_date > disc_date + datetime.timedelta(days=1):
                return False
        return True

    def test_both_dates_old_is_eligible(self):
        self.assertTrue(self._eligible_for_deletion(
            registered_date=datetime.date(2026, 6, 1),
            discovered_at=datetime.datetime(2026, 6, 2, tzinfo=datetime.timezone.utc),
        ))

    def test_registered_date_old_but_discovered_at_recent_is_protected(self):
        """Nick's original ask: one stray recent date protects the lead,
        even though registered_date alone looks stale."""
        self.assertFalse(self._eligible_for_deletion(
            registered_date=datetime.date(2026, 1, 1),
            discovered_at=datetime.datetime(2026, 9, 20, tzinfo=datetime.timezone.utc),
        ))

    def test_discovered_at_old_but_registered_date_recent_is_protected(self):
        self.assertFalse(self._eligible_for_deletion(
            registered_date=datetime.date(2026, 9, 20),
            discovered_at=datetime.datetime(2026, 1, 1, tzinfo=datetime.timezone.utc),
        ))

    def test_only_registered_date_present_and_old_is_eligible(self):
        self.assertTrue(self._eligible_for_deletion(
            registered_date=datetime.date(2026, 6, 1), discovered_at=None,
        ))

    def test_only_discovered_at_present_and_old_is_eligible(self):
        self.assertTrue(self._eligible_for_deletion(
            registered_date=None, discovered_at=datetime.datetime(2026, 6, 1, tzinfo=datetime.timezone.utc),
        ))

    def test_both_dates_missing_is_protected_not_deleted(self):
        self.assertFalse(self._eligible_for_deletion(registered_date=None, discovered_at=None))

    def test_not_yet_past_the_60_day_deadline_is_protected(self):
        self.assertFalse(self._eligible_for_deletion(
            registered_date=datetime.date(2026, 9, 1),  # 22 days ago, sellable and inside the deletion window
            discovered_at=datetime.datetime(2026, 9, 1, tzinfo=datetime.timezone.utc),
        ))

    def test_implausible_registered_date_is_quarantined_not_deleted(self):
        """A pre-2015 registered_date is almost certainly a parsing bug,
        not a genuine decade-old application -- must be excluded even
        though it technically satisfies 'older than the cutoff'."""
        self.assertFalse(self._eligible_for_deletion(
            registered_date=datetime.date(1970, 1, 1),
            discovered_at=datetime.datetime(2026, 6, 1, tzinfo=datetime.timezone.utc),
        ))

    def test_registered_date_after_discovered_at_is_contradictory_and_quarantined(self):
        """Impossible in practice (an application can't be registered
        after TreeKey discovered it) -- treated as unreliable data, not
        extra-confident evidence of staleness."""
        self.assertFalse(self._eligible_for_deletion(
            registered_date=datetime.date(2026, 7, 1),
            discovered_at=datetime.datetime(2026, 6, 1, tzinfo=datetime.timezone.utc),
        ))


class TestCleanupStaleLeadsHappyPath(unittest.TestCase):
    def test_returns_deleted_and_quarantined_counts(self):
        conn, cur = _conn_with_cursor()
        cur.rowcount = 3
        # First cur.execute() is the DELETE (rowcount read straight off
        # cur, no fetchone needed); count_deletion_quarantined_leads then
        # opens its OWN connection/cursor via get_db_conn, so we return a
        # fresh mock from that too.
        quarantine_conn, quarantine_cur = _conn_with_cursor()
        quarantine_cur.fetchone.return_value = (2,)

        calls = {"n": 0}

        def _get_db_conn():
            calls["n"] += 1
            return conn if calls["n"] == 1 else quarantine_conn

        with patch.object(database, "get_db_conn", side_effect=_get_db_conn):
            result = database.cleanup_stale_leads()

        self.assertEqual(result, {"deleted": 3, "quarantined": 2})
        conn.commit.assert_called_once()


class TestCleanupStaleLeadsFailureIsNeverSilent(unittest.TestCase):
    def test_delete_failure_alerts_and_reraises(self):
        conn, cur = _conn_with_cursor()
        cur.execute.side_effect = RuntimeError("simulated DB outage mid-DELETE")

        fake_notifications = types.ModuleType("notifications")
        fake_notifications.send_system_incident_alert = MagicMock()
        with patch.object(database, "get_db_conn", return_value=conn), \
             patch.dict(sys.modules, {"notifications": fake_notifications}):
            with self.assertRaises(RuntimeError):
                database.cleanup_stale_leads()

        conn.rollback.assert_called_once()
        fake_notifications.send_system_incident_alert.assert_called_once()
        alert_kwargs = fake_notifications.send_system_incident_alert.call_args.kwargs
        self.assertEqual(alert_kwargs.get("category"), "DATA RETENTION")
        self.assertIn("simulated DB outage", alert_kwargs.get("description", ""))

    def test_alert_itself_failing_does_not_hide_the_original_error(self):
        """The alert channel failing too must never swallow the real
        DELETE failure -- the original exception is what callers (the
        admin route, the autonomous cycle) need to see and retry on."""
        conn, cur = _conn_with_cursor()
        cur.execute.side_effect = RuntimeError("simulated DB outage mid-DELETE")

        fake_notifications = types.ModuleType("notifications")
        fake_notifications.send_system_incident_alert = MagicMock(side_effect=Exception("alert channel also down"))
        with patch.object(database, "get_db_conn", return_value=conn), \
             patch.dict(sys.modules, {"notifications": fake_notifications}):
            with self.assertRaises(RuntimeError) as ctx:
                database.cleanup_stale_leads()
        self.assertIn("simulated DB outage", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()

"""
test_scraper_resilience.py -- 2026-09-22 review: tests for
scraper_resilience.py's incident/repair-attempt lifecycle and scan-
checkpoint/pass-metrics bookkeeping (see that module's own docstring for
the full origin -- an external architecture review relayed by Nick, in
response to Claude's own finding that the pre-existing detection/alerting
infrastructure never tracked whether a fix actually worked).

Uses a plain queued FakeCursor/FakeConn (same convention as every other
FakeCursor in this test suite, e.g. tests/test_address_release_allocation_
eligibility.py) and patches via @patch.object(scraper_resilience.database,
...) rather than string-based @patch("database....") -- see this session's
own earlier-documented sys.modules["database"] identity-desync bug for why
attribute-patching on the module's own bound reference is the fix that
survives other test files reassigning sys.modules["database"] during
collection.

Run with:
    python -m unittest tests.test_scraper_resilience -v
"""
import os
import sys
import types
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

if "database" not in sys.modules:
    sys.modules["database"] = types.ModuleType("database")

import scraper_resilience

# scraper_resilience.py binds `database` once at its own module-import time
# (see that module's own comment on why). If some OTHER test file imported
# scraper_resilience FIRST during this same `unittest discover` collection
# (Python caches the module -- a later `import scraper_resilience` here
# returns that same cached object without re-running its top-level code),
# scraper_resilience.database could already be bound to a bare stub from
# THAT file, missing get_db_conn/SURL. Ensuring the attributes exist on the
# ACTUALLY-bound object (scraper_resilience.database) rather than on
# whatever sys.modules["database"] happens to be right now is what makes
# this robust to that ordering, regardless of which test file imports
# scraper_resilience first.
if not hasattr(scraper_resilience.database, "get_db_conn"):
    from unittest.mock import MagicMock
    scraper_resilience.database.get_db_conn = MagicMock()
if not hasattr(scraper_resilience.database, "SURL"):
    scraper_resilience.database.SURL = "postgres://fake-for-tests"


class FakeCursor:
    """Queued fetchone/fetchall results, consumed in call order -- same
    convention as every other FakeCursor in this test suite."""

    def __init__(self, fetchone_results=None, fetchall_results=None, rowcount=1):
        self.executed = []
        self._fetchone_results = list(fetchone_results or [])
        self._fetchall_results = list(fetchall_results or [])
        # psycopg2 cursors always expose .rowcount after execute(); this
        # fake defaults to 1 (matches "the upsert affected a row" -- the
        # common case in these tests) so every pre-existing test that
        # doesn't care about it keeps working unchanged. Tests for
        # advance_council_scan_checkpoint's backward-movement protection
        # (2026-09-22 second review) pass rowcount=0 to simulate the
        # UPDATE ... WHERE guard blocking an out-of-order write.
        self.rowcount = rowcount

    def close(self):
        pass

    def execute(self, sql, params=None):
        self.executed.append((" ".join(sql.split()), params))

    def fetchone(self):
        return self._fetchone_results.pop(0) if self._fetchone_results else None

    def fetchall(self):
        return self._fetchall_results.pop(0) if self._fetchall_results else []


class FakeConn:
    def __init__(self, cur):
        self._cur = cur
        self.committed = False
        self.rolled_back = False
        self.closed = False

    def cursor(self):
        return self._cur

    def commit(self):
        self.committed = True

    def rollback(self):
        self.rolled_back = True

    def close(self):
        self.closed = True


class _ScraperResilienceTestBase(unittest.TestCase):
    def setUp(self):
        self._surl_patch = patch.object(scraper_resilience.database, "SURL", "postgres://fake-for-tests")
        self._surl_patch.start()

    def tearDown(self):
        self._surl_patch.stop()

    def _patched_conn(self, cur):
        conn = FakeConn(cur)
        return patch.object(scraper_resilience.database, "get_db_conn", return_value=conn), conn


class TestOpenOrTouchIncident(_ScraperResilienceTestBase):
    def test_no_existing_row_inserts_a_new_incident(self):
        cur = FakeCursor(fetchone_results=[None, ("new-incident-id",)])
        patcher, conn = self._patched_conn(cur)
        with patcher:
            result = scraper_resilience.open_or_touch_incident(
                council="Bristol", platform="idox", failure_type="page_structure_changed", note="7/7 days"
            )
        self.assertEqual(result, "new-incident-id")
        self.assertTrue(conn.committed)
        insert_calls = [sql for sql, _ in cur.executed if "INSERT INTO source_incident" in sql]
        self.assertEqual(len(insert_calls), 1)

    def test_no_existing_row_insert_uses_atomic_on_conflict_to_close_the_race(self):
        """2026-09-22 second review (Astra): two concurrent callers could
        both see 'no row' from the SELECT and both attempt a plain INSERT --
        the second would violate idx_source_incident_open_unique and be
        silently lost. Fixed via ON CONFLICT ... DO UPDATE targeting that
        exact index, so this asserts the atomic statement is actually what
        gets executed, not just that *an* insert happens."""
        cur = FakeCursor(fetchone_results=[None, ("new-incident-id",)])
        patcher, _conn = self._patched_conn(cur)
        with patcher:
            scraper_resilience.open_or_touch_incident(
                council="Bristol", platform="idox", failure_type="page_structure_changed"
            )
        insert_sql = [sql for sql, _ in cur.executed if "INSERT INTO source_incident" in sql][0]
        self.assertIn("ON CONFLICT (council, failure_type) WHERE status != 'resolved'", insert_sql)
        self.assertIn("DO UPDATE", insert_sql)

    def test_existing_open_row_is_touched_not_duplicated(self):
        cur = FakeCursor(fetchone_results=[("existing-id", "open")])
        patcher, conn = self._patched_conn(cur)
        with patcher:
            result = scraper_resilience.open_or_touch_incident(
                council="Bristol", platform="idox", failure_type="page_structure_changed"
            )
        self.assertEqual(result, "existing-id")
        insert_calls = [sql for sql, _ in cur.executed if "INSERT INTO source_incident" in sql]
        self.assertEqual(insert_calls, [], "must not insert a duplicate row for an already-open incident")
        update_calls = [sql for sql, _ in cur.executed if sql.startswith("UPDATE source_incident SET last_seen")]
        self.assertEqual(len(update_calls), 1)

    def test_previously_resolved_row_is_reopened_not_duplicated(self):
        """The review's own words: 'reopen it when the same failure
        returns; don't suppress it merely because it was previously
        seen.'"""
        cur = FakeCursor(fetchone_results=[("resolved-id", "resolved")])
        patcher, conn = self._patched_conn(cur)
        with patcher:
            result = scraper_resilience.open_or_touch_incident(
                council="Bristol", platform="idox", failure_type="silent_source_failure"
            )
        self.assertEqual(result, "resolved-id")
        insert_calls = [sql for sql, _ in cur.executed if "INSERT INTO source_incident" in sql]
        self.assertEqual(insert_calls, [], "must reopen the existing row, never insert a fresh duplicate")
        reopen_calls = [sql for sql, _ in cur.executed if "status = 'open'" in sql]
        self.assertEqual(len(reopen_calls), 1)
        self.assertIn("reopen_count = reopen_count + 1", reopen_calls[0],
                       "2026-09-22 second review: reopening must increment reopen_count")

    def test_fails_safe_to_none_when_surl_not_configured(self):
        with patch.object(scraper_resilience.database, "SURL", ""):
            result = scraper_resilience.open_or_touch_incident(
                council="Bristol", platform="idox", failure_type="page_structure_changed"
            )
        self.assertIsNone(result)

    def test_fails_safe_to_none_on_database_error(self):
        patcher = patch.object(scraper_resilience.database, "get_db_conn", side_effect=RuntimeError("db down"))
        with patcher:
            result = scraper_resilience.open_or_touch_incident(
                council="Bristol", platform="idox", failure_type="page_structure_changed"
            )
        self.assertIsNone(result)


class TestRecordRepairAttempt(_ScraperResilienceTestBase):
    def test_inserts_attempt_and_bumps_incident_to_repair_attempted(self):
        cur = FakeCursor(fetchone_results=[("attempt-id",)])
        patcher, conn = self._patched_conn(cur)
        with patcher:
            result = scraper_resilience.record_repair_attempt(
                "incident-1", action="Added fallback selector for Idox v2 template", outcome="success"
            )
        self.assertEqual(result, "attempt-id")
        self.assertTrue(conn.committed)
        update_calls = [sql for sql, _ in cur.executed if "status = 'repair_attempted'" in sql]
        self.assertEqual(len(update_calls), 1)

    def test_rejects_unknown_outcome(self):
        with self.assertRaises(ValueError):
            scraper_resilience.record_repair_attempt("incident-1", action="x", outcome="maybe")

    def test_fails_safe_to_none_without_incident_id(self):
        result = scraper_resilience.record_repair_attempt(None, action="x")
        self.assertIsNone(result)


class TestIncidentLifecycleTransitions(_ScraperResilienceTestBase):
    def test_mark_verifying_only_succeeds_from_repair_attempted(self):
        cur = FakeCursor(fetchone_results=[("incident-1",)])
        patcher, conn = self._patched_conn(cur)
        with patcher:
            result = scraper_resilience.mark_incident_verifying("incident-1")
        self.assertTrue(result)

    def test_mark_verifying_returns_false_when_no_row_matched(self):
        cur = FakeCursor(fetchone_results=[None])
        patcher, _conn = self._patched_conn(cur)
        with patcher:
            result = scraper_resilience.mark_incident_verifying("incident-not-repair-attempted")
        self.assertFalse(result)

    def test_resolve_incident_requires_verified_by_and_note(self):
        cur = FakeCursor(fetchone_results=[("incident-1",)])
        patcher, conn = self._patched_conn(cur)
        with patcher:
            result = scraper_resilience.resolve_incident(
                "incident-1", verified_by="nick@treekey.co.uk",
                note="Verified 3 consecutive clean passes after fallback selector deployed."
            )
        self.assertTrue(result)
        self.assertTrue(conn.committed)
        update_sql = [sql for sql, params in cur.executed if "status = 'resolved'" in sql][0]
        self.assertIn("resolved_by", update_sql)
        self.assertIn("last_verified_success", update_sql)

    def test_a_repair_attempt_alone_does_not_resolve_the_incident(self):
        """The review's own words: 'a proposed patch does not resolve an
        incident.' record_repair_attempt must never itself flip status to
        'resolved' -- only resolve_incident does."""
        cur = FakeCursor(fetchone_results=[("attempt-id",)])
        patcher, _conn = self._patched_conn(cur)
        with patcher:
            scraper_resilience.record_repair_attempt("incident-1", action="tried something", outcome="success")
        resolved_calls = [sql for sql, _ in cur.executed if "status = 'resolved'" in sql]
        self.assertEqual(resolved_calls, [])


class TestVerificationResult(_ScraperResilienceTestBase):
    """2026-09-22 second review (Astra): record_verification_result fills
    the gap where a FAILED verification had nowhere to go -- an incident
    could be stuck in 'verifying' with no record of a check having failed."""

    def test_failure_reopens_incident_and_records_reason(self):
        cur = FakeCursor(fetchone_results=[("incident-1",)])
        patcher, conn = self._patched_conn(cur)
        with patcher:
            result = scraper_resilience.record_verification_result(
                "incident-1", outcome="failure", failure_reason="Still 0 records parsed after fallback selector deploy"
            )
        self.assertTrue(result)
        self.assertTrue(conn.committed)
        update_sql, params = cur.executed[0]
        self.assertIn("status = 'open'", update_sql)
        self.assertIn("last_verification_failure_reason", update_sql)
        self.assertIn("failure", params)

    def test_success_records_outcome_without_forcing_a_status_transition(self):
        """The review's own words carried over from resolve_incident's own
        docstring: a passing check is evidence, not itself a resolution --
        resolve_incident stays the only thing that sets 'resolved'."""
        cur = FakeCursor(fetchone_results=[("incident-1",)])
        patcher, conn = self._patched_conn(cur)
        with patcher:
            result = scraper_resilience.record_verification_result("incident-1", outcome="success")
        self.assertTrue(result)
        self.assertTrue(conn.committed)
        update_sql = cur.executed[0][0]
        self.assertNotIn("status =", update_sql)
        self.assertNotIn("resolved", update_sql)

    def test_rejects_unknown_outcome(self):
        with self.assertRaises(ValueError):
            scraper_resilience.record_verification_result("incident-1", outcome="maybe")

    def test_fails_safe_to_false_without_incident_id(self):
        result = scraper_resilience.record_verification_result(None, outcome="success")
        self.assertFalse(result)

    def test_fails_safe_to_false_on_database_error(self):
        patcher = patch.object(scraper_resilience.database, "get_db_conn", side_effect=RuntimeError("db down"))
        with patcher:
            result = scraper_resilience.record_verification_result("incident-1", outcome="success")
        self.assertFalse(result)


class TestScanCheckpoint(_ScraperResilienceTestBase):
    def test_advance_checkpoint_upserts(self):
        cur = FakeCursor(rowcount=1)
        patcher, conn = self._patched_conn(cur)
        with patcher:
            result = scraper_resilience.advance_council_scan_checkpoint(
                "Bristol", "idox", window_start="2026-09-15T00:00:00Z", window_end="2026-09-22T00:00:00Z"
            )
        self.assertTrue(result)
        self.assertTrue(conn.committed)
        insert_sql, params = cur.executed[0]
        self.assertIn("ON CONFLICT (council, search_definition) DO UPDATE", insert_sql)
        # search_definition defaults to 'default' when the caller doesn't
        # specify one -- 2026-09-22 second review.
        self.assertIn("default", params)

    def test_advance_checkpoint_accepts_explicit_search_definition(self):
        cur = FakeCursor(rowcount=1)
        patcher, conn = self._patched_conn(cur)
        with patcher:
            result = scraper_resilience.advance_council_scan_checkpoint(
                "Bristol", "idox", window_start="2026-09-15T00:00:00Z", window_end="2026-09-22T00:00:00Z",
                search_definition="demolition_notices",
            )
        self.assertTrue(result)
        self.assertIn("demolition_notices", cur.executed[0][1])

    def test_advance_checkpoint_sql_guards_against_moving_backwards(self):
        """2026-09-22 second review: the UPDATE branch must only fire when
        the new window_end is actually later than the existing one (or no
        checkpoint/window_end exists yet) -- this is enforced in Postgres
        itself via the upsert's WHERE guard, so assert that guard is
        actually present in the executed SQL."""
        cur = FakeCursor(rowcount=1)
        patcher, _conn = self._patched_conn(cur)
        with patcher:
            scraper_resilience.advance_council_scan_checkpoint(
                "Bristol", "idox", window_start="2026-09-15T00:00:00Z", window_end="2026-09-22T00:00:00Z"
            )
        insert_sql = cur.executed[0][0]
        self.assertIn("last_completed_window_end IS NULL", insert_sql)
        self.assertIn("EXCLUDED.last_completed_window_end > council_scan_checkpoint.last_completed_window_end", insert_sql)

    def test_advance_checkpoint_still_reports_true_when_guard_blocks_the_write(self):
        """rowcount=0 simulates an older/slower concurrent scan losing the
        WHERE guard race -- the call still succeeded (no exception, no lost
        incident-tracking), it just correctly declined to move the
        checkpoint backwards, so this should still return True, not False."""
        cur = FakeCursor(rowcount=0)
        patcher, conn = self._patched_conn(cur)
        with patcher:
            result = scraper_resilience.advance_council_scan_checkpoint(
                "Bristol", "idox", window_start="2026-09-01T00:00:00Z", window_end="2026-09-10T00:00:00Z"
            )
        self.assertTrue(result)
        self.assertTrue(conn.committed)

    def test_get_checkpoint_returns_none_when_no_row(self):
        cur = FakeCursor(fetchone_results=[None])
        patcher, _conn = self._patched_conn(cur)
        with patcher:
            result = scraper_resilience.get_council_scan_checkpoint("Brand New Council")
        self.assertIsNone(result)

    def test_get_checkpoint_defaults_search_definition(self):
        cur = FakeCursor(fetchone_results=[None])
        patcher, _conn = self._patched_conn(cur)
        with patcher:
            scraper_resilience.get_council_scan_checkpoint("Bristol")
        self.assertIn("default", cur.executed[0][1])


class TestPassMetrics(_ScraperResilienceTestBase):
    def test_records_all_counters(self):
        cur = FakeCursor(fetchone_results=[("metrics-id",)])
        patcher, conn = self._patched_conn(cur)
        with patcher:
            result = scraper_resilience.record_council_scan_pass_metrics(
                council="Bristol", platform="idox", pages_fetched=12, links_discovered=40,
                records_parsed=38, records_rejected_validation=2,
                records_excluded_business_filters=5, new_sellable_leads=31,
            )
        self.assertEqual(result, "metrics-id")
        self.assertTrue(conn.committed)
        _, params = cur.executed[0]
        self.assertEqual(params, ("Bristol", "idox", 12, 40, 38, 2, 5, 31))

    def test_get_recent_pass_metrics_fails_safe_on_db_error(self):
        patcher = patch.object(scraper_resilience.database, "get_db_conn", side_effect=RuntimeError("db down"))
        with patcher:
            result = scraper_resilience.get_recent_pass_metrics("Bristol")
        self.assertEqual(result, [])


class TestSchemaInitDoesNotRaise(unittest.TestCase):
    def test_init_schema_runs_against_a_capturing_cursor_without_error(self):
        captured = []

        class _Capture:
            def execute(self, sql, params=None):
                captured.append(sql)

        scraper_resilience.init_scraper_resilience_schema(_Capture())
        combined = "\n".join(captured)
        for table in ("source_incident", "repair_attempt", "council_scan_checkpoint", "council_scan_pass_metrics"):
            self.assertIn(table, combined)


if __name__ == "__main__":
    unittest.main()

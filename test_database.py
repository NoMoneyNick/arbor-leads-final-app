"""
test_database.py -- Unit tests for the pure logic in database.py that
doesn't need a real Postgres connection.

Run with:

    python -m unittest test_database.py -v

database.py imports psycopg2 (a real Postgres driver) at module load time.
Rather than requiring it installed just to test marketplace-filtering logic
that has nothing to do with the database connection itself, this file stubs
psycopg2 in sys.modules before importing database -- the same technique
test_scrapers.py already uses for database/notifications/dotenv. No real
network or DB call is made anywhere in this file.

Added Sep 2 2026, during the multi-vertical build: writing
test_hmo_lead_with_agent_is_not_excluded below is what caught a real bug
before it shipped further -- see _is_agent_already_handling_the_job's
Sep 2 2026 docstring in database.py for the full story. Short version: the
tree-specific "already has an agent, probably taken" exclusion rule was
being applied to every vertical, and since the tree-surgeon-name classifier
has no idea what an HMO conversion contractor's name looks like, it would
have silently excluded most real HMO leads with any agent on record from
the marketplace the moment that vertical started producing real leads.
"""
import importlib.util
import os
import sys
import types
import unittest
from unittest.mock import MagicMock, patch

if "psycopg2" not in sys.modules:
    sys.modules["psycopg2"] = types.ModuleType("psycopg2")

# Sep 2 2026: when this file runs in the same process as test_scrapers.py
# (e.g. `python -m unittest test_scrapers.py test_idox.py test_database.py`),
# test_scrapers.py registers its OWN fake `database` module under
# sys.modules["database"] first (a stub with only get_db_conn/
# increment_api_usage mocked, for its own unrelated tests), and scanners.py
# (imported by test_scrapers.py) binds its own internal `database` name to
# that fake object. A plain `import database` here would just reuse
# whatever is already cached -- and popping/reassigning
# sys.modules["database"] instead was tried and rejected: scanners.py keeps
# its OWN reference to the module object from when IT first imported
# "database", so swapping the sys.modules entry afterwards doesn't change
# what scanners.py actually calls, while unittest.mock.patch("database.x")
# resolves against whatever sys.modules["database"] is NOW -- the two end
# up patching different objects and tests silently stop asserting anything
# real (caught by running the combined suite, not by this file alone).
# Loading database.py fresh under a private module name -- never touching
# sys.modules["database"] at all -- sidesteps this entirely and is safe
# regardless of what other test files import first, in either order.
_DATABASE_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "database.py")
_spec = importlib.util.spec_from_file_location("_database_under_test", _DATABASE_PATH)
database = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(database)


class TestAgentAlreadyHandlingTheJob(unittest.TestCase):
    """_is_agent_already_handling_the_job -- the marketplace pre-purchase
    filter deciding whether a lead should be pulled from sale because the
    council record already names an agent apparently already doing the
    work. Wrong in one direction (excludes True) loses real revenue on a
    perfectly sellable lead; wrong the other way (excludes False) risks
    selling a job someone already has -- Nick's explicit red line."""

    # --- Tree vertical: must behave EXACTLY as before this refactor -------

    def test_tree_lead_with_confirmed_tree_agent_is_excluded(self):
        lead = {"vertical": "tree", "has_agent": True, "agent_is_tree_surgeon": True}
        self.assertTrue(database._is_agent_already_handling_the_job(lead))

    def test_tree_lead_with_confirmed_non_tree_agent_is_kept(self):
        lead = {"vertical": "tree", "has_agent": True, "agent_is_tree_surgeon": False}
        self.assertFalse(database._is_agent_already_handling_the_job(lead))

    def test_tree_lead_with_unconfirmed_agent_status_is_excluded(self):
        """The Aug 31 2026 rule: unknown (None) is treated the same as
        "confirmed tree surgeon" -- conservative by design, since almost
        the entire lead pool used to sit in this unconfirmed state."""
        lead = {"vertical": "tree", "has_agent": True, "agent_is_tree_surgeon": None}
        self.assertTrue(database._is_agent_already_handling_the_job(lead))

    def test_tree_lead_with_no_agent_at_all_is_kept(self):
        for has_agent in (False, None):
            with self.subTest(has_agent=has_agent):
                lead = {"vertical": "tree", "has_agent": has_agent, "agent_is_tree_surgeon": None}
                self.assertFalse(database._is_agent_already_handling_the_job(lead))

    def test_missing_vertical_key_defaults_to_tree_behaviour(self):
        """Rows written before the `vertical` column existed, or fetched
        without it for any reason, must keep the original tree-only
        behaviour -- matches the DB column's own DEFAULT 'tree'."""
        lead = {"has_agent": True, "agent_is_tree_surgeon": True}
        self.assertTrue(database._is_agent_already_handling_the_job(lead))

    # --- HMO (and any future non-tree) vertical: exempt from this check ---

    def test_hmo_lead_with_agent_is_not_excluded(self):
        """The bug this test caught: agent_is_tree_surgeon is a
        tree-surgeon-name classifier with no idea what an HMO conversion
        contractor's name looks like, so it returns None for almost every
        real HMO agent -- which the tree-only rule above treats as
        "excluded". Without the vertical scope, essentially every HMO lead
        with any agent on record would have silently never reached the
        marketplace."""
        lead = {"vertical": "hmo", "has_agent": True, "agent_is_tree_surgeon": None}
        self.assertFalse(database._is_agent_already_handling_the_job(lead))

    def test_hmo_lead_is_exempt_even_if_agent_is_tree_surgeon_happens_true(self):
        """Belt and braces: even in the unlikely case the classifier
        happens to return True for an HMO lead's agent text (e.g. it
        contains a coincidental tree-surgery-sounding word), the exclusion
        is still tree-vertical-only until a real HMO-specific classifier
        exists -- this is a deliberate, documented policy choice (favour
        never wrongly excluding a sellable lead), not an oversight."""
        lead = {"vertical": "hmo", "has_agent": True, "agent_is_tree_surgeon": True}
        self.assertFalse(database._is_agent_already_handling_the_job(lead))


class TestRunDdlStatementsResiliently(unittest.TestCase):
    """_run_ddl_statements_resiliently -- the Sep 2 2026 production incident
    fix. Real incident: the new `vertical` column's ALTER TABLE had to wait
    for a lock on the busy `leads` table, Postgres's statement_timeout
    eventually killed it, and because every resilience_cols statement used
    to share ONE transaction with a single commit at the end, that single
    failure rolled back everything else that had already succeeded in the
    same call -- and left `vertical` missing, which took lead capture AND
    the marketplace to zero (scanners._insert_lead lists the column
    unconditionally; get_marketplace_leads_with_freshness's SELECT does
    too). These tests use a fake connection/cursor -- no real Postgres --
    and stub out time.sleep so retry-with-backoff doesn't slow the suite."""

    def _make_conn(self, side_effects_by_substring, column_already_exists=False):
        """side_effects_by_substring: {sql_substring: [effect, effect, ...]}
        consumed in order per matching statement across attempts. An effect
        that's an Exception instance is raised; anything else is a no-op
        success. A substring with no entry always succeeds immediately.

        column_already_exists controls the information_schema precheck
        (Sep 2 2026 audit fix) added ahead of the retry loop: False (the
        default) makes every precheck report "not found," so every
        existing test here -- written before the precheck existed -- still
        exercises the real ALTER/retry path unchanged."""
        conn = MagicMock()
        call_counts = {}

        def make_cursor():
            cur = MagicMock()
            cur.fetchone.return_value = ("column_name",) if column_already_exists else None

            def execute(sql, *args, **kwargs):
                if sql.strip().upper().startswith("SET LOCAL"):
                    return
                for key, effects in side_effects_by_substring.items():
                    if key in sql:
                        idx = call_counts.get(key, 0)
                        call_counts[key] = idx + 1
                        effect = effects[min(idx, len(effects) - 1)]
                        if isinstance(effect, Exception):
                            raise effect
                        return
                return  # no configured effect -- plain success

            cur.execute.side_effect = execute
            return cur

        conn.cursor.side_effect = make_cursor
        return conn

    @patch("time.sleep", return_value=None)
    def test_statement_with_no_contention_lands_on_first_try(self, mock_sleep):
        stmt = "ALTER TABLE leads ADD COLUMN IF NOT EXISTS vertical TEXT DEFAULT 'tree';"
        conn = self._make_conn({})
        failed = database._run_ddl_statements_resiliently(conn, [stmt], "test-phase")
        self.assertEqual(failed, [])
        conn.commit.assert_called()
        mock_sleep.assert_not_called()

    @patch("time.sleep", return_value=None)
    def test_transient_lock_contention_is_retried_and_recovers(self, mock_sleep):
        """Mirrors the real incident's likely shape: a scan job's transaction
        briefly holds the lock, and it's gone a couple of retries later."""
        stmt = "ALTER TABLE leads ADD COLUMN IF NOT EXISTS vertical TEXT DEFAULT 'tree';"
        conn = self._make_conn({
            stmt: [
                Exception("canceling statement due to lock timeout"),
                Exception("canceling statement due to lock timeout"),
                None,
            ]
        })
        failed = database._run_ddl_statements_resiliently(conn, [stmt], "test-phase", max_attempts=5)
        self.assertEqual(failed, [])
        self.assertEqual(mock_sleep.call_count, 2)

    @patch("time.sleep", return_value=None)
    def test_one_persistently_blocked_statement_does_not_take_down_a_sibling(self, mock_sleep):
        """The actual bug being fixed: previously ALL of resilience_cols
        shared one transaction, so this contended statement rolling back
        would have undone the good one too. Proves the good one lands
        (and is committed) independently of the bad one's fate."""
        bad = "ALTER TABLE leads ADD COLUMN IF NOT EXISTS vertical TEXT DEFAULT 'tree';"
        good = "ALTER TABLE leads ADD COLUMN IF NOT EXISTS lead_score TEXT DEFAULT 'small';"
        conn = self._make_conn({
            bad: [Exception("canceling statement due to statement timeout")] * 10,
        })
        failed = database._run_ddl_statements_resiliently(conn, [bad, good], "test-phase", max_attempts=3)
        self.assertEqual(len(failed), 1)
        self.assertIn("vertical", failed[0][0])
        conn.commit.assert_called()  # the good statement's own commit happened

    @patch("time.sleep", return_value=None)
    def test_gives_up_after_max_attempts_and_names_the_failing_statement(self, mock_sleep):
        stmt = "ALTER TABLE leads ADD COLUMN IF NOT EXISTS vertical TEXT DEFAULT 'tree';"
        conn = self._make_conn({stmt: [Exception("canceling statement due to statement timeout")] * 10})
        failed = database._run_ddl_statements_resiliently(conn, [stmt], "test-phase", max_attempts=3)
        self.assertEqual(len(failed), 1)
        failed_stmt, failed_err = failed[0]
        self.assertIn("vertical", failed_stmt)
        self.assertIn("statement timeout", failed_err)
        self.assertEqual(mock_sleep.call_count, 2)  # retried between attempts 1-2 and 2-3, gave up after 3

    @patch("time.sleep", return_value=None)
    def test_precheck_skips_the_alter_entirely_when_column_already_exists(self, mock_sleep):
        """Sep 2 2026 audit fix: the real incident this same day showed
        every ADD-COLUMN statement against a busy table fail all 5 retries
        even though the columns already existed -- a CRITICAL alert fired
        for what was actually a no-op that just couldn't get its lock. The
        information_schema precheck needs no exclusive lock, so it should
        report the column present and skip the contention-prone ALTER
        (and therefore never sleep/retry) entirely."""
        stmt = "ALTER TABLE potential_partners ADD COLUMN IF NOT EXISTS tags TEXT[] DEFAULT '{}';"
        # If the precheck didn't skip it, this configured effect would make
        # every attempt fail -- proves the ALTER itself is never reached.
        conn = self._make_conn(
            {stmt: [Exception("canceling statement due to lock timeout")] * 10},
            column_already_exists=True,
        )
        failed = database._run_ddl_statements_resiliently(conn, [stmt], "test-phase", max_attempts=5)
        self.assertEqual(failed, [])
        mock_sleep.assert_not_called()

    @patch("time.sleep", return_value=None)
    def test_precheck_falls_through_to_real_alter_when_column_is_genuinely_missing(self, mock_sleep):
        """The other half of the fix: a genuinely new column (not found by
        the precheck) must still go through the real ALTER/retry path --
        the precheck is an optimization for the common case, not a way to
        skip real, needed migrations."""
        stmt = "ALTER TABLE potential_partners ADD COLUMN IF NOT EXISTS brand_new_col TEXT;"
        conn = self._make_conn({}, column_already_exists=False)
        failed = database._run_ddl_statements_resiliently(conn, [stmt], "test-phase")
        self.assertEqual(failed, [])
        conn.commit.assert_called()

    @patch("time.sleep", return_value=None)
    def test_precheck_error_falls_back_to_direct_alter_instead_of_crashing(self, mock_sleep):
        """A broken/unreadable information_schema precheck must never take
        the whole migration down with it -- fall back to attempting the
        real ALTER, same as if the precheck had reported 'not found'."""
        stmt = "ALTER TABLE potential_partners ADD COLUMN IF NOT EXISTS tags TEXT[] DEFAULT '{}';"
        conn = self._make_conn({"information_schema.columns": [Exception("precheck boom")]})
        failed = database._run_ddl_statements_resiliently(conn, [stmt], "test-phase")
        self.assertEqual(failed, [])
        conn.commit.assert_called()

    def _make_rls_conn(self, side_effects_by_substring, rowsecurity_already_on=False):
        """Same shape as _make_conn but the precheck cursor answers the RLS
        precheck's pg_tables.rowsecurity query instead of the ADD COLUMN
        precheck's information_schema.columns query."""
        conn = MagicMock()
        call_counts = {}

        def make_cursor():
            cur = MagicMock()
            cur.fetchone.return_value = (rowsecurity_already_on,)

            def execute(sql, *args, **kwargs):
                if sql.strip().upper().startswith("SET LOCAL"):
                    return
                for key, effects in side_effects_by_substring.items():
                    if key in sql:
                        idx = call_counts.get(key, 0)
                        call_counts[key] = idx + 1
                        effect = effects[min(idx, len(effects) - 1)]
                        if isinstance(effect, Exception):
                            raise effect
                        return
                return

            cur.execute.side_effect = execute
            return cur

        conn.cursor.side_effect = make_cursor
        return conn

    @patch("time.sleep", return_value=None)
    def test_rls_precheck_skips_the_alter_entirely_when_already_enabled(self, mock_sleep):
        """Sep 2 2026 fix, RLS half: mirrors the ADD COLUMN precheck -- a
        table that already has RLS on should never even attempt the
        lock-needing ALTER, so a busy table can't fail retries for a no-op."""
        stmt = "ALTER TABLE leads ENABLE ROW LEVEL SECURITY;"
        conn = self._make_rls_conn(
            {stmt: [Exception("canceling statement due to statement timeout")] * 10},
            rowsecurity_already_on=True,
        )
        failed = database._run_ddl_statements_resiliently(conn, [stmt], "Phase 2 (RLS)", max_attempts=5)
        self.assertEqual(failed, [])
        mock_sleep.assert_not_called()

    @patch("time.sleep", return_value=None)
    def test_rls_precheck_falls_through_to_real_alter_when_not_yet_enabled(self, mock_sleep):
        stmt = "ALTER TABLE leads ENABLE ROW LEVEL SECURITY;"
        conn = self._make_rls_conn({}, rowsecurity_already_on=False)
        failed = database._run_ddl_statements_resiliently(conn, [stmt], "Phase 2 (RLS)")
        self.assertEqual(failed, [])
        conn.commit.assert_called()

    @patch("time.sleep", return_value=None)
    def test_one_persistently_blocked_rls_statement_does_not_take_down_a_sibling(self, mock_sleep):
        """The actual live incident this fixes: Phase 2 used to run all 13
        ENABLE ROW LEVEL SECURITY statements in one shared transaction, so
        one table's lock timeout rolled back all 13. Proves a good sibling
        statement still lands independently of a persistently blocked one."""
        bad = "ALTER TABLE leads ENABLE ROW LEVEL SECURITY;"
        good = "ALTER TABLE payments ENABLE ROW LEVEL SECURITY;"
        conn = self._make_rls_conn({
            bad: [Exception("canceling statement due to statement timeout")] * 10,
        }, rowsecurity_already_on=False)
        failed = database._run_ddl_statements_resiliently(conn, [bad, good], "Phase 2 (RLS)", max_attempts=3)
        self.assertEqual(len(failed), 1)
        self.assertIn("leads", failed[0][0])
        conn.commit.assert_called()  # the good statement's own commit happened

    @patch("time.sleep", return_value=None)
    def test_rls_precheck_error_falls_back_to_direct_alter_instead_of_crashing(self, mock_sleep):
        stmt = "ALTER TABLE leads ENABLE ROW LEVEL SECURITY;"
        conn = self._make_rls_conn({"pg_tables": [Exception("precheck boom")]})
        failed = database._run_ddl_statements_resiliently(conn, [stmt], "Phase 2 (RLS)")
        self.assertEqual(failed, [])
        conn.commit.assert_called()


class TestMarketplaceVerticalColumnFallback(unittest.TestCase):
    """get_marketplace_leads_with_freshness -- Sep 2 2026 production
    incident: its SELECT lists COALESCE(vertical, 'tree'), and when that
    column's migration hasn't landed yet, the whole query used to raise
    "column vertical does not exist" straight into the function's outer
    except-return-[] -- i.e. the ENTIRE public marketplace shows zero leads
    for every customer, not just HMO ones. Proves the fallback SELECT (no
    vertical column, defaulted to 'tree' in Python) keeps the marketplace
    working exactly as it did before that column existed."""

    def _make_conn_where_vertical_select_fails_then_legacy_succeeds(self, legacy_rows):
        conn = MagicMock()
        cur = MagicMock()

        def execute(sql, *args, **kwargs):
            if "vertical" in sql:
                raise Exception('column "vertical" does not exist')
            # legacy SELECT (no vertical column) succeeds
            cur.fetchall.return_value = legacy_rows

        cur.execute.side_effect = execute
        conn.cursor.return_value = cur
        return conn, cur

    def test_falls_back_to_legacy_select_and_defaults_vertical_to_tree(self):
        import datetime
        row = (
            "some-uuid", "23/07777/TPO", "3 Oak Ave, Leeds",
            "Felling of 1no. diseased oak tree", "Leeds", "small", 19,
            datetime.datetime.now(datetime.timezone.utc), None, None,
            "council_planning", None, None,
        )
        conn, cur = self._make_conn_where_vertical_select_fails_then_legacy_succeeds([row])
        with patch.object(database, "get_db_conn", return_value=conn), \
             patch.object(database, "SURL", "postgres://fake-for-test"):
            leads = database.get_marketplace_leads_with_freshness()
        self.assertEqual(len(leads), 1)
        self.assertEqual(leads[0]["vertical"], "tree")
        self.assertEqual(cur.execute.call_count, 2)
        conn.rollback.assert_called_once()

    def test_unrelated_select_error_is_not_swallowed_by_the_vertical_fallback(self):
        conn = MagicMock()
        cur = MagicMock()
        cur.execute.side_effect = Exception("connection to server was lost")
        conn.cursor.return_value = cur
        with patch.object(database, "get_db_conn", return_value=conn), \
             patch.object(database, "SURL", "postgres://fake-for-test"):
            # The outer try/except in get_marketplace_leads_with_freshness
            # catches everything and returns [] -- this just proves an
            # unrelated error still takes that path (i.e. is NOT retried as
            # if it were the vertical-column issue) rather than raising or
            # silently returning fabricated data.
            leads = database.get_marketplace_leads_with_freshness()
        self.assertEqual(leads, [])
        self.assertEqual(cur.execute.call_count, 1)  # no fallback retry attempted


class TestReviewQueueFunctions(unittest.TestCase):
    """Sep 2 2026, master_expansion_plan_v2.md build-order step 4, Tier 4:
    the manual review queue's DB layer. Before this existed, every scan
    call site's `if vertical is None: continue` discarded an application
    that matched neither Tier 1 nor Tier 2 completely and permanently, with
    no trace anywhere -- these functions are what makes it "visible, never
    silently dropped" instead, per the plan's own wording."""

    def _conn_with_cursor(self, fetchone_return=None, fetchall_return=None):
        conn = MagicMock()
        cur = MagicMock()
        cur.fetchone.return_value = fetchone_return
        cur.fetchall.return_value = fetchall_return or []
        conn.cursor.return_value = cur
        return conn, cur

    def test_insert_unclassified_application_returns_true_on_genuine_insert(self):
        conn, cur = self._conn_with_cursor(fetchone_return=("some-uuid",))
        with patch.object(database, "get_db_conn", return_value=conn), \
             patch.object(database, "SURL", "postgres://fake-for-test"):
            result = database.insert_unclassified_application(
                "23/07777/TPO", "3 Oak Ave, Leeds", "T1 - Cherry - Reduce height by 4m.", "Leeds", app_type="Trees"
            )
        self.assertTrue(result)

    def test_insert_unclassified_application_returns_false_on_duplicate(self):
        """ON CONFLICT (reference) DO NOTHING -- a still-open application
        reappearing in tomorrow's scan of the same source must not re-queue
        (or reset the review state of) an already-queued row."""
        conn, cur = self._conn_with_cursor(fetchone_return=None)  # DO NOTHING -> no RETURNING row
        with patch.object(database, "get_db_conn", return_value=conn), \
             patch.object(database, "SURL", "postgres://fake-for-test"):
            result = database.insert_unclassified_application(
                "23/07777/TPO", "3 Oak Ave, Leeds", "T1 - Cherry - Reduce height by 4m.", "Leeds"
            )
        self.assertFalse(result)

    def test_get_pending_review_queue_without_attempt_filter_for_human_visibility(self):
        """The default (max_llm_attempts=None) must return every pending row
        regardless of attempt count -- this is what main.py's /review-queue
        endpoint uses, and Nick must be able to see a genuinely stuck item,
        not just fresh ones."""
        row = ("id1", "23/07777/TPO", "3 Oak Ave, Leeds", "T1 - Cherry - Reduce height by 4m.", "Leeds", "Trees", 5, "2026-09-02T00:00:00Z")
        conn, cur = self._conn_with_cursor(fetchall_return=[row])
        with patch.object(database, "get_db_conn", return_value=conn), \
             patch.object(database, "SURL", "postgres://fake-for-test"):
            items = database.get_pending_review_queue(limit=100)
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["llm_attempts"], 5)
        sql = cur.execute.call_args[0][0]
        self.assertNotIn("llm_attempts <", sql)

    def test_get_pending_review_queue_with_attempt_filter_for_tier_3(self):
        """process_review_queue_with_llm's own fetch passes max_llm_attempts
        so a stuck item stops being retried -- proves the WHERE clause
        actually changes shape when it's supplied."""
        conn, cur = self._conn_with_cursor(fetchall_return=[])
        with patch.object(database, "get_db_conn", return_value=conn), \
             patch.object(database, "SURL", "postgres://fake-for-test"):
            database.get_pending_review_queue(limit=25, max_llm_attempts=2)
        sql, params = cur.execute.call_args[0]
        self.assertIn("llm_attempts <", sql)
        self.assertIn(2, params)

    def test_increment_review_queue_llm_attempts(self):
        conn, cur = self._conn_with_cursor(fetchone_return=("some-uuid",))
        with patch.object(database, "get_db_conn", return_value=conn), \
             patch.object(database, "SURL", "postgres://fake-for-test"):
            result = database.increment_review_queue_llm_attempts("23/07777/TPO")
        self.assertTrue(result)
        conn.commit.assert_called_once()

    def test_resolve_unclassified_application_sets_llm_classified_status(self):
        conn, cur = self._conn_with_cursor(fetchone_return=("some-uuid",))
        with patch.object(database, "get_db_conn", return_value=conn), \
             patch.object(database, "SURL", "postgres://fake-for-test"):
            result = database.resolve_unclassified_application("23/07777/TPO", "tree")
        self.assertTrue(result)
        sql, params = cur.execute.call_args[0]
        self.assertIn("llm_classified", sql)
        self.assertIn("tree", params)

    def test_resolve_unclassified_application_requires_a_vertical(self):
        """Guards against accidentally closing out a queue row with no
        actual classification -- that would silently drop it just as surely
        as the old bare `continue` did."""
        with patch.object(database, "SURL", "postgres://fake-for-test"):
            self.assertFalse(database.resolve_unclassified_application("23/07777/TPO", None))
            self.assertFalse(database.resolve_unclassified_application("23/07777/TPO", ""))


class TestLeadTagQuerying(unittest.TestCase):
    """Sep 2 2026: the query/backfill side of the lead tagging system --
    see scanners.py's TestLeadTagging for the tag-generation side."""

    def _conn_with_cursor(self, fetchone_return=None, fetchall_return=None):
        conn = MagicMock()
        cur = MagicMock()
        cur.fetchone.return_value = fetchone_return
        cur.fetchall.return_value = fetchall_return or []
        conn.cursor.return_value = cur
        return conn, cur

    def test_get_leads_by_tags_returns_empty_with_no_tags_or_no_db(self):
        with patch.object(database, "SURL", "postgres://fake-for-test"):
            self.assertEqual(database.get_leads_by_tags([]), [])
        with patch.object(database, "SURL", ""):
            self.assertEqual(database.get_leads_by_tags(["locale:bromley"]), [])

    def test_get_leads_by_tags_match_all_uses_contains_operator(self):
        conn, cur = self._conn_with_cursor(fetchall_return=[])
        with patch.object(database, "get_db_conn", return_value=conn), \
             patch.object(database, "SURL", "postgres://fake-for-test"):
            database.get_leads_by_tags(["locale:bromley", "job:crown-work"], match_all=True)
        sql, params = cur.execute.call_args[0]
        self.assertIn("@>", sql)
        self.assertNotIn("&&", sql)
        self.assertEqual(params[0], ["locale:bromley", "job:crown-work"])

    def test_get_leads_by_tags_match_any_uses_overlap_operator(self):
        conn, cur = self._conn_with_cursor(fetchall_return=[])
        with patch.object(database, "get_db_conn", return_value=conn), \
             patch.object(database, "SURL", "postgres://fake-for-test"):
            database.get_leads_by_tags(["region:london", "region:scotland"], match_all=False)
        sql, params = cur.execute.call_args[0]
        self.assertIn("&&", sql)

    def test_get_leads_by_tags_maps_rows_to_dicts(self):
        row = ("id1", "23/07777/TPO", "3 Oak Ave, Bromley", "Crown reduction of oak tree.",
                "large", 75, "BROMLEY", "tree", False,
                ["vertical:tree", "locale:bromley", "region:london"], "2026-09-02T00:00:00Z", "new")
        conn, cur = self._conn_with_cursor(fetchall_return=[row])
        with patch.object(database, "get_db_conn", return_value=conn), \
             patch.object(database, "SURL", "postgres://fake-for-test"):
            results = database.get_leads_by_tags(["locale:bromley"])
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["reference"], "23/07777/TPO")
        self.assertIn("region:london", results[0]["tags"])

    def test_backfill_lead_tags_only_touches_untagged_rows(self):
        """The SELECT must filter to tags IS NULL OR tags = '{}' -- a lead
        already tagged should never be silently recomputed/overwritten by a
        routine backfill re-run."""
        conn, cur = self._conn_with_cursor(fetchall_return=[])
        with patch.object(database, "get_db_conn", return_value=conn), \
             patch.object(database, "SURL", "postgres://fake-for-test"):
            result = database.backfill_lead_tags(batch_size=10)
        sql = cur.execute.call_args[0][0]
        self.assertIn("tags IS NULL OR tags = '{}'", sql)
        self.assertEqual(result["updated"], 0)

    def test_backfill_lead_tags_computes_and_writes_tags_per_row(self):
        """backfill_lead_tags only imports the real `scanners` module when
        there's actually a row to process (see its Sep 2 2026 comment) --
        that import is faked out here with sys.modules so this test proves
        backfill_lead_tags' OWN logic (SELECT filter, per-row call, UPDATE,
        commit) in isolation, without depending on scanners.py's full
        dependency chain (psycopg2/dotenv/etc, not all stubbed in this
        file) being importable in whatever process runs this file alone.
        scanners._generate_tags' own real behaviour is covered separately
        by test_scrapers.py's TestLeadTagging."""
        select_row = ("lead-id-1", "3 Oak Ave, Bromley", "Fell a tree in rear garden.",
                       "BROMLEY", "tree", "small", None)
        conn = MagicMock()
        cur = MagicMock()
        cur.fetchall.return_value = [select_row]
        conn.cursor.return_value = cur
        fake_scanners = types.ModuleType("scanners")
        fake_scanners._generate_tags = MagicMock(
            return_value=["vertical:tree", "locale:bromley", "region:london", "job:felling"]
        )
        with patch.object(database, "get_db_conn", return_value=conn), \
             patch.object(database, "SURL", "postgres://fake-for-test"), \
             patch.dict(sys.modules, {"scanners": fake_scanners}):
            result = database.backfill_lead_tags(batch_size=10)
        self.assertEqual(result["updated"], 1)
        self.assertEqual(result["errors"], 0)
        fake_scanners._generate_tags.assert_called_once_with(
            "3 Oak Ave, Bromley", "Fell a tree in rear garden.", "BROMLEY", "tree", "small", None
        )
        update_calls = [c for c in cur.execute.call_args_list if "UPDATE leads SET tags" in c[0][0]]
        self.assertEqual(len(update_calls), 1)
        written_tags = update_calls[0][0][1][0]
        self.assertIn("vertical:tree", written_tags)
        self.assertIn("locale:bromley", written_tags)
        self.assertTrue(any(t.startswith("job:") for t in written_tags))

    def test_resync_region_tags_only_touches_region_unclassified_rows(self):
        """Sep 2 2026: the correction pass for leads stuck at
        region:unclassified before region resolution became postcode-based
        (see scanners._resolve_region). The SELECT must filter to that
        exact tag -- a lead with a real region already resolved must never
        be touched by this pass."""
        conn, cur = self._conn_with_cursor(fetchall_return=[])
        with patch.object(database, "get_db_conn", return_value=conn), \
             patch.object(database, "SURL", "postgres://fake-for-test"):
            result = database.resync_region_tags(batch_size=10)
        sql = cur.execute.call_args[0][0]
        self.assertIn("region:unclassified", sql)
        self.assertEqual(result["updated"], 0)

    def test_resync_region_tags_recomputes_and_swaps_only_the_region_tag(self):
        """Every other tag on the row must survive untouched -- only the
        stale region:unclassified entry gets removed and replaced."""
        select_row = ("lead-id-1", "14 Corporation St, Birmingham B2 4LP", "London",
                       ["vertical:tree", "locale:birmingham", "region:unclassified", "size:large"])
        conn = MagicMock()
        cur = MagicMock()
        cur.fetchall.return_value = [select_row]
        conn.cursor.return_value = cur
        fake_scanners = types.ModuleType("scanners")
        fake_scanners._resolve_region = MagicMock(return_value="West Midlands")
        fake_scanners._slugify_tag = MagicMock(return_value="west-midlands")
        with patch.object(database, "get_db_conn", return_value=conn), \
             patch.object(database, "SURL", "postgres://fake-for-test"), \
             patch.dict(sys.modules, {"scanners": fake_scanners}):
            result = database.resync_region_tags(batch_size=10)
        self.assertEqual(result["updated"], 1)
        self.assertEqual(result["errors"], 0)
        fake_scanners._resolve_region.assert_called_once_with(
            "14 Corporation St, Birmingham B2 4LP", "London"
        )
        update_calls = [c for c in cur.execute.call_args_list if "UPDATE leads SET tags" in c[0][0]]
        self.assertEqual(len(update_calls), 1)
        written_tags = update_calls[0][0][1][0]
        self.assertIn("region:west-midlands", written_tags)
        self.assertNotIn("region:unclassified", written_tags)
        self.assertIn("vertical:tree", written_tags)
        self.assertIn("locale:birmingham", written_tags)
        self.assertIn("size:large", written_tags)

    def test_resync_region_tags_leaves_row_unchanged_if_still_unclassified(self):
        """If postcode lookup and the council table both still fail, the
        row must not be pointlessly UPDATEd -- counted as 'unchanged', not
        'updated', and no UPDATE statement issued for it."""
        select_row = ("lead-id-1", "no postcode here", "SOME COUNCIL NOT IN THE LIST",
                       ["vertical:tree", "region:unclassified"])
        conn = MagicMock()
        cur = MagicMock()
        cur.fetchall.return_value = [select_row]
        conn.cursor.return_value = cur
        fake_scanners = types.ModuleType("scanners")
        fake_scanners._resolve_region = MagicMock(return_value="unclassified")
        fake_scanners._slugify_tag = MagicMock(return_value="unclassified")
        with patch.object(database, "get_db_conn", return_value=conn), \
             patch.object(database, "SURL", "postgres://fake-for-test"), \
             patch.dict(sys.modules, {"scanners": fake_scanners}):
            result = database.resync_region_tags(batch_size=10)
        self.assertEqual(result["updated"], 0)
        self.assertEqual(result["unchanged"], 1)
        update_calls = [c for c in cur.execute.call_args_list if "UPDATE leads SET tags" in c[0][0]]
        self.assertEqual(len(update_calls), 0)


class TestPartnerTagQuerying(unittest.TestCase):
    """Sep 2 2026: the backfill/report side of the partner tagging system --
    see test_research.py for the tag-generation side (business kind,
    phone/email sanity checks, contact:reachable vs contact:dead)."""

    def _conn_with_cursor(self, fetchone_return=None, fetchall_return=None):
        conn = MagicMock()
        cur = MagicMock()
        cur.fetchone.return_value = fetchone_return
        cur.fetchall.return_value = fetchall_return or []
        conn.cursor.return_value = cur
        return conn, cur

    def test_backfill_partner_tags_only_touches_untagged_rows(self):
        conn, cur = self._conn_with_cursor(fetchall_return=[])
        with patch.object(database, "get_db_conn", return_value=conn), \
             patch.object(database, "SURL", "postgres://fake-for-test"):
            result = database.backfill_partner_tags(batch_size=10)
        sql = cur.execute.call_args[0][0]
        self.assertIn("tags IS NULL OR tags = '{}'", sql)
        self.assertEqual(result["updated"], 0)

    def test_backfill_partner_tags_computes_and_writes_tags_per_row(self):
        """Mirrors test_backfill_lead_tags_computes_and_writes_tags_per_row
        -- fakes out `research` via sys.modules so this proves
        backfill_partner_tags' own logic (SELECT filter, per-row call,
        UPDATE, commit) without needing research.py's full dependency chain
        importable in this process."""
        select_row = ("partner-id-1", ["81300"], "Jane Smith", "020 7946 0958", "jane@realtreecompany.co.uk", "Acme Landscaping Ltd")
        conn = MagicMock()
        cur = MagicMock()
        cur.fetchall.return_value = [select_row]
        conn.cursor.return_value = cur
        fake_research = types.ModuleType("research")
        fake_research._generate_partner_tags = MagicMock(
            return_value=["business:landscaping-grounds-maintenance", "director:yes",
                          "phone:yes", "email:yes", "contact:reachable"]
        )
        with patch.object(database, "get_db_conn", return_value=conn), \
             patch.object(database, "SURL", "postgres://fake-for-test"), \
             patch.dict(sys.modules, {"research": fake_research}):
            result = database.backfill_partner_tags(batch_size=10)
        self.assertEqual(result["updated"], 1)
        self.assertEqual(result["errors"], 0)
        fake_research._generate_partner_tags.assert_called_once_with(
            ["81300"], "Jane Smith", "020 7946 0958", "jane@realtreecompany.co.uk", company_name="Acme Landscaping Ltd"
        )
        update_calls = [c for c in cur.execute.call_args_list if "UPDATE potential_partners SET tags" in c[0][0]]
        self.assertEqual(len(update_calls), 1)
        written_tags = update_calls[0][0][1][0]
        self.assertIn("contact:reachable", written_tags)

    def test_get_partner_tag_counts_groups_by_prefix(self):
        conn, cur = self._conn_with_cursor()
        cur.fetchone.side_effect = [(1994,), (91,)]
        cur.fetchall.return_value = [
            ("business:landscaping-grounds-maintenance", 800),
            ("contact:dead", 300),
            ("contact:reachable", 1694),
            ("director:yes", 950),
        ]
        with patch.object(database, "get_db_conn", return_value=conn), \
             patch.object(database, "SURL", "postgres://fake-for-test"):
            result = database.get_partner_tag_counts()
        self.assertEqual(result["total_partners"], 1994)
        self.assertEqual(result["untagged_partners"], 91)
        self.assertEqual(result["categories"]["contact"]["contact:dead"], 300)
        self.assertEqual(result["categories"]["business"]["business:landscaping-grounds-maintenance"], 800)

    def test_get_partner_tag_counts_returns_error_with_no_db(self):
        with patch.object(database, "SURL", ""):
            result = database.get_partner_tag_counts()
        self.assertIn("error", result)

    def test_resync_all_partner_tags_recomputes_every_row_not_just_untagged(self):
        """Unlike backfill_partner_tags, this must touch a row that
        already has tags -- added for the director-name-quality audit fix
        (corporate/nominee CH officers no longer count as director:yes),
        which only new lookups pick up on their own."""
        rows = [
            ("p1", ["81300"], "Acme Trustees Limited", "020 7946 0958", None, ["business:landscaping-grounds-maintenance", "director:yes", "phone:yes", "email:no", "contact:reachable"], "Acme Trustees Limited"),
            ("p2", ["81300"], "Jane Smith", "020 7946 0958", None, ["business:landscaping-grounds-maintenance", "director:yes", "phone:yes", "email:no", "contact:reachable"], "Some Landscaping Ltd"),
        ]
        conn = MagicMock()
        cur = MagicMock()
        cur.fetchall.return_value = rows
        conn.cursor.return_value = cur
        fake_research = types.ModuleType("research")

        def _fake_generate(sic_codes, md_name, phone, email, company_name=None):
            is_person = md_name == "Jane Smith"
            return ["business:landscaping-grounds-maintenance",
                    "director:yes" if is_person else "director:no",
                    "phone:yes", "email:no", "contact:reachable"]
        fake_research._generate_partner_tags = _fake_generate
        with patch.object(database, "get_db_conn", return_value=conn), \
             patch.object(database, "SURL", "postgres://fake-for-test"), \
             patch.dict(sys.modules, {"research": fake_research}):
            result = database.resync_all_partner_tags(commit_every=500)
        self.assertEqual(result["updated"], 1)   # p1's tags changed (director:yes -> director:no)
        self.assertEqual(result["unchanged"], 1)  # p2's tags stayed the same
        self.assertEqual(result["errors"], 0)
        update_calls = [c for c in cur.execute.call_args_list if "UPDATE potential_partners SET tags" in c[0][0]]
        self.assertEqual(len(update_calls), 1)
        self.assertEqual(update_calls[0][0][1][1], "p1")

    def test_resync_all_partner_tags_returns_error_with_no_db(self):
        with patch.object(database, "SURL", ""):
            result = database.resync_all_partner_tags()
        self.assertIn("error", result)

    def test_requeue_dead_contact_enrichment_issues_correct_update(self):
        """Only rows with zero contact info found (phone AND email both
        NULL) should be re-queued -- partners that already have a phone or
        email must be left alone since they don't need re-checking."""
        conn = MagicMock()
        cur = MagicMock()
        cur.rowcount = 7
        conn.cursor.return_value = cur
        with patch.object(database, "get_db_conn", return_value=conn), \
             patch.object(database, "SURL", "postgres://fake-for-test"):
            result = database.requeue_dead_contact_enrichment()
        self.assertEqual(result["requeued"], 7)
        self.assertNotIn("error", result)
        update_sql = cur.execute.call_args_list[0][0][0]
        self.assertIn("SET enriched_at = NULL", update_sql)
        self.assertIn("phone_number IS NULL", update_sql)
        self.assertIn("email IS NULL", update_sql)
        conn.commit.assert_called_once()

    def test_requeue_dead_contact_enrichment_returns_error_with_no_db(self):
        with patch.object(database, "SURL", ""):
            result = database.requeue_dead_contact_enrichment()
        self.assertIn("error", result)

    def test_resync_all_lead_tags_recomputes_every_row_not_just_untagged(self):
        """Same 'full recompute, not just untagged rows' pattern as
        resync_all_partner_tags -- added for the agent_type/agent_guess tag
        split, which only NEW lookups pick up on their own."""
        rows = [
            ("l1", "3 Oak Ave, Bromley", "Fell a tree", "BROMLEY", "tree", "small", True, None,
             ["vertical:tree", "agent:yes", "region:london"]),
            ("l2", "9 Ivy Rd, Leeds", "Crown reduction", "LEEDS", "tree", "small", False, None,
             ["vertical:tree", "agent:no", "agent_type:none", "region:leeds"]),
        ]
        conn = MagicMock()
        cur = MagicMock()
        cur.fetchall.return_value = rows
        conn.cursor.return_value = cur
        fake_scanners = types.ModuleType("scanners")

        def _fake_generate(address, summary, council_source, vertical, lead_score, has_agent, agent_is_tree_surgeon=None):
            if has_agent is True:
                return ["vertical:tree", "agent:yes", "agent_type:type-unconfirmed", "region:london"]
            return ["vertical:tree", "agent:no", "agent_type:none", "region:leeds"]
        fake_scanners._generate_tags = _fake_generate
        with patch.object(database, "get_db_conn", return_value=conn), \
             patch.object(database, "SURL", "postgres://fake-for-test"), \
             patch.dict(sys.modules, {"scanners": fake_scanners}):
            result = database.resync_all_lead_tags(commit_every=500)
        self.assertEqual(result["updated"], 1)    # l1 gained agent_type:type-unconfirmed
        self.assertEqual(result["unchanged"], 1)  # l2 already matches
        self.assertEqual(result["errors"], 0)
        update_calls = [c for c in cur.execute.call_args_list if "UPDATE leads SET tags" in c[0][0]]
        self.assertEqual(len(update_calls), 1)
        self.assertEqual(update_calls[0][0][1][1], "l1")

    def test_resync_all_lead_tags_returns_error_with_no_db(self):
        with patch.object(database, "SURL", ""):
            result = database.resync_all_lead_tags()
        self.assertIn("error", result)


class TestLeadSourceHealthReport(unittest.TestCase):
    """Sep 3 2026: get_lead_source_health_report() -- the failsafe built
    after the Leeds ArcGIS incident, which never raised a single error
    while silently producing zero real leads. See the function's own
    docstring for the full reasoning; these tests prove the flagging logic
    itself (a source with a real history that suddenly goes to zero gets
    flagged; a naturally-quiet source does not; a brand-new source with no
    history does not)."""

    def _conn_with_rows(self, rows):
        conn = MagicMock()
        cur = MagicMock()
        cur.fetchall.return_value = rows
        conn.cursor.return_value = cur
        return conn, cur

    def test_source_with_real_history_and_zero_recent_is_flagged(self):
        """Leeds' own shape: used to produce leads steadily, then zero in
        the recent window, with no error anywhere in the pipeline."""
        conn, cur = self._conn_with_rows([("Leeds", 54, 0)])  # 54 over the 27-day baseline window, 0 recently
        with patch.object(database, "get_db_conn", return_value=conn), \
             patch.object(database, "SURL", "postgres://fake-for-test"):
            report = database.get_lead_source_health_report()
        self.assertEqual(len(report), 1)
        self.assertEqual(report[0]["council_source"], "Leeds")
        self.assertTrue(report[0]["possible_silent_failure"])
        self.assertEqual(report[0]["recent_leads"], 0)

    def test_naturally_quiet_source_is_not_flagged(self):
        """A genuinely low-volume small council (a handful of leads across
        the whole month) going quiet for 3 days is normal, not a failure --
        must not be flagged just for having a low baseline rate."""
        conn, cur = self._conn_with_rows([("Tiny Rural Council", 1, 0)])  # ~0.037/day baseline
        with patch.object(database, "get_db_conn", return_value=conn), \
             patch.object(database, "SURL", "postgres://fake-for-test"):
            report = database.get_lead_source_health_report()
        self.assertFalse(report[0]["possible_silent_failure"])

    def test_source_still_producing_leads_is_not_flagged(self):
        conn, cur = self._conn_with_rows([("London", 90, 5)])
        with patch.object(database, "get_db_conn", return_value=conn), \
             patch.object(database, "SURL", "postgres://fake-for-test"):
            report = database.get_lead_source_health_report()
        self.assertFalse(report[0]["possible_silent_failure"])

    def test_no_database_returns_empty_list_not_an_error(self):
        with patch.object(database, "SURL", ""):
            report = database.get_lead_source_health_report()
        self.assertEqual(report, [])

    def test_db_error_returns_empty_list_not_a_crash(self):
        with patch.object(database, "get_db_conn", side_effect=Exception("connection refused")), \
             patch.object(database, "SURL", "postgres://fake-for-test"):
            report = database.get_lead_source_health_report()
        self.assertEqual(report, [])


class TestSystemWarningsLogging(unittest.TestCase):
    """Sep 4 2026: backs the WARNING-severity half of notifications.
    send_system_incident_alert() -- see that function's own Sep 4 comment.
    Nick's own words: "if the warning emails are mostly ignored i will
    never know when to pay attention" -- a WARNING now gets logged here
    instead of emailed immediately, and only escalates to a real email
    once the same category+title has recurred on 3+ distinct calendar days
    within a rolling week. These tests lock in the three DB functions that
    logic depends on, independent of the email-dispatch decision itself
    (which is covered in test_notifications.py)."""

    def _conn_with_execute(self):
        conn = MagicMock()
        cur = MagicMock()
        conn.cursor.return_value = cur
        return conn, cur

    def test_log_system_warning_inserts_and_returns_true(self):
        conn, cur = self._conn_with_execute()
        with patch.object(database, "get_db_conn", return_value=conn), \
             patch.object(database, "SURL", "postgres://fake-for-test"):
            ok = database.log_system_warning("SCRAPER TLS FALLBACK", "example.gov.uk required fallback", "some detail")
        self.assertTrue(ok)
        cur.execute.assert_called_once()
        args = cur.execute.call_args[0]
        self.assertIn("INSERT INTO system_warnings", args[0])
        self.assertEqual(args[1], ("SCRAPER TLS FALLBACK", "example.gov.uk required fallback", "some detail"))
        conn.commit.assert_called_once()

    def test_log_system_warning_no_database_returns_false(self):
        with patch.object(database, "SURL", ""):
            ok = database.log_system_warning("CAT", "TITLE")
        self.assertFalse(ok)

    def test_log_system_warning_db_error_returns_false_not_a_crash(self):
        with patch.object(database, "get_db_conn", side_effect=Exception("connection refused")), \
             patch.object(database, "SURL", "postgres://fake-for-test"):
            ok = database.log_system_warning("CAT", "TITLE")
        self.assertFalse(ok)

    def test_get_warning_recurrence_days_returns_count(self):
        conn = MagicMock()
        cur = MagicMock()
        cur.fetchone.return_value = (3,)
        conn.cursor.return_value = cur
        with patch.object(database, "get_db_conn", return_value=conn), \
             patch.object(database, "SURL", "postgres://fake-for-test"):
            days = database.get_warning_recurrence_days("CAT", "TITLE", window_days=7)
        self.assertEqual(days, 3)

    def test_get_warning_recurrence_days_no_rows_returns_zero(self):
        conn = MagicMock()
        cur = MagicMock()
        cur.fetchone.return_value = None
        conn.cursor.return_value = cur
        with patch.object(database, "get_db_conn", return_value=conn), \
             patch.object(database, "SURL", "postgres://fake-for-test"):
            days = database.get_warning_recurrence_days("CAT", "TITLE")
        self.assertEqual(days, 0)

    def test_get_warning_recurrence_days_no_database_returns_zero(self):
        with patch.object(database, "SURL", ""):
            days = database.get_warning_recurrence_days("CAT", "TITLE")
        self.assertEqual(days, 0)

    def test_get_warning_recurrence_days_db_error_returns_zero_not_a_crash(self):
        with patch.object(database, "get_db_conn", side_effect=Exception("boom")), \
             patch.object(database, "SURL", "postgres://fake-for-test"):
            days = database.get_warning_recurrence_days("CAT", "TITLE")
        self.assertEqual(days, 0)

    def test_get_recent_warnings_returns_dicts(self):
        conn = MagicMock()
        cur = MagicMock()
        cur.fetchall.return_value = [("SCRAPER TLS FALLBACK", "x.gov.uk required fallback", "detail", "2026-09-04T10:00:00Z")]
        conn.cursor.return_value = cur
        with patch.object(database, "get_db_conn", return_value=conn), \
             patch.object(database, "SURL", "postgres://fake-for-test"):
            warnings = database.get_recent_warnings(hours=24)
        self.assertEqual(len(warnings), 1)
        self.assertEqual(warnings[0]["category"], "SCRAPER TLS FALLBACK")
        self.assertEqual(warnings[0]["title"], "x.gov.uk required fallback")

    def test_get_recent_warnings_no_database_returns_empty_list(self):
        with patch.object(database, "SURL", ""):
            warnings = database.get_recent_warnings()
        self.assertEqual(warnings, [])

    def test_get_recent_warnings_db_error_returns_empty_list_not_a_crash(self):
        with patch.object(database, "get_db_conn", side_effect=Exception("boom")), \
             patch.object(database, "SURL", "postgres://fake-for-test"):
            warnings = database.get_recent_warnings()
        self.assertEqual(warnings, [])


if __name__ == "__main__":
    unittest.main()

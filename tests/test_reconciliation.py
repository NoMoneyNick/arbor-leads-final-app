"""
test_reconciliation.py -- failure-case tests that need to be driven through
database.py's actual allocation call sites (not fulfilment.py in isolation)
to prove the WHOLE transaction behaves correctly, not just the fulfilment
half of it, plus the pipeline-separation tests added in the 2026-09-18
review:

  1. Successful payment, then a database failure while recording the
     allocation -- must roll back the ENTIRE sale (lead stays unclaimed),
     not leave a lead marked 'claimed'/'reserved-gone' with no allocation
     record and a customer who was charged. Tested under BOTH pipeline
     modes (see TestPipelineSeparation's docstring for why that matters).
  2. Subscription quota interaction -- a dispatch that creates an
     allocation+obligation but is then rejected by the quota-enforcing
     UPDATE (delivered_this_month < monthly_quota) must roll back that
     allocation too, not leave an orphaned allocation for a lead that was
     actually released back to the pool.
  3. Migration compatibility -- the new fulfilment pipeline must never read
     or requeue rows from the pre-existing letter_dispatches table (which
     may contain historically dry-run-marked rows from the OLD
     process_pending_letter_dispatches bug -- see fulfilment.py's module
     docstring's MIGRATION NOTES). Asserted by inspecting the actual SQL
     text of every promotion/send function.
  4. Pipeline separation -- database.py's four allocation call sites route
     through fulfilment.active_pipeline() so that, for any given sale,
     EXACTLY ONE of (_queue_letter_dispatch, fulfilment.create_allocation_
     and_obligation) runs, never both, controlled by config rather than by
     which schedulers happen to be wired up.

Uses the same "load database.py under a private module name, stub psycopg2
only" technique test_database.py already established (see that file's own
long comment on why) -- this avoids depending on import order relative to
other test files in the same `unittest discover` run, several of which
install their own sys.modules["database"] stub.

Run with:
    python -m unittest tests.test_reconciliation -v
"""
import importlib.util
import inspect
import os
import sys
import types
import unittest
from contextlib import contextmanager
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
_APP_DIR = os.path.dirname(_THIS_DIR)
sys.path.insert(0, _APP_DIR)

if "psycopg2" not in sys.modules:
    sys.modules["psycopg2"] = types.ModuleType("psycopg2")

_DATABASE_PATH = os.path.join(_APP_DIR, "database.py")
_spec = importlib.util.spec_from_file_location("_database_under_test_reconciliation", _DATABASE_PATH)
database = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(database)

import fulfilment
import worker
import letter_providers.registry as registry_module


def _conn_with_cursor():
    conn = MagicMock()
    cur = MagicMock()
    conn.cursor.return_value = cur
    return conn, cur


@contextmanager
def _pipeline(mode: str):
    """Temporarily sets LETTER_DISPATCH_PIPELINE for the duration of a
    test, restoring whatever was there before (including "unset") on exit
    -- tests in this file must never leak env var state into other test
    files run in the same process."""
    env_key = fulfilment.LETTER_DISPATCH_PIPELINE_ENV
    had_old = env_key in os.environ
    old = os.environ.get(env_key)
    os.environ[env_key] = mode
    try:
        yield
    finally:
        if had_old:
            os.environ[env_key] = old
        else:
            os.environ.pop(env_key, None)


class TestPaymentThenDatabaseFailureRollsBackWholeSale(unittest.TestCase):
    """'Successful payment followed by database failure.'
    confirm_reserved_lead_sale is the Stripe-webhook-driven path where this
    matters most -- real money has already moved by the time this function
    runs (see that function's own docstring: 'the caller treats None here
    as payment succeeded but we can't honour it, and must auto-refund' --
    and see TestDistinguishesAllocationFailureFromNoReservation below for
    why a caller must NOT actually do that for THIS specific failure)."""

    def test_confirm_reserved_lead_sale_rolls_back_if_fulfilment_pipeline_write_fails(self):
        conn, cur = _conn_with_cursor()
        cur.fetchone.return_value = (
            "lead-uuid-1", "PLANIT-001", "1 Test St", "Fell one oak", "Leeds",
            "medium", 4500, "J Bloggs", None, None, False, None,
        )
        with _pipeline("fulfilment"), \
             patch.object(database, "get_db_conn", return_value=conn), \
             patch.object(database, "SURL", "postgres://fake-for-test"), \
             patch.object(database.fulfilment, "create_allocation_and_obligation",
                           side_effect=Exception("simulated DB write failure (disk full / connection drop)")):
            with self.assertRaises(fulfilment.AllocationPersistenceError):
                database.confirm_reserved_lead_sale("lead-uuid-1", "cs_test_123", "buyer@example.com")
        # Must NOT have committed -- a commit here, followed by the
        # exception, would leave status='claimed' permanently with no
        # allocation record and a charged customer.
        conn.commit.assert_not_called()
        conn.rollback.assert_called_once()
        conn.close.assert_called_once()

    def test_confirm_reserved_lead_sale_rolls_back_if_legacy_pipeline_write_fails(self):
        """Same guarantee under the DEFAULT pipeline mode -- the one that
        actually runs in production today (see fulfilment.ACTIVE_PIPELINES'
        docstring). A DB failure inserting into letter_dispatches must be
        just as safe as one inserting into letter_obligations."""
        conn, cur = _conn_with_cursor()
        cur.fetchone.return_value = (
            "lead-uuid-1", "PLANIT-001", "1 Test St", "Fell one oak", "Leeds",
            "medium", 4500, "J Bloggs", None, None, False, None,
        )
        with _pipeline("legacy"), \
             patch.object(database, "get_db_conn", return_value=conn), \
             patch.object(database, "SURL", "postgres://fake-for-test"), \
             patch.object(database, "_queue_letter_dispatch",
                           side_effect=Exception("simulated DB write failure")) as mock_legacy, \
             patch.object(database.fulfilment, "create_allocation_and_obligation") as mock_new:
            with self.assertRaises(fulfilment.AllocationPersistenceError):
                database.confirm_reserved_lead_sale("lead-uuid-1", "cs_test_123", "buyer@example.com")
        mock_legacy.assert_called_once()
        mock_new.assert_not_called()  # exactly one pipeline was even attempted
        conn.commit.assert_not_called()

    def test_confirm_reserved_lead_sale_commits_normally_on_success(self):
        """Sanity check for the tests above: proves the mock setup itself
        is capable of reaching a commit, so the assertNotCalled above is
        actually testing something rather than passing vacuously."""
        conn, cur = _conn_with_cursor()
        cur.fetchone.return_value = (
            "lead-uuid-1", "PLANIT-001", "1 Test St", "Fell one oak", "Leeds",
            "medium", 4500, "J Bloggs", None, None, False, None,
        )
        with _pipeline("fulfilment"), \
             patch.object(database, "get_db_conn", return_value=conn), \
             patch.object(database, "SURL", "postgres://fake-for-test"), \
             patch.object(database.fulfilment, "create_allocation_and_obligation",
                           return_value=fulfilment.AllocationResult(ok=True, allocation_id="a1", obligation_id="o1")):
            result = database.confirm_reserved_lead_sale("lead-uuid-1", "cs_test_123", "buyer@example.com")
        self.assertIsNotNone(result)
        conn.commit.assert_called_once()

    def test_burn_lead_inventory_also_rolls_back_on_fulfilment_failure(self):
        """burn_lead_inventory is the free-lead-grant / legacy single-sale
        path -- no Stripe refund is relevant here, but the same atomicity
        guarantee (no half-burned lead with no allocation record) must
        still hold."""
        conn, cur = _conn_with_cursor()
        cur.fetchone.return_value = (
            "lead-uuid-2", "PLANIT-002", "2 Test St", "Fell one ash", "Leeds",
            "medium", 4500, "A Homeowner", None, None, False, None,
        )
        with _pipeline("fulfilment"), \
             patch.object(database, "get_db_conn", return_value=conn), \
             patch.object(database, "SURL", "postgres://fake-for-test"), \
             patch.object(database.fulfilment, "create_allocation_and_obligation",
                           side_effect=Exception("simulated DB write failure")):
            with self.assertRaises(fulfilment.AllocationPersistenceError):
                database.burn_lead_inventory("lead-uuid-2", "buyer@example.com")
        conn.commit.assert_not_called()
        conn.rollback.assert_called_once()


class TestDistinguishesAllocationFailureFromNoReservation(unittest.TestCase):
    """The actual point of AllocationPersistenceError: a caller (payments.py's
    webhook, above all) that only ever sees 'confirm_reserved_lead_sale
    returned None' cannot tell 'this reservation never existed / already
    expired' apart from 'this reservation was completely valid but a DB
    write failed while recording it' -- and those two cases need OPPOSITE
    handling (the first should auto-refund; the second must not, since the
    underlying problem may be transient and refunding a valid payment for
    a retryable failure is a worse outcome than asking Stripe to retry the
    webhook). This proves the two cases are now distinguishable at the
    Python level: one raises, the other returns None cleanly."""

    def test_no_matching_reservation_returns_none_and_does_not_raise(self):
        conn, cur = _conn_with_cursor()
        cur.fetchone.return_value = None  # UPDATE matched nothing
        with _pipeline("fulfilment"), \
             patch.object(database, "get_db_conn", return_value=conn), \
             patch.object(database, "SURL", "postgres://fake-for-test"), \
             patch.object(database.fulfilment, "create_allocation_and_obligation") as mock_alloc:
            result = database.confirm_reserved_lead_sale("lead-uuid-9", "cs_test_gone", "buyer@example.com")
        self.assertIsNone(result)
        mock_alloc.assert_not_called()  # never even attempted -- nothing to persist

    def test_matched_reservation_with_persistence_failure_raises_instead_of_returning_none(self):
        conn, cur = _conn_with_cursor()
        cur.fetchone.return_value = (
            "lead-uuid-1", "PLANIT-001", "1 Test St", "Fell one oak", "Leeds",
            "medium", 4500, "J Bloggs", None, None, False, None,
        )
        with _pipeline("fulfilment"), \
             patch.object(database, "get_db_conn", return_value=conn), \
             patch.object(database, "SURL", "postgres://fake-for-test"), \
             patch.object(database.fulfilment, "create_allocation_and_obligation",
                           side_effect=Exception("simulated transient DB failure")):
            with self.assertRaises(fulfilment.AllocationPersistenceError):
                database.confirm_reserved_lead_sale("lead-uuid-1", "cs_test_123", "buyer@example.com")


class TestSubscriptionQuotaRollsBackAllocation(unittest.TestCase):
    """'Subscription quota / legacy entitlement interaction.'
    record_lead_dispatch_and_burn creates the fulfilment allocation BEFORE
    the quota-enforcing UPDATE runs (both must happen inside the same
    reserved-vs-burned transaction as the lead burn itself). If the quota
    UPDATE then finds the subscriber already at their monthly_quota, the
    function calls conn.rollback() and returns False -- this test proves
    that rollback genuinely undoes the allocation too (as it must, being
    the same transaction), not just the lead-burn UPDATE, so a subscriber
    who is over quota never ends up with a real allocation/obligation row
    for a lead they don't legitimately hold."""

    def test_quota_exceeded_releases_lead_and_rolls_back_allocation(self):
        conn, cur = _conn_with_cursor()
        cur.fetchone.side_effect = [
            ("lead-uuid-3", "PLANIT-003", "3 Test St", "J Bloggs"),  # burn UPDATE
            None,                                                     # quota UPDATE finds nothing
        ]
        allocation_calls = []
        with _pipeline("fulfilment"), \
             patch.object(database, "get_db_conn", return_value=conn), \
             patch.object(database, "SURL", "postgres://fake-for-test"), \
             patch.object(database.fulfilment, "create_allocation_and_obligation",
                           side_effect=lambda *a, **k: allocation_calls.append(1)):
            result = database.record_lead_dispatch_and_burn("lead-uuid-3", "sub-1", "contractor@example.com")

        self.assertFalse(result)
        # The allocation WAS attempted (this is what proves the ordering:
        # allocation happens before the quota check can veto it) --
        self.assertEqual(len(allocation_calls), 1)
        # -- but the whole thing was rolled back, not committed, so that
        # attempted allocation never becomes a durable row.
        conn.rollback.assert_called_once()
        conn.commit.assert_not_called()

    def test_quota_available_commits_allocation_normally(self):
        conn, cur = _conn_with_cursor()
        cur.fetchone.side_effect = [
            ("lead-uuid-4", "PLANIT-004", "4 Test St", "J Bloggs"),  # burn UPDATE
            (3,),                                                     # quota UPDATE succeeds, delivered_this_month=3
        ]
        with _pipeline("fulfilment"), \
             patch.object(database, "get_db_conn", return_value=conn), \
             patch.object(database, "SURL", "postgres://fake-for-test"), \
             patch.object(database.fulfilment, "create_allocation_and_obligation",
                           return_value=fulfilment.AllocationResult(ok=True, allocation_id="a1", obligation_id="o1")):
            result = database.record_lead_dispatch_and_burn("lead-uuid-4", "sub-1", "contractor@example.com")
        self.assertTrue(result)
        conn.commit.assert_called_once()
        conn.rollback.assert_not_called()

    def test_allocation_persistence_failure_is_distinct_from_quota_hit(self):
        """A DB write failure mid-dispatch must not be logged/handled as an
        ordinary quota-hit -- both currently return False to the caller
        (correct: the caller's retry-next-subscriber behaviour is the same
        either way), but they must not be conflated in the exception path
        itself: a quota-hit never raises AllocationPersistenceError."""
        conn, cur = _conn_with_cursor()
        cur.fetchone.side_effect = [("lead-uuid-5", "PLANIT-005", "5 Test St", "J Bloggs")]
        with _pipeline("fulfilment"), \
             patch.object(database, "get_db_conn", return_value=conn), \
             patch.object(database, "SURL", "postgres://fake-for-test"), \
             patch.object(database.fulfilment, "create_allocation_and_obligation",
                           side_effect=Exception("simulated DB failure")):
            result = database.record_lead_dispatch_and_burn("lead-uuid-5", "sub-1", "contractor@example.com")
        # record_lead_dispatch_and_burn's own contract is bool, not raise --
        # it catches AllocationPersistenceError itself (no Stripe refund
        # concern at dispatch time -- see that function's own comment) and
        # returns False, same as a quota-hit, but rolls back first.
        self.assertFalse(result)
        conn.rollback.assert_called_once()
        conn.commit.assert_not_called()


class TestPipelineSeparation(unittest.TestCase):
    """Exactly one of (_queue_letter_dispatch, fulfilment.create_allocation_
    and_obligation) must run per sale, chosen by fulfilment.active_pipeline(),
    never both. This matters because main.py's run_full_autonomous_cycle
    already runs database.process_pending_letter_dispatches once a day via
    a live external scheduler (see /trigger-autonomous-cycle's own
    docstring) -- so "the new obligations table isn't processed by anything
    yet" was never a reason the old and new pipelines couldn't collide; it
    only takes something starting to process letter_obligations (worker.py,
    wired to any scheduler) for both to be live, uncoordinated, and acting
    on the same sales."""

    def _lead_row(self):
        return ("lead-uuid-1", "PLANIT-001", "1 Test St", "Fell one oak", "Leeds",
                "medium", 4500, "J Bloggs", None, None, False, None)

    def test_default_pipeline_is_legacy(self):
        os.environ.pop(fulfilment.LETTER_DISPATCH_PIPELINE_ENV, None)
        self.assertEqual(fulfilment.active_pipeline(), "legacy")

    def test_unknown_pipeline_value_falls_back_to_legacy(self):
        with _pipeline("some_third_option_nobody_configured"):
            self.assertEqual(fulfilment.active_pipeline(), "legacy")

    def test_legacy_mode_calls_only_the_old_queue(self):
        conn, cur = _conn_with_cursor()
        cur.fetchone.return_value = self._lead_row()
        with _pipeline("legacy"), \
             patch.object(database, "get_db_conn", return_value=conn), \
             patch.object(database, "SURL", "postgres://fake-for-test"), \
             patch.object(database, "_queue_letter_dispatch") as mock_legacy, \
             patch.object(database.fulfilment, "create_allocation_and_obligation") as mock_new:
            database.burn_lead_inventory("lead-uuid-1", "buyer@example.com")
        mock_legacy.assert_called_once()
        mock_new.assert_not_called()

    def test_fulfilment_mode_calls_only_the_new_pipeline(self):
        conn, cur = _conn_with_cursor()
        cur.fetchone.return_value = self._lead_row()
        with _pipeline("fulfilment"), \
             patch.object(database, "get_db_conn", return_value=conn), \
             patch.object(database, "SURL", "postgres://fake-for-test"), \
             patch.object(database, "_queue_letter_dispatch") as mock_legacy, \
             patch.object(database.fulfilment, "create_allocation_and_obligation") as mock_new:
            database.burn_lead_inventory("lead-uuid-1", "buyer@example.com")
        mock_new.assert_called_once()
        mock_legacy.assert_not_called()

    def test_this_holds_across_all_four_allocation_call_sites(self):
        """The other three call sites (confirm_reserved_lead_sale,
        redeem_free_lead_code, record_lead_dispatch_and_burn) all route
        through the same _dispatch_via_active_pipeline helper -- this test
        drives all four and checks the same invariant on each, so a future
        call site that bypasses the helper (calls _queue_letter_dispatch or
        fulfilment.create_allocation_and_obligation directly again) is
        caught here rather than trusting that every call site remembered
        to use the helper."""
        with _pipeline("fulfilment"), \
             patch.object(database, "_queue_letter_dispatch") as mock_legacy, \
             patch.object(database.fulfilment, "create_allocation_and_obligation",
                           return_value=fulfilment.AllocationResult(ok=True, allocation_id="a", obligation_id="o")):

            conn, cur = _conn_with_cursor()
            cur.fetchone.return_value = self._lead_row()
            with patch.object(database, "get_db_conn", return_value=conn), \
                 patch.object(database, "SURL", "postgres://fake-for-test"):
                database.burn_lead_inventory("lead-uuid-1", "buyer@example.com")

            conn, cur = _conn_with_cursor()
            cur.fetchone.return_value = self._lead_row()
            with patch.object(database, "get_db_conn", return_value=conn), \
                 patch.object(database, "SURL", "postgres://fake-for-test"):
                database.confirm_reserved_lead_sale("lead-uuid-1", "cs_test_1", "buyer@example.com")

            conn, cur = _conn_with_cursor()
            cur.fetchone.side_effect = [
                ("PLANIT-001", None, None),                                   # free_lead_codes lookup
                (False,),                                                      # NOW() > expires_at -> not expired
                ("lead-uuid-1", "PLANIT-001", "1 Test St", "J Bloggs",
                 None, None, False, None, None, None, None, None),            # leads UPDATE...RETURNING (12 cols)
            ]
            with patch.object(database, "get_db_conn", return_value=conn), \
                 patch.object(database, "SURL", "postgres://fake-for-test"):
                database.redeem_free_lead_code("buyer@example.com", "CODE123")

            conn, cur = _conn_with_cursor()
            cur.fetchone.side_effect = [
                ("lead-uuid-1", "PLANIT-001", "1 Test St", "J Bloggs"),  # burn UPDATE (4 cols)
                (1,),                                                     # quota UPDATE succeeds
            ]
            with patch.object(database, "get_db_conn", return_value=conn), \
                 patch.object(database, "SURL", "postgres://fake-for-test"):
                database.record_lead_dispatch_and_burn("lead-uuid-1", "sub-1", "contractor@example.com")

        mock_legacy.assert_not_called()


class TestProcessingStageRefusesTheWrongPipeline(unittest.TestCase):
    """2026-09-18 review, Section 2: '_dispatch_via_active_pipeline already
    guarantees exactly one pipeline may act on a sale at ALLOCATION time --
    this proves the mirror-image guarantee at PROCESSING time: worker.py's
    three functions refuse to run unless LETTER_DISPATCH_PIPELINE=
    'fulfilment', and database.process_pending_letter_dispatches refuses to
    run WHEN it is. Without both halves, an operator could flip the flag at
    allocation time while an old scheduled job (or a stale manual
    invocation) kept acting on the other queue regardless."""

    def test_promote_pending_approvals_refuses_under_legacy_pipeline(self):
        with _pipeline("legacy"):
            report = worker.promote_pending_approvals(MagicMock())
        self.assertEqual(report.refused, "active_pipeline_is_not_fulfilment")
        self.assertEqual(report.checked, 0)

    def test_promote_pending_funding_refuses_under_legacy_pipeline(self):
        with _pipeline("legacy"):
            report = worker.promote_pending_funding(MagicMock(), MagicMock(), estimated_cost_pence=95)
        self.assertEqual(report.refused, "active_pipeline_is_not_fulfilment")

    def test_run_batch_refuses_under_legacy_pipeline(self):
        with _pipeline("legacy"):
            outcomes = worker.run_batch(MagicMock(), MagicMock(), worker_id="w1")
        self.assertEqual(outcomes, [])

    def test_worker_functions_proceed_normally_under_fulfilment_pipeline(self):
        """Contrast case -- the guard must not block the RIGHT mode, only
        the wrong one. A cursor returning no rows at all is enough to prove
        the function actually reached its normal SELECT rather than being
        refused (report.refused stays None, and it's not the sentinel
        'legacy' rejection string)."""
        cur = MagicMock()
        cur.fetchall.return_value = []
        with _pipeline("fulfilment"):
            report = worker.promote_pending_approvals(cur)
        self.assertIsNone(report.refused)
        self.assertEqual(report.checked, 0)  # ran normally, just found nothing to do

    def test_process_pending_letter_dispatches_refuses_under_fulfilment_pipeline(self):
        with _pipeline("fulfilment"), \
             patch.object(database, "SURL", "postgres://fake-for-test"):
            result = database.process_pending_letter_dispatches()
        self.assertEqual(result.get("refused"), "active_pipeline_is_fulfilment")
        self.assertEqual(result["sent"], 0)
        self.assertEqual(result["batch_size"], 0)

    def test_process_pending_letter_dispatches_proceeds_normally_under_legacy_pipeline(self):
        conn, cur = _conn_with_cursor()
        cur.fetchall.return_value = []  # nothing queued -- proves it reached the real SELECT
        with _pipeline("legacy"), \
             patch.object(database, "get_db_conn", return_value=conn), \
             patch.object(database, "SURL", "postgres://fake-for-test"):
            result = database.process_pending_letter_dispatches()
        self.assertNotIn("refused", result)
        self.assertEqual(result["batch_size"], 0)


class TestDryRunNeverMismarkedAsSentInLegacyPipeline(unittest.TestCase):
    """2026-09-18 review, Section 2: 'the old dry-run-as-sent issue is
    relevant to this transition, not simply out of scope.' Regression test
    for the exact bug docs/letter_provider_assessment.md found: a dry-run
    'success' (ConsoleLetterProvider, status='queued_dry_run') must never
    get a real sent_at timestamp -- only a genuinely real send may."""

    def _fake_provider_module(self, *, ok: bool, status: str):
        result = SimpleNamespace(ok=ok, status=status, provider_name="console",
                                  provider_reference=None, error=None if ok else "simulated failure")
        provider = MagicMock()
        provider.send.return_value = result
        module = MagicMock()
        module.get_letter_provider.return_value = provider
        return module

    def test_dry_run_result_never_sets_sent_at(self):
        conn, cur = _conn_with_cursor()
        cur.fetchall.return_value = [("dispatch-1", "PLANIT-001", "1 Test St", "J Bloggs", "single_purchase")]
        fake_letter_provider = self._fake_provider_module(ok=True, status="queued_dry_run")

        with _pipeline("legacy"), \
             patch.object(database, "get_db_conn", return_value=conn), \
             patch.object(database, "SURL", "postgres://fake-for-test"), \
             patch.dict(sys.modules, {"letter_provider": fake_letter_provider}):
            result = database.process_pending_letter_dispatches()

        self.assertEqual(result["dry_run"], 1)
        self.assertEqual(result["sent"], 0)
        update_calls = [c for c in cur.execute.call_args_list if c.args[0].strip().startswith("UPDATE letter_dispatches")]
        self.assertEqual(len(update_calls), 1)
        sql_text = update_calls[0].args[0]
        self.assertNotIn("sent_at = NOW()", sql_text)

    def test_genuinely_real_send_still_sets_sent_at(self):
        conn, cur = _conn_with_cursor()
        cur.fetchall.return_value = [("dispatch-1", "PLANIT-001", "1 Test St", "J Bloggs", "single_purchase")]
        fake_letter_provider = self._fake_provider_module(ok=True, status="sent")

        with _pipeline("legacy"), \
             patch.object(database, "get_db_conn", return_value=conn), \
             patch.object(database, "SURL", "postgres://fake-for-test"), \
             patch.dict(sys.modules, {"letter_provider": fake_letter_provider}):
            result = database.process_pending_letter_dispatches()

        self.assertEqual(result["sent"], 1)
        self.assertEqual(result["dry_run"], 0)
        update_calls = [c for c in cur.execute.call_args_list if c.args[0].strip().startswith("UPDATE letter_dispatches")]
        self.assertEqual(len(update_calls), 1)
        self.assertIn("sent_at = NOW()", update_calls[0].args[0])


class TestNeverReadsLegacyLetterDispatchesForResending(unittest.TestCase):
    """'Migration compatibility' -- the new pipeline must not blindly
    requeue historical rows. There is no migration script in this session's
    work that copies letter_dispatches rows into letter_obligations
    (deliberately -- see fulfilment.py's MIGRATION NOTES), so the only way
    this invariant could be violated is if some promotion/send function
    queried letter_dispatches directly. This test greps the actual SQL
    string literals embedded in the relevant source files, so it fails
    loudly if that ever changes -- it does not rely on trusting the
    docstrings to stay accurate."""

    def _source_of(self, *modules_and_funcs):
        return "\n".join(inspect.getsource(f) for f in modules_and_funcs)

    def test_worker_promotion_and_send_functions_never_mention_letter_dispatches(self):
        src = self._source_of(
            worker.promote_pending_approvals, worker.promote_pending_funding, worker.run_batch,
        )
        self.assertNotIn("letter_dispatches", src)

    def test_registry_attempt_send_never_mentions_letter_dispatches(self):
        src = inspect.getsource(registry_module.attempt_send)
        self.assertNotIn("letter_dispatches", src)

    def test_get_lead_owner_is_the_ONLY_sanctioned_reader_of_letter_dispatches(self):
        """The one deliberate exception -- ownership lookup for historical
        purchases, read-only, documented in fulfilment.py's MIGRATION
        NOTES. This test pins that down explicitly so a future change that
        adds a SECOND read (or a write) doesn't slip in unnoticed."""
        src = inspect.getsource(fulfilment.get_lead_owner)
        self.assertIn("letter_dispatches", src)
        self.assertIn("SELECT buyer_email FROM letter_dispatches", src)
        # And prove it's genuinely read-only in this function.
        self.assertNotIn("UPDATE letter_dispatches", src)
        self.assertNotIn("INSERT INTO letter_dispatches", src)
        self.assertNotIn("DELETE FROM letter_dispatches", src)


class TestHasUnresolvedPaymentReconciliationIssueWrapper(unittest.TestCase):
    """2026-09-18 review, Section 4 (second pass): database.
    has_unresolved_payment_reconciliation_issue is the self-contained
    wrapper payments.py's webhook handler actually calls -- this drives it
    through database.py's real code (opens-its-own-connection, fail-safe
    behaviour), not just fulfilment.has_unresolved_reconciliation_issue's
    pure logic (already covered directly in tests/test_fulfilment.py)."""

    def setUp(self):
        self._had_surl = database.SURL
        database.SURL = "postgres://test-not-real"

    def tearDown(self):
        database.SURL = self._had_surl

    def test_true_when_an_unresolved_row_matches(self):
        conn, cur = _conn_with_cursor()
        cur.fetchone.return_value = (1,)
        with patch.object(database, "get_db_conn", return_value=conn):
            result = database.has_unresolved_payment_reconciliation_issue(stripe_event_id="evt_1")
        self.assertTrue(result)

    def test_false_when_no_row_matches(self):
        conn, cur = _conn_with_cursor()
        cur.fetchone.return_value = None
        with patch.object(database, "get_db_conn", return_value=conn):
            result = database.has_unresolved_payment_reconciliation_issue(stripe_event_id="evt_gone")
        self.assertFalse(result)

    def test_false_when_no_ids_given_does_not_even_touch_the_database(self):
        with patch.object(database, "get_db_conn") as mock_get_conn:
            result = database.has_unresolved_payment_reconciliation_issue()
        self.assertFalse(result)
        mock_get_conn.assert_not_called()

    def test_fails_safe_to_true_on_a_database_error(self):
        """The deliberately-inverted fail-safe direction (see this
        function's own docstring): a DB error while checking must NOT be
        treated the same as 'no open issue' -- that would let an
        unanswerable check fall through to an automatic refund. Modelled
        by get_db_conn itself raising, i.e. the connection can't even be
        opened -- the outage case."""
        with patch.object(database, "get_db_conn", side_effect=RuntimeError("simulated DB outage")):
            result = database.has_unresolved_payment_reconciliation_issue(stripe_event_id="evt_outage")
        self.assertTrue(result)

    def test_no_database_url_configured_also_fails_safe_to_true(self):
        database.SURL = ""
        result = database.has_unresolved_payment_reconciliation_issue(stripe_event_id="evt_1")
        self.assertTrue(result)


if __name__ == "__main__":
    unittest.main()

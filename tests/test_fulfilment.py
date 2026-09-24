"""
test_fulfilment.py -- fulfilment.py's allocation/obligation core, with a
mocked psycopg2-style cursor (no real Postgres). Run with:

    python -m unittest tests.test_fulfilment -v
"""
import os
import sys
import unittest
from unittest.mock import MagicMock, call

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import fulfilment


class FakeCursor:
    """A tiny fake cursor good enough for fulfilment.py's SQL shapes:
    tracks every execute() call and lets a test script canned fetchone()
    return values in order. Much simpler (and more transparent about what
    it's asserting) than trying to simulate real SQL semantics."""

    def __init__(self, fetchone_results=None):
        self.executed = []
        self._fetchone_results = list(fetchone_results or [])

    def execute(self, sql, params=None):
        self.executed.append((sql.strip(), params))

    def fetchone(self):
        if not self._fetchone_results:
            return None
        return self._fetchone_results.pop(0)


class TestMakeIdempotencyKey(unittest.TestCase):
    def test_same_inputs_produce_same_key(self):
        k1 = fulfilment.make_idempotency_key("single_purchase", "PLANIT-001", "cs_test_123")
        k2 = fulfilment.make_idempotency_key("single_purchase", "PLANIT-001", "cs_test_123")
        self.assertEqual(k1, k2)

    def test_different_inputs_produce_different_keys(self):
        k1 = fulfilment.make_idempotency_key("single_purchase", "PLANIT-001", "cs_test_123")
        k2 = fulfilment.make_idempotency_key("single_purchase", "PLANIT-002", "cs_test_123")
        self.assertNotEqual(k1, k2)

    def test_case_and_whitespace_insensitive(self):
        k1 = fulfilment.make_idempotency_key("single_purchase", "PLANIT-001", "cs_test_123")
        k2 = fulfilment.make_idempotency_key(" Single_Purchase ", " planit-001 ", " CS_TEST_123 ")
        self.assertEqual(k1, k2)


class TestGetLeadOwner(unittest.TestCase):
    def test_authoritative_table_used_first(self):
        cur = FakeCursor(fetchone_results=[("buyer@example.com",)])
        owner = fulfilment.get_lead_owner(cur, "PLANIT-001")
        self.assertEqual(owner, "buyer@example.com")
        self.assertIn("lead_allocations", cur.executed[0][0])

    def test_falls_back_to_letter_dispatches_for_historical_leads(self):
        # lead_allocations has nothing (first fetchone -> None), so the
        # function must fall back to letter_dispatches (second query).
        cur = FakeCursor(fetchone_results=[None, ("historical-buyer@example.com",)])
        owner = fulfilment.get_lead_owner(cur, "PLANIT-OLD-001")
        self.assertEqual(owner, "historical-buyer@example.com")
        self.assertEqual(len(cur.executed), 2)
        self.assertIn("letter_dispatches", cur.executed[1][0])

    def test_returns_none_when_nothing_found_anywhere(self):
        cur = FakeCursor(fetchone_results=[None, None])
        owner = fulfilment.get_lead_owner(cur, "PLANIT-NEVER-SOLD")
        self.assertIsNone(owner)

    def test_email_is_normalised_lowercase(self):
        cur = FakeCursor(fetchone_results=[("Buyer@Example.COM",)])
        owner = fulfilment.get_lead_owner(cur, "PLANIT-001")
        self.assertEqual(owner, "buyer@example.com")


class TestCreateAllocationAndObligation(unittest.TestCase):
    def test_happy_path_creates_allocation_and_obligation(self):
        cur = FakeCursor(fetchone_results=[
            None,                       # idempotency lookup: nothing yet
            ("alloc-uuid-1",),          # INSERT lead_allocations RETURNING id
            ("obligation-uuid-1",),     # INSERT letter_obligations RETURNING id
        ])
        result = fulfilment.create_allocation_and_obligation(
            cur, lead_reference="PLANIT-001", lead_id="lead-uuid-1", address="1 Test St",
            applicant_name="J Bloggs", buyer_email="Buyer@Example.com",
            allocation_type="single_purchase", sale_context="single_purchase",
            source_payment_ref="cs_test_1",
        )
        self.assertTrue(result.ok)
        self.assertEqual(result.allocation_id, "alloc-uuid-1")
        self.assertEqual(result.obligation_id, "obligation-uuid-1")
        self.assertEqual(result.obligation_status, "pending_approval")

    def test_missing_address_produces_blocked_status_not_silent_skip(self):
        """Section 2/4: 'missing addresses ... must produce an actionable
        blocked/error state, not an apparently completed sale with only a
        log message.' This is the exact behaviour that differs from the
        old _queue_letter_dispatch, which only logged and returned nothing
        durable."""
        cur = FakeCursor(fetchone_results=[None, ("alloc-uuid-2",), ("obligation-uuid-2",)])
        result = fulfilment.create_allocation_and_obligation(
            cur, lead_reference="PLANIT-002", lead_id="lead-uuid-2", address="",
            applicant_name=None, buyer_email="buyer@example.com",
            allocation_type="single_purchase", sale_context="single_purchase",
            source_payment_ref="cs_test_2",
        )
        self.assertTrue(result.ok)
        self.assertEqual(result.obligation_status, "blocked_missing_data")
        # And it's a REAL, queryable row -- assert the INSERT actually
        # wrote 'blocked_missing_data' as the status value.
        obligation_insert = [e for e in cur.executed if "INSERT INTO letter_obligations" in e[0]][0]
        self.assertIn("blocked_missing_data", obligation_insert[1])

    def test_repeated_call_with_same_identifying_facts_does_not_duplicate(self):
        """Section 2: 'handle repeat and out-of-order Stripe events without
        duplicate allocations, letters or quota consumption.'"""
        cur = FakeCursor(fetchone_results=[
            ("existing-alloc-uuid",),          # idempotency lookup: FOUND on retry
            ("existing-obligation-uuid", "ready"),  # its existing obligation
        ])
        result = fulfilment.create_allocation_and_obligation(
            cur, lead_reference="PLANIT-003", lead_id="lead-uuid-3", address="1 Test St",
            applicant_name=None, buyer_email="buyer@example.com",
            allocation_type="single_purchase", sale_context="single_purchase",
            source_payment_ref="cs_test_3",
        )
        self.assertTrue(result.ok)
        self.assertEqual(result.allocation_id, "existing-alloc-uuid")
        self.assertEqual(result.reason, "already_allocated")
        # Only the two SELECTs -- no INSERT statements at all on a replay.
        inserts = [e for e in cur.executed if e[0].upper().startswith("INSERT")]
        self.assertEqual(inserts, [])

    def test_template_approved_flag_produces_pending_funding_not_pending_approval(self):
        cur = FakeCursor(fetchone_results=[None, ("alloc-uuid-4",), ("obligation-uuid-4",)])
        result = fulfilment.create_allocation_and_obligation(
            cur, lead_reference="PLANIT-004", lead_id="lead-uuid-4", address="1 Test St",
            applicant_name=None, buyer_email="buyer@example.com",
            allocation_type="single_purchase", sale_context="single_purchase",
            source_payment_ref="cs_test_4", template_approved=True,
        )
        self.assertEqual(result.obligation_status, "pending_funding")

    def test_insert_race_onto_same_idempotency_key_returns_existing_not_error(self):
        """2026-09-22 handoff, database-level allocation atomicity: two
        concurrent callers can both pass the pre-check SELECT (nothing
        found yet) and both attempt the INSERT -- ON CONFLICT DO NOTHING
        means only one wins the row; the loser must recover gracefully by
        re-reading, not raise. When the row that won shares THIS call's
        own idempotency_key (a genuine same-event race, e.g. a retried
        webhook arriving concurrently with itself), that's still
        'already_allocated', not an error."""
        cur = FakeCursor(fetchone_results=[
            None,                                   # idempotency pre-check: nothing yet
            None,                                   # INSERT ... ON CONFLICT DO NOTHING -- lost the race
            ("winner-alloc-uuid", fulfilment.make_idempotency_key(
                "single_purchase", "PLANIT-005", "cs_test_5")),  # follow-up SELECT by lead_reference
            ("winner-obligation-uuid", "pending_approval"),      # its obligation
        ])
        result = fulfilment.create_allocation_and_obligation(
            cur, lead_reference="PLANIT-005", lead_id="lead-uuid-5", address="1 Test St",
            applicant_name=None, buyer_email="buyer@example.com",
            allocation_type="single_purchase", sale_context="single_purchase",
            source_payment_ref="cs_test_5",
        )
        self.assertTrue(result.ok)
        self.assertEqual(result.allocation_id, "winner-alloc-uuid")
        self.assertEqual(result.obligation_id, "winner-obligation-uuid")
        self.assertEqual(result.reason, "already_allocated")

    def test_insert_conflict_from_different_buyer_raises_lead_already_allocated(self):
        """The genuine cross-buyer case: the row already sitting on this
        lead_reference belongs to a DIFFERENT idempotency_key (a different
        buyer/event already claimed it). Must raise LeadAlreadyAllocatedError
        -- never silently return ok=False (which _dispatch_via_active_
        pipeline's caller would discard, letting an unpaid/unallocated sale
        look successful) and never duplicate the allocation."""
        cur = FakeCursor(fetchone_results=[
            None,                                    # idempotency pre-check: nothing yet
            None,                                    # INSERT ... ON CONFLICT DO NOTHING -- lost the race
            ("other-buyers-alloc-uuid", "some-other-idempotency-key"),  # follow-up SELECT: different event
        ])
        with self.assertRaises(fulfilment.LeadAlreadyAllocatedError):
            fulfilment.create_allocation_and_obligation(
                cur, lead_reference="PLANIT-006", lead_id="lead-uuid-6", address="1 Test St",
                applicant_name=None, buyer_email="second-buyer@example.com",
                allocation_type="single_purchase", sale_context="single_purchase",
                source_payment_ref="cs_test_6",
            )
        # No letter_obligations row must ever be created for the loser.
        obligation_inserts = [e for e in cur.executed if "INSERT INTO letter_obligations" in e[0]]
        self.assertEqual(obligation_inserts, [])


class TestClaimForSubmission(unittest.TestCase):
    def test_claim_succeeds_when_ready_and_unclaimed(self):
        cur = FakeCursor(fetchone_results=[("obligation-uuid-1",)])
        self.assertTrue(fulfilment.claim_for_submission(cur, "obligation-uuid-1", "worker-A"))

    def test_claim_fails_when_already_claimed_or_not_ready(self):
        """Concurrent-worker safety: the UPDATE ... WHERE status = 'ready'
        clause is what makes this atomic against a real DB; this test
        proves the Python-level contract (no row returned = no claim) that
        callers must respect."""
        cur = FakeCursor(fetchone_results=[None])
        self.assertFalse(fulfilment.claim_for_submission(cur, "obligation-uuid-1", "worker-B"))


class TestMarkProviderResult(unittest.TestCase):
    def test_dry_run_never_sets_real_dispatch_fields_even_if_outcome_says_dispatched(self):
        """The core regression test for the old process_pending_letter_dispatches
        bug (database.py:5381-5388 in the inspected working copy): a
        dry-run result must NEVER produce dispatched_at/provider_accepted_at,
        regardless of what `outcome` claims."""
        cur = FakeCursor()
        fulfilment.mark_provider_result(cur, "obligation-uuid-1", outcome="dispatched", is_dry_run=True,
                                         provider_name="dry_run")
        sql, params = cur.executed[0]
        self.assertIn("status = 'dry_run'", sql)
        self.assertNotIn("dispatched_at = NOW()", sql)
        self.assertNotIn("provider_accepted_at = NOW()", sql)

    def test_real_accepted_outcome_sets_provider_accepted_at(self):
        cur = FakeCursor()
        fulfilment.mark_provider_result(cur, "obligation-uuid-1", outcome="accepted", is_dry_run=False,
                                         provider_name="fake_test", provider_reference="FAKE-1")
        sql, params = cur.executed[0]
        self.assertIn("provider_accepted_at = NOW()", sql)
        self.assertIn("status = 'provider_accepted'", sql)

    def test_unknown_outcome_does_not_reset_to_ready(self):
        """This is what makes 'never auto-resend an ambiguous outcome' true
        at the data layer: 'unknown' must never be spelled in a way that a
        later query could mistake for 'ready'."""
        cur = FakeCursor()
        fulfilment.mark_provider_result(cur, "obligation-uuid-1", outcome="unknown", is_dry_run=False,
                                         provider_name="stannp", error="timeout")
        sql, params = cur.executed[0]
        self.assertIn("status = 'unknown'", sql)
        self.assertNotIn("status = 'ready'", sql)

    def test_invalid_outcome_raises(self):
        cur = FakeCursor()
        with self.assertRaises(ValueError):
            fulfilment.mark_provider_result(cur, "obligation-uuid-1", outcome="definitely_sent_trust_me", is_dry_run=False)


class TestMarkCancelled(unittest.TestCase):
    def test_cancel_succeeds_before_submission(self):
        cur = FakeCursor(fetchone_results=[("obligation-uuid-1",)])
        self.assertTrue(fulfilment.mark_cancelled(cur, "obligation-uuid-1", "refund issued"))
        self.assertIn("status IN ('pending_approval', 'pending_funding', 'ready', 'blocked_missing_data')",
                       cur.executed[0][0])

    def test_cancel_is_rejected_once_already_submitted_or_beyond(self):
        """Section 3: 'a refund does not automatically cancel a letter
        already submitted.' The WHERE clause's status list is what
        enforces this -- a row already in submitting/provider_accepted/
        dispatched/unknown will not match, so fetchone() returns None."""
        cur = FakeCursor(fetchone_results=[None])
        self.assertFalse(fulfilment.mark_cancelled(cur, "obligation-uuid-1", "refund issued"))


class TestHasUnresolvedReconciliationIssue(unittest.TestCase):
    """2026-09-18 review, Section 4 (second pass): the durable-identity
    lookup payments.py's webhook handler uses to tell a delayed retry of
    an already-known allocation failure apart from a genuinely lost
    reservation -- see this function's own docstring in fulfilment.py."""

    def test_no_ids_given_returns_false_without_querying(self):
        cur = FakeCursor()
        self.assertFalse(fulfilment.has_unresolved_reconciliation_issue(cur))
        self.assertEqual(cur.executed, [], "must not issue a query with nothing to match on")

    def test_matching_unresolved_row_by_event_id_returns_true(self):
        cur = FakeCursor(fetchone_results=[(1,)])
        self.assertTrue(fulfilment.has_unresolved_reconciliation_issue(cur, stripe_event_id="evt_1"))
        sql, params = cur.executed[0]
        self.assertIn("resolved = FALSE", sql)
        self.assertEqual(params, ("evt_1", "evt_1", None, None))

    def test_matching_unresolved_row_by_reference_only_returns_true(self):
        cur = FakeCursor(fetchone_results=[(1,)])
        self.assertTrue(fulfilment.has_unresolved_reconciliation_issue(cur, stripe_reference="tok-abc"))
        sql, params = cur.executed[0]
        self.assertEqual(params, (None, None, "tok-abc", "tok-abc"))

    def test_no_matching_row_returns_false(self):
        cur = FakeCursor(fetchone_results=[None])
        self.assertFalse(fulfilment.has_unresolved_reconciliation_issue(
            cur, stripe_event_id="evt_gone", stripe_reference="tok-gone",
        ))

    def test_both_ids_passed_through_together(self):
        """Matched on EITHER id -- both are still passed through to the
        query so a row recorded under only one of them (see
        record_reconciliation_issue's callers, which don't always have
        both available) is still found."""
        cur = FakeCursor(fetchone_results=[(1,)])
        fulfilment.has_unresolved_reconciliation_issue(cur, stripe_event_id="evt_1", stripe_reference="tok-1")
        sql, params = cur.executed[0]
        self.assertEqual(params, ("evt_1", "evt_1", "tok-1", "tok-1"))


if __name__ == "__main__":
    unittest.main()

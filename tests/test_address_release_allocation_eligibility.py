"""
test_address_release_allocation_eligibility.py -- 2026-09-18 review,
Section 2 (second pass): "Review ADDRESS_RELEASE_LIVE. Preserve authorised
historical purchase access. For new allocations, implement and test
eligibility at allocation/order level, with the global flag serving only
as an additional control. Do not invent a legally sufficient release
trigger; leave that policy configurable and explicitly unresolved."

Covers the new functions in address_release.py:
  - _lead_allocation_kind / is_historical_purchase -- the historical-vs-new
    distinction, reusing fulfilment.get_lead_owner's own pre-existing
    lead_allocations-then-letter_dispatches fallback logic.
  - set_allocation_address_eligible / get_allocation_address_eligible --
    the per-allocation, operator-recorded decision (address_disclosure_
    decisions table). Proves this is never inferred or defaulted to True.
    Kept as an audit-trail primitive (2026-09-22 handoff) even though
    guarded_address_for_lead/lead_address_release_allowed no longer read
    it toward disclosure -- see those functions' own tests below.
  - guarded_address_for_lead / lead_address_release_allowed -- 2026-09-22
    handoff, "new purchases never release the address, no toggle": the
    combined decision is now two-tier, not three -- historical always
    wins; literally everything else (a new allocation regardless of any
    recorded eligibility decision or ADDRESS_RELEASE_LIVE, or a lead this
    can't classify at all) is redacted/denied. The old "new allocation +
    eligible=True + flag on -> real address" path is gone; several tests
    below exist specifically to prove it's gone (not just "still off by
    default"), including that get_allocation_address_eligible isn't even
    consulted for a new allocation any more.
  - guarded_address_for_lead_reference -- the self-contained wrapper
    real route call sites use, including its fail-safe-on-DB-error path.
  - count_historical_address_exposure_leads -- the reporting primitive for
    the (frozen, never silently grown) historical-exposure population.

Uses a plain queued FakeCursor (same convention as tests/test_worker.py
etc) for the cur-based functions, and mocks address_release.database.
get_db_conn for the self-contained wrappers -- deliberately NOT a bare
MagicMock() cursor (which would return truthy-by-default fetchone()
results and mask real logic bugs, as a quick manual check during
development confirmed happens to reproduce the OLD single-flag behaviour
by accident in tests/test_address_release_gate.py's existing route tests
-- this file is what actually exercises the new decision paths those
tests don't).

Run with:
    python -m unittest tests.test_address_release_allocation_eligibility -v
"""
import os
import sys
import types
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# database.py imports psycopg2, which is not installable in this sandbox
# (see docs/handoff.md). Same stubbing convention test_main.py established:
# register a fake "database" module in sys.modules (with a get_db_conn
# attribute to patch) BEFORE anything imports the real one, so
# address_release.py's own module-level `import database` resolves to the
# stub rather than trying to import psycopg2. Only stub if a real/other-
# stub "database" isn't already registered (e.g. running alongside
# test_main-based files).
if "database" not in sys.modules:
    sys.modules["database"] = types.ModuleType("database")
_database_stub = sys.modules["database"]
if not hasattr(_database_stub, "get_db_conn"):
    _database_stub.get_db_conn = MagicMock()

import address_release

# This file patches via @patch.object(address_release.database, ...) below
# -- not the string form @patch("database.get_db_conn") -- because
# address_release.py binds "database" ONCE at its own module-import time
# (see that module's own comment on why). A string-based patch re-resolves
# sys.modules["database"] fresh at patch time instead, which under the
# full test suite (where other files replace sys.modules["database"] with
# a new object during collection) can end up patching a DIFFERENT object
# than the one address_release.py actually calls. Patching the attribute
# on the bound reference itself sidesteps that regardless of what
# sys.modules currently holds.


class FakeCursor:
    """Queued fetchone results, consumed in call order -- same convention
    as every other FakeCursor in this test suite."""

    def __init__(self, fetchone_results=None):
        self.executed = []
        self._fetchone_results = list(fetchone_results or [])

    def close(self):
        """No-op -- guarded_address_for_lead_reference/lead_address_release_allowed
        call cur.close() in a finally block, same as real DB cursors."""
        pass

    def execute(self, sql, params=None):
        self.executed.append((" ".join(sql.split()), params))

    def fetchone(self):
        return self._fetchone_results.pop(0) if self._fetchone_results else None


class TestLeadAllocationKind(unittest.TestCase):
    def test_found_in_lead_allocations_is_a_new_allocation(self):
        cur = FakeCursor(fetchone_results=[(1,)])  # lead_allocations hit
        self.assertTrue(address_release._lead_allocation_kind(cur, "PLANIT-001"))

    def test_found_only_in_letter_dispatches_is_historical(self):
        cur = FakeCursor(fetchone_results=[None, (1,)])  # lead_allocations miss, letter_dispatches hit
        self.assertFalse(address_release._lead_allocation_kind(cur, "PLANIT-001"))

    def test_found_in_neither_is_unknown(self):
        cur = FakeCursor(fetchone_results=[None, None])
        self.assertIsNone(address_release._lead_allocation_kind(cur, "PLANIT-GONE"))

    def test_lead_allocations_checked_before_letter_dispatches(self):
        """A lead present in BOTH tables (e.g. a legacy dispatch that was
        later also allocated under the new pipeline) must be treated as a
        NEW allocation -- only one query should even be needed."""
        cur = FakeCursor(fetchone_results=[(1,)])
        result = address_release._lead_allocation_kind(cur, "PLANIT-001")
        self.assertTrue(result)
        self.assertEqual(len(cur.executed), 1, "must not query letter_dispatches once lead_allocations already matched")


class TestIsHistoricalPurchase(unittest.TestCase):
    def test_new_allocation_is_not_historical(self):
        cur = FakeCursor(fetchone_results=[(1,)])
        self.assertFalse(address_release.is_historical_purchase(cur, "PLANIT-001"))

    def test_letter_dispatches_only_is_historical(self):
        cur = FakeCursor(fetchone_results=[None, (1,)])
        self.assertTrue(address_release.is_historical_purchase(cur, "PLANIT-001"))

    def test_unknown_lead_fails_toward_not_historical(self):
        """Section 2's own instruction: never invent a release trigger --
        an unclassifiable lead must fail toward the STRICTER path, not the
        more permissive one."""
        cur = FakeCursor(fetchone_results=[None, None])
        self.assertFalse(address_release.is_historical_purchase(cur, "PLANIT-GONE"))


class TestAllocationEligibilityDecision(unittest.TestCase):
    def test_no_decision_recorded_is_not_eligible(self):
        """Never defaults to eligible -- an allocation nobody has made a
        call on yet must stay redacted."""
        cur = FakeCursor(fetchone_results=[None])
        self.assertFalse(address_release.get_allocation_address_eligible(cur, "PLANIT-001"))

    def test_recorded_eligible_true_reads_back_true(self):
        cur = FakeCursor(fetchone_results=[(True,)])
        self.assertTrue(address_release.get_allocation_address_eligible(cur, "PLANIT-001"))

    def test_recorded_eligible_false_reads_back_false(self):
        cur = FakeCursor(fetchone_results=[(False,)])
        self.assertFalse(address_release.get_allocation_address_eligible(cur, "PLANIT-001"))

    def test_set_requires_a_named_decider(self):
        """The decision must be attributable to a human -- this is an
        audited admin action, not an automated inference (Section 2:
        'do not invent a legally sufficient release trigger')."""
        cur = FakeCursor()
        with self.assertRaises(ValueError):
            address_release.set_allocation_address_eligible(cur, "PLANIT-001", eligible=True, decided_by="")

    def test_set_issues_an_upsert_with_the_given_values(self):
        cur = FakeCursor()
        address_release.set_allocation_address_eligible(
            cur, "PLANIT-001", eligible=True, decided_by="nick@treekey.co.uk", note="pilot contractor, reviewed manually",
        )
        self.assertEqual(len(cur.executed), 1)
        sql, params = cur.executed[0]
        self.assertIn("INSERT INTO address_disclosure_decisions", sql)
        self.assertIn("ON CONFLICT", sql)
        self.assertEqual(params, ("PLANIT-001", True, "nick@treekey.co.uk", "pilot contractor, reviewed manually"))


class TestGuardedAddressForLead(unittest.TestCase):
    """The combined two-tier decision (2026-09-22 handoff) -- guarded_address_for_lead."""

    def setUp(self):
        os.environ.pop(address_release.ADDRESS_RELEASE_LIVE_ENV, None)

    def tearDown(self):
        os.environ.pop(address_release.ADDRESS_RELEASE_LIVE_ENV, None)

    def test_historical_lead_shows_real_address_even_with_global_flag_off(self):
        """THE core 'preserve authorised historical purchase access'
        guarantee -- a pre-existing claim keeps its address regardless of
        ADDRESS_RELEASE_LIVE."""
        # lead_allocations miss, letter_dispatches hit -> historical.
        # Nothing after that should even be queried (no eligibility check).
        cur = FakeCursor(fetchone_results=[None, (1,)])
        result = address_release.guarded_address_for_lead(cur, "PLANIT-001", "1 Real Street, Leeds")
        self.assertEqual(result, "1 Real Street, Leeds")
        self.assertEqual(len(cur.executed), 2, "must not query address_disclosure_decisions for a historical lead")

    def test_new_allocation_eligible_and_flag_on_is_still_redacted(self):
        """2026-09-22 handoff, the core regression guard: this exact
        combination (an operator recorded eligible=True AND
        ADDRESS_RELEASE_LIVE is on) used to be sufficient to release a new
        allocation's real address. It no longer is, for any new
        allocation, ever -- and get_allocation_address_eligible must not
        even be QUERIED any more (only 1 execute: the lead_allocations hit
        that classifies it as new), not merely have its result ignored."""
        cur = FakeCursor(fetchone_results=[(1,)])  # lead_allocations hit -- new allocation
        os.environ[address_release.ADDRESS_RELEASE_LIVE_ENV] = "true"
        result = address_release.guarded_address_for_lead(cur, "PLANIT-002", "2 New Street, Leeds")
        self.assertEqual(result, address_release.REDACTED_ADDRESS_PLACEHOLDER)
        self.assertEqual(len(cur.executed), 1,
                          "must not query address_disclosure_decisions for a new allocation any more")

    def test_new_allocation_is_redacted_regardless_of_flag_state(self):
        cur = FakeCursor(fetchone_results=[(1,)])
        # ADDRESS_RELEASE_LIVE left unset (false) -- still redacted, same as when it's on.
        result = address_release.guarded_address_for_lead(cur, "PLANIT-002", "2 New Street, Leeds")
        self.assertEqual(result, address_release.REDACTED_ADDRESS_PLACEHOLDER)

    def test_unclassifiable_lead_is_redacted_even_with_flag_on(self):
        cur = FakeCursor(fetchone_results=[None, None])  # neither table
        os.environ[address_release.ADDRESS_RELEASE_LIVE_ENV] = "true"
        result = address_release.guarded_address_for_lead(cur, "PLANIT-UNKNOWN", "4 Nowhere Street")
        self.assertEqual(result, address_release.REDACTED_ADDRESS_PLACEHOLDER)

    def test_falsy_address_is_always_redacted_regardless_of_classification(self):
        cur = FakeCursor()
        result = address_release.guarded_address_for_lead(cur, "PLANIT-001", "")
        self.assertEqual(result, address_release.REDACTED_ADDRESS_PLACEHOLDER)
        self.assertEqual(cur.executed, [], "must not even query the DB for an empty address")


class TestGuardedAddressForLeadReferenceSelfContained(unittest.TestCase):
    """The wrapper real main.py/notifications.py call sites use -- no cur
    of their own; opens and closes its own connection."""

    @patch.object(address_release.database, "get_db_conn")
    def test_delegates_to_guarded_address_for_lead_and_closes_the_connection(self, mock_get_db_conn):
        mock_conn = MagicMock()
        mock_cur = FakeCursor(fetchone_results=[None, (1,)])  # historical
        mock_conn.cursor.return_value = mock_cur
        mock_get_db_conn.return_value = mock_conn

        result = address_release.guarded_address_for_lead_reference("PLANIT-001", "1 Real Street")
        self.assertEqual(result, "1 Real Street")
        mock_conn.close.assert_called_once()

    @patch.object(address_release.database, "get_db_conn")
    def test_fails_safe_redacted_on_a_database_error(self, mock_get_db_conn):
        mock_get_db_conn.side_effect = RuntimeError("simulated DB outage")
        result = address_release.guarded_address_for_lead_reference("PLANIT-001", "1 Real Street")
        self.assertEqual(result, address_release.REDACTED_ADDRESS_PLACEHOLDER)

    def test_falsy_address_short_circuits_without_touching_the_database(self):
        with patch.object(address_release.database, "get_db_conn") as mock_get_db_conn:
            result = address_release.guarded_address_for_lead_reference("PLANIT-001", "")
            mock_get_db_conn.assert_not_called()
        self.assertEqual(result, address_release.REDACTED_ADDRESS_PLACEHOLDER)


class TestLeadAddressReleaseAllowed(unittest.TestCase):
    """The plain-boolean wrapper for whole-route refusals (generate_street_
    flyer, street_view_redirect) -- no real address string needed."""

    def setUp(self):
        os.environ.pop(address_release.ADDRESS_RELEASE_LIVE_ENV, None)

    def tearDown(self):
        os.environ.pop(address_release.ADDRESS_RELEASE_LIVE_ENV, None)

    @patch.object(address_release.database, "get_db_conn")
    def test_historical_lead_is_allowed_even_with_flag_off(self, mock_get_db_conn):
        mock_conn = MagicMock()
        mock_conn.cursor.return_value = FakeCursor(fetchone_results=[None, (1,)])
        mock_get_db_conn.return_value = mock_conn
        self.assertTrue(address_release.lead_address_release_allowed("PLANIT-001"))

    @patch.object(address_release.database, "get_db_conn")
    def test_new_allocation_is_denied_even_with_flag_on(self, mock_get_db_conn):
        """2026-09-22 handoff regression guard, mirroring guarded_address_
        for_lead's own test above -- no eligible decision, no flag, and no
        COMBINATION of the two can allow a new allocation any more."""
        mock_conn = MagicMock()
        mock_conn.cursor.return_value = FakeCursor(fetchone_results=[(1,)])  # new allocation
        mock_get_db_conn.return_value = mock_conn
        os.environ[address_release.ADDRESS_RELEASE_LIVE_ENV] = "true"
        self.assertFalse(address_release.lead_address_release_allowed("PLANIT-002"))

    @patch.object(address_release.database, "get_db_conn")
    def test_database_error_fails_safe_to_denied(self, mock_get_db_conn):
        mock_get_db_conn.side_effect = RuntimeError("simulated DB outage")
        self.assertFalse(address_release.lead_address_release_allowed("PLANIT-001"))


class TestBuyerFacingReference(unittest.TestCase):
    """2026-09-22 handoff: 'give buyers a random internal reference not
    derivable from the council reference.'"""

    def test_new_allocation_gets_the_allocation_uuid_not_the_council_reference(self):
        cur = FakeCursor(fetchone_results=[
            (1,),                       # _lead_allocation_kind: lead_allocations hit -> not historical
            ("alloc-uuid-xyz",),        # this function's own id lookup
        ])
        result = address_release.buyer_facing_reference(cur, "PLANIT-001")
        self.assertEqual(result, "alloc-uuid-xyz")

    def test_historical_claim_keeps_showing_the_real_council_reference(self):
        cur = FakeCursor(fetchone_results=[None, (1,)])  # lead_allocations miss, letter_dispatches hit -> historical
        result = address_release.buyer_facing_reference(cur, "PLANIT-001")
        self.assertEqual(result, "PLANIT-001")
        self.assertEqual(len(cur.executed), 2, "must not look up an allocation id for a historical lead")

    def test_falls_back_to_the_council_reference_if_no_allocation_row_found(self):
        """Defensive only -- should not happen for a genuine new
        allocation (it always has a lead_allocations row by construction),
        but fails toward showing what the caller already had rather than
        raising out of a render."""
        cur = FakeCursor(fetchone_results=[(1,), None])
        result = address_release.buyer_facing_reference(cur, "PLANIT-002")
        self.assertEqual(result, "PLANIT-002")


class TestCountHistoricalAddressExposureLeads(unittest.TestCase):
    """The reporting primitive for Task #32's gap-list -- purely additive,
    changes nothing about disclosure itself."""

    @patch.object(address_release.database, "get_db_conn")
    def test_returns_the_query_result_and_closes_the_connection(self, mock_get_db_conn):
        mock_conn = MagicMock()
        mock_cur = FakeCursor(fetchone_results=[(7,)])
        mock_conn.cursor.return_value = mock_cur
        mock_get_db_conn.return_value = mock_conn

        result = address_release.count_historical_address_exposure_leads()
        self.assertEqual(result, 7)
        mock_conn.close.assert_called_once()
        sql, _ = mock_cur.executed[0]
        self.assertIn("letter_dispatches", sql)
        self.assertIn("NOT EXISTS", sql)
        self.assertIn("lead_allocations", sql)


if __name__ == "__main__":
    unittest.main()

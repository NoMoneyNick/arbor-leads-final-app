"""
test_funding_reservation_wiring.py -- 2026-09-18 review, Section 6: proves
letter_providers.registry.attempt_send actually calls FundingGate.reserve()
/ .settle() / .release() at the right points, not just that those methods
work in isolation (tests/test_funding.py covers the methods themselves).

Uses a real funding.FundingGate(mode="hold") wired to a FakeCursor that
supports both fetchone and fetchall, so these are true integration tests of
registry.py + funding.py together, not funding.py mocked out.

Run with:
    python -m unittest tests.test_funding_reservation_wiring -v
"""
import os
import sys
import unittest
from unittest.mock import MagicMock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# 2026-09-22 handoff: suppression.is_suppressed (invoked via registry.py's
# attempt_send, which these tests drive) now requires SUPPRESSION_HASH_KEY
# to be set (fails loud -- see suppression.py::_hash_key's docstring). Not
# a real secret, just a fixed non-empty string for deterministic test
# hashing.
os.environ.setdefault("SUPPRESSION_HASH_KEY", "unit-test-suppression-hash-key-not-a-real-secret")

import funding
from letter_providers.base import LetterRequest, fingerprint_content
from letter_providers.fake_provider import FakeLetterProvider
from letter_providers.registry import ProviderRegistry, ProviderSlot, attempt_send


class FakeCursor:
    """Supports both fetchone and fetchall as separate FIFO queues, and
    records every execute() call -- same convention as this suite's other
    FakeCursor helpers."""

    def __init__(self, fetchone_results=None, fetchall_results=None):
        self.executed = []
        self._fetchone_results = list(fetchone_results or [])
        self._fetchall_results = list(fetchall_results or [])

    def execute(self, sql, params=None):
        self.executed.append((sql.strip(), params))

    def fetchone(self):
        return self._fetchone_results.pop(0) if self._fetchone_results else None

    def fetchall(self):
        return self._fetchall_results.pop(0) if self._fetchall_results else []


def _content_html():
    return "<html>Approved letter content.</html>"


def _send_kwargs(**overrides):
    base = dict(
        worker_id="w1", content_html=_content_html(),
        address_lines={"line1": "1 Test St", "city": "Leeds", "postcode": "LS1 1AA", "country": "GB"},
        applicant_name="J Bloggs", lead_reference="PLANIT-001", idempotency_key="idem-1",
        is_dry_run=False, estimated_cost_pence=95,
    )
    base.update(overrides)
    return base


class TestAcceptedSettlesTheReservation(unittest.TestCase):

    def test_accepted_outcome_calls_settle_not_release(self):
        cur = FakeCursor(
            fetchone_results=[
                None,        # suppression check
                ("ob-1",),   # claim_for_submission
                (0,),        # reserve(): already_reserved check
            ],
            fetchall_results=[
                [("budget-1", 1000, 0, 0)],  # reserve(): candidate budget rows
                [("res-1", "budget-1", 95)],  # settle(): reservations to settle
            ],
        )
        gate = funding.FundingGate(mode="hold")
        adapter = FakeLetterProvider(force_outcome="accepted")
        registry = ProviderRegistry([ProviderSlot(adapter=adapter)])

        outcome = attempt_send(cur, registry, "ob-1", gate=gate, **_send_kwargs())

        self.assertEqual(outcome.final_status, "accepted")
        settle_calls = [e for e in cur.executed if "spent_pence = spent_pence" in e[0]]
        release_calls = [e for e in cur.executed if e[0].startswith("UPDATE mailing_budget_confirmations SET reserved_pence = reserved_pence -")]
        self.assertEqual(len(settle_calls), 1)
        self.assertEqual(release_calls, [])


class TestAllRejectedReleasesTheReservation(unittest.TestCase):

    def test_all_providers_rejected_calls_release_not_settle(self):
        cur = FakeCursor(
            fetchone_results=[
                None,        # suppression check
                ("ob-1",),   # claim_for_submission
                (0,),        # reserve(): already_reserved check
            ],
            fetchall_results=[
                [("budget-1", 1000, 0, 0)],   # reserve(): candidate budget rows
                [("res-1", "budget-1", 95)],  # release(): reservations to release
            ],
        )
        gate = funding.FundingGate(mode="hold")
        adapter = FakeLetterProvider(force_outcome="rejected")
        registry = ProviderRegistry([ProviderSlot(adapter=adapter)])

        outcome = attempt_send(cur, registry, "ob-1", gate=gate, **_send_kwargs())

        self.assertEqual(outcome.final_status, "failed")
        release_calls = [e for e in cur.executed if e[0].startswith("UPDATE mailing_budget_confirmations SET reserved_pence = reserved_pence -")]
        settle_calls = [e for e in cur.executed if "spent_pence = spent_pence" in e[0]]
        self.assertEqual(len(release_calls), 1)
        self.assertEqual(settle_calls, [])


class TestUnknownOutcomePreservesTheReservation(unittest.TestCase):
    """The core 'preserve reservations for uncertain submissions until
    reconciled' requirement -- neither settle() nor release() may be
    called."""

    def test_ambiguous_provider_response_touches_neither_settle_nor_release(self):
        cur = FakeCursor(
            fetchone_results=[
                None,        # suppression check
                ("ob-1",),   # claim_for_submission
                (0,),        # reserve(): already_reserved check
            ],
            fetchall_results=[
                [("budget-1", 1000, 0, 0)],  # reserve(): candidate budget rows
            ],
        )
        gate = funding.FundingGate(mode="hold")
        adapter = FakeLetterProvider(force_outcome="unknown")
        registry = ProviderRegistry([ProviderSlot(adapter=adapter)])

        outcome = attempt_send(cur, registry, "ob-1", gate=gate, **_send_kwargs())

        self.assertEqual(outcome.final_status, "unknown")
        # Excludes the initial "reserved_pence = reserved_pence + %s" reserve()
        # call (expected -- it happens before any provider is called at all);
        # only a SUBSEQUENT release ("- %s") or settle ("spent_pence =
        # spent_pence + ...") would mean the reservation was resolved.
        reservation_resolving_calls = [
            e for e in cur.executed
            if "reserved_pence = reserved_pence -" in e[0]
            or "spent_pence = spent_pence" in e[0]
            or e[0].startswith("UPDATE funding_reservations")
        ]
        self.assertEqual(reservation_resolving_calls, [],
                          "an 'unknown' outcome must leave the reservation completely untouched")

    def test_provider_exception_also_preserves_the_reservation(self):
        cur = FakeCursor(
            fetchone_results=[None, ("ob-1",), (0,)],
            fetchall_results=[[("budget-1", 1000, 0, 0)]],
        )
        gate = funding.FundingGate(mode="hold")

        class CrashingAdapter(FakeLetterProvider):
            def send(self, request):
                raise ConnectionError("simulated crash")
        adapter = CrashingAdapter()
        registry = ProviderRegistry([ProviderSlot(adapter=adapter)])

        outcome = attempt_send(cur, registry, "ob-1", gate=gate, **_send_kwargs())

        self.assertEqual(outcome.final_status, "unknown")
        reservation_resolving_calls = [
            e for e in cur.executed
            if "reserved_pence = reserved_pence -" in e[0]
            or "spent_pence = spent_pence" in e[0]
        ]
        self.assertEqual(reservation_resolving_calls, [])


class TestInsufficientFundingStopsBeforeAnyProviderIsCalled(unittest.TestCase):

    def test_reservation_failure_never_reaches_a_provider(self):
        cur = FakeCursor(
            fetchone_results=[
                None,        # suppression check
                ("ob-1",),   # claim_for_submission
                (0,),        # reserve(): already_reserved check
            ],
            fetchall_results=[
                [("budget-1", 10, 0, 0)],  # only 10p available, need 95p
            ],
        )
        gate = funding.FundingGate(mode="hold")
        adapter = FakeLetterProvider(force_outcome="accepted")
        calls = []
        adapter.send = lambda request: calls.append(1)
        registry = ProviderRegistry([ProviderSlot(adapter=adapter)])

        outcome = attempt_send(cur, registry, "ob-1", gate=gate, **_send_kwargs())

        self.assertEqual(outcome.final_status, "failed")
        self.assertEqual(outcome.attempts_made, 0)
        self.assertEqual(calls, [], "no provider should ever be called when funding can't be reserved")
        self.assertIn("Could not reserve funding", outcome.note)


class TestNoGateOrNoCostSkipsReservationWithAWarning(unittest.TestCase):
    """Backward compatibility for test_providers.py's own fallback-logic
    suite (none of which pass a gate) -- and an explicit, documented
    'proceed without protection' escape hatch rather than a silent one."""

    def test_no_gate_supplied_still_sends_but_never_touches_funding_sql(self):
        cur = FakeCursor(fetchone_results=[None, ("ob-1",)])
        adapter = FakeLetterProvider(force_outcome="accepted")
        registry = ProviderRegistry([ProviderSlot(adapter=adapter)])
        outcome = attempt_send(cur, registry, "ob-1", estimated_cost_pence=95, **{
            k: v for k, v in _send_kwargs().items() if k != "estimated_cost_pence"
        })
        self.assertEqual(outcome.final_status, "accepted")
        self.assertEqual([e for e in cur.executed if "mailing_budget_confirmations" in e[0]], [])

    def test_no_estimated_cost_supplied_still_sends_but_never_touches_funding_sql(self):
        cur = FakeCursor(fetchone_results=[None, ("ob-1",)])
        adapter = FakeLetterProvider(force_outcome="accepted")
        registry = ProviderRegistry([ProviderSlot(adapter=adapter)])
        gate = funding.FundingGate(mode="hold")
        kwargs = {k: v for k, v in _send_kwargs().items() if k != "estimated_cost_pence"}
        outcome = attempt_send(cur, registry, "ob-1", gate=gate, **kwargs)
        self.assertEqual(outcome.final_status, "accepted")
        self.assertEqual([e for e in cur.executed if "mailing_budget_confirmations" in e[0]], [])


if __name__ == "__main__":
    unittest.main()

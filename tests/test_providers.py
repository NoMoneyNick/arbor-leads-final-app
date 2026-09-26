"""
test_providers.py -- letter_providers/*, including the registry's fallback
rules (section 6's most safety-critical logic). Run with:
    python -m unittest tests.test_providers -v

standalone_mailer/mailer.py itself was smoke-tested separately, live, in an
isolated sandbox earlier this session (dry-run send, idempotent replay,
and same-key-different-content rejection all verified by direct
execution) -- see docs/handoff.md for that transcript. These tests cover
the NEW adapter interface built on the same pattern.
"""
import json
import os
import sys
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import fulfilment
import suppression

# 2026-09-22 handoff: suppression.is_suppressed (called from registry.py's
# attempt_send, which this file drives extensively) now requires
# SUPPRESSION_HASH_KEY to be set (fails loud, not safe-and-silent -- see
# suppression.py::_hash_key's own docstring). Not a real secret, just a
# fixed non-empty string so hashing is deterministic for these tests, none
# of which are testing suppression's own hashing behaviour (see
# tests/test_suppression.py for that).
os.environ.setdefault(suppression.SUPPRESSION_HASH_KEY_ENV, "unit-test-suppression-hash-key-not-a-real-secret")
from letter_providers.base import LetterRequest, ProviderResult, fingerprint_content, OUTCOME_ACCEPTED, OUTCOME_DISPATCHED, OUTCOME_REJECTED, OUTCOME_UNKNOWN
from letter_providers.fake_provider import FakeLetterProvider
from letter_providers.stannp_provider import StannpProvider
from letter_providers.intelliprint_provider import IntelliprintProvider
from letter_providers.postworks_provider import PostworksProvider
from letter_providers.registry import ProviderRegistry, ProviderSlot, attempt_send


class FakeCursor:
    def __init__(self, fetchone_results=None):
        self.executed = []
        self._fetchone_results = list(fetchone_results or [])

    def execute(self, sql, params=None):
        self.executed.append((sql.strip(), params))

    def fetchone(self):
        return self._fetchone_results.pop(0) if self._fetchone_results else None


def _request(key="idem-1"):
    return LetterRequest(
        idempotency_key=key, lead_reference="PLANIT-001",
        address_lines={"line1": "1 Test St", "city": "Leeds", "postcode": "LS1 1AA", "country": "GB"},
        applicant_name="J Bloggs", content_html="<html>hi</html>",
        content_fingerprint=fingerprint_content("<html>hi</html>"),
    )


class TestFakeProvider(unittest.TestCase):
    def test_accepts_by_default(self):
        provider = FakeLetterProvider()
        result = provider.send(_request())
        self.assertEqual(result.outcome, OUTCOME_ACCEPTED)
        self.assertTrue(result.provider_reference.startswith("FAKE-"))

    def test_duplicate_idempotency_key_returns_cached_result_not_a_new_send(self):
        provider = FakeLetterProvider()
        first = provider.send(_request(key="dup-key"))
        second = provider.send(_request(key="dup-key"))
        self.assertEqual(first.provider_reference, second.provider_reference)

    def test_forced_rejection(self):
        provider = FakeLetterProvider(force_outcome="rejected")
        self.assertEqual(provider.send(_request()).outcome, OUTCOME_REJECTED)

    def test_forced_unknown(self):
        provider = FakeLetterProvider(force_outcome="unknown")
        self.assertEqual(provider.send(_request()).outcome, OUTCOME_UNKNOWN)

    def test_reject_specific_postcodes(self):
        provider = FakeLetterProvider(reject_postcodes={"LS1 1AA"})
        self.assertEqual(provider.send(_request()).outcome, OUTCOME_REJECTED)


class TestUnimplementedProvidersAreHonest(unittest.TestCase):
    def test_intelliprint_reports_not_configured_without_credentials(self):
        os.environ.pop("INTELLIPRINT_API_KEY", None)
        self.assertFalse(IntelliprintProvider().is_configured())

    def test_postworks_reports_not_configured(self):
        self.assertFalse(PostworksProvider().is_configured())

    def test_postworks_raises_rather_than_pretending_to_work(self):
        with self.assertRaises(NotImplementedError):
            PostworksProvider().send(_request())

    def test_stannp_reports_not_configured_without_credentials(self):
        os.environ.pop("STANNP_API_KEY", None)
        os.environ.pop("STANNP_TEMPLATE_ID", None)
        self.assertFalse(StannpProvider().is_configured())


def _fake_http_response(body: dict, status: int = 200):
    """A context-manager-compatible stand-in for the object
    build_opener().open(...) returns, matching how
    intelliprint_provider.py reads it (`with ... as response:
    response.read()`)."""
    class _Resp:
        def __enter__(self):
            return self
        def __exit__(self, *a):
            return False
        def read(self, n=None):
            return json.dumps(body).encode("utf-8")
    return _Resp()


class TestIntelliprintProviderConfiguration(unittest.TestCase):
    def tearDown(self):
        os.environ.pop("INTELLIPRINT_API_KEY", None)
        os.environ.pop("INTELLIPRINT_TEST_MODE", None)

    def test_not_configured_without_api_key(self):
        os.environ.pop("INTELLIPRINT_API_KEY", None)
        self.assertFalse(IntelliprintProvider().is_configured())

    def test_configured_with_api_key(self):
        os.environ["INTELLIPRINT_API_KEY"] = "test-key-123"
        self.assertTrue(IntelliprintProvider().is_configured())

    def test_test_mode_defaults_true(self):
        os.environ["INTELLIPRINT_API_KEY"] = "test-key-123"
        os.environ.pop("INTELLIPRINT_TEST_MODE", None)
        self.assertTrue(IntelliprintProvider().test_mode)

    def test_test_mode_can_be_explicitly_disabled(self):
        os.environ["INTELLIPRINT_API_KEY"] = "test-key-123"
        os.environ["INTELLIPRINT_TEST_MODE"] = "false"
        self.assertFalse(IntelliprintProvider().test_mode)

    def test_send_without_api_key_is_unknown_not_a_crash(self):
        os.environ.pop("INTELLIPRINT_API_KEY", None)
        result = IntelliprintProvider().send(_request())
        self.assertEqual(result.outcome, OUTCOME_UNKNOWN)


class TestIntelliprintProviderSendMapping(unittest.TestCase):
    """Mocks the HTTP layer only -- exercises the real request-building and
    response-mapping code in intelliprint_provider.py against the field
    names/status values verified against Intelliprint's current live docs
    (see that module's own docstring for citations)."""

    def setUp(self):
        os.environ["INTELLIPRINT_API_KEY"] = "test-key-123"

    def tearDown(self):
        os.environ.pop("INTELLIPRINT_API_KEY", None)
        os.environ.pop("INTELLIPRINT_TEST_MODE", None)

    def _send_with_response(self, body):
        provider = IntelliprintProvider()
        with patch("letter_providers.intelliprint_provider.build_opener") as mock_build_opener:
            mock_build_opener.return_value.open.return_value = _fake_http_response(body)
            result = provider.send(_request())
        request_sent = mock_build_opener.return_value.open.call_args[0][0]
        return result, request_sent

    def test_draft_status_maps_to_accepted(self):
        result, _ = self._send_with_response({"id": "print_abc", "status": "draft",
                                                "letters": [{"id": "letter_1", "status": "draft"}]})
        self.assertEqual(result.outcome, OUTCOME_ACCEPTED)
        self.assertEqual(result.provider_reference, "print_abc")

    def test_printing_status_maps_to_accepted(self):
        result, _ = self._send_with_response({"id": "print_abc",
                                                "letters": [{"id": "letter_1", "status": "printing"}]})
        self.assertEqual(result.outcome, OUTCOME_ACCEPTED)

    def test_sent_status_maps_to_dispatched(self):
        result, _ = self._send_with_response({"id": "print_abc",
                                                "letters": [{"id": "letter_1", "status": "sent"}]})
        self.assertEqual(result.outcome, OUTCOME_DISPATCHED)

    def test_invalid_address_maps_to_rejected(self):
        result, _ = self._send_with_response({"id": "print_abc",
                                                "letters": [{"id": "letter_1", "status": "invalid_address"}]})
        self.assertEqual(result.outcome, OUTCOME_REJECTED)

    def test_cancelled_maps_to_rejected(self):
        result, _ = self._send_with_response({"id": "print_abc",
                                                "letters": [{"id": "letter_1", "status": "cancelled"}]})
        self.assertEqual(result.outcome, OUTCOME_REJECTED)

    def test_unrecognised_status_maps_to_unknown_not_guessed(self):
        result, _ = self._send_with_response({"id": "print_abc",
                                                "letters": [{"id": "letter_1", "status": "some_new_status_we_have_never_seen"}]})
        self.assertEqual(result.outcome, OUTCOME_UNKNOWN)

    def test_error_body_maps_to_rejected(self):
        result, _ = self._send_with_response({"error": {"type": "authentication_error", "message": "bad key"}})
        self.assertEqual(result.outcome, OUTCOME_REJECTED)
        self.assertIn("authentication_error", result.message)

    def test_non_dict_body_maps_to_unknown(self):
        provider = IntelliprintProvider()
        with patch("letter_providers.intelliprint_provider.build_opener") as mock_build_opener:
            mock_build_opener.return_value.open.return_value = _fake_http_response(["not", "a", "dict"])
            # json.dumps(list) still parses back to a list -- exercise the
            # isinstance(data, dict) guard directly.
            result = provider.send(_request())
        self.assertEqual(result.outcome, OUTCOME_UNKNOWN)

    def test_network_exception_maps_to_unknown_never_rejected(self):
        provider = IntelliprintProvider()
        with patch("letter_providers.intelliprint_provider.build_opener") as mock_build_opener:
            mock_build_opener.return_value.open.side_effect = TimeoutError("simulated timeout")
            result = provider.send(_request())
        self.assertEqual(result.outcome, OUTCOME_UNKNOWN)

    def test_cost_pence_conversion(self):
        result, _ = self._send_with_response({
            "id": "print_abc", "status": "draft",
            "cost": {"amount": 100000000, "tax": 20000000, "after_tax": 120000000, "currency": "GBP"},
        })
        self.assertEqual(result.cost_pence, 120)

    def test_request_uses_bearer_auth_header(self):
        _, request_sent = self._send_with_response({"id": "print_abc", "status": "draft"})
        self.assertEqual(request_sent.get_header("Authorization"), "Bearer test-key-123")

    def test_request_defaults_to_testmode_true(self):
        _, request_sent = self._send_with_response({"id": "print_abc", "status": "draft"})
        body = request_sent.data.decode()
        self.assertIn("testmode=true", body)

    def test_testmode_false_is_actually_sent_when_explicitly_configured(self):
        os.environ["INTELLIPRINT_TEST_MODE"] = "false"
        _, request_sent = self._send_with_response({"id": "print_abc", "status": "draft"})
        body = request_sent.data.decode()
        self.assertIn("testmode=false", body)

    def test_address_line_and_city_are_combined(self):
        _, request_sent = self._send_with_response({"id": "print_abc", "status": "draft"})
        body = request_sent.data.decode()
        # _request() in this file uses address_lines={"line1": "1 Test St", "city": "Leeds", ...}
        self.assertIn("recipients%5B0%5D%5Baddress%5D%5Bline%5D=1+Test+St%2C+Leeds", body)


class TestIntelliprintProviderCheckStatus(unittest.TestCase):
    def setUp(self):
        os.environ["INTELLIPRINT_API_KEY"] = "test-key-123"

    def tearDown(self):
        os.environ.pop("INTELLIPRINT_API_KEY", None)

    def test_check_status_reconciles_to_dispatched(self):
        provider = IntelliprintProvider()
        with patch("letter_providers.intelliprint_provider.build_opener") as mock_build_opener:
            mock_build_opener.return_value.open.return_value = _fake_http_response(
                {"id": "print_abc", "letters": [{"id": "letter_1", "status": "sent"}]})
            result = provider.check_status("print_abc")
        self.assertIsNotNone(result)
        self.assertEqual(result.outcome, OUTCOME_DISPATCHED)

    def test_check_status_returns_none_on_network_failure(self):
        provider = IntelliprintProvider()
        with patch("letter_providers.intelliprint_provider.build_opener") as mock_build_opener:
            mock_build_opener.return_value.open.side_effect = TimeoutError("simulated timeout")
            result = provider.check_status("print_abc")
        self.assertIsNone(result)

    def test_check_status_without_credentials_returns_none(self):
        os.environ.pop("INTELLIPRINT_API_KEY", None)
        self.assertIsNone(IntelliprintProvider().check_status("print_abc"))


class TestRegistryFallbackRules(unittest.TestCase):
    """The load-bearing tests in this file -- see registry.py's module
    docstring for the invariant being locked in here."""

    def setUp(self):
        self.cur = FakeCursor(fetchone_results=[
            None,                    # suppression.is_suppressed -> not suppressed
            ("obligation-1",),       # claim_for_submission -> claimed
        ])

    def test_falls_back_to_second_provider_after_confirmed_rejection(self):
        primary = FakeLetterProvider(force_outcome="rejected")
        primary.name = "primary"
        backup = FakeLetterProvider(force_outcome="accepted")
        backup.name = "backup"
        registry = ProviderRegistry([ProviderSlot(adapter=primary), ProviderSlot(adapter=backup)])

        outcome = attempt_send(self.cur, registry, "obligation-1", worker_id="w1",
                                content_html="<html>x</html>", address_lines=_request().address_lines,
                                applicant_name="J Bloggs", lead_reference="PLANIT-001",
                                idempotency_key="idem-1", is_dry_run=False)
        self.assertEqual(outcome.final_status, "accepted")
        self.assertEqual(outcome.provider_name, "backup")
        self.assertEqual(outcome.attempts_made, 2)

    def test_does_not_fall_back_on_unknown_outcome(self):
        """The single most important behaviour in this file: an ambiguous
        first attempt must STOP the whole thing at 'unknown', never try a
        second provider (which could result in the letter going out twice
        if the first attempt was actually accepted)."""
        primary = FakeLetterProvider(force_outcome="unknown")
        primary.name = "primary"
        backup = FakeLetterProvider(force_outcome="accepted")
        backup.name = "backup"
        registry = ProviderRegistry([ProviderSlot(adapter=primary), ProviderSlot(adapter=backup)])

        outcome = attempt_send(self.cur, registry, "obligation-1", worker_id="w1",
                                content_html="<html>x</html>", address_lines=_request().address_lines,
                                applicant_name="J Bloggs", lead_reference="PLANIT-001",
                                idempotency_key="idem-1", is_dry_run=False)
        self.assertEqual(outcome.final_status, "unknown")
        self.assertEqual(outcome.attempts_made, 1)
        self.assertNotEqual(outcome.provider_name, "backup")

    def test_an_adapter_that_raises_is_treated_as_unknown_not_rejected(self):
        """Covers 'crash after submission' -- an exception must never be
        read as 'safe to try the next provider'."""
        class CrashingAdapter(FakeLetterProvider):
            def send(self, request):
                raise ConnectionError("simulated network crash mid-request")
        crashing = CrashingAdapter()
        crashing.name = "primary"
        backup = FakeLetterProvider(force_outcome="accepted")
        backup.name = "backup"
        registry = ProviderRegistry([ProviderSlot(adapter=crashing), ProviderSlot(adapter=backup)])

        outcome = attempt_send(self.cur, registry, "obligation-1", worker_id="w1",
                                content_html="<html>x</html>", address_lines=_request().address_lines,
                                applicant_name="J Bloggs", lead_reference="PLANIT-001",
                                idempotency_key="idem-1", is_dry_run=False)
        self.assertEqual(outcome.final_status, "unknown")
        self.assertEqual(outcome.attempts_made, 1)

    def test_dry_run_never_calls_any_provider(self):
        primary = FakeLetterProvider()
        primary.name = "primary"
        registry = ProviderRegistry([ProviderSlot(adapter=primary)])
        calls = []
        primary.send = lambda request: calls.append(1) or ProviderResult(outcome=OUTCOME_ACCEPTED, provider_name="primary")

        outcome = attempt_send(self.cur, registry, "obligation-1", worker_id="w1",
                                content_html="<html>x</html>", address_lines=_request().address_lines,
                                applicant_name="J Bloggs", lead_reference="PLANIT-001",
                                idempotency_key="idem-1", is_dry_run=True)
        self.assertEqual(outcome.final_status, "dry_run")
        self.assertEqual(calls, [])

    def test_suppressed_recipient_never_reaches_any_provider(self):
        cur = FakeCursor(fetchone_results=[("row-1", "objected", "this_person")])  # suppressed
        primary = FakeLetterProvider()
        primary.name = "primary"
        calls = []
        primary.send = lambda request: calls.append(1) or ProviderResult(outcome=OUTCOME_ACCEPTED, provider_name="primary")
        registry = ProviderRegistry([ProviderSlot(adapter=primary)])

        outcome = attempt_send(cur, registry, "obligation-1", worker_id="w1",
                                content_html="<html>x</html>", address_lines=_request().address_lines,
                                applicant_name="J Bloggs", lead_reference="PLANIT-001",
                                idempotency_key="idem-1", is_dry_run=False)
        self.assertEqual(outcome.final_status, "suppressed")
        self.assertEqual(calls, [])

    def test_already_claimed_obligation_is_skipped_not_resent(self):
        """Concurrent-worker case: a second worker racing on the same
        obligation must not attempt a send at all."""
        cur = FakeCursor(fetchone_results=[None, None])  # not suppressed, claim fails
        primary = FakeLetterProvider()
        registry = ProviderRegistry([ProviderSlot(adapter=primary)])
        outcome = attempt_send(cur, registry, "obligation-1", worker_id="w2",
                                content_html="<html>x</html>", address_lines=_request().address_lines,
                                applicant_name="J Bloggs", lead_reference="PLANIT-001",
                                idempotency_key="idem-1", is_dry_run=False)
        self.assertEqual(outcome.final_status, "skipped")

    def test_disabled_or_unconfigured_provider_is_never_selected(self):
        unconfigured = IntelliprintProvider()  # is_configured() always False
        backup = FakeLetterProvider(force_outcome="accepted")
        backup.name = "backup"
        registry = ProviderRegistry([ProviderSlot(adapter=unconfigured), ProviderSlot(adapter=backup)])
        outcome = attempt_send(self.cur, registry, "obligation-1", worker_id="w1",
                                content_html="<html>x</html>", address_lines=_request().address_lines,
                                applicant_name="J Bloggs", lead_reference="PLANIT-001",
                                idempotency_key="idem-1", is_dry_run=False)
        self.assertEqual(outcome.provider_name, "backup")

    def test_cost_ceiling_excludes_a_too_expensive_provider(self):
        expensive = FakeLetterProvider(force_outcome="accepted")
        expensive.name = "expensive"
        registry = ProviderRegistry([ProviderSlot(adapter=expensive, cost_ceiling_pence=10)])
        outcome = attempt_send(self.cur, registry, "obligation-1", worker_id="w1",
                                content_html="<html>x</html>", address_lines=_request().address_lines,
                                applicant_name="J Bloggs", lead_reference="PLANIT-001",
                                idempotency_key="idem-1", is_dry_run=False, estimated_cost_pence=45)
        self.assertEqual(outcome.final_status, "failed")

    def test_provider_suspended_after_repeated_non_accept_outcomes(self):
        flaky = FakeLetterProvider(force_outcome="rejected")
        flaky.name = "flaky"
        slot = ProviderSlot(adapter=flaky)
        for outcome in (OUTCOME_REJECTED, OUTCOME_REJECTED, OUTCOME_REJECTED):
            slot.record_outcome(outcome)
        self.assertTrue(slot.suspended)
        self.assertFalse(slot.usable(estimated_cost_pence=None))


if __name__ == "__main__":
    unittest.main()

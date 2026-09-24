"""
test_payments_webhook.py -- 2026-09-18 review, Section 3: "Show the full
recovery path when Stripe payment succeeds but the database transaction
fails."

Drives the REAL payments.handle_stripe_webhook (not a stub -- unlike
test_access_control.py's main.py-level tests, which stub payments.py
entirely; see that file's own docstring for why) with `database` and
`notifications` replaced by lightweight fakes and `stripe` stubbed (not
installed in this sandbox), but `fulfilment` imported for real so
AllocationPersistenceError is a genuine exception class, not a MagicMock.

Proves, with evidence, that a Stripe charge which succeeds while the local
allocation write fails (fulfilment.AllocationPersistenceError):
  1. Never triggers an auto-refund (that branch exists only for a
     genuinely lost/stolen reservation, a DIFFERENT failure mode).
  2. Never fires the (legacy-path) "DOUBLE SALE RACE CONDITION" alert --
     that mislabels a DB write failure as a stolen/contested lead.
  3. Is recorded durably (database.record_payment_reconciliation_issue)
     for admin visibility, distinct from every other alert category.
  4. Fires its own distinct CRITICAL alert.
  5. Is signalled back as {"retry": True} so main.py's route (see
     test_webhook_route.py) can return a retryable status instead of a
     false 200 or a misleading 400.
  6. Leaves the Stripe event unmarked as fulfilled, so a genuine Stripe
     retry (or manual Resend) is handled as a fresh attempt, not swallowed
     as "already seen".

Also regression-guards the pre-existing success and duplicate-delivery
paths so this change is proven not to have altered their behaviour.

Run with:
    python -m unittest tests.test_payments_webhook -v
"""
import os
import sys
import types
import unittest
from unittest.mock import MagicMock, patch

_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
_APP_DIR = os.path.dirname(_THIS_DIR)
sys.path.insert(0, _APP_DIR)

# --- stripe isn't installed in this sandbox; payments.py only needs a
# handful of names from it (Webhook.construct_event, error.
# SignatureVerificationError, Refund.create, api_key). Always (re)built
# fresh here, not just "if absent" -- run under `unittest discover`,
# test_access_control.py (which alphabetically sorts before this file) has
# ALREADY imported real main.py with `payments` itself stubbed as a
# permissive MagicMock module (see that file's own docstring) and never
# touches `stripe` -- but other orderings/direct single-file runs shouldn't
# be able to leave a stale/partial stripe stub behind either. -------------
_fake_stripe = types.ModuleType("stripe")
_fake_stripe.api_key = ""


class _FakeSignatureVerificationError(Exception):
    pass


_fake_stripe_error_mod = types.ModuleType("stripe.error")
_fake_stripe_error_mod.SignatureVerificationError = _FakeSignatureVerificationError


class _FakeErrorNS:
    SignatureVerificationError = _FakeSignatureVerificationError


_fake_stripe.error = _FakeErrorNS
_fake_stripe.Webhook = MagicMock()
_fake_stripe.Refund = MagicMock()
sys.modules["stripe"] = _fake_stripe
sys.modules["stripe.error"] = _fake_stripe_error_mod

# --- database, notifications AND payments are (re)stubbed/(re)loaded fresh
# here, unconditionally. Critical for `payments`: test_access_control.py
# (alphabetically before this file under `unittest discover`) stubs
# sys.modules["payments"] as a permissive MagicMock BEFORE this file's
# `import payments` would run -- without deleting that cached entry first,
# `import payments` below silently returns the STUB, and every test in
# this file would be asserting against a MagicMock instead of exercising
# payments.py's real branching logic (caught by running the full suite,
# not just this file in isolation -- see docs/handoff.md's evidence
# section on why "passes alone" isn't sufficient). database/notifications
# are stubbed (not the real modules) deliberately -- this file tests
# payments.py's own logic, not theirs. ------------------------------------
#
# NOTE (2026-09-18 review, Section 2 second pass): this intentionally
# replaces sys.modules["database"] with a brand-new object, which means
# anything that bound its own "database" reference earlier in this same
# process (main.database, set when test_access_control.py first imported
# main.py) keeps pointing at the OLD object -- a real divergence risk for
# any module that resolves "database" dynamically at call time rather
# than through main.database. address_release.py's DB-opening helpers
# are fixed to bind "database" at THEIR OWN module-import time instead
# (same early import chain as main.py, before this file ever runs), so
# they are unaffected by this swap -- see address_release.py's own
# comment on this. Don't reintroduce a dynamic per-call `import database`
# there without re-reading that comment.
for _name in ("database", "notifications", "payments"):
    if _name in sys.modules:
        del sys.modules[_name]

for _name in ("database", "notifications"):
    _mod = types.ModuleType(_name)
    sys.modules[_name] = _mod

import database  # the fake module object just registered above
import notifications  # ditto
import fulfilment  # REAL module -- AllocationPersistenceError must be a genuine exception class
import payments  # noqa: E402 -- the REAL module, freshly (re)imported above

# payments.STRIPE_WEBHOOK_SECRET is read once at import time from the
# environment; handle_stripe_webhook bails out immediately (a config error,
# not the behaviour under test here) if it's empty. Set it directly on the
# already-imported module rather than the environment, which would be too
# late.
payments.STRIPE_WEBHOOK_SECRET = "whsec_test_dummy"


def _fake_event(event_id="evt_1", session_id="cs_test_1", lead_id=None,
                 reservation_token=None, customer_email="buyer@example.com",
                 amount_total=1900, payment_intent="pi_1"):
    data = {
        "id": session_id,
        "customer_details": {"email": customer_email},
        "amount_total": amount_total,
        "metadata": {},
        "payment_intent": payment_intent,
    }
    if lead_id:
        data["metadata"]["lead_id"] = lead_id
    if reservation_token:
        data["metadata"]["reservation_token"] = reservation_token
    return {"id": event_id, "type": "checkout.session.completed", "data": {"object": data}}


class TestReservationPathAllocationPersistenceFailure(unittest.TestCase):
    """The reservation-based flow (metadata carries both lead_id AND
    reservation_token) -- database.confirm_reserved_lead_sale raises."""

    def setUp(self):
        payments._PROCESSED_STRIPE_EVENT_IDS.clear()
        database.confirm_reserved_lead_sale = MagicMock(
            side_effect=fulfilment.AllocationPersistenceError(
                "Failed to record the letter obligation via the 'fulfilment' pipeline "
                "for lead_reference='PLANIT-001': simulated DB failure"
            )
        )
        database.record_payment_reconciliation_issue = MagicMock(return_value="issue-uuid-1")
        database.get_already_sold_lead_if_matching_session = MagicMock(return_value=None)
        database.get_order_status = MagicMock(return_value=None)
        database.update_order_fulfillment = MagicMock()
        notifications.send_system_incident_alert = MagicMock()
        notifications.send_purchased_lead_email = MagicMock()
        self._stripe_patch = patch("stripe.Refund.create")
        self.mock_refund = self._stripe_patch.start()
        self.addCleanup(self._stripe_patch.stop)

    @patch("stripe.Webhook.construct_event")
    def test_does_not_auto_refund(self, mock_construct):
        mock_construct.return_value = _fake_event(lead_id="PLANIT-001", reservation_token="tok-abc")
        payments.handle_stripe_webhook(b"payload", "sig")
        self.mock_refund.assert_not_called()

    @patch("stripe.Webhook.construct_event")
    def test_records_a_reconciliation_issue(self, mock_construct):
        mock_construct.return_value = _fake_event(lead_id="PLANIT-001", reservation_token="tok-abc",
                                                    customer_email="buyer@example.com")
        payments.handle_stripe_webhook(b"payload", "sig")
        database.record_payment_reconciliation_issue.assert_called_once()
        _, kwargs = database.record_payment_reconciliation_issue.call_args
        self.assertEqual(kwargs["lead_reference"], "PLANIT-001")
        self.assertEqual(kwargs["buyer_email"], "buyer@example.com")
        self.assertIn("allocation_persistence_failed", kwargs["reason"])

    @patch("stripe.Webhook.construct_event")
    def test_fires_a_distinct_critical_alert_not_reservation_lost(self, mock_construct):
        mock_construct.return_value = _fake_event(lead_id="PLANIT-001", reservation_token="tok-abc")
        payments.handle_stripe_webhook(b"payload", "sig")
        notifications.send_system_incident_alert.assert_called_once()
        _, kwargs = notifications.send_system_incident_alert.call_args
        self.assertEqual(kwargs["severity"], "CRITICAL")
        self.assertIn("ALLOCATION FAILED", kwargs["title"])
        self.assertNotIn("reservation lost", kwargs["title"].lower())

    @patch("stripe.Webhook.construct_event")
    def test_returns_retry_signal_not_false_success(self, mock_construct):
        mock_construct.return_value = _fake_event(lead_id="PLANIT-001", reservation_token="tok-abc")
        result = payments.handle_stripe_webhook(b"payload", "sig")
        self.assertIn("error", result)
        self.assertTrue(result.get("retry"))
        self.assertEqual(result.get("reconciliation_issue_id"), "issue-uuid-1")

    @patch("stripe.Webhook.construct_event")
    def test_event_not_marked_fulfilled_so_a_real_retry_is_reattempted(self, mock_construct):
        mock_construct.return_value = _fake_event(event_id="evt_retry_1", lead_id="PLANIT-001",
                                                    reservation_token="tok-abc")
        payments.handle_stripe_webhook(b"payload", "sig")
        self.assertNotIn("evt_retry_1", payments._PROCESSED_STRIPE_EVENT_IDS)

    @patch("stripe.Webhook.construct_event")
    def test_purchased_lead_email_is_not_sent(self, mock_construct):
        mock_construct.return_value = _fake_event(lead_id="PLANIT-001", reservation_token="tok-abc")
        payments.handle_stripe_webhook(b"payload", "sig")
        notifications.send_purchased_lead_email.assert_not_called()


class TestLegacyPathAllocationPersistenceFailure(unittest.TestCase):
    """The pre-reservation legacy flow (metadata carries only lead_id, no
    reservation_token) -- database.burn_lead_inventory raises. Before this
    fix, this failure mode was indistinguishable from 'lead already claimed
    by someone else' and wrongly fired the DOUBLE SALE RACE CONDITION alert."""

    def setUp(self):
        payments._PROCESSED_STRIPE_EVENT_IDS.clear()
        database.burn_lead_inventory = MagicMock(
            side_effect=fulfilment.AllocationPersistenceError("simulated DB failure (legacy path)")
        )
        database.record_payment_reconciliation_issue = MagicMock(return_value="issue-uuid-2")
        database.register_or_update_subscription = MagicMock(return_value=True)
        notifications.send_system_incident_alert = MagicMock()
        notifications.send_purchased_lead_email = MagicMock()

    @patch("stripe.Webhook.construct_event")
    def test_does_not_fire_double_sale_alert(self, mock_construct):
        mock_construct.return_value = _fake_event(lead_id="PLANIT-002")  # no reservation_token -> legacy branch
        payments.handle_stripe_webhook(b"payload", "sig")
        notifications.send_system_incident_alert.assert_called_once()
        _, kwargs = notifications.send_system_incident_alert.call_args
        self.assertNotIn("DOUBLE SALE", kwargs["title"])
        self.assertIn("ALLOCATION FAILED", kwargs["title"])

    @patch("stripe.Webhook.construct_event")
    def test_records_reconciliation_issue_and_signals_retry(self, mock_construct):
        mock_construct.return_value = _fake_event(lead_id="PLANIT-002")
        result = payments.handle_stripe_webhook(b"payload", "sig")
        database.record_payment_reconciliation_issue.assert_called_once()
        self.assertTrue(result.get("retry"))


class TestGenuinelyNoMatchingReservationStillAutoRefunds(unittest.TestCase):
    """Contrast case -- proves the fix didn't remove the OTHER, correct
    behaviour: a genuinely expired/stolen reservation (confirm_reserved_
    lead_sale returns None, no exception) with NO open reconciliation issue
    must still auto-refund exactly as before."""

    def setUp(self):
        payments._PROCESSED_STRIPE_EVENT_IDS.clear()
        database.confirm_reserved_lead_sale = MagicMock(return_value=None)
        database.get_already_sold_lead_if_matching_session = MagicMock(return_value=None)
        database.get_order_status = MagicMock(return_value=None)
        database.update_order_fulfillment = MagicMock()
        database.record_payment_reconciliation_issue = MagicMock()
        database.has_unresolved_payment_reconciliation_issue = MagicMock(return_value=False)
        notifications.send_system_incident_alert = MagicMock()
        notifications.send_purchased_lead_email = MagicMock()
        self._stripe_patch = patch("stripe.Refund.create")
        self.mock_refund = self._stripe_patch.start()
        self.addCleanup(self._stripe_patch.stop)

    @patch("stripe.Webhook.construct_event")
    def test_still_auto_refunds_a_genuinely_lost_reservation(self, mock_construct):
        mock_construct.return_value = _fake_event(lead_id="PLANIT-003", reservation_token="tok-gone")
        result = payments.handle_stripe_webhook(b"payload", "sig")
        self.mock_refund.assert_called_once()
        # This path must NOT carry the new retry=True signal -- it's a
        # completed, handled outcome (refunded), not "please retry".
        self.assertNotIn("error", result)
        database.record_payment_reconciliation_issue.assert_not_called()
        database.has_unresolved_payment_reconciliation_issue.assert_called_once()
        _, kwargs = database.has_unresolved_payment_reconciliation_issue.call_args
        self.assertEqual(kwargs.get("stripe_reference"), "tok-gone")


class TestDelayedRetryAfterReservationExpiryReconcilesByDurableIdentity(unittest.TestCase):
    """2026-09-18 review, Section 4 (second pass): "Test successful Stripe
    payment followed by database failure and then a delayed retry after
    reservation expiry. Reconcile using durable purchase/payment identity."

    Models the full real-world sequence in two separate webhook deliveries
    against the SAME event/session identity, exactly as Stripe's own retry
    would redeliver it:
      1. First delivery: confirm_reserved_lead_sale raises
         AllocationPersistenceError (payment genuinely succeeded, the
         reservation was genuinely valid, but the local write failed) --
         a reconciliation issue is durably recorded.
      2. Second delivery (a DELAYED retry -- modelled here by
         confirm_reserved_lead_sale now returning None, exactly what it
         returns once RESERVATION_RELEASE_MINUTES has passed and
         release_expired_reservations swept the reservation back to 'new'):
         must NOT auto-refund, because an open reconciliation issue for
         this exact durable identity (event id / checkout session id)
         already exists -- refunding here would contradict that still-open,
         admin-alerted issue rather than "reconcile" it."""

    def setUp(self):
        payments._PROCESSED_STRIPE_EVENT_IDS.clear()
        database.record_payment_reconciliation_issue = MagicMock(return_value="issue-uuid-delayed")
        database.update_order_fulfillment = MagicMock()
        database.get_already_sold_lead_if_matching_session = MagicMock(return_value=None)
        database.get_order_status = MagicMock(return_value=None)
        notifications.send_system_incident_alert = MagicMock()
        notifications.send_purchased_lead_email = MagicMock()
        self._stripe_patch = patch("stripe.Refund.create")
        self.mock_refund = self._stripe_patch.start()
        self.addCleanup(self._stripe_patch.stop)

    @patch("stripe.Webhook.construct_event")
    def test_first_delivery_fails_persistence_then_delayed_retry_does_not_refund(self, mock_construct):
        event_id = "evt_delayed_retry_1"
        session_id = "cs_delayed_retry_1"

        # -- Delivery 1: payment succeeds, reservation is genuinely valid,
        # but the local allocation write fails.
        database.confirm_reserved_lead_sale = MagicMock(
            side_effect=fulfilment.AllocationPersistenceError("simulated DB failure")
        )
        mock_construct.return_value = _fake_event(event_id=event_id, session_id=session_id,
                                                     lead_id="PLANIT-005", reservation_token="tok-delayed")
        first_result = payments.handle_stripe_webhook(b"payload", "sig")
        self.assertTrue(first_result.get("retry"))
        database.record_payment_reconciliation_issue.assert_called_once()
        _, kwargs = database.record_payment_reconciliation_issue.call_args
        self.assertEqual(kwargs.get("stripe_event_id"), event_id)
        self.assertEqual(kwargs.get("stripe_reference"), "tok-delayed")
        self.assertNotIn(event_id, payments._PROCESSED_STRIPE_EVENT_IDS, "must not be marked fulfilled after delivery 1")
        self.mock_refund.assert_not_called()

        # -- Delivery 2 (the delayed retry, same event_id/session_id, as a
        # real Stripe redelivery would send): the reservation has since
        # expired and been swept -- confirm_reserved_lead_sale now finds
        # nothing to confirm and returns None, same as a genuinely lost
        # reservation would. The durable reconciliation record from
        # delivery 1 is what must make the difference here.
        database.confirm_reserved_lead_sale = MagicMock(return_value=None)
        database.has_unresolved_payment_reconciliation_issue = MagicMock(return_value=True)
        second_result = payments.handle_stripe_webhook(b"payload", "sig")

        self.mock_refund.assert_not_called()
        self.assertTrue(second_result.get("retry"))
        self.assertNotIn(event_id, payments._PROCESSED_STRIPE_EVENT_IDS, "must still not be marked fulfilled")
        _, kwargs2 = database.has_unresolved_payment_reconciliation_issue.call_args
        self.assertEqual(kwargs2.get("stripe_event_id"), event_id)
        self.assertEqual(kwargs2.get("stripe_reference"), "tok-delayed")


class TestReconciliationCheckItselfFailsDuringOutage(unittest.TestCase):
    """2026-09-18 review, Section 4 (second pass), the sibling instruction:
    "Also handle the case where the reconciliation-record write fails
    during a database outage: return a retryable response and do not mark
    the event fulfilled." Two distinct write/read paths can fail during an
    outage, both covered here and in the class below:
      1. has_unresolved_payment_reconciliation_issue itself can't be
         answered (this class) -- must fail safe to NOT refunding.
      2. record_payment_reconciliation_issue's own write fails when
         recording a NEW issue (next class) -- must still return retry=True
         and never mark the event fulfilled, even though the durable
         record of the failure didn't get written this time."""

    def setUp(self):
        payments._PROCESSED_STRIPE_EVENT_IDS.clear()
        database.confirm_reserved_lead_sale = MagicMock(return_value=None)
        database.get_already_sold_lead_if_matching_session = MagicMock(return_value=None)
        database.get_order_status = MagicMock(return_value=None)
        database.update_order_fulfillment = MagicMock()
        database.record_payment_reconciliation_issue = MagicMock()
        notifications.send_system_incident_alert = MagicMock()
        notifications.send_purchased_lead_email = MagicMock()
        self._stripe_patch = patch("stripe.Refund.create")
        self.mock_refund = self._stripe_patch.start()
        self.addCleanup(self._stripe_patch.stop)

    @patch("stripe.Webhook.construct_event")
    def test_check_itself_failing_fails_safe_to_not_refunding(self, mock_construct):
        # database.has_unresolved_payment_reconciliation_issue's own
        # docstring: fails safe to True (assume an issue might exist) on a
        # DB error, specifically so this exact situation never auto-refunds
        # on an unanswerable check. Modelled directly (this is the
        # self-contained wrapper's own contract, not something this
        # webhook-level test re-derives).
        database.has_unresolved_payment_reconciliation_issue = MagicMock(return_value=True)
        mock_construct.return_value = _fake_event(event_id="evt_outage_check", lead_id="PLANIT-006",
                                                     reservation_token="tok-outage-check")
        result = payments.handle_stripe_webhook(b"payload", "sig")
        self.mock_refund.assert_not_called()
        self.assertTrue(result.get("retry"))
        self.assertNotIn("evt_outage_check", payments._PROCESSED_STRIPE_EVENT_IDS)


class TestReconciliationRecordWriteFailsDuringOutage(unittest.TestCase):
    """The other half of the Section 4 (second pass) sibling instruction:
    "the reconciliation-record write fails during a database outage:
    return a retryable response and do not mark the event fulfilled."

    database.record_payment_reconciliation_issue's own docstring already
    says it's best-effort and returns None (never raises) when ITS write
    fails -- this proves the webhook handler's behaviour around that is
    actually correct, not just documented as intended: even with no
    reconciliation_issue_id at all (the durable record of the failure
    itself didn't get written), the response must still be retryable and
    the event must still not be marked fulfilled, so Stripe's own retry
    mechanism remains the backstop for completing this sale once the
    outage clears -- exactly the same guarantee as when the write
    succeeds, just without an issue id to point an admin at in the
    meantime (the CRITICAL alert, sent regardless via notifications, is
    what carries the burden of getting a human's attention in that case)."""

    def setUp(self):
        payments._PROCESSED_STRIPE_EVENT_IDS.clear()
        database.confirm_reserved_lead_sale = MagicMock(
            side_effect=fulfilment.AllocationPersistenceError("simulated DB failure")
        )
        # The reconciliation write itself fails during the outage --
        # exactly database.record_payment_reconciliation_issue's own
        # documented best-effort None-on-failure return, never a raise.
        database.record_payment_reconciliation_issue = MagicMock(return_value=None)
        database.get_already_sold_lead_if_matching_session = MagicMock(return_value=None)
        database.get_order_status = MagicMock(return_value=None)
        database.update_order_fulfillment = MagicMock()
        notifications.send_system_incident_alert = MagicMock()
        notifications.send_purchased_lead_email = MagicMock()
        self._stripe_patch = patch("stripe.Refund.create")
        self.mock_refund = self._stripe_patch.start()
        self.addCleanup(self._stripe_patch.stop)

    @patch("stripe.Webhook.construct_event")
    def test_still_retryable_and_not_marked_fulfilled_even_with_no_issue_id(self, mock_construct):
        mock_construct.return_value = _fake_event(event_id="evt_recon_write_fails", lead_id="PLANIT-007",
                                                     reservation_token="tok-recon-write-fails")
        result = payments.handle_stripe_webhook(b"payload", "sig")
        self.assertTrue(result.get("retry"))
        self.assertIsNone(result.get("reconciliation_issue_id"))
        self.assertNotIn("evt_recon_write_fails", payments._PROCESSED_STRIPE_EVENT_IDS)
        self.mock_refund.assert_not_called()
        # The CRITICAL alert is still the backstop for admin visibility
        # even though the durable record didn't get written this time.
        notifications.send_system_incident_alert.assert_called_once()
        _, kwargs = notifications.send_system_incident_alert.call_args
        self.assertEqual(kwargs["severity"], "CRITICAL")


class TestSuccessfulReservationSaleUnaffected(unittest.TestCase):
    """Regression guard: the ordinary success path (no exception at all)
    behaves exactly as before this review's change."""

    def setUp(self):
        payments._PROCESSED_STRIPE_EVENT_IDS.clear()
        database.confirm_reserved_lead_sale = MagicMock(return_value={
            "id": "lead-uuid-1", "reference": "PLANIT-004", "address": "1 Real St",
            "summary": "s", "council_source": "c", "lead_score": 1, "lead_price": 1900,
            "applicant_name": "A", "agent_name": None, "agent_company": None,
            "has_agent": False, "registered_date": None,
        })
        database.update_order_fulfillment = MagicMock()
        database.record_payment_reconciliation_issue = MagicMock()
        notifications.send_system_incident_alert = MagicMock()
        notifications.send_purchased_lead_email = MagicMock()

    @patch("stripe.Webhook.construct_event")
    def test_success_returns_payment_complete_no_retry_flag(self, mock_construct):
        mock_construct.return_value = _fake_event(lead_id="PLANIT-004", reservation_token="tok-good")
        result = payments.handle_stripe_webhook(b"payload", "sig")
        self.assertEqual(result.get("event"), "payment_complete")
        self.assertNotIn("retry", result)
        notifications.send_purchased_lead_email.assert_called_once()
        database.record_payment_reconciliation_issue.assert_not_called()


if __name__ == "__main__":
    unittest.main()

"""
test_payments.py -- Unit tests for payments.py's own failure-alerting.

Sep 3 2026: added during the "predict future issues, even ones that have
never shown an error" failsafe audit. payments.py had NO test file at all
before this -- found while auditing alert coverage across every external
provider (Companies House, GLA, ukplanningapi.co.uk, PlanIt, Stripe,
Resend). This file does not attempt to cover the whole module (a full
Stripe webhook/checkout test suite is a larger, separate undertaking) --
it exists specifically to prove the one fix made this session: an invalid
STRIPE_SECRET_KEY during checkout-session creation now fires a CRITICAL
incident alert instead of only a log line, since this is the function
that IS the customer "buy now" button -- a silent failure here means zero
revenue with nobody ever told.

The real `stripe` PyPI package isn't installed in this sandbox (network
egress to pypi.org is blocked here -- see generate_qr_codes.py's own
docstring for the same constraint hit earlier this session), so `stripe`
is stubbed into sys.modules before importing payments.py, same technique
test_scrapers.py/test_research.py already use for `database`/
`notifications`/`dotenv`. This runs anywhere, no real Stripe account or
network access required.
"""
import os
import sys
import types
import unittest
from unittest.mock import MagicMock, patch

if "dotenv" not in sys.modules:
    _fake_dotenv = types.ModuleType("dotenv")
    _fake_dotenv.load_dotenv = lambda *a, **k: None
    sys.modules["dotenv"] = _fake_dotenv

if "stripe" not in sys.modules:
    _fake_stripe = types.ModuleType("stripe")
    _fake_stripe.api_key = ""

    _fake_stripe_error = types.ModuleType("stripe.error")

    class _StripeError(Exception):
        pass

    class _AuthenticationError(_StripeError):
        pass

    class _SignatureVerificationError(_StripeError):
        pass

    _fake_stripe_error.StripeError = _StripeError
    _fake_stripe_error.AuthenticationError = _AuthenticationError
    _fake_stripe_error.SignatureVerificationError = _SignatureVerificationError
    _fake_stripe.error = _fake_stripe_error

    _fake_checkout = types.ModuleType("stripe.checkout")
    _fake_session = types.ModuleType("stripe.checkout.Session")
    _fake_session.create = MagicMock()
    _fake_checkout.Session = _fake_session
    _fake_stripe.checkout = _fake_checkout

    _fake_stripe.Webhook = MagicMock()

    sys.modules["stripe"] = _fake_stripe
    sys.modules["stripe.error"] = _fake_stripe_error
    sys.modules["stripe.checkout"] = _fake_checkout

if "notifications" not in sys.modules:
    _fake_notifications = types.ModuleType("notifications")
    _fake_notifications.send_system_incident_alert = MagicMock()
    sys.modules["notifications"] = _fake_notifications

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import stripe  # noqa: E402  (the fake module set up above)
import payments  # noqa: E402


class TestCheckoutSessionAuthFailureAlerting(unittest.TestCase):
    """Sep 3 2026: create_checkout_session's stripe.error.AuthenticationError
    branch used to be log-only (see payments.py's own comment at that
    except block for the full reasoning) -- this is the single most
    expensive silent-failure path in the app, since this function IS the
    customer checkout button."""

    def setUp(self):
        stripe.checkout.Session.create.reset_mock()
        sys.modules["notifications"].send_system_incident_alert.reset_mock()

    def test_authentication_error_fires_a_critical_alert(self):
        stripe.checkout.Session.create.side_effect = stripe.error.AuthenticationError("invalid api key")
        with patch.object(payments.stripe, "api_key", "sk_test_fake"):
            result = payments.create_checkout_session("single_lead_small")

        self.assertIsNone(result)
        mock_alert = sys.modules["notifications"].send_system_incident_alert
        mock_alert.assert_called_once()
        _, kwargs = mock_alert.call_args
        self.assertEqual(kwargs.get("severity"), "CRITICAL")
        self.assertIn("STRIPE", kwargs.get("title", "").upper())
        self.assertIn("CHECKOUT", kwargs.get("title", "").upper())

    def test_generic_stripe_error_does_not_fire_the_auth_alert(self):
        """A generic StripeError (e.g. a transient API outage) is a
        different, less actionable problem than a genuinely bad key --
        must not fire the same 'checkout is down, fix your key' alert,
        which would send Nick chasing the wrong fix."""
        stripe.checkout.Session.create.side_effect = stripe.error.StripeError("temporary outage")
        with patch.object(payments.stripe, "api_key", "sk_test_fake"):
            result = payments.create_checkout_session("single_lead_small")

        self.assertIsNone(result)
        sys.modules["notifications"].send_system_incident_alert.assert_not_called()

    def test_successful_checkout_fires_no_alert(self):
        fake_session = MagicMock()
        fake_session.id = "cs_test_123"
        fake_session.url = "https://checkout.stripe.com/fake"
        stripe.checkout.Session.create.side_effect = None
        stripe.checkout.Session.create.return_value = fake_session
        with patch.object(payments.stripe, "api_key", "sk_test_fake"):
            result = payments.create_checkout_session("single_lead_small")

        self.assertEqual(result, "https://checkout.stripe.com/fake")
        sys.modules["notifications"].send_system_incident_alert.assert_not_called()

    def test_no_api_key_configured_returns_none_without_calling_stripe(self):
        with patch.object(payments.stripe, "api_key", ""):
            result = payments.create_checkout_session("single_lead_small")
        self.assertIsNone(result)
        stripe.checkout.Session.create.assert_not_called()


if __name__ == "__main__":
    unittest.main()

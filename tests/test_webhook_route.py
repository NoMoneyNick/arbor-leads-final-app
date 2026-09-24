"""
test_webhook_route.py -- 2026-09-18 review, Section 3: proves main.py's
/webhook route itself maps payments.handle_stripe_webhook's new retry
signal to a retryable HTTP status (503), not a false 200 or the pre-
existing blanket 400 (which would leave a valid-payment-but-failed-write
webhook looking, to Stripe, identical to "this request was permanently
invalid" -- also still non-2xx so Stripe would retry regardless, but the
status code itself would misdescribe the failure to any other consumer of
it, e.g. Render's own request logs/alerting).

Reuses test_access_control.py's exact import convention (test_main.py's
stub set, where `payments` itself is a permissive MagicMock module -- this
file only needs to prove the ROUTE reads payments' return dict correctly,
not payments.py's own internal logic, which test_payments_webhook.py
covers against the real module).

Run with:
    python -m unittest tests.test_webhook_route -v
"""
import asyncio
import os
import sys
import unittest
from unittest.mock import MagicMock, patch

_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
_APP_DIR = os.path.dirname(_THIS_DIR)
sys.path.insert(0, _APP_DIR)
sys.path.insert(0, _THIS_DIR)

import test_access_control  # noqa: E402 -- populates every stub, then imports main
import main  # noqa: E402


def _mock_webhook_request(body: bytes = b"{}", sig: str = "t=1,v1=abc"):
    req = MagicMock()

    async def _body():
        return body
    req.body = _body
    req.headers = {"stripe-signature": sig}
    return req


class TestWebhookRouteRetrySignalling(unittest.TestCase):

    @patch("main.payments.handle_stripe_webhook")
    def test_allocation_persistence_failure_returns_503_not_400(self, mock_handle):
        mock_handle.return_value = {"error": "allocation_persistence_failed", "retry": True,
                                     "reconciliation_issue_id": "issue-uuid-1", "lead_id": "PLANIT-001"}
        request = _mock_webhook_request()
        with self.assertRaises(main.HTTPException) as ctx:
            asyncio.run(main.stripe_webhook(request))
        self.assertEqual(ctx.exception.status_code, 503)

    @patch("main.payments.handle_stripe_webhook")
    def test_signature_error_still_returns_400_not_503(self, mock_handle):
        """A genuinely malformed/unsigned payload is a permanent rejection,
        not a 'please retry, might be transient' situation -- must not be
        swept into the new 503 branch just because it's also an error."""
        mock_handle.return_value = {"error": "Invalid signature"}
        request = _mock_webhook_request()
        with self.assertRaises(main.HTTPException) as ctx:
            asyncio.run(main.stripe_webhook(request))
        self.assertEqual(ctx.exception.status_code, 400)

    @patch("main.payments.handle_stripe_webhook")
    def test_success_returns_ok_status_not_error(self, mock_handle):
        mock_handle.return_value = {"event": "payment_complete", "email": "buyer@example.com",
                                     "session_id": "cs_1", "amount_pence": 1900,
                                     "outcode": "LS1", "lead_id": "PLANIT-001"}
        request = _mock_webhook_request()
        result = asyncio.run(main.stripe_webhook(request))
        self.assertEqual(result["status"], "ok")


if __name__ == "__main__":
    unittest.main()

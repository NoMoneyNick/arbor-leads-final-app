"""Homeowner quote requests are switched off (25 Oct 2026): no storage, no routing, clear message, no response-time promise."""
import asyncio
import json
import os
import sys
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import test_indirect_identifier_gating as _tiig  # noqa: E402,F401  (stubs heavy imports before main loads)
import main  # noqa: E402

MSG = "Quote requests are currently unavailable"


class TestQuoteApiOff(unittest.TestCase):
    def _call(self, env):
        class _Rec:
            def __init__(self, content=None, media_type=None, status_code=200, **k):
                self.body, self.media_type, self.status_code = content, media_type, status_code
        self._rec = _Rec
        request = MagicMock()
        async def _json():
            raise AssertionError("body must not even be read while switched off")
        request.json = _json
        with patch.dict(os.environ, env, clear=False), patch.object(main, "Response", self._rec), \
             patch.object(main.database, "get_db_conn", side_effect=AssertionError("no DB access while off"), create=True):
            return asyncio.run(main.submit_homeowner_quote(request))

    def test_off_by_default_returns_unavailable_and_touches_nothing(self):
        env = {k: v for k, v in os.environ.items() if k != "HOMEOWNER_QUOTES_ENABLED"}
        with patch.dict(os.environ, env, clear=True):
            resp = self._call({})
        self.assertEqual(resp.status_code, 503)
        data = json.loads(resp.body)
        self.assertEqual(data["status"], "unavailable")
        self.assertIn(MSG, data["message"])

    def test_wrong_values_stay_off(self):
        for v in ("", "0", "false", "off", "no"):
            resp = self._call({"HOMEOWNER_QUOTES_ENABLED": v})
            self.assertEqual(resp.status_code, 503, v)


class TestQuotePagesCopy(unittest.TestCase):
    def _pages(self):
        return [main.quote_estimator_page(), main.local_seo_intake_page("leeds")]

    def test_no_promise_and_clear_message(self):
        for page in self._pages():
            page = page if isinstance(page, str) else getattr(page, "body", b"").decode()
            low = page.lower()
            self.assertIn(MSG, page)
            for bad in ("2 business hours", "within 2", "dispatched 1-to-1", "1-to-1 dispatch", "quote request dispatched",
                        "custname", "custphone", "request free", "never sell your details to 5"):
                self.assertNotIn(bad, low)

    def test_failed_submit_never_shows_success(self):
        for page in self._pages():
            page = page if isinstance(page, str) else getattr(page, "body", b"").decode()
            self.assertNotIn("Dispatched", page)


if __name__ == "__main__":
    unittest.main()

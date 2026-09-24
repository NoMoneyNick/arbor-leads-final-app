"""
test_net_utils_capture.py -- 2026-09-22 second review (Astra, relayed by
Nick): tests for net_utils.py's optional fixture-capture hook
(_maybe_capture_response, wired into smart_get/smart_post via
capture_context=...). See that function's own module-level comment in
net_utils.py for the full design (off by default twice over: no
capture_context = nothing happens; NET_UTILS_DISABLE_CAPTURE=1 forces it
off regardless).

Uses a fake session (no real network call) and a temp directory for
NET_UTILS_CAPTURE_DIR so nothing here touches the real
captured_responses/ directory.

Run with:
    python -m unittest tests.test_net_utils_capture -v
"""
import os
import sys
import json
import glob
import shutil
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import net_utils


class FakeResponse:
    def __init__(self, status_code=200, text="<html>ok</html>", url="https://example-council.test/search.do",
                 headers=None, encoding="utf-8", history=None):
        self.status_code = status_code
        self.text = text
        self.url = url
        self.headers = headers if headers is not None else {"Content-Type": "text/html; charset=UTF-8"}
        self.encoding = encoding
        self.history = history or []


class FakeSession:
    """Stands in for requests.Session -- .request(...) returns a
    pre-built FakeResponse instead of making a real network call, and
    .headers mirrors a real Session's default empty headers dict (checked
    by net_utils._request's own User-Agent-injection logic)."""

    def __init__(self, response):
        self._response = response
        self.headers = {}

    def request(self, method, url, verify=True, **kwargs):
        return self._response


class _CaptureTestBase(unittest.TestCase):
    def setUp(self):
        self.tmp_dir = tempfile.mkdtemp(prefix="net_utils_capture_test_")
        self._env_patch = patch.dict(os.environ, {"NET_UTILS_CAPTURE_DIR": self.tmp_dir}, clear=False)
        self._env_patch.start()

    def tearDown(self):
        self._env_patch.stop()
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def _captured_dirs(self):
        return sorted(glob.glob(os.path.join(self.tmp_dir, "**", "meta.json"), recursive=True))


class TestCaptureIsOptInOnly(_CaptureTestBase):
    def test_no_capture_context_writes_nothing(self):
        session = FakeSession(FakeResponse())
        res = net_utils.smart_get("https://example-council.test/search.do", session=session)
        self.assertEqual(res.status_code, 200)
        self.assertEqual(self._captured_dirs(), [])

    def test_env_kill_switch_blocks_capture_even_with_context(self):
        with patch.dict(os.environ, {"NET_UTILS_DISABLE_CAPTURE": "1"}):
            session = FakeSession(FakeResponse())
            net_utils.smart_get(
                "https://example-council.test/search.do", session=session,
                capture_context={"council": "Bristol", "platform": "idox"},
            )
        self.assertEqual(self._captured_dirs(), [])


class TestCaptureWritesFixture(_CaptureTestBase):
    def test_writes_body_and_meta_under_council_directory(self):
        session = FakeSession(FakeResponse(text="<html><body>search results</body></html>"))
        net_utils.smart_get(
            "https://example-council.test/search.do", session=session,
            capture_context={"council": "Bristol", "platform": "idox", "page_kind": "search_results",
                              "search_parameters": {"week": "2026-09-15"}},
        )
        captured = self._captured_dirs()
        self.assertEqual(len(captured), 1)
        case_dir = os.path.dirname(captured[0])
        self.assertIn(os.path.join(self.tmp_dir, "Bristol"), case_dir)

        with open(os.path.join(case_dir, "body.html")) as f:
            self.assertIn("search results", f.read())

        with open(captured[0]) as f:
            meta = json.load(f)
        self.assertEqual(meta["provenance"], "captured")
        self.assertEqual(meta["council"], "Bristol")
        self.assertEqual(meta["platform"], "idox")
        self.assertEqual(meta["response"]["status"], 200)
        self.assertEqual(meta["request"]["search_parameters"], {"week": "2026-09-15"})
        self.assertFalse(meta["sanitisation"]["applied"])
        self.assertIn("NOT yet reviewed", meta["sanitisation"]["notes"])

    def test_never_writes_expected_json(self):
        """The whole point, per tests/fixtures/idox/README.md: a captured
        response is never auto-promoted into a trusted fixture -- a human
        must write expected.json themselves."""
        session = FakeSession(FakeResponse())
        net_utils.smart_get(
            "https://example-council.test/search.do", session=session,
            capture_context={"council": "Bristol", "platform": "idox"},
        )
        case_dir = os.path.dirname(self._captured_dirs()[0])
        self.assertFalse(os.path.exists(os.path.join(case_dir, "expected.json")))

    def test_uncategorized_council_when_context_omits_it(self):
        session = FakeSession(FakeResponse())
        net_utils.smart_get(
            "https://example-council.test/search.do", session=session,
            capture_context={"platform": "idox"},
        )
        case_dir = os.path.dirname(self._captured_dirs()[0])
        self.assertIn(os.path.join(self.tmp_dir, "_uncategorized"), case_dir)

    def test_captures_error_responses_too(self):
        """A 429/503/challenge response is exactly the kind of real
        response this hook exists to bootstrap fixtures from -- it must
        not only capture the "happy path"."""
        session = FakeSession(FakeResponse(status_code=429, text="Too Many Requests",
                                            headers={"Content-Type": "text/html", "Retry-After": "120"}))
        net_utils.smart_get(
            "https://example-council.test/search.do", session=session,
            capture_context={"council": "Bristol", "platform": "idox"},
        )
        with open(self._captured_dirs()[0]) as f:
            meta = json.load(f)
        self.assertEqual(meta["response"]["status"], 429)
        self.assertEqual(meta["response"]["headers"].get("Retry-After"), "120")


class TestCaptureSkipsNonTextualContent(_CaptureTestBase):
    def test_skips_image_content_type(self):
        session = FakeSession(FakeResponse(headers={"Content-Type": "image/png"}))
        net_utils.smart_get(
            "https://example-council.test/document.png", session=session,
            capture_context={"council": "Bristol", "platform": "idox"},
        )
        self.assertEqual(self._captured_dirs(), [])


class TestCaptureFailsSafe(_CaptureTestBase):
    def test_capture_error_does_not_raise_or_affect_the_returned_response(self):
        session = FakeSession(FakeResponse(text="<html>ok</html>"))
        with patch("net_utils.os.makedirs", side_effect=OSError("disk full")):
            res = net_utils.smart_get(
                "https://example-council.test/search.do", session=session,
                capture_context={"council": "Bristol", "platform": "idox"},
            )
        # The real fetch must still succeed and be returned normally, even
        # though the capture hook itself blew up.
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.text, "<html>ok</html>")


if __name__ == "__main__":
    unittest.main()

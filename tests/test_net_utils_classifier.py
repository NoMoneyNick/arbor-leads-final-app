"""
test_net_utils_classifier.py -- 2026-09-22 second review (Astra, relayed by
Nick): tests for net_utils.classify_response, both direct unit tests and
against every fixture in tests/fixtures/idox/ (see that directory's own
README.md for the fixture format and what "synthetic" means there).

Run with:
    python -m unittest tests.test_net_utils_classifier -v
"""
import os
import sys
import json
import glob
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import net_utils

_FIXTURES_ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures", "idox")


def _load_fixture(case_dir):
    with open(os.path.join(case_dir, "meta.json")) as f:
        meta = json.load(f)
    with open(os.path.join(case_dir, "expected.json")) as f:
        expected = json.load(f)
    body_path = os.path.join(case_dir, "body.html")
    body_text = None
    if os.path.exists(body_path):
        with open(body_path) as f:
            body_text = f.read()
    return meta, expected, body_text


def _all_fixture_dirs():
    # Every directory under tests/fixtures/idox/ that directly contains a
    # meta.json -- works for both tests/fixtures/idox/_synthetic/<case>/
    # and, in future, tests/fixtures/idox/<council>/<case>/ real captures.
    return sorted(
        os.path.dirname(p) for p in glob.glob(os.path.join(_FIXTURES_ROOT, "**", "meta.json"), recursive=True)
    )


class TestClassifyResponseDirect(unittest.TestCase):
    """Direct unit tests, independent of any fixture file, so the
    classifier's own logic is covered even if the fixture set changes."""

    def test_200_with_no_signal_is_page_ok(self):
        self.assertEqual(net_utils.classify_response(status_code=200), "PAGE_OK")

    def test_200_with_known_no_results_hint_is_valid_empty(self):
        self.assertEqual(
            net_utils.classify_response(status_code=200, known_no_results=True), "VALID_EMPTY"
        )

    def test_never_infers_valid_empty_without_the_explicit_hint(self):
        """The module-level comment's own point: this function must never
        guess "no results" from body text alone -- only an explicit,
        caller-supplied hint can produce VALID_EMPTY."""
        self.assertNotEqual(
            net_utils.classify_response(status_code=200, body_text="No applications found for this search."),
            "VALID_EMPTY",
        )

    def test_200_with_empty_body_is_unrecognised(self):
        self.assertEqual(
            net_utils.classify_response(status_code=200, body_text="   "), "UNRECOGNISED_PAGE"
        )

    def test_429_is_rate_limited(self):
        self.assertEqual(net_utils.classify_response(status_code=429), "RATE_LIMITED")

    def test_401_and_403_are_auth_required(self):
        self.assertEqual(net_utils.classify_response(status_code=401), "AUTH_REQUIRED")
        self.assertEqual(net_utils.classify_response(status_code=403), "AUTH_REQUIRED")

    def test_5xx_retryable_statuses_are_server_error(self):
        for code in (500, 502, 503, 504):
            self.assertEqual(net_utils.classify_response(status_code=code), "SERVER_ERROR")

    def test_unmapped_status_is_unrecognised(self):
        self.assertEqual(net_utils.classify_response(status_code=418), "UNRECOGNISED_PAGE")

    def test_cf_mitigated_challenge_header_wins_even_on_403(self):
        """A Cloudflare challenge can arrive on a 403 -- the same status
        AUTH_REQUIRED would otherwise claim. The header must take
        precedence, per classify_response's own docstring on ordering."""
        result = net_utils.classify_response(status_code=403, headers={"cf-mitigated": "challenge"})
        self.assertEqual(result, "CHALLENGED")

    def test_header_lookup_is_case_insensitive(self):
        result = net_utils.classify_response(status_code=200, headers={"CF-Mitigated": "challenge"})
        self.assertEqual(result, "CHALLENGED")

    def test_unrelated_headers_do_not_trigger_challenge(self):
        result = net_utils.classify_response(status_code=200, headers={"Content-Type": "text/html"})
        self.assertEqual(result, "PAGE_OK")


class TestClassifyResponseAgainstFixtures(unittest.TestCase):
    """Every fixture in tests/fixtures/idox/ must classify exactly the way
    its own expected.json says -- this is what actually catches drift if
    classify_response's logic ever changes in a way that breaks one of
    these saved cases."""

    def test_fixture_directory_is_not_empty(self):
        # Sanity check the discovery glob itself -- an empty result would
        # make every test below vacuously pass.
        self.assertGreater(len(_all_fixture_dirs()), 0, "no fixtures found under tests/fixtures/idox/")

    def test_every_fixture_classifies_as_expected(self):
        for case_dir in _all_fixture_dirs():
            meta, expected, body_text = _load_fixture(case_dir)
            with self.subTest(case=os.path.relpath(case_dir, _FIXTURES_ROOT)):
                result = net_utils.classify_response(
                    status_code=meta["response"]["status"],
                    headers=meta["response"].get("headers"),
                    body_text=body_text,
                )
                self.assertEqual(
                    result, expected["classification"],
                    f"{case_dir}: expected {expected['classification']!r}, got {result!r}",
                )

    def test_every_fixture_declares_its_provenance(self):
        """README.md's own rule: every fixture must say whether it's a real
        capture or a hand-built synthetic one -- this guards against a
        future fixture being added without that field."""
        for case_dir in _all_fixture_dirs():
            meta, _expected, _body = _load_fixture(case_dir)
            with self.subTest(case=os.path.relpath(case_dir, _FIXTURES_ROOT)):
                self.assertIn(meta.get("provenance"), ("captured", "synthetic"))

    def test_synthetic_fixtures_are_filed_under_the_synthetic_council_name(self):
        """README.md's own rule: a synthetic fixture's council must be
        literally '_synthetic', so it's never mistaken for a real
        council's response just by glancing at the directory name."""
        for case_dir in _all_fixture_dirs():
            meta, _expected, _body = _load_fixture(case_dir)
            if meta.get("provenance") == "synthetic":
                with self.subTest(case=os.path.relpath(case_dir, _FIXTURES_ROOT)):
                    self.assertEqual(meta.get("council"), "_synthetic")


if __name__ == "__main__":
    unittest.main()

"""
test_council_health_check_retry.py -- 2026-09-22 review: verifies the retry
added to main._run_council_health_check (the background worker behind
/system-health-check). A single failed fetch used to be reported as
"error" immediately, with no distinction from a genuinely dead council --
exactly the kind of one-off network blip net_utils.smart_get already
retries for every real scan (see that module's own docstring). This adds
one retry, after a short pause, before concluding a council is actually
failing.

mesh_scrapers.py (the module that contains the real council registries and
scrape functions) does not exist anywhere in this dev environment -- see
ERROR_LOG.md's first entry. This test drives main._run_council_health_check
against a hand-built FAKE mesh_scrapers module (stubbed into sys.modules,
same convention test_access_control.py already uses for scanners/research/
payments), so the retry LOOP itself is genuinely exercised even though the
real scraper code can't be.

Run with:
    python -m unittest tests.test_council_health_check_retry -v
"""
import os
import sys
import types
import unittest
from unittest.mock import patch

_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
_APP_DIR = os.path.dirname(_THIS_DIR)
sys.path.insert(0, _APP_DIR)
sys.path.insert(0, _THIS_DIR)

import test_access_control  # noqa: E402  (populates sys.modules stubs + imports test_main + main)
import main  # noqa: E402


def _make_fake_mesh_scrapers(council_registry, scrape_mesh_council_fn):
    """Builds a minimal fake mesh_scrapers module with exactly the
    attributes main._all_mesh_health_targets reads. The four dict-based
    registries besides COUNCIL_REGISTRY are left empty, and the seven
    bespoke single-council platforms are given trivial always-succeeds
    functions, so this test's assertions stay entirely about
    COUNCIL_REGISTRY's own entries (which the caller controls)."""
    mod = types.ModuleType("mesh_scrapers")
    mod.COUNCIL_REGISTRY = council_registry
    mod.NORTHGATE_COUNCILS = {}
    mod.AGILE_APPLICATIONS_COUNCILS = {}
    mod.ARCUS_COUNCILS = {}
    mod.scrape_mesh_council = scrape_mesh_council_fn
    mod.scrape_northgate_council = lambda name: []
    mod.scrape_agile_applications_council = lambda name: []
    mod.scrape_arcus_council = lambda name: []

    for base_attr, fn_attr in [
        ("HOUNSLOW_BASE", "scrape_hounslow_council"),
        ("NORTH_YORK_MOORS_BASE", "scrape_north_york_moors"),
        ("HAVERING_BASE", "scrape_havering_council"),
        ("ST_ALBANS_BASE", "scrape_st_albans_council"),
        ("RBKC_BASE", "scrape_kensington_chelsea_council"),
        ("DORSET_BASE", "scrape_dorset_council"),
        ("STRATFORD_BASE", "scrape_stratford_on_avon_council"),
    ]:
        setattr(mod, base_attr, f"http://example.com/{base_attr.lower()}")
        setattr(mod, fn_attr, lambda: [])
    return mod


class TestCouncilHealthCheckRetry(unittest.TestCase):
    def setUp(self):
        self._orig_mesh_scrapers = sys.modules.get("mesh_scrapers")
        self._sleep_patch = patch("main.time.sleep")  # keep the test fast -- real retry/courtesy delays aren't the point under test
        self._sleep_patch.start()
        main._health_check_state["results"] = []
        main._health_check_state["summary"] = None

    def tearDown(self):
        self._sleep_patch.stop()
        if self._orig_mesh_scrapers is not None:
            sys.modules["mesh_scrapers"] = self._orig_mesh_scrapers
        else:
            sys.modules.pop("mesh_scrapers", None)

    def test_council_that_fails_once_then_succeeds_is_reported_ok_and_recovered(self):
        call_count = {"TESTCOUNCIL_RETRY_OK": 0}

        def scrape(name):
            call_count[name] += 1
            if name == "TESTCOUNCIL_RETRY_OK" and call_count[name] == 1:
                raise ConnectionError("simulated one-off network blip")
            return ["lead-1", "lead-2"]

        sys.modules["mesh_scrapers"] = _make_fake_mesh_scrapers(
            {"TESTCOUNCIL_RETRY_OK": "http://example.com/retry-ok"}, scrape
        )

        main._run_council_health_check()

        results = main._health_check_state["results"]
        target = next(r for r in results if r["council"] == "TESTCOUNCIL_RETRY_OK")
        self.assertEqual(target["status"], "ok")
        self.assertEqual(target["leads_found"], 2)
        self.assertEqual(target["attempts"], 2)
        self.assertTrue(target["recovered_on_retry"])
        self.assertEqual(call_count["TESTCOUNCIL_RETRY_OK"], 2)

        # The summary must count this as reachable, not failing -- the
        # entire point of the retry is that a recovered council is not
        # reported as broken.
        summary = main._health_check_state["summary"]
        self.assertIn("TESTCOUNCIL_RETRY_OK", [r for r in [target["council"]]])
        self.assertNotIn("TESTCOUNCIL_RETRY_OK", summary["failing_councils"])

    def test_council_that_fails_both_attempts_is_reported_as_error_with_both_errors_recorded(self):
        call_count = {"TESTCOUNCIL_ALWAYS_FAILS": 0}

        def scrape(name):
            call_count[name] += 1
            raise TimeoutError(f"simulated persistent failure, attempt {call_count[name]}")

        sys.modules["mesh_scrapers"] = _make_fake_mesh_scrapers(
            {"TESTCOUNCIL_ALWAYS_FAILS": "http://example.com/always-fails"}, scrape
        )

        main._run_council_health_check()

        results = main._health_check_state["results"]
        target = next(r for r in results if r["council"] == "TESTCOUNCIL_ALWAYS_FAILS")
        self.assertEqual(target["status"], "error")
        self.assertEqual(target["attempts"], 2)
        self.assertIn("attempt 1", target["first_attempt_error"])
        self.assertIn("attempt 2", target["error"])
        self.assertEqual(call_count["TESTCOUNCIL_ALWAYS_FAILS"], 2)

        summary = main._health_check_state["summary"]
        self.assertIn("TESTCOUNCIL_ALWAYS_FAILS", summary["failing_councils"])

    def test_council_that_succeeds_first_try_makes_only_one_attempt(self):
        call_count = {"TESTCOUNCIL_FINE": 0}

        def scrape(name):
            call_count[name] += 1
            return []

        sys.modules["mesh_scrapers"] = _make_fake_mesh_scrapers(
            {"TESTCOUNCIL_FINE": "http://example.com/fine"}, scrape
        )

        main._run_council_health_check()

        results = main._health_check_state["results"]
        target = next(r for r in results if r["council"] == "TESTCOUNCIL_FINE")
        self.assertEqual(target["status"], "ok")
        self.assertEqual(target["attempts"], 1)
        self.assertFalse(target["recovered_on_retry"])
        self.assertEqual(call_count["TESTCOUNCIL_FINE"], 1)  # no wasted retry when the first attempt already succeeded


if __name__ == "__main__":
    unittest.main()

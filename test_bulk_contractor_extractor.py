"""
Sep 2 2026, master_expansion_plan_v2.md §8 step 5: pure-logic + mocked-network
tests for bulk_contractor_extractor.py's generalization from a tree-only
script to a CONTRACTOR_VERTICALS-driven tool (tree + hmo today). No real
Companies House/DuckDuckGo calls -- this file has no COMPANIES_HOUSE_KEY in
this sandbox to test against live, matching the "no live network calls" rule
test_scrapers.py already documents for this codebase's test suite.
"""

import os
import sys
import unittest
from unittest.mock import patch, MagicMock

sys.path.insert(0, os.path.dirname(__file__))
import bulk_contractor_extractor as bce


class TestContractorVerticalsConfig(unittest.TestCase):
    """The config itself, before any code runs against it -- catches a typo'd
    or missing key before it ever reaches a real harvest run."""

    def test_both_verticals_registered(self):
        self.assertEqual(set(bce.CONTRACTOR_VERTICALS.keys()), {"tree", "hmo"})

    def test_every_vertical_has_all_required_keys(self):
        required_keys = {
            "search_queries", "excluded_words", "required_words",
            "sic_codes_trusted", "sic_codes_gated",
            "default_output_csv", "google_search_trade_label",
        }
        for name, config in bce.CONTRACTOR_VERTICALS.items():
            with self.subTest(vertical=name):
                self.assertTrue(required_keys.issubset(config.keys()))
                self.assertTrue(config["search_queries"], "must have at least one search query")
                self.assertTrue(config["default_output_csv"].endswith(".csv"))

    def test_output_csv_filenames_are_distinct_per_vertical(self):
        """A copy-paste slip here would mean two verticals silently overwrite
        the same CSV file."""
        filenames = [c["default_output_csv"] for c in bce.CONTRACTOR_VERTICALS.values()]
        self.assertEqual(len(filenames), len(set(filenames)))

    def test_hmo_construction_sic_codes_are_trusted_not_name_gated(self):
        """Per master_expansion_plan_v2.md line 114 / the module comment: the
        specific construction-trade SIC codes are genuine standalone signals
        (a builder's name rarely mentions HMOs), only the broad property-
        management codes need the extra name check."""
        hmo = bce.CONTRACTOR_VERTICALS["hmo"]
        for code in ["41202", "41100", "43999", "43390", "43210", "43220"]:
            self.assertIn(code, hmo["sic_codes_trusted"])
        for code in ["68320", "68209"]:
            self.assertIn(code, hmo["sic_codes_gated"])
        # And no overlap between the two tiers for any vertical.
        for name, config in bce.CONTRACTOR_VERTICALS.items():
            with self.subTest(vertical=name):
                self.assertFalse(set(config["sic_codes_trusted"]) & set(config["sic_codes_gated"]))

    def test_hmo_excluded_words_do_not_wrongly_exclude_legitimate_hmo_trades(self):
        """The bug this guards against: tree's exclusion list drops plumbing/
        roofing/scaffolding/electrical-adjacent words because those are
        irrelevant to tree surgery -- but HMO conversion work genuinely
        involves electricians and plumbers (that's the whole reason
        43210/43220 are two of its own converged SIC codes), so HMO's list
        must not inherit those tree-specific exclusions."""
        hmo_excluded = bce.CONTRACTOR_VERTICALS["hmo"]["excluded_words"]
        for word in ["plumbing", "roofing", "scaffolding", "electrical", "auto", "garage"]:
            self.assertNotIn(word, hmo_excluded)


class TestIsValidContractor(unittest.TestCase):

    def test_tree_vertical_matches_a_real_tree_company_name(self):
        self.assertTrue(bce.is_valid_contractor("Ridgeline Tree Surgery Ltd", "tree"))

    def test_tree_vertical_excludes_a_medical_surgery_false_positive(self):
        """The exact false-positive class this exclusion list exists for --
        'surgery' alone would otherwise match dental/medical practices."""
        self.assertFalse(bce.is_valid_contractor("Riverside Dental Surgery Ltd", "tree"))

    def test_tree_vertical_rejects_a_name_with_no_tree_word_at_all(self):
        self.assertFalse(bce.is_valid_contractor("Acme Plumbing Ltd", "tree"))

    def test_hmo_vertical_matches_a_real_hmo_compliance_company_name(self):
        self.assertTrue(bce.is_valid_contractor("Acme HMO Compliance Ltd", "hmo"))

    def test_hmo_vertical_matches_licensing_and_lettings_terms(self):
        self.assertTrue(bce.is_valid_contractor("Landlord Licensing Solutions Ltd", "hmo"))
        self.assertTrue(bce.is_valid_contractor("Prime Residential Lettings Ltd", "hmo"))

    def test_hmo_vertical_rejects_a_generic_builder_name_with_no_hmo_signal(self):
        """The precision half: 'build'/'construction'/'contractor' are
        deliberately NOT required_words (see the module comment) -- a bare
        generic builder name with no HMO/compliance/licensing signal at all
        must not pass the name-gate."""
        self.assertFalse(bce.is_valid_contractor("Acme Construction Ltd", "hmo"))

    def test_hmo_vertical_still_excludes_generic_irrelevant_businesses(self):
        self.assertFalse(bce.is_valid_contractor("HMO Dental Practice Ltd", "hmo"))

    def test_unknown_vertical_returns_false_not_an_exception(self):
        self.assertFalse(bce.is_valid_contractor("Anything Ltd", "not-a-real-vertical"))

    def test_backward_compatible_tree_wrapper_still_works(self):
        self.assertTrue(bce.is_valid_tree_company("Ridgeline Tree Surgery Ltd"))
        self.assertFalse(bce.is_valid_tree_company("Acme Plumbing Ltd"))


class TestRunBulkExtractionVerticalWiring(unittest.TestCase):
    """Full-pipeline wiring, with every network call mocked -- proves
    run_bulk_extraction actually uses the vertical's own config throughout
    (search terms, SIC tiers, output filename, trade label) rather than the
    old hardcoded tree-only globals."""

    def setUp(self):
        # Keep the harvest small and fast: one region, one town, one query.
        self.region_patch = patch.object(bce, "UK_TARGET_REGIONS", [
            {"region": "Test Region", "country": "England", "terms": ["Testville"]}
        ])
        self.region_patch.start()
        self.addCleanup(self.region_patch.stop)

    def test_hmo_run_uses_hmo_search_queries_and_writes_hmo_csv(self):
        seen_queries = []

        def fake_search(query, items_per_page=50):
            seen_queries.append(query)
            return [{
                "company_name": "Test HMO Compliance Ltd",
                "company_number": "12345678",
                "registered_office_address": {"postal_code": "TE1 1ST"},
                "sic_codes": ["68320"],
            }]

        with patch.object(bce, "search_companies_house", side_effect=fake_search), \
             patch.object(bce, "search_companies_house_by_sic", return_value=[]), \
             patch.object(bce, "get_director_from_ch", return_value=None), \
             patch.object(bce, "enrich_with_google_places", return_value={}) as mock_enrich, \
             patch("builtins.open", MagicMock()), \
             patch("csv.writer") as mock_csv_writer:
            bce.run_bulk_extraction(vertical="hmo", target_count=10)

        # Confirms the HMO search terms (not tree's) actually drove the harvest.
        self.assertTrue(any("hmo" in q.lower() for q in seen_queries))
        self.assertFalse(any("tree surgery" in q.lower() for q in seen_queries))
        # Confirms the HMO trade label was passed through to enrichment.
        mock_enrich.assert_called()
        _, kwargs = mock_enrich.call_args
        self.assertEqual(kwargs.get("trade_label"), "HMO conversion contractor")

    def test_tree_run_is_completely_unaffected_by_the_generalization(self):
        """The original no-argument call path (python bulk_contractor_extractor.py
        with nothing else) must behave exactly as before."""
        seen_queries = []

        def fake_search(query, items_per_page=50):
            seen_queries.append(query)
            return []

        with patch.object(bce, "search_companies_house", side_effect=fake_search), \
             patch.object(bce, "search_companies_house_by_sic", return_value=[]), \
             patch("builtins.open", MagicMock()), \
             patch("csv.writer"):
            bce.run_bulk_extraction()  # no args at all, same as the CLI default

        self.assertTrue(any("tree surgery" in q.lower() for q in seen_queries))

    def test_unknown_vertical_raises_instead_of_silently_defaulting_to_tree(self):
        with self.assertRaises(ValueError):
            bce.run_bulk_extraction(vertical="not-a-real-vertical")

    def test_output_csv_defaults_to_the_vertical_specific_filename(self):
        with patch.object(bce, "search_companies_house", return_value=[]), \
             patch.object(bce, "search_companies_house_by_sic", return_value=[]), \
             patch("builtins.open", MagicMock()) as mock_open, \
             patch("csv.writer"):
            bce.run_bulk_extraction(vertical="hmo", target_count=10)

        opened_path = mock_open.call_args.args[0]
        self.assertEqual(opened_path, bce.CONTRACTOR_VERTICALS["hmo"]["default_output_csv"])
        self.assertNotEqual(opened_path, bce.CONTRACTOR_VERTICALS["tree"]["default_output_csv"])


if __name__ == "__main__":
    unittest.main(verbosity=2)

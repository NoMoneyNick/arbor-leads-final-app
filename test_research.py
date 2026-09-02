"""
test_research.py -- Unit tests for the partner tagging system in research.py
(business kind, director/phone/email sanity checks, contact:reachable vs
contact:dead). See test_scrapers.py's TestLeadTagging for the equivalent
lead-side tests, and test_database.py's TestPartnerTagQuerying for the
backfill/report side.

Sep 2 2026: Nick's call -- a partner with no working phone and no working
email is dead to the business exactly like an unclassified lead, and that
has to be a queryable, tagged fact rather than something only visible by
eyeballing NULL columns. This file only tests the pure classification
functions (_is_realistic_uk_phone, _is_realistic_email,
_classify_business_kind, _generate_partner_tags) -- no real network call,
no live database, no API keys required.

research.py imports `database` (needs psycopg2, a real Postgres driver) at
module load time. Same technique as test_scrapers.py: stub `database`,
`notifications`, and `dotenv` in sys.modules before importing research.py,
so this runs anywhere.
"""
import os
import sys
import types
import unittest

if "database" not in sys.modules:
    _fake_database = types.ModuleType("database")
    _fake_database.get_db_conn = lambda *a, **k: None
    sys.modules["database"] = _fake_database

if "notifications" not in sys.modules:
    _fake_notifications = types.ModuleType("notifications")
    sys.modules["notifications"] = _fake_notifications

if "dotenv" not in sys.modules:
    _fake_dotenv = types.ModuleType("dotenv")
    _fake_dotenv.load_dotenv = lambda *a, **k: None
    sys.modules["dotenv"] = _fake_dotenv

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import research  # noqa: E402


class TestRealisticPhoneCheck(unittest.TestCase):
    def test_accepts_plausible_uk_numbers(self):
        self.assertTrue(research._is_realistic_uk_phone("020 7946 0958"))
        self.assertTrue(research._is_realistic_uk_phone("07911 123456"))
        self.assertTrue(research._is_realistic_uk_phone("+44 20 7946 0958"))
        self.assertTrue(research._is_realistic_uk_phone("00 44 20 7946 0958"))

    def test_rejects_missing_and_malformed(self):
        self.assertFalse(research._is_realistic_uk_phone(None))
        self.assertFalse(research._is_realistic_uk_phone(""))
        self.assertFalse(research._is_realistic_uk_phone("12345"))  # too short
        self.assertFalse(research._is_realistic_uk_phone("020 7946 09581234"))  # too long

    def test_rejects_known_placeholder_and_repeated_digit_numbers(self):
        self.assertFalse(research._is_realistic_uk_phone("00000000000"))
        self.assertFalse(research._is_realistic_uk_phone("01234567890"))
        self.assertFalse(research._is_realistic_uk_phone("07000000000"))
        self.assertFalse(research._is_realistic_uk_phone("00111111111"))  # second digit 0


class TestRealisticEmailCheck(unittest.TestCase):
    def test_accepts_plausible_emails(self):
        self.assertTrue(research._is_realistic_email("info@bromleytreeservices.co.uk"))
        self.assertTrue(research._is_realistic_email("J.Smith@example-arb.com"))

    def test_rejects_missing_and_malformed(self):
        self.assertFalse(research._is_realistic_email(None))
        self.assertFalse(research._is_realistic_email(""))
        self.assertFalse(research._is_realistic_email("not-an-email"))
        self.assertFalse(research._is_realistic_email("missing-domain@"))

    def test_rejects_known_placeholder_domains(self):
        self.assertFalse(research._is_realistic_email("info@example.com"))
        self.assertFalse(research._is_realistic_email("contact@test.com"))
        self.assertFalse(research._is_realistic_email("hello@yourcompany.com"))


class TestClassifyBusinessKind(unittest.TestCase):
    def test_known_sic_divisions_map_to_expected_bucket(self):
        self.assertEqual(research._classify_business_kind(["81300"]), "landscaping-grounds-maintenance")
        self.assertEqual(research._classify_business_kind(["02100"]), "forestry-agriculture")
        self.assertEqual(research._classify_business_kind(["43999"]), "construction-specialist-trade")

    def test_first_matching_code_wins_when_multiple_present(self):
        self.assertEqual(research._classify_business_kind(["96090", "81300"]), "other-personal-service")

    def test_unknown_or_empty_sic_codes_are_unclassified_not_guessed(self):
        self.assertEqual(research._classify_business_kind([]), "unclassified")
        self.assertEqual(research._classify_business_kind(None), "unclassified")
        self.assertEqual(research._classify_business_kind(["99999"]), "unclassified")


class TestGeneratePartnerTags(unittest.TestCase):
    def test_full_example_with_all_contact_details_present(self):
        tags = research._generate_partner_tags(
            ["81300"], "Jane Smith", "020 7946 0958", "jane@realtreecompany.co.uk"
        )
        self.assertIn("business:landscaping-grounds-maintenance", tags)
        self.assertIn("director:yes", tags)
        self.assertIn("phone:yes", tags)
        self.assertIn("email:yes", tags)
        self.assertIn("contact:reachable", tags)
        self.assertNotIn("contact:dead", tags)

    def test_reachable_via_phone_alone_is_not_dead(self):
        """Nick's rule is 'no phone AND no email' -- having just one real
        channel is enough to not be a dead partner."""
        tags = research._generate_partner_tags([], None, "020 7946 0958", None)
        self.assertIn("contact:reachable", tags)
        self.assertIn("email:no", tags)
        self.assertIn("director:no", tags)

    def test_no_phone_and_no_email_is_tagged_dead(self):
        tags = research._generate_partner_tags(["81300"], "Jane Smith", None, None)
        self.assertIn("contact:dead", tags)
        self.assertNotIn("contact:reachable", tags)

    def test_junk_phone_and_junk_email_still_count_as_dead(self):
        """A non-NULL column full of placeholder junk must not be mistaken
        for a real, reachable contact."""
        tags = research._generate_partner_tags([], None, "01234567890", "info@example.com")
        self.assertIn("phone:no", tags)
        self.assertIn("email:no", tags)
        self.assertIn("contact:dead", tags)


if __name__ == "__main__":
    unittest.main()

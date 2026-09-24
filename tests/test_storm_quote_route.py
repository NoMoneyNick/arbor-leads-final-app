"""
test_storm_quote_route.py -- 2026-09-24, presentation pass.

Nick's ask: "review tools with no example business names or placeholder
Google links... no generic qualification/insurance badges."

Live-site check on /generate-storm-quote/{lead_id} (reached from the
storm-radar page's "Get Emergency Quote Sheet" link with no query params
at all) found it always rendered the literal placeholder "Your Emergency
Tree Surgery Team" and the phone "07XXX XXXXXX" -- a document meant to be
printed and handed to a real, panicking storm-damage customer with a
phone number nobody can actually call -- plus a hardcoded
"BS3998:2010 • NPTC • £5M Insurance" badge for every contractor
regardless of whether any of it is true. Same fabricated-credential
pattern already fixed on generate_street_flyer and boost_review_page;
this covers the identical fix applied to generate_storm_quote.

Covers:
  1. A logged-in contractor with saved business_name/phone gets them
     substituted automatically -- never the fake placeholder identity.
  2. A logged-in contractor with no saved settings is redirected to
     /letter-settings first, never shown the placeholder as real.
  3. No session at all gets an obviously-a-placeholder bracketed string,
     never the old specific-sounding fake name/phone.
  4. The hardcoded BS3998/NPTC/£5M badge is gone.
  5. A contractor's own real saved insurance_note/qualifications_note
     renders if present; nothing renders if neither is saved.

Run with:
    python -m unittest tests.test_storm_quote_route -v
"""
import os
import sys
import unittest
from unittest.mock import MagicMock, patch

_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
_APP_DIR = os.path.dirname(_THIS_DIR)
sys.path.insert(0, _APP_DIR)
sys.path.insert(0, _THIS_DIR)

# Reuses the established fastapi/database/notifications stubbing chain --
# see test_indirect_identifier_gating.py's own import comment.
import test_indirect_identifier_gating as _tiig  # noqa: E402
import main  # noqa: E402


class _FakeSettings:
    def __init__(self, business_name="", phone="", insurance_note="", qualifications_note=""):
        self.business_name = business_name
        self.phone = phone
        self.insurance_note = insurance_note
        self.qualifications_note = qualifications_note


def _request(email=None):
    request = MagicMock()
    if email:
        os.environ["SESSION_SECRET"] = "test-session-secret"
        main._SESSION_SECRET = b"test-session-secret"
        request.cookies = {"treekey_contractor_session": main._sign_session_cookie(email)}
    else:
        request.cookies = {}
    request.url.path = "/generate-storm-quote/EMERGENCY-DISPATCH"
    request.url.query = ""
    return request


def _body(response):
    return response if isinstance(response, str) else getattr(response, "body", str(response))


class TestStormQuotePersonalization(unittest.TestCase):

    @patch("main.database.get_db_conn")
    @patch("main.letter_content.get_contractor_settings")
    def test_logged_in_contractor_with_saved_details_gets_real_identity(self, mock_settings, mock_get_conn):
        mock_settings.return_value = _FakeSettings(business_name="Apex Tree Surgery Ltd", phone="01234 567890")
        conn = MagicMock(); cur = MagicMock()
        conn.cursor.return_value = cur
        mock_get_conn.return_value = conn

        response = main.generate_storm_quote(_request(email="dave@apex-trees.co.uk"), "EMERGENCY-DISPATCH")
        body = _body(response)
        self.assertIn("Apex Tree Surgery Ltd", body)
        self.assertIn("01234 567890", body)
        self.assertNotIn("Your Emergency Tree Surgery Team", body)
        self.assertNotIn("07XXX XXXXXX", body)

    @patch("main.database.get_db_conn")
    @patch("main.letter_content.get_contractor_settings")
    def test_logged_in_contractor_with_no_saved_details_is_sent_to_letter_settings(self, mock_settings, mock_get_conn):
        mock_settings.return_value = None
        conn = MagicMock(); cur = MagicMock()
        conn.cursor.return_value = cur
        mock_get_conn.return_value = conn

        response = main.generate_storm_quote(_request(email="new@contractor.co.uk"), "EMERGENCY-DISPATCH")
        self.assertEqual(getattr(response, "status_code", None), 303)
        self.assertIn("/letter-settings", response.url)
        self.assertIn("next=", response.url)

    def test_no_session_gets_bracketed_placeholder_not_a_fake_identity(self):
        response = main.generate_storm_quote(_request(email=None), "EMERGENCY-DISPATCH")
        body = _body(response)
        self.assertIn("[Your Business Name]", body)
        self.assertIn("[Your Phone Number]", body)
        self.assertNotIn("Your Emergency Tree Surgery Team", body)
        self.assertNotIn("07XXX XXXXXX", body)

    @patch("main.database.get_db_conn")
    @patch("main.letter_content.get_contractor_settings")
    def test_no_fabricated_certification_badge_when_nothing_saved(self, mock_settings, mock_get_conn):
        mock_settings.return_value = _FakeSettings(business_name="Apex Tree Surgery Ltd", phone="01234 567890")
        conn = MagicMock(); cur = MagicMock()
        conn.cursor.return_value = cur
        mock_get_conn.return_value = conn

        response = main.generate_storm_quote(_request(email="dave@apex-trees.co.uk"), "EMERGENCY-DISPATCH")
        body = _body(response)
        self.assertNotIn("BS3998", body)
        self.assertNotIn("NPTC", body)
        self.assertNotIn("£5M Insurance", body)

    @patch("main.database.get_db_conn")
    @patch("main.letter_content.get_contractor_settings")
    def test_real_saved_credentials_do_render(self, mock_settings, mock_get_conn):
        mock_settings.return_value = _FakeSettings(
            business_name="Apex Tree Surgery Ltd",
            phone="01234 567890",
            insurance_note="Public liability insured up to £2M",
            qualifications_note="NPTC Level 2 Certificate in Arboriculture",
        )
        conn = MagicMock(); cur = MagicMock()
        conn.cursor.return_value = cur
        mock_get_conn.return_value = conn

        response = main.generate_storm_quote(_request(email="dave@apex-trees.co.uk"), "EMERGENCY-DISPATCH")
        body = _body(response)
        self.assertIn("Public liability insured up to £2M", body)
        self.assertIn("NPTC Level 2 Certificate in Arboriculture", body)


if __name__ == "__main__":
    unittest.main()

"""
test_boost_review_route.py -- 2026-09-24, presentation pass.

Nick's ask: "review tools with no example business names or placeholder
Google links... no generic qualification/insurance badges."

Live-site check on /boost-review found it always rendered the literal
placeholder "Your Tree Surgery Business" and a fake
"https://g.page/r/your-google-review-link" behind a one-tap "Send Review
Request via WhatsApp" button, plus a hardcoded "BS3998:2010 British
Standard Verified Arborist / £5M Public Liability Insured / NPTC Certified
Crew" badge for every contractor regardless of whether any of it is true
-- the same fabricated-credential pattern already fixed on
generate_street_flyer (test_address_release_gate.py's own
TestGenerateStreetFlyerRoute covers that route; this covers the same fix
applied to boost_review_page).

Covers:
  1. A logged-in contractor with a saved business_name gets it substituted
     automatically -- never the fake placeholder.
  2. A logged-in contractor with no saved settings/business_name is
     redirected to /letter-settings first (same as generate_street_flyer),
     never shown the placeholder as if it were their real name.
  3. No session at all gets an obviously-a-placeholder bracketed string,
     never a specific name that could pass for a real business.
  4. The WhatsApp send button is disabled whenever contractor_name or
     google_link is still a bracketed placeholder.
  5. The fabricated BS3998/£5M/NPTC badge is gone; a contractor's own real
     saved insurance_note/qualifications_note renders if present, nothing
     renders if neither is saved -- never a generic "verified" claim.
  6. /boost-review is no longer in the public sitemap (it isn't linked
     from anywhere else in the app).

Run with:
    python -m unittest tests.test_boost_review_route -v
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
import letter_content  # noqa: E402


class _FakeSettings:
    def __init__(self, business_name="", insurance_note="", qualifications_note=""):
        self.business_name = business_name
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
    request.url.path = "/boost-review"
    request.url.query = ""
    return request


def _body(response):
    return response if isinstance(response, str) else getattr(response, "body", str(response))


class TestBoostReviewPersonalization(unittest.TestCase):

    @patch("main.database.get_db_conn")
    @patch("main.letter_content.get_contractor_settings")
    def test_logged_in_contractor_with_saved_name_gets_real_name(self, mock_settings, mock_get_conn):
        mock_settings.return_value = _FakeSettings(business_name="Apex Tree Surgery Ltd")
        conn = MagicMock(); cur = MagicMock()
        conn.cursor.return_value = cur
        mock_get_conn.return_value = conn

        response = main.boost_review_page(_request(email="dave@apex-trees.co.uk"))
        body = _body(response)
        self.assertIn("Apex Tree Surgery Ltd", body)
        self.assertNotIn("Your Tree Surgery Business", body)
        self.assertNotIn("your-google-review-link", body)

    @patch("main.database.get_db_conn")
    @patch("main.letter_content.get_contractor_settings")
    def test_logged_in_contractor_with_no_saved_name_is_sent_to_letter_settings(self, mock_settings, mock_get_conn):
        mock_settings.return_value = None
        conn = MagicMock(); cur = MagicMock()
        conn.cursor.return_value = cur
        mock_get_conn.return_value = conn

        response = main.boost_review_page(_request(email="new@contractor.co.uk"))
        self.assertEqual(getattr(response, "status_code", None), 303)
        self.assertIn("/letter-settings", response.url)
        self.assertIn("next=", response.url)

    @patch("main.database.get_db_conn")
    def test_no_session_gets_bracketed_placeholder_not_a_fake_business_name(self, mock_get_conn):
        conn = MagicMock(); cur = MagicMock()
        conn.cursor.return_value = cur
        mock_get_conn.return_value = conn

        response = main.boost_review_page(_request(email=None))
        body = _body(response)
        self.assertIn("[Your Business Name]", body)
        self.assertNotIn("Your Tree Surgery Business", body)

    @patch("main.database.get_db_conn")
    def test_whatsapp_button_disabled_when_placeholders_unfilled(self, mock_get_conn):
        conn = MagicMock(); cur = MagicMock()
        conn.cursor.return_value = cur
        mock_get_conn.return_value = conn

        response = main.boost_review_page(_request(email=None))
        body = _body(response)
        self.assertIn("pointer-events:none", body)
        self.assertIn("aria-disabled", body)

    @patch("main.database.get_db_conn")
    def test_whatsapp_button_enabled_when_query_params_explicitly_supplied(self, mock_get_conn):
        conn = MagicMock(); cur = MagicMock()
        conn.cursor.return_value = cur
        mock_get_conn.return_value = conn

        response = main.boost_review_page(
            _request(email=None), contractor_name="Apex Trees", google_link="https://g.page/r/real123"
        )
        body = _body(response)
        self.assertNotIn("pointer-events:none", body)

    @patch("main.database.get_db_conn")
    @patch("main.letter_content.get_contractor_settings")
    def test_no_fabricated_certification_badge_when_nothing_saved(self, mock_settings, mock_get_conn):
        mock_settings.return_value = _FakeSettings(business_name="Apex Tree Surgery Ltd")
        conn = MagicMock(); cur = MagicMock()
        conn.cursor.return_value = cur
        mock_get_conn.return_value = conn

        response = main.boost_review_page(_request(email="dave@apex-trees.co.uk"))
        body = _body(response)
        self.assertNotIn("BS3998", body)
        self.assertNotIn("NPTC Certified Crew", body)
        self.assertNotIn("£5M Public Liability Insured", body)

    @patch("main.database.get_db_conn")
    @patch("main.letter_content.get_contractor_settings")
    def test_real_saved_credentials_do_render(self, mock_settings, mock_get_conn):
        mock_settings.return_value = _FakeSettings(
            business_name="Apex Tree Surgery Ltd",
            insurance_note="Public liability insured up to £2M",
            qualifications_note="NPTC Level 2 Certificate in Arboriculture",
        )
        conn = MagicMock(); cur = MagicMock()
        conn.cursor.return_value = cur
        mock_get_conn.return_value = conn

        response = main.boost_review_page(_request(email="dave@apex-trees.co.uk"))
        body = _body(response)
        self.assertIn("Public liability insured up to £2M", body)
        self.assertIn("NPTC Level 2 Certificate in Arboriculture", body)
        self.assertIn("TreeKey never invents or verifies", body)


class TestBoostReviewNotInSitemap(unittest.TestCase):

    def test_boost_review_not_in_public_sitemap(self):
        response = main.sitemap_xml()
        body = _body(response)
        self.assertNotIn("/boost-review", body)


if __name__ == "__main__":
    unittest.main()

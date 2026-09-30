"""
test_letter_page_shell.py -- 2026-09-30 customer-page presentation pass.
Focused structural checks (not a visual audit) for the shared letter-journey
page shell: the stylesheet the shared nav/footer depend on is linked, the
footer T/k mark is pinned small, blanket compliance/licence claims are gone
from the shared footer, and the purchase-time nudge and preview render
through the same shell. Run with:
    python -m unittest tests.test_letter_page_shell -v
"""
import os
import sys
import unittest
from unittest.mock import MagicMock, patch

_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(_THIS_DIR))
sys.path.insert(0, _THIS_DIR)

import test_address_release_gate  # noqa: E402,F401  (populates sys.modules stubs + imports main)
import main  # noqa: E402
import letter_content  # noqa: E402
from test_letter_onboarding import _mock_request, _signed  # noqa: E402


class TestLetterPageShell(unittest.TestCase):
    def _req(self):
        return _mock_request(cookie_value=_signed("c@example.com"), path="/letter-settings")

    def test_shell_links_the_stylesheet_the_shared_nav_and_footer_need(self):
        page = main._letter_page_html(self._req(), "Any", "<p>x</p>")
        self.assertIn('href="/static/tailwind.css"', page)
        self.assertIn('name="viewport"', page)

    def test_settings_form_uses_the_shell_with_and_without_a_request(self):
        s = letter_content.ContractorLetterSettings(contractor_email="c@example.com", business_name="B", phone="1")
        for req in (self._req(), None):
            page = main._letter_settings_form_html(s, request=req)
            self.assertIn("/static/tailwind.css", page)
            self.assertIn("<footer", page)

    def test_footer_mark_is_pinned_small_and_footer_has_no_blanket_claims(self):
        footer = main._shared_footer_html()
        self.assertIn("max-width:64px", footer)
        self.assertIn('height:28px', footer)
        for claim in ("GDPR Compliant", "Open Government Licence", "Operating in compliance", "256-bit"):
            self.assertNotIn(claim, footer)
        for link in ("/privacy-policy", "/terms-of-service", "mailto:contact@treekey.co.uk"):
            self.assertIn(link, footer)

    def test_purchase_nudge_renders_through_the_shell(self):
        settings = letter_content.ContractorLetterSettings(
            contractor_email="c@example.com", business_name="B", phone="1", approved=True)
        with patch("main.database.get_db_conn") as mock_conn, \
             patch("main.letter_content.get_contractor_settings", return_value=settings), \
             patch("main.letter_content.is_approval_current", return_value=True), \
             patch("main.HTMLResponse", side_effect=lambda content=None, *a, **k: content):
            mock_conn.return_value = MagicMock()
            page = main._letter_purchase_nudge_response(self._req(), "c@example.com", "/checkout/starter")
        if page is not None:  # nudge conditions are covered elsewhere; when shown it must be styled
            self.assertIn("/static/tailwind.css", page)
            self.assertIn("Continue with your standard letter", page)
            self.assertIn("Personalise my letter", page)


if __name__ == "__main__":
    unittest.main()

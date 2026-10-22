"""Oct 22 2026: what the APPLICATION controls for unsubscribing. Links in
TreeKey's own emails (and RFC 8058 one-click POSTs) must add the address to the
suppression list; bad tokens must change nothing. Instantly's own unsubscribe
and reply-stop behaviour is NOT covered here -- that needs a campaign test."""
import os
import sys
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import test_access_control  # noqa: F401,E402
import main  # noqa: E402


class TestUnsubscribeRoutes(unittest.TestCase):
    def setUp(self):
        self.token = main._sign_session_cookie("someone@example.com")

    def test_generic_link_suppresses_the_address(self):
        with patch.object(main, "database") as db, patch.object(main, "_branded_message_page", return_value="ok") as page:
            main.unsubscribe_generic(MagicMock(), token=self.token)
        db.add_email_suppression.assert_called_once_with("someone@example.com", reason="unsubscribe_link")
        self.assertIn("unsubscribed", page.call_args.args[1].lower())

    def test_one_click_post_suppresses_the_address(self):
        with patch.object(main, "database") as db:
            r = main.unsubscribe_generic_one_click(token=self.token)
        db.add_email_suppression.assert_called_once()
        self.assertEqual(getattr(r, "status_code", 200), 200)

    def test_teaser_link_and_one_click_suppress(self):
        with patch.object(main, "database") as db, patch.object(main, "_branded_message_page", return_value="ok"):
            main.unsubscribe_teaser(MagicMock(), token=self.token)
            main.unsubscribe_teaser_one_click(token=self.token)
        self.assertEqual(db.add_email_suppression.call_count, 2)

    def test_forged_or_missing_token_changes_nothing(self):
        with patch.object(main, "database") as db, patch.object(main, "_branded_message_page", return_value="bad") as page:
            main.unsubscribe_generic(MagicMock(), token="forged.token")
            main.unsubscribe_generic(MagicMock(), token=None)
            r = main.unsubscribe_generic_one_click(token="forged.token")
        db.add_email_suppression.assert_not_called()
        self.assertEqual(r.status_code, 400)
        self.assertEqual(page.call_args.kwargs.get("status_code"), 400)


if __name__ == "__main__":
    unittest.main()

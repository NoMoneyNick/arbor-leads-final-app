"""
test_letter_settings_routes.py -- 2026-09-18 review, Section 1 (second
pass): "Implement the contractor setup, letter preview and reusable-
template approval UI requested originally. Verify that approval is
authenticated, belongs to the contractor, and corresponds to the exact
content/version being approved."

These tests exercise the three real route functions in main.py
(letter_settings_form / save_letter_settings / letter_settings_preview /
approve_letter_settings) directly, the same technique
test_access_control.py and test_worker_trigger_routes.py already use, and
specifically verify the three properties the brief calls out:
  1. Authenticated -- every route requires a valid signed session cookie;
     none of them accept an email/contractor id from a query param or form
     field.
  2. Belongs to the contractor -- save/preview/approve all operate on
     whatever settings row belongs to the SESSION email, never a
     client-supplied one, and can never read or write another
     contractor's row.
  3. Corresponds to the exact content/version being approved -- the
     approval fingerprint is always computed server-side, from the row
     just re-read from the database inside the same request, and the
     route body never even inspects the submitted form for a fingerprint
     field (there isn't one to submit).

The full multi-lead customer journey (save -> preview -> approve -> two
different real leads promoted through to a fake-provider submission) is in
tests/test_letter_settings_journey.py -- kept separate since it needs a
stateful fake database, not just mocked function calls.

Run with:
    python -m unittest tests.test_letter_settings_routes -v
"""
import asyncio
import os
import sys
import unittest
from unittest.mock import MagicMock, patch

_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
_APP_DIR = os.path.dirname(_THIS_DIR)
sys.path.insert(0, _APP_DIR)
sys.path.insert(0, _THIS_DIR)

import test_access_control  # noqa: E402  (populates sys.modules stubs + imports test_main + main)
import main  # noqa: E402
import letter_content  # noqa: E402


def _run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


def _mock_request(cookie_value=None, form_data=None):
    req = MagicMock()
    req.cookies = {"treekey_contractor_session": cookie_value} if cookie_value else {}
    req.headers = {}

    async def _form():
        return form_data or {}
    req.form = _form
    return req


class _SessionTestBase(unittest.TestCase):
    def setUp(self):
        os.environ["SESSION_SECRET"] = "test-session-secret"
        main._SESSION_SECRET = b"test-session-secret"

    def _signed_cookie(self, email: str) -> str:
        return main._sign_session_cookie(email)


class TestLetterSettingsFormAuth(_SessionTestBase):

    def test_anonymous_visitor_is_redirected_to_login(self):
        request = _mock_request(cookie_value=None)
        result = main.letter_settings_form(request)
        self.assertIn("/login", result.url)

    @patch("main.letter_content.get_contractor_settings")
    @patch("main.database.get_db_conn")
    def test_logged_in_contractor_with_no_saved_row_gets_a_blank_form(self, mock_get_db_conn, mock_get_settings):
        mock_get_db_conn.return_value = MagicMock()
        mock_get_settings.return_value = None
        request = _mock_request(cookie_value=self._signed_cookie("contractor@example.com"))
        result = main.letter_settings_form(request)
        self.assertEqual(result.status_code, 200)

    @patch("main.letter_content.get_contractor_settings")
    @patch("main.database.get_db_conn")
    def test_only_this_contractors_own_email_is_ever_looked_up(self, mock_get_db_conn, mock_get_settings):
        """The route must never accept a contractor identity from anywhere
        but the signed cookie -- there is no email query param or form
        field it could even read."""
        mock_get_db_conn.return_value = MagicMock()
        mock_get_settings.return_value = None
        request = _mock_request(cookie_value=self._signed_cookie("real-contractor@example.com"))
        main.letter_settings_form(request)
        args, _ = mock_get_settings.call_args
        self.assertEqual(args[1], "real-contractor@example.com")


class TestSaveLetterSettingsAuthAndOwnership(_SessionTestBase):

    def test_anonymous_post_is_redirected_to_login_without_touching_the_database(self):
        request = _mock_request(cookie_value=None, form_data={"business_name": "Should Not Save", "phone": "0"})
        with patch("main.database.get_db_conn") as mock_get_db_conn:
            result = _run(main.save_letter_settings(request))
            mock_get_db_conn.assert_not_called()
        self.assertIn("/login", result.url)

    @patch("main.letter_content.upsert_contractor_settings")
    @patch("main.database.get_db_conn")
    def test_settings_are_saved_under_the_session_email_never_a_form_supplied_one(self, mock_get_db_conn, mock_upsert):
        """Even if a malicious/confused client submits a `contractor_email`
        form field trying to write to someone else's row, save_letter_settings
        must never read it -- ContractorLetterSettings.contractor_email is
        always built from the session, and the form dict here doesn't even
        offer a contractor_email key to prove that."""
        mock_get_db_conn.return_value = MagicMock()
        request = _mock_request(
            cookie_value=self._signed_cookie("real-contractor@example.com"),
            form_data={"business_name": "Apex Tree Care", "phone": "0113 000 0000",
                       "contractor_email": "attacker-target@example.com"},
        )
        _run(main.save_letter_settings(request))
        args, _ = mock_upsert.call_args
        saved = args[1]
        self.assertEqual(saved.contractor_email, "real-contractor@example.com")
        self.assertEqual(saved.business_name, "Apex Tree Care")

    @patch("main.letter_content.upsert_contractor_settings")
    @patch("main.database.get_db_conn")
    def test_invalid_settings_reshow_the_form_with_an_error_not_a_crash(self, mock_get_db_conn, mock_upsert):
        mock_conn = MagicMock()
        mock_get_db_conn.return_value = mock_conn
        mock_upsert.side_effect = ValueError("Invalid contractor letter settings: business_name is required")
        request = _mock_request(
            cookie_value=self._signed_cookie("real-contractor@example.com"),
            form_data={"business_name": "", "phone": "0113 000 0000"},
        )
        result = _run(main.save_letter_settings(request))
        self.assertEqual(result.status_code, 400)
        mock_conn.rollback.assert_called_once()
        mock_conn.commit.assert_not_called()

    # -- 2026-09-23 handoff: template selector + constrained editor --------

    @patch("main.letter_content.upsert_contractor_settings")
    @patch("main.database.get_db_conn")
    def test_new_template_fields_are_read_from_the_form_under_the_session_email(self, mock_get_db_conn, mock_upsert):
        mock_get_db_conn.return_value = MagicMock()
        request = _mock_request(
            cookie_value=self._signed_cookie("real-contractor@example.com"),
            form_data={
                "business_name": "Apex Tree Care", "phone": "0113 000 0000",
                "template_key": "professional_and_factual",
                "business_intro": "We are a family-run tree care business.",
                "services_note": "Felling, pruning, stump grinding.",
                "contact_email": "office@apex.example",
            },
        )
        _run(main.save_letter_settings(request))
        args, _ = mock_upsert.call_args
        saved = args[1]
        self.assertEqual(saved.contractor_email, "real-contractor@example.com")
        self.assertEqual(saved.template_key, "professional_and_factual")
        self.assertEqual(saved.business_intro, "We are a family-run tree care business.")
        self.assertEqual(saved.services_note, "Felling, pruning, stump grinding.")
        self.assertEqual(saved.contact_email, "office@apex.example")

    @patch("main.letter_content.upsert_contractor_settings")
    @patch("main.database.get_db_conn")
    def test_omitted_template_key_falls_back_to_the_default_not_a_crash(self, mock_get_db_conn, mock_upsert):
        mock_get_db_conn.return_value = MagicMock()
        request = _mock_request(
            cookie_value=self._signed_cookie("real-contractor@example.com"),
            form_data={"business_name": "Apex Tree Care", "phone": "0113 000 0000"},
        )
        _run(main.save_letter_settings(request))
        args, _ = mock_upsert.call_args
        self.assertEqual(args[1].template_key, letter_content.DEFAULT_TEMPLATE_KEY)

    @patch("main.letter_content.upsert_contractor_settings")
    @patch("main.database.get_db_conn")
    def test_invalid_template_key_reshows_the_form_with_an_error(self, mock_get_db_conn, mock_upsert):
        mock_conn = MagicMock()
        mock_get_db_conn.return_value = mock_conn
        mock_upsert.side_effect = ValueError("Invalid contractor letter settings: template_key must be one of [...]")
        request = _mock_request(
            cookie_value=self._signed_cookie("real-contractor@example.com"),
            form_data={"business_name": "Apex Tree Care", "phone": "0113 000 0000", "template_key": "not-a-real-template"},
        )
        result = _run(main.save_letter_settings(request))
        self.assertEqual(result.status_code, 400)
        mock_conn.rollback.assert_called_once()

    @patch("main.letter_content.upsert_contractor_settings")
    @patch("main.database.get_db_conn")
    def test_locked_footer_fields_posted_by_a_tampering_client_are_never_read(self, mock_get_db_conn, mock_upsert):
        """Section: 'Enforce these restrictions on the server, not just in
        the interface.' The locked privacy/data-source/contact-policy
        footer (letter_content.render_letter's final <div>, built entirely
        from TREEKEY_PRIVACY_CONTACT_EMAIL / TREEKEY_PRIVACY_POLICY_URL
        server config) has no corresponding form field at all -- so even a
        client that posts field names shaped like an override has nothing
        to actually override. Also covers an attempt to smuggle approval
        state directly."""
        mock_get_db_conn.return_value = MagicMock()
        request = _mock_request(
            cookie_value=self._signed_cookie("real-contractor@example.com"),
            form_data={
                "business_name": "Apex Tree Care", "phone": "0113 000 0000",
                "privacy_contact_email": "attacker@evil.example",
                "policy_url": "evil.example/fake-policy",
                "footer_html": "<script>alert(1)</script>",
                "data_source_note": "We made this up",
                "approved": "true",
                "approved_fingerprint": "attacker-supplied-fingerprint",
            },
        )
        _run(main.save_letter_settings(request))
        args, _ = mock_upsert.call_args
        saved = args[1]
        # Nothing about the tampering fields leaked into the saved object --
        # ContractorLetterSettings has no attribute for any of them, and
        # approval always starts False regardless of what was posted.
        self.assertFalse(hasattr(saved, "privacy_contact_email"))
        self.assertFalse(hasattr(saved, "policy_url"))
        self.assertFalse(hasattr(saved, "footer_html"))
        self.assertFalse(saved.approved)
        self.assertIsNone(saved.approved_fingerprint)

    @patch("main.letter_content.render_preview_letter")
    @patch("main.letter_content.get_contractor_settings")
    @patch("main.database.get_db_conn")
    def test_preview_route_takes_no_lead_identifying_input_from_the_request(
        self, mock_get_db_conn, mock_get_settings, mock_render_preview,
    ):
        """Identifier-leakage guard at the route level: the preview route
        function accepts only `request` -- there is no query param or form
        field through which a real lead_reference/address/summary/council
        could reach render_preview_letter, which itself only ever calls
        render_letter against the fixed PREVIEW_* constants (see
        letter_content.render_preview_letter's own docstring/test in
        tests/test_letter_content.py)."""
        import inspect
        params = list(inspect.signature(main.letter_settings_preview).parameters)
        self.assertNotIn("lead_reference", params)
        self.assertNotIn("address", params)
        self.assertNotIn("summary", params)
        self.assertNotIn("council", params)

        mock_get_db_conn.return_value = MagicMock()
        settings = letter_content.ContractorLetterSettings(
            contractor_email="contractor@example.com", business_name="Apex Tree Care", phone="0113 000 0000",
        )
        mock_get_settings.return_value = settings
        mock_render_preview.return_value = "<html>preview</html>"
        request = _mock_request(cookie_value=self._signed_cookie("contractor@example.com"))

        main.letter_settings_preview(request)

        # render_preview_letter was called with ONLY the fetched settings --
        # no second positional/keyword argument a route could have smuggled
        # lead data through.
        call = mock_render_preview.call_args
        self.assertEqual(call.args, (settings,))
        self.assertEqual(call.kwargs, {})


class TestPreviewRouteAuthAndOwnership(_SessionTestBase):

    def test_anonymous_visitor_is_redirected_to_login(self):
        request = _mock_request(cookie_value=None)
        result = main.letter_settings_preview(request)
        self.assertIn("/login", result.url)

    @patch("main.letter_content.get_contractor_settings")
    @patch("main.database.get_db_conn")
    def test_no_saved_settings_redirects_to_the_setup_form_rather_than_erroring(self, mock_get_db_conn, mock_get_settings):
        mock_get_db_conn.return_value = MagicMock()
        mock_get_settings.return_value = None
        request = _mock_request(cookie_value=self._signed_cookie("contractor@example.com"))
        result = main.letter_settings_preview(request)
        self.assertIn("/letter-settings", result.url)

    @patch("main.letter_content.render_preview_letter")
    @patch("main.letter_content.get_contractor_settings")
    @patch("main.database.get_db_conn")
    def test_preview_is_produced_via_render_preview_letter_using_the_fetched_settings(
        self, mock_get_db_conn, mock_get_settings, mock_render_preview,
    ):
        """The preview must be produced by letter_content.render_preview_letter
        -- a thin wrapper around the SAME render_letter function a real send
        uses (see its own docstring) -- not a second, hand-rolled copy that
        could silently diverge from what actually gets posted. Spied here
        rather than string-matching the fake HTMLResponse stub's content
        (which this shared test stub doesn't retain -- see
        test_worker_trigger_routes.py's own note on this stub's
        limitations)."""
        mock_get_db_conn.return_value = MagicMock()
        settings = letter_content.ContractorLetterSettings(
            contractor_email="contractor@example.com", business_name="Apex Tree Care", phone="0113 000 0000",
        )
        mock_get_settings.return_value = settings
        mock_render_preview.return_value = "<html>preview</html>"
        request = _mock_request(cookie_value=self._signed_cookie("contractor@example.com"))

        result = main.letter_settings_preview(request)

        mock_render_preview.assert_called_once_with(settings)
        self.assertEqual(result.status_code, 200)


class TestApproveRouteAuthAndFingerprintIntegrity(_SessionTestBase):

    def test_anonymous_post_is_redirected_to_login_without_touching_the_database(self):
        request = _mock_request(cookie_value=None)
        with patch("main.database.get_db_conn") as mock_get_db_conn:
            result = _run(main.approve_letter_settings(request))
            mock_get_db_conn.assert_not_called()
        self.assertIn("/login", result.url)

    @patch("main.letter_content.get_contractor_settings")
    @patch("main.database.get_db_conn")
    def test_no_saved_settings_redirects_rather_than_approving_nothing(self, mock_get_db_conn, mock_get_settings):
        mock_get_db_conn.return_value = MagicMock()
        mock_get_settings.return_value = None
        request = _mock_request(cookie_value=self._signed_cookie("contractor@example.com"))
        result = _run(main.approve_letter_settings(request))
        self.assertIn("/letter-settings", result.url)

    @patch("main.letter_content.approve_template")
    @patch("main.letter_content.get_contractor_settings")
    @patch("main.database.get_db_conn")
    def test_approval_fingerprint_is_computed_server_side_from_the_current_settings(
        self, mock_get_db_conn, mock_get_settings, mock_approve_template,
    ):
        """THE core property the brief asks to verify: the fingerprint
        passed to approve_template is letter_content.template_fingerprint
        of the row this route itself just fetched -- never anything a
        client could have supplied (the request body is never even
        parsed as a form in this route -- see approve_letter_settings'
        own docstring/comment)."""
        mock_get_db_conn.return_value = MagicMock()
        settings = letter_content.ContractorLetterSettings(
            contractor_email="contractor@example.com", business_name="Apex Tree Care", phone="0113 000 0000",
        )
        mock_get_settings.return_value = settings
        request = _mock_request(cookie_value=self._signed_cookie("contractor@example.com"))

        _run(main.approve_letter_settings(request))

        expected_fp = letter_content.template_fingerprint(settings)
        args, kwargs = mock_approve_template.call_args
        self.assertEqual(args[1], "contractor@example.com")  # belongs to the session's own contractor
        self.assertEqual(kwargs.get("preview_fingerprint"), expected_fp)

    @patch("main.letter_content.approve_template")
    @patch("main.letter_content.get_contractor_settings")
    @patch("main.database.get_db_conn")
    def test_approval_is_recorded_against_the_session_contractor_never_another_one(
        self, mock_get_db_conn, mock_get_settings, mock_approve_template,
    ):
        mock_get_db_conn.return_value = MagicMock()
        settings = letter_content.ContractorLetterSettings(
            contractor_email="victim@example.com", business_name="Victim Tree Care", phone="0113 111 1111",
        )
        # Even if get_contractor_settings somehow returned a DIFFERENT
        # contractor's row (it can't, in the real implementation, since it
        # is looked up BY the session email -- this is a belt-and-braces
        # check that the route passes the session email through
        # unconditionally, not whatever contractor_email happens to be on
        # the returned settings object).
        mock_get_settings.return_value = settings
        request = _mock_request(cookie_value=self._signed_cookie("real-logged-in-contractor@example.com"))

        _run(main.approve_letter_settings(request))

        get_settings_args, _ = mock_get_settings.call_args
        self.assertEqual(get_settings_args[1], "real-logged-in-contractor@example.com")
        approve_args, _ = mock_approve_template.call_args
        self.assertEqual(approve_args[1], "real-logged-in-contractor@example.com")


if __name__ == "__main__":
    unittest.main()

"""
test_letter_onboarding.py -- 2026-09-24 handoff. Nick's task: "Fix the
broken account Letter link and adjust letter onboarding in one focused
pass."

Covers the genuinely new behaviour this pass added (the pre-existing
setup/preview/approve pipeline's own auth/ownership/fingerprint-integrity
properties, and the checkout-time forced-setup gate and its `next`
preservation, are already covered by tests/test_letter_settings_routes.py,
tests/test_letter_settings_journey.py and
tests/test_letter_setup_checkout_gate.py -- not re-tested here):

  1. "Test navigation from the actual account link, not merely the
     destination handler in isolation" (task item 1): renders the REAL
     contractor_dashboard HTML for a signed-in, owning contractor,
     extracts the actual `/generate-letter/...` href from it (not a
     hand-typed one), and navigates it as that same contractor -- proving
     the link TreeKey's own dashboard renders actually works end to end,
     with a real saved settings row (the case that renders 200) and
     without one (the case that now redirects to /letter-settings instead
     of fabricating a business identity -- see #2).

  2. The fake-business-identity fallback fix (item 3, "never save or
     print example business names, numbers or fictional credentials"):
     a real, logged-in contractor with no saved letter_settings row is
     redirected to /letter-settings (never shown "Your Local Tree
     Specialists" / "07XXX XXXXXX" as if real), for both
     generate_homeowner_letter and generate_street_flyer, with `next`
     carrying them back to the exact letter/flyer they came from.

  3. The first-time onboarding interstitial (item 2): offered once (no
     saved settings row yet), never again once one exists, and skipped
     entirely when a `next` (checkout continuation) is already present --
     preserving "the original purchase destination when signup began from
     checkout" by simply not intercepting that case, reusing the
     pre-existing `next`/checkout-gate machinery instead of new code for
     it.

  4. Prefilling known details (item 3): a saved company_name from
     database.get_limbo_account prefills business_name; a subscriber's
     customer_name is NEVER used for business_name ("do not assume a
     personal name is a business name"), even when it is the only name on
     file.

  5. Skipping personalisation renders real standard wording, never a
     form placeholder: a settings row with blank optional fields (the
     "Use the standard letter" path) still renders a complete, real
     letter from only the saved business_name/phone -- none of the new
     example placeholder text (item 3's "useful example placeholder" on
     every field) can appear in actual rendered output, because HTML
     placeholder attributes are never submitted as field values in the
     first place; this proves that at the render layer too.

Run with:
    python -m unittest tests.test_letter_onboarding -v
"""
import os
import re
import sys
import unittest
import urllib.parse
from unittest.mock import MagicMock, patch

_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
_APP_DIR = os.path.dirname(_THIS_DIR)
sys.path.insert(0, _APP_DIR)
sys.path.insert(0, _THIS_DIR)

import test_address_release_gate  # noqa: E402  (populates sys.modules stubs + imports test_main + main)
from test_indirect_identifier_gating import (  # noqa: E402
    _ensure_database_stub_has_dashboard_attrs,
    _ensure_shared_notifications_stub_has_dashboard_attrs,
)
import main  # noqa: E402
import letter_content  # noqa: E402


def _signed(email: str) -> str:
    return main._sign_session_cookie(email)


def _capture_html():
    """main.HTMLResponse (via test_main.py's fake fastapi.responses stub)
    is `__init__(self, *a, **k): pass` -- it discards its content entirely,
    so a route's returned object has nothing readable on it. Patching
    main.HTMLResponse with this side_effect to return the raw content
    string instead is the established way this suite's other test files
    inspect rendered HTML (see tests/test_magic_link_credential_exposure.py's
    own identical note)."""
    def _capture(content=None, *a, **k):
        return content
    return _capture


class _FakeURL:
    def __init__(self, path, query):
        self.path = path
        self.query = query


def _mock_request(cookie_value=None, path="/", query="", query_params=None):
    req = MagicMock()
    req.cookies = {"treekey_contractor_session": cookie_value} if cookie_value else {}
    req.headers = {}
    query_params = query_params or {}
    # checkout() reads some params via request.query_params.get(...) and
    # others (outcode/lead_id) as its own explicit function args in real
    # FastAPI routing -- these tests call main.checkout() directly, so
    # request.query_params is what main.py's own `request.query_params.get`
    # calls see, same convention as tests/test_letter_setup_checkout_gate.py.
    req.query_params = query_params
    req.url = _FakeURL(path, query or urllib.parse.urlencode(query_params))
    return req


class _EnvBase(unittest.TestCase):
    def setUp(self):
        os.environ["SESSION_SECRET"] = "test-session-secret"
        main._SESSION_SECRET = b"test-session-secret"
        os.environ[letter_content.PRIVACY_CONTACT_EMAIL_ENV] = "privacy@treekey.co.uk"
        _ensure_database_stub_has_dashboard_attrs()
        _ensure_shared_notifications_stub_has_dashboard_attrs()


class TestActualDashboardLinkNavigation(_EnvBase):
    """Item 1: don't just call generate_homeowner_letter directly with a
    hand-typed reference -- render the real dashboard and click the real
    link it produced."""

    def _render_dashboard_and_extract_letter_href(self, owner_email: str) -> str:
        with patch("main.database.get_contractor_dashboard_data") as mock_dash_data, \
             patch("main.database.get_contractor_settings") as mock_settings, \
             patch("main.database.get_db_conn") as mock_get_conn:
            mock_dash_data.return_value = {
                "subscription": {"active": True, "tier": "pro"},
                "dispatched_leads": [{
                    "id": "lead-uuid-1", "ref": "PLANIT-NEW-777", "addr": "1 Real Street, Leeds",
                    "summary": "Fell one oak", "council": "Leeds City Council", "score": "small",
                    "price": 25, "dispatched_at": "2026-09-20 10:00", "registered_date": None,
                    "applicant_name": "Jane Homeowner", "has_agent": None,
                }],
            }
            mock_settings.return_value = {"notification_preference": "email"}
            conn = MagicMock()
            cur = MagicMock()
            cur.fetchone.return_value = ("alloc-id-777",)  # buyer_facing_reference's own lookup
            conn.cursor.return_value = cur
            mock_get_conn.return_value = conn

            request = _mock_request(cookie_value=_signed(owner_email), path="/dashboard")
            dashboard_response = main.contractor_dashboard(request)

        body = dashboard_response if isinstance(dashboard_response, str) else getattr(dashboard_response, "body", str(dashboard_response))
        match = re.search(r'href="(/generate-letter/[^"]+)"', body)
        self.assertIsNotNone(match, "dashboard did not render a /generate-letter link at all")
        return match.group(1)

    def test_the_actual_rendered_link_reaches_a_real_letter_for_its_owner(self):
        owner_email = "contractor@example.com"
        letter_path = self._render_dashboard_and_extract_letter_href(owner_email)
        buyer_facing_ref = letter_path.rsplit("/", 1)[-1]

        cur2 = MagicMock()
        cur2.fetchone.side_effect = [
            ("PLANIT-NEW-777",),  # resolve_buyer_facing_reference hit
            ("PLANIT-NEW-777", "1 Real Street, Leeds", "Fell one oak", "Leeds City Council", "claimed"),
            ("contractor@example.com", "Leeds Tree Care Ltd", "0113 000 0000", "", "", "", 1, True, "fp",
             "friendly_introduction", "", "", ""),  # a real saved settings row
            ("contractor@example.com",),  # require_lead_ownership -> get_lead_owner
            (1,),  # guarded_address_for_lead_reference -> historical
        ]
        conn2 = MagicMock()
        conn2.cursor.return_value = cur2
        nav_request = _mock_request(cookie_value=_signed(owner_email), path=letter_path)
        with patch.object(main.database, "get_db_conn", return_value=conn2):
            response = main.generate_homeowner_letter(nav_request, buyer_facing_ref)

        body = response if isinstance(response, str) else getattr(response, "body", str(response))
        self.assertEqual(getattr(response, "status_code", 200), 200)
        self.assertIn("letter-frame", body)
        self.assertIn("Leeds Tree Care Ltd", body)

    def test_the_actual_rendered_link_never_shows_a_fabricated_identity_when_unset_up(self):
        """The same real link, same owning contractor, but this
        contractor has never saved letter settings -- must redirect to
        /letter-settings (carrying them back to this exact link), never
        render 'Your Local Tree Specialists' as if it were real."""
        owner_email = "contractor@example.com"
        letter_path = self._render_dashboard_and_extract_letter_href(owner_email)
        buyer_facing_ref = letter_path.rsplit("/", 1)[-1]

        cur2 = MagicMock()
        cur2.fetchone.side_effect = [
            ("PLANIT-NEW-777",),
            ("PLANIT-NEW-777", "1 Real Street, Leeds", "Fell one oak", "Leeds City Council", "claimed"),
            None,  # no saved contractor_letter_settings
            ("contractor@example.com",),
            (1,),
        ]
        conn2 = MagicMock()
        conn2.cursor.return_value = cur2
        nav_request = _mock_request(cookie_value=_signed(owner_email), path=letter_path)
        with patch.object(main.database, "get_db_conn", return_value=conn2):
            response = main.generate_homeowner_letter(nav_request, buyer_facing_ref)

        self.assertEqual(getattr(response, "status_code", None), 303)
        self.assertIn("/letter-settings", response.url)
        self.assertIn("next=", response.url)
        body = getattr(response, "body", "") or ""
        self.assertNotIn("Your Local Tree Specialists", str(body))


class TestFakeIdentityFallbackFix(_EnvBase):
    """Item 3, generate_street_flyer's own version of the same bug (this
    route never even loaded letter_content settings before this fix --
    see main.py's own comment)."""

    def test_street_flyer_redirects_a_real_contractor_with_no_settings_instead_of_fabricating(self):
        cur = MagicMock()
        cur.fetchone.side_effect = [
            None,  # resolve_buyer_facing_reference miss
            ("PLANIT-001", "1 Real Street, Leeds, LS1 1AA", "Fell one oak", "claimed"),
            None,  # no saved settings
        ]
        conn = MagicMock()
        conn.cursor.return_value = cur
        request = _mock_request(cookie_value=_signed("contractor@example.com"), path="/generate-street-flyer/PLANIT-001")
        with patch("main.database.get_db_conn", return_value=conn), \
             patch("main.fulfilment.get_lead_owner", return_value="contractor@example.com"), \
             patch("main.address_release.lead_address_release_allowed", return_value=True):
            response = main.generate_street_flyer(request, "PLANIT-001")

        self.assertEqual(getattr(response, "status_code", None), 303)
        self.assertIn("/letter-settings", response.url)

    def test_street_flyer_uses_real_saved_details_when_present(self):
        cur = MagicMock()
        cur.fetchone.side_effect = [
            None,
            ("PLANIT-001", "1 Real Street, Leeds, LS1 1AA", "Fell one oak", "claimed"),
            ("contractor@example.com", "Ashcroft Tree Surgery", "01234 567890", "", "", "", 1, True, "fp",
             "friendly_introduction", "", "", ""),
        ]
        conn = MagicMock()
        conn.cursor.return_value = cur
        request = _mock_request(cookie_value=_signed("contractor@example.com"), path="/generate-street-flyer/PLANIT-001")
        with patch("main.database.get_db_conn", return_value=conn), \
             patch("main.fulfilment.get_lead_owner", return_value="contractor@example.com"), \
             patch("main.address_release.lead_address_release_allowed", return_value=True):
            response = main.generate_street_flyer(request, "PLANIT-001")

        body = response if isinstance(response, str) else getattr(response, "body", str(response))
        self.assertNotEqual(getattr(response, "status_code", 200), 303)
        self.assertIn("Ashcroft Tree Surgery", body)
        self.assertIn("01234 567890", body)
        self.assertNotIn("Your Local Tree Surgery Team", body)
        self.assertNotIn("07XXX XXXXXX", body)


class TestFirstTimeOnboardingOffer(_EnvBase):

    def test_login_offers_onboarding_when_no_letter_settings_exist_yet(self):
        with patch("main.database.get_contractor_subscription", return_value={"active": True}), \
             patch("main.database.get_db_conn") as mock_conn, \
             patch("main.letter_content.get_contractor_settings", return_value=None):
            mock_conn.return_value = MagicMock()
            response = main._login_session_response("new-contractor@example.com")
        self.assertIn("/letter-onboarding", response.url)
        self.assertIn("next=", response.url)

    def test_login_skips_onboarding_once_a_settings_row_exists(self):
        settings = letter_content.ContractorLetterSettings(
            contractor_email="returning@example.com", business_name="Returning Co", phone="0113 000 0000")
        with patch("main.database.get_contractor_subscription", return_value={"active": True}), \
             patch("main.database.get_db_conn") as mock_conn, \
             patch("main.letter_content.get_contractor_settings", return_value=settings):
            mock_conn.return_value = MagicMock()
            response = main._login_session_response("returning@example.com")
        self.assertEqual(response.url, "/dashboard")

    def test_login_never_offers_onboarding_when_a_next_destination_is_already_pending(self):
        """Preserves the original purchase destination when signup began
        from checkout: onboarding must not intercept a login that already
        has somewhere specific to go."""
        with patch("main.letter_content.get_contractor_settings") as mock_settings:
            response = main._login_session_response("mid-checkout@example.com", next_url="/checkout/starter")
        mock_settings.assert_not_called()
        self.assertEqual(response.url, "/checkout/starter")

    def test_login_fails_open_on_a_lookup_error_and_signs_in_normally(self):
        with patch("main.database.get_contractor_subscription", return_value={"active": True}), \
             patch("main.database.get_db_conn", side_effect=RuntimeError("db down")):
            response = main._login_session_response("contractor@example.com")
        self.assertEqual(response.url, "/dashboard")

    def test_onboarding_page_offers_both_choices_and_never_forces_writing(self):
        request = _mock_request(cookie_value=_signed("new-contractor@example.com"), path="/letter-onboarding")
        with patch("main.database.get_db_conn") as mock_conn, \
             patch("main.letter_content.get_contractor_settings", return_value=None), \
             patch("main.HTMLResponse", side_effect=_capture_html()):
            mock_conn.return_value = MagicMock()
            response = main.letter_onboarding(request, next="/dashboard")
        body = response if isinstance(response, str) else getattr(response, "body", str(response))
        self.assertIn("Personalise my letter", body)
        self.assertIn("Use the standard letter", body)
        self.assertIn("/letter-settings", body)
        self.assertIn("intent=standard", body)

    def test_onboarding_page_does_not_repeat_once_settings_exist(self):
        settings = letter_content.ContractorLetterSettings(
            contractor_email="returning@example.com", business_name="Returning Co", phone="0113 000 0000")
        request = _mock_request(cookie_value=_signed("returning@example.com"), path="/letter-onboarding")
        with patch("main.database.get_db_conn") as mock_conn, \
             patch("main.letter_content.get_contractor_settings", return_value=settings):
            mock_conn.return_value = MagicMock()
            response = main.letter_onboarding(request, next="/dashboard")
        self.assertEqual(getattr(response, "status_code", None), 303)
        self.assertEqual(response.url, "/dashboard")


class TestPrefillNeverAssumesAPersonalNameIsABusinessName(_EnvBase):

    @patch("main.letter_content.get_contractor_settings", return_value=None)
    @patch("main.database.get_db_conn")
    def test_company_name_from_limbo_account_prefills_business_name(self, mock_get_db_conn, mock_get_settings):
        mock_get_db_conn.return_value = MagicMock()
        with patch("main.database.get_limbo_account",
                    return_value={"company_name": "Fenwick Tree Services", "phone": "0121 000 0000"}), \
             patch("main.HTMLResponse", side_effect=_capture_html()):
            request = _mock_request(cookie_value=_signed("fenwick@example.com"), path="/letter-settings")
            response = main.letter_settings_form(request)
        body = response if isinstance(response, str) else getattr(response, "body", str(response))
        self.assertIn("Fenwick Tree Services", body)
        self.assertIn("0121 000 0000", body)

    @patch("main.letter_content.get_contractor_settings", return_value=None)
    @patch("main.database.get_db_conn")
    def test_a_subscribers_personal_customer_name_never_prefills_business_name(self, mock_get_db_conn, mock_get_settings):
        """contractor_subscriptions only ever holds customer_name/phone --
        never a company name -- and a personal name must never be used as
        a business name (task item 3's explicit instruction)."""
        mock_get_db_conn.return_value = MagicMock()
        with patch("main.database.get_limbo_account", return_value=None), \
             patch("main.database.get_contractor_subscription",
                    return_value={"customer_name": "Dave Smith", "phone": "0121 999 8888"}), \
             patch("main.HTMLResponse", side_effect=_capture_html()):
            request = _mock_request(cookie_value=_signed("dave@example.com"), path="/letter-settings")
            response = main.letter_settings_form(request)
        body = response if isinstance(response, str) else getattr(response, "body", str(response))
        self.assertNotIn("Dave Smith", body)
        self.assertIn("0121 999 8888", body)  # phone is still safe to prefill


class TestStandardWordingRendersOnlyRealDetailsNeverPlaceholders(_EnvBase):

    def test_blank_optional_fields_render_real_standard_wording_with_no_placeholder_text(self):
        """The 'Use the standard letter' path: a settings row with only
        the two essential fields filled in, everything else left blank --
        this must still render a complete, real letter, and none of this
        pass's new example placeholder copy (form placeholders only, never
        submitted values) can appear in it."""
        settings = letter_content.ContractorLetterSettings(
            contractor_email="standard@example.com", business_name="Oakwood Tree Care", phone="0161 000 0000",
        )
        rendered = letter_content.render_letter(
            settings, lead_reference="PLANIT-999", address="1 Sample Street, Leeds",
            summary="Fell one oak", council="Leeds City Council",
        )
        self.assertIn("Oakwood Tree Care", rendered)
        self.assertIn("0161 000 0000", rendered)
        for placeholder_text in (
            "Ashcroft Tree Surgery", "e.g. We're a family-run tree surgery",
            "Tree felling, crown reduction, hedge trimming, stump grinding",
            "Public liability insured up to", "NPTC Level 2 Certificate",
        ):
            self.assertNotIn(placeholder_text, rendered)


_PAYMENT_PLAN = {"single_lead_small": {"name": "Single Lead Unlock (Entry)", "amount": 1900, "mode": "payment"}}


def _approved_settings(business_intro="") -> "letter_content.ContractorLetterSettings":
    return letter_content.ContractorLetterSettings(
        contractor_email="contractor@example.com", business_name="Approved Tree Co", phone="0113 000 0000",
        approved=True, approved_fingerprint="placeholder", business_intro=business_intro,
    )


class TestPurchaseTimePersonalisationNudge(_EnvBase):
    """2026-09-24, second pass -- Nick's explicit authorisation: "the
    purchase-time personalisation opportunity for contractors who already
    have an approved standard letter ... was part of my original
    request." checkout()'s pre-existing gate (tests/test_letter_setup_
    checkout_gate.py) only ever detours an INCOMPLETE setup; these tests
    cover the new case it added on top -- an already-complete contractor
    who is still on standard wording."""

    def _checkout_request(self, query_params=None):
        return _mock_request(cookie_value=_signed("contractor@example.com"),
                              path="/checkout/single_lead_small", query_params=query_params or {"lead_id": "LEAD-1"})

    def test_approved_standard_contractor_sees_the_nudge_before_any_reservation(self):
        request = self._checkout_request()
        with patch.object(main.payments, "PLANS", _PAYMENT_PLAN), \
             patch("main._letter_setup_complete", return_value=True), \
             patch("main.letter_content.get_contractor_settings", return_value=_approved_settings(business_intro="")), \
             patch("main.database.get_db_conn", return_value=MagicMock()), \
             patch.object(main.payments, "create_checkout_session") as mock_create, \
             patch("main.HTMLResponse", side_effect=_capture_html()):
            response = main.checkout("single_lead_small", request)

        mock_create.assert_not_called()  # no reservation, no Stripe -- the nudge itself is read-only
        self.assertIsInstance(response, str)
        self.assertIn("Add a personal introduction, or continue with your standard letter", response)
        self.assertIn("Continue with your standard letter", response)
        self.assertIn("Personalise my letter", response)
        self.assertIn("letter_nudge=continue", response)
        self.assertIn("/letter-settings?next=", response)

    def test_dismissing_the_nudge_proceeds_to_stripe_exactly_once(self):
        """The nudge's own 'Continue with your standard letter' link is
        exactly this same checkout URL plus `letter_nudge=continue` -- a
        contractor following it hits checkout() a second time, and THAT
        request must be a normal, single, unchanged purchase: no second
        nudge, no second lead reservation, one Stripe session."""
        request = self._checkout_request(query_params={"lead_id": "LEAD-1", "letter_nudge": "continue"})
        with patch.object(main.payments, "PLANS", _PAYMENT_PLAN), \
             patch("main._letter_setup_complete", return_value=True), \
             patch("main.letter_content.get_contractor_settings") as mock_settings, \
             patch.object(main.payments, "create_checkout_session",
                           return_value="https://stripe.example/session/xyz") as mock_create:
            response = main.checkout("single_lead_small", request)

        mock_settings.assert_not_called()  # never even re-checked wording once dismissed
        mock_create.assert_called_once()
        self.assertEqual(response.url, "https://stripe.example/session/xyz")

    def test_already_personalised_contractor_proceeds_normally_with_no_nudge(self):
        """'Already-approved personalised letters should proceed
        normally' -- a non-blank business_intro must never show the
        nudge, first hit or otherwise."""
        request = self._checkout_request()
        with patch.object(main.payments, "PLANS", _PAYMENT_PLAN), \
             patch("main._letter_setup_complete", return_value=True), \
             patch("main.letter_content.get_contractor_settings",
                   return_value=_approved_settings(business_intro="We're a family-run business.")), \
             patch("main.database.get_db_conn", return_value=MagicMock()), \
             patch.object(main.payments, "create_checkout_session",
                           return_value="https://stripe.example/session/abc") as mock_create:
            response = main.checkout("single_lead_small", request)

        mock_create.assert_called_once()
        self.assertEqual(response.url, "https://stripe.example/session/abc")

    def test_a_lookup_error_fails_open_and_the_purchase_proceeds_unnudged(self):
        request = self._checkout_request()
        with patch.object(main.payments, "PLANS", _PAYMENT_PLAN), \
             patch("main._letter_setup_complete", return_value=True), \
             patch("main.database.get_db_conn", side_effect=RuntimeError("db down")), \
             patch.object(main.payments, "create_checkout_session",
                           return_value="https://stripe.example/session/def") as mock_create:
            response = main.checkout("single_lead_small", request)

        mock_create.assert_called_once()
        self.assertEqual(response.url, "https://stripe.example/session/def")

    def test_approving_during_a_checkout_detour_does_not_immediately_reshow_the_nudge(self):
        """The real experience Nick asked to be tested, not just the gate:
        a brand-new buyer who was forced through /letter-settings from
        checkout, chose standard wording (left business_intro blank), and
        clicked Approve must NOT see the exact same personalise-or-
        standard question again one redirect later -- approve_letter_
        settings' own redirect must already carry the dismiss marker."""
        import asyncio
        settings = _approved_settings(business_intro="")
        approve_request = _mock_request(
            cookie_value=_signed("contractor@example.com"), path="/letter-settings/approve",
            query_params={"next": "/checkout/single_lead_small?lead_id=LEAD-1"})
        with patch("main.letter_content.get_contractor_settings", return_value=settings), \
             patch("main.letter_content.approve_template"), \
             patch("main.database.get_db_conn", return_value=MagicMock()):
            approve_response = asyncio.get_event_loop().run_until_complete(main.approve_letter_settings(approve_request))

        self.assertIn("letter_nudge=continue", approve_response.url)

        # Following that exact redirect back into checkout() must be a
        # normal, single purchase -- no nudge shown a second time.
        parsed = urllib.parse.urlparse(approve_response.url)
        checkout_request = _mock_request(
            cookie_value=_signed("contractor@example.com"), path=parsed.path,
            query_params=dict(urllib.parse.parse_qsl(parsed.query)))
        with patch.object(main.payments, "PLANS", _PAYMENT_PLAN), \
             patch("main._letter_setup_complete", return_value=True), \
             patch("main.letter_content.get_contractor_settings") as mock_settings_at_checkout, \
             patch.object(main.payments, "create_checkout_session",
                           return_value="https://stripe.example/session/once") as mock_create:
            checkout_response = main.checkout("single_lead_small", checkout_request)

        mock_settings_at_checkout.assert_not_called()
        mock_create.assert_called_once()
        self.assertEqual(checkout_response.url, "https://stripe.example/session/once")

    def test_the_nudge_screen_itself_never_writes_to_settings(self):
        """Rendering (or being shown) the nudge must never touch approval
        -- only an actual edit+save on /letter-settings does that."""
        request = self._checkout_request()
        with patch.object(main.payments, "PLANS", _PAYMENT_PLAN), \
             patch("main._letter_setup_complete", return_value=True), \
             patch("main.letter_content.get_contractor_settings", return_value=_approved_settings(business_intro="")), \
             patch("main.database.get_db_conn", return_value=MagicMock()), \
             patch("main.letter_content.upsert_contractor_settings") as mock_upsert, \
             patch("main.letter_content.approve_template") as mock_approve, \
             patch("main.HTMLResponse", side_effect=_capture_html()):
            main.checkout("single_lead_small", request)

        mock_upsert.assert_not_called()
        mock_approve.assert_not_called()


if __name__ == "__main__":
    unittest.main()

"""
test_customer_journey_fixes.py -- 8 Oct 2026, the logged-in one-off customer journey.

Covers only what this change touched:
  * /my-leads lists a one-off buyer's purchases and their letter status (it used to show them only to
    subscribers), only ever for the signed-in account, and never shows a restricted homeowner address;
  * /account billing history applies the existing address-release guard;
  * the old /free-dashboard no longer picks, grants or emails a lead, and sends such visits to /welcome;
  * navigation: the signed-in "Dashboard" link, the payment-success page, the welcome page, the My Account
    labels and the signed-out "sign in" prompts, with subscription sales off;
  * subscribers keep their pages and behaviour (and the subscriptions-on wording is unchanged).

Run with:  python -m unittest tests.test_customer_journey_fixes -v
"""
import importlib.util
import os
import sys
import unittest
from unittest.mock import MagicMock, patch

_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
_APP_DIR = os.path.dirname(_THIS_DIR)
sys.path.insert(0, _APP_DIR)
sys.path.insert(0, _THIS_DIR)

import test_access_control  # noqa: E402,F401
import main  # noqa: E402
import address_release  # noqa: E402
import test_indirect_identifier_gating as _tiig  # noqa: E402



class _Base(unittest.TestCase):
    def setUp(self):
        # main.py's own `import database` / local `import notifications` resolve against the shared test
        # stubs, which other test files swap during a combined run; give them the few attributes the pages
        # read, per test (same convention as the other route tests).
        os.environ["SESSION_SECRET"] = "test-session-secret"
        main._SESSION_SECRET = b"test-session-secret"
        _tiig._ensure_database_stub_has_dashboard_attrs()
        _tiig._ensure_shared_notifications_stub_has_dashboard_attrs()

EMAIL = "buyer@example.com"
OTHER = "someone.else@example.com"
REF = "PLANIT-NEW-001"
REAL_ADDRESS = "14 Acacia Road, Leeds"
APPLICANT = "Jane Homeowner"


class _Body:
    """Captures what a handler passes to HTMLResponse, like the other route tests do."""
    def __init__(self, content="", status_code=200, **kwargs):
        self.body = content
        self.status_code = status_code


def _text(resp):
    return resp if isinstance(resp, str) else getattr(resp, "body", str(resp))


def _loc(resp):
    """Where a redirect points (the shared test stub keeps .url; real Starlette keeps a Location header)."""
    return resp.url if hasattr(resp, "url") else resp.headers["location"]


def _req(email=EMAIL, path="/my-leads", query=None):
    r = MagicMock()
    main._SESSION_SECRET = b"test-session-secret"
    r.cookies = {"treekey_contractor_session": main._sign_session_cookie(email)} if email else {}
    r.query_params = query or {}
    r.headers = {}
    return r


def _purchase(ref=REF, addr=REAL_ADDRESS):
    return {
        "id": "lead-uuid-1", "ref": ref, "addr": addr, "summary": "Fell one oak at " + addr,
        "council": "Leeds City Council", "score": "small", "price": 19,
        "dispatched_at": "2026-10-07 10:00:00", "dispatch_type": "purchased", "registered_date": None,
        "applicant_name": APPLICANT, "agent_name": None, "agent_company": None, "has_agent": None,
    }


_INTRO = {
    "stage_label": "Being printed and posted", "stage_explanation": "Your letter is with the print provider.",
    "is_dry_run": False, "template_version": 3, "provider_name": None, "provider_reference": None,
    "provider_accepted_at": None, "dispatched_at": None, "failed_at": None, "letter_number": 42,
}


def _address_guard(historical_refs=()):
    """The REAL address_release guard, with only its database lookup faked."""
    fake_db = MagicMock()
    fake_db.get_db_conn.return_value = MagicMock()
    return (
        patch.object(address_release, "database", fake_db),
        patch.object(address_release, "is_historical_purchase", side_effect=lambda cur, ref: ref in historical_refs),
        patch.object(address_release, "buyer_facing_reference_standalone", side_effect=lambda ref: "TK-" + ref[-3:]),
    )


class TestMyLeadsForAOneOffBuyer(_Base):

    def _page(self, *, email=EMAIL, rows=None, sub=None, limbo=None, subs_open=False, query=None, hist=()):
        g1, g2, g3 = _address_guard(hist)
        dash = MagicMock(return_value={"dispatched_leads": rows if rows is not None else [_purchase()]})
        with patch("main.HTMLResponse", _Body), \
             patch.object(main.database, "get_contractor_subscription", return_value=sub, create=True), \
             patch.object(main.database, "get_limbo_account", return_value=limbo, create=True), \
             patch.object(main.database, "get_contractor_dashboard_data", dash, create=True), \
             patch.object(main.fulfilment, "get_introduction_record_for_lead_reference", return_value=dict(_INTRO), create=True), \
             patch.object(main.fulfilment, "get_letter_status_label_for_lead_reference", return_value=None, create=True), \
             patch("main.payments.subscription_sales_enabled", return_value=subs_open, create=True), \
             g1, g2, g3:
            out = _text(main.my_leads_view(_req(email, query=query)))
        return out, dash

    def test_a_non_subscriber_sees_their_purchase_and_its_letter_status(self):
        page, _ = self._page()
        self.assertIn("Mailed introduction:", page)
        self.assertIn("Being printed and posted", page)
        self.assertIn("Letter number:", page)
        self.assertIn("Purchased: 2026-10-07 10:00", page)
        self.assertIn("1 total", page)
        self.assertNotIn("No leads purchased yet", page)

    def test_only_the_signed_in_accounts_purchases_are_requested_whatever_the_url_says(self):
        _, dash = self._page(query={"email": OTHER, "account": OTHER})
        dash.assert_called_once_with(EMAIL)

    def test_another_account_with_no_purchases_sees_none_of_them(self):
        page, dash = self._page(email=OTHER, rows=[])
        dash.assert_called_once_with(OTHER)
        self.assertNotIn(REF, page)
        self.assertIn("No introductions to show yet", page)
        self.assertIn("0 total", page)

    def test_a_restricted_homeowner_address_and_name_stay_hidden(self):
        page, _ = self._page()
        self.assertNotIn(REAL_ADDRESS, page)
        self.assertNotIn("Acacia", page)
        self.assertNotIn(APPLICANT, page)
        self.assertIn(address_release.REDACTED_ADDRESS_PLACEHOLDER.split(".")[0][:20], page)

    def test_no_street_view_button_is_offered_for_a_restricted_lead(self):
        page, _ = self._page()
        self.assertNotIn("Street View", page)
        self.assertNotIn("/street-view/", page)
        self.assertIn("Preview Letter", page)
        self.assertNotIn("Street Flyer", page)

    def test_a_historical_purchase_keeps_its_existing_behaviour(self):
        page, _ = self._page(hist=(REF,))
        self.assertIn(REAL_ADDRESS, page)
        self.assertIn("Street View", page)

    def test_no_subscription_selling_with_subscriptions_off(self):
        page, _ = self._page()
        self.assertNotIn("Subscription Plans", page)
        self.assertNotIn("Subscribe for a steady stream", page)

    def test_a_subscriber_still_sees_their_leads_and_nothing_changes_for_them(self):
        page, dash = self._page(sub={"active": True}, rows=[_purchase()], subs_open=True)
        dash.assert_called_once_with(EMAIL)
        self.assertIn("Mailed introduction:", page)
        self.assertNotIn("Subscription Plans", page)          # no upsell to an active subscriber
        empty, _ = self._page(sub={"active": True}, rows=[])
        self.assertIn("No leads purchased yet", empty)

    def test_a_non_subscriber_with_nothing_bought_still_gets_the_empty_state_and_upsell_only_when_on(self):
        off, _ = self._page(rows=[], subs_open=False)
        on, _ = self._page(rows=[], subs_open=True)
        self.assertIn("No introductions to show yet", off)
        self.assertNotIn("Subscription Plans", off)
        self.assertIn("Subscription Plans", on)

    def test_signed_out_visitors_are_sent_to_login(self):
        with patch("main.HTMLResponse", _Body):
            r = main.my_leads_view(_req(None))
        self.assertEqual(r.status_code, 303)
        self.assertEqual(_loc(r), "/login")


class TestTheDashboardQueryIsKeyedOnTheAccountEmail(_Base):
    """The real SQL function, run against a fake cursor that holds two accounts' purchases."""

    @classmethod
    def setUpClass(cls):
        spec = importlib.util.spec_from_file_location("database_cj_probe", os.path.join(_APP_DIR, "database.py"))
        cls.db = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.db)

    def _run(self, email):
        data = {
            EMAIL: [("id1", REF, REAL_ADDRESS, "s", "c", "small", 19, "2026-10-07", "purchased",
                     APPLICANT, None, None, None, None)],
            OTHER: [("id2", "PLANIT-OTHER-9", "9 Other Street", "s", "c", "small", 19, "2026-10-06", "purchased",
                     None, None, None, None, None)],
        }
        calls = []

        class Cur:
            def execute(self, sql, params=None):
                calls.append((sql, params)); self.last = params
            def fetchone(self):
                return None
            def fetchall(self):
                return data.get(self.last[0], [])
            def close(self):
                pass

        conn = MagicMock(); conn.cursor.return_value = Cur()
        with patch.object(self.db, "SURL", "postgres://fake"), patch.object(self.db, "get_db_conn", return_value=conn):
            out = self.db.get_contractor_dashboard_data(email)
        return out, calls

    def test_each_account_gets_only_its_own_purchases(self):
        a, calls_a = self._run(EMAIL)
        b, calls_b = self._run(OTHER)
        self.assertEqual([x["ref"] for x in a["dispatched_leads"]], [REF])
        self.assertEqual([x["ref"] for x in b["dispatched_leads"]], ["PLANIT-OTHER-9"])
        for sql, params in (calls_a[-1], calls_b[-1]):
            self.assertIn("d.contractor_email = %s", sql)
            self.assertIn("p.account_email = %s", sql)
            self.assertEqual(params[0], params[1])
        self.assertEqual(calls_a[-1][1], (EMAIL, EMAIL))

    def test_the_failure_shape_has_no_dispatched_leads_key_and_my_leads_copes(self):
        # database.get_contractor_dashboard_data returns this on an error; the page must not crash.
        g1, g2, g3 = _address_guard()
        with patch("main.HTMLResponse", _Body), \
             patch.object(main.database, "get_contractor_subscription", return_value=None, create=True), \
             patch.object(main.database, "get_limbo_account", return_value=None, create=True), \
             patch.object(main.database, "get_contractor_dashboard_data",
                          return_value={"email": EMAIL, "tier": "Error", "leads": [], "active": False}, create=True), \
             patch("main.payments.subscription_sales_enabled", return_value=False, create=True), g1, g2, g3:
            page = _text(main.my_leads_view(_req()))
        self.assertIn("No introductions to show yet", page)


class TestAccountBillingHistoryAndLabels(_Base):

    def _page(self, *, history, sub=None, subs_open=False, hist=()):
        g1, g2, g3 = _address_guard(hist)
        db = MagicMock()
        db.get_contractor_subscription.return_value = sub
        db.get_limbo_account.return_value = None
        db.get_contractor_settings.return_value = {}
        db.get_payment_history_for_contractor.return_value = history
        with patch("main.database", db), \
             patch("main.letter_content.get_contractor_settings", return_value=None), \
             patch("main.HTMLResponse", _Body), \
             patch("main.payments.subscription_sales_enabled", return_value=subs_open, create=True), g1, g2, g3:
            return _text(main.my_account_view(_req(path="/account")))

    def _row(self, **kw):
        row = {"session_id": "cs_1", "plan": "single_lead_small", "amount_pence": 1900, "status": "paid",
               "fulfillment_outcome": "fulfilled", "created_at": "2026-10-07 09:00", "updated_at": "2026-10-07 09:01",
               "lead_reference": REF, "lead_address": REAL_ADDRESS}
        row.update(kw)
        return row

    def test_billing_history_hides_a_restricted_address_and_shows_the_buyer_facing_reference(self):
        page = self._page(history=[self._row()])
        self.assertNotIn(REAL_ADDRESS, page)
        self.assertNotIn("Acacia", page)
        self.assertIn("Introduction ", page)
        self.assertIn("Introduction TK-001", page)
        self.assertIn("£19.00", page)
        self.assertIn("Paid", page)

    def test_billing_history_keeps_the_address_for_a_historical_purchase(self):
        self.assertIn(REAL_ADDRESS, self._page(history=[self._row()], hist=(REF,)))

    def test_a_subscription_row_with_no_lead_is_unchanged(self):
        page = self._page(history=[self._row(plan="starter", lead_reference=None, lead_address=None, amount_pence=3900)])
        self.assertIn("Starter", page)
        self.assertIn("£39.00", page)

    def test_non_subscriber_labels_with_subscriptions_off(self):
        page = self._page(history=[])
        self.assertIn("NO SUBSCRIPTION", page)
        self.assertIn("Pay per introduction", page)
        self.assertNotIn("FREE TIER", page)
        self.assertNotIn('card-label">Subscription<', page)
        self.assertIn('card-label">Plan<', page)
        self.assertIn("Browse the Marketplace", page)
        self.assertNotIn("/free-dashboard", page)
        self.assertIn('href="/welcome"', page)
        self.assertIn("Your introductions and letter status", page)
        self.assertNotIn("View everything you've received", page)

    def test_non_subscriber_labels_with_subscriptions_on_keep_the_subscription_card(self):
        page = self._page(history=[], subs_open=True)
        self.assertIn('card-label">Subscription<', page)
        self.assertIn("Upgrade to a Subscription", page)

    def test_a_subscriber_page_is_unchanged(self):
        sub = {"active": True, "tier": "starter", "quota": 6, "delivered": 1, "outcode": "AB1",
               "radius": 15, "subscribed_at": "2026-09-01"}
        page = self._page(history=[], sub=sub, subs_open=False)
        self.assertIn("ACTIVE PARTNER", page)
        self.assertIn('card-label">Subscription<', page)
        self.assertIn("Your plan is unchanged.", page)
        self.assertIn('href="/dashboard"', page)
        self.assertIn(">Dashboard<", page)


class TestOldFreeDashboardGrantsNothing(_Base):

    def _visit(self, *, account, sub=None, email=EMAIL):
        names = ("find_nearest_unclaimed_lead", "burn_lead_inventory", "record_free_lead_grant")
        mocks = {n: MagicMock(return_value={"reference": "PLANIT-FREE-1", "address": "1 Real St"}) for n in names}
        email_mock = MagicMock()
        patches = [patch.object(main.database, n, m, create=True) for n, m in mocks.items()]
        patches += [
            patch.object(main.database, "get_contractor_subscription", return_value=sub, create=True),
            patch.object(main.database, "get_limbo_account", return_value=account, create=True),
            patch.object(main.database, "get_lead_by_reference", return_value=None, create=True),
            patch.object(main.database, "get_contractor_settings", return_value={}, create=True),
            patch.object(sys.modules["notifications"], "send_free_lead_granted_email", email_mock, create=True),
            patch.object(main.fulfilment, "get_letter_status_label_for_lead_reference", return_value=None, create=True),
            patch("main.HTMLResponse", _Body),
        ]
        for p in patches:
            p.start()
        try:
            resp = main.free_dashboard(_req(email, path="/free-dashboard"))
        finally:
            for p in patches:
                p.stop()
        return resp, mocks, email_mock

    def test_visiting_creates_no_grant_and_sends_no_email_and_redirects_to_welcome(self):
        account = {"email": EMAIL, "lat": 53.0, "lon": -1.0, "free_lead_ref": None}
        resp, mocks, email_mock = self._visit(account=account)
        self.assertEqual(resp.status_code, 303)
        self.assertEqual(_loc(resp), "/welcome")
        for name, m in mocks.items():
            m.assert_not_called()
        email_mock.assert_not_called()
        self.assertIsNone(account["free_lead_ref"])

    def test_an_account_that_already_holds_a_free_lead_still_sees_it_with_no_new_grant(self):
        account = {"email": EMAIL, "lat": 53.0, "lon": -1.0, "free_lead_ref": "PLANIT-FREE-OLD"}
        resp, mocks, email_mock = self._visit(account=account)
        self.assertNotEqual(getattr(resp, "status_code", 200), 303)
        for m in mocks.values():
            m.assert_not_called()
        email_mock.assert_not_called()

    def test_a_subscriber_still_goes_to_their_dashboard(self):
        resp, mocks, _ = self._visit(account={"email": EMAIL}, sub={"active": True})
        self.assertEqual(_loc(resp), "/dashboard")
        for m in mocks.values():
            m.assert_not_called()

    def test_an_account_with_no_record_goes_to_the_signup_form_and_signed_out_to_login(self):
        resp, mocks, _ = self._visit(account=None)
        self.assertEqual(_loc(resp), "/free-account")
        with patch("main.HTMLResponse", _Body):
            out = main.free_dashboard(_req(None))
        self.assertEqual(_loc(out), "/login")

    def test_the_handler_source_no_longer_contains_a_grant_or_email_call(self):
        with open(os.path.join(_APP_DIR, "main.py"), encoding="utf-8") as fh:
            src = fh.read()
        a = src.index("def free_dashboard"); b = src.index("\n@app.", a)
        seg = src[a:b]
        for gone in ("burn_lead_inventory", "record_free_lead_grant", "find_nearest_unclaimed_lead",
                     "send_free_lead_granted_email"):
            self.assertNotIn(gone, seg)


class TestNavigationAndSubscriptionsOffWording(_Base):

    def _nav(self, sub, limbo):
        with patch.object(main.database, "get_contractor_subscription", return_value=sub, create=True), \
             patch.object(main.database, "get_limbo_account", return_value=limbo, create=True):
            return main._nav_auth_state(_req())

    def test_dashboard_link_by_account_type(self):
        self.assertEqual(self._nav({"active": True}, None)["dashboard_url"], "/dashboard")
        self.assertEqual(self._nav(None, None)["dashboard_url"], "/my-leads")
        self.assertEqual(self._nav(None, {"email": EMAIL})["dashboard_url"], "/my-leads")
        self.assertEqual(self._nav({"active": False}, None)["dashboard_url"], "/my-leads")

    def _success(self, subs_open):
        with patch("main.HTMLResponse", _Body), \
             patch("main.payments.subscription_sales_enabled", return_value=subs_open, create=True):
            r = main.payment_success(_req(None, path="/payment/success"))
        return _text(r)

    def test_payment_success_points_to_my_leads_and_has_no_subscription_wording_when_off(self):
        page = self._success(False)
        self.assertIn('href="/my-leads"', page)
        self.assertIn("View My Leads", page)
        self.assertNotIn("View My Account", page)
        self.assertNotIn("subscription", page.lower())
        self.assertIn("printing and postage are included", page)

    def test_payment_success_keeps_the_subscription_note_when_subscriptions_are_on(self):
        page = self._success(True)
        self.assertIn("manage your subscription in My Account", page)
        self.assertIn('href="/my-leads"', page)

    def _welcome(self, subs_open):
        with patch.object(main.database, "get_db_conn", MagicMock()), \
             patch.object(main.letter_content, "get_contractor_settings", return_value=object()), \
             patch.object(main.database, "get_contractor_subscription", return_value=None, create=True), \
             patch.object(main.database, "first_offer_status", MagicMock(return_value={"eligible": True, "reason": None}), create=True), \
             patch.object(main, "_first_offer_stock_count", return_value=3), \
             patch.object(main, "_letter_setup_complete", return_value=False), \
             patch("main.HTMLResponse", _Body), \
             patch("main.payments.subscription_sales_enabled", return_value=subs_open, create=True), \
             patch.multiple(main.payments, create=True, FIRST_INTRO_PRICE_PENCE=499,
                            FIRST_OFFER_PLAN_KEYS=("single_lead_small", "single_lead_medium")):
            return _text(main.welcome_page(_req(path="/welcome")))

    def test_welcome_links_to_my_leads_and_hides_subscriptions_when_off(self):
        page = self._welcome(False)
        self.assertIn('<a href="/my-leads">My Leads</a>', page)
        self.assertIn('<a href="/account">My Account</a>', page)
        self.assertNotIn("Subscriptions", page)
        self.assertNotIn('<a href="/pricing">Subscriptions</a>', page)   # (the site footer's own "Packages" link is separate)
        self.assertIn("£4.99", page)

    def test_welcome_keeps_the_subscriptions_link_when_on(self):
        self.assertIn('<a href="/pricing">Subscriptions</a>', self._welcome(True))

    def _lead_row(self):
        return {
            "id": "3fae9c1e-fake-uuid", "ref": "26/P/1118/S73", "summary": "Fell one oak.", "council": "Newark",
            "price": 19, "plan_key": "single_lead_small", "job_category": {"key": "general", "label": "General",
            "icon": "general", "color": "#64748b"}, "discovered_at": None, "reg_date": None, "has_agent": None,
            "is_urgent": False, "score": "small", "area_label": "NG22, Newark", "badge_bg": "#000",
            "badge_color": "#fff", "badge_text": "NEW", "days_left": "3 days left",
        }

    def _signed_out_marketplace(self, subs_open):
        import test_marketplace_privacy_review as _t
        request = MagicMock(); request.cookies = {}; request.query_params = {}; request.headers = {}
        with patch("main.database.get_marketplace_leads_with_freshness", return_value=[self._lead_row()], create=True), \
             patch("main.database.get_subscriber_discount", return_value={"eligible": False, "discount_pct": 0}, create=True), \
             patch("main.database.get_contractor_subscription", return_value=None, create=True), \
             patch("main.database.get_area_capacity_status", return_value={"status": "unknown"}, create=True), \
             patch("main.database.release_expired_reservations", create=True), \
             patch("main.database.JOB_CATEGORIES", _t._real_database.JOB_CATEGORIES, create=True), \
             patch("main.database.EARLY_ACCESS_WINDOW_MINUTES", _t._real_database.EARLY_ACCESS_WINDOW_MINUTES, create=True), \
             patch("main._verify_session_cookie", return_value=None), \
             patch("main.HTMLResponse", _Body), \
             patch("main.payments.subscription_sales_enabled", return_value=subs_open, create=True):
            list_page = _text(main.marketplace_view(request))
            detail_page = _text(main.lead_detail_view("3fae9c1e-fake-uuid", request))
        return list_page, detail_page

    def test_signed_out_prompts_do_not_promise_a_member_discount_when_subscriptions_are_off(self):
        for page in self._signed_out_marketplace(False):
            self.assertNotIn("Sign in for your discount", page)
            self.assertNotIn("Already a member?", page)
            self.assertIn("Already have an account? Sign in to buy", page)
            self.assertIn("/login?next=", page)

    def test_signed_out_prompts_are_unchanged_when_subscriptions_are_on(self):
        for page in self._signed_out_marketplace(True):
            self.assertIn("Already a member? Sign in for your discount", page)

    def test_marketplace_header_button_follows_the_subscription_switch(self):
        # The "View Monthly Subscriptions" button (links to /pricing) must not
        # appear while subscription sales are off, and is unchanged when on.
        off_list, _ = self._signed_out_marketplace(False)
        on_list, _ = self._signed_out_marketplace(True)
        self.assertNotIn("View Monthly Subscriptions", off_list)
        self.assertIn("Statutory Planning Marketplace", off_list)
        self.assertIn("/checkout/", off_list)
        self.assertIn('<a href="/pricing" class="bg-emerald-600', on_list)
        self.assertIn("View Monthly Subscriptions", on_list)

    def test_marketplace_buy_panel_stacks_so_the_button_is_not_squeezed_on_a_phone(self):
        # The price panel used to sit side by side (price | button) below the sm breakpoint, which squeezed
        # "Buy This Lead" into a tall narrow sliver at ~375px. It now stacks at every width.
        page, _ = self._signed_out_marketplace(False)
        self.assertIn("rounded-xl p-4 flex flex-col gap-3 text-center", page)
        self.assertNotIn("flex sm:flex-col items-center sm:items-stretch", page)
        self.assertIn("Buy This Lead", page)

    def _marketplace_for(self, email=None, eligible=False, subscriber=False):
        request = MagicMock(); request.cookies = {}; request.query_params = {}; request.headers = {}
        sub = {"active": True} if subscriber else None
        with patch("main.database.get_marketplace_leads_with_freshness", return_value=[self._lead_row()], create=True), \
             patch("main.database.get_subscriber_discount", return_value={"eligible": False, "discount_pct": 0}, create=True), \
             patch("main.database.get_contractor_subscription", return_value=sub, create=True), \
             patch("main.database.get_area_capacity_status", return_value={"status": "unknown"}, create=True), \
             patch("main.database.release_expired_reservations", create=True), \
             patch("main.database.JOB_CATEGORIES", __import__("test_marketplace_privacy_review")._real_database.JOB_CATEGORIES, create=True), \
             patch("main.database.EARLY_ACCESS_WINDOW_MINUTES", 15, create=True), \
             patch("main._verify_session_cookie", return_value=email), \
             patch("main._viewer_first_offer_eligible", return_value=eligible), \
             patch("main.HTMLResponse", _Body), \
             patch("main.payments.subscription_sales_enabled", return_value=False, create=True):
            return _text(main.marketplace_view(request))

    def test_marketplace_explains_the_first_introduction_offer_and_its_limits(self):
        # Polish pass, 8 Oct 2026 (defect 4): same wording and limits as the homepage / Pricing promo.
        for page in (self._marketplace_for(None), self._marketplace_for("a@b.test", eligible=True)):
            self.assertIn("New to TreeKey?", page)
            self.assertIn("Your first introduction can be &pound;4.99.", page)
            self.assertIn("Printing and postage included. No subscription required.", page)
            self.assertIn("One per eligible business, on selected Standard opportunities.", page)
            self.assertIn('href="/pricing" class="underline font-bold"', page)

    def test_marketplace_offer_line_is_not_shown_to_accounts_that_cannot_use_it(self):
        self.assertNotIn("New to TreeKey?", self._marketplace_for("a@b.test", eligible=False))
        self.assertNotIn("New to TreeKey?", self._marketplace_for("a@b.test", eligible=True, subscriber=True))

    def test_the_address_placeholder_no_longer_promises_a_future_release(self):
        text = address_release.REDACTED_ADDRESS_PLACEHOLDER
        for gone in ("release pending", "legal review", "finalising", "before exact addresses are shown"):
            self.assertNotIn(gone, text)
        self.assertIn("Exact address kept confidential", text)
        self.assertEqual(text, "Exact address kept confidential. TreeKey posts your introduction without sharing the homeowner's address with you.")

    def _login(self, subs_open):
        request = MagicMock(); request.cookies = {}; request.query_params = {}; request.headers = {}
        with patch("main.payments.subscription_sales_enabled", return_value=subs_open, create=True):
            r = main.login_page(request, next="/checkout/single_lead_small?lead_id=1")
        return _text(r)

    def test_login_note_matches_the_state_of_subscription_sales(self):
        off = self._login(False)
        self.assertIn("Sign in to continue to checkout", off)
        self.assertNotIn("member discount", off)
        self.assertIn("Sign in to see your member discount on that lead", self._login(True))


if __name__ == "__main__":
    unittest.main()

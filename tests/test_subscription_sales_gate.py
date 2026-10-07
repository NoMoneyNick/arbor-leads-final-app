"""
test_subscription_sales_gate.py -- 6 Oct 2026, Nick's decision for the INITIAL
LAUNCH: sell only the one-off offers (GBP 4.99 first introduction and
single-lead purchases); NEW subscription sales are disabled in the UI and in
the server-side checkout path. Subscriptions are DEFERRED, not removed: the
plans, the webhook handling and stored subscriber records are untouched.

Covers, with the real payments.py behind a fake Stripe where it matters:
  1. payments.subscription_sales_enabled(): off by default; on only for
     1 / true / yes in SUBSCRIPTION_SALES_ENABLED.
  2. payments.create_checkout_session: a subscription plan is refused (None,
     Stripe never called) while off, created when the switch is on, and a
     one-off plan is unaffected either way.
  3. main.checkout() (GET) and main.checkout_post() (POST): a subscription
     plan redirects to /pricing BEFORE any sign-in or letter-setup detour and
     before Stripe; a one-off plan keeps its existing behaviour.
  4. The pricing page shows no subscription cards/links while off and still
     shows the one-off offers; with the switch on it is unchanged.
  5. The two teaser-email notes carry no subscription wording while off.
  6. Terms section 4, the refund FAQ and the account links match the one-off offers and
     keep existing subscribers' rights.

Run with:
    python -m unittest tests.test_subscription_sales_gate -v
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

import test_access_control  # noqa: E402,F401  (populates sys.modules stubs + imports main)
import main  # noqa: E402
import test_new_customer_journey as _journey  # noqa: E402

def _run(coro):
    """Run a coroutine on a private loop WITHOUT touching the thread's current
    event loop (asyncio.run() resets it to None, which breaks other test files
    that later call asyncio.get_event_loop() in the same process)."""
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


_PLANS = {
    "starter": {"name": "TreeKey Starter", "amount": 3900, "mode": "subscription"},
    "single_lead_small": {"name": "Single Lead Unlock (Entry)", "amount": 1900, "mode": "payment",
                          "badge": "Standard"},
}


def _req(path="/checkout/starter", cookie=None, query=None):
    r = MagicMock()
    r.cookies = {"treekey_contractor_session": cookie} if cookie else {}
    r.headers = {}
    r.query_params = query or {}
    r.url = MagicMock(path=path, query="")
    return r


class TestSwitch(unittest.TestCase):
    def _payments(self):
        pay, _created, _stripe = _journey._load_real_payments(MagicMock())
        return pay

    def test_off_by_default_and_on_only_for_explicit_values(self):
        pay = self._payments()
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("SUBSCRIPTION_SALES_ENABLED", None)
            self.assertFalse(pay.subscription_sales_enabled())
        for v in ("1", "true", "TRUE", "yes", " Yes "):
            with patch.dict(os.environ, {"SUBSCRIPTION_SALES_ENABLED": v}):
                self.assertTrue(pay.subscription_sales_enabled(), v)
        for v in ("", "0", "false", "no", "off"):
            with patch.dict(os.environ, {"SUBSCRIPTION_SALES_ENABLED": v}):
                self.assertFalse(pay.subscription_sales_enabled(), v)


class TestCreateCheckoutSession(unittest.TestCase):
    def _real(self):
        db = MagicMock()
        db.STRIPE_CHECKOUT_EXPIRY_MINUTES = 30
        pay, created, _stripe = _journey._load_real_payments(db)
        return pay, created

    def test_subscription_refused_server_side_while_off(self):
        pay, created = self._real()
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("SUBSCRIPTION_SALES_ENABLED", None)
            for key in ("starter", "growth", "arb_consultant", "commercial_forestry", "treekey_elite"):
                self.assertEqual(pay.PLANS[key]["mode"], "subscription")
                self.assertIsNone(pay.create_checkout_session(key, outcode="NG22"), key)
        self.assertEqual(created, [], "Stripe must never be called for a refused subscription")

    def test_subscription_allowed_when_switch_is_on(self):
        pay, created = self._real()
        with patch.dict(os.environ, {"SUBSCRIPTION_SALES_ENABLED": "1"}):
            url = pay.create_checkout_session("starter", outcode="NG22")
        self.assertEqual(url, "https://checkout.test/session")
        self.assertEqual(len(created), 1)
        self.assertEqual(created[0]["mode"], "subscription")

    def test_one_off_plan_unaffected_while_off(self):
        pay, created = self._real()
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("SUBSCRIPTION_SALES_ENABLED", None)
            url = pay.create_checkout_session("single_lead_small")
        self.assertEqual(url, "https://checkout.test/session")
        self.assertEqual(created[0]["mode"], "payment")


class TestCheckoutRoutes(unittest.TestCase):
    def setUp(self):
        os.environ["SESSION_SECRET"] = "test-session-secret"
        main._SESSION_SECRET = b"test-session-secret"

    def _run_get(self, plan_key, enabled, cookie=None):
        with patch("main.payments.PLANS", _PLANS, create=True), \
             patch("main.payments.subscription_sales_enabled", return_value=enabled, create=True), \
             patch("main.payments.create_checkout_session", MagicMock(return_value="https://stripe.test/x"), create=True) as cs:
            return main.checkout(plan_key, _req("/checkout/" + plan_key, cookie)), cs

    def test_get_subscription_goes_to_pricing_before_login_while_off(self):
        resp, cs = self._run_get("starter", enabled=False)
        self.assertEqual(resp.status_code, 303)
        self.assertEqual(resp.url, "/pricing?msg=packages_coming_soon")
        cs.assert_not_called()

    def test_get_subscription_unchanged_when_on(self):
        resp, _ = self._run_get("starter", enabled=True)
        self.assertEqual(resp.status_code, 303)
        self.assertTrue(resp.url.startswith("/login?next="), resp.url)

    def test_get_one_off_unchanged_while_off(self):
        resp, _ = self._run_get("single_lead_small", enabled=False)
        self.assertTrue(resp.url.startswith("/login?next="), resp.url)

    def test_post_subscription_goes_to_pricing_while_off(self):
        with patch("main.payments.PLANS", _PLANS, create=True), \
             patch("main.payments.subscription_sales_enabled", return_value=False, create=True), \
             patch("main.payments.create_checkout_session", MagicMock(return_value="https://stripe.test/x"), create=True) as cs:
            resp = _run(main.checkout_post("starter", _req("/checkout/starter"), outcode="NG22",
                                                  radius=15, job_size="all", agree_terms="on"))
        self.assertEqual(resp.status_code, 303)
        self.assertEqual(resp.url, "/pricing?msg=packages_coming_soon")
        cs.assert_not_called()

    def test_post_subscription_still_reaches_login_gate_when_on(self):
        with patch("main.payments.PLANS", _PLANS, create=True), \
             patch("main.payments.subscription_sales_enabled", return_value=True, create=True):
            resp = _run(main.checkout_post("starter", _req("/checkout/starter"), outcode="NG22",
                                                  radius=15, job_size="all", agree_terms="on"))
        self.assertTrue(resp.url.startswith("/login?next="), resp.url)


class TestPricingPage(unittest.TestCase):
    def _page(self, enabled, msg=""):
        req = MagicMock(); req.query_params = {"msg": msg} if msg else {}; req.cookies = {}
        with patch("main.HTMLResponse", side_effect=lambda content=None, *a, **k: content), \
             patch("main.payments.PLANS", _PLANS, create=True), \
             patch("main.payments.subscription_sales_enabled", return_value=enabled, create=True), \
             patch("main.payments.plan_description", return_value="Includes one letter.", create=True):
            return main.pricing(req)

    def test_off_hides_every_subscription_offer_and_keeps_one_off_offers(self):
        page = self._page(False)
        self.assertNotIn("/checkout/starter", page)
        self.assertNotIn("Select Your Dedicated Subscription Tier", page)
        self.assertNotIn("Active subscribers get an early-access alert", page)
        self.assertNotIn("introductions included each month", page)
        self.assertNotIn("Packages & Pricing", page)
        self.assertIn("/checkout/single_lead_small", page)
        self.assertIn("Buy one introduction at a time", page)
        self.assertIn("Every introduction includes printing and postage.", page)

    def test_off_banners_explain_without_offering_a_subscription(self):
        for msg in ("packages_coming_soon", "no_subscription"):
            page = self._page(False, msg)
            self.assertIn("Monthly packages aren't available at the moment", page, msg)
            self.assertNotIn("Pick a tier below to subscribe", page, msg)

    def test_on_is_unchanged(self):
        page = self._page(True)
        self.assertIn("Select Your Dedicated Subscription Tier", page)
        self.assertIn("Packages & Pricing", page)


class TestHomepage(unittest.TestCase):
    _PLANS = {
        "starter": {"name": "TreeKey Starter", "amount": 3900, "mode": "subscription"},
        "commercial_forestry": {"name": "CF", "amount": 15900, "mode": "subscription"},
        "treekey_elite": {"name": "Elite", "amount": 24900, "mode": "subscription"},
        "single_lead_small": {"name": "S", "amount": 1900, "mode": "payment", "badge": "Standard"},
    }

    def _page(self, enabled):
        req = MagicMock(); req.cookies = {}; req.query_params = {}; req.headers = {}
        with patch("main.HTMLResponse", side_effect=lambda content=None, *a, **k: content), \
             patch("main.payments.PLANS", self._PLANS, create=True), \
             patch("main.payments.subscription_sales_enabled", return_value=enabled, create=True), \
             patch("main.payments.plan_description", return_value="d", create=True), \
             patch.object(main.database, "get_db_conn", side_effect=Exception("no db"), create=True):
            page = main.public_homepage(req)
        return page if isinstance(page, str) else page.decode()

    def test_off_removes_subscription_wording_but_keeps_the_marketplace_route(self):
        page = self._page(False)
        for gone in ("Pick a monthly package", "Not ready for a subscription", "rolling monthly",
                     "Compare all five packages", "tk-pkgs-3"):
            self.assertNotIn(gone, page, gone)
        self.assertIn("Buy one introduction at a time.", page)
        self.assertIn("There is no subscription or contract.", page)
        self.assertIn('href="/marketplace"', page)

    def test_on_is_unchanged(self):
        page = self._page(True)
        for kept in ("Pick a monthly package", "Not ready for a subscription", "rolling monthly",
                     "Compare all five packages"):
            self.assertIn(kept, page, kept)


class TestFaq(unittest.TestCase):
    def _page(self, enabled):
        req = MagicMock(); req.cookies = {}; req.query_params = {}; req.headers = {}
        with patch("main.HTMLResponse", side_effect=lambda content=None, *a, **k: content), \
             patch("main.payments.subscription_sales_enabled", return_value=enabled, create=True):
            page = _run(main.faq_page(req))
        return page if isinstance(page, str) else page.decode()

    def test_off_uses_one_off_wording(self):
        page = self._page(False)
        self.assertIn("There is no subscription or contract.", page)
        self.assertNotIn("A subscription includes a set number of opportunities", page)
        self.assertNotIn("choose a subscription tier", page)

    def test_on_is_unchanged(self):
        page = self._page(True)
        self.assertIn("A subscription includes a set number of opportunities", page)
        self.assertIn("choose a subscription tier", page)


class TestEmailNotes(unittest.TestCase):
    def test_teaser_notes_follow_the_switch(self):
        import importlib.util
        spec = importlib.util.spec_from_file_location("notifications_under_test", os.path.join(_APP_DIR, "notifications.py"))
        n = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(n)
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("SUBSCRIPTION_SALES_ENABLED", None)
            for fn in (n._teaser_pricing_note_html, n._teaser_plans_note_html):
                out = fn()
                self.assertNotIn("Subscribe", out)
                self.assertNotIn("/pricing", out)
                self.assertIn("/marketplace", out)
        with patch.dict(os.environ, {"SUBSCRIPTION_SALES_ENABLED": "1"}):
            self.assertIn("Subscribe from", n._teaser_pricing_note_html())
            self.assertIn("See plans", n._teaser_plans_note_html())


class TestTermsAndRefundWording(unittest.TestCase):
    """Terms section 4, the refund FAQ and the account links match the one-off
    offers while preserving existing subscribers' rights (6 Oct 2026)."""

    def _terms_page(self):
        req = MagicMock(); req.cookies = {}; req.query_params = {}; req.headers = {}
        with patch("main.HTMLResponse", side_effect=lambda content=None, *a, **k: content):
            page = _run(main.terms_of_service(req))
        return page if isinstance(page, str) else page.decode()

    def _faq_page(self):
        req = MagicMock(); req.cookies = {}; req.query_params = {}; req.headers = {}
        with patch("main.HTMLResponse", side_effect=lambda content=None, *a, **k: content), \
             patch("main.payments.subscription_sales_enabled", return_value=False, create=True):
            page = _run(main.faq_page(req))
        return page if isinstance(page, str) else page.decode()

    _SEND_OR_REFUND = "If your letter is not sent, we will send it or, at your choice, refund the amount you paid for it."
    _CANCEL = "You may cancel a purchase by emailing <strong>nick@treekey.uk</strong>."
    _CAP = "Total refunds for a purchase never exceed the amount you paid, taking account of any earlier refunds."
    _NO_RECALL_PROMISE = "we cannot promise that it can be stopped"
    _PROMISE = ("email <strong>nick@treekey.uk</strong> telling us which purchase it concerns, and we will, at your choice, "
                "give you a replacement introduction of equivalent value or a refund for that purchase.")

    def test_terms_section_4_describes_the_product_and_keeps_subscriber_rights(self):
        page = self._terms_page()
        i = page.index("4. Purchases, Pricing, and Payment")
        sec = page[i:page.index("<h2", i + 10)]
        self.assertNotIn("4. Subscriptions, Pricing, and Payment", page)
        # what the customer buys and receives (CURRENT_BUSINESS_MODEL.md)
        self.assertIn("printed and posted introduction", sec)
        self.assertIn("postage included", sec)
        self.assertIn("You do not receive the homeowner's name, address or contact details", sec)
        self.assertIn("the homeowner contacts you directly", sec)
        self.assertIn("We do not guarantee that a homeowner will reply", sec)
        self.assertIn("New subscription plans are not currently offered.", sec)
        self.assertIn("A one-off purchase is a single payment", sec)
        # no claim of access to lead data, no blanket non-refundable wording, no internal process in customer text
        for banned in ("immediate access", "proprietary", "non-refundable", "unlock your", "print provider",
                       "confirmed as dispatched", "manual", "void", "Stripe Dashboard"):
            self.assertNotIn(banned, sec, banned)
        self.assertNotIn("By subscribing, you authorize recurring charges", page)
        self.assertNotIn("purchasing a subscription", page)
        # D4 (Nick, 7 Oct 2026): the existing-subscriber paragraph and its cross-references are NOT in customer text;
        # the subscription policy is kept internally (operator guide 32, CURRENT_BUSINESS_MODEL.md)
        for gone in ("Existing subscriptions", "existing subscription", "pause further renewal", "30 days",
                     "billing period", "recurring charges"):
            self.assertNotIn(gone, sec, gone)
        self.assertNotIn("terminate an existing subscription in accordance with Section 4", page)
        # refunds: stated cases, capped at what was paid, statutory rights preserved
        self.assertIn(self._CANCEL, sec)
        self.assertIn(self._SEND_OR_REFUND, sec)
        self.assertIn(self._CAP, sec)
        self.assertIn(self._NO_RECALL_PROMISE, sec)
        self.assertIn("our cancellation process confirms that your letter was stopped before it was submitted", sec)
        self.assertIn("Lead-Quality Promise", sec)
        self.assertIn(self._PROMISE, sec)
        # approved 7 Oct 2026: no screenshot of information the customer never receives, no "Lead" remedy wording,
        # no "recall impossible" claim, no general-support address in the refund and promise paragraphs
        for banned2 in ("screenshot", "replacement Lead", "refund for that Lead", "reaches you despite", "cannot be recalled",
                        "contact@treekey.co.uk"):
            self.assertNotIn(banned2, sec, banned2)
        self.assertIn("Your statutory rights.", sec)
        self.assertIn("affects any statutory rights you have that cannot lawfully be excluded", sec)

    def test_refund_faq_matches_the_terms_and_preserves_statutory_rights(self):
        faq = self._faq_page()
        i = faq.index("You may cancel a purchase by emailing")
        ans = faq[i:faq.index("taking account of any earlier refunds.", i) + 60]
        self.assertIn(self._CANCEL, ans)
        self.assertIn(self._SEND_OR_REFUND, ans)
        self.assertIn(self._CAP, ans)
        self.assertIn(self._NO_RECALL_PROMISE, ans)
        self.assertIn("Lead-Quality Promise", ans)
        self.assertNotIn("Existing subscribers", ans)
        self.assertNotIn("Section 4", ans)
        for banned in ("non-refundable", "immediate access", "proprietary", "the same policy that applies",
                       "screenshot", "contact@treekey.co.uk"):
            self.assertNotIn(banned, ans, banned)
        # the Promise entry: same remedy, no screenshot, no Lead wording
        j = faq.index("What if an opportunity turns out not to be tree work at all?")
        prom = faq[j:j + 900]
        self.assertIn(self._PROMISE, prom)
        self.assertNotIn("screenshot", prom)
        self.assertNotIn("replacement lead", prom.lower())
        # same cancel / refund / promise text in both places
        terms = self._terms_page()
        for part in (self._CANCEL, self._SEND_OR_REFUND, self._CAP, self._PROMISE):
            self.assertIn(part, terms)

    def test_account_request_form_gives_email_route_not_a_pricing_link(self):
        db = MagicMock(); db.ACCOUNT_REQUEST_NOTE_MAX = 1000
        with patch("main.database", db):
            out = main._account_request_form_html("a@b.co", True)
            free = main._account_request_form_html("a@b.co", False)
        self.assertIn("This request does not cancel it.", out)
        self.assertIn("mailto:contact@treekey.co.uk", out)
        self.assertNotIn("/pricing", out)
        self.assertNotIn("Manage subscription", free)

    def test_account_page_links_distinguish_subscribers_from_free_accounts(self):
        def page(active, enabled):
            sub = {"active": True, "tier": "starter", "quota": 6, "delivered": 1, "outcode": "AB1",
                   "radius": 15, "subscribed_at": "2026-09-01"} if active else None
            req = MagicMock(); req.cookies = {"treekey_contractor_session": "x"}; req.query_params = {}
            req.headers = {}
            db = MagicMock()
            db.get_contractor_subscription.return_value = sub
            db.get_limbo_account.return_value = None
            db.get_contractor_settings.return_value = {}
            db.get_payment_history_for_contractor.return_value = []
            with patch("main._verify_session_cookie", return_value="a@b.co"), \
                 patch("main.database", db), \
                 patch("main.letter_content.get_contractor_settings", return_value=None), \
                 patch("main.HTMLResponse", side_effect=lambda content=None, *a, **k: content), \
                 patch("main.payments.subscription_sales_enabled", return_value=enabled, create=True):
                out = main.my_account_view(req)
            return out if isinstance(out, str) else getattr(out, "body", b"").decode()
        paid_off = page(True, False)
        self.assertIn("mailto:contact@treekey.co.uk", paid_off)
        self.assertIn("Your plan is unchanged.", paid_off)
        self.assertNotIn("Change Plan", paid_off)
        free_off = page(False, False)
        self.assertIn("Browse the Marketplace", free_off)
        self.assertNotIn("Upgrade to a Subscription", free_off)
        self.assertIn("Change Plan", page(True, True))
        self.assertIn("Upgrade to a Subscription", page(False, True))


class TestPortableDateFormats(unittest.TestCase):
    """The "%-d" strftime flag works on Linux but raises ValueError on Windows (7 Oct 2026, seen when
    the launcher ran these tests on Nick's PC). Keep it out of the files this change touches."""

    def test_no_non_portable_strftime_flags_in_main_and_notifications(self):
        import os, re
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        for name in ("main.py", "notifications.py"):
            with open(os.path.join(root, name), encoding="utf-8") as fh:
                for n, line in enumerate(fh, 1):
                    self.assertIsNone(re.search(r"%[-#][dmHIjyUeSMBbY]", line), "%s:%d uses a Windows-invalid strftime flag" % (name, n))

    def test_filed_date_text_is_unchanged(self):
        # notifications is stubbed by this module, so compile just the real function from the source file
        import ast, datetime, os
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        with open(os.path.join(root, "notifications.py"), encoding="utf-8") as fh:
            tree = ast.parse(fh.read())
        fn = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "_format_filed_date")
        ns = {}
        exec(compile(ast.Module(body=[fn], type_ignores=[]), "notifications.py", "exec"), ns)
        fmt = ns["_format_filed_date"]
        self.assertEqual(fmt(datetime.date(2026, 10, 7)), "7 October 2026")
        self.assertEqual(fmt(datetime.date(2026, 10, 17)), "17 October 2026")
        self.assertEqual(fmt("2026-10-07"), "2026-10-07")
        self.assertEqual(fmt(None), "")


class TestLegalOperatorIdentity(unittest.TestCase):
    """7 Oct 2026, Nick's confirmed identity: the legal operator and data controller is Nicholas Michael Secular,
    a sole trader trading as TreeKey. Vector Data Labs is not a legal entity and must not appear in the Terms or
    privacy page; the supplied PO Box is described as a correspondence address, not as a place of establishment."""

    _OPERATOR = "Nicholas Michael Secular, a sole trader"
    _ADDRESS = "Nicholas Secular / TreeKey, Unit 173384, PO Box 7169, Poole, BH15 9EL"

    def _terms(self):
        req = MagicMock(); req.cookies = {}; req.query_params = {}; req.headers = {}
        with patch("main.HTMLResponse", side_effect=lambda content=None, *a, **k: content):
            page = _run(main.terms_of_service(req))
        return page if isinstance(page, str) else page.decode()

    def _privacy(self):
        page = _run(main.privacy_policy())
        return page if isinstance(page, str) else page.decode()

    def test_terms_and_privacy_name_the_sole_trader_operator_and_not_vector_data_labs(self):
        for name, page in (("terms", self._terms()), ("privacy", self._privacy())):
            self.assertNotIn("Vector Data Labs", page, name)
            self.assertIn("TreeKey is a trading name of " + self._OPERATOR, page, name)
            self.assertIn("(a PO Box)", page, name)
            self.assertIn(self._ADDRESS, page, name)
            self.assertIn("correspondence address", page, name)
            self.assertNotIn("registered office", page.lower(), name)

    def test_terms_operator_sentence_and_privacy_controller_sentence(self):
        self.assertIn('operated by Nicholas Michael Secular, a sole trader trading as TreeKey ("we", "us", "our")', self._terms())
        priv = self._privacy()
        self.assertIn("who is the data controller for the personal data described below", priv)
        self.assertIn("For privacy matters, contact <strong>nick@treekey.uk</strong>.", priv)

    def test_general_support_contact_in_terms_is_unchanged(self):
        self.assertIn("Questions about these Terms can be sent to <strong>contact@treekey.co.uk</strong>.", self._terms())


if __name__ == "__main__":
    unittest.main()

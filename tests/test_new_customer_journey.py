"""
test_new_customer_journey.py -- 2026-09-30, the new-customer journey fix.

Covers only the paths this change touched:
  * a verified account with saved details but no subscription lands on the
    welcome page (never /pricing?msg=no_subscription), with `next` preserved
    and feature restrictions unchanged;
  * the welcome page: signed-in only, no subscription needed, the agreed
    offer copy for eligible accounts, honest no-stock message, no offer for
    ineligible accounts;
  * checkout: `first_offer=1` only REQUESTS the offer; a refusal charges
    nothing and says so;
  * the REAL payments.create_checkout_session (loaded fresh with a fake
    Stripe/database): price and eligibility decided server-side, standard
    opportunities only, lead released when the redemption is lost, ordinary
    purchases untouched;
  * telephone validation (server side, inline error, preserved values);
  * the Service-area capitalisation suggestion helper.
The SQL (one redemption per account/phone/business, duplicate and concurrent
redemption) is proven on a real PostgreSQL by
tests/postgres_concurrency/run_first_offer_real_db_tests.py.

Run with:  python -m unittest tests.test_new_customer_journey -v
"""
import asyncio
import importlib.util
import os
import sys
import types
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
_APP_DIR = os.path.dirname(_THIS_DIR)
sys.path.insert(0, _APP_DIR)
sys.path.insert(0, _THIS_DIR)

import test_access_control  # noqa: E402,F401
import main  # noqa: E402
import letter_content  # noqa: E402
from test_letter_setup_checkout_gate import _mock_request, _PAYMENT_PLAN  # noqa: E402


def _run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def _cookie(email="dave@apex-trees.co.uk"):
    main._SESSION_SECRET = b"test-session-secret"
    return main._sign_session_cookie(email)


class _Refusal(Exception):
    def __init__(self, reason):
        super().__init__(reason)
        self.reason = reason


OFFER_CONSTS = dict(FIRST_INTRO_PRICE_PENCE=499, FIRST_OFFER_PLAN_KEYS=("single_lead_small", "single_lead_medium"))


class TestLoginDestination(unittest.TestCase):

    def _login(self, *, has_settings=True, sub=None, limbo=None, next_url=None):
        settings = object() if has_settings else None
        with patch.object(main.database, "get_db_conn", MagicMock()), \
             patch.object(main.letter_content, "get_contractor_settings", return_value=settings), \
             patch.object(main.database, "get_contractor_subscription", return_value=sub), \
             patch.object(main.database, "get_limbo_account", return_value=limbo):
            return main._login_session_response("dave@apex-trees.co.uk", next_url=next_url)

    def test_valid_non_subscriber_lands_on_welcome_with_a_session_cookie(self):
        r = self._login()
        self.assertEqual(r.url, "/welcome")
        self.assertNotIn("no_subscription", r.url)

    def test_checkout_continuation_is_preserved(self):
        self.assertEqual(self._login(next_url="/checkout/single_lead_small?lead_id=L1&first_offer=1").url,
                         "/checkout/single_lead_small?lead_id=L1&first_offer=1")

    def test_unsafe_next_is_not_followed(self):
        self.assertEqual(self._login(next_url="https://evil.example/x").url, "/welcome")

    def test_account_without_details_still_goes_to_the_signup_form(self):
        self.assertEqual(self._login(has_settings=False).url, "/free-account")

    def test_subscriber_and_limbo_destinations_unchanged(self):
        self.assertEqual(self._login(sub={"active": True}).url, "/dashboard")
        self.assertEqual(self._login(limbo={"email": "x"}).url, "/free-dashboard")

    def test_pricing_banner_no_longer_asks_for_another_account(self):
        req = MagicMock(); req.query_params = {"msg": "no_subscription"}; req.cookies = {}
        with patch("main.HTMLResponse", side_effect=lambda content=None, *a, **k: content):
            page = main.pricing(req)
        self.assertNotIn("create your account first", page)
        self.assertNotIn("create another account", page.lower())
        self.assertIn("There is no active subscription on this account", page)


class TestPricingCopy(unittest.TestCase):

    def test_unsupported_claims_are_gone_and_the_agreed_description_is_present(self):
        req = MagicMock(); req.query_params = {}; req.cookies = {}
        with patch("main.HTMLResponse", side_effect=lambda content=None, *a, **k: content), \
             patch.object(main.payments, "PLANS", {}, create=True):
            page = main.pricing(req)
        low = page.lower()
        for phrase in ("photo-verified", "100% exclusive", "bidding wars"):
            self.assertNotIn(phrase, low)
        # 2026-10-01: agreed USP block sits above the package cards.
        self.assertIn("Every introduction includes printing and postage.", page)
        self.assertIn("Your approved letter introduces your business. Interested homeowners "
                      "contact you directly, and TreeKey won't sell the same introduction to another contractor.", page)
        self.assertLess(page.index("Every introduction includes printing and postage."),
                        page.index("Select Your Dedicated Subscription Tier"))
        # Obsolete comparison / creed content is replaced, not duplicated.
        for gone in ("Bark", "Checkatrade", "TrustATrader", "competing contractors",
                     "100% committed", "First-Mover", "tank of diesel", "One job easily covers",
                     "Creed", "Opposite of Directories", "Real-World Math"):
            self.assertNotIn(gone, page)
        self.assertEqual(page.count("won't sell the same introduction"), 1)

    def test_plan_descriptions_no_longer_claim_100_percent_exclusive(self):
        with open(os.path.join(_APP_DIR, "payments.py"), encoding="utf-8") as fh:
            src = fh.read()
        self.assertNotIn('"description": "100% Exclusive', src)


class TestHomepageCopy(unittest.TestCase):

    def test_unsupported_exclusivity_claims_are_gone_and_one_contractor_is_stated(self):
        with open(os.path.join(_APP_DIR, "main.py"), encoding="utf-8") as fh:
            src = fh.read()
        self.assertNotIn("100% Exclusive Leads", src)
        self.assertNotIn("100% Exclusive &mdash;", src)
        self.assertNotIn("sell the same job to 5 different contractors", src)
        self.assertIn("Each introduction is sold to one contractor only", src)

    def test_usp_sits_immediately_below_the_hero_with_the_agreed_copy(self):
        with open(os.path.join(_APP_DIR, "main.py"), encoding="utf-8") as fh:
            src = fh.read()
        h = "Your business. Your introduction. Their choice."
        body = ("Choose a local tree-work opportunity. We print and post your approved introduction, "
                "and interested homeowners contact you directly. TreeKey sells each introduction to one contractor only.")
        self.assertEqual(src.count(h), 1)
        self.assertIn(body, src)
        end_hero = src.index("    </main>\n\n    <!-- 2026-10-01: the USP, immediately below the main hero. -->")
        self.assertLess(end_hero, src.index(h))
        self.assertLess(src.index(h), src.index('<section id="radar"'))

    def test_hero_to_usp_gap_is_compact(self):
        with open(os.path.join(_APP_DIR, "main.py"), encoding="utf-8") as fh:
            src = fh.read()
        a = src.index("def public_homepage"); b = src.index("\n@app.", a)
        seg = src[a:b]
        main_tag = seg[seg.index('<main class="relative overflow-hidden'):]
        main_tag = main_tag[:main_tag.index(">") + 1]
        self.assertIn('style="padding-bottom:0;"', main_tag)           # old pb-24 / lg:pb-32 spacer neutralised
        self.assertIn("padding:22px 16px 24px;", seg)                    # compact USP section padding
        self.assertIn(".tk-hero-feed { margin-bottom: 0 !important; }", seg.replace("{{", "{").replace("}}", "}"))
        self.assertIn(".tk-hero-logo { margin-bottom: 0 !important; }", seg.replace("{{", "{").replace("}}", "}"))
        for bad in ("min-height", "margin-top:-", "margin-top: -", "-mt-"):
            self.assertNotIn(bad, seg[seg.index('<section id="usp"'):seg.index('<section id="radar"')])

    def test_homepage_no_longer_claims_one_job_pays_for_the_year(self):
        with open(os.path.join(_APP_DIR, "main.py"), encoding="utf-8") as fh:
            src = fh.read()
        self.assertNotIn("One job pays for the year", src)
        self.assertNotIn("{payments.plan_roi(_tier_key, _tier)}", src)


class TestSharedChromeStyles(unittest.TestCase):
    """2026-10-01: the shared header/footer carry their own stylesheet, so
    pages that never link /static/tailwind.css (My Account, My Leads) no
    longer show browser-default blue/purple links and cramped spacing."""

    def _req(self, cookie=False):
        r = MagicMock(); r.cookies = {"treekey_contractor_session": _cookie()} if cookie else {}
        return r

    def test_nav_and_footer_are_self_contained(self):
        nav = main._shared_nav_html(self._req())
        foot = main._shared_footer_html()
        self.assertIn("<style>", nav); self.assertIn(".tk-nav {", nav)
        self.assertIn("<style>", foot); self.assertIn(".tk-footer {", foot)
        # link, visited and focus states exist
        self.assertIn(".tk-footer-links a:visited", foot)
        self.assertIn(".tk-footer-links a:focus-visible", foot)
        self.assertIn(".tk-nav a:focus-visible", nav)
        self.assertIn(".tk-l-radar:visited", nav)
        # the small T/k mark keeps its existing size
        self.assertIn("height:28px", foot); self.assertIn("max-width:64px", foot)
        # no Tailwind-only layout classes left in the chrome
        for cls in ("hidden lg:flex", "bg-slate-950", "text-slate-"):
            self.assertNotIn(cls, nav); self.assertNotIn(cls, foot)

    def test_account_and_leads_pages_use_the_site_font_and_shared_chrome(self):
        with open(os.path.join(_APP_DIR, "main.py"), encoding="utf-8") as fh:
            src = fh.read()
        for fn in ("def my_account_view", "def my_leads_view"):
            a = src.index(fn); b = src.index("\n@app.", a)
            seg = src[a:b]
            self.assertIn('font-family: "Inter", ui-sans-serif, system-ui, sans-serif;', seg)
            self.assertIn("_shared_nav_html(request)", seg)
            self.assertIn("_shared_footer_html()", seg)

    def test_homepage_uses_the_shared_footer_and_nav_css(self):
        with open(os.path.join(_APP_DIR, "main.py"), encoding="utf-8") as fh:
            src = fh.read()
        a = src.index("def public_homepage"); b = src.index("\n@app.", a)
        seg = src[a:b]
        self.assertIn("{_shared_footer_html()}", seg)
        self.assertIn("<style>{_SHARED_NAV_CSS}</style>", seg)
        self.assertNotIn('<footer class="border-t border-slate-800', seg)

    def test_phone_menu_carries_secondary_account_links(self):
        out = main._nav_auth_block_html(self._req()) + main._nav_auth_panel_html(self._req())
        self.assertIn("tk-hide-sm", out)       # Create Account hidden on phones in the bar ...
        self.assertIn("tk-mpanel-auth", out)   # ... and offered in the menu instead
        self.assertIn('href="/login" class="tk-auth-btn"', out)
        with patch.object(main.database, "get_contractor_subscription", return_value=None, create=True), \
             patch.object(main.database, "get_limbo_account", return_value=None, create=True):
            li = main._nav_auth_block_html(self._req(True)) + main._nav_auth_panel_html(self._req(True))
        self.assertIn('href="/account"', li); self.assertIn('href="/logout"', li)

    def test_no_free_lead_wording_on_any_account_state(self):
        for sub, limbo in ((None, None), (None, {"customer_name": "L", "signed_up_at": "2026-07-01"}),
                           ({"active": True, "tier": "starter", "quota": 6, "delivered": 1,
                             "outcode": "LS1", "radius": 15, "customer_name": "S", "subscribed_at": "2026-08-01"}, None)):
            r = self._req(True)
            with patch.object(main.database, "get_contractor_subscription", return_value=sub, create=True), \
                 patch.object(main.database, "get_limbo_account", return_value=limbo, create=True), \
                 patch.object(main.database, "get_contractor_settings", return_value={}, create=True), \
                 patch.object(main.database, "get_payment_history_for_contractor", return_value=[], create=True), \
                 patch.object(main.database, "get_db_conn", MagicMock(), create=True), \
                 patch.object(main.letter_content, "get_contractor_settings", return_value=None), \
                 patch("main.HTMLResponse", side_effect=lambda content=None, *a, **k: content):
                page = main.my_account_view(r)
            self.assertNotIn("free lead", page.lower())
            self.assertNotIn("One free lead per account", page)


class TestPackageCardsAndOfferPromo(unittest.TestCase):
    """2026-10-01: shared package-card layout + conditional GBP 4.99 invitation."""

    AGREED = {  # key: (price GBP, introductions, discount %)
        "starter": (39, 6, 10), "growth": (79, 10, 15), "arb_consultant": (99, 8, 15),
        "commercial_forestry": (159, 14, 20), "treekey_elite": (249, 20, 25),
    }

    def test_card_facts_match_the_enforced_entitlements_and_agreed_prices(self):
        import re
        with open(os.path.join(_APP_DIR, "database.py"), encoding="utf-8") as fh:
            dbsrc = fh.read()
        with open(os.path.join(_APP_DIR, "payments.py"), encoding="utf-8") as fh:
            paysrc = fh.read()
        quotas = dict(re.findall(r'"(\w+)": (\d+),', dbsrc[dbsrc.index("TIER_QUOTAS = {"):dbsrc.index("}", dbsrc.index("TIER_QUOTAS = {"))]))
        disc = dict(re.findall(r'"(\w+)": (\d+),', dbsrc[dbsrc.index("TIER_DISCOUNT_PCT = {"):dbsrc.index("}", dbsrc.index("TIER_DISCOUNT_PCT = {"))]))
        for key, (price, intros, pct) in self.AGREED.items():
            f = main._PACKAGE_FACTS[key]
            self.assertEqual((f["intros"], f["discount"]), (intros, pct), key)
            self.assertEqual((int(quotas[key]), int(disc[key])), (intros, pct), key)
            block = paysrc[paysrc.index(f'"{key}": {{'):]
            self.assertIn(f'"amount": {price}00,', block[:3500], key)

    def test_card_structure_and_no_unsupported_wording(self):
        plan = {"name": "TreeKey Starter", "amount": 3900, "mode": "subscription", "badge": "Most Popular"}
        card = main._package_card_html("starter", plan, cta_href="/checkout/starter")
        for needle in ("TreeKey Starter", "&pound;39", "<b>6</b> introductions included each month",
                       "Printing and postage included", "10% off additional marketplace introductions",
                       "Alerts when matching opportunities are found.", 'href="/checkout/starter"', "Choose Starter"):
            self.assertIn(needle, card)
        for banned in ("Most Popular", "Claim Tailored", "Secure Priority", "See This Tier",
                       "matching lead is filed", "Real-World Math"):
            self.assertNotIn(banned, card)
        self.assertEqual(main._package_card_html("unknown_plan", plan, cta_href="/x"), "")

    def _promo(self, *, cookie, sub=None, offer=None, boom=False):
        r = MagicMock(); r.cookies = {"treekey_contractor_session": _cookie()} if cookie else {}
        fo = MagicMock(side_effect=RuntimeError("x")) if boom else MagicMock(return_value=offer or {"eligible": False})
        with patch.object(main.database, "get_contractor_subscription", return_value=sub, create=True), \
             patch.object(main.database, "first_offer_status", fo, create=True), \
             patch.object(main.payments, "FIRST_INTRO_PRICE_PENCE", 499, create=True):
            return main._first_offer_promo_html(r)

    def test_visitor_sees_the_conditional_offer_with_agreed_copy(self):
        out = self._promo(cookie=False)
        self.assertIn("New to TreeKey? Try your first eligible introduction for &pound;4.99.", out)
        self.assertIn("Printing and postage included. No subscription required. One per eligible business, on selected Standard opportunities.", out)
        self.assertIn("Find my first introduction", out)
        self.assertIn('href="/login?next=%2Fwelcome"', out)

    def test_eligible_signed_in_account_gets_the_existing_offer_destination(self):
        out = self._promo(cookie=True, offer={"eligible": True, "reason": None})
        self.assertIn('href="/marketplace"', out)
        self.assertIn("Find my first introduction", out)

    def test_ineligible_accounts_are_never_told_they_qualify(self):
        for kw in (dict(offer={"eligible": False, "reason": "offer_used"}),
                   dict(offer={"eligible": False, "reason": "offer_in_progress"}),
                   dict(sub={"active": True}, offer={"eligible": True}),   # subscriber, even if the check said yes
                   dict(boom=True)):                                         # lookup failure fails closed
            self.assertEqual(self._promo(cookie=True, **kw), "", kw)

    def test_homepage_offer_is_the_same_component_placed_under_the_usp(self):
        # hero variant: new headline, large price, same destinations and gates
        out = self._promo_hero(cookie=False)
        self.assertIn('Your first introduction for <span class="tk-offer-amt">&pound;4.99</span>', out)
        self.assertIn("Printing and postage included. No subscription required.", out)
        self.assertIn("One per eligible business, on selected Standard opportunities.", out)
        self.assertIn("Find my first introduction", out)
        self.assertIn('href="/login?next=%2Fwelcome"', out)
        self.assertNotIn("animation", out.lower())
        out2 = self._promo_hero(cookie=True, offer={"eligible": True, "reason": None})
        self.assertIn('href="/marketplace"', out2)
        for kw in (dict(offer={"eligible": False, "reason": "offer_used"}),
                   dict(sub={"active": True}, offer={"eligible": True}), dict(boom=True)):
            self.assertEqual(self._promo_hero(cookie=True, **kw), "", kw)
        with open(os.path.join(_APP_DIR, "main.py"), encoding="utf-8") as fh:
            src = fh.read()
        a = src.index("def public_homepage"); b = src.index("\n@app.", a)
        seg = src[a:b]
        self.assertEqual(seg.count("{_homepage_offer_promo}"), 1)          # moved, not duplicated
        self.assertIn("_first_offer_promo_html(request, hero=True)", seg)
        usp = seg.index('<section id="usp"'); radar = seg.index('<section id="radar"')
        self.assertLess(usp, seg.index("{_homepage_offer_promo}"))
        self.assertLess(seg.index("{_homepage_offer_promo}"), radar)        # directly under the USP, above the radar
        self.assertLess(seg.index("{_homepage_offer_promo}"), seg.index("Featured packages"))

    def _promo_hero(self, *, cookie, sub=None, offer=None, boom=False):
        r = MagicMock(); r.cookies = {"treekey_contractor_session": _cookie()} if cookie else {}
        fo = MagicMock(side_effect=RuntimeError("x")) if boom else MagicMock(return_value=offer or {"eligible": False})
        with patch.object(main.database, "get_contractor_subscription", return_value=sub, create=True), \
             patch.object(main.database, "first_offer_status", fo, create=True), \
             patch.object(main.payments, "FIRST_INTRO_PRICE_PENCE", 499, create=True):
            return main._first_offer_promo_html(r, hero=True)

    def test_pricing_page_shows_all_five_cards_and_offer_above_them(self):
        import importlib.util
        spec = importlib.util.spec_from_file_location("realpay_cards", os.path.join(_APP_DIR, "payments.py"))
        rp = importlib.util.module_from_spec(spec); spec.loader.exec_module(rp)
        r = MagicMock(); r.query_params = {}; r.cookies = {}
        with patch("main.HTMLResponse", side_effect=lambda content=None, *a, **k: content), \
             patch.object(main.payments, "PLANS", rp.PLANS, create=True), \
             patch.object(main.payments, "plan_description", rp.plan_description, create=True), \
             patch.object(main.payments, "FIRST_INTRO_PRICE_PENCE", 499, create=True):
            page = main.pricing(r)
        self.assertEqual(page.count('class="tk-pkg"'), 5)
        for key, (price, intros, pct) in self.AGREED.items():
            self.assertIn(f"&pound;{price}<span>/month</span>", page)
            self.assertIn(f"<b>{intros}</b> introductions included each month", page)
            self.assertIn(f"{pct}% off additional marketplace introductions", page)
            self.assertIn(f'href="/checkout/{key}"', page)
        self.assertLess(page.index("Find my first introduction"), page.index('class="tk-pkgs"'))
        self.assertNotIn("Most Popular", page)
        self.assertIn("Single Lead Purchase (Entry)", page)  # ordinary single-purchase prices still listed

    def test_homepage_source_has_featured_packages_compare_link_and_no_stale_copy(self):
        with open(os.path.join(_APP_DIR, "main.py"), encoding="utf-8") as fh:
            src = fh.read()
        a = src.index("def public_homepage"); b = src.index("\n@app.", a)
        seg = src[a:b]
        self.assertIn("Featured packages", seg)
        self.assertIn("Compare all five packages", seg)
        self.assertIn('["starter", "commercial_forestry", "treekey_elite"]', seg)
        self.assertIn("{_homepage_offer_promo}", seg)
        # buttons use the same plan-specific destination as the pricing page
        self.assertIn('cta_href=f"/checkout/{_tier_key}"', seg)
        self.assertNotIn('cta_href="#map"', seg)
        self.assertIn("Choose how you get introductions", seg)
        self.assertIn("Pick a monthly package, or buy individual introductions without a subscription.", seg)
        self.assertNotIn("Zero Commitment. Cancel Anytime.", seg)
        self.assertNotIn("Dominate Your Area", seg)
        for gone in ("Early-access alert the moment a matching lead is filed", "Secure Priority Access", "See This Tier"):
            self.assertNotIn(gone, seg)


class TestWelcomePage(unittest.TestCase):

    def _get(self, *, cookie=True, has_settings=True, sub=None, offer=None, stock=3, approved=False):
        req = _mock_request(cookie_value=_cookie() if cookie else None, path="/welcome")
        with patch.object(main.database, "get_db_conn", MagicMock()), \
             patch.object(main.letter_content, "get_contractor_settings", return_value=object() if has_settings else None), \
             patch.object(main.database, "get_contractor_subscription", return_value=sub), \
             patch.object(main.database, "first_offer_status", MagicMock(return_value=offer or {"eligible": True, "reason": None}), create=True), \
             patch.object(main, "_first_offer_stock_count", return_value=stock), \
             patch.object(main, "_letter_setup_complete", return_value=approved), \
             patch("main.HTMLResponse", side_effect=lambda content=None, *a, **k: content), \
             patch.multiple(main.payments, create=True, **OFFER_CONSTS):
            r = main.welcome_page(req)
        return r

    def _body(self, r):
        return r if isinstance(r, str) else str(getattr(r, "url", r))

    def test_needs_sign_in(self):
        r = self._get(cookie=False)
        self.assertEqual(r.url, "/login?next=%2Fwelcome")

    def test_account_without_details_goes_to_signup(self):
        self.assertEqual(self._get(has_settings=False).url, "/free-account?next=%2Fwelcome")

    def test_subscriber_goes_to_dashboard(self):
        self.assertEqual(self._get(sub={"active": True}).url, "/dashboard")

    def test_eligible_customer_sees_the_agreed_offer_without_a_subscription(self):
        body = self._body(self._get())
        self.assertIn("Welcome to TreeKey", body)
        self.assertIn("Try your first introduction for £4.99", body)
        self.assertIn("Choose a suitable opportunity and we&#x27;ll print and post your approved letter. Printing and postage included. No subscription required."
                      if "&#x27;" in body else
                      "Choose a suitable opportunity and we'll print and post your approved letter. Printing and postage included. No subscription required.", body)
        self.assertIn("Find my first introduction", body)
        self.assertNotIn("no_subscription", body)

    def test_no_suitable_stock_is_reported_honestly_and_offers_no_buy_button(self):
        body = self._body(self._get(stock=0))
        self.assertIn("no suitable standard opportunities on sale right now", body)
        self.assertIn("you have not been charged", body)
        self.assertNotIn("Find my first introduction", body)

    def test_ineligible_customer_is_not_shown_the_499_offer(self):
        for reason in ("offer_used", "prior_purchase", "offer_in_progress"):
            body = self._body(self._get(offer={"eligible": False, "reason": reason}))
            self.assertNotIn("£4.99", body, reason)
            self.assertNotIn("Find my first introduction", body, reason)
            self.assertIn("Browse the marketplace", body, reason)

    def test_approval_before_payment_is_stated(self):
        self.assertIn("preview your letter and approve it", self._body(self._get(approved=False)))


class TestCheckoutRequestsTheOfferOnly(unittest.TestCase):

    def _checkout(self, query, side_effect=None, return_value="https://stripe.example/s"):
        req = _mock_request(cookie_value=_cookie(), query_params=query)
        with patch.object(main.payments, "PLANS", _PAYMENT_PLAN), \
             patch.object(main.payments, "FirstOfferUnavailable", _Refusal, create=True), \
             patch("main._letter_setup_complete", return_value=True), \
             patch("main.HTMLResponse", side_effect=lambda content=None, *a, **k: SimpleNamespace(body=content, status_code=k.get("status_code", 200))), \
             patch.object(main.payments, "create_checkout_session", side_effect=side_effect, return_value=return_value) as create:
            result = main.checkout("single_lead_small", req)
        return result, create

    def test_offer_is_requested_only_with_the_flag_and_a_lead(self):
        _, c = self._checkout({"lead_id": "L1", "first_offer": "1"})
        self.assertIs(c.call_args.kwargs["first_offer"], True)
        _, c = self._checkout({"lead_id": "L1"})
        self.assertIs(c.call_args.kwargs["first_offer"], False)
        _, c = self._checkout({"first_offer": "1"})
        self.assertIs(c.call_args.kwargs["first_offer"], False)

    def test_refused_offer_says_nothing_was_charged_and_never_redirects_to_stripe(self):
        result, _ = self._checkout({"lead_id": "L1", "first_offer": "1"}, side_effect=_Refusal("offer_used"))
        self.assertEqual(result.status_code, 409)
        self.assertIn("Nothing has been charged", result.body)
        self.assertIn("already been used", result.body)
        self.assertNotIn("stripe.example", result.body)


def _load_real_payments(fake_db):
    created = []

    class _Sess:
        @staticmethod
        def create(**kw):
            created.append(kw)
            return SimpleNamespace(id="cs_test_1", url="https://checkout.test/session")

    stripe = types.ModuleType("stripe")
    stripe.api_key = "sk_test_dummy"
    stripe.error = SimpleNamespace(AuthenticationError=type("A", (Exception,), {}),
                                   StripeError=type("S", (Exception,), {}),
                                   SignatureVerificationError=type("V", (Exception,), {}))
    stripe.checkout = SimpleNamespace(Session=_Sess)
    with patch.dict(sys.modules, {"stripe": stripe, "database": fake_db}):
        spec = importlib.util.spec_from_file_location("payments_under_test", os.path.join(_APP_DIR, "payments.py"))
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
    mod.stripe.api_key = "sk_test_dummy"
    return mod, created, stripe


class TestRealCheckoutSession(unittest.TestCase):
    """The real payments.create_checkout_session against a fake Stripe and a
    fake database: asserts what would be charged, and what is never touched."""

    def _run(self, *, live_plan="single_lead_small", live_amount=1900, offer_state=None, claim=None,
             first_offer=True, email="dave@apex-trees.co.uk", live_none=False, value_tier="standard"):
        db = MagicMock()
        db.STRIPE_CHECKOUT_EXPIRY_MINUTES = 30
        db.first_offer_status.return_value = offer_state or {"eligible": True, "reason": None}
        db.claim_first_offer_order.return_value = claim or {"ok": True}
        db.reserve_lead_for_checkout.return_value = True
        db.get_subscriber_discount.return_value = {"eligible": False, "discount_pct": 0}
        pay, created, stripe = _load_real_payments(db)
        live = None if live_none else {"amount_pence": live_amount, "plan_key": live_plan,
                                       "name": "Single Lead Purchase", "description": "d", "value_tier": value_tier}
        outcome = {}
        with patch.dict(sys.modules, {"database": db, "stripe": stripe}), \
             patch.object(pay, "_resolve_live_single_lead_price", return_value=live):
            try:
                outcome["url"] = pay.create_checkout_session("single_lead_small", "GB", "LEAD-1",
                                                             account_email=email, first_offer=first_offer)
            except pay.FirstOfferUnavailable as e:
                outcome["refused"] = e.reason
        return db, created, outcome

    def test_eligible_standard_opportunity_is_charged_499_decided_by_the_server(self):
        for plan, amount in (("single_lead_small", 1900), ("single_lead_medium", 2900)):
            db, created, out = self._run(live_plan=plan, live_amount=amount)
            self.assertEqual(out["url"], "https://checkout.test/session")
            params = created[0]
            self.assertEqual(params["line_items"][0]["price_data"]["unit_amount"], 499)
            self.assertFalse(params["allow_promotion_codes"])
            self.assertEqual(params["metadata"]["first_offer"], "1")
            db.claim_first_offer_order.assert_called_once()
            self.assertEqual(db.claim_first_offer_order.call_args.args[2], 499)
            db.record_order.assert_not_called()  # the redemption record was written atomically by the claim

    def test_priority_and_elite_opportunities_are_never_offered_at_499(self):
        for plan, amount in (("single_lead_priority", 3900), ("single_lead_large", 4900)):
            db, created, out = self._run(live_plan=plan, live_amount=amount)
            self.assertEqual(out["refused"], "not_standard")
            self.assertEqual(created, [])
            db.reserve_lead_for_checkout.assert_not_called()

    def test_priority_or_elite_listing_priced_at_29_is_still_excluded(self):
        # price alone must not decide: same GBP 29 price point, non-Standard classification
        for tier in ("priority", "elite", None):
            db, created, out = self._run(live_plan="single_lead_medium", live_amount=2900, value_tier=tier)
            self.assertEqual(out["refused"], "not_standard", tier)
            self.assertEqual(created, [])
            db.reserve_lead_for_checkout.assert_not_called()

    def test_ineligible_account_is_refused_before_any_lead_is_reserved_or_stripe_touched(self):
        for reason in ("offer_used", "prior_purchase", "subscriber", "offer_in_progress", "no_details"):
            db, created, out = self._run(offer_state={"eligible": False, "reason": reason})
            self.assertEqual(out["refused"], reason)
            self.assertEqual(created, [])
            db.reserve_lead_for_checkout.assert_not_called()
            db.claim_first_offer_order.assert_not_called()

    def test_lost_redemption_race_releases_the_lead_and_charges_nothing(self):
        db, created, out = self._run(claim={"ok": False, "reason": "offer_used"})
        self.assertEqual(out["refused"], "offer_used")
        self.assertEqual(created, [])
        db.release_lead_reservation.assert_called_once()

    def test_offer_needs_a_signed_in_account(self):
        db, created, out = self._run(email=None)
        self.assertEqual(out["refused"], "not_signed_in")
        self.assertEqual(created, [])

    def test_no_stock_or_expired_lead_creates_no_charge_and_no_redemption(self):
        db, created, out = self._run(live_none=True)
        self.assertIsNone(out["url"])
        self.assertEqual(created, [])
        db.claim_first_offer_order.assert_not_called()

    def test_ordinary_purchase_keeps_its_normal_price_and_records_a_normal_order(self):
        db, created, out = self._run(first_offer=False)
        self.assertEqual(created[0]["line_items"][0]["price_data"]["unit_amount"], 1900)
        self.assertTrue(created[0]["allow_promotion_codes"])
        self.assertNotIn("first_offer", created[0]["metadata"])
        db.claim_first_offer_order.assert_not_called()
        db.first_offer_status.assert_not_called()
        db.record_order.assert_called_once()


class TestMarketplaceOfferPrice(unittest.TestCase):

    def test_helper_fails_closed(self):
        with patch.object(main.database, "first_offer_status", MagicMock(side_effect=RuntimeError("db down")), create=True):
            self.assertFalse(main._viewer_first_offer_eligible("dave@apex-trees.co.uk"))
        self.assertFalse(main._viewer_first_offer_eligible(None))
        with patch.object(main.database, "first_offer_status", MagicMock(return_value={"eligible": False, "reason": "offer_used"}), create=True):
            self.assertFalse(main._viewer_first_offer_eligible("dave@apex-trees.co.uk"))
        with patch.object(main.database, "first_offer_status", MagicMock(return_value={"eligible": True, "reason": None}), create=True):
            self.assertTrue(main._viewer_first_offer_eligible("dave@apex-trees.co.uk"))

    def test_listing_rule_uses_the_value_classification_not_the_price(self):
        with patch.multiple(main.payments, create=True, **OFFER_CONSTS):
            ok = {"value_tier": "standard", "plan_key": "single_lead_medium", "price": 29}
            self.assertTrue(main._listing_is_first_offer_standard(ok))
            self.assertFalse(main._listing_is_first_offer_standard(dict(ok, value_tier="priority")))
            self.assertFalse(main._listing_is_first_offer_standard(dict(ok, value_tier="elite")))
            self.assertFalse(main._listing_is_first_offer_standard(dict(ok, value_tier=None)))
            self.assertFalse(main._listing_is_first_offer_standard({"plan_key": "single_lead_medium", "price": 29}))
            self.assertFalse(main._listing_is_first_offer_standard(dict(ok, plan_key="single_lead_priority", price=39)))

    def test_price_block_shows_499_with_the_normal_price_struck_through(self):
        with patch.multiple(main.payments, create=True, **OFFER_CONSTS):
            block = main._first_offer_price_block(19)
        self.assertIn("£4.99", block)
        self.assertIn("line-through", block)
        self.assertIn("£19", block)


class TestPhoneValidation(unittest.TestCase):

    def test_accepts_uk_and_international_formats(self):
        for n in ("01234 567890", "+44 1234 567890", "0044 1234 567890", "07700 900123", "(01234) 567-890",
                  "+1 (415) 555-2671", "+353 87 123 4567", "+61 2 9374 4000", "01234.567.890"):
            self.assertTrue(letter_content.validate_phone(n)[0], n)

    def test_rejects_clearly_invalid_input_with_a_helpful_message(self):
        for n in ("", "abc", "07XXX XXXXXX", "12345", "+44++1234567890", "0000000000", "01234 567890 ext 5", "1" * 16):
            ok, msg = letter_content.validate_phone(n)
            self.assertFalse(ok, n)
            self.assertTrue(msg)

    def test_same_number_in_different_formats_has_the_same_identity(self):
        keys = {letter_content.phone_identity_key(n) for n in ("01234 567890", "+44 1234 567890", "0044 (1234) 567-890")}
        self.assertEqual(len(keys), 1)

    def test_settings_validation_enforces_it_server_side(self):
        bad = letter_content.ContractorLetterSettings(contractor_email="a@b.co", business_name="X Ltd", phone="hello").validate()
        self.assertTrue(any(p.startswith("phone:") for p in bad), bad)
        good = letter_content.ContractorLetterSettings(contractor_email="a@b.co", business_name="X Ltd", phone="+44 1234 567890").validate()
        self.assertEqual(good, [])

    def test_signup_form_shows_an_inline_error_and_keeps_every_entered_value(self):
        values = {"email": "dave@apex-trees.co.uk", "responsible_name": "Dave Smith", "business_name": "Apex Trees",
                  "phone": "not a phone", "service_area_note": "leeds and york"}
        err = main._signup_field_errors(values)
        self.assertIn("phone", err)
        page = main._first_time_signup_page_html(None, values=values, error="Please check the telephone number below.",
                                                 field_errors=err)
        self.assertIn('id="phone-error"', page)
        self.assertIn("lp-fielderr lp-on", page)
        self.assertIn(letter_content.PHONE_FORMAT_HINT.split(",")[0], page)
        for kept in ("not a phone", "Dave Smith", "Apex Trees", "leeds and york"):
            self.assertIn(kept, page)
        self.assertIn('aria-invalid="true"', page)

    def test_valid_international_number_passes_the_signup_validation(self):
        values = {"email": "dave@apex-trees.co.uk", "responsible_name": "Dave Smith", "business_name": "Apex Trees",
                  "phone": "+353 87 123 4567", **{k: "" for k in main._SIGNUP_FIELDS if k not in
                                                  ("email", "responsible_name", "business_name", "phone")}}
        err, fields = main._validate_signup_values(values, True)
        self.assertIsNone(err)

    def test_invalid_phone_is_rejected_by_the_signup_validation_and_by_javascript(self):
        values = {"email": "dave@apex-trees.co.uk", "responsible_name": "Dave", "business_name": "Apex", "phone": "abc",
                  **{k: "" for k in main._SIGNUP_FIELDS if k not in ("email", "responsible_name", "business_name", "phone")}}
        err, fields = main._validate_signup_values(values, True)
        self.assertIsNotNone(err)
        self.assertIsNone(fields)
        self.assertIn("phoneOk", main._SIGNUP_FORM_JS)
        self.assertIn("data-phone", main._first_time_signup_page_html(None))


class TestPlaceCapitalisationSuggestion(unittest.TestCase):

    def test_suggests_for_lower_case_place_names(self):
        s = letter_content.suggest_place_capitalisation
        self.assertEqual(s("nottingham and newark"), "Nottingham and Newark")
        self.assertEqual(s("covering leeds and the surrounding 15 miles"), "covering Leeds and the surrounding 15 miles")
        self.assertEqual(s("stoke-on-trent"), "Stoke-on-Trent")
        self.assertEqual(s("king's lynn"), "King's Lynn")

    def test_leaves_acronyms_postcodes_and_deliberate_capitalisation_alone(self):
        s = letter_content.suggest_place_capitalisation
        for value in ("NG22 and Leeds", "Leeds, York", "McDonald Green", "AONB", "", "  "):
            self.assertIsNone(s(value), value)

    def test_only_the_service_area_field_offers_it_and_it_never_edits_by_itself(self):
        page = main._first_time_signup_page_html(None)
        self.assertEqual(page.count('id="service_area_suggest"'), 1)
        self.assertIn("Use this", main._PLACE_SUGGEST_JS)
        self.assertIn("Keep mine", main._PLACE_SUGGEST_JS)
        self.assertNotIn("business_name'", main._PLACE_SUGGEST_JS)


if __name__ == "__main__":
    unittest.main(verbosity=2)

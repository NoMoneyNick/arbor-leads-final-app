"""test_home_v2.py -- the new homepage design (home_v2.py), 20 Oct 2026.

Pure rendering plus the switch. No database, no network.
Run with:  python -m unittest tests.test_home_v2 -v
"""
import os
import sys
import unittest
from unittest.mock import MagicMock, patch

_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
_APP_DIR = os.path.dirname(_THIS_DIR)
sys.path.insert(0, _APP_DIR)
sys.path.insert(0, _THIS_DIR)

import home_v2  # noqa: E402

FEED = [{"title": "Crown reduction", "area": "Wandsworth, London", "size": "Medium job"},
        {"title": "Tree removal", "area": "Sutton Coldfield", "size": "Large job"}]


def _page(**over):
    data = {"feed": FEED, "counts": {"today": 16, "week": 230, "month": 2974}, "total": 9802,
            "offer": {"href": "/login?next=%2Fwelcome", "price": "£4.99"}, "auth": None, "asset_version": "1"}
    data.update(over)
    return home_v2.render(data)


class TestRender(unittest.TestCase):
    def test_real_data_appears(self):
        page = _page()
        for text in ("Crown reduction", "Wandsworth, London", "Medium job", "Tree removal", "Large job",
                     'id="countToday">16<', 'id="countWeek">230<', 'id="countMonth">2,974<', "9,802"):
            self.assertIn(text, page, text)

    def test_values_are_escaped(self):
        page = _page(feed=[{"title": "<script>alert(1)</script>", "area": '"><img src=x>', "size": "Small job"}])
        self.assertNotIn("<script>alert(1)</script>", page)
        self.assertNotIn('"><img src=x>', page)

    def test_prices_and_tier_descriptions(self):
        page = _page()
        for price in ("£19", "£29", "£39", "£49"):
            self.assertIn(price, page)
        self.assertNotIn("to be confirmed", page)
        self.assertNotIn("Preview only", page)
        self.assertNotIn("ILLUSTRATIVE", page)
        self.assertNotIn("data-open", page)

    def test_offer_only_when_eligible(self):
        self.assertIn("NEW CUSTOMER OFFER", _page())
        self.assertIn('href="/login?next=%2Fwelcome"', _page())
        self.assertNotIn("NEW CUSTOMER OFFER", _page(offer=None))

    def test_no_subscription_or_radar_wording(self):
        page = _page()
        for gone in ("Pick a monthly package", "rolling monthly", "Upgrade For Full Commercial Intel", "Intercept", "intercept", "Radar Locked"):
            self.assertNotIn(gone, page, gone)

    def test_account_links_follow_login_state(self):
        self.assertIn('href="/free-account"', _page())
        self.assertIn('href="/login"', _page())
        signed_in = _page(auth={"dashboard_url": "/my-leads"})
        self.assertIn('href="/my-leads"', signed_in)
        self.assertIn('href="/logout"', signed_in)

    def test_real_routes_and_provenance_kept(self):
        page = _page()
        for href in ("/marketplace", "/pricing", "/faq", "/storm-radar", "/chip-drop", "/free-account", "/suggestions",
                     "/privacy-policy", "/terms-of-service", "mailto:support@treekey.co.uk"):
            self.assertIn(href, page, href)
        self.assertIn("Open Government Licence", page)
        self.assertIn("Planning information comes from public UK council planning registers", page)

    def test_install_and_map_controls_present(self):
        page = _page()
        for needle in ('id="pwaInstallBtn"', 'id="pwaInstallTip"', 'id="postcodeInput"', 'id="radiusSelect"',
                       'id="map"', "/api/check-postcode", "/api/live-counts"):
            self.assertIn(needle, page, needle)

    def test_empty_feed_still_renders(self):
        self.assertIn("New notices are being processed.", _page(feed=[]))


try:
    import test_access_control  # noqa: F401
    import main
    _ERR = None
except Exception as _e:  # pragma: no cover
    main, _ERR = None, _e


@unittest.skipIf(main is None, f"main.py cannot be imported here: {_ERR!r}")
class TestSwitch(unittest.TestCase):
    def _req(self, query=None):
        req = MagicMock(); req.cookies = {}; req.headers = {}; req.query_params = query or {}
        return req

    def test_default_on_when_subscriptions_closed(self):
        with patch.dict(os.environ, {"TREEKEY_HOME_V2": ""}), patch.object(main, "_subs_open", return_value=False):
            self.assertTrue(main._home_v2_enabled(self._req()))

    def test_kill_switch_and_old_param_and_subscriptions(self):
        with patch.object(main, "_subs_open", return_value=False):
            with patch.dict(os.environ, {"TREEKEY_HOME_V2": "off"}):
                self.assertFalse(main._home_v2_enabled(self._req()))
            with patch.dict(os.environ, {"TREEKEY_HOME_V2": ""}):
                self.assertFalse(main._home_v2_enabled(self._req({"design": "old"})))
        with patch.dict(os.environ, {"TREEKEY_HOME_V2": ""}), patch.object(main, "_subs_open", return_value=True):
            self.assertFalse(main._home_v2_enabled(self._req()))


if __name__ == "__main__":
    unittest.main()


class TestAgreedCopy(unittest.TestCase):
    def _page(self):
        import home_v2
        return home_v2.render({"feed": [], "counts": {}, "total": 0,
                               "offer": {"price": "£4.99", "href": "/x"}, "auth": None})

    def test_agreed_wording_present(self):
        page = self._page()
        for text in ("LOCAL TREE WORK. ON YOUR PATCH.", "from crown reductions to felling and removals",
                     "Buy one introduction at a time. No subscription. Printing and postage included.",
                     "Sold to one tree firm through TreeKey, then taken off sale.",
                     "Try your first introduction for", "Not available to subscribers.",
                     "Printed introductions and practical tools for tree contractors.",
                     "published under the Open Government Licence"):
            self.assertIn(text, page)

    def test_old_claims_gone(self):
        page = self._page()
        for text in ("No shared leads", "for you alone", "A CLEARER VIEW", "write your own", "Closer to home",
                     "An exclusive introduction", "Explore available leads", "Latest opportunities"):
            self.assertNotIn(text, page)

    def test_document_wording_present(self):
        page = self._page()
        for text in ("Browse local opportunities", "Find work that fits", "You choose the work.",
                     "Keep an eye on the weather", "Find a woodchip or timber tip site",
                     "Proposed work near you", "See prices and what’s included"):
            self.assertIn(text, page)

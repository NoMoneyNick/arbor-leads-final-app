"""Oct 22 2026: tests built from the REAL shape of a live Idox portal
(captured from Brighton & Hove / Leeds in a real browser), not from the
synthetic 'REF | description' fixture the older tests use."""
import unittest
from unittest import mock

import mesh_scrapers
from mesh_scrapers import IdoxScraper

BASE = "https://planningapps.example.gov.uk/online-applications"

FORM_PAGE = """
<html><body><form action="/online-applications/advancedSearchResults.do?action=firstPage" method="post">
<input type="hidden" name="_csrf" value="tok123">
<input type="text" name="searchCriteria.description" value="">
<select name="searchCriteria.caseType"><option value="" selected>Any</option><option value="TPO">TPO</option></select>
<input type="hidden" name="caseAddressType" value="Application">
<input type="text" name="date(applicationValidatedStart)" value="">
<input type="text" name="date(applicationValidatedEnd)" value="">
<input type="hidden" name="searchType" value="Application">
<input type="hidden" name="newHiddenIdoxField" value="abc">
<input type="submit" value="Search">
</form></body></html>
"""

def _li(key, desc, ref, received="Fri 16 Oct 2026"):
    return (f'<li class="searchresult"><a href="/online-applications/applicationDetails.do?keyVal={key}&amp;activeTab=summary"> {desc} </a>'
            f'<p class="address"> 1 Test Road Town AB1 2CD </p>'
            f'<p class="metaInfo"> Ref. No: {ref} <span class="divider">|</span> Received: {received} '
            f'<span class="divider">|</span> Status: Registered </p></li>')

def _results(items, next_href=None, per_page_form=True):
    nxt = f'<a href="{next_href}">Next</a>' if next_href else ""
    form = ('<form action="/online-applications/pagedSearchResults.do" method="post">'
            '<input type="hidden" name="_csrf" value="tok123"><input type="hidden" name="searchCriteria.page" value="1">'
            '<input type="hidden" name="action" value="page"><input type="hidden" name="orderBy" value="DateReceived">'
            '<input type="hidden" name="searchCriteria.resultsPerPage" value="10"></form>') if per_page_form else ""
    return f'<html><body><p>Showing 1-{len(items)}</p><ul id="searchresults">{"".join(items)}</ul>{nxt}{form}</body></html>'


class Resp:
    def __init__(self, text, url=BASE + "/advancedSearchResults.do?action=firstPage", status=200):
        self.text, self.url, self.status_code = text, url, status


class TestIdoxLiveShape(unittest.TestCase):
    def test_reference_comes_from_metainfo_and_is_stable(self):
        from bs4 import BeautifulSoup
        li = BeautifulSoup(_li("K1", "Fell oak tree", "BH2026/02351"), "html.parser").li
        ref, desc, received = IdoxScraper._extract_result_fields(li, li.a)
        self.assertEqual(ref, "BH2026/02351")
        self.assertEqual(desc, "Fell oak tree")
        self.assertEqual(received, "2026-10-16")

    def test_fallback_reference_is_not_time_based(self):
        from bs4 import BeautifulSoup
        li = BeautifulSoup('<li class="searchresult"><a href="/x.do?keyVal=ABC123">Fell oak</a></li>', "html.parser").li
        r1 = IdoxScraper._extract_result_fields(li, li.a)[0]
        r2 = IdoxScraper._extract_result_fields(li, li.a)[0]
        self.assertEqual(r1, "MESH-ABC123")
        self.assertEqual(r1, r2)

    def test_payload_is_built_from_the_councils_own_form(self):
        import datetime
        sc = IdoxScraper(BASE)
        url, payload = sc._build_search_request(FORM_PAGE, BASE + "/search.do?action=advanced", "tree",
                                                datetime.datetime(2026, 10, 1), datetime.datetime(2026, 10, 22), "tok123")
        self.assertTrue(url.endswith("advancedSearchResults.do?action=firstPage"))
        self.assertEqual(payload["searchCriteria.description"], "tree")
        self.assertEqual(payload["newHiddenIdoxField"], "abc")          # unknown new field is carried through
        self.assertEqual(payload["date(applicationValidatedStart)"], "01/10/2026")  # form has no 'received' pair
        self.assertNotIn("date(applicationReceivedStart)", payload)
        self.assertEqual(payload["_csrf"], "tok123")

    def test_payload_falls_back_when_no_form(self):
        import datetime
        sc = IdoxScraper(BASE)
        url, payload = sc._build_search_request("<html></html>", BASE + "/search.do?action=advanced", "tree",
                                                datetime.datetime(2026, 10, 1), datetime.datetime(2026, 10, 22), "t")
        self.assertIn("date(applicationReceivedStart)", payload)

    def test_all_pages_are_read(self):
        page1 = _results([_li(f"A{i}", f"Crown reduction tree {i}", f"R/{i}") for i in range(10)],
                         next_href="/online-applications/pagedSearchResults.do?action=page&searchCriteria.page=2")
        big = _results([_li(f"A{i}", f"Crown reduction tree {i}", f"R/{i}") for i in range(10)],
                       next_href="/online-applications/pagedSearchResults.do?action=page&searchCriteria.page=2")
        page2 = _results([_li(f"B{i}", f"Crown reduction tree {i}", f"S/{i}") for i in range(3)])

        def fake_get(url, session=None, **kw):
            if "searchCriteria.page=2" in url:
                return Resp(page2, url=url)
            return Resp(FORM_PAGE, url=url)

        def fake_post(url, session=None, data=None, **kw):
            if "pagedSearchResults" in url:
                return Resp(big, url=url)
            return Resp(page1, url=url)

        with mock.patch.object(mesh_scrapers.net_utils, "smart_get", side_effect=fake_get), \
             mock.patch.object(mesh_scrapers.net_utils, "smart_post", side_effect=fake_post), \
             mock.patch.object(IdoxScraper, "_fetch_applicant_and_agent", return_value={}), \
             mock.patch.object(mesh_scrapers.time, "sleep"):
            leads = IdoxScraper(BASE).search_tree_applications(days_back=7, search_term="tree")
        refs = [l["reference"] for l in leads]
        self.assertEqual(len(refs), 13)
        self.assertEqual(len(set(refs)), 13)
        self.assertIn("R/0", refs)
        self.assertIn("S/2", refs)
        self.assertEqual(leads[0]["registered_date"], "2026-10-16")

    def test_page_classification(self):
        c = IdoxScraper.classify_results_page
        self.assertEqual(c("Access Denied - please verify you are human"), "blocked")
        self.assertEqual(c("A server problem prevented your request. No results"), "error")
        self.assertEqual(c("Too many results, please narrow your search"), "too_many")
        self.assertEqual(c("Your search did not match any applications"), "no_results")
        self.assertEqual(c("Welcome to our portal"), "unknown")

    def test_blocked_page_alerts_with_a_specific_title(self):
        sc = IdoxScraper(BASE)
        blocked = "<html><body>Access Denied. Please verify you are human.</body></html>"
        with mock.patch.object(mesh_scrapers.net_utils, "smart_get", return_value=Resp(FORM_PAGE)), \
             mock.patch.object(mesh_scrapers.net_utils, "smart_post", return_value=Resp(blocked)), \
             mock.patch.object(mesh_scrapers.time, "sleep"), \
             mock.patch.dict("sys.modules", {"notifications": mock.MagicMock()}) as _mods:
            alert = _mods["notifications"].send_system_incident_alert
            IdoxScraper._structure_alert_throttle.clear()
            out = sc.search_tree_applications(days_back=7, search_term="tree")
        self.assertEqual(out, [])
        titles = [c.kwargs.get("title") for c in alert.call_args_list]
        self.assertTrue(any("blocking the scraper" in (t or "") for t in titles), titles)


if __name__ == "__main__":
    unittest.main()

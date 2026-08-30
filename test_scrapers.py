"""
test_scrapers.py -- Unit tests for the core scraping/parsing logic in
net_utils.py, scanners.py, and mesh_scrapers.py.

These are pure logic tests: no real network calls, no live database, no
API keys required, nothing hits a real council portal. Run with:

    python -m unittest test_scrapers.py -v

Uses only Python's standard library (unittest + unittest.mock) -- no new
package was added to requirements.txt to support this.

scanners.py and research.py import `database` (which needs psycopg2, a
real Postgres driver) at module load time. Rather than requiring a live
database connection just to test parsing/scoring logic that has nothing
to do with the database, this file stubs `database`, `notifications`, and
`dotenv` in sys.modules before importing anything else -- the same
technique you'd use to fake any dependency you don't want a unit test to
actually need. Every network call is mocked with unittest.mock; nothing
here ever makes a real HTTP request.

Added Aug 29 2026 as part of the scraper-hardening pass documented in
PROJECT_STATE.md. Writing test_extracts_only_tree_related_leads_from_listing
and the false-positive test below is what caught a real bug before it
shipped further: TREE_GOLD used to contain a bare "fell " entry that
matched almost any ordinary sentence using "fell" as a verb ("a branch
fell in the storm", "the applicant fell ill") -- see scanners.py's comment
at that entry for the fix.
"""
import sys
import types
import unittest
from unittest.mock import patch, MagicMock

import requests as _requests  # noqa: E402  (used for constructing real exception instances below)

# ---------------------------------------------------------------------------
# Stub heavy/external modules BEFORE importing the project modules under
# test, so this file runs anywhere -- no Postgres, no .env file, no network.
# If the real modules happen to already be importable (e.g. this ever runs
# somewhere with psycopg2 installed), we leave them alone.
# ---------------------------------------------------------------------------
if "database" not in sys.modules:
    _fake_database = types.ModuleType("database")
    _fake_database.get_db_conn = MagicMock()
    sys.modules["database"] = _fake_database

if "notifications" not in sys.modules:
    _fake_notifications = types.ModuleType("notifications")
    _fake_notifications.send_system_incident_alert = MagicMock()
    _fake_notifications.send_resend_email = MagicMock()
    sys.modules["notifications"] = _fake_notifications

if "dotenv" not in sys.modules:
    _fake_dotenv = types.ModuleType("dotenv")
    _fake_dotenv.load_dotenv = lambda *a, **k: None
    sys.modules["dotenv"] = _fake_dotenv

import net_utils   # noqa: E402
import scanners    # noqa: E402
import mesh_scrapers  # noqa: E402


def _connection_error():
    return _requests.exceptions.ConnectionError("simulated connection drop")


def _ssl_error():
    return _requests.exceptions.SSLError("simulated certificate failure")


class TestTreeGoldFiltering(unittest.TestCase):
    """The compound-phrase relevance filter deciding whether a planning
    application description is tree-work at all. Highest-value thing to
    test in this whole file: a false positive here creates a junk lead a
    contractor pays for; a false negative silently loses a real one."""

    def test_real_tree_work_matches(self):
        real = [
            "Notice: felling of 3no. oak trees within a Conservation Area.",
            "Crown reduction of mature oak tree, TPO protected.",
            "Fell 1: Ash (diseased) - fell to ground level.",
            "Application for hedge trimming works to boundary hedge.",
            "TPO application: pollarding of lime trees on highway verge.",
        ]
        for text in real:
            with self.subTest(text=text):
                self.assertTrue(scanners._is_tree_related(text))
                self.assertTrue(mesh_scrapers.is_tree_related(text))

    def test_known_false_positives_are_excluded(self):
        """Mirrors the exact false-positive patterns PROJECT_STATE.md
        documents TREE_GOLD as having been built to avoid: street names,
        bank branches, and ordinary (non-arboricultural) uses of "fell"."""
        junk = [
            "Change of use for former bank branch on Crown Street.",
            "A large branch fell during the storm, blocking the road.",
            "Applicant fell ill and withdrew the application.",
            "The company fell behind on its Companies House filings.",
            "Erection of a two-storey rear extension.",
        ]
        for text in junk:
            with self.subTest(text=text):
                self.assertFalse(scanners._is_tree_related(text))
                self.assertFalse(mesh_scrapers.is_tree_related(text))


class TestLeadScoring(unittest.TestCase):
    def test_score_tiers(self):
        self.assertEqual(
            scanners.score_lead("TPO application, conservation area, woodland clearance"),
            ("large", 75),
        )
        self.assertEqual(
            scanners.score_lead("Crown reduction of mature oak tree"),
            ("medium", 50),
        )
        self.assertEqual(
            scanners.score_lead("Routine hedge trimming works"),
            ("small", 25),
        )


class TestIdoxCsrfExtraction(unittest.TestCase):
    def setUp(self):
        self.scraper = mesh_scrapers.IdoxScraper("https://example-council.gov.uk/online-applications")

    def test_extracts_token_when_present(self):
        html = '<form><input type="hidden" name="_csrf" value="abc123"/></form>'
        self.assertEqual(self.scraper.get_csrf_token(html), "abc123")

    def test_returns_empty_string_when_absent(self):
        html = "<form><input type='text' name='other'/></form>"
        self.assertEqual(self.scraper.get_csrf_token(html), "")


class TestIdoxResultsParsing(unittest.TestCase):
    """Feeds realistic Idox results-page HTML through search_tree_applications
    with net_utils.smart_get/smart_post mocked out -- no real network call --
    to lock the parsing logic in against regressions."""

    LISTING_HTML = """
    <html><body>
    <ul id="searchresults">
        <li class="searchresult">
            <a href="/online-applications/applicationDetails.do?id=1">23/01234/TPO | Crown reduction of mature oak tree, rear garden</a>
            <p class="address">12 Oak Lane, Birmingham, B1 1AA</p>
        </li>
        <li class="searchresult">
            <a href="/online-applications/applicationDetails.do?id=2">23/05678/FUL | Erection of a two-storey rear extension</a>
            <p class="address">4 High Street, Birmingham, B2 2BB</p>
        </li>
        <li class="searchresult">
            <a href="/online-applications/applicationDetails.do?id=3">23/09999/TPO | Felling of 2no. diseased ash trees, conservation area</a>
            <p class="address">7 The Green, Birmingham, B3 3CC</p>
        </li>
    </ul>
    </body></html>
    """

    NO_RESULTS_HTML = "<html><body><p>Your search returned no results. Please try again.</p></body></html>"

    SINGLE_REDIRECT_HTML = """
    <html><body>
    <table>
        <tr><th>Reference</th><td>23/04321/TPO</td></tr>
        <tr><th>Address</th><td>1 Elm Close, Birmingham</td></tr>
        <tr><th>Proposal</th><td>Crown lift of protected beech tree</td></tr>
    </table>
    </body></html>
    """

    def _fake_response(self, status_code=200, text="", url="https://example-council.gov.uk/online-applications/advancedSearchResults.do"):
        r = MagicMock()
        r.status_code = status_code
        r.text = text
        r.url = url
        return r

    def _run(self, get_response, post_response):
        scraper = mesh_scrapers.IdoxScraper("https://example-council.gov.uk/online-applications")
        with patch("net_utils.smart_get", return_value=get_response), \
             patch("net_utils.smart_post", return_value=post_response):
            return scraper.search_tree_applications(search_term="tree")

    def test_extracts_only_tree_related_leads_from_listing(self):
        leads = self._run(
            self._fake_response(text="<html></html>"),
            self._fake_response(text=self.LISTING_HTML),
        )
        refs = {l["reference"] for l in leads}
        self.assertIn("23/01234/TPO", refs)
        self.assertIn("23/09999/TPO", refs)
        self.assertNotIn("23/05678/FUL", refs)  # extension application must be filtered out
        self.assertEqual(len(leads), 2)

    def test_genuine_no_results_page_returns_empty_without_alert(self):
        with patch.object(mesh_scrapers.IdoxScraper, "_alert_possible_structure_change") as mock_alert:
            leads = self._run(
                self._fake_response(text="<html></html>"),
                self._fake_response(text=self.NO_RESULTS_HTML),
            )
        self.assertEqual(leads, [])
        mock_alert.assert_not_called()

    def test_unexpected_page_shape_triggers_structure_alert(self):
        """A 200 response that's neither a results list, a single-redirect
        detail page, nor a recognisable 'no results' message (e.g. the
        council changed their Idox theme) must be flagged, not silently
        swallowed the way it was before this hardening pass."""
        with patch.object(mesh_scrapers.IdoxScraper, "_alert_possible_structure_change") as mock_alert:
            leads = self._run(
                self._fake_response(text="<html></html>"),
                self._fake_response(text="<html><body><div>Something totally different</div></body></html>"),
            )
        self.assertEqual(leads, [])
        mock_alert.assert_called_once()

    def test_single_result_redirect_page_is_parsed(self):
        leads = self._run(
            self._fake_response(text="<html></html>"),
            self._fake_response(
                text=self.SINGLE_REDIRECT_HTML,
                url="https://example-council.gov.uk/online-applications/applicationDetails.do?id=9",
            ),
        )
        self.assertEqual(len(leads), 1)
        self.assertEqual(leads[0]["reference"], "23/04321/TPO")


class TestApplicantAgentExtraction(unittest.TestCase):
    """_fetch_applicant_and_agent (added Aug 30 2026) reads each application's
    own 'Details' tab to tell a genuinely open lead (no Agent listed) apart
    from one where a contractor has already been hired to file it. Fixture
    HTML below matches the real markup confirmed live against Cornwall
    Council's Idox portal on Aug 30 2026 (th/td rows, exact label text)."""

    DETAILS_WITH_AGENT = """
    <html><body><table>
    <tr class="row0"><th scope="row">Applicant Name</th><td>Mr Colin Hamilton</td></tr>
    <tr class="row1"><th scope="row">Agent Name</th><td>Mr Richard Ede</td></tr>
    <tr class="row0"><th scope="row">Agent Company Name</th><td>Rich Ede TreeSurgeon</td></tr>
    <tr class="row1"><th scope="row">Agent Address</th><td>Rosemelling Cottage, Bodmin</td></tr>
    </table></body></html>
    """

    DETAILS_NO_AGENT = """
    <html><body><table>
    <tr class="row0"><th scope="row">Applicant Name</th><td>Mr Matthew Cotton</td></tr>
    <tr class="row1"><th scope="row">Applicant Address</th><td>The Old Coach House, Bodmin</td></tr>
    <tr class="row0"><th scope="row">Environmental Assessment Requested</th><td>No</td></tr>
    </table></body></html>
    """

    def _fake_response(self, status_code=200, text=""):
        r = MagicMock()
        r.status_code = status_code
        r.text = text
        return r

    def test_agent_present_is_detected(self):
        scraper = mesh_scrapers.IdoxScraper("https://example-council.gov.uk/online-applications")
        with patch("net_utils.smart_get", return_value=self._fake_response(text=self.DETAILS_WITH_AGENT)):
            result = scraper._fetch_applicant_and_agent("ABC123")
        self.assertEqual(result["applicant_name"], "Mr Colin Hamilton")
        self.assertEqual(result["agent_name"], "Mr Richard Ede")
        self.assertEqual(result["agent_company"], "Rich Ede TreeSurgeon")
        self.assertTrue(result["has_agent"])

    def test_no_agent_is_open_lead(self):
        scraper = mesh_scrapers.IdoxScraper("https://example-council.gov.uk/online-applications")
        with patch("net_utils.smart_get", return_value=self._fake_response(text=self.DETAILS_NO_AGENT)):
            result = scraper._fetch_applicant_and_agent("XYZ789")
        self.assertEqual(result["applicant_name"], "Mr Matthew Cotton")
        self.assertNotIn("agent_name", result)
        self.assertNotIn("agent_company", result)
        self.assertFalse(result["has_agent"])

    def test_fetch_failure_returns_empty_not_a_crash(self):
        """A timeout or non-200 must not blow up the whole lead -- caller
        treats a missing key as 'unknown', never as 'no agent'."""
        scraper = mesh_scrapers.IdoxScraper("https://example-council.gov.uk/online-applications")
        with patch("net_utils.smart_get", return_value=self._fake_response(status_code=500)):
            result = scraper._fetch_applicant_and_agent("BROKEN")
        self.assertEqual(result, {})

        with patch("net_utils.smart_get", side_effect=_requests.exceptions.Timeout()):
            result = scraper._fetch_applicant_and_agent("TIMEOUT")
        self.assertEqual(result, {})

    def test_search_results_listing_attaches_agent_info_per_lead(self):
        """End-to-end: a multi-result listing page, where each lead's own
        detail page is then fetched for Applicant/Agent."""
        listing_html = """
        <html><body>
        <ul id="searchresults">
            <li class="searchresult">
                <a href="/online-applications/applicationDetails.do?keyVal=OPEN001&activeTab=summary">23/01234/TPO | Crown reduction of mature oak tree</a>
                <p class="address">12 Oak Lane, Birmingham, B1 1AA</p>
            </li>
            <li class="searchresult">
                <a href="/online-applications/applicationDetails.do?keyVal=TAKEN002&activeTab=summary">23/09999/TPO | Felling of 2no. diseased ash trees</a>
                <p class="address">7 The Green, Birmingham, B3 3CC</p>
            </li>
        </ul>
        </body></html>
        """

        def fake_get(url, session=None, **kwargs):
            if "keyVal=OPEN001" in url:
                return self._fake_response(text=self.DETAILS_NO_AGENT)
            if "keyVal=TAKEN002" in url:
                return self._fake_response(text=self.DETAILS_WITH_AGENT)
            return self._fake_response(text="<html></html>")  # initial CSRF-token GET

        scraper = mesh_scrapers.IdoxScraper("https://example-council.gov.uk/online-applications")
        with patch("net_utils.smart_get", side_effect=fake_get), \
             patch("net_utils.smart_post", return_value=self._fake_response(text=listing_html)), \
             patch("time.sleep", return_value=None):
            leads = scraper.search_tree_applications(search_term="tree")

        by_ref = {l["reference"]: l for l in leads}
        self.assertFalse(by_ref["23/01234/TPO"]["has_agent"])
        self.assertTrue(by_ref["23/09999/TPO"]["has_agent"])
        self.assertEqual(by_ref["23/09999/TPO"]["agent_company"], "Rich Ede TreeSurgeon")


class TestMeshCouncilDedup(unittest.TestCase):
    def test_dedupes_leads_seen_across_multiple_search_terms(self):
        """scrape_mesh_council runs one search per term in IDOX_SEARCH_TERMS
        and merges the results -- a lead matching more than one term (e.g.
        a TPO application containing both "tree" and "tpo") must only
        appear once in the merged output."""
        duplicate_lead = {"reference": "23/01234/TPO", "address": "12 Oak Lane", "description": "TPO tree works"}
        unique_lead = {"reference": "23/05555/TPO", "address": "9 Ash Grove", "description": "Hedge works"}
        call_results = {"tree": [duplicate_lead], "tpo": [duplicate_lead, unique_lead], "hedge": []}

        def fake_search(self, days_back=30, search_term="tree"):
            return call_results.get(search_term, [])

        council = next(iter(mesh_scrapers.COUNCIL_REGISTRY))  # any real Idox council from the registry

        with patch.object(mesh_scrapers.IdoxScraper, "search_tree_applications", fake_search), \
             patch("time.sleep", return_value=None):
            leads = mesh_scrapers.scrape_mesh_council(council)

        refs = [l["reference"] for l in leads]
        self.assertEqual(refs.count("23/01234/TPO"), 1)
        self.assertIn("23/05555/TPO", refs)
        self.assertEqual(len(leads), 2)


class TestNetUtilsResilience(unittest.TestCase):
    """net_utils.smart_get/smart_post -- the retry/backoff/TLS-fallback
    wrapper every scraper now goes through. All network calls are mocked;
    time.sleep is patched out so these run instantly, not over ~1-2s of
    real backoff delay."""

    def setUp(self):
        patcher = patch("time.sleep", return_value=None)
        self.addCleanup(patcher.stop)
        patcher.start()

    def test_returns_response_immediately_on_success(self):
        ok_response = MagicMock(status_code=200)
        with patch("requests.request", return_value=ok_response) as mock_req:
            res = net_utils.smart_get("https://example.com/x")
        self.assertIs(res, ok_response)
        self.assertEqual(mock_req.call_count, 1)
        self.assertEqual(mock_req.call_args.kwargs.get("verify"), True)  # verified first, by default

    def test_retries_transient_connection_error_then_succeeds(self):
        ok_response = MagicMock(status_code=200)
        with patch("requests.request", side_effect=[_connection_error(), ok_response]) as mock_req:
            res = net_utils.smart_get("https://example.com/x", max_retries=2)
        self.assertIs(res, ok_response)
        self.assertEqual(mock_req.call_count, 2)

    def test_gives_up_after_max_retries_on_repeated_500(self):
        bad_response = MagicMock(status_code=500)
        with patch("requests.request", return_value=bad_response) as mock_req:
            res = net_utils.smart_get("https://example.com/x", max_retries=2)
        self.assertIs(res, bad_response)
        self.assertEqual(mock_req.call_count, 3)  # 1 initial attempt + 2 retries

    def test_does_not_retry_on_429(self):
        """A 429 must come back immediately, untouched, so each caller's
        own existing rate-limit handling (stop this pass, alert, etc.)
        stays in charge -- this wrapper must never mask it with a retry."""
        rate_limited = MagicMock(status_code=429)
        with patch("requests.request", return_value=rate_limited) as mock_req:
            res = net_utils.smart_get("https://example.com/x", max_retries=2)
        self.assertIs(res, rate_limited)
        self.assertEqual(mock_req.call_count, 1)

    def test_falls_back_to_unverified_on_ssl_error_and_alerts_once(self):
        ok_response = MagicMock(status_code=200)

        def fake_request(method, url, verify=True, **kwargs):
            if verify:
                raise _ssl_error()
            return ok_response

        net_utils._TLS_ALERT_THROTTLE.clear()
        with patch("requests.request", side_effect=fake_request) as mock_req, \
             patch("notifications.send_system_incident_alert") as mock_alert:
            res = net_utils.smart_get("https://flaky-council.gov.uk/x")

        self.assertIs(res, ok_response)
        self.assertEqual(mock_req.call_count, 2)  # verified attempt, then the unverified fallback
        mock_alert.assert_called_once()

    def test_uses_session_when_provided(self):
        """mesh_scrapers.py's Idox flow needs cookie/CSRF continuity across
        calls -- smart_get/smart_post must call session.request, not the
        bare module-level requests.request, whenever a session is passed."""
        ok_response = MagicMock(status_code=200)
        fake_session = MagicMock()
        fake_session.request.return_value = ok_response

        res = net_utils.smart_get("https://example.com/x", session=fake_session)

        self.assertIs(res, ok_response)
        fake_session.request.assert_called_once()


if __name__ == "__main__":
    unittest.main(verbosity=2)

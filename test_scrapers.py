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
    _fake_database.increment_api_usage = MagicMock(return_value={"warning_needed": False})
    _fake_database.get_scan_progress = MagicMock(return_value=None)
    _fake_database.set_scan_progress = MagicMock()
    sys.modules["database"] = _fake_database

if "notifications" not in sys.modules:
    _fake_notifications = types.ModuleType("notifications")
    _fake_notifications.send_system_incident_alert = MagicMock()
    _fake_notifications.send_resend_email = MagicMock()
    _fake_notifications.dispatch_lead_alerts = MagicMock()
    _fake_notifications.send_api_quota_warning_email = MagicMock()
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


class TestInsertLeadBackfill(unittest.TestCase):
    """_insert_lead (Aug 30 2026 change): the daily scan re-finds the same
    still-pending applications every run, so ON CONFLICT DO NOTHING alone
    would mean a lead's applicant/agent fields -- discovered on some later
    day the scraper re-visits it -- could never reach an already-existing
    row. Switched to DO UPDATE ... COALESCE (fills a NULL field once, never
    overwrites) with Postgres' xmax=0 trick to tell a genuine insert apart
    from a backfill-only touch. This test covers the Python-side branch
    (the SQL COALESCE itself only runs for real inside Postgres) -- a
    mocked cursor can't verify the update happened, only that the function
    correctly treats was_inserted=False as "not a new lead"."""

    def _mock_cursor(self, fetchone_return):
        cur = MagicMock()
        cur.fetchone.return_value = fetchone_return
        return cur

    def test_genuine_insert_returns_lead_dict(self):
        cur = self._mock_cursor(("some-uuid", True))  # (id, was_inserted)
        result = scanners._insert_lead(
            cur, "23/09999/TPO", "7 The Green, Birmingham", "Felling of 2no. diseased ash trees", "Birmingham"
        )
        self.assertIsNotNone(result)
        self.assertEqual(result["ref"], "23/09999/TPO")

    def test_backfill_only_touch_returns_none(self):
        """Same reference already existed -- fields got backfilled in the DB,
        but this must NOT be treated as a new lead to notify/count."""
        cur = self._mock_cursor(("some-uuid", False))  # (id, was_inserted=False)
        result = scanners._insert_lead(
            cur, "23/09999/TPO", "7 The Green, Birmingham", "Felling of 2no. diseased ash trees", "Birmingham",
            applicant_name="Mrs Jane Smith", has_agent=False
        )
        self.assertIsNone(result)


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

    def test_too_many_results_page_does_not_trigger_false_structure_alert(self):
        """Aug 30 2026: Idox's own "too many results, please narrow your
        search" response is a real, valid page distinct from both "no
        results" and a structural break -- it was wrongly triggering the
        false SCRAPER PAGE STRUCTURE alerts seen repeatedly in production
        logs (Cornwall, Nottingham, Glasgow, Bristol, Guildford, Dartford,
        Maidstone, Tunbridge Wells, Winchester)."""
        too_many_html = "<html><body><p>Your search has returned too many results. Please narrow your search.</p></body></html>"
        with patch.object(mesh_scrapers.IdoxScraper, "_alert_possible_structure_change") as mock_alert:
            leads = self._run(
                self._fake_response(text="<html></html>"),
                self._fake_response(text=too_many_html),
            )
        self.assertEqual(leads, [])
        mock_alert.assert_not_called()

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


class TestPlanitRealValueFilter(unittest.TestCase):
    """_planit_real_value: PlanIt returns a placeholder like "See source"
    when it hasn't actually captured a field -- that must never be stored
    as if it were a real applicant/agent name."""

    def test_real_value_passes_through(self):
        self.assertEqual(scanners._planit_real_value("Mr John Smith"), "Mr John Smith")
        self.assertEqual(scanners._planit_real_value("  Rich Ede TreeSurgeon  "), "Rich Ede TreeSurgeon")

    def test_known_placeholders_are_filtered(self):
        for junk in ["See source", "SEE SOURCE", "n/a", "N/A", "None", "", "   "]:
            with self.subTest(junk=junk):
                self.assertIsNone(scanners._planit_real_value(junk))

    def test_non_string_or_missing_is_filtered(self):
        self.assertIsNone(scanners._planit_real_value(None))
        self.assertIsNone(scanners._planit_real_value(123))


class TestPlanitFallback(unittest.TestCase):
    """scan_city_planning_api's Aug 30 2026 rewrite. Root cause of the
    "0 leads found everywhere" pipeline failure, confirmed live against
    the real PlanIt API: wrong param name (postcode vs pcode), a missing
    required radius, and invalid postcode values -- fixed by switching to
    `auth=<real authority name>` via REGION_TOWNS. Also fixed: the free
    PlanIt fallback was wrongly gated behind the paid UK_PLANNING_API_KEY,
    and PlanIt's HTTP-200-but-{"error": ...} responses were silently
    treated as zero results. These tests exercise fetch_planit only
    (through the public scan_city_planning_api entry point), with the
    paid key unset -- net_utils.smart_get is mocked, no real network call,
    and scanners._insert_lead is mocked so these tests don't depend on
    SQL/cursor internals covered separately by TestInsertLeadBackfill."""

    PLANIT_OK = {
        "records": [
            {
                "uid": "24/01111/TPO",
                "description": "Felling of 1no. diseased ash tree, TPO protected.",
                "address": "12 Example Road, Bristol",
                "link": "https://www.planit.org.uk/planapplic/24-01111-tpo",
                "other_fields": {
                    "applicant_name": "Mr John Smith",
                    "agent_name": "See source",      # PlanIt placeholder -- must be filtered out
                    "agent_company": "See source",
                },
            },
            {
                "uid": "24/02222/FUL",
                "description": "Change of use for former bank branch.",  # not tree-related -- must be skipped
                "address": "1 High Street, Bristol",
                "other_fields": {},
            },
        ]
    }

    PLANIT_WITH_REAL_AGENT = {
        "records": [
            {
                "uid": "24/03333/TPO",
                "description": "Crown reduction of mature oak tree, TPO protected.",
                "address": "5 Park Lane, Bristol",
                "other_fields": {
                    "applicant_name": "Mrs Jane Doe",
                    "agent_name": "A. Contractor",
                    "agent_company": "Bristol Tree Surgeons Ltd",
                },
            },
        ]
    }

    PLANIT_ERROR = {"error": "P0001: No valid query field combination supplied"}

    def _fake_response(self, status_code=200, json_data=None):
        resp = MagicMock(status_code=status_code)
        resp.json.return_value = json_data if json_data is not None else {}
        return resp

    def setUp(self):
        # Isolated fake DB conn/cursor -- _insert_lead itself is mocked in
        # every test below, so these just need to not blow up when called.
        self.cur = MagicMock()
        self.conn = MagicMock()
        self.conn.cursor.return_value = self.cur

    def test_planit_runs_even_without_paid_key(self):
        """Previously the whole region (both APIs) returned 0 immediately
        whenever UK_PLANNING_API_KEY was unset -- the key-free PlanIt path
        must still run and find real leads."""
        with patch.object(scanners, "UK_PLANNING_API_KEY", ""), \
             patch("net_utils.smart_get", return_value=self._fake_response(json_data=self.PLANIT_OK)), \
             patch("database.get_db_conn", return_value=self.conn), \
             patch.object(scanners, "_insert_lead", return_value={"ref": "24/01111/TPO"}) as mock_insert, \
             patch("time.sleep", return_value=None):
            count = scanners.scan_city_planning_api("Bristol")

        self.assertEqual(count, 1)  # only the tree-related record counts
        self.assertEqual(mock_insert.call_count, 1)  # the bank-branch record was filtered before insert

    def test_placeholder_agent_values_are_never_stored_as_real(self):
        with patch.object(scanners, "UK_PLANNING_API_KEY", ""), \
             patch("net_utils.smart_get", return_value=self._fake_response(json_data=self.PLANIT_OK)), \
             patch("database.get_db_conn", return_value=self.conn), \
             patch.object(scanners, "_insert_lead", return_value=None) as mock_insert, \
             patch("time.sleep", return_value=None):
            scanners.scan_city_planning_api("Bristol")

        _, kwargs = mock_insert.call_args
        self.assertEqual(kwargs["applicant_name"], "Mr John Smith")
        self.assertIsNone(kwargs["agent_name"])       # "See source" filtered out
        self.assertIsNone(kwargs["agent_company"])    # "See source" filtered out
        self.assertIsNone(kwargs["has_agent"])         # unknown, not "no agent" -- must not be False

    def test_real_agent_sets_has_agent_true(self):
        with patch.object(scanners, "UK_PLANNING_API_KEY", ""), \
             patch("net_utils.smart_get", return_value=self._fake_response(json_data=self.PLANIT_WITH_REAL_AGENT)), \
             patch("database.get_db_conn", return_value=self.conn), \
             patch.object(scanners, "_insert_lead", return_value=None) as mock_insert, \
             patch("time.sleep", return_value=None):
            scanners.scan_city_planning_api("Bristol")

        _, kwargs = mock_insert.call_args
        self.assertEqual(kwargs["agent_company"], "Bristol Tree Surgeons Ltd")
        self.assertTrue(kwargs["has_agent"])

    def test_planit_error_payload_is_not_treated_as_leads(self):
        """PlanIt returns HTTP 200 even for error responses -- the old code
        only checked status_code == 200 and silently read 0 leads from the
        error body. Must now be detected and skipped, not crash."""
        with patch.object(scanners, "UK_PLANNING_API_KEY", ""), \
             patch("net_utils.smart_get", return_value=self._fake_response(json_data=self.PLANIT_ERROR)), \
             patch("database.get_db_conn", return_value=self.conn), \
             patch.object(scanners, "_insert_lead") as mock_insert, \
             patch("time.sleep", return_value=None):
            count = scanners.scan_city_planning_api("Bristol")

        self.assertEqual(count, 0)
        mock_insert.assert_not_called()

    def test_no_prefixes_and_no_towns_returns_zero_without_crashing(self):
        self.assertEqual(scanners.scan_city_planning_api("Nonexistent Region"), 0)

    def test_paid_api_mismatched_outcode_is_skipped_not_mislabeled(self):
        """Aug 30 2026: ukplanningapi.co.uk was found (during the PlanIt
        live-testing pass) to sometimes return an address that doesn't
        actually match the requested postcode-prefix param -- e.g. a
        "Sheffield"-requested scan returning a Kent/Tonbridge address.
        Rather than trust the paid API's filtering and mislabel
        council_source, the returned outcode must be checked against the
        requested prefix; a mismatch is skipped, not relabeled or guessed."""
        body = {
            "data": [
                {"reference": "24/1000/TPO", "description": "Felling of 2no. ash trees, TPO protected.",
                 "address": "10 Example Road, Sheffield S1 2AB"},
                {"reference": "24/2000/TPO", "description": "Crown reduction of oak tree, TPO protected.",
                 "address": "5 High Street, Tonbridge TN9 1AB"},  # wrong region for prefix "S"
            ]
        }
        resp = MagicMock(status_code=200)
        resp.json.return_value = body

        with patch.object(scanners, "UK_PLANNING_API_KEY", "fake-key"), \
             patch.object(scanners, "CITY_POSTCODE_PREFIX", {"Sheffield": ["S"]}), \
             patch.object(scanners, "REGION_TOWNS", {}), \
             patch("net_utils.smart_get", return_value=resp), \
             patch("database.get_db_conn", return_value=self.conn), \
             patch.object(scanners, "_insert_lead", return_value={"ref": "24/1000/TPO"}) as mock_insert, \
             patch("time.sleep", return_value=None):
            count = scanners.scan_city_planning_api("Sheffield")

        self.assertEqual(count, 1)  # only the correctly-attributed Sheffield lead counts
        mock_insert.assert_called_once()
        args, _ = mock_insert.call_args
        self.assertIn("S1 2AB", args[2])  # the Tonbridge/Kent record was skipped


if __name__ == "__main__":
    unittest.main(verbosity=2)

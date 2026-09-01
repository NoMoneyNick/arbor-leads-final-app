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
import os
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
import research    # noqa: E402


class _FakeDedupStore:
    """Sep 1 2026: test double for persistent_dedup_cache, used in place of
    the real module wherever a test needs to control/observe same-day-dedup
    state. scanners.py's day-caches (mesh sweep, paid-API rotation, PlanIt,
    GLA) moved from in-memory dicts to persistent_dedup_cache (backed by a
    real DB connection) so the guard survives a Render redeploy instead of
    silently resetting with it -- see scanners.py's Sep 1 comments for the
    live-log evidence of why that mattered.

    That real module does genuine SQL against whatever connection it's
    given, which doesn't play well with these tests' MagicMock DB
    connections (a MagicMock's default __eq__/__getitem__ behaviour makes
    already_done_today's real SQL-round-trip logic behave unpredictably,
    not because the code is wrong but because the mock has no real
    storage). This fake mirrors the same three-function interface with a
    plain in-memory set, scoped to one test via patch.object(scanners,
    'dedup', new=_FakeDedupStore()) in setUp -- same isolation the old
    `scanners._SOMETHING_DAY_CACHE.clear()` pattern gave directly against
    the dict, just aimed at the new module instead."""

    def __init__(self):
        self.done_today: set = set()

    def ensure_table(self, conn):
        pass

    def already_done_today(self, conn, key: str) -> bool:
        return key in self.done_today

    def mark_done_today(self, conn, key: str) -> None:
        self.done_today.add(key)


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

    def test_vertical_defaults_to_tree_for_every_call_site_written_before_it_existed(self):
        """Sep 2 2026: `vertical` was added as an _insert_lead parameter for
        the multi-vertical build, defaulting to "tree" specifically so the
        pipeline's existing call sites (none of which pass it) keep
        inserting tree leads with the correct vertical, unchanged."""
        cur = self._mock_cursor(("some-uuid", True))
        result = scanners._insert_lead(
            cur, "23/08888/TPO", "1 Elm Street, Leeds", "Crown reduction of mature oak tree", "Leeds"
        )
        self.assertEqual(result["vertical"], "tree")
        _, params = cur.execute.call_args[0]
        self.assertEqual(params[-1], "tree")  # vertical is the last bound param

    def test_vertical_param_is_passed_through_and_stored(self):
        cur = self._mock_cursor(("some-uuid", True))
        result = scanners._insert_lead(
            cur, "26/01234/HMO", "9 Ivy Road, Bristol",
            "Change of use to a house in multiple occupation (7 persons)", "Bristol",
            vertical="hmo",
        )
        self.assertEqual(result["vertical"], "hmo")
        _, params = cur.execute.call_args[0]
        self.assertEqual(params[-1], "hmo")

    def test_hmo_lead_never_stores_applicant_or_agent_identity_even_if_passed_in(self):
        """Sep 2 2026, master_expansion_plan_v2.md build-order step 3 (the
        GDPR-safe lead format): HMO is configured with capture_identity=False
        in VERTICALS. This proves the enforcement happens at _insert_lead
        itself, not just "current call sites happen not to pass these" --
        even a caller that DOES pass a real name/agent must have it stripped
        before it ever reaches the INSERT. This is what "built in from day
        one" means: it's structurally impossible for an HMO row to end up
        with a name in it, not merely unlikely given today's call sites."""
        cur = self._mock_cursor(("some-uuid", True))
        result = scanners._insert_lead(
            cur, "26/05555/HMO", "12 Ivy Road, Bristol",
            "Change of use to a house in multiple occupation (7 persons)", "Bristol",
            applicant_name="Mrs Jane Smith",
            agent_name="John Doe", agent_company="Doe Conversions Ltd",
            has_agent=True, agent_is_tree_surgeon=False,
            vertical="hmo",
        )
        self.assertIsNotNone(result)
        for key in ("applicant_name", "agent_name", "agent_company", "has_agent", "agent_is_tree_surgeon"):
            self.assertIsNone(result[key], f"{key} should have been stripped for an HMO lead")

        _, params = cur.execute.call_args[0]
        # INSERT param order: (reference, address, summary, source, lead_score,
        # lead_price, applicant_name, agent_name, agent_company, has_agent,
        # agent_is_tree_surgeon, vertical) -- indices 6-10 are the 5 identity fields.
        self.assertEqual(params[6:11], (None, None, None, None, None))
        self.assertEqual(params[-1], "hmo")

    def test_tree_lead_identity_capture_is_completely_unaffected(self):
        """The other half of the fix: capture_identity defaults to True and
        tree is explicitly configured True, so this existing, live business
        behaviour (has_agent exclusion filter, the /dashboard "Applicant:"
        display) must be provably unchanged by adding the flag."""
        cur = self._mock_cursor(("some-uuid", True))
        result = scanners._insert_lead(
            cur, "23/09999/TPO", "7 The Green, Birmingham",
            "Felling of 2no. diseased ash trees", "Birmingham",
            applicant_name="Mrs Jane Smith",
            agent_name="John Doe", agent_company="Doe Tree Surgery Ltd",
            has_agent=True, agent_is_tree_surgeon=True,
            vertical="tree",
        )
        self.assertEqual(result["applicant_name"], "Mrs Jane Smith")
        self.assertEqual(result["agent_name"], "John Doe")
        self.assertEqual(result["agent_company"], "Doe Tree Surgery Ltd")
        self.assertIs(result["has_agent"], True)
        self.assertIs(result["agent_is_tree_surgeon"], True)

    def test_falls_back_to_legacy_insert_when_vertical_column_is_missing(self):
        """Sep 2 2026 production incident: the `vertical` column's own ALTER
        TABLE migration can be delayed by lock contention on the busy `leads`
        table (see database._run_ddl_statements_resiliently's docstring for
        the full incident) -- without this fallback, EVERY insert across
        BOTH verticals fails with "column vertical does not exist" until
        that migration lands, taking lead capture to zero. This is exactly
        what happened live: caught only because Nick noticed an unrelated
        page hadn't updated after a deploy. Proves _insert_lead detects that
        specific failure and retries without the vertical column instead of
        losing the lead."""
        cur = self._mock_cursor(None)  # overwritten below via side_effect
        cur.execute.side_effect = [
            Exception('column "vertical" of relation "leads" does not exist'),
            None,
        ]
        cur.fetchone.return_value = ("some-uuid", True)
        result = scanners._insert_lead(
            cur, "23/07777/TPO", "3 Oak Ave, Leeds", "Felling of 1no. diseased oak tree", "Leeds"
        )
        self.assertIsNotNone(result)
        self.assertEqual(cur.execute.call_count, 2)
        cur.connection.rollback.assert_called_once()
        second_call_sql = cur.execute.call_args_list[1][0][0]
        self.assertNotIn("vertical", second_call_sql)

    def test_unrelated_insert_error_is_not_swallowed_by_the_vertical_fallback(self):
        """Only the specific 'vertical column missing' failure mode should be
        caught and retried -- any other DB error (a real constraint
        violation, a dropped connection, etc.) must still propagate
        normally rather than being silently masked."""
        cur = self._mock_cursor(None)
        cur.execute.side_effect = Exception("connection to server was lost")
        with self.assertRaises(Exception):
            scanners._insert_lead(
                cur, "23/06666/TPO", "4 Oak Ave, Leeds", "Felling of 1no. diseased oak tree", "Leeds"
            )


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

    def test_plain_english_fell_phrasing_from_real_live_data(self):
        """Sep 2 2026: found by sampling real live PlanIt data for
        Nottingham while sanity-checking HMO_GOLD against genuine
        applications (not synthetic test text) -- "Fell a dead tree in
        rear garden." is a real, currently-live application that matched
        NONE of TREE_GOLD's phrasing at the time: not "felling"/"fell to
        ground"/"fell 1/2/3", no species name, and "dead tree" is a
        distinct phrase from "deadwood"/"dead wood"/"dead branches". A
        genuine false negative, unrelated to the multi-vertical work this
        session's sampling pass was actually checking -- fixed by adding
        "dead tree" plus the "a"/"the" article variants of the existing
        "fell ... tree" / "remove tree" phrasing, all essentially
        unambiguous in real English."""
        real = [
            "Fell a dead tree in rear garden.",
            "Please can you fell the tree at the front of my house, it is dying.",
            "Request to remove a tree that is damaging the driveway.",
            "Removal of a tree overhanging the neighbour's fence.",
        ]
        for text in real:
            with self.subTest(text=text):
                self.assertTrue(scanners._is_tree_related(text))

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


class TestVerticalsClassifier(unittest.TestCase):
    """Sep 2 2026: first piece of the multi-vertical build. _is_tree_related
    became a thin wrapper over the generalized _matches_vertical/VERTICALS
    config -- these tests exist specifically to prove that generalization
    didn't change tree's own behaviour at all (TestTreeGoldFiltering above
    covers _is_tree_related's black-box behaviour directly; these cover the
    new generic layer underneath it, plus the new HMO vertical)."""

    def test_is_tree_related_unchanged_via_generic_matcher(self):
        self.assertTrue(scanners._matches_vertical("Felling of 2no. diseased ash trees", "tree"))
        self.assertTrue(scanners._is_tree_related("Felling of 2no. diseased ash trees"))
        self.assertFalse(scanners._matches_vertical("Erection of a two-storey rear extension", "tree"))

    def test_hmo_gold_matches_real_hmo_applications(self):
        real = [
            "Change of use to a house in multiple occupation (7 persons).",
            "Conversion to a small HMO for 4 unrelated sharers.",
            "Application under Class C4 for use as a house in multiple occupation.",
            "Sui generis HMO for 8 occupants with associated bin store.",
        ]
        for text in real:
            with self.subTest(text=text):
                self.assertTrue(scanners._matches_vertical(text, "hmo"))

    def test_hmo_gold_excludes_unrelated_change_of_use(self):
        """The precision check: bare "change of use" must NOT trigger the
        HMO vertical on its own -- only HMO-qualified change-of-use phrasing
        should. Mirrors TREE_GOLD's own bare-"fell" false-positive lesson."""
        junk = [
            "Change of use from retail (Class E) to restaurant (Class E).",
            "Change of use of ground floor office to gymnasium.",
            "Erection of a two-storey rear extension.",
            "Felling of 2no. diseased ash trees.",
        ]
        for text in junk:
            with self.subTest(text=text):
                self.assertFalse(scanners._matches_vertical(text, "hmo"))

    def test_bare_sui_generis_and_article_4_do_not_falsely_tag_unrelated_applications(self):
        """Sep 2 2026: an adversarial review pass (run before this build ships)
        caught that bare "sui generis" and bare "article 4 direction" used to
        sit in HMO_GOLD unqualified -- both are general planning mechanisms
        used for HMOs but also for many completely unrelated uses (sui generis
        covers nightclubs, drive-throughs, casinos, scrapyards; Article 4
        directions cover shopfronts, agricultural conversions, demolition
        control, and more). The exact class of bug TREE_GOLD's bare-"fell"
        false positive already taught this project to avoid."""
        junk = [
            "Change of use to sui generis (drive-through restaurant), no external alterations.",
            "Erection of a sui generis nightclub with associated car parking.",
            "Prior notification for removal of Article 4 Direction restricting "
            "conversion of an agricultural building to residential use.",
            "Application to vary conditions on an Article 4 Direction covering "
            "shopfront alterations in the conservation area.",
        ]
        for text in junk:
            with self.subTest(text=text):
                self.assertFalse(scanners._matches_vertical(text, "hmo"))

    def test_qualified_sui_generis_and_article_4_hmo_phrasing_still_matches(self):
        """The other half of the fix above: real large-HMO/Article-4-for-HMO
        applications, phrased the way they actually are in practice, must
        still match -- the fix trades bare-keyword risk for required context,
        not recall entirely."""
        real = [
            "Certificate of lawfulness for a large HMO (sui generis) for 9 persons.",
            "Change of use to sui generis house in multiple occupation.",
            "Article 4 Direction removing permitted development rights for a "
            "house in multiple occupation at this address.",
        ]
        for text in real:
            with self.subTest(text=text):
                self.assertTrue(scanners._matches_vertical(text, "hmo"))

    def test_unknown_vertical_key_returns_false_not_an_exception(self):
        self.assertFalse(scanners._matches_vertical("anything at all", "not-a-real-vertical"))

    def test_classify_verticals_returns_every_match(self):
        self.assertEqual(scanners.classify_verticals("Felling of 2no. diseased ash trees"), ["tree"])
        self.assertEqual(
            scanners.classify_verticals("Change of use to a house in multiple occupation (7 persons)."),
            ["hmo"],
        )
        self.assertEqual(scanners.classify_verticals("Erection of a two-storey rear extension."), [])

    def test_classify_verticals_multi_label_for_an_application_matching_both(self):
        """A single application can genuinely be both -- e.g. an HMO
        conversion that also involves removing a protected tree. Multi-label
        classification (not a single best-guess category) is what makes the
        "sell into every matching vertical" monetization idea buildable."""
        text = "Conversion to a house in multiple occupation including felling of 1no. protected oak tree."
        result = scanners.classify_verticals(text)
        self.assertCountEqual(result, ["tree", "hmo"])


class TestResolveVertical(unittest.TestCase):
    """Sep 2 2026: _resolve_vertical is what the four broad-fetching scan
    call sites (Leeds, GLA Datahub, and the paid-API/PlanIt loops inside
    scan_city_planning_api) use to decide which single vertical to tag a
    lead with, given the `leads` table's ON CONFLICT (reference) constraint
    only supports one row per application today (not yet one row per
    matched vertical -- see TASKS.md)."""

    def test_no_match_returns_none(self):
        self.assertIsNone(scanners._resolve_vertical("Erection of a two-storey rear extension."))

    def test_tree_only_match_resolves_to_tree(self):
        self.assertEqual(
            scanners._resolve_vertical("Felling of 2no. diseased ash trees"), "tree"
        )

    def test_hmo_only_match_resolves_to_hmo(self):
        """This is the previously-discarded case: before this vertical
        existed, an HMO-only application failed _is_tree_related and was
        silently skipped by every call site -- never reached _insert_lead
        at all. This is new, additive pipeline output, not a change to
        anything that was already flowing."""
        self.assertEqual(
            scanners._resolve_vertical("Change of use to a house in multiple occupation (7 persons)."),
            "hmo",
        )

    def test_matching_both_resolves_to_tree_not_hmo(self):
        """Tree always wins on a dual match -- deliberate, so every existing
        tree lead's behaviour/economics are provably unchanged by this
        refactor. An application matching both verticals is real additional
        upside (documented in master_expansion_plan_v2.md as "sell into
        every matching vertical"), but realizing that upside needs a schema
        change (composite unique constraint on (reference, vertical)) that
        hasn't been built yet -- this is the safe interim choice, not the
        final one."""
        text = "Conversion to a house in multiple occupation including felling of 1no. protected oak tree."
        self.assertEqual(scanners._resolve_vertical(text), "tree")


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

    # Aug 30 2026: some applications are filed by a company/organisation
    # rather than a person -- Idox labels that row "Applicant Company Name"
    # instead of "Applicant Name". Before this fix, that row was never
    # matched at all, so every company-applicant lead silently lost its
    # applicant name entirely.
    DETAILS_COMPANY_APPLICANT = """
    <html><body><table>
    <tr class="row0"><th scope="row">Applicant Company Name</th><td>Bodmin Property Holdings Ltd</td></tr>
    <tr class="row1"><th scope="row">Applicant Address</th><td>Unit 4, Bodmin Business Park</td></tr>
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

    def test_company_applicant_uses_company_name_label(self):
        """The 'Applicant Company Name' fallback must fill applicant_name
        when the plain 'Applicant Name' row isn't present at all."""
        scraper = mesh_scrapers.IdoxScraper("https://example-council.gov.uk/online-applications")
        with patch("net_utils.smart_get", return_value=self._fake_response(text=self.DETAILS_COMPANY_APPLICANT)):
            result = scraper._fetch_applicant_and_agent("COMPANY001")
        self.assertEqual(result["applicant_name"], "Bodmin Property Holdings Ltd")

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

    def test_placeholder_agent_company_is_not_treated_as_a_real_agent(self):
        """Aug 31 2026: councils fill an empty Agent Company Name cell with
        text like 'Not Available' instead of leaving it blank -- confirmed
        live (8 of 186 'has agent' leads in one export were literally 'Not
        Available'). That must not flip has_agent to True."""
        details_html = """
        <html><body><table>
        <tr class="row0"><th scope="row">Applicant Name</th><td>Mrs Jane Doe</td></tr>
        <tr class="row1"><th scope="row">Agent Company Name</th><td>Not Available</td></tr>
        </table></body></html>
        """
        scraper = mesh_scrapers.IdoxScraper("https://example-council.gov.uk/online-applications")
        with patch("net_utils.smart_get", return_value=self._fake_response(text=details_html)):
            result = scraper._fetch_applicant_and_agent("PLACEHOLDER1")
        self.assertNotIn("agent_company", result)
        self.assertFalse(result.get("has_agent"))
        self.assertEqual(result.get("applicant_name"), "Mrs Jane Doe")


class TestClassifyAgentAsTreeSurgeon(unittest.TestCase):
    """classify_agent_as_tree_surgeon (Aug 31 2026): Nick's point -- 'an
    agent' on a planning application isn't always a tree surgeon. An
    architect, planning consultant, or block management company filing the
    paperwork doesn't mean the tree work itself is taken."""

    def test_obvious_tree_company_classifies_true(self):
        self.assertTrue(mesh_scrapers.classify_agent_as_tree_surgeon(None, "Red Squirrel Tree Surgery"))
        self.assertTrue(mesh_scrapers.classify_agent_as_tree_surgeon(None, "Cheltenham Tree Services Ltd"))
        self.assertTrue(mesh_scrapers.classify_agent_as_tree_surgeon("John Smith", "ABC Arboriculture Ltd"))

    def test_obvious_non_tree_agent_classifies_false(self):
        self.assertFalse(mesh_scrapers.classify_agent_as_tree_surgeon(None, "DP Architecture"))
        self.assertFalse(mesh_scrapers.classify_agent_as_tree_surgeon(None, "D&G Block Management"))
        self.assertFalse(mesh_scrapers.classify_agent_as_tree_surgeon(None, "Nottingham City Council"))
        self.assertFalse(mesh_scrapers.classify_agent_as_tree_surgeon(None, "Hybrid Planning & Development"))

    def test_bare_personal_name_is_unknown_not_assumed_open(self):
        # A person's name alone gives no signal either way -- must not be
        # assumed "still open" (that's the costlier mistake to get wrong).
        self.assertIsNone(mesh_scrapers.classify_agent_as_tree_surgeon("Julian Schad", None))

    def test_ambiguous_text_matching_both_lists_is_unknown(self):
        self.assertIsNone(mesh_scrapers.classify_agent_as_tree_surgeon(None, "Tree Design Associates"))

    def test_empty_input_is_unknown(self):
        self.assertIsNone(mesh_scrapers.classify_agent_as_tree_surgeon(None, None))
        self.assertIsNone(mesh_scrapers.classify_agent_as_tree_surgeon("", ""))


class TestConfirmAgentStatusFromSource(unittest.TestCase):
    """confirm_agent_status_from_source / _parse_idox_detail_url (Aug 30
    2026): PlanIt's own field dictionary confirms it deliberately never
    stores real applicant/agent names, but its "url" field (and
    other_fields.source_url) point back to the real council portal page --
    usually the same Idox software this file already knows how to read.
    This turns most of PlanIt's structural "unconfirmed" into a real,
    confirmed yes/no by following that link."""

    def _fake_response(self, status_code=200, text=""):
        r = MagicMock()
        r.status_code = status_code
        r.text = text
        return r

    def test_parses_online_applications_url_with_keyval(self):
        result = mesh_scrapers._parse_idox_detail_url(
            "https://publicaccess.somecouncil.gov.uk/online-applications/applicationDetails.do?activeTab=summary&keyVal=ABC123"
        )
        self.assertEqual(result, ("https://publicaccess.somecouncil.gov.uk/online-applications", "ABC123"))

    def test_parses_idoxpa_web_variant(self):
        result = mesh_scrapers._parse_idox_detail_url(
            "https://citydev-portal.edinburgh.gov.uk/idoxpa-web/applicationDetails.do?keyVal=XYZ789&activeTab=summary"
        )
        self.assertEqual(result, ("https://citydev-portal.edinburgh.gov.uk/idoxpa-web", "XYZ789"))

    def test_non_idox_url_returns_none(self):
        """A council on genuinely different software (e.g. Northgate/NEC) --
        must not be misparsed as an Idox authority."""
        result = mesh_scrapers._parse_idox_detail_url("https://planning.somecouncil.gov.uk/portal/servlet/planning?keyVal=ABC123")
        self.assertIsNone(result)

    def test_missing_keyval_returns_none(self):
        result = mesh_scrapers._parse_idox_detail_url("https://publicaccess.somecouncil.gov.uk/online-applications/search.do?action=advanced")
        self.assertIsNone(result)

    def test_empty_or_non_string_returns_none(self):
        self.assertIsNone(mesh_scrapers._parse_idox_detail_url(""))
        self.assertIsNone(mesh_scrapers._parse_idox_detail_url(None))

    def test_confirm_fetches_real_agent_status_for_idox_source(self):
        details_html = """
        <html><body><table>
        <tr><th scope="row">Applicant Name</th><td>Mrs Angela Ford</td></tr>
        <tr><th scope="row">Agent Company Name</th><td>Ford & Sons Tree Surgery</td></tr>
        </table></body></html>
        """
        source_url = "https://publicaccess.somecouncil.gov.uk/online-applications/applicationDetails.do?keyVal=REAL001&activeTab=summary"
        with patch("net_utils.smart_get", return_value=self._fake_response(text=details_html)):
            result = mesh_scrapers.confirm_agent_status_from_source(source_url)
        self.assertEqual(result["applicant_name"], "Mrs Angela Ford")
        self.assertEqual(result["agent_company"], "Ford & Sons Tree Surgery")
        self.assertTrue(result["has_agent"])

    def test_confirm_returns_empty_dict_for_non_idox_source(self):
        result = mesh_scrapers.confirm_agent_status_from_source("https://planning.somecouncil.gov.uk/portal/servlet/planning?keyVal=ABC123")
        self.assertEqual(result, {})

    def test_confirm_returns_empty_dict_for_blank_source(self):
        self.assertEqual(mesh_scrapers.confirm_agent_status_from_source(""), {})


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


class TestIdoxPathRouting(unittest.TestCase):
    """Aug 30 2026: scrape_mesh_council() used to only recognise
    "online-applications" in a council's URL as a working Idox instance --
    anything else (including other standard Idox conventions like
    "publicaccess" and "idoxpa-web") silently returned [] with no error,
    no log, nothing. Confirmed live against the registry: Edinburgh
    (idoxpa-web) and Dacorum (publicaccess) were both being scraped for
    zero leads every run despite being real, working Idox portals."""

    def _fake_no_leads(self, days_back=30, search_term="tree"):
        return []

    def test_publicaccess_path_is_routed_to_idox_engine(self):
        with patch.object(mesh_scrapers.IdoxScraper, "search_tree_applications", self._fake_no_leads), \
             patch("time.sleep", return_value=None), \
             patch.dict(mesh_scrapers.COUNCIL_REGISTRY, {"TEST PUBLICACCESS": "https://planning.example.gov.uk/publicaccess"}):
            with patch.object(mesh_scrapers, "logger") as mock_logger:
                mesh_scrapers.scrape_mesh_council("TEST PUBLICACCESS")
                mock_logger.info.assert_any_call(
                    "[MESH] Routing TEST PUBLICACCESS to free Idox Engine..."
                )

    def test_idoxpa_web_path_is_routed_to_idox_engine(self):
        with patch.object(mesh_scrapers.IdoxScraper, "search_tree_applications", self._fake_no_leads), \
             patch("time.sleep", return_value=None), \
             patch.dict(mesh_scrapers.COUNCIL_REGISTRY, {"TEST IDOXPA": "https://planning.example.gov.uk/idoxpa-web"}):
            with patch.object(mesh_scrapers, "logger") as mock_logger:
                mesh_scrapers.scrape_mesh_council("TEST IDOXPA")
                mock_logger.info.assert_any_call(
                    "[MESH] Routing TEST IDOXPA to free Idox Engine..."
                )

    def test_genuinely_non_idox_url_still_returns_empty_without_routing(self):
        """A council on a real non-Idox platform (Northgate, Salesforce,
        etc.) must NOT be routed into IdoxScraper -- that would just waste
        requests hammering a page IdoxScraper can't parse."""
        with patch.dict(mesh_scrapers.COUNCIL_REGISTRY, {"TEST NORTHGATE": "https://planning1.example.gov.uk/Northgate/PlanningExplorer/GeneralSearch.aspx"}):
            leads = mesh_scrapers.scrape_mesh_council("TEST NORTHGATE")
        self.assertEqual(leads, [])

    def test_confirmed_idox_exceptions_are_routed_despite_no_marker_match(self):
        """Sep 1 2026: Fife (/online) and Derby (bare domain root) are both
        real, live, working Idox portals confirmed via browser -- neither
        URL contains any of _KNOWN_IDOX_PATH_MARKERS, so without this
        explicit exception list they'd silently return [] forever despite
        being perfectly good Idox instances, the exact bug this whole class
        exists to catch for the other three markers. Uses the real
        registry entries directly (not a patched fake) so this breaks
        loudly if either URL is ever "cleaned up" back out of
        _CONFIRMED_IDOX_EXCEPTIONS without updating the other."""
        for council in ("FIFE", "DERBY"):
            with self.subTest(council=council):
                self.assertIn(mesh_scrapers.COUNCIL_REGISTRY[council], mesh_scrapers._CONFIRMED_IDOX_EXCEPTIONS)
                with patch.object(mesh_scrapers.IdoxScraper, "search_tree_applications", self._fake_no_leads), \
                     patch("time.sleep", return_value=None), \
                     patch.object(mesh_scrapers, "logger") as mock_logger:
                    mesh_scrapers.scrape_mesh_council(council)
                    mock_logger.info.assert_any_call(f"[MESH] Routing {council} to free Idox Engine...")

    def test_manchester_and_wiltshire_are_no_longer_in_the_registry(self):
        """Sep 1 2026: both confirmed migrated to non-Idox platforms
        (Manchester and Wiltshire both to Arcus BE) with no correct Idox
        URL to swap in -- removed rather than left pointing at a URL that
        can never work. A future re-add needs a real Arcus BE adapter, not
        a registry entry."""
        self.assertNotIn("MANCHESTER", mesh_scrapers.COUNCIL_REGISTRY)
        self.assertNotIn("WILTSHIRE", mesh_scrapers.COUNCIL_REGISTRY)

    def test_sutton_was_readded_after_being_wrongly_written_off(self):
        """Sep 1 2026, second-pass audit: the Aug 30 removal of SUTTON gave
        up after a web search only surfaced an unrelated Cambridgeshire
        parish council of the same name. A proper search this time found
        the real portal, and a live browser check confirmed it: title
        "Applications Search | Sutton Council" (the same Idox title
        pattern already confirmed for Gloucester and Fife), classic Idox
        Simple/Advanced search tabs, and a search.do URL under
        /online-applications -- the two strongest Idox tells this whole
        audit has used, together, on the same URL. Uses the real registry
        entry directly so this breaks loudly if it's ever removed again
        without re-verifying."""
        self.assertIn("SUTTON", mesh_scrapers.COUNCIL_REGISTRY)
        self.assertIn("online-applications", mesh_scrapers.COUNCIL_REGISTRY["SUTTON"])
        with patch.object(mesh_scrapers.IdoxScraper, "search_tree_applications", self._fake_no_leads), \
             patch("time.sleep", return_value=None), \
             patch.object(mesh_scrapers, "logger") as mock_logger:
            mesh_scrapers.scrape_mesh_council("SUTTON")
            mock_logger.info.assert_any_call("[MESH] Routing SUTTON to free Idox Engine...")

    def test_every_registered_council_is_actually_routable(self):
        """Sep 1 2026: the whole point of this audit -- every URL left in
        COUNCIL_REGISTRY must be reachable via either a known path marker
        or an explicit confirmed exception. A registry entry matching
        neither is dead weight that silently produces zero leads forever,
        exactly the bug this test suite exists to prevent from recurring
        unnoticed (a plain DNS failure at least logs an error; this
        category doesn't even do that, which is how it went unnoticed for
        as long as it did)."""
        for council, url in mesh_scrapers.COUNCIL_REGISTRY.items():
            with self.subTest(council=council):
                routable = (
                    any(marker in url.lower() for marker in mesh_scrapers._KNOWN_IDOX_PATH_MARKERS)
                    or url in mesh_scrapers._CONFIRMED_IDOX_EXCEPTIONS
                )
                self.assertTrue(routable, f"{council} ({url}) matches no Idox marker and isn't a confirmed exception")


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
        # Sep 1 2026: the same-day dedup caches now live in
        # persistent_dedup_cache (see _FakeDedupStore docstring above) --
        # a fresh fake store per test gives the same per-test isolation the
        # old scanners._PAID_API_DAY_CACHE.clear() / _PLANIT_DAY_CACHE.clear()
        # calls gave directly against the in-memory dicts.
        dedup_patch = patch.object(scanners, "dedup", new=_FakeDedupStore())
        dedup_patch.start()
        self.addCleanup(dedup_patch.stop)
        # Aug 30 2026: the cross-region PlanIt pacing gate is also
        # module-level, tracking real wall-clock time (time.monotonic())
        # across the whole process -- reset it so one test's PlanIt calls
        # don't leave the "last request" timestamp fresh enough to make the
        # next test's very first call think it needs to wait too.
        scanners._PLANIT_LAST_REQUEST_AT = 0.0

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

    def test_planit_429_backs_off_and_retries_once_then_succeeds(self):
        """Aug 30 2026: live logs showed PlanIt returning 429 for every
        single authority in every region, days in a row -- indistinguishable
        from a genuine zero-results run until the new aggregate warning
        surfaced it. PlanIt's own docs say a 429 carries a Retry-After
        header callers should wait out before retrying. This must actually
        wait it out and succeed on the retry, not give up immediately."""
        rate_limited = MagicMock(status_code=429)
        rate_limited.headers = {"Retry-After": "3"}
        success = self._fake_response(json_data={"records": [
            {"uid": "23/9999/TRE", "description": "Fell 1 Oak", "address": "1 Test St", "link": "", "other_fields": {}}
        ]})
        success.headers = {}

        with patch.object(scanners, "UK_PLANNING_API_KEY", ""), \
             patch("net_utils.smart_get", side_effect=[rate_limited, success]), \
             patch("database.get_db_conn", return_value=self.conn), \
             patch.object(scanners, "_insert_lead", return_value={"reference": "23/9999/TRE"}), \
             patch("time.sleep") as mock_sleep:
            count = scanners.scan_city_planning_api("Bristol")

        self.assertEqual(count, 1)
        # Must have waited out the Retry-After value (3s) somewhere in its sleeps.
        slept_for = [c.args[0] for c in mock_sleep.call_args_list if c.args]
        self.assertIn(3.0, slept_for)

    def test_planit_429_with_huge_retry_after_is_skipped_not_slept_through(self):
        """Aug 31 2026 incident: PlanIt returned Retry-After: 20070 (5.6
        hours) after PLANIT_MIN_INTERVAL_SECONDS was lowered. Sleeping that
        out synchronously on the single PlanIt worker thread stalled the
        whole pipeline for hours -- indistinguishable from a hang. Past
        PLANIT_MAX_RETRY_WAIT_SECONDS, this must give up on the town
        immediately (no multi-hour time.sleep) rather than block."""
        rate_limited = MagicMock(status_code=429)
        rate_limited.headers = {"Retry-After": "20070"}

        with patch.object(scanners, "UK_PLANNING_API_KEY", ""), \
             patch("net_utils.smart_get", return_value=rate_limited), \
             patch("database.get_db_conn", return_value=self.conn), \
             patch.object(scanners, "_insert_lead") as mock_insert, \
             patch("time.sleep", return_value=None) as mock_sleep, \
             patch.object(scanners, "logger") as mock_logger:
            count = scanners.scan_city_planning_api("Bristol")

        self.assertEqual(count, 0)
        mock_insert.assert_not_called()
        # The one real sleep here is the pacing gate (<= PLANIT_MIN_INTERVAL_SECONDS);
        # the 20070s Retry-After itself must never reach time.sleep().
        slept_for = [c.args[0] for c in mock_sleep.call_args_list if c.args]
        self.assertTrue(all(s < 100 for s in slept_for), f"a huge Retry-After leaked into a real sleep: {slept_for}")
        warning_texts = [c.args[0] for c in mock_logger.warning.call_args_list if c.args]
        self.assertTrue(any("hard block" in t for t in warning_texts))

    def test_planit_429_gives_up_after_one_retry_and_is_counted_as_a_failure(self):
        """If the retry ALSO 429s, this must be recorded as a real failure
        (so the "PlanIt failed for ALL authorities" aggregate warning can
        fire) rather than silently returning an empty, indistinguishable-
        from-genuine-zero result."""
        rate_limited = MagicMock(status_code=429)
        rate_limited.headers = {"Retry-After": "1"}

        with patch.object(scanners, "UK_PLANNING_API_KEY", ""), \
             patch("net_utils.smart_get", return_value=rate_limited), \
             patch("database.get_db_conn", return_value=self.conn), \
             patch.object(scanners, "_insert_lead") as mock_insert, \
             patch("time.sleep", return_value=None), \
             patch.object(scanners, "logger") as mock_logger:
            count = scanners.scan_city_planning_api("Bristol")

        self.assertEqual(count, 0)
        mock_insert.assert_not_called()
        # The "failed for ALL authorities" warning must have fired.
        warning_texts = [c.args[0] for c in mock_logger.warning.call_args_list if c.args]
        self.assertTrue(any("PlanIt failed for ALL" in t for t in warning_texts))

    def test_planit_pacing_gate_waits_out_the_minimum_interval(self):
        """_planit_wait_for_slot() is the Aug 30 2026 fix for the blanket-429
        failure: a per-region-local time.sleep(1.5) had no memory of earlier
        regions' PlanIt requests in the same run. A second call immediately
        after a first must wait out roughly the full configured minimum
        interval, not a fixed local throttle."""
        scanners._PLANIT_LAST_REQUEST_AT = 0.0
        with patch("time.sleep") as mock_sleep:
            scanners._planit_wait_for_slot()  # first call: nothing to wait out yet
            scanners._planit_wait_for_slot()  # second call: must wait ~ full interval
        slept_for = [c.args[0] for c in mock_sleep.call_args_list if c.args]
        self.assertEqual(len(slept_for), 1)
        self.assertAlmostEqual(slept_for[0], scanners.PLANIT_MIN_INTERVAL_SECONDS, delta=1.0)

    def test_planit_pacing_gate_is_shared_across_regions(self):
        """The gate is module-level/process-wide, not reset per
        scan_city_planning_api call -- a second region's PlanIt fetch,
        immediately after a first region's, must still wait out the shared
        interval. This is the specific behavior the previous per-region
        throttle lacked, and which let 16 back-to-back regions each reset
        their own local 1.5s clock and blow through PlanIt's real limit."""
        ok_response = self._fake_response(json_data={"records": []})
        with patch.object(scanners, "UK_PLANNING_API_KEY", ""), \
             patch("net_utils.smart_get", return_value=ok_response), \
             patch("database.get_db_conn", return_value=self.conn), \
             patch.object(scanners, "_insert_lead", return_value=None), \
             patch("time.sleep") as mock_sleep:
            scanners.scan_city_planning_api("Bristol")    # single-town region
            scanners.scan_city_planning_api("Sheffield")  # different single-town region

        slept_for = [c.args[0] for c in mock_sleep.call_args_list if c.args]
        # If the gate were still per-region-local, every wait here would be
        # ~0s (a fresh, unclaimed throttle for each region). A wait close to
        # the full shared interval proves the second region's request saw
        # the first region's claim on the pacing gate.
        self.assertTrue(any(s >= scanners.PLANIT_MIN_INTERVAL_SECONDS - 1.0 for s in slept_for))

    def test_confirms_agent_status_via_source_url_when_planit_gives_nothing(self):
        """Aug 30 2026: PlanIt's own field dictionary confirms it never
        stores real applicant/agent names -- but does return the original
        authority's own URL. When PlanIt itself gives no agent info, that
        URL must be followed via mesh_scrapers.confirm_agent_status_from_
        source to get a REAL confirmed answer instead of leaving the lead
        permanently unconfirmed."""
        planit_body = {
            "records": [{
                "uid": "24/05555/TPO",
                "description": "Felling of 1no. diseased ash tree, TPO protected.",
                "address": "9 Real Street, Bristol",
                "link": "https://www.planit.org.uk/planapplic/24-05555-tpo",
                "url": "https://planningonline.bristol.gov.uk/online-applications/applicationDetails.do?keyVal=REALSRC1&activeTab=summary",
                "other_fields": {},  # PlanIt itself has nothing -- not even a placeholder
            }]
        }
        idox_details_html = """
        <html><body><table>
        <tr><th scope="row">Applicant Name</th><td>Mr Real Homeowner</td></tr>
        </table></body></html>
        """  # no agent row at all -- a genuine, confirmed "no agent"

        planit_resp = self._fake_response(json_data=planit_body)
        idox_resp = MagicMock(status_code=200, text=idox_details_html)
        # Aug 30 2026: the confirmation step now does a cheap DB lookup
        # first ("has this reference already been resolved on a previous
        # day?") before ever spending a real HTTP request -- fetchone()
        # must explicitly report "never seen before" (None), or a bare
        # MagicMock's default truthy return would look like an
        # already-resolved row and skip confirmation entirely.
        self.cur.fetchone.return_value = None

        with patch.object(scanners, "UK_PLANNING_API_KEY", ""), \
             patch("net_utils.smart_get", side_effect=[planit_resp, idox_resp]), \
             patch("database.get_db_conn", return_value=self.conn), \
             patch.object(scanners, "_insert_lead", return_value=None) as mock_insert, \
             patch("time.sleep", return_value=None):
            scanners.scan_city_planning_api("Bristol")

        _, kwargs = mock_insert.call_args
        self.assertEqual(kwargs["applicant_name"], "Mr Real Homeowner")
        self.assertFalse(kwargs["has_agent"])  # a REAL confirmed no, not None/unconfirmed

    def test_confirmation_skipped_when_already_resolved_in_db(self):
        """The other half of the same fix: if a PREVIOUS day's scan already
        resolved this exact reference, today's re-encounter (PlanIt keeps
        returning the same still-live application for up to 45 days) must
        NOT spend another real HTTP request confirming it again -- that
        would mean re-confirming every known lead forever, once per day,
        for as long as it stays in PlanIt's window. has_agent=False here
        (a real confirmed "no agent"), so there's nothing left to classify
        either -- agent_is_tree_surgeon should stay None."""
        planit_body = {
            "records": [{
                "uid": "24/07777/TPO",
                "description": "Felling of 1no. diseased ash tree, TPO protected.",
                "address": "1 Previously Resolved Rd, Bristol",
                "other_fields": {},
                "url": "https://planningonline.bristol.gov.uk/online-applications/applicationDetails.do?keyVal=ALREADYKNOWN&activeTab=summary",
            }]
        }
        # (has_agent, applicant_name, agent_name, agent_company, agent_is_tree_surgeon)
        self.cur.fetchone.return_value = (False, "Mr Previously Confirmed", None, None, None)

        with patch.object(scanners, "UK_PLANNING_API_KEY", ""), \
             patch("net_utils.smart_get", return_value=self._fake_response(json_data=planit_body)), \
             patch("database.get_db_conn", return_value=self.conn), \
             patch.object(scanners, "_insert_lead", return_value=None) as mock_insert, \
             patch("mesh_scrapers.confirm_agent_status_from_source") as mock_confirm, \
             patch("time.sleep", return_value=None):
            scanners.scan_city_planning_api("Bristol")

        mock_confirm.assert_not_called()
        _, kwargs = mock_insert.call_args
        self.assertFalse(kwargs["has_agent"])
        self.assertIsNone(kwargs["agent_is_tree_surgeon"])
        self.assertEqual(kwargs["applicant_name"], "Mr Previously Confirmed")

    def test_already_resolved_has_agent_gets_classified_with_no_new_network_call(self):
        """Regression test for a real bug found live in a production
        export: 187 leads sitting at has_agent=True with
        agent_is_tree_surgeon still NULL, permanently excluded from the
        marketplace (get_marketplace_leads_with_freshness treats a NULL
        classification the same as a confirmed tree surgeon -- excluded
        either way). Root cause -- this same "already resolved, skip"
        check only ever looked at has_agent, so once has_agent was
        resolved (e.g. before agent_is_tree_surgeon existed as a field at
        all) it skipped re-checking forever, and agent_is_tree_surgeon was
        never given a chance to be computed from the agent name/company
        already sitting right there in the same row. Fixed: when has_agent
        is already True but agent_is_tree_surgeon is still NULL, classify
        it from the on-file agent name/company -- pure string matching, no
        HTTP request -- instead of leaving it stuck at NULL forever."""
        planit_body = {
            "records": [{
                "uid": "24/08888/TPO",
                "description": "Felling of 1no. diseased ash tree, TPO protected.",
                "address": "1 Stuck Unclassified Rd, Bristol",
                "other_fields": {},
                "url": "https://planningonline.bristol.gov.uk/online-applications/applicationDetails.do?keyVal=STUCKREF&activeTab=summary",
            }]
        }
        # has_agent already True, agent_company already on file, but
        # agent_is_tree_surgeon (last column) is still NULL -- exactly the
        # stuck state found in production.
        self.cur.fetchone.return_value = (True, None, None, "Bristol Tree Surgeons Ltd", None)

        with patch.object(scanners, "UK_PLANNING_API_KEY", ""), \
             patch("net_utils.smart_get", return_value=self._fake_response(json_data=planit_body)), \
             patch("database.get_db_conn", return_value=self.conn), \
             patch.object(scanners, "_insert_lead", return_value=None) as mock_insert, \
             patch("mesh_scrapers.confirm_agent_status_from_source") as mock_confirm, \
             patch("time.sleep", return_value=None):
            scanners.scan_city_planning_api("Bristol")

        mock_confirm.assert_not_called()  # zero new network requests -- pure classification from on-file data
        _, kwargs = mock_insert.call_args
        self.assertTrue(kwargs["has_agent"])
        self.assertEqual(kwargs["agent_company"], "Bristol Tree Surgeons Ltd")
        self.assertTrue(kwargs["agent_is_tree_surgeon"], "a real tree-surgeon company name must classify True, not stay stuck at None")

    def test_already_resolved_and_already_classified_is_left_alone(self):
        """When agent_is_tree_surgeon is already set (not NULL), the fix
        must not re-classify or otherwise touch it -- it's already a real,
        previously-computed answer."""
        planit_body = {
            "records": [{
                "uid": "24/09999/TPO",
                "description": "Felling of 1no. diseased ash tree, TPO protected.",
                "address": "1 Already Classified Rd, Bristol",
                "other_fields": {},
                "url": "https://planningonline.bristol.gov.uk/online-applications/applicationDetails.do?keyVal=ALREADYCLASSIFIED&activeTab=summary",
            }]
        }
        # Already classified False (e.g. an architect, not a tree surgeon)
        # on a previous run -- must be preserved exactly, not recomputed.
        self.cur.fetchone.return_value = (True, None, None, "DP Architecture", False)

        with patch.object(scanners, "UK_PLANNING_API_KEY", ""), \
             patch("net_utils.smart_get", return_value=self._fake_response(json_data=planit_body)), \
             patch("database.get_db_conn", return_value=self.conn), \
             patch.object(scanners, "_insert_lead", return_value=None) as mock_insert, \
             patch("mesh_scrapers.classify_agent_as_tree_surgeon") as mock_classify, \
             patch("mesh_scrapers.confirm_agent_status_from_source") as mock_confirm, \
             patch("time.sleep", return_value=None):
            scanners.scan_city_planning_api("Bristol")

        mock_confirm.assert_not_called()
        mock_classify.assert_not_called()
        _, kwargs = mock_insert.call_args
        self.assertFalse(kwargs["agent_is_tree_surgeon"])

    def test_confirmation_skipped_when_planit_already_gave_an_agent(self):
        """No need to spend an extra real HTTP request confirming something
        PlanIt already told us directly."""
        planit_body = {
            "records": [{
                "uid": "24/06666/TPO",
                "description": "Crown reduction of mature oak tree, TPO protected.",
                "address": "3 Already Known Ave, Bristol",
                "other_fields": {"agent_company": "Known Tree Surgeons Ltd"},
                "url": "https://planningonline.bristol.gov.uk/online-applications/applicationDetails.do?keyVal=SHOULDNOTFETCH&activeTab=summary",
            }]
        }
        with patch.object(scanners, "UK_PLANNING_API_KEY", ""), \
             patch("net_utils.smart_get", return_value=self._fake_response(json_data=planit_body)), \
             patch("database.get_db_conn", return_value=self.conn), \
             patch.object(scanners, "_insert_lead", return_value=None) as mock_insert, \
             patch("mesh_scrapers.confirm_agent_status_from_source") as mock_confirm, \
             patch("time.sleep", return_value=None):
            scanners.scan_city_planning_api("Bristol")

        mock_confirm.assert_not_called()
        _, kwargs = mock_insert.call_args
        self.assertTrue(kwargs["has_agent"])
        self.assertEqual(kwargs["agent_company"], "Known Tree Surgeons Ltd")

    def test_confirmation_attempts_are_capped_per_call(self):
        """Each confirmation is a real HTTP request straight to a council's
        own server -- PLANIT_AGENT_CONFIRM_LIMIT must actually bound how
        many of those one scan_city_planning_api() call will attempt."""
        planit_body = {
            "records": [
                {
                    "uid": f"24/0700{i}/TPO",
                    "description": "Felling of 1no. diseased ash tree, TPO protected.",
                    "address": f"{i} Budget Test Rd, Bristol",
                    "other_fields": {},
                    "url": f"https://planningonline.bristol.gov.uk/online-applications/applicationDetails.do?keyVal=BUDGET{i}&activeTab=summary",
                }
                for i in range(3)
            ]
        }
        self.cur.fetchone.return_value = None  # none of these 3 have been seen/resolved before

        with patch.object(scanners, "PLANIT_AGENT_CONFIRM_LIMIT", 1), \
             patch.object(scanners, "UK_PLANNING_API_KEY", ""), \
             patch("net_utils.smart_get", return_value=self._fake_response(json_data=planit_body)), \
             patch("database.get_db_conn", return_value=self.conn), \
             patch.object(scanners, "_insert_lead", return_value=None), \
             patch("mesh_scrapers.confirm_agent_status_from_source", return_value={}) as mock_confirm, \
             patch("time.sleep", return_value=None):
            scanners.scan_city_planning_api("Bristol")

        self.assertEqual(mock_confirm.call_count, 1)

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
             patch.dict(os.environ, {"PAID_API_ROTATION_DAYS": "1"}), \
             patch("net_utils.smart_get", return_value=resp), \
             patch("database.get_db_conn", return_value=self.conn), \
             patch.object(scanners, "_insert_lead", return_value={"ref": "24/1000/TPO"}) as mock_insert, \
             patch("time.sleep", return_value=None):
            count = scanners.scan_city_planning_api("Sheffield")

        self.assertEqual(count, 1)  # only the correctly-attributed Sheffield lead counts
        mock_insert.assert_called_once()
        args, _ = mock_insert.call_args
        self.assertIn("S1 2AB", args[2])  # the Tonbridge/Kent record was skipped

    def test_paid_api_429_is_logged_and_counted_as_failure_not_silent_zero(self):
        """Aug 30 2026: this used to be worse than PlanIt's 429 bug -- a 429
        from ukplanningapi.co.uk was deliberately carved out of the `elif`
        chain so it was never logged AND never added to paid_failures,
        meaning it couldn't even trip the "failed for ALL prefixes" aggregate
        warning. A free-tier key hitting its 500/month cap would silently
        look exactly like a genuine zero-results run, indistinguishable in
        the logs from a real empty scan, for the rest of the month. Must now
        be a visible, real, aggregatable failure -- not a retry (a monthly
        quota can't be fixed by waiting a few seconds, unlike PlanIt's
        burst limit)."""
        rate_limited = MagicMock(status_code=429)

        with patch.object(scanners, "UK_PLANNING_API_KEY", "fake-key"), \
             patch.object(scanners, "CITY_POSTCODE_PREFIX", {"Sheffield": ["S"]}), \
             patch.object(scanners, "REGION_TOWNS", {}), \
             patch.dict(os.environ, {"PAID_API_ROTATION_DAYS": "1"}), \
             patch("net_utils.smart_get", return_value=rate_limited), \
             patch("database.get_db_conn", return_value=self.conn), \
             patch.object(scanners, "_insert_lead") as mock_insert, \
             patch("time.sleep", return_value=None), \
             patch.object(scanners, "logger") as mock_logger:
            count = scanners.scan_city_planning_api("Sheffield")

        self.assertEqual(count, 0)
        mock_insert.assert_not_called()
        warning_texts = [c.args[0] for c in mock_logger.warning.call_args_list if c.args]
        # The per-prefix 429 must be logged (previously silently excluded)...
        self.assertTrue(any("429" in t and "quota" in t.lower() for t in warning_texts))
        # ...AND counted, so the aggregate "failed for ALL" warning can fire.
        self.assertTrue(any("ukplanningapi.co.uk failed for ALL" in t for t in warning_texts))


class TestPaidApiRotationAndDedup(unittest.TestCase):
    """The Aug 30 2026 quota-headroom fix: Nick hit ukplanningapi.co.uk's
    500/month free-tier cap last week because scan_city_planning_api()
    queried every postcode prefix in every region every single day
    (~178/day, exhausting 500/month in under 3 days). Instead of querying
    every prefix every day, it now rotates through a subset each day
    (PAID_API_ROTATION_DAYS, default 12) and skips a second paid-API pass
    for the same region on the same calendar day, so a manual re-trigger
    on a heavy testing/development day doesn't multiply quota usage for
    zero new coverage."""

    def setUp(self):
        self.cur = MagicMock()
        self.conn = MagicMock()
        self.conn.cursor.return_value = self.cur
        dedup_patch = patch.object(scanners, "dedup", new=_FakeDedupStore())
        dedup_patch.start()
        self.addCleanup(dedup_patch.stop)

    def _fake_response(self, status_code=200, json_data=None):
        resp = MagicMock(status_code=status_code)
        resp.json.return_value = json_data if json_data is not None else {"data": []}
        return resp

    def test_rotation_only_queries_a_subset_of_prefixes_on_a_given_day(self):
        import datetime as real_datetime
        prefixes = ["A", "B", "C", "D", "E", "F"]  # 6 prefixes, period 3 -> 2/day

        class FixedDate(real_datetime.date):
            @classmethod
            def today(cls):
                return cls(2026, 1, 4)

        expected_day_index = FixedDate(2026, 1, 4).toordinal() % 3
        expected_prefixes = sorted(p for i, p in enumerate(prefixes) if i % 3 == expected_day_index)

        with patch.object(scanners, "UK_PLANNING_API_KEY", "fake-key"), \
             patch.object(scanners, "CITY_POSTCODE_PREFIX", {"TestRegion": prefixes}), \
             patch.object(scanners, "REGION_TOWNS", {}), \
             patch.dict(os.environ, {"PAID_API_ROTATION_DAYS": "3"}), \
             patch("datetime.date", FixedDate), \
             patch("net_utils.smart_get", return_value=self._fake_response()) as mock_get, \
             patch("database.get_db_conn", return_value=self.conn), \
             patch.object(scanners, "_insert_lead", return_value=None), \
             patch("time.sleep", return_value=None):
            scanners.scan_city_planning_api("TestRegion")

        queried = sorted(c.kwargs["params"]["postcode"] for c in mock_get.call_args_list)
        self.assertEqual(queried, expected_prefixes)
        self.assertLess(len(queried), len(prefixes))  # confirms rotation actually narrowed it

    def test_rotation_disabled_queries_every_prefix_every_day(self):
        """PAID_API_ROTATION_DAYS=1 must restore the old behaviour exactly
        -- e.g. for a future paid tier with enough headroom that pacing is
        no longer needed."""
        prefixes = ["A", "B", "C", "D"]

        with patch.object(scanners, "UK_PLANNING_API_KEY", "fake-key"), \
             patch.object(scanners, "CITY_POSTCODE_PREFIX", {"TestRegion": prefixes}), \
             patch.object(scanners, "REGION_TOWNS", {}), \
             patch.dict(os.environ, {"PAID_API_ROTATION_DAYS": "1"}), \
             patch("net_utils.smart_get", return_value=self._fake_response()) as mock_get, \
             patch("database.get_db_conn", return_value=self.conn), \
             patch.object(scanners, "_insert_lead", return_value=None), \
             patch("time.sleep", return_value=None):
            scanners.scan_city_planning_api("TestRegion")

        self.assertEqual(mock_get.call_count, len(prefixes))

    def test_same_day_retrigger_skips_the_paid_api_the_second_time(self):
        """Directly the scenario Nick flagged: multiple manual triggers on
        one development day must not multiply quota usage for the same
        region -- only the first pass that day should hit the network."""
        prefixes = ["A", "B"]

        with patch.object(scanners, "UK_PLANNING_API_KEY", "fake-key"), \
             patch.object(scanners, "CITY_POSTCODE_PREFIX", {"TestRegion": prefixes}), \
             patch.object(scanners, "REGION_TOWNS", {}), \
             patch.dict(os.environ, {"PAID_API_ROTATION_DAYS": "1"}), \
             patch("net_utils.smart_get", return_value=self._fake_response()) as mock_get, \
             patch("database.get_db_conn", return_value=self.conn), \
             patch.object(scanners, "_insert_lead", return_value=None), \
             patch("time.sleep", return_value=None):
            scanners.scan_city_planning_api("TestRegion")  # first trigger today
            first_call_count = mock_get.call_count
            scanners.scan_city_planning_api("TestRegion")  # second trigger, same day

        self.assertEqual(first_call_count, len(prefixes))
        self.assertEqual(mock_get.call_count, first_call_count)  # no extra calls on retrigger

    def test_usage_tracking_counts_only_todays_rotated_subset(self):
        """increment_api_usage must be told how many prefixes were
        ACTUALLY queried today (post-rotation), not the region's full
        prefix list -- otherwise the usage tracker (and its predictive
        quota-breach warning email) would think the full list runs every
        day, defeating the entire point of rotating in the first place."""
        prefixes = ["A", "B", "C", "D"]

        with patch.object(scanners, "UK_PLANNING_API_KEY", "fake-key"), \
             patch.object(scanners, "CITY_POSTCODE_PREFIX", {"TestRegion": prefixes}), \
             patch.object(scanners, "REGION_TOWNS", {}), \
             patch.dict(os.environ, {"PAID_API_ROTATION_DAYS": "2"}), \
             patch("net_utils.smart_get", return_value=self._fake_response()), \
             patch("database.get_db_conn", return_value=self.conn), \
             patch.object(scanners, "_insert_lead", return_value=None), \
             patch("database.increment_api_usage", return_value={"warning_needed": False}) as mock_usage, \
             patch("time.sleep", return_value=None):
            scanners.scan_city_planning_api("TestRegion")

        mock_usage.assert_called_once()
        _, kwargs = mock_usage.call_args
        self.assertEqual(kwargs["increment"], 2)  # half of 4 (rotation period 2), not all 4


class TestMeshScanSameDayDedup(unittest.TestCase):
    """run_mesh_network_scan() -- Aug 30 2026 same-day dedup, prompted
    directly by Nick noticing that troubleshooting/manual re-triggers each
    separately re-scraped all 50+ real council websites in
    COUNCIL_REGISTRY again on the same day. Unlike the paid-API rotation
    (a money quota), this is about not hammering other people's free
    council government servers with a full sweep every time the pipeline
    gets manually kicked off. Must run the full sweep at most once per
    calendar day."""

    def setUp(self):
        self.cur = MagicMock()
        self.conn = MagicMock()
        self.conn.cursor.return_value = self.cur
        dedup_patch = patch.object(scanners, "dedup", new=_FakeDedupStore())
        dedup_patch.start()
        self.addCleanup(dedup_patch.stop)

    def test_first_call_today_runs_the_full_sweep(self):
        fake_registry = {"Testville": "https://example-council.gov.uk/online-applications"}
        with patch.object(mesh_scrapers, "COUNCIL_REGISTRY", fake_registry), \
             patch.object(mesh_scrapers, "scrape_mesh_council", return_value=[]) as mock_scrape, \
             patch("database.get_db_conn", return_value=self.conn), \
             patch("time.sleep", return_value=None):
            scanners.run_mesh_network_scan()

        mock_scrape.assert_called_once_with("Testville")

    def test_same_day_retrigger_skips_the_sweep_entirely(self):
        fake_registry = {"Testville": "https://example-council.gov.uk/online-applications"}
        with patch.object(mesh_scrapers, "COUNCIL_REGISTRY", fake_registry), \
             patch.object(mesh_scrapers, "scrape_mesh_council", return_value=[]) as mock_scrape, \
             patch("database.get_db_conn", return_value=self.conn), \
             patch("time.sleep", return_value=None):
            scanners.run_mesh_network_scan()            # first trigger today
            first_call_count = mock_scrape.call_count
            result = scanners.run_mesh_network_scan()   # second trigger, same day

        self.assertEqual(first_call_count, 1)
        self.assertEqual(mock_scrape.call_count, first_call_count)  # no re-scraping
        self.assertEqual(result, 0)


class TestGlaDatahubLondon(unittest.TestCase):
    """scan_gla_datahub_london() -- extracted Aug 30 2026 out of the legacy
    scan_london_leads() function, which was found (while chasing Nick's
    recalled "use multiple free planning sites to spread out the request
    caps" strategy) to never actually be called anywhere in the scheduled
    daily pipeline -- only from three manual/admin-triggered endpoints.
    This free GLA Planning Datahub source is now called directly from
    Stage 1 for the London region, additionally to the existing
    scan_city_planning_api("London") call."""

    def setUp(self):
        self.cur = MagicMock()
        self.conn = MagicMock()
        self.conn.cursor.return_value = self.cur
        # Fresh fake dedup store per test -- see _FakeDedupStore docstring.
        dedup_patch = patch.object(scanners, "dedup", new=_FakeDedupStore())
        dedup_patch.start()
        self.addCleanup(dedup_patch.stop)

    def test_returns_zero_without_crashing_when_key_not_configured(self):
        """If GLA_API_KEY isn't set in Render, this must be a harmless no-op
        (not an exception, not a wasted DB connection) -- Stage 1 calls this
        unconditionally for London every day regardless of whether the key
        is configured."""
        with patch.object(scanners, "GLA_API_KEY", ""), \
             patch("database.get_db_conn") as mock_get_conn:
            count = scanners.scan_gla_datahub_london()

        self.assertEqual(count, 0)
        mock_get_conn.assert_not_called()

    def test_valid_response_inserts_only_tree_related_leads(self):
        body = {
            "data": [
                {"reference": "GLA-1", "description": "Crown reduction of protected oak tree, TPO.",
                 "location": {"address": "1 Borough High St, London"}},
                {"reference": "GLA-2", "description": "Change of use to former bank branch.",
                 "location": {"address": "2 Borough High St, London"}},  # not tree-related
            ]
        }
        resp = MagicMock(status_code=200)
        resp.json.return_value = body

        with patch.object(scanners, "GLA_API_KEY", "fake-gla-key"), \
             patch("net_utils.smart_get", return_value=resp), \
             patch("database.get_db_conn", return_value=self.conn), \
             patch.object(scanners, "_insert_lead", return_value={"ref": "GLA-1"}) as mock_insert, \
             patch("time.sleep", return_value=None):
            count = scanners.scan_gla_datahub_london()

        self.assertEqual(count, 1)
        mock_insert.assert_called_once()
        self.conn.commit.assert_called_once()

    def test_one_malformed_record_does_not_lose_the_rest_of_the_batch(self):
        """Sep 2 2026: an adversarial review pass caught that a truthy
        non-string value in any of the description fields (e.g. a nested
        dict from a genuinely messy upstream API response) used to raise
        AttributeError out of .strip() before this per-item try/except and
        str() coercion existed -- which crashed the whole loop partway
        through and, worse, this function's own dedup marker is set BEFORE
        the fetch (see mark_done_today above), so the rest of that day's
        London batch would have been lost until tomorrow. Proves: the bad
        record is skipped, the good one right after it still gets inserted,
        and the whole batch still commits."""
        body = {
            "data": [
                {"reference": "GLA-BAD", "description": {"nested": "not a string"},
                 "location": {"address": "Bad Record House, London"}},
                {"reference": "GLA-GOOD", "description": "Crown reduction of protected oak tree, TPO.",
                 "location": {"address": "1 Borough High St, London"}},
            ]
        }
        resp = MagicMock(status_code=200)
        resp.json.return_value = body

        with patch.object(scanners, "GLA_API_KEY", "fake-gla-key"), \
             patch("net_utils.smart_get", return_value=resp), \
             patch("database.get_db_conn", return_value=self.conn), \
             patch.object(scanners, "_insert_lead", return_value={"ref": "GLA-GOOD"}) as mock_insert, \
             patch("time.sleep", return_value=None):
            count = scanners.scan_gla_datahub_london()

        self.assertEqual(count, 1)
        mock_insert.assert_called_once()
        self.assertEqual(mock_insert.call_args.args[1], "GLA-GOOD")
        self.conn.commit.assert_called_once()

    def test_hmo_only_lead_is_inserted_with_hmo_vertical_not_discarded(self):
        """Before _resolve_vertical existed, an HMO-only application failed
        the bare _is_tree_related check here and was silently skipped --
        never reached _insert_lead at all. This is new, additive pipeline
        output, not a change to anything already flowing."""
        body = {
            "data": [
                {"reference": "GLA-HMO-1",
                 "description": "Change of use to a house in multiple occupation (7 persons).",
                 "location": {"address": "3 Borough High St, London"}},
            ]
        }
        resp = MagicMock(status_code=200)
        resp.json.return_value = body

        with patch.object(scanners, "GLA_API_KEY", "fake-gla-key"), \
             patch("net_utils.smart_get", return_value=resp), \
             patch("database.get_db_conn", return_value=self.conn), \
             patch.object(scanners, "_insert_lead", return_value={"ref": "GLA-HMO-1"}) as mock_insert, \
             patch("time.sleep", return_value=None):
            count = scanners.scan_gla_datahub_london()

        self.assertEqual(count, 1)
        mock_insert.assert_called_once()
        _, kwargs = mock_insert.call_args
        self.assertEqual(kwargs.get("vertical"), "hmo")

    def test_401_triggers_critical_alert_not_a_crash(self):
        resp = MagicMock(status_code=401)

        with patch.object(scanners, "GLA_API_KEY", "stale-key"), \
             patch("net_utils.smart_get", return_value=resp), \
             patch("database.get_db_conn", return_value=self.conn), \
             patch("time.sleep", return_value=None), \
             patch("notifications.send_system_incident_alert") as mock_alert:
            count = scanners.scan_gla_datahub_london()

        self.assertEqual(count, 0)
        mock_alert.assert_called_once()
        self.assertEqual(mock_alert.call_args.kwargs["severity"], "CRITICAL")

    def test_scan_london_leads_still_includes_gla_count_in_its_total(self):
        """scan_london_leads() (the legacy manual-trigger entry point) must
        still report GLA-sourced leads in its own return value, and now
        delegates its postcode-radar coverage entirely to
        scan_city_planning_api("London") -- Aug 30 2026: this used to carry
        its own duplicate, unrotated, undeduped copy of the same 29
        prefixes, which meant a manual trigger of scan_london_leads()
        completely bypassed the quota-headroom fixes added to
        scan_city_planning_api(). Delegating closes that gap."""
        with patch.object(scanners, "scan_gla_datahub_london", return_value=3), \
             patch.object(scanners, "scan_city_planning_api", return_value=7) as mock_radar:
            total = scanners.scan_london_leads()

        mock_radar.assert_called_once_with("London")
        self.assertEqual(total, 10)  # 3 from GLA + 7 from the delegated radar call

    def test_same_day_retrigger_skips_the_gla_fetch(self):
        resp = MagicMock(status_code=200)
        resp.json.return_value = {"data": []}

        with patch.object(scanners, "GLA_API_KEY", "fake-gla-key"), \
             patch("net_utils.smart_get", return_value=resp) as mock_get, \
             patch("database.get_db_conn", return_value=self.conn), \
             patch("time.sleep", return_value=None):
            scanners.scan_gla_datahub_london()   # first trigger today
            first_call_count = mock_get.call_count
            result = scanners.scan_gla_datahub_london()  # second trigger, same day

        self.assertEqual(first_call_count, 1)
        self.assertEqual(mock_get.call_count, first_call_count)  # no second HTTP call
        self.assertEqual(result, 0)


class TestVerticalWiringPaidApiAndPlanit(unittest.TestCase):
    """Sep 2 2026: proves scan_city_planning_api's two data sources (the
    paid ukplanningapi.co.uk loop and the free PlanIt loop) now tag each
    inserted lead with _resolve_vertical's pick instead of the old bare
    _is_tree_related gate, which silently discarded every HMO-only
    application before this vertical existed -- these are genuinely new,
    additive leads, not a change to anything already flowing. Per the Sep 1
    traffic-scaling discussion: both sources already fetch broadly per
    postcode-prefix/authority with no keyword filter sent to the API
    itself, so this wiring costs zero additional API traffic -- it's a
    second classification pass over data already being fetched for the
    tree vertical."""

    def setUp(self):
        self.cur = MagicMock()
        self.conn = MagicMock()
        self.conn.cursor.return_value = self.cur
        dedup_patch = patch.object(scanners, "dedup", new=_FakeDedupStore())
        dedup_patch.start()
        self.addCleanup(dedup_patch.stop)
        scanners._PLANIT_LAST_REQUEST_AT = 0.0

    def test_paid_api_hmo_only_lead_gets_hmo_vertical_not_discarded(self):
        body = {
            "data": [
                {"reference": "24/HMO/1",
                 "description": "Change of use to a house in multiple occupation (7 persons).",
                 "address": "10 Example Road, Sheffield S1 2AB"},
            ]
        }
        resp = MagicMock(status_code=200)
        resp.json.return_value = body

        with patch.object(scanners, "UK_PLANNING_API_KEY", "fake-key"), \
             patch.object(scanners, "CITY_POSTCODE_PREFIX", {"Sheffield": ["S"]}), \
             patch.object(scanners, "REGION_TOWNS", {}), \
             patch.dict(os.environ, {"PAID_API_ROTATION_DAYS": "1"}), \
             patch("net_utils.smart_get", return_value=resp), \
             patch("database.get_db_conn", return_value=self.conn), \
             patch.object(scanners, "_insert_lead", return_value={"ref": "24/HMO/1"}) as mock_insert, \
             patch("time.sleep", return_value=None):
            count = scanners.scan_city_planning_api("Sheffield")

        self.assertEqual(count, 1)
        mock_insert.assert_called_once()
        _, kwargs = mock_insert.call_args
        self.assertEqual(kwargs.get("vertical"), "hmo")

    def test_paid_api_tree_lead_still_gets_tree_vertical(self):
        body = {
            "data": [
                {"reference": "24/TREE/1", "description": "Felling of 2no. ash trees, TPO protected.",
                 "address": "10 Example Road, Sheffield S1 2AB"},
            ]
        }
        resp = MagicMock(status_code=200)
        resp.json.return_value = body

        with patch.object(scanners, "UK_PLANNING_API_KEY", "fake-key"), \
             patch.object(scanners, "CITY_POSTCODE_PREFIX", {"Sheffield": ["S"]}), \
             patch.object(scanners, "REGION_TOWNS", {}), \
             patch.dict(os.environ, {"PAID_API_ROTATION_DAYS": "1"}), \
             patch("net_utils.smart_get", return_value=resp), \
             patch("database.get_db_conn", return_value=self.conn), \
             patch.object(scanners, "_insert_lead", return_value={"ref": "24/TREE/1"}) as mock_insert, \
             patch("time.sleep", return_value=None):
            count = scanners.scan_city_planning_api("Sheffield")

        self.assertEqual(count, 1)
        _, kwargs = mock_insert.call_args
        self.assertEqual(kwargs.get("vertical"), "tree")

    def test_planit_hmo_only_lead_gets_hmo_vertical_not_discarded(self):
        planit_body = {
            "records": [
                {
                    "uid": "26/HMO/1",
                    "description": "Conversion to a small HMO for 4 unrelated sharers.",
                    "address": "9 Ivy Road, Bristol",
                    "other_fields": {},
                }
            ]
        }
        resp = MagicMock(status_code=200)
        resp.json.return_value = planit_body

        with patch.object(scanners, "UK_PLANNING_API_KEY", ""), \
             patch("net_utils.smart_get", return_value=resp), \
             patch("database.get_db_conn", return_value=self.conn), \
             patch.object(scanners, "_insert_lead", return_value={"ref": "26/HMO/1"}) as mock_insert, \
             patch("mesh_scrapers.confirm_agent_status_from_source", return_value={}), \
             patch("time.sleep", return_value=None):
            count = scanners.scan_city_planning_api("Bristol")

        self.assertEqual(count, 1)
        mock_insert.assert_called_once()
        _, kwargs = mock_insert.call_args
        self.assertEqual(kwargs.get("vertical"), "hmo")

    def test_paid_api_one_malformed_record_does_not_lose_the_rest_of_the_batch(self):
        """Sep 2 2026: an adversarial review pass caught that a truthy
        non-string "description" (e.g. a nested dict from a genuinely messy
        upstream API response) used to raise AttributeError inside
        _resolve_vertical's .lower() call, uncaught, which crashed this
        whole loop partway through -- and since this loop only
        conn.commit()s once, at the very end, every lead already inserted
        earlier in the SAME run would have been implicitly rolled back too.
        Proves: the bad record is skipped, the good one right after it
        still gets inserted, and the whole batch still commits."""
        body = {
            "data": [
                {"reference": "24/BAD/1", "description": {"nested": "not a string"},
                 "address": "10 Example Road, Sheffield S1 2AB"},
                {"reference": "24/GOOD/1", "description": "Felling of 2no. ash trees, TPO protected.",
                 "address": "10 Example Road, Sheffield S1 2AB"},
            ]
        }
        resp = MagicMock(status_code=200)
        resp.json.return_value = body

        with patch.object(scanners, "UK_PLANNING_API_KEY", "fake-key"), \
             patch.object(scanners, "CITY_POSTCODE_PREFIX", {"Sheffield": ["S"]}), \
             patch.object(scanners, "REGION_TOWNS", {}), \
             patch.dict(os.environ, {"PAID_API_ROTATION_DAYS": "1"}), \
             patch("net_utils.smart_get", return_value=resp), \
             patch("database.get_db_conn", return_value=self.conn), \
             patch.object(scanners, "_insert_lead", return_value={"ref": "24/GOOD/1"}) as mock_insert, \
             patch("time.sleep", return_value=None):
            count = scanners.scan_city_planning_api("Sheffield")

        self.assertEqual(count, 1)
        mock_insert.assert_called_once()
        self.assertEqual(mock_insert.call_args.args[1], "24/GOOD/1")
        self.conn.commit.assert_called_once()

    def test_planit_one_malformed_record_does_not_lose_the_rest_of_the_batch(self):
        """Same fix, same reasoning as the paid-API version above, applied
        to the PlanIt loop -- a distinct crash site (PlanIt has its own
        `summary = item.get("description", "") or ""` extraction and its
        own single conn.commit() at the very end of both loops combined)."""
        planit_body = {
            "records": [
                {"uid": "26/BAD/1", "description": {"nested": "not a string"},
                 "address": "9 Ivy Road, Bristol", "other_fields": {}},
                {"uid": "26/GOOD/1", "description": "Conversion to a small HMO for 4 unrelated sharers.",
                 "address": "9 Ivy Road, Bristol", "other_fields": {}},
            ]
        }
        resp = MagicMock(status_code=200)
        resp.json.return_value = planit_body

        with patch.object(scanners, "UK_PLANNING_API_KEY", ""), \
             patch("net_utils.smart_get", return_value=resp), \
             patch("database.get_db_conn", return_value=self.conn), \
             patch.object(scanners, "_insert_lead", return_value={"ref": "26/GOOD/1"}) as mock_insert, \
             patch("mesh_scrapers.confirm_agent_status_from_source", return_value={}), \
             patch("time.sleep", return_value=None):
            count = scanners.scan_city_planning_api("Bristol")

        self.assertEqual(count, 1)
        mock_insert.assert_called_once()
        self.assertEqual(mock_insert.call_args.args[1], "26/GOOD/1")
        self.conn.commit.assert_called_once()

    def test_planit_lead_matching_both_verticals_resolves_to_tree(self):
        planit_body = {
            "records": [
                {
                    "uid": "26/BOTH/1",
                    "description": "Conversion to a house in multiple occupation including "
                                    "felling of 1no. protected oak tree.",
                    "address": "12 Ivy Road, Bristol",
                    "other_fields": {},
                }
            ]
        }
        resp = MagicMock(status_code=200)
        resp.json.return_value = planit_body

        with patch.object(scanners, "UK_PLANNING_API_KEY", ""), \
             patch("net_utils.smart_get", return_value=resp), \
             patch("database.get_db_conn", return_value=self.conn), \
             patch.object(scanners, "_insert_lead", return_value={"ref": "26/BOTH/1"}) as mock_insert, \
             patch("mesh_scrapers.confirm_agent_status_from_source", return_value={}), \
             patch("time.sleep", return_value=None):
            count = scanners.scan_city_planning_api("Bristol")

        self.assertEqual(count, 1)
        _, kwargs = mock_insert.call_args
        self.assertEqual(kwargs.get("vertical"), "tree")


class TestVerticalWiringLeeds(unittest.TestCase):
    """Sep 2 2026: proves the Leeds ArcGIS scan (part 1 of scan_leeds_leads,
    separate from the delegated scan_city_planning_api part covered by
    TestLeedsScanDelegation below) now tags each inserted lead with the
    vertical _resolve_vertical picked, instead of silently discarding every
    HMO-only application the way the old bare _is_tree_related check did."""

    def setUp(self):
        self.cur = MagicMock()
        self.conn = MagicMock()
        self.conn.cursor.return_value = self.cur

    def test_hmo_only_feature_is_inserted_with_hmo_vertical_not_discarded(self):
        arcgis_resp = MagicMock(status_code=200)
        arcgis_resp.json.return_value = {"features": [
            {"attributes": {
                "DESCRIPTION": "Change of use to a house in multiple occupation (7 persons).",
                "REFERENCE": "LDS/HMO/1", "ADDRESS": "1 Kirkgate, Leeds",
            }},
            {"attributes": {
                "DESCRIPTION": "Felling of 2no. diseased ash trees",
                "REFERENCE": "LDS/TREE/1", "ADDRESS": "2 Kirkgate, Leeds",
            }},
            {"attributes": {
                "DESCRIPTION": "Erection of a two-storey rear extension.",
                "REFERENCE": "LDS/JUNK/1", "ADDRESS": "3 Kirkgate, Leeds",
            }},
        ]}

        with patch("net_utils.smart_get", return_value=arcgis_resp), \
             patch("database.get_db_conn", return_value=self.conn), \
             patch.object(scanners, "scan_city_planning_api", return_value=0), \
             patch.object(scanners, "_insert_lead", return_value=None) as mock_insert:
            scanners.scan_leeds_leads()

        self.assertEqual(mock_insert.call_count, 2)  # HMO + tree inserted, junk skipped entirely
        by_ref = {c.args[1]: c.kwargs.get("vertical") for c in mock_insert.call_args_list}
        self.assertEqual(by_ref["LDS/HMO/1"], "hmo")
        self.assertEqual(by_ref["LDS/TREE/1"], "tree")


class TestLeedsScanDelegation(unittest.TestCase):
    """scan_leeds_leads() -- same Aug 30 2026 gap as scan_london_leads()
    (found while checking "will that cover it though?"): part 2 used to
    carry its own duplicate, unrotated, undeduped copy of the Yorkshire
    postcode prefixes queried directly against ukplanningapi.co.uk. Since
    this function is only reachable via manual/admin endpoints, every
    manual trigger completely bypassed the quota-headroom fixes. Now
    delegates to scan_city_planning_api("Leeds") instead."""

    def setUp(self):
        self.cur = MagicMock()
        self.conn = MagicMock()
        self.conn.cursor.return_value = self.cur

    def test_yorkshire_radar_is_delegated_to_scan_city_planning_api(self):
        arcgis_resp = MagicMock(status_code=200)
        arcgis_resp.json.return_value = {"features": []}

        with patch("net_utils.smart_get", return_value=arcgis_resp), \
             patch("database.get_db_conn", return_value=self.conn), \
             patch.object(scanners, "scan_city_planning_api", return_value=5) as mock_radar:
            total = scanners.scan_leeds_leads()

        mock_radar.assert_called_once_with("Leeds")
        self.assertEqual(total, 5)  # 0 from ArcGIS (no features) + 5 delegated


class TestIsTreeTradeCompanyName(unittest.TestCase):
    """research.is_tree_trade_company_name (Aug 31 2026): a real production
    run of perform_research() enriched a psychology practice, a nursery, an
    IT company, a mortgage broker, a leaseholders' management company, and
    a padel court as "new tree surgery partners" -- all because the old
    bare `tree`-word fallback treated ANY company with "tree" somewhere in
    its name as sufficient evidence on its own. These are the exact
    real-world names (and a few genuine tree companies that must keep
    passing) that drove the fix."""

    def test_real_false_positives_from_production_are_now_excluded(self):
        false_positives = [
            "ACORN TREE PSYCHOLOGY AND CONSULTANCY SERVICES LTD",
            "YEW TREE COURT KINGSTON BAGPUIZE LIMITED",
            "APPLE TREE COURT (LEWISHAM) RTM COMPANY LIMITED",
            "HARINGEY TREE PROTECTORS LTD",
            "APPLE TREE CHILDREN'S SERVICES LIMITED",
            "APPLE TREE IT SERVICES LTD",
            "APPLE TREE MORTGAGE SERVICES LTD",
            "THE HERTFORDSHIRE PADEL TREE LTD",
        ]
        for name in false_positives:
            with self.subTest(name=name):
                self.assertFalse(research.is_tree_trade_company_name(name))

    def test_genuine_tree_trade_phrase_matches_are_kept(self):
        legit = [
            "ACE TREE SERVICES LTD",
            "ARGYLL FORESTRY FENCING LIMITED",
            "ARBORICULTURAL SERVICES TREEWORK LIMITED",
            "LUUX TREE SURGERY LIMITED",
            "ARBORTECH PROFESSIONAL TREE SERVICES LTD",
        ]
        for name in legit:
            with self.subTest(name=name):
                self.assertTrue(research.is_tree_trade_company_name(name))

    def test_trade_phrase_match_bypasses_the_exclusion_list(self):
        """A real arboricultural consultancy must not be thrown out just
        because 'consultancy' is also useful for catching the weak
        bare-tree-word false positives -- the trade phrase itself
        ('arboricultural') is specific enough to trust on its own."""
        self.assertTrue(research.is_tree_trade_company_name("XYZ ARBORICULTURAL CONSULTANCY LTD"))

    def test_weak_bare_tree_match_without_a_trade_phrase_still_requires_exclusion_check(self):
        """'JN Tree Consultancy' has no REQUIRED_PHRASES match (just the
        bare word 'tree' + 'consultancy') -- 'consultancy' is deliberately
        NOT in EXCLUDED_NAME_WORDS (see the comment in research.py) because
        this exact name is a real production example of a genuine tree
        consultancy, so it must still pass."""
        self.assertTrue(research.is_tree_trade_company_name("JN TREE CONSULTANCY"))
        self.assertTrue(research.is_tree_trade_company_name("THE BERKELEY TREE COMPANY LIMITED"))

    def test_ambiguous_weak_match_with_no_excluded_word_is_kept(self):
        """No trade phrase, no excluded word -- genuinely ambiguous small
        outfits (a huge fraction of real tree surgeons trade under a bare
        '<Name> Tree Ltd'/'<Name> Tree Services' style name) must still be
        allowed through rather than over-tightened into false negatives."""
        self.assertTrue(research.is_tree_trade_company_name("CHERRY TREE KENT LTD"))

    def test_no_tree_word_at_all_is_excluded(self):
        self.assertFalse(research.is_tree_trade_company_name("BLOGGS PLUMBING AND HEATING LTD"))


if __name__ == "__main__":
    unittest.main(verbosity=2)

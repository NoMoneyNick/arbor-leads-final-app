"""
test_research.py -- Unit tests for the partner tagging system in research.py
(business kind, director/phone/email sanity checks, contact:reachable vs
contact:dead). See test_scrapers.py's TestLeadTagging for the equivalent
lead-side tests, and test_database.py's TestPartnerTagQuerying for the
backfill/report side.

Sep 2 2026: Nick's call -- a partner with no working phone and no working
email is dead to the business exactly like an unclassified lead, and that
has to be a queryable, tagged fact rather than something only visible by
eyeballing NULL columns. This file only tests the pure classification
functions (_is_realistic_uk_phone, _is_realistic_email,
_classify_business_kind, _generate_partner_tags) -- no real network call,
no live database, no API keys required.

research.py imports `database` (needs psycopg2, a real Postgres driver) at
module load time. Same technique as test_scrapers.py: stub `database`,
`notifications`, and `dotenv` in sys.modules before importing research.py,
so this runs anywhere.
"""
import os
import sys
import types
import unittest
import unittest.mock as mock

if "database" not in sys.modules:
    _fake_database = types.ModuleType("database")
    _fake_database.get_db_conn = lambda *a, **k: None
    sys.modules["database"] = _fake_database

# Sep 2 2026: another test file imported earlier in the same run (e.g.
# test_scrapers.py) may have already stubbed `database` into sys.modules
# WITHOUT get_system_state/set_system_state, since the `if "database" not
# in sys.modules` guard above only fires for whichever file runs first --
# a pre-existing cross-file stub-sharing quirk in this test suite. Add
# these idempotently to whatever `database` module is already there
# (real or fake) instead of assuming this file's own stub won it, so the
# monthly-cap tests work regardless of import order / which files are run
# together.
if not hasattr(sys.modules["database"], "_fake_state_store"):
    _fake_state_store = {}
    sys.modules["database"].get_system_state = lambda key: _fake_state_store.get(key)
    def _fake_set_system_state(key, value):
        _fake_state_store[key] = value
        return True
    sys.modules["database"].set_system_state = _fake_set_system_state
    sys.modules["database"]._fake_state_store = _fake_state_store

if "notifications" not in sys.modules:
    _fake_notifications = types.ModuleType("notifications")
    sys.modules["notifications"] = _fake_notifications

if "dotenv" not in sys.modules:
    _fake_dotenv = types.ModuleType("dotenv")
    _fake_dotenv.load_dotenv = lambda *a, **k: None
    sys.modules["dotenv"] = _fake_dotenv

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import research  # noqa: E402


class TestRealisticPhoneCheck(unittest.TestCase):
    def test_accepts_plausible_uk_numbers(self):
        self.assertTrue(research._is_realistic_uk_phone("020 7946 0958"))
        self.assertTrue(research._is_realistic_uk_phone("07911 123456"))
        self.assertTrue(research._is_realistic_uk_phone("+44 20 7946 0958"))
        self.assertTrue(research._is_realistic_uk_phone("00 44 20 7946 0958"))

    def test_rejects_missing_and_malformed(self):
        self.assertFalse(research._is_realistic_uk_phone(None))
        self.assertFalse(research._is_realistic_uk_phone(""))
        self.assertFalse(research._is_realistic_uk_phone("12345"))  # too short
        self.assertFalse(research._is_realistic_uk_phone("020 7946 09581234"))  # too long

    def test_rejects_known_placeholder_and_repeated_digit_numbers(self):
        self.assertFalse(research._is_realistic_uk_phone("00000000000"))
        self.assertFalse(research._is_realistic_uk_phone("01234567890"))
        self.assertFalse(research._is_realistic_uk_phone("07000000000"))
        self.assertFalse(research._is_realistic_uk_phone("00111111111"))  # second digit 0


class TestRealisticEmailCheck(unittest.TestCase):
    def test_accepts_plausible_emails(self):
        self.assertTrue(research._is_realistic_email("info@bromleytreeservices.co.uk"))
        self.assertTrue(research._is_realistic_email("J.Smith@example-arb.com"))

    def test_rejects_missing_and_malformed(self):
        self.assertFalse(research._is_realistic_email(None))
        self.assertFalse(research._is_realistic_email(""))
        self.assertFalse(research._is_realistic_email("not-an-email"))
        self.assertFalse(research._is_realistic_email("missing-domain@"))

    def test_rejects_known_placeholder_domains(self):
        self.assertFalse(research._is_realistic_email("info@example.com"))
        self.assertFalse(research._is_realistic_email("contact@test.com"))
        self.assertFalse(research._is_realistic_email("hello@yourcompany.com"))


class TestClassifyBusinessKind(unittest.TestCase):
    def test_known_sic_divisions_map_to_expected_bucket(self):
        self.assertEqual(research._classify_business_kind(["81300"]), "landscaping-grounds-maintenance")
        self.assertEqual(research._classify_business_kind(["02100"]), "forestry-agriculture")
        self.assertEqual(research._classify_business_kind(["43999"]), "construction-specialist-trade")

    def test_first_matching_code_wins_when_multiple_present(self):
        self.assertEqual(research._classify_business_kind(["96090", "81300"]), "other-personal-service")

    def test_unknown_or_empty_sic_codes_are_unclassified_not_guessed(self):
        self.assertEqual(research._classify_business_kind([]), "unclassified")
        self.assertEqual(research._classify_business_kind(None), "unclassified")
        self.assertEqual(research._classify_business_kind(["99999"]), "unclassified")

    def test_decisive_company_name_overrides_sic_nicks_real_example(self):
        """Nick's own live example: AA GARDENING TREE SURGEONS LTD, company
        12026615, sits under SIC 91040 (Botanical and zoological gardens and
        nature reserves activities) -- nowhere near any SIC division this
        file maps. His rule: 'if a company has the words tree surgeon in
        their name they are always tree surgeons regardless of sic'. Name
        must win outright, not just break a tie."""
        self.assertEqual(
            research._classify_business_kind(["91040"], "AA GARDENING TREE SURGEONS LTD"),
            "tree-surgery"
        )
        # A decisive name with NO sic_codes at all must still win.
        self.assertEqual(
            research._classify_business_kind(None, "Acorn Tree Surgery Ltd"),
            "tree-surgery"
        )

    def test_non_decisive_name_still_falls_back_to_sic(self):
        """A name that doesn't clear is_tree_trade_company_name's bar
        (no REQUIRED_PHRASES match) must not spuriously trigger the
        override -- ordinary SIC-based classification still applies."""
        self.assertEqual(
            research._classify_business_kind(["81300"], "Sunnydale Grounds Ltd"),
            "landscaping-grounds-maintenance"
        )

    def test_guess_business_kind_soft_keywords_and_priority_order(self):
        """The 'third round' educated guess for a name that cleared neither
        the decisive name check nor any SIC division -- lower confidence,
        a separate tag from the confirmed classification."""
        self.assertEqual(research._guess_business_kind("Random Nature Reserve Ltd"), None)
        self.assertEqual(research._guess_business_kind("Sunnydale Landscaping Ltd"), "landscaping-grounds-maintenance")
        self.assertEqual(research._guess_business_kind("Oakwood Forestry Services Ltd"), "forestry-agriculture")
        # "Tree" keyword takes priority over a co-occurring landscaping word
        # -- resolves to the more tree-specific bucket, not an arbitrary one.
        self.assertEqual(research._guess_business_kind("Tree & Garden Services Ltd"), "tree-surgery")
        self.assertEqual(research._guess_business_kind(None), None)
        self.assertEqual(research._guess_business_kind(""), None)

    def test_generate_partner_tags_adds_business_guess_only_when_unclassified(self):
        """business_guess: must never appear alongside a CONFIRMED
        business:* classification -- it's strictly the fallback for the
        unclassified bucket, per _guess_business_kind's docstring."""
        confirmed_tags = research._generate_partner_tags(
            ["91040"], "Jane Smith", "020 7946 0958", None, company_name="AA GARDENING TREE SURGEONS LTD"
        )
        self.assertIn("business:tree-surgery", confirmed_tags)
        self.assertFalse(any(t.startswith("business_guess:") for t in confirmed_tags))

        guessed_tags = research._generate_partner_tags(
            ["99999"], "Jane Smith", "020 7946 0958", None, company_name="Sunnydale Landscaping Ltd"
        )
        self.assertIn("business:unclassified", guessed_tags)
        self.assertIn("business_guess:landscaping-grounds-maintenance", guessed_tags)

        no_signal_tags = research._generate_partner_tags(
            ["99999"], "Jane Smith", "020 7946 0958", None, company_name="Blueberry Holdings Ltd"
        )
        self.assertIn("business:unclassified", no_signal_tags)
        self.assertFalse(any(t.startswith("business_guess:") for t in no_signal_tags))

    def test_generate_partner_tags_without_company_name_keeps_old_behaviour(self):
        """company_name is optional/keyword specifically so every existing
        positional call site (and every pre-existing caller in this
        codebase before this fix) keeps working unchanged."""
        tags = research._generate_partner_tags(["99999"], "Jane Smith", "020 7946 0958", None)
        self.assertIn("business:unclassified", tags)


class TestGeneratePartnerTags(unittest.TestCase):
    def test_full_example_with_all_contact_details_present(self):
        tags = research._generate_partner_tags(
            ["81300"], "Jane Smith", "020 7946 0958", "jane@realtreecompany.co.uk"
        )
        self.assertIn("business:landscaping-grounds-maintenance", tags)
        self.assertIn("director:yes", tags)
        self.assertIn("phone:yes", tags)
        self.assertIn("email:yes", tags)
        self.assertIn("contact:reachable", tags)
        self.assertNotIn("contact:dead", tags)

    def test_reachable_via_phone_alone_is_not_dead(self):
        """Nick's rule is 'no phone AND no email' -- having just one real
        channel is enough to not be a dead partner."""
        tags = research._generate_partner_tags([], None, "020 7946 0958", None)
        self.assertIn("contact:reachable", tags)
        self.assertIn("email:no", tags)
        self.assertIn("director:no", tags)

    def test_no_phone_and_no_email_is_tagged_dead(self):
        tags = research._generate_partner_tags(["81300"], "Jane Smith", None, None)
        self.assertIn("contact:dead", tags)
        self.assertNotIn("contact:reachable", tags)

    def test_junk_phone_and_junk_email_still_count_as_dead(self):
        """A non-NULL column full of placeholder junk must not be mistaken
        for a real, reachable contact."""
        tags = research._generate_partner_tags([], None, "01234567890", "info@example.com")
        self.assertIn("phone:no", tags)
        self.assertIn("email:no", tags)
        self.assertIn("contact:dead", tags)

    def test_corporate_officer_name_is_not_a_director(self):
        """Sep 2 2026 audit fix: md_name coming back as another company's
        registered name (a corporate officer, not a person) must not be
        presented as 'the boss's name.'"""
        tags = research._generate_partner_tags([], "Acme Trustees Limited", "020 7946 0958", None)
        self.assertIn("director:no", tags)
        self.assertNotIn("director:yes", tags)


class TestRealisticPersonNameCheck(unittest.TestCase):
    """Sep 2 2026: added during the 'don't trust anything inherited,
    verify it' audit Nick asked for after the region-tag mislabeling was
    found. get_director_from_ch's own fallback loop used to hand back a
    corporate officer's company name as if it were a person, and a blank
    or single-word placeholder value was truthy enough to pass the old
    'if md_name' check. This is the same shape of bug as the region
    issue: a value inherited from an external system was trusted at face
    value instead of independently sanity-checked."""

    def test_accepts_plausible_full_names(self):
        self.assertTrue(research._is_realistic_person_name("Jane Smith"))
        self.assertTrue(research._is_realistic_person_name("Mohammed Al-Farsi"))

    def test_rejects_missing_blank_and_single_word(self):
        self.assertFalse(research._is_realistic_person_name(None))
        self.assertFalse(research._is_realistic_person_name(""))
        self.assertFalse(research._is_realistic_person_name("   "))
        self.assertFalse(research._is_realistic_person_name("Unknown"))

    def test_rejects_corporate_looking_names(self):
        self.assertFalse(research._is_realistic_person_name("Acme Trustees Limited"))
        self.assertFalse(research._is_realistic_person_name("Bromley Holdings Ltd"))
        self.assertFalse(research._is_realistic_person_name("Green Group PLC"))
        self.assertFalse(research._is_realistic_person_name("Corporate Secretarial Services LLP"))


class TestGetDirectorFromChExcludesCorporateOfficers(unittest.TestCase):
    """Mocks research.net_utils.smart_get -- no real network call, no API
    key required."""

    def setUp(self):
        research.CH_KEY = "fake-test-key"

    def _fake_response(self, items):
        class _Resp:
            status_code = 200
            def json(self_inner):
                return {"items": items}
        return _Resp()

    def test_skips_corporate_director_and_returns_the_individual(self):
        items = [
            {"officer_role": "corporate-director", "name": "SHELL COMPANY LIMITED"},
            {"officer_role": "director", "name": "SMITH, Jane"},
        ]
        import unittest.mock as mock
        with mock.patch.object(research.net_utils, "smart_get", return_value=self._fake_response(items)):
            name = research.get_director_from_ch("12345678")
        self.assertEqual(name, "Jane Smith")

    def test_fallback_loop_also_skips_corporate_officers(self):
        """No individual director/secretary present at all -- the
        fallback must still refuse to hand back a corporate officer."""
        items = [
            {"officer_role": "corporate-nominee-director", "name": "NOMINEE SERVICES LTD"},
        ]
        import unittest.mock as mock
        with mock.patch.object(research.net_utils, "smart_get", return_value=self._fake_response(items)):
            name = research.get_director_from_ch("12345678")
        self.assertIsNone(name)


class TestGetGooglePlacesInfo(unittest.TestCase):
    """Sep 2 2026: covers the switch back to the real Places API (New) as
    the primary path, with the old DuckDuckGo scrape kept only as a
    fallback for when GOOGLE_MAPS_KEY isn't configured. Mocks
    research.net_utils.smart_post/smart_get -- no real network call, no
    live API key required to run this suite."""

    def setUp(self):
        self._orig_key = research.GOOGLE_MAPS_KEY
        self._orig_quota_reset_at = research._GOOGLE_PLACES_DAILY_QUOTA_RESET_AT[0]
        research._GOOGLE_PLACES_DAILY_QUOTA_RESET_AT[0] = 0.0
        import database as _db
        self._orig_state_store = dict(_db._fake_state_store)
        _db._fake_state_store.clear()
        self._orig_cap = research.GOOGLE_PLACES_MONTHLY_PAID_CALL_CAP

    def tearDown(self):
        research.GOOGLE_MAPS_KEY = self._orig_key
        research._GOOGLE_PLACES_DAILY_QUOTA_RESET_AT[0] = self._orig_quota_reset_at
        import database as _db
        _db._fake_state_store.clear()
        _db._fake_state_store.update(self._orig_state_store)
        research.GOOGLE_PLACES_MONTHLY_PAID_CALL_CAP = self._orig_cap

    def _fake_response(self, status_code=200, json_data=None, text=""):
        class _Resp:
            pass
        r = _Resp()
        r.status_code = status_code
        r.text = text
        r.json = lambda: json_data or {}
        return r

    def test_uses_real_api_when_key_is_configured_and_parses_fields(self):
        research.GOOGLE_MAPS_KEY = "fake-test-key"
        fake_places = {"places": [{
            "nationalPhoneNumber": "020 3143 6969",
            "websiteUri": "https://acmetreesurgery.co.uk",
            "rating": 4.6,
        }]}
        import unittest.mock as mock
        with mock.patch.object(research.net_utils, "smart_post",
                                return_value=self._fake_response(json_data=fake_places)) as mock_post:
            rating, phone, website = research.get_google_places_info("Acme Tree Surgery Ltd", "London")
        self.assertEqual(website, "https://acmetreesurgery.co.uk")
        self.assertEqual(rating, 4.6)
        self.assertTrue(phone and phone.replace(" ", "").endswith("31436969"))
        # Confirms the real API endpoint is hit, not DuckDuckGo.
        called_url = mock_post.call_args[0][0]
        self.assertIn("places.googleapis.com", called_url)
        called_headers = mock_post.call_args.kwargs.get("headers", {})
        self.assertEqual(called_headers.get("X-Goog-Api-Key"), "fake-test-key")

    def test_no_places_found_returns_all_none(self):
        research.GOOGLE_MAPS_KEY = "fake-test-key"
        import unittest.mock as mock
        with mock.patch.object(research.net_utils, "smart_post",
                                return_value=self._fake_response(json_data={"places": []})):
            result = research.get_google_places_info("Nonexistent Company Ltd", "London")
        self.assertEqual(result, (None, None, None))

    def test_non_200_response_is_logged_and_returns_all_none_not_a_crash(self):
        research.GOOGLE_MAPS_KEY = "fake-test-key"
        import unittest.mock as mock
        with mock.patch.object(research.net_utils, "smart_post",
                                return_value=self._fake_response(status_code=403, text="API key invalid")):
            result = research.get_google_places_info("Acme Tree Surgery Ltd", "London")
        self.assertEqual(result, (None, None, None))

    def test_falls_back_to_ddg_scrape_when_no_key_configured(self):
        """Without a key, this must not attempt the real API at all --
        confirms the DDG scrape path (not the API) is what actually runs,
        by checking net_utils.smart_get (DDG's method) gets called instead
        of smart_post (the API's method)."""
        research.GOOGLE_MAPS_KEY = ""
        import unittest.mock as mock
        with mock.patch.object(research.net_utils, "smart_post") as mock_post, \
             mock.patch.object(research.net_utils, "smart_get",
                                return_value=self._fake_response(status_code=200, text="<html></html>")) as mock_get:
            research.get_google_places_info("Acme Tree Surgery Ltd", "London")
        mock_post.assert_not_called()
        mock_get.assert_called_once()

    def test_daily_quota_429_falls_back_to_ddg_for_that_company(self):
        """Sep 2 2026 live incident: once the project's daily SearchText
        quota is exhausted, Google returns 429 with 'SearchTextRequest per
        day' in the body. Before this fix that was treated like any other
        non-200 and returned all-None -- silently reintroducing the exact
        blank phone/email problem this whole API switch was meant to fix.
        It must instead fall back to the free DDG scrape for that company,
        not give up."""
        research.GOOGLE_MAPS_KEY = "fake-test-key"
        import unittest.mock as mock
        quota_body = ('{"error": {"code": 429, "message": "Quota exceeded for quota metric '
                      '\'SearchTextRequest\' and limit \'SearchTextRequest per day\' of service '
                      '\'places.googleapis.com\'."}}')
        with mock.patch.object(research.net_utils, "smart_post",
                                return_value=self._fake_response(status_code=429, text=quota_body)) as mock_post, \
             mock.patch.object(research.net_utils, "smart_get",
                                return_value=self._fake_response(status_code=200, text="<html></html>")) as mock_get:
            research.get_google_places_info("Acme Tree Surgery Ltd", "London")
        mock_post.assert_called_once()  # the API was tried once for this company
        mock_get.assert_called_once()   # then fell back to the free scrape, not a blank result

    def test_daily_quota_429_trips_a_cooldown_so_later_calls_skip_the_api(self):
        """The other half of the fix: hitting the daily cap once shouldn't
        mean every remaining company that day still pays the cost of a
        doomed API round-trip before falling back -- later calls should go
        straight to DDG until the cooldown clears."""
        research.GOOGLE_MAPS_KEY = "fake-test-key"
        import unittest.mock as mock
        quota_body = ('{"error": {"message": "Quota exceeded for quota metric '
                      '\'SearchTextRequest\' and limit \'SearchTextRequest per day\'."}}')
        with mock.patch.object(research.net_utils, "smart_post",
                                return_value=self._fake_response(status_code=429, text=quota_body)) as mock_post, \
             mock.patch.object(research.net_utils, "smart_get",
                                return_value=self._fake_response(status_code=200, text="<html></html>")):
            research.get_google_places_info("First Tree Company Ltd", "London")
        self.assertEqual(mock_post.call_count, 1)
        self.assertGreater(research._GOOGLE_PLACES_DAILY_QUOTA_RESET_AT[0], 0.0)

        with mock.patch.object(research.net_utils, "smart_post") as mock_post_2, \
             mock.patch.object(research.net_utils, "smart_get",
                                return_value=self._fake_response(status_code=200, text="<html></html>")) as mock_get_2:
            research.get_google_places_info("Second Tree Company Ltd", "London")
        mock_post_2.assert_not_called()  # cooldown active -- API not even attempted
        mock_get_2.assert_called_once()

    def test_429_without_per_day_wording_still_falls_back_but_does_not_trip_cooldown(self):
        """A 429 for a different reason (e.g. a per-minute burst limit)
        should still fall back to DDG for that one call (better than a
        blank result), but must NOT trip the daily-quota cooldown on a
        guess -- only the specific confirmed 'per day' wording seen in the
        real incident should skip the API for every later call today."""
        research.GOOGLE_MAPS_KEY = "fake-test-key"
        import unittest.mock as mock
        with mock.patch.object(research.net_utils, "smart_post",
                                return_value=self._fake_response(status_code=429, text="Too many requests per minute")), \
             mock.patch.object(research.net_utils, "smart_get",
                                return_value=self._fake_response(status_code=200, text="<html></html>")) as mock_get:
            research.get_google_places_info("Acme Tree Surgery Ltd", "London")
        mock_get.assert_called_once()
        self.assertEqual(research._GOOGLE_PLACES_DAILY_QUOTA_RESET_AT[0], 0.0)

    def test_monthly_cap_reached_falls_back_to_ddg_without_calling_the_paid_api(self):
        """Sep 2 2026 real incident: Nick manually triggered a full backfill
        that could have made thousands of paid calls with no cost ceiling.
        Once the persistent monthly counter reaches the cap, the paid API
        must not be called at all -- confirmed here by asserting
        smart_post (the paid path) is never invoked once the cap is hit."""
        research.GOOGLE_MAPS_KEY = "fake-test-key"
        research.GOOGLE_PLACES_MONTHLY_PAID_CALL_CAP = 2
        import unittest.mock as mock
        with mock.patch.object(research.net_utils, "smart_post",
                                return_value=self._fake_response(json_data={"places": []})) as mock_post, \
             mock.patch.object(research.net_utils, "smart_get",
                                return_value=self._fake_response(status_code=200, text="<html></html>")) as mock_get:
            research.get_google_places_info("Company One Ltd", "London")
            research.get_google_places_info("Company Two Ltd", "London")
            self.assertEqual(mock_post.call_count, 2)
            # Cap (2) now reached -- this third call must skip the paid API entirely.
            research.get_google_places_info("Company Three Ltd", "London")
        self.assertEqual(mock_post.call_count, 2)
        mock_get.assert_called_once()

    def test_monthly_cap_persists_across_a_fresh_check_simulating_a_restart(self):
        """The whole point of storing this in system_state instead of an
        in-memory counter: a Render restart must not reset it back to zero.
        Simulated here by reading the counter fresh via database.get_system_state
        after the 'process' already recorded calls -- exactly what a new
        process would see after restarting mid-run."""
        research.GOOGLE_MAPS_KEY = "fake-test-key"
        research.GOOGLE_PLACES_MONTHLY_PAID_CALL_CAP = 1
        import unittest.mock as mock
        with mock.patch.object(research.net_utils, "smart_post",
                                return_value=self._fake_response(json_data={"places": []})) as mock_post:
            research.get_google_places_info("Company One Ltd", "London")
        self.assertEqual(mock_post.call_count, 1)
        # Cap of 1 already used -- a brand new call (as if after a restart)
        # must see the persisted count and refuse to spend again this month.
        self.assertFalse(research._google_places_paid_call_budget_available())

    def test_monthly_cap_resets_on_a_new_calendar_month(self):
        research.GOOGLE_MAPS_KEY = "fake-test-key"
        research.GOOGLE_PLACES_MONTHLY_PAID_CALL_CAP = 1
        import database as _db
        _db.set_system_state("google_places_paid_calls_month", "2020-01")  # a long-past month
        _db.set_system_state("google_places_paid_calls_count", "1")
        self.assertTrue(research._google_places_paid_call_budget_available())

    def test_known_spam_listing_is_rejected_not_stored(self):
        """Sep 2 2026 real incident: several genuinely different UK tree
        surgery companies got matched by Google's Text Search to the exact
        same unrelated US business. Confirmed live the same night. Must be
        rejected entirely (not just the website field) since the phone and
        email in the same result came from the same wrong match."""
        research.GOOGLE_MAPS_KEY = "fake-test-key"
        import unittest.mock as mock
        fake_places = {"places": [{
            "nationalPhoneNumber": "555-0100",
            "websiteUri": "https://10summersheatingandcoolingllc.pro/",
            "rating": 4.9,
        }]}
        with mock.patch.object(research.net_utils, "smart_post",
                                return_value=self._fake_response(json_data=fake_places)):
            result = research.get_google_places_info("Oakmont Arborist & Ground Works Ltd", "Manchester")
        self.assertEqual(result, (None, None, None))

    def test_budget_check_failure_fails_closed_not_open(self):
        """If the persistent counter can't even be read, this must refuse
        to spend rather than silently allow it -- a wrongly-skipped paid
        call costs nothing; a wrongly-allowed one when we can't verify the
        count is the exact mistake this whole cap exists to prevent."""
        import unittest.mock as mock
        with mock.patch.object(research.database, "get_system_state", side_effect=Exception("db down")):
            self.assertFalse(research._google_places_paid_call_budget_available())


class _FakeCursor:
    """Minimal cursor test double shared by the SIC-code and sole-trader
    discovery tests below -- records every execute() call so tests can
    assert on what was actually written, without a real Postgres driver."""
    def __init__(self, fetchall_result=None, executed_log=None):
        self._fetchall_result = fetchall_result if fetchall_result is not None else []
        self.executed = executed_log if executed_log is not None else []

    def execute(self, query, params=None):
        self.executed.append((query, params))

    def fetchall(self):
        return self._fetchall_result

    def fetchone(self):
        return self._fetchall_result[0] if self._fetchall_result else None

    def close(self):
        pass


class _FakeConn:
    def __init__(self, fetchall_result=None, executed_log=None):
        self._cursor = _FakeCursor(fetchall_result, executed_log)

    def cursor(self):
        return self._cursor

    def commit(self):
        pass

    def close(self):
        pass


class TestSicCodeCompanyDiscovery(unittest.TestCase):
    """Sep 2 2026: perform_research_by_sic finds tree surgery Ltd companies
    whose registered NAME says nothing about trees (e.g. "Ridgeline
    Contracting Ltd") by searching Companies House by SIC code instead --
    something perform_research's name-substring search can never do,
    regardless of how it's tuned. Mocks research.database and
    research._search_companies_house_by_sic -- no real network call, no
    live database."""

    def setUp(self):
        self._orig_key = research.CH_KEY
        research.CH_KEY = "fake-ch-key"
        self._executed_log = []

    def tearDown(self):
        research.CH_KEY = self._orig_key

    def _patch_db(self, known_company_numbers=None):
        fetchall_result = [(n,) for n in (known_company_numbers or [])]
        conn = _FakeConn(fetchall_result, self._executed_log)
        return mock.patch.object(research.database, "get_db_conn", return_value=conn)

    def test_no_ch_key_aborts_without_searching(self):
        research.CH_KEY = ""
        with mock.patch.object(research, "_search_companies_house_by_sic") as mock_search:
            research.perform_research_by_sic("Bristol")
        mock_search.assert_not_called()

    def test_trusted_sic_tier_accepted_without_a_name_check(self):
        """A genuine forestry SIC code (02100/02200/02400) is trusted on its
        own -- a branded name with zero tree-related words must still be
        accepted, which is the entire point of this discovery method."""
        trusted_item = {
            "company_name": "RIDGELINE CONTRACTING LTD", "company_number": "SIC001",
            "registered_office_address": {"postal_code": "BS1 1AA"}, "sic_codes": ["02100"],
        }
        def fake_search(sic_codes, location=None, items_per_page=100):
            return [trusted_item] if sic_codes == research.TREE_SIC_CODES_TRUSTED else []

        with self._patch_db(), \
             mock.patch.object(research, "_search_companies_house_by_sic", side_effect=fake_search), \
             mock.patch.object(research, "_enrich_and_upsert_partner_candidate") as mock_enrich:
            research.perform_research_by_sic("Bristol")

        mock_enrich.assert_called_once()
        candidate = mock_enrich.call_args[0][0]
        self.assertEqual(candidate[2], "SIC001")  # company_number
        self.assertIn("RIDGELINE", candidate[1])   # name, upper-cased

    def test_gated_sic_tier_rejects_a_name_with_no_tree_signal(self):
        """SIC 81300 (general landscape service activities) also covers
        lawn care and garden design -- a name with no tree-related word at
        all must be rejected here, unlike the trusted tier above."""
        gated_item = {
            "company_name": "GREEN LAWNS LANDSCAPING LTD", "company_number": "SIC002",
            "registered_office_address": {"postal_code": "BS1 1AA"}, "sic_codes": ["81300"],
        }
        def fake_search(sic_codes, location=None, items_per_page=100):
            return [gated_item] if sic_codes == research.TREE_SIC_CODES_GATED else []

        with self._patch_db(), \
             mock.patch.object(research, "_search_companies_house_by_sic", side_effect=fake_search), \
             mock.patch.object(research, "_enrich_and_upsert_partner_candidate") as mock_enrich:
            research.perform_research_by_sic("Bristol")

        mock_enrich.assert_not_called()

    def test_gated_sic_tier_accepts_a_name_with_a_real_tree_signal(self):
        gated_item = {
            "company_name": "GREENWOOD TREE & GARDEN SERVICES LTD", "company_number": "SIC003",
            "registered_office_address": {"postal_code": "BS1 1AA"}, "sic_codes": ["81300"],
        }
        def fake_search(sic_codes, location=None, items_per_page=100):
            return [gated_item] if sic_codes == research.TREE_SIC_CODES_GATED else []

        with self._patch_db(), \
             mock.patch.object(research, "_search_companies_house_by_sic", side_effect=fake_search), \
             mock.patch.object(research, "_enrich_and_upsert_partner_candidate") as mock_enrich:
            research.perform_research_by_sic("Bristol")

        mock_enrich.assert_called_once()

    def test_already_known_company_number_is_skipped(self):
        trusted_item = {
            "company_name": "RIDGELINE CONTRACTING LTD", "company_number": "SIC001",
            "registered_office_address": {"postal_code": "BS1 1AA"}, "sic_codes": ["02100"],
        }
        def fake_search(sic_codes, location=None, items_per_page=100):
            return [trusted_item] if sic_codes == research.TREE_SIC_CODES_TRUSTED else []

        with self._patch_db(known_company_numbers=["SIC001"]), \
             mock.patch.object(research, "_search_companies_house_by_sic", side_effect=fake_search), \
             mock.patch.object(research, "_enrich_and_upsert_partner_candidate") as mock_enrich:
            research.perform_research_by_sic("Bristol")

        mock_enrich.assert_not_called()

    def test_non_ltd_company_is_skipped(self):
        """company_type=ltd is already a server-side filter on the real
        endpoint, but a defensive client-side check costs nothing and
        matches perform_research's own equivalent check."""
        weird_item = {
            "company_name": "RIDGELINE FORESTRY LLP", "company_number": "SIC004",
            "registered_office_address": {"postal_code": "BS1 1AA"}, "sic_codes": ["02100"],
        }
        def fake_search(sic_codes, location=None, items_per_page=100):
            return [weird_item] if sic_codes == research.TREE_SIC_CODES_TRUSTED else []

        with self._patch_db(), \
             mock.patch.object(research, "_search_companies_house_by_sic", side_effect=fake_search), \
             mock.patch.object(research, "_enrich_and_upsert_partner_candidate") as mock_enrich:
            research.perform_research_by_sic("Bristol")

        mock_enrich.assert_not_called()

    def test_db_error_loading_known_companies_aborts_safely(self):
        with mock.patch.object(research.database, "get_db_conn", side_effect=Exception("db down")), \
             mock.patch.object(research, "_search_companies_house_by_sic") as mock_search:
            research.perform_research_by_sic("Bristol")  # must not raise
        mock_search.assert_not_called()


class TestSearchCompaniesHouseBySic(unittest.TestCase):
    def setUp(self):
        self._orig_key = research.CH_KEY
        research.CH_KEY = "fake-ch-key"

    def tearDown(self):
        research.CH_KEY = self._orig_key

    def _fake_response(self, status_code=200, json_data=None):
        class _Resp:
            pass
        r = _Resp()
        r.status_code = status_code
        r.json = lambda: json_data or {}
        return r

    def test_no_ch_key_returns_empty_without_a_network_call(self):
        research.CH_KEY = ""
        with mock.patch.object(research.net_utils, "smart_get") as mock_get:
            result = research._search_companies_house_by_sic(["02100"])
        self.assertEqual(result, [])
        mock_get.assert_not_called()

    def test_passes_comma_joined_sic_codes_and_ltd_filter(self):
        with mock.patch.object(research.net_utils, "smart_get",
                                return_value=self._fake_response(json_data={"items": []})) as mock_get:
            research._search_companies_house_by_sic(["02100", "02200"], location="Leeds")
        params = mock_get.call_args.kwargs.get("params", {})
        self.assertEqual(params.get("sic_codes"), "02100,02200")
        self.assertEqual(params.get("company_type"), "ltd")
        self.assertEqual(params.get("company_status"), "active")
        self.assertEqual(params.get("location"), "Leeds")

    def test_non_200_returns_empty_not_a_crash(self):
        with mock.patch.object(research.net_utils, "smart_get",
                                return_value=self._fake_response(status_code=500)):
            result = research._search_companies_house_by_sic(["02100"])
        self.assertEqual(result, [])

    def test_network_exception_returns_empty_not_a_crash(self):
        with mock.patch.object(research.net_utils, "smart_get", side_effect=Exception("timeout")):
            result = research._search_companies_house_by_sic(["02100"])
        self.assertEqual(result, [])


class TestSoleTraderDiscovery(unittest.TestCase):
    """Sep 2 2026: discover_sole_traders_via_google_places finds sole
    traders, who never register with Companies House at all, via a Google
    Places category search instead of a company lookup. Mocks
    research.net_utils.smart_post and research.database -- no real network
    call, no live database, no live API key."""

    def setUp(self):
        self._orig_key = research.GOOGLE_MAPS_KEY
        research.GOOGLE_MAPS_KEY = "fake-test-key"
        import database as _db
        self._orig_state_store = dict(_db._fake_state_store)
        _db._fake_state_store.clear()
        self._executed_log = []

    def tearDown(self):
        research.GOOGLE_MAPS_KEY = self._orig_key
        import database as _db
        _db._fake_state_store.clear()
        _db._fake_state_store.update(self._orig_state_store)

    def _fake_response(self, status_code=200, json_data=None, text=""):
        class _Resp:
            pass
        r = _Resp()
        r.status_code = status_code
        r.text = text
        r.json = lambda: json_data or {}
        return r

    def _patch_db(self, known_phones=None):
        fetchall_result = [(p,) for p in (known_phones or [])]
        conn = _FakeConn(fetchall_result, self._executed_log)
        return mock.patch.object(research.database, "get_db_conn", return_value=conn)

    def test_no_key_aborts_without_a_network_call(self):
        research.GOOGLE_MAPS_KEY = ""
        with mock.patch.object(research.net_utils, "smart_post") as mock_post:
            result = research.discover_sole_traders_via_google_places("Bristol")
        self.assertEqual(result, 0)
        mock_post.assert_not_called()

    def test_monthly_cap_exhausted_skips_without_a_network_call(self):
        """Shares the SAME monthly cap as every other paid Places call --
        must not spend a separate budget of its own."""
        orig_cap = research.GOOGLE_PLACES_MONTHLY_PAID_CALL_CAP
        research.GOOGLE_PLACES_MONTHLY_PAID_CALL_CAP = 0
        try:
            with mock.patch.object(research.net_utils, "smart_post") as mock_post:
                result = research.discover_sole_traders_via_google_places("Bristol")
            self.assertEqual(result, 0)
            mock_post.assert_not_called()
        finally:
            research.GOOGLE_PLACES_MONTHLY_PAID_CALL_CAP = orig_cap

    def test_writes_a_new_sole_trader_with_no_company_number(self):
        places_response = {"places": [{
            "id": "places/abc123",
            "displayName": {"text": "Barker's Tree Surgery"},
            "formattedAddress": "12 Oak Lane, Bristol, BS1 1AA",
            "nationalPhoneNumber": "07911 123456",
            "websiteUri": "https://barkerstreesurgery.example.co.uk",
            "rating": 4.8,
        }]}
        with self._patch_db(), \
             mock.patch.object(research.net_utils, "smart_post",
                                return_value=self._fake_response(json_data=places_response)), \
             mock.patch.object(research, "scrape_contact_info_from_website", return_value=(None, None)):
            written = research.discover_sole_traders_via_google_places("Bristol")

        self.assertEqual(written, 1)
        insert_calls = [e for e in self._executed_log if "INSERT INTO potential_partners" in e[0]]
        self.assertEqual(len(insert_calls), 1)
        params = insert_calls[0][1]
        self.assertIn("BARKER'S TREE SURGERY", params[0])
        self.assertEqual(params[1], "places/abc123")  # place_id
        self.assertIn("registration:sole_trader", params[-1])

    def test_non_tree_name_is_rejected(self):
        places_response = {"places": [{
            "id": "places/xyz789",
            "displayName": {"text": "Bristol Garden Centre"},
            "formattedAddress": "1 High St, Bristol",
            "nationalPhoneNumber": "0117 123 4567",
        }]}
        with self._patch_db(), \
             mock.patch.object(research.net_utils, "smart_post",
                                return_value=self._fake_response(json_data=places_response)):
            written = research.discover_sole_traders_via_google_places("Bristol")
        self.assertEqual(written, 0)

    def test_known_spam_website_is_rejected(self):
        places_response = {"places": [{
            "id": "places/spam1",
            "displayName": {"text": "Some Tree Surgeon Ltd"},
            "websiteUri": "https://10summersheatingandcoolingllc.pro/",
            "nationalPhoneNumber": "0117 123 4567",
        }]}
        with self._patch_db(), \
             mock.patch.object(research.net_utils, "smart_post",
                                return_value=self._fake_response(json_data=places_response)):
            written = research.discover_sole_traders_via_google_places("Bristol")
        self.assertEqual(written, 0)

    def test_phone_already_known_is_treated_as_a_duplicate(self):
        """A phone number that already belongs to a Ltd company partner
        means this is the same real business found twice -- must not be
        inserted again as a second, contact-less sole-trader row."""
        places_response = {"places": [{
            "id": "places/dup1",
            "displayName": {"text": "Barker's Tree Surgery"},
            "nationalPhoneNumber": "07911 123456",
        }]}
        with self._patch_db(known_phones=["07911123456"]), \
             mock.patch.object(research.net_utils, "smart_post",
                                return_value=self._fake_response(json_data=places_response)):
            written = research.discover_sole_traders_via_google_places("Bristol")
        self.assertEqual(written, 0)

    def test_non_200_response_returns_zero_not_a_crash(self):
        with mock.patch.object(research.net_utils, "smart_post",
                                return_value=self._fake_response(status_code=403, text="denied")):
            written = research.discover_sole_traders_via_google_places("Bristol")
        self.assertEqual(written, 0)

    def test_no_places_found_returns_zero(self):
        with mock.patch.object(research.net_utils, "smart_post",
                                return_value=self._fake_response(json_data={"places": []})):
            written = research.discover_sole_traders_via_google_places("Bristol")
        self.assertEqual(written, 0)


class TestResearchAllCitiesRunsAllThreeDiscoveryMethods(unittest.TestCase):
    """Sep 2 2026: research_all_cities is the function the live autonomous
    cycle actually calls (confirmed against production logs) -- this just
    guards that all three discovery methods stay wired in per region, so a
    future refactor can't silently drop one without a test failing."""

    def test_all_three_methods_called_once_per_region(self):
        with mock.patch.object(research, "perform_research") as mock_name, \
             mock.patch.object(research, "perform_research_by_sic") as mock_sic, \
             mock.patch.object(research, "discover_sole_traders_via_google_places") as mock_sole:
            research.research_all_cities()
        expected_calls = 11  # London, South East, South West, West Midlands,
        # East Midlands, Yorkshire, North West, North East, East of England,
        # Scotland, Wales
        self.assertEqual(mock_name.call_count, expected_calls)
        self.assertEqual(mock_sic.call_count, expected_calls)
        self.assertEqual(mock_sole.call_count, expected_calls)


if __name__ == "__main__":
    unittest.main()

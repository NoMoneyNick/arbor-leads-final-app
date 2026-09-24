"""
test_marketplace_privacy_review.py -- 2026-09-24, privacy review of the
mailed-introduction model's public and authenticated outputs.

Nick's own stated concerns, verbatim: "Public marketplace detail URLs have
exposed raw council references." / "Some references contain slashes and
linked detail pages returned 404." / "Detailed descriptions and Tree
Preservation Order numbers may let someone locate the original
application." / "Regex redaction currently covers only some patterns." /
"Use opaque public/buyer identifiers where required, resolving the
council reference internally. Encoding the raw reference is not
sufficient."

Covers, matching what was actually found and fixed this pass:
  1. database._redact_address_from_summary now also strips a council/TPO
     reference restated inline in free text, not just an address --
     tested with real names, addresses, postcodes, council references and
     TPO identifiers, per this pass's own instruction, plus deliberate
     non-matches (a plain date, a fraction) to prove it isn't over-broad.
  2. database.get_marketplace_leads_with_freshness's single-lead lookup
     (`only_id`, was `only_reference` until this pass) now resolves the
     lead's own opaque UUID `id`, never the raw council reference --
     encoding the reference was never the fix; the lookup key itself
     changed.
  3. main.py's public marketplace card and /marketplace/lead/{lead_id}
     detail page now link and resolve by that same opaque id -- the raw
     reference never appears in the pre-purchase HTML or its own href,
     even when the reference itself contains "/" (the same routing-
     convertor failure already fixed elsewhere in this codebase for the
     buyer-facing letter/flyer/street-view routes; moot here since a UUID
     never contains "/", but asserted anyway as the regression this fix
     must not reintroduce).
  4. notifications.dispatch_lead_alerts's subscriber "Unlock" link (the
     one part of that function that goes to a real customer, not the
     internal-only admin digest) now uses the lead's opaque id too -- it
     used to embed the raw reference, which was both this same privacy
     leak AND a live functional bug (payments._resolve_live_single_lead_
     price looks leads up by `id`, a UUID column, so the old link could
     never have resolved a price at all).

Uses the same "load database.py under a private module name, stub
psycopg2 only" technique tests/test_reconciliation.py already established
(see that file's own comment) for parts 1-2, and this suite's established
fastapi-stub import chain (via test_indirect_identifier_gating) for part
3. Part 4 stubs a minimal fake `database` module directly, restored in
tearDown, since dispatch_lead_alerts only needs a handful of its
attributes and doesn't touch a real connection.

Run with:
    python -m unittest tests.test_marketplace_privacy_review -v
"""
import importlib.util
import os
import sys
import types
import unittest
from unittest.mock import MagicMock, patch

_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
_APP_DIR = os.path.dirname(_THIS_DIR)
sys.path.insert(0, _APP_DIR)
sys.path.insert(0, _THIS_DIR)

if "psycopg2" not in sys.modules:
    sys.modules["psycopg2"] = types.ModuleType("psycopg2")

_DATABASE_PATH = os.path.join(_APP_DIR, "database.py")
_spec = importlib.util.spec_from_file_location("_database_under_test_marketplace_privacy", _DATABASE_PATH)
_real_database = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_real_database)

# A real UK planning reference, taken verbatim from main.py's own
# _SUSPECT_DISCHARGE_REFS_SEP11 constant (same one test_slash_reference_
# routing.py uses) -- not a synthetic worst case.
_REAL_SLASH_REFERENCE = "26/P/1118/S73"


class TestRedactAddressFromSummaryReferencesAndTPO(unittest.TestCase):
    """database._redact_address_from_summary, real implementation (loaded
    above, no stubbing of the function itself). Focused checks per this
    pass's own instruction: names, addresses, postcodes, council
    references and TPO identifiers, plus deliberate non-matches."""

    def _redact(self, s):
        return _real_database._redact_address_from_summary(s)

    def test_none_and_empty_pass_through_unchanged(self):
        self.assertIsNone(self._redact(None))
        self.assertEqual(self._redact(""), "")

    def test_full_postcode_still_hidden_no_regression(self):
        out = self._redact("Site: 14 Oak Avenue, Newark, NG22 8AA.")
        self.assertNotIn("NG22 8AA", out)
        self.assertIn("[postcode hidden]", out)

    def test_house_number_and_street_still_hidden_no_regression(self):
        out = self._redact("Fell one oak at 14 Oak Avenue, rear garden.")
        self.assertNotIn("14 Oak Avenue", out)
        self.assertIn("[address hidden]", out)

    def test_real_council_reference_with_letter_segment_is_hidden(self):
        out = self._redact(f"TPO ref {_REAL_SLASH_REFERENCE} refers to this tree.")
        self.assertNotIn(_REAL_SLASH_REFERENCE, out)

    def test_another_real_shaped_reference_is_hidden(self):
        out = self._redact("See application 23/00568/WTCA for full details.")
        self.assertNotIn("23/00568/WTCA", out)

    def test_tpo_with_slash_is_hidden(self):
        out = self._redact("TPO/2024/0142 - crown reduction of oak.")
        self.assertNotIn("2024/0142", out)
        self.assertIn("[reference hidden]", out)

    def test_tpo_with_dash_is_hidden(self):
        out = self._redact("TPO 24-0142 covers this tree.")
        self.assertNotIn("24-0142", out)

    def test_tpo_with_no_separator_is_hidden(self):
        out = self._redact("TPO2024/0142 near the boundary.")
        self.assertNotIn("2024/0142", out)

    def test_reference_before_the_word_tpo_is_still_hidden(self):
        out = self._redact("Ref: 2024/1234/TPO for felling consent.")
        self.assertNotIn("2024/1234/TPO", out)

    def test_plain_date_is_not_redacted(self):
        """The letter-somewhere requirement in _COUNCIL_REFERENCE_IN_TEXT_RE
        exists specifically so an ordinary DD/MM/YYYY date isn't eaten --
        see that regex's own comment in database.py."""
        out = self._redact("Application filed on 12/09/2026 for tree work.")
        self.assertIn("12/09/2026", out)

    def test_ordinary_fraction_is_not_redacted(self):
        out = self._redact("Roughly 3/4 of the crown is affected by dieback.")
        self.assertIn("3/4", out)

    def test_ordinary_description_with_no_identifiers_is_unchanged(self):
        text = "Fell one oak, dead and dangerous, urgent removal needed."
        self.assertEqual(self._redact(text), text)

    def test_combined_real_shaped_summary_hides_every_identifier(self):
        """An integration-style check: a summary shaped like a genuine
        scraped council description, with an address, a postcode, AND a
        reference all restated inline -- none of the three identifying
        substrings may survive."""
        text = (
            f"T1 - Ash - Fell. Site: 14 Oak Avenue, Newark, NG22 8AA. "
            f"Ref {_REAL_SLASH_REFERENCE}."
        )
        out = self._redact(text)
        self.assertNotIn("14 Oak Avenue", out)
        self.assertNotIn("NG22 8AA", out)
        self.assertNotIn(_REAL_SLASH_REFERENCE, out)
        # Still useful and non-empty -- omission of identifiers, not of
        # everything (matches "prefer a useful structured summary... if a
        # field cannot be safely produced, omit it rather than inventing
        # it").
        self.assertIn("Ash", out)
        self.assertIn("Fell", out)


class TestGetMarketplaceLeadsWithFreshnessOnlyId(unittest.TestCase):
    """The single-lead prepurchase lookup now resolves by the lead's own
    opaque `id` (a real UUID column), never by the raw council reference
    -- "encoding the raw reference is not sufficient" is why this is a
    lookup-key swap, not a quoting fix. Asserted directly against the SQL
    text sent to the cursor, the same technique tests/test_dispatch_
    purge.py's TestHistoricalDispatchesAreNeverPurged already uses for
    this class of "prove the actual query changed" check."""

    def _conn_returning(self, rows):
        conn = MagicMock()
        cur = MagicMock()
        cur.fetchall.return_value = rows
        conn.cursor.return_value = cur
        return conn, cur

    def test_only_id_adds_an_id_filter_not_a_reference_filter(self):
        conn, cur = self._conn_returning([])
        with patch.object(_real_database, "SURL", "postgres://fake"), \
             patch.object(_real_database, "get_db_conn", return_value=conn):
            _real_database.get_marketplace_leads_with_freshness(only_id="fake-uuid-1234", limit=1)
        sql_text = cur.execute.call_args[0][0]
        self.assertIn("id::text = %s", sql_text)
        self.assertNotIn("AND reference = %s", sql_text)
        # The id value itself was actually bound as a query parameter.
        params = cur.execute.call_args[0][1]
        self.assertIn("fake-uuid-1234", params)

    def test_no_only_id_omits_the_extra_filter_entirely(self):
        conn, cur = self._conn_returning([])
        with patch.object(_real_database, "SURL", "postgres://fake"), \
             patch.object(_real_database, "get_db_conn", return_value=conn):
            _real_database.get_marketplace_leads_with_freshness(limit=5)
        sql_text = cur.execute.call_args[0][0]
        self.assertNotIn("id::text = %s", sql_text)
        self.assertNotIn("AND reference = %s", sql_text)

    def test_a_slash_containing_reference_passed_as_only_id_matches_nothing(self):
        """Confirms the lookup key genuinely changed, not just got quoted
        differently: passing the OLD kind of value (a raw, slash-
        containing council reference) as `only_id` still only ever
        compares it against `id`, so it can never accidentally match a
        row via the old reference column -- there is no fallback."""
        conn, cur = self._conn_returning([])
        with patch.object(_real_database, "SURL", "postgres://fake"), \
             patch.object(_real_database, "get_db_conn", return_value=conn):
            _real_database.get_marketplace_leads_with_freshness(only_id=_REAL_SLASH_REFERENCE, limit=1)
        sql_text = cur.execute.call_args[0][0]
        self.assertIn("id::text = %s", sql_text)


# ---------------------------------------------------------------------------
# Part 3: main.py's public marketplace card + /marketplace/lead/{lead_id}.

import test_indirect_identifier_gating as _tiig  # noqa: E402
import main  # noqa: E402


class TestLeadDetailViewUsesOpaqueId(unittest.TestCase):
    """main.lead_detail_view (the /marketplace/lead/{lead_id} route)."""

    def _fake_lead(self, lead_id="3fae9c1e-fake-uuid", ref=_REAL_SLASH_REFERENCE):
        return {
            "id": lead_id, "ref": ref, "plan_key": "single_lead_medium", "price": 25,
            "job_category": {"key": "general", "label": "General", "icon": "general", "color": "#64748b"},
            "discovered_at": None, "reg_date": None,
            "has_agent": None, "is_urgent": False, "score": "small",
            "area_label": "NG22, Newark and Sherwood", "summary": "Fell one oak, dead and dangerous.",
            "council": "Newark and Sherwood", "badge_bg": "#000", "badge_color": "#fff",
            "badge_text": "NEW", "days_left": "3 days left",
        }

    def test_looked_up_by_the_path_ids_only_id_not_only_reference(self):
        request = MagicMock()
        request.cookies = {}
        with patch("main.database.get_marketplace_leads_with_freshness", return_value=[self._fake_lead()], create=True) as mock_lookup, \
             patch("main.database.get_subscriber_discount", return_value={"eligible": False, "discount_pct": 0}, create=True), \
             patch("main.database.get_contractor_subscription", return_value=None, create=True), \
             patch("main._verify_session_cookie", return_value=None):
            main.lead_detail_view("3fae9c1e-fake-uuid", request)
        _, kwargs = mock_lookup.call_args
        self.assertEqual(kwargs.get("only_id"), "3fae9c1e-fake-uuid")
        self.assertNotIn("only_reference", kwargs)

    def test_the_raw_reference_never_appears_in_the_rendered_page(self):
        request = MagicMock()
        request.cookies = {}
        with patch("main.database.get_marketplace_leads_with_freshness", return_value=[self._fake_lead()], create=True), \
             patch("main.database.get_subscriber_discount", return_value={"eligible": False, "discount_pct": 0}, create=True), \
             patch("main.database.get_contractor_subscription", return_value=None, create=True), \
             patch("main._verify_session_cookie", return_value=None):
            body = main.lead_detail_view("3fae9c1e-fake-uuid", request)
        self.assertNotIn(_REAL_SLASH_REFERENCE, body)


class TestMarketplaceCardLinksUseOpaqueId(unittest.TestCase):
    """main.marketplace_view -- the "View Full Job Details" card link."""

    def _fake_lead_row(self, lead_id="3fae9c1e-fake-uuid", ref=_REAL_SLASH_REFERENCE):
        return {
            "id": lead_id, "ref": ref, "summary": "Fell one oak, dead and dangerous.",
            "council": "Newark and Sherwood", "price": 25, "plan_key": "single_lead_medium",
            "job_category": {"key": "general", "label": "General", "icon": "general", "color": "#64748b"},
            "discovered_at": None, "reg_date": None, "has_agent": None,
            "is_urgent": False, "score": "small", "area_label": "NG22, Newark and Sherwood",
            "badge_bg": "#000", "badge_color": "#fff", "badge_text": "NEW", "days_left": "3 days left",
        }

    def test_card_href_uses_the_opaque_id_not_the_raw_reference(self):
        request = MagicMock()
        request.cookies = {}
        with patch("main.database.get_marketplace_leads_with_freshness", return_value=[self._fake_lead_row()], create=True), \
             patch("main.database.get_subscriber_discount", return_value={"eligible": False, "discount_pct": 0}, create=True), \
             patch("main.database.get_contractor_subscription", return_value=None, create=True), \
             patch("main.database.get_area_capacity_status", return_value={"status": "unknown"}, create=True), \
             patch("main.database.release_expired_reservations", create=True), \
             patch("main.database.JOB_CATEGORIES", _real_database.JOB_CATEGORIES, create=True), \
             patch("main.database.EARLY_ACCESS_WINDOW_MINUTES", _real_database.EARLY_ACCESS_WINDOW_MINUTES, create=True), \
             patch("main._verify_session_cookie", return_value=None):
            body = main.marketplace_view(request)
        body_str = body if isinstance(body, str) else getattr(body, "body", str(body))
        self.assertIn("/marketplace/lead/3fae9c1e-fake-uuid", body_str)
        self.assertNotIn(_REAL_SLASH_REFERENCE, body_str)


# ---------------------------------------------------------------------------
# Part 4: notifications.dispatch_lead_alerts's real-subscriber "Unlock" link.
#
# Loaded under a PRIVATE module name via importlib -- same technique, and
# the same reason, as tests/test_address_release_gate.py's own Layer 2b
# section (see its long comment): a bare `import notifications` here would
# just return whatever main.py's own import chain (above) already left in
# sys.modules["notifications"] -- a stub with no RESEND_API_KEY and no
# real dispatch_lead_alerts logic. Loading a private copy touches
# sys.modules["notifications"] not at all, so this class can drive the
# real function regardless of what any other file in the same process did
# to that shared name.
_NOTIFICATIONS_PATH = os.path.join(_APP_DIR, "notifications.py")
_notifications_spec = importlib.util.spec_from_file_location(
    "_notifications_under_test_marketplace_privacy", _NOTIFICATIONS_PATH
)
notifications = importlib.util.module_from_spec(_notifications_spec)
_notifications_spec.loader.exec_module(notifications)


class TestDispatchLeadAlertsUnlockLinkUsesOpaqueId(unittest.TestCase):
    """The one part of dispatch_lead_alerts that reaches a real customer
    (the "to": [email] requests.post call) -- not the internal-only
    admin digest/individual-email branches further down, which go to
    TEST_EMAIL only and are unaffected by this fix. Stubs a minimal fake
    `database` module directly (dispatch_lead_alerts does its own local
    `import database`), restored in tearDown so this never leaks into
    other test files run in the same process."""

    def setUp(self):
        self._had_database = "database" in sys.modules
        self._old_database = sys.modules.get("database")
        fake_db = types.ModuleType("database")
        fake_db.release_expired_reservations = MagicMock()
        fake_db.get_active_subscribers_by_seniority = MagicMock(return_value=[{
            "email": "contractor@example.com", "outcode": "NG22", "lat": None, "lon": None,
            "radius": 15, "job_size_preference": "all", "notification_preference": "email",
        }])
        fake_db.get_subscriber_discount = MagicMock(return_value={"eligible": False, "discount_pct": 0})
        fake_db.lead_distance_miles = MagicMock(return_value=None)
        fake_db._redact_address_from_summary = MagicMock(side_effect=lambda s: s)
        sys.modules["database"] = fake_db
        self._fake_db = fake_db

    def tearDown(self):
        if self._had_database:
            sys.modules["database"] = self._old_database
        else:
            sys.modules.pop("database", None)

    def test_unlock_link_uses_the_leads_id_not_its_raw_reference(self):
        leads = [{
            "id": "3fae9c1e-fake-uuid", "ref": _REAL_SLASH_REFERENCE, "reference": _REAL_SLASH_REFERENCE,
            "addr": "14 OAK AVENUE, NEWARK, NG22 8AA", "summary": "Fell one oak, dead and dangerous.",
            "lead_score": "small", "lead_price": 25, "council": "Newark and Sherwood", "vertical": "tree",
            "has_agent": None,
        }]
        captured = {}

        def _fake_post(url, headers=None, json=None, **kwargs):
            captured["json"] = json
            return MagicMock(status_code=200)

        old_key = notifications.RESEND_API_KEY
        notifications.RESEND_API_KEY = "fake-key"
        try:
            with patch.object(notifications.requests, "post", side_effect=_fake_post):
                notifications.dispatch_lead_alerts("Newark", leads)
        finally:
            notifications.RESEND_API_KEY = old_key

        self.assertIn("json", captured)
        html_body = captured["json"]["html"]
        self.assertIn("lead_id=3fae9c1e-fake-uuid", html_body)
        self.assertNotIn(_REAL_SLASH_REFERENCE, html_body)
        self.assertNotIn(urllib_quote_placeholder(_REAL_SLASH_REFERENCE), html_body)


def urllib_quote_placeholder(s):
    """Small local helper so the test above also checks the PERCENT-
    ENCODED form of the raw reference never appears either -- "encoding
    the raw reference is not sufficient" means neither the plain nor the
    encoded form should be there, since the lookup key itself changed."""
    import urllib.parse
    return urllib.parse.quote(s, safe="")


if __name__ == "__main__":
    unittest.main()

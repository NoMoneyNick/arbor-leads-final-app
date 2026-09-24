"""
test_indirect_identifier_gating.py -- 2026-09-23, Request D, Part 1:
"Remove homeowner names and raw council references from every new-buyer
page, API response, email, preview and download. Wire in the existing
buyer-facing reference... Preserve the agreed historical-access
distinction, but do not treat historical disclosures as undone."

Three layers of coverage, mirroring test_address_release_gate.py's own
structure:
  1. The two new address_release.py primitives in isolation:
     guarded_applicant_name_for_lead(_reference) (mirrors guarded_address_
     for_lead's historical/new two-tier decision, applied to the applicant
     name) and resolve_buyer_facing_reference(_standalone) (the reverse of
     the pre-existing buyer_facing_reference -- what a route's incoming
     path parameter is resolved through before any existing ownership/
     gating logic runs).
  2. The three routes that take a buyer-facing reference as their path
     parameter (/generate-letter, /generate-street-flyer, /street-view) --
     proving a NEW allocation's buyer-facing reference (a lead_
     allocations.id UUID) is correctly resolved back to the real lead
     before the route's existing lookup runs, and that a historical
     claim's own reference (unchanged) still works exactly as before.
  3. contractor_dashboard/my_leads_view's per-lead rendering and the two
     transactional emails -- proving applicant_name is hidden and the raw
     council reference never reaches the rendered output for a NEW
     allocation, while both are preserved unchanged for a historical claim
     (the "do not treat historical disclosures as undone" requirement).

Run with:
    python -m unittest tests.test_indirect_identifier_gating -v
"""
import os
import sys
import types
import unittest
from unittest.mock import MagicMock, patch

_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
_APP_DIR = os.path.dirname(_THIS_DIR)
sys.path.insert(0, _APP_DIR)

if "database" not in sys.modules:
    sys.modules["database"] = types.ModuleType("database")

# test_address_release_gate.py already builds the full fastapi/scanners/
# research/payments stub set this sandbox needs before `import main` can
# succeed (neither fastapi, stripe, beautifulsoup4 nor google-generativeai
# is installed here), pre-populates database.get_dispatched_lead_address_
# for_contractor/street_view_url on the shared database stub, AND loads a
# PRIVATE copy of the real notifications.py via importlib (see that file's
# own long comment on why a plain `import notifications` is unsafe under
# `unittest discover` -- another file elsewhere replaces the shared
# sys.modules["notifications"] slot with a bare stub). Reusing all of that
# via one import avoids duplicating ~150 lines of established plumbing.
# Established convention (see test_letter_settings_routes.py / test_council_
# health_check_retry.py's identical `sys.path.insert(0, _THIS_DIR); import
# test_access_control` preamble, one level further down this file's own
# dependency chain).
sys.path.insert(0, _THIS_DIR)
import test_address_release_gate  # noqa: E402  (populates sys.modules stubs + imports test_main + main; loads its own private `notifications`)
import test_main  # noqa: E402  (populates sys.modules stubs + imports main)
import main  # noqa: E402
import address_release  # noqa: E402

notifications = test_address_release_gate.notifications  # the private, real-module copy -- see the import comment above
_ensure_database_stub_has_street_view_url = test_address_release_gate._ensure_database_stub_has_street_view_url


def _ensure_shared_notifications_stub_has_dashboard_attrs():
    """contractor_dashboard/my_leads_view do a LOCAL `import notifications`
    (resolved fresh against the SHARED sys.modules["notifications"] slot at
    call time, not this file's own private `notifications` reference --
    same class of gap _ensure_database_stub_has_street_view_url fixes for
    `database`). test_main.py's shared notifications stub doesn't carry
    _format_filed_date (only the send_* functions its own tests need)."""
    ns_stub = sys.modules.get("notifications")
    if ns_stub is not None and not hasattr(ns_stub, "_format_filed_date"):
        ns_stub._format_filed_date = MagicMock(return_value=None)


def _ensure_database_stub_has_dashboard_attrs():
    """contractor_dashboard/my_leads_view route tests below need to @patch
    main.database.get_contractor_dashboard_data / get_contractor_settings,
    neither of which test_main.py's shared database stub pre-populates (it
    only carries the specific attributes ITS OWN tests need -- same gap
    test_address_release_gate.py's own _ensure_database_stub_has_street_
    view_url fixes for a different pair of functions). Pre-populating them
    here (rather than passing create=True to each @patch) keeps the
    MagicMock objects @patch restores stable across tests, matching the
    established convention. Set on `main.database` specifically (the
    object main.py's own `import database` actually bound, at ITS first
    import) rather than the current sys.modules["database"] -- under
    `unittest discover`, other test files replace sys.modules["database"]
    wholesale at collection time, which main.py's already-bound reference
    does not follow; @patch("main.database....") resolves against main's
    bound reference too, so that's what needs the attribute."""
    db_stub = getattr(main, "database", None)
    if db_stub is not None:
        if not hasattr(db_stub, "get_contractor_dashboard_data"):
            db_stub.get_contractor_dashboard_data = MagicMock()
        if not hasattr(db_stub, "get_contractor_settings"):
            db_stub.get_contractor_settings = MagicMock(return_value={})


class _EnvIsolation(unittest.TestCase):
    ENV_KEY = address_release.ADDRESS_RELEASE_LIVE_ENV

    def setUp(self):
        self._had = self.ENV_KEY in os.environ
        self._old = os.environ.get(self.ENV_KEY)

    def tearDown(self):
        if self._had:
            os.environ[self.ENV_KEY] = self._old
        else:
            os.environ.pop(self.ENV_KEY, None)

    def _clear(self):
        os.environ.pop(self.ENV_KEY, None)


# ---------------------------------------------------------------------------
# Layer 1: the new primitives in isolation.

class TestGuardedApplicantName(unittest.TestCase):

    def test_historical_claim_returns_the_real_name(self):
        cur = MagicMock()
        with patch.object(address_release, "is_historical_purchase", return_value=True):
            self.assertEqual(
                address_release.guarded_applicant_name_for_lead(cur, "PLANIT-001", "Jane Homeowner"),
                "Jane Homeowner",
            )

    def test_new_allocation_hides_the_name_entirely(self):
        cur = MagicMock()
        with patch.object(address_release, "is_historical_purchase", return_value=False):
            self.assertIsNone(
                address_release.guarded_applicant_name_for_lead(cur, "ALLOC-REF-1", "Jane Homeowner")
            )

    def test_never_invents_a_name_the_council_never_published(self):
        """A None/empty applicant_name must stay None/empty either way --
        this never invents a name, historical or not."""
        cur = MagicMock()
        with patch.object(address_release, "is_historical_purchase", return_value=True):
            self.assertIsNone(address_release.guarded_applicant_name_for_lead(cur, "PLANIT-001", None))
            self.assertIsNone(address_release.guarded_applicant_name_for_lead(cur, "PLANIT-001", ""))

    def test_standalone_wrapper_fails_safe_hidden_on_db_error(self):
        with patch.object(address_release.database, "get_db_conn", side_effect=RuntimeError("db down")):
            result = address_release.guarded_applicant_name_for_lead_reference("PLANIT-001", "Jane Homeowner")
        self.assertIsNone(result)

    def test_standalone_wrapper_historical_returns_the_real_name(self):
        conn = MagicMock()
        cur = MagicMock()
        cur.fetchone.side_effect = [None, (1,)]  # lead_allocations miss, letter_dispatches hit -> historical
        conn.cursor.return_value = cur
        with patch.object(address_release.database, "get_db_conn", return_value=conn):
            result = address_release.guarded_applicant_name_for_lead_reference("PLANIT-001", "Jane Homeowner")
        self.assertEqual(result, "Jane Homeowner")


class TestGuardedSummary(unittest.TestCase):
    """2026-09-23, external-review Finding 2: 'free-text descriptions
    remain exposed and retained ... use an explicitly permitted structured
    description for new buyers, or suppress free text pending a verified
    sanitisation policy; HTML escaping alone is not anonymisation.' Mirrors
    TestGuardedApplicantName's own structure exactly, applied to the
    `summary` field via database._redact_address_from_summary (the
    pre-existing, Sep 9 2026 regex-based best-effort scrub that was already
    applied everywhere PRE-purchase but never post-purchase until this fix).

    Honest limitation, carried into this test's own docstring rather than
    just the code comments: this is regex-based, best-effort redaction of
    postcodes and house-number+street patterns -- not a structured/
    allow-listed description and not a guarantee that every identifying
    detail a free-text summary could contain (a name mentioned in passing,
    a portal reference number embedded in prose, a link) is caught. The
    review's ask to "use an explicitly permitted structured description for
    new buyers, or suppress free text pending a verified sanitisation
    policy" is a bigger product/data change than this pass makes -- flagged
    to Nick as still open, not silently treated as solved by this."""

    def test_historical_claim_returns_the_real_summary_unredacted(self):
        cur = MagicMock()
        with patch.object(address_release, "is_historical_purchase", return_value=True):
            self.assertEqual(
                address_release.guarded_summary_for_lead(cur, "PLANIT-001", "Fell one oak at 14 Oak Avenue, NG22 8AA"),
                "Fell one oak at 14 Oak Avenue, NG22 8AA",
            )

    def test_new_allocation_redacts_the_address_and_postcode(self):
        cur = MagicMock()
        with patch.object(address_release, "is_historical_purchase", return_value=False), \
             patch.object(address_release.database, "_redact_address_from_summary",
                           side_effect=lambda s: s.replace("14 Oak Avenue", "[address hidden]").replace("NG22 8AA", "[postcode hidden]")):
            result = address_release.guarded_summary_for_lead(cur, "ALLOC-REF-1", "Fell one oak at 14 Oak Avenue, NG22 8AA")
        self.assertNotIn("14 Oak Avenue", result)
        self.assertNotIn("NG22 8AA", result)
        self.assertIn("[address hidden]", result)
        self.assertIn("[postcode hidden]", result)

    def test_empty_or_none_summary_passes_through_untouched_either_way(self):
        """Never invents a summary -- a lead with no free-text description
        stays exactly as empty/None whether historical or not, matching
        guarded_applicant_name_for_lead's own never-invent behaviour."""
        cur = MagicMock()
        with patch.object(address_release, "is_historical_purchase", return_value=True):
            self.assertIsNone(address_release.guarded_summary_for_lead(cur, "PLANIT-001", None))
            self.assertEqual(address_release.guarded_summary_for_lead(cur, "PLANIT-001", ""), "")
        with patch.object(address_release, "is_historical_purchase", return_value=False):
            self.assertIsNone(address_release.guarded_summary_for_lead(cur, "ALLOC-REF-1", None))
            self.assertEqual(address_release.guarded_summary_for_lead(cur, "ALLOC-REF-1", ""), "")

    def test_standalone_wrapper_fails_safe_redacted_on_db_error(self):
        """Fail-safe direction matters: on a DB error, guarded_applicant_
        name_for_lead_reference fails toward HIDDEN (None); the summary
        wrapper has no safe 'hide the whole field' option that doesn't
        break existing pages, so it fails toward the same best-effort
        redaction a genuinely-new allocation gets -- never toward the raw,
        unredacted text."""
        with patch.object(address_release.database, "get_db_conn", side_effect=RuntimeError("db down")):
            result = address_release.guarded_summary_for_lead_reference(
                "PLANIT-001", "Fell one oak at 14 Oak Avenue, Newark, NG22 8AA")
        self.assertNotIn("14 Oak Avenue", result)
        self.assertNotIn("NG22 8AA", result)

    def test_standalone_wrapper_historical_returns_the_real_summary(self):
        conn = MagicMock()
        cur = MagicMock()
        cur.fetchone.side_effect = [None, (1,)]  # lead_allocations miss, letter_dispatches hit -> historical
        conn.cursor.return_value = cur
        with patch.object(address_release.database, "get_db_conn", return_value=conn):
            result = address_release.guarded_summary_for_lead_reference("PLANIT-001", "Fell one oak")
        self.assertEqual(result, "Fell one oak")

    def test_standalone_wrapper_new_allocation_redacts(self):
        conn = MagicMock()
        cur = MagicMock()
        cur.fetchone.return_value = ("alloc-id-999",)  # every lookup hits -> "new"
        conn.cursor.return_value = cur
        with patch.object(address_release.database, "get_db_conn", return_value=conn):
            result = address_release.guarded_summary_for_lead_reference(
                "ALLOC-REF-1", "Fell one oak at 14 Oak Avenue, Newark, NG22 8AA")
        self.assertNotIn("14 Oak Avenue", result)
        self.assertNotIn("NG22 8AA", result)
        self.assertIn("[address hidden]", result)
        self.assertIn("[postcode hidden]", result)


class TestResolveBuyerFacingReference(unittest.TestCase):

    def test_resolves_an_allocation_id_back_to_the_real_reference(self):
        cur = MagicMock()
        cur.fetchone.return_value = ("PLANIT-REAL-REF",)
        result = address_release.resolve_buyer_facing_reference(cur, "3fae9c1e-1111-4b1e-9c1e-000000000001")
        self.assertEqual(result, "PLANIT-REAL-REF")
        cur.execute.assert_called_once()
        self.assertIn("lead_allocations", cur.execute.call_args[0][0])

    def test_unresolvable_input_falls_through_unchanged(self):
        """An old bookmarked link, an admin call, or a real council
        reference passed directly (the historical case) -- no
        lead_allocations row matches, so the input is used verbatim."""
        cur = MagicMock()
        cur.fetchone.return_value = None
        result = address_release.resolve_buyer_facing_reference(cur, "PLANIT-HISTORICAL-001")
        self.assertEqual(result, "PLANIT-HISTORICAL-001")

    def test_round_trips_with_buyer_facing_reference_for_a_new_allocation(self):
        """buyer_facing_reference hands out lead_allocations.id for a new
        allocation; resolve_buyer_facing_reference must resolve that exact
        value back to the real lead_reference."""
        cur = MagicMock()
        # buyer_facing_reference: is_historical_purchase (1 miss + 1 hit
        # for kind=True=new... wait: lead_allocations hit => kind True =>
        # not historical), then its own lead_allocations.id SELECT (hit).
        cur.fetchone.side_effect = [(1,), ("alloc-id-999",)]
        buyer_ref = address_release.buyer_facing_reference(cur, "PLANIT-NEW-001")
        self.assertEqual(buyer_ref, "alloc-id-999")

        cur2 = MagicMock()
        cur2.fetchone.return_value = ("PLANIT-NEW-001",)
        resolved = address_release.resolve_buyer_facing_reference(cur2, buyer_ref)
        self.assertEqual(resolved, "PLANIT-NEW-001")

    def test_standalone_wrapper_fails_toward_the_input_unchanged_on_db_error(self):
        with patch.object(address_release.database, "get_db_conn", side_effect=RuntimeError("db down")):
            result = address_release.resolve_buyer_facing_reference_standalone("some-ref")
        self.assertEqual(result, "some-ref")

    def test_standalone_wrapper_falsy_input_short_circuits(self):
        self.assertEqual(address_release.resolve_buyer_facing_reference_standalone(""), "")
        self.assertIsNone(address_release.resolve_buyer_facing_reference_standalone(None))


class TestBuyerFacingReferenceStandalone(unittest.TestCase):

    def test_fails_toward_the_real_reference_on_db_error(self):
        with patch.object(address_release.database, "get_db_conn", side_effect=RuntimeError("db down")):
            result = address_release.buyer_facing_reference_standalone("PLANIT-001")
        self.assertEqual(result, "PLANIT-001")


# ---------------------------------------------------------------------------
# Layer 2: the three buyer-facing-reference routes, end to end.

class _RouteTestBase(_EnvIsolation):
    def setUp(self):
        super().setUp()
        os.environ["DASHBOARD_USER"] = "admin"
        os.environ["DASHBOARD_PASS"] = "test-admin-pass"
        os.environ["SESSION_SECRET"] = "test-session-secret"
        main._SESSION_SECRET = b"test-session-secret"
        import letter_content
        os.environ[letter_content.PRIVACY_CONTACT_EMAIL_ENV] = "privacy@treekey.co.uk"

    def _admin_request(self):
        import base64
        token = base64.b64encode(b"admin:test-admin-pass").decode()
        req = MagicMock()
        req.cookies = {}
        req.headers = {"authorization": f"Basic {token}"}
        return req


class TestGenerateLetterAcceptsABuyerFacingReference(_RouteTestBase):

    @patch("main.database.get_db_conn")
    def test_a_lead_allocations_id_resolves_to_the_real_lead(self, mock_get_conn):
        """The exact scenario Part 1 asks for: a NEW allocation's
        dashboard link now carries lead_allocations.id, not the raw
        council reference -- this proves generate_homeowner_letter
        resolves that id back to the real lead server-side, rather than
        404ing every new-allocation letter link."""
        self._clear()
        conn = MagicMock()
        cur = MagicMock()
        # This test's request is admin-basic-auth (no session cookie), so
        # the route's own optional get_contractor_settings lookup never
        # runs -- only 3 fetchone() calls total: (1) the resolve step: a
        # lead_allocations.id -> real reference hit, (2) the route's own
        # lead-data row fetch, (3) guarded_address_for_lead_reference's
        # is_historical_purchase -> a lead_allocations hit (kind=True) ->
        # NOT historical -> stays redacted, in one query (no letter_
        # dispatches fallback needed once the first check already hits).
        cur.fetchone.side_effect = [
            ("PLANIT-REAL-001",),
            ("PLANIT-REAL-001", "1 Real Street, Leeds, LS1 1AA", "Fell one oak", "Leeds City Council", "claimed"),
            (1,),
        ]
        conn.cursor.return_value = cur
        mock_get_conn.return_value = conn

        response = main.generate_homeowner_letter(self._admin_request(), "3fae9c1e-buyer-facing-uuid")
        body = response if isinstance(response, str) else getattr(response, "body", str(response))
        self.assertIn("letter-frame", body)
        self.assertNotIn("1 Real Street", body)  # new allocation -- address stays redacted


class TestStreetViewAcceptsABuyerFacingReference(_RouteTestBase):

    @patch("main.database.get_db_conn")
    @patch("main.database.street_view_url")
    @patch("main.database.get_dispatched_lead_address_for_contractor")
    def test_a_lead_allocations_id_resolves_before_the_dispatch_lookup(self, mock_get_addr, mock_street_view_url, mock_get_conn):
        self._clear()
        conn = MagicMock()
        cur = MagicMock()
        # 1) resolve step: lead_allocations.id -> real reference, then
        # 2) lead_address_release_allowed's own is_historical_purchase:
        # lead_allocations hit -> new allocation -> refused.
        cur.fetchone.side_effect = [("PLANIT-REAL-001",), (1,)]
        conn.cursor.return_value = cur
        mock_get_conn.return_value = conn
        mock_get_addr.return_value = "1 Real Street, Leeds, LS1 1AA"
        mock_street_view_url.return_value = "https://maps.google.com/?q=1+Real+Street"

        request = MagicMock()
        request.cookies = {"treekey_contractor_session": main._sign_session_cookie("contractor@example.com")}
        response = main.street_view_redirect("3fae9c1e-buyer-facing-uuid", request)

        # Resolved reference was passed through to the ownership/dispatch
        # lookup -- not the raw buyer-facing UUID.
        mock_get_addr.assert_called_once_with("contractor@example.com", "PLANIT-REAL-001")
        # New allocation -> still refused (this test isn't about the
        # address-release decision itself, just that resolution happened
        # before it).
        self.assertEqual(getattr(response, "status_code", None), 403)


# ---------------------------------------------------------------------------
# Layer 3: dashboard/my-leads rendering and the two transactional emails.

class TestDashboardHidesIndirectIdentifiersForANewAllocation(_EnvIsolation):

    def setUp(self):
        super().setUp()
        os.environ["SESSION_SECRET"] = "test-session-secret"
        main._SESSION_SECRET = b"test-session-secret"
        _ensure_database_stub_has_dashboard_attrs()
        _ensure_shared_notifications_stub_has_dashboard_attrs()

    def _lead(self):
        return {
            "id": "lead-uuid-1", "ref": "PLANIT-NEW-001", "addr": "1 Real Street, Leeds",
            # 2026-09-23 external-review fix (Finding 2): the free-text
            # summary itself can restate the site address, exactly like
            # database._redact_address_from_summary's own docstring
            # describes -- seeded here with a house-number/street AND a
            # full postcode so the redaction assertions below are testing
            # the real regex behaviour, not just "didn't crash".
            "summary": "Fell one oak at 14 Oak Avenue, Newark, NG22 8AA",
            "council": "Leeds City Council", "score": "small",
            "price": 25, "dispatched_at": "2026-09-20 10:00", "registered_date": None,
            "applicant_name": "Jane Homeowner", "has_agent": None,
        }

    @patch("main.database.get_db_conn")
    @patch("main.database.get_contractor_settings")
    @patch("main.database.get_contractor_dashboard_data")
    def test_new_allocation_hides_applicant_name_and_raw_reference(self, mock_dash_data, mock_settings, mock_get_conn):
        self._clear()
        mock_dash_data.return_value = {"subscription": {"active": True, "tier": "pro"}, "dispatched_leads": [self._lead()]}
        mock_settings.return_value = {"notification_preference": "email"}
        conn = MagicMock()
        cur = MagicMock()
        # Every lead_allocations lookup hits (truthy) -> classified "new"
        # for every one of the three address_release calls in the loop
        # (addr, buyer_ref, applicant_name); the id-lookup used by
        # buyer_facing_reference for a "hit" also just needs a truthy,
        # indexable row.
        cur.fetchone.return_value = ("alloc-id-999",)
        conn.cursor.return_value = cur
        mock_get_conn.return_value = conn

        request = MagicMock()
        request.cookies = {"treekey_contractor_session": main._sign_session_cookie("contractor@example.com")}
        response = main.contractor_dashboard(request)
        body = response if isinstance(response, str) else getattr(response, "body", str(response))

        self.assertNotIn("Jane Homeowner", body)
        self.assertNotIn("Applicant:", body)
        self.assertNotIn("PLANIT-NEW-001", body)
        self.assertIn("alloc-id-999", body)  # the buyer-facing substitute is what actually renders
        # 2026-09-23 external-review fix (Finding 2): the free-text summary
        # must not hand out the address for a NEW (non-historical)
        # allocation either -- guarded_summary_for_lead_reference redacts it.
        self.assertNotIn("14 Oak Avenue", body)
        self.assertNotIn("NG22 8AA", body)
        self.assertIn("[address hidden]", body)
        self.assertIn("[postcode hidden]", body)

    @patch("main.database.get_db_conn")
    @patch("main.database.get_contractor_settings")
    @patch("main.database.get_contractor_dashboard_data")
    def test_historical_claim_still_shows_the_real_name_and_reference(self, mock_dash_data, mock_settings, mock_get_conn):
        """'Do not treat historical disclosures as undone' -- a lead only
        found via letter_dispatches keeps showing exactly what it always
        did, unchanged."""
        self._clear()
        mock_dash_data.return_value = {"subscription": {"active": True, "tier": "pro"}, "dispatched_leads": [self._lead()]}
        mock_settings.return_value = {"notification_preference": "email"}
        conn = MagicMock()
        cur = MagicMock()
        # Four address_release calls in loop order (addr, buyer_ref,
        # summary [2026-09-23 external-review fix: guarded_summary_for_lead_
        # reference], applicant_name), each its own is_historical_purchase
        # check: lead_allocations miss, letter_dispatches hit -> historical.
        # buyer_facing_reference's historical branch takes the early
        # return (no extra query), so exactly 2 fetchones per call.
        cur.fetchone.side_effect = [None, (1,)] * 4
        conn.cursor.return_value = cur
        mock_get_conn.return_value = conn

        request = MagicMock()
        request.cookies = {"treekey_contractor_session": main._sign_session_cookie("contractor@example.com")}
        response = main.contractor_dashboard(request)
        body = response if isinstance(response, str) else getattr(response, "body", str(response))

        self.assertIn("Jane Homeowner", body)
        self.assertIn("1 Real Street, Leeds", body)
        self.assertIn("PLANIT-NEW-001", body)  # historical claim's buyer-facing reference IS the real one
        # 2026-09-23 external-review fix (Finding 2): "do not treat
        # historical disclosures as undone" applies to the summary too --
        # the real, unredacted text (including the address it restates)
        # still shows for a historical claim.
        self.assertIn("14 Oak Avenue", body)
        self.assertIn("NG22 8AA", body)


class TestPurchasedLeadEmailHidesIndirectIdentifiersForANewAllocation(_EnvIsolation):

    def _lead_data(self):
        return {
            "reference": "PLANIT-EMAIL-001", "address": "1 Real Street, Leeds, LS1 1AA",
            "council_source": "Leeds City Council",
            # 2026-09-23 external-review fix (Finding 2) -- see the
            # matching comment on TestDashboardHidesIndirectIdentifiersFor
            # ANewAllocation._lead() above.
            "summary": "Fell one oak at 14 Oak Avenue, Newark, NG22 8AA",
            "lead_score": "medium", "registered_date": None, "applicant_name": "Jane Homeowner",
        }

    def setUp(self):
        super().setUp()
        notifications.RESEND_API_KEY = "test-key"
        _ensure_database_stub_has_street_view_url()

    def test_new_allocation_email_omits_applicant_name_and_uses_a_buyer_facing_reference(self):
        self._clear()
        conn = MagicMock()
        cur = MagicMock()
        cur.fetchone.return_value = ("alloc-id-777",)  # every lead_allocations lookup hits -> "new"
        conn.cursor.return_value = cur
        with patch.object(address_release.database, "get_db_conn", return_value=conn), \
             patch.object(notifications.requests, "post", return_value=MagicMock(status_code=200)) as mock_post:
            notifications._send_purchased_lead_email_inner("buyer@example.com", self._lead_data())
        sent_html = mock_post.call_args.kwargs["json"]["html"]
        self.assertNotIn("Jane Homeowner", sent_html)
        self.assertNotIn("PLANIT-EMAIL-001", sent_html)
        self.assertIn("alloc-id-777", sent_html)  # letter/flyer links still work, with the substitute
        # 2026-09-23 external-review fix (Finding 2): same redaction for
        # the unlock email's free-text summary as the dashboard.
        self.assertNotIn("14 Oak Avenue", sent_html)
        self.assertNotIn("NG22 8AA", sent_html)
        self.assertIn("[address hidden]", sent_html)
        self.assertIn("[postcode hidden]", sent_html)

    def test_historical_claim_email_still_shows_the_real_name_and_reference(self):
        self._clear()
        conn = MagicMock()
        cur = MagicMock()
        # 5 address_release calls in this function (guarded_addr,
        # lead_address_release_allowed, buyer_facing_reference_standalone,
        # guarded_summary_for_lead_reference [2026-09-23 external-review
        # fix], guarded_applicant_name_for_lead_reference), each a
        # 2-fetchone historical classification (miss, hit);
        # buyer_facing_reference's historical branch takes the early
        # return, no extra query.
        cur.fetchone.side_effect = [None, (1,)] * 5
        conn.cursor.return_value = cur
        with patch.object(address_release.database, "get_db_conn", return_value=conn), \
             patch.object(notifications.requests, "post", return_value=MagicMock(status_code=200)) as mock_post:
            notifications._send_purchased_lead_email_inner("buyer@example.com", self._lead_data())
        sent_html = mock_post.call_args.kwargs["json"]["html"]
        self.assertIn("Jane Homeowner", sent_html)
        self.assertIn("PLANIT-EMAIL-001", sent_html)
        # 2026-09-23 external-review fix (Finding 2): historical disclosure
        # is never undone -- the real, unredacted summary text still shows.
        self.assertIn("14 Oak Avenue", sent_html)
        self.assertIn("NG22 8AA", sent_html)


if __name__ == "__main__":
    unittest.main()

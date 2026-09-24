"""
test_lead_action_links_and_flyer_claims.py -- 2026-09-24 handoff.

Nick's task: "Inspect actual account/dashboard/my-leads links and
distinguish: Letter Settings; fictional template preview; historical
per-lead letter; street flyer. For new mailed-introduction purchases,
replace misleading old actions with the appropriate template preview or
mailing-status action. Do not offer address-dependent tools that the
customer cannot legitimately use." Also: "Remove unconditional claims from
the separate street-flyer route: 'NPTC Certified', '£5M Insured' and '20%
Same-Day Street Discount'."

Covers:
  1. my_leads_view / contractor_dashboard / free_dashboard: a NEW
     allocation's card shows "Preview Letter" (never the old "Letter"
     label, which implied this is the actual posted letter), never offers
     "Street Flyer" at all (lead_address_release_allowed is structurally
     False for anything but a historical claim -- offering it would always
     404/403), and shows an honest mailing-status line when one is on
     record. A HISTORICAL claim is untouched -- both "Letter" and "Street
     Flyer" still work exactly as before (real address, both routes
     genuinely usable).
  2. fulfilment.get_letter_status_label_for_lead_reference: the status
     mapping in isolation, including the is_dry_run override and the
     fail-toward-None posture on a missing row or a DB error.
  3. generate_street_flyer: the old hardcoded "NPTC Certified", "£5M
     Insured" and "20% Same-Day Street Discount" claims never render for
     any contractor, a contractor's own real, saved insurance/
     qualifications notes DO render (reusing the exact mechanism
     letter_content.render_letter already uses for the homeowner letter),
     and a contractor with neither saved just gets no credentials line at
     all -- never a fabricated one.

Run with:
    python -m unittest tests.test_lead_action_links_and_flyer_claims -v
"""
import os
import sys
import unittest
from unittest.mock import MagicMock, patch

_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
_APP_DIR = os.path.dirname(_THIS_DIR)
sys.path.insert(0, _APP_DIR)
sys.path.insert(0, _THIS_DIR)

# Reuses the established fastapi/database/notifications stubbing chain --
# see test_indirect_identifier_gating.py's own import comment for exactly
# what this pulls in and why importing it (rather than duplicating ~150
# lines of stub setup) is this codebase's own convention.
import test_indirect_identifier_gating as _tiig  # noqa: E402
import main  # noqa: E402
import fulfilment  # noqa: E402
import address_release  # noqa: E402
import letter_content  # noqa: E402

_ensure_database_stub_has_dashboard_attrs = _tiig._ensure_database_stub_has_dashboard_attrs
_ensure_shared_notifications_stub_has_dashboard_attrs = _tiig._ensure_shared_notifications_stub_has_dashboard_attrs


class _CapturingHTMLResponse:
    """The shared fastapi stub's HTMLResponse (test_main.py's own
    _FakeHTMLResponse, which this whole chain's sys.modules["fastapi.
    responses"] resolves to) discards every constructor argument -- `def
    __init__(self, *a, **k): pass` -- including the rendered content
    itself. That's harmless for routes tests only assert_called_with on,
    but my_leads_view and free_dashboard both `return HTMLResponse(f"\"\"...
    \"\"\")` (unlike contractor_dashboard/generate_homeowner_letter/
    generate_street_flyer, which return bare f-strings), so this codebase's
    established `body = response if isinstance(response, str) else
    getattr(response, "body", str(response))` extraction pattern silently
    got the object's repr instead of real HTML for those two. Patching
    main.HTMLResponse to this capturing fake -- only for the tests that
    need it -- stores the positional `content` arg as `.body`, matching
    the attribute name real starlette.responses.HTMLResponse exposes, so
    that same established extraction pattern works here too. No production
    code changes; this is a test-only fix for a pre-existing stub gap that
    no earlier test happened to hit."""
    def __init__(self, content="", status_code=200, **kwargs):
        self.body = content
        self.status_code = status_code
        self._cookies = {}

    def set_cookie(self, key=None, value=None, *a, **k):
        self._cookies[key] = value


def _lead(ref="PLANIT-NEW-001", addr="1 Real Street, Leeds"):
    return {
        "id": "lead-uuid-1", "ref": ref, "addr": addr,
        "summary": "Fell one oak", "council": "Leeds City Council", "score": "small",
        "price": 25, "dispatched_at": "2026-09-20 10:00", "registered_date": None,
        "applicant_name": "Jane Homeowner", "has_agent": None,
    }


class _DashboardTestBase(unittest.TestCase):
    def setUp(self):
        os.environ["SESSION_SECRET"] = "test-session-secret"
        main._SESSION_SECRET = b"test-session-secret"
        _ensure_database_stub_has_dashboard_attrs()
        _ensure_shared_notifications_stub_has_dashboard_attrs()

    def _request(self, email="contractor@example.com"):
        request = MagicMock()
        request.cookies = {"treekey_contractor_session": main._sign_session_cookie(email)}
        return request


class TestMyLeadsViewNewAllocation(_DashboardTestBase):

    @patch("main.HTMLResponse", _CapturingHTMLResponse)
    @patch("fulfilment.get_letter_status_label_for_lead_reference", return_value="Preparing to post")
    @patch("main.database.get_db_conn")
    @patch("main.database.get_contractor_settings")
    @patch("main.database.get_contractor_dashboard_data")
    @patch("main.database.get_contractor_subscription")
    def test_new_allocation_shows_preview_letter_no_street_flyer_and_a_status_line(
        self, mock_sub, mock_dash_data, mock_settings, mock_get_conn, mock_status
    ):
        mock_sub.return_value = {"active": True}
        mock_dash_data.return_value = {"dispatched_leads": [_lead()]}
        mock_settings.return_value = {"notification_preference": "email"}
        conn = MagicMock()
        cur = MagicMock()
        cur.fetchone.return_value = ("alloc-id-999",)  # every address_release call -> "new allocation"
        conn.cursor.return_value = cur
        mock_get_conn.return_value = conn

        response = main.my_leads_view(self._request())
        body = response if isinstance(response, str) else getattr(response, "body", str(response))

        self.assertIn("Preview Letter", body)
        self.assertNotIn(">Letter<", body)
        self.assertNotIn("Street Flyer", body)
        self.assertIn("Mailed introduction:", body)
        self.assertIn("Preparing to post", body)

    @patch("main.HTMLResponse", _CapturingHTMLResponse)
    @patch("main.database.get_db_conn")
    @patch("main.database.get_contractor_settings")
    @patch("main.database.get_contractor_dashboard_data")
    @patch("main.database.get_contractor_subscription")
    def test_new_allocation_with_no_obligation_on_record_shows_no_status_line(
        self, mock_sub, mock_dash_data, mock_settings, mock_get_conn
    ):
        """fulfilment.get_letter_status_label_for_lead_reference itself
        opens its own DB connection (database.get_db_conn(), patched here
        too via the same mock) and, given this test's blanket 1-tuple
        return_value, fails its own unpack safely and returns None -- the
        status line must be omitted entirely, never a fabricated one."""
        mock_sub.return_value = {"active": True}
        mock_dash_data.return_value = {"dispatched_leads": [_lead()]}
        mock_settings.return_value = {"notification_preference": "email"}
        conn = MagicMock()
        cur = MagicMock()
        cur.fetchone.return_value = ("alloc-id-999",)
        conn.cursor.return_value = cur
        mock_get_conn.return_value = conn

        response = main.my_leads_view(self._request())
        body = response if isinstance(response, str) else getattr(response, "body", str(response))

        self.assertIn("Preview Letter", body)
        self.assertNotIn("Mailed introduction:", body)


class TestMyLeadsViewIntroductionRecord(_DashboardTestBase):
    """2026-09-24 handoff ("My Introductions" account view): the richer
    per-introduction block that renders when fulfilment.
    get_introduction_record_for_lead_reference returns a record (i.e. a
    sale that went through the fulfilment pipeline, not the default legacy
    one). Verifies the field list the handoff actually asked for -- opaque
    reference (already covered by TestMyLeadsViewNewAllocation), category/
    area, template version, provider evidence + the explicit
    no-document-exists caveat, the dry-run note, and the address-free
    preview link -- and that acceptance is never worded as dispatch."""

    def setUp(self):
        super().setUp()
        # database.classify_job_category / _extract_outcodes /
        # get_outcode_area_label aren't pre-populated on the shared stub
        # (test_indirect_identifier_gating.py's _ensure_database_stub_has_
        # dashboard_attrs only carries the pair ITS OWN tests need) -- same
        # gap that function's own docstring documents for a different pair
        # of attributes, fixed here the same way: set directly on main's
        # already-bound `database` reference.
        db_stub = getattr(main, "database", None)
        if db_stub is not None:
            db_stub.classify_job_category = MagicMock(return_value={"key": "felling", "label": "Felling & Removal"})
            db_stub._extract_outcodes = MagicMock(return_value=["NG22"])
            db_stub.get_outcode_area_label = MagicMock(return_value={"label": "NG22, Newark and Sherwood"})

    @patch("main.HTMLResponse", _CapturingHTMLResponse)
    @patch("fulfilment.get_introduction_record_for_lead_reference")
    @patch("main.database.get_db_conn")
    @patch("main.database.get_contractor_settings")
    @patch("main.database.get_contractor_dashboard_data")
    @patch("main.database.get_contractor_subscription")
    def test_dispatched_record_shows_full_evidence_never_claims_delivery(
        self, mock_sub, mock_dash_data, mock_settings, mock_get_conn, mock_intro
    ):
        mock_sub.return_value = {"active": True}
        mock_dash_data.return_value = {"dispatched_leads": [_lead()]}
        mock_settings.return_value = {"notification_preference": "email"}
        conn = MagicMock()
        cur = MagicMock()
        cur.fetchone.return_value = ("alloc-id-999",)  # every address_release call -> "new allocation"
        conn.cursor.return_value = cur
        mock_get_conn.return_value = conn
        mock_intro.return_value = {
            "stage_key": "dispatched", "stage_label": "Dispatch confirmed",
            "stage_explanation": "Our mailing provider has confirmed this has left their system for posting. "
                                  "We have no way to confirm actual delivery to the homeowner.",
            "is_dry_run": False, "template_version": 3, "provider_name": "Stannp",
            "provider_reference": "STANNP-REF-999", "provider_accepted_at": "2026-09-20 09:00:00",
            "dispatched_at": "2026-09-20 15:00:00", "failed_at": None,
        }

        response = main.my_leads_view(self._request())
        body = response if isinstance(response, str) else getattr(response, "body", str(response))

        self.assertIn("Dispatch confirmed", body)
        self.assertNotIn("Delivered", body)
        self.assertIn("Felling &amp; Removal", body)
        self.assertIn("Newark and Sherwood", body)
        self.assertIn("Letter template: v3", body)
        self.assertIn("Stannp", body)
        self.assertIn("STANNP-REF-999", body)
        self.assertIn("No downloadable proof-of-postage document is available", body)
        self.assertIn("/letter-settings/preview", body)

    @patch("main.HTMLResponse", _CapturingHTMLResponse)
    @patch("fulfilment.get_introduction_record_for_lead_reference")
    @patch("main.database.get_db_conn")
    @patch("main.database.get_contractor_settings")
    @patch("main.database.get_contractor_dashboard_data")
    @patch("main.database.get_contractor_subscription")
    def test_provider_accepted_is_not_worded_as_dispatch(
        self, mock_sub, mock_dash_data, mock_settings, mock_get_conn, mock_intro
    ):
        mock_sub.return_value = {"active": True}
        mock_dash_data.return_value = {"dispatched_leads": [_lead()]}
        mock_settings.return_value = {"notification_preference": "email"}
        conn = MagicMock()
        cur = MagicMock()
        cur.fetchone.return_value = ("alloc-id-999",)
        conn.cursor.return_value = cur
        mock_get_conn.return_value = conn
        mock_intro.return_value = {
            "stage_key": "submitted", "stage_label": "Submitted to postal provider",
            "stage_explanation": "Our mailing provider has accepted this for printing and posting. It has not "
                                  "left their system yet, so this is acceptance, not dispatch.",
            "is_dry_run": False, "template_version": 1, "provider_name": "Stannp",
            "provider_reference": "STANNP-REF-1", "provider_accepted_at": "2026-09-20 09:00:00",
            "dispatched_at": None, "failed_at": None,
        }

        response = main.my_leads_view(self._request())
        body = response if isinstance(response, str) else getattr(response, "body", str(response))

        self.assertIn("Submitted to postal provider", body)
        self.assertNotIn("Dispatch confirmed", body)

    @patch("main.HTMLResponse", _CapturingHTMLResponse)
    @patch("fulfilment.get_introduction_record_for_lead_reference")
    @patch("main.database.get_db_conn")
    @patch("main.database.get_contractor_settings")
    @patch("main.database.get_contractor_dashboard_data")
    @patch("main.database.get_contractor_subscription")
    def test_dry_run_shows_test_mode_note(
        self, mock_sub, mock_dash_data, mock_settings, mock_get_conn, mock_intro
    ):
        mock_sub.return_value = {"active": True}
        mock_dash_data.return_value = {"dispatched_leads": [_lead()]}
        mock_settings.return_value = {"notification_preference": "email"}
        conn = MagicMock()
        cur = MagicMock()
        cur.fetchone.return_value = ("alloc-id-999",)
        conn.cursor.return_value = cur
        mock_get_conn.return_value = conn
        mock_intro.return_value = {
            "stage_key": "preparing", "stage_label": "Preparing",
            "stage_explanation": "This introduction is being prepared in our test/practice sending mode.",
            "is_dry_run": True, "template_version": None, "provider_name": None,
            "provider_reference": None, "provider_accepted_at": None,
            "dispatched_at": None, "failed_at": None,
        }

        response = main.my_leads_view(self._request())
        body = response if isinstance(response, str) else getattr(response, "body", str(response))

        self.assertIn("Test/practice sending mode", body)
        self.assertIn("Letter template: not yet finalised", body)
        self.assertIn("No mailing provider evidence recorded yet", body)


class TestMyLeadsViewHistorical(_DashboardTestBase):

    @patch("main.HTMLResponse", _CapturingHTMLResponse)
    @patch("main.database.get_db_conn")
    @patch("main.database.get_contractor_settings")
    @patch("main.database.get_contractor_dashboard_data")
    @patch("main.database.get_contractor_subscription")
    def test_historical_claim_keeps_the_real_letter_and_street_flyer_buttons(
        self, mock_sub, mock_dash_data, mock_settings, mock_get_conn
    ):
        mock_sub.return_value = {"active": True}
        mock_dash_data.return_value = {"dispatched_leads": [_lead()]}
        mock_settings.return_value = {"notification_preference": "email"}
        conn = MagicMock()
        cur = MagicMock()
        # Four address_release calls in the loop (addr, buyer_ref, summary,
        # applicant_name), each its own is_historical_purchase check:
        # lead_allocations miss (None), letter_dispatches hit ((1,)) ->
        # historical. buyer_facing_reference's historical branch takes the
        # early return (no extra query) -- exactly 2 fetchones per call.
        cur.fetchone.side_effect = [None, (1,)] * 4
        conn.cursor.return_value = cur
        mock_get_conn.return_value = conn

        response = main.my_leads_view(self._request())
        body = response if isinstance(response, str) else getattr(response, "body", str(response))

        self.assertIn(">Letter<", body)
        self.assertIn("Street Flyer", body)
        self.assertNotIn("Preview Letter", body)


class TestContractorDashboardNewAllocation(_DashboardTestBase):

    @patch("fulfilment.get_letter_status_label_for_lead_reference", return_value="Posted")
    @patch("main.database.get_db_conn")
    @patch("main.database.get_contractor_settings")
    @patch("main.database.get_contractor_dashboard_data")
    def test_new_allocation_shows_preview_letter_no_street_flyer_and_a_status_line(
        self, mock_dash_data, mock_settings, mock_get_conn, mock_status
    ):
        mock_dash_data.return_value = {"subscription": {"active": True, "tier": "pro"}, "dispatched_leads": [_lead()]}
        mock_settings.return_value = {"notification_preference": "email"}
        conn = MagicMock()
        cur = MagicMock()
        cur.fetchone.return_value = ("alloc-id-999",)
        conn.cursor.return_value = cur
        mock_get_conn.return_value = conn

        response = main.contractor_dashboard(self._request())
        body = response if isinstance(response, str) else getattr(response, "body", str(response))

        self.assertIn("Preview Letter", body)
        self.assertNotIn(">Letter<", body)
        self.assertNotIn("Street Flyer", body)
        self.assertIn("Mailed introduction:", body)
        self.assertIn("Posted", body)


class TestContractorDashboardHistorical(_DashboardTestBase):

    @patch("main.database.get_db_conn")
    @patch("main.database.get_contractor_settings")
    @patch("main.database.get_contractor_dashboard_data")
    def test_historical_claim_keeps_the_real_letter_and_street_flyer_buttons(
        self, mock_dash_data, mock_settings, mock_get_conn
    ):
        mock_dash_data.return_value = {"subscription": {"active": True, "tier": "pro"}, "dispatched_leads": [_lead()]}
        mock_settings.return_value = {"notification_preference": "email"}
        conn = MagicMock()
        cur = MagicMock()
        cur.fetchone.side_effect = [None, (1,)] * 4
        conn.cursor.return_value = cur
        mock_get_conn.return_value = conn

        response = main.contractor_dashboard(self._request())
        body = response if isinstance(response, str) else getattr(response, "body", str(response))

        self.assertIn(">Letter<", body)
        self.assertIn("Street Flyer", body)
        self.assertNotIn("Preview Letter", body)


class TestFreeDashboardToolCards(_DashboardTestBase):

    @patch("main.HTMLResponse", _CapturingHTMLResponse)
    @patch("fulfilment.get_letter_status_label_for_lead_reference", return_value=None)
    @patch("main.database.get_db_conn")
    @patch("main.database.get_contractor_settings")
    @patch("main.database.get_limbo_account")
    def test_free_lead_shows_preview_letter_and_never_street_flyer(
        self, mock_limbo, mock_settings, mock_get_conn, mock_status
    ):
        mock_limbo.return_value = {"free_lead_ref": "PLANIT-FREE-001", "customer_name": None, "signed_up_at": None}
        mock_settings.return_value = {"notification_preference": "email"}
        conn = MagicMock()
        cur = MagicMock()
        cur.fetchone.return_value = ("alloc-id-777",)
        conn.cursor.return_value = cur
        mock_get_conn.return_value = conn

        with patch("main.database.get_lead_by_reference", return_value={
            "reference": "PLANIT-FREE-001", "address": "1 Real Street, Leeds", "summary": "Fell one oak",
            "council_source": "Leeds City Council", "registered_date": None,
        }):
            response = main.free_dashboard(self._request())
        body = response if isinstance(response, str) else getattr(response, "body", str(response))

        self.assertIn("Preview Intro Letter", body)
        self.assertNotIn("Street Flyer", body)


class TestLetterStatusLabelForLeadReference(unittest.TestCase):
    """fulfilment.get_letter_status_label_for_lead_reference in isolation.

    NOTE on `create=True` (found chasing a full-suite-only failure, same
    class of bug test_magic_link_credential_exposure.py's own NOTE already
    documents): fulfilment.py has no top-level `import database` -- it
    can't, database.py itself does `import fulfilment` at ITS top level
    (see database.py line ~15), so a top-level import here would be a
    genuine circular import, not just a test inconvenience. This function
    therefore does a plain `import database` INSIDE its own body, which
    means it re-resolves sys.modules["database"] fresh on every call --
    unlike main.py's own `import database` (bound once, at main's first
    import) or address_release.py's. Under `unittest discover`,
    tests/test_letter_promise_gate.py unconditionally dels and replaces
    sys.modules["database"] with a bare, attribute-less module at ITS OWN
    collection time (its own comment explains why that's safe for main.py/
    address_release.py specifically, since those bind "database" once and
    never look at sys.modules again) -- and because discover() finishes
    importing every test file before running any of them, that bare
    replacement is already in place by the time these tests actually run,
    regardless of file order. patch("database.get_db_conn", ...) needs the
    attribute to already exist on whatever sys.modules["database"] holds
    AT PATCH TIME to work without `create=True` -- it didn't, hence
    `AttributeError: <module 'database'> does not have the attribute
    'get_db_conn'` under the full suite despite this file passing every
    time it was run standalone. `create=True` is the correct fix here
    (rather than patching a bound reference like main.database, which
    fulfilment's fresh per-call import does not use)."""

    def _with_row(self, status, is_dry_run):
        conn = MagicMock()
        cur = MagicMock()
        cur.fetchone.return_value = (status, is_dry_run)
        conn.cursor.return_value = cur
        return conn

    def test_empty_reference_short_circuits(self):
        self.assertIsNone(fulfilment.get_letter_status_label_for_lead_reference(""))

    def test_no_row_returns_none(self):
        conn = MagicMock()
        cur = MagicMock()
        cur.fetchone.return_value = None
        conn.cursor.return_value = cur
        with patch("database.get_db_conn", return_value=conn, create=True):
            self.assertIsNone(fulfilment.get_letter_status_label_for_lead_reference("PLANIT-1"))

    def test_db_error_fails_toward_none_not_a_crash(self):
        with patch("database.get_db_conn", side_effect=RuntimeError("db down"), create=True):
            self.assertIsNone(fulfilment.get_letter_status_label_for_lead_reference("PLANIT-1"))

    def test_dispatched_and_not_dry_run_is_posted(self):
        conn = self._with_row("dispatched", False)
        with patch("database.get_db_conn", return_value=conn, create=True):
            self.assertEqual(fulfilment.get_letter_status_label_for_lead_reference("PLANIT-1"), "Posted")

    def test_dry_run_never_claims_posted_even_if_status_says_dispatched(self):
        conn = self._with_row("dispatched", True)
        with patch("database.get_db_conn", return_value=conn, create=True):
            self.assertEqual(fulfilment.get_letter_status_label_for_lead_reference("PLANIT-1"), "Preparing to post")

    def test_provider_accepted_maps_to_being_printed_and_posted(self):
        conn = self._with_row("provider_accepted", False)
        with patch("database.get_db_conn", return_value=conn, create=True):
            self.assertEqual(fulfilment.get_letter_status_label_for_lead_reference("PLANIT-1"), "Being printed & posted")

    def test_failed_maps_to_an_honest_issue_message(self):
        conn = self._with_row("failed", False)
        with patch("database.get_db_conn", return_value=conn, create=True):
            self.assertEqual(
                fulfilment.get_letter_status_label_for_lead_reference("PLANIT-1"), "Issue detected -- contact support"
            )

    def test_suppressed_maps_to_on_hold(self):
        conn = self._with_row("suppressed", False)
        with patch("database.get_db_conn", return_value=conn, create=True):
            self.assertEqual(fulfilment.get_letter_status_label_for_lead_reference("PLANIT-1"), "On hold")


class TestGetIntroductionRecordForLeadReference(unittest.TestCase):
    """2026-09-24 handoff ("My Introductions" account view): the richer,
    structured record -- same self-contained-connection / fail-toward-None
    posture as TestLetterStatusLabelForLeadReference above, same
    `create=True` reasoning (this class's own docstring explains why it's
    needed under the full suite)."""

    _ROW = (
        "dispatched", False, 2, "Stannp", "STANNP-REF-123",
        "2026-09-20 09:00:00", "2026-09-20 15:00:00", None, None, None,
        "2026-09-18 08:00:00", "2026-09-18 07:55:00", "single_purchase",
    )

    def _with_row(self, row):
        conn = MagicMock()
        cur = MagicMock()
        cur.fetchone.return_value = row
        conn.cursor.return_value = cur
        return conn

    def test_empty_reference_short_circuits(self):
        self.assertIsNone(fulfilment.get_introduction_record_for_lead_reference(""))

    def test_no_row_returns_none(self):
        conn = MagicMock()
        cur = MagicMock()
        cur.fetchone.return_value = None
        conn.cursor.return_value = cur
        with patch("database.get_db_conn", return_value=conn, create=True):
            self.assertIsNone(fulfilment.get_introduction_record_for_lead_reference("PLANIT-1"))

    def test_db_error_fails_toward_none_not_a_crash(self):
        with patch("database.get_db_conn", side_effect=RuntimeError("db down"), create=True):
            self.assertIsNone(fulfilment.get_introduction_record_for_lead_reference("PLANIT-1"))

    def test_malformed_row_fails_toward_none_not_a_crash(self):
        # Guards the my_leads_view fallback path this same handoff relies
        # on: an unexpected row shape (e.g. an older mock/stub returning a
        # short tuple) must degrade to None, never raise out of this
        # function into the caller.
        conn = self._with_row(("alloc-id-999",))
        with patch("database.get_db_conn", return_value=conn, create=True):
            self.assertIsNone(fulfilment.get_introduction_record_for_lead_reference("PLANIT-1"))

    def test_dispatched_row_never_claims_delivery(self):
        conn = self._with_row(self._ROW)
        with patch("database.get_db_conn", return_value=conn, create=True):
            record = fulfilment.get_introduction_record_for_lead_reference("PLANIT-1")
        self.assertEqual(record["stage_key"], "dispatched")
        self.assertEqual(record["stage_label"], "Dispatch confirmed")
        self.assertNotIn("delivered", record["stage_explanation"].lower())
        self.assertIn("no way to confirm", record["stage_explanation"].lower())
        self.assertEqual(record["template_version"], 2)
        self.assertEqual(record["provider_name"], "Stannp")
        self.assertEqual(record["provider_reference"], "STANNP-REF-123")
        self.assertEqual(record["purchase_date"], "2026-09-18 07:55:00")

    def test_provider_accepted_is_never_labelled_dispatch(self):
        row = ("provider_accepted", False, 1, "Stannp", "REF-1", "2026-09-20 09:00:00",
               None, None, None, None, "2026-09-18 08:00:00", "2026-09-18 07:55:00", "single_purchase")
        conn = self._with_row(row)
        with patch("database.get_db_conn", return_value=conn, create=True):
            record = fulfilment.get_introduction_record_for_lead_reference("PLANIT-1")
        self.assertEqual(record["stage_label"], "Submitted to postal provider")
        self.assertNotEqual(record["stage_key"], "dispatched")

    def test_dry_run_overrides_status_with_test_mode_note(self):
        row = ("dispatched", True, 1, "fake_test", "FAKE-1", None, "2026-09-20 09:00:00",
               None, None, None, "2026-09-18 08:00:00", "2026-09-18 07:55:00", "single_purchase")
        conn = self._with_row(row)
        with patch("database.get_db_conn", return_value=conn, create=True):
            record = fulfilment.get_introduction_record_for_lead_reference("PLANIT-1")
        self.assertTrue(record["is_dry_run"])
        self.assertEqual(record["stage_label"], "Preparing")
        self.assertIn("test/practice", record["stage_explanation"].lower())

    def test_null_template_version_is_not_fabricated(self):
        row = ("pending_approval", True, None, None, None, None, None, None, None, None,
               "2026-09-18 08:00:00", "2026-09-18 07:55:00", "single_purchase")
        conn = self._with_row(row)
        with patch("database.get_db_conn", return_value=conn, create=True):
            record = fulfilment.get_introduction_record_for_lead_reference("PLANIT-1")
        self.assertIsNone(record["template_version"])


class TestStreetFlyerRemovesUnconditionalClaims(unittest.TestCase):
    """generate_street_flyer no longer shows a hardcoded credential/discount
    claim for every contractor -- see main.py's own 2026-09-24 comment."""

    def _base_cur_side_effect(self, settings_row):
        return [
            None,  # resolve_buyer_facing_reference miss -> use as-is
            ("PLANIT-001", "1 Real Street, Leeds, LS1 1AA", "Fell one oak", "claimed"),
            settings_row,
        ]

    def test_no_saved_settings_shows_no_credentials_line_and_no_discount(self):
        cur = MagicMock()
        cur.fetchone.side_effect = self._base_cur_side_effect(None)
        conn = MagicMock()
        conn.cursor.return_value = cur
        request = MagicMock()
        request.cookies = {}
        # require_lead_ownership (called unconditionally, before any of the
        # claims-removal logic runs) 404s for a request with no session
        # UNLESS _admin_basic_auth_ok(request) is True -- see its docstring
        # ("Admin ... always passes"). Mocking fulfilment.get_lead_owner
        # alone (this test's original approach) never reaches that: the
        # function raises on the "no session" branch before get_lead_owner
        # is ever called. Patching _admin_basic_auth_ok directly is the
        # correct way to simulate the anonymous/admin preview path this
        # test is actually about.
        with patch("main.database.get_db_conn", return_value=conn), \
             patch("main._admin_basic_auth_ok", return_value=True), \
             patch("main.address_release.lead_address_release_allowed", return_value=True), \
             patch("main._verify_session_cookie", return_value=None):
            response = main.generate_street_flyer(request, "PLANIT-001")
        body = response if isinstance(response, str) else getattr(response, "body", str(response))

        self.assertNotIn("NPTC Certified", body)
        self.assertNotIn("£5M Insured", body)
        self.assertNotIn("Same-Day Street Discount", body)
        self.assertNotIn("20%", body)

    def test_a_contractors_own_real_saved_insurance_note_renders_instead(self):
        settings_row = (
            "contractor@example.com", "Ashcroft Tree Surgery", "01234 567890", "",
            "Fully insured up to £2M, NPTC certified crew", "City & Guilds NPTC Level 3",
            1, True, "fp", "friendly_introduction", "", "", "",
        )
        cur = MagicMock()
        cur.fetchone.side_effect = self._base_cur_side_effect(settings_row)
        conn = MagicMock()
        conn.cursor.return_value = cur
        request = MagicMock()
        request.cookies = {"treekey_contractor_session": main._sign_session_cookie("contractor@example.com")}
        with patch("main.database.get_db_conn", return_value=conn), \
             patch("main.fulfilment.get_lead_owner", return_value="contractor@example.com"), \
             patch("main.address_release.lead_address_release_allowed", return_value=True):
            response = main.generate_street_flyer(request, "PLANIT-001")
        body = response if isinstance(response, str) else getattr(response, "body", str(response))

        self.assertIn("Fully insured up to £2M, NPTC certified crew", body)
        self.assertIn("City &amp; Guilds NPTC Level 3", body)
        self.assertNotIn("NPTC Certified • £5M Insured", body)
        self.assertNotIn("Same-Day Street Discount", body)

    def test_title_no_longer_promises_a_discount(self):
        cur = MagicMock()
        cur.fetchone.side_effect = self._base_cur_side_effect(None)
        conn = MagicMock()
        conn.cursor.return_value = cur
        request = MagicMock()
        request.cookies = {}
        # See test_no_saved_settings_shows_no_credentials_line_and_no_discount
        # above for why _admin_basic_auth_ok (not get_lead_owner) is the
        # correct thing to patch here.
        with patch("main.database.get_db_conn", return_value=conn), \
             patch("main._admin_basic_auth_ok", return_value=True), \
             patch("main.address_release.lead_address_release_allowed", return_value=True), \
             patch("main._verify_session_cookie", return_value=None):
            response = main.generate_street_flyer(request, "PLANIT-001")
        body = response if isinstance(response, str) else getattr(response, "body", str(response))
        self.assertIn("Neighbor Street Notice |", body)
        self.assertNotIn("Neighbor Street Notice & Discount", body)


if __name__ == "__main__":
    unittest.main()

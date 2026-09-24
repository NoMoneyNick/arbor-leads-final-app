"""
test_letter_settings_journey.py -- 2026-09-18 review, Section 1 (second
pass): "Test a complete customer journey through to a fake-provider
submission."

Unlike the static-queue FakeCursor used elsewhere in this test suite
(tests/test_worker.py etc -- fine for testing one function's SQL calls in
isolation), this file drives a small STATEFUL in-memory fake database
(_JourneyFakeCursor below) that actually persists writes and reflects them
in later reads within the same test -- because a genuine customer journey
spans multiple real function calls (save settings, then read them back to
preview; approve; then, separately, purchase two DIFFERENT leads and have
the worker promote/fund/send both against that ONE approval) and a static
queue can't represent that.

_JourneyFakeCursor deliberately does NOT parse SQL generally -- it pattern-
matches the specific, known query shapes this codebase's own functions
issue (letter_content.py, fulfilment.py, worker.py, suppression.py), which
this test file's own docstring-reading during development enumerated
exactly. This is a test double for the DATABASE ADAPTER only -- every
business-logic function actually under test here
(letter_content.upsert_contractor_settings/get_contractor_settings/
approve_template/render_preview_letter, fulfilment.
create_allocation_and_obligation, worker.run_one_pass and its three
stages, suppression.is_suppressed, letter_providers.registry.attempt_send)
runs completely unmodified, for real.

The journey exercised:
  1. A contractor saves their letter settings (main.save_letter_settings).
  2. They preview it (main.letter_settings_preview) and approve it
     (main.approve_letter_settings) -- ONE approval.
  3. TWO different real leads get purchased (fulfilment.
     create_allocation_and_obligation, twice, different lead_reference/
     address/summary/council -- the actual create-allocation call every
     real purchase path in this codebase uses).
  4. worker.run_one_pass (the actual entry point wired to both real
     invocation surfaces -- see tests/test_worker_trigger_routes.py and
     tests/test_worker_runner.py) processes BOTH obligations in a single
     pass, promoting each through pending_approval -> pending_funding ->
     ready -> submitted, without a second approval -- proving the
     reusable-template fix (2026-09-18 review, Section 1, second pass) is
     not just unit-tested in isolation but actually holds across the real
     call chain a purchase triggers.
  5. Both obligations reach a FakeLetterProvider and are ACCEPTED (a real,
     non-dry-run send, not just a dry-run no-op) -- "through to a
     fake-provider submission."

Run with:
    python -m unittest tests.test_letter_settings_journey -v
"""
import asyncio
import os
import sys
import unittest
from unittest.mock import MagicMock, patch

_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
_APP_DIR = os.path.dirname(_THIS_DIR)
sys.path.insert(0, _APP_DIR)
sys.path.insert(0, _THIS_DIR)

# 2026-09-22 handoff: suppression.is_suppressed (invoked via registry.py's
# attempt_send, which the full customer journey below drives) now requires
# SUPPRESSION_HASH_KEY to be set (fails loud -- see
# suppression.py::_hash_key's docstring). Not a real secret, just a fixed
# non-empty string for deterministic test hashing.
os.environ.setdefault("SUPPRESSION_HASH_KEY", "unit-test-suppression-hash-key-not-a-real-secret")

import test_access_control  # noqa: E402  (populates sys.modules stubs + imports test_main + main)
import main  # noqa: E402
import worker  # noqa: E402
import funding  # noqa: E402
import fulfilment  # noqa: E402
import letter_content  # noqa: E402
from letter_providers.fake_provider import FakeLetterProvider  # noqa: E402
from letter_providers.registry import ProviderRegistry, ProviderSlot  # noqa: E402

_PIPELINE_ENV_KEY = fulfilment.LETTER_DISPATCH_PIPELINE_ENV
_had_old_pipeline_env = _PIPELINE_ENV_KEY in os.environ
_old_pipeline_env = os.environ.get(_PIPELINE_ENV_KEY)


def setUpModule():
    os.environ[_PIPELINE_ENV_KEY] = "fulfilment"


def tearDownModule():
    if _had_old_pipeline_env:
        os.environ[_PIPELINE_ENV_KEY] = _old_pipeline_env
    else:
        os.environ.pop(_PIPELINE_ENV_KEY, None)


def _run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


def _mock_request(cookie_value=None, form_data=None):
    req = MagicMock()
    req.cookies = {"treekey_contractor_session": cookie_value} if cookie_value else {}
    req.headers = {}

    async def _form():
        return form_data or {}
    req.form = _form
    return req


class _JourneyFakeCursor:
    """A small, stateful, in-memory stand-in for a real DB cursor -- see
    this module's own docstring for why a static queue can't do this.
    `execute` dispatches on distinctive substrings of each known query
    (never a general SQL parser); `_STATE` is shared class-level storage
    reset per test in setUp, standing in for a persistent database across
    every `cur = conn.cursor()` call in a test (a real connection/cursor
    pair would see the same committed rows too)."""

    def __init__(self, state):
        self.state = state
        self._pending_fetchone = None
        self._pending_fetchall = []

    def execute(self, sql, params=None):
        params = params or ()
        s = " ".join(sql.split())  # normalise whitespace for substring matching

        if "INSERT INTO contractor_letter_settings" in s:
            self._exec_upsert_contractor_settings(params)
        elif "SELECT contractor_email, business_name, phone, service_area_note" in s:
            self._exec_get_contractor_settings(params)
        elif "UPDATE contractor_letter_settings SET approved = TRUE" in s:
            self._exec_approve_template(params)
        elif "SELECT id FROM lead_allocations WHERE idempotency_key" in s:
            self._exec_idempotency_check(params)
        elif "INSERT INTO lead_allocations" in s:
            self._exec_insert_allocation(params)
        elif "INSERT INTO letter_obligations" in s:
            self._exec_insert_obligation(params)
        elif "FROM letter_obligations WHERE status = 'pending_approval'" in s:
            self._exec_select_pending_approval(params)
        elif "SELECT summary, council_source FROM leads" in s:
            self._exec_select_lead_content(params)
        elif "SET status = 'pending_funding'" in s:
            self._exec_update_to_pending_funding(params)
        elif "SELECT id FROM letter_obligations WHERE status = 'pending_funding'" in s:
            self._exec_select_pending_funding(params)
        elif "UPDATE letter_obligations SET status = 'ready'" in s:
            self._exec_update_to_ready(params)
        elif "SELECT id, lead_reference, address, applicant_name, idempotency_key, approved_content_html" in s:
            self._exec_select_ready(params)
        elif "FROM postal_suppressions" in s:
            self._exec_suppression_check(params)
        elif "SET status = 'submitting'" in s:
            self._exec_claim_for_submission(params)
        elif "UPDATE letter_obligations SET attempts = attempts + 1" in s:
            self._exec_mark_provider_result(s, params)
        else:
            raise AssertionError(f"_JourneyFakeCursor: unrecognised query, add a handler: {s!r}")

    def fetchone(self):
        return self._pending_fetchone

    def fetchall(self):
        return self._pending_fetchall

    def close(self):
        pass  # state lives in self.state, not on the cursor

    # -- contractor_letter_settings ----------------------------------
    def _exec_upsert_contractor_settings(self, params):
        # 2026-09-23 handoff, contractor letter-template selector: param
        # order here must match letter_content.upsert_contractor_settings'
        # actual INSERT statement exactly (see that function).
        (email, business_name, phone, service_area_note, insurance_note,
         qualifications_note, template_key, business_intro, services_note,
         contact_email, template_version) = params
        existing = self.state["contractor_letter_settings"].get(email)
        new_version = (existing["template_version"] + 1) if existing else template_version
        self.state["contractor_letter_settings"][email] = {
            "business_name": business_name, "phone": phone, "service_area_note": service_area_note,
            "insurance_note": insurance_note, "qualifications_note": qualifications_note,
            "template_key": template_key, "business_intro": business_intro,
            "services_note": services_note, "contact_email": contact_email,
            "template_version": new_version, "approved": False, "approved_fingerprint": None,
        }

    def _exec_get_contractor_settings(self, params):
        (email,) = params
        row = self.state["contractor_letter_settings"].get(email.strip().lower())
        if not row:
            self._pending_fetchone = None
            return
        self._pending_fetchone = (
            email.strip().lower(), row["business_name"], row["phone"], row["service_area_note"],
            row["insurance_note"], row["qualifications_note"], row["template_version"],
            row["approved"], row["approved_fingerprint"],
            row.get("template_key", "friendly_introduction"), row.get("business_intro", ""),
            row.get("services_note", ""), row.get("contact_email", ""),
        )

    def _exec_approve_template(self, params):
        (fingerprint, email) = params
        row = self.state["contractor_letter_settings"].get(email.strip().lower())
        if not row:
            self._pending_fetchone = None
            return
        row["approved"] = True
        row["approved_fingerprint"] = fingerprint
        self._pending_fetchone = (1,)

    # -- lead_allocations / letter_obligations (creation) ------------
    def _exec_idempotency_check(self, params):
        (idem_key,) = params
        alloc_id = self.state["lead_allocations_by_idem"].get(idem_key)
        self._pending_fetchone = (alloc_id,) if alloc_id else None

    def _exec_insert_allocation(self, params):
        (lead_reference, lead_id, buyer_email, allocation_type, source_payment_ref, stripe_event_id, idem_key) = params
        alloc_id = self.state["_next_id"]("alloc")
        self.state["lead_allocations_by_idem"][idem_key] = alloc_id
        self._pending_fetchone = (alloc_id,)

    def _exec_insert_obligation(self, params):
        (allocation_id, lead_reference, address, applicant_name, buyer_email,
         sale_context, status, template_version, idem_key) = params
        ob_id = self.state["_next_id"]("ob")
        self.state["letter_obligations"][ob_id] = {
            "id": ob_id, "allocation_id": allocation_id, "lead_reference": lead_reference, "address": address,
            "applicant_name": applicant_name, "buyer_email": buyer_email, "status": status,
            "content_fingerprint": None, "approved_content_html": None, "claimed_by_worker": None,
            "attempts": 0, "provider_name": None, "provider_reference": None, "last_error": None,
            "is_dry_run": False, "_seq": self.state["_next_seq"](),
        }
        self._pending_fetchone = (ob_id,)

    # -- worker.promote_pending_approvals -----------------------------
    def _exec_select_pending_approval(self, params):
        rows = sorted(
            (o for o in self.state["letter_obligations"].values() if o["status"] == "pending_approval"),
            key=lambda o: o["_seq"],
        )
        self._pending_fetchall = [
            (o["id"], o["lead_reference"], o["address"], o["applicant_name"], o["buyer_email"]) for o in rows
        ]

    def _exec_select_lead_content(self, params):
        (lead_reference,) = params
        row = self.state["leads"].get(lead_reference)
        self._pending_fetchone = row  # (summary, council) tuple, or None

    def _exec_update_to_pending_funding(self, params):
        (fingerprint, content_html, ob_id) = params
        row = self.state["letter_obligations"].get(ob_id)
        if row and row["status"] == "pending_approval":
            row["status"] = "pending_funding"
            row["content_fingerprint"] = fingerprint
            row["approved_content_html"] = content_html
            self._pending_fetchone = (ob_id,)
        else:
            self._pending_fetchone = None

    # -- worker.promote_pending_funding --------------------------------
    def _exec_select_pending_funding(self, params):
        rows = sorted(
            (o for o in self.state["letter_obligations"].values() if o["status"] == "pending_funding"),
            key=lambda o: o["_seq"],
        )
        self._pending_fetchall = [(o["id"],) for o in rows]

    def _exec_update_to_ready(self, params):
        (ob_id,) = params
        row = self.state["letter_obligations"].get(ob_id)
        if row and row["status"] == "pending_funding":
            row["status"] = "ready"
            self._pending_fetchone = (ob_id,)
        else:
            self._pending_fetchone = None

    # -- worker.run_batch / attempt_send --------------------------------
    def _exec_select_ready(self, params):
        rows = sorted(
            (o for o in self.state["letter_obligations"].values() if o["status"] == "ready"),
            key=lambda o: o["_seq"],
        )
        self._pending_fetchall = [
            (o["id"], o["lead_reference"], o["address"], o["applicant_name"],
             o["id"] + "-idem", o["approved_content_html"])
            for o in rows
        ]

    def _exec_suppression_check(self, params):
        self._pending_fetchone = None  # no suppressions in this journey

    def _exec_claim_for_submission(self, params):
        (worker_id, ob_id) = params
        row = self.state["letter_obligations"].get(ob_id)
        if row and row["status"] == "ready":
            row["status"] = "submitting"
            row["claimed_by_worker"] = worker_id
            self._pending_fetchone = (ob_id,)
        else:
            self._pending_fetchone = None

    def _exec_mark_provider_result(self, s, params):
        # Dynamic column list (fulfilment.mark_provider_result) -- this
        # journey only exercises real (non-dry-run) sends, so the shape is
        # always [provider_name, provider_reference, error, content_fingerprint, obligation_id].
        provider_name, provider_reference, error, content_fingerprint, ob_id = params
        row = self.state["letter_obligations"][ob_id]
        row["attempts"] += 1
        row["provider_name"] = provider_name
        row["provider_reference"] = provider_reference
        row["last_error"] = error
        row["content_fingerprint"] = content_fingerprint
        if "status = 'provider_accepted'" in s:
            row["status"] = "provider_accepted"
        elif "status = 'dispatched'" in s:
            row["status"] = "dispatched"
        elif "status = 'failed'" in s:
            row["status"] = "failed"
        elif "status = 'unknown'" in s:
            row["status"] = "unknown"
        elif "status = 'dry_run'" in s:
            row["status"] = "dry_run"
            row["is_dry_run"] = True


def _new_state():
    counters = {"n": 0, "seq": 0}

    def _next_id(prefix):
        counters["n"] += 1
        return f"{prefix}-{counters['n']}"

    def _next_seq():
        counters["seq"] += 1
        return counters["seq"]

    return {
        "contractor_letter_settings": {},
        "lead_allocations_by_idem": {},
        "letter_obligations": {},
        "leads": {},
        "_next_id": _next_id,
        "_next_seq": _next_seq,
    }


class TestCompleteContractorToFakeProviderJourney(unittest.TestCase):

    def setUp(self):
        os.environ[letter_content.PRIVACY_CONTACT_EMAIL_ENV] = "privacy@treekey.co.uk"
        os.environ["SESSION_SECRET"] = "test-session-secret"
        main._SESSION_SECRET = b"test-session-secret"
        self.state = _new_state()
        self.contractor_email = "apex@example.com"

    def _cur(self):
        return _JourneyFakeCursor(self.state)

    def _conn_patch(self):
        """database.get_db_conn() -> an object whose .cursor() returns a
        fresh _JourneyFakeCursor bound to this test's shared state (a real
        connection would similarly hand out cursors that all see the same
        committed rows). commit()/rollback()/close() are no-ops -- the
        state lives in self.state regardless."""
        mock_conn = MagicMock()
        mock_conn.cursor.side_effect = lambda: self._cur()
        return patch("main.database.get_db_conn", return_value=mock_conn)

    def test_full_journey_two_different_leads_one_approval_both_accepted_by_fake_provider(self):
        signed_cookie = main._sign_session_cookie(self.contractor_email)

        # Step 1: contractor saves their letter settings via the real HTTP
        # route function.
        with self._conn_patch():
            save_request = _mock_request(
                cookie_value=signed_cookie,
                form_data={
                    "business_name": "Apex Tree Care", "phone": "0113 000 0000",
                    "service_area_note": "Leeds & surrounding", "insurance_note": "Public liability £5m",
                    "qualifications_note": "NPTC certified",
                },
            )
            save_result = _run(main.save_letter_settings(save_request))
        self.assertIn("/letter-settings/preview", save_result.url)

        stored = self.state["contractor_letter_settings"][self.contractor_email]
        self.assertFalse(stored["approved"], "saving must not auto-approve")

        # Step 2: preview (just exercised for real -- must not raise).
        with self._conn_patch():
            preview_request = _mock_request(cookie_value=signed_cookie)
            preview_result = main.letter_settings_preview(preview_request)
        self.assertEqual(preview_result.status_code, 200)

        # Step 3: approve -- ONE approval, via the real route function.
        with self._conn_patch():
            approve_request = _mock_request(cookie_value=signed_cookie)
            approve_result = _run(main.approve_letter_settings(approve_request))
        self.assertIn("saved=approved", approve_result.url)
        self.assertTrue(self.state["contractor_letter_settings"][self.contractor_email]["approved"])

        # Step 4: TWO different real leads get purchased -- the actual
        # allocation-creation call every real purchase path in this
        # codebase uses (database.py's four call sites all call this).
        self.state["leads"]["PLANIT-AAA"] = ("Fell one oak", "Leeds City Council")
        self.state["leads"]["PLANIT-BBB"] = ("Reduce two sycamores", "Manchester City Council")

        cur = self._cur()
        result_a = fulfilment.create_allocation_and_obligation(
            cur, lead_reference="PLANIT-AAA", lead_id="lead-aaa", address="1 First Street, Leeds",
            applicant_name="J Bloggs", buyer_email=self.contractor_email, allocation_type="single_lead_purchase",
            sale_context="stripe_checkout", source_payment_ref="pi_test_aaa",
        )
        result_b = fulfilment.create_allocation_and_obligation(
            cur, lead_reference="PLANIT-BBB", lead_id="lead-bbb", address="99 Second Road, Manchester",
            applicant_name="K Smith", buyer_email=self.contractor_email, allocation_type="single_lead_purchase",
            sale_context="stripe_checkout", source_payment_ref="pi_test_bbb",
        )
        self.assertTrue(result_a.ok)
        self.assertTrue(result_b.ok)
        # Both obligations start life needing approval -- template_approved
        # is never passed True by any real caller in this codebase (see
        # worker.py's module docstring) -- proving the worker pass below is
        # what actually promotes them, not obligation-creation-time trust.
        self.assertEqual(result_a.obligation_status, "pending_approval")
        self.assertEqual(result_b.obligation_status, "pending_approval")

        # Step 5: a single worker.run_one_pass -- the SAME function both
        # real invocation surfaces call (see tests/test_worker_trigger_
        # routes.py, tests/test_worker_runner.py) -- processes BOTH
        # obligations, using the ONE approval from Step 3, all the way to
        # a REAL (non-dry-run) fake-provider submission.
        gate = funding.FundingGate(mode="simulate")
        gate.simulate_budget(100_00)
        adapter = FakeLetterProvider(force_outcome="accepted")
        registry = ProviderRegistry([ProviderSlot(adapter=adapter)])

        report = worker.run_one_pass(
            cur, worker_id="journey-test-worker", is_dry_run=False,
            registry=registry, gate=gate, estimated_cost_pence=95,
        )

        self.assertTrue(report.pipeline_active)
        self.assertEqual(report.approvals.promoted_to_pending_funding, 2,
                          "one approval must cover BOTH different leads -- this is the actual "
                          "reusable-template regression test for the real call chain")
        self.assertEqual(report.approvals.left_pending_approval, 0)
        self.assertEqual(report.funding.promoted_to_ready, 2)
        self.assertEqual(len(report.send_outcomes), 2)
        for outcome in report.send_outcomes:
            self.assertEqual(outcome.final_status, "accepted")

        # And the underlying obligation rows genuinely reflect a real
        # fake-provider acceptance, not just the returned SendOutcome list.
        for ob in self.state["letter_obligations"].values():
            self.assertEqual(ob["status"], "provider_accepted")
            self.assertEqual(ob["provider_name"], "fake_test")
            self.assertFalse(ob["is_dry_run"])
            self.assertIsNotNone(ob["approved_content_html"])
            self.assertIn("Apex Tree Care", ob["approved_content_html"])

    def test_editing_settings_after_approval_blocks_a_new_lead_until_reapproved(self):
        """The other half of 'reusable, not indefinitely stale': a settings
        change resets approval (letter_content.upsert_contractor_settings
        always sets approved=FALSE), so a lead purchased afterwards must
        stay in pending_approval until the contractor re-approves -- proven
        here through the real route functions + a real worker pass, not
        just the isolated unit test in tests/test_worker.py."""
        signed_cookie = main._sign_session_cookie(self.contractor_email)

        with self._conn_patch():
            _run(main.save_letter_settings(_mock_request(
                cookie_value=signed_cookie,
                form_data={"business_name": "Apex Tree Care", "phone": "0113 000 0000"},
            )))
        with self._conn_patch():
            _run(main.approve_letter_settings(_mock_request(cookie_value=signed_cookie)))
        self.assertTrue(self.state["contractor_letter_settings"][self.contractor_email]["approved"])

        # The contractor now edits their phone number -- upsert always
        # resets approved=FALSE (letter_content.upsert_contractor_settings'
        # own documented invariant).
        with self._conn_patch():
            _run(main.save_letter_settings(_mock_request(
                cookie_value=signed_cookie,
                form_data={"business_name": "Apex Tree Care", "phone": "0113 999 9999"},
            )))
        self.assertFalse(self.state["contractor_letter_settings"][self.contractor_email]["approved"])

        self.state["leads"]["PLANIT-CCC"] = ("Fell one willow", "Leeds City Council")
        cur = self._cur()
        fulfilment.create_allocation_and_obligation(
            cur, lead_reference="PLANIT-CCC", lead_id="lead-ccc", address="5 Third Ave, Leeds",
            applicant_name="A Homeowner", buyer_email=self.contractor_email, allocation_type="single_lead_purchase",
            sale_context="stripe_checkout", source_payment_ref="pi_test_ccc",
        )

        gate = funding.FundingGate(mode="simulate")
        gate.simulate_budget(100_00)
        registry = ProviderRegistry([ProviderSlot(adapter=FakeLetterProvider(force_outcome="accepted"))])
        report = worker.run_one_pass(cur, worker_id="journey-test-worker", is_dry_run=False,
                                      registry=registry, gate=gate, estimated_cost_pence=95)

        self.assertEqual(report.approvals.promoted_to_pending_funding, 0)
        self.assertEqual(report.approvals.left_pending_approval, 1)
        self.assertEqual(report.send_outcomes, [])
        (only_ob,) = self.state["letter_obligations"].values()
        self.assertEqual(only_ob["status"], "pending_approval")


if __name__ == "__main__":
    unittest.main()

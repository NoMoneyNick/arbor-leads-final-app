"""
test_worker.py -- worker.py's promotion pipeline (pending_approval ->
pending_funding -> ready) and run_batch's use of the provider registry.

2026-09-18 review, Section 2: every function under test here now refuses to
run unless LETTER_DISPATCH_PIPELINE=fulfilment (see worker.py's
_refuse_unless_fulfilment_pipeline_active) -- set for the whole module via
setUpModule/tearDownModule below, restoring whatever was there before on
exit so this doesn't leak into other test files run in the same
`unittest discover` process.

Run with:
    python -m unittest tests.test_worker -v
"""
import os
import sys
import unittest
from unittest.mock import MagicMock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# 2026-09-22 handoff: suppression.is_suppressed (invoked via registry.py's
# attempt_send, which run_batch/run_one_pass drive here) now requires
# SUPPRESSION_HASH_KEY to be set (fails loud -- see
# suppression.py::_hash_key's docstring). Not a real secret, just a fixed
# non-empty string for deterministic test hashing.
os.environ.setdefault("SUPPRESSION_HASH_KEY", "unit-test-suppression-hash-key-not-a-real-secret")
import worker
import letter_content
import funding
import fulfilment
from letter_providers.fake_provider import FakeLetterProvider
from letter_providers.registry import ProviderRegistry, ProviderSlot

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


class FakeCursor:
    """Supports both fetchone (a queue of single results) and fetchall (a
    queue of row-list results), consumed in call order -- same convention
    as the other FakeCursor helpers in this test suite."""

    def __init__(self, fetchone_results=None, fetchall_results=None):
        self.executed = []
        self._fetchone_results = list(fetchone_results or [])
        self._fetchall_results = list(fetchall_results or [])

    def execute(self, sql, params=None):
        self.executed.append((sql.strip(), params))

    def fetchone(self):
        return self._fetchone_results.pop(0) if self._fetchone_results else None

    def fetchall(self):
        return self._fetchall_results.pop(0) if self._fetchall_results else []


def _approved_settings(fingerprint):
    return letter_content.ContractorLetterSettings(
        contractor_email="contractor@example.com", business_name="Apex Tree Care", phone="0113 000 0000",
        approved=True, approved_fingerprint=fingerprint,
    )


class TestPromotePendingApprovals(unittest.TestCase):
    def setUp(self):
        os.environ[letter_content.PRIVACY_CONTACT_EMAIL_ENV] = "privacy@treekey.co.uk"

    def _current_template_fingerprint(self):
        """2026-09-18 review, Section 1 (second pass): the fingerprint an
        approval must match is now the TEMPLATE-level one (business_name/
        phone/notes/version), not a full per-lead render's fingerprint --
        see letter_content.template_fingerprint's own docstring for why.
        This must match settings row (contractor@example.com, Apex Tree
        Care, 0113 000 0000, '', '', '', template_version=1) used below."""
        return letter_content.template_fingerprint(
            letter_content.ContractorLetterSettings(contractor_email="contractor@example.com",
                                                      business_name="Apex Tree Care", phone="0113 000 0000"),
        )

    def test_promotes_when_approval_fingerprint_matches_current_render(self):
        fp = self._current_template_fingerprint()
        cur = FakeCursor(
            fetchall_results=[[("ob-1", "PLANIT-001", "1 Test St", "J Bloggs", "contractor@example.com")]],
            fetchone_results=[
                ("contractor@example.com", "Apex Tree Care", "0113 000 0000", "", "", "", 1, True, fp,
                 "friendly_introduction", "", "", ""),  # settings row
                ("Fell one oak", "Leeds"),   # leads row
                ("ob-1",),                    # UPDATE ... RETURNING id
            ],
        )
        report = worker.promote_pending_approvals(cur)
        self.assertEqual(report.promoted_to_pending_funding, 1)
        self.assertEqual(report.left_pending_approval, 0)

    def test_promotion_stamps_template_version_onto_the_obligation(self):
        """2026-09-24 handoff ("My Introductions" account view, "template/
        version used" field): letter_obligations.template_version was
        defined in the schema and accepted by fulfilment.
        create_allocation_and_obligation, but no caller anywhere ever
        actually passed a value for it -- confirmed by grepping every real
        call site. This is the one place a specific template is actually
        frozen for an obligation (the same UPDATE that freezes
        approved_content_html), so it's the fix: the settings row's own
        template_version (7 in the mocked settings row below) must now flow
        into the UPDATE's params. The approval fingerprint must be computed
        against a settings object with the SAME template_version=7 --
        template_fingerprint bakes in str(settings.template_version), so a
        mismatched version here would make is_approval_current legitimately
        false and the obligation would never reach the UPDATE at all."""
        fp = letter_content.template_fingerprint(
            letter_content.ContractorLetterSettings(
                contractor_email="contractor@example.com", business_name="Apex Tree Care",
                phone="0113 000 0000", template_version=7,
            ),
        )
        cur = FakeCursor(
            fetchall_results=[[("ob-1", "PLANIT-001", "1 Test St", "J Bloggs", "contractor@example.com")]],
            fetchone_results=[
                ("contractor@example.com", "Apex Tree Care", "0113 000 0000", "", "", "", 7, True, fp,
                 "friendly_introduction", "", "", ""),  # settings row -- template_version=7
                ("Fell one oak", "Leeds"),   # leads row
                ("ob-1",),                    # UPDATE ... RETURNING id
            ],
        )
        worker.promote_pending_approvals(cur)
        update_sql, update_params = cur.executed[-1]
        self.assertIn("template_version", update_sql)
        self.assertIn(7, update_params)

    def test_leaves_in_place_when_not_yet_approved(self):
        cur = FakeCursor(
            fetchall_results=[[("ob-1", "PLANIT-001", "1 Test St", "J Bloggs", "contractor@example.com")]],
            fetchone_results=[None],  # get_contractor_settings -> no row at all
        )
        report = worker.promote_pending_approvals(cur)
        self.assertEqual(report.left_pending_approval, 1)
        self.assertEqual(report.promoted_to_pending_funding, 0)

    def test_leaves_in_place_when_settings_changed_since_approval(self):
        """Section 5: the contractor approved an OLDER version of their
        details; something changed since (business name, phone, insurance
        note) -- re-rendering now produces a different fingerprint than what
        was approved, so this must NOT be promoted."""
        cur = FakeCursor(
            fetchall_results=[[("ob-1", "PLANIT-001", "1 Test St", "J Bloggs", "contractor@example.com")]],
            fetchone_results=[
                ("contractor@example.com", "A DIFFERENT NAME NOW", "0113 000 0000", "", "", "", 2, True, "stale-fingerprint-abc",
                 "friendly_introduction", "", "", ""),
                ("Fell one oak", "Leeds"),
            ],
        )
        report = worker.promote_pending_approvals(cur)
        self.assertEqual(report.left_pending_approval, 1)

    def test_leaves_in_place_when_templates_own_wording_changed_since_approval(self):
        """2026-09-23 handoff: mirrors test_leaves_in_place_when_settings_
        changed_since_approval, but the thing that changed is NOT the
        contractor's own fields -- it's TreeKey's own placeholder copy in
        letter_content.TEMPLATE_REGISTRY for the template this contractor
        approved (simulating Nick's eventual final-wording swap). The
        approval on file must stop being current the moment the template's
        own text changes, exactly as it would for an edited business
        name -- see letter_content.template_fingerprint's own docstring."""
        settings_for_fp = letter_content.ContractorLetterSettings(
            contractor_email="contractor@example.com", business_name="Apex Tree Care", phone="0113 000 0000",
        )
        stale_fp = letter_content.template_fingerprint(settings_for_fp)  # computed BEFORE the copy edit below

        original = letter_content.TEMPLATE_REGISTRY[letter_content.DEFAULT_TEMPLATE_KEY]
        edited = letter_content.LetterTemplateDefinition(
            key=original.key, label=original.label, version=original.version + 1,
            opening_line="Brand new final wording, not what was approved.",
            quote_request_line=original.quote_request_line, sign_off_word=original.sign_off_word,
        )
        letter_content.TEMPLATE_REGISTRY[letter_content.DEFAULT_TEMPLATE_KEY] = edited
        try:
            cur = FakeCursor(
                fetchall_results=[[("ob-1", "PLANIT-001", "1 Test St", "J Bloggs", "contractor@example.com")]],
                fetchone_results=[
                    ("contractor@example.com", "Apex Tree Care", "0113 000 0000", "", "", "", 1, True, stale_fp,
                     "friendly_introduction", "", "", ""),
                ],
            )
            report = worker.promote_pending_approvals(cur)
        finally:
            letter_content.TEMPLATE_REGISTRY[letter_content.DEFAULT_TEMPLATE_KEY] = original

        self.assertEqual(report.left_pending_approval, 1)
        self.assertEqual(report.promoted_to_pending_funding, 0)

    def test_leaves_in_place_when_lead_row_missing(self):
        cur = FakeCursor(
            fetchall_results=[[("ob-1", "PLANIT-GONE", "1 Test St", "J Bloggs", "contractor@example.com")]],
            fetchone_results=[
                ("contractor@example.com", "Apex Tree Care", "0113 000 0000", "", "", "", 1, True, "whatever",
                 "friendly_introduction", "", "", ""),
                None,  # leads lookup finds nothing
            ],
        )
        report = worker.promote_pending_approvals(cur)
        self.assertEqual(report.left_pending_approval, 1)

    def test_missing_privacy_config_is_counted_as_an_error_not_silently_skipped(self):
        os.environ.pop(letter_content.PRIVACY_CONTACT_EMAIL_ENV, None)
        # The template fingerprint must actually match (2026-09-18 review,
        # Section 1, second pass) so this obligation gets past the
        # eligibility check and reaches render_letter -- which is what
        # actually raises LetterConfigError here, since the privacy-contact
        # env var was just removed above. A mismatched fingerprint would
        # short-circuit to left_pending_approval before ever calling
        # render_letter, which would test the wrong thing.
        matching_fp = self._current_template_fingerprint()
        cur = FakeCursor(
            fetchall_results=[[("ob-1", "PLANIT-001", "1 Test St", "J Bloggs", "contractor@example.com")]],
            fetchone_results=[
                ("contractor@example.com", "Apex Tree Care", "0113 000 0000", "", "", "", 1, True, matching_fp,
                 "friendly_introduction", "", "", ""),
                ("Fell one oak", "Leeds"),
            ],
        )
        report = worker.promote_pending_approvals(cur)
        self.assertEqual(report.errors, 1)
        os.environ[letter_content.PRIVACY_CONTACT_EMAIL_ENV] = "privacy@treekey.co.uk"  # restore for other tests

    def test_same_approval_promotes_a_second_different_lead_without_reapproval(self):
        """2026-09-18 review, Section 1 (second pass): THE test that
        actually distinguishes a genuinely reusable approval from one that
        merely happens to match the single lead it was computed against.
        One approval (template_fingerprint of the contractor's settings,
        never touching lead_reference/address/summary/council) must
        promote TWO obligations for two DIFFERENT leads -- different
        address, different reference, different summary/council -- with no
        re-approval in between."""
        fp = self._current_template_fingerprint()
        settings_row = ("contractor@example.com", "Apex Tree Care", "0113 000 0000", "", "", "", 1, True, fp,
                        "friendly_introduction", "", "", "")

        cur_lead_a = FakeCursor(
            fetchall_results=[[("ob-a", "PLANIT-AAA", "1 Test St", "J Bloggs", "contractor@example.com")]],
            fetchone_results=[settings_row, ("Fell one oak", "Leeds"), ("ob-a",)],
        )
        report_a = worker.promote_pending_approvals(cur_lead_a)
        self.assertEqual(report_a.promoted_to_pending_funding, 1)

        cur_lead_b = FakeCursor(
            fetchall_results=[[("ob-b", "PLANIT-BBB", "99 Another Rd, Manchester", "K Smith", "contractor@example.com")]],
            fetchone_results=[settings_row, ("Reduce two sycamores", "Manchester"), ("ob-b",)],
        )
        report_b = worker.promote_pending_approvals(cur_lead_b)
        self.assertEqual(report_b.promoted_to_pending_funding, 1)


class TestPromotePendingFunding(unittest.TestCase):
    def test_promotes_when_gate_eligible(self):
        gate = funding.FundingGate(mode="hold")
        cur = FakeCursor(
            fetchall_results=[[("ob-1",)]],
            fetchone_results=[(5000,), ("ob-1",)],  # budget check, then UPDATE RETURNING id
        )
        report = worker.promote_pending_funding(cur, gate, estimated_cost_pence=50)
        self.assertEqual(report.promoted_to_ready, 1)

    def test_leaves_in_place_when_gate_says_no_budget(self):
        gate = funding.FundingGate(mode="hold")
        cur = FakeCursor(fetchall_results=[[("ob-1",)]], fetchone_results=[(0,)])
        report = worker.promote_pending_funding(cur, gate, estimated_cost_pence=50)
        self.assertEqual(report.left_pending_funding_ineligible, 1)
        self.assertEqual(report.promoted_to_ready, 0)

    def test_partial_budget_promotes_only_what_it_can_cover(self):
        """Two obligations, 50p each, but only 60p available -- the gate is
        re-checked per obligation (via mailing_budget_confirmations' real
        remaining balance in production; here simulated by the FIRST check
        succeeding and consuming nothing itself -- the actual debit happens
        via gate.spend() elsewhere, e.g. after a confirmed send, which this
        promotion step deliberately does NOT call, since promotion is not a
        spend). This test asserts BOTH obligations are checked independently
        rather than the whole batch being all-or-nothing."""
        gate = funding.FundingGate(mode="hold")
        cur = FakeCursor(
            fetchall_results=[[("ob-1",), ("ob-2",)]],
            fetchone_results=[(60,), ("ob-1",), (0,)],  # ob-1: eligible+promoted; ob-2: re-check returns 0 -> ineligible
        )
        report = worker.promote_pending_funding(cur, gate, estimated_cost_pence=50)
        self.assertEqual(report.promoted_to_ready, 1)
        self.assertEqual(report.left_pending_funding_ineligible, 1)


class TestRunBatch(unittest.TestCase):
    """2026-09-18 review, Section 7: run_batch no longer looks up
    contractor_letter_settings or the leads row at send time at all -- it
    sends whatever promote_pending_approvals already froze into
    approved_content_html. The fetchall row shape changed accordingly (no
    buyer_email, an approved_content_html column added); see
    tests/test_content_freezing.py for the end-to-end regression proving a
    settings edit after approval can't change what gets sent."""

    def setUp(self):
        os.environ[letter_content.PRIVACY_CONTACT_EMAIL_ENV] = "privacy@treekey.co.uk"

    def test_dry_run_batch_marks_every_ready_obligation_dry_run(self):
        cur = FakeCursor(
            fetchall_results=[[("ob-1", "PLANIT-001", "1 Test St", "J Bloggs", "idem-1", "<p>Approved letter</p>")]],
            fetchone_results=[
                None,                          # suppression check -> not suppressed
                ("ob-1",),                     # claim_for_submission
            ],
        )
        registry = ProviderRegistry([ProviderSlot(adapter=FakeLetterProvider())])
        outcomes = worker.run_batch(cur, registry, worker_id="w1", is_dry_run=True)
        self.assertEqual(len(outcomes), 1)
        self.assertEqual(outcomes[0].final_status, "dry_run")

    def test_skips_obligation_with_no_frozen_content_rather_than_resending(self):
        """A 'ready' row with no approved_content_html (NULL) must be
        skipped, never re-rendered as a fallback -- re-rendering here would
        quietly reopen the exact gap Section 7 closed."""
        cur = FakeCursor(
            fetchall_results=[[("ob-1", "PLANIT-001", "1 Test St", "J Bloggs", "idem-1", None)]],
        )
        registry = ProviderRegistry([ProviderSlot(adapter=FakeLetterProvider())])
        outcomes = worker.run_batch(cur, registry, worker_id="w1", is_dry_run=True)
        self.assertEqual(outcomes, [])

    def test_skips_obligation_with_empty_string_frozen_content(self):
        cur = FakeCursor(
            fetchall_results=[[("ob-1", "PLANIT-001", "1 Test St", "J Bloggs", "idem-1", "")]],
        )
        registry = ProviderRegistry([ProviderSlot(adapter=FakeLetterProvider())])
        outcomes = worker.run_batch(cur, registry, worker_id="w1", is_dry_run=True)
        self.assertEqual(outcomes, [])

    def test_real_send_uses_the_frozen_content_verbatim_not_a_fresh_render(self):
        """Drives run_batch with is_dry_run=False against a FakeLetterProvider
        whose send() is wrapped to record the exact LetterRequest it
        received, and asserts content_html is byte-for-byte the frozen
        approved_content_html -- proving run_batch never calls
        letter_content.render_letter (or anything else) to produce
        different content on this path."""
        frozen_html = "<p>This exact wording was approved by the contractor.</p>"
        cur = FakeCursor(
            fetchall_results=[[("ob-1", "PLANIT-001", "1 Test St", "J Bloggs", "idem-1", frozen_html)]],
            fetchone_results=[
                None,        # suppression check -> not suppressed
                ("ob-1",),   # claim_for_submission
            ],
        )
        adapter = FakeLetterProvider(force_outcome="accepted")
        received_requests = []
        _real_send = adapter.send

        def _spy_send(request):
            received_requests.append(request)
            return _real_send(request)
        adapter.send = _spy_send

        registry = ProviderRegistry([ProviderSlot(adapter=adapter)])
        outcomes = worker.run_batch(cur, registry, worker_id="w1", is_dry_run=False)
        self.assertEqual(len(outcomes), 1)
        self.assertEqual(outcomes[0].final_status, "accepted")
        self.assertEqual(len(received_requests), 1)
        self.assertEqual(received_requests[0].content_html, frozen_html)


class TestRunOnePass(unittest.TestCase):
    """2026-09-18 review, Section 6 (second pass): run_one_pass is the
    actual function now wired to both real entry points (main.py's
    /trigger-letter-fulfilment-worker route and worker_runner.py -- see
    both files' own docstrings and docs/operator_guide.md section 5).
    These tests exercise run_one_pass directly, the same way those two
    real callers do, rather than only the three stage functions in
    isolation above."""

    def setUp(self):
        os.environ[letter_content.PRIVACY_CONTACT_EMAIL_ENV] = "privacy@treekey.co.uk"

    def test_refuses_cleanly_when_pipeline_is_not_fulfilment(self):
        """setUpModule sets LETTER_DISPATCH_PIPELINE=fulfilment for the
        whole module; temporarily override it here to prove run_one_pass's
        own top-level refusal check (not just each stage function's
        individual one) short-circuits before the cursor is touched at
        all -- a caller must be able to trust that a misconfigured
        pipeline flag results in literally zero DB activity, not a
        half-run pass."""
        old = os.environ.get(fulfilment.LETTER_DISPATCH_PIPELINE_ENV)
        os.environ[fulfilment.LETTER_DISPATCH_PIPELINE_ENV] = "legacy"
        try:
            cur = FakeCursor()
            report = worker.run_one_pass(cur, worker_id="test-worker")
        finally:
            if old is None:
                os.environ.pop(fulfilment.LETTER_DISPATCH_PIPELINE_ENV, None)
            else:
                os.environ[fulfilment.LETTER_DISPATCH_PIPELINE_ENV] = old

        self.assertFalse(report.pipeline_active)
        self.assertIsNone(report.approvals)
        self.assertIsNone(report.funding)
        self.assertEqual(report.send_outcomes, [])
        self.assertEqual(cur.executed, [])  # never touched the DB at all

    def test_full_pass_promotes_pending_approval_all_the_way_to_a_dry_run_send(self):
        """Full three-stage chain in one run_one_pass call: pending_approval
        -> pending_funding -> ready -> dry_run. Uses FundingGate(mode=
        "simulate") because that mode's check()/reserve()/release()/
        settle() all short-circuit with zero DB calls (verified by reading
        funding.py directly), which is what makes a full-chain FakeCursor
        test tractable without also having to model the funding SQL.

        The FakeCursor does not itself persist state between the three
        stages -- this test independently precomputes the exact HTML
        promote_pending_approvals will freeze (letter_content.render_letter
        is documented pure/deterministic: same inputs, same output) and
        feeds that identical string into the run_batch-stage fetchall row,
        standing in for what a real DB round-trip between the three UPDATE/
        SELECT statements would genuinely produce. Real persistence across
        stages against an actual database is separately proven in
        tests/test_content_freezing.py; this test is about run_one_pass's
        own sequencing and result-aggregation, not about persistence."""
        contractor_email = "contractor@example.com"
        lead_reference = "PLANIT-001"
        address = "1 Test St"
        applicant_name = "J Bloggs"
        summary, council = "Fell one oak", "Leeds"

        settings_for_render = letter_content.ContractorLetterSettings(
            contractor_email=contractor_email, business_name="Apex Tree Care", phone="0113 000 0000",
        )
        expected_html = letter_content.render_letter(
            settings_for_render, lead_reference=lead_reference, address=address,
            summary=summary, council=council,
        )
        # 2026-09-18 review, Section 1 (second pass): the approval matching
        # is now against the TEMPLATE-level fingerprint, not this specific
        # lead's full-render fingerprint -- see letter_content.
        # template_fingerprint's own docstring. Using content_fingerprint
        # of expected_html here (the OLD approach) would still happen to
        # match, since this test approves and promotes the SAME lead's
        # data -- test_same_approval_promotes_a_second_different_lead_
        # without_reapproval in TestPromotePendingApprovals above is what
        # actually proves genuine reusability across different leads.
        expected_template_fp = letter_content.template_fingerprint(settings_for_render)

        cur = FakeCursor(
            fetchall_results=[
                [("ob-1", lead_reference, address, applicant_name, contractor_email)],         # stage 1: pending_approval rows
                [("ob-1",)],                                                                    # stage 2: pending_funding rows
                [("ob-1", lead_reference, address, applicant_name, "idem-1", expected_html)],   # stage 3: ready rows
            ],
            fetchone_results=[
                (contractor_email, "Apex Tree Care", "0113 000 0000", "", "", "", 1, True, expected_template_fp,
                 "friendly_introduction", "", "", ""),  # get_contractor_settings
                (summary, council),        # _fetch_lead_content_fields
                ("ob-1",),                 # promote_pending_approvals: UPDATE ... RETURNING id
                ("ob-1",),                 # promote_pending_funding: UPDATE ... RETURNING id
                None,                      # suppression.is_suppressed -> not suppressed
                ("ob-1",),                 # claim_for_submission
            ],
        )

        gate = funding.FundingGate(mode="simulate")
        gate.simulate_budget(100_00)  # far more than one letter's estimated cost
        registry = ProviderRegistry([ProviderSlot(adapter=FakeLetterProvider())])

        report = worker.run_one_pass(
            cur, worker_id="test-worker", is_dry_run=True, registry=registry, gate=gate,
            estimated_cost_pence=95,
        )

        self.assertTrue(report.pipeline_active)
        self.assertTrue(report.is_dry_run)

        self.assertIsNotNone(report.approvals)
        self.assertEqual(report.approvals.promoted_to_pending_funding, 1)
        self.assertEqual(report.approvals.left_pending_approval, 0)

        self.assertIsNotNone(report.funding)
        self.assertEqual(report.funding.promoted_to_ready, 1)
        self.assertEqual(report.funding.left_pending_funding_ineligible, 0)

        self.assertEqual(len(report.send_outcomes), 1)
        self.assertEqual(report.send_outcomes[0].final_status, "dry_run")
        self.assertEqual(report.send_outcomes[0].obligation_id, "ob-1")

    def test_defaults_registry_and_gate_when_not_supplied(self):
        """The two real callers (the HTTP trigger route, worker_runner.py)
        both call run_one_pass without a registry= or gate= -- proving this
        works with an empty batch (no pending_approval/pending_funding/
        ready rows) means it completes cleanly via
        letter_providers.registry.build_registry_from_env() and a default
        FundingGate() without needing any real provider credentials or
        FUNDING_MODE set up, exactly as it will the first time an operator
        actually points a scheduler at either entry point."""
        cur = FakeCursor(fetchall_results=[[], [], []])
        report = worker.run_one_pass(cur, worker_id="test-worker")
        self.assertTrue(report.pipeline_active)
        self.assertEqual(report.approvals.checked, 0)
        self.assertEqual(report.funding.checked, 0)
        self.assertEqual(report.send_outcomes, [])


if __name__ == "__main__":
    unittest.main()

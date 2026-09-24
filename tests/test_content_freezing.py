"""
test_content_freezing.py -- 2026-09-18 review, Section 7: "Demonstrate that
an approved letter's content stays fixed through submission and fallback,
even if contractor details or templates change later."

End-to-end regression (worker.promote_pending_approvals -> worker.run_batch)
proving:
  1. The exact HTML rendered and approved at promotion time is what gets
     frozen into letter_obligations.approved_content_html.
  2. A contractor settings change AFTER promotion (but before the worker
     gets around to sending) does not change what run_batch sends -- it
     never re-renders, never re-reads contractor_letter_settings at all.
  3. The SAME frozen content is sent to every provider tried, including a
     backup after a primary's confirmed rejection -- not re-rendered or
     re-fetched between attempts.

2026-09-18 review, Section 2: worker.run_batch/promote_pending_approvals now
refuse to run unless LETTER_DISPATCH_PIPELINE=fulfilment -- set for the
whole module via setUpModule/tearDownModule below (same convention as
tests/test_worker.py), restored on exit.

Run with:
    python -m unittest tests.test_content_freezing -v
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# 2026-09-22 handoff: suppression.is_suppressed (invoked via registry.py's
# attempt_send, which this file's worker.run_batch pass drives) now
# requires SUPPRESSION_HASH_KEY to be set (fails loud -- see
# suppression.py::_hash_key's docstring). Not a real secret, just a fixed
# non-empty string for deterministic test hashing.
os.environ.setdefault("SUPPRESSION_HASH_KEY", "unit-test-suppression-hash-key-not-a-real-secret")
import worker
import letter_content
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


def _settings(business_name, fingerprint=None, approved=True):
    return letter_content.ContractorLetterSettings(
        contractor_email="contractor@example.com", business_name=business_name, phone="0113 000 0000",
        approved=approved, approved_fingerprint=fingerprint,
    )


class TestApprovalFreezesExactRenderedContent(unittest.TestCase):

    def setUp(self):
        os.environ[letter_content.PRIVACY_CONTACT_EMAIL_ENV] = "privacy@treekey.co.uk"

    def test_promotion_stores_the_exact_html_it_rendered(self):
        settings_v1 = _settings("Apex Tree Care")
        html_v1 = letter_content.render_letter(
            settings_v1, lead_reference="PLANIT-001", address="1 Test St",
            summary="Fell one oak", council="Leeds",
        )
        fp_v1 = letter_content.content_fingerprint(html_v1)
        # 2026-09-18 review, Section 1 (second pass): the settings row's
        # approved_fingerprint (what worker.promote_pending_approvals
        # checks eligibility against) is now the TEMPLATE-level
        # fingerprint, not this full render's fingerprint -- see
        # letter_content.template_fingerprint's own docstring, and
        # tests/test_worker.py's TestPromotePendingApprovals for the test
        # that specifically proves this makes an approval reusable across
        # DIFFERENT leads, not just this one. fp_v1 (the full-render
        # fingerprint) remains what gets FROZEN into the obligation's own
        # content_fingerprint column below -- an entirely separate
        # guarantee (Section 7, "content stays fixed") that this test is
        # actually about.
        template_fp_v1 = letter_content.template_fingerprint(settings_v1)
        settings_v1_approved = _settings("Apex Tree Care", fingerprint=template_fp_v1)

        cur = FakeCursor(
            fetchall_results=[[("ob-1", "PLANIT-001", "1 Test St", "J Bloggs", "contractor@example.com")]],
            fetchone_results=[
                ("contractor@example.com", "Apex Tree Care", "0113 000 0000", "", "", "", 1, True, template_fp_v1,
                 "friendly_introduction", "", "", ""),
                ("Fell one oak", "Leeds"),
                ("ob-1",),
            ],
        )
        report = worker.promote_pending_approvals(cur)
        self.assertEqual(report.promoted_to_pending_funding, 1)

        update_calls = [c for c in cur.executed if c[0].startswith("UPDATE letter_obligations")]
        self.assertEqual(len(update_calls), 1)
        _, params = update_calls[0]
        # 2026-09-24 handoff ("My Introductions" account view): this UPDATE
        # now also stamps template_version (settings_v1's own, 1 by
        # default) at the same point content_fingerprint/approved_content_
        # html are frozen -- see worker.promote_pending_approvals' own
        # updated comment. One extra param, same position as the schema's
        # column order.
        frozen_fingerprint, frozen_html, frozen_template_version, obligation_id = params
        self.assertEqual(frozen_fingerprint, fp_v1)
        self.assertEqual(frozen_html, html_v1)
        self.assertEqual(frozen_template_version, settings_v1.template_version)


class TestSendUsesFrozenContentDespiteLaterSettingsChange(unittest.TestCase):
    """The core Section 7 scenario: approve under settings v1, then the
    contractor edits their settings (v2) before the worker actually sends
    -- run_batch must send v1's exact wording, not a fresh v2 render."""

    def setUp(self):
        os.environ[letter_content.PRIVACY_CONTACT_EMAIL_ENV] = "privacy@treekey.co.uk"

    def test_settings_change_after_promotion_does_not_change_what_is_sent(self):
        settings_v1 = _settings("Apex Tree Care")
        html_v1 = letter_content.render_letter(
            settings_v1, lead_reference="PLANIT-001", address="1 Test St",
            summary="Fell one oak", council="Leeds",
        )

        # The contractor now renames their business (settings v2) -- if
        # run_batch re-rendered, THIS is the content it would produce.
        settings_v2 = _settings("Apex Tree Care & Landscaping Ltd")
        html_v2 = letter_content.render_letter(
            settings_v2, lead_reference="PLANIT-001", address="1 Test St",
            summary="Fell one oak", council="Leeds",
        )
        self.assertNotEqual(html_v1, html_v2, "test setup sanity check: v1 and v2 must actually differ")

        # run_batch reads ONLY approved_content_html (frozen at promotion,
        # i.e. html_v1) from the 'ready' row -- it does not query
        # contractor_letter_settings or leads at all, so settings_v2 is
        # never consulted, exactly as if the contractor's edit happened
        # after the worker already committed to sending v1's wording.
        run_batch_cur = FakeCursor(
            fetchall_results=[[("ob-1", "PLANIT-001", "1 Test St", "J Bloggs", "idem-1", html_v1)]],
            fetchone_results=[None, ("ob-1",)],  # suppression check, claim_for_submission
        )
        adapter = FakeLetterProvider(force_outcome="accepted")
        received = []
        _real_send = adapter.send

        def _spy(request):
            received.append(request)
            return _real_send(request)
        adapter.send = _spy

        registry = ProviderRegistry([ProviderSlot(adapter=adapter)])
        outcomes = worker.run_batch(run_batch_cur, registry, worker_id="w1", is_dry_run=False)

        self.assertEqual(len(outcomes), 1)
        self.assertEqual(len(received), 1)
        self.assertEqual(received[0].content_html, html_v1)
        self.assertNotEqual(received[0].content_html, html_v2)


class TestFallbackSendsIdenticalContentToEveryProvider(unittest.TestCase):
    """A primary provider's confirmed rejection triggers a fallback to
    backup_1 -- both attempts must receive byte-for-byte the same frozen
    content, never re-derived between attempts."""

    def setUp(self):
        os.environ[letter_content.PRIVACY_CONTACT_EMAIL_ENV] = "privacy@treekey.co.uk"

    def test_primary_and_backup_receive_the_same_frozen_content(self):
        frozen_html = "<p>Approved wording, frozen at promotion time.</p>"
        cur = FakeCursor(
            fetchall_results=[[("ob-1", "PLANIT-001", "1 Test St", "J Bloggs", "idem-1", frozen_html)]],
            fetchone_results=[None, ("ob-1",)],  # suppression check, claim_for_submission
        )

        primary = FakeLetterProvider(force_outcome="rejected")
        backup = FakeLetterProvider(force_outcome="accepted")
        received_primary, received_backup = [], []

        def _wrap(adapter, sink):
            real = adapter.send

            def _spy(request):
                sink.append(request)
                return real(request)
            adapter.send = _spy

        _wrap(primary, received_primary)
        _wrap(backup, received_backup)

        registry = ProviderRegistry([ProviderSlot(adapter=primary, role="primary"),
                                      ProviderSlot(adapter=backup, role="backup_1")])
        outcomes = worker.run_batch(cur, registry, worker_id="w1", is_dry_run=False)

        self.assertEqual(len(outcomes), 1)
        self.assertEqual(outcomes[0].final_status, "accepted")
        self.assertEqual(outcomes[0].attempts_made, 2)
        self.assertEqual(len(received_primary), 1)
        self.assertEqual(len(received_backup), 1)
        self.assertEqual(received_primary[0].content_html, frozen_html)
        self.assertEqual(received_backup[0].content_html, frozen_html)
        self.assertEqual(received_primary[0].content_fingerprint, received_backup[0].content_fingerprint)


if __name__ == "__main__":
    unittest.main()

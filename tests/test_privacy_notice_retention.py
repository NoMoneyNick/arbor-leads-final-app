"""
test_privacy_notice_retention.py -- 2026-09-23, Request D, Part 3: "Correct
the privacy-notice mismatch in the working copy. Remove unsupported claims
about automatic 24-month deletion. Clearly distinguish implemented
retention from unresolved policy decisions."

The published /privacy-policy page (main.py::privacy_policy) previously
claimed "Lead data is retained for 24 months from discovery, after which
personal identifiers are anonymized or deleted" -- audited (2026-09-23,
Request C) and confirmed to be backed by NO code anywhere: no 24-month
constant, no anonymisation job, nothing. This test proves that specific
false claim is gone, and that what replaces it matches what this session's
Part 1/Part 2 work actually implemented (database.UNSOLD_LEAD_DELETION_DAYS
= 60 for unsold leads, retention_dispatch_purge.DISPATCH_PURGE_DELAY_HOURS
= 72 for a sold/dispatched lead's personal data) rather than a second
invented figure.

Run with:
    python -m unittest tests.test_privacy_notice_retention -v
"""
import asyncio
import os
import sys
import types
import unittest

_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
_APP_DIR = os.path.dirname(_THIS_DIR)
sys.path.insert(0, _APP_DIR)

if "database" not in sys.modules:
    sys.modules["database"] = types.ModuleType("database")

sys.path.insert(0, _THIS_DIR)
import test_access_control  # noqa: E402  (populates sys.modules stubs + imports test_main + main)
import main  # noqa: E402
import database  # noqa: E402  (the shared stub -- UNSOLD_LEAD_DELETION_DAYS asserted below is the real module's, imported separately)

import retention_dispatch_purge  # noqa: E402  real module (not stubbed anywhere)

# database.UNSOLD_LEAD_DELETION_DAYS is a plain module-level constant on the
# REAL database.py, not something the test stub carries -- load the real
# module under a private name (same technique test_lead_retention.py
# established) purely to read that one constant for the assertion below.
import importlib.util
if "psycopg2" not in sys.modules:
    sys.modules["psycopg2"] = types.ModuleType("psycopg2")
_spec = importlib.util.spec_from_file_location("_real_database_for_privacy_notice_test", os.path.join(_APP_DIR, "database.py"))
_real_database = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_real_database)


class TestPrivacyPolicyRetentionSectionIsAccurate(unittest.TestCase):

    def _page_html(self) -> str:
        return asyncio.run(main.privacy_policy())

    def test_the_unsupported_24_month_auto_deletion_claim_is_gone(self):
        html = self._page_html()
        self.assertNotIn("24 months", html)
        self.assertNotIn("anonymized or deleted", html)

    def test_states_the_actual_implemented_unsold_lead_deletion_window(self):
        html = self._page_html()
        self.assertIn(f"{_real_database.UNSOLD_LEAD_DELETION_DAYS} days", html)

    def test_states_the_actual_implemented_post_dispatch_purge_window(self):
        html = self._page_html()
        self.assertIn(f"{retention_dispatch_purge.DISPATCH_PURGE_DELAY_HOURS} hours", html)

    def test_does_not_invent_a_fixed_expiry_for_the_preserved_evidence_record(self):
        """Part 3: 'identify any decisions you need from me instead of
        inventing them' -- the page must not claim a specific retention
        period for the minimal financial/evidence record this session's
        Part 2 work deliberately keeps with no defined expiry; that number
        does not exist anywhere in code and must not be invented here."""
        html = self._page_html()
        self.assertIn("not yet set a fixed expiry", html)

    def test_no_longer_claims_an_enforced_six_year_billing_deletion(self):
        """The old text asserted billing records are actively deleted /
        expired after 6 years -- audited and confirmed no such job exists
        in code. The corrected text says plainly that no automated
        deletion process exists for billing records, rather than repeating
        an unenforced number."""
        html = self._page_html()
        self.assertIn("do not currently operate an automated deletion process", html)


if __name__ == "__main__":
    unittest.main()

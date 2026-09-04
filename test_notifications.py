"""
test_notifications.py -- Unit tests for notifications.py's severity-based
email dispatch, added Sep 4 2026 alongside the WARNING-tier change to
send_system_incident_alert().

Nick's own words, verbatim, after a Sep 3/4 redeploy caused ~15 unrelated
WARNING alerts (SCRAPER TLS FALLBACK / SCRAPER PAGE STRUCTURE) to fire in
one simultaneous burst, all confirmed benign false alarms by live
spot-check: "if the warning emails are mostly ignored i will never know
when to pay attention, is there anything you can do about that? make sure
i only get emails when there is a serious issue or a serious issue about
to occur."

The fix: CRITICAL/SECURITY severities are completely unchanged -- always
an immediate email, deduped by the existing throttle cache. WARNING no
longer emails immediately -- it's logged via database.log_system_warning
and only escalates to a real email once database.get_warning_recurrence_
days says the same category+title has recurred on 3+ distinct calendar
days within a trailing 7-day window. These tests mock database.
log_system_warning / get_warning_recurrence_days / notifications.
send_resend_email to prove the branching in isolation -- no real DB, no
real Resend call, ever.

notifications.py itself only needs `requests` and `dotenv` at import time
(both installed in this sandbox); `database` is imported lazily inside
send_system_incident_alert only when severity is WARNING, so it's stubbed
into sys.modules the same way test_scrapers.py/test_main.py stub their
own heavy dependencies -- this runs anywhere, no live Postgres required.

When this file runs alongside test_main.py/test_payments.py/test_scrapers.py
under `python -m unittest discover` (alphabetical import order), one of
those files has ALREADY put its own fake, MagicMock-only module into
sys.modules["notifications"] before this file ever runs -- a plain
`import notifications` here would just bind to that fake and silently test
nothing real (the exact module-identity trap test_database.py's own
comment documents for `database`). Loading notifications.py fresh under a
private module name -- never touching sys.modules["notifications"] at all
-- sidesteps this entirely, the same fix test_database.py already applies
to database.py.
"""
import importlib.util
import os
import sys
import types
import time
import unittest
from unittest.mock import MagicMock, patch

if "dotenv" not in sys.modules:
    try:
        import dotenv  # noqa: F401
    except ImportError:
        _fake_dotenv = types.ModuleType("dotenv")
        _fake_dotenv.load_dotenv = lambda *a, **k: None
        sys.modules["dotenv"] = _fake_dotenv

if "database" not in sys.modules:
    sys.modules["database"] = types.ModuleType("database")
_database = sys.modules["database"]
if not hasattr(_database, "log_system_warning"):
    _database.log_system_warning = MagicMock(return_value=True)
if not hasattr(_database, "get_warning_recurrence_days"):
    _database.get_warning_recurrence_days = MagicMock(return_value=0)

_NOTIFICATIONS_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "notifications.py")
_spec = importlib.util.spec_from_file_location("_notifications_under_test", _NOTIFICATIONS_PATH)
notifications = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(notifications)


class TestSystemIncidentAlertSeverityRouting(unittest.TestCase):
    """Locks in the Sep 4 2026 behaviour change: WARNING no longer emails
    immediately; CRITICAL/SECURITY are untouched."""

    def setUp(self):
        notifications._ALERT_THROTTLE_CACHE.clear()
        sys.modules["database"].log_system_warning.reset_mock(side_effect=True)
        sys.modules["database"].log_system_warning.return_value = True
        sys.modules["database"].log_system_warning.side_effect = None
        sys.modules["database"].get_warning_recurrence_days.reset_mock(side_effect=True)
        sys.modules["database"].get_warning_recurrence_days.return_value = 0
        sys.modules["database"].get_warning_recurrence_days.side_effect = None

    def _send(self, **overrides):
        kwargs = dict(
            category="SCRAPER TLS FALLBACK",
            title="example.gov.uk required unverified TLS fallback",
            description="desc",
            impact="impact",
            action_required="action",
            severity="WARNING",
        )
        kwargs.update(overrides)
        return notifications.send_system_incident_alert(**kwargs)

    def test_fresh_warning_is_logged_but_does_not_email(self):
        sys.modules["database"].get_warning_recurrence_days.return_value = 1
        with patch.object(notifications, "send_resend_email") as mock_send:
            self._send()
        sys.modules["database"].log_system_warning.assert_called_once_with(
            "SCRAPER TLS FALLBACK", "example.gov.uk required unverified TLS fallback", "desc"
        )
        mock_send.assert_not_called()

    def test_warning_recurring_two_days_still_does_not_email(self):
        """2 distinct days is not yet the 3-day threshold -- a one-off
        blip or short-lived flakiness must not escalate."""
        sys.modules["database"].get_warning_recurrence_days.return_value = 2
        with patch.object(notifications, "send_resend_email") as mock_send:
            self._send()
        mock_send.assert_not_called()

    def test_warning_recurring_three_days_escalates_to_email(self):
        sys.modules["database"].get_warning_recurrence_days.return_value = 3
        with patch.object(notifications, "send_resend_email", return_value=True) as mock_send:
            self._send()
        mock_send.assert_called_once()
        subject, html = mock_send.call_args[0]
        self.assertIn("RECURRING", subject.upper())

    def test_warning_recurring_more_than_three_days_also_escalates(self):
        sys.modules["database"].get_warning_recurrence_days.return_value = 5
        with patch.object(notifications, "send_resend_email", return_value=True) as mock_send:
            self._send()
        mock_send.assert_called_once()

    def test_escalated_warning_email_is_throttled_separately_from_fresh_key(self):
        """An escalated WARNING email must not re-send every single time a
        new (benign) occurrence of the same warning is logged -- it should
        only nag again after its own (48h) throttle window, independent of
        the original category:title throttle key."""
        sys.modules["database"].get_warning_recurrence_days.return_value = 3
        with patch.object(notifications, "send_resend_email", return_value=True) as mock_send:
            self._send()
            self._send()
        mock_send.assert_called_once()

    def test_db_error_during_warning_check_does_not_crash_and_does_not_email(self):
        """If logging/recurrence-checking itself fails (DB down), the safe
        default is to NOT email -- an unconfirmed recurrence must never be
        treated as a confirmed one."""
        sys.modules["database"].log_system_warning.side_effect = Exception("db down")
        with patch.object(notifications, "send_resend_email") as mock_send:
            self._send()
        mock_send.assert_not_called()

    def test_critical_severity_still_emails_immediately(self):
        with patch.object(notifications, "send_resend_email", return_value=True) as mock_send:
            self._send(severity="CRITICAL", category="DATABASE INFRASTRUCTURE", title="DB down")
        mock_send.assert_called_once()
        sys.modules["database"].log_system_warning.assert_not_called()
        sys.modules["database"].get_warning_recurrence_days.assert_not_called()

    def test_security_severity_still_emails_immediately(self):
        with patch.object(notifications, "send_resend_email", return_value=True) as mock_send:
            self._send(severity="SECURITY", category="SECURITY & PAYMENTS", title="Webhook signature mismatch")
        mock_send.assert_called_once()
        sys.modules["database"].log_system_warning.assert_not_called()

    def test_critical_severity_is_still_throttled_as_before(self):
        with patch.object(notifications, "send_resend_email", return_value=True) as mock_send:
            self._send(severity="CRITICAL", category="DATABASE INFRASTRUCTURE", title="DB down")
            self._send(severity="CRITICAL", category="DATABASE INFRASTRUCTURE", title="DB down")
        mock_send.assert_called_once()

    def test_default_severity_is_still_critical_and_emails_immediately(self):
        """severity isn't passed at all -- must keep behaving exactly like
        before this change (default CRITICAL, immediate email)."""
        with patch.object(notifications, "send_resend_email", return_value=True) as mock_send:
            notifications.send_system_incident_alert(
                category="DATABASE INFRASTRUCTURE",
                title="DB down",
                description="desc",
                impact="impact",
                action_required="action",
            )
        mock_send.assert_called_once()


if __name__ == "__main__":
    unittest.main(verbosity=2)

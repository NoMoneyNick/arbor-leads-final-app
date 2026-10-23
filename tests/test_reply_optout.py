"""Reply opt-out suppression: classification, add-only guarantees, Instantly client shape, route gating.
Database behaviour (duplicates, retries, saved point) is covered by
tests/postgres_concurrency/run_reply_optout_real_db_test.py against a real Postgres."""
import inspect
import os
import re
import sys
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import reply_optout as ro  # noqa: E402


def item(text, **kw):
    d = {"id": "e1", "lead": "owner@acme.co.uk", "from_address_email": "owner@acme.co.uk", "eaccount": "nick@treekey.uk",
         "ue_type": 2, "is_auto_reply": 0, "subject": "Re: tree work leads", "body": {"text": text}}
    d.update(kw)
    return d


class TestClassification(unittest.TestCase):
    def test_clear_opt_outs_are_suppressed(self):
        for t in ["No thanks", "no thank you.", "Not interested in your services.", "Please remove me from your list",
                  "STOP", "Unsubscribe", "Take us off your mailing list please", "Do not contact me again",
                  "Not interested.\n\nOn Mon, 12 Oct 2026, Nick wrote:\n> Do you want leads? yes"]:
            self.assertEqual(ro.classify_reply(t)[0], "suppressed", t)

    def test_mixed_or_interested_replies_go_to_review_not_suppression(self):
        for t in ["Not interested right now, maybe next year", "Can you call me? Otherwise not interested",
                  "Yes please, tell me more. Remove my colleague though", "Don't stop sending these, unsubscribe him only"]:
            self.assertEqual(ro.classify_reply(t)[0], "review", t)

    def test_long_reply_with_optout_wording_goes_to_review(self):
        self.assertEqual(ro.classify_reply("no thanks " + "word " * 60)[0], "review")

    def test_data_rights_and_complaints_are_review_even_with_clear_wording(self):
        for t in ["Unsubscribe me and delete my data under GDPR", "I will complain to the ICO. Remove me.",
                  "This breaches PECR, stop emailing"]:
            d, why = ro.classify_reply(t)
            self.assertEqual(d, "review", t)
            self.assertTrue(why.startswith("data-rights"))

    def test_possible_removal_wording_without_a_clear_phrase_goes_to_review(self):
        self.assertEqual(ro.classify_reply("this is spam")[0], "review")

    def test_ordinary_and_empty_replies_take_no_action(self):
        for t in ["Thanks, sounds good", "Who is this?", "", ">only quoted text\n> unsubscribe"]:
            self.assertIn(ro.classify_reply(t)[0], ("no_action", "review"))
        self.assertEqual(ro.classify_reply("Thanks, I will pass this to the team")[0], "no_action")
        self.assertEqual(ro.classify_reply("")[0], "no_action")

    def test_quoted_original_is_ignored(self):
        t = "Thanks for that\n\nOn Tue, 13 Oct 2026 at 09:00 Nick <nick@treekey.uk> wrote:\n> Reply 'no thanks' and I won't email again."
        self.assertEqual(ro.classify_reply(t)[0], "no_action")


class TestDecide(unittest.TestCase):
    def test_clear_optout_returns_lead_address_lowercased(self):
        d, _, lead, _ = ro.decide(item("No thanks", lead="Owner@Acme.co.uk", from_address_email="owner@acme.co.uk"))
        self.assertEqual((d, lead), ("suppressed", "owner@acme.co.uk"))

    def test_auto_replies_and_bounces_are_never_acted_on(self):
        self.assertEqual(ro.decide(item("unsubscribe", is_auto_reply=1))[0], "ignored_auto")
        self.assertEqual(ro.decide(item("unsubscribe", from_address_email="mailer-daemon@x.com"))[0], "ignored_auto")
        self.assertEqual(ro.decide(item("unsubscribe", subject="Automatic reply: out of office"))[0], "ignored_auto")

    def test_reply_from_a_different_person_needs_a_human(self):
        d, why, lead, excerpt = ro.decide(item("remove us", from_address_email="colleague@acme.co.uk"))
        self.assertEqual(d, "review")
        self.assertIn("different address", why)
        self.assertEqual(lead, "owner@acme.co.uk")

    def test_missing_or_invalid_lead_is_review_and_never_suppressed(self):
        for bad in ["", "not-an-email", None]:
            self.assertEqual(ro.decide(item("no thanks", lead=bad))[0], "review")

    def test_own_addresses_are_never_suppressed(self):
        self.assertEqual(ro.decide(item("no thanks", lead="nick@treekey.uk", from_address_email="nick@treekey.uk"))[0], "no_action")
        self.assertEqual(ro.decide(item("stop", lead="x@treekey.co.uk", from_address_email="x@treekey.co.uk"))[0], "no_action")

    def test_html_only_body_is_read(self):
        d = ro.decide(item(None, body={"text": "", "html": "<div>No thanks</div><blockquote>> old</blockquote>"}))[0]
        self.assertEqual(d, "suppressed")

    def test_excerpt_kept_only_for_review(self):
        self.assertEqual(ro.decide(item("No thanks"))[3], "")
        self.assertTrue(ro.decide(item("this is spam"))[3])


class TestAddOnly(unittest.TestCase):
    def test_module_never_deletes_or_rewrites_suppressions(self):
        src = inspect.getsource(ro)
        code = re.sub(r'"""[\s\S]*?"""', "", src)           # ignore docstrings
        code = re.sub(r"(?m)^\s*#.*$", "", code)
        self.assertNotRegex(code.upper(), r"DELETE\s+FROM")
        self.assertNotRegex(code.upper(), r"UPDATE\s+EMAIL_SUPPRESSIONS")
        self.assertNotRegex(code.upper(), r"TRUNCATE|DROP\s+TABLE")
        self.assertNotIn('"DELETE"', code)
        self.assertEqual(len(re.findall(r"INSERT INTO email_suppressions", code)), 1)

    def test_client_has_no_delete_capability(self):
        names = [n for n in dir(ro.InstantlyClient) if not n.startswith("__")]
        self.assertEqual(sorted(n for n in names if not n.startswith("_")), ["add_to_blocklist", "list_received"])


class TestClientRequests(unittest.TestCase):
    def _resp(self, body=b"{}"):
        r = MagicMock()
        r.__enter__.return_value = r
        r.read.return_value = body
        return r

    def test_list_request_shape_and_no_content_type_on_get(self):
        with patch("reply_optout.urllib.request.urlopen", return_value=self._resp(b'{"items": []}')) as uo:
            ro.InstantlyClient("KEY", base="https://api.instantly.ai/api/v2").list_received("2026-10-20T00:00:00.000Z")
        req = uo.call_args.args[0]
        self.assertEqual(req.get_method(), "GET")
        self.assertIn("email_type=received", req.full_url)
        self.assertIn("sort_order=asc", req.full_url)
        self.assertIn("min_timestamp_created=2026-10-20", req.full_url)
        self.assertFalse(req.has_header("Content-type"))
        self.assertEqual(req.get_header("Authorization"), "Bearer KEY")

    def test_blocklist_post_sends_only_bl_value(self):
        with patch("reply_optout.urllib.request.urlopen", return_value=self._resp()) as uo:
            ro.InstantlyClient("KEY", base="https://x/api/v2").add_to_blocklist("a@b.co.uk")
        req = uo.call_args.args[0]
        self.assertEqual((req.get_method(), req.full_url), ("POST", "https://x/api/v2/block-lists-entries"))
        self.assertEqual(req.data, b'{"bl_value": "a@b.co.uk"}')

    def test_http_errors_become_clear_messages_without_the_key(self):
        import urllib.error, io
        err = urllib.error.HTTPError("u", 401, "x", {}, io.BytesIO(b'{"message":"bad"}'))
        with patch("reply_optout.urllib.request.urlopen", side_effect=err):
            with self.assertRaises(ro.InstantlyError) as cm:
                ro.InstantlyClient("SECRETKEY").list_received("2026-10-20T00:00:00.000Z")
        self.assertIn("API key rejected", str(cm.exception))
        self.assertNotIn("SECRETKEY", str(cm.exception))


class TestRoutesAndSwitches(unittest.TestCase):
    def setUp(self):
        import test_access_control  # noqa: F401
        import main
        self.main = main

    def test_disabled_by_default_makes_no_calls(self):
        with patch.dict(os.environ, {}, clear=False), patch.object(self.main, "verify_cron_secret"), \
                patch.object(self.main.reply_optout, "is_enabled", return_value=False), \
                patch.object(self.main.reply_optout, "run_check") as rc, patch.object(self.main.database, "get_db_conn") as db:
            out = self.main.trigger_reply_optout_check(secret="s")
        self.assertEqual(out, {"status": "disabled"})
        rc.assert_not_called()
        db.assert_not_called()

    def test_enabled_without_key_fails_loudly(self):
        with patch.object(self.main, "verify_cron_secret"), patch.object(self.main.reply_optout, "is_enabled", return_value=True), \
                patch.object(self.main.reply_optout, "build_client", return_value=None):
            with self.assertRaises(self.main.HTTPException) as cm:
                self.main.trigger_reply_optout_check(secret="s")
        self.assertEqual(cm.exception.status_code, 500)

    def test_failure_returns_500_and_alerts(self):
        with patch.object(self.main, "verify_cron_secret"), patch.object(self.main.reply_optout, "is_enabled", return_value=True), \
                patch.object(self.main.reply_optout, "build_client", return_value=object()), \
                patch.object(self.main.database, "get_db_conn", return_value=MagicMock()), \
                patch.object(self.main.reply_optout, "run_check", return_value={"error": "Instantly: HTTP 401"}), \
                patch.object(self.main.reply_optout, "alert_if_needed") as alert:
            with self.assertRaises(self.main.HTTPException) as cm:
                self.main.trigger_reply_optout_check(secret="s")
        self.assertEqual(cm.exception.status_code, 500)
        alert.assert_called_once()

    def test_trigger_requires_the_cron_secret(self):
        with patch.object(self.main, "T_SEC", "right-secret"):
            with self.assertRaises(self.main.HTTPException) as cm:
                self.main.trigger_reply_optout_check(secret="wrong")
        self.assertEqual(cm.exception.status_code, 401)

    def test_enabled_switch_is_exactly_true(self):
        for val, expect in [("true", True), ("TRUE", True), ("", False), ("1", False), ("yes", False)]:
            with patch.dict(os.environ, {"REPLY_OPTOUT_ENABLED": val}):
                self.assertEqual(ro.is_enabled(), expect, val)


if __name__ == "__main__":
    unittest.main()

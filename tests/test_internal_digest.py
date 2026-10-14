"""tests/test_internal_digest.py -- daily internal digests (14 Oct 2026). No network, no database, no live email:
the Resend sender and the storage are fakes. Run: python -m unittest tests.test_internal_digest -v

The storage fake follows the same rules as PgStore (advisory-lock-style claim, done-today gate, live-claim check, retry
back-off); the real SQL is exercised separately by sql_check.py against a real Postgres."""
import ast
import base64
import csv
import json
import datetime as dt
import hashlib
import io
import os
import sys
import threading
import types
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import internal_digest as D

UTC = dt.timezone.utc


class FakeStore:
    """In-memory stand-in with the same observable behaviour as PgStore. `data` is the 'database': several FakeStore
    objects sharing one `data` model several app instances / a restart."""
    def __init__(self, data=None):
        self.data = data if data is not None else {"rows": [], "state": {}, "id": 0, "lock": threading.Lock()}

    def enqueue(self, rows):
        with self.data["lock"]:
            for r in rows:
                if r["kind"] in ("lead", "note") and any(x["topic"] == r["topic"] and x["item_key"] == r["item_key"] and x["kind"] in ("lead", "note") for x in self.data["rows"]):
                    continue
                self.data["id"] += 1
                self.data["rows"].append({**dict(id=self.data["id"], severity="", category="", title="", source="", detail="", link="", payload={},
                                                 occurred_at=self.data.get("clock", dt.datetime.now(UTC)), claim_token=None, claimed_at=None, sent_at=None), **r})
        return True

    def done_topics(self, today):
        return {k.split(":", 1)[1] for k, v in self.data["state"].items() if k.startswith("digest_done:") and v == today}

    def claim(self, topic, today, now, token):
        with self.data["lock"]:                      # == pg_advisory_xact_lock
            st = self.data["state"]
            if st.get("digest_done:" + topic) == today:
                return "done_today", []
            rt = st.get("retry:" + topic)
            if rt and rt["day"] == today:
                if rt["n"] >= D.MAX_ATTEMPTS:
                    return "gave_up_today", []
                if now < rt["next"]:
                    return "backoff", []
            pend = [r for r in self.data["rows"] if r["topic"] == topic and r["sent_at"] is None]
            if any(r["claim_token"] and r["claimed_at"] > now - dt.timedelta(minutes=D.CLAIM_TTL_MIN) for r in pend):
                return "in_progress", []
            for r in pend:
                r["claim_token"], r["claimed_at"] = token, now
            return "claimed", sorted((dict(r) for r in pend), key=lambda r: (r["occurred_at"], r["id"]))

    def finish_ok(self, topic, token, today):
        with self.data["lock"]:
            for r in self.data["rows"]:
                if r["claim_token"] == token:
                    r["sent_at"] = dt.datetime.now(UTC)
            self.data["state"]["digest_done:" + topic] = today
            self.data["state"].pop("retry:" + topic, None)

    def finish_fail(self, topic, token, today, now, error=""):
        with self.data["lock"]:
            for r in self.data["rows"]:
                if r["claim_token"] == token and r["sent_at"] is None:
                    r["claim_token"], r["claimed_at"] = None, None
            rt = self.data["state"].get("retry:" + topic)
            n = rt["n"] + 1 if rt and rt["day"] == today else 1
            self.data["state"]["retry:" + topic] = {"day": today, "n": n, "next": now + dt.timedelta(minutes=D.BACKOFF_MIN[min(n - 1, 2)])}

    def recurring_warnings(self):
        return self.data.get("warnings", [])

    def ensure_schema(self):
        pass

    def ensure_cutover(self):
        if self.data.get("leads_since") is None:                    # written once, never overwritten
            self.data["leads_since"] = D._PROCESS_START

    def fallback_reserve(self, key, now_ts):
        with self.data["lock"]:
            ok, new = D._fallback_decide(self.data["state"].get(D.FALLBACK_STATE_KEY), key, now_ts)
            if ok:
                self.data["state"][D.FALLBACK_STATE_KEY] = new
            return ok

    def missing_leads(self):
        """Same rule as BACKFILL_SQL: leads stored after the cut-over with no queue row (queued or sent)."""
        since = self.data.get("leads_since")
        if since is None:
            return []
        have = {r["item_key"] for r in self.data["rows"] if r["topic"] == "LEADS"}
        return [(l["reference"], l["council_source"], l["address"], l["summary"], l["lead_score"], l["lead_price"])
                for l in self.data.get("leads_table", []) if l["discovered_at"] >= since and l["reference"] not in have]

    def pending(self, topic=None):
        return [r for r in self.data["rows"] if r["sent_at"] is None and (topic is None or r["topic"] == topic)]


class Sender:
    def __init__(self, ok=True):
        self.calls, self.ok = [], ok

    def __call__(self, subject, body, attachments, idem):
        self.calls.append(dict(subject=subject, body=body, attachments=attachments, idem=idem))
        return self.ok


def at(y, m, d, h, mi=0):
    return dt.datetime(y, m, d, h, mi, tzinfo=UTC)


SUMMER_9AM = at(2026, 7, 15, 8, 5)     # 09:05 BST
LEAD = lambda i, city="Leeds": dict(ref=f"REF-{i:03d}", city=city, addr=f"{i} High St", summary=f"Fell oak {i}", score="Large", price=75, link=f"https://wa.me/{i}")


class DigestTests(unittest.TestCase):
    def setUp(self):
        self.store, self.send = FakeStore(), Sender()

    def run_(self, now=SUMMER_9AM, store=None, sender=None, **kw):
        return D.run_due_digests(now=now, store=store or self.store, sender=sender or self.send, base_url="https://app.example", **kw)

    # ---- 1. many new leads -> ONE email, every lead once, nothing silently dropped
    def test_many_new_leads_produce_one_daily_email_with_every_lead_once(self):
        D.enqueue_leads([LEAD(i, "Leeds" if i % 2 else "London") for i in range(45)], store=self.store)
        D.enqueue_leads([LEAD(3), LEAD(7)], store=self.store)           # the same leads reported again by a later scan
        out = self.run_()
        self.assertEqual(out["LEADS"], "sent")
        self.assertEqual(len(self.send.calls), 1)
        c = self.send.calls[0]
        self.assertIn("45 new leads", c["subject"])
        self.assertEqual(len(c["attachments"]), 1)
        rows = list(csv.reader(io.StringIO(base64.b64decode(c["attachments"][0]["content"]).decode())))
        refs = [r[0] for r in rows[1:]]
        self.assertEqual(len(refs), 45)
        self.assertEqual(len(set(refs)), 45)
        self.assertIn("Showing 30 of 45", c["body"])
        self.assertEqual(self.store.pending("LEADS"), [])

    def test_small_batch_is_listed_inline_without_attachment(self):
        D.enqueue_leads([LEAD(1), LEAD(2)], store=self.store)
        self.run_()
        c = self.send.calls[0]
        self.assertEqual(c["attachments"], [])
        self.assertIn("REF-001", c["body"])
        self.assertIn("REF-002", c["body"])
        self.assertIn("wa.me/1", c["body"])

    def test_a_lead_already_sent_is_never_sent_again(self):
        D.enqueue_leads([LEAD(1)], store=self.store)
        self.run_()
        D.enqueue_leads([LEAD(1), LEAD(2)], store=self.store)           # next day
        self.run_(now=SUMMER_9AM + dt.timedelta(days=1))
        self.assertEqual(len(self.send.calls), 2)
        self.assertNotIn("REF-001", self.send.calls[1]["body"])
        self.assertIn("REF-002", self.send.calls[1]["body"])

    def test_html_in_lead_text_is_escaped(self):
        D.enqueue_leads([dict(LEAD(1), summary="<script>x</script>")], store=self.store)
        self.run_()
        self.assertNotIn("<script>", self.send.calls[0]["body"])

    # ---- 2. repeated scanner errors -> ONE issues email with counts, sources, resolved flag
    def test_repeated_scanner_errors_are_combined_into_one_digest(self):
        for city in ("LEEDS", "LEEDS", "LONDON", "MANCHESTER", "LONDON"):
            D.enqueue_issue("SCANNER", "SCRAPER API KEY", f"UKPLANNINGAPI.CO.UK FAILED FOR ALL PREFIXES IN {city}", description="key rejected",
                            action="Check key", severity="CRITICAL", store=self.store)
        D.enqueue_issue("SCANNER", "AUTOMATED SCRAPER PIPELINE", "DAILY PLANNING RADAR SWEEP (STAGE 1) FAILED", description="boom", store=self.store)
        out = self.run_()
        self.assertEqual(out["SCANNER"], "sent")
        self.assertEqual(len([c for c in self.send.calls if "Scanner" in c["subject"]]), 1)
        body = self.send.calls[0]["body"]
        self.assertIn("FAILED FOR ALL PREFIXES", body)
        self.assertIn("5x", body)                       # five occurrences combined into one entry
        for place in ("LEEDS", "LONDON", "MANCHESTER"):
            self.assertIn(place, body)
        self.assertIn("Not resolved", body)
        self.assertIn("Check key", body)                # what to do is in the email
        self.assertIn("2 items, 2 unresolved", self.send.calls[0]["subject"])

    def test_resolved_marker_is_shown_and_is_per_source(self):
        t0 = at(2026, 7, 14, 10)
        self.store.data["clock"] = t0
        D.enqueue_issue("SCANNER", "AUTONOMOUS CYCLE", "AUTONOMOUS_DAILY_CYCLE CRASHED BEFORE FINISHING", store=self.store)
        D.enqueue_issue("SCANNER", "AUTONOMOUS CYCLE", "OTHER_ACTION CRASHED BEFORE FINISHING", store=self.store)
        self.store.data["clock"] = t0 + dt.timedelta(hours=2)
        D.mark_resolved("SCANNER", "AUTONOMOUS CYCLE", "AUTONOMOUS_DAILY_CYCLE CRASHED BEFORE FINISHING", store=self.store)
        rows = [dict(r) for r in self.store.data["rows"]]
        entries, _ = D.aggregate_issues(rows)
        self.assertEqual(len(entries), 1)                # same issue, two sources
        self.assertIsNone(entries[0]["resolved_at"])     # one source recovered, the other did not -> NOT resolved
        D.mark_resolved("SCANNER", "AUTONOMOUS CYCLE", "OTHER_ACTION CRASHED BEFORE FINISHING", store=self.store)
        entries, _ = D.aggregate_issues([dict(r) for r in self.store.data["rows"]])
        self.assertIsNotNone(entries[0]["resolved_at"])

    def test_recurring_warnings_are_included_by_topic_in_the_same_email(self):
        self.store.data["warnings"] = [dict(category="SCRAPER PAGE STRUCTURE", title="x.gov.uk may have changed", days_seen=4),
                                       dict(category="REVENUE & BILLING", title="CUSTOMER PAYMENT FAILED", days_seen=3)]
        out = self.run_()
        self.assertEqual(out["SCANNER"], "sent")
        self.assertEqual(out["BILLING"], "sent")
        self.assertEqual(len(self.send.calls), 2)
        self.assertIn("x.gov.uk", [c for c in self.send.calls if "Scanner" in c["subject"]][0]["body"])

    # ---- 3. empty digests send nothing
    def test_empty_topics_send_nothing(self):
        out = self.run_()
        self.assertEqual(self.send.calls, [])
        self.assertTrue(all(v == "empty" for v in out.values()))
        self.assertEqual(self.run_(now=SUMMER_9AM + dt.timedelta(minutes=20)), {})   # done for the day: no more work

    def test_only_resolved_markers_send_nothing(self):
        D.mark_resolved("SCANNER", "AUTONOMOUS CYCLE", "AUTONOMOUS_DAILY_CYCLE CRASHED BEFORE FINISHING", store=self.store)
        self.run_()
        self.assertEqual(self.send.calls, [])
        self.assertEqual(self.store.pending(), [])

    # ---- 4. failed sends keep their items; controlled retry
    def test_failed_send_preserves_pending_items_and_retries_with_backoff(self):
        D.enqueue_leads([LEAD(1)], store=self.store)
        bad = Sender(ok=False)
        self.assertEqual(self.run_(sender=bad)["LEADS"], "failed")
        self.assertEqual(len(self.store.pending("LEADS")), 1)                       # still pending, claim released
        self.assertIsNone(self.store.pending("LEADS")[0]["claim_token"])
        # too soon -> no retry storm
        self.assertEqual(self.run_(now=SUMMER_9AM + dt.timedelta(minutes=20), sender=bad)["LEADS"], "backoff")
        self.assertEqual(len(bad.calls), 1)
        # after the back-off -> retried, and succeeds
        good = Sender()
        self.assertEqual(self.run_(now=SUMMER_9AM + dt.timedelta(minutes=31), sender=good)["LEADS"], "sent")
        self.assertEqual(len(good.calls), 1)
        self.assertEqual(self.store.pending("LEADS"), [])

    def test_gives_up_for_the_day_after_max_attempts_but_keeps_items_for_tomorrow(self):
        D.enqueue_leads([LEAD(1)], store=self.store)
        bad, t = Sender(ok=False), SUMMER_9AM
        for step in (0, 31, 31 + 61, 31 + 61 + 121):
            self.run_(now=SUMMER_9AM + dt.timedelta(minutes=step), sender=bad)
        self.assertEqual(len(bad.calls), D.MAX_ATTEMPTS)
        self.assertEqual(self.run_(now=SUMMER_9AM + dt.timedelta(hours=8), sender=bad)["LEADS"], "gave_up_today")
        self.assertEqual(len(bad.calls), D.MAX_ATTEMPTS)
        self.assertEqual(len(self.store.pending("LEADS")), 1)
        good = Sender()
        self.assertEqual(self.run_(now=SUMMER_9AM + dt.timedelta(days=1), sender=good)["LEADS"], "sent")

    def test_sender_exception_is_treated_as_failure(self):
        D.enqueue_leads([LEAD(1)], store=self.store)
        def boom(*a):
            raise RuntimeError("network down")
        self.assertEqual(self.run_(sender=boom)["LEADS"], "failed")
        self.assertEqual(len(self.store.pending("LEADS")), 1)

    # ---- 5. restart / overlapping runs never duplicate
    def test_overlapping_runs_send_one_digest(self):
        D.enqueue_leads([LEAD(i) for i in range(5)], store=self.store)
        gate, calls = threading.Event(), []
        def slow(subject, body, att, idem):
            calls.append(subject)
            gate.wait(2)
            return True
        results = []
        ts = [threading.Thread(target=lambda: results.append(self.run_(sender=slow, topics=("LEADS",)))) for _ in range(4)]
        for t in ts:
            t.start()
        threading.Event().wait(0.3)
        gate.set()
        for t in ts:
            t.join()
        self.assertEqual(len(calls), 1)
        self.assertEqual(sorted(r.get("LEADS") for r in results if r), sorted(["sent"] + ["in_progress"] * 3))

    def test_restart_after_send_does_not_duplicate(self):
        D.enqueue_leads([LEAD(1)], store=self.store)
        self.run_()
        restarted = FakeStore(self.store.data)           # new process, same database
        again = Sender()
        self.run_(now=SUMMER_9AM + dt.timedelta(hours=3), store=restarted, sender=again)
        self.assertEqual(again.calls, [])

    def test_crash_after_resend_accepted_reuses_the_same_idempotency_key(self):
        D.enqueue_leads([LEAD(1), LEAD(2)], store=self.store)
        s1 = Sender()
        real = self.store.finish_ok
        self.store.finish_ok = lambda *a: (_ for _ in ()).throw(RuntimeError("db died after send"))
        self.run_(sender=s1)
        self.store.finish_ok = real
        for r in self.store.data["rows"]:                # the dead sender's claim expires
            r["claimed_at"] -= dt.timedelta(minutes=D.CLAIM_TTL_MIN + 1)
        s2 = Sender()
        self.run_(now=SUMMER_9AM + dt.timedelta(minutes=40), sender=s2)
        self.assertEqual(s1.calls[0]["idem"], s2.calls[0]["idem"])   # Resend de-duplicates the replay

    # ---- 6. schedule: 08:00 Europe/London, DST aware
    def test_not_before_8am_london_in_winter_and_summer(self):
        D.enqueue_leads([LEAD(1)], store=self.store)
        self.assertEqual(self.run_(now=at(2026, 7, 15, 6, 59)), {})      # 07:59 BST
        self.assertEqual(self.run_(now=at(2026, 12, 15, 7, 30)), {})     # 07:30 GMT
        self.assertEqual(self.send.calls, [])
        self.assertEqual(self.run_(now=at(2026, 12, 15, 8, 0))["LEADS"], "sent")          # 08:00 GMT
        D.enqueue_leads([LEAD(2)], store=self.store)
        self.assertEqual(self.run_(now=at(2027, 7, 15, 7, 0))["LEADS"], "sent")           # 08:00 BST (a later day)

    def test_dst_changeover_days(self):
        self.assertEqual(D.to_london(at(2026, 3, 29, 0, 30)).hour, 0)    # still GMT
        self.assertEqual(D.to_london(at(2026, 3, 29, 1, 0)).hour, 2)     # BST starts at 01:00 UTC
        self.assertEqual(D.to_london(at(2026, 10, 25, 0, 30)).hour, 1)   # still BST
        self.assertEqual(D.to_london(at(2026, 10, 25, 1, 0)).hour, 1)    # GMT again (01:00 UTC = 01:00 GMT)
        with patch.dict(sys.modules, {"zoneinfo": None}):               # no tz database on the host -> built-in EU rule agrees
            self.assertEqual(D.to_london(at(2026, 7, 1, 7, 0)).hour, 8)
            self.assertEqual(D.to_london(at(2026, 12, 1, 7, 0)).hour, 7)
            self.assertEqual(D.to_london(at(2026, 3, 29, 1, 0)).hour, 2)
            self.assertEqual(D.to_london(at(2026, 10, 25, 1, 0)).hour, 1)

    def test_at_most_one_email_per_topic_per_day(self):
        D.enqueue_leads([LEAD(1)], store=self.store)
        self.run_()
        D.enqueue_leads([LEAD(2)], store=self.store)
        for m in (20, 40, 60, 600):
            self.run_(now=SUMMER_9AM + dt.timedelta(minutes=m))
        self.assertEqual(len(self.send.calls), 1)
        self.assertEqual(len(self.store.pending("LEADS")), 1)           # waits for tomorrow 08:00

    # ---- 7. routing: what stays immediate
    def test_routing_table(self):
        T = D.digest_topic_for_critical
        for cat in ("SCRAPER API KEY", "AUTOMATED SCRAPER PIPELINE", "AUTONOMOUS CYCLE", "SCRAPER OUTAGE", "CONTRACTOR DISCOVERY"):
            self.assertEqual(T(cat), "SCANNER", cat)
        for cat in ("SECURITY & API KEYS", "SECURITY & PAYMENTS", "SECURITY & ABUSE", "DATABASE INFRASTRUCTURE", "DATABASE SCHEMA MIGRATION",
                    "DATABASE SECURITY", "REVENUE & BILLING", "SOMETHING NEW"):
            self.assertIsNone(T(cat), cat)
        self.assertEqual(D.topic_for_category("DATA RETENTION"), "SYSTEM")
        self.assertEqual(D.topic_for_category("REVENUE & BILLING"), "BILLING")
        self.assertEqual(D.topic_for_category("SECURITY & ABUSE"), "SECURITY")
        self.assertEqual(set(D.TOPICS), {"LEADS", "SCANNER", "BILLING", "SECURITY", "SYSTEM"})


def _load_notifications():
    import importlib
    import notifications
    return importlib.reload(notifications)


class IncidentAlertRoutingTests(unittest.TestCase):
    def setUp(self):
        self.state = {}
        fake_db = types.ModuleType("database")
        fake_db.get_system_state = lambda k: self.state.get(k)
        fake_db.set_system_state = lambda k, v: self.state.__setitem__(k, v) or True
        fake_db.log_system_warning = lambda *a: True
        self.p = patch.dict(sys.modules, {"database": fake_db})
        self.p.start()
        self.addCleanup(self.p.stop)
        self.n = _load_notifications()
        self.n._ALERT_THROTTLE_CACHE.clear()
        self.queued = []
        self.q = patch.object(D, "enqueue_issue", side_effect=lambda *a, **k: self.queued.append((a, k)) or True)
        self.q.start()
        self.addCleanup(self.q.stop)

    def alert(self, category, title="T", severity="CRITICAL"):
        self.n.send_system_incident_alert(category=category, title=title, description="d", impact="i", action_required="a", severity=severity)

    def test_routine_scanner_failure_is_queued_not_emailed(self):
        with patch.object(self.n, "send_resend_email", return_value=True) as send:
            self.alert("SCRAPER API KEY", "UKPLANNINGAPI.CO.UK FAILED FOR ALL PREFIXES IN LEEDS")
            self.alert("AUTOMATED SCRAPER PIPELINE", "DAILY PLANNING RADAR SWEEP (STAGE 1) FAILED")
        send.assert_not_called()
        self.assertEqual(len(self.queued), 2)
        self.assertEqual(self.queued[0][0][0], "SCANNER")

    def test_urgent_alerts_stay_immediate_and_are_deduplicated_across_restarts(self):
        for cat in ("SECURITY & PAYMENTS", "DATABASE INFRASTRUCTURE", "REVENUE & BILLING", "SECURITY & API KEYS"):
            with patch.object(self.n, "send_resend_email", return_value=True) as send:
                self.alert(cat, "incident")
                self.alert(cat, "incident")                       # repeat: de-duplicated
                self.assertEqual(send.call_count, 1, cat)
            self.n._ALERT_THROTTLE_CACHE.clear()                  # simulate a restart: memory gone, database remembers
            with patch.object(self.n, "send_resend_email", return_value=True) as send:
                self.alert(cat, "incident")
                send.assert_not_called()
        self.assertEqual(self.queued, [])

    def test_failed_immediate_send_does_not_start_the_dedupe_window(self):
        with patch.object(self.n, "send_resend_email", return_value=False):
            self.alert("DATABASE INFRASTRUCTURE", "down")
        with patch.object(self.n, "send_resend_email", return_value=True) as send:
            self.alert("DATABASE INFRASTRUCTURE", "down")
            self.assertEqual(send.call_count, 1)

    def test_if_the_queue_is_unavailable_a_scanner_alert_is_not_lost(self):
        self.q.stop()
        with patch.object(D, "enqueue_issue", return_value=False), patch.object(D, "_get_store", return_value=FakeStore()), \
                patch.object(self.n, "send_resend_email", return_value=True) as send:
            self.alert("SCRAPER API KEY", "FAILED IN LEEDS")
        self.assertEqual(send.call_count, 1)
        self.q.start()

    def test_warning_severity_is_still_log_only(self):
        with patch.object(self.n, "send_resend_email") as send:
            self.alert("SCRAPER TLS FALLBACK", "x.gov.uk", severity="WARNING")
        send.assert_not_called()
        self.assertEqual(self.queued, [])


class LeadAlertTests(unittest.TestCase):
    def test_admin_lead_emails_are_queued_and_no_internal_email_is_sent(self):
        fake_db = types.ModuleType("database")
        fake_db.get_system_state = lambda k: None
        p = patch.dict(sys.modules, {"database": fake_db})
        p.start()
        self.addCleanup(p.stop)
        n = _load_notifications()
        captured = []
        leads = [dict(ref=f"R{i}", addr=f"{i} Road", summary="fell tree", lead_score="large", lead_price=75) for i in range(12)]
        with patch.object(D, "enqueue_leads", side_effect=lambda rows, **k: captured.append(rows) or True), \
             patch.object(n, "send_resend_email") as send, patch.object(n.requests, "post") as post:
            # customer matching needs the database; with no customers the function must still queue the admin side only
            fake_db.release_expired_reservations = lambda: None
            fake_db.get_active_subscribers_by_seniority = lambda: []      # no customers: only the internal (admin) side is exercised
            n.dispatch_lead_alerts("Leeds", leads)
        send.assert_not_called()
        post.assert_not_called()
        self.assertEqual(len(captured), 1)
        self.assertEqual(len(captured[0]), 12)
        self.assertEqual(captured[0][0]["score"], "Large")
        self.assertEqual(captured[0][0]["city"], "Leeds")


class BrokenStore:
    """The queue database is down."""
    def enqueue(self, rows):
        raise RuntimeError("database unavailable")

    def missing_leads(self):
        raise RuntimeError("database unavailable")

    def ensure_schema(self):
        raise RuntimeError("database unavailable")

    def ensure_cutover(self):
        raise RuntimeError("database unavailable")

    def fallback_reserve(self, key, now_ts):
        raise RuntimeError("database unavailable")

    def done_topics(self, today):
        raise RuntimeError("database unavailable")


class QueueDownStore(FakeStore):
    """The queue table cannot be written (missing table / permission) but the rest of the database, incl. the durable fallback
    limit in system_state, works."""
    def enqueue(self, rows):
        raise RuntimeError("queue table unusable")


def _reset_outage_state():
    D._buffer.clear()
    D._cutover_done = False


class QueueOutageTests(unittest.TestCase):
    """Point 1: fallback emails are capped DURABLY (database), survive restarts, and fail closed without a database."""
    def setUp(self):
        _reset_outage_state()
        self.addCleanup(_reset_outage_state)
        fake_db = types.ModuleType("database")
        self.state = {}
        fake_db.get_system_state = lambda k: self.state.get(k)
        fake_db.set_system_state = lambda k, v: self.state.__setitem__(k, v) or True
        fake_db.log_system_warning = lambda *a: True
        p = patch.dict(sys.modules, {"database": fake_db})
        p.start()
        self.addCleanup(p.stop)
        self.n = _load_notifications()
        self.n._ALERT_THROTTLE_CACHE.clear()
        self.shared = {"rows": [], "state": {}, "id": 0, "lock": threading.Lock()}

    def use(self, store):
        q = patch.object(D, "_get_store", return_value=store)
        q.start()
        self.addCleanup(q.stop)

    def alert(self, title):
        self.n.send_system_incident_alert(category="SCRAPER API KEY", title=title, description="d", impact="i", action_required="a", severity="CRITICAL")

    def test_no_database_means_no_fallback_email_at_all(self):
        self.use(BrokenStore())
        with patch.object(self.n, "send_resend_email", return_value=True) as send:
            for i in range(40):
                self.alert(f"FAILED IN COUNCIL {i}")
            self.n.send_api_quota_warning_email(api_name="UK PLANNING DATA API", current_calls=450, cap=500)
        send.assert_not_called()
        self.assertEqual(len(D._buffer), 41)                   # 40 alerts + the quota warning: held, not lost, while the app keeps running

    def test_queue_unusable_but_database_readable_allows_a_tiny_capped_number(self):
        self.use(QueueDownStore(self.shared))
        with patch.object(self.n, "send_resend_email", return_value=True) as send:
            for i in range(40):
                self.alert(f"FAILED IN COUNCIL {i}")
        self.assertEqual(send.call_count, D.FALLBACK_PER_HOUR)

    def test_the_cap_survives_a_restart(self):
        self.use(QueueDownStore(self.shared))
        with patch.object(self.n, "send_resend_email", return_value=True) as send:
            for i in range(10):
                self.alert(f"FAILED IN COUNCIL {i}")
        self.assertEqual(send.call_count, D.FALLBACK_PER_HOUR)
        D._buffer.clear()                                       # restart: all process memory gone...
        self.n._ALERT_THROTTLE_CACHE.clear()
        self.use(QueueDownStore(self.shared))                   # ...new process, same database
        with patch.object(self.n, "send_resend_email", return_value=True) as send2:
            for i in range(10):
                self.alert(f"FAILED IN COUNCIL {i}")            # the same alerts again
            self.alert("A BRAND NEW FAILURE")
        send2.assert_not_called()

    def test_the_same_error_repeating_sends_one_email(self):
        self.use(QueueDownStore(self.shared))
        with patch.object(self.n, "send_resend_email", return_value=True) as send:
            for _ in range(100):
                self.alert("FAILED IN LEEDS")
        self.assertEqual(send.call_count, 1)

    def test_daily_cap_and_rolling_window(self):
        store, sent, t0 = FakeStore(), [], 1_700_000_000.0
        for i in range(60):                                     # a different alert every 20 minutes for 20 hours
            if D.fallback_allowed(f"k{i}", now_ts=t0 + i * 1200, store=store):
                sent.append(t0 + i * 1200)
        self.assertEqual(len(sent), D.FALLBACK_PER_DAY)
        self.assertTrue(all(sum(1 for y in sent if x <= y < x + 3600) <= D.FALLBACK_PER_HOUR for x in sent))
        self.assertTrue(D.fallback_allowed("later", now_ts=t0 + 90000, store=store))   # a day later the allowance is back

    def test_corrupt_limit_state_is_treated_as_empty_not_as_unlimited(self):
        store = FakeStore()
        store.data["state"][D.FALLBACK_STATE_KEY] = "{not json"
        self.assertTrue(D.fallback_allowed("a", now_ts=1_700_000_000.0, store=store))
        self.assertFalse(D.fallback_allowed("a", now_ts=1_700_000_001.0, store=store))

    def test_quota_warning_fallback_is_capped_and_deduplicated(self):
        self.use(QueueDownStore(self.shared))
        with patch.object(self.n, "send_resend_email", return_value=True) as send:
            for _ in range(10):
                self.n.send_api_quota_warning_email(api_name="UK PLANNING DATA API", current_calls=450, cap=500)
        self.assertEqual(send.call_count, 1)

    def test_held_items_reach_the_digest_after_the_queue_recovers(self):
        self.use(BrokenStore())
        with patch.object(self.n, "send_resend_email", return_value=True):
            for i in range(5):
                self.alert(f"FAILED IN COUNCIL {i}")
        good = FakeStore()
        self.assertEqual(D.flush_buffer(good), 5)
        self.assertEqual(D._buffer, [])
        send = Sender()
        D.run_due_digests(now=SUMMER_9AM, store=good, sender=send, base_url="https://app.example")
        self.assertEqual(len(send.calls), 1)

    def test_flush_keeps_items_while_the_queue_is_still_down(self):
        D._buffer_rows([dict(topic="SCANNER", kind="issue", item_key="k", title="t")])
        self.assertEqual(D.flush_buffer(BrokenStore()), 0)
        self.assertEqual(len(D._buffer), 1)


class LeadRecoveryTests(unittest.TestCase):
    """Point 2: a lead whose queueing failed is picked up later; already-notified leads are never replayed."""
    def setUp(self):
        _reset_outage_state()
        self.addCleanup(_reset_outage_state)
        self.store = FakeStore()
        self.store.data["leads_since"] = at(2026, 7, 14, 6)

    def lead(self, ref, when):
        return dict(reference=ref, council_source="Leeds", address="1 Rd", summary="fell oak", lead_score="large", lead_price=75, discovered_at=when)

    def test_lead_whose_queueing_failed_is_queued_by_the_sweep_and_sent_once(self):
        self.store.data["leads_table"] = [self.lead("NEW-1", at(2026, 7, 14, 9)), self.lead("NEW-2", at(2026, 7, 14, 10))]
        with patch.object(D, "_get_store", return_value=BrokenStore()):
            self.assertFalse(D.enqueue_leads([dict(LEAD(1), ref="NEW-1")]))                       # queueing failed: no exception, nothing queued
        self.assertEqual(self.store.pending("LEADS"), [])
        send = Sender()
        D.run_due_digests(now=SUMMER_9AM, store=self.store, sender=send, base_url="https://app.example")
        self.assertEqual(len(send.calls), 1)
        self.assertIn("2 new leads", send.calls[0]["subject"])
        send2 = Sender()
        D.run_due_digests(now=SUMMER_9AM + dt.timedelta(days=1), store=self.store, sender=send2, base_url="https://app.example")
        self.assertEqual(send2.calls, [])                                       # nothing replayed next day

    def test_leads_already_queued_or_sent_are_not_added_again(self):
        self.store.data["leads_table"] = [self.lead("A", at(2026, 7, 14, 9)), self.lead("B", at(2026, 7, 14, 9))]
        D.enqueue_leads([dict(LEAD(1), ref="A")], store=self.store)
        send = Sender()
        D.run_due_digests(now=SUMMER_9AM, store=self.store, sender=send, base_url="https://app.example")
        self.assertIn("2 new leads", send.calls[0]["subject"])                 # A (queued) + B (swept) = 2, A not doubled
        D.backfill_leads(self.store)
        self.assertEqual(self.store.pending("LEADS"), [])                       # A and B are now 'sent': never re-added

    def test_database_decimal_prices_are_handled(self):
        import decimal
        l = self.lead("D1", at(2026, 7, 14, 9)); l["lead_price"] = decimal.Decimal("75.00")
        self.store.data["leads_table"] = [l]
        self.assertEqual(D.backfill_leads(self.store), 1)
        json.dumps(self.store.pending("LEADS")[0]["payload"])          # must be serialisable like the real INSERT does
        self.assertEqual(self.store.pending("LEADS")[0]["payload"]["price"], 75)

    def test_leads_from_before_the_cutover_are_never_replayed(self):
        self.store.data["leads_table"] = [self.lead("OLD", at(2026, 7, 13, 12))]
        self.assertEqual(D.backfill_leads(self.store), 0)

    def test_cutover_is_the_process_start_even_if_it_is_written_late(self):
        """Activation at T0. A lead is saved right after T0 and its queueing fails; the first attempt to record the cut-over
        also failed (database blip) and only succeeds later. The lead must still be recovered, older leads must not."""
        store = FakeStore()
        t0 = D._PROCESS_START
        store.data["leads_table"] = [self.lead("BEFORE-ACTIVATION", t0 - dt.timedelta(hours=3)),
                                     self.lead("JUST-AFTER", t0 + dt.timedelta(milliseconds=5))]
        with patch.object(D, "_get_store", return_value=BrokenStore()):
            self.assertFalse(D.ensure_cutover())                       # startup write failed
            self.assertFalse(D.enqueue_leads([dict(LEAD(1), ref="JUST-AFTER")]))   # queueing failed too
        self.assertIsNone(store.data.get("leads_since"))
        self.assertTrue(D.ensure_cutover(store))                       # a later attempt succeeds
        self.assertEqual(store.data["leads_since"], t0)                # value is the START time, not 'now'
        send = Sender()
        D.run_due_digests(now=SUMMER_9AM, store=store, sender=send, base_url="https://app.example")
        self.assertEqual(len(send.calls), 1)
        self.assertIn("1 new lead", send.calls[0]["subject"])
        self.assertIn("JUST-AFTER", send.calls[0]["body"])
        self.assertNotIn("BEFORE-ACTIVATION", send.calls[0]["body"])

    def test_cutover_is_written_once_and_never_moved_later(self):
        store = FakeStore()
        D.ensure_cutover(store)
        first = store.data["leads_since"]
        D._cutover_done = False
        store.data["leads_since"] = first
        with patch.object(D, "_PROCESS_START", first + dt.timedelta(days=5)):
            D.ensure_cutover(store)
        self.assertEqual(store.data["leads_since"], first)

    def test_app_startup_records_the_cutover_before_the_scheduler_thread_starts(self):
        with open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "main.py"), encoding="utf-8") as fh:
            src = fh.read()
        i = src.index("def _start_autonomous_scheduler():")
        body = src[i:i + 900]
        self.assertLess(body.index("internal_digest.ensure_cutover()"), body.index("threading.Thread("))

    def test_no_cutover_marker_means_no_sweep(self):
        self.store.data["leads_since"] = None
        self.store.data["leads_table"] = [self.lead("X", at(2026, 7, 14, 9))]
        self.assertEqual(D.backfill_leads(self.store), 0)

    def test_sweep_failure_does_not_stop_the_digest(self):
        self.store.missing_leads = lambda: (_ for _ in ()).throw(RuntimeError("db"))
        D.enqueue_leads([LEAD(1)], store=self.store)
        send = Sender()
        D.run_due_digests(now=SUMMER_9AM, store=self.store, sender=send, base_url="https://app.example")
        self.assertEqual(len(send.calls), 1)


class ResolvedEvidenceTests(unittest.TestCase):
    """Point 3: 'resolved' needs a later success marker; silence or ageing never resolves anything."""
    def test_issue_with_no_success_marker_stays_unresolved(self):
        store = FakeStore()
        D.enqueue_issue("SCANNER", "SILENT SOURCE", "CHESHIRE WEST HAS PRODUCED ZERO LEADS IN 3 DAYS", store=store)
        entries, _ = D.aggregate_issues([dict(r) for r in store.data["rows"]])
        self.assertIsNone(entries[0]["resolved_at"])

    def test_a_success_marker_older_than_the_latest_failure_does_not_resolve(self):
        store = FakeStore()
        store.data["clock"] = at(2026, 7, 14, 8)
        D.mark_resolved("SCANNER", "AUTONOMOUS CYCLE", "AUTONOMOUS_DAILY_CYCLE CRASHED BEFORE FINISHING", store=store)
        store.data["clock"] = at(2026, 7, 14, 12)
        D.enqueue_issue("SCANNER", "AUTONOMOUS CYCLE", "AUTONOMOUS_DAILY_CYCLE CRASHED BEFORE FINISHING", store=store)
        entries, _ = D.aggregate_issues([dict(r) for r in store.data["rows"]])
        self.assertIsNone(entries[0]["resolved_at"])

    def test_recurring_warning_list_makes_no_resolved_claim_and_an_aged_out_warning_is_simply_absent(self):
        store = FakeStore()
        store.data["warnings"] = [dict(category="SCRAPER PAGE STRUCTURE", title="x may have changed", days_seen=7)]
        send = Sender()
        D.run_due_digests(now=SUMMER_9AM, store=store, sender=send, base_url="https://app.example")
        self.assertNotIn("Resolved", send.calls[0]["body"])
        store.data["warnings"] = []                       # the warning ages out of the 7-day window
        send2 = Sender()
        D.run_due_digests(now=SUMMER_9AM + dt.timedelta(days=1), store=store, sender=send2, base_url="https://app.example")
        self.assertEqual(send2.calls, [])                 # silence, and no 'resolved' notice invented


class CustomerPathsUnchangedTests(unittest.TestCase):
    """Customer-facing / outreach / login email functions must be byte-for-byte what they were before this change.
    Hashes were taken from the pre-change source."""
    EXPECTED = {
        "send_transactional_email": "72f7d255e532c9d5d3f8d55dc764cb1e224c4e07be61d2b470f634d5dc2e7e45",
        "send_purchased_lead_email": "62e61b5cd9a27ac566694b20c4f0d34184ee67dac621080fab120d219b11f1fb",
        "_send_purchased_lead_email_inner": "470dbcae05e876850b008b21e143485e2c1517995039679c2af1740b638e8062",
        "send_free_lead_granted_email": "f6db52777d1288f19501bc5e03b41cd5829e449b8567e824ed4862612008a064",
        "_send_free_lead_granted_email_inner": "654db89b15c829ad833d322945885e438f42daee3726aef4f90e92d8e7c908be",
        "_list_unsubscribe_headers": "e22f292a0fade81c79256ce68b59cc4de6fb92698f8911e39a17d6c987259564",
        "send_cold_email_1": "f04cbf1065963436fa4ee1d491e51e218dbb9463f8206262710b9e268ec4dbc9",
        "send_free_lead_code_email": "212c72c45f204fd450c240645e882acc76d31a0ba9aad9ad774085395bdfc038",
        "send_teaser_lead_email": "4f31a7e5e1f9ca38fc3ee20c9d1f470d7d205d3693ae904e23284cb0b75d5493",
        "send_teaser_email_batch": "5faa386e50748f52b841c638c57404fa507fc1fc80d114aae6dd1a38c522958f",
        "create_whatsapp_link": "43c58af601f87d63a1c94e464181a64ce5fa3528c7fbca3eadf62925a9960d58",
    }   # function name -> sha256 of its pre-change source

    def test_customer_email_functions_are_unchanged(self):
        src = open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "notifications.py"), encoding="utf-8").read()
        tree = ast.parse(src)
        got = {n.name: hashlib.sha256(ast.get_source_segment(src, n).encode()).hexdigest() for n in tree.body if isinstance(n, ast.FunctionDef)}
        for name, digest in self.EXPECTED.items():
            self.assertEqual(got.get(name), digest, f"{name} changed")

    def test_send_resend_email_default_behaviour_is_unchanged(self):
        n = _load_notifications()
        n.RESEND_API_KEY, n.TEST_EMAIL = "k", "me@example.com"
        with patch.object(n.requests, "post") as post, patch.object(n, "_record_email_attempt"):
            post.return_value.status_code = 200
            self.assertTrue(n.send_resend_email("s", "b"))
        kw = post.call_args.kwargs
        self.assertEqual(kw["json"], {"from": "TreeKey Intelligence <leads@mail.treekey.co.uk>", "to": ["me@example.com"], "subject": "s", "html": "b"})
        self.assertNotIn("Idempotency-Key", kw["headers"])
        with patch.object(n.requests, "post") as post, patch.object(n, "_record_email_attempt"):
            post.return_value.status_code = 200
            n.send_resend_email("s", "b", attachments=[{"filename": "a.csv", "content": "eA=="}], idempotency_key="k1")
        self.assertEqual(post.call_args.kwargs["headers"]["Idempotency-Key"], "k1")
        self.assertEqual(post.call_args.kwargs["json"]["attachments"][0]["filename"], "a.csv")

    def test_resend_rejection_returns_false(self):
        n = _load_notifications()
        n.RESEND_API_KEY, n.TEST_EMAIL = "k", "me@example.com"
        with patch.object(n.requests, "post") as post, patch.object(n, "_record_email_attempt"):
            post.return_value.status_code = 429
            post.return_value.text = "rate"
            self.assertFalse(n.send_resend_email("s", "b"))


if __name__ == "__main__":
    unittest.main()

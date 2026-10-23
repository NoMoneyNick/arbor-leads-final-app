"""
test_refund_sync.py -- Stripe refund state -> TreeKey payment record (refund_sync.py).

Covers: full vs partial vs none classification, impossible figures, the webhook handler end to end with fakes
(Stripe lookups, DB cursor, alert), idempotency (no change => no alert), retryable failures, ignored events,
and that NOTHING beyond the payment row is ever written (no letter, allocation or funding change).
Real-SQL behaviour: tests/postgres_concurrency/run_refund_and_purge_real_db_test.py.

Run: python -m unittest test_refund_sync
"""
import os
import sys
import types
import unittest
from unittest.mock import MagicMock, patch

_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(_THIS_DIR))

import refund_sync  # noqa: E402


class TestClassify(unittest.TestCase):
    def test_states(self):
        self.assertEqual(refund_sync.classify(499, 0), "none")
        self.assertEqual(refund_sync.classify(499, 1), "partial")
        self.assertEqual(refund_sync.classify(499, 498), "partial")
        self.assertEqual(refund_sync.classify(499, 499), "full")

    def test_impossible_figures(self):
        for a, r in [(499, 500), (499, -1), (0, 0), (-5, 0), (499, 1.5), (499, True), ("499", 0), (None, 0)]:
            with self.assertRaises(ValueError, msg=(a, r)):
                refund_sync.classify(a, r)

    def test_fulfilment_stages(self):
        f = refund_sync.fulfilment_stage
        self.assertEqual(f(None), "no_order")
        for st in ("pending_approval", "pending_funding", "ready", "blocked_missing_data"):
            self.assertEqual(f(st), "not_submitted")
        for st in ("submitting", "unknown"):
            self.assertEqual(f(st), "in_flight")
        for st in ("provider_accepted", "dispatched", "delivered"):
            self.assertEqual(f(st), "submitted")
        for st in ("failed", "cancelled", "suppressed", "dry_run"):
            self.assertEqual(f(st), "closed")
        self.assertEqual(f("something_new"), "unrecognised")


def _setup(charge_amount=499, refunded=499, pay_row=(1, "paid", 0), rowcount=1, order_status="dispatched",
           session_found=True):
    """Fakes for stripe, database and notifications; returns (stripe, database, notifications, cursor, conn)."""
    fake_stripe = MagicMock()
    fake_stripe.Charge.retrieve.return_value = {"id": "ch_1", "amount": charge_amount, "amount_refunded": refunded,
                                                  "currency": "gbp", "payment_intent": "pi_1"}
    fake_stripe.checkout.Session.list.return_value = {"data": [{"id": "cs_1"}] if session_found else []}
    cur = MagicMock()
    cur.fetchone.return_value = pay_row
    cur.rowcount = rowcount
    cur.fetchall.return_value = [("alloc1", "L1", "ord1" if order_status else None, order_status, False)]
    conn = MagicMock()
    conn.cursor.return_value = cur
    fake_db = types.ModuleType("database")
    fake_db.get_db_conn = MagicMock(return_value=conn)
    fake_notif = types.ModuleType("notifications")
    fake_notif.send_system_incident_alert = MagicMock()
    return fake_stripe, fake_db, fake_notif, cur, conn


def _run(event_type="charge.refunded", data=None, **kw):
    s, d, n, cur, conn = _setup(**kw)
    data = data or {"object": "charge", "id": "ch_1", "payment_intent": "pi_1"}
    with patch.dict(sys.modules, {"stripe": s, "database": d, "notifications": n}):
        out = refund_sync.handle_refund_event(event_type, data, "evt_1")
    return out, s, d, n, cur, conn


class TestHandler(unittest.TestCase):
    def test_full_refund_marks_refunded_and_alerts_once(self):
        out, s, d, n, cur, conn = _run()
        self.assertEqual(out, {"event": "refund_synced", "kind": "full", "status": "refunded"})
        n.send_system_incident_alert.assert_called_once()
        conn.commit.assert_called_once()

    def test_full_refund_of_a_submitted_letter_is_urgent_and_says_nothing_was_cancelled(self):
        out, s, d, n, cur, conn = _run(order_status="dispatched")
        kw = n.send_system_incident_alert.call_args.kwargs
        self.assertEqual(kw["severity"], "CRITICAL")
        self.assertIn("cannot be recalled", kw["impact"])
        self.assertIn("No letter was cancelled", kw["impact"])
        self.assertIn("Nothing was changed automatically", kw["action_required"])

    def test_full_refund_before_submission_is_not_urgent_and_not_cancelled(self):
        out, s, d, n, cur, conn = _run(order_status="pending_funding")
        kw = n.send_system_incident_alert.call_args.kwargs
        self.assertEqual(kw["severity"], "WARNING")
        self.assertIn("NOT been submitted", kw["impact"])

    def test_partial_refund_keeps_status_paid(self):
        out, s, d, n, cur, conn = _run(refunded=200)
        self.assertEqual(out["kind"], "partial")
        self.assertEqual(out["status"], "paid")
        self.assertEqual(n.send_system_incident_alert.call_args.kwargs["severity"], "WARNING")
        self.assertIn("PARTIAL", n.send_system_incident_alert.call_args.kwargs["title"])

    def test_already_synced_is_a_silent_no_op(self):
        out, s, d, n, cur, conn = _run(rowcount=0)
        self.assertEqual(out["event"], "refund_already_synced")
        n.send_system_incident_alert.assert_not_called()

    def test_only_the_payment_row_is_ever_written(self):
        out, s, d, n, cur, conn = _run()
        written = [c.args[0] for c in cur.execute.call_args_list if c.args[0].strip().upper().startswith(("UPDATE", "INSERT", "DELETE"))]
        self.assertEqual(len(written), 1)
        self.assertIn("UPDATE payments", written[0])
        everything = " ".join(c.args[0] for c in cur.execute.call_args_list).lower()
        for forbidden in ("letter_obligations set", "lead_allocations set", "funding_reservations", "mailing_budget", "leads set"):
            self.assertNotIn(forbidden, everything)

    def test_stripe_is_read_after_the_row_is_locked(self):
        s, d, n, cur, conn = _setup()[0:5]
        order = []
        cur.execute.side_effect = lambda sql, *a: order.append("lock" if "FOR UPDATE" in sql else ("update" if "UPDATE payments" in sql else "other"))
        orig = s.Charge.retrieve.return_value
        s.Charge.retrieve.side_effect = lambda cid: (order.append("stripe_charge"), orig)[1]
        with patch.dict(sys.modules, {"stripe": s, "database": d, "notifications": n}):
            refund_sync.handle_refund_event("charge.refunded", {"object": "charge", "id": "ch_1", "payment_intent": "pi_1"}, "e")
        self.assertLess(order.index("lock"), order.index("stripe_charge"))
        self.assertLess(order.index("stripe_charge"), order.index("update"))

    def test_refund_object_events_use_the_charge_it_points_to(self):
        out, s, d, n, cur, conn = _run("refund.updated", {"object": "refund", "id": "re_1", "charge": "ch_9", "payment_intent": "pi_1"})
        s.Charge.retrieve.assert_called_with("ch_9")
        self.assertEqual(out["event"], "refund_synced")

    def test_event_without_a_charge_is_ignored(self):
        out, *_ = _run("refund.updated", {"object": "refund", "id": "re_1"})
        self.assertEqual(out["event"], "refund_ignored")

    def test_charge_not_from_a_checkout_session_is_ignored(self):
        out, s, d, n, cur, conn = _run(session_found=False)
        self.assertEqual(out["event"], "refund_ignored")
        d.get_db_conn.assert_not_called()

    def test_unknown_payment_and_unfinished_payment_are_ignored(self):
        out, *_ = _run(pay_row=None)
        self.assertEqual(out["reason"], "no_payment_record")
        out, *_ = _run(pay_row=(1, "pending", 0))
        self.assertEqual(out["reason"], "payment_status_pending")

    def test_stripe_failure_asks_for_a_retry_and_touches_nothing(self):
        s, d, n, cur, conn = _setup()
        s.checkout.Session.list.side_effect = RuntimeError("network")
        with patch.dict(sys.modules, {"stripe": s, "database": d, "notifications": n}):
            out = refund_sync.handle_refund_event("charge.refunded", {"object": "charge", "id": "ch_1", "payment_intent": "pi_1"}, "e")
        self.assertTrue(out["retry"])
        d.get_db_conn.assert_not_called()

    def test_database_failure_rolls_back_and_asks_for_a_retry(self):
        s, d, n, cur, conn = _setup()
        cur.execute.side_effect = RuntimeError("db down")
        with patch.dict(sys.modules, {"stripe": s, "database": d, "notifications": n}):
            out = refund_sync.handle_refund_event("charge.refunded", {"object": "charge", "id": "ch_1", "payment_intent": "pi_1"}, "e")
        self.assertTrue(out["retry"])
        conn.rollback.assert_called()
        conn.commit.assert_not_called()
        n.send_system_incident_alert.assert_not_called()

    def test_alert_failure_does_not_undo_the_record(self):
        s, d, n, cur, conn = _setup()
        n.send_system_incident_alert.side_effect = RuntimeError("smtp")
        with patch.dict(sys.modules, {"stripe": s, "database": d, "notifications": n}):
            out = refund_sync.handle_refund_event("charge.refunded", {"object": "charge", "id": "ch_1", "payment_intent": "pi_1"}, "e")
        self.assertEqual(out["event"], "refund_synced")
        conn.commit.assert_called_once()


class TestSchema(unittest.TestCase):
    def test_refund_columns_are_added_idempotently(self):
        src = open(os.path.join(os.path.dirname(_THIS_DIR), "database.py")).read()
        self.assertIn("ALTER TABLE payments ADD COLUMN IF NOT EXISTS refunded_pence INT NOT NULL DEFAULT 0;", src)
        self.assertIn("ALTER TABLE payments ADD COLUMN IF NOT EXISTS refund_synced_at TIMESTAMPTZ;", src)


if __name__ == "__main__":
    unittest.main()

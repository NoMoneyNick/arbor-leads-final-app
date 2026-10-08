"""
test_accepted_dispatch_alignment.py -- the two status-processing paths and the accepted-order checker
use ONE meaning of the provider status `sent`: the item has been handed over to Royal Mail (Intelliprint's
written reply, 8 Oct 2026, as reported by Nick). Handover is not delivery.

No real database and no provider: a scripted fake cursor and a fake adapter. The real-PostgreSQL version of
the same checks is tests/postgres_concurrency/run_accepted_dispatch_check_real_db_test.py.

Run with:
    python -m unittest tests.test_accepted_dispatch_alignment -v
"""
import os
import sys
import types
import unittest
from unittest.mock import patch

_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
_APP_DIR = os.path.dirname(_THIS_DIR)
sys.path.insert(0, _APP_DIR)
sys.path.insert(0, _THIS_DIR)
import test_access_control  # noqa: E402,F401  (populates the shared stubs the app modules import)

import accepted_dispatch_check as adc  # noqa: E402
import fulfilment  # noqa: E402
import retention_dispatch_purge as purge  # noqa: E402
from letter_providers import intelliprint_provider as ip  # noqa: E402
from letter_providers.base import ProviderResult  # noqa: E402

LIVE_MSG = "Intelliprint status='waiting_to_print' (testmode=False)."


class _Cur:
    def __init__(self, rows):
        self.rows, self.executed, self._one = rows, [], None

    def execute(self, sql, params=None):
        s = " ".join(sql.split())
        self.executed.append((s, params))
        if s.startswith("SELECT status FROM letter_obligations"):
            self._one = ("provider_accepted",)
        elif "RETURNING id" in s:
            self._one = ("oid-1",)
        else:
            self._one = None

    def fetchall(self):
        return self.rows

    def fetchone(self):
        return self._one

    def close(self):
        pass


class _Conn:
    def __init__(self, cur):
        self.cur = cur

    def cursor(self):
        return self.cur

    def commit(self):
        pass

    def rollback(self):
        pass

    def close(self):
        pass


class _Adapter:
    name = "fake_test"
    test_mode = False

    def __init__(self, result):
        self.result, self.sends = result, 0

    def check_status(self, ref):
        return self.result

    def send(self, request):  # pragma: no cover - must never run
        self.sends += 1
        raise AssertionError("send() must never be called")


def _run_check(outcome, ref="REF-1"):
    cur = _Cur([("oid-1", "fake_test", ref, LIVE_MSG)])
    adapter = _Adapter(ProviderResult(outcome, "fake_test", ref, message=f"scripted {outcome}"))
    registry = types.SimpleNamespace(slots=[types.SimpleNamespace(adapter=adapter)])
    with patch.object(adc.database, "get_db_conn", create=True, return_value=_Conn(cur)):
        summary = adc.check_accepted_orders(registry=registry)
    return summary, cur, adapter


class TestOneMeaningOfSent(unittest.TestCase):
    def test_the_handover_switch_is_on(self):
        self.assertIs(adc.SENT_MEANS_POSTAL_HANDOVER_VERIFIED, True)

    def test_shared_provider_mapping_sent_is_dispatched_and_returned_is_rejected(self):
        self.assertEqual(ip._map_status("sent"), "dispatched")
        self.assertEqual(ip._map_status("shipping"), "accepted")
        for s in ("returned", "cancelled", "invalid_address"):
            self.assertEqual(ip._map_status(s), "rejected")

    def test_checker_and_manual_reconcile_treat_the_same_sent_result_the_same_way(self):
        sent = ProviderResult("dispatched", "fake_test", "REF-1")
        self.assertEqual(purge._classify_result(sent), "settle")            # manual reconcile path
        summary, cur, _ = _run_check("dispatched")                          # accepted-order checker
        self.assertEqual(summary["dispatched"], 1)
        self.assertTrue(any("SET status = 'dispatched'" in s for s, _ in cur.executed))

    def test_checker_marks_dispatched_once_without_sending_or_touching_money(self):
        summary, cur, adapter = _run_check("dispatched")
        self.assertEqual((summary["dispatched"], summary["errors"], adapter.sends), (1, 0, 0))
        writes = " ".join(s for s, _ in cur.executed if s.startswith("UPDATE"))
        for forbidden in ("funding", "attempts", "provider_reference", "provider_name", "cost"):
            self.assertNotIn(forbidden, writes)

    def test_switching_the_handover_switch_off_holds_instead_of_dispatching(self):
        with patch.object(adc, "SENT_MEANS_POSTAL_HANDOVER_VERIFIED", False):
            summary, cur, _ = _run_check("dispatched")
        self.assertEqual((summary["dispatched"], summary["dispatched_status_held"]), (0, 1))
        self.assertFalse(any("SET status = 'dispatched'" in s for s, _ in cur.executed))


class TestReturnedLetterKeepsItsDispatchRecord(unittest.TestCase):
    def test_checker_only_reads_provider_accepted_so_a_dispatched_order_is_never_revisited(self):
        _, cur, _ = _run_check("rejected")
        select_sql = cur.executed[0][0]
        self.assertIn("WHERE status = 'provider_accepted'", select_sql)
        self.assertNotIn("'dispatched'", select_sql)

    def test_every_checker_write_is_guarded_to_provider_accepted_so_it_cannot_alter_a_dispatched_row(self):
        for outcome in ("dispatched", "rejected", "accepted"):
            _, cur, _ = _run_check(outcome)
            for s, _p in cur.executed:
                if s.startswith("UPDATE"):
                    self.assertIn("status = 'provider_accepted'", s, (outcome, s))

    def test_a_returned_report_on_a_still_accepted_order_is_held_for_a_human_not_dispatched_or_released(self):
        summary, cur, _ = _run_check("rejected")
        self.assertEqual((summary["needs_review"], summary["dispatched"]), (1, 0))
        self.assertFalse(any("'dispatched'" in s for s, _ in cur.executed if s.startswith("UPDATE")))

    def test_manual_reconcile_only_selects_unknown_orders_and_never_releases_a_returned_one(self):
        import inspect
        src = inspect.getsource(purge.reconcile_unknown_outcome_obligations)
        self.assertIn("WHERE status = 'unknown'", src)
        returned = ProviderResult("rejected", "fake_test", "REF-1")      # confirmed_uncharged defaults to False
        self.assertEqual(purge._classify_result(returned), "charge_unproven")

    def test_the_purge_still_keys_off_the_dispatch_record_only(self):
        import inspect
        src = inspect.getsource(purge)
        self.assertIn("status = 'dispatched'", src)


class TestWordingAndWiring(unittest.TestCase):
    def test_customer_facing_stage_wording_says_handed_to_royal_mail(self):
        stage = fulfilment.STAGE_MAP if hasattr(fulfilment, "STAGE_MAP") else fulfilment._STAGE_MAP
        done = stage["dispatched"][2]
        self.assertIn("handed to Royal Mail", done)
        self.assertIn("no way to confirm actual delivery", done)
        self.assertNotIn("left their system", done)
        self.assertNotIn("left their system", stage["provider_accepted"][2])

    def test_main_runs_the_check_in_its_own_try_except_before_the_purge(self):
        src = open(os.path.join(_APP_DIR, "main.py"), encoding="utf-8").read()
        i = src.index("accepted_dispatch_check.check_accepted_orders()")
        j = src.index("retention_dispatch_purge.purge_dispatched_personal_data()", i)
        between = src[i:j]
        self.assertIn("except Exception as e:", between)
        self.assertIn("Accepted-letter status check error", between)


if __name__ == "__main__":
    unittest.main()

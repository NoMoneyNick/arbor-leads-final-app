#!/usr/bin/env python3
"""
tests/postgres_concurrency/run_actual_cost_real_db_test.py

Real disposable PostgreSQL check of actual-cost postage accounting
(funding.FundingGate.settle_actual, wired into letter_providers.registry.
attempt_send and retention_dispatch_purge.reconcile_unknown_outcome_obligations).
The production SQL runs unmodified on a private throwaway server started (and
deleted) by this script, with FAKE provider responses only. No network, no real
Intelliprint, no payments, no production or kit database.

Covers: cost equal to / below / above the estimate; an above-estimate charge with
insufficient budget (shortfall flagged, accepted order kept, further spending
blocked); missing and invalid cost (unresolved, reservation held, never treated
as confirmed); recovery of an 'unknown' order with a confirmed cost; repeated and
concurrent processing; rollback after injected failures; adapter cost units.

Exit codes: 0 all passed, 1 a check failed, 2 PostgreSQL unavailable.
"""
from __future__ import annotations

import os
import sys
import threading
import time
import types
from unittest.mock import MagicMock, patch

HERE = os.path.dirname(os.path.abspath(__file__))
APP = os.path.abspath(os.path.join(HERE, "..", ".."))
sys.path[:0] = [APP, HERE]

import run_concurrency_tests as rct  # noqa: E402
import run_signup_real_db_tests as base  # noqa: E402
from run_reconciliation_real_db_test import ConvConn  # noqa: E402

DB = "treekey_actual_cost_test"
EST = 108


def main() -> int:
    try:
        pg_bin = rct.find_pg_bin()
    except FileNotFoundError:
        print("PostgreSQL not available: NOT run.")
        return 2
    for n in ("psycopg2", "psycopg2.extras", "psycopg2.pool", "psycopg2.errors", "psycopg2.extensions"):
        sys.modules[n] = MagicMock()
    for _ in range(20):
        try:
            import database  # noqa: F401
            break
        except ModuleNotFoundError as e:
            sys.modules[e.name] = MagicMock()
            sys.modules.pop("database", None)

    os.environ["FUNDING_MODE"] = "hold"
    os.environ["SUPPRESSION_HASH_KEY"] = "unit-test-suppression-hash-key-not-a-real-secret"
    import fulfilment, funding, suppression, retention_dispatch_purge as purge
    import letter_providers.intelliprint_provider as ip
    from letter_providers.base import ProviderResult
    from letter_providers.fake_provider import FakeLetterProvider
    from letter_providers.registry import ProviderRegistry, ProviderSlot, attempt_send

    class CostProvider(FakeLetterProvider):
        """Fake provider: send() returns the scripted result and counts calls;
        check_status() answers from `status_script` (for recovery)."""
        def __init__(self):
            super().__init__(seed=1)
            self.result, self.sends, self.status_script = None, 0, {}

        def send(self, request):
            self.sends += 1
            return self.result

        def check_status(self, ref):
            return self.status_script.get(ref)

    cluster = rct.Cluster(pg_bin, rct.PG_OS_USER_DEFAULT)
    results, conns = [], []

    def new_conn():
        c = ConvConn(cluster); conns.append(c); return c

    def check(name, cond, detail=""):
        results.append((name, bool(cond)))
        print(f"[{'PASS' if cond else 'FAIL'}] {name}" + (f"  -- {detail}" if detail and not cond else ""))

    try:
        cluster.start()
        cluster.createdb(DB)
        base.DB = DB
        database.SURL = "scratch"
        database.get_db_conn = new_conn

        setup = new_conn(); cu = setup.cursor()
        fulfilment.init_fulfilment_schema(cu); funding.init_funding_schema(cu); suppression.init_suppression_schema(cu)
        setup.commit()
        RD = new_conn()
        gate = funding.FundingGate(mode="hold")
        counter = [0]

        def fresh_budget(amount):
            """Deactivate earlier test budgets; insert one labelled FICTIONAL budget."""
            c = new_conn(); k = c.cursor()
            k.execute("UPDATE mailing_budget_confirmations SET active = FALSE;")
            bid = gate.confirm_budget(k, amount_pence=amount, confirmed_by="LOCAL TEST HARNESS (fictional)",
                                      note="FICTIONAL LOCAL TEST BUDGET - not real money")
            c.commit(); c.close(); return bid

        def make_obligation(status="ready", ref=None, provider="fake_test", reserve=False):
            counter[0] += 1; n = counter[0]
            c = new_conn(); k = c.cursor()
            k.execute("""INSERT INTO letter_obligations
                (lead_reference, address, buyer_email, sale_context, status, is_dry_run, idempotency_key,
                 provider_name, provider_reference, attempts)
                VALUES (%s,%s,%s,%s,%s,FALSE,%s,%s,%s,0) RETURNING id;""",
                      (f"TKTEST-C{n}", "1 Fictional Close, Leeds LS1 1AA", "stripe.test.buyer@example.com",
                       "single_purchase", status, f"cost-test-{n}", provider if ref else None, ref))
            oid = str(k.fetchone()[0])
            if reserve:
                assert gate.reserve(k, oid, EST).ok
            c.commit(); c.close(); return oid

        prov = CostProvider()
        reg = ProviderRegistry([ProviderSlot(adapter=prov)])

        def send(oid, outcome="accepted", cost=None, ref=None, estimate=EST):
            prov.result = ProviderResult(outcome=outcome, provider_name="fake_test", provider_reference=ref or f"REF-{oid[:8]}",
                                         cost_pence=cost, message="Scripted (fake).")
            c = new_conn(); k = c.cursor()
            o = attempt_send(k, reg, oid, worker_id="w1", content_html="<p>fictional</p>",
                             address_lines={"line1": "1 Fictional Close", "city": "Leeds", "postcode": "LS1 1AA", "country": "GB"},
                             applicant_name="Fictional Person", lead_reference="TKTEST", idempotency_key=f"idem-{oid}",
                             is_dry_run=False, estimated_cost_pence=estimate, gate=gate)
            c.commit(); c.close(); return o

        def ob(oid):
            RD.rollback(); k = RD.cursor()
            k.execute("SELECT status, cost_status, estimated_cost_pence, provider_cost_pence, cost_shortfall_pence "
                      "FROM letter_obligations WHERE id=%s;", (oid,))
            r = k.fetchone(); RD.rollback(); return r

        def rows(oid):
            RD.rollback(); k = RD.cursor()
            k.execute("SELECT status, count(*), COALESCE(SUM(amount_pence),0) FROM funding_reservations "
                      "WHERE obligation_id=%s GROUP BY status;", (oid,))
            r = {s: (n, a) for s, n, a in k.fetchall()}; RD.rollback(); return r

        def bud(bid):
            RD.rollback(); k = RD.cursor()
            k.execute("SELECT amount_pence, spent_pence, reserved_pence FROM mailing_budget_confirmations WHERE id=%s;", (bid,))
            r = k.fetchone(); RD.rollback(); return r

        # ---- A. cost equal / below / above the estimate ------------------
        b = fresh_budget(1000); o = make_obligation()
        out = send(o, cost=108)
        check("A1 equal: accepted, cost confirmed est=108 actual=108, spent 108, nothing reserved",
              out.final_status == "accepted" and ob(o) == ("provider_accepted", "confirmed", 108, 108, 0)
              and bud(b) == (1000, 108, 0) and rows(o) == {"settled": (1, 108)}, f"{out.final_status} {ob(o)} {bud(b)} {rows(o)}")

        b = fresh_budget(1000); o = make_obligation()
        send(o, cost=90)
        check("A2 lower: actual 90 spent, unused 18 released back to available budget",
              ob(o) == ("provider_accepted", "confirmed", 108, 90, 0) and bud(b) == (1000, 90, 0)
              and rows(o) == {"settled": (1, 90), "released": (1, 18)}, f"{ob(o)} {bud(b)} {rows(o)}")
        check("A2 available budget afterwards = 1000 - 90 (the 18p is reusable)",
              gate._available_pence_from_db(new_conn().cursor()) == 910)

        b = fresh_budget(1000); o = make_obligation()
        send(o, cost=120)
        check("A3 higher with headroom: full 120 spent (108 reservation + 12 extra), confirmed, no shortfall",
              ob(o) == ("provider_accepted", "confirmed", 108, 120, 0) and bud(b) == (1000, 120, 0)
              and rows(o) == {"settled": (2, 120)}, f"{ob(o)} {bud(b)} {rows(o)}")

        # ---- B. above estimate, insufficient remaining budget -------------
        b = fresh_budget(110); o = make_obligation(); sends_before = prov.sends
        out = send(o, cost=125)
        check("B1 shortfall: order stays ACCEPTED (not failed), charge evidence kept, shortfall 15 flagged",
              out.final_status == "accepted" and ob(o) == ("provider_accepted", "shortfall", 108, 125, 15), f"{out.final_status} {ob(o)}")
        check("B1 ledger records the full 125 spent (budget row overspent by 15), nothing reserved",
              bud(b) == (110, 125, 0) and rows(o) == {"settled": (3, 125)} and gate.outstanding_shortfall_pence(new_conn().cursor()) == 15,
              f"{bud(b)} {rows(o)}")
        check("B1 not resent", prov.sends == sends_before + 1)
        o2 = make_obligation(); out2 = send(o2, cost=108)
        check("B2 further spending is blocked: a new letter cannot reserve against the unavailable funds",
              out2.final_status == "failed" and "Outstanding budget shortfall" in out2.note and rows(o2) == {} and prov.sends == sends_before + 1,
              f"{out2.final_status} {out2.note}")
        c = new_conn(); k = c.cursor(); b_big = gate.confirm_budget(k, amount_pence=1000, confirmed_by="LOCAL TEST HARNESS (fictional)", note="FICTIONAL"); c.commit()
        o3 = make_obligation(); out3 = send(o3, cost=108)
        check("B3 after more budget is confirmed (net 985 >= 108) spending resumes",
              out3.final_status == "accepted" and ob(o3)[1] == "confirmed", f"{out3.final_status} {ob(o3)}")

        # ---- C. missing / invalid cost stays unresolved ------------------
        b = fresh_budget(1000)
        for label, cost, expect in (("missing (None)", None, "unresolved_missing"), ("zero", 0, "unresolved_invalid"),
                                    ("negative", -5, "unresolved_invalid"), ("float", 108.0, "unresolved_invalid"),
                                    ("bool", True, "unresolved_invalid")):
            o = make_obligation(); out = send(o, cost=cost)
            check(f"C {label}: order accepted, cost_status {expect}, reservation HELD at estimate, not treated as spent",
                  out.final_status == "accepted" and ob(o)[:2] == ("provider_accepted", expect) and ob(o)[3] is None
                  and rows(o) == {"reserved": (1, EST)}, f"{ob(o)} {rows(o)}")
        check("C budget: only reserved (5 x 108), nothing spent", bud(b) == (1000, 0, 5 * EST), str(bud(b)))
        # later, a confirmed cost can resolve one of them, once
        o = make_obligation(); send(o, cost=None)
        c = new_conn(); k = c.cursor(); s1 = gate.settle_actual(k, o, 100); c.commit()
        c = new_conn(); k = c.cursor(); s2 = gate.settle_actual(k, o, 100); c.commit()
        check("C later confirmed cost resolves it once (100 spent, 8 released); a repeat changes nothing",
              s1.status == "confirmed" and s2.status == "already_recorded" and ob(o) == ("provider_accepted", "confirmed", 108, 100, 0)
              and rows(o) == {"settled": (1, 100), "released": (1, 8)}, f"{s1.status} {s2.status} {ob(o)} {rows(o)}")

        # ---- D. repeated send ---------------------------------------------
        b = fresh_budget(1000); o = make_obligation(); send(o, cost=108); snap = (ob(o), bud(b), rows(o), prov.sends)
        again = send(o, cost=108)
        check("D repeated send of an accepted order: skipped, provider not called, ledger unchanged",
              again.final_status == "skipped" and (ob(o), bud(b), rows(o), prov.sends) == snap,
              f"{again.final_status}")

        # ---- E. injected failures in the send path ------------------------
        b = fresh_budget(1000); o = make_obligation(); sends_before = prov.sends
        with patch.object(funding.FundingGate, "settle_actual", side_effect=RuntimeError("injected accounting failure")):
            out = send(o, cost=108)
        check("E1 accounting fails after acceptance: order still accepted (NOT failed), flagged unresolved_accounting_error, reservation held, not resent",
              out.final_status == "accepted" and ob(o)[:2] == ("provider_accepted", "unresolved_accounting_error")
              and rows(o) == {"reserved": (1, EST)} and bud(b) == (1000, 0, EST) and prov.sends == sends_before + 1, f"{ob(o)} {rows(o)} {bud(b)}")
        c = new_conn(); k = c.cursor(); gate.settle_actual(k, o, 108); c.commit()
        check("E1 a later settle_actual resolves it", ob(o) == ("provider_accepted", "confirmed", 108, 108, 0) and bud(b) == (1000, 108, 0), str(ob(o)))

        b = fresh_budget(1000); o = make_obligation()
        real_record = funding.FundingGate._record_cost

        def failing_record(self, cur, obligation_id, **kw):
            if kw.get("status") in ("confirmed", "shortfall"):
                raise RuntimeError("injected failure after the ledger was updated")
            return real_record(self, cur, obligation_id, **kw)
        with patch.object(funding.FundingGate, "_record_cost", failing_record):
            out = send(o, cost=90)
        check("E2 failure AFTER the ledger writes: savepoint rolls the partial ledger back (still reserved 108, nothing spent/released), order accepted + flagged",
              out.final_status == "accepted" and ob(o)[:2] == ("provider_accepted", "unresolved_accounting_error")
              and rows(o) == {"reserved": (1, EST)} and bud(b) == (1000, 0, EST), f"{ob(o)} {rows(o)} {bud(b)}")

        # ---- F. concurrency: two processors settle the same order --------
        b = fresh_budget(1000); o = make_obligation(status="provider_accepted", ref="REF-CONC", reserve=True)
        out_ = {}

        def worker(tag, delay, hold):
            time.sleep(delay)
            c = new_conn(); k = c.cursor()
            out_[tag] = gate.settle_actual(k, o, 100)
            time.sleep(hold)
            c.commit()
        t1 = threading.Thread(target=worker, args=("a", 0, 1.2)); t2 = threading.Thread(target=worker, args=("b", 0.4, 0))
        t1.start(); t2.start(); t1.join(); t2.join()
        statuses = sorted([out_["a"].status, out_["b"].status])
        check("F two overlapping settles of one order: exactly one applies, the other is a no-op",
              statuses == ["already_recorded", "confirmed"] and ob(o) == ("provider_accepted", "confirmed", 108, 100, 0)
              and bud(b) == (1000, 100, 0) and rows(o) == {"settled": (1, 100), "released": (1, 8)}, f"{statuses} {ob(o)} {bud(b)} {rows(o)}")

        # ---- G. recovery of an UNKNOWN order with a confirmed cost --------
        scripted = CostProvider()
        scripted.status_script = {}
        rreg = types.SimpleNamespace(slots=[types.SimpleNamespace(adapter=scripted)])

        def reconcile():
            with patch("letter_providers.registry.build_registry_from_env", return_value=rreg):
                return purge.reconcile_unknown_outcome_obligations()

        b = fresh_budget(1000)
        og = make_obligation(status="unknown", ref="REF-REC-LOW", reserve=True)
        scripted.status_script["REF-REC-LOW"] = ProviderResult("accepted", "fake_test", "REF-REC-LOW", cost_pence=95, message="m")
        r = reconcile()
        check("G1 recovered unknown with confirmed cost 95: accepted, actual 95 spent, 13 released",
              ob(og) == ("provider_accepted", "confirmed", 108, 95, 0) and bud(b) == (1000, 95, 0) and r["resolved"] == 1, f"{ob(og)} {bud(b)} {r}")
        snap = (ob(og), bud(b), rows(og)); reconcile(); reconcile()
        check("G1 repeat recovery x2: nothing adjusted twice", (ob(og), bud(b), rows(og)) == snap)

        b = fresh_budget(1000)
        oh = make_obligation(status="unknown", ref="REF-REC-HIGH", reserve=True)
        scripted.status_script["REF-REC-HIGH"] = ProviderResult("dispatched", "fake_test", "REF-REC-HIGH", cost_pence=130, message="m")
        reconcile()
        check("G2 recovered with HIGHER cost 130 and headroom: confirmed 130, no shortfall",
              ob(oh) == ("dispatched", "confirmed", 108, 130, 0) and bud(b) == (1000, 130, 0), f"{ob(oh)} {bud(b)}")

        b = fresh_budget(110)
        os_ = make_obligation(status="unknown", ref="REF-REC-SHORT", reserve=True)
        scripted.status_script["REF-REC-SHORT"] = ProviderResult("accepted", "fake_test", "REF-REC-SHORT", cost_pence=125, message="m")
        reconcile()
        check("G3 recovered with cost above remaining budget: accepted kept, shortfall 15 flagged",
              ob(os_) == ("provider_accepted", "shortfall", 108, 125, 15) and bud(b) == (110, 125, 0), f"{ob(os_)} {bud(b)}")

        b = fresh_budget(1000)
        on = make_obligation(status="unknown", ref="REF-REC-NOCOST", reserve=True)
        scripted.status_script["REF-REC-NOCOST"] = ProviderResult("accepted", "fake_test", "REF-REC-NOCOST", cost_pence=None, message="m")
        reconcile()
        check("G4 recovered but provider gave no cost: accepted, cost UNRESOLVED, reservation held (not treated as spent)",
              ob(on)[:2] == ("provider_accepted", "unresolved_missing") and rows(on) == {"reserved": (1, EST)} and bud(b) == (1000, 0, EST), f"{ob(on)} {rows(on)}")

        b = fresh_budget(1000)
        ou = make_obligation(status="unknown", ref="REF-REC-RET", reserve=True)
        scripted.status_script["REF-REC-RET"] = ProviderResult("rejected", "fake_test", "REF-REC-RET", cost_pence=108, message="returned")
        ox = make_obligation(status="unknown", ref="REF-REC-UNK", reserve=True)
        scripted.status_script["REF-REC-UNK"] = ProviderResult("unknown", "fake_test", "REF-REC-UNK", cost_pence=108, message="?")
        reconcile()
        check("G5 safeguards kept: rejection without proof of no charge, and unresolved status, stay unknown with reservations held",
              ob(ou)[0] == "unknown" and ob(ox)[0] == "unknown" and rows(ou) == {"reserved": (1, EST)} and rows(ox) == {"reserved": (1, EST)}
              and bud(b) == (1000, 0, 2 * EST), f"{ob(ou)} {ob(ox)} {bud(b)}")

        # rollback in recovery after an injected failure
        b = fresh_budget(1000)
        orb = make_obligation(status="unknown", ref="REF-REC-FAIL", reserve=True)
        scripted.status_script["REF-REC-FAIL"] = ProviderResult("accepted", "fake_test", "REF-REC-FAIL", cost_pence=90, message="m")
        with patch.object(funding.FundingGate, "_record_cost", failing_record):
            r = reconcile()
        check("G6 injected failure mid-accounting during recovery: whole order rolled back (still unknown, reservation intact, ledger untouched)",
              ob(orb)[0] == "unknown" and ob(orb)[1] is None and rows(orb) == {"reserved": (1, EST)} and bud(b) == (1000, 0, EST) and r["errors"] == 1,
              f"{ob(orb)} {rows(orb)} {bud(b)} {r}")
        reconcile()
        check("G6 retry after the fault settles correctly, once", ob(orb) == ("provider_accepted", "confirmed", 108, 90, 0) and bud(b) == (1000, 90, 0), str(ob(orb)))

        # ---- H. adapter money units / rounding ---------------------------
        cp = ip._cost_pence
        check("H1 108,000,000 units (the real test job's after_tax) = 108p", cp({"cost": {"after_tax": 108_000_000}}) == 108)
        check("H2 fractional pence rounds UP (never under-records): 107,000,001 -> 108; 107,500,000 -> 108; float noise 108000000.4 -> 108",
              cp({"cost": {"after_tax": 107_000_001}}) == 108 and cp({"cost": {"after_tax": 107_500_000}}) == 108
              and cp({"cost": {"after_tax": 108000000.4}}) == 108)
        bad = [None, "108000000", True, 0, -5, float("nan"), float("inf"), [], {}]
        check("H3 invalid after_tax (null, string, bool, zero, negative, NaN, inf, list, dict) -> None",
              all(cp({"cost": {"after_tax": v}}) is None for v in bad))
        check("H4 missing/odd cost object -> None", cp({}) is None and cp({"cost": None}) is None and cp({"cost": 5}) is None and cp({"cost": {}}) is None)
        check("H5 uses the VAT-inclusive field, not ex-VAT amount: amount 90p / after_tax 108p -> 108",
              cp({"cost": {"amount": 90_000_000, "tax": 18_000_000, "after_tax": 108_000_000}}) == 108)

        # ---- invariants across everything --------------------------------
        RD.rollback(); k = RD.cursor()
        k.execute("""SELECT m.id, m.reserved_pence,
                            COALESCE((SELECT SUM(amount_pence) FROM funding_reservations r WHERE r.budget_confirmation_id=m.id AND r.status='reserved'),0),
                            m.spent_pence,
                            COALESCE((SELECT SUM(amount_pence) FROM funding_reservations r WHERE r.budget_confirmation_id=m.id AND r.status='settled'),0)
                     FROM mailing_budget_confirmations m;""")
        led = k.fetchall(); RD.rollback()
        check("Ledger consistent for every budget row: reserved_pence == open reservations and spent_pence == settled rows",
              all(r[1] == r[2] and r[3] == r[4] for r in led), str([r for r in led if not (r[1] == r[2] and r[3] == r[4])]))
        k = RD.cursor(); k.execute("SELECT count(*) FROM letter_obligations WHERE status IN ('ready','failed') AND provider_cost_pence IS NOT NULL;")
        n = k.fetchone()[0]; RD.rollback()
        check("No accepted-with-cost order was ever marked failed or returned to ready", n == 0)
    finally:
        for c in conns:
            try:
                c.close()
            except Exception:
                pass
        cluster.cleanup()

    failed = [n for n, ok in results if not ok]
    print(f"\n{len(results) - len(failed)}/{len(results)} checks passed.")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())

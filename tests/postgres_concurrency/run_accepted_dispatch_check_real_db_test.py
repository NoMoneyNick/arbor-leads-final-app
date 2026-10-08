#!/usr/bin/env python3
"""
tests/postgres_concurrency/run_accepted_dispatch_check_real_db_test.py

Focused real-PostgreSQL check of accepted_dispatch_check.check_accepted_orders() (3 Oct 2026): letters
the provider has ACCEPTED are re-checked by their saved provider reference; a 'sent' report moves an order to
'dispatched' (status `sent` = handed to Royal Mail, per Intelliprint's written reply of 8 Oct 2026). The
handover switch (SENT_MEANS_POSTAL_HANDOVER_VERIFIED) ships ON; sections A/B prove the switch-off and switch-on paths,
and K proves a later `returned` report never touches an order that is already dispatched.
Fake provider objects only (scripted check_status; send() raises if ever called). A private throwaway server
is the only database touched. No network, no real provider, no Intelliprint key.

Exit codes: 0 all passed, 1 a check failed, 2 PostgreSQL unavailable.
"""
from __future__ import annotations

import os
import sys
import types
from unittest.mock import MagicMock, patch

HERE = os.path.dirname(os.path.abspath(__file__))
APP = os.path.abspath(os.path.join(HERE, "..", ".."))
sys.path[:0] = [APP, HERE]

import run_concurrency_tests as rct  # noqa: E402
import run_signup_real_db_tests as base  # noqa: E402
from run_reconciliation_real_db_test import ConvConn  # noqa: E402

DB = base.DB = "treekey_accepted_check_test"
EST = 108
LIVE_MSG = "Intelliprint status='waiting_to_print' (testmode=False)."
TEST_MSG = "Intelliprint status='waiting_to_print' (testmode=True)."


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
    import fulfilment, funding
    import accepted_dispatch_check as adc
    import retention_dispatch_purge as purge
    from letter_providers.fake_provider import FakeLetterProvider
    from letter_providers.base import ProviderResult

    class Scripted(FakeLetterProvider):
        def __init__(self, test_mode=False):
            super().__init__(seed=1)
            self.script, self.sends, self.lookups, self.test_mode = {}, 0, [], test_mode

        def send(self, request):
            self.sends += 1
            raise AssertionError("the accepted-order check must never call send()")

        def check_status(self, ref):
            self.lookups.append(ref)
            v = self.script.get(ref)
            if isinstance(v, Exception):
                raise v
            return v

    cluster = rct.Cluster(pg_bin, rct.PG_OS_USER_DEFAULT)
    results, conns = [], []

    def new_conn():
        c = ConvConn(cluster)
        conns.append(c)
        return c

    def check(name, cond, detail=""):
        results.append((name, bool(cond), detail))
        print(f"[{'PASS' if cond else 'FAIL'}] {name}" + (f"  -- {detail}" if detail and not cond else ""))

    try:
        cluster.start()
        cluster.createdb(DB)
        database.SURL = "scratch"
        database.get_db_conn = new_conn
        base.DB = DB

        setup = new_conn(); cu = setup.cursor()
        fulfilment.init_fulfilment_schema(cu)
        funding.init_funding_schema(cu)
        cu.execute("CREATE TABLE IF NOT EXISTS letter_dispatches (id SERIAL PRIMARY KEY, lead_reference TEXT, sent_at TIMESTAMPTZ, purged_at TIMESTAMPTZ);")
        cu.execute("INSERT INTO mailing_budget_confirmations (amount_pence, confirmed_by, note) VALUES (%s,%s,%s) RETURNING id;",
                   (20000, "LOCAL TEST HARNESS (fictional)", "FICTIONAL LOCAL TEST BUDGET - not real money"))
        budget_id = cu.fetchone()[0]
        setup.commit()
        gate = funding.FundingGate(mode="hold")
        RD = new_conn()

        live = Scripted(test_mode=False)
        registry = types.SimpleNamespace(slots=[types.SimpleNamespace(adapter=live)])
        counter = [0]

        def make(ref, *, last_error=LIVE_MSG, provider_name="fake_test", dry=False, status="provider_accepted",
                 aged=True, with_ref=True, accepted_at=True):
            """A paid, accepted order: reservation settled once at acceptance (the real production path)."""
            counter[0] += 1
            n = counter[0]
            c = new_conn(); k = c.cursor()
            k.execute("""INSERT INTO letter_obligations
                (lead_reference, address, applicant_name, buyer_email, sale_context, status, is_dry_run, idempotency_key,
                 provider_name, provider_reference, attempts, last_error, approved_content_html,
                 provider_accepted_at, updated_at)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,1,%s,%s,
                        CASE WHEN %s THEN NOW() - INTERVAL '3 hours' END,
                        CASE WHEN %s THEN NOW() - INTERVAL '3 hours' ELSE NOW() END) RETURNING id;""",
                      (f"TKTEST-A{n}", "1 Fictional Close, Leeds LS1 1AA", "Fictional Person", "stripe.test.buyer@example.com",
                       "single_purchase", status, bool(dry), f"accepted-test-{n}", provider_name, ref if with_ref else None,
                       last_error, "<p>frozen letter</p>", bool(accepted_at), bool(aged)))
            oid = str(k.fetchone()[0])
            d = gate.reserve(k, oid, EST); assert d.ok, d.reason
            gate.settle_actual(k, oid, EST)
            if aged:   # settling touches updated_at; put the 'last checked' time back
                k.execute("UPDATE letter_obligations SET updated_at = NOW() - INTERVAL '3 hours' WHERE id = %s;", (oid,))
            c.commit(); c.close()
            return oid

        def snap(oid):
            k = RD.cursor()
            k.execute("""SELECT status, attempts, provider_name, provider_reference, provider_accepted_at IS NOT NULL,
                                dispatched_at IS NOT NULL, estimated_cost_pence, provider_cost_pence, cost_status,
                                COALESCE(last_error, ''), approved_content_html, address FROM letter_obligations WHERE id=%s;""", (oid,))
            r = k.fetchone(); RD.rollback(); return r

        def money():
            k = RD.cursor()
            k.execute("SELECT status, amount_pence, resolved_at IS NOT NULL FROM funding_reservations ORDER BY id;")
            r1 = k.fetchall()
            k.execute("SELECT amount_pence, spent_pence, reserved_pence FROM mailing_budget_confirmations WHERE id=%s;", (budget_id,))
            r2 = k.fetchone(); RD.rollback(); return (tuple(r1), r2)

        def run(reg=None):
            with patch("letter_providers.registry.build_registry_from_env", return_value=reg or registry):
                return adc.check_accepted_orders(registry=reg or registry)

        def result(ref, outcome, **kw):
            return ProviderResult(outcome, "fake_test", ref, cost_pence=EST, message=f"scripted {outcome}", **kw)

        # ---- A. handover switch ON by default; switched OFF it only HOLDS a 'sent' report ----
        check("A0 the handover switch ships ON (sent = handed to Royal Mail, confirmed in writing by the provider)", adc.SENT_MEANS_POSTAL_HANDOVER_VERIFIED is True)
        with patch.object(adc, "SENT_MEANS_POSTAL_HANDOVER_VERIFIED", False):
            o = make("REF-SENT-A")
            live.script["REF-SENT-A"] = result("REF-SENT-A", "dispatched")
            before, m0 = snap(o), money()
            r = run()
            after = snap(o)
            check("A1 switch off: provider says sent -> status stays provider_accepted, no dispatched_at, counted as held",
                  after[0] == "provider_accepted" and after[5] in (False, "f") and r["dispatched_status_held"] == 1 and r["dispatched"] == 0, str((after, r)))
            check("A2 switch off: a review note says handover is not verified; original text kept",
                  "not treated as verified" in after[9] and after[9].startswith(LIVE_MSG), after[9])
            check("A3 switch off: attempts, provider, reference, costs, content and ALL funding records unchanged",
                  after[1:5] == before[1:5] and after[6:9] == before[6:9] and after[10:] == before[10:] and money() == m0, str(money()))


        # ---- B. the shipped default (switch ON) -----------------------------
        with patch.object(adc, "SENT_MEANS_POSTAL_HANDOVER_VERIFIED", True):
            o = make("REF-SENT-B")
            live.script["REF-SENT-B"] = result("REF-SENT-B", "dispatched")
            before, m0 = snap(o), money()
            sends0 = live.sends
            r = run()
            after = snap(o)
            check("B1 switch on: sent -> dispatched, dispatched_at set, counted once", after[0] == "dispatched" and after[5] and r["dispatched"] == 1, str((after, r)))
            check("B2 attempts, provider name, provider reference, accepted time, costs and frozen content all PRESERVED",
                  after[1:5] == before[1:5] and after[6:9] == before[6:9] and after[10:] == before[10:], str((before, after)))
            check("B3 funding untouched: reservation still settled once, budget spent/reserved unchanged",
                  money() == m0 and money()[0].count(("settled", EST, "t")) >= 1, str(money()))
            check("B4 the note says dispatched_at is the time TreeKey OBSERVED the status, not the provider's time",
                  "time TreeKey observed" in after[9] and "Royal Mail" in after[9] and after[9].startswith(LIVE_MSG), after[9])
            check("B5 send() never called", live.sends == sends0 == 0)
            # repeat / overlapping
            st1, m1 = snap(o), money()
            run(); run()
            check("B6 repeat x2: nothing more changes (dispatched order is not selected; funding identical)", snap(o) == st1 and money() == m1)
            # force an overlapping writer: the guarded transition is a no-op on an already-dispatched row
            c = new_conn(); k = c.cursor()
            again = fulfilment.mark_dispatch_observed(k, o, "second writer"); c.rollback(); c.close()
            check("B7 the guarded transition refuses a row that is not provider_accepted", again is False and snap(o) == st1)

            # ---- test-mode / not-live exclusion --------------------------
            ot = make("REF-TESTMODE", last_error=TEST_MSG)
            live.script["REF-TESTMODE"] = result("REF-TESTMODE", "dispatched")
            on = make("REF-NOMARK", last_error="no mode recorded")
            live.script["REF-NOMARK"] = result("REF-NOMARK", "dispatched")
            ob = make("REF-BOTHMARK", last_error=f"{TEST_MSG} {LIVE_MSG}")
            live.script["REF-BOTHMARK"] = result("REF-BOTHMARK", "dispatched")
            od = make("REF-DRY", dry=True)
            live.script["REF-DRY"] = result("REF-DRY", "dispatched")
            lk0 = len(live.lookups)
            r = run()
            check("C1 a test-mode submission is NEVER eligible (even though it is not dry-run)", snap(ot)[0] == "provider_accepted" and "REF-TESTMODE" not in live.lookups[lk0:])
            check("C2 no recorded mode, or conflicting modes, is NEVER eligible", snap(on)[0] == "provider_accepted" and snap(ob)[0] == "provider_accepted"
                  and "REF-NOMARK" not in live.lookups[lk0:] and "REF-BOTHMARK" not in live.lookups[lk0:])
            check("C3 a dry-run order is never selected", snap(od)[0] == "provider_accepted" and "REF-DRY" not in live.lookups[lk0:])
            check("C4 the exclusions were counted as not live", r["skipped_not_live"] >= 3, str(r))
            test_adapter = Scripted(test_mode=True)
            test_adapter.script["REF-LIVEROW-TESTADAPTER"] = result("REF-LIVEROW-TESTADAPTER", "dispatched")
            ol = make("REF-LIVEROW-TESTADAPTER")
            r = run(types.SimpleNamespace(slots=[types.SimpleNamespace(adapter=test_adapter)]))
            check("C5 a live-recorded order is not checked by an adapter running in TEST mode",
                  snap(ol)[0] == "provider_accepted" and not test_adapter.lookups and r["skipped_not_live"] >= 1)

            # ---- statuses that must not move the order -------------------
            cases = {}
            for label, outcome in [("accepted", "accepted"), ("unknown", "unknown")]:
                ref = f"REF-{label.upper()}"
                cases[label] = (make(ref), ref)
                live.script[ref] = result(ref, outcome)
            cases["noanswer"] = (make("REF-NONE"), "REF-NONE")        # script returns None
            cases["raises"] = (make("REF-RAISE"), "REF-RAISE")
            live.script["REF-RAISE"] = RuntimeError("provider down")
            cases["mismatch"] = (make("REF-MISMATCH"), "REF-MISMATCH")
            live.script["REF-MISMATCH"] = result("SOMEONE-ELSES-ID", "dispatched")
            m0 = money()
            snaps = {k: snap(v[0]) for k, v in cases.items()}
            r = run()
            for k, (oid, ref) in cases.items():
                a = snap(oid)
                check(f"D1 {k}: status, attempts, reference, dispatched_at all unchanged (looked at again later)",
                      a[0] == "provider_accepted" and a[1:6] == snaps[k][1:6], str(a))
            check("D2 none of those moved any money", money() == m0)
            check("D3 the provider's other-reference answer was counted and ignored", r["reference_mismatch"] == 1, str(r))
            check("D4 a lookup that raised did not stop the pass or change anything", r["errors"] == 0)

            # ---- rejection-type statuses: hold, never release ------------
            orj = make("REF-RETURNED")
            live.script["REF-RETURNED"] = result("REF-RETURNED", "rejected")
            m0 = money(); b = snap(orj)
            r = run()
            a = snap(orj)
            check("E1 returned/cancelled/invalid-address: status kept, held for review, counted", a[0] == "provider_accepted" and r["needs_review"] == 1 and "HELD" in a[9], str((a, r)))
            check("E2 ...and NO money moved (no release, no second settle)", money() == m0 and a[1:5] == b[1:5])

            # ---- throttle ------------------------------------------------
            lk = len(live.lookups)
            run()
            check("F1 an order checked less than an hour ago is not looked up again", len(live.lookups) == lk, str(live.lookups[lk:]))
            c = new_conn(); k = c.cursor()
            k.execute("UPDATE letter_obligations SET updated_at = NOW() - INTERVAL '2 hours' WHERE provider_reference = 'REF-ACCEPTED';")
            c.commit(); c.close()
            run()
            check("F2 ...but is looked up again after the hour has passed", "REF-ACCEPTED" in live.lookups[lk:])

            # ---- other order kinds are ignored ---------------------------
            ig = [make("REF-IGN-NOREF", with_ref=False), make("REF-IGN-UNKNOWNST", status="unknown"),
                  make("REF-IGN-NOPROVIDER", provider_name="not_configured")]
            lk = len(live.lookups)
            run()
            check("G1 no reference / status 'unknown' / no matching adapter: never looked up, never changed",
                  "REF-IGN-NOREF" not in live.lookups[lk:] and "REF-IGN-UNKNOWNST" not in live.lookups[lk:]
                  and "REF-IGN-NOPROVIDER" not in live.lookups[lk:] and [snap(x)[0] for x in ig] == ["provider_accepted", "unknown", "provider_accepted"])

            # ---- end to end with the existing retention policy -----------
            c = new_conn(); k = c.cursor()
            k.execute("CREATE TABLE IF NOT EXISTS leads (id UUID PRIMARY KEY DEFAULT gen_random_uuid(), reference TEXT UNIQUE, address TEXT, applicant_name TEXT, summary TEXT);")
            purge.init_dispatch_purge_schema(k); c.commit(); c.close()
            oe = make("REF-E2E")
            live.script["REF-E2E"] = result("REF-E2E", "dispatched")
            run()
            e0 = purge.count_dispatch_purge_eligible()["obligations"]
            c = new_conn(); k = c.cursor()
            k.execute("UPDATE letter_obligations SET dispatched_at = NOW() - INTERVAL '73 hours' WHERE id = %s;", (oe,))
            c.commit(); c.close()
            e1 = purge.count_dispatch_purge_eligible()["obligations"]
            c = new_conn(); k = c.cursor()
            k.execute("SELECT count(*) FROM letter_obligations WHERE status='provider_accepted' AND dispatched_at IS NULL;")
            still_accepted = k.fetchone()[0]; c.rollback(); c.close()
            check("H1 the existing purge rule counts a dispatched order only once 72 hours have passed since dispatched_at",
                  e0 == 0 and e1 == 1, f"{e0} -> {e1}")
            check("H2 accepted orders that were not moved are never purge-eligible (purge only reads status 'dispatched')",
                  still_accepted >= 1 and e1 == 1)

        # ---- K. a letter that later becomes 'returned' keeps its dispatch record -------------
        with patch.object(adc, "SENT_MEANS_POSTAL_HANDOVER_VERIFIED", True):
            ok = make("REF-LATER-RETURNED")
            live.script["REF-LATER-RETURNED"] = result("REF-LATER-RETURNED", "dispatched")
            run()
            d0, m0 = snap(ok), money()
            live.script["REF-LATER-RETURNED"] = result("REF-LATER-RETURNED", "rejected")
            lk = len(live.lookups)
            c = new_conn(); k = c.cursor()
            k.execute("UPDATE letter_obligations SET updated_at = NOW() - INTERVAL '5 hours' WHERE id = %s;", (ok,))
            c.commit(); c.close()
            r = run()
            d1 = snap(ok)
            check("K1 an already-dispatched order is never looked up again by the check", "REF-LATER-RETURNED" not in live.lookups[lk:], str(live.lookups[lk:]))
            check("K2 its dispatch record is PRESERVED: still dispatched, dispatched_at kept, nothing else changed",
                  d0[0] == "dispatched" and d1[0] == "dispatched" and d1[5] and d1[:9] == d0[:9] and d1[10:] == d0[10:], str((d0, d1)))
            check("K3 no money moved and nothing was counted for review", money() == m0 and r["needs_review"] == 0, str(r))

        # ---- I. the purge stays isolated from this check -----------------
        src = open(os.path.join(APP, "main.py"), encoding="utf-8").read()
        i_check = src.index("accepted_dispatch_check.check_accepted_orders()")
        i_purge = src.index("retention_dispatch_purge.purge_dispatched_personal_data()", i_check)
        between = src[i_check:i_purge]
        check("I1 main.py runs the check in its OWN try/except before the purge (a failure cannot reach the purge)",
              "except Exception as e:" in between and "Accepted-letter status check error" in between)
        broken = types.SimpleNamespace(slots=[types.SimpleNamespace(adapter=None)])
        boom = Scripted(); boom.check_status = lambda ref: (_ for _ in ()).throw(KeyboardInterrupt) if False else None
        with patch.object(database, "get_db_conn", side_effect=RuntimeError("db down")):
            r = adc.check_accepted_orders()
        check("I2 even a database failure returns a summary and never raises", r["errors"] == 1)
        check("I3 the module never imports the funding gate (cannot settle or release)", "funding" not in open(os.path.join(APP, "accepted_dispatch_check.py")).read().replace("funding record", "").replace("every funding", ""))
        from letter_providers import intelliprint_provider as ip
        check("J1 provider mapping pinned: 'sent' -> dispatched, 'shipping' -> accepted, 'draft' unchanged (separate item)",
              ip._map_status("sent") == "dispatched" and ip._map_status("shipping") == "accepted" and ip._map_status("draft") == "accepted")
    finally:
        for c in conns:
            try:
                c.close()
            except Exception:  # noqa: BLE001
                pass
        cluster.cleanup()

    failed = [n for n, ok, _ in results if not ok]
    print(f"\n{len(results) - len(failed)}/{len(results)} checks passed.")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())

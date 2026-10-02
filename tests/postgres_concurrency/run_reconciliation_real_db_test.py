#!/usr/bin/env python3
"""
tests/postgres_concurrency/run_reconciliation_real_db_test.py

Real disposable PostgreSQL check of retention_dispatch_purge.
reconcile_unknown_outcome_obligations() (fix A, 2 Oct 2026): orders stuck at
'unknown' that already HAVE a provider_reference, resolved via a scripted
FAKE provider's check_status() only. The production SQL runs unmodified
(real fulfilment.mark_provider_result, real funding.FundingGate reserve/
settle/release). A private server started (and deleted) by this script is the
only database touched. No real provider, no network, no email, no Stripe.

Section N (no-reference recovery, added 2 Oct 2026) drives the REAL
IntelliprintProvider.find_by_reference() against a MOCKED Intelliprint list
API (a small in-process fake that implements the documented list semantics:
exact `reference` filter, `testmode` filter (default live only), limit/skip,
has_more, total_available) plus the real database. No network.

Out of scope here (not tested): actual provider cost settlement (spend is the
reserved estimate), a real Intelliprint response (see the checklist for what
a real lookup did or did not show).

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

DB = base.DB = "treekey_reconcile_test"
EST = 108


class ConvCursor(base.PsqlCursor):
    """psql returns text; psycopg2 returns ints. Convert whole-number text to
    int on fetch so the REAL funding.py arithmetic runs unmodified."""
    @staticmethod
    def _c(row):
        return tuple(int(v) if isinstance(v, str) and v.lstrip("-").isdigit() else v for v in row)

    def fetchone(self):
        r = super().fetchone()
        return None if r is None else self._c(r)

    def fetchall(self):
        return [self._c(r) for r in super().fetchall()]


class ConvConn(base.PsqlConn):
    def cursor(self):
        return ConvCursor(self)


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
    import fulfilment, funding, retention_dispatch_purge as purge
    from letter_providers.fake_provider import FakeLetterProvider
    from letter_providers.base import ProviderResult

    class Scripted(FakeLetterProvider):
        """Fake provider whose check_status answers are scripted per reference.
        send() must never be called by reconciliation."""
        def __init__(self):
            super().__init__(seed=1)
            self.script, self.sends, self.lookups, self.on_lookup = {}, 0, [], {}

        def send(self, request):
            self.sends += 1
            raise AssertionError("reconciliation must never call send()")

        def check_status(self, ref):
            self.lookups.append(ref)
            if ref in self.on_lookup:
                self.on_lookup[ref]()
            return self.script.get(ref)

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
        setup.commit()

        gate = funding.FundingGate(mode="hold")
        RD = new_conn()   # one shared read-only connection for the check helpers
        cu.execute("INSERT INTO mailing_budget_confirmations (amount_pence, confirmed_by, note) "
                   "VALUES (%s, %s, %s) RETURNING id;",
                   (20000, "LOCAL TEST HARNESS (fictional)", "FICTIONAL LOCAL TEST BUDGET - not real money"))
        budget_id = cu.fetchone()[0]
        setup.commit()

        adapter = Scripted()
        registry = types.SimpleNamespace(slots=[types.SimpleNamespace(adapter=adapter)])

        counter = [0]

        def make_obligation(ref, reserve=True, status="unknown", provider_name="fake_test",
                            last_error=None, idem=None):
            counter[0] += 1
            n = counter[0]
            c = new_conn(); k = c.cursor()
            k.execute("""INSERT INTO letter_obligations
                (lead_reference, address, buyer_email, sale_context, status, is_dry_run, idempotency_key,
                 provider_name, provider_reference, attempts, last_error)
                VALUES (%s,%s,%s,%s,%s,FALSE,%s,%s,%s,1,%s) RETURNING id;""",
                      (f"TKTEST-R{n}", "1 Fictional Close, Leeds LS1 1AA", "stripe.test.buyer@example.com",
                       "single_purchase", status, idem or f"reconcile-test-{n}", provider_name, ref, last_error))
            oid = str(k.fetchone()[0])
            if reserve:
                d = gate.reserve(k, oid, EST)
                assert d.ok, d.reason
            c.commit(); c.close()
            return oid

        def row(oid):
            c = RD; k = c.cursor()
            k.execute("SELECT status, attempts, provider_accepted_at IS NOT NULL, dispatched_at IS NOT NULL, "
                      "failed_at IS NOT NULL FROM letter_obligations WHERE id=%s;", (oid,))
            r = k.fetchone(); c.rollback(); return r

        def res(oid):
            c = RD; k = c.cursor()
            k.execute("SELECT status, count(*), COALESCE(SUM(amount_pence),0) FROM funding_reservations "
                      "WHERE obligation_id=%s GROUP BY status;", (oid,))
            r = {s: (n, a) for s, n, a in k.fetchall()}; c.rollback(); return r

        def budget():
            c = RD; k = c.cursor()
            k.execute("SELECT amount_pence, spent_pence, reserved_pence FROM mailing_budget_confirmations WHERE id=%s;",
                      (budget_id,))
            r = k.fetchone(); c.rollback(); return r

        def reconcile(reg=None):
            with patch("letter_providers.registry.build_registry_from_env", return_value=reg or registry):
                return purge.reconcile_unknown_outcome_obligations()

        # ---- R1 acceptance settles once ---------------------------------
        o1 = make_obligation("REF-ACC")
        adapter.script["REF-ACC"] = ProviderResult("accepted", "fake_test", "REF-ACC", cost_pence=EST, message="Accepted (scripted).")
        b0 = budget()
        check("R1 setup: order unknown with 108p reserved", row(o1)[0] == "unknown" and res(o1) == {"reserved": (1, EST)})
        r = reconcile()
        st = row(o1)
        check("R1 acceptance: status provider_accepted, timestamp set", st[0] == "provider_accepted" and st[2], str(st))
        check("R1 acceptance: reservation settled once (108p), none left reserved", res(o1) == {"settled": (1, EST)}, str(res(o1)))
        b1 = budget()
        check("R1 acceptance: budget spent +108 (the ESTIMATE), reserved -108", b1[1] == b0[1] + EST and b1[2] == b0[2] - EST, f"{b0} -> {b1}")
        check("R1 summary counts one resolved", r["resolved"] == 1, str(r))
        r2 = reconcile(); r3 = reconcile()
        check("R1 repeat x2: nothing more spent, status and attempts unchanged",
              budget() == b1 and row(o1) == st and r2["resolved"] == 0 and r3["resolved"] == 0, f"{budget()} {row(o1)}")

        # ---- R2 dispatched also settles once ----------------------------
        o2 = make_obligation("REF-DSP")
        adapter.script["REF-DSP"] = ProviderResult("dispatched", "fake_test", "REF-DSP", cost_pence=EST, message="Sent (scripted).")
        b0 = budget(); reconcile(); b1 = budget()
        check("R2 dispatched: status dispatched, settled once, spent +108",
              row(o2)[0] == "dispatched" and row(o2)[3] and res(o2) == {"settled": (1, EST)} and b1[1] == b0[1] + EST, f"{row(o2)} {res(o2)}")

        # ---- R3 definite UNCHARGED rejection releases once --------------
        o3 = make_obligation("REF-REJ-UNCHARGED")
        adapter.script["REF-REJ-UNCHARGED"] = ProviderResult("rejected", "fake_test", "REF-REJ-UNCHARGED",
                                                              message="Rejected before any charge (scripted).",
                                                              confirmed_uncharged=True)
        b0 = budget(); r = reconcile(); b1 = budget(); st = row(o3)
        check("R3 uncharged rejection: status 'failed' (valid internal status), failed_at set", st[0] == "failed" and st[4], str(st))
        check("R3 uncharged rejection: reservation released once, nothing spent",
              res(o3) == {"released": (1, EST)} and b1[1] == b0[1] and b1[2] == b0[2] - EST, f"{res(o3)} {b0}->{b1}")
        check("R3 uncharged rejection: order is NOT reset to ready", st[0] != "ready")
        reconcile(); reconcile()
        check("R3 repeat x2: no second release (reserved total unchanged)", budget() == b1 and row(o3) == st, f"{budget()}")

        # ---- R4 rejection-type status but charge NOT proven absent ------
        o4 = make_obligation("REF-REJ-UNPROVEN")
        adapter.script["REF-REJ-UNPROVEN"] = ProviderResult("rejected", "fake_test", "REF-REJ-UNPROVEN",
                                                             message="status='returned' (scripted; may have been posted and paid).")
        b0 = budget(); st0 = row(o4); r = reconcile()
        check("R4 rejection w/o proof of no charge: stays 'unknown', reservation HELD",
              row(o4) == st0 and res(o4) == {"reserved": (1, EST)} and budget() == b0, f"{row(o4)} {res(o4)}")
        check("R4 counted for manual review, not resolved", r["held_charge_unproven"] == 1 and r["resolved"] == 0, str(r))
        reconcile()
        check("R4 repeat: still held, nothing changed", row(o4) == st0 and res(o4) == {"reserved": (1, EST)} and budget() == b0)

        # ---- R5 unresolved outcomes stay held ----------------------------
        o5a = make_obligation("REF-STILL-UNKNOWN"); o5b = make_obligation("REF-NO-ANSWER")
        adapter.script["REF-STILL-UNKNOWN"] = ProviderResult("unknown", "fake_test", "REF-STILL-UNKNOWN", message="Unrecognised status (scripted).")
        # REF-NO-ANSWER has no script entry -> check_status returns None
        b0 = budget(); sa, sb = row(o5a), row(o5b); r = reconcile()
        check("R5 unresolved/no-answer: both stay 'unknown' with reservations held, attempts unchanged",
              row(o5a) == sa and row(o5b) == sb and res(o5a) == {"reserved": (1, EST)} and res(o5b) == {"reserved": (1, EST)}
              and budget() == b0, f"{row(o5a)} {row(o5b)}")
        check("R5 counted as still_unknown", r["still_unknown"] >= 2, str(r))

        # ---- R6 no provider_reference: skipped, untouched ----------------
        o6 = make_obligation(None); s6 = row(o6); b0 = budget(); r = reconcile()
        check("R6 no provider_reference: skipped, status/reservation untouched",
              row(o6) == s6 and res(o6) == {"reserved": (1, EST)} and budget() == b0 and r["skipped_no_adapter"] >= 1, str(r))

        # ---- R7 transactional consistency (failure mid-way) --------------
        o7 = make_obligation("REF-FAULT"); o7b = make_obligation("REF-AFTER-FAULT")
        adapter.script["REF-FAULT"] = ProviderResult("accepted", "fake_test", "REF-FAULT", message="Accepted (scripted).", cost_pence=EST)
        adapter.script["REF-AFTER-FAULT"] = ProviderResult("accepted", "fake_test", "REF-AFTER-FAULT", message="Accepted (scripted).", cost_pence=EST)
        b0 = budget(); real_settle = funding.FundingGate.settle_actual

        def faulty_settle(self, cur, obligation_id, actual_pence):
            if obligation_id == o7:
                raise RuntimeError("simulated failure while settling")
            return real_settle(self, cur, obligation_id, actual_pence)

        with patch.object(funding.FundingGate, "settle_actual", faulty_settle):
            r = reconcile()
        check("R7 settle failure: status change rolled back (still 'unknown') and reservation still held",
              row(o7)[0] == "unknown" and res(o7) == {"reserved": (1, EST)}, f"{row(o7)} {res(o7)}")
        check("R7 failure isolated: the next order in the batch still resolved and settled once",
              row(o7b)[0] == "provider_accepted" and res(o7b) == {"settled": (1, EST)} and r["errors"] == 1, f"{row(o7b)} {r}")
        reconcile()
        check("R7 retry after fault: resolves and settles exactly once",
              row(o7)[0] == "provider_accepted" and res(o7) == {"settled": (1, EST)}, f"{row(o7)} {res(o7)}")

        # ---- R8 resolved by someone else mid-run: no double spend --------
        o8 = make_obligation("REF-RACE")
        adapter.script["REF-RACE"] = ProviderResult("accepted", "fake_test", "REF-RACE", message="Accepted (scripted).", cost_pence=EST)

        def resolve_elsewhere():
            c = new_conn(); k = c.cursor()
            fulfilment.mark_provider_result(k, o8, outcome="accepted", is_dry_run=False,
                                            provider_name="fake_test", provider_reference="REF-RACE")
            gate.settle_actual(k, o8, EST); c.commit(); c.close()

        adapter.on_lookup["REF-RACE"] = resolve_elsewhere
        b0 = budget(); reconcile(); b1 = budget()
        check("R8 resolved elsewhere between lookup and write: spent only once, not doubled",
              res(o8) == {"settled": (1, EST)} and b1[1] == b0[1] + EST and row(o8)[1] == 2, f"{res(o8)} {b0}->{b1} {row(o8)}")
        adapter.on_lookup.clear()


        # =================================================================
        # Section N: no-reference recovery (real adapter, mocked list API)
        # =================================================================
        import json as _json
        from urllib.parse import urlparse, parse_qs
        import letter_providers.intelliprint_provider as ip
        from letter_providers.base import LetterRequest

        os.environ["INTELLIPRINT_API_KEY"] = "test-key-not-real"   # fake; opener is mocked, no network
        os.environ["INTELLIPRINT_TEST_MODE"] = "true"

        class FakeIntelliprint:
            """In-process stand-in for Intelliprint's list/retrieve API, following
            the documented list semantics (reference/prints/list)."""
            def __init__(self):
                self.prints, self.calls = [], []
                self.ignore_reference_filter = False
                self.fail_with = None           # exception to raise on every call
                self.mutate = None              # fn(page_dict) -> page_dict, to simulate bad responses
                self.always_more = False
                self.hook = None                # called (once) at the first request

            def add(self, reference, pid, status="waiting_to_print", testmode=True, confirmed=True):
                self.prints.append({"id": pid, "object": "print", "reference": reference, "created": 1790000000,
                                    "confirmed": confirmed, "testmode": testmode, "type": "letter",
                                    "cost": {"amount": 90_000_000, "tax": 18_000_000, "after_tax": 108_000_000},
                                    "letters": [{"status": status}]})

            def open(self, request, timeout=None):
                url = urlparse(request.full_url); q = parse_qs(url.query)
                self.calls.append((request.get_method(), url.path, {k: v[0] for k, v in q.items()}))
                if self.hook:
                    h, self.hook = self.hook, None
                    h()
                if self.fail_with:
                    raise self.fail_with
                if request.get_method() != "GET":
                    raise AssertionError("recovery must never POST")
                if url.path.rstrip("/").endswith("/prints"):
                    tm = q.get("testmode", ["false"])[0] == "true"       # documented default: live only
                    rows = [p for p in self.prints if p["testmode"] == tm]
                    if "reference" in q and not self.ignore_reference_filter:
                        rows = [p for p in rows if p["reference"] == q["reference"][0]]
                    skip, limit = int(q.get("skip", ["0"])[0]), int(q.get("limit", ["10"])[0])
                    page = {"object": "list", "data": rows[skip:skip + limit], "total_available": len(rows),
                            "has_more": skip + limit < len(rows)}
                    if self.always_more:
                        page["has_more"] = True
                    if self.mutate:
                        page = self.mutate(page)
                else:
                    pid = url.path.rstrip("/").split("/")[-1]
                    page = next((p for p in self.prints if p["id"] == pid), {"error": {"type": "not_found"}})
                body = _json.dumps(page).encode()
                resp = MagicMock(); resp.read.return_value = body
                resp.__enter__.return_value = resp
                return resp

            def posts(self):
                return [c for c in self.calls if c[0] != "GET"]

        srv = FakeIntelliprint()
        real_adapter = ip.IntelliprintProvider()
        real_registry = types.SimpleNamespace(slots=[types.SimpleNamespace(adapter=real_adapter)])

        def recover():
            with patch("letter_providers.intelliprint_provider.build_opener") as bo:
                bo.return_value.open.side_effect = srv.open
                return reconcile(real_registry)

        # Real send() ambiguity -> the real marker is recorded in the message
        with patch.object(ip, "_render_html_to_pdf_bytes", return_value=b"%PDF-fake"), \
             patch("letter_providers.intelliprint_provider.build_opener") as bo:
            bo.return_value.open.side_effect = TimeoutError("simulated lost response")
            lost = real_adapter.send(LetterRequest(idempotency_key="n-marker", lead_reference="TKTEST", address_lines={},
                                                   applicant_name=None, content_html="<p>x</p>", content_fingerprint="f"))
        check("N0 real send() timeout -> unknown, message records the submitted mode",
              lost.outcome == "unknown" and "[submitted_testmode=true]" in lost.message, lost.message)
        MARK_T = "Ambiguous outcome [submitted_testmode=true]"
        MARK_L = "Ambiguous outcome [submitted_testmode=false]"

        def make_unknown(idem, last_error=MARK_T):
            return make_obligation(None, provider_name="intelliprint", last_error=last_error, idem=idem)

        def snap(oid):
            c = RD; k = c.cursor()
            k.execute("SELECT status, provider_reference, last_error, attempts FROM letter_obligations WHERE id=%s;", (oid,))
            r = k.fetchone(); c.rollback(); return r

        # ---- N1 single valid match -> id saved, settled once --------------
        o = make_unknown("n1-key"); srv.add(ip.submission_reference("n1-key"), "print_n1")
        b0 = budget(); r = recover(); st = snap(o); b1 = budget()
        check("N1 single match: id saved, status provider_accepted, reservation settled once, spent +108",
              st[0] == "provider_accepted" and st[1] == "print_n1" and res(o) == {"settled": (1, EST)} and b1[1] == b0[1] + EST,
              f"{st} {res(o)} {b0}->{b1}")
        check("N1 summary: resolved and references_recovered", r["resolved"] == 1 and r["references_recovered"] == 1, str(r))
        check("N1 request was exact-reference, explicit test mode, GET only",
              any(c[2].get("reference") == ip.submission_reference("n1-key") and c[2].get("testmode") == "true" for c in srv.calls)
              and not srv.posts())
        before = (snap(o), budget(), len(srv.calls))
        recover(); recover()
        check("N1 repeat x2: nothing changes, no further spend", (snap(o), budget()) == before[:2], str(snap(o)))

        # ---- N2 zero matches -> held with reason, never resolved ----------
        o = make_unknown("n2-key"); b0 = budget(); r = recover(); st = snap(o)
        check("N2 zero matches: stays unknown, reservation held, no id saved",
              st[0] == "unknown" and st[1] is None and res(o) == {"reserved": (1, EST)} and budget() == b0, str(st))
        check("N2 review reason says no match is NOT proof, original marker kept",
              "RECOVERY: No job found" in st[2] and "NOT prove" in st[2] and "[submitted_testmode=true]" in st[2], st[2])
        check("N2 counted recovery_held and reported", r["recovery_held"] == 1 and any(i == o for i, _ in r["review"]), str(r))
        recover(); recover()
        check("N2 repeat x2: reason replaced not accumulated, nothing else changed",
              snap(o)[2] == st[2] and snap(o)[0] == "unknown" and snap(o)[3] == st[3] and budget() == b0, snap(o)[2])

        # ---- N3 multiple matches -> held ----------------------------------
        o = make_unknown("n3-key"); ref = ip.submission_reference("n3-key")
        srv.add(ref, "print_n3a"); srv.add(ref, "print_n3b"); b0 = budget(); recover(); st = snap(o)
        check("N3 two jobs share the reference: held (no id saved, no spend)",
              st[0] == "unknown" and st[1] is None and "2 jobs share" in st[2] and res(o) == {"reserved": (1, EST)} and budget() == b0, str(st))

        # ---- N4 pagination ------------------------------------------------
        with patch.object(ip, "_LOOKUP_PAGE_SIZE", 2):
            o = make_unknown("n4-key"); ref4 = ip.submission_reference("n4-key")
            srv.ignore_reference_filter = True      # server returns unrelated jobs too: client must filter exactly
            for i in range(4):
                srv.add(f"treekey_other_{i}", f"print_noise{i}")
            srv.add(ref4, "print_n4")
            srv.calls.clear(); recover(); st = snap(o)
            skips = [c[2].get("skip") for c in srv.calls if c[2].get("reference") == ref4]
            check("N4 pagination: every page read (skip 0,2,4,...) and the single exact match across pages resolved",
                  st[1] == "print_n4" and st[0] == "provider_accepted" and skips[:3] == ["0", "2", "4"], f"{st} {skips}")
            srv.ignore_reference_filter = False
            # 3 matches spread over 2 pages -> all read, held as multiple
            o = make_unknown("n4b-key"); ref4b = ip.submission_reference("n4b-key")
            for i in range(3):
                srv.add(ref4b, f"print_n4b{i}")
            srv.calls.clear(); recover(); st = snap(o)
            check("N4b matches spread across pages are all counted: 3 jobs -> held",
                  st[0] == "unknown" and "3 jobs share" in st[2], str(st))

        # ---- N5 wrong mode / unrecorded mode / wrong reference ------------
        o = make_unknown("n5-key", MARK_L); srv.add(ip.submission_reference("n5-key"), "print_n5_test", testmode=True)
        srv.calls.clear(); recover(); st = snap(o)
        check("N5 order submitted LIVE: searches live mode only, a test-mode job is not accepted as the match",
              st[0] == "unknown" and st[1] is None and any(c[2].get("testmode") == "false" for c in srv.calls)
              and not any(c[2].get("testmode") == "true" and c[2].get("reference") == ip.submission_reference("n5-key") for c in srv.calls),
              f"{st} {srv.calls[-1:]}")
        o = make_unknown("n5b-key", "Ambiguous outcome, no marker recorded"); srv.add(ip.submission_reference("n5b-key"), "print_n5b")
        srv.calls.clear(); recover(); st = snap(o)
        check("N5b mode not recorded: NO search made, held with reason",
              st[0] == "unknown" and "not recorded reliably" in st[2] and not any(c[2].get("reference") == ip.submission_reference("n5b-key") for c in srv.calls), str(st))
        o = make_unknown("n5c-key", MARK_T + " " + MARK_L); srv.add(ip.submission_reference("n5c-key"), "print_n5c")
        srv.calls.clear(); recover()
        check("N5c conflicting modes recorded: NO search made, held",
              snap(o)[0] == "unknown" and "not recorded reliably" in snap(o)[2] and not any(c[2].get("reference") == ip.submission_reference("n5c-key") for c in srv.calls))
        o = make_unknown("n5d-key"); srv.add("treekey_someone_elses_reference", "print_n5d"); recover()
        check("N5d only a different reference exists: not a match, held", snap(o)[0] == "unknown" and snap(o)[1] is None)

        # ---- N6 lookup failures / incomplete responses --------------------
        def held_case(name, setup, expect):
            nonlocal srv
            o = make_unknown(f"{name}-key"); srv.add(ip.submission_reference(f"{name}-key"), f"print_{name}")
            setup(); b0 = budget(); recover(); st = snap(o)
            srv.fail_with = srv.mutate = None; srv.always_more = False
            check(f"{name}: incomplete/failed lookup keeps order unknown + reservation held (reason: {expect})",
                  st[0] == "unknown" and st[1] is None and expect in st[2] and res(o) == {"reserved": (1, EST)} and budget() == b0, f"{st} {res(o)} {b0} {budget()}")
            srv.prints = [p_ for p_ in srv.prints if p_['id'] != f'print_{name}']   # so later runs don't legitimately resolve it
            return o
        def setf(): srv.fail_with = TimeoutError("simulated")
        n6a = held_case("n6a", setf, "Lookup failed")
        def setm(): srv.mutate = lambda pg: {k: v for k, v in pg.items() if k != "has_more"}
        held_case("n6b", setm, "missing the documented")
        def sett(): srv.mutate = lambda pg: {**pg, "total_available": pg["total_available"] + 3}
        held_case("n6c", sett, "provider reports")
        def sete(): srv.mutate = lambda pg: {"error": {"type": "authentication_error"}}
        held_case("n6d", sete, "missing the documented")
        def seta(): srv.always_more = True
        held_case("n6e", seta, "")
        def setu():
            srv.prints[-1]["confirmed"] = False
        held_case("n6f", setu, "not confirmed")

        # ---- N7 match with unresolved status: id saved, then fix A resolves later
        o = make_unknown("n7-key"); srv.add(ip.submission_reference("n7-key"), "print_n7", status="strange_new_status")
        b0 = budget(); r = recover(); st = snap(o)
        check("N7 match with unrecognised status: id SAVED, still unknown, reservation held, reason recorded",
              st[0] == "unknown" and st[1] == "print_n7" and "does not prove acceptance" in st[2] and res(o) == {"reserved": (1, EST)} and budget() == b0, str(st))
        next(p for p in srv.prints if p["id"] == "print_n7")["letters"][0]["status"] = "printing"
        r = recover(); st = snap(o)
        check("N7 next run resolves via check_status (fix A) on the saved id: settled once",
              st[0] == "provider_accepted" and res(o) == {"settled": (1, EST)} and budget()[1] == b0[1] + EST, f"{st} {res(o)}")

        # ---- N8 match whose rejection-type status does not prove no charge
        o = make_unknown("n8-key"); srv.add(ip.submission_reference("n8-key"), "print_n8", status="returned")
        b0 = budget(); recover(); st = snap(o)
        check("N8 'returned' match: id saved, order unknown, reservation HELD (not released)",
              st[0] == "unknown" and st[1] == "print_n8" and res(o) == {"reserved": (1, EST)} and budget() == b0, str(st))

        # ---- N9 concurrent state changes ----------------------------------
        o = make_unknown("n9-key"); srv.add(ip.submission_reference("n9-key"), "print_n9")
        def resolve_elsewhere_n9():
            c = new_conn(); k = c.cursor()
            fulfilment.mark_provider_result(k, o, outcome="accepted", is_dry_run=False,
                                            provider_name="intelliprint", provider_reference="print_other_worker")
            gate.settle_actual(k, o, EST); c.commit(); c.close()
        srv.hook = resolve_elsewhere_n9
        b0 = budget(); recover(); st = snap(o); b1 = budget()
        check("N9 order resolved by another process during the lookup: not overwritten, spent once",
              st[0] == "provider_accepted" and st[1] == "print_other_worker" and res(o) == {"settled": (1, EST)} and b1[1] == b0[1] + EST, f"{st} {b0}->{b1}")
        o = make_unknown("n9b-key"); srv.add(ip.submission_reference("n9b-key"), "print_n9b")
        def save_ref_elsewhere():
            c = new_conn(); k = c.cursor()
            k.execute("UPDATE letter_obligations SET provider_reference = %s WHERE id = %s;", ("print_saved_meanwhile", o)); c.commit(); c.close()
        srv.hook = save_ref_elsewhere; b0 = budget(); recover(); st = snap(o)
        check("N9b another process saved a reference meanwhile: recovery does not overwrite or resolve",
              st[0] == "unknown" and st[1] == "print_saved_meanwhile" and res(o) == {"reserved": (1, EST)} and budget() == b0, str(st))

        check("N* recovery never made a non-GET request (no send / no resubmission)", not srv.posts(), str(srv.posts()))

        # ---- global invariants -------------------------------------------
        c = RD; k = c.cursor()
        k.execute("SELECT count(*) FROM letter_obligations WHERE status='ready';"); nready = k.fetchone()[0]
        k.execute("SELECT amount_pence, spent_pence, reserved_pence FROM mailing_budget_confirmations WHERE id=%s;", (budget_id,))
        a, sp, rv = k.fetchone()
        k.execute("SELECT COALESCE(SUM(amount_pence),0) FROM funding_reservations WHERE status='reserved';"); open_res = k.fetchone()[0]
        k.execute("SELECT COALESCE(SUM(amount_pence),0) FROM funding_reservations WHERE status='settled';"); settled = k.fetchone()[0]
        c.rollback()
        check("No send() call was ever made by reconciliation", adapter.sends == 0, str(adapter.sends))
        check("No order was reset to 'ready'", nready == 0, str(nready))
        check("Budget ledger consistent: reserved_pence == open reservations, spent_pence == settled",
              rv == open_res and sp == settled, f"reserved {rv} vs {open_res}; spent {sp} vs {settled}")
    finally:
        for c in conns:
            try:
                c.close()
            except Exception:
                pass
        cluster.cleanup()

    failed = [n for n, ok, _ in results if not ok]
    print(f"\n{len(results) - len(failed)}/{len(results)} checks passed.")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())

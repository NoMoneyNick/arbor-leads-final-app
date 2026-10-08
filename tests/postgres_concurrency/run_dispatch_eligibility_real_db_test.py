#!/usr/bin/env python3
"""
tests/postgres_concurrency/run_dispatch_eligibility_real_db_test.py

Regression test (8 Oct 2026) for the path a REAL letter takes, starting from a NORMALLY CREATED order (the flag is
never set by hand): fulfilment.create_allocation_and_obligation -> worker.promote_pending_approvals ->
worker.promote_pending_funding -> registry.attempt_send with the REAL IntelliprintProvider (its HTTP layer and PDF
renderer are replaced by in-process fakes; no network, no key, no Intelliprint) -> accepted_dispatch_check.
check_accepted_orders -> the customer-facing label.

Found by the earlier test-tool work: a live-accepted order kept is_dry_run = TRUE (the column default; nothing cleared
it), so the checker (which requires FALSE) never selected it. Reading the checker then showed two more gates that a
normal acceptance did not satisfy: the order's own record must show a LIVE submission (testmode=False in last_error),
which a normal acceptance never wrote; and the 20-minute loop on a host with no provider adapter refreshed
updated_at on every accepted order it could not check, which used up the one-hour window the PC needs.

Proves: a real-mode acceptance is eligible and is moved to 'dispatched' by the existing checker when run with the
live adapter; a host with no adapter leaves the order exactly as it was; real dry runs stay excluded and keep
their flag; a test-mode submission is never treated as live; only GET requests are made by the checker.

Exit codes: 0 all passed, 1 a check failed, 2 PostgreSQL unavailable.
"""
from __future__ import annotations

import json
import os
import re
import sys
from unittest.mock import MagicMock

HERE = os.path.dirname(os.path.abspath(__file__))
APP = os.path.abspath(os.path.join(HERE, "..", ".."))
sys.path[:0] = [APP, HERE]

import run_concurrency_tests as rct  # noqa: E402
import run_signup_real_db_tests as base  # noqa: E402
from run_reconciliation_real_db_test import ConvConn  # noqa: E402

DB = base.DB = "treekey_dispatch_eligibility_test"
EST = 120


def main() -> int:
    try:
        pg_bin = rct.find_pg_bin()
    except FileNotFoundError:
        print("PostgreSQL not available: NOT run.")
        return 2
    for n in ("psycopg2", "psycopg2.extras", "psycopg2.pool", "psycopg2.errors", "psycopg2.extensions"):
        sys.modules[n] = MagicMock()
    sys.modules.setdefault("stripe", MagicMock())
    for _ in range(20):
        try:
            import database  # noqa: F401
            break
        except ModuleNotFoundError as e:
            sys.modules[e.name] = MagicMock()
            sys.modules.pop("database", None)

    os.environ.update({"FUNDING_MODE": "hold", "SUPPRESSION_HASH_KEY": "unit-test-suppression-hash-key-not-a-real-secret",
                       "LETTER_DISPATCH_PIPELINE": "fulfilment", "TREEKEY_PRIVACY_CONTACT_EMAIL": "privacy@treekeytests.co.uk",
                       "INTELLIPRINT_API_KEY": "unit-test-key", "INTELLIPRINT_TEST_MODE": "false"})
    for k_ in ("LETTER_PROVIDER_PRIMARY", "LETTER_PROVIDER_BACKUP_1", "LETTER_PROVIDER_BACKUP_2"):
        os.environ.pop(k_, None)
    import fulfilment, funding, suppression, letter_content, worker
    import accepted_dispatch_check as adc
    import letter_providers.intelliprint_provider as ip
    from letter_providers.registry import ProviderRegistry, ProviderSlot, attempt_send, build_registry_from_env

    # ---- in-process fake of Intelliprint's HTTP layer (the REAL adapter code runs on top of it)
    calls = []

    class FakeResp:
        def __init__(self, payload):
            self.b = json.dumps(payload).encode()

        def read(self, n=-1):
            return self.b

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    state = {"remote_status": "waiting_to_print", "job": 0}

    class FakeOpener:
        def open(self, req, timeout=None):
            method, url = req.get_method(), req.full_url
            calls.append((method, url))
            if method == "POST":
                state["job"] += 1
                return FakeResp({"id": f"JOB-{state['job']}", "letters": [{"status": "waiting_to_print"}],
                                 "cost": {"after_tax": 108_000_000}})
            m = re.search(r"/prints/([^/?]+)$", url)
            if m:
                return FakeResp({"id": m.group(1), "letters": [{"status": state["remote_status"]}]})
            raise AssertionError(f"unexpected request {method} {url}")

    ip.build_opener = lambda *a, **k: FakeOpener()
    ip._render_html_to_pdf_bytes = lambda html: b"%PDF-1.4 fake"

    cluster = rct.Cluster(pg_bin, rct.PG_OS_USER_DEFAULT)
    results, conns = [], []

    def new_conn():
        k = ConvConn(cluster)
        conns.append(k)
        return k

    def check(name, cond, detail=""):
        results.append((name, bool(cond)))
        print(f"[{'PASS' if cond else 'FAIL'}] {name}" + (f"  -- {detail}" if detail and not cond else ""))

    try:
        cluster.start()
        cluster.createdb(DB)
        base.DB = DB
        src = open(os.path.join(APP, "database.py"), encoding="utf-8").read()
        leads_create = re.search(r"CREATE TABLE IF NOT EXISTS leads \(.*?\n\s*\);", src, re.S).group(0)
        alters = re.findall(r'"(ALTER TABLE leads ADD COLUMN IF NOT EXISTS [^"]+;)"', src)
        cluster.psql("CREATE EXTENSION IF NOT EXISTS pgcrypto;\n" + leads_create + "\n" + "\n".join(alters), db=DB)
        boot = new_conn()
        k = boot.cursor()
        fulfilment.init_fulfilment_schema(k)
        funding.init_funding_schema(k)
        suppression.init_suppression_schema(k)
        letter_content.init_letter_content_schema(k)
        email = "buyer.account@treekeytests.co.uk"
        letter_content.upsert_contractor_settings(k, letter_content.ContractorLetterSettings(
            contractor_email=email, business_name="Ashcroft Tree Surgery Ltd", phone="01234 567890", contact_email=email))
        st = letter_content.get_contractor_settings(k, email)
        letter_content.approve_template(k, email, preview_fingerprint=letter_content.template_fingerprint(st))
        boot.commit()
        boot.close()
        database.SURL = "scratch"
        database.get_db_conn = new_conn

        def q(sql, params=None):
            cn = new_conn()
            cu = cn.cursor()
            cu.execute(sql, params)
            rows = list(cu.rows)
            cn.commit()
            cn.close()
            return rows

        def label(ref):
            """The customer label, using the REAL function. The psql-backed test connection returns booleans as
            't'/'f' text (psycopg2 returns real bools), so the row is read from the database and handed back
            to the function with real booleans."""
            st, dr = q("SELECT status, is_dry_run FROM letter_obligations WHERE lead_reference = %s "
                       "ORDER BY created_at DESC LIMIT 1", (ref,))[0]

            class _Cur:
                def execute(self, *a, **k): pass
                def fetchone(self): return (st, dr in (True, "t"))
                def close(self): pass

            class _Conn:
                def cursor(self): return _Cur()
                def close(self): pass

            saved = database.get_db_conn
            database.get_db_conn = lambda: _Conn()
            try:
                return fulfilment.get_letter_status_label_for_lead_reference(ref)
            finally:
                database.get_db_conn = saved

        n_orders = [0]

        def normal_order():
            """A normally created order: lead row + allocation/obligation exactly as the sale path creates it."""
            n_orders[0] += 1
            ref = f"TKTEST-E{n_orders[0]}"
            q("INSERT INTO leads (reference, address, summary, council_source, status) VALUES (%s,%s,%s,%s,'claimed')",
              (ref, "7 Maple Court, Harrogate, HG1 2AB", "T1 Oak - reduce crown by 15%", "Test"))
            cn = new_conn()
            cu = cn.cursor()
            res = fulfilment.create_allocation_and_obligation(
                cu, lead_reference=ref, lead_id=ref, address="7 Maple Court, Harrogate, HG1 2AB", applicant_name="Pat Tester",
                buyer_email=email, allocation_type="single_purchase", sale_context="single_purchase",
                source_payment_ref=f"pay-{n_orders[0]}")
            cn.commit()
            cn.close()
            return ref, res.obligation_id

        def row(ref):
            return q("SELECT status, is_dry_run, attempts, COALESCE(last_error,''), provider_accepted_at IS NOT NULL, "
                     "dispatched_at IS NOT NULL, COALESCE(provider_reference,''), provider_cost_pence FROM letter_obligations "
                     "WHERE lead_reference = %s", (ref,))[0]

        def advance_to_ready_and_send(ref, oid, adapter, *, dry):
            cn = new_conn()
            cu = cn.cursor()
            worker.promote_pending_approvals(cu)
            gate = funding.FundingGate(mode="hold")
            if not q("SELECT 1 FROM mailing_budget_confirmations"):
                gate.confirm_budget(cu, amount_pence=1000, confirmed_by="test", note="regression test budget")
            worker.promote_pending_funding(cu, gate, estimated_cost_pence=EST)
            cu.execute("SELECT idempotency_key, approved_content_html FROM letter_obligations WHERE id = %s", (oid,))
            idem, html = cu.fetchone()
            reg = ProviderRegistry([ProviderSlot(adapter=adapter, role="primary")])
            out = attempt_send(cu, reg, oid, worker_id="t", content_html=html,
                               address_lines={"line1": "7 Maple Court, Harrogate, HG1 2AB", "city": "", "postcode": "", "country": "GB"},
                               applicant_name="Pat Tester", lead_reference=ref, idempotency_key=idem, is_dry_run=dry,
                               estimated_cost_pence=EST, gate=gate)
            cn.commit()
            cn.close()
            return out

        def age(ref, hours=3):
            q("UPDATE letter_obligations SET updated_at = NOW() - (%s * INTERVAL '1 hour') WHERE lead_reference = %s", (hours, ref))

        live = ip.IntelliprintProvider()
        pc_registry = ProviderRegistry([ProviderSlot(adapter=live, role="primary")])

        # ============================================================ A. a real, normally created order
        ref_a, oid_a = normal_order()
        check("precondition: a normally created order starts with is_dry_run = TRUE (the column default)",
              row(ref_a)[:2] == ("pending_approval", True) or row(ref_a)[:2] == ("pending_approval", "t"), str(row(ref_a)))
        out = advance_to_ready_and_send(ref_a, oid_a, live, dry=False)
        a = row(ref_a)
        check("real-mode acceptance through the real adapter: status provider_accepted, one submission, cost 108p",
              a[0] == "provider_accepted" and int(a[2]) == 1 and a[6] == "JOB-1" and int(a[7]) == 108, str(a))
        check("REGRESSION: a real acceptance clears is_dry_run", a[1] in (False, "f"), str(a[1]))
        check("the order's own record shows a LIVE submission (testmode=False), and only that",
              adc.recorded_live_submission(a[3]), repr(a[3])[:200])
        check("the record holds no signed PDF link or letter content", "http" not in a[3] and "<" not in a[3], repr(a[3])[:200])

        # ---- a host with NO provider adapter (Render) must leave the order exactly as it is
        age(ref_a, 3)
        before = q("SELECT status, updated_at::text FROM letter_obligations WHERE lead_reference = %s", (ref_a,))[0]
        n_calls = len(calls)
        os.environ.pop("LETTER_PROVIDER_PRIMARY", None)
        summ = adc.check_accepted_orders(registry=build_registry_from_env())
        after = q("SELECT status, updated_at::text FROM letter_obligations WHERE lead_reference = %s", (ref_a,))[0]
        check("a host with no adapter: skipped_no_adapter, no provider request",
              summ["skipped_no_adapter"] == 1 and summ["checked"] == 0 and len(calls) == n_calls, str(summ))
        check("REGRESSION: ...and it does NOT refresh updated_at (the PC's one-hour window is not used up)",
              before == after, f"{before} -> {after}")

        # ---- the PC pass (live adapter), existing function unchanged
        state["remote_status"] = "printing"
        summ = adc.check_accepted_orders(registry=pc_registry)
        check("PC pass, provider still printing: checked, not dispatched",
              summ["checked"] == 1 and summ["dispatched"] == 0 and summ["still_accepted"] == 1, str(summ))
        check("...that pass made GET requests only (never a send)", all(m == "GET" for m, _u in calls[n_calls:]), str(calls[n_calls:]))
        age(ref_a, 3)
        state["remote_status"] = "sent"
        summ = adc.check_accepted_orders(registry=pc_registry)
        a = row(ref_a)
        check("PC pass, provider reports sent: the existing checker moves the order to 'dispatched'",
              summ["dispatched"] == 1 and a[0] == "dispatched" and a[5] in (True, "t"), f"{summ} {a}")
        check("...attempts, provider reference and cost are untouched",
              int(a[2]) == 1 and a[6] == "JOB-1" and int(a[7]) == 108)
        check("...and the customer label now reads 'Posted'",
              label(ref_a) == "Posted",
              str(label(ref_a)))

        # ============================================================ B. real dry runs stay excluded
        state["remote_status"] = "waiting_to_print"
        ref_b, oid_b = normal_order()
        advance_to_ready_and_send(ref_b, oid_b, live, dry=True)
        b = row(ref_b)
        check("a real dry run ends 'dry_run', keeps is_dry_run = TRUE, no accepted time, no provider reference",
              b[0] == "dry_run" and b[1] in (True, "t") and b[4] in (False, "f") and b[6] == "", str(b))
        age(ref_b, 3)
        n_calls = len(calls)
        summ = adc.check_accepted_orders(registry=pc_registry)
        check("the checker never selects a dry-run order (nothing checked, no request)",
              summ["checked"] == 0 and len(calls) == n_calls and row(ref_b)[0] == "dry_run", str(summ))
        check("...and its label is still 'Preparing to post'",
              label(ref_b) == "Preparing to post")
        # a dry-run result that claims 'accepted' must still be a dry run
        ref_c, oid_c = normal_order()
        cn = new_conn(); cu = cn.cursor()
        fulfilment.mark_provider_result(cu, oid_c, outcome="accepted", is_dry_run=True, provider_name="intelliprint",
                                        provider_reference="NOT-REAL")
        cn.commit(); cn.close()
        c_ = row(ref_c)
        check("mark_provider_result(is_dry_run=True, outcome='accepted') still ends 'dry_run' with the flag TRUE and no accepted time",
              c_[0] == "dry_run" and c_[1] in (True, "t") and c_[4] in (False, "f"), str(c_))
        # a legacy row (accepted by the pre-fix code: flag still TRUE) stays excluded by the checker's own SQL
        ref_d, oid_d = normal_order()
        q("UPDATE letter_obligations SET status='provider_accepted', provider_name='intelliprint', provider_reference='LEGACY', "
          "provider_accepted_at = NOW() - INTERVAL '3 hours', updated_at = NOW() - INTERVAL '3 hours', "
          "last_error = 'Intelliprint status=''waiting_to_print'' (testmode=False).' WHERE id = %s", (oid_d,))
        n_calls = len(calls)
        summ = adc.check_accepted_orders(registry=pc_registry)
        check("a row whose flag is still TRUE is excluded by the checker (unchanged behaviour)",
              summ["checked"] == 0 and len(calls) == n_calls, str(summ))

        # ============================================================ C. a TEST-mode submission is never treated as live
        os.environ["INTELLIPRINT_TEST_MODE"] = "true"
        testadapter = ip.IntelliprintProvider()
        os.environ["INTELLIPRINT_TEST_MODE"] = "false"
        ref_e, oid_e = normal_order()
        advance_to_ready_and_send(ref_e, oid_e, testadapter, dry=False)
        e = row(ref_e)
        check("a test-mode acceptance records testmode=true and is NOT live", e[0] == "provider_accepted"
              and "testmode=true" in e[3] and not adc.recorded_live_submission(e[3]), repr(e[3])[:120])
        age(ref_e, 3)
        summ = adc.check_accepted_orders(registry=pc_registry)
        check("...so the live checker skips it as not live", summ["skipped_not_live"] >= 1 and row(ref_e)[0] == "provider_accepted", str(summ))

        # ============================================================ D. other real outcomes
        class Scripted(ip.IntelliprintProvider):
            def __init__(self, outcome):
                super().__init__()
                self.outcome = outcome

            def send(self, request):
                from letter_providers.base import ProviderResult
                return ProviderResult(outcome=self.outcome, provider_name="intelliprint", message="scripted")

        ref_f, oid_f = normal_order()
        advance_to_ready_and_send(ref_f, oid_f, Scripted("unknown"), dry=False)
        f = row(ref_f)
        check("a real attempt that ends 'unknown' is held there (not retried), flag cleared as a real attempt",
              f[0] == "unknown" and f[1] in (False, "f") and int(f[2]) == 1, str(f))
    finally:
        for cn in conns:
            try:
                cn.close()
            except Exception:  # noqa: BLE001
                pass
        try:
            cluster.stop()
        except Exception:  # noqa: BLE001
            pass

    failed = [n for n, ok in results if not ok]
    print(f"\n{len(results) - len(failed)}/{len(results)} checks passed.")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())

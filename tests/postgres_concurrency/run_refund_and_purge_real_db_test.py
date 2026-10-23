"""
Real-Postgres checks (disposable server, same helper as the other runners here) for:
  1. refund_sync.apply_refund_state / read_fulfilment_state  -- full vs partial vs none, idempotency,
     out-of-order, stale statuses, and that NOTHING outside the payment row is changed.
  2. retention_dispatch_purge -- one 72-hour rule for every dispatched order (test-mode included, no longer
     window); the sweep clears exactly the right rows and keeps the test/real record and provider reference.

Run: python tests/postgres_concurrency/run_refund_and_purge_real_db_test.py
"""
import os
import sys
import types
from unittest.mock import MagicMock

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path[:0] = [os.path.dirname(os.path.dirname(_HERE)), _HERE]
import run_concurrency_tests as rct  # noqa: E402
import run_signup_real_db_tests as base  # noqa: E402

fake_db = types.ModuleType("database")
fake_db.get_db_conn = MagicMock()
fake_db._redact_address_from_summary = lambda s: s
sys.modules["database"] = fake_db
import fulfilment, funding, refund_sync  # noqa: E402
import retention_dispatch_purge as purge  # noqa: E402

base.DB = "refund_purge_test"
results = []


def check(name, cond, detail=""):
    results.append(bool(cond))
    print(f"[{'PASS' if cond else 'FAIL'}] {name}" + (f" -- {detail}" if detail and not cond else ""))


class Cur:
    """PsqlCursor that can also take a list parameter (ANY(%s))."""
    def __init__(self, c): self.c = c; self.inner = c.cursor()
    def execute(self, sql, params=None):
        if params is not None:
            params = list(params)
            parts = sql.split("%s")
            out = parts[0]
            keep = []
            for i, p in enumerate(params):
                if isinstance(p, list):
                    out += "ARRAY[" + ",".join("'" + str(x).replace("'", "''") + "'" for x in p) + "]::text[]" + parts[i + 1]
                else:
                    out += "%s" + parts[i + 1]; keep.append(p)
            sql, params = out, keep
        return self.inner.execute(sql, params if params else None)
    def __getattr__(self, n): return getattr(self.inner, n)


class Conn:
    def __init__(self, c): self.c = c
    def cursor(self): return Cur(self.c)
    def commit(self): self.c.commit()
    def rollback(self): self.c.rollback()
    def close(self): self.c.close(); conns.remove(self.c)


cl = rct.Cluster(rct.find_pg_bin(), rct.PG_OS_USER_DEFAULT)
conns = []
def newconn():
    c = base.PsqlConn(cl); conns.append(c); return c
def run(sql):
    c = newconn(); cu = c.cursor()
    try:
        cu.execute(sql); rows = cu.fetchall() if cu.rows else []; c.commit(); return rows
    finally:
        c.close(); conns.remove(c)

def seed():
    c = newconn(); cu = c.cursor()
    for t in ("funding_reservations", "letter_obligations", "lead_allocations", "mailing_budget_confirmations",
              "payments", "letter_dispatches", "leads", "payment_allocation_reconciliation"):
        cu.execute(f"DROP TABLE IF EXISTS {t} CASCADE")
    fulfilment.init_fulfilment_schema(cu); funding.init_funding_schema(cu)
    cu.execute("CREATE TABLE payments (id UUID PRIMARY KEY DEFAULT gen_random_uuid(), stripe_session_id TEXT UNIQUE, status TEXT, amount_pence INT, customer_email TEXT, account_email TEXT, lead_id TEXT, offer_kind TEXT, updated_at TIMESTAMPTZ DEFAULT NOW(), refunded_pence INT NOT NULL DEFAULT 0, refund_synced_at TIMESTAMPTZ)")
    cu.execute("CREATE TABLE letter_dispatches (id UUID PRIMARY KEY DEFAULT gen_random_uuid(), lead_reference TEXT, address TEXT, applicant_name TEXT, sent_at TIMESTAMPTZ)")
    cu.execute("CREATE TABLE leads (reference TEXT PRIMARY KEY, address TEXT, applicant_name TEXT, summary TEXT)")
    purge.init_dispatch_purge_schema(cu)
    c.commit(); c.close(); conns.remove(c)

try:
    cl.start(); cl.createdb("refund_purge_test")

    # ---------------- refund sync ----------------
    seed()
    run("INSERT INTO payments (stripe_session_id,status,amount_pence,account_email,offer_kind) VALUES ('cs_1','paid',499,'s@x.com','first_offer')")
    run("INSERT INTO lead_allocations (id,lead_reference,buyer_email,allocation_type,source_payment_ref,idempotency_key) VALUES ('aaaaaaaa-0000-0000-0000-000000000001','L1','s@x.com','single_purchase','cs_1','i1')")
    run("INSERT INTO letter_obligations (id,allocation_id,lead_reference,address,buyer_email,sale_context,status,idempotency_key) VALUES ('bbbbbbbb-0000-0000-0000-000000000001','aaaaaaaa-0000-0000-0000-000000000001','L1','a','s@x.com','single_purchase','dispatched','o1')")
    run("INSERT INTO mailing_budget_confirmations (id,amount_pence,spent_pence,reserved_pence,confirmed_by) VALUES ('cccccccc-0000-0000-0000-000000000001',108,108,0,'t')")
    run("INSERT INTO funding_reservations (obligation_id,budget_confirmation_id,amount_pence,status) VALUES ('bbbbbbbb-0000-0000-0000-000000000001','cccccccc-0000-0000-0000-000000000001',108,'settled')")
    def snapshot_other():
        return (run("SELECT status,attempts,provider_reference FROM letter_obligations"),
                run("SELECT buyer_email,allocation_type FROM lead_allocations"),
                run("SELECT amount_pence,spent_pence,reserved_pence,active FROM mailing_budget_confirmations"),
                run("SELECT status,amount_pence FROM funding_reservations"))
    other0 = snapshot_other()
    def sync(session, amount, refunded):
        c = newconn(); cu = Cur(c)
        try:
            r = refund_sync.apply_refund_state(cu, session, lambda: (amount, refunded)); c.commit(); return r
        finally:
            c.close(); conns.remove(c)
    pay = lambda: run("SELECT status, refunded_pence, refund_synced_at IS NOT NULL FROM payments WHERE stripe_session_id='cs_1'")[0]

    r = sync("cs_1", 499, 200)
    check("partial refund: status stays paid, amount recorded, changed", r["kind"] == "partial" and r["changed"] and pay()[:2] == ("paid", "200"), str((r, pay())))
    r = sync("cs_1", 499, 200)
    check("partial refund repeated: no change (idempotent)", not r["changed"], str(r))
    r = sync("cs_1", 499, 499)
    check("full refund: status refunded, amount = charge", r["kind"] == "full" and r["changed"] and pay()[:2] == ("refunded", "499"), str((r, pay())))
    r = sync("cs_1", 499, 499)
    check("full refund repeated: no change (idempotent)", not r["changed"], str(r))
    r = sync("cs_1", 499, 200)
    check("a later reading with less refunded (refund failed/cancelled) moves back to paid/partial", r["changed"] and pay()[:2] == ("paid", "200"), str((r, pay())))
    r = sync("cs_1", 499, 0)
    check("nothing refunded: back to paid, 0", r["kind"] == "none" and pay()[:2] == ("paid", "0"), str((r, pay())))
    check("letter order, allocation, budget and reservation rows untouched by every refund state", snapshot_other() == other0, str(snapshot_other()))
    for bad in [(499, 500), (499, -1), (0, 0), (499, 1.5)]:
        try:
            sync("cs_1", *bad); ok = False
        except ValueError:
            ok = True
        check(f"impossible figures {bad} refused", ok)
    check("payment untouched after refused figures", pay()[:2] == ("paid", "0"), str(pay()))
    check("unknown payment reference -> not found", sync("cs_nope", 499, 499)["found"] is False)
    run("UPDATE payments SET status='pending' WHERE stripe_session_id='cs_1'")
    r = sync("cs_1", 499, 499)
    check("a payment that never completed is not touched", r["found"] and not r["applicable"] and pay()[:2] == ("pending", "0"), str((r, pay())))
    run("UPDATE payments SET status='refund_failed', refunded_pence=0 WHERE stripe_session_id='cs_1'")
    r = sync("cs_1", 499, 200)
    check("refund_failed stays flagged on a partial refund", pay()[0] == "refund_failed", str(pay()))
    r = sync("cs_1", 499, 499)
    check("refund_failed becomes refunded once Stripe shows a full refund", pay()[0] == "refunded", str(pay()))
    # fulfilment stage
    def st(ref="cs_1"):
        c = newconn()
        try:
            r = refund_sync.read_fulfilment_state(Cur(c), ref); c.commit(); return r
        finally:
            c.close(); conns.remove(c)
    check("fulfilment stage: dispatched order is 'submitted'", st()["stage"] == "submitted", str(st()))
    for status, stage in [("pending_approval", "not_submitted"), ("ready", "not_submitted"), ("submitting", "in_flight"),
                          ("unknown", "in_flight"), ("provider_accepted", "submitted"), ("cancelled", "closed")]:
        run(f"UPDATE letter_obligations SET status='{status}'")
        check(f"fulfilment stage: {status} -> {stage}", st()["stage"] == stage, str(st()))
    check("no allocation -> no_order", st("cs_none")["stage"] == "no_order")
    # concurrency: two simultaneous syncs on one row both complete and end consistent
    run("UPDATE payments SET status='paid', refunded_pence=0 WHERE stripe_session_id='cs_1'")
    import threading
    errs = []
    def worker(a):
        try: sync("cs_1", 499, a)
        except Exception as e: errs.append(e)
    ts = [threading.Thread(target=worker, args=(a,)) for a in (499, 499, 499, 499)]
    [t.start() for t in ts]; [t.join() for t in ts]
    check("4 simultaneous identical full-refund syncs: no errors, one final state", not errs and pay()[:2] == ("refunded", "499"), str((errs, pay())))

    # ---------------- purge: ONE 72-hour rule, test-mode orders included ----------------
    seed()
    marker_true, marker_false = "[submitted_testmode=true]", "[submitted_testmode=false]"
    def ob(i, status, hours_ago, err, purged=False):
        da = f"NOW() - INTERVAL '{hours_ago} hours'" if hours_ago is not None else "NULL"
        run(f"""INSERT INTO letter_obligations (id,lead_reference,address,applicant_name,approved_content_html,buyer_email,sale_context,status,dispatched_at,last_error,provider_reference,purged_at,idempotency_key)
                VALUES ('00000000-0000-0000-0000-{i:012d}','LEAD{i}','1 Real Road','Jo Bloggs','<p>x</p>','b@x.com','single_purchase','{status}',{da},{('NULL' if err is None else "'" + err + "'")},'PREF{i}',{'NOW()' if purged else 'NULL'},'k{i}')""")
        run(f"INSERT INTO leads (reference,address,applicant_name,summary) VALUES ('LEAD{i}','1 Real Road','Jo Bloggs','s')")
    ob(1, "dispatched", 80, marker_false)                    # real, past 72h              -> purged
    ob(2, "dispatched", 80, None)                            # no marker (older real)       -> purged
    ob(3, "dispatched", 80, marker_true)                     # TEST, past 72h              -> purged (same rule)
    ob(4, "dispatched", 10, marker_true)                     # TEST, recent                -> not yet
    ob(5, "dispatched", 80, marker_true + " " + marker_false)  # conflicting markers        -> purged
    ob(6, "dispatched", 10, marker_false)                    # real, too recent            -> not
    ob(7, "dispatched", 80, marker_false, purged=True)       # already purged              -> not again
    ob(8, "provider_accepted", 80, marker_false)             # accepted is not dispatch    -> not
    ob(9, "dispatched", 71, marker_true)                     # TEST, just under 72h        -> not
    fake_db.get_db_conn.side_effect = lambda: Conn(newconn())
    cnt = purge.count_dispatch_purge_eligible()
    check("preview count: rows 1, 2, 3, 5 (test-mode counted like any other)", int(cnt["obligations"]) == 4, str(cnt))
    res = purge.purge_dispatched_personal_data()
    check("sweep purges exactly those 4", res["obligations_purged"] == 4, str(res))
    after = run("SELECT right(id::text,12), purged_at IS NOT NULL, applicant_name IS NULL, approved_content_html IS NULL FROM letter_obligations ORDER BY id")
    got = {int(r[0]): r[1:] for r in after}
    check("rows 1, 2, 3, 5 cleared; 4, 6, 8, 9 untouched",
          all(got[i] == ("t", "t", "t") for i in (1, 2, 3, 5)) and all(got[i] == ("f", "f", "f") for i in (4, 6, 8, 9)), str(got))
    rec = run("SELECT right(id::text,12), last_error, provider_reference, status FROM letter_obligations WHERE right(id::text,12) = '000000000003'")[0]
    check("the test-mode purge keeps the test/real record (last_error marker), provider reference and status",
          rec[1] == marker_true and rec[2] == "PREF3" and rec[3] == "dispatched", str(rec))
    check("the purged test-mode lead is cleared like a real one", run("SELECT applicant_name IS NULL FROM leads WHERE reference='LEAD3'") == [("t",)])
    check("second sweep is a no-op", purge.purge_dispatched_personal_data()["obligations_purged"] == 0)
finally:
    for c in conns:
        c.close()
    cl.cleanup()
print(f"\n{sum(results)}/{len(results)} checks passed")
sys.exit(0 if all(results) else 1)

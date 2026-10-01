#!/usr/bin/env python3
"""
tests/postgres_concurrency/run_account_request_real_db_test.py

Real disposable PostgreSQL check of the account closure / data deletion REQUEST
storage (database.create_account_request & friends): saved durably, one open
request per account (duplicates are a no-op), constraint, resolve-then-new, and
a failure (table missing) is reported as not saved. No email, no Stripe, nothing deleted.

Exit codes: 0 all passed, 1 a check failed, 2 PostgreSQL unavailable.
"""
from __future__ import annotations

import os
import sys
from unittest.mock import MagicMock

HERE = os.path.dirname(os.path.abspath(__file__))
APP = os.path.abspath(os.path.join(HERE, "..", ".."))
sys.path[:0] = [APP, HERE]
for extra in ("/mnt/user-data/uploads/VECTOR DATA LABS",):
    if os.path.isdir(extra):
        sys.path.append(extra)

import run_concurrency_tests as rct  # noqa: E402
import run_signup_real_db_tests as base  # noqa: E402

DB = base.DB = "treekey_account_request"


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

    cluster = rct.Cluster(pg_bin, rct.PG_OS_USER_DEFAULT)
    results, conns = [], []

    def new_conn():
        c = base.PsqlConn(cluster)
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
        a = "dave@apex-trees.co.uk"

        # table missing -> NOT saved, never a false success
        r = database.create_account_request(a, True, False, "x")
        check("table missing: reported as not saved", r == {"ok": False}, str(r))

        c = new_conn(); cu = c.cursor(); database.init_account_request_schema(cu); c.commit()
        c = new_conn(); cu = c.cursor(); database.init_account_request_schema(cu); c.commit()
        check("schema creation is repeatable", True)  # ran twice above without error

        r1 = database.create_account_request(a, True, False, "  please close  ")
        check("first request saved", r1.get("ok") and not r1["duplicate"] and r1["id"], str(r1))
        cu = new_conn().cursor()
        cu.execute("SELECT account_email, request_close, request_delete, note, status, operator_notified_at FROM account_closure_requests")
        rows = cu.fetchall()
        check("row is durable and correct", rows == [(a, "t", "f", "please close", "open", None)], str(rows))

        r1b = database.create_account_request(a.upper(), True, False, "again")
        check("repeat of the same option is a no-op (same request, nothing added)",
              r1b.get("ok") and r1b["duplicate"] and r1b["added"] == [] and r1b["id"] == r1["id"], str(r1b))
        cu = new_conn().cursor(); cu.execute("SELECT note, amended_at FROM account_closure_requests")
        check("no-op did not change the note or amend", cu.fetchall() == [("please close", None)])
        database.record_account_request_notification(r1["id"], None)   # operator had been emailed about close-only

        r2 = database.create_account_request(a, False, True, "and delete my data too")
        check("later deletion request on a close-only request is MERGED, not swallowed",
              r2.get("ok") and r2["duplicate"] and r2["added"] == ["delete"] and r2["close"] and r2["delete"]
              and r2["id"] == r1["id"], str(r2))
        cu = new_conn().cursor()
        cu.execute("SELECT request_close, request_delete, replace(note, chr(10), ' | '), operator_notified_at, amended_at IS NOT NULL FROM account_closure_requests")
        row = cu.fetchall()
        check("row now holds BOTH intentions, both notes, and needs re-notification",
              row and row[0][0] == "t" and row[0][1] == "t" and "please close" in row[0][2]
              and "[added: delete] and delete my data too" in row[0][2] and row[0][3] is None and row[0][4] == "t", str(row))
        cu = new_conn().cursor(); cu.execute("SELECT count(*) FROM account_closure_requests")
        check("still exactly one row", cu.fetchall()[0][0] == "1")
        r2b = database.create_account_request(a, True, True, "third")
        check("once both are held, a repeat adds nothing", r2b["duplicate"] and r2b["added"] == [], str(r2b))
        database.record_account_request_notification(r1["id"], None)

        r3 = database.create_account_request("other@example.com", False, True, "")
        check("another account gets its own request", r3.get("ok") and not r3["duplicate"] and r3["id"] != r1["id"])
        check("neither option ticked is refused", database.create_account_request("x@example.com", False, False) == {"ok": False})

        try:
            c = new_conn(); cu = c.cursor()
            cu.execute("INSERT INTO account_closure_requests (account_email) VALUES ('z@example.com')")
            c.commit()
            ok = False
        except RuntimeError:
            ok = True
        check("database itself rejects a request with neither option", ok)

        check("open request is retrievable", (database.get_open_account_request(a) or {}).get("id") == r1["id"])
        check("operator can mark it handled", database.resolve_account_request(r1["id"], "done by hand"))
        check("handled request no longer open", database.get_open_account_request(a) is None)
        r4 = database.create_account_request(a, True, True, "again")
        check("after handling, the account may file a new request", r4.get("ok") and not r4["duplicate"] and r4["id"] != r1["id"])

        # operator queue + notification bookkeeping (open rows only; the earlier merged note is multi-line
        # and this test's psql shim cannot parse multi-line fields, which real drivers handle)
        check("notification failure is recorded against the request",
              database.record_account_request_notification(r3["id"], "email send failed"))
        mine = [x for x in database.list_account_requests("open") if x["id"] == r3["id"]][0]
        check("operator queue shows the request with the failed notification", mine["operator_notified_at"] is None
              and int(mine["notify_attempts"]) == 1 and mine["notify_error"] == "email send failed", str(mine))
        check("notification success is recorded", database.record_account_request_notification(r3["id"], None)
              and [x for x in database.list_account_requests("open") if x["id"] == r3["id"]][0]["operator_notified_at"] is not None)
    finally:
        for cn in conns:
            cn.close()
        cluster.cleanup()

    failed = [r for r in results if not r[1]]
    print(f"\n{len(results) - len(failed)}/{len(results)} checks passed.")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""
tests/postgres_concurrency/run_budget_reference_real_db_test.py

Real disposable PostgreSQL check of the website "Confirm mailing budget" duplicate protection
(funding.FundingGate.confirm_budget with a funding_reference, and the unique index added by
funding.init_funding_schema): the column/index are added to an EXISTING table without touching old rows,
the schema step is repeatable, a repeated or differently-cased reference records nothing, and 8 simultaneous
submissions of one reference record exactly one row. No letter worker, no provider, no email.

Exit codes: 0 all passed, 1 a check failed, 2 PostgreSQL unavailable.
"""
from __future__ import annotations

import os
import sys
import threading

HERE = os.path.dirname(os.path.abspath(__file__))
APP = os.path.abspath(os.path.join(HERE, "..", ".."))
sys.path[:0] = [APP, HERE]

import run_concurrency_tests as rct  # noqa: E402
import run_signup_real_db_tests as base  # noqa: E402
import funding  # noqa: E402

DB = base.DB = "treekey_budget_reference"


def main() -> int:
    try:
        pg_bin = rct.find_pg_bin()
    except FileNotFoundError:
        print("PostgreSQL not available: NOT run.")
        return 2
    cluster = rct.Cluster(pg_bin, rct.PG_OS_USER_DEFAULT)
    results, conns = [], []

    def new_conn():
        c = base.PsqlConn(cluster)
        conns.append(c)
        return c

    def check(name, cond, detail=""):
        results.append((name, bool(cond), detail))
        print(f"[{'PASS' if cond else 'FAIL'}] {name}" + (f"  -- {detail}" if detail and not cond else ""))

    def record(ref, pence=2000):
        c = new_conn()
        cu = c.cursor()
        try:
            funding.FundingGate(mode="hold").confirm_budget(cu, amount_pence=pence, confirmed_by="t", note="n",
                                                             funding_reference=ref)
            c.commit()
            return "recorded"
        except funding.DuplicateFundingReference:
            c.rollback()
            return "duplicate"

    def count(where="TRUE"):
        cu = new_conn().cursor()
        cu.execute(f"SELECT COUNT(*) FROM mailing_budget_confirmations WHERE {where}")
        return int(cu.fetchone()[0])

    try:
        cluster.start()
        cluster.createdb(DB)

        # An EXISTING table in the old shape, with an older record already in it.
        c = new_conn(); cu = c.cursor()
        cu.execute("""CREATE TABLE mailing_budget_confirmations (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(), amount_pence INT NOT NULL, confirmed_by TEXT NOT NULL,
            note TEXT, spent_pence INT NOT NULL DEFAULT 0, reserved_pence INT NOT NULL DEFAULT 0,
            active BOOLEAN NOT NULL DEFAULT TRUE, created_at TIMESTAMPTZ DEFAULT NOW())""")
        cu.execute("INSERT INTO mailing_budget_confirmations (amount_pence, confirmed_by, spent_pence, reserved_pence) "
                   "VALUES (1000, 'old', 108, 108)")
        c.commit()
        for _ in range(2):
            c = new_conn(); cu = c.cursor(); funding.init_funding_schema(cu); c.commit()
        check("schema step adds the column to an existing table and is repeatable", True)
        cu = new_conn().cursor()
        cu.execute("SELECT amount_pence, spent_pence, reserved_pence, funding_reference FROM mailing_budget_confirmations")
        check("the older record is untouched", cu.fetchall() == [("1000", "108", "108", None)])

        check("first submission is recorded", record("TOPUP-001", 2000) == "recorded")
        check("exact repeat (double-click/retry) records nothing", record("TOPUP-001", 2000) == "duplicate")
        check("same reference, different case, records nothing", record("topup-001", 2000) == "duplicate")
        check("same reference, different amount, records nothing", record("TOPUP-001", 5000) == "duplicate")
        check("exactly one row holds that reference", count("LOWER(funding_reference) = 'topup-001'") == 1)

        c = new_conn(); cu = c.cursor()
        try:
            funding.FundingGate(mode="hold").confirm_budget(cu, amount_pence=5000, confirmed_by="t", note="n",
                                                             funding_reference="Topup-001")
        except funding.DuplicateFundingReference as d:
            check("duplicate reports the earlier record", d.existing and d.existing["amount_pence"] == 2000, str(d.existing))
        c.rollback()

        check("a different reference is recorded", record("TOPUP-002", 500) == "recorded")
        c = new_conn(); cu = c.cursor()
        funding.FundingGate(mode="hold").confirm_budget(cu, amount_pence=700, confirmed_by="t", note="no ref"); c.commit()
        c = new_conn(); cu = c.cursor()
        funding.FundingGate(mode="hold").confirm_budget(cu, amount_pence=700, confirmed_by="t", note="no ref"); c.commit()
        check("the original no-reference path still records (two such rows allowed)", count("funding_reference IS NULL") == 3)

        cu = new_conn().cursor()
        s = funding.FundingGate(mode="hold").budget_summary(cu)
        check("summary: confirmed 1000+2000+500+700+700, spent 108, reserved 108, available = rest",
              s == {"confirmed_pence": 4900, "spent_pence": 108, "reserved_pence": 108, "available_pence": 4684}, str(s))

        # 8 simultaneous submissions of ONE reference.
        out, barrier = [], threading.Barrier(8)
        def attempt():
            c = new_conn(); cu = c.cursor()
            barrier.wait()
            try:
                funding.FundingGate(mode="hold").confirm_budget(cu, amount_pence=2500, confirmed_by="t", note="race",
                                                                 funding_reference="RACE-1")
                c.commit(); out.append("recorded")
            except funding.DuplicateFundingReference:
                c.rollback(); out.append("duplicate")
        ths = [threading.Thread(target=attempt) for _ in range(8)]
        [t.start() for t in ths]; [t.join(60) for t in ths]
        check("8 simultaneous submissions: exactly 1 recorded, 7 refused", sorted(out) == ["duplicate"] * 7 + ["recorded"], str(out))
        check("and only one row exists for it", count("funding_reference = 'RACE-1'") == 1)

        out2 = []
        def attempt2(i):
            c = new_conn(); cu = c.cursor(); barrier2.wait()
            funding.FundingGate(mode="hold").confirm_budget(cu, amount_pence=100, confirmed_by="t", note="r",
                                                             funding_reference=f"DIFF-{i}")
            c.commit(); out2.append(i)
        barrier2 = threading.Barrier(6)
        ths = [threading.Thread(target=attempt2, args=(i,)) for i in range(6)]
        [t.start() for t in ths]; [t.join(60) for t in ths]
        check("6 simultaneous DIFFERENT references are all recorded (no false blocking)", len(out2) == 6 and count("funding_reference LIKE 'DIFF-%'") == 6)
    finally:
        for cn in conns:
            cn.close()
        cluster.cleanup()

    failed = [r for r in results if not r[1]]
    print(f"\n{len(results) - len(failed)}/{len(results)} checks passed.")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())

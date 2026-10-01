#!/usr/bin/env python3
"""
tests/postgres_concurrency/run_first_offer_real_db_tests.py

Runs the REAL database.first_offer_status / claim_first_offer_order (and the
existing record_order / update_order_fulfillment underneath the payment flow)
against a genuine disposable PostgreSQL server, for the GBP 4.99 first
introduction:

  * eligibility (new account yes; subscriber / earlier purchase / no details no);
  * one redemption per account, per telephone number (any formatting) and per
    business name, including two competing checkouts started at the same moment;
  * a pending checkout holds the offer only for a bounded time; a paid one
    consumes it permanently; failed / refunded release it; refund_failed blocks;
  * ordinary orders are untouched; the startup ALTERs repeat cleanly on a
    payments table that already has rows.

Reuses the psql-backed stand-in and disposable-server helper of
run_signup_real_db_tests.py. Only a private server started (and deleted) by this
script is touched. Exit codes: 0 all passed, 1 a check failed, 2 no PostgreSQL.
"""
from __future__ import annotations

import os
import re
import sys
import threading

HERE = os.path.dirname(os.path.abspath(__file__))
APP = os.path.abspath(os.path.join(HERE, "..", ".."))
sys.path[:0] = [APP, HERE]

import run_concurrency_tests as rct  # noqa: E402
import run_signup_real_db_tests as base  # noqa: E402

DB = base.DB = "treekey_first_offer_test"


def main() -> int:
    try:
        pg_bin = rct.find_pg_bin()
    except FileNotFoundError:
        print("PostgreSQL not available: first-offer real-DB tests NOT run.")
        return 2
    from unittest.mock import MagicMock
    for n in ("psycopg2", "psycopg2.extras", "psycopg2.pool", "psycopg2.errors", "psycopg2.extensions"):
        sys.modules[n] = MagicMock()
    import database
    import letter_content

    src = open(os.path.join(APP, "database.py"), encoding="utf-8").read()
    offer_alters = re.findall(r'"(ALTER TABLE payments ADD COLUMN IF NOT EXISTS offer_[a-z_]+ TEXT;)"', src)

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
        cluster.psql("""
            CREATE EXTENSION IF NOT EXISTS pgcrypto;
            CREATE TABLE payments (id UUID PRIMARY KEY DEFAULT gen_random_uuid(), stripe_session_id TEXT UNIQUE,
                plan TEXT, amount_pence INT, customer_email TEXT, status TEXT DEFAULT 'pending',
                created_at TIMESTAMPTZ DEFAULT NOW(), lead_id TEXT, account_email TEXT, fulfillment_outcome TEXT,
                updated_at TIMESTAMPTZ DEFAULT NOW());
            INSERT INTO payments (stripe_session_id, plan, amount_pence, account_email, status)
                VALUES ('old-order', 'single_lead_small', 1900, 'old@example.com', 'paid');
            CREATE TABLE contractor_subscriptions (id SERIAL PRIMARY KEY, customer_email TEXT, active BOOLEAN DEFAULT TRUE);
        """ + "\n".join(offer_alters), db=DB)
        boot = new_conn()
        cur = boot.cursor()
        letter_content.init_letter_content_schema(cur)
        boot.commit()

        database.SURL = "scratch"
        database.get_db_conn = new_conn

        def q(sql, params=None):
            c = new_conn()
            cu = c.cursor()
            cu.execute(sql, params)
            rows = list(cu.rows)
            c.commit()
            return rows

        def account(email, biz, phone):
            c = new_conn()
            cu = c.cursor()
            letter_content.upsert_contractor_settings(cu, letter_content.ContractorLetterSettings(
                contractor_email=email, business_name=biz, phone=phone))
            c.commit()

        def claim(token, email, lead="L1"):
            return database.claim_first_offer_order(token, email, 499, lead, "single_lead_small")

        check("3 offer columns found in the startup ALTER list", len(offer_alters) == 3, str(offer_alters))

        # eligibility
        account("a@example.com", "Ashcroft Tree Surgery Ltd", "01234 567890")
        check("new account with details is eligible", database.first_offer_status("a@example.com") == {"eligible": True, "reason": None})
        check("account with no details is not eligible", database.first_offer_status("nodetails@example.com")["reason"] == "no_details")
        check("empty email is not eligible", database.first_offer_status("")["eligible"] is False)

        # claim + one per account/phone/business
        r = claim("tok-a1", "a@example.com")
        check("first claim succeeds", r == {"ok": True}, str(r))
        row = q("SELECT status, amount_pence, offer_kind, offer_phone_key, offer_business_key FROM payments WHERE stripe_session_id='tok-a1'")[0]
        check("claim records a pending £4.99 first_introduction order with identity keys",
              row == ("pending", "499", "first_introduction", "1234567890", "ashcrofttreesurgery"), str(row))
        check("same account, second attempt while pending -> offer_in_progress", claim("tok-a2", "a@example.com")["reason"] == "offer_in_progress")
        check("eligibility now reads offer_in_progress", database.first_offer_status("a@example.com")["reason"] == "offer_in_progress")
        account("b@example.com", "Different Name", "+44 1234 567890")
        check("second account, same phone in international format, is blocked", claim("tok-b1", "b@example.com")["ok"] is False)
        account("c@example.com", "ashcroft tree surgery", "07700 900123")
        check("second account, same business name (different case, no 'Ltd'), is blocked", claim("tok-c1", "c@example.com")["ok"] is False)
        account("d@example.com", "Other Trees", "01900 111222")
        check("a genuinely different business can take its own first offer", claim("tok-d1", "d@example.com")["ok"] is True)

        # paid consumes permanently
        database.update_order_fulfillment("tok-a1", "paid", "fulfilled")
        check("paid offer -> offer_used for the account", database.first_offer_status("a@example.com")["reason"] == "offer_used")
        check("paid offer -> still blocks the same phone under another account", claim("tok-b2", "b@example.com")["reason"] == "offer_used")
        q("UPDATE payments SET created_at = NOW() - INTERVAL '10 days' WHERE stripe_session_id='tok-a1'")
        check("a long-ago paid offer still blocks (never expires)", database.first_offer_status("a@example.com")["reason"] == "offer_used")

        # pending expiry, failed and refunded release
        q("UPDATE payments SET created_at = NOW() - INTERVAL '2 hours' WHERE stripe_session_id='tok-d1'")
        check("an abandoned pending checkout stops holding the offer after the hold window",
              database.first_offer_status("d@example.com") == {"eligible": True, "reason": None})
        check("...and the same business can start again", claim("tok-d2", "d@example.com")["ok"] is True)
        database.update_order_fulfillment("tok-d2", "failed", "stripe_session_creation_failed")
        check("a failed order releases the offer", database.first_offer_status("d@example.com")["eligible"] is True)
        claim("tok-d3", "d@example.com")
        database.update_order_fulfillment("tok-d3", "refunded", "reservation_lost_auto_refunded")
        check("a refunded (reservation lost) order releases the offer", database.first_offer_status("d@example.com")["eligible"] is True)
        claim("tok-d4", "d@example.com")
        database.update_order_fulfillment("tok-d4", "refund_failed", "reservation_lost_refund_failed")
        check("refund_failed keeps the offer blocked (money taken, needs a human)", database.first_offer_status("d@example.com")["reason"] == "offer_used")

        # other exclusions
        account("e@example.com", "Sub Co", "01111 222333")
        q("INSERT INTO contractor_subscriptions (customer_email, active) VALUES ('e@example.com', TRUE)")
        check("active subscriber is not eligible", database.first_offer_status("e@example.com")["reason"] == "subscriber")
        check("subscriber claim refused", claim("tok-e1", "e@example.com")["reason"] == "subscriber")
        account("f@example.com", "Prior Buyer", "01222 333444")
        database.record_order("ord-f", "f@example.com", 1900, lead_id="L9", plan="single_lead_small", status="pending")
        database.update_order_fulfillment("ord-f", "paid", "fulfilled")
        check("an earlier ordinary paid purchase is not a first purchase", database.first_offer_status("f@example.com")["reason"] == "prior_purchase")
        ordinary = q("SELECT offer_kind, offer_phone_key, offer_business_key FROM payments WHERE stripe_session_id='ord-f'")[0]
        check("ordinary orders never carry offer markers", ordinary == (None, None, None), str(ordinary))

        # concurrency
        account("g1@example.com", "Race Trees", "01333 444555")
        out = []
        ts = [threading.Thread(target=lambda i=i: out.append(claim(f"race-{i}", "g1@example.com", f"L{i}")["ok"])) for i in range(6)]
        [t.start() for t in ts]; [t.join() for t in ts]
        check("6 simultaneous checkouts, one account: exactly one wins", out.count(True) == 1, str(out))
        account("h1@example.com", "Twin One", "01444 555666")
        account("h2@example.com", "Twin Two", "+44 1444 555666")
        out = []
        ts = [threading.Thread(target=lambda e=e: out.append(claim(f"twin-{e}", e)["ok"])) for e in ("h1@example.com", "h2@example.com")]
        [t.start() for t in ts]; [t.join() for t in ts]
        check("2 accounts, same phone, simultaneous: exactly one wins", out.count(True) == 1, str(out))
        n = q("SELECT COUNT(*) FROM payments WHERE offer_kind='first_introduction' AND status='pending' AND account_email IN ('h1@example.com','h2@example.com')")[0][0]
        check("...and only one pending offer row exists for the pair", n == "1", n)

        # startup schema is repeatable and keeps existing rows
        cluster.createdb(DB + "_old")
        cluster.psql("""
            CREATE TABLE payments (id SERIAL PRIMARY KEY, stripe_session_id TEXT UNIQUE, plan TEXT, amount_pence INT,
                customer_email TEXT, status TEXT DEFAULT 'pending', created_at TIMESTAMPTZ DEFAULT NOW(),
                lead_id TEXT, account_email TEXT, fulfillment_outcome TEXT, updated_at TIMESTAMPTZ DEFAULT NOW());
            INSERT INTO payments (stripe_session_id, plan, amount_pence, account_email, status) VALUES ('keep','single_lead_small',1900,'k@example.com','paid');
        """, db=DB + "_old")
        for _ in range(2):
            cluster.psql("\n".join(offer_alters), db=DB + "_old")
        out = cluster.psql("SELECT stripe_session_id, status, offer_kind IS NULL FROM payments", db=DB + "_old")
        check("startup ALTERs repeat cleanly and keep existing orders", re.search(r"keep\|paid\|t", out.replace(" ", "")) is not None, out)
    finally:
        for c in conns:
            c.close()
        cluster.cleanup()

    failed = [r for r in results if not r[1]]
    print(f"\n{len(results) - len(failed)}/{len(results)} checks passed.")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())

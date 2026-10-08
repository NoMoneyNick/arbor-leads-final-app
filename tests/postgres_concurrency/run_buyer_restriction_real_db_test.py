#!/usr/bin/env python3
"""
tests/postgres_concurrency/run_buyer_restriction_real_db_test.py

Real-PostgreSQL test (8 Oct 2026) of the buyer restriction inside database.reserve_lead_for_checkout: a lead tagged
'only_buyer:<sha256 of one account's email>' can be reserved ONLY by that account, at every point (on sale, mid-
checkout, after a lapsed reservation, after the sweep), by id or by reference, even with other accounts racing for it.
Any malformed or conflicting restriction tag refuses EVERYONE (fail-closed). A caller with no usable identity never
reserves a restricted lead. Leads with no restriction behave exactly as before.

Exit codes: 0 all passed, 1 a check failed, 2 PostgreSQL unavailable.
"""
from __future__ import annotations

import hashlib
import os
import re
import sys
import threading
from unittest.mock import MagicMock

HERE = os.path.dirname(os.path.abspath(__file__))
APP = os.path.abspath(os.path.join(HERE, "..", ".."))
sys.path[:0] = [APP, HERE]

import run_concurrency_tests as rct  # noqa: E402
import run_signup_real_db_tests as base  # noqa: E402
from run_reconciliation_real_db_test import ConvConn  # noqa: E402

DB = base.DB = "treekey_buyer_restriction_test"
OWNER = "designated.buyer@treekeytests.co.uk"
OTHER = "someone.else@treekeytests.co.uk"
NEWCOMER = "signed.up.meanwhile@treekeytests.co.uk"


def tag_for(email: str) -> str:
    return "only_buyer:" + hashlib.sha256(email.strip().lower().encode()).hexdigest()


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
    import database

    cluster = rct.Cluster(pg_bin, rct.PG_OS_USER_DEFAULT)
    results, conns = [], []

    def new_conn():
        k = ConvConn(cluster)
        conns.append(k)
        return k

    def check(name, cond, detail=""):
        results.append((name, bool(cond)))
        print(f"[{'PASS' if cond else 'FAIL'}] {name}" + (f"  -- {detail}" if detail and not cond else ""))

    def q(sql, params=None):
        cn = new_conn()
        cu = cn.cursor()
        cu.execute(sql, params)
        rows = list(cu.rows)
        cn.commit()
        cn.close()
        return rows

    counter = [0]

    def lead(tags=None, status="new", reserved_at_sql="NULL"):
        """tags: None -> NULL column; list -> exactly those strings (test-controlled values)."""
        counter[0] += 1
        ref = f"BR-{counter[0]:04d}"
        if tags is None:
            tags_sql = "NULL"
        else:
            tags_sql = "ARRAY[" + ",".join("'" + t.replace("'", "''") + "'" for t in tags) + "]::text[]" if tags else "'{}'::text[]"
        row = q(f"INSERT INTO leads (reference, address, summary, status, tags, reserved_at) VALUES "
                f"('{ref}', '1 Test Road, Harrogate, HG1 2AB', 'tree work', '{status}', {tags_sql}, {reserved_at_sql}) "
                f"RETURNING id::text")
        return ref, row[0][0]

    def state(ref):
        r = q("SELECT status, COALESCE(reserved_by_email,''), COALESCE(reserved_session_id,'') FROM leads WHERE reference = %s", (ref,))[0]
        return r

    tok = lambda: os.urandom(8).hex()  # noqa: E731

    try:
        cluster.start()
        cluster.createdb(DB)
        base.DB = DB
        src = open(os.path.join(APP, "database.py"), encoding="utf-8").read()
        leads_create = re.search(r"CREATE TABLE IF NOT EXISTS leads \(.*?\n\s*\);", src, re.S).group(0)
        alters = re.findall(r'"(ALTER TABLE leads ADD COLUMN IF NOT EXISTS [^"]+;)"', src)
        extra = "\n".join(re.search(r"CREATE TABLE IF NOT EXISTS %s \(.*?\n\s*\);" % t, src, re.S).group(0)
                          for t in ("payments", "contractor_subscriptions"))
        cluster.psql("CREATE EXTENSION IF NOT EXISTS pgcrypto;\n" + leads_create + "\n" + "\n".join(alters) + "\n" + extra, db=DB)
        database.SURL = "scratch"
        database.get_db_conn = new_conn

        reserve = database.reserve_lead_for_checkout

        # ---------------------------------------------------------------- the tag helper
        check("tag helper: exact 'only_buyer:' + sha256 of the lower-cased, stripped email",
              database.buyer_restriction_tag("  Designated.Buyer@TreeKeyTests.co.uk ") == tag_for(OWNER))
        check("tag helper: no usable identity -> None (nothing, 'anonymous', not an email)",
              all(database.buyer_restriction_tag(x) is None for x in (None, "", "   ", "anonymous", "not-an-email")))

        # ---------------------------------------------------------------- 1. unrestricted leads are unchanged
        for label, tags in (("NULL tags", None), ("empty tags", []), ("ordinary tags", ["vertical:tree", "job:felling"])):
            ref, _ = lead(tags)
            check(f"unrestricted lead ({label}): any account can still reserve it",
                  reserve(ref, OTHER, tok()) and state(ref)[0] == "reserved" and state(ref)[1] == OTHER)
        ref, _ = lead([])
        check("unrestricted lead: even the 'anonymous' fallback still reserves it (existing behaviour)",
              reserve(ref, "anonymous", tok()))

        # ---------------------------------------------------------------- 2. a restricted lead on sale
        ref, lid = lead([tag_for(OWNER)])
        t_other = tok()
        check("restricted lead on sale: another account is refused, nothing changes",
              not reserve(lid, OTHER, t_other) and state(ref) == ("new", "", ""))
        check("restricted lead: an account that signed up meanwhile is refused",
              not reserve(lid, NEWCOMER, tok()) and state(ref)[0] == "new")
        check("restricted lead: refused by REFERENCE too",
              not reserve(ref, OTHER, tok()) and state(ref)[0] == "new")
        check("restricted lead: no identity ('anonymous', empty, not an email) is refused",
              not any(reserve(lid, x, tok()) for x in ("anonymous", "", "   ", "not-an-email", None)) and state(ref)[0] == "new")
        check("restricted lead: guessing the owner's email in another case/spacing is the SAME identity (accepted)",
              reserve(lid, "  DESIGNATED.buyer@treekeytests.co.uk ", t_owner := tok()) and state(ref)[0] == "reserved")

        # ---------------------------------------------------------------- 3. throughout the owner's checkout
        check("mid-checkout: another account is refused while the owner's reservation is live",
              not reserve(lid, OTHER, tok()) and state(ref)[2] == t_owner)
        q("UPDATE leads SET reserved_at = NOW() - INTERVAL '40 minutes' WHERE reference = %s", (ref,))
        check("lapsed reservation (an abandoned checkout): another account is STILL refused, status untouched",
              not reserve(lid, OTHER, tok()) and not reserve(ref, NEWCOMER, tok()) and state(ref)[2] == t_owner)
        t2 = tok()
        check("lapsed reservation: the owner can start a fresh checkout",
              reserve(lid, OWNER, t2) and state(ref)[2] == t2)
        q("UPDATE leads SET reserved_at = NOW() - INTERVAL '40 minutes' WHERE reference = %s", (ref,))
        released = database.release_expired_reservations()
        check("the expiry sweep returns the lead to 'new' (existing behaviour) - the restriction tag survives it",
              released >= 1 and state(ref)[0] == "new"
              and q("SELECT %s = ANY(tags) FROM leads WHERE reference = %s", (tag_for(OWNER), ref))[0][0] in (True, "t"))
        check("after the sweep: another account is still refused; the owner is accepted",
              not reserve(lid, OTHER, tok()) and reserve(lid, OWNER, tok()))

        # ---------------------------------------------------------------- 4. held lead (existing hold state) and sold lead
        ref, lid = lead([tag_for(OWNER)], status="reserved", reserved_at_sql="NOW() + INTERVAL '90 days'")
        check("held lead (far-future reservation): nobody reserves it, not even the owner (existing behaviour)",
              not reserve(lid, OWNER, tok()) and not reserve(lid, OTHER, tok()) and state(ref)[0] == "reserved")
        ref, lid = lead([tag_for(OWNER)], status="claimed")
        check("sold lead: refused for everyone, owner included (existing behaviour)",
              not reserve(lid, OWNER, tok()) and state(ref)[0] == "claimed")

        # ---------------------------------------------------------------- 5. malformed / conflicting tags refuse EVERYONE
        good = tag_for(OWNER)
        bad_cases = {
            "upper-case marker": good.replace("only_buyer", "ONLY_BUYER"),
            "mixed-case marker": good.replace("only_buyer", "Only_Buyer"),
            "truncated hash (63 chars)": good[:-1],
            "extra character on the hash": good + "0",
            "upper-case hash": good.upper().replace("ONLY_BUYER:", "only_buyer:"),
            "marker with no hash": "only_buyer:",
            "bare marker": "only_buyer",
            "marker, wrong separator": good.replace(":", "="),
            "leading space": " " + good,
            "trailing space": good + " ",
            "prefix text before the marker": "x" + good,
            "a different account's tag, mangled": tag_for(OTHER)[:-2],
        }
        for label, t in bad_cases.items():
            ref, lid = lead([t])
            res = [reserve(lid, e, tok()) for e in (OWNER, OTHER, NEWCOMER, "anonymous")]
            check(f"malformed tag ({label}): refuses EVERYONE and leaves the lead untouched",
                  not any(res) and state(ref) == ("new", "", ""), str(res))
        ref, lid = lead(["vertical:tree", "only_buyer:zzz", good])
        check("malformed tag next to a valid one: the lead is not unlocked for the owner",
              not reserve(lid, OWNER, tok()) and state(ref)[0] == "new")
        ref, lid = lead([tag_for(OWNER), tag_for(OTHER)])
        check("two different valid restriction tags conflict: refuses both accounts",
              not reserve(lid, OWNER, tok()) and not reserve(lid, OTHER, tok()) and state(ref)[0] == "new")
        ref, lid = lead([tag_for(OWNER), tag_for(OWNER), "vertical:tree"])
        check("the same valid tag twice is still one restriction: the owner is accepted, others refused",
              not reserve(lid, OTHER, tok()) and reserve(lid, OWNER, tok()))

        # ---------------------------------------------------------------- 6. a race: one owner attempt among many others
        ref, lid = lead([tag_for(OWNER)])
        outcomes = {}
        barrier = threading.Barrier(8)

        def attempt(email, key):
            barrier.wait()
            outcomes[key] = reserve(lid, email, tok())

        ths = [threading.Thread(target=attempt, args=(OWNER if i == 0 else f"racer{i}@treekeytests.co.uk", i)) for i in range(8)]
        [t.start() for t in ths]
        [t.join() for t in ths]
        check("race: eight accounts reserve at once - exactly the owner wins, the seven others are refused",
              outcomes[0] is True and not any(outcomes[i] for i in range(1, 8)) and state(ref)[1] == OWNER, str(outcomes))

        # ---------------------------------------------------------------- 7. through the real checkout function
        import payments
        from unittest.mock import patch
        payments.stripe.api_key = "placeholder"
        ref, lid = lead([tag_for(OWNER)])
        q("UPDATE leads SET lead_score = 'medium', lead_price = 29, discovered_at = NOW() - INTERVAL '24 hours', "
          "council_source = 'test_council', lead_source_type = 'council_planning', planning_status = 'pending', vertical = 'tree' "
          "WHERE reference = %s", (ref,))
        created = []

        class _Sess:
            id = "cs_test_1"
            url = "https://checkout.stripe.example/session"

        def _fake_create(**kw):
            created.append(kw)
            return _Sess()

        with patch.object(payments.stripe.checkout.Session, "create", side_effect=_fake_create):
            r_other = payments.create_checkout_session("single_lead_medium", "GB", lid, account_email=OTHER)
            r_none = payments.create_checkout_session("single_lead_medium", "GB", lid, account_email=None)
            check("real checkout function: another account / no account gets no payment session and Stripe is never called",
                  r_other is None and r_none is None and not created and state(ref)[0] == "new", f"{r_other} {r_none} {created}")
            r_owner = payments.create_checkout_session("single_lead_medium", "GB", lid, account_email=OWNER)
        check("real checkout function: the designated account gets a payment session and the lead is reserved for it",
              bool(r_owner) and len(created) == 1 and state(ref)[0] == "reserved" and state(ref)[1] == OWNER, f"{r_owner} {state(ref)}")
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

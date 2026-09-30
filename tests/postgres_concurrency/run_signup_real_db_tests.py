#!/usr/bin/env python3
"""
tests/postgres_concurrency/run_signup_real_db_tests.py

Runs the REAL database.create_magic_auth_token / verify_magic_auth_token /
email_has_existing_account code (and letter_content.insert_initial_contractor_
settings underneath them) against a genuine, disposable local PostgreSQL 16
server, for the integrated first-time signup:

  * pending details are applied exactly once, on verification;
  * expired / reused tokens apply nothing and clear the pending details;
  * an existing account -- with OR without letter settings -- is never
    overwritten;
  * two competing verifications for the same new email apply at most once.

Like run_concurrency_tests.py (whose disposable-server helper it reuses), it
is a separate script, not part of `unittest discover`: this sandbox has no
psycopg2, so a tiny psql-backed stand-in for the few psycopg2 calls those
functions use (execute / fetchone / rowcount / commit / rollback) drives a
persistent psql session per connection. The SQL that runs is the production
SQL, unmodified. Only a private server started (and deleted) by this script
is ever touched; no environment variable is read.

Exit codes: 0 all passed, 1 a scenario failed, 2 PostgreSQL unavailable.
"""
from __future__ import annotations

import inspect
import json
import os
import re
import shutil
import subprocess
import sys
import threading

HERE = os.path.dirname(os.path.abspath(__file__))
APP = os.path.abspath(os.path.join(HERE, "..", ".."))
sys.path[:0] = [APP, HERE]

import run_concurrency_tests as rct  # noqa: E402  (disposable-server helper)

DB = "treekey_signup_test"
SEP = "\x1f"
NULL = "<<NULL>>"
END = "<<END>>"
TAG = re.compile(r"^(INSERT \d+ \d+|UPDATE \d+|DELETE \d+|SELECT \d+|BEGIN|COMMIT|ROLLBACK|SAVEPOINT|RELEASE|"
                 r"CREATE [A-Z ]+|ALTER TABLE|DO|SET)$")


def lit(v) -> str:
    if v is None:
        return "NULL"
    if isinstance(v, bool):
        return "TRUE" if v else "FALSE"
    if isinstance(v, (int, float)):
        return str(v)
    return "'" + str(v).replace("'", "''") + "'"


class PsqlCursor:
    def __init__(self, conn):
        self.c = conn
        self.rows, self.rowcount, self.i = [], -1, 0

    def execute(self, sql, params=None):
        sql = sql.strip()
        if params is not None:
            parts = sql.replace("%%", "\x00").split("%s")
            assert len(parts) == len(params) + 1, (sql, params)
            sql = "".join(p + (lit(params[k]) if k < len(params) else "") for k, p in enumerate(parts))
        sql = sql.replace("\x00", "%")
        self.rows, self.rowcount = self.c.run(sql)
        self.i = 0

    def fetchone(self):
        if self.i < len(self.rows):
            self.i += 1
            return self.rows[self.i - 1]
        return None

    def close(self):
        pass


class PsqlConn:
    def __init__(self, cluster):
        psql = "/usr/bin/psql" if os.path.exists("/usr/bin/psql") else os.path.join(cluster.pg_bin, "psql")
        self.p = subprocess.Popen(
            [psql, "-h", cluster.sock_dir, "-U", cluster.pg_os_user, "-d", DB, "-X", "-A", "-t", "-F", SEP,
             "-P", f"null={NULL}"],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1)
        self.in_tx = False

    def run(self, sql):
        upper = sql.lstrip().upper()
        if upper.rstrip(";") in ("COMMIT", "ROLLBACK"):
            self.in_tx = False
        elif not self.in_tx and not upper.startswith("BEGIN"):
            self._send("BEGIN")
            self.in_tx = True
        return self._send(sql)

    def _send(self, sql):
        if not sql.rstrip().endswith(";"):
            sql += ";"
        self.p.stdin.write(sql + "\n\\echo " + END + "\n")
        self.p.stdin.flush()
        lines = []
        while True:
            line = self.p.stdout.readline()
            if line == "":
                raise RuntimeError("psql exited")
            line = line.rstrip("\n")
            if line == END:
                break
            lines.append(line)
        errs = [l for l in lines if "ERROR:" in l]
        if errs:
            raise RuntimeError(errs[0])
        rowcount, rows = -1, lines
        if lines and TAG.match(lines[-1]):
            tag = lines[-1]
            rows = lines[:-1]
            m = re.search(r"(\d+)$", tag)
            rowcount = int(m.group(1)) if m else -1
        parsed = [tuple(None if f == NULL else f for f in r.split(SEP)) for r in rows]
        return parsed, rowcount

    def cursor(self):
        return PsqlCursor(self)

    def commit(self):
        if self.in_tx:
            self.run("COMMIT")

    def rollback(self):
        if self.in_tx:
            self.run("ROLLBACK")

    def close(self):
        try:
            self.p.stdin.close()
            self.p.wait(timeout=5)
        except Exception:
            self.p.kill()


def main() -> int:
    try:
        pg_bin = rct.find_pg_bin()
    except FileNotFoundError:
        print("PostgreSQL not available: signup real-DB tests NOT run.")
        return 2

    from unittest.mock import MagicMock
    for n in ("psycopg2", "psycopg2.extras", "psycopg2.pool", "psycopg2.errors", "psycopg2.extensions"):
        sys.modules[n] = MagicMock()  # only so the REAL database.py imports; it never connects through this
    import database
    import letter_content

    cluster = rct.Cluster(pg_bin, rct.PG_OS_USER_DEFAULT)
    results = []
    conns = []

    def new_conn():
        c = PsqlConn(cluster)
        conns.append(c)
        return c

    try:
        cluster.start()
        cluster.createdb(DB)
        cluster.psql("""
            CREATE EXTENSION IF NOT EXISTS pgcrypto;
            CREATE TABLE contractor_auth_tokens (
                id UUID PRIMARY KEY DEFAULT gen_random_uuid(), customer_email TEXT NOT NULL,
                token TEXT UNIQUE NOT NULL, otp_code TEXT NOT NULL, created_at TIMESTAMPTZ DEFAULT NOW(),
                expires_at TIMESTAMPTZ DEFAULT (NOW() + INTERVAL '15 minutes'), used BOOLEAN DEFAULT FALSE);
            ALTER TABLE contractor_auth_tokens ADD COLUMN IF NOT EXISTS pending_signup TEXT;
            CREATE TABLE contractor_subscriptions (id SERIAL PRIMARY KEY, customer_email TEXT, active BOOLEAN DEFAULT TRUE);
            CREATE TABLE limbo_accounts (id UUID PRIMARY KEY DEFAULT gen_random_uuid(), email TEXT UNIQUE NOT NULL,
                center_outcode TEXT NOT NULL);
            CREATE TABLE lead_dispatches (id SERIAL PRIMARY KEY, contractor_email TEXT);
        """, db=DB)
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
            r = cu.fetchall_ = list(cu.rows)
            c.commit()
            return r

        def settings_row(email):
            r = q("SELECT business_name, phone, business_intro, approved, responsible_contact_name, "
                  "terms_accepted_at IS NOT NULL, contact_first_name FROM contractor_letter_settings "
                  "WHERE contractor_email=%s", (email,))
            return r[0] if r else None

        def pending_of(email):
            return [r[0] for r in q("SELECT pending_signup FROM contractor_auth_tokens WHERE customer_email=%s", (email,))]

        def payload(biz="Ashcroft Tree Surgery", intro="", first=""):
            return {"responsible_contact_name": "Dave Smith", "terms_accepted_at": "2026-09-30T10:00:00+00:00",
                    "letter": {"business_name": biz, "phone": "01234 567890", "business_intro": intro,
                               "contact_first_name": first, "template_key": letter_content.DEFAULT_TEMPLATE_KEY}}

        def check(name, cond, detail=""):
            results.append((name, bool(cond), detail))
            print(f"[{'PASS' if cond else 'FAIL'}] {name}" + (f"  -- {detail}" if detail and not cond else ""))

        # 1 new signup applies on verification (link), nothing before
        e = "new1@example.com"
        a = database.create_magic_auth_token(e, pending_signup=payload(intro="We are careful.", first="Dave"))
        check("pending payload never returned to caller", set(a) == {"token", "otp", "email"})
        check("nothing saved before verification", settings_row(e) is None)
        check("details held on the verification record", pending_of(e)[0] is not None)
        check("verify returns the email", database.verify_magic_auth_token(token=a["token"]) == e)
        row = settings_row(e)
        check("settings applied on verification (unapproved, identity + terms recorded)",
              row == ("Ashcroft Tree Surgery", "01234 567890", "We are careful.", "f", "Dave Smith", "t", "Dave"), str(row))
        check("pending cleared after use", pending_of(e) == [None], str(pending_of(e)))
        # 2 reuse
        check("reused link rejected", database.verify_magic_auth_token(token=a["token"]) is None)
        check("settings unchanged after reuse", settings_row(e) == row)

        # 3 OTP path
        e = "new2@example.com"
        a = database.create_magic_auth_token(e, pending_signup=payload(biz="Otp Trees"))
        check("OTP verification applies details", database.verify_magic_auth_token(otp=a["otp"], email=e) == e
              and settings_row(e)[0] == "Otp Trees")
        check("reused OTP rejected", database.verify_magic_auth_token(otp=a["otp"], email=e) is None)

        # 4 expired
        e = "expired@example.com"
        a = database.create_magic_auth_token(e, pending_signup=payload())
        q("UPDATE contractor_auth_tokens SET expires_at = NOW() - INTERVAL '1 minute' WHERE customer_email=%s", (e,))
        check("expired link rejected", database.verify_magic_auth_token(token=a["token"]) is None)
        check("expired: nothing saved", settings_row(e) is None)
        check("expired: pending details cleared", pending_of(e) == [None], str(pending_of(e)))
        e2 = "expired2@example.com"
        a2 = database.create_magic_auth_token(e2, pending_signup=payload())
        q("UPDATE contractor_auth_tokens SET expires_at = NOW() - INTERVAL '1 minute' WHERE customer_email=%s", (e2,))
        database.create_magic_auth_token("someone.else@example.com")
        check("expired pending also cleared by the next token request", pending_of(e2) == [None], str(pending_of(e2)))

        # 5..9 existing accounts are never overwritten
        def existing_case(label, email, setup_sql, has_settings):
            q(setup_sql, (email,))
            before = settings_row(email)
            a = database.create_magic_auth_token(email, pending_signup=payload(biz="INTRUDER LTD"))
            ok = database.verify_magic_auth_token(token=a["token"]) == email
            after = settings_row(email)
            check(f"existing account ({label}): login still works", ok)
            check(f"existing account ({label}): details not changed", after == before and
                  (after is None or after[0] != "INTRUDER LTD"), f"{before} -> {after}")
            check(f"existing account ({label}): pending cleared", pending_of(email)[-1] is None)

        e = "has-settings@example.com"
        c = new_conn(); cu = c.cursor()
        letter_content.upsert_contractor_settings(cu, letter_content.ContractorLetterSettings(
            contractor_email=e, business_name="Original Ltd", phone="0700"))
        c.commit()
        existing_case("with letter settings", e, "SELECT %s", True)
        existing_case("subscriber, no settings", "sub@example.com", "INSERT INTO contractor_subscriptions (customer_email) VALUES (%s)", False)
        existing_case("free/limbo account, no settings", "limbo@example.com", "INSERT INTO limbo_accounts (email, center_outcode) VALUES (%s, 'NG22')", False)
        existing_case("lead dispatch history, no settings", "disp@example.com", "INSERT INTO lead_dispatches (contractor_email) VALUES (%s)", False)
        e = "priorlogin@example.com"
        old = database.create_magic_auth_token(e)
        database.verify_magic_auth_token(token=old["token"])
        existing_case("earlier verified login, no settings", e, "SELECT %s", False)
        check("public existing-account check: new=False, existing=True",
              database.email_has_existing_account("brand-new@example.com") is False
              and database.email_has_existing_account("sub@example.com") is True)

        # 10 two competing tokens for the same new email, verified concurrently
        e = "race@example.com"
        t1 = database.create_magic_auth_token(e, pending_signup=payload(biz="First Co"))
        t2 = database.create_magic_auth_token(e, pending_signup=payload(biz="Second Co"))
        out = []
        threads = [threading.Thread(target=lambda t=t: out.append(database.verify_magic_auth_token(token=t["token"])))
                   for t in (t1, t2)]
        [t.start() for t in threads]; [t.join() for t in threads]
        n = q("SELECT COUNT(*) FROM contractor_letter_settings WHERE contractor_email=%s", (e,))[0][0]
        check("competing verifications: both log in, exactly one settings row", out == [e, e] and n == "1", f"{out} {n}")
        check("competing verifications: one payload won, none overwritten later",
              settings_row(e)[0] in ("First Co", "Second Co"))

        # 11 invalid stored details never block login
        e = "badpending@example.com"
        a = database.create_magic_auth_token(e, pending_signup=payload(biz=""))
        check("unappliable details do not block login or save anything",
              database.verify_magic_auth_token(token=a["token"]) == e and settings_row(e) is None
              and pending_of(e) == [None])

        # 12 ordinary login token unchanged
        e = "plain@example.com"
        a = database.create_magic_auth_token(e)
        check("ordinary login (no pending) unchanged", database.verify_magic_auth_token(token=a["token"]) == e
              and settings_row(e) is None)

        # 13 an ordinary settings save never touches signup identity / terms
        e = "identity@example.com"
        a = database.create_magic_auth_token(e, pending_signup=payload(biz="Identity Co"))
        database.verify_magic_auth_token(token=a["token"])
        c = new_conn(); cu = c.cursor()
        before = letter_content.get_account_identity(cu, e)
        letter_content.upsert_contractor_settings(cu, letter_content.ContractorLetterSettings(
            contractor_email=e, business_name="Identity Co (edited)", phone="0999", business_intro="New words"))
        c.commit()
        after = letter_content.get_account_identity(cu, e)
        check("ordinary settings save keeps the signup terms record and responsible contact",
              before["terms_accepted_at"] is not None and before == after
              and before["responsible_contact_name"] == "Dave Smith", f"{before} -> {after}")
        check("ordinary settings save still resets approval (material change)",
              settings_row(e)[3] == "f")
        # 14 responsible contact can be edited later without touching terms or letter content
        c = new_conn(); cu = c.cursor()
        ok = letter_content.update_responsible_contact_name(cu, e, "Davina Smith")
        blank = letter_content.update_responsible_contact_name(cu, e, "   ")
        missing = letter_content.update_responsible_contact_name(cu, "nobody@example.com", "X")
        c.commit()
        ident = letter_content.get_account_identity(cu, e)
        check("responsible contact name is editable; blank/unknown rejected; terms record untouched",
              ok and not blank and not missing and ident["responsible_contact_name"] == "Davina Smith"
              and ident["terms_accepted_at"] == before["terms_accepted_at"]
              and settings_row(e)[0] == "Identity Co (edited)", str(ident))
        # 15 insert-only helper never overwrites
        c = new_conn(); cu = c.cursor()
        again = letter_content.insert_initial_contractor_settings(cu, letter_content.ContractorLetterSettings(
            contractor_email=e, business_name="OVERWRITE", phone="1"), "Other Person", "2026-01-01T00:00:00+00:00")
        c.commit()
        check("insert-only helper reports False and changes nothing on an existing row",
              again is False and settings_row(e)[0] == "Identity Co (edited)"
              and letter_content.get_account_identity(cu, e)["responsible_contact_name"] == "Davina Smith")

        # 16 startup schema statements are repeatable and preserve existing rows
        cluster.createdb(DB + "_old")
        cluster.psql("""
            CREATE EXTENSION IF NOT EXISTS pgcrypto;
            CREATE TABLE contractor_auth_tokens (
                id UUID PRIMARY KEY DEFAULT gen_random_uuid(), customer_email TEXT NOT NULL,
                token TEXT UNIQUE NOT NULL, otp_code TEXT NOT NULL, created_at TIMESTAMPTZ DEFAULT NOW(),
                expires_at TIMESTAMPTZ DEFAULT (NOW() + INTERVAL '15 minutes'), used BOOLEAN DEFAULT FALSE);
            INSERT INTO contractor_auth_tokens (customer_email, token, otp_code) VALUES ('old@example.com','t1','111111');
        """, db=DB + "_old")
        src = open(os.path.join(APP, "database.py"), encoding="utf-8").read()
        alter = re.search(r'"(ALTER TABLE contractor_auth_tokens ADD COLUMN IF NOT EXISTS pending_signup TEXT;)"', src).group(1)
        # a pre-signup-era letter settings table (without the two new columns) holding a real row
        cluster.psql("""
            CREATE TABLE contractor_letter_settings (
                id UUID PRIMARY KEY DEFAULT gen_random_uuid(), contractor_email TEXT NOT NULL UNIQUE,
                business_name TEXT NOT NULL, phone TEXT NOT NULL, service_area_note TEXT, insurance_note TEXT,
                qualifications_note TEXT, template_version INT NOT NULL DEFAULT 1,
                approved BOOLEAN NOT NULL DEFAULT FALSE, approved_fingerprint TEXT, approved_at TIMESTAMPTZ,
                created_at TIMESTAMPTZ DEFAULT NOW(), updated_at TIMESTAMPTZ DEFAULT NOW());
            INSERT INTO contractor_letter_settings (contractor_email, business_name, phone, approved)
                VALUES ('old@example.com','Old Co','0100', TRUE);
        """, db=DB + "_old")
        import inspect
        ddl = re.search(r'cur\.execute\("""(.*?)"""\)', inspect.getsource(letter_content.init_letter_content_schema), re.S).group(1)
        for _ in range(2):   # run twice: repeatable
            cluster.psql(ddl + "\n" + alter, db=DB + "_old")
        out = cluster.psql("SELECT business_name, approved, responsible_contact_name IS NULL, terms_accepted_at IS NULL "
                           "FROM contractor_letter_settings WHERE contractor_email='old@example.com'", db=DB + "_old")
        out2 = cluster.psql("SELECT token, pending_signup IS NULL FROM contractor_auth_tokens", db=DB + "_old")
        check("startup schema updates repeat cleanly and keep existing rows (settings + tokens)",
              re.search(r"OldCo\|t\|t\|t", out.replace(" ", "")) is not None
              and re.search(r"t1\|t", out2.replace(" ", "")) is not None, out + out2)
    finally:
        for c in conns:
            c.close()
        cluster.cleanup()

    failed = [r for r in results if not r[1]]
    print(f"\n{len(results) - len(failed)}/{len(results)} checks passed.")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())

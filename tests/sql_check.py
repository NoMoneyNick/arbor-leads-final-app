"""sql_check.py -- runs the REAL PgStore SQL (internal_digest.py) against a real Postgres through a tiny psql-backed
DB-API shim (no psycopg2 needed), including simultaneous senders. Not part of the unit-test run (needs Postgres).
usage: python3 tests/sql_check.py <psql> <socket dir>      (database 't' with a system_state table must exist)"""
import datetime as dt
import os
import re
import subprocess
import sys
import threading
import types

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
PSQL, SOCK = sys.argv[1], sys.argv[2]
SEP, NULLV, END = "\x1f", "\x01NULL", "@@END@@"
DBUSER = "postgres"          # switched to a restricted role for the permission checks


def lit(v):
    if v is None:
        return "NULL"
    if isinstance(v, (int, float)):
        return str(v)
    return "'" + str(v).replace("'", "''") + "'"


class Cur:
    def __init__(self, c):
        self.c, self.rows = c, []

    def execute(self, sql, params=()):
        params = list(params)
        sql = sql.replace("%%", "\x02")
        sql = re.sub(r"%s", lambda m: lit(params.pop(0)), sql).replace("\x02", "%")
        self.rows = self.c.run(sql)

    def fetchone(self):
        return self.rows.pop(0) if self.rows else None

    def fetchall(self):
        r, self.rows = self.rows, []
        return r


class Conn:
    def __init__(self):
        self.p = subprocess.Popen([PSQL, "-h", SOCK, "-U", DBUSER, "-d", "t", "-X", "-q", "-At", "-F", SEP, "-P", f"null={NULLV}"],
                                  stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        self.intx = False

    def run(self, sql):
        if not self.intx:
            self.p.stdin.write("BEGIN;\n")
            self.intx = True
        self.p.stdin.write(sql.strip().rstrip(";") + ";\n\\echo " + END + "\n")
        self.p.stdin.flush()
        rows = []
        while True:
            line = self.p.stdout.readline()
            if line.strip() == END:
                break
            if line.startswith("ERROR:"):
                raise RuntimeError(line.strip())
            if line.strip():
                rows.append(tuple(None if x == NULLV else x for x in line.rstrip("\n").split(SEP)))
        return rows

    def cursor(self):
        return Cur(self)

    def commit(self):
        if self.intx:
            self.run("COMMIT")
            self.intx = False

    def rollback(self):
        if self.intx:
            self.p.stdin.write("ROLLBACK;\n\\echo " + END + "\n")
            self.p.stdin.flush()
            while self.p.stdout.readline().strip() != END:
                pass
            self.intx = False

    def close(self):
        try:
            self.rollback()
            self.p.stdin.write("\\q\n")
            self.p.stdin.flush()
            self.p.wait(timeout=5)
        except Exception:
            pass


fake_db = types.ModuleType("database")
fake_db.get_db_conn = Conn
fake_db.get_all_recurring_warnings = lambda **k: []
sys.modules["database"] = fake_db
import internal_digest as D  # noqa: E402

UTC = dt.timezone.utc
NOW = dt.datetime(2026, 7, 15, 8, 5, tzinfo=UTC)
TODAY = "2026-07-15"
ok_all = True


def check(name, cond, extra=""):
    global ok_all
    ok_all &= bool(cond)
    print(("PASS " if cond else "FAIL ") + name + (f"  [{extra}]" if extra else ""))


def sql(q):
    c = Conn()
    try:
        r = c.run(q)
        c.commit()
        return r
    finally:
        c.close()


def reset():
    sql("CREATE TABLE IF NOT EXISTS leads (reference TEXT UNIQUE, address TEXT, summary TEXT, council_source TEXT, lead_score TEXT, lead_price NUMERIC, discovered_at TIMESTAMPTZ DEFAULT NOW()); DROP TABLE IF EXISTS internal_digest_queue; DELETE FROM system_state")
    D.PgStore._schema_ready = False


class Sender:
    def __init__(self, ok=True, delay=0.0):
        self.calls, self.ok, self.delay, self.lock = [], ok, delay, threading.Lock()

    def __call__(self, subject, body, att, idem):
        import time
        time.sleep(self.delay)
        with self.lock:
            self.calls.append((subject, idem, len(att)))
        return self.ok


store = D.PgStore()
reset()
store.ensure_schema()
D.PgStore._schema_ready = False
store.ensure_schema()
check("schema creates twice without error (IF NOT EXISTS)", True)

# leads: unique per ref, even across the 'sent' state
D.enqueue_leads([{"ref": f"R{i}", "city": "Leeds", "addr": f"{i} Rd", "summary": "s", "score": "Large", "price": 75, "link": "x"} for i in range(40)], store=store)
D.enqueue_leads([{"ref": "R1", "city": "Leeds"}, {"ref": "R2", "city": "Leeds"}], store=store)
check("duplicate lead refs are stored once", sql("SELECT count(*) FROM internal_digest_queue WHERE kind='lead'")[0][0] == "40")
for _ in range(5):
    D.enqueue_issue("SCANNER", "SCRAPER API KEY", "FAILED FOR ALL PREFIXES IN LEEDS", description="it's \"bad\" <b>", store=store)
check("issue occurrences are all stored (they are combined at send time)", sql("SELECT count(*) FROM internal_digest_queue WHERE kind='issue'")[0][0] == "5")

# 8 simultaneous senders for the same topic
s = Sender(delay=0.5)
res = []
ts = [threading.Thread(target=lambda: res.append(D.run_due_digests(now=NOW, store=D.PgStore(), sender=s, topics=("LEADS", "SCANNER"), base_url="https://app"))) for _ in range(8)]
[t.start() for t in ts]
[t.join() for t in ts]
subjects = sorted(c[0] for c in s.calls)
check("8 simultaneous senders -> exactly one LEADS email and one SCANNER email", len(s.calls) == 2, str(subjects))
check("lead digest has the full CSV attachment (40 > 30)", any("new lead" in c[0] and c[2] == 1 for c in s.calls))
check("rows marked sent only after Resend accepted", sql("SELECT count(*) FROM internal_digest_queue WHERE sent_at IS NULL AND kind IN ('lead','issue')")[0][0] == "0")
again = Sender()
D.run_due_digests(now=NOW + dt.timedelta(hours=1), store=D.PgStore(), sender=again, base_url="https://app")
check("later runs the same day send nothing (done-today gate)", again.calls == [])

# failure keeps items, back-off, then success
reset()
D.enqueue_leads([{"ref": "A1", "city": "Leeds", "addr": "1 Rd", "summary": "s", "score": "Large", "price": 75}], store=store)
bad = Sender(ok=False)
D.run_due_digests(now=NOW, store=store, sender=bad, topics=("LEADS",))
check("failed send keeps the item pending and releases the claim", sql("SELECT count(*) FROM internal_digest_queue WHERE sent_at IS NULL AND claim_token IS NULL")[0][0] == "1")
r = D.run_due_digests(now=NOW + dt.timedelta(minutes=10), store=store, sender=bad, topics=("LEADS",))
check("no retry inside the back-off window", r.get("LEADS") == "backoff" and len(bad.calls) == 1, str(r))
good = Sender()
r = D.run_due_digests(now=NOW + dt.timedelta(minutes=31), store=store, sender=good, topics=("LEADS",))
check("retry after back-off sends once", r.get("LEADS") == "sent" and len(good.calls) == 1, str(r))

# crash after Resend accepted but before marking sent: claim expires, same idempotency key
reset()
D.enqueue_leads([{"ref": "B1", "city": "X", "addr": "1", "summary": "s", "score": "Small", "price": 25}], store=store)
s1 = Sender()
orig = D.PgStore.finish_ok
D.PgStore.finish_ok = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("died"))
D.run_due_digests(now=NOW, store=store, sender=s1, topics=("LEADS",))
D.PgStore.finish_ok = orig
r = D.run_due_digests(now=NOW + dt.timedelta(minutes=5), store=store, sender=Sender(), topics=("LEADS",))
check("live claim blocks a second sender", r.get("LEADS") == "in_progress", str(r))
sql("UPDATE internal_digest_queue SET claimed_at = NOW() - interval '25 minutes'")
s2 = Sender()
D.run_due_digests(now=NOW + dt.timedelta(minutes=40), store=store, sender=s2, topics=("LEADS",))
check("expired claim is re-claimed and the Resend idempotency key matches the first attempt", s1.calls and s2.calls and s1.calls[0][1] == s2.calls[0][1], f"{s1.calls[:1]} {s2.calls[:1]}")

# empty topics send nothing; items arriving after the daily run wait for tomorrow
reset()
none = Sender()
r = D.run_due_digests(now=NOW, store=store, sender=none, base_url="https://app")
check("empty topics send nothing", none.calls == [] and all(v == "empty" for v in r.values()), str(r))
D.enqueue_leads([{"ref": "C1", "city": "Y", "addr": "1", "summary": "s", "score": "Small", "price": 25}], store=store)
D.run_due_digests(now=NOW + dt.timedelta(hours=3), store=store, sender=none)
check("item arriving after the day's run waits for tomorrow", none.calls == [])
D.run_due_digests(now=NOW + dt.timedelta(days=1), store=store, sender=none)
check("...and goes out in the next day's digest", len(none.calls) == 1)


# lead sweep: leads stored but never queued are picked up once; old leads and already-sent leads are not
reset()
sql("DROP TABLE IF EXISTS leads; CREATE TABLE leads (reference TEXT UNIQUE, address TEXT, summary TEXT, council_source TEXT, lead_score TEXT, lead_price NUMERIC, discovered_at TIMESTAMPTZ DEFAULT NOW())")
sql("INSERT INTO leads (reference, address, summary, council_source, lead_score, lead_price, discovered_at) VALUES ('OLD-1','1 Rd','fell','Leeds','large',75, NOW() - interval '3 days')")
store.ensure_schema()                       # creates the table AND the cut-over marker (now)
check("cut-over marker written once", sql("SELECT count(*) FROM system_state WHERE key = 'digest_leads_since'")[0][0] == "1")
D.PgStore._schema_ready = False; store.ensure_schema()
check("cut-over marker is not overwritten by a second ensure_schema", sql("SELECT count(*) FROM system_state WHERE key = 'digest_leads_since'")[0][0] == "1")
sql("INSERT INTO leads (reference, address, summary, council_source, lead_score, lead_price) VALUES ('NEW-1','2 Rd','fell oak','Leeds','large',75), ('NEW-2','3 Rd','prune','London','small',25), ('NEW-3','4 Rd','fell','Leeds','medium',50)")
D.enqueue_leads([{"ref": "NEW-3", "city": "Leeds", "addr": "4 Rd", "summary": "fell", "score": "Medium", "price": 50}], store=store)   # already queued normally
check("sweep queues only the two never-queued, post-cut-over leads", D.backfill_leads(store) == 2)
check("old (pre-cut-over) lead is never replayed", sql("SELECT count(*) FROM internal_digest_queue WHERE item_key = 'OLD-1'")[0][0] == "0")
check("sweep is idempotent", D.backfill_leads(store) == 0 and sql("SELECT count(*) FROM internal_digest_queue WHERE kind='lead'")[0][0] == "3")
s5 = Sender()
D.run_due_digests(now=NOW, store=D.PgStore(), sender=s5, topics=("LEADS",), base_url="https://app")
check("one lead email with all 3 leads", len(s5.calls) == 1 and "3 new lead" in s5.calls[0][0], str(s5.calls))
check("after sending, the sweep does not re-add sent leads", D.backfill_leads(store) == 0)
sql("DELETE FROM system_state WHERE key = 'digest_leads_since'")
sql("INSERT INTO leads (reference, address, summary, council_source, lead_score, lead_price) VALUES ('NEW-9','9 Rd','x','Leeds','small',25)")
check("without a cut-over marker nothing is swept", D.backfill_leads(store) == 0)

# ---- durable fallback limit (real SQL): shared by restarts and by simultaneous processes
reset()
store.ensure_schema()
T = 1_700_000_000.0
got = []
ts = [threading.Thread(target=lambda i=i: got.append(D.fallback_allowed(f"alert-{i}", now_ts=T, store=D.PgStore()))) for i in range(8)]
[t.start() for t in ts]
[t.join() for t in ts]
check("8 simultaneous processes: exactly FALLBACK_PER_HOUR fallback emails allowed", sum(got) == D.FALLBACK_PER_HOUR, str(sum(got)))
check("after a 'restart' (new store object) the same alert is refused", D.fallback_allowed("alert-0", now_ts=T + 5, store=D.PgStore()) is False)
check("an hour later one more is allowed, never more than the daily cap", sum(D.fallback_allowed(f"later-{i}", now_ts=T + 3700 + i, store=D.PgStore()) for i in range(10)) == min(D.FALLBACK_PER_HOUR, D.FALLBACK_PER_DAY - D.FALLBACK_PER_HOUR))

# ---- lead recovery gap: lead saved right after activation, queueing fails, cut-over written late
reset()
sql("DROP TABLE IF EXISTS leads; CREATE TABLE leads (reference TEXT UNIQUE, address TEXT, summary TEXT, council_source TEXT, lead_score TEXT, lead_price NUMERIC, discovered_at TIMESTAMPTZ DEFAULT NOW())")
act = dt.datetime.now(UTC)
D._PROCESS_START = act                                     # 'activation'
sql(f"INSERT INTO leads (reference, address, summary, council_source, lead_score, lead_price, discovered_at) VALUES ('BEFORE','1 Rd','fell','Leeds','large',75, '{(act - dt.timedelta(hours=3)).isoformat()}')")
sql(f"INSERT INTO leads (reference, address, summary, council_source, lead_score, lead_price, discovered_at) VALUES ('JUST-AFTER','2 Rd','fell oak','Leeds','large',75, '{(act + dt.timedelta(milliseconds=5)).isoformat()}')")
D._cutover_done = False
class DownEnqueue(D.PgStore):
    def enqueue(self, rows):
        raise RuntimeError("queue write failed")
D.PgStore._schema_ready = False
bad = DownEnqueue()
check("queueing fails for the lead saved just after activation", D.enqueue_leads([{"ref": "JUST-AFTER", "city": "Leeds"}], store=bad) is False)
D._buffer.clear()                                          # simulate a restart losing the in-memory copy
check("the durable cut-over was recorded (as the activation time) even though queueing failed", sql("SELECT count(*) FROM system_state WHERE key='digest_leads_since'")[0][0] == "1")
s6 = Sender()
D.run_due_digests(now=NOW, store=D.PgStore(), sender=s6, topics=("LEADS",), base_url="https://app")
check("later digest recovers JUST-AFTER and not the older lead", len(s6.calls) == 1 and "1 new lead" in s6.calls[0][0], str(s6.calls))
check("the older lead was never queued", sql("SELECT count(*) FROM internal_digest_queue WHERE item_key='BEFORE'")[0][0] == "0")
check("JUST-AFTER queued exactly once and now sent", sql("SELECT count(*) FROM internal_digest_queue WHERE item_key='JUST-AFTER' AND sent_at IS NOT NULL")[0][0] == "1")
# cut-over written LATE (first attempts failed) still equals the activation time, and is never moved afterwards
reset(); D._cutover_done = False; D.PgStore._schema_ready = False
D._PROCESS_START = act
D.PgStore().ensure_cutover()
D._PROCESS_START = act + dt.timedelta(hours=9)             # a later process must not move it
D.PgStore().ensure_cutover()
v = sql("SELECT value::timestamptz = %s::timestamptz FROM system_state WHERE key='digest_leads_since'" % lit(act.isoformat()))
check("cut-over equals the first activation time and is never overwritten", v and v[0][0] == "t", str(v))

# ---- production-style restricted role: the migration is run once by the owner, the app then needs no CREATE rights
reset()
sql("DROP TABLE IF EXISTS internal_digest_queue")
sql("DO $$ BEGIN IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname='app_limited') THEN CREATE ROLE app_limited LOGIN; END IF; END $$")
sql("REVOKE CREATE ON SCHEMA public FROM PUBLIC; REVOKE CREATE ON SCHEMA public FROM app_limited; GRANT USAGE ON SCHEMA public TO app_limited")
sql("GRANT SELECT, INSERT, UPDATE, DELETE ON system_state TO app_limited; GRANT SELECT ON leads TO app_limited")
DBUSER = "app_limited"
D.PgStore._schema_ready = False
try:
    D.PgStore().ensure_schema(); no_mig = True
except Exception as e:
    no_mig = False
check("WITHOUT the migration a restricted role cannot create the table (so the migration is a prerequisite)", no_mig is False)
DBUSER = "postgres"
for stmt in D.DDL:                                          # == migrations/0007_internal_digest_queue.sql, run once by the owner
    sql(stmt)
sql("GRANT SELECT, INSERT, UPDATE, DELETE ON internal_digest_queue TO app_limited; GRANT USAGE, SELECT ON SEQUENCE internal_digest_queue_id_seq TO app_limited")
DBUSER = "app_limited"
D.PgStore._schema_ready = False
try:
    D.PgStore().ensure_schema(); with_mig = True
except Exception as e:
    with_mig = False; print("   restricted role error:", str(e)[:150])
check("WITH the migration run, the restricted role needs no CREATE rights", with_mig is True)
D._cutover_done = False
s7 = Sender()
D.enqueue_leads([{"ref": "RL-1", "city": "Leeds", "addr": "1 Rd", "summary": "s", "score": "Large", "price": 75}], store=D.PgStore())
D.enqueue_issue("SCANNER", "SCRAPER", "X FAILED IN LEEDS", store=D.PgStore())
D.run_due_digests(now=NOW, store=D.PgStore(), sender=s7, topics=("LEADS", "SCANNER"), base_url="https://app")
check("restricted role can queue, claim, send and mark sent (advisory locks included)", len(s7.calls) == 2, str(s7.calls))
check("restricted role can use the durable fallback limit", D.fallback_allowed("rl", now_ts=T, store=D.PgStore()) is True)
DBUSER = "postgres"

print("RESULT:", "PASS" if ok_all else "FAIL")
sys.exit(0 if ok_all else 1)

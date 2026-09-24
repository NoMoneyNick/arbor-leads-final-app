#!/usr/bin/env python3
"""
tests/postgres_concurrency/run_concurrency_tests.py

Item 5 of the 2026-09-18 review: "Run funding/allocation concurrency tests
against disposable local PostgreSQL if available (simultaneous workers,
insufficient balance, transaction rollback, and uncertain submissions).
Never run these against a production or shared database. If no local
database is available, state that this test is outstanding and cannot be
verified in this environment, rather than skipping it silently."

WHY THIS IS A SEPARATE SCRIPT, NOT PART OF `unittest discover`
----------------------------------------------------------------
This sandbox cannot install psycopg2 or psycopg (no network access to PyPI
or apt for either package -- confirmed by direct failed install attempts).
That means the real Python call path through funding.FundingGate and
fulfilment.claim_for_submission cannot be driven directly against a live
Postgres from this environment: those modules import psycopg2 themselves
(see database.py) and there is no way to satisfy that import here.

What CAN be done, and what this script does, is drive the *exact* SQL those
functions execute (copied verbatim from funding.py and fulfilment.py, with
each copy pointing at the source line numbers it was copied from) via `psql`
subprocesses, so that the actual PostgreSQL row-locking behaviour those
functions depend on is genuinely exercised against a genuine, disposable,
local PostgreSQL 16 server -- not mocked, not assumed. This is a legitimate
way to test "does this SQL's concurrency control actually work against real
Postgres", which is what Item 5 is asking for; it does NOT test "does the
Python glue around that SQL behave correctly", which is what the existing
mocked unit tests (tests/test_funding.py, tests/test_funding_reservation_
wiring.py, tests/test_fulfilment.py) already cover, using fakes, as part of
the regular `unittest discover` suite.

WHAT THIS NEVER DOES
---------------------
- Never connects to anything but a disposable server this script itself
  started, on a private unix socket, in a temp directory this script itself
  created and will delete.
- Never reads DATABASE_URL / SUPABASE_DB_URL or any other environment
  variable that could point at a real database.
- Never runs outside explicit invocation of this script.

REQUIREMENTS
------------
- PostgreSQL 16 server + client binaries (initdb, pg_ctl, createdb, psql).
  On this sandbox these exist at /usr/lib/postgresql/16/bin/ and /usr/bin/.
- A non-root OS user to run the Postgres *server* process as (initdb/pg_ctl
  refuse to run as root). This script uses `su <user> -c "..."` for the
  server lifecycle. Defaults to the 'claude' user (uid 999), which exists
  in this sandbox; override with --pg-os-user if running elsewhere.
- Must be run as root (or a user that can `su` to --pg-os-user), because
  the orchestration itself (this script) runs as root in this sandbox.

USAGE
-----
    python3 tests/postgres_concurrency/run_concurrency_tests.py

Exit codes: 0 = all scenarios passed. 1 = at least one scenario failed.
2 = PostgreSQL is not available in this environment -- Item 5 is OUTSTANDING
and cannot be verified here; this is reported loudly, not swallowed.

This script always cleans up after itself (stops the server, deletes the
temp data directory) even on failure or interruption.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import time
import uuid

PG_BIN_CANDIDATES = [
    "/usr/lib/postgresql/16/bin",
    "/usr/lib/postgresql/15/bin",
    "/usr/lib/postgresql/14/bin",
]
PG_OS_USER_DEFAULT = "claude"
DB_NAME = "treekey_concurrency_test"

REPO_APP_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
MIGRATION_FILE = os.path.join(REPO_APP_DIR, "migrations", "0001_letter_fulfilment.sql")


class Cluster:
    """Owns the lifecycle of one disposable local PostgreSQL server."""

    def __init__(self, pg_bin: str, pg_os_user: str):
        self.pg_bin = pg_bin
        self.pg_os_user = pg_os_user
        self.base_dir = tempfile.mkdtemp(prefix="treekey_pg_concurrency_")
        self.data_dir = os.path.join(self.base_dir, "data")
        self.sock_dir = os.path.join(self.base_dir, "sock")
        self.log_path = os.path.join(self.base_dir, "server.log")
        self.started = False

    def _su(self, cmd: str, check: bool = True) -> subprocess.CompletedProcess:
        return subprocess.run(["su", self.pg_os_user, "-c", cmd],
                               capture_output=True, text=True, check=check)

    def start(self) -> None:
        os.makedirs(self.sock_dir, exist_ok=True)
        subprocess.run(["chown", "-R", f"{self.pg_os_user}:{self.pg_os_user}", self.base_dir], check=True)

        initdb = os.path.join(self.pg_bin, "initdb")
        r = self._su(f"{initdb} -D {self.data_dir} --auth=trust --username={self.pg_os_user}", check=False)
        if r.returncode != 0:
            raise RuntimeError(f"initdb failed:\nSTDOUT: {r.stdout}\nSTDERR: {r.stderr}")

        # Trust auth for local unix-socket connections regardless of OS
        # user, and no TCP listener at all -- this is a private, disposable
        # test cluster, never reachable except via this exact socket path.
        hba_path = os.path.join(self.data_dir, "pg_hba.conf")
        with open(hba_path, "w") as f:
            f.write("local all all trust\n")
        os.chown(hba_path, _uid(self.pg_os_user), _gid(self.pg_os_user))

        pg_ctl = os.path.join(self.pg_bin, "pg_ctl")
        start_cmd = (
            f'{pg_ctl} -D {self.data_dir} -l {self.log_path} '
            f'-o "-c unix_socket_directories={self.sock_dir} -c listen_addresses=\'\'" start'
        )
        r = self._su(start_cmd, check=False)
        if r.returncode != 0:
            log_tail = ""
            if os.path.exists(self.log_path):
                with open(self.log_path) as f:
                    log_tail = f.read()[-2000:]
            raise RuntimeError(f"pg_ctl start failed:\nSTDOUT: {r.stdout}\nSTDERR: {r.stderr}\nLOG: {log_tail}")
        self.started = True

        # Wait for the socket to actually accept connections.
        deadline = time.time() + 15
        last_err = None
        while time.time() < deadline:
            try:
                self.psql("SELECT 1;", db="postgres")
                return
            except RuntimeError as e:
                last_err = e
                time.sleep(0.3)
        raise RuntimeError(f"Postgres did not become ready in time: {last_err}")

    def stop(self) -> None:
        if self.started:
            pg_ctl = os.path.join(self.pg_bin, "pg_ctl")
            self._su(f"{pg_ctl} -D {self.data_dir} stop -m fast", check=False)
            self.started = False

    def cleanup(self) -> None:
        self.stop()
        shutil.rmtree(self.base_dir, ignore_errors=True)

    def createdb(self, name: str) -> None:
        createdb = os.path.join(os.path.dirname(self.pg_bin), "..", "bin", "createdb")
        # createdb also ships alongside psql at /usr/bin -- prefer that if present.
        createdb_bin = "/usr/bin/createdb" if os.path.exists("/usr/bin/createdb") else os.path.join(self.pg_bin, "createdb")
        r = self._su(f"{createdb_bin} -h {self.sock_dir} -U {self.pg_os_user} {name}", check=False)
        if r.returncode != 0:
            raise RuntimeError(f"createdb failed:\nSTDOUT: {r.stdout}\nSTDERR: {r.stderr}")

    def psql(self, sql: str, db: str = DB_NAME, timeout: float = 30) -> str:
        """Run one SQL string via psql, synchronously, return combined
        stdout (including any \\echo output)."""
        return self._psql_common(["-c", sql], db=db, timeout=timeout)

    def psql_file(self, path: str, db: str = DB_NAME, timeout: float = 30) -> str:
        return self._psql_common(["-f", path], db=db, timeout=timeout)

    def _psql_common(self, extra_args, db: str, timeout: float) -> str:
        psql_bin = "/usr/bin/psql" if os.path.exists("/usr/bin/psql") else os.path.join(self.pg_bin, "psql")
        args = [psql_bin, "-h", self.sock_dir, "-U", self.pg_os_user, "-d", db,
                 "-v", "ON_ERROR_STOP=1", "-X", "-q"] + extra_args
        r = subprocess.run(args, capture_output=True, text=True, timeout=timeout)
        if r.returncode != 0:
            raise RuntimeError(f"psql failed (args={extra_args}):\nSTDOUT: {r.stdout}\nSTDERR: {r.stderr}")
        return r.stdout

    def psql_popen(self, sql_file_path: str, db: str = DB_NAME) -> subprocess.Popen:
        """Launch one SQL script asynchronously (does not wait). Used to run
        two 'workers' truly concurrently as separate OS processes/connections."""
        psql_bin = "/usr/bin/psql" if os.path.exists("/usr/bin/psql") else os.path.join(self.pg_bin, "psql")
        args = [psql_bin, "-h", self.sock_dir, "-U", self.pg_os_user, "-d", db,
                 "-v", "ON_ERROR_STOP=1", "-X", "-q", "-f", sql_file_path]
        return subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)


def _uid(name: str) -> int:
    import pwd
    return pwd.getpwnam(name).pw_uid


def _gid(name: str) -> int:
    import pwd
    return pwd.getpwnam(name).pw_gid


def find_pg_bin() -> str:
    for candidate in PG_BIN_CANDIDATES:
        if os.path.exists(os.path.join(candidate, "initdb")):
            return candidate
    # Fall back to PATH.
    initdb_on_path = shutil.which("initdb")
    if initdb_on_path:
        return os.path.dirname(initdb_on_path)
    raise FileNotFoundError("initdb not found in any known location or PATH")


class Result:
    def __init__(self, name: str):
        self.name = name
        self.passed = False
        self.detail = ""

    def ok(self, detail: str = ""):
        self.passed = True
        self.detail = detail
        return self

    def fail(self, detail: str):
        self.passed = False
        self.detail = detail
        return self


def apply_schema(cluster: Cluster) -> None:
    if not os.path.exists(MIGRATION_FILE):
        raise FileNotFoundError(f"migration file not found: {MIGRATION_FILE}")
    cluster.psql_file(MIGRATION_FILE)
    # Extra table this test harness itself needs, not part of the app schema.
    cluster.psql("""
        CREATE TABLE IF NOT EXISTS concurrency_test_events (
            id SERIAL PRIMARY KEY,
            worker TEXT NOT NULL,
            event TEXT NOT NULL,
            ts TIMESTAMPTZ DEFAULT clock_timestamp()
        );
    """)


def reset_test_data(cluster: Cluster) -> None:
    """Truncate everything this test suite writes to, between scenarios, so
    scenarios are independent of each other and re-runnable."""
    cluster.psql("""
        TRUNCATE funding_reservations, mailing_budget_confirmations,
                 letter_obligations, lead_allocations,
                 payment_allocation_reconciliation, concurrency_test_events
        RESTART IDENTITY CASCADE;
    """)


def write_script(base_dir: str, name: str, content: str) -> str:
    path = os.path.join(base_dir, name)
    with open(path, "w") as f:
        f.write(content)
    return path


# ---------------------------------------------------------------------------
# Scenario 1: simultaneous workers, insufficient COMBINED balance.
#
# Verbatim SQL below is copied from funding.py FundingGate.reserve()
# (funding.py lines ~276-320 at the time this was written): the idempotent
# pre-check, the `SELECT ... FOR UPDATE` row lock over candidate budget
# rows, and the all-or-nothing write. The Python-level looping/FIFO-split
# across MULTIPLE budget rows is deliberately not re-implemented here (it is
# ordinary single-process logic with no concurrency implications of its own
# -- see tests/test_funding.py for that); this scenario uses a single budget
# row so the SQL below is a faithful, direct translation of reserve()'s
# actual locking behaviour for the case that matters here: can two
# concurrent transactions ever both believe they've reserved money that
# only exists once.
# ---------------------------------------------------------------------------
_RESERVE_WORKER_SQL = """\
\\set ON_ERROR_STOP on

BEGIN;

INSERT INTO concurrency_test_events (worker, event) VALUES (:'worker', 'begin');

SELECT (COALESCE(SUM(amount_pence), 0) > 0) AS already_reserved
FROM funding_reservations WHERE obligation_id = :'obl_id' AND status = 'reserved';
\\gset

\\if :already_reserved
\\echo RESULT_IDEMPOTENT_SKIP
\\else

SELECT id AS budget_id
FROM mailing_budget_confirmations
WHERE active = TRUE AND (amount_pence - spent_pence - reserved_pence) > 0
ORDER BY created_at ASC
FOR UPDATE
LIMIT 1;
\\gset

INSERT INTO concurrency_test_events (worker, event) VALUES (:'worker', 'lock_acquired');

SELECT pg_sleep(:sleep_secs);

SELECT ((amount_pence - spent_pence - reserved_pence) >= :amount_pence) AS covered
FROM mailing_budget_confirmations WHERE id = :'budget_id';
\\gset

\\if :covered
UPDATE mailing_budget_confirmations SET reserved_pence = reserved_pence + :amount_pence WHERE id = :'budget_id';
INSERT INTO funding_reservations (obligation_id, budget_confirmation_id, amount_pence, status)
    VALUES (:'obl_id', :'budget_id', :amount_pence, 'reserved');
\\echo RESULT_OK
\\else
\\echo RESULT_INSUFFICIENT
\\endif

\\endif

INSERT INTO concurrency_test_events (worker, event) VALUES (:'worker', 'commit');
COMMIT;
"""


def scenario_1_concurrent_workers_insufficient_combined_balance(cluster: Cluster, base_dir: str) -> Result:
    result = Result("1. Simultaneous workers racing for a budget that cannot cover both")
    reset_test_data(cluster)
    cluster.psql("INSERT INTO mailing_budget_confirmations (amount_pence, confirmed_by, note) "
                 "VALUES (1000, 'test-harness', 'scenario 1');")

    # Worker A: reserves 700 of the 1000 available, holding the row lock for
    # 2 seconds so Worker B is guaranteed to have to wait on it. Worker B is
    # launched immediately after (no artificial delay), so both BEGINs
    # overlap in wall-clock time -- the serialization guarantee under test
    # is enforced by Postgres's row lock, not by process launch order: B's
    # own FOR UPDATE would still correctly block even if it somehow reached
    # the lock first (that's Postgres's job, not this harness's).
    def run_variant(sql, worker, obl_id, amount, sleep_secs):
        content = sql.replace(":'worker'", f"'{worker}'").replace(":'obl_id'", f"'{obl_id}'")
        content = content.replace(":amount_pence", str(amount)).replace(":sleep_secs", str(sleep_secs))
        return content

    content_a = run_variant(_RESERVE_WORKER_SQL, "worker_a", "obl_a", 700, 2)
    content_b = run_variant(_RESERVE_WORKER_SQL, "worker_b", "obl_b", 700, 0)
    path_a = write_script(base_dir, "s1_a.sql", content_a)
    path_b = write_script(base_dir, "s1_b.sql", content_b)

    p_a = cluster.psql_popen(path_a)
    p_b = cluster.psql_popen(path_b)
    out_a, err_a = p_a.communicate(timeout=30)
    out_b, err_b = p_b.communicate(timeout=30)

    if p_a.returncode != 0 or p_b.returncode != 0:
        return result.fail(f"psql exited nonzero. A: rc={p_a.returncode} err={err_a}\nB: rc={p_b.returncode} err={err_b}")

    a_ok = "RESULT_OK" in out_a
    a_insufficient = "RESULT_INSUFFICIENT" in out_a
    b_ok = "RESULT_OK" in out_b
    b_insufficient = "RESULT_INSUFFICIENT" in out_b

    row = cluster.psql("SELECT reserved_pence FROM mailing_budget_confirmations LIMIT 1;")
    reservations = cluster.psql("SELECT obligation_id, amount_pence, status FROM funding_reservations ORDER BY obligation_id;")

    exactly_one_ok = (a_ok and b_insufficient) or (b_ok and a_insufficient)
    reserved_pence_is_700 = "700" in row and "1400" not in row

    if exactly_one_ok and reserved_pence_is_700:
        return result.ok(
            f"Exactly one worker succeeded (A={'OK' if a_ok else 'INSUFFICIENT'}, "
            f"B={'OK' if b_ok else 'INSUFFICIENT'}); budget reserved_pence ended at 700, "
            f"never double-committed to 1400. Reservations table:\n{reservations.strip()}"
        )
    return result.fail(
        f"Expected exactly one OK and one INSUFFICIENT, reserved_pence=700. "
        f"Got: A stdout={out_a!r} B stdout={out_b!r} budget_row={row!r} reservations={reservations!r}"
    )


# ---------------------------------------------------------------------------
# Scenario 2: single worker, insufficient balance from the very start
# (non-concurrent -- explicitly requested by Item 5's own wording alongside
# the concurrency scenarios). Verbatim reserve() all-or-nothing contract:
# "if the full amount can't be covered, NOTHING is written and ok=False".
# ---------------------------------------------------------------------------
def scenario_2_single_worker_insufficient_balance_from_start(cluster: Cluster, base_dir: str) -> Result:
    result = Result("2. Single worker, insufficient balance from the start (all-or-nothing)")
    reset_test_data(cluster)
    cluster.psql("INSERT INTO mailing_budget_confirmations (amount_pence, confirmed_by, note) "
                 "VALUES (500, 'test-harness', 'scenario 2');")

    content = _RESERVE_WORKER_SQL.replace(":'worker'", "'worker_solo'").replace(":'obl_id'", "'obl_solo'")
    content = content.replace(":amount_pence", "900").replace(":sleep_secs", "0")
    path = write_script(base_dir, "s2.sql", content)
    out = cluster.psql_file(path)

    reservations = cluster.psql("SELECT count(*) FROM funding_reservations;").strip()
    budget_row = cluster.psql("SELECT reserved_pence FROM mailing_budget_confirmations LIMIT 1;").strip()

    if "RESULT_INSUFFICIENT" in out and reservations.splitlines()[-2].strip() == "0" and "0" in budget_row:
        return result.ok("reserve() for 900 against 500 available correctly reported insufficient, "
                          "wrote zero funding_reservations rows, and left reserved_pence at 0.")
    return result.fail(f"stdout={out!r} reservations_count_output={reservations!r} budget_row={budget_row!r}")


# ---------------------------------------------------------------------------
# Scenario 3: transaction rollback reverts a reservation that was never
# committed -- this is what makes it safe that create_allocation_and_
# obligation's own docstring says "call this on the SAME cursor/transaction
# as the sale itself, before the caller's own conn.commit()": if something
# later in that same transaction fails and the whole thing rolls back, the
# reservation must not have been durably applied either.
# ---------------------------------------------------------------------------
def scenario_3_transaction_rollback_reverts_reservation(cluster: Cluster, base_dir: str) -> Result:
    result = Result("3. Transaction rollback reverts an uncommitted reservation")
    reset_test_data(cluster)
    cluster.psql("INSERT INTO mailing_budget_confirmations (amount_pence, confirmed_by, note) "
                 "VALUES (1000, 'test-harness', 'scenario 3');")

    # Same reserve() SQL as above, but ROLLBACK instead of COMMIT at the end
    # -- simulating the caller's outer transaction aborting after reserve()
    # ran but before the sale itself finished.
    content = _RESERVE_WORKER_SQL.replace(":'worker'", "'worker_rollback'").replace(":'obl_id'", "'obl_rollback'")
    content = content.replace(":amount_pence", "400").replace(":sleep_secs", "0")
    content = content.replace("COMMIT;", "ROLLBACK;")
    path = write_script(base_dir, "s3.sql", content)
    out = cluster.psql_file(path)

    reservations_count = cluster.psql("SELECT count(*) FROM funding_reservations;")
    budget_row = cluster.psql("SELECT reserved_pence FROM mailing_budget_confirmations LIMIT 1;")

    zero_reservations = reservations_count.strip().splitlines()[-2].strip() == "0"
    zero_reserved_pence = budget_row.strip().splitlines()[-2].strip() == "0"

    if "RESULT_OK" in out and zero_reservations and zero_reserved_pence:
        return result.ok("reserve() reported OK inside the transaction (would have written 400 pence), "
                          "but after ROLLBACK both funding_reservations and reserved_pence are back to "
                          "their pre-transaction state -- confirming the writes were never durably applied.")
    return result.fail(f"stdout={out!r} reservations_count={reservations_count!r} budget_row={budget_row!r} "
                        f"(zero_reservations={zero_reservations}, zero_reserved_pence={zero_reserved_pence})")


# ---------------------------------------------------------------------------
# Scenario 4: an 'unknown' provider outcome must NEVER be released. This
# tests funding.py's release()/settle() contract negatively: as long as
# nothing calls release() or settle() for an obligation, its reservation
# stays 'reserved' and its pence stay unavailable to any other obligation --
# proving the money really is held, not just labelled as held.
# ---------------------------------------------------------------------------
def scenario_4_unknown_outcome_preserves_reservation(cluster: Cluster, base_dir: str) -> Result:
    result = Result("4. Uncertain ('unknown') submission outcome leaves the reservation held, untouched")
    reset_test_data(cluster)
    cluster.psql("INSERT INTO mailing_budget_confirmations (amount_pence, confirmed_by, note) "
                 "VALUES (1000, 'test-harness', 'scenario 4');")

    # Obligation A reserves 800 of the 1000 available. Its provider outcome
    # is then 'unknown' -- per funding.py's release() docstring, this must
    # NEVER be released. This harness models that literally: it simply does
    # not call release() or settle() for obl_unknown, which is the entire
    # point of the contract (the correct behaviour is an absence of a call).
    content_a = _RESERVE_WORKER_SQL.replace(":'worker'", "'worker_unknown'").replace(":'obl_id'", "'obl_unknown'")
    content_a = content_a.replace(":amount_pence", "800").replace(":sleep_secs", "0")
    path_a = write_script(base_dir, "s4_a.sql", content_a)
    out_a = cluster.psql_file(path_a)

    if "RESULT_OK" not in out_a:
        return result.fail(f"setup failed: obl_unknown's own reservation did not succeed: {out_a!r}")

    # A second obligation now tries to reserve 300 -- more than the 200
    # pence actually still free (1000 - 800 held for the unresolved
    # 'unknown' outcome). It must fail, proving the 800 is genuinely
    # unavailable, not just cosmetically marked.
    content_b = _RESERVE_WORKER_SQL.replace(":'worker'", "'worker_after_unknown'").replace(":'obl_id'", "'obl_after_unknown'")
    content_b = content_b.replace(":amount_pence", "300").replace(":sleep_secs", "0")
    path_b = write_script(base_dir, "s4_b.sql", content_b)
    out_b = cluster.psql_file(path_b)

    reservation_row = cluster.psql(
        "SELECT status FROM funding_reservations WHERE obligation_id = 'obl_unknown';"
    )
    status_line = [l.strip() for l in reservation_row.strip().splitlines() if l.strip() and l.strip() != "status" and "row" not in l and set(l.strip()) != {"-"}]

    still_reserved = any(s == "reserved" for s in status_line)
    b_correctly_insufficient = "RESULT_INSUFFICIENT" in out_b

    if still_reserved and b_correctly_insufficient:
        return result.ok("obl_unknown's 800-pence reservation was never released (no release()/settle() call "
                          "was ever made for it, matching the 'never call release() for an unknown outcome' "
                          "contract); a second obligation correctly could not reserve more than the 200 pence "
                          "genuinely still free.")
    return result.fail(f"reservation_status_rows={status_line!r} out_b={out_b!r}")


# ---------------------------------------------------------------------------
# Scenario 5: claim_for_submission's atomic claim. Verbatim SQL copied from
# fulfilment.py claim_for_submission (fulfilment.py lines ~590-596 at the
# time this was written): a single UPDATE ... WHERE status = 'ready'
# RETURNING id. Postgres's own row-level locking during UPDATE makes this
# safe without an explicit FOR UPDATE -- two concurrent UPDATEs targeting
# the same row serialize automatically, and the second to run re-evaluates
# the WHERE clause against the now-committed row, so it correctly affects
# zero rows once the first has claimed it.
# ---------------------------------------------------------------------------
_CLAIM_WORKER_SQL = """\
\\set ON_ERROR_STOP on
BEGIN;
SELECT pg_sleep(0.3);
UPDATE letter_obligations
SET status = 'submitting', claimed_by_worker = :'worker', claimed_at = NOW(), updated_at = NOW()
WHERE id = :'obligation_id' AND status = 'ready'
RETURNING id;
COMMIT;
"""


def scenario_5_atomic_claim_for_submission(cluster: Cluster, base_dir: str) -> Result:
    result = Result("5. claim_for_submission: two workers racing to claim the same obligation")
    reset_test_data(cluster)

    cluster.psql("""
        INSERT INTO lead_allocations (lead_reference, buyer_email, allocation_type, idempotency_key)
        VALUES ('LR-CONCURRENCY-5', 'test@example.com', 'purchase', 'idem-concurrency-5');
    """)
    obligation_id = cluster.psql(
        "SELECT id FROM lead_allocations WHERE idempotency_key = 'idem-concurrency-5';"
    ).strip().splitlines()[-2].strip()

    cluster.psql(f"""
        INSERT INTO letter_obligations (allocation_id, lead_reference, address, buyer_email, sale_context,
                                         status, idempotency_key)
        VALUES ('{obligation_id}', 'LR-CONCURRENCY-5', '1 Test Street', 'test@example.com', 'purchase',
                'ready', 'ob-idem-concurrency-5');
    """)
    real_obligation_id = cluster.psql(
        "SELECT id FROM letter_obligations WHERE idempotency_key = 'ob-idem-concurrency-5';"
    ).strip().splitlines()[-2].strip()

    content_a = _CLAIM_WORKER_SQL.replace(":'worker'", "'worker_a'").replace(":'obligation_id'", f"'{real_obligation_id}'")
    content_b = _CLAIM_WORKER_SQL.replace(":'worker'", "'worker_b'").replace(":'obligation_id'", f"'{real_obligation_id}'")
    path_a = write_script(base_dir, "s5_a.sql", content_a)
    path_b = write_script(base_dir, "s5_b.sql", content_b)

    p_a = cluster.psql_popen(path_a)
    p_b = cluster.psql_popen(path_b)
    out_a, err_a = p_a.communicate(timeout=30)
    out_b, err_b = p_b.communicate(timeout=30)

    if p_a.returncode != 0 or p_b.returncode != 0:
        return result.fail(f"psql exited nonzero. A: rc={p_a.returncode} err={err_a}\nB: rc={p_b.returncode} err={err_b}")

    # The script runs two SELECTs (pg_sleep, then the UPDATE...RETURNING) --
    # both results contain a "(1 row)"/"(0 rows)" footer, so we must look at
    # the LAST result block (the RETURNING id from the UPDATE), not just
    # search the whole output for "(1 row)".
    def claimed_from_output(out: str) -> bool:
        blocks = [b for b in out.strip().split("\n\n") if b.strip()]
        return bool(blocks) and "(1 row)" in blocks[-1]

    a_claimed = claimed_from_output(out_a)
    b_claimed = claimed_from_output(out_b)

    final = cluster.psql(f"SELECT status, claimed_by_worker FROM letter_obligations WHERE id = '{real_obligation_id}';")

    exactly_one_claimed = a_claimed != b_claimed  # XOR
    if exactly_one_claimed:
        winner = "worker_a" if a_claimed else "worker_b"
        return result.ok(f"Exactly one worker ({winner}) claimed the obligation; the other's UPDATE affected "
                          f"zero rows. Final row: {final.strip()}")
    return result.fail(f"Expected exactly one claim. a_claimed={a_claimed} b_claimed={b_claimed} "
                        f"out_a={out_a!r} out_b={out_b!r} final={final!r}")


SCENARIOS = [
    scenario_1_concurrent_workers_insufficient_combined_balance,
    scenario_2_single_worker_insufficient_balance_from_start,
    scenario_3_transaction_rollback_reverts_reservation,
    scenario_4_unknown_outcome_preserves_reservation,
    scenario_5_atomic_claim_for_submission,
]


def main() -> int:
    import argparse
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--pg-os-user", default=PG_OS_USER_DEFAULT,
                         help="Non-root OS user to run the disposable Postgres server as (default: claude)")
    args = parser.parse_args()

    try:
        pg_bin = find_pg_bin()
    except FileNotFoundError as e:
        print("=" * 70)
        print("ITEM 5 OUTSTANDING: PostgreSQL server binaries were not found in this")
        print("environment (checked /usr/lib/postgresql/{16,15,14}/bin and PATH).")
        print(f"Detail: {e}")
        print("This concurrency test cannot be verified here. It has NOT been run,")
        print("and its results must not be assumed. Run this script in an environment")
        print("with a local PostgreSQL 16 server installed to verify it.")
        print("=" * 70)
        return 2

    if not shutil.which("psql") and not os.path.exists("/usr/bin/psql"):
        print("ITEM 5 OUTSTANDING: psql client not found. Cannot verify.")
        return 2

    print(f"Using PostgreSQL server binaries at: {pg_bin}")
    print(f"Running the server as OS user: {args.pg_os_user}")
    print(f"Applying schema from: {MIGRATION_FILE}")
    print()

    cluster = Cluster(pg_bin, args.pg_os_user)
    results = []
    try:
        cluster.start()
        cluster.createdb(DB_NAME)
        apply_schema(cluster)

        for scenario_fn in SCENARIOS:
            scratch_dir = tempfile.mkdtemp(dir=cluster.base_dir, prefix="scenario_")
            try:
                result = scenario_fn(cluster, scratch_dir)
            except Exception as e:  # noqa: BLE001 -- report, don't crash the whole run
                result = Result(scenario_fn.__name__).fail(f"raised {type(e).__name__}: {e}")
            results.append(result)
            status = "PASS" if result.passed else "FAIL"
            print(f"[{status}] {result.name}")
            print(f"       {result.detail}\n")

    finally:
        cluster.cleanup()
        print(f"Disposable PostgreSQL instance stopped and its data directory deleted "
              f"({cluster.base_dir} no longer exists). Nothing was left running.")

    failed = [r for r in results if not r.passed]
    print()
    print(f"{len(results) - len(failed)}/{len(results)} scenarios passed.")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())

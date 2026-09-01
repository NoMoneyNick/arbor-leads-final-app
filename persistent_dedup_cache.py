"""
persistent_dedup_cache.py -- a tiny, dependency-light "have I already done
this today" cache that survives process restarts.

THE PROBLEM THIS SOLVES
------------------------
The obvious way to avoid repeating an expensive or quota-limited action more
than once per calendar day is a module-level dict:

    _DAY_CACHE: dict = {}

    def do_the_thing():
        today = date.today().isoformat()
        if _DAY_CACHE.get(today):
            return
        ...do the expensive/quota-limited thing...
        _DAY_CACHE[today] = True

This works fine for a long-running process that restarts rarely. But it
silently resets on every process restart -- a deploy, a crash, a host
recycling the container -- and "restarts are rare" is a much shakier
assumption during active development than in steady-state production. A
day with three redeploys silently re-enables the exact thing the cache
existed to prevent, three times, on exactly the day you're least likely to
be watching for it (you're busy shipping fixes, not auditing quota usage).

This module is the same idea backed by a real table instead of a dict, so
it survives restarts. It costs one extra tiny DB round-trip per check --
irrelevant for anything gating a per-day action, which by definition
doesn't happen often enough for that cost to matter.

DESIGN NOTES
------------
- Works against ANY DB-API 2.0 connection -- Postgres (psycopg2), SQLite,
  MySQL. Pass in whatever connection your project already has open, or
  call get_default_connection() for a zero-config local SQLite file when
  you just want this to work standalone with no setup.
- Keys are arbitrary strings you choose -- namespace them yourself
  (e.g. "paid_api_rotation:london", "mesh_scan:full_sweep") so unrelated
  callers sharing one table don't collide.
- "Today" means real calendar date, not "since this process started" --
  that's the entire point versus the in-memory version.
- Idempotent: calling mark_done_today() twice in the same day for the same
  key is harmless (upsert, not insert-only).

USAGE
-----
    import persistent_dedup_cache as dedup

    conn = dedup.get_default_connection()   # or your own DB connection
    dedup.ensure_table(conn)

    key = f"paid_api_rotation:{city_name}"
    if dedup.already_done_today(conn, key):
        ...skip the expensive/quota-limited action...
    else:
        ...do the thing...
        dedup.mark_done_today(conn, key)
"""

import datetime
import sqlite3
from pathlib import Path

DEFAULT_SQLITE_PATH = Path.home() / ".persistent_dedup_cache.sqlite3"


def _is_sqlite(conn) -> bool:
    return type(conn).__module__.startswith("sqlite3")


def get_default_connection(path: Path = DEFAULT_SQLITE_PATH) -> sqlite3.Connection:
    """Zero-config local SQLite file -- good enough for a single-process
    tool or a quick script. Pass your own Postgres/MySQL connection instead
    for anything running as a real service, especially one with more than
    one instance/worker sharing the same quota."""
    return sqlite3.connect(str(path))


def ensure_table(conn) -> None:
    """Creates the backing table if it doesn't exist yet. Idempotent --
    safe (and cheap) to call on every process startup."""
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS persistent_dedup_cache (
            cache_key TEXT PRIMARY KEY,
            done_date TEXT NOT NULL
        )
    """)
    conn.commit()
    cur.close()


def _today_str() -> str:
    return datetime.date.today().isoformat()


def already_done_today(conn, key: str) -> bool:
    """True if mark_done_today(conn, key) was already called for today's
    calendar date -- in any process, at any point earlier today, including
    one that has since restarted."""
    cur = conn.cursor()
    placeholder = "?" if _is_sqlite(conn) else "%s"
    cur.execute(
        f"SELECT done_date FROM persistent_dedup_cache WHERE cache_key = {placeholder}",
        (key,),
    )
    row = cur.fetchone()
    cur.close()
    return bool(row) and row[0] == _today_str()


def mark_done_today(conn, key: str) -> None:
    """Records `key` as done for today's date. Safe to call more than once
    a day for the same key -- upserts rather than erroring on conflict."""
    cur = conn.cursor()
    today = _today_str()
    if _is_sqlite(conn):
        cur.execute(
            """INSERT INTO persistent_dedup_cache (cache_key, done_date)
               VALUES (?, ?)
               ON CONFLICT(cache_key) DO UPDATE SET done_date = excluded.done_date""",
            (key, today),
        )
    else:
        cur.execute(
            """INSERT INTO persistent_dedup_cache (cache_key, done_date)
               VALUES (%s, %s)
               ON CONFLICT (cache_key) DO UPDATE SET done_date = EXCLUDED.done_date""",
            (key, today),
        )
    conn.commit()
    cur.close()


def prune_older_than(conn, days: int = 30) -> int:
    """Optional housekeeping for long-running projects with many distinct
    keys accumulated over time (e.g. one key per city, per API, per day
    guard) -- deletes rows whose done_date is older than `days` days.
    Returns the number of rows deleted. Not required for correctness
    (already_done_today only ever compares against *today's* date, so a
    stale row is simply ignored, never a false positive) -- this exists
    purely to keep the table from growing forever."""
    cur = conn.cursor()
    cutoff = (datetime.date.today() - datetime.timedelta(days=days)).isoformat()
    placeholder = "?" if _is_sqlite(conn) else "%s"
    cur.execute(
        f"DELETE FROM persistent_dedup_cache WHERE done_date < {placeholder}",
        (cutoff,),
    )
    deleted = cur.rowcount
    conn.commit()
    cur.close()
    return deleted

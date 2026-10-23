"""
Real-Postgres checks (disposable server) for reply_optout.run_check / review actions:
duplicates, ADD-ONLY suppression, blocklist failure + retry, Instantly outage keeps the saved point,
review flow, and that existing suppressions are never changed.
Run: python tests/postgres_concurrency/run_reply_optout_real_db_test.py
"""
import datetime
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path[:0] = [os.path.dirname(os.path.dirname(_HERE)), _HERE]
import run_concurrency_tests as rct  # noqa: E402
import run_signup_real_db_tests as base  # noqa: E402
import reply_optout as ro  # noqa: E402

base.DB = "reply_optout_test"
results = []


def check(name, cond, detail=""):
    results.append(bool(cond))
    print(f"[{'PASS' if cond else 'FAIL'}] {name}" + (f" -- {detail}" if detail and not cond else ""))


class FakeClient:
    def __init__(self, pages=None):
        self.pages, self.blocked, self.fail_block, self.fail_list, self.calls = pages or [], [], False, None, []

    def list_received(self, min_iso, after=None):
        self.calls.append((min_iso, after))
        if self.fail_list:
            raise ro.InstantlyError(self.fail_list, 401)
        return self.pages.pop(0) if self.pages else {"items": []}

    def add_to_blocklist(self, email):
        if self.fail_block:
            raise ro.InstantlyError("HTTP 500", 500)
        self.blocked.append(email)


def mail(i, text, lead="a@acme.co.uk", ts="2026-10-23T10:00:00.000Z", **kw):
    d = {"id": i, "lead": lead, "from_address_email": lead, "eaccount": "nick@treekey.uk", "ue_type": 2,
         "is_auto_reply": 0, "subject": "Re: x", "timestamp_created": ts, "body": {"text": text}}
    d.update(kw)
    return d


cl = rct.Cluster(rct.find_pg_bin(), rct.PG_OS_USER_DEFAULT)
try:
    cl.start(); cl.createdb("reply_optout_test")
    conn = base.PsqlConn(cl)
    cu = conn.cursor()
    cu.execute("CREATE TABLE email_suppressions (email TEXT PRIMARY KEY, suppressed_at TIMESTAMPTZ DEFAULT NOW(), reason TEXT)")
    ro.init_reply_optout_schema(cu); ro.init_reply_optout_schema(cu)   # idempotent
    cu.execute("INSERT INTO email_suppressions (email, reason) VALUES ('old@acme.co.uk','user_unsubscribed')")
    conn.commit()
    NOW = datetime.datetime(2026, 10, 23, 12, 0, tzinfo=datetime.timezone.utc)

    def rows(sql):
        c = conn.cursor(); c.execute(sql); return c.fetchall()

    # 1. clear opt-out, ordinary reply, auto reply, ambiguous, in one page
    fc = FakeClient([{"items": [
        mail("m1", "No thanks", lead="a@acme.co.uk", ts="2026-10-23T09:00:00.000Z"),
        mail("m2", "Thanks, will pass on", lead="b@acme.co.uk", ts="2026-10-23T09:01:00.000Z"),
        mail("m3", "unsubscribe", lead="c@acme.co.uk", is_auto_reply=1, ts="2026-10-23T09:02:00.000Z"),
        mail("m4", "this is spam", lead="d@acme.co.uk", ts="2026-10-23T09:03:00.000Z"),
        mail("m5", "Remove me. Under GDPR delete my data", lead="e@acme.co.uk", ts="2026-10-23T09:04:00.000Z"),
    ]}])
    rep = ro.run_check(conn, fc, now=NOW)
    check("first pass: no error", rep["error"] is None, rep)
    check("counts are right", (rep["suppressed"], rep["no_action"], rep["ignored_auto"], rep["review"], rep["data_rights"]) == (1, 1, 1, 2, 1), rep)
    sup = {r[0]: r[1] for r in rows("SELECT email, reason FROM email_suppressions")}
    check("only the clear opt-out was suppressed (plus the old row)", set(sup) == {"a@acme.co.uk", "old@acme.co.uk"}, sup)
    check("suppression reason recorded", sup.get("a@acme.co.uk") == "reply_optout")
    check("blocklist add called once for it", fc.blocked == ["a@acme.co.uk"], fc.blocked)
    check("ambiguous replies are pending review with an excerpt",
          rows("SELECT count(*) FROM outreach_reply_events WHERE decision='review' AND review_status='pending' AND review_excerpt IS NOT NULL")[0][0] == "2")
    check("ordinary/auto replies store no excerpt", rows("SELECT count(*) FROM outreach_reply_events WHERE decision IN ('no_action','ignored_auto') AND review_excerpt IS NOT NULL")[0][0] == "0")
    _ls = rows("SELECT (last_seen AT TIME ZONE 'UTC')::text FROM outreach_reply_state")[0][0]; check("saved point moved to newest reply", _ls.startswith("2026-10-23 09:04"), _ls)

    # 2. same replies delivered again (overlap window / replay): nothing new happens
    fc2 = FakeClient([{"items": [mail("m1", "No thanks", lead="a@acme.co.uk"), mail("m4", "this is spam", lead="d@acme.co.uk")]}])
    rep = ro.run_check(conn, fc2, now=NOW)
    check("replayed replies counted as duplicates", rep["duplicates"] == 2 and rep["suppressed"] == 0, rep)
    check("no second blocklist call", fc2.blocked == [])
    check("still one event row per reply", rows("SELECT count(*) FROM outreach_reply_events")[0][0] == "5")
    check("read starts a little before the saved point (overlap)", fc2.calls[0][0] == "2026-10-23T08:54:00.000000Z", fc2.calls)

    # 3. existing suppression is never changed (add-only), even if the same person replies opt-out
    fc3 = FakeClient([{"items": [mail("m6", "unsubscribe", lead="old@acme.co.uk", ts="2026-10-23T10:00:00.000Z")]}])
    ro.run_check(conn, fc3, now=NOW)
    check("pre-existing suppression keeps its original reason", rows("SELECT reason FROM email_suppressions WHERE email='old@acme.co.uk'")[0][0] == "user_unsubscribed")
    check("suppression table only ever grew", rows("SELECT count(*) FROM email_suppressions")[0][0] == "2")

    # 4. Instantly blocklist failure: TreeKey suppression still written; retried next run
    fc4 = FakeClient([{"items": [mail("m7", "Not interested", lead="f@acme.co.uk", ts="2026-10-23T10:05:00.000Z")]}])
    fc4.fail_block = True
    rep = ro.run_check(conn, fc4, now=NOW)
    check("blocklist failure reported but no pass error", rep["blocklist_failed"] == 1 and rep["error"] is None, rep)
    check("TreeKey suppression exists despite blocklist failure", rows("SELECT count(*) FROM email_suppressions WHERE email='f@acme.co.uk'")[0][0] == "1")
    check("event marked failed with attempt count", rows("SELECT blocklist_status, blocklist_attempts FROM outreach_reply_events WHERE instantly_email_id='m7'")[0] == ("failed", "1"))
    fc5 = FakeClient([])
    rep = ro.run_check(conn, fc5, now=NOW)
    check("retry succeeds next run", rep["retried"] == 1 and fc5.blocked == ["f@acme.co.uk"], rep)
    check("event now added", rows("SELECT blocklist_status FROM outreach_reply_events WHERE instantly_email_id='m7'")[0][0] == "added")

    # 5. retries stop after the cap
    fc6 = FakeClient([{"items": [mail("m8", "Not interested", lead="g@acme.co.uk", ts="2026-10-23T10:06:00.000Z")]}])
    fc6.fail_block = True
    ro.run_check(conn, fc6, now=NOW)
    for _ in range(ro.MAX_BLOCKLIST_ATTEMPTS):
        ro.run_check(conn, fc6, now=NOW)
    n = rows("SELECT blocklist_attempts FROM outreach_reply_events WHERE instantly_email_id='m8'")[0][0]
    check("attempts capped", int(n) == ro.MAX_BLOCKLIST_ATTEMPTS, n)

    # 6. Instantly outage: error reported, saved point and data unchanged
    before = rows("SELECT last_seen::text FROM outreach_reply_state")[0][0]
    fc7 = FakeClient([]); fc7.fail_list = "HTTP 401 API key rejected or revoked"
    rep = ro.run_check(conn, fc7, now=NOW)
    st = rows("SELECT last_seen::text, last_run_status, last_error FROM outreach_reply_state")[0]
    check("outage returned as error", "401" in (rep["error"] or ""), rep)
    check("saved point not moved on error", st[0] == before, st)
    check("state shows error and message", st[1] == "error" and "401" in st[2], st)

    # 7. review flow: suppress one, dismiss one; neither removes anything
    n_before = rows("SELECT count(*) FROM email_suppressions")[0][0]
    fcr = FakeClient()
    r1 = ro.review_suppress(conn, fcr, "m4")
    check("review suppress adds suppression + blocklist", r1 == "suppressed" and fcr.blocked == ["d@acme.co.uk"], r1)
    check("excerpt cleared after review", rows("SELECT review_excerpt IS NULL, review_status FROM outreach_reply_events WHERE instantly_email_id='m4'")[0] == ("t", "suppressed"))
    check("second click is a no-op", ro.review_suppress(conn, fcr, "m4") == "already_reviewed" and fcr.blocked == ["d@acme.co.uk"])
    check("dismiss adds nothing", ro.review_dismiss(conn, "m5") == "dismissed" and rows("SELECT count(*) FROM email_suppressions WHERE email='e@acme.co.uk'")[0][0] == "0")
    check("dismiss of a non-review/unknown id does nothing", ro.review_dismiss(conn, "m1") == "not_found" and ro.review_dismiss(conn, "nope") == "not_found")
    r2 = ro.review_suppress(conn, None, "m9-missing")
    check("unknown id -> not_found", r2 == "not_found")
    check("suppression count only grew by the one approved", int(rows("SELECT count(*) FROM email_suppressions")[0][0]) == int(n_before) + 1)

    # 8. review-suppress without an API key queues the blocklist retry
    fc8 = FakeClient([{"items": [mail("m10", "stop emailing us but call me?", lead="h@acme.co.uk", ts="2026-10-23T10:10:00.000Z")]}])
    ro.run_check(conn, fc8, now=NOW)
    check("mixed reply flagged", rows("SELECT decision FROM outreach_reply_events WHERE instantly_email_id='m10'")[0][0] == "review")
    check("no key: still suppressed locally, retry queued", ro.review_suppress(conn, None, "m10") == "suppressed_blocklist_pending"
          and rows("SELECT blocklist_status FROM outreach_reply_events WHERE instantly_email_id='m10'")[0][0] == "failed")

    # 9. paging: a full page followed by a second page
    many = [mail(f"p{i}", "ordinary", lead=f"x{i}@acme.co.uk", ts="2026-10-23T11:00:00.000Z") for i in range(ro.PAGE_LIMIT)]
    fc9 = FakeClient([{"items": many, "next_starting_after": "CUR1"}, {"items": [mail("p-last", "no thanks", lead="z@acme.co.uk", ts="2026-10-23T11:30:00.000Z")]}])
    rep = ro.run_check(conn, fc9, now=NOW)
    check("second page read with the cursor", len(fc9.calls) == 2 and fc9.calls[1][1] == "CUR1", fc9.calls)
    check("second page opt-out suppressed", "z@acme.co.uk" in fc9.blocked, rep)

    conn.close()
finally:
    cl.cleanup()

print(f"\n{sum(results)}/{len(results)} passed")
sys.exit(0 if all(results) else 1)

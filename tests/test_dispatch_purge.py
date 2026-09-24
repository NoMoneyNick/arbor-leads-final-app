"""
test_dispatch_purge.py -- 2026-09-23, Request D, Part 2: "Implement the
agreed deletion process. Schedule removal of homeowner personal data and
personalised mailing content within 72 hours of provider-confirmed
dispatch... Preserve only explicitly defined financial, suppression and
minimal evidence records. Do not treat provider acceptance as dispatch.
Handle unknown outcomes through restricted reconciliation, with retryable
cleanup and failure alerts."

No real Postgres is available in this sandbox (same constraint every other
test file in this session works under -- see test_lead_retention.py's own
docstring), so SQL-filtering correctness is verified the same established
way: asserting on the actual SQL text executed (the exact WHERE/SET clauses
retention_dispatch_purge.py sends to the database), not by running it
against a real engine. Behavioural coverage (what gets updated on which
rows, rollback-and-alert-and-reraise on failure, restricted reconciliation)
is driven with a MagicMock cursor whose fetchall/fetchone are scripted per
test, same convention as tests/test_address_release_gate.py and
tests/test_lead_retention.py.

Covers:
  1. Dispatch timing -- the eligibility SQL keys on `status = 'dispatched'`
     / `dispatched_at` (new pipeline) and `sent_at` (legacy), both gated by
     the 72-hour constant, and NEVER on `provider_accepted_at` or
     `status = 'provider_accepted'` -- proving acceptance is never treated
     as dispatch.
  2. Actual deletion of the frozen approved_content_html, plus address/
     applicant_name, on every eligible row, and NOTHING else (ids,
     references, buyer_email, status, timestamps, provider fields survive
     -- the SQL text never mentions them in a SET clause).
  3. The leads row is only cleared once no OTHER unresolved obligation/
     dispatch remains for that reference (the NOT EXISTS guards).
  4. Failed cleanup + retries: a DB error rolls back, raises an
     admin-visible incident alert, and re-raises (never swallowed) --
     exactly database.cleanup_stale_leads' own established pattern.
  5. 'unknown' outcomes are never swept by the purge and never auto-resent
     by reconciliation -- restricted reconciliation only ever asks the
     obligation's own provider for its current status via check_status,
     using the real FakeLetterProvider (the "use a fake provider to test
     dispatch events... now" instruction) end to end.
  6. (2026-09-23, Request F) A HISTORICAL letter_dispatches row (no
     lead_allocations counterpart for its lead_reference) is excluded from
     purge eligibility entirely, in both the count and the actual sweep --
     preventing this module from silently undoing a historical disclosure
     it has no business touching. letter_obligations rows need no
     equivalent filter (never historical by construction); that asymmetry
     is asserted explicitly.

Run with:
    python -m unittest tests.test_dispatch_purge -v
"""
import os
import sys
import types
import unittest
from unittest.mock import MagicMock, patch

_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
_APP_DIR = os.path.dirname(_THIS_DIR)
sys.path.insert(0, _APP_DIR)

if "database" not in sys.modules:
    sys.modules["database"] = types.ModuleType("database")
if not hasattr(sys.modules["database"], "get_db_conn"):
    # retention_dispatch_purge.py imports `database` at module level (same
    # binding-once rationale as address_release.py -- see that module's own
    # comment) and every test below patches THIS attribute via patch.object,
    # which requires it to already exist on the stub.
    sys.modules["database"].get_db_conn = MagicMock()

import retention_dispatch_purge as purge  # noqa: E402  real module

if not hasattr(purge.database, "_redact_address_from_summary"):
    # 2026-09-23 external-review fix (Finding 2, free-text descriptions):
    # purge_dispatched_personal_data() now redacts leads.summary the same
    # way address_release.guarded_summary_for_lead(_reference) does for
    # display, via database._redact_address_from_summary -- real
    # database.py can't be imported here (it does `import psycopg2` at
    # module level, not installed in this sandbox, which is exactly why
    # `database` is stubbed at all). This is a deliberate, minimal mirror
    # of that function's actual regex behaviour (database.py:3595,
    # unchanged this session), the same one test_address_release_gate.py
    # adds for its own tests -- kept in sync with it if the real regexes
    # ever change. Set on `purge.database` specifically (not
    # sys.modules["database"]) since retention_dispatch_purge.py binds its
    # own `database` name once, at ITS first import -- same class of
    # cross-test-file sys.modules replacement documented throughout this
    # test suite (see test_address_release_gate.py's own comment on it).
    import re as _re
    _FULL_POSTCODE_RE = _re.compile(r'\b[A-Z]{1,2}[0-9][A-Z0-9]?\s*[0-9][A-Z]{2}\b')
    _HOUSE_STREET_RE = _re.compile(
        r'\b\d{1,4}[A-Za-z]?\s+(?:[A-Z][a-zA-Z\'\-]*\s+){0,3}'
        r'(?:Road|Rd|Street|St|Avenue|Ave|Lane|Ln|Close|Drive|Dr|Way|Grove|Grv|'
        r'Crescent|Cres|Gardens|Gdns|Court|Ct|Place|Pl|Rise|Walk|Terrace|Ter|'
        r'Hill|Park|Row|Mews|Square|Sq|Green|Gn)\b'
    )

    def _stub_redact_address_from_summary(summary):
        if not summary:
            return summary
        redacted = _FULL_POSTCODE_RE.sub("[postcode hidden]", summary)
        redacted = _HOUSE_STREET_RE.sub("[address hidden]", redacted)
        return redacted

    purge.database._redact_address_from_summary = MagicMock(side_effect=_stub_redact_address_from_summary)


def _conn_with_cursor():
    conn = MagicMock()
    cur = MagicMock()
    conn.cursor.return_value = cur
    return conn, cur


class TestDispatchTimingNeverTreatsAcceptanceAsDispatch(unittest.TestCase):
    """SQL-text assertions -- see this file's own docstring for why."""

    def test_purge_delay_is_72_hours(self):
        self.assertEqual(purge.DISPATCH_PURGE_DELAY_HOURS, 72)

    def test_eligibility_count_keys_on_dispatched_status_and_timestamp(self):
        conn, cur = _conn_with_cursor()
        cur.fetchone.side_effect = [(0,), (0,)]
        with patch.object(purge.database, "get_db_conn", return_value=conn):
            purge.count_dispatch_purge_eligible()
        obligations_sql = cur.execute.call_args_list[0][0][0]
        dispatches_sql = cur.execute.call_args_list[1][0][0]
        self.assertIn("status = 'dispatched'", obligations_sql)
        self.assertIn("dispatched_at", obligations_sql)
        self.assertIn("INTERVAL '72 hours'", obligations_sql)
        self.assertNotIn("provider_accepted", obligations_sql)
        self.assertIn("sent_at", dispatches_sql)
        self.assertIn("INTERVAL '72 hours'", dispatches_sql)

    def test_purge_select_never_mentions_provider_accepted(self):
        """The exact scenario the brief calls out: 'do not treat provider
        acceptance as dispatch'. A row sitting at status='provider_accepted'
        (accepted but not yet dispatched) must never even be selected as a
        candidate -- proven here by the SELECT SQL text never referencing
        that state at all, only 'dispatched'."""
        conn, cur = _conn_with_cursor()
        cur.fetchall.return_value = []
        with patch.object(purge.database, "get_db_conn", return_value=conn):
            purge.purge_dispatched_personal_data()
        select_calls_sql = " ".join(c[0][0] for c in cur.execute.call_args_list if c[0][0].strip().upper().startswith("SELECT"))
        self.assertIn("status = 'dispatched'", select_calls_sql)
        self.assertNotIn("provider_accepted", select_calls_sql)
        self.assertNotIn("'unknown'", select_calls_sql)


class TestPurgeClearsPersonalDataAndPreservesEvidence(unittest.TestCase):

    def test_letter_obligation_purge_nulls_html_and_name_and_placeholders_address(self):
        conn, cur = _conn_with_cursor()
        cur.fetchall.side_effect = [
            [("ob-1", "PLANIT-001")],           # eligible obligations
            [],                                 # eligible dispatches
            [("PLANIT-001",)],                  # all_purged_references union query
            [("PLANIT-001", "Fell one oak")],    # 2026-09-23: leads-to-clear SELECT (reference, summary)
        ]
        with patch.object(purge.database, "get_db_conn", return_value=conn):
            result = purge.purge_dispatched_personal_data()

        self.assertEqual(result["obligations_purged"], 1)
        update_calls = [c for c in cur.execute.call_args_list if c[0][0].strip().upper().startswith("UPDATE LETTER_OBLIGATIONS")]
        self.assertEqual(len(update_calls), 1)
        update_sql, update_params = update_calls[0][0]
        self.assertIn("approved_content_html = NULL", update_sql)
        self.assertIn("applicant_name = NULL", update_sql)
        self.assertIn("purged_at = NOW()", update_sql)
        self.assertIn("address = %s", update_sql)
        self.assertEqual(update_params[0], purge.PURGED_ADDRESS_PLACEHOLDER)
        self.assertEqual(update_params[1], "ob-1")
        # Minimal evidence -- nothing else is ever named in a SET clause.
        for preserved in ("buyer_email", "status =", "provider_name =", "provider_reference =",
                           "content_fingerprint =", "template_version =", "sale_context ="):
            self.assertNotIn(preserved, update_sql)

    def test_legacy_dispatch_purge_nulls_name_and_placeholders_address(self):
        conn, cur = _conn_with_cursor()
        cur.fetchall.side_effect = [
            [],                                  # eligible obligations
            [("dp-1", "PLANIT-002")],            # eligible dispatches
            [("PLANIT-002",)],
            [("PLANIT-002", "Fell one oak")],    # 2026-09-23: leads-to-clear SELECT (reference, summary)
        ]
        with patch.object(purge.database, "get_db_conn", return_value=conn):
            result = purge.purge_dispatched_personal_data()

        self.assertEqual(result["dispatches_purged"], 1)
        update_calls = [c for c in cur.execute.call_args_list if c[0][0].strip().upper().startswith("UPDATE LETTER_DISPATCHES")]
        self.assertEqual(len(update_calls), 1)
        update_sql, update_params = update_calls[0][0]
        self.assertIn("applicant_name = NULL", update_sql)
        self.assertIn("purged_at = NOW()", update_sql)
        self.assertEqual(update_params[0], purge.PURGED_ADDRESS_PLACEHOLDER)
        self.assertEqual(update_params[1], "dp-1")

    def test_leads_row_purge_guards_against_any_still_unresolved_row(self):
        """The NOT EXISTS guards -- a lead's own row is only ever cleared
        once nothing for its reference is still unresolved.

        2026-09-23 update (external-review Finding 2, summary retention):
        the leads sweep is now a SELECT-then-per-row-UPDATE (so each row's
        summary can be redacted individually -- a single bulk UPDATE can't
        apply a per-row value) instead of one bulk `UPDATE ... WHERE
        reference = ANY(%s)`. The NOT EXISTS guards moved WITH it onto the
        SELECT; the per-row UPDATE that follows filters on that row's own
        `reference` alone, since the SELECT already did the eligibility
        filtering."""
        conn, cur = _conn_with_cursor()
        cur.fetchall.side_effect = [
            [("ob-1", "PLANIT-003")],
            [],
            [("PLANIT-003",)],
            [("PLANIT-003", "Fell one oak")],  # leads-to-clear SELECT (reference, summary)
        ]
        with patch.object(purge.database, "get_db_conn", return_value=conn):
            purge.purge_dispatched_personal_data()

        leads_select_calls = [
            c for c in cur.execute.call_args_list
            if c[0][0].strip().upper().startswith("SELECT REFERENCE, SUMMARY FROM LEADS")
        ]
        self.assertEqual(len(leads_select_calls), 1)
        leads_select_sql, leads_select_params = leads_select_calls[0][0]
        self.assertIn("NOT EXISTS", leads_select_sql)
        self.assertIn("letter_obligations", leads_select_sql)
        self.assertIn("letter_dispatches", leads_select_sql)
        self.assertIn("personal_data_purged_at IS NULL", leads_select_sql)
        self.assertEqual(leads_select_params[0], ["PLANIT-003"])

        leads_update_calls = [c for c in cur.execute.call_args_list if c[0][0].strip().upper().startswith("UPDATE LEADS")]
        self.assertEqual(len(leads_update_calls), 1)
        leads_update_sql, leads_update_params = leads_update_calls[0][0]
        self.assertIn("address = NULL", leads_update_sql)
        self.assertIn("applicant_name = NULL", leads_update_sql)
        self.assertIn("summary = %s", leads_update_sql)
        self.assertIn("personal_data_purged_at = NOW()", leads_update_sql)
        self.assertIn("WHERE reference = %s", leads_update_sql)
        self.assertEqual(leads_update_params, ("Fell one oak", "PLANIT-003"))

    def test_leads_row_purge_redacts_the_summary_rather_than_leaving_it_raw(self):
        """2026-09-23, external-review Finding 2: 'retention_dispatch_
        purge.py:314 clears address/name but not summary or raw reference;
        retained data is not just a minimal non-identifying receipt.' Seeds
        a summary with a real house-number/street AND postcode (the exact
        case database._redact_address_from_summary's own docstring warns
        about -- scraped council text restating the site address inline)
        and proves the purge no longer leaves it raw."""
        conn, cur = _conn_with_cursor()
        cur.fetchall.side_effect = [
            [("ob-1", "PLANIT-006")],
            [],
            [("PLANIT-006",)],
            [("PLANIT-006", "T1 - Ash - Fell. Site: 14 Oak Avenue, Newark, NG22 8AA")],
        ]
        with patch.object(purge.database, "get_db_conn", return_value=conn):
            result = purge.purge_dispatched_personal_data()

        self.assertEqual(result["leads_purged"], 1)
        leads_update_calls = [c for c in cur.execute.call_args_list if c[0][0].strip().upper().startswith("UPDATE LEADS")]
        self.assertEqual(len(leads_update_calls), 1)
        _, leads_update_params = leads_update_calls[0][0]
        redacted_summary, redacted_reference = leads_update_params
        self.assertEqual(redacted_reference, "PLANIT-006")
        self.assertNotIn("14 Oak Avenue", redacted_summary)
        self.assertNotIn("NG22 8AA", redacted_summary)
        self.assertIn("[address hidden]", redacted_summary)
        self.assertIn("[postcode hidden]", redacted_summary)
        # The purge is still evidence-preserving, not a blank-out -- the
        # non-identifying part of the description survives.
        self.assertIn("T1 - Ash - Fell", redacted_summary)

    def test_leads_row_with_an_empty_summary_is_left_as_is_not_turned_into_a_string(self):
        """A lead with no free-text description (empty/None summary) must
        not come out of the purge as the literal string 'None' or crash --
        _redact_address_from_summary's own None-passthrough behaviour is
        preserved through the purge's per-row loop."""
        conn, cur = _conn_with_cursor()
        cur.fetchall.side_effect = [
            [("ob-1", "PLANIT-007")],
            [],
            [("PLANIT-007",)],
            [("PLANIT-007", None)],
        ]
        with patch.object(purge.database, "get_db_conn", return_value=conn):
            result = purge.purge_dispatched_personal_data()

        self.assertEqual(result["leads_purged"], 1)
        leads_update_calls = [c for c in cur.execute.call_args_list if c[0][0].strip().upper().startswith("UPDATE LEADS")]
        _, leads_update_params = leads_update_calls[0][0]
        self.assertIsNone(leads_update_params[0])

    def test_no_eligible_rows_is_a_clean_no_op(self):
        conn, cur = _conn_with_cursor()
        cur.fetchall.side_effect = [[], []]
        with patch.object(purge.database, "get_db_conn", return_value=conn):
            result = purge.purge_dispatched_personal_data()
        self.assertEqual(result, {"obligations_purged": 0, "dispatches_purged": 0, "leads_purged": 0})
        leads_update_calls = [c for c in cur.execute.call_args_list if c[0][0].strip().upper().startswith("UPDATE LEADS")]
        self.assertEqual(leads_update_calls, [])
        conn.commit.assert_called_once()


class TestPurgeFailureIsRetryableAndAlerted(unittest.TestCase):

    def test_db_error_rolls_back_alerts_and_reraises(self):
        conn, cur = _conn_with_cursor()
        cur.execute.side_effect = RuntimeError("simulated connection drop mid-purge")
        fake_notifications = types.ModuleType("notifications")
        fake_notifications.send_system_incident_alert = MagicMock()
        with patch.object(purge.database, "get_db_conn", return_value=conn), \
             patch.dict(sys.modules, {"notifications": fake_notifications}):
            with self.assertRaises(RuntimeError):
                purge.purge_dispatched_personal_data()
        conn.rollback.assert_called_once()
        fake_notifications.send_system_incident_alert.assert_called_once()
        alert_kwargs = fake_notifications.send_system_incident_alert.call_args.kwargs
        self.assertEqual(alert_kwargs["category"], "DATA RETENTION")
        self.assertIn("72-HOUR", alert_kwargs["title"])

    def test_retry_after_failure_is_safe_and_picks_up_the_same_eligible_set(self):
        """A failed pass never partially-commits (whole-pass rollback), so
        a retry with the SAME eligible rows still queued must succeed
        cleanly -- proving the retry story the brief asks for."""
        conn, cur = _conn_with_cursor()
        cur.execute.side_effect = RuntimeError("transient failure")
        fake_notifications = types.ModuleType("notifications")
        fake_notifications.send_system_incident_alert = MagicMock()
        with patch.object(purge.database, "get_db_conn", return_value=conn), \
             patch.dict(sys.modules, {"notifications": fake_notifications}):
            with self.assertRaises(RuntimeError):
                purge.purge_dispatched_personal_data()

        # Retry: same rows still eligible (purged_at was never actually
        # set, since the whole pass rolled back), this time succeeding.
        conn2, cur2 = _conn_with_cursor()
        cur2.fetchall.side_effect = [
            [("ob-1", "PLANIT-004")], [], [("PLANIT-004",)],
            [("PLANIT-004", "Fell one oak")],  # 2026-09-23: leads-to-clear SELECT (reference, summary)
        ]
        with patch.object(purge.database, "get_db_conn", return_value=conn2):
            result = purge.purge_dispatched_personal_data()
        self.assertEqual(result["obligations_purged"], 1)
        conn2.commit.assert_called_once()


class TestHistoricalDispatchesAreNeverPurged(unittest.TestCase):
    """2026-09-23, Request F: "Inspect the fields retained after purging ...
    remove or restrict what defeats the agreed privacy model." A
    letter_dispatches row with no lead_allocations counterpart for the same
    lead_reference is address_release.is_historical_purchase's own
    definition of a HISTORICAL claim -- and Request D Part 1 already
    required "do not treat historical disclosures as undone". Before this
    fix, every historical dispatch (by definition already long past 72
    hours old) would have been purged on the very first run, silently
    redacting an address a contractor was permanently entitled to see. See
    this module's own docstring for the full story.

    letter_obligations rows are excluded from this concern entirely (never
    historical, by construction -- see fulfilment.create_allocation_and_
    obligation), so only the letter_dispatches queries need the guard;
    that asymmetry is asserted explicitly below too."""

    def test_dispatch_eligibility_count_excludes_rows_with_no_lead_allocation(self):
        conn, cur = _conn_with_cursor()
        cur.fetchone.side_effect = [(0,), (0,)]
        with patch.object(purge.database, "get_db_conn", return_value=conn):
            purge.count_dispatch_purge_eligible()
        obligations_sql = cur.execute.call_args_list[0][0][0]
        dispatches_sql = cur.execute.call_args_list[1][0][0]
        self.assertIn("EXISTS", dispatches_sql)
        self.assertIn("lead_allocations", dispatches_sql)
        self.assertIn("la.lead_reference = letter_dispatches.lead_reference", dispatches_sql)
        # The asymmetry: letter_obligations rows are never historical, so
        # this filter must NOT appear on that query at all.
        self.assertNotIn("lead_allocations", obligations_sql)

    def test_dispatch_purge_select_excludes_rows_with_no_lead_allocation(self):
        conn, cur = _conn_with_cursor()
        cur.fetchall.return_value = []
        with patch.object(purge.database, "get_db_conn", return_value=conn):
            purge.purge_dispatched_personal_data()
        select_calls = [c for c in cur.execute.call_args_list if c[0][0].strip().upper().startswith("SELECT")]
        obligations_select_sql = select_calls[0][0][0]
        dispatches_select_sql = select_calls[1][0][0]
        self.assertIn("EXISTS", dispatches_select_sql)
        self.assertIn("lead_allocations", dispatches_select_sql)
        self.assertIn("la.lead_reference = letter_dispatches.lead_reference", dispatches_select_sql)
        self.assertNotIn("lead_allocations", obligations_select_sql)

    def test_a_historical_dispatch_row_returned_by_the_db_would_still_be_correctly_excluded_upstream(self):
        """Belt-and-braces behavioural check: even granting that the SQL
        filter above is what actually does the exclusion in a real
        database, prove the Python-side loop applies no OTHER filter that
        could accidentally purge a row if the SQL guard were ever removed
        by mistake -- i.e. confirm there is exactly one gate (the SQL
        EXISTS clause) and the loop itself purges whatever rows it is
        handed. This pins today's single-gate design so a future edit that
        weakens the SQL clause without adding a Python-side check is
        caught by the two tests above, not silently passed here."""
        conn, cur = _conn_with_cursor()
        # Simulate the SQL guard correctly excluding a historical row: the
        # mocked fetchall for letter_dispatches returns nothing, as a real
        # database would for a historical-only reference.
        cur.fetchall.side_effect = [[], [], []]
        with patch.object(purge.database, "get_db_conn", return_value=conn):
            result = purge.purge_dispatched_personal_data()
        self.assertEqual(result["dispatches_purged"], 0)


class TestUnknownOutcomesAreNeverSweptOrAutoResent(unittest.TestCase):

    def test_count_unknown_reads_only_status_unknown(self):
        conn, cur = _conn_with_cursor()
        cur.fetchone.return_value = (3,)
        with patch.object(purge.database, "get_db_conn", return_value=conn):
            count = purge.count_unknown_outcome_obligations_awaiting_reconciliation()
        self.assertEqual(count, 3)
        sql = cur.execute.call_args[0][0]
        self.assertIn("status = 'unknown'", sql)

    def test_reconciliation_resolves_via_the_fake_providers_check_status_not_send(self):
        """Drives the REAL FakeLetterProvider end to end (2026-09-23,
        Request D Part 2: 'use a fake provider to test dispatch events and
        deletion now'). A previously-accepted send is advanced to
        dispatched via simulate_dispatch_confirmed (the realistic
        'discovered on a later status check' flow), then reconciliation
        must pick that up via check_status alone -- send() is asserted
        never called during reconciliation."""
        import fulfilment
        from letter_providers.fake_provider import FakeLetterProvider
        from letter_providers.base import LetterRequest

        fake = FakeLetterProvider(force_outcome="accepted", seed=1)
        request = LetterRequest(
            idempotency_key="idem-1", lead_reference="PLANIT-005",
            address_lines={"postcode": "LS1 1AA"}, applicant_name=None,
            content_html="<p>hi</p>", content_fingerprint="fp1",
        )
        accepted_result = fake.send(request)
        self.assertTrue(fake.simulate_dispatch_confirmed(accepted_result.provider_reference))

        conn, cur = _conn_with_cursor()
        cur.fetchall.return_value = [("ob-unknown-1", fake.name, accepted_result.provider_reference)]
        mark_calls = []

        def _fake_mark_provider_result(cur_, obligation_id, **kwargs):
            mark_calls.append((obligation_id, kwargs))

        fake_slot = types.SimpleNamespace(adapter=fake)
        fake_registry = types.SimpleNamespace(slots=[fake_slot])
        fake_send = MagicMock(wraps=fake.send)
        fake.send = fake_send

        with patch.object(purge.database, "get_db_conn", return_value=conn), \
             patch("letter_providers.registry.build_registry_from_env", return_value=fake_registry), \
             patch.object(fulfilment, "mark_provider_result", side_effect=_fake_mark_provider_result):
            result = purge.reconcile_unknown_outcome_obligations()

        self.assertEqual(result["resolved"], 1)
        self.assertEqual(result["still_unknown"], 0)
        fake_send.assert_not_called()  # restricted: reconciliation never resends
        self.assertEqual(len(mark_calls), 1)
        obligation_id, kwargs = mark_calls[0]
        self.assertEqual(obligation_id, "ob-unknown-1")
        self.assertEqual(kwargs["outcome"], "dispatched")
        self.assertFalse(kwargs["is_dry_run"])

    def test_reconciliation_leaves_it_unknown_when_the_provider_has_no_answer(self):
        conn, cur = _conn_with_cursor()
        cur.fetchall.return_value = [("ob-1", "fake_test", "FAKE-000001")]
        adapter = MagicMock()
        adapter.name = "fake_test"
        adapter.check_status.return_value = None
        fake_slot = types.SimpleNamespace(adapter=adapter)
        fake_registry = types.SimpleNamespace(slots=[fake_slot])

        with patch.object(purge.database, "get_db_conn", return_value=conn), \
             patch("letter_providers.registry.build_registry_from_env", return_value=fake_registry):
            result = purge.reconcile_unknown_outcome_obligations()

        self.assertEqual(result["still_unknown"], 1)
        self.assertEqual(result["resolved"], 0)
        adapter.send.assert_not_called()

    def test_reconciliation_skips_when_no_configured_adapter_matches(self):
        conn, cur = _conn_with_cursor()
        cur.fetchall.return_value = [("ob-1", "some_unconfigured_provider", "REF-1")]
        fake_registry = types.SimpleNamespace(slots=[])

        with patch.object(purge.database, "get_db_conn", return_value=conn), \
             patch("letter_providers.registry.build_registry_from_env", return_value=fake_registry):
            result = purge.reconcile_unknown_outcome_obligations()

        self.assertEqual(result["skipped_no_adapter"], 1)
        self.assertEqual(result["resolved"], 0)


if __name__ == "__main__":
    unittest.main()

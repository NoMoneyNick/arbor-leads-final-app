"""
test_suppression.py -- suppression.py's address/person matching, including
the 2026-09-22 handoff's keyed-digest fix (no more browsable plaintext
address list -- see that module's own docstring). Run with:
    python -m unittest tests.test_suppression -v
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import suppression


class FakeCursor:
    def __init__(self, fetchone_results=None, fetchall_results=None):
        self.executed = []
        self._fetchone_results = list(fetchone_results or [])
        self._fetchall_results = list(fetchall_results or [])

    def execute(self, sql, params=None):
        self.executed.append((" ".join(sql.split()), params))

    def fetchone(self):
        return self._fetchone_results.pop(0) if self._fetchone_results else None

    def fetchall(self):
        return self._fetchall_results.pop(0) if self._fetchall_results else []


class _WithHashKey(unittest.TestCase):
    """Every hashing/matching function now requires SUPPRESSION_HASH_KEY --
    set a fixed test key for the duration of each test, restoring whatever
    was there before on exit (never leak env state into other test files)."""

    def setUp(self):
        self._had = suppression.SUPPRESSION_HASH_KEY_ENV in os.environ
        self._old = os.environ.get(suppression.SUPPRESSION_HASH_KEY_ENV)
        os.environ[suppression.SUPPRESSION_HASH_KEY_ENV] = "test-suppression-hash-key-do-not-use-in-prod"

    def tearDown(self):
        if self._had:
            os.environ[suppression.SUPPRESSION_HASH_KEY_ENV] = self._old
        else:
            os.environ.pop(suppression.SUPPRESSION_HASH_KEY_ENV, None)


class TestNormalizeAddress(unittest.TestCase):
    def test_formatting_variants_normalise_the_same(self):
        a = suppression.normalize_address("1 Test Street, Leeds, LS1 1AA")
        b = suppression.normalize_address("1  test street  leeds  ls1 1aa")
        self.assertEqual(a, b)

    def test_empty_address_is_empty_string(self):
        self.assertEqual(suppression.normalize_address(""), "")
        self.assertEqual(suppression.normalize_address(None), "")


class TestHashKey(unittest.TestCase):
    def test_missing_key_raises_not_silently_matches_nothing(self):
        """Fail LOUD, not safe-and-silent -- see _hash_key's own docstring:
        a suppression check that silently passed with no key configured
        would be far more dangerous than one that refuses to run at all.

        Explicitly saves/restores the env var (never a bare pop with no
        tearDown) -- several other test files (test_providers.py,
        test_worker.py, etc) set their own SUPPRESSION_HASH_KEY default at
        module-import/collection time, which happens ONCE, before any
        test method runs; a pop here with no restore would silently break
        every one of THEIR tests that happens to execute after this one in
        the same `unittest discover` process, however unrelated to
        suppression hashing they are."""
        had = suppression.SUPPRESSION_HASH_KEY_ENV in os.environ
        old = os.environ.get(suppression.SUPPRESSION_HASH_KEY_ENV)
        os.environ.pop(suppression.SUPPRESSION_HASH_KEY_ENV, None)
        try:
            with self.assertRaises(RuntimeError):
                suppression.is_suppressed(FakeCursor(), address="1 Test St", applicant_name="J Bloggs")
        finally:
            if had:
                os.environ[suppression.SUPPRESSION_HASH_KEY_ENV] = old
            else:
                os.environ.pop(suppression.SUPPRESSION_HASH_KEY_ENV, None)


class TestDigests(_WithHashKey):
    def test_address_digest_is_deterministic(self):
        d1 = suppression._address_digest("1 test st leeds")
        d2 = suppression._address_digest("1 test st leeds")
        self.assertEqual(d1, d2)

    def test_address_digest_differs_for_different_addresses(self):
        self.assertNotEqual(
            suppression._address_digest("1 test st leeds"),
            suppression._address_digest("2 test st leeds"),
        )

    def test_address_digest_is_not_a_bare_hash_of_the_address(self):
        """The whole point of HMAC over plain SHA-256: without the key, an
        attacker with the UK's public address list can't precompute a
        rainbow table and match it against stored digests."""
        import hashlib
        bare_sha256 = hashlib.sha256("1 test st leeds".encode()).hexdigest()
        self.assertNotEqual(suppression._address_digest("1 test st leeds"), bare_sha256)

    def test_address_digest_changes_with_a_different_key(self):
        d1 = suppression._address_digest("1 test st leeds")
        os.environ[suppression.SUPPRESSION_HASH_KEY_ENV] = "a-completely-different-key"
        d2 = suppression._address_digest("1 test st leeds")
        self.assertNotEqual(d1, d2)

    def test_address_person_digest_is_a_different_value_than_address_alone(self):
        addr_only = suppression._address_digest("1 test st leeds")
        addr_person = suppression._address_person_digest("1 test st leeds", "j bloggs")
        self.assertNotEqual(addr_only, addr_person)

    def test_address_person_digest_differs_by_name(self):
        self.assertNotEqual(
            suppression._address_person_digest("1 test st leeds", "j bloggs"),
            suppression._address_person_digest("1 test st leeds", "a different person"),
        )


class TestIsSuppressed(_WithHashKey):
    def test_not_suppressed_when_no_row_matches(self):
        cur = FakeCursor(fetchone_results=[None])
        match = suppression.is_suppressed(cur, address="1 Test St, Leeds", applicant_name="J Bloggs")
        self.assertFalse(match.suppressed)

    def test_suppressed_when_row_matches(self):
        cur = FakeCursor(fetchone_results=[("row-1", "objected by letter reply", "this_person")])
        match = suppression.is_suppressed(cur, address="1 Test St, Leeds", applicant_name="J Bloggs")
        self.assertTrue(match.suppressed)
        self.assertEqual(match.reason, "objected by letter reply")

    def test_address_scope_and_person_scope_are_both_passed_to_the_query(self):
        """Section 7: 'Consider multiple applications at the same property
        without assuming all residents are the same person.' Both match
        rules are still present in the query -- a scope='this_person' row
        must only match the SAME applicant_name, not anyone else applying
        at that address later. 2026-09-22 handoff: params are now keyed
        digests, never the plaintext name/address -- this is itself part
        of what's being tested (the previous version of this test asserted
        the plaintext string appeared in params, which is exactly the
        'browsable address list' leak this handoff fixes)."""
        cur = FakeCursor(fetchone_results=[None])
        suppression.is_suppressed(cur, address="1 Test St, Leeds", applicant_name="Different Person")
        sql, params = cur.executed[0]
        self.assertIn("this_address_anyone", sql)
        self.assertIn("this_person", sql)
        self.assertIn("address_hash", sql)
        self.assertIn("address_person_hash", sql)
        self.assertNotIn("different person", params)
        self.assertNotIn("normalized_address", sql)
        expected_person_digest = suppression._address_person_digest(
            suppression.normalize_address("1 Test St, Leeds"), "Different Person")
        self.assertIn(expected_person_digest, params)

    def test_matches_by_digest_not_by_plaintext_equality(self):
        """Two differently-formatted inputs that normalise to the same
        address must produce the SAME query digest -- proves matching
        didn't silently become exact-string-only."""
        cur1, cur2 = FakeCursor(fetchone_results=[None]), FakeCursor(fetchone_results=[None])
        suppression.is_suppressed(cur1, address="1 Test St, Leeds", applicant_name="J Bloggs")
        suppression.is_suppressed(cur2, address="1  test st  leeds", applicant_name="J Bloggs")
        self.assertEqual(cur1.executed[0][1], cur2.executed[0][1])


class TestAddSuppression(_WithHashKey):
    def test_rejects_invalid_scope(self):
        cur = FakeCursor()
        with self.assertRaises(ValueError):
            suppression.add_suppression(cur, address="1 Test St", applicant_name="J Bloggs",
                                         reason="objected", scope="everyone_everywhere",
                                         recorded_by="admin@treekey.co.uk")

    def test_records_who_added_it(self):
        cur = FakeCursor(fetchone_results=[("row-1",)])
        row_id = suppression.add_suppression(cur, address="1 Test St", applicant_name="J Bloggs",
                                              reason="objected by email", scope="this_person",
                                              recorded_by="admin@treekey.co.uk")
        self.assertEqual(row_id, "row-1")
        self.assertIn("admin@treekey.co.uk", cur.executed[0][1])

    def test_never_writes_the_real_address_into_the_stored_row(self):
        """The core regression guard for this whole fix: normalized_address
        must be NULL in every INSERT this function issues, and the real
        address string must not appear anywhere in the query params --
        only its keyed digests."""
        cur = FakeCursor(fetchone_results=[("row-1",)])
        suppression.add_suppression(cur, address="42 Secret Lane, Leeds, LS1 9ZZ", applicant_name="J Bloggs",
                                     reason="objected by email", scope="this_person",
                                     recorded_by="admin@treekey.co.uk")
        sql, params = cur.executed[0]
        self.assertIn("VALUES (NULL,", sql)
        self.assertNotIn("42 secret lane", [str(p).lower() for p in params])
        for p in params:
            self.assertNotIn("secret lane", str(p).lower())

    def test_stores_digests_matching_a_later_is_suppressed_lookup(self):
        """End-to-end (within this fake-cursor harness): the digest
        add_suppression stores for scope='this_address_anyone' is exactly
        what is_suppressed will look up for the same address later."""
        cur = FakeCursor(fetchone_results=[("row-1",)])
        suppression.add_suppression(cur, address="1 Test St, Leeds", applicant_name=None,
                                     reason="objected", scope="this_address_anyone",
                                     recorded_by="admin@treekey.co.uk")
        _, insert_params = cur.executed[0]
        stored_address_hash = insert_params[0]  # normalized_address, address_hash, address_person_hash, ...
        expected = suppression._address_digest(suppression.normalize_address("1 Test St, Leeds"))
        self.assertEqual(stored_address_hash, expected)


class TestResolveSuppression(unittest.TestCase):
    def test_resolve_only_affects_unresolved_rows(self):
        cur = FakeCursor(fetchone_results=[("row-1",)])
        self.assertTrue(suppression.resolve_suppression(cur, "row-1", resolved_by="admin", note="confirmed handled"))

    def test_resolve_returns_false_when_nothing_matched(self):
        cur = FakeCursor(fetchone_results=[None])
        self.assertFalse(suppression.resolve_suppression(cur, "row-does-not-exist", resolved_by="admin", note="n/a"))


class TestBackfillAddressSuppressionHashes(_WithHashKey):
    def test_computes_digests_and_clears_the_plaintext(self):
        cur = FakeCursor(
            fetchall_results=[[("row-1", "1 test st leeds", "j bloggs"), ("row-2", "2 other rd leeds", None)]],
            fetchone_results=[(0,)],  # the final "remaining" count
        )
        result = suppression.backfill_address_suppression_hashes(cur)
        self.assertEqual(result, {"backfilled": 2, "remaining": 0})

        update_calls = [e for e in cur.executed if e[0].startswith("UPDATE postal_suppressions")]
        self.assertEqual(len(update_calls), 2)
        sql1, params1 = update_calls[0]
        self.assertIn("normalized_address = NULL", sql1)
        self.assertEqual(params1[0], suppression._address_digest("1 test st leeds"))
        self.assertEqual(params1[1], suppression._address_person_digest("1 test st leeds", "j bloggs"))
        self.assertEqual(params1[2], "row-1")

    def test_no_rows_needing_backfill_is_a_clean_no_op(self):
        cur = FakeCursor(fetchall_results=[[]], fetchone_results=[(0,)])
        result = suppression.backfill_address_suppression_hashes(cur)
        self.assertEqual(result, {"backfilled": 0, "remaining": 0})
        update_calls = [e for e in cur.executed if e[0].startswith("UPDATE postal_suppressions")]
        self.assertEqual(update_calls, [])

    def test_reports_remaining_rows_beyond_the_batch_limit(self):
        cur = FakeCursor(
            fetchall_results=[[("row-1", "1 test st leeds", None)]],
            fetchone_results=[(37,)],  # still 37 left after this batch
        )
        result = suppression.backfill_address_suppression_hashes(cur, batch_limit=1)
        self.assertEqual(result, {"backfilled": 1, "remaining": 37})
        select_call = cur.executed[0]
        self.assertIn("LIMIT %s", select_call[0])
        self.assertEqual(select_call[1], (1,))


if __name__ == "__main__":
    unittest.main()

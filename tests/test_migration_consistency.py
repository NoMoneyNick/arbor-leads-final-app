"""
test_migration_consistency.py -- migrations/0001_letter_fulfilment.sql
claims to be a verbatim copy of the CREATE TABLE/INDEX statements each
init_*_schema function executes. This test catches drift between the two
(someone edits one and forgets the other) by extracting the exact SQL each
Python function would run and checking every table/index name it creates
is also declared in the .sql file. Run with:
    python -m unittest tests.test_migration_consistency -v
"""
import os
import re
import sys
import types
import unittest
from unittest.mock import MagicMock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# address_release.py imports "database" at its own module level (2026-09-18
# review, Section 2 second pass -- see that module's own comment on why).
# database.py itself imports psycopg2, not installed in this sandbox, so a
# placeholder must be registered before address_release is ever imported --
# same minimal stub-if-absent convention test_main.py uses. This file
# doesn't need database to actually do anything (it only inspects
# address_release.init_address_release_schema's SQL text), so an empty
# stub is enough.
if "database" not in sys.modules:
    sys.modules["database"] = types.ModuleType("database")

import fulfilment
import funding
import suppression
import letter_content
import address_release

_MIGRATION_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "migrations", "0001_letter_fulfilment.sql"
)


def _tables_and_indexes_created_by(schema_fn) -> set:
    """Runs the schema function against a capturing fake cursor and
    extracts every 'CREATE TABLE IF NOT EXISTS <name>' / 'CREATE INDEX IF
    NOT EXISTS <name>' identifier it executes."""
    captured = []

    class _Capture:
        def execute(self, sql, params=None):
            captured.append(sql)

    schema_fn(_Capture())
    sql_text = "\n".join(captured)
    names = set(re.findall(r"CREATE TABLE IF NOT EXISTS (\w+)", sql_text))
    names |= set(re.findall(r"CREATE INDEX IF NOT EXISTS (\w+)", sql_text))
    return names


class TestMigrationFileCoversEverySchemaFunction(unittest.TestCase):
    def setUp(self):
        with open(_MIGRATION_PATH) as f:
            self.migration_sql = f.read()

    def _assert_all_names_present(self, names: set):
        missing = [n for n in names if n not in self.migration_sql]
        self.assertEqual(missing, [], f"migrations/0001_letter_fulfilment.sql is missing: {missing}")

    def test_fulfilment_tables_and_indexes_are_all_in_the_migration_file(self):
        self._assert_all_names_present(_tables_and_indexes_created_by(fulfilment.init_fulfilment_schema))

    def test_funding_tables_and_indexes_are_all_in_the_migration_file(self):
        self._assert_all_names_present(_tables_and_indexes_created_by(funding.init_funding_schema))

    def test_suppression_tables_and_indexes_are_all_in_the_migration_file(self):
        self._assert_all_names_present(_tables_and_indexes_created_by(suppression.init_suppression_schema))

    def test_letter_content_tables_and_indexes_are_all_in_the_migration_file(self):
        self._assert_all_names_present(_tables_and_indexes_created_by(letter_content.init_letter_content_schema))

    def test_address_release_tables_and_indexes_are_all_in_the_migration_file(self):
        """2026-09-18 review, Section 2 (second pass): address_disclosure_
        decisions (the new per-allocation eligibility table) must stay in
        sync with migrations/0001_letter_fulfilment.sql too, same as every
        other schema function."""
        self._assert_all_names_present(_tables_and_indexes_created_by(address_release.init_address_release_schema))

    def test_migration_file_declares_no_extra_tables_not_created_in_python(self):
        """The other direction -- catches the .sql file growing a table
        that no Python schema function actually creates (e.g. a stale leftover
        from a renamed table), which would make it silently diverge from
        what database.init_db() actually does."""
        all_python_names = set()
        for fn in (fulfilment.init_fulfilment_schema, funding.init_funding_schema,
                   suppression.init_suppression_schema, letter_content.init_letter_content_schema,
                   address_release.init_address_release_schema):
            all_python_names |= _tables_and_indexes_created_by(fn)
        sql_table_names = set(re.findall(r"CREATE TABLE IF NOT EXISTS (\w+)", self.migration_sql))
        extra = sql_table_names - all_python_names
        self.assertEqual(extra, set(), f"migrations/0001_letter_fulfilment.sql declares tables no init_*_schema function creates: {extra}")


if __name__ == "__main__":
    unittest.main()
